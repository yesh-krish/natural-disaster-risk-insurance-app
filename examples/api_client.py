"""Standard-library example: retrieve and export NDApp trigger comparisons.

Run the server first, then:
  python examples/api_client.py --peril flood --payout 50000
  python examples/api_client.py --address "100 Dolorosa St, San Antonio, TX"
  python examples/api_client.py --output comparison.json --csv comparison.csv
"""
import argparse
import csv
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
import os
from urllib.request import Request, urlopen


def fetch_comparison(base_url, peril, lat, lon, payout, site_class="BC", address=None, api_key=None):
    location = {"address": address} if address is not None else {"lat": lat, "lon": lon}
    query = urlencode({**location, "payout_usd": payout, "site_class": site_class})
    url = f"{base_url.rstrip('/')}/api/v1/trigger-comparisons/{peril}?{query}"
    headers = {"Accept": "application/json", **({"X-API-Key": api_key} if api_key else {})}
    try:
        with urlopen(Request(url, headers=headers), timeout=90) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            error = json.load(exc)["error"]
            message = error["message"]
            candidates = error.get("details", {}).get("candidates", [])
            if candidates:
                message += " Candidates: " + "; ".join(f"{item['matched_address']} ({item['latitude']}, {item['longitude']})" for item in candidates)
        except (ValueError, KeyError, TypeError):
            message = exc.reason
        raise RuntimeError(f"HTTP {exc.code}: {message}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not reach NDApp: {exc}. Start server.py and check --base-url.") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=os.environ.get("NDAPP_API_KEY"),
                        help="Key for a shared server (or set NDAPP_API_KEY); not needed for a local copy")
    parser.add_argument("--peril", default="flood", choices=["flood", "earthquake", "wildfire", "cyclone", "rainfall", "heat", "auto"])
    parser.add_argument("--address", help="Full US street address, city and state, enclosed in quotes; accepts extra spaces")
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lon", type=float)
    parser.add_argument("--payout", default="50000", help="Fixed payout in USD")
    parser.add_argument("--site-class", default="BC", choices=["A", "B", "BC", "C", "CD", "D", "DE", "E"])
    parser.add_argument("--output", type=Path, help="Save full JSON including inputs, provenance and assumptions")
    parser.add_argument("--csv", type=Path, help="Save comparison rows as CSV")
    args = parser.parse_args(argv)
    if args.address is not None and (args.lat is not None or args.lon is not None):
        parser.error("Use --address or --lat with --lon, not both.")
    if args.address is not None and not args.address.strip():
        parser.error("--address cannot be blank.")
    if (args.lat is None) != (args.lon is None):
        parser.error("Supply both --lat and --lon.")
    if args.address is None and args.lat is None:
        args.lat, args.lon = 29.39741277, -98.47084089
    try:
        result = fetch_comparison(args.base_url, args.peril, args.lat, args.lon, args.payout, args.site_class,
                                  address=args.address, api_key=args.api_key)
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        data = result["data"]
        print(f"API {result['api_version']} | {result['status']}")
        print(f"Request: {result['meta']['request']}")
        location = result["meta"].get("location")
        if location:
            print(f"Address used: {location['normalized_address']}")
            print(f"Matched address: {location['matched_address']}")
            print(f"Coordinates: {location['latitude']:.7f}, {location['longitude']:.7f}")
            print(location["note"])
        if result["status"] != "available":
            print(data.get("reason", "No supported data; no contribution estimate was substituted."))
            return 0
        print(f"Source: {data.get('source_name', 'See provider details')} | {data['frequency_basis']}")
        print(f"{'Threshold':<24} {'Annual exceedance (%)':<26} Contribution (USD/year)")
        rows = []
        for row in data["comparisons"]:
            frequency, contribution = row["frequency_pct"], row["expected_annual_contribution_usd"]
            def display(bounds):
                low, high = bounds["minimum"], bounds["maximum"]
                return f"{low:,.6g}" if low == high else f"{low:,.6g}–{high:,.6g}"
            threshold = f">= {row['severity']:.6g} {data['unit']}"
            print(f"{threshold:<24} {display(frequency):<26} {display(contribution)}")
            resolved = result["meta"]["request"]
            rows.append([data["peril"], resolved["lat"], resolved["lon"], resolved["site_class"], row["severity"], data["unit"],
                         frequency["minimum"], frequency["maximum"], data["payout_usd"],
                         contribution["minimum"], contribution["maximum"], data["frequency_basis"], data.get("source_name", ""),
                         location["normalized_address"] if location else "", location["matched_address"] if location else ""])
        if args.csv:
            with args.csv.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["peril", "latitude", "longitude", "site_class", "severity", "unit", "minimum_frequency_pct",
                                 "maximum_frequency_pct", "payout_usd", "minimum_contribution_usd", "maximum_contribution_usd", "frequency_basis", "source_name", "input_address", "matched_address"])
                writer.writerows(rows)
        print("Alternative trigger choices; contributions are not added together. One payout/year; fees excluded.")
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
