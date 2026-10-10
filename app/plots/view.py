from __future__ import annotations

import pandas as pd
import streamlit as st

from app.plots.metrics_rags import CHARTS, PlotInputError, chart, question_legend, render


def plots_view(frame: pd.DataFrame) -> None:
    st.subheader("Coleção metrics-rags")
    st.caption(
        "Reprodução dinâmica dos gráficos do repositório RSPLE/metrics-rags, "
        "com a mesma metodologia, paleta, IC de 95% e tipografia."
    )
    controls, preview = st.columns([1, 2.2], gap="large")
    with controls:
        language_label = st.radio("Idioma", ["Português", "English"], horizontal=True)
        language = "en" if language_label == "English" else "pt"
        selected_key = st.selectbox(
            "Gráfico",
            [item.key for item in CHARTS],
            format_func=lambda key: chart(key).title(language),
        )
        specification = chart(selected_key)
        questions = None
        if specification.scope == "question":
            available = list(dict.fromkeys(frame.get("question", pd.Series(dtype=str)).astype(str)))
            questions = st.multiselect(
                "Questões",
                available,
                default=available[:10],
                format_func=lambda value: (
                    f"Q{available.index(value) + 1} · {value}"
                    if value in available
                    else value
                ),
            )
            st.caption("O original usa dez questões; o recorte inicial preserva essa composição.")
    if not questions and specification.scope == "question":
        preview.info("Selecione pelo menos uma questão para gerar o gráfico.")
        return
    try:
        png, eps, stem = render(selected_key, frame, language, questions)
    except PlotInputError as exc:
        preview.warning(f"Não foi possível gerar este gráfico: {exc}")
        return
    with preview:
        st.image(png, width="stretch")
        left, right = st.columns(2)
        left.download_button(
            "Baixar PNG",
            png,
            stem + ".png",
            "image/png",
            key="metrics-rags-" + stem + "-png",
            on_click="ignore",
            width="stretch",
        )
        right.download_button(
            "Baixar EPS",
            eps,
            stem + ".eps",
            "application/postscript",
            key="metrics-rags-" + stem + "-eps",
            on_click="ignore",
            width="stretch",
        )
    if specification.scope == "question":
        with st.expander("Legenda das questões"):
            st.dataframe(question_legend(frame, questions), hide_index=True, width="stretch")
