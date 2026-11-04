"""Live HTTP smoke check; run against server.py (requires Census/NOAA access)."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("validation/monitoring-stations-live.json"))
    args = parser.parse_args()

    def lookup(**query):
        url = args.base_url.rstrip("/") + "/api/v1/monitoring-stations?" + urlencode(query)
        try:
            with urlopen(url, timeout=40) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    status, payload = lookup(address="100 Dolorosa St, San Antonio, TX", n=5)
    assert status == 200, payload
    rows = payload["data"]["stations"]
    assert len(rows) == 5
    assert len({row["id"] for row in rows}) == 5
    assert [r["distance_km"] for r in rows] == sorted(r["distance_km"] for r in rows)
    assert all(0 <= r["distance_km"] <= 200 and r["measured_variables"] for r in rows)
    assert payload["meta"]["location"]["status"] == "matched"
    links = []
    for row in rows:
        request = Request(row["url"], headers={"User-Agent": "NDApp/0.2 link verification"})
        with urlopen(request, timeout=15) as response:
            assert response.status == 200
            links.append({"id": row["id"], "url": response.url, "status": response.status})
    lat, lon = payload["meta"]["request"]["lat"], payload["meta"]["request"]["lon"]
    edges = []
    for n in ["0", "-1", "1.5", "51", "NaN"]:
        status, result = lookup(lat=lat, lon=lon, n=n)
        assert status == 400, result
        edges.append({"case": "invalid n=" + n, "http_status": status})
    status, result = lookup(lat="nan", lon=lon, n=5)
    assert status == 400, result
    edges.append({"case": "nonfinite coordinates", "http_status": status})
    status, result = lookup(lat=lat, n=5)
    assert status == 400, result
    edges.append({"case": "missing longitude", "http_status": status})
    status, result = lookup(lat=0, lon=-140, n=5)
    assert status == 200 and result["data"]["status"] == "not_found", result
    edges.append({"case": "no stations within 200 km at 0,-140", "http_status": status, "data": result["data"]})
    status, partial = lookup(lat=lat, lon=lon, n=50)
    assert status == 200 and partial["data"]["returned_count"] <= 50, partial
    assert partial["data"]["fewer_than_requested"] == (partial["data"]["returned_count"] < 50)
    edges.append({"case": "request 50", "http_status": status, "returned_count": partial["data"]["returned_count"],
                  "fewer_than_requested": partial["data"]["fewer_than_requested"]})
    status, result = lookup(address="999999999 Imaginary Avenue, San Antonio, TX 78205", n=5)
    assert status == 422 and result["error"]["code"] == "address_not_found", result
    edges.append({"case": "unmatched real-service address", "http_status": status, "error": result["error"]})
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "response": payload,
              "station_links": links, "edge_cases": edges}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("Matched:", payload["meta"]["location"]["matched_address"])
    for row in rows:
        print(f"{row['id']}: {row['distance_km']:.3f} km; observations {row['observation']['status']}")
    print(f"Verified {len(links)} data links and {len(edges)} edge cases. Saved {args.output}")


if __name__ == "__main__":
    main()
