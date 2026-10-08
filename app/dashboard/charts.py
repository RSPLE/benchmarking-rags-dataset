from __future__ import annotations

import copy
import importlib.util
import io
import threading
from functools import lru_cache

import matplotlib

matplotlib.use("Agg")

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
from matplotlib.collections import Collection
from matplotlib.patches import Patch

from app.dashboard.config import ROOT
from app.dashboard.legacy_plots import graph_figure, scatter_figure

LOCK = threading.RLock()


@lru_cache(maxsize=1)
def original_module():
    spec = importlib.util.spec_from_file_location(
        "original_rag_plot", ROOT / "app/rags/context-rag/plot_graph.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def original_csv_chart(frame):
    metrics = original_module().METRIC_COLS
    means = frame.groupby("experiment_id")[metrics].mean()
    return original_module().build_overall_figure(means)


def bar_chart(frame, value, *, title, ylabel, category="project"):
    with plt.rc_context({"font.size": 10}):
        figure, axes = plt.subplots(figsize=(9, 5))
        data = frame.dropna(subset=[value])
        bars = axes.bar(data[category], data[value], color="#237A73", width=0.58)
        axes.set_title(title, loc="left", pad=18, fontweight="bold")
        axes.set_ylabel(ylabel)
        axes.tick_params(axis="x", labelrotation=25)
        axes.spines[["top", "right"]].set_visible(False)
        axes.grid(axis="y", color="#e4e9e8")
        axes.set_axisbelow(True)
        axes.bar_label(bars, fmt="%.1f", padding=4, fontsize=9)
        axes.margins(y=0.2)
        figure.tight_layout()
        return figure


def format_exports(figure):
    png, eps = io.BytesIO(), io.BytesIO()
    figure.savefig(png, format="png", dpi=200)
    vector = copy.deepcopy(figure)
    for artist in vector.findobj():
        alpha = artist.get_alpha() if hasattr(artist, "get_alpha") else None
        if alpha is not None and alpha < 1:
            if isinstance(artist, Patch):
                color = mcolors.to_rgba(artist.get_facecolor())
                artist.set_facecolor(tuple(alpha * v + 1 - alpha for v in color[:3]))
            elif isinstance(artist, Collection):
                colors = artist.get_facecolors().copy()
                if len(colors):
                    colors[:, :3] = alpha * colors[:, :3] + 1 - alpha
                    colors[:, 3] = 1
                    artist.set_facecolors(colors)
            elif hasattr(artist, "get_color") and hasattr(artist, "set_color"):
                color = mcolors.to_rgba(artist.get_color())
                artist.set_color(tuple(alpha * v + 1 - alpha for v in color[:3]))
            artist.set_alpha(1)
    vector.savefig(eps, format="eps", dpi=200)
    plt.close(vector)
    return png.getvalue(), eps.getvalue()


def render(kind, frame, title="", metric=None):
    with LOCK, plt.rc_context():
        if kind == "original":
            figure = original_csv_chart(frame)
        elif kind == "scatter":
            figure = scatter_figure(frame)
        elif kind == "graph":
            figure = graph_figure(frame, title)
        else:
            figure = bar_chart(
                frame, metric, title=title, ylabel="Tokens" if metric == "tokens" else "Segundos"
            )
        try:
            return format_exports(figure)
        finally:
            plt.close(figure)
