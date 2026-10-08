# Confiabilidade e operação do benchmark

[English](reliability.en.md)

## Escopo e estado

O projeto executa seis arquiteturas sobre o dataset canônico de 90 perguntas.
O controle remoto usa exclusivamente Python, uma interface local por socket Unix
e a Telegram Bot API. Não existe integração com agentes administrativos externos.

A ordem acordada é concluir código e verificações sem consumo, conectar o Telegram
e somente então iniciar os testes na VPS pelo bot. O operador retirou o teto
monetário do projeto e informou que já possui bot e canal privado. Faltam configurar
o token, o ID do canal e os IDs dos usuários autorizados na `.env` única da raiz.
Nenhuma chamada paga foi feita nesta etapa. Consulte o [guia de configuração](configuracao.md).

## Preservação e identidade

Os resultados e datasets históricos permanecem nos caminhos anteriores. Não
editar esses arquivos para acomodar o novo runner. `main.py migrate SOURCE DEST`
é uma simulação; `--apply` cria um arquivo separado, preservando o original.
Resultados legados sem textos dos contextos não podem ser reavaliados como se
as evidências originais estivessem disponíveis.

Novas saídas ficam em `resultados/<rag>/<experiment_id>/`. O manifesto identifica
código, bibliotecas, dataset, corpus, modelos, parâmetros, política de evidência,
índice e modo de execução. Correções metodológicas criam outros experimentos.
`/retomar` exige o ID completo e rejeita configuração divergente antes de chamadas.

Checkpoint, exportações e manifestos são escritos por substituição atômica com
fsync. A resposta e suas evidências são salvas antes do juiz; cada métrica é salva
individualmente. Sucessos compatíveis são reutilizados. Eventos históricos não
são apagados quando uma falha é recuperada. Índices anteriores não são excluídos.

## Dados, evidências e índices

O Knowledge utiliza os mesmos sete PDFs das demais arquiteturas.
`python scripts/prepare_corpus.py` prepara essa cópia com verificação de hash, sem
sobrescrever um corpus diferente. Uma instalação
nova precisa transferir esses PDFs: eles não devem ser presumidos presentes por
um simples clone. `main.py preflight` verifica os seis ambientes e extrai texto
dos PDFs localmente, sem chamar modelos.

A recuperação remove documentos de texto idêntico e seleciona documentos inteiros
até `BENCHMARK_CONTEXT_MAX_BYTES` (32000). Ordem e metadados dos documentos mantidos
são preservados. Evidências efetivamente fornecidas às ferramentas/agentes são
salvas. `BENCHMARK_INPUT_MAX_BYTES` (128000) rejeita requisições serializadas maiores.
Esses limites são em bytes UTF-8, não uma contagem exata do tokenizer de cada modelo.
A reserva de tokens usa bytes das entradas serializadas mais o limite de saída;
é uma estimativa conservadora, registrada como estimativa.

Chroma usa manifestos e IDs determinísticos para retomar chunks faltantes. O
manifesto registra a dimensão observada; divergência em dimensão, corpus ou IDs
interrompe a reutilização. `EMBEDDING_DIMENSIONS`, quando configurado, é enviado ao
provedor e comparado com o índice existente. A disponibilidade desse parâmetro
precisa ser validada no piloto do modelo escolhido. Índices legados sem manifesto
não são adotados automaticamente; uma nova ingestão pode consumir embeddings.

O Graph conserva a extração dos mesmos vinte primeiros chunks por identidade.
Isso não transforma esta arquitetura em Microsoft GraphRAG. O Self recebe as
evidências na autocrítica, normaliza SIM/NAO/NÃO e permite um refinamento. Memória
entre perguntas independentes permanece desativada/isolada.

## Knowledge e Neo4j

A configuração padrão é `BENCHMARK_KG_MODE=required`: os mesmos sete PDFs em
`rags/knowledge-enhanced-rag/data/apostilas/`, mais o grafo curado no Neo4j. Esse
caminho de PDFs já existia no pipeline. Não é um dataset de avaliação adicional.
O arquivo novo `data/knowledge-graph.json`, na raiz, é uma alternativa inativa:
não foi extraído dos PDFs, não substitui os livros e não é selecionado pelos serviços.
O grafo pedagógico curado já fazia parte da arquitetura original; criar um grafo
a partir dos PDFs seria outra alteração metodológica e não foi feito aqui.

`BENCHMARK_KG_MODE` aceita:

- `snapshot`: usa `BENCHMARK_KG_SNAPSHOT`, sem conexão com Neo4j. O arquivo
  `data/knowledge-graph.json` representa o grafo curado definido no código original,
  com 14 conceitos, relações e hash verificável; não é um backup de uma VPS.
  A cópia carregada permanece imutável durante o caso.
- `required`: exige Neo4j acessível e populado, com identidade conferida antes de
  cada pergunta. Escritas concorrentes ainda exigem isolamento operacional.
- `disabled`: ausência de KG explicitamente registrada, como outro experimento.

Reconstruir o grafo no Neo4j fica bloqueado por padrão. Após backup consistente e
isolamento do banco, o operador pode definir `BENCHMARK_ALLOW_KG_REBUILD=true`.
A rotina histórica de reconstrução substitui nós `Conceito`; não executá-la em
um banco compartilhado sem conhecer esse efeito.

## Orçamento, credenciais e tentativas

Preparação, geração, embeddings e cada métrica têm chamadas registradas em
`usage.jsonl`. O arquivo financeiro compartilhado é `budget.jsonl` sob
`BENCHMARK_BUDGET_DIR`; use o mesmo diretório em todos os serviços/raízes de saída.
O bloqueio nesse diretório admite um worker por vez. Outros consumidores da chave
fora deste projeto não são controlados por esse bloqueio.

| Variável | Padrão | Significado |
| --- | ---: | --- |
| `BENCHMARK_MAX_CALLS` | 200 | Chamadas por lote, incluindo retries |
| `BENCHMARK_MAX_QUESTION_CALLS` | 60 | Chamadas por pergunta |
| `BENCHMARK_MAX_PREPARATION_CALLS` | 100 | Chamadas de preparação |
| `BENCHMARK_MAX_TOKENS` | 1000000 | Tokens conhecidos e reservas por lote |
| `BENCHMARK_MAX_QUESTION_TOKENS` | 100000 | Tokens por pergunta, incluindo preparação |
| `BENCHMARK_MAX_COST_USD` | 0 | Custo por lote |
| `BENCHMARK_MAX_QUESTION_COST_USD` | 0 | Custo por pergunta |
| `BENCHMARK_MAX_PREPARATION_COST_USD` | 0 | Custo de preparação |
| `BENCHMARK_MAX_DAILY_COST_USD` | 0 | Custo no dia UTC de início da chamada |
| `BENCHMARK_MAX_PERIOD_COST_USD` | 0 | Custo acumulado de todo o diretório financeiro |
| `BENCHMARK_RESERVE_COST_USD` | 0 | Estimativa reservada por chamada em andamento |
| `BENCHMARK_MAX_SECONDS` | 3600 | Prazo do lote |
| `BENCHMARK_QUESTION_TIMEOUT_SECONDS` | 900 | Prazo por pergunta |
| `BENCHMARK_MAX_STAGE_ATTEMPTS` | 3 | Falhas máximas por etapa entre rodadas |
| `BENCHMARK_RETRY_COOLDOWN_SECONDS` | 60 nos pipelines | Espera antes de retentar o caso |

Zero desativa um limite monetário; não significa gratuidade. Com qualquer limite
monetário ativo, chamadas HTTP exigem uma reserva positiva. A reserva não é uma
tarifa consultada nem um teto exato de cobrança. Custo ausente bloqueia novas
chamadas monetariamente limitadas. Use também limites da chave/conta do provedor.
Não zere nem remova o diário financeiro para retomar um piloto esgotado.

Não há teto em dólares na configuração entregue; as variáveis monetárias são
opcionais e assumem zero quando ausentes. Execução remota não exige orçamento no
comando. Limites técnicos de chamadas, tokens, tempo e tentativas permanecem ativos,
assim como a contabilização. Se um teto opcional for configurado, omitir USD no
comando não o contorna; uma reserva positiva passa a ser necessária. Uma resposta
pode custar mais que essa estimativa.

Credenciais opcionais por função: `OPENROUTER_GENERATION_API_KEY`,
`OPENROUTER_JUDGE_API_KEY`, `OPENROUTER_EMBEDDING_API_KEY` e
`OPENROUTER_JUDGE_EMBEDDING_API_KEY`. Equivalentes `OPENAI_*` também são aceitos.
Na ausência da chave específica, usa-se a chave geral; embeddings do juiz preferem
a chave do juiz. `BENCHMARK_CREDIT_SCOPE=judge` bloqueia geração/preparação.
O inicializador dos serviços usa a `.env` única e inicia um novo processo com
apenas as variáveis da função. O notificador recusa credenciais de modelos. As
unidades systemd tornam a `.env` inacessível dentro dos serviços após sua leitura
pelo gerenciador; nenhum arquivo de credenciais derivado precisa ser mantido.

SDK retries ficam desativados. HTTP 429/503 respeitam Retry-After com limite;
RAGAs tem uma tentativa por padrão e recuperação de formato limitada.
Erros de credencial/configuração/crédito suspendem o lote. Timeout de rede não é
repetido automaticamente, pois pode ter ocorrido cobrança.

A solicitação de pausa também bloqueia a próxima chamada durante preparação,
geração ou avaliação, inclusive após SIGTERM. Uma chamada já enviada pode terminar.
O prazo limita admissão, esperas e timeouts HTTP; chamadas assíncronas têm deadline.
O worker supervisionado encerra o grupo de processos quando o prazo do lote ou da pergunta acaba,
com SIGTERM e tolerância de 15 segundos antes de SIGKILL. Operações em andamento
podem terminar sem resposta salva: isso é consumo ambíguo, não execução gratuita.

## Reconciliação e respostas congeladas

```bash
uv run --locked python main.py reconcile /var/lib/benchmark/budget
uv run --locked python main.py reconcile /var/lib/benchmark/budget --apply
uv run --locked python main.py export-frozen resultados/RAG/EXPERIMENTO /caminho/respostas.json
uv run --locked python main.py run context-rag --mode evaluate --frozen /caminho/respostas.json --questions 1
uv run --locked python main.py report resultados
```

Reconciliação é simulação sem `--apply`. `BENCHMARK_RECONCILE_ON_START=true` também tenta a reconciliação antes de um lote.
A aplicação consulta metadados de gerações
identificáveis no OpenRouter e acrescenta um evento; não gera conteúdo nem repete
a pergunta. Sem ID recuperável, o consumo permanece desconhecido. Não se inventa
uma cobrança zero. A consulta usa a credencial da função registrada.

A exportação exige checkpoint v2 de modo `full`, identidade íntegra e evidências salvas. O modo
`evaluate` valida perguntas, referências e proveniência e proíbe geração/preparação.
O dataset de referência nunca é usado como resposta candidata automática.

## Controle remoto e Telegram

`benchmark_control.py` mantém uma fila SQLite privada e atende somente no socket
Unix configurado. O grupo do socket é a fronteira local de acesso. O notificador
usa outro usuário, sem leitura dos checkpoints ou das credenciais do worker.
IDs numéricos são validados tanto no gateway quanto no serviço de controle.
Comandos são aceitos somente na conversa privada com o bot. O canal privado recebe
publicações; mensagens de canal/grupo não autorizam execuções. Texto livre e shell
são recusados. `TELEGRAM_RESULTS_CHAT_ID` recebe o ID do canal, nunca o ID do bot.

| Comando | Efeito |
| --- | --- |
| `/status` | Lista lotes e experimentos recentes |
| `/status RAG EXP` | Mostra o estado salvo |
| `/executar RAG N [USD]` | Enfileira até N tentativas; USD é opcional |
| `/retomar RAG EXP N [USD]` | Retoma o experimento compatível; USD é opcional |
| `/pausar RAG EXP` | Solicita pausa cooperativa |
| `/falhas RAG EXP` | Mostra falhas salvas |
| `/pergunta RAG EXP Q001` | Mostra estado e métricas do caso, sem textos privados |
| `/resultado RAG EXP` | Envia JSON público de notas |

EXP é o hash completo de 64 caracteres. Botões oferecem status, pausa e resultados.
Os serviços usam a `.env` da raiz, com `BENCHMARK_MODE=full` para os seis RAGs.
Não é necessário manter `profiles.json`. A CLI de controle ainda aceita `--profiles`
para configurações avançadas explícitas. O modo `evaluate` pela `.env` exige
`BENCHMARK_PROJECT` e `BENCHMARK_FROZEN_FILE`. Comandos Telegram não escolhem
caminhos arbitrários ou credenciais. Exemplo sem teto: `/executar context-rag 1`.

O `command_id` deriva do update do Telegram e é persistido junto com a criação do
lote. Repetição do mesmo comando não cria outro lote. Depois de reiniciar o serviço,
lotes antes pendentes/em execução ficam interrompidos e exigem nova solicitação.
Um único processo deve consumir getUpdates para o token.

Eventos públicos de início, falha e término ficam persistidos mesmo quando o
notificador está desconectado. A outbox persiste mensagens/CSV/JSON e aguarda retry_after. Falha entre entrega e
confirmação pode duplicar uma mensagem; nunca inicia outro benchmark. Publicação
inclui cobertura por métrica, custo conhecido/desconhecido, etapa, duração e alertas.
Não são enviados prompts, respostas, contextos, chaves ou traceback bruto.

## Implantação e validação

Consulte [deployment/README.md](../deployment/README.md) e os exemplos de serviço.
Não habilitar execução remota antes de preparar ambientes, verificar backups,
configurar a `.env`, limites técnicos e destino do bot. Os exemplos não instalam serviços.

```bash
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/verify_integrations.py
uv run --locked python main.py preflight
uv run --locked ruff check --exclude '*.ipynb' .
uv run --locked ruff format --check --exclude '*.ipynb' --exclude '*.md' .
git diff --check
```

A matriz [aceite.md](aceite.md) distingue testes automatizados e homologação real.
Os testes simulam modelos/HTTP, mantendo RAGAs, LangChain e Chroma reais nos seis
ambientes. Não comprovam qualidade das notas ou disponibilidade do provedor.
Notebooks históricos não são entrypoints suportados do benchmark.

Fontes das interfaces: [Telegram Bot API](https://core.telegram.org/bots/api) e
[metadados de geração OpenRouter](https://openrouter.ai/docs/api/api-reference/generations/get-request-&-usage-metadata-for-a-generation).
