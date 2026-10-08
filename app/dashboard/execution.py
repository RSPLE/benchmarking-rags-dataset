from __future__ import annotations

import re
import uuid

import pandas as pd
import streamlit as st

from app.cli import PROJECTS
from app.dashboard.operations import build_command, request_control

SELECTIONS = {
    "pending": "Somente pendentes",
    "failed": "Somente falhas",
    "unresolved": "Pendentes e falhas",
}


def compatible_records(records, project=None):
    return [
        row
        for row in records
        if row["origin"] == "v2"
        and re.fullmatch(r"[a-f0-9]{64}", row["external_id"])
        and (project is None or row["project"] == project)
    ]


def control_status(settings, token):
    try:
        return request_control(settings, token, "/status")
    except (OSError, ValueError, PermissionError) as exc:
        st.warning("O serviço de execução está indisponível: " + str(exc))
        return None


def pending_view(settings, token, records):
    st.write("Identifique o que falta e retome as etapas incompletas de cada experimento.")
    if not records:
        st.info("Ainda não há experimentos. Use Executar e retomar para iniciar um RAG.")
        return
    frame = pd.DataFrame(
        [
            {
                "RAG": row["project"],
                "Experimento": row["external_id"][:12],
                "Sucessos": row["summary"].get("success", 0),
                "Pendentes": row["summary"].get("pending", 0),
                "Falhas": row["summary"].get("failed", 0),
                "Retomada": "Disponível" if row in compatible_records(records) else "Histórico",
            }
            for row in records
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch")
    eligible = compatible_records(records)
    if not eligible:
        st.info(
            "Os arquivos importados são históricos e não possuem checkpoint v2 retomável. Eles permanecem preservados; uma nova execução terá seu próprio experimento."
        )
        return
    selected = st.selectbox(
        "Inspecionar experimento",
        eligible,
        format_func=lambda row: f"{row['project']} · {row['external_id'][:12]}",
    )
    try:
        result = request_control(
            settings, token, f"/falhas {selected['project']} {selected['external_id']}"
        )
        rows = [
            {
                "Questão": key,
                "Estado": value.get("status"),
                "Etapa": value.get("failed_stage"),
                "Erro": value.get("error_type"),
                "Tentativas": value.get("attempts"),
                "Próxima tentativa": value.get("retry_after"),
            }
            for key, value in result.get("items", {}).items()
        ]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.success("Nenhuma falha atual neste experimento.")
    except (OSError, ValueError, PermissionError) as exc:
        st.warning("Não foi possível consultar as falhas: " + str(exc))
    if st.button("Preparar retomada deste experimento", type="primary"):
        st.session_state["resume_target"] = selected["external_id"]
        st.session_state["navigate_to"] = "Executar e retomar"
        st.rerun()


def execution_view(settings, token, records):
    status = control_status(settings, token)
    jobs = status.get("jobs", []) if status else []
    busy = any(job["state"] in {"queued", "running"} for job in jobs)
    st.caption(
        "A interface e o Telegram compartilham a mesma fila. Fechar o navegador não interrompe um lote em execução."
    )
    if busy:
        st.info(
            "Há um lote na fila ou em execução. Novos envios ficam disponíveis quando ele encerrar."
        )
    target = st.session_state.get("resume_target")
    all_eligible = compatible_records(records)
    target_record = next((row for row in all_eligible if row["external_id"] == target), None)
    projects = list(PROJECTS) + ["all"]
    project = st.selectbox(
        "RAG",
        projects,
        index=projects.index(target_record["project"]) if target_record else 0,
        format_func=lambda value: "Todos os seis RAGs · sequência" if value == "all" else value,
    )
    eligible = compatible_records(records, project)
    actions = ["Retomar experimento", "Iniciar execução"] if eligible else ["Iniciar execução"]
    action = st.radio("Operação", actions, horizontal=True)
    selected = None
    if action == "Retomar experimento":
        selected = st.selectbox(
            "Experimento",
            eligible,
            index=next((i for i, row in enumerate(eligible) if row["external_id"] == target), 0),
            format_func=lambda row: (
                f"{row['external_id'][:12]} · {row['summary'].get('pending', 0)} pendentes · {row['summary'].get('failed', 0)} falhas"
            ),
        )
        st.caption(
            "Respostas e métricas já salvas são reaproveitadas. Mudanças incompatíveis de configuração são recusadas pelo executor."
        )
    with st.form("execution_form"):
        left, right = st.columns(2)
        selection = left.selectbox(
            "Quais questões", list(SELECTIONS), index=2, format_func=SELECTIONS.get
        )
        questions = right.number_input(
            "Máximo de tentativas neste lote", min_value=1, max_value=90, value=90, step=1
        )
        options = {"questions": questions, "selection": selection}
        with st.expander("Parâmetros de execução"):
            provider = st.selectbox("Provedor", ["Configuração atual", "openrouter", "openai"])
            default_mode = selected["manifest"].get("mode", "full") if selected else "full"
            mode = st.selectbox(
                "Modo",
                ["full", "evaluate"],
                index=int(default_mode == "evaluate"),
                format_func=lambda value: (
                    "Preparação, respostas e avaliação"
                    if value == "full"
                    else "Avaliar respostas congeladas"
                ),
            )
            frozen = st.text_input(
                "Arquivo de respostas congeladas", placeholder="frozen/respostas.json"
            )
            repetition = st.number_input(
                "Repetição · 0 mantém a configuração atual", min_value=0, value=0, step=1
            )
            max_calls = st.number_input(
                "Máximo de chamadas · 0 mantém a configuração atual", min_value=0, value=0, step=1
            )
            max_seconds = st.number_input(
                "Tempo máximo do lote (s) · 0 mantém a configuração atual",
                min_value=0,
                value=0,
                step=1,
            )
            question_timeout = st.number_input(
                "Tempo máximo por questão (s) · 0 mantém a configuração atual",
                min_value=0,
                value=0,
                step=1,
            )
            options.update(
                provider=provider if provider != "Configuração atual" else None,
                mode=mode,
                frozen=frozen or None,
                repetition=repetition or None,
                max_calls=max_calls or None,
                max_seconds=max_seconds or None,
                question_timeout=question_timeout or None,
            )
        st.caption(
            "O limite conta tentativas, não sucessos. A execução pode consumir a API configurada, incluindo preparação, embeddings e avaliação."
        )
        submit = st.form_submit_button(
            "Enviar lote para execução",
            type="primary",
            disabled=busy or status is None,
            width="stretch",
        )
    if submit:
        try:
            command = build_command(
                "retomar" if selected else "executar",
                project,
                selected["external_id"] if selected else None,
                **options,
            )
            current = st.session_state.get("web_submission")
            if current is None or current["command"] != command:
                current = {"command": command, "id": "web:" + uuid.uuid4().hex}
                st.session_state["web_submission"] = current
            result = request_control(settings, token, command, current["id"])
            st.session_state["last_submission"] = {"command": command, "result": result}
            st.rerun()
        except (OSError, ValueError, PermissionError) as exc:
            st.error("O lote não foi confirmado: " + str(exc))
    if st.session_state.get("last_submission"):
        st.success(
            "Lote registrado na fila. Você pode acompanhar a execução aqui ou pelo Telegram."
        )
        st.code(st.session_state["last_submission"]["command"], language="bash")
        st.caption(
            "Esse pedido mantém o mesmo identificador em caso de reenvio. Para enviar um novo lote igual após sua conclusão, prepare um novo pedido."
        )
        if st.button("Preparar outro pedido", disabled=busy):
            st.session_state.pop("web_submission", None)
            st.session_state.pop("last_submission", None)
            st.rerun()
    st.subheader("Fila e últimas execuções")
    if jobs:
        st.dataframe(
            pd.DataFrame(jobs).rename(
                columns={
                    "job_id": "Lote",
                    "state": "Estado",
                    "created": "Criado em",
                    "finished": "Encerrado em",
                }
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("Nenhum lote registrado na fila.")
    active = [
        row for row in (status or {}).get("experiments", []) if row.get("operation") == "running"
    ]
    if active:
        with st.form("pause_execution"):
            chosen = st.selectbox(
                "Experimento em execução",
                active,
                format_func=lambda row: (
                    f"{row.get('project')} · {row.get('experiment_id', '')[:12]}"
                ),
            )
            pause = st.form_submit_button("Pausar preservando o checkpoint")
        if pause:
            try:
                request_control(
                    settings,
                    token,
                    build_command("pausar", chosen["project"], chosen["experiment_id"]),
                )
                st.success("Pausa solicitada. Acompanhe o encerramento na fila.")
            except (OSError, ValueError, PermissionError) as exc:
                st.error(str(exc))
