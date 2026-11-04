"""Verified USGS static PGA adapter and NDApp's existing local TWDB adapter."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
import urllib.request

from hazard_engine import HazardCurve, ModelUnavailable, coordinates, rate_to_aep

USGS_ORIGIN = "https://earthquake.usgs.gov"
USGS_MODELS = USGS_ORIGIN + "/ws/nshmp/hazard/static/nshms"


def fetch_json(url: str) -> tuple[dict, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "NDApp-hazard-research/0.1",
                                                  "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ModelUnavailable("Agency response exceeds size limit")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ModelUnavailable("Unexpected agency response")
    return payload, hashlib.sha256(raw).hexdigest()


class USGSPGAProvider:
    """Pin an explicit installed model ID and site class; never silently upgrade.

    Static curves use siteClass; continuous Vs30 belongs to dynamic services.
    """
    def __init__(self, model_id: str, site_class: str):
        if site_class not in {"A", "B", "BC", "C", "CD", "D", "DE", "E"}:
            raise ValueError("Unsupported site class")
        if not model_id:
            raise ValueError("Select an explicit NSHM model ID")
        self.model_id, self.site_class = model_id, site_class

    def curve_document_at(self, latitude: float, longitude: float) -> tuple[HazardCurve, dict]:
        coordinates(latitude, longitude)
        models, _ = fetch_json(USGS_MODELS)
        model = next((m for m in models.get("response", []) if m.get("id") == self.model_id), None)
        if model is None:
            raise ModelUnavailable("Pinned NSHM is not installed")
        path = model.get("url", "")
        # Endpoint comes from agency metadata, but cannot change the outbound host.
        if not path.startswith("/ws/nshmp/") or "?" in path or "#" in path or ".." in path:
            raise ModelUnavailable("Unexpected NSHM service path")
        url = USGS_ORIGIN + path.rstrip("/") + "/hazard?" + urlencode({
            "latitude": latitude, "longitude": longitude, "siteClass": self.site_class,
            "imt": "PGA", "format": "JSON"})
        payload, digest = fetch_json(url)
        if payload.get("status") != "success":
            raise ModelUnavailable("USGS did not return a successful curve")
        try:
            metadata = payload["response"]["metadata"]
            request = payload["request"]
            if (metadata["imt"]["value"] != "PGA" or metadata["siteClass"] != self.site_class
                    or metadata["xLabel"] != "Ground Motion (g)"
                    or "Annual Frequency" not in metadata["yLabel"]
                    or request["latitude"] != latitude or request["longitude"] != longitude):
                raise ModelUnavailable("USGS units/site/metric mismatch")
            xs, ys = payload["response"]["data"]["xs"], payload["response"]["data"]["ys"]
            if len(xs) != len(ys):
                raise ModelUnavailable("USGS curve array lengths differ")
            curve = HazardCurve("pga_pct_g", tuple((x * 100, y) for x, y in zip(xs, ys)),
                                "annual_rate", "log_log")
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelUnavailable(f"Invalid USGS curve contract: {exc}") from exc
        doc = curve_document(curve, provider="USGS", model_version=self.model_id,
                             endpoint=url, artifact_sha256=digest,
                             definition_id="site_pga_nshm_shakemap_v1",
                             definition="Site PGA; %g; ground-motion component must match contract",
                             spatial_method="agency_interpolated_grid",
                             assumptions={"site_class": self.site_class,
                                          "occurrence_model": "stationary_poisson",
                                          "raw_metadata": metadata})
        return curve, doc

    def model_at(self, latitude: float, longitude: float) -> HazardCurve:
        return self.curve_document_at(latitude, longitude)[0]


class TWDBFloodProvider:
    def curve_document_at(self, latitude: float, longitude: float) -> tuple[HazardCurve, dict]:
        from server import DATA, EVENTS, SOURCE_URL, lookup
        coordinates(latitude, longitude)
        if not 25 <= latitude <= 37 or not -107 <= longitude <= -93:
            raise ModelUnavailable("TWDB coverage is Texas; point outside regional bounds")
        result = lookup(latitude, longitude)
        points = []
        files = []
        for chance, event in EVENTS:
            entry = result["events"][chance]
            if entry["status"] != "sampled" or entry["depth_ft"] is None:
                raise ModelUnavailable(f"TWDB {chance}%: {entry['status']}; no complete depth curve")
            points.append((entry["depth_ft"] * 0.3048, float(chance) / 100))
            path = DATA / event / f"{result['tile']}.tif"
            files.append({"path": str(path.relative_to(Path(__file__).resolve().parent)),
                          "size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns})
        try:
            curve = HazardCurve("inundation_m", tuple(sorted(points)), "annual_probability")
        except ValueError as exc:
            raise ModelUnavailable(f"TWDB depths cannot form a strictly ordered curve: {exc}") from exc
        doc = curve_document(curve, provider="TWDB", model_version="2025_scenario_5_combined_peril",
                             endpoint=SOURCE_URL, artifact_sha256=None,
                             definition_id="twdb_s5_peak_depth_above_ground_v1",
                             definition="Peak combined-peril inundation above ground, meters",
                             spatial_method="raster_cell_sample",
                             assumptions={"tile": result["tile"], "horizontal_crs": "EPSG:4326",
                                          "source_unit": "ft", "resolution_m": 3,
                                          "files": files,
                                          "zero_is_ambiguous_nodata": True,
                                          "rating_status": "research_only"})
        return curve, doc

    def model_at(self, latitude: float, longitude: float) -> HazardCurve:
        return self.curve_document_at(latitude, longitude)[0]


def curve_document(curve: HazardCurve, *, provider: str, model_version: str, endpoint: str,
                   artifact_sha256: str | None, definition_id: str, definition: str,
                   spatial_method: str, assumptions: dict) -> dict:
    from hazard_engine import UNITS
    return {"status": "available", "source": {"provider": provider, "model_version": model_version,
            "endpoint": endpoint, "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "artifact_sha256": artifact_sha256, "spatial_method": spatial_method},
            "metrics": [{"id": curve.metric, "unit": UNITS[curve.metric],
                         "definition_id": definition_id, "definition": definition}],
            "representation": {"kind": "curve", "frequency_measure": curve.frequency_measure,
                               "interpolation": curve.interpolation,
                               "points": [{"intensity": x, "frequency": y,
                                           "annual_exceedance_probability": rate_to_aep(y)
                                           if curve.frequency_measure == "annual_rate" else y}
                                          for x, y in curve.points]},
            "assumptions": assumptions}


def hazard_bundle(latitude: float, longitude: float, *, earthquake: USGSPGAProvider | None = None) -> dict:
    """Four-peril coordinate response; unavailable models remain explicit."""
    coordinates(latitude, longitude)
    entries = []
    for peril, provider in (("earthquake", earthquake), ("flood", TWDBFloodProvider()),
                            ("rain_wind", None), ("wildfire", None)):
        if provider is None:
            reason = ("Select a pinned NSHM and site class" if peril == "earthquake" else
                      "Calibrated paired storm catalog is not installed" if peril == "rain_wind" else
                      "BP and compatible conditional flame-length layers are not installed")
            entry = {"status": "unavailable", "reason": reason}
        else:
            try:
                _, entry = provider.curve_document_at(latitude, longitude)
            except ModelUnavailable as exc:
                entry = {"status": "unavailable", "reason": str(exc)}
        entries.append({"peril": peril, **entry})
    return {"schema_version": "1.0.0", "location": {"latitude": latitude, "longitude": longitude,
            "horizontal_crs": "EPSG:4326"}, "reference_duration_years": 1,
            "rating_status": "research_only", "hazards": entries}
