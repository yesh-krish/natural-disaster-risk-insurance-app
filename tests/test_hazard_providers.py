import unittest
from unittest.mock import patch

from hazard_engine import ModelUnavailable
from hazard_providers import TWDBFloodProvider, USGSPGAProvider, hazard_bundle


class ProviderTests(unittest.TestCase):
    def test_usgs_verified_response_shape_and_g_to_percent_g(self):
        models = {"response": [{"id": "STATIC_CONUS_2023_R2",
                                "url": "/ws/nshmp/conus-2023.R2/static"}]}
        response = {"status": "success", "request": {"latitude": 29.4, "longitude": -98.5},
                    "response": {"metadata": {"imt": {"value": "PGA"}, "siteClass": "BC",
                                              "xLabel": "Ground Motion (g)",
                                              "yLabel": "Annual Frequency of Exceedence"},
                                 "data": {"xs": [.1, .3], "ys": [.02, .005]}}}
        with patch("hazard_providers.fetch_json", side_effect=[(models, "a" * 64), (response, "b" * 64)]) as fetch:
            curve, doc = USGSPGAProvider("STATIC_CONUS_2023_R2", "BC").curve_document_at(29.4, -98.5)
        self.assertEqual(curve.points, ((10, .02), (30, .005)))
        self.assertIn("siteClass=BC", fetch.call_args.args[0])
        self.assertNotIn("vs30", fetch.call_args.args[0])
        self.assertEqual(doc["source"]["artifact_sha256"], "b" * 64)

    def test_missing_nshm_and_unknown_site_class_rejected(self):
        with patch("hazard_providers.fetch_json", return_value=({"response": []}, "a" * 64)):
            with self.assertRaises(ModelUnavailable):
                USGSPGAProvider("NOT_INSTALLED", "BC").model_at(29.4, -98.5)
        with self.assertRaises(ValueError):
            USGSPGAProvider("STATIC_CONUS_2023_R2", "unknown")

    def test_usgs_metadata_cannot_change_request_host(self):
        for path in ("https://example.org/", "//example.org/", "/ws/nshmp/../bad"):
            with patch("hazard_providers.fetch_json", return_value=(
                    {"response": [{"id": "model", "url": path}]}, "a" * 64)):
                with self.assertRaises(ModelUnavailable):
                    USGSPGAProvider("model", "BC").model_at(29.4, -98.5)

    def test_flood_missing_cells_cannot_create_zero_risk(self):
        result = {"tile": "n29w99", "events": {
            "20": {"status": "no_mapped_depth", "depth_ft": None}}}
        with patch("server.lookup", return_value=result):
            with self.assertRaises(ModelUnavailable):
                TWDBFloodProvider().model_at(29.4, -98.5)

    def test_four_peril_bundle_retains_missing_model_status(self):
        with patch.object(TWDBFloodProvider, "curve_document_at", side_effect=ModelUnavailable("no data")):
            bundle = hazard_bundle(29.4, -98.5)
        self.assertEqual(len(bundle["hazards"]), 4)
        self.assertTrue(all(h["status"] == "unavailable" for h in bundle["hazards"]))
        with self.assertRaises(ValueError):
            hazard_bundle(float("nan"), -98.5)


if __name__ == "__main__":
    unittest.main()
