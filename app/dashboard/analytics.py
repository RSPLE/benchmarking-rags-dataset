from __future__ import annotations

import json

import pandas as pd

from app.dashboard.database import connect


def experiments(database):
    with connect(database) as db:
        rows = [
            dict(row)
            for row in db.execute("SELECT * FROM experiments ORDER BY project, imported_at DESC")
        ]
    for row in rows:
        row["summary"] = json.loads(row["summary"])
        row["manifest"] = json.loads(row["manifest"])
    return rows


def frames(database, identifiers):
    if not identifiers:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    placeholders = ",".join("?" for _ in identifiers)
    with connect(database) as db:
        rows = []
        for row in db.execute(
            f"SELECT s.*, e.project,e.external_id,e.model FROM samples s JOIN experiments e ON e.id=s.experiment_id WHERE e.id IN ({placeholders}) ORDER BY e.project,s.position",
            identifiers,
        ):
            rows.append(
                {
                    **json.loads(row["payload"]),
                    "experiment_id": row["experiment_id"],
                    "project": row["project"],
                    "model": row["model"],
                    "external_id": row["external_id"],
                }
            )
        calls = pd.read_sql_query(
            f"SELECT c.*,e.project FROM calls c JOIN experiments e ON e.id=c.experiment_id WHERE e.id IN ({placeholders})",
            db,
            params=identifiers,
        )
        runs = pd.read_sql_query(
            f"SELECT r.*,e.project FROM runs r JOIN experiments e ON e.id=r.experiment_id WHERE e.id IN ({placeholders})",
            db,
            params=identifiers,
        )
    return pd.DataFrame(rows), calls, runs


def common_questions(frame):
    if frame.empty:
        return frame
    count = frame["experiment_id"].nunique()
    coverage = frame.groupby("question")["experiment_id"].nunique()
    return frame[frame["question"].isin(coverage[coverage == count].index)].copy()


def response_totals(frame):
    if frame.empty:
        return pd.DataFrame()
    fields = ["answer_total_tokens", "answer_response_time_seconds"]
    data = frame.copy()
    for field in fields:
        data[field] = pd.to_numeric(data[field], errors="coerce")
    grouped = data.groupby("project", sort=True)
    result = (
        grouped[fields].sum(min_count=1).rename(columns={fields[0]: "tokens", fields[1]: "seconds"})
    )
    result["mean_tokens"] = grouped[fields[0]].mean()
    result["mean_seconds"] = grouped[fields[1]].mean()
    result["token_cases"] = grouped[fields[0]].count()
    result["time_cases"] = grouped[fields[1]].count()
    return result.reset_index()


def extrema(frame, metric):
    if frame.empty:
        return pd.DataFrame()
    data = frame.copy()
    data[metric] = pd.to_numeric(data[metric], errors="coerce")
    data = data.dropna(subset=[metric])
    if data.empty:
        return data
    rows = []
    for project, group in data.groupby("project"):
        for label, value in (("Menor", group[metric].min()), ("Maior", group[metric].max())):
            matches = group[group[metric] == value]
            for _, row in matches.iterrows():
                rows.append(
                    {
                        "RAG": project,
                        "Extremo": label,
                        "Questão": row["id"],
                        "Pergunta": row["question"],
                        "Valor": value,
                        "Experimento": row["external_id"],
                    }
                )
    return pd.DataFrame(rows)
