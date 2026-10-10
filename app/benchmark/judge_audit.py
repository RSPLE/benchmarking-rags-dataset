from __future__ import annotations

import contextlib
import contextvars
import json
import math
import threading
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from app.benchmark.config import configuration
from app.benchmark.storage import append_event, fingerprint, now, sanitize

_context = contextvars.ContextVar("judge_audit_context", default=None)


@contextlib.contextmanager
def judge_context(directory, question_id, run_id, **metadata):
    token = _context.set(
        {"directory": Path(directory), "question_id": question_id, "run_id": run_id, **metadata}
    )
    try:
        yield
    finally:
        _context.reset(token)


def private_value(value):
    """Persist visible judge output, redacting credentials even in malformed responses."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, dict):
        return {str(key): private_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [private_value(item) for item in value]
    if isinstance(value, str):
        return sanitize(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize(str(value))


def judge_configuration():
    config = configuration()
    result = {
        key: value
        for key, value in config.items()
        if key == "LLM_PROVIDER"
        or "MODEL" in key
        or key.startswith("RAGAS_")
        or key in {"EMBEDDING_PROVIDER", "EMBEDDING_DIMENSIONS", "OPENAI_REASONING_EFFORT"}
    }
    try:
        result["ragas_version"] = version("ragas")
    except PackageNotFoundError:
        result["ragas_version"] = None
    return result


class JudgeResult(dict):
    def __init__(self, values, trace_id):
        super().__init__(values)
        self.judge_trace_id = trace_id


class JudgeTrace:
    def __init__(self, metric, artifact):
        context = dict(_context.get() or {})
        directory = context.pop("directory", None)
        self.path = directory / "judge_responses.jsonl" if directory else None
        self.identity = {
            "trace_id": uuid.uuid4().hex,
            "metric": metric,
            "input_sha256": fingerprint(artifact),
            **context,
        }
        self.lock = threading.Lock()
        self.events = []
        self.emit(
            "started",
            configuration=judge_configuration(),
            inputs={
                key: artifact.get(key) for key in ("question", "answer", "contexts", "ground_truth")
            },
        )

    def emit(self, kind, **values):
        event = private_value({**self.identity, "at": now(), "kind": kind, **values})
        with self.lock:
            self.events.append(event)
            if self.path:
                append_event(self.path, event)

    @property
    def trace_id(self):
        return self.identity["trace_id"]

    def callback(self):
        from langchain_core.callbacks import BaseCallbackHandler

        trace = self

        class Capture(BaseCallbackHandler):
            raise_error = True

            def on_llm_end(self, response, *, run_id, **kwargs):
                # Only the model's visible completion, never private reasoning fields,
                # credentials, HTTP headers or serialized client objects.
                trace.emit(
                    "response",
                    call_id=str(run_id),
                    responses=[
                        [generation.text for generation in batch] for batch in response.generations
                    ],
                )

            def on_llm_error(self, error, *, run_id, **kwargs):
                trace.emit("call_error", call_id=str(run_id), error=str(error))

        return Capture()


def read_events(content):
    if isinstance(content, bytes):
        # Synchronization can catch a write halfway through a UTF-8 character.
        content = content[: content.rfind(b"\n") + 1].decode("utf-8")
    events = []
    for line in content.splitlines(keepends=True):
        if not line.endswith("\n"):
            continue
        if line.strip():
            events.append(json.loads(line))
    return events
