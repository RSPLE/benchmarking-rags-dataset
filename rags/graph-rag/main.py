import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional, TypedDict

import networkx as nx
from langchain.tools import tool
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langsmith import traceable
from rag_settings import (
    build_llm,
    configure_environment,
    extract_response_text,
    finish_usage_tracker,
    get_chroma_settings,
    start_usage_tracker,
)

from benchmark_config import configuration, positive_int
from benchmark_index import cached_extraction, load_index
from benchmark_pipeline import evaluation_documents, execute_pipeline, tool_evidence
from benchmark_storage import fingerprint

configure_environment("benchmark-graph-rag")

DOCS_DIR = os.getenv("DOCS_DIR", "./docs/")
PERSIST_DIR, CHROMA_COLLECTION_NAME = get_chroma_settings(
    "./chroma_graph_db_openai",
    "graph_collection_openai",
)


llm_extractor = vector_store = llm_with_tools = agent = None


@dataclass
class EntityNode:
    id: str
    name: str
    type: str
    description: str = ""
    chunk_ids: List[str] = field(default_factory=list)


@dataclass
class RelationEdge:
    source_id: str
    target_id: str
    relation_type: str
    description: str = ""
    weight: float = 1.0


class KnowledgeGraph:
    def __init__(self):
        self.graph = nx.DiGraph()
        self.entities: Dict[str, EntityNode] = {}
        self._name_index: Dict[str, str] = {}

    def add_entity(self, entity: EntityNode) -> None:
        self.entities[entity.id] = entity
        self._name_index[entity.name.lower()] = entity.id
        self.graph.add_node(
            entity.id,
            name=entity.name,
            type=entity.type,
            description=entity.description,
        )

    def add_relation(self, relation: RelationEdge) -> None:
        if relation.source_id in self.entities and relation.target_id in self.entities:
            self.graph.add_edge(
                relation.source_id,
                relation.target_id,
                relation=relation.relation_type,
                description=relation.description,
                weight=relation.weight,
            )

    def find_entity_by_name(self, name: str) -> Optional[EntityNode]:
        name_lower = name.lower()
        if name_lower in self._name_index:
            return self.entities[self._name_index[name_lower]]
        for stored_name, eid in self._name_index.items():
            if name_lower in stored_name or stored_name in name_lower:
                return self.entities[eid]
        return None

    def get_neighbors(self, entity_id: str, depth: int = 2) -> List[str]:
        visited = set()
        frontier = {entity_id}
        for _ in range(depth):
            next_frontier = set()
            for nid in frontier:
                nbrs = set(self.graph.successors(nid)) | set(self.graph.predecessors(nid))
                next_frontier.update(nbrs - visited)
            visited.update(frontier)
            frontier = next_frontier
        visited.update(frontier)
        return sorted(visited)

    def build_context(self, node_ids: List[str]) -> str:
        if not node_ids:
            return "Nenhuma entidade relevante encontrada no grafo."
        subgraph = self.graph.subgraph(node_ids)
        lines = ["### Entidades Relevantes do Grafo\n"]
        for nid in subgraph.nodes():
            entity = self.entities.get(nid)
            if entity:
                lines.append(f"- **{entity.name}** [{entity.type}]: {entity.description}")
        lines.append("\n### Relações\n")
        for u, v, data in subgraph.edges(data=True):
            u_name = self.entities.get(u, EntityNode(u, u, "")).name
            v_name = self.entities.get(v, EntityNode(v, v, "")).name
            rel = data.get("relation", "RELATES_TO")
            desc = data.get("description", "")
            lines.append(f"- {u_name} --[{rel}]--> {v_name}: {desc}")
        return "\n".join(lines)

    @property
    def stats(self) -> Dict[str, int]:
        return {"nodes": self.graph.number_of_nodes(), "edges": self.graph.number_of_edges()}


knowledge_graph = KnowledgeGraph()

EXTRACTION_SYSTEM = """Você é um extrator especializado de entidades e relações.
Retorne APENAS um JSON válido, sem texto adicional, sem markdown."""

EXTRACTION_TEMPLATE = """Analise o texto e extraia entidades e relações.

Retorne SOMENTE este JSON (sem blocos de código, sem explicações):
{{
  "entities": [
    {{"id": "e1", "name": "Nome da Entidade", "type": "Concept|Person|Organization|Location|Event", "description": "descrição breve"}}
  ],
  "relations": [
    {{"source": "e1", "target": "e2", "type": "RELATES_TO|IS_A|PART_OF|CAUSES|DEFINES", "description": "como se relacionam"}}
  ]
}}

Texto:
{text}"""


def extract_entities_from_chunk(chunk: Document) -> Dict:
    prompt = EXTRACTION_TEMPLATE.format(text=chunk.page_content[:1500])
    messages = [
        SystemMessage(content=EXTRACTION_SYSTEM),
        HumanMessage(content=prompt),
    ]
    response = llm_extractor.invoke(messages)
    raw = extract_response_text(response).strip()
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")
    return json.loads(raw)


def ingest_chunk_into_graph(chunk: Document, extraction: Dict) -> None:
    chunk_id = chunk.metadata.get("chunk_id", str(uuid.uuid4()))
    local_id_map: Dict[str, str] = {}

    for ent in extraction.get("entities", []):
        global_id = f"{ent['name'].lower().replace(' ', '_')}_{ent['type'].lower()}"
        local_id_map[ent["id"]] = global_id
        if global_id not in knowledge_graph.entities:
            knowledge_graph.add_entity(
                EntityNode(
                    id=global_id,
                    name=ent["name"],
                    type=ent.get("type", "Concept"),
                    description=ent.get("description", ""),
                    chunk_ids=[chunk_id],
                )
            )
        else:
            knowledge_graph.entities[global_id].chunk_ids.append(chunk_id)

    for rel in extraction.get("relations", []):
        src = local_id_map.get(rel["source"])
        tgt = local_id_map.get(rel["target"])
        if src and tgt:
            knowledge_graph.add_relation(
                RelationEdge(
                    source_id=src,
                    target_id=tgt,
                    relation_type=rel.get("type", "RELATES_TO"),
                    description=rel.get("description", ""),
                )
            )


@tool(
    response_format="content_and_artifact",
    description="Recupera chunks de texto relevantes por similaridade semântica.",
)
def retrieve_vector_context(query: str):
    retrieved_docs = vector_store.similarity_search(query, k=3)
    serialized = "\n\n".join(
        f"Source: {doc.metadata}\nContent: {doc.page_content}" for doc in retrieved_docs
    )
    return serialized, retrieved_docs


@tool(
    response_format="content_and_artifact",
    description="Recupera contexto estruturado do grafo de conhecimento: entidades e relações relevantes à query.",
)
def retrieve_graph_context(query: str):
    words = [w.strip('.,;:?!"') for w in query.split() if len(w) > 3]
    relevant_nodes: List[str] = []
    for word in words:
        entity = knowledge_graph.find_entity_by_name(word)
        if entity:
            neighbors = knowledge_graph.get_neighbors(entity.id, depth=2)
            relevant_nodes.extend(neighbors)
    relevant_nodes = sorted(set(relevant_nodes))[:40]
    graph_context = knowledge_graph.build_context(relevant_nodes)
    return graph_context, relevant_nodes


tools = [retrieve_vector_context, retrieve_graph_context]

SYSTEM_PROMPT = """Você é um assistente especializado em análise de documentos com acesso a um grafo de conhecimento.

Você possui DOIS tools:
1. retrieve_graph_context — recupera entidades e relações estruturadas do grafo de conhecimento
2. retrieve_vector_context — recupera trechos de texto por similaridade semântica

Para responder bem:
- Sempre use retrieve_graph_context primeiro para entender as relações entre conceitos
- Use retrieve_vector_context para obter detalhes textuais complementares
- Integre ambas as fontes na sua resposta
- Cite explicitamente quais entidades e relações do grafo embasam sua resposta
- Responda sempre em português, mesmo que a pergunta seja em outro idioma"""


class GraphRAGState(TypedDict):
    messages: Annotated[list, add_messages]


def agent_node(state: GraphRAGState) -> GraphRAGState:
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}


def should_continue(state: GraphRAGState) -> str:
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return END


@traceable(name="graph-rag-query", run_type="chain")
def query_graph_rag(
    question: str,
    thread_id: Optional[str] = None,
    callbacks=None,
) -> Dict[str, Any]:
    if thread_id is None:
        thread_id = str(uuid.uuid4())
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": positive_int("BENCHMARK_AGENT_RECURSION_LIMIT", 12),
    }

    if callbacks:
        config["callbacks"] = callbacks

    events = list(
        agent.stream(
            {"messages": [{"role": "user", "content": question}]},
            config=config,
            stream_mode="values",
        )
    )
    final_event = events[-1]
    answer = extract_response_text(final_event["messages"][-1])
    generation_contexts, generation_evidence = tool_evidence(final_event["messages"])
    documents = evaluation_documents(
        final_event["messages"], question, vector_store, tool_name="retrieve_vector_context", k=3
    )
    contexts = [doc.page_content for doc in documents]
    evidence = [doc.metadata for doc in documents]
    return {
        "question": question,
        "answer": answer,
        "contexts": contexts,
        "evidence_metadata": evidence,
        "generation_contexts": generation_contexts,
        "generation_evidence_metadata": generation_evidence,
    }


def prepare():
    global vector_store, llm_extractor, llm_with_tools, agent, knowledge_graph
    vector_store, embeddings, all_splits = load_index(
        DOCS_DIR, PERSIST_DIR, CHROMA_COLLECTION_NAME, chunk_size=1000, chunk_overlap=200
    )
    llm_extractor = build_llm()
    llm_with_tools = build_llm().bind_tools(tools)
    knowledge_graph = KnowledgeGraph()
    for chunk in all_splits[:20]:
        identity = fingerprint(
            {
                "text": chunk.page_content,
                "metadata": chunk.metadata,
                "prompt": EXTRACTION_TEMPLATE,
                "system": EXTRACTION_SYSTEM,
                "configuration": configuration(),
            }
        )
        chunk.metadata["chunk_id"] = identity
        extraction = cached_extraction(
            Path(PERSIST_DIR) / "graph" / f"{identity}.json",
            identity,
            lambda chunk=chunk: extract_entities_from_chunk(chunk),
        )
        ingest_chunk_into_graph(chunk, extraction)
    workflow = StateGraph(GraphRAGState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", ToolNode(tools))
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    workflow.add_edge("tools", "agent")

    checkpointer = MemorySaver()
    agent = workflow.compile(checkpointer=checkpointer)

    def answer_question(item):
        tracker, started_at = start_usage_tracker()
        result = query_graph_rag(item["question"], callbacks=[tracker])
        result["ground_truth"] = item["ground_truth"]
        result.update(finish_usage_tracker(tracker, started_at))
        return result

    return answer_question


def main():
    return execute_pipeline("graph-rag", prepare)


if __name__ == "__main__":
    main()
