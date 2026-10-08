import os

from langsmith import traceable
from rag_settings import (
    build_callback_config,
    build_llm,
    configure_environment,
    extract_response_text,
    finish_usage_tracker,
    get_chroma_settings,
    start_usage_tracker,
)

from app.benchmark.index import load_index
from app.benchmark.pipeline import critique_decision, execute_pipeline

configure_environment("benchmark-self-rag")

DOCS_DIR = os.getenv("DOCS_DIR", "./docs/")
PERSIST_DIR, CHROMA_COLLECTION_NAME = get_chroma_settings(
    "./chroma_self_db_openai",
    "self_rag_contexts_openai",
)


def build_vectorstore():
    store, embeddings, _ = load_index(
        os.getenv("DOCS_DIR", "./docs"), PERSIST_DIR, CHROMA_COLLECTION_NAME
    )
    return store, embeddings


def self_rag(query, retriever, llm, callbacks=None):
    docs = retriever.invoke(query)
    contexts = [d.page_content for d in docs]
    context = "\n\n".join(contexts)
    callback_config = build_callback_config(callbacks)

    prompt = f"""
    Contexto:
    {context}

    Pergunta:
    {query}

    Responda usando apenas o contexto.
    """

    response = extract_response_text(llm.invoke(prompt, config=callback_config))

    critique_prompt = f"""
    Pergunta: {query}
    Resposta: {response}

    A resposta está fundamentada no contexto?
    Responda apenas SIM ou NAO.
    """

    critique = extract_response_text(llm.invoke(critique_prompt, config=callback_config))

    if not critique_decision(critique):
        refine_prompt = f"""
        Refaça a resposta usando melhor o contexto.

        Contexto:
        {context}

        Pergunta:
        {query}
        """
        response = extract_response_text(llm.invoke(refine_prompt, config=callback_config))

    return response, contexts, [doc.metadata for doc in docs]


@traceable(name="self-rag-query", run_type="chain")
def self_rag_traced(query, retriever, llm, callbacks=None):
    return self_rag(query, retriever, llm, callbacks=callbacks)


def prepare():
    store, embeddings = build_vectorstore()
    retriever = store.as_retriever(search_kwargs={"k": 5})
    llm = build_llm()

    def answer_question(item):
        tracker, started_at = start_usage_tracker()
        answer, contexts, evidence = self_rag_traced(
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
    return execute_pipeline("self-rag", prepare)


if __name__ == "__main__":
    main()
