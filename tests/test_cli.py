from __future__ import annotations

import contextlib
import io
import os
import unittest
from unittest.mock import patch

import main as cli


class CliTests(unittest.TestCase):
    def test_run_accepts_multiple_rags_and_question_limit(self) -> None:
        args = cli.build_parser().parse_args(
            ["run", "context-rag", "self-rag", "--questions", "10"]
        )
        self.assertEqual(args.projects, ["context-rag", "self-rag"])
        self.assertEqual(args.questions, 10)

    def test_run_all_accepts_limit_alias(self) -> None:
        args = cli.build_parser().parse_args(["run-all", "--limit", "5"])
        self.assertEqual(args.questions, 5)

    def test_project_runner_does_not_forward_parent_virtualenv(self) -> None:
        completed = type("Completed", (), {"returncode": 0})()
        with (
            patch.dict(os.environ, {"VIRTUAL_ENV": "/tmp/parent-venv"}, clear=False),
            patch.object(cli.subprocess, "run", return_value=completed) as run,
        ):
            self.assertEqual(cli.run_project("context-rag", None, 1), 0)

        child_environment = run.call_args.kwargs["env"]
        self.assertNotIn("VIRTUAL_ENV", child_environment)

    def test_question_limit_rejects_zero(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["run", "context-rag", "--questions", "0"])

    def test_all_cannot_be_combined_with_another_rag(self) -> None:
        with self.assertRaisesRegex(ValueError, "usado sozinho"):
            cli.resolve_projects(["all", "context-rag"])

    def test_web_commands_are_not_available(self) -> None:
        parser = cli.build_parser()
        for removed_command in ("dashboard", "api"):
            with (
                self.subTest(command=removed_command),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                parser.parse_args([removed_command])


if __name__ == "__main__":
    unittest.main()
