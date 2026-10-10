from __future__ import annotations

import uuid
from copy import deepcopy

from app.benchmark.config import METRICS
from app.benchmark.judge_audit import JudgeResult, JudgeTrace
from app.benchmark.runner import validate_metrics


class MetricEvaluator:
    def __init__(self):
        self.llm = self.embeddings = None

    def evaluate(self, name, artifact):
        if name not in METRICS:
            raise ValueError("Unknown metric")
        trace = JudgeTrace(name, artifact)
        try:
            values = self._evaluate(name, artifact, trace)
        except BaseException as exc:
            exc.judge_trace_id = trace.trace_id
            trace.emit("failed", error_type=type(exc).__name__, error=str(exc))
            raise
        trace.emit("finished", values=values)
        return JudgeResult(values, trace.trace_id)

    def _evaluate(self, name, artifact, trace):
        if not artifact.get("contexts"):
            raise ValueError("Evaluation requires saved evidence")
        from app.providers.ragas_compat import build_ragas_run_config, ensure_ragas_langchain_compat

        ensure_ragas_langchain_compat()
        from datasets import Dataset
        from ragas import evaluate, metrics

        from app.providers.models import build_embeddings, build_ragas_llm

        if self.llm is None:
            self.llm = build_ragas_llm()
            self.embeddings = build_embeddings(judge=True)
        metric = deepcopy(getattr(metrics, name))
        if hasattr(metric, "max_retries"):
            metric.max_retries = 0
        for prompt in metric.get_prompts().values():
            original = prompt.generate_multiple

            async def bounded(*args, _original=original, _prompt=prompt, **kwargs):
                kwargs["retries_left"] = min(kwargs.get("retries_left", 1), 1)
                invocation_id = uuid.uuid4().hex
                trace.emit(
                    "prompt",
                    invocation_id=invocation_id,
                    name=_prompt.name,
                    instruction=_prompt.instruction,
                    data=kwargs.get("data", args[0] if args else None),
                )
                outputs = await _original(*args, **kwargs)
                trace.emit(
                    "judgment", invocation_id=invocation_id, name=_prompt.name, outputs=outputs
                )
                return outputs

            prompt.generate_multiple = bounded
        result = evaluate(
            Dataset.from_list(
                [{key: artifact[key] for key in ("question", "answer", "contexts", "ground_truth")}]
            ),
            metrics=[metric],
            llm=self.llm,
            embeddings=self.embeddings,
            run_config=build_ragas_run_config(),
            raise_exceptions=True,
            show_progress=False,
            callbacks=[trace.callback()],
        )
        return validate_metrics(result.to_pandas().iloc[0].to_dict(), [name])

    def handlers(self):
        return {name: lambda artifact, name=name: self.evaluate(name, artifact) for name in METRICS}
