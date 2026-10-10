from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.benchmark.config import METRICS
from app.benchmark.export import result_bytes
from app.benchmark.storage import atomic_json, fingerprint
from app.dashboard.catalog import set_visibility
from app.dashboard.database import connect
from app.dashboard.ingest import store_source
from app.paths import DATASET_ROOT


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Run in the dashboard environment")
class DownloadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.database = self.root / "dashboard.sqlite3"
        self.questions = json.loads((DATASET_ROOT / "qa_dataset_90.json").read_bytes())
        self.records = [
            self.seed(project, 90) for project in ("context-rag", "graph-rag", "hybrid-rag")
        ]
        self.discarded = self.seed("hybrid-rag", 0)

    def seed(self, project, success):
        manifest = {
            "project": project,
            "configuration": {"OPENROUTER_MODEL": project},
            "success": success,
        }
        external = fingerprint(manifest)
        manifest["experiment_id"] = external
        directory = self.root / project / external
        items, results = {}, []
        for index, question in enumerate(self.questions):
            passed = index < success
            result = {"question": question["question"], **{metric: 0.5 for metric in METRICS}}
            items[question["id"]] = {
                "question": question["question"],
                "status": "success" if passed else "failed" if index == 0 else "pending",
            }
            if passed:
                items[question["id"]]["result"] = result
                results.append(result)
        atomic_json(directory / "checkpoint.json", {"items": items})
        atomic_json(directory / "manifest.json", manifest)
        atomic_json(
            directory / "summary.json",
            {
                "success": success,
                "failed": int(not success),
                "pending": 0 if success else 89,
                "operation": "idle" if success else "paused",
            },
        )
        (directory / "results.csv").write_bytes(result_bytes(results))
        with connect(self.database) as db, db:
            store_source(db, directory / "results.csv", self.root, project, "v2")
            identifier = db.execute(
                "SELECT id FROM experiments WHERE external_id=?", (external,)
            ).fetchone()[0]
        return {
            "id": identifier,
            "external_id": external,
            "project": project,
            "directory": directory,
        }

    def test_hidden_failure_stays_hidden_after_sync_and_can_be_restored_without_data_loss(self):
        from app.dashboard.analytics import experiments

        item = self.discarded
        before = (item["directory"] / "checkpoint.json").read_bytes()
        set_visibility(
            self.database,
            item["project"],
            item["external_id"],
            hidden=True,
            reason="Descartado pelo usuário",
        )
        (item["directory"] / "usage.jsonl").write_text("\n")
        with connect(self.database) as db, db:
            store_source(db, item["directory"] / "results.csv", self.root, item["project"], "v2")
            self.assertEqual(db.execute("SELECT count(*) FROM experiments").fetchone()[0], 4)
        self.assertEqual(len(experiments(self.database)), 3)
        self.assertEqual((item["directory"] / "checkpoint.json").read_bytes(), before)
        set_visibility(self.database, item["project"], item["external_id"], hidden=False)
        self.assertEqual(len(experiments(self.database)), 4)

    def test_running_experiment_cannot_be_hidden(self):
        item = self.discarded
        with connect(self.database) as db, db:
            db.execute(
                "UPDATE experiments SET summary=? WHERE id=?",
                (json.dumps({"operation": "running"}), item["id"]),
            )
        with self.assertRaisesRegex(ValueError, "andamento"):
            set_visibility(self.database, item["project"], item["external_id"], hidden=True)

    def test_completed_downloads_cross_protocols_and_exclude_discarded_hybrid(self):
        from streamlit.testing.v1 import AppTest

        item = self.discarded
        set_visibility(self.database, item["project"], item["external_id"], hidden=True)
        app = AppTest.from_string("""
import streamlit as st
from app.dashboard.analytics import experiments
from app.dashboard.downloads import downloads_view
downloads_view(st.session_state.database, experiments(st.session_state.database))
""")
        app.session_state.database = self.database
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe[0].value), 3)
        self.assertEqual(set(app.dataframe[0].value["Questões concluídas"]), {"90/90"})
        app.selectbox[0].select("hybrid-rag").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox[1].value, self.records[2]["id"])
        app.radio[0].set_value("Todas").run()
        self.assertEqual(len(app.dataframe[0].value), 3)
        self.assertEqual(len(app.get("download_button")), 3)

    def test_incomplete_results_are_available_only_when_requested(self):
        from app.dashboard.analytics import experiments
        from app.dashboard.downloads import is_complete

        records = experiments(self.database)
        self.assertEqual(sum(is_complete(row, 90) for row in records), 3)
        self.assertFalse(is_complete({"summary": {"success": 89, "failed": 0, "pending": 0}}, 90))
        self.assertFalse(is_complete({"summary": {"success": 90, "operation": "running"}}, 90))

    def test_download_archive_has_exact_original_files_and_valid_checksums(self):
        from app.benchmark.archive import archive_bytes
        from app.dashboard.downloads import experiment_files

        item = self.records[2]
        files = experiment_files(self.database, item["id"])
        self.assertEqual(files["results.csv"], (item["directory"] / "results.csv").read_bytes())
        with zipfile.ZipFile(io.BytesIO(archive_bytes(files))) as archive:
            checksums = json.loads(archive.read("checksums.json"))
            self.assertEqual(set(checksums), set(files))
            for name, digest in checksums.items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
