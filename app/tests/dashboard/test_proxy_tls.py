from __future__ import annotations

import http.client
import ipaddress
import os
import ssl
import unittest
from http.cookies import SimpleCookie
from urllib.parse import urlsplit


@unittest.skipUnless(
    os.getenv("BENCHMARK_WEB_TLS_TEST_URL"), "Public IP TLS target is not configured"
)
class ProxyTlsTests(unittest.TestCase):
    def setUp(self):
        self.url = urlsplit(os.environ["BENCHMARK_WEB_TLS_TEST_URL"])
        self.assertEqual(self.url.scheme, "https")
        self.assertIsNone(self.url.username)
        self.assertIsNone(self.url.password)
        self.assertFalse(self.url.query or self.url.fragment)
        self.host = str(ipaddress.ip_address(self.url.hostname))

    def request(self, path):
        connection = http.client.HTTPSConnection(
            self.host,
            self.url.port or 443,
            timeout=10,
            context=ssl.create_default_context(),
        )
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_ip_certificate_is_trusted_without_dns_sni(self):
        status, _, body = self.request("/auth/login")
        self.assertEqual(status, 200)
        self.assertIn(b"Entrar no painel", body)

    def test_https_login_cookie_is_secure_and_not_cached(self):
        status, headers, _ = self.request("/auth/login")
        self.assertEqual(status, 200)
        cookie = SimpleCookie(headers["Set-Cookie"])
        self.assertTrue(cookie["rag_login_csrf"]["secure"])
        self.assertTrue(cookie["rag_login_csrf"]["httponly"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_https_private_routes_still_require_login(self):
        for path in ("/", "/_stcore/host-config", "/media/test.png"):
            with self.subTest(path=path):
                status, headers, _ = self.request(path)
                self.assertEqual(status, 303)
                self.assertEqual(headers["Location"], "/auth/login")


if __name__ == "__main__":
    unittest.main()
