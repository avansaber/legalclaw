"""LegalClaw -- calendar & deadlines domain module

Actions for calendar events and deadline tracking (2 tables, 8 actions).
Imported by db_query.py (unified router).
"""
import os
import sys
import uuid
from datetime import datetime, timezone

try:
    sys.path.insert(0, os.path.expanduser("~/.openclaw/erpclaw/lib"))
    from erpclaw_lib.db import get_connection
    from erpclaw_lib.response import ok, err, row_to_dict
    from erpclaw_lib.audit import audit
    from erpclaw_lib.query import Q, P, Table, Field, fn, Order, insert_row, update_row
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_EVENT_TYPES = (
    "hearing", "deposition", "filing_deadline", "statute_of_limitations",
    "trial", "mediation", "meeting", "other",
)
VALID_EVENT_STATUSES = ("scheduled", "completed", "cancelled", "postponed")
VALID_DEADLINE_TYPES = ("filing", "response", "discovery", "statute", "appeal", "other")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    if not conn.execute(Q.from_(Table("company")).select(Field("id")).where(Field("id") == P()).get_sql(), (company_id,)).fetchone():
        err(f"Company {company_id} not found")


def _validate_enum(value, valid_values, field_name):
    if value and value not in valid_values:
        err(f"Invalid {field_name}: {value}. Must be one of: {', '.join(valid_values)}")


# ---------------------------------------------------------------------------
# 1. add-calendar-event
# ---------------------------------------------------------------------------
def add_calendar_event(conn, args):
    _validate_company(conn, args.company_id)

    event_title = getattr(args, "event_title", None)
    if not event_title:
        err("--event-title is required")

    event_date = getattr(args, "event_date", None)
    if not event_date:
        err("--event-date is required")

    event_type = getattr(args, "event_type", None) or "other"
    _validate_enum(event_type, VALID_EVENT_TYPES, "event-type")

    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        if not conn.execute(Q.from_(Table("legalclaw_matter")).select(Field("id")).where(Field("id") == P()).get_sql(), (matter_id,)).fetchone():
            err(f"Matter {matter_id} not found")

    is_critical = 0
    ic = getattr(args, "is_critical", None)
    if ic is not None and str(ic) == "1":
        is_critical = 1

    reminder_days = 7
    rd = getattr(args, "reminder_days", None)
    if rd is not None:
        try:
            reminder_days = int(rd)
        except (ValueError, TypeError):
            pass

    event_id = str(uuid.uuid4())
    now = _now_iso()
    sql, _ = insert_row("legalclaw_calendar_event", {"id": P(), "matter_id": P(), "title": P(), "event_type": P(), "event_date": P(), "event_time": P(), "location": P(), "description": P(), "reminder_days": P(), "is_critical": P(), "status": P(), "company_id": P(), "created_at": P(), "updated_at": P()})
    conn.execute(sql, (
        event_id, matter_id, event_title, event_type, event_date,
        getattr(args, "event_time", None),
        getattr(args, "location", None),
        getattr(args, "event_description", None),
        reminder_days, is_critical, "scheduled",
        args.company_id, now, now,
    ))
    audit(conn, "legalclaw_calendar_event", event_id, "legal-add-calendar-event", args.company_id)
    conn.commit()
    ok({
        "id": event_id, "title": event_title, "event_type": event_type,
        "event_date": event_date, "event_status": "scheduled",
        "is_critical": is_critical,
    })


# ---------------------------------------------------------------------------
# 2. update-calendar-event
# ---------------------------------------------------------------------------
def update_calendar_event(conn, args):
    event_id = getattr(args, "event_id", None)
    if not event_id:
        err("--event-id is required")
    row = conn.execute(Q.from_(Table("legalclaw_calendar_event")).select(Table("legalclaw_calendar_event").star).where(Field("id") == P()).get_sql(), (event_id,)).fetchone()
    if not row:
        err(f"Calendar event {event_id} not found")

    updates, params, changed = [], [], []
    for arg_name, col_name in {
        "event_title": "title", "event_type": "event_type",
        "event_date": "event_date", "event_time": "event_time",
        "location": "location", "event_description": "description",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            if arg_name == "event_type":
                _validate_enum(val, VALID_EVENT_TYPES, "event-type")
            updates.append(f"{col_name} = ?")
            params.append(val)
            changed.append(col_name)

    event_status = getattr(args, "event_status", None)
    if event_status:
        _validate_enum(event_status, VALID_EVENT_STATUSES, "event-status")
        updates.append("status = ?")
        params.append(event_status)
        changed.append("status")

    rd = getattr(args, "reminder_days", None)
    if rd is not None:
        updates.append("reminder_days = ?")
        params.append(int(rd))
        changed.append("reminder_days")

    ic = getattr(args, "is_critical", None)
    if ic is not None:
        updates.append("is_critical = ?")
        params.append(int(ic))
        changed.append("is_critical")

    if not updates:
        err("No fields to update")

    updates.append("updated_at = ?")
    params.append(_now_iso())
    params.append(event_id)
    conn.execute(f"UPDATE legalclaw_calendar_event SET {', '.join(updates)} WHERE id = ?", params)
    audit(conn, "legalclaw_calendar_event", event_id, "legal-update-calendar-event",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": event_id, "updated_fields": changed})


# ---------------------------------------------------------------------------
# 3. list-calendar-events
# ---------------------------------------------------------------------------
def list_calendar_events(conn, args):
    sql = "SELECT * FROM legalclaw_calendar_event WHERE 1=1"
    params = []
    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        sql += " AND matter_id = ?"
        params.append(matter_id)
    event_type = getattr(args, "event_type", None)
    if event_type:
        sql += " AND event_type = ?"
        params.append(event_type)
    event_status = getattr(args, "event_status", None)
    if event_status:
        sql += " AND status = ?"
        params.append(event_status)
    if args.company_id:
        sql += " AND company_id = ?"
        params.append(args.company_id)
    sql += " ORDER BY event_date ASC LIMIT ? OFFSET ?"
    params.extend([args.limit, args.offset])
    rows = conn.execute(sql, params).fetchall()
    ok({"events": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 4. complete-event
# ---------------------------------------------------------------------------
def complete_event(conn, args):
    event_id = getattr(args, "event_id", None)
    if not event_id:
        err("--event-id is required")
    row = conn.execute(Q.from_(Table("legalclaw_calendar_event")).select(Table("legalclaw_calendar_event").star).where(Field("id") == P()).get_sql(), (event_id,)).fetchone()
    if not row:
        err(f"Calendar event {event_id} not found")
    if row["status"] == "completed":
        err(f"Event {event_id} is already completed")

    now = _now_iso()
    conn.execute("UPDATE legalclaw_calendar_event SET status = 'completed', updated_at = ? WHERE id = ?",
                 (now, event_id))
    audit(conn, "legalclaw_calendar_event", event_id, "legal-complete-event",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": event_id, "event_status": "completed"})


# ---------------------------------------------------------------------------
# 5. add-deadline
# ---------------------------------------------------------------------------
def add_deadline(conn, args):
    _validate_company(conn, args.company_id)

    matter_id = getattr(args, "matter_id", None)
    if not matter_id:
        err("--matter-id is required")
    if not conn.execute(Q.from_(Table("legalclaw_matter")).select(Field("id")).where(Field("id") == P()).get_sql(), (matter_id,)).fetchone():
        err(f"Matter {matter_id} not found")

    deadline_title = getattr(args, "deadline_title", None)
    if not deadline_title:
        err("--deadline-title is required")

    due_date = getattr(args, "due_date", None)
    if not due_date:
        err("--due-date is required")

    deadline_type = getattr(args, "deadline_type", None) or "filing"
    _validate_enum(deadline_type, VALID_DEADLINE_TYPES, "deadline-type")

    is_court_imposed = 0
    ico = getattr(args, "is_court_imposed", None)
    if ico is not None and str(ico) == "1":
        is_court_imposed = 1

    dl_id = str(uuid.uuid4())
    now = _now_iso()
    sql, _ = insert_row("legalclaw_deadline", {"id": P(), "matter_id": P(), "title": P(), "deadline_type": P(), "due_date": P(), "is_court_imposed": P(), "assigned_to": P(), "is_completed": P(), "completed_date": P(), "notes": P(), "company_id": P(), "created_at": P(), "updated_at": P()})
    conn.execute(sql, (
        dl_id, matter_id, deadline_title, deadline_type, due_date,
        is_court_imposed,
        getattr(args, "assigned_to", None),
        0, None,
        getattr(args, "notes", None),
        args.company_id, now, now,
    ))
    audit(conn, "legalclaw_deadline", dl_id, "legal-add-deadline", args.company_id)
    conn.commit()
    ok({
        "id": dl_id, "matter_id": matter_id, "title": deadline_title,
        "deadline_type": deadline_type, "due_date": due_date,
        "deadline_status": "pending", "is_court_imposed": is_court_imposed,
    })


# ---------------------------------------------------------------------------
# 6. update-deadline
# ---------------------------------------------------------------------------
def update_deadline(conn, args):
    dl_id = getattr(args, "deadline_id", None)
    if not dl_id:
        err("--deadline-id is required")
    row = conn.execute(Q.from_(Table("legalclaw_deadline")).select(Table("legalclaw_deadline").star).where(Field("id") == P()).get_sql(), (dl_id,)).fetchone()
    if not row:
        err(f"Deadline {dl_id} not found")

    updates, params, changed = [], [], []
    for arg_name, col_name in {
        "deadline_title": "title", "deadline_type": "deadline_type",
        "due_date": "due_date", "assigned_to": "assigned_to", "notes": "notes",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            if arg_name == "deadline_type":
                _validate_enum(val, VALID_DEADLINE_TYPES, "deadline-type")
            updates.append(f"{col_name} = ?")
            params.append(val)
            changed.append(col_name)

    ico = getattr(args, "is_court_imposed", None)
    if ico is not None:
        updates.append("is_court_imposed = ?")
        params.append(int(ico))
        changed.append("is_court_imposed")

    if not updates:
        err("No fields to update")

    updates.append("updated_at = ?")
    params.append(_now_iso())
    params.append(dl_id)
    conn.execute(f"UPDATE legalclaw_deadline SET {', '.join(updates)} WHERE id = ?", params)
    audit(conn, "legalclaw_deadline", dl_id, "legal-update-deadline",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": dl_id, "updated_fields": changed})


# ---------------------------------------------------------------------------
# 7. list-deadlines
# ---------------------------------------------------------------------------
def list_deadlines(conn, args):
    sql = "SELECT * FROM legalclaw_deadline WHERE 1=1"
    params = []
    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        sql += " AND matter_id = ?"
        params.append(matter_id)
    deadline_type = getattr(args, "deadline_type", None)
    if deadline_type:
        sql += " AND deadline_type = ?"
        params.append(deadline_type)
    is_completed = getattr(args, "is_completed", None)
    if is_completed is not None:
        sql += " AND is_completed = ?"
        params.append(int(is_completed))
    if args.company_id:
        sql += " AND company_id = ?"
        params.append(args.company_id)
    sql += " ORDER BY due_date ASC LIMIT ? OFFSET ?"
    params.extend([args.limit, args.offset])
    rows = conn.execute(sql, params).fetchall()
    ok({"deadlines": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 8. complete-deadline
# ---------------------------------------------------------------------------
def complete_deadline(conn, args):
    dl_id = getattr(args, "deadline_id", None)
    if not dl_id:
        err("--deadline-id is required")
    row = conn.execute(Q.from_(Table("legalclaw_deadline")).select(Table("legalclaw_deadline").star).where(Field("id") == P()).get_sql(), (dl_id,)).fetchone()
    if not row:
        err(f"Deadline {dl_id} not found")
    if row["is_completed"]:
        err(f"Deadline {dl_id} is already completed")

    completed_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = _now_iso()
    conn.execute("""
        UPDATE legalclaw_deadline
        SET is_completed = 1, completed_date = ?, updated_at = ?
        WHERE id = ?
    """, (completed_date, now, dl_id))
    audit(conn, "legalclaw_deadline", dl_id, "legal-complete-deadline",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": dl_id, "deadline_status": "completed", "completed_date": completed_date})


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------
ACTIONS = {
    "legal-add-calendar-event": add_calendar_event,
    "legal-update-calendar-event": update_calendar_event,
    "legal-list-calendar-events": list_calendar_events,
    "legal-complete-event": complete_event,
    "legal-add-deadline": add_deadline,
    "legal-update-deadline": update_deadline,
    "legal-list-deadlines": list_deadlines,
    "legal-complete-deadline": complete_deadline,
}
