import copy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from hazard_engine import HazardCurve
from hazard_providers import curve_document

ROOT = Path(__file__).resolve().parents[1]


class SchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = json.loads((ROOT / "schemas/hazard-location.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        cls.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def bundle(self):
        doc = curve_document(HazardCurve("pga_pct_g", ((10, .02), (30, .005)), "annual_rate"),
                             provider="test", model_version="test", endpoint="https://example.org/curve",
                             artifact_sha256="a" * 64, definition_id="test-site-pga-v1",
                             definition="Illustrative site PGA", spatial_method="agency_interpolated_grid",
                             assumptions={"data_status": "illustrative"})
        return {"schema_version": "1.0.0", "location": {"latitude": 29.4, "longitude": -98.5,
                "horizontal_crs": "EPSG:4326"}, "reference_duration_years": 1,
                "rating_status": "research_only", "hazards": [{"peril": "earthquake", **doc},
                *[{"peril": p, "status": "unavailable", "reason": "not installed"}
                  for p in ("rain_wind", "flood", "wildfire")]]}

    def test_generated_contract_and_unavailable_models(self):
        self.validator.validate(self.bundle())

    def test_duplicate_or_missing_peril_rejected(self):
        bundle = self.bundle()
        bundle["hazards"][3] = copy.deepcopy(bundle["hazards"][2])
        with self.assertRaises(ValidationError):
            self.validator.validate(bundle)

    def test_incompatible_units_or_probability_ranges_rejected(self):
        bundle = self.bundle()
        bundle["hazards"][0]["metrics"][0]["unit"] = "mm"
        with self.assertRaises(ValidationError):
            self.validator.validate(bundle)
        bundle = self.bundle()
        bundle["hazards"][0]["representation"]["points"][0]["annual_exceedance_probability"] = 1.1
        with self.assertRaises(ValidationError):
            self.validator.validate(bundle)

    def test_joint_surface_contract(self):
        bundle = self.bundle()
        rain_wind = copy.deepcopy(bundle["hazards"][0])
        rain_wind["peril"] = "rain_wind"
        rain_wind["metrics"] = [{"id": "rain_24h_mm", "unit": "mm", "definition_id": "24h-v1",
                                 "definition": "Max rolling 24-hour accumulation in same storm"},
                                {"id": "wind_gust_kn", "unit": "kn", "definition_id": "gust-v1",
                                 "definition": "Maximum approved gust in same storm"}]
        rain_wind["representation"] = {"kind": "surface", "frequency_measure": "annual_probability",
                                       "operator": "AND", "event_definition_id": "storm-v1",
                                       "points": [{"thresholds": {"rain_24h_mm": 150, "wind_gust_kn": 70},
                                                   "annual_exceedance_probability": None}]}
        bundle["hazards"][1] = rain_wind
        self.validator.validate(bundle)


if __name__ == "__main__":
    unittest.main()
