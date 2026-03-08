"""LegalClaw -- compliance & reports domain module

Actions for bar admissions, CLE tracking, profitability and practice area analysis (2 tables, 9 actions).
Imported by db_query.py (unified router).
"""
import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal

try:
    sys.path.insert(0, os.path.expanduser("~/.openclaw/erpclaw/lib"))
    from erpclaw_lib.db import get_connection
    from erpclaw_lib.decimal_utils import to_decimal, round_currency
    from erpclaw_lib.naming import get_next_name, ENTITY_PREFIXES
    from erpclaw_lib.response import ok, err, row_to_dict
    from erpclaw_lib.audit import audit

    ENTITY_PREFIXES.setdefault("legalclaw_bar_admission", "LBAR-")
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_BAR_STATUSES = ("active", "inactive", "suspended", "retired")
VALID_CLE_CATEGORIES = ("general", "ethics", "professionalism", "diversity", "substance_abuse", "other")
DEFAULT_DB_PATH = os.path.expanduser("~/.openclaw/erpclaw/data.sqlite")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    if not conn.execute("SELECT id FROM company WHERE id = ?", (company_id,)).fetchone():
        err(f"Company {company_id} not found")


def _validate_enum(value, valid_values, field_name):
    if value and value not in valid_values:
        err(f"Invalid {field_name}: {value}. Must be one of: {', '.join(valid_values)}")


# ---------------------------------------------------------------------------
# 1. add-bar-admission
# ---------------------------------------------------------------------------
def add_bar_admission(conn, args):
    _validate_company(conn, args.company_id)

    attorney_name = getattr(args, "attorney_name", None)
    if not attorney_name:
        err("--attorney-name is required")

    jurisdiction = getattr(args, "jurisdiction", None)
    if not jurisdiction:
        err("--jurisdiction is required")

    admission_status = getattr(args, "admission_status", None) or "active"
    _validate_enum(admission_status, VALID_BAR_STATUSES, "admission-status")

    cle_required = getattr(args, "cle_hours_required", None) or "0"
    to_decimal(cle_required)

    ba_id = str(uuid.uuid4())
    ns = get_next_name(conn, "legalclaw_bar_admission", company_id=args.company_id)
    now = _now_iso()

    conn.execute("""
        INSERT INTO legalclaw_bar_admission (
            id, attorney_name, bar_number, jurisdiction, admission_date,
            expiry_date, status, cle_hours_required, cle_hours_completed,
            company_id, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        ba_id, attorney_name,
        getattr(args, "bar_number", None),
        jurisdiction,
        getattr(args, "admission_date", None),
        getattr(args, "expiry_date", None),
        admission_status, cle_required, "0",
        args.company_id, now, now,
    ))
    audit(conn, "legalclaw_bar_admission", ba_id, "legal-add-bar-admission", args.company_id)
    conn.commit()
    ok({
        "id": ba_id, "attorney_name": attorney_name,
        "jurisdiction": jurisdiction, "admission_status": admission_status,
    })


# ---------------------------------------------------------------------------
# 2. update-bar-admission
# ---------------------------------------------------------------------------
def update_bar_admission(conn, args):
    ba_id = getattr(args, "bar_admission_id", None)
    if not ba_id:
        err("--bar-admission-id is required")
    row = conn.execute("SELECT * FROM legalclaw_bar_admission WHERE id = ?", (ba_id,)).fetchone()
    if not row:
        err(f"Bar admission {ba_id} not found")

    updates, params, changed = [], [], []
    for arg_name, col_name in {
        "attorney_name": "attorney_name", "bar_number": "bar_number",
        "jurisdiction": "jurisdiction", "admission_date": "admission_date",
        "expiry_date": "expiry_date", "cle_hours_required": "cle_hours_required",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            if arg_name == "cle_hours_required":
                to_decimal(val)
            updates.append(f"{col_name} = ?")
            params.append(val)
            changed.append(col_name)

    admission_status = getattr(args, "admission_status", None)
    if admission_status:
        _validate_enum(admission_status, VALID_BAR_STATUSES, "admission-status")
        updates.append("status = ?")
        params.append(admission_status)
        changed.append("status")

    if not updates:
        err("No fields to update")

    updates.append("updated_at = ?")
    params.append(_now_iso())
    params.append(ba_id)
    conn.execute(f"UPDATE legalclaw_bar_admission SET {', '.join(updates)} WHERE id = ?", params)
    audit(conn, "legalclaw_bar_admission", ba_id, "legal-update-bar-admission",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": ba_id, "updated_fields": changed})


# ---------------------------------------------------------------------------
# 3. list-bar-admissions
# ---------------------------------------------------------------------------
def list_bar_admissions(conn, args):
    sql = "SELECT * FROM legalclaw_bar_admission WHERE 1=1"
    params = []
    if args.company_id:
        sql += " AND company_id = ?"
        params.append(args.company_id)
    attorney_name = getattr(args, "attorney_name", None)
    if attorney_name:
        sql += " AND attorney_name LIKE ?"
        params.append(f"%{attorney_name}%")
    admission_status = getattr(args, "admission_status", None)
    if admission_status:
        sql += " AND status = ?"
        params.append(admission_status)
    jurisdiction = getattr(args, "jurisdiction", None)
    if jurisdiction:
        sql += " AND jurisdiction = ?"
        params.append(jurisdiction)
    sql += " ORDER BY attorney_name ASC LIMIT ? OFFSET ?"
    params.extend([args.limit, args.offset])
    rows = conn.execute(sql, params).fetchall()
    ok({"bar_admissions": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 4. add-cle-record
# ---------------------------------------------------------------------------
def add_cle_record(conn, args):
    _validate_company(conn, args.company_id)

    attorney_name = getattr(args, "attorney_name", None)
    if not attorney_name:
        err("--attorney-name is required")

    course_name = getattr(args, "course_name", None)
    if not course_name:
        err("--course-name is required")

    completion_date = getattr(args, "completion_date", None)
    if not completion_date:
        err("--completion-date is required")

    hours_raw = getattr(args, "cle_hours", None) or "0"
    hours = str(to_decimal(hours_raw))

    category = getattr(args, "cle_category", None) or "general"
    _validate_enum(category, VALID_CLE_CATEGORIES, "cle-category")

    bar_admission_id = getattr(args, "bar_admission_id", None)
    if bar_admission_id:
        ba_row = conn.execute("SELECT * FROM legalclaw_bar_admission WHERE id = ?",
                              (bar_admission_id,)).fetchone()
        if not ba_row:
            err(f"Bar admission {bar_admission_id} not found")

    cle_id = str(uuid.uuid4())
    now = _now_iso()
    conn.execute("""
        INSERT INTO legalclaw_cle_record (
            id, attorney_name, bar_admission_id, course_name, provider,
            completion_date, hours, category, certificate_number,
            company_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        cle_id, attorney_name, bar_admission_id, course_name,
        getattr(args, "cle_provider", None),
        completion_date, hours, category,
        getattr(args, "certificate_number", None),
        args.company_id, now,
    ))

    # Auto-update bar admission CLE hours if linked
    if bar_admission_id:
        conn.execute("""
            UPDATE legalclaw_bar_admission
            SET cle_hours_completed = CAST(
                CAST(cle_hours_completed AS REAL) + ? AS TEXT
            ), updated_at = ?
            WHERE id = ?
        """, (float(to_decimal(hours)), now, bar_admission_id))

    audit(conn, "legalclaw_cle_record", cle_id, "legal-add-cle-record", args.company_id)
    conn.commit()
    ok({
        "id": cle_id, "attorney_name": attorney_name,
        "course_name": course_name, "hours": hours, "category": category,
    })


# ---------------------------------------------------------------------------
# 5. list-cle-records
# ---------------------------------------------------------------------------
def list_cle_records(conn, args):
    sql = "SELECT * FROM legalclaw_cle_record WHERE 1=1"
    params = []
    if args.company_id:
        sql += " AND company_id = ?"
        params.append(args.company_id)
    attorney_name = getattr(args, "attorney_name", None)
    if attorney_name:
        sql += " AND attorney_name LIKE ?"
        params.append(f"%{attorney_name}%")
    bar_admission_id = getattr(args, "bar_admission_id", None)
    if bar_admission_id:
        sql += " AND bar_admission_id = ?"
        params.append(bar_admission_id)
    cle_category = getattr(args, "cle_category", None)
    if cle_category:
        sql += " AND category = ?"
        params.append(cle_category)
    sql += " ORDER BY completion_date DESC LIMIT ? OFFSET ?"
    params.extend([args.limit, args.offset])
    rows = conn.execute(sql, params).fetchall()
    ok({"cle_records": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 6. cle-compliance-report
# ---------------------------------------------------------------------------
def cle_compliance_report(conn, args):
    _validate_company(conn, args.company_id)

    admissions = conn.execute("""
        SELECT id, attorney_name, jurisdiction, bar_number, status,
               cle_hours_required, cle_hours_completed, expiry_date
        FROM legalclaw_bar_admission
        WHERE company_id = ? AND status = 'active'
        ORDER BY attorney_name
    """, (args.company_id,)).fetchall()

    attorneys = []
    non_compliant = 0
    for a in admissions:
        required = to_decimal(a["cle_hours_required"] or "0")
        completed = to_decimal(a["cle_hours_completed"] or "0")
        remaining = max(Decimal("0"), required - completed)
        compliant = completed >= required

        if not compliant:
            non_compliant += 1

        # Get CLE breakdown by category
        categories = conn.execute("""
            SELECT category, SUM(CAST(hours AS REAL)) as total_hours
            FROM legalclaw_cle_record
            WHERE bar_admission_id = ? AND company_id = ?
            GROUP BY category
        """, (a["id"], args.company_id)).fetchall()

        attorneys.append({
            "attorney_name": a["attorney_name"],
            "jurisdiction": a["jurisdiction"],
            "bar_number": a["bar_number"],
            "hours_required": str(required),
            "hours_completed": str(completed),
            "hours_remaining": str(remaining),
            "is_compliant": compliant,
            "expiry_date": a["expiry_date"],
            "by_category": {c["category"]: str(round(c["total_hours"], 1)) for c in categories},
        })

    ok({
        "attorneys": attorneys,
        "total_attorneys": len(attorneys),
        "non_compliant_count": non_compliant,
        "compliance_rate": str(round(
            ((len(attorneys) - non_compliant) / len(attorneys) * 100) if attorneys else 100, 1
        )),
    })


# ---------------------------------------------------------------------------
# 7. matter-profitability-report
# ---------------------------------------------------------------------------
def matter_profitability_report(conn, args):
    _validate_company(conn, args.company_id)

    matters = conn.execute("""
        SELECT m.id, m.title, m.practice_area, m.billing_method,
               m.status, m.billed_amount, m.collected_amount, m.budget,
               c.name as client_name
        FROM legalclaw_matter m
        JOIN legalclaw_client c ON m.client_id = c.id
        WHERE m.company_id = ?
        ORDER BY m.opened_date DESC
    """, (args.company_id,)).fetchall()

    results = []
    for m in matters:
        # Total time cost (hours * rate for all entries)
        time_row = conn.execute("""
            SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) as revenue,
                   COALESCE(SUM(CAST(hours AS REAL)), 0) as total_hours
            FROM legalclaw_time_entry WHERE matter_id = ?
        """, (m["id"],)).fetchone()

        expense_row = conn.execute("""
            SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) as total_expenses
            FROM legalclaw_expense WHERE matter_id = ?
        """, (m["id"],)).fetchone()

        revenue = Decimal(str(time_row["revenue"]))
        expenses = Decimal(str(expense_row["total_expenses"]))
        collected = to_decimal(m["collected_amount"] or "0")
        billed = to_decimal(m["billed_amount"] or "0")
        budget = to_decimal(m["budget"] or "0")

        profit = collected - expenses
        margin = round((profit / collected * 100) if collected > 0 else Decimal("0"), 1)
        budget_used = round((billed / budget * 100) if budget > 0 else Decimal("0"), 1)

        results.append({
            "matter_id": m["id"],
            "title": m["title"],
            "client_name": m["client_name"],
            "practice_area": m["practice_area"],
            "billing_method": m["billing_method"],
            "matter_status": m["status"],
            "total_hours": str(round(time_row["total_hours"], 1)),
            "time_revenue": str(revenue),
            "expenses": str(expenses),
            "billed": str(billed),
            "collected": str(collected),
            "profit": str(profit),
            "margin_pct": str(margin),
            "budget": str(budget),
            "budget_used_pct": str(budget_used),
        })

    ok({"matters": results, "count": len(results)})


# ---------------------------------------------------------------------------
# 8. practice-area-analysis
# ---------------------------------------------------------------------------
def practice_area_analysis(conn, args):
    _validate_company(conn, args.company_id)

    rows = conn.execute("""
        SELECT m.practice_area,
               COUNT(*) as matter_count,
               SUM(CASE WHEN m.status = 'active' THEN 1 ELSE 0 END) as active_count,
               SUM(CASE WHEN m.status = 'closed' THEN 1 ELSE 0 END) as closed_count,
               SUM(CAST(m.billed_amount AS REAL)) as total_billed,
               SUM(CAST(m.collected_amount AS REAL)) as total_collected
        FROM legalclaw_matter m
        WHERE m.company_id = ?
        GROUP BY m.practice_area
        ORDER BY total_billed DESC
    """, (args.company_id,)).fetchall()

    areas = []
    for r in rows:
        billed = Decimal(str(r["total_billed"] or 0))
        collected = Decimal(str(r["total_collected"] or 0))

        # Get total hours for this practice area
        hours_row = conn.execute("""
            SELECT COALESCE(SUM(CAST(t.hours AS REAL)), 0) as total_hours
            FROM legalclaw_time_entry t
            JOIN legalclaw_matter m ON t.matter_id = m.id
            WHERE m.practice_area = ? AND m.company_id = ?
        """, (r["practice_area"], args.company_id)).fetchone()

        areas.append({
            "practice_area": r["practice_area"],
            "matter_count": r["matter_count"],
            "active_matters": r["active_count"],
            "closed_matters": r["closed_count"],
            "total_hours": str(round(hours_row["total_hours"], 1)),
            "total_billed": str(billed),
            "total_collected": str(collected),
            "collection_rate": str(round((collected / billed * 100) if billed > 0 else Decimal("0"), 1)),
        })

    ok({"practice_areas": areas, "count": len(areas)})


# ---------------------------------------------------------------------------
# 9. status
# ---------------------------------------------------------------------------
def legalclaw_status(conn, args):
    ok({
        "skill": "legalclaw",
        "version": "1.0.0",
        "actions_available": 69,
        "domains": ["matters", "timebilling", "trust", "documents", "calendar", "conflicts", "compliance"],
        "database": DEFAULT_DB_PATH,
    })


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------
ACTIONS = {
    "legal-add-bar-admission": add_bar_admission,
    "legal-update-bar-admission": update_bar_admission,
    "legal-list-bar-admissions": list_bar_admissions,
    "legal-add-cle-record": add_cle_record,
    "legal-list-cle-records": list_cle_records,
    "legal-cle-compliance-report": cle_compliance_report,
    "legal-matter-profitability-report": matter_profitability_report,
    "legal-practice-area-analysis": practice_area_analysis,
    "status": legalclaw_status,
}
