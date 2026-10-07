from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from numbers import Real
from time import sleep

from benchmark_config import positive_int
from benchmark_storage import append_event, now, sanitize

ACTIVE_LEDGER = None


def nonnegative_number(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
        or value < 0
    ):
        return None
    return value


def historical_usage(root):
    today = datetime.now(UTC).date().isoformat()
    pending = set()
    total = 0.0
    unknown = 0
    for path in root.glob("*/*/usage.jsonl"):
        for line in path.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                unknown += 1
                continue
            if not event.get("at", "").startswith(today):
                continue
            if event.get("kind") == "started":
                pending.add(event["call_id"])
            elif event.get("kind") == "finished":
                pending.discard(event["call_id"])
                cost = nonnegative_number(event.get("cost_usd"))
                if cost is None:
                    unknown += 1
                else:
                    total += cost
    return total, unknown + len(pending)


class BudgetExceeded(RuntimeError):
    pass


class ProviderResponseError(RuntimeError):
    def __init__(self, status_code, message):
        super().__init__(sanitize(message))
        self.status_code = status_code


class UsageLedger:
    def __init__(self, path, budget_root=None):
        self.path = path
        self.run_id = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.calls = self.question_calls = 0
        self.tokens = self.question_tokens = self.preparation_calls = 0
        self.cost = self.question_cost = 0.0
        self.unknown = 0
        self.question_id = self.metric = None
        self.stage = "preparation"
        self.started = self.question_started = time.monotonic()
        self.max_calls = positive_int("BENCHMARK_MAX_CALLS", 200)
        self.max_question_calls = positive_int("BENCHMARK_MAX_QUESTION_CALLS", 60)
        self.max_tokens = positive_int("BENCHMARK_MAX_TOKENS", 1000000)
        self.max_seconds = positive_int("BENCHMARK_MAX_SECONDS", 3600)
        self.max_question_seconds = positive_int("BENCHMARK_QUESTION_TIMEOUT_SECONDS", 900)
        self.max_cost = float(os.getenv("BENCHMARK_MAX_COST_USD", "0"))
        self.max_question_tokens = positive_int("BENCHMARK_MAX_QUESTION_TOKENS", 100000)
        self.max_preparation_calls = positive_int("BENCHMARK_MAX_PREPARATION_CALLS", 100)
        self.max_question_cost = float(os.getenv("BENCHMARK_MAX_QUESTION_COST_USD", "0"))
        self.max_daily_cost = float(os.getenv("BENCHMARK_MAX_DAILY_COST_USD", "0"))
        if any(
            not math.isfinite(value) or value < 0
            for value in (self.max_cost, self.max_question_cost, self.max_daily_cost)
        ):
            raise ValueError("Cost limits must be finite and nonnegative")
        self.prior_daily_cost, self.prior_unknown = (
            historical_usage(budget_root) if budget_root else (0.0, 0)
        )

    def set_stage(self, question_id, stage, metric=None):
        if question_id != self.question_id:
            self.question_calls = self.question_tokens = 0
            self.question_cost = 0.0
            self.question_started = time.monotonic()
        self.question_id, self.stage, self.metric = question_id, stage, metric

    def admit(self, model, endpoint):
        with self.lock:
            elapsed = time.monotonic() - self.started
            if (
                self.calls >= self.max_calls
                or self.question_tokens >= self.max_question_tokens
                or (
                    self.stage == "preparation"
                    and self.preparation_calls >= self.max_preparation_calls
                )
                or (
                    self.max_question_cost
                    and (self.unknown or self.question_cost >= self.max_question_cost)
                )
                or (
                    self.max_daily_cost
                    and (
                        self.prior_unknown
                        or self.unknown
                        or self.prior_daily_cost + self.cost >= self.max_daily_cost
                    )
                )
                or self.question_calls >= self.max_question_calls
                or self.tokens >= self.max_tokens
                or elapsed >= self.max_seconds
                or time.monotonic() - self.question_started >= self.max_question_seconds
                or (self.max_cost and (self.unknown or self.cost >= self.max_cost))
            ):
                raise BudgetExceeded("External call budget exhausted or cost unavailable")
            if os.getenv("BENCHMARK_MODE") == "evaluate" and self.stage != "judge":
                raise BudgetExceeded("Evaluation-only mode forbids generation and preparation")
            self.calls += 1
            if self.stage == "preparation":
                self.preparation_calls += 1
            self.question_calls += 1
            call = {
                "call_id": uuid.uuid4().hex,
                "run_id": self.run_id,
                "at": now(),
                "question_id": self.question_id,
                "stage": self.stage,
                "metric": self.metric,
                "model": model,
                "endpoint": endpoint,
                "kind": "started",
            }
            append_event(self.path, call)
            return call

    def finish(self, call, *, elapsed, body=None, status=None, error=None):
        body = body if isinstance(body, dict) else {}
        usage = body.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        tokens = nonnegative_number(usage.get("total_tokens"))
        cost = nonnegative_number(usage.get("cost"))
        try:
            json.dumps(usage, allow_nan=False)
        except ValueError:
            usage = {}

        with self.lock:
            self.tokens += tokens or 0
            self.question_tokens += tokens or 0
            if cost is None:
                self.unknown += 1
            else:
                self.cost += float(cost)
                self.question_cost += float(cost)
            append_event(
                self.path,
                {
                    **call,
                    "kind": "finished",
                    "at": now(),
                    "seconds": elapsed,
                    "http_status": status,
                    "provider_id": body.get("id"),
                    "provider": body.get("provider"),
                    "actual_model": body.get("model"),
                    "usage": usage,
                    "cost_usd": cost,
                    "consumption_unknown": cost is None,
                    "finish_reasons": [
                        choice.get("finish_reason") for choice in body.get("choices", [])
                    ],
                    "error": sanitize(error) if error else None,
                },
            )

    def summary(self):
        return {
            "run_id": self.run_id,
            "calls": self.calls,
            "known_tokens": self.tokens,
            "known_cost_usd": self.cost,
            "unknown_cost_calls": self.unknown,
            "prior_daily_cost_usd": self.prior_daily_cost,
            "prior_unknown_calls": self.prior_unknown,
            "cost_usd": None if self.unknown else self.cost,
        }


def retry_delay(response, attempt):
    value = response.headers.get("retry-after")
    if value:
        try:
            return max(0, float(value))
        except ValueError:
            try:
                return max(0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError):
                pass
    import random

    return min(2**attempt, 30) + random.random()


def http_clients(timeout):
    if ACTIVE_LEDGER is None:
        return {}
    import asyncio

    import httpx

    attempts = positive_int("BENCHMARK_HTTP_ATTEMPTS", 3)
    max_wait = positive_int("BENCHMARK_RETRY_MAX_WAIT_SECONDS", 60)

    def begin(request):
        if ACTIVE_LEDGER is None:
            raise RuntimeError("Paid clients require an active benchmark usage ledger")
        payload = json.loads(request.content)
        return ACTIVE_LEDGER, ACTIVE_LEDGER.admit(payload.get("model"), request.url.path)

    def finish(ledger, call, response, started):
        try:
            body = response.json()
        except ValueError:
            body = {}
        ledger.finish(
            call,
            elapsed=time.monotonic() - started,
            body=body,
            status=response.status_code,
            error=body.get("error") if isinstance(body, dict) else None,
        )

        if response.is_success and isinstance(body, dict) and isinstance(body.get("error"), dict):
            error = body["error"]
            raise ProviderResponseError(error.get("code"), error.get("message", "Provider failed"))

    class Transport(httpx.BaseTransport):
        def __init__(self):
            self.transport = httpx.HTTPTransport(retries=0)

        def handle_request(self, request):
            for attempt in range(attempts):
                ledger, call = begin(request)
                started = time.monotonic()
                try:
                    response = self.transport.handle_request(request)
                    response.read()
                except httpx.TransportError as exc:
                    ledger.finish(call, elapsed=time.monotonic() - started, error=str(exc))
                    raise
                finish(ledger, call, response, started)
                if response.status_code not in {429, 503} or attempt + 1 == attempts:
                    return response
                delay = retry_delay(response, attempt)
                if delay > max_wait:
                    return response
                response.close()
                sleep(delay)

        def close(self):
            self.transport.close()

    class AsyncTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.transport = httpx.AsyncHTTPTransport(
                retries=0, limits=httpx.Limits(max_keepalive_connections=0)
            )

        async def handle_async_request(self, request):
            for attempt in range(attempts):
                ledger, call = begin(request)
                started = time.monotonic()
                try:
                    response = await self.transport.handle_async_request(request)
                    await response.aread()
                except httpx.TransportError as exc:
                    ledger.finish(call, elapsed=time.monotonic() - started, error=str(exc))
                    raise
                finish(ledger, call, response, started)
                if response.status_code not in {429, 503} or attempt + 1 == attempts:
                    return response
                delay = retry_delay(response, attempt)
                if delay > max_wait:
                    return response
                await response.aclose()
                await asyncio.sleep(delay)

        async def aclose(self):
            await self.transport.aclose()

    return {
        "http_client": httpx.Client(transport=Transport(), timeout=timeout),
        "http_async_client": httpx.AsyncClient(transport=AsyncTransport(), timeout=timeout),
    }
