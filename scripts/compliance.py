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
    from erpclaw_lib.query import (
        Q, P, Table, Field, fn, Order, LiteralValue,
        insert_row, update_row, dynamic_update,
    )

    ENTITY_PREFIXES.setdefault("legalclaw_bar_admission", "LBAR-")
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_BAR_STATUSES = ("active", "inactive", "suspended", "retired")
VALID_CLE_CATEGORIES = ("general", "ethics", "professionalism", "diversity", "substance_abuse", "other")
DEFAULT_DB_PATH = os.path.expanduser("~/.openclaw/erpclaw/data.sqlite")

# ── Table aliases ──
_company = Table("company")
_ba = Table("legalclaw_bar_admission")
_cle = Table("legalclaw_cle_record")
_matter = Table("legalclaw_matter")
_te = Table("legalclaw_time_entry")
_expense = Table("legalclaw_expense")
_ext = Table("legalclaw_client_ext")
_cust = Table("customer")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    q = Q.from_(_company).select(_company.id).where(_company.id == P())
    if not conn.execute(q.get_sql(), (company_id,)).fetchone():
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

    sql, _ = insert_row("legalclaw_bar_admission", {"id": P(), "attorney_name": P(), "bar_number": P(), "jurisdiction": P(), "admission_date": P(), "expiry_date": P(), "status": P(), "cle_hours_required": P(), "cle_hours_completed": P(), "company_id": P(), "created_at": P(), "updated_at": P()})
    conn.execute(sql, (
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
    q = Q.from_(_ba).select(_ba.star).where(_ba.id == P())
    row = conn.execute(q.get_sql(), (ba_id,)).fetchone()
    if not row:
        err(f"Bar admission {ba_id} not found")

    data = {}
    changed = []
    for arg_name, col_name in {
        "attorney_name": "attorney_name", "bar_number": "bar_number",
        "jurisdiction": "jurisdiction", "admission_date": "admission_date",
        "expiry_date": "expiry_date", "cle_hours_required": "cle_hours_required",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            if arg_name == "cle_hours_required":
                to_decimal(val)
            data[col_name] = val
            changed.append(col_name)

    admission_status = getattr(args, "admission_status", None)
    if admission_status:
        _validate_enum(admission_status, VALID_BAR_STATUSES, "admission-status")
        data["status"] = admission_status
        changed.append("status")

    if not data:
        err("No fields to update")

    data["updated_at"] = _now_iso()
    sql, params = dynamic_update("legalclaw_bar_admission", data, where={"id": ba_id})
    conn.execute(sql, params)
    audit(conn, "legalclaw_bar_admission", ba_id, "legal-update-bar-admission",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": ba_id, "updated_fields": changed})


# ---------------------------------------------------------------------------
# 3. list-bar-admissions
# ---------------------------------------------------------------------------
def list_bar_admissions(conn, args):
    conditions = []
    params = []
    if args.company_id:
        conditions.append(_ba.company_id == P())
        params.append(args.company_id)
    attorney_name = getattr(args, "attorney_name", None)
    if attorney_name:
        conditions.append(_ba.attorney_name.like(P()))
        params.append(f"%{attorney_name}%")
    admission_status = getattr(args, "admission_status", None)
    if admission_status:
        conditions.append(_ba.status == P())
        params.append(admission_status)
    jurisdiction = getattr(args, "jurisdiction", None)
    if jurisdiction:
        conditions.append(_ba.jurisdiction == P())
        params.append(jurisdiction)

    q = Q.from_(_ba).select(_ba.star)
    for cond in conditions:
        q = q.where(cond)
    q = q.orderby(_ba.attorney_name, order=Order.asc).limit(P()).offset(P())

    rows = conn.execute(q.get_sql(), params + [args.limit, args.offset]).fetchall()
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
        q = Q.from_(_ba).select(_ba.star).where(_ba.id == P())
        ba_row = conn.execute(q.get_sql(), (bar_admission_id,)).fetchone()
        if not ba_row:
            err(f"Bar admission {bar_admission_id} not found")

    cle_id = str(uuid.uuid4())
    now = _now_iso()
    sql, _ = insert_row("legalclaw_cle_record", {"id": P(), "attorney_name": P(), "bar_admission_id": P(), "course_name": P(), "provider": P(), "completion_date": P(), "hours": P(), "category": P(), "certificate_number": P(), "company_id": P(), "created_at": P()})
    conn.execute(sql, (
        cle_id, attorney_name, bar_admission_id, course_name,
        getattr(args, "cle_provider", None),
        completion_date, hours, category,
        getattr(args, "certificate_number", None),
        args.company_id, now,
    ))

    # Auto-update bar admission CLE hours if linked
    if bar_admission_id:
        upd_q = (
            Q.update(_ba)
            .set(_ba.cle_hours_completed,
                 LiteralValue("CAST(CAST(cle_hours_completed AS REAL) + ? AS TEXT)"))
            .set(_ba.updated_at, P())
            .where(_ba.id == P())
        )
        conn.execute(upd_q.get_sql(), (float(to_decimal(hours)), now, bar_admission_id))

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
    conditions = []
    params = []
    if args.company_id:
        conditions.append(_cle.company_id == P())
        params.append(args.company_id)
    attorney_name = getattr(args, "attorney_name", None)
    if attorney_name:
        conditions.append(_cle.attorney_name.like(P()))
        params.append(f"%{attorney_name}%")
    bar_admission_id = getattr(args, "bar_admission_id", None)
    if bar_admission_id:
        conditions.append(_cle.bar_admission_id == P())
        params.append(bar_admission_id)
    cle_category = getattr(args, "cle_category", None)
    if cle_category:
        conditions.append(_cle.category == P())
        params.append(cle_category)

    q = Q.from_(_cle).select(_cle.star)
    for cond in conditions:
        q = q.where(cond)
    q = q.orderby(_cle.completion_date, order=Order.desc).limit(P()).offset(P())

    rows = conn.execute(q.get_sql(), params + [args.limit, args.offset]).fetchall()
    ok({"cle_records": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 6. cle-compliance-report
# ---------------------------------------------------------------------------
def cle_compliance_report(conn, args):
    _validate_company(conn, args.company_id)

    adm_q = (
        Q.from_(_ba)
        .select(
            _ba.id, _ba.attorney_name, _ba.jurisdiction, _ba.bar_number,
            _ba.status, _ba.cle_hours_required, _ba.cle_hours_completed, _ba.expiry_date,
        )
        .where(_ba.company_id == P())
        .where(_ba.status == "active")
        .orderby(_ba.attorney_name)
    )
    admissions = conn.execute(adm_q.get_sql(), (args.company_id,)).fetchall()

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
        cat_q = (
            Q.from_(_cle)
            .select(_cle.category, LiteralValue("SUM(CAST(hours AS REAL))").as_("total_hours"))
            .where(_cle.bar_admission_id == P())
            .where(_cle.company_id == P())
            .groupby(_cle.category)
        )
        categories = conn.execute(cat_q.get_sql(), (a["id"], args.company_id)).fetchall()

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

    m_q = (
        Q.from_(_matter)
        .join(_ext).on(_matter.client_id == _ext.id)
        .join(_cust).on(_ext.customer_id == _cust.id)
        .select(
            _matter.id, _matter.title, _matter.practice_area, _matter.billing_method,
            _matter.status, _matter.billed_amount, _matter.collected_amount, _matter.budget,
            _cust.name.as_("client_name"),
        )
        .where(_matter.company_id == P())
        .orderby(_matter.opened_date, order=Order.desc)
    )
    matters = conn.execute(m_q.get_sql(), (args.company_id,)).fetchall()

    results = []
    for m in matters:
        # Total time cost (hours * rate for all entries)
        time_q = (
            Q.from_(_te)
            .select(
                LiteralValue("COALESCE(SUM(CAST(amount AS NUMERIC)), 0)").as_("revenue"),
                LiteralValue("COALESCE(SUM(CAST(hours AS REAL)), 0)").as_("total_hours"),
            )
            .where(_te.matter_id == P())
        )
        time_row = conn.execute(time_q.get_sql(), (m["id"],)).fetchone()

        exp_q = (
            Q.from_(_expense)
            .select(LiteralValue("COALESCE(SUM(CAST(amount AS NUMERIC)), 0)").as_("total_expenses"))
            .where(_expense.matter_id == P())
        )
        expense_row = conn.execute(exp_q.get_sql(), (m["id"],)).fetchone()

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

    pa_q = (
        Q.from_(_matter)
        .select(
            _matter.practice_area,
            fn.Count("*").as_("matter_count"),
            LiteralValue("SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END)").as_("active_count"),
            LiteralValue("SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END)").as_("closed_count"),
            LiteralValue("SUM(CAST(billed_amount AS NUMERIC))").as_("total_billed"),
            LiteralValue("SUM(CAST(collected_amount AS NUMERIC))").as_("total_collected"),
        )
        .where(_matter.company_id == P())
        .groupby(_matter.practice_area)
        .orderby(LiteralValue("total_billed"), order=Order.desc)
    )
    rows = conn.execute(pa_q.get_sql(), (args.company_id,)).fetchall()

    areas = []
    for r in rows:
        billed = Decimal(str(r["total_billed"] or 0))
        collected = Decimal(str(r["total_collected"] or 0))

        # Get total hours for this practice area
        hours_q = (
            Q.from_(_te)
            .join(_matter).on(_te.matter_id == _matter.id)
            .select(LiteralValue("COALESCE(SUM(CAST(\"legalclaw_time_entry\".\"hours\" AS REAL)), 0)").as_("total_hours"))
            .where(_matter.practice_area == P())
            .where(_matter.company_id == P())
        )
        hours_row = conn.execute(hours_q.get_sql(), (r["practice_area"], args.company_id)).fetchone()

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
