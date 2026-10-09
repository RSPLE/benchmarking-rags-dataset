from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import Counter
from datetime import datetime

from app.benchmark.export import RESULT_COLUMNS, USAGE_COLUMNS
from app.benchmark.storage import fingerprint, now
from app.dashboard.database import connect

METRICS = RESULT_COLUMNS[1:5]
FILES = (
    "results.csv",
    "results_detailed.csv",
    "checkpoint.json",
    "manifest.json",
    "summary.json",
    "public_results.json",
    "usage.jsonl",
    "events.jsonl",
    "public_events.jsonl",
    "errors.json",
)


def numeric(value, *, minimum=0):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not a measurement")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError("Invalid measurement")
    return result


def csv_rows(content):
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    if not reader.fieldnames or set(RESULT_COLUMNS) - set(reader.fieldnames):
        raise ValueError("CSV does not contain the original nine result columns")
    rows = []
    for row in reader:
        if not row.get("question") or None in row:
            raise ValueError("Invalid CSV row")
        for name in RESULT_COLUMNS[1:]:
            row[name] = numeric(row.get(name), minimum=-1 if name == "answer_relevancy" else 0)
            if name in METRICS and (row[name] is None or row[name] > 1 + 1e-9):
                raise ValueError("Missing or invalid RAGAS score")
        rows.append(row)
    return rows


def json_lines(content):
    lines = content.decode().splitlines(keepends=True)
    rows = []
    for index, line in enumerate(lines):
        if index == len(lines) - 1 and not line.endswith("\n"):
            break
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_source(path, boundary):
    directory = path.parent
    if not directory.resolve().is_relative_to(boundary.resolve()):
        raise ValueError("Source escapes its configured directory")
    files = {}
    for name in FILES:
        candidate = directory / name
        if candidate.is_file():
            if candidate.is_symlink() or not candidate.resolve().is_relative_to(boundary.resolve()):
                raise ValueError("Symbolic artifact paths are not supported")
            files[name] = candidate.read_bytes()
    if path.name != "results.csv":
        files = {path.name: path.read_bytes()}
    checkpoint = json.loads(files.get("checkpoint.json", b"{}"))
    manifest = json.loads(files.get("manifest.json", b"{}"))
    summary = json.loads(files.get("summary.json", b"{}"))
    if summary and checkpoint and summary.get("updated_at") != checkpoint.get("updated_at"):
        raise ValueError("Snapshot is being updated; retry on next synchronization")
    rows = csv_rows(files[path.name])
    if summary and len(rows) != summary.get("success"):
        raise ValueError("CSV and summary are not yet consistent")
    if checkpoint and (directory / "checkpoint.json").read_bytes() != files["checkpoint.json"]:
        raise ValueError("Checkpoint changed during synchronization")
    return files, checkpoint, manifest, summary, rows


def source_paths(settings):
    for path in sorted(settings.results.glob("*/*/results.csv")):
        yield path, settings.results, path.parents[1].name, "v2"
    for directory in sorted(settings.legacy.glob("*/results*")):
        if not directory.is_dir():
            continue
        primary = directory / "results.csv"
        paths = [primary] if primary.exists() else sorted(directory.glob("*-rag-run-*.csv"))
        seen = set()
        for path in paths:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest not in seen:
                yield path, settings.legacy, directory.parent.name, "legacy"
                seen.add(digest)


def sample_rows(rows, checkpoint):
    by_question = {}
    for key, state in checkpoint.get("items", {}).items():
        question = (
            state.get("question")
            or state.get("result", {}).get("question")
            or state.get("artifact", {}).get("question")
        )
        if question:
            by_question[question] = key
    result = {}
    for position, row in enumerate(rows):
        question_id = (
            row.get("id")
            or by_question.get(row["question"])
            or "text:" + hashlib.sha256(row["question"].encode()).hexdigest()[:16]
        )
        if question_id in result:
            raise ValueError("Duplicate question in the same experiment")
        result[question_id] = {**row, "id": question_id, "position": position, "status": "success"}
    for position, (key, state) in enumerate(checkpoint.get("items", {}).items()):
        if key in result:
            continue
        artifact = state.get("artifact", {})
        question = (
            state.get("question")
            or artifact.get("question")
            or state.get("result", {}).get("question", key)
        )
        row = {
            "id": key,
            "question": question,
            "position": position,
            "status": state.get("status", "pending"),
        }
        row.update({name: numeric(artifact.get(name)) for name in USAGE_COLUMNS})
        for metric in METRICS:
            entry = state.get("metrics", {}).get(metric, {})
            row[metric] = entry.get("value") if entry.get("status") == "success" else None
        result[key] = row
    return list(result.values())


def comparison_identity(manifest):
    if not manifest:
        return "legacy-unknown"
    configuration = manifest.get("configuration", {})
    current = fingerprint(
        {
            "dataset": manifest.get("dataset_sha256"),
            "corpus": sorted(entry["sha256"] for entry in manifest.get("corpus", [])),
            "mode": manifest.get("mode"),
            "policy": manifest.get("evidence_policy"),
            "settings": {
                key: value
                for key, value in configuration.items()
                if "MODEL" in key
                or "PROVIDER" in key
                or key in {"LLM_MAX_TOKENS", "RAGAS_MAX_TOKENS"}
            },
        }
    )
    if manifest.get("continuation"):
        return "continuation-" + fingerprint(
            {"current": current, "source": manifest["continuation"]["source_sha256"]}
        )
    return current


def merge_calls(events):
    calls = {}
    for event in events:
        key = event.get("call_id")
        if not key:
            raise ValueError("Usage event lacks call ID")
        previous = calls.get(key, {})
        if previous.get("kind") == "reconciled" and event.get("kind") != "reconciled":
            continue
        if previous and event.get("kind") == "started":
            continue
        calls[key] = {**previous, **event}
    return calls


def store_source(db, path, boundary, project, origin):
    files, checkpoint, manifest, summary, rows = load_source(path, boundary)
    revision = fingerprint(
        {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
    )
    source = str(path.resolve())
    current = db.execute("SELECT id,revision FROM experiments WHERE source=?", (source,)).fetchone()
    relocated = False
    if current is None and origin == "legacy" and boundary.parent.name == "app":
        prior_source = str(boundary.parent.parent / "rags" / path.relative_to(boundary))
        current = db.execute(
            "SELECT id,revision FROM experiments WHERE source=? AND project=? AND origin='legacy'",
            (prior_source, project),
        ).fetchone()
        if current:
            if current["revision"] != revision:
                raise ValueError("Historical source changed during layout transition")
            db.execute("UPDATE experiments SET source=? WHERE id=?", (source, current["id"]))
            relocated = True
    identifier = current["id"] if current else fingerprint({"project": project, "source": source})
    if current and current["revision"] == revision:
        db.execute("DELETE FROM sync_errors WHERE source=?", (str(path),))
        return relocated
    external_id = manifest.get("experiment_id") or "legacy-" + identifier[:12]
    configuration = manifest.get("configuration", {})
    prefix = configuration.get("LLM_PROVIDER", "").upper()
    model = configuration.get(f"{prefix}_MODEL") or "Não registrado"
    judge = configuration.get(f"{prefix}_JUDGE_MODEL") or model
    if manifest.get("continuation"):
        model = "Continuação: " + model + " · anteriores não registrados"
        judge = "Continuação: " + judge + " · anteriores não registrados"
    samples = sample_rows(rows, checkpoint)
    if not summary:
        counts = Counter(item["status"] for item in samples)
        summary = {
            "operation": "historical",
            **{key: counts.get(key, 0) for key in ("success", "failed", "pending")},
        }
    values = (
        identifier,
        project,
        external_id,
        str(path),
        origin,
        model,
        judge,
        comparison_identity(manifest),
        json.dumps(manifest),
        json.dumps(summary),
        revision,
        now(),
    )
    db.execute(
        "INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "model=excluded.model,judge_model=excluded.judge_model,comparison_key=excluded.comparison_key,"
        "manifest=excluded.manifest,summary=excluded.summary,revision=excluded.revision,imported_at=excluded.imported_at",
        values,
    )
    for row in samples:
        db.execute(
            "INSERT INTO samples VALUES (?,?,?,?,?,?) ON CONFLICT(experiment_id,question_id) DO UPDATE SET "
            "position=excluded.position,question=excluded.question,status=excluded.status,payload=excluded.payload",
            (
                identifier,
                row["id"],
                row["position"],
                row["question"],
                row["status"],
                json.dumps(row, ensure_ascii=False, allow_nan=False),
            ),
        )
    for name, content in files.items():
        db.execute(
            "INSERT INTO artifacts VALUES (?,?,?,?) ON CONFLICT(experiment_id,name) DO UPDATE SET sha256=excluded.sha256,content=excluded.content",
            (identifier, name, hashlib.sha256(content).hexdigest(), content),
        )
    for call_id, call in merge_calls(json_lines(files.get("usage.jsonl", b""))).items():
        usage = call.get("usage") or {}
        db.execute(
            "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(experiment_id,call_id) DO UPDATE SET "
            "model=excluded.model,kind=excluded.kind,tokens=excluded.tokens,cost=excluded.cost,seconds=excluded.seconds,payload=excluded.payload",
            (
                identifier,
                call_id,
                call.get("question_id"),
                call.get("metric") or call.get("stage"),
                call.get("actual_model") or call.get("model"),
                call.get("kind"),
                numeric(usage.get("total_tokens")),
                numeric(call.get("cost_usd")),
                numeric(call.get("seconds")),
                json.dumps(call),
            ),
        )
    runs = {}
    for event in json_lines(files.get("events.jsonl", b"")):
        if event.get("kind") in {"started", "finished"}:
            runs.setdefault(event["run_id"], {})[event["kind"]] = event["at"]
    for run_id, times in runs.items():
        seconds = None
        if "started" in times and "finished" in times:
            seconds = max(
                0,
                (
                    datetime.fromisoformat(times["finished"])
                    - datetime.fromisoformat(times["started"])
                ).total_seconds(),
            )
        db.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?) ON CONFLICT(experiment_id,run_id) DO UPDATE SET started_at=excluded.started_at,finished_at=excluded.finished_at,seconds=excluded.seconds",
            (identifier, run_id, times.get("started"), times.get("finished"), seconds),
        )
    db.execute("INSERT OR IGNORE INTO revisions VALUES (?,?,?)", (identifier, revision, now()))
    db.execute("DELETE FROM sync_errors WHERE source=?", (str(path),))
    return True


def synchronize(settings):
    changed = 0
    with connect(settings.database) as db:
        for path, boundary, project, origin in source_paths(settings):
            try:
                with db:
                    changed += int(store_source(db, path, boundary, project, origin))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                with db:
                    db.execute(
                        "INSERT INTO sync_errors VALUES (?,?,?) ON CONFLICT(source) DO UPDATE SET error=excluded.error,at=excluded.at",
                        (
                            str(path),
                            f"{type(exc).__name__}: invalid or changing source; previous data retained",
                            now(),
                        ),
                    )
        with db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_sync',?)", (now(),))
    return changed
