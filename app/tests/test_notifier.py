import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.benchmark.storage import atomic_json
from app.telegram.notifier import Notifier, TelegramError


class NotifierTests(unittest.TestCase):
    def test_event_receipt_is_persisted_and_not_resent_after_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = Mock()
            client.call.return_value = {"message_id": 42}
            notifier = Notifier(root, root / "outbox.db", client, "-100123")
            event = {
                "event_id": "stage-one",
                "kind": "stage_started",
                "project": "graph-rag",
                "question_id": "Q002",
                "stage": "faithfulness",
            }
            notifier.enqueue_event(event)
            notifier.db.commit()
            notifier.deliver()
            row = notifier.db.execute("SELECT sent,message_id,delivered_at FROM outbox").fetchone()
            self.assertEqual(row[:2], (1, 42))
            self.assertIsNotNone(row[2])
            notifier.db.close()
            restarted = Notifier(root, root / "outbox.db", client, "-100123")
            restarted.enqueue_event(event)
            restarted.db.commit()
            restarted.deliver()
            self.assertEqual(client.call.call_count, 1)
            self.assertIn("Etapa iniciada", client.call.call_args.args[1]["text"])
            self.assertEqual(restarted.last_delivery, row[2])
            restarted.db.close()

    def test_delivery_failure_restart_and_deduplication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = {
                "project": "test",
                "experiment_id": "a",
                "operation": "idle",
                "success": 1,
                "failed": 0,
                "pending": 0,
                "partial": 0,
                "metrics": {},
                "usage": {"cost_usd": None},
            }
            atomic_json(root / "test" / "a" / "summary.json", summary)
            client = Mock()
            client.call.side_effect = TelegramError(429, 1)
            notifier = Notifier(root, root / "outbox.db", client, "123")
            notifier.scan()
            notifier.scan()
            notifier.deliver()
            self.assertEqual(notifier.db.execute("SELECT count(*) FROM outbox").fetchone()[0], 1)
            self.assertEqual(notifier.db.execute("SELECT sent FROM outbox").fetchone()[0], 0)
            notifier.health(root / "telegram-status.json")
            health = json.loads((root / "telegram-status.json").read_text())
            self.assertEqual(health["pending"], 1)
            self.assertIn("429", health["delivery_error"])
            notifier.db.execute("UPDATE outbox SET next_attempt=0")
            notifier.db.commit()
            notifier.db.close()
            client.call.side_effect = None
            restarted = Notifier(root, root / "outbox.db", client, "123")
            restarted.deliver()
            restarted.scan()
            restarted.deliver()
            self.assertEqual(client.call.call_count, 2)
            restarted.health(root / "telegram-status.json")
            health = json.loads((root / "telegram-status.json").read_text())
            self.assertEqual(health["pending"], 0)
            self.assertIsNone(health["delivery_error"])
            self.assertIsNotNone(health["last_delivery"])
            self.assertIn("desconhecido", client.call.call_args.args[1]["text"])
            self.assertEqual(json.loads((root / "test/a/summary.json").read_text()), summary)
            restarted.db.close()


if __name__ == "__main__":
    unittest.main()
