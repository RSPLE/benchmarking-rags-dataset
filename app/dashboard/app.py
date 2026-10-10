from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

from app.dashboard.analytics import common_questions, experiments, frames
from app.dashboard.config import Settings
from app.dashboard.database import connect
from app.dashboard.downloads import downloads_view
from app.dashboard.execution import execution_view, pending_view
from app.dashboard.judge import judge_view
from app.dashboard.questions import question_results_view
from app.dashboard.sessions import COOKIE_NAME, csrf_token, session_identity
from app.plots.view import plots_view

st.set_page_config(
    page_title="Observatório RAG · LogiBots",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="auto",
)
settings = Settings.from_environment()
SECTIONS = [
    "Executar e retomar",
    "Pendências e falhas",
    "Resultados por questão",
    "Baixar dados",
    "Juiz e reavaliação",
    "Gráficos",
]
st.markdown(
    """<style>
.block-container {max-width: 1320px; padding: 2.6rem 2.3rem 3rem;}
h1 {letter-spacing: -.035em; font-weight: 650;}
h2,h3 {letter-spacing: -.02em;}
[data-testid="stMetric"] {border: 1px solid rgba(130,148,172,.24); border-radius: 12px; padding: 1rem 1.2rem;}
[data-testid="stMetricValue"] {font-variant-numeric: tabular-nums;}
.eyebrow {opacity: .75; font-size: .72rem; font-weight: 700; letter-spacing: .14em; margin: 0;}
[data-testid="stButton"] button,[data-testid="stDownloadButton"] button {min-height: 44px;}
[data-testid="stSidebar"] [role="radiogroup"] {gap: .3rem;}
[data-testid="stSidebar"] [role="radiogroup"] label {padding: .45rem .5rem; min-height:44px; border-radius:8px;}
.session-exit button {font:inherit; color:inherit; background:transparent; width:100%; border:1px solid rgba(130,148,172,.4); border-radius:9px; padding:10px 16px; cursor:pointer; min-height:44px;}
.session-exit button:focus-visible {outline:3px solid #60a5fa; outline-offset:3px;}
@media (max-width: 640px) {
 .block-container {padding: 1.3rem 1rem 2rem;}
 h1 {font-size: 1.85rem !important;}
 [data-testid="stHorizontalBlock"] {flex-wrap: wrap; gap: .75rem;}
 [data-testid="stColumn"] {min-width: min(100%, 240px); flex: 1 1 240px !important;}
}
</style>""",
    unsafe_allow_html=True,
)


def login():
    token = st.context.cookies.get(COOKIE_NAME, "")
    identity = session_identity(settings.database, token)
    if identity is None:
        st.session_state.clear()
        st.title("Sua sessão foi encerrada")
        st.write("Entre novamente para acessar os resultados e controlar as execuções.")
        st.link_button("Entrar no painel", "/auth/login", type="primary")
        st.stop()
    return token, identity


def dashboard():
    token, _ = login()
    records = experiments(settings.database)
    section = st.session_state.get("section", SECTIONS[0])
    if section == "Executar e retomar":
        execution_view(settings, token, records)
        return
    if section == "Pendências e falhas":
        pending_view(settings, token, records)
        return
    if section == "Juiz e reavaliação":
        judge_view(settings, token, records)
        return
    with connect(settings.database) as db:
        last = db.execute("SELECT value FROM metadata WHERE key='last_sync'").fetchone()
        problems = [dict(row) for row in db.execute("SELECT * FROM sync_errors")]
    if last:
        elapsed = (datetime.now(UTC) - datetime.fromisoformat(last[0])).total_seconds()
        local_time = datetime.fromisoformat(last[0]).astimezone(ZoneInfo("America/Belem"))
        st.caption(
            f"Atualizado em {local_time:%d/%m/%Y às %H:%M:%S} · Brasília · a cada {settings.poll_seconds}s"
        )
        if elapsed > settings.poll_seconds * 3:
            st.warning(
                "A sincronização está atrasada. Os dados salvos continuam disponíveis; confira o serviço monitor."
            )
    else:
        st.info("Aguardando a primeira sincronização do serviço monitor.")
    if problems:
        st.warning(
            f"{len(problems)} fonte(s) aguardando uma leitura consistente. Os últimos dados válidos foram preservados."
        )
    if section == "Resultados por questão":
        question_results_view(settings.database, records)
        return
    if section == "Baixar dados":
        downloads_view(settings.database, records)
        return
    if not records:
        st.info(
            "Nenhum resultado importado ainda. Assim que um benchmark salvar resultados, eles aparecerão aqui."
        )
        return
    with st.expander("Filtrar experimentos", expanded=False):
        groups = sorted({row["comparison_key"] for row in records})
        selected_group = st.selectbox(
            "Protocolo de comparação",
            groups,
            format_func=lambda key: (
                "Histórico · configuração não registrada"
                if key == "legacy-unknown"
                else "Continuação · resultados anteriores preservados"
                if key.startswith("continuation-")
                else f"Mesmo dataset, corpus e modelos · {key[:12]}"
            ),
        )
        candidates = [row for row in records if row["comparison_key"] == selected_group]
        by_id = {row["id"]: row for row in candidates}
        defaults = []
        seen = set()
        for row in candidates:
            if row["project"] not in seen:
                defaults.append(row["id"])
                seen.add(row["project"])
        chosen = st.multiselect(
            "RAGs e repetições",
            list(by_id),
            default=defaults,
            key=(
                "experiment-selection-"
                + hashlib.sha256("\n".join(by_id).encode()).hexdigest()[:12]
            ),
            format_func=lambda key: (
                f"{by_id[key]['project']} · {by_id[key]['external_id'][:12]} · {by_id[key]['model']}"
            ),
        )
        common = st.checkbox(
            "Comparar somente perguntas com sucesso em todos os experimentos selecionados",
            value=True,
        )
    if not chosen:
        st.info("Selecione pelo menos um experimento.")
        return
    if selected_group == "legacy-unknown":
        st.warning(
            "Os arquivos históricos não registram todos os modelos e parâmetros. A compatibilidade científica entre eles não pode ser confirmada."
        )
    if selected_group.startswith("continuation-"):
        st.info(
            "Os resultados anteriores foram preservados. Seus modelos e contextos não estavam registrados; as questões retomadas usam a configuração atual."
        )
    samples, _, _ = frames(settings.database, chosen)
    successes = samples[samples["status"] == "success"].copy() if not samples.empty else samples
    comparison = common_questions(successes) if common else successes
    a, b, c = st.columns(3)
    a.metric("Experimentos", len(chosen))
    b.metric("Casos com sucesso", len(successes))
    c.metric("Casos no recorte", len(comparison))
    plots_view(comparison)


token, identity = login()
if "navigate_to" in st.session_state:
    st.session_state["section"] = st.session_state.pop("navigate_to")
if st.session_state.get("section") == "Gráficos metrics-rags":
    st.session_state["section"] = "Gráficos"
elif "section" in st.session_state and st.session_state["section"] not in SECTIONS:
    st.session_state["section"] = SECTIONS[0]
if "section" not in st.session_state:
    saved = st.query_params.get("view", SECTIONS[0])
    if saved == "Gráficos metrics-rags":
        saved = "Gráficos"
    st.session_state["section"] = saved if saved in SECTIONS else SECTIONS[0]
with st.sidebar:
    st.markdown('<p class="eyebrow">LOGIBOTS / PESQUISA</p>', unsafe_allow_html=True)
    st.title("Observatório RAG")
    st.caption("Experimentos, consumo e resultados")
    st.divider()
    section = st.radio("Navegação", SECTIONS, key="section", label_visibility="collapsed")
    st.divider()
    st.caption("Tema claro ou escuro no menu ⋮ → Theme. A preferência fica salva no navegador.")
    st.caption(f"Conectado como {identity['username']}")
    st.markdown(
        f'<form class="session-exit" action="/auth/logout" method="post"><input type="hidden" name="csrf" value="{escape(csrf_token(token), quote=True)}"><button type="submit">Sair da conta</button></form>',
        unsafe_allow_html=True,
    )
st.query_params["view"] = section
st.markdown('<p class="eyebrow">AMBIENTE DE BENCHMARK</p>', unsafe_allow_html=True)
st.title(section)
st.fragment(run_every=settings.poll_seconds)(dashboard)()
