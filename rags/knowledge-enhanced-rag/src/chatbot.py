"""
Módulo principal do Chatbot KE-RAG.

Implementa a lógica de conversação usando LangChain + OpenAI
combinada com o retriever KE-RAG.
"""

from collections import deque

from langchain_core.messages import HumanMessage, AIMessage

from rag_settings import (
    build_callback_config,
    build_llm,
    extract_response_text,
)
from src.retriever import KERagRetriever
from src.knowledge_graph import KnowledgeGraph


# Prompt template em português, tom amigável para iniciantes
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
    """
    Chatbot de Lógica de Programação baseado em KE-RAG.
    """

    def __init__(self, knowledge_graph: KnowledgeGraph | None = None):
        """
        Inicializa o chatbot com o retriever KE-RAG e o modelo LLM.

        Args:
            knowledge_graph: Instância do KnowledgeGraph (opcional).
        """
        self.llm = build_llm()

        self.retriever = KERagRetriever(knowledge_graph=knowledge_graph)

        # Histórico de conversas por sessão (últimas 5 pares de mensagens)
        self.memorias: dict[str, deque] = {}

    def _obter_memoria(self, session_id: str) -> deque:
        """
        Obtém ou cria a memória de conversa para uma sessão.

        Args:
            session_id: Identificador da sessão.

        Returns:
            Deque com as últimas mensagens da sessão.
        """
        if session_id not in self.memorias:
            # maxlen=10 para guardar 5 pares (pergunta + resposta)
            self.memorias[session_id] = deque(maxlen=10)
        return self.memorias[session_id]

    def chat(self, pergunta: str, session_id: str = "default", callbacks=None) -> dict:
        """
        Processa uma pergunta e retorna a resposta do chatbot.

        Args:
            pergunta: Pergunta do usuário.
            session_id: Identificador da sessão de conversa.

        Returns:
            Dicionário com:
                - answer: Resposta gerada pelo chatbot
                - prerequisites: Pré-requisitos do conceito identificado
                - next_concepts: Próximos conceitos sugeridos
        """
        # Recupera contexto via KE-RAG
        resultado = self.retriever.retrieve(pergunta)

        docs = resultado["docs"]
        kg_facts = resultado["kg_facts"]
        prerequisites = resultado["prerequisites"]
        next_concepts = resultado["next_concepts"]

        # Formata o contexto dos documentos
        if docs:
            contexto_docs = "\n\n".join(
                [
                    f"[Fonte: {doc.metadata.get('fonte', 'desconhecida')}, "
                    f"Pág. {doc.metadata.get('pagina', '?')}]\n{doc.page_content}"
                    for doc in docs
                ]
            )
        else:
            contexto_docs = "Nenhum trecho relevante encontrado nas apostilas."

        # Formata pré-requisitos e próximos conceitos
        prereqs_texto = (
            ", ".join(prerequisites)
            if prerequisites
            else "Nenhum pré-requisito específico identificado."
        )
        proximos_texto = (
            ", ".join(next_concepts) if next_concepts else "Continue praticando o conceito atual."
        )

        # Monta o prompt
        prompt_usuario = PROMPT_TEMPLATE.format(
            contexto_docs=contexto_docs,
            kg_facts=kg_facts or "Nenhum fato do Knowledge Graph disponível.",
            prerequisites=prereqs_texto,
            next_concepts=proximos_texto,
            pergunta=pergunta,
        )

        # Obtém o histórico da sessão
        memoria = self._obter_memoria(session_id)

        # Monta as mensagens com histórico
        mensagens = list(memoria) + [HumanMessage(content=prompt_usuario)]

        # Chama o LLM
        resposta = self.llm.invoke(
            mensagens,
            config=build_callback_config(callbacks),
        )
        resposta_texto = extract_response_text(resposta)

        # Salva no histórico
        memoria.append(HumanMessage(content=pergunta))
        memoria.append(AIMessage(content=resposta_texto))

        return {
            "answer": resposta_texto,
            "prerequisites": prerequisites,
            "next_concepts": next_concepts,
        }
