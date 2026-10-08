from __future__ import annotations

import http.client
import os
import re
import unittest
from http.cookies import SimpleCookie
from urllib.parse import urlencode, urlsplit

from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from dashboard.sessions import COOKIE_NAME, csrf_token


@unittest.skipUnless(os.getenv("BENCHMARK_WEB_TEST_URL"), "Isolated proxy is not configured")
class ProxyHttpTests(unittest.TestCase):
    def setUp(self):
        self.base = os.environ["BENCHMARK_WEB_TEST_URL"].rstrip("/")
        self.url = urlsplit(self.base)
        self.assertEqual(self.url.scheme, "http")
        self.assertEqual(self.url.hostname, "127.0.0.1")
        self.cookies = {}

    def request(self, path, values=None):
        connection = http.client.HTTPConnection(self.url.hostname, self.url.port, timeout=10)
        headers = {"Cookie": "; ".join(f"{key}={value}" for key, value in self.cookies.items())}
        if values is not None:
            headers.update(
                {"Content-Type": "application/x-www-form-urlencoded", "Origin": self.base}
            )
        connection.request(
            "POST" if values is not None else "GET",
            path,
            body=urlencode(values) if values is not None else None,
            headers=headers,
        )
        response = connection.getresponse()
        content = response.read()
        for key, value in response.getheaders():
            if key.lower() == "set-cookie":
                cookie = SimpleCookie(value)
                for name, morsel in cookie.items():
                    if morsel["max-age"] == "0":
                        self.cookies.pop(name, None)
                    else:
                        self.cookies[name] = morsel.value
        result = response.status, dict(response.getheaders()), content
        connection.close()
        return result

    def login(self):
        status, _, content = self.request("/auth/login")
        self.assertEqual(status, 200)
        nonce = re.search(rb'name="csrf" value="([A-Za-z0-9_-]+)"', content)[1].decode()
        status, _, _ = self.request(
            "/auth/login",
            {"username": "web_test", "password": "web-only-test-password", "csrf": nonce},
        )
        self.assertEqual(status, 303)

    def test_private_routes_require_authentication(self):
        for path in ("/", "/_stcore/host-config", "/media/test.png"):
            with self.subTest(path=path):
                status, headers, _ = self.request(path)
                self.assertEqual(status, 303)
                self.assertEqual(headers.get("Location"), "/auth/login")

    def test_authenticated_websocket_and_http_survive_proxy(self):
        self.login()
        self.assertEqual(self.request("/")[0], 200)
        cookie = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
        with connect(
            self.base.replace("http:", "ws:") + "/_stcore/stream",
            origin=self.base,
            additional_headers={"Cookie": cookie},
            subprotocols=["streamlit"],
            open_timeout=10,
        ) as websocket:
            self.assertEqual(websocket.subprotocol, "streamlit")

    def test_websocket_without_login_is_rejected(self):
        with self.assertRaises(InvalidStatus):
            connect(
                self.base.replace("http:", "ws:") + "/_stcore/stream",
                origin=self.base,
                open_timeout=10,
            )

    def test_logout_revokes_authenticated_download_access(self):
        self.login()
        token = self.cookies[COOKIE_NAME]
        self.assertEqual(self.request("/auth/logout", {"csrf": csrf_token(token)})[0], 303)
        self.cookies[COOKIE_NAME] = token
        self.assertEqual(self.request("/media/test.png")[0], 303)


if __name__ == "__main__":
    unittest.main()
