import os
import uuid
from typing import Dict, Any

from langchain_chroma import Chroma
from langchain_community.document_loaders import DirectoryLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.tools import tool
from langchain.agents import create_agent

from langgraph.checkpoint.memory import MemorySaver
from langsmith import traceable

from rag_settings import (
    build_embeddings,
    build_llm,
    build_ragas_llm,
    configure_environment,
    extract_response_text,
    finish_usage_tracker,
    get_chroma_settings,
    run_ragas,
    salvar,
    start_usage_tracker,
)
from benchmark_runner import run_resumable_benchmark

configure_environment("benchmark-memory-augmented-rag")

DOCS_DIR = os.getenv("DOCS_DIR", "./docs/")
PERSIST_DIR, CHROMA_COLLECTION_NAME = get_chroma_settings(
    "./chroma_memory_db_openai",
    "memory_collection_openai",
)

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


def build_vectorstore():
    embeddings = build_embeddings()

    vector_store = Chroma(
        collection_name=CHROMA_COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=PERSIST_DIR,
    )

    if vector_store._collection.count() == 0:
        loader = DirectoryLoader(path=DOCS_DIR, glob="**/*.pdf", loader_cls=PyPDFLoader)
        docs = loader.load()

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=800, chunk_overlap=100, add_start_index=True
        )

        all_splits = text_splitter.split_documents(docs)

        print(f"Adicionando {len(all_splits)} chunks ao Chroma em batches...")
        batch_size = 500

        for i in range(0, len(all_splits), batch_size):
            batch = all_splits[i : i + batch_size]
            vector_store.add_documents(documents=batch)
            print(f"  {min(i + batch_size, len(all_splits))}/{len(all_splits)} chunks adicionados")

        print("Ingestão concluída!")
    else:
        print(f"Coleção existente com {vector_store._collection.count()} chunks. Pulando ingestão.")

    return vector_store, embeddings


def build_agent(vector_store, llm):
    @tool(response_format="content_and_artifact")
    def retrieve_context(query: str):
        """Retrieve information to help answer a query."""
        retrieved_docs = vector_store.similarity_search(query, k=5)
        serialized = "\n\n".join(
            (f"Source: {doc.metadata}\nContent: {doc.page_content}") for doc in retrieved_docs
        )
        return serialized, retrieved_docs

    tools = [retrieve_context]

    prompt = (
        "Voce tem acesso a uma ferramenta que recupera contexto dos documentos. "
        "Use a ferramenta para responder as perguntas do usuario. "
        "Responda sempre em portugues, mesmo que a pergunta seja feita em outro idioma."
    )

    return create_agent(llm, tools, system_prompt=prompt, checkpointer=MemorySaver())


@traceable(name="memory-augmented-rag-query", run_type="chain")
def run_agent_and_collect_data(
    agent,
    vector_store,
    query: str,
    ground_truth: str,
    callbacks=None,
) -> Dict[str, Any]:
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    if callbacks:
        config["callbacks"] = callbacks

    events = list(
        agent.stream(
            {"messages": [{"role": "user", "content": query}]},
            config=config,
            stream_mode="values",
        )
    )

    final_event = events[-1]
    answer = extract_response_text(final_event["messages"][-1])

    retrieved_docs = vector_store.similarity_search(query, k=5)
    contexts = [doc.page_content for doc in retrieved_docs]

    return {"question": query, "contexts": contexts, "answer": answer, "ground_truth": ground_truth}


def evaluate_with_ragas(agent, vector_store, eval_llm, embeddings):
    """Executa avaliação completa com RAGAS."""
    print("Executando agent para coletar dados de teste...")
    ragas_data = []

    for i, query in enumerate(test_queries):
        print(f"  [{i + 1}/{len(test_queries)}] {query}")
        tracker, started_at = start_usage_tracker()
        data_point = run_agent_and_collect_data(
            agent,
            vector_store,
            query,
            ground_truths[i],
            callbacks=[tracker],
        )
        data_point.update(finish_usage_tracker(tracker, started_at))
        ragas_data.append(data_point)

    print("\nExecutando avaliação RAGAS...")
    return run_ragas(ragas_data, eval_llm, embeddings)


if __name__ == "__main__":
    vector_store, embeddings = build_vectorstore()
    print(f"Vectorstore pronto: {vector_store._collection.count()} chunks indexados.")
    answer_llm = build_llm()
    eval_llm = build_ragas_llm()
    agent = build_agent(vector_store, answer_llm)

    def answer_question(item):
        tracker, started_at = start_usage_tracker()
        ragas_item = run_agent_and_collect_data(
            agent,
            vector_store,
            item["question"],
            item["ground_truth"],
            callbacks=[tracker],
        )
        ragas_item.update(finish_usage_tracker(tracker, started_at))
        return ragas_item

    def evaluate_question(ragas_item):
        result = run_ragas([ragas_item], eval_llm, embeddings)
        return result.iloc[0].to_dict()

    counts = run_resumable_benchmark(
        "memory-augmented-rag",
        answer_question,
        evaluate_question,
    )
    if counts["run_failed"]:
        raise SystemExit(1)
