import matplotlib.pyplot as plt
import numpy as np


def graph_figure(frame, title):
    df_results = frame

    metrics = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
    metric_labels = ["Faithfulness", "Answer Relevancy", "Context Precision", "Context Recall"]
    available_metrics = [m for m in metrics if m in df_results.columns]
    available_labels = [metric_labels[i] for i, m in enumerate(metrics) if m in df_results.columns]

    if not available_metrics:
        raise ValueError("Nenhuma métrica esperada encontrada em df_results.")

    plot_df = df_results[available_metrics].apply(lambda col: col.astype(float)).copy()
    mean_scores = plot_df.mean().tolist()
    n_queries = len(plot_df)
    colors = ["#2c3e50", "#3498db", "#e74c3c", "#27ae60"][: len(available_metrics)]
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 11,
            "axes.titlesize": 13,
            "axes.labelsize": 11,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 10,
            "figure.titlesize": 14,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.linewidth": 0.8,
            "grid.linewidth": 0.5,
            "lines.linewidth": 2,
            "lines.markersize": 8,
        }
    )

    fig = plt.figure(figsize=(12, 42))
    fig.suptitle(title, fontsize=16, fontweight="bold", y=0.995)

    x = np.arange(n_queries)

    ax1 = fig.add_subplot(7, 1, 1)
    bars = ax1.bar(
        range(len(available_metrics)),
        mean_scores,
        color=colors,
        edgecolor="black",
        linewidth=1,
        width=0.5,
    )
    ax1.set_title("(a) Métricas Médias", fontweight="bold", pad=12)
    ax1.set_ylabel("Score Médio")
    ax1.set_ylim(0, 1.15)
    ax1.set_xticks(range(len(available_metrics)))
    ax1.set_xticklabels(available_labels)
    ax1.axhline(y=1.0, color="gray", linestyle="--", linewidth=0.5, alpha=0.7)
    ax1.grid(axis="y", linestyle=":", alpha=0.4)
    for bar, score in zip(bars, mean_scores, strict=False):
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            score + 0.03,
            f"{score:.3f}",
            ha="center",
            va="bottom",
            fontsize=11,
            fontweight="bold",
        )

    ax2 = fig.add_subplot(7, 1, 2)
    width = 0.18
    for i, (metric, label) in enumerate(zip(available_metrics, available_labels, strict=False)):
        offset = width * i
        rects = ax2.bar(
            x + offset,
            plot_df[metric].values,
            width,
            label=label,
            color=colors[i],
            edgecolor="black",
            linewidth=0.6,
        )
        for rect, val in zip(rects, plot_df[metric].values, strict=False):
            ax2.text(
                rect.get_x() + rect.get_width() / 2,
                rect.get_height() + 0.02,
                f"{val:.2f}",
                ha="center",
                va="bottom",
                fontsize=9,
                fontweight="bold",
            )
    ax2.set_title("(b) Métricas por Query", fontweight="bold", pad=12)
    ax2.set_xlabel("Query")
    ax2.set_ylabel("Score")
    ax2.set_xticks(x + width * (len(available_metrics) - 1) / 2)
    ax2.set_xticklabels(frame["id"].tolist())
    ax2.set_ylim(0, 1.25)
    ax2.axhline(y=1.0, color="gray", linestyle="--", linewidth=0.5, alpha=0.7)
    ax2.legend(loc="upper right", frameon=True, framealpha=0.95, fontsize=9, ncol=4)
    ax2.grid(axis="y", linestyle=":", alpha=0.4)

    ax3 = fig.add_subplot(7, 1, 3, projection="polar")
    angles = np.linspace(0, 2 * np.pi, len(available_metrics), endpoint=False).tolist()
    angles += angles[:1]
    radar_values = mean_scores + [mean_scores[0]]
    ax3.plot(angles, radar_values, color="#2c3e50", linewidth=2.5, marker="o", markersize=10)
    ax3.fill(angles, radar_values, color="#3498db", alpha=0.25)
    for angle, value, _label in zip(angles[:-1], mean_scores, available_labels, strict=False):
        ax3.annotate(
            f"{value:.3f}",
            xy=(angle, value),
            xytext=(angle, value + 0.18),
            ha="center",
            va="bottom",
            fontsize=11,
            fontweight="bold",
        )
    ax3.set_xticks(angles[:-1])
    ax3.set_xticklabels(available_labels, fontsize=11)
    ax3.set_yticks([0.2, 0.4, 0.6, 0.8, 1.0])
    ax3.set_yticklabels(["0.2", "0.4", "0.6", "0.8", "1.0"], fontsize=9, color="gray")
    ax3.set_ylim(0, 1.15)
    ax3.set_title("(c) Radar de Métricas", fontweight="bold", pad=20, y=1.1)
    ax3.grid(True, linestyle=":", alpha=0.5)

    ax4 = fig.add_subplot(7, 1, 4)
    heatmap_data = plot_df.values
    im = ax4.imshow(heatmap_data, cmap="RdYlGn", aspect="auto", vmin=0, vmax=1)
    ax4.set_title("(d) Heatmap de Scores", fontweight="bold", pad=12)
    ax4.set_xticks(np.arange(len(available_metrics)))
    ax4.set_xticklabels(available_labels)
    ax4.set_yticks(np.arange(n_queries))
    ax4.set_yticklabels(frame["id"].tolist())
    for i in range(n_queries):
        for j in range(len(available_metrics)):
            val = heatmap_data[i, j]
            text_color = "white" if val < 0.4 or val > 0.75 else "black"
            ax4.text(
                j,
                i,
                f"{val:.2f}",
                ha="center",
                va="center",
                color=text_color,
                fontsize=12,
                fontweight="bold",
            )
    cbar = fig.colorbar(im, ax=ax4, fraction=0.03, pad=0.02)
    cbar.set_label("Score", fontsize=11)
    cbar.ax.tick_params(labelsize=10)

    ax5 = fig.add_subplot(7, 1, 5)
    box_data = [plot_df[m].dropna().values for m in available_metrics]
    bp = ax5.boxplot(
        box_data,
        tick_labels=available_labels,
        patch_artist=True,
        showmeans=True,
        meanprops={"marker": "D", "markerfacecolor": "red", "markersize": 8},
    )
    for patch, color in zip(bp["boxes"], colors, strict=False):
        patch.set_facecolor(color)
        patch.set_alpha(0.6)
        patch.set_edgecolor("black")
        patch.set_linewidth(1)
    for i, data in enumerate(box_data):
        mean_val = np.mean(data)
        median_val = np.median(data)
        q1 = np.percentile(data, 25)
        q3 = np.percentile(data, 75)
        ax5.text(
            i + 1,
            q3 + 0.12,
            f"μ={mean_val:.2f}",
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
            color="red",
        )
        ax5.text(
            i + 1,
            q1 - 0.12,
            f"M={median_val:.2f}",
            ha="center",
            va="top",
            fontsize=10,
            color="black",
            fontweight="bold",
        )
    ax5.set_title("(e) Distribuição de Scores", fontweight="bold", pad=12)
    ax5.set_ylabel("Score")
    ax5.set_ylim(-0.3, 1.35)
    ax5.axhline(y=1.0, color="gray", linestyle="--", linewidth=0.5, alpha=0.7)
    ax5.grid(axis="y", linestyle=":", alpha=0.4)

    ax6 = fig.add_subplot(7, 1, 6)
    markers = ["o", "s", "^", "D"]
    for i, (metric, label) in enumerate(zip(available_metrics, available_labels, strict=False)):
        ax6.plot(
            x,
            plot_df[metric],
            marker=markers[i],
            label=label,
            color=colors[i],
            linewidth=2.5,
            markersize=10,
            markeredgecolor="black",
            markeredgewidth=0.8,
        )
        for xi, val in zip(x, plot_df[metric], strict=False):
            ax6.annotate(
                f"{val:.2f}",
                xy=(xi, val),
                xytext=(0, 12),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=10,
                fontweight="bold",
            )
    ax6.set_title("(f) Evolução por Query", fontweight="bold", pad=12)
    ax6.set_xlabel("Query")
    ax6.set_ylabel("Score")
    ax6.set_xticks(x)
    ax6.set_xticklabels(frame["id"].tolist())
    ax6.set_ylim(-0.05, 1.3)
    ax6.axhline(y=1.0, color="gray", linestyle="--", linewidth=0.5, alpha=0.7)
    ax6.legend(loc="lower right", frameon=True, framealpha=0.95, fontsize=10, ncol=2)
    ax6.grid(linestyle=":", alpha=0.4)

    ax7 = fig.add_subplot(7, 1, 7)
    ax7.axis("off")
    desc = plot_df.describe().loc[["mean", "std", "min", "max"]].round(3)
    table_data = [["Métrica", "Média (μ)", "Desvio (σ)", "Mínimo", "Máximo"]]
    for m, label in zip(available_metrics, available_labels, strict=False):
        table_data.append(
            [
                label,
                f"{desc.loc['mean', m]:.3f}",
                f"{desc.loc['std', m]:.3f}",
                f"{desc.loc['min', m]:.3f}",
                f"{desc.loc['max', m]:.3f}",
            ]
        )
    table = ax7.table(
        cellText=table_data[1:],
        colLabels=table_data[0],
        loc="center",
        cellLoc="center",
        colColours=["#f0f0f0"] * 5,
        cellColours=[["white"] * 5 for _ in range(len(available_metrics))],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1.2, 2)
    for i in range(5):
        table[(0, i)].set_text_props(fontweight="bold")
        table[(0, i)].set_facecolor("#d0d0d0")
    ax7.set_title("(g) Resumo Estatístico", fontweight="bold", pad=12, y=0.95)

    plt.tight_layout(rect=[0, 0, 1, 0.99])
    return fig


def scatter_figure(frame):
    df = frame.rename(
        columns={"project": "RAG", "mean_tokens": "Avg Tokens", "mean_seconds": "Avg Latência (s)"}
    )
    COLORS = ["#2c3e50", "#3498db", "#e74c3c", "#27ae60", "#f39c12", "#9b59b6"]

    df_plot = df.dropna(subset=["Avg Tokens", "Avg Latência (s)"])

    if df_plot.empty:
        raise ValueError("No complete token/time measurements")
    else:
        fig, ax = plt.subplots(figsize=(11, 7))

        for i, row in df_plot.reset_index(drop=True).iterrows():
            color = COLORS[i % len(COLORS)]
            ax.scatter(
                row["Avg Latência (s)"],
                row["Avg Tokens"],
                s=220,
                color=color,
                zorder=5,
                edgecolors="white",
                linewidths=1.5,
            )
            ax.annotate(
                row["RAG"],
                xy=(row["Avg Latência (s)"], row["Avg Tokens"]),
                xytext=(10, 5),
                textcoords="offset points",
                fontsize=10,
                color=color,
                fontweight="bold",
            )

        if len(df_plot) > 1:
            ax.axvline(
                df_plot["Avg Latência (s)"].median(),
                color="gray",
                linestyle=":",
                alpha=0.5,
                label="Mediana latência",
            )
            ax.axhline(
                df_plot["Avg Tokens"].median(),
                color="gray",
                linestyle="--",
                alpha=0.5,
                label="Mediana tokens",
            )
            ax.legend(fontsize=9, loc="upper left")

        ax.set_xlabel("Latência Média por Query (segundos)", fontsize=12)
        ax.set_ylabel("Tokens Médios por Query", fontsize=12)
        ax.set_title(
            "Benchmark RAG: Consumo de Tokens × Latência\n"
            "(canto inferior esquerdo = mais eficiente)",
            fontsize=14,
            fontweight="bold",
        )
        ax.grid(True, linestyle="--", alpha=0.4)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        plt.tight_layout()
        return fig
