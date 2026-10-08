from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.benchmark.control import Control, Worker, call_control, environment_profiles, parse_command
from app.benchmark.knowledge import read_snapshot
from app.benchmark.reconcile import reconcile
from app.benchmark.storage import append_event, atomic_json
from app.benchmark.usage import BudgetExceeded, UsageLedger, historical_usage
from app.services.entrypoint import role_environment, service_command
from app.telegram.gateway import Gateway
from app.telegram.notifier import Notifier


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def control(self, **kwargs):
        kwargs.setdefault("max_budget", 2)
        control = Control(
            self.root / "results",
            self.root / "queue.db",
            {123},
            enabled=True,
            profiles={"context-rag": {"mode": "full"}},
            **kwargs,
        )
        self.addCleanup(control.db.close)
        return control

    def test_command_authorization_and_injection_never_queue_work(self):
        control = self.control()
        for user in (456, True, "123"):
            with self.assertRaises(PermissionError):
                control.handle(
                    {"user_id": user, "command_id": "1", "text": "/executar context-rag 1 1"}
                )
        for text in (
            "rm -rf /",
            "/executar context-rag 1 nan",
            "/executar context-rag 1 -1",
            "/executar context-rag 1 1;id",
            "/status ../etc " + "a" * 64,
        ):
            with self.assertRaises(ValueError):
                parse_command(text)
        self.assertEqual(control.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    def test_job_is_idempotent_and_restart_never_replays_paid_work(self):
        control = self.control()
        request = {
            "user_id": 123,
            "command_id": "telegram:1",
            "text": "/executar context-rag 1 0.5",
        }
        first = control.handle(request)
        self.assertEqual(control.handle(request), first)
        self.assertEqual(control.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)
        with self.assertRaises(ValueError):
            control.handle({**request, "text": "/executar context-rag 2 0.5"})
        with self.assertRaises(ValueError):
            control.handle({**request, "command_id": "telegram:2"})
        restarted = self.control()
        self.assertEqual(
            restarted.db.execute("SELECT state FROM jobs").fetchone()[0], "interrupted"
        )
        restarted.handle(request)
        self.assertEqual(restarted.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)

    def test_remote_budget_and_disabled_state_block_queue(self):
        control = self.control()
        request = {"user_id": 123, "command_id": "1", "text": "/executar context-rag 1 2.01"}
        with self.assertRaises(ValueError):
            control.handle(request)
        control.enabled = False
        with self.assertRaises(ValueError):
            control.handle({**request, "text": "/executar context-rag 1 1"})
        self.assertEqual(control.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    def test_read_pause_export_and_inspection_never_launch(self):
        control = self.control()
        experiment = "a" * 64
        directory = control.root / "context-rag" / experiment
        atomic_json(
            directory / "checkpoint.json",
            {"items": {"Q001": {"status": "failed", "artifact": {"answer": "private text"}}}},
        )
        atomic_json(directory / "summary.json", {"operation": "paused"})
        atomic_json(directory / "public_results.json", {"rows": []})
        with patch("app.benchmark.control.subprocess.Popen") as popen:
            for i, command in enumerate(("status", "pausar", "falhas", "resultado", "pergunta")):
                text = f"/{command} context-rag {experiment}" + (
                    " Q001" if command == "pergunta" else ""
                )
                result = control.handle({"user_id": 123, "command_id": str(i), "text": text})
                self.assertNotIn("private text", json.dumps(result))
            popen.assert_not_called()
        self.assertTrue((directory / "pause.request").exists())

    def test_gateway_filters_chat_scope_and_persists_offset(self):
        control = self.control()
        client = Mock()
        updates = [
            {
                "update_id": 1,
                "message": {
                    "from": {"id": 999},
                    "chat": {"id": 999, "type": "private"},
                    "text": "/executar context-rag 1 1",
                },
            },
            {
                "update_id": 2,
                "message": {
                    "from": {"id": 123},
                    "chat": {"id": 123, "type": "group"},
                    "text": "/executar context-rag 1 1",
                },
            },
            {
                "update_id": 3,
                "message": {
                    "from": {"id": 123},
                    "chat": {"id": 123, "type": "private"},
                    "text": "/executar context-rag 1 1",
                },
            },
        ]
        client.call.return_value = updates
        notifier = Notifier(control.root, self.root / "outbox.db", client, "-100")
        self.addCleanup(notifier.db.close)
        gateway = Gateway(notifier, "unused.sock", {123})
        with patch(
            "app.telegram.gateway.call_control", side_effect=lambda path, req: control.handle(req)
        ) as dispatch:
            gateway.poll()
            gateway.poll()
            self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(control.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)
        self.assertEqual(notifier.db.execute("SELECT offset FROM telegram_cursor").fetchone()[0], 4)

    def test_gateway_lost_ack_retries_same_command_without_duplicate_job(self):
        control = self.control()
        client = Mock()
        client.call.return_value = [
            {
                "update_id": 9,
                "message": {
                    "from": {"id": 123},
                    "chat": {"id": 123, "type": "private"},
                    "text": "/executar context-rag 1 1",
                },
            }
        ]
        notifier = Notifier(control.root, self.root / "outbox.db", client, "-100")
        self.addCleanup(notifier.db.close)
        gateway = Gateway(notifier, "unused.sock", {123})

        def dropped(path, request):
            control.handle(request)
            raise OSError("Lost response")

        with (
            patch("app.telegram.gateway.call_control", side_effect=dropped),
            self.assertRaises(OSError),
        ):
            gateway.poll()
        self.assertEqual(notifier.db.execute("SELECT offset FROM telegram_cursor").fetchone()[0], 0)
        with patch(
            "app.telegram.gateway.call_control", side_effect=lambda path, req: control.handle(req)
        ):
            gateway.poll()
        self.assertEqual(control.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)

    def test_inflight_reservations_prevent_oversubscription(self):
        with patch.dict(
            os.environ, {"BENCHMARK_MAX_COST_USD": "1", "BENCHMARK_RESERVE_COST_USD": "0.6"}
        ):
            ledger = UsageLedger(self.root / "usage.jsonl")
            call = ledger.admit("model", "/chat", {"input": "hello"})
            with self.assertRaises(BudgetExceeded):
                ledger.admit("model", "/chat", {"input": "hello"})
            ledger.finish(call, elapsed=0, body={"usage": {"cost": 0.1}})
            ledger.admit("model", "/chat", {"input": "hello"})

    def test_monetary_http_admission_requires_estimate(self):
        with patch.dict(os.environ, {"BENCHMARK_MAX_PERIOD_COST_USD": "2"}):
            ledger = UsageLedger(self.root / "usage.jsonl")
            with self.assertRaises(BudgetExceeded):
                ledger.admit("model", "/chat", {"input": "hello"})

    def test_shared_period_budget_survives_different_output_roots(self):
        budget = self.root / "budget"
        with patch.dict(
            os.environ, {"BENCHMARK_MAX_PERIOD_COST_USD": "1", "BENCHMARK_RESERVE_COST_USD": "0.6"}
        ):
            first = UsageLedger(self.root / "one/usage.jsonl", budget_root=budget)
            first.finish(
                first.admit("model", "/chat", {}), elapsed=0, body={"usage": {"cost": 0.6}}
            )
            second = UsageLedger(self.root / "two/usage.jsonl", budget_root=budget)
            with self.assertRaises(BudgetExceeded):
                second.admit("model", "/chat", {})
            self.assertEqual(historical_usage(budget)[0], 0.6)

    def test_midnight_and_old_ambiguous_calls_are_not_lost(self):
        path = self.root / "budget.jsonl"
        append_event(
            path,
            {
                "call_id": "one",
                "kind": "started",
                "at": "2026-10-06T23:59:59Z",
                "started_at": "2026-10-06T23:59:59Z",
            },
        )
        self.assertEqual(historical_usage(self.root, day="2026-10-07"), (0, 1))
        append_event(
            path,
            {"call_id": "one", "kind": "finished", "at": "2026-10-07T00:00:10Z", "cost_usd": 0.2},
        )
        self.assertEqual(historical_usage(self.root, day="2026-10-06"), (0.2, 0))
        self.assertEqual(historical_usage(self.root, day="2026-10-07"), (0, 0))

    def test_reconciliation_is_append_only_and_idempotent(self):
        append_event(
            self.root / "budget.jsonl",
            {
                "call_id": "one",
                "kind": "finished",
                "at": "2026-10-07",
                "provider_id": "gen-one",
                "cost_usd": None,
                "role": "judge",
            },
        )
        fetch = Mock(return_value={"id": "gen-one", "total_cost": 0.3})
        self.assertEqual(reconcile(self.root, fetch=fetch)["calls"][0]["status"], "eligible")
        fetch.assert_not_called()
        reconcile(self.root, apply=True, fetch=fetch)
        reconcile(self.root, apply=True, fetch=fetch)
        fetch.assert_called_once_with("gen-one", "judge")
        self.assertEqual(historical_usage(self.root, all_time=True), (0.3, 0))
        self.assertEqual(len((self.root / "budget.jsonl").read_text().splitlines()), 2)

    def test_snapshot_is_validated_and_independent_from_live_database(self):
        source = Path(__file__).resolve().parents[1] / "data/knowledge-graph.json"
        graph = read_snapshot(source)
        self.assertEqual(len(graph["nodes"]), 14)
        changed = json.loads(source.read_text())
        changed["graph"]["nodes"][0]["nome"] = "changed"
        atomic_json(self.root / "snapshot.json", changed)
        with self.assertRaises(ValueError):
            read_snapshot(self.root / "snapshot.json")

    def test_worker_refuses_monetary_limit_without_reservation(self):
        control = self.control()
        control.handle({"user_id": 123, "command_id": "one", "text": "/executar context-rag 1 1"})
        with patch("app.benchmark.control.subprocess.Popen") as popen:
            Worker(control).tick()
            popen.assert_not_called()
        self.assertEqual(control.db.execute("SELECT state FROM jobs").fetchone()[0], "failed")

    def test_remote_execution_without_dollar_limit_keeps_technical_limits(self):
        control = self.control(max_budget=0)
        result = control.handle(
            {"user_id": 123, "command_id": "uncapped", "text": "/executar context-rag 1"}
        )
        self.assertIsNone(result["budget_usd"])
        worker = Worker(control)
        with (
            patch.dict(os.environ, {"BENCHMARK_MAX_CALLS": "200", "TELEGRAM_BOT_TOKEN": "private"}),
            patch("app.benchmark.control.subprocess.Popen") as popen,
        ):
            worker.tick()
        self.addCleanup(worker.log.close)
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["BENCHMARK_MAX_COST_USD"], "0")
        self.assertEqual(env["BENCHMARK_QUESTION_LIMIT"], "1")
        self.assertEqual(env["BENCHMARK_MAX_CALLS"], "200")
        self.assertNotIn("TELEGRAM_BOT_TOKEN", env)
        self.assertEqual(control.db.execute("SELECT state FROM jobs").fetchone()[0], "running")
        resumed = parse_command("/retomar context-rag " + "a" * 64 + " 2")
        self.assertEqual(resumed["questions"], 2)
        self.assertIsNone(resumed["budget"])

    def test_optional_cost_limit_cannot_be_bypassed_by_omitting_usd(self):
        control = self.control(max_budget=3)
        result = control.handle(
            {"user_id": 123, "command_id": "capped", "text": "/executar context-rag 1"}
        )
        self.assertEqual(result["budget_usd"], 3)
        worker = Worker(control)
        with (
            patch.dict(
                os.environ, {"BENCHMARK_MAX_COST_USD": "1", "BENCHMARK_RESERVE_COST_USD": "0.1"}
            ),
            patch("app.benchmark.control.subprocess.Popen") as popen,
        ):
            worker.tick()
        self.addCleanup(worker.log.close)
        self.assertEqual(float(popen.call_args.kwargs["env"]["BENCHMARK_MAX_COST_USD"]), 1)

    def test_unified_configuration_separates_service_secrets(self):
        source = {
            "OPENROUTER_API_KEY": "model-secret",
            "NEO4J_PASSWORD": "database-secret",
            "LANGCHAIN_API_KEY": "trace-secret",
            "TELEGRAM_BOT_TOKEN": "bot-secret",
            "TELEGRAM_RESULTS_CHAT_ID": "-1001234567890",
            "TELEGRAM_ALLOWED_USER_IDS": "123",
            "BENCHMARK_KG_MODE": "required",
            "UNRELATED_SECRET": "unrelated",
            "PYTHONPATH": "/untrusted",
            "PATH": "/usr/bin",
            "UV_NO_SYNC": "1",
            "UV_LINK_MODE": "copy",
            "DASHBOARD_PASSWORD": "dashboard-secret",
        }
        telegram = role_environment("telegram", source)
        control = role_environment("control", source)
        worker = role_environment("worker", source)
        self.assertEqual(telegram["TELEGRAM_RESULTS_CHAT_ID"], "-1001234567890")
        for name in ("OPENROUTER_API_KEY", "NEO4J_PASSWORD", "LANGCHAIN_API_KEY"):
            self.assertNotIn(name, telegram)
            self.assertEqual(control[name], source[name])
        self.assertNotIn("TELEGRAM_BOT_TOKEN", control)
        self.assertEqual(control["TELEGRAM_ALLOWED_USER_IDS"], "123")
        self.assertNotIn("TELEGRAM_ALLOWED_USER_IDS", worker)
        for env in (telegram, control, worker):
            self.assertNotIn("UNRELATED_SECRET", env)
            self.assertNotIn("DASHBOARD_PASSWORD", env)
            self.assertEqual(env["UV_NO_SYNC"], "1")
            self.assertNotIn("PYTHONPATH", env)
            self.assertEqual(env["PYTHON_DOTENV_DISABLED"], "1")

    def test_service_launcher_executes_notifier_without_model_secrets(self):
        result = subprocess.run(
            [sys.executable, "-m", "app.services.entrypoint", "telegram", "--from-environment"],
            env={
                **os.environ,
                "TELEGRAM_ENABLED": "false",
                "OPENROUTER_API_KEY": "fake-model-secret",
                "NEO4J_PASSWORD": "fake-db-secret",
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_default_profiles_preserve_live_knowledge_graph(self):
        profiles = environment_profiles()
        self.assertEqual(len(profiles), 6)
        self.assertEqual(profiles["knowledge-enhanced-rag"], {"mode": "full"})
        command = service_command("control", {})
        self.assertNotIn("--profiles", command)
        with patch.dict(os.environ, {"BENCHMARK_MODE": "evaluate"}):
            with self.assertRaises(ValueError):
                environment_profiles()
            with patch.dict(
                os.environ,
                {"BENCHMARK_PROJECT": "context-rag", "BENCHMARK_FROZEN_FILE": "frozen.json"},
            ):
                self.assertEqual(set(environment_profiles()), {"context-rag"})

    def test_control_socket_lifecycle_without_model_credentials(self):
        profiles = self.root / "profiles.json"
        atomic_json(profiles, {})
        path = self.root / "control.sock"
        with (self.root / "service.log").open("w+") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "app.benchmark.control",
                    "--socket",
                    str(path),
                    "--database",
                    str(self.root / "control.db"),
                    "--root",
                    str(self.root / "results"),
                    "--profiles",
                    str(profiles),
                ],
                env={**os.environ, "TELEGRAM_ALLOWED_USER_IDS": "123"},
                stdout=log,
                stderr=log,
            )
            try:
                for _ in range(100):
                    if path.exists() or process.poll() is not None:
                        break
                    time.sleep(0.05)
                if not path.exists():
                    log.seek(0)
                    output = log.read()
                    if "Operation not permitted" in output:
                        self.skipTest(
                            "Unix socket binding requires the unsandboxed verification run"
                        )
                    self.fail(output)
                result = call_control(
                    path, {"user_id": 123, "command_id": "one", "text": "/status"}
                )
                self.assertEqual(result, {"jobs": [], "experiments": []})
                with self.assertRaises(ValueError):
                    call_control(path, {"user_id": 456, "command_id": "two", "text": "/status"})
                with self.assertRaises(ValueError):
                    call_control(
                        path,
                        {
                            "user_id": 123,
                            "command_id": "three",
                            "text": "/executar context-rag 1 1",
                        },
                    )
            finally:
                process.terminate()
                process.wait(timeout=5)
            self.assertFalse(path.exists())

    def test_retry_cooldown_and_stage_limit_preserve_saved_answer(self):
        from app.benchmark.runner import run_resumable_benchmark

        dataset = self.root / "dataset.json"
        atomic_json(dataset, [{"id": "Q001", "question": "Question", "ground_truth": "Reference"}])
        answer = Mock(return_value={"answer": "Candidate", "contexts": ["Evidence"]})
        judge = Mock(side_effect=RuntimeError("judge failure"))

        def run():
            return run_resumable_benchmark(
                "test",
                answer,
                dataset_path=dataset,
                output_dir=self.root / "results",
                metric_evaluators={"faithfulness": judge},
            )

        with (
            patch.dict(
                os.environ,
                {"BENCHMARK_RETRY_COOLDOWN_SECONDS": "60", "BENCHMARK_MAX_STAGE_ATTEMPTS": "2"},
            ),
            patch("app.benchmark.runner.time.time", return_value=100),
        ):
            run()
            self.assertEqual(run()["attempted"], 0)
        with (
            patch.dict(
                os.environ,
                {"BENCHMARK_RETRY_COOLDOWN_SECONDS": "0", "BENCHMARK_MAX_STAGE_ATTEMPTS": "2"},
            ),
            patch("app.benchmark.runner.time.time", return_value=200),
        ):
            run()
            self.assertEqual(run()["attempted"], 0)
        self.assertEqual(answer.call_count, 1)
        self.assertEqual(judge.call_count, 2)

    def test_frozen_export_roundtrip_preserves_actual_evidence(self):
        from app.benchmark.admin import export_frozen
        from app.benchmark.pipeline import frozen_answers
        from app.benchmark.runner import run_resumable_benchmark

        dataset = self.root / "dataset.json"
        questions = [{"id": "Q001", "question": "Question", "ground_truth": "Reference"}]
        atomic_json(dataset, questions)
        manifest = {
            "project": "context-rag",
            "experiment_id": "a" * 64,
            "mode": "full",
            "evidence_policy": "actual",
        }
        output = self.root / "results"
        run_resumable_benchmark(
            "context-rag",
            lambda q: {
                "answer": "Candidate",
                "contexts": ["Exact evidence"],
                "evidence_metadata": [{"source": "book"}],
                "generation_contexts": ["Full tool evidence"],
                "answer_response_time_seconds": 2.5,
                "answer_input_tokens": 11,
                "answer_output_tokens": 7,
                "answer_total_tokens": 18,
            },
            dataset_path=dataset,
            output_dir=output,
            manifest=manifest,
            metric_evaluators={"faithfulness": lambda a: {"faithfulness": 1}},
        )
        target = self.root / "frozen.json"
        with patch("app.benchmark.admin.DEFAULT_DATASET", dataset):
            export_frozen(output, target)
        restored = frozen_answers(target, "context-rag", questions)
        self.assertEqual(restored["Q001"]["contexts"], ["Exact evidence"])
        self.assertEqual(restored["Q001"]["answer"], "Candidate")
        self.assertEqual(restored["Q001"]["generation_contexts"], ["Full tool evidence"])
        self.assertEqual(restored["Q001"]["answer_total_tokens"], 18)
        self.assertEqual(restored["Q001"]["answer_response_time_seconds"], 2.5)
        with (
            patch("app.benchmark.admin.DEFAULT_DATASET", dataset),
            self.assertRaises(FileExistsError),
        ):
            export_frozen(output, target)

    def test_supervisor_stops_only_its_process_group_at_deadline(self):
        control = self.control()
        control.handle(
            {"user_id": 123, "command_id": "deadline", "text": "/executar context-rag 1 1"}
        )
        worker = Worker(control)
        worker.process = Mock()
        worker.process.poll.return_value = None
        worker.process.pid = 12345
        worker.job, worker.started = "deadline", 0
        with (
            patch.dict(os.environ, {"BENCHMARK_MAX_SECONDS": "1"}),
            patch("app.benchmark.control.time.monotonic", return_value=2),
            patch("app.benchmark.control.os.killpg") as kill,
        ):
            worker.tick()
            kill.assert_called_once_with(12345, __import__("signal").SIGTERM)
        with (
            patch("app.benchmark.control.time.monotonic", return_value=18),
            patch("app.benchmark.control.os.killpg") as kill,
        ):
            worker.stop()
            kill.assert_called_once_with(12345, __import__("signal").SIGKILL)

    def test_scientific_repetition_has_an_independent_identity(self):
        from app.benchmark.config import build_manifest

        docs = self.root / "docs"
        docs.mkdir()
        (docs / "book.pdf").write_bytes(b"corpus identity")
        dataset = self.root / "dataset.json"
        atomic_json(dataset, [])
        with patch.dict(os.environ, {"DOCS_DIR": str(docs), "BENCHMARK_REPETITION": "1"}):
            first = build_manifest("context-rag", dataset)
        with patch.dict(os.environ, {"DOCS_DIR": str(docs), "BENCHMARK_REPETITION": "2"}):
            second = build_manifest("context-rag", dataset)
        self.assertNotEqual(first["experiment_id"], second["experiment_id"])

    def test_lifecycle_events_survive_offline_notifier_without_duplicates(self):
        root = self.root / "results"
        append_event(
            root / "rag/experiment/public_events.jsonl",
            {"event_id": "start", "kind": "started"},
            mode=0o640,
        )
        append_event(
            root / "rag/experiment/public_events.jsonl",
            {"event_id": "finish", "kind": "finished"},
            mode=0o640,
        )
        notifier = Notifier(root, self.root / "events.db", Mock(), "channel")
        self.addCleanup(notifier.db.close)
        notifier.scan()
        notifier.scan()
        self.assertEqual(notifier.db.execute("SELECT count(*) FROM outbox").fetchone()[0], 2)
        notifier.deliver()
        self.assertEqual(notifier.client.call.call_count, 2)

    def test_doctor_accepts_separate_role_keys_without_general_key(self):
        from app import cli as main

        for name in main.PROJECTS:
            docs = (
                self.root
                / name
                / ("data/apostilas" if name == "knowledge-enhanced-rag" else "docs")
            )
            docs.mkdir(parents=True)
            (docs / "book.pdf").write_bytes(b"presence check")
        with (
            patch("app.cli.RAGS_ROOT", self.root),
            patch("app.cli.load_environment"),
            patch.dict(
                os.environ,
                {
                    "OPENROUTER_GENERATION_API_KEY": "generation",
                    "OPENROUTER_JUDGE_API_KEY": "judge",
                    "OPENROUTER_EMBEDDING_API_KEY": "embedding",
                    "BENCHMARK_KG_MODE": "snapshot",
                },
            ),
        ):
            self.assertEqual(main.doctor(), 0)
            os.environ.pop("OPENROUTER_JUDGE_API_KEY")
            self.assertEqual(main.doctor(), 1)

    def test_pause_request_stops_admission_inside_preparation(self):
        ledger = UsageLedger(self.root / "usage.jsonl")
        first = ledger.admit("model", "/embeddings", {"input": "chunk"})
        ledger.finish(first, elapsed=0, body={"usage": {"cost": 0.1}})
        (self.root / "pause.request").touch()
        with self.assertRaises(BudgetExceeded):
            ledger.admit("model", "/embeddings", {"input": "next chunk"})
        self.assertEqual(ledger.calls, 1)
        with self.assertRaises(ValueError):
            parse_command("/pergunta context-rag " + "a" * 64 + " Q999")


if __name__ == "__main__":
    unittest.main()
