from __future__ import annotations

import argparse
import os
from pathlib import Path


def positive_integer(value):
    try:
        parsed = int(value)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("Expected a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("Expected a positive integer")
    return parsed


def add_run_options(parser):
    parser.add_argument("--provider", choices=("openrouter", "openai"))
    parser.add_argument("--questions", "--limit", dest="questions", type=positive_integer)
    parser.add_argument("--mode", choices=("full", "evaluate"))
    parser.add_argument("--frozen", type=Path)
    parser.add_argument("--selection", choices=("unresolved", "pending", "failed"))
    parser.add_argument("--max-calls", type=positive_integer)
    parser.add_argument("--max-seconds", type=positive_integer)
    parser.add_argument("--question-timeout", type=positive_integer)
    parser.add_argument("--repetition", type=positive_integer)


def option_environment(options):
    names = {
        "provider": "LLM_PROVIDER",
        "questions": "BENCHMARK_QUESTION_LIMIT",
        "mode": "BENCHMARK_MODE",
        "frozen": "BENCHMARK_FROZEN_FILE",
        "selection": "BENCHMARK_SELECTION",
        "max_calls": "BENCHMARK_MAX_CALLS",
        "max_seconds": "BENCHMARK_MAX_SECONDS",
        "question_timeout": "BENCHMARK_QUESTION_TIMEOUT_SECONDS",
        "repetition": "BENCHMARK_REPETITION",
    }
    values = vars(options) if isinstance(options, argparse.Namespace) else options
    return {
        target: str(values[key]) for key, target in names.items() if values.get(key) is not None
    }


def validate_run_options(options, projects):
    values = vars(options) if isinstance(options, argparse.Namespace) else options
    mode = values.get("mode") or os.getenv("BENCHMARK_MODE", "full")
    frozen = values.get("frozen") or os.getenv("BENCHMARK_FROZEN_FILE")
    if mode == "evaluate" and not frozen:
        raise ValueError("--mode evaluate requires --frozen or BENCHMARK_FROZEN_FILE")
    if values.get("frozen") and mode != "evaluate":
        raise ValueError("--frozen requires --mode evaluate")
    if mode == "evaluate" and len(projects) != 1:
        raise ValueError("Frozen answers must target one RAG")
    return mode


class CommandParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)
