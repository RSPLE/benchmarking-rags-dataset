from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.dashboard.auth import (
    authenticate,
    bootstrap_account,
    valid_session,
)
from app.dashboard.config import Settings
from app.dashboard.database import backup_database, connect
from app.dashboard.server import main as server_main


class DashboardAccessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "state" / "app.dashboard.sqlite3"
        self.password = "local-test-password-only"

    def test_no_account_refuses_startup_before_launching_server(self):
        settings = Settings(self.database, self.root, self.root, self.root)
        with (
            patch("app.dashboard.server.Settings.from_environment", return_value=settings),
            patch("app.dashboard.server.os.execv") as execute,
            patch("sys.argv", ["app.dashboard.server"]),
            self.assertRaises(SystemExit) as stopped,
        ):
            server_main()
        self.assertEqual(stopped.exception.code, 1)
        execute.assert_not_called()

    def test_partial_or_malformed_configuration_never_creates_account(self):
        for username, password in [
            ("tester", ""),
            ("", self.password),
            ("tester", "short"),
            ("invalid user", self.password),
        ]:
            with self.subTest(username=username, configured=bool(password)):
                with self.assertRaises(ValueError):
                    bootstrap_account(self.database, username, password)
        with connect(self.database) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM users").fetchone()[0], 0)

    def test_account_is_created_before_server_exec(self):
        settings = Settings(
            self.database,
            self.root,
            self.root,
            self.root,
            username="tester",
            password=self.password,
        )
        with (
            patch("app.dashboard.server.Settings.from_environment", return_value=settings),
            patch("app.dashboard.server.os.execv") as execute,
            patch("app.dashboard.server.os.chdir"),
            patch("sys.argv", ["app.dashboard.server"]),
        ):
            server_main()
        execute.assert_called_once()
        self.assertIsNotNone(authenticate(self.database, "tester", self.password))
        self.assertIn("--server.address=127.0.0.1", execute.call_args.args[1])
        self.assertNotIn(self.password, repr(settings))

    def test_restart_preserves_password_session_and_lockout(self):
        bootstrap_account(self.database, "tester", self.password)
        session = authenticate(self.database, "tester", self.password)
        bootstrap_account(self.database, "tester", self.password)
        self.assertTrue(valid_session(self.database, session, 3600))
        for _ in range(5):
            self.assertIsNone(authenticate(self.database, "tester", "wrong-password"))
        with connect(self.database) as db:
            before = dict(db.execute("SELECT * FROM users").fetchone())
        bootstrap_account(self.database, "tester", self.password)
        with connect(self.database) as db:
            after = dict(db.execute("SELECT * FROM users").fetchone())
        self.assertEqual(before, after)
        self.assertIsNone(authenticate(self.database, "tester", self.password))

    def test_password_change_revokes_sessions_without_removing_results(self):
        bootstrap_account(self.database, "tester", self.password)
        session = authenticate(self.database, "tester", self.password)
        with connect(self.database) as db, db:
            db.execute("INSERT INTO metadata VALUES ('retained', 'result-marker')")
        replacement = "replacement-password-only"
        bootstrap_account(self.database, "tester", replacement)
        self.assertFalse(valid_session(self.database, session, 3600))
        self.assertIsNone(authenticate(self.database, "tester", self.password))
        self.assertIsNotNone(authenticate(self.database, "tester", replacement))
        with connect(self.database) as db:
            self.assertEqual(
                db.execute("SELECT value FROM metadata").fetchone()[0], "result-marker"
            )

    def test_backup_preserves_authentication_without_plaintext(self):
        bootstrap_account(self.database, "tester", self.password)
        backup = self.root / "backup.sqlite3"
        backup_database(self.database, backup)
        self.assertNotIn(self.password.encode(), backup.read_bytes())
        self.assertIsNotNone(authenticate(backup, "tester", self.password))

    def test_env_password_is_not_interpolated_or_exposed_in_repr(self):
        password = "literal-${MISSING}-password"
        (self.root / ".env").write_text(
            f"DASHBOARD_USERNAME=tester\nDASHBOARD_PASSWORD='{password}'\n"
        )
        with patch.dict(os.environ, {}, clear=True), patch("app.dashboard.config.ROOT", self.root):
            settings = Settings.from_environment()
        self.assertEqual(settings.username, "tester")
        self.assertEqual(settings.password, password)
        self.assertNotIn(password, repr(settings))
        bootstrap_account(self.database, settings.username, settings.password)
        self.assertIsNotNone(authenticate(self.database, "tester", password))


if __name__ == "__main__":
    unittest.main()
