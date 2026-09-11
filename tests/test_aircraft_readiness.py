import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import Mock, patch, AsyncMock

from fastapi import HTTPException
from sqlalchemy import select
from control_plane import ControlPlaneStore, OrganizationUser, Organization
from aircraft_readiness import (
    AircraftServiceEvent, EquipmentMail, record_service, validate_aircraft_details,
    validate_pilot, deliver_mail, preserve_legacy_aircraft_fields, qualified_on, with_aircraft_identities, aircraft_key,
)


class ReadinessTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ControlPlaneStore(f"sqlite+aiosqlite:///{Path(self.temp.name) / 'test.db'}")
        await self.store.init()
        self.actor = SimpleNamespace(id="pilot", organization_id="org", state="active",
            roles=("r2c_device",), email="pilot@example.test", display_name="Pilot")
        async with self.store.sessions() as session:
            session.add(Organization(id="org", legal_name="SAR", designator="SAR", hostname="sar.test"))
            session.add(OrganizationUser(id="manager", organization_id="org", email="manager@example.test",
                display_name="Manager", state="active", roles_json='["equip_manager"]'))
            session.add(OrganizationUser(id="pilot", organization_id="org", email=self.actor.email,
                display_name="Pilot", state="active", roles_json='["r2c_device"]'))
            await session.commit()

    async def asyncTearDown(self):
        await self.store.dispose()
        self.temp.cleanup()

    def report(self, **overrides):
        return {"eventId": str(uuid4()), "status": "out_of_service", "revision": 0,
                "note": "Damaged propeller", "selfRemediation": True, **overrides}

    async def test_service_retries_are_idempotent_and_attributed(self):
        payload = self.report(username="spoof@example.test")
        first = await record_service(self.store, "org", "RID", self.actor, payload)
        self.assertEqual(first, await record_service(self.store, "org", "RID", self.actor, payload))
        self.assertEqual("pilot@example.test", first["username"])
        async with self.store.sessions() as session:
            self.assertEqual(1, len((await session.scalars(select(AircraftServiceEvent))).all()))
            self.assertEqual(1, len((await session.scalars(select(EquipmentMail))).all()))

    async def test_delayed_return_to_service_cannot_clear_newer_issue(self):
        await record_service(self.store, "org", "RID", self.actor, self.report())
        with self.assertRaises(HTTPException) as raised:
            await record_service(self.store, "org", "RID", self.actor,
                self.report(status="in_service", note="Replaced propeller"))
        self.assertEqual(409, raised.exception.status_code)
        event = await record_service(self.store, "org", "RID", self.actor,
            self.report(status="in_service", revision=1, note="Replaced propeller"))
        self.assertEqual("out_of_service", event["previousStatus"])

    async def test_corrected_rid_keeps_the_same_aircraft_service_history(self):
        aircraft_id = str(uuid4())
        await record_service(self.store, "org", "OLD-RID", self.actor, self.report(), aircraft_id)
        event = await record_service(self.store, "org", "CORRECTED-RID", self.actor,
            self.report(status="in_service", revision=1, note="Replaced propeller"), aircraft_id)
        self.assertEqual(2, event["revision"])
        self.assertEqual(aircraft_id, event["aircraftId"])
        async with self.store.sessions() as session:
            history = (await session.scalars(select(AircraftServiceEvent).where(AircraftServiceEvent.remote_id == aircraft_id))).all()
            self.assertEqual(2, len(history))

    async def test_roles_tenants_and_required_notes(self):
        for overrides in ({"organization_id": "other"}, {"roles": ("config_admin",)}, {"state": "disabled"}):
            actor = SimpleNamespace(**{**vars(self.actor), **overrides})
            with self.assertRaises(HTTPException) as raised:
                await record_service(self.store, "org", "RID", actor, self.report())
            self.assertEqual(403, raised.exception.status_code)
        for overrides in ({"note": ""}, {"selfRemediation": None}, {"revision": True}):
            with self.assertRaises(HTTPException) as raised:
                await record_service(self.store, "org", "RID", self.actor, self.report(**overrides))
            self.assertEqual(422, raised.exception.status_code)

    async def test_failed_mail_is_durable_then_retried(self):
        await record_service(self.store, "org", "RID", self.actor, self.report())
        sender = Mock(is_configured=True)
        sender.send_equipment_status.side_effect = RuntimeError("offline")
        await deliver_mail(self.store, sender)
        async with self.store.sessions() as session:
            self.assertEqual("pending", (await session.scalar(select(EquipmentMail))).state)
        sender.send_equipment_status.side_effect = None
        await deliver_mail(self.store, sender)
        await deliver_mail(self.store, sender)
        self.assertEqual(2, sender.send_equipment_status.call_count)

    async def test_browser_routes_enforce_current_member_roles_and_csrf(self):
        import main
        import httpx
        import base64
        from itsdangerous import TimestampSigner
        org = SimpleNamespace(id="org", designator="SAR")
        session_data = {"organization_user_id": "pilot", "organization_designator": "SAR",
                        "_csrf_organization_aircraft_service": "test-token"}
        cookie = TimestampSigner(str(main.SECRET_KEY)).sign(base64.b64encode(json.dumps(session_data).encode())).decode()
        release = SimpleNamespace(snapshot={"droneSpecs": [{"remoteId": "RID", "model": "Test", "owner": "Pilot"}]})
        with patch.object(main, "control_plane_store", self.store), patch.object(main, "organization_site_ready", return_value=True), \
             patch.object(self.store, "get_organization", AsyncMock(return_value=org)), \
             patch.object(self.store, "get_current_organization_config_release", AsyncMock(return_value=release)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://testserver",
                cookies={"r2c_tracker_session_v2": cookie}) as client:
                page = await client.get("/sar/aircraft")
                self.assertEqual(200, page.status_code, page.text)
                self.assertIn("Not yet reported", page.text)
                form = {"actor_id": "pilot", "remote_id": "RID", "event_id": str(uuid4()), "revision": "0",
                        "note": "Broken propeller", "status": "out_of_service", "self_remediation": "yes"}
                self.assertEqual(403, (await client.post("/sar/aircraft/service", data=form)).status_code)
                form["form_token"] = "test-token"
                result = await client.post("/sar/aircraft/service", data=form)
                self.assertEqual(303, result.status_code, result.text)
                async with self.store.sessions() as session:
                    member = await session.get(OrganizationUser, "pilot")
                    member.roles_json = '["records_viewer"]'
                    await session.commit()
                self.assertEqual(403, (await client.post("/sar/aircraft/service", data=form)).status_code)
                self.assertEqual(403, (await client.post("/sar/members/pilot/pilot", data={})).status_code)
                org.id = "other"
                self.assertEqual(303, (await client.get("/sar/aircraft")).status_code)

    async def test_device_endpoint_rejects_cross_organization_and_unattributed_devices(self):
        import main
        import httpx
        credential = SimpleNamespace(id="missing", organization_id="org", designator="SAR")
        main.app.dependency_overrides[main.get_api_key] = lambda: credential
        try:
            with patch.object(main, "control_plane_store", self.store):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://testserver") as client:
                    for path in ("/other/api/v1/aircraft-readiness", "/sar/api/v1/aircraft-readiness"):
                        self.assertEqual(403, (await client.get(path)).status_code)
        finally:
            main.app.dependency_overrides.pop(main.get_api_key, None)


class ReadinessValidationTest(unittest.TestCase):
    def test_legacy_aircraft_gets_an_identity_before_rid_correction(self):
        source = {"droneSpecs": [{"remoteId": "ORIGINAL"}]}
        upgraded = with_aircraft_identities(source)
        aircraft = upgraded["droneSpecs"][0]
        self.assertEqual(aircraft_key(source["droneSpecs"][0]), aircraft_key(aircraft))
        self.assertEqual(aircraft_key(aircraft), aircraft_key({**aircraft, "remoteId": "CORRECTED"}))
        self.assertNotIn("readiness", source["droneSpecs"][0])
        self.assertIsNone(aircraft["readiness"]["baseWeightGrams"])

    def test_currency_uses_knowledge_date_and_calendar_month_boundary(self):
        from datetime import date
        pilot = {"callsign": "1SAR7", "status": "active", "certificateNumber": "123",
                 "certificateDate": "2020-01-01", "recurrentTrainingDate": "2024-09-10"}
        self.assertTrue(qualified_on(pilot, date(2026, 9, 30)))
        self.assertFalse(qualified_on(pilot, date(2026, 10, 1)))
        self.assertFalse(qualified_on({**pilot, "recurrentTrainingDate": ""}, date(2021, 1, 1)))

    def test_unknown_weight_is_not_zero(self):
        self.assertIsNone(validate_aircraft_details({})["baseWeightGrams"])
        for value in (True, -1, "nan", "inf"):
            with self.assertRaises(ValueError):
                validate_aircraft_details({"baseWeightGrams": value})

    def test_active_pilot_requires_qualification_record(self):
        with self.assertRaises(ValueError):
            validate_pilot({"status": "active", "callsign": "1SAR7"})
        self.assertEqual("active", validate_pilot({"status": "active", "callsign": "1SAR7",
            "certificateNumber": "123", "certificateDate": "2020-01-01"})["status"])

    def test_old_client_preserves_new_fields_while_editing_legacy_fields(self):
        current = with_aircraft_identities({"droneSpecs": [{"remoteId": "RID", "readiness": {"baseWeightGrams": 1200}}]})
        proposed = {"droneSpecs": [{"remoteId": "RID", "model": "Updated model"}]}
        merged = preserve_legacy_aircraft_fields(current, proposed)
        self.assertEqual(current["droneSpecs"][0]["readiness"], merged["droneSpecs"][0]["readiness"])
        self.assertEqual("Updated model", merged["droneSpecs"][0]["model"])
        self.assertNotIn("readiness", proposed["droneSpecs"][0])
        with self.assertRaises(ValueError):
            preserve_legacy_aircraft_fields(current, {**proposed, "aircraftSchemaVersion": 1})
