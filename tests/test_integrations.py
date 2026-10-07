from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
AVAILABLE = importlib.util.find_spec("langchain_openai") is not None


@unittest.skipUnless(AVAILABLE, "Run with a RAG's locked environment")
class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "test-key",
                "OPENROUTER_MODEL": "test-generation",
                "OPENROUTER_JUDGE_MODEL": "test-judge",
                "LLM_PROVIDER": "openrouter",
                "EMBEDDING_PROVIDER": "openrouter",
                "OPENROUTER_EMBEDDING_MODEL": "test-embedding",
                "RAGAS_DO_NOT_TRACK": "true",
                "LANGCHAIN_TRACING_V2": "false",
                "ANONYMIZED_TELEMETRY": "false",
            },
            clear=True,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        from ragas_compat import ensure_ragas_langchain_compat

        ensure_ragas_langchain_compat()

    def test_pipeline_import_has_no_model_calls_or_index_creation(self):
        project = os.getenv("TEST_PROJECT") or Path(sys.executable).absolute().parents[2].name
        if project not in {p.name for p in (ROOT / "rags").iterdir()}:
            project = "context-rag"
        import runpy

        project_dir = ROOT / "rags" / project
        sys.path.insert(0, str(project_dir))
        self.addCleanup(sys.path.remove, str(project_dir))
        with (
            patch("dotenv.load_dotenv"),
            patch(
                "rag_provider.build_llm", side_effect=AssertionError("model construction at import")
            ),
            patch(
                "rag_provider.build_embeddings",
                side_effect=AssertionError("embedding construction at import"),
            ),
        ):
            module = runpy.run_path(str(project_dir / "main.py"), run_name="pipeline_import_test")
            self.assertIn("prepare", module)

    def test_generation_evidence_and_memory_isolation(self):
        import inspect
        import runpy

        from langchain_core.documents import Document
        from langchain_core.messages import AIMessage, ToolMessage

        project = Path(sys.executable).absolute().parents[2].name
        project_dir = ROOT / "rags" / project
        sys.path.insert(0, str(project_dir))
        self.addCleanup(sys.path.remove, str(project_dir))
        with patch("dotenv.load_dotenv"):
            module = runpy.run_path(str(project_dir / "main.py"), run_name="test_generation")
        docs = [Document(page_content="actual evidence", metadata={"source": "book"})]
        llm = Mock()
        llm.invoke.side_effect = [
            SimpleNamespace(text="candidate"),
            SimpleNamespace(text="NÃO"),
            SimpleNamespace(text="refined"),
        ]
        retriever = Mock()
        retriever.invoke.return_value = docs
        if project in {"context-rag", "hybrid-rag", "self-rag"}:
            result = module[project.replace("-", "_")]("question", retriever, llm)
            self.assertEqual(result[1], ["actual evidence"])
            retriever.invoke.assert_called_once_with("question")
            self.assertEqual(result[2], [{"source": "book"}])
            if project == "self-rag":
                self.assertEqual(llm.invoke.call_count, 3)
                self.assertIn("actual evidence", llm.invoke.call_args_list[1].args[0])
        elif project in {"graph-rag", "memory-augmented-rag"}:
            messages = [
                ToolMessage(content="actual evidence", tool_call_id="call-1", name="retrieve"),
                AIMessage(content="candidate"),
            ]
            agent = Mock()
            agent.stream.return_value = iter([{"messages": messages}])
            store = Mock()
            store.similarity_search.side_effect = AssertionError("redundant retrieval")
            if project == "graph-rag":
                function = inspect.unwrap(module["query_graph_rag"])
                with patch.dict(function.__globals__, {"agent": agent, "vector_store": store}):
                    result = function("question")
            else:
                result = module["run_agent_and_collect_data"](agent, store, "question", "reference")
            self.assertEqual(result["contexts"], ["actual evidence"])
            self.assertIn("recursion_limit", agent.stream.call_args.kwargs["config"])
        else:
            chatbot = module["Chatbot"].__new__(module["Chatbot"])
            chatbot.llm, chatbot.retriever, chatbot.memorias = llm, retriever, {}
            retriever.retrieve.return_value = {
                "docs": docs,
                "kg_facts": "graph evidence",
                "prerequisites": ["prerequisite"],
                "next_concepts": ["next"],
                "kg_mode": "required",
            }
            result = chatbot.chat("question", session_id=None)
            retriever.retrieve.assert_called_once_with("question")
            self.assertEqual(chatbot.memorias, {})
            self.assertIn("graph evidence", result["contexts"])

    def test_sync_and_async_provider_accounting_and_judge_selection(self):
        import asyncio

        import httpx

        import benchmark_usage
        from benchmark_usage import UsageLedger
        from rag_provider import build_embeddings, build_llm, build_ragas_llm

        ledger = UsageLedger(Path(self.temp.name) / "usage.jsonl")
        benchmark_usage.ACTIVE_LEDGER = ledger
        self.addCleanup(setattr, benchmark_usage, "ACTIVE_LEDGER", None)
        seen = []

        def response(request):
            payload = json.loads(request.content)
            seen.append(payload["model"])
            if request.url.path.endswith("embeddings"):
                body = {
                    "object": "list",
                    "data": [
                        {"object": "embedding", "index": i, "embedding": [0.1, 0.9]}
                        for i, _ in enumerate(payload["input"])
                    ],
                    "usage": {"total_tokens": 2, "cost": 0.01},
                }
            else:
                body = {
                    "id": "response-1",
                    "object": "chat.completion",
                    "created": 1,
                    "model": payload["model"],
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "answer"},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 1,
                        "total_tokens": 3,
                        "cost": 0.01,
                    },
                }
            return httpx.Response(200, json=body, request=request)

        async def async_response(_self, request):
            return response(request)

        with (
            patch.object(
                httpx.HTTPTransport, "handle_request", lambda _self, request: response(request)
            ),
            patch.object(httpx.AsyncHTTPTransport, "handle_async_request", async_response),
        ):
            llm = build_llm()
            self.assertEqual(llm.invoke("question").content, "answer")
            judge = build_ragas_llm()
            self.assertEqual(
                asyncio.run(asyncio.wait_for(judge.langchain_llm.ainvoke("evaluate"), 10)).content,
                "answer",
            )
            embedding = build_embeddings()
            self.assertEqual(embedding.embed_query("question"), [0.1, 0.9])
        self.assertEqual(seen, ["test-generation", "test-judge", "test-embedding"])
        self.assertEqual(ledger.summary()["calls"], 3)
        self.assertAlmostEqual(ledger.summary()["cost_usd"], 0.03)

    def test_http_retry_and_budget_are_enforced_below_sdk(self):
        import httpx

        import benchmark_usage
        from benchmark_runner import error_category
        from benchmark_usage import UsageLedger
        from rag_provider import build_llm

        for status, limit, expected in [(403, 10, 1), (429, 10, 2), (503, 1, 1), (200, 10, 1)]:
            with (
                self.subTest(status=status),
                patch.dict(os.environ, {"BENCHMARK_MAX_CALLS": str(limit)}),
            ):
                ledger = UsageLedger(Path(self.temp.name) / f"{status}.jsonl")
                benchmark_usage.ACTIVE_LEDGER = ledger
                self.addCleanup(setattr, benchmark_usage, "ACTIVE_LEDGER", None)
                calls = []

                def respond(_transport, request, calls=calls, status=status):
                    calls.append(request)
                    if len(calls) == 1:
                        return httpx.Response(
                            status,
                            json={
                                "error": {
                                    "message": "rejected",
                                    "code": 403 if status == 200 else status,
                                }
                            },
                            headers={"retry-after": "2"},
                            request=request,
                        )
                    return httpx.Response(
                        200,
                        json={
                            "id": "test",
                            "model": "test",
                            "object": "chat.completion",
                            "created": 1,
                            "choices": [
                                {
                                    "index": 0,
                                    "finish_reason": "stop",
                                    "message": {"role": "assistant", "content": "answer"},
                                }
                            ],
                        },
                        request=request,
                    )

                with (
                    patch.object(httpx.HTTPTransport, "handle_request", respond),
                    patch("benchmark_usage.sleep") as sleep,
                ):
                    if status == 429:
                        self.assertEqual(build_llm().invoke("question").content, "answer")
                        sleep.assert_called_once_with(2.0)
                    else:
                        with self.assertRaises(Exception) as raised:
                            build_llm().invoke("question")
                        self.assertEqual(
                            error_category(raised.exception),
                            "configuration" if status in {403, 200} else "BudgetExceeded",
                        )
                self.assertEqual(len(calls), expected)
                self.assertEqual(ledger.calls, expected)

    def test_real_ragas_four_metrics_with_fake_judge(self):
        from langchain_core.embeddings import Embeddings
        from langchain_core.outputs import Generation, LLMResult
        from ragas.llms.base import BaseRagasLLM

        from benchmark_config import METRICS
        from benchmark_evaluation import MetricEvaluator

        class Judge(BaseRagasLLM):
            def is_finished(self, response):
                return True

            def generate_text(self, prompt, n=1, **kwargs):
                text = prompt.to_string()
                if '"classifications"' in text:
                    result = {
                        "classifications": [
                            {"statement": "A fact", "reason": "supported", "attributed": 1}
                        ]
                    }
                elif '"noncommittal"' in text:
                    result = {"question": "Question", "noncommittal": 0}
                elif '"statements"' in text and '"verdict"' in text:
                    result = {
                        "statements": [{"statement": "A fact", "reason": "supported", "verdict": 1}]
                    }
                elif '"statements"' in text:
                    result = {"statements": ["A fact"]}
                else:
                    result = {"reason": "supported", "verdict": 1}
                return LLMResult(
                    generations=[[Generation(text=json.dumps(result)) for _ in range(n)]]
                )

            async def agenerate_text(self, prompt, n=1, **kwargs):
                return self.generate_text(prompt, n=n, **kwargs)

        class Vectors(Embeddings):
            def embed_documents(self, texts):
                return [[1.0, 0.0] for _ in texts]

            def embed_query(self, text):
                return [1.0, 0.0]

        evaluator = MetricEvaluator()
        evaluator.llm = Judge()
        evaluator.embeddings = Vectors()
        artifact = {
            "question": "Question",
            "answer": "A fact",
            "contexts": ["A fact"],
            "ground_truth": "A fact",
        }
        for name in METRICS:
            with self.subTest(metric=name):
                self.assertAlmostEqual(evaluator.evaluate(name, artifact)[name], 1.0)

    def test_real_chroma_resume_uses_no_additional_embeddings(self):
        from langchain_chroma import Chroma
        from langchain_core.documents import Document
        from langchain_core.embeddings import Embeddings

        from benchmark_index import ensure_index

        class FakeEmbeddings(Embeddings):
            calls = 0

            def embed_documents(self, texts):
                self.calls += len(texts)
                return [[0.2, 0.8] for _ in texts]

            def embed_query(self, text):
                return [0.2, 0.8]

        embedding = FakeEmbeddings()
        store = Chroma(
            collection_name="test_collection",
            persist_directory=self.temp.name,
            embedding_function=embedding,
        )
        docs = [Document(page_content="document", metadata={"source": "test"})]
        manifest = Path(self.temp.name) / "index.json"
        ensure_index(store, docs, manifest, "identity")
        ensure_index(store, docs, manifest, "identity")
        self.assertEqual(embedding.calls, 1)
        self.assertEqual(len(store.similarity_search("document")), 1)


if __name__ == "__main__":
    unittest.main()
