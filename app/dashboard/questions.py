from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from app.benchmark.config import METRICS
from app.cli import PROJECTS
from app.dashboard.database import connect
from app.paths import DATASET_ROOT

STATUS = {
    "success": "Concluída",
    "failed": "Falhou",
    "pending": "Pendente",
    "running": "Em execução",
    "partial": "Avaliação parcial",
}
LABELS = {
    "id": "Questão",
    "status": "Estado",
    "faithfulness": "Faithfulness",
    "answer_relevancy": "Relevância da resposta",
    "context_precision": "Precisão do contexto",
    "context_recall": "Recall do contexto",
    "answer_total_tokens": "Tokens da resposta",
    "answer_response_time_seconds": "Tempo da resposta (s)",
    "cost_usd": "Custo registrado (US$)",
    "cost_coverage": "Chamadas com custo",
    "question": "Pergunta",
    "answer": "Resposta gerada",
}


def question_results(database, experiment_id=None, *, dataset=None):
    """Keep every dataset question, including missing results and unknown costs."""
    questions = json.loads((dataset or DATASET_ROOT / "qa_dataset_90.json").read_bytes())
    samples, states, costs = {}, {}, {}
    if experiment_id:
        with connect(database) as db:
            for row in db.execute(
                "SELECT question,payload FROM samples WHERE experiment_id=?", (experiment_id,)
            ):
                samples[row["question"]] = json.loads(row["payload"])
            checkpoint = db.execute(
                "SELECT content FROM artifacts WHERE experiment_id=? AND name='checkpoint.json'",
                (experiment_id,),
            ).fetchone()
            if checkpoint:
                states = json.loads(checkpoint["content"]).get("items", {})
            # Calls are already deduplicated by call_id during ingestion. Include retries
            # across runs, but never charge shared corpus preparation to one question.
            costs = {
                row["question_id"]: dict(row)
                for row in db.execute(
                    "SELECT question_id,SUM(cost) AS cost,COUNT(*) AS calls,"
                    "COUNT(cost) AS known FROM calls WHERE experiment_id=? "
                    "AND COALESCE(stage,'')!='preparation' AND question_id IS NOT NULL "
                    "GROUP BY question_id",
                    (experiment_id,),
                )
            }
    rows = []
    for question in questions:
        state = states.get(question["id"], {})
        sample = samples.get(question["question"], {})
        result, artifact = state.get("result") or {}, state.get("artifact") or {}
        usage = costs.get(question["id"]) or costs.get(sample.get("id")) or {}
        status = sample.get("status", state.get("status", "pending"))
        row = {
            "id": question["id"],
            "status": STATUS.get(status, status),
            "question": question["question"],
            "answer": result.get("answer") or artifact.get("answer"),
            "cost_usd": usage.get("cost"),
            "cost_coverage": (
                f"{usage['known']}/{usage['calls']} chamadas" if usage else "Sem registro"
            ),
        }
        for key in (*METRICS, "answer_total_tokens", "answer_response_time_seconds"):
            row[key] = sample.get(key)
        rows.append(row)
    return pd.DataFrame(rows)


def question_results_view(database, records, *, consumption_only=False):
    prefix = "consumption" if consumption_only else "question-results"
    st.subheader("Consumo por questão" if consumption_only else "Resultados das 90 questões")
    left, right = st.columns(2)
    project = left.selectbox("Arquitetura RAG", list(PROJECTS), key=prefix + "-project")
    candidates = [row for row in records if row["project"] == project]
    experiment_id = None
    external_id = "sem-execucao"
    if candidates:
        by_id = {row["id"]: row for row in candidates}

        def label(key):
            row = by_id[key]
            kind = "Continuação" if row["manifest"].get("continuation") else "Execução"
            return f"{kind} · {row['external_id'][:12]} · {row['summary'].get('success', 0)}/90 concluídas"

        experiment_id = right.selectbox(
            "Experimento", list(by_id), format_func=label, key=prefix + "-experiment-" + project
        )
        external_id = by_id[experiment_id]["external_id"]
    else:
        right.info("Esta arquitetura ainda não possui resultados salvos.")
    frame = question_results(database, experiment_id)
    counts = frame["status"].value_counts()
    st.caption(
        f"{len(frame)} questões · {counts.get('Concluída', 0)} concluídas · "
        f"{counts.get('Falhou', 0)} com falha · "
        f"{len(frame) - counts.get('Concluída', 0) - counts.get('Falhou', 0)} pendentes ou em andamento"
    )
    st.caption(
        "Custo em US$: soma das chamadas registradas da questão, incluindo geração, avaliação "
        "e novas tentativas. Preparação do corpus não é rateada. Registros antigos podem não "
        "ter custo informado; valores ausentes não significam zero. Tokens e tempo referem-se à resposta."
    )
    columns = ["id", "status"]
    if not consumption_only:
        columns.extend(METRICS)
    columns.extend(
        [
            "answer_total_tokens",
            "answer_response_time_seconds",
            "cost_usd",
            "cost_coverage",
            "question",
        ]
    )
    if not consumption_only:
        columns.append("answer")
    config = {key: st.column_config.TextColumn(label) for key, label in LABELS.items()}
    for key in METRICS:
        config[key] = st.column_config.NumberColumn(LABELS[key], format="%.4f")
    config["answer_total_tokens"] = st.column_config.NumberColumn(
        LABELS["answer_total_tokens"], format="%d"
    )
    config["answer_response_time_seconds"] = st.column_config.NumberColumn(
        LABELS["answer_response_time_seconds"], format="%.2f"
    )
    config["cost_usd"] = st.column_config.NumberColumn(
        LABELS["cost_usd"],
        format="$%.8f",
        help="Soma dos custos informados pelo provedor. Pode ser parcial se houver chamadas sem custo.",
    )
    config["question"] = st.column_config.TextColumn("Pergunta", width="large")
    config["answer"] = st.column_config.TextColumn("Resposta gerada", width="large")
    table = frame[columns].copy()
    if not consumption_only:
        summary = {key: None for key in columns}
        summary.update(id="TOTAL / MÉDIA", status="Valores registrados")
        for key in ("answer_total_tokens", "answer_response_time_seconds"):
            summary[key] = pd.to_numeric(frame[key], errors="coerce").sum(min_count=1)
        for key in METRICS:
            summary[key] = pd.to_numeric(frame[key], errors="coerce").mean()
        table = pd.DataFrame([*table.to_dict(orient="records"), summary], columns=columns)
    st.dataframe(
        table,
        column_config=config,
        hide_index=True,
        width="stretch",
        height=600,
        key=prefix + "-table",
    )
    st.caption(
        "Role a tabela para ver todas as colunas. Clique duas vezes na célula para ler o texto completo."
    )
    if not consumption_only:
        coverage = " · ".join(
            f"{LABELS[key]}: n={frame[key].count()}"
            for key in (*METRICS, "answer_total_tokens", "answer_response_time_seconds")
        )
        st.caption(
            "Linha TOTAL / MÉDIA: soma dos tokens e tempos de resposta registrados e média "
            "das notas disponíveis de cada métrica, inclusive avaliações parciais. Notas zero "
            "entram na média; valores ausentes são ignorados. O tempo é a soma das durações "
            "das respostas, sem preparação nem avaliação do juiz."
        )
        st.caption("Quantidade de questões com valores registrados — " + coverage)
    st.download_button(
        "Baixar tabela CSV",
        table.rename(columns=LABELS).to_csv(index=False, sep=";").encode("utf-8-sig"),
        f"{project}-{external_id[:12]}-{prefix}.csv",
        "text/csv",
        key=prefix + "-download",
        on_click="ignore",
    )
