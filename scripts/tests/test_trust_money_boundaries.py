"""Trust transfers preserve company boundaries and deposits use cents."""
from decimal import Decimal

import pytest

from legal_helpers import (
    call_action, is_error, is_ok, load_db_query, ns,
    seed_account, seed_company, seed_trust_account,
)
from erpclaw_lib.query import P, Q, Table, dynamic_update

ACTIONS = load_db_query().ACTIONS


def _snapshot(conn):
    tables = (
        "legalclaw_trust_account", "legalclaw_trust_transaction",
        "legalclaw_matter", "gl_entry", "audit_log",
    )
    return {
        name: sorted(tuple(row) for row in conn.execute(
            Q.from_(Table(name)).select(Table(name).star).get_sql()
        ).fetchall())
        for name in tables
    }


@pytest.mark.parametrize("boundary", ["same_account", "foreign_destination", "foreign_source"])
def test_transfer_refuses_account_boundary_without_writes(conn, env, boundary):
    source = env["trust_account_id"]
    other_company = seed_company(conn, name="Other Firm", abbr="OF")
    foreign = seed_trust_account(conn, other_company, name="Other Trust")
    local = seed_trust_account(conn, env["company_id"], name="Second Local Trust")
    for account_id in (source, foreign, local):
        sql, params = dynamic_update(
            "legalclaw_trust_account", {"current_balance": "1000.00"},
            where={"id": account_id},
        )
        conn.execute(sql, params)
    conn.commit()
    if boundary == "same_account":
        destination = source
    elif boundary == "foreign_destination":
        destination = foreign
    else:
        source, destination = foreign, local
    before = _snapshot(conn)
    result = call_action(ACTIONS["legal-transfer-trust"], conn, ns(
        company_id=env["company_id"], trust_account_id=source,
        to_trust_account_id=destination, amount="250.00",
        transaction_date="2026-01-20",
    ))
    assert is_error(result), result
    assert _snapshot(conn) == before


@pytest.mark.parametrize("raw,rounded", [("10.005", "10.01"), ("0.005", "0.01")])
def test_deposit_uses_same_cents_in_transaction_balances_and_gl(conn, env, raw, rounded):
    account_id = seed_trust_account(
        conn, env["company_id"], name="Linked Trust",
        gl_account_id=env["trust_bank_acct"],
        trust_liability_account_id=env["trust_liability_acct"],
    )
    transaction = Table("legalclaw_trust_transaction")
    account = Table("legalclaw_trust_account")
    matter = Table("legalclaw_matter")
    gl = Table("gl_entry")
    for _ in range(2):
        result = call_action(ACTIONS["legal-deposit-trust"], conn, ns(
            company_id=env["company_id"], trust_account_id=account_id,
            matter_id=env["matter_id"], amount=raw, transaction_date="2026-01-20",
        ))
        assert is_ok(result), result
        assert result["amount"] == rounded
        rows = conn.execute(Q.from_(gl).select(gl.debit, gl.credit)
                            .where(gl.voucher_id == P()).get_sql(), (result["id"],)).fetchall()
        assert len(rows) == 2
        assert sum(Decimal(row["debit"]) for row in rows) == Decimal(rounded)
        assert sum(Decimal(row["credit"]) for row in rows) == Decimal(rounded)
    amounts = conn.execute(Q.from_(transaction).select(transaction.amount)
                           .where(transaction.trust_account_id == P()).get_sql(), (account_id,)).fetchall()
    assert [row["amount"] for row in amounts] == [rounded, rounded]
    expected = Decimal(rounded) * 2
    stored_account = conn.execute(Q.from_(account).select(account.current_balance)
                                 .where(account.id == P()).get_sql(), (account_id,)).fetchone()
    stored_matter = conn.execute(Q.from_(matter).select(matter.trust_balance)
                                .where(matter.id == P()).get_sql(), (env["matter_id"],)).fetchone()
    assert Decimal(stored_account["current_balance"]) == expected
    assert Decimal(stored_matter["trust_balance"]) == expected


def test_deposit_refuses_amount_that_rounds_to_zero_without_writes(conn, env):
    before = _snapshot(conn)
    result = call_action(ACTIONS["legal-deposit-trust"], conn, ns(
        company_id=env["company_id"], trust_account_id=env["trust_account_id"],
        matter_id=env["matter_id"], amount="0.004", transaction_date="2026-01-20",
    ))
    assert is_error(result), result
    assert _snapshot(conn) == before


def _linked_accounts(conn, env):
    other_bank = seed_account(
        conn, env["company_id"], name="Second Trust Bank",
        root_type="asset", account_type="bank",
    )
    source = seed_trust_account(
        conn, env["company_id"], name="Source Trust",
        gl_account_id=env["trust_bank_acct"],
        trust_liability_account_id=env["trust_liability_acct"],
        interest_income_account_id=env["interest_income_acct"],
    )
    destination = seed_trust_account(
        conn, env["company_id"], name="Destination Trust",
        gl_account_id=other_bank,
        trust_liability_account_id=env["trust_liability_acct"],
    )
    return source, destination, other_bank


def _bank_balance(conn, bank_id):
    gl = Table("gl_entry")
    rows = conn.execute(
        Q.from_(gl).select(gl.debit, gl.credit)
        .where(gl.account_id == P()).where(gl.is_cancelled == 0).get_sql(),
        (bank_id,),
    ).fetchall()
    return sum((Decimal(row["debit"]) - Decimal(row["credit"])
                for row in rows), Decimal("0"))


def _stored_balance(conn, account_id):
    account = Table("legalclaw_trust_account")
    return Decimal(conn.execute(
        Q.from_(account).select(account.current_balance)
        .where(account.id == P()).get_sql(), (account_id,),
    ).fetchone()["current_balance"])


def _trust_action(conn, env, action, source, destination, amount):
    return call_action(ACTIONS[action], conn, ns(
        company_id=env["company_id"], trust_account_id=source,
        to_trust_account_id=destination,
        matter_id=env["matter_id"], amount=amount, payee="Test Client",
        transaction_date="2026-01-20",
    ))


def test_half_cent_deposit_disbursement_then_transfer_has_no_bank_drift(conn, env):
    source, destination, other_bank = _linked_accounts(conn, env)
    for action, amount in (
        ("legal-deposit-trust", "10.005"),
        ("legal-disburse-trust", "10.005"),
        ("legal-deposit-trust", "5.00"),
        ("legal-transfer-trust", "1.00"),
    ):
        result = _trust_action(conn, env, action, source, destination, amount)
        assert is_ok(result), result
    assert _stored_balance(conn, source) == Decimal("4.00")
    assert _bank_balance(conn, env["trust_bank_acct"]) == Decimal("4.00")
    assert _stored_balance(conn, destination) == Decimal("1.00")
    assert _bank_balance(conn, other_bank) == Decimal("1.00")


@pytest.mark.parametrize("action", [
    "legal-disburse-trust", "legal-transfer-trust",
    "legal-trust-interest-distribution",
])
@pytest.mark.parametrize("raw,rounded", [
    ("1.004", "1.00"), ("1.005", "1.01"), ("0.005", "0.01"),
])
def test_all_trust_writers_use_the_same_cents_for_books_and_gl(conn, env, action, raw, rounded):
    source, destination, other_bank = _linked_accounts(conn, env)
    deposit = _trust_action(conn, env, "legal-deposit-trust", source, destination, "5.00")
    assert is_ok(deposit), deposit
    result = _trust_action(conn, env, action, source, destination, raw)
    assert is_ok(result), result
    assert result["amount"] == rounded
    expected = Decimal("5.00") + (
        Decimal(rounded) if action == "legal-trust-interest-distribution"
        else -Decimal(rounded)
    )
    assert _stored_balance(conn, source) == expected
    assert _bank_balance(conn, env["trust_bank_acct"]) == expected
    transaction = Table("legalclaw_trust_transaction")
    rows = conn.execute(
        Q.from_(transaction).select(transaction.amount)
        .where(transaction.trust_account_id == P())
        .where(transaction.transaction_type != "deposit").get_sql(),
        (source,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["amount"] == rounded
    gl = Table("gl_entry")
    legs = conn.execute(
        Q.from_(gl).select(gl.debit, gl.credit)
        .where(gl.id.isin([P() for _ in result["gl_entry_ids"]])).get_sql(),
        tuple(result["gl_entry_ids"]),
    ).fetchall()
    assert len(legs) == 2
    assert sum(Decimal(row["debit"]) for row in legs) == Decimal(rounded)
    assert sum(Decimal(row["credit"]) for row in legs) == Decimal(rounded)
    if action == "legal-transfer-trust":
        assert _stored_balance(conn, destination) == Decimal(rounded)
        assert _bank_balance(conn, other_bank) == Decimal(rounded)
    else:
        matter = Table("legalclaw_matter")
        stored = conn.execute(
            Q.from_(matter).select(matter.trust_balance)
            .where(matter.id == P()).get_sql(), (env["matter_id"],),
        ).fetchone()
        expected_matter = expected if action == "legal-disburse-trust" else Decimal("5.00")
        assert Decimal(stored["trust_balance"]) == expected_matter


@pytest.mark.parametrize("action", [
    "legal-deposit-trust", "legal-disburse-trust", "legal-transfer-trust",
    "legal-trust-interest-distribution",
])
def test_each_trust_action_refuses_rounded_zero_without_writes(conn, env, action):
    source, destination, _ = _linked_accounts(conn, env)
    deposit = _trust_action(conn, env, "legal-deposit-trust", source, destination, "5.00")
    assert is_ok(deposit), deposit
    before = _snapshot(conn)
    result = _trust_action(conn, env, action, source, destination, "0.004")
    assert is_error(result), result
    assert _snapshot(conn) == before
