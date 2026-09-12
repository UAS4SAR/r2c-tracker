"""Organization aircraft configuration, pilot qualifications and service history.

Service events are independent of configuration releases. All identities and
permissions are resolved on the server; clients cannot name their own actor.
"""
import asyncio
import json
import math
from calendar import monthrange
from datetime import date, datetime, timezone
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import Column, String, Text, Integer, UniqueConstraint, select, update
from sqlalchemy.exc import IntegrityError

from readiness_audit import audit_for_history

from control_plane import Base, DeviceCredential, OrganizationUser, Organization, utc_now


class PilotProfile(Base):
    __tablename__ = "pilot_profiles"
    __table_args__ = (UniqueConstraint("organization_id", "callsign_key"),)
    member_id = Column(String(36), primary_key=True)
    organization_id = Column(String(36), nullable=False, index=True)
    callsign_key = Column(String(160), nullable=True)
    data_json = Column(Text, nullable=False, default="{}")


class PilotProfileRevision(Base):
    __tablename__ = "pilot_profile_revisions"
    id = Column(String(36), primary_key=True)
    organization_id = Column(String(36), nullable=False, index=True)
    member_id = Column(String(36), nullable=False, index=True)
    data_json = Column(Text, nullable=False)


class AircraftService(Base):
    __tablename__ = "organization_aircraft_service"
    organization_id = Column(String(36), primary_key=True)
    remote_id = Column(String(160), primary_key=True)
    revision = Column(Integer, nullable=False, default=0)
    status = Column(String(24), nullable=False, default="unreported")


class AircraftServiceEvent(Base):
    __tablename__ = "aircraft_service_events"
    organization_id = Column(String(36), primary_key=True)
    event_id = Column(String(36), primary_key=True)
    remote_id = Column(String(160), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    data_json = Column(Text, nullable=False)


class EquipmentMail(Base):
    __tablename__ = "equipment_mail_outbox"
    organization_id = Column(String(36), primary_key=True)
    event_id = Column(String(36), primary_key=True)
    recipient = Column(String(320), primary_key=True)
    data_json = Column(Text, nullable=False)
    state = Column(String(24), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)


def bounded_text(value, field, limit=200, required=False):
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise ValueError(f"{field} is invalid.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} is required.")
    return value


def grams(value, field):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a weight in grams.")
    try:
        weight = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a weight in grams.")
    if not math.isfinite(weight) or weight < 0 or weight > 100000:
        raise ValueError(f"{field} is outside the supported range.")
    return weight


def validate_aircraft_details(value):
    if not isinstance(value, dict) or set(value) - {
        "recordId", "serialNumber", "registrationNumber", "baseWeightGrams", "baseWeightIncludes",
        "requiredEquipment", "monitoringEquipment", "accessories",
    }:
        raise ValueError("Aircraft readiness fields are invalid.")
    result = {key: bounded_text(value.get(key, ""), key, 1000) for key in (
        "serialNumber", "registrationNumber", "baseWeightIncludes",
        "requiredEquipment", "monitoringEquipment",
    )}
    record_id = value.get("recordId", "")
    result["recordId"] = str(UUID(record_id)) if record_id else ""
    result["baseWeightGrams"] = grams(value.get("baseWeightGrams"), "Base weight")
    accessories = value.get("accessories", [])
    if not isinstance(accessories, list) or len(accessories) > 32:
        raise ValueError("At most 32 accessories may be configured.")
    ids = set()
    result["accessories"] = []
    for item in accessories:
        if not isinstance(item, dict) or set(item) - {"id", "name", "weightGrams", "required", "group"}:
            raise ValueError("Accessory fields are invalid.")
        key = bounded_text(item.get("id", ""), "Accessory ID", 64, True)
        if key in ids:
            raise ValueError("Accessory IDs must be unique.")
        ids.add(key)
        if not isinstance(item.get("required", False), bool):
            raise ValueError("Required equipment must be true or false.")
        result["accessories"].append({
            "id": key, "name": bounded_text(item.get("name", ""), "Accessory name", 120, True),
            "weightGrams": grams(item.get("weightGrams"), "Accessory weight"),
            "required": item.get("required", False),
            "group": bounded_text(item.get("group", ""), "Accessory group", 64),
        })
    return result


def validate_pilot(value):
    result = {key: bounded_text(value.get(key, ""), key, 160) for key in (
        "callsign", "certificateNumber", "certificateDate", "initialKnowledgeDate", "recurrentTrainingDate", "status"
    )}
    if result["status"] not in {"unrecorded", "active", "inactive"}:
        raise ValueError("Invalid pilot status.")
    for key in ("certificateDate", "initialKnowledgeDate", "recurrentTrainingDate"):
        if result[key]:
            parsed = date.fromisoformat(result[key])
            if parsed > date.today():
                raise ValueError("Certificate/training dates cannot be in the future.")
    if result["status"] == "active" and not all(result[k] for k in ("callsign", "certificateNumber", "certificateDate")):
        raise ValueError("An active pilot requires callsign, certificate number and certificate date.")
    return result


def pilot_valid_until(profile):
    # FAA 107.65 uses knowledge-test/training dates, not certificate issue date.
    dates = [profile.get(key) for key in ("initialKnowledgeDate", "recurrentTrainingDate") if profile.get(key)]
    if not dates or not profile.get("certificateNumber") or profile.get("status") != "active":
        return ""
    latest = date.fromisoformat(max(dates))
    return date(latest.year + 2, latest.month, monthrange(latest.year + 2, latest.month)[1]).isoformat()


def qualified_on(profile, flight_date):
    try:
        if profile.get("status") != "active" or not profile.get("callsign") or not profile.get("certificateNumber"):
            return False
        if not profile.get("certificateDate") or date.fromisoformat(profile["certificateDate"]) > flight_date:
            return False
        dates = [date.fromisoformat(profile[key]) for key in ("initialKnowledgeDate", "recurrentTrainingDate")
                 if profile.get(key) and date.fromisoformat(profile[key]) <= flight_date]
        if not dates:
            return False
        latest = max(dates)
        return flight_date <= date(latest.year + 2, latest.month, monthrange(latest.year + 2, latest.month)[1])
    except (TypeError, ValueError):
        return False


async def historical_pilot(store, org_id, member_id, flight_date, *, require_qualified=True, callsign=None):
    async with store.sessions() as session:
        profile = await session.get(PilotProfile, member_id)
        member = await session.get(OrganizationUser, member_id)
        if not profile or not member or profile.organization_id != org_id or member.organization_id != org_id:
            return None
        revisions = (await session.scalars(select(PilotProfileRevision).where(
            PilotProfileRevision.organization_id == org_id, PilotProfileRevision.member_id == member_id))).all()
        candidates = [json.loads(profile.data_json)]
        for revision in sorted(revisions, key=lambda row: json.loads(row.data_json)["recordedAt"], reverse=True):
            candidates.append(json.loads(revision.data_json)["before"])
        for candidate in candidates:
            if callsign is not None and str(candidate.get("callsign", "")).casefold() != callsign.casefold():
                continue
            if not require_qualified or qualified_on(candidate, flight_date):
                return {**candidate, "memberId": member.id, "name": member.display_name}
        return None


async def device_member(store, credential):
    async with store.sessions() as session:
        device = await session.get(DeviceCredential, credential.id)
        member = await session.get(OrganizationUser, device.authorized_user_id) if device and device.authorized_user_id else None
        if not member or member.organization_id != credential.organization_id or member.state != "active" or "r2c_device" not in member.roles:
            raise HTTPException(403, "An active organization member must authorize this device.")
        return member


async def pilots(store, organization_id):
    async with store.sessions() as session:
        rows = (await session.execute(select(PilotProfile, OrganizationUser).join(
            OrganizationUser, OrganizationUser.id == PilotProfile.member_id
        ).where(PilotProfile.organization_id == organization_id))).all()
        result = []
        for profile, member in rows:
            value = json.loads(profile.data_json)
            valid_until = pilot_valid_until(value)
            result.append({**value, "memberId": member.id, "name": member.display_name,
                "accountActive": member.state == "active", "validUntil": valid_until,
                "eligible": member.state == "active" and bool(valid_until) and valid_until >= date.today().isoformat()})
        return result


def preserve_legacy_aircraft_fields(current, proposed):
    """Carry fields an older app cannot represent through the reviewed proposal."""
    if proposed.get("aircraftSchemaVersion") == 1:
        old = {d["remoteId"].casefold(): d for d in current.get("droneSpecs", [])}
        for entry in proposed.get("droneSpecs", []):
            if "readiness" in old.get(entry["remoteId"].casefold(), {}) and "readiness" not in entry:
                raise ValueError("The source app omitted aircraft readiness data.")
        return proposed
    old = {d["remoteId"].casefold(): d for d in current.get("droneSpecs", [])}
    entries = []
    for entry in proposed.get("droneSpecs", []):
        previous = old.get(entry["remoteId"].casefold(), {})
        preserved = {key: previous[key] for key in ("readiness", "ownerName", "ownerCallsign") if key in previous}
        entries.append({**entry, **preserved})
    return with_aircraft_identities({**proposed, "droneSpecs": entries})


async def deliver_mail(store, sender):
    if store is None or not sender.is_configured:
        return
    async with store.sessions() as session:
        jobs = (await session.scalars(select(EquipmentMail).where(
            EquipmentMail.state == "pending").limit(25))).all()
        for job in jobs:
            # Recheck membership at delivery, so revoked recipients receive no new notes.
            members = (await session.scalars(select(OrganizationUser).where(
                OrganizationUser.organization_id == job.organization_id,
                OrganizationUser.email == job.recipient,
                OrganizationUser.state == "active"))).all()
            if not any("equip_manager" in member.roles for member in members):
                job.state = "cancelled"
                continue
            org = await session.get(Organization, job.organization_id)
            event = json.loads(job.data_json)
            job.attempts += 1
            await session.commit()
            try:
                await asyncio.to_thread(sender.send_equipment_status,
                    recipient=job.recipient, event=event, designator=org.designator)
            except Exception:
                # Retain durable pending work; a failed email never undoes a report.
                continue
            job.state = "sent"
        await session.commit()


async def mail_worker(store, sender, stop, logger):
    while not stop.is_set():
        try:
            await deliver_mail(store, sender)
        except Exception:
            logger.exception("Equipment notification delivery failed; queued work retained")
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass


async def record_service(store, organization_id, remote_id, actor, payload, aircraft_id=None):
    if "r2c_device" not in actor.roles or actor.state != "active" or actor.organization_id != organization_id:
        raise HTTPException(403, "r2c_device role required.")
    try:
        event_id = str(UUID(payload.get("eventId", "")))
        note = bounded_text(payload.get("note", ""), "Problem/remediation note", 4000, True)
        reported_at = bounded_text(payload.get("reportedAt", ""), "Client report time", 64)
        if reported_at:
            datetime.fromisoformat(reported_at)
        status = payload.get("status")
        if isinstance(payload.get("revision"), bool):
            raise ValueError("Invalid revision.")
        revision = int(payload.get("revision", -1))
        self_remediation = payload.get("selfRemediation")
        if status not in {"in_service", "out_of_service"}:
            raise ValueError("Invalid service status.")
        if status == "out_of_service" and not isinstance(self_remediation, bool):
            raise ValueError("Specify whether you will address the problem yourself.")
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    remote_id = remote_id.strip().upper()
    service_key = aircraft_id or remote_id
    async with store.sessions() as session:
        previous = await session.get(AircraftServiceEvent, (organization_id, event_id))
        if previous:
            event = json.loads(previous.data_json)
            if previous.remote_id != service_key or event["actorId"] != actor.id or event["note"] != note or event["status"] != status or event["selfRemediation"] != self_remediation:
                raise HTTPException(409, "This event ID was already used for a different report.")
            return event
        current = await session.get(AircraftService, (organization_id, service_key))
        if current is None:
            current = AircraftService(organization_id=organization_id, remote_id=service_key, revision=0)
            session.add(current)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                raise HTTPException(409, "Aircraft status changed. Refresh before reporting.")
        previous_status = current.status
        changed = await session.execute(update(AircraftService).where(
            AircraftService.organization_id == organization_id,
            AircraftService.remote_id == service_key, AircraftService.revision == revision
        ).values(revision=revision + 1, status=status))
        if changed.rowcount != 1:
            raise HTTPException(409, "Aircraft status changed. Review the latest report before submitting.")
        event = {"eventId": event_id, "remoteId": remote_id, "aircraftId": service_key, "revision": revision + 1,
                 "status": status, "previousStatus": previous_status, "note": note, "selfRemediation": self_remediation,
                 "actorId": actor.id, "username": actor.email, "actorName": actor.display_name,
                 "receivedAt": utc_now().isoformat()}
        members = (await session.scalars(select(OrganizationUser).where(
            OrganizationUser.organization_id == organization_id, OrganizationUser.state == "active"
        ))).all()
        event["notificationsQueued"] = sum("equip_manager" in member.roles for member in members)
        event["clientReportedAt"] = reported_at
        encoded = json.dumps(event)
        history = AircraftServiceEvent(organization_id=organization_id, event_id=event_id,
                    remote_id=service_key, revision=revision + 1, data_json=encoded)
        session.add(history)
        session.add(audit_for_history(history))
        for member in members:
            if "equip_manager" in member.roles:
                session.add(EquipmentMail(organization_id=organization_id, event_id=event_id,
                            recipient=member.email, data_json=encoded))
        await session.commit()
        return event



def aircraft_key(entry):
    return entry.get("readiness", {}).get("recordId") or str(uuid5(NAMESPACE_URL, "r2c-aircraft:" + entry["remoteId"].upper()))


def with_aircraft_identities(snapshot):
    """Give legacy entries a stable identity before an app can edit their RID."""
    entries = []
    for entry in snapshot.get("droneSpecs", []):
        details = validate_aircraft_details(entry.get("readiness", {}))
        details["recordId"] = aircraft_key(entry)
        entries.append({**entry, "readiness": details})
    return {**snapshot, "aircraftSchemaVersion": 1, "droneSpecs": entries}

def install_routes(app, ctx):
    from fastapi import Depends

    async def fleet(organization_id, include_history=True):
        store = ctx["control_plane_store"]
        release = await store.get_current_organization_config_release(organization_id)
        entries = with_aircraft_identities(release.snapshot)["droneSpecs"] if release else []
        async with store.sessions() as session:
            query = select(AircraftServiceEvent).where(AircraftServiceEvent.organization_id == organization_id)
            if not include_history:
                query = query.join(AircraftService, (AircraftService.organization_id == AircraftServiceEvent.organization_id)
                    & (AircraftService.remote_id == AircraftServiceEvent.remote_id)
                    & (AircraftService.revision == AircraftServiceEvent.revision))
            events = (await session.scalars(query.order_by(AircraftServiceEvent.revision.desc()))).all()
        result = [{**entry, "history": [json.loads(e.data_json) for e in events
                    if e.remote_id in {entry["remoteId"].upper(), aircraft_key(entry)}]} for entry in entries]
        return sorted(result, key=lambda entry: (0 if entry["history"] and entry["history"][0]["status"] == "out_of_service" else 1, entry["remoteId"]))

    @app.get("/{designator}/api/v1/aircraft-readiness")
    async def readiness_state(designator: str, credential=Depends(ctx["get_api_key"])):
        credential = ctx["require_scoped_upload_credential"](designator, credential)
        member = await device_member(ctx["control_plane_store"], credential)
        from operating_profiles import catalog
        return JSONResponse({"organizationId": credential.organization_id,
            "operatingProfiles": await catalog(ctx["control_plane_store"], credential.organization_id), "memberId": member.id, "username": member.email,
            "canEditAircraft": "config_admin" in member.roles,
            "fetchedAt": utc_now().isoformat(),
            "configurationVersion": await ctx["control_plane_store"].get_organization_config_version_ms(credential.organization_id),
            "aircraft": await fleet(credential.organization_id, include_history=False),
            "pilots": await pilots(ctx["control_plane_store"], credential.organization_id)},
            headers={"Cache-Control": "private, no-store"})

    @app.get("/{designator}/aircraft", response_class=HTMLResponse)
    async def aircraft_page(request: Request, designator: str):
        org, user = await ctx["require_organization_user"](request, designator,
            required_roles=("r2c_device", "records_viewer", "records_admin", "equip_manager", "organization_owner"), redirect_to_login=True,
            login_next=f"/{designator.lower()}/aircraft")
        return ctx["templates"].TemplateResponse(request=request, name="aircraft_status.html", context={
            "request": request, "organization": org, "actor": user,
            "aircraft": await fleet(org.id), "form_token": ctx["csrf_token"](request, "organization_aircraft_service"), "new_event_id": lambda: str(uuid4()),
            "include_leaflet": False, "enable_live_refresh": False,
            "organization_page_designator": org.designator,
            "organization_identity_name": user.display_name,
            "has_equipment_manager": any("equip_manager" in member.roles and member.state == "active"
                for member in await ctx["control_plane_store"].list_users(org.id)),
        }, headers={"Cache-Control": "private, no-store"})

    @app.post("/{designator}/aircraft/service")
    async def service_report(request: Request, designator: str):
        org, user = await ctx["require_organization_user"](request, designator,
            required_roles=("r2c_device",), redirect_to_login=True)
        form = await request.form()
        ctx["verify_csrf"](request, "organization_aircraft_service", str(form.get("form_token", "")))
        if form.get("actor_id") != user.id:
            raise HTTPException(409, "Signed-in operator changed. Reload before submitting this report.")
        rid = str(form.get("remote_id", "")).strip().upper()
        registered = next((d for d in await fleet(org.id, include_history=False) if d["remoteId"].upper() == rid), None)
        if registered is None:
            raise HTTPException(404, "Aircraft is not registered in this organization.")
        event = await record_service(ctx["control_plane_store"], org.id, rid, user, {
            "eventId": form.get("event_id"), "note": form.get("note"), "status": form.get("status"),
            "reportedAt": form.get("reported_at", ""),
            "revision": form.get("revision"), "selfRemediation": form.get("self_remediation") == "yes"
                if form.get("self_remediation") in {"yes", "no"} else None,
        }, aircraft_id=aircraft_key(registered))
        await ctx["r2c_hub"].notify_aircraft_readiness_changed(org.id)
        if request.headers.get("X-R2C-Response") == "json":
            return JSONResponse(event, headers={"Cache-Control": "private, no-store"})
        return RedirectResponse(f"/{org.designator.lower()}/aircraft", status_code=303)

    @app.post("/{designator}/members/{member_id}/pilot")
    async def save_pilot(request: Request, designator: str, member_id: str):
        org, user = await ctx["require_organization_user"](request, designator,
            required_roles=("user_admin", "organization_owner"), redirect_to_login=True)
        form = await request.form()
        ctx["verify_csrf"](request, "organization_pilot_profile", str(form.get("form_token", "")))
        try:
            data = validate_pilot(dict(form))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        store = ctx["control_plane_store"]
        async with store.sessions() as session:
            member = await session.get(OrganizationUser, member_id)
            if not member or member.organization_id != org.id:
                raise HTTPException(404, "Member not found.")
            profile = await session.get(PilotProfile, member_id)
            if profile is None:
                profile = PilotProfile(member_id=member_id, organization_id=org.id)
                session.add(profile)
            before = json.loads(profile.data_json or "{}")
            profile.callsign_key = data["callsign"].casefold() or None
            profile.data_json = json.dumps(data)
            history = PilotProfileRevision(id=str(uuid4()), organization_id=org.id, member_id=member_id,
                data_json=json.dumps({"before": before, "after": data, "editorId": user.id,
                    "username": user.email, "recordedAt": utc_now().isoformat()}))
            session.add(history)
            session.add(audit_for_history(history))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise HTTPException(409, "That pilot callsign belongs to another member.")
        await ctx["r2c_hub"].notify_aircraft_readiness_changed(org.id)
        return RedirectResponse(f"/{org.designator.lower()}/admin/members", status_code=303)
