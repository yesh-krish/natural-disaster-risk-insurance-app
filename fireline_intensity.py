"""Native TxWRAP fireline intensity; scenario statistics, never annual AEP.

The service's raw F32 raster is already in kW/m. Do not invert a flame-length
relationship or infer a probability from weather weights or intensity percentiles.
"""
from datetime import datetime, timezone
from functools import lru_cache
import json
import math
import re
from urllib.parse import urlencode

from hazard_engine import ModelUnavailable, coordinates
from hazard_providers import fetch_json

SERVICE = ("https://gis.texaswildfirerisk.com/server/rest/services/TxWRAP/"
           "characteristic_fire_intensity_scale_img/ImageServer")
SOURCE_NAME = "Texas A&M Forest Service / TxWRAP (WildEST)"
STATISTIC = "weather_weighted_mean_heading_fireline_intensity"
NO_PROBABILITY = ("This source provides weather-weighted fireline intensity, not annual "
                  "exceedance probabilities. Trigger probabilities and annual pool "
                  "contributions are unavailable for this hazard.")
REFERENCES = [
    {"title": "TxWRAP native fireline-intensity dataset and methodology", "url": SERVICE},
    {"title": "Federal fireline-intensity definition and kW/m units",
     "url": "https://iftdss.firenet.gov/firenetHelp/help/pageHelp/content/20-models/lfb/out/firelintensitylfb.htm"},
]


def now_bucket(seconds):
    return int(datetime.now(timezone.utc).timestamp() // seconds)


@lru_cache(maxsize=4)
def source_metadata(day):
    doc, digest = fetch_json(SERVICE + "?f=json")
    description = re.sub(r"<[^>]+>", " ", doc.get("description", "")).lower()
    # Refuse a styled/classified replacement or an undocumented change of units.
    if (doc.get("error") or doc.get("pixelType") != "F32" or doc.get("bandCount") != 1
            or "kilowatts per meter" not in description
            or "weighted-average fireline intensity" not in description
            or "heading-only" not in description):
        raise ModelUnavailable("TxWRAP no longer advertises the expected native kW/m intensity raster.")
    return doc, digest


@lru_cache(maxsize=2048)
def source_sample(lat, lon, bucket):
    params = {"f": "json", "geometryType": "esriGeometryPoint",
              "geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
              "returnFirstValueOnly": "true", "interpolation": "RSP_NearestNeighbor",
              "renderingRule": json.dumps({"rasterFunction": "None"})}
    url = SERVICE + "/getSamples?" + urlencode(params)
    doc, digest = fetch_json(url)
    if doc.get("error"):
        raise ModelUnavailable("TxWRAP could not sample the native fireline-intensity raster.")
    samples = doc.get("samples")
    if not isinstance(samples, list):
        raise ModelUnavailable("Unexpected TxWRAP sample response; no estimate substituted.")
    value = None
    if samples:
        if len(samples) != 1 or not isinstance(samples[0], dict):
            raise ModelUnavailable("Unexpected TxWRAP point sample count.")
        raw = samples[0].get("value")
        if raw is not None and str(raw).strip().lower() not in {"", "nodata", "no data", "nan"}:
            try:
                value = float(raw) if not isinstance(raw, bool) else -1
            except (ValueError, TypeError):
                raise ModelUnavailable("Invalid TxWRAP intensity value.") from None
            if not math.isfinite(value) or value < 0:
                raise ModelUnavailable("Invalid TxWRAP intensity value.")
    return value, {"sample_url": url, "sample_sha256": digest,
                   "retrieved_at": datetime.now(timezone.utc).isoformat()}


def fireline_intensity(lat, lon):
    coordinates(lat, lon)
    metadata, digest = source_metadata(now_bucket(86400))
    value, sample_provenance = source_sample(lat, lon, now_bucket(600))
    result = {"peril": "fireline_intensity", "metric": "Fireline intensity", "unit": "kW/m",
              "hazard_kind": "scenario_intensity", "status": "available" if value is not None else "unavailable",
              "source_name": SOURCE_NAME, "source_url": SERVICE, "source_references": REFERENCES,
              "points": [], "frequency_label": "Annual exceedance probability unavailable",
              "annual_probability_available": False, "trigger_unavailable_reason": NO_PROBABILITY,
              "hazard_value": {"value": value, "unit": "kW/m", "statistic": STATISTIC},
              "provenance": {**sample_provenance, "metadata_url": SERVICE + "?f=json",
                             "metadata_sha256": digest,
                             "service_pixel_size": {"x": metadata.get("pixelSizeX"),
                                                    "y": metadata.get("pixelSizeY"),
                                                    "crs": metadata.get("extent", {}).get("spatialReference")},
                             "model_version": "Service does not publish an immutable model version; response hashes retained."},
              "details": [["Metric", "Heat release rate per unit fire-front length (kW/m)"],
                          ["Statistic", "Weather-weighted mean; heading-only modeled fire behavior"],
                          ["Coverage", "Texas assessment raster; missing cells remain unavailable"],
                          ["Sampling", "Nearest native raster pixel; no rendering or flame-length conversion"],
                          ["Annual probability", "Not supplied by this source"]],
              "notes": [NO_PROBABILITY,
                        "WildEST weights deterministic fire-behavior simulations by weather-type probabilities, including temporal frequencies and high-spread conditions. These weights are not annual wildfire occurrence probabilities.",
                        "Modeled potential fire behavior, not a live reading, a forecast, or heat flux received by a building. Embers and building vulnerability are separate.",
                        "A native zero is retained; it does not establish no fire, structure-to-structure, or ember exposure. NoData is never converted to zero.",
                        "Live service values can change. Sample retrieval time and metadata/sample SHA256 hashes accompany this result."]}
    if value is None:
        result["reason"] = "No native TxWRAP fireline-intensity value at this point (Texas coverage or NoData)."
    return result


def aggregate_intensity(samples, area):
    """Equal-weight spatial mean of native intensities, with explicit coverage."""
    usable = []
    for sample in samples:
        curve = sample["curve"]
        value = curve.get("hazard_value", {}).get("value")
        if (curve.get("status") == "available" and curve.get("unit") == "kW/m"
                and curve.get("hazard_value", {}).get("statistic") == STATISTIC
                and isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0):
            usable.append(sample)
    area.update(available_samples=len(usable), quantity="fireline_intensity_kw_per_m",
                method="Equal-weight arithmetic mean of available native fireline intensities across a 5×5 grid including edges and center. No interpolation. Missing samples excluded; native zero values included.")
    area["samples"] = [{"latitude": s["latitude"], "longitude": s["longitude"],
                        "status": "available" if s in usable else "unavailable",
                        "value": s["curve"].get("hazard_value", {}).get("value") if s in usable else None,
                        "unit": "kW/m", "provenance": s["curve"].get("provenance"),
                        "reason": s["curve"].get("reason")} for s in samples]
    reference = (usable[0] if usable else samples[0])["curve"]
    values = [s["curve"]["hazard_value"]["value"] for s in usable]
    area["summary"] = {"minimum": min(values) if values else None,
                       "maximum": max(values) if values else None,
                       "sum": math.fsum(values), "missing_samples": len(samples) - len(values)}
    return {**reference, "peril": "fireline_intensity", "metric": "Fireline intensity", "unit": "kW/m",
            "hazard_kind": "scenario_intensity", "annual_probability_available": False,
            "trigger_unavailable_reason": NO_PROBABILITY,
            "status": "available" if values else "unavailable", "points": [],
            "latitude": area["centroid"]["latitude"], "longitude": area["centroid"]["longitude"],
            "hazard_value": {"value": math.fsum(values) / len(values) if values else None,
                             "unit": "kW/m", "statistic": "spatial_mean_of_" + STATISTIC},
            "area": area, "source_name": SOURCE_NAME, "source_url": SERVICE,
            "source_references": REFERENCES,
            "provenance": {"sample_provenance_location": "area.samples[].provenance"},
            "details": [["Statistic", "Spatial mean of weather-weighted heading fireline intensity"],
                        ["Grid coverage", f"{len(values)}/{len(samples)} samples"],
                        ["Annual probability", "Not supplied by this source"]],
            "notes": [*reference.get("notes", [NO_PROBABILITY]), area["method"],
                      "This is a grid-point spatial mean of intensity, not an exceedance probability, an area-wide maximum, or an exhaustive raster-cell average."],
            **({"reason": "No native fireline-intensity values in the selected area's 25 samples."} if not values else {})}
