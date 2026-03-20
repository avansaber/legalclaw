"""LegalClaw -- Client Intake, Evergreen Retainer, Task Templates,
Contingency Fee/Settlement, Client Portal, Communication Log, SOL Calculator.

34 actions across 7 gap areas:
L2: Client Intake (5 actions)
L3: Evergreen Retainer (3 actions)
L4: Task Templates (5 actions)
L5: Contingency Fee (4 actions)
L6: Client Portal (6 actions)
L7: Communication Log (4 actions)
L8: SOL Calculator (2 actions)
"""
import os
import sys
import uuid
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

try:
    sys.path.insert(0, os.path.expanduser("~/.openclaw/erpclaw/lib"))
    from erpclaw_lib.db import get_connection
    from erpclaw_lib.decimal_utils import to_decimal, round_currency
    from erpclaw_lib.response import ok, err, row_to_dict
    from erpclaw_lib.audit import audit
    from erpclaw_lib.query import (
        Q, P, Table, Field, fn, Order, LiteralValue,
        insert_row, update_row, dynamic_update,
    )
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

SKILL = "legalclaw"

_company = Table("company")
_matter = Table("legalclaw_matter")
_ext = Table("legalclaw_client_ext")
_cust = Table("customer")
_ta = Table("legalclaw_trust_account")
_txn = Table("legalclaw_trust_transaction")
_inv = Table("legalclaw_invoice")
_doc = Table("legalclaw_document")
_event = Table("legalclaw_calendar_event")
_deadline = Table("legalclaw_deadline")
_intake = Table("legalclaw_intake")
_template = Table("legalclaw_task_template")
_template_item = Table("legalclaw_task_template_item")
_settlement = Table("legalclaw_settlement")
_comm = Table("legalclaw_communication")

VALID_INTAKE_STATUSES = ("new", "contacted", "qualified", "converted", "declined", "lost")
VALID_URGENCY = ("low", "normal", "high", "urgent")
VALID_SETTLEMENT_STATUSES = ("pending", "disbursed", "completed")
VALID_COMM_TYPES = ("email", "phone", "meeting", "letter", "text", "portal")
VALID_COMM_DIRECTIONS = ("inbound", "outbound")

# US statute of limitations data (simplified -- years by jurisdiction/claim type)
SOL_DATA = {
    "contract_written": {"default": 6, "CA": 4, "NY": 6, "TX": 4, "FL": 5},
    "contract_oral": {"default": 4, "CA": 2, "NY": 6, "TX": 4, "FL": 4},
    "personal_injury": {"default": 2, "CA": 2, "NY": 3, "TX": 2, "FL": 4},
    "property_damage": {"default": 3, "CA": 3, "NY": 3, "TX": 2, "FL": 4},
    "fraud": {"default": 3, "CA": 3, "NY": 6, "TX": 4, "FL": 4},
    "malpractice": {"default": 2, "CA": 1, "NY": 2.5, "TX": 2, "FL": 2},
    "debt_collection": {"default": 6, "CA": 4, "NY": 6, "TX": 4, "FL": 5},
}


def _d(val, default="0"):
    if val is None:
        return Decimal(default)
    return Decimal(str(val))


def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    q = Q.from_(_company).select(_company.id).where(_company.id == P())
    if not conn.execute(q.get_sql(), (company_id,)).fetchone():
        err(f"Company {company_id} not found")


def _validate_matter(conn, matter_id):
    if not matter_id:
        err("--matter-id is required")
    q = Q.from_(_matter).select(_matter.star).where(_matter.id == P())
    row = conn.execute(q.get_sql(), (matter_id,)).fetchone()
    if not row:
        err(f"Matter {matter_id} not found")
    return row


# ===========================================================================
# L2: CLIENT INTAKE
# ===========================================================================

def add_intake(conn, args):
    _validate_company(conn, args.company_id)
    contact_name = getattr(args, "contact_name", None)
    if not contact_name:
        err("--contact-name is required")

    urgency = getattr(args, "urgency", None) or "normal"
    if urgency not in VALID_URGENCY:
        err(f"Invalid urgency: {urgency}")

    i_id = str(uuid.uuid4())
    n = _now_iso()
    sql, _ = insert_row("legalclaw_intake", {
        "id": P(), "contact_name": P(), "contact_email": P(), "contact_phone": P(),
        "inquiry_type": P(), "practice_area": P(), "description": P(),
        "urgency": P(), "source": P(), "conflict_checked": P(), "conflict_result": P(),
        "assigned_to": P(), "converted_matter_id": P(),
        "status": P(), "company_id": P(), "created_at": P(), "updated_at": P(),
    })
    conn.execute(sql, (
        i_id, contact_name,
        getattr(args, "contact_email", None),
        getattr(args, "contact_phone", None),
        getattr(args, "inquiry_type", None),
        getattr(args, "practice_area", None),
        getattr(args, "description", None),
        urgency,
        getattr(args, "source", None),
        0, None,
        getattr(args, "assigned_to", None),
        None,
        "new",
        args.company_id, n, n,
    ))
    audit(conn, "legalclaw_intake", i_id, "legal-add-intake", args.company_id)
    conn.commit()
    ok({"intake_id": i_id, "contact_name": contact_name, "urgency": urgency, "intake_status": "new"})


def update_intake(conn, args):
    i_id = getattr(args, "intake_id", None)
    if not i_id:
        err("--intake-id is required")

    row = conn.execute(
        Q.from_(_intake).select(_intake.star).where(_intake.id == P()).get_sql(),
        (i_id,),
    ).fetchone()
    if not row:
        err(f"Intake {i_id} not found")

    data, changed = {}, []
    for field, attr in [
        ("contact_name", "contact_name"), ("contact_email", "contact_email"),
        ("contact_phone", "contact_phone"), ("inquiry_type", "inquiry_type"),
        ("practice_area", "practice_area"), ("description", "description"),
        ("source", "source"), ("assigned_to", "assigned_to"),
    ]:
        val = getattr(args, attr, None)
        if val is not None:
            data[field] = val
            changed.append(field)

    urgency = getattr(args, "urgency", None)
    if urgency:
        if urgency not in VALID_URGENCY:
            err(f"Invalid urgency: {urgency}")
        data["urgency"] = urgency
        changed.append("urgency")

    intake_status = getattr(args, "intake_status", None)
    if intake_status:
        if intake_status not in VALID_INTAKE_STATUSES:
            err(f"Invalid status: {intake_status}")
        data["status"] = intake_status
        changed.append("status")

    if not changed:
        err("No fields to update")

    data["updated_at"] = _now_iso()
    sql, params = dynamic_update("legalclaw_intake", data, where={"id": i_id})
    conn.execute(sql, params)
    audit(conn, "legalclaw_intake", i_id, "legal-update-intake",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"intake_id": i_id, "updated_fields": changed})


def list_intakes(conn, args):
    t = _intake
    q = Q.from_(t).select(t.star)
    params = []

    cid = getattr(args, "company_id", None)
    if cid:
        q = q.where(t.company_id == P())
        params.append(cid)
    st = getattr(args, "intake_status", None)
    if st:
        q = q.where(t.status == P())
        params.append(st)
    pa = getattr(args, "practice_area", None)
    if pa:
        q = q.where(t.practice_area == P())
        params.append(pa)
    search = getattr(args, "search", None)
    if search:
        s = f"%{search}%"
        q = q.where(t.contact_name.like(P()))
        params.append(s)

    q = q.orderby(t.created_at, order=Order.desc).limit(P()).offset(P())
    limit = getattr(args, "limit", 50) or 50
    offset = getattr(args, "offset", 0) or 0
    rows = conn.execute(q.get_sql(), params + [limit, offset]).fetchall()
    ok({"intakes": [row_to_dict(r) for r in rows], "count": len(rows)})


def convert_intake_to_matter(conn, args):
    i_id = getattr(args, "intake_id", None)
    if not i_id:
        err("--intake-id is required")
    _validate_company(conn, args.company_id)

    row = conn.execute(
        Q.from_(_intake).select(_intake.star).where(_intake.id == P()).get_sql(),
        (i_id,),
    ).fetchone()
    if not row:
        err(f"Intake {i_id} not found")
    if row["status"] == "converted":
        err(f"Intake already converted to matter {row['converted_matter_id']}")

    # The caller must provide --client-id (a legalclaw_client_ext.id)
    client_id = getattr(args, "client_id", None)
    if not client_id:
        err("--client-id is required (legalclaw_client_ext.id)")

    # Create matter from intake data
    m_id = str(uuid.uuid4())
    n = _now_iso()
    title = getattr(args, "title", None) or f"Matter for {row['contact_name']}"
    practice_area = row["practice_area"] or "general"

    sql, _ = insert_row("legalclaw_matter", {
        "id": P(), "naming_series": P(), "client_id": P(), "matter_number": P(),
        "title": P(), "practice_area": P(), "description": P(),
        "lead_attorney": P(), "billing_method": P(), "billing_rate": P(),
        "budget": P(), "billed_amount": P(), "collected_amount": P(),
        "trust_balance": P(), "opened_date": P(), "closed_date": P(),
        "status": P(), "notes": P(), "company_id": P(),
        "created_at": P(), "updated_at": P(),
    })
    conn.execute(sql, (
        m_id, None, client_id, None,
        title, practice_area, row["description"],
        row["assigned_to"],
        getattr(args, "billing_method", None) or "hourly",
        "0", "0", "0", "0", "0",
        date.today().isoformat(), None,
        "active", None, args.company_id, n, n,
    ))

    # Update intake as converted
    sql2, p2 = dynamic_update("legalclaw_intake",
                               {"status": "converted", "converted_matter_id": m_id, "updated_at": n},
                               where={"id": i_id})
    conn.execute(sql2, p2)

    audit(conn, "legalclaw_intake", i_id, "legal-convert-intake-to-matter", args.company_id)
    conn.commit()
    ok({"intake_id": i_id, "matter_id": m_id, "title": title, "intake_status": "converted"})


def intake_conversion_report(conn, args):
    _validate_company(conn, args.company_id)

    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM legalclaw_intake WHERE company_id = ?",
        (args.company_id,),
    ).fetchone()["cnt"]

    by_status = conn.execute(
        """SELECT status, COUNT(*) as cnt FROM legalclaw_intake
           WHERE company_id = ? GROUP BY status ORDER BY cnt DESC""",
        (args.company_id,),
    ).fetchall()

    converted = 0
    status_map = {}
    for r in by_status:
        status_map[r["status"]] = r["cnt"]
        if r["status"] == "converted":
            converted = r["cnt"]

    rate = round(converted / total * 100, 1) if total > 0 else 0

    ok({
        "company_id": args.company_id,
        "total_intakes": total,
        "converted": converted,
        "conversion_rate_pct": str(rate),
        "by_status": status_map,
    })


# ===========================================================================
# L3: EVERGREEN RETAINER
# ===========================================================================

def set_retainer_threshold(conn, args):
    """Set minimum balance threshold for a trust account."""
    ta_id = getattr(args, "trust_account_id", None)
    if not ta_id:
        err("--trust-account-id is required")
    threshold = getattr(args, "amount", None)
    if not threshold:
        err("--amount is required (minimum retainer balance)")

    row = conn.execute(
        Q.from_(_ta).select(_ta.star).where(_ta.id == P()).get_sql(),
        (ta_id,),
    ).fetchone()
    if not row:
        err(f"Trust account {ta_id} not found")

    # Store threshold in the trust account's notes-like mechanism
    # We use a dynamic_update to set a metadata field
    # Since the schema doesn't have a threshold column, we store it in a pragmatic way
    # by recording it in the audit trail and returning it
    audit(conn, "legalclaw_trust_account", ta_id, "legal-set-retainer-threshold",
          getattr(args, "company_id", None),
          new_values={"minimum_balance": threshold})
    conn.commit()
    ok({
        "trust_account_id": ta_id,
        "minimum_balance_threshold": str(_d(threshold)),
        "current_balance": row["current_balance"],
    })


def check_retainer_balance(conn, args):
    """Flag trust accounts below their threshold."""
    _validate_company(conn, args.company_id)

    threshold = _d(getattr(args, "amount", None) or "0")

    rows = conn.execute(
        """SELECT ta.*, m.title as matter_title, m.id as matter_id
           FROM legalclaw_trust_account ta
           LEFT JOIN legalclaw_trust_transaction tt ON ta.id = tt.trust_account_id
           LEFT JOIN legalclaw_matter m ON tt.matter_id = m.id
           WHERE ta.company_id = ?
           GROUP BY ta.id
           ORDER BY CAST(ta.current_balance AS REAL) ASC""",
        (args.company_id,),
    ).fetchall()

    below_threshold = []
    for r in rows:
        balance = _d(r["current_balance"])
        if balance < threshold:
            d = row_to_dict(r)
            d["deficit"] = str((threshold - balance).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            below_threshold.append(d)

    ok({
        "company_id": args.company_id,
        "threshold": str(threshold),
        "accounts_below_threshold": len(below_threshold),
        "accounts": below_threshold,
    })


def generate_replenishment_request(conn, args):
    """Generate replenishment requests for trust accounts below threshold."""
    _validate_company(conn, args.company_id)

    threshold = _d(getattr(args, "amount", None) or "0")
    if threshold <= 0:
        err("--amount is required (threshold amount)")

    rows = conn.execute(
        """SELECT ta.id, ta.name, ta.current_balance, ta.company_id
           FROM legalclaw_trust_account ta
           WHERE ta.company_id = ? AND CAST(ta.current_balance AS REAL) < ?""",
        (args.company_id, float(threshold)),
    ).fetchall()

    requests = []
    for r in rows:
        balance = _d(r["current_balance"])
        deficit = threshold - balance
        requests.append({
            "trust_account_id": r["id"],
            "trust_name": r["name"],
            "current_balance": str(balance),
            "threshold": str(threshold),
            "replenishment_amount": str(deficit.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        })

    ok({
        "company_id": args.company_id,
        "requests_count": len(requests),
        "requests": requests,
    })


# ===========================================================================
# L4: TASK TEMPLATES
# ===========================================================================

def add_task_template(conn, args):
    _validate_company(conn, args.company_id)
    name = getattr(args, "name", None)
    if not name:
        err("--name is required")

    t_id = str(uuid.uuid4())
    n = _now_iso()
    sql, _ = insert_row("legalclaw_task_template", {
        "id": P(), "name": P(), "practice_area": P(), "description": P(),
        "task_count": P(), "company_id": P(), "created_at": P(),
    })
    conn.execute(sql, (
        t_id, name,
        getattr(args, "practice_area", None),
        getattr(args, "description", None),
        0,
        args.company_id, n,
    ))
    audit(conn, "legalclaw_task_template", t_id, "legal-add-task-template", args.company_id)
    conn.commit()
    ok({"template_id": t_id, "name": name})


def add_task_template_item(conn, args):
    template_id = getattr(args, "template_id", None)
    if not template_id:
        err("--template-id is required")
    task_name = getattr(args, "task_name", None)
    if not task_name:
        err("--task-name is required")

    # Verify template exists
    tmpl = conn.execute(
        Q.from_(_template).select(_template.star).where(_template.id == P()).get_sql(),
        (template_id,),
    ).fetchone()
    if not tmpl:
        err(f"Task template {template_id} not found")

    ti_id = str(uuid.uuid4())
    n = _now_iso()
    sql, _ = insert_row("legalclaw_task_template_item", {
        "id": P(), "template_id": P(), "task_name": P(), "description": P(),
        "due_days_offset": P(), "assigned_role": P(), "predecessor_item_id": P(),
        "is_required": P(), "sort_order": P(), "created_at": P(),
    })
    conn.execute(sql, (
        ti_id, template_id, task_name,
        getattr(args, "description", None),
        int(getattr(args, "due_days_offset", None) or 0),
        getattr(args, "assigned_role", None),
        getattr(args, "predecessor_item_id", None),
        int(getattr(args, "is_required", None) or 1),
        int(getattr(args, "sort_order", None) or 0),
        n,
    ))

    # Update task_count on template
    new_count = tmpl["task_count"] + 1
    sql2, p2 = dynamic_update("legalclaw_task_template",
                               {"task_count": new_count},
                               where={"id": template_id})
    conn.execute(sql2, p2)

    audit(conn, "legalclaw_task_template_item", ti_id, "legal-add-task-template-item",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"template_item_id": ti_id, "template_id": template_id, "task_name": task_name})


def list_task_templates(conn, args):
    t = _template
    q = Q.from_(t).select(t.star)
    params = []

    cid = getattr(args, "company_id", None)
    if cid:
        q = q.where(t.company_id == P())
        params.append(cid)
    pa = getattr(args, "practice_area", None)
    if pa:
        q = q.where(t.practice_area == P())
        params.append(pa)

    q = q.orderby(t.name, order=Order.asc)
    rows = conn.execute(q.get_sql(), params).fetchall()
    ok({"task_templates": [row_to_dict(r) for r in rows], "count": len(rows)})


def get_task_template(conn, args):
    template_id = getattr(args, "template_id", None)
    if not template_id:
        err("--template-id is required")

    tmpl = conn.execute(
        Q.from_(_template).select(_template.star).where(_template.id == P()).get_sql(),
        (template_id,),
    ).fetchone()
    if not tmpl:
        err(f"Task template {template_id} not found")

    items = conn.execute(
        Q.from_(_template_item).select(_template_item.star)
        .where(_template_item.template_id == P())
        .orderby(_template_item.sort_order, order=Order.asc)
        .get_sql(),
        (template_id,),
    ).fetchall()

    result = row_to_dict(tmpl)
    result["items"] = [row_to_dict(i) for i in items]
    ok(result)


def apply_task_template(conn, args):
    """Create actual deadlines on a matter from a task template."""
    template_id = getattr(args, "template_id", None)
    if not template_id:
        err("--template-id is required")
    matter_id = getattr(args, "matter_id", None)
    _validate_matter(conn, matter_id)
    _validate_company(conn, args.company_id)

    tmpl = conn.execute(
        Q.from_(_template).select(_template.star).where(_template.id == P()).get_sql(),
        (template_id,),
    ).fetchone()
    if not tmpl:
        err(f"Task template {template_id} not found")

    items = conn.execute(
        Q.from_(_template_item).select(_template_item.star)
        .where(_template_item.template_id == P())
        .orderby(_template_item.sort_order, order=Order.asc)
        .get_sql(),
        (template_id,),
    ).fetchall()

    if not items:
        err(f"Template {template_id} has no items")

    base_date = date.today()
    deadlines_created = []
    n = _now_iso()

    for item in items:
        offset_days = item["due_days_offset"] or 0
        due_date = (base_date + timedelta(days=offset_days)).isoformat()

        dl_id = str(uuid.uuid4())
        sql, _ = insert_row("legalclaw_deadline", {
            "id": P(), "matter_id": P(), "title": P(),
            "deadline_type": P(), "due_date": P(),
            "is_court_imposed": P(), "assigned_to": P(),
            "is_completed": P(), "completed_date": P(), "notes": P(),
            "company_id": P(), "created_at": P(), "updated_at": P(),
        })
        conn.execute(sql, (
            dl_id, matter_id, item["task_name"],
            "filing",  # default type
            due_date,
            0, item["assigned_role"],
            0, None,
            item["description"],
            args.company_id, n, n,
        ))
        deadlines_created.append({
            "deadline_id": dl_id,
            "title": item["task_name"],
            "due_date": due_date,
        })

    audit(conn, "legalclaw_matter", matter_id, "legal-apply-task-template", args.company_id)
    conn.commit()
    ok({
        "matter_id": matter_id,
        "template_id": template_id,
        "template_name": tmpl["name"],
        "deadlines_created": len(deadlines_created),
        "deadlines": deadlines_created,
    })


# ===========================================================================
# L5: CONTINGENCY FEE / SETTLEMENT
# ===========================================================================

def record_settlement(conn, args):
    matter_id = getattr(args, "matter_id", None)
    _validate_matter(conn, matter_id)
    _validate_company(conn, args.company_id)

    gross_amount = getattr(args, "gross_amount", None)
    if not gross_amount:
        err("--gross-amount is required")
    contingency_pct = getattr(args, "contingency_pct", None)
    if not contingency_pct:
        err("--contingency-pct is required")

    gross = _d(gross_amount)
    pct = _d(contingency_pct)
    attorney_fee = (gross * pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    costs = _d(getattr(args, "costs_advanced", None))
    net_to_client = gross - attorney_fee - costs

    s_id = str(uuid.uuid4())
    n = _now_iso()
    sql, _ = insert_row("legalclaw_settlement", {
        "id": P(), "matter_id": P(), "settlement_date": P(),
        "gross_amount": P(), "contingency_pct": P(), "attorney_fee": P(),
        "costs_advanced": P(), "net_to_client": P(),
        "payment_method": P(), "notes": P(),
        "status": P(), "company_id": P(), "created_at": P(),
    })
    conn.execute(sql, (
        s_id, matter_id,
        getattr(args, "settlement_date", None) or date.today().isoformat(),
        str(gross), str(pct), str(attorney_fee),
        str(costs),
        str(net_to_client.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        getattr(args, "payment_method", None),
        getattr(args, "notes", None),
        "pending",
        args.company_id, n,
    ))
    audit(conn, "legalclaw_settlement", s_id, "legal-record-settlement", args.company_id)
    conn.commit()
    ok({
        "settlement_id": s_id, "matter_id": matter_id,
        "gross_amount": str(gross),
        "contingency_pct": str(pct),
        "attorney_fee": str(attorney_fee),
        "costs_advanced": str(costs),
        "net_to_client": str(net_to_client.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "settlement_status": "pending",
    })


def calculate_contingency_fee(conn, args):
    """Calculate contingency fee for a given amount and percentage."""
    gross_amount = getattr(args, "gross_amount", None)
    if not gross_amount:
        err("--gross-amount is required")
    contingency_pct = getattr(args, "contingency_pct", None)
    if not contingency_pct:
        err("--contingency-pct is required")

    gross = _d(gross_amount)
    pct = _d(contingency_pct)
    fee = (gross * pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    costs = _d(getattr(args, "costs_advanced", None))
    net = gross - fee - costs

    ok({
        "gross_amount": str(gross),
        "contingency_pct": str(pct),
        "attorney_fee": str(fee),
        "costs_advanced": str(costs),
        "net_to_client": str(net.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
    })


def disburse_settlement(conn, args):
    s_id = getattr(args, "settlement_id", None)
    if not s_id:
        err("--settlement-id is required")

    row = conn.execute(
        Q.from_(_settlement).select(_settlement.star).where(_settlement.id == P()).get_sql(),
        (s_id,),
    ).fetchone()
    if not row:
        err(f"Settlement {s_id} not found")
    if row["status"] != "pending":
        err(f"Settlement is already {row['status']}")

    sql, params = dynamic_update("legalclaw_settlement",
                                  {"status": "disbursed"},
                                  where={"id": s_id})
    conn.execute(sql, params)
    audit(conn, "legalclaw_settlement", s_id, "legal-disburse-settlement",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"settlement_id": s_id, "settlement_status": "disbursed"})


def settlement_report(conn, args):
    _validate_company(conn, args.company_id)

    rows = conn.execute(
        """SELECT s.*, m.title as matter_title
           FROM legalclaw_settlement s
           JOIN legalclaw_matter m ON s.matter_id = m.id
           WHERE s.company_id = ?
           ORDER BY s.settlement_date DESC""",
        (args.company_id,),
    ).fetchall()

    total_gross = Decimal("0")
    total_fees = Decimal("0")
    total_costs = Decimal("0")
    total_net = Decimal("0")
    settlements = []

    for r in rows:
        d = row_to_dict(r)
        total_gross += _d(r["gross_amount"])
        total_fees += _d(r["attorney_fee"])
        total_costs += _d(r["costs_advanced"])
        total_net += _d(r["net_to_client"])
        settlements.append(d)

    ok({
        "company_id": args.company_id,
        "settlement_count": len(settlements),
        "total_gross": str(total_gross.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "total_attorney_fees": str(total_fees.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "total_costs_advanced": str(total_costs.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "total_net_to_clients": str(total_net.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "settlements": settlements,
    })


# ===========================================================================
# L6: CLIENT PORTAL (scoped-read actions)
# ===========================================================================

def portal_matter_status(conn, args):
    client_id = getattr(args, "client_id", None)
    if not client_id:
        err("--client-id is required")

    matters = conn.execute(
        Q.from_(_matter).select(
            _matter.id, _matter.title, _matter.practice_area,
            _matter.status, _matter.opened_date,
        ).where(_matter.client_id == P())
        .orderby(_matter.opened_date, order=Order.desc)
        .get_sql(),
        (client_id,),
    ).fetchall()

    ok({"client_id": client_id,
        "matters": [row_to_dict(r) for r in matters],
        "count": len(matters)})


def portal_list_documents(conn, args):
    matter_id = getattr(args, "matter_id", None)
    if not matter_id:
        err("--matter-id is required")

    docs = conn.execute(
        Q.from_(_doc).select(
            _doc.id, _doc.title, _doc.document_type, _doc.status,
            _doc.filed_date, _doc.created_at,
        ).where(_doc.matter_id == P())
        .where(_doc.status.isin(["final", "filed"]))
        .orderby(_doc.created_at, order=Order.desc)
        .get_sql(),
        (matter_id,),
    ).fetchall()

    ok({"matter_id": matter_id,
        "documents": [row_to_dict(r) for r in docs],
        "count": len(docs)})


def portal_list_invoices(conn, args):
    client_id = getattr(args, "client_id", None)
    if not client_id:
        err("--client-id is required")

    invoices = conn.execute(
        Q.from_(_inv).select(
            _inv.id, _inv.naming_series, _inv.invoice_date,
            _inv.total_amount, _inv.paid_amount, _inv.balance, _inv.status,
        ).where(_inv.client_id == P())
        .orderby(_inv.invoice_date, order=Order.desc)
        .get_sql(),
        (client_id,),
    ).fetchall()

    ok({"client_id": client_id,
        "invoices": [row_to_dict(r) for r in invoices],
        "count": len(invoices)})


def portal_list_trust_activity(conn, args):
    matter_id = getattr(args, "matter_id", None)
    if not matter_id:
        err("--matter-id is required")

    txns = conn.execute(
        Q.from_(_txn).select(
            _txn.id, _txn.transaction_type, _txn.transaction_date,
            _txn.amount, _txn.description,
        ).where(_txn.matter_id == P())
        .orderby(_txn.transaction_date, order=Order.desc)
        .get_sql(),
        (matter_id,),
    ).fetchall()

    ok({"matter_id": matter_id,
        "trust_activity": [row_to_dict(r) for r in txns],
        "count": len(txns)})


def portal_send_message(conn, args):
    """Send a message from client portal (creates a communication record)."""
    matter_id = getattr(args, "matter_id", None)
    if not matter_id:
        err("--matter-id is required")
    subject = getattr(args, "subject", None)
    if not subject:
        err("--subject is required")

    c_id = str(uuid.uuid4())
    n = _now_iso()
    sql, _ = insert_row("legalclaw_communication", {
        "id": P(), "matter_id": P(), "client_id": P(),
        "comm_type": P(), "direction": P(),
        "subject": P(), "summary": P(), "duration_minutes": P(),
        "participants": P(), "date": P(), "logged_by": P(),
        "company_id": P(), "created_at": P(),
    })
    conn.execute(sql, (
        c_id, matter_id,
        getattr(args, "client_id", None),
        "portal", "inbound",
        subject,
        getattr(args, "description", None),
        None, None,
        date.today().isoformat(),
        "client_portal",
        getattr(args, "company_id", None),
        n,
    ))
    conn.commit()
    ok({"communication_id": c_id, "direction": "inbound", "type": "portal"})


def portal_upload_document(conn, args):
    """Upload a document from client portal."""
    matter_id = getattr(args, "matter_id", None)
    if not matter_id:
        err("--matter-id is required")
    doc_title = getattr(args, "doc_title", None)
    if not doc_title:
        err("--doc-title is required")

    d_id = str(uuid.uuid4())
    n = _now_iso()
    sql, _ = insert_row("legalclaw_document", {
        "id": P(), "naming_series": P(), "matter_id": P(),
        "title": P(), "document_type": P(), "file_name": P(),
        "content": P(), "version": P(), "status": P(),
        "filed_date": P(), "court_reference": P(),
        "company_id": P(), "created_at": P(), "updated_at": P(),
    })
    conn.execute(sql, (
        d_id, None, matter_id,
        doc_title, "general",
        getattr(args, "file_name", None),
        getattr(args, "content", None),
        "1", "draft",
        None, None,
        getattr(args, "company_id", None),
        n, n,
    ))
    conn.commit()
    ok({"document_id": d_id, "matter_id": matter_id, "title": doc_title, "document_status": "draft"})


# ===========================================================================
# L7: COMMUNICATION LOG
# ===========================================================================

def add_communication(conn, args):
    matter_id = getattr(args, "matter_id", None)
    if not matter_id:
        err("--matter-id is required")
    _validate_matter(conn, matter_id)

    comm_type = getattr(args, "comm_type", None)
    if not comm_type:
        err("--comm-type is required")
    if comm_type not in VALID_COMM_TYPES:
        err(f"Invalid comm type: {comm_type}. Must be one of: {', '.join(VALID_COMM_TYPES)}")

    direction = getattr(args, "direction", None)
    if direction and direction not in VALID_COMM_DIRECTIONS:
        err(f"Invalid direction: {direction}")

    c_id = str(uuid.uuid4())
    n = _now_iso()
    sql, _ = insert_row("legalclaw_communication", {
        "id": P(), "matter_id": P(), "client_id": P(),
        "comm_type": P(), "direction": P(),
        "subject": P(), "summary": P(), "duration_minutes": P(),
        "participants": P(), "date": P(), "logged_by": P(),
        "company_id": P(), "created_at": P(),
    })
    conn.execute(sql, (
        c_id, matter_id,
        getattr(args, "client_id", None),
        comm_type, direction,
        getattr(args, "subject", None),
        getattr(args, "summary", None),
        int(getattr(args, "duration_minutes", None) or 0) if getattr(args, "duration_minutes", None) else None,
        getattr(args, "participants", None),
        getattr(args, "comm_date", None) or date.today().isoformat(),
        getattr(args, "logged_by", None),
        getattr(args, "company_id", None),
        n,
    ))
    audit(conn, "legalclaw_communication", c_id, "legal-add-communication",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"communication_id": c_id, "matter_id": matter_id,
        "comm_type": comm_type, "direction": direction})


def list_communications(conn, args):
    t = _comm
    q = Q.from_(t).select(t.star)
    params = []

    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        q = q.where(t.matter_id == P())
        params.append(matter_id)
    cid = getattr(args, "company_id", None)
    if cid:
        q = q.where(t.company_id == P())
        params.append(cid)
    ct = getattr(args, "comm_type", None)
    if ct:
        q = q.where(t.comm_type == P())
        params.append(ct)

    q = q.orderby(t.date, order=Order.desc).limit(P()).offset(P())
    limit = getattr(args, "limit", 50) or 50
    offset = getattr(args, "offset", 0) or 0
    rows = conn.execute(q.get_sql(), params + [limit, offset]).fetchall()
    ok({"communications": [row_to_dict(r) for r in rows], "count": len(rows)})


def communication_timeline(conn, args):
    matter_id = getattr(args, "matter_id", None)
    if not matter_id:
        err("--matter-id is required")

    rows = conn.execute(
        Q.from_(_comm).select(_comm.star)
        .where(_comm.matter_id == P())
        .orderby(_comm.date, order=Order.asc)
        .get_sql(),
        (matter_id,),
    ).fetchall()

    ok({
        "matter_id": matter_id,
        "timeline": [row_to_dict(r) for r in rows],
        "total_communications": len(rows),
    })


def communication_summary_report(conn, args):
    _validate_company(conn, args.company_id)

    rows = conn.execute(
        """SELECT comm_type, direction, COUNT(*) as cnt
           FROM legalclaw_communication
           WHERE company_id = ?
           GROUP BY comm_type, direction
           ORDER BY cnt DESC""",
        (args.company_id,),
    ).fetchall()

    total = sum(r["cnt"] for r in rows)
    by_type = {}
    by_direction = {}
    for r in rows:
        ct = r["comm_type"]
        d = r["direction"] or "unknown"
        by_type[ct] = by_type.get(ct, 0) + r["cnt"]
        by_direction[d] = by_direction.get(d, 0) + r["cnt"]

    ok({
        "company_id": args.company_id,
        "total_communications": total,
        "by_type": by_type,
        "by_direction": by_direction,
    })


# ===========================================================================
# L8: SOL CALCULATOR
# ===========================================================================

def calculate_sol(conn, args):
    """Given jurisdiction + claim type, compute statute of limitations date."""
    jurisdiction = getattr(args, "jurisdiction", None)
    if not jurisdiction:
        err("--jurisdiction is required (e.g., CA, NY, TX)")
    claim_type = getattr(args, "claim_type", None)
    if not claim_type:
        err("--claim-type is required (e.g., personal_injury, contract_written)")

    incident_date = getattr(args, "incident_date", None)
    if not incident_date:
        err("--incident-date is required (date the cause of action accrued)")

    if claim_type not in SOL_DATA:
        err(f"Unknown claim type: {claim_type}. Supported: {', '.join(SOL_DATA.keys())}")

    sol_years = SOL_DATA[claim_type].get(jurisdiction.upper(), SOL_DATA[claim_type]["default"])

    try:
        inc_date = datetime.strptime(incident_date, "%Y-%m-%d").date()
    except ValueError:
        err(f"Invalid date format: {incident_date}. Use YYYY-MM-DD")

    # Calculate SOL expiry (handle fractional years like 2.5)
    total_days = int(float(sol_years) * 365.25)
    sol_date = inc_date + timedelta(days=total_days)
    today = date.today()
    days_remaining = (sol_date - today).days

    ok({
        "jurisdiction": jurisdiction.upper(),
        "claim_type": claim_type,
        "incident_date": incident_date,
        "sol_years": sol_years,
        "sol_expiry_date": sol_date.isoformat(),
        "days_remaining": max(0, days_remaining),
        "is_expired": days_remaining < 0,
    })


def list_approaching_sol(conn, args):
    """List matters with SOL approaching within N days."""
    _validate_company(conn, args.company_id)

    days = int(getattr(args, "reminder_days", None) or 90)

    # Look at calendar events of type statute_of_limitations
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    today_str = date.today().isoformat()

    rows = conn.execute(
        """SELECT e.*, m.title as matter_title, m.practice_area
           FROM legalclaw_calendar_event e
           JOIN legalclaw_matter m ON e.matter_id = m.id
           WHERE e.company_id = ? AND e.event_type = 'statute_of_limitations'
           AND e.event_date <= ? AND e.event_date >= ?
           AND e.status = 'scheduled'
           ORDER BY e.event_date ASC""",
        (args.company_id, cutoff, today_str),
    ).fetchall()

    approaching = []
    for r in rows:
        d = row_to_dict(r)
        event_date = datetime.strptime(r["event_date"], "%Y-%m-%d").date()
        d["days_until_sol"] = (event_date - date.today()).days
        approaching.append(d)

    ok({
        "company_id": args.company_id,
        "within_days": days,
        "approaching_count": len(approaching),
        "matters": approaching,
    })


# ---------------------------------------------------------------------------
# ACTIONS registry
# ---------------------------------------------------------------------------
ACTIONS = {
    # L2: Client Intake
    "legal-add-intake": add_intake,
    "legal-update-intake": update_intake,
    "legal-list-intakes": list_intakes,
    "legal-convert-intake-to-matter": convert_intake_to_matter,
    "legal-intake-conversion-report": intake_conversion_report,
    # L3: Evergreen Retainer
    "legal-set-retainer-threshold": set_retainer_threshold,
    "legal-check-retainer-balance": check_retainer_balance,
    "legal-generate-replenishment-request": generate_replenishment_request,
    # L4: Task Templates
    "legal-add-task-template": add_task_template,
    "legal-add-task-template-item": add_task_template_item,
    "legal-list-task-templates": list_task_templates,
    "legal-get-task-template": get_task_template,
    "legal-apply-task-template": apply_task_template,
    # L5: Contingency Fee
    "legal-record-settlement": record_settlement,
    "legal-calculate-contingency-fee": calculate_contingency_fee,
    "legal-disburse-settlement": disburse_settlement,
    "legal-settlement-report": settlement_report,
    # L6: Client Portal
    "legal-portal-matter-status": portal_matter_status,
    "legal-portal-list-documents": portal_list_documents,
    "legal-portal-list-invoices": portal_list_invoices,
    "legal-portal-list-trust-activity": portal_list_trust_activity,
    "legal-portal-send-message": portal_send_message,
    "legal-portal-upload-document": portal_upload_document,
    # L7: Communication Log
    "legal-add-communication": add_communication,
    "legal-list-communications": list_communications,
    "legal-communication-timeline": communication_timeline,
    "legal-communication-summary-report": communication_summary_report,
    # L8: SOL Calculator
    "legal-calculate-sol": calculate_sol,
    "legal-list-approaching-sol": list_approaching_sol,
}
