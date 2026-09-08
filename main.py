"""Root command-line entry point for the RAG benchmark collection."""

from __future__ import annotations

import argparse
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
CORPUS_TARGETS = {
    project: RAGS_ROOT
    / project
    / ("data/apostilas" if project == "knowledge-enhanced-rag" else "docs")
    for project in PROJECTS
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
    try:
        import uv  # noqa: F401
    except ImportError as exc:
        raise SystemExit("uv nao encontrado. Instale-o e execute `uv sync` na raiz.") from exc
    return [sys.executable, "-m", "uv"]


def pdf_files(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    )


def prepare_corpus(source: Path, clean: bool = False) -> int:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise RuntimeError(f"Pasta de corpus nao encontrada: {source}")

    files = pdf_files(source)
    if not files:
        raise RuntimeError(f"Nenhum PDF encontrado em: {source}")

    for target in CORPUS_TARGETS.values():
        target.mkdir(parents=True, exist_ok=True)
        if clean and target.resolve() != source:
            for existing in pdf_files(target):
                existing.unlink()

        used_names: dict[str, int] = {}
        for pdf in files:
            count = used_names.get(pdf.name, 0) + 1
            used_names[pdf.name] = count
            if count == 1:
                filename = pdf.name
            else:
                filename = f"{pdf.stem}_{count}{pdf.suffix.lower()}"
            destination = target / filename
            if pdf.resolve() != destination.resolve():
                shutil.copy2(pdf, destination)

    print(f"Corpus preparado: {len(files)} PDF(s) copiado(s) para {len(CORPUS_TARGETS)} pipelines.")
    return len(files)


def run_project(project: str, provider: str | None, questions: int | None = None) -> int:
    load_environment()
    env = os.environ.copy()
    if provider:
        env["LLM_PROVIDER"] = provider
    if questions is not None:
        env["BENCHMARK_QUESTION_LIMIT"] = str(questions)
    else:
        # No flag on the root CLI always means the complete remaining dataset,
        # even if the parent shell happens to define this runner-only variable.
        env.pop("BENCHMARK_QUESTION_LIMIT", None)

    project_dir = RAGS_ROOT / project
    command = [
        *uv_command(),
        "run",
        "--project",
        str(project_dir),
        "--locked",
        "python",
        "main.py",
    ]
    scope = f"ate {questions} pergunta(s)" if questions is not None else "dataset completo"
    print(
        f"Executando {project} com LLM_PROVIDER={env.get('LLM_PROVIDER', 'openrouter')} "
        f"| escopo: {scope}"
    )
    return subprocess.run(command, cwd=project_dir, env=env, check=False).returncode


def resolve_projects(requested: list[str]) -> list[str]:
    """Resolve the CLI selection while preserving the requested order."""
    if "all" in requested:
        if len(requested) != 1:
            raise ValueError("'all' deve ser usado sozinho, sem nomes de RAG adicionais")
        return list(PROJECTS)
    return list(dict.fromkeys(requested))


def run_projects(
    projects: list[str],
    provider: str | None,
    questions: int | None = None,
    corpus: Path | None = None,
    clean_corpus: bool = False,
) -> int:
    if corpus is not None:
        prepare_corpus(corpus, clean=clean_corpus)

    failures: list[str] = []
    for project in projects:
        print(f"\n{'=' * 72}\nPipeline: {project}\n{'=' * 72}")
        if run_project(project, provider, questions) != 0:
            failures.append(project)
    if failures:
        print(f"\nPipelines incompletos: {', '.join(failures)}")
        return 1
    return 0


def run_all(
    provider: str | None,
    questions: int | None = None,
    corpus: Path | None = None,
    clean_corpus: bool = False,
) -> int:
    """Backward-compatible alias for running all pipelines."""
    return run_projects(list(PROJECTS), provider, questions, corpus, clean_corpus)


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

    for project, docs_dir in CORPUS_TARGETS.items():
        count = len(pdf_files(docs_dir)) if docs_dir.exists() else 0
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

    corpus_parser = subparsers.add_parser(
        "prepare-corpus",
        help="copia todos os PDFs de uma pasta para os seis pipelines",
    )
    corpus_parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=ROOT / "corpus",
        help="pasta de origem dos PDFs (padrao: ./corpus)",
    )
    corpus_parser.add_argument(
        "--clean",
        action="store_true",
        help="remove PDFs existentes nos destinos antes de copiar",
    )

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
    run_parser.add_argument(
        "--corpus",
        type=Path,
        metavar="DIR",
        help="prepara os PDFs de DIR antes de executar os pipelines",
    )
    run_parser.add_argument(
        "--clean-corpus",
        action="store_true",
        help="remove PDFs existentes dos destinos antes de preparar o corpus",
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
    all_parser.add_argument(
        "--corpus",
        type=Path,
        metavar="DIR",
        help="prepara os PDFs de DIR antes de executar os pipelines",
    )
    all_parser.add_argument(
        "--clean-corpus",
        action="store_true",
        help="remove PDFs existentes dos destinos antes de preparar o corpus",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command in {None, "list"}:
        show_projects()
        return 0
    if args.command == "doctor":
        return doctor()
    if args.command == "prepare-corpus":
        try:
            prepare_corpus(args.source, clean=args.clean)
        except RuntimeError as exc:
            parser.error(str(exc))
        return 0
    if args.command == "run":
        try:
            projects = resolve_projects(args.projects)
        except ValueError as exc:
            parser.error(str(exc))
        try:
            return run_projects(
                projects,
                args.provider,
                args.questions,
                args.corpus,
                args.clean_corpus,
            )
        except RuntimeError as exc:
            parser.error(str(exc))
    if args.command == "run-all":
        try:
            return run_all(
                args.provider,
                args.questions,
                args.corpus,
                args.clean_corpus,
            )
        except RuntimeError as exc:
            parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
