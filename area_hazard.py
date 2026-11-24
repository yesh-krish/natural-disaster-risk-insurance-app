"""Average local exceedance curves over a reproducible spatial sampling grid."""
from concurrent.futures import ThreadPoolExecutor
import math

from disaster_data import disaster_at
from hazard_engine import coordinates

PERILS = {"auto", "earthquake", "flood", "wildfire", "fireline_intensity", "potential_fire_intensity", "cyclone", "rainfall", "heat", "satellite_fire"}
SITE_CLASSES = {"A", "B", "BC", "C", "CD", "D", "DE", "E"}


def validate_area(south, west, north, east, peril, site_class):
    coordinates(south, west)
    coordinates(north, east)
    if south >= north or west >= east:
        raise ValueError("Area needs ordered south/west/north/east bounds")
    if peril not in PERILS or site_class not in SITE_CLASSES:
        raise ValueError("Unknown hazard or site class")


def probability_at(points, threshold):
    """Exact source value, or log-probability interpolation within a valid bracket.

    Retain missing levels as gaps. Never extrapolate or interpolate through zero.
    Duplicate rounded intensities use the largest supplied exceedance frequency.
    """
    valid = {}
    incomplete = False
    for point in points:
        x, y = point.get("severity"), point.get("frequency_pct")
        if (isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x >= 0
                and isinstance(y, (int, float)) and not isinstance(y, bool)
                and math.isfinite(y) and 0 <= y <= 100):
            valid[x] = max(valid.get(x, -1), y)
        else:
            incomplete = True
    if threshold in valid:
        return valid[threshold], "source"
    ordered = sorted(valid.items())
    # A missing severity prevents establishing the complete source bracket.
    if incomplete:
        return None
    if any(y1 > y0 for (_, y0), (_, y1) in zip(ordered, ordered[1:])):
        return None
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:]):
        if x0 < threshold < x1 and y0 > 0 and y1 > 0:
            weight = (threshold - x0) / (x1 - x0)
            return math.exp(math.log(y0) + weight * math.log(y1 / y0)), "interpolated"
    return None


def area_disaster(south, west, north, east, peril, site_class="BC", *, provider=None):
    validate_area(south, west, north, east, peril, site_class)
    provider = provider or disaster_at
    centroid = {"latitude": (south + north) / 2, "longitude": (west + east) / 2}
    selection = None
    center_curve = None
    if peril == "auto":
        from hazard_relevance import most_relevant
        center_curve = most_relevant(centroid["latitude"], centroid["longitude"], site_class)
        peril = center_curve["peril"]
        selection = center_curve["selection"]
    area = {"bounds": {"south": south, "west": west, "north": north, "east": east},
            "centroid": centroid, "grid_size": 5, "sample_count": 25,
            "aggregation": "arithmetic_mean",
            "method": "Equal-weight arithmetic mean of available probabilities at the same severity across a 5×5 grid including edges and center; log-probability interpolation between valid source levels, no extrapolation. Missing values are excluded from the denominator.",
            "samples": []}
    if peril == "auto":
        return {**center_curve, "area": area}
    if peril == "potential_fire_intensity":
        reason = "Potential fire intensity supports Point mode. An area curve requires joint annual spatial maxima, not an average of point probabilities."
        area.update(available_samples=0, method=reason)
        return {"peril": peril, "status": "unavailable", "points": [], "area": area,
                "reason": reason, "annual_probability_available": False, "trigger_unavailable_reason": reason}
    if peril == "heat":
        reason = "Historical heat uses a qualifying station record as a point proxy. Select Point mode; an area heat curve is not available and stations are not averaged across a rectangle."
        area.update(available_samples=0, method=reason)
        return {"peril": "heat", "status": "unavailable", "points": [], "area": area,
                "reason": reason, "frequency_basis": "historical_annual_frequency"}
    if peril == "satellite_fire":
        reason = "Satellite fire exceedance counts detections within a radius of one point. Select Point mode; grid-point probabilities of overlapping circles are not averaged."
        area.update(available_samples=0, method=reason)
        return {"peril": "satellite_fire", "status": "unavailable", "points": [], "area": area,
                "reason": reason, "frequency_basis": "historical_annual_frequency"}
    locations = [(south + (north - south) * row / 4, west + (east - west) * col / 4)
                 for row in range(5) for col in range(5)]
    locations[12] = (centroid["latitude"], centroid["longitude"])

    def sample(location):
        lat, lon = location
        try:
            curve = (center_curve if center_curve is not None and (lat, lon) ==
                     (centroid["latitude"], centroid["longitude"]) else provider(peril, lat, lon, site_class))
        except (OSError, ValueError, KeyError, TypeError):
            curve = {"status": "unavailable", "points": [], "reason": "Source lookup failed"}
        return {"latitude": lat, "longitude": lon, "curve": curve}

    with ThreadPoolExecutor(max_workers=6) as executor:
        samples = list(executor.map(sample, locations))
    if peril == "fireline_intensity":
        from fireline_intensity import aggregate_intensity
        return aggregate_intensity(samples, area)
    usable = [s for s in samples if s["curve"].get("status") == "available" and
              any(probability_at(s["curve"].get("points", []), p.get("severity")) is not None
                  for p in s["curve"].get("points", []) if isinstance(p.get("severity"), (int, float)))]
    area["available_samples"] = len(usable)
    area["samples"] = [{"latitude": s["latitude"], "longitude": s["longitude"],
                        "status": s["curve"].get("status", "unavailable"),
                        "points": s["curve"].get("points", []),
                        "source_name": s["curve"].get("source_name"),
                        "source_url": s["curve"].get("source_url"),
                        "reason": s["curve"].get("reason")} for s in samples]
    if not usable:
        return {"peril": peril, "status": "unavailable", "points": [], "area": area,
                "selection": selection, "reason": "No usable hazard curves in the selected area's 25 samples."}
    reference = next((s for s in usable if s["latitude"] == centroid["latitude"] and
                      s["longitude"] == centroid["longitude"]), usable[0])["curve"]
    thresholds = sorted({p["severity"] for s in usable for p in s["curve"].get("points", [])
                         if isinstance(p.get("severity"), (int, float)) and
                         math.isfinite(p["severity"]) and p["severity"] >= 0})
    # Bound response/UI size while retaining the reference source's levels.
    if len(thresholds) > 100:
        thresholds = sorted({p["severity"] for p in reference["points"]
                             if isinstance(p.get("severity"), (int, float)) and
                             math.isfinite(p["severity"]) and p["severity"] >= 0})
    points = []
    for threshold in thresholds:
        evaluated = []
        sample_values = []
        for s in samples:
            probability = (probability_at(s["curve"].get("points", []), threshold)
                           if s["curve"].get("status") == "available" else None)
            sample_values.append({"latitude": s["latitude"], "longitude": s["longitude"],
                                  "frequency_pct": probability[0] if probability else None,
                                  "method": probability[1] if probability else "unavailable"})
            if probability is not None:
                evaluated.append((probability[0], s, probability[1]))
        if not evaluated:
            continue
        maximum = max(p[0] for p in evaluated)
        total = math.fsum(p[0] for p in evaluated)
        mean = total / len(evaluated)
        winners = [p for p in evaluated if math.isclose(p[0], maximum, rel_tol=1e-10, abs_tol=0)]
        points.append({"severity": threshold, "frequency_pct": mean,
                       "sum_frequency_pct": total, "sample_values": sample_values,
                       "minimum_frequency_pct": min(p[0] for p in evaluated),
                       "maximum_frequency_pct": maximum,
                       "evaluated_samples": len(evaluated), "missing_samples": 25 - len(evaluated),
                       "interpolated_samples": sum(p[2] == "interpolated" for p in evaluated),
                       "maximum_locations": [{"latitude": p[1]["latitude"], "longitude": p[1]["longitude"]} for p in winners],
                       "maximum_centroid": {"latitude": sum(p[1]["latitude"] for p in winners) / len(winners),
                                            "longitude": sum(p[1]["longitude"] for p in winners) / len(winners)}})
    return {**reference, "points": points, "status": "available" if points else "unavailable",
            "area": area, "selection": selection,
            "notes": [*reference.get("notes", []), area["method"],
                      "This is an unweighted mean of available grid-point probabilities, not the probability of exceedance anywhere in the area or an exhaustive raster-cell average. Missing samples do not imply zero risk.",
                      "The locations contributing to the mean may differ by threshold; compare sample counts when interpreting the averaged curve."]}
