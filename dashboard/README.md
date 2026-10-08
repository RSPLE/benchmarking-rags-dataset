# Painel web e Docker

[English](README.en.md) · [Implantação](../deployment/README.md)

## Iniciar o projeto

Na `.env` da raiz, preencha antes de subir:

```dotenv
DASHBOARD_USERNAME=seu_usuario
DASHBOARD_PASSWORD='sua-senha-com-pelo-menos-12-caracteres'
```

Use suas próprias credenciais. O usuário aceita 3–64 letras, números, ponto,
hífen e sublinhado. A senha precisa ter 12–1024 caracteres. Aspas simples preservam
caracteres como `$` na leitura pelo Compose. A `.env` deve permanecer privada,
com permissão `0600`, fora do Git e das imagens Docker.

Depois, na raiz do projeto:

```bash
docker compose up -d
```

Abra <http://127.0.0.1:8501> e entre com o usuário e a senha da `.env`.
**Não há credenciais padrão, cadastro posterior ou comando adicional de criação de
usuário.** O Compose exige ambos os campos e o painel valida as credenciais antes
de iniciar o servidor. A senha fica na `.env` conforme configurada; no SQLite,
a aplicação armazena somente hash scrypt com salt individual.

Esse comando inicia Neo4j, painel, importador/backup, controle e Telegram. Os quatro
serviços da aplicação são construídos a partir dos Dockerfiles locais, usando cache.
Não há perfis nem imagens privadas do projeto para baixar. Não é necessário login
em registro de imagens. A primeira construção precisa de internet para baixar
as imagens base públicas e as dependências fixadas nos lockfiles.

Subir os serviços deixa a fila e o bot disponíveis, conforme as opções Telegram
já preenchidas na `.env`. Novos benchmarks são solicitados pelos comandos da CLI
ou do bot. O painel lê resultados, sem iniciar chamadas aos modelos.

## Conferir e reiniciar

```bash
docker compose ps
docker compose logs --tail 80 dashboard monitor control telegram neo4j
```

Aguarde o estado `healthy` do painel, importador, controle e Neo4j. O Telegram só
opera quando token, canal e opções de ativação estão configurados. Credenciais
curtas ou ausentes impedem o acesso; confira o log sem imprimir a `.env` inteira.

Para mudar a senha, edite `DASHBOARD_PASSWORD` e execute novamente:

```bash
docker compose up -d
```

A conta é atualizada antes do servidor iniciar e as sessões anteriores são
invalidadas. Com a mesma senha, reiniciar preserva a conta e os bloqueios existentes.
Cinco tentativas incorretas bloqueiam o login por cinco minutos. Alterar o nome de
usuário cria outra conta, preservando contas anteriores; isso não revoga o nome antigo.
As credenciais do painel são independentes de Neo4j, Telegram e OpenRouter.

## Persistência e backup

`dashboard-state` guarda o SQLite com usuários e resultados importados.
`dashboard-backups` guarda suas cópias. Outros volumes conservam fila, notificações,
Neo4j e índices de cada RAG. `BENCHMARK_OUTPUT_DIR=resultados` é montado como um
diretório real do host, com leitura para importação e Telegram e escrita para o
executor. Os arquivos históricos em `rags/*/results*` são lidos sem alteração.

Os containers usam UID/GID 1000. O diretório de resultados precisa ser legível pelo
importador e gravável pelo executor. Evite permissões `777` e alterações recursivas
sobre os resultados históricos. Não use `docker compose down -v` para reiniciar,
pois isso remove os volumes persistentes.

Para solicitar uma cópia online consistente do SQLite:

```bash
docker compose exec monitor python -m dashboard.manage backup
```

Não copie o arquivo SQLite ativo manualmente como substituto da API de backup.
Os backups locais não substituem cópias fora da máquina.

## Opções na mesma `.env`

| Variável | Padrão | Efeito |
|---|---|---|
| `DASHBOARD_USERNAME` | Obrigatório | Seu usuário |
| `DASHBOARD_PASSWORD` | Obrigatório | Sua senha, com pelo menos 12 caracteres |
| `DASHBOARD_BIND_ADDRESS` | `127.0.0.1` | Endereço publicado pelo Docker |
| `DASHBOARD_PORT` | `8501` | Porta do painel |
| `DASHBOARD_POLL_SECONDS` | `15` | Intervalo de importação/atualização |
| `DASHBOARD_BACKUP_SECONDS` | `60` | Intervalo mínimo de backup periódico quando há mudanças |
| `DASHBOARD_SESSION_SECONDS` | `28800` | Duração máxima da sessão de login |

Na VPS, mantendo a porta restrita a localhost, conecte do seu computador com
`ssh -L 8501:127.0.0.1:8501 usuario@servidor` e abra a mesma URL local.
Para publicar o login na internet, configure HTTPS e um proxy reverso.

## Neo4j

O erro `Invalid admin username, it must be neo4j` foi corrigido: a inicialização do
banco local usa o administrador `neo4j`. A senha vem de `NEO4J_PASSWORD` e só é
aplicada quando o volume ainda não tem autenticação. Não apague dados existentes
para trocar credenciais.

O executor respeita `NEO4J_URI` e `NEO4J_USERNAME` da `.env` para um banco hospedado.
O painel não depende de Neo4j. Para escolher explicitamente o banco local do Compose:

```dotenv
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_CONTAINER_URI=bolt://neo4j:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=sua-senha-local
```

Com banco hospedado, deixe `NEO4J_CONTAINER_URI` vazio. Um Neo4j local vazio não
contém automaticamente o grafo científico do Knowledge; os controles do pipeline
continuam verificando a disponibilidade desse grafo antes dos testes.

## Gráfico principal

`rags/context-rag/plot_graph.py` separa a criação da figura
(`build_overall_figure`) do salvamento (`plot_overall_mean`). O painel reutiliza a
mesma figura para PNG/EPS. Essa extração não altera médias, barras, cores, rótulos,
CSVs ou a execução anterior pela CLI.

## Sem Docker

Com as mesmas credenciais na `.env`, execute na raiz:

```bash
uv sync --project dashboard --frozen
uv run --project dashboard --frozen python -m dashboard.monitor
```

Em outro terminal, na raiz:

```bash
uv run --project dashboard --frozen python -m dashboard.server
```

O modo local usa `dashboard/state/dashboard.sqlite3` e `backups/dashboard`.
Esses arquivos são separados dos volumes Docker, embora as credenciais sejam
carregadas da mesma `.env`.
