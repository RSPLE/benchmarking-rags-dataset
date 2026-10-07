from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAGS_ROOT = ROOT / "rags"
PROJECTS = {
    "context-rag": "RAG vetorial clássico com contexto restrito",
    "graph-rag": "busca vetorial + grafo NetworkX extraído por LLM",
    "hybrid-rag": "busca BM25 + busca vetorial",
    "knowledge-enhanced-rag": "RAG vetorial + grafo de conhecimento Neo4j",
    "memory-augmented-rag": "agente RAG com memória conversacional",
    "self-rag": "RAG com autocrítica e uma etapa de refinamento",
}


def positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("deve ser um inteiro positivo") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("deve ser um inteiro positivo")
    return parsed


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
    if importlib.util.find_spec("uv") is None:
        raise SystemExit("uv nao encontrado. Instale-o e execute `uv sync` na raiz.")
    return [sys.executable, "-m", "uv"]


def run_project(project: str, provider: str | None, questions: int | None = None) -> int:
    load_environment()
    env = os.environ.copy()

    env.pop("VIRTUAL_ENV", None)
    if provider:
        env["LLM_PROVIDER"] = provider
    if questions is not None:
        env["BENCHMARK_QUESTION_LIMIT"] = str(questions)
    else:
        env.pop("BENCHMARK_QUESTION_LIMIT", None)

    project_dir = RAGS_ROOT / project
    if env.get("BENCHMARK_MODE") == "evaluate":
        entrypoint = str(ROOT / "evaluate_saved.py")
    else:
        entrypoint = "main.py"
    env["BENCHMARK_PROJECT"] = project
    env.setdefault("RAGAS_DO_NOT_TRACK", "true")
    for name in ("DOCS_DIR", "CHROMA_PERSIST_DIR", "BENCHMARK_FROZEN_FILE"):
        if env.get(name):
            env[name] = str((ROOT / env[name]).resolve())
    command = [
        *uv_command(),
        "run",
        "--project",
        str(project_dir),
        "--locked",
        "python",
        entrypoint,
    ]
    scope = f"ate {questions} pergunta(s)" if questions is not None else "dataset completo"
    print(
        f"Executando {project} com LLM_PROVIDER={env.get('LLM_PROVIDER', 'openrouter')} "
        f"| escopo: {scope}"
    )
    return subprocess.run(command, cwd=project_dir, env=env, check=False).returncode


def resolve_projects(requested: list[str]) -> list[str]:
    if "all" in requested:
        if len(requested) != 1:
            raise ValueError("'all' deve ser usado sozinho, sem nomes de RAG adicionais")
        return list(PROJECTS)
    return list(dict.fromkeys(requested))


def run_projects(
    projects: list[str],
    provider: str | None,
    questions: int | None = None,
) -> int:
    failures: list[str] = []
    for project in projects:
        print(f"\n{'=' * 72}\nPipeline: {project}\n{'=' * 72}")
        if run_project(project, provider, questions) != 0:
            failures.append(project)
            break
    if failures:
        print(f"\nPipelines incompletos: {', '.join(failures)}")
        return 1
    return 0


def run_all(
    provider: str | None,
    questions: int | None = None,
) -> int:
    return run_projects(list(PROJECTS), provider, questions)


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
        docs_dir = RAGS_ROOT / project / "docs"
        if project == "knowledge-enhanced-rag":
            docs_dir = RAGS_ROOT / project / "data" / "apostilas"
        if os.getenv("DOCS_DIR"):
            docs_dir = (ROOT / os.environ["DOCS_DIR"]).resolve()
        count = len(list(docs_dir.rglob("*.pdf"))) if docs_dir.exists() else 0
        if not count:
            errors += 1
            print(f"[ERRO] Corpus ausente: {docs_dir}")
        print(f"[INFO] {project}: {count} PDF(s) local(is)")

    if not os.getenv("NEO4J_URI") or not os.getenv("NEO4J_PASSWORD"):
        print(
            "[AVISO] Neo4j nao configurado; knowledge-enhanced-rag requer BENCHMARK_KG_MODE=disabled para rodar sem KG."
        )
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Executa e inspeciona os benchmarks RAG do monorepo."
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("list", help="lista pipelines e dataset")
    subparsers.add_parser("doctor", help="valida chaves, provedores e corpora")

    run_parser = subparsers.add_parser(
        "run",
        help="executa um, varios ou todos os pipelines de benchmark",
    )
    run_parser.add_argument(
        "projects",
        nargs="+",
        choices=["all", *sorted(PROJECTS)],
        metavar="RAG",
        help="um ou mais nomes de pipeline, ou 'all' para executar os seis",
    )
    run_parser.add_argument("--provider", choices=("openrouter", "openai"))
    run_parser.add_argument(
        "--questions",
        "--limit",
        dest="questions",
        type=positive_integer,
        metavar="X",
        help="tenta no maximo X perguntas ainda nao concluidas por RAG",
    )
    all_parser = subparsers.add_parser(
        "run-all",
        help="executa ou retoma todos os pipelines, um apos o outro",
    )
    all_parser.add_argument("--provider", choices=("openrouter", "openai"))
    all_parser.add_argument(
        "--questions",
        "--limit",
        dest="questions",
        type=positive_integer,
        metavar="X",
        help="tenta no maximo X perguntas ainda nao concluidas por RAG",
    )
    for command_parser in (run_parser, all_parser):
        command_parser.add_argument("--mode", choices=("full", "evaluate"), default="full")
        command_parser.add_argument("--frozen", type=Path)
        command_parser.add_argument(
            "--selection", choices=("unresolved", "pending", "failed"), default="unresolved"
        )
        command_parser.add_argument("--max-calls", type=positive_integer)
    subparsers.add_parser("audit", help="auditoria local sem chamadas externas")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("directory", type=Path)
    for name in ("pause", "clear-pause"):
        pause_parser = subparsers.add_parser(name)
        pause_parser.add_argument("directory", type=Path)
    migration_parser = subparsers.add_parser("migrate")
    migration_parser.add_argument("source", type=Path)
    migration_parser.add_argument("destination", type=Path)
    migration_parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command in {None, "list"}:
        show_projects()
        return 0
    if args.command == "doctor":
        return doctor()
    if args.command in {"audit", "status", "migrate", "pause", "clear-pause"}:
        from benchmark_admin import audit, migrate, status

        if args.command == "audit":
            result = audit()
        elif args.command == "status":
            result = status(args.directory)
        elif args.command == "migrate":
            result = migrate(args.source, args.destination, apply=args.apply)
        else:
            if not (args.directory / "checkpoint.json").is_file():
                parser.error("Diretorio sem checkpoint")
            request = args.directory / "pause.request"
            if args.command == "pause":
                request.touch()
            else:
                request.unlink(missing_ok=True)
            result = {"request": args.command, "directory": str(args.directory)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command in {"run", "run-all"}:
        if args.mode == "evaluate" and not args.frozen:
            parser.error("--mode evaluate requires --frozen")
        if args.frozen and args.mode != "evaluate":
            parser.error("--frozen requires --mode evaluate")
        requested = args.projects if args.command == "run" else ["all"]
        if args.mode == "evaluate" and (len(requested) != 1 or requested == ["all"]):
            parser.error("Frozen answers must target one RAG")
        os.environ["BENCHMARK_MODE"] = args.mode
        os.environ["BENCHMARK_SELECTION"] = args.selection
        if args.frozen:
            os.environ["BENCHMARK_FROZEN_FILE"] = str(args.frozen.resolve())
        if args.max_calls:
            os.environ["BENCHMARK_MAX_CALLS"] = str(args.max_calls)
    if args.command == "run":
        try:
            projects = resolve_projects(args.projects)
        except ValueError as exc:
            parser.error(str(exc))
        return run_projects(projects, args.provider, args.questions)
    if args.command == "run-all":
        return run_all(args.provider, args.questions)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
