# Painel web, execução e publicação na VPS

[English](README.md) · [Implantação](../deployment/README.pt-BR.md)

## Iniciar

Defina suas credenciais na única `.env`, na raiz, antes de subir:

```dotenv
DASHBOARD_USERNAME=seu_usuario
DASHBOARD_PASSWORD='sua-senha-com-pelo-menos-12-caracteres'
DASHBOARD_PUBLIC_HOST=IP_PUBLICO_DA_VPS
```

O usuário aceita 3–64 letras, números, ponto, hífen e sublinhado. A senha exige
12–1024 caracteres. Aspas simples preservam `$` na leitura do Compose. Mantenha a
`.env` com modo `0600`, fora do Git e das imagens. Não há conta ou senha padrão.

```bash
docker compose up -d
```

- Na VPS: `https://<DASHBOARD_PUBLIC_HOST>`.
- Na própria máquina: <http://127.0.0.1:8501>.

Substitua `IP_PUBLICO_DA_VPS` pelo IP público ou domínio, sem protocolo ou porta.
O Compose lê `DASHBOARD_PUBLIC_HOST` da `.env` e a repassa ao serviço `proxy`.
O [Caddyfile da raiz](../../Caddyfile) usa essa variável, sem endereço fixo ou padrão.
Se ela estiver ausente ou vazia, o Compose interrompe a inicialização com uma
mensagem de configuração. O proxy protege a interface, os downloads e o WebSocket.
HTTP público redireciona para HTTPS. A porta 8501 atende somente localhost e passa
pela mesma autenticação; o Streamlit não é publicado diretamente.

Libere **TCP 80 e 443** no firewall da VPS/provedor e mantenha essas portas livres
para o Caddy. O IP precisa alcançar esta VPS pela internet para emitir o certificado.
O Caddy solicita e renova o certificado automaticamente, com armazenamento no
volume `caddy-data`. A configuração usa o perfil ACME `shortlived`, compatível com
[certificados públicos de IP do Let's Encrypt](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability).
A emissão real só pode ser confirmada após subir na VPS com essas portas acessíveis.
Rodar no computador local não comprova o HTTPS do IP público.

São sete serviços: proxy, autenticação, painel, importador/backup, executor,
Telegram e Neo4j. Os cinco serviços da aplicação e o proxy Caddy são construídos localmente.
Caddy, Neo4j e as imagens base são públicos. Não existem perfis nem imagem privada
do projeto; não é necessário `docker login`. A primeira construção requer internet.
Não publique portas adicionais para SQLite, autenticação, fila, Neo4j ou Telegram.
O bot usa polling de saída e não precisa de webhook/porta pública.

## Login persistente e navegação

Recarregar, mudar de aba ou reiniciar os containers preserva o login até o prazo da
sessão, por padrão oito horas desde a autenticação. O navegador guarda um cookie
`HttpOnly`, `SameSite=Lax` e, no HTTPS, `Secure`. O SQLite guarda somente o hash do
token da sessão e o hash scrypt da senha. A senha não vai para a URL nem para o
armazenamento local do navegador. Limpar cookies ou usar outra janela privada exige
novo login. **Sair da conta** revoga a sessão imediatamente.

Editar `DASHBOARD_PASSWORD` e repetir `docker compose up -d` atualiza a conta e
revoga sessões antigas. Cinco senhas incorretas bloqueiam a conta por cinco minutos.
Trocar somente o nome cria outra conta e preserva a anterior. As contas existentes
são de operador e podem enviar execuções; não há perfil de visitante nesta versão.

O painel lateral reúne visão geral, execução/retomada, pendências/falhas, gráficos
originais, tokens/tempo, questões, chamadas/modelos e arquivos/backups. A página
selecionada permanece na URL ao recarregar. No celular, abra o menu lateral pelo
botão superior. A configuração inclui tema escuro; **⋮ → Theme** permite escolher
claro ou escuro, com preferência salva no navegador. Os gráficos destinados aos
artigos mantêm seu estilo original mesmo quando o painel está escuro.

## Executar, retomar e pausar

1. Abra **Executar e retomar**, escolha um dos seis RAGs ou todos em sequência.
2. Escolha iniciar ou retomar um experimento existente com checkpoint v2.
3. Selecione somente pendentes, somente falhas ou ambos e o número de tentativas.
4. Em **Parâmetros de execução**, ajuste provedor, modo, respostas congeladas,
   repetição, chamadas e tempos. Zero nos limites opcionais herda a configuração;
   não significa remover um limite existente.
5. Clique **Enviar lote para execução**. A fila e o consumo ficam disponíveis nas
   páginas do painel e no Telegram. Fechar o navegador não interrompe o executor.

**Pendências e falhas** mostra a cobertura e os erros por etapa. O botão de retomada
preenche a tela de execução, sem disparar chamadas automaticamente. Casos completos
são reaproveitados pelo mesmo executor e pelo mesmo checkpoint usados na CLI e no
Telegram. Configurações incompatíveis são rejeitadas. CSVs históricos sem checkpoint
v2 continuam consultáveis, mas não podem servir como ponto de retomada.

A interface usa o socket interno do controle e o parser existente, sem executar
comandos de shell. `BENCHMARK_REMOTE_ENABLED=true` permite admitir lotes tanto pelo
painel quanto pelo bot. O usuário de controle vem do primeiro ID de
`TELEGRAM_ALLOWED_USER_IDS`; opcionalmente, `DASHBOARD_CONTROL_USER_ID` pode escolher
outro ID já autorizado nessa lista. A `.env` real já possui o ID autorizado.

Há uma fila compartilhada: ela impede lotes concorrentes e usa um identificador
por solicitação para evitar duplicação em reenvios. Para repetir o mesmo lote após
encerrá-lo, use **Preparar outro pedido**. Uma execução ativa oferece **Pausar
preservando o checkpoint**; a pausa é cooperativa e depende da chamada em andamento.
Reiniciar o controle marca lotes ativos como interrompidos; a retomada é explícita.

Abrir ou atualizar a página não chama modelos. Enviar um lote pode consumir APIs
desde preparação/embeddings até avaliação. Os controles operacionais não alteram
prompts, memória, recuperação, PDFs nem métricas dos RAGs.

## Persistência, backup e diagnóstico

A página **Juiz e reavaliação** permite ler a saída e as justificativas do juiz,
auditar notas zero e reexecutar uma única métrica em questões escolhidas, preservando
os resultados originais. Consulte [Justificativas e reavaliação](../docs/judge-review.pt-BR.md).

`dashboard-state` conserva SQLite, contas, sessões e resultados importados;
`dashboard-backups` guarda cópias consistentes. `caddy-data` e `caddy-config` conservam
a configuração e os certificados. Outros volumes preservam fila, notificações,
Neo4j e índices. `BENCHMARK_OUTPUT_DIR=resultados` continua sendo um diretório real
do host. O monitor lê resultados novos e históricos sem alterá-los.

Containers da aplicação usam UID/GID 1000. O diretório de resultados deve ser
legível pelo monitor e gravável pelo executor. Não use permissões `777` nem
`docker compose down -v` para reiniciar: a opção `-v` remove volumes persistentes.

```bash
docker compose ps
docker compose logs --tail 80 proxy auth dashboard monitor control telegram
docker compose exec monitor python -m app.dashboard.manage backup
```

O backup usa a API online do SQLite. Não substitua esse procedimento por uma cópia
manual do arquivo ativo. A rotina já existente envia resultados públicos e hashes
ao canal Telegram ao encerrar cada rodada, conforme sua configuração; não envia
senhas nem checkpoints privados. Mantenha também cópias fora da VPS.

## Opções na mesma `.env`

| Variável | Padrão | Efeito |
|---|---|---|
| `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` | Obrigatórios | Credenciais definidas antes de subir |
| `DASHBOARD_PUBLIC_HOST` | Obrigatório, sem padrão | IP público ou domínio apontado para a VPS |
| `DASHBOARD_HTTP_PORT` / `DASHBOARD_HTTPS_PORT` | `80` / `443` | Portas públicas; mantenha os padrões para ACME sem redirecionamento externo |
| `DASHBOARD_PORT` | `8501` | Acesso local autenticado em localhost |
| `DASHBOARD_POLL_SECONDS` | `15` | Importação e atualização |
| `DASHBOARD_BACKUP_SECONDS` | `60` | Intervalo mínimo de backup periódico quando há mudanças |
| `DASHBOARD_SESSION_SECONDS` | `28800` | Validade total da sessão, em segundos |
| `DASHBOARD_CONTROL_USER_ID` | Primeiro ID autorizado do Telegram | Identidade de operador usada na fila |

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

`app/rags/context-rag/plot_graph.py` separa a criação da figura
(`build_overall_figure`) do salvamento (`plot_overall_mean`). O painel reutiliza a
mesma figura para PNG/EPS. Essa extração não altera médias, barras, cores, rótulos,
CSVs ou a execução anterior pela CLI.

## Desenvolvimento

O caminho de publicação suportado é o Compose, que inclui proxy e autenticação.
Executar apenas Streamlit não cria uma sessão autenticada. O executor científico
continua disponível pela CLI, conforme o guia da raiz.

Para validar o painel sem modelos pagos:

```bash
uv sync --project app/dashboard --frozen
uv run --project app/dashboard --frozen python -m unittest discover -s app/tests/dashboard -v
uv run --locked python -m unittest discover -s app/tests -v
```

## Atualização do proxy e WebSocket

O Caddyfile é copiado para a imagem do proxy. `docker compose up -d` reconstrói
e recria esse serviço quando o arquivo muda. A verificação de autenticação remove
os cabeçalhos de upgrade apenas da consulta HTTP de sessão; o WebSocket autenticado
é encaminhado ao Streamlit. Isso evita manter a configuração antiga em memória.

No acesso por IP, o Caddy usa `default_sni {$DASHBOARD_PUBLIC_HOST}` para clientes
sem SNI. O endereço público é `https://<DASHBOARD_PUBLIC_HOST>`; `:8501` continua
restrito ao próprio servidor. Consulte o [diagnóstico de TLS](../deployment/README.pt-BR.md#acesso-público-por-ip-e-diagnóstico-de-tls).

## Execução guiada e diagnóstico

Em **Visão geral**, use **Configurar e iniciar pipeline**. Em **Executar e retomar**,
escolha o RAG, comece com uma questão e clique em **Iniciar pipeline**. Os parâmetros
avançados são opcionais. O painel atualiza automaticamente e mostra o estado atual,
o motivo de uma falha e o horário local do lote; confirmação de envio não significa
conclusão do processamento. Após concluir, outro clique cria um novo lote. Reenvios
após perda de conexão mantêm o mesmo identificador para não duplicar trabalho.

Os eventos da fila ficam no SQLite do controle, no volume `control-state`. O Telegram
consulta esses eventos pelo socket interno e persiste seu cursor junto com a outbox.
Assim, uma falha de escrita em `resultados/` também pode ser notificada. O estado da
entrega é exibido no painel; mensagens pendentes são tentadas novamente. Atualize
`control`, `telegram` e `dashboard` juntos para usar esse fluxo.
