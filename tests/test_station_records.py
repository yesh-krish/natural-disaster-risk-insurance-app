import calendar
import json
import unittest
from unittest.mock import patch

import historical_heat as ghcn
import station_records as sr

ID = "USW00012921"
SOURCE = {"url": "https://example.test", "sha256": "a" * 64, "fetched_at": "2026-11-22T00:00:00+00:00"}


def month(year, number, element="PRCP", value=50, station=ID, overrides=None):
    days = calendar.monthrange(year, number)[1]
    values = [f"{value:5d}   " if day <= days else "-9999   " for day in range(1, 32)]
    for day, replacement in (overrides or {}).items():
        values[day - 1] = replacement
    return f"{station}{year:04d}{number:02d}{element}" + "".join(values) + "\n"


def record(element, years, wet=None):
    """Every day reads 5.0; in years listed in wet, 1 March reads 120.0."""
    wet = wet or set()
    return "".join(month(y, m, element, overrides={1: " 1200   "} if (y in wet and m == 3) else None)
                   for y in years for m in range(1, 13))


def station(identifier, lon=-98.5):
    return {"id": identifier, "name": "Station " + identifier, "latitude": 29.4, "longitude": lon,
            "url": ghcn.ROOT_URL + "all/" + identifier + ".dly"}


PEAKS = "\n".join([
    "# USGS peak file",
    "agency_cd\tsite_no\tpeak_dt\tpeak_va\tpeak_cd\tgage_ht\tgage_ht_cd\tag_dt\tag_gage_ht\tag_gage_ht_cd",
    "5s\t15s\t10d\t8s\t33s\t8s\t27s\t10d\t8s\t27s",
    "USGS\t08178000\t1900-06-01\t99999\t7\t40.0\t\t\t\t",            # historic peak: left out
    "USGS\t08178000\t2000-10-05\t500\t\t9.0\t\t\t\t",               # water year 2001
    "USGS\t08178000\t2001-05-01\t800\t\t12.0\t\t2001-06-01\t14.5\t", # same water year; separate maximum level is higher
    "USGS\t08178000\t2002-05-01\t300\t\t30.0\t3\t\t\t",             # different site/datum: left out
    "USGS\t08178000\t2003-05-01\t\t\t6.0\t\t\t\t",                  # level without flow still counts
]) + "\n"


class GhcnRecordTests(unittest.TestCase):
    def test_year_counts_with_95_percent_days_and_skips_flagged_values(self):
        raw = "".join(month(1991, m, overrides={day: "-9999   " for day in range(1, 19)} if m == 1 else None) for m in range(1, 13))
        raw += "".join(month(1992, m, overrides={day: "-9999   " for day in range(1, 20)} if m == 1 else None) for m in range(1, 13))
        raw += "".join(month(1993, m, overrides={2: " 3000 X "} if m == 6 else None) for m in range(1, 13))
        years, rejected = sr.ghcn_annual_maxima(raw, ID, "PRCP", 1991, 1993)
        self.assertEqual(rejected, 0)
        self.assertEqual((years[0]["valid_days"], years[0]["counted"]), (347, True), "18 missing days still counts")
        self.assertEqual((years[1]["valid_days"], years[1]["counted"]), (347, False), "leap year needs 348 days")
        self.assertEqual(years[2]["maximum"], 5.0, "quality-flagged 300 mm day is skipped")
        with self.assertRaises(ValueError):
            sr.ghcn_annual_maxima(month(1991, 1, station="USW00099999"), ID, "PRCP", 1991, 1991)

    def test_wind_guard_removes_implausible_values_and_tallies_them(self):
        raw = "".join(month(2000, m, "WSF5", 200, overrides={3: "  657   "} if m == 7 else None) for m in range(1, 13))
        years, rejected = sr.ghcn_annual_maxima(raw, ID, "WSF5", 2000, 2000, max_valid=60)
        self.assertEqual(rejected, 1)
        self.assertEqual(years[0]["maximum"], 20.0)

    def lookup(self, kind, stations, spans, texts, n=5):
        def text(identifier, day):
            outcome = texts[identifier]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome, SOURCE
        index = ({row["id"]: row for row in stations}, spans, [SOURCE])
        with patch.object(ghcn, "ghcn_index", return_value=index), patch.object(ghcn, "station_text", side_effect=text):
            return sr.station_records(kind, 29.4, -98.5, n)

    def test_rainfall_rows_are_separate_nearest_first_and_capped(self):
        rows = [station(f"USW{i:08d}", lon=-98.5 - i * 0.01) for i in range(12)]
        spans = {(row["id"], "PRCP"): (1950, 2026) for row in rows}
        spans[("USW00000011", "PRCP")] = (2010, 2026)  # under 20 years inside the window: screened out
        spans[("USW00000000", "TMAX")] = (1950, 2026)
        texts = {row["id"]: record("PRCP", range(1991, 2021), wet=set(range(1991, 2001))).replace(ID, row["id"]) for row in rows}
        texts["USW00000001"] = OSError("NOAA offline")
        texts["USW00000002"] = record("PRCP", range(1991, 2001)).replace(ID, "USW00000002")
        result = self.lookup("rainfall", rows, spans, texts, n=50)
        self.assertEqual((result["requested_stations"], result["station_limit"], result["stations_within_radius"]), (50, 10, 11))
        self.assertEqual([row["id"] for row in result["stations"]], [row["id"] for row in rows[:10]])
        first = result["stations"][0]
        self.assertEqual((first["status"], first["counted_years"], first["highest"], first["median"]), ("available", 30, 120.0, 5.0))
        self.assertAlmostEqual({p["severity"]: p for p in first["points"]}[120.0]["frequency_pct"], 100 / 3)
        self.assertEqual(result["stations"][1]["status"], "source_unavailable")
        self.assertEqual((result["stations"][2]["status"], result["stations"][2]["counted_years"]), ("insufficient_years", 10))
        self.assertNotIn("frequency_pct", result, "monitors are never averaged")
        json.dumps(result, allow_nan=False)

    def test_wind_counts_sustained_wind_on_storm_days_only(self):
        """2004–2025 WSF2: 2005 storm day 30 m/s; 2008 storm day has no reading (year left out);
        an all-year 25 m/s thunderstorm-type value outside storm days never counts."""
        row = station(ID)
        def day_value(y, m):
            overrides = {}
            if (y, m) == (2005, 8):
                overrides[29] = "  300   "
            if (y, m) == (2008, 9):
                overrides[13] = "-9999   "
            if (y, m) == (2010, 5):
                overrides[1] = "  250   "  # strong non-storm wind
            if (y, m) == (2012, 1):
                overrides[4] = "  657   "  # implausible early-ASOS value
            return overrides
        text = "".join(month(y, m, "WSF2", 100, overrides=day_value(y, m)) for y in range(2004, 2026) for m in range(1, 13))
        storm_days = {"2005-08-29", "2008-09-13", "2017-08-26"}
        with patch("disaster_data.cyclone_footprint_days", return_value=(storm_days, 2025)) as footprint:
            result = self.lookup("wind", [row], {(ID, "WSF2"): (1996, 2026)}, {ID: text})
        footprint.assert_called_once_with(row["latitude"], row["longitude"])
        only = result["stations"][0]
        self.assertEqual(result["period"], {"start": 2004, "end": 2025, "window_years": 22})
        self.assertEqual((only["counted_years"], only["storm_years"], only["storm_years_missing_readings"]), (21, 3, 1),
                         "2008 is left out: no reading on its storm day")
        maxima = only["annual_maxima"]
        self.assertEqual((max(maxima), maxima.count(10.0), maxima.count(0.0)), (30.0, 1, 19),
                         "storm years take the storm-day value; other years count as 0, even with strong non-storm wind")
        self.assertEqual(only["rejected_implausible_days"], 1)
        self.assertIn("2-minute sustained", " ".join(result["notes"]))

    def test_footprint_days_use_the_main_curve_rule(self):
        import disaster_data
        # (event, year, lat, lon, wind kt, 12 radii NE/SE/SW/NW for 34/50/64 kt, date)
        records = [("AL012005", 2005, 29.0, -95.0, 60, (100, 100, 100, 100, 30, 30, 30, 30, 0, 0, 0, 0), "20050829"),
                   ("AL022006", 2006, 25.0, -90.0, 60, (50, 50, 50, 50, 0, 0, 0, 0, 0, 0, 0, 0), "20060901"),   # too far
                   ("AL032007", 2007, 29.0, -95.0, 30, (100, 100, 100, 100, 0, 0, 0, 0, 0, 0, 0, 0), "20070901")]  # below 34 kt
        with patch("disaster_data.cyclone_catalog", return_value=(records, 2025, ["src"])):
            days, end = disaster_data.cyclone_footprint_days(29.5, -95.2)
        self.assertEqual((days, end), ({"2005-08-29"}, 2025))

    def test_invalid_kind_and_count(self):
        for kind, n in (("earthquake", 5), ("rainfall", 0), ("rainfall", 51), ("rainfall", True)):
            with self.assertRaises(ValueError):
                sr.station_records(kind, 29.4, -98.5, n)


class FloodRecordTests(unittest.TestCase):
    def test_annual_stage_uses_highest_level_and_drops_historic_and_datum_readings(self):
        by_year, datum_excluded, historic = sr.annual_stages(PEAKS, "08178000")
        self.assertEqual({year: entry["stage"] for year, entry in by_year.items()}, {2001: 14.5, 2003: 6.0})
        self.assertEqual((by_year[2001]["date"], datum_excluded, historic), ("2001-06-01", 1, 1))
        with self.assertRaises(ValueError):
            sr.annual_stages(PEAKS, "08170000")
        self.assertEqual(sr.datum_offset(by_year, [("2001-06-01", 14.0), ("1999-01-01", 99.0)]), 0.5)

    def test_flood_rows_compare_each_gauge_with_its_own_flood_stages(self):
        gauges = [{"lid": "NEAR1", "name": "Near gauge", "latitude": 29.4, "longitude": -98.5},
                  {"lid": "NOSTG", "name": "No flood stage", "latitude": 29.4, "longitude": -98.51},
                  {"lid": "LAKE1", "name": "Reservoir", "latitude": 29.4, "longitude": -98.52},
                  {"lid": "FAR01", "name": "Far gauge", "latitude": 29.4, "longitude": -98.6},
                  {"lid": "AWAY1", "name": "Too far", "latitude": 31.0, "longitude": -98.5}]
        details = {"NEAR1": {"stages": {"action": 8.0, "minor": 10.0, "moderate": 15.0, "major": 20.0}, "units": "ft",
                             "usgs": "08000001", "crests": [("1990-05-01", 25.0)]},
                   "NOSTG": {"stages": {"action": 5.0}, "units": "ft", "usgs": "08000002", "crests": []},
                   "LAKE1": {"stages": {"minor": 940.0}, "units": "ft", "usgs": "08000003", "crests": []},
                   "FAR01": {"stages": {"minor": 12.0}, "units": "ft", "usgs": "08000004", "crests": [("1990-05-01", 18.0)]}}
        header = "agency_cd\tsite_no\tpeak_dt\tpeak_va\tpeak_cd\tgage_ht\n5s\t15s\t10d\t8s\t33s\t8s\n"

        def peaks(site, flood_stage):
            return header + "".join(f"USGS\t{site}\t{1980 + i}-05-01\t100\t\t{flood_stage if i == 10 else 5.0 + (i % 3)}\n" for i in range(25))
        texts = {"08000001": peaks("08000001", 25.0), "08000004": peaks("08000004", 30.0), "08000003": header}
        with patch.object(sr, "nwps_gauges", return_value=(gauges, SOURCE)), \
             patch.object(sr, "nwps_gauge", side_effect=lambda lid, day: (details[lid], SOURCE)), \
             patch.object(sr, "usgs_peaks", side_effect=lambda site, day: (texts[site], SOURCE)):
            result = sr.station_records("flood", 29.4, -98.5, 5)
        rows = result["stations"]
        self.assertEqual([row["id"] for row in rows], ["NEAR1", "FAR01"], "no minor stage, no river levels and >100 km are not listed")
        near, far = rows
        self.assertEqual((near["status"], near["counted_years"], near["flood_stages"]["minor"]), ("available", 25, 10.0))
        self.assertEqual(sum(v >= near["flood_stages"]["minor"] for v in near["annual_maxima"]), 1)
        self.assertEqual((near["datum_check_ft"], near["datum_warning"]), (0.0, False))
        self.assertEqual((far["datum_check_ft"], far["datum_warning"]), (12.0, True), "NWS and USGS disagree on the same flood")
        self.assertEqual((result["unit"], result["stations_within_radius"]), ("ft", 4))
        self.assertIn("water.noaa.gov/gauges/NEAR1", near["url"])
        json.dumps(result, allow_nan=False)

    def test_downloads_retry_network_errors(self):
        calls = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                return b"ok"

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) < 3:
                raise OSError("temporary")
            return Response()
        with patch.object(sr.urllib.request, "urlopen", side_effect=opener), patch.object(sr.time, "sleep"):
            text, _ = sr._download("https://example.test/x")
        self.assertEqual((text, len(calls)), ("ok", 3))
        with patch.object(sr.urllib.request, "urlopen", side_effect=OSError("down")), patch.object(sr.time, "sleep"), \
             self.assertRaises(OSError):
            sr._download("https://example.test/x")


class EndpointTests(unittest.TestCase):
    def test_station_records_endpoint_validates_inputs(self):
        from tests.test_api_v1 import http_request
        payload = {"kind": "wind", "stations": []}
        with patch.object(sr, "station_records", return_value=payload) as provider:
            status, _, body = http_request("/api/station-records?kind=wind&lat=29.4&lon=-98.5&n=12")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), payload)
            provider.assert_called_once_with("wind", 29.4, -98.5, 12)
            for query in ("kind=heat&lat=29.4&lon=-98.5", "kind=wind&lat=91&lon=0", "kind=wind&lat=29.4&lon=-98.5&n=0",
                          "kind=wind&lat=29.4&lon=-98.5&n=51", "lat=29.4&lon=-98.5"):
                self.assertEqual(http_request("/api/station-records?" + query)[0], 400, query)
        with patch.object(sr, "station_records", side_effect=OSError("USGS offline")):
            self.assertEqual(http_request("/api/station-records?kind=flood&lat=29.4&lon=-98.5")[0], 502)


if __name__ == "__main__":
    unittest.main()
