"""NDApp natural-disaster assessment agent: an AI host with an MCP client.

The host owns the workflow; the MCP server (ndapp_mcp_server.py) owns tools;
the backend owns numbers. Claude is optional and only (1) turns a natural-language
request into a proposed scope and (2) explains extracted facts. Code decides which
tools run, validates every call/result, extracts facts, and rejects any model
statement whose citations or numbers do not match the evidence.

Examples:
  python risk_agent.py --lat 29.4242 --lon -98.4933 --hazards flood earthquake
  python risk_agent.py --address "100 Dolorosa St, San Antonio, TX" --hazards flood
  python risk_agent.py --ask "How exposed is 100 Dolorosa St, San Antonio TX to floods?" --narrate
  python risk_agent.py --write-pins      # review, then pin current tool definitions
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import uuid

import anyio
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_tools import definition_hash  # noqa: E402  (same hash the reviewer pins)

SCHEMA_VERSION = "ndapp.assessment/0.1"
PINS_PATH = ROOT / "validation" / "mcp-tool-pins.json"
MODEL = "claude-opus-5-5"
MAX_CALLS, CONCURRENCY, CALL_TIMEOUT_S = 10, 2, 45
SUPPORTED_HAZARDS = ("flood", "earthquake", "wildfire")
HAZARD_PLANS = {
    "flood": [("get_hazard_curve", {"peril": "flood"}), ("get_flood_zone_context", {}),
              ("get_elevation_context", {})],
    "earthquake": [("get_hazard_curve", {"peril": "earthquake"}), ("get_recent_earthquakes", {})],
    "wildfire": [("get_wildfire_history", {})],
}
CONTEXT_PLAN = [("get_active_weather_alerts", {}), ("get_community_risk_index", {})]
NRI_FOR_HAZARD = {"flood": ("inland_flooding", "coastal_flooding"), "earthquake": ("earthquake",),
                  "wildfire": ("wildfire",)}
_IMPACT = r"(?:flood(?:ed|s)?|burn(?:ed|s)?|shake|be (?:hit|damaged|destroyed|affected|flooded|burned))"
# Word-boundary patterns rather than substrings, so rewordings like "no flood risk", "relatively safe"
# or "flooding is impossible" are withheld as well as the literal phrases.
BANNED_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bno\s+(?:[\w-]+\s+){0,2}(?:risk|danger|threat|chance|possibility|likelihood|exposure)\b",
    r"\bzero\s+(?:[\w-]+\s+){0,2}(?:risk|danger|threat|chance|probability|likelihood|exposure)\b",
    r"\brisk[\s-]*free\b", r"\bsafe(?:r|st|ty|ly)?\b", r"\bimpossible\b", r"\bguarantee[ds]?\b",
    r"\b(?:will|would|can|could)(?:\s+not|n't)?\s+(?:never\s+)?" + _IMPACT + r"\b",
    r"\b(?:won't|cannot|can't|never)\s+" + _IMPACT + r"\b",
    r"\boverall\s+(?:[\w-]+\s+)?(?:risk|hazard)\s+(?:score|rating|level|grade)\b",
))


def now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- tool policy
class ToolPolicy:
    """Allowlist + pinned definition hashes. Discovery never grants permission."""

    def __init__(self, pins=None):
        self.pins = pins  # {name: sha256} or None when no review file exists
        self.allowed, self.blocked = {}, {}

    @classmethod
    def from_file(cls, path=PINS_PATH):
        try:
            return cls(json.loads(Path(path).read_text(encoding="utf-8"))["tools"])
        except (OSError, ValueError, KeyError):
            return cls(None)

    def review(self, discovered):
        needed = {name for plan in HAZARD_PLANS.values() for name, _ in plan}
        needed |= {name for name, _ in CONTEXT_PLAN} | {"resolve_location"}
        for definition in discovered:
            name = definition["name"]
            if name not in needed:
                continue  # discovered but not part of any reviewed plan: never called
            if self.pins is not None and self.pins.get(name) != definition_hash(definition):
                self.blocked[name] = "Tool definition differs from the reviewed pin (drift)."
                continue
            self.allowed[name] = definition
        for name in needed - set(self.allowed) - set(self.blocked):
            self.blocked[name] = "Tool not offered by the server."

    def check(self, name, arguments):
        if name not in self.allowed:
            raise PermissionError(self.blocked.get(name, "Tool is not allowed."))
        Draft202012Validator(self.allowed[name]["inputSchema"]).validate(arguments)


# ---------------------------------------------------------------- evidence
class EvidenceRegistry:
    def __init__(self):
        self.items = []

    def add(self, tool, arguments, result, started, error=None):
        payload = result.get("structuredContent") if result else None
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) if payload else ""
        item = {"id": f"E{len(self.items) + 1}", "server": "ndapp", "tool": tool, "arguments": arguments,
                "started_at": started, "retrieved_at": now(),
                "is_error": bool(error) or bool(result and result.get("isError")),
                "status": (payload or {}).get("status", "error"), "error": error,
                "sha256": hashlib.sha256(body.encode()).hexdigest() if body else None,
                "api_version": (payload or {}).get("api_version"), "envelope": payload}
        self.items.append(item)
        return item


def _pointer_get(document, pointer):
    for part in pointer.strip("/").split("/"):
        document = document[int(part)] if isinstance(document, list) else document[part]
    return document


# ---------------------------------------------------------------- facts (deterministic)
def _fact(facts, evidence, pointer, label, unit=None, hazard="context"):
    value = _pointer_get(evidence["envelope"], pointer)
    facts.append({"id": f"F{len(facts) + 1}", "evidence_id": evidence["id"], "pointer": pointer,
                  "label": label, "value": value, "unit": unit, "hazard": hazard,
                  "evidence_kind": KIND[evidence["tool"]]})


KIND = {"resolve_location": "location", "get_hazard_curve": "modeled_hazard_curve",
        "get_active_weather_alerts": "current_observation", "get_flood_zone_context": "regulatory_map",
        "get_elevation_context": "terrain", "get_recent_earthquakes": "historical_catalog",
        "get_community_risk_index": "community_index", "get_wildfire_history": "historical_frequency"}


def extract_facts(evidence_items, hazards):
    facts = []
    for ev in evidence_items:
        env = ev["envelope"]
        if ev["is_error"] or not env or env.get("status") not in ("available", "not_found"):
            continue
        data, tool = env["data"], ev["tool"]
        if tool == "get_hazard_curve":
            hz = data["peril"]
            _fact(facts, ev, "/data/source_name", f"{hz.capitalize()} curve source model", hazard=hz)
            for i, point in enumerate(data.get("points", [])):
                if point.get("severity") is None:
                    _fact(facts, ev, f"/data/points/{i}/frequency_pct",
                          f"Annual exceedance probability with no mapped {data['metric'].lower()}",
                          "% AEP", hz)
                else:
                    _fact(facts, ev, f"/data/points/{i}/severity",
                          f"{data['metric']} at {point['frequency_pct']:.6g}% annual exceedance probability",
                          data["unit"], hz)
        elif tool == "get_flood_zone_context":
            for i, zone in enumerate(data["zones"]):
                _fact(facts, ev, f"/data/zones/{i}/zone", "FEMA NFHL flood zone", hazard="flood")
                _fact(facts, ev, f"/data/zones/{i}/special_flood_hazard_area",
                      "Inside FEMA Special Flood Hazard Area", hazard="flood")
                if zone["static_base_flood_elevation"] is not None:
                    _fact(facts, ev, f"/data/zones/{i}/static_base_flood_elevation",
                          f"FEMA base flood elevation (datum {zone['vertical_datum']})", zone["length_unit"], "flood")
        elif tool == "get_elevation_context" and data["elevation_m"] is not None:
            _fact(facts, ev, "/data/elevation_m", "USGS 3DEP ground elevation (datum not stated)", "m", "flood")
        elif tool == "get_recent_earthquakes":
            w = data["window"]
            scope = f"within {w['radius_km']:g} km, past {w['days']} days, M{w['min_magnitude']:g}+"
            _fact(facts, ev, "/data/total_count", f"USGS catalog earthquakes {scope}", "events", "earthquake")
            if data["largest_magnitude"] is not None:
                _fact(facts, ev, "/data/largest_magnitude", f"Largest catalog magnitude {scope}", hazard="earthquake")
                _fact(facts, ev, "/data/events/0/epicentral_distance_km",
                      "Epicentral distance of the largest event", "km", "earthquake")
        elif tool == "get_wildfire_history":
            for i, row in enumerate(data["frequencies"]):
                _fact(facts, ev, f"/data/frequencies/{i}/frequency_pct",
                      f"Share of {row['total_years']} baseline years with a reported wildfire within "
                      f"{row['radius_km']} km", "% of years", "wildfire")
            size = data.get("size_exceedance")
            if size:
                for i, row in enumerate(size["threshold_frequencies"]):
                    _fact(facts, ev, f"/data/size_exceedance/threshold_frequencies/{i}/frequency_pct",
                          f"Share of {row['total_years']} baseline years with a reported wildfire of at least "
                          f"{row['min_acres']:,} acres within {row['radius_km']} km", "% of years", "wildfire")
                for c, curve in enumerate(size.get("area_exceedance", {}).get("curves", [])):
                    base = f"/data/size_exceedance/area_exceedance/curves/{c}"
                    _fact(facts, ev, f"{base}/expected_annual_reported_acres",
                          f"Average reported acres burned per year by fires within {curve['radius_km']} km", "acres/year", "wildfire")
                    for i, point in enumerate(curve["points"]):
                        if point["min_acres"] in (10, 100, 1000):
                            label = f"fire of at least {point['min_acres']:,} acres within {curve['radius_km']} km"
                            _fact(facts, ev, f"{base}/points/{i}/annual_probability_pct",
                                  f"Annual probability of a {label} at the historical rate (Poisson)", "% per year", "wildfire")
                            _fact(facts, ev, f"{base}/points/{i}/empirical_years_pct",
                                  f"Share of baseline years that had a {label}", "% of years", "wildfire")
                _fact(facts, ev, "/data/size_exceedance/footprint_reach/frequency_pct",
                      "Share of baseline years in which a reported fire's equivalent-circle footprint covered the point "
                      "(approximate geometry)", "% of years", "wildfire")
                if size["footprint_reach"]["closest_footprint_gap_km"] is not None:
                    _fact(facts, ev, "/data/size_exceedance/footprint_reach/closest_footprint_gap_km",
                          "Closest gap between the point and any reported fire's equivalent-circle footprint", "km", "wildfire")
                for i, row in enumerate(size["burned_area"]):
                    _fact(facts, ev, f"/data/size_exceedance/burned_area/{i}/mean_annual_burned_share_pct",
                          f"Mean annual share of the {row['radius_km']} km circle covered by reported fire footprints "
                          "(whole-circle average, not property burn probability)", "% per year", "wildfire")
        elif tool == "get_active_weather_alerts":
            _fact(facts, ev, "/data/alert_count", "Active NWS alerts at retrieval time", "alerts")
            for i, _ in enumerate(data["alerts"]):
                _fact(facts, ev, f"/data/alerts/{i}/event", "Active NWS alert event")
                _fact(facts, ev, f"/data/alerts/{i}/expires", "Alert expiry time")
        elif tool == "get_community_risk_index":
            for t, tract in enumerate(data["tracts"]):
                base = f"/data/tracts/{t}"
                _fact(facts, ev, f"{base}/nri_version", "FEMA NRI version")
                _fact(facts, ev, f"{base}/tract_fips", "Census tract (FIPS)")
                _fact(facts, ev, f"{base}/composite/risk_rating", "FEMA NRI composite tract rating (FEMA's index)")
                _fact(facts, ev, f"{base}/composite/social_vulnerability_rating", "FEMA NRI social vulnerability rating")
                _fact(facts, ev, f"{base}/composite/community_resilience_rating", "FEMA NRI community resilience rating")
                for hz in hazards:
                    for key in NRI_FOR_HAZARD[hz]:
                        _fact(facts, ev, f"{base}/hazards/{key}/risk_rating",
                              f"FEMA NRI {key.replace('_', ' ')} tract rating", hazard=hz)
                        if tract["hazards"][key]["expected_annual_loss_usd"] is not None:
                            _fact(facts, ev, f"{base}/hazards/{key}/expected_annual_loss_usd",
                                  f"FEMA NRI {key.replace('_', ' ')} expected annual loss, whole tract", "USD", hz)
    return facts


def find_gaps(evidence_items, policy, hazards, location):
    gaps = []
    if not location.get("confirmed"):
        gaps.append("Location is an approximate point that the user has not confirmed; parcel-level "
                    "interpretation is not supported.")
    for name, reason in sorted(policy.blocked.items()):
        gaps.append(f"{name} was not called: {reason}")
    if policy.pins is None:
        gaps.append("Tool definitions are not pinned (run --write-pins after review); drift cannot be detected.")
    for ev in evidence_items:
        env = ev["envelope"] or {}
        if ev["is_error"]:
            msg = ev["error"] or (env.get("error") or {}).get("message", "unknown error")
            gaps.append(f"{ev['tool']} ({ev['id']}) failed: {msg}")
        elif env.get("status") == "unavailable":
            gaps.append(f"{ev['tool']} ({ev['id']}) has no coverage here: "
                        f"{env['data'].get('reason') or 'source returned unavailable'}. Unknown, not zero.")
    if any(ev["tool"] == "get_hazard_curve" and (ev["arguments"].get("peril") == "earthquake")
           for ev in evidence_items):
        gaps.append("Earthquake curve assumes site class BC unless a site class was supplied.")
    gaps.append("No exposure or vulnerability inputs (building type, first-floor height, value) were "
                "supplied, so no property loss estimate is possible.")
    return gaps


# ---------------------------------------------------------------- MCP execution
def _definition(tool):
    data = tool.model_dump(by_alias=True, mode="json", exclude_none=True)
    return {"name": data["name"], "description": data.get("description"),
            "inputSchema": data.get("inputSchema"), "outputSchema": data.get("outputSchema")}


def _result_dict(result):
    text = next((c.text for c in result.content if getattr(c, "type", None) == "text"), "")
    return {"isError": result.is_error, "structuredContent": result.structured_content, "text": text}


async def _call(client, policy, registry, limiter, tool, arguments):
    started = now()
    try:
        policy.check(tool, arguments)
    except (PermissionError, ValidationError) as exc:
        return registry.add(tool, arguments, None, started, error=f"Rejected by host policy: {exc}")
    async with limiter:
        try:
            with anyio.fail_after(CALL_TIMEOUT_S):
                result = _result_dict(await client.call_tool(tool, arguments))
        except TimeoutError:
            return registry.add(tool, arguments, None, started, error=f"Host deadline {CALL_TIMEOUT_S}s exceeded")
        except Exception as exc:  # protocol errors, invalid structured content, transport failure
            return registry.add(tool, arguments, None, started, error=f"{type(exc).__name__}: {exc}")
    item = registry.add(tool, arguments, result, started)
    request = ((item["envelope"] or {}).get("meta") or {}).get("request") or {}
    if "lat" in arguments and not item["is_error"] and (
            request.get("lat") != arguments["lat"] or request.get("lon") != arguments["lon"]):
        item.update(is_error=True, error="Result location does not match the requested point; discarded.")
    return item


def plan_calls(hazards, lat, lon):
    plan = [(name, {**extra, "lat": lat, "lon": lon}) for hz in hazards for name, extra in HAZARD_PLANS[hz]]
    plan += [(name, {"lat": lat, "lon": lon}) for name, _ in CONTEXT_PLAN]
    if len(plan) > MAX_CALLS:
        raise ValueError(f"Plan exceeds the {MAX_CALLS}-call budget.")
    return plan


async def assess(server, *, hazards, address=None, lat=None, lon=None, confirmed=False, purpose=None,
                 policy=None):
    """Run one bounded hazard screening. `server` is anything mcp.Client accepts."""
    from mcp import Client

    hazards = list(dict.fromkeys(hazards))
    if not hazards or any(h not in SUPPORTED_HAZARDS for h in hazards):
        raise ValueError(f"hazards must be a non-empty subset of {SUPPORTED_HAZARDS}")
    if (address is None) == (lat is None or lon is None):
        raise ValueError("Supply either an address or both lat and lon.")
    policy = policy or ToolPolicy.from_file()
    registry, limiter = EvidenceRegistry(), anyio.CapacityLimiter(CONCURRENCY)
    record = {"assessment_id": str(uuid.uuid4()), "schema_version": SCHEMA_VERSION, "created_at": now(),
              "assessment_type": "hazard_screening", "status": "failed",
              "scope": {"hazards": hazards, "purpose": purpose}, "location": {}, "evidence": registry.items,
              "facts": [], "gaps": [], "narrative": None,
              "run": {"host": "ndapp-risk-agent/0.1", "max_calls": MAX_CALLS, "concurrency": CONCURRENCY,
                      "call_timeout_s": CALL_TIMEOUT_S, "model": None, "blocked_tools": {}}}
    async with Client(server, read_timeout_seconds=CALL_TIMEOUT_S + 5) as client:
        listed = await client.list_tools()
        policy.review([_definition(t) for t in listed.tools])
        record["run"]["blocked_tools"] = dict(policy.blocked)
        if address is not None:
            ev = await _call(client, policy, registry, limiter, "resolve_location", {"address": address})
            data = (ev["envelope"] or {}).get("data") or {}
            if ev["is_error"] or data.get("status") != "available":
                record["status"] = "awaiting_input" if data.get("match_status") in ("ambiguous", "no_match") else "failed"
                record["location"] = {"address": address, "match_status": data.get("match_status"),
                                      "candidates": data.get("candidates", []), "resolution_evidence_id": ev["id"]}
                record["gaps"] = find_gaps(registry.items, policy, hazards, record["location"])
                return record
            lat, lon = data["latitude"], data["longitude"]
            record["location"] = {"address": address, "matched_address": data.get("matched_address"),
                                  "approximate": True, "resolution_evidence_id": ev["id"]}
        record["location"].update(lat=lat, lon=lon, confirmed=bool(confirmed))
        plan = plan_calls(hazards, lat, lon)
        async with anyio.create_task_group() as tg:
            for tool, arguments in plan:
                tg.start_soon(_call, client, policy, registry, limiter, tool, arguments)
    registry.items.sort(key=lambda e: int(e["id"][1:]))
    record["facts"] = extract_facts(registry.items, hazards)
    record["gaps"] = find_gaps(registry.items, policy, hazards, record["location"])
    curve_ok = {h: any(e["tool"] == "get_hazard_curve" and e["arguments"].get("peril") == h
                       and not e["is_error"] and e["status"] == "available" for e in registry.items)
                for h in hazards if h != "wildfire"}
    hazard_ok = [any(f["hazard"] == h for f in record["facts"]) for h in hazards]
    record["hazard_status"] = {h: ("evidence_available" if ok else "no_usable_evidence")
                               for h, ok in zip(hazards, hazard_ok)}
    record["hazard_status"].update({h: "evidence_available_no_curve" for h, ok in curve_ok.items()
                                    if not ok and record["hazard_status"][h] == "evidence_available"})
    record["status"] = ("complete" if all(hazard_ok) and not any(e["is_error"] for e in registry.items)
                        else "partial" if any(hazard_ok) else "unavailable")
    return record


# ---------------------------------------------------------------- Claude (optional)
SCOPE_SCHEMA = {"type": "object", "additionalProperties": False,
                "required": ["address", "lat", "lon", "hazards", "purpose", "clarification_needed"],
                "properties": {"address": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                               "lat": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                               "lon": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                               "hazards": {"type": "array", "items": {"enum": list(SUPPORTED_HAZARDS)}},
                               "purpose": {"type": "string"},
                               "clarification_needed": {"anyOf": [{"type": "string"}, {"type": "null"}]}}}
NARRATIVE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["statements"],
                    "properties": {"statements": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False, "required": ["hazard", "text", "fact_ids"],
                        "properties": {"hazard": {"type": "string"}, "text": {"type": "string"},
                                       "fact_ids": {"type": "array", "items": {"type": "string"}}}}}}}


class ModelStopped(RuntimeError):
    pass


def _claude_json(client, system, user, schema, effort="medium"):
    # Server-side fallback (beta) re-runs a refused request on a fallback model inside the same call.
    response = client.beta.messages.create(
        model=MODEL, max_tokens=16000, system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    # Anything other than a normal finish (refusal, max_tokens, pause_turn, context exhaustion, ...)
    # may leave truncated or missing JSON, so it is reported rather than parsed.
    if response.stop_reason not in ("end_turn", "stop_sequence"):
        raise ModelStopped(f"Model stopped without a usable answer: {response.stop_reason}")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ModelStopped("Model returned no text block")
    try:
        return json.loads(text), response.model
    except json.JSONDecodeError as exc:
        raise ModelStopped(f"Model returned invalid JSON: {exc}") from exc


def interpret_request(text, client=None):
    """Claude proposes a scope; the host validates it. The model never chooses tools directly."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    system = ("You convert a user's natural-disaster question into an assessment scope for a US hazard "
              "screening tool. Supported hazards: flood, earthquake, wildfire. Copy any street address "
              "exactly as written; give lat/lon only if the user wrote coordinates. If the location or "
              "hazards are missing, set clarification_needed to one short question. Do not answer the question.")
    scope, model = _claude_json(client, system, text, SCOPE_SCHEMA, effort="low")
    Draft202012Validator(SCOPE_SCHEMA).validate(scope)
    for key, low, high in (("lat", -90, 90), ("lon", -180, 180)):
        if scope[key] is not None and not (math.isfinite(scope[key]) and low <= scope[key] <= high):
            scope["clarification_needed"] = "Please confirm the coordinates."
    if not scope["hazards"]:
        scope["clarification_needed"] = scope["clarification_needed"] or "Which hazards should be assessed?"
    return scope, model


# A leading "-" is a sign only when it does not follow a word/number, so "2.5-4.1" and "5-10 km"
# read as ranges (2.5, 4.1) rather than a negative second number.
_NUMBER = re.compile(r"(?:(?<![\w.])-)?\d[\d,]*(?:\.\d+)?")
_FACT_ID = re.compile(r"\b[FE]\d+\b")  # inline citations like "F3" or "E12" are not quantities
MAX_ROUNDING_ERROR = 0.10  # rounded values may differ from the fact by at most 10%


def _numbers(text):
    out = []
    for token in _NUMBER.findall(_FACT_ID.sub(" ", text)):
        try:
            out.append((float(token.replace(",", "")), len(token.split(".")[1]) if "." in token else 0))
        except ValueError:
            pass
    return out


def _supports(number, decimals, fact_value):
    """True if `number` as written is a faithful rendering of `fact_value`."""
    if number == fact_value or (fact_value and abs(number - fact_value) / abs(fact_value) <= 0.005):
        return True
    if fact_value != 0 and number == 0:
        return False  # never let rounding turn a real, non-zero value into zero
    if abs(number - fact_value) > 0.5 * 10 ** -decimals + 1e-9:
        return False  # not a rounding of the fact at the written precision
    return fact_value == 0 or abs(number - fact_value) / abs(fact_value) <= MAX_ROUNDING_ERROR


def check_statement(statement, facts_by_id, context_numbers=()):
    """Return None if supported, else the rejection reason.

    `context_numbers` are non-fact values the model was shown and may quote (e.g. the site coordinates).
    """
    cited = [facts_by_id.get(fid) for fid in statement["fact_ids"]]
    if not cited or None in cited:
        return "cites a missing or unknown fact"
    if any(pattern.search(statement["text"]) for pattern in BANNED_PATTERNS):
        return "uses a prohibited conclusion (safety, zero risk or invented score)"
    allowed = [float(v) for v in context_numbers if isinstance(v, (int, float)) and math.isfinite(v)]
    for fact in cited:
        values = [fact["value"]] if not isinstance(fact["value"], list) else fact["value"]
        allowed += [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        allowed += [n for n, _ in _numbers(f"{fact['label']} {fact['unit'] or ''} {fact['value']}")]
    for number, decimals in _numbers(statement["text"]):
        if not any(_supports(number, decimals, a) for a in allowed):
            return f"number {number:g} is not supported by the cited facts"
    return None


def narrate(record, client=None):
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    from agent_tools import EVIDENCE_RULES
    table = [{k: f[k] for k in ("id", "hazard", "evidence_kind", "label", "value", "unit")} for f in record["facts"]]
    system = ("You explain natural-disaster hazard evidence to a non-specialist. Use only the facts provided. "
              "Every statement must cite the fact IDs it relies on, and every number you write must appear in "
              "those facts. Keep evidence kinds distinct and state their meaning (modeled probability vs "
              "historical frequency vs map zone vs tract-level index). Never claim safety, zero risk, or an "
              "overall score, and never estimate property loss.\n\n" + EVIDENCE_RULES)
    user = json.dumps({"location": record["location"], "hazards": record["scope"]["hazards"],
                       "facts": table, "gaps": record["gaps"]}, default=str)
    draft, model = _claude_json(client, system, user, NARRATIVE_SCHEMA)
    Draft202012Validator(NARRATIVE_SCHEMA).validate(draft)
    facts_by_id = {f["id"]: f for f in record["facts"]}
    location_numbers = [record["location"].get(k) for k in ("lat", "lon")]
    accepted, rejected = [], []
    for statement in draft["statements"]:
        reason = check_statement(statement, facts_by_id, context_numbers=location_numbers)
        (rejected if reason else accepted).append({**statement, **({"rejection": reason} if reason else {})})
    record["narrative"] = {"model": model, "accepted": accepted, "rejected": rejected}
    record["run"]["model"] = model
    return record


# ---------------------------------------------------------------- rendering
def _fmt(value):
    if isinstance(value, float):
        return f"{value:.4g}"
    return "—" if value is None else str(value)


def render_markdown(record):
    loc = record["location"]
    lines = [f"# Hazard screening {record['assessment_id'][:8]}",
             f"Status: **{record['status']}** · created {record['created_at']} · schema {record['schema_version']}",
             "", "Research evidence only. This is not a warning, insurance quote, safety determination or "
             "property loss estimate.", "",
             f"**Location:** {loc.get('matched_address') or loc.get('address') or ''} "
             f"({_fmt(loc.get('lat'))}, {_fmt(loc.get('lon'))}); "
             f"{'confirmed' if loc.get('confirmed') else 'approximate, not confirmed'}", ""]
    if record["status"] == "awaiting_input":
        lines += [f"Address match status: {loc.get('match_status')}. Choose one candidate and rerun:", ""]
        lines += [f"- {c}" for c in loc.get("candidates", [])]
    for hazard in [*record["scope"]["hazards"], "context"]:
        rows = [f for f in record["facts"] if f["hazard"] == hazard]
        status = record.get("hazard_status", {}).get(hazard, "")
        lines += ["", f"## {hazard.title()} {('— ' + status.replace('_', ' ')) if status else ''}".rstrip(), ""]
        if not rows:
            lines.append("No usable evidence (see gaps).")
            continue
        lines += ["| Fact | Kind | Measure | Value | Evidence |", "| --- | --- | --- | --- | --- |"]
        lines += [f"| {f['id']} | {f['evidence_kind']} | {f['label']} | {_fmt(f['value'])} {f['unit'] or ''} "
                  f"| {f['evidence_id']}{f['pointer']} |" for f in rows]
    narrative = record.get("narrative")
    if narrative and narrative.get("error"):
        lines += ["", "## Explanation", "", f"_No explanation was added: {narrative['error']}_"]
    elif narrative:
        lines += ["", f"## Explanation ({narrative['model']}, claim-checked)", ""]
        lines += [f"- {s['text']} [{', '.join(s['fact_ids'])}]" for s in narrative["accepted"]]
        if narrative["rejected"]:
            lines += ["", f"_{len(narrative['rejected'])} model statement(s) were withheld because they failed "
                      "evidence checks._"]
    lines += ["", "## Gaps and limitations", ""] + [f"- {g}" for g in record["gaps"]]
    lines += ["", "## Evidence", "", "| ID | Tool | Status | Retrieved | SHA-256 |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| {e['id']} | {e['tool']} | {'error' if e['is_error'] else e['status']} | {e['retrieved_at']} "
              f"| {(e['sha256'] or '')[:12]} |" for e in record["evidence"]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- web
def public_record(record):
    """Browser-sized view: facts, gaps and evidence provenance, without raw agency envelopes."""
    evidence = []
    for e in record["evidence"]:
        data = (e["envelope"] or {}).get("data") or {}
        evidence.append({"id": e["id"], "tool": e["tool"], "status": "error" if e["is_error"] else e["status"],
                         "retrieved_at": e["retrieved_at"], "sha256": e["sha256"],
                         "source_name": data.get("source_name"), "source_url": data.get("source_url")})
    ok = record["status"] in ("complete", "partial")
    reasons = {"awaiting_input": "The address matched more than one place or none; choose a point on the map.",
               "unavailable": "No requested hazard returned usable evidence here.",
               "failed": "The assessment could not run; no evidence was substituted."}
    return {"status": "available" if ok else "unavailable", "assessment_status": record["status"],
            "assessment_id": record["assessment_id"], "schema_version": record["schema_version"],
            "created_at": record["created_at"], "assessment_type": record["assessment_type"],
            "scope": record["scope"], "location": record["location"],
            "hazard_status": record.get("hazard_status", {}), "facts": record["facts"], "gaps": record["gaps"],
            "evidence": evidence, "reason": None if ok else reasons.get(record["status"]),
            "notes": ["Evidence screening, not a warning, insurance quote, safety determination or loss estimate.",
                      "Each fact keeps its evidence kind; kinds are never combined into a score.",
                      "Unavailable or failed sources are unknowns, not zero risk."]}


def web_assessment(hazards, lat=None, lon=None, address=None):
    """Run one assessment through the in-process MCP server. Call from a worker thread."""
    from ndapp_mcp_server import build_server

    async def run():
        return await assess(build_server(), hazards=hazards, lat=lat, lon=lon, address=address)
    return public_record(anyio.run(run))


# ---------------------------------------------------------------- CLI
def stdio_server():
    from mcp import StdioServerParameters
    return StdioServerParameters(command=sys.executable, args=[str(ROOT / "ndapp_mcp_server.py")], cwd=str(ROOT))


async def write_pins(server, path=PINS_PATH):
    from mcp import Client
    async with Client(server) as client:
        listed = await client.list_tools()
    pins = {t.name: definition_hash(_definition(t)) for t in listed.tools}
    Path(path).write_text(json.dumps({"reviewed_at": now(), "server": "ndapp", "tools": pins}, indent=2) + "\n",
                          encoding="utf-8")
    return pins


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--address")
    where.add_argument("--ask", help="Natural-language request; Claude proposes the scope (needs API credentials).")
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lon", type=float)
    parser.add_argument("--hazards", nargs="+", choices=SUPPORTED_HAZARDS, default=["flood", "earthquake"])
    parser.add_argument("--confirmed", action="store_true", help="The user confirmed the location point.")
    parser.add_argument("--narrate", action="store_true", help="Add a claim-checked Claude explanation.")
    parser.add_argument("--json", type=Path, help="Write the full assessment record here.")
    parser.add_argument("--write-pins", action="store_true", help="Pin current tool definitions after review.")
    args = parser.parse_args(argv)
    if args.write_pins:
        pins = anyio.run(write_pins, stdio_server())
        print(f"Pinned {len(pins)} tool definitions to {PINS_PATH}")
        return 0
    for name in ("lat", "lon"):
        if getattr(args, name) is not None and not math.isfinite(getattr(args, name)):
            parser.error(f"--{name} must be a finite number.")
    if args.address and (args.lat is not None or args.lon is not None):
        parser.error("Use either --address or --lat/--lon, not both.")
    purpose = None
    if args.ask:
        try:
            scope, _ = interpret_request(args.ask)
        except Exception as exc:  # noqa: BLE001 - report model/credential failures without a traceback
            print(f"Could not interpret the request: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        if scope["clarification_needed"]:
            print("Clarification needed:", scope["clarification_needed"])
            return 2
        args.address, args.lat, args.lon, args.hazards = scope["address"], scope["lat"], scope["lon"], scope["hazards"]
        purpose = scope["purpose"]
        if args.address and args.lat is not None:
            args.lat = args.lon = None  # an address always goes through the geocoder
    if args.address is None and (args.lat is None or args.lon is None):
        parser.error("Supply --address, --ask, or both --lat and --lon.")

    async def run():
        return await assess(stdio_server(), hazards=args.hazards, address=args.address, lat=args.lat,
                            lon=args.lon, confirmed=args.confirmed, purpose=purpose)

    record = anyio.run(run)
    if args.narrate and record["facts"]:
        try:
            narrate(record)
        except Exception as exc:  # noqa: BLE001 - the narrative is optional; never lose the assessment
            record["narrative"] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"Explanation skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
    if args.json:
        args.json.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    sys.stdout.write(render_markdown(record))
    return 0 if record["status"] in ("complete", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(main())
