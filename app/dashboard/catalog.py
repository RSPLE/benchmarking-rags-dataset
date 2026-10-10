from __future__ import annotations

import json

from app.benchmark.storage import now
from app.dashboard.database import connect


def set_visibility(database, project, external_id, *, hidden, reason=""):
    """Hide an exact experiment without removing its data or source files."""
    with connect(database) as db, db:
        rows = db.execute(
            "SELECT id,summary FROM experiments WHERE project=? AND external_id=?",
            (project, external_id),
        ).fetchall()
        if len(rows) != 1:
            raise ValueError("Informe um experimento existente e único pelo ID completo")
        row = rows[0]
        if hidden:
            if json.loads(row["summary"]).get("operation") == "running":
                raise ValueError("Não é possível ocultar uma execução em andamento")
            db.execute(
                "INSERT OR REPLACE INTO hidden_experiments VALUES (?,?,?)",
                (row["id"], reason, now()),
            )
        else:
            db.execute("DELETE FROM hidden_experiments WHERE experiment_id=?", (row["id"],))
    return row["id"]
