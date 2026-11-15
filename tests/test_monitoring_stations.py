import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from monitoring_stations import distance_km, nearest_hazard_stations, nearest_station, _candidates


class StationTests(unittest.TestCase):
    def test_search_expands_only_when_needed(self):
        near = dict(id="near", name="Near", latitude=29.41, longitude=-98.49)
        with patch("monitoring_stations._candidates", return_value=[near]) as candidates:
            nearest_station("flood", 29.4, -98.49)
        self.assertEqual([c.args[3] for c in candidates.call_args_list], [20])
        # A station in the bounding-box corner can lie outside the circle.
        outside = dict(id="outside", latitude=29.7, longitude=-98.49)
        with patch("monitoring_stations._candidates", side_effect=[[outside], [outside]]) as candidates:
            result = nearest_station("flood", 29.4, -98.49)
        self.assertEqual([c.args[3] for c in candidates.call_args_list], [20, 75])
        self.assertEqual(result["station"]["id"], "outside")
        with patch("monitoring_stations._candidates", return_value=[]) as candidates:
            nearest_station("rainfall", 29.4, -98.49)
        self.assertEqual([c.args[3] for c in candidates.call_args_list], [20, 75, 200])

    def test_nearest_n_hazard_stations_expand_until_enough_and_dedupe(self):
        near = dict(id="near", name="Near", latitude=29.41, longitude=-98.49)
        mid = dict(id="mid", name="Mid", latitude=29.7, longitude=-98.49)
        far = dict(id="far", name="Far", latitude=30.5, longitude=-98.49)
        with patch("monitoring_stations._candidates", side_effect=[[near], [near, mid, mid], [near, mid, far]]) as candidates:
            result = nearest_hazard_stations("flood", 29.4, -98.49, 3)
        self.assertEqual([c.args[3] for c in candidates.call_args_list], [20, 75, 200])
        self.assertEqual([s["id"] for s in result["stations"]], ["near", "mid", "far"])
        self.assertEqual((result["returned_count"], result["fewer_than_requested"]), (3, False))
        self.assertEqual(result["station"]["id"], "near")
        with patch("monitoring_stations._candidates", side_effect=[[near], [near, mid]]) as candidates:
            result = nearest_hazard_stations("flood", 29.4, -98.49, 2)
        self.assertEqual([c.args[3] for c in candidates.call_args_list], [20, 75], "stops once n stations are found")
        with patch("monitoring_stations._candidates", return_value=[near]):
            result = nearest_hazard_stations("earthquake", 29.4, -98.49, 5)
        self.assertEqual((result["returned_count"], result["requested_count"], result["fewer_than_requested"]), (1, 5, True))
        with patch("monitoring_stations._candidates", return_value=[near]):
            result = nearest_hazard_stations("fireline_intensity", 29.4, -98.49, 2)
        self.assertEqual(result["peril"], "fireline_intensity")
        self.assertIn("fireline intensity", result["message"])
        for bad in (0, 51, True, "x"):
            with self.assertRaises(ValueError):
                nearest_hazard_stations("flood", 29.4, -98.49, bad)

    def test_hazard_station_api_route(self):
        from api_spec import openapi_document
        from api_v1 import dispatch
        from jsonschema import Draft202012Validator, FormatChecker
        rows = [dict(id=f"S{i}", name=f"Gauge {i}", latitude=29.4 + i / 100, longitude=-98.49,
                     url="https://waterdata.usgs.gov/x") for i in range(1, 5)]
        with patch("monitoring_stations._candidates", return_value=rows):
            status, payload = dispatch("/api/v1/monitoring-stations", "peril=flood&n=3&lat=29.4&lon=-98.49")
        self.assertEqual(status, 200)
        self.assertEqual([s["id"] for s in payload["data"]["stations"]], ["S1", "S2", "S3"])
        self.assertEqual(payload["meta"]["request"]["n"], 3)
        spec = openapi_document()
        schema = spec["paths"]["/api/v1/monitoring-stations"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        Draft202012Validator({**schema, "components": spec["components"]}, format_checker=FormatChecker()).validate(payload)
        with patch("monitoring_stations._candidates", return_value=rows):
            legacy = dispatch("/api/v1/monitoring-stations", "peril=flood&lat=29.4&lon=-98.49")[1]["data"]
        self.assertNotIn("stations", legacy, "legacy single-station contract is unchanged")
        self.assertEqual(legacy["station"]["id"], "S1")

    def test_nearby_clicks_reuse_catalog_but_recompute_distance(self):
        raw = '# comment\nagency_cd\tsite_no\tstation_nm\tdec_lat_va\tdec_long_va\n5s\t15s\t50s\t16s\t16s\nUSGS\t0123\tRiver\t29.4\t-98.49\n'
        with patch("monitoring_stations._download", return_value=raw) as download:
            first = nearest_station("flood", 29.41, -98.49)
            second = nearest_station("flood", 29.42, -98.48)
        self.assertEqual(download.call_args_list[0], download.call_args_list[1])
        self.assertNotEqual(first["station"]["distance_km"], second["station"]["distance_km"])

    def test_http_contract(self):
        from server import Handler
        handler = Handler.__new__(Handler)
        responses = []
        handler.send_json = lambda status, data: responses.append((status, data))
        handler.path = "/api/monitoring-station?peril=flood&lat=29.4&lon=-98.49"
        with patch("monitoring_stations._candidates", return_value=[]):
            handler.do_GET()
        self.assertEqual(responses[-1][0], 200)
        self.assertEqual(responses[-1][1]["status"], "not_found")
        with patch("monitoring_stations.nearest_station", side_effect=OSError("offline")):
            handler.do_GET()
        self.assertEqual(responses[-1][0], 502)
        handler.path = "/api/monitoring-station?peril=flood&lat=nan&lon=0"
        handler.do_GET()
        self.assertEqual(responses[-1][0], 400)

    def test_distance_and_nearest(self):
        self.assertAlmostEqual(distance_km(0, 0, 0, 1), 111.195, places=2)
        self.assertAlmostEqual(distance_km(0, 179.9, 0, -179.9), 22.239, places=2)
        rows = [dict(id="far", name="far", latitude=31, longitude=-98),
                dict(id="near", name="near", latitude=29.41, longitude=-98.49),
                dict(id="invalid", latitude="", longitude="")]
        with patch("monitoring_stations._candidates", return_value=rows):
            result = nearest_station("flood", 29.4, -98.49)
        self.assertEqual(result["station"]["id"], "near")
        self.assertIn("watershed", result["message"])

    def test_missing_and_invalid(self):
        with patch("monitoring_stations._candidates", return_value=[]):
            for peril in ("rainfall", "earthquake", "wildfire"):
                self.assertEqual(nearest_station(peril, 29, -98)["status"], "not_found")
        for peril, lat, lon in [("auto", 0, 0), ("flood", float("nan"), 0), ("flood", 91, 0)]:
            with self.assertRaises(ValueError):
                nearest_station(peril, lat, lon)

    def test_no_station_outside_radius(self):
        with patch("monitoring_stations._candidates", return_value=[dict(id="far", latitude=40, longitude=-98)]):
            self.assertEqual(nearest_station("flood", 29, -98)["status"], "not_found")

    def test_wind_catalog_filter(self):
        with patch("monitoring_stations._download", return_value='[{"id":"KSAT","site":"San Antonio","lat":29.54,"lon":-98.48,"siteType":["METAR"]},{"id":"RADAR","siteType":[]}]'):
            rows = list(_candidates("cyclone", 29.4, -98.49))
        self.assertEqual([r["id"] for r in rows], ["KSAT"])

    def test_usgs_rdb_and_dateline(self):
        raw = '# comment\nagency_cd\tsite_no\tstation_nm\tdec_lat_va\tdec_long_va\n5s\t15s\t50s\t16s\t16s\nUSGS\t0123\tRiver\t29.4\t-98.49\n'
        with patch("monitoring_stations._download", return_value=raw) as download:
            rows = list(_candidates("flood", 29.4, -98.49))
            self.assertIn("parameterCd=00065", download.call_args.args[0])
            bbox = parse_qs(urlsplit(download.call_args.args[0]).query)["bBox"][0]
            self.assertTrue(all(len(value.split(".")[1]) <= 7 for value in bbox.split(",")))
        self.assertEqual(rows[0]["id"], "0123")
        with patch("monitoring_stations._download", return_value="[]") as download:
            list(_candidates("cyclone", 0, 179.9))
        self.assertEqual(download.call_count, 2)

    def test_seismic_epochs_identity_and_empty_response(self):
        raw = ('#Network | Station | Latitude | Longitude | Elevation | SiteName | StartTime | EndTime\n'
               'TX|TEST|29.41|-98.49|100|Seismic test|2020-01-01T00:00:00|\n'
               'US|TEST|29.42|-98.49|100|Other network|2020-01-01T00:00:00|2099-01-01T00:00:00\n'
               'TX|OLD|29.40|-98.49|100|Closed|2000-01-01T00:00:00|2020-01-01T00:00:00\n'
               'TX|FUTURE|29.40|-98.49|100|Future|2099-01-01T00:00:00|\n')
        with patch("monitoring_stations._download", return_value=raw) as download:
            result = nearest_station("earthquake", 29.4, -98.49)
            query = parse_qs(urlsplit(download.call_args.args[0]).query)
        self.assertEqual(result["station"]["id"], "TX.TEST")
        self.assertIn("ground motion", result["message"])
        self.assertEqual(query["starttime"], query["endtime"])
        self.assertEqual(query["channel"], ["?H?,?N?"])
        with patch("monitoring_stations._download", return_value=raw):
            self.assertEqual([r["id"] for r in _candidates("earthquake", 29.4, -98.49)], ["TX.TEST", "US.TEST"])
        for raw in ("", "#Network|Station|Latitude|Longitude|Elevation|SiteName|StartTime|EndTime\n"):
            with patch("monitoring_stations._download", return_value=raw):
                self.assertEqual(nearest_station("earthquake", 29.4, -98.49)["status"], "not_found")
        with patch("monitoring_stations._download", return_value="Service unavailable"):
            with self.assertRaises(OSError):
                nearest_station("earthquake", 29.4, -98.49)

    def test_raws_active_filter_pagination_and_links(self):
        def station(oid, status="A", **extra):
            return {"attributes": {"OBJECTID": oid, "WXID": str(oid), "StationName": "Fire weather",
                                   "Latitude": 29.41, "Longitude": -98.49, "Status": status, **extra}}
        pages = [{"features": [station(1), station(2, "I")], "exceededTransferLimit": True},
                 {"features": [station(3, NOAA_URL="https://www.weather.gov/wrh/timeseries?site=TEST")]}]
        with patch("monitoring_stations._download", side_effect=[json_string(p) for p in pages]) as download:
            rows = list(_candidates("wildfire", 29.4, -98.49, 20))
        self.assertEqual([r["id"] for r in rows], ["RAWS-1", "RAWS-3"])
        self.assertIn("objectIds=1", rows[0]["url"])
        self.assertTrue(rows[1]["url"].startswith("https://www.weather.gov/"))
        query = parse_qs(urlsplit(download.call_args.args[0]).query)
        self.assertEqual(query["resultOffset"], ["2"])
        self.assertEqual(query["where"], ["Status = 'A'"])
        with patch("monitoring_stations._download", return_value=json_string(pages[1])):
            result = nearest_station("wildfire", 29.4, -98.49)
        self.assertEqual(result["status"], "available")
        self.assertIn("not fire occurrence", result["message"])
        for payload in ({"error": {"code": 503}}, {}, {"features": [], "exceededTransferLimit": True}):
            with patch("monitoring_stations._download", return_value=json_string(payload)):
                with self.assertRaises(OSError):
                    nearest_station("wildfire", 29.4, -98.49)

    def test_new_networks_http_contract(self):
        from server import Handler
        handler = Handler.__new__(Handler)
        responses = []
        handler.send_json = lambda status, data: responses.append((status, data))
        for peril in ("earthquake", "wildfire"):
            handler.path = f"/api/monitoring-station?peril={peril}&lat=29.4&lon=-98.49"
            with patch("monitoring_stations._candidates", return_value=[dict(id="real-catalog-id", name="Station", latitude=29.41, longitude=-98.49)]):
                handler.do_GET()
            self.assertEqual(responses[-1][0], 200)
            self.assertEqual(responses[-1][1]["status"], "available")


def json_string(value):
    import json
    return json.dumps(value)


if __name__ == "__main__":
    unittest.main()
