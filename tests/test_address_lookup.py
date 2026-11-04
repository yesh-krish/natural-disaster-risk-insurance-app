import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from address_lookup import geocode_address, normalize_address


class AddressLookupTests(unittest.TestCase):
    def test_whitespace_normalization_preserves_address_identity(self):
        for address in ["  100    Dolorosa St,  San Antonio, TX  ", "100\tDolorosa St ,San Antonio , TX",
                        "100\u00a0Dolorosa\nSt, San Antonio, TX"]:
            self.assertEqual(normalize_address(address), "100 Dolorosa St, San Antonio, TX")
        self.assertEqual(normalize_address("12-14 N Main St Apt #2B, Austin, TX 78701"), "12-14 N Main St Apt #2B, Austin, TX 78701")
        self.assertEqual(normalize_address("100 Dolorossa St, San Antonio, TX"), "100 Dolorossa St, San Antonio, TX", "No speculative spelling correction")
        for address in ["", "   ", ", ,", "x" * 101, "100\x00Main St", None]:
            with self.assertRaises(ValueError):
                normalize_address(address)

    def test_request_encodes_normalized_address_and_preserves_actual_match(self):
        payload = {"result": {"addressMatches": [{"matchedAddress": "100 DOLOROSA, SAN ANTONIO, TX, 78205",
                                                 "coordinates": {"x": -98.493261, "y": 29.424206}}]}}
        with patch("address_lookup.urllib.request.urlopen", return_value=io.StringIO(json.dumps(payload))) as download:
            result = geocode_address(" 100   Dolorosa St , San Antonio, TX ")
        query = parse_qs(urlsplit(download.call_args.args[0].full_url).query)
        self.assertEqual(query["address"], ["100 Dolorosa St, San Antonio, TX"])
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["matched_address"], payload["result"]["addressMatches"][0]["matchedAddress"])
        self.assertTrue(result["approximate"])

    def test_no_match_and_ambiguous_matches_do_not_pick_coordinates(self):
        for matches, status in [([], "no_match"), ([
            {"matchedAddress": "100 MAIN ST, CITY A", "coordinates": {"x": -98, "y": 29}},
            {"matchedAddress": "100 MAIN ST, CITY B", "coordinates": {"x": -97, "y": 30}},
        ], "ambiguous")]:
            with patch("address_lookup.urllib.request.urlopen", return_value=io.StringIO(json.dumps({"result": {"addressMatches": matches}}))):
                result = geocode_address("100 Main St")
            self.assertEqual(result["status"], status)
            self.assertNotIn("latitude", result)
            self.assertEqual(result["candidate_count"], len(matches))
            self.assertIn("message", result)

    def test_invalid_geocoder_payload_is_a_provider_failure(self):
        for payload in [{}, {"result": {"addressMatches": "bad"}}, {"result": {"addressMatches": [
            {"matchedAddress": "Bad", "coordinates": {"x": -98, "y": 99}}]}}]:
            with patch("address_lookup.urllib.request.urlopen", return_value=io.StringIO(json.dumps(payload))):
                with self.assertRaises((ValueError, KeyError)):
                    geocode_address("100 Main St")
