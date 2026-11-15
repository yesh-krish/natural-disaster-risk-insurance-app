"""Public hosting must not expose source, local datasets, or directory listings."""

import io
import unittest
from unittest.mock import patch

from server import Handler


class MemorySocket:
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = bytearray()

    def makefile(self, *args):
        return self.input

    def sendall(self, data):
        self.output.extend(data)


def request(path, method="GET"):
    sock = MemorySocket(f"{method} {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
    with patch.object(Handler, "log_message"):
        Handler(sock, ("127.0.0.1", 12345), None)
    headers, body = bytes(sock.output).split(b"\r\n\r\n", 1)
    return int(headers.split()[1]), headers, body


class PublicServerTests(unittest.TestCase):
    def test_truncated_provider_responses_return_json_errors(self):
        import http.client
        import json
        with patch("server.geocode_address", side_effect=http.client.IncompleteRead(b"{")),                 patch("builtins.print"):
            status, _, body = request("/api/geocode?address=100+Dolorosa+St%2C+San+Antonio%2C+TX+78205")
        self.assertEqual(status, 502)
        self.assertIn("error", json.loads(body))
        with patch("api_v1.dispatch", side_effect=RuntimeError("unexpected")), patch("builtins.print"):
            status, _, body = request("/api/v1/hazards/flood?lat=0&lon=0")
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(body)["error"]["code"], "internal_error")

    def test_browser_assets_and_api_docs_remain_accessible(self):
        for path in ["/", "/index.html", "/script.js?v=27", "/fire_history.js",
                     "/fire_proximity.js", "/api/docs", "/docs/api-explorer.js",
                     "/examples/api_client.py", "/docs/fire-history.md"]:
            with self.subTest(path=path):
                status, headers, body = request(path)
                self.assertEqual(status, 200)
                self.assertTrue(body)
                self.assertIn(b"X-Content-Type-Options: nosniff", headers)

    def test_private_files_and_directory_listings_are_blocked_for_get_and_head(self):
        for method in ["GET", "HEAD"]:
            for path in ["/.git/config", "/.env", "/server.py", "/data/",
                         "/data/twdb/1in5/n29w99.tif", "/tests/", "/docs/",
                         "/%2eenv", "/docs/../server.py", "/docs/%2e%2e/.git/config"]:
                with self.subTest(method=method, path=path):
                    status, _, body = request(path, method)
                    self.assertEqual(status, 404)
                    if method == "HEAD":
                        self.assertEqual(body, b"")

    def test_optional_password_blocks_requests_without_credentials(self):
        import base64
        import os
        with patch.dict(os.environ, {"NDAPP_PASSWORD": "s3cret", "NDAPP_USER": "ndapp"}):
            status, headers, _ = request("/")
            self.assertEqual(status, 401)
            self.assertIn(b"WWW-Authenticate", headers)
            token = base64.b64encode(b"ndapp:s3cret").decode()
            sock = MemorySocket(
                f"GET / HTTP/1.0\r\nHost: localhost\r\nAuthorization: Basic {token}\r\n\r\n".encode())
            with patch.object(Handler, "log_message"):
                Handler(sock, ("127.0.0.1", 12345), None)
            self.assertEqual(int(bytes(sock.output).split()[1]), 200)


if __name__ == "__main__":
    unittest.main()
