from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard.auth import authenticate, bootstrap_account
from dashboard.config import Settings
from dashboard.database import connect
from dashboard.operations import build_command, request_control
from dashboard.sessions import create_session, revoke_session, session_identity


class WebSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "test.sqlite3"
        self.password = "session-test-password"
        bootstrap_account(self.database, "tester", self.password)
        self.identity = authenticate(self.database, "tester", self.password)
        self.token = create_session(self.database, self.identity, 3600)
        self.settings = Settings(
            self.database, self.root, self.root, self.root, control_user_id=123
        )

    def test_cookie_restores_session_across_database_connections(self):
        self.assertEqual(session_identity(self.database, self.token)["username"], "tester")
        with connect(self.database) as db:
            row = dict(db.execute("SELECT * FROM web_sessions").fetchone())
        self.assertNotIn(self.token, str(row))
        self.assertNotIn(self.token.encode(), self.database.read_bytes())
        bootstrap_account(self.database, "tester", self.password)
        self.assertEqual(session_identity(self.database, self.token)["username"], "tester")

    def test_logout_and_expiry_reject_old_cookie(self):
        with patch("dashboard.sessions.time.time", return_value=time.time() + 3601):
            self.assertIsNone(session_identity(self.database, self.token))
        revoke_session(self.database, self.token)
        self.assertIsNone(session_identity(self.database, self.token))

    def test_password_rotation_invalidates_all_sessions(self):
        second = create_session(self.database, self.identity, 3600)
        bootstrap_account(self.database, "tester", "changed-session-password")
        self.assertIsNone(session_identity(self.database, self.token))
        self.assertIsNone(session_identity(self.database, second))

    def test_forged_cookie_cannot_submit_commands(self):
        for token in (None, "", "wrong", "a" * 43):
            with self.subTest(token=token), patch("dashboard.operations.call_control") as call:
                with self.assertRaises(PermissionError):
                    request_control(self.settings, token, "/executar context-rag --questions 1")
                call.assert_not_called()

    def test_web_resume_uses_existing_parser_and_fixed_identity(self):
        experiment = "b" * 64
        command = build_command(
            "retomar",
            "context-rag",
            experiment,
            questions=2,
            selection="failed",
            provider="openrouter",
            max_calls=5,
        )
        with patch("dashboard.operations.call_control", return_value={"state": "queued"}) as call:
            request_control(self.settings, self.token, command, "web:stable-request")
            request = call.call_args.args[1]
        self.assertEqual(request["user_id"], 123)
        self.assertEqual(request["command_id"], "web:stable-request")
        self.assertIn("--selection failed", request["text"])
        self.assertIn(experiment, request["text"])
        self.assertNotIn(self.token, str(request))

    def test_invalid_flags_and_targets_never_reach_controller(self):
        for project in ("hermes", "context-rag; touch /tmp/unwanted"):
            with self.assertRaises(ValueError):
                build_command("executar", project, questions=1)
        with self.assertRaises(ValueError):
            build_command("retomar", "context-rag", "../bad", questions=1)
        with self.assertRaises(ValueError):
            build_command("executar", "context-rag", questions=91)

    def test_dotted_username_generates_valid_control_identifier(self):
        bootstrap_account(self.database, "operator.name", self.password)
        identity = authenticate(self.database, "operator.name", self.password)
        token = create_session(self.database, identity, 3600)
        with patch("dashboard.operations.call_control", return_value={}) as call:
            request_control(self.settings, token, "/status")
        self.assertRegex(call.call_args.args[1]["command_id"], r"^web:[a-f0-9]{32}$")


if __name__ == "__main__":
    unittest.main()
