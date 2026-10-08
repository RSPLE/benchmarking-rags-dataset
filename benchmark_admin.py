from __future__ import annotations

import copy
import json
import shutil
from collections import Counter
from pathlib import Path

from benchmark_config import METRICS, ROOT, corpus_inventory
from benchmark_export import ARTIFACT_EXTRAS
from benchmark_runner import DEFAULT_DATASET, _load_dataset, validate_metrics
from benchmark_storage import atomic_json, exclusive_lock, file_hash, fingerprint


def audit():
    report = {
        "dataset_sha256": file_hash(DEFAULT_DATASET),
        "questions": len(_load_dataset(DEFAULT_DATASET)),
        "rags": {},
    }
    for directory in sorted((ROOT / "rags").iterdir()):
        if not directory.is_dir():
            continue
        docs = directory / (
            "data/apostilas" if directory.name == "knowledge-enhanced-rag" else "docs"
        )
        entry = {
            "corpus": corpus_inventory(docs),
            "environment_exists": (directory / ".venv").is_dir(),
            "lock_sha256": file_hash(directory / "uv.lock"),
        }
        path = directory / "results" / "checkpoint.json"
        if path.exists():
            checkpoint = json.loads(path.read_text())
            items = checkpoint["items"]
            entry.update(
                states=dict(Counter(item["status"] for item in items.values())),
                errors=dict(
                    Counter(
                        item.get("error_type", "unknown")
                        for item in items.values()
                        if item["status"] == "failed"
                    )
                ),
                checkpoint_sha256=file_hash(path),
                dataset_compatible=checkpoint["dataset_sha256"] == report["dataset_sha256"],
            )
        report["rags"][directory.name] = entry
    return report


def migrate(source, destination, *, apply=False):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination in (source, source.parent):
        raise ValueError("Migration requires an independent destination")
    checkpoint = json.loads(source.read_text())
    if checkpoint.get("version") != 1 or not isinstance(checkpoint.get("items"), dict):
        raise ValueError("Expected a version 1 checkpoint")
    invalid = []
    migrated = copy.deepcopy(checkpoint)
    migrated.update(
        version=2,
        legacy=True,
        provenance={"configuration": "unknown", "source_sha256": file_hash(source)},
        required_metrics=list(METRICS),
    )
    for key, state in migrated["items"].items():
        state["legacy_evidence"] = "contexts_not_recorded"
        if state.get("status") == "success":
            try:
                validate_metrics(state.get("result", {}), METRICS)
            except ValueError:
                invalid.append(key)
                state["legacy_status"] = state["status"]
                state["status"] = "invalid_legacy"
    report = {
        "source": str(source),
        "destination": str(destination),
        "items": len(migrated["items"]),
        "invalid_successes": invalid,
        "configuration": "unknown",
        "resumable": False,
        "applied": apply,
    }
    if apply:
        with exclusive_lock(destination.parent / ".migration.lock"):
            if destination.exists():
                raise FileExistsError(destination)
            destination.mkdir(parents=True)
            shutil.copy2(source, destination / "original.checkpoint.json")
            if file_hash(source) != file_hash(destination / "original.checkpoint.json"):
                raise RuntimeError("Backup verification failed")
            atomic_json(destination / "checkpoint.json", migrated)
            atomic_json(destination / "migration.json", report)
    return report


def status(directory):
    directory = Path(directory)
    path = directory / "summary.json"
    if path.exists():
        return json.loads(path.read_text())
    path = directory / "checkpoint.json"
    if not path.exists():
        return {"operation": "not_started"}
    checkpoint = json.loads(path.read_text())
    return {
        "project": checkpoint["project"],
        "version": checkpoint["version"],
        "states": dict(Counter(item["status"] for item in checkpoint["items"].values())),
    }


def export_frozen(directory, destination):
    from benchmark_pipeline import frozen_answers
    from benchmark_runner import validate_answer

    directory, destination = Path(directory).resolve(), Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    with exclusive_lock(directory / ".lock"):
        checkpoint = json.loads((directory / "checkpoint.json").read_text())
        manifest = json.loads((directory / "manifest.json").read_text())
        if (
            checkpoint.get("legacy")
            or checkpoint.get("version") != 2
            or manifest.get("mode") != "full"
        ):
            raise ValueError("Only experiments with saved evidence can be exported")
        if checkpoint.get("manifest_fingerprint") != fingerprint(manifest):
            raise ValueError("Manifest identity mismatch")
        questions = _load_dataset(DEFAULT_DATASET)
        if checkpoint["dataset_sha256"] != file_hash(DEFAULT_DATASET):
            raise ValueError("Dataset identity mismatch")
        rows = []
        for question in questions:
            state = checkpoint["items"].get(question["id"], {})
            if "artifact" not in state:
                continue
            artifact = validate_answer(state["artifact"], question)
            if fingerprint(artifact) != state.get("artifact_sha256"):
                raise ValueError("Saved answer identity mismatch")
            if not artifact.get("contexts") or not isinstance(
                artifact.get("evidence_metadata", []), list
            ):
                raise ValueError("Saved evidence is incomplete")
            rows.append(
                {
                    "experiment_id": manifest["experiment_id"],
                    "rag": manifest["project"],
                    "question_id": question["id"],
                    "question": question["question"],
                    "reference": question["ground_truth"],
                    "response": artifact["answer"],
                    "retrieved_contexts": artifact["contexts"],
                    "generation_fingerprint": state["artifact_sha256"],
                    "evidence_metadata": artifact.get("evidence_metadata", []),
                    **{key: artifact[key] for key in ARTIFACT_EXTRAS if key in artifact},
                }
            )
        if not rows:
            raise ValueError("No saved answers with evidence")
        for row in rows:
            row["generation_fingerprint"] = fingerprint(
                {"experiment": manifest["experiment_id"], "policy": manifest["evidence_policy"]}
            )
        atomic_json(destination, rows)
        frozen_answers(destination, manifest["project"], questions)
    return {"path": str(destination), "cases": len(rows), "sha256": file_hash(destination)}


def report(root):
    from benchmark_usage import historical_usage

    root = Path(root).resolve()
    experiments = []
    for path in sorted(root.glob("*/*/summary.json")):
        if path.resolve().is_relative_to(root):
            summary = json.loads(path.read_text())
            experiments.append({"directory": str(path.parent), **summary})
    cost, unknown = historical_usage(root)
    return {
        "experiments": experiments,
        "utc_day_known_cost_usd": cost,
        "unresolved_calls": unknown,
        "comparison_policy": "Compare matching datasets, evidence policies and judge configurations; report each metric's coverage",
    }
