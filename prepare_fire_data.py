"""Fetch selected original USFS GeoTIFFs using ZIP HTTP ranges (~325 MB for CONUS).

The national ZIP is never downloaded in full. CRC and SHA256 are recorded.
Run: python prepare_fire_data.py [--region CONUS|HI|AK]
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import struct
import urllib.request
import zipfile
import zlib

ARCHIVE = "https://www.fs.usda.gov/rds/archive/products/RDS-2016-0034-3/RDS-2016-0034-3.zip"
DATA = Path(__file__).resolve().parent / "data" / "usfs"


def request_range(start, end):
    response = urllib.request.urlopen(urllib.request.Request(ARCHIVE, headers={
        "Range": f"bytes={start}-{end}", "User-Agent": "NDApp-government-hazards/1.0"}), timeout=60)
    if response.status != 206:
        response.close()
        raise ValueError("USFS server did not honor byte range; refusing a full archive download")
    return response


class RangeFile(io.RawIOBase):
    def __init__(self, size):
        self.size, self.pos = size, 0
    def seekable(self):
        return True
    def tell(self):
        return self.pos
    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos
    def read(self, size=-1):
        size = self.size - self.pos if size < 0 else min(size, self.size - self.pos)
        if size <= 0:
            return b""
        with request_range(self.pos, self.pos + size - 1) as response:
            raw = response.read()
        if len(raw) != size:
            raise ValueError("Truncated ZIP metadata range")
        self.pos += size
        return raw


def download_entry(entry):
    # Never extract a remote archive path; only fixed allowlisted filenames.
    filename = Path(entry.filename).name
    target = DATA / filename
    manifest_path = DATA / (filename + ".json")
    if target.is_file() and manifest_path.is_file():
        print(f"Already installed: {filename}", flush=True)
        return
    if entry.compress_type != zipfile.ZIP_DEFLATED:
        raise ValueError("Unexpected archive compression")
    with request_range(entry.header_offset, entry.header_offset + 29) as response:
        header = response.read()
    if header[:4] != b"PK\x03\x04":
        raise ValueError("Invalid ZIP entry")
    name_size, extra_size = struct.unpack_from("<HH", header, 26)
    start = entry.header_offset + 30 + name_size + extra_size
    decoder = zlib.decompressobj(-15)
    temporary = DATA / (filename + ".partial")
    crc, count, digest = 0, 0, hashlib.sha256()
    print(f"Downloading {filename}: {entry.compress_size / 1e6:.1f} MB", flush=True)
    with request_range(start, start + entry.compress_size - 1) as response, temporary.open("wb") as output:
        remaining = entry.compress_size
        while remaining:
            block = response.read(min(1024 * 1024, remaining))
            if not block:
                raise ValueError("Truncated USFS archive entry")
            remaining -= len(block)
            raw = decoder.decompress(block)
            output.write(raw)
            crc = zlib.crc32(raw, crc)
            count += len(raw)
            digest.update(raw)
        raw = decoder.flush()
        output.write(raw)
        crc = zlib.crc32(raw, crc)
        count += len(raw)
        digest.update(raw)
    if not decoder.eof or count != entry.file_size or crc != entry.CRC:
        raise ValueError("USFS file failed size/CRC verification")
    temporary.replace(target)
    manifest_path.write_text(json.dumps({"source": ARCHIVE, "entry": entry.filename,
                             "sha256": digest.hexdigest(), "file_size": count,
                             "zip_crc32": entry.CRC}, indent=2), encoding="utf-8")
    print(f"Installed and verified: {filename}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", choices=["CONUS", "HI", "AK"], default="CONUS")
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(ARCHIVE, method="HEAD"), timeout=30) as response:
        size = int(response.headers["Content-Length"])
    wanted = {f"{args.region}_{name}.tif" for name in ["BP", "FLP5", "FLP6"]}
    with zipfile.ZipFile(RangeFile(size)) as archive:
        entries = [entry for entry in archive.infolist() if Path(entry.filename).name in wanted]
    if {Path(e.filename).name for e in entries} != wanted:
        raise ValueError("Source archive no longer has the expected three rasters")
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(download_entry, entries))


if __name__ == "__main__":
    main()
