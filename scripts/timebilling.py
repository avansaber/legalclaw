"""LegalClaw -- time & billing domain module

Actions for time tracking, expenses, invoicing, and billing reports (3 tables, 14 actions).
Imported by db_query.py (unified router).
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

try:
    sys.path.insert(0, os.path.expanduser("~/.openclaw/erpclaw/lib"))
    from erpclaw_lib.db import get_connection
    from erpclaw_lib.decimal_utils import to_decimal, round_currency
    from erpclaw_lib.naming import get_next_name, ENTITY_PREFIXES
    from erpclaw_lib.response import ok, err, row_to_dict
    from erpclaw_lib.audit import audit
    from erpclaw_lib.cross_skill import (
        create_invoice, submit_invoice, create_payment, CrossSkillError,
    )

    ENTITY_PREFIXES.setdefault("legalclaw_invoice", "LINV-")
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------
VALID_EXPENSE_CATEGORIES = (
    "filing", "courier", "copying", "expert", "travel",
    "postage", "research", "deposition", "mediation", "other",
)
VALID_INVOICE_STATUSES = (
    "draft", "sent", "paid", "partially_paid", "overdue", "written_off",
)
VALID_INVOICE_FORMATS = ("standard", "ledes")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    if not conn.execute("SELECT id FROM company WHERE id = ?", (company_id,)).fetchone():
        err(f"Company {company_id} not found")


def _validate_matter(conn, matter_id):
    if not matter_id:
        err("--matter-id is required")
    row = conn.execute("SELECT id, client_id, company_id, billing_rate FROM legalclaw_matter WHERE id = ?",
                       (matter_id,)).fetchone()
    if not row:
        err(f"Matter {matter_id} not found")
    return row


def _validate_enum(value, valid_values, field_name):
    if value and value not in valid_values:
        err(f"Invalid {field_name}: {value}. Must be one of: {', '.join(valid_values)}")


def _round_to_tenth(hours_str):
    """Round hours to nearest 0.1 (6-minute increment)."""
    d = to_decimal(hours_str)
    return str(d.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _lookup_customer_id(conn, client_id):
    """Resolve legalclaw_client_ext.id -> customer.id for cross_skill calls."""
    row = conn.execute(
        "SELECT customer_id FROM legalclaw_client_ext WHERE id = ?", (client_id,)
    ).fetchone()
    if not row:
        return None
    return row["customer_id"]


def _get_db_path(conn):
    """Extract the database file path from a connection (for cross_skill calls)."""
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        if row:
            # PRAGMA returns (seq, name, file) -- try dict access first, then index
            try:
                return row["file"]
            except (IndexError, KeyError):
                return row[2]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 1. add-time-entry
# ---------------------------------------------------------------------------
def add_time_entry(conn, args):
    matter_id = getattr(args, "matter_id", None)
    matter_row = _validate_matter(conn, matter_id)
    _validate_company(conn, args.company_id)

    attorney = getattr(args, "attorney", None)
    if not attorney:
        err("--attorney is required")
    te_description = getattr(args, "te_description", None)
    if not te_description:
        err("--te-description is required (time entry description)")

    entry_date = getattr(args, "entry_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hours_raw = getattr(args, "hours", None) or "0"
    hours = _round_to_tenth(hours_raw)

    # Rate: explicit > matter billing_rate > 0
    rate_raw = getattr(args, "rate", None)
    if rate_raw:
        rate = str(to_decimal(rate_raw))
    else:
        rate = matter_row["billing_rate"] or "0"

    amount = str(to_decimal(hours) * to_decimal(rate))

    is_billable = 1
    ib = getattr(args, "is_billable", None)
    if ib is not None and str(ib) == "0":
        is_billable = 0

    te_id = str(uuid.uuid4())
    now = _now_iso()
    conn.execute("""
        INSERT INTO legalclaw_time_entry (
            id, matter_id, attorney, entry_date, hours, rate, amount,
            description, utbms_code, is_billable, is_billed, invoice_id,
            company_id, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        te_id, matter_id, attorney, entry_date, hours, rate, amount,
        te_description,
        getattr(args, "utbms_code", None),
        is_billable, 0, None,
        args.company_id, now, now,
    ))
    audit(conn, "legalclaw_time_entry", te_id, "legal-add-time-entry", args.company_id)
    conn.commit()
    ok({
        "id": te_id, "matter_id": matter_id, "attorney": attorney,
        "hours": hours, "rate": rate, "amount": amount,
        "is_billable": is_billable,
    })


# ---------------------------------------------------------------------------
# 2. update-time-entry
# ---------------------------------------------------------------------------
def update_time_entry(conn, args):
    te_id = getattr(args, "time_entry_id", None)
    if not te_id:
        err("--time-entry-id is required")
    row = conn.execute("SELECT * FROM legalclaw_time_entry WHERE id = ?", (te_id,)).fetchone()
    if not row:
        err(f"Time entry {te_id} not found")
    if row["is_billed"]:
        err(f"Time entry {te_id} is already billed and cannot be modified")

    updates, params, changed = [], [], []
    for arg_name, col_name in {
        "attorney": "attorney", "entry_date": "entry_date",
        "te_description": "description", "utbms_code": "utbms_code",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            updates.append(f"{col_name} = ?")
            params.append(val)
            changed.append(col_name)

    # Recalculate amount if hours or rate changed
    hours_raw = getattr(args, "hours", None)
    rate_raw = getattr(args, "rate", None)
    new_hours = _round_to_tenth(hours_raw) if hours_raw else row["hours"]
    new_rate = str(to_decimal(rate_raw)) if rate_raw else row["rate"]

    if hours_raw:
        updates.append("hours = ?")
        params.append(new_hours)
        changed.append("hours")
    if rate_raw:
        updates.append("rate = ?")
        params.append(new_rate)
        changed.append("rate")

    if hours_raw or rate_raw:
        new_amount = str(to_decimal(new_hours) * to_decimal(new_rate))
        updates.append("amount = ?")
        params.append(new_amount)
        changed.append("amount")

    ib = getattr(args, "is_billable", None)
    if ib is not None:
        updates.append("is_billable = ?")
        params.append(int(ib))
        changed.append("is_billable")

    if not updates:
        err("No fields to update")

    updates.append("updated_at = ?")
    params.append(_now_iso())
    params.append(te_id)
    conn.execute(f"UPDATE legalclaw_time_entry SET {', '.join(updates)} WHERE id = ?", params)
    audit(conn, "legalclaw_time_entry", te_id, "legal-update-time-entry",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": te_id, "updated_fields": changed})


# ---------------------------------------------------------------------------
# 3. list-time-entries
# ---------------------------------------------------------------------------
def list_time_entries(conn, args):
    sql = "SELECT * FROM legalclaw_time_entry WHERE 1=1"
    params = []
    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        sql += " AND matter_id = ?"
        params.append(matter_id)
    attorney = getattr(args, "attorney", None)
    if attorney:
        sql += " AND attorney = ?"
        params.append(attorney)
    is_billed = getattr(args, "is_billed", None)
    if is_billed is not None:
        sql += " AND is_billed = ?"
        params.append(int(is_billed))
    is_billable = getattr(args, "is_billable", None)
    if is_billable is not None:
        sql += " AND is_billable = ?"
        params.append(int(is_billable))
    sql += " ORDER BY entry_date DESC LIMIT ? OFFSET ?"
    params.extend([args.limit, args.offset])
    rows = conn.execute(sql, params).fetchall()
    ok({"time_entries": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 4. add-expense
# ---------------------------------------------------------------------------
def add_expense(conn, args):
    matter_id = getattr(args, "matter_id", None)
    _validate_matter(conn, matter_id)
    _validate_company(conn, args.company_id)

    amount_raw = getattr(args, "expense_amount", None) or getattr(args, "amount", None)
    if not amount_raw:
        err("--expense-amount is required")
    amount = str(to_decimal(amount_raw))

    category = getattr(args, "category", None) or "other"
    _validate_enum(category, VALID_EXPENSE_CATEGORIES, "category")

    expense_date = getattr(args, "expense_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    is_billable = 1
    ib = getattr(args, "is_billable", None)
    if ib is not None and str(ib) == "0":
        is_billable = 0

    exp_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO legalclaw_expense (
            id, matter_id, expense_date, amount, category, description,
            is_billable, is_billed, invoice_id, receipt_reference,
            company_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        exp_id, matter_id, expense_date, amount, category,
        getattr(args, "expense_description", None),
        is_billable, 0, None,
        getattr(args, "receipt_reference", None),
        args.company_id, _now_iso(),
    ))
    audit(conn, "legalclaw_expense", exp_id, "legal-add-expense", args.company_id)
    conn.commit()
    ok({"id": exp_id, "matter_id": matter_id, "amount": amount, "category": category})


# ---------------------------------------------------------------------------
# 5. update-expense
# ---------------------------------------------------------------------------
def update_expense(conn, args):
    exp_id = getattr(args, "expense_id", None)
    if not exp_id:
        err("--expense-id is required")
    row = conn.execute("SELECT * FROM legalclaw_expense WHERE id = ?", (exp_id,)).fetchone()
    if not row:
        err(f"Expense {exp_id} not found")
    if row["is_billed"]:
        err(f"Expense {exp_id} is already billed and cannot be modified")

    updates, params, changed = [], [], []
    for arg_name, col_name in {
        "expense_date": "expense_date", "category": "category",
        "expense_description": "description", "receipt_reference": "receipt_reference",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            if arg_name == "category":
                _validate_enum(val, VALID_EXPENSE_CATEGORIES, "category")
            updates.append(f"{col_name} = ?")
            params.append(val)
            changed.append(col_name)

    amount_raw = getattr(args, "expense_amount", None) or getattr(args, "amount", None)
    if amount_raw:
        updates.append("amount = ?")
        params.append(str(to_decimal(amount_raw)))
        changed.append("amount")

    ib = getattr(args, "is_billable", None)
    if ib is not None:
        updates.append("is_billable = ?")
        params.append(int(ib))
        changed.append("is_billable")

    if not updates:
        err("No fields to update")

    params.append(exp_id)
    conn.execute(f"UPDATE legalclaw_expense SET {', '.join(updates)} WHERE id = ?", params)
    audit(conn, "legalclaw_expense", exp_id, "legal-update-expense",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": exp_id, "updated_fields": changed})


# ---------------------------------------------------------------------------
# 6. list-expenses
# ---------------------------------------------------------------------------
def list_expenses(conn, args):
    sql = "SELECT * FROM legalclaw_expense WHERE 1=1"
    params = []
    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        sql += " AND matter_id = ?"
        params.append(matter_id)
    category = getattr(args, "category", None)
    if category:
        sql += " AND category = ?"
        params.append(category)
    is_billed = getattr(args, "is_billed", None)
    if is_billed is not None:
        sql += " AND is_billed = ?"
        params.append(int(is_billed))
    sql += " ORDER BY expense_date DESC LIMIT ? OFFSET ?"
    params.extend([args.limit, args.offset])
    rows = conn.execute(sql, params).fetchall()
    ok({"expenses": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 7. generate-invoice
# ---------------------------------------------------------------------------
def generate_invoice(conn, args):
    matter_id = getattr(args, "matter_id", None)
    matter_row = _validate_matter(conn, matter_id)
    _validate_company(conn, args.company_id)

    client_id = matter_row["client_id"]
    invoice_date = getattr(args, "invoice_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    due_date = getattr(args, "due_date", None)
    inv_format = getattr(args, "invoice_format", None) or "standard"
    _validate_enum(inv_format, VALID_INVOICE_FORMATS, "format")

    # Collect unbilled billable time entries
    time_entries = conn.execute("""
        SELECT id, amount FROM legalclaw_time_entry
        WHERE matter_id = ? AND is_billed = 0 AND is_billable = 1
    """, (matter_id,)).fetchall()

    # Collect unbilled billable expenses
    expenses = conn.execute("""
        SELECT id, amount FROM legalclaw_expense
        WHERE matter_id = ? AND is_billed = 0 AND is_billable = 1
    """, (matter_id,)).fetchall()

    if not time_entries and not expenses:
        err(f"No unbilled items found for matter {matter_id}")

    time_amount = sum(to_decimal(r["amount"]) for r in time_entries)
    expense_amount = sum(to_decimal(r["amount"]) for r in expenses)
    total_amount = time_amount + expense_amount

    # Look up matter title for invoice line item description
    matter_full = conn.execute(
        "SELECT title FROM legalclaw_matter WHERE id = ?", (matter_id,)
    ).fetchone()
    matter_title = matter_full["title"] if matter_full else "Legal Services"

    inv_id = str(uuid.uuid4())
    ns = get_next_name(conn, "legalclaw_invoice", company_id=args.company_id)
    now = _now_iso()

    # ------------------------------------------------------------------
    # Cross-skill: create a real sales_invoice via erpclaw-selling
    # ------------------------------------------------------------------
    sales_invoice_id = None
    cross_skill_error = None
    customer_id = _lookup_customer_id(conn, client_id)
    db_path = _get_db_path(conn)

    if customer_id:
        # Build line items — separate time and expense lines for clarity
        invoice_items = []
        if time_amount > 0:
            invoice_items.append({
                "description": f"Legal Services - {matter_title} (time)",
                "qty": "1",
                "rate": str(time_amount),
            })
        if expense_amount > 0:
            invoice_items.append({
                "description": f"Legal Services - {matter_title} (expenses)",
                "qty": "1",
                "rate": str(expense_amount),
            })

        try:
            inv_result = create_invoice(
                customer_id=customer_id,
                items=invoice_items,
                company_id=args.company_id,
                posting_date=invoice_date,
                due_date=due_date,
                remarks=f"LegalClaw invoice for matter: {matter_title}",
                db_path=db_path,
            )
            # Extract the sales_invoice id from the response
            si = inv_result.get("sales_invoice") or inv_result.get("data", {})
            sales_invoice_id = si.get("id") if isinstance(si, dict) else None

            # Auto-submit the sales invoice to post GL entries
            if sales_invoice_id:
                try:
                    submit_invoice(sales_invoice_id, db_path=db_path)
                except CrossSkillError:
                    # Submission failure is non-fatal; invoice was created in draft
                    pass
        except CrossSkillError as e:
            # Non-fatal: selling module may not be installed.
            # Legal invoice is still created as the authoritative record.
            cross_skill_error = str(e)

    conn.execute("""
        INSERT INTO legalclaw_invoice (
            id, naming_series, matter_id, client_id, invoice_date, due_date,
            time_amount, expense_amount, total_amount, paid_amount, balance,
            format, status, sales_invoice_id, notes, company_id, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        inv_id, ns, matter_id, client_id, invoice_date, due_date,
        str(time_amount), str(expense_amount), str(total_amount),
        "0", str(total_amount),
        inv_format, "draft", sales_invoice_id,
        getattr(args, "notes", None),
        args.company_id, now, now,
    ))

    # Mark time entries as billed
    for te in time_entries:
        conn.execute("""
            UPDATE legalclaw_time_entry SET is_billed = 1, invoice_id = ?, updated_at = ?
            WHERE id = ?
        """, (inv_id, now, te["id"]))

    # Mark expenses as billed
    for exp in expenses:
        conn.execute("""
            UPDATE legalclaw_expense SET is_billed = 1, invoice_id = ?
            WHERE id = ?
        """, (inv_id, exp["id"]))

    # Update matter billed_amount (Decimal-safe addition)
    current_billed = conn.execute(
        "SELECT billed_amount FROM legalclaw_matter WHERE id = ?", (matter_id,)
    ).fetchone()
    new_billed = to_decimal(current_billed["billed_amount"]) + total_amount
    conn.execute("""
        UPDATE legalclaw_matter SET billed_amount = ?, updated_at = ? WHERE id = ?
    """, (str(new_billed), now, matter_id))

    audit(conn, "legalclaw_invoice", inv_id, "legal-generate-invoice", args.company_id)
    conn.commit()

    result = {
        "id": inv_id, "naming_series": ns, "matter_id": matter_id,
        "time_entries_count": len(time_entries),
        "expenses_count": len(expenses),
        "time_amount": str(time_amount),
        "expense_amount": str(expense_amount),
        "total_amount": str(total_amount),
        "invoice_status": "draft",
    }
    if sales_invoice_id:
        result["sales_invoice_id"] = sales_invoice_id
    if cross_skill_error:
        result["cross_skill_warning"] = cross_skill_error
    ok(result)


# ---------------------------------------------------------------------------
# 8. get-invoice
# ---------------------------------------------------------------------------
def get_invoice(conn, args):
    inv_id = getattr(args, "invoice_id", None)
    if not inv_id:
        err("--invoice-id is required")
    row = conn.execute("SELECT * FROM legalclaw_invoice WHERE id = ?", (inv_id,)).fetchone()
    if not row:
        err(f"Invoice {inv_id} not found")

    # Include line items
    time_entries = conn.execute(
        "SELECT * FROM legalclaw_time_entry WHERE invoice_id = ?", (inv_id,)
    ).fetchall()
    expenses = conn.execute(
        "SELECT * FROM legalclaw_expense WHERE invoice_id = ?", (inv_id,)
    ).fetchall()

    result = row_to_dict(row)
    result["time_entries"] = [row_to_dict(r) for r in time_entries]
    result["expenses"] = [row_to_dict(r) for r in expenses]
    ok(result)


# ---------------------------------------------------------------------------
# 9. list-invoices
# ---------------------------------------------------------------------------
def list_invoices(conn, args):
    sql = "SELECT * FROM legalclaw_invoice WHERE 1=1"
    params = []
    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        sql += " AND matter_id = ?"
        params.append(matter_id)
    client_id = getattr(args, "client_id", None)
    if client_id:
        sql += " AND client_id = ?"
        params.append(client_id)
    invoice_status = getattr(args, "invoice_status", None)
    if invoice_status:
        sql += " AND status = ?"
        params.append(invoice_status)
    if args.company_id:
        sql += " AND company_id = ?"
        params.append(args.company_id)
    sql += " ORDER BY invoice_date DESC LIMIT ? OFFSET ?"
    params.extend([args.limit, args.offset])
    rows = conn.execute(sql, params).fetchall()
    ok({"invoices": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 10. send-invoice
# ---------------------------------------------------------------------------
def send_invoice(conn, args):
    inv_id = getattr(args, "invoice_id", None)
    if not inv_id:
        err("--invoice-id is required")
    row = conn.execute("SELECT * FROM legalclaw_invoice WHERE id = ?", (inv_id,)).fetchone()
    if not row:
        err(f"Invoice {inv_id} not found")
    if row["status"] not in ("draft",):
        err(f"Invoice {inv_id} is not in draft status (current: {row['status']})")

    # Cross-skill: submit the linked sales invoice if not already submitted
    si_id = row["sales_invoice_id"]
    submit_warning = None
    if si_id:
        db_path = _get_db_path(conn)
        try:
            submit_invoice(si_id, db_path=db_path)
        except CrossSkillError as e:
            # Non-fatal — legal invoice still transitions to sent
            submit_warning = str(e)

    now = _now_iso()
    conn.execute("""
        UPDATE legalclaw_invoice SET status = 'sent', updated_at = ? WHERE id = ?
    """, (now, inv_id))
    audit(conn, "legalclaw_invoice", inv_id, "legal-send-invoice",
          getattr(args, "company_id", None))
    conn.commit()
    result = {"id": inv_id, "invoice_status": "sent"}
    if si_id:
        result["sales_invoice_id"] = si_id
    if submit_warning:
        result["cross_skill_warning"] = submit_warning
    ok(result)


# ---------------------------------------------------------------------------
# 11. record-payment
# ---------------------------------------------------------------------------
def record_payment(conn, args):
    inv_id = getattr(args, "invoice_id", None)
    if not inv_id:
        err("--invoice-id is required")
    row = conn.execute("SELECT * FROM legalclaw_invoice WHERE id = ?", (inv_id,)).fetchone()
    if not row:
        err(f"Invoice {inv_id} not found")
    if row["status"] in ("paid", "written_off"):
        err(f"Invoice {inv_id} is already {row['status']}")

    payment_amount_raw = getattr(args, "payment_amount", None)
    if not payment_amount_raw:
        err("--payment-amount is required")
    payment_amount = to_decimal(payment_amount_raw)
    if payment_amount <= 0:
        err("Payment amount must be greater than 0")

    current_paid = to_decimal(row["paid_amount"])
    total = to_decimal(row["total_amount"])
    new_paid = current_paid + payment_amount
    new_balance = total - new_paid

    if new_balance < 0:
        err(f"Payment of {payment_amount} exceeds remaining balance of {total - current_paid}")

    new_status = "paid" if new_balance == 0 else "partially_paid"

    # ------------------------------------------------------------------
    # Cross-skill: create a payment entry via erpclaw-payments
    # ------------------------------------------------------------------
    payment_entry_id = None
    payment_warning = None
    si_id = row["sales_invoice_id"]
    client_id = row["client_id"]
    customer_id = _lookup_customer_id(conn, client_id)
    db_path = _get_db_path(conn)
    company_id = getattr(args, "company_id", None) or row["company_id"]

    if customer_id:
        try:
            pay_args = {
                "payment_type": "receive",
                "party_type": "customer",
                "party_id": customer_id,
                "paid_amount": str(payment_amount),
                "company_id": company_id,
                "remarks": f"LegalClaw payment for invoice {row.get('naming_series', inv_id)}",
                "db_path": db_path,
            }
            # Link to the sales invoice if one exists
            if si_id:
                pay_args["reference_type"] = "sales_invoice"
                pay_args["reference_id"] = si_id

            pay_result = create_payment(**pay_args)
            pe = pay_result.get("payment_entry") or pay_result.get("data", {})
            payment_entry_id = pe.get("id") if isinstance(pe, dict) else None
        except CrossSkillError as e:
            # Non-fatal: payments module may not be installed.
            # Legal invoice payment is still recorded locally.
            payment_warning = str(e)

    now = _now_iso()
    conn.execute("""
        UPDATE legalclaw_invoice
        SET paid_amount = ?, balance = ?, status = ?, updated_at = ?
        WHERE id = ?
    """, (str(new_paid), str(new_balance), new_status, now, inv_id))

    # Update matter collected_amount (Decimal-safe addition)
    matter_id = row["matter_id"]
    current_collected = conn.execute(
        "SELECT collected_amount FROM legalclaw_matter WHERE id = ?", (matter_id,)
    ).fetchone()
    new_collected = to_decimal(current_collected["collected_amount"]) + payment_amount
    conn.execute("""
        UPDATE legalclaw_matter SET collected_amount = ?, updated_at = ? WHERE id = ?
    """, (str(new_collected), now, matter_id))

    audit(conn, "legalclaw_invoice", inv_id, "legal-record-payment",
          getattr(args, "company_id", None))
    conn.commit()

    result = {
        "id": inv_id, "payment_amount": str(payment_amount),
        "paid_amount": str(new_paid), "balance": str(new_balance),
        "invoice_status": new_status,
    }
    if payment_entry_id:
        result["payment_entry_id"] = payment_entry_id
    if si_id:
        result["sales_invoice_id"] = si_id
    if payment_warning:
        result["cross_skill_warning"] = payment_warning
    ok(result)


# ---------------------------------------------------------------------------
# 12. write-off-invoice
# ---------------------------------------------------------------------------
def write_off_invoice(conn, args):
    inv_id = getattr(args, "invoice_id", None)
    if not inv_id:
        err("--invoice-id is required")
    row = conn.execute("SELECT * FROM legalclaw_invoice WHERE id = ?", (inv_id,)).fetchone()
    if not row:
        err(f"Invoice {inv_id} not found")
    if row["status"] in ("paid", "written_off"):
        err(f"Invoice {inv_id} is already {row['status']}")

    now = _now_iso()
    conn.execute("""
        UPDATE legalclaw_invoice SET status = 'written_off', balance = '0', updated_at = ?
        WHERE id = ?
    """, (now, inv_id))
    audit(conn, "legalclaw_invoice", inv_id, "legal-write-off-invoice",
          getattr(args, "company_id", None))
    conn.commit()
    ok({"id": inv_id, "invoice_status": "written_off", "written_off_amount": row["balance"]})


# ---------------------------------------------------------------------------
# 13. billable-utilization-report
# ---------------------------------------------------------------------------
def billable_utilization_report(conn, args):
    _validate_company(conn, args.company_id)

    rows = conn.execute("""
        SELECT attorney,
               SUM(CAST(hours AS REAL)) as total_hours,
               SUM(CASE WHEN is_billable = 1 THEN CAST(hours AS REAL) ELSE 0 END) as billable_hours,
               SUM(CASE WHEN is_billable = 0 THEN CAST(hours AS REAL) ELSE 0 END) as non_billable_hours,
               SUM(CASE WHEN is_billable = 1 THEN CAST(amount AS REAL) ELSE 0 END) as billable_amount,
               SUM(CASE WHEN is_billed = 1 THEN CAST(amount AS REAL) ELSE 0 END) as billed_amount,
               COUNT(*) as entry_count
        FROM legalclaw_time_entry
        WHERE company_id = ?
        GROUP BY attorney
        ORDER BY total_hours DESC
    """, (args.company_id,)).fetchall()

    attorneys = []
    for r in rows:
        total = r["total_hours"] or 0
        billable = r["billable_hours"] or 0
        utilization = round((billable / total * 100) if total > 0 else 0, 1)
        attorneys.append({
            "attorney": r["attorney"],
            "total_hours": str(round(total, 1)),
            "billable_hours": str(round(billable, 1)),
            "non_billable_hours": str(round(r["non_billable_hours"] or 0, 1)),
            "billable_amount": str(round(r["billable_amount"] or 0, 2)),
            "billed_amount": str(round(r["billed_amount"] or 0, 2)),
            "utilization_pct": str(utilization),
            "entry_count": r["entry_count"],
        })

    ok({"attorneys": attorneys, "count": len(attorneys)})


# ---------------------------------------------------------------------------
# 14. ar-aging-report
# ---------------------------------------------------------------------------
def ar_aging_report(conn, args):
    _validate_company(conn, args.company_id)

    rows = conn.execute("""
        SELECT i.*, cust.name as client_name, m.title as matter_title
        FROM legalclaw_invoice i
        JOIN legalclaw_client_ext ext ON i.client_id = ext.id
        JOIN customer cust ON ext.customer_id = cust.id
        JOIN legalclaw_matter m ON i.matter_id = m.id
        WHERE i.company_id = ? AND i.status IN ('sent','partially_paid','overdue')
        AND CAST(i.balance AS REAL) > 0
        ORDER BY i.invoice_date ASC
    """, (args.company_id,)).fetchall()

    current, over_30, over_60, over_90 = [], [], [], []
    today = datetime.now(timezone.utc).date()

    for r in rows:
        inv = row_to_dict(r)
        inv_date = datetime.strptime(r["invoice_date"], "%Y-%m-%d").date()
        days_old = (today - inv_date).days
        inv["days_outstanding"] = days_old

        if days_old <= 30:
            current.append(inv)
        elif days_old <= 60:
            over_30.append(inv)
        elif days_old <= 90:
            over_60.append(inv)
        else:
            over_90.append(inv)

    sum_balance = lambda lst: str(sum(to_decimal(x["balance"]) for x in lst))

    ok({
        "current": {"invoices": current, "count": len(current), "total": sum_balance(current)},
        "over_30": {"invoices": over_30, "count": len(over_30), "total": sum_balance(over_30)},
        "over_60": {"invoices": over_60, "count": len(over_60), "total": sum_balance(over_60)},
        "over_90": {"invoices": over_90, "count": len(over_90), "total": sum_balance(over_90)},
        "total_outstanding": sum_balance(current + over_30 + over_60 + over_90),
    })


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------
ACTIONS = {
    "legal-add-time-entry": add_time_entry,
    "legal-update-time-entry": update_time_entry,
    "legal-list-time-entries": list_time_entries,
    "legal-add-expense": add_expense,
    "legal-update-expense": update_expense,
    "legal-list-expenses": list_expenses,
    "legal-generate-invoice": generate_invoice,
    "legal-get-invoice": get_invoice,
    "legal-list-invoices": list_invoices,
    "legal-send-invoice": send_invoice,
    "legal-record-payment": record_payment,
    "legal-write-off-invoice": write_off_invoice,
    "legal-billable-utilization-report": billable_utilization_report,
    "legal-ar-aging-report": ar_aging_report,
}
