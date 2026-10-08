from __future__ import annotations

import argparse
import glob
import os
from typing import List

import matplotlib.pyplot as plt
import pandas as pd

METRIC_COLS = [
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
]


def find_result_files(results_dir: str = "results") -> List[str]:
    pattern = os.path.join(results_dir, "*-rag-run-*.csv")
    files = sorted(glob.glob(pattern))
    return files


def read_mean_metrics(csv_path: str) -> pd.Series:
    try:
        df = pd.read_csv(csv_path, sep=";", encoding="utf-8-sig")
    except Exception:
        df = pd.read_csv(csv_path, sep=";", encoding="latin-1")

    df.columns = [c.strip() for c in df.columns]
    cols_lower = {c.lower(): c for c in df.columns}

    found = {}
    for m in METRIC_COLS:
        if m not in cols_lower:
            raise ValueError(f"Missing metric {m} in {csv_path}")
        values = pd.to_numeric(df[cols_lower[m]], errors="raise")
        lower = -1 if m == "answer_relevancy" else 0
        if values.empty or values.isna().any() or not values.between(lower - 1e-9, 1 + 1e-9).all():
            raise ValueError(f"Invalid or missing values for {m} in {csv_path}")
        found[m] = values.mean()

    return pd.Series(found)


def aggregate_means(files: List[str]) -> pd.DataFrame:
    rows = []
    names = []
    for f in files:
        s = read_mean_metrics(f)
        if s.empty:
            continue
        rows.append(s)
        names.append(os.path.basename(f))

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, index=names)
    return df


def build_overall_figure(df_means: pd.DataFrame):
    overall = df_means.mean(axis=0)

    plt.style.use("seaborn-v0_8")
    fig, ax = plt.subplots(figsize=(8, 5))

    labels = [
        "Fidedignidade",
        "Relevância da resposta",
        "Precisão do contexto",
        "Revocação do contexto",
    ]

    values = [overall[m] for m in METRIC_COLS]

    bars = ax.bar(labels, values, color=["#4c72b0", "#55a868", "#c44e52", "#8172b2"])
    lower = -1 if min(values) < 0 else 0
    ax.set_ylim(lower, 1)
    ax.set_ylabel(f"Média ({lower}–1)")
    ax.set_title("Média das métricas RAGAS (agregado em arquivos results/)")

    for rect, v in zip(bars, values, strict=True):
        ax.text(rect.get_x() + rect.get_width() / 2, v + 0.02, f"{v:.3f}", ha="center")

    fig.tight_layout()
    return fig


def plot_overall_mean(df_means: pd.DataFrame, save_path: str) -> None:
    fig = build_overall_figure(df_means)
    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    fig.savefig(save_path, dpi=200)
    plt.close(fig)


def main(results_dir: str = "results", out_name: str = "mean_metrics.png") -> None:
    files = find_result_files(results_dir)
    if not files:
        raise ValueError(f"No result files in {results_dir}: expected *-rag-run-*.csv")

    df = aggregate_means(files)
    if df.empty:
        print("Nenhuma métrica válida encontrada nos arquivos.")
        return

    out_path = os.path.join(results_dir, out_name)
    plot_overall_mean(df, out_path)
    print(f"Gráfico salvo em: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", default="results")
    parser.add_argument("--output", default="mean_metrics.png")
    args = parser.parse_args()
    main(args.results_dir, args.output)
