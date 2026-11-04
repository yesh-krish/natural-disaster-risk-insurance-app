"""Probability denominator, chronological coverage and physical moisture checks."""
import calendar
from datetime import datetime, timedelta
import json
import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import potential_fire as fire
from api_v1 import comparison, dispatch
from api_spec import openapi_document
from area_hazard import area_disaster
from decimal import Decimal
from jsonschema import Draft202012Validator


def weather(year=2020):
    start = datetime(year, 1, 1) - timedelta(days=31)
    n = ((366 if calendar.isleap(year) else 365) + 31) * 24
    return {"utc_offset_seconds": 0, "hourly_units": dict(zip(fire.VARIABLES, ("°C", "%", "mm", "m/s", "°", "m"))),
            "hourly": {"time": [(start + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(n)],
                       **{k: [v] * n for k, v in zip(fire.VARIABLES, (20, 30, 0, 1, 0, 0))}}}


LANDSCAPE = {"fuel_model": 101, "slope_degrees": 0, "aspect_degrees": 0,
             "canopy_height_m": 0, "canopy_cover_fraction": 0}


class StubEngine:
    """Intensity equal to wind input: isolates annual aggregation from model physics."""
    def get_fuel_model(self, number):
        return SimpleNamespace(delta=1)

    def moisturize(self, *args):
        return None

    def calc_surface_fire_behavior_no_wind_no_slope(self, *args):
        return None

    def wind_speed_10m_to_wind_speed_20ft(self, wind):
        return wind

    def calc_midflame_wind_speed(self, wind, *args):
        return wind

    def calc_surface_fire_behavior_max(self, base, wind, *args):
        return SimpleNamespace(max_fireline_intensity=wind, max_spread_rate=60)

    def calc_areal_heat_output(self, rate, intensity):
        return intensity


class MoistureTests(unittest.TestCase):
    def test_time_lag_matches_analytic_solution(self):
        _, dry = fire.equilibrium(25, 20)
        result = fire.moisture_step([0.5] * 3, 25, 20, 0)
        for lag, actual in zip((1, 10, 100), result):
            self.assertAlmostEqual(actual, dry + (0.5 - dry) * math.exp(-1 / lag))

    def test_hysteresis_retains_between_equilibria(self):
        wet, dry = fire.equilibrium(25, 50)
        state = [(wet + dry) / 2] * 3
        self.assertEqual(fire.moisture_step(state, 25, 50, 0), state)

    def test_rain_increases_moisture_with_class_specific_lag(self):
        result = fire.moisture_step([0.05] * 3, 25, 20, 8.05)
        self.assertGreater(result[0], result[1])
        self.assertGreater(result[1], result[2])
        self.assertGreater(result[2], 0.05)
        expected = 2.5 + (0.05 - 2.5) * math.exp(-(1 - math.exp(-1)) / 14)
        self.assertAlmostEqual(result[1], expected)

    def test_rain_below_threshold_does_not_soak(self):
        self.assertEqual(fire.moisture_step([0.1] * 3, 25, 20, 0.05), fire.moisture_step([0.1] * 3, 25, 20, 0))


class AnnualTests(unittest.TestCase):
    def setUp(self):
        stub = StubEngine()
        self.mock = patch.object(fire, "engine", return_value=(stub, stub, stub))
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def test_leap_year_maximum_ignores_spinup(self):
        doc = weather()
        doc["hourly"]["wind_speed_10m"][0] = 100
        doc["hourly"]["wind_speed_10m"][-1] = 5
        result = fire.annual_maximum(doc, 2020, LANDSCAPE)
        self.assertTrue(result["complete"])
        self.assertEqual(result["valid_hours"], 8784)
        self.assertEqual(result["maximum_kw_m"], 300)
        self.assertEqual(result["peak"]["time_utc"], "2020-12-31T23:00Z")

    def test_missing_input_excludes_entire_year(self):
        doc = weather()
        doc["hourly"]["rain"][5000] = None
        result = fire.annual_maximum(doc, 2020, LANDSCAPE)
        self.assertFalse(result["complete"])
        self.assertNotIn("maximum_kw_m", result)

    def test_missing_spinup_also_excludes_year(self):
        doc = weather()
        doc["hourly"]["rain"][0] = None
        self.assertFalse(fire.annual_maximum(doc, 2020, LANDSCAPE)["complete"])

    def test_duplicate_hours_and_wrong_units_rejected(self):
        doc = weather()
        doc["hourly"]["time"][1] = doc["hourly"]["time"][0]
        with self.assertRaises(ValueError):
            fire.annual_maximum(doc, 2020, LANDSCAPE)
        doc = weather()
        doc["hourly_units"]["wind_speed_10m"] = "km/h"
        with self.assertRaises(ValueError):
            fire.annual_maximum(doc, 2020, LANDSCAPE)

    def test_snow_covered_year_is_included_as_zero(self):
        doc = weather(2019)
        doc["hourly"]["snow_depth"] = [0.1] * len(doc["hourly"]["time"])
        result = fire.annual_maximum(doc, 2019, LANDSCAPE)
        self.assertTrue(result["complete"])
        self.assertEqual(result["valid_hours"], 8760)
        self.assertEqual(result["maximum_kw_m"], 0)

    def test_annual_denominator_includes_zeros_excludes_missing_and_retains_ties(self):
        years = [{"complete": True, "maximum_kw_m": x} for x in (0, 100, 100, 200)]
        years += [{"complete": False}]
        points = fire.empirical_points(years)
        self.assertEqual([(p["severity"], p["frequency_pct"]) for p in points], [(0, 100), (100, 75), (200, 25)])
        self.assertEqual(points[-1]["exceedance_years"], 1)
        self.assertEqual(points[-1]["complete_years"], 4)


class IntegrationTests(unittest.TestCase):
    def test_national_region_routing(self):
        self.assertEqual(fire.region(30, -97), "CONUS")
        self.assertEqual(fire.region(64, -150), "AK")
        self.assertEqual(fire.region(52, 179), "AK")
        self.assertEqual(fire.region(20, -156), "HI")

    def test_point_only_area_does_not_download_25_baselines(self):
        with patch("area_hazard.disaster_at") as provider:
            result = area_disaster(30, -98, 30.1, -97.9, "potential_fire_intensity")
        provider.assert_not_called()
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("Point mode", result["reason"])

    def test_api_retains_potential_interpretation_and_comparison_basis(self):
        curve = {"status": "available", "peril": "potential_fire_intensity", "points": [{"severity": 100, "frequency_pct": 25}],
                 "actual_wildfire_probability_available": False, "hazard_kind": "potential_intensity_exceedance"}
        with patch("api_v1.disaster_at", return_value=curve):
            code, response = dispatch("/api/v1/hazards/potential_fire_intensity", "lat=30&lon=-97")
        self.assertEqual(code, 200)
        self.assertFalse(response["data"]["actual_wildfire_probability_available"])
        result = comparison(curve, Decimal("10000"))
        self.assertEqual(result["frequency_basis"], "historical_potential_condition_frequency")
        self.assertEqual(result["comparisons"][0]["expected_annual_contribution_usd"]["minimum"], 2500)
        self.assertIn("whether or not a fire occurs", result["assumptions"][-1])

    def test_real_baseline_contract_when_prepared(self):
        path = fire.CACHE / "bastrop-baseline.json"
        if not path.exists():
            self.skipTest("Run the documented CLI to prepare the optional live-data fixture")
        curve = json.loads(path.read_text(encoding="utf-8"))
        spec = openapi_document()
        schema = {"$ref": "#/components/schemas/Hazard", "components": spec["components"]}
        Draft202012Validator(schema).validate(curve)
        self.assertEqual(curve["baseline"]["complete_years"], 30)
        self.assertTrue(all(a["frequency_pct"] >= b["frequency_pct"] for a, b in zip(curve["points"], curve["points"][1:])))
        self.assertEqual(curve["points"][-1]["frequency_pct"], 100 / 30)
        for row in curve["annual_maxima"]:
            p = row["peak"]
            self.assertAlmostEqual(p["intensity_kw_m"], p["areal_heat_release_kj_m2"] * p["spread_rate_m_s"], places=8)


if __name__ == "__main__":
    unittest.main()
