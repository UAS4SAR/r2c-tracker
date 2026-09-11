import json
import tempfile
import unittest
from datetime import datetime, date
from pathlib import Path
from types import SimpleNamespace
from fastapi import HTTPException
from control_plane import ControlPlaneStore
from operating_profiles import catalog, save, historical_profiles, validate_profile, review_snapshot, STANDARD
from flight_readiness_records import preserve_submission, correct, export_records

class OperatingProfilesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ControlPlaneStore(f"sqlite+aiosqlite:///{Path(self.temp.name) / 'profiles.db'}")
        await self.store.init()
        self.actor = SimpleNamespace(id="admin", organization_id="org", state="active", roles=("config_admin",), email="admin@example.test", display_name="Administrator")
        self.profile = {"id": "waiver", "version": 1, "name": "Test authority", "authorityType": "part107_waiver", "waiverNumber": "test", "holder": "Holder", "effectiveFrom": "2026-01-01", "effectiveUntil": "2026-12-31", "document": "source", "conditions": [{"text": "Verify RTH reference", "source": "Provision 8", "unit": "feet", "reference": "ATO"}]}
    async def asyncTearDown(self):
        await self.store.dispose(); self.temp.cleanup()
    async def test_defaults_versions_and_tenant_isolation(self):
        self.assertEqual(STANDARD["id"], (await catalog(self.store, "org"))["defaultProfileId"])
        await save(self.store, "org", self.actor, 0, self.profile, "waiver")
        await save(self.store, "org", self.actor, 1, {**self.profile, "name": "Updated"}, "standard-part-107")
        versions = await historical_profiles(self.store, "org")
        self.assertEqual([1, 2], [p["version"] for p in versions if p["id"] == "waiver"])
        self.assertEqual([], (await catalog(self.store, "other"))["profiles"])
        self.assertEqual(2, (await catalog(self.store, "org"))["revision"])
    async def test_permissions_and_optimistic_conflict(self):
        for override in ({"roles": ("r2c_device",)}, {"organization_id": "other"}, {"state": "disabled"}):
            with self.assertRaises(HTTPException) as caught:
                await save(self.store, "org", SimpleNamespace(**{**vars(self.actor), **override}), 0, self.profile, "waiver")
            self.assertEqual(403, caught.exception.status_code)
        await save(self.store, "org", self.actor, 0, self.profile, "waiver")
        with self.assertRaises(HTTPException) as caught:
            await save(self.store, "org", self.actor, 0, self.profile, "waiver")
        self.assertEqual(409, caught.exception.status_code)
    async def test_flight_snapshot_and_timeline_survive_catalog_edit_correction_and_replay(self):
        flight = SimpleNamespace(id=1, organization_id="org", remote_id="RID", start_time=datetime(2026, 9, 11))
        snapshot = {"profile": self.profile, "checkedConditions": [0]}
        timeline = [{"at": "2026-09-11T12:01:00Z", "before": snapshot, "after": {"profile": STANDARD}}]
        data = {"features": [{"properties": {"r2c_prop": {"flightReadiness": {"operatingProfile": snapshot, "operatingProfileChanges": timeline}}}}]}
        await preserve_submission(self.store, flight, data)
        await save(self.store, "org", self.actor, 0, {**self.profile, "conditions": []}, "waiver")
        exported = (await export_records(self.store, [flight]))[1]
        self.assertEqual("Provision 8", exported["current"]["operatingProfile"]["profile"]["conditions"][0]["source"])
        self.assertEqual(timeline, exported["current"]["operatingProfileChanges"])
        actor = SimpleNamespace(**{**vars(self.actor), "roles": ("records_admin",)})
        await correct(self.store, flight, actor, 0, {"operatingProfile": {"profile": STANDARD}}, "RPIC clarified authority")
        await preserve_submission(self.store, flight, data)
        await preserve_submission(self.store, flight, {})
        exported = (await export_records(self.store, [flight]))[1]
        self.assertEqual(STANDARD, exported["current"]["operatingProfile"]["profile"])
        self.assertEqual(snapshot, exported["corrections"][0]["before"]["operatingProfile"])
        self.assertEqual(1, exported["revision"])
    def test_unknown_authority_readable_conditions_and_expiry(self):
        future = validate_profile({**self.profile, "authorityType": "future_authority"})
        self.assertEqual("ATO", future["conditions"][0]["reference"])
        self.assertTrue(review_snapshot({"profile": future}, date(2027, 1, 1)))
        self.assertTrue(review_snapshot(None, date(2026, 9, 11)))
        with self.assertRaises(ValueError):
            validate_profile({**self.profile, "version": True})

    async def test_browser_editor_render_csrf_and_role_enforcement(self):
        from fastapi import FastAPI, Request
        from fastapi.templating import Jinja2Templates
        import httpx
        from operating_profiles import install_routes
        actor = self.actor
        async def require_user(request, designator, required_roles, **kwargs):
            if designator != "org" or not set(actor.roles).intersection(required_roles):
                raise HTTPException(403)
            return SimpleNamespace(id="org", designator="ORG", legal_name="Test Organization"), actor
        def verify_csrf(request, scope, token):
            if token != "valid-token": raise HTTPException(403)
        async def notify(org_id): pass
        app = FastAPI()
        import main
        from starlette.middleware.sessions import SessionMiddleware
        app.add_middleware(SessionMiddleware, secret_key="test-session-key")  # pragma: allowlist secret
        templates = main.templates
        install_routes(app, {"require_organization_user": require_user, "control_plane_store": self.store,
            "templates": templates, "csrf_token": lambda *args: "valid-token", "verify_csrf": verify_csrf,
            "r2c_hub": SimpleNamespace(notify_aircraft_readiness_changed=notify)})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get('/org/admin/operating-profiles')
            self.assertEqual(200, response.status_code)
            self.assertIn('Standard Part 107', response.text)
            self.assertEqual(403, (await client.post('/org/admin/operating-profiles', data={})).status_code)
            data = {**{k: v for k, v in self.profile.items() if isinstance(v, str)}, "form_token": "valid-token", "revision": "0", "defaultProfileId": "waiver"}
            self.assertEqual(303, (await client.post('/org/admin/operating-profiles', data=data)).status_code)
            self.assertEqual(403, (await client.get('/other/admin/operating-profiles')).status_code)
            actor.roles = ("r2c_device",)
            self.assertEqual(403, (await client.get('/org/admin/operating-profiles')).status_code)
            self.assertEqual(403, (await client.post('/org/admin/operating-profiles', data=data)).status_code)
