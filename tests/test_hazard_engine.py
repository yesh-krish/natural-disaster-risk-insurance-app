import math
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from hazard_engine import (HazardCurve, ModelUnavailable, Observation, Policy, StormCatalog,
                           StormEvent, Tier, aep_to_rate, benchmark_quote,
                           daily_fire_probabilities, evaluate_trigger, rate_to_aep,
                           wildfire_curve)


class ProbabilityTests(unittest.TestCase):
    def test_rate_and_annual_probability_are_distinct(self):
        rate_curve = HazardCurve("inundation_m", ((1, .1), (2, .01)), "annual_rate")
        aep_curve = HazardCurve("inundation_m", ((1, .1), (2, .01)), "annual_probability")
        self.assertAlmostEqual(rate_curve.aep({"inundation_m": 2}), 1 - math.exp(-.01))
        self.assertEqual(aep_curve.aep({"inundation_m": 2}), .01)
        self.assertAlmostEqual(rate_to_aep(aep_to_rate(.01)), .01)

    def test_small_rate_is_not_lost_to_cancellation(self):
        self.assertAlmostEqual(rate_to_aep(1e-20) / 1e-20, 1)

    def test_usgs_log_log_interpolation(self):
        curve = HazardCurve("pga_pct_g", ((1, .1), (100, .001)), "annual_rate", "log_log")
        self.assertAlmostEqual(curve.frequency(10), .01)

    def test_flood_depth_log_probability_interpolation(self):
        curve = HazardCurve("inundation_m", ((0.5, .1), (1.5, .01)), "annual_probability")
        self.assertAlmostEqual(curve.aep({"inundation_m": 1}), math.sqrt(.001))

    def test_unknown_tail_cannot_be_priced(self):
        curve = HazardCurve("pga_pct_g", ((1, .1), (10, 0)), "annual_rate", "log_log")
        for threshold in (.5, 5, 20):
            with self.assertRaises(ModelUnavailable):
                curve.aep({"pga_pct_g": threshold})

    def test_bad_frequency_and_nonmonotone_curve_rejected(self):
        for points in (((1, .1), (1, .01)), ((1, .01), (2, .1)),
                       ((1, math.nan), (2, .01)), ((1, 1.2), (2, .01))):
            with self.assertRaises(ValueError):
                HazardCurve("inundation_m", points, "annual_probability")

    def test_nested_annual_tier_expected_value(self):
        curve = HazardCurve("inundation_m", ((1, .1), (2, .01)), "annual_probability")
        quote = benchmark_quote(curve, [Tier({"inundation_m": 1}, 1000),
                                       Tier({"inundation_m": 2}, 3000)], 3000,
                                fixed_expense=20, risk_load=.2, expense_ratio=.1)
        self.assertAlmostEqual(quote["expected_annual_payout"], 120)
        self.assertAlmostEqual(quote["benchmark_premium"], 164 / .9)

    def test_invalid_nested_schedule_or_loads_rejected(self):
        curve = HazardCurve("inundation_m", ((1, .1), (2, .01)), "annual_probability")
        with self.assertRaises(ValueError):
            benchmark_quote(curve, [Tier({"inundation_m": 2}, 1000),
                                    Tier({"inundation_m": 1}, 3000)], 3000)
        with self.assertRaises(ValueError):
            benchmark_quote(curve, [Tier({"inundation_m": 1}, 1000)], 3000, expense_ratio=1)

    def test_same_storm_and_vs_or(self):
        # Rain and wind threshold breaches occur in different events.
        catalog = StormCatalog((StormEvent("wet", 200, 20, .1),
                                StormEvent("windy", 20, 100, .2)), "empirical")
        thresholds = {"rain_24h_mm": 150, "wind_gust_kn": 70}
        with self.assertRaises(ModelUnavailable):
            catalog.aep(thresholds, "AND")
        self.assertAlmostEqual(catalog.aep(thresholds, "OR"), rate_to_aep(.3))
        self.assertIsNone(catalog.surface([150], [70])[0]["annual_exceedance_probability"])

    def test_duplicate_storm_does_not_double_count(self):
        with self.assertRaises(ValueError):
            StormCatalog((StormEvent("a", 100, 50, .1), StormEvent("a", 200, 70, .1)))

    def test_wildfire_curve_and_annual_weather_normalization(self):
        curve = wildfire_curve(.02, ((1, 1), (2, .5), (4, .1)))
        self.assertEqual(curve.aep({"flame_length_m": 2}), .01)
        daily = daily_fire_probabilities(.02, [1] * 365)
        annual = -math.expm1(math.fsum(math.log1p(-p) for p in daily))
        self.assertAlmostEqual(annual, .02)


class TriggerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 11, 18, tzinfo=timezone.utc)
        self.policy = Policy("p", "site", "earthquake", self.now - timedelta(days=180),
                             self.now + timedelta(days=185),
                             (Tier({"pga_pct_g": 10}, 1000), Tier({"pga_pct_g": 30}, 3000)),
                             3000, {"pga_pct_g": "site-pga-v1"},
                             {"pga_pct_g": frozenset({"approved"})})
        self.obs = Observation("site", "earthquake", "e", "pga_pct_g", "site-pga-v1",
                               30, "%g", self.now - timedelta(minutes=10),
                               self.now - timedelta(minutes=9), self.now,
                               "approved", "b" * 64, "accepted", True, "1")

    def test_threshold_equality_and_annual_top_up(self):
        result = evaluate_trigger(self.policy, [self.obs], "e", self.now, already_paid=1000)
        self.assertTrue(result["triggered"])
        self.assertEqual(result["payout_due"], 2000)

    def test_repeat_or_lower_tier_cannot_pay_twice(self):
        result = evaluate_trigger(self.policy, [self.obs], "e", self.now, already_paid=3000)
        self.assertFalse(result["triggered"])
        self.assertEqual(result["payout_due"], 0)
        lower = replace(self.obs, value=15, event_id="later")
        result = evaluate_trigger(self.policy, [lower], "later", self.now, already_paid=3000)
        self.assertFalse(result["triggered"])

    def test_source_unit_identity_quality_and_missing_guards(self):
        changes = ({"source_id": "unknown"}, {"unit": "g"}, {"event_id": "other"},
                   {"location_id": "other"}, {"definition_id": "other"}, {"peril": "flood"},
                   {"quality": "provisional"}, {"finalized": False}, {"value": None},
                   {"value": math.nan}, {"value": True}, {"artifact_sha256": "invalid"})
        for change in changes:
            with self.subTest(change=change):
                result = evaluate_trigger(self.policy, [replace(self.obs, **change)], "e", self.now)
                self.assertFalse(result["triggered"])
                self.assertEqual(result["status"], "insufficient_data")

    def test_expired_window_future_and_stale_publications(self):
        for change in ({"window_start": self.policy.starts_at - timedelta(seconds=1)},
                       {"issued_at": self.now + timedelta(seconds=1)},
                       {"window_end": self.policy.ends_at},
                       {"issued_at": self.now.replace(tzinfo=None)}):
            result = evaluate_trigger(self.policy, [replace(self.obs, **change)], "e", self.now)
            self.assertEqual(result["status"], "insufficient_data")
        result = evaluate_trigger(self.policy, [self.obs], "e", self.now + timedelta(days=2))
        self.assertEqual(result["status"], "insufficient_data")

    def test_missing_and_ambiguous_revisions(self):
        for readings in ([], [self.obs, replace(self.obs, revision="2")]):
            self.assertEqual(evaluate_trigger(self.policy, readings, "e", self.now)["status"],
                             "insufficient_data")

    def test_joint_storm_requires_same_event_complete_24h(self):
        policy = Policy("p", "site", "rain_wind", self.policy.starts_at, self.policy.ends_at,
                        (Tier({"rain_24h_mm": 150, "wind_gust_kn": 70}, 1000),), 1000,
                        {"rain_24h_mm": "rolling-24h-v1", "wind_gust_kn": "gust-v1"},
                        {"rain_24h_mm": frozenset({"approved"}),
                         "wind_gust_kn": frozenset({"approved"})})
        rain = replace(self.obs, peril="rain_wind", metric="rain_24h_mm", definition_id="rolling-24h-v1",
                       value=200, unit="mm", window_start=self.now-timedelta(days=1), window_end=self.now)
        wind = replace(self.obs, peril="rain_wind", metric="wind_gust_kn", definition_id="gust-v1",
                       value=80, unit="kn")
        self.assertTrue(evaluate_trigger(policy, [rain, wind], "e", self.now)["triggered"])
        for bad in (replace(wind, event_id="another-storm"),):
            self.assertFalse(evaluate_trigger(policy, [rain, bad], "e", self.now)["triggered"])
        short_rain = replace(rain, window_start=self.now - timedelta(hours=23))
        self.assertEqual(evaluate_trigger(policy, [short_rain, wind], "e", self.now)["status"],
                         "insufficient_data")


if __name__ == "__main__":
    unittest.main()
