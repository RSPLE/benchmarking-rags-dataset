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
import tempfile
import time
import uuid
from pathlib import Path

from app.benchmark.options import CommandParser, add_run_options, option_environment
from app.benchmark.storage import exclusive_lock, fingerprint, now, sanitize
from app.cli import PROJECTS, ROOT, resolve_projects, uv_command
from app.services.entrypoint import role_environment

MAX_MESSAGE = 65536


def parse_command(text):
    if not isinstance(text, str) or len(text) > 2000:
        raise ValueError("Invalid command")
    parts = shlex.split(text)
    if not parts:
        raise ValueError("Invalid command")
    action = parts[0].split("@", 1)[0].removeprefix("/")
    action = {"run": "executar", "resume": "retomar", "start": "ajuda", "help": "ajuda"}.get(
        action, action
    )
    args = parts[1:]
    if action == "eventos":
        if len(args) != 1 or not re.fullmatch(r"[0-9]{1,18}", args[0]):
            raise ValueError("Expected an event cursor")
        return {"action": action, "after": int(args[0])}
    if action in {"executar", "retomar"}:
        parser = CommandParser(prog="/" + action, add_help=False, allow_abbrev=False)
        parser.add_argument("targets", nargs="+")
        add_run_options(parser)
        options = vars(parser.parse_args(args))
        targets = options.pop("targets")
        experiment = None
        if action == "retomar":
            if len(targets) < 2 or not re.fullmatch(r"[0-9a-f]{64}", targets[1]):
                raise ValueError("Expected RAG and the full experiment ID")
            project, experiment, *legacy = targets
            projects = resolve_projects([project])
        else:
            split = next(
                (i for i, part in enumerate(targets) if part not in PROJECTS and part != "all"),
                len(targets),
            )
            projects = resolve_projects(targets[:split])
            legacy = targets[split:]
        if not projects or any(project not in PROJECTS for project in projects):
            raise ValueError("Unknown RAG")
        if action == "retomar" and len(projects) != 1:
            raise ValueError("Resume requires one RAG")
        if len(legacy) > 2:
            raise ValueError("Expected N [USD] or named flags")
        budget = None
        if legacy:
            if options.get("questions") is not None:
                raise ValueError("Use either N or --questions")
            options["questions"] = int(legacy[0])
            if len(legacy) == 2:
                budget = float(legacy[1])
        if options.get("questions") is not None and not 1 <= options["questions"] <= 90:
            raise ValueError("Expected 1..90 attempts")
        if budget is not None and (not math.isfinite(budget) or budget <= 0):
            raise ValueError("Expected a positive optional USD limit")
        options = {
            key: str(value) if isinstance(value, Path) else value
            for key, value in options.items()
            if value is not None
        }
        return {
            "action": action,
            "project": projects[0],
            "projects": projects,
            "experiment": experiment,
            "questions": options.get("questions", 90),
            "budget": budget,
            "options": options,
        }
    sizes = {
        "status": (0, 2),
        "pausar": (2,),
        "falhas": (2,),
        "pergunta": (3,),
        "resultado": (2,),
        "ajuda": (0,),
        "notificar": (0,),
    }
    if action not in sizes or len(args) not in sizes[action]:
        raise ValueError("Use /ajuda to list commands and flags")
    if args and (args[0] not in PROJECTS or not re.fullmatch(r"[0-9a-f]{64}", args[1])):
        raise ValueError("Expected RAG and the full experiment ID")
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
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(jobs)")}
        if "batch" not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN batch TEXT")
        if "error" not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN error TEXT")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS job_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)"
        )
        interrupted = self.db.execute(
            "SELECT id FROM jobs WHERE state IN ('queued','running')"
        ).fetchall()
        self.db.execute(
            "UPDATE jobs SET state='interrupted', finished=?, error=? WHERE state IN ('queued','running')",
            (now(), "O serviço de execução reiniciou. Envie um novo pedido para continuar."),
        )
        for (identifier,) in interrupted:
            self.job_event(identifier, "interrupted")
        self.db.commit()

    def directory(self, project, experiment):
        path = (self.root / project / experiment).resolve()
        if not path.is_relative_to(self.root) or not (path / "checkpoint.json").is_file():
            raise ValueError("Experiment not found")
        return path

    def job_event(self, identifier, state):
        row = self.db.execute(
            "SELECT request,error,code FROM jobs WHERE id=?", (identifier,)
        ).fetchone()
        command = json.loads(row[0])
        self.db.execute(
            "INSERT INTO job_events (payload) VALUES (?)",
            (
                json.dumps(
                    {
                        "event_id": uuid.uuid4().hex,
                        "job_id": identifier,
                        "project": command["project"],
                        "kind": "job_state",
                        "state": state,
                        "error": row[1],
                        "code": row[2],
                        "at": now(),
                    }
                ),
            ),
        )

    def handle(self, request):
        user = request.get("user_id")
        if type(user) is not int or user not in self.allowed:
            raise PermissionError("Unauthorized user")
        identifier = request.get("command_id", "")
        if not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", identifier):
            raise ValueError("Invalid command ID")
        try:
            command = parse_command(request.get("text", ""))
        except ValueError as exc:
            text = request.get("text", "")
            if isinstance(text, str) and re.match(
                r"^/(executar|retomar|pausar|run|resume)\b", text
            ):
                self.request_event(identifier, "request_rejected", error=sanitize(str(exc))[:1500])
                self.db.commit()
            raise
        if command["action"] in {"status", "eventos", "ajuda", "falhas", "pergunta", "resultado"}:
            return self.execute(identifier, command)
        encoded = json.dumps(command, sort_keys=True)
        prior = self.db.execute(
            "SELECT user, request, response FROM commands WHERE id=?", (identifier,)
        ).fetchone()
        if prior:
            if prior[:2] != (user, encoded):
                raise ValueError("Command ID conflicts with a previous request")
            return json.loads(prior[2])
        try:
            with self.db:
                response = self.execute(identifier, command)
                self.db.execute(
                    "INSERT INTO commands VALUES (?, ?, ?, ?)",
                    (identifier, user, encoded, json.dumps(response)),
                )
        except (ValueError, OSError) as exc:
            self.request_event(
                identifier,
                "request_rejected",
                error=sanitize(str(exc))[:1500],
                project=command.get("project"),
            )
            self.db.commit()
            raise
        return response

    def request_event(self, identifier, kind, **values):
        event = {
            "event_id": fingerprint({"request": identifier, "kind": kind, **values}),
            "kind": kind,
            "job_id": identifier,
            "at": now(),
            **values,
        }
        self.db.execute("INSERT INTO job_events (payload) VALUES (?)", (json.dumps(event),))

    def execute(self, identifier, command):
        action = command["action"]
        if action == "notificar":
            self.request_event(identifier, "notification_test")
            return {"state": "notification_queued", "request_id": identifier}
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
            projects = command.get("projects", [command["project"]])
            jobs = []
            for index, project in enumerate(projects):
                configured = self.profiles.get(project)
                if not isinstance(configured, dict):
                    raise ValueError("The operator must configure this RAG profile first")
                profile = {**configured, "environment": dict(configured.get("environment", {}))}
                options = command.get("options", {})
                mode = options.get("mode", profile.get("mode", "full"))
                if mode not in {"full", "evaluate"}:
                    raise ValueError("Invalid benchmark mode")
                if options.get("frozen") and mode != "evaluate":
                    raise ValueError("--frozen requires --mode evaluate")
                profile["mode"] = mode
                if mode == "evaluate":
                    frozen = (
                        options.get("frozen")
                        or profile.get("frozen")
                        or os.getenv("BENCHMARK_FROZEN_FILE")
                    )
                    if len(projects) != 1 or not frozen:
                        raise ValueError("Evaluation requires one RAG and a frozen file")
                    path = (ROOT / frozen).resolve()
                    configured_path = (
                        Path(configured["frozen"]).resolve() if configured.get("frozen") else None
                    )
                    approved = path == configured_path or any(
                        path.is_relative_to(root)
                        for root in ((ROOT / "frozen").resolve(), (self.root / "frozen").resolve())
                    )
                    if not approved or not path.is_file():
                        raise ValueError(
                            "Frozen file must exist under frozen/ or the configured profile path"
                        )
                    profile["frozen"] = str(path)
                else:
                    profile.pop("frozen", None)
                if action == "retomar":
                    directory = self.directory(project, command["experiment"])
                    manifest = json.loads((directory / "manifest.json").read_text())
                    if manifest.get("mode") != mode:
                        raise ValueError("Profile and saved experiment modes differ")
                job_id = identifier if len(projects) == 1 else f"{identifier}:{index + 1}"
                job = {**command, "project": project, "budget": budget, "profile": profile}
                self.db.execute(
                    "INSERT INTO jobs (id, request, state, created, batch) VALUES (?, ?, 'queued', ?, ?)",
                    (job_id, json.dumps(job), now(), identifier),
                )
                jobs.append({"job_id": job_id, "project": project, "state": "queued"})
                self.job_event(job_id, "queued")
            return {
                "job_id": jobs[0]["job_id"],
                "jobs": jobs,
                "state": "queued",
                "budget_usd": budget,
            }
        if action == "eventos":
            rows = self.db.execute(
                "SELECT sequence,payload FROM job_events WHERE sequence>? ORDER BY sequence LIMIT 20",
                (command["after"],),
            ).fetchall()
            return {
                "events": [json.loads(row[1]) for row in rows],
                "cursor": rows[-1][0] if rows else command["after"],
            }
        if action == "ajuda":
            parser = CommandParser(prog="/executar RAG [RAG ...]", add_help=False)
            add_run_options(parser)
            return {
                "commands": "/executar RAG|all; /retomar RAG EXP; /status [RAG EXP]; /pausar RAG EXP; /falhas RAG EXP; /pergunta RAG EXP Q001; /resultado RAG EXP",
                "flags": parser.format_help(),
                "example": "/executar all --questions 1 --selection pending --repetition 1",
            }
        args = command["args"]
        if action == "status" and not args:
            jobs = [
                {
                    "job_id": row[0],
                    "state": row[1],
                    "created": row[2],
                    "finished": row[3],
                    "code": row[4],
                    "error": row[5],
                    "project": json.loads(row[6])["project"],
                }
                for row in self.db.execute(
                    "SELECT id,state,created,finished,code,error,request FROM jobs ORDER BY rowid DESC LIMIT 10"
                )
            ]
            summaries = []
            for path in sorted(self.root.glob("*/*/summary.json")):
                if path.resolve().is_relative_to(self.root):
                    try:
                        summary = json.loads(path.read_text())
                        progress_path = path.parent / "progress.json"
                        if summary.get("operation") == "running" and progress_path.is_file():
                            progress = json.loads(progress_path.read_text())
                            if progress.get("run_id") == summary.get("run_id"):
                                summary.update(progress)
                        summaries.append(summary)
                    except (OSError, ValueError, TypeError, AttributeError):
                        continue
            return {
                "jobs": jobs,
                "experiments": summaries[-10:],
                "enabled": self.enabled,
                "projects": list(self.profiles),
            }
        directory = self.directory(*args[:2])
        if action == "pausar":
            (directory / "pause.request").touch(mode=0o600)
            self.request_event(
                identifier, "pause_requested", project=args[0], experiment_id=args[1]
            )
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
        self.max_seconds = None
        self.stop_reason = None

    def tick(self):
        if self.process:
            limit = self.max_seconds or int(os.getenv("BENCHMARK_MAX_SECONDS", "3600"))
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
                self.stop_reason = (
                    "O tempo máximo por questão foi atingido."
                    if question_expired
                    else "O tempo máximo do lote foi atingido."
                )
                self.stop()
            code = self.process.poll()
            if code is None:
                return
            self.control.db.execute(
                "UPDATE jobs SET state=?,finished=?,code=?,error=? WHERE id=?",
                (
                    "finished" if code == 0 else "stopped",
                    now(),
                    code,
                    self.process_error(code) if code else None,
                    self.job,
                ),
            )
            if code != 0:
                self.cancel_batch(self.job)
            self.control.job_event(self.job, "finished" if code == 0 else "stopped")
            self.control.db.commit()
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
        self.control.job_event(identifier, "running")
        self.control.db.commit()
        try:
            self.launch(identifier, command)
        except Exception as exc:
            if self.log:
                self.log.close()
                self.log = None
            self.control.db.execute(
                "UPDATE jobs SET state='failed',finished=?,error=? WHERE id=?",
                (now(), sanitize(str(exc))[:1500], identifier),
            )
            self.cancel_batch(identifier)
            self.control.job_event(identifier, "failed")
            self.control.db.commit()
            self.process = self.job = None

    def process_error(self, code):
        if self.stop_reason:
            return self.stop_reason
        try:
            assignment = json.loads((self.control.database.parent / f"{self.job}.json").read_text())
            directory = self.control.directory(assignment["project"], assignment["experiment_id"])
            summary = json.loads((directory / "summary.json").read_text())
            alert = summary.get("alert", {})
            if summary.get("run_id") == assignment["run_id"] and alert.get("error"):
                return sanitize(
                    f"{alert.get('question_id', '')} · {alert.get('stage', '')}: {alert['error']}"
                )[:1500]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        try:
            with Path(self.log.name).open("rb") as log:
                log.seek(0, os.SEEK_END)
                log.seek(max(0, log.tell() - 8192))
                tail = log.read().decode("utf-8", errors="replace")
        except (OSError, TypeError):
            tail = ""
        for marker, message in (
            (
                "Permission denied",
                "Sem permissão para gravar arquivos. Confira o proprietário dos volumes do executor (UID/GID 1000).",
            ),
            (
                "Read-only file system",
                "O executor tentou gravar em um diretório somente leitura. Confira os volumes persistentes.",
            ),
            (
                "No space left on device",
                "O disco do executor está cheio. Libere espaço antes de tentar novamente.",
            ),
            (
                "ModuleNotFoundError",
                "Uma dependência Python está ausente. Reconstrua a imagem do serviço control.",
            ),
        ):
            if marker in tail:
                return message
        return f"O processo encerrou com código {code}. Consulte o log do lote no serviço control."

    def cancel_batch(self, identifier):
        batch = self.control.db.execute(
            "SELECT batch FROM jobs WHERE id=?", (identifier,)
        ).fetchone()
        if batch and batch[0]:
            cancelled = self.control.db.execute(
                "SELECT id FROM jobs WHERE batch=? AND state='queued'", (batch[0],)
            ).fetchall()
            self.control.db.execute(
                "UPDATE jobs SET state='cancelled', finished=?,error=? WHERE batch=? AND state='queued'",
                (now(), "Cancelado porque um RAG anterior deste lote falhou.", batch[0]),
            )
            for (job_id,) in cancelled:
                self.control.job_event(job_id, "cancelled")

    def launch(self, identifier, command):
        try:
            self.control.root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=self.control.root) as probe:
                probe.write(b"check")
                probe.flush()
                os.fsync(probe.fileno())
        except OSError as exc:
            raise ValueError(
                "Não foi possível gravar no diretório de resultados. "
                "Confira espaço livre e permissões do volume (UID/GID 1000 no Docker). "
                f"Detalhe: {exc.strerror or type(exc).__name__}"
            ) from exc
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
        env.update(option_environment(command.get("options", {})))
        env["BENCHMARK_MODE"] = profile["mode"]
        env["BENCHMARK_FROZEN_FILE"] = profile.get("frozen", "")
        self.max_seconds = int(env.get("BENCHMARK_MAX_SECONDS", "3600"))
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
        entrypoint = (
            str(ROOT / "app/benchmark/evaluate_saved.py")
            if profile["mode"] == "evaluate"
            else "main.py"
        )
        project = ROOT / "app" / "rags" / command["project"]
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
        self.stop_reason = None

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
