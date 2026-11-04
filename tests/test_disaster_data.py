import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin

from disaster_data import cyclone, disaster_at, distance_bearing, wildfire


class DisasterViewerTests(unittest.TestCase):
    def test_rainfall_uses_24_hour_ams_depth_and_reciprocal_aep(self):
        source = """Point precipitation frequency estimates (millimeters)
NOAA Atlas 14 Volume 11 Version 2
Time series type: Annual maximum
by duration for AEP:, '1/2,'1/100
12-hr:, 30,90
24-hr:, 40,120
"""
        with patch("disaster_data.government_text", return_value=source) as fetch:
            result = disaster_at("rainfall", 29.4, -98.5)
        self.assertEqual(result["unit"], "mm")
        self.assertEqual([(p["frequency_pct"], p["severity"]) for p in result["points"]], [(1, 120), (50, 40)])
        self.assertIn("series=ams", fetch.call_args.args[0])
        self.assertIn("units=metric", fetch.call_args.args[0])

    def test_rainfall_incompatible_response_is_unavailable(self):
        with patch("disaster_data.government_text", return_value="Time series type: Partial duration"):
            result = disaster_at("rainfall", 29.4, -98.5)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["points"], [])

    def test_cyclone_counts_years_not_repeated_track_records(self):
        radii = (100, 0, 0, 0, 50, 0, 0, 0, 20, 0, 0, 0)
        records = [("AL012004", 2004, 25, -80, 70, radii)] * 8
        records.append(("AL022004", 2004, 25, -80, 70, radii))
        records.append(("AL012005", 2005, 25, -80, 40, radii))
        with patch("disaster_data.cyclone_catalog", return_value=(records, 2005, ["source"])):
            result = cyclone(25.1, -79.9)
        self.assertEqual([p["frequency_pct"] for p in result["points"]], [100, 50, 50])
        self.assertEqual(result["unit"], "kn")
        self.assertIn("Observed", result["frequency_label"])
        self.assertEqual(result["peril"], "cyclone", "Wind retains the existing machine identifier")
        self.assertIn("tropical cyclones only", " ".join(result["notes"]))

    def test_cyclone_radii_use_site_quadrant_and_nautical_miles(self):
        distance, bearing = distance_bearing(0, 0, 1, 0)
        self.assertAlmostEqual(distance, 60.04, places=1)
        self.assertAlmostEqual(bearing, 0)
        records = [("AL012004", 2004, 25, -80, 70, (100, 0, 0, 0) * 3)]
        with patch("disaster_data.cyclone_catalog", return_value=(records, 2004, ["source"])):
            self.assertTrue(all(p["frequency_pct"] == 0 for p in cyclone(24.9, -80.1)["points"]))

    def test_fire_uses_probability_fractions_and_keeps_real_zero(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            root = Path(directory)
            folder = root / "data" / "usfs"
            folder.mkdir(parents=True)
            for name, values in {"BP": [[.02, 0]], "FLP5": [[.3, 0]], "FLP6": [[.1, 0]]}.items():
                with rasterio.open(folder / f"CONUS_{name}.tif", "w", driver="GTiff", height=1,
                                   width=2, count=1, dtype="float32", crs="EPSG:4326",
                                   transform=from_origin(-100, 30, 1, 1), nodata=-9999) as raster:
                    raster.write(np.array(values, dtype="float32"), 1)
            with patch("disaster_data.ROOT", root), patch("disaster_data.fire_class", return_value="Moderate"):
                result = wildfire(29.5, -99.5)
                zero = wildfire(29.5, -98.5)
            self.assertAlmostEqual(result["points"][0]["frequency_pct"], .8, places=6)
            self.assertAlmostEqual(result["points"][1]["frequency_pct"], .2, places=6)
            self.assertAlmostEqual(result["points"][0]["severity"], 2.4384)
            self.assertTrue(all(p["frequency_pct"] == 0 for p in zero["points"]))

    def test_flood_converts_feet_to_meters_and_preserves_missing_points(self):
        sample = {"tile": "n29w99", "events": {"1": {"status": "sampled", "depth_ft": 10},
                                                    "20": {"status": "no_mapped_depth", "depth_ft": None}}}
        with patch("server.lookup", return_value=sample):
            result = disaster_at("flood", 29.4, -98.5)
        self.assertEqual(result["points"][0]["severity"], 3.048)
        self.assertIsNone(result["points"][1]["severity"])

    def test_invalid_coordinates_and_unknown_disasters_fail(self):
        for coordinate in [(math.nan, 0), (91, 0), (0, 181)]:
            with self.assertRaises(ValueError):
                disaster_at("rainfall", *coordinate)
        with self.assertRaises(ValueError):
            disaster_at("unknown", 29, -98)

    def test_outside_supported_region_has_no_invented_points(self):
        result = disaster_at("earthquake", 0, 0)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["points"], [])


if __name__ == "__main__":
    unittest.main()
