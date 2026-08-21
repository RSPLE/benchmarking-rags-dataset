# Self-RAG

> **Atualização do monorepo:** este pipeline agora lê as 90 perguntas de `../eval-dataset/qa_dataset_90.json`, executa uma pergunta por vez e retoma apenas falhas por meio de `results/checkpoint.json`. Use `uv run python ../main.py run self-rag` a partir da raiz; as referências abaixo a 5 rodadas e 10 perguntas descrevem a versão histórica.
> Dependências vêm de `pyproject.toml`/`uv.lock` deste diretório e toda configuração vem exclusivamente de `../.env`; ignore as instruções históricas de `requirements.txt` e `.env` local abaixo.

Sistema de perguntas e respostas sobre documentos PDF (apostilas de lógica de programação e algoritmos) usando Retrieval-Augmented Generation com uma etapa de **auto-crítica** sobre a própria resposta, e avaliação automática de qualidade via RAGAS.

## Contextualização

RAG (Retrieval-Augmented Generation) é o padrão de recuperar trechos relevantes de uma base documental e injetá-los no prompt de um LLM para fundamentar a resposta. "Self-RAG" (Asai et al.) é o nome de uma família de técnicas em que o próprio modelo participa da decisão de quando recuperar, do julgamento da relevância dos documentos e da crítica da resposta gerada, tipicamente usando tokens de reflexão treinados especificamente para isso.

Este projeto implementa uma **versão simplificada** desse conceito, baseada em auto-crítica pós-geração via prompting (sem tokens de reflexão treinados, sem grading de documentos individuais e sem decisão adaptativa de "recuperar ou não" — o retrieval é sempre executado). O fluxo é:

1. Recupera contexto (top-5 chunks por similaridade vetorial).
2. Gera uma resposta com base nesse contexto.
3. Pergunta ao próprio LLM, em uma segunda chamada, se a resposta está fundamentada no contexto (resposta binária SIM/NAO).
4. Se a crítica for "NAO", refaz a resposta **uma única vez** com um prompt de refinamento.

## Arquitetura do pipeline

```
docs/*.pdf
    │  PyPDFLoader + DirectoryLoader
    ▼
Documentos (1 por página)
    │  RecursiveCharacterTextSplitter (chunk_size=800, overlap=100)
    ▼
Chunks
    │  OpenAIEmbeddings (text-embedding-3-large)
    ▼
Chroma (persistente, batches de 500)
    │  as_retriever(k=5)
    ▼
Contexto recuperado
    │
    ▼
① Prompt de geração ──► ChatOpenAI ──► Resposta
    │
    ▼
② Prompt de auto-crítica (SIM/NAO) ──► ChatOpenAI
    │
    ├── SIM ──► resposta final = resposta do passo ①
    │
    └── NAO ──► ③ Prompt de refinamento ──► ChatOpenAI ──► resposta final
                                                                │
                                                                ▼
                                                    Avaliação RAGAS (5 rodadas)
                                                                │
                                                                ▼
                                                results/self-rag-run-N_i.csv
```

| Etapa | Função / arquivo |
|---|---|
| Configuração de ambiente | `configure_environment()` — `rag_settings.py:36-45` |
| Ingestão + chunking + indexação | `build_vectorstore()` — `main.py:68-100` |
| Embeddings | `build_embeddings()` — `rag_settings.py:67-71` |
| Retrieval | `vectordb.as_retriever(search_kwargs={"k": 5})` — `main.py:154` |
| Geração + auto-crítica + refino | `self_rag()` — `main.py:103-144` |
| Rastreamento (LangSmith) | `@traceable` em `self_rag_traced()` — `main.py:147-149` |
| Avaliação | `run_ragas()` — `rag_settings.py:277-298` |
| Persistência dos resultados | `salvar()` — `rag_settings.py:301-336` |
| Orquestração / loop principal | `main()` — `main.py:152-185` |

## Detalhes técnicos

### Prompts

Todos definidos dentro de `self_rag()` (`main.py:103-144`).

**1. Geração** (`main.py:109-117`):
```
Contexto:
{context}

Pergunta:
{query}

Responda usando apenas o contexto.
```

**2. Auto-crítica** (`main.py:122-128`):
```
Pergunta: {query}
Resposta: {response}

A resposta está fundamentada no contexto?
Responda apenas SIM ou NAO.
```

A decisão de refazer a resposta é feita por checagem literal de string: `if "NAO" in critique.upper():` (`main.py:132`).

**3. Refinamento** (usado só se a crítica retornar "NAO", `main.py:133-141`):
```
Refaça a resposta usando melhor o contexto.

Contexto:
{context}

Pergunta:
{query}
```

Não há prompt de grading de documentos individuais nem de decisão prévia de "recuperar ou não" — o retrieval é sempre executado incondicionalmente antes da geração.

### Chunking

- Biblioteca: `langchain_text_splitters.RecursiveCharacterTextSplitter`.
- `chunk_size=800`, `chunk_overlap=100` (`main.py:81-84`).
- Carregamento via `DirectoryLoader(DOCS_DIR, glob="**/*.pdf", loader_cls=PyPDFLoader)`, `DOCS_DIR` configurável (default `./docs/`).
- Inserção no Chroma em batches de 500 chunks.

### Embeddings

```python
def build_embeddings():
    return OpenAIEmbeddings(
        model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large"),
        api_key=get_openai_api_key(),
    )
```

Modelo padrão `text-embedding-3-large` (OpenAI), dimensão nativa 3072 não fixada explicitamente no código.

### Banco vetorial

- **ChromaDB**, persistido em disco: `persist_directory=CHROMA_PERSIST_DIR` (default `./chroma_self_db_openai`), `collection_name=CHROMA_COLLECTION_NAME` (default `self_rag_contexts_openai`).
- Ingestão idempotente (`vectordb._collection.count() == 0` decide se popula ou pula).

Este projeto não usa nenhum grafo de conhecimento.

### Parâmetros de recuperação

- `retriever = vectordb.as_retriever(search_kwargs={"k": 5})` — top-5 por similaridade vetorial padrão do Chroma.
- Critério de re-tentativa: string matching simples (`"NAO" in critique.upper()`), sem threshold numérico.
- Número máximo de refinamentos: **1** (um único `if`, não é um loop iterativo até convergência).
- Sem MMR, sem reranking, sem filtros de metadata.

### Versões das bibliotecas

`requirements.txt` não fixa versões exatas (só um mínimo):

| Biblioteca | Versão |
|---|---|
| langchain | não pinada |
| langchain-community | não pinada |
| langchain-openai | `>=1.1.11` |
| langchain-text-splitters | não pinada |
| openai | não pinada |
| chromadb | não pinada |
| pypdf | não pinada |
| datasets | não pinada |
| python-dotenv | não pinada |
| ragas | não pinada |
| langsmith | não pinada |

Não há `pyproject.toml` nem lockfile.

## Requisitos

- Python 3.10+
- Conta OpenAI com acesso à API
- Conta LangSmith, para rastreamento (tracing) do fluxo e cálculo de uso de tokens

## Replicabilidade / Instalação

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
```

## Configuração

Crie um `.env` a partir de `.env.example`:

```env
OPENAI_API_KEY=sk-sua_chave_openai
OPENAI_MODEL=gpt-5.5
OPENAI_EMBEDDING_MODEL=text-embedding-3-large
OPENAI_REASONING_EFFORT=medium
DOCS_DIR=./docs/
CHROMA_PERSIST_DIR=./chroma_self_db_openai
CHROMA_COLLECTION_NAME=self_rag_contexts_openai
LANGCHAIN_TRACING_V2=false
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=benchmark-self-rag
```

| Variável | Default | Descrição |
|---|---|---|
| `OPENAI_API_KEY` | — (obrigatória) | Chave da API OpenAI. |
| `OPENAI_MODEL` | `gpt-5.5` | Modelo usado para geração, auto-crítica, refinamento e avaliação RAGAS. Reportado como está no código/`.env.example`. |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-large` | Modelo de embeddings. |
| `OPENAI_REASONING_EFFORT` | `medium` | Parâmetro `reasoning_effort` do `ChatOpenAI` (Responses API, `use_responses_api=True`). |
| `DOCS_DIR` | `./docs/` | Pasta com os PDFs a indexar. |
| `CHROMA_PERSIST_DIR` | `./chroma_self_db_openai` | Diretório de persistência do índice vetorial. |
| `CHROMA_COLLECTION_NAME` | `self_rag_contexts_openai` | Nome da coleção no Chroma. |
| `LANGCHAIN_TRACING_V2` | `false` | Ativa tracing no LangSmith. |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` | Endpoint do LangSmith. |
| `LANGCHAIN_API_KEY` | — | Chave do LangSmith. |
| `LANGCHAIN_PROJECT` | `benchmark-self-rag` | Nome do projeto no LangSmith. |

## Uso

Coloque os PDFs em `docs/` (já populada com 10 apostilas/livros sobre algoritmos, estruturas de dados e lógica de programação) e execute:

```bash
python main.py
```

O script:
1. Indexa os PDFs de `docs/` no Chroma (pula se a coleção já existir).
2. Roda **5 rodadas** das mesmas **10 perguntas de benchmark** fixas no código (`test_queries`/`ground_truths` em `main.py`).
3. Para cada pergunta, recupera os 5 chunks mais relevantes, gera a resposta, aplica auto-crítica e, se necessário, refina a resposta uma vez.
4. Avalia cada rodada com RAGAS e salva um CSV por rodada em `results/` (ou `results_2/`, `results_3/`... se a pasta já existir).

## Estrutura do projeto

```
self-rag/
├── .env.example
├── README.md
├── requirements.txt
├── main.py              # pipeline Self-RAG + benchmark RAGAS (versão oficial)
├── rag_settings.py       # utilitários compartilhados: env, LLM/embeddings, tracking de uso, RAGAS, salvar CSV
├── main.ipynb            # variante histórica (ver Notas)
├── benchmark.ipynb       # ferramenta de análise pós-hoc via LangSmith (ver Notas)
└── docs/                 # 10 PDFs (livros de algoritmos/estruturas de dados/lógica de programação em PT-BR)
```

Gerados em runtime (fora do controle de versão): `chroma_self_db_openai/` (índice vetorial) e `results*/` (CSVs).

## Avaliação e resultados

Métricas RAGAS calculadas a cada rodada: `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`. Cada linha do CSV também traz `answer_response_time_seconds`, `answer_input_tokens`, `answer_output_tokens` e `answer_total_tokens`, medidos por pergunta via `TokenUsageTracker`.

## Notas e limitações

- O retrieval é sempre executado (não há decisão adaptativa de "recuperar ou não"), e não há grading individual de documentos recuperados — a auto-crítica avalia apenas a resposta final, não os chunks.
- O refinamento é limitado a uma única tentativa; não há loop iterativo até a crítica retornar "SIM".
- `main.ipynb` é uma variante histórica que usa `HuggingFaceEmbeddings (sentence-transformers/all-MiniLM-L6-v2)` e um LLM via endpoint compatível com OpenAI hospedado na DigitalOcean, em vez da stack OpenAI usada em `main.py` — não é equivalente ao pipeline oficial e roda uma única vez (sem 5 rodadas, sem tracking de tokens, sem exportação em CSV).
- `benchmark.ipynb` não faz parte do pipeline Self-RAG em si: é uma ferramenta separada que consulta a API do LangSmith para comparar tokens médios e latência de execuções já rastreadas (requer `LANGCHAIN_TRACING_V2=true` em execuções anteriores) e salva um gráfico comparativo (`rag_benchmark_chart.png`).
- Dependências em `requirements.txt` não são pinadas (exceto o mínimo de `langchain-openai`).
