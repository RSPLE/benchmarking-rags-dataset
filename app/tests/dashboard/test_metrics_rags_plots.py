from __future__ import annotations

import unittest

import pandas as pd

from app.plots.metrics_rags import (
    CHARTS,
    PlotInputError,
    chart,
    confidence_interval_95,
    prepare_results,
    question_legend,
    render,
)


def result_frame():
    projects = (
        "context-rag",
        "graph-rag",
        "hybrid-rag",
        "knowledge-enhanced-rag",
        "memory-augmented-rag",
        "self-rag",
    )
    rows = []
    for project_index, project in enumerate(projects):
        for run in range(2):
            for question_index in range(3):
                rows.append(
                    {
                        "project": project,
                        "question": f"Question {question_index + 1}",
                        "faithfulness": 0.70 + project_index / 100 + run / 100,
                        "answer_relevancy": 0.75 + question_index / 100,
                        "context_precision": 0.80 + run / 100,
                        "context_recall": 0.65 + question_index / 100,
                        "answer_response_time_seconds": 2 + project_index + run / 10,
                        "answer_total_tokens": 1000 + project_index * 100 + run * 10,
                    }
                )
    return pd.DataFrame(rows)


class MetricsRagsPlotTests(unittest.TestCase):
    def test_catalog_matches_reference_collection(self):
        self.assertEqual(len(CHARTS), 9)
        self.assertEqual(chart("efficiency").stem("pt"), "01_tempo_raciocinio_tokens_vs_tempo")
        self.assertEqual(chart("faithfulness").title("pt"), "Fidelidade")
        self.assertEqual(
            chart("faithfulness_by_question").title("pt"), "Fidelidade por questão"
        )
        self.assertEqual(chart("faithfulness").title("en"), "Faithfulness")
        self.assertEqual(
            chart("context_recall_by_question").stem("en"),
            "09_barras_context_recall_por_questao_en",
        )

    def test_preparation_preserves_model_and_question_order(self):
        prepared = prepare_results(result_frame())
        self.assertEqual(
            list(prepared["rag_model"].cat.categories),
            [
                "Context RAG",
                "Graph RAG",
                "Hybrid RAG",
                "Knowledge-Enhanced RAG",
                "Memory-Augmented RAG",
                "Self-RAG",
            ],
        )
        self.assertEqual(question_legend(result_frame())["question_id"].tolist(), ["Q1", "Q2", "Q3"])

    def test_confidence_interval_matches_reference_formula(self):
        self.assertAlmostEqual(
            confidence_interval_95(pd.Series([1.0, 2.0, 3.0])),
            4.303 / 3**0.5,
            places=6,
        )

    def test_all_charts_render_png_and_eps_in_both_languages(self):
        frame = result_frame()
        for language in ("pt", "en"):
            for specification in CHARTS:
                with self.subTest(language=language, chart=specification.key):
                    png, eps, stem = render(
                        specification.key,
                        frame,
                        language,
                        ["Question 1", "Question 2", "Question 3"],
                    )
                    self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
                    self.assertTrue(eps.startswith(b"%!PS-Adobe"))
                    self.assertGreater(len(png), 20_000)
                    self.assertGreater(len(eps), 20_000)
                    self.assertEqual(stem.endswith("_en"), language == "en")

    def test_rejects_unknown_project_and_incomplete_input(self):
        frame = result_frame()
        frame.loc[0, "project"] = "other-rag"
        with self.assertRaisesRegex(PlotInputError, "other-rag"):
            prepare_results(frame)
        with self.assertRaisesRegex(PlotInputError, "Missing result columns"):
            prepare_results(pd.DataFrame({"project": ["context-rag"]}))

    def test_normalizes_floating_point_noise_at_metric_boundaries(self):
        frame = result_frame()
        frame.loc[0, "answer_relevancy"] = 1.0000000000000002
        prepared = prepare_results(frame)
        self.assertEqual(prepared.loc[0, "answer_relevancy"], 1.0)


if __name__ == "__main__":
    unittest.main()
