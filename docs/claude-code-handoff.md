# NDApp handoff for Claude Code

Prepared November 7, 2026. This is a project/session summary, not a verbatim
conversation export. Verify the current files and Git status before editing.

## User objective and decisions

The user wants to investigate MCP and develop an AI natural-disaster risk
assessment agent, including additional tools that improve its evidence.
The user explicitly clarified that the intended domain is natural-disaster
risk using MCP tools, rather than security assessment of MCP servers.

The latest request is how to continue this work in Claude Code. No particular
next tool, model provider or agent framework has been chosen by the user.

## Workspace and change preservation

Windows checkout:
`C:\Users\Yeshas Krishna\OneDrive\Documents\ChatGPT\NDApp`

Branch at handoff: `main`. The checkout has many modified and untracked files.
Some predate this session, including API hosting, fire-history and UI work.
The work from this session has not been committed. Use this existing working
directory to retain it; a new clone of the current remote will not include
uncommitted or untracked work. Do not reset, clean or overwrite the checkout.
No commits or deployment were performed in this session.

No AGENTS.md was found during the architecture review. Re-check for applicable
instructions when beginning work. Codex's account connections, permissions,
credentials and conversation state are not contained in this summary; configure
any needed Claude Code integrations separately.

## Read first

1. `docs/ai-risk-agent-architecture.md`: proposed host/client/server boundaries,
   bounded agent workflow, evidence records and scientific limitations.
2. `docs/risk-assessment-tools.md`: implemented tools, usage, live evidence and
   prioritized future tools.
3. `docs/mcp-evaluation.md`: earlier MCP evaluation and station-handler prototype.
4. `docs/api.md`: current API 1.7.0 contract.
5. `docs/student-api-handoff.md` and `README.md`: standalone API and app setup.

## Architecture agreed for the draft

- Agent host: conversation, planning, execution policy, evidence validation and
  explanation. Its MCP client calls reviewed tools on an NDApp MCP server.
- MCP server: narrow adapter over existing NDApp operations. It supplies
  interoperability; it does not supply the scientific models or the AI host.
- Deterministic backend: numerical hazard results, interpolation, rounding,
  unit conversions and any future validated damage calculations.
- Initial assessment type: hazard screening. Property losses additionally
  require verified exposure/asset attributes and applicable vulnerability models.
- Preserve source meanings: current observations, historical annual frequencies,
  modeled annual probabilities and potential-fire conditions are different.
- Missing coverage is unknown, not zero risk. Do not invent an overall risk
  score, infer building attributes or combine unlike hazard probabilities.
- Long-term hazard curves and current alerts/weather remain separate evidence.
- No insurance quoting, payment, settlement or emergency-warning service was built.

The initial architecture proposed flood/earthquake tools and a local stdio MCP
pilot. A real MCP transport, SDK server and model-driven AI host are still absent.
The earlier station example is also only a transport-free handler. Verify current
official protocol/SDK compatibility before building a server; do not copy an old
handshake tutorial without checking the chosen client/server versions.

## Implemented in the latest development turn

New provider module: `risk_context.py`.
Shared result schemas: `risk_context_schemas.py`.
API integration: additive changes in `api_v1.py` and `api_spec.py`, version 1.7.0.

| Agent handler | GET endpoint | Meaning |
| --- | --- | --- |
| `get_active_weather_alerts` | `/api/v1/weather-alerts` | NWS point metadata verifies coverage; actual active point alerts preserve agency text and times. Full pagination is required within bounded work limits. Expired/cancelled/ended products are excluded. |
| `get_flood_zone_context` | `/api/v1/flood-zone-context` | All intersecting FEMA NFHL layer-28 polygons, raw attributes, units and elevation datum. Missing numeric codes normalize to null. No polygon means unavailable coverage. |

API routes accept an address or coordinates. Transport-free handlers in
`examples/mcp_risk_context_tools.py` accept finite WGS84 coordinates only and
validate input/output JSON Schemas; they return structured/text results and
execution-error flags. `jsonschema` is already in `requirements-dev.txt`.

Provider reads constrain destinations/redirects, response size and socket waits.
Deadlines are cooperative between blocking reads, not strict wall-clock
cancellation. There is no application cache, automatic retry or shared pacing
for these new providers. Shared production hosting still needs auth, quotas,
private backend boundaries and host controls described in the architecture.

Docs updated: `README.md`, `docs/api.md`, architecture implementation note and
the new tool guide. New tests: `tests/test_risk_context.py`.

## Validation and commands

All 184 Python tests passed after this development turn, including 11 new
risk-context tests. The sandboxed full suite initially hung while Windows
asyncio created its internal loopback socket pair; allowing local socket access
resolved it. Do not mistake that environment issue for a provider or test failure.
Schema, document-link and diff-whitespace checks also passed.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe examples/mcp_risk_context_tools.py --list
.\.venv\Scripts\python.exe examples/mcp_risk_context_tools.py --tool get_active_weather_alerts --lat 29.4242 --lon -98.4933
.\.venv\Scripts\python.exe examples/mcp_risk_context_tools.py --tool get_flood_zone_context --lat 29.4242 --lon -98.4933
```

Live snapshots: `validation/risk-context-live.json` (San Antonio: no returned
active NWS alerts and FEMA Zone X), `validation/risk-alerts-live.json` (Seattle:
Wind Advisory). Captured November 7, 2026 around 23:15–23:17 UTC. These are
historical verification evidence, not current assessments or calibrated science.

## Claude Code session update (November 7, 2026)

Built the first architecture slice: `agent_tools.py` (reviewed tool registry),
`ndapp_mcp_server.py` (real MCP stdio server, `mcp==2.3.0`), `risk_agent.py`
(host: bounded plan, pinned tool policy, hashed evidence, fact extraction,
optional claim-checked Claude narrative), and API 1.8.0 routes for USGS
elevation, USGS recent earthquakes and the FEMA National Risk Index. Pins are in
`validation/mcp-tool-pins.json`; a live run is in `validation/agent-assessment-live.json`.
Install `requirements-agent.txt`. Tests: `tests/test_risk_agent.py`. Claude calls
are tested with a stub only (no API credentials were configured). Nothing committed.
See the "Implemented reference architecture" section of
`docs/ai-risk-agent-architecture.md` and the researched-tools table in
`docs/risk-assessment-tools.md`.

## Candidate next work, not yet implemented

The tool guide prioritizes asset-profile validation, a typed backend wrapper
around the existing browser flood-damage calculation, elevation context, recent
water conditions, area exposure and like-for-like location comparisons.
The browser's existing damage curves use pinned Hazus 4.0 RES1 coefficients;
do not silently replace them with a newer method or imply all occupancies fit.

An alternative next slice is a real local MCP SDK server around the reviewed
handlers, with discovery/call interoperability tests. Confirm the user's current
priority before treating either candidate as the selected milestone.
