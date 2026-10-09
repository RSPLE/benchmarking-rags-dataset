from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from app.benchmark.config import METRICS, build_manifest, experiment_directory
from app.benchmark.export import RESULT_COLUMNS, result_rows, write_results
from app.benchmark.runner import (
    DEFAULT_DATASET,
    _load_dataset,
    _write_errors_json,
    _write_results_csv,
    summary_for,
    validate_metrics,
)
from app.benchmark.storage import atomic_json, exclusive_lock, file_hash, fingerprint, now
from app.paths import ROOT


def continue_checkpoint(source, manifest, *, dataset=DEFAULT_DATASET, apply=False):
    source, dataset = Path(source), Path(dataset)
    original = source.read_bytes()
    checkpoint = json.loads(original)
    questions = _load_dataset(dataset)
    by_id = {q["id"]: q for q in questions}
    if checkpoint.get("version") != 1 or not isinstance(checkpoint.get("items"), dict):
        raise ValueError("Expected a version 1 checkpoint")
    if checkpoint.get("project") != manifest["project"] or manifest.get("mode") != "full":
        raise ValueError("Continuation requires the same RAG in full mode")
    if checkpoint.get("dataset_sha256") != file_hash(dataset) or manifest[
        "dataset_sha256"
    ] != file_hash(dataset):
        raise ValueError("The source checkpoint must match the current dataset exactly")
    if set(checkpoint["items"]) - set(by_id):
        raise ValueError("Checkpoint contains unknown question IDs")
    source_hash = file_hash(source)
    items = {}
    retained = []
    for key, saved in checkpoint["items"].items():
        question = by_id[key]
        if not isinstance(saved, dict) or saved.get("status") not in {
            "success",
            "failed",
            "running",
            "pending",
        }:
            raise ValueError(f"Invalid source state: {key}")
        if saved.get("question") != question["question"]:
            raise ValueError(f"Question text differs from the dataset: {key}")
        state = copy.deepcopy(saved)
        state.update(metrics={}, stage_failures={}, retry_after=0)
        state["attempts"] = int(saved.get("attempts", 0))
        if state["attempts"] < 0:
            raise ValueError("Invalid attempt count")
        state["provenance"] = {
            "kind": "legacy_v1",
            "source_sha256": source_hash,
            "configuration": "unknown",
            "evidence": "contexts_not_recorded",
        }
        if state["status"] == "success":
            validate_metrics(state.get("result", {}), METRICS)
            if state["result"].get("question") != question["question"]:
                raise ValueError(f"Result question differs from the dataset: {key}")
            retained.append(key)
        else:
            state["status"] = "failed"
            state["failed_stage"] = "generation"
            state.setdefault("error_type", "LegacyIncomplete")
            state.setdefault("error_message", "Questão incompleta no checkpoint anterior.")
        items[key] = state
    manifest = copy.deepcopy(manifest)
    manifest.pop("experiment_id", None)
    manifest["continuation"] = {
        "source_version": 1,
        "source_sha256": source_hash,
        "retained_success_ids": sorted(retained),
        "previous_configuration": "unknown",
        "previous_evidence": "contexts_not_recorded",
    }
    manifest["experiment_id"] = fingerprint(manifest)
    destination = experiment_directory(manifest)
    updated = now()
    migrated = {
        "version": 2,
        "project": manifest["project"],
        "dataset": str(dataset.resolve()),
        "dataset_sha256": manifest["dataset_sha256"],
        "manifest_fingerprint": fingerprint(manifest),
        "required_metrics": list(METRICS),
        "created_at": checkpoint.get("created_at", updated),
        "updated_at": updated,
        "operation": "idle",
        "items": items,
        "continuation": manifest["continuation"],
    }
    summary = {
        "project": manifest["project"],
        "experiment_id": manifest["experiment_id"],
        "updated_at": updated,
        "operation": "idle",
        "run_id": None,
        "mode": "full",
        "elapsed_seconds": 0,
        "usage": {"cost_usd": None},
        **summary_for(questions, migrated),
    }
    report = {
        "project": manifest["project"],
        "experiment_id": manifest["experiment_id"],
        "source_sha256": source_hash,
        "destination": str(destination),
        "resumable": True,
        "preserved_successes": len(retained),
        "failed": summary["failed"],
        "pending": summary["pending"],
        "applied": False,
    }
    if not apply:
        return report
    root = destination.parents[1]
    root.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(root / ".worker.lock"), exclusive_lock(root / ".continuation.lock"):
        if destination.exists():
            saved = json.loads((destination / "manifest.json").read_text())
            if (
                saved != manifest
                or file_hash(destination / "original.checkpoint.json") != source_hash
            ):
                raise ValueError("Existing continuation does not match the verified source")
            return {**report, "applied": True, "already_exists": True}
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".continuation-", dir=destination.parent
        ) as temporary:
            stage = Path(temporary)
            backup = stage / "original.checkpoint.json"
            backup.write_bytes(original)
            backup.chmod(0o600)
            if file_hash(backup) != source_hash:
                raise ValueError("Source changed during continuation import")
            atomic_json(stage / "manifest.json", manifest)
            atomic_json(stage / "checkpoint.json", migrated)
            write_results(stage / "results.csv", questions, migrated)
            _write_results_csv(stage / "results_detailed.csv", questions, migrated)
            _write_errors_json(stage / "errors.json", questions, migrated)
            atomic_json(
                stage / "public_results.json",
                {
                    "updated_at": updated,
                    "columns": list(RESULT_COLUMNS),
                    "continuation": manifest["continuation"],
                    "rows": [
                        {name: row.get(name) for name in ("id", *RESULT_COLUMNS)}
                        for row in result_rows(questions, migrated)
                    ],
                },
                mode=0o640,
            )
            atomic_json(stage / "summary.json", summary, mode=0o640)
            atomic_json(stage / "continuation.json", {**report, "applied": True})
            stage.chmod(0o750)
            os.replace(stage, destination)
    return {**report, "applied": True}


def main():
    parser = argparse.ArgumentParser(
        description="Continue verified v1 checkpoints without repeating successes"
    )
    parser.add_argument("projects", nargs="+", choices=["context-rag", "graph-rag", "hybrid-rag"])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    for project in args.projects:
        environment = ROOT / "app/rags" / project / ".venv"
        if Path(sys.prefix).resolve() != environment.resolve():
            subprocess.run(
                [
                    str(environment / "bin/python"),
                    "-m",
                    "app.tools.continue_legacy",
                    project,
                    *(["--apply"] if args.apply else []),
                ],
                cwd=ROOT,
                check=True,
            )
            continue
        manifest = build_manifest(project, DEFAULT_DATASET)
        print(
            json.dumps(
                continue_checkpoint(
                    ROOT / "app/rags" / project / "results/checkpoint.json",
                    manifest,
                    apply=args.apply,
                ),
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
