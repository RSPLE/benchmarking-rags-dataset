from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.benchmark.config import METRICS
from app.benchmark.layout import resume_manifest
from app.benchmark.runner import DEFAULT_DATASET, run_resumable_benchmark
from app.benchmark.storage import atomic_json, file_hash, fingerprint
from app.dashboard.ingest import comparison_identity
from app.paths import ROOT
from app.tools.continue_legacy import continue_checkpoint


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = patch.dict(
            os.environ, {"BENCHMARK_OUTPUT_DIR": str(self.root / "results")}, clear=True
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.source = ROOT / "app/rags/graph-rag/results/checkpoint.json"
        self.original = json.loads(self.source.read_text())
        self.source_hash = file_hash(self.source)
        self.manifest = {
            "schema": 2,
            "project": "graph-rag",
            "mode": "full",
            "dataset_sha256": file_hash(DEFAULT_DATASET),
            "configuration": {"BENCHMARK_REPETITION": "1"},
            "code": {"verified": "test"},
        }
        self.manifest["experiment_id"] = fingerprint(self.manifest)

    def test_graph_continues_exactly_69_failures_preserving_all_21_successes(self):
        preview = continue_checkpoint(self.source, self.manifest)
        self.assertFalse(Path(preview["destination"]).exists())
        report = continue_checkpoint(self.source, self.manifest, apply=True)
        directory = Path(report["destination"])
        manifest = json.loads((directory / "manifest.json").read_text())
        self.assertEqual(
            (report["preserved_successes"], report["failed"], report["pending"]), (21, 69, 0)
        )
        summary = json.loads((directory / "summary.json").read_text())
        self.assertEqual(summary["metrics"]["faithfulness"]["count"], 21)
        self.assertEqual(resume_manifest(self.manifest, report["experiment_id"]), manifest)
        answered = []

        def answer(question):
            answered.append(question["id"])
            return {"answer": "New answer", "contexts": ["Actual test evidence"]}

        with contextlib.redirect_stdout(io.StringIO()):
            counts = run_resumable_benchmark(
                "graph-rag",
                answer,
                output_dir=directory,
                manifest=manifest,
                metric_evaluators={name: lambda value, n=name: {n: 0.5} for name in METRICS},
                question_limit=90,
            )
        failed = {
            key for key, state in self.original["items"].items() if state["status"] == "failed"
        }
        self.assertEqual(set(answered), failed)
        self.assertEqual(len(answered), 69)
        self.assertEqual(counts["success"], 90)
        checkpoint = json.loads((directory / "checkpoint.json").read_text())
        for key, state in self.original["items"].items():
            if state["status"] == "success":
                self.assertEqual(checkpoint["items"][key]["result"], state["result"])
                self.assertNotIn("artifact", checkpoint["items"][key])
        again = continue_checkpoint(self.source, self.manifest, apply=True)
        self.assertTrue(again["already_exists"])
        self.assertEqual(json.loads((directory / "checkpoint.json").read_text()), checkpoint)
        self.assertEqual(file_hash(self.source), self.source_hash)
        self.assertEqual(file_hash(directory / "original.checkpoint.json"), self.source_hash)

    def test_partial_resume_and_stage_events_preserve_progress(self):
        report = continue_checkpoint(self.source, self.manifest, apply=True)
        directory = Path(report["destination"])
        manifest = json.loads((directory / "manifest.json").read_text())
        answer = Mock(return_value={"answer": "New answer", "contexts": ["Evidence"]})
        metrics = {name: Mock(return_value={name: 0.5}) for name in METRICS}
        with contextlib.redirect_stdout(io.StringIO()):
            result = run_resumable_benchmark(
                "graph-rag",
                answer,
                output_dir=directory,
                manifest=manifest,
                metric_evaluators=metrics,
                question_limit=1,
                selection="failed",
            )
        self.assertEqual((result["success"], result["failed"]), (22, 68))
        self.assertEqual(answer.call_count, 1)
        events = [
            json.loads(line)
            for line in (directory / "public_events.jsonl").read_text().splitlines()
        ]
        self.assertTrue(
            {"started", "stage_started", "answer_saved", "metric_saved", "success", "finished"}
            <= {event["kind"] for event in events}
        )
        self.assertNotIn("New answer", json.dumps(events))
        self.assertNotIn("Evidence", json.dumps(events))

    def test_changed_dataset_question_or_invalid_success_are_rejected(self):
        for change in ("dataset", "question", "metric"):
            source = copy.deepcopy(self.original)
            if change == "dataset":
                source["dataset_sha256"] = "different"
            elif change == "question":
                source["items"]["Q001"]["question"] = "different"
            else:
                source["items"]["Q001"]["result"]["faithfulness"] = None
            path = self.root / f"{change}.json"
            atomic_json(path, source)
            with self.subTest(change=change), self.assertRaises(ValueError):
                continue_checkpoint(path, self.manifest, apply=True)
        changed = {**self.manifest, "configuration": {"BENCHMARK_REPETITION": "2"}}
        changed.pop("experiment_id")
        changed["experiment_id"] = fingerprint(changed)
        report = continue_checkpoint(self.source, self.manifest, apply=True)
        with self.assertRaises(ValueError):
            resume_manifest(changed, report["experiment_id"])

    def test_other_original_checkpoints_remain_resumable(self):
        for project, counts in [("context-rag", (89, 1, 0)), ("hybrid-rag", (0, 1, 89))]:
            manifest = {**self.manifest, "project": project}
            report = continue_checkpoint(
                ROOT / "app/rags" / project / "results/checkpoint.json", manifest
            )
            self.assertEqual(
                (report["preserved_successes"], report["failed"], report["pending"]), counts
            )

    def test_continuation_comparisons_keep_previous_and_current_provenance(self):
        continued = {**self.manifest, "continuation": {"source_sha256": self.source_hash}}
        identity = comparison_identity(continued)
        self.assertNotEqual(identity, comparison_identity(self.manifest))
        self.assertNotEqual(
            identity,
            comparison_identity({**continued, "configuration": {"OPENROUTER_MODEL": "other"}}),
        )

    @unittest.skipUnless(importlib.util.find_spec("pandas"), "Dashboard environment required")
    def test_monitor_displays_continuation_without_duplicate_history(self):
        from app.dashboard.analytics import experiments
        from app.dashboard.database import connect
        from app.dashboard.ingest import store_source

        report = continue_checkpoint(self.source, self.manifest, apply=True)
        directory = Path(report["destination"])
        historical = self.root / "legacy/graph-rag/results"
        historical.mkdir(parents=True)
        (historical / "checkpoint.json").write_bytes(self.source.read_bytes())
        (historical / "results.csv").write_bytes((directory / "results.csv").read_bytes())
        database = self.root / "dashboard.db"
        with connect(database) as db:
            store_source(
                db, historical / "results.csv", self.root / "legacy", "graph-rag", "legacy"
            )
            store_source(db, directory / "results.csv", self.root / "results", "graph-rag", "v2")
            db.commit()
            self.assertEqual(db.execute("SELECT count(*) FROM experiments").fetchone()[0], 2)
        visible = experiments(database)
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["external_id"], report["experiment_id"])
        self.assertEqual(visible[0]["summary"]["failed"], 69)


if __name__ == "__main__":
    unittest.main()
