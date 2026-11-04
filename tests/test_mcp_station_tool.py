"""Boundary checks for the transport-free, read-only tool prototype."""
import json
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from examples.mcp_station_tool import call_tool, tool_definition
import monitoring_stations as stations


ADDRESS = "100 Dolorosa St, San Antonio, TX"
MATCH = {"status": "matched", "normalized_address": ADDRESS,
         "matched_address": "100 DOLOROSA, SAN ANTONIO, TX, 78205",
         "latitude": 29.4242, "longitude": -98.4933,
         "source_name": "US Census Geocoding Services",
         "source_url": "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
         "approximate": True, "note": "Approximate address-range point."}


class MCPStationToolTests(unittest.TestCase):
    def validate_result(self, result):
        Draft202012Validator(tool_definition()["outputSchema"]).validate(result["structuredContent"])
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        self.assertEqual(result["resultType"], "complete")

    def test_invalid_arguments_never_reach_backend(self):
        invalid = [None, [], {}, {"address": 42}, {"address": " "}, {"address": "x" * 101},
                   {"address": "x" * 1001}, {"address": "100\x00Main St"},
                   {"address": ADDRESS, "url": "http://localhost/admin"}]
        invalid += [{"address": ADDRESS, "n": n} for n in [True, "3", 3.0, 0, 51, float("nan")]]
        with patch("examples.mcp_station_tool.dispatch") as backend:
            for arguments in invalid:
                result = call_tool(arguments)
                self.assertTrue(result["isError"], arguments)
                self.assertEqual(result["structuredContent"]["error"]["code"], "invalid_request")
                self.validate_result(result)
            backend.assert_not_called()

    def test_real_backend_sorts_three_and_preserves_stale_missing_and_units(self):
        catalog = [dict(icaoId=identifier, site=identifier, lat=29.4242, lon=lon, siteType=["METAR"])
                   for identifier, lon in [("KFAR", -99), ("KCCC", -98.6), ("KBBB", -98.55), ("KAAA", -98.5)]]
        reports = [dict(icaoId="KAAA", obsTime=999900, temp=0, wspd=0),
                   dict(icaoId="KBBB", obsTime=990000, temp=12)]
        with patch("api_v1.geocode_address", return_value=MATCH), \
             patch.object(stations.time, "time", return_value=1000000), \
             patch.object(stations, "_awc_resource", side_effect=[(catalog, "1970-01-12T13:00:00+00:00"), (reports, "unused")]):
            result = call_tool({"address": ADDRESS})
        self.assertFalse(result["isError"])
        payload = result["structuredContent"]
        rows = payload["data"]["stations"]
        self.assertEqual([row["id"] for row in rows], ["KAAA", "KBBB", "KCCC"])
        self.assertEqual([row["observation"]["status"] for row in rows], ["available", "stale", "missing"])
        self.assertEqual(rows[0]["observation"]["values"][0], {"variable": "Air temperature", "value": 0, "unit": "\u00b0C"})
        self.assertEqual(payload["meta"]["location"], MATCH)
        self.assertEqual(payload["meta"]["request"]["network"], "awc_metar")
        self.validate_result(result)

    def test_ambiguous_address_does_not_search_stations(self):
        ambiguous = {**MATCH, "status": "ambiguous", "message": "Refine address", "candidates": []}
        with patch("api_v1.geocode_address", return_value=ambiguous), patch("api_v1.nearest_stations") as provider:
            result = call_tool({"address": ADDRESS})
        provider.assert_not_called()
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "address_ambiguous")
        self.validate_result(result)

    def test_provider_failure_remains_error(self):
        with patch("api_v1.geocode_address", side_effect=OSError("offline")):
            result = call_tool({"address": ADDRESS})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "provider_unavailable")
        self.validate_result(result)

    def test_partial_observation_failure_retains_station_links(self):
        catalog = [dict(icaoId="KAAA", site="Airport", lat=29.43, lon=-98.5, siteType=["METAR"])]
        with patch("api_v1.geocode_address", return_value=MATCH), \
             patch.object(stations, "_awc_resource", side_effect=[(catalog, "2026-11-03T00:00:00+00:00"), OSError("offline")]):
            result = call_tool({"address": ADDRESS, "n": 3})
        self.assertFalse(result["isError"])
        data = result["structuredContent"]["data"]
        self.assertTrue(data["fewer_than_requested"])
        self.assertEqual(data["stations"][0]["observation"]["status"], "unavailable")
        self.assertTrue(data["stations"][0]["url"].startswith("https://aviationweather.gov/"))
        self.validate_result(result)


if __name__ == "__main__":
    unittest.main()
