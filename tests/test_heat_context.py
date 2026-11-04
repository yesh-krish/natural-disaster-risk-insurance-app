from datetime import datetime, timezone
import json
import math
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker
from api_spec import openapi_document
from api_v1 import dispatch
from heat_context import heat_context, heat_index_band, nws_heat_index_f, relative_humidity

NOW = 1804208400


def observation(temperature=(100-32)*5/9, dew_point=25, age=60, **fields):
    return {"status": "available", "observed_at": datetime.fromtimestamp(NOW-age, timezone.utc).isoformat(),
            "values": [{"variable": "Air temperature", "value": temperature, "unit": "°C"},
                       {"variable": "Dew point", "value": dew_point, "unit": "°C"}], **fields}


class HeatScreeningTests(unittest.TestCase):
    def test_nws_published_examples_and_humidity_adjustments(self):
        # NWS Amarillo examples rounded to whole F, plus regression/adjustment
        # reference values checked independently from the WPC equations.
        self.assertEqual(round(nws_heat_index_f(100, 55)), 124)
        self.assertEqual(round(nws_heat_index_f(100, 15)), 96)
        self.assertAlmostEqual(nws_heat_index_f(90, 70), 105.9220206, places=5)
        self.assertAlmostEqual(nws_heat_index_f(100, 10), 94.1224827, places=5)
        self.assertAlmostEqual(nws_heat_index_f(80, 90), 86.3418917, places=5)
        self.assertLess(nws_heat_index_f(76, 40), 80)  # Simple branch, not regression.

    def test_units_same_report_humidity_estimate_and_finite_json(self):
        self.assertEqual(relative_humidity(30, 30), 100)
        self.assertAlmostEqual(relative_humidity(30, 20), 55.06, delta=.1)
        result = heat_context(observation(), NOW)
        self.assertEqual(result["status"], "available")
        self.assertAlmostEqual(result["air_temperature_c"], 37.7777778)
        self.assertAlmostEqual(result["heat_index_c"], (result["heat_index_f"]-32)*5/9)
        self.assertEqual(result["observed_at"], observation()["observed_at"])
        self.assertEqual(result["category"], heat_index_band(result["heat_index_f"]))
        json.dumps(result, allow_nan=False)

    def test_unrounded_band_boundaries_are_not_work_stop_decisions(self):
        for value, expected in [(79.999, "Below listed"), (80, "Caution"), (89.999, "Caution"),
                                (90, "Extreme caution"), (102.999, "Extreme caution"),
                                (103, "Danger"), (124.999, "Danger"), (125, "Extreme danger")]:
            self.assertTrue(heat_index_band(value).startswith(expected), value)

    def test_missing_invalid_inputs_never_become_zero_or_a_band(self):
        for temp, dew in [(None, 25), (38, None), (True, 25), (38, False), (math.nan, 25),
                          (38, math.inf), (9999, 25), (38, 39), ("38", 25)]:
            result = heat_context(observation(temp, dew), NOW)
            self.assertEqual(result["status"], "unavailable", (temp, dew))
            self.assertIsNone(result["heat_index_f"])
            self.assertIsNone(result["category"])
            json.dumps(result, allow_nan=False)
        result = heat_context(observation(0, 0), NOW)
        self.assertEqual(result["air_temperature_c"], 0, "valid zero temperature retained")
        self.assertIn("envelope", result["reason"])
        for values in [[], [{"variable": "Air temperature", "value": 100, "unit": "°F"}]]:
            self.assertIsNone(heat_context(observation(values=values), NOW)["heat_index_f"])

    def test_freshness_invalid_missing_and_future_timestamps(self):
        self.assertEqual(heat_context(observation(age=7200), NOW)["status"], "available")
        for fields in [{"observed_at": None}, {"observed_at": "invalid"},
                       {"observed_at": "2026-11-02T14:00:00"}, {"status": "missing"},
                       {"status": "unavailable"}, {"status": "stale"}]:
            self.assertIsNone(heat_context(observation(**fields), NOW)["heat_index_f"], fields)
        for age, word in [(7201, "older"), (-1, "future")]:
            result = heat_context(observation(age=age), NOW)
            self.assertIn(word, result["reason"])
            self.assertIsNone(result["category"])
            self.assertIsNotNone(result["air_temperature_c"], "old raw readings remain timestamped")

    def test_display_envelope_is_explicit_and_does_not_extrapolate(self):
        for temp, dew in [(53, 25), (40, -30), (50, 50)]:
            result = heat_context(observation(temp, dew), NOW)
            self.assertEqual(result["status"], "unavailable")
            self.assertIn("envelope", result["reason"])
            self.assertIsNone(result["category"])

    def test_existing_station_route_additive_contract_and_report_selection(self):
        rows = [dict(icaoId="KAAA", lat=29.4, lon=-98.5, siteType=["METAR"]),
                dict(icaoId="KBBB", lat=29.5, lon=-98.5, siteType=["METAR"])]
        reports = [dict(icaoId="KAAA", obsTime=NOW-60, temp=38, dewp=25),
                   dict(icaoId="KAAA", obsTime=NOW-120, temp=30, dewp=20)]
        with patch("monitoring_stations._awc_resource", side_effect=[(rows, "2026-11-02T20:00:00Z"), (reports, "now")]), \
             patch("monitoring_stations.time.time", return_value=NOW):
            status, payload = dispatch("/api/v1/monitoring-stations", "lat=29.4&lon=-98.5&n=2")
        self.assertEqual(status, 200)
        stations = payload["data"]["stations"]
        self.assertEqual(stations[0]["observation"]["heat"]["air_temperature_c"], 38)
        self.assertEqual(stations[0]["observation"]["heat"]["status"], "available")
        self.assertEqual(stations[1]["observation"]["heat"]["status"], "unavailable")
        spec = openapi_document()
        schema = spec["paths"]["/api/v1/monitoring-stations"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        Draft202012Validator({**schema, "components": spec["components"]}, format_checker=FormatChecker()).validate(payload)
        json.dumps(payload, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
