import os
import uuid
from typing import Any, Dict

from langchain.agents import create_agent
from langchain.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langsmith import traceable
from rag_settings import (
    build_llm,
    configure_environment,
    extract_response_text,
    finish_usage_tracker,
    get_chroma_settings,
    start_usage_tracker,
)

from benchmark_config import positive_int
from benchmark_index import load_index
from benchmark_pipeline import execute_pipeline, tool_evidence

configure_environment("benchmark-memory-augmented-rag")

DOCS_DIR = os.getenv("DOCS_DIR", "./docs/")
PERSIST_DIR, CHROMA_COLLECTION_NAME = get_chroma_settings(
    "./chroma_memory_db_openai",
    "memory_collection_openai",
)


def build_vectorstore():
    store, embeddings, _ = load_index(
        os.getenv("DOCS_DIR", "./docs"), PERSIST_DIR, CHROMA_COLLECTION_NAME
    )
    return store, embeddings


def build_agent(vector_store, llm):
    @tool(
        response_format="content_and_artifact",
        description="Retrieve information to help answer a query.",
    )
    def retrieve_context(query: str):
        retrieved_docs = vector_store.similarity_search(query, k=5)
        serialized = "\n\n".join(
            (f"Source: {doc.metadata}\nContent: {doc.page_content}") for doc in retrieved_docs
        )
        return serialized, retrieved_docs

    tools = [retrieve_context]

    prompt = (
        "Voce tem acesso a uma ferramenta que recupera contexto dos documentos. "
        "Use a ferramenta para responder as perguntas do usuario. "
        "Responda sempre em portugues, mesmo que a pergunta seja feita em outro idioma."
    )

    return create_agent(llm, tools, system_prompt=prompt, checkpointer=MemorySaver())


@traceable(name="memory-augmented-rag-query", run_type="chain")
def run_agent_and_collect_data(
    agent,
    vector_store,
    query: str,
    ground_truth: str,
    callbacks=None,
) -> Dict[str, Any]:
    thread_id = str(uuid.uuid4())
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": positive_int("BENCHMARK_AGENT_RECURSION_LIMIT", 12),
    }

    if callbacks:
        config["callbacks"] = callbacks

    events = list(
        agent.stream(
            {"messages": [{"role": "user", "content": query}]},
            config=config,
            stream_mode="values",
        )
    )

    final_event = events[-1]
    answer = extract_response_text(final_event["messages"][-1])

    contexts, evidence = tool_evidence(final_event["messages"])

    return {
        "question": query,
        "contexts": contexts,
        "answer": answer,
        "ground_truth": ground_truth,
        "evidence_metadata": evidence,
    }


def prepare():
    vector_store, embeddings = build_vectorstore()
    agent = build_agent(vector_store, build_llm())

    def answer_question(item):
        tracker, started_at = start_usage_tracker()
        result = run_agent_and_collect_data(
            agent, vector_store, item["question"], item["ground_truth"], callbacks=[tracker]
        )
        result.update(finish_usage_tracker(tracker, started_at))
        return result

    return answer_question


if __name__ == "__main__":
    execute_pipeline("memory-augmented-rag", prepare)
