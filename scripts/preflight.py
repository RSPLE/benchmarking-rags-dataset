from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inspect(project):
    from importlib.metadata import version

    from pypdf import PdfReader

    docs = Path(
        os.getenv(
            "DOCS_DIR",
            str(
                ROOT
                / "rags"
                / project
                / ("data/apostilas" if project == "knowledge-enhanced-rag" else "docs")
            ),
        )
    ).resolve()
    result = {
        "project": project,
        "documents": str(docs),
        "pdfs": [],
        "errors": [],
        "dependencies": {},
    }
    for package in ("ragas", "langchain", "chromadb", "langchain-openai", "pypdf"):
        result["dependencies"][package] = version(package)
    for path in sorted(docs.rglob("*")):
        if path.suffix.lower() != ".pdf" or not path.is_file():
            continue
        try:
            reader = PdfReader(path)
            pages = sum(bool(page.extract_text().strip()) for page in reader.pages)
            if not pages:
                raise ValueError("No usable text")
            result["pdfs"].append(
                {"name": path.name, "pages": len(reader.pages), "text_pages": pages}
            )
        except Exception as exc:
            result["errors"].append({"file": path.name, "error": type(exc).__name__})
    if not result["pdfs"]:
        result["errors"].append({"error": "Missing corpus"})
    manifests = sorted((ROOT / "rags" / project / "chroma_v2").glob("*.manifest.json"))
    result["indices"] = [
        {
            "file": str(path),
            "complete": data.get("complete"),
            "dimension": data.get("embedding_dimension"),
            "chunks": len(data.get("ids", [])),
        }
        for path in manifests
        for data in [json.loads(path.read_text())]
    ]
    result["provider_validation"] = "not_run"
    return result


def main():
    if len(sys.argv) == 2:
        print(json.dumps(inspect(sys.argv[1]), ensure_ascii=False))
        return 0
    report = []
    for project in sorted((ROOT / "rags").iterdir()):
        if not project.is_dir():
            continue
        interpreter = project / ".venv/bin/python"
        if not interpreter.is_file():
            report.append({"project": project.name, "errors": ["Missing environment"]})
            continue
        result = subprocess.run(
            [str(interpreter), __file__, project.name], capture_output=True, text=True, timeout=300
        )
        if result.returncode:
            report.append(
                {
                    "project": project.name,
                    "errors": ["Environment inspection failed"],
                    "exit_code": result.returncode,
                }
            )
        else:
            report.append(json.loads(result.stdout))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(any(row.get("errors") for row in report))


if __name__ == "__main__":
    raise SystemExit(main())
