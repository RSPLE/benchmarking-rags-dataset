# Benchmarking RAGs Dataset

[English version](README.en.md)

Monorepo para comparar seis arquiteturas de Retrieval-Augmented Generation (RAG) sobre um dataset comum de 90 perguntas e respostas de referência. O projeto mede qualidade com RAGAS, latência e consumo de tokens, usa Chroma como índice vetorial e oferece OpenRouter como provedor padrão de LLM e embeddings. Cada pipeline possui ambiente e lockfile `uv` próprios para impedir conflitos de dependências.

## O que existe neste repositório

| Diretório | Estratégia | Recuperação / enriquecimento |
|---|---|---|
| `rags/context-rag/` | RAG clássico | top-5 por similaridade vetorial; resposta restrita ao contexto |
| `rags/graph-rag/` | Graph RAG experimental | Chroma + grafo `networkx` extraído por LLM; agente LangGraph |
| `rags/hybrid-rag/` | RAG híbrido | BM25 (peso 0,4) + Chroma (peso 0,6) |
| `rags/knowledge-enhanced-rag/` | Knowledge-Enhanced RAG | Chroma + grafo pedagógico curado em Neo4j |
| `rags/memory-augmented-rag/` | RAG com memória | agente LangGraph com `MemorySaver` e ferramenta de recuperação |
| `rags/self-rag/` | Self-RAG simplificado | geração, autocrítica binária e até um refinamento |
| `eval-dataset/` | Dataset compartilhado | 90 itens (`Q001`–`Q090`) com pergunta, resposta e fonte |

Os projetos preservam suas implementações e notebooks para facilitar inspeção e comparação. A instalação, o provedor de modelos, o dataset e a política de execução são compartilhados pela raiz.

## Execução sequencial e retomável

Cada pipeline percorre o dataset uma única vez e processa exatamente uma pergunta por vez. Após cada tentativa, o executor grava de forma atômica:

- `rags/<pipeline>/results/checkpoint.json`: estado, número de tentativas, erro e resultado por ID na execução local;
- `rags/<pipeline>/results/results.csv`: CSV cumulativo com todas as perguntas concluídas, sem duplicar IDs entre lotes;
- `rags/<pipeline>/results/errors.json`: falhas atuais com ID, pergunta, tentativa, tipo, mensagem, saída e traceback.

Em uma nova execução:

- itens com `status: success` são ignorados;
- itens com `status: failed`, `running` (interrupção) ou sem estado são executados;
- uma mudança no conteúdo do dataset invalida o checkpoint de forma explícita, evitando misturar avaliações diferentes.

Assim, se `Q037` falhar no Self-RAG, executar novamente o mesmo comando repete `Q037` e qualquer outra falha, sem gastar novamente com os sucessos.

O limite `--questions X` controla quantas perguntas ainda não concluídas cada RAG tentará na rodada. Repetir o comando avança em lotes, mantendo os sucessos anteriores no mesmo CSV. Falhas anteriores têm prioridade de retomada; depois delas, o executor continua pelos itens pendentes na ordem do dataset. Sem a flag, todo o restante do dataset é processado.

O valor de `X` é aplicado **a cada RAG selecionado**. Portanto, selecionar três RAGs com `--questions 10` permite até 30 tentativas no total: no máximo 10 em cada pipeline. A seleção não é aleatória e a ordem de uma rodada é:

1. falhas registradas anteriormente, na ordem do dataset;
2. perguntas ainda pendentes, também na ordem do dataset;
3. perguntas já concluídas nunca são executadas novamente.

`--questions` limita tentativas, e não sucessos. Se um lote de 10 tiver duas falhas, a rodada termina após as 10 tentativas, com oito novas linhas no CSV e duas entradas no JSON de erros. Na próxima rodada, essas duas falhas serão tentadas antes de novos itens.

### Exemplo concreto: lote inicial de 10

| Resultado do primeiro lote | Estado salvo | Próxima execução sem `--questions` |
|---|---|---|
| As 10 perguntas tiveram sucesso | 10 sucessos e 80 pendentes | ignora as 10 concluídas e executa somente as 80 pendentes |
| 8 tiveram sucesso e 2 falharam | 8 sucessos, 2 falhas e 80 pendentes | ignora os 8 sucessos, tenta primeiro as 2 falhas e depois as 80 pendentes |
| As 10 falharam | 10 falhas e 80 pendentes | tenta novamente as 10 falhas e depois segue para as 80 pendentes |

Uma falha não interrompe imediatamente o lote: ela é registrada e o RAG continua até atingir o limite de tentativas daquela rodada. Ao selecionar vários RAGs, o orquestrador também continua para o próximo pipeline; ao final, retorna código `1` se qualquer RAG teve falhas na rodada.

Se a segunda execução também usar `--questions 10`, as falhas consomem primeiro as vagas desse novo lote. Por exemplo, com duas falhas anteriores, a rodada tentará essas duas e depois até oito perguntas pendentes. Sem a flag, não há limite de lote e todo o conjunto ainda não concluído é tentado.

### Persistência dos resultados

O `checkpoint.json` é a fonte de verdade da execução. O `results.csv` não recebe linhas por simples concatenação: ele é reconstruído atomicamente após cada tentativa a partir dos sucessos do checkpoint. Isso mantém a ordem do dataset, evita IDs duplicados e reduz o risco de um CSV incompleto em caso de interrupção.

O `results.csv` contém somente perguntas concluídas com sucesso. O `errors.json` contém somente as falhas ainda abertas e segue esta estrutura:

```json
{
  "version": 1,
  "project": "self-rag",
  "dataset": "/caminho/para/eval-dataset/qa_dataset_90.json",
  "dataset_sha256": "sha256-do-dataset",
  "updated_at": "2026-09-04T12:00:04+00:00",
  "count": 1,
  "errors": [
    {
      "id": "Q037",
      "question": "Pergunta que estava sendo avaliada",
      "attempts": 2,
      "started_at": "2026-09-04T12:00:00+00:00",
      "finished_at": "2026-09-04T12:00:04+00:00",
      "error_type": "RuntimeError",
      "message": "mensagem original da exceção",
      "output": "RuntimeError: mensagem original da exceção",
      "traceback": "Traceback completo da falha"
    }
  ]
}
```

Quando uma pergunta é concluída em uma nova tentativa, sua entrada desaparece do `errors.json` e seu resultado passa a constar uma única vez no CSV. Se o processo for encerrado enquanto uma pergunta estiver com estado `running`, ela será marcada como `InterruptedRun` e retomada na próxima execução.

Os artefatos ficam em `rags/<pipeline>/results/`. Para reiniciar uma avaliação do zero, arquive o diretório completo do pipeline — checkpoint, CSV e JSON de erros devem permanecer juntos.

## Requisitos e instalação com uv

- Python 3.11, 3.12 ou 3.13 para execução local;
- [`uv`](https://docs.astral.sh/uv/);
- Docker Desktop com Docker Compose apenas se você quiser executar um Neo4j local em container;
- chave do OpenRouter ou da OpenAI;
- Neo4j apenas para o `knowledge-enhanced-rag` quando o grafo for usado;
- PDFs próprios ou com autorização de redistribuição para formar os corpora locais.

```bash
git clone https://github.com/RSPLE/benchmarking-rags-dataset.git
cd benchmarking-rags-dataset
uv sync
```

O `pyproject.toml` e o `uv.lock` da raiz contêm somente o orquestrador. Cada diretório em `rags/` tem seu próprio `pyproject.toml`, `uv.lock` e `.venv`, criados sob demanda pelo `main.py`. Não há arquivos `requirements.txt`. Essa separação permite que um pipeline evolua suas bibliotecas sem alterar o ambiente dos demais.

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
LLM_MAX_TOKENS=1024
RAGAS_MAX_TOKENS=2048
LLM_TIMEOUT_SECONDS=600
RAGAS_TIMEOUT_SECONDS=600
RAGAS_MAX_WORKERS=2

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

Os PDFs estão presentes nas seguintes pastas:

```text
rags/context-rag/docs/
rags/graph-rag/docs/
rags/hybrid-rag/docs/
rags/knowledge-enhanced-rag/data/apostilas/
rags/memory-augmented-rag/docs/
rags/self-rag/docs/
```

Cada pipeline cria seu próprio índice Chroma na primeira execução. Índices, resultados, segredos e ambientes virtuais estão no `.gitignore`.

## Neo4j local opcional

Os benchmarks são controlados exclusivamente pela CLI. Docker não é necessário para os cinco RAGs que não usam Neo4j. Para executar o `knowledge-enhanced-rag` com o Neo4j local incluído no Compose, ajuste o `.env`:

```env
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=uma-senha-local-segura
```

Inicie o banco:

```bash
docker compose up -d
```

O banco fica disponível para a CLI em `127.0.0.1:7687`. O volume `neo4j-data` conserva os dados e a senha usada na primeira inicialização; se ele já existir, mantenha essa senha no `.env`. Para acompanhar ou encerrar apenas o banco:

```bash
docker compose logs -f neo4j
docker compose down
```

Ordem recomendada para testar o `knowledge-enhanced-rag` com Neo4j local:

```bash
docker compose up --detach neo4j
uv run python main.py doctor
uv run python main.py run knowledge-enhanced-rag --questions 3
```

### Neo4j hospedado

Para usar Neo4j Aura ou outra instância hospedada, não execute `docker compose`. Crie o banco hospedado, copie as credenciais fornecidas pelo serviço e configure o `.env`:

```env
NEO4J_URI=neo4j+s://seu-id.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=sua_senha
```

Depois execute diretamente o benchmark:

```bash
uv run python main.py run knowledge-enhanced-rag --questions 3
```

O `doctor` verifica apenas se as variáveis existem; a conexão real é validada quando o pipeline inicia. O benchmark conecta ao grafo, mas não o popula automaticamente. Para construir o grafo curado antes do benchmark, inicie a API em um terminal:

```bash
cd rags/knowledge-enhanced-rag
uv run python app.py
```

Em outro terminal, na raiz do projeto, execute:

```bash
curl -X POST http://127.0.0.1:8000/build-graph
```

Depois encerre a API e rode o comando do benchmark. Se o grafo não for configurado ou estiver vazio, o `knowledge-enhanced-rag` ainda pode usar somente a busca vetorial do Chroma.

## Uso

## Execução passo a passo

Antes dos testes, confirme a configuração e a quantidade de PDFs:

```bash
uv sync
uv run python main.py doctor
```

O `uv sync` instala dependências, mas não faz a ingestão. A ingestão é feita quando o RAG é iniciado. Cada RAG possui seu próprio índice Chroma e seus resultados ficam em `rags/<pipeline>/results/`.


1. Coloque os PDFs diretamente na pasta `docs/` de cada RAG. Para o `knowledge-enhanced-rag`, use `data/apostilas/`.
2. Configure a chave e o modelo no `.env`.
3. Execute o script, que valida os PDFs, roda `uv sync`, verifica a configuração e inicia os seis pipelines:

```bash
bash scripts/run_benchmark.sh
```

Por padrão, o script testa uma pergunta por pipeline. Para testar outro lote:

```bash
QUESTIONS=10 bash scripts/run_benchmark.sh
```

A ingestão ocorre automaticamente durante a inicialização de cada pipeline. O pipeline carrega os PDFs, divide o texto em chunks, gera embeddings e grava o índice Chroma antes de processar as perguntas.

Para testar cada RAG individualmente com até três perguntas:

```bash
uv run python main.py run context-rag --questions 3
uv run python main.py run graph-rag --questions 3
uv run python main.py run hybrid-rag --questions 3
uv run python main.py run knowledge-enhanced-rag --questions 3
uv run python main.py run memory-augmented-rag --questions 3
uv run python main.py run self-rag --questions 3
```

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

Executar somente as próximas 10 perguntas ainda não concluídas:

```bash
uv run python main.py run self-rag --questions 10
```

Executar dois ou mais RAGs, na ordem informada:

```bash
uv run python main.py run context-rag hybrid-rag self-rag
```

Executar um lote de 15 perguntas em cada um dos RAGs selecionados:

```bash
uv run python main.py run context-rag hybrid-rag self-rag --questions 15
```

Executar ou retomar os seis RAGs sequencialmente, cada um em seu ambiente isolado:

```bash
uv run python main.py run all
```

Executar as próximas 5 perguntas de cada um dos seis RAGs:

```bash
uv run python main.py run all --questions 5
```

`--limit` é um alias de `--questions`. O limite é aplicado individualmente a cada RAG selecionado, e não dividido entre eles.

O comando anterior `uv run python main.py run-all` continua disponível como alias.

Um lote bem-sucedido retorna código `0`, mesmo que ainda existam perguntas pendentes por causa do limite escolhido. A execução retorna código `1` quando uma ou mais perguntas tentadas naquela rodada falham. Isso permite usar o comando em scripts e pipelines de CI sem tratar um lote parcial bem-sucedido como erro.

Selecionar OpenAI apenas para uma execução:

```bash
uv run python main.py run hybrid-rag --provider openai
```

## Fluxos de execução recomendados

Processar o dataset em lotes de 10 perguntas:

```bash
uv run python main.py run self-rag --questions 10
# Repita o mesmo comando até o resumo indicar 90 sucessos e 0 pendentes.
```

Retomar depois de falha, cancelamento ou reinicialização:

```bash
uv run python main.py run self-rag --questions 10
```

Não é necessário informar um deslocamento: o checkpoint ignora os sucessos, tenta primeiro as falhas e continua do próximo item pendente. Também não edite apenas o CSV para alterar o progresso; o estado oficial está no `checkpoint.json`.

Antes de uma comparação entre pipelines, mantenha iguais o modelo de geração, o modelo de embeddings, os documentos e as demais configurações do `.env`. A opção `--provider` altera o provedor do LLM somente naquela execução; o provedor de embeddings continua sendo controlado por `EMBEDDING_PROVIDER`.

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
├── docker-compose.yml       # Neo4j local opcional
├── pyproject.toml
├── uv.lock                 # somente dependências do orquestrador
├── .env.example
├── eval-dataset/
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

## Problemas comuns

- **Checkpoint incompatível:** acontece quando o conteúdo do dataset muda. Arquive o diretório de resultados do pipeline e inicie uma avaliação nova; não combine resultados de versões diferentes do dataset.
- **Chave ausente:** execute `uv run python main.py doctor` e confira o `.env` da raiz. Os seis RAGs compartilham esse arquivo.
- **Neo4j em Docker:** como os benchmarks rodam no host pela CLI, use `NEO4J_URI=bolt://127.0.0.1:7687` no `.env`.
- **Neo4j Aura:** use a URI `neo4j+s://...` fornecida pela instância e as credenciais correspondentes. Não misture a senha do banco local persistido no volume com a senha da instância Aura.
- **Modelo de embeddings alterado:** use outro `CHROMA_PERSIST_DIR` ou recrie conscientemente o índice; modelos com dimensões distintas não devem compartilhar a mesma coleção.

## Testes de desenvolvimento

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check main.py benchmark_runner.py tests
```

Os testes cobrem seleção de um, vários ou todos os RAGs, validação do limite, execução incremental sem duplicatas, prioridade de retomada das falhas e atualização do `errors.json` após uma tentativa bem-sucedida.

## Licença

Nenhuma licença de software foi declarada até o momento. Até que um arquivo `LICENSE` seja adicionado, permanecem reservados todos os direitos sobre o código. Os documentos usados como corpus mantêm as licenças e os direitos de seus respectivos autores e não fazem parte da distribuição deste monorepo.
