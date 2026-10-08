# Implantação e recuperação

[English](README.en.md) · [Configuração única](../docs/configuracao.md) · [Operação](../docs/confiabilidade.md)

## Preparação sem execução paga

1. Registrar commit, alterações locais, datasets, resultados, corpus, índices e
   estado dos serviços da VPS. Preservar `.env` sem exibir seu conteúdo.
2. Fazer backup consistente. Para Chroma, nenhum worker pode estar usando o índice.
   Para Neo4j, usar backup/dump compatível com a versão instalada e restauração em
   banco separado; copiar um volume ativo não comprova um backup válido.
3. Transferir código validado e PDFs sem substituir resultados, `.env`, bancos ou
   índices históricos. Sincronizar raiz e seis ambientes com
   `uv sync --locked --python 3.12`; usar `--project rags/NOME` em cada ambiente.
4. Executar `main.py preflight` e as suítes descritas no guia. Corrigir bloqueios.
5. Criar usuários de sistema `benchmark` e `benchmark-notifier`, ambos com grupo
   `benchmark`. O segundo não pode ler credenciais ou checkpoints. Dar escrita ao
   primeiro em resultados, índices e `/var/lib/benchmark`. O código implantado deve
   ser legível pelos serviços, mas administrado separadamente dos seus usuários.
6. Completar somente `/logibot/benchmarking-rags-dataset/.env`, com proprietário
   administrativo e modo `0600`, seguindo o guia de configuração. Não criar
   `worker.env`, `control.env`, `telegram.env` ou `profiles.json`. O modo padrão
   `full` cobre os seis RAGs; Knowledge usa `required`, com PDFs + Neo4j.
7. Criar diretórios de resultados com grupo benchmark e travessia `0750`. Arquivos
   públicos usam `0640`; checkpoints e diários financeiros usam `0600`. StateDirectory
   das unidades prepara `/var/lib/benchmark` e `/var/lib/benchmark-notifier`.
8. Instalar `control.service` como `benchmark-control.service` e `telegram.service`
   como `benchmark-telegram.service` durante a conexão. `systemctl daemon-reload`
   lê as unidades, sem criar lotes. Reiniciar os serviços após editar a `.env`.

Os exemplos usam caminhos absolutos da VPS informada. Ajustar se a instalação for
outra. O cache UV usa `/var/lib/benchmark/uv-cache`; ambientes devem estar preparados
antes de iniciar o serviço. `service_entrypoint.py` seleciona as variáveis da função
antes de iniciar o processo final. O controle recebe IDs humanos, sem token do bot;
o notificador recebe token/destino, sem chaves dos modelos ou Neo4j. O systemd lê
`EnvironmentFile` antes de aplicar o bloqueio de leitura da `.env` pelos serviços.
As versões de python-dotenv dos locks reconhecem `PYTHON_DOTENV_DISABLED`, usado
pelo inicializador para impedir novas leituras do arquivo no processo filho.

`benchmark.service` é uma alternativa de lote único. Exige `BENCHMARK_PROJECT` e
`BENCHMARK_QUESTION_LIMIT` na mesma `.env`. Não iniciar dois supervisores para o
mesmo trabalho. O fluxo usual pelo Telegram usa somente os serviços de controle e
notificação; não exige essas duas variáveis de lote único.

## Conexão ao Telegram

1. O bot e o canal privado já existem. Preencher `TELEGRAM_BOT_TOKEN` com o token
   completo do BotFather e `TELEGRAM_RESULTS_CHAT_ID` com o ID do canal, incluindo
   sinal negativo. O ID numérico do bot não substitui esses valores.
2. Permitir publicação pelo bot como administrador do canal. Iniciar uma conversa
   privada com o bot e preencher `TELEGRAM_ALLOWED_USER_IDS` com IDs humanos.
3. Habilitar `TELEGRAM_ENABLED` e `TELEGRAM_CONTROL_ENABLED`. Manter
   `BENCHMARK_REMOTE_ENABLED=false` durante publicação fictícia e `/status`.
4. Testar reconexão e acesso negado a outro usuário. Apenas um processo deve
   consumir getUpdates com o token; remover eventual webhook antes de usar polling.
5. Conferir modelos, sete PDFs, grafo Neo4j, contabilização e limites técnicos.
   Não há teto em dólares obrigatório nem reserva financeira exigida sem um teto.
6. Somente depois habilitar `BENCHMARK_REMOTE_ENABLED=true`, reiniciar o controle
   e enviar, na conversa privada com o bot, `/executar context-rag 1`. Esse comando
   pode consumir modelos desde a preparação. Acompanhar progresso no canal.
7. Observar `/status` e o diário financeiro antes de ampliar o lote. `/retomar`
   exige configuração compatível e o ID completo do experimento.

Não houve implantação, conexão real ao Telegram ou chamada paga nesta revisão.
Alterar a `.env` local não atualiza a cópia da VPS automaticamente.

## Recuperação

Uma reinicialização do controle marca lotes pendentes/em execução como interrompidos.
Não há reinício pago automático. Conferir estado e consumo, reconciliar quando
possível e enviar uma nova solicitação explícita de retomada. Uma chamada cobrada
sem resposta salva pode precisar de investigação e não deve ser tratada como gratuita.

Testar desligamento do serviço durante um lote simulado, restauração em diretório
isolado e reinício do notificador. Medir CPU/RAM/tempo na VPS antes de ampliar lotes.
Para rollback, parar admissão de novos lotes e usar uma cópia separada do código
anterior; não misturar checkpoints antigos com experimentos novos.
