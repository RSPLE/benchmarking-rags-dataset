from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from app.cli import PROJECTS
from app.dashboard.operations import build_command, request_control
from app.runtime_config import read_runtime_config

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


JOB_STATES = {
    "queued": "Na fila",
    "running": "Em execução",
    "finished": "Encerrada",
    "failed": "Falha ao iniciar",
    "stopped": "Interrompida",
    "interrupted": "Serviço reiniciado",
    "cancelled": "Cancelada",
}
PROJECT_DESCRIPTIONS = {
    "context-rag": "Recupera trechos dos PDFs para responder e avaliar cada questão.",
    "graph-rag": "Usa um grafo de documentos para encontrar relações antes de responder.",
    "hybrid-rag": "Combina estratégias de recuperação para responder às questões.",
    "knowledge-enhanced-rag": "Combina os PDFs com o grafo de conhecimento configurado no Neo4j.",
    "memory-augmented-rag": "Usa memória durante o processamento de cada questão.",
    "self-rag": "Revisa a própria resposta com base nos documentos recuperados.",
    "all": "Executa os seis RAGs em sequência. Se um processo falhar, os seguintes são cancelados.",
}


def local_time(value):
    if not value:
        return "—"
    try:
        return (
            datetime.fromisoformat(value)
            .astimezone(ZoneInfo("America/Belem"))
            .strftime("%d/%m às %H:%M:%S")
        )
    except (ValueError, TypeError):
        return str(value)


def notification_status(settings):
    try:
        health = json.loads(settings.control_socket.with_name("telegram-status.json").read_text())
        age = (datetime.now(UTC) - datetime.fromisoformat(health["updated_at"])).total_seconds()
        if age > 120:
            st.warning("Telegram sem atualização recente. Confira o serviço de notificações.")
        elif health.get("delivery_error"):
            st.error(
                f"Telegram não conseguiu entregar: {health['delivery_error']}. O envio será tentado novamente."
            )
        elif health.get("control_error"):
            st.warning(health["control_error"])
        elif health.get("last_delivery"):
            st.success(f"Telegram · última entrega em {local_time(health['last_delivery'])}")
        else:
            st.info("Telegram conectado ao executor. Aguardando a primeira entrega desta sessão.")
        if health.get("pending"):
            st.caption(f"{health['pending']} notificação(ões) aguardando envio.")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        st.warning(
            "Telegram sem confirmação de funcionamento. Confira a configuração e o serviço de notificações."
        )


def job_feedback(job):
    state = job.get("state")
    label = f"{job.get('project', 'Pipeline')} · {JOB_STATES.get(state, state)}"
    if state in {"failed", "stopped", "interrupted", "cancelled"}:
        st.error(label)
        st.write(
            job.get("error")
            or (
                "O executor foi interrompido antes de concluir. Confira os logs do serviço control e envie um novo pedido após corrigir a causa."
            )
        )
    elif state == "finished":
        st.success(label)
        st.caption(
            "O processo terminou. Confira sucessos, falhas e pendências nos resultados antes de iniciar outro lote."
        )
    else:
        st.info(label)
        st.caption(
            "A preparação dos documentos pode levar alguns minutos. O acompanhamento atualiza automaticamente."
        )
    st.caption(f"Lote: {job['job_id']} · Enviado em {local_time(job.get('created'))}")


def execution_view(settings, token, records):
    st.write(
        "Escolha uma pipeline, defina quantas questões processar e inicie a execução. O andamento aparece aqui automaticamente."
    )
    status = control_status(settings, token)
    jobs = status.get("jobs", []) if status else []
    busy = any(job["state"] in {"queued", "running"} for job in jobs)
    enabled = status is not None and status.get("enabled", True)
    left, right = st.columns(2)
    with left:
        if status is not None:
            if not enabled:
                st.error(
                    "Execução desativada. Configure BENCHMARK_REMOTE_ENABLED=true no serviço control."
                )
            elif busy:
                st.info("Executor ocupado · acompanhe o lote abaixo.")
            else:
                st.success("Executor disponível para iniciar uma pipeline.")
    with right:
        notification_status(settings)
        if st.button("Testar notificação no Telegram", disabled=status is None):
            try:
                request_control(settings, token, "/notificar")
                st.success(
                    "Aviso enfileirado no canal configurado. A confirmação da entrega aparece acima."
                )
            except (OSError, ValueError, PermissionError) as exc:
                st.error("Não foi possível solicitar o aviso: " + str(exc))
    if jobs:
        latest = next((job for job in jobs if job["state"] == "running"), jobs[0])
        if busy or st.session_state.get("last_submission"):
            st.subheader("Acompanhar execução")
            feedback = st.container(border=True)
        else:
            feedback = st.expander(
                f"Última execução · {latest.get('project', 'Pipeline')} · "
                f"{JOB_STATES.get(latest['state'], latest['state'])} · "
                f"{local_time(latest.get('created'))}"
            )
        with feedback:
            job_feedback(latest)
        active = [
            row
            for row in (status or {}).get("experiments", [])
            if row.get("operation") == "running"
        ]
        if busy:
            for row in active:
                st.write(
                    f"**{row.get('project')}** · Questão: {row.get('current_question') or 'preparando'} · Etapa: {row.get('current_stage') or 'preparação'}"
                )
                st.caption(
                    f"Sucessos: {row.get('success', 0)} · Falhas: {row.get('failed', 0)} · Pendentes: {row.get('pending', 0)}"
                )
    st.caption(
        f"Atualização automática a cada {settings.poll_seconds}s. Fechar o navegador não interrompe a execução."
    )
    if st.button("Atualizar andamento"):
        st.rerun()
    target = st.session_state.get("resume_target")
    try:
        dataset_size = int(
            (read_runtime_config(getattr(settings, "configuration", None)).get("dataset") or {}).get(
                "questions", 90
            )
        )
    except (OSError, ValueError, TypeError):
        dataset_size = 90
    all_eligible = compatible_records(records)
    target_record = next((row for row in all_eligible if row["external_id"] == target), None)
    st.subheader("1. Escolha a pipeline")
    reported_projects = (
        status["projects"]
        if status is not None and "projects" in status
        else list(PROJECTS)
    )
    configured_projects = [project for project in reported_projects if project in PROJECTS]
    if not configured_projects:
        st.warning("Nenhuma pipeline está habilitada. Selecione ao menos um RAG em Parâmetros.")
        return
    projects = configured_projects + (["all"] if len(configured_projects) > 1 else [])
    selected_index = (
        projects.index(target_record["project"])
        if target_record and target_record["project"] in projects
        else 0
    )
    project = st.selectbox(
        "Pipeline RAG",
        projects,
        index=selected_index,
        format_func=lambda value: "Todos os RAGs habilitados · sequência" if value == "all" else value,
    )
    st.caption(PROJECT_DESCRIPTIONS[project])
    eligible = compatible_records(records, project)
    selected = None
    if eligible:
        continuation = any(row["manifest"].get("continuation") for row in eligible)
        action = st.radio(
            "O que deseja fazer?",
            ["Iniciar execução", "Retomar experimento"],
            index=int(target_record in eligible or continuation),
            key="operation-" + project,
            horizontal=True,
        )
        if action == "Retomar experimento":
            selected = st.selectbox(
                "Experimento a retomar",
                eligible,
                index=next(
                    (i for i, row in enumerate(eligible) if row["external_id"] == target), 0
                ),
                format_func=lambda row: (
                    f"{row['external_id'][:12]} · {row['summary'].get('pending', 0)} pendentes · {row['summary'].get('failed', 0)} falhas"
                ),
            )
            st.caption(
                "Respostas e métricas já salvas são reaproveitadas. Mantenha os parâmetros do experimento original."
            )
            st.info(
                f"{selected['summary'].get('success', 0)} questões concluídas serão preservadas. "
                f"Restam {selected['summary'].get('failed', 0)} falhas e "
                f"{selected['summary'].get('pending', 0)} pendentes para continuar."
            )
            if selected["manifest"].get("continuation"):
                st.caption(
                    "Checkpoint anterior convertido para retomada. As questões concluídas não serão executadas novamente; as restantes usam a configuração atual."
                )
    with st.form("execution_form"):
        st.subheader("2. Defina o tamanho da execução")
        remaining = (
            (selected["summary"].get("failed", 0) + selected["summary"].get("pending", 0))
            if selected
            else 1
        )
        st.caption(
            "O lote tenta somente as questões incompletas do experimento selecionado."
            if selected
            else f"Comece com 1 questão para verificar o fluxo. Depois, aumente até {dataset_size} questões por RAG."
        )
        left, right = st.columns(2)
        questions = left.number_input(
            "Quantidade de questões por RAG",
            min_value=1,
            max_value=dataset_size,
            value=max(1, min(dataset_size, remaining)),
            key="question-count-" + (selected["external_id"] if selected else project),
            step=1,
            help="Máximo de questões tentadas neste lote, incluindo as que falharem.",
        )
        selection = right.selectbox(
            "Quais questões processar",
            list(SELECTIONS),
            index=2,
            format_func=SELECTIONS.get,
            help="Questões concluídas não são repetidas. Pendentes ainda não terminaram; falhas precisam de nova tentativa.",
        )
        options = {"questions": questions, "selection": selection}
        with st.expander("Configurações avançadas · modelos, avaliação e limites"):
            st.caption(
                "Os valores padrão usam a configuração do servidor. Para uma primeira execução, mantenha-os."
            )
            provider = st.selectbox("Provedor", ["Configuração atual", "openrouter", "openai"])
            default_mode = selected["manifest"].get("mode", "full") if selected else "full"
            mode = st.selectbox(
                "Modo",
                ["full", "evaluate"],
                index=int(default_mode == "evaluate"),
                format_func=lambda value: (
                    "Preparar documentos, responder e avaliar"
                    if value == "full"
                    else "Avaliar respostas já salvas"
                ),
            )
            frozen = st.text_input(
                "Arquivo de respostas já salvas · apenas no modo avaliação",
                placeholder="frozen/respostas.json",
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
        st.subheader("3. Inicie e acompanhe")
        st.write(
            "O executor prepara os documentos, gera as respostas e avalia as métricas. Cada etapa usa a API configurada e pode consumir créditos."
        )
        if project == "all":
            st.info("A quantidade escolhida será aplicada a cada um dos seis RAGs.")
        submit = st.form_submit_button(
            "Retomar pipeline" if selected else "Iniciar pipeline",
            type="primary",
            disabled=busy or not enabled,
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
            with st.spinner("Enviando pedido ao executor…"):
                result = request_control(settings, token, command, current["id"])
            st.session_state["last_submission"] = {"command": command, "result": result}
            st.session_state.pop("web_submission", None)
            st.session_state.pop("submission_error", None)
            st.rerun()
        except (OSError, ValueError, PermissionError) as exc:
            st.session_state["submission_error"] = str(exc)
    if st.session_state.get("submission_error"):
        st.error("A execução não foi confirmada: " + st.session_state["submission_error"])
        st.caption(
            "Se houve perda de conexão, tente novamente com os mesmos parâmetros. O pedido pendente mantém seu identificador para evitar duplicação."
        )
    if st.session_state.get("last_submission"):
        with st.expander("Detalhes do último pedido enviado"):
            st.caption(
                "O estado atual aparece em Acompanhar execução. Um novo clique após o término cria outro lote, mesmo com os mesmos parâmetros."
            )
            st.code(st.session_state["last_submission"]["command"], language="bash")
    if jobs:
        with st.expander("Histórico de execuções"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Pipeline": job.get("project", "—"),
                            "Estado": JOB_STATES.get(job["state"], job["state"]),
                            "Enviado": local_time(job.get("created")),
                            "Encerrado": local_time(job.get("finished")),
                            "Motivo": job.get("error") or "—",
                            "Lote": job["job_id"],
                        }
                        for job in jobs
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
    active = [
        row for row in (status or {}).get("experiments", []) if row.get("operation") == "running"
    ]
    if busy and active:
        with st.form("pause_execution"):
            chosen = st.selectbox(
                "Experimento em execução",
                active,
                format_func=lambda row: (
                    f"{row.get('project')} · {row.get('experiment_id', '')[:12]}"
                ),
            )
            pause = st.form_submit_button("Pausar preservando o progresso")
        if pause:
            try:
                request_control(
                    settings,
                    token,
                    build_command("pausar", chosen["project"], chosen["experiment_id"]),
                )
                st.success("Pausa solicitada. Acompanhe o encerramento acima.")
            except (OSError, ValueError, PermissionError) as exc:
                st.error(str(exc))
