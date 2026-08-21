"""Tiny dependency-free HTTP runner used inside each RAG container."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections import deque
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RAG_NAME = os.environ.get("RAG_NAME", "").strip()
if not RAG_NAME:
    raise RuntimeError("RAG_NAME precisa ser definido")
PROJECT_DIR = REPOSITORY_ROOT / "rags" / RAG_NAME
RESULTS_DIR = Path(os.getenv("BENCHMARK_OUTPUT_DIR", f"/results/{RAG_NAME}"))
PORT = int(os.getenv("RUNNER_PORT", "8090"))
MAX_LOG_LINES = int(os.getenv("RUNNER_MAX_LOG_LINES", "1200"))

state_lock = threading.Lock()
process_lock = threading.Lock()
current_process: subprocess.Popen[str] | None = None


def now() -> str:
    return datetime.now(UTC).isoformat()


def elapsed_seconds(started_at: str | None, finished_at: str | None = None) -> float:
    if not started_at:
        return 0.0
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(finished_at) if finished_at else datetime.now(UTC)
    except (TypeError, ValueError):
        return 0.0
    return round(max((end - start).total_seconds(), 0.0), 2)


def initial_state() -> dict[str, Any]:
    state: dict[str, Any] = {
        "rag": RAG_NAME,
        "state": "idle",
        "started_at": None,
        "finished_at": None,
        "returncode": None,
        "duration_seconds": 0.0,
        "total_duration_seconds": 0.0,
        "logs": deque(maxlen=MAX_LOG_LINES),
    }
    target = RESULTS_DIR / "runner.json"
    try:
        persisted = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state

    for key in state:
        if key != "logs" and key in persisted:
            state[key] = persisted[key]
    state["logs"].extend(persisted.get("logs", []))
    if state["state"] in {"queued", "running"}:
        finished_at = now()
        duration = elapsed_seconds(state.get("started_at"), finished_at)
        state.update(
            state="failed",
            finished_at=finished_at,
            returncode=-1,
            duration_seconds=duration,
            total_duration_seconds=round(
                float(state.get("total_duration_seconds") or 0.0) + duration,
                2,
            ),
        )
        state["logs"].append("Execução interrompida pela reinicialização do container.")
        try:
            target.write_text(
                json.dumps({**state, "logs": list(state["logs"])}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
    return state


run_state = initial_state()


def snapshot() -> dict[str, Any]:
    with state_lock:
        payload = {**run_state, "logs": list(run_state["logs"])}
        if run_state["state"] == "running":
            payload["duration_seconds"] = elapsed_seconds(run_state.get("started_at"))
        return payload


def append_log(line: str) -> None:
    clean = line.rstrip("\r\n")
    with state_lock:
        run_state["logs"].append(clean)
    print(clean, flush=True)


def persist_state() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    target = RESULTS_DIR / "runner.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def execute() -> None:
    global current_process
    command = [
        shutil.which("uv") or "uv",
        "run",
        "--project",
        str(PROJECT_DIR),
        "--locked",
        "python",
        "main.py",
    ]
    environment = os.environ.copy()
    environment["BENCHMARK_OUTPUT_DIR"] = str(RESULTS_DIR)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with state_lock:
        run_state.update(
            state="running",
            started_at=now(),
            finished_at=None,
            returncode=None,
            duration_seconds=0.0,
        )
        run_state["logs"].clear()
    append_log(f"$ {' '.join(command)}")
    persist_state()
    try:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_DIR,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        with process_lock:
            current_process = process
        if process.stdout:
            for line in process.stdout:
                append_log(line)
        returncode = process.wait()
    except Exception as exc:  # noqa: BLE001 - runner must expose infrastructure failures
        append_log(f"Falha ao iniciar o benchmark: {type(exc).__name__}: {exc}")
        returncode = 1
    finally:
        with process_lock:
            current_process = None
    finished_at = now()
    with state_lock:
        duration = elapsed_seconds(run_state.get("started_at"), finished_at)
        run_state.update(
            state="succeeded" if returncode == 0 else "failed",
            finished_at=finished_at,
            returncode=returncode,
            duration_seconds=duration,
            total_duration_seconds=round(
                float(run_state.get("total_duration_seconds") or 0.0) + duration,
                2,
            ),
        )
    persist_state()


def start_run() -> bool:
    with state_lock:
        if run_state["state"] in {"queued", "running"}:
            return False
        run_state["state"] = "queued"
        run_state["finished_at"] = None
    threading.Thread(target=execute, name=f"{RAG_NAME}-benchmark", daemon=True).start()
    return True


def cancel_run() -> bool:
    with process_lock:
        process = current_process
        if not process or process.poll() is not None:
            return False
        process.terminate()
    append_log("Cancelamento solicitado pelo dashboard.")
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "RagRunner/0.1"

    def send_json(self, payload: dict[str, Any], code: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json({"status": "ok", "rag": RAG_NAME})
        elif self.path == "/status":
            self.send_json(snapshot())
        else:
            self.send_json({"detail": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path == "/run":
            if not start_run():
                self.send_json({"detail": "Benchmark já está em execução"}, HTTPStatus.CONFLICT)
                return
            self.send_json({"accepted": True, "rag": RAG_NAME}, HTTPStatus.ACCEPTED)
        elif self.path == "/cancel":
            if not cancel_run():
                self.send_json({"detail": "Nenhuma execução ativa"}, HTTPStatus.CONFLICT)
                return
            self.send_json({"accepted": True, "rag": RAG_NAME}, HTTPStatus.ACCEPTED)
        else:
            self.send_json({"detail": "Not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return


if __name__ == "__main__":
    print(f"Runner {RAG_NAME} ouvindo em 0.0.0.0:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
