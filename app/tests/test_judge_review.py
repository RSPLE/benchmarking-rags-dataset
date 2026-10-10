from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from app.benchmark.control import Control, Worker, parse_command
from app.benchmark.evaluation import MetricEvaluator
from app.benchmark.judge_audit import JudgeResult, judge_context, read_events
from app.benchmark.review import run_review
from app.benchmark.runner import run_resumable_benchmark
from app.benchmark.storage import append_event, atomic_json, exclusive_lock, fingerprint
from app.dashboard.database import connect
from app.dashboard.ingest import store_source


def seed(directory):
    manifest = {"project": "graph-rag", "mode": "full", "configuration": {}}
    manifest["experiment_id"] = fingerprint(manifest)
    items = {}
    for number in (1, 2):
        artifact = {
            "question": f"Pergunta {number}",
            "ground_truth": "Referência",
            "answer": "Resposta preservada",
            "contexts": ["Contexto avaliado"],
            "generation_contexts": ["Contexto realmente utilizado"],
        }
        digest = fingerprint(artifact)
        items[f"Q{number:03d}"] = {
            "status": "success",
            "artifact": artifact,
            "artifact_sha256": digest,
            "metrics": {
                "context_recall": {"status": "success", "value": 0, "input_sha256": digest}
            },
            "result": {"context_recall": 0},
        }
    checkpoint = {
        "version": 2,
        "project": "graph-rag",
        "manifest_fingerprint": fingerprint(manifest),
        "items": items,
    }
    atomic_json(directory / "manifest.json", manifest)
    atomic_json(directory / "checkpoint.json", checkpoint)
    return manifest, checkpoint


class JudgeReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.directory = self.root / "results/graph-rag/experiment"
        self.manifest, self.checkpoint = seed(self.directory)
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def review(self, evaluator, **kwargs):
        return run_review(
            self.directory,
            kwargs.pop("identifiers", ["Q001"]),
            "context_recall",
            evaluator=evaluator,
            budget_root=self.root / "results",
            **kwargs,
        )

    def test_targeted_retry_preserves_originals_and_other_questions_and_is_idempotent(self):
        before = (self.directory / "checkpoint.json").read_bytes()
        evaluator = Mock()
        evaluator.evaluate.return_value = JudgeResult({"context_recall": 0.75}, "trace")
        result = self.review(evaluator, request_id="review-one")
        self.assertEqual(result["success"], 1)
        evaluator.evaluate.assert_called_once_with(
            "context_recall", self.checkpoint["items"]["Q001"]["artifact"]
        )
        self.assertEqual((self.directory / "checkpoint.json").read_bytes(), before)
        self.assertEqual(self.review(evaluator, request_id="review-one"), result)
        self.assertEqual(evaluator.evaluate.call_count, 1)
        events = read_events((self.directory / "judge_reviews.jsonl").read_bytes())
        self.assertEqual(events[1]["previous_value"], 0)
        self.assertEqual(events[1]["value"], 0.75)
        self.assertEqual(events[1]["question_id"], "Q001")
        with self.assertRaisesRegex(ValueError, "outros parâmetros"):
            self.review(evaluator, request_id="review-one", evidence="generation")

    def test_generation_evidence_is_an_explicit_separate_review(self):
        evaluator = Mock()
        evaluator.evaluate.return_value = {"context_recall": 1}
        self.review(evaluator, identifiers=["Q001", "Q002"], evidence="generation")
        self.assertEqual(evaluator.evaluate.call_count, 2)
        self.assertEqual(
            evaluator.evaluate.call_args.args[1]["contexts"], ["Contexto realmente utilizado"]
        )
        self.assertEqual(
            json.loads((self.directory / "checkpoint.json").read_bytes()), self.checkpoint
        )
        events = read_events((self.directory / "judge_reviews.jsonl").read_bytes())
        self.assertEqual(events[1]["evidence"], "generation")

    def test_all_targets_are_validated_before_any_paid_calls(self):
        evaluator = Mock()
        with self.assertRaisesRegex(ValueError, "Q090"):
            self.review(evaluator, identifiers=["Q001", "Q090"])
        evaluator.evaluate.assert_not_called()
        self.checkpoint["items"]["Q001"]["artifact"]["contexts"] = ["Alterado"]
        atomic_json(self.directory / "checkpoint.json", self.checkpoint)
        with self.assertRaisesRegex(ValueError, "alteradas"):
            self.review(evaluator)
        evaluator.evaluate.assert_not_called()

    def test_missing_legacy_evidence_cannot_be_recreated_silently(self):
        self.checkpoint["items"]["Q001"].pop("artifact")
        atomic_json(self.directory / "checkpoint.json", self.checkpoint)
        with self.assertRaisesRegex(ValueError, "não foram salvos"):
            self.review(Mock())

    def test_failed_rejudge_keeps_original_score_and_failure_record(self):
        evaluator = Mock()
        error = RuntimeError("Judge returned malformed output")
        error.judge_trace_id = "failed-trace"
        evaluator.evaluate.side_effect = error
        result = self.review(evaluator)
        self.assertEqual(result["failed"], 1)
        events = read_events((self.directory / "judge_reviews.jsonl").read_bytes())
        self.assertEqual(events[1]["judge_trace_id"], "failed-trace")
        self.assertNotIn("value", events[1])
        self.assertEqual(
            json.loads((self.directory / "checkpoint.json").read_bytes()), self.checkpoint
        )

    def test_credential_failure_stops_batch_without_charging_remaining_questions(self):
        error = RuntimeError("Provider rejected credentials")
        error.status_code = 401
        evaluator = Mock()
        evaluator.evaluate.side_effect = error
        result = self.review(evaluator, identifiers=["Q001", "Q002"])
        self.assertEqual((result["failed"], result["pending"]), (1, 1))
        evaluator.evaluate.assert_called_once()

    def test_live_reader_ignores_partial_utf8_tail_but_rejects_complete_corruption(self):
        complete = b'{"kind":"started"}\n'
        self.assertEqual(read_events(complete + b'{"text":"\xc3'), [{"kind": "started"}])
        with self.assertRaises(ValueError):
            read_events(complete + b"{broken}\n")

    def test_pipeline_keeps_failed_and_successful_judgments_in_private_archive_and_dashboard(self):
        directory = self.root / "original-run"
        dataset = self.root / "dataset.json"
        artifact = self.checkpoint["items"]["Q001"]["artifact"]
        atomic_json(dataset, [{"id": "Q001", **artifact}])
        answer = Mock(return_value=artifact)
        failure = True

        def judge(name, candidate, trace):
            trace.emit("response", responses=[["Judge explanation"]])
            if name == "context_recall" and failure:
                raise ValueError("Invalid judge JSON")
            return {name: 0.5}

        evaluator = MetricEvaluator()
        handlers = evaluator.handlers()
        with (
            patch.object(MetricEvaluator, "_evaluate", side_effect=judge),
            patch.dict(os.environ, {"BENCHMARK_RETRY_COOLDOWN_SECONDS": "0"}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            run_resumable_benchmark(
                "graph-rag",
                answer,
                dataset_path=dataset,
                output_dir=directory,
                metric_evaluators=handlers,
                manifest=self.manifest,
            )
            failed = json.loads((directory / "checkpoint.json").read_bytes())["items"]["Q001"]
            failed_trace = failed["metrics"]["context_recall"]["judge_trace_id"]
            self.assertEqual(failed["metrics"]["context_recall"]["status"], "failed")
            failure = False
            run_resumable_benchmark(
                "graph-rag",
                answer,
                dataset_path=dataset,
                output_dir=directory,
                metric_evaluators=handlers,
                manifest=self.manifest,
            )
        answer.assert_called_once()
        checkpoint = json.loads((directory / "checkpoint.json").read_bytes())
        state = checkpoint["items"]["Q001"]
        self.assertEqual(state["status"], "success")
        self.assertNotEqual(state["metrics"]["context_recall"]["judge_trace_id"], failed_trace)
        self.assertEqual(
            state["metrics"]["faithfulness"]["judge_trace_id"],
            failed["metrics"]["faithfulness"]["judge_trace_id"],
        )
        events = read_events((directory / "judge_responses.jsonl").read_bytes())
        self.assertEqual(len({event["trace_id"] for event in events}), 5)
        self.assertTrue(
            any(e["trace_id"] == failed_trace and e["kind"] == "failed" for e in events)
        )
        summary = json.loads((directory / "summary.json").read_bytes())
        with zipfile.ZipFile(directory / "archives" / f"{summary['run_id']}.zip") as archive:
            self.assertEqual(
                archive.read("judge_responses.jsonl"),
                (directory / "judge_responses.jsonl").read_bytes(),
            )
        with zipfile.ZipFile(
            directory / "deliveries" / summary["run_id"] / "results.zip"
        ) as archive:
            self.assertNotIn("judge_responses.jsonl", archive.namelist())
        # A review changes the imported artifacts even though original scores stay unchanged.
        with connect(self.root / "dashboard.sqlite3") as db:
            self.assertTrue(
                store_source(db, directory / "results.csv", self.root, "graph-rag", "v2")
            )
            raw = db.execute(
                "SELECT content FROM artifacts WHERE name='judge_responses.jsonl'"
            ).fetchone()[0]
            self.assertEqual(raw, (directory / "judge_responses.jsonl").read_bytes())
            self.assertFalse(
                store_source(db, directory / "results.csv", self.root, "graph-rag", "v2")
            )
            run_review(
                directory,
                ["Q001"],
                "context_recall",
                evaluator=Mock(evaluate=Mock(return_value={"context_recall": 1})),
                budget_root=self.root,
            )
            self.assertTrue(
                store_source(db, directory / "results.csv", self.root, "graph-rag", "v2")
            )
            self.assertIsNotNone(
                db.execute(
                    "SELECT content FROM artifacts WHERE name='judge_reviews.jsonl'"
                ).fetchone()
            )

    def test_active_worker_and_interrupted_request_never_replay_calls(self):
        evaluator = Mock()
        with exclusive_lock(self.root / "results/.worker.lock"), self.assertRaises(RuntimeError):
            self.review(evaluator)
        append_event(
            self.directory / "judge_reviews.jsonl",
            {
                "request_id": "interrupted",
                "kind": "started",
                "request": {
                    "question_ids": ["Q001"],
                    "metric": "context_recall",
                    "evidence": "original",
                    "reason": "Revisão solicitada pelo operador",
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "interrompido"):
            self.review(evaluator, request_id="interrupted")
        evaluator.evaluate.assert_not_called()

    def test_trace_is_durable_on_evaluation_failure_and_redacts_credentials(self):
        secret = "private-test-api-key"

        def fail(name, artifact, trace):
            trace.emit("response", responses=[[f"broken JSON {secret}"]])
            raise RuntimeError("Parser failed")

        with (
            patch.dict(os.environ, {"OPENROUTER_API_KEY": secret}),
            patch.object(MetricEvaluator, "_evaluate", side_effect=fail),
        ):
            with (
                judge_context(self.directory, "Q001", "run"),
                self.assertRaises(RuntimeError) as caught,
            ):
                MetricEvaluator().evaluate(
                    "context_recall", self.checkpoint["items"]["Q001"]["artifact"]
                )
        raw = (self.directory / "judge_responses.jsonl").read_bytes()
        self.assertNotIn(secret.encode(), raw)
        events = read_events(raw)
        self.assertEqual([e["kind"] for e in events], ["started", "response", "failed"])
        self.assertEqual(events[-1]["trace_id"], caught.exception.judge_trace_id)
        self.assertEqual(events[0]["inputs"]["contexts"], ["Contexto avaliado"])
        self.assertEqual((self.directory / "judge_responses.jsonl").stat().st_mode & 0o777, 0o600)

    def test_control_queues_only_requested_metric_and_worker_uses_review_entrypoint(self):
        destination = self.directory.parent / self.manifest["experiment_id"]
        self.directory.rename(destination)
        self.directory = destination
        control = Control(
            self.root / "results",
            self.root / "queue.db",
            {123},
            enabled=True,
            profiles={"graph-rag": {"mode": "full"}},
        )
        self.addCleanup(control.db.close)
        text = f"/reavaliar graph-rag {self.manifest['experiment_id']} --metric context_recall --question-ids Q001"
        request = {"user_id": 123, "command_id": "web:review:test", "text": text}
        response = control.handle(request)
        self.assertEqual(control.handle(request), response)
        worker = Worker(control)
        with patch("app.benchmark.control.subprocess.Popen") as launch:
            worker.tick()
            command = launch.call_args.args[0]
            self.assertIn("app.benchmark.review", command)
            self.assertNotIn("main.py", command)
            self.assertEqual(launch.call_args.kwargs["env"]["BENCHMARK_CREDIT_SCOPE"], "judge")
            self.assertEqual(command[command.index("--metric") + 1], "context_recall")
        worker.log.close()
        for invalid in ("Q000", "Q091", "Q001,Q001", "Q001;id"):
            with self.assertRaises(ValueError):
                parse_command(text.replace("--question-ids Q001", "--question-ids " + invalid))


@unittest.skipUnless(importlib.util.find_spec("ragas"), "Run in the locked RAG environment")
class RealJudgeAuditTests(unittest.TestCase):
    def test_malformed_real_completion_is_saved_before_ragas_parser_failure(self):
        from app.providers.ragas_compat import ensure_ragas_langchain_compat

        ensure_ragas_langchain_compat()
        from langchain_core.language_models.fake_chat_models import FakeListChatModel
        from ragas.llms.base import LangchainLLMWrapper

        malformed = "Malformed judge output that must survive the parser"
        artifact = {
            "question": "Question",
            "answer": "Candidate",
            "ground_truth": "Reference",
            "contexts": ["Evidence"],
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "app.providers.models.build_ragas_llm",
                return_value=LangchainLLMWrapper(FakeListChatModel(responses=[malformed])),
            ),
            patch("app.providers.models.build_embeddings", return_value=Mock()),
            judge_context(directory, "Q001", "offline-run"),
        ):
            with self.assertRaises(Exception) as caught:
                MetricEvaluator().evaluate("context_recall", artifact)
            events = read_events((Path(directory) / "judge_responses.jsonl").read_bytes())
        self.assertTrue(
            any(e["kind"] == "response" and malformed in e["responses"][0] for e in events)
        )
        self.assertEqual(events[-1]["kind"], "failed")
        self.assertEqual(events[-1]["trace_id"], caught.exception.judge_trace_id)

    def test_real_ragas_saves_visible_completion_and_structured_reason_without_network(self):
        from app.providers.ragas_compat import ensure_ragas_langchain_compat

        ensure_ragas_langchain_compat()
        from langchain_core.language_models.fake_chat_models import FakeListChatModel
        from ragas.llms.base import LangchainLLMWrapper

        answer = json.dumps(
            {
                "classifications": [
                    {
                        "statement": "Reference claim",
                        "reason": "Supported by the saved passage",
                        "attributed": 1,
                    }
                ]
            }
        )
        artifact = {
            "question": "Question",
            "answer": "Candidate",
            "ground_truth": "Reference claim",
            "contexts": ["Reference claim"],
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "app.providers.models.build_ragas_llm",
                return_value=LangchainLLMWrapper(FakeListChatModel(responses=[answer])),
            ),
            patch("app.providers.models.build_embeddings", return_value=Mock()),
            judge_context(directory, "Q001", "offline-run"),
        ):
            result = MetricEvaluator().evaluate("context_recall", artifact)
            events = read_events((Path(directory) / "judge_responses.jsonl").read_bytes())
        self.assertEqual(result["context_recall"], 1)
        self.assertTrue(
            any(e["kind"] == "response" and answer in e["responses"][0] for e in events)
        )
        judgments = [e for e in events if e["kind"] == "judgment"]
        self.assertEqual(
            judgments[0]["outputs"][0]["classifications"][0]["reason"],
            "Supported by the saved passage",
        )
        self.assertEqual(events[-1]["trace_id"], result.judge_trace_id)


if __name__ == "__main__":
    unittest.main()
