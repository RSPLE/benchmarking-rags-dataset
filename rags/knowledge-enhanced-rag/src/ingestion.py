"""
Modulo de ingestao de PDFs para o KE-RAG.

Responsavel por:
- Carregar PDFs da pasta configurada
- Dividir em chunks de texto
- Gerar embeddings OpenAI e salvar em um indice Chroma persistido
"""

import os
from pathlib import Path

from langchain_community.document_loaders import PyPDFDirectoryLoader
from langchain_chroma import Chroma
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
    """
    Carrega todos os PDFs de uma pasta usando PyPDFDirectoryLoader.

    Args:
        pasta: Caminho para a pasta com os PDFs.

    Returns:
        Lista de objetos Document do LangChain.
    """
    arquivos_pdf = list(pasta.glob("*.pdf"))

    if not arquivos_pdf:
        print(f"Aviso: Nenhum PDF encontrado em '{pasta}'.")
        return []

    loader = PyPDFDirectoryLoader(str(pasta))
    documentos = loader.load()
    print(f"Total de páginas carregadas: {len(documentos)}")
    return documentos


def dividir_em_chunks(documentos: list) -> list:
    """
    Divide os documentos em chunks menores para indexacao.

    Args:
        documentos: Lista de objetos Document do LangChain.

    Returns:
        Lista de objetos Document do LangChain divididos em chunks.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=100,
        add_start_index=True,
    )

    chunks = splitter.split_documents(documentos)
    print(f"Total de chunks gerados: {len(chunks)}")
    return chunks


def criar_vectorstore() -> Chroma:
    """
    Inicializa o Chroma com embeddings OpenAI.

    Returns:
        Instancia de Chroma pronta para leitura ou escrita.
    """
    return Chroma(
        collection_name=CHROMA_COLLECTION_NAME,
        embedding_function=build_embeddings(),
        persist_directory=PERSIST_DIR,
    )


def criar_indice(chunks: list) -> Chroma:
    """
    Cria um novo indice Chroma a partir dos chunks com batching.

    Args:
        chunks: Lista de Documents do LangChain.

    Returns:
        Indice Chroma criado.
    """
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
    """
    Carrega o indice Chroma existente do disco.

    Returns:
        Indice Chroma carregado.
    """
    indice = criar_vectorstore()
    print("Indice Chroma inicializado.")
    return indice


def load_or_create_index() -> Chroma:
    """
    Verifica se o indice Chroma ja existe e o carrega; caso contrario, cria um novo.

    Returns:
        Indice Chroma pronto para uso.

    Raises:
        RuntimeError: Se nao houver PDFs e o indice nao existir.
    """
    indice = carregar_indice()

    if indice._collection.count() > 0:
        print(
            f"Indice Chroma encontrado com {indice._collection.count()} chunks. Pulando ingestao."
        )
        return indice

    print("Indice Chroma vazio. Criando novo indice...")
    documentos = carregar_pdfs()

    if not documentos:
        raise RuntimeError(
            "Nenhum PDF encontrado e nenhum indice existente. "
            f"Adicione PDFs em '{APOSTILAS_DIR}' e tente novamente."
        )

    chunks = dividir_em_chunks(documentos)
    return criar_indice(chunks)


def reindexar() -> Chroma:
    """
    Forca a recriacao do indice Chroma a partir dos PDFs atuais.

    Returns:
        Novo indice Chroma.

    Raises:
        RuntimeError: Se nao houver PDFs disponiveis.
    """
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
