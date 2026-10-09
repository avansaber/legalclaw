"""Trust writers refuse foreign company accounts and matters before writes."""
from decimal import Decimal

import pytest

from legal_helpers import (
    call_action, load_db_query, ns, seed_company, seed_customer,
    seed_client_ext, seed_matter, seed_trust_account,
)
from erpclaw_lib.query import Q, Table, dynamic_update

ACTIONS = load_db_query().ACTIONS


def snapshot(conn):
    return {name: sorted(tuple(row) for row in conn.execute(
        Q.from_(Table(name)).select("*").get_sql()).fetchall())
        for name in ("company", "legalclaw_trust_account", "legalclaw_matter",
                     "legalclaw_trust_transaction", "gl_entry", "audit_log", "naming_series")}


@pytest.fixture
def foreign(conn, env):
    company = seed_company(conn, name="Other Firm", abbr="OF")
    customer = seed_customer(conn, company)
    client = seed_client_ext(conn, customer, company)
    matter = seed_matter(conn, client, company)
    account = seed_trust_account(conn, company)
    for table_name, row_id, field in (
        ("legalclaw_trust_account", account, "current_balance"),
        ("legalclaw_trust_account", env["trust_account_id"], "current_balance"),
        ("legalclaw_matter", matter, "trust_balance"),
        ("legalclaw_matter", env["matter_id"], "trust_balance"),
    ):
        query, values = dynamic_update(table_name, {field: "500.00"}, where={"id": row_id})
        conn.execute(query, values)
    conn.commit()
    return dict(company=company, matter=matter, account=account)


@pytest.mark.parametrize("action", ["legal-deposit-trust", "legal-disburse-trust", "legal-trust-interest-distribution"])
@pytest.mark.parametrize("boundary", ["account", "matter"])
def test_foreign_company_refused_before_any_write(conn, env, foreign, action, boundary):
    before = snapshot(conn)
    args = ns(company_id=env["company_id"], amount="10.01", payee="Test Client",
              trust_account_id=foreign["account"] if boundary == "account" else env["trust_account_id"],
              matter_id=foreign["matter"] if boundary == "matter" else env["matter_id"],
              transaction_date="2026-01-20")
    result = call_action(ACTIONS[action], conn, args)
    assert result["status"] == "error", result
    assert "company" in result["message"].lower()
    assert snapshot(conn) == before
    assert not conn.in_transaction


@pytest.mark.parametrize("action", ["legal-deposit-trust", "legal-disburse-trust", "legal-trust-interest-distribution"])
def test_same_company_controls_keep_exact_balance_behaviour(conn, env, foreign, action):
    result = call_action(ACTIONS[action], conn, ns(
        company_id=env["company_id"], trust_account_id=env["trust_account_id"],
        matter_id=env["matter_id"], amount="10.005", payee="Test Client",
        transaction_date="2026-01-20"))
    assert result["status"] == "ok", result
    assert result["amount"] == "10.01"
    expected = Decimal("489.99") if action == "legal-disburse-trust" else Decimal("510.01")
    assert Decimal(result["new_balance"]) == expected


@pytest.mark.parametrize("boundary", ["account", "matter"])
def test_shared_disbursement_writer_refuses_foreign_scope_before_write(conn, env, foreign, boundary):
    from trust import write_trust_disbursement
    before = snapshot(conn)
    with pytest.raises(ValueError, match="company"):
        write_trust_disbursement(
            conn, foreign["account"] if boundary == "account" else env["trust_account_id"],
            Decimal("10.01"), "Test Client",
            foreign["matter"] if boundary == "matter" else env["matter_id"],
            "2026-01-20", None, None, env["company_id"])
    assert snapshot(conn) == before
    assert not conn.in_transaction
