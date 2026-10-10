from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

import pandas as pd
import streamlit as st

from app.dashboard.auth import authenticate
from app.runtime_config import (
    PROJECTS,
    SECRET_KEYS,
    index_collection_name,
    install_corpus,
    install_dataset,
    read_runtime_config,
    save_runtime_config,
)

MODEL_CACHE_SECONDS = 15 * 60
MAX_CATALOG_BYTES = 12 * 1024 * 1024

DEFAULTS = {
    "LLM_PROVIDER": "openrouter",
    "EMBEDDING_PROVIDER": "openrouter",
    "OPENROUTER_MODEL": "",
    "OPENROUTER_JUDGE_MODEL": "",
    "OPENROUTER_EMBEDDING_MODEL": "openai/text-embedding-3-small",
    "NEO4J_URI": "",
    "NEO4J_USERNAME": "neo4j",
    "NEO4J_DATABASE": "neo4j",
    "TELEGRAM_RESULTS_CHAT_ID": "",
    "TELEGRAM_ALLOWED_USER_IDS": "",
    "TELEGRAM_ENABLED": "false",
    "TELEGRAM_CONTROL_ENABLED": "false",
    "TELEGRAM_SEND_FINAL_FILES": "true",
    "TELEGRAM_PROGRESS_INTERVAL_SECONDS": "15",
    "LLM_MAX_TOKENS": "2048",
    "LLM_TIMEOUT_SECONDS": "600",
    "RAGAS_MAX_TOKENS": "4096",
    "RAGAS_TIMEOUT_SECONDS": "600",
    "RAGAS_MAX_WORKERS": "1",
    "RAGAS_MAX_ATTEMPTS": "1",
    "BENCHMARK_HTTP_ATTEMPTS": "3",
    "BENCHMARK_RETRY_MAX_WAIT_SECONDS": "60",
    "BENCHMARK_MAX_CALLS": "1000",
    "BENCHMARK_MAX_QUESTION_CALLS": "60",
    "BENCHMARK_MAX_TOKENS": "5000000",
    "BENCHMARK_MAX_QUESTION_TOKENS": "100000",
    "BENCHMARK_MAX_PREPARATION_CALLS": "256",
    "BENCHMARK_MAX_SECONDS": "3600",
    "BENCHMARK_QUESTION_TIMEOUT_SECONDS": "900",
    "BENCHMARK_AGENT_RECURSION_LIMIT": "12",
    "BENCHMARK_MAX_STAGE_ATTEMPTS": "3",
    "BENCHMARK_RETRY_COOLDOWN_SECONDS": "60",
}


def _effective(configuration: dict) -> dict[str, str]:
    configured = configuration.get("environment", {})
    return {
        key: str(configured.get(key, os.getenv(key, default))).strip()
        for key, default in DEFAULTS.items()
    } | {
        key: str(configured.get(key, os.getenv(key, ""))).strip()
        for key in SECRET_KEYS
    }


def _normalise_model(item: dict) -> dict | None:
    identifier = item.get("id")
    if not isinstance(identifier, str) or not identifier or len(identifier) > 300:
        return None
    architecture = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    supported = item.get("supported_parameters")
    supported = sorted(value for value in supported if isinstance(value, str)) if isinstance(supported, list) else []
    inputs = architecture.get("input_modalities") or []
    outputs = architecture.get("output_modalities") or []
    if not isinstance(inputs, list):
        inputs = []
    if not isinstance(outputs, list):
        outputs = []
    return {
        "id": identifier,
        "name": str(item.get("name") or identifier)[:300],
        "company": identifier.split("/", 1)[0] if "/" in identifier else "outros",
        "context": int(item.get("context_length") or 0),
        "inputs": sorted(value for value in inputs if isinstance(value, str)),
        "outputs": sorted(value for value in outputs if isinstance(value, str)),
        "parameters": supported,
        "reasoning": "reasoning" in supported,
        "tools": "tools" in supported,
        "web": "web_search_options" in supported,
        "structured": bool({"response_format", "structured_outputs"}.intersection(supported)),
        "embedding": "embedding" in " ".join([*outputs, str(architecture.get("modality", ""))]).lower(),
    }


def fetch_openrouter_models(api_key: str) -> list[dict]:
    if not api_key:
        raise ValueError("Informe a chave do OpenRouter antes de atualizar o catálogo")
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/models",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "LogiBots-RAG-Dashboard/1.0",
            "HTTP-Referer": "https://github.com/RSPLE/benchmarking-rags-dataset",
            "X-Title": "Benchmarking RAGs Dataset",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            data = response.read(MAX_CATALOG_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError("Não foi possível consultar o catálogo do OpenRouter") from exc
    if len(data) > MAX_CATALOG_BYTES:
        raise ValueError("O catálogo do OpenRouter excedeu o limite de segurança")
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("O OpenRouter retornou um catálogo inválido") from exc
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("O OpenRouter retornou um catálogo inválido")
    models = [model for item in rows if isinstance(item, dict) if (model := _normalise_model(item))]
    if not models:
        raise ValueError("Nenhum modelo foi retornado pelo OpenRouter")
    return sorted(models, key=lambda item: item["id"].casefold())


def _catalog(effective: dict) -> list[dict]:
    cached = st.session_state.get("openrouter-model-catalog")
    if cached and time.time() - cached.get("at", 0) < MODEL_CACHE_SECONDS:
        return cached.get("models", [])
    return []


def _model_picker(label: str, key: str, current: str, models: list[dict], *, embedding=False):
    candidates = [row for row in models if row["embedding"] == embedding]
    options = [row["id"] for row in candidates]
    if current and current not in options:
        options.insert(0, current)
    options.append("__manual__")
    selected = st.selectbox(
        label,
        options,
        index=options.index(current) if current in options else 0,
        key=key,
        format_func=lambda value: "Informar ID manualmente…" if value == "__manual__" else value,
    )
    if selected == "__manual__":
        return st.text_input("ID exato do modelo", value=current, key=key + "-manual").strip()
    return selected


def _secret_input(label: str, key: str, current: str, unlocked: bool) -> tuple[str, bool]:
    value = st.text_input(
        label,
        value=current if unlocked else "",
        type="password",
        key=f"parameter-secret-{key}-{'open' if unlocked else 'closed'}",
        placeholder="Mantido sem alteração" if current and not unlocked else "",
        help="Use o ícone de olho do campo para mostrar ou ocultar o valor.",
    )
    clear = st.checkbox("Apagar valor salvo", key=f"parameter-clear-{key}") if unlocked and current else False
    return value.strip(), clear


def _number(label: str, key: str, current: str, *, maximum: int = 100_000_000) -> int:
    try:
        value = max(1, int(current))
    except ValueError:
        value = 1
    return int(st.number_input(label, min_value=1, max_value=maximum, value=min(value, maximum), step=1, key=key))


def parameters_view(settings, identity):
    st.write(
        "Configure provedores, integrações, limites, RAGs e arquivos sem editar código. "
        "As alterações valem para as próximas execuções; o lote em andamento não é modificado."
    )
    try:
        configuration = read_runtime_config(settings.configuration)
    except ValueError as exc:
        st.error(str(exc))
        return
    effective = _effective(configuration)
    unlocked = (
        st.session_state.get("parameters-unlocked-user") == identity["username"]
        and st.session_state.get("parameters-unlocked-until", 0) > time.time()
    )
    if not unlocked:
        for secret_name in SECRET_KEYS:
            st.session_state.pop(f"parameter-secret-{secret_name}-open", None)
    with st.container(border=True):
        st.subheader("Proteção de dados secretos")
        if unlocked:
            remaining = max(1, int((st.session_state["parameters-unlocked-until"] - time.time()) / 60) + 1)
            st.success(f"Segredos liberados nesta sessão por mais {remaining} minuto(s).")
            if st.button("Ocultar segredos agora"):
                st.session_state.pop("parameters-unlocked-user", None)
                st.session_state.pop("parameters-unlocked-until", None)
                st.rerun()
        else:
            st.caption("Confirme sua senha para carregar valores secretos no navegador. A liberação expira em 5 minutos.")
            password = st.text_input(
                "Senha atual do painel",
                type="password",
                key="parameters-reauth-password",
                help="O ícone de olho permite conferir a senha antes da validação.",
            )
            if st.button("Mostrar configurações secretas"):
                if authenticate(settings.database, identity["username"], password):
                    st.session_state["parameters-unlocked-user"] = identity["username"]
                    st.session_state["parameters-unlocked-until"] = time.time() + 300
                    st.session_state.pop("parameters-reauth-password", None)
                    st.rerun()
                else:
                    st.error("Senha inválida ou acesso temporariamente bloqueado.")

    st.subheader("Modelos e credenciais")
    openrouter_key, clear_openrouter = _secret_input(
        "Chave API do OpenRouter", "OPENROUTER_API_KEY", effective["OPENROUTER_API_KEY"], unlocked
    )
    key_for_catalog = openrouter_key or effective["OPENROUTER_API_KEY"]
    if st.button("Atualizar modelos disponíveis no OpenRouter"):
        try:
            with st.spinner("Consultando o catálogo oficial…"):
                models = fetch_openrouter_models(key_for_catalog)
            st.session_state["openrouter-model-catalog"] = {"at": time.time(), "models": models}
            st.success(f"{len(models)} modelo(s) carregado(s).")
        except ValueError as exc:
            st.error(str(exc))
    models = _catalog(effective)
    if models:
        companies = sorted({row["company"] for row in models})
        filter_columns = st.columns(2)
        with filter_columns[0]:
            companies_filter = st.multiselect("Empresa/provedor", companies, key="parameter-model-companies")
            search = st.text_input("Buscar modelo", key="parameter-model-search").strip().casefold()
        with filter_columns[1]:
            capabilities = st.multiselect(
                "Recursos exigidos",
                ["Raciocínio", "Ferramentas / busca web", "Saída estruturada", "Imagem na entrada"],
                key="parameter-model-capabilities",
            )
        filtered = []
        for row in models:
            if companies_filter and row["company"] not in companies_filter:
                continue
            if search and search not in f"{row['id']} {row['name']}".casefold():
                continue
            checks = {
                "Raciocínio": row["reasoning"],
                "Ferramentas / busca web": row["tools"] or row["web"],
                "Saída estruturada": row["structured"],
                "Imagem na entrada": "image" in row["inputs"],
            }
            if all(checks[name] for name in capabilities):
                filtered.append(row)
        models = filtered
        st.caption(
            "O filtro de busca web indica suporte a ferramentas. A disponibilidade efetiva da busca depende do modelo e do roteamento do OpenRouter."
        )
    else:
        st.info("Atualize o catálogo para filtrar e selecionar os modelos disponíveis. IDs já salvos continuam utilizáveis.")
    generation_model = _model_picker(
        "Modelo de geração", "parameter-generation-model", effective["OPENROUTER_MODEL"], models
    )
    judge_model = _model_picker(
        "Modelo do juiz", "parameter-judge-model", effective["OPENROUTER_JUDGE_MODEL"], models
    )
    embedding_model = _model_picker(
        "Modelo de embeddings",
        "parameter-embedding-model",
        effective["OPENROUTER_EMBEDDING_MODEL"],
        models,
        embedding=True,
    )

    st.subheader("Neo4j")
    neo4j_columns = st.columns(2)
    with neo4j_columns[0]:
        neo4j_uri = st.text_input("URI", value=effective["NEO4J_URI"], key="parameter-neo4j-uri")
        neo4j_user = st.text_input("Usuário", value=effective["NEO4J_USERNAME"], key="parameter-neo4j-user")
    with neo4j_columns[1]:
        neo4j_database = st.text_input("Banco", value=effective["NEO4J_DATABASE"], key="parameter-neo4j-database")
        neo4j_password, clear_neo4j = _secret_input(
            "Senha do Neo4j", "NEO4J_PASSWORD", effective["NEO4J_PASSWORD"], unlocked
        )

    st.subheader("Telegram")
    telegram_enabled = st.checkbox(
        "Ativar notificações", value=effective["TELEGRAM_ENABLED"] == "true", key="parameter-telegram-enabled"
    )
    telegram_control = st.checkbox(
        "Permitir controle por usuários autorizados",
        value=effective["TELEGRAM_CONTROL_ENABLED"] == "true",
        key="parameter-telegram-control",
    )
    telegram_token, clear_telegram = _secret_input(
        "Token da API do bot", "TELEGRAM_BOT_TOKEN", effective["TELEGRAM_BOT_TOKEN"], unlocked
    )
    telegram_columns = st.columns(2)
    with telegram_columns[0]:
        telegram_chat = st.text_input(
            "ID do canal/chat de resultados", value=effective["TELEGRAM_RESULTS_CHAT_ID"], key="parameter-telegram-chat"
        )
    with telegram_columns[1]:
        telegram_users = st.text_input(
            "IDs de usuários autorizados (separados por vírgula)",
            value=effective["TELEGRAM_ALLOWED_USER_IDS"],
            key="parameter-telegram-users",
        )

    st.subheader("RAGs habilitados")
    enabled = st.multiselect(
        "Arquiteturas disponíveis para execução",
        list(PROJECTS),
        default=configuration.get("enabled_projects", list(PROJECTS)),
        key="parameter-enabled-rags",
    )
    if not enabled:
        st.warning("Sem RAGs selecionados, novas execuções ficarão bloqueadas.")

    st.subheader("Limites de execução")
    limit_columns = st.columns(2)
    values = {}
    with limit_columns[0]:
        values["LLM_MAX_TOKENS"] = _number("Máximo de tokens da resposta", "parameter-llm-tokens", effective["LLM_MAX_TOKENS"])
        values["LLM_TIMEOUT_SECONDS"] = _number("Timeout do LLM (segundos)", "parameter-llm-timeout", effective["LLM_TIMEOUT_SECONDS"])
        values["BENCHMARK_MAX_TOKENS"] = _number("Máximo de tokens por lote", "parameter-batch-tokens", effective["BENCHMARK_MAX_TOKENS"])
        values["BENCHMARK_MAX_SECONDS"] = _number("Tempo máximo do lote (segundos)", "parameter-batch-time", effective["BENCHMARK_MAX_SECONDS"])
    with limit_columns[1]:
        values["RAGAS_MAX_TOKENS"] = _number("Máximo de tokens do juiz", "parameter-ragas-tokens", effective["RAGAS_MAX_TOKENS"])
        values["RAGAS_TIMEOUT_SECONDS"] = _number("Timeout do juiz (segundos)", "parameter-ragas-timeout", effective["RAGAS_TIMEOUT_SECONDS"])
        values["BENCHMARK_MAX_QUESTION_TOKENS"] = _number("Máximo de tokens por questão", "parameter-question-tokens", effective["BENCHMARK_MAX_QUESTION_TOKENS"])
        values["BENCHMARK_QUESTION_TIMEOUT_SECONDS"] = _number("Timeout por questão (segundos)", "parameter-question-time", effective["BENCHMARK_QUESTION_TIMEOUT_SECONDS"])
    with st.expander("Limites avançados"):
        advanced = {
            "BENCHMARK_MAX_CALLS": "Chamadas máximas por lote",
            "BENCHMARK_MAX_QUESTION_CALLS": "Chamadas máximas por questão",
            "BENCHMARK_MAX_PREPARATION_CALLS": "Chamadas máximas de preparação",
            "BENCHMARK_HTTP_ATTEMPTS": "Tentativas HTTP",
            "BENCHMARK_RETRY_MAX_WAIT_SECONDS": "Espera máxima entre tentativas (s)",
            "BENCHMARK_MAX_STAGE_ATTEMPTS": "Tentativas por etapa",
            "BENCHMARK_RETRY_COOLDOWN_SECONDS": "Intervalo de retomada (s)",
            "BENCHMARK_AGENT_RECURSION_LIMIT": "Limite de recursão dos agentes",
            "RAGAS_MAX_WORKERS": "Workers simultâneos do juiz",
            "RAGAS_MAX_ATTEMPTS": "Tentativas do juiz",
        }
        for name, label in advanced.items():
            values[name] = _number(label, "parameter-" + name.lower(), effective[name])

    if st.button("Salvar parâmetros", type="primary", width="stretch"):
        environment = dict(configuration.get("environment", {}))
        environment.update({name: str(value) for name, value in values.items()})
        environment.update(
            {
                "LLM_PROVIDER": "openrouter",
                "EMBEDDING_PROVIDER": "openrouter",
                "OPENROUTER_MODEL": generation_model,
                "OPENROUTER_JUDGE_MODEL": judge_model,
                "OPENROUTER_EMBEDDING_MODEL": embedding_model,
                "CHROMA_COLLECTION_NAME": index_collection_name(
                    embedding_model,
                    (configuration.get("corpus") or {}).get("sha256", "repository-default"),
                ),
                "NEO4J_URI": neo4j_uri.strip(),
                "NEO4J_USERNAME": neo4j_user.strip(),
                "NEO4J_DATABASE": neo4j_database.strip(),
                "TELEGRAM_ENABLED": str(telegram_enabled).lower(),
                "TELEGRAM_CONTROL_ENABLED": str(telegram_control).lower(),
                "TELEGRAM_SEND_FINAL_FILES": "true",
                "TELEGRAM_BOT_TOKEN": "" if clear_telegram else telegram_token or effective["TELEGRAM_BOT_TOKEN"],
                "TELEGRAM_RESULTS_CHAT_ID": telegram_chat.strip(),
                "TELEGRAM_ALLOWED_USER_IDS": telegram_users.strip(),
                "TELEGRAM_PROGRESS_INTERVAL_SECONDS": effective["TELEGRAM_PROGRESS_INTERVAL_SECONDS"],
                "OPENROUTER_API_KEY": "" if clear_openrouter else openrouter_key or effective["OPENROUTER_API_KEY"],
                "NEO4J_PASSWORD": "" if clear_neo4j else neo4j_password or effective["NEO4J_PASSWORD"],
            }
        )
        try:
            if not enabled:
                raise ValueError("Selecione ao menos um RAG")
            if not environment["OPENROUTER_API_KEY"]:
                raise ValueError("A chave do OpenRouter é obrigatória")
            if not generation_model or not embedding_model:
                raise ValueError("Selecione os modelos de geração e embeddings")
            if telegram_enabled and (
                not environment["TELEGRAM_BOT_TOKEN"] or not telegram_chat.strip()
            ):
                raise ValueError("Telegram ativo exige token e ID do canal/chat")
            if telegram_control and not telegram_users.strip():
                raise ValueError("O controle pelo Telegram exige ao menos um usuário autorizado")
            if "knowledge-enhanced-rag" in enabled and (
                not neo4j_uri.strip() or not environment["NEO4J_PASSWORD"]
            ):
                raise ValueError("O Knowledge-Enhanced RAG exige URI e senha do Neo4j")
            save_runtime_config(
                settings.configuration,
                environment,
                enabled,
                editor=identity["username"],
            )
            st.success("Parâmetros salvos. Eles serão aplicados à próxima execução.")
        except (OSError, ValueError) as exc:
            st.error("Não foi possível salvar: " + str(exc))

    st.divider()
    st.subheader("Documentos PDF")
    corpus = configuration.get("corpus")
    if corpus and corpus.get("files"):
        st.caption(f"Corpus ativo · {len(corpus['files'])} arquivo(s) · hash {corpus['sha256'][:12]}")
        st.dataframe(pd.DataFrame(corpus["files"]), hide_index=True, width="stretch")
    pdfs = st.file_uploader(
        "Subir o conjunto completo de documentos",
        type=["pdf"],
        accept_multiple_files=True,
        key="parameter-pdf-upload",
        help="O envio cria uma nova versão do corpus. A anterior não é alterada.",
    )
    if st.button("Validar e ativar PDFs", disabled=not pdfs):
        try:
            metadata = install_corpus(
                settings.configuration.parent,
                [(upload.name, upload.getvalue()) for upload in pdfs],
            )
            environment = dict(configuration.get("environment", {}))
            environment["DOCS_DIR"] = metadata["path"]
            environment["CHROMA_COLLECTION_NAME"] = index_collection_name(
                environment.get(
                    "OPENROUTER_EMBEDDING_MODEL", effective["OPENROUTER_EMBEDDING_MODEL"]
                ),
                metadata["sha256"],
            )
            save_runtime_config(
                settings.configuration,
                environment,
                configuration["enabled_projects"],
                editor=identity["username"],
                corpus=metadata,
            )
            st.success("Novo corpus validado e ativado para as próximas execuções.")
            st.rerun()
        except (OSError, ValueError) as exc:
            st.error("PDFs rejeitados: " + str(exc))

    st.subheader("Dataset de questões")
    dataset = configuration.get("dataset")
    if dataset:
        st.caption(
            f"Dataset ativo · {dataset['questions']} questão(ões) · {dataset['filename']} · hash {dataset['sha256'][:12]}"
        )
    dataset_file = st.file_uploader(
        "Subir dataset JSON",
        type=["json"],
        accept_multiple_files=False,
        key="parameter-dataset-upload",
        help="Cada item deve conter id, question e ground_truth; use IDs únicos entre Q001 e Q090.",
    )
    if st.button("Validar e ativar dataset", disabled=dataset_file is None):
        try:
            metadata = install_dataset(
                settings.configuration.parent, dataset_file.name, dataset_file.getvalue()
            )
            environment = dict(configuration.get("environment", {}))
            environment["BENCHMARK_DATASET"] = metadata["path"]
            save_runtime_config(
                settings.configuration,
                environment,
                configuration["enabled_projects"],
                editor=identity["username"],
                dataset=metadata,
            )
            st.success("Dataset validado e ativado para as próximas execuções.")
            st.rerun()
        except (OSError, ValueError) as exc:
            st.error("Dataset rejeitado: " + str(exc))

    st.caption(
        "Segredos são armazenados no servidor com permissão 0600. Eles não aparecem em logs, downloads de resultados ou na interface bloqueada."
    )
