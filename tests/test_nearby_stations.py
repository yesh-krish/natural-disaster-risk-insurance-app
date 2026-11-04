import gzip
import io
import json
import math
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from jsonschema import Draft202012Validator
from api_spec import openapi_document
from api_v1 import dispatch
import monitoring_stations as stations


def station(identifier="KAAA", lat=0, lon=0, **fields):
    return dict(icaoId=identifier, site=identifier + " Airport", lat=lat, lon=lon, siteType=["METAR"], **fields)


class NearbyStationTests(unittest.TestCase):
    def search(self, rows, n=5, lat=0, lon=0, reports=()):
        with patch.object(stations, "_awc_resource", side_effect=[(rows, "2026-11-03T00:00:00+00:00"), (list(reports), "now")]):
            return stations.nearest_stations(lat, lon, n)

    def test_sort_deduplicate_filter_and_partial(self):
        result = self.search([station("KFAR", lon=1), station("KBBB", lon=.1), station("KAAA", lon=-.1),
                              station("KAAA", lon=-.1), station("KOUT", lon=2), station("KBAD", lat=None),
                              station("KNAN", lat=math.nan), station("KBOOL", lat=True),
                              {**station("KTAF"), "siteType": ["TAF"]}])
        self.assertEqual([s["id"] for s in result["stations"]], ["KAAA", "KBBB", "KFAR"])
        self.assertEqual(result["returned_count"], 3)
        self.assertTrue(result["fewer_than_requested"])
        self.assertEqual(result["skipped_catalog_rows"], 3)
        self.assertEqual(len(self.search([station("KBBB", lon=.1), station("KAAA")], n=1)["stations"]), 1)
        for row in result["stations"]:
            self.assertEqual(row["observation"]["status"], "missing")
            self.assertIn("id=" + row["id"], row["url"])
            self.assertTrue(row["measured_variables"])

    def test_distance_dateline_poles_and_exact_radius(self):
        result = self.search([station("KNEAR", lon=-179.9), station("KFAR", lon=178)], lat=0, lon=179.9)
        self.assertEqual([s["id"] for s in result["stations"]], ["KNEAR"])
        self.assertAlmostEqual(result["stations"][0]["distance_km"], 22.239, places=2)
        result = self.search([station("KPOLE", lat=89.9, lon=180)], lat=89.9, lon=0)
        self.assertAlmostEqual(result["stations"][0]["distance_km"], 22.239, places=2)
        boundary = math.degrees(200 / stations.EARTH_KM)
        result = self.search([station("KEDGE", lon=boundary), station("KOVER", lon=boundary + .000001)])
        self.assertEqual([s["id"] for s in result["stations"]], ["KEDGE"])

    def test_no_results_is_distinct_from_empty_or_failed_catalog(self):
        result = self.search([station(lat=50)])
        self.assertEqual(result["status"], "not_found")
        self.assertTrue(result["fewer_than_requested"])
        with patch.object(stations, "_awc_resource", return_value=([], "now")):
            with self.assertRaises(OSError):
                stations.nearest_stations(0, 0)
        with patch.object(stations, "_awc_resource", side_effect=OSError("offline")):
            status, result = dispatch("/api/v1/monitoring-stations", "lat=0&lon=0&n=5")
        self.assertEqual(status, 502)
        self.assertEqual(result["error"]["code"], "provider_unavailable")

    def test_observations_latest_stale_units_missing_fields_and_failure(self):
        rows = [station("KAAA"), station("KBBB", lon=.1), station("KCCC", lon=.2), station("KDDD", lon=.3)]
        reports = [dict(icaoId="KAAA", obsTime=9900, temp=0, wspd=0, wdir="VRB", visib="10+", altim=1012.3),
                   dict(icaoId="KAAA", obsTime=9800, temp=20), dict(icaoId="KBBB", obsTime=1000, temp=12),
                   dict(icaoId="KCCC", obsTime=9999, temp=None), dict(icaoId="KDDD", obsTime=20000, temp=15)]
        with patch.object(stations.time, "time", return_value=10000):
            result = self.search(rows, reports=reports)
        observations = [r["observation"] for r in result["stations"]]
        self.assertEqual([o["status"] for o in observations], ["available", "stale", "missing", "missing"])
        self.assertEqual(observations[0]["values"][0], {"variable": "Air temperature", "value": 0, "unit": "°C"})
        self.assertIn({"variable": "Wind direction", "value": "Variable", "unit": ""}, observations[0]["values"])
        self.assertIn({"variable": "Visibility", "value": "10+", "unit": "statute mi"}, observations[0]["values"])
        self.assertIn("+00:00", observations[0]["observed_at"])
        with patch.object(stations, "_awc_resource", side_effect=[(rows, "now"), OSError("offline")]):
            result = stations.nearest_stations(0, 0)
        self.assertEqual(result["status"], "available")
        self.assertTrue(all(r["observation"]["status"] == "unavailable" for r in result["stations"]))
        self.assertTrue(all(r["url"] for r in result["stations"]))

    def test_validation_before_geocoder_or_provider(self):
        invalid = ["0", "-1", "1.5", "1e1", "51", "NaN", "true", "", "3&n=4", "9" * 100]
        with patch("api_v1.geocode_address") as geocoder, patch("api_v1.nearest_stations") as provider:
            for n in invalid:
                self.assertEqual(dispatch("/api/v1/monitoring-stations", "address=test&n=" + n)[0], 400, n)
            for query in ["lat=nan&lon=0", "lat=91&lon=0", "lat=0", "n=1", "lat=0&lon=0&network=other",
                          "lat=0&lon=0&peril=flood&n=2", "address=test&lat=0&lon=0"]:
                self.assertEqual(dispatch("/api/v1/monitoring-stations", query)[0], 400, query)
            geocoder.assert_not_called()
            provider.assert_not_called()
        for value in [True, 1.5, None, 0, 51]:
            with self.assertRaises(ValueError):
                stations.station_count(value)

    def test_address_ambiguity_missing_coordinates_and_success_contract(self):
        lookup = {"status": "matched", "latitude": 29.4, "longitude": -98.5,
                  "matched_address": "100 DOLOROSA, SAN ANTONIO, TX", "normalized_address": "100 Dolorosa St, San Antonio, TX",
                  "source_name": "Census", "source_url": "https://geocoding.geo.census.gov/", "approximate": True, "note": "Approximate"}
        for outcome in ["ambiguous", "no_match"]:
            with patch("api_v1.geocode_address", return_value={**lookup, "status": outcome, "message": "Refine address"}), patch("api_v1.nearest_stations") as provider:
                self.assertEqual(dispatch("/api/v1/monitoring-stations", "address=test&n=2")[0], 422)
                provider.assert_not_called()
        with patch("api_v1.geocode_address", return_value={"status": "matched"}), patch("api_v1.nearest_stations") as provider:
            self.assertEqual(dispatch("/api/v1/monitoring-stations", "address=test")[0], 502)
            provider.assert_not_called()
        with patch("api_v1.geocode_address", return_value=lookup), patch.object(stations, "_awc_resource", side_effect=[([station(lat=29.41, lon=-98.5)], "2026-11-03T00:00:00+00:00"), ([], "now")]):
            status, payload = dispatch("/api/v1/monitoring-stations", urlencode({"address": lookup["normalized_address"], "n": 2}))
        self.assertEqual(status, 200)
        self.assertEqual(payload["meta"]["location"]["matched_address"], lookup["matched_address"])
        spec = openapi_document()
        schema = spec["paths"]["/api/v1/monitoring-stations"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        Draft202012Validator({**schema, "components": spec["components"]}).validate(payload)
        json.dumps(payload, allow_nan=False)

    def test_cache_gzip_coalescing_and_error_backoff(self):
        url = stations.AWC_CATALOG
        try:
            stations._awc_cache.clear()
            stations._awc_next_request = 0
            raw = gzip.compress(json.dumps([station()]).encode())
            with patch.object(stations.urllib.request, "urlopen", return_value=io.BytesIO(raw)) as download:
                first = stations._awc_resource(url, 86400, True)
                second = stations._awc_resource(url, 86400, True)
            self.assertEqual(first, second)
            download.assert_called_once()
            self.assertIn("NDApp", download.call_args.args[0].headers["User-agent"])
            stations._awc_cache.clear()
            stations._awc_next_request = 0
            with patch.object(stations.urllib.request, "urlopen", side_effect=OSError("offline")) as download:
                for _ in range(2):
                    with self.assertRaises(OSError):
                        stations._awc_resource(url, 86400, True)
                download.assert_called_once()
        finally:
            stations._awc_cache.clear()
            stations._awc_next_request = 0


if __name__ == "__main__":
    unittest.main()
