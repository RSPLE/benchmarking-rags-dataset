from __future__ import annotations

import importlib.util
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Streamlit runtime required")
class DashboardExecutionTests(unittest.TestCase):
    def setUp(self):
        from streamlit.testing.v1 import AppTest

        self.status = {"jobs": [], "experiments": [], "enabled": True}
        self.submissions = []

        def request(settings, token, text, command_id=None):
            if text == "/status":
                return self.status
            self.submissions.append(command_id)
            return {"job_id": command_id, "state": "queued"}

        mocked = patch("app.dashboard.execution.request_control", side_effect=request)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.app = AppTest.from_string("""
from pathlib import Path
from types import SimpleNamespace
from app.dashboard.execution import execution_view
settings = SimpleNamespace(control_socket=Path('/missing/control.sock'), poll_seconds=15)
execution_view(settings, 'test-session', [])
""").run()

    def submit(self):
        next(
            button for button in self.app.button if button.label == "Iniciar pipeline"
        ).click().run()
        self.assertFalse(self.app.exception)

    def test_same_configuration_can_start_a_new_job_after_acknowledgement(self):
        self.assertEqual(self.app.number_input[0].value, 1)
        self.submit()
        self.submit()
        self.assertEqual(len(set(self.submissions)), 2)

    def test_failed_job_shows_reason_without_stale_success(self):
        self.status["jobs"] = [
            {
                "job_id": "web:failed",
                "state": "failed",
                "project": "context-rag",
                "error": "Sem permissão no volume de resultados.",
            }
        ]
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any("Falha ao iniciar" in error.value for error in self.app.error))
        self.assertTrue(any("Sem permissão" in text.value for text in self.app.markdown))
        self.assertFalse(any("registrado na fila" in text.value for text in self.app.success))

    def test_busy_or_disabled_executor_blocks_submission(self):
        for status in (
            {"enabled": False, "jobs": []},
            {"enabled": True, "jobs": [{"job_id": "busy", "state": "running"}]},
        ):
            self.status.update(status)
            self.app.run()
            self.assertFalse(self.app.exception)
            self.assertTrue(
                next(
                    button for button in self.app.button if button.label == "Iniciar pipeline"
                ).disabled
            )

    def test_continuation_defaults_to_resume_69_without_repeating_successes(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_string("""
from pathlib import Path
from types import SimpleNamespace
from app.dashboard.execution import execution_view
settings = SimpleNamespace(control_socket=Path('/missing/control.sock'), poll_seconds=15)
record = {'origin':'v2', 'project':'graph-rag', 'external_id':'a'*64,
          'summary':{'success':21,'failed':69,'pending':0},
          'manifest':{'mode':'full','continuation':{'source_sha256':'b'*64}}}
execution_view(settings, 'test-session', [record])
""").run()
        next(widget for widget in app.selectbox if widget.label == "Pipeline RAG").select(
            "graph-rag"
        ).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.radio[0].value, "Retomar experimento")
        self.assertEqual(app.number_input[0].value, 69)
        self.assertTrue(any("21 questões concluídas" in element.value for element in app.info))
        next(button for button in app.button if button.label == "Retomar pipeline").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(self.submissions), 1)

    def test_lost_acknowledgement_reuses_request_id(self):
        def dropped(settings, token, text, command_id=None):
            if text == "/status":
                return self.status
            self.submissions.append(command_id)
            raise TimeoutError("Lost acknowledgement")

        with patch("app.dashboard.execution.request_control", side_effect=dropped):
            self.submit()
            self.app.run()
            self.assertTrue(any("não foi confirmada" in error.value for error in self.app.error))
            self.submit()
        self.assertEqual(len(self.submissions), 2)
        self.assertEqual(self.submissions[0], self.submissions[1])


if __name__ == "__main__":
    unittest.main()
