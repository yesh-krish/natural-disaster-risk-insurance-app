r"""Read-only tool-handler prototype; no MCP transport, listener, or AI required.

Run from the project root:
  .\.venv\Scripts\python.exe examples/mcp_station_tool.py --schema
  .\.venv\Scripts\python.exe examples/mcp_station_tool.py --address "100 Dolorosa St, San Antonio, TX" --n 3

Uses the real v1 dispatcher in process. Provider caches and socket timeouts are
unchanged; an aggregate deadline and authentication belong in a future host/server.
"""
import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from address_lookup import normalize_address
from api_spec import openapi_document
from api_v1 import dispatch, error_response


def tool_definition():
    """Reuse backend output schemas rather than introduce a second data model."""
    spec = openapi_document()
    success = {"type": "object", "required": ["api_version", "status", "data", "meta"],
               "properties": {"api_version": {"const": spec["info"]["version"]},
                              "status": {"enum": ["available", "not_found"]},
                              "data": {"$ref": "#/components/schemas/NearbyStations"},
                              "meta": {"$ref": "#/components/schemas/Meta"}}}
    return {
        "name": "find_nearest_weather_stations",
        "description": "Find the nearest NOAA AWC METAR stations within 200 km of a US street address, with latest returned observations. Reports may be stale, missing or unavailable; this does not calculate hazard probabilities.",
        "inputSchema": {
            "type": "object", "additionalProperties": False, "required": ["address"],
            "properties": {
                "address": {"type": "string", "minLength": 1, "maxLength": 1000,
                            "description": "Full US street address, city and state; at most 100 characters after whitespace normalization."},
                "n": {"type": "integer", "minimum": 1, "maximum": 50, "default": 3}}},
        "outputSchema": {"oneOf": [success, {"$ref": "#/components/schemas/Error"}],
                         "components": spec["components"]},
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": True}}


def call_tool(arguments):
    """Return MCP-shaped data, not a JSON-RPC response or a deployed MCP server."""
    try:
        if not isinstance(arguments, dict) or set(arguments) - {"address", "n"}:
            raise ValueError("Supply an object with address and optional n only.")
        address = arguments.get("address")
        if not isinstance(address, str) or not 1 <= len(address) <= 1000:
            raise ValueError("Address must be text with 1 to 1000 characters before cleanup.")
        address = normalize_address(address)
        n = arguments.get("n", 3)
        if type(n) is not int or not 1 <= n <= 50:
            raise ValueError("n must be an integer from 1 to 50.")
    except ValueError as exc:
        status, payload = error_response(400, "invalid_request", str(exc))
    else:
        # Fixed route/network; never accept arbitrary URLs, paths, or executable text.
        # The dispatcher can log provider failures: keep that separate from JSON.
        with redirect_stdout(sys.stderr):
            status, payload = dispatch("/api/v1/monitoring-stations", urlencode({
                "address": address, "n": n, "network": "awc_metar"}))
    return {"resultType": "complete", "isError": status >= 400,
            "structuredContent": payload,
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=True, allow_nan=False)}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--schema", action="store_true")
    action.add_argument("--address")
    parser.add_argument("--n", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = tool_definition() if args.schema else call_tool({"address": args.address, "n": args.n})
    text = json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return int(result.get("isError", False))


if __name__ == "__main__":
    raise SystemExit(main())
