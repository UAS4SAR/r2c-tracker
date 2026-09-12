"""Organization audit summaries for the four immutable readiness histories."""
import json
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from control_plane import (
    AUDIT_EVENT_RETENTION_DAYS, ControlPlaneAuditEvent, Organization, as_utc, utc_now,
)


def audit_for_history(row):
    """Use the same identity and original attribution for live writes and backfill.

    Summaries identify the change; full qualification data and flight snapshots
    remain in the original histories rather than being copied into a wider log.
    """
    source = row.__tablename__
    data = json.loads(row.data_json)
    before, after = data.get("before", {}), data.get("after", {})
    details = {"history_table": source, "editor_email": data.get("username", "")}
    if source == "pilot_profile_revisions":
        key = [row.id]
        event_type = "member.pilot_profile_updated"
        details.update(member_id=row.member_id, old_callsign=before.get("callsign", ""),
                       new_callsign=after.get("callsign", ""))
    elif source == "operating_profile_revisions":
        key = [row.revision]
        event_type = "organization.operating_profiles_updated"
        old_profiles = {p["id"]: p for p in before.get("profiles", [])}
        details.update(revision=row.revision, profiles=[
            {"id": p["id"], "name": p["name"], "version": p["version"]}
            for p in after.get("profiles", []) if p != old_profiles.get(p["id"])],
            old_default=before.get("defaultProfileId"), new_default=after.get("defaultProfileId"))
    elif source == "aircraft_service_events":
        key = [row.event_id]
        event_type = "organization.aircraft_service_reported"
        details.update(aircraft_id=row.remote_id, remote_id=data.get("remoteId", ""),
                       revision=row.revision, previous_status=data.get("previousStatus"),
                       status=data.get("status"))
    elif source == "flight_readiness_corrections":
        key = [row.flight_key, row.revision]
        event_type = "recording.readiness_corrected"
        details.update(flight_key=row.flight_key, revision=row.revision,
                       reason=data.get("reason", ""))
    else:
        raise ValueError("Unsupported readiness history")
    if source != "aircraft_service_events":
        details["changed_fields"] = sorted(k for k in before.keys() | after.keys()
                                            if before.get(k) != after.get(k))
    details["history_key"] = key
    actor_id = data.get("actorId") if source == "aircraft_service_events" else data.get("editorId")
    timestamp = data.get("receivedAt") if source == "aircraft_service_events" else (
        data.get("correctedAt") if source == "flight_readiness_corrections" else data.get("recordedAt"))
    recorded_at = datetime.fromisoformat(timestamp)
    if not actor_id or recorded_at.tzinfo is None:
        raise ValueError("History is missing original attribution or timezone")
    identity = json.dumps([source, row.organization_id, key], separators=(",", ":"))
    return ControlPlaneAuditEvent(
        id=str(uuid5(NAMESPACE_URL, "r2c-readiness-audit:" + identity)),
        organization_id=row.organization_id, actor_type="organization_user", actor_id=actor_id,
        event_type=event_type, details_json=json.dumps(details), created_at=as_utc(recorded_at),
    )


async def backfill_readiness_audit(store, *, now=None):
    """Repeatable startup backfill, bounded batches and normal audit retention.

    Conflict-ignore is intentional: an overlapping startup or a new live write
    must never overwrite an existing event (including retention holds).
    """
    from aircraft_readiness import PilotProfileRevision, AircraftServiceEvent
    from operating_profiles import OperatingProfileRevision
    from flight_readiness_records import FlightReadinessCorrection

    cutoff = as_utc(now or utc_now()) - timedelta(days=AUDIT_EVENT_RETENTION_DAYS)
    insert = pg_insert if store.engine.dialect.name == "postgresql" else sqlite_insert
    counts = {"inserted": 0, "existing": 0, "expired": 0, "invalid": 0}
    for model in (PilotProfileRevision, OperatingProfileRevision, AircraftServiceEvent, FlightReadinessCorrection):
        offset = 0
        while True:
            async with store.sessions() as session:
                rows = (await session.scalars(select(model).join(
                    Organization, Organization.id == model.organization_id
                ).order_by(*model.__table__.primary_key.columns).offset(offset).limit(250))).all()
                if not rows:
                    break
                for row in rows:
                    try:
                        audit = audit_for_history(row)
                    except (ValueError, TypeError, KeyError, AttributeError):
                        counts["invalid"] += 1
                        continue
                    if audit.created_at < cutoff:
                        counts["expired"] += 1
                        continue
                    result = await session.execute(insert(ControlPlaneAuditEvent).values(
                        id=audit.id, organization_id=audit.organization_id, actor_type=audit.actor_type,
                        actor_id=audit.actor_id, event_type=audit.event_type,
                        details_json=audit.details_json, created_at=audit.created_at,
                    ).on_conflict_do_nothing(index_elements=["id"]))
                    counts["inserted" if result.rowcount else "existing"] += 1
                await session.commit()
                offset += len(rows)
    return counts
