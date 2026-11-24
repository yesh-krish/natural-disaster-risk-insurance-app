import calendar
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker
from api_spec import openapi_document
from api_v1 import comparison, dispatch
from area_hazard import area_disaster
from disaster_data import disaster_at
from hazard_engine import ModelUnavailable
import historical_heat as heat

ID = "USW00012921"
SOURCE = {"url": heat.ROOT_URL + "all/" + ID + ".dly", "sha256": "a"*64,
          "fetched_at": "2026-11-04T00:00:00+00:00"}


def month(year, number, value=350, *, station=ID, overrides=None):
    values = [f"{value:5d}  X" if day <= calendar.monthrange(year, number)[1] else "-9999   " for day in range(1, 32)]
    for day, replacement in (overrides or {}).items():
        values[day-1] = replacement
    return f"{station}{year:04d}{number:02d}TMAX" + "".join(values) + "\n"


def record(years=range(1991, 2021)):
    # Ten complete years reach 40 C, twenty reach 35 C; every day is repeated
    # to ensure frequency counts years, not hot days or months.
    return "".join(month(year, m, 400 if year % 3 == 0 else 350) for year in years for m in range(1, 13))


def station(identifier=ID, lat=29.4, lon=-98.5):
    return {"id": identifier, "name": "Historical station " + identifier, "latitude": lat, "longitude": lon,
            "url": heat.ROOT_URL + "all/" + identifier + ".dly"}


class HistoricalHeatTests(unittest.TestCase):
    def setUp(self):
        self.years = heat.annual_maxima(record(), ID)

    def lookup(self, years=None):
        with patch.object(heat, "station_catalog", return_value=([station()], [SOURCE])), \
             patch.object(heat, "station_years", return_value=(years if years is not None else self.years, SOURCE)):
            return disaster_at("heat", 29.4, -98.5)

    def test_daily_units_leap_days_source_dates_flags_and_complete_years(self):
        self.assertEqual(len(self.years), 30)
        self.assertTrue(all(row["complete"] for row in self.years))
        self.assertEqual(self.years[1]["expected_days"], 366)
        self.assertEqual(self.years[0]["expected_days"], 365)
        self.assertEqual(self.years[0]["maximum_c"], 35)
        self.assertEqual(self.years[0]["maximum_date"], "1991-12-31")
        self.assertEqual(self.years[0]["maximum_source_flag"], "X")
        raw = "".join(month(1992, m, value=0, overrides={29: "  100H X"} if m == 2 else {}) for m in range(1, 13))
        row = heat.annual_maxima(raw, ID)[1]
        self.assertTrue(row["complete"])
        self.assertEqual(row["maximum_c"], 10)
        self.assertEqual(row["maximum_date"], "1992-02-29")
        self.assertEqual(row["maximum_measurement_flag"], "H")

    def test_missing_or_quality_flagged_day_excludes_whole_year(self):
        for replacement in ["-9999   ", "  450IX "]:
            raw = "".join(month(1991, m, overrides={1: replacement} if m == 1 else {}) for m in range(1, 13))
            row = heat.annual_maxima(raw, ID)[0]
            self.assertEqual(row["valid_days"], 364)
            self.assertFalse(row["complete"])
            self.assertIsNone(row["maximum_c"], "partial-year maxima must not enter denominator")
            self.assertIsNone(row["maximum_date"])

    def test_truncated_duplicate_or_wrong_station_month_fails_closed(self):
        for raw in [month(1991, 1)[:100] + "\n", month(1991, 1)*2,
                    month(1991, 1, station="USW00099999")]:
            with self.assertRaises(ValueError):
                heat.annual_maxima(raw, ID)

    def test_empirical_annual_exceedance_inclusive_not_day_frequency(self):
        result = self.lookup()
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["unit"], "°C")
        levels = {point["severity"]: point for point in result["points"]}
        self.assertEqual(levels[35]["frequency_pct"], 100)
        self.assertAlmostEqual(levels[40]["frequency_pct"], 100/3)
        self.assertEqual(levels[40]["hit_years"], 10)
        self.assertEqual(levels[40]["exposure_years"], 30)
        self.assertEqual(result["baseline"]["complete_years"], 30)
        self.assertEqual(result["station"]["distance_km"], 0)
        self.assertEqual(result["frequency_basis"], "historical_annual_frequency")
        json.dumps(result, allow_nan=False)

    def test_excluded_years_change_denominator_and_19_years_withhold_curve(self):
        twenty = heat.annual_maxima(record(range(1991, 2011)), ID)
        result = self.lookup(twenty)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["baseline"]["complete_years"], 20)
        self.assertEqual(len(result["baseline"]["excluded_years"]), 10)
        self.assertTrue(all(p["exposure_years"] == 20 for p in result["points"]))
        result = self.lookup(heat.annual_maxima(record(range(1991, 2010)), ID))
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["points"], [])
        self.assertIn("20 complete years", result["reason"])

    def test_nearer_incomplete_station_rejection_is_disclosed_and_deterministic(self):
        first, second = station("USW00000001"), station(ID, lon=-98.6)
        empty = heat.annual_maxima("", first["id"])
        with patch.object(heat, "station_catalog", return_value=([second, first], [SOURCE])), \
             patch.object(heat, "station_years", side_effect=[(empty, SOURCE), (self.years, SOURCE)]):
            result = heat.heat_at(29.4, -98.5)
        self.assertEqual(result["station"]["id"], ID)
        self.assertEqual([r["eligible"] for r in result["station_selection"]["checked"]], [False, True])
        self.assertGreater(result["station"]["distance_km"], 0)

    def test_no_coverage_invalid_inputs_and_provider_failure(self):
        with patch.object(heat, "station_catalog", return_value=([station()], [SOURCE])), patch.object(heat, "station_years") as provider:
            result = disaster_at("heat", 0, 0)
            self.assertEqual(result["status"], "unavailable")
            self.assertIn("100 km", result["reason"])
            provider.assert_not_called()
        with patch.object(heat, "station_catalog") as provider:
            with self.assertRaises(ValueError):
                heat.heat_at(91, 0)
            provider.assert_not_called()
        with patch.object(heat, "station_catalog", return_value=([station()], [SOURCE])), \
             patch.object(heat, "station_years", side_effect=OSError("NOAA offline")):
            self.assertEqual(dispatch("/api/v1/hazards/heat", "lat=29.4&lon=-98.5")[0], 502)

    def test_point_only_area_does_not_average_shared_station_records(self):
        with patch.object(heat, "station_catalog") as provider:
            result = area_disaster(29, -99, 30, -98, "heat")
            self.assertEqual(result["status"], "unavailable")
            self.assertIn("Point mode", result["reason"])
            self.assertEqual(result["points"], [])
            provider.assert_not_called()

    def test_comparison_keeps_historical_basis_and_api_schema(self):
        data = self.lookup()
        rows = comparison(data, Decimal("30000"))
        self.assertEqual(rows["frequency_basis"], "historical_annual_frequency")
        self.assertEqual(rows["comparisons"][1]["expected_annual_contribution_usd"]["minimum"], 10000)
        with patch("api_v1.disaster_at", return_value=data):
            status, payload = dispatch("/api/v1/hazards/heat", "lat=29.4&lon=-98.5")
        self.assertEqual(status, 200)
        spec = openapi_document()
        schema = spec["paths"]["/api/v1/hazards/{peril}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        Draft202012Validator({**schema, "components": spec["components"]}, format_checker=FormatChecker()).validate(payload)
        self.assertEqual(dispatch("/api/v1/monitoring-stations", "peril=heat&lat=29.4&lon=-98.5")[0], 400)

    def test_station_comparison_lists_each_station_separately_and_caps_at_ten(self):
        stations = [station(f"USW{index:08d}", lon=-98.5 - index * 0.01) for index in range(12)]
        short = heat.annual_maxima(record(range(1991, 2001)), ID)
        def years(identifier, day):
            if identifier.endswith("01"):
                raise OSError("NOAA offline")
            return (short if identifier.endswith("02") else self.years), SOURCE
        with patch.object(heat, "station_catalog", return_value=(stations, [SOURCE])), \
             patch.object(heat, "station_years", side_effect=years) as provider:
            result = heat.station_comparison(29.4, -98.5, 50)
            self.assertEqual(provider.call_count, 10)
            self.assertEqual(len(heat.station_comparison(29.4, -98.5, 3)["stations"]), 3)
        self.assertEqual((result["requested_stations"], result["station_limit"], result["stations_within_radius"]), (50, 10, 12))
        rows = result["stations"]
        self.assertEqual([row["id"] for row in rows], [row["id"] for row in stations[:10]], "nearest first")
        self.assertEqual(rows[0]["status"], "available")
        self.assertEqual(rows[0]["annual_maxima_c"].count(40), 10)
        self.assertAlmostEqual({p["severity"]: p for p in rows[0]["points"]}[40]["frequency_pct"], 100/3)
        self.assertEqual(rows[1]["status"], "source_unavailable", "a download failure is not reported as missing coverage")
        self.assertIsNone(rows[1]["complete_years"])
        self.assertEqual((rows[2]["status"], rows[2]["complete_years"], rows[2]["points"]), ("insufficient_years", 10, []))
        self.assertNotIn("frequency_pct", result, "stations are never averaged")
        json.dumps(result, allow_nan=False)
        for bad in (0, 51, True, "5"):
            with self.assertRaises(ValueError):
                heat.station_comparison(29.4, -98.5, bad)

    def test_station_comparison_endpoint_validates_n(self):
        from tests.test_api_v1 import http_request
        with patch.object(heat, "station_catalog", return_value=([station()], [SOURCE])), \
             patch.object(heat, "station_years", return_value=(self.years, SOURCE)):
            status, _, body = http_request("/api/heat-stations?lat=29.4&lon=-98.5&n=12")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["stations"][0]["id"], ID)
            for query in ("lat=29.4&lon=-98.5&n=0", "lat=29.4&lon=-98.5&n=51", "lat=29.4&lon=-98.5&n=2.5", "lat=91&lon=0"):
                self.assertEqual(http_request("/api/heat-stations?" + query)[0], 400, query)
        with patch.object(heat, "station_catalog", side_effect=OSError("NOAA offline")):
            self.assertEqual(http_request("/api/heat-stations?lat=29.4&lon=-98.5")[0], 502)

    def test_catalog_metadata_inventory_units_and_baseline_screen(self):
        def metadata(identifier):
            return f"{identifier} {29.4:8.4f} {-98.5:9.4f} {100:6.1f} TX {'Test airport':30s} GSN     \n"
        def inventory(identifier, element="TMAX", start=1991, end=2020):
            return f"{identifier} {29.4:8.4f} {-98.5:9.4f} {element} {start:4d} {end:4d}\n"
        raw_metadata = metadata(ID) + metadata("USW00000002")
        raw_inventory = inventory(ID) + inventory("USW00000002", start=1992)
        heat.station_catalog.cache_clear()
        heat.ghcn_index.cache_clear()
        try:
            with patch.object(heat, "_resource", side_effect=lambda name, limit: (raw_inventory if "inventory" in name else raw_metadata, SOURCE)):
                stations, provenance = heat.station_catalog(0)
            self.assertEqual([row["id"] for row in stations], [ID])
            self.assertEqual(stations[0]["name"], "Test airport")
            self.assertEqual(len(provenance), 2)
        finally:
            heat.station_catalog.cache_clear()
            heat.ghcn_index.cache_clear()


if __name__ == "__main__":
    unittest.main()
