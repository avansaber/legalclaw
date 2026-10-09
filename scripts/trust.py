"""LegalClaw -- trust accounting domain module

Actions for IOLTA/escrow trust accounts, transactions, reconciliation (2 tables, 10 actions).
Imported by db_query.py (unified router).
"""
import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

try:
    import importlib.util
    if importlib.util.find_spec("erpclaw_lib") is None:
        sys.path.insert(0, os.path.join(os.path.expanduser(os.environ.get("ERPCLAW_HOME", "~/.openclaw/erpclaw")), "lib"))
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

SKILL = "legalclaw"

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


def _validate_trust_account(conn, trust_account_id, company_id=None):
    if not trust_account_id:
        err("--trust-account-id is required")
    q = Q.from_(_ta).select(_ta.star).where(_ta.id == P())
    params = [trust_account_id]
    if company_id is not None:
        q = q.where(_ta.company_id == P())
        params.append(company_id)
    row = conn.execute(q.get_sql(), params).fetchone()
    if not row:
        if company_id is not None:
            err("Trust account not found for this company")
        err(f"Trust account {trust_account_id} not found")
    return row


def _validate_trust_matter(conn, matter_id, company_id):
    if not matter_id:
        return None
    query = Q.from_(_matter).select(_matter.id, _matter.trust_balance)
    query = query.where(_matter.id == P()).where(_matter.company_id == P())
    row = conn.execute(query.get_sql(), (matter_id, company_id)).fetchone()
    if not row:
        err("Matter not found for this company")
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
    audit(conn, SKILL, "legal-add-trust-account", "legalclaw_trust_account", ta_id)
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
    _validate_company(conn, args.company_id)
    ta_row = _validate_trust_account(conn, ta_id, args.company_id)

    amount_raw = getattr(args, "amount", None)
    if not amount_raw:
        err("--amount is required")
    amount = round_currency(to_decimal(amount_raw))
    if amount <= 0:
        err("Deposit amount must be greater than 0")

    matter_id = getattr(args, "matter_id", None)
    _validate_trust_matter(conn, matter_id, args.company_id)

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

    audit(conn, SKILL, "legal-deposit-trust", "legalclaw_trust_transaction", txn_id)
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
# Shared write path: one trust disbursement line
# ---------------------------------------------------------------------------
def write_trust_disbursement(conn, trust_account_id, amount, payee, matter_id,
                             transaction_date, reference, description, company_id):
    """Write one trust disbursement line inside the caller's transaction.

    The shared write half of legal-disburse-trust, reused by
    legal-disburse-settlement so both actions move trust money the same way.
    Re-reads the trust account row itself, so a second call in the same
    transaction sees the first call's balance. Inserts the disbursement
    transaction, lowers the trust account balance and the matter trust balance
    (when a matter is given), and posts the Trust Disbursement GL legs when
    the account is GL-linked, exactly as legal-disburse-trust always has.

    Returns {"id", "new_balance", "gl_entry_ids"}. Uses the same cents amount
    as the public trust actions and refuses a nonpositive rounded amount.
    Performs no audit, commit or ok/err response; lets ValueError propagate
    so the caller can roll the whole transaction back.
    """
    ta_q = Q.from_(_ta).select(_ta.star).where(_ta.id == P()).where(_ta.company_id == P())
    ta_row = conn.execute(ta_q.get_sql(), (trust_account_id, company_id)).fetchone()
    if not ta_row:
        raise ValueError("Trust account not found for this company")
    matter_row = None
    if matter_id:
        matter_q = Q.from_(_matter).select(_matter.trust_balance)
        matter_q = matter_q.where(_matter.id == P()).where(_matter.company_id == P())
        matter_row = conn.execute(matter_q.get_sql(), (matter_id, company_id)).fetchone()
        if not matter_row:
            raise ValueError("Matter not found for this company")
    amount = round_currency(to_decimal(amount))
    if amount <= 0:
        raise ValueError("Disbursement amount must be greater than 0 after rounding to cents")
    current_balance = to_decimal(ta_row["current_balance"])

    txn_id = str(uuid.uuid4())
    now = _now_iso()
    sql, _ = insert_row("legalclaw_trust_transaction", {"id": P(), "trust_account_id": P(), "matter_id": P(), "transaction_type": P(), "transaction_date": P(), "amount": P(), "reference": P(), "payee": P(), "description": P(), "company_id": P(), "created_at": P()})
    conn.execute(sql, (
        txn_id, trust_account_id, matter_id, "disbursement", transaction_date,
        str(amount),
        reference,
        payee,
        description,
        company_id, now,
    ))

    new_balance = current_balance - amount
    sql_u, params_u = dynamic_update("legalclaw_trust_account",
        {"current_balance": str(new_balance), "updated_at": now},
        where={"id": trust_account_id})
    conn.execute(sql_u, params_u)

    # Update matter trust_balance if matter specified (Decimal math in Python, not SQL CAST)
    if matter_id:
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
            company_id=company_id,
        )
        sql_gl, params_gl = dynamic_update("legalclaw_trust_transaction",
            {"gl_entry_ids": ",".join(gl_entry_ids)},
            where={"id": txn_id})
        conn.execute(sql_gl, params_gl)

    return {"id": txn_id, "new_balance": str(new_balance), "gl_entry_ids": gl_entry_ids}


# ---------------------------------------------------------------------------
# 5. disburse-trust
# ---------------------------------------------------------------------------
def disburse_trust(conn, args):
    ta_id = getattr(args, "trust_account_id", None)
    _validate_company(conn, args.company_id)
    ta_row = _validate_trust_account(conn, ta_id, args.company_id)

    amount_raw = getattr(args, "amount", None)
    if not amount_raw:
        err("--amount is required")
    amount = round_currency(to_decimal(amount_raw))
    if amount <= 0:
        err("Disbursement amount must be greater than 0")

    current_balance = to_decimal(ta_row["current_balance"])
    if amount > current_balance:
        err(f"Insufficient trust balance: {current_balance} available, {amount} requested")

    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        matter_row = _validate_trust_matter(conn, matter_id, args.company_id)
        matter_balance_text = matter_row["trust_balance"] if matter_row["trust_balance"] is not None else "0"
        matter_balance = to_decimal(matter_balance_text)
        if amount > matter_balance:
            err(f"Insufficient trust balance for matter {matter_id}: {matter_balance_text} available, {amount} requested")

    payee = getattr(args, "payee", None)
    if not payee:
        err("--payee is required for disbursements")

    transaction_date = getattr(args, "transaction_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    written = write_trust_disbursement(
        conn, ta_id, amount, payee, matter_id, transaction_date,
        getattr(args, "reference", None),
        getattr(args, "trust_description", None),
        args.company_id,
    )
    txn_id = written["id"]
    new_balance = written["new_balance"]
    gl_entry_ids = written["gl_entry_ids"]

    audit(conn, SKILL, "legal-disburse-trust", "legalclaw_trust_transaction", txn_id)
    conn.commit()
    result = {
        "id": txn_id, "trust_account_id": ta_id, "transaction_type": "disbursement",
        "amount": str(amount), "payee": payee, "new_balance": new_balance,
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
    if from_row["company_id"] != args.company_id:
        err("Source trust account belongs to another company")

    to_id = getattr(args, "to_trust_account_id", None)
    if not to_id:
        err("--to-trust-account-id is required")
    if to_id == from_id:
        err("Source and destination trust accounts must differ")
    to_q = Q.from_(_ta).select(_ta.star).where(_ta.id == P())
    to_row = conn.execute(to_q.get_sql(), (to_id,)).fetchone()
    if not to_row:
        err(f"Destination trust account {to_id} not found")
    if to_row["company_id"] != args.company_id:
        err("Destination trust account belongs to another company")

    amount_raw = getattr(args, "amount", None)
    if not amount_raw:
        err("--amount is required")
    amount = round_currency(to_decimal(amount_raw))
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

    audit(conn, SKILL, "legal-transfer-trust", "legalclaw_trust_account", from_id)
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
def _reconciliation_direction(row):
    transaction_type = row["transaction_type"]
    if transaction_type in ("deposit", "interest"):
        return "in"
    if transaction_type in ("disbursement", "fee"):
        return "out"
    if transaction_type == "transfer":
        description = row["description"] or ""
        if description.startswith("Transfer to "):
            return "out"
        if description.startswith("Transfer from "):
            return "in"
        err(
            f"Cannot reconcile transfer {row['id']}: "
            "stored direction is ambiguous"
        )
    err(
        f"Cannot reconcile transaction {row['id']}: "
        f"unsupported type {transaction_type!r}"
    )


def trust_reconciliation(conn, args):
    ta_id = getattr(args, "trust_account_id", None)
    ta_row = _validate_trust_account(conn, ta_id)

    raw_statement = getattr(args, "statement_balance", None)
    if raw_statement is None or (isinstance(raw_statement, str) and raw_statement.strip() == ""):
        err("--statement-balance is required")
    try:
        statement_balance = to_decimal(raw_statement)
    except (ValueError, TypeError, InvalidOperation):
        err(f"Invalid --statement-balance: {raw_statement!r}")
    if not statement_balance.is_finite():
        err(f"Invalid --statement-balance: {raw_statement!r}")
    try:
        statement_balance = round_currency(statement_balance)
    except (ValueError, TypeError, InvalidOperation):
        err(f"Invalid --statement-balance: {raw_statement!r}")

    book_balance = round_currency(to_decimal(ta_row["current_balance"] or "0"))

    # Calculate balance from transactions (TEXT amounts, Decimal math in Python)
    txn_q = (
        Q.from_(_txn)
        .select(
            _txn.id, _txn.transaction_type, _txn.amount,
            _txn.description, _txn.matter_id, _txn.company_id,
        )
        .where(_txn.trust_account_id == P())
    )
    txn_rows = conn.execute(txn_q.get_sql(), (ta_id,)).fetchall()

    classified = []
    for row in txn_rows:
        if row["company_id"] != ta_row["company_id"]:
            err(
                f"Cannot reconcile transaction {row['id']}: "
                "company does not match the trust account"
            )
        classified.append((row, _reconciliation_direction(row)))

    deposits = sum(
        (to_decimal(row["amount"]) for row, direction in classified
         if direction == "in"),
        Decimal("0"),
    )
    withdrawals = sum(
        (to_decimal(row["amount"]) for row, direction in classified
         if direction == "out"),
        Decimal("0"),
    )
    calc_balance = round_currency(deposits - withdrawals)

    # Per-matter breakdown (fetch raw TEXT amounts, aggregate in Python)
    matter_txn_q = (
        Q.from_(_txn)
        .left_join(_matter).on(_txn.matter_id == _matter.id)
        .select(
            _txn.matter_id, _matter.id.as_("mid"), _matter.title,
            _matter.company_id.as_("matter_company_id"),
            _txn.id, _txn.transaction_type, _txn.amount,
            _txn.description, _txn.company_id,
        )
        .where(_txn.trust_account_id == P())
        .where(_txn.matter_id.isnotnull())
    )
    matter_txn_rows = conn.execute(matter_txn_q.get_sql(), (ta_id,)).fetchall()

    # Aggregate per-matter in Python with Decimal
    matter_data = {}
    for row in matter_txn_rows:
        if (row["company_id"] != ta_row["company_id"]
                or row["matter_company_id"] != ta_row["company_id"]):
            err(
                f"Cannot reconcile transaction {row['id']}: "
                "company does not match its trust account and matter"
            )
        mid = row["mid"]
        if mid not in matter_data:
            matter_data[mid] = {"title": row["title"], "deposits": Decimal("0"), "withdrawals": Decimal("0")}
        amt = to_decimal(row["amount"])
        if _reconciliation_direction(row) == "in":
            matter_data[mid]["deposits"] += amt
        else:
            matter_data[mid]["withdrawals"] += amt

    client_ledger = []
    for mid, md in matter_data.items():
        client_ledger.append({
            "matter_id": mid,
            "title": md["title"],
            "deposits": str(round_currency(md["deposits"])),
            "withdrawals": str(round_currency(md["withdrawals"])),
            "balance": str(round_currency(md["deposits"] - md["withdrawals"])),
        })

    account_deposits = sum(
        (to_decimal(row["amount"]) for row, direction in classified
         if row["matter_id"] is None
         and row["transaction_type"] in ("interest", "transfer")
         and direction == "in"),
        Decimal("0"),
    )
    account_withdrawals = sum(
        (to_decimal(row["amount"]) for row, direction in classified
         if row["matter_id"] is None
         and row["transaction_type"] == "transfer"
         and direction == "out"),
        Decimal("0"),
    )
    if account_deposits or account_withdrawals:
        client_ledger.append({
            "matter_id": None,
            "title": "Account-level activity",
            "deposits": str(round_currency(account_deposits)),
            "withdrawals": str(round_currency(account_withdrawals)),
            "balance": str(round_currency(
                account_deposits - account_withdrawals)),
        })

    client_total = round_currency(sum((to_decimal(c["balance"]) for c in client_ledger), Decimal("0")))
    statement_to_book_difference = round_currency(statement_balance - book_balance)
    book_to_client_difference = round_currency(book_balance - client_total)
    is_reconciled = (statement_balance == book_balance == calc_balance == client_total)

    ok({
        "trust_account_id": ta_id,
        "account_name": ta_row["name"],
        "statement_balance": str(statement_balance),
        "book_balance": str(book_balance),
        "calculated_balance": str(calc_balance),
        "client_ledger_total": str(client_total),
        "statement_to_book_difference": str(statement_to_book_difference),
        "book_to_client_difference": str(book_to_client_difference),
        "is_reconciled": is_reconciled,
        "total_deposits": str(round_currency(deposits)),
        "total_withdrawals": str(round_currency(withdrawals)),
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
    _validate_company(conn, args.company_id)
    ta_row = _validate_trust_account(conn, ta_id, args.company_id)
    _validate_trust_matter(conn, getattr(args, "matter_id", None), args.company_id)

    amount_raw = getattr(args, "amount", None)
    if not amount_raw:
        err("--amount is required (interest amount)")
    amount = round_currency(to_decimal(amount_raw))
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

    audit(conn, SKILL, "legal-trust-interest-distribution", "legalclaw_trust_transaction", txn_id)
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
