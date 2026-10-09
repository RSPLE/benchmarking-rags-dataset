from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

AVAILABLE = importlib.util.find_spec("streamlit") is not None


@unittest.skipUnless(AVAILABLE, "Run with the dashboard environment")
class QuestionResultsTests(unittest.TestCase):
    def setUp(self):
        from app.benchmark.export import METRICS, result_bytes
        from app.dashboard.database import connect
        from app.dashboard.ingest import store_source
        from app.paths import DATASET_ROOT

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "dashboard.sqlite3"
        questions = json.loads((DATASET_ROOT / "qa_dataset_90.json").read_bytes())
        result = {"question": questions[0]["question"], "answer": "Resposta preservada"}
        result.update({metric: 0 for metric in METRICS})
        checkpoint = {
            "items": {
                "Q001": {
                    "status": "success",
                    "question": questions[0]["question"],
                    "result": result,
                },
                "Q002": {
                    "status": "failed",
                    "artifact": {
                        "question": questions[1]["question"],
                        "answer": "Avaliação incompleta",
                    },
                    "metrics": {"faithfulness": {"status": "success", "value": 0.5}},
                },
            }
        }
        (self.root / "results.csv").write_bytes(result_bytes([result]))
        (self.root / "checkpoint.json").write_text(json.dumps(checkpoint))
        events = [
            {
                "call_id": "generation",
                "question_id": "Q001",
                "stage": "generation",
                "kind": "started",
            },
            {
                "call_id": "generation",
                "question_id": "Q001",
                "stage": "generation",
                "kind": "finished",
                "cost_usd": 0.01,
            },
            {"call_id": "retry", "question_id": "Q001", "stage": "generation", "cost_usd": 0.02},
            {"call_id": "judge", "question_id": "Q001", "stage": "faithfulness", "cost_usd": 0.03},
            {"call_id": "unknown", "question_id": "Q001", "stage": "generation"},
            {"call_id": "prep", "question_id": "Q001", "stage": "preparation", "cost_usd": 1},
            {"call_id": "free", "question_id": "Q002", "stage": "generation", "cost_usd": 0},
            {"call_id": "unreported", "question_id": "Q003", "stage": "generation"},
        ]
        (self.root / "usage.jsonl").write_text("".join(json.dumps(row) + "\n" for row in events))
        (self.root / "other").mkdir()
        for name in ("results.csv", "checkpoint.json", "usage.jsonl"):
            (self.root / "other" / name).write_bytes((self.root / name).read_bytes())
        with connect(self.database) as db, db:
            store_source(db, self.root / "results.csv", self.root, "context-rag", "legacy")
            self.identifier = db.execute("SELECT id FROM experiments").fetchone()[0]
            store_source(db, self.root / "other/results.csv", self.root, "graph-rag", "legacy")

    def test_all_90_questions_preserve_answers_partial_metrics_and_zero(self):
        import pandas as pd

        from app.dashboard.questions import question_results

        frame = question_results(self.database, self.identifier).set_index("id")
        self.assertEqual(len(frame), 90)
        self.assertEqual(frame.loc["Q001", "answer"], "Resposta preservada")
        self.assertEqual(frame.loc["Q001", "faithfulness"], 0)
        self.assertEqual(frame.loc["Q002", "status"], "Falhou")
        self.assertEqual(frame.loc["Q002", "answer"], "Avaliação incompleta")
        self.assertEqual(frame.loc["Q002", "faithfulness"], 0.5)
        self.assertTrue(pd.isna(frame.loc["Q002", "context_precision"]))
        self.assertEqual(frame.loc["Q090", "status"], "Pendente")

    def test_cost_sums_retries_and_judge_once_without_preparation_or_other_rags(self):
        import pandas as pd

        from app.dashboard.questions import question_results

        frame = question_results(self.database, self.identifier).set_index("id")
        self.assertAlmostEqual(frame.loc["Q001", "cost_usd"], 0.06)
        self.assertEqual(frame.loc["Q001", "cost_coverage"], "3/4 chamadas")
        self.assertEqual(frame.loc["Q002", "cost_usd"], 0)
        self.assertTrue(pd.isna(frame.loc["Q003", "cost_usd"]))
        self.assertEqual(frame.loc["Q003", "cost_coverage"], "0/1 chamadas")
        self.assertTrue(pd.isna(frame.loc["Q090", "cost_usd"]))

    def test_architecture_without_experiment_has_90_pending_questions(self):
        from app.dashboard.questions import question_results

        frame = question_results(self.database)
        self.assertEqual(len(frame), 90)
        self.assertTrue((frame["status"] == "Pendente").all())
        self.assertTrue(frame["cost_usd"].isna().all())

    def test_both_web_tables_render_and_switch_architectures(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_string("""
from app.dashboard.analytics import experiments
from app.dashboard.questions import question_results_view
import streamlit as st
question_results_view(st.session_state.database, experiments(st.session_state.database),
                      consumption_only=st.session_state.consumption_only)
""")
        app.session_state.database = self.database
        for consumption_only in (False, True):
            app.session_state.consumption_only = consumption_only
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.dataframe[0].value), 90)
            self.assertIn("cost_usd", app.dataframe[0].value)
            self.assertEqual("faithfulness" in app.dataframe[0].value, not consumption_only)
            app.selectbox[0].select("self-rag").run()
            self.assertFalse(app.exception)
            self.assertTrue((app.dataframe[0].value["status"] == "Pendente").all())


if __name__ == "__main__":
    unittest.main()
