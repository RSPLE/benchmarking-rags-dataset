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
                f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="results.csv"\r\nContent-Type: text/csv\r\n\r\n'.encode()
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
        for path in sorted(self.root.glob("*/*/summary.json")):
            if not path.resolve().is_relative_to(self.root):
                continue
            summary = json.loads(path.read_text())
            payload = {"chat_id": self.chat_id, "text": format_summary(summary)}
            identifier = fingerprint({"chat": self.chat_id, "summary": summary})
            self.db.execute(
                "INSERT OR IGNORE INTO outbox (id, method, payload) VALUES (?, ?, ?)",
                (identifier, "sendMessage", json.dumps(payload)),
            )
            if self.send_files and summary.get("operation") in {"idle", "paused"}:
                checkpoint_path = path.parent / "public_results.json"
                checkpoint = json.loads(checkpoint_path.read_text())
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
    if any(os.getenv(key) for key in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "LANGCHAIN_API_KEY")):
        parser.error("Run the notifier with its own environment, without model credentials")
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
        try:
            while True:
                notifier.scan()
                notifier.deliver()
                if args.once:
                    break
                time.sleep(interval)
        finally:
            notifier.db.close()


if __name__ == "__main__":
    main()
