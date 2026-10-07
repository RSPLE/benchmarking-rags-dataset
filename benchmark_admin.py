from __future__ import annotations

import copy
import json
import shutil
from collections import Counter
from pathlib import Path

from benchmark_config import METRICS, ROOT, corpus_inventory
from benchmark_runner import DEFAULT_DATASET, _load_dataset, validate_metrics
from benchmark_storage import atomic_json, exclusive_lock, file_hash


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
