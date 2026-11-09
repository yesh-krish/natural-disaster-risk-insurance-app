import contextlib
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from jsonschema import Draft202012Validator

from api_spec import openapi_document
from api_v1 import comparison, dispatch
from server import Handler
from examples.api_client import main


def curve(peril="flood"):
    return {"status": "available", "peril": peril, "metric": "Depth", "unit": "m",
            "source_name": "Test model", "source_url": "https://example.org/model", "notes": ["Research fixture"],
            "points": [{"severity": 3, "frequency_pct": 1}, {"severity": 1, "frequency_pct": 10},
                       {"severity": None, "frequency_pct": 20}]}


class MemorySocket:
    """Exercise Python's real HTTP parser and response writer without sockets."""
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = bytearray()

    def makefile(self, *args):
        return self.input

    def sendall(self, data):
        self.output.extend(data)


def http_request(path, method="GET"):
    sock = MemorySocket(f"{method} {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
    with patch.object(Handler, "log_message"):
        Handler(sock, ("127.0.0.1", 12345), None)
    headers, body = bytes(sock.output).split(b"\r\n\r\n", 1)
    return int(headers.split()[1]), headers.decode(), body


class APIV1Tests(unittest.TestCase):
    def assert_contract(self, payload, path, status=200):
        spec = openapi_document()
        schema = spec["paths"][path]["get"]["responses"][str(status)]["content"]["application/json"]["schema"]
        schema = {**schema, "components": spec["components"]}
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(payload)
        json.dumps(payload, allow_nan=False)

    def test_hazard_preserves_source_missing_depth_and_inputs(self):
        with patch("api_v1.disaster_at", return_value=curve()) as provider:
            status, payload = dispatch("/api/v1/hazards/flood", "lat=29.4&lon=-98.5")
        self.assertEqual(status, 200)
        provider.assert_called_once_with("flood", 29.4, -98.5, "BC")
        self.assertIsNone(payload["data"]["points"][-1]["severity"])
        self.assertEqual(payload["data"]["source_name"], "Test model")
        self.assertEqual(payload["meta"]["request"]["site_class"], "BC")
        self.assert_contract(payload, "/api/v1/hazards/{peril}")

    def test_auto_reuses_map_selection(self):
        chosen = {**curve("rainfall"), "selection": {"explanation": "County context"}}
        with patch("hazard_relevance.most_relevant", return_value=chosen) as provider:
            status, payload = dispatch("/api/v1/hazards/auto", "lat=29&lon=-98&site_class=D")
        self.assertEqual(status, 200)
        provider.assert_called_once_with(29, -98, "D")
        self.assertEqual(payload["data"]["selection"], chosen["selection"])
        self.assertEqual(payload["meta"]["request"]["peril"], "auto")

    def test_comparison_matches_map_and_keeps_ranges_and_zeros(self):
        source = curve("cyclone")
        source["points"] += [{"severity": 3, "frequency_pct": 2}, {"severity": 5, "frequency_pct": 0}]
        with patch("api_v1.disaster_at", return_value=source):
            status, payload = dispatch("/api/v1/trigger-comparisons/cyclone", "lat=29&lon=-98&payout_usd=50000")
        self.assertEqual(status, 200)
        data = payload["data"]
        self.assertEqual([row["severity"] for row in data["comparisons"]], [1, 3, 5])
        self.assertEqual(data["comparisons"][1]["frequency_pct"], {"minimum": 1, "maximum": 2})
        self.assertEqual(data["comparisons"][1]["expected_annual_contribution_usd"], {"minimum": 500, "maximum": 1000})
        self.assertEqual(data["comparisons"][2]["expected_annual_contribution_usd"]["maximum"], 0)
        self.assertEqual(data["frequency_basis"], "historical_annual_frequency")
        self.assert_contract(payload, "/api/v1/trigger-comparisons/{peril}")
        zero = comparison(source, Decimal("0"))
        self.assertTrue(all(row["expected_annual_contribution_usd"] == {"minimum": 0, "maximum": 0} for row in zero["comparisons"]))

    def test_contributions_round_half_up_and_omit_invalid_source_values(self):
        source = curve("wildfire")
        source["points"] = [{"severity": 1, "frequency_pct": .005}, {"severity": True, "frequency_pct": 1},
                            {"severity": 2, "frequency_pct": float("nan")}, {"severity": 3, "frequency_pct": 101}]
        result = comparison(source, Decimal("100"))
        self.assertEqual(len(result["comparisons"]), 1)
        self.assertEqual(result["comparisons"][0]["expected_annual_contribution_usd"]["minimum"], .01)
        self.assertEqual(result["frequency_basis"], "research_estimate")

    def test_invalid_inputs_fail_before_any_provider_call(self):
        bad_queries = ["", "lat=nan&lon=0", "lat=91&lon=0", "lat=0&lon=181", "lat=0&lon=inf",
                       "lat=0&lat=1&lon=0", "lat=&lon=0", "lat=0&lon=0&site_class=Z", "lat=0&lon=0&typo=1"]
        with patch("api_v1.disaster_at") as provider:
            for query in bad_queries:
                with self.subTest(query=query):
                    status, payload = dispatch("/api/v1/hazards/flood", query)
                    self.assertEqual(status, 400)
                    self.assertEqual(payload["error"]["code"], "invalid_request")
                    self.assert_contract(payload, "/api/v1/hazards/{peril}", 400)
            for payout in ["", "-1", "nan", "inf", "1.001", "1000000000000.01", "abc"]:
                status, _ = dispatch("/api/v1/trigger-comparisons/flood", f"lat=0&lon=0&payout_usd={payout}")
                self.assertEqual(status, 400, payout)
            self.assertEqual(dispatch("/api/v1/hazards/hurricane", "lat=0&lon=0")[0], 400)
            self.assertEqual(dispatch("/api/v1/health", "&".join(f"x{i}=1" for i in range(21)))[0], 400)
        provider.assert_not_called()

    def test_unavailable_is_distinct_from_failure(self):
        missing = {"peril": "flood", "status": "unavailable", "points": [], "reason": "No installed tile"}
        with patch("api_v1.disaster_at", return_value=missing):
            status, payload = dispatch("/api/v1/trigger-comparisons/flood", "lat=0&lon=0&payout_usd=50000")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["data"]["comparisons"], [])
        self.assert_contract(payload, "/api/v1/trigger-comparisons/{peril}")
        with patch("api_v1.disaster_at", side_effect=OSError("private upstream diagnostic")):
            status, payload = dispatch("/api/v1/hazards/flood", "lat=0&lon=0")
        self.assertEqual(status, 502)
        self.assertIsNone(payload["data"])
        self.assertNotIn("private upstream diagnostic", json.dumps(payload))
        self.assert_contract(payload, "/api/v1/hazards/{peril}", 502)
        # Truncated or malformed upstream bodies are provider failures too, not crashes.
        import http.client
        for error in (http.client.IncompleteRead(b"{"), AttributeError("'list' object has no attribute 'get'"),
                      IndexError("list index out of range")):
            with self.subTest(error=type(error).__name__), patch("api_v1.disaster_at", side_effect=error):
                status, payload = dispatch("/api/v1/hazards/flood", "lat=0&lon=0")
            self.assertEqual(status, 502)
            self.assert_contract(payload, "/api/v1/hazards/{peril}", 502)

    def test_area_contract_with_real_aggregation_and_partial_coverage(self):
        with patch("area_hazard.disaster_at", return_value=curve()):
            status, payload = dispatch("/api/v1/areas/flood", "south=29&west=-99&north=30&east=-98")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["area"]["sample_count"], 25)
        self.assertEqual(payload["data"]["points"][0]["evaluated_samples"], 25)
        self.assert_contract(payload, "/api/v1/areas/{peril}")
        with patch("api_v1.area_disaster") as provider:
            self.assertEqual(dispatch("/api/v1/areas/flood", "south=30&west=-99&north=29&east=-98")[0], 400)
            provider.assert_not_called()

    def test_stations_not_found_and_validation(self):
        source = {"peril": "earthquake", "status": "not_found", "station": None,
                  "network": "Test network", "search_radius_km": 200, "message": "No station"}
        with patch("api_v1.nearest_station", return_value=source) as provider:
            status, payload = dispatch("/api/v1/monitoring-stations", "peril=earthquake&lat=29&lon=-98")
        self.assertEqual(status, 200)
        provider.assert_called_once_with("earthquake", 29, -98)
        self.assertEqual(payload["status"], "not_found")
        self.assert_contract(payload, "/api/v1/monitoring-stations")
        self.assertEqual(dispatch("/api/v1/monitoring-stations", "peril=auto&lat=0&lon=0")[0], 400)

    def test_http_routes_headers_docs_spec_and_methods(self):
        for path, expected in [("/api/v1/health", 200), ("/api/v1/nothing", 404),
                               ("/api/v1/hazards/flood?lat=NaN&lon=0", 400)]:
            status, headers, body = http_request(path)
            self.assertEqual(status, expected)
            self.assertIn("application/json", headers)
            self.assertIn("no-store", headers)
            self.assertIn("api_version", json.loads(body))
        status, _, body = http_request("/api/v1/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["openapi"], "3.1.0")
        for method in ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]:
            status, headers, body = http_request("/api/v1/health", method)
            self.assertEqual(status, 405)
            self.assertIn("Allow: GET", headers)
            if method != "HEAD":
                self.assertEqual(json.loads(body)["error"]["code"], "method_not_allowed")
            else:
                self.assertEqual(body, b"")
        status, _, body = http_request("/api/docs")
        self.assertEqual(status, 200)
        self.assertIn(b"Try a request", body)
        status, _, body = http_request("/docs/api-explorer.js")
        self.assertEqual(status, 200)
        self.assertIn(b"openapi.json", body)

    def test_legacy_map_endpoint_unchanged(self):
        with patch("disaster_data.disaster_at", return_value=curve()):
            status, _, body = http_request("/api/disaster?peril=flood&lat=29&lon=-98")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), curve())

    def test_python_client_exports_and_skips_missing_data(self):
        result = dispatch("/api/v1/health", "")[1]
        result["meta"]["request"] = {"peril": "flood", "lat": 29.4, "lon": -98.5, "site_class": "BC"}
        result["data"] = comparison(curve(), Decimal("50000"))
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            output = Path(directory) / "comparison.json"
            csv_file = Path(directory) / "comparison.csv"
            with patch("examples.api_client.fetch_comparison", return_value=result), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--output", str(output), "--csv", str(csv_file)]), 0)
            self.assertEqual(json.loads(output.read_text())["data"]["payout_usd"], 50000)
            self.assertIn("modeled_annual_probability", csv_file.read_text())
            csv_file.unlink()
            result.update(status="unavailable", data={"reason": "Missing model"})
            with patch("examples.api_client.fetch_comparison", return_value=result), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--csv", str(csv_file)]), 0)
            self.assertFalse(csv_file.exists())

    def address_match(self):
        return {"status": "matched", "normalized_address": "100 Dolorosa St, San Antonio, TX",
                "matched_address": "100 DOLOROSA, SAN ANTONIO, TX, 78205", "latitude": 29.424206, "longitude": -98.493261,
                "source_name": "US Census Geocoding Services", "source_url": "https://geocoding.geo.census.gov/",
                "approximate": True, "note": "Approximate address point; verify the match."}

    def test_address_inputs_resolve_for_every_point_route(self):
        for route in ["hazards/flood", "trigger-comparisons/flood", "monitoring-stations"]:
            query = {"address": "100   Dolorosa St , San Antonio, TX"}
            if route.startswith("trigger"):
                query["payout_usd"] = "50000"
            if route == "monitoring-stations":
                query["peril"] = "flood"
            with patch("api_v1.geocode_address", return_value=self.address_match()), \
                    patch("api_v1.disaster_at", return_value=curve()) as hazard, \
                    patch("api_v1.nearest_station", return_value={"status": "not_found"}) as station:
                status, payload = dispatch("/api/v1/" + route, urlencode(query))
            self.assertEqual(status, 200)
            self.assertEqual(payload["meta"]["location"]["matched_address"], self.address_match()["matched_address"])
            self.assertEqual(payload["meta"]["request"]["lat"], 29.424206)
            if route != "monitoring-stations":
                hazard.assert_called_once_with("flood", 29.424206, -98.493261, "BC")
                contract = "/api/v1/trigger-comparisons/{peril}" if route.startswith("trigger") else "/api/v1/hazards/{peril}"
                self.assert_contract(payload, contract)
            else:
                station.assert_called_once_with("flood", 29.424206, -98.493261)

    def test_address_failures_never_compute_hazard(self):
        for outcome, code in [("no_match", "address_not_found"), ("ambiguous", "address_ambiguous")]:
            lookup = {**self.address_match(), "status": outcome, "message": "Check the address", "candidates": []}
            with patch("api_v1.geocode_address", return_value=lookup), patch("api_v1.disaster_at") as provider:
                status, payload = dispatch("/api/v1/trigger-comparisons/flood", "address=wrong&payout_usd=50000")
            self.assertEqual(status, 422)
            self.assertEqual(payload["error"]["code"], code)
            self.assertIsNone(payload["data"])
            provider.assert_not_called()
            self.assert_contract(payload, "/api/v1/trigger-comparisons/{peril}", 422)
        with patch("api_v1.geocode_address", side_effect=OSError("offline")), patch("api_v1.disaster_at") as provider:
            status, payload = dispatch("/api/v1/hazards/flood", "address=100+Dolorosa+St")
        self.assertEqual(status, 502)
        provider.assert_not_called()

    def test_address_and_coordinates_are_mutually_exclusive(self):
        with patch("api_v1.geocode_address") as geocoder, patch("api_v1.disaster_at") as provider:
            for query in ["address=100+Dolorosa&lat=29&lon=-98", "address=100+Dolorosa&lat=29", "lat=29",
                          "address=+++", "address=" + "a" * 101, "address=x&site_class=INVALID"]:
                self.assertEqual(dispatch("/api/v1/hazards/flood", query)[0], 400, query)
            self.assertEqual(dispatch("/api/v1/trigger-comparisons/flood", "address=x&payout_usd=-1")[0], 400)
            geocoder.assert_not_called()
            provider.assert_not_called()

    def test_geocode_endpoint_reports_match_outcomes(self):
        for outcome in ["matched", "no_match", "ambiguous"]:
            with patch("api_v1.geocode_address", return_value={**self.address_match(), "status": outcome}):
                status, payload = dispatch("/api/v1/geocode", "address=100+++Dolorosa+St%2C+San+Antonio%2C+TX")
            self.assertEqual(status, 200)
            self.assertEqual(payload["data"]["match_status"], outcome)
            self.assertEqual(payload["status"], "available" if outcome == "matched" else "unavailable")
            self.assert_contract(payload, "/api/v1/geocode")

    def test_address_client_exports_resolved_coordinates_and_prints_match(self):
        with patch("api_v1.geocode_address", return_value=self.address_match()), patch("api_v1.disaster_at", return_value=curve()):
            _, result = dispatch("/api/v1/trigger-comparisons/flood", "address=100+Dolorosa+St&payout_usd=50000")
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            csv_path = Path(directory) / "address.csv"
            output = io.StringIO()
            with patch("examples.api_client.fetch_comparison", return_value=result) as fetch, contextlib.redirect_stdout(output):
                self.assertEqual(main(["--address", "100 Dolorosa St", "--csv", str(csv_path)]), 0)
            self.assertEqual(fetch.call_args.kwargs["address"], "100 Dolorosa St")
            self.assertIn("100 DOLOROSA, SAN ANTONIO", output.getvalue())
            self.assertIn("29.424206,-98.493261", csv_path.read_text())
            self.assertIn("matched_address", csv_path.read_text())
        for args in [["--address", "x", "--lat", "29", "--lon", "-98"], ["--lat", "29"], ["--address", " "]]:
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                main(args)


if __name__ == "__main__":
    unittest.main()
