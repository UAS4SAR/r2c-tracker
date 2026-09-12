"""Historical readiness snapshots and attributed, optimistic record corrections."""
import hashlib
import json
from datetime import datetime
from control_plane import as_utc

from fastapi import HTTPException, Request, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy import Column, String, Text, Integer, select, update
from sqlalchemy.exc import IntegrityError

from readiness_audit import audit_for_history

from control_plane import Base, utc_now
from aircraft_readiness import bounded_text, grams, pilots, historical_pilot, validate_aircraft_details


class FlightReadinessRecord(Base):
    __tablename__ = "flight_readiness_records"
    organization_id = Column(String(36), primary_key=True)
    flight_key = Column(String(64), primary_key=True)
    revision = Column(Integer, nullable=False, default=0)
    original_json = Column(Text, nullable=False)
    effective_json = Column(Text, nullable=False)


class FlightReadinessCorrection(Base):
    __tablename__ = "flight_readiness_corrections"
    organization_id = Column(String(36), primary_key=True)
    flight_key = Column(String(64), primary_key=True)
    revision = Column(Integer, primary_key=True)
    data_json = Column(Text, nullable=False)


def flight_local_time(ctx, flight, value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if value is None:
        return "Not recorded"
    value = as_utc(value)
    localize = ctx.get("localize_flight_time")
    if localize:
        value = localize(value, getattr(flight, "start_lat", None), getattr(flight, "start_lng", None))
    return value.strftime("%b %d, %Y %I:%M:%S %p %Z")


def key_for(flight):
    # Stable across archive reimports; independent of editable callsign/model.
    value = f"{flight.organization_id}|{(flight.remote_id or '').upper()}|{flight.start_time.isoformat()}"
    return hashlib.sha256(value.encode()).hexdigest()


def extract(data):
    for feature in data.get("features", []):
        props = feature.get("properties", {}).get("r2c_prop", {})
        value = props.get("flightReadiness") if isinstance(props, dict) else None
        if isinstance(value, dict) and len(json.dumps(value).encode("utf-8")) <= 4 * 1024 * 1024:
            return value
    return {}


async def preserve_submission(store, flight, data):
    if store is None or not flight.organization_id:
        return
    original = extract(data)
    value = json.dumps(original)
    effective = {}
    issues = []
    for key in ("payloadDescription", "confirmedAt", "rosterFetchedAt"):
        effective[key] = original.get(key, "") if isinstance(original.get(key, ""), str) else ""
    try:
        effective["aircraft"] = validate_aircraft_details(original.get("aircraft", {}))
        effective["payloadWeightGrams"] = grams(original.get("payloadWeightGrams"), "Payload weight")
    except ValueError:
        issues.append("Invalid aircraft or payload measurements; original values retained for review.")
    selected = original.get("selectedAccessories", [])
    effective["selectedAccessories"] = list(dict.fromkeys(selected)) if isinstance(selected, list) and all(isinstance(key, str) for key in selected) else []
    reported_pilot = original.get("pilot", {})
    pilot = await historical_pilot(store, flight.organization_id, reported_pilot.get("memberId", ""), flight.start_time.date()) if isinstance(reported_pilot, dict) else None
    effective["reportedPilotCallsign"] = reported_pilot.get("callsign", "") if isinstance(reported_pilot, dict) else ""
    if pilot and pilot["callsign"].casefold() == str(reported_pilot.get("callsign", "")).casefold():
        effective["pilot"] = pilot
        effective["pilotAttribution"] = "operator_selected_record_matched"
    else:
        identity = await historical_pilot(store, flight.organization_id, reported_pilot.get("memberId", ""),
            flight.start_time.date(), require_qualified=False, callsign=effective["reportedPilotCallsign"]) if isinstance(reported_pilot, dict) else None
        effective["pilot"] = identity or {}
        effective["pilotAttribution"] = "operator_selected_member_matched" if identity else "unresolved"
        if identity:
            issues.append("RPIC identity matched; qualifications for the flight date are not verified.")
    if isinstance(original.get("service"), dict):
        effective["service"] = original["service"]
    effective["configurationVersion"] = original.get("configurationVersion")
    # Preserve the reported immutable profile and time-stamped changes, never resolve
    # historical conditions from the latest organization catalog.
    submitted_profile = original.get("operatingProfile")
    if isinstance(submitted_profile, dict) and isinstance(submitted_profile.get("profile"), dict):
        effective["operatingProfile"] = submitted_profile
    submitted_changes = original.get("operatingProfileChanges")
    if isinstance(submitted_changes, list):
        effective["operatingProfileChanges"] = [item for item in submitted_changes if isinstance(item, dict)]
    from operating_profiles import review_snapshot
    effective["operatingProfileReviewIssues"] = review_snapshot(original.get("operatingProfile"), flight.start_time.date())
    changes = original.get("operatingProfileChanges", [])
    if isinstance(changes, list):
        for change in changes:
            if isinstance(change, dict):
                effective["operatingProfileReviewIssues"].extend(review_snapshot(change.get("after"), flight.start_time.date()))
    effective["reviewIssues"] = issues
    async with store.sessions() as session:
        key = (flight.organization_id, key_for(flight))
        if await session.get(FlightReadinessRecord, key) is not None:
            return
        session.add(FlightReadinessRecord(organization_id=key[0], flight_key=key[1],
                    original_json=value, effective_json=json.dumps(effective), revision=0))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()  # Another import preserved it first.


def total_weight(snapshot):
    try:
        aircraft = snapshot.get("aircraft", {})
        base = grams(aircraft.get("baseWeightGrams"), "Base weight")
        payload = grams(snapshot.get("payloadWeightGrams"), "Payload weight")
        selected = snapshot.get("selectedAccessories", [])
        accessories = {item["id"]: item for item in aircraft.get("accessories", [])}
        if base is None or any(key not in accessories for key in selected):
            return None
        if snapshot.get("payloadDescription", "").strip() and payload is None:
            return None
        weights = [grams(accessories[key].get("weightGrams"), "Accessory weight") for key in selected]
        groups = [accessories[key].get("group") for key in selected if accessories[key].get("group")]
        if None in weights or len(groups) != len(set(groups)):
            return None
        return base + sum(weights) + (payload or 0)
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def needs_completion(snapshot):
    return (not snapshot.get("pilot", {}).get("memberId") or total_weight(snapshot) is None
            or bool(snapshot.get("reviewIssues")) or bool(snapshot.get("operatingProfileReviewIssues")))


async def correct(store, flight, actor, revision, changes, reason):
    if actor.organization_id != flight.organization_id or actor.state != "active" or not {"records_admin", "organization_owner"}.intersection(actor.roles):
        raise HTTPException(403, "Records administrator required.")
    reason = bounded_text(reason, "Correction reason / measurement basis", 4000, True)
    async with store.sessions() as session:
        key = (flight.organization_id, key_for(flight))
        record = await session.get(FlightReadinessRecord, key)
        if record is None:
            raise HTTPException(409, "Refresh this record before editing.")
        before = json.loads(record.effective_json)
        after = {**before, **changes}
        changed = await session.execute(update(FlightReadinessRecord).where(
            FlightReadinessRecord.organization_id == key[0], FlightReadinessRecord.flight_key == key[1],
            FlightReadinessRecord.revision == revision
        ).values(revision=revision + 1, effective_json=json.dumps(after)))
        if changed.rowcount != 1:
            raise HTTPException(409, "Another administrator changed this record. Refresh and review their correction.")
        history = FlightReadinessCorrection(organization_id=key[0], flight_key=key[1], revision=revision + 1,
            data_json=json.dumps({"before": before, "after": after, "reason": reason,
                "editorId": actor.id, "username": actor.email, "correctedAt": utc_now().isoformat(),
                "postFlight": True}))
        session.add(history)
        session.add(audit_for_history(history))
        await session.commit()


async def export_records(store, flights):
    result = {}
    if store is None:
        return result
    async with store.sessions() as session:
        for flight in flights:
            record = await session.get(FlightReadinessRecord, (flight.organization_id, key_for(flight)))
            if record:
                history = (await session.scalars(select(FlightReadinessCorrection).where(
                    FlightReadinessCorrection.organization_id == flight.organization_id,
                    FlightReadinessCorrection.flight_key == key_for(flight)
                ).order_by(FlightReadinessCorrection.revision))).all()
                effective = json.loads(record.effective_json)
                result[flight.id] = {"revision": record.revision, "current": effective,
                    "takeoffWeightGrams": total_weight(effective),
                    "corrections": [json.loads(row.data_json) for row in history]}
    return result


def install_routes(app, ctx):
    async def scoped_flight(request, designator, flight_id, db):
        org, user = await ctx["require_organization_records_admin"](request, designator)
        flight = await db.scalar(select(ctx["Flight"]).where(
            ctx["Flight"].id == flight_id, ctx["Flight"].organization_id == org.id))
        if flight is None:
            raise HTTPException(404, "Flight not found.")
        return org, user, flight

    @app.get("/{designator}/admin/flights/{flight_id}/readiness")
    async def flight_readiness_editor(request: Request, designator: str, flight_id: int, db=Depends(ctx["get_db"])):
        # scoped_flight applies require_organization_records_admin and tenant filtering.
        org, user, flight = await scoped_flight(request, designator, flight_id, db)
        store = ctx["control_plane_store"]
        await preserve_submission(store, flight, {})
        async with store.sessions() as session:
            record = await session.get(FlightReadinessRecord, (org.id, key_for(flight)))
            history = (await session.scalars(select(FlightReadinessCorrection).where(
                FlightReadinessCorrection.organization_id == org.id,
                FlightReadinessCorrection.flight_key == key_for(flight)
            ).order_by(FlightReadinessCorrection.revision))).all()
            effective = json.loads(record.effective_json)
            from operating_profiles import historical_profiles
            profile_versions = await historical_profiles(store, org.id)
            return ctx["templates"].TemplateResponse(request=request, name="flight_readiness.html", context={
                "request": request, "organization": org, "flight": flight, "record": effective,
                "profile_versions": profile_versions,
                "flight_start_local": flight_local_time(ctx, flight, flight.start_time),
                "format_flight_time": lambda value: flight_local_time(ctx, flight, value),
                "original": json.loads(record.original_json), "revision": record.revision,
                "history": [json.loads(item.data_json) for item in history],
                "total_weight": total_weight(effective), "pilots": await pilots(store, org.id),
                "form_token": ctx["csrf_token"](request, "organization_records_admin"),
                "organization_page_designator": org.designator, "organization_identity_name": user.display_name,
                "include_leaflet": False, "enable_live_refresh": False,
            })

    @app.post("/{designator}/admin/flights/{flight_id}/readiness")
    async def amend_flight_readiness(request: Request, designator: str, flight_id: int, db=Depends(ctx["get_db"])):
        # scoped_flight applies require_organization_records_admin and tenant filtering.
        org, user, flight = await scoped_flight(request, designator, flight_id, db)
        form = await request.form()
        ctx["verify_csrf"](request, "organization_records_admin", str(form.get("form_token", "")))
        try:
            changes = {"payloadDescription": bounded_text(form.get("payload_description", ""), "Payload description", 1000),
                       "payloadWeightGrams": grams(form.get("payload_weight"), "Payload weight")}
            async with ctx["control_plane_store"].sessions() as session:
                existing = await session.get(FlightReadinessRecord, (org.id, key_for(flight)))
                if existing is None:
                    raise HTTPException(409, "Refresh the record before editing.")
                snapshot = json.loads(existing.effective_json)
            if form.get("configuration_present") == "yes":
                aircraft = snapshot.get("aircraft", {})
                choices = {item["id"]: item for item in aircraft.get("accessories", [])}
                selected = form.getlist("selected_accessories")
                if len(set(selected)) != len(selected) or any(key not in choices for key in selected):
                    raise ValueError("Select accessories from this flight's saved configuration.")
                groups = [choices[key].get("group") for key in selected if choices[key].get("group")]
                if len(groups) != len(set(groups)):
                    raise ValueError("Choose only one option from each battery/accessory group.")
                changes["selectedAccessories"] = selected
                changes["aircraft"] = {**aircraft, "baseWeightGrams": grams(form.get("base_weight"), "Historical base weight")}
            if str(form.get("operating_profile_correction", "")).strip():
                from operating_profiles import historical_profiles, review_snapshot
                options = await historical_profiles(ctx["control_plane_store"], org.id)
                corrected = next((p for p in options if f"{p['id']}@{p['version']}" == form["operating_profile_correction"]), None)
                if corrected is None:
                    raise ValueError("Select a saved profile version from this organization.")
                changes["operatingProfile"] = {"profile": corrected, "postFlight": True}
                changes["operatingProfileReviewIssues"] = review_snapshot(changes["operatingProfile"], flight.start_time.date())
                changes["operatingProfileCorrectionNote"] = "Historical authority corrected; original timeline retained."
            if str(form.get("checklist_amendment", "")).strip():
                changes["checklistAmendment"] = bounded_text(form["checklist_amendment"], "Post-flight checklist note", 4000)
            if form.get("pilot_member_id"):
                pilot = await historical_pilot(ctx["control_plane_store"], org.id, form["pilot_member_id"], flight.start_time.date())
                if pilot is None:
                    raise ValueError("This pilot's records do not establish qualification on the flight date. Complete the member's qualification history first.")
                changes["pilot"] = pilot
                changes["pilotAttribution"] = "post_flight_correction"
            await correct(ctx["control_plane_store"], flight, user, int(form.get("revision", -1)),
                          changes, str(form.get("reason", "")))
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc))
        return RedirectResponse(f"/{org.designator.lower()}/admin/flights/{flight_id}/readiness", status_code=303)
