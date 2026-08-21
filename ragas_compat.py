"""Compatibility for a legacy optional Vertex AI import in RAGAS 0.4.x."""

from __future__ import annotations

import importlib
import sys
import types

LEGACY_VERTEX_MODULE = "langchain_community.chat_models.vertexai"


def ensure_ragas_langchain_compat() -> None:
    """Provide the removed optional class used only by RAGAS type checks.

    RAGAS 0.4.3 imports ``ChatVertexAI`` from the old LangChain Community
    module at import time. LangChain Community 0.4 removed that module, even
    when Vertex AI is not used. A minimal placeholder keeps the optional
    ``isinstance`` check importable without adding Google Vertex dependencies.
    """
    try:
        importlib.import_module(LEGACY_VERTEX_MODULE)
        return
    except ModuleNotFoundError as exc:
        if exc.name != LEGACY_VERTEX_MODULE:
            raise

    module = types.ModuleType(LEGACY_VERTEX_MODULE)
    chat_vertex_ai = type("ChatVertexAI", (), {"__module__": LEGACY_VERTEX_MODULE})
    module.ChatVertexAI = chat_vertex_ai
    sys.modules[LEGACY_VERTEX_MODULE] = module
