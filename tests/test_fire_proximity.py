from datetime import datetime, timezone
import math
import unittest
from unittest.mock import patch

from api_v1 import dispatch
import fire_proximity as proximity
from fire_history import SOURCES


def stamp(year, month=1, day=1):
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


META = {"name": "Fire_FPA_FOD_7th_Fires", "timeInfo": {"timeExtent": [stamp(1992), stamp(2024, 12, 31)]}}
COUNTY = {"features": [{"attributes": {"COUNTY": "Bexar", "STATE": "Texas", "STCOFIPS": "48029"}}]}


def feature(oid=1, year=2000, lat=29.01, lon=-98):
    return {"attributes": {"objectid": oid, "fod_id": oid, "fire_name": "Test fire",
                           "discovery_date": stamp(year), "fire_size": 10},
            "geometry": {"x": lon, "y": lat}}


def record(oid, year, distance, acres=1.0):
    return {"id": str(oid), "name": f"Fire {oid}", "discovery_date": f"{year}-01-01", "distance_km": distance,
            "reported_acres": acres, "record_url": f"https://example.test/{oid}"}


class FireProximityTests(unittest.TestCase):
    def setUp(self):
        proximity._cache.clear()

    def test_years_count_once_and_zero_years_stay_in_denominator(self):
        records = [record(1, 2000, 2), record(2, 2000, 3), record(3, 2001, 7), record(4, 2002, 18),
                   record(5, 2025, 1), record(6, 1991, 1), record(7, 2003, 20.1)]
        frequencies, rows = proximity.summarize(records, 1992, 2024)
        self.assertEqual([item["years_with_fire"] for item in frequencies], [1, 2, 3])
        self.assertEqual([item["total_years"] for item in frequencies], [33, 33, 33])
        self.assertAlmostEqual(frequencies[0]["frequency_pct"], 100 / 33)
        self.assertEqual(rows[0]["within_radii"], {"5": False, "10": False, "20": False})
        self.assertEqual(rows[8]["reported_fire_count_within_20_km"], 2)
        self.assertEqual(rows[8]["nearest_distance_within_20_km"], 2)
        self.assertIsNone(rows[0]["nearest_distance_within_20_km"])

    def test_boundary_radii_and_all_or_zero_years(self):
        frequencies, _ = proximity.summarize([record(1, 2000, 5), record(2, 2001, 10), record(3, 2002, 20)], 2000, 2002)
        self.assertEqual([item["years_with_fire"] for item in frequencies], [1, 2, 3])
        zero, _ = proximity.summarize([], 1992, 2024)
        self.assertEqual(zero[0]["frequency_pct"], 0)
        self.assertGreater(zero[0]["reference_interval"]["maximum_pct"], 0)
        full = proximity.wilson_interval(33, 33)
        self.assertAlmostEqual(full["maximum_pct"], 100)
        self.assertGreater(full["minimum_pct"], 89)

    def test_circle_geometry(self):
        self.assertAlmostEqual(proximity.footprint_radius_km(1), 0.03589, places=4)
        self.assertEqual(proximity.circle_overlap_km2(5, 1, 10), 0)
        self.assertAlmostEqual(proximity.circle_overlap_km2(5, 1, 0), math.pi)
        self.assertAlmostEqual(proximity.circle_overlap_km2(1, 5, 2), math.pi)
        self.assertAlmostEqual(proximity.circle_overlap_km2(5, 5, 5), 50 * math.pi / 3 - 12.5 * math.sqrt(3))
        self.assertAlmostEqual(proximity._omitted_acres(20), 310521.78, places=0)

    def test_size_thresholds_count_each_year_once_and_ignore_missing_size(self):
        records = [record(1, 2000, 2, 0.5), record(2, 2000, 4, 150), record(3, 2000, 4.5, 200),
                   record(4, 2001, 8, 1200), record(5, 2002, 18, 6000), record(6, 2003, 1, None),
                   record(7, 2030, 1, 9000)]
        result = proximity.size_exceedance(records, 1992, 2024)
        table = {(row["min_acres"], row["radius_km"]): row["years_with_fire"] for row in result["threshold_frequencies"]}
        self.assertEqual(table[(10, 5)], 1)
        self.assertEqual(table[(100, 10)], 2)
        self.assertEqual(table[(1000, 5)], 0)
        self.assertEqual(table[(1000, 20)], 2)
        self.assertEqual(table[(5000, 20)], 1)
        self.assertEqual(len(result["threshold_frequencies"]), 12)
        self.assertEqual((result["records_in_query"], result["records_missing_size"]), (6, 1))

    def test_area_exceedance_curve_uses_rates_and_stops_above_largest_fire(self):
        records = [record(1, 2000, 2, 0.5), record(2, 2000, 4, 150), record(3, 2000, 4.5, 200),
                   record(4, 2001, 8, 1200), record(5, 2002, 18, 6000), record(6, 2003, 1, None)]
        curves = proximity.size_exceedance(records, 1992, 2024)["area_exceedance"]["curves"]
        five = curves[0]
        self.assertEqual((five["radius_km"], five["fire_count"], five["largest_reported_acres"]), (5, 3, 200))
        self.assertAlmostEqual(five["expected_annual_reported_acres"], 350.5 / 33)
        by_size = {point["min_acres"]: point for point in five["points"]}
        self.assertEqual(max(by_size), 300)  # first threshold with no fire, then stop
        self.assertEqual(by_size[300]["fires_at_or_above"], 0)
        self.assertIsNone(by_size[300]["return_period_years"])
        hundred = by_size[100]
        self.assertEqual(hundred["fires_at_or_above"], 2)
        self.assertAlmostEqual(hundred["expected_annual_exceedances"], 2 / 33)
        self.assertAlmostEqual(hundred["annual_probability_pct"], (1 - math.exp(-2 / 33)) * 100)
        self.assertAlmostEqual(hundred["empirical_years_pct"], 100 / 33)  # both fires in 2000
        self.assertAlmostEqual(hundred["return_period_years"], 1 / (1 - math.exp(-2 / 33)))
        twenty = curves[2]
        self.assertEqual(twenty["largest_reported_acres"], 6000)
        self.assertEqual(twenty["points"][-1]["min_acres"], 10000)
        empty = proximity.size_exceedance([], 1992, 2024)["area_exceedance"]["curves"][0]
        self.assertEqual((empty["fire_count"], len(empty["points"])), (0, 1))
        self.assertIsNone(empty["largest_reported_acres"])

    def test_footprint_reach_and_burned_area(self):
        # A 100,000-acre fire (11.35 km equivalent radius) reported 8 km away reaches the point.
        records = [record(1, 2005, 8, 100000), record(2, 2006, 0.5, 1)]
        result = proximity.size_exceedance(records, 1992, 2024)
        reach = result["footprint_reach"]
        self.assertEqual(reach["qualifying_years"], [2005])
        self.assertEqual(reach["fires"][0]["id"], "1")
        self.assertEqual(reach["closest_footprint_gap_km"], 0)
        five = result["burned_area"][0]
        self.assertEqual(five["radius_km"], 5)
        expected = proximity.circle_overlap_km2(5, proximity.footprint_radius_km(100000), 8) + 1 * proximity.KM2_PER_ACRE
        self.assertAlmostEqual(five["overlapping_reported_area_km2"], expected)
        self.assertAlmostEqual(five["mean_annual_burned_share_pct"], expected / (25 * math.pi) / 33 * 100)
        self.assertLess(five["overlapping_reported_area_km2"], 25 * math.pi)
        self.assertAlmostEqual(five["omitted_fire_min_acres"], proximity._omitted_acres(15))

    def test_small_nearby_fires_do_not_reach(self):
        result = proximity.size_exceedance([record(1, 2011, 0.6, 1)], 1992, 2024)
        self.assertEqual(result["footprint_reach"]["years_with_reach"], 0)
        self.assertAlmostEqual(result["footprint_reach"]["closest_footprint_gap_km"], 0.6 - 0.03589, places=4)
        empty = proximity.size_exceedance([], 1992, 2024)
        self.assertIsNone(empty["footprint_reach"]["closest_footprint_gap_km"])
        self.assertIsNone(empty["burned_area"][0]["equivalent_return_interval_years"])

    def test_complete_baseline_excludes_partial_edges_and_current_year(self):
        now = datetime(2026, 11, 5, tzinfo=timezone.utc)
        self.assertEqual(proximity._baseline(META, now)["year_count"], 33)
        meta = {"name": "New edition", "timeInfo": {"timeExtent": [stamp(1992, 2, 1), stamp(2026, 12, 31)]}}
        baseline = proximity._baseline(meta, now)
        self.assertEqual((baseline["start_year"], baseline["end_year"]), (1993, 2025))
        with self.assertRaises(OSError):
            proximity._baseline({"name": "Short", "timeInfo": {"timeExtent": [stamp(2020), stamp(2024, 12, 31)]}}, now)

    def test_archive_only_not_live_incidents_and_deduplicates(self):
        with patch.object(proximity, "_json", side_effect=[COUNTY, META]), patch.object(proximity, "_records", return_value=[feature(), feature()]) as records:
            result = proximity.fire_proximity(29, -98)
        self.assertEqual(records.call_args.args[0]["id"], "fpa_fod")
        self.assertEqual(records.call_args.args[3], 20)
        self.assertEqual(result["frequencies"][0]["years_with_fire"], 1)
        self.assertEqual(result["annual_records"][8]["reported_fire_count_within_20_km"], 1)
        self.assertFalse(result["baseline"]["local_reporting_completeness_verified"])
        self.assertEqual(result["size_exceedance"]["records_in_query"], 1)
        self.assertEqual(result["size_exceedance"]["threshold_frequencies"][0]["years_with_fire"], 1)

    def test_no_county_coverage_is_unavailable_not_zero(self):
        with patch.object(proximity, "_json", return_value={"features": []}), patch.object(proximity, "_records") as records:
            result = proximity.fire_proximity(0, 0)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["frequencies"], [])
        self.assertIsNone(result["baseline"])
        records.assert_not_called()

    def test_invalid_returned_record_and_truncated_data_fail(self):
        invalid = feature()
        invalid["attributes"]["discovery_date"] = None
        with patch.object(proximity, "_json", side_effect=[COUNTY, META]), patch.object(proximity, "_records", return_value=[invalid]):
            self.assertEqual(dispatch("/api/v1/fire-proximity", "lat=29&lon=-98")[0], 502)
        with patch.object(proximity, "_json", side_effect=[COUNTY, META]), patch.object(proximity, "_records", side_effect=OSError("Incomplete page")):
            code, payload = dispatch("/api/v1/fire-proximity", "lat=29&lon=-98")
        self.assertEqual(code, 502)
        self.assertIsNone(payload["data"])

    def test_provider_error_and_invalid_inputs_do_not_become_zero(self):
        with patch.object(proximity, "_json", side_effect=OSError("offline")):
            self.assertEqual(dispatch("/api/v1/fire-proximity", "lat=29&lon=-98")[0], 502)
        for query in ["lat=nan&lon=0", "lat=91&lon=0", "lat=1", "lat=1&lon=2&days=30",
                      "lat=1&lon=2&lat=3", "lat=1&lon=2&address=x", "lat=&lon=2"]:
            with patch("api_v1.fire_proximity") as provider:
                self.assertEqual(dispatch("/api/v1/fire-proximity", query)[0], 400, query)
                provider.assert_not_called()

    def test_api_schema_and_cache_expiry(self):
        from api_spec import openapi_document
        from jsonschema import Draft202012Validator
        with patch.object(proximity, "_json", side_effect=[COUNTY, META, COUNTY, META]) as query, patch.object(proximity, "_records", return_value=[feature()]) as records:
            code, payload = dispatch("/api/v1/fire-proximity", "lat=29&lon=-98")
            self.assertEqual(code, 200)
            result = payload["data"]
            Draft202012Validator(openapi_document()["components"]["schemas"]["FireProximity"]).validate(result)
            result["frequencies"][0]["years_with_fire"] = 999
            self.assertEqual(proximity.fire_proximity(29, -98)["frequencies"][0]["years_with_fire"], 1)
            self.assertEqual(records.call_count, 1)
            proximity._cache[(29, -98)] = (-999999999, proximity._cache[(29, -98)][1])
            proximity.fire_proximity(29, -98)
            self.assertEqual(records.call_count, 2)
            self.assertEqual(query.call_count, 4)


if __name__ == "__main__":
    unittest.main()
