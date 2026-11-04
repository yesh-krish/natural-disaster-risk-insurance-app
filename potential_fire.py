"""Historical-weather potential surface-fire intensity, never wildfire arrival AEP.

Fixed LANDFIRE 2023 landscape; ERA5 hourly weather; WRF-SFIRE time-lag dead
moisture equations; pinned, unmodified Pyretechnics Rothermel/Byram engine.
See docs/potential-fire-intensity.md for the scenario and validation limits.
"""
import calendar
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import importlib
import io
import json
import math
import multiprocessing
import os
from pathlib import Path
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from hazard_engine import ModelUnavailable, coordinates

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "potential_fire"
START_YEAR, END_YEAR, MIN_YEARS = 1991, 2020, 20
MODEL_VERSION = "2026.10.20"
MODEL_SHA256 = "1d79429c87325f45d0c56b2c0a82ae1930ffb3ff93db3cb52a340e1d59fb9c1f"
MODEL_URL = ("https://files.pythonhosted.org/packages/19/49/06e21073207369b06b5497eccb37ff775b05370283016606690eac95bee8/"
             "pyretechnics-2026.10.20.tar.gz")
LAND = "https://edcintl.cr.usgs.gov/arcgis/rest/services/"
WEATHER = "https://archive-api.open-meteo.com/v1/archive"
VARIABLES = ("temperature_2m", "relative_humidity_2m", "rain", "wind_speed_10m",
             "wind_direction_10m", "snow_depth")
LOCK = threading.Lock()
POOL_LOCK = threading.Lock()
INFLIGHT = {}
JOBS = {}
JOBS_LOCK = threading.Lock()
JOB_RUNNER = ThreadPoolExecutor(max_workers=2, thread_name_prefix="potential-fire")
DOWNLOAD_GATE = threading.BoundedSemaphore(2)
YEAR_WORKERS = min(4, max(1, os.cpu_count() or 1))
# Bump when equations, input versions, calendar, moisture or scenario change.
# Performance/UI edits deliberately do not invalidate the scientific results.
METHOD_VERSION = "potential-surface-v1"
REFERENCES = [
    {"title": "ERA5 historical weather / Open-Meteo", "url": "https://open-meteo.com/en/docs/historical-weather-api"},
    {"title": "LANDFIRE fuel models", "url": "https://landfire.gov/fuel/fbfm40"},
    {"title": "Pyretechnics surface-fire equations", "url": "https://pyregence.github.io/pyretechnics/"},
    {"title": "WRF-SFIRE moisture methods (Mandel et al. 2014)", "url": "https://doi.org/10.5194/nhess-14-2829-2014"},
]
INTERPRETATION = ("Frequency of years with weather capable of producing this modeled surface-fire intensity "
                  "on the fixed landscape. This is not the annual probability of an actual wildfire reaching the site.")


def _download(url, limit=4_000_000):
    request = urllib.request.Request(url, headers={"User-Agent": "NDApp potential-fire research/1"})
    with DOWNLOAD_GATE:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(limit + 1)
    if len(raw) > limit:
        raise OSError("Potential-fire source exceeds response size limit")
    return raw


def _json(url):
    """Persistent content-addressed source snapshots; failed responses never cached."""
    path = CACHE / "requests" / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    with LOCK:
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            raw = saved["body"].encode("utf-8")
            if saved["url"] != url or hashlib.sha256(raw).hexdigest() != saved["sha256"]:
                raise OSError("Potential-fire source cache failed integrity check")
            return json.loads(raw), {k: saved[k] for k in ("url", "sha256", "retrieved_at")}
    raw = _download(url)
    doc = json.loads(raw)
    if doc.get("error"):
        raise OSError("Potential-fire source returned an error: " + str(doc.get("reason", doc["error"])))
    # Hash the exact UTF-8 source body retained in the cache.
    saved = {"url": url, "sha256": hashlib.sha256(raw).hexdigest(), "body": raw.decode("utf-8"),
             "retrieved_at": datetime.now(timezone.utc).isoformat()}
    with LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        temp.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
    return doc, {k: saved[k] for k in ("url", "sha256", "retrieved_at")}


@lru_cache(maxsize=1)
def engine():
    """Use upstream's pure-Python mode on Windows, avoiding a C compiler install.

    Only five required modules and the licence are unpacked, with archive SHA256
    pinned to PyPI. They remain unmodified under ignored data/, not app source.
    """
    try:
        import cython  # noqa: F401
    except ImportError:
        raise ModelUnavailable("Install requirements-fire.txt in the app environment to run potential fire intensity.") from None
    model_root = CACHE / "model" / MODEL_VERSION
    marker = model_root / "verified.json"
    files = ("__init__.py", "py_types.py", "conversion.py", "vector_utils.py", "fuel_models.py", "surface_fire.py")
    with LOCK:
        if not marker.exists():
            raw = _download(MODEL_URL)
            if hashlib.sha256(raw).hexdigest() != MODEL_SHA256:
                raise OSError("Published fire-model archive failed SHA256 verification")
            prefix = "pyretechnics-" + MODEL_VERSION + "/"
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
                contents = {"pyretechnics/" + name: archive.extractfile(prefix + "src/pyretechnics/" + name).read()
                            for name in files}
                license_names = [m.name for m in archive.getmembers()
                                 if m.isfile() and m.name.startswith(prefix) and m.name.rsplit("/", 1)[-1].lower().startswith("license")]
                if not license_names:
                    raise OSError("Published model licence is missing")
                contents["LICENSE"] = archive.extractfile(license_names[0]).read()
            for name, body in contents.items():
                target = model_root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
            marker.write_text(json.dumps({"archive_sha256": MODEL_SHA256,
                              "files": {name: hashlib.sha256(body).hexdigest() for name, body in contents.items()}}), encoding="utf-8")
        manifest = json.loads(marker.read_text(encoding="utf-8"))
        if manifest["archive_sha256"] != MODEL_SHA256 or any(
            hashlib.sha256((model_root / name).read_bytes()).hexdigest() != digest
            for name, digest in manifest["files"].items()):
            raise OSError("Installed fire-model files failed integrity verification")
        sys.path.insert(0, str(model_root))
        try:
            fm = importlib.import_module("pyretechnics.fuel_models")
            sf = importlib.import_module("pyretechnics.surface_fire")
            conv = importlib.import_module("pyretechnics.conversion")
            if Path(sf.__file__).resolve().parent != (model_root / "pyretechnics").resolve():
                raise OSError("Another fire-model version is already loaded")
        finally:
            sys.path.remove(str(model_root))
    return fm, sf, conv


def region(lat, lon):
    if 18 <= lat <= 23 and -161 <= lon <= -154:
        return "HI"
    if 51 <= lat <= 72 and (lon <= -129 or lon >= 170):
        return "AK"
    if 24 <= lat <= 50 and -125 <= lon <= -66:
        return "CONUS"
    raise ModelUnavailable("Potential fire intensity needs a LANDFIRE cell in CONUS, Alaska or Hawaii.")


def landscape_at(lat, lon):
    area = region(lat, lon)
    layers = {name: f"Landfire_LF2023/LF2023_{name}_{area}" for name in ("FBFM40", "CC", "CH")}
    layers.update(SlpD=f"Landfire_Topo/LF2020_SlpD_{area}", Asp=f"Landfire_Topo/LF2020_Asp_{area}")
    def sample(item):
        name, layer = item
        url = LAND + layer + "/ImageServer/getSamples?" + urlencode({
            "f": "json", "geometryType": "esriGeometryPoint",
            "geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
            "returnFirstValueOnly": "true", "interpolation": "RSP_NearestNeighbor",
            "renderingRule": json.dumps({"rasterFunction": "None"})})
        doc, source = _json(url)
        samples = doc.get("samples", [])
        try:
            value = float(samples[0]["value"]) if len(samples) == 1 else math.nan
        except (ValueError, TypeError, KeyError):
            value = math.nan
        if not math.isfinite(value):
            raise ModelUnavailable(f"No valid LANDFIRE {name} cell here; no replacement fuel was assumed.")
        return name, value, source
    with ThreadPoolExecutor(max_workers=3) as pool:
        samples = list(pool.map(sample, layers.items()))
    values = {name: value for name, value, _ in samples}
    code = values["FBFM40"]
    fm, _, _ = engine()
    if code != int(code) or int(code) not in fm.fuel_model_table or not fm.get_fuel_model(int(code)).burnable:
        raise ModelUnavailable(f"LANDFIRE fuel code {code:g} is nonburnable or unsupported. This does not rule out ember or building fire exposure.")
    if not 0 <= values["CC"] <= 100 or not 0 <= values["CH"] <= 10000 or not 0 <= values["SlpD"] < 90:
        raise ModelUnavailable("Unsupported LANDFIRE canopy or slope encoding.")
    if not 0 <= values["Asp"] <= 360 and not (values["SlpD"] == 0 and values["Asp"] == -1):
        raise ModelUnavailable("Unsupported LANDFIRE aspect encoding.")
    return {"fuel_model": int(code), "canopy_cover_fraction": values["CC"] / 100,
            "canopy_height_m": values["CH"] / 10, "slope_degrees": values["SlpD"],
            "aspect_degrees": max(0, values["Asp"]), "region": area,
            "fuel_vintage": "LANDFIRE 2023", "terrain_vintage": "LANDFIRE 2020",
            "nominal_resolution_m": 30, "sources": [source for _, _, source in samples]}


def weather_year(lat, lon, year):
    start = datetime(year, 1, 1) - timedelta(days=31)
    url = WEATHER + "?" + urlencode({"latitude": lat, "longitude": lon,
        "start_date": start.date().isoformat(), "end_date": f"{year}-12-31",
        "hourly": ",".join(VARIABLES[:-1]), "wind_speed_unit": "ms", "timezone": "GMT",
        "models": "era5", "elevation": "nan"})
    doc, source = _json(url)
    snow_url = WEATHER + "?" + urlencode({"latitude": lat, "longitude": lon,
        "start_date": start.date().isoformat(), "end_date": f"{year}-12-31",
        "hourly": "snow_depth", "timezone": "GMT", "models": "era5_land", "elevation": "nan"})
    snow, snow_source = _json(snow_url)
    if snow.get("hourly", {}).get("time") != doc.get("hourly", {}).get("time"):
        raise ValueError("ERA5 and ERA5-Land hourly timestamps do not align")
    doc["hourly"]["snow_depth"] = snow["hourly"]["snow_depth"]
    doc["hourly_units"]["snow_depth"] = snow["hourly_units"]["snow_depth"]
    source["snow_source"] = {**snow_source, "grid_latitude": snow.get("latitude"),
                           "grid_longitude": snow.get("longitude")}
    return doc, source


def equilibrium(temp_c, rh_pct):
    """Van Wagner/Viney drying and wetting EMC, kg water/kg dry fuel."""
    correction = 0.18 * (21.1 - temp_c) * (1 - math.exp(-0.115 * rh_pct))
    dry = (0.942 * rh_pct ** 0.679 + 0.000499 * math.exp(0.1 * rh_pct) + correction) / 100
    wet = (0.618 * rh_pct ** 0.753 + 0.000454 * math.exp(0.1 * rh_pct) + correction) / 100
    return max(0, min(dry, wet)), max(0, dry, wet)


def moisture_step(state, temp_c, rh_pct, rain_mm_h):
    """One-hour exact time-lag step; WRF-SFIRE default 1/10/100 h parameters."""
    wet, dry = equilibrium(temp_c, rh_pct)
    if state is None:
        state = [(wet + dry) / 2] * 3
    output = []
    rain = max(0, rain_mm_h - 0.05)
    for old, lag in zip(state, (1, 10, 100)):
        target = 2.5 if rain else min(dry, max(wet, old))
        rate = (1 - math.exp(-rain / 8)) / (1.4 * lag) if rain else 1 / lag
        output.append(old + (target - old) * -math.expm1(-rate))
    return output


def annual_maximum(doc, year, landscape, live_herb=0.60, live_woody=0.90):
    """Require every baseline and warm-up hour; never impute or pool daily values."""
    fm, sf, conv = engine()
    fuel = fm.get_fuel_model(landscape["fuel_model"])
    hours = (366 if calendar.isleap(year) else 365) * 24
    count = hours + 31 * 24
    start = datetime(year, 1, 1) - timedelta(days=31)
    hourly, units = doc.get("hourly", {}), doc.get("hourly_units", {})
    expected_units = dict(zip(VARIABLES, ("°C", "%", "mm", "m/s", "°", "m")))
    if doc.get("utc_offset_seconds") != 0 or any(units.get(k) != v for k, v in expected_units.items()):
        raise ValueError("Historical weather units or UTC time basis changed")
    if any(not isinstance(hourly.get(k), list) or len(hourly[k]) != count for k in ("time", *VARIABLES)):
        return {"year": year, "complete": False, "expected_hours": hours, "reason": "Incomplete hourly arrays or spin-up"}
    state, peak = None, None
    slope = math.tan(math.radians(landscape["slope_degrees"]))
    for index, stamp in enumerate(hourly["time"]):
        if stamp != (start + timedelta(hours=index)).isoformat(timespec="minutes"):
            raise ValueError("Historical weather contains duplicate, missing or out-of-order hours")
        vals = [hourly[k][index] for k in VARIABLES]
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in vals):
            return {"year": year, "complete": False, "expected_hours": hours, "reason": "Missing weather or spin-up values"}
        temp, rh, rain, wind, direction, snow = vals
        if not (-100 <= temp <= 70 and 0 <= rh <= 100 and 0 <= rain <= 500
                and 0 <= wind <= 150 and 0 <= direction <= 360 and 0 <= snow <= 30):
            raise ValueError("Historical weather is outside supported physical ranges")
        state = moisture_step(state, temp, rh, rain)
        if index < 31 * 24:
            continue
        # A snow-covered modeled surface cannot carry the surface fire in this scenario.
        intensity, spread, heat = 0.0, 0.0, 0.0
        if snow == 0:
            moist = fm.moisturize(fuel, (*state, 0.0, live_herb, live_woody))
            base = sf.calc_surface_fire_behavior_no_wind_no_slope(moist)
            # Upstream conversion: 10 m wind -> 20 ft wind -> canopy-adjusted midflame wind.
            midwind = sf.calc_midflame_wind_speed(conv.wind_speed_10m_to_wind_speed_20ft(wind * 60),
                          fuel.delta, landscape["canopy_height_m"] / 0.3048, landscape["canopy_cover_fraction"])
            behavior = sf.calc_surface_fire_behavior_max(base, midwind, direction, slope,
                                                        landscape["aspect_degrees"], True)
            intensity = float(behavior.max_fireline_intensity)
            spread = float(behavior.max_spread_rate) / 60
            heat = float(sf.calc_areal_heat_output(behavior.max_spread_rate, intensity))
        if not all(math.isfinite(v) and v >= 0 for v in (intensity, spread, heat)):
            raise ValueError("Fire behavior engine produced invalid output")
        if peak is None or intensity > peak["intensity_kw_m"]:
            peak = {"time_utc": stamp + "Z", "intensity_kw_m": intensity, "spread_rate_m_s": spread,
                    "areal_heat_release_kj_m2": heat, "dead_fuel_moisture_fraction": list(state),
                    "weather": dict(zip(VARIABLES, vals))}
    return {"year": year, "complete": True, "expected_hours": hours, "valid_hours": hours,
            "maximum_kw_m": peak["intensity_kw_m"], "peak": peak}


def empirical_points(years):
    values = [row["maximum_kw_m"] for row in years if row["complete"]]
    return [{"severity": value, "frequency_pct": 100 * sum(v >= value for v in values) / len(values),
             "exceedance_years": sum(v >= value for v in values), "complete_years": len(values)}
            for value in sorted(set(values))] if values else []


def _identity():
    return {"method": METHOD_VERSION, "engine_sha256": MODEL_SHA256,
            "start_year": START_YEAR, "end_year": END_YEAR, "minimum_years": MIN_YEARS}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _read_record(path):
    if not path.exists():
        return None
    saved = json.loads(path.read_text(encoding="utf-8"))
    if hashlib.sha256(saved["body"].encode()).hexdigest() != saved["sha256"]:
        raise OSError("Potential-fire calculation cache failed integrity verification")
    return json.loads(saved["body"])


def _write_record(path, result):
    body = json.dumps(result, ensure_ascii=False, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    temp.write_text(json.dumps({"body": body, "sha256": hashlib.sha256(body.encode()).hexdigest()}), encoding="utf-8")
    temp.replace(path)


def _result_path(lat, lon):
    return CACHE / "results" / (_digest([lat, lon, _identity()]) + ".json")


def _init_worker(download_gate):
    global DOWNLOAD_GATE
    DOWNLOAD_GATE = download_gate


@lru_cache(maxsize=1)
def _year_pool():
    # Spawn is safe from the threaded HTTP server on Windows. Share the download
    # limit across processes, rather than opening four streams per worker.
    context = multiprocessing.get_context("spawn")
    return ProcessPoolExecutor(max_workers=YEAR_WORKERS, mp_context=context,
                               initializer=_init_worker, initargs=(context.BoundedSemaphore(2),))


def _year_task(lat, lon, year, landscape):
    doc, source = weather_year(lat, lon, year)
    numerical_landscape = {key: landscape[key] for key in (
        "fuel_model", "canopy_cover_fraction", "canopy_height_m", "slope_degrees", "aspect_degrees")}
    key = _digest([_identity(), year, numerical_landscape, source["sha256"], source["snow_source"]["sha256"]])
    path = CACHE / "years" / (key + ".json")
    result = _read_record(path)
    if result is None:
        result = annual_maximum(doc, year, landscape)
        _write_record(path, result)
    return result, {**source, "year": year, "grid_latitude": doc.get("latitude"),
                    "grid_longitude": doc.get("longitude")}


def _calculate_years(lat, lon, landscape, progress):
    with POOL_LOCK:
        pool = _year_pool()
    remaining = iter(range(START_YEAR, END_YEAR + 1))
    pending = {}
    years, sources = {}, {}
    def submit_next():
        year = next(remaining, None)
        if year is not None:
            pending[pool.submit(_year_task, lat, lon, year, landscape)] = year
    # Bound the outstanding work per location, allowing another location to make
    # progress without waiting for all 30 years of the first point.
    for _ in range(YEAR_WORKERS):
        submit_next()
    try:
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                year = pending.pop(future)
                years[year], sources[year] = future.result()
                if progress:
                    progress({"phase": "calculating", "completed_years": len(years),
                              "total_years": END_YEAR - START_YEAR + 1})
                submit_next()
    finally:
        for future in pending:
            future.cancel()
    return [years[y] for y in sorted(years)], [sources[y] for y in sorted(sources)]


def _calculate(lat, lon, progress=None):
    coordinates(lat, lon)
    lat, lon = round(lat, 6), round(lon, 6)
    if progress:
        progress({"phase": "landscape", "completed_years": 0, "total_years": END_YEAR - START_YEAR + 1})
    landscape = landscape_at(lat, lon)
    if progress:
        progress({"phase": "calculating", "completed_years": 0, "total_years": END_YEAR - START_YEAR + 1})
    years, weather_sources = _calculate_years(lat, lon, landscape, progress)
    complete = sum(row["complete"] for row in years)
    available = complete >= MIN_YEARS
    return {"peril": "potential_fire_intensity", "metric": "Potential surface-fire intensity", "unit": "kW/m",
        "status": "available" if available else "unavailable", "hazard_kind": "potential_intensity_exceedance",
        "annual_probability_available": available, "actual_wildfire_probability_available": False,
        "trigger_unavailable_reason": "Insufficient complete historical-weather years." if not available else "",
        "frequency_label": "Annual potential-intensity exceedance frequency, 1991–2020 (%)",
        "frequency_basis": "Empirical count of complete years whose modeled hourly maximum is at or above the threshold; no fitted tails or interpolation.",
        "points": empirical_points(years) if available else [], "annual_maxima": years,
        "baseline": {"start_year": START_YEAR, "end_year": END_YEAR, "complete_years": complete,
                     "minimum_complete_years": MIN_YEARS, "excluded_years": [r["year"] for r in years if not r["complete"]]},
        "scenario": {"live_herbaceous_moisture_fraction": 0.60, "live_woody_moisture_fraction": 0.90,
                     "snow_cover_rule": "No surface spread when ERA5 snow_depth > 0", "wind_limit": True,
                     "spinup_days_per_year": 31, "calendar": "UTC", "crown_fire": False},
        "landscape": landscape, "source_name": "ERA5 / LANDFIRE / Rothermel–Byram research model",
        "source_url": REFERENCES[0]["url"], "source_references": REFERENCES,
        "provenance": {"engine": "Pyretechnics", "engine_version": MODEL_VERSION,
                       "engine_archive_sha256": MODEL_SHA256, "weather": weather_sources, "method_version": METHOD_VERSION},
        "details": [["Interpretation", INTERPRETATION], ["Baseline", f"{complete}/30 complete years, 1991–2020"],
                    ["Fuel model", str(landscape["fuel_model"]) + " · fixed LANDFIRE 2023"],
                    ["Resolution", "ERA5 0.25° weather; nominal 30 m landscape inputs"],
                    ["Fire behavior", "Heading surface fire only; no ignition/arrival model"]],
        "notes": [INTERPRETATION,
                  "Research prototype: the combined model has not been validated against historical intensity observations. No claim of improved accuracy.",
                  "Live fuel moisture is a fixed scenario (herbaceous 60%, woody 90% dry mass), not historical vegetation moisture. Dynamic herb curing follows the selected fuel model.",
                  "Fixed 2023 fuels and 2020 terrain are replayed under historical weather; this does not reconstruct historical landscapes or actual fires.",
                  "Dead fuel moisture uses a simplified WRF-SFIRE time-lag model with rainfall and hysteresis, without local moisture assimilation or explicit solar heating.",
                  "Annual maxima are taken from synchronized hourly model states; hourly resolution can miss subhourly peaks. Complete years include zero-spread years.",
                  "Thirty years cannot establish rare 1-in-100 or 1-in-500 levels. Zero exceedances would not prove zero future probability.",
                  "Potential intensity is heat release per fire-front length, not radiative power, building heat flux, burn severity or damage.",
                  "Open-Meteo free endpoint is for noncommercial use; commercial deployment needs appropriate service access. ERA5 and LANDFIRE attribution is retained."],
        **({"reason": f"Only {complete} complete years; at least {MIN_YEARS} required."} if not available else {})}


def potential_fire_at(lat, lon, progress=None):
    """Stable scientific cache plus a shared future for identical concurrent lookups."""
    coordinates(lat, lon)
    lat, lon = round(lat, 6), round(lon, 6)
    path = _result_path(lat, lon)
    with LOCK:
        cached = _read_record(path)
        if cached is not None:
            return cached
        owner = path not in INFLIGHT
        future = INFLIGHT.setdefault(path, Future())
    if not owner:
        # Return an independent object: map/API consumers attach coordinates and sort.
        return json.loads(json.dumps(future.result()))
    try:
        result = _calculate(lat, lon, progress)
        if result["status"] == "available":
            _write_record(path, result)
        future.set_result(result)
        return json.loads(json.dumps(result))
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        with LOCK:
            INFLIGHT.pop(path, None)


def potential_fire_request(lat, lon):
    """Nonblocking map endpoint; completed curves retain the normal result shape."""
    coordinates(lat, lon)
    lat, lon = round(lat, 6), round(lon, 6)
    cached = _read_record(_result_path(lat, lon))
    if cached is not None:
        return 200, {**cached, "latitude": lat, "longitude": lon}
    key = (lat, lon)
    with JOBS_LOCK:
        job = JOBS.get(key)
        if job is None:
            for other in list(JOBS):
                if JOBS[other]["future"].done():
                    del JOBS[other]
            if len(JOBS) >= 8:
                return 503, {"error": "Several fire locations are being prepared. Please retry shortly."}
            job = {"progress": {"phase": "queued", "completed_years": 0,
                                "total_years": END_YEAR - START_YEAR + 1}, "started": time.monotonic()}
            def update(progress):
                with JOBS_LOCK:
                    job["progress"] = progress
            job["future"] = JOB_RUNNER.submit(potential_fire_at, lat, lon, update)
            JOBS[key] = job
        if job["future"].done():
            try:
                result = job["future"].result()
                return 200, {**result, "latitude": lat, "longitude": lon}
            except ModelUnavailable as exc:
                JOBS.pop(key, None)
                return 200, {"peril": "potential_fire_intensity", "status": "unavailable", "points": [],
                             "reason": str(exc), "annual_probability_available": False,
                             "trigger_unavailable_reason": str(exc), "actual_wildfire_probability_available": False}
            except Exception:
                # Permit an explicit re-selection/retry; completed yearly work survives.
                JOBS.pop(key, None)
                return 502, {"error": "Fire preparation failed. Retry the location; completed years are saved."}
        return 202, {"peril": "potential_fire_intensity", "status": "processing", "points": [],
                     "progress": {**job["progress"], "elapsed_seconds": int(time.monotonic() - job["started"])}}


if __name__ == "__main__":
    import argparse
    import csv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--output", type=Path, default=CACHE / "example-result.json")
    args = parser.parse_args()
    print("Preparing fixed-landscape 1991–2020 potential-intensity curve; first run downloads historical weather.", flush=True)
    result = potential_fire_at(args.lat, args.lon)
    result.update(latitude=args.lat, longitude=args.lon)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    with args.output.with_suffix(".csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["year", "complete", "maximum_potential_intensity_kw_m", "peak_time_utc", "interpretation"])
        for row in result["annual_maxima"]:
            writer.writerow([row["year"], row["complete"], row.get("maximum_kw_m"), row.get("peak", {}).get("time_utc"), INTERPRETATION])
    print(json.dumps({"status": result["status"], "complete_years": result["baseline"]["complete_years"],
                      "curve_points": len(result["points"]), "output": str(args.output)}), flush=True)
