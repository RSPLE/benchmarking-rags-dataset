from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from rag_settings import configure_environment

configure_environment("benchmark-knowledge-enhanced-rag")

from src.chatbot import Chatbot
from src.ingestion import reindexar
from src.knowledge_graph import KnowledgeGraph

chatbot: Chatbot | None = None
knowledge_graph: KnowledgeGraph | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global chatbot, knowledge_graph

    print("Inicializando KE-RAG Chatbot...")

    try:
        knowledge_graph = KnowledgeGraph()
        print("Knowledge Graph conectado com sucesso.")
    except Exception as e:
        print(f"Aviso: Knowledge Graph não disponível: {e}")
        knowledge_graph = None

    try:
        chatbot = Chatbot(knowledge_graph=knowledge_graph)
        print("Chatbot inicializado com sucesso.")
    except Exception as e:
        print(f"Erro ao inicializar o Chatbot: {e}")
        chatbot = None

    yield

    if knowledge_graph:
        knowledge_graph.fechar()
    print("API encerrada.")


app = FastAPI(
    title="KE-RAG Chatbot — Lógica de Programação",
    description=(
        "Chatbot baseado em Knowledge Enhanced RAG para ajudar alunos "
        "iniciantes na matéria de Lógica de Programação."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    question: str
    session_id: str = "default"


class ChatResponse(BaseModel):
    answer: str
    prerequisites: list[str]
    next_concepts: list[str]


class MessageResponse(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str
    chatbot_ready: bool
    knowledge_graph_ready: bool


@app.get("/health", response_model=HealthResponse, tags=["Sistema"])
async def health_check():
    return HealthResponse(
        status="ok",
        chatbot_ready=chatbot is not None,
        knowledge_graph_ready=knowledge_graph is not None,
    )


@app.post("/chat", response_model=ChatResponse, tags=["Chatbot"])
async def chat(request: ChatRequest):
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
        ) from e


@app.post("/index", response_model=MessageResponse, tags=["Administração"])
async def reindexar_pdfs():
    global chatbot

    try:
        novo_indice = reindexar()

        if chatbot is not None:
            chatbot.retriever.indice = novo_indice

        return MessageResponse(message="PDFs reindexados com sucesso!")
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao reindexar PDFs: {str(e)}",
        ) from e


@app.post("/build-graph", response_model=MessageResponse, tags=["Administração"])
async def construir_grafo():
    global knowledge_graph, chatbot

    try:
        if knowledge_graph is None:
            knowledge_graph = KnowledgeGraph()
            if chatbot is not None:
                chatbot.retriever.kg = knowledge_graph

        knowledge_graph.build_graph()

        return MessageResponse(message="Knowledge Graph construído com sucesso!")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao construir o Knowledge Graph: {str(e)}",
        ) from e


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
