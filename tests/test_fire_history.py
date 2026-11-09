import unittest
from unittest.mock import patch

import fire_history as history
from api_v1 import dispatch


def feature(source, oid, x, y, name="Fire", date=946684800000, size=None):
    if source["id"] == "fpa_fod":
        attrs = {"objectid": oid, "fod_id": oid, "fire_name": name, "discovery_date": date, "fire_size": size}
    else:
        attrs = {"OBJECTID": oid, "IrwinID": str(oid), "IncidentName": name, "FireDiscoveryDateTime": date, "IncidentSize": size}
    return {"attributes": attrs, "geometry": {"x": x, "y": y}}


class FireHistoryTests(unittest.TestCase):
    def setUp(self):
        history._cache.clear()

    def test_older_nearer_record_beats_recent_fire_across_sources(self):
        def records(source, lat, lon, radius, deadline, max_pages=100):
            return [feature(source, 1, -98.001 if source["id"] == "fpa_fod" else -98.01,
                            29, name=source["id"], date=694224000000 if source["id"] == "fpa_fod" else 1735689600000)]
        with patch.object(history, "_records", side_effect=records) as fetch:
            data = history.nearest_fire(29, -98)
        self.assertEqual(data["fire"]["name"], "fpa_fod")
        self.assertEqual(data["fire"]["discovery_date"], "1992-01-01")
        self.assertEqual(data["time_window"], "all_available_history")
        self.assertEqual(fetch.call_count, 2)
        self.assertIsNone(data["fire"]["reported_acres"])

    def test_expands_and_does_not_accept_a_bounding_box_corner(self):
        calls = []
        def records(source, lat, lon, radius, deadline, max_pages=100):
            calls.append((source["id"], radius))
            return [feature(source, 1, .015, .015), feature(source, 2, 0, .03)]
        with patch.object(history, "_records", side_effect=records):
            result = history.nearest_fire(0, 0)
        self.assertEqual(result["searched_radius_km"], 10)
        self.assertEqual(sorted(radius for _, radius in calls), [2, 2, 10, 10])
        self.assertEqual(result["fire"]["id"], "1")

    def test_far_point_narrows_dense_radius_instead_of_failing(self):
        # Point far from records: 2-250 km are empty, 1250 km covers a dense region (too many to download).
        nearest_km = 700
        def records(source, lat, lon, radius, deadline, max_pages=100):
            if radius > 900:
                raise history.TooManyRecords("page budget")
            return [feature(source, 1, 0, nearest_km / 111.195)] if radius >= nearest_km else []
        def count(source, lat, lon, radius, deadline):
            return 10**6 if radius > 900 else 10
        with patch.object(history, "_records", side_effect=records), \
                patch.object(history, "_count", side_effect=count) as counts:
            result = history.nearest_fire(0, 0)
        self.assertEqual(result["status"], "available")
        self.assertAlmostEqual(result["fire"]["distance_km"], nearest_km, delta=0.5)
        self.assertGreater(result["searched_radius_km"], 250)
        self.assertLess(result["searched_radius_km"], 900)
        self.assertGreater(counts.call_count, 0)

    def test_count_query_sums_envelopes_and_rejects_bad_counts(self):
        source = history.SOURCES[0]
        with patch.object(history, "_json", return_value={"count": 7}) as fetch:
            self.assertEqual(history._count(source, 0, 179.99, 10, 999999999), 14)  # split at the date line
        self.assertEqual(fetch.call_args.args[1]["returnCountOnly"], "true")
        with patch.object(history, "_json", return_value={}), self.assertRaises(OSError):
            history._count(source, 0, 0, 10, 999999999)

    def test_complete_pagination_finds_nearest_on_later_page(self):
        source = history.SOURCES[0]
        pages = [{"features": [feature(source, 1, 0, .01)], "exceededTransferLimit": True},
                 {"features": [feature(source, 2, 0, .001)]}]
        with patch.object(history, "_json", side_effect=pages) as fetch:
            records = history._records(source, 0, 0, 2, 999999999)
        self.assertEqual(len(records), 2)
        self.assertEqual(fetch.call_args_list[1].args[1]["resultOffset"], 1)
        self.assertNotIn("time", fetch.call_args.args[1])
        self.assertEqual(fetch.call_args.args[1]["where"], "1=1")

    def test_truncated_empty_or_repeated_page_fails(self):
        source = history.SOURCES[0]
        row = feature(source, 1, 0, .01)
        for pages in [[{"features": [], "exceededTransferLimit": True}],
                      [{"features": [row], "exceededTransferLimit": True}] * 2]:
            with patch.object(history, "_json", side_effect=pages), self.assertRaises(OSError):
                history._records(source, 0, 0, 2, 999999999)

    def test_failure_of_one_source_never_claims_partial_nearest(self):
        def records(source, *args):
            if source["id"] == "wfigs":
                raise OSError("offline")
            return [feature(source, 1, 0, .001)]
        with patch.object(history, "_records", side_effect=records):
            code, payload = dispatch("/api/v1/nearest-fire", "lat=0&lon=0")
        self.assertEqual(code, 502)
        self.assertIsNone(payload["data"])

    def test_invalid_missing_future_records_are_skipped(self):
        source = history.SOURCES[0]
        now = "2026-11-05T00:00:00+00:00"
        for row in [feature(source, 1, 0, 91), feature(source, 1, float("nan"), 0),
                    feature(source, 1, 0, 0, date=None), feature(source, 1, 0, 0, date=4102444800000)]:
            self.assertIsNone(history._record(source, row, 0, 0, now))

    def test_wraps_dateline_and_covers_poles(self):
        bounds = history.envelopes(0, 179.99, 10)
        self.assertEqual(len(bounds), 2)
        self.assertEqual(bounds[0][2], 180)
        self.assertEqual(bounds[1][0], -180)
        self.assertEqual(history.envelopes(89.99, 0, 10)[0][::2], (-180, 180))

    def test_cache_expires_and_exact_input_is_used(self):
        def records(source, *args):
            return [feature(source, 1, 0, .001)]
        with patch.object(history, "_records", side_effect=records) as fetch:
            first = history.nearest_fire(0, 0)
            first["fire"]["name"] = "mutated"
            self.assertNotEqual(history.nearest_fire(0, 0)["fire"]["name"], "mutated")
            self.assertEqual(fetch.call_count, 2)
            history._cache[(0, 0)] = (-999999999, history._cache[(0, 0)][1])
            history.nearest_fire(0, 0)
            self.assertEqual(fetch.call_count, 4)
            history.nearest_fire(0, .0001)
            self.assertEqual(fetch.call_count, 6)

    def test_invalid_api_inputs_stop_before_provider(self):
        for query in ["lat=nan&lon=0", "lat=91&lon=0", "lat=1", "lat=1&lon=2&days=30",
                      "lat=1&lon=2&lat=3", "lat=1&lon=2&address=x", "lat=&lon=2"]:
            with patch("api_v1.nearest_fire") as provider:
                self.assertEqual(dispatch("/api/v1/nearest-fire", query)[0], 400, query)
                provider.assert_not_called()

    def test_api_schema_and_address_resolution(self):
        from api_spec import openapi_document
        from jsonschema import Draft202012Validator
        result = {"status": "not_found", "time_window": "all_available_history", "fire": None,
                  "checked_at": "2026-11-05T00:00:00+00:00", "refresh_seconds": 300, "sources": [{}, {}]}
        address = {"status": "matched", "latitude": 29, "longitude": -98, "normalized_address": "100 TEST ST"}
        with patch("api_v1.nearest_fire", return_value=result) as provider, patch("api_v1.geocode_address", return_value=address):
            code, payload = dispatch("/api/v1/nearest-fire", "address=100+Test+St")
        self.assertEqual(code, 200)
        provider.assert_called_once_with(29, -98)
        self.assertEqual(payload["meta"]["request"]["time_window"], "all_available_history")
        Draft202012Validator(openapi_document()["components"]["schemas"]["NearestFire"]).validate(payload["data"])


if __name__ == "__main__":
    unittest.main()
