import os
from pathlib import Path

from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFDirectoryLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from rag_settings import build_embeddings, get_chroma_settings

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DOCS_DIR = BASE_DIR / "data" / "apostilas"
APOSTILAS_DIR = Path(os.getenv("DOCS_DIR", str(DEFAULT_DOCS_DIR))).resolve()

PERSIST_DIR, CHROMA_COLLECTION_NAME = get_chroma_settings(
    "./chroma_knowledge_db_openai",
    "knowledge_collection_openai",
)


def carregar_pdfs(pasta: Path = APOSTILAS_DIR) -> list:
    arquivos_pdf = list(pasta.glob("*.pdf"))

    if not arquivos_pdf:
        print(f"Aviso: Nenhum PDF encontrado em '{pasta}'.")
        return []

    loader = PyPDFDirectoryLoader(str(pasta))
    documentos = loader.load()
    print(f"Total de páginas carregadas: {len(documentos)}")
    return documentos


def dividir_em_chunks(documentos: list) -> list:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=100,
        add_start_index=True,
    )

    chunks = splitter.split_documents(documentos)
    print(f"Total de chunks gerados: {len(chunks)}")
    return chunks


def criar_vectorstore() -> Chroma:
    return Chroma(
        collection_name=CHROMA_COLLECTION_NAME,
        embedding_function=build_embeddings(),
        persist_directory=PERSIST_DIR,
    )


def criar_indice(chunks: list) -> Chroma:
    print(f"Gerando embeddings e criando indice Chroma ({len(chunks)} chunks)...")
    indice = criar_vectorstore()
    batch_size = 500

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        indice.add_documents(documents=batch)
        print(f"  {min(i + batch_size, len(chunks))}/{len(chunks)} chunks indexados")

    print(f"Indice Chroma salvo em '{PERSIST_DIR}'.")
    return indice


def carregar_indice() -> Chroma:
    indice = criar_vectorstore()
    print("Indice Chroma inicializado.")
    return indice


def load_or_create_index() -> Chroma:
    from benchmark_index import load_index

    store, _, _ = load_index(APOSTILAS_DIR, PERSIST_DIR, CHROMA_COLLECTION_NAME)
    return store


def reindexar() -> Chroma:
    print("Reindexando PDFs...")
    documentos = carregar_pdfs()

    if not documentos:
        raise RuntimeError(
            f"Nenhum PDF encontrado em '{APOSTILAS_DIR}'. Adicione PDFs antes de reindexar."
        )

    indice = criar_vectorstore()
    existing_ids = indice.get().get("ids", [])

    if existing_ids:
        indice.delete(ids=existing_ids)

    chunks = dividir_em_chunks(documentos)
    return criar_indice(chunks)
