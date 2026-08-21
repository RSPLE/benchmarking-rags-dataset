# KE-RAG: Chatbot de Lógica de Programação

> **Atualização do monorepo:** este pipeline agora lê as 90 perguntas de `../eval-dataset/qa_dataset_90.json`, executa uma pergunta por vez e retoma apenas falhas por meio de `results/checkpoint.json`. Use `uv run python ../main.py run knowledge-enhanced-rag` a partir da raiz; as referências abaixo a 5 rodadas e 10 perguntas descrevem a versão histórica.
> Dependências vêm de `pyproject.toml`/`uv.lock` deste diretório e toda configuração vem exclusivamente de `../.env`; ignore as instruções históricas de `requirements.txt` e `.env` local abaixo.
> Dependências vêm de `pyproject.toml`/`uv.lock` deste diretório e toda configuração vem exclusivamente de `../.env`; ignore as instruções históricas de `requirements.txt` e `.env` local abaixo.

> **Atualização do monorepo:** este pipeline agora lê as 90 perguntas de `../eval-dataset/qa_dataset_90.json`, executa uma pergunta por vez e retoma apenas falhas por meio de `results/checkpoint.json`. Use `uv run python ../main.py run knowledge-enhanced-rag` a partir da raiz; as referências abaixo a 5 rodadas e 10 perguntas descrevem a versão histórica.

Chatbot baseado em **KE-RAG (Knowledge Enhanced RAG)** para ajudar alunos iniciantes de Computação na matéria de Lógica de Programação. Combina busca semântica em apostilas PDF (RAG clássico) com um **Knowledge Graph no Neo4j**, curado manualmente, para enriquecer as respostas com relações entre conceitos (pré-requisitos, categorização, progressão de aprendizado).

## Contextualização

RAG (Retrieval-Augmented Generation) é o padrão de recuperar trechos relevantes de uma base documental e injetá-los no prompt de um LLM para fundamentar a resposta. "Knowledge-Enhanced RAG" designa a família de técnicas que complementam esse retrieval textual com uma fonte de conhecimento estruturado adicional.

Neste projeto, o enriquecimento é feito com um **Knowledge Graph curado manualmente** (não extraído automaticamente de texto por LLM ou NER): um grafo fixo de 14 conceitos de Lógica de Programação e suas relações (pré-requisito, categorização, similaridade, progressão), definido diretamente em código e materializado no Neo4j. Ao responder, o sistema tenta identificar qual conceito do grafo é relevante para a pergunta (por casamento de palavras-chave) e injeta no prompt tanto os trechos recuperados das apostilas quanto os fatos, pré-requisitos e próximos conceitos vindos do grafo — permitindo respostas que não só citam o conteúdo das apostilas, mas também situam o aluno na jornada de aprendizado (o que ele precisa saber antes, e o que vem depois).

## Arquitetura do pipeline

```
Pergunta do aluno (via API ou avaliação RAGAS)
        │
        ▼
┌────────────────────┐
│   Chatbot (KE-RAG)  │  src/chatbot.py
└─────────┬───────────┘
          │
    ┌─────┴──────┐
    │            │
    ▼            ▼
┌───────┐  ┌───────────────┐
│ Chroma │  │ Knowledge     │
│ (k=5)  │  │ Graph (Neo4j) │
└───┬────┘  └──────┬────────┘
    │              │  fatos, pré-requisitos,
    │  chunks       │  próximos conceitos (1 salto)
    └──────┬────────┘
           ▼
   PROMPT_TEMPLATE (apostilas + KG + histórico de sessão)
           │
           ▼
   ChatOpenAI (gpt-5.5)
           │
           ▼
   Resposta enriquecida
```

| Etapa | Função / arquivo |
|---|---|
| Ingestão de PDFs | `carregar_pdfs()` — `src/ingestion.py:30-49` |
| Chunking | `dividir_em_chunks()` — `src/ingestion.py:52-70` |
| Embeddings | `build_embeddings()` — `rag_settings.py:67-71` |
| Indexação/carregamento vetorial | `criar_indice()`, `load_or_create_index()`, `reindexar()` — `src/ingestion.py:73-178` |
| Construção do Knowledge Graph | `KnowledgeGraph.build_graph()` — `src/knowledge_graph.py:38-134` |
| Consulta ao Knowledge Graph | `get_prerequisites`, `get_related_facts`, `get_next_concepts`, `find_concept` — `src/knowledge_graph.py:136-257` |
| Retrieval combinado (RAG + KG) | `KERagRetriever.retrieve()` — `src/retriever.py:34-79` |
| Geração da resposta | `Chatbot.chat()` — `src/chatbot.py:85-157` |
| API REST | `app.py` |
| Avaliação (RAGAS) | `evaluate_ke_rag()` — `main.py:76-118` (também acessível via `python evaluate.py`) |

## Detalhes técnicos

### Prompt de geração

Único prompt de sistema/geração do projeto, `src/chatbot.py:23-47`:

```
Você é um professor assistente de Lógica de Programação, especialista em ajudar alunos iniciantes de Computação. Seu tom é amigável, paciente e didático.

Use as informações abaixo para responder à pergunta do aluno de forma clara e completa.

--- CONTEXTO DAS APOSTILAS ---
{contexto_docs}

--- FATOS DO KNOWLEDGE GRAPH ---
{kg_facts}

--- PRÉ-REQUISITOS DO CONCEITO ---
{prerequisites}

--- PRÓXIMOS CONCEITOS A ESTUDAR ---
{next_concepts}

Instruções:
- Responda sempre em português brasileiro
- Use exemplos simples e práticos quando possível
- Se o aluno não entender algo, sugira os pré-requisitos listados acima
- Ao final, sugira o próximo conceito a estudar se houver
- Se não encontrar informação nas apostilas, use seu conhecimento geral sobre o tema
- Seja encorajador e positivo

Pergunta do aluno: {pergunta}
```

Esse prompt (já preenchido) é enviado como uma única `HumanMessage`, junto com o histórico de mensagens da sessão. Não há prompt de extração de grafo via LLM — o grafo é estático e curado manualmente (ver seção de grafo).

### Chunking

- Biblioteca: `langchain_text_splitters.RecursiveCharacterTextSplitter`.
- `chunk_size=800`, `chunk_overlap=100`, `add_start_index=True` (`src/ingestion.py:62-66`).
- Carregamento via `PyPDFDirectoryLoader` (`langchain_community.document_loaders`), um `Document` por página.
- Indexação em batches de 500 chunks (`src/ingestion.py:99`).

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

- **ChromaDB**, persistido em disco: `persist_directory=CHROMA_PERSIST_DIR` (default `./chroma_knowledge_db_openai`), `collection_name=CHROMA_COLLECTION_NAME` (default `knowledge_collection_openai`).
- `load_or_create_index()` carrega o índice existente ou, se vazio, ingere os PDFs de `DOCS_DIR`; `reindexar()` apaga todos os IDs existentes e recria o índice do zero (usado pelo endpoint `POST /index`).

### Configuração de grafo

**Banco de grafos**: Neo4j (via driver oficial `neo4j.GraphDatabase`), recomendado Neo4j Aura Free (cloud) — não é NetworkX, não é extraído automaticamente de texto.

**Conexão** (`src/knowledge_graph.py:19-32`): lê `NEO4J_URI`, `NEO4J_USERNAME` (default `neo4j`) e `NEO4J_PASSWORD` do ambiente; levanta `ValueError` se `NEO4J_URI` ou `NEO4J_PASSWORD` estiverem ausentes.

**Schema de nós**: label único `Conceito {nome: string, dificuldade: int}`. 14 nós fixos, criados por `build_graph()`:

| Conceito | Dificuldade |
|---|---|
| Variáveis | 1 |
| Tipos de Dados | 1 |
| Operadores | 1 |
| Entrada e Saída | 1 |
| Condicionais IF/ELSE | 2 |
| Switch/Case | 2 |
| Loop FOR | 2 |
| Loop WHILE | 2 |
| Loop DO-WHILE | 2 |
| Funções | 3 |
| Vetores/Arrays | 3 |
| Matrizes | 3 |
| Recursão | 4 |
| Ordenação | 4 |

**Schema de relações** (direcionadas, criadas por `build_graph()` via Cypher dinâmico `CREATE (a)-[:{tipo}]->(b)`):
- `REQUER` — pré-requisito necessário (17 arestas).
- `SIMILAR_A` — conceitos com características parecidas (5 arestas).
- `LEVA_A` — próximo conceito sugerido na jornada de aprendizado (13 arestas).
- `É_UM_TIPO_DE` — está listada no dicionário de relações do código, mas **nenhuma aresta desse tipo é de fato criada**: todas as suas entradas têm origem igual ao destino, e o loop de criação (`src/knowledge_graph.py:124-132`) explicitamente pula pares com `origem == destino`. Documentado aqui como peculiaridade conhecida do código, não como funcionalidade ativa.

**Construção**: `KnowledgeGraph.build_graph()` primeiro limpa o grafo existente (`MATCH (n:Conceito) DETACH DELETE n`), depois recria os 14 nós e as arestas.

**Consultas** (todas de 1 salto, sem traversal multi-hop, sem algoritmos de comunidade/Leiden/Louvain):
- `get_prerequisites(conceito)` → `MATCH (a:Conceito {nome: $nome})-[:REQUER]->(b) RETURN b.nome`.
- `get_related_facts(conceito)` → todas as relações de saída de 1 salto, formatadas como texto.
- `get_next_concepts(conceito)` → `MATCH (a:Conceito {nome: $nome})-[:LEVA_A]->(b) RETURN b.nome ORDER BY b.dificuldade`.
- `find_concept(consulta)` → **não é traversal de grafo**: é matching de palavras-chave contra um dicionário fixo de ~30 termos/sinônimos em português (ex. "variável", "loop for", "recursão") para identificar qual `Conceito` é relevante à pergunta do aluno. Retorna o primeiro match encontrado, sem scoring.

### Parâmetros de recuperação

- Retrieval vetorial: `similarity_search(pergunta, k=5)` no Chroma — sem threshold, sem MMR, sem reranking.
- Retrieval do grafo: sempre 1 salto a partir do conceito identificado; sem top-k (retorna todos os fatos/pré-requisitos/próximos conceitos daquele conceito).
- Identificação do conceito: primeiro match de palavra-chave, sem múltiplos candidatos nem pontuação de confiança.
- Memória de conversa: `deque(maxlen=10)` por `session_id` — últimas 5 trocas (pergunta+resposta), em memória de processo (não persistida).

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
| neo4j | não pinada |
| fastapi | não pinada |
| uvicorn | não pinada |
| pydantic | não pinada |

Não há `pyproject.toml` nem lockfile.

## Requisitos

- Python 3.10+
- Conta OpenAI com acesso à API
- Conta gratuita no [Neo4j Aura](https://neo4j.com/cloud/aura) (ou outra instância Neo4j) para o Knowledge Graph
- Conta LangSmith, para rastreamento (tracing) do fluxo e cálculo de uso de tokens

## Replicabilidade / Instalação

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
```

### Criar o banco Neo4j Aura

1. Acesse [neo4j.com/cloud/aura](https://neo4j.com/cloud/aura) e crie uma conta gratuita.
2. Clique em **Create Free Instance**.
3. Anote a URI, usuário e senha gerados — vão para `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` no `.env`.

## Configuração

Crie um `.env` a partir de `.env.example`:

```env
OPENAI_API_KEY=sk-sua_chave_openai
OPENAI_MODEL=gpt-5.5
OPENAI_EMBEDDING_MODEL=text-embedding-3-large
OPENAI_REASONING_EFFORT=medium

DOCS_DIR=../docs/

CHROMA_PERSIST_DIR=./chroma_knowledge_db_openai
CHROMA_COLLECTION_NAME=knowledge_collection_openai

NEO4J_URI=neo4j+s://your-instance-id.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_neo4j_password_here

LANGCHAIN_TRACING_V2=false
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=benchmark-knowledge-enhanced-rag
```

> **Atenção ao `DOCS_DIR`**: o valor padrão no `.env.example` é `../docs/` (uma pasta **fora** deste projeto). Os PDFs deste repositório estão em `data/apostilas/`, dentro da própria pasta do projeto. Ajuste `DOCS_DIR=./data/apostilas/` no seu `.env` antes de rodar, ou a ingestão não encontrará nenhum PDF.

| Variável | Default | Descrição |
|---|---|---|
| `OPENAI_API_KEY` | — (obrigatória) | Chave da API OpenAI. |
| `OPENAI_MODEL` | `gpt-5.5` | Modelo usado pelo chatbot e pela avaliação RAGAS. Reportado como está no código/`.env.example`. |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-large` | Modelo de embeddings. |
| `OPENAI_REASONING_EFFORT` | `medium` | Parâmetro `reasoning_effort` do `ChatOpenAI` (Responses API). |
| `DOCS_DIR` | `../docs/` (ver aviso acima) | Pasta com os PDFs/apostilas a indexar. |
| `CHROMA_PERSIST_DIR` | `./chroma_knowledge_db_openai` | Diretório de persistência do índice vetorial. |
| `CHROMA_COLLECTION_NAME` | `knowledge_collection_openai` | Nome da coleção no Chroma. |
| `NEO4J_URI` | — (obrigatória para o grafo) | URI de conexão com o Neo4j Aura/instância. |
| `NEO4J_USERNAME` | `neo4j` | Usuário do Neo4j. |
| `NEO4J_PASSWORD` | — (obrigatória para o grafo) | Senha do Neo4j. |
| `LANGCHAIN_TRACING_V2` | `false` | Ativa tracing no LangSmith. |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` | Endpoint do LangSmith. |
| `LANGCHAIN_API_KEY` | — | Chave do LangSmith. |
| `LANGCHAIN_PROJECT` | `benchmark-knowledge-enhanced-rag` | Nome do projeto no LangSmith. |

Se `NEO4J_URI`/`NEO4J_PASSWORD` não forem configurados, o chatbot ainda funciona (o retriever captura a exceção de conexão e segue sem o Knowledge Graph, usando só a busca vetorial).

## Uso

Este projeto tem **dois modos de execução**: uma API interativa (chatbot) e um script de avaliação em lote (benchmark RAGAS).

### 1. API do chatbot

```bash
python app.py
# ou
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

A API fica disponível em `http://localhost:8000` (documentação Swagger em `/docs`).

Antes do primeiro uso, construa o Knowledge Graph no Neo4j:
```bash
curl -X POST http://localhost:8000/build-graph
```

Endpoints:

| Endpoint | Descrição |
|---|---|
| `GET /health` | Verifica se a API, o chatbot e o Knowledge Graph estão prontos. |
| `POST /chat` | Envia `{"question": "...", "session_id": "..."}`, recebe `{"answer", "prerequisites", "next_concepts"}`. |
| `POST /index` | Reindexa os PDFs de `DOCS_DIR` (use ao adicionar novas apostilas). |
| `POST /build-graph` | (Re)constrói o Knowledge Graph no Neo4j. |

Exemplo:
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "O que é uma variável em programação?", "session_id": "aluno123"}'
```

### 2. Avaliação RAGAS (benchmark)

```bash
python main.py
# ou, de forma equivalente:
python evaluate.py
```

O script:
1. Carrega ou cria o índice Chroma (`load_or_create_index()`) e tenta conectar ao Knowledge Graph (segue sem ele se a conexão falhar).
2. Roda **5 rodadas** das mesmas **10 perguntas de benchmark** fixas no código (`test_queries`/`ground_truths` em `main.py`).
3. Para cada pergunta, consulta o retriever combinado (Chroma + KG) e gera a resposta via `Chatbot.chat()`.
4. Avalia cada rodada com RAGAS e salva um CSV por rodada em `results/` (ou `results_2/`, `results_3/`... se a pasta já existir).

## Estrutura do projeto

```
knowledge-enhanced-rag/
├── .env.example
├── README.md
├── requirements.txt
├── app.py                      # API REST (FastAPI) — modo interativo do chatbot
├── main.py                     # driver de avaliação RAGAS (test_queries, ground_truths, evaluate_ke_rag)
├── evaluate.py                 # atalho: importa e chama evaluate_ke_rag() de main.py
├── rag_settings.py             # utilitários compartilhados: env, LLM/embeddings, tracking de uso, RAGAS, salvar CSV
├── main.ipynb                  # variante histórica (ver Notas)
├── data/
│   └── apostilas/              # PDFs usados como base de conhecimento
└── src/
    ├── __init__.py
    ├── ingestion.py             # carrega, divide e indexa os PDFs no Chroma
    ├── knowledge_graph.py       # constrói e consulta o Knowledge Graph (Neo4j)
    ├── retriever.py             # KERagRetriever — combina Chroma + Knowledge Graph
    └── chatbot.py               # Chatbot: PROMPT_TEMPLATE, memória por sessão, geração
```

Gerado em runtime (fora do controle de versão): `chroma_knowledge_db_openai/` (índice vetorial) e `results*/` (CSVs da avaliação).

## Avaliação e resultados

Métricas RAGAS calculadas a cada rodada: `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`. Cada linha do CSV também traz `answer_response_time_seconds`, `answer_input_tokens`, `answer_output_tokens` e `answer_total_tokens`, medidos por pergunta via `TokenUsageTracker`.

## Notas e limitações

- A relação `É_UM_TIPO_DE` está definida no dicionário de relações do código mas nunca é de fato criada no grafo (ver seção "Configuração de grafo") — não confiar nela em consultas.
- Todas as consultas ao grafo são de 1 salto; não há multi-hop, comunidades ou algoritmos de centralidade.
- A identificação do conceito relevante para uma pergunta é feita por casamento simples de palavras-chave, não por NER/LLM — perguntas fora do vocabulário mapeado não acionam o Knowledge Graph (o chatbot cai para "Nenhum fato do Knowledge Graph disponível" e responde só com o contexto das apostilas).
- A memória de conversa (`deque` por `session_id`) existe apenas em memória de processo — reiniciar a API perde todo o histórico.
- `main.ipynb` é uma variante histórica e não equivalente ao pipeline atual: usava FAISS como vector store (não Chroma), embeddings `HuggingFaceEmbeddings (sentence-transformers/all-mpnet-base-v2)`, um LLM via endpoint compatível com OpenAI hospedado na DigitalOcean (`llama3.3-70b-instruct`), e um conjunto de perguntas de teste diferente (sobre livros de referência específicos). Não faz múltiplas rodadas nem salva CSV.
- Dependências em `requirements.txt` não são pinadas (exceto o mínimo de `langchain-openai`).
