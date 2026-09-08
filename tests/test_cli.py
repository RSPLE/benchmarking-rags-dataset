from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import main as cli


class CliTests(unittest.TestCase):
    def test_prepare_corpus_copies_pdfs_to_all_pipelines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "apostila.pdf").write_bytes(b"pdf")
            targets = {
                "first": root / "first",
                "second": root / "second",
            }
            original_targets = cli.CORPUS_TARGETS
            cli.CORPUS_TARGETS = targets
            self.addCleanup(setattr, cli, "CORPUS_TARGETS", original_targets)

            self.assertEqual(cli.prepare_corpus(source), 1)
            for target in targets.values():
                self.assertEqual((target / "apostila.pdf").read_bytes(), b"pdf")

    def test_run_accepts_multiple_rags_and_question_limit(self) -> None:
        args = cli.build_parser().parse_args(
            ["run", "context-rag", "self-rag", "--questions", "10"]
        )
        self.assertEqual(args.projects, ["context-rag", "self-rag"])
        self.assertEqual(args.questions, 10)

    def test_run_all_accepts_limit_alias(self) -> None:
        args = cli.build_parser().parse_args(["run-all", "--limit", "5"])
        self.assertEqual(args.questions, 5)

    def test_run_accepts_corpus_options(self) -> None:
        args = cli.build_parser().parse_args(
            ["run", "all", "--corpus", "/tmp/pdfs", "--clean-corpus"]
        )
        self.assertEqual(args.corpus, Path("/tmp/pdfs"))
        self.assertTrue(args.clean_corpus)

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
