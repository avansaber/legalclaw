"""LegalClaw -- matters domain module

Actions for matter management (3 tables, 14 actions).
Imported by db_query.py (unified router).

Client records use legalclaw_client_ext (extension table) which FKs to core customer(id).
Core customer fields (name, email, phone, address, tax_id) live in the customer table;
domain-specific fields (client_type, billing_rate) live in the ext table.
"""
import json
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
    from erpclaw_lib.cross_skill import create_customer, CrossSkillError
    from erpclaw_lib.query import (
        Q, P, Table, Field, fn, Order, LiteralValue,
        insert_row, update_row, dynamic_update,
    )

    ENTITY_PREFIXES.setdefault("legalclaw_client_ext", "LCLI-")
    ENTITY_PREFIXES.setdefault("legalclaw_matter", "LMTR-")
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------
VALID_CLIENT_TYPES = ("individual", "business", "government", "nonprofit")
VALID_PRACTICE_AREAS = (
    "general", "corporate", "litigation", "real_estate", "family", "criminal",
    "ip", "employment", "tax", "estate", "bankruptcy", "immigration", "other",
)
VALID_BILLING_METHODS = ("hourly", "flat_fee", "contingency", "retainer", "pro_bono")
VALID_MATTER_STATUSES = ("active", "pending", "on_hold", "closed", "archived")
VALID_PARTY_TYPES = (
    "plaintiff", "defendant", "witness", "expert", "opposing_counsel",
    "judge", "mediator", "party", "other",
)

# ── Table aliases ──
_company = Table("company")
_ext = Table("legalclaw_client_ext")
_cust = Table("customer")
_matter = Table("legalclaw_matter")
_party = Table("legalclaw_matter_party")
_te = Table("legalclaw_time_entry")
_expense = Table("legalclaw_expense")
_trust_txn = Table("legalclaw_trust_transaction")
_deadline = Table("legalclaw_deadline")
_inv = Table("legalclaw_invoice")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    q = Q.from_(_company).select(_company.id).where(_company.id == P())
    if not conn.execute(q.get_sql(), (company_id,)).fetchone():
        err(f"Company {company_id} not found")


def _validate_client(conn, client_id):
    if not client_id:
        err("--client-id is required")
    q = Q.from_(_ext).select(_ext.id).where(_ext.id == P())
    if not conn.execute(q.get_sql(), (client_id,)).fetchone():
        err(f"Client {client_id} not found")


def _validate_matter(conn, matter_id):
    if not matter_id:
        err("--matter-id is required")
    q = Q.from_(_matter).select(_matter.id).where(_matter.id == P())
    row = conn.execute(q.get_sql(), (matter_id,)).fetchone()
    if not row:
        err(f"Matter {matter_id} not found")
    return row


def _validate_enum(value, valid_values, field_name):
    if value and value not in valid_values:
        err(f"Invalid {field_name}: {value}. Must be one of: {', '.join(valid_values)}")


# ---------------------------------------------------------------------------
# 1. add-client
# ---------------------------------------------------------------------------
def add_client(conn, args):
    _validate_company(conn, args.company_id)
    name = getattr(args, "name", None)
    if not name:
        err("--name is required")
    client_type = getattr(args, "client_type", None) or "individual"
    _validate_enum(client_type, VALID_CLIENT_TYPES, "client-type")

    billing_rate = getattr(args, "billing_rate", None)
    if billing_rate:
        to_decimal(billing_rate)  # validate

    # Map legal client_type to core customer_type
    core_customer_type = "individual" if client_type == "individual" else "company"

    # Create core customer via cross_skill (respects table ownership)
    db_path = getattr(args, "db_path", None)
    try:
        cust_result = create_customer(
            customer_name=name,
            company_id=args.company_id,
            customer_type=core_customer_type,
            email=getattr(args, "email", None),
            phone=getattr(args, "phone", None),
            db_path=db_path,
        )
        customer_id = cust_result.get("customer_id", "")
        if not customer_id:
            err("Failed to create core customer record: no customer_id returned")
    except CrossSkillError as e:
        err(f"Failed to create core customer record: {e}")

    # Insert extension record
    ext_id = str(uuid.uuid4())
    ns = get_next_name(conn, "legalclaw_client_ext", company_id=args.company_id)
    now = _now_iso()
    sql, _ = insert_row("legalclaw_client_ext", {"id": P(), "naming_series": P(), "customer_id": P(), "client_type": P(), "billing_rate": P(), "is_active": P(), "company_id": P(), "created_at": P(), "updated_at": P()})
    conn.execute(sql, (
        ext_id, ns, customer_id, client_type,
        billing_rate, 1, args.company_id, now, now,
    ))
    audit(conn, "legalclaw_client_ext", ext_id, "legal-add-client", args.company_id)
    conn.commit()
    ok({"id": ext_id, "naming_series": ns, "customer_id": customer_id,
        "name": name, "client_type": client_type})


# ---------------------------------------------------------------------------
# 2. update-client
# ---------------------------------------------------------------------------
def update_client(conn, args):
    client_id = getattr(args, "client_id", None)
    if not client_id:
        err("--client-id is required")
    q = Q.from_(_ext).select(_ext.id, _ext.customer_id).where(_ext.id == P())
    row = conn.execute(q.get_sql(), (client_id,)).fetchone()
    if not row:
        err(f"Client {client_id} not found")

    customer_id = row["customer_id"]

    # --- Update core customer fields via cross_skill ---
    core_updates = {}
    for arg_name, flag_name in {
        "name": "--name",
        "email": "--email",
        "phone": "--phone",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            core_updates[flag_name] = val

    changed = []
    if core_updates:
        from erpclaw_lib.cross_skill import call_skill_action
        core_args = {"--customer-id": customer_id}
        core_args.update(core_updates)
        db_path = getattr(args, "db_path", None)
        try:
            call_skill_action("erpclaw", "update-customer",
                              args=core_args, db_path=db_path)
        except CrossSkillError as e:
            err(f"Failed to update core customer: {e}")
        changed.extend([k.lstrip("-").replace("-", "_") for k in core_updates.keys()])

    # --- Update extension table fields ---
    ext_data = {}
    client_type = getattr(args, "client_type", None)
    if client_type is not None:
        _validate_enum(client_type, VALID_CLIENT_TYPES, "client-type")
        ext_data["client_type"] = client_type
        changed.append("client_type")

    billing_rate = getattr(args, "billing_rate", None)
    if billing_rate is not None:
        to_decimal(billing_rate)
        ext_data["billing_rate"] = billing_rate
        changed.append("billing_rate")

    is_active = getattr(args, "is_active", None)
    if is_active is not None:
        ext_data["is_active"] = int(is_active)
        changed.append("is_active")

    if not changed:
        err("No fields to update")

    if ext_data:
        ext_data["updated_at"] = _now_iso()
        sql, params = dynamic_update("legalclaw_client_ext", ext_data, where={"id": client_id})
        conn.execute(sql, params)

    audit(conn, "legalclaw_client_ext", client_id, "legal-update-client",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": client_id, "customer_id": customer_id, "updated_fields": changed})


# ---------------------------------------------------------------------------
# 3. get-client
# ---------------------------------------------------------------------------
def get_client(conn, args):
    client_id = getattr(args, "client_id", None)
    if not client_id:
        err("--client-id is required")
    q = (
        Q.from_(_ext)
        .join(_cust).on(_ext.customer_id == _cust.id)
        .select(
            _ext.star, _cust.name,
            _cust.customer_type.as_("core_customer_type"),
            _cust.primary_address.as_("address"),
            _cust.primary_contact.as_("phone"),
            _cust.tax_id,
        )
        .where(_ext.id == P())
    )
    row = conn.execute(q.get_sql(), (client_id,)).fetchone()
    if not row:
        err(f"Client {client_id} not found")
    ok(row_to_dict(row))


# ---------------------------------------------------------------------------
# 4. list-clients
# ---------------------------------------------------------------------------
def list_clients(conn, args):
    base = Q.from_(_ext).join(_cust).on(_ext.customer_id == _cust.id)
    conditions = []
    params = []

    if args.company_id:
        conditions.append(_ext.company_id == P())
        params.append(args.company_id)
    search = getattr(args, "search", None)
    if search:
        conditions.append(_cust.name.like(P()) | _cust.primary_contact.like(P()))
        params.extend([f"%{search}%", f"%{search}%"])
    is_active = getattr(args, "is_active", None)
    if is_active is not None:
        conditions.append(_ext.is_active == P())
        params.append(int(is_active))

    q = base.select(
        _ext.star, _cust.name,
        _cust.primary_address.as_("address"),
        _cust.primary_contact.as_("phone"),
        _cust.tax_id,
    )
    for cond in conditions:
        q = q.where(cond)
    q = q.orderby(_cust.name, order=Order.asc).limit(P()).offset(P())

    rows = conn.execute(q.get_sql(), params + [args.limit, args.offset]).fetchall()
    ok({"clients": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 5. add-matter
# ---------------------------------------------------------------------------
def add_matter(conn, args):
    _validate_company(conn, args.company_id)
    client_id = getattr(args, "client_id", None)
    _validate_client(conn, client_id)

    title = getattr(args, "title", None)
    if not title:
        err("--title is required")

    practice_area = getattr(args, "practice_area", None) or "general"
    _validate_enum(practice_area, VALID_PRACTICE_AREAS, "practice-area")
    billing_method = getattr(args, "billing_method", None) or "hourly"
    _validate_enum(billing_method, VALID_BILLING_METHODS, "billing-method")

    billing_rate = getattr(args, "billing_rate", None) or "0"
    to_decimal(billing_rate)
    budget = getattr(args, "budget", None) or "0"
    to_decimal(budget)

    matter_id = str(uuid.uuid4())
    ns = get_next_name(conn, "legalclaw_matter", company_id=args.company_id)
    now = _now_iso()
    opened_date = getattr(args, "opened_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    sql, _ = insert_row("legalclaw_matter", {"id": P(), "naming_series": P(), "client_id": P(), "matter_number": P(), "title": P(), "practice_area": P(), "description": P(), "lead_attorney": P(), "billing_method": P(), "billing_rate": P(), "budget": P(), "billed_amount": P(), "collected_amount": P(), "trust_balance": P(), "opened_date": P(), "status": P(), "notes": P(), "company_id": P(), "created_at": P(), "updated_at": P()})
    conn.execute(sql, (
        matter_id, ns, client_id, ns, title, practice_area,
        getattr(args, "description", None),
        getattr(args, "lead_attorney", None),
        billing_method, billing_rate, budget,
        "0", "0", "0", opened_date,
        "active",
        getattr(args, "notes", None),
        args.company_id, now, now,
    ))
    audit(conn, "legalclaw_matter", matter_id, "legal-add-matter", args.company_id)
    conn.commit()
    ok({
        "id": matter_id, "naming_series": ns, "matter_number": ns,
        "title": title, "practice_area": practice_area,
        "billing_method": billing_method, "matter_status": "active",
    })


# ---------------------------------------------------------------------------
# 6. update-matter
# ---------------------------------------------------------------------------
def update_matter(conn, args):
    matter_id = getattr(args, "matter_id", None)
    _validate_matter(conn, matter_id)

    data = {}
    changed = []
    for arg_name, col_name in {
        "title": "title", "practice_area": "practice_area",
        "description": "description", "lead_attorney": "lead_attorney",
        "billing_method": "billing_method", "billing_rate": "billing_rate",
        "budget": "budget", "notes": "notes",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            if arg_name == "practice_area":
                _validate_enum(val, VALID_PRACTICE_AREAS, "practice-area")
            if arg_name == "billing_method":
                _validate_enum(val, VALID_BILLING_METHODS, "billing-method")
            if arg_name in ("billing_rate", "budget"):
                to_decimal(val)
            data[col_name] = val
            changed.append(col_name)

    matter_status = getattr(args, "matter_status", None)
    if matter_status:
        _validate_enum(matter_status, VALID_MATTER_STATUSES, "matter-status")
        data["status"] = matter_status
        changed.append("status")

    if not data:
        err("No fields to update")

    data["updated_at"] = _now_iso()
    sql, params = dynamic_update("legalclaw_matter", data, where={"id": matter_id})
    conn.execute(sql, params)
    audit(conn, "legalclaw_matter", matter_id, "legal-update-matter",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": matter_id, "updated_fields": changed})


# ---------------------------------------------------------------------------
# 7. get-matter
# ---------------------------------------------------------------------------
def get_matter(conn, args):
    matter_id = getattr(args, "matter_id", None)
    _validate_matter(conn, matter_id)
    q = Q.from_(_matter).select(_matter.star).where(_matter.id == P())
    row = conn.execute(q.get_sql(), (matter_id,)).fetchone()
    ok(row_to_dict(row))


# ---------------------------------------------------------------------------
# 8. list-matters
# ---------------------------------------------------------------------------
def list_matters(conn, args):
    conditions = []
    params = []
    if args.company_id:
        conditions.append(_matter.company_id == P())
        params.append(args.company_id)
    client_id = getattr(args, "client_id", None)
    if client_id:
        conditions.append(_matter.client_id == P())
        params.append(client_id)
    matter_status = getattr(args, "matter_status", None)
    if matter_status:
        conditions.append(_matter.status == P())
        params.append(matter_status)
    practice_area = getattr(args, "practice_area", None)
    if practice_area:
        conditions.append(_matter.practice_area == P())
        params.append(practice_area)
    search = getattr(args, "search", None)
    if search:
        conditions.append(_matter.title.like(P()) | _matter.matter_number.like(P()))
        params.extend([f"%{search}%", f"%{search}%"])

    q = Q.from_(_matter).select(_matter.star)
    for cond in conditions:
        q = q.where(cond)
    q = q.orderby(_matter.opened_date, order=Order.desc).limit(P()).offset(P())

    rows = conn.execute(q.get_sql(), params + [args.limit, args.offset]).fetchall()
    ok({"matters": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 9. add-matter-party
# ---------------------------------------------------------------------------
def add_matter_party(conn, args):
    matter_id = getattr(args, "matter_id", None)
    _validate_matter(conn, matter_id)
    _validate_company(conn, args.company_id)

    party_name = getattr(args, "party_name", None)
    if not party_name:
        err("--party-name is required")

    party_type = getattr(args, "party_type", None) or "party"
    _validate_enum(party_type, VALID_PARTY_TYPES, "party-type")

    party_id = str(uuid.uuid4())
    sql, _ = insert_row("legalclaw_matter_party", {"id": P(), "matter_id": P(), "party_name": P(), "party_type": P(), "role": P(), "contact_info": P(), "notes": P(), "company_id": P(), "created_at": P()})
    conn.execute(sql, (
        party_id, matter_id, party_name, party_type,
        getattr(args, "role", None),
        getattr(args, "contact_info", None),
        getattr(args, "notes", None),
        args.company_id, _now_iso(),
    ))
    audit(conn, "legalclaw_matter_party", party_id, "legal-add-matter-party", args.company_id)
    conn.commit()
    ok({"id": party_id, "matter_id": matter_id, "party_name": party_name, "party_type": party_type})


# ---------------------------------------------------------------------------
# 10. list-matter-parties
# ---------------------------------------------------------------------------
def list_matter_parties(conn, args):
    matter_id = getattr(args, "matter_id", None)
    conditions = []
    params = []
    if matter_id:
        conditions.append(_party.matter_id == P())
        params.append(matter_id)
    party_type = getattr(args, "party_type", None)
    if party_type:
        conditions.append(_party.party_type == P())
        params.append(party_type)

    q = Q.from_(_party).select(_party.star)
    for cond in conditions:
        q = q.where(cond)
    q = q.orderby(_party.party_name, order=Order.asc).limit(P()).offset(P())

    rows = conn.execute(q.get_sql(), params + [args.limit, args.offset]).fetchall()
    ok({"parties": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 11. close-matter
# ---------------------------------------------------------------------------
def close_matter(conn, args):
    matter_id = getattr(args, "matter_id", None)
    _validate_matter(conn, matter_id)

    q = Q.from_(_matter).select(_matter.status).where(_matter.id == P())
    row = conn.execute(q.get_sql(), (matter_id,)).fetchone()
    current_status = row["status"] if row else None
    if current_status == "closed":
        err(f"Matter {matter_id} is already closed")

    now = _now_iso()
    closed_date = getattr(args, "closed_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sql, params = dynamic_update("legalclaw_matter",
        {"status": "closed", "closed_date": closed_date, "updated_at": now},
        where={"id": matter_id})
    conn.execute(sql, params)
    audit(conn, "legalclaw_matter", matter_id, "legal-close-matter",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": matter_id, "matter_status": "closed", "closed_date": closed_date})


# ---------------------------------------------------------------------------
# 12. reopen-matter
# ---------------------------------------------------------------------------
def reopen_matter(conn, args):
    matter_id = getattr(args, "matter_id", None)
    _validate_matter(conn, matter_id)

    q = Q.from_(_matter).select(_matter.status).where(_matter.id == P())
    row = conn.execute(q.get_sql(), (matter_id,)).fetchone()
    current_status = row["status"] if row else None
    if current_status != "closed":
        err(f"Matter {matter_id} is not closed (current status: {current_status})")

    now = _now_iso()
    t = Table("legalclaw_matter")
    upd = (
        Q.update(t)
        .set(t.status, "active")
        .set(t.closed_date, None)
        .set(t.updated_at, P())
        .where(t.id == P())
    )
    conn.execute(upd.get_sql(), (now, matter_id))
    audit(conn, "legalclaw_matter", matter_id, "legal-reopen-matter",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": matter_id, "matter_status": "active"})


# ---------------------------------------------------------------------------
# 13. matter-summary
# ---------------------------------------------------------------------------
def matter_summary(conn, args):
    matter_id = getattr(args, "matter_id", None)
    _validate_matter(conn, matter_id)

    q = Q.from_(_matter).select(_matter.star).where(_matter.id == P())
    matter = conn.execute(q.get_sql(), (matter_id,)).fetchone()
    m = row_to_dict(matter)

    # Time entries summary
    time_q = (
        Q.from_(_te)
        .select(
            fn.Count("*").as_("count"),
            LiteralValue("COALESCE(SUM(CAST(hours AS REAL)), 0)").as_("total_hours"),
            LiteralValue("COALESCE(SUM(CAST(amount AS REAL)), 0)").as_("total_amount"),
        )
        .where(_te.matter_id == P())
    )
    time_rows = conn.execute(time_q.get_sql(), (matter_id,)).fetchone()

    unbilled_q = (
        Q.from_(_te)
        .select(
            fn.Count("*").as_("count"),
            LiteralValue("COALESCE(SUM(CAST(amount AS REAL)), 0)").as_("total_amount"),
        )
        .where(_te.matter_id == P())
        .where(_te.is_billed == 0)
        .where(_te.is_billable == 1)
    )
    unbilled_time = conn.execute(unbilled_q.get_sql(), (matter_id,)).fetchone()

    # Expenses summary
    exp_q = (
        Q.from_(_expense)
        .select(
            fn.Count("*").as_("count"),
            LiteralValue("COALESCE(SUM(CAST(amount AS REAL)), 0)").as_("total_amount"),
        )
        .where(_expense.matter_id == P())
    )
    expense_rows = conn.execute(exp_q.get_sql(), (matter_id,)).fetchone()

    unbilled_exp_q = (
        Q.from_(_expense)
        .select(
            fn.Count("*").as_("count"),
            LiteralValue("COALESCE(SUM(CAST(amount AS REAL)), 0)").as_("total_amount"),
        )
        .where(_expense.matter_id == P())
        .where(_expense.is_billed == 0)
        .where(_expense.is_billable == 1)
    )
    unbilled_expenses = conn.execute(unbilled_exp_q.get_sql(), (matter_id,)).fetchone()

    # Trust balance
    trust_q = (
        Q.from_(_trust_txn)
        .select(
            LiteralValue(
                "COALESCE(SUM(CASE WHEN transaction_type = 'deposit' THEN CAST(amount AS REAL) ELSE 0 END), 0)"
                " - COALESCE(SUM(CASE WHEN transaction_type IN ('disbursement','fee') THEN CAST(amount AS REAL) ELSE 0 END), 0)"
            ).as_("trust_balance"),
        )
        .where(_trust_txn.matter_id == P())
    )
    trust_row = conn.execute(trust_q.get_sql(), (matter_id,)).fetchone()

    # Open deadlines
    dl_q = (
        Q.from_(_deadline)
        .select(fn.Count("*").as_("count"))
        .where(_deadline.matter_id == P())
        .where(_deadline.is_completed == 0)
    )
    open_deadlines = conn.execute(dl_q.get_sql(), (matter_id,)).fetchone()

    # Invoices summary
    inv_q = (
        Q.from_(_inv)
        .select(
            fn.Count("*").as_("count"),
            LiteralValue("COALESCE(SUM(CAST(total_amount AS REAL)), 0)").as_("total_invoiced"),
            LiteralValue("COALESCE(SUM(CAST(paid_amount AS REAL)), 0)").as_("total_collected"),
        )
        .where(_inv.matter_id == P())
    )
    inv_row = conn.execute(inv_q.get_sql(), (matter_id,)).fetchone()

    ok({
        "matter_id": matter_id,
        "title": m["title"],
        "matter_status": m["status"],
        "practice_area": m["practice_area"],
        "billing_method": m["billing_method"],
        "opened_date": m["opened_date"],
        "closed_date": m.get("closed_date"),
        "time_entries": time_rows["count"],
        "total_hours": str(round(time_rows["total_hours"], 2)),
        "total_time_amount": str(round(time_rows["total_amount"], 2)),
        "unbilled_time_entries": unbilled_time["count"],
        "unbilled_time_amount": str(round(unbilled_time["total_amount"], 2)),
        "expenses": expense_rows["count"],
        "total_expense_amount": str(round(expense_rows["total_amount"], 2)),
        "unbilled_expense_entries": unbilled_expenses["count"],
        "unbilled_expense_amount": str(round(unbilled_expenses["total_amount"], 2)),
        "trust_balance": str(round(trust_row["trust_balance"], 2)),
        "open_deadlines": open_deadlines["count"],
        "invoices": inv_row["count"],
        "total_invoiced": str(round(inv_row["total_invoiced"], 2)),
        "total_collected": str(round(inv_row["total_collected"], 2)),
        "budget": m["budget"],
    })


# ---------------------------------------------------------------------------
# 14. client-portfolio
# ---------------------------------------------------------------------------
def client_portfolio(conn, args):
    client_id = getattr(args, "client_id", None)
    _validate_client(conn, client_id)

    q = (
        Q.from_(_ext)
        .join(_cust).on(_ext.customer_id == _cust.id)
        .select(_ext.star, _cust.name)
        .where(_ext.id == P())
    )
    client = conn.execute(q.get_sql(), (client_id,)).fetchone()
    c = row_to_dict(client)

    mq = (
        Q.from_(_matter)
        .select(
            _matter.id, _matter.naming_series, _matter.title, _matter.practice_area,
            _matter.billing_method, _matter.status, _matter.opened_date, _matter.closed_date,
            _matter.billed_amount, _matter.collected_amount, _matter.trust_balance,
        )
        .where(_matter.client_id == P())
        .orderby(_matter.opened_date, order=Order.desc)
    )
    matters = conn.execute(mq.get_sql(), (client_id,)).fetchall()

    active_count = sum(1 for m in matters if m["status"] == "active")
    closed_count = sum(1 for m in matters if m["status"] == "closed")
    total_billed = sum(Decimal(m["billed_amount"] or "0") for m in matters)
    total_collected = sum(Decimal(m["collected_amount"] or "0") for m in matters)

    ok({
        "client_id": client_id,
        "customer_id": c.get("customer_id"),
        "client_name": c["name"],
        "client_type": c["client_type"],
        "total_matters": len(matters),
        "active_matters": active_count,
        "closed_matters": closed_count,
        "total_billed": str(total_billed),
        "total_collected": str(total_collected),
        "matters": [row_to_dict(m) for m in matters],
    })


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------
ACTIONS = {
    "legal-add-client": add_client,
    "legal-update-client": update_client,
    "legal-get-client": get_client,
    "legal-list-clients": list_clients,
    "legal-add-matter": add_matter,
    "legal-update-matter": update_matter,
    "legal-get-matter": get_matter,
    "legal-list-matters": list_matters,
    "legal-add-matter-party": add_matter_party,
    "legal-list-matter-parties": list_matter_parties,
    "legal-close-matter": close_matter,
    "legal-reopen-matter": reopen_matter,
    "legal-matter-summary": matter_summary,
    "legal-client-portfolio": client_portfolio,
}
