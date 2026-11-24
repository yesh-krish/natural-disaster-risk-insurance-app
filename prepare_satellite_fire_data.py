"""Download NASA FIRMS yearly US archives and keep presumed vegetation fires, per satellite.

Builds the local archives behind the satellite-fire exceedance curve and its
per-satellite monitor table (satellite_fire_history.py). Each satellite has its
own folder of complete calendar years (no partial first year):

  firms_viirs_snpp    Suomi NPP VIIRS, 375 m    2013–2025  (data begin 2012-01-20)
  firms_viirs_noaa20  NOAA-20 VIIRS, 375 m      2019–2025  (data begin 2018-04-01)
  firms_modis_terra   Terra MODIS, 1 km         2001–2025
  firms_modis_aqua    Aqua MODIS, 1 km          2003–2025  (data begin 2002-07-04)

Each yearly file (~8–40 MB) is streamed; nothing raw is kept. Kept rows: FIRMS
`type` 0 (presumed vegetation fire), nominal/high confidence (VIIRS n/h; MODIS
confidence ≥ 30). The MODIS file holds Terra and Aqua and is split by satellite.
Output: data/<folder>/<year>.npz (float32 latitude, longitude, FRP; int16 day of
year) and data/<folder>/manifest.json with URLs, SHA256 hashes and row counts.

Run: python prepare_satellite_fire_data.py [--satellites viirs_snpp,modis_terra] [--force]
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import urllib.request

import numpy as np

ROOT = Path(__file__).resolve().parent / "data"
BASE = "https://firms.modaps.eosdis.nasa.gov/data/country"
MAX_BYTES = 120_000_000
# Downloads: one entry per NASA file family, listing the satellites it is split into.
FAMILIES = {
    "viirs-snpp": {"url": BASE + "/viirs-snpp/{year}/viirs-snpp_{year}_United_States.csv", "instrument": "VIIRS",
                   "satellites": {"viirs_snpp": {"folder": "firms_viirs_snpp", "years": (2013, 2025), "code": None,
                                                 "name": "Suomi NPP VIIRS (375 m)"}}},
    "viirs-jpss1": {"url": BASE + "/viirs-jpss1/{year}/viirs-jpss1_{year}_United_States.csv", "instrument": "VIIRS",
                    "satellites": {"viirs_noaa20": {"folder": "firms_viirs_noaa20", "years": (2019, 2025), "code": None,
                                                    "name": "NOAA-20 VIIRS (375 m)"}}},
    "modis": {"url": BASE + "/modis/{year}/modis_{year}_United_States.csv", "instrument": "MODIS",
              "satellites": {"modis_terra": {"folder": "firms_modis_terra", "years": (2001, 2025), "code": "Terra",
                                             "name": "Terra MODIS (1 km)"},
                             "modis_aqua": {"folder": "firms_modis_aqua", "years": (2003, 2025), "code": "Aqua",
                                            "name": "Aqua MODIS (1 km)"}}},
}
SATELLITES = {key: {**spec, "family": family} for family, info in FAMILIES.items() for key, spec in info["satellites"].items()}


class HashingReader(io.RawIOBase):
    """Stream a response through SHA256 without holding the whole file."""

    def __init__(self, response):
        self.response, self.sha, self.size = response, hashlib.sha256(), 0

    def readable(self):
        return True

    def readinto(self, buffer):
        chunk = self.response.read(len(buffer))
        self.sha.update(chunk)
        self.size += len(chunk)
        if self.size > MAX_BYTES:
            raise OSError("FIRMS yearly file exceeds size limit")
        buffer[:len(chunk)] = chunk
        return len(chunk)


def confident(instrument, value):
    value = (value or "").strip().lower()
    if instrument == "VIIRS":
        return value in {"n", "h", "nominal", "high"}
    try:
        return float(value) >= 30  # MODIS: <30 low, 30–79 nominal, ≥80 high
    except ValueError:
        return False


def build_year(family, year, wanted):
    """Download one yearly file and write one .npz per wanted satellite; returns manifest entries."""
    info = FAMILIES[family]
    url = info["url"].format(year=year)
    out = {key: {"lat": [], "lon": [], "frp": [], "doy": [], "dropped": {"low_confidence": 0, "static_or_other_type": 0, "invalid": 0}}
           for key in wanted}
    codes = {SATELLITES[key]["code"]: key for key in wanted}
    request = urllib.request.Request(url, headers={"User-Agent": "NDApp-satellite-fire-archive/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        reader = HashingReader(response)
        for row in csv.DictReader(io.TextIOWrapper(io.BufferedReader(reader, 1 << 20), encoding="ascii", newline="")):
            key = codes.get(None) or codes.get((row.get("satellite") or "").strip())
            if key is None:
                continue
            target = out[key]
            if row.get("type") != "0":
                target["dropped"]["static_or_other_type"] += 1
                continue
            if not confident(info["instrument"], row.get("confidence")):
                target["dropped"]["low_confidence"] += 1
                continue
            try:
                when = date.fromisoformat(row["acq_date"])
                lat, lon, frp = float(row["latitude"]), float(row["longitude"]), float(row["frp"])
            except (KeyError, ValueError):
                target["dropped"]["invalid"] += 1
                continue
            if when.year != year or not (-90 <= lat <= 90 and -180 <= lon <= 180) or frp < 0:
                target["dropped"]["invalid"] += 1
                continue
            target["lat"].append(lat); target["lon"].append(lon); target["frp"].append(frp)
            target["doy"].append(when.timetuple().tm_yday)
    fetched = datetime.now(timezone.utc).isoformat()
    entries = {}
    for key, target in out.items():
        folder = ROOT / SATELLITES[key]["folder"]
        folder.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(folder / f"{year}.npz", latitude=np.array(target["lat"], np.float32),
                            longitude=np.array(target["lon"], np.float32), frp=np.array(target["frp"], np.float32),
                            day_of_year=np.array(target["doy"], np.int16))
        entries[key] = {"year": year, "url": url, "sha256": reader.sha.hexdigest(), "bytes": reader.size,
                        "fetched_at": fetched, "kept_rows": len(target["lat"]), "dropped_rows": target["dropped"]}
    return entries


def manifest_for(key):
    spec = SATELLITES[key]
    path = ROOT / spec["folder"] / "manifest.json"
    manifest = json.loads(path.read_text()) if path.exists() else {}
    manifest.update(source=f"NASA FIRMS {spec['name']}, standard archive (country yearly files)",
                    filter="type 0 (presumed vegetation fire); nominal or high confidence",
                    satellite=key, name=spec["name"], first_year=spec["years"][0], last_year=spec["years"][1])
    manifest.setdefault("years", {})
    return path, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--satellites", default=",".join(SATELLITES), help="Comma-separated: " + ", ".join(SATELLITES))
    parser.add_argument("--force", action="store_true", help="Rebuild years that already exist")
    args = parser.parse_args()
    chosen = [key.strip() for key in args.satellites.split(",") if key.strip()]
    unknown = set(chosen) - set(SATELLITES)
    if unknown:
        parser.error("unknown satellite: " + ", ".join(sorted(unknown)))
    manifests = {key: manifest_for(key) for key in chosen}
    for family in FAMILIES:
        keys = [key for key in chosen if SATELLITES[key]["family"] == family]
        years = sorted({year for key in keys for year in range(SATELLITES[key]["years"][0], SATELLITES[key]["years"][1] + 1)})
        for year in years:
            wanted = [key for key in keys if SATELLITES[key]["years"][0] <= year <= SATELLITES[key]["years"][1]
                      and (args.force or str(year) not in manifests[key][1]["years"]
                           or not (ROOT / SATELLITES[key]["folder"] / f"{year}.npz").exists())]
            if not wanted:
                print(family, year, "already built")
                continue
            for key, entry in build_year(family, year, wanted).items():
                path, manifest = manifests[key]
                manifest["years"][str(year)] = entry
                path.write_text(json.dumps(manifest, indent=1))
                print(key, year, entry["kept_rows"], "kept", entry["dropped_rows"])


if __name__ == "__main__":
    main()
