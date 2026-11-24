import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import satellite_fire_history as sat
from area_hazard import area_disaster
from disaster_data import disaster_at
from hazard_engine import ModelUnavailable

LAT, LON = 29.4242, -98.4936


def build(folder, detections, key="viirs_snpp"):
    """detections: {year: [(lat, lon, frp, day_of_year), ...]} for that satellite's complete years."""
    folder = Path(folder) if key == "viirs_snpp" else Path(folder).parent / sat.MONITORS[key]["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"years": {}}
    first, last = sat.MONITORS[key]["years"]
    for year in range(first, last + 1):
        rows = detections.get(year, [])
        np.savez_compressed(Path(folder) / f"{year}.npz",
                            latitude=np.array([r[0] for r in rows], np.float32), longitude=np.array([r[1] for r in rows], np.float32),
                            frp=np.array([r[2] for r in rows], np.float32), day_of_year=np.array([r[3] for r in rows], np.int16))
        manifest["years"][str(year)] = {"url": f"https://example.test/{year}.csv", "sha256": "a" * 64, "fetched_at": "2026-11-22T00:00:00+00:00"}
    (Path(folder) / "manifest.json").write_text(json.dumps(manifest))


class SatelliteFireHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.snpp = Path(self.temp.name) / "firms_viirs_snpp"
        self.snpp.mkdir()
        patcher = patch.object(sat, "DATA", self.snpp)
        patcher.start()
        self.addCleanup(patcher.stop)
        sat.load_archive.cache_clear()
        self.addCleanup(sat.load_archive.cache_clear)

    def test_each_year_counts_once_at_its_strongest_detection_within_radius(self):
        near = 0.05  # about 5.6 km north
        build(self.snpp, {
            2013: [(LAT + near, LON, 10.0, 32), (LAT + near, LON, 40.0, 33), (LAT + near, LON, 2.0, 200)],  # max 40
            2014: [(LAT + near, LON, 10.0, 60)],                                                       # max 10
            2015: [(LAT + 1.0, LON, 999.0, 10)],                                                       # ~111 km away: outside 25 km
            2020: [(LAT, LON + 0.1, 40.0, 366)],                                                       # leap-year day 366
        })
        curve = sat.satellite_fire_curve(LAT, LON, 25)
        self.assertEqual(curve["status"], "available")
        self.assertEqual(curve["unit"], "MW")
        levels = {p["severity"]: p for p in curve["points"]}
        self.assertEqual(sorted(levels), [10.0, 40.0], "only observed annual maxima become levels")
        self.assertEqual((levels[40.0]["hit_years"], levels[40.0]["exposure_years"]), (2, 13))
        self.assertAlmostEqual(levels[10.0]["frequency_pct"], 300 / 13, "three years reach 10 MW; years count once")
        years = {row["year"]: row for row in curve["annual_detections"]}
        self.assertEqual((years[2013]["detections"], years[2013]["max_frp_mw"], years[2013]["max_date"]), (3, 40.0, "2013-02-02"))
        self.assertEqual(years[2020]["max_date"], "2020-12-31")
        self.assertEqual((years[2015]["detections"], years[2015]["max_frp_mw"]), (0, None), "outside the radius is not counted")
        self.assertEqual(len(curve["provenance"]["sources"]), 13)
        json.dumps(curve, allow_nan=False)
        small = sat.satellite_fire_curve(LAT, LON, 5)
        self.assertEqual(small["status"], "unavailable", "5.6 km detections are outside a 5 km radius")
        self.assertIn("Zero observed years does not mean zero future risk", small["reason"])

    def test_radius_coverage_and_missing_archive(self):
        for bad in (0.2, 0, -5, 100.5, float("nan"), True, "abc"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                sat.valid_radius(bad)
        self.assertEqual((sat.valid_radius("7"), sat.valid_radius(12.5), sat.valid_radius(0.5), sat.valid_radius(100)), (7, 12.5, 0.5, 100))
        with self.assertRaises(ModelUnavailable):
            sat.satellite_fire_curve(51.5, -0.1, 25)  # London: outside the US archive
        with self.assertRaisesRegex(ModelUnavailable, "prepare_satellite_fire_data.py"):
            sat.satellite_fire_curve(LAT, LON, 25)  # nothing built in the temp folder

    def test_each_satellite_is_its_own_monitor_with_zero_years_in_the_denominator(self):
        near = 0.05
        build(self.snpp, {2013: [(LAT + near, LON, 60.0, 10)], 2014: [(LAT + near, LON, 5.0, 10)]})
        build(self.snpp, {2001: [(LAT + near, LON, 80.0, 5)], 2002: [(LAT + near, LON, 9.0, 5)]}, key="modis_terra")
        build(self.snpp, {2019: [(LAT + near, LON, 70.0, 5)]}, key="viirs_noaa20")
        result = sat.satellite_monitor_records(LAT, LON, 25)
        rows = {row["id"]: row for row in result["stations"]}
        self.assertEqual(list(rows), ["viirs_snpp", "viirs_noaa20", "modis_terra", "modis_aqua"], "fixed satellite order")
        self.assertEqual((rows["viirs_snpp"]["status"], rows["viirs_snpp"]["counted_years"], rows["viirs_snpp"]["years_with_detection"]), ("available", 13, 2))
        self.assertEqual(rows["viirs_snpp"]["annual_maxima"].count(0.0), 11, "years without detections stay in the denominator as 0")
        self.assertEqual({p["severity"]: p["hit_years"] for p in rows["viirs_snpp"]["points"]}, {5.0: 2, 60.0: 1})
        self.assertEqual((rows["viirs_noaa20"]["status"], rows["viirs_noaa20"]["counted_years"]), ("insufficient_years", 7), "7 years is a short record")
        self.assertEqual((rows["modis_terra"]["counted_years"], rows["modis_terra"]["highest"]), (25, 80.0))
        self.assertEqual(rows["modis_aqua"]["status"], "source_unavailable", "an archive that is not built is reported, not invented")
        self.assertIn("--satellites modis_aqua", rows["modis_aqua"]["reason"])
        self.assertEqual((result["kind"], result["minimum_years"], rows["viirs_snpp"]["distance_km"]), ("satellite", 10, None))
        json.dumps(result, allow_nan=False)
        from tests.test_api_v1 import http_request
        status, _, body = http_request(f"/api/satellite-monitors?lat={LAT}&lon={LON}&radius_km=12.5")
        self.assertEqual((status, json.loads(body)["radius_km"]), (200, 12.5))
        self.assertEqual(http_request(f"/api/satellite-monitors?lat={LAT}&lon={LON}&radius_km=200")[0], 400)
        self.assertEqual(json.loads(http_request("/api/satellite-monitors?lat=51.5&lon=-0.1")[2])["status"], "unavailable")

    def test_viewer_routes_point_curve_and_point_only_area(self):
        build(self.snpp, {2013: [(LAT, LON, 5.0, 1)]})
        result = disaster_at("satellite_fire", LAT, LON, radius_km=10)
        self.assertEqual((result["peril"], result["radius_km"], result["status"]), ("satellite_fire", 10, "available"))
        area = area_disaster(29, -99, 30, -98, "satellite_fire")
        self.assertEqual(area["status"], "unavailable")
        self.assertIn("Point mode", area["reason"])
        from tests.test_api_v1 import http_request
        status, _, body = http_request(f"/api/disaster?peril=satellite_fire&lat={LAT}&lon={LON}&radius_km=12.5")
        self.assertEqual((status, json.loads(body)["radius_km"]), (200, 12.5), "any radius in range is accepted")
        self.assertIn("within 12.5 km", json.loads(body)["metric"])
        for query in (f"peril=satellite_fire&lat={LAT}&lon={LON}&radius_km=101", f"peril=satellite_fire&lat={LAT}&lon={LON}&radius_km=0.1", f"peril=satellite_fire&lat={LAT}&lon={LON}&radius_km=nan", f"peril=flood&lat={LAT}&lon={LON}&radius_km=25"):
            self.assertEqual(http_request("/api/disaster?" + query)[0], 400, query)
        empty = disaster_at("satellite_fire", 51.5, -0.1)
        self.assertEqual(empty["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
