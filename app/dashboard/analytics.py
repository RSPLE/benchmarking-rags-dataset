from __future__ import annotations

import json

import pandas as pd

from app.dashboard.database import connect


def experiments(database):
    with connect(database) as db:
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM experiments WHERE id NOT IN "
                "(SELECT experiment_id FROM hidden_experiments) ORDER BY project, imported_at DESC"
            )
        ]
        source_hashes = {
            row["experiment_id"]: row["sha256"]
            for row in db.execute(
                "SELECT experiment_id,sha256 FROM artifacts WHERE name='checkpoint.json'"
            )
        }
    for row in rows:
        row["summary"] = json.loads(row["summary"])
        row["manifest"] = json.loads(row["manifest"])
    continued = {
        (row["project"], row["manifest"]["continuation"]["source_sha256"])
        for row in rows
        if row["manifest"].get("continuation")
    }
    return [
        row
        for row in rows
        if row["origin"] != "legacy"
        or (row["project"], source_hashes.get(row["id"])) not in continued
    ]


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
