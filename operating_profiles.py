"""Versioned organization operating authority; recording is never gated by this data.

Independent of legacy device configuration releases so an old client's snapshot,
restore, or export cannot remove operating profiles.
"""
import json
from datetime import date
from uuid import uuid4
from urllib.parse import quote
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import Column, String, Integer, Text, select, update
from sqlalchemy.exc import IntegrityError
from readiness_audit import audit_for_history

from control_plane import Base, utc_now
from aircraft_readiness import bounded_text

STANDARD = {"id": "standard-part-107", "version": 1, "name": "Standard Part 107 — no operational waiver", "authorityType": "part107", "conditions": []}
BVLOS = {"id": "bvlos-pending", "version": 1, "name": "BVLOS — authority details pending", "authorityType": "unresolved", "conditions": []}
OTHER = {"id": "other-pending", "version": 1, "name": "Other / details pending", "authorityType": "unresolved", "conditions": []}

class OperatingProfileCatalog(Base):
    __tablename__ = "operating_profile_catalogs"
    organization_id = Column(String(36), primary_key=True)
    revision = Column(Integer, nullable=False)
    data_json = Column(Text, nullable=False)

class OperatingProfileRevision(Base):
    __tablename__ = "operating_profile_revisions"
    organization_id = Column(String(36), primary_key=True)
    revision = Column(Integer, primary_key=True)
    data_json = Column(Text, nullable=False)


def validate_profile(value):
    if not isinstance(value, dict):
        raise ValueError("Operating profile must be an object.")
    result = {key: bounded_text(value.get(key, ""), key, limit, key in ("id", "name", "authorityType"))
              for key, limit in (("id", 160), ("name", 200), ("authorityType", 80), ("waiverNumber", 200),
                                 ("holder", 500), ("document", 2000), ("effectiveFrom", 10), ("effectiveUntil", 10))}
    version = value.get("version")
    if type(version) is not int or version < 1:
        raise ValueError("Profile version must be a positive integer.")
    result["version"] = version
    for key in ("effectiveFrom", "effectiveUntil"):
        if result[key]:
            date.fromisoformat(result[key])
    if result["effectiveFrom"] and result["effectiveUntil"] and result["effectiveFrom"] > result["effectiveUntil"]:
        raise ValueError("Effective dates are reversed.")
    for key in ("pilotIds", "aircraftIds", "locationIds"):
        items = value.get(key, [])
        if not isinstance(items, list) or len(items) > 200:
            raise ValueError("Invalid applicability list.")
        result[key] = list(dict.fromkeys(bounded_text(item, key, 160, True) for item in items))
    conditions = value.get("conditions", [])
    if not isinstance(conditions, list) or len(conditions) > 100:
        raise ValueError("At most 100 briefing conditions are supported.")
    result["conditions"] = []
    for item in conditions:
        if not isinstance(item, dict):
            raise ValueError("Invalid condition.")
        # Unknown machine rules remain readable, never silently executed or discarded.
        result["conditions"].append({"text": bounded_text(item.get("text", ""), "Condition", 4000, True),
            "source": bounded_text(item.get("source", ""), "Source provision", 1000),
            "kind": bounded_text(item.get("kind", "briefing"), "Condition kind", 100),
            "unit": bounded_text(item.get("unit", ""), "Unit", 100),
            "reference": bounded_text(item.get("reference", ""), "Vertical / distance reference", 200)})
    if len(json.dumps(result).encode("utf-8")) > 16384:
        raise ValueError("Keep each profile briefing within 16 KiB; use the document reference for full source text.")
    return result


async def catalog(store, org_id):
    async with store.sessions() as session:
        row = await session.get(OperatingProfileCatalog, org_id)
        return {"revision": row.revision, **json.loads(row.data_json)} if row else {"revision": 0, "defaultProfileId": STANDARD["id"], "profiles": []}


async def save(store, org_id, actor, expected_revision, profile, default_id):
    if actor.organization_id != org_id or actor.state != "active" or not {"config_admin", "organization_owner"}.intersection(actor.roles):
        raise HTTPException(403, "Configuration administrator required.")
    profile = validate_profile(profile)
    if profile["id"] in (STANDARD["id"], BVLOS["id"], OTHER["id"]):
        raise ValueError("Built-in profile IDs are reserved.")
    async with store.sessions() as session:
        row = await session.get(OperatingProfileCatalog, org_id)
        before = json.loads(row.data_json) if row else {"defaultProfileId": STANDARD["id"], "profiles": []}
        if (row.revision if row else 0) != expected_revision:
            raise HTTPException(409, "Profiles changed. Refresh and review the latest version.")
        previous = next((p for p in before["profiles"] if p["id"] == profile["id"]), None)
        profile["version"] = previous["version"] + 1 if previous else 1
        profiles = [p for p in before["profiles"] if p["id"] != profile["id"]] + [profile]
        if len(profiles) > 100:
            raise ValueError("At most 100 organization profiles are supported.")
        if default_id not in {STANDARD["id"], BVLOS["id"], OTHER["id"], *(p["id"] for p in profiles)}:
            raise ValueError("Select an existing default profile.")
        after = {"defaultProfileId": default_id, "profiles": profiles}
        if row:
            changed = await session.execute(update(OperatingProfileCatalog).where(
                OperatingProfileCatalog.organization_id == org_id, OperatingProfileCatalog.revision == expected_revision
            ).values(revision=expected_revision + 1, data_json=json.dumps(after)))
            if changed.rowcount != 1:
                raise HTTPException(409, "Profiles changed. Refresh and review.")
        else:
            session.add(OperatingProfileCatalog(organization_id=org_id, revision=1, data_json=json.dumps(after)))
        history = OperatingProfileRevision(organization_id=org_id, revision=expected_revision + 1,
            data_json=json.dumps({"before": before, "after": after, "editorId": actor.id, "username": actor.email, "recordedAt": utc_now().isoformat()}))
        session.add(history)
        session.add(audit_for_history(history))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(409, "Profiles changed. Refresh and review.")


async def historical_profiles(store, org_id):
    """Every saved version is available to a records administrator for correction."""
    result = {(p["id"], p["version"]): p for p in (STANDARD, BVLOS, OTHER)}
    async with store.sessions() as session:
        rows = (await session.scalars(select(OperatingProfileRevision).where(
            OperatingProfileRevision.organization_id == org_id).order_by(OperatingProfileRevision.revision))).all()
        for row in rows:
            for profile in json.loads(row.data_json)["after"]["profiles"]:
                result[(profile["id"], profile["version"])] = profile
    return list(result.values())


def review_snapshot(snapshot, flight_date):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("profile"), dict):
        return ["Operating authority was not reported; review details."]
    try:
        profile = validate_profile(snapshot["profile"])
    except (ValueError, TypeError):
        return ["Operating profile details are invalid; original report retained."]
    issues = [s for s in snapshot.get("reviewIssues", []) if isinstance(s, str)][:100] if isinstance(snapshot.get("reviewIssues", []), list) else []
    if profile["authorityType"] not in ("part107", "part107_waiver"):
        issues.append("Operating authority and qualification requirements need review.")
    if ((profile["effectiveFrom"] and profile["effectiveFrom"] > flight_date.isoformat()) or
        (profile["effectiveUntil"] and profile["effectiveUntil"] < flight_date.isoformat())):
        issues.append("Profile is outside its effective dates.")
    if profile["authorityType"] == "part107_waiver" and any(not profile[k] for k in ("waiverNumber", "holder", "document", "effectiveFrom", "effectiveUntil")):
        issues.append("Waiver details are incomplete; review the source document.")
    checked = snapshot.get("checkedConditions", [])
    if not isinstance(checked, list) or any(i not in checked for i in range(len(profile["conditions"]))):
        issues.append("One or more briefing conditions have not been acknowledged.")
    return list(dict.fromkeys(issues))


def install_routes(app, ctx):
    async def access(request, designator):
        return await ctx["require_organization_user"](request, designator,
            required_roles=("organization_owner", "config_admin"), redirect_to_login=True)

    @app.get("/{designator}/admin/operating-profiles")
    async def editor(request: Request, designator: str):
        org, actor = await ctx["require_organization_user"](request, designator,
            required_roles=("organization_owner", "config_admin"), redirect_to_login=True)
        state = await catalog(ctx["control_plane_store"], org.id)
        selected = next((p for p in state["profiles"] if p["id"] == request.query_params.get("profile")),
                        {"id": str(uuid4()), "name": "", "authorityType": "part107_waiver", "conditions": []})
        from aircraft_readiness import with_aircraft_identities
        release = await ctx["control_plane_store"].get_current_organization_config_release(org.id)
        aircraft_choices = with_aircraft_identities(release.snapshot)["droneSpecs"] if release else []
        pilot_choices = await ctx["control_plane_store"].list_users(org.id)
        return ctx["templates"].TemplateResponse(request=request, name="operating_profiles.html", context={
            "request": request, "organization": org, "catalog": state, "profile": selected,
            "pilot_choices": pilot_choices, "aircraft_choices": aircraft_choices,
            "standard": STANDARD, "other": OTHER, "form_token": ctx["csrf_token"](request, "operating_profiles"),
            "organization_page_designator": org.designator, "organization_identity_name": actor.display_name,
            "include_leaflet": False, "enable_live_refresh": False})

    @app.post("/{designator}/admin/operating-profiles")
    async def amend(request: Request, designator: str):
        org, actor = await ctx["require_organization_user"](request, designator,
            required_roles=("organization_owner", "config_admin"), redirect_to_login=True)
        form = await request.form()
        ctx["verify_csrf"](request, "operating_profiles", str(form.get("form_token", "")))
        try:
            profile = {key: str(form.get(key, "")) for key in ("id", "name", "authorityType", "waiverNumber", "holder", "document", "effectiveFrom", "effectiveUntil")}
            profile["version"] = 1
            for key in ("pilotIds", "aircraftIds"):
                profile[key] = [str(item) for item in form.getlist(key)]
            profile["locationIds"] = [item.strip() for item in str(form.get("locationIds", "")).split(",") if item.strip()]
            profile["conditions"] = [{"text": text.strip(), "source": source.strip(), "kind": kind, "unit": unit, "reference": reference}
                for text, source, kind, unit, reference in zip(form.getlist("condition_text"), form.getlist("condition_source"),
                    form.getlist("condition_kind"), form.getlist("condition_unit"), form.getlist("condition_reference")) if text.strip()]
            await save(ctx["control_plane_store"], org.id, actor, int(form.get("revision", -1)), profile, str(form.get("defaultProfileId", "")))
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc))
        await ctx["r2c_hub"].notify_aircraft_readiness_changed(org.id)
        return RedirectResponse(f"/{org.designator.lower()}/admin/operating-profiles?profile={quote(profile['id'], safe='')}", status_code=303)
