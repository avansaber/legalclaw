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
    from erpclaw_lib.query import (
        Q, P, Table, Field, fn, Order, LiteralValue,
        insert_row, update_row, dynamic_update,
    )
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ── Table aliases ──
_company = Table("company")
_party = Table("legalclaw_matter_party")
_matter = Table("legalclaw_matter")
_ext = Table("legalclaw_client_ext")
_cust = Table("customer")
_cc = Table("legalclaw_conflict_check")
_cw = Table("legalclaw_conflict_waiver")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    q = Q.from_(_company).select(_company.id).where(_company.id == P())
    if not conn.execute(q.get_sql(), (company_id,)).fetchone():
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
    party_q = (
        Q.from_(_party)
        .join(_matter).on(_party.matter_id == _matter.id)
        .select(
            _party.party_name, _party.party_type, _party.role, _party.matter_id,
            _matter.title.as_("matter_title"), _matter.status.as_("matter_status"),
        )
        .where(_party.party_name.like(P()))
        .where(_party.company_id == P())
    )
    party_matches = conn.execute(party_q.get_sql(), (f"%{search_name}%", args.company_id)).fetchall()

    client_q = (
        Q.from_(_ext)
        .join(_cust).on(_ext.customer_id == _cust.id)
        .left_join(_matter).on(_ext.id == _matter.client_id)
        .select(
            _ext.id, _cust.name, _ext.client_type,
            LiteralValue("GROUP_CONCAT(\"legalclaw_matter\".\"title\", '; ')").as_("matter_titles"),
        )
        .where(_cust.name.like(P()))
        .where(_ext.company_id == P())
        .groupby(_ext.id)
    )
    client_matches = conn.execute(client_q.get_sql(), (f"%{search_name}%", args.company_id)).fetchall()

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
    q = Q.from_(_cc).select(_cc.star).where(_cc.id == P())
    check_row = conn.execute(q.get_sql(), (conflict_check_id,)).fetchone()
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
    upd_sql, upd_params = dynamic_update("legalclaw_conflict_check",
        {"result": "waived"},
        where={"id": conflict_check_id})
    conn.execute(upd_sql, upd_params)

    audit(conn, "legalclaw_conflict_waiver", waiver_id, "legal-add-conflict-waiver", args.company_id)
    conn.commit()
    ok({"id": waiver_id, "conflict_check_id": conflict_check_id,
        "waived_by": waived_by, "result": "waived"})


# ---------------------------------------------------------------------------
# 3. list-conflict-checks
# ---------------------------------------------------------------------------
def list_conflict_checks(conn, args):
    conditions = []
    params = []
    if args.company_id:
        conditions.append(_cc.company_id == P())
        params.append(args.company_id)
    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        conditions.append(_cc.matter_id == P())
        params.append(matter_id)
    result = getattr(args, "conflict_result", None)
    if result:
        conditions.append(_cc.result == P())
        params.append(result)

    q = Q.from_(_cc).select(_cc.star)
    for cond in conditions:
        q = q.where(cond)
    q = q.orderby(_cc.checked_date, order=Order.desc).limit(P()).offset(P())

    rows = conn.execute(q.get_sql(), params + [args.limit, args.offset]).fetchall()
    ok({"conflict_checks": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 4. conflict-report
# ---------------------------------------------------------------------------
def conflict_report(conn, args):
    _validate_company(conn, args.company_id)

    total_q = Q.from_(_cc).select(fn.Count("*").as_("cnt")).where(_cc.company_id == P())
    total = conn.execute(total_q.get_sql(), (args.company_id,)).fetchone()["cnt"]

    by_result_q = (
        Q.from_(_cc)
        .select(_cc.result, fn.Count("*").as_("count"))
        .where(_cc.company_id == P())
        .groupby(_cc.result)
    )
    by_result = conn.execute(by_result_q.get_sql(), (args.company_id,)).fetchall()

    waivers_q = Q.from_(_cw).select(fn.Count("*").as_("cnt")).where(_cw.company_id == P())
    waivers = conn.execute(waivers_q.get_sql(), (args.company_id,)).fetchone()["cnt"]

    recent_q = (
        Q.from_(_cc)
        .select(_cc.id, _cc.search_name, _cc.checked_date, _cc.matches_found, _cc.result)
        .where(_cc.company_id == P())
        .orderby(_cc.checked_date, order=Order.desc)
        .limit(10)
    )
    recent = conn.execute(recent_q.get_sql(), (args.company_id,)).fetchall()

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
