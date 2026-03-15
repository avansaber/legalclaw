"""LegalClaw -- conflict checking domain module

Actions for conflict of interest checking and waivers (2 tables, 4 actions).
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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    if not conn.execute(Q.from_(Table("company")).select(Field("id")).where(Field("id") == P()).get_sql(), (company_id,)).fetchone():
        err(f"Company {company_id} not found")


# ---------------------------------------------------------------------------
# 1. check-conflicts
# ---------------------------------------------------------------------------
def check_conflicts(conn, args):
    _validate_company(conn, args.company_id)

    search_name = getattr(args, "search_name", None)
    if not search_name:
        err("--search-name is required")

    checked_by = getattr(args, "checked_by", None)
    matter_id = getattr(args, "matter_id", None)

    # Search across all matter parties, clients, and opposing counsel
    party_matches = conn.execute("""
        SELECT mp.party_name, mp.party_type, mp.role, mp.matter_id,
               m.title as matter_title, m.status as matter_status
        FROM legalclaw_matter_party mp
        JOIN legalclaw_matter m ON mp.matter_id = m.id
        WHERE mp.party_name LIKE ? AND mp.company_id = ?
    """, (f"%{search_name}%", args.company_id)).fetchall()

    client_matches = conn.execute("""
        SELECT ext.id, cust.name, ext.client_type,
               GROUP_CONCAT(m.title, '; ') as matter_titles
        FROM legalclaw_client_ext ext
        JOIN customer cust ON ext.customer_id = cust.id
        LEFT JOIN legalclaw_matter m ON ext.id = m.client_id
        WHERE cust.name LIKE ? AND ext.company_id = ?
        GROUP BY ext.id
    """, (f"%{search_name}%", args.company_id)).fetchall()

    matches_found = len(party_matches) + len(client_matches)
    match_details = {
        "party_matches": [row_to_dict(r) for r in party_matches],
        "client_matches": [row_to_dict(r) for r in client_matches],
    }

    if matches_found > 0:
        result = "potential"
    else:
        result = "clear"

    check_id = str(uuid.uuid4())
    now = _now_iso()
    import json
    sql, _ = insert_row("legalclaw_conflict_check", {"id": P(), "search_name": P(), "checked_date": P(), "checked_by": P(), "matches_found": P(), "match_details": P(), "result": P(), "matter_id": P(), "company_id": P(), "created_at": P()})
    conn.execute(sql, (
        check_id, search_name,
        datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        checked_by, matches_found,
        json.dumps(match_details),
        result, matter_id, args.company_id, now,
    ))
    audit(conn, "legalclaw_conflict_check", check_id, "legal-check-conflicts", args.company_id)
    conn.commit()
    ok({
        "id": check_id, "search_name": search_name,
        "matches_found": matches_found, "result": result,
        "party_matches": [row_to_dict(r) for r in party_matches],
        "client_matches": [row_to_dict(r) for r in client_matches],
    })


# ---------------------------------------------------------------------------
# 2. add-conflict-waiver
# ---------------------------------------------------------------------------
def add_conflict_waiver(conn, args):
    _validate_company(conn, args.company_id)

    conflict_check_id = getattr(args, "conflict_check_id", None)
    if not conflict_check_id:
        err("--conflict-check-id is required")
    check_row = conn.execute(Q.from_(Table("legalclaw_conflict_check")).select(Table("legalclaw_conflict_check").star).where(Field("id") == P()).get_sql(), (conflict_check_id,)).fetchone()
    if not check_row:
        err(f"Conflict check {conflict_check_id} not found")

    waived_by = getattr(args, "waived_by", None)
    if not waived_by:
        err("--waived-by is required")

    waiver_id = str(uuid.uuid4())
    now = _now_iso()
    matter_id = getattr(args, "matter_id", None)

    sql, _ = insert_row("legalclaw_conflict_waiver", {"id": P(), "conflict_check_id": P(), "matter_id": P(), "waived_by": P(), "waiver_date": P(), "reason": P(), "company_id": P(), "created_at": P()})
    conn.execute(sql, (
        waiver_id, conflict_check_id, matter_id, waived_by,
        datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        getattr(args, "waiver_reason", None),
        args.company_id, now,
    ))

    # Update conflict check result to waived
    conn.execute("UPDATE legalclaw_conflict_check SET result = 'waived' WHERE id = ?",
                 (conflict_check_id,))

    audit(conn, "legalclaw_conflict_waiver", waiver_id, "legal-add-conflict-waiver", args.company_id)
    conn.commit()
    ok({"id": waiver_id, "conflict_check_id": conflict_check_id,
        "waived_by": waived_by, "result": "waived"})


# ---------------------------------------------------------------------------
# 3. list-conflict-checks
# ---------------------------------------------------------------------------
def list_conflict_checks(conn, args):
    sql = "SELECT * FROM legalclaw_conflict_check WHERE 1=1"
    params = []
    if args.company_id:
        sql += " AND company_id = ?"
        params.append(args.company_id)
    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        sql += " AND matter_id = ?"
        params.append(matter_id)
    result = getattr(args, "conflict_result", None)
    if result:
        sql += " AND result = ?"
        params.append(result)
    sql += " ORDER BY checked_date DESC LIMIT ? OFFSET ?"
    params.extend([args.limit, args.offset])
    rows = conn.execute(sql, params).fetchall()
    ok({"conflict_checks": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 4. conflict-report
# ---------------------------------------------------------------------------
def conflict_report(conn, args):
    _validate_company(conn, args.company_id)

    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM legalclaw_conflict_check WHERE company_id = ?",
        (args.company_id,)
    ).fetchone()["cnt"]

    by_result = conn.execute("""
        SELECT result, COUNT(*) as count
        FROM legalclaw_conflict_check WHERE company_id = ?
        GROUP BY result
    """, (args.company_id,)).fetchall()

    waivers = conn.execute(
        "SELECT COUNT(*) as cnt FROM legalclaw_conflict_waiver WHERE company_id = ?",
        (args.company_id,)
    ).fetchone()["cnt"]

    recent = conn.execute("""
        SELECT id, search_name, checked_date, matches_found, result
        FROM legalclaw_conflict_check
        WHERE company_id = ?
        ORDER BY checked_date DESC LIMIT 10
    """, (args.company_id,)).fetchall()

    ok({
        "total_checks": total,
        "by_result": {r["result"]: r["count"] for r in by_result},
        "total_waivers": waivers,
        "recent_checks": [row_to_dict(r) for r in recent],
    })


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------
ACTIONS = {
    "legal-check-conflicts": check_conflicts,
    "legal-add-conflict-waiver": add_conflict_waiver,
    "legal-list-conflict-checks": list_conflict_checks,
    "legal-conflict-report": conflict_report,
}
