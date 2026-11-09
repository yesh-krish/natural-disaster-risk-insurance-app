"""Serve the map and sample locally stored TWDB flood-depth rasters."""

from __future__ import annotations

import http.client
import json
import math
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

# Malformed, truncated or unreachable upstream data. http.client.IncompleteRead is not an OSError,
# so without it a cut-off provider response drops the connection instead of returning a 502.
PROVIDER_ERRORS = (OSError, ValueError, LookupError, TypeError, AttributeError, http.client.HTTPException)

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "twdb"
EVENTS = (("20", "1in5"), ("10", "1in10"), ("1", "1in100"), ("0.2", "1in500"))
SOURCE_URL = (
    "https://twdb-flood-planning-resources-twdb.hub.arcgis.com/"
    "pages/cursory-floodplain-data-2025"
)
GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"


def geocode_address(address: str) -> dict:
    """Keep the map's existing route while sharing address handling with v1."""
    from address_lookup import geocode_address as lookup_address
    return lookup_address(address)


def tile_name(lat: float, lon: float) -> str:
    """TWDB tile names use the southwest corner of a 1-degree WGS84 tile."""
    return f"n{math.floor(lat)}w{abs(math.floor(lon))}"


def sample_depth(path: Path, lat: float, lon: float) -> float | None:
    # Import here so the web page can still explain missing setup if Rasterio is absent.
    import rasterio

    with rasterio.open(path) as raster:
        if raster.crs is None or raster.crs.to_epsg() != 4326:
            raise ValueError(f"Unexpected raster coordinate system: {raster.crs}")
        if not (raster.bounds.left <= lon < raster.bounds.right and
                raster.bounds.bottom <= lat < raster.bounds.top):
            raise ValueError("Selected point is outside the installed raster")
        value = next(raster.sample([(lon, lat)], masked=True))[0]
        if getattr(value, "mask", False):
            return None
        depth = float(value)
        # TWDB defines zero as either NoData or no associated modeled flood risk.
        return depth if math.isfinite(depth) and depth > 0 else None


def lookup(lat: float, lon: float) -> dict:
    tile = tile_name(lat, lon)
    output = {"tile": tile, "source": SOURCE_URL, "events": {}}
    for chance, event in EVENTS:
        path = DATA / event / f"{tile}.tif"
        entry = {"depth_ft": None, "status": "not_installed"}
        if path.is_file():
            try:
                depth = sample_depth(path, lat, lon)
                entry = {"depth_ft": depth,
                         "status": "sampled" if depth is not None else "no_mapped_depth"}
            except Exception as exc:
                # Keep one damaged tile from hiding the other event depths.
                print(f"Could not read {path}: {exc}")
                entry = {"depth_ft": None, "status": "read_error"}
        output["events"][chance] = entry
    return output


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_head(self):
        """Expose only browser assets and public documentation, never the workspace."""
        path = Path(self.translate_path(self.path)).resolve()
        try:
            relative = path.relative_to(ROOT)
        except ValueError:
            self.send_error(404, "Not found")
            return None
        assets = {"index.html", "script.js", "damage_model.js", "hazard_chart.js",
                  "hazard_trigger.js", "fire_history.js", "fire_proximity.js",
                  "docs/api-explorer.js", "examples/api_client.py"}
        if path == ROOT:
            self.path = "/index.html"
        elif not (relative.as_posix() in assets or (
                relative.parts[0] == "docs" and
                not any(part.startswith(".") for part in relative.parts) and
                path.suffix.lower() in {".md", ".html", ".txt"} and path.is_file())):
            self.send_error(404, "Not found")
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path in {"/api/docs", "/api/docs/"}:
            self.path = "/docs/api-explorer.html"
            return super().do_GET()
        if url.path == "/api/v1/openapi.json":
            from api_spec import openapi_document
            return self.send_json(200, openapi_document())
        if url.path == "/api/v1" or url.path.startswith("/api/v1/"):
            from api_v1 import dispatch, error_response
            try:
                code, payload = dispatch(url.path, url.query)
            except Exception as exc:  # noqa: BLE001 - always answer with JSON instead of dropping the socket
                print(f"Unexpected v1 failure: {type(exc).__name__}")
                code, payload = error_response(500, "internal_error", "The lookup could not complete. Retry later.")
            return self.send_json(code, payload)
        if url.path == "/api/potential-fire":
            from hazard_engine import coordinates
            from potential_fire import potential_fire_request
            query = parse_qs(url.query, keep_blank_values=True)
            try:
                if set(query) != {"lat", "lon"} or any(len(v) != 1 or not v[0].strip() for v in query.values()):
                    raise ValueError("Supply one lat and lon")
                lat, lon = float(query["lat"][0]), float(query["lon"][0])
                coordinates(lat, lon)
            except (KeyError, ValueError):
                return self.send_json(400, {"error": "Supply one valid lat and lon."})
            try:
                code, payload = potential_fire_request(lat, lon)
            except PROVIDER_ERRORS:
                return self.send_json(502, {"error": "Potential fire data are unavailable; no estimate was substituted."})
            return self.send_json(code, payload)
        if url.path == "/api/monitoring-station":
            from monitoring_stations import nearest_station
            from hazard_engine import coordinates
            query = parse_qs(url.query)
            try:
                lat, lon = float(query["lat"][0]), float(query["lon"][0])
                coordinates(lat, lon)
                peril = query["peril"][0]
                if peril not in {"flood", "cyclone", "rainfall", "earthquake", "wildfire", "fireline_intensity"}:
                    raise ValueError("Unknown hazard")
            except (KeyError, IndexError, ValueError):
                return self.send_json(400, {"error": "Supply valid lat, lon and peril."})
            try:
                return self.send_json(200, nearest_station(peril, lat, lon))
            except PROVIDER_ERRORS as exc:
                print(f"Station lookup failed: {type(exc).__name__}")
                return self.send_json(502, {"error": "Monitoring-station service is unavailable. Try selecting the location again."})
        if url.path == "/api/disaster-area":
            from area_hazard import area_disaster, validate_area
            query = parse_qs(url.query)
            try:
                bounds = [float(query[key][0]) for key in ("south", "west", "north", "east")]
                peril = query["peril"][0]
                site_class = query.get("site_class", ["BC"])[0]
                validate_area(*bounds, peril, site_class)
            except (KeyError, IndexError, ValueError):
                return self.send_json(400, {"error": "Supply valid south, west, north, east, peril and site_class."})
            try:
                payload = area_disaster(*bounds, peril, site_class)
            except PROVIDER_ERRORS as exc:
                print(f"Area government lookup failed: {type(exc).__name__}: {exc}")
                return self.send_json(502, {"error": "Area hazard lookup failed. Try again shortly."})
            return self.send_json(200, payload)
        if url.path == "/api/disaster":
            from disaster_data import disaster_at
            from hazard_engine import coordinates
            query = parse_qs(url.query)
            try:
                lat, lon = float(query["lat"][0]), float(query["lon"][0])
                coordinates(lat, lon)
                peril = query["peril"][0]
                if peril not in {"auto", "earthquake", "flood", "wildfire", "fireline_intensity", "potential_fire_intensity", "cyclone", "rainfall", "heat"}:
                    raise ValueError("Unknown disaster")
                site_class = query.get("site_class", ["BC"])[0]
                if site_class not in {"A", "B", "BC", "C", "CD", "D", "DE", "E"}:
                    raise ValueError("Unknown site class")
            except (KeyError, IndexError, ValueError):
                return self.send_json(400, {"error": "Supply valid lat, lon, peril and site_class."})
            try:
                if peril == "auto":
                    from hazard_relevance import most_relevant
                    payload = most_relevant(lat, lon, site_class)
                else:
                    payload = disaster_at(peril, lat, lon, site_class)
            except PROVIDER_ERRORS as exc:
                print(f"{peril} government lookup failed: {type(exc).__name__}: {exc}")
                return self.send_json(502, {"error": "Government data service is unavailable. Try again shortly."})
            return self.send_json(200, payload)
        if url.path == "/api/hazards":
            from hazard_engine import coordinates
            from hazard_providers import USGSPGAProvider, hazard_bundle
            query = parse_qs(url.query)
            try:
                lat, lon = float(query["lat"][0]), float(query["lon"][0])
                coordinates(lat, lon)
                provider = None
                if "earthquake_model" in query:
                    provider = USGSPGAProvider(query["earthquake_model"][0], query["site_class"][0])
            except (KeyError, IndexError, TypeError, ValueError):
                return self.send_json(400, {"error": "Supply valid lat/lon; earthquake_model also requires site_class."})
            try:
                bundle = hazard_bundle(lat, lon, earthquake=provider)
            except PROVIDER_ERRORS as exc:
                print(f"Hazard provider failed: {type(exc).__name__}")
                return self.send_json(502, {"error": "Hazard provider unavailable; no risk estimate was substituted."})
            return self.send_json(200, bundle)
        if url.path == "/api/geocode":
            address = parse_qs(url.query).get("address", [""])[0].strip()
            try:
                from address_lookup import normalize_address
                normalize_address(address)
            except ValueError as exc:
                return self.send_json(400, {"error": str(exc)})
            try:
                return self.send_json(200, geocode_address(address))
            except PROVIDER_ERRORS as exc:
                print(f"Census geocoding failed: {type(exc).__name__}")
                return self.send_json(502, {"error": "Address search is unavailable. Try again shortly."})
        if url.path != "/api/depths":
            return super().do_GET()
        try:
            query = parse_qs(url.query)
            lat = float(query["lat"][0])
            lon = float(query["lon"][0])
            if not (math.isfinite(lat) and math.isfinite(lon) and
                    -90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("Coordinates out of range")
        except (KeyError, IndexError, TypeError, ValueError):
            return self.send_json(400, {"error": "Supply valid lat and lon coordinates."})
        self.send_json(200, lookup(lat, lon))

    def do_POST(self):
        if self.path.split("?", 1)[0].startswith("/api/v1"):
            from api_v1 import error_response
            self.close_connection = True
            return self.send_json(*error_response(405, "method_not_allowed", "This research API supports GET requests only."))
        return self.send_error(405, "Method not allowed")

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST
    do_OPTIONS = do_POST

    def do_HEAD(self):
        if self.path.split("?", 1)[0].startswith("/api/v1"):
            from api_v1 import error_response
            return self.send_json(*error_response(405, "method_not_allowed", "Use GET requests."))
        return super().do_HEAD()

    def send_json(self, status: int, payload: dict):
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status == 405:
            self.send_header("Allow", "GET")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


if __name__ == "__main__":
    import argparse
    import os
    parser = argparse.ArgumentParser(description="Run the local NDApp research map and API.")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    print(f"Map: http://{args.host}:{args.port}/\nAPI explorer: http://{args.host}:{args.port}/api/docs", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
