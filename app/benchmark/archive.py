from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

from app.benchmark.storage import atomic_json, file_hash


def archive_bytes(files):
    buffer = io.BytesIO()
    checksums = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, content)
        archive.writestr("checksums.json", json.dumps(checksums, sort_keys=True).encode())
    return buffer.getvalue()


def write_binary(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd, temporary = tempfile.mkstemp(prefix=".archive-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def create_run_archive(directory, run_id, dataset_path):
    directory = Path(directory)
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Invalid run archive ID")
    summary = json.loads((directory / "summary.json").read_text())
    manifest = json.loads((directory / "manifest.json").read_text())
    if summary.get("run_id") != run_id or summary.get("operation") not in {"idle", "paused"}:
        raise ValueError("Cannot archive an unfinished or different run")
    names = (
        "checkpoint.json",
        "manifest.json",
        "summary.json",
        "results.csv",
        "results_detailed.csv",
        "public_results.json",
        "errors.json",
        "usage.jsonl",
        "events.jsonl",
        "public_events.jsonl",
        "judge_responses.jsonl",
        "judge_reviews.jsonl",
    )
    files = {
        name: (directory / name).read_bytes() for name in names if (directory / name).is_file()
    }
    files["dataset.json"] = Path(dataset_path).read_bytes()
    private_path = directory / "archives" / f"{run_id}.zip"
    if private_path.exists():
        raise FileExistsError("Run archive already exists")
    write_binary(private_path, archive_bytes(files))
    provenance = {
        name: manifest.get(name)
        for name in (
            "project",
            "experiment_id",
            "mode",
            "dataset_sha256",
            "evidence_policy",
            "question_memory_policy",
            "result_schema",
            "corpus",
            "continuation",
        )
    }
    public = {name: files[name] for name in ("results.csv", "public_results.json", "summary.json")}
    public["provenance.json"] = json.dumps(provenance, ensure_ascii=False, indent=2).encode()
    destination = directory / "deliveries" / run_id
    write_binary(destination / "results.zip", archive_bytes(public), mode=0o640)
    atomic_json(
        destination / "metadata.json",
        {
            "project": summary["project"],
            "experiment_id": summary["experiment_id"],
            "run_id": run_id,
            "sha256": file_hash(destination / "results.zip"),
            "success": summary["success"],
            "failed": summary["failed"],
            "pending": summary["pending"],
            "operation": summary["operation"],
        },
        mode=0o640,
    )
    return private_path


def restore_archive(source, destination):
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("Restore requires a new directory")
    with zipfile.ZipFile(source) as archive:
        checksums = json.loads(archive.read("checksums.json"))
        if set(archive.namelist()) != {*checksums, "checksums.json"}:
            raise ValueError("Archive file inventory mismatch")
        files = {}
        for name, expected in checksums.items():
            if not name or Path(name).name != name or name in {".", ".."}:
                raise ValueError("Unsafe archive path")
            content = archive.read(name)
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError("Archive checksum mismatch")
            files[name] = content
    destination.mkdir(parents=True, mode=0o700)
    for name, content in files.items():
        write_binary(destination / name, content)
    return len(files)
