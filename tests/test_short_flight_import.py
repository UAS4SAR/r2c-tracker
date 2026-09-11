import unittest
from unittest.mock import patch

from fastapi import HTTPException

import main


class ShortFlightImportTest(unittest.TestCase):
    def archive(self, seconds=10, distance="0.01"):
        start_ms = 1_783_000_000_000
        return {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "title": "1SAR7",
                    "r2c_prop": {
                        "owner": "1SAR7",
                        "rid": "TEST-RID",
                        "distance_mi": distance,
                    },
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        ["-121.000000", "39.000000", "100", str(start_ms)],
                        ["-121.000000", "39.000000", "100",
                         str(start_ms + seconds * 1000)],
                    ],
                },
            }],
        }

    def extract(self, archive):
        with patch.object(main, "localize_flight_time", side_effect=lambda dt, *_: dt):
            return main._extract_flight_inputs_from_geojson(archive)

    def test_short_flight_preserves_duration(self):
        result = self.extract(self.archive(seconds=1))
        self.assertAlmostEqual(1 / 3600, result["duration_hrs"])
        self.assertEqual(1, (result["end_time"] - result["start_time"]).total_seconds())

    def test_short_distance_is_accepted_independently_of_duration(self):
        result = self.extract(self.archive(seconds=120))
        self.assertEqual(0.01, result["distance"])

    def test_stationary_hover_is_accepted(self):
        result = self.extract(self.archive(seconds=120, distance="0"))
        self.assertEqual(0, result["distance"])
        self.assertAlmostEqual(120 / 3600, result["duration_hrs"])

    def test_single_observation_has_zero_observed_duration(self):
        archive = self.archive(distance="0")
        archive["features"][0]["geometry"]["coordinates"].pop()
        result = self.extract(archive)
        self.assertEqual(0, result["duration_hrs"])
        self.assertEqual(0, result["distance"])

    def test_reversed_timestamps_remain_invalid(self):
        with self.assertRaises(HTTPException) as raised:
            self.extract(self.archive(seconds=-1))
        self.assertEqual(400, raised.exception.status_code)
        self.assertIn("precedes", raised.exception.detail)

    def test_missing_timestamps_remain_invalid(self):
        archive = self.archive()
        for coordinate in archive["features"][0]["geometry"]["coordinates"]:
            coordinate.pop()
        with self.assertRaises(HTTPException) as raised:
            self.extract(archive)
        self.assertEqual(400, raised.exception.status_code)
        self.assertIn("timestamps", raised.exception.detail)

    def test_explicit_flight_pilot_does_not_inherit_routing_callsign(self):
        props = {"r2c_prop": {"mid": "1SAR7m3t", "rid": "RID", "flightReadiness": {"pilot": {}}}}
        self.assertEqual("", main.parse_prop(props)["sar_id"])
        props["r2c_prop"]["flightReadiness"]["pilot"] = {"callsign": "2SAR8", "memberId": "pilot"}
        self.assertEqual("2SAR8", main.parse_prop(props)["sar_id"])


if __name__ == "__main__":
    unittest.main()
