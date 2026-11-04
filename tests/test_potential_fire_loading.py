"""Cache validity, shared work, background progress and HTTP loading semantics."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import potential_fire as fire
from tests.test_api_v1 import http_request


def result():
    return {"status": "available", "peril": "potential_fire_intensity", "points": [{"severity": 10, "frequency_pct": 50}]}


class LoadingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cache = patch.object(fire, "CACHE", Path(temp.name))
        cache.start()
        self.addCleanup(cache.stop)
        self.inflight = patch.object(fire, "INFLIGHT", {})
        self.inflight.start()
        self.addCleanup(self.inflight.stop)

    def test_disk_cache_avoids_recomputation_and_consumer_mutations(self):
        with patch.object(fire, "_calculate", return_value=result()) as calculate:
            first = fire.potential_fire_at(30.08, -97.3)
            first["points"].clear()
            second = fire.potential_fire_at(30.08, -97.3)
        self.assertEqual(calculate.call_count, 1)
        self.assertEqual(len(second["points"]), 1)

    def test_cache_identity_changes_with_science_not_code_file(self):
        original = fire._result_path(30, -97)
        with patch.object(fire, "METHOD_VERSION", "changed-physics"):
            self.assertNotEqual(original, fire._result_path(30, -97))
        with patch.object(fire, "END_YEAR", 2021):
            self.assertNotEqual(original, fire._result_path(30, -97))
        fire._write_record(original, result())
        with patch.object(fire, "_calculate", side_effect=AssertionError("Cache miss")), patch.object(
                Path, "read_bytes", side_effect=AssertionError("Unrelated code file fingerprint")):
            self.assertEqual(fire.potential_fire_at(30, -97)["status"], "available")

    def test_same_location_requests_share_one_calculation(self):
        entered, release = threading.Event(), threading.Event()
        def calculate(*args):
            entered.set()
            self.assertTrue(release.wait(5))
            return result()
        with patch.object(fire, "_calculate", side_effect=calculate) as calculate_mock, ThreadPoolExecutor(2) as pool:
            first = pool.submit(fire.potential_fire_at, 30, -97)
            self.assertTrue(entered.wait(5))
            second = pool.submit(fire.potential_fire_at, 30, -97)
            release.set()
            self.assertEqual(first.result(5), second.result(5))
        self.assertEqual(calculate_mock.call_count, 1)
        self.assertFalse(fire.INFLIGHT)

    def test_different_locations_are_not_globally_serialized(self):
        barrier = threading.Barrier(2)
        def calculate(*args):
            barrier.wait(timeout=5)
            return result()
        with patch.object(fire, "_calculate", side_effect=calculate), ThreadPoolExecutor(2) as pool:
            jobs = [pool.submit(fire.potential_fire_at, lat, -97) for lat in (30, 31)]
            self.assertTrue(all(job.result(6)["status"] == "available" for job in jobs))

    def test_failure_releases_shared_work_and_retry_is_allowed(self):
        with patch.object(fire, "_calculate", side_effect=[OSError("source down"), result()]) as calculate:
            with self.assertRaises(OSError):
                fire.potential_fire_at(30, -97)
            self.assertFalse(fire.INFLIGHT)
            self.assertEqual(fire.potential_fire_at(30, -97)["status"], "available")
        self.assertEqual(calculate.call_count, 2)

    def test_year_cache_reuses_completed_work_but_tracks_inputs(self):
        landscape = {"fuel_model": 101, "canopy_cover_fraction": 0, "canopy_height_m": 0,
                     "slope_degrees": 0, "aspect_degrees": 0}
        source = {"sha256": "weather-1", "snow_source": {"sha256": "snow-1"}}
        with patch.object(fire, "weather_year", return_value=({}, source)), patch.object(
                fire, "annual_maximum", return_value={"year": 2020, "complete": True, "maximum_kw_m": 5}) as annual:
            fire._year_task(30, -97, 2020, landscape)
            fire._year_task(30, -97, 2020, {**landscape, "sources": ["different retrieval time"]})
            self.assertEqual(annual.call_count, 1)
            fire._year_task(30, -97, 2020, {**landscape, "fuel_model": 102})
            source["sha256"] = "weather-2"
            fire._year_task(30, -97, 2020, landscape)
            self.assertEqual(annual.call_count, 3)

    def test_bad_year_cache_integrity_is_not_silently_accepted(self):
        path = fire.CACHE / "bad.json"
        fire._write_record(path, {"value": 1})
        path.write_text(path.read_text().replace('value', 'other'), encoding="utf-8")
        with self.assertRaises(OSError):
            fire._read_record(path)

    def test_parallel_years_are_sorted_and_progress_is_monotone(self):
        def task(lat, lon, year, landscape):
            return {"year": year}, {"year": year}
        progress = []
        with ThreadPoolExecutor(2) as pool, patch.object(fire, "_year_pool", return_value=pool), patch.object(
                fire, "_year_task", side_effect=task), patch.object(fire, "START_YEAR", 2018), patch.object(fire, "END_YEAR", 2020):
            years, sources = fire._calculate_years(30, -97, {}, progress.append)
        self.assertEqual([y["year"] for y in years], [2018, 2019, 2020])
        self.assertEqual(sources, years)
        self.assertEqual([p["completed_years"] for p in progress], [1, 2, 3])

    def test_background_poll_returns_while_work_is_blocked_and_reuses_job(self):
        entered, release = threading.Event(), threading.Event()
        def calculate(*args):
            entered.set()
            self.assertTrue(release.wait(5))
            return result()
        with ThreadPoolExecutor(2) as runner, patch.object(fire, "JOB_RUNNER", runner), patch.object(
                fire, "JOBS", {}), patch.object(fire, "_calculate", side_effect=calculate) as calculate_mock:
            first, payload = fire.potential_fire_request(30, -97)
            self.assertEqual(first, 202)
            self.assertEqual(payload["status"], "processing")
            self.assertTrue(entered.wait(5))
            self.assertEqual(fire.potential_fire_request(30, -97)[0], 202)
            self.assertEqual(len(fire.JOBS), 1)
            release.set()
            fire.JOBS[(30, -97)]["future"].result(5)
            self.assertEqual(fire.potential_fire_request(30, -97)[0], 200)
            self.assertEqual(calculate_mock.call_count, 1)

    def test_new_http_route_rejects_bad_coordinates_before_starting_work(self):
        with patch.object(fire, "potential_fire_request") as request:
            for query in ("lat=nan&lon=-97", "lat=30&lon=-97&lat=31", "lat=&lon=-97", "lat=91&lon=0"):
                self.assertEqual(http_request("/api/potential-fire?" + query)[0], 400)
        request.assert_not_called()

    def test_new_http_route_preserves_202_progress(self):
        with patch.object(fire, "potential_fire_request", return_value=(202, {"status": "processing", "progress": {"completed_years": 5}})):
            code, headers, body = http_request("/api/potential-fire?lat=30&lon=-97")
        self.assertEqual(code, 202)
        self.assertIn(b'"completed_years": 5', body)
        self.assertIn("no-store", headers)


if __name__ == "__main__":
    unittest.main()
