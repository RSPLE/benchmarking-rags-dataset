# Matriz de aceite

[English](acceptance.md) · [Operação](reliability.pt-BR.md)

Aprovação automatizada significa verificação local sem chamadas pagas. Homologação
significa execução com serviços reais. Não são equivalentes. A autenticação e as permissões do Telegram foram verificadas por consultas reais
de leitura. Entrega de mensagens e piloto continuam pendentes da implantação.

| Caso | Evidência automatizada | Limite / homologação restante |
| --- | --- | --- |
| T01 | `test_resume_only_failed_metric_and_never_regenerate_saved_answer` | Juiz simulado |
| T02 | Mesmo teste; checkpoint independente por métrica | RAGAs real com juiz falso nas integrações |
| T03 | `test_invalid_metrics_cannot_be_successful` | Zero, ausências e valores inválidos |
| T04 | `test_lock_rejects_second_process_before_call` | Bloqueio real entre processos locais |
| T05 | `test_configuration_model_prompt_and_corpus_are_fingerprinted` | Alias externo ainda depende de versão/provedor fixados |
| T06 | `test_hard_process_exit_between_metrics_is_resumable` | Queda real de subprocesso local |
| T07 | `test_reconciliation_is_append_only_and_idempotent` | Metadados simulados; sem ID não há reconciliação automática |
| T08 | `test_authorization_and_credit_errors_stop_after_one_case` e teste HTTP de integração | Credenciais reais serão verificadas depois da conexão ao bot |
| T09 | `test_http_retry_and_budget_are_enforced_below_sdk` | HTTP simulado com cliente real |
| T10 | Cache de extração inválida, validação de resposta/métricas e quatro métricas com juiz falso | Truncamento/saída vazia do GLM real dependem do piloto |
| T11 | `test_real_chroma_resume_uses_no_additional_embeddings` | Chroma real, embeddings falsos |
| T12 | `test_unusable_pdfs_fail_before_embedding_calls` e preflight dos seis corpora | PDFs vazios, inválidos e sem texto; sete livros reais por RAG |
| T13 | `test_observed_index_dimension_mismatch_stops_before_embedding` | Dimensão real de Chroma; Neo4j ao vivo não homologado |
| T14 | Testes de migração, corrupção e legado em `test_reliability.py` | Migração real dos históricos não foi aplicada |
| T15 | `test_pause_between_metrics_preserves_answer_and_first_metric` | Serviço da VPS ainda não ativado |
| T16 | Reservas, consumo desconhecido, período compartilhado e virada UTC em `test_operations.py` | Reserva financeira é estimativa, não garantia da cobrança final |
| T17 | Idempotência da fila e resposta perdida no gateway | Reinício não reenfileira trabalho pago |
| T18 | Autorização e injeção em `test_operations.py` | Socket local real também testado |
| T19 | `test_delivery_failure_restart_and_deduplication` | Telegram simulado; testar rate limit real posteriormente |
| T20 | Evidência/memória nas seis integrações e watchdog do supervisor | Carga e limites dos modelos reais dependem do piloto |
| T21 | Testes originais de CSV/erros e histórico persistente | Publicação consome somente artefatos públicos |
| T22 | `test_scientific_repetition_has_an_independent_identity` | Estabilidade estatística do juiz ainda não medida |
| T23 | Gateway/notificador sem clientes de modelo e serviço sem chaves de modelo | Nenhuma integração com agente externo |
| T24 | `test_gateway_filters_chat_scope_and_persists_offset` e socket real | Bot, canal e permissão confirmados pela Bot API; entrega real ainda não testada |
| T25 | `test_evaluation_mode_never_prepares_a_pipeline` e exportação/importação de evidências | Conjunto congelado real completo ainda depende das gerações |
| T26 | `test_frozen_answer_missing_evidence_never_uses_reference` | Contextos históricos ausentes não são reconstruídos artificialmente |
| T27 | Hashes de prompts upstream e CSV de nove colunas para os seis RAGs | CSV original e bytes enviados pelo Telegram coincidem |
| T28 | `test_knowledge_shared_history_survives_restart_and_metric_retry` | Histórico dos cinco últimos pares persistido sem regenerar respostas |
| T29 | Teste integrado de documentos longos e duplicados nos seis ambientes | Sem deduplicação global ou corte em bytes |
| T30 | Flags CLI/bot, fila atômica e cancelamento após falha | Seis lotes sequenciais; sem execução de shell |
| T31 | Gráfico com CSV original, exportação de consumo e checagem de canal | Métrica ausente não vira zero; tokens da resposta são preservados |

## Dados protegidos e auditoria

Os hashes dos 25 arquivos conferidos nesta etapa (datasets, resultados históricos
e locks fora de ambientes virtuais) permaneceram iguais. Os seis corpora locais
contêm os mesmos livros: sete PDFs e 2604 páginas por arquitetura. São seis
arquiteturas, totalizando 42 cópias locais de PDFs, não 42 livros diferentes.

O corpus do Knowledge foi preparado a partir do Context e conferido por hash.
O procedimento reproduzível é `python -m app.tools.prepare_corpus`; ele recusa
sobrescrever arquivos diferentes. Os comentários de código nos notebooks foram
removidos preservando as saídas já salvas; cópias anteriores ficaram no backup
local desta etapa. Notebooks continuam sendo material histórico, não entrypoints.

A VPS foi consultada por SSH: repositório no commit
`84fb661147a6dc68b97a6c50f3b2cd40b9fd7a0c`, sem mudanças reportadas pelo Git e sem
contêiner listado por Docker Compose naquele momento. Código, serviços e bancos
remotos não foram alterados nesta etapa. Esse registro não substitui auditoria e
backup consistentes imediatamente antes da implantação.

## Condições para encerrar a homologação

- Transferir a `.env` local já configurada, preservando os endereços próprios da VPS; revalidar com `telegram-check`.
- Implantar código, ambientes e serviços. A configuração atual admite comandos remotos, mas não inicia lotes automaticamente.
- Verificar permissões, entrega fictícia, acesso negado e reinício da outbox.
- Conferir contabilização e limites técnicos; o operador retirou o teto monetário.
- Autorizar pelo bot um lote pequeno e medir preparação, geração, avaliação e falhas.
- Conferir manualmente evidências/notas; testar pausa e retomada reais.
- Somente depois ampliar o escopo dos lotes. O benchmark 6 × 90 não foi executado
  nesta etapa; a configuração da `.env` não dispara testes ou envios.

## Resultado da verificação local desta revisão

- Suíte raiz: 76 testes descobertos, 65 aprovados e 11 integrações puladas porque
  pertencem aos ambientes filhos. O teste com socket Unix real passou.
- Integrações: 11 testes descobertos em cada ambiente; 56 execuções aprovadas e
  10 puladas por serem específicas de Knowledge ou do ambiente de gráficos, sem chamadas pagas.
- Preflight: seis corpora aprovados, sete PDFs e 2604 páginas por corpus.
- Ruff, formatação e `git diff --check`: aprovados.
- Arquivos protegidos: 25 hashes conferidos, nenhuma divergência.
- Configuração única: preservação dos valores anteriores, permissão `0600`, filtragem
  de credenciais por processo e início sem USD verificados.
- Sintaxe das três unidades systemd validada com caminhos locais; permissões e
  execução real dessas unidades ainda dependem da implantação na VPS.

Logs locais: `tmp/implementation-20261007/`. A execução dentro do sandbox havia
bloqueado o socket e o encerramento assíncrono; a validação conclusiva foi feita
fora do sandbox, mantendo modelos/HTTP simulados. Nenhum serviço da VPS foi ativado.

Protocolo original e diferenças declaradas: [compatibilidade](compatibility.pt-BR.md).
Logs atuais: `unit-paper.log` e `integrations-paper.log` no diretório local de logs.

## Validação da estrutura e do proxy — 8 de outubro de 2026

- `app/` reúne benchmark, dashboard, RAGs, providers, Telegram, serviços, ferramentas,
  implantação, documentação, scripts e testes. Os READMEs principais estão em inglês.
- Suíte geral: 101 descobertos, 90 aprovados e 11 integrações ignoradas no ambiente
  leve da raiz. Essas integrações passaram nos seis ambientes RAG com lockfiles,
  usando respostas simuladas e bibliotecas reais de RAGAS/Chroma.
- Suíte HTTP/proxy: 10 aprovados em Compose isolado, incluindo WebSocket autenticado,
  bloqueio sem login, logout e persistência de sessão.
- Builds de dashboard/auth, executor e proxy concluídos. Auth, dashboard, monitor,
  control e proxy ficaram saudáveis na instância isolada. Nenhum benchmark foi enviado.
- Navegador: login, recarga sem logout, formulário de execução, gráfico original
  do CSV e downloads PNG/EPS validados, sem erros observados no console.
- Preservação: 33 arquivos históricos/dados/locks registrados e o script de gráficos
  conservaram seus bytes; os mesmos sete hashes de PDFs coincidem nos seis RAGs.
- Testes de migração cobrem a mudança aprovada de código/caminho, rejeição de revisão
  desconhecida/configuração alterada, reutilização de respostas/métricas salvas,
  registro de procedência e importação histórica no SQLite sem duplicar IDs.

A falha local de WebSocket foi reproduzida com HTTP 403: o proxy em execução mantinha
uma configuração antiga do Caddy e enviava upgrade à rota HTTP de autenticação.
Copiar o Caddyfile da raiz para uma imagem construída localmente faz mudanças
entrarem em vigor com `docker compose up -d`. Após substituir somente o proxy local,
o login retornou 303, a página retornou 200 e o WebSocket autenticado conectou com
o subprotocolo `streamlit`.

Essas verificações não comprovam emissão pública de TLS, capacidade da VPS,
disponibilidade dos provedores ou notas científicas. Não houve chamadas pagas a
modelos nem reconstrução de grafo de produção nesta reorganização. Fontes
históricas inválidas/incompletas são preservadas e sinalizadas pelo dashboard.
