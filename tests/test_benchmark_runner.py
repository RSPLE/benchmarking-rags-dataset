from __future__ import annotations

import csv
import json
import shutil
import unittest
from pathlib import Path

from benchmark_runner import run_resumable_benchmark


class BenchmarkRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / ".runtime" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.dataset_path = self.root / "dataset.json"
        self.output_dir = self.root / "results"
        dataset = [
            {
                "id": f"Q{index:03d}",
                "question": f"Pergunta {index}",
                "ground_truth": f"Resposta {index}",
                "source_book": "Livro",
            }
            for index in range(1, 6)
        ]
        self.dataset_path.write_text(
            json.dumps(dataset, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def answer(question: dict[str, object]) -> dict[str, object]:
        return {
            "answer": f"Resposta gerada para {question['id']}",
            "contexts": ["contexto"],
        }

    @staticmethod
    def evaluate(_item: dict[str, object]) -> dict[str, float]:
        return {"faithfulness": 0.9}

    def run_benchmark(self, *, question_limit: int | None = None, answer=None):
        return run_resumable_benchmark(
            "test-rag",
            answer or self.answer,
            self.evaluate,
            dataset_path=self.dataset_path,
            output_dir=self.output_dir,
            question_limit=question_limit,
        )

    def csv_rows(self) -> list[dict[str, str]]:
        with (self.output_dir / "results.csv").open(encoding="utf-8-sig", newline="") as file:
            return list(csv.DictReader(file, delimiter=";"))

    def errors(self) -> dict[str, object]:
        return json.loads((self.output_dir / "errors.json").read_text(encoding="utf-8"))

    def test_limited_runs_append_successes_without_duplicates(self) -> None:
        first = self.run_benchmark(question_limit=2)
        self.assertEqual(first["attempted"], 2)
        self.assertEqual(first["run_success"], 2)
        self.assertEqual(first["pending"], 3)
        self.assertEqual([row["id"] for row in self.csv_rows()], ["Q001", "Q002"])

        second = self.run_benchmark(question_limit=2)
        self.assertEqual(second["attempted"], 2)
        self.assertEqual(second["success"], 4)
        self.assertEqual(
            [row["id"] for row in self.csv_rows()],
            ["Q001", "Q002", "Q003", "Q004"],
        )

    def test_error_report_is_created_and_cleared_after_retry(self) -> None:
        self.run_benchmark(question_limit=4)

        def fail_last(question: dict[str, object]) -> dict[str, object]:
            raise RuntimeError(f"falha simulada em {question['id']}")

        failed = self.run_benchmark(question_limit=1, answer=fail_last)
        self.assertEqual(failed["run_failed"], 1)
        report = self.errors()
        self.assertEqual(report["count"], 1)
        error = report["errors"][0]
        self.assertEqual(error["id"], "Q005")
        self.assertEqual(error["error_type"], "RuntimeError")
        self.assertEqual(error["message"], "falha simulada em Q005")
        self.assertIn("RuntimeError: falha simulada em Q005", error["output"])
        self.assertIn("Traceback", error["traceback"])
        self.assertEqual(len(self.csv_rows()), 4)

        recovered = self.run_benchmark(question_limit=1)
        self.assertEqual(recovered["run_success"], 1)
        self.assertEqual(recovered["success"], 5)
        self.assertEqual(self.errors()["count"], 0)
        self.assertEqual(len(self.csv_rows()), 5)

    def test_failed_questions_are_retried_before_pending_questions(self) -> None:
        def fail_second(question: dict[str, object]) -> dict[str, object]:
            if question["id"] == "Q002":
                raise RuntimeError("falha temporaria")
            return self.answer(question)

        first = self.run_benchmark(question_limit=2, answer=fail_second)
        self.assertEqual(first["success"], 1)
        self.assertEqual(first["failed"], 1)
        self.assertEqual(first["pending"], 3)

        second = self.run_benchmark(question_limit=1)
        self.assertEqual(second["run_success"], 1)
        self.assertEqual([row["id"] for row in self.csv_rows()], ["Q001", "Q002"])
        self.assertEqual(second["pending"], 3)

    def test_limit_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "inteiro positivo"):
            self.run_benchmark(question_limit=0)


if __name__ == "__main__":
    unittest.main()
