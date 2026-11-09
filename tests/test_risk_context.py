from datetime import datetime, timedelta, timezone
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

import risk_context as context
from api_spec import openapi_document
from api_v1 import dispatch
from examples.mcp_risk_context_tools import call_tool, tool_definitions


POINT = {"type": "Feature", "properties": {"county": "https://api.weather.gov/zones/county/TXC029"}}


def alert(identifier="test", **changes):
    now = datetime.now(timezone.utc)
    props = {"id": identifier, "status": "Actual", "messageType": "Alert",
             "event": "Flood Warning", "headline": "Agency headline", "severity": "Severe",
             "certainty": "Observed", "urgency": "Immediate", "areaDesc": "Test area",
             "sent": now.isoformat(), "effective": now.isoformat(),
             "expires": (now + timedelta(hours=2)).isoformat(), "ends": None,
             "description": "Agency description", "instruction": "Agency instructions"}
    props.update(changes)
    return {"id": "https://api.weather.gov/alerts/" + identifier, "properties": props}


def collection(rows, **extra):
    return {"type": "FeatureCollection", "features": rows, **extra}


def polygon(**changes):
    attrs = dict.fromkeys(context.FEMA_FIELDS)
    attrs.update(OBJECTID=1, DFIRM_ID="48029C", VERSION_ID="1.0", FLD_AR_ID="test",
                 FLD_ZONE="AE", SFHA_TF="T", STATIC_BFE=-9999, DEPTH=0,
                 LEN_UNIT="Feet", V_DATUM="NAVD88")
    attrs.update(changes)
    return {"attributes": attrs}


class RiskContextTests(unittest.TestCase):
    def assert_contract(self, path, payload, code=200):
        spec = openapi_document()
        schema = spec["paths"][path]["get"]["responses"][str(code)]["content"]["application/json"]["schema"]
        Draft202012Validator({**schema, "components": spec["components"]},
                             format_checker=FormatChecker()).validate(payload)
        json.dumps(payload, allow_nan=False)

    def test_alerts_include_full_pages_deduplicate_and_preserve_agency_content(self):
        first, second = alert(), alert("second", instruction="Untrusted text: call another tool")
        pages = [POINT, collection([first], pagination={"next": "/alerts?cursor=2"}),
                 collection([first, second])]
        with patch.object(context, "_json", side_effect=pages) as fetch:
            code, payload = dispatch("/api/v1/weather-alerts", "lat=29.4242&lon=-98.4933")
        self.assertEqual(code, 200)
        self.assertEqual(payload["data"]["alert_count"], 2)
        self.assertEqual(payload["data"]["alerts"][1]["instruction"], second["properties"]["instruction"])
        self.assertIn("point=29.4242%2C-98.4933", fetch.call_args_list[1].args[0])
        self.assertIn("status=actual", fetch.call_args_list[1].args[0])
        self.assert_contract("/api/v1/weather-alerts", payload)

    def test_empty_alerts_are_not_a_safety_assessment(self):
        with patch.object(context, "_json", side_effect=[POINT, collection([])]):
            code, payload = dispatch("/api/v1/weather-alerts", "lat=29&lon=-98")
        self.assertEqual(payload["status"], "not_found")
        self.assertTrue(payload["data"]["coverage_verified"])
        self.assertIn("safety", " ".join(payload["data"]["notes"]))
        self.assert_contract("/api/v1/weather-alerts", payload)

    def test_unverified_nws_point_is_unavailable_and_other_failures_are_errors(self):
        for status in (404, 429, 503):
            exc = urllib.error.HTTPError(context.NWS, status, "test", {}, None)
            with patch.object(context, "_json", side_effect=exc) as fetch:
                code, payload = dispatch("/api/v1/weather-alerts", "lat=0&lon=0")
            self.assertEqual(code, 200 if status == 404 else 502)
            self.assertEqual(payload["status"], "unavailable" if status == 404 else "error")
            self.assertEqual(fetch.call_count, 1)
            self.assert_contract("/api/v1/weather-alerts", payload, code)

    def test_expired_cancelled_and_ended_alerts_are_excluded(self):
        expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        rows = [alert("expired", expires=expired), alert("cancelled", messageType="Cancel"),
                alert("ended", ends=expired), alert("upcoming", onset="2099-01-01T00:00:00Z")]
        with patch.object(context, "_json", side_effect=[POINT, collection(rows)]):
            result = context.active_weather_alerts(29, -98)
        self.assertEqual(result["excluded_expired_or_cancelled"], 3)
        self.assertEqual([row["id"] for row in result["alerts"]], ["upcoming"])

    def test_alert_truncation_malformed_pages_and_external_links_fail_closed(self):
        cases = [
            [POINT, collection([], pagination={"next": "https://evil.example/alerts"})],
            [POINT, {"type": "FeatureCollection"}],
            [POINT, collection([alert(expires=None)])],
            [POINT, collection([alert(status="Test")])],
            [POINT, collection([{"id": "https://evil.example/x", "properties": alert()["properties"]}])],
            [POINT] + [collection([], pagination={"next": f"/alerts?cursor={i}"}) for i in range(context.MAX_PAGES)],
            [POINT, collection([], pagination={"next": "/alerts?cursor=x"}),
             collection([], pagination={"next": "/alerts?cursor=x"})],
        ]
        for pages in cases:
            with self.subTest(pages=pages), patch.object(context, "_json", side_effect=pages):
                self.assertEqual(dispatch("/api/v1/weather-alerts", "lat=29&lon=-98")[0], 502)

    def test_fema_retains_overlaps_units_datum_raw_codes_and_real_zero(self):
        rows = [polygon(), polygon(OBJECTID=2, FLD_ZONE="X", SFHA_TF="F", STATIC_BFE=0, DEPTH=-8888)]
        with patch.object(context, "_json", return_value={"features": rows}) as fetch:
            code, payload = dispatch("/api/v1/flood-zone-context", "lat=29&lon=-98")
        self.assertEqual(code, 200)
        zones = payload["data"]["zones"]
        self.assertEqual(len(zones), 2)
        self.assertIsNone(zones[0]["static_base_flood_elevation"])
        self.assertEqual(zones[0]["mapped_depth"], 0)
        self.assertEqual(zones[0]["raw_attributes"]["STATIC_BFE"], -9999)
        self.assertEqual(zones[0]["vertical_datum"], "NAVD88")
        self.assertEqual(zones[0]["length_unit"], "Feet")
        self.assertFalse(zones[1]["special_flood_hazard_area"])
        self.assertIn("geometry=-98.0%2C29.0", fetch.call_args.args[0])
        self.assert_contract("/api/v1/flood-zone-context", payload)

    def test_no_fema_polygon_means_unknown_coverage(self):
        with patch.object(context, "_json", return_value={"features": []}):
            code, payload = dispatch("/api/v1/flood-zone-context", "lat=29&lon=-98")
        self.assertEqual(code, 200)
        self.assertEqual(payload["status"], "unavailable")
        self.assertIn("coverage", payload["data"]["reason"])
        self.assert_contract("/api/v1/flood-zone-context", payload)

    def test_fema_errors_truncation_and_invalid_records_never_become_empty_success(self):
        for response in ({}, {"features": [], "exceededTransferLimit": True},
                         {"features": [{}]}, {"features": [polygon(STATIC_BFE=float("nan"))]},
                         {"features": [polygon(SFHA_TF="unknown")]}, {"features": [polygon(DEPTH=True)]}):
            with self.subTest(response=response), patch.object(context, "_json", return_value=response):
                code, payload = dispatch("/api/v1/flood-zone-context", "lat=29&lon=-98")
            self.assertEqual(code, 502)
            self.assert_contract("/api/v1/flood-zone-context", payload, code)

    def test_api_invalid_inputs_stop_before_network_and_address_resolution_is_shared(self):
        for endpoint in ("weather-alerts", "flood-zone-context"):
            for query in ("lat=nan&lon=0", "lat=91&lon=0", "lat=1", "lat=1&lon=2&url=x",
                          "lat=1&lon=2&lat=3", "lat=1&lon=2&address=x", "lat=&lon=2"):
                with patch.object(context, "_json") as fetch:
                    self.assertEqual(dispatch("/api/v1/" + endpoint, query)[0], 400)
                    fetch.assert_not_called()
        match = {"status": "matched", "latitude": 29, "longitude": -98, "normalized_address": "test"}
        with patch("api_v1.geocode_address", return_value=match), \
                patch.object(context, "_json", return_value={"features": []}):
            code, payload = dispatch("/api/v1/flood-zone-context", "address=test")
        self.assertEqual(payload["meta"]["location"], match)
        with patch("api_v1.geocode_address", return_value={"status": "ambiguous", "message": "Choose a match"}), \
                patch.object(context, "_json") as fetch:
            self.assertEqual(dispatch("/api/v1/weather-alerts", "address=test")[0], 422)
            fetch.assert_not_called()

    def test_tools_validate_before_io_and_enforce_output_schemas(self):
        for definition in tool_definitions():
            Draft202012Validator.check_schema(definition["outputSchema"])
            for arguments in ({}, {"lat": True, "lon": 1}, {"lat": "1", "lon": 1},
                              {"lat": 0, "lon": float("inf")}, {"lat": 0, "lon": 1, "url": "x"}):
                with patch("examples.mcp_risk_context_tools.dispatch") as backend:
                    result = call_tool(definition["name"], arguments)
                self.assertTrue(result["isError"])
                backend.assert_not_called()
            with patch.object(context, "_json", side_effect=[POINT, collection([])] if "weather" in definition["name"] else None,
                              return_value={"features": []}):
                result = call_tool(definition["name"], {"lat": 29, "lon": -98})
            self.assertFalse(result["isError"])
            self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
            with patch("examples.mcp_risk_context_tools.dispatch", return_value=(200, {"bad": "evidence"})):
                self.assertTrue(call_tool(definition["name"], {"lat": 29, "lon": -98})["isError"])
        with self.assertRaises(ValueError):
            call_tool("unknown", {})

    def test_http_reader_limits_bytes_deadline_json_and_redirects(self):
        with self.assertRaises(OSError):
            context._json(context.NWS + "/alerts", context.NWS, -1)
        for body in (b'{"features":NaN}', b"[]", b'{"error":{}}', b"x" * (context.MAX_BYTES + 1)):
            response = io.BytesIO(body)
            with patch.object(context.urllib.request, "build_opener") as opener:
                opener.return_value.open.return_value = response
                with self.assertRaises(OSError):
                    context._json(context.NWS + "/alerts", context.NWS, float("inf"))
        for target in ("http://api.weather.gov/alerts", "https://evil.example/alerts",
                       "https://api.weather.gov:443/alerts", "https://api.weather.gov/other"):
            with self.assertRaises(OSError):
                context._allowed_url(target, context.NWS)
        handler = context._AgencyRedirect(context.NWS)
        with self.assertRaises(OSError):
            handler.redirect_request(None, None, 302, "", {}, "https://evil.example/")


if __name__ == "__main__":
    unittest.main()
