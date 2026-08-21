import os
from dotenv import load_dotenv

from langchain_community.document_loaders import DirectoryLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
from datasets import Dataset
from langsmith import traceable

load_dotenv()

os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGCHAIN_TRACING_V2", "false")
os.environ["LANGCHAIN_API_KEY"]     = os.getenv("LANGCHAIN_API_KEY", "")
os.environ["LANGCHAIN_PROJECT"]     = os.getenv("LANGCHAIN_PROJECT", "benchmark-context-rag")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5")
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "medium")

test_queries = [
    "Como o livro Algoritmos: Teoria e Prática, de Cormen, define a notação Θ (Theta) e qual teorema relaciona Θ com as notações O e Ω?",
    "Como Manzano e Oliveira, no livro Algoritmos: Lógica para Desenvolvimento de Programação de Computadores, descrevem o papel do programador de computador e o que é o diagrama de blocos?",
    "Segundo Dilermando Junior e Nakamiti em Algoritmos e Programação de Computadores, qual é a origem do termo \"algoritmo\" e em que consiste o Algoritmo Euclidiano para o cálculo do mdc?",
    "Por que, segundo Sebesta no livro Conceitos de Linguagens de Programação, é importante estudar os conceitos de linguagens de programação mesmo para quem não vai criar uma nova linguagem?",
    "Como Bhargava, no livro Entendendo Algoritmos, define a notação Big O e o que ela estabelece sobre o tempo de execução de um algoritmo?",
    "Segundo Szwarcfiter em Estruturas de Dados e Seus Algoritmos, quais são as complexidades das operações de seleção, inserção, remoção, alteração e construção em um heap?",
    "Como Ascencio, no livro Fundamentos da Programação de Computadores, descreve a plataforma Java, os arquivos gerados na compilação e o papel da Máquina Virtual Java?",
    "Segundo o livro Introdução a Algoritmos e Programação, quais são as três partes que compõem um algoritmo executado em um computador e quais sistemas de representação numérica são utilizados internamente?",
    "Quais são as quatro perguntas que Nilo Menezes, em Introdução à Programação com Python, recomenda que o iniciante responda antes de começar a aprender a programar e qual é, segundo o autor, a maneira mais difícil de aprender?",
    "Quais são os operadores aritméticos não convencionais apresentados por Forbellone em Lógica de Programação e como o autor define o conceito de contador?"
]

ground_truths = [
    "Cormen define que, para uma função g(n), Θ(g(n)) representa o conjunto de funções com limites assintóticos justos: existe um limite superior e inferior do mesmo crescimento. O Teorema 3.1 do livro estabelece que, para quaisquer duas funções f(n) e g(n), tem-se f(n) = Θ(g(n)) se e somente se f(n) = O(g(n)) e f(n) = Ω(g(n)). Em outras palavras, uma função tem ordem Θ exatamente quando possui simultaneamente o mesmo limite assintótico superior (O) e inferior (Ω).",
    "Manzano e Oliveira comparam o programador a um construtor (ou pedreiro especializado), responsável por construir o programa empilhando instruções de uma linguagem como se fossem tijolos, inclusive elaborando a interface gráfica. Além de interpretar o fluxograma desenhado pelo analista, o programador deve detalhar a lógica do programa em nível micro, desenhando uma planta operacional chamada diagrama de blocos (ou diagrama de quadros), seguindo a norma ISO 5807:1985. Essa atividade exige alto grau de atenção e cuidado, pois o descuido pode \"matar\" uma empresa.",
    "Segundo Dilermando Junior e Nakamiti, o termo \"algoritmo\" deriva do nome do matemático persa al-Khwarizmi, considerado por muitos o \"Pai da Álgebra\". No século XII, Adelardo de Bath traduziu uma de suas obras para o latim, registrando o termo como \"Algorithmi\"; originalmente referia-se às regras de aritmética com algarismos indo-arábicos e, posteriormente, passou a designar qualquer procedimento definido para resolver problemas. O Algoritmo Euclidiano, criado por Euclides, calcula o máximo divisor comum (mdc): divide-se a por b, obtendo o resto r; substitui-se a por b e b por r; e repete-se a divisão até que não seja mais possível dividir, sendo o último valor de a o mdc.",
    "Sebesta argumenta que estudar conceitos de linguagens valoriza recursos e construções importantes e estimula o programador a usá-los mesmo quando a linguagem em uso não os suporta diretamente — por exemplo, simulando matrizes associativas de Perl em outra linguagem. Também fornece embasamento para escolher a linguagem mais adequada a cada projeto, evitando que o profissional se restrinja àquela com a qual está mais familiarizado. Por fim, conhecer uma gama mais ampla de linguagens torna o aprendizado de novas linguagens mais fácil, ampliando a capacidade de avaliar trade-offs de projeto.",
    "Bhargava define a notação Big O como uma forma de medir o tempo de execução de um algoritmo no pior caso (pior hipótese), descrevendo o quão rapidamente esse tempo cresce em relação ao tamanho n da entrada. Por exemplo, a pesquisa simples tem tempo O(n) — no pior caso verifica todos os elementos da lista — enquanto a pesquisa binária tem tempo O(log n). Algoritmos com tempos diferentes crescem a taxas muito distintas, e o Big O permite compará-los independentemente do hardware utilizado.",
    "Segundo Szwarcfiter, em um heap o elemento de maior prioridade é sempre a raiz da árvore, e as operações têm os seguintes parâmetros de eficiência: seleção em O(1), pois basta retornar a raiz; inserção em O(log n); remoção em O(log n); alteração em O(log n); e construção em O(n), tempo este inferior ao de uma ordenação. Esses tempos tornam o heap especialmente adequado para implementar listas de prioridades.",
    "Ascencio explica que a tecnologia Java é composta pela linguagem de programação Java e pela plataforma de desenvolvimento Java, com características de simplicidade, orientação a objetos, portabilidade, alta performance e segurança. Os programas são escritos em arquivos de texto com extensão .java e, ao serem compilados pelo compilador javac, geram arquivos .class compostos por bytecodes — código interpretado pela Máquina Virtual Java (JVM). A plataforma Java é composta apenas por software, pois é a JVM que faz a interface entre os programas e o sistema operacional.",
    "O livro descreve que um algoritmo, quando programado em um computador, é constituído por pelo menos três partes: entrada de dados, processamento de dados e saída de dados. Internamente, os computadores digitais utilizam o sistema binário (base 2), com apenas dois algarismos (0 e 1), aproveitando a noção de ligado/desligado ou verdadeiro/falso. Como representações auxiliares, são também utilizados o sistema decimal (base 10), o sistema hexadecimal (base 16, com dígitos 0–9 e A–F) e o sistema octal (base 8).",
    "Menezes propõe que o iniciante responda a quatro perguntas antes de começar: (1) Você quer aprender a programar?; (2) Como está seu nível de paciência?; (3) Quanto tempo você pretende estudar?; (4) Qual o seu objetivo ao programar? Para o autor, a maneira mais difícil de aprender a programar é não querer programar — a vontade deve vir do próprio aluno e não de um professor ou amigo. Programar é uma arte que exige tempo, dedicação e paciência para que a mente se acostume com a nova forma de pensar.",
    "Forbellone apresenta operadores aritméticos não convencionais úteis na construção de algoritmos: pot(x,y) para potenciação (x elevado a y), rad(x) para radiciação (raiz quadrada de x), mod para o resto da divisão (ex.: 9 mod 4 = 1) e div para o quociente da divisão inteira (ex.: 9 div 4 = 2). Um contador é uma variável usada para registrar quantas vezes um trecho de algoritmo é executado: é declarada com um valor inicial e incrementada (somada de uma constante, normalmente 1) a cada repetição, comportando-se como o ponteiro dos segundos de um relógio."
]


def get_openai_api_key():
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY nao encontrada. Crie um arquivo .env a partir do "
            ".env.example e informe sua chave da OpenAI."
        )

    return OPENAI_API_KEY


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


def build_vectorstore():
    loader = DirectoryLoader("../docs/", glob="**/*.pdf", loader_cls=PyPDFLoader)
    docs = loader.load()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=100
    )
    chunks = splitter.split_documents(docs)

    embeddings = OpenAIEmbeddings(
        model=OPENAI_EMBEDDING_MODEL,
        api_key=get_openai_api_key(),
    )

    vectordb = Chroma.from_documents(chunks, embedding=embeddings)
    return vectordb, embeddings


def build_llm():
    return ChatOpenAI(
        api_key=get_openai_api_key(),
        model=OPENAI_MODEL,
        reasoning_effort=OPENAI_REASONING_EFFORT,
        temperature=None,
        use_responses_api=True,
    )


def context_rag(query, retriever, llm, top_k=5):
    docs = retriever.invoke(query)
    selected_docs = docs[:top_k]
    contexts = [d.page_content for d in selected_docs]
    context_text = "\n\n".join(contexts)

    prompt = f"""
    Você deve responder usando SOMENTE o contexto fornecido.

    Contexto:
    {context_text}

    Pergunta:
    {query}

    Se a resposta não estiver no contexto, diga:
    "A informação não está presente no contexto."
    """

    response = llm.invoke(prompt)
    return extract_response_text(response), contexts


@traceable(name="context-rag-query", run_type="chain")
def context_rag_traced(query, retriever, llm, top_k=5):
    return context_rag(query, retriever, llm, top_k)


def run_ragas(ragas_data, llm, embeddings):
    dataset = Dataset.from_list(ragas_data)

    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=llm,
        embeddings=embeddings
    )

    print("\n=== RESULTADOS RAGAS ===")
    print(result)

    df = result.to_pandas()
    print("\nDetalhes por query:")
    print(df.to_string())

    return result


def main():
    print("\nIndexando PDFs de ../docs/ ...\n")
    vectordb, embeddings = build_vectorstore()
    retriever = vectordb.as_retriever()

    print("Coletando respostas para avaliacao RAGAS...\n")
    ragas_data = []
    answer_llm = build_llm()
    eval_llm = build_llm()

    for i, query in enumerate(test_queries):
        print(f"  [{i+1}/{len(test_queries)}] {query}")
        answer, contexts = context_rag_traced(query, retriever, answer_llm)
        ragas_data.append({
            "question": query,
            "answer": answer,
            "contexts": contexts,
            "ground_truth": ground_truths[i]
        })

    run_ragas(ragas_data, eval_llm, embeddings)


if __name__ == "__main__":
    main()
