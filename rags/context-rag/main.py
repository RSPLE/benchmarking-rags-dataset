import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from ragas_compat import ensure_ragas_langchain_compat

ensure_ragas_langchain_compat()

from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler
from langsmith import traceable

from benchmark_index import load_index
from benchmark_pipeline import execute_pipeline
from rag_provider import build_llm

load_dotenv(REPOSITORY_ROOT / ".env", override=False)

os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGCHAIN_TRACING_V2", "false")
os.environ["LANGSMITH_ENDPOINT"] = os.getenv(
    "LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"
)
os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGCHAIN_API_KEY", "")
os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGCHAIN_PROJECT", "benchmark-context-rag")


PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chroma_v2")
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "context_rag")

METRIC_COLS = [
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
]

USAGE_COLS = [
    "answer_response_time_seconds",
    "answer_input_tokens",
    "answer_output_tokens",
    "answer_total_tokens",
]

EXPORT_COLS = ["question", *METRIC_COLS, *USAGE_COLS]


def extract_response_text(response):
    text = getattr(response, "text", None)

    if isinstance(text, str) and text:
        return text

    content = getattr(response, "content", response)

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []

        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
                parts.append(block.get("text", ""))

        return "\n".join(part for part in parts if part)

    return str(content)


def _empty_token_usage():
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }


def _normalizar_token_usage(usage):
    tokens = _empty_token_usage()

    if not isinstance(usage, dict):
        return tokens

    tokens["input_tokens"] = (
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("prompt_token_count")
        or 0
    )
    tokens["output_tokens"] = (
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("completion_token_count")
        or 0
    )
    tokens["total_tokens"] = (
        usage.get("total_tokens")
        or usage.get("total_token_count")
        or tokens["input_tokens"] + tokens["output_tokens"]
    )

    return tokens


def _somar_token_usage(total, usage):
    total["input_tokens"] += usage.get("input_tokens", 0) or 0
    total["output_tokens"] += usage.get("output_tokens", 0) or 0
    total["total_tokens"] += usage.get("total_tokens", 0) or 0
    return total


def extract_token_usage(response):
    usage = getattr(response, "usage_metadata", None)

    if usage:
        return _normalizar_token_usage(usage)

    response_metadata = getattr(response, "response_metadata", None) or {}

    for key in ("token_usage", "usage"):
        if response_metadata.get(key):
            return _normalizar_token_usage(response_metadata[key])

    return _normalizar_token_usage(response_metadata)


def extract_llm_result_token_usage(result):
    llm_output = getattr(result, "llm_output", None) or {}

    for key in ("token_usage", "usage"):
        if isinstance(llm_output, dict) and llm_output.get(key):
            return _normalizar_token_usage(llm_output[key])

    usage = _normalizar_token_usage(llm_output)
    if usage["total_tokens"]:
        return usage

    total = _empty_token_usage()

    for generations in getattr(result, "generations", []) or []:
        for generation in generations:
            message = getattr(generation, "message", None)

            if message is not None:
                _somar_token_usage(total, extract_token_usage(message))

            generation_info = getattr(generation, "generation_info", None) or {}
            for key in ("token_usage", "usage"):
                if generation_info.get(key):
                    _somar_token_usage(total, _normalizar_token_usage(generation_info[key]))

    return total


class TokenUsageTracker(BaseCallbackHandler):
    def __init__(self):
        self._tokens = _empty_token_usage()

    def on_llm_end(self, response, **kwargs):
        _somar_token_usage(self._tokens, extract_llm_result_token_usage(response))

    @property
    def input_tokens(self):
        return self._tokens["input_tokens"]

    @property
    def output_tokens(self):
        return self._tokens["output_tokens"]

    @property
    def total_tokens(self):
        return self._tokens["total_tokens"]


def build_callback_config(callbacks):
    return {"callbacks": callbacks} if callbacks else None


def start_usage_tracker():
    return TokenUsageTracker(), time.perf_counter()


def finish_usage_tracker(tracker, started_at):
    return {
        "answer_response_time_seconds": round(time.perf_counter() - started_at, 6),
        "answer_input_tokens": tracker.input_tokens,
        "answer_output_tokens": tracker.output_tokens,
        "answer_total_tokens": tracker.total_tokens,
    }


def anexar_metricas_execucao(df, ragas_data):
    usage_by_question = {
        item["question"]: {col: item.get(col, 0) for col in USAGE_COLS} for item in ragas_data
    }

    for col in USAGE_COLS:
        df[col] = df["question"].map(
            lambda question, col=col: usage_by_question.get(question, {}).get(col, 0)
        )

    return df


def build_vectorstore():
    store, embeddings, _ = load_index(
        os.getenv("DOCS_DIR", "./docs"), PERSIST_DIR, CHROMA_COLLECTION_NAME
    )
    return store, embeddings


def context_rag(query, retriever, llm, callbacks=None):
    docs = retriever.invoke(query)

    contexts = [doc.page_content for doc in docs]
    context_text = "\n\n".join(contexts)
    callback_config = build_callback_config(callbacks)

    prompt = f"""
Você deve responder usando SOMENTE o contexto fornecido.

Contexto:
{context_text}

Pergunta:
{query}

Se a resposta não estiver no contexto, diga:
"A informação não está presente no contexto."
"""

    response = llm.invoke(prompt, config=callback_config)

    return extract_response_text(response), contexts, [doc.metadata for doc in docs]


@traceable(name="context-rag-query", run_type="chain")
def context_rag_traced(query, retriever, llm, callbacks=None):
    return context_rag(query, retriever, llm, callbacks=callbacks)


def prepare():
    store, embeddings = build_vectorstore()
    retriever = store.as_retriever(search_kwargs={"k": 5})
    llm = build_llm()

    def answer_question(item):
        tracker, started_at = start_usage_tracker()
        answer, contexts, evidence = context_rag_traced(
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
    return execute_pipeline("context-rag", prepare)


if __name__ == "__main__":
    main()
