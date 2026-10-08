import os

from langchain_classic.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langsmith import traceable
from rag_settings import (
    build_callback_config,
    build_llm,
    configure_environment,
    extract_response_text,
    finish_usage_tracker,
    get_chroma_settings,
    get_int_env,
    start_usage_tracker,
)

from app.benchmark.index import load_index
from app.benchmark.pipeline import execute_pipeline

configure_environment("benchmark-hybrid-rag")

DOCS_DIR = os.getenv("DOCS_DIR", "./docs/")
PERSIST_DIR, CHROMA_COLLECTION_NAME = get_chroma_settings(
    "./chroma_hybrid_db_openai",
    "hybrid_collection_openai",
)
RETRIEVER_K = get_int_env("RETRIEVER_K", 3)


def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)


def build_hybrid_retriever():
    vector_store, embeddings, all_splits = load_index(DOCS_DIR, PERSIST_DIR, CHROMA_COLLECTION_NAME)
    vector_retriever = vector_store.as_retriever(search_kwargs={"k": RETRIEVER_K})
    bm25_retriever = BM25Retriever.from_documents(all_splits, k=RETRIEVER_K)
    hybrid_retriever = EnsembleRetriever(
        retrievers=[bm25_retriever, vector_retriever], weights=[0.4, 0.6]
    )
    return hybrid_retriever, embeddings, vector_store


@traceable(name="hybrid-rag-query", run_type="chain")
def hybrid_rag(query, retriever, llm, callbacks=None):
    context_docs = retriever.invoke(query)
    contexts = [doc.page_content for doc in context_docs]
    context = format_docs(context_docs)

    prompt = f"""Você é um assistente útil. Use o contexto abaixo para responder a pergunta.
Se não souber a resposta com base no contexto, diga que não sabe.

Contexto:
{context}

Pergunta:
{query}

Resposta:"""

    answer = extract_response_text(llm.invoke(prompt, config=build_callback_config(callbacks)))
    return answer, contexts, [doc.metadata for doc in context_docs]


def prepare():
    retriever, embeddings, store = build_hybrid_retriever()
    llm = build_llm()

    def answer_question(item):
        tracker, started_at = start_usage_tracker()
        answer, contexts, evidence = hybrid_rag(
            item["question"], retriever, llm, callbacks=[tracker]
        )
        return {
            "question": item["question"],
            "answer": answer,
            "contexts": contexts,
            "ground_truth": item["ground_truth"],
            "evidence_metadata": evidence,
            **finish_usage_tracker(tracker, started_at),
        }

    return answer_question


def main():
    return execute_pipeline("hybrid-rag", prepare)


if __name__ == "__main__":
    main()
