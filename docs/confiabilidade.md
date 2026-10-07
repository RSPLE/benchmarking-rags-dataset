# Confiabilidade do benchmark

Revisão local de 07/10/2026. Base: branch `develop`, commit
`84fb661147a6dc68b97a6c50f3b2cd40b9fd7a0c`. O relatório distingue resultados
históricos, testes de software e validação de modelos. Nenhum benchmark pago
foi executado durante esta implementação; nenhum resultado foi enviado ao Telegram.

## Estado encontrado e preservação

| Pipeline | Sucessos históricos | Falhas históricas | Sem registro | PDFs no caminho padrão |
| --- | ---: | ---: | ---: | ---: |
| Context | 89 | 1 | 0 | 7 |
| Graph | 21 | 69 | 0 | 7 |
| Hybrid | 0 | 1 | 89 | 7 |
| Knowledge | 0 | 0 | 90 | 0 |
| Memory | 0 | 0 | 90 | 7 |
| Self | 0 | 0 | 90 | 7 |

“Sem registro” refere-se a este checkout, não a outras máquinas. Os cinco corpora
presentes contêm os mesmos sete PDFs, com 2.604 páginas por corpus. Os sete arquivos
únicos foram abertos com PyPDF; todos contêm texto extraível. Isso não certifica
qualidade de OCR, tabelas ou ordem de leitura de cada página.

O dataset canônico de 90 perguntas e todos os arquivos históricos em `results/`
foram preservados byte a byte. Antes das alterações, foram copiados, com os scripts
raiz, para `tmp/audit-20261007/baseline.tar.gz`. O manifesto está em
`tmp/audit-20261007/sha256.json`; os 25 arquivos arquivados foram relidos e conferidos.
O arquivo `.env` não foi editado, copiado para o backup nem publicado. O backup é
local e não versionado. Não é um backup de bancos ativos nem da VPS.

O erro histórico do Context é de geração não concluída; o Graph inclui erros de
conexão, truncamento e limite de chave. O Hybrid registra uma interrupção, que não
prova defeito na arquitetura. Os sucessos históricos têm quatro notas válidas,
mas não têm os textos dos contextos usados na geração.

## Mudanças aplicadas

| Problema do plano | Implementação | Efeito |
| --- | --- | --- |
| Resposta perdida após falha do juiz | Checkpoint v2 grava `artifact` antes da avaliação | A retomada reutiliza resposta, referência, contextos ordenados e metadados |
| Avaliação monolítica | Uma chamada de `ragas.evaluate` por métrica | Uma métrica concluída não é avaliada de novo na retomada |
| Notas inválidas | Validação central de presença, número, finitude e faixa das quatro métricas | Zero é válido; `null`, booleano, texto, NaN, infinito e campos ausentes não viram sucesso |
| Experimentos misturados | Manifesto determinístico e diretório por RAG/identidade | Alteração de código, dataset, corpus, modelo, configuração ou lock cria outro experimento |
| Escritas concorrentes | `flock` por experimento e por raiz de execução | Preparação e avaliação ocorrem sob exclusão mútua |
| Arquivos parciais | Temporário exclusivo, fsync, rename atômico e fsync do diretório | Um checkpoint corrompido não provoca reset silencioso |
| Falhas apagadas após recuperação | Eventos append-only, além da visão atual `errors.json` | O histórico permanece mesmo quando o erro atual é resolvido |
| Juiz acoplado à geração | `OPENROUTER_JUDGE_MODEL` separado | Vazio mantém o modelo de geração efetivamente configurado; nenhum modelo é substituído automaticamente |
| Chamadas invisíveis | Registro no transporte HTTP síncrono e assíncrono | Embeddings, preparação, geração, reflexão e juiz recebem registros por chamada |
| Retries multiplicados | SDK sem retry, RAGAs com uma tentativa por padrão, transporte limitado | 429/503 usam Retry-After ou backoff; correção de formato do RAGAs limitada a uma recuperação por prompt |
| Erros sistemáticos | Pausa em autenticação/configuração/crédito/orçamento e após três erros consecutivos da mesma categoria | Evita repetir um erro de configuração por todo o dataset |
| Indexação incompleta | Manifesto, IDs determinísticos, verificação do conjunto de chunks | Retoma chunks faltantes; rejeita índice legado não verificado e corpus/configuração divergentes |
| Grafo reconstruído | Extrações persistidas por identidade, com validação do JSON e hash do resultado | Reutiliza as extrações dos mesmos vinte primeiros chunks |
| Evidências divergentes | Graph e Memory usam mensagens reais das ferramentas | Não executam busca posterior para fabricar contextos de avaliação |
| Memória entre casos | Knowledge usa `session_id=None` no benchmark | Recupera uma vez e não mantém histórico entre perguntas independentes |
| KG silenciosamente ausente | `BENCHMARK_KG_MODE=required` ou `disabled` explícito | Modo requerido valida disponibilidade, população e hash do conteúdo; mudança detectada antes de cada caso |
| Autocrítica sem evidência | Self recebe o contexto e aceita somente SIM/NAO/NÃO normalizados | Máximo de um refinamento; saída ambígua é erro explícito |
| Orçamento exclusivo do juiz | Entrada separada `evaluate_saved.py` | Não importa os pipelines nem prepara índices/agentes no modo de avaliação |
| Telegram dependente de LLM | Notificador stdlib com outbox SQLite | Mensagens e CSV de notas sem Hermes ou credenciais de modelos |

A validação usa [0,1] para faithfulness, context_precision e context_recall,
e [-1,1] para answer_relevancy, com tolerância numérica de 1e-9 sem alterar a nota.
A faixa de relevância decorre da similaridade de cosseno, conforme a
[definição oficial da métrica](https://docs.ragas.io/en/v0.2.2/concepts/metrics/available_metrics/answer_relevance/)
e a implementação instalada. Notas negativas válidas não são apagadas.

Os seis projetos UV e seus locks foram mantidos. A CLI existente continua aceitando
`run`, vários RAGs, `run all`, `run-all`, `--questions` e `--limit`.
`--questions` continua limitando tentativas por RAG, não sucessos. A execução de
vários RAGs agora para no primeiro processo que retorna falha.

Comentários e docstrings explicativas foram removidos dos scripts Python. As
descrições exigidas pelas ferramentas LangChain são argumentos do schema da
ferramenta. Prompts continuam em português, pois são parte do método experimental.
Notebooks históricos foram preservados; contêm código anterior e não são a entrada
validada de execução.

## Identidade, saídas e histórico

As novas execuções gravam em:

```text
resultados/<rag>/<experiment_id>/
  manifest.json
  checkpoint.json
  results.csv
  errors.json
  events.jsonl
  usage.jsonl
  summary.json
  public_results.json
```

`BENCHMARK_OUTPUT_DIR` altera a raiz. Caminhos relativos são resolvidos a partir da
raiz do repositório pela CLI, e o runner adiciona o RAG e o identificador. Os arquivos
antigos `rags/<rag>/results/*` permanecem disponíveis para leitura.

O manifesto inclui hashes do código compartilhado e específico do RAG, lock do
ambiente, parâmetros de modelos, dataset, PDFs, modo, política de evidências e
entrada congelada. Para Knowledge com KG requerido, inclui o hash dos conceitos
e relações do Neo4j. Credenciais não entram no manifesto. Os parâmetros fixos e
prompts estão cobertos pelos hashes do código. Os artefatos de resposta possuem
hash próprio; a métrica registra o hash das entradas avaliadas.

Uma correção metodológica não continua o experimento antigo. Em particular, corrigir
as evidências do Graph/Memory ou a memória do Knowledge muda o objeto avaliado.
Uma execução nova pode gerar casos que já existiam no histórico, em outro experimento.
Isso é explícito no diretório e manifesto, e não deve ser somado à série antiga.
Não há reutilização automática de respostas entre arquiteturas.

`BENCHMARK_REPETITION` permite criar uma repetição identificada separadamente,
mesmo com os mesmos demais parâmetros. Não há cache global de métricas; a retomada
reutiliza somente resultados já persistidos no mesmo experimento.

`results.csv` mantém separador `;`, BOM UTF-8, campos do dataset, resposta,
quantidade de contextos, métricas e colunas de uso da geração. Contém somente casos
completos. `summary.json` calcula a média por métrica com seu denominador, incluindo
métricas válidas de casos ainda parciais; por isso seu denominador pode ser maior
que o número de linhas do CSV. `partial` é um subconjunto dos casos não concluídos.
`errors.json` é uma visão das falhas atuais; `events.jsonl` preserva as anteriores.

### Migração explícita

```bash
uv run --locked python main.py migrate \
  rags/context-rag/results/checkpoint.json \
  resultados-legados/context-rag
```

O comando acima apenas simula. Acrescente `--apply` para criar uma cópia em destino
novo, o original arquivado, o checkpoint v2 marcado como legado e o relatório da
migração. Não sobrescreve destinos existentes nem altera a origem.

O arquivo migrado é um arquivo histórico de leitura. Mantém valores e tentativas,
identifica métricas inválidas e marca configuração desconhecida. Não pode ser usado
pelo runner como experimento novo. `contexts_count` não permite reconstruir textos;
sem um trace/snapshot confiável não existe reavaliação idêntica da evidência antiga.

## Operação local

Diagnóstico sem chamadas de modelos:

```bash
uv sync --locked --python 3.12
uv run --locked python main.py audit
uv run --locked python main.py doctor
uv run --locked python main.py status rags/context-rag/results
```

`audit` inventaria o dataset, corpora, locks e estados históricos sem ler segredos.
`doctor` verifica presença de configuração e PDFs; ausência de corpus causa retorno
não zero. Nenhum dos dois certifica validade da chave ou compatibilidade do modelo.
Knowledge precisa de PDFs em `data/apostilas` ou de `DOCS_DIR` explícito, além do KG
ou do modo vetorial explicitamente escolhido.

Exemplo de **lote pago**, para usar após definir orçamento e validar o piloto:

```bash
uv run --locked python main.py run context-rag \
  --questions 1 --max-calls 30 --provider openrouter
```

Os limites do exemplo não são uma estimativa de gasto nem uma recomendação de
configuração de tokens para GLM. O comando não foi executado nesta revisão.

Seleção de casos:

```bash
uv run --locked python main.py run graph-rag --selection pending --questions 1
uv run --locked python main.py run graph-rag --selection failed --questions 1
```

O padrão `unresolved` tenta falhas antes de pendências. A seleção `pending` permite
avançar sem ficar repetindo as mesmas falhas. Não há cooldown automático entre
rodadas; uma nova chamada da CLI é uma nova autorização de lote.

Pausa e liberação para retomada:

```bash
uv run --locked python main.py pause resultados/context-rag/IDENTIFICADOR
uv run --locked python main.py status resultados/context-rag/IDENTIFICADOR
uv run --locked python main.py clear-pause resultados/context-rag/IDENTIFICADOR
```

`clear-pause` só remove a solicitação. Execute novamente `run` com o limite desejado
para retomar. SIGTERM/SIGINT são cooperativos; uma chamada em andamento pode terminar
antes da pausa. Uma saída forçada deixa estado `running`; a próxima execução registra
a interrupção e aproveita somente as etapas efetivamente salvas. Uma requisição
cobrada, mas sem resposta local, pode ser repetida. Não há garantia de execução
exatamente uma vez em serviços externos.

### Avaliação de respostas congeladas

```bash
uv run --locked python main.py run context-rag \
  --mode evaluate --frozen /caminho/casos.json --questions 1 --max-calls 20
```

O JSON é uma lista de objetos como este:

```json
[
  {
    "experiment_id": "identidade-da-geracao",
    "rag": "context-rag",
    "question_id": "Q001",
    "question": "Texto exato da pergunta do dataset",
    "reference": "Texto exato da referência do dataset",
    "response": "Resposta efetivamente produzida pelo RAG",
    "retrieved_contexts": ["Evidência efetivamente usada"],
    "generation_fingerprint": "identidade-da-configuracao-de-geracao",
    "evidence_metadata": [{"source": "fonte-original"}]
  }
]
```

Os textos ilustrativos precisam ser substituídos por dados reais. O importador
valida IDs, duplicatas, RAG, pergunta, referência, campos, evidências não vazias e
origem comum. Aceita um subconjunto do dataset. Casos fora do arquivo permanecem
pendentes; não são gerados para completar o lote. Referências nunca são copiadas
como respostas candidatas. Embeddings internos de `answer_relevancy` continuam
sendo chamadas de avaliação e entram no consumo.

### Índices

O padrão dos novos pipelines é `rags/<rag>/chroma_v2`, com coleção própria. Os
índices anteriores não são apagados nem adotados sem verificação. Para mudar corpus,
chunker ou embeddings, escolha um novo `CHROMA_PERSIST_DIR`; uma divergência no
manifesto existente interrompe a ingestão. A leitura dos PDFs e o chunking continuam
ocorrendo na preparação para conferir o corpus; reutilização evita chamadas de
embeddings e extração, não toda a computação local.

## Orçamento e tentativas

| Variável | Padrão do código | Semântica |
| --- | ---: | --- |
| `BENCHMARK_MAX_CALLS` | 200 | Chamadas HTTP admitidas por processo/lote, incluindo retries |
| `BENCHMARK_MAX_QUESTION_CALLS` | 60 | Chamadas por pergunta, incluindo preparação sob demanda |
| `BENCHMARK_MAX_PREPARATION_CALLS` | 100 | Chamadas da etapa de preparação por lote |
| `BENCHMARK_MAX_TOKENS` | 1000000 | Tokens conhecidos por lote |
| `BENCHMARK_MAX_QUESTION_TOKENS` | 100000 | Tokens conhecidos por pergunta |
| `BENCHMARK_MAX_COST_USD` | 0 | Limite de custo conhecido por lote; zero desativa o limite monetário |
| `BENCHMARK_MAX_QUESTION_COST_USD` | 0 | Limite por pergunta; zero desativa |
| `BENCHMARK_MAX_DAILY_COST_USD` | 0 | Limite por dia UTC na mesma raiz de resultados; zero desativa |
| `BENCHMARK_MAX_SECONDS` | 3600 | Prazo para admitir chamadas no lote |
| `BENCHMARK_QUESTION_TIMEOUT_SECONDS` | 900 | Prazo para admitir chamadas por pergunta |
| `BENCHMARK_HTTP_ATTEMPTS` | 3 | Total máximo de tentativas HTTP para 429/503 |
| `BENCHMARK_RETRY_MAX_WAIT_SECONDS` | 60 | Retry-After maior que isso encerra a tentativa local |
| `RAGAS_MAX_ATTEMPTS` | 1 | Tentativas do wrapper RAGAs; manter 1 para centralizar retries |
| `RAGAS_MAX_WORKERS` | 1 | Concorrência interna da avaliação |
| `BENCHMARK_AGENT_RECURSION_LIMIT` | 12 | Limite de passos do agente Graph/Memory |

O limite diário agrega `usage.jsonl` dos experimentos da mesma raiz e considera
chamadas sem conclusão como custo desconhecido. Processos em outras raízes e outros
consumidores da chave não estão incluídos. O dia é UTC, explicitamente, e não o dia
civil do operador. Uma chamada iniciada antes da virada e terminada depois merece
reconciliação de período se houver uso contínuo na meia-noite.

Quando algum limite monetário ativo depende de consumo desconhecido, novas chamadas
são bloqueadas. Sem limite monetário configurado, o custo continua marcado como
desconhecido, enquanto os limites de chamadas e tempo seguem ativos. Limites de
custo/tokens são verificados antes da próxima chamada; uma resposta em andamento
pode ultrapassá-los. O prazo total controla admissão, não mata threads ou desfaz uma
requisição. O timeout HTTP continua específico por chamada. Use também o limite
financeiro da chave OpenRouter para proteção da conta.

HTTP 400/401/403/404/422 e crédito 402 suspendem o lote. O corpo sanitizado fica no
histórico para distinguir causas; um 402 não é automaticamente identificado como
saldo definitivamente esgotado. 429/503 recebem Retry-After/backoff limitado.
Falhas de conexão e timeout são registradas sem retry automático no transporte,
pois a cobrança pode ter ocorrido; podem ser retomadas em outra rodada limitada.
Não há reconciliação automática pelo endpoint `/generation` nesta versão.

O transporte conserva `usage`, ID, modelo efetivo, provedor, finish_reason e tempo.
A estrutura de uso segue a [documentação do OpenRouter](https://openrouter.ai/docs/cookbook/administration/usage-accounting).
Ausência de custo não vira zero. A política de tentativas do wrapper respeita a
[semântica de RunConfig do RAGAs 0.4.3](https://docs.ragas.io/en/v0.4.3/references/run_config/).
Os contadores antigos de tokens da geração permanecem no CSV por compatibilidade;
`usage.jsonl` é a fonte para distinguir consumo conhecido de desconhecido.

## Telegram e serviços

`telegram_notifier.py` usa apenas biblioteca padrão e módulos locais de persistência.
Lê `summary.json` e `public_results.json`, que contêm estado e notas; não precisa
abrir checkpoint com respostas, `.env`, agentes ou OpenRouter. Recusa iniciar se
receber credenciais conhecidas de modelos em seu ambiente.

O serviço publica resumos em texto e, opcionalmente, CSV apenas com IDs e métricas.
A implementação segue [sendMessage e sendDocument da Bot API](https://core.telegram.org/bots/api).
Tem outbox SQLite durável, identidade de notificação, confirmação local e retry_after.
Uma falha entre envio e confirmação pode duplicar uma mensagem; nunca dispara
novamente o benchmark. A indisponibilidade do notificador não altera o runner.

Configure um bot e um canal fora deste repositório. Use somente as variáveis de
`deployment/telegram.env.example` no processo de comunicação. O exemplo começa
desativado. Não coloque a chave OpenRouter nesse arquivo. Após conferir o destino,
o comando operacional é:

```bash
python3 telegram_notifier.py /logibot/benchmarking-rags-dataset/resultados \
  --database /var/lib/benchmark-notifier/telegram.outbox.db
```

`--once` processa uma varredura/entrega e encerra. Sem a flag, o intervalo é de
15 segundos por padrão, com mínimo de 10. O processo deve ter escrita apenas na
própria outbox e leitura dos resultados públicos. O exemplo systemd separa os
usuários, nega acesso a arquivos de credenciais e usa o grupo `benchmark` para
leitura dos arquivos públicos, gravados com modo 0640. Diretórios precisam permitir
travessia desse grupo; o worker usa umask 0027. Checkpoints permanecem 0600.

`deployment/benchmark.service` é um modelo de lote único, sem reinício automático.
Ajuste usuários, caminhos e `/etc/benchmark/worker.env` antes de instalar. Esse arquivo
precisa de `BENCHMARK_PROJECT`, `BENCHMARK_QUESTION_LIMIT`, `BENCHMARK_MAX_CALLS` e da
configuração aplicável ao worker. Prepare os seis ambientes com `uv sync --locked`
antes do serviço. Não habilite boot automático para autorizar lotes pagos sem intenção.
Os serviços não foram instalados nem ativados nesta máquina ou na VPS.

Comandos privados do Telegram, execução remota de lotes, botões e Hermes não foram
habilitados. A camada entregue é um notificador independente; controle local é pela
CLI. Controle remoto pago exige uma fila de comandos com autorização por ID,
idempotência e orçamento aprovados, seguida de validação do piloto. O plano coloca
essa etapa depois do piloto; não há um shell remoto nem interpretação livre nesta
implementação. Hermes não é uma dependência.

## Verificação e limites remanescentes

Comandos reproduzíveis:

```bash
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/verify_integrations.py
uv run --locked ruff check --exclude '*.ipynb' .
uv run --locked ruff format --check --exclude '*.ipynb' --exclude '*.md' .
git diff --check
```

Resultado da verificação: 31 testes passaram no ambiente raiz; os seis testes de
integração foram executados em cada um dos seis ambientes, totalizando 36 execuções
aprovadas. No ambiente raiz esses seis aparecem como `skipped`, pois suas dependências
pertencem aos projetos filhos. Lint, formatação e `git diff --check` passaram. Os 21
arquivos históricos de dataset/resultados conferidos mantiveram seus hashes originais.

A suíte raiz mantém os dez testes originais e acrescenta retomada por métrica,
queda real de subprocesso entre métricas, concorrência, corrupção, identidade,
valores inválidos, pausa, seleção, migração, avaliação sem preparação, ingestão
parcial, extrações persistidas, normalização da autocrítica, consumo desconhecido,
limites diários e fila de entrega. Os testes de integração são pulados no ambiente
raiz leve e executados nos seis ambientes filhos pelo segundo comando.

Os testes dos ambientes usam RAGAs, LangChain e Chroma reais, com juiz, embeddings
e transporte HTTP falsos. Verificam as quatro métricas, imports sem preparação,
coleta de evidência e isolamento de memória, uso síncrono/assíncrono, escolha do
juiz, retries HTTP e bloqueio de orçamento abaixo do SDK. Nenhum teste precisa de
chave real ou faz solicitação paga. RAGAs 0.4.3 e LangChain emitem avisos de
obsolescência; migrar as APIs e alterar locks exigiria outro experimento e não foi
feito nesta correção.

Ainda dependem de ambiente real e de decisão operacional:

- Piloto pequeno com o GLM efetivamente configurado: aceitação de parâmetros,
  truncamento, estabilidade das notas, custo real e limites adequados.
- Disponibilizar o corpus do Knowledge e validar o banco Neo4j populado na VPS;
  não executar `/build-graph` em banco compartilhado sem backup, pois o endpoint
  histórico substitui nós `Conceito`.
- Backup consistente dos bancos e volumes ativos da VPS, restauração em cópia e
  validação dos serviços com os usuários/caminhos definitivos.
- Testar o bot e destino reais com dados fictícios antes de publicar resultados.
- Reconciliação automática de chamadas ambíguas, limites financeiros estritos com
  reserva estimada antes da chamada e controle remoto de lotes ainda são extensões.
- O hash do KG é conferido antes de cada caso, mas alterações concorrentes no banco
  durante uma consulta exigem banco imutável ou snapshot para isolamento científico
  completo. Fixe também versões reais de modelos; aliases externos podem mudar.

Testes de software reduzem riscos demonstrados; não comprovam disponibilidade de
serviços, qualidade científica das notas nem ausência absoluta de falhas futuras.
