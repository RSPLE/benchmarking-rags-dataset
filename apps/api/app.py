"""HTTP API used by the React dashboard to control benchmark runners."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RESULTS_ROOT = Path(os.getenv("BENCHMARK_RESULTS_ROOT", REPOSITORY_ROOT / "results"))
DATASET_PATH = REPOSITORY_ROOT / "eval-dataset" / "qa_dataset_90.json"
REQUEST_TIMEOUT = float(os.getenv("RUNNER_REQUEST_TIMEOUT", "5"))

RAGS: dict[str, dict[str, str]] = {
    "context-rag": {
        "name": "Context RAG",
        "description": "Busca vetorial clássica com resposta restrita ao contexto.",
    },
    "graph-rag": {
        "name": "Graph RAG",
        "description": "Busca vetorial combinada a um grafo NetworkX extraído por LLM.",
    },
    "hybrid-rag": {
        "name": "Hybrid RAG",
        "description": "Recuperação híbrida BM25 e vetorial.",
    },
    "knowledge-enhanced-rag": {
        "name": "Knowledge-Enhanced RAG",
        "description": "Busca vetorial enriquecida por grafo de conhecimento Neo4j.",
    },
    "memory-augmented-rag": {
        "name": "Memory-Augmented RAG",
        "description": "Agente RAG com memória conversacional.",
    },
    "self-rag": {
        "name": "Self-RAG",
        "description": "Geração com autocrítica e uma etapa de refinamento.",
    },
}


class RunRequest(BaseModel):
    projects: list[str] = Field(min_length=1)


def runner_url(project: str) -> str:
    variable = f"RUNNER_URL_{project.upper().replace('-', '_')}"
    return os.getenv(variable, f"http://{project}-runner:8090").rstrip("/")


def dataset_size() -> int:
    try:
        data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 90
    return len(data) if isinstance(data, list) else 90


def _numeric_averages(items: Mapping[str, Any]) -> dict[str, float]:
    values: dict[str, list[float]] = {}
    ignored = {"contexts_count", "input_tokens", "output_tokens", "total_tokens"}
    for state in items.values():
        if state.get("status") != "success":
            continue
        for key, value in state.get("result", {}).items():
            if key in ignored or isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            values.setdefault(key, []).append(float(value))
    return {key: round(sum(group) / len(group), 4) for key, group in values.items() if group}


def checkpoint_summary(project: str) -> dict[str, Any]:
    total = dataset_size()
    path = RESULTS_ROOT / project / "checkpoint.json"
    if not path.exists():
        return {
            "total": total,
            "success": 0,
            "failed": 0,
            "running": 0,
            "pending": total,
            "progress": 0.0,
            "updated_at": None,
            "metrics": {},
        }
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "total": total,
            "success": 0,
            "failed": 0,
            "running": 0,
            "pending": total,
            "progress": 0.0,
            "updated_at": None,
            "metrics": {},
            "checkpoint_error": "checkpoint ilegível",
        }

    items = checkpoint.get("items", {})
    counts = {"success": 0, "failed": 0, "running": 0}
    for state in items.values():
        item_status = state.get("status")
        if item_status in counts:
            counts[item_status] += 1
    completed = counts["success"]
    return {
        "total": total,
        **counts,
        "pending": max(total - sum(counts.values()), 0),
        "progress": round((completed / total) * 100, 1) if total else 0.0,
        "updated_at": checkpoint.get("updated_at"),
        "metrics": _numeric_averages(items),
    }


async def runner_state(project: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(f"{runner_url(project)}/status")
            response.raise_for_status()
            return {"available": True, **response.json()}
    except (httpx.HTTPError, ValueError):
        return {"available": False, "state": "offline", "logs": []}


def display_state(runner: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    if not runner.get("available"):
        return "offline"
    state = str(runner.get("state", "idle"))
    if state in {"queued", "running"}:
        return state
    if result.get("failed", 0) > 0 or state == "failed":
        return "failed"
    if result.get("total", 0) > 0 and result.get("success") == result.get("total"):
        return "succeeded"
    return state


def state_reason(runner: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    state = display_state(runner, result)
    if state == "offline":
        return "O container deste RAG não respondeu à verificação de saúde."
    if state == "queued":
        return "Execução aceita; o container está preparando o processo."
    if state == "running":
        return "Dataset em processamento, uma pergunta por vez."
    if state == "succeeded":
        return "Todas as perguntas foram concluídas; nenhuma será repetida."
    if state == "failed":
        logs = "\n".join(str(line) for line in runner.get("logs", []))
        if runner.get("returncode") in {143, -15} or "Cancelamento solicitado" in logs:
            return "Execução cancelada manualmente; nenhuma pergunta concluída será repetida."
        failures = int(result.get("failed", 0) or 0)
        if failures:
            return f"{failures} pergunta(s) falharam e podem ser retomadas sem repetir sucessos."
        for line in reversed(runner.get("logs", [])):
            message = str(line).strip()
            if "OPENROUTER_API_KEY" in message and "nao encontrada" in message:
                return "Falha de configuração: OPENROUTER_API_KEY não foi encontrada no .env da raiz."
            if message.startswith("RuntimeError:"):
                return f"Falha de configuração: {message.removeprefix('RuntimeError:').strip()}"
        return f"A execução terminou com código {runner.get('returncode', 'desconhecido')}; consulte os logs."
    if result.get("success", 0):
        return "Execução parcial salva; ao retomar, sucessos serão preservados."
    return "Pronto para iniciar; este RAG ainda não foi executado."


async def rag_payload(project: str) -> dict[str, Any]:
    runner, result = await asyncio.gather(
        runner_state(project),
        asyncio.to_thread(checkpoint_summary, project),
    )
    return {
        "id": project,
        **RAGS[project],
        "display_state": display_state(runner, result),
        "state_reason": state_reason(runner, result),
        "runner": runner,
        "result": result,
    }


app = FastAPI(title="RAG Benchmark Dashboard API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", "*").split(",")],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/rags")
async def list_rags() -> dict[str, Any]:
    return {"items": await asyncio.gather(*(rag_payload(project) for project in RAGS))}


@app.get("/api/rags/{project}")
async def get_rag(project: str) -> dict[str, Any]:
    if project not in RAGS:
        raise HTTPException(status_code=404, detail="RAG desconhecido")
    return await rag_payload(project)


async def relay(project: str, action: str) -> dict[str, Any]:
    if project not in RAGS:
        raise HTTPException(status_code=404, detail="RAG desconhecido")
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(f"{runner_url(project)}/{action}")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Runner indisponível: {project}") from exc
    if response.status_code == status.HTTP_409_CONFLICT:
        raise HTTPException(status_code=409, detail=response.json().get("detail", "Conflito"))
    if response.is_error:
        raise HTTPException(status_code=502, detail=f"Falha no runner {project}")
    return response.json()


@app.post("/api/rags/{project}/run", status_code=202)
async def run_rag(project: str) -> dict[str, Any]:
    return await relay(project, "run")


@app.post("/api/rags/{project}/cancel", status_code=202)
async def cancel_rag(project: str) -> dict[str, Any]:
    return await relay(project, "cancel")


@app.post("/api/runs", status_code=202)
async def run_many(request: RunRequest) -> dict[str, Any]:
    projects = list(RAGS) if request.projects == ["all"] else list(dict.fromkeys(request.projects))
    invalid = [project for project in projects if project not in RAGS]
    if invalid:
        raise HTTPException(status_code=422, detail=f"RAGs desconhecidos: {', '.join(invalid)}")
    results = await asyncio.gather(*(relay(project, "run") for project in projects))
    return {"started": projects, "runners": results}


@app.get("/api/results")
async def results() -> dict[str, Any]:
    items = await asyncio.gather(*(rag_payload(project) for project in RAGS))
    return {"items": items}


@app.get("/api/rags/{project}/results.csv")
async def download_results(project: str) -> Response:
    if project not in RAGS:
        raise HTTPException(status_code=404, detail="RAG desconhecido")
    path = RESULTS_ROOT / project / "results.csv"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Resultado ainda não disponível")
    return Response(
        content=path.read_bytes(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{project}-results.csv"'},
    )
