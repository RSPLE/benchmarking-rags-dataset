# Benchmarking RAGs Dataset

[English version](README.en.md)

Monorepo para comparar seis arquiteturas de Retrieval-Augmented Generation (RAG) sobre um dataset comum de 90 perguntas e respostas de referência. O projeto mede qualidade com RAGAS, latência e consumo de tokens, usa Chroma como índice vetorial e oferece OpenRouter como provedor padrão de LLM e embeddings. Cada pipeline possui ambiente e lockfile `uv` próprios para impedir conflitos de dependências.

## O que existe neste repositório

| Diretório | Estratégia | Recuperação / enriquecimento |
|---|---|---|
| `rags/context-rag/` | RAG clássico | top-5 por similaridade vetorial; resposta restrita ao contexto |
| `rags/graph-rag/` | Graph RAG experimental | Chroma + grafo `networkx` extraído por LLM; agente LangGraph |
| `rags/hybrid-rag/` | RAG híbrido | BM25 (peso 0,4) + Chroma (peso 0,6) |
| `rags/knowledge-enhanced-rag/` | Knowledge-Enhanced RAG | Chroma + grafo pedagógico curado em Neo4j; inclui API FastAPI |
| `rags/memory-augmented-rag/` | RAG com memória | agente LangGraph com `MemorySaver` e ferramenta de recuperação |
| `rags/self-rag/` | Self-RAG simplificado | geração, autocrítica binária e até um refinamento |
| `eval-dataset/` | Dataset compartilhado | 90 itens (`Q001`–`Q090`) com pergunta, resposta e fonte |
| `apps/ui/` | Dashboard React | execução, logs, progresso e comparação visual |
| `apps/api/` | API do dashboard | agrega runners, checkpoints e downloads CSV |

Os projetos preservam suas implementações e notebooks para facilitar inspeção e comparação. A instalação, o provedor de modelos, o dataset e a política de execução são compartilhados pela raiz.

## Execução sequencial e retomável

Cada pipeline percorre o dataset uma única vez e processa exatamente uma pergunta por vez. Após cada tentativa, o executor grava de forma atômica:

- `rags/<pipeline>/results/checkpoint.json`: estado, número de tentativas, erro e resultado por ID na execução local;
- `rags/<pipeline>/results/results.csv`: somente perguntas concluídas, com resposta, métricas RAGAS, latência e tokens.

Em uma nova execução:

- itens com `status: success` são ignorados;
- itens com `status: failed`, `running` (interrupção) ou sem estado são executados;
- uma mudança no conteúdo do dataset invalida o checkpoint de forma explícita, evitando misturar avaliações diferentes.

Assim, se `Q037` falhar no Self-RAG, executar novamente o mesmo comando repete `Q037` e qualquer outra falha, sem gastar novamente com os sucessos.

## Requisitos e instalação com uv

- Python 3.11, 3.12 ou 3.13 para execução local;
- [`uv`](https://docs.astral.sh/uv/);
- Docker Desktop com Docker Compose para o dashboard conteinerizado;
- chave do OpenRouter ou da OpenAI;
- Neo4j apenas para o `knowledge-enhanced-rag` quando o grafo for usado;
- PDFs próprios ou com autorização de redistribuição para formar os corpora locais.

```bash
git clone https://github.com/RSPLE/benchmarking-rags-dataset.git
cd benchmarking-rags-dataset
uv sync
```

O `pyproject.toml` e o `uv.lock` da raiz contêm somente o orquestrador. Cada diretório em `rags/` tem seu próprio `pyproject.toml`, `uv.lock` e `.venv`, criados sob demanda pelo `main.py`. A API também usa `apps/api/pyproject.toml` e `apps/api/uv.lock`. Não há arquivos `requirements.txt`. Essa separação permite que um pipeline evolua suas bibliotecas sem alterar o ambiente dos demais.

Crie a configuração local:

```bash
cp .env.example .env
```

No PowerShell:

```powershell
Copy-Item .env.example .env
```

## OpenRouter

O OpenRouter é o padrão tanto para chat quanto para embeddings. A integração usa sua API compatível com OpenAI em `https://openrouter.ai/api/v1`, conforme o [guia oficial](https://openrouter.ai/docs/quickstart) e a [API de embeddings](https://openrouter.ai/docs/api/reference/embeddings).

Configuração mínima:

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=~openai/gpt-latest

EMBEDDING_PROVIDER=openrouter
OPENROUTER_EMBEDDING_MODEL=openai/text-embedding-3-small
```

LLM e embeddings são configurados separadamente. Por exemplo, é possível gerar pelo OpenRouter e manter embeddings diretamente na OpenAI:

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...

EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_EMBEDDING_MODEL=text-embedding-3-large
```

Para usar somente OpenAI, defina ambos os provedores como `openai`. Ao trocar o modelo de embeddings, use um novo `CHROMA_PERSIST_DIR` ou remova conscientemente o índice antigo, pois dimensões diferentes não podem compartilhar a mesma coleção.

## Corpora locais

Os PDFs não são publicados por este monorepo. Além de evitar um repositório excessivamente grande, isso impede redistribuição sem licença comprovada. Coloque documentos que você tem direito de usar nestes diretórios:

```text
rags/context-rag/docs/
rags/graph-rag/docs/
rags/hybrid-rag/docs/
rags/knowledge-enhanced-rag/data/apostilas/
rags/memory-augmented-rag/docs/
rags/self-rag/docs/
```

Cada pipeline cria seu próprio índice Chroma na primeira execução. Índices, resultados, segredos e ambientes virtuais estão no `.gitignore`.

## Dashboard e containers

O dashboard segue uma interface operacional minimalista inspirada no projeto Smart. A página **Pipelines** possui um job por RAG, botão de execução/retomada, progresso por pergunta, contagem de falhas e logs. A página **Resultados** compara as médias RAGAS e permite baixar o CSV de cada pipeline.

Prepare o `.env` da raiz e os corpora locais, depois inicie toda a infraestrutura:

```bash
uv run python main.py dashboard --detach
```

O comando equivale a `docker compose up --build --detach`. Depois da construção inicial:

- dashboard: `http://localhost:8080`;
- documentação da API do dashboard: `http://localhost:8001/docs`;
- Neo4j Browser local: `http://localhost:7474`.

Cada RAG possui uma imagem e um runner isolado. As dependências Python ficam nos containers; somente Docker, os PDFs e o `.env` compartilhado permanecem necessários no host. Os checkpoints e CSVs ficam no volume `benchmark-results`, os índices Chroma no volume `rag-cache` e o banco local no volume `neo4j-data`. Todos sobrevivem a `docker compose down`. Use a interface para baixar os CSVs.

Para acompanhar ou encerrar a infraestrutura:

```bash
docker compose logs -f
docker compose down
```

## Uso

Listar pipelines:

```bash
uv run python main.py list
```

Verificar chaves e quantidade de PDFs:

```bash
uv run python main.py doctor
```

Executar ou retomar um RAG:

```bash
uv run python main.py run self-rag
```

Executar dois ou mais RAGs, na ordem informada:

```bash
uv run python main.py run context-rag hybrid-rag self-rag
```

Executar ou retomar os seis RAGs sequencialmente, cada um em seu ambiente isolado:

```bash
uv run python main.py run all
```

O comando anterior `uv run python main.py run-all` continua disponível como alias.

Selecionar OpenAI apenas para uma execução:

```bash
uv run python main.py run hybrid-rag --provider openai
```

Iniciar a API do Knowledge-Enhanced RAG:

```bash
uv run python main.py api --host 127.0.0.1 --port 8000
```

A documentação interativa ficará em `http://127.0.0.1:8000/docs`.

## Dataset e métricas

`eval-dataset/qa_dataset_90.json` contém 90 objetos com:

- `id`: identificador estável;
- `question`: pergunta de avaliação;
- `ground_truth`: resposta de referência;
- `source_book`: obra usada como origem conceitual.

Para cada pergunta, o RAGAS calcula `faithfulness`, `answer_relevancy`, `context_precision` e `context_recall`. O CSV também registra `answer_response_time_seconds`, `answer_input_tokens`, `answer_output_tokens` e `answer_total_tokens`.

## Estrutura

```text
.
├── main.py                  # CLI única do monorepo
├── benchmark_runner.py      # checkpoint e retomada por pergunta
├── rag_provider.py          # OpenRouter/OpenAI compartilhado
├── docker-compose.yml       # UI, API, seis runners e Neo4j
├── docker/                  # imagens da API e dos runners
├── pyproject.toml
├── uv.lock                 # somente dependências do orquestrador
├── .env.example
├── eval-dataset/
├── apps/
│   ├── api/                 # FastAPI + uv
│   ├── runner/              # serviço HTTP interno dos containers
│   └── ui/                  # React + Vite + nginx
└── rags/
    ├── context-rag/
    ├── graph-rag/
    ├── hybrid-rag/
    ├── knowledge-enhanced-rag/
    ├── memory-augmented-rag/
    └── self-rag/
```

Cada diretório de pipeline contém ainda seu próprio `pyproject.toml` e `uv.lock`. O único arquivo de configuração de segredos é `/.env`; os subdiretórios não possuem cópias de `.env.example`.

## Observações metodológicas

- O Graph RAG deste projeto não é a implementação GraphRAG da Microsoft; ele usa um grafo NetworkX menor, extraído de uma amostra dos chunks.
- O Self-RAG é uma aproximação por prompting, sem tokens de reflexão treinados.
- A memória do Memory-Augmented RAG é de processo; no benchmark, cada pergunta permanece isolada para comparação justa.
- O Knowledge-Enhanced RAG usa um grafo Neo4j curado; os demais pipelines não exigem Neo4j.
- O dataset tem 90 itens, mas os corpora locais podem variar. Comparações só são válidas quando modelos, documentos e parâmetros são controlados.

## Licença

Nenhuma licença de software foi declarada até o momento. Até que um arquivo `LICENSE` seja adicionado, permanecem reservados todos os direitos sobre o código. Os documentos usados como corpus mantêm as licenças e os direitos de seus respectivos autores e não fazem parte da distribuição deste monorepo.
