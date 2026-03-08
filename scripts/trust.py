"""LegalClaw -- trust accounting domain module

Actions for IOLTA/escrow trust accounts, transactions, reconciliation (2 tables, 10 actions).
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

    ENTITY_PREFIXES.setdefault("legalclaw_trust_account", "LTRS-")
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_ACCOUNT_TYPES = ("iolta", "escrow", "retainer", "other")
VALID_TRANSACTION_TYPES = ("deposit", "disbursement", "transfer", "interest", "fee")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    if not conn.execute("SELECT id FROM company WHERE id = ?", (company_id,)).fetchone():
        err(f"Company {company_id} not found")


def _validate_trust_account(conn, trust_account_id):
    if not trust_account_id:
        err("--trust-account-id is required")
    row = conn.execute("SELECT * FROM legalclaw_trust_account WHERE id = ?",
                       (trust_account_id,)).fetchone()
    if not row:
        err(f"Trust account {trust_account_id} not found")
    return row


def _validate_enum(value, valid_values, field_name):
    if value and value not in valid_values:
        err(f"Invalid {field_name}: {value}. Must be one of: {', '.join(valid_values)}")


# ---------------------------------------------------------------------------
# 1. add-trust-account
# ---------------------------------------------------------------------------
def add_trust_account(conn, args):
    _validate_company(conn, args.company_id)
    name = getattr(args, "trust_name", None)
    if not name:
        err("--trust-name is required")

    account_type = getattr(args, "account_type", None) or "iolta"
    _validate_enum(account_type, VALID_ACCOUNT_TYPES, "account-type")

    ta_id = str(uuid.uuid4())
    ns = get_next_name(conn, "legalclaw_trust_account", company_id=args.company_id)
    now = _now_iso()

    conn.execute("""
        INSERT INTO legalclaw_trust_account (
            id, naming_series, name, bank_name, account_number,
            account_type, current_balance, company_id, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        ta_id, ns, name,
        getattr(args, "bank_name", None),
        getattr(args, "account_number", None),
        account_type, "0", args.company_id, now, now,
    ))
    audit(conn, "legalclaw_trust_account", ta_id, "legal-add-trust-account", args.company_id)
    conn.commit()
    ok({"id": ta_id, "naming_series": ns, "name": name, "account_type": account_type,
        "current_balance": "0"})


# ---------------------------------------------------------------------------
# 2. get-trust-account
# ---------------------------------------------------------------------------
def get_trust_account(conn, args):
    ta_id = getattr(args, "trust_account_id", None)
    row = _validate_trust_account(conn, ta_id)
    ok(row_to_dict(row))


# ---------------------------------------------------------------------------
# 3. list-trust-accounts
# ---------------------------------------------------------------------------
def list_trust_accounts(conn, args):
    sql = "SELECT * FROM legalclaw_trust_account WHERE 1=1"
    params = []
    if args.company_id:
        sql += " AND company_id = ?"
        params.append(args.company_id)
    account_type = getattr(args, "account_type", None)
    if account_type:
        sql += " AND account_type = ?"
        params.append(account_type)
    sql += " ORDER BY name ASC LIMIT ? OFFSET ?"
    params.extend([args.limit, args.offset])
    rows = conn.execute(sql, params).fetchall()
    ok({"trust_accounts": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 4. deposit-trust
# ---------------------------------------------------------------------------
def deposit_trust(conn, args):
    ta_id = getattr(args, "trust_account_id", None)
    ta_row = _validate_trust_account(conn, ta_id)
    _validate_company(conn, args.company_id)

    amount_raw = getattr(args, "amount", None)
    if not amount_raw:
        err("--amount is required")
    amount = to_decimal(amount_raw)
    if amount <= 0:
        err("Deposit amount must be greater than 0")

    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        if not conn.execute("SELECT id FROM legalclaw_matter WHERE id = ?", (matter_id,)).fetchone():
            err(f"Matter {matter_id} not found")

    transaction_date = getattr(args, "transaction_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    txn_id = str(uuid.uuid4())
    now = _now_iso()
    conn.execute("""
        INSERT INTO legalclaw_trust_transaction (
            id, trust_account_id, matter_id, transaction_type, transaction_date,
            amount, reference, payee, description, company_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        txn_id, ta_id, matter_id, "deposit", transaction_date,
        str(amount),
        getattr(args, "reference", None),
        getattr(args, "payee", None),
        getattr(args, "trust_description", None),
        args.company_id, now,
    ))

    # Update trust account balance
    new_balance = to_decimal(ta_row["current_balance"]) + amount
    conn.execute("""
        UPDATE legalclaw_trust_account SET current_balance = ?, updated_at = ? WHERE id = ?
    """, (str(new_balance), now, ta_id))

    # Update matter trust_balance if matter specified
    if matter_id:
        conn.execute("""
            UPDATE legalclaw_matter
            SET trust_balance = CAST(
                CAST(trust_balance AS REAL) + ? AS TEXT
            ), updated_at = ?
            WHERE id = ?
        """, (float(amount), now, matter_id))

    audit(conn, "legalclaw_trust_transaction", txn_id, "legal-deposit-trust", args.company_id)
    conn.commit()
    ok({
        "id": txn_id, "trust_account_id": ta_id, "transaction_type": "deposit",
        "amount": str(amount), "new_balance": str(new_balance),
        "matter_id": matter_id,
    })


# ---------------------------------------------------------------------------
# 5. disburse-trust
# ---------------------------------------------------------------------------
def disburse_trust(conn, args):
    ta_id = getattr(args, "trust_account_id", None)
    ta_row = _validate_trust_account(conn, ta_id)
    _validate_company(conn, args.company_id)

    amount_raw = getattr(args, "amount", None)
    if not amount_raw:
        err("--amount is required")
    amount = to_decimal(amount_raw)
    if amount <= 0:
        err("Disbursement amount must be greater than 0")

    current_balance = to_decimal(ta_row["current_balance"])
    if amount > current_balance:
        err(f"Insufficient trust balance: {current_balance} available, {amount} requested")

    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        if not conn.execute("SELECT id FROM legalclaw_matter WHERE id = ?", (matter_id,)).fetchone():
            err(f"Matter {matter_id} not found")

    payee = getattr(args, "payee", None)
    if not payee:
        err("--payee is required for disbursements")

    transaction_date = getattr(args, "transaction_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    txn_id = str(uuid.uuid4())
    now = _now_iso()
    conn.execute("""
        INSERT INTO legalclaw_trust_transaction (
            id, trust_account_id, matter_id, transaction_type, transaction_date,
            amount, reference, payee, description, company_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        txn_id, ta_id, matter_id, "disbursement", transaction_date,
        str(amount),
        getattr(args, "reference", None),
        payee,
        getattr(args, "trust_description", None),
        args.company_id, now,
    ))

    new_balance = current_balance - amount
    conn.execute("""
        UPDATE legalclaw_trust_account SET current_balance = ?, updated_at = ? WHERE id = ?
    """, (str(new_balance), now, ta_id))

    if matter_id:
        conn.execute("""
            UPDATE legalclaw_matter
            SET trust_balance = CAST(
                CAST(trust_balance AS REAL) - ? AS TEXT
            ), updated_at = ?
            WHERE id = ?
        """, (float(amount), now, matter_id))

    audit(conn, "legalclaw_trust_transaction", txn_id, "legal-disburse-trust", args.company_id)
    conn.commit()
    ok({
        "id": txn_id, "trust_account_id": ta_id, "transaction_type": "disbursement",
        "amount": str(amount), "payee": payee, "new_balance": str(new_balance),
        "matter_id": matter_id,
    })


# ---------------------------------------------------------------------------
# 6. transfer-trust
# ---------------------------------------------------------------------------
def transfer_trust(conn, args):
    from_id = getattr(args, "trust_account_id", None)
    from_row = _validate_trust_account(conn, from_id)
    _validate_company(conn, args.company_id)

    to_id = getattr(args, "to_trust_account_id", None)
    if not to_id:
        err("--to-trust-account-id is required")
    to_row = conn.execute("SELECT * FROM legalclaw_trust_account WHERE id = ?", (to_id,)).fetchone()
    if not to_row:
        err(f"Destination trust account {to_id} not found")

    amount_raw = getattr(args, "amount", None)
    if not amount_raw:
        err("--amount is required")
    amount = to_decimal(amount_raw)
    if amount <= 0:
        err("Transfer amount must be greater than 0")

    from_balance = to_decimal(from_row["current_balance"])
    if amount > from_balance:
        err(f"Insufficient balance in source account: {from_balance} available, {amount} requested")

    transaction_date = getattr(args, "transaction_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = _now_iso()

    # Debit source
    debit_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO legalclaw_trust_transaction (
            id, trust_account_id, matter_id, transaction_type, transaction_date,
            amount, reference, payee, description, company_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        debit_id, from_id, None, "transfer", transaction_date,
        str(amount),
        getattr(args, "reference", None),
        None,
        f"Transfer to {to_row['name']}",
        args.company_id, now,
    ))

    # Credit destination
    credit_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO legalclaw_trust_transaction (
            id, trust_account_id, matter_id, transaction_type, transaction_date,
            amount, reference, payee, description, company_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        credit_id, to_id, None, "transfer", transaction_date,
        str(amount),
        getattr(args, "reference", None),
        None,
        f"Transfer from {from_row['name']}",
        args.company_id, now,
    ))

    new_from = from_balance - amount
    new_to = to_decimal(to_row["current_balance"]) + amount

    conn.execute("UPDATE legalclaw_trust_account SET current_balance = ?, updated_at = ? WHERE id = ?",
                 (str(new_from), now, from_id))
    conn.execute("UPDATE legalclaw_trust_account SET current_balance = ?, updated_at = ? WHERE id = ?",
                 (str(new_to), now, to_id))

    audit(conn, "legalclaw_trust_account", from_id, "legal-transfer-trust", args.company_id)
    conn.commit()
    ok({
        "from_account_id": from_id, "to_account_id": to_id,
        "amount": str(amount),
        "from_new_balance": str(new_from), "to_new_balance": str(new_to),
    })


# ---------------------------------------------------------------------------
# 7. list-trust-transactions
# ---------------------------------------------------------------------------
def list_trust_transactions(conn, args):
    sql = "SELECT * FROM legalclaw_trust_transaction WHERE 1=1"
    params = []
    ta_id = getattr(args, "trust_account_id", None)
    if ta_id:
        sql += " AND trust_account_id = ?"
        params.append(ta_id)
    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        sql += " AND matter_id = ?"
        params.append(matter_id)
    transaction_type = getattr(args, "transaction_type", None)
    if transaction_type:
        sql += " AND transaction_type = ?"
        params.append(transaction_type)
    sql += " ORDER BY transaction_date DESC, created_at DESC LIMIT ? OFFSET ?"
    params.extend([args.limit, args.offset])
    rows = conn.execute(sql, params).fetchall()
    ok({"transactions": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 8. trust-reconciliation
# ---------------------------------------------------------------------------
def trust_reconciliation(conn, args):
    ta_id = getattr(args, "trust_account_id", None)
    ta_row = _validate_trust_account(conn, ta_id)

    book_balance = to_decimal(ta_row["current_balance"])

    # Calculate balance from transactions
    txn_rows = conn.execute("""
        SELECT transaction_type, CAST(amount AS REAL) as amt
        FROM legalclaw_trust_transaction WHERE trust_account_id = ?
    """, (ta_id,)).fetchall()

    deposits = sum(Decimal(str(r["amt"])) for r in txn_rows if r["transaction_type"] in ("deposit", "interest"))
    withdrawals = sum(Decimal(str(r["amt"])) for r in txn_rows if r["transaction_type"] in ("disbursement", "fee"))

    # For transfers: outgoing = debit, incoming = credit (both stored as "transfer")
    # We track them by description convention; for simplicity, use net of deposits - withdrawals
    calc_balance = deposits - withdrawals

    # Per-matter breakdown
    matter_rows = conn.execute("""
        SELECT m.id, m.title,
               SUM(CASE WHEN t.transaction_type IN ('deposit','interest')
                   THEN CAST(t.amount AS REAL) ELSE 0 END) as matter_deposits,
               SUM(CASE WHEN t.transaction_type IN ('disbursement','fee')
                   THEN CAST(t.amount AS REAL) ELSE 0 END) as matter_withdrawals
        FROM legalclaw_trust_transaction t
        LEFT JOIN legalclaw_matter m ON t.matter_id = m.id
        WHERE t.trust_account_id = ? AND t.matter_id IS NOT NULL
        GROUP BY m.id
    """, (ta_id,)).fetchall()

    client_ledger = []
    for mr in matter_rows:
        dep = Decimal(str(mr["matter_deposits"] or 0))
        wd = Decimal(str(mr["matter_withdrawals"] or 0))
        client_ledger.append({
            "matter_id": mr["id"],
            "title": mr["title"],
            "deposits": str(dep),
            "withdrawals": str(wd),
            "balance": str(dep - wd),
        })

    client_total = sum(to_decimal(c["balance"]) for c in client_ledger)
    is_reconciled = (book_balance == calc_balance)

    ok({
        "trust_account_id": ta_id,
        "account_name": ta_row["name"],
        "book_balance": str(book_balance),
        "calculated_balance": str(calc_balance),
        "client_ledger_total": str(client_total),
        "is_reconciled": is_reconciled,
        "total_deposits": str(deposits),
        "total_withdrawals": str(withdrawals),
        "client_ledger": client_ledger,
    })


# ---------------------------------------------------------------------------
# 9. trust-balance-report
# ---------------------------------------------------------------------------
def trust_balance_report(conn, args):
    _validate_company(conn, args.company_id)

    rows = conn.execute("""
        SELECT m.id as matter_id, m.title, m.client_id, c.name as client_name,
               m.trust_balance
        FROM legalclaw_matter m
        JOIN legalclaw_client c ON m.client_id = c.id
        WHERE m.company_id = ? AND CAST(m.trust_balance AS REAL) != 0
        ORDER BY c.name, m.title
    """, (args.company_id,)).fetchall()

    total = sum(to_decimal(r["trust_balance"]) for r in rows)
    ok({
        "matters": [row_to_dict(r) for r in rows],
        "count": len(rows),
        "total_trust_balance": str(total),
    })


# ---------------------------------------------------------------------------
# 10. trust-interest-distribution
# ---------------------------------------------------------------------------
def trust_interest_distribution(conn, args):
    ta_id = getattr(args, "trust_account_id", None)
    ta_row = _validate_trust_account(conn, ta_id)
    _validate_company(conn, args.company_id)

    amount_raw = getattr(args, "amount", None)
    if not amount_raw:
        err("--amount is required (interest amount)")
    amount = to_decimal(amount_raw)
    if amount <= 0:
        err("Interest amount must be greater than 0")

    transaction_date = getattr(args, "transaction_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    txn_id = str(uuid.uuid4())
    now = _now_iso()
    conn.execute("""
        INSERT INTO legalclaw_trust_transaction (
            id, trust_account_id, matter_id, transaction_type, transaction_date,
            amount, reference, payee, description, company_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        txn_id, ta_id, None, "interest", transaction_date,
        str(amount), getattr(args, "reference", None), None,
        "Interest distribution",
        args.company_id, now,
    ))

    new_balance = to_decimal(ta_row["current_balance"]) + amount
    conn.execute("""
        UPDATE legalclaw_trust_account SET current_balance = ?, updated_at = ? WHERE id = ?
    """, (str(new_balance), now, ta_id))

    audit(conn, "legalclaw_trust_transaction", txn_id, "legal-trust-interest-distribution", args.company_id)
    conn.commit()
    ok({
        "id": txn_id, "trust_account_id": ta_id, "transaction_type": "interest",
        "amount": str(amount), "new_balance": str(new_balance),
    })


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------
ACTIONS = {
    "legal-add-trust-account": add_trust_account,
    "legal-get-trust-account": get_trust_account,
    "legal-list-trust-accounts": list_trust_accounts,
    "legal-deposit-trust": deposit_trust,
    "legal-disburse-trust": disburse_trust,
    "legal-transfer-trust": transfer_trust,
    "legal-list-trust-transactions": list_trust_transactions,
    "legal-trust-reconciliation": trust_reconciliation,
    "legal-trust-balance-report": trust_balance_report,
    "legal-trust-interest-distribution": trust_interest_distribution,
}
