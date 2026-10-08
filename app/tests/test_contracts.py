from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.benchmark.control import Control, Worker, environment_profiles, parse_command
from app.benchmark.export import RESULT_COLUMNS, result_bytes
from app.benchmark.pipeline import evaluation_documents
from app.benchmark.runner import run_resumable_benchmark
from app.benchmark.storage import atomic_json
from app.cli import build_parser
from app.telegram.notifier import Notifier, TelegramClient
from app.telegram.setup import check_configuration

ROOT = Path(__file__).resolve().parents[2]


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_original_prompts_match_pinned_upstream_revisions(self):
        contracts = json.loads((ROOT / "app/tests/fixtures/upstream_contracts.json").read_text())
        for project, contract in contracts.items():
            self.assertEqual(contract["columns"], list(RESULT_COLUMNS))
            for spec in contract["prompts"]:
                with self.subTest(project=project, prompt=spec["name"]):
                    tree = ast.parse((ROOT / "app" / "rags" / project / spec["file"]).read_text())
                    scope = (
                        next(
                            n
                            for n in ast.walk(tree)
                            if isinstance(n, ast.FunctionDef) and n.name == spec["function"]
                        )
                        if spec["function"]
                        else tree
                    )
                    node = next(
                        n.value
                        for n in ast.walk(scope)
                        if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == spec["name"] for t in n.targets)
                    )
                    digest = hashlib.sha256(
                        ast.dump(node, include_attributes=False).encode()
                    ).hexdigest()
                    self.assertEqual(digest, spec["sha256"])

    def test_primary_csv_and_telegram_match_original_contract_for_every_rag(self):
        questions = [
            {"id": "Q001", "question": "Questão; com acentuação", "ground_truth": "Reference"},
            {"id": "Q002", "question": "Pending", "ground_truth": "Reference"},
        ]
        dataset = self.root / "dataset.json"
        atomic_json(dataset, questions)
        for project in environment_profiles():
            directory = self.root / project / "experiment"
            run_resumable_benchmark(
                project,
                lambda question: {
                    "answer": "Private answer",
                    "contexts": ["Evidence"],
                    "answer_response_time_seconds": 1.25,
                    "answer_input_tokens": 11,
                    "answer_output_tokens": 7,
                    "answer_total_tokens": 18,
                },
                dataset_path=dataset,
                output_dir=directory,
                question_limit=1,
                metric_evaluators={
                    name: lambda artifact, name=name: {name: 0.75} for name in RESULT_COLUMNS[1:5]
                },
            )
            actual = (directory / "results.csv").read_bytes()
            self.assertTrue(actual.startswith(b"\xef\xbb\xbf"))
            reader = csv.DictReader(io.StringIO(actual.decode("utf-8-sig")), delimiter=";")
            self.assertEqual(reader.fieldnames, list(RESULT_COLUMNS))
            rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["question"], questions[0]["question"])
            self.assertEqual(rows[0]["answer_total_tokens"], "18")
            self.assertEqual((directory / f"{project}-run-1_1.csv").read_bytes(), actual)
            self.assertIn("Private answer", (directory / "results_detailed.csv").read_text())
            self.assertNotIn(b"Private answer", actual)
            public = json.loads((directory / "public_results.json").read_text())
            self.assertEqual(result_bytes(public["rows"]), actual)
        notifier = Notifier(self.root, self.root / "outbox.db", Mock(), "-100123", send_files=True)
        self.addCleanup(notifier.db.close)
        notifier.scan()
        files = notifier.db.execute(
            "SELECT payload,document FROM outbox WHERE method='sendDocument'"
        ).fetchall()
        csv_files = [
            data
            for payload, data in files
            if json.loads(payload).get("_filename", "results.csv") == "results.csv"
        ]
        self.assertEqual(len(csv_files), 6)
        self.assertTrue(all(data == actual for data in csv_files))

    def test_cli_and_telegram_share_flags(self):
        flags = "--questions 3 --selection failed --provider openrouter --mode full --max-calls 50 --max-seconds 600 --question-timeout 120 --repetition 2".split()
        cli = build_parser().parse_args(["run", "context-rag", *flags])
        telegram = parse_command("/executar context-rag " + " ".join(flags))
        for name, value in telegram["options"].items():
            self.assertEqual(getattr(cli, name), value)
        self.assertEqual(len(parse_command("/executar all --limit 1")["projects"]), 6)
        self.assertEqual(parse_command("/start")["action"], "ajuda")
        for invalid in ("--shell rm", "--questions 0", "--max-calls -1", "--prov openai"):
            with self.assertRaises(ValueError):
                parse_command("/executar context-rag " + invalid)

    def test_preflight_reads_the_same_root_environment_and_corpus_path(self):
        from app import cli

        def load():
            os.environ["DOCS_DIR"] = "corpus"

        with (
            patch.object(sys, "argv", ["main.py", "preflight"]),
            patch.object(cli, "load_environment", side_effect=load) as loader,
            patch.object(cli.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run,
        ):
            self.assertEqual(cli.main(), 0)
        loader.assert_called_once()
        self.assertEqual(run.call_args.kwargs["env"]["DOCS_DIR"], str(ROOT / "corpus"))

    def test_batch_queue_is_atomic_idempotent_and_stops_after_failure(self):
        control = Control(
            self.root / "results",
            self.root / "queue.db",
            {123},
            enabled=True,
            profiles=environment_profiles(),
        )
        self.addCleanup(control.db.close)
        request = {
            "user_id": 123,
            "command_id": "batch",
            "text": "/executar all --questions 1 --max-calls 30 --max-seconds 60",
        }
        response = control.handle(request)
        self.assertEqual(control.handle(request), response)
        self.assertEqual(len(response["jobs"]), 6)
        worker = Worker(control)
        with patch("app.benchmark.control.subprocess.Popen") as launch:
            launch.return_value.poll.return_value = None
            worker.tick()
            self.assertEqual(launch.call_args.kwargs["env"]["BENCHMARK_MAX_CALLS"], "30")
            self.assertEqual(worker.max_seconds, 60)
            worker.process.poll.return_value = 1
            worker.tick()
        states = [row[0] for row in control.db.execute("SELECT state FROM jobs ORDER BY rowid")]
        self.assertEqual(states, ["stopped", *(["cancelled"] * 5)])

    def test_rewritten_agent_query_keeps_original_evaluation_query(self):
        docs = [SimpleNamespace(page_content="same"), SimpleNamespace(page_content="same")]
        messages = [
            SimpleNamespace(
                type="ai",
                tool_calls=[{"id": "a", "name": "retrieve", "args": {"query": "rewrite"}}],
            ),
            SimpleNamespace(type="tool", tool_call_id="a", status="success", artifact=docs),
        ]
        store = Mock()
        store.similarity_search.return_value = docs
        result = evaluation_documents(messages, "original", store, tool_name="retrieve", k=5)
        self.assertIs(result, docs)
        store.similarity_search.assert_called_once_with("original", k=5)

    def test_token_whitespace_is_normalized_and_invalid_errors_hide_secrets(self):
        self.assertEqual(TelegramClient(" 123456:fake_token\n").token, "123456:fake_token")
        with self.assertRaisesRegex(ValueError, "Invalid Telegram token format"):
            TelegramClient("private secret invalid")

    def test_channel_check_is_read_only_and_requires_post_permission(self):
        env = {"TELEGRAM_RESULTS_CHAT_ID": "-100123", "TELEGRAM_ALLOWED_USER_IDS": "456"}
        client = Mock()
        replies = [
            {"id": 789, "username": "benchmark_bot"},
            {"url": ""},
            {"id": -100123, "type": "channel", "title": "Results"},
            {"status": "administrator", "can_post_messages": True},
        ]
        client.call.side_effect = replies
        result = check_configuration(env, client=client)
        self.assertEqual(result["channel_id"], -100123)
        self.assertEqual(result["messages_sent"], 0)
        self.assertEqual(
            [call.args[0] for call in client.call.call_args_list],
            ["getMe", "getWebhookInfo", "getChat", "getChatMember"],
        )
        client.call.side_effect = [
            *replies[:-1],
            {"status": "administrator", "can_post_messages": False},
        ]
        with self.assertRaisesRegex(ValueError, "permission to post"):
            check_configuration(env, client=client)
