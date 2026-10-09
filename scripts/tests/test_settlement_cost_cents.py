"""Recorded cost reimbursement agrees with the cents actually disbursed."""
from decimal import Decimal

import pytest

from erpclaw_lib.query import Q, P, Table
from test_settlement_split import _record, _calculate, _settlement
from test_settlement_fee_revenue import _setup_firm, _deposit, _disburse, _account_balance


@pytest.mark.parametrize("raw, expected", [
    ("10.004", "10.00"), ("10.005", "10.01"), ("10.006", "10.01"),
    ("0.005", "0.01"), ("0.004", "0.00"),
])
def test_record_and_preview_use_the_same_rounded_costs(conn, env, raw, expected):
    preview = _calculate(conn, "100.00", "10", raw)
    record = _record(conn, env, "100.00", "10", raw)
    assert preview["status"] == record["status"] == "ok"
    row = _settlement(conn, record["settlement_id"])
    for result in (preview, record, row):
        assert result["costs_advanced"] == expected
        assert Decimal(result["net_to_client"]) == Decimal("90.00") - Decimal(expected)
        assert sum(Decimal(result[key]) for key in
                   ("attorney_fee", "costs_advanced", "net_to_client")) == Decimal("100.00")


def test_tiny_negative_cost_is_refused_before_rounding(conn, env):
    settlement, audit = Table("legalclaw_settlement"), Table("audit_log")
    before = [list(conn.execute(Q.from_(t).select("*").get_sql()).fetchall()) for t in (settlement, audit)]
    assert _record(conn, env, "100.00", "10", "-0.004")["status"] == "error"
    assert [list(conn.execute(Q.from_(t).select("*").get_sql()).fetchall()) for t in (settlement, audit)] == before


def test_full_disbursement_reimburses_only_the_recorded_cents(conn, env):
    context = _setup_firm(conn, env)
    deposited = _deposit(conn, env, context["trust_account_id"], "100.00")
    assert deposited["status"] == "ok"
    recorded = _record(conn, env, "100.00", "10", "10.005")
    assert recorded["status"] == "ok"
    result = _disburse(conn, env, context, recorded["settlement_id"])
    assert result["status"] == "ok", result
    assert result["costs_recovered"] == "10.01"
    assert result["amount_disbursed"] == "100.00"
    assert Decimal(_account_balance(conn, context["costs_recovery"])) == Decimal("-10.01")
    transaction = Table("legalclaw_trust_transaction")
    costs = conn.execute(Q.from_(transaction).select(transaction.amount)
                         .where(transaction.reference == P())
                         .where(transaction.description == P()).get_sql(),
                         (recorded["settlement_id"], "Settlement costs reimbursement")).fetchall()
    assert len(costs) == 1
    assert costs[0]["amount"] == recorded["costs_advanced"] == "10.01"
    assert Decimal(result["new_balance"]) == Decimal("0.00")
