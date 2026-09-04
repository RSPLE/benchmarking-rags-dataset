"""
API REST do KE-RAG Chatbot.

Endpoints disponíveis:
- POST /chat         — Envia uma pergunta ao chatbot
- POST /index        — Reindexar os PDFs das apostilas
- POST /build-graph  — (Re)construir o Knowledge Graph no Neo4j
- GET  /health       — Verificação de saúde da API
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from rag_settings import configure_environment

configure_environment("benchmark-knowledge-enhanced-rag")

from src.chatbot import Chatbot
from src.ingestion import reindexar
from src.knowledge_graph import KnowledgeGraph


# Instâncias globais (inicializadas no startup)
chatbot: Chatbot | None = None
knowledge_graph: KnowledgeGraph | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia o ciclo de vida da aplicação."""
    global chatbot, knowledge_graph

    print("Inicializando KE-RAG Chatbot...")

    # Inicializa o Knowledge Graph (opcional)
    try:
        knowledge_graph = KnowledgeGraph()
        print("Knowledge Graph conectado com sucesso.")
    except Exception as e:
        print(f"Aviso: Knowledge Graph não disponível: {e}")
        knowledge_graph = None

    # Inicializa o Chatbot
    try:
        chatbot = Chatbot(knowledge_graph=knowledge_graph)
        print("Chatbot inicializado com sucesso.")
    except Exception as e:
        print(f"Erro ao inicializar o Chatbot: {e}")
        chatbot = None

    yield

    # Cleanup ao encerrar
    if knowledge_graph:
        knowledge_graph.fechar()
    print("API encerrada.")


# Cria a aplicação FastAPI
app = FastAPI(
    title="KE-RAG Chatbot — Lógica de Programação",
    description=(
        "Chatbot baseado em Knowledge Enhanced RAG para ajudar alunos "
        "iniciantes na matéria de Lógica de Programação."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Habilita CORS para todas as origens
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Modelos de Request/Response ---


class ChatRequest(BaseModel):
    """Modelo de requisição para o endpoint /chat."""

    question: str
    session_id: str = "default"


class ChatResponse(BaseModel):
    """Modelo de resposta do endpoint /chat."""

    answer: str
    prerequisites: list[str]
    next_concepts: list[str]


class MessageResponse(BaseModel):
    """Modelo de resposta genérico com mensagem."""

    message: str


class HealthResponse(BaseModel):
    """Modelo de resposta do health check."""

    status: str
    chatbot_ready: bool
    knowledge_graph_ready: bool


# --- Endpoints ---


@app.get("/health", response_model=HealthResponse, tags=["Sistema"])
async def health_check():
    """
    Verifica se a API está funcionando corretamente.

    Returns:
        Status da API e dos componentes.
    """
    return HealthResponse(
        status="ok",
        chatbot_ready=chatbot is not None,
        knowledge_graph_ready=knowledge_graph is not None,
    )


@app.post("/chat", response_model=ChatResponse, tags=["Chatbot"])
async def chat(request: ChatRequest):
    """
    Envia uma pergunta ao chatbot e recebe uma resposta.

    Args:
        request: Objeto com a pergunta e o ID da sessão.

    Returns:
        Resposta do chatbot com pré-requisitos e próximos conceitos.
    """
    if chatbot is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Chatbot não está disponível. "
                "Verifique se a OPENAI_API_KEY está configurada corretamente."
            ),
        )

    if not request.question.strip():
        raise HTTPException(
            status_code=400,
            detail="A pergunta não pode estar vazia.",
        )

    try:
        resultado = chatbot.chat(
            pergunta=request.question,
            session_id=request.session_id,
        )
        return ChatResponse(**resultado)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao processar a pergunta: {str(e)}",
        )


@app.post("/index", response_model=MessageResponse, tags=["Administração"])
async def reindexar_pdfs():
    """
    Reindexar os PDFs da pasta data/apostilas/.

    Use este endpoint sempre que adicionar novos PDFs às apostilas.

    Returns:
        Mensagem de confirmação.
    """
    global chatbot

    try:
        novo_indice = reindexar()

        # Atualiza o índice no chatbot se ele estiver ativo
        if chatbot is not None:
            chatbot.retriever.indice = novo_indice

        return MessageResponse(message="PDFs reindexados com sucesso!")
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao reindexar PDFs: {str(e)}",
        )


@app.post("/build-graph", response_model=MessageResponse, tags=["Administração"])
async def construir_grafo():
    """
    (Re)construir o Knowledge Graph de Lógica de Programação no Neo4j.

    Use este endpoint para recriar o grafo de conhecimento.

    Returns:
        Mensagem de confirmação.
    """
    global knowledge_graph, chatbot

    try:
        if knowledge_graph is None:
            knowledge_graph = KnowledgeGraph()
            if chatbot is not None:
                chatbot.retriever.kg = knowledge_graph

        knowledge_graph.build_graph()

        return MessageResponse(message="Knowledge Graph construído com sucesso!")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao construir o Knowledge Graph: {str(e)}",
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
