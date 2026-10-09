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
from pathlib import Path
from time import sleep

from app.benchmark.config import positive_int
from app.benchmark.storage import append_event, atomic_json, now, sanitize

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


def usage_records(root):
    root = Path(root)
    records = {}
    paths = sorted(set(root.glob("*/*/usage.jsonl")) | set(root.glob("budget.jsonl")))
    for path in paths:
        for index, line in enumerate(path.read_text().splitlines()):
            try:
                event = json.loads(line)
                key = event["call_id"]
                previous = records.get(key, {})
                if event.get("kind") == "started" and previous:
                    continue
                if previous.get("kind") == "reconciled" and event.get("kind") != "reconciled":
                    continue
                records[key] = {**previous, **event}
            except (ValueError, KeyError, TypeError):
                records[f"corrupt:{path}:{index}"] = {"kind": "corrupt"}
    return records


def historical_usage(root, *, day=None, exclude_run=None, all_time=False):
    day = day or datetime.now(UTC).date().isoformat()
    total = 0.0
    unknown = 0
    for event in usage_records(root).values():
        if exclude_run and event.get("run_id") == exclude_run:
            continue
        cost = nonnegative_number(event.get("cost_usd"))
        if event.get("kind") not in {"finished", "reconciled"} or cost is None:
            unknown += 1
        elif all_time or event.get("started_at", event.get("at", "")).startswith(day):
            total += cost
    return total, unknown


class BudgetExceeded(RuntimeError):
    def __init__(self, message, *, stage=None, limit=None):
        super().__init__(message)
        self.stage, self.limit = stage, limit


class ReservationPending(BudgetExceeded):
    """This call fits after in-flight token reservations have been released."""


def budget_cause(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, BudgetExceeded):
            return exc
        exc = exc.__cause__ or exc.__context__
    return None


class ProviderResponseError(RuntimeError):
    def __init__(self, status_code, message):
        super().__init__(sanitize(message))
        self.status_code = status_code


class UsageLedger:
    def __init__(self, path, budget_root=None):
        self.path = Path(path)
        self.budget_root = Path(budget_root) if budget_root else None
        self.reservations = {}
        self.pause_event = None
        self.preparation_cost = 0.0
        self.day_costs = {}
        self.run_id = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.calls = self.question_calls = 0
        self.tokens = self.question_tokens = self.preparation_calls = 0
        self.preparation_tokens = 0
        self.cost = self.question_cost = 0.0
        self.unknown = self.question_unknown = 0
        self.question_id = self.metric = None
        self.stage = "preparation"
        self.started = self.question_started = time.monotonic()
        self.max_calls = positive_int("BENCHMARK_MAX_CALLS", 1000)
        self.max_question_calls = positive_int("BENCHMARK_MAX_QUESTION_CALLS", 60)
        self.max_tokens = positive_int("BENCHMARK_MAX_TOKENS", 5000000)
        self.max_seconds = positive_int("BENCHMARK_MAX_SECONDS", 3600)
        self.max_question_seconds = positive_int("BENCHMARK_QUESTION_TIMEOUT_SECONDS", 900)
        self.max_cost = float(os.getenv("BENCHMARK_MAX_COST_USD", "0"))
        self.max_question_tokens = positive_int("BENCHMARK_MAX_QUESTION_TOKENS", 100000)
        self.max_preparation_calls = positive_int("BENCHMARK_MAX_PREPARATION_CALLS", 256)
        self.max_question_cost = float(os.getenv("BENCHMARK_MAX_QUESTION_COST_USD", "0"))
        self.max_daily_cost = float(os.getenv("BENCHMARK_MAX_DAILY_COST_USD", "0"))
        self.max_preparation_cost = float(os.getenv("BENCHMARK_MAX_PREPARATION_COST_USD", "0"))
        self.max_period_cost = float(os.getenv("BENCHMARK_MAX_PERIOD_COST_USD", "0"))
        self.reserve_cost = float(os.getenv("BENCHMARK_RESERVE_COST_USD", "0"))
        if any(
            not math.isfinite(value) or value < 0
            for value in (
                self.max_cost,
                self.max_question_cost,
                self.max_daily_cost,
                self.max_preparation_cost,
                self.reserve_cost,
                self.max_period_cost,
            )
        ):
            raise ValueError("Cost limits must be finite and nonnegative")
        self.prior_daily_cost, self.prior_unknown = (
            historical_usage(budget_root) if budget_root else (0.0, 0)
        )

    def set_stage(self, question_id, stage, metric=None):
        if question_id != self.question_id:
            self.question_calls = self.question_tokens = 0
            self.question_cost = 0.0
            self.question_unknown = 0
            self.question_started = time.monotonic()
        elif self.stage == "preparation" and stage != "preparation":
            self.question_started = time.monotonic()
        self.question_id, self.stage, self.metric = question_id, stage, metric
        self.progress()

    def progress(self):
        atomic_json(
            self.path.parent / "progress.json",
            {
                "run_id": self.run_id,
                "current_question": self.question_id,
                "current_stage": self.metric or self.stage,
                "usage": self.summary(),
                "elapsed_seconds": time.monotonic() - self.started,
                "progress_at": now(),
                "deadline_monotonic": time.monotonic() + self.remaining_seconds(),
            },
            mode=0o640,
        )

    def remaining_seconds(self):
        remaining = self.max_seconds - (time.monotonic() - self.started)
        if self.stage != "preparation":
            remaining = min(
                remaining, self.max_question_seconds - (time.monotonic() - self.question_started)
            )
        return max(0.0, remaining)

    def check_limit(self, name, maximum, used, reserved=0, requested=0):
        if used >= maximum or used + reserved + requested > maximum:
            error = (
                ReservationPending
                if reserved and used < maximum and used + requested <= maximum
                else BudgetExceeded
            )
            raise error(
                f"Limite {name} atingido na etapa {self.stage}: "
                f"usado={used:g}, reservado={reserved:g}, próxima chamada={requested:g}, "
                f"limite={maximum:g}. O progresso foi preservado.",
                stage=self.stage,
                limit=name,
            )

    def append(self, event):
        append_event(self.path, event)
        if self.budget_root:
            append_event(self.budget_root / "budget.jsonl", event)

    def admit(self, model, endpoint, payload=None, role=None):
        with self.lock:
            question_scope = self.stage != "preparation"
            if (self.path.parent / "pause.request").exists() or (
                self.pause_event is not None and self.pause_event.is_set()
            ):
                raise BudgetExceeded("Pause requested; no further external calls admitted")
            if self.budget_root:
                self.prior_daily_cost, self.prior_unknown = historical_usage(
                    self.budget_root, exclude_run=self.run_id
                )
            period_cost, period_unknown = (
                historical_usage(self.budget_root, all_time=True, exclude_run=self.run_id)
                if self.budget_root
                else (0.0, 0)
            )
            if self.max_period_cost and (
                period_unknown
                or self.unknown
                or period_cost
                + self.cost
                + sum(value[0] for value in self.reservations.values())
                + self.reserve_cost
                > self.max_period_cost
            ):
                raise BudgetExceeded("Shared period budget exhausted or unresolved")
            if self.unknown and any(
                (
                    self.max_cost,
                    self.max_daily_cost,
                    self.max_preparation_cost,
                    self.max_period_cost,
                )
            ):
                raise BudgetExceeded("Unresolved cost blocks monetary admission")
            if question_scope and self.max_question_cost and self.question_unknown:
                raise BudgetExceeded("Unresolved question cost blocks monetary admission")
            reserve = self.reserve_cost
            reserved = sum(r[0] for r in self.reservations.values())
            token_estimate = 0
            if payload is not None:
                inputs = {k: v for k, v in payload.items() if k in {"messages", "input", "tools"}}
                token_estimate = len(json.dumps(inputs, ensure_ascii=False).encode()) + int(
                    payload.get("max_tokens")
                    or payload.get("max_completion_tokens")
                    or payload.get("max_output_tokens")
                    or 0
                )
                if (
                    any(
                        (
                            self.max_cost,
                            self.max_question_cost if question_scope else 0,
                            self.max_daily_cost,
                            self.max_preparation_cost,
                            self.max_period_cost,
                        )
                    )
                    and not reserve
                ):
                    raise BudgetExceeded(
                        "Configure BENCHMARK_RESERVE_COST_USD before monetary-limited calls"
                    )
            reserved_tokens = sum(r[1] for r in self.reservations.values())
            question_reserved = [
                r
                for r in self.reservations.values()
                if r[2] == self.question_id and r[2] is not None
            ]
            self.check_limit(
                "BENCHMARK_MAX_TOKENS",
                self.max_tokens,
                self.tokens,
                reserved_tokens,
                token_estimate,
            )
            self.check_limit("BENCHMARK_MAX_CALLS", self.max_calls, self.calls, requested=1)
            self.check_limit(
                "BENCHMARK_MAX_SECONDS", self.max_seconds, time.monotonic() - self.started
            )
            if question_scope:
                self.check_limit(
                    "BENCHMARK_MAX_QUESTION_TOKENS",
                    self.max_question_tokens,
                    self.question_tokens,
                    sum(r[1] for r in question_reserved),
                    token_estimate,
                )
                self.check_limit(
                    "BENCHMARK_MAX_QUESTION_CALLS",
                    self.max_question_calls,
                    self.question_calls,
                    requested=1,
                )
                self.check_limit(
                    "BENCHMARK_QUESTION_TIMEOUT_SECONDS",
                    self.max_question_seconds,
                    time.monotonic() - self.question_started,
                )
            else:
                self.check_limit(
                    "BENCHMARK_MAX_PREPARATION_CALLS",
                    self.max_preparation_calls,
                    self.preparation_calls,
                    requested=1,
                )
            today = datetime.now(UTC).date().isoformat()
            if (
                (self.max_cost and self.cost + reserved + reserve > self.max_cost)
                or (
                    question_scope
                    and self.max_question_cost
                    and self.question_cost + sum(r[0] for r in question_reserved) + reserve
                    > self.max_question_cost
                )
                or (
                    self.max_daily_cost
                    and self.prior_daily_cost + self.day_costs.get(today, 0) + reserved + reserve
                    > self.max_daily_cost
                )
                or (
                    self.stage == "preparation"
                    and self.max_preparation_cost
                    and self.preparation_cost + reserved + reserve > self.max_preparation_cost
                )
            ):
                raise BudgetExceeded("Insufficient budget for estimated call reservation")
            if (
                (
                    question_scope
                    and self.max_question_cost
                    and (self.question_unknown or self.question_cost >= self.max_question_cost)
                )
                or (
                    self.max_daily_cost
                    and (
                        self.prior_unknown
                        or self.unknown
                        or self.prior_daily_cost + self.day_costs.get(today, 0)
                        >= self.max_daily_cost
                    )
                )
                or (self.max_cost and (self.unknown or self.cost >= self.max_cost))
            ):
                raise BudgetExceeded("External call budget exhausted or cost unavailable")
            if (
                os.getenv("BENCHMARK_MODE") == "evaluate"
                or os.getenv("BENCHMARK_CREDIT_SCOPE") == "judge"
            ) and self.stage != "judge":
                raise BudgetExceeded("Evaluation-only mode forbids generation and preparation")
            self.calls += 1
            if self.stage == "preparation":
                self.preparation_calls += 1
            else:
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
                "role": role or self.stage,
                "started_at": now(),
                "reserved_cost_usd": reserve,
                "estimated_max_tokens": token_estimate,
            }
            self.reservations[call["call_id"]] = (
                reserve,
                token_estimate,
                self.question_id if question_scope else None,
            )
            self.append(call)
            self.progress()
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
            self.reservations.pop(call["call_id"], None)
            self.tokens += tokens or 0
            question_call = (
                call["stage"] != "preparation" and call["question_id"] == self.question_id
            )
            if question_call:
                self.question_tokens += tokens or 0
            if call["stage"] == "preparation":
                self.preparation_tokens += tokens or 0
            if cost is None:
                self.unknown += 1
                if question_call:
                    self.question_unknown += 1
            else:
                self.cost += float(cost)
                if question_call:
                    self.question_cost += float(cost)
                day = call.get("started_at", call["at"])[:10]
                self.day_costs[day] = self.day_costs.get(day, 0) + float(cost)
                if call["stage"] == "preparation":
                    self.preparation_cost += float(cost)
            self.append(
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

            self.progress()

    def summary(self):
        return {
            "remaining_seconds": self.remaining_seconds(),
            "preparation_cost_usd": self.preparation_cost,
            "preparation_tokens": self.preparation_tokens,
            "preparation_calls": self.preparation_calls,
            "question_tokens": self.question_tokens,
            "question_calls": self.question_calls,
            "reserved_cost_usd": sum(v[0] for v in self.reservations.values()),
            "remaining_cost_usd": max(
                0, self.max_cost - self.cost - sum(v[0] for v in self.reservations.values())
            )
            if self.max_cost and not self.unknown
            else None,
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


def http_clients(timeout, *, role=None):
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
        ledger = ACTIVE_LEDGER
        call = ledger.admit(payload.get("model"), request.url.path, payload, role)
        remaining = min(timeout, ledger.remaining_seconds())
        request.extensions["timeout"] = {
            name: remaining for name in ("connect", "read", "write", "pool")
        }
        return ledger, call

    def capacity_delay(exc):
        remaining = ACTIVE_LEDGER.remaining_seconds()
        if remaining <= 0:
            raise BudgetExceeded(
                f"Tempo esgotado aguardando liberação de {exc.limit}.",
                stage=ACTIVE_LEDGER.stage,
                limit=exc.limit,
            ) from exc
        return min(0.1, remaining)

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
                while True:
                    try:
                        ledger, call = begin(request)
                        break
                    except ReservationPending as exc:
                        sleep(capacity_delay(exc))
                started = time.monotonic()
                try:
                    response = self.transport.handle_request(request)
                    response.read()
                except (httpx.TransportError, TimeoutError) as exc:
                    ledger.finish(call, elapsed=time.monotonic() - started, error=str(exc))
                    raise
                finish(ledger, call, response, started)
                if response.status_code not in {429, 503} or attempt + 1 == attempts:
                    return response
                delay = retry_delay(response, attempt)
                if delay > max_wait or delay >= ledger.remaining_seconds():
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
                while True:
                    try:
                        ledger, call = begin(request)
                        break
                    except ReservationPending as exc:
                        await asyncio.sleep(capacity_delay(exc))
                started = time.monotonic()
                try:
                    async with asyncio.timeout(ledger.remaining_seconds()):
                        response = await self.transport.handle_async_request(request)
                        await response.aread()
                except (httpx.TransportError, TimeoutError) as exc:
                    ledger.finish(call, elapsed=time.monotonic() - started, error=str(exc))
                    raise
                finish(ledger, call, response, started)
                if response.status_code not in {429, 503} or attempt + 1 == attempts:
                    return response
                delay = retry_delay(response, attempt)
                if delay > max_wait or delay >= ledger.remaining_seconds():
                    return response
                await response.aclose()
                await asyncio.sleep(delay)

        async def aclose(self):
            await self.transport.aclose()

    return {
        "http_client": httpx.Client(transport=Transport(), timeout=timeout),
        "http_async_client": httpx.AsyncClient(transport=AsyncTransport(), timeout=timeout),
    }
