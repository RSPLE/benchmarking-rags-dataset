from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from app.benchmark.archive import archive_bytes
from app.dashboard.database import connect
from app.paths import DATASET_ROOT

FILE_LABELS = {
    "results.csv": "Resultados e métricas (CSV)",
    "results_detailed.csv": "Respostas e resultados detalhados (CSV)",
    "summary.json": "Resumo da execução (JSON)",
    "manifest.json": "Configuração e identificação do experimento (JSON)",
    "checkpoint.json": "Progresso, respostas e contextos (JSON)",
    "usage.jsonl": "Tokens, tempo e custo das chamadas (JSONL)",
    "judge_responses.jsonl": "Respostas e justificativas do juiz (JSONL)",
    "judge_reviews.jsonl": "Histórico de reavaliações (JSONL)",
    "public_results.json": "Resultados por ID de questão (JSON)",
    "errors.json": "Falhas registradas (JSON)",
    "events.jsonl": "Histórico detalhado da execução (JSONL)",
    "public_events.jsonl": "Eventos públicos (JSONL)",
}


def is_complete(record, expected):
    summary = record["summary"]
    return (
        summary.get("success", 0) == expected
        and not any(summary.get(key, 0) for key in ("failed", "pending", "partial"))
        and summary.get("operation") != "running"
    )


def experiment_files(database, identifier):
    with connect(database) as db:
        return {
            row["name"]: bytes(row["content"])
            for row in db.execute(
                "SELECT name,content FROM artifacts WHERE experiment_id=? ORDER BY name",
                (identifier,),
            )
            if row["name"] in FILE_LABELS
        }


def downloads_view(database, records):
    expected = len(json.loads((DATASET_ROOT / "qa_dataset_90.json").read_bytes()))
    st.write(
        "Baixe os resultados por RAG. Todas as execuções aparecem aqui, independentemente do protocolo de comparação."
    )
    selection = st.radio(
        "Exibir execuções", ["Concluídas", "Todas"], horizontal=True, key="downloads-status"
    )
    visible = [row for row in records if selection == "Todas" or is_complete(row, expected)]
    if not visible:
        st.info(
            "Nenhuma execução concluída disponível. Selecione Todas para consultar resultados parciais."
            if records
            else "Ainda não há dados disponíveis para download."
        )
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "RAG": row["project"],
                    "Estado": "Concluída"
                    if is_complete(row, expected)
                    else "Em andamento / incompleta",
                    "Questões concluídas": f"{row['summary'].get('success', 0)}/{expected}",
                    "Experimento": row["external_id"][:12],
                }
                for row in visible
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    project = st.selectbox(
        "RAG para baixar", sorted({row["project"] for row in visible}), key="downloads-project"
    )
    candidates = {row["id"]: row for row in visible if row["project"] == project}
    identifier = st.selectbox(
        "Execução",
        list(candidates),
        format_func=lambda key: (
            f"{candidates[key]['external_id'][:12]} · {candidates[key]['summary'].get('success', 0)}/{expected} concluídas"
        ),
        key="downloads-experiment-" + project,
    )
    record = candidates[identifier]
    files = experiment_files(database, identifier)
    if not files:
        st.info("Os arquivos desta execução ainda não foram sincronizados.")
        return
    st.subheader(project)
    st.caption(f"Experimento: {record['external_id']}")
    if not is_complete(record, expected):
        st.info(
            "Esta execução está incompleta. Os arquivos contêm somente os resultados já registrados."
        )
    prefix = f"{project}-{record['external_id'][:12]}"
    csv_column, zip_column = st.columns(2)
    if "results.csv" in files:
        csv_column.download_button(
            "Baixar resultados (CSV)",
            files["results.csv"],
            prefix + "-resultados.csv",
            "text/csv",
            key="downloads-results",
            on_click="ignore",
            width="stretch",
        )
    zip_column.download_button(
        "Baixar dados completos (ZIP)",
        archive_bytes(files),
        prefix + "-dados.zip",
        "application/zip",
        key="downloads-package",
        on_click="ignore",
        width="stretch",
    )
    st.caption(
        "O ZIP reúne os arquivos salvos deste experimento e seus hashes de verificação. Inclui respostas, contextos e registros do juiz quando disponíveis."
    )
    with st.expander("Baixar um arquivo específico"):
        name = st.selectbox(
            "Arquivo",
            list(files),
            format_func=lambda key: FILE_LABELS[key],
            key="downloads-file-" + identifier,
        )
        mime = (
            "text/csv"
            if name.endswith(".csv")
            else "application/x-ndjson"
            if name.endswith(".jsonl")
            else "application/json"
        )
        st.download_button(
            "Baixar arquivo selecionado",
            files[name],
            prefix + "-" + name,
            mime,
            key="downloads-selected",
            on_click="ignore",
            width="stretch",
        )


def backups_view(database):
    st.write(
        "Cópias verificadas do banco de dados do painel. Para baixar resultados de um RAG, abra Baixar dados."
    )
    with connect(database) as db:
        backups = pd.read_sql_query(
            "SELECT id,created_at,sha256 FROM backups ORDER BY created_at DESC LIMIT 20", db
        )
    st.dataframe(backups, hide_index=True, width="stretch")
    st.caption(
        "Os backups completos permanecem no volume privado da VPS e preservam contas, configurações e resultados importados."
    )
