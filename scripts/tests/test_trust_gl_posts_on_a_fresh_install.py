"""Trust GL posts on a fresh install without hand-registering voucher types.

The four trust voucher types ("Trust Deposit", "Trust Disbursement",
"Trust Transfer", "Trust Interest") are seeded by init_schema on a fresh
install, so every GL-linked trust posting succeeds with no manual
`add-voucher-type` step. (Before migration 049 / the seed rows, each of
these calls returned an error naming the unregistered voucher type and the
whole action rolled back.)
"""
import os
import sys
from decimal import Decimal

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from legal_helpers import (  # noqa: E402
    call_action, ns, is_ok, is_error, load_db_query,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS


def _gl_linked_account(conn, env, name="GL Trust", with_interest=False):
    r = call_action(ACTIONS["legal-add-trust-account"], conn, ns(
        company_id=env["company_id"], trust_name=name,
        gl_account_id=env["trust_bank_acct"],
        trust_liability_account_id=env["trust_liability_acct"],
        interest_income_account_id=env["interest_income_acct"] if with_interest else None))
    assert is_ok(r), r
    return r["id"]


def _gl_rows(conn, voucher_id):
    return conn.execute(
        "SELECT id, account_id, debit, credit, voucher_type, voucher_id, "
        "party_type, is_cancelled, cost_center_id FROM gl_entry WHERE voucher_id = ?",
        (voucher_id,)).fetchall()


def _legs(rows):
    return {(row["account_id"], Decimal(row["debit"]), Decimal(row["credit"]),
             row["voucher_type"]) for row in rows}


class TestTrustPostsOnAFreshInstall:
    def test_deposit_and_disbursement_post_gl_rows(self, conn, env):
        ta_id = _gl_linked_account(conn, env)

        dep = call_action(ACTIONS["legal-deposit-trust"], conn, ns(
            company_id=env["company_id"], trust_account_id=ta_id,
            matter_id=env["matter_id"], amount="5000.00",
            transaction_date="2026-03-05"))
        assert is_ok(dep), dep
        assert "gl_entry_ids" in dep
        assert len(dep["gl_entry_ids"]) == 2

        dep_rows = _gl_rows(conn, dep["id"])
        assert len(dep_rows) == 2
        assert _legs(dep_rows) == {
            (env["trust_bank_acct"], Decimal("5000.00"), Decimal("0"),
             "Trust Deposit"),
            (env["trust_liability_acct"], Decimal("0"), Decimal("5000.00"),
             "Trust Deposit"),
        }
        assert {row["voucher_id"] for row in dep_rows} == {dep["id"]}
        stored = conn.execute(
            "SELECT gl_entry_ids FROM legalclaw_trust_transaction WHERE id = ?",
            (dep["id"],)).fetchone()["gl_entry_ids"]
        assert set(stored.split(",")) == {row["id"] for row in dep_rows}

        dis = call_action(ACTIONS["legal-disburse-trust"], conn, ns(
            company_id=env["company_id"], trust_account_id=ta_id,
            matter_id=env["matter_id"], amount="1200.00",
            payee="Jane Client", transaction_date="2026-03-10"))
        assert is_ok(dis), dis
        assert "gl_entry_ids" in dis
        assert len(dis["gl_entry_ids"]) == 2

        dis_rows = _gl_rows(conn, dis["id"])
        assert len(dis_rows) == 2
        assert _legs(dis_rows) == {
            (env["trust_liability_acct"], Decimal("1200.00"), Decimal("0"),
             "Trust Disbursement"),
            (env["trust_bank_acct"], Decimal("0"), Decimal("1200.00"),
             "Trust Disbursement"),
        }
        assert {row["voucher_id"] for row in dis_rows} == {dis["id"]}

    def test_transfer_and_interest_post_gl_rows(self, conn, env):
        from_id = _gl_linked_account(conn, env, name="GL Trust Source")
        to_id = _gl_linked_account(conn, env, name="GL Trust Dest",
                                   with_interest=True)

        dep = call_action(ACTIONS["legal-deposit-trust"], conn, ns(
            company_id=env["company_id"], trust_account_id=from_id,
            matter_id=env["matter_id"], amount="5000.00",
            transaction_date="2026-03-05"))
        assert is_ok(dep), dep

        tr = call_action(ACTIONS["legal-transfer-trust"], conn, ns(
            company_id=env["company_id"], trust_account_id=from_id,
            to_trust_account_id=to_id, amount="300.00",
            transaction_date="2026-03-08"))
        assert is_ok(tr), tr
        assert "gl_entry_ids" in tr
        assert len(tr["gl_entry_ids"]) == 2

        tr_rows = conn.execute(
            "SELECT id, account_id, debit, credit, voucher_type, voucher_id "
            "FROM gl_entry WHERE id IN (?, ?)",
            tuple(tr["gl_entry_ids"])).fetchall()
        assert len(tr_rows) == 2
        assert _legs(tr_rows) == {
            (env["trust_bank_acct"], Decimal("300.00"), Decimal("0"),
             "Trust Transfer"),
            (env["trust_bank_acct"], Decimal("0"), Decimal("300.00"),
             "Trust Transfer"),
        }

        intr = call_action(
            ACTIONS["legal-trust-interest-distribution"], conn, ns(
                company_id=env["company_id"], trust_account_id=to_id,
                amount="12.50", transaction_date="2026-03-15"))
        assert is_ok(intr), intr
        assert "gl_entry_ids" in intr
        assert len(intr["gl_entry_ids"]) == 2

        int_rows = _gl_rows(conn, intr["id"])
        assert len(int_rows) == 2
        assert _legs(int_rows) == {
            (env["trust_bank_acct"], Decimal("12.50"), Decimal("0"),
             "Trust Interest"),
            (env["interest_income_acct"], Decimal("0"), Decimal("12.50"),
             "Trust Interest"),
        }
        income_legs = [row for row in int_rows
                       if row["account_id"] == env["interest_income_acct"]]
        assert len(income_legs) == 1
        assert income_legs[0]["cost_center_id"]
