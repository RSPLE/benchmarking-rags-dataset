from __future__ import annotations

import contextlib
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from benchmark_admin import migrate
from benchmark_config import METRICS, build_manifest
from benchmark_index import cached_extraction, ensure_index
from benchmark_pipeline import critique_decision, frozen_answers, tool_evidence
from benchmark_runner import run_resumable_benchmark, validate_metrics
from benchmark_storage import atomic_json, exclusive_lock, file_hash, fingerprint, sanitize
from benchmark_usage import BudgetExceeded, UsageLedger, retry_delay


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / "dataset.json"
        self.questions = [
            {"id": f"Q{i}", "question": f"Question {i}", "ground_truth": f"Reference {i}"}
            for i in range(3)
        ]
        atomic_json(self.dataset, self.questions)
        self.output = self.root / "output"
        self.answer = Mock(side_effect=lambda q: {"answer": "Candidate", "contexts": ["Evidence"]})
        self.first = Mock(return_value={"faithfulness": 0.0})
        self.second = Mock(return_value={"context_recall": 0.8})
        self.handlers = {"faithfulness": self.first, "context_recall": self.second}

    def run_cases(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_resumable_benchmark(
                "test",
                self.answer,
                dataset_path=self.dataset,
                output_dir=self.output,
                metric_evaluators=self.handlers,
                **kwargs,
            )

    def checkpoint(self):
        return json.loads((self.output / "checkpoint.json").read_text())

    def test_resume_only_failed_metric_and_never_regenerate_saved_answer(self):
        self.second.side_effect = RuntimeError("judge failed")
        self.run_cases(question_limit=1)
        saved = self.checkpoint()["items"]["Q0"]
        self.assertEqual(saved["artifact"]["contexts"], ["Evidence"])
        self.assertEqual(saved["metrics"]["faithfulness"]["value"], 0.0)
        self.second.side_effect = None
        self.run_cases(question_limit=1)
        self.assertEqual(self.answer.call_count, 1)
        self.assertEqual(self.first.call_count, 1)
        self.assertEqual(self.second.call_count, 2)
        self.assertEqual(self.checkpoint()["items"]["Q0"]["status"], "success")
        events = [
            json.loads(line) for line in (self.output / "events.jsonl").read_text().splitlines()
        ]
        self.assertTrue(any(e["kind"] == "failed" for e in events))
        self.assertEqual(json.loads((self.output / "errors.json").read_text())["count"], 0)

    def test_hard_process_exit_between_metrics_is_resumable(self):
        script = """
import os,sys
from pathlib import Path
from benchmark_runner import run_resumable_benchmark
run_resumable_benchmark('test', lambda q: {'answer':'Candidate','contexts':['Evidence']},
    dataset_path=Path(sys.argv[1]), output_dir=Path(sys.argv[2]), question_limit=1,
    metric_evaluators={'faithfulness':lambda a:{'faithfulness':0},
                       'context_recall':lambda a:os._exit(23)})
"""
        child = subprocess.run(
            [sys.executable, "-c", script, str(self.dataset), str(self.output)], capture_output=True
        )
        self.assertEqual(child.returncode, 23)
        result = self.run_cases(question_limit=1)
        self.assertEqual(result["run_success"], 1)
        self.answer.assert_not_called()
        self.first.assert_not_called()
        self.second.assert_called_once()

    def test_evaluation_mode_never_prepares_a_pipeline(self):
        from benchmark_pipeline import execute_pipeline
        from benchmark_storage import atomic_json

        rows = [
            {
                "experiment_id": "generated",
                "rag": "context-rag",
                "question_id": q["id"],
                "question": q["question"],
                "reference": q["ground_truth"],
                "response": "Candidate",
                "retrieved_contexts": ["Evidence"],
                "generation_fingerprint": "source",
                "evidence_metadata": [],
            }
            for q in self.questions
        ]
        frozen = self.root / "frozen.json"
        atomic_json(frozen, rows)
        prepare = Mock(side_effect=AssertionError("paid preparation"))
        handlers = {name: Mock(return_value={name: 0.5}) for name in METRICS}
        with (
            patch.dict(
                os.environ,
                {
                    "BENCHMARK_MODE": "evaluate",
                    "BENCHMARK_FROZEN_FILE": str(frozen),
                    "BENCHMARK_OUTPUT_DIR": str(self.output),
                },
            ),
            patch("benchmark_pipeline.DEFAULT_DATASET", self.dataset),
            patch("benchmark_pipeline.MetricEvaluator.handlers", return_value=handlers),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            execute_pipeline("context-rag", prepare)
            execute_pipeline("context-rag", prepare)
        prepare.assert_not_called()
        for handler in handlers.values():
            self.assertEqual(handler.call_count, len(self.questions))

    def test_invalid_metrics_cannot_be_successful(self):
        for value in (None, math.nan, math.inf, -math.inf, True, "0.5", -0.1, 1.1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_metrics({"faithfulness": value}, ["faithfulness"])
        self.assertEqual(
            validate_metrics({"answer_relevancy": -0.5}, ["answer_relevancy"]),
            {"answer_relevancy": -0.5},
        )
        self.assertEqual(
            validate_metrics({"answer_relevancy": 1.0000000000000002}, ["answer_relevancy"])[
                "answer_relevancy"
            ],
            1.0000000000000002,
        )
        with self.assertRaises(ValueError):
            validate_metrics({"answer_relevancy": -1.1}, ["answer_relevancy"])
        with self.assertRaises(ValueError):
            validate_metrics({}, METRICS)
        self.assertEqual(
            validate_metrics({"faithfulness": 0}, ["faithfulness"]), {"faithfulness": 0.0}
        )

    def test_lock_rejects_second_process_before_call(self):
        with exclusive_lock(self.output / ".lock"):
            command = [
                sys.executable,
                "-c",
                "from benchmark_storage import exclusive_lock; import sys;\nwith exclusive_lock(sys.argv[1]): pass",
                str(self.output / ".lock"),
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            with self.assertRaisesRegex(RuntimeError, "worker"):
                self.run_cases(question_limit=1)
        self.answer.assert_not_called()

    def test_changed_manifest_or_dataset_rejected_before_calls(self):
        self.run_cases(question_limit=1, manifest={"judge": "a"})
        self.answer.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "configuration"):
            self.run_cases(manifest={"judge": "b"})
        self.answer.assert_not_called()
        self.dataset.write_text(self.dataset.read_text() + "\n")
        with self.assertRaisesRegex(RuntimeError, "Dataset"):
            self.run_cases(manifest={"judge": "a"})

    def test_configuration_model_prompt_and_corpus_are_fingerprinted(self):
        with patch.dict(
            os.environ, {"DOCS_DIR": str(self.root), "OPENROUTER_JUDGE_MODEL": "judge-a"}
        ):
            (self.root / "book.pdf").write_bytes(b"pdf-one")
            one = build_manifest("context-rag", self.dataset)
            with patch.dict(os.environ, {"OPENROUTER_JUDGE_MODEL": "judge-b"}):
                two = build_manifest("context-rag", self.dataset)
            self.assertNotEqual(one["experiment_id"], two["experiment_id"])
            (self.root / "book.pdf").write_bytes(b"pdf-two")
            three = build_manifest("context-rag", self.dataset)
            self.assertNotEqual(one["experiment_id"], three["experiment_id"])

    def test_crashed_running_state_preserves_completed_stages(self):
        self.second.side_effect = RuntimeError("failure")
        self.run_cases(question_limit=1)
        state = self.checkpoint()
        state["items"]["Q0"]["status"] = "running"
        atomic_json(self.output / "checkpoint.json", state)
        self.second.side_effect = None
        self.run_cases(question_limit=1)
        self.assertEqual(self.answer.call_count, 1)
        self.assertEqual(self.first.call_count, 1)

    def test_authorization_and_credit_errors_stop_after_one_case(self):
        for status in (400, 401, 402, 403, 404):
            with self.subTest(status=status):
                self.output = self.root / str(status)
                error = RuntimeError("provider rejected request")
                error.status_code = status
                self.answer.side_effect = error
                counts = self.run_cases()
                self.assertEqual(counts["attempted"], 1)
                self.assertEqual(counts["paused"], 1)

    def test_pause_between_metrics_preserves_answer_and_first_metric(self):
        def pause(_):
            (self.output / "pause.request").touch()
            return {"faithfulness": 0.5}

        self.first.side_effect = pause
        result = self.run_cases()
        self.assertEqual(result["paused"], 1)
        self.second.assert_not_called()
        (self.output / "pause.request").unlink()
        self.first.side_effect = None
        self.run_cases(question_limit=1)
        self.assertEqual(self.answer.call_count, 1)
        self.assertEqual(self.first.call_count, 1)

    def test_corrupt_checkpoint_never_resets(self):
        self.output.mkdir()
        (self.output / "checkpoint.json").write_text('{"items":')
        with self.assertRaises(ValueError):
            self.run_cases()
        self.answer.assert_not_called()

    def test_migration_dry_run_and_archive_preserve_source(self):
        original = self.root / "old.json"
        data = {
            "version": 1,
            "project": "context-rag",
            "items": {"Q0": {"status": "success", "result": {"faithfulness": None}}},
        }
        atomic_json(original, data)
        before = file_hash(original)
        target = self.root / "archive"
        self.assertEqual(migrate(original, target)["invalid_successes"], ["Q0"])
        self.assertFalse(target.exists())
        migrate(original, target, apply=True)
        self.assertEqual(file_hash(original), before)
        archived = json.loads((target / "checkpoint.json").read_text())
        self.assertTrue(archived["legacy"])
        self.assertEqual(archived["items"]["Q0"]["result"], data["items"]["Q0"]["result"])

    def test_frozen_answer_missing_evidence_never_uses_reference(self):
        path = self.root / "frozen.json"
        row = {
            "experiment_id": "old",
            "rag": "test",
            "question_id": "Q0",
            "question": "Question 0",
            "reference": "Reference 0",
            "response": "Candidate",
            "retrieved_contexts": ["Evidence"],
            "generation_fingerprint": "known",
            "evidence_metadata": [],
        }
        atomic_json(path, [row])
        self.assertEqual(frozen_answers(path, "test", self.questions)["Q0"]["answer"], "Candidate")
        row.pop("retrieved_contexts")
        atomic_json(path, [row])
        with self.assertRaises(ValueError):
            frozen_answers(path, "test", self.questions)

    def test_tool_evidence_is_actual_ordered_tool_output(self):
        messages = [
            SimpleNamespace(type="ai", content="answer"),
            SimpleNamespace(type="tool", content="graph facts", name="graph"),
            SimpleNamespace(type="tool", content="specific document", name="vector"),
        ]
        contexts, metadata = tool_evidence(messages)
        self.assertEqual(contexts, ["graph facts", "specific document"])
        self.assertEqual([m["tool"] for m in metadata], ["graph", "vector"])

    def test_self_critique_handles_accents_and_rejects_ambiguous_text(self):
        self.assertFalse(critique_decision("NÃO."))
        self.assertTrue(critique_decision("SIM"))
        with self.assertRaises(ValueError):
            critique_decision("SIM, mas NAO")

    def test_partial_index_resumes_without_duplicate_embedding(self):
        documents = [SimpleNamespace(page_content=f"chunk-{i}", metadata={}) for i in range(70)]

        class Store:
            def __init__(self):
                self.ids = set()
                self.embedded = []
                self.fail = True

            def get(self, **_):
                return {"ids": list(self.ids)}

            def add_documents(self, documents, ids):
                if self.ids and self.fail:
                    raise RuntimeError("interrupted")
                self.embedded.extend(ids)
                self.ids.update(ids)

        store = Store()
        manifest = self.root / "index.json"
        with self.assertRaises(RuntimeError):
            ensure_index(store, documents, manifest, "config")
        store.fail = False
        ensure_index(store, documents, manifest, "config")
        ensure_index(store, documents, manifest, "config")
        self.assertEqual(len(store.embedded), 70)
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            ensure_index(store, documents, manifest, "changed")

    def test_graph_cache_validates_and_avoids_repeated_extraction(self):
        extract = Mock(return_value={"entities": [], "relations": []})
        path = self.root / "graph.json"
        cached_extraction(path, "id", extract)
        cached_extraction(path, "id", extract)
        extract.assert_called_once()
        with self.assertRaises(ValueError):
            cached_extraction(self.root / "bad.json", "id", lambda: {})

    def test_budget_and_unknown_cost_stop_admission(self):
        with patch.dict(os.environ, {"BENCHMARK_MAX_CALLS": "1"}):
            ledger = UsageLedger(self.root / "usage.jsonl")
            call = ledger.admit("model", "/chat")
            ledger.finish(call, elapsed=0.1)
            self.assertIsNone(ledger.summary()["cost_usd"])
            with self.assertRaises(BudgetExceeded):
                ledger.admit("model", "/chat")
        with patch.dict(os.environ, {"BENCHMARK_MAX_COST_USD": "1"}):
            ledger = UsageLedger(self.root / "cost.jsonl")
            ledger.finish(ledger.admit("model", "/chat"), elapsed=0.1)
            with self.assertRaises(BudgetExceeded):
                ledger.admit("model", "/chat")

    def test_daily_and_preparation_limits_survive_restart(self):
        log = self.root / "rag" / "experiment" / "usage.jsonl"
        with patch.dict(os.environ, {"BENCHMARK_MAX_DAILY_COST_USD": "1"}):
            first = UsageLedger(log, budget_root=self.root)
            first.finish(
                first.admit("model", "/chat"),
                elapsed=0.1,
                body={"usage": {"cost": 1.0, "total_tokens": 3}},
            )
            second = UsageLedger(log, budget_root=self.root)
            with self.assertRaises(BudgetExceeded):
                second.admit("model", "/chat")
        with patch.dict(os.environ, {"BENCHMARK_MAX_PREPARATION_CALLS": "1"}):
            ledger = UsageLedger(self.root / "prep.jsonl")
            ledger.admit("model", "/embeddings")
            with self.assertRaises(BudgetExceeded):
                ledger.admit("model", "/embeddings")

    def test_retry_after_and_secret_redaction(self):
        response = SimpleNamespace(headers={"retry-after": "7"})
        self.assertEqual(retry_delay(response, 0), 7)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "example-private-key"}):
            self.assertNotIn("example-private-key", sanitize("error example-private-key"))
        self.assertNotEqual(fingerprint(["a", "b"]), fingerprint(["b", "a"]))


if __name__ == "__main__":
    unittest.main()
