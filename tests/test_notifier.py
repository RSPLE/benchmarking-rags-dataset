import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from benchmark_storage import atomic_json
from telegram_notifier import Notifier, TelegramError


class NotifierTests(unittest.TestCase):
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
            notifier.db.execute("UPDATE outbox SET next_attempt=0")
            notifier.db.commit()
            notifier.db.close()
            client.call.side_effect = None
            restarted = Notifier(root, root / "outbox.db", client, "123")
            restarted.deliver()
            restarted.scan()
            restarted.deliver()
            self.assertEqual(client.call.call_count, 2)
            self.assertIn("desconhecido", client.call.call_args.args[1]["text"])
            self.assertEqual(json.loads((root / "test/a/summary.json").read_text()), summary)
            restarted.db.close()


if __name__ == "__main__":
    unittest.main()
