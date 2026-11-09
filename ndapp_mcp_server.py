"""NDApp MCP server (stdio) over the reviewed tool registry in agent_tools.py.

Run:  .venv\\Scripts\\python.exe ndapp_mcp_server.py
Built on the official MCP Python SDK (mcp==2.3.0, low-level Server) so tool
input/output schemas come straight from the API's OpenAPI contract. The server
is a narrow adapter: tools are read-only, validated, and call the same
dispatcher as the HTTP API. It holds no conversation or assessment state.
"""
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import anyio
import mcp_types as types
from mcp.server import Server
from mcp.shared.exceptions import MCPError

import agent_tools

NAME, VERSION = "ndapp", "0.1.0"
INVALID_PARAMS = -32602
RESOURCES = {
    "ndapp://catalog/tools": ("Tool and evidence catalog", "application/json",
                              lambda: json.dumps(agent_tools.catalog(), indent=2)),
    "ndapp://methods/evidence-rules": ("Evidence interpretation rules", "text/markdown",
                                       lambda: agent_tools.EVIDENCE_RULES),
}
INSTRUCTIONS = ("NDApp natural-disaster evidence tools. Results are research evidence, not warnings, "
                "quotes or safety determinations. Read ndapp://methods/evidence-rules before interpreting.")


async def list_tools(ctx, params):
    return types.ListToolsResult(tools=[
        types.Tool(name=d["name"], description=d["description"], input_schema=d["inputSchema"],
                   output_schema=d["outputSchema"], annotations=types.ToolAnnotations(
                       read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
                   _meta=d["_meta"])
        for d in agent_tools.tool_definitions()])


async def call_tool(ctx, params):
    if params.name not in agent_tools.TOOLS:
        raise MCPError(INVALID_PARAMS, f"Unknown tool: {params.name}")
    # Provider reads block; run them off the event loop so concurrent calls do not stall the server.
    result = await anyio.to_thread.run_sync(agent_tools.call_tool, params.name, params.arguments or {})
    return types.CallToolResult(content=[types.TextContent(type="text", text=result["content"][0]["text"])],
                                structured_content=result["structuredContent"], is_error=result["isError"])


async def list_resources(ctx, params):
    return types.ListResourcesResult(resources=[
        types.Resource(name=title, uri=uri, mime_type=mime) for uri, (title, mime, _) in RESOURCES.items()])


async def read_resource(ctx, params):
    uri = str(params.uri)
    if uri not in RESOURCES:
        raise MCPError(INVALID_PARAMS, f"Unknown resource: {uri}")
    _, mime, body = RESOURCES[uri]
    return types.ReadResourceResult(contents=[types.TextResourceContents(uri=uri, mime_type=mime, text=body())])


def build_server():
    return Server(NAME, version=VERSION, instructions=INSTRUCTIONS,
                  on_list_tools=list_tools, on_call_tool=call_tool,
                  on_list_resources=list_resources, on_read_resource=read_resource)


async def main():
    from mcp.server.stdio import stdio_server
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
