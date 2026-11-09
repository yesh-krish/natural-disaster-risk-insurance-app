"""Validated tool-handler prototypes over the real API dispatcher; no MCP transport.

Requires requirements-dev.txt (jsonschema). Use --list or --tool NAME --lat Y --lon X.
"""
import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from api_spec import openapi_document
from api_v1 import dispatch, error_response

TOOLS = {
    "get_active_weather_alerts": ("/api/v1/weather-alerts", "WeatherAlerts",
        "Read current actual NWS alerts for a WGS84 point. Preserves agency instructions and times. Empty results do not establish safety or long-term hazard probability."),
    "get_flood_zone_context": ("/api/v1/flood-zone-context", "FloodZoneContext",
        "Read all intersecting FEMA NFHL flood-zone polygons for a WGS84 point. Includes map attributes, units and datum. Not a depth curve or property loss model; missing coverage remains unknown."),
}


def tool_definitions():
    spec = openapi_document()
    tools = []
    for name, (_, schema, description) in TOOLS.items():
        output = {"type": "object", "required": ["api_version", "status", "data", "meta"],
                  "properties": {"api_version": {"const": spec["info"]["version"]},
                      "status": {"enum": ["available", "not_found", "unavailable"]},
                      "data": {"$ref": f"#/components/schemas/{schema}"},
                      "meta": {"$ref": "#/components/schemas/Meta"}}}
        tools.append({"name": name, "description": description,
                      "inputSchema": {"type": "object", "additionalProperties": False,
                          "required": ["lat", "lon"], "properties": {
                              "lat": {"type": "number", "minimum": -90, "maximum": 90},
                              "lon": {"type": "number", "minimum": -180, "maximum": 180}}},
                      "outputSchema": {"oneOf": [output, {"$ref": "#/components/schemas/Error"}],
                                       "components": spec["components"]},
                      "annotations": {"readOnlyHint": True, "destructiveHint": False,
                                      "idempotentHint": True, "openWorldHint": True}})
    return tools


def call_tool(name, arguments):
    definition = next((item for item in tool_definitions() if item["name"] == name), None)
    if definition is None:
        raise ValueError("Unknown tool; the MCP transport must map this to a protocol error.")
    try:
        # Disallow non-JSON NaN/Infinity before schema validation or provider I/O.
        json.dumps(arguments, allow_nan=False)
        Draft202012Validator(definition["inputSchema"]).validate(arguments)
    except (ValidationError, ValueError, TypeError):
        code, payload = error_response(400, "invalid_request", "Supply finite numeric lat and lon only, within WGS84 ranges.")
    else:
        with redirect_stdout(sys.stderr):
            code, payload = dispatch(TOOLS[name][0], urlencode(arguments))
        try:
            Draft202012Validator(definition["outputSchema"], format_checker=FormatChecker()).validate(payload)
            json.dumps(payload, allow_nan=False)
        except (ValidationError, ValueError, TypeError):
            code, payload = error_response(502, "provider_unavailable", "The tool returned invalid evidence; no result was substituted.")
    return {"resultType": "complete", "isError": code >= 400, "structuredContent": payload,
            "content": [{"type": "text", "text": json.dumps(payload, allow_nan=False)}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true")
    action.add_argument("--tool", choices=list(TOOLS))
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lon", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.tool and (args.lat is None or args.lon is None):
        parser.error("--tool requires --lat and --lon")
    result = {"tools": tool_definitions()} if args.list else call_tool(args.tool, {"lat": args.lat, "lon": args.lon})
    rendered = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return int(result.get("isError", False))


if __name__ == "__main__":
    raise SystemExit(main())
