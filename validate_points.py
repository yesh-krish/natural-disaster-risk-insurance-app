"""Reproducible ten-point TWDB/FEMA cross-check around central San Antonio.

This checks data flow and highlights disagreements; it is not ground-truth validation.
"""

from __future__ import annotations

import csv
import json
import math
import random
import time
import urllib.parse
import urllib.request
from pathlib import Path

import rasterio

from server import DATA, lookup

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "validation" / "san_antonio_points.csv"
FEMA_URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
SEED = 20261007
BBOX = (29.37, 29.49, -98.57, -98.45)  # Central San Antonio; all in n29w99.


def choose_points() -> list[tuple[str, float, float]]:
    """Choose five mapped and five blank 1% cells with a fixed random seed."""
    rng = random.Random(SEED)
    candidates = [(rng.uniform(BBOX[0], BBOX[1]), rng.uniform(BBOX[2], BBOX[3]))
                  for _ in range(4000)]
    chosen: dict[str, list[tuple[float, float]]] = {"mapped": [], "blank": []}
    with rasterio.open(DATA / "1in100" / "n29w99.tif") as raster:
        values = raster.sample(((lon, lat) for lat, lon in candidates), masked=True)
        for (lat, lon), sample in zip(candidates, values):
            value = sample[0]
            mapped = not getattr(value, "mask", False) and math.isfinite(float(value)) and float(value) > 0
            group = "mapped" if mapped else "blank"
            if len(chosen[group]) == 5:
                continue
            # Avoid choosing nearly identical spots within either stratum.
            if any(math.hypot(lat - other_lat, lon - other_lon) < 0.006
                   for other_lat, other_lon in chosen[group]):
                continue
            chosen[group].append((lat, lon))
            if all(len(points) == 5 for points in chosen.values()):
                break
    if not all(len(points) == 5 for points in chosen.values()):
        raise RuntimeError("Could not find five points in each stratum")
    return [(group, lat, lon) for group in ("mapped", "blank") for lat, lon in chosen[group]]


def query_fema(lat: float, lon: float) -> dict:
    params = urllib.parse.urlencode({
        "f": "json", "where": "1=1", "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "returnGeometry": "false",
        "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF",
    })
    request = urllib.request.Request(f"{FEMA_URL}?{params}", headers={"User-Agent": "NDApp-research/0.1"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            if payload.get("error"):
                raise ValueError(payload["error"].get("message", "FEMA service error"))
            features = payload.get("features", [])
            return {"status": "found" if features else "no_polygon",
                    "attributes": features[0]["attributes"] if features else {}}
        except Exception as exc:
            if attempt == 2:
                return {"status": "error", "attributes": {}, "error": str(exc)}
            time.sleep(attempt + 1)


def run():
    OUTPUT.parent.mkdir(exist_ok=True)
    fieldnames = ["point_id", "latitude", "longitude", "selection_1pct", "twdb_tile",
                  "depth_20pct_ft", "depth_10pct_ft", "depth_1pct_ft", "depth_0_2pct_ft",
                  "status_20pct", "status_10pct", "status_1pct", "status_0_2pct",
                  "fema_status", "fema_fld_zone", "fema_zone_subty", "fema_sfha_tf",
                  "comparison_1pct", "fema_error"]
    rows = []
    for number, (group, lat, lon) in enumerate(choose_points(), 1):
        twdb = lookup(lat, lon)
        fema = query_fema(lat, lon)
        attributes = fema["attributes"]
        sfha = attributes.get("SFHA_TF")
        comparison = "unavailable"
        if fema["status"] == "found" and sfha in ("T", "F"):
            comparison = "same" if (group == "mapped") == (sfha == "T") else "different"
        row = {"point_id": number, "latitude": f"{lat:.8f}", "longitude": f"{lon:.8f}",
               "selection_1pct": group, "twdb_tile": twdb["tile"],
               "fema_status": fema["status"], "fema_fld_zone": attributes.get("FLD_ZONE", ""),
               "fema_zone_subty": attributes.get("ZONE_SUBTY", ""),
               "fema_sfha_tf": sfha or "", "comparison_1pct": comparison,
               "fema_error": fema.get("error", "")}
        for chance, label in (("20", "20pct"), ("10", "10pct"), ("1", "1pct"), ("0.2", "0_2pct")):
            event = twdb["events"][chance]
            row[f"depth_{label}_ft"] = "" if event["depth_ft"] is None else f"{event['depth_ft']:.2f}"
            row[f"status_{label}"] = event["status"]
        rows.append(row)
        print(f"{number}/10: {group}, FEMA {fema['status']}, comparison {comparison}", flush=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    run()
