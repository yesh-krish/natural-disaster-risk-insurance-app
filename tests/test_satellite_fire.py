"""NASA FIRMS satellite fire detections: parsing, filtering, coverage, failures and API contract."""
from datetime import datetime, timedelta, timezone
import io
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

import satellite_fire as sat
from api_spec import openapi_document
from api_v1 import dispatch

LAT, LON = 29.4242, -98.4933
HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight\n"


def row(lat, lon, hours_ago=2, confidence="n", frp="5.21", daynight="D"):
    at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return f"{lat},{lon},330.1,0.39,0.36,{at:%Y-%m-%d},{at:%H%M},N21,{confidence},2.0NRT,290.2,{frp},{daynight}\n"


def files(n20="", n21=""):
    texts = {"NOAA-20": HEADER + n20, "NOAA-21": HEADER + n21}
    return lambda url: texts["NOAA-20" if "noaa-20" in url else "NOAA-21"]


class SatelliteFireTests(unittest.TestCase):
    def setUp(self):
        sat._cache.clear()

    def contract(self, payload, code=200):
        spec = openapi_document()
        schema = spec["paths"]["/api/v1/satellite-fires"]["get"]["responses"][str(code)]["content"]["application/json"]["schema"]
        Draft202012Validator({**schema, "components": spec["components"]}, format_checker=FormatChecker()).validate(payload)

    def test_nearby_detections_are_filtered_sorted_and_labelled(self):
        near, mid, far = row(29.43, -98.49, 1, "h", "12.5"), row(29.55, -98.49, 20, "l", ""), row(31.0, -98.49, 3)
        with patch.object(sat, "_download", side_effect=files(n20=far + mid, n21=near)):
            result = sat.satellite_fire_detections(LAT, LON, 25)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["detection_count"], 2, "the 175 km detection is outside 25 km")
        first, second = result["detections"]
        self.assertEqual((first["satellite"], first["confidence"], first["frp_mw"]), ("NOAA-21", "high", 12.5))
        self.assertLess(first["distance_km"], second["distance_km"])
        self.assertIsNone(second["frp_mw"], "missing FRP stays null, never zero")
        self.assertEqual(second["confidence"], "low")
        self.assertAlmostEqual(first["age_hours"], 1, delta=0.1)
        self.assertEqual(result["newest_age_hours"], first["age_hours"])
        self.assertEqual([s["status"] for s in result["sources"]], ["ok", "ok"])
        self.assertTrue(any("not a confirmed wildfire" in note for note in result["notes"]))

    def test_frp_summary_is_an_exceedance_share_of_detections(self):
        rows = "".join(row(29.43, -98.49, 1, frp=value) for value in ("2.0", "8.0", "8.0", "40.5", ""))
        with patch.object(sat, "_download", side_effect=files(n21=rows)):
            result = sat.satellite_fire_detections(LAT, LON, 25)
        summary = result["frp_summary"]
        self.assertEqual((summary["unit"], summary["detections_with_frp"], summary["detections_without_frp"]), ("MW", 4, 1))
        self.assertEqual((summary["minimum_mw"], summary["median_mw"], summary["maximum_mw"]), (2.0, 8.0, 40.5))
        self.assertEqual([(p["frp_mw"], p["detections_at_or_above"], p["share_pct"]) for p in summary["exceedance"]],
                         [(40.5, 1, 25.0), (8.0, 3, 75.0), (2.0, 4, 100.0)], "missing FRP is excluded, ties count once")
        self.assertIn("not an annual probability", summary["basis"])

    def test_frp_curve_is_thinned_but_keeps_both_ends(self):
        summary = sat.frp_summary([{"frp_mw": float(v)} for v in range(1, 301)])
        points = summary["exceedance"]
        self.assertEqual(len(points), sat.FRP_CURVE_POINTS)
        self.assertEqual((points[0]["frp_mw"], points[0]["share_pct"]), (300.0, round(100 / 300, 4)))
        self.assertEqual((points[-1]["frp_mw"], points[-1]["share_pct"]), (1.0, 100.0))
        empty = sat.frp_summary([{"frp_mw": None}])
        self.assertEqual((empty["detections_with_frp"], empty["median_mw"], empty["exceedance"]), (0, None, []))

    def test_no_detection_is_not_no_fire(self):
        with patch.object(sat, "_download", side_effect=files(n20=row(35, -100))):
            result = sat.satellite_fire_detections(LAT, LON, 10)
        self.assertEqual((result["status"], result["detection_count"]), ("not_found", 0))
        self.assertIn("does not mean there is no fire", result["reason"])

    def test_one_failed_satellite_is_partial_and_all_failed_is_an_error(self):
        def half(url):
            if "noaa-20" in url:
                raise OSError("down")
            return HEADER + row(29.43, -98.49)
        with patch.object(sat, "_download", side_effect=half):
            result = sat.satellite_fire_detections(LAT, LON)
        self.assertEqual(result["detection_count"], 1)
        self.assertIn("Partial: NOAA-20", result["reason"])
        self.assertEqual({s["satellite"]: s["status"] for s in result["sources"]}, {"NOAA-20": "failed", "NOAA-21": "ok"})
        sat._cache.clear()
        with patch.object(sat, "_download", side_effect=OSError("down")):
            code, payload = dispatch("/api/v1/satellite-fires", f"lat={LAT}&lon={LON}")
        self.assertEqual(code, 502)
        self.contract(payload, 502)

    def test_malformed_files_fail_instead_of_becoming_empty(self):
        future = datetime.now(timezone.utc) + timedelta(hours=3)
        bad_rows = ["29.4,-98.4,1,1,1,2026-13-40,9999,N,n,v,1,1,D\n",
                    f"29.4,-98.4,1,1,1,{future:%Y-%m-%d},{future:%H%M},N,n,v,1,1,D\n",
                    row(29.4, -98.4, confidence="maybe"), row("nan", -98.4), row(95, -98.4)]
        for bad in bad_rows:
            with self.subTest(bad=bad):
                with self.assertRaises(OSError if "95" not in bad else ValueError):
                    sat._parse(HEADER + bad, "NOAA-20", datetime.now(timezone.utc))
        with self.assertRaises(OSError):
            sat._parse("latitude,longitude\n1,2\n", "NOAA-20", datetime.now(timezone.utc))

    def test_outside_coverage_is_unavailable_without_download(self):
        with patch.object(sat, "_download") as download:
            for lat, lon, radius in ((61.2, -149.9, 25), (51.5, -0.1, 25), (24.3, -81.5, 50)):
                with self.subTest(lat=lat):
                    result = sat.satellite_fire_detections(lat, lon, radius)
                    self.assertEqual(result["status"], "unavailable")
            download.assert_not_called()
        self.assertTrue(sat.in_coverage(21.3, -157.9, 10), "Hawaii is covered")

    def test_files_are_cached_for_ten_minutes(self):
        with patch.object(sat, "_download", side_effect=files(n21=row(29.43, -98.49))) as download:
            sat.satellite_fire_detections(LAT, LON)
            sat.satellite_fire_detections(LAT + 0.1, LON)
        self.assertEqual(download.call_count, 2, "one download per satellite, reused for the second point")

    def test_api_validates_before_io_and_matches_contract(self):
        with patch.object(sat, "_download") as download:
            for query in ("lat=29&lon=-98&radius_km=0", "lat=29&lon=-98&radius_km=101", "lat=29&lon=-98&radius_km=nan",
                          "lat=29", "lat=29&lon=-98&url=x"):
                with self.subTest(query=query):
                    code, payload = dispatch("/api/v1/satellite-fires", query)
                    self.assertEqual(code, 400)
                    self.contract(payload, 400)
            download.assert_not_called()
        with patch.object(sat, "_download", side_effect=files(n20=row(29.43, -98.49))):
            code, payload = dispatch("/api/v1/satellite-fires", f"lat={LAT}&lon={LON}&radius_km=50")
        self.assertEqual(code, 200)
        self.assertEqual(payload["meta"]["request"]["radius_km"], 50)
        self.contract(payload)

    def test_window_selects_the_matching_nasa_file_and_is_validated(self):
        requested = []

        def download(url):
            requested.append(url)
            return HEADER + row(29.43, -98.49, 100)
        with patch.object(sat, "_download", side_effect=download):
            week = sat.satellite_fire_detections(LAT, LON, 25, "7d")
            day = sat.satellite_fire_detections(LAT, LON, 25, "24h")
        self.assertTrue(all(url.endswith("_7d.csv") for url in requested[:2]))
        self.assertTrue(all(url.endswith("_24h.csv") for url in requested[2:]))
        self.assertEqual(len(requested), 4, "each window is cached separately")
        self.assertEqual((week["window"], day["window"]), ("7d", "24h"))
        self.assertIn("7–8 days", week["window_description"])
        self.assertTrue(any("not separate fires" in note for note in week["notes"]))
        self.assertFalse(any("not separate fires" in note for note in day["notes"]))
        with self.assertRaises(ValueError):
            sat.satellite_fire_detections(LAT, LON, 25, "30d")
        with patch.object(sat, "_download") as nothing:
            for query in ("window=30d", "window=", "window=7D"):
                with self.subTest(query=query):
                    self.assertEqual(dispatch("/api/v1/satellite-fires", f"lat={LAT}&lon={LON}&{query}")[0], 400)
            nothing.assert_not_called()
        sat._cache.clear()
        with patch.object(sat, "_download", side_effect=download):
            code, payload = dispatch("/api/v1/satellite-fires", f"lat={LAT}&lon={LON}&window=48h")
        self.assertEqual((code, payload["data"]["window"], payload["meta"]["request"]["window"]), (200, "48h", "48h"))
        self.contract(payload)

    def test_redirects_off_nasa_are_refused(self):
        handler = sat._NasaOnly()
        with self.assertRaises(OSError):
            handler.redirect_request(None, io.BytesIO(), 302, "", {}, "https://evil.example/fires.csv")
        with self.assertRaises(OSError):
            handler.redirect_request(None, io.BytesIO(), 302, "", {}, f"http://{sat.HOST}/x.csv")


if __name__ == "__main__":
    unittest.main()
