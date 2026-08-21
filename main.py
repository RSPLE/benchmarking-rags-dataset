"""Root command-line entry point for the RAG benchmark collection."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECTS = {
    "context-rag": "RAG vetorial clássico com contexto restrito",
    "graph-rag": "busca vetorial + grafo NetworkX extraído por LLM",
    "hybrid-rag": "busca BM25 + busca vetorial",
    "knowledge-enhanced-rag": "RAG vetorial + grafo de conhecimento Neo4j",
    "memory-augmented-rag": "agente RAG com memória conversacional",
    "self-rag": "RAG com autocrítica e uma etapa de refinamento",
}


def load_environment() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise SystemExit(
            "Dependencias ausentes. Execute `uv sync` antes de rodar um benchmark."
        ) from exc
    load_dotenv(ROOT / ".env", override=False)


def uv_command() -> list[str]:
    executable = shutil.which("uv")
    if executable:
        return [executable]
    try:
        import uv  # noqa: F401
    except ImportError as exc:
        raise SystemExit("uv nao encontrado. Instale-o e execute `uv sync` na raiz.") from exc
    return [sys.executable, "-m", "uv"]


def run_project(project: str, provider: str | None) -> int:
    load_environment()
    env = os.environ.copy()
    if provider:
        env["LLM_PROVIDER"] = provider

    project_dir = ROOT / project
    command = [
        *uv_command(),
        "run",
        "--project",
        str(project_dir),
        "--locked",
        "python",
        "main.py",
    ]
    print(f"Executando {project} com LLM_PROVIDER={env.get('LLM_PROVIDER', 'openrouter')}")
    return subprocess.run(command, cwd=project_dir, env=env, check=False).returncode


def run_all(provider: str | None) -> int:
    failures: list[str] = []
    for project in PROJECTS:
        print(f"\n{'=' * 72}\nPipeline: {project}\n{'=' * 72}")
        if run_project(project, provider) != 0:
            failures.append(project)
    if failures:
        print(f"\nPipelines incompletos: {', '.join(failures)}")
        return 1
    return 0


def run_api(provider: str | None, host: str, port: int) -> int:
    load_environment()
    env = os.environ.copy()
    if provider:
        env["LLM_PROVIDER"] = provider
    project_dir = ROOT / "knowledge-enhanced-rag"
    command = [
        *uv_command(),
        "run",
        "--project",
        str(project_dir),
        "--locked",
        "uvicorn",
        "app:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    return subprocess.run(
        command,
        cwd=project_dir,
        env=env,
        check=False,
    ).returncode


def show_projects() -> None:
    print("Pipelines disponiveis:")
    for name, description in PROJECTS.items():
        print(f"  {name:<24} {description}")
    print("\nDataset: eval-dataset/qa_dataset_90.json (90 perguntas e respostas)")


def doctor() -> int:
    load_environment()
    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()
    embedding_provider = os.getenv("EMBEDDING_PROVIDER", provider).lower()
    key_by_provider = {
        "openrouter": "OPENROUTER_API_KEY",
        "openai": "OPENAI_API_KEY",
    }

    errors = 0
    print(f"Python: {sys.version.split()[0]}")
    print(f"LLM: {provider}")
    print(f"Embeddings: {embedding_provider}")
    for selected in {provider, embedding_provider}:
        variable = key_by_provider.get(selected)
        if not variable:
            print(f"[ERRO] Provedor desconhecido: {selected}")
            errors += 1
        elif os.getenv(variable):
            print(f"[OK] {variable} configurada")
        else:
            print(f"[ERRO] {variable} ausente")
            errors += 1

    for project in PROJECTS:
        docs_dir = ROOT / project / "docs"
        if project == "knowledge-enhanced-rag":
            docs_dir = ROOT / project / "data" / "apostilas"
        count = len(list(docs_dir.glob("*.pdf"))) if docs_dir.exists() else 0
        print(f"[INFO] {project}: {count} PDF(s) local(is)")

    if not os.getenv("NEO4J_URI") or not os.getenv("NEO4J_PASSWORD"):
        print("[AVISO] Neo4j nao configurado; knowledge-enhanced-rag nao iniciara.")
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Executa e inspeciona os benchmarks RAG do monorepo."
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("list", help="lista pipelines e dataset")
    subparsers.add_parser("doctor", help="valida chaves, provedores e corpora")

    run_parser = subparsers.add_parser("run", help="executa um pipeline de benchmark")
    run_parser.add_argument("project", choices=sorted(PROJECTS))
    run_parser.add_argument("--provider", choices=("openrouter", "openai"))

    all_parser = subparsers.add_parser(
        "run-all",
        help="executa ou retoma todos os pipelines, um apos o outro",
    )
    all_parser.add_argument("--provider", choices=("openrouter", "openai"))

    api_parser = subparsers.add_parser("api", help="inicia a API do Knowledge-Enhanced RAG")
    api_parser.add_argument("--provider", choices=("openrouter", "openai"))
    api_parser.add_argument("--host", default="127.0.0.1")
    api_parser.add_argument("--port", default=8000, type=int)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command in {None, "list"}:
        show_projects()
        return 0
    if args.command == "doctor":
        return doctor()
    if args.command == "run":
        return run_project(args.project, args.provider)
    if args.command == "run-all":
        return run_all(args.provider)
    if args.command == "api":
        return run_api(args.provider, args.host, args.port)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
