from __future__ import annotations

import json
import uuid

from benchmark_control import call_control


class Gateway:
    def __init__(self, notifier, socket_path, allowed):
        self.notifier = notifier
        self.db, self.client = notifier.db, notifier.client
        self.socket_path, self.allowed = socket_path, set(allowed)
        if not self.allowed or any(type(value) is not int or value <= 0 for value in self.allowed):
            raise ValueError("Numeric authorized user IDs are required")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS telegram_cursor (id INTEGER PRIMARY KEY CHECK(id=1), offset INTEGER NOT NULL)"
        )
        self.db.execute("INSERT OR IGNORE INTO telegram_cursor VALUES (1,0)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS buttons (id TEXT PRIMARY KEY, user INTEGER NOT NULL, command TEXT NOT NULL)"
        )
        self.db.commit()

    def buttons(self, user, result):
        commands = [("Status", "/status")]
        if result.get("project") and result.get("experiment_id"):
            target = f"{result['project']} {result['experiment_id']}"
            commands += [
                ("Pausar / Pause", f"/pausar {target}"),
                ("Resultados / Results", f"/resultado {target}"),
            ]
        rows = []
        for label, command in commands:
            identifier = uuid.uuid4().hex
            self.db.execute("INSERT INTO buttons VALUES (?, ?, ?)", (identifier, user, command))
            rows.append({"text": label, "callback_data": identifier})
        return {"inline_keyboard": [rows]}

    def poll(self):
        offset = self.db.execute("SELECT offset FROM telegram_cursor WHERE id=1").fetchone()[0]
        updates = self.client.call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": 0,
                "limit": 20,
                "allowed_updates": ["message", "callback_query"],
            },
        )
        for update in sorted(updates or [], key=lambda item: item["update_id"]):
            if update["update_id"] < offset:
                continue
            callback = update.get("callback_query")
            message = callback.get("message", {}) if callback else update.get("message", {})
            actor = callback.get("from", {}) if callback else message.get("from", {})
            user = actor.get("id")
            chat = message.get("chat", {})
            authorized = (
                type(user) is int
                and user in self.allowed
                and chat.get("type") == "private"
                and chat.get("id") == user
                and not actor.get("is_bot")
            )
            if authorized:
                text = message.get("text", "")
                if callback:
                    row = self.db.execute(
                        "SELECT command FROM buttons WHERE id=? AND user=?",
                        (callback.get("data", ""), user),
                    ).fetchone()
                    text = row[0] if row else ""
                identifier = f"telegram:{update['update_id']}"
                try:
                    result = call_control(
                        self.socket_path, {"user_id": user, "command_id": identifier, "text": text}
                    )
                except ValueError as exc:
                    result = {"error": str(exc)}
                payload = {
                    "chat_id": user,
                    "text": json.dumps(result, ensure_ascii=False, indent=2)[:3500],
                    "reply_markup": self.buttons(user, result),
                }
                self.db.execute(
                    "INSERT OR IGNORE INTO outbox (id,method,payload) VALUES (?, 'sendMessage', ?)",
                    (identifier, json.dumps(payload)),
                )
                if "document" in result or len(json.dumps(result)) > 3500:
                    document = json.dumps(
                        result.get("document", result), ensure_ascii=False, indent=2
                    ).encode()
                    self.db.execute(
                        "INSERT OR IGNORE INTO outbox (id,method,payload,document) VALUES (?, 'sendDocument', ?, ?)",
                        (
                            identifier + "-json",
                            json.dumps({"chat_id": user, "_filename": "results.json"}),
                            document,
                        ),
                    )
                if callback:
                    self.db.execute(
                        "INSERT OR IGNORE INTO outbox (id,method,payload) VALUES (?, 'answerCallbackQuery', ?)",
                        (identifier + "-ack", json.dumps({"callback_query_id": callback["id"]})),
                    )
            self.db.execute(
                "UPDATE telegram_cursor SET offset=? WHERE id=1", (update["update_id"] + 1,)
            )
            self.db.commit()
            offset = update["update_id"] + 1
