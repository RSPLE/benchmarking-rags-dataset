from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import signal
import socket
import socketserver
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

from benchmark_storage import append_event, atomic_json, exclusive_lock, now, sanitize
from main import PROJECTS, ROOT, uv_command
from service_entrypoint import role_environment

MAX_MESSAGE = 65536


def parse_command(text):
    parts = shlex.split(text)
    if not parts or len(text) > 1000:
        raise ValueError("Invalid command")
    action = parts[0].split("@", 1)[0].removeprefix("/")
    sizes = {
        "status": (0, 2),
        "executar": (2, 3),
        "retomar": (3, 4),
        "pausar": (2,),
        "falhas": (2,),
        "pergunta": (3,),
        "resultado": (2,),
    }
    args = parts[1:]
    if action not in sizes or len(args) not in sizes[action]:
        raise ValueError(
            "Use /status; /executar RAG N [USD]; /retomar RAG EXP N [USD]; /pausar RAG EXP; /falhas RAG EXP; /pergunta RAG EXP Q001; /resultado RAG EXP"
        )
    if args and args[0] not in PROJECTS:
        raise ValueError("Unknown RAG")
    if args and action != "executar" and not re.fullmatch(r"[0-9a-f]{64}", args[1]):
        raise ValueError("Expected the full experiment ID")
    if action in {"executar", "retomar"}:
        position = 1 if action == "executar" else 2
        count = int(args[position])
        cost = float(args[position + 1]) if len(args) > position + 1 else None
        if not 1 <= count <= 90 or (cost is not None and (not math.isfinite(cost) or cost <= 0)):
            raise ValueError("Expected 1..90 attempts and, optionally, a positive USD budget")
        return {
            "action": action,
            "project": args[0],
            "experiment": args[1] if action == "retomar" else None,
            "questions": count,
            "budget": cost,
        }
    if action == "pergunta" and (
        not re.fullmatch(r"Q\d{3}", args[2]) or not 1 <= int(args[2][1:]) <= 90
    ):
        raise ValueError("Invalid question ID")
    return {"action": action, "args": args}


class Control:
    def __init__(self, root, database, allowed, *, enabled=False, max_budget=0.0, profiles=None):
        self.root = Path(root).resolve()
        self.database = Path(database).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.allowed = set(allowed)
        if not self.allowed or any(type(value) is not int or value <= 0 for value in self.allowed):
            raise ValueError("Explicit numeric authorized user IDs are required")
        if not math.isfinite(max_budget) or max_budget < 0:
            raise ValueError("Invalid server budget cap")
        self.enabled, self.max_budget, self.profiles = enabled, max_budget, profiles or {}
        self.db = sqlite3.connect(self.database)
        os.chmod(self.database, 0o600)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, user INTEGER NOT NULL, request TEXT NOT NULL, response TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request TEXT NOT NULL, state TEXT NOT NULL, created TEXT NOT NULL, finished TEXT, code INTEGER)"
        )
        self.db.execute(
            "UPDATE jobs SET state='interrupted', finished=? WHERE state IN ('queued','running')",
            (now(),),
        )
        self.db.commit()

    def directory(self, project, experiment):
        path = (self.root / project / experiment).resolve()
        if not path.is_relative_to(self.root) or not (path / "checkpoint.json").is_file():
            raise ValueError("Experiment not found")
        return path

    def job_event(self, identifier, state):
        append_event(
            self.root / "control_events.jsonl",
            {
                "event_id": uuid.uuid4().hex,
                "job_id": identifier,
                "kind": "job_state",
                "state": state,
                "at": now(),
            },
            mode=0o640,
        )

    def handle(self, request):
        user = request.get("user_id")
        if type(user) is not int or user not in self.allowed:
            raise PermissionError("Unauthorized user")
        identifier = request.get("command_id", "")
        if not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", identifier):
            raise ValueError("Invalid command ID")
        command = parse_command(request.get("text", ""))
        encoded = json.dumps(command, sort_keys=True)
        prior = self.db.execute(
            "SELECT user, request, response FROM commands WHERE id=?", (identifier,)
        ).fetchone()
        if prior:
            if prior[:2] != (user, encoded):
                raise ValueError("Command ID conflicts with a previous request")
            return json.loads(prior[2])
        with self.db:
            response = self.execute(identifier, command)
            self.db.execute(
                "INSERT INTO commands VALUES (?, ?, ?, ?)",
                (identifier, user, encoded, json.dumps(response)),
            )
        return response

    def execute(self, identifier, command):
        action = command["action"]
        if action in {"executar", "retomar"}:
            if not self.enabled:
                raise ValueError("Remote execution is disabled by the operator")
            budget = command["budget"]
            if self.max_budget and budget is not None and budget > self.max_budget:
                raise ValueError("Requested budget exceeds the server cap")
            if budget is None and self.max_budget:
                budget = self.max_budget
            if self.db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone():
                raise ValueError("A job is already queued or running")
            profile = self.profiles.get(command["project"])
            if not isinstance(profile, dict) or profile.get("mode") not in {"full", "evaluate"}:
                raise ValueError("The operator must configure this RAG profile first")
            if profile["mode"] == "evaluate" and not Path(profile.get("frozen", "")).is_file():
                raise ValueError("Frozen answers are missing")
            if action == "retomar":
                directory = self.directory(command["project"], command["experiment"])
                manifest = json.loads((directory / "manifest.json").read_text())
                if manifest.get("mode") != profile["mode"]:
                    raise ValueError("Profile and saved experiment modes differ")
            command = {**command, "budget": budget, "profile": profile}
            self.db.execute(
                "INSERT INTO jobs (id, request, state, created) VALUES (?, ?, 'queued', ?)",
                (identifier, json.dumps(command), now()),
            )
            return {"job_id": identifier, "state": "queued", "budget_usd": command["budget"]}
        args = command["args"]
        if action == "status" and not args:
            jobs = [
                {"job_id": row[0], "state": row[1], "created": row[2], "finished": row[3]}
                for row in self.db.execute(
                    "SELECT id,state,created,finished FROM jobs ORDER BY rowid DESC LIMIT 10"
                )
            ]
            summaries = []
            for path in sorted(self.root.glob("*/*/summary.json")):
                if path.resolve().is_relative_to(self.root):
                    summaries.append(json.loads(path.read_text()))
            return {"jobs": jobs, "experiments": summaries[-10:]}
        directory = self.directory(*args[:2])
        if action == "pausar":
            (directory / "pause.request").touch(mode=0o600)
            return {"state": "pause_requested", "experiment_id": args[1]}
        if action == "status":
            return json.loads((directory / "summary.json").read_text())
        if action == "resultado":
            return {"document": json.loads((directory / "public_results.json").read_text())}
        checkpoint = json.loads((directory / "checkpoint.json").read_text())
        items = checkpoint["items"]
        if action == "pergunta":
            if args[2] not in items:
                return {"question_id": args[2], "status": "pending"}
            items = {args[2]: items[args[2]]}
        else:
            items = {key: value for key, value in items.items() if value.get("status") == "failed"}
        return {
            "items": {
                key: {
                    field: value.get(field)
                    for field in (
                        "status",
                        "attempts",
                        "failed_stage",
                        "error_type",
                        "stage_failures",
                        "retry_after",
                    )
                }
                | {
                    "metrics": {
                        name: {
                            field: entry.get(field)
                            for field in ("status", "value", "seconds", "attempts")
                        }
                        for name, entry in value.get("metrics", {}).items()
                    }
                }
                for key, value in items.items()
            }
        }


class Worker:
    def __init__(self, control):
        self.control = control
        self.process = self.job = self.log = None
        self.started = self.stopping = None

    def tick(self):
        if self.process:
            limit = int(os.getenv("BENCHMARK_MAX_SECONDS", "3600"))
            question_expired = False
            assignment = self.control.database.parent / f"{self.job}.json"
            if assignment.is_file():
                try:
                    location = json.loads(assignment.read_text())
                    directory = self.control.directory(
                        location["project"], location["experiment_id"]
                    )
                    progress = json.loads((directory / "progress.json").read_text())
                    question_expired = (
                        progress.get("run_id") == location.get("run_id")
                        and time.monotonic() >= progress["deadline_monotonic"]
                    )
                except (OSError, ValueError, KeyError):
                    pass
            if self.process.poll() is None and (
                time.monotonic() - self.started >= limit or question_expired
            ):
                self.stop()
            code = self.process.poll()
            if code is None:
                return
            self.control.db.execute(
                "UPDATE jobs SET state=?,finished=?,code=? WHERE id=?",
                ("finished" if code == 0 else "stopped", now(), code, self.job),
            )
            self.control.db.commit()
            self.control.job_event(self.job, "finished" if code == 0 else "stopped")
            self.log.close()
            self.process = self.job = self.log = None
            return
        row = self.control.db.execute(
            "SELECT id,request FROM jobs WHERE state='queued' ORDER BY rowid LIMIT 1"
        ).fetchone()
        if not row:
            return
        identifier, encoded = row
        command = json.loads(encoded)
        self.control.db.execute("UPDATE jobs SET state='running' WHERE id=?", (identifier,))
        self.control.db.commit()
        self.control.job_event(identifier, "running")
        try:
            self.launch(identifier, command)
        except Exception as exc:
            if self.log:
                self.log.close()
                self.log = None
            self.control.db.execute(
                "UPDATE jobs SET state='failed',finished=? WHERE id=?", (now(), identifier)
            )
            self.control.db.commit()
            self.control.job_event(identifier, "failed")
            atomic_json(
                self.control.database.parent / f"{identifier}.error.json",
                {"error": sanitize(str(exc))},
            )

    def launch(self, identifier, command):
        profile = command["profile"]
        reserve = float(os.getenv("BENCHMARK_RESERVE_COST_USD", "0"))
        caps = [
            float(os.getenv(name, "0"))
            for name in (
                "BENCHMARK_MAX_COST_USD",
                "BENCHMARK_MAX_QUESTION_COST_USD",
                "BENCHMARK_MAX_PREPARATION_COST_USD",
                "BENCHMARK_MAX_DAILY_COST_USD",
                "BENCHMARK_MAX_PERIOD_COST_USD",
            )
        ]
        if command["budget"] is not None:
            caps.append(command["budget"])
        if any(not math.isfinite(value) or value < 0 for value in [reserve, *caps]):
            raise ValueError("Cost limits and reservations must be finite and nonnegative")
        if any(caps) and reserve <= 0:
            raise ValueError(
                "Configure a positive per-call reservation when enabling a monetary limit"
            )
        env = role_environment("worker", os.environ)
        env.pop("VIRTUAL_ENV", None)
        env.update(
            BENCHMARK_OUTPUT_DIR=str(self.control.root),
            BENCHMARK_QUESTION_LIMIT=str(command["questions"]),
            BENCHMARK_MODE=profile["mode"],
            BENCHMARK_PROJECT=command["project"],
            BENCHMARK_JOB_FILE=str(self.control.database.parent / f"{identifier}.json"),
        )
        batch_caps = [value for value in (caps[0], command["budget"]) if value]
        env["BENCHMARK_MAX_COST_USD"] = str(min(batch_caps) if batch_caps else 0)
        env["BENCHMARK_EXPECTED_EXPERIMENT"] = command.get("experiment") or ""
        env["BENCHMARK_FROZEN_FILE"] = profile.get("frozen", "")
        allowed = {
            "DOCS_DIR",
            "CHROMA_PERSIST_DIR",
            "BENCHMARK_KG_MODE",
            "BENCHMARK_KG_SNAPSHOT",
            "BENCHMARK_SELECTION",
        }
        settings = profile.get("environment", {})
        if set(settings) - allowed or any(
            not isinstance(value, str) for value in settings.values()
        ):
            raise ValueError("Profile contains unsupported settings")
        env.update(settings)
        for name in (
            "DOCS_DIR",
            "CHROMA_PERSIST_DIR",
            "BENCHMARK_FROZEN_FILE",
            "BENCHMARK_KG_SNAPSHOT",
            "BENCHMARK_BUDGET_DIR",
        ):
            if env.get(name):
                env[name] = str((ROOT / env[name]).resolve())
        if command.get("experiment"):
            directory = self.control.directory(command["project"], command["experiment"])
            if not (directory / "manifest.json").is_file():
                raise ValueError("Missing manifest")
        entrypoint = str(ROOT / "evaluate_saved.py") if profile["mode"] == "evaluate" else "main.py"
        project = ROOT / "rags" / command["project"]
        self.log = (self.control.database.parent / f"{identifier}.log").open("w")
        os.chmod(self.log.name, 0o600)
        self.process = subprocess.Popen(
            [*uv_command(), "run", "--project", str(project), "--locked", "python", entrypoint],
            cwd=project,
            env=env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.job, self.started, self.stopping = identifier, time.monotonic(), None

    def stop(self):
        if self.process and self.process.poll() is None:
            if self.stopping is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.stopping = time.monotonic()
            elif time.monotonic() - self.stopping >= 15:
                os.killpg(self.process.pid, signal.SIGKILL)


def call_control(path, request):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10)
        client.connect(str(path))
        client.sendall(json.dumps(request).encode() + b"\n")
        response = client.makefile("rb").readline(MAX_MESSAGE + 1)
        if len(response) > MAX_MESSAGE:
            raise ValueError("Control response exceeds limit")
        result = json.loads(response)
        if not result.get("ok"):
            raise ValueError(result.get("error", "Control failed"))
        return result["result"]


def environment_profiles():
    mode = os.getenv("BENCHMARK_MODE", "full")
    if mode not in {"full", "evaluate"}:
        raise ValueError("Unsupported benchmark mode")
    if mode == "evaluate":
        project = os.getenv("BENCHMARK_PROJECT")
        frozen = os.getenv("BENCHMARK_FROZEN_FILE")
        if project not in PROJECTS or not frozen:
            raise ValueError("Evaluation requires BENCHMARK_PROJECT and BENCHMARK_FROZEN_FILE")
        return {project: {"mode": mode, "frozen": str((ROOT / frozen).resolve())}}
    return {project: {"mode": mode} for project in PROJECTS}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--profiles", type=Path)
    args = parser.parse_args()
    allowed = [int(value) for value in os.environ["TELEGRAM_ALLOWED_USER_IDS"].split(",")]
    args.socket.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(args.database.with_suffix(".lock")):
        control = Control(
            args.root,
            args.database,
            allowed,
            enabled=os.getenv("BENCHMARK_REMOTE_ENABLED") == "true",
            max_budget=float(os.getenv("BENCHMARK_REMOTE_MAX_COST_USD", "0")),
            profiles=json.loads(args.profiles.read_text())
            if args.profiles
            else environment_profiles(),
        )
        worker = Worker(control)

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(5)
                try:
                    raw = self.rfile.readline(MAX_MESSAGE + 1)
                    if len(raw) > MAX_MESSAGE:
                        raise ValueError("Request exceeds limit")
                    result = {"ok": True, "result": control.handle(json.loads(raw))}
                except Exception as exc:
                    result = {"ok": False, "error": sanitize(str(exc))}
                encoded = json.dumps(result, ensure_ascii=False).encode()
                if len(encoded) > MAX_MESSAGE:
                    encoded = b'{"ok":false,"error":"Select a smaller result"}'
                self.wfile.write(encoded + b"\n")

        class Server(socketserver.UnixStreamServer):
            def service_actions(self):
                worker.tick()

        args.socket.unlink(missing_ok=True)
        with Server(str(args.socket), Handler) as server:
            os.chmod(args.socket, 0o660)
            try:
                server.serve_forever(poll_interval=0.5)
            except KeyboardInterrupt:
                pass
            finally:
                while worker.process and worker.process.poll() is None:
                    worker.stop()
                    time.sleep(0.1)
                control.db.close()
                args.socket.unlink(missing_ok=True)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
