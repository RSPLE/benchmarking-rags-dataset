from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.benchmark.control import Control
from app.runtime_config import (
    PROJECTS,
    enabled_projects,
    install_corpus,
    install_dataset,
    read_runtime_config,
    runtime_environment,
    save_runtime_config,
)


class RuntimeConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "settings.json"

    def test_configuration_is_atomic_private_and_overrides_static_environment(self):
        saved = save_runtime_config(
            self.path,
            {
                "OPENROUTER_API_KEY": "secret-value",
                "LLM_MAX_TOKENS": "8192",
                "TELEGRAM_ENABLED": "true",
            },
            ["context-rag", "hybrid-rag"],
            editor="tester",
        )
        self.assertEqual(saved, read_runtime_config(self.path))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        merged = runtime_environment(
            {"LLM_MAX_TOKENS": "100", "UNRELATED": "preserved"}, self.path
        )
        self.assertEqual(merged["LLM_MAX_TOKENS"], "8192")
        self.assertEqual(merged["OPENROUTER_API_KEY"], "secret-value")
        self.assertEqual(merged["UNRELATED"], "preserved")
        self.assertEqual(enabled_projects(self.path), ("context-rag", "hybrid-rag"))

    def test_invalid_fields_projects_and_paths_are_rejected(self):
        invalid = (
            ({"UNKNOWN": "x"}, list(PROJECTS)),
            ({"LLM_MAX_TOKENS": "0"}, list(PROJECTS)),
            ({"DOCS_DIR": "../documents"}, list(PROJECTS)),
            ({}, ["not-a-rag"]),
        )
        for environment, projects in invalid:
            with self.subTest(environment=environment, projects=projects):
                with self.assertRaises(ValueError):
                    save_runtime_config(
                        self.path, environment, projects, editor="tester"
                    )

    def test_dataset_is_validated_and_versioned_by_content(self):
        content = json.dumps(
            [{"id": "Q001", "question": "Pergunta?", "ground_truth": "Resposta."}]
        ).encode()
        metadata = install_dataset(self.root, "questions.json", content)
        self.assertEqual(metadata["questions"], 1)
        self.assertTrue(Path(metadata["path"]).is_file())
        duplicate = json.dumps(
            [
                {"id": "Q001", "question": "A", "ground_truth": "B"},
                {"id": "Q001", "question": "C", "ground_truth": "D"},
            ]
        ).encode()
        with self.assertRaisesRegex(ValueError, "duplicado"):
            install_dataset(self.root, "duplicate.json", duplicate)

    def test_pdf_corpus_rejects_spoofing_and_preserves_exact_bytes(self):
        first = b"%PDF-1.7\nfirst"
        second = b"%PDF-1.7\nsecond"
        metadata = install_corpus(
            self.root, [("Documento 1.pdf", first), ("documento-2.PDF", second)]
        )
        directory = Path(metadata["path"])
        self.assertEqual((directory / "Documento 1.pdf").read_bytes(), first)
        self.assertEqual(len(metadata["files"]), 2)
        with self.assertRaises(ValueError):
            install_corpus(self.root, [("fake.pdf", b"not a pdf")])
        with self.assertRaises(ValueError):
            install_corpus(self.root, [("../escape.pdf", first)])

    def test_controller_filters_all_and_rejects_an_explicit_disabled_rag(self):
        save_runtime_config(
            self.path, {}, ["context-rag"], editor="tester"
        )
        with patch.dict(os.environ, {"BENCHMARK_RUNTIME_CONFIG": str(self.path)}, clear=True):
            control = Control(
                self.root / "results",
                self.root / "queue.db",
                {123},
                enabled=True,
                profiles={
                    "context-rag": {"mode": "full"},
                    "graph-rag": {"mode": "full"},
                },
            )
            self.addCleanup(control.db.close)
            with self.assertRaisesRegex(ValueError, "desabilitado"):
                control.handle(
                    {
                        "user_id": 123,
                        "command_id": "disabled",
                        "text": "/executar graph-rag --questions 1",
                    }
                )
            response = control.handle(
                {"user_id": 123, "command_id": "all", "text": "/executar all --questions 1"}
            )
            self.assertEqual([job["project"] for job in response["jobs"]], ["context-rag"])


if __name__ == "__main__":
    unittest.main()
