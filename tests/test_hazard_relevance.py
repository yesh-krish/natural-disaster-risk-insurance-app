import unittest
from unittest.mock import patch

from hazard_engine import ModelUnavailable
from hazard_relevance import county_at, most_relevant, rank_county, usable_curve


def curve(peril, frequency=2, severity=1):
    return {"peril": peril, "status": "available", "points": [{"frequency_pct": frequency, "severity": severity}],
            "latitude": 29.4, "longitude": -98.5}


def missing(peril):
    return {"peril": peril, "status": "unavailable", "points": [], "reason": "No local model cell."}


class RelevanceTests(unittest.TestCase):
    def county(self, **values):
        return {"COUNTY": "Example", "STATE": "Texas", "NRI_VER": "December 2025", **values}, "a" * 64

    def test_ranking_uses_shared_loss_units_and_preserves_missing_values(self):
        ranks = rank_county({"IFLD_EALT": 80, "CFLD_EALT": 40, "HRCN_EALT": 100,
                             "ERQK_EALT": None, "WFIR_EALT": -9999})
        self.assertEqual([(r["peril"], r["annual_loss_total"]) for r in ranks],
                         [("flood", 120), ("cyclone", 100), ("wildfire", None), ("earthquake", None)])

    def test_largest_supported_county_hazard_with_local_curve_wins(self):
        with patch("hazard_relevance.county_at", return_value=self.county(WFIR_EALT=500, ERQK_EALT=100)), \
             patch("hazard_relevance.disaster_at", side_effect=lambda peril, *args: curve(peril)) as fetch:
            data = most_relevant(29.4, -98.5)
        self.assertEqual(data["peril"], "wildfire")
        self.assertEqual(data["selection"]["method"], "fema_county_expected_annual_loss")
        self.assertEqual(fetch.call_count, 1)

    def test_missing_inland_flood_curve_shows_rainfall_context(self):
        with patch("hazard_relevance.county_at", return_value=self.county(IFLD_EALT=500, ERQK_EALT=100)), \
             patch("hazard_relevance.disaster_at", side_effect=lambda peril, *args: missing(peril) if peril == "flood" else curve(peril)):
            data = most_relevant(29.4, -98.5)
        self.assertEqual(data["peril"], "rainfall")
        self.assertEqual(data["selection"]["method"], "inland_flood_rainfall_context")
        self.assertIn("does not measure local inundation", data["selection"]["explanation"])
        self.assertFalse(data["selection"]["checks"][0]["usable"])

    def test_coastal_flood_missing_does_not_get_mapped_to_rainfall(self):
        with patch("hazard_relevance.county_at", return_value=self.county(CFLD_EALT=500, IFLD_EALT=0, ERQK_EALT=100)), \
             patch("hazard_relevance.disaster_at", side_effect=lambda peril, *args: missing(peril) if peril == "flood" else curve(peril)) as fetch:
            data = most_relevant(29.4, -98.5)
        self.assertEqual(data["peril"], "earthquake")
        self.assertEqual([c.args[0] for c in fetch.call_args_list], ["flood", "earthquake"])

    def test_zero_exceedances_do_not_win_automatic_selection(self):
        with patch("hazard_relevance.county_at", return_value=self.county(HRCN_EALT=500, ERQK_EALT=100)), \
             patch("hazard_relevance.disaster_at", side_effect=lambda peril, *args: curve(peril, frequency=0 if peril == "cyclone" else 2)):
            data = most_relevant(29.4, -98.5)
        self.assertEqual(data["peril"], "earthquake")
        self.assertIn("No positive", data["selection"]["checks"][0]["reason"])

    def test_provider_failure_does_not_hide_next_ranked_curve(self):
        def fetch(peril, *args):
            if peril == "wildfire":
                raise OSError("Fire service offline")
            return curve(peril)
        with patch("hazard_relevance.county_at", return_value=self.county(WFIR_EALT=500, ERQK_EALT=100)), \
             patch("hazard_relevance.disaster_at", side_effect=fetch):
            data = most_relevant(29.4, -98.5)
        self.assertEqual(data["peril"], "earthquake")
        self.assertIn("offline", data["selection"]["checks"][0]["reason"])

    def test_county_outage_fallback_is_explicit_not_fabricated_rank(self):
        with patch("hazard_relevance.county_at", side_effect=OSError("FEMA offline")), \
             patch("hazard_relevance.disaster_at", side_effect=lambda peril, *args: curve(peril)):
            data = most_relevant(29.4, -98.5)
        self.assertEqual(data["peril"], "rainfall")
        self.assertEqual(data["selection"]["method"], "available_data_fallback")
        self.assertEqual(data["selection"]["rankings"], [])
        self.assertIn("FEMA offline", data["selection"]["ranking_unavailable_reason"])

    def test_all_missing_models_returns_unavailable_with_no_graph(self):
        with patch("hazard_relevance.county_at", return_value=self.county()), \
             patch("hazard_relevance.disaster_at", side_effect=lambda peril, *args: missing(peril)):
            data = most_relevant(29.4, -98.5)
        self.assertEqual(data["peril"], "auto")
        self.assertEqual(data["status"], "unavailable")
        self.assertEqual(data["points"], [])
        self.assertEqual(len(data["selection"]["checks"]), 5)

    def test_invalid_points_are_not_usable(self):
        for frequency, severity in [(0, 2), (2, None), (2, -1), (float("nan"), 2), (True, 2)]:
            self.assertFalse(usable_curve(curve("flood", frequency, severity)))

    def test_county_query_uses_longitude_latitude_and_rejects_ambiguous_boundaries(self):
        county_at.cache_clear()
        with patch("hazard_relevance.fetch_json", return_value=({"features": [{"attributes": {"COUNTY": "Example"}}]}, "a" * 64)) as fetch:
            attributes, digest = county_at(29.4, -98.5, 0)
        self.assertEqual(attributes["COUNTY"], "Example")
        self.assertIn("geometry=-98.5%2C29.4", fetch.call_args.args[0])
        self.assertIn("IFLD_EALT", fetch.call_args.args[0])
        with patch("hazard_relevance.fetch_json", return_value=({"features": [{}, {}]}, "a" * 64)):
            with self.assertRaises(ModelUnavailable):
                county_at(30, -98, 0)
        county_at.cache_clear()

    def test_invalid_coordinate_and_site_class_do_not_query_government(self):
        with patch("hazard_relevance.county_at") as fetch:
            for lat, lon, site_class in [(91, 0, "BC"), (0, float("nan"), "BC"), (29, -98, "bad")]:
                with self.assertRaises(ValueError):
                    most_relevant(lat, lon, site_class)
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
