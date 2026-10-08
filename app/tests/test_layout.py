from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.benchmark.config import METRICS, build_manifest, experiment_directory
from app.benchmark.export import RESULT_COLUMNS, result_bytes
from app.benchmark.layout import REGISTRY, resume_manifest
from app.benchmark.pipeline import execute_pipeline
from app.benchmark.runner import run_resumable_benchmark
from app.benchmark.storage import atomic_json, fingerprint
from app.dashboard.config import Settings
from app.dashboard.database import connect
from app.dashboard.ingest import store_source
from app.paths import ROOT


def identify(manifest):
    manifest = copy.deepcopy(manifest)
    manifest.pop("experiment_id", None)
    return {**manifest, "experiment_id": fingerprint(manifest)}


class LayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dataset = self.root / "dataset.json"
        atomic_json(
            self.dataset, [{"id": "Q001", "question": "Question", "ground_truth": "Reference"}]
        )
        (self.root / "book.pdf").write_bytes(b"local test corpus")
        environment = patch.dict(
            os.environ,
            {
                "BENCHMARK_OUTPUT_DIR": str(self.root / "results"),
                "BENCHMARK_RETRY_COOLDOWN_SECONDS": "0",
                "DOCS_DIR": str(self.root),
            },
            clear=True,
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.registry = json.loads(REGISTRY.read_text())
        self.current = build_manifest("context-rag", self.dataset)
        self.previous = self.old_manifest(self.current)
        atomic_json(experiment_directory(self.previous) / "manifest.json", self.previous)

    def old_manifest(self, current):
        previous = copy.deepcopy(current)
        project = current["project"]
        previous["code"] = {
            **self.registry["legacy_shared"],
            **self.registry["projects"][project]["legacy"],
        }
        previous["index"]["path"] = str(ROOT / "rags" / project / "chroma_v2")
        return identify(previous)

    def test_verified_move_accepts_original_identity_for_all_six_rags(self):
        for project in self.registry["projects"]:
            with self.subTest(project=project):
                current = build_manifest(project, self.dataset, mode="evaluate")
                previous = self.old_manifest(current)
                atomic_json(experiment_directory(previous) / "manifest.json", previous)
                self.assertEqual(resume_manifest(current, previous["experiment_id"]), previous)

    def test_changed_inputs_and_code_cannot_resume_old_experiment(self):
        cases = (
            ("configuration", "OPENROUTER_MODEL", "different-model"),
            ("runtime", "python", "0.0.0"),
            ("index", "path", str(self.root / "other-index")),
            ("code", "app/benchmark/runner.py", "changed"),
        )
        for section, key, value in cases:
            current = copy.deepcopy(self.current)
            current[section][key] = value
            with self.subTest(section=section), self.assertRaises(ValueError):
                resume_manifest(identify(current), self.previous["experiment_id"])
        for key, value in (("dataset_sha256", "changed"), ("corpus", [])):
            with self.subTest(field=key), self.assertRaises(ValueError):
                resume_manifest(
                    identify({**self.current, key: value}), self.previous["experiment_id"]
                )

    def test_unregistered_old_revision_and_corrupt_identity_are_rejected(self):
        unknown = copy.deepcopy(self.previous)
        unknown["code"]["benchmark_runner.py"] = "different"
        unknown = identify(unknown)
        atomic_json(experiment_directory(unknown) / "manifest.json", unknown)
        with self.assertRaisesRegex(ValueError, "different code revision"):
            resume_manifest(self.current, unknown["experiment_id"])
        unknown["project"] = "corrupted"
        atomic_json(
            experiment_directory(unknown).parent.parent
            / "context-rag"
            / unknown["experiment_id"]
            / "manifest.json",
            unknown,
        )
        with self.assertRaisesRegex(ValueError, "identity is invalid"):
            resume_manifest(self.current, unknown["experiment_id"])
        for identifier in ("../manifest", "", "a" * 63):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                resume_manifest(self.current, identifier)

    def test_resume_retains_answer_and_completed_metrics_and_records_transition(self):
        handlers = {name: Mock(return_value={name: 0.5}) for name in METRICS}
        handlers["context_recall"].side_effect = ValueError("judge failed")
        output = experiment_directory(self.previous)
        answer = Mock(return_value={"answer": "Candidate", "contexts": ["Evidence"]})
        with contextlib.redirect_stdout(io.StringIO()):
            run_resumable_benchmark(
                "context-rag",
                answer,
                dataset_path=self.dataset,
                output_dir=output,
                manifest=self.previous,
                metric_evaluators=handlers,
                required_metrics=METRICS,
            )
        manifest_bytes = (output / "manifest.json").read_bytes()
        for handler in handlers.values():
            handler.reset_mock()
        handlers["context_recall"].side_effect = None
        prepare = Mock(side_effect=AssertionError("saved answer must not be regenerated"))
        with (
            patch.dict(
                os.environ, {"BENCHMARK_EXPECTED_EXPERIMENT": self.previous["experiment_id"]}
            ),
            patch("app.benchmark.pipeline.DEFAULT_DATASET", self.dataset),
            patch("app.benchmark.pipeline.MetricEvaluator.handlers", return_value=handlers),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            counts = execute_pipeline("context-rag", prepare)
        self.assertEqual(counts["success"], 1)
        prepare.assert_not_called()
        answer.assert_called_once()
        handlers["context_recall"].assert_called_once()
        for name in METRICS[:-1]:
            handlers[name].assert_not_called()
        self.assertEqual((output / "manifest.json").read_bytes(), manifest_bytes)
        records = list((output / "layout_transitions").glob("*.json"))
        self.assertEqual(len(records), 1)
        record = json.loads(records[0].read_text())
        self.assertEqual(record["original_experiment_id"], self.previous["experiment_id"])
        self.assertEqual(record["executed_manifest"], self.current)

    def test_local_database_keeps_its_existing_filename(self):
        with patch("app.dashboard.config.ROOT", self.root):
            settings = Settings.from_environment()
        self.assertEqual(settings.database, self.root / "app/dashboard/state/dashboard.sqlite3")

    def test_historical_move_keeps_database_ids_and_artifacts(self):
        old_root = self.root / "rags"
        source = old_root / "context-rag/results/results.csv"
        source.parent.mkdir(parents=True)
        payload = result_bytes(
            [{"question": "Question", **{key: 0.5 for key in RESULT_COLUMNS[1:]}}]
        )
        source.write_bytes(payload)
        with connect(self.root / "dashboard.sqlite3") as db:
            with db:
                self.assertTrue(store_source(db, source, old_root, "context-rag", "legacy"))
            identifier = db.execute("SELECT id FROM experiments").fetchone()[0]
            before = [tuple(row) for row in db.execute("SELECT * FROM artifacts")]
            (self.root / "app").mkdir()
            new_root = self.root / "app/rags"
            old_root.rename(new_root)
            moved = new_root / source.relative_to(old_root)
            with db:
                self.assertTrue(store_source(db, moved, new_root, "context-rag", "legacy"))
            self.assertEqual(db.execute("SELECT count(*) FROM experiments").fetchone()[0], 1)
            self.assertEqual(
                db.execute("SELECT id,source FROM experiments").fetchone()[:],
                (identifier, str(moved)),
            )
            self.assertEqual(before, [tuple(row) for row in db.execute("SELECT * FROM artifacts")])
            self.assertEqual(
                db.execute("SELECT experiment_id FROM samples").fetchone()[0], identifier
            )
            self.assertEqual(moved.read_bytes(), payload)
            self.assertFalse(store_source(db, moved, new_root, "context-rag", "legacy"))


if __name__ == "__main__":
    unittest.main()
