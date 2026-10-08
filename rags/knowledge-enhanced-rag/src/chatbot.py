from collections import deque

from langchain_core.messages import AIMessage, HumanMessage
from rag_settings import (
    build_callback_config,
    build_llm,
    extract_response_text,
)

from src.knowledge_graph import KnowledgeGraph
from src.retriever import KERagRetriever

PROMPT_TEMPLATE = """Você é um professor assistente de Lógica de Programação, especialista em ajudar alunos iniciantes de Computação. Seu tom é amigável, paciente e didático.

Use as informações abaixo para responder à pergunta do aluno de forma clara e completa.

--- CONTEXTO DAS APOSTILAS ---
{contexto_docs}

--- FATOS DO KNOWLEDGE GRAPH ---
{kg_facts}

--- PRÉ-REQUISITOS DO CONCEITO ---
{prerequisites}

--- PRÓXIMOS CONCEITOS A ESTUDAR ---
{next_concepts}

Instruções:
- Responda sempre em português brasileiro
- Use exemplos simples e práticos quando possível
- Se o aluno não entender algo, sugira os pré-requisitos listados acima
- Ao final, sugira o próximo conceito a estudar se houver
- Se não encontrar informação nas apostilas, use seu conhecimento geral sobre o tema
- Seja encorajador e positivo

Pergunta do aluno: {pergunta}"""


class Chatbot:
    def __init__(self, knowledge_graph: KnowledgeGraph | None = None):
        self.llm = build_llm()

        self.retriever = KERagRetriever(knowledge_graph=knowledge_graph)

        self.memorias: dict[str, deque] = {}

    def _obter_memoria(self, session_id: str) -> deque:
        if session_id not in self.memorias:
            self.memorias[session_id] = deque(maxlen=10)
        return self.memorias[session_id]

    def chat(self, pergunta: str, session_id: str | None = "default", callbacks=None) -> dict:

        resultado = self.retriever.retrieve(pergunta)

        docs = resultado["docs"]
        kg_facts = resultado["kg_facts"]
        prerequisites = resultado["prerequisites"]
        next_concepts = resultado["next_concepts"]

        if docs:
            contexto_docs = "\n\n".join(
                [
                    f"[Fonte: {doc.metadata.get('source', doc.metadata.get('fonte', 'desconhecida'))}, "
                    f"Pág. {doc.metadata.get('page', doc.metadata.get('pagina', '?'))}]\n{doc.page_content}"
                    for doc in docs
                ]
            )
        else:
            contexto_docs = "Nenhum trecho relevante encontrado nas apostilas."

        prereqs_texto = (
            ", ".join(prerequisites)
            if prerequisites
            else "Nenhum pré-requisito específico identificado."
        )
        proximos_texto = (
            ", ".join(next_concepts) if next_concepts else "Continue praticando o conceito atual."
        )

        prompt_usuario = PROMPT_TEMPLATE.format(
            contexto_docs=contexto_docs,
            kg_facts=kg_facts or "Nenhum fato do Knowledge Graph disponível.",
            prerequisites=prereqs_texto,
            next_concepts=proximos_texto,
            pergunta=pergunta,
        )

        memoria = self._obter_memoria(session_id) if session_id is not None else deque(maxlen=10)

        mensagens = list(memoria) + [HumanMessage(content=prompt_usuario)]

        resposta = self.llm.invoke(
            mensagens,
            config=build_callback_config(callbacks),
        )
        resposta_texto = extract_response_text(resposta)

        memoria.append(HumanMessage(content=pergunta))
        memoria.append(AIMessage(content=resposta_texto))

        return {
            "answer": resposta_texto,
            "contexts": [doc.page_content for doc in docs],
            "generation_contexts": [
                contexto_docs,
                kg_facts or "Nenhum fato do Knowledge Graph disponível.",
                prereqs_texto,
                proximos_texto,
            ],
            "evidence_metadata": [doc.metadata for doc in docs],
            "generation_evidence_metadata": [
                {"sources": [doc.metadata for doc in docs], "kg_mode": resultado["kg_mode"]}
            ],
            "prerequisites": prerequisites,
            "next_concepts": next_concepts,
            "conversation_history": [
                {"role": message.type, "content": message.content} for message in memoria
            ],
        }
