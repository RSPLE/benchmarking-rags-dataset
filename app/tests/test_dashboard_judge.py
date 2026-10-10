from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.benchmark.control import parse_command
from app.benchmark.judge_audit import JudgeTrace, judge_context, read_events
from app.tests.test_judge_review import seed


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Run in the dashboard environment")
class DashboardJudgeTests(unittest.TestCase):
    def setUp(self):
        from streamlit.testing.v1 import AppTest

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest, self.checkpoint = seed(self.root)
        with judge_context(self.root, "Q001", "run"):
            trace = JudgeTrace("context_recall", self.checkpoint["items"]["Q001"]["artifact"])
            trace.emit("response", responses=[['{"reason":"Trecho insuficiente"}']])
            trace.emit(
                "judgment",
                outputs=[
                    {
                        "classifications": [
                            {
                                "statement": "Afirmação",
                                "attributed": 0,
                                "reason": "Trecho insuficiente",
                            }
                        ]
                    }
                ],
            )
            trace.emit("finished", values={"context_recall": 0})
        events = read_events((self.root / "judge_responses.jsonl").read_bytes())
        self.traces = {trace.trace_id: events}
        self.status = {"enabled": True, "jobs": []}
        self.requests = []

        def request(settings, token, text, command_id=None):
            if text == "/status":
                return self.status
            self.requests.append((text, command_id))
            return {"job_id": command_id, "state": "queued"}

        for mocked in (
            patch(
                "app.dashboard.judge.judge_data",
                side_effect=lambda *args: (self.checkpoint, self.traces, []),
            ),
            patch("app.dashboard.judge.request_control", side_effect=request),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.app = AppTest.from_string("""
from types import SimpleNamespace
from app.dashboard.judge import judge_view
import streamlit as st
record = {'id':'local', 'project':'graph-rag', 'external_id':st.session_state.experiment}
judge_view(SimpleNamespace(database='unused'), 'session', [record])
""")
        self.app.session_state.experiment = self.manifest["experiment_id"]
        self.app.run()

    def button(self, label):
        return next(button for button in self.app.button if button.label == label)

    def test_shows_zero_rationale_and_evidence_difference_without_launching_work(self):
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.metric[0].label, "Notas originais")
        self.assertEqual(self.app.metric[0].value, "2")
        self.assertEqual(self.app.metric[1].label, "Notas zero")
        self.assertEqual(self.app.metric[1].value, "2")
        self.assertEqual(self.app.metric[2].label, "Justificativas gravadas")
        self.assertEqual(self.app.metric[2].value, "1")
        self.assertEqual(self.app.metric[3].label, "Elegíveis para reavaliação")
        self.assertEqual(self.app.metric[3].value, "2")
        self.assertTrue(any("Justificativa" in item.value for item in self.app.dataframe))
        self.assertIn("Trecho insuficiente", str([item.value for item in self.app.dataframe]))
        self.assertEqual(self.requests, [])

    def test_legacy_csv_fields_are_shown_without_inventing_rejudging_evidence(self):
        from app.dashboard.judge import audit_rows, display_artifact, merge_saved_results

        checkpoint = {
            "items": {
                "Q001": {
                    "status": "success",
                    "question": "Pergunta antiga",
                    "result": {"context_recall": 0.25},
                }
            }
        }
        merge_saved_results(
            checkpoint,
            [
                {
                    "id": "Q001",
                    "question": "Pergunta antiga",
                    "status": "success",
                    "answer": "Resposta preservada no CSV",
                    "context_recall": 0.25,
                }
            ],
            {
                "Pergunta antiga": {
                    "id": "Q001",
                    "answer": "Resposta preservada no CSV",
                    "ground_truth": "Gabarito preservado no CSV",
                    "source_book": "Livro",
                }
            },
        )
        artifact = display_artifact(checkpoint["items"]["Q001"])
        frame = audit_rows(checkpoint, {}, [], "context_recall")

        self.assertEqual(artifact["answer"], "Resposta preservada no CSV")
        self.assertEqual(artifact["ground_truth"], "Gabarito preservado no CSV")
        self.assertEqual(frame.iloc[0]["Nota original"], 0.25)
        self.assertFalse(frame.iloc[0]["Justificativa gravada"])
        self.assertFalse(frame.iloc[0]["Pode reavaliar"])
        self.assertIn("Nota registrada", frame.iloc[0]["Dados disponíveis"])
        self.assertIn("Justificativa não gravada", frame.iloc[0]["Dados disponíveis"])

    def test_batch_targets_only_selected_metric_and_explicit_generation_evidence(self):
        self.app.radio[0].set_value("generation").run()
        self.button("Selecionar todas as questões elegíveis deste filtro").click().run()
        self.button("Reavaliar métrica selecionada").click().run()
        self.assertFalse(self.app.exception)
        command = parse_command(self.requests[0][0])
        self.assertEqual(command["review"]["metric"], "context_recall")
        self.assertEqual(command["review"]["question_ids"], ["Q001", "Q002"])
        self.assertEqual(command["review"]["evidence"], "generation")

    def test_busy_executor_blocks_review_and_legacy_output_is_explicit(self):
        self.status["jobs"] = [{"state": "running", "job_id": "active"}]
        self.traces.clear()
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertTrue(self.button("Reavaliar métrica selecionada").disabled)
        self.assertTrue(any("não foi gravada" in element.value for element in self.app.info))
        self.assertEqual(self.requests, [])

    def test_recovered_metric_is_not_filtered_as_failed_and_question_selection_follows_detail(self):
        self.checkpoint["items"]["Q001"]["failed_stage"] = "context_recall"
        self.app.run()
        self.assertFalse(self.app.dataframe[0].value["Falha na métrica"].any())
        detail = next(
            item for item in self.app.selectbox if item.label == "Questão para ver os detalhes"
        )
        detail.set_value("Q002").run()
        self.assertEqual(self.app.multiselect[0].value, ["Q002"])
        self.assertFalse(self.app.exception)

    def test_lost_acknowledgment_reuses_request_identity(self):
        def dropped(settings, token, text, command_id=None):
            if text == "/status":
                return self.status
            self.requests.append((text, command_id))
            raise TimeoutError("Lost acknowledgment")

        with patch("app.dashboard.judge.request_control", side_effect=dropped):
            self.button("Reavaliar métrica selecionada").click().run()
            self.button("Reavaliar métrica selecionada").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.requests[0][1], self.requests[1][1])


if __name__ == "__main__":
    unittest.main()
