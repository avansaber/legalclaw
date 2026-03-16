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
    from erpclaw_lib.gl_posting import insert_gl_entries
    from erpclaw_lib.query import (
        Q, P, Table, Field, fn, Order, LiteralValue,
        insert_row, update_row, dynamic_update,
    )

    ENTITY_PREFIXES.setdefault("legalclaw_trust_account", "LTRS-")
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_ACCOUNT_TYPES = ("iolta", "escrow", "retainer", "other")
VALID_TRANSACTION_TYPES = ("deposit", "disbursement", "transfer", "interest", "fee")

# ── Table aliases ──
_company = Table("company")
_account = Table("account")
_ta = Table("legalclaw_trust_account")
_txn = Table("legalclaw_trust_transaction")
_matter = Table("legalclaw_matter")
_ext = Table("legalclaw_client_ext")
_cust = Table("customer")
_cc = Table("cost_center")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    q = Q.from_(_company).select(_company.id).where(_company.id == P())
    if not conn.execute(q.get_sql(), (company_id,)).fetchone():
        err(f"Company {company_id} not found")


def _validate_trust_account(conn, trust_account_id):
    if not trust_account_id:
        err("--trust-account-id is required")
    q = Q.from_(_ta).select(_ta.star).where(_ta.id == P())
    row = conn.execute(q.get_sql(), (trust_account_id,)).fetchone()
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

    # GL account linkage (optional — enables double-entry trust posting)
    gl_account_id = getattr(args, "gl_account_id", None)
    trust_liability_account_id = getattr(args, "trust_liability_account_id", None)
    interest_income_account_id = getattr(args, "interest_income_account_id", None)

    # Validate referenced GL accounts exist
    for acct_id, label in [
        (gl_account_id, "--gl-account-id"),
        (trust_liability_account_id, "--trust-liability-account-id"),
        (interest_income_account_id, "--interest-income-account-id"),
    ]:
        if acct_id:
            q = Q.from_(_account).select(_account.id).where(_account.id == P())
            if not conn.execute(q.get_sql(), (acct_id,)).fetchone():
                err(f"{label} account {acct_id} not found in chart of accounts")

    ta_id = str(uuid.uuid4())
    ns = get_next_name(conn, "legalclaw_trust_account", company_id=args.company_id)
    now = _now_iso()

    sql, _ = insert_row("legalclaw_trust_account", {"id": P(), "naming_series": P(), "name": P(), "bank_name": P(), "account_number": P(), "account_type": P(), "current_balance": P(), "gl_account_id": P(), "trust_liability_account_id": P(), "interest_income_account_id": P(), "company_id": P(), "created_at": P(), "updated_at": P()})
    conn.execute(sql, (
        ta_id, ns, name,
        getattr(args, "bank_name", None),
        getattr(args, "account_number", None),
        account_type, "0",
        gl_account_id, trust_liability_account_id, interest_income_account_id,
        args.company_id, now, now,
    ))
    audit(conn, "legalclaw_trust_account", ta_id, "legal-add-trust-account", args.company_id)
    conn.commit()
    ok({"id": ta_id, "naming_series": ns, "name": name, "account_type": account_type,
        "current_balance": "0",
        "gl_account_id": gl_account_id,
        "trust_liability_account_id": trust_liability_account_id,
        "interest_income_account_id": interest_income_account_id})


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
    conditions = []
    params = []
    if args.company_id:
        conditions.append(_ta.company_id == P())
        params.append(args.company_id)
    account_type = getattr(args, "account_type", None)
    if account_type:
        conditions.append(_ta.account_type == P())
        params.append(account_type)

    q = Q.from_(_ta).select(_ta.star)
    for cond in conditions:
        q = q.where(cond)
    q = q.orderby(_ta.name, order=Order.asc).limit(P()).offset(P())

    rows = conn.execute(q.get_sql(), params + [args.limit, args.offset]).fetchall()
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
        mq = Q.from_(_matter).select(_matter.id).where(_matter.id == P())
        if not conn.execute(mq.get_sql(), (matter_id,)).fetchone():
            err(f"Matter {matter_id} not found")

    transaction_date = getattr(args, "transaction_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    txn_id = str(uuid.uuid4())
    now = _now_iso()
    sql, _ = insert_row("legalclaw_trust_transaction", {"id": P(), "trust_account_id": P(), "matter_id": P(), "transaction_type": P(), "transaction_date": P(), "amount": P(), "reference": P(), "payee": P(), "description": P(), "company_id": P(), "created_at": P()})
    conn.execute(sql, (
        txn_id, ta_id, matter_id, "deposit", transaction_date,
        str(amount),
        getattr(args, "reference", None),
        getattr(args, "payee", None),
        getattr(args, "trust_description", None),
        args.company_id, now,
    ))

    # Update trust account balance
    new_balance = to_decimal(ta_row["current_balance"]) + amount
    sql_u, params_u = dynamic_update("legalclaw_trust_account",
        {"current_balance": str(new_balance), "updated_at": now},
        where={"id": ta_id})
    conn.execute(sql_u, params_u)

    # Update matter trust_balance if matter specified (Decimal math in Python, not SQL CAST)
    if matter_id:
        mb_q = Q.from_(_matter).select(_matter.trust_balance).where(_matter.id == P())
        matter_row = conn.execute(mb_q.get_sql(), (matter_id,)).fetchone()
        current_matter_balance = to_decimal(matter_row["trust_balance"] or "0")
        new_matter_balance = current_matter_balance + amount
        sql_m, params_m = dynamic_update("legalclaw_matter",
            {"trust_balance": str(new_matter_balance), "updated_at": now},
            where={"id": matter_id})
        conn.execute(sql_m, params_m)

    # GL posting: DR Trust Bank (asset), CR Trust Liability (liability)
    gl_entry_ids = []
    if ta_row["gl_account_id"] and ta_row["trust_liability_account_id"]:
        entries = [
            {"account_id": ta_row["gl_account_id"], "debit": str(amount), "credit": "0"},
            {"account_id": ta_row["trust_liability_account_id"], "debit": "0", "credit": str(amount)},
        ]
        gl_entry_ids = insert_gl_entries(
            conn, entries, voucher_type="Trust Deposit",
            voucher_id=txn_id, posting_date=transaction_date,
            company_id=args.company_id,
        )
        sql_gl, params_gl = dynamic_update("legalclaw_trust_transaction",
            {"gl_entry_ids": ",".join(gl_entry_ids)},
            where={"id": txn_id})
        conn.execute(sql_gl, params_gl)

    audit(conn, "legalclaw_trust_transaction", txn_id, "legal-deposit-trust", args.company_id)
    conn.commit()
    result = {
        "id": txn_id, "trust_account_id": ta_id, "transaction_type": "deposit",
        "amount": str(amount), "new_balance": str(new_balance),
        "matter_id": matter_id,
    }
    if gl_entry_ids:
        result["gl_entry_ids"] = gl_entry_ids
    ok(result)


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
        mq = Q.from_(_matter).select(_matter.id).where(_matter.id == P())
        if not conn.execute(mq.get_sql(), (matter_id,)).fetchone():
            err(f"Matter {matter_id} not found")

    payee = getattr(args, "payee", None)
    if not payee:
        err("--payee is required for disbursements")

    transaction_date = getattr(args, "transaction_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    txn_id = str(uuid.uuid4())
    now = _now_iso()
    sql, _ = insert_row("legalclaw_trust_transaction", {"id": P(), "trust_account_id": P(), "matter_id": P(), "transaction_type": P(), "transaction_date": P(), "amount": P(), "reference": P(), "payee": P(), "description": P(), "company_id": P(), "created_at": P()})
    conn.execute(sql, (
        txn_id, ta_id, matter_id, "disbursement", transaction_date,
        str(amount),
        getattr(args, "reference", None),
        payee,
        getattr(args, "trust_description", None),
        args.company_id, now,
    ))

    new_balance = current_balance - amount
    sql_u, params_u = dynamic_update("legalclaw_trust_account",
        {"current_balance": str(new_balance), "updated_at": now},
        where={"id": ta_id})
    conn.execute(sql_u, params_u)

    # Update matter trust_balance if matter specified (Decimal math in Python, not SQL CAST)
    if matter_id:
        mb_q = Q.from_(_matter).select(_matter.trust_balance).where(_matter.id == P())
        matter_row = conn.execute(mb_q.get_sql(), (matter_id,)).fetchone()
        current_matter_balance = to_decimal(matter_row["trust_balance"] or "0")
        new_matter_balance = current_matter_balance - amount
        sql_m, params_m = dynamic_update("legalclaw_matter",
            {"trust_balance": str(new_matter_balance), "updated_at": now},
            where={"id": matter_id})
        conn.execute(sql_m, params_m)

    # GL posting: DR Trust Liability, CR Trust Bank (reverse of deposit)
    gl_entry_ids = []
    if ta_row["gl_account_id"] and ta_row["trust_liability_account_id"]:
        entries = [
            {"account_id": ta_row["trust_liability_account_id"], "debit": str(amount), "credit": "0"},
            {"account_id": ta_row["gl_account_id"], "debit": "0", "credit": str(amount)},
        ]
        gl_entry_ids = insert_gl_entries(
            conn, entries, voucher_type="Trust Disbursement",
            voucher_id=txn_id, posting_date=transaction_date,
            company_id=args.company_id,
        )
        sql_gl, params_gl = dynamic_update("legalclaw_trust_transaction",
            {"gl_entry_ids": ",".join(gl_entry_ids)},
            where={"id": txn_id})
        conn.execute(sql_gl, params_gl)

    audit(conn, "legalclaw_trust_transaction", txn_id, "legal-disburse-trust", args.company_id)
    conn.commit()
    result = {
        "id": txn_id, "trust_account_id": ta_id, "transaction_type": "disbursement",
        "amount": str(amount), "payee": payee, "new_balance": str(new_balance),
        "matter_id": matter_id,
    }
    if gl_entry_ids:
        result["gl_entry_ids"] = gl_entry_ids
    ok(result)


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
    to_q = Q.from_(_ta).select(_ta.star).where(_ta.id == P())
    to_row = conn.execute(to_q.get_sql(), (to_id,)).fetchone()
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
    sql, _ = insert_row("legalclaw_trust_transaction", {"id": P(), "trust_account_id": P(), "matter_id": P(), "transaction_type": P(), "transaction_date": P(), "amount": P(), "reference": P(), "payee": P(), "description": P(), "company_id": P(), "created_at": P()})
    conn.execute(sql, (
        debit_id, from_id, None, "transfer", transaction_date,
        str(amount),
        getattr(args, "reference", None),
        None,
        f"Transfer to {to_row['name']}",
        args.company_id, now,
    ))

    # Credit destination
    credit_id = str(uuid.uuid4())
    sql, _ = insert_row("legalclaw_trust_transaction", {"id": P(), "trust_account_id": P(), "matter_id": P(), "transaction_type": P(), "transaction_date": P(), "amount": P(), "reference": P(), "payee": P(), "description": P(), "company_id": P(), "created_at": P()})
    conn.execute(sql, (
        credit_id, to_id, None, "transfer", transaction_date,
        str(amount),
        getattr(args, "reference", None),
        None,
        f"Transfer from {from_row['name']}",
        args.company_id, now,
    ))

    new_from = from_balance - amount
    new_to = to_decimal(to_row["current_balance"]) + amount

    sql_from, p_from = dynamic_update("legalclaw_trust_account",
        {"current_balance": str(new_from), "updated_at": now},
        where={"id": from_id})
    conn.execute(sql_from, p_from)

    sql_to, p_to = dynamic_update("legalclaw_trust_account",
        {"current_balance": str(new_to), "updated_at": now},
        where={"id": to_id})
    conn.execute(sql_to, p_to)

    # GL posting: DR Destination Trust Bank, CR Source Trust Bank (no liability change)
    gl_entry_ids = []
    if from_row["gl_account_id"] and to_row["gl_account_id"]:
        entries = [
            {"account_id": to_row["gl_account_id"], "debit": str(amount), "credit": "0"},
            {"account_id": from_row["gl_account_id"], "debit": "0", "credit": str(amount)},
        ]
        # Use debit_id as the voucher — it's the source-side transaction record
        gl_entry_ids = insert_gl_entries(
            conn, entries, voucher_type="Trust Transfer",
            voucher_id=debit_id, posting_date=transaction_date,
            company_id=args.company_id,
        )
        gl_ids_str = ",".join(gl_entry_ids)
        sql_g1, p_g1 = dynamic_update("legalclaw_trust_transaction",
            {"gl_entry_ids": gl_ids_str}, where={"id": debit_id})
        conn.execute(sql_g1, p_g1)
        sql_g2, p_g2 = dynamic_update("legalclaw_trust_transaction",
            {"gl_entry_ids": gl_ids_str}, where={"id": credit_id})
        conn.execute(sql_g2, p_g2)

    audit(conn, "legalclaw_trust_account", from_id, "legal-transfer-trust", args.company_id)
    conn.commit()
    result = {
        "from_account_id": from_id, "to_account_id": to_id,
        "amount": str(amount),
        "from_new_balance": str(new_from), "to_new_balance": str(new_to),
    }
    if gl_entry_ids:
        result["gl_entry_ids"] = gl_entry_ids
    ok(result)


# ---------------------------------------------------------------------------
# 7. list-trust-transactions
# ---------------------------------------------------------------------------
def list_trust_transactions(conn, args):
    conditions = []
    params = []
    ta_id = getattr(args, "trust_account_id", None)
    if ta_id:
        conditions.append(_txn.trust_account_id == P())
        params.append(ta_id)
    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        conditions.append(_txn.matter_id == P())
        params.append(matter_id)
    transaction_type = getattr(args, "transaction_type", None)
    if transaction_type:
        conditions.append(_txn.transaction_type == P())
        params.append(transaction_type)

    q = Q.from_(_txn).select(_txn.star)
    for cond in conditions:
        q = q.where(cond)
    q = q.orderby(_txn.transaction_date, order=Order.desc).orderby(_txn.created_at, order=Order.desc).limit(P()).offset(P())

    rows = conn.execute(q.get_sql(), params + [args.limit, args.offset]).fetchall()
    ok({"transactions": [row_to_dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# 8. trust-reconciliation
# ---------------------------------------------------------------------------
def trust_reconciliation(conn, args):
    ta_id = getattr(args, "trust_account_id", None)
    ta_row = _validate_trust_account(conn, ta_id)

    book_balance = to_decimal(ta_row["current_balance"])

    # Calculate balance from transactions (TEXT amounts, Decimal math in Python)
    txn_q = (
        Q.from_(_txn)
        .select(_txn.transaction_type, _txn.amount)
        .where(_txn.trust_account_id == P())
    )
    txn_rows = conn.execute(txn_q.get_sql(), (ta_id,)).fetchall()

    deposits = sum(to_decimal(r["amount"]) for r in txn_rows if r["transaction_type"] in ("deposit", "interest"))
    withdrawals = sum(to_decimal(r["amount"]) for r in txn_rows if r["transaction_type"] in ("disbursement", "fee"))

    # For transfers: outgoing = debit, incoming = credit (both stored as "transfer")
    # We track them by description convention; for simplicity, use net of deposits - withdrawals
    calc_balance = deposits - withdrawals

    # Per-matter breakdown (fetch raw TEXT amounts, aggregate in Python)
    matter_txn_q = (
        Q.from_(_txn)
        .left_join(_matter).on(_txn.matter_id == _matter.id)
        .select(
            _txn.matter_id, _matter.id.as_("mid"), _matter.title,
            _txn.transaction_type, _txn.amount,
        )
        .where(_txn.trust_account_id == P())
        .where(_txn.matter_id.isnotnull())
    )
    matter_txn_rows = conn.execute(matter_txn_q.get_sql(), (ta_id,)).fetchall()

    # Aggregate per-matter in Python with Decimal
    matter_data = {}
    for row in matter_txn_rows:
        mid = row["mid"]
        if mid not in matter_data:
            matter_data[mid] = {"title": row["title"], "deposits": Decimal("0"), "withdrawals": Decimal("0")}
        amt = to_decimal(row["amount"])
        if row["transaction_type"] in ("deposit", "interest"):
            matter_data[mid]["deposits"] += amt
        elif row["transaction_type"] in ("disbursement", "fee"):
            matter_data[mid]["withdrawals"] += amt

    client_ledger = []
    for mid, md in matter_data.items():
        client_ledger.append({
            "matter_id": mid,
            "title": md["title"],
            "deposits": str(md["deposits"]),
            "withdrawals": str(md["withdrawals"]),
            "balance": str(md["deposits"] - md["withdrawals"]),
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

    # Fetch all matters with trust balances (filter non-zero in Python with Decimal)
    q = (
        Q.from_(_matter)
        .join(_ext).on(_matter.client_id == _ext.id)
        .join(_cust).on(_ext.customer_id == _cust.id)
        .select(
            _matter.id.as_("matter_id"), _matter.title, _matter.client_id,
            _cust.name.as_("client_name"), _matter.trust_balance,
        )
        .where(_matter.company_id == P())
        .orderby(_cust.name).orderby(_matter.title)
    )
    all_rows = conn.execute(q.get_sql(), (args.company_id,)).fetchall()

    # Filter out zero-balance matters using Decimal comparison (not SQL CAST)
    rows = [r for r in all_rows if to_decimal(r["trust_balance"] or "0") != Decimal("0")]

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
    sql, _ = insert_row("legalclaw_trust_transaction", {"id": P(), "trust_account_id": P(), "matter_id": P(), "transaction_type": P(), "transaction_date": P(), "amount": P(), "reference": P(), "payee": P(), "description": P(), "company_id": P(), "created_at": P()})
    conn.execute(sql, (
        txn_id, ta_id, None, "interest", transaction_date,
        str(amount), getattr(args, "reference", None), None,
        "Interest distribution",
        args.company_id, now,
    ))

    new_balance = to_decimal(ta_row["current_balance"]) + amount
    sql_u, params_u = dynamic_update("legalclaw_trust_account",
        {"current_balance": str(new_balance), "updated_at": now},
        where={"id": ta_id})
    conn.execute(sql_u, params_u)

    # GL posting: DR Trust Bank (asset), CR Interest Income (revenue)
    # For IOLTA, interest goes to state bar foundation, not the firm.
    # The interest_income_account_id on the trust account controls where it posts.
    gl_entry_ids = []
    if ta_row["gl_account_id"] and ta_row["interest_income_account_id"]:
        # Interest income is a P&L account — needs cost_center_id
        cost_center_id = getattr(args, "cost_center_id", None)
        if not cost_center_id:
            # Try to find a default cost center for this company
            cc_q = (
                Q.from_(_cc)
                .select(_cc.id)
                .where(_cc.company_id == P())
                .where(_cc.is_group == 0)
                .limit(1)
            )
            cc_row = conn.execute(cc_q.get_sql(), (args.company_id,)).fetchone()
            if cc_row:
                cost_center_id = cc_row["id"]
        entries = [
            {"account_id": ta_row["gl_account_id"], "debit": str(amount), "credit": "0"},
            {"account_id": ta_row["interest_income_account_id"], "debit": "0", "credit": str(amount),
             "cost_center_id": cost_center_id},
        ]
        gl_entry_ids = insert_gl_entries(
            conn, entries, voucher_type="Trust Interest",
            voucher_id=txn_id, posting_date=transaction_date,
            company_id=args.company_id,
        )
        sql_gl, params_gl = dynamic_update("legalclaw_trust_transaction",
            {"gl_entry_ids": ",".join(gl_entry_ids)},
            where={"id": txn_id})
        conn.execute(sql_gl, params_gl)

    audit(conn, "legalclaw_trust_transaction", txn_id, "legal-trust-interest-distribution", args.company_id)
    conn.commit()
    result = {
        "id": txn_id, "trust_account_id": ta_id, "transaction_type": "interest",
        "amount": str(amount), "new_balance": str(new_balance),
    }
    if gl_entry_ids:
        result["gl_entry_ids"] = gl_entry_ids
    ok(result)


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
