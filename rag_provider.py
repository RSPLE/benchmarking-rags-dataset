from __future__ import annotations

import os
from typing import Any

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ragas.llms.base import LangchainLLMWrapper

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
SUPPORTED_PROVIDERS = {"openai", "openrouter"}


def _provider(variable: str, default: str) -> str:
    provider = os.getenv(variable, default).strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        choices = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ValueError(f"{variable} deve ser um destes valores: {choices}.")
    return provider


def _required(variable: str, provider: str) -> str:
    value = os.getenv(variable, "").strip()
    if not value:
        raise RuntimeError(
            f"{variable} nao encontrada para o provedor {provider}. "
            "Crie um arquivo .env a partir de .env.example."
        )
    return value


def _positive_int(variable: str, default: int) -> int:
    value = os.getenv(variable, str(default)).strip()
    try:
        max_tokens = int(value)
    except ValueError as exc:
        raise ValueError(f"{variable} deve ser um inteiro positivo.") from exc
    if max_tokens <= 0:
        raise ValueError(f"{variable} deve ser um inteiro positivo.")
    return max_tokens


def _max_tokens(variable: str = "LLM_MAX_TOKENS", default: int = 4096) -> int:
    return _positive_int(variable, default)


def _timeout_seconds() -> int:
    return _positive_int("LLM_TIMEOUT_SECONDS", 600)


def _openrouter_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    referer = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
    title = os.getenv("OPENROUTER_APP_TITLE", "").strip()
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-OpenRouter-Title"] = title
    return headers


def role_key(provider, role):
    prefix = provider.upper()
    name = f"{prefix}_{role.upper()}_API_KEY"
    value = (
        os.getenv(name)
        or (os.getenv(f"{prefix}_JUDGE_API_KEY") if role == "judge_embedding" else None)
        or os.getenv(f"{prefix}_API_KEY")
    )
    if not value:
        raise RuntimeError(f"Missing {name} or {prefix}_API_KEY")
    if os.getenv("BENCHMARK_CREDIT_SCOPE") == "judge" and role not in {"judge", "judge_embedding"}:
        raise RuntimeError("Judge-only credentials cannot fund generation or preparation")
    return value


def build_llm(max_tokens: int | None = None, *, judge: bool = False) -> ChatOpenAI:
    provider = _provider("LLM_PROVIDER", "openrouter")
    from benchmark_usage import http_clients

    timeout = _positive_int("RAGAS_TIMEOUT_SECONDS", 600) if judge else _timeout_seconds()
    role = "judge" if judge else "generation"
    clients = http_clients(timeout, role=role)

    if provider == "openrouter":
        kwargs: dict[str, Any] = {
            "api_key": role_key("openrouter", role),
            "base_url": os.getenv("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL),
            "model": (os.getenv("OPENROUTER_JUDGE_MODEL") if judge else None)
            or _required("OPENROUTER_MODEL", provider),
            "max_tokens": max_tokens or _max_tokens(),
            "timeout": timeout,
            "max_retries": 0,
            **clients,
            "temperature": None,
            "use_responses_api": False,
        }
        headers = _openrouter_headers()
        if headers:
            kwargs["default_headers"] = headers
        return ChatOpenAI(**kwargs)

    kwargs = {
        "api_key": role_key("openai", role),
        "model": (os.getenv("OPENAI_JUDGE_MODEL") if judge else None)
        or os.getenv("OPENAI_MODEL", "gpt-5.5"),
        "max_tokens": max_tokens or _max_tokens(),
        "timeout": timeout,
        "max_retries": 0,
        **clients,
        "temperature": None,
        "use_responses_api": True,
    }
    reasoning_effort = os.getenv("OPENAI_REASONING_EFFORT", "medium").strip()
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    return ChatOpenAI(**kwargs)


def build_embeddings(*, judge=False) -> OpenAIEmbeddings:
    from benchmark_usage import http_clients

    role = "judge_embedding" if judge else "embedding"
    clients = http_clients(_timeout_seconds(), role=role)
    default_provider = _provider("LLM_PROVIDER", "openrouter")
    provider = _provider("EMBEDDING_PROVIDER", default_provider)

    if provider == "openrouter":
        kwargs: dict[str, Any] = {
            **clients,
            "max_retries": 0,
            "check_embedding_ctx_length": False,
            **(
                {"dimensions": int(os.environ["EMBEDDING_DIMENSIONS"])}
                if os.getenv("EMBEDDING_DIMENSIONS")
                else {}
            ),
            "api_key": role_key("openrouter", role),
            "base_url": os.getenv("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL),
            "model": os.getenv(
                "OPENROUTER_EMBEDDING_MODEL",
                "openai/text-embedding-3-small",
            ),
        }
        headers = _openrouter_headers()
        if headers:
            kwargs["default_headers"] = headers
        return OpenAIEmbeddings(**kwargs)

    return OpenAIEmbeddings(
        **clients,
        max_retries=0,
        **(
            {"dimensions": int(os.environ["EMBEDDING_DIMENSIONS"])}
            if os.getenv("EMBEDDING_DIMENSIONS")
            else {}
        ),
        api_key=role_key("openai", role),
        model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large"),
    )


def build_ragas_llm() -> LangchainLLMWrapper:
    return LangchainLLMWrapper(
        build_llm(max_tokens=_max_tokens("RAGAS_MAX_TOKENS", 2048), judge=True),
        bypass_n=True,
        bypass_temperature=True,
    )
