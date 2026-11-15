"""Choose a useful local curve using FEMA county context, never unlike units."""
from functools import lru_cache
import math
from urllib.parse import urlencode

from disaster_data import disaster_at, today_key
from hazard_engine import ModelUnavailable, coordinates
from hazard_providers import fetch_json

NRI_LAYER = "https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Counties/FeatureServer/0"
NRI_FIELDS = ("COUNTY", "STATE", "STCOFIPS", "NRI_VER", "ERQK_EALT", "HRCN_EALT",
              "IFLD_EALT", "CFLD_EALT")
# Wildfire flame length (FSim) was removed from the map on 2026-11-10, so automatic
# selection no longer chooses it. Wildfire evidence is in the nearby-fire views instead.
NAMES = {"flood": "Flood", "earthquake": "Earthquake", "cyclone": "Wind (tropical cyclones)",
         "rainfall": "Rainfall"}
FIELDS = {"flood": ("IFLD_EALT", "CFLD_EALT"), "cyclone": ("HRCN_EALT",), "earthquake": ("ERQK_EALT",)}


def valid_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


@lru_cache(maxsize=128)
def county_at(lat, lon, day):
    params = urlencode({"f": "json", "where": "1=1", "geometry": f"{lon},{lat}",
                        "geometryType": "esriGeometryPoint", "inSR": 4326,
                        "spatialRel": "esriSpatialRelIntersects", "outFields": ",".join(NRI_FIELDS),
                        "returnGeometry": "false"})
    data, digest = fetch_json(NRI_LAYER + "/query?" + params)
    if data.get("error") or len(data.get("features", [])) != 1:
        raise ModelUnavailable("FEMA county ranking is unavailable or the point crosses a county boundary.")
    attributes = data["features"][0].get("attributes")
    if not isinstance(attributes, dict):
        raise ModelUnavailable("FEMA returned no county hazard attributes.")
    return attributes, digest


def rank_county(attributes):
    """EAL totals share dollar units. Null/sentinel values remain unranked."""
    rankings = []
    for peril, fields in FIELDS.items():
        known = {field: attributes.get(field) for field in fields if valid_number(attributes.get(field))}
        rankings.append({"peril": peril, "annual_loss_total": sum(known.values()) if known else None,
                         "components": known})
    return sorted(rankings, key=lambda r: -(r["annual_loss_total"] if r["annual_loss_total"] is not None else -1))


def usable_curve(data):
    # Auto mode avoids entirely blank/zero-frequency graphs. Manual mode keeps source zeros.
    return data.get("status") == "available" and any(
        valid_number(p.get("frequency_pct")) and p["frequency_pct"] > 0
        and valid_number(p.get("severity")) and p["severity"] > 0
        for p in data.get("points", []))


def most_relevant(lat, lon, site_class="BC"):
    coordinates(lat, lon)
    if site_class not in {"A", "B", "BC", "C", "CD", "D", "DE", "E"}:
        raise ValueError("Unknown site class")
    selection = {"mode": "most_relevant", "method": "fema_county_expected_annual_loss",
                 "source_url": NRI_LAYER, "county": None, "rankings": [], "checks": [],
                 "notes": ["County-level expected annual loss is regional context, not property risk or a premium. Only supported disasters with usable local curves are considered.",
                           "A missing flood curve can mean missing coverage or NoData; it does not establish low flood risk.",
                           "FEMA NRI has no standalone rainfall category. Rainfall can be shown as related inland-flood context or as an available-data fallback."]}
    attributes = {}
    try:
        attributes, digest = county_at(lat, lon, today_key())
        selection["county"] = {"name": attributes.get("COUNTY"), "state": attributes.get("STATE"),
                               "fips": attributes.get("STCOFIPS"), "model_version": attributes.get("NRI_VER")}
        selection["artifact_sha256"] = digest
        selection["rankings"] = rank_county(attributes)
    except (ModelUnavailable, OSError, ValueError, KeyError, TypeError) as exc:
        selection["ranking_unavailable_reason"] = str(exc)

    checked = {}

    def check(peril):
        if peril not in checked:
            try:
                data = disaster_at(peril, lat, lon, site_class)
            except (ModelUnavailable, OSError, ValueError, KeyError, TypeError) as exc:
                data = {"peril": peril, "status": "unavailable", "points": [], "reason": str(exc)}
            checked[peril] = data
            selection["checks"].append({"peril": peril, "usable": usable_curve(data),
                                        "status": data.get("status"),
                                        "reason": data.get("reason") or ("Local curve available." if usable_curve(data)
                                                    else "No positive modeled/observed exceedance points at supported thresholds.")})
        return checked[peril]

    def choose(data, method, explanation):
        selection.update(chosen_peril=data["peril"], method=method, explanation=explanation)
        return {**data, "selection": selection}

    county = selection["county"]
    area = f"{county['name']}, {county['state']}" if county else "this area"
    for rank in selection["rankings"]:
        if not valid_number(rank["annual_loss_total"]) or rank["annual_loss_total"] == 0:
            continue
        peril = rank["peril"]
        data = check(peril)
        if usable_curve(data):
            explanation = f"{NAMES[peril]} is the highest-ranked supported disaster with a usable local curve, based on FEMA's county expected annual loss for {area}."
            if any(not c["usable"] for c in selection["checks"]):
                explanation += " Higher-ranked choices lacked a usable curve at this coordinate."
            return choose(data, "fema_county_expected_annual_loss", explanation)
        inland, coastal = attributes.get("IFLD_EALT"), attributes.get("CFLD_EALT")
        if peril == "flood" and valid_number(inland) and inland > 0 and (not valid_number(coastal) or inland >= coastal):
            rain = check("rainfall")
            if usable_curve(rain):
                return choose(rain, "inland_flood_rainfall_context",
                              f"Flooding is the highest remaining county-ranked choice for {area}, mainly from inland flooding, but no usable flood-depth curve is available at this coordinate. Showing Rainfall as related context; rainfall depth does not measure local inundation.")

    # No county inference is claimed when ranking/data cannot support one.
    for peril in ("rainfall", "earthquake", "cyclone", "flood"):
        data = check(peril)
        if usable_curve(data):
            return choose(data, "available_data_fallback",
                          f"A reliable regional selection with a usable local curve was unavailable. Showing {NAMES[peril]} because government curve data is available here; this is a data-availability fallback.")
    selection.update(method="unavailable", explanation="No supported government curve with positive severity and frequency is available at this coordinate.")
    return {"peril": "auto", "status": "unavailable", "reason": selection["explanation"],
            "points": [], "latitude": lat, "longitude": lon, "selection": selection}
