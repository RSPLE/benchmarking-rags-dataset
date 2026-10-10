from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import uuid
from pathlib import Path

from app.benchmark.config import METRICS
from app.benchmark.judge_audit import judge_configuration, judge_context, read_events
from app.benchmark.runner import error_category, pause_signals, validate_answer, validate_metrics
from app.benchmark.storage import (
    append_event,
    atomic_json,
    exclusive_lock,
    fingerprint,
    now,
    sanitize,
)
from app.benchmark.usage import BudgetExceeded, UsageLedger, budget_cause

EVIDENCE_MODES = {
    "original": "Contextos da avaliação original",
    "generation": "Evidências usadas na resposta",
}


def question_ids(value):
    items = value.split(",")
    if (
        not items
        or len(items) > 90
        or len(set(items)) != len(items)
        or any(not re.fullmatch(r"Q\d{3}", item) or not 1 <= int(item[1:]) <= 90 for item in items)
    ):
        raise argparse.ArgumentTypeError("Use IDs únicos entre Q001 e Q090, separados por vírgulas")
    return items


def add_review_options(parser):
    parser.add_argument("--metric", choices=METRICS, required=True)
    parser.add_argument("--question-ids", type=question_ids, required=True)
    parser.add_argument("--evidence", choices=EVIDENCE_MODES, default="original")
    parser.add_argument("--reason", default="Revisão solicitada pelo operador")


def review_inputs(directory, identifiers, metric, evidence="original"):
    if metric not in METRICS or evidence not in EVIDENCE_MODES:
        raise ValueError("Métrica ou evidência inválida")
    question_ids(",".join(identifiers))
    directory = Path(directory)
    checkpoint = json.loads((directory / "checkpoint.json").read_bytes())
    manifest = json.loads((directory / "manifest.json").read_bytes())
    if manifest.get("experiment_id") != fingerprint(
        {k: v for k, v in manifest.items() if k != "experiment_id"}
    ):
        raise ValueError("Identidade do experimento inválida")
    if checkpoint.get("version") != 2 or checkpoint.get("legacy"):
        raise ValueError("Reavaliação exige checkpoint v2 e as evidências originais salvas")
    if checkpoint.get("manifest_fingerprint") != fingerprint(manifest) or checkpoint.get(
        "project"
    ) != manifest.get("project"):
        raise ValueError("Manifesto incompatível com o checkpoint")
    inputs = []
    for identifier in identifiers:
        state = checkpoint["items"].get(identifier, {})
        artifact = state.get("artifact")
        if not artifact:
            raise ValueError(
                f"{identifier}: resposta/contextos não foram salvos; não é possível reavaliar apenas a métrica"
            )
        if fingerprint(artifact) != state.get("artifact_sha256"):
            raise ValueError(f"{identifier}: evidências salvas foram alteradas")
        validate_answer(artifact, artifact)
        if (
            not isinstance(artifact.get("ground_truth"), str)
            or not artifact["ground_truth"].strip()
        ):
            raise ValueError(f"{identifier}: gabarito ausente")
        candidate = copy.deepcopy(artifact)
        if evidence == "generation":
            candidate["contexts"] = candidate.get("generation_contexts")
        if (
            not isinstance(candidate.get("contexts"), list)
            or not candidate["contexts"]
            or any(
                not isinstance(context, str) or not context.strip()
                for context in candidate["contexts"]
            )
        ):
            raise ValueError(f"{identifier}: contextos desta modalidade não foram salvos")
        entry = state.get("metrics", {}).get(metric, {})
        previous = (
            entry.get("value")
            if entry.get("status") == "success"
            else state.get("result", {}).get(metric)
        )
        inputs.append((identifier, candidate, previous, state["artifact_sha256"]))
    return manifest, inputs


def run_review(
    directory,
    identifiers,
    metric,
    *,
    evidence="original",
    reason="Revisão solicitada pelo operador",
    request_id=None,
    evaluator=None,
    budget_root=None,
):
    """Rejudge saved answers, leaving original checkpoints and scores untouched."""
    import app.benchmark.usage
    from app.benchmark.evaluation import MetricEvaluator

    directory = Path(directory).resolve()
    budget_root = Path(
        budget_root or os.getenv("BENCHMARK_BUDGET_DIR", directory.parents[1])
    ).resolve()
    request_id = request_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", request_id) or len(reason) > 1000:
        raise ValueError("Identificador ou motivo inválido")
    path = directory / "judge_reviews.jsonl"
    request = {
        "question_ids": identifiers,
        "metric": metric,
        "evidence": evidence,
        "reason": reason,
    }
    with (
        exclusive_lock(budget_root / ".worker.lock"),
        exclusive_lock(directory / ".lock"),
        pause_signals() as paused,
    ):
        previous = [
            e
            for e in read_events(path.read_bytes() if path.exists() else b"")
            if e["request_id"] == request_id
        ]
        if previous:
            if previous[0].get("request") != request:
                raise ValueError("Pedido já utilizado com outros parâmetros")
            if previous[-1]["kind"] == "finished":
                return previous[-1]
            raise ValueError(
                "Pedido anterior interrompido; consulte os resultados parciais e envie um novo pedido explícito"
            )
        manifest, inputs = review_inputs(directory, identifiers, metric, evidence)
        (directory / "pause.request").unlink(missing_ok=True)
        ledger = UsageLedger(directory / "usage.jsonl", budget_root)
        ledger.pause_event = paused
        run_id = ledger.run_id

        def event(kind, **values):
            row = {
                "request_id": request_id,
                "run_id": run_id,
                "at": now(),
                "kind": kind,
                "metric": metric,
                "evidence": evidence,
                **values,
            }
            append_event(path, row)
            return row

        event(
            "started",
            request=request,
            configuration=judge_configuration(),
            source_experiment=manifest["experiment_id"],
        )
        if os.getenv("BENCHMARK_JOB_FILE"):
            atomic_json(
                os.environ["BENCHMARK_JOB_FILE"],
                {
                    "project": manifest["project"],
                    "experiment_id": manifest["experiment_id"],
                    "directory": str(directory),
                    "run_id": run_id,
                    "review": request_id,
                },
            )
        evaluator = evaluator or MetricEvaluator()
        prior_ledger = app.benchmark.usage.ACTIVE_LEDGER
        app.benchmark.usage.ACTIVE_LEDGER = ledger
        success = failed = consecutive = 0
        previous_category = None
        try:
            for identifier, artifact, old_value, source_hash in inputs:
                if paused.is_set() or (directory / "pause.request").exists():
                    break
                ledger.set_stage(identifier, "judge", metric)
                try:
                    if ledger.remaining_seconds() <= 0:
                        raise BudgetExceeded("Tempo máximo de reavaliação atingido")
                    with judge_context(
                        directory, identifier, run_id, review_id=request_id, evidence=evidence
                    ):
                        value = evaluator.evaluate(metric, artifact)
                    score = validate_metrics(value, [metric])[metric]
                    event(
                        "result",
                        question_id=identifier,
                        status="success",
                        previous_value=old_value,
                        value=score,
                        source_artifact_sha256=source_hash,
                        input_sha256=fingerprint(artifact),
                        judge_trace_id=getattr(value, "judge_trace_id", None),
                    )
                    success += 1
                    consecutive = 0
                except Exception as exc:
                    failed += 1
                    event(
                        "result",
                        question_id=identifier,
                        status="failed",
                        previous_value=old_value,
                        source_artifact_sha256=source_hash,
                        input_sha256=fingerprint(artifact),
                        judge_trace_id=getattr(exc, "judge_trace_id", None),
                        error_type=type(exc).__name__,
                        error=sanitize(str(exc)),
                    )
                    category = error_category(exc)
                    consecutive = consecutive + 1 if category == previous_category else 1
                    previous_category = category
                    if (
                        budget_cause(exc)
                        or category in {"configuration", "credit"}
                        or consecutive >= 3
                    ):
                        break
        finally:
            app.benchmark.usage.ACTIVE_LEDGER = prior_ledger
        return event(
            "finished",
            success=success,
            failed=failed,
            pending=len(inputs) - success - failed,
            usage=ledger.summary(),
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    add_review_options(parser)
    parser.add_argument("--request-id")
    args = parser.parse_args()
    result = run_review(
        args.directory,
        args.question_ids,
        args.metric,
        evidence=args.evidence,
        reason=args.reason,
        request_id=args.request_id,
    )
    print(json.dumps(result, ensure_ascii=False))
    return int(bool(result["failed"] or result["pending"]))


if __name__ == "__main__":
    sys.exit(main())
