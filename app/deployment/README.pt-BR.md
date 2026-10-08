# Implantação na VPS com Docker Compose

[English](README.md) · [Configuração](../docs/configuration.pt-BR.md) · [Dashboard](../dashboard/README.pt-BR.md)

Execute os comandos abaixo na raiz do repositório. O fluxo padrão usa apenas
Docker Engine e o plugin Docker Compose na VPS; não exige instalar Python/uv no host.
Confirme `docker --version` e `docker compose version` antes de começar.

## 1. Clonar ou copiar o projeto

```bash
git clone https://github.com/RSPLE/benchmarking-rags-dataset.git
cd benchmarking-rags-dataset
```

O clone precisa conter a revisão com `app/` e os Dockerfiles novos. Alterações ainda
somente locais precisam ser transferidas ou publicadas no repositório antes do clone.
Não copie `.venv`, caches ou containers. Se já existem dados na VPS, preserve o `.env`,
`resultados/`, índices e volumes; mantenha o nome do projeto Compose usado anteriormente.

## 2. Criar o único arquivo de configuração

Em uma instalação nova:

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Se `.env` já existir, edite-o sem substituir suas credenciais. Preencha:

| Variável | O que colocar |
| --- | --- |
| `DASHBOARD_PUBLIC_HOST` | IP público da VPS, por exemplo `179.236.251.180`, ou domínio; sem `https://` nem porta |
| `DASHBOARD_USERNAME` | Seu usuário, com 3–64 letras, números, `.`, `_` ou `-` |
| `DASHBOARD_PASSWORD` | Sua senha, com pelo menos 12 caracteres; use aspas simples se contiver `$` |
| `OPENROUTER_API_KEY` | Chave real do OpenRouter |
| `OPENROUTER_MODEL`, `OPENROUTER_JUDGE_MODEL`, `OPENROUTER_EMBEDDING_MODEL` | Modelos do protocolo; juiz vazio herda o modelo principal |
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` | Conexão do grafo do Knowledge |
| `NEO4J_CONTAINER_URI` | `bolt://neo4j:7687` para banco local do Compose; vazio para usar a URI hospedada |
| `TELEGRAM_BOT_TOKEN` | Token completo do BotFather, não apenas o ID do bot |
| `TELEGRAM_RESULTS_CHAT_ID` | ID numérico negativo do canal privado, geralmente `-100...` |
| `TELEGRAM_ALLOWED_USER_IDS` | Seu ID pessoal numérico; múltiplos IDs separados por vírgula |
| `BENCHMARK_REMOTE_ENABLED` | `true` para permitir execução pelo dashboard e Telegram |
| `TELEGRAM_ENABLED`, `TELEGRAM_CONTROL_ENABLED` | `true` para publicar resultados e receber comandos |
| `TELEGRAM_SEND_FINAL_FILES` | `true` para enviar os arquivos públicos ao concluir cada rodada de RAG |

Não há limite monetário obrigatório. Os limites técnicos de chamadas, tokens e tempo
continuam configuráveis. As credenciais não têm valores padrão de acesso ao dashboard.

Para Neo4j local, use `NEO4J_USERNAME=neo4j`, escolha uma senha e mantenha
`NEO4J_URI=bolt://127.0.0.1:7687` com `NEO4J_CONTAINER_URI=bolt://neo4j:7687`.
Para Aura/outro servidor, use suas credenciais e deixe `NEO4J_CONTAINER_URI` vazio.
Uma senha nova não altera a autenticação de um volume Neo4j já existente.

## 3. Conferir PDFs, diretório de resultados e portas

Coloque os mesmos sete PDFs aprovados em `app/rags/context-rag/docs/` se ainda não
estiverem presentes. No Compose, esse diretório é montado como `/corpus` para os
seis RAGs, incluindo Knowledge; não é necessário manter seis cópias na VPS.
Os PDFs não entram na imagem. O dataset já fica em `data/evaluation/`.

Os serviços gravam como UID/GID 1000. Para o `BENCHMARK_OUTPUT_DIR=resultados`
padrão, em instalação nova:

```bash
sudo install -d -m 0750 -o 1000 -g 1000 resultados
```

Se estiver como root, `sudo` pode ser omitido. Para outro caminho, crie esse diretório
com a mesma propriedade; se houver resultados existentes, preserve-os e ajuste acesso
antes da execução. Não use permissão `777`.

Libere **TCP 80 e 443** no firewall da VPS e do provedor, preservando o acesso SSH.
Essas portas precisam estar livres e chegar a esta VPS. Não publique Streamlit,
autenticação, fila, SQLite ou Neo4j diretamente. O Telegram usa conexões de saída.

## 4. Subir tudo

```bash
docker compose config --quiet
docker compose up -d
docker compose ps
```

As imagens são construídas localmente a partir de bases públicas. A primeira subida
precisa de internet e pode demorar. Não há profiles nem login em registry privado.
O Compose inicia os serviços, sem agendar um benchmark automaticamente.
O Caddyfile da raiz é copiado para a imagem do proxy: mudanças nele são aplicadas
pela próxima execução de `docker compose up -d`.

Acesse `https://<DASHBOARD_PUBLIC_HOST>` com o usuário/senha definidos na `.env`.
No mesmo computador que executa Docker, use `http://127.0.0.1:8501`.
O endereço `127.0.0.1` em seu notebook não aponta para a VPS.
A emissão do certificado público depende de o endereço e as portas chegarem à VPS;
executar a aplicação localmente com o IP remoto não comprova HTTPS na VPS.

```bash
docker compose logs --tail 80 proxy auth dashboard monitor control telegram
docker compose exec control python -m app doctor
docker compose exec control python -m app preflight
docker compose exec control python -m app telegram-check
```

`doctor` confere configuração/corpus; `preflight` lê PDFs e dependências sem gerar
embeddings ou chamar modelos. A leitura dos sete PDFs pode demorar. `telegram-check`
consulta a API para conferir bot/canal/permissões, sem enviar mensagens nem consumir
updates. O bot deve ser administrador do canal com permissão de publicar.

## 5. Preparar o grafo do Knowledge quando necessário

`BENCHMARK_KG_MODE=required` exige o grafo curado preenchido. Se você usa a base
hospedada existente ou restaurou o grafo, não o reconstrua. Um Neo4j novo e vazio
não contém esse grafo automaticamente. Os PDFs continuam sendo os mesmos sete.

Somente para inicializar uma base sem nós `Conceito`, o comando abaixo cria o grafo
definido no código original e recusa uma base que já contenha esses nós. Execute
antes de enviar trabalhos e mantenha `BENCHMARK_ALLOW_KG_REBUILD=false` na `.env`:

```bash
docker compose exec -T -w /app/app/rags/knowledge-enhanced-rag control .venv/bin/python - <<'PY'
import os
import rag_settings
from src.knowledge_graph import KnowledgeGraph

graph = KnowledgeGraph()
try:
    with graph.driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
        count = session.run("MATCH (n:Conceito) RETURN count(n) AS total").single()["total"]
    if count:
        raise SystemExit("O grafo já contém conceitos; preserve a base existente.")
    os.environ["BENCHMARK_ALLOW_KG_REBUILD"] = "true"
    graph.build_graph()
finally:
    graph.fechar()
PY
```

A preparação acima grava no Neo4j configurado, não chama modelos nem extrai um novo
grafo dos PDFs. Bancos existentes devem ser preservados/restaurados conforme seu
protocolo de pesquisa, sem substituir o grafo por outro para contornar uma falha.

## 6. Iniciar e acompanhar

Na interface, abra **Executar e retomar**, escolha RAG, quantidade e flags e envie
o lote. **Pendências e falhas** prepara retomadas. Fechar o navegador não interrompe
o executor. Para Telegram, envie os comandos na conversa privada com o bot:

```text
/start
/status
/executar context-rag --questions 1
/retomar context-rag ID_COMPLETO --questions 3 --selection failed
```

O canal recebe resultados; comandos de execução são aceitos na conversa privada.
O equivalente pelo terminal, dentro do container, é:

```bash
docker compose exec control python -m app run context-rag --questions 1
docker compose exec control python -m app resume context-rag ID_COMPLETO --questions 3 --selection failed
```

Esses comandos iniciam trabalho real e podem consumir créditos desde a preparação.
CLI, dashboard e Telegram compartilham as flags e checkpoints. Uma execução ativa
possui trava global; não inicie outro lote enquanto ela estiver em andamento.
Consulte [as opções e o protocolo](../docs/compatibility.pt-BR.md).

## 7. Preservar e atualizar

`resultados/` fica no host. SQLite, fila, outbox do Telegram, índices e certificados
ficam em volumes persistentes. O monitor usa backup consistente do SQLite após
mudanças; o Telegram envia os artefatos públicos de cada rodada conforme a configuração.
O canal não substitui backup completo: checkpoints privados, índices e banco precisam
ser preservados também fora da VPS.

```bash
docker compose exec monitor python -m app.dashboard.manage backup
docker compose ps
```

Faça atualizações sem trabalho ativo. Preserve os dados, atualize o código e execute
`docker compose up -d`. Não use `docker compose down -v`: `-v` apaga os volumes.
Retomadas exigem configuração compatível; confira o [guia de migração](../docs/layout.pt-BR.md).
Em caso de WebSocket recusado, verifique os logs de `proxy`, `auth` e `dashboard`;
o proxy deve usar a imagem atual, com verificação HTTP da sessão antes do upgrade.

A alternativa sem Compose está no [guia systemd](systemd.pt-BR.md). Não execute
os dois supervisores sobre os mesmos trabalhos ou o mesmo polling do Telegram.

## Acesso público por IP e diagnóstico de TLS

Na VPS, abra `https://<DASHBOARD_PUBLIC_HOST>`, sem `:8501`. A porta 8501 fica
publicada apenas em `127.0.0.1` para acesso local. Um timeout ao usar
`http://IP:8501` de outro computador é compatível com essa restrição.

O Caddyfile também define `default_sni {$DASHBOARD_PUBLIC_HOST}`. Isso seleciona
o certificado configurado para clientes sem SNI, necessário neste acesso por IP
atrás do Docker. Consulte a [documentação do Caddy](https://caddyserver.com/docs/caddyfile/options#default-sni).
O endereço continua vindo exclusivamente da `.env`, sem IP fixo no Caddyfile.

Para investigar sem desativar a validação do certificado:

```bash
curl --silent --show-error --output /dev/null --write-out '%{http_code}\n' http://IP_DA_VPS
curl --silent --show-error --output /dev/null --write-out '%{http_code}\n' https://IP_DA_VPS/auth/login
docker compose logs --tail 80 proxy
```

Substitua `IP_DA_VPS` pelo endereço real. O HTTP deve redirecionar para HTTPS,
e a página de login deve retornar 200 com certificado válido. `health: starting`
logo após subir é transitório; o healthcheck local não comprova o HTTPS público.
Se 80/443 já respondem, investigue o log TLS antes de mudar regras de firewall.
Depois de atualizar o Caddyfile, `docker compose up -d` aplica a nova imagem.
Para atualizar apenas esse serviço, use `docker compose up -d --no-deps proxy`.
