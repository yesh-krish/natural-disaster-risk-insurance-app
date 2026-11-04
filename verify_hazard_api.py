"""Live read-only smoke check: python verify_hazard_api.py [--live-usgs].

Validates real HTTP responses against the schema; with --live-usgs also writes
the normalized San Antonio research bundle into examples/san_antonio_hazards.json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request

from jsonschema import Draft202012Validator, FormatChecker
from http.server import ThreadingHTTPServer
from server import Handler

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-usgs", action="store_true")
    args = parser.parse_args()
    schema = json.loads((ROOT / "schemas/hazard-location.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        routes = ["/api/hazards?lat=29.4241&lon=-98.4936"]
        if args.live_usgs:
            routes.append(routes[0] + "&earthquake_model=STATIC_CONUS_2023_R2&site_class=BC")
        for route in routes:
            with urllib.request.urlopen(base + route, timeout=60) as response:
                bundle = json.load(response)
            validator.validate(bundle)
            print("schema valid:", [(h["peril"], h["status"]) for h in bundle["hazards"]])
            if "earthquake_model" in route:
                quake = next(h for h in bundle["hazards"] if h["peril"] == "earthquake")
                assert quake["status"] == "available", quake
                (ROOT / "examples/san_antonio_hazards.json").write_text(
                    json.dumps(bundle, indent=2, allow_nan=False) + "\n", encoding="utf-8")
                print("USGS PGA nodes:", len(quake["representation"]["points"]))
        with urllib.request.urlopen(base + "/api/depths?lat=29.4241&lon=-98.4936", timeout=30) as response:
            depths = json.load(response)
        assert set(depths["events"]) == {"20", "10", "1", "0.2"}
        for route in ("/api/hazards?lat=nan&lon=0", "/api/hazards?lat=91&lon=0",
                      "/api/hazards?lat=29.4&lon=-98.5&earthquake_model=model"):
            try:
                urllib.request.urlopen(base + route, timeout=30)
            except urllib.error.HTTPError as exc:
                assert exc.code == 400, exc.code
            else:
                raise AssertionError("Invalid query should return 400")
        print("HTTP smoke passed: hazard schema, invalid inputs, and existing depths response")
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


if __name__ == "__main__":
    main()
