from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    files = {
        path.relative_to(source): path
        for path in source.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    }
    if len(files) != 7:
        raise ValueError("The approved corpus requires exactly seven PDFs")
    existing = {
        path.relative_to(destination)
        for path in destination.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    }
    if existing - set(files):
        raise ValueError("Destination contains another corpus; preserve it and use a separate path")
    for name, path in files.items():
        target = destination / name
        if target.exists() and digest(target) != digest(path):
            raise ValueError(f"Existing PDF differs: {name}")
    for name, path in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(path, target)
        if digest(target) != digest(path):
            raise RuntimeError("Corpus verification failed")
    return {
        "source": str(source),
        "destination": str(destination),
        "files": {str(name): digest(path) for name, path in sorted(files.items())},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "rags/context-rag/docs")
    parser.add_argument(
        "--destination", type=Path, default=ROOT / "rags/knowledge-enhanced-rag/data/apostilas"
    )
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.destination), ensure_ascii=False, indent=2))
