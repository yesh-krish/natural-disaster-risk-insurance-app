import math
import unittest
from unittest.mock import patch

from area_hazard import area_disaster, probability_at, validate_area


def curve(points, **extra):
    return {"peril": "rainfall", "status": "available", "points": [
        {"severity": x, "frequency_pct": p} for x, p in points],
        "unit": "mm", "notes": [], **extra}


class AreaHazardTests(unittest.TestCase):
    def test_common_threshold_average_and_raw_sample_curves(self):
        def provider(peril, lat, lon, site):
            return curve([(10, lat + lon + 10), (20, (lat + lon + 10) / 2)])
        result = area_disaster(0, 0, 4, 4, "rainfall", provider=provider)
        self.assertEqual(result["area"]["sample_count"], 25)
        self.assertEqual(result["area"]["centroid"], {"latitude": 2, "longitude": 2})
        low, high = result["points"]
        self.assertEqual(low["frequency_pct"], 14)
        self.assertEqual(high["frequency_pct"], 7)
        self.assertEqual(low["maximum_frequency_pct"], 18)
        self.assertEqual(low["minimum_frequency_pct"], 10)
        self.assertEqual(low["sum_frequency_pct"], 350)
        self.assertEqual(len(low["sample_values"]), 25)
        self.assertEqual(result["area"]["aggregation"], "arithmetic_mean")
        self.assertEqual(result["area"]["samples"][0]["points"], curve([(10, 10), (20, 5)])["points"])
        self.assertEqual(low["maximum_centroid"], {"latitude": 4, "longitude": 4})
        self.assertEqual(low["evaluated_samples"], 25)

    def test_tie_centroid_and_location_changes_with_trigger(self):
        def provider(peril, lat, lon, site):
            return curve([(10, 30 - lat), (20, 10 + lat)])
        result = area_disaster(0, 0, 4, 4, "rainfall", provider=provider)
        self.assertEqual(result["points"][0]["maximum_centroid"], {"latitude": 0, "longitude": 2})
        self.assertEqual(result["points"][1]["maximum_centroid"], {"latitude": 4, "longitude": 2})
        self.assertEqual(len(result["points"][0]["maximum_locations"]), 5)

    def test_interpolation_compares_same_severity(self):
        def provider(peril, lat, lon, site):
            return curve([(10, 10), (20, 1)]) if lat == 0 else curve([(5, 80), (25, .8)])
        result = area_disaster(0, 0, 4, 4, "rainfall", provider=provider)
        point = next(p for p in result["points"] if p["severity"] == 10)
        self.assertAlmostEqual(point["frequency_pct"], (5 * 10 + 20 * 80 * .01 ** .25) / 25)
        self.assertEqual(point["interpolated_samples"], 20)
        self.assertEqual(point["evaluated_samples"], 25)

    def test_missing_and_out_of_range_are_not_zero(self):
        def provider(peril, lat, lon, site):
            if lat == 4:
                raise OSError("source down")
            return curve([(10, 0), (20, 0)]) if lat == 0 else curve([(20, 5), (30, 1)])
        result = area_disaster(0, 0, 4, 4, "rainfall", provider=provider)
        point = next(p for p in result["points"] if p["severity"] == 10)
        self.assertEqual(point["frequency_pct"], 0)
        self.assertEqual(point["evaluated_samples"], 5)
        self.assertEqual(point["missing_samples"], 20)
        self.assertEqual(result["area"]["available_samples"], 20)

    def test_average_counts_zero_and_excludes_missing_for_every_hazard(self):
        def provider(peril, lat, lon, site):
            if lat == 0:
                return {"status": "unavailable", "points": []}
            return curve([(10, (lat - 1) * 10), (20, (lat - 1) * 5)], peril=peril)
        for peril in ("flood", "earthquake", "wildfire", "cyclone", "rainfall"):
            with self.subTest(peril=peril):
                result = area_disaster(0, 0, 4, 4, peril, provider=provider)
                point = result["points"][0]
                self.assertEqual(point["frequency_pct"], 15)
                self.assertEqual(point["sum_frequency_pct"], 300)
                self.assertEqual(point["evaluated_samples"], 20)
                self.assertEqual(point["missing_samples"], 5)
                self.assertEqual(sum(p["frequency_pct"] is None for p in point["sample_values"]), 5)
                self.assertEqual(sum(p["frequency_pct"] == 0 for p in point["sample_values"]), 5)

    def test_all_unavailable_returns_no_maximum(self):
        result = area_disaster(0, 0, 4, 4, "rainfall", provider=lambda *args: {"status": "unavailable", "points": []})
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["points"], [])
        self.assertEqual(result["area"]["available_samples"], 0)

    def test_probability_brackets_duplicate_and_missing_levels(self):
        points = curve([(10, 10), (20, 1)])["points"]
        self.assertEqual(probability_at(points, 10), (10, "source"))
        self.assertAlmostEqual(probability_at(points, 15)[0], math.sqrt(10))
        self.assertIsNone(probability_at(points, 25))
        self.assertIsNone(probability_at(curve([(10, 10), (20, 0)])["points"], 15))
        self.assertIsNone(probability_at(curve([(10, 1), (20, 10)])["points"], 15))
        self.assertIsNone(probability_at(points + [{"severity": None, "frequency_pct": 5}], 15))
        self.assertEqual(probability_at(points + [{"severity": 10, "frequency_pct": 20}], 10)[0], 20)

    def test_invalid_bounds_and_options(self):
        for bounds in [(4, 0, 0, 4), (0, 4, 4, 0), (0, 0, 0, 4), (0, 0, math.nan, 4), (0, 0, 91, 4)]:
            with self.assertRaises(ValueError):
                validate_area(*bounds, "flood", "BC")
        with self.assertRaises(ValueError):
            validate_area(0, 0, 4, 4, "invalid", "BC")

    def test_request_handler_validates_and_returns_computed_payload(self):
        from server import Handler
        handler = Handler.__new__(Handler)
        responses = []
        handler.send_json = lambda status, data: responses.append((status, data))
        handler.path = "/api/disaster-area?peril=rainfall&south=0&west=0&north=4&east=4"
        with patch("area_hazard.disaster_at", side_effect=lambda *args: curve([(10, 10), (20, 1)])):
            handler.do_GET()
        self.assertEqual(responses[-1][0], 200)
        self.assertEqual(responses[-1][1]["points"][0]["frequency_pct"], 10)
        self.assertEqual(responses[-1][1]["points"][0]["maximum_centroid"], {"latitude": 2, "longitude": 2})
        handler.path = "/api/disaster-area?peril=rainfall&south=4&west=0&north=0&east=4"
        handler.do_GET()
        self.assertEqual(responses[-1][0], 400)
        handler.path = "/api/disaster-area?peril=rainfall&south=0&west=0&north=4&east=4"
        with patch("area_hazard.area_disaster", side_effect=OSError("service down")):
            handler.do_GET()
        self.assertEqual(responses[-1][0], 502)

    def test_auto_holds_centroid_hazard_fixed_for_grid(self):
        chosen = curve([(10, 10), (20, 1)], selection={"explanation": "centroid context"})
        calls = []
        def provider(peril, lat, lon, site):
            calls.append((peril, site))
            return curve([(10, 5), (20, .5)])
        with patch("hazard_relevance.most_relevant", return_value=chosen):
            result = area_disaster(0, 0, 4, 4, "auto", "C", provider=provider)
        self.assertEqual(set(calls), {("rainfall", "C")})
        self.assertEqual(len(calls), 24)
        self.assertEqual(result["selection"], chosen["selection"])
        self.assertEqual(result["points"][0]["frequency_pct"], 5.2)
        self.assertEqual(result["points"][0]["maximum_centroid"], {"latitude": 2, "longitude": 2})


if __name__ == "__main__":
    unittest.main()
