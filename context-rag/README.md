# Context RAG

> **Atualização do monorepo:** este pipeline agora lê as 90 perguntas de `../eval-dataset/qa_dataset_90.json`, executa uma pergunta por vez e retoma apenas falhas por meio de `results/checkpoint.json`. Use `uv run python ../main.py run context-rag` a partir da raiz; as referências abaixo a 5 rodadas e 10 perguntas descrevem a versão histórica.
> Dependências vêm de `pyproject.toml`/`uv.lock` deste diretório e toda configuração vem exclusivamente de `../.env`; ignore as instruções históricas de `requirements.txt` e `.env` local abaixo.
> Dependências vêm de `pyproject.toml`/`uv.lock` deste diretório e toda configuração vem exclusivamente de `../.env`; ignore as instruções históricas de `requirements.txt` e `.env` local abaixo.

> **Atualização do monorepo:** este pipeline agora lê as 90 perguntas de `../eval-dataset/qa_dataset_90.json`, executa uma pergunta por vez e retoma apenas falhas por meio de `results/checkpoint.json`. Use `uv run python ../main.py run context-rag` a partir da raiz; as referências abaixo a 5 rodadas e 10 perguntas descrevem a versão histórica.

Sistema de perguntas e respostas sobre documentos PDF (apostilas de lógica de programação e algoritmos) usando Retrieval-Augmented Generation (RAG), com avaliação automática de qualidade via RAGAS.

## Contextualização

RAG (Retrieval-Augmented Generation) é o padrão de recuperar trechos relevantes de uma base documental e injetá-los no prompt de um LLM, para que a resposta seja fundamentada nesses trechos em vez de depender só do conhecimento paramétrico do modelo. Este projeto implementa a variante mais direta do padrão — **retrieve-then-read** clássico, sem reranking, sem HyDE, sem reescrita de query e sem nenhuma etapa de auto-avaliação: os `k` chunks mais similares à pergunta são recuperados por busca vetorial e concatenados no prompt, com uma instrução explícita para o modelo responder **somente** com base nesse contexto.

O nome "Context RAG" refere-se a essa restrição do prompt ("responda usando SOMENTE o contexto fornecido"), não a nenhuma técnica de "Contextual Retrieval" (como a técnica da Anthropic de gerar um resumo contextual por chunk antes de indexar) — não há geração de contexto sintético por chunk neste projeto.

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
Contexto recuperado ──► Prompt "responda SOMENTE com o contexto" ──► ChatOpenAI (gpt-5.5)
                                                                          │
                                                                          ▼
                                                                     Resposta
                                                                          │
                                                                          ▼
                                                              Avaliação RAGAS (5 rodadas)
                                                                          │
                                                                          ▼
                                                        results/context-rag-run-N_i.csv
```

| Etapa | Função / arquivo |
|---|---|
| Ingestão | `build_vectorstore()` — `main.py:265-307` |
| Chunking | dentro de `build_vectorstore()` — `main.py:286-291` |
| Embedding | `OpenAIEmbeddings(...)` — `main.py:266-269` |
| Indexação vetorial | `Chroma(...)` — `main.py:271-306` |
| Retrieval | `vectordb.as_retriever(search_kwargs={"k": 5})` — `main.py:472` |
| Geração | `context_rag()` — `main.py:328-350` |
| Rastreamento (LangSmith) | `@traceable` em `context_rag_traced()` — `main.py:353-355` |
| Avaliação | `run_ragas()` — `main.py:407-428` |
| Persistência dos resultados | `salvar()` — `main.py:431-466` |
| Orquestração / loop principal | `main()` — `main.py:469-483` |
| Visualização das métricas | `plot_graph.py` (script independente) |

## Detalhes técnicos

### Prompt de geração

Único prompt do sistema, montado em `context_rag()` (`main.py:335-346`):

```
Você deve responder usando SOMENTE o contexto fornecido.

Contexto:
{context_text}

Pergunta:
{query}

Se a resposta não estiver no contexto, diga:
"A informação não está presente no contexto."
```

Não há prompt de sistema separado, nem reescrita de query, nem sumarização/contextualização por chunk.

### Chunking

- Biblioteca: `langchain_text_splitters.RecursiveCharacterTextSplitter`.
- `chunk_size=800`, `chunk_overlap=100` (divisão por caracteres, não por tokens).
- Carregamento via `DirectoryLoader("./docs/", glob="**/*.pdf", loader_cls=PyPDFLoader)` — um `Document` por página de PDF antes do split.
- Inserção no Chroma em batches de 500 chunks, com log de progresso.

### Embeddings

```python
embeddings = OpenAIEmbeddings(
    model=OPENAI_EMBEDDING_MODEL,  # default: text-embedding-3-large
    api_key=get_openai_api_key()
)
```

Modelo padrão `text-embedding-3-large` (OpenAI). A dimensão do vetor (3072, padrão público do modelo) não é configurada explicitamente no código — não há parâmetro `dimensions` fixado.

### Banco vetorial

- **ChromaDB** (`langchain_community.vectorstores.Chroma`), persistido em disco.
- `persist_directory` = `CHROMA_PERSIST_DIR` (default `./chroma_context_db_openai`).
- `collection_name` = `CHROMA_COLLECTION_NAME` (default `context_collection_openai`).
- Ingestão idempotente: só popula a coleção se `vectordb._collection.count() == 0`; caso já exista, pula a indexação.
- Ao trocar de modelo de embeddings, é necessário usar um `CHROMA_PERSIST_DIR` novo (ou apagar o antigo) — coleções com dimensões diferentes não podem ser misturadas.

Este projeto não usa nenhum grafo de conhecimento — `plot_graph.py` apenas plota um gráfico de barras (matplotlib) com as médias das métricas RAGAS, não tem relação com GraphRAG.

### Parâmetros de recuperação

- `retriever = vectordb.as_retriever(search_kwargs={"k": 5})` — top-5 por similaridade vetorial padrão do Chroma.
- Sem MMR, sem reranking, sem filtros de metadata, sem threshold de score, sem busca híbrida.

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

Não há `pyproject.toml`, `poetry.lock` nem lockfile — `pip install -r requirements.txt` sempre traz as versões mais recentes disponíveis no momento da instalação.

## Requisitos

- Python 3.10+
- Conta OpenAI com acesso à API (chave de API)
- Conta LangSmith, para rastreamento (tracing) do fluxo e cálculo de uso de tokens

## Replicabilidade / Instalação

```bash
python -m venv .venv
```

Ativar no Windows:
```bash
.venv\Scripts\activate
```

Ativar no Git Bash:
```bash
source .venv/Scripts/activate
```

Instalar dependências:
```bash
pip install -r requirements.txt
```

## Configuração

Crie um `.env` a partir de `.env.example`:

```env
OPENAI_API_KEY=sk-sua_chave_openai
OPENAI_MODEL=gpt-5.5
OPENAI_EMBEDDING_MODEL=text-embedding-3-large
OPENAI_REASONING_EFFORT=medium
CHROMA_PERSIST_DIR=./chroma_context_db_openai
CHROMA_COLLECTION_NAME=context_collection_openai
LANGCHAIN_TRACING_V2=false
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=benchmark-context-rag
```

| Variável | Default | Descrição |
|---|---|---|
| `OPENAI_API_KEY` | — (obrigatória) | Chave da API OpenAI; sem ela o script lança `RuntimeError`. |
| `OPENAI_MODEL` | `gpt-5.5` | Modelo usado tanto para geração quanto para avaliação RAGAS. Reportado aqui exatamente como está no código/`.env.example`. |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-large` | Modelo de embeddings. |
| `OPENAI_REASONING_EFFORT` | `medium` | Passado como `reasoning_effort` ao `ChatOpenAI` (usa a Responses API, `use_responses_api=True`). |
| `CHROMA_PERSIST_DIR` | `./chroma_context_db_openai` | Diretório de persistência do índice vetorial. |
| `CHROMA_COLLECTION_NAME` | `context_collection_openai` | Nome da coleção no Chroma. |
| `LANGCHAIN_TRACING_V2` | `false` | Ativa tracing no LangSmith. |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` | Endpoint do LangSmith. |
| `LANGCHAIN_API_KEY` | — | Chave do LangSmith. |
| `LANGCHAIN_PROJECT` | `benchmark-context-rag` | Nome do projeto no LangSmith. |

## Uso

Coloque os PDFs em `docs/` (já populada com 7 apostilas/livros sobre algoritmos e lógica de programação) e execute:

```bash
python main.py
```

O script:
1. Indexa os PDFs de `docs/` no Chroma (pula se a coleção já existir).
2. Roda **5 rodadas** das mesmas **10 perguntas de benchmark** fixas no código (`test_queries`/`ground_truths` em `main.py`).
3. Para cada pergunta, recupera os 5 chunks mais relevantes e gera a resposta usando somente esse contexto.
4. Avalia cada rodada com RAGAS e salva um CSV por rodada em `results/` (ou `results_2/`, `results_3/`... se a pasta já existir), com as colunas `question`, `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`, `answer_response_time_seconds`, `answer_input_tokens`, `answer_output_tokens`, `answer_total_tokens`.

Depois de gerar resultados, para visualizar as médias das métricas em um gráfico de barras:

```bash
python plot_graph.py
```

Isso agrega todos os `results/context-rag-run-*.csv` e salva `results/mean_metrics.png`.

## Estrutura do projeto

```
context-rag/
├── .env.example
├── README.md
├── requirements.txt
├── main.py              # pipeline RAG + benchmark RAGAS (versão oficial, com persistência Chroma)
├── main_backup.py        # versão legada: sem persistência (Chroma efêmero), sem batching, sem tracking de tokens, sem loop de 5 rodadas
├── main.ipynb             # notebook espelhando main.py
├── plot_graph.py          # plota gráfico de barras com médias das métricas RAGAS salvas em results/
└── docs/                  # 7 PDFs (livros de algoritmos/lógica de programação em PT-BR) usados como corpus
```

Gerados em runtime (fora do controle de versão): `chroma_context_db_openai/` (índice vetorial) e `results*/` (CSVs + gráfico).

## Avaliação e resultados

Métricas RAGAS calculadas a cada rodada: `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`. Além delas, cada linha do CSV traz o tempo de resposta e o uso de tokens (input/output/total) da chamada de geração, medidos por um `TokenUsageTracker` (callback do LangChain) por pergunta.

## Notas e limitações

- `main_backup.py` é uma versão anterior/mais simples: usa `Chroma.from_documents(...)` sem `persist_directory` (recria o índice do zero a cada execução) e roda as perguntas uma única vez, sem tracking de tokens nem exportação em CSV — mantido apenas como referência histórica, não é o pipeline recomendado.
- `main.ipynb` reproduz fielmente `main.py`; use `main.py` para execução em lote.
- Dependências em `requirements.txt` não são pinadas (exceto o mínimo de `langchain-openai`) — para reprodutibilidade estrita, considere gerar um `pip freeze` após a instalação.
- `plot_graph.py` não é um grafo de conhecimento; é apenas uma visualização (matplotlib) das médias das métricas RAGAS.
