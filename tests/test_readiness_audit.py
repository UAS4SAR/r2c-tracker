import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import delete, select

from control_plane import ControlPlaneStore, ControlPlaneAuditEvent, Organization, OrganizationUser, as_utc
from aircraft_readiness import PilotProfile, PilotProfileRevision, AircraftServiceEvent, install_routes, record_service
from operating_profiles import save, OperatingProfileRevision, OperatingProfileCatalog
from flight_readiness_records import preserve_submission, correct, FlightReadinessCorrection
from readiness_audit import audit_for_history, backfill_readiness_audit


class ReadinessAuditTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ControlPlaneStore(f"sqlite+aiosqlite:///{Path(self.temp.name) / 'audit.db'}")
        await self.store.init()
        self.actor = SimpleNamespace(id="editor", organization_id="org", state="active",
            roles=("user_admin", "config_admin", "records_admin", "r2c_device"),
            email="editor@example.test", display_name="Editor")
        async with self.store.sessions() as session:
            session.add_all([Organization(id="org", legal_name="SAR", designator="SAR", hostname="sar.test"),
                            Organization(id="other", legal_name="Other", designator="OTHER", hostname="other.test")])
            await session.flush()
            session.add(OrganizationUser(id="member", organization_id="org", email="member@example.test",
                display_name="Member", state="active", roles_json='[]'))
            await session.commit()
        self.profile = {"id": "authority", "version": 1, "name": "Authority", "authorityType": "part107"}
        self.app = FastAPI()
        self.authorize = AsyncMock(return_value=(SimpleNamespace(id="org", designator="SAR"), self.actor))
        install_routes(self.app, {"require_organization_user": self.authorize,
            "get_api_key": lambda: None, "verify_csrf": lambda *args: None, "control_plane_store": self.store,
            "r2c_hub": SimpleNamespace(notify_aircraft_readiness_changed=AsyncMock())})

    async def asyncTearDown(self):
        await self.store.dispose()
        self.temp.cleanup()

    async def pilot_edit(self, callsign="1SAR7"):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
            return await client.post("/sar/members/member/pilot", data={"callsign": callsign, "status": "unrecorded"})

    async def create_histories(self):
        self.assertEqual(303, (await self.pilot_edit()).status_code)
        await save(self.store, "org", self.actor, 0, self.profile, "authority")
        payload = {"eventId": str(uuid4()), "status": "out_of_service", "revision": 0,
                   "note": "Propeller damage", "selfRemediation": True, "username": "spoofed"}
        await record_service(self.store, "org", "RID", self.actor, payload)
        await record_service(self.store, "org", "RID", self.actor, payload)
        flight = SimpleNamespace(id=1, organization_id="org", remote_id="RID", start_time=datetime(2026, 9, 11))
        await preserve_submission(self.store, flight, {})
        await correct(self.store, flight, self.actor, 0, {"payloadWeightGrams": 10}, "Measured")

    async def test_all_four_appear_in_org_audit_and_backfill_preserves_events(self):
        await self.create_histories()
        page = await self.store.search_audit_events(organization_designator="SAR")
        self.assertEqual({"member.pilot_profile_updated", "organization.operating_profiles_updated",
            "organization.aircraft_service_reported", "recording.readiness_corrected"}, {e.event_type for e in page.events})
        self.assertEqual(4, len(page.events))
        self.assertEqual(0, len((await self.store.search_audit_events(organization_designator="OTHER")).events))
        async with self.store.sessions() as session:
            originals = {r.id: (r.actor_id, as_utc(r.created_at), r.details_json) for r in
                         (await session.scalars(select(ControlPlaneAuditEvent))).all()}
            await session.execute(delete(ControlPlaneAuditEvent))
            await session.commit()
        counts = await backfill_readiness_audit(self.store)
        self.assertEqual(4, counts["inserted"])
        self.assertEqual(0, (await backfill_readiness_audit(self.store))["inserted"])
        async with self.store.sessions() as session:
            restored = {r.id: (r.actor_id, as_utc(r.created_at), r.details_json) for r in
                        (await session.scalars(select(ControlPlaneAuditEvent))).all()}
        self.assertEqual(originals, restored)
        self.assertTrue(all(v[0] == "editor" for v in restored.values()))

    async def test_cancelled_transaction_does_not_leave_history_or_audit(self):
        with patch("operating_profiles.audit_for_history", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                await save(self.store, "org", self.actor, 0, self.profile, "authority")
        async with self.store.sessions() as session:
            for model in (OperatingProfileCatalog, OperatingProfileRevision, ControlPlaneAuditEvent):
                self.assertEqual([], (await session.scalars(select(model))).all())
        with patch("aircraft_readiness.audit_for_history", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                await self.pilot_edit()
        async with self.store.sessions() as session:
            for model in (PilotProfile, PilotProfileRevision, ControlPlaneAuditEvent):
                self.assertEqual([], (await session.scalars(select(model))).all())

    async def test_backfill_retention_bad_data_tenant_identity_and_holds(self):
        timestamp = datetime.now(timezone.utc) - timedelta(days=2)
        def history(org, key, when, data=None):
            return PilotProfileRevision(id=key, organization_id=org, member_id="member", data_json=data or json.dumps({
                "before": {"callsign": "OLD"}, "after": {"callsign": "NEW", "certificateNumber": "private"},
                "editorId": "deleted-editor", "username": "original@example.test", "recordedAt": when.isoformat()}))
        row = history("org", "a", timestamp)
        async with self.store.sessions() as session:
            session.add_all([row, history("other", "b", timestamp), history("org", "c", timestamp - timedelta(days=400)),
                             history("org", "d", timestamp, "invalid json")])
            await session.commit()
        result = await backfill_readiness_audit(self.store)
        self.assertEqual({"inserted": 2, "existing": 0, "expired": 1, "invalid": 1}, result)
        async with self.store.sessions() as session:
            event = await session.get(ControlPlaneAuditEvent, audit_for_history(row).id)
            self.assertEqual(timestamp, as_utc(event.created_at))
            self.assertEqual("deleted-editor", event.actor_id)
            self.assertNotIn("private", event.details_json)
            event.retention_hold = True
            await session.commit()
        await backfill_readiness_audit(self.store)
        async with self.store.sessions() as session:
            self.assertTrue((await session.get(ControlPlaneAuditEvent, audit_for_history(row).id)).retention_hold)
        self.assertEqual(1, len((await self.store.search_audit_events(organization_designator="OTHER")).events))
