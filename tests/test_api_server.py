"""Exercise the standalone HTTP adapter and its original v1 response contract."""

from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api_server import create_app
from api_v1 import VERSION


class APIServerTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app())
        self.addCleanup(self.client.close)

    def test_health_docs_and_spec_match_existing_contract(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["api_version"], VERSION)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertIn("/api/v1/openapi.json", self.client.get("/api/docs").text)
        spec = self.client.get("/api/v1/openapi.json").json()
        self.assertEqual(spec["info"]["version"], VERSION)
        self.assertIn("/api/v1/nearest-fire", spec["paths"])
        self.assertIn("503", spec["paths"]["/api/v1/hazards/{peril}"]["get"]["responses"])

    def test_original_lookup_and_validation_are_preserved(self):
        curve = {"status": "available", "peril": "flood", "unit": "m", "points": [
            {"severity": 2, "frequency_pct": 1}]}
        with patch("api_v1.disaster_at", return_value=curve) as provider:
            response = self.client.get("/api/v1/hazards/flood?lat=29.4&lon=-98.5")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"], curve)
            provider.assert_called_once_with("flood", 29.4, -98.5, "BC")
            for query in ["lat=29&lat=30&lon=-98", "lat=nan&lon=-98", "lat=29&lon=-98&typo=1"]:
                response = self.client.get("/api/v1/hazards/flood?" + query)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_request")
            self.assertEqual(provider.call_count, 1)

    def test_no_workspace_or_map_files_are_exposed(self):
        for path in ["/server.py", "/.git/config", "/data/twdb/1in5/n29w99.tif", "/index.html"]:
            self.assertEqual(self.client.get(path).status_code, 404, path)
        response = self.client.post("/api/v1/health")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["allow"], "GET")
        self.assertEqual(response.json()["error"]["code"], "method_not_allowed")

    def test_browser_origin_allowlist_and_preflight(self):
        with TestClient(create_app(allowed_origins=["http://localhost:3000"])) as client:
            headers = {"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"}
            response = client.options("/api/v1/hazards/flood", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["access-control-allow-origin"], headers["Origin"])
            response = client.get("/api/v1/health", headers={"Origin": "http://localhost:9999"})
            self.assertNotIn("access-control-allow-origin", response.headers)
        self.assertNotIn("access-control-allow-origin", self.client.get(
            "/api/v1/health", headers={"Origin": "http://localhost:3000"}).headers)

    def test_busy_lookup_does_not_block_health_and_releases_slot(self):
        started, release = threading.Event(), threading.Event()

        def provider(*args):
            started.set()
            if not release.wait(5):
                raise TimeoutError("test lookup was not released")
            return {"status": "unavailable", "reason": "Fixture coverage missing", "points": []}

        with TestClient(create_app(max_lookups=1)) as client, \
                patch("api_v1.disaster_at", side_effect=provider), ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(client.get, "/api/v1/hazards/flood?lat=29&lon=-98")
            try:
                self.assertTrue(started.wait(3))
                self.assertEqual(client.get("/api/v1/health").status_code, 200)
                busy = client.get("/api/v1/hazards/flood?lat=29&lon=-98")
                self.assertEqual(busy.status_code, 503)
                self.assertEqual(busy.headers["retry-after"], "2")
            finally:
                release.set()
            self.assertEqual(first.result().json()["status"], "unavailable")
            self.assertEqual(client.get("/api/v1/hazards/flood?lat=29&lon=-98").status_code, 200)

    def test_unexpected_failure_has_no_private_details_and_slot_recovers(self):
        with patch("api_server.dispatch", side_effect=RuntimeError("private detail")), \
                self.assertLogs("api_server", level="ERROR"):
            response = self.client.get("/api/v1/hazards/flood?lat=29&lon=-98")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private detail", response.text)
        self.assertEqual(self.client.get("/api/v1/health").status_code, 200)


if __name__ == "__main__":
    unittest.main()
