"""L1 behaviour tests for the LegalClaw contingency-fee settlement split.

Covers legal-record-settlement, legal-calculate-contingency-fee,
legal-disburse-settlement and legal-settlement-report:
  - exact fee and net arithmetic (ROUND_HALF_UP to 0.01), read back from
    legalclaw_settlement;
  - the 0% and 100% boundaries;
  - refusal of a gross amount that is not greater than zero, a contingency
    percentage outside 0..100 and negative costs advanced, with nothing written;
  - the pending -> disbursed status transition and the "already disbursed" refusal;
  - settlement report totals over the rows this test created.

A split whose costs exceed what is left after the fee (negative net to
client) is refused at creation.
"""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from legal_helpers import (  # noqa: E402
    call_action, ns, is_ok, is_error, load_db_query,
    seed_company, seed_customer, seed_client_ext, seed_matter,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS

MSG_GROSS = "--gross-amount must be greater than zero"
MSG_PCT = "--contingency-pct must be between 0 and 100"
MSG_COSTS = "--costs-advanced must not be negative"


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _settlement(conn, settlement_id):
    return conn.execute(
        "SELECT matter_id, company_id, settlement_date, gross_amount, "
        "contingency_pct, attorney_fee, costs_advanced, net_to_client, status "
        "FROM legalclaw_settlement WHERE id = ?",
        (settlement_id,),
    ).fetchone()


def _record(conn, env, gross, pct, costs=None, date="2026-03-02"):
    return call_action(ACTIONS["legal-record-settlement"], conn, ns(
        company_id=env["company_id"], matter_id=env["matter_id"],
        gross_amount=gross, contingency_pct=pct, costs_advanced=costs,
        settlement_date=date, payment_method="check"))


def _calculate(conn, gross, pct, costs=None):
    return call_action(ACTIONS["legal-calculate-contingency-fee"], conn, ns(
        gross_amount=gross, contingency_pct=pct, costs_advanced=costs))


def _fund(conn, env, amount, date="2026-03-01"):
    r = call_action(ACTIONS["legal-deposit-trust"], conn, ns(
        company_id=env["company_id"], trust_account_id=env["trust_account_id"],
        matter_id=env["matter_id"], amount=amount, transaction_date=date))
    assert is_ok(r), r
    return r


# -- legal-record-settlement ------------------------------------------------


class TestRecordSettlementSplit:

    def test_split_is_stored_exactly(self, conn, env):
        r = _record(conn, env, "100000.00", "33.33", "2500.00")
        assert is_ok(r), r
        assert r["attorney_fee"] == "33330.00"
        assert r["net_to_client"] == "64170.00"
        assert r["settlement_status"] == "pending"
        row = _settlement(conn, r["settlement_id"])
        assert (row["matter_id"], row["company_id"], row["settlement_date"]) == (
            env["matter_id"], env["company_id"], "2026-03-02")
        assert row["gross_amount"] == "100000.00"
        assert row["contingency_pct"] == "33.33"
        assert row["attorney_fee"] == "33330.00"
        assert row["costs_advanced"] == "2500.00"
        assert row["net_to_client"] == "64170.00"
        assert row["status"] == "pending"

    def test_fee_rounds_half_up(self, conn, env):
        # 100.50 x 25% = 25.125: HALF_UP gives 25.13 (HALF_EVEN would give 25.12).
        r = _record(conn, env, "100.50", "25")
        assert is_ok(r), r
        row = _settlement(conn, r["settlement_id"])
        assert row["attorney_fee"] == "25.13"
        assert row["costs_advanced"] == "0.00"
        assert row["net_to_client"] == "75.37"

    def test_zero_percent_boundary(self, conn, env):
        r = _record(conn, env, "10000.00", "0", "250.00")
        assert is_ok(r), r
        row = _settlement(conn, r["settlement_id"])
        assert row["attorney_fee"] == "0.00"
        assert row["net_to_client"] == "9750.00"

    def test_hundred_percent_boundary(self, conn, env):
        r = _record(conn, env, "10000.00", "100")
        assert is_ok(r), r
        row = _settlement(conn, r["settlement_id"])
        assert row["attorney_fee"] == "10000.00"
        assert row["net_to_client"] == "0.00"

    @staticmethod
    def _assert_refused(conn, audit_before, r, message):
        assert is_error(r), r
        assert r["message"] == message
        assert _count(conn, "legalclaw_settlement") == 0
        assert _count(conn, "audit_log") == audit_before

    def test_refuses_zero_gross(self, conn, env):
        before = _count(conn, "audit_log")
        self._assert_refused(conn, before, _record(conn, env, "0", "30"), MSG_GROSS)

    def test_refuses_negative_gross(self, conn, env):
        before = _count(conn, "audit_log")
        self._assert_refused(conn, before, _record(conn, env, "-5000.00", "30"), MSG_GROSS)

    def test_refuses_negative_percent(self, conn, env):
        before = _count(conn, "audit_log")
        self._assert_refused(conn, before, _record(conn, env, "10000.00", "-1"), MSG_PCT)

    def test_refuses_percent_above_hundred(self, conn, env):
        before = _count(conn, "audit_log")
        self._assert_refused(conn, before, _record(conn, env, "10000.00", "100.01"), MSG_PCT)

    def test_refuses_negative_costs(self, conn, env):
        before = _count(conn, "audit_log")
        self._assert_refused(
            conn, before, _record(conn, env, "10000.00", "30", "-1.00"), MSG_COSTS)

    def test_refuses_negative_net(self, conn, env):
        before = _count(conn, "audit_log")
        self._assert_refused(
            conn, before, _record(conn, env, "10000.00", "30", "8000.00"),
            "Costs advanced (8000.00) exceed the amount left after the attorney fee "
            "(7000.00); net to client would be -1000.00")


# -- legal-calculate-contingency-fee ----------------------------------------


class TestCalculateContingencyFee:

    def test_split_matches_record(self, conn):
        r = _calculate(conn, "100000.00", "33.33", "2500.00")
        assert is_ok(r), r
        assert (r["gross_amount"], r["contingency_pct"], r["attorney_fee"],
                r["costs_advanced"], r["net_to_client"]) == (
            "100000.00", "33.33", "33330.00", "2500.00", "64170.00")

    def test_fee_rounds_half_up(self, conn):
        r = _calculate(conn, "100.50", "25")
        assert (r["attorney_fee"], r["net_to_client"]) == ("25.13", "75.37")

    def test_boundaries(self, conn):
        r0 = _calculate(conn, "10000.00", "0", "250.00")
        assert (r0["attorney_fee"], r0["net_to_client"]) == ("0.00", "9750.00")
        r100 = _calculate(conn, "10000.00", "100")
        assert (r100["attorney_fee"], r100["net_to_client"]) == ("10000.00", "0.00")

    def test_refusals(self, conn):
        cases = [
            (("0", "30", None), MSG_GROSS),
            (("-5000.00", "30", None), MSG_GROSS),
            (("10000.00", "-1", None), MSG_PCT),
            (("10000.00", "150", None), MSG_PCT),
            (("10000.00", "30", "-1.00"), MSG_COSTS),
        ]
        for (gross, pct, costs), message in cases:
            r = _calculate(conn, gross, pct, costs)
            assert is_error(r), (gross, pct, costs, r)
            assert r["message"] == message
        assert _count(conn, "legalclaw_settlement") == 0

    def test_refuses_negative_net(self, conn):
        r = _calculate(conn, "10000.00", "30", "8000.00")
        assert is_error(r), r
        assert r["message"] == (
            "Costs advanced (8000.00) exceed the amount left after the attorney fee "
            "(7000.00); net to client would be -1000.00")
        assert _count(conn, "legalclaw_settlement") == 0


# -- legal-disburse-settlement ----------------------------------------------


class TestDisburseSettlement:

    def test_pending_to_disbursed_then_refused(self, conn, env):
        rec = _record(conn, env, "100000.00", "33.33", "2500.00")
        sid = rec["settlement_id"]
        assert _settlement(conn, sid)["status"] == "pending"
        _fund(conn, env, "100000.00")

        r = call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
            settlement_id=sid, company_id=env["company_id"],
            trust_account_id=env["trust_account_id"]))
        assert is_ok(r), r
        assert r["settlement_status"] == "disbursed"
        row = _settlement(conn, sid)
        assert row["status"] == "disbursed"
        assert (row["attorney_fee"], row["net_to_client"]) == ("33330.00", "64170.00")

        audit_before = _count(conn, "audit_log")
        again = call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
            settlement_id=sid, company_id=env["company_id"]))
        assert is_error(again)
        assert again["message"] == "Settlement is already disbursed"
        assert _settlement(conn, sid)["status"] == "disbursed"
        assert _count(conn, "audit_log") == audit_before

    def test_unknown_settlement_refused(self, conn, env):
        audit_before = _count(conn, "audit_log")
        r = call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
            settlement_id="no-such-settlement", company_id=env["company_id"]))
        assert is_error(r)
        assert r["message"] == "Settlement no-such-settlement not found"
        assert _count(conn, "audit_log") == audit_before


# -- legal-settlement-report ------------------------------------------------


class TestSettlementReport:

    def test_totals_over_created_rows(self, conn, env):
        a = _record(conn, env, "100000.00", "33.33", "2500.00", date="2026-03-02")
        b = _record(conn, env, "100.50", "25", date="2026-03-05")
        _fund(conn, env, "100000.00")
        assert is_ok(call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
            settlement_id=a["settlement_id"], company_id=env["company_id"],
            trust_account_id=env["trust_account_id"])))

        # A settlement in another company must not reach this report.
        other_co = seed_company(conn, name="Other Firm", abbr="OF")
        other_cust = seed_customer(conn, other_co, "Other Client")
        other_ext = seed_client_ext(conn, other_cust, other_co)
        other_matter = seed_matter(conn, other_ext, other_co)
        other = call_action(ACTIONS["legal-record-settlement"], conn, ns(
            company_id=other_co, matter_id=other_matter,
            gross_amount="5000.00", contingency_pct="40",
            settlement_date="2026-03-03"))
        assert is_ok(other), other

        r = call_action(ACTIONS["legal-settlement-report"], conn, ns(company_id=env["company_id"]))
        assert is_ok(r), r
        assert r["settlement_count"] == 2
        assert r["total_gross"] == "100100.50"
        assert r["total_attorney_fees"] == "33355.13"
        assert r["total_costs_advanced"] == "2500.00"
        assert r["total_net_to_clients"] == "64245.37"
        by_id = {s["id"]: s["status"] for s in r["settlements"]}
        assert by_id == {a["settlement_id"]: "disbursed",
                         b["settlement_id"]: "pending"}
