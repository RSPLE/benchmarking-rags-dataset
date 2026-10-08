from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("docker"), "Docker Compose is unavailable")
class ComposeDashboardTests(unittest.TestCase):
    def configuration(self, extra=""):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = Path(__file__).resolve().parents[2] / "docker-compose.yml"
            (root / "docker-compose.yml").write_bytes(source.read_bytes())
            (root / ".env").write_text(
                "BENCHMARK_OUTPUT_DIR=resultados\nNEO4J_USERNAME=hosted-user\n"
                "DASHBOARD_USERNAME=tester\nDASHBOARD_PASSWORD=local-test-password\n"
                "DASHBOARD_PUBLIC_HOST=203.0.113.10\n"
                "NEO4J_PASSWORD='test-$-quoted-password'\n"
                "NEO4J_URI=neo4j+s://example.invalid\n" + extra
            )
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "config",
                    "--format",
                    "json",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
                env={"PATH": os.environ["PATH"], "HOME": directory},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def test_relative_output_is_host_directory_for_all_consumers(self):
        services = self.configuration()["services"]
        for name, target, read_only in [
            ("monitor", "/results", True),
            ("control", "/app/resultados", False),
            ("telegram", "/app/resultados", True),
        ]:
            with self.subTest(service=name):
                volume = next(v for v in services[name]["volumes"] if v["target"] == target)
                self.assertEqual(volume["type"], "bind")
                self.assertTrue(volume["source"].endswith("/resultados"))
                self.assertEqual(volume.get("read_only", False), read_only)

    def test_local_admin_and_hosted_connection_are_independent(self):
        services = self.configuration()["services"]
        self.assertEqual(
            services["neo4j"]["environment"]["NEO4J_AUTH"].replace("$$", "$"),
            "neo4j/test-$-quoted-password",
        )
        self.assertEqual(services["control"]["environment"]["NEO4J_USERNAME"], "hosted-user")
        self.assertEqual(
            services["control"]["environment"]["NEO4J_URI"], "neo4j+s://example.invalid"
        )
        self.assertNotIn("test-$-quoted-password", repr(services["neo4j"]["healthcheck"]))

    def test_explicit_container_uri_takes_precedence(self):
        services = self.configuration("NEO4J_CONTAINER_URI=bolt://neo4j:7687\n")["services"]
        self.assertEqual(services["control"]["environment"]["NEO4J_URI"], "bolt://neo4j:7687")

    def test_web_receives_only_explicit_settings_and_local_port(self):
        services = self.configuration(
            "OPENROUTER_API_KEY=example-provider-secret\nTELEGRAM_BOT_TOKEN=example-bot-secret\n"
        )["services"]
        dashboard = services["dashboard"]
        self.assertEqual(dashboard["environment"]["DASHBOARD_USERNAME"], "tester")
        self.assertEqual(dashboard["environment"]["DASHBOARD_PASSWORD"], "local-test-password")
        self.assertNotIn("OPENROUTER_API_KEY", dashboard["environment"])
        self.assertNotIn("TELEGRAM_BOT_TOKEN", dashboard["environment"])
        self.assertNotIn("ports", dashboard)
        self.assertNotIn("ports", services["auth"])
        local_port = next(port for port in services["proxy"]["ports"] if port["target"] == 8080)
        self.assertEqual(local_port["host_ip"], "127.0.0.1")
        public_ports = [
            port["target"]
            for port in services["proxy"]["ports"]
            if port.get("host_ip") != "127.0.0.1"
        ]
        self.assertEqual(public_ports, [80, 443])

    def test_plain_compose_builds_all_application_images_locally(self):
        services = self.configuration()["services"]
        self.assertEqual(
            set(services), {"neo4j", "dashboard", "monitor", "control", "telegram", "proxy", "auth"}
        )
        for name in ("dashboard", "monitor", "control", "telegram", "auth"):
            with self.subTest(service=name):
                self.assertNotIn("profiles", services[name])
                self.assertNotIn("image", services[name])
                self.assertIn("build", services[name])
                self.assertEqual(services[name]["pull_policy"], "build")


if __name__ == "__main__":
    unittest.main()
