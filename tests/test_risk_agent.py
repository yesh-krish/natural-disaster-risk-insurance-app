"""New providers, the MCP server over agent_tools, and the risk_agent host."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import unittest.mock
from unittest.mock import patch

import anyio
from jsonschema import Draft202012Validator, FormatChecker
from mcp import Client
from mcp.shared.exceptions import MCPError

import agent_tools
import ndapp_mcp_server
import risk_agent
import risk_context as context
from api_spec import openapi_document
from api_v1 import dispatch

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "agent_san_antonio.json").read_text())
from api_v1 import VERSION as API_VERSION  # noqa: E402
for _envelope in FIXTURES.values():  # recorded live under 1.8.0; replay under the current contract
    _envelope["api_version"] = API_VERSION
LAT, LON = 29.4242, -98.4933


def assert_contract(test, path, payload, code=200):
    spec = openapi_document()
    schema = spec["paths"][path]["get"]["responses"][str(code)]["content"]["application/json"]["schema"]
    Draft202012Validator({**schema, "components": spec["components"]}, format_checker=FormatChecker()).validate(payload)


def nri_attrs(**changes):
    attrs = dict.fromkeys(context.NRI_FIELDS)
    attrs.update(TRACTFIPS="48029110100", STATE="Texas", COUNTY="Bexar", NRI_VER="December 2025",
                 RISK_RATNG="Relatively High", RISK_SCORE=96.1, IFLD_RISKR="Very High", IFLD_EALT=3498664.9)
    attrs.update(changes)
    return {"attributes": attrs}


def quake(identifier="ci1", mag=3.2, url="https://earthquake.usgs.gov/earthquakes/eventpage/ci1"):
    return {"type": "Feature", "id": identifier,
            "properties": {"mag": mag, "time": 1791531857430, "place": "near", "magType": "ml",
                           "status": "reviewed", "url": url, "mmi": 3.5, "alert": None, "tsunami": 0},
            "geometry": {"type": "Point", "coordinates": [-118.18, 33.79, 12]}}


class ProviderTests(unittest.TestCase):
    def test_elevation_value_no_data_and_bad_values(self):
        with patch.object(context, "_json", return_value={"value": "197.47", "resolution": 1,
                                                          "attributes": {"AcquisitionDate": "2/2/2017"}}):
            code, payload = dispatch("/api/v1/elevation-context", f"lat={LAT}&lon={LON}")
        self.assertEqual((code, payload["data"]["elevation_m"]), (200, 197.47))
        assert_contract(self, "/api/v1/elevation-context", payload)
        with patch.object(context, "_json", return_value={"value": -1000000}):
            code, payload = dispatch("/api/v1/elevation-context", f"lat={LAT}&lon={LON}")
        self.assertEqual(payload["status"], "unavailable")
        assert_contract(self, "/api/v1/elevation-context", payload)
        for body in ({"value": "x"}, {"value": None}, {"value": "nan"}, {"value": 1, "resolution": True}):
            with self.subTest(body=body), patch.object(context, "_json", return_value=body):
                self.assertEqual(dispatch("/api/v1/elevation-context", f"lat={LAT}&lon={LON}")[0], 502)

    def test_earthquakes_report_full_count_truncation_and_distance(self):
        pages = [{"count": 250}, {"type": "FeatureCollection", "features": [quake(), quake("ci2", 2.6)]}]
        with patch.object(context, "_json", side_effect=pages) as fetch:
            code, payload = dispatch("/api/v1/recent-earthquakes", "lat=34.05&lon=-118.25&days=30")
        data = payload["data"]
        self.assertEqual((code, data["total_count"], data["returned_count"], data["truncated"]), (200, 250, 2, True))
        self.assertEqual(data["largest_magnitude"], 3.2)
        self.assertAlmostEqual(data["events"][0]["epicentral_distance_km"], 30.1, delta=1)
        self.assertIn("orderby=magnitude", fetch.call_args_list[1].args[0])
        assert_contract(self, "/api/v1/recent-earthquakes", payload)
        with patch.object(context, "_json", side_effect=[{"count": 0}, {"type": "FeatureCollection", "features": []}]):
            payload = dispatch("/api/v1/recent-earthquakes", "lat=34&lon=-118")[1]
        self.assertEqual(payload["status"], "not_found")
        assert_contract(self, "/api/v1/recent-earthquakes", payload)

    def test_earthquake_bad_inputs_links_and_inconsistent_counts_fail(self):
        for query in ("lat=34&lon=-118&radius_km=0", "lat=34&lon=-118&days=1.5", "lat=34&lon=-118&min_magnitude=11",
                      "lat=34&lon=-118&radius_km=nan"):
            with self.subTest(query=query), patch.object(context, "_json") as fetch:
                self.assertEqual(dispatch("/api/v1/recent-earthquakes", query)[0], 400)
                fetch.assert_not_called()
        cases = [[{"count": 1}, {"type": "FeatureCollection", "features": [quake(url="https://evil.example/x")]}],
                 [{"count": 0}, {"type": "FeatureCollection", "features": [quake()]}],
                 [{"count": True}], [{"count": 1}, {"type": "FeatureCollection", "features": [quake(mag=None)]}]]
        for pages in cases:
            with self.subTest(pages=pages), patch.object(context, "_json", side_effect=pages):
                self.assertEqual(dispatch("/api/v1/recent-earthquakes", "lat=34&lon=-118")[0], 502)

    def test_nri_tract_is_preserved_and_missing_tract_is_unavailable(self):
        with patch.object(context, "_json", return_value={"features": [nri_attrs()]}):
            code, payload = dispatch("/api/v1/risk-index", f"lat={LAT}&lon={LON}")
        tract = payload["data"]["tracts"][0]
        self.assertEqual(tract["hazards"]["inland_flooding"]["risk_rating"], "Very High")
        self.assertEqual(len(tract["hazards"]), 18)
        assert_contract(self, "/api/v1/risk-index", payload)
        with patch.object(context, "_json", return_value={"features": []}):
            payload = dispatch("/api/v1/risk-index", f"lat={LAT}&lon={LON}")[1]
        self.assertEqual(payload["status"], "unavailable")
        for body in ({"features": [{"attributes": {"TRACTFIPS": "1"}}]}, {"features": [], "exceededTransferLimit": True},
                     {"features": [nri_attrs(RISK_SCORE=float("inf"))]}, {"features": [nri_attrs(TRACTFIPS="")]}):
            with self.subTest(body=body), patch.object(context, "_json", return_value=body):
                self.assertEqual(dispatch("/api/v1/risk-index", f"lat={LAT}&lon={LON}")[0], 502)

    def test_new_origins_are_constrained(self):
        for url, origin in ((context.EPQS + "x", context.EPQS), (context.USGS_EVENTS + "/other", context.USGS_EVENTS),
                            (context.NRI_LAYER + "/1/query", context.NRI_LAYER),
                            ("https://evil.example/fdsnws/event/1/query", context.USGS_EVENTS)):
            with self.subTest(url=url), self.assertRaises(OSError):
                context._allowed_url(url, origin)


class McpServerTests(unittest.TestCase):
    def run_client(self, body):
        async def go():
            async with Client(ndapp_mcp_server.build_server()) as client:
                return await body(client)
        return anyio.run(go)

    def test_discovery_resources_and_schemas(self):
        async def body(client):
            tools = (await client.list_tools()).tools
            resources = (await client.list_resources()).resources
            rules = await client.read_resource("ndapp://methods/evidence-rules")
            return tools, resources, rules
        tools, resources, rules = self.run_client(body)
        self.assertEqual({t.name for t in tools}, set(agent_tools.TOOLS))
        for tool in tools:
            self.assertTrue(tool.annotations.read_only_hint)
            Draft202012Validator.check_schema(tool.output_schema)
            self.assertFalse(tool.input_schema["additionalProperties"])
        self.assertEqual(len(resources), 2)
        self.assertIn("never zero risk", rules.contents[0].text)

    def test_invalid_arguments_fail_before_backend_and_unknown_tools_are_protocol_errors(self):
        async def body(client):
            with patch("agent_tools.dispatch") as backend:
                bad = [await client.call_tool("get_flood_zone_context", args) for args in
                       ({}, {"lat": 1}, {"lat": True, "lon": 1}, {"lat": 0, "lon": 1, "url": "x"}, {"lat": 95, "lon": 0})]
                bad.append(await client.call_tool("get_hazard_curve", {"lat": 1, "lon": 1, "peril": "auto"}))
                backend.assert_not_called()
            with self.assertRaises(MCPError):
                await client.call_tool("delete_everything", {})
            return bad
        for result in self.run_client(body):
            self.assertTrue(result.is_error)
            self.assertEqual(result.structured_content["error"]["code"], "invalid_request")

    def test_valid_call_reaches_backend_and_invalid_evidence_is_replaced_by_error(self):
        async def body(client):
            with patch.object(context, "_json", return_value={"features": [nri_attrs()]}):
                good = await client.call_tool("get_community_risk_index", {"lat": LAT, "lon": LON})
            with patch("agent_tools.dispatch", return_value=(200, {"bad": "evidence"})):
                bad = await client.call_tool("get_community_risk_index", {"lat": LAT, "lon": LON})
            return good, bad
        good, bad = self.run_client(body)
        self.assertFalse(good.is_error)
        self.assertEqual(json.loads(good.content[0].text), good.structured_content)
        self.assertTrue(bad.is_error)
        self.assertEqual(bad.structured_content["error"]["code"], "provider_unavailable")


def fake_call_tool(overrides=None, calls=None):
    def call(name, arguments):
        if calls is not None:
            calls.append((name, dict(arguments)))
        key = name + (":" + arguments["peril"] if "peril" in arguments else "")
        if overrides and key in overrides:
            return overrides[key](name, arguments)
        envelope = copy.deepcopy(FIXTURES[key])
        envelope["meta"]["request"].update(lat=arguments["lat"], lon=arguments["lon"])
        return {"isError": False, "structuredContent": envelope, "content": [{"type": "text", "text": json.dumps(envelope)}]}
    return call


def run_assess(policy=None, overrides=None, calls=None, **kwargs):
    kwargs.setdefault("hazards", ["flood", "earthquake", "wildfire"])
    if "address" not in kwargs:
        kwargs.update(lat=LAT, lon=LON)
    with patch("agent_tools.call_tool", side_effect=fake_call_tool(overrides, calls)):
        return anyio.run(lambda: risk_agent.assess(ndapp_mcp_server.build_server(),
                                                   policy=policy or risk_agent.ToolPolicy.from_file(), **kwargs))


class AgentHostTests(unittest.TestCase):
    def test_complete_run_with_pinned_tools_and_traceable_facts(self):
        calls = []
        record = run_assess(calls=calls)
        self.assertEqual(record["status"], "complete")
        self.assertEqual(len(calls), 8)
        self.assertLessEqual(len(calls), risk_agent.MAX_CALLS)
        evidence = {e["id"]: e for e in record["evidence"]}
        for fact in record["facts"]:
            self.assertEqual(risk_agent._pointer_get(evidence[fact["evidence_id"]]["envelope"], fact["pointer"]),
                             fact["value"])
        self.assertFalse(any("not pinned" in g for g in record["gaps"]))
        self.assertTrue(any("property loss" in g for g in record["gaps"]))
        markdown = risk_agent.render_markdown(record)
        self.assertIn("not a warning, insurance quote", markdown)
        json.dumps(record, allow_nan=False, default=str)

    def test_tool_definition_drift_blocks_the_tool_and_yields_partial(self):
        pins = dict(risk_agent.ToolPolicy.from_file().pins, get_flood_zone_context="0" * 64)
        calls = []
        record = run_assess(policy=risk_agent.ToolPolicy(pins), calls=calls)
        self.assertNotIn("get_flood_zone_context", [name for name, _ in calls])
        self.assertEqual(record["status"], "partial")
        self.assertTrue(any("drift" in g for g in record["gaps"]))

    def test_failed_tool_is_a_gap_not_a_zero(self):
        def fail(name, arguments):
            payload = agent_tools.error_response(502, "provider_unavailable", "down")[1]
            return {"isError": True, "structuredContent": payload, "content": [{"type": "text", "text": json.dumps(payload)}]}
        record = run_assess(overrides={"get_hazard_curve:flood": fail}, hazards=["flood"])
        self.assertEqual(record["status"], "partial")
        self.assertEqual(record["hazard_status"]["flood"], "evidence_available_no_curve")
        self.assertTrue(any("get_hazard_curve" in g and "down" in g for g in record["gaps"]))

    def test_mismatched_result_location_is_discarded(self):
        def moved(name, arguments):
            envelope = copy.deepcopy(FIXTURES["get_elevation_context"])
            envelope["meta"]["request"].update(lat=0.0, lon=0.0)
            return {"isError": False, "structuredContent": envelope, "content": [{"type": "text", "text": json.dumps(envelope)}]}
        record = run_assess(overrides={"get_elevation_context": moved}, hazards=["flood"])
        self.assertFalse(any(f["evidence_kind"] == "terrain" for f in record["facts"]))

    def test_ambiguous_address_stops_before_hazard_calls(self):
        def ambiguous(name, arguments):
            payload = dispatch_like({"status": "unavailable", "match_status": "ambiguous", "normalized_address": "x",
                                     "approximate": True, "note": "choose", "candidates": [{"address": "A"}, {"address": "B"}]})
            return {"isError": False, "structuredContent": payload, "content": [{"type": "text", "text": json.dumps(payload)}]}
        calls = []
        record = run_assess(overrides={"resolve_location": ambiguous}, calls=calls, address="1 Main St, Springfield, TX")
        self.assertEqual(record["status"], "awaiting_input")
        self.assertEqual([name for name, _ in calls], ["resolve_location"])
        self.assertEqual(len(record["location"]["candidates"]), 2)

    def test_scope_validation(self):
        with self.assertRaises(ValueError):
            run_assess(hazards=["auto"])
        with self.assertRaises(ValueError):
            run_assess(hazards=["flood"], address="x", lat=1, lon=1)


def dispatch_like(data):
    from api_v1 import envelope
    return envelope(data, {"address": "x"})


class AssessmentRouteTests(unittest.TestCase):
    PATH = "/api/v1/assessment"

    def test_invalid_requests_stop_before_the_agent_runs(self):
        for query in ("lat=29&lon=-98&hazards=auto", "lat=29&lon=-98&hazards=flood,flood", "lat=29",
                      "lat=29&lon=-98&address=1%20Main%20St", "lat=nan&lon=1", "lat=29&lon=-98&hazards=",
                      "lat=29&lon=-98&url=x"):
            with self.subTest(query=query), patch("risk_agent.web_assessment") as agent:
                code, payload = dispatch(self.PATH, query)
                self.assertEqual(code, 400)
                agent.assert_not_called()
                assert_contract(self, self.PATH, payload, code)

    def test_point_assessment_returns_facts_without_raw_envelopes(self):
        with patch("agent_tools.call_tool", side_effect=fake_call_tool()):
            code, payload = dispatch(self.PATH, f"lat={LAT}&lon={LON}&hazards=flood,earthquake")
        self.assertEqual(code, 200)
        assert_contract(self, self.PATH, payload)
        data = payload["data"]
        self.assertEqual((payload["status"], data["assessment_status"]), ("available", "complete"))
        self.assertEqual(data["scope"]["hazards"], ["flood", "earthquake"])
        self.assertTrue(any(f["hazard"] == "flood" for f in data["facts"]))
        self.assertFalse(any("envelope" in e for e in data["evidence"]))
        self.assertTrue(all(e["sha256"] for e in data["evidence"]))
        json.dumps(payload, allow_nan=False)

    def test_ambiguous_address_is_unavailable_with_candidates(self):
        def ambiguous(name, arguments):
            body = dispatch_like({"status": "unavailable", "match_status": "ambiguous", "normalized_address": "x",
                                  "approximate": True, "note": "choose", "candidates": [{"address": "A"}]})
            return {"isError": False, "structuredContent": body, "content": [{"type": "text", "text": json.dumps(body)}]}
        with patch("agent_tools.call_tool", side_effect=fake_call_tool({"resolve_location": ambiguous})):
            code, payload = dispatch(self.PATH, "address=1%20Main%20St%2C%20Springfield%2C%20TX&hazards=flood")
        self.assertEqual((code, payload["status"]), (200, "unavailable"))
        self.assertEqual(payload["data"]["assessment_status"], "awaiting_input")
        self.assertEqual(len(payload["data"]["location"]["candidates"]), 1)
        assert_contract(self, self.PATH, payload)


class FakeResponse:
    def __init__(self, payload, stop_reason="end_turn"):
        self.content = [type("Block", (), {"type": "text", "text": json.dumps(payload)})()]
        self.stop_reason, self.model = stop_reason, risk_agent.MODEL


class FakeClaude:
    def __init__(self, payload, stop_reason="end_turn"):
        self.requests = []
        outer = self

        class Messages:
            def create(self, **kwargs):
                outer.requests.append(kwargs)
                return FakeResponse(payload, stop_reason)
        self.beta = type("Beta", (), {"messages": Messages()})()


class ClaudeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.record = run_assess(hazards=["flood", "earthquake"])
        self.facts = {f["id"]: f for f in self.record["facts"]}
        self.zone = next(f for f in self.record["facts"] if f["label"] == "FEMA NFHL flood zone")
        self.elev = next(f for f in self.record["facts"] if f["evidence_kind"] == "terrain")

    def test_claim_checker_accepts_supported_and_rejects_unsupported_statements(self):
        ok = {"hazard": "flood", "text": f"USGS ground elevation is about {self.elev['value']:.1f} m.",
              "fact_ids": [self.elev["id"]]}
        self.assertIsNone(risk_agent.check_statement(ok, self.facts))
        cases = [{**ok, "text": "Ground elevation is 450 m."}, {**ok, "fact_ids": ["F999"]}, {**ok, "fact_ids": []},
                 {**ok, "text": "The property is safe from floods."},
                 {**ok, "text": "The overall risk score is high."}]
        for statement in cases:
            with self.subTest(statement=statement):
                self.assertIsNotNone(risk_agent.check_statement(statement, self.facts))

    def test_claim_checker_numeric_rounding_cannot_erase_risk(self):
        facts = {"F1": {"id": "F1", "label": "Annual exceedance probability", "unit": "%", "value": 0.2},
                 "F2": {"id": "F2", "label": "Depth", "unit": "ft", "value": 0.6},
                 "F3": {"id": "F3", "label": "Elevation", "unit": "m", "value": 187.34},
                 "F4": {"id": "F4", "label": "Magnitude range", "unit": None, "value": [2.5, 4.1]}}
        rejected = [("There is a 0% annual chance of this flood depth.", ["F1"]),
                    ("Flood depth is 0 ft.", ["F2"]), ("Flood depth is about 1 ft.", ["F2"])]
        accepted = [("There is a 0.2% annual chance.", ["F1"]), ("Depth is 0.6 ft.", ["F2"]),
                    ("Ground elevation is about 187 m.", ["F3"]),
                    ("Recent magnitudes ranged 2.5-4.1 (F4).", ["F4"]),
                    ("At 29.4242, -98.4933 the ground elevation is 187.3 m.", ["F3"])]
        for text, ids in rejected:
            with self.subTest(text=text):
                self.assertIsNotNone(risk_agent.check_statement({"text": text, "fact_ids": ids}, facts))
        for text, ids in accepted:
            with self.subTest(text=text):
                self.assertIsNone(risk_agent.check_statement({"text": text, "fact_ids": ids}, facts,
                                                             context_numbers=(29.4242, -98.4933)))

    def test_claim_checker_withholds_reworded_safety_claims(self):
        facts = {"F1": {"id": "F1", "label": "FEMA NFHL flood zone", "unit": None, "value": "X"}}
        for text in ("There is no flood risk here.", "This home is relatively safe from wildfire.",
                     "Flooding is impossible at this site.", "The site won't flood.",
                     "Zero chance of flooding.", "The overall flood risk score is low.",
                     "This location can't be damaged.", "The property is risk free."):
            with self.subTest(text=text):
                self.assertIsNotNone(risk_agent.check_statement({"text": text, "fact_ids": ["F1"]}, facts))
        ok = {"text": "FEMA maps the point in Zone X, outside the mapped 1% annual-chance floodplain.",
              "fact_ids": ["F1"]}
        self.assertIsNone(risk_agent.check_statement(
            {**ok, "text": "FEMA maps the point in Zone X; no active alerts were returned."}, facts))

    def test_narration_failure_keeps_the_assessment(self):
        class Boom:
            class beta:
                class messages:
                    @staticmethod
                    def create(**_):
                        raise TypeError("Could not resolve authentication method")
        with unittest.mock.patch.object(risk_agent, "assess", new=unittest.mock.AsyncMock(return_value=self.record)), \
                unittest.mock.patch("anthropic.Anthropic", return_value=Boom()), \
                tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()):
            path = Path(tmp) / "record.json"
            code = risk_agent.main(["--lat", "29.4", "--lon", "-98.5", "--narrate", "--json", str(path)])
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn(code, (0, 1))
        self.assertIn("TypeError", saved["narrative"]["error"])
        self.assertIn("No explanation was added", out.getvalue())

    def test_truncated_or_paused_model_output_raises_model_stopped(self):
        scope = {"address": None, "lat": 1, "lon": 1, "hazards": ["flood"], "purpose": "", "clarification_needed": None}
        for reason in ("pause_turn", "model_context_window_exceeded", "max_tokens"):
            with self.subTest(reason=reason), self.assertRaises(risk_agent.ModelStopped):
                risk_agent.interpret_request("x", client=FakeClaude(scope, stop_reason=reason))

    def test_cli_rejects_nan_and_mixed_location_inputs(self):
        for argv in (["--lat", "nan", "--lon", "1"], ["--address", "1 Main St", "--lat", "1", "--lon", "2"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                risk_agent.main(argv)

    def test_narrate_keeps_only_checked_statements(self):
        draft = {"statements": [
            {"hazard": "flood", "text": f"FEMA maps the point in Zone {self.zone['value']}.", "fact_ids": [self.zone["id"]]},
            {"hazard": "flood", "text": "Expect 3 feet of water every year.", "fact_ids": [self.zone["id"]]}]}
        fake = FakeClaude(draft)
        risk_agent.narrate(self.record, client=fake)
        self.assertEqual(len(self.record["narrative"]["accepted"]), 1)
        self.assertEqual(len(self.record["narrative"]["rejected"]), 1)
        request = fake.requests[0]
        self.assertEqual(request["model"], "claude-opus-5-5")
        self.assertEqual(request["output_config"]["format"]["type"], "json_schema")
        self.assertNotIn("envelope", request["messages"][0]["content"])  # only extracted facts reach the model

    def test_interpret_request_is_validated_and_refusals_raise(self):
        scope = {"address": "100 Dolorosa St, San Antonio, TX", "lat": None, "lon": None,
                 "hazards": ["flood"], "purpose": "home purchase", "clarification_needed": None}
        result, _ = risk_agent.interpret_request("Flood risk at 100 Dolorosa St?", client=FakeClaude(scope))
        self.assertEqual(result["hazards"], ["flood"])
        bad, _ = risk_agent.interpret_request("x", client=FakeClaude({**scope, "lat": 500, "lon": 0}))
        self.assertIsNotNone(bad["clarification_needed"])
        with self.assertRaises(risk_agent.ModelStopped):
            risk_agent.interpret_request("x", client=FakeClaude(scope, stop_reason="refusal"))


if __name__ == "__main__":
    unittest.main()
