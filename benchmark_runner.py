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


def run_resumable_benchmark(
    project: str,
    answer_question: QuestionHandler,
    evaluate_question: EvaluationHandler,
    *,
    dataset_path: Path = DEFAULT_DATASET,
    output_dir: Path | str = "results",
) -> dict[str, int]:
    """Run pending/failed questions sequentially and checkpoint each attempt."""
    dataset_path = dataset_path.resolve()
    output_dir = Path(output_dir).resolve()
    checkpoint_path = output_dir / "checkpoint.json"
    results_path = output_dir / "results.csv"
    questions = _load_dataset(dataset_path)
    dataset_sha256 = _dataset_hash(dataset_path)
    checkpoint = _load_checkpoint(
        checkpoint_path,
        project,
        dataset_path,
        dataset_sha256,
    )
    states = checkpoint["items"]

    successful = sum(1 for state in states.values() if state.get("status") == "success")
    print(
        f"Dataset: {len(questions)} perguntas | concluidas: {successful} | "
        f"checkpoint: {checkpoint_path}"
    )

    for position, question in enumerate(questions, start=1):
        question_id = str(question["id"])
        previous = states.get(question_id, {})
        if previous.get("status") == "success":
            print(f"[{position}/{len(questions)}] {question_id}: sucesso existente, ignorando.")
            continue

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
                error="Execucao interrompida pelo usuario.",
            )
            checkpoint["updated_at"] = _now()
            _atomic_json(checkpoint_path, checkpoint)
            _write_results_csv(results_path, questions, checkpoint)
            raise
        except Exception as exc:  # noqa: BLE001 - isolate failures per question
            states[question_id].update(
                status="failed",
                finished_at=_now(),
                error=f"{type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(),
            )
            print(f"  FALHA: {type(exc).__name__}: {exc}")
        else:
            states[question_id].update(
                status="success",
                finished_at=_now(),
                error=None,
                result=result,
            )
            states[question_id].pop("traceback", None)
            print("  SUCESSO: checkpoint atualizado.")

        checkpoint["updated_at"] = _now()
        _atomic_json(checkpoint_path, checkpoint)
        _write_results_csv(results_path, questions, checkpoint)

    counts = {"success": 0, "failed": 0, "pending": 0}
    for question in questions:
        status = states.get(str(question["id"]), {}).get("status", "pending")
        if status not in counts:
            status = "pending"
        counts[status] += 1
    print(
        "Resumo: "
        f"{counts['success']} sucesso(s), {counts['failed']} falha(s), "
        f"{counts['pending']} pendente(s)."
    )
    return counts
