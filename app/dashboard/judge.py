from __future__ import annotations

import json
import uuid

import pandas as pd
import streamlit as st

from app.benchmark.config import METRICS
from app.benchmark.judge_audit import read_events
from app.benchmark.review import EVIDENCE_MODES
from app.benchmark.storage import fingerprint
from app.dashboard.database import connect
from app.dashboard.ingest import detailed_csv_rows
from app.dashboard.operations import build_review_command, request_control
from app.dashboard.questions import LABELS


def judge_data(database, experiment):
    with connect(database) as db:
        files = {
            row["name"]: row["content"]
            for row in db.execute(
                "SELECT name,content FROM artifacts WHERE experiment_id=? "
                "AND name IN ('checkpoint.json','results_detailed.csv',"
                "'judge_responses.jsonl','judge_reviews.jsonl')",
                (experiment,),
            )
        }
        samples = [
            json.loads(row["payload"])
            for row in db.execute(
                "SELECT payload FROM samples WHERE experiment_id=? ORDER BY position",
                (experiment,),
            )
        ]
    checkpoint = json.loads(files.get("checkpoint.json", b"{}"))
    merge_saved_results(
        checkpoint,
        samples,
        detailed_csv_rows(files.get("results_detailed.csv", b"")),
    )
    traces = {}
    for event in read_events(files.get("judge_responses.jsonl", b"")):
        traces.setdefault(event["trace_id"], []).append(event)
    return checkpoint, traces, read_events(files.get("judge_reviews.jsonl", b""))


def merge_saved_results(checkpoint, samples, details):
    """Expose saved CSV fields without pretending legacy evidence can be rejudged."""
    items = checkpoint.setdefault("items", {})
    by_question = {
        state.get("question")
        or (state.get("result") or {}).get("question")
        or (state.get("artifact") or {}).get("question"): identifier
        for identifier, state in items.items()
    }
    by_question.pop(None, None)
    for sample in samples:
        question = sample.get("question")
        detail = details.get(question, {})
        identifier = by_question.get(question) or detail.get("id") or sample.get("id")
        if not identifier:
            continue
        state = items.setdefault(identifier, {})
        state.setdefault("status", sample.get("status", "success"))
        state.setdefault("question", question)
        result = state.get("result")
        if not isinstance(result, dict):
            result = {}
            state["result"] = result
        for key, value in sample.items():
            if value is not None:
                result.setdefault(key, value)
        state["_dashboard_detail"] = {
            **sample,
            **detail,
            "id": identifier,
            "question": question,
        }
    return checkpoint


def display_artifact(state):
    """Merge display-only CSV data while preserving the original audit artifact."""
    artifact = dict(state.get("artifact") or {})
    detail = state.get("_dashboard_detail") or {}
    result = state.get("result") or {}
    for key in ("question", "answer", "ground_truth"):
        value = artifact.get(key) or detail.get(key) or result.get(key)
        if value is not None:
            artifact[key] = value
    return artifact


def original_score(state, metric):
    entry = state.get("metrics", {}).get(metric, {})
    return (
        entry.get("value")
        if entry.get("status") == "success"
        else (state.get("result") or {}).get(metric)
    )


def audit_rows(checkpoint, traces, reviews, metric):
    rows = []
    for identifier, state in sorted(checkpoint.get("items", {}).items()):
        saved_artifact = state.get("artifact") or {}
        generation = "\n".join(saved_artifact.get("generation_contexts") or [])
        matching = [
            events
            for events in traces.values()
            if events[0].get("question_id") == identifier and events[0].get("metric") == metric
        ]
        attempts = [
            r
            for r in reviews
            if r["kind"] == "result"
            and r.get("question_id") == identifier
            and r["metric"] == metric
        ]
        latest = attempts[-1] if attempts else {}
        score = original_score(state, metric)
        rationale_saved = any(
            e["kind"] in {"response", "judgment"} for events in matching for e in events
        )
        can_review = bool(
            saved_artifact.get("answer")
            and saved_artifact.get("contexts")
            and saved_artifact.get("ground_truth")
        )
        rows.append(
            {
                "Questão": identifier,
                "Nota original": score,
                "Nota registrada": score is not None,
                "Última reavaliação": latest.get("value"),
                "Estado da reavaliação": latest.get("status", "Sem reavaliação"),
                "Evidência da reavaliação": EVIDENCE_MODES.get(latest.get("evidence"), "—"),
                "Justificativa gravada": rationale_saved,
                "Contextos diferentes": bool(generation)
                and any(c not in generation for c in saved_artifact.get("contexts", [])),
                "Pode reavaliar": can_review,
                "Dados disponíveis": " · ".join(
                    (
                        "Nota registrada" if score is not None else "Nota ausente",
                        "Justificativa gravada"
                        if rationale_saved
                        else "Justificativa não gravada",
                        "Reavaliação disponível"
                        if can_review
                        else "Reavaliação indisponível: faltam evidências completas",
                    )
                ),
                "Falha na métrica": state.get("metrics", {}).get(metric, {}).get("status")
                == "failed"
                or (
                    state.get("status") in {"failed", "partial"}
                    and state.get("failed_stage") == metric
                    and state.get("metrics", {}).get(metric, {}).get("status") != "success"
                ),
            }
        )
    return pd.DataFrame(rows)


def rationales(value):
    rows = []
    if isinstance(value, dict):
        if value.get("reason") is not None:
            rows.append(
                {
                    "Afirmação": value.get("statement", value.get("answer", "")),
                    "Veredito": value.get("attributed", value.get("verdict")),
                    "Justificativa": value["reason"],
                }
            )
        for item in value.values():
            rows.extend(rationales(item))
    elif isinstance(value, list):
        for item in value:
            rows.extend(rationales(item))
    return rows


def show_trace(events):
    start = events[0]
    end = events[-1]
    st.caption(f"Registro: {start['trace_id']} · {start['at']} · {end['kind']}")
    reasons = [
        row
        for event in events
        if event["kind"] == "judgment"
        for row in rationales(event.get("outputs"))
    ]
    if reasons:
        st.dataframe(pd.DataFrame(reasons), hide_index=True, width="stretch")
    else:
        st.info(
            "Este registro não contém justificativas textuais estruturadas. Confira a saída do juiz abaixo."
        )
    if end.get("error"):
        st.error(end["error"])
    with st.expander("Resposta do juiz e dados completos da avaliação"):
        for event in events:
            if event["kind"] == "response":
                for batch in event["responses"]:
                    for text in batch:
                        st.code(text, language="json")
            elif event["kind"] in {"prompt", "judgment"}:
                st.json(event, expanded=False)
        st.write("Entrada enviada à avaliação")
        st.json(start.get("inputs", {}), expanded=False)
        st.write("Configuração do juiz")
        st.json(start.get("configuration", {}), expanded=False)
    st.download_button(
        "Baixar registro do juiz",
        json.dumps(events, ensure_ascii=False, indent=2),
        f"juiz-{start['trace_id']}.json",
        "application/json",
        key="judge-trace-download",
        on_click="ignore",
    )


def judge_view(settings, token, records):
    st.write(
        "Consulte as notas originais, as justificativas que foram efetivamente gravadas e "
        "reavalie apenas questões que preservam resposta, gabarito e contextos originais."
    )
    st.caption(
        "Nota e justificativa são dados distintos: a nota pode existir no CSV/checkpoint mesmo "
        "quando a resposta textual do juiz não foi armazenada em judge_responses.jsonl."
    )
    if not records:
        st.info("Ainda não há experimentos importados.")
        return
    projects = sorted({record["project"] for record in records})
    left, right = st.columns(2)
    project = left.selectbox("Arquitetura RAG", projects, key="judge-project")
    candidates = {row["id"]: row for row in records if row["project"] == project}
    identifier = right.selectbox(
        "Experimento",
        list(candidates),
        format_func=lambda key: candidates[key]["external_id"][:12],
        key="judge-experiment-" + project,
    )
    record = candidates[identifier]
    metric = st.selectbox(
        "Métrica para inspecionar ou reavaliar",
        METRICS,
        index=METRICS.index("context_recall"),
        format_func=lambda name: LABELS[name],
        key="judge-metric",
    )
    checkpoint, traces, reviews = judge_data(settings.database, identifier)
    frame = audit_rows(checkpoint, traces, reviews, metric)
    if frame.empty:
        st.info("Este experimento ainda não tem questões registradas no checkpoint.")
        return
    score_count = int(frame["Nota registrada"].sum())
    rationale_count = int(frame["Justificativa gravada"].sum())
    eligible_count = int(frame["Pode reavaliar"].sum())
    a, b, c, d = st.columns(4)
    a.metric("Notas originais", score_count)
    b.metric("Notas zero", int((frame["Nota original"] == 0).sum()))
    c.metric("Justificativas gravadas", rationale_count)
    d.metric("Elegíveis para reavaliação", eligible_count)
    if score_count > rationale_count:
        st.warning(
            f"Há {score_count} nota(s) original(is), mas somente {rationale_count} "
            "justificativa(s) bruta(s) foi(ram) gravada(s). As justificativas ausentes não "
            "podem ser reconstruídas a partir das notas; uma nova reavaliação cria um novo registro."
        )
    if eligible_count < len(frame):
        st.info(
            f"{len(frame) - eligible_count} questão(ões) não pode(m) ser reavaliada(s) porque "
            "a execução original não preservou resposta, gabarito e contextos completos."
        )
    st.caption(
        "Contextos diferentes indica trechos avaliados que não aparecem nas evidências de geração salvas. É um sinal para revisão, não uma confirmação automática de erro."
    )
    scope = st.selectbox(
        "Filtrar questões",
        [
            "Todas",
            "Notas zero",
            "Falha na métrica",
            "Contextos diferentes",
            "Sem justificativa gravada",
            "Elegíveis para reavaliação",
        ],
        key="judge-filter",
    )
    visible = frame
    if scope == "Notas zero":
        visible = frame[frame["Nota original"] == 0]
    elif scope == "Sem justificativa gravada":
        visible = frame[~frame["Justificativa gravada"]]
    elif scope == "Elegíveis para reavaliação":
        visible = frame[frame["Pode reavaliar"]]
    elif scope != "Todas":
        visible = frame[frame[scope]]
    st.dataframe(visible, hide_index=True, width="stretch")
    st.download_button(
        "Baixar auditoria CSV",
        visible.to_csv(index=False, sep=";").encode("utf-8-sig"),
        f"auditoria-{project}-{metric}.csv",
        "text/csv",
        on_click="ignore",
    )
    if visible.empty:
        st.info("Nenhuma questão neste filtro.")
        return
    selected = st.selectbox(
        "Questão para ver os detalhes",
        visible["Questão"].tolist(),
        key=f"judge-detail-{identifier}-{metric}-{scope}",
    )
    state = checkpoint["items"][selected]
    artifact = display_artifact(state)
    st.write(artifact.get("question") or state.get("question", selected))
    with st.expander("Resposta, gabarito e comparação dos contextos"):
        st.write("Resposta gerada")
        st.write(
            artifact.get("answer") or state.get("result", {}).get("answer") or "Não registrada"
        )
        st.write("Gabarito")
        st.write(artifact.get("ground_truth") or "Não registrado")
        first, second = st.columns(2)
        with first:
            st.write("Contextos da avaliação original")
            st.json(artifact.get("contexts", []), expanded=False)
        with second:
            st.write("Evidências usadas na resposta")
            st.json(artifact.get("generation_contexts", []), expanded=False)
    available = {
        key: events
        for key, events in traces.items()
        if events[0].get("question_id") == selected and events[0].get("metric") == metric
    }
    if available:
        trace_id = st.selectbox(
            "Tentativa do juiz",
            list(reversed(available)),
            format_func=lambda key: (
                f"{'Reavaliação' if available[key][0].get('review_id') else 'Original'} · {available[key][0]['at']} · {available[key][-1]['kind']}"
            ),
            key=f"judge-attempt-{identifier}-{selected}-{metric}",
        )
        show_trace(available[trace_id])
    else:
        st.info(
            "A justificativa desta avaliação não foi gravada. Uma reavaliação salva uma nova resposta do juiz; não recupera a resposta antiga."
        )
    history = [
        r
        for r in reviews
        if r["kind"] == "result" and r.get("question_id") == selected and r["metric"] == metric
    ]
    if history:
        st.subheader("Histórico de reavaliações")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Data": r["at"],
                        "Nota original": r.get("previous_value"),
                        "Nova nota": r.get("value"),
                        "Estado": r["status"],
                        "Contextos": EVIDENCE_MODES[r["evidence"]],
                        "Erro": r.get("error"),
                    }
                    for r in history
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    st.subheader("Reavaliar somente esta métrica")
    st.caption(
        "Reutiliza respostas salvas. As novas notas e justificativas ficam no histórico de reavaliações, sem sobrescrever a avaliação original. Cada pedido pode consumir a API do juiz."
    )
    evidence = st.radio(
        "Contextos enviados ao juiz",
        list(EVIDENCE_MODES),
        format_func=EVIDENCE_MODES.get,
        key="judge-evidence",
        horizontal=True,
    )
    if evidence == "generation":
        st.info(
            "Esta opção avalia as evidências usadas na resposta, incluindo ferramentas e grafo quando registrados. É uma política diferente, identificada separadamente no histórico."
        )
    eligible = visible.loc[visible["Pode reavaliar"], "Questão"].tolist()
    if evidence == "generation":
        eligible = [
            key
            for key in eligible
            if checkpoint["items"][key].get("artifact", {}).get("generation_contexts")
        ]
    selection_key = f"judge-ids-{identifier}-{metric}-{scope}-{evidence}-{selected}"
    if selection_key not in st.session_state:
        st.session_state[selection_key] = [selected] if selected in eligible else []
    if st.button("Selecionar todas as questões elegíveis deste filtro", disabled=not eligible):
        st.session_state[selection_key] = eligible
    identifiers = st.multiselect(
        "Questões a reavaliar",
        eligible,
        key=selection_key,
    )
    if len(eligible) < len(visible):
        st.caption(
            "Questões sem resposta, gabarito ou contextos salvos não podem ser reavaliadas isoladamente."
        )
    reason = st.text_input(
        "Motivo da reavaliação", "Conferir a nota e a justificativa do juiz", max_chars=1000
    )
    status = None
    try:
        status = request_control(settings, token, "/status")
    except (OSError, ValueError, PermissionError):
        st.info("Executor indisponível. A consulta dos registros continua disponível.")
    busy = status and any(job["state"] in {"queued", "running"} for job in status.get("jobs", []))
    if busy:
        st.info("Aguarde a execução atual terminar antes de solicitar uma reavaliação.")
    enabled = bool(status and status.get("enabled") and not busy and identifiers)
    if st.button("Reavaliar métrica selecionada", type="primary", disabled=not enabled):
        command = build_review_command(
            project, record["external_id"], metric, identifiers, evidence, reason
        )
        command_key = "judge-request-" + fingerprint(command)
        command_id = st.session_state.setdefault(command_key, "web:review:" + uuid.uuid4().hex)
        try:
            result = request_control(settings, token, command, command_id)
        except (OSError, ValueError, PermissionError) as exc:
            st.error(
                "O envio não foi confirmado. Tente novamente para consultar o mesmo pedido: "
                + str(exc)
            )
        else:
            st.session_state.pop(command_key, None)
            st.session_state["judge-last-request"] = result["job_id"]
            st.success(f"Reavaliação de {metric} enviada para {len(identifiers)} questão(ões).")
    if st.session_state.get("judge-last-request"):
        job_id = st.session_state["judge-last-request"]
        latest = next(
            (job for job in (status or {}).get("jobs", []) if job["job_id"] == job_id), None
        )
        if latest:
            st.caption(f"Pedido {job_id}: {latest['state']}")
            if latest.get("error"):
                st.error(latest["error"])
