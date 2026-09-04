"""Sequential, resumable execution for the shared evaluation dataset."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import traceback
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = REPOSITORY_ROOT / "eval-dataset" / "qa_dataset_90.json"
CHECKPOINT_VERSION = 1
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


def _atomic_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(data, output, ensure_ascii=False, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as dataset_file:
        questions = json.load(dataset_file)
    if not isinstance(questions, list) or not questions:
        raise ValueError(f"Dataset vazio ou invalido: {path}")

    required = {"id", "question", "ground_truth"}
    seen: set[str] = set()
    for index, item in enumerate(questions, start=1):
        missing = required.difference(item)
        if missing:
            raise ValueError(f"Item {index} sem campos obrigatorios: {', '.join(sorted(missing))}")
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
        raise RuntimeError(f"Versao de checkpoint incompativel em {path}")
    if checkpoint.get("project") != project:
        raise RuntimeError(f"Checkpoint pertence a outro pipeline: {path}")
    if checkpoint.get("dataset_sha256") != dataset_sha256:
        raise RuntimeError(
            "O dataset mudou desde a criacao do checkpoint. Mova ou remova "
            f"{path} conscientemente antes de iniciar uma nova avaliacao."
        )
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
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore", delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


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


def run_resumable_benchmark(
    project: str,
    answer_question: QuestionHandler,
    evaluate_question: EvaluationHandler,
    *,
    dataset_path: Path = DEFAULT_DATASET,
    output_dir: Path | str | None = None,
    question_limit: int | None = None,
) -> dict[str, int]:
    """Run up to ``question_limit`` unresolved questions and persist each attempt."""
    dataset_path = dataset_path.resolve()
    question_limit = _question_limit(question_limit)
    configured_output = output_dir or os.getenv("BENCHMARK_OUTPUT_DIR", "results")
    output_dir = Path(configured_output).resolve()
    checkpoint_path = output_dir / "checkpoint.json"
    results_path = output_dir / "results.csv"
    errors_path = output_dir / "errors.json"
    questions = _load_dataset(dataset_path)
    dataset_sha256 = _dataset_hash(dataset_path)
    checkpoint = _load_checkpoint(
        checkpoint_path,
        project,
        dataset_path,
        dataset_sha256,
    )
    states = checkpoint["items"]

    if _recover_interrupted_questions(checkpoint):
        checkpoint["updated_at"] = _now()
        _atomic_json(checkpoint_path, checkpoint)

    _write_results_csv(results_path, questions, checkpoint)
    _write_errors_json(errors_path, questions, checkpoint)

    successful = sum(1 for state in states.values() if state.get("status") == "success")
    limit_description = str(question_limit) if question_limit is not None else "dataset completo"
    print(
        f"Dataset: {len(questions)} perguntas | concluidas: {successful} | "
        f"limite desta rodada: {limit_description} | checkpoint: {checkpoint_path}"
    )

    attempted = 0
    run_success = 0
    run_failed = 0
    positions = {str(question["id"]): index for index, question in enumerate(questions, start=1)}
    failed_questions = [
        question
        for question in questions
        if states.get(str(question["id"]), {}).get("status") == "failed"
    ]
    pending_questions = [
        question
        for question in questions
        if states.get(str(question["id"]), {}).get("status") not in {"success", "failed"}
    ]
    for question in [*failed_questions, *pending_questions]:
        question_id = str(question["id"])
        position = positions[question_id]
        previous = states.get(question_id, {})
        if question_limit is not None and attempted >= question_limit:
            break

        attempted += 1
        attempts = int(previous.get("attempts", 0)) + 1
        states[question_id] = {
            "status": "running",
            "attempts": attempts,
            "question": question["question"],
            "started_at": _now(),
        }
        checkpoint["updated_at"] = _now()
        _atomic_json(checkpoint_path, checkpoint)
        print(f"[{position}/{len(questions)}] {question_id}: tentativa {attempts}")

        try:
            ragas_item = answer_question(question)
            evaluation = evaluate_question(ragas_item)
            result = _json_value(
                {
                    "answer": ragas_item.get("answer", ""),
                    "contexts_count": len(ragas_item.get("contexts", [])),
                    **evaluation,
                }
            )
        except KeyboardInterrupt:
            states[question_id].update(
                status="failed",
                finished_at=_now(),
                error_type="KeyboardInterrupt",
                error_message="Execucao interrompida pelo usuario.",
                error="KeyboardInterrupt: Execucao interrompida pelo usuario.",
            )
            checkpoint["updated_at"] = _now()
            _atomic_json(checkpoint_path, checkpoint)
            _write_results_csv(results_path, questions, checkpoint)
            _write_errors_json(errors_path, questions, checkpoint)
            raise
        except Exception as exc:  # noqa: BLE001 - isolate failures per question
            states[question_id].update(
                status="failed",
                finished_at=_now(),
                error_type=type(exc).__name__,
                error_message=str(exc),
                error=f"{type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(),
            )
            run_failed += 1
            print(f"  FALHA: {type(exc).__name__}: {exc}")
        else:
            states[question_id].update(
                status="success",
                finished_at=_now(),
                error=None,
                result=result,
            )
            states[question_id].pop("error_type", None)
            states[question_id].pop("error_message", None)
            states[question_id].pop("traceback", None)
            run_success += 1
            print("  SUCESSO: checkpoint atualizado.")

        checkpoint["updated_at"] = _now()
        _atomic_json(checkpoint_path, checkpoint)
        _write_results_csv(results_path, questions, checkpoint)
        _write_errors_json(errors_path, questions, checkpoint)

    counts = {"success": 0, "failed": 0, "pending": 0}
    for question in questions:
        status = states.get(str(question["id"]), {}).get("status", "pending")
        if status not in counts:
            status = "pending"
        counts[status] += 1
    print(
        f"Rodada: {attempted} tentativa(s), {run_success} sucesso(s), "
        f"{run_failed} falha(s). Resumo acumulado: "
        f"{counts['success']} sucesso(s), {counts['failed']} falha(s), "
        f"{counts['pending']} pendente(s)."
    )
    counts.update(
        attempted=attempted,
        run_success=run_success,
        run_failed=run_failed,
    )
    return counts
