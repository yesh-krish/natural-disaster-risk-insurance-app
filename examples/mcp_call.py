"""Talk to the NDApp MCP server directly: list its tools or call one.

    .venv\\Scripts\\python.exe examples\\mcp_call.py --list
    .venv\\Scripts\\python.exe examples\\mcp_call.py get_flood_zone_context lat=29.4242 lon=-98.4933
    .venv\\Scripts\\python.exe examples\\mcp_call.py get_hazard_curve peril=earthquake lat=29.4242 lon=-98.4933 site_class=D

The script starts ndapp_mcp_server.py as a separate process and exchanges real MCP
messages with it (tools/list, tools/call), exactly as an AI host would.
Arguments are name=value; numbers are converted automatically.
"""
import argparse
import json
from pathlib import Path
import sys

import anyio
from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent


def parse_value(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


async def main(args):
    server = StdioServerParameters(command=sys.executable, args=[str(ROOT / "ndapp_mcp_server.py")], cwd=str(ROOT))
    async with Client(server, read_timeout_seconds=60) as client:
        if args.list or not args.tool:
            for tool in (await client.list_tools()).tools:
                inputs = ", ".join((tool.input_schema or {}).get("properties", {}))
                print(f"{tool.name}({inputs})\n    {tool.description}\n")
            return 0
        arguments = {}
        for item in args.arguments:
            name, sep, value = item.partition("=")
            if not sep:
                raise SystemExit(f"Arguments must be name=value, got {item!r}")
            arguments[name] = parse_value(value)
        result = await client.call_tool(args.tool, arguments)
        payload = result.structured_content
        if payload is None:
            payload = [getattr(block, "text", str(block)) for block in result.content]
        print(json.dumps(payload, indent=2, default=str))
        return 1 if result.is_error else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="List the server's tools.")
    parser.add_argument("tool", nargs="?")
    parser.add_argument("arguments", nargs="*", help="name=value pairs")
    raise SystemExit(anyio.run(main, parser.parse_args()))
