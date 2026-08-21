# Memory-Augmented RAG

> **Atualização do monorepo:** este pipeline agora lê as 90 perguntas de `../eval-dataset/qa_dataset_90.json`, executa uma pergunta por vez e retoma apenas falhas por meio de `results/checkpoint.json`. Use `uv run python ../main.py run memory-augmented-rag` a partir da raiz; as referências abaixo a 5 rodadas e 10 perguntas descrevem a versão histórica.
> Dependências vêm de `pyproject.toml`/`uv.lock` deste diretório e toda configuração vem exclusivamente de `../.env`; ignore as instruções históricas de `requirements.txt` e `.env` local abaixo.

Sistema de perguntas e respostas sobre documentos PDF usando Retrieval-Augmented Generation implementado como um **agente com memória conversacional**, orquestrado via LangGraph, com avaliação automática de qualidade via RAGAS.

## Contextualização

RAG (Retrieval-Augmented Generation) é o padrão de recuperar trechos relevantes de uma base documental e injetá-los no prompt de um LLM para fundamentar a resposta. Neste projeto, em vez de montar o prompt de recuperação manualmente a cada pergunta, o retrieval é exposto como uma **tool** para um agente (`langchain.agents.create_agent`), que decide quando chamá-la, e o agente é equipado com um **checkpointer** do LangGraph (`MemorySaver`) que permite manter o histórico da conversa entre turnos, associado a um identificador de sessão (`thread_id`).

Isso é o que caracteriza este projeto como "memory-augmented": a arquitetura suporta conversas multi-turno com memória de curto prazo (o histórico de mensagens da própria thread), diferente de um pipeline RAG stateless que trata cada pergunta isoladamente.

## Arquitetura do pipeline

```
docs/*.pdf
    │  PyPDFLoader + DirectoryLoader
    ▼
Documentos (1 por página)
    │  RecursiveCharacterTextSplitter (chunk_size=800, overlap=100, add_start_index=True)
    ▼
Chunks
    │  OpenAIEmbeddings (text-embedding-3-large)
    ▼
Chroma (persistente, batches de 500)
    │
    ▼
Tool "retrieve_context" (k=5) ◄──────────────┐
    │                                        │  o agente decide quando chamar a tool
    ▼                                        │
Agente LangGraph (create_agent + MemorySaver) ┘
    │  system_prompt + histórico da thread
    ▼
Resposta
    │
    ▼
Avaliação RAGAS (5 rodadas)
    │
    ▼
results/memory-augmented-rag-run-N_i.csv
```

| Etapa | Função / arquivo |
|---|---|
| Configuração de ambiente | `configure_environment()` — `rag_settings.py:36-45` |
| Ingestão + chunking + indexação | `build_vectorstore()` — `main.py:73-110` |
| Embeddings | `build_embeddings()` — `rag_settings.py:67-71` |
| Tool de retrieval | `retrieve_context()` — `main.py:114-122` |
| Construção do agente | `build_agent()` — `main.py:113-137` |
| Execução de uma pergunta | `run_agent_and_collect_data()` — `main.py:140-171` |
| Avaliação | `evaluate_with_ragas()` — `main.py:173-192`, usando `run_ragas()` de `rag_settings.py:277-298` |
| Persistência dos resultados | `salvar()` — `rag_settings.py:301-336` |
| Orquestração / loop principal | bloco `if __name__ == "__main__"` — `main.py:195-208` |

## Detalhes técnicos

### Prompt de sistema do agente

`main.py:126-130`:

```
Voce tem acesso a uma ferramenta que recupera contexto dos documentos. Use a ferramenta para responder as perguntas do usuario. Responda sempre em portugues, mesmo que a pergunta seja feita em outro idioma.
```

Não há prompt de geração manual separado — o agente monta a resposta a partir do histórico de mensagens da thread mais o resultado da tool, seguindo esse `system_prompt`.

### Chunking

- Biblioteca: `langchain_text_splitters.RecursiveCharacterTextSplitter`.
- `chunk_size=800`, `chunk_overlap=100`, `add_start_index=True` (`main.py:90-94`).
- Carregamento via `DirectoryLoader(path=DOCS_DIR, glob="**/*.pdf", loader_cls=PyPDFLoader)`.

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

- **ChromaDB**, persistido em disco: `persist_directory=CHROMA_PERSIST_DIR` (default `./chroma_memory_db_openai`), `collection_name=CHROMA_COLLECTION_NAME` (default `memory_collection_openai`).
- Ingestão em batches de 500, condicional a `vector_store._collection.count() == 0`.

Este projeto não usa nenhum grafo de conhecimento — o único componente "graph" é a biblioteca `langgraph`, usada para orquestrar o agente e seu checkpointer, não para representar conhecimento.

### Mecanismo de memória (agente)

- O agente é construído com `create_agent(llm, tools=[retrieve_context], system_prompt=prompt, checkpointer=MemorySaver())` (`main.py:132-137`).
- `MemorySaver` é um checkpointer **em memória de processo** (não persiste em disco entre execuções do script).
- Cada chamada ao agente recebe um `config={"configurable": {"thread_id": thread_id}}`; o histórico da conversa fica associado a esse `thread_id` — chamadas com o mesmo `thread_id` compartilham contexto, chamadas com `thread_id` diferentes são isoladas.
- **Importante**: no laço de avaliação (`run_agent_and_collect_data`, `main.py:141-148`), um novo `thread_id = str(uuid.uuid4())` é gerado a cada pergunta. Ou seja, embora a arquitetura suporte memória multi-turno, o script de benchmark **não a exercita** — cada uma das 10 perguntas roda em uma thread isolada, sem histórico compartilhado entre elas. Para aproveitar a memória em uso real, é necessário reutilizar o mesmo `thread_id` entre chamadas subsequentes de `agent.stream(...)`.

### Parâmetros de recuperação

- Tool `retrieve_context`: `vector_store.similarity_search(query, k=5)` — top-5 por similaridade vetorial padrão do Chroma.
- Sem MMR, sem reranking, sem filtros de metadata, sem threshold de score.
- É o próprio agente (via tool-calling) quem decide se e quando chamar a tool de retrieval — não há chamada obrigatória/hardcoded antes da geração.

### Versões das bibliotecas

`requirements.txt` não fixa versões exatas (só um mínimo):

| Biblioteca | Versão |
|---|---|
| langchain | não pinada |
| langchain-community | não pinada |
| langchain-openai | `>=1.1.11` |
| langchain-text-splitters | não pinada |
| langgraph | não pinada |
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

DOCS_DIR=../docs/

CHROMA_PERSIST_DIR=./chroma_memory_db_openai
CHROMA_COLLECTION_NAME=memory_collection_openai

LANGCHAIN_TRACING_V2=false
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=benchmark-memory-augmented-rag
```

> **Atenção ao `DOCS_DIR`**: o valor padrão no `.env.example` é `../docs/` (uma pasta **fora** deste projeto). Os PDFs deste repositório estão em `docs/`, dentro da própria pasta do projeto. Ajuste `DOCS_DIR=./docs/` no seu `.env` antes de rodar, ou a ingestão não encontrará nenhum PDF.

| Variável | Default | Descrição |
|---|---|---|
| `OPENAI_API_KEY` | — (obrigatória) | Chave da API OpenAI. |
| `OPENAI_MODEL` | `gpt-5.5` | Modelo usado pelo agente e pela avaliação RAGAS. Reportado como está no código/`.env.example`. |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-large` | Modelo de embeddings. |
| `OPENAI_REASONING_EFFORT` | `medium` | Parâmetro `reasoning_effort` do `ChatOpenAI` (Responses API). |
| `DOCS_DIR` | `../docs/` (ver aviso acima) | Pasta com os PDFs a indexar. |
| `CHROMA_PERSIST_DIR` | `./chroma_memory_db_openai` | Diretório de persistência do índice vetorial. |
| `CHROMA_COLLECTION_NAME` | `memory_collection_openai` | Nome da coleção no Chroma. |
| `LANGCHAIN_TRACING_V2` | `false` | Ativa tracing no LangSmith. |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` | Endpoint do LangSmith. |
| `LANGCHAIN_API_KEY` | — | Chave do LangSmith. |
| `LANGCHAIN_PROJECT` | `benchmark-memory-augmented-rag` | Nome do projeto no LangSmith. |

## Uso

Coloque os PDFs em `docs/` (por padrão contém 1 apostila de lógica de programação) e execute:

```bash
python main.py
```

O script:
1. Indexa os PDFs de `DOCS_DIR` no Chroma (pula se a coleção já existir).
2. Roda **5 rodadas** das mesmas **10 perguntas de benchmark** fixas no código (`test_queries`/`ground_truths` em `main.py`), reconstruindo o agente a cada rodada.
3. Para cada pergunta, invoca o agente (que decide se/quando usar a tool de retrieval) e coleta a resposta final.
4. Avalia cada rodada com RAGAS e salva um CSV por rodada em `results/` (ou `results_2/`, `results_3/`... se a pasta já existir).

## Estrutura do projeto

```
memory-augmented-rag/
├── .env.example
├── README.md
├── requirements.txt
├── main.py              # agente RAG com memória (LangGraph) + benchmark RAGAS
├── rag_settings.py       # utilitários compartilhados: env, LLM/embeddings, tracking de uso, RAGAS, salvar CSV
├── main.ipynb            # variante histórica (ver Notas)
└── docs/                 # PDFs usados como base de conhecimento
```

Gerados em runtime (fora do controle de versão): `chroma_memory_db_openai/` (índice vetorial) e `results*/` (CSVs).

## Avaliação e resultados

Métricas RAGAS calculadas a cada rodada: `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`. Cada linha do CSV também traz `answer_response_time_seconds`, `answer_input_tokens`, `answer_output_tokens` e `answer_total_tokens`, medidos por pergunta via `TokenUsageTracker`.

## Notas e limitações

- A memória do checkpointer **não é exercitada** pelo script de benchmark (`main.py`), já que cada pergunta roda em um `thread_id` novo — ver seção "Mecanismo de memória" acima. Para testar multi-turno de fato, é necessário adaptar o código para reutilizar o mesmo `thread_id` entre chamadas.
- `MemorySaver` guarda o histórico apenas em memória de processo — reiniciar o script perde todo o histórico de conversas.
- `main.ipynb` é uma variante histórica com stack diferente: `HuggingFaceEmbeddings` local, `InMemoryVectorStore` (não persistente) em vez de Chroma, chunking com `chunk_size=1000`, e um LLM via endpoint compatível com OpenAI hospedado na DigitalOcean, em vez da stack OpenAI usada em `main.py`. Não é equivalente ao pipeline oficial.
- Dependências em `requirements.txt` não são pinadas (exceto o mínimo de `langchain-openai`).
