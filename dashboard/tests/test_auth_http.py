from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from dashboard.auth import bootstrap_account
from dashboard.auth_server import CSRF_COOKIE, create_app
from dashboard.config import Settings
from dashboard.sessions import COOKIE_NAME, csrf_token


class AuthHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.settings = Settings(
            root / "test.sqlite3",
            root,
            root,
            root,
            username="tester",
            password="test-only-password",
        )
        self.client = TestClient(create_app(self.settings), base_url="https://179.236.251.180")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def login(self, password="test-only-password"):
        page = self.client.get("/auth/login")
        nonce = re.search(r'name="csrf" value="([A-Za-z0-9_-]+)"', page.text)[1]
        return self.client.post(
            "/auth/login",
            data={"username": "tester", "password": password, "csrf": nonce},
            follow_redirects=False,
        )

    def test_login_cookie_survives_new_http_requests_and_is_protected(self):
        response = self.login()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        cookie = next(
            value
            for value in response.headers.get_list("set-cookie")
            if value.startswith(COOKIE_NAME + "=")
        )
        for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Max-Age=28800"):
            self.assertIn(attribute, cookie)
        self.assertEqual(self.client.get("/auth/verify").status_code, 200)
        replacement = TestClient(create_app(self.settings), base_url="https://179.236.251.180")
        replacement.cookies.update(self.client.cookies)
        with replacement:
            self.assertEqual(replacement.get("/auth/verify").status_code, 200)

    def test_wrong_password_and_missing_csrf_do_not_create_session(self):
        self.assertEqual(self.login("wrong-password").status_code, 401)
        self.assertNotIn(COOKIE_NAME, self.client.cookies)
        response = self.client.post(
            "/auth/login",
            data={"username": "tester", "password": "test-only-password", "csrf": "invalid"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(COOKIE_NAME, self.client.cookies)

    def test_cross_origin_post_is_rejected(self):
        self.client.get("/auth/login")
        response = self.client.post(
            "/auth/login",
            headers={"Origin": "https://example.invalid"},
            data={
                "username": "tester",
                "password": "test-only-password",
                "csrf": self.client.cookies[CSRF_COOKIE],
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_logout_revokes_cookie_and_prevents_replay(self):
        self.login()
        token = self.client.cookies[COOKIE_NAME]
        response = self.client.post(
            "/auth/logout", data={"csrf": csrf_token(token)}, follow_redirects=False
        )
        self.assertEqual(response.status_code, 303)
        self.assertNotIn(COOKIE_NAME, self.client.cookies)
        response = self.client.get(
            "/auth/verify", headers={"Cookie": f"{COOKIE_NAME}={token}"}, follow_redirects=False
        )
        self.assertEqual(response.headers["location"], "/auth/login")

    def test_password_change_and_unknown_cookie_require_login(self):
        self.login()
        bootstrap_account(self.settings.database, "tester", "changed-test-password")
        self.assertEqual(self.client.get("/auth/verify", follow_redirects=False).status_code, 303)

    def test_http_cookie_for_local_loopback_and_no_secret_cache(self):
        with TestClient(create_app(self.settings), base_url="http://127.0.0.1:8501") as client:
            response = client.get("/auth/login")
            self.assertNotIn("Secure", response.headers["set-cookie"])
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn(self.settings.password, response.text)


if __name__ == "__main__":
    unittest.main()
