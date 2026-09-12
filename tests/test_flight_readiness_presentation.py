import unittest
from datetime import datetime
from types import SimpleNamespace

import main
from flight_readiness_records import flight_local_time
from operating_profiles import historical_profiles


class ReadinessPresentationTest(unittest.TestCase):
    def test_flight_local_time_includes_daylight_or_standard_zone(self):
        flight = SimpleNamespace(start_lat=39.2, start_lng=-121.0)
        ctx = {"localize_flight_time": main.localize_flight_time}
        self.assertEqual("Sep 11, 2026 04:55:00 PM PDT", flight_local_time(ctx, flight, "2026-09-11T23:55:00Z"))
        self.assertEqual("Jan 11, 2026 03:55:00 PM PST", flight_local_time(ctx, flight, datetime(2026, 1, 11, 23, 55)))
        self.assertTrue(flight_local_time(ctx, SimpleNamespace(), "2026-09-11T23:55:00Z").endswith("UTC"))

    def test_legacy_empty_fields_are_not_edits_but_real_name_and_weight_are(self):
        before = {"remoteId": "RID", "mappedId": "1sar7DjMn4Pr", "owner": "1sar7", "org": "SAR", "model": "DJI Mini 4 Pro"}
        after = {**before, "ownerName": "", "ownerCallsign": "1sar7", "readiness": {}}
        self.assertEqual([], main.organization_config_diff({"droneSpecs": [before]}, {"droneSpecs": [after]})["changedDrones"])
        for edited in ({**after, "ownerName": "Ken"}, {**after, "readiness": {"baseWeightGrams": 297}}):
            self.assertEqual(1, len(main.organization_config_diff({"droneSpecs": [before]}, {"droneSpecs": [edited]})["changedDrones"]))

    def test_page_shows_reported_callsign_and_local_time_without_rewriting_original(self):
        from pathlib import Path
        from jinja2 import Environment, DictLoader, select_autoescape
        page = Path(__file__).resolve().parents[1] / "templates" / "flight_readiness.html"
        env = Environment(loader=DictLoader({"base.html": "{% block content %}{% endblock %}", "flight_readiness.html": page.read_text()}), autoescape=select_autoescape())
        original = {"pilot": {"callsign": "1sar7"}, "confirmedAt": "2026-09-11T23:55:00Z"}
        html = env.get_template("flight_readiness.html").render(flight=SimpleNamespace(id=1, remote_id="RID", sar_id="SAR"),
            flight_start_local="Sep 11, 2026 04:55:00 PM PDT", record={"reportedPilotCallsign": "1sar7", "pilot": {}},
            original=original, history=[], pilots=[], profile_versions=[], revision=0, total_weight=None,
            organization=SimpleNamespace(designator="SAR"))
        self.assertIn("Reported RPIC: 1sar7", html)
        self.assertIn("04:55:00 PM PDT", html)
        self.assertEqual("2026-09-11T23:55:00Z", original["confirmedAt"])
