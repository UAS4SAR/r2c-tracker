import json
import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace as Obj

from adoption_reports import export_csv, period_bounds, period_label, pilot_token, summarize
from flight_readiness_records import key_for


class AdoptionReportsTest(unittest.TestCase):
    def flight(self, org, day, hours=0.5, model="m3t"):
        return Obj(organization_id=org, start_time=datetime(2026, 8, day),
                   remote_id="PRIVATE-SERIAL", hours=hours, uas=model)

    def test_calendar_boundaries_and_dst(self):
        prior, start, end = period_bounds("daily", date(2026, 3, 9), "America/Los_Angeles")
        self.assertEqual(23 * 3600, (end - start).total_seconds())
        self.assertEqual(24 * 3600, (start - prior).total_seconds())
        prior, start, end = period_bounds("monthly", date(2026, 1, 15), "UTC")
        self.assertEqual(("2025-11-01", "2025-12-01", "2026-01-01"),
                         tuple(x.date().isoformat() for x in (prior, start, end)))
        self.assertEqual(datetime(2026, 9, 7, tzinfo=UTC),
                         period_bounds("weekly", date(2026, 9, 14), "UTC")[1])
        self.assertEqual("August 2026", period_label(date(2026, 8, 1), date(2026, 9, 1), "monthly"))
        self.assertEqual("14 Sep 2026", period_label(date(2026, 9, 14), date(2026, 9, 15), "daily"))

    def test_adoption_retention_unknown_pilots_and_privacy(self):
        bounds = tuple(datetime(2026, 8, day, tzinfo=UTC) for day in (1, 8, 15))
        orgs = [Obj(id=name, legal_name=name, designator=name) for name in
                ("new", "repeat", "return", "quiet", "unused")]
        flights = [self.flight("new", 8), self.flight("repeat", 2), self.flight("repeat", 10),
                   self.flight("return", 9, 0), self.flight("quiet", 4),
                   self.flight(None, 10, float("nan"), "PRIVATE-SERIAL")]
        first = {"new": flights[0].start_time, "repeat": flights[1].start_time,
                 "return": datetime(2026, 7, 1), "quiet": flights[4].start_time,
                 None: flights[5].start_time}
        readiness = {(f.organization_id, key_for(f)): {"pilot": {"memberId": "secret-member", "name": "Private Person"},
                     "pilotAttribution": "operator_selected_member_matched"} for f in flights[1:3]}
        report = summarize(orgs, first, flights, readiness, bounds, "UTC", "test-key")
        self.assertEqual(3, report["totals"]["active_organizations"])
        self.assertEqual(4, report["totals"]["current_flights"])
        self.assertEqual(2, report["totals"]["previous_flights"])
        self.assertEqual(50, report["totals"]["organization_retention_percent"])
        for key in ("new", "retained", "reactivated", "quiet"):
            self.assertEqual(1, report["totals"][key + "_organizations"])
        repeat = next(r for r in report["organizations"] if r["organization"] == "repeat")
        self.assertEqual(1, repeat["retained_pilots"])
        self.assertEqual(1, repeat["current"]["pilots"])
        self.assertEqual(30, repeat["current"]["minutes"])
        raw = json.dumps(report, allow_nan=False)
        for value in ("PRIVATE-SERIAL", "secret-member", "Private Person"):
            self.assertNotIn(value, raw)
        self.assertEqual("unknown", report["flights"][-1]["drone_model"])
        self.assertIsNone(report["flights"][-1]["duration_minutes"])

    def test_exclusive_end_and_zero_baseline(self):
        bounds = tuple(datetime(2026, 8, d, tzinfo=UTC) for d in (1, 8, 15))
        report = summarize([Obj(id="a", legal_name="A", designator="A")],
            {"a": datetime(2026, 8, 8)}, [self.flight("a", 8), self.flight("a", 15)], {}, bounds, "UTC", "key")
        self.assertEqual(1, report["organizations"][0]["current"]["flights"])
        self.assertIsNone(report["organizations"][0]["flight_change_percent"])
        self.assertIsNone(report["totals"]["organization_retention_percent"])
        self.assertEqual(0, report["organizations"][0]["current"]["pilots"])

    def test_pilot_tokens_are_stable_and_organization_scoped(self):
        self.assertEqual(pilot_token("a", "p", "key"), pilot_token("a", "p", "key"))
        self.assertNotEqual(pilot_token("a", "p", "key"), pilot_token("b", "p", "key"))

    def test_csv_neutralizes_formulas(self):
        csv = export_csv({"flights": [{"period": "current", "organization": " =DANGEROUS()",
            "designator": "ORG", "pilot_number": "unknown", "drone_model": "@formula", "duration_minutes": 5}]})
        self.assertIn("' =DANGEROUS()", csv)
        self.assertIn("'@formula", csv)


if __name__ == "__main__":
    unittest.main()
