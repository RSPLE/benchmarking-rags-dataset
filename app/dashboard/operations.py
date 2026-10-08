from __future__ import annotations

import shlex
import uuid

from app.benchmark.control import call_control, parse_command
from app.cli import PROJECTS
from app.dashboard.sessions import session_identity

OPTIONS = (
    "questions",
    "selection",
    "provider",
    "mode",
    "frozen",
    "max_calls",
    "max_seconds",
    "question_timeout",
    "repetition",
)


def build_command(action, project, experiment=None, **options):
    if project not in PROJECTS and not (project == "all" and action == "executar"):
        raise ValueError("Selecione um RAG válido.")
    if action not in {"executar", "retomar", "pausar"}:
        raise ValueError("Ação inválida.")
    parts = ["/" + action, project]
    if action in {"retomar", "pausar"}:
        if not experiment:
            raise ValueError("Selecione o experimento.")
        parts.append(experiment)
    for name in OPTIONS:
        value = options.get(name)
        if value is not None and value != "":
            parts.extend(["--" + name.replace("_", "-"), str(value)])
    command = shlex.join(parts)
    parse_command(command)
    return command


def request_control(settings, token, text, command_id=None):
    identity = session_identity(settings.database, token)
    if not identity:
        raise PermissionError("Sua sessão expirou. Entre novamente.")
    if settings.control_user_id <= 0:
        raise ValueError("Configure o usuário autorizado de controle na .env.")
    parse_command(text)
    identifier = command_id or "web:" + uuid.uuid4().hex
    return call_control(
        settings.control_socket,
        {
            "user_id": settings.control_user_id,
            "command_id": identifier,
            "text": text,
        },
    )
