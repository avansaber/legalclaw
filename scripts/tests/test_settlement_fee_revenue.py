"""A disbursed settlement books the firm's fee as revenue.

legal-disburse-settlement pays a pending settlement out of a GL-linked trust
account in one transaction. The three trust legs (net to client, contingency
fee, costs reimbursement) post DR trust liability / CR trust bank as before;
the firm's two lines additionally post DR operating / CR fee income and
DR operating / CR costs recovery under entry_set 'firm_receipt' on the same
voucher, so the fee lands in profit and loss and the cash lands in the firm's
own bank.
"""
import argparse
import importlib.util
import io
import json
import os
import sys
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

SCRIPTS_DIR = os.path.dirname(_TESTS_DIR)
MODULE_DIR = os.path.dirname(SCRIPTS_DIR)
SRC_DIR = os.path.dirname(MODULE_DIR)
ERPCLAW_DIR = os.path.join(SRC_DIR, "erpclaw", "scripts", "erpclaw-setup")

_IN_TREE_LIB = os.path.join(SRC_DIR, "erpclaw", "scripts", "erpclaw-setup", "lib")
ERPCLAW_LIB = (_IN_TREE_LIB if os.path.isdir(os.path.join(_IN_TREE_LIB, "erpclaw_lib"))
               else os.path.join(os.path.expanduser(
                   os.environ.get("ERPCLAW_HOME", "~/.openclaw/erpclaw")), "lib"))
if ERPCLAW_LIB not in sys.path:
    if importlib.util.find_spec("erpclaw_lib") is None:
        sys.path.insert(0, ERPCLAW_LIB)

from erpclaw_lib.query import Q, P, Table, dynamic_update  # noqa: E402

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def load_db_query():
    db_query_path = os.path.join(SCRIPTS_DIR, "db_query.py")
    spec = importlib.util.spec_from_file_location("db_query_legal_m764", db_query_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_foundation_setup():
    path = os.path.join(ERPCLAW_DIR, "db_query.py")
    spec = importlib.util.spec_from_file_location("erpclaw_setup_db_query_m764", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_reports():
    path = os.path.join(SRC_DIR, "erpclaw", "scripts", "erpclaw-reports", "db_query.py")
    spec = importlib.util.spec_from_file_location("erpclaw_reports_db_query_m764", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = load_db_query()
ACTIONS = _mod.ACTIONS
_REP = load_reports()

_gl = Table("gl_entry")
_txn = Table("legalclaw_trust_transaction")
_settlement = Table("legalclaw_settlement")
_ta = Table("legalclaw_trust_account")
_matter = Table("legalclaw_matter")
_company = Table("company")
_vtr = Table("voucher_type_registry")

GROSS = "100000.00"
PCT = "33"
COSTS = "2000.00"
FEE = "33000.00"
NET = "65000.00"
TOTAL = "100000.00"
FIRM_CASH = "35000.00"
ZERO_COSTS = "0.00"
TXN_DATE = "2026-03-10"


def call_action(fn, conn, args):
    buf = io.StringIO()

    def _fake_exit(code=0):
        raise SystemExit(code)

    try:
        with patch("sys.stdout", buf), patch("sys.exit", side_effect=_fake_exit):
            fn(conn, args)
    except SystemExit:
        pass
    output = buf.getvalue().strip()
    if not output:
        return {"status": "error", "message": "no output captured"}
    return json.loads(output)


def ns(**kwargs):
    defaults = {
        "company_id": None,
        "trust_account_id": None,
        "trust_name": None,
        "gl_account_id": None,
        "trust_liability_account_id": None,
        "matter_id": None,
        "amount": None,
        "transaction_date": None,
        "gross_amount": None,
        "contingency_pct": None,
        "costs_advanced": None,
        "settlement_date": None,
        "payment_method": None,
        "settlement_id": None,
        "operating_account_id": None,
        "fee_income_account_id": None,
        "costs_recovery_account_id": None,
        "cost_center_id": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def is_ok(result):
    return result.get("status") == "ok"


def is_error(result):
    return result.get("status") == "error"


def _uuid():
    return str(uuid.uuid4())


def seed_account(conn, company_id, name="Test Account",
                 root_type="asset", account_type=None,
                 account_number=None):
    aid = _uuid()
    direction = "debit_normal" if root_type in ("asset", "expense") else "credit_normal"
    conn.execute(
        """INSERT INTO account (id, name, account_number, root_type, account_type,
           balance_direction, company_id, depth)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
        (aid, name, account_number or f"ACC-{aid[:6]}", root_type,
         account_type, direction, company_id)
    )
    conn.commit()
    return aid


def _register_voucher_types(conn, *voucher_types):
    setup_mod = load_foundation_setup()
    for voucher_type in voucher_types:
        q = (Q.from_(_vtr).select(_vtr.voucher_type)
             .where(_vtr.voucher_type == P())
             .where(_vtr.target_table == P()))
        if conn.execute(q.get_sql(), (voucher_type, "gl_entry")).fetchone() is not None:
            continue
        r = call_action(setup_mod.add_voucher_type, conn, SimpleNamespace(
            voucher_type=voucher_type, target_table="gl_entry",
            label=None, skill_name="legalclaw"))
        assert is_ok(r), r


def _setup_firm(conn, env):
    """Register voucher types and seed the trust + firm accounts."""
    _register_voucher_types(conn, "Trust Deposit", "Trust Disbursement")
    q = (Q.from_(_vtr).select(_vtr.voucher_type)
         .where((_vtr.voucher_type == P()) | (_vtr.voucher_type == P()))
         .where(_vtr.target_table == P())
         .where(_vtr.is_active == P()))
    rows = conn.execute(
        q.get_sql(), ("Trust Deposit", "Trust Disbursement", "gl_entry", 1)).fetchall()
    assert {r["voucher_type"] for r in rows} == {"Trust Deposit", "Trust Disbursement"}, rows
    trust_bank = seed_account(conn, env["company_id"], "IOLTA Trust Bank",
                              "asset", "bank", "1310")
    trust_liability = seed_account(conn, env["company_id"], "Client Trust Liability",
                                   "liability", "liability", "2110")
    r = call_action(ACTIONS["legal-add-trust-account"], conn, ns(
        company_id=env["company_id"], trust_name="IOLTA",
        gl_account_id=trust_bank,
        trust_liability_account_id=trust_liability))
    assert is_ok(r), r
    operating = seed_account(conn, env["company_id"], "Firm Operating",
                             "asset", "bank", "1010")
    fee_income = seed_account(conn, env["company_id"], "Fee Income",
                              "income", "revenue", "4010")
    costs_recovery = seed_account(conn, env["company_id"], "Costs Advanced",
                                  "asset", None, "1410")
    return {
        "trust_bank": trust_bank,
        "trust_liability": trust_liability,
        "trust_account_id": r["id"],
        "operating": operating,
        "fee_income": fee_income,
        "costs_recovery": costs_recovery,
    }


def _deposit(conn, env, trust_account_id, amount):
    r = call_action(ACTIONS["legal-deposit-trust"], conn, ns(
        company_id=env["company_id"], trust_account_id=trust_account_id,
        matter_id=env["matter_id"], amount=amount,
        transaction_date="2026-03-05"))
    assert is_ok(r), r
    return r


def _record(conn, env, gross=GROSS, pct=PCT, costs=COSTS):
    r = call_action(ACTIONS["legal-record-settlement"], conn, ns(
        company_id=env["company_id"], matter_id=env["matter_id"],
        gross_amount=gross, contingency_pct=pct, costs_advanced=costs,
        settlement_date="2026-03-06", payment_method="check"))
    assert is_ok(r), r
    return r["settlement_id"]


def _disburse(conn, env, ctx, settlement_id, **overrides):
    args = {
        "settlement_id": settlement_id,
        "company_id": env["company_id"],
        "trust_account_id": ctx["trust_account_id"],
        "transaction_date": TXN_DATE,
        "operating_account_id": ctx["operating"],
        "fee_income_account_id": ctx["fee_income"],
        "costs_recovery_account_id": ctx["costs_recovery"],
        "cost_center_id": env["cost_center_id"],
    }
    args.update(overrides)
    return call_action(ACTIONS["legal-disburse-settlement"], conn, ns(**args))


def _gl_rows(conn, voucher_id, entry_set):
    q = (Q.from_(_gl).select(_gl.star)
         .where(_gl.voucher_id == P())
         .where(_gl.entry_set == P()))
    return conn.execute(q.get_sql(), (voucher_id, entry_set)).fetchall()


def _gl_counts(conn):
    return (
        len(conn.execute(Q.from_(_gl).select(_gl.id).get_sql()).fetchall()),
        len(conn.execute(Q.from_(_txn).select(_txn.id).get_sql()).fetchall()),
    )


def _settlement_status(conn, settlement_id):
    q = (Q.from_(_settlement).select(_settlement.status)
         .where(_settlement.id == P()))
    return conn.execute(q.get_sql(), (settlement_id,)).fetchone()["status"]


def _trust_balances(conn, trust_account_id, matter_id):
    q = (Q.from_(_ta).select(_ta.current_balance)
         .where(_ta.id == P()))
    account_balance = conn.execute(q.get_sql(), (trust_account_id,)).fetchone()["current_balance"]
    mq = (Q.from_(_matter).select(_matter.trust_balance)
          .where(_matter.id == P()))
    matter_balance = conn.execute(mq.get_sql(), (matter_id,)).fetchone()["trust_balance"]
    return account_balance, matter_balance


def _account_balance(conn, account_id):
    q = (Q.from_(_gl).select(_gl.debit, _gl.credit)
         .where(_gl.account_id == P()))
    rows = conn.execute(q.get_sql(), (account_id,)).fetchall()
    debits = sum((Decimal(r["debit"]) for r in rows), Decimal("0"))
    credits = sum((Decimal(r["credit"]) for r in rows), Decimal("0"))
    return debits - credits


def _profit_and_loss(conn, company_id):
    return call_action(_REP.profit_and_loss, conn, SimpleNamespace(
        company_id=company_id, company_name=None,
        from_date="2026-01-01", to_date="2026-12-31",
        group_by=None, project_id=None,
        dimension_key=[], dimension_value=[]))


def _as_tuples(rows):
    return {(r["account_id"], r["debit"], r["credit"], r["entry_set"],
             r["cost_center_id"], r["party_type"], r["party_id"]) for r in rows}


class TestFeeBookedAsRevenue:

    def test_fee_booked_as_revenue(self, conn, env):
        ctx = _setup_firm(conn, env)
        _deposit(conn, env, ctx["trust_account_id"], TOTAL)
        sid = _record(conn, env)

        r = _disburse(conn, env, ctx, sid)
        assert is_ok(r), r
        assert r["fee_income_posted"] == "33000.00"
        assert r["costs_recovered"] == "2000.00"

        txn_ids = r["trust_transaction_ids"]
        assert len(txn_ids) == 3

        net_rows = _gl_rows(conn, txn_ids[0], "primary")
        assert _as_tuples(net_rows) == {
            (ctx["trust_liability"], "65000.00", "0.00", "primary", None, None, None),
            (ctx["trust_bank"], "0.00", "65000.00", "primary", None, None, None),
        }
        assert _gl_rows(conn, txn_ids[0], "firm_receipt") == []

        fee_primary = _gl_rows(conn, txn_ids[1], "primary")
        assert _as_tuples(fee_primary) == {
            (ctx["trust_liability"], "33000.00", "0.00", "primary", None, None, None),
            (ctx["trust_bank"], "0.00", "33000.00", "primary", None, None, None),
        }
        fee_firm = _gl_rows(conn, txn_ids[1], "firm_receipt")
        assert _as_tuples(fee_firm) == {
            (ctx["operating"], "33000.00", "0.00", "firm_receipt", None, None, None),
            (ctx["fee_income"], "0.00", "33000.00", "firm_receipt",
             env["cost_center_id"], None, None),
        }

        costs_primary = _gl_rows(conn, txn_ids[2], "primary")
        assert _as_tuples(costs_primary) == {
            (ctx["trust_liability"], "2000.00", "0.00", "primary", None, None, None),
            (ctx["trust_bank"], "0.00", "2000.00", "primary", None, None, None),
        }
        costs_firm = _gl_rows(conn, txn_ids[2], "firm_receipt")
        assert _as_tuples(costs_firm) == {
            (ctx["operating"], "2000.00", "0.00", "firm_receipt", None, None, None),
            (ctx["costs_recovery"], "0.00", "2000.00", "firm_receipt", None, None, None),
        }

        assert len(r["gl_entry_ids"]) == 10

        pl = _profit_and_loss(conn, env["company_id"])
        assert is_ok(pl), pl
        assert pl["income_total"] == "33000.00"

    def test_ledger_balances(self, conn, env):
        ctx = _setup_firm(conn, env)
        _deposit(conn, env, ctx["trust_account_id"], TOTAL)
        sid = _record(conn, env)
        before_trust = _account_balance(conn, ctx["trust_bank"])
        assert before_trust == Decimal("100000.00")
        assert _account_balance(conn, ctx["operating"]) == Decimal("0")

        r = _disburse(conn, env, ctx, sid)
        assert is_ok(r), r

        total_debit = Decimal("0")
        total_credit = Decimal("0")
        for txn_id in r["trust_transaction_ids"]:
            for entry_set in ("primary", "firm_receipt"):
                for row in _gl_rows(conn, txn_id, entry_set):
                    total_debit += Decimal(row["debit"])
                    total_credit += Decimal(row["credit"])
        assert total_debit == total_credit

        after_trust = _account_balance(conn, ctx["trust_bank"])
        assert before_trust - after_trust == Decimal("100000.00")
        assert _account_balance(conn, ctx["operating"]) == Decimal("35000.00")


class TestDisburseRefusals:

    def test_missing_operating_account_refused(self, conn, env):
        ctx = _setup_firm(conn, env)
        _deposit(conn, env, ctx["trust_account_id"], TOTAL)
        sid = _record(conn, env)
        before = _gl_counts(conn)
        balances = _trust_balances(conn, ctx["trust_account_id"], env["matter_id"])

        r = _disburse(conn, env, ctx, sid, operating_account_id=None)
        assert is_error(r), r
        assert "--operating-account-id" in r["message"]

        assert _settlement_status(conn, sid) == "pending"
        assert _trust_balances(conn, ctx["trust_account_id"], env["matter_id"]) == balances
        assert _gl_counts(conn) == before

    def test_non_income_fee_account_refused(self, conn, env):
        ctx = _setup_firm(conn, env)
        _deposit(conn, env, ctx["trust_account_id"], TOTAL)
        sid = _record(conn, env)
        before = _gl_counts(conn)
        expense_acct = seed_account(conn, env["company_id"], "Office Expense",
                                    "expense", None, "5210")

        r = _disburse(conn, env, ctx, sid, fee_income_account_id=expense_acct)
        assert is_error(r), r
        assert "--fee-income-account-id" in r["message"]

        assert _settlement_status(conn, sid) == "pending"
        assert _gl_counts(conn) == before

    def test_costs_account_required_only_with_costs(self, conn, env):
        ctx = _setup_firm(conn, env)
        _deposit(conn, env, ctx["trust_account_id"], "200000.00")

        free_sid = _record(conn, env, costs=ZERO_COSTS)
        r = _disburse(conn, env, ctx, free_sid, costs_recovery_account_id=None)
        assert is_ok(r), r
        assert r["fee_income_posted"] == "33000.00"
        assert r["costs_recovered"] == "0.00"

        before = _gl_counts(conn)
        costly_sid = _record(conn, env)
        r = _disburse(conn, env, ctx, costly_sid, costs_recovery_account_id=None)
        assert is_error(r), r
        assert "--costs-recovery-account-id" in r["message"]

        assert _settlement_status(conn, costly_sid) == "pending"
        assert _gl_counts(conn) == before


class TestUnlinkedTrustAccount:

    def test_unlinked_trust_account_unchanged(self, conn, env):
        ta_id = env["trust_account_id"]
        _deposit(conn, env, ta_id, TOTAL)
        sid = _record(conn, env)
        gl_before, txn_before = _gl_counts(conn)

        r = call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
            settlement_id=sid, company_id=env["company_id"],
            trust_account_id=ta_id, transaction_date=TXN_DATE))
        assert is_ok(r), r
        assert r["fee_income_posted"] == "0.00"
        assert r["costs_recovered"] == "0.00"
        assert "gl_entry_ids" not in r
        gl_after, txn_after = _gl_counts(conn)
        assert gl_after == gl_before
        assert txn_after == txn_before + 3

    def test_unlinked_account_with_new_inputs_refused(self, conn, env):
        ta_id = env["trust_account_id"]
        _deposit(conn, env, ta_id, TOTAL)
        sid = _record(conn, env)
        before = _gl_counts(conn)
        balances = _trust_balances(conn, ta_id, env["matter_id"])
        operating = seed_account(conn, env["company_id"], "Firm Operating",
                                 "asset", "bank", "1010")

        r = call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
            settlement_id=sid, company_id=env["company_id"],
            trust_account_id=ta_id, transaction_date=TXN_DATE,
            operating_account_id=operating))
        assert is_error(r), r
        assert r["message"] == (
            f"Trust account {ta_id} is not GL-linked; "
            "the fee and costs cannot be posted")

        assert _settlement_status(conn, sid) == "pending"
        assert _trust_balances(conn, ta_id, env["matter_id"]) == balances
        assert _gl_counts(conn) == before


class TestCostsRecoveryVariants:

    def test_costs_receivable_carries_client_party(self, conn, env):
        ctx = _setup_firm(conn, env)
        receivable = seed_account(conn, env["company_id"], "Client Cost Advances",
                                  "asset", "receivable", "1210")
        _deposit(conn, env, ctx["trust_account_id"], TOTAL)
        sid = _record(conn, env)

        r = _disburse(conn, env, ctx, sid, costs_recovery_account_id=receivable)
        assert is_ok(r), r
        assert r["costs_recovered"] == "2000.00"

        costs_firm = _gl_rows(conn, r["trust_transaction_ids"][2], "firm_receipt")
        assert _as_tuples(costs_firm) == {
            (ctx["operating"], "2000.00", "0.00", "firm_receipt", None, None, None),
            (receivable, "0.00", "2000.00", "firm_receipt", None,
             "customer", env["core_customer_id"]),
        }

    def test_costs_expense_recovery(self, conn, env):
        ctx = _setup_firm(conn, env)
        expense_acct = seed_account(conn, env["company_id"], "Advanced Costs",
                                    "expense", None, "5210")
        _deposit(conn, env, ctx["trust_account_id"], TOTAL)
        sid = _record(conn, env)

        r = _disburse(conn, env, ctx, sid, costs_recovery_account_id=expense_acct)
        assert is_ok(r), r
        assert r["costs_recovered"] == "2000.00"

        costs_firm = _gl_rows(conn, r["trust_transaction_ids"][2], "firm_receipt")
        assert _as_tuples(costs_firm) == {
            (ctx["operating"], "2000.00", "0.00", "firm_receipt", None, None, None),
            (expense_acct, "0.00", "2000.00", "firm_receipt",
             env["cost_center_id"], None, None),
        }

        pl = _profit_and_loss(conn, env["company_id"])
        assert is_ok(pl), pl
        assert pl["income_total"] == "33000.00"
        assert pl["expense_total"] == "-2000.00"


class TestFeeAccountDefaults:

    def test_fee_account_defaults(self, conn, env):
        ctx = _setup_firm(conn, env)
        _deposit(conn, env, ctx["trust_account_id"], "200000.00")

        sql, params = dynamic_update("company",
            {"default_income_account_id": ctx["fee_income"]},
            where={"id": env["company_id"]})
        conn.execute(sql, params)
        conn.commit()

        first_sid = _record(conn, env)
        r = _disburse(conn, env, ctx, first_sid, fee_income_account_id=None)
        assert is_ok(r), r
        assert r["fee_income_posted"] == "33000.00"
        fee_firm = _gl_rows(conn, r["trust_transaction_ids"][1], "firm_receipt")
        assert _as_tuples(fee_firm) == {
            (ctx["operating"], "33000.00", "0.00", "firm_receipt", None, None, None),
            (ctx["fee_income"], "0.00", "33000.00", "firm_receipt",
             env["cost_center_id"], None, None),
        }

        sql, params = dynamic_update("company",
            {"default_income_account_id": None},
            where={"id": env["company_id"]})
        conn.execute(sql, params)
        conn.commit()

        before = _gl_counts(conn)
        second_sid = _record(conn, env)
        r = _disburse(conn, env, ctx, second_sid, fee_income_account_id=None)
        assert is_error(r), r
        assert "--fee-income-account-id" in r["message"]

        assert _settlement_status(conn, second_sid) == "pending"
        assert _gl_counts(conn) == before
