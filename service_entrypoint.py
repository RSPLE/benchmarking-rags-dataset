from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SYSTEM_KEYS = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "UV_CACHE_DIR",
    "UV_NO_SYNC",
    "UV_LINK_MODE",
}
TELEGRAM_KEYS = {
    "TELEGRAM_ENABLED",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_RESULTS_CHAT_ID",
    "TELEGRAM_ALLOWED_USER_IDS",
    "TELEGRAM_CONTROL_ENABLED",
    "TELEGRAM_PROGRESS_INTERVAL_SECONDS",
    "TELEGRAM_SEND_FINAL_FILES",
    "BENCHMARK_CONTROL_SOCKET",
    "BENCHMARK_OUTPUT_DIR",
    "BENCHMARK_TELEGRAM_DATABASE",
}
WORKER_PREFIXES = (
    "BENCHMARK_",
    "OPENROUTER_",
    "OPENAI_",
    "LLM_",
    "RAGAS_",
    "EMBEDDING_",
    "NEO4J_",
    "LANGCHAIN_",
    "LANGSMITH_",
    "CHROMA_",
)


def role_environment(role, source):
    if role not in {"control", "telegram", "batch", "worker"}:
        raise ValueError("Unknown service role")
    names = SYSTEM_KEYS | (TELEGRAM_KEYS if role == "telegram" else {"DOCS_DIR", "RETRIEVER_K"})
    if role == "control":
        names = names | {"TELEGRAM_ALLOWED_USER_IDS"}
    env = {
        key: value
        for key, value in source.items()
        if key in names or (role != "telegram" and key.startswith(WORKER_PREFIXES))
    }
    env["PYTHON_DOTENV_DISABLED"] = "1"
    return env


def service_command(role, env):
    output = str((ROOT / env.get("BENCHMARK_OUTPUT_DIR", "resultados")).resolve())
    if role == "telegram":
        return [
            sys.executable,
            str(ROOT / "telegram_notifier.py"),
            output,
            "--database",
            env.get(
                "BENCHMARK_TELEGRAM_DATABASE", "/var/lib/benchmark-notifier/telegram.outbox.db"
            ),
        ]
    if role == "control":
        return [
            sys.executable,
            str(ROOT / "benchmark_control.py"),
            "--socket",
            env.get("BENCHMARK_CONTROL_SOCKET", "/run/benchmark/control.sock"),
            "--database",
            env.get("BENCHMARK_CONTROL_DATABASE", "/var/lib/benchmark/control/queue.db"),
            "--root",
            output,
        ]
    if role == "batch":
        project = env.get("BENCHMARK_PROJECT")
        count = env.get("BENCHMARK_QUESTION_LIMIT")
        if not project or not count:
            raise ValueError(
                "Batch service requires BENCHMARK_PROJECT and BENCHMARK_QUESTION_LIMIT"
            )
        return [sys.executable, str(ROOT / "main.py"), "run", project, "--questions", count]
    raise ValueError("Unknown service role")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=("control", "telegram", "batch"))
    parser.add_argument("--from-environment", action="store_true")
    args = parser.parse_args()
    if not args.from_environment:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    env = role_environment(args.role, os.environ)
    command = service_command(args.role, env)
    os.chdir(ROOT)
    os.execve(command[0], command, env)


if __name__ == "__main__":
    main()
