from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from app.benchmark.control import call_control
from app.benchmark.export import result_bytes
from app.benchmark.storage import atomic_json, exclusive_lock, file_hash, fingerprint, now
from app.runtime_config import read_runtime_config

logger = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    def __init__(self, code, retry_after=30):
        super().__init__(f"Telegram delivery failed ({code})")
        self.retry_after = retry_after


class TelegramClient:
    def __init__(self, token):
        token = token.strip()
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
            raise ValueError("Invalid Telegram token format")
        self.token = token

    def call(self, method, payload, document=None):
        payload = dict(payload)
        filename = payload.pop("_filename", "results.csv")
        if filename not in {"results.csv", "results.json", "results.zip"}:
            raise ValueError("Invalid document name")
        mime = {
            "results.json": "application/json",
            "results.csv": "text/csv",
            "results.zip": "application/zip",
        }[filename]
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


def format_event(event):
    if event.get("kind") != "job_state":
        labels = {
            "started": "Execução iniciada",
            "stage_started": "Etapa iniciada",
            "answer_saved": "Resposta salva",
            "metric_saved": "Métrica salva",
            "success": "Questão concluída",
            "failed": "Questão falhou",
            "finished": "Rodada encerrada",
            "interrupted": "Execução interrompida",
            "request_rejected": "Pedido recusado",
            "pause_requested": "Pausa solicitada",
            "notification_test": "Teste de notificações confirmado",
            "service_started": "Serviço Telegram iniciado",
            "control_unavailable": "Executor indisponível",
            "control_recovered": "Conexão com o executor recuperada",
        }
        label = labels.get(event.get("kind"))
        if label is None:
            return json.dumps(event, ensure_ascii=False, indent=2)[:3500]
        lines = [f"Observatório RAG · {label}"]
        for key, title in (
            ("project", "RAG"),
            ("question_id", "Questão"),
            ("stage", "Etapa"),
            ("category", "Tipo de falha"),
            ("error", "Motivo"),
            ("job_id", "Pedido"),
            ("experiment_id", "Experimento"),
        ):
            if event.get(key):
                lines.append(f"{title}: {event[key]}")
        if event.get("metrics"):
            lines.extend(f"{name}: {value}" for name, value in event["metrics"].items())
        if event.get("kind") == "finished":
            lines.append(
                f"Sucessos: {event.get('success', 0)} | Falhas: {event.get('failed', 0)} | Pendentes: {event.get('pending', 0)}"
            )
            if event.get("paused"):
                lines.append("Pausada: o progresso foi preservado para retomada.")
        if event.get("kind") in {"notification_test", "service_started"}:
            lines.append(
                "Avisos de fila, etapas, questões, falhas, pausas e resultados estão ativos neste canal. Nenhuma chamada a modelos é feita por este aviso."
            )
        return "\n".join(lines)[:3500]
    states = {
        "queued": "Na fila",
        "running": "Iniciando execução",
        "finished": "Execução encerrada",
        "failed": "Falha ao iniciar",
        "stopped": "Execução interrompida",
        "interrupted": "Serviço reiniciado",
        "cancelled": "Execução cancelada",
    }
    lines = [
        f"Pipeline · {states.get(event['state'], event['state'])}",
        f"RAG: {event.get('project', 'não informado')}",
        f"Lote: {event['job_id']}",
    ]
    if event.get("error"):
        lines.append(f"Motivo: {event['error']}")
    if event.get("code") is not None:
        lines.append(f"Código de saída: {event['code']}")
    return "\n".join(lines)[:3500]


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
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS control_cursor (source TEXT PRIMARY KEY, sequence INTEGER NOT NULL)"
        )
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(outbox)")}
        for name, kind in (("delivered_at", "TEXT"), ("message_id", "INTEGER")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE outbox ADD COLUMN {name} {kind}")
        self.last_delivery = self.db.execute(
            "SELECT max(delivered_at) FROM outbox WHERE sent=1"
        ).fetchone()[0]
        self.delivery_error = None
        self.db.commit()

    def reconfigure(self, client, chat_id, send_files):
        previous = self.chat_id
        self.client, self.chat_id, self.send_files = client, str(chat_id), send_files
        if previous != self.chat_id:
            rows = self.db.execute(
                "SELECT id,payload FROM outbox WHERE sent=0 AND method IN ('sendMessage','sendDocument')"
            ).fetchall()
            for identifier, encoded in rows:
                try:
                    payload = json.loads(encoded)
                except (TypeError, ValueError):
                    continue
                if str(payload.get("chat_id")) == previous:
                    payload["chat_id"] = self.chat_id
                    self.db.execute(
                        "UPDATE outbox SET payload=? WHERE id=?",
                        (json.dumps(payload), identifier),
                    )
            self.db.commit()

    def enqueue_event(self, event):
        identifier = fingerprint({"chat": self.chat_id, "event": event["event_id"]})
        payload = {"chat_id": self.chat_id, "text": format_event(event)}
        self.db.execute(
            "INSERT OR IGNORE INTO outbox (id,method,payload) VALUES (?, 'sendMessage', ?)",
            (identifier, json.dumps(payload)),
        )

    def scan_control(self, socket_path, user_id):
        source = f"{socket_path}:{self.chat_id}"
        row = self.db.execute(
            "SELECT sequence FROM control_cursor WHERE source=?", (source,)
        ).fetchone()
        result = call_control(
            socket_path,
            {
                "user_id": user_id,
                "command_id": "notifier:" + uuid.uuid4().hex,
                "text": f"/eventos {row[0] if row else 0}",
            },
        )
        with self.db:
            for event in result["events"]:
                self.enqueue_event(event)
            self.db.execute(
                "INSERT INTO control_cursor VALUES (?,?) ON CONFLICT(source) DO UPDATE SET sequence=excluded.sequence",
                (source, result["cursor"]),
            )

    def health(self, path, control_error=None):
        pending = self.db.execute("SELECT count(*) FROM outbox WHERE sent=0").fetchone()[0]
        atomic_json(
            path,
            {
                "updated_at": now(),
                "pending": pending,
                "last_delivery": self.last_delivery,
                "delivery_error": self.delivery_error,
                "control_error": control_error,
            },
            mode=0o640,
        )

    def scan(self):
        for metadata_path in sorted(self.root.glob("*/*/deliveries/*/metadata.json")):
            archive_path = metadata_path.parent / "results.zip"
            if not metadata_path.resolve().is_relative_to(
                self.root
            ) or not archive_path.resolve().is_relative_to(self.root):
                continue
            try:
                metadata = json.loads(metadata_path.read_text())
                identifier = fingerprint(
                    {"chat": self.chat_id, "archive": metadata["sha256"], "run": metadata["run_id"]}
                )
                if self.db.execute("SELECT 1 FROM outbox WHERE id=?", (identifier,)).fetchone():
                    continue
                if (
                    archive_path.stat().st_size > 49 * 1024 * 1024
                    or file_hash(archive_path) != metadata["sha256"]
                ):
                    continue
                payload = {
                    "chat_id": self.chat_id,
                    "_filename": "results.zip",
                    "caption": f"{metadata['project']} · {metadata['operation']}\nExperimento: {metadata['experiment_id']}\nRodada: {metadata['run_id']}\nVálidos: {metadata['success']} | Falhas: {metadata['failed']} | Pendentes: {metadata['pending']}\nSHA-256: {metadata['sha256']}",
                }
                if self.send_files:
                    self.db.execute(
                        "INSERT OR IGNORE INTO outbox (id,method,payload,document) VALUES (?, 'sendDocument', ?, ?)",
                        (identifier, json.dumps(payload), archive_path.read_bytes()),
                    )
            except (OSError, ValueError, KeyError):
                continue
        paths = [self.root / "control_events.jsonl", *self.root.glob("*/*/public_events.jsonl")]
        for event_path in paths:
            if not event_path.is_file() or not event_path.resolve().is_relative_to(self.root):
                continue
            try:
                lines = event_path.read_text().splitlines()
            except OSError:
                logger.warning("Cannot read notification events: %s", event_path.name)
                continue
            for line in lines:
                try:
                    event = json.loads(line)
                    self.enqueue_event(event)
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
                payload = {"chat_id": self.chat_id, "text": format_summary(summary)}
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
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
                document = result_bytes(checkpoint["rows"])
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
            "SELECT id, method, payload, document FROM outbox WHERE sent=0 AND next_attempt<=? ORDER BY rowid LIMIT 30",
            (time.time(),),
        ).fetchall():
            try:
                receipt = self.client.call(method, json.loads(payload), document)
            except TelegramError as exc:
                self.delivery_error = str(exc)
                logger.warning("%s; retrying later", exc)
                self.db.execute(
                    "UPDATE outbox SET next_attempt=? WHERE id=?",
                    (time.time() + max(1, exc.retry_after), identifier),
                )
                self.db.commit()
                return
            delivered = now()
            self.db.execute(
                "UPDATE outbox SET sent=1,delivered_at=?,message_id=? WHERE id=?",
                (
                    delivered,
                    receipt.get("message_id") if isinstance(receipt, dict) else None,
                    identifier,
                ),
            )
            self.db.commit()
            self.delivery_error = None
            self.last_delivery = delivered


def telegram_configuration():
    values = dict(os.environ)
    try:
        values.update(read_runtime_config()["environment"])
    except ValueError as exc:
        logger.warning("Runtime configuration unavailable (%s)", type(exc).__name__)
    allowed = []
    try:
        allowed = [
            int(value.strip())
            for value in values.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
            if value.strip()
        ]
    except ValueError:
        allowed = []
    return {
        "enabled": values.get("TELEGRAM_ENABLED", "false").lower() == "true",
        "token": values.get("TELEGRAM_BOT_TOKEN", ""),
        "chat": values.get("TELEGRAM_RESULTS_CHAT_ID", ""),
        "allowed": allowed,
        "control": values.get("TELEGRAM_CONTROL_ENABLED", "false").lower() == "true",
        "send_files": values.get("TELEGRAM_SEND_FINAL_FILES", "false").lower() == "true",
        "interval": max(10, int(values.get("TELEGRAM_PROGRESS_INTERVAL_SECONDS", "15"))),
        "socket": values.get("BENCHMARK_CONTROL_SOCKET", ""),
    }


def main():
    logging.basicConfig(level=logging.INFO)
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
        parser.error(
            "Use python -m app.services.entrypoint telegram to filter the shared configuration"
        )
    if not telegram_configuration()["enabled"]:
        return
    with exclusive_lock(args.database.with_suffix(".lock")):
        notifier = gateway = None
        signature = gateway_signature = None
        control_available = True
        try:
            while True:
                settings = telegram_configuration()
                if not settings["enabled"]:
                    if args.once:
                        break
                    time.sleep(settings["interval"])
                    continue
                if not settings["token"] or not settings["chat"]:
                    logger.warning("Telegram is enabled but token or destination is missing")
                    if args.once:
                        break
                    time.sleep(settings["interval"])
                    continue
                current_signature = (
                    fingerprint(settings["token"]),
                    settings["chat"],
                    settings["send_files"],
                )
                if current_signature != signature:
                    try:
                        client = TelegramClient(settings["token"])
                    except ValueError:
                        logger.warning("Telegram token has an invalid format")
                        if args.once:
                            break
                        time.sleep(settings["interval"])
                        continue
                    if notifier is None:
                        notifier = Notifier(
                            args.root,
                            args.database,
                            client,
                            settings["chat"],
                            send_files=settings["send_files"],
                        )
                    else:
                        notifier.reconfigure(
                            client, settings["chat"], settings["send_files"]
                        )
                    notifier.enqueue_event(
                        {"event_id": uuid.uuid4().hex, "kind": "service_started", "at": now()}
                    )
                    notifier.db.commit()
                    signature = current_signature
                    gateway_signature = None
                current_gateway_signature = (
                    settings["control"],
                    settings["socket"],
                    tuple(settings["allowed"]),
                    current_signature,
                )
                if current_gateway_signature != gateway_signature:
                    gateway = None
                    if settings["control"] and settings["socket"] and settings["allowed"]:
                        from app.telegram.gateway import Gateway

                        gateway = Gateway(
                            notifier, settings["socket"], settings["allowed"]
                        )
                    gateway_signature = current_gateway_signature
                control_error = None
                if settings["socket"] and settings["allowed"]:
                    try:
                        notifier.scan_control(settings["socket"], settings["allowed"][0])
                        if not control_available:
                            notifier.enqueue_event(
                                {
                                    "event_id": uuid.uuid4().hex,
                                    "kind": "control_recovered",
                                    "at": now(),
                                }
                            )
                        control_available = True
                    except (OSError, ValueError) as exc:
                        control_error = "Não foi possível consultar os eventos do executor."
                        logger.warning("Control events unavailable (%s)", type(exc).__name__)
                        if control_available:
                            notifier.enqueue_event(
                                {
                                    "event_id": uuid.uuid4().hex,
                                    "kind": "control_unavailable",
                                    "at": now(),
                                }
                            )
                        control_available = False
                if gateway:
                    try:
                        gateway.poll()
                    except (OSError, TelegramError) as exc:
                        logger.warning("Telegram commands unavailable (%s)", type(exc).__name__)
                notifier.scan()
                notifier.deliver()
                if settings["socket"]:
                    try:
                        notifier.health(
                            Path(settings["socket"]).with_name("telegram-status.json"), control_error
                        )
                    except OSError:
                        logger.warning("Cannot publish Telegram delivery status")
                if args.once:
                    break
                time.sleep(settings["interval"])
        finally:
            if notifier is not None:
                notifier.db.close()


if __name__ == "__main__":
    main()
