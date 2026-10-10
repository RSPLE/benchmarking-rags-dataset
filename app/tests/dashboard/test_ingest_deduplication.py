from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.benchmark.export import RESULT_COLUMNS, result_bytes
from app.dashboard.database import connect
from app.dashboard.ingest import (
    remove_duplicate_experiments,
    source_paths,
    store_source,
    synchronize,
)


class IngestDeduplicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.results = self.root / "results"
        self.legacy = self.root / "legacy"
        self.database = self.root / "dashboard.sqlite3"
        payload = result_bytes(
            [{"question": "Question", **{key: 0.5 for key in RESULT_COLUMNS[1:]}}]
        )
        self.current = self.results / "context-rag/current/results.csv"
        self.historical = self.legacy / "context-rag/results/results.csv"
        self.current.parent.mkdir(parents=True)
        self.historical.parent.mkdir(parents=True)
        self.current.write_bytes(payload.replace(b"\r\n", b"\n"))
        self.historical.write_bytes(payload)

    def test_exact_legacy_copy_is_not_discovered_when_current_result_exists(self):
        settings = SimpleNamespace(results=self.results, legacy=self.legacy)
        sources = list(source_paths(settings))
        self.assertEqual(
            [(path, project, origin) for path, _, project, origin in sources],
            [(self.current, "context-rag", "v2")],
        )

    def test_existing_legacy_copy_is_physically_removed_from_database(self):
        with connect(self.database) as db, db:
            store_source(db, self.historical, self.legacy, "context-rag", "legacy")
            store_source(db, self.current, self.results, "context-rag", "v2")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0], 2)
            self.assertEqual(remove_duplicate_experiments(db), 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT origin FROM experiments").fetchone()[0], "v2")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 1)

        settings = SimpleNamespace(
            results=self.results,
            legacy=self.legacy,
            database=self.database,
        )
        self.assertEqual(synchronize(settings), 0)
        with connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
