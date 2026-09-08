import os

from langchain_community.document_loaders import DirectoryLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma

from langsmith import traceable

from rag_settings import (
    build_callback_config,
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

configure_environment("benchmark-self-rag")

DOCS_DIR = os.getenv("DOCS_DIR", "./docs/")
PERSIST_DIR, CHROMA_COLLECTION_NAME = get_chroma_settings(
    "./chroma_self_db_openai",
    "self_rag_contexts_openai",
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
    "Como funciona a repetição ‘repita ... até’ e o que ela garante sobre a execução do bloco?"
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
    "A estrutura de repetição ‘repita ... até’ permite que um bloco (ou ação primitiva) seja repetido até que uma determinada condição seja verdadeira. Pela sintaxe da estrutura, o bloco é executado pelo menos uma vez, independentemente da validade inicial da condição."
]

def build_vectorstore():
    embeddings = build_embeddings()

    vectordb = Chroma(
        collection_name=CHROMA_COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=PERSIST_DIR,
    )

    if vectordb._collection.count() == 0:
        loader = DirectoryLoader(DOCS_DIR, glob="**/*.pdf", loader_cls=PyPDFLoader)
        docs = loader.load()

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            chunk_overlap=100
        )
        chunks = splitter.split_documents(docs)

        print(f"Adicionando {len(chunks)} chunks ao Chroma em batches...")

        batch_size = 500

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            vectordb.add_documents(documents=batch)
            print(f"  {min(i + batch_size, len(chunks))}/{len(chunks)} chunks adicionados")

        print("Ingestão concluída!")
    else:
        print(f"Coleção existente com {vectordb._collection.count()} chunks. Pulando ingestão.")

    return vectordb, embeddings


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

    # Auto-crítica simples
    critique_prompt = f"""
    Pergunta: {query}
    Resposta: {response}

    A resposta está fundamentada no contexto?
    Responda apenas SIM ou NAO.
    """

    critique = extract_response_text(llm.invoke(critique_prompt, config=callback_config))

    if "NAO" in critique.upper():
        refine_prompt = f"""
        Refaça a resposta usando melhor o contexto.

        Contexto:
        {context}

        Pergunta:
        {query}
        """
        response = extract_response_text(llm.invoke(refine_prompt, config=callback_config))

    return response, contexts


@traceable(name="self-rag-query", run_type="chain")
def self_rag_traced(query, retriever, llm, callbacks=None):
    return self_rag(query, retriever, llm, callbacks=callbacks)


def main():
    vectordb, embeddings = build_vectorstore()
    retriever = vectordb.as_retriever(search_kwargs={"k": 5})
    print(f"Vectorstore pronto: {vectordb._collection.count()} chunks indexados.")
    answer_llm = build_llm()
    eval_llm = build_ragas_llm()

    def answer_question(item):
        tracker, started_at = start_usage_tracker()
        answer, contexts = self_rag_traced(
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
        "self-rag",
        answer_question,
        evaluate_question,
    )
    if counts["run_failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
