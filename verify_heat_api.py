"""Read-only live HTTP smoke check for the additive station heat snapshot."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from jsonschema import Draft202012Validator, FormatChecker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("validation/heat-stations-live.json"))
    args = parser.parse_args()

    def get(path):
        try:
            with urlopen(args.base_url.rstrip("/") + path, timeout=35) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    status, health = get("/api/v1/health")
    assert status == 200, health
    status, spec = get("/api/v1/openapi.json")
    assert status == 200
    status, payload = get("/api/v1/monitoring-stations?lat=29.4241&lon=-98.4936&n=3")
    assert status == 200, payload
    schema = spec["paths"]["/api/v1/monitoring-stations"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    Draft202012Validator({**schema, "components": spec["components"]}, format_checker=FormatChecker()).validate(payload)
    stations = payload["data"]["stations"]
    assert stations and len(stations) <= 3
    assert [r["distance_km"] for r in stations] == sorted(r["distance_km"] for r in stations)
    for station in stations:
        observation = station["observation"]
        heat = observation["heat"]
        assert heat["data_kind"] == "observation_derived"
        assert heat["observed_at"] == observation.get("observed_at")
        if heat["status"] == "available":
            assert observation["status"] == "available"
            assert 0 <= observation["age_minutes"] <= 120
            assert abs(heat["heat_index_c"] - (heat["heat_index_f"]-32)*5/9) < 1e-9
            assert heat["category"] and heat["source_urls"]
        else:
            assert heat["heat_index_f"] is None and heat["category"] is None
        print(station["id"], observation.get("observed_at"), heat["status"], heat["reason"])
    edges = []
    for query in ["lat=nan&lon=0&n=3", "lat=29.4&lon=-98.5&n=0"]:
        status, response = get("/api/v1/monitoring-stations?" + query)
        assert status == 400, response
        edges.append({"query": query, "status": status})
    status, empty = get("/api/v1/monitoring-stations?lat=0&lon=-140&n=3")
    assert status == 200 and empty["data"]["status"] == "not_found" and not empty["data"]["stations"], empty
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "response": payload,
              "invalid_inputs": edges, "no_station_response": empty}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("Live HTTP, schema, units, UTC timestamps, missing stations and invalid inputs passed; saved", args.output)


if __name__ == "__main__":
    main()
