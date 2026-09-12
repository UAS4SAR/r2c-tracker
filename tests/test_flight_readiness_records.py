import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from control_plane import ControlPlaneStore, Organization
from flight_readiness_records import (
    FlightReadinessRecord, key_for, preserve_submission, correct, export_records, total_weight,
)


class FlightReadinessRecordsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ControlPlaneStore(f"sqlite+aiosqlite:///{Path(self.temp.name) / 'records.db'}")
        await self.store.init()
        async with self.store.sessions() as session:
            session.add(Organization(id="org", legal_name="Test", designator="ORG", hostname="org.test"))
            await session.commit()
        self.flight = SimpleNamespace(id=1, organization_id="org", remote_id="RID", start_time=datetime(2026, 9, 1))
        self.actor = SimpleNamespace(id="editor", organization_id="org", state="active", roles=("records_admin",), email="editor@example.test")
        self.snapshot = {"aircraft": {"baseWeightGrams": 1200, "accessories": [
            {"id": "battery", "name": "Battery", "weightGrams": 500}]}, "selectedAccessories": ["battery"],
            "payloadDescription": "water bottle", "payloadWeightGrams": None, "confirmedAt": "2026-09-01T01:00:00Z"}
        self.data = {"features": [{"properties": {"r2c_prop": {"flightReadiness": self.snapshot}}}]}
        await preserve_submission(self.store, self.flight, self.data)

    async def asyncTearDown(self):
        await self.store.dispose()
        self.temp.cleanup()

    async def test_rpic_identity_does_not_require_complete_qualifications(self):
        from control_plane import OrganizationUser
        from aircraft_readiness import PilotProfile
        async with self.store.sessions() as session:
            session.add(OrganizationUser(id="pilot", organization_id="org", email="pilot@example.test", display_name="Pilot", state="active", roles_json="[]"))
            session.add(PilotProfile(member_id="pilot", organization_id="org", callsign_key="1sar7", data_json=json.dumps({"callsign": "1sar7", "status": "unrecorded"})))
            await session.commit()
        flight = SimpleNamespace(id=2, organization_id="org", remote_id="OTHER", start_time=datetime(2026, 9, 11))
        await preserve_submission(self.store, flight, {"features": [{"properties": {"r2c_prop": {"flightReadiness": {"pilot": {"memberId": "pilot", "callsign": "1SAR7"}}}}}]})
        record = (await export_records(self.store, [flight]))[2]["current"]
        self.assertEqual("pilot", record["pilot"]["memberId"])
        self.assertEqual("operator_selected_member_matched", record["pilotAttribution"])
        self.assertTrue(any("not verified" in issue for issue in record["reviewIssues"]))

    async def test_payload_completion_preserves_original_and_survives_reimport(self):
        self.assertIsNone(total_weight(self.snapshot))
        await correct(self.store, self.flight, self.actor, 0, {"payloadWeightGrams": 535}, "Weighed an identical full bottle with cap")
        await preserve_submission(self.store, self.flight, self.data)
        result = (await export_records(self.store, [self.flight]))[1]
        self.assertEqual(2235, result["takeoffWeightGrams"])
        self.assertEqual(1, result["revision"])
        self.assertEqual("water bottle", result["current"]["payloadDescription"])
        self.assertEqual(self.snapshot["confirmedAt"], result["current"]["confirmedAt"])
        self.assertTrue(result["corrections"][0]["postFlight"])
        async with self.store.sessions() as session:
            original = json.loads((await session.get(FlightReadinessRecord, ("org", key_for(self.flight)))).original_json)
            self.assertIsNone(original["payloadWeightGrams"])

    async def test_stale_editor_and_unauthorized_roles_cannot_change_record(self):
        await correct(self.store, self.flight, self.actor, 0, {"payloadWeightGrams": 535}, "Measured")
        with self.assertRaises(HTTPException) as caught:
            await correct(self.store, self.flight, self.actor, 0, {"payloadWeightGrams": 400}, "Estimate")
        self.assertEqual(409, caught.exception.status_code)
        for override in ({"roles": ("records_viewer",)}, {"organization_id": "other"}, {"state": "disabled"}):
            actor = SimpleNamespace(**{**vars(self.actor), **override})
            with self.assertRaises(HTTPException) as caught:
                await correct(self.store, self.flight, actor, 1, {"payloadWeightGrams": 400}, "Estimate")
            self.assertEqual(403, caught.exception.status_code)

    async def test_legacy_archive_without_readiness_is_retained(self):
        self.flight.remote_id = "LEGACY-RID"
        legacy = {"features": [{"properties": {"r2c_prop": {"owner": "1SAR7", "rid": "LEGACY-RID"}}}]}
        await preserve_submission(self.store, self.flight, legacy)
        result = (await export_records(self.store, [self.flight]))[1]
        self.assertEqual("unresolved", result["current"]["pilotAttribution"])
        async with self.store.sessions() as session:
            record = await session.get(FlightReadinessRecord, ("org", key_for(self.flight)))
            self.assertEqual({}, json.loads(record.original_json))

    def test_incompatible_batteries_and_unknown_accessories_are_incomplete(self):
        snapshot = {**self.snapshot, "selectedAccessories": ["unknown"]}
        self.assertIsNone(total_weight(snapshot))
