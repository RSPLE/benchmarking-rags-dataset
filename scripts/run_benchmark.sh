#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
QUESTIONS="${QUESTIONS:-1}"

cd "$ROOT"
unset VIRTUAL_ENV

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' "Erro: uv nao encontrado. Instale o uv antes de continuar." >&2
    exit 1
fi

if [[ ! -f "$ROOT/.env" ]]; then
    printf '%s\n' "Erro: arquivo .env nao encontrado na raiz do projeto." >&2
    exit 1
fi

directories=(
    "rags/context-rag/docs"
    "rags/graph-rag/docs"
    "rags/hybrid-rag/docs"
    "rags/memory-augmented-rag/docs"
    "rags/self-rag/docs"
    "rags/knowledge-enhanced-rag/data/apostilas"
)

printf '%s\n' "[1/4] Verificando PDFs..."
for directory in "${directories[@]}"; do
    if [[ ! -d "$directory" ]]; then
        printf 'Erro: diretorio nao encontrado: %s\n' "$directory" >&2
        exit 1
    fi

    shopt -s nullglob nocaseglob
    pdfs=("$directory"/*.pdf)
    shopt -u nullglob nocaseglob
    if (( ${#pdfs[@]} == 0 )); then
        printf 'Erro: nenhum PDF encontrado em %s\n' "$directory" >&2
        exit 1
    fi
    printf '  %s: %d PDF(s)\n' "$directory" "${#pdfs[@]}"
done

printf '%s\n' "[2/4] Sincronizando o ambiente raiz..."
uv sync

printf '%s\n' "[3/4] Validando configuracao..."
uv run python main.py doctor

printf '%s\n' "[4/4] Executando ate $QUESTIONS pergunta(s) por pipeline..."
exec uv run python main.py run all --questions "$QUESTIONS"
