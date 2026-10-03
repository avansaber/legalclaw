"""Disbursing a settlement pays it out of the trust account.

legal-disburse-settlement moves the settlement total (net to client +
contingency fee + costs advanced) out of a named trust account through the
same write path as legal-disburse-trust: one trust disbursement line per
split, each lowering the trust account and the matter trust balance, posting
Trust Disbursement GL legs when the account is GL-linked. All lines commit
together; any failure rolls everything back.

(On the base commit this action only flipped the settlement status and wrote
no trust row at all.)
"""
import importlib.util
import os
import sys
from decimal import Decimal
from types import SimpleNamespace

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from legal_helpers import (  # noqa: E402
    call_action, ns, is_ok, is_error, load_db_query,
    seed_company, seed_trust_account, seed_account,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS

from erpclaw_lib.query import Q, P, Table  # noqa: E402

_settlement = Table("legalclaw_settlement")
_company = Table("company")

SNAPSHOT_TABLES = (
    "legalclaw_settlement",
    "legalclaw_trust_account",
    "legalclaw_trust_transaction",
    "legalclaw_matter",
    "gl_entry",
    "audit_log",
)


def _snapshot(conn):
    return {
        table: sorted(
            repr(tuple(row))
            for row in conn.execute("SELECT * FROM %s ORDER BY id" % table).fetchall()
        )
        for table in SNAPSHOT_TABLES
    }


def _setup(conn, env, deposit_amount, with_matter=True, trust_account_id=None):
    """Fund a trust account and record the standard 10000.00 settlement."""
    ta_id = trust_account_id or env["trust_account_id"]
    dep = call_action(ACTIONS["legal-deposit-trust"], conn, ns(
        company_id=env["company_id"], trust_account_id=ta_id,
        matter_id=env["matter_id"] if with_matter else None,
        amount=deposit_amount, transaction_date="2026-03-05"))
    assert is_ok(dep), dep
    rec = call_action(ACTIONS["legal-record-settlement"], conn, ns(
        company_id=env["company_id"], matter_id=env["matter_id"],
        gross_amount="10000.00", contingency_pct="30", costs_advanced="500.00",
        settlement_date="2026-03-06", payment_method="check"))
    assert is_ok(rec), rec
    assert (rec["attorney_fee"], rec["net_to_client"]) == ("3000.00", "6500.00")
    return ta_id, rec["settlement_id"]


def _disburse(conn, env, settlement_id, trust_account_id, date="2026-03-10"):
    return call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
        settlement_id=settlement_id, company_id=env["company_id"],
        trust_account_id=trust_account_id, transaction_date=date))


def _company_name(conn, company_id):
    row = conn.execute(
        Q.from_(_company).select(_company.name).where(_company.id == P()).get_sql(),
        (company_id,),
    ).fetchone()
    return row["name"]


def _trust_lines(conn, settlement_id):
    return conn.execute(
        "SELECT * FROM legalclaw_trust_transaction WHERE reference = ? ORDER BY rowid",
        (settlement_id,),
    ).fetchall()


def _register_voucher_types(conn, *voucher_types):
    setup_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(_TESTS_DIR))),
        "erpclaw", "scripts", "erpclaw-setup", "db_query.py")
    spec = importlib.util.spec_from_file_location(
        "erpclaw_setup_db_query_m671", setup_path)
    setup_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup_mod)
    for voucher_type in voucher_types:
        r = call_action(setup_mod.add_voucher_type, conn, SimpleNamespace(
            voucher_type=voucher_type, target_table="gl_entry",
            label=None, skill_name="legalclaw"))
        if is_ok(r):
            continue
        assert r.get("message") == (
            f"voucher_type '{voucher_type}' is already registered for gl_entry"), r
    return setup_mod


def _gl_linked_account(conn, env):
    r = call_action(ACTIONS["legal-add-trust-account"], conn, ns(
        company_id=env["company_id"], trust_name="GL Trust",
        gl_account_id=env["trust_bank_acct"],
        trust_liability_account_id=env["trust_liability_acct"]))
    assert is_ok(r), r
    return r["id"]


def _firm_accounts(conn, env):
    """Seed the firm's own accounts a GL-linked disbursement now requires."""
    operating = seed_account(conn, env["company_id"], "Firm Operating",
                             "asset", "bank", "1010")
    fee_income = seed_account(conn, env["company_id"], "Fee Income",
                              "income", "revenue", "4010")
    costs_recovery = seed_account(conn, env["company_id"], "Costs Advanced",
                                  "asset", None, "1410")
    return operating, fee_income, costs_recovery


def _disburse_gl_linked(conn, env, settlement_id, trust_account_id,
                        date="2026-03-10"):
    operating, fee_income, costs_recovery = _firm_accounts(conn, env)
    return call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
        settlement_id=settlement_id, company_id=env["company_id"],
        trust_account_id=trust_account_id, transaction_date=date,
        operating_account_id=operating,
        fee_income_account_id=fee_income,
        costs_recovery_account_id=costs_recovery,
        cost_center_id=env["cost_center_id"]))


class TestDisbursePaysThreeLinesFromTrust:

    def test_disburse_pays_three_lines_from_trust(self, conn, env):
        ta_id, sid = _setup(conn, env, "12000.00")
        before = _snapshot(conn)

        r = _disburse(conn, env, sid, ta_id)
        assert is_ok(r), r
        assert r["settlement_id"] == sid
        assert r["settlement_status"] == "disbursed"
        assert r["trust_account_id"] == ta_id
        assert r["amount_disbursed"] == "10000.00"
        assert r["new_balance"] == "2000.00"
        assert "gl_entry_ids" not in r

        firm_name = _company_name(conn, env["company_id"])
        lines = _trust_lines(conn, sid)
        assert len(lines) == 3
        assert r["trust_transaction_ids"] == [lines[0]["id"], lines[1]["id"], lines[2]["id"]]
        expected = [
            ("6500.00", "Jane Client", "Settlement net to client"),
            ("3000.00", firm_name, "Settlement contingency fee"),
            ("500.00", firm_name, "Settlement costs reimbursement"),
        ]
        for line, (amount, payee, description) in zip(lines, expected):
            assert line["transaction_type"] == "disbursement"
            assert line["trust_account_id"] == ta_id
            assert line["matter_id"] == env["matter_id"]
            assert line["transaction_date"] == "2026-03-10"
            assert line["amount"] == amount
            assert line["payee"] == payee
            assert line["description"] == description
            assert line["reference"] == sid

        account = conn.execute(
            "SELECT current_balance FROM legalclaw_trust_account WHERE id = ?",
            (ta_id,)).fetchone()
        assert account["current_balance"] == "2000.00"
        matter = conn.execute(
            "SELECT trust_balance FROM legalclaw_matter WHERE id = ?",
            (env["matter_id"],)).fetchone()
        assert matter["trust_balance"] == "2000.00"

        settlement = conn.execute(
            "SELECT * FROM legalclaw_settlement WHERE id = ?", (sid,)).fetchone()
        assert settlement["status"] == "disbursed"
        assert (settlement["gross_amount"], settlement["contingency_pct"],
                settlement["attorney_fee"], settlement["costs_advanced"],
                settlement["net_to_client"]) == (
            "10000.00", "30", "3000.00", "500.00", "6500.00")

        after = _snapshot(conn)
        assert after["gl_entry"] == before["gl_entry"]
        assert len(after["audit_log"]) == len(before["audit_log"]) + 4
        for table in ("legalclaw_settlement", "legalclaw_trust_account",
                      "legalclaw_trust_transaction", "legalclaw_matter"):
            assert after[table] != before[table]


class TestGLLinkedAccount:

    def test_gl_linked_account_posts_trust_disbursement_rows(self, conn, env):
        _register_voucher_types(conn, "Trust Deposit", "Trust Disbursement")
        ta_id = _gl_linked_account(conn, env)
        _, sid = _setup(conn, env, "10000.00", trust_account_id=ta_id)

        r = _disburse_gl_linked(conn, env, sid, ta_id)
        assert is_ok(r), r["message"] if is_error(r) else r

        account = conn.execute(
            "SELECT current_balance FROM legalclaw_trust_account WHERE id = ?",
            (ta_id,)).fetchone()
        assert account["current_balance"] == "0.00"
        matter = conn.execute(
            "SELECT trust_balance FROM legalclaw_matter WHERE id = ?",
            (env["matter_id"],)).fetchone()
        assert matter["trust_balance"] == "0.00"

        txn_ids = r["trust_transaction_ids"]
        assert len(txn_ids) == 3
        per_line_ids = []
        for txn_id, amount in zip(txn_ids, ("6500.00", "3000.00", "500.00")):
            rows = conn.execute(
                "SELECT id, account_id, debit, credit, voucher_type, posting_date, "
                "party_type, is_cancelled FROM gl_entry WHERE voucher_id = ? "
                "AND entry_set = 'primary'",
                (txn_id,)).fetchall()
            assert {(row["account_id"], row["debit"], row["credit"],
                     row["voucher_type"], row["posting_date"],
                     row["party_type"], row["is_cancelled"]) for row in rows} == {
                (env["trust_liability_acct"], amount, "0.00", "Trust Disbursement",
                 "2026-03-10", None, 0),
                (env["trust_bank_acct"], "0.00", amount, "Trust Disbursement",
                 "2026-03-10", None, 0),
            }
            assert Decimal(sum(Decimal(row["debit"]) for row in rows)) == \
                Decimal(sum(Decimal(row["credit"]) for row in rows))
            stored = conn.execute(
                "SELECT gl_entry_ids FROM legalclaw_trust_transaction WHERE id = ?",
                (txn_id,)).fetchone()["gl_entry_ids"]
            firm_rows = conn.execute(
                "SELECT id FROM gl_entry WHERE voucher_id = ? "
                "AND entry_set = 'firm_receipt'",
                (txn_id,)).fetchall()
            assert set(stored.split(",")) == (
                {row["id"] for row in rows} | {row["id"] for row in firm_rows})
            per_line_ids.append(stored.split(","))
        assert r["gl_entry_ids"] == per_line_ids[0] + per_line_ids[1] + per_line_ids[2]

    def test_gl_failure_rolls_back_every_line(self, conn, env):
        _reg = Table("voucher_type_registry")
        conn.execute(
            Q.update(_reg).set(_reg.is_active, P()).where(
                _reg.voucher_type == P()).where(
                _reg.target_table == P()).get_sql(),
            (0, "Trust Disbursement", "gl_entry"))
        conn.commit()
        ta_id = _gl_linked_account(conn, env)
        _, sid = _setup(conn, env, "10000.00", trust_account_id=ta_id)
        before = _snapshot(conn)

        r = _disburse_gl_linked(conn, env, sid, ta_id)
        assert is_error(r), r
        assert r["message"].startswith(
            f"Settlement {sid} could not be disbursed; nothing was written: "
            "voucher_type 'Trust Disbursement' is not a registered")

        assert _snapshot(conn) == before
        settlement = conn.execute(
            "SELECT status FROM legalclaw_settlement WHERE id = ?", (sid,)).fetchone()
        assert settlement["status"] == "pending"


class TestDisburseRefusals:

    def test_refuses_overdraw_of_account(self, conn, env):
        _, sid = _setup(conn, env, "9000.00")
        before = _snapshot(conn)

        r = call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
            settlement_id=sid, company_id=env["company_id"],
            trust_account_id=env["trust_account_id"], transaction_date="2026-03-10"))
        assert is_error(r), r
        assert r["message"] == "Insufficient trust balance: 9000.00 available, 10000.00 requested"
        assert _snapshot(conn) == before

    def test_refuses_overdraw_of_matter(self, conn, env):
        _, sid = _setup(conn, env, "12000.00", with_matter=False)
        before = _snapshot(conn)

        r = call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
            settlement_id=sid, company_id=env["company_id"],
            trust_account_id=env["trust_account_id"], transaction_date="2026-03-10"))
        assert is_error(r), r
        assert r["message"] == (
            f"Insufficient trust balance for matter {env['matter_id']}: "
            "0 available, 10000.00 requested")
        assert _snapshot(conn) == before

    def test_refuses_missing_trust_account(self, conn, env):
        _, sid = _setup(conn, env, "12000.00")
        before = _snapshot(conn)

        r = call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
            settlement_id=sid, company_id=env["company_id"],
            transaction_date="2026-03-10"))
        assert is_error(r), r
        assert r["message"] == "--trust-account-id is required"
        assert _snapshot(conn) == before

    def test_refuses_other_company_account(self, conn, env):
        _, sid = _setup(conn, env, "12000.00")
        other_co = seed_company(conn, name="Other Firm", abbr="OF")
        other_ta = seed_trust_account(conn, other_co)
        before = _snapshot(conn)

        r = call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
            settlement_id=sid, company_id=env["company_id"],
            trust_account_id=other_ta, transaction_date="2026-03-10"))
        assert is_error(r), r
        assert r["message"] == (
            f"Trust account {other_ta} belongs to a different company than settlement {sid}")
        assert _snapshot(conn) == before

    def test_refuses_stored_negative_net(self, conn, env):
        _, sid = _setup(conn, env, "12000.00")
        conn.execute(
            Q.update(_settlement).set(_settlement.net_to_client, P()).where(
                _settlement.id == P()).get_sql(),
            ("-1000.00", sid))
        before = _snapshot(conn)

        r = call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
            settlement_id=sid, company_id=env["company_id"],
            trust_account_id=env["trust_account_id"], transaction_date="2026-03-10"))
        assert is_error(r), r
        assert r["message"] == (
            f"Settlement {sid} has a negative net to client (-1000.00); "
            "it cannot be disbursed")
        assert _snapshot(conn) == before

    def test_refuses_when_status_changes_before_write(self, conn, env, monkeypatch):
        import trust as trust_module

        _, sid = _setup(conn, env, "12000.00")
        before = _snapshot(conn)
        real_validate = trust_module._validate_trust_account

        def _flipping_validate(conn_arg, trust_account_id):
            conn_arg.execute(
                Q.update(_settlement).set(_settlement.status, P()).where(
                    _settlement.id == P()).get_sql(),
                ("disbursed", sid))
            return real_validate(conn_arg, trust_account_id)

        monkeypatch.setattr(trust_module, "_validate_trust_account", _flipping_validate)

        r = call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
            settlement_id=sid, company_id=env["company_id"],
            trust_account_id=env["trust_account_id"], transaction_date="2026-03-10"))
        assert is_error(r), r
        assert r["message"] == f"Settlement {sid} is no longer pending; nothing was written"
        assert _snapshot(conn) == before
        assert conn.execute(
            "SELECT COUNT(*) FROM legalclaw_trust_transaction "
            "WHERE transaction_type = 'disbursement'").fetchone()[0] == 0
