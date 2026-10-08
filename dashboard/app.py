from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import streamlit as st

from dashboard.analytics import common_questions, experiments, extrema, frames, response_totals
from dashboard.auth import authenticate, valid_session
from dashboard.charts import render
from dashboard.config import Settings
from dashboard.database import connect

st.set_page_config(page_title="Observatório RAG · LogiBots", page_icon="📊", layout="wide")
settings = Settings.from_environment()
st.markdown(
    """<style>
.stApp {background: #f6f8f7;}
.block-container {max-width: 1200px; padding-top: 2.5rem; padding-bottom: 3rem;}
h1,h2,h3 {letter-spacing: -0.035em; color: #153f39;}
[data-testid="stMetric"] {background: white; border: 1px solid #dfe8e3; border-radius: 14px; padding: 1rem;}
[data-testid="stMetricLabel"] {color: #526961;}
.eyebrow {color: #237a73; font-size: .75rem; font-weight: 700; letter-spacing: .14em; margin: 0;}
[data-testid="stButton"] button,[data-testid="stDownloadButton"] button {min-height: 44px;}
@media (max-width: 640px) {
 .block-container {padding: 1.2rem .8rem 2rem;}
 h1 {font-size: 1.85rem !important;}
 [data-testid="stHorizontalBlock"] {flex-wrap: wrap; gap: .7rem;}
 [data-testid="stColumn"] {min-width: min(100%, 260px); flex: 1 1 260px !important;}
}
</style>""",
    unsafe_allow_html=True,
)


def login():
    if valid_session(settings.database, st.session_state.get("identity"), settings.session_seconds):
        return
    st.session_state.pop("identity", None)
    st.markdown('<p class="eyebrow">LOGIBOTS / ÁREA PRIVADA</p>', unsafe_allow_html=True)
    st.title("Observatório RAG")
    st.write("Resultados, métricas e consumo dos seus experimentos em um só lugar.")
    with connect(settings.database) as db:
        configured = db.execute("SELECT count(*) FROM users").fetchone()[0]
    if not configured:
        st.info(
            "Defina DASHBOARD_USERNAME e DASHBOARD_PASSWORD na .env da raiz "
            "antes de iniciar a aplicação. Use uma senha com pelo menos 12 caracteres."
        )
        st.code(
            "docker compose up -d",
            language="bash",
        )
        st.caption("O painel não possui credenciais padrão. Guia completo: dashboard/README.md.")
        st.stop()
    with st.form("login", clear_on_submit=True):
        username = st.text_input("Usuário", max_chars=64)
        password = st.text_input("Senha", type="password", max_chars=1024)
        submit = st.form_submit_button("Entrar", type="primary", width="stretch")
    if submit:
        identity = authenticate(settings.database, username, password)
        if identity:
            st.session_state["identity"] = identity
            st.rerun()
        st.error(
            "Não foi possível entrar. Confira os dados ou aguarde cinco minutos após várias tentativas."
        )
    st.stop()


def downloads(kind, frame, name, title="", metric=None):
    if frame.empty:
        st.info("Ainda não há medições suficientes para este gráfico.")
        return
    try:
        png, eps = render(kind, frame, title, metric)
    except ValueError:
        st.warning("Este recorte não possui todas as medições necessárias para o gráfico.")
        return
    st.image(png, width="stretch")
    left, right = st.columns(2)
    left.download_button(
        "Baixar PNG",
        png,
        name + ".png",
        "image/png",
        key=name + "-png",
        on_click="ignore",
        width="stretch",
    )
    right.download_button(
        "Baixar EPS",
        eps,
        name + ".eps",
        "application/postscript",
        key=name + "-eps",
        on_click="ignore",
        width="stretch",
    )


def dashboard():
    if not valid_session(
        settings.database, st.session_state.get("identity"), settings.session_seconds
    ):
        st.rerun()
    records = experiments(settings.database)
    with connect(settings.database) as db:
        last = db.execute("SELECT value FROM metadata WHERE key='last_sync'").fetchone()
        problems = [dict(row) for row in db.execute("SELECT * FROM sync_errors")]
    if last:
        elapsed = (datetime.now(UTC) - datetime.fromisoformat(last[0])).total_seconds()
        st.caption(f"Última sincronização: {last[0]} · atualização a cada {settings.poll_seconds}s")
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
    if not records:
        st.info(
            "Nenhum resultado importado ainda. Assim que um benchmark salvar resultados, eles aparecerão aqui."
        )
        return
    with st.expander("Escolher experimentos", expanded=True):
        groups = sorted({row["comparison_key"] for row in records})
        selected_group = st.selectbox(
            "Protocolo de comparação",
            groups,
            format_func=lambda key: (
                "Histórico · configuração não registrada"
                if key == "legacy-unknown"
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
    selected = [by_id[key] for key in chosen]
    samples, calls, runs = frames(settings.database, chosen)
    successes = samples[samples["status"] == "success"].copy() if not samples.empty else samples
    comparison = common_questions(successes) if common else successes
    overview = pd.DataFrame(
        [
            {
                "RAG": row["project"],
                "Experimento": row["external_id"][:12],
                "Estado": row["summary"].get("operation"),
                "Sucessos": row["summary"].get("success", 0),
                "Falhas": row["summary"].get("failed", 0),
                "Pendentes": row["summary"].get("pending", 0),
                "Modelo": row["model"],
            }
            for row in selected
        ]
    )
    a, b, c = st.columns(3)
    a.metric("Experimentos", len(selected))
    b.metric("Casos com sucesso", len(successes))
    c.metric("Casos no recorte", len(comparison))
    section = st.selectbox(
        "Explorar",
        [
            "Visão geral",
            "Gráficos originais",
            "Tokens e tempo",
            "Questões",
            "Chamadas e modelos",
            "Arquivos e backups",
        ],
        key="section",
    )
    if section == "Visão geral":
        st.subheader("Cobertura antes da comparação")
        st.dataframe(overview, hide_index=True, width="stretch")
        st.caption(
            "Sucessos, falhas e pendências são mostrados separadamente. Uma nota ausente nunca é tratada como zero."
        )
        if not comparison.empty:
            downloads("original", comparison, "metricas-originais")
    elif section == "Gráficos originais":
        choice = st.selectbox(
            "Tipo original",
            [
                "Barras de métricas · script CSV",
                "Tokens × latência · notebook Self",
                "Painel de sete gráficos · notebook Graph",
            ],
        )
        if choice.startswith("Barras"):
            downloads("original", comparison, "barras-originais")
        elif choice.startswith("Tokens"):
            downloads("scatter", response_totals(comparison), "dispersao-original")
        else:
            experiment = st.selectbox(
                "Experimento do painel",
                chosen,
                format_func=lambda key: (
                    f"{by_id[key]['project']} · {by_id[key]['external_id'][:12]}"
                ),
            )
            frame = (
                successes[successes["experiment_id"] == experiment]
                if not successes.empty
                else successes
            )
            if frame.empty:
                st.info("Este experimento ainda não possui casos completos.")
            else:
                questions = st.multiselect(
                    "Questões no painel", frame["id"].tolist(), default=frame["id"].tolist()[:10]
                )
                frame = frame[frame["id"].isin(questions)]
                st.caption(
                    "Barras médias e por questão, radar, heatmap, boxplot, linhas e tabela estatística. Os títulos identificam o modelo realmente registrado."
                )
                downloads(
                    "graph",
                    frame,
                    "painel-original",
                    f"Avaliação de Métricas RAGAS — {by_id[experiment]['project']}\n{by_id[experiment]['model']}",
                )
    elif section == "Tokens e tempo":
        st.subheader("Geração das respostas")
        st.caption(
            "Tokens e latência registrados por resposta com sucesso, no recorte selecionado. Não incluem o juiz ou toda a preparação."
        )
        totals = response_totals(comparison)
        measure = st.radio("Medida", ["Tokens", "Tempo"], horizontal=True)
        metric = "tokens" if measure == "Tokens" else "seconds"
        downloads(
            "bar",
            totals,
            "respostas-" + metric,
            "Tokens das respostas por RAG" if metric == "tokens" else "Tempo das respostas por RAG",
            metric,
        )
        st.dataframe(totals, hide_index=True, width="stretch")
        if not runs.empty:
            st.subheader("Tempo de execução dos lotes")
            completed = runs[runs["finished_at"].notna()]
            st.dataframe(
                completed.groupby("project")["seconds"]
                .sum(min_count=1)
                .rename("Segundos de lotes encerrados")
                .reset_index(),
                hide_index=True,
                width="stretch",
            )
            st.caption(
                "Tempo entre os eventos de início e fim; inclui preparação e avaliação. Rodadas ainda abertas não entram nesse total."
            )
    elif section == "Questões":
        metric = st.selectbox(
            "Ranking de geração",
            ["answer_total_tokens", "answer_response_time_seconds"],
            format_func=lambda key: (
                "Tokens da resposta" if key.endswith("tokens") else "Tempo da resposta (s)"
            ),
        )
        st.dataframe(extrema(comparison, metric), hide_index=True, width="stretch")
        st.caption("Empates são preservados. Medições ausentes não participam do ranking.")
        st.dataframe(
            comparison[
                [
                    name
                    for name in (
                        "project",
                        "id",
                        "question",
                        "answer_total_tokens",
                        "answer_response_time_seconds",
                    )
                    if name in comparison
                ]
            ],
            hide_index=True,
            width="stretch",
        )
    elif section == "Chamadas e modelos":
        st.subheader("Consumo total registrado")
        st.caption(
            "Inclui todas as questões e etapas dos experimentos selecionados, mesmo falhas e tentativas. Não é restrito ao recorte de sucessos dos gráficos de respostas."
        )
        if calls.empty:
            st.info(
                "Não há diário de chamadas para estes arquivos. O painel não estima nem inventa esse consumo."
            )
        else:
            st.metric("Chamadas sem tokens informados", int(calls["tokens"].isna().sum()))
            group = (
                calls.groupby(["project", "model", "stage"], dropna=False)[
                    ["tokens", "cost", "seconds"]
                ]
                .sum(min_count=1)
                .reset_index()
            )
            st.dataframe(group, hide_index=True, width="stretch")
            by_rag = calls.groupby("project")["tokens"].sum(min_count=1).reset_index()
            downloads(
                "bar",
                by_rag,
                "tokens-todas-etapas",
                "Tokens conhecidos · todas as etapas",
                "tokens",
            )
            st.caption(
                f"{int(calls['cost'].isna().sum())} chamada(s) sem custo informado. Valores desconhecidos não equivalem a gratuidade. Tempos HTTP somados não equivalem a tempo de parede quando há concorrência."
            )
    else:
        st.subheader("Resultados e preservação")
        experiment = st.selectbox(
            "Baixar de",
            chosen,
            format_func=lambda key: f"{by_id[key]['project']} · {by_id[key]['external_id'][:12]}",
        )
        with connect(settings.database) as db:
            artifacts = db.execute(
                "SELECT name,content,sha256 FROM artifacts WHERE experiment_id=? AND name IN ('results.csv','results_detailed.csv','summary.json','manifest.json','checkpoint.json','usage.jsonl') ORDER BY name",
                (experiment,),
            ).fetchall()
            backups = pd.read_sql_query(
                "SELECT id,created_at,sha256 FROM backups ORDER BY created_at DESC LIMIT 20", db
            )
        for row in artifacts:
            st.download_button(
                row["name"],
                row["content"],
                row["name"],
                key="artifact-" + row["name"],
                on_click="ignore",
                width="stretch",
            )
        st.caption(
            "Arquivos completos são restritos a usuários autenticados. Os originais continuam nos volumes de resultados."
        )
        st.subheader("Cópias verificadas do SQLite")
        st.dataframe(backups, hide_index=True, width="stretch")
        st.caption(
            "Cópia online local e cópias ao encerrar rodadas. O ZIP enviado ao Telegram contém resultados públicos e hashes; não contém senhas, checkpoints privados ou chaves."
        )


login()
st.markdown('<p class="eyebrow">LOGIBOTS / BENCHMARKS</p>', unsafe_allow_html=True)
st.title("Observatório RAG")
st.write("Compare qualidade, acompanhe consumo e preserve os resultados.")
if st.button("Sair", key="logout"):
    st.session_state.clear()
    st.rerun()
st.fragment(run_every=settings.poll_seconds)(dashboard)()
