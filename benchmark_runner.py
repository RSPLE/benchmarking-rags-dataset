from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import signal
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from numbers import Real
from pathlib import Path
from typing import Any

from benchmark_config import METRICS
from benchmark_export import RESULT_COLUMNS, result_rows, write_results
from benchmark_storage import (
    append_event,
    atomic_file,
    atomic_json,
    exclusive_lock,
    fingerprint,
    sanitize,
)

REPOSITORY_ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = REPOSITORY_ROOT / "eval-dataset" / "qa_dataset_90.json"
CHECKPOINT_VERSION = 2
ERRORS_VERSION = 1

QuestionHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]
EvaluationHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dataset_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as dataset_file:
        for block in iter(lambda: dataset_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_value(value: Any) -> Any:
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


_atomic_json = atomic_json


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as dataset_file:
        questions = json.load(dataset_file)
    if not isinstance(questions, list) or not questions:
        raise ValueError(f"Dataset vazio ou invalido: {path}")

    required = {"id", "question", "ground_truth"}
    seen: set[str] = set()
    for index, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Invalid dataset item {index}")
        missing = required.difference(item)
        if missing:
            raise ValueError(f"Item {index} sem campos obrigatorios: {', '.join(sorted(missing))}")
        if any(not isinstance(item[key], str) or not item[key].strip() for key in required):
            raise ValueError(f"Invalid dataset fields at item {index}")
        question_id = str(item["id"])
        if question_id in seen:
            raise ValueError(f"ID duplicado no dataset: {question_id}")
        seen.add(question_id)
    return questions


def _new_checkpoint(project: str, dataset_path: Path, dataset_sha256: str) -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "project": project,
        "dataset": str(dataset_path),
        "dataset_sha256": dataset_sha256,
        "created_at": _now(),
        "updated_at": _now(),
        "items": {},
    }


def _load_checkpoint(
    path: Path,
    project: str,
    dataset_path: Path,
    dataset_sha256: str,
) -> dict[str, Any]:
    if not path.exists():
        return _new_checkpoint(project, dataset_path, dataset_sha256)
    with path.open(encoding="utf-8") as checkpoint_file:
        checkpoint = json.load(checkpoint_file)
    if checkpoint.get("version") != CHECKPOINT_VERSION:
        raise RuntimeError(
            f"Legacy or incompatible checkpoint: {path}. Use migrate; original files must be preserved."
        )
    if checkpoint.get("project") != project:
        raise RuntimeError(f"Checkpoint pertence a outro pipeline: {path}")
    if checkpoint.get("dataset_sha256") != dataset_sha256:
        raise RuntimeError(f"Dataset changed; create a new experiment and preserve {path}.")
    if not isinstance(checkpoint.get("items"), dict):
        raise ValueError("Invalid checkpoint items")
    return checkpoint


def _write_results_csv(
    path: Path,
    questions: list[dict[str, Any]],
    checkpoint: Mapping[str, Any],
) -> None:
    rows: list[dict[str, Any]] = []
    metric_fields: list[str] = []
    items = checkpoint.get("items", {})
    for question in questions:
        state = items.get(str(question["id"]), {})
        if state.get("status") != "success":
            continue
        result = state.get("result", {})
        for key in result:
            if key not in metric_fields and key not in question:
                metric_fields.append(key)
        rows.append({**question, **result})

    fields = ["id", "question", "ground_truth", "source_book"]
    fields.extend(field for field in metric_fields if field not in fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_file(path, encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore", delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def _split_error(state: Mapping[str, Any]) -> tuple[str, str]:
    error_type = str(state.get("error_type") or "Error")
    message = str(state.get("error_message") or "")
    if message:
        return error_type, message

    legacy_error = str(state.get("error") or "Erro sem mensagem.")
    if ": " in legacy_error:
        legacy_type, legacy_message = legacy_error.split(": ", 1)
        return legacy_type, legacy_message
    return error_type, legacy_error


def _write_errors_json(
    path: Path,
    questions: list[dict[str, Any]],
    checkpoint: Mapping[str, Any],
) -> None:
    items = checkpoint.get("items", {})
    errors: list[dict[str, Any]] = []
    for question in questions:
        state = items.get(str(question["id"]), {})
        if state.get("status") != "failed":
            continue
        error_type, message = _split_error(state)
        errors.append(
            {
                "id": str(question["id"]),
                "question": question["question"],
                "attempts": int(state.get("attempts", 0)),
                "started_at": state.get("started_at"),
                "finished_at": state.get("finished_at"),
                "error_type": error_type,
                "message": message,
                "output": str(state.get("error") or f"{error_type}: {message}"),
                "traceback": state.get("traceback"),
            }
        )

    _atomic_json(
        path,
        {
            "version": ERRORS_VERSION,
            "project": checkpoint.get("project"),
            "dataset": checkpoint.get("dataset"),
            "dataset_sha256": checkpoint.get("dataset_sha256"),
            "updated_at": checkpoint.get("updated_at"),
            "count": len(errors),
            "errors": errors,
        },
    )


def _question_limit(configured: int | None) -> int | None:
    if configured is None:
        value = os.getenv("BENCHMARK_QUESTION_LIMIT", "").strip()
        if not value:
            return None
        try:
            configured = int(value)
        except ValueError as exc:
            raise ValueError("BENCHMARK_QUESTION_LIMIT deve ser um inteiro positivo") from exc
    if configured <= 0:
        raise ValueError("O limite de perguntas deve ser um inteiro positivo")
    return configured


def _recover_interrupted_questions(checkpoint: dict[str, Any]) -> bool:
    recovered = False
    for state in checkpoint.get("items", {}).values():
        if state.get("status") != "running":
            continue
        message = "A execução anterior foi interrompida antes de concluir esta pergunta."
        state.update(
            status="failed",
            finished_at=_now(),
            error_type="InterruptedRun",
            error_message=message,
            error=f"InterruptedRun: {message}",
        )
        recovered = True
    return recovered


def validate_metrics(values, required):
    for metric in required:
        value = values.get(metric)
        if hasattr(value, "item"):
            value = value.item()
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
            raise ValueError(f"Invalid metric {metric}: expected a finite number")
        lower = -1 if metric == "answer_relevancy" else 0
        if metric in METRICS and not lower - 1e-9 <= value <= 1 + 1e-9:
            raise ValueError(f"Metric {metric} outside [{lower}, 1]")
    return {metric: float(values[metric]) for metric in required}


def validate_answer(raw, question):
    if not isinstance(raw, Mapping):
        raise ValueError("Answer must be an object")
    answer = raw.get("answer")
    contexts = raw.get("contexts")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("Missing candidate answer")
    if not isinstance(contexts, list) or any(
        not isinstance(c, str) or not c.strip() for c in contexts
    ):
        raise ValueError("Contexts must be a list of nonempty strings")
    for key in ("question", "ground_truth"):
        if key in raw and raw[key] != question[key]:
            raise ValueError(f"Answer has incompatible {key}")
    return _json_value(
        {**raw, "question": question["question"], "ground_truth": question["ground_truth"]}
    )


def error_category(exc):
    cause = exc
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if type(cause).__name__ == "BudgetExceeded":
            return "BudgetExceeded"
        status = getattr(cause, "status_code", None)
        body = str(cause).lower()
        if status in {400, 401, 403, 404, 422} or "key limit exceeded" in body:
            return "configuration"
        if status == 402:
            return "credit"
        if status in {429, 503} or isinstance(cause, (TimeoutError, ConnectionError)):
            return "transient"
        cause = cause.__cause__ or cause.__context__
    return type(exc).__name__


@contextmanager
def pause_signals():
    paused = threading.Event()
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, lambda *_: paused.set())
    try:
        yield paused
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def summary_for(questions, checkpoint):
    counts = {"success": 0, "failed": 0, "pending": 0, "partial": 0}
    scores = {name: [] for name in checkpoint.get("required_metrics", [])}
    for question in questions:
        state = checkpoint["items"].get(str(question["id"]), {})
        status = state.get("status", "pending")
        counts[status if status in ("success", "failed") else "pending"] += 1
        if state.get("artifact") and status != "success":
            counts["partial"] += 1
        for metric, entry in state.get("metrics", {}).items():
            if entry.get("status") == "success":
                scores.setdefault(metric, []).append(
                    validate_metrics({metric: entry["value"]}, [metric])[metric]
                )
    return {
        **counts,
        "metrics": {
            name: {"count": len(values), "mean": sum(values) / len(values) if values else None}
            for name, values in scores.items()
        },
    }


def run_resumable_benchmark(
    project: str,
    answer_question: QuestionHandler,
    evaluate_question: EvaluationHandler | None = None,
    *,
    dataset_path: Path = DEFAULT_DATASET,
    output_dir: Path | str | None = None,
    question_limit: int | None = None,
    metric_evaluators: Mapping[str, EvaluationHandler] | None = None,
    required_metrics=None,
    manifest=None,
    selection="unresolved",
    question_ids=None,
    ledger=None,
) -> dict[str, int]:
    dataset_path = dataset_path.resolve()
    question_limit = _question_limit(question_limit)
    output_dir = Path(output_dir or os.getenv("BENCHMARK_OUTPUT_DIR", "results")).resolve()
    questions = _load_dataset(dataset_path)
    required = tuple(
        required_metrics or (metric_evaluators.keys() if metric_evaluators else ("faithfulness",))
    )
    if not required or (metric_evaluators is not None and set(metric_evaluators) != set(required)):
        raise ValueError("Metric evaluators must match required metrics")
    if evaluate_question is None and not metric_evaluators:
        raise ValueError("An evaluator is required")
    if selection not in {"unresolved", "pending", "failed"}:
        raise ValueError("Invalid selection")
    if question_ids and set(question_ids) - {q["id"] for q in questions}:
        raise ValueError("Unknown question IDs")
    with exclusive_lock(output_dir / ".lock"), pause_signals() as paused:
        if ledger:
            ledger.pause_event = paused
        checkpoint_path = output_dir / "checkpoint.json"
        checkpoint = _load_checkpoint(
            checkpoint_path, project, dataset_path, _dataset_hash(dataset_path)
        )
        identity = fingerprint(manifest) if manifest else None
        if checkpoint.get("manifest_fingerprint") != identity and checkpoint_path.exists():
            raise RuntimeError("Experiment configuration changed; use a new output directory")
        if checkpoint.get("legacy"):
            raise RuntimeError("Legacy archive is read-only; original evidence is incomplete")
        if checkpoint.get("required_metrics", list(required)) != list(required):
            raise RuntimeError("Metric configuration changed")
        checkpoint.update(manifest_fingerprint=identity, required_metrics=list(required))
        if manifest:
            _atomic_json(output_dir / "manifest.json", manifest)
        run_id = ledger.run_id if ledger else uuid.uuid4().hex
        run_started = time.monotonic()
        events_path = output_dir / "events.jsonl"
        states = checkpoint["items"]
        unknown = set(states) - {q["id"] for q in questions}
        if unknown or any(not isinstance(s, dict) for s in states.values()):
            raise ValueError("Checkpoint contains invalid items")
        for question in questions:
            state = states.get(question["id"], {})
            if state.get("status") not in {
                None,
                "pending",
                "running",
                "partial",
                "failed",
                "success",
            }:
                raise ValueError("Invalid checkpoint status")
            for name, entry in state.get("metrics", {}).items():
                if entry.get("status") == "success":
                    validate_metrics({name: entry.get("value")}, [name])
                    if entry.get("input_sha256") != state.get("artifact_sha256"):
                        raise ValueError("Metric input fingerprint mismatch")
            if state.get("status") == "success":
                validate_metrics(state.get("result", {}), required)
            if state.get("artifact"):
                validate_answer(state["artifact"], question)
                if fingerprint(state["artifact"]) != state.get("artifact_sha256"):
                    raise ValueError("Saved answer fingerprint mismatch")

        def event(kind, **values):
            record = {
                "event_id": uuid.uuid4().hex,
                "run_id": run_id,
                "at": _now(),
                "kind": kind,
                **values,
            }
            append_event(events_path, record)
            if kind in {"started", "failed", "finished", "interrupted"}:
                public = {
                    key: value
                    for key, value in record.items()
                    if key
                    in {
                        "event_id",
                        "run_id",
                        "at",
                        "kind",
                        "question_id",
                        "stage",
                        "category",
                        "question_limit",
                        "success",
                        "failed",
                        "pending",
                        "partial",
                        "attempted",
                        "paused",
                    }
                }
                public.update(
                    project=project,
                    experiment_id=(manifest or {}).get("experiment_id"),
                    mode=(manifest or {}).get("mode", "full"),
                )
                append_event(output_dir / "public_events.jsonl", public, mode=0o640)

        def save():
            checkpoint["updated_at"] = _now()
            _atomic_json(checkpoint_path, checkpoint)
            _write_results_csv(output_dir / "results_detailed.csv", questions, checkpoint)
            write_results(output_dir / "results.csv", questions, checkpoint)
            repetition = (manifest or {}).get("configuration", {}).get("BENCHMARK_REPETITION", "1")
            if not str(repetition).isdigit() or int(repetition) < 1:
                raise ValueError("Invalid experiment repetition")
            if not project.replace("-", "").isalnum():
                raise ValueError("Invalid project export name")
            write_results(output_dir / f"{project}-run-{repetition}_1.csv", questions, checkpoint)
            _write_errors_json(output_dir / "errors.json", questions, checkpoint)
            _atomic_json(
                output_dir / "public_results.json",
                {
                    "updated_at": checkpoint["updated_at"],
                    "columns": list(RESULT_COLUMNS),
                    "rows": [
                        {key: row.get(key) for key in ("id", *RESULT_COLUMNS)}
                        for row in result_rows(questions, checkpoint)
                    ],
                },
                mode=0o640,
            )
            _atomic_json(
                output_dir / "summary.json",
                {
                    "project": project,
                    "experiment_id": (manifest or {}).get("experiment_id"),
                    "updated_at": checkpoint["updated_at"],
                    "operation": checkpoint.get("operation"),
                    "run_id": run_id,
                    "mode": (manifest or {}).get("mode", "full"),
                    "elapsed_seconds": time.monotonic() - run_started,
                    "current_question": checkpoint.get("current_question"),
                    "current_stage": checkpoint.get("current_stage"),
                    "alert": checkpoint.get("alert"),
                    **summary_for(questions, checkpoint),
                    "usage": ledger.summary() if ledger else {"cost_usd": None},
                },
                mode=0o640,
            )

        if _recover_interrupted_questions(checkpoint):
            event("interrupted", consumption="unknown", action="resume_saved_stages_only")
        checkpoint["operation"] = "running"
        checkpoint.pop("alert", None)
        print(f"Experimento: {project} | checkpoint: {checkpoint_path}")
        save()
        event("started", project=project, question_limit=question_limit)
        eligible = [q for q in questions if states.get(q["id"], {}).get("status") != "success"]
        if selection == "pending":
            eligible = [q for q in eligible if q["id"] not in states]
        elif selection == "failed":
            eligible = [q for q in eligible if states.get(q["id"], {}).get("status") == "failed"]
        if question_ids:
            eligible = [q for q in eligible if q["id"] in question_ids]
        max_stage_attempts = int(os.getenv("BENCHMARK_MAX_STAGE_ATTEMPTS", "3"))
        cooldown = int(os.getenv("BENCHMARK_RETRY_COOLDOWN_SECONDS", "0"))
        if max_stage_attempts < 1 or cooldown < 0:
            raise ValueError("Invalid retry policy")
        eligible = [
            q
            for q in eligible
            if (
                states.get(q["id"], {}).get("retry_after", 0) <= time.time()
                and max(states.get(q["id"], {}).get("stage_failures", {}).values(), default=0)
                < max_stage_attempts
            )
        ]
        eligible.sort(key=lambda q: states.get(q["id"], {}).get("status") != "failed")
        attempted = run_success = run_failed = consecutive = 0
        previous_category = None
        for question in eligible:
            if question_limit is not None and attempted >= question_limit:
                break
            if paused.is_set() or (output_dir / "pause.request").exists():
                checkpoint["operation"] = "paused"
                break
            question_id = question["id"]
            state = states.setdefault(question_id, {"attempts": 0, "metrics": {}})
            attempted += 1
            state.update(
                status="running",
                attempts=state["attempts"] + 1,
                question=question["question"],
                started_at=_now(),
            )
            stage = "generation"
            checkpoint.update(current_question=question_id, current_stage=stage)
            save()
            try:
                if ledger:
                    ledger.set_stage(question_id, stage)
                if "artifact" not in state:
                    event("stage_started", question_id=question_id, stage=stage)
                    started = time.monotonic()
                    artifact = validate_answer(answer_question(question), question)
                    state.update(
                        artifact=artifact,
                        artifact_sha256=fingerprint(artifact),
                        generation_seconds=time.monotonic() - started,
                        generation_order=1
                        + max(
                            (item.get("generation_order", 0) for item in states.values()), default=0
                        ),
                    )
                    save()
                    event("answer_saved", question_id=question_id)
                artifact = state["artifact"]
                evaluators = metric_evaluators or {"evaluation": evaluate_question}
                for name, evaluator in evaluators.items():
                    names = [name] if metric_evaluators else list(required)
                    if all(state["metrics"].get(n, {}).get("status") == "success" for n in names):
                        continue
                    if paused.is_set() or (output_dir / "pause.request").exists():
                        checkpoint["operation"] = "paused"
                        break
                    stage = name
                    checkpoint["current_stage"] = name
                    if ledger:
                        ledger.set_stage(question_id, "judge", name)
                    for n in names:
                        entry = state["metrics"].setdefault(n, {})
                        entry.update(
                            status="running",
                            attempts=entry.get("attempts", 0) + 1,
                            input_sha256=state["artifact_sha256"],
                        )
                    save()
                    event("stage_started", question_id=question_id, stage=stage)
                    started = time.monotonic()
                    raw = evaluator(artifact)
                    values = validate_metrics(raw, names)
                    for n, value in values.items():
                        state["metrics"][n].update(
                            status="success", value=value, seconds=time.monotonic() - started
                        )
                    save()
                    event("metric_saved", question_id=question_id, metrics=values)
                if checkpoint["operation"] == "paused":
                    state["status"] = "partial"
                    break
                values = {n: state["metrics"][n]["value"] for n in required}
                validate_metrics(values, required)
                usage = {key: value for key, value in artifact.items() if key.startswith("answer_")}
                state.update(
                    status="success",
                    finished_at=_now(),
                    result={
                        "answer": artifact["answer"],
                        "contexts_count": len(artifact["contexts"]),
                        **usage,
                        **values,
                    },
                )
                for key in ("error", "error_type", "error_message", "traceback"):
                    state.pop(key, None)
                run_success += 1
                consecutive = 0
                event("success", question_id=question_id)
            except (Exception, KeyboardInterrupt) as exc:
                failures = state.setdefault("stage_failures", {})
                failures[stage] = failures.get(stage, 0) + 1
                state["retry_after"] = time.time() + cooldown
                category = error_category(exc)
                checkpoint["alert"] = {
                    "question_id": question_id,
                    "stage": stage,
                    "category": category,
                }
                message = sanitize(str(exc))
                state.update(
                    status="failed",
                    finished_at=_now(),
                    failed_stage=stage,
                    error_type=type(exc).__name__,
                    error_message=message,
                    error=f"{type(exc).__name__}: {message}",
                    traceback=sanitize(traceback.format_exc()),
                )
                for entry in state["metrics"].values():
                    if entry.get("status") == "running":
                        entry.update(status="failed", error=message)
                event(
                    "failed",
                    question_id=question_id,
                    stage=stage,
                    category=category,
                    message=message,
                )
                run_failed += 1
                consecutive = consecutive + 1 if category == previous_category else 1
                previous_category = category
                if isinstance(exc, KeyboardInterrupt):
                    checkpoint["operation"] = "paused"
                    raise
                if category in {"configuration", "credit", "BudgetExceeded"} or consecutive >= 3:
                    checkpoint["operation"] = "paused"
                    break
            finally:
                save()
        if checkpoint["operation"] == "running":
            checkpoint["operation"] = "idle"
        checkpoint.update(current_question=None, current_stage=None)
        save()
        counts = summary_for(questions, checkpoint)
        counts.pop("metrics")
        counts.update(
            attempted=attempted,
            run_success=run_success,
            run_failed=run_failed,
            paused=int(checkpoint["operation"] == "paused"),
        )
        event("finished", **counts)
        print(json.dumps(counts, ensure_ascii=False))
        return counts
