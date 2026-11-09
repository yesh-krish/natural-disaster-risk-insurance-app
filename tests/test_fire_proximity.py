from datetime import datetime, timezone
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


def record(oid, year, distance):
    return {"id": str(oid), "discovery_date": f"{year}-01-01", "distance_km": distance}


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
