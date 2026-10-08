from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from benchmark_options import add_run_options, option_environment, validate_run_options
from service_entrypoint import role_environment

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
    env = role_environment("worker", os.environ)

    env.pop("VIRTUAL_ENV", None)
    if provider:
        env["LLM_PROVIDER"] = provider
    if questions is not None:
        env["BENCHMARK_QUESTION_LIMIT"] = str(questions)

    project_dir = RAGS_ROOT / project
    if env.get("BENCHMARK_MODE") == "evaluate":
        entrypoint = str(ROOT / "evaluate_saved.py")
    else:
        entrypoint = "main.py"
    env["BENCHMARK_PROJECT"] = project
    env.setdefault("RAGAS_DO_NOT_TRACK", "true")
    for name in (
        "DOCS_DIR",
        "CHROMA_PERSIST_DIR",
        "BENCHMARK_FROZEN_FILE",
        "BENCHMARK_KG_SNAPSHOT",
        "BENCHMARK_OUTPUT_DIR",
        "BENCHMARK_BUDGET_DIR",
    ):
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
    errors = 0
    print(f"Python: {sys.version.split()[0]}")
    print(f"LLM: {provider}")
    print(f"Embeddings: {embedding_provider}")
    roles = (
        ("judge", "judge_embedding")
        if os.getenv("BENCHMARK_MODE") == "evaluate"
        or os.getenv("BENCHMARK_CREDIT_SCOPE") == "judge"
        else ("generation", "judge", "embedding", "judge_embedding")
    )
    for role in roles:
        selected = embedding_provider if "embedding" in role else provider
        if selected not in {"openrouter", "openai"}:
            print(f"[ERRO] Provedor desconhecido: {selected}")
            errors += 1
            continue
        prefix = selected.upper()
        names = [f"{prefix}_{role.upper()}_API_KEY", f"{prefix}_API_KEY"]
        if role == "judge_embedding":
            names.insert(1, f"{prefix}_JUDGE_API_KEY")
        if any(os.getenv(name) for name in names):
            print(f"[OK] Credencial configurada: {selected}/{role}")
        else:
            print(f"[ERRO] Credencial ausente: {selected}/{role}")
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

    if os.getenv("BENCHMARK_KG_MODE", "required") == "required" and (
        not os.getenv("NEO4J_URI") or not os.getenv("NEO4J_PASSWORD")
    ):
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
    subparsers.add_parser("telegram-check", help="verifica bot e canal sem enviar mensagens")

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
    all_parser = subparsers.add_parser(
        "run-all",
        help="executa ou retoma todos os pipelines, um apos o outro",
    )
    resume_parser = subparsers.add_parser("resume", help="retoma um experimento compatível")
    resume_parser.add_argument("project", choices=sorted(PROJECTS))
    resume_parser.add_argument("experiment")
    for command_parser in (run_parser, all_parser, resume_parser):
        add_run_options(command_parser)
    export_parser = subparsers.add_parser("export-frozen")
    export_parser.add_argument("directory", type=Path)
    export_parser.add_argument("destination", type=Path)
    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("root", type=Path)
    reconcile_parser = subparsers.add_parser("reconcile")
    reconcile_parser.add_argument("root", type=Path)
    reconcile_parser.add_argument("--apply", action="store_true")
    subparsers.add_parser("preflight", help="valida os seis ambientes sem chamadas pagas")
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
    if args.command == "preflight":
        load_environment()
        env = dict(os.environ)
        if env.get("DOCS_DIR"):
            env["DOCS_DIR"] = str((ROOT / env["DOCS_DIR"]).resolve())
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/preflight.py")], env=env, check=False
        ).returncode
    if args.command in {"export-frozen", "report", "reconcile"}:
        from benchmark_admin import export_frozen, report
        from benchmark_reconcile import reconcile

        if args.command == "export-frozen":
            result = export_frozen(args.directory, args.destination)
        elif args.command == "report":
            result = report(args.root)
        else:
            if args.apply:
                load_environment()
            result = reconcile(args.root, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "doctor":
        return doctor()
    if args.command == "telegram-check":
        from telegram_setup import check_configuration

        load_environment()
        try:
            result = check_configuration(os.environ)
        except (ValueError, RuntimeError) as exc:
            print(str(exc))
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
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
    if args.command in {"run", "run-all", "resume"}:
        load_environment()
        requested = args.projects if args.command == "run" else ["all"]
        if args.command == "resume":
            import re

            if not re.fullmatch(r"[0-9a-f]{64}", args.experiment):
                parser.error("Expected the full experiment ID")
            requested = [args.project]
            os.environ["BENCHMARK_EXPECTED_EXPERIMENT"] = args.experiment
        else:
            os.environ.pop("BENCHMARK_EXPECTED_EXPERIMENT", None)
        try:
            projects = resolve_projects(requested)
            validate_run_options(args, projects)
        except ValueError as exc:
            parser.error(str(exc))
        os.environ.update(option_environment(args))
        if args.frozen:
            os.environ["BENCHMARK_FROZEN_FILE"] = str(args.frozen.resolve())
        return run_projects(projects, args.provider, args.questions)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
