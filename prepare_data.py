"""Download and unpack TWDB's 2025 existing-conditions depth tiles for one area."""

from __future__ import annotations

import argparse
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "twdb"
BASE_URL = (
    "https://s3.amazonaws.com/twdb-gis-data/Staged/office-of-planning/"
    "flood-planning/flood-data/flood-risk/cursory-floodplain-data-2025/"
    "Scenario_5_Existing_Conditions/"
    "Fathom_3m_Combined_Peril_Scenario_5_Depth_Raster_Tiles"
)
EVENTS = ("1in5", "1in10", "1in100", "1in500")


def prepare(tile: str):
    if not (tile.startswith("n") and "w" in tile and
            tile[1:tile.index("w")].isdigit() and tile[tile.index("w") + 1:].isdigit()):
        raise ValueError("Use a TWDB tile name such as n29w99")

    for event in EVENTS:
        folder = DATA / event
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{tile}.tif"
        if target.exists():
            print(f"Already installed: {target}")
            continue

        filename = f"{tile}_2020_0p50_combined_{event}.zip"
        url = f"{BASE_URL}/{event}/{filename}"
        archive = folder / filename
        if not archive.exists():
            partial = folder / f"{filename}.part"
            print(f"Downloading {event}: {url}", flush=True)
            try:
                with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as out:
                    shutil.copyfileobj(response, out, length=1024 * 1024)
                partial.replace(archive)
            except Exception:
                partial.unlink(missing_ok=True)
                raise

        print(f"Extracting {event} into {target}", flush=True)
        with zipfile.ZipFile(archive) as zipped:
            candidates = [item for item in zipped.infolist()
                          if not item.is_dir() and
                          Path(item.filename).suffix.lower() in (".tif", ".tiff") and
                          Path(item.filename).name.lower().startswith(tile.lower())]
            if len(candidates) != 1:
                raise RuntimeError(f"Expected one {tile} GeoTIFF in {archive}; found {len(candidates)}")
            if shutil.disk_usage(folder).free < candidates[0].file_size:
                raise RuntimeError(f"Not enough free disk space to extract {archive}")
            temporary = target.with_suffix(".tif.part")
            try:
                with zipped.open(candidates[0]) as source, temporary.open("wb") as out:
                    shutil.copyfileobj(source, out, length=1024 * 1024)
                temporary.replace(target)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        archive.unlink()  # The extracted raster is the local source of record.
        print(f"Ready: {target}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tile", default="n29w99", help="1-degree TWDB tile; San Antonio is n29w99")
    args = parser.parse_args()
    try:
        prepare(args.tile)
    except Exception as exc:
        print(f"Data preparation failed: {exc}", file=sys.stderr)
        sys.exit(1)
