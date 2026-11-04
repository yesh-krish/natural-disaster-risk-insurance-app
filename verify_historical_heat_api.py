"""Live historical-heat HTTP/data-contract verification (requires NOAA access)."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from jsonschema import Draft202012Validator, FormatChecker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("validation/historical-heat-live.json"))
    args = parser.parse_args()

    def get(path):
        try:
            with urlopen(args.base_url.rstrip("/") + path, timeout=150) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    status, spec = get("/api/v1/openapi.json")
    assert status == 200
    status, payload = get("/api/v1/hazards/heat?lat=29.4241&lon=-98.4936")
    assert status == 200 and payload["data"]["status"] == "available", payload
    schema = spec["paths"]["/api/v1/hazards/{peril}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    Draft202012Validator({**schema, "components": spec["components"]}, format_checker=FormatChecker()).validate(payload)
    data = payload["data"]
    complete = [row for row in data["annual_maxima"] if row["complete"]]
    assert len(complete) == data["baseline"]["complete_years"] >= 20
    assert all(row["valid_days"] == row["expected_days"] for row in complete)
    for point in data["points"]:
        hits = sum(row["maximum_c"] >= point["severity"] for row in complete)
        assert point["hit_years"] == hits and point["exposure_years"] == len(complete)
        assert abs(point["frequency_pct"] - 100*hits/len(complete)) < 1e-9
    assert data["unit"] == "°C" and data["frequency_basis"] == "historical_annual_frequency"
    status, legacy = get("/api/disaster?peril=heat&lat=29.4241&lon=-98.4936")
    assert status == 200 and legacy["points"] == data["points"]
    status, comparison = get("/api/v1/trigger-comparisons/heat?lat=29.4241&lon=-98.4936&payout_usd=30000")
    assert status == 200 and comparison["data"]["frequency_basis"] == "historical_annual_frequency"
    for row in comparison["data"]["comparisons"]:
        expected = (Decimal(str(row["frequency_pct"]["minimum"]))*300).quantize(Decimal("0.01"))
        assert abs(float(expected) - row["expected_annual_contribution_usd"]["minimum"]) < .001
    status, area = get("/api/v1/areas/heat?south=29.4&west=-98.5&north=29.5&east=-98.4")
    assert status == 200 and area["data"]["status"] == "unavailable" and not area["data"]["points"]
    status, uncovered = get("/api/v1/hazards/heat?lat=0&lon=-140")
    assert status == 200 and uncovered["data"]["status"] == "unavailable" and not uncovered["data"]["points"]
    status, invalid = get("/api/v1/hazards/heat?lat=nan&lon=0")
    assert status == 400
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "response": payload,
              "comparison": comparison, "unsupported_area": area, "uncovered_point": uncovered,
              "invalid_input_status": status}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(data["station"]["name"], data["station"]["id"], f"{data['station']['distance_km']:.2f} km")
    print(len(complete), "complete years;", len(data["points"]), "observed thresholds")
    for row in data["station_selection"]["checked"]:
        print("Station checked:", row["id"], "complete years:", row["complete_years"], "eligible:", row["eligible"])
    print("Live point, legacy API, historical contributions, coverage, area boundary and schema checks passed; saved", args.output)


if __name__ == "__main__":
    main()
