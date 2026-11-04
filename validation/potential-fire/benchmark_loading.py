"""Compare sequential and parallel evaluation using the prepared pilot's source cache.

Run with .venv/Scripts/python.exe validation/potential-fire/benchmark_loading.py.
Sources should already be prepared; this does not clear any caches.
"""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import potential_fire as fire


def main():
    reference = json.loads((fire.CACHE / "bastrop-baseline.json").read_text(encoding="utf-8"))
    lat, lon = 30.08, -97.3
    start = time.perf_counter()
    sequential = []
    for year in range(fire.START_YEAR, fire.END_YEAR + 1):
        doc, _ = fire.weather_year(lat, lon, year)
        sequential.append(fire.annual_maximum(doc, year, reference["landscape"]))
    sequential_seconds = time.perf_counter() - start
    print(f"Sequential baseline: {sequential_seconds:.3f} s", flush=True)
    start = time.perf_counter()
    parallel = fire.potential_fire_at(lat, lon, lambda p: print(
        f"{p['phase']}: {p['completed_years']}/{p['total_years']}", flush=True))
    parallel_seconds = time.perf_counter() - start
    assert parallel["annual_maxima"] == sequential == reference["annual_maxima"]
    assert parallel["points"] == reference["points"]
    start = time.perf_counter()
    cached = fire.potential_fire_at(lat, lon)
    cached_seconds = time.perf_counter() - start
    assert cached["annual_maxima"] == sequential
    report = {"sequential_cached_weather_seconds": sequential_seconds,
              "parallel_first_result_seconds": parallel_seconds,
              "completed_cache_seconds": cached_seconds, "workers": fire.YEAR_WORKERS,
              "annual_maxima_identical": True, "curve_identical": True,
              "scientific_identity": fire._identity(),
              "note": "Weather was already downloaded. First-result timing uses any existing yearly/result caches; none are deleted."}
    target = Path(__file__).with_name("loading-benchmark.json")
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
