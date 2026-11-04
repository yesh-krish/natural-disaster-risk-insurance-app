"""Live data-flow verification for native Texas fireline intensity (not calibration)."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


def verify(base_url):
    records = {}

    def get(name, path, **query):
        url = base_url.rstrip("/") + path + "?" + urlencode(query)
        with urlopen(url, timeout=90) as response:
            assert response.status == 200
            data = json.load(response)
        records[name] = {"url": url, "response": data}
        return data

    positive = get("positive_point", "/api/v1/hazards/fireline_intensity", lat=29.62, lon=-98.6)
    data = positive["data"]
    assert positive["status"] == "available"
    assert data["unit"] == "kW/m" and data["hazard_value"]["value"] > 0
    assert not data["annual_probability_available"] and data["points"] == []
    assert len(data["provenance"]["sample_sha256"]) == 64
    zero = get("native_zero", "/api/v1/hazards/fireline_intensity", lat=29.4241, lon=-98.4936)
    assert zero["status"] == "available" and zero["data"]["hazard_value"]["value"] == 0
    missing = get("outside_coverage", "/api/v1/hazards/fireline_intensity", lat=40.7, lon=-74)
    assert missing["status"] == "unavailable" and missing["data"]["hazard_value"]["value"] is None
    comparison = get("no_invented_probability", "/api/v1/trigger-comparisons/fireline_intensity",
                     lat=29.62, lon=-98.6, payout_usd=50000)
    assert comparison["status"] == "unavailable" and comparison["data"]["comparisons"] == []
    assert comparison["data"]["frequency_basis"] == "unavailable"
    area = get("area", "/api/v1/areas/fireline_intensity", south=29.619, west=-98.601,
               north=29.621, east=-98.599)["data"]
    values = [s["value"] for s in area["area"]["samples"] if s["value"] is not None]
    assert len(area["area"]["samples"]) == 25 and values
    assert area["area"]["available_samples"] == len(values)
    assert math.isclose(area["hazard_value"]["value"], math.fsum(values) / len(values))
    assert area["points"] == [] and not area["annual_probability_available"]
    legacy = get("legacy_map_route", "/api/disaster", peril="fireline_intensity", lat=29.62, lon=-98.6)
    assert legacy["hazard_value"] == data["hazard_value"]
    return {"verified_at": datetime.now(timezone.utc).isoformat(),
            "scope": "Live data flow and source/API contracts; not observed-fire or damage calibration.",
            "checks_passed": ["native positive value", "native zero", "NoData outside Texas",
                              "probability and contribution unavailable", "25-point mean", "legacy map route"],
            "records": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("validation/fireline-intensity-live.json"))
    args = parser.parse_args()
    report = verify(args.base_url)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("Passed: " + "; ".join(report["checks_passed"]))
    print(f"Saved {args.output}")
