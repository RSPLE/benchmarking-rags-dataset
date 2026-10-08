# Configuração única e canal privado

[English](configuration.en.md) · [Operação](confiabilidade.md) · [Implantação](../deployment/README.md)

Use somente a `.env` da raiz do repositório. A `.env.example` é um modelo sem
credenciais; não é outro arquivo a manter. Na máquina local, o arquivo real está em
`/home/ramon/Git/GitHub/LogiBots/benchmarking-rags-dataset/.env`. Na VPS, o caminho
das unidades de serviço é `/logibot/benchmarking-rags-dataset/.env`. Ao implantar,
preserve credenciais e endereços próprios da VPS; não substitua sua configuração
às cegas pela cópia local. Os antigos arquivos separados de Telegram/controle não
são mais necessários.

## Acesso web antes de subir

Preencha `DASHBOARD_USERNAME` e `DASHBOARD_PASSWORD` na mesma `.env`. Escolha uma
senha com pelo menos 12 caracteres. Depois execute somente `docker compose up -d`
e acesse <http://127.0.0.1:8501>. Não há usuário padrão nem cadastro posterior.
As imagens da aplicação são construídas localmente, sem perfis ou login em registro.
No SQLite, a senha é armazenada como hash; a `.env` permanece privada com modo
`0600`. Veja [acesso, persistência e troca de senha](../dashboard/README.md).

## Onde colocar os dados do Telegram

| Variável na `.env` | Valor necessário |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Token completo fornecido pelo BotFather, com a parte numérica e o segredo separados por `:`. O ID numérico do bot sozinho não autentica. |
| `TELEGRAM_RESULTS_CHAT_ID` | ID numérico do **canal privado**, incluindo o sinal negativo; normalmente começa com `-100`. Não usar o ID do bot, seu ID pessoal ou o link de convite. |
| `TELEGRAM_ALLOWED_USER_IDS` | Seu ID numérico de **usuário humano** autorizado a enviar comandos ao bot. Para vários usuários, separar por vírgulas. Não usar ID do canal, ID do bot ou `@username`. |

Apesar do nome `CHAT_ID`, o campo aceita um canal: na
[Telegram Bot API](https://core.telegram.org/bots/api#sendmessage), `chat_id` é o
identificador do destino da mensagem. Adicione o bot como administrador do canal
com permissão de publicar mensagens. Ser membro do canal não autoriza controlar
o benchmark; essa autorização depende de `TELEGRAM_ALLOWED_USER_IDS`.

O canal recebe progresso, notas e arquivos públicos CSV/JSON. Para iniciar, pausar
ou consultar testes, abra a conversa **privada com o bot** usando sua conta pessoal.
Posts feitos no canal não são aceitos como comandos. Isso permite validar quem
está solicitando uma execução.

Os três campos já estão preenchidos na `.env` local real. A autenticação do bot,
o canal e a permissão de publicar foram conferidos pela Bot API. Credenciais de
modelos e Neo4j foram preservadas. Não publicar tokens em documentação, commits,
URLs de navegador ou comandos de terminal.

## Ativação e diagnóstico

A configuração local real está com `TELEGRAM_ENABLED=true`,
`TELEGRAM_CONTROL_ENABLED=true` e `BENCHMARK_REMOTE_ENABLED=true`. O arquivo de
exemplo mantém essas opções desligadas. Configurar não inicia serviços ou lotes.
Após implantar os serviços, apenas os IDs humanos cadastrados podem iniciar testes.

```bash
uv run --locked python main.py telegram-check
```

O comando valida credencial, tipo de canal, permissão de publicação e ausência de
webhook sem enviar mensagens, consumir eventos ou chamar modelos. Reinicie os dois
serviços após editar a `.env`. `/start` no privado do bot mostra a ajuda;
`/executar context-rag --questions 1` inicia uma tentativa. `/status` mostra os IDs;
`/retomar context-rag EXP --questions 1` retoma um experimento compatível. Para
bloquear temporariamente novos lotes remotos, defina `BENCHMARK_REMOTE_ENABLED=false`
e reinicie o controle.

## Como obter o ID de canal em uma instalação nova

1. Adicione o bot como administrador do canal, com permissão de publicar.
2. Antes de iniciar o serviço de polling, consulte `getUpdates` com
   `allowed_updates=["message","channel_post","my_chat_member"]` pela Bot API,
   carregando o token da `.env`, sem colocá-lo em URL compartilhada de navegador.
3. Publique qualquer texto novo no canal. Não é esperada uma resposta do bot nessa etapa.
4. Leia `result[].channel_post.chat.id`: esse é o ID do canal. Já um `/start` no
   privado produz `result[].message.from.id`, que identifica o usuário humano.
5. Grave os IDs e execute `telegram-check`. Não use dois consumidores de eventos ao mesmo tempo.

O canal desta instalação já foi identificado e salvo; não é necessário repetir
esses passos. Consulte as [definições de eventos do Telegram](https://core.telegram.org/bots/api#update).

Não há limite de dólares na `.env` entregue. O consumo continua registrado; os
limites técnicos de chamadas, tokens, tempo e tentativas continuam ativos. USD é
um argumento opcional dos comandos para quem quiser habilitar um teto específico;
esse uso exige configurar também uma reserva por chamada, descrita no guia de operação.

## Outros campos

- `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` e `OPENROUTER_EMBEDDING_MODEL` mantêm
  provedor/modelos existentes. `OPENROUTER_JUDGE_MODEL` vazio usa o modelo geral.
- `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` e `NEO4J_DATABASE` identificam
  o banco usado pelo Knowledge. Confirme os valores próprios da VPS na implantação.
- `BENCHMARK_MODE=full` habilita as seis arquiteturas, sem um arquivo de perfis.
- `BENCHMARK_OUTPUT_DIR` e `BENCHMARK_BUDGET_DIR` apontam para `resultados`,
  resolvido a partir da raiz, para manter saídas e contabilização compartilhadas.
- `BENCHMARK_CONTROL_SOCKET`, `BENCHMARK_CONTROL_DATABASE` e
  `BENCHMARK_TELEGRAM_DATABASE` já correspondem aos caminhos dos serviços da VPS.
- `TELEGRAM_PROGRESS_INTERVAL_SECONDS=15` define a frequência de consulta;
  `TELEGRAM_SEND_FINAL_FILES=true` inclui exportações públicas ao concluir.

Variáveis avançadas opcionais, como chaves por função, dimensões de embeddings,
snapshot e limites monetários, permanecem documentadas no guia de operação e não
precisam poluir a configuração usual. Não defina um `CHROMA_PERSIST_DIR` global
compartilhado pelos seis RAGs: cada arquitetura conserva seu índice próprio.

## O que significa `data` no Knowledge

| Caminho | Origem e uso |
| --- | --- |
| `rags/knowledge-enhanced-rag/data/apostilas/` | Caminho original de ingestão. Contém cópias dos mesmos sete PDFs, conferidas por SHA-256 contra o Context RAG. |
| `data/knowledge-graph.json` | Alternativa adicional de snapshot, copiada dos 14 conceitos e relações curados no código original. Não foi extraída dos PDFs e está inativa. |
| `eval-dataset/qa_dataset_90.json` | Dataset de avaliação compartilhado de 90 perguntas e referências. Não substitui os PDFs nem é usado como resposta candidata. |

A configuração entregue usa `BENCHMARK_KG_MODE=required`, ou seja, PDFs + Neo4j.
Não há seleção automática do JSON nem construção de outro corpus. O grafo curado
já fazia parte da arquitetura original e constitui conhecimento adicional aos
PDFs. Substituí-lo por um grafo extraído dos livros alteraria o método e exigiria
uma decisão separada. Resultados anteriores não são reescritos para refletir isso.

## Leitura da configuração pelos serviços

`service_entrypoint.py` inicia controle, lote ou Telegram com apenas as variáveis
de sua função. O notificador não recebe chaves de modelos; os workers não recebem
o token do Telegram. Nas unidades systemd, o gerenciador lê a `.env` antes de
torná-la inacessível aos processos dos serviços. Use permissão `0600` no arquivo
e mantenha o proprietário administrativo na VPS. O arquivo é ignorado pelo Git.

Para iniciar manualmente na raiz, use `uv run --locked python service_entrypoint.py
control` ou `uv run --locked python service_entrypoint.py telegram`, com os
diretórios configurados acessíveis ao usuário. Esses comandos iniciam serviços:
não são testes secos. `main.py` também carrega a `.env` única para a CLI de RAGs.
