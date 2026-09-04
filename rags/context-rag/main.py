import os
from pathlib import Path
import sys
import time
from dotenv import load_dotenv
from itertools import count

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from ragas_compat import ensure_ragas_langchain_compat

ensure_ragas_langchain_compat()

from langchain_core.callbacks import BaseCallbackHandler
from langchain_community.document_loaders import DirectoryLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma

from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
from datasets import Dataset

from langsmith import traceable

from rag_provider import build_embeddings, build_llm, build_ragas_llm
from benchmark_runner import run_resumable_benchmark


load_dotenv(REPOSITORY_ROOT / ".env", override=False)

os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGCHAIN_TRACING_V2", "false")
os.environ["LANGSMITH_ENDPOINT"] = os.getenv(
    "LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"
)
os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGCHAIN_API_KEY", "")
os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGCHAIN_PROJECT", "benchmark-context-rag")


PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chroma_context_db_openai")
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "context_collection_openai")

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


test_queries = [
    # FÁCEIS
    "O que significa ‘lógica de programação’ em palavras simples?",
    "De um jeito bem direto: o que é um algoritmo?",
    "Qual é a diferença entre constante e variável?",
    "Pra que serve o comando ‘leia’ em um algoritmo?",
    # MÉDIAS
    "O que é um comando de atribuição e por que o tipo do dado precisa ser compatível com o tipo da variável?",
    "O que são operadores aritméticos (como +, -, * e /) e pra que eles servem?",
    "Pra que servem os operadores relacionais numa expressão?",
    # DIFÍCEIS
    "O que é uma ‘expressão lógica’?",
    "Em uma repetição, o que é um contador e como ele é incrementado?",
    "Como funciona a repetição ‘repita ... até’ e o que ela garante sobre a execução do bloco?",
]


ground_truths = [
    # FÁCEIS
    "Lógica de programação é o uso correto das leis do pensamento, da ‘ordem da razão’ e de processos formais de raciocínio e simbolização na programação de computadores, com o objetivo de produzir soluções logicamente válidas e coerentes para resolver problemas.",
    "Um algoritmo é uma sequência de passos bem definidos que têm por objetivo solucionar um determinado problema.",
    "Um dado é constante quando não sofre variação durante a execução do algoritmo: seu valor permanece constante do início ao fim (e também em execuções diferentes ao longo do tempo). Já um dado é variável quando pode ser alterado em algum instante durante a execução do algoritmo, ou quando seu valor depende da execução em um certo momento ou circunstância.",
    "O comando de entrada de dados ‘leia’ é usado para que o algoritmo receba os dados de que precisa: ele tem a finalidade de atribuir o dado fornecido à variável identificada, seguindo a sintaxe leia(identificador) (por exemplo, leia(X) ou leia(A, XPTO, NOTA)).",
    # MÉDIAS
    "Um comando de atribuição permite fornecer um valor a uma variável. O tipo do dado atribuído deve ser compatível com o tipo da variável: por exemplo, só se pode atribuir um valor lógico a uma variável declarada como do tipo lógico.",
    "Operadores aritméticos são o conjunto de símbolos que representam as operações básicas da matemática (por exemplo: + para adição, - para subtração, * para multiplicação e / para divisão). Para potenciação e radiciação, o livro indica o uso das palavras-chave pot e rad.",
    "Operadores relacionais são usados para realizar comparações entre dois valores de mesmo tipo primitivo. Esses valores podem ser constantes, variáveis ou expressões aritméticas, e esses operadores são comuns na construção de equações.",
    # DIFÍCEIS
    "Uma expressão lógica é aquela cujos operadores são lógicos ou relacionais e cujos operandos são relações, variáveis ou constantes do tipo lógico.",
    "Um contador é um modo de contagem feito com a ajuda de uma variável com um valor inicial, que é incrementada a cada repetição. Incrementar significa somar um valor constante (normalmente 1) a cada repetição.",
    "A estrutura de repetição ‘repita ... até’ permite que um bloco (ou ação primitiva) seja repetido até que uma determinada condição seja verdadeira. Pela sintaxe da estrutura, o bloco é executado pelo menos uma vez, independentemente da validade inicial da condição.",
]


def extract_response_text(response):
    text = getattr(response, "text", None)

    if callable(text):
        text = text()

    if text:
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
            lambda question: usage_by_question.get(question, {}).get(col, 0)
        )

    return df


def build_vectorstore():
    embeddings = build_embeddings()

    vectordb = Chroma(
        collection_name=CHROMA_COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=PERSIST_DIR,
    )

    if vectordb._collection.count() == 0:
        loader = DirectoryLoader("./docs/", glob="**/*.pdf", loader_cls=PyPDFLoader)

        docs = loader.load()

        splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)

        chunks = splitter.split_documents(docs)

        print(f"Adicionando {len(chunks)} chunks ao Chroma em batches...")

        batch_size = 500

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            vectordb.add_documents(documents=batch)
            print(f"  {min(i + batch_size, len(chunks))}/{len(chunks)} chunks adicionados")

        print("Ingestão concluída!")

    else:
        print(f"Coleção existente com {vectordb._collection.count()} chunks. Pulando ingestão.")

    return vectordb, embeddings


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

    return extract_response_text(response), contexts


@traceable(name="context-rag-query", run_type="chain")
def context_rag_traced(query, retriever, llm, callbacks=None):
    return context_rag(query, retriever, llm, callbacks=callbacks)


def executar_context_rag(retriever, llm):
    ragas_data = []

    print("Coletando respostas do Context RAG...")

    for i, query in enumerate(test_queries):
        print(f"  [{i + 1}/{len(test_queries)}] {query}")

        tracker, started_at = start_usage_tracker()
        answer, contexts = context_rag_traced(
            query,
            retriever,
            llm,
            callbacks=[tracker],
        )

        ragas_item = {
            "question": query,
            "answer": answer,
            "contexts": contexts,
            "ground_truth": ground_truths[i],
        }
        ragas_item.update(finish_usage_tracker(tracker, started_at))
        ragas_data.append(ragas_item)

    return ragas_data


def preparar_export_ragas(df):
    df = df.rename(columns={"user_input": "question"})

    missing_cols = [col for col in EXPORT_COLS if col not in df.columns]
    if missing_cols:
        raise RuntimeError(f"Resultado do RAGAS sem colunas esperadas: {', '.join(missing_cols)}")

    null_metrics = df[df[METRIC_COLS].isnull().any(axis=1)]
    if not null_metrics.empty:
        failed_questions = null_metrics["question"].fillna("<pergunta ausente>").tolist()
        raise RuntimeError(f"RAGAS retornou metricas nulas para as perguntas: {failed_questions}")

    return df[EXPORT_COLS]


def run_ragas(ragas_data, llm, embeddings):
    dataset = Dataset.from_list(ragas_data)

    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=llm,
        embeddings=embeddings,
        raise_exceptions=True,
    )

    print("=== RESULTADOS RAGAS ===")
    print(result)

    df = result.to_pandas().rename(columns={"user_input": "question"})
    df = anexar_metricas_execucao(df, ragas_data)
    df = preparar_export_ragas(df)

    print("Detalhes por query:")
    print(df.to_string(index=False))

    return df


def salvar(df, nome_base="context-rag"):
    if not hasattr(salvar, "_results_dir"):
        base_dir = "results"

        if not os.path.exists(base_dir):
            os.makedirs(base_dir, exist_ok=False)
            salvar._results_dir = base_dir
        else:
            for n in count(2):
                candidate = f"{base_dir}_{n}"

                if not os.path.exists(candidate):
                    os.makedirs(candidate, exist_ok=False)
                    salvar._results_dir = candidate
                    break

        print(f"Resultados desta execução serão salvos em: {salvar._results_dir}")

    os.makedirs(salvar._results_dir, exist_ok=True)

    for i in count(1):
        nome = os.path.join(salvar._results_dir, f"{nome_base}_{i}.csv")

        if not os.path.exists(nome):
            df.to_csv(nome, index=False, encoding="utf-8-sig", sep=";")

            print(f"Salvo em: {nome}")
            break


def main():
    vectordb, embeddings = build_vectorstore()
    retriever = vectordb.as_retriever(search_kwargs={"k": 5})
    print(f"Vectorstore pronto: {vectordb._collection.count()} chunks indexados.")
    answer_llm = build_llm()
    eval_llm = build_ragas_llm()

    def answer_question(item):
        tracker, started_at = start_usage_tracker()
        answer, contexts = context_rag_traced(
            item["question"],
            retriever,
            answer_llm,
            callbacks=[tracker],
        )
        ragas_item = {
            "question": item["question"],
            "answer": answer,
            "contexts": contexts,
            "ground_truth": item["ground_truth"],
        }
        ragas_item.update(finish_usage_tracker(tracker, started_at))
        return ragas_item

    def evaluate_question(ragas_item):
        result = run_ragas([ragas_item], eval_llm, embeddings)
        return result.iloc[0].to_dict()

    counts = run_resumable_benchmark(
        "context-rag",
        answer_question,
        evaluate_question,
    )
    if counts["run_failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
