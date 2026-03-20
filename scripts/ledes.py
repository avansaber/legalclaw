"""LegalClaw -- LEDES e-billing module

LEDES 1998B pipe-delimited invoice generation and validation.
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
    from erpclaw_lib.response import ok, err, row_to_dict
    from erpclaw_lib.audit import audit
    from erpclaw_lib.query import Q, P, Table, Field, fn, Order
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ── Table aliases ──
_inv = Table("legalclaw_invoice")
_matter = Table("legalclaw_matter")
_ext = Table("legalclaw_client_ext")
_te = Table("legalclaw_time_entry")
_expense = Table("legalclaw_expense")
_cust = Table("customer")
_company = Table("company")

# LEDES 1998B header row
LEDES_HEADER = "LEDES1998B[]"
LEDES_COLUMN_HEADER = (
    "INVOICE_DATE|INVOICE_NUMBER|CLIENT_ID|LAW_FIRM_MATTER_ID|"
    "INVOICE_TOTAL|BILLING_START_DATE|BILLING_END_DATE|"
    "LAW_FIRM_ID|LAW_FIRM_NAME|CLIENT_MATTER_ID[]"
)
LEDES_LINE_HEADER = (
    "LINE_ITEM_NUMBER|EXP/FEE/INV_ADJ_TYPE|LINE_ITEM_NUMBER_OF_UNITS|"
    "LINE_ITEM_UNIT_COST|LINE_ITEM_TOTAL|LINE_ITEM_DATE|"
    "LINE_ITEM_TASK_CODE|LINE_ITEM_EXPENSE_CODE|LINE_ITEM_ACTIVITY_CODE|"
    "TIMEKEEPER_ID|LINE_ITEM_DESCRIPTION[]"
)

# UTBMS task code prefixes
UTBMS_TASK_CODES = {
    "L": "Litigation", "A": "Counseling/Advisory",
    "P": "Project", "B": "Bankruptcy",
}


def _format_date_ledes(date_str):
    """Convert YYYY-MM-DD to YYYYMMDD for LEDES."""
    if not date_str:
        return ""
    return date_str.replace("-", "")


def _format_amount_ledes(amount_str):
    """Format amount for LEDES: no currency symbol, 2 decimal places."""
    if not amount_str:
        return "0.00"
    d = to_decimal(amount_str)
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _parse_utbms_code(utbms_code):
    """Parse UTBMS code into task_code and activity_code.

    UTBMS codes follow the pattern: L110 = Task L110 (Litigation)
    First letter = task area, digits = activity code.
    """
    if not utbms_code:
        return "", ""
    # Task code is the full UTBMS code
    task_code = utbms_code
    # Activity code is the numeric portion
    activity_code = "".join(c for c in utbms_code if c.isdigit())
    return task_code, activity_code


# ---------------------------------------------------------------------------
# LEDES generation (called from generate_invoice when format='ledes')
# ---------------------------------------------------------------------------
def generate_ledes_output(conn, invoice_id):
    """Generate LEDES 1998B formatted output for an invoice.

    Returns a dict with ledes_output (string) and metadata.
    """
    # Fetch invoice
    inv = conn.execute(
        Q.from_(_inv).select(_inv.star).where(_inv.id == P()).get_sql(),
        (invoice_id,)).fetchone()
    if not inv:
        return {"error": f"Invoice {invoice_id} not found"}

    # Fetch matter
    matter = conn.execute(
        Q.from_(_matter).select(_matter.star).where(_matter.id == P()).get_sql(),
        (inv["matter_id"],)).fetchone()
    if not matter:
        return {"error": f"Matter {inv['matter_id']} not found"}

    # Fetch client (customer)
    client_ext = conn.execute(
        Q.from_(_ext).select(_ext.star).where(_ext.id == P()).get_sql(),
        (inv["client_id"],)).fetchone()
    customer = None
    if client_ext:
        customer = conn.execute(
            Q.from_(_cust).select(_cust.star).where(_cust.id == P()).get_sql(),
            (client_ext["customer_id"],)).fetchone()

    # Fetch company (law firm)
    company = conn.execute(
        Q.from_(_company).select(_company.star).where(_company.id == P()).get_sql(),
        (inv["company_id"],)).fetchone()

    # Fetch time entries for this invoice
    time_entries = conn.execute(
        Q.from_(_te).select(_te.star).where(_te.invoice_id == P())
        .orderby(_te.entry_date).get_sql(),
        (invoice_id,)).fetchall()

    # Fetch expenses for this invoice
    expenses = conn.execute(
        Q.from_(_expense).select(_expense.star).where(_expense.invoice_id == P())
        .orderby(_expense.expense_date).get_sql(),
        (invoice_id,)).fetchall()

    # Determine billing period from line items
    all_dates = []
    for te in time_entries:
        if te["entry_date"]:
            all_dates.append(te["entry_date"])
    for exp in expenses:
        if exp["expense_date"]:
            all_dates.append(exp["expense_date"])

    billing_start = min(all_dates) if all_dates else inv["invoice_date"]
    billing_end = max(all_dates) if all_dates else inv["invoice_date"]

    # Build LEDES output
    lines = []
    lines.append(LEDES_HEADER)
    lines.append(LEDES_COLUMN_HEADER)

    # Invoice header line
    invoice_line = "|".join([
        _format_date_ledes(inv["invoice_date"]),
        inv["naming_series"] or inv["id"][:12],
        client_ext["customer_id"] if client_ext else "",
        matter["matter_number"] or matter["id"][:12],
        _format_amount_ledes(inv["total_amount"]),
        _format_date_ledes(billing_start),
        _format_date_ledes(billing_end),
        inv["company_id"][:12] if inv["company_id"] else "",
        company["name"] if company else "",
        matter["id"],
    ]) + "[]"
    lines.append(invoice_line)

    # Line item header
    lines.append(LEDES_LINE_HEADER)

    line_num = 1

    # Time entry line items
    for te in time_entries:
        task_code, activity_code = _parse_utbms_code(te["utbms_code"])
        line = "|".join([
            str(line_num),
            "F",  # Fee
            te["hours"] or "0",
            _format_amount_ledes(te["rate"]),
            _format_amount_ledes(te["amount"]),
            _format_date_ledes(te["entry_date"]),
            task_code,
            "",  # expense_code (N/A for fees)
            activity_code,
            te["attorney"] or "",
            te["description"] or "",
        ]) + "[]"
        lines.append(line)
        line_num += 1

    # Expense line items
    for exp in expenses:
        line = "|".join([
            str(line_num),
            "E",  # Expense
            "1",  # qty
            _format_amount_ledes(exp["amount"]),
            _format_amount_ledes(exp["amount"]),
            _format_date_ledes(exp["expense_date"]),
            "",  # task_code
            exp["category"] or "",
            "",  # activity_code
            "",  # timekeeper
            exp["description"] or "",
        ]) + "[]"
        lines.append(line)
        line_num += 1

    ledes_output = "\n".join(lines)

    return {
        "ledes_output": ledes_output,
        "invoice_id": invoice_id,
        "invoice_number": inv["naming_series"] or inv["id"][:12],
        "format": "LEDES1998B",
        "line_items": line_num - 1,
        "time_entries": len(time_entries),
        "expenses": len(expenses),
        "total_amount": _format_amount_ledes(inv["total_amount"]),
        "billing_start": billing_start,
        "billing_end": billing_end,
    }


# ---------------------------------------------------------------------------
# legal-generate-invoice-ledes (standalone LEDES generation)
# ---------------------------------------------------------------------------
def generate_invoice_ledes(conn, args):
    """Generate LEDES 1998B output for an existing invoice."""
    inv_id = getattr(args, "invoice_id", None)
    if not inv_id:
        err("--invoice-id is required")

    result = generate_ledes_output(conn, inv_id)
    if "error" in result:
        err(result["error"])

    ok(result)


# ---------------------------------------------------------------------------
# legal-validate-ledes
# ---------------------------------------------------------------------------
def validate_ledes(conn, args):
    """Validate LEDES format compliance for an invoice.

    Checks:
    1. Invoice exists and has format='ledes'
    2. All required header fields present
    3. Line items have valid UTBMS codes (if provided)
    4. Amounts are properly formatted (2 decimal places)
    5. Dates are in YYYYMMDD format
    """
    inv_id = getattr(args, "invoice_id", None)
    if not inv_id:
        err("--invoice-id is required")

    inv = conn.execute(
        Q.from_(_inv).select(_inv.star).where(_inv.id == P()).get_sql(),
        (inv_id,)).fetchone()
    if not inv:
        err(f"Invoice {inv_id} not found")

    issues = []
    warnings = []

    # Check 1: format field
    if inv["format"] != "ledes":
        issues.append("Invoice format is not 'ledes'")

    # Check 2: required header fields
    if not inv["invoice_date"]:
        issues.append("Missing invoice_date")
    if not inv["naming_series"]:
        warnings.append("No naming_series (invoice number) — ID will be used")
    if not inv["total_amount"] or to_decimal(inv["total_amount"]) == 0:
        warnings.append("Invoice total is zero")

    # Check matter linkage
    matter = conn.execute(
        Q.from_(_matter).select(_matter.star).where(_matter.id == P()).get_sql(),
        (inv["matter_id"],)).fetchone()
    if not matter:
        issues.append(f"Matter {inv['matter_id']} not found")
    else:
        if not matter["matter_number"]:
            warnings.append("Matter has no matter_number — ID will be used as LAW_FIRM_MATTER_ID")

    # Check client linkage
    client_ext = conn.execute(
        Q.from_(_ext).select(_ext.star).where(_ext.id == P()).get_sql(),
        (inv["client_id"],)).fetchone()
    if not client_ext:
        issues.append(f"Client extension {inv['client_id']} not found")

    # Check 3: line items UTBMS codes
    time_entries = conn.execute(
        Q.from_(_te).select(_te.star).where(_te.invoice_id == P()).get_sql(),
        (inv_id,)).fetchall()

    entries_without_utbms = 0
    for te in time_entries:
        if not te["utbms_code"]:
            entries_without_utbms += 1

    if entries_without_utbms > 0:
        warnings.append(f"{entries_without_utbms} time entries lack UTBMS codes")

    # Check 4: amounts are valid decimals
    for te in time_entries:
        try:
            to_decimal(te["amount"])
        except Exception:
            issues.append(f"Time entry {te['id'][:8]} has invalid amount: {te['amount']}")
        try:
            to_decimal(te["rate"])
        except Exception:
            issues.append(f"Time entry {te['id'][:8]} has invalid rate: {te['rate']}")

    expenses = conn.execute(
        Q.from_(_expense).select(_expense.star).where(_expense.invoice_id == P()).get_sql(),
        (inv_id,)).fetchall()

    for exp in expenses:
        try:
            to_decimal(exp["amount"])
        except Exception:
            issues.append(f"Expense {exp['id'][:8]} has invalid amount: {exp['amount']}")

    # Check 5: dates are valid (ISO format YYYY-MM-DD)
    if inv["invoice_date"]:
        parts = inv["invoice_date"].split("-")
        if len(parts) != 3 or len(parts[0]) != 4 or len(parts[1]) != 2 or len(parts[2]) != 2:
            issues.append(f"Invalid invoice_date format: {inv['invoice_date']}")
        else:
            try:
                int(parts[0]); int(parts[1]); int(parts[2])
            except ValueError:
                issues.append(f"Invalid invoice_date format: {inv['invoice_date']}")

    is_valid = len(issues) == 0

    # Try generating LEDES output to verify it works
    ledes_result = None
    if is_valid:
        ledes_result = generate_ledes_output(conn, inv_id)
        if "error" in ledes_result:
            issues.append(f"LEDES generation failed: {ledes_result['error']}")
            is_valid = False

    result = {
        "invoice_id": inv_id,
        "is_valid": is_valid,
        "issues": issues,
        "warnings": warnings,
        "time_entries_count": len(time_entries),
        "expenses_count": len(expenses),
    }

    if ledes_result and "ledes_output" in ledes_result:
        result["ledes_line_count"] = ledes_result["ledes_output"].count("\n") + 1

    ok(result)


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------
ACTIONS = {
    "legal-generate-invoice-ledes": generate_invoice_ledes,
    "legal-validate-ledes": validate_ledes,
}
