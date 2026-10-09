# Estrutura da aplicação e experimentos existentes

[English](layout.md) · [Repositório](../../README.pt-BR.md) · [Implantação](../deployment/README.pt-BR.md)

A implementação e as pastas de suporte ficam dentro de `app/`:

| Caminho | Responsabilidade |
| --- | --- |
| `app/__main__.py`, `app/cli.py` | Comando único `python -m app` |
| `app/benchmark/` | Execução, avaliação, checkpoints, exportação, fila, consumo e identidade dos experimentos |
| `app/dashboard/` | Streamlit, autenticação, SQLite, gráficos e monitoramento |
| `app/rags/` | Seis pipelines de pesquisa com ambientes isolados |
| `app/providers/` | Clientes de modelos/embeddings e compatibilidade RAGAS |
| `app/telegram/` | Notificações, comandos e verificação da configuração do Telegram |
| `app/services/` | Inicialização dos serviços e variáveis por função |
| `app/tools/` | Preparação do corpus e verificações locais de integração |
| `app/scripts/` | Comandos auxiliares em shell |
| `app/deployment/` | Dockerfiles, instruções da VPS e units opcionais do systemd |
| `app/docs/` | Configuração, metodologia, operação e evidências de aceitação |
| `app/tests/` | Regressões comuns e contratos dos repositórios originais |
| `app/tests/dashboard/` | Testes HTTP de autenticação e proxy |

Na raiz ficam `.env`, `.env.example`, Compose, Caddyfile, dependências Python e
READMEs principais. `data/` contém entradas científicas; `resultados/` e `backups/`
contêm dados de execução. O padrão é `README.md` em inglês e `README.pt-BR.md` em
português. Não há scripts Python de compatibilidade mantidos na raiz.

## Comandos na raiz do repositório

```bash
uv run --locked python -m app list
uv run --locked python -m app doctor
uv run --locked python -m app preflight
uv run --locked python -m app.tools.verify_integrations
uv run --locked python -m unittest discover -s app/tests -v
uv run --project app/dashboard --locked python -m unittest discover -s app/tests/dashboard -v
uv run --locked ruff check app --exclude '*.ipynb'
```

O antigo `main.py` da raiz passa a ser `python -m app`. Serviços usam
`python -m app.services.entrypoint`. No Compose, prefixe a CLI científica com
`docker compose exec control`; as imagens já possuem as dependências instaladas.
Ambientes virtuais locais não são portáveis: em outra máquina, recrie-os pelos
lockfiles preservados. Não copie `.venv` para a VPS.

## Preservação dos resultados

JSONs do dataset, notebooks, CSVs/checkpoints históricos, gráficos e lockfiles
mantêm seus conteúdos. Os nomes dos volumes Docker e os destinos internos de
resultados/bancos permanecem iguais. Mantenha o nome do projeto Compose e seus
volumes ao atualizar; mudar a pasta do checkout pode alterar o nome padrão do projeto.

Fora dos containers, o antigo `dashboard/state/` passa a `app/dashboard/state/`,
com o banco ainda chamado `dashboard.sqlite3`. Registros históricos importados
são associados ao novo caminho `app/rags/` somente se o hash dos artefatos coincidir;
os IDs e amostras existentes são mantidos, sem duplicar experimentos.

## Retomar um checkpoint v2 após a reorganização

A identidade dos experimentos inclui hashes do código e caminhos dos índices.
Novos experimentos recebem outra identidade depois da mudança. Uma retomada
explícita pode manter a identidade anterior somente para as revisões exatas
registradas em `app/benchmark/layout_v1.json`:

1. Validar o hash do manifesto salvo e seu inventário completo de código original.
2. Validar os módulos atuais de ciência/serviços contra a revisão aprovada.
3. Aceitar apenas a mudança registrada do índice padrão de `rags/` para `app/rags/`.
4. Exigir corpus, dataset, runtime, modelos, métricas e demais configurações idênticos.
5. Manter manifesto/checkpoint originais e registrar o manifesto do código executado
   em `layout_transitions/<id_atual_do_experimento>.json`.

Respostas e métricas concluídas continuam reutilizáveis. Revisões incompatíveis ou
desconhecidas são rejeitadas antes da preparação dos modelos. Caminhos personalizados
de índices precisam permanecer iguais. Alterações futuras precisam de outro
experimento ou migração auditada; não regenere o registro para contornar divergências.
Outro runtime ou caminho absoluto na VPS também pode impedir a retomada; preservar
os arquivos não comprova, por si só, compatibilidade científica.

Saídas v1 continuam disponíveis para consulta/importação, sem conversão automática
em experimentos v2. Consulte o [guia de recuperação](reliability.pt-BR.md).

A correção operacional de 09/10/2026 mantém uma lista explícita dos hashes da revisão
anterior em `compatible_shared`. A transição permite retomar esses checkpoints com
o controle de fila e notificações corrigidos. Dataset, corpus, modelos, código de
respostas e avaliação continuam sujeitos à validação integral; alterações fora das
revisões registradas são recusadas. A retomada grava `app-layout-and-operations-v2`
no registro de transição, sem reescrever o manifesto original.
