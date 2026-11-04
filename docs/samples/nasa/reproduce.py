"""Offline NASA evaluation sample; --download DIR retrieves new public responses.

Standard library only. Does not import, configure, or run the application.
Rolling FIRMS downloads cannot reproduce an older snapshot; use saved bytes.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen

URLS = {
    "firms-noaa20-24h.csv": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_USA_contiguous_and_Hawaii_24h.csv",
    "mod11a2-san-antonio.json": "https://modis.ornl.gov/rst/api/v1/MOD11A2/subset?latitude=29.4241&longitude=-98.4936&startDate=A2024185&endDate=A2024185&kmAboveBelow=0&kmLeftRight=0",
    "mod11a2-bands.json": "https://modis.ornl.gov/rst/api/v1/MOD11A2/bands",
    "mod11a2-cmr-granule.json": "https://cmr.earthdata.nasa.gov/search/granules.json?collection_concept_id=C2269056084-LPCLOUD&temporal=2024-07-03T00%3A00%3A00Z%2C2024-07-10T23%3A59%3A59Z&producer_granule_id=MOD11A2.A2024185.h09v06.%2A&options%5Bproducer_granule_id%5D%5Bpattern%5D=true&page_size=1",
}
BBOX = (-99, 29, -98, 30)  # west,south,east,north; San Antonio region
DAY = "2026-11-02"


def summarize(folder):
    fire = list(csv.DictReader(io.StringIO((folder / "firms-noaa20-24h.csv").read_text(encoding="utf-8-sig"))))
    west, south, east, north = BBOX
    rows = [r for r in fire if r["acq_date"] == DAY
            and west <= float(r["longitude"]) <= east
            and south <= float(r["latitude"]) <= north]
    for r in rows:
        datetime.strptime(r["acq_date"] + r["acq_time"].zfill(4), "%Y-%m-%d%H%M")
    doc = json.loads((folder / "mod11a2-san-antonio.json").read_bytes())
    assert doc["nrows"] == doc["ncols"] == 1
    bands = {s["band"]: s["data"][0] for s in doc["subset"]}
    granules = json.loads((folder / "mod11a2-cmr-granule.json").read_bytes())["feed"]["entry"]
    assert len(granules) == 1
    granule_id = granules[0]["producer_granule_id"]
    assert granule_id == "MOD11A2.A2024185.h09v06.061." + doc["subset"][0]["proc_date"]
    lst = {}
    for period in ("Day", "Night"):
        raw, qc = bands[f"LST_{period}_1km"], bands[f"QC_{period}"]
        mandatory, data, emissivity, error = (qc >> shift & 3 for shift in (0, 2, 4, 6))
        lst[period.lower()] = {
            "raw_lst": raw, "raw_qc": qc,
            "kelvin": round(raw * 0.02, 2) if raw else None,
            "celsius": round(raw * 0.02 - 273.15, 2) if raw else None,
            "qa_codes": {"mandatory": mandatory, "data_quality": data,
                         "emissivity_error": emissivity, "lst_error": error},
            "strict_filter_accepted": bool(raw and mandatory == data == error == 0 and emissivity <= 1),
            "clear_sky_bitmap": bands[f"Clear_sky_{period.lower()}s"],
        }
    return {
        "fire": {"bbox_wsen": BBOX, "date_utc": DAY,
                 "source_rows": len(fire), "selected_rows": len(rows),
                 "source_date_min": min(r["acq_date"] for r in fire),
                 "source_date_max": max(r["acq_date"] for r in fire),
                 "acquisition_times_utc": sorted({r["acq_time"].zfill(4) for r in rows}),
                 "confidence_values": sorted({r["confidence"] for r in rows}),
                 "version_values": sorted({r["version"] for r in rows}),
                 "frp_mw_min": min((float(r["frp"]) for r in rows), default=None),
                 "frp_mw_max": max((float(r["frp"]) for r in rows), default=None)},
        "lst": {"location": [29.4241, -98.4936], "composite_start": "2024-07-03",
                "composite_end_inclusive": "2024-07-10", "collection_version": "061 (matched to CMR by product/date/tile/processing ID)",
                "matched_granule_id": granule_id,
                "tile": doc["subset"][0]["tile"], "proc_date": doc["subset"][0]["proc_date"],
                "cellsize_sinusoidal_m": doc["cellsize"], **lst},
    }, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", type=Path, help="Download to a new directory; never overwrite the saved sample")
    args = parser.parse_args()
    folder = args.download or Path(__file__).resolve().parent / "capture"
    if args.download:
        folder.mkdir(parents=True, exist_ok=False)
        manifest = {"files": {}, "authentication": "none"}
        for name, url in URLS.items():
            with urlopen(Request(url, headers={"User-Agent": "NDApp-NASA-evaluation/1.0"}), timeout=60) as response:
                body = response.read()
                entry = {"url": url, "resolved_url": response.url, "http_status": response.status,
                         "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                         "last_modified": response.headers.get("Last-Modified"),
                         "http_date": response.headers.get("Date"),
                         "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            (folder / name).write_bytes(body)
            manifest["files"][name] = entry
        summary, rows = summarize(folder)
        with (folder / "firms-san-antonio-2026-11-02.csv").open("w", newline="", encoding="utf-8") as out:
            writer = csv.DictWriter(out, fieldnames=list(csv.DictReader(io.StringIO((folder / "firms-noaa20-24h.csv").read_text())).fieldnames))
            writer.writeheader()
            writer.writerows(rows)
        manifest["summary"] = summary
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    else:
        manifest = json.loads((folder / "manifest.json").read_bytes())
        for name, entry in manifest["files"].items():
            assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == entry["sha256"], name
        summary, rows = summarize(folder)
        # JSON round trip normalizes bbox tuples into lists.
        assert json.loads(json.dumps(summary)) == manifest["summary"]
        with (folder / "firms-san-antonio-2026-11-02.csv").open(newline="", encoding="utf-8") as source:
            assert list(csv.DictReader(source)) == rows
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
