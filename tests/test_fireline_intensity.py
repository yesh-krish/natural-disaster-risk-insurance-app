import json
import math
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from api_spec import openapi_document
from api_v1 import dispatch
from area_hazard import area_disaster
from disaster_data import disaster_at
from fireline_intensity import SERVICE, STATISTIC, fireline_intensity, source_metadata, source_sample
from hazard_engine import ModelUnavailable
from jsonschema import Draft202012Validator
from monitoring_stations import nearest_station
from tests.test_api_v1 import http_request


METADATA = {"pixelType": "F32", "bandCount": 1, "pixelSizeX": 10, "pixelSizeY": 10,
            "description": "This dataset represents the weighted-average fireline intensity in kilowatts per meter. FLI is calculated using heading-only fire behavior.",
            "extent": {"spatialReference": {"wkid": 102100}}}


class FirelineIntensityTests(unittest.TestCase):
    def setUp(self):
        source_metadata.cache_clear()
        source_sample.cache_clear()

    def tearDown(self):
        source_metadata.cache_clear()
        source_sample.cache_clear()

    @staticmethod
    def fetch(value):
        def response(url):
            return (METADATA, "a" * 64) if "/getSamples?" not in url else ({"samples": [{"value": value}]}, "b" * 64)
        return response

    def assert_contract(self, payload, route):
        spec = openapi_document()
        schema = spec["paths"][route]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        Draft202012Validator({**schema, "components": spec["components"]}).validate(payload)
        json.dumps(payload, allow_nan=False)

    def test_native_units_raw_sampling_and_reproducible_provenance(self):
        with patch("fireline_intensity.fetch_json", side_effect=self.fetch("71.296150208")) as fetch:
            result = disaster_at("fireline_intensity", 29.62, -98.6)
            repeated = disaster_at("fireline_intensity", 29.62, -98.6)
        self.assertEqual(result, repeated)
        self.assertEqual(fetch.call_count, 2, "Metadata and exact samples are cached")
        self.assertEqual(result["hazard_value"]["value"], 71.296150208)
        self.assertEqual(result["hazard_value"]["statistic"], STATISTIC)
        self.assertEqual(result["unit"], "kW/m")
        self.assertEqual(result["points"], [])
        self.assertFalse(result["annual_probability_available"])
        self.assertEqual(result["provenance"]["sample_sha256"], "b" * 64)
        self.assertEqual(result["provenance"]["metadata_sha256"], "a" * 64)
        query = parse_qs(urlsplit(fetch.call_args.args[0]).query)
        geometry = json.loads(query["geometry"][0])
        self.assertEqual((geometry["x"], geometry["y"]), (-98.6, 29.62))
        self.assertEqual(geometry["spatialReference"]["wkid"], 4326)
        self.assertEqual(json.loads(query["renderingRule"][0]), {"rasterFunction": "None"})
        self.assertEqual(query["interpolation"], ["RSP_NearestNeighbor"])

    def test_native_zero_is_available_but_nodata_stays_missing(self):
        for raw, status, value in [("0.000000000", "available", 0), ("", "unavailable", None),
                                   ("NoData", "unavailable", None), (None, "unavailable", None)]:
            source_sample.cache_clear()
            with self.subTest(raw=raw), patch("fireline_intensity.fetch_json", side_effect=self.fetch(raw)):
                result = fireline_intensity(29.4, -98.49)
            self.assertEqual(result["status"], status)
            self.assertEqual(result["hazard_value"]["value"], value)
            self.assertEqual(result["points"], [])

    def test_bad_metadata_or_values_never_become_probabilities(self):
        with patch("fireline_intensity.fetch_json", return_value=({**METADATA, "description": "Classified flame length"}, "x")):
            result = disaster_at("fireline_intensity", 29.4, -98.49)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["annual_probability_available"])
        for value in ["-9999", "inf", "bad", True]:
            source_metadata.cache_clear()
            with self.subTest(value=value), patch("fireline_intensity.fetch_json", side_effect=self.fetch(value)):
                with self.assertRaises(ModelUnavailable):
                    fireline_intensity(29.4, -98.49)

    def test_source_errors_are_unavailable_and_invalid_coordinates_stop_before_fetch(self):
        for doc in [{"error": {"code": 500}}, {"samples": "bad"}, {"samples": [{"value": "1"}, {"value": "2"}]}]:
            with patch("fireline_intensity.fetch_json", side_effect=[(METADATA, "x"), (doc, "y")]):
                source_metadata.cache_clear()
                result = disaster_at("fireline_intensity", 29.4, -98.49)
            self.assertEqual(result["status"], "unavailable")
        with patch("fireline_intensity.fetch_json") as fetch:
            for lat, lon in [(math.nan, 0), (91, 0), (0, 181)]:
                with self.assertRaises(ValueError):
                    fireline_intensity(lat, lon)
            fetch.assert_not_called()

    def test_point_and_comparison_http_contracts(self):
        with patch("fireline_intensity.fetch_json", side_effect=self.fetch("71.296150208")):
            status, payload = dispatch("/api/v1/hazards/fireline_intensity", "lat=29.62&lon=-98.6")
            self.assertEqual(status, 200)
            self.assertEqual(payload["status"], "available")
            self.assert_contract(payload, "/api/v1/hazards/{peril}")
            status, compared = dispatch("/api/v1/trigger-comparisons/fireline_intensity", "lat=29.62&lon=-98.6&payout_usd=50000")
            self.assertEqual(status, 200)
            self.assertEqual(compared["status"], "unavailable")
            self.assertEqual(compared["data"]["frequency_basis"], "unavailable")
            self.assertEqual(compared["data"]["comparisons"], [])
            self.assertNotIn("expected_annual_contribution_usd", compared["data"])
            self.assert_contract(compared, "/api/v1/trigger-comparisons/{peril}")
            for route in ["/api/disaster?peril=fireline_intensity&lat=29.62&lon=-98.6",
                          "/api/v1/hazards/fireline_intensity?lat=29.62&lon=-98.6"]:
                self.assertEqual(http_request(route)[0], 200)

    def test_area_means_intensity_with_zeros_missing_and_full_sample_provenance(self):
        def provider(peril, lat, lon, site):
            value = None if lat == 0 else lon * 100
            return {"peril": peril, "status": "unavailable" if value is None else "available", "unit": "kW/m",
                    "hazard_value": {"value": value, "statistic": STATISTIC}, "points": [],
                    "provenance": {"sample_sha256": f"{lat},{lon}"}}
        result = area_disaster(0, 0, 4, 4, "fireline_intensity", provider=provider)
        self.assertEqual(result["hazard_value"]["value"], 200)
        self.assertEqual(result["area"]["available_samples"], 20)
        self.assertEqual(result["area"]["summary"], {"minimum": 0, "maximum": 400, "sum": 4000, "missing_samples": 5})
        self.assertEqual(len(result["area"]["samples"]), 25)
        self.assertEqual(result["points"], [])
        self.assertFalse(result["annual_probability_available"])
        self.assertIsNone(result["area"]["samples"][0]["value"])
        self.assertEqual(result["area"]["samples"][5]["value"], 0)
        with patch("api_v1.area_disaster", return_value=result):
            _, payload = dispatch("/api/v1/areas/fireline_intensity", "south=0&west=0&north=4&east=4")
        self.assert_contract(payload, "/api/v1/areas/{peril}")

    def test_area_no_data_and_station_uses_fire_weather_network(self):
        result = area_disaster(0, 0, 1, 1, "fireline_intensity", provider=lambda *args: {"status": "unavailable", "points": []})
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["hazard_value"]["value"])
        self.assertEqual(result["area"]["available_samples"], 0)
        with patch("monitoring_stations._candidates", return_value=[]) as candidates:
            station = nearest_station("fireline_intensity", 29.62, -98.6)
        self.assertEqual(station["peril"], "fireline_intensity")
        self.assertTrue(all(call.args[0] == "wildfire" for call in candidates.call_args_list))
        self.assertIn("do not measure", station["message"])


if __name__ == "__main__":
    unittest.main()
