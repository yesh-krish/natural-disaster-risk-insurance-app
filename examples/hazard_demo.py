"""Run with: python examples/hazard_demo.py (illustrative data only)."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hazard_engine import (HazardCurve, Observation, Policy, StormCatalog, StormEvent,
                           Tier, benchmark_quote, evaluate_trigger, wildfire_curve)

tiers = (Tier({"pga_pct_g": 10}, 10000), Tier({"pga_pct_g": 30}, 30000))
curve = HazardCurve("pga_pct_g", ((10, 0.02), (30, 0.005)), "annual_rate", "log_log")
quote = benchmark_quote(curve, tiers, 30000, fixed_expense=50, risk_load=0.2, expense_ratio=0.15)
now = datetime.now(timezone.utc)
policy = Policy("demo-policy", "demo-location", "earthquake", now - timedelta(days=180),
                now + timedelta(days=185), tiers, 30000,
                {"pga_pct_g": "demo-site-pga-v1"}, {"pga_pct_g": frozenset({"demo-sensor"})})
observation = Observation("demo-location", "earthquake", "demo-event", "pga_pct_g",
                          "demo-site-pga-v1", 35, "%g", now - timedelta(minutes=10),
                          now - timedelta(minutes=9), now, "demo-sensor", "a" * 64,
                          "accepted", True, "1")
storm = StormCatalog((StormEvent("demo-storm-1", 200, 70, 0.01),
                      StormEvent("demo-storm-2", 100, 90, 0.02)), "synthetic")
fire = wildfire_curve(0.01, ((1, 0.8), (2, 0.5), (4, 0.1)))
print(json.dumps({"data_status": "illustrative_not_location_risk", "earthquake_quote": quote,
                  "trigger": evaluate_trigger(policy, [observation], "demo-event", now),
                  "storm_AND_aep": storm.aep({"rain_24h_mm": 150, "wind_gust_kn": 60}),
                  "wildfire_2m_aep": fire.aep({"flame_length_m": 2})}, indent=2))
