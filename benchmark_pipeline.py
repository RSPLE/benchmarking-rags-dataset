from __future__ import annotations

import json
import os
from pathlib import Path

import benchmark_usage
from benchmark_config import METRICS, ROOT, build_manifest, experiment_directory
from benchmark_evaluation import MetricEvaluator
from benchmark_runner import (
    DEFAULT_DATASET,
    _load_dataset,
    run_resumable_benchmark,
    validate_answer,
)
from benchmark_storage import exclusive_lock
from benchmark_usage import UsageLedger


def frozen_answers(path, project, questions):
    rows = json.loads(Path(path).read_text())
    if not isinstance(rows, list) or not rows:
        raise ValueError("Frozen answers must be a nonempty list")
    dataset = {q["id"]: q for q in questions}
    answers = {}
    identities = set()
    for row in rows:
        required = (
            "experiment_id",
            "rag",
            "question_id",
            "question",
            "reference",
            "response",
            "retrieved_contexts",
            "generation_fingerprint",
            "evidence_metadata",
        )
        if not isinstance(row, dict) or any(key not in row for key in required):
            raise ValueError("Incomplete frozen answer")
        key = row["question_id"]
        if row["rag"] != project or key not in dataset or key in answers:
            raise ValueError("Incompatible or duplicate frozen answer")
        if (
            not row["experiment_id"]
            or not row["generation_fingerprint"]
            or not isinstance(row["evidence_metadata"], list)
        ):
            raise ValueError("Frozen answer provenance is required")
        if not row["retrieved_contexts"]:
            raise ValueError("Frozen answer requires actual evidence")
        identities.add((row["experiment_id"], row["generation_fingerprint"]))
        answers[key] = validate_answer(
            {
                "question": row["question"],
                "ground_truth": row["reference"],
                "answer": row["response"],
                "contexts": row["retrieved_contexts"],
                "provenance": {
                    key: row[key]
                    for key in ("experiment_id", "generation_fingerprint", "evidence_metadata")
                },
            },
            dataset[key],
        )
    if len(identities) != 1:
        raise ValueError("Frozen file mixes generation experiments")
    return answers


def execute_pipeline(project, prepare=None):
    dataset = DEFAULT_DATASET
    mode = os.getenv("BENCHMARK_MODE", "full")
    if mode not in {"full", "evaluate"}:
        raise ValueError("Invalid BENCHMARK_MODE")
    frozen_path = os.getenv("BENCHMARK_FROZEN_FILE") if mode == "evaluate" else None
    if mode == "evaluate" and not frozen_path:
        raise ValueError("Evaluation mode requires BENCHMARK_FROZEN_FILE")
    manifest = build_manifest(project, dataset, mode=mode, frozen_path=frozen_path)
    output = experiment_directory(manifest)
    root = Path(os.getenv("BENCHMARK_OUTPUT_DIR", str(ROOT / "resultados")))
    if not root.is_absolute():
        root = ROOT / root
    imported = frozen_answers(frozen_path, project, _load_dataset(dataset)) if frozen_path else None
    prepared = None

    def answer(question):
        nonlocal prepared
        if imported is not None:
            return imported[question["id"]]
        if project == "knowledge-enhanced-rag":
            from benchmark_config import knowledge_identity

            if knowledge_identity() != manifest["knowledge_sha256"]:
                raise ValueError("Knowledge graph changed during experiment")
        if prepared is None:
            ledger.set_stage(question["id"], "preparation")
            prepared = prepare()
        ledger.set_stage(question["id"], "generation")
        return prepared(question)

    with exclusive_lock(root / ".worker.lock"):
        ledger = UsageLedger(output / "usage.jsonl", budget_root=root)
        benchmark_usage.ACTIVE_LEDGER = ledger
        try:
            counts = run_resumable_benchmark(
                project,
                answer,
                dataset_path=dataset,
                metric_evaluators=MetricEvaluator().handlers(),
                required_metrics=METRICS,
                manifest=manifest,
                output_dir=output,
                selection=os.getenv("BENCHMARK_SELECTION", "unresolved"),
                question_ids=list(imported) if imported is not None else None,
                ledger=ledger,
            )
        finally:
            benchmark_usage.ACTIVE_LEDGER = None
    if counts["run_failed"] or counts["paused"]:
        raise SystemExit(1)
    return counts


def tool_evidence(messages):
    contexts = []
    metadata = []
    for message in messages:
        if getattr(message, "type", None) != "tool":
            continue
        if getattr(message, "status", "success") == "error":
            raise ValueError("Agent tool failed")
        content = message.content
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Tool returned invalid evidence")
        contexts.append(content)
        documents = getattr(message, "artifact", None)
        metadata.append(
            {
                "tool": getattr(message, "name", None),
                "tool_call_id": getattr(message, "tool_call_id", None),
                "sources": [doc.metadata for doc in documents if hasattr(doc, "metadata")]
                if isinstance(documents, list)
                else [],
            }
        )
    return contexts, metadata


def critique_decision(text):
    import unicodedata

    normalized = (
        unicodedata.normalize("NFKD", text.strip()).encode("ascii", "ignore").decode().upper()
    )
    normalized = normalized.rstrip(".! ")
    if normalized not in {"SIM", "NAO"}:
        raise ValueError("Invalid self-critique decision")
    return normalized == "SIM"
