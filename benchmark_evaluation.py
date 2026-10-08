from __future__ import annotations

from copy import deepcopy

from benchmark_config import METRICS
from benchmark_runner import validate_metrics


class MetricEvaluator:
    def __init__(self):
        self.llm = self.embeddings = None

    def evaluate(self, name, artifact):
        if not artifact.get("contexts"):
            raise ValueError("Evaluation requires saved evidence")
        from ragas_compat import build_ragas_run_config, ensure_ragas_langchain_compat

        ensure_ragas_langchain_compat()
        from datasets import Dataset
        from ragas import evaluate, metrics

        from rag_provider import build_embeddings, build_ragas_llm

        if self.llm is None:
            self.llm = build_ragas_llm()
            self.embeddings = build_embeddings(judge=True)
        metric = deepcopy(getattr(metrics, name))
        if hasattr(metric, "max_retries"):
            metric.max_retries = 0
        for prompt in metric.get_prompts().values():
            original = prompt.generate_multiple

            async def bounded(*args, _original=original, **kwargs):
                kwargs["retries_left"] = min(kwargs.get("retries_left", 1), 1)
                return await _original(*args, **kwargs)

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
        )
        return validate_metrics(result.to_pandas().iloc[0].to_dict(), [name])

    def handlers(self):
        return {name: lambda artifact, name=name: self.evaluate(name, artifact) for name in METRICS}
