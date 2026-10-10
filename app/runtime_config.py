from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.benchmark.storage import atomic_file, atomic_json
from app.paths import ROOT

PROJECTS = (
    "context-rag",
    "graph-rag",
    "hybrid-rag",
    "knowledge-enhanced-rag",
    "memory-augmented-rag",
    "self-rag",
)
SECRET_KEYS = frozenset({"OPENROUTER_API_KEY", "NEO4J_PASSWORD", "TELEGRAM_BOT_TOKEN"})
BOOLEAN_KEYS = frozenset(
    {"TELEGRAM_ENABLED", "TELEGRAM_CONTROL_ENABLED", "TELEGRAM_SEND_FINAL_FILES"}
)
POSITIVE_INTEGER_KEYS = frozenset(
    {
        "LLM_MAX_TOKENS",
        "LLM_TIMEOUT_SECONDS",
        "RAGAS_MAX_TOKENS",
        "RAGAS_TIMEOUT_SECONDS",
        "RAGAS_MAX_WORKERS",
        "RAGAS_MAX_ATTEMPTS",
        "BENCHMARK_HTTP_ATTEMPTS",
        "BENCHMARK_RETRY_MAX_WAIT_SECONDS",
        "BENCHMARK_MAX_CALLS",
        "BENCHMARK_MAX_QUESTION_CALLS",
        "BENCHMARK_MAX_TOKENS",
        "BENCHMARK_MAX_QUESTION_TOKENS",
        "BENCHMARK_MAX_PREPARATION_CALLS",
        "BENCHMARK_MAX_SECONDS",
        "BENCHMARK_QUESTION_TIMEOUT_SECONDS",
        "BENCHMARK_AGENT_RECURSION_LIMIT",
        "BENCHMARK_MAX_STAGE_ATTEMPTS",
        "BENCHMARK_RETRY_COOLDOWN_SECONDS",
        "TELEGRAM_PROGRESS_INTERVAL_SECONDS",
    }
)
TEXT_KEYS = frozenset(
    {
        "LLM_PROVIDER",
        "EMBEDDING_PROVIDER",
        "OPENROUTER_MODEL",
        "OPENROUTER_JUDGE_MODEL",
        "OPENROUTER_EMBEDDING_MODEL",
        "NEO4J_URI",
        "NEO4J_USERNAME",
        "NEO4J_DATABASE",
        "TELEGRAM_RESULTS_CHAT_ID",
        "TELEGRAM_ALLOWED_USER_IDS",
        "CHROMA_COLLECTION_NAME",
    }
)
PATH_KEYS = frozenset({"DOCS_DIR", "BENCHMARK_DATASET"})
RUNTIME_ENV_KEYS = SECRET_KEYS | BOOLEAN_KEYS | POSITIVE_INTEGER_KEYS | TEXT_KEYS | PATH_KEYS
MAX_CONFIGURATION_BYTES = 256 * 1024
MAX_DATASET_BYTES = 10 * 1024 * 1024
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_CORPUS_BYTES = 500 * 1024 * 1024


def default_runtime_path() -> Path:
    return Path(os.getenv("BENCHMARK_RUNTIME_CONFIG", ROOT / "configuration/settings.json")).resolve()


def empty_configuration() -> dict:
    return {
        "version": 1,
        "updated_at": None,
        "updated_by": None,
        "environment": {},
        "enabled_projects": list(PROJECTS),
        "corpus": None,
        "dataset": None,
    }


def _validate_environment(values: dict) -> dict[str, str]:
    if not isinstance(values, dict) or set(values) - RUNTIME_ENV_KEYS:
        raise ValueError("A configuração contém campos não permitidos")
    result: dict[str, str] = {}
    for key, raw in values.items():
        if not isinstance(raw, (str, int, bool)):
            raise ValueError(f"Valor inválido para {key}")
        value = str(raw).strip()
        if "\x00" in value or len(value) > 4096:
            raise ValueError(f"Valor inválido para {key}")
        if key in POSITIVE_INTEGER_KEYS:
            if not value.isdecimal() or int(value) < 1:
                raise ValueError(f"{key} deve ser um inteiro positivo")
            value = str(int(value))
        elif key in BOOLEAN_KEYS:
            value = value.lower()
            if value not in {"true", "false"}:
                raise ValueError(f"{key} deve ser true ou false")
        elif key == "LLM_PROVIDER" or key == "EMBEDDING_PROVIDER":
            if value not in {"openrouter", "openai"}:
                raise ValueError(f"Provedor inválido em {key}")
        elif key == "NEO4J_URI" and value and not re.fullmatch(
            r"(?:neo4j(?:\+s|\+ssc)?|bolt(?:\+s|\+ssc)?)://[^\s]+", value
        ):
            raise ValueError("A URI do Neo4j é inválida")
        elif key == "TELEGRAM_ALLOWED_USER_IDS" and value:
            ids = [part.strip() for part in value.split(",")]
            if any(not part.isdecimal() or int(part) < 1 for part in ids):
                raise ValueError("Os IDs autorizados do Telegram devem ser números positivos")
            value = ",".join(ids)
        elif key in PATH_KEYS:
            path = Path(value)
            if not value or not path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Caminho inválido em {key}")
        result[key] = value
    return result


def _validate_document(document: dict) -> dict:
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("Versão de configuração inválida")
    projects = document.get("enabled_projects")
    if not isinstance(projects, list) or len(projects) != len(set(projects)):
        raise ValueError("Lista de RAGs inválida")
    if any(project not in PROJECTS for project in projects):
        raise ValueError("A configuração contém um RAG desconhecido")
    document = {**empty_configuration(), **document}
    document["environment"] = _validate_environment(document.get("environment", {}))
    document["enabled_projects"] = projects
    return document


def read_runtime_config(path: Path | str | None = None) -> dict:
    path = Path(path or default_runtime_path()).resolve()
    if not path.exists():
        return empty_configuration()
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CONFIGURATION_BYTES:
        raise ValueError("Arquivo de configuração inseguro ou grande demais")
    try:
        return _validate_document(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Não foi possível ler a configuração operacional") from exc


def save_runtime_config(
    path: Path | str,
    environment: dict,
    enabled_projects: list[str],
    *,
    editor: str,
    corpus: dict | None = None,
    dataset: dict | None = None,
) -> dict:
    path = Path(path).resolve()
    current = read_runtime_config(path)
    document = {
        "version": 1,
        "updated_at": datetime.now(UTC).isoformat(),
        "updated_by": str(editor)[:64],
        "environment": _validate_environment(environment),
        "enabled_projects": list(enabled_projects),
        "corpus": current.get("corpus") if corpus is None else corpus,
        "dataset": current.get("dataset") if dataset is None else dataset,
    }
    document = _validate_document(document)
    atomic_json(path, document, mode=0o600)
    os.chmod(path, 0o600)
    return document


def runtime_environment(source: dict | os._Environ, path: Path | str | None = None) -> dict:
    merged = dict(source)
    try:
        merged.update(read_runtime_config(path)["environment"])
    except ValueError:
        # Startup remains possible with the static environment if the optional file is corrupt.
        pass
    return merged


def enabled_projects(path: Path | str | None = None) -> tuple[str, ...]:
    try:
        return tuple(read_runtime_config(path)["enabled_projects"])
    except ValueError:
        return PROJECTS


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def index_collection_name(embedding_model: str, corpus_sha256: str = "repository-default") -> str:
    identity = _sha256(f"{embedding_model}\0{corpus_sha256}".encode())[:24]
    return f"runtime_{identity}"


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install_dataset(root: Path | str, filename: str, data: bytes) -> dict:
    from app.benchmark.runner import _load_dataset

    if Path(filename).name != filename or Path(filename).suffix.lower() != ".json":
        raise ValueError("Envie um arquivo JSON com nome simples")
    if not data or len(data) > MAX_DATASET_BYTES:
        raise ValueError("O dataset está vazio ou excede 10 MiB")
    digest = _sha256(data)
    target = Path(root).resolve() / "datasets" / digest / "questions.json"
    if not target.exists():
        with atomic_file(target, mode=0o600) as output:
            try:
                output.write(data.decode("utf-8-sig"))
            except UnicodeDecodeError as exc:
                raise ValueError("O dataset deve estar em UTF-8") from exc
    try:
        questions = _load_dataset(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if len(questions) > 90 or any(
        not re.fullmatch(r"Q\d{3}", item["id"])
        or not 1 <= int(item["id"][1:]) <= 90
        for item in questions
    ):
        target.unlink(missing_ok=True)
        raise ValueError("O dataset deve ter até 90 IDs únicos entre Q001 e Q090")
    return {
        "path": str(target),
        "filename": filename,
        "sha256": digest,
        "questions": len(questions),
        "bytes": len(data),
    }


def install_corpus(root: Path | str, files: list[tuple[str, bytes]]) -> dict:
    if not files:
        raise ValueError("Selecione pelo menos um PDF")
    normalized: list[tuple[str, bytes]] = []
    seen = set()
    total = 0
    for filename, data in files:
        name = Path(filename).name
        if name != filename or not re.fullmatch(r"[\w .()\-]{1,180}\.pdf", name, re.I):
            raise ValueError(f"Nome de PDF inválido: {filename}")
        key = name.casefold()
        if key in seen:
            raise ValueError(f"PDF duplicado: {name}")
        if not data.startswith(b"%PDF-") or len(data) > MAX_PDF_BYTES:
            raise ValueError(f"{name} não é um PDF válido ou excede 50 MiB")
        seen.add(key)
        total += len(data)
        normalized.append((name, data))
    if total > MAX_CORPUS_BYTES:
        raise ValueError("O corpus excede 500 MiB")
    digest = hashlib.sha256()
    for name, data in sorted(normalized, key=lambda item: item[0].casefold()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).digest())
    corpus_hash = digest.hexdigest()
    directory = Path(root).resolve() / "corpora" / corpus_hash
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    inventory = []
    for name, data in normalized:
        target = directory / name
        if not target.exists():
            _atomic_bytes(target, data)
        inventory.append({"name": name, "bytes": len(data), "sha256": _sha256(data)})
    return {"path": str(directory), "sha256": corpus_hash, "bytes": total, "files": inventory}
