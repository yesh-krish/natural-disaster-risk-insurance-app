"""US Census address lookup with explicit normalization and match outcomes."""
import json
import re
import urllib.request
from urllib.parse import urlencode

from hazard_engine import coordinates

GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
ADDRESS_NOTE = "Approximate Census address-range point, not a surveyed property location. Check the matched address and coordinates; spelling corrections are not guaranteed."


def normalize_address(address):
    if not isinstance(address, str):
        raise ValueError("Address must be text.")
    # Collapse ordinary/Unicode whitespace without changing numbers, apartment
    # identifiers, street spelling or directional abbreviations.
    normalized = re.sub(r"\s*,\s*", ", ", " ".join(address.split())).strip(" ,")
    if not normalized or len(normalized) > 100 or any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise ValueError("Enter a full US street address, city and state (up to 100 characters after whitespace cleanup).")
    return normalized


def geocode_address(address):
    normalized = normalize_address(address)
    params = urlencode({"address": normalized, "benchmark": "Public_AR_Current", "format": "json"})
    request = urllib.request.Request(f"{GEOCODER_URL}?{params}", headers={"User-Agent": "NDApp-address-lookup/1.1"})
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.load(response)
    matches = payload["result"]["addressMatches"]
    if not isinstance(matches, list):
        raise ValueError("Invalid Census address match list")
    candidates = []
    for match in matches:
        lat, lon = float(match["coordinates"]["y"]), float(match["coordinates"]["x"])
        coordinates(lat, lon)
        candidate = {"matched_address": match["matchedAddress"], "latitude": lat, "longitude": lon}
        if not isinstance(candidate["matched_address"], str) or not candidate["matched_address"]:
            raise ValueError("Missing matched address")
        if candidate not in candidates:
            candidates.append(candidate)
    result = {"status": "matched" if len(candidates) == 1 else "ambiguous" if candidates else "no_match",
              "input_address": address, "normalized_address": normalized,
              "source_name": "US Census Geocoding Services", "source_url": GEOCODER_URL,
              "benchmark": "Public_AR_Current", "approximate": True, "note": ADDRESS_NOTE}
    if len(candidates) == 1:
        result.update(candidates[0])
    else:
        result["candidates"] = candidates[:10]
        result["candidate_count"] = len(candidates)
        result["message"] = ("More than one address matched. Add a ZIP code or apartment/unit details, or use verified coordinates."
                             if candidates else "No address matched. Check the street number, spelling, city, state and ZIP code, or use coordinates.")
    return result
