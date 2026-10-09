from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.benchmark.control import Control, Worker
from app.benchmark.runner import run_resumable_benchmark
from app.benchmark.storage import atomic_json, sanitize
from app.benchmark.usage import BudgetExceeded, UsageLedger
from app.telegram.notifier import format_event


class UsageScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_real_preparation_pattern_does_not_exhaust_question_limits(self):
        ledger = UsageLedger(self.root / "usage.jsonl")
        ledger.set_stage("Q084", "preparation")
        # Reproduce the five successful batches and next request seen on the VPS.
        for tokens in [12206, 12335, 12922, 13582, 13620]:
            call = ledger.admit("embedding", "/embeddings", {"input": "x" * 46000})
            ledger.finish(call, elapsed=0, body={"usage": {"total_tokens": tokens, "cost": 0.001}})
        ledger.admit("embedding", "/embeddings", {"input": "x" * 46000})
        self.assertEqual(ledger.tokens, 64665)
        self.assertEqual(ledger.preparation_tokens, 64665)
        self.assertEqual(
            (ledger.question_tokens, ledger.question_calls, ledger.question_cost), (0, 0, 0)
        )
        ledger.set_stage("Q084", "generation")
        call = ledger.admit("model", "/chat", {"input": "question"})
        ledger.finish(call, elapsed=0, body={"usage": {"total_tokens": 60000, "cost": 0.1}})
        ledger.set_stage("Q084", "judge", "faithfulness")
        with self.assertRaisesRegex(BudgetExceeded, "BENCHMARK_MAX_QUESTION_TOKENS"):
            ledger.admit("judge", "/chat", {"input": "x" * 46000})
        ledger.set_stage("Q085", "generation")
        ledger.admit("model", "/chat", {"input": "x" * 46000})

    def test_global_and_preparation_limits_still_apply(self):
        for setting, value, pattern in [
            ("BENCHMARK_MAX_PREPARATION_CALLS", "1", "BENCHMARK_MAX_PREPARATION_CALLS"),
            ("BENCHMARK_MAX_CALLS", "1", "BENCHMARK_MAX_CALLS"),
            ("BENCHMARK_MAX_TOKENS", "60000", "BENCHMARK_MAX_TOKENS"),
        ]:
            with self.subTest(setting=setting), patch.dict(os.environ, {setting: value}):
                ledger = UsageLedger(self.root / (setting + ".jsonl"))
                call = ledger.admit("embedding", "/embeddings", {"input": "x" * 46000})
                ledger.finish(call, elapsed=0, body={"usage": {"total_tokens": 20000, "cost": 0}})
                with self.assertRaisesRegex(BudgetExceeded, pattern):
                    ledger.admit("embedding", "/embeddings", {"input": "x" * 46000})

    def test_numeric_token_limits_are_visible_but_credentials_are_redacted(self):
        with patch.dict(
            os.environ,
            {"BENCHMARK_MAX_QUESTION_TOKENS": "100000", "OPENROUTER_API_KEY": "private-test-key"},
        ):
            self.assertEqual(sanitize("limite=100000 private-test-key"), "limite=100000 [REDACTED]")

    def test_question_timer_starts_after_preparation_and_money_stays_global(self):
        with (
            patch.dict(os.environ, {"BENCHMARK_MAX_QUESTION_COST_USD": "1"}),
            patch("app.benchmark.usage.time.monotonic", return_value=100) as clock,
        ):
            ledger = UsageLedger(self.root / "timer.jsonl")
            ledger.set_stage("Q084", "preparation")
            clock.return_value = 1100
            self.assertEqual(ledger.remaining_seconds(), 2600)
            call = ledger.admit("embedding", "/embeddings", {"input": "corpus"})
            ledger.finish(call, elapsed=0, body={"usage": {"total_tokens": 10, "cost": 2}})
            ledger.set_stage("Q084", "generation")
            self.assertEqual(ledger.remaining_seconds(), 900)
            self.assertEqual(ledger.question_cost, 0)
            ledger.max_cost = 1
            with self.assertRaises(BudgetExceeded):
                ledger.admit("model", "/chat")

    def test_wrapped_limit_reports_real_stage_and_reaches_public_events(self):
        with patch.dict(os.environ, {"BENCHMARK_MAX_PREPARATION_CALLS": "1"}):
            ledger = UsageLedger(self.root / "output/usage.jsonl")
        dataset = self.root / "dataset.json"
        atomic_json(dataset, [{"id": "Q084", "question": "Question", "ground_truth": "Reference"}])

        def answer(question):
            ledger.set_stage(question["id"], "preparation")
            ledger.admit("embedding", "/embeddings")
            try:
                ledger.admit("embedding", "/embeddings")
            except BudgetExceeded as exc:
                raise RuntimeError("Connection error.") from exc

        with contextlib.redirect_stdout(io.StringIO()):
            result = run_resumable_benchmark(
                "context-rag",
                answer,
                dataset_path=dataset,
                output_dir=self.root / "output",
                ledger=ledger,
                evaluate_question=Mock(side_effect=AssertionError("Evaluation must not run")),
            )
        self.assertEqual(result["failed"], 1)
        state = json.loads((self.root / "output/checkpoint.json").read_text())["items"]["Q084"]
        self.assertEqual(state["failed_stage"], "preparation")
        self.assertEqual(state["error_type"], "BudgetExceeded")
        self.assertIn("BENCHMARK_MAX_PREPARATION_CALLS", state["error_message"])
        events = [
            json.loads(line)
            for line in (self.root / "output/public_events.jsonl").read_text().splitlines()
        ]
        failed = next(row for row in events if row["kind"] == "failed")
        self.assertIn("BENCHMARK_MAX_PREPARATION_CALLS", format_event(failed))

    def test_controller_uses_this_runs_failure_instead_of_generic_exit_code(self):
        control = Control(
            self.root / "results",
            self.root / "queue.db",
            {123},
            enabled=True,
            profiles={"context-rag": {"mode": "full"}},
        )
        self.addCleanup(control.db.close)
        control.handle(
            {"user_id": 123, "command_id": "failure", "text": "/executar context-rag --questions 1"}
        )
        directory = self.root / "results/context-rag" / ("a" * 64)
        atomic_json(directory / "checkpoint.json", {})
        assignment = {"project": "context-rag", "experiment_id": "a" * 64, "run_id": "current"}
        atomic_json(self.root / "failure.json", assignment)
        atomic_json(
            directory / "summary.json",
            {
                "run_id": "current",
                "alert": {
                    "question_id": "Q084",
                    "stage": "preparation",
                    "error": "BENCHMARK_MAX_TOKENS atingido",
                },
            },
        )
        worker = Worker(control)
        worker.job, worker.started = "failure", time.monotonic()
        worker.process = Mock(poll=Mock(return_value=1))
        worker.log = (self.root / "failure.log").open("w")
        worker.tick()
        row = control.db.execute("SELECT error FROM jobs WHERE id='failure'").fetchone()
        self.assertIn("Q084 · preparation: BENCHMARK_MAX_TOKENS", row[0])
        event = json.loads(
            control.db.execute(
                "SELECT payload FROM job_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()[0]
        )
        self.assertIn("BENCHMARK_MAX_TOKENS", format_event(event))


if __name__ == "__main__":
    unittest.main()
