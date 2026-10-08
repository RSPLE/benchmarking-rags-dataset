from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from benchmark_storage import exclusive_lock, fingerprint


class TelegramError(RuntimeError):
    def __init__(self, code, retry_after=30):
        super().__init__(f"Telegram delivery failed ({code})")
        self.retry_after = retry_after


class TelegramClient:
    def __init__(self, token):
        self.token = token

    def call(self, method, payload, document=None):
        payload = dict(payload)
        filename = payload.pop("_filename", "results.csv")
        if filename not in {"results.csv", "results.json"}:
            raise ValueError("Invalid document name")
        mime = "application/json" if filename.endswith(".json") else "text/csv"
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        if document is None:
            data = json.dumps(payload).encode()
            content_type = "application/json"
        else:
            boundary = uuid.uuid4().hex
            sections = []
            for key, value in payload.items():
                sections.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
                )
            sections.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
                + document
                + b"\r\n"
            )
            sections.append(f"--{boundary}--\r\n".encode())
            data = b"".join(sections)
            content_type = f"multipart/form-data; boundary={boundary}"
        request = urllib.request.Request(
            url, data=data, headers={"Content-Type": content_type}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                result = json.load(exc)
            except ValueError:
                raise TelegramError(exc.code) from None
        except (urllib.error.URLError, TimeoutError):
            raise TelegramError("network") from None
        if not result.get("ok"):
            raise TelegramError(
                result.get("error_code", "unknown"),
                result.get("parameters", {}).get("retry_after", 30),
            )
        return result.get("result")


def format_summary(summary):
    lines = [
        f"{summary['project']} / {summary['experiment_id']}",
        f"Estado: {summary.get('operation')}",
        f"Válidos: {summary['success']} | Falhos: {summary['failed']} | Pendentes: {summary['pending']} | Parciais: {summary['partial']}",
    ]
    for metric, data in summary.get("metrics", {}).items():
        mean = f"{data['mean']:.4f}" if data["mean"] is not None else "indisponível"
        lines.append(f"{metric}: {mean} (n={data['count']})")
    usage = summary.get("usage", {})
    cost = usage.get("cost_usd")
    lines.extend(
        [
            f"Modo / Mode: {summary.get('mode', 'unknown')}",
            f"Pergunta / Question: {summary.get('current_question') or '-'} | Etapa / Stage: {summary.get('current_stage') or '-'}",
            f"Duração / Duration (s): {summary.get('elapsed_seconds', 0):.1f}",
            f"Consumo desconhecido / Unknown cost calls: {usage.get('unknown_cost_calls', 0)}",
        ]
    )
    if usage.get("remaining_cost_usd") is not None:
        lines.append(
            f"Orçamento restante estimado / Estimated remaining USD: {usage['remaining_cost_usd']}"
        )
    if summary.get("alert"):
        lines.append(f"Alerta / Alert: {summary['alert']}")
    lines.append(f"Custo da rodada USD: {cost if cost is not None else 'desconhecido'}")
    return "\n".join(lines)


class Notifier:
    def __init__(self, root, database, client, chat_id, *, send_files=False):
        self.root = Path(root).resolve()
        self.client, self.chat_id, self.send_files = client, str(chat_id), send_files
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, method TEXT NOT NULL, payload TEXT NOT NULL, document BLOB, sent INTEGER DEFAULT 0, next_attempt REAL DEFAULT 0)"
        )
        self.db.commit()

    def scan(self):
        paths = [self.root / "control_events.jsonl", *self.root.glob("*/*/public_events.jsonl")]
        for event_path in paths:
            if not event_path.is_file() or not event_path.resolve().is_relative_to(self.root):
                continue
            for line in event_path.read_text().splitlines():
                try:
                    event = json.loads(line)
                    identifier = fingerprint({"chat": self.chat_id, "event": event["event_id"]})
                    payload = {
                        "chat_id": self.chat_id,
                        "text": json.dumps(event, ensure_ascii=False, indent=2)[:3500],
                    }
                    self.db.execute(
                        "INSERT OR IGNORE INTO outbox (id,method,payload) VALUES (?, 'sendMessage', ?)",
                        (identifier, json.dumps(payload)),
                    )
                except (ValueError, KeyError, TypeError):
                    continue
        for path in sorted(self.root.glob("*/*/summary.json")):
            if not path.resolve().is_relative_to(self.root):
                continue
            try:
                summary = json.loads(path.read_text())
                progress_path = path.parent / "progress.json"
                if progress_path.is_file() and summary.get("operation") == "running":
                    progress = json.loads(progress_path.read_text())
                    if progress.get("run_id") == summary.get("run_id"):
                        summary.update(progress)
            except (OSError, ValueError):
                continue
            payload = {"chat_id": self.chat_id, "text": format_summary(summary)}
            identifier = fingerprint({"chat": self.chat_id, "summary": summary})
            self.db.execute(
                "INSERT OR IGNORE INTO outbox (id, method, payload) VALUES (?, ?, ?)",
                (identifier, "sendMessage", json.dumps(payload)),
            )
            if self.send_files and summary.get("operation") in {"idle", "paused"}:
                checkpoint_path = path.parent / "public_results.json"
                if not checkpoint_path.resolve().is_relative_to(self.root):
                    continue
                try:
                    checkpoint = json.loads(checkpoint_path.read_text())
                except (OSError, ValueError):
                    continue
                if checkpoint.get("updated_at") != summary.get("updated_at"):
                    continue
                import csv
                import io

                stream = io.StringIO(newline="")
                writer = csv.DictWriter(
                    stream,
                    fieldnames=[
                        "id",
                        "faithfulness",
                        "answer_relevancy",
                        "context_precision",
                        "context_recall",
                    ],
                    delimiter=";",
                    extrasaction="ignore",
                )
                writer.writeheader()
                writer.writerows(checkpoint["rows"])
                document = stream.getvalue().encode("utf-8-sig")
                self.db.execute(
                    "INSERT OR IGNORE INTO outbox (id, method, payload, document) VALUES (?, ?, ?, ?)",
                    (
                        identifier + "-csv",
                        "sendDocument",
                        json.dumps({"chat_id": self.chat_id}),
                        document,
                    ),
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO outbox (id, method, payload, document) VALUES (?, ?, ?, ?)",
                    (
                        identifier + "-json",
                        "sendDocument",
                        json.dumps({"chat_id": self.chat_id, "_filename": "results.json"}),
                        json.dumps(checkpoint, ensure_ascii=False).encode(),
                    ),
                )
        self.db.commit()

    def deliver(self):
        for identifier, method, payload, document in self.db.execute(
            "SELECT id, method, payload, document FROM outbox WHERE sent=0 AND next_attempt<=? ORDER BY rowid LIMIT 10",
            (time.time(),),
        ).fetchall():
            try:
                self.client.call(method, json.loads(payload), document)
            except TelegramError as exc:
                self.db.execute(
                    "UPDATE outbox SET next_attempt=? WHERE id=?",
                    (time.time() + max(1, exc.retry_after), identifier),
                )
                self.db.commit()
                return
            self.db.execute("UPDATE outbox SET sent=1 WHERE id=?", (identifier,))
            self.db.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if any(
        value
        for key, value in os.environ.items()
        if key.endswith("API_KEY") or key in {"NEO4J_PASSWORD", "LANGCHAIN_API_KEY"}
    ):
        parser.error("Use service_entrypoint.py telegram to filter the shared configuration")
    if os.getenv("TELEGRAM_ENABLED", "false").lower() != "true":
        return
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_RESULTS_CHAT_ID")
    if not token or not chat:
        parser.error("Telegram token and destination are required")
    interval = int(os.getenv("TELEGRAM_PROGRESS_INTERVAL_SECONDS", "15"))
    if interval < 10:
        parser.error("Progress interval must be at least 10 seconds")
    with exclusive_lock(args.database.with_suffix(".lock")):
        notifier = Notifier(
            args.root,
            args.database,
            TelegramClient(token),
            chat,
            send_files=os.getenv("TELEGRAM_SEND_FINAL_FILES", "false").lower() == "true",
        )
        gateway = None
        if os.getenv("TELEGRAM_CONTROL_ENABLED") == "true":
            from telegram_gateway import Gateway

            allowed = [int(value) for value in os.environ["TELEGRAM_ALLOWED_USER_IDS"].split(",")]
            gateway = Gateway(notifier, os.environ["BENCHMARK_CONTROL_SOCKET"], allowed)
        try:
            while True:
                if gateway:
                    try:
                        gateway.poll()
                    except (OSError, TelegramError):
                        pass
                notifier.scan()
                notifier.deliver()
                if args.once:
                    break
                time.sleep(interval)
        finally:
            notifier.db.close()


if __name__ == "__main__":
    main()
