"""Internal, read-only adoption reports from retained uploaded flight records."""
import csv
import hashlib
import hmac
import io
import json
import math
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from flight_readiness_records import FlightReadinessRecord, key_for


def period_bounds(period, as_of, timezone_name):
    zone = ZoneInfo(timezone_name)
    if period == "daily":
        end = as_of
        start = end - timedelta(days=1)
        previous = start - timedelta(days=1)
    elif period == "weekly":
        end = as_of - timedelta(days=as_of.weekday())
        start = end - timedelta(days=7)
        previous = start - timedelta(days=7)
    elif period == "monthly":
        end = as_of.replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
        previous = (start - timedelta(days=1)).replace(day=1)
    else:
        raise ValueError("Choose daily, weekly, or monthly.")
    return tuple(datetime.combine(d, time.min, zone).astimezone(UTC)
                 for d in (previous, start, end))


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def pilot_token(organization_id, member_id, secret):
    payload = json.dumps(["adoption-pilot-v1", organization_id, member_id]).encode()
    return "P-" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()[:16]


def summarize(organizations, first_seen, flights, readiness, bounds, timezone_name, secret):
    previous, start, end = bounds
    zone = ZoneInfo(timezone_name)
    rows = {}
    for org in organizations:
        rows[org.id] = {"organization": org.legal_name, "designator": org.designator}
    # Preserve unassigned/deleted-organization activity without inventing attribution.
    for org_id in first_seen:
        rows.setdefault(org_id, {"organization": "Unassigned / legacy" if not org_id else "Unknown organization",
                                 "designator": ""})
    for org_id, row in rows.items():
        first = first_seen.get(org_id)
        row.update(first_recorded_flight=utc(first).astimezone(zone).date().isoformat() if first else None)
        for prefix in ("current", "previous"):
            row[prefix] = {"flights": 0, "minutes": 0.0, "pilots": set(), "days": set(),
                           "unknown_pilot_flights": 0, "unknown_duration_flights": 0, "models": {}}
    details = []
    for flight in flights:
        timestamp = utc(flight.start_time)
        if not previous <= timestamp < end:
            continue
        org_id = flight.organization_id
        row = rows[org_id]
        prefix = "current" if timestamp >= start else "previous"
        bucket = row[prefix]
        record = readiness.get((org_id, key_for(flight)), {})
        pilot = record.get("pilot", {})
        member = pilot.get("memberId") if isinstance(pilot, dict) else None
        matched = record.get("pilotAttribution") in {
            "operator_selected_record_matched", "operator_selected_member_matched"}
        token = pilot_token(org_id, member, secret) if org_id and member and matched else None
        if token:
            bucket["pilots"].add(token)
        else:
            bucket["unknown_pilot_flights"] += 1
        minutes = flight.hours * 60 if flight.hours is not None else None
        if minutes is None or not math.isfinite(minutes) or minutes < 0:
            minutes = None
            bucket["unknown_duration_flights"] += 1
        else:
            bucket["minutes"] += minutes
        # The model field may contain legacy shorthand. Never substitute a Remote ID.
        model = str(flight.uas or "").strip().lower()
        if not model or (flight.remote_id and model == flight.remote_id.strip().lower()):
            model = "unknown"
        bucket["models"][model] = bucket["models"].get(model, 0) + 1
        bucket["flights"] += 1
        bucket["days"].add(timestamp.astimezone(zone).date())
        details.append({"period": prefix, "organization": row["organization"],
                        "designator": row["designator"], "pilot_number": token or "unknown",
                        "drone_model": model, "duration_minutes": round(minutes, 2) if minutes is not None else None})
    totals = {"active_organizations": 0, "previous_active_organizations": 0,
              "new_organizations": 0, "retained_organizations": 0,
              "reactivated_organizations": 0, "quiet_organizations": 0}
    for org_id, row in rows.items():
        current, prior = row["current"], row["previous"]
        active, was_active = bool(current["flights"]), bool(prior["flights"])
        first = first_seen.get(org_id)
        state = ("New" if active and first and utc(first) >= start else
                 "Retained" if active and was_active else "Reactivated" if active else
                 "Quiet" if was_active else "No activity")
        row["status"] = state if org_id else "Unassigned"
        if org_id:
            totals["active_organizations"] += active
            totals["previous_active_organizations"] += was_active
            for status, metric in (("New", "new"), ("Retained", "retained"),
                                   ("Reactivated", "reactivated"), ("Quiet", "quiet")):
                totals[metric + "_organizations"] += state == status
        row["retained_pilots"] = len(current["pilots"] & prior["pilots"])
        row["flight_change"] = current["flights"] - prior["flights"]
        row["flight_change_percent"] = round(100 * row["flight_change"] / prior["flights"], 1) if prior["flights"] else None
        row["minutes_change"] = round(current["minutes"] - prior["minutes"], 2)
        for bucket in (current, prior):
            bucket["pilots"] = len(bucket["pilots"])
            bucket["active_days"] = len(bucket.pop("days"))
            bucket["minutes"] = round(bucket["minutes"], 2)
    denominator = totals["previous_active_organizations"]
    totals["organization_retention_percent"] = round(100 * totals["retained_organizations"] / denominator, 1) if denominator else None
    return {"timezone": timezone_name, "previous_start": previous.astimezone(zone).date().isoformat(),
            "period_start": start.astimezone(zone).date().isoformat(),
            "period_end_exclusive": end.astimezone(zone).date().isoformat(), "totals": totals,
            "organizations": sorted(rows.values(), key=lambda r: (-r["flight_change"], -r["current"]["flights"], r["organization"])),
            "flights": details}


async def load_report(db, store, Flight, period, as_of, timezone_name, secret):
    bounds = period_bounds(period, as_of, timezone_name)
    # Flight timestamps use the existing naive-UTC database convention.
    previous, _, end = [b.replace(tzinfo=None) for b in bounds]
    first_seen = dict((await db.execute(select(Flight.organization_id, func.min(Flight.start_time))
        .where(Flight.start_time < end).group_by(Flight.organization_id))).all())
    flights = (await db.execute(select(Flight.organization_id, Flight.start_time, Flight.remote_id,
        Flight.uas, Flight.hours).where(Flight.start_time >= previous, Flight.start_time < end)
        .order_by(Flight.start_time, Flight.id))).all()
    readiness = {}
    keys = list({key_for(f) for f in flights if f.organization_id})
    async with store.sessions() as session:
        for offset in range(0, len(keys), 400):
            records = (await session.execute(select(FlightReadinessRecord).where(
                FlightReadinessRecord.flight_key.in_(keys[offset:offset + 400])))).scalars()
            for record in records:
                try:
                    value = json.loads(record.effective_json)
                    readiness[(record.organization_id, record.flight_key)] = value if isinstance(value, dict) else {}
                except (ValueError, TypeError):
                    pass
    report = summarize(await store.list_organizations(), first_seen, flights, readiness,
                       bounds, timezone_name, secret)
    report.update(period=period, as_of=as_of.isoformat(), generated_at=datetime.now(UTC).isoformat())
    return report


def export_csv(report):
    output = io.StringIO()
    fields = ["period", "organization", "designator", "pilot_number", "drone_model", "duration_minutes"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in report["flights"]:
        # Spreadsheet formula injection protection for user-entered labels.
        writer.writerow({key: ("'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value)
                         for key, value in row.items()})
    return output.getvalue()
