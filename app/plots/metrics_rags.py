from __future__ import annotations

import io
import threading
from dataclasses import dataclass

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Chart:
    key: str
    index: int
    metric: str | None
    scope: str
    title_pt: str
    title_en: str

    def title(self, language: str) -> str:
        return self.title_en if language == "en" else self.title_pt

    def stem(self, language: str) -> str:
        if self.scope == "efficiency":
            base = "01_tempo_raciocinio_tokens_vs_tempo"
        elif self.scope == "overall":
            base = f"{self.index:02d}_barras_{self.metric}"
        else:
            base = f"{self.index:02d}_barras_{self.metric}_por_questao"
        return base + ("_en" if language == "en" else "")


CHARTS = (
    Chart(
        "efficiency",
        1,
        None,
        "efficiency",
        "Tokens e tempo de raciocínio",
        "Tokens and reasoning time",
    ),
    Chart("faithfulness", 2, "faithfulness", "overall", "Fidelidade", "Faithfulness"),
    Chart(
        "answer_relevancy",
        3,
        "answer_relevancy",
        "overall",
        "Relevância da resposta",
        "Answer Relevancy",
    ),
    Chart(
        "context_precision",
        4,
        "context_precision",
        "overall",
        "Precisão do contexto",
        "Context Precision",
    ),
    Chart(
        "context_recall",
        5,
        "context_recall",
        "overall",
        "Revocação do contexto",
        "Context Recall",
    ),
    Chart(
        "faithfulness_by_question",
        6,
        "faithfulness",
        "question",
        "Fidelidade por questão",
        "Faithfulness by question",
    ),
    Chart(
        "answer_relevancy_by_question",
        7,
        "answer_relevancy",
        "question",
        "Relevância da resposta por questão",
        "Answer Relevancy by question",
    ),
    Chart(
        "context_precision_by_question",
        8,
        "context_precision",
        "question",
        "Precisão do contexto por questão",
        "Context Precision by question",
    ),
    Chart(
        "context_recall_by_question",
        9,
        "context_recall",
        "question",
        "Revocação do contexto por questão",
        "Context Recall by question",
    ),
)

MODEL_ORDER = (
    ("context-rag", "Context RAG", "CR"),
    ("graph-rag", "Graph RAG", "GR"),
    ("hybrid-rag", "Hybrid RAG", "HR"),
    ("knowledge-enhanced-rag", "Knowledge-Enhanced RAG", "KER"),
    ("memory-augmented-rag", "Memory-Augmented RAG", "MAR"),
    ("self-rag", "Self-RAG", "SR"),
)
PROJECT_ALIASES = {
    alias: label
    for slug, label, _ in MODEL_ORDER
    for alias in (slug, label, slug.replace("-", " "), label.lower())
}
MODEL_ABBREVIATIONS = {label: abbreviation for _, label, abbreviation in MODEL_ORDER}
PALETTE = {
    "Context RAG": "#8BC7C9",
    "Graph RAG": "#E8A19B",
    "Hybrid RAG": "#A9C8E8",
    "Knowledge-Enhanced RAG": "#D7B98D",
    "Memory-Augmented RAG": "#BBBCE8",
    "Self-RAG": "#9FD3B4",
}
QUALITY_METRICS = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)
REQUIRED_COLUMNS = (
    "project",
    "question",
    *QUALITY_METRICS,
    "answer_response_time_seconds",
    "answer_total_tokens",
)
RC_PARAMS = {
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
    "savefig.edgecolor": "white",
    "savefig.dpi": 300,
    "font.family": "DejaVu Sans",
    "font.size": 12,
    "axes.titlesize": 12,
    "axes.labelsize": 17,
    "axes.labelcolor": "#000000",
    "axes.titlecolor": "#000000",
    "xtick.labelsize": 14,
    "xtick.color": "#000000",
    "ytick.labelsize": 14,
    "ytick.color": "#000000",
    "legend.fontsize": 14,
    "text.color": "#000000",
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
}
T_CRITICAL_95 = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}
LOCK = threading.RLock()


class PlotInputError(ValueError):
    """The selected result frame cannot produce a metrics-rags plot."""


def chart(key: str) -> Chart:
    try:
        return next(item for item in CHARTS if item.key == key)
    except StopIteration as exc:
        raise PlotInputError(f"Unknown chart: {key}") from exc


def confidence_interval_95(values: pd.Series) -> float:
    clean = values.dropna()
    count = len(clean)
    if count < 2:
        return 0.0
    critical = T_CRITICAL_95.get(count - 1, 1.96)
    return float(critical * clean.std(ddof=1) / np.sqrt(count))


def prepare_results(frame: pd.DataFrame, questions: list[str] | None = None) -> pd.DataFrame:
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise PlotInputError("Missing result columns: " + ", ".join(missing))
    results = frame.loc[:, REQUIRED_COLUMNS].copy()
    results["rag_model"] = results["project"].map(
        lambda value: PROJECT_ALIASES.get(str(value), PROJECT_ALIASES.get(str(value).lower()))
    )
    unknown = sorted(results.loc[results["rag_model"].isna(), "project"].astype(str).unique())
    if unknown:
        raise PlotInputError("Unsupported RAG projects: " + ", ".join(unknown))
    for column in (*QUALITY_METRICS, "answer_response_time_seconds", "answer_total_tokens"):
        results[column] = pd.to_numeric(results[column], errors="coerce")
    if questions is not None:
        results = results[results["question"].isin(questions)].copy()
    if results.empty:
        raise PlotInputError("The selected result set is empty")
    tolerance = 1e-9
    invalid = [
        metric
        for metric in QUALITY_METRICS
        if not results[metric].dropna().between(-tolerance, 1 + tolerance).all()
    ]
    if invalid:
        raise PlotInputError("Metrics outside the 0-1 range: " + ", ".join(invalid))
    results.loc[:, QUALITY_METRICS] = results.loc[:, QUALITY_METRICS].clip(0, 1)
    question_order = list(dict.fromkeys(results["question"].astype(str)))
    question_ids = {question: f"Q{index + 1}" for index, question in enumerate(question_order)}
    results["question_id"] = results["question"].astype(str).map(question_ids)
    present = set(results["rag_model"])
    labels = [label for _, label, _ in MODEL_ORDER if label in present]
    results["rag_model"] = pd.Categorical(results["rag_model"], categories=labels, ordered=True)
    results["question_id"] = pd.Categorical(
        results["question_id"],
        categories=[f"Q{i}" for i in range(1, len(question_order) + 1)],
        ordered=True,
    )
    return results


def question_legend(frame: pd.DataFrame, questions: list[str] | None = None) -> pd.DataFrame:
    results = prepare_results(frame, questions)
    ordered = list(dict.fromkeys(results["question"].astype(str)))
    return pd.DataFrame(
        {"question_id": [f"Q{i + 1}" for i in range(len(ordered))], "question": ordered}
    )


def _labels(results: pd.DataFrame) -> list[str]:
    return list(results["rag_model"].cat.categories)


def _prettify_axes(axis: plt.Axes) -> None:
    axis.grid(axis="y", color="#D9DEE3", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.spines["left"].set_color("#7D8790")
    axis.spines["bottom"].set_color("#7D8790")
    axis.tick_params(colors="#000000")


def _efficiency_figure(results: pd.DataFrame, language: str) -> plt.Figure:
    labels = _labels(results)
    abbreviations = [MODEL_ABBREVIATIONS[label] for label in labels]
    if language == "en":
        figure_title = "Distribution of tokens and reasoning time by RAG"
        tokens_label = "Total tokens"
        time_label = "Response time (s)"
        x_label = "RAG model"
        legend_labels = ["Total tokens", "Response time"]
    else:
        figure_title = "Distribuição de tokens e tempo de raciocínio por RAG"
        tokens_label = "Tokens totais"
        time_label = "Tempo de resposta (s)"
        x_label = "Modelo de RAG"
        legend_labels = ["Tokens totais", "Tempo de resposta"]
    tokens_means = [
        results.loc[results["rag_model"] == label, "answer_total_tokens"].mean()
        for label in labels
    ]
    tokens_cis = [
        confidence_interval_95(
            results.loc[results["rag_model"] == label, "answer_total_tokens"]
        )
        for label in labels
    ]
    time_means = [
        results.loc[results["rag_model"] == label, "answer_response_time_seconds"].mean()
        for label in labels
    ]
    time_cis = [
        confidence_interval_95(
            results.loc[results["rag_model"] == label, "answer_response_time_seconds"]
        )
        for label in labels
    ]
    if not all(np.isfinite(tokens_means + time_means)):
        raise PlotInputError("Token and response-time measurements are required")
    positions = np.arange(1, len(labels) + 1)
    figure, tokens_axis = plt.subplots(figsize=(10.8, 5.4))
    time_axis = tokens_axis.twinx()
    bar_options = {
        "linewidth": 0.5,
        "edgecolor": "#263238",
        "capsize": 3,
        "error_kw": {"ecolor": "#374151", "elinewidth": 0.9, "capthick": 0.9},
    }
    token_bars = tokens_axis.bar(
        positions - 0.18,
        tokens_means,
        width=0.28,
        color="#A9C8E8",
        yerr=tokens_cis,
        **bar_options,
    )
    time_bars = time_axis.bar(
        positions + 0.18,
        time_means,
        width=0.28,
        color="#F0B98C",
        yerr=time_cis,
        **bar_options,
    )
    figure.suptitle(figure_title, fontsize=12)
    tokens_axis.set_ylabel(tokens_label, color="#000000")
    time_axis.set_ylabel(time_label, color="#000000")
    tokens_axis.set_xlabel(x_label)
    tokens_axis.tick_params(axis="y", labelcolor="#000000")
    time_axis.tick_params(axis="y", labelcolor="#000000")
    tokens_axis.set_xticks(positions)
    tokens_axis.tick_params(axis="x", pad=8)
    tokens_axis.set_xticklabels(abbreviations, rotation=0, ha="center")
    tokens_axis.set_xlim(positions[0] - 0.65, positions[-1] + 0.65)
    tokens_axis.set_ylim(
        0,
        max(mean + ci for mean, ci in zip(tokens_means, tokens_cis, strict=True)) * 1.18,
    )
    time_axis.set_ylim(
        0,
        max(mean + ci for mean, ci in zip(time_means, time_cis, strict=True)) * 1.18,
    )
    _prettify_axes(tokens_axis)
    time_axis.spines["top"].set_visible(False)
    time_axis.spines["right"].set_color("#7D8790")
    time_axis.tick_params(colors="#000000")
    time_axis.tick_params(axis="y", labelcolor="#000000")
    tokens_axis.legend(
        [token_bars, time_bars],
        legend_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.14),
        ncol=2,
        frameon=False,
    )
    figure.subplots_adjust(top=0.88, bottom=0.22, left=0.10, right=0.90)
    return figure


def _metric_figure(
    results: pd.DataFrame, metric: str, title: str, language: str
) -> plt.Figure:
    labels = _labels(results)
    means = [results.loc[results["rag_model"] == label, metric].mean() for label in labels]
    intervals = [
        confidence_interval_95(results.loc[results["rag_model"] == label, metric])
        for label in labels
    ]
    if not all(np.isfinite(means)):
        raise PlotInputError(f"Measurements are required for {metric}")
    axis_title = (
        f"Mean {title} by RAG (95% CI)"
        if language == "en"
        else f"Média de {title} por RAG (IC 95%)"
    )
    x = np.arange(len(labels))
    figure, axis = plt.subplots(figsize=(8.8, 4.8))
    axis.bar(
        x,
        means,
        width=0.42,
        color=[PALETTE[label] for label in labels],
        edgecolor="#263238",
        linewidth=0.5,
        yerr=intervals,
        capsize=4,
        error_kw={"ecolor": "#374151", "elinewidth": 1.0, "capthick": 1.0},
    )
    axis.set_title(axis_title)
    axis.set_ylabel(title)
    axis.set_xlabel("RAG model" if language == "en" else "Modelo de RAG")
    axis.set_xticks(x)
    axis.set_xticklabels([MODEL_ABBREVIATIONS[label] for label in labels], rotation=0, ha="center")
    top = max(mean + interval for mean, interval in zip(means, intervals, strict=True))
    axis.set_ylim(0, min(1.12, max(0.6, top * 1.18)))
    _prettify_axes(axis)
    figure.tight_layout()
    return figure


def _question_figure(
    results: pd.DataFrame, metric: str, title: str, language: str
) -> plt.Figure:
    summary = (
        results.groupby(["question_id", "rag_model"], observed=False)[metric]
        .mean()
        .unstack("rag_model")
        .reindex(columns=_labels(results))
    )
    intervals = (
        results.groupby(["question_id", "rag_model"], observed=False)[metric]
        .apply(confidence_interval_95)
        .unstack("rag_model")
        .reindex(index=summary.index, columns=summary.columns)
        .fillna(0)
    )
    if summary.dropna(how="all").empty:
        raise PlotInputError(f"Measurements are required for {metric}")
    axis_title = (
        f"Mean {title} by question and RAG"
        if language == "en"
        else f"Média de {title} por questão e RAG"
    )
    question_labels = summary.index.astype(str).tolist()
    models = summary.columns.tolist()
    x = np.arange(len(question_labels))
    width = 0.12
    offsets = (np.arange(len(models)) - (len(models) - 1) / 2) * width
    figure, axis = plt.subplots(figsize=(11.2, 5.2))
    for model, offset in zip(models, offsets, strict=True):
        axis.bar(
            x + offset,
            summary[model].to_numpy(),
            width=width,
            label=MODEL_ABBREVIATIONS[model],
            color=PALETTE[model],
            edgecolor="#263238",
            linewidth=0.35,
            yerr=intervals[model].to_numpy(),
            capsize=2.2,
            error_kw={"ecolor": "#111827", "elinewidth": 0.85, "capthick": 0.85},
        )
    axis.set_title(axis_title)
    axis.set_ylabel(title)
    axis.set_xlabel("Question" if language == "en" else "Questão")
    axis.set_xticks(x)
    axis.set_xticklabels(question_labels)
    y_max = np.nanmax((summary + intervals).to_numpy())
    axis.set_ylim(0, min(1.18, max(1.08, y_max * 1.08)))
    axis.legend(ncol=3, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.16))
    _prettify_axes(axis)
    figure.tight_layout()
    return figure


def _export(figure: plt.Figure) -> tuple[bytes, bytes]:
    png = io.BytesIO()
    eps = io.BytesIO()
    figure.savefig(png, format="png", dpi=300, bbox_inches="tight")
    figure.savefig(eps, format="eps", dpi=300, bbox_inches="tight")
    return png.getvalue(), eps.getvalue()


def render(
    key: str,
    frame: pd.DataFrame,
    language: str = "pt",
    questions: list[str] | None = None,
) -> tuple[bytes, bytes, str]:
    if language not in {"pt", "en"}:
        raise PlotInputError(f"Unsupported language: {language}")
    specification = chart(key)
    selected_questions = questions if specification.scope == "question" else None
    with LOCK, plt.rc_context(RC_PARAMS):
        results = prepare_results(frame, selected_questions)
        if specification.scope == "efficiency":
            figure = _efficiency_figure(results, language)
        elif specification.scope == "overall":
            figure = _metric_figure(
                results, specification.metric or "", specification.title(language), language
            )
        else:
            figure = _question_figure(
                results,
                specification.metric or "",
                specification.title(language).replace(" por questão", "").replace(
                    " by question", ""
                ),
                language,
            )
        try:
            png, eps = _export(figure)
        finally:
            plt.close(figure)
    return png, eps, specification.stem(language)
