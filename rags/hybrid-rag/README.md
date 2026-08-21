# Hybrid RAG

> **Atualização do monorepo:** este pipeline agora lê as 90 perguntas de `../eval-dataset/qa_dataset_90.json`, executa uma pergunta por vez e retoma apenas falhas por meio de `results/checkpoint.json`. Use `uv run python ../main.py run hybrid-rag` a partir da raiz; as referências abaixo a 5 rodadas e 10 perguntas descrevem a versão histórica.
> Dependências vêm de `pyproject.toml`/`uv.lock` deste diretório e toda configuração vem exclusivamente de `../.env`; ignore as instruções históricas de `requirements.txt` e `.env` local abaixo.
> Dependências vêm de `pyproject.toml`/`uv.lock` deste diretório e toda configuração vem exclusivamente de `../.env`; ignore as instruções históricas de `requirements.txt` e `.env` local abaixo.

> **Atualização do monorepo:** este pipeline agora lê as 90 perguntas de `../eval-dataset/qa_dataset_90.json`, executa uma pergunta por vez e retoma apenas falhas por meio de `results/checkpoint.json`. Use `uv run python ../main.py run hybrid-rag` a partir da raiz; as referências abaixo a 5 rodadas e 10 perguntas descrevem a versão histórica.

Sistema de perguntas e respostas sobre documentos PDF usando Retrieval-Augmented Generation **híbrido**, combinando busca lexical (BM25) e busca semântica (vetorial), com avaliação automática de qualidade via RAGAS.

## Contextualização

RAG (Retrieval-Augmented Generation) é o padrão de recuperar trechos relevantes de uma base documental e injetá-los no prompt de um LLM para fundamentar a resposta. A busca puramente vetorial (por embeddings) é boa para similaridade semântica, mas pode perder correspondências exatas de palavras-chave, siglas ou termos técnicos raros — é aí que entra a busca lexical (BM25), que pontua documentos por sobreposição de termos.

Este projeto implementa **RAG híbrido**: dois retrievers independentes (um lexical via BM25, um vetorial via Chroma) são executados em paralelo sobre a mesma pergunta, e seus rankings são combinados por um `EnsembleRetriever` do LangChain, que faz a fusão dos resultados com pesos fixos (40% para o retriever BM25, 60% para o vetorial). Não há um algoritmo de Reciprocal Rank Fusion escrito manualmente neste repositório — a fusão é inteiramente delegada à implementação do `EnsembleRetriever` (pacote `langchain-classic`); apenas os pesos de cada retriever são configurados explicitamente no código. Não há reranking (nenhum cross-encoder) após a fusão.

## Arquitetura do pipeline

```
docs/*.pdf
    │  PyPDFLoader + DirectoryLoader
    ▼
Documentos (1 por página)
    │  RecursiveCharacterTextSplitter (chunk_size=800, overlap=100, add_start_index=True)
    ▼
Chunks ──────────────────┬──────────────────────────────┐
                          │                              │
                          ▼                              ▼
              OpenAIEmbeddings (text-embedding-3-large)  BM25Retriever.from_documents(k=RETRIEVER_K)
                          │                              │  (índice lexical em memória, não persistido)
                          ▼                              │
              Chroma (persistente, batches de 500)       │
                          │                               │
              as_retriever(k=RETRIEVER_K)                 │
                          │                               │
                          └──────────────┬────────────────┘
                                         ▼
                     EnsembleRetriever(weights=[0.4 BM25, 0.6 vetorial])
                                         │
                                         ▼
                     Prompt de geração ──► ChatOpenAI (gpt-5.5)
                                         │
                                         ▼
                                    Resposta
                                         │
                                         ▼
                             Avaliação RAGAS (5 rodadas)
                                         │
                                         ▼
                        results/hybrid-rag-run-N_i.csv
```

| Etapa | Função / arquivo |
|---|---|
| Configuração de ambiente | `configure_environment()` — `rag_settings.py:61-70` |
| Ingestão + chunking + indexação vetorial | `build_hybrid_retriever()` — `main.py:75-119` |
| Índice lexical (BM25) | `BM25Retriever.from_documents(...)` — `main.py:112` |
| Fusão híbrida | `EnsembleRetriever(...)` — `main.py:114-117` |
| Geração | `hybrid_rag()` — `main.py:122-142` |
| Rastreamento (LangSmith) | `@traceable` em `hybrid_rag()` — `main.py:122` |
| Avaliação | `run_ragas()` — `rag_settings.py:309-331` |
| Persistência dos resultados | `salvar()` — `rag_settings.py:334-369` |
| Orquestração / loop principal | `main()` — `main.py:145-177` |

## Detalhes técnicos

### Prompt de geração

`main.py:128-137` (dentro de `hybrid_rag()`):

```
Você é um assistente útil. Use o contexto abaixo para responder a pergunta.
Se não souber a resposta com base no contexto, diga que não sabe.

Contexto:
{context}

Pergunta:
{query}

Resposta:
```

### Chunking

- Biblioteca: `langchain_text_splitters.RecursiveCharacterTextSplitter`.
- `chunk_size=800`, `chunk_overlap=100`, `add_start_index=True` (`main.py:81-85`).
- Carregamento via `DirectoryLoader(DOCS_DIR, glob="**/*.pdf", loader_cls=PyPDFLoader)`.
- Os mesmos chunks alimentam tanto o índice vetorial (Chroma) quanto o índice lexical (BM25).

### Embeddings

```python
def build_embeddings():
    return OpenAIEmbeddings(
        model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large"),
        api_key=get_openai_api_key(),
    )
```

Modelo padrão `text-embedding-3-large` (OpenAI), dimensão nativa 3072 não fixada explicitamente no código. Usado apenas pelo retriever vetorial — o retriever BM25 não usa embeddings.

### Banco vetorial e índice lexical

- **Vetorial**: ChromaDB, persistido em disco (`persist_directory=CHROMA_PERSIST_DIR`, default `./chroma_hybrid_db_openai`; `collection_name=CHROMA_COLLECTION_NAME`, default `hybrid_collection_openai`). Ingestão em batches de 500, condicional a `vector_store._collection.count() == 0`.
- **Lexical**: `BM25Retriever` (`langchain_community.retrievers`, sobre a biblioteca `rank_bm25`), construído **em memória** a cada execução via `BM25Retriever.from_documents(all_splits, k=RETRIEVER_K)` — não é persistido em disco, é reconstruído do zero a partir dos chunks toda vez que o script roda.

Este projeto não usa nenhum grafo de conhecimento.

### Parâmetros de recuperação

- `RETRIEVER_K` (env, default `3`): usado como `k` tanto no retriever BM25 quanto no retriever vetorial.
- Fusão: `EnsembleRetriever(retrievers=[bm25_retriever, vector_retriever], weights=[0.4, 0.6])` — peso 0.4 para BM25, 0.6 para o vetorial.
- Sem threshold de similaridade, sem reranking, sem filtros de metadata.
- `RAGAS_TIMEOUT_SECONDS` (default `600`) e `RAGAS_MAX_WORKERS` (default `4`) configuram um `RunConfig` do RAGAS usado na avaliação (`build_ragas_run_config()`, `rag_settings.py:302-306`) — parâmetros únicos deste projeto entre os utilitários compartilhados.

### Versões das bibliotecas

`requirements.txt` não fixa versões exatas (só um mínimo):

| Biblioteca | Versão |
|---|---|
| langchain | não pinada |
| langchain-community | não pinada |
| langchain-classic | não pinada (fornece `EnsembleRetriever`) |
| langchain-openai | `>=1.1.11` |
| langchain-text-splitters | não pinada |
| openai | não pinada |
| chromadb | não pinada |
| rank_bm25 | não pinada |
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

RAGAS_TIMEOUT_SECONDS=600
RAGAS_MAX_WORKERS=4

DOCS_DIR=../docs/

RETRIEVER_K=3

CHROMA_PERSIST_DIR=./chroma_hybrid_db_openai
CHROMA_COLLECTION_NAME=hybrid_collection_openai

LANGCHAIN_TRACING_V2=false
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=benchmark-hybrid-rag
```

> **Atenção ao `DOCS_DIR`**: o valor padrão no `.env.example` é `../docs/` (uma pasta **fora** deste projeto). Os PDFs deste repositório estão em `docs/`, dentro da própria pasta do projeto. Ajuste `DOCS_DIR=./docs/` no seu `.env` antes de rodar, ou a ingestão não encontrará nenhum PDF.

| Variável | Default | Descrição |
|---|---|---|
| `OPENAI_API_KEY` | — (obrigatória) | Chave da API OpenAI. |
| `OPENAI_MODEL` | `gpt-5.5` | Modelo usado para geração e avaliação RAGAS. Reportado como está no código/`.env.example`. |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-large` | Modelo de embeddings (retriever vetorial). |
| `OPENAI_REASONING_EFFORT` | `medium` | Parâmetro `reasoning_effort` do `ChatOpenAI` (Responses API). |
| `RAGAS_TIMEOUT_SECONDS` | `600` | Timeout do `RunConfig` da avaliação RAGAS. |
| `RAGAS_MAX_WORKERS` | `4` | Paralelismo do `RunConfig` da avaliação RAGAS. |
| `DOCS_DIR` | `../docs/` (ver aviso acima) | Pasta com os PDFs a indexar. |
| `RETRIEVER_K` | `3` | `k` usado por ambos retrievers (BM25 e vetorial) antes da fusão. |
| `CHROMA_PERSIST_DIR` | `./chroma_hybrid_db_openai` | Diretório de persistência do índice vetorial. |
| `CHROMA_COLLECTION_NAME` | `hybrid_collection_openai` | Nome da coleção no Chroma. |
| `LANGCHAIN_TRACING_V2` | `false` | Ativa tracing no LangSmith. |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` | Endpoint do LangSmith. |
| `LANGCHAIN_API_KEY` | — | Chave do LangSmith. |
| `LANGCHAIN_PROJECT` | `benchmark-hybrid-rag` | Nome do projeto no LangSmith. |

## Uso

Coloque os PDFs em `docs/` (por padrão contém 1 PDF de exemplo) e execute:

```bash
python main.py
```

O script:
1. Carrega e faz o chunking dos PDFs de `DOCS_DIR`.
2. Indexa os chunks no Chroma (pula se a coleção já existir) e constrói o índice BM25 em memória.
3. Roda **5 rodadas** das mesmas **10 perguntas de benchmark** fixas no código (`test_queries`/`ground_truths` em `main.py`).
4. Para cada pergunta, recupera o contexto combinado (BM25 + vetorial via `EnsembleRetriever`) e gera a resposta.
5. Avalia cada rodada com RAGAS e salva um CSV por rodada em `results/` (ou `results_2/`, `results_3/`... se a pasta já existir).

## Estrutura do projeto

```
hybrid-rag/
├── .env.example
├── README.md
├── requirements.txt
├── main.py              # pipeline RAG híbrido (BM25 + vetorial) + benchmark RAGAS
├── rag_settings.py       # utilitários compartilhados: env, LLM/embeddings, tracking de uso, RAGAS, salvar CSV
├── main.ipynb            # variante histórica (ver Notas)
└── docs/                 # PDFs usados como base de conhecimento
```

Gerados em runtime (fora do controle de versão): `chroma_hybrid_db_openai/` (índice vetorial) e `results*/` (CSVs).

## Avaliação e resultados

Métricas RAGAS calculadas a cada rodada: `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`. Cada linha do CSV também traz `answer_response_time_seconds`, `answer_input_tokens`, `answer_output_tokens` e `answer_total_tokens`, medidos por pergunta via `TokenUsageTracker`.

## Notas e limitações

- Não há reranking pós-fusão (nenhum cross-encoder ou modelo de re-rank).
- O índice BM25 é reconstruído em memória a cada execução do script — não é persistido em disco (diferente do índice vetorial, que é persistido no Chroma).
- `main.ipynb` é uma variante histórica com parâmetros e stack diferentes do `main.py` atual: chunking `chunk_size=500, chunk_overlap=120` (em vez de 800/100), `k=5` hardcoded para ambos retrievers (em vez de `RETRIEVER_K` configurável), embeddings `HuggingFaceEmbeddings (sentence-transformers/all-mpnet-base-v2)` em vez de OpenAI, e um LLM via endpoint compatível com OpenAI hospedado na DigitalOcean (`llama3.3-70b-instruct`) em vez de `gpt-5.5` via API OpenAI direta. Roda uma única vez, sem tracking de tokens nem exportação em CSV — não é equivalente ao pipeline oficial.
- Dependências em `requirements.txt` não são pinadas (exceto o mínimo de `langchain-openai`).
