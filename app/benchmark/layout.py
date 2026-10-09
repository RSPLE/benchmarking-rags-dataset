from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from app.benchmark.config import ROOT, experiment_directory
from app.benchmark.storage import file_hash, fingerprint

REGISTRY = Path(__file__).with_name("layout_v1.json")


def resume_manifest(current, expected):
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Invalid experiment ID")
    if expected == current["experiment_id"]:
        return current
    location = experiment_directory({**current, "experiment_id": expected})
    saved = json.loads((location / "manifest.json").read_text())
    if (
        saved.get("experiment_id") != expected
        or fingerprint({key: value for key, value in saved.items() if key != "experiment_id"})
        != expected
    ):
        raise ValueError("Saved manifest identity is invalid")
    execution = copy.deepcopy(saved)
    execution.pop("experiment_id")
    execution.pop("continuation", None)
    if execution == {key: value for key, value in current.items() if key != "experiment_id"}:
        return saved
    registry = json.loads(REGISTRY.read_text())
    project = current["project"]
    approved = registry["projects"].get(project)
    if not approved:
        raise ValueError("No verified layout transition for this RAG")
    compatible = [
        {**registry["legacy_shared"], **approved["legacy"]},
        *[{**shared, **approved["current"]} for shared in registry.get("compatible_shared", [])],
    ]
    if saved.get("code") not in compatible:
        raise ValueError("Saved experiment uses a different code revision")
    if current.get("code") != {**registry["current_shared"], **approved["current"]}:
        raise ValueError("Current code differs from the verified layout transition")
    comparable = copy.deepcopy(saved)
    comparable.pop("experiment_id")
    comparable.pop("continuation", None)
    comparable["code"] = current["code"]
    old_index = str(ROOT / "rags" / project / "chroma_v2")
    new_index = str(ROOT / "app" / "rags" / project / "chroma_v2")
    if comparable.get("index", {}).get("path") == old_index:
        if current["index"]["path"] != new_index:
            raise ValueError("Index location differs from the verified layout transition")
        comparable["index"]["path"] = new_index
    if comparable != {key: value for key, value in current.items() if key != "experiment_id"}:
        raise ValueError("Requested experiment is incompatible with current configuration")
    return saved


def transition_record(current, previous):
    return {
        "schema": 1,
        "transition": "app-layout-and-operations-v2",
        "registry_sha256": file_hash(REGISTRY),
        "original_experiment_id": previous["experiment_id"],
        "executed_manifest": current,
    }
