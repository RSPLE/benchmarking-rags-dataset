from __future__ import annotations

import os
import platform
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from benchmark_storage import file_hash, fingerprint

ROOT = Path(__file__).resolve().parent
METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
DEFAULTS = {
    "LLM_PROVIDER": "openrouter",
    "RETRIEVER_K": "3",
    "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
    "OPENROUTER_MODEL": "",
    "OPENROUTER_JUDGE_MODEL": "",
    "OPENAI_MODEL": "gpt-5.5",
    "OPENAI_JUDGE_MODEL": "",
    "OPENAI_EMBEDDING_MODEL": "text-embedding-3-large",
    "OPENAI_REASONING_EFFORT": "medium",
    "EMBEDDING_PROVIDER": "",
    "EMBEDDING_DIMENSIONS": "",
    "OPENROUTER_EMBEDDING_MODEL": "openai/text-embedding-3-small",
    "LLM_MAX_TOKENS": "4096",
    "RAGAS_MAX_TOKENS": "2048",
    "LLM_TIMEOUT_SECONDS": "600",
    "RAGAS_TIMEOUT_SECONDS": "600",
    "RAGAS_MAX_WORKERS": "1",
    "RAGAS_MAX_ATTEMPTS": "1",
    "BENCHMARK_HTTP_ATTEMPTS": "3",
    "BENCHMARK_AGENT_RECURSION_LIMIT": "12",
    "BENCHMARK_REPETITION": "1",
    "BENCHMARK_KG_MODE": "required",
    "BENCHMARK_KG_SNAPSHOT": "",
    "NEO4J_URI": "",
    "NEO4J_DATABASE": "neo4j",
}


def positive_int(name, default):
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def configuration():
    config = {name: os.getenv(name, default).strip() for name, default in DEFAULTS.items()}
    config["EMBEDDING_PROVIDER"] = config["EMBEDDING_PROVIDER"] or config["LLM_PROVIDER"]
    for provider in ("OPENROUTER", "OPENAI"):
        config[f"{provider}_JUDGE_MODEL"] = (
            config[f"{provider}_JUDGE_MODEL"] or config[f"{provider}_MODEL"]
        )
    return config


def corpus_inventory(directory):
    directory = Path(directory).resolve()
    files = sorted(p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
    return [
        {"path": str(p.relative_to(directory)), "sha256": file_hash(p), "bytes": p.stat().st_size}
        for p in files
    ]


def build_manifest(project, dataset, *, mode="full", frozen_path=None):
    project_dir = ROOT / "rags" / project
    if not project_dir.is_dir():
        raise ValueError(f"Unknown RAG: {project}")
    default_docs = "data/apostilas" if project == "knowledge-enhanced-rag" else "docs"
    docs_dir = Path(os.getenv("DOCS_DIR", str(project_dir / default_docs))).resolve()
    corpus = corpus_inventory(docs_dir) if mode == "full" else []
    if mode == "full" and (not corpus or any(p["bytes"] == 0 for p in corpus)):
        raise ValueError(f"Missing or empty PDFs: {docs_dir}")
    files = [
        *ROOT.glob("*.py"),
        *project_dir.rglob("*.py"),
        project_dir / "uv.lock",
        project_dir / "pyproject.toml",
    ]
    files = sorted(p for p in files if ".venv" not in p.parts and p.is_file())
    dependencies = {}
    for package in (
        "ragas",
        "langchain",
        "langchain-core",
        "langchain-openai",
        "langchain-chroma",
        "langchain-community",
        "langchain-text-splitters",
        "chromadb",
        "pypdf",
        "langgraph",
        "openai",
    ):
        try:
            dependencies[package] = version(package)
        except PackageNotFoundError:
            dependencies[package] = None
    manifest = {
        "schema": 2,
        "runtime": {"python": platform.python_version(), "dependencies": dependencies},
        "project": project,
        "mode": mode,
        "dataset_sha256": file_hash(dataset),
        "configuration": configuration(),
        "code": {str(p.relative_to(ROOT)): file_hash(p) for p in files},
        "corpus": corpus,
        "metrics": list(METRICS),
        "evidence_policy": "upstream-document-contexts-v4",
        "generation_evidence_policy": "complete-tool-and-prompt-evidence",
        "result_schema": "upstream-nine-columns-v1",
        "index": {
            "path": str(
                Path(os.getenv("CHROMA_PERSIST_DIR", str(project_dir / "chroma_v2"))).resolve()
            ),
            "collection": os.getenv("CHROMA_COLLECTION_NAME", project.replace("-", "_")),
        },
        "frozen_sha256": file_hash(frozen_path) if frozen_path else None,
    }
    if project == "knowledge-enhanced-rag" and mode == "full":
        manifest["knowledge_sha256"] = knowledge_identity()
        manifest["question_memory_policy"] = "upstream-shared-session-last-five-exchanges-resumable"
    manifest["experiment_id"] = fingerprint(manifest)
    return manifest


def experiment_directory(manifest):
    base = Path(os.getenv("BENCHMARK_OUTPUT_DIR", str(ROOT / "resultados")))
    if not base.is_absolute():
        base = ROOT / base
    return base / manifest["project"] / manifest["experiment_id"]


def knowledge_identity():
    if os.getenv("BENCHMARK_KG_MODE") == "snapshot":
        from benchmark_knowledge import read_snapshot

        return fingerprint(read_snapshot(os.environ["BENCHMARK_KG_SNAPSHOT"]))
    if os.getenv("BENCHMARK_KG_MODE", "required") == "disabled":
        return "disabled"
    from neo4j import GraphDatabase

    uri = os.getenv("NEO4J_URI")
    password = os.getenv("NEO4J_PASSWORD")
    if not uri or not password:
        raise ValueError("Neo4j configuration is required")
    with GraphDatabase.driver(
        uri, auth=(os.getenv("NEO4J_USERNAME", "neo4j"), password), connection_timeout=10
    ) as driver:
        driver.verify_connectivity()
        with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:

            def read_snapshot(tx):
                nodes = [
                    dict(record["properties"])
                    for record in tx.run("MATCH (n:Conceito) RETURN properties(n) AS properties")
                ]
                edges = [
                    record.data()
                    for record in tx.run(
                        "MATCH (a:Conceito)-[r]->(b:Conceito) RETURN a.nome AS source, b.nome AS target, type(r) AS relation, properties(r) AS properties"
                    )
                ]
                if not nodes:
                    raise ValueError("Knowledge graph is empty")
                return {
                    "nodes": sorted(nodes, key=fingerprint),
                    "edges": sorted(edges, key=fingerprint),
                }

            return fingerprint(session.execute_read(read_snapshot))
