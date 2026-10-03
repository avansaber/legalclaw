"""Audit rows name the right skill, action and entity; matter trust floor.

Every legalclaw audit row stores skill ``legalclaw``, the action name as
``action``, the record's table as ``entity_type`` and the record's id as
``entity_id``. A trust disbursement naming a matter is refused before
anything is written when it exceeds the matter's trust balance.
"""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from legal_helpers import (  # noqa: E402
    call_action, ns, is_ok, is_error, load_db_query,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS

from erpclaw_lib.query import Q, P, Table  # noqa: E402

_audit_log = Table("audit_log")
_matter = Table("legalclaw_matter")

SNAPSHOT_TABLES = (
    "legalclaw_bar_admission",
    "legalclaw_calendar_event",
    "legalclaw_cle_record",
    "legalclaw_client_ext",
    "legalclaw_communication",
    "legalclaw_conflict_check",
    "legalclaw_conflict_waiver",
    "legalclaw_deadline",
    "legalclaw_document",
    "legalclaw_expense",
    "legalclaw_intake",
    "legalclaw_invoice",
    "legalclaw_matter",
    "legalclaw_matter_party",
    "legalclaw_settlement",
    "legalclaw_task_template",
    "legalclaw_task_template_item",
    "legalclaw_time_entry",
    "legalclaw_trust_account",
    "legalclaw_trust_transaction",
    "audit_log",
    "gl_entry",
)


def _snapshot(conn):
    snap = {}
    for name in SNAPSHOT_TABLES:
        tbl = Table(name)
        rows = conn.execute(Q.from_(tbl).select(tbl.star).get_sql()).fetchall()
        snap[name] = sorted(repr(tuple(row)) for row in rows)
    return snap


def _audit_rows(conn, entity_id):
    q = (
        Q.from_(_audit_log)
        .select(
            _audit_log.skill,
            _audit_log.action,
            _audit_log.entity_type,
            _audit_log.entity_id,
        )
        .where(_audit_log.entity_id == P())
    )
    return [
        tuple(row)
        for row in conn.execute(q.get_sql(), (entity_id,)).fetchall()
    ]


def _matter_balance(conn, matter_id):
    q = Q.from_(_matter).select(_matter.trust_balance).where(_matter.id == P())
    return conn.execute(q.get_sql(), (matter_id,)).fetchone()["trust_balance"]


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


def test_disburse_trust_audit_row(conn, env):
    dep = call_action(ACTIONS["legal-deposit-trust"], conn, ns(
        company_id=env["company_id"], trust_account_id=env["trust_account_id"],
        matter_id=env["matter_id"], amount="5000.00",
        transaction_date="2026-03-05"))
    assert is_ok(dep), dep
    r = call_action(ACTIONS["legal-disburse-trust"], conn, ns(
        company_id=env["company_id"], trust_account_id=env["trust_account_id"],
        matter_id=env["matter_id"], amount="1000.00", payee="Expert Witness",
        transaction_date="2026-03-06"))
    assert is_ok(r), r
    assert _audit_rows(conn, r["id"]) == [
        ("legalclaw", "legal-disburse-trust",
         "legalclaw_trust_transaction", r["id"])
    ]


def test_disburse_settlement_audit_rows(conn, env):
    ta_id, sid = _setup(conn, env, "12000.00")
    r = call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
        settlement_id=sid, company_id=env["company_id"],
        trust_account_id=ta_id, transaction_date="2026-03-10"))
    assert is_ok(r), r
    assert len(r["trust_transaction_ids"]) == 3
    for txn_id in r["trust_transaction_ids"]:
        assert _audit_rows(conn, txn_id) == [
            ("legalclaw", "legal-disburse-settlement",
             "legalclaw_trust_transaction", txn_id)
        ]
    assert (
        "legalclaw", "legal-disburse-settlement",
        "legalclaw_settlement", sid,
    ) in _audit_rows(conn, sid)


def test_add_matter_audit_row(conn, env):
    r = call_action(ACTIONS["legal-add-matter"], conn, ns(
        company_id=env["company_id"], client_id=env["client_ext_id"],
        title="Audit Matter", practice_area="litigation"))
    assert is_ok(r), r
    assert _audit_rows(conn, r["id"]) == [
        ("legalclaw", "legal-add-matter", "legalclaw_matter", r["id"])
    ]


def test_disburse_trust_refuses_matter_overdraw(conn, env):
    ta_id = env["trust_account_id"]
    matter_id = env["matter_id"]
    dep = call_action(ACTIONS["legal-deposit-trust"], conn, ns(
        company_id=env["company_id"], trust_account_id=ta_id,
        matter_id=None, amount="5000.00", transaction_date="2026-03-05"))
    assert is_ok(dep), dep
    dep = call_action(ACTIONS["legal-deposit-trust"], conn, ns(
        company_id=env["company_id"], trust_account_id=ta_id,
        matter_id=matter_id, amount="1000.00",
        transaction_date="2026-03-06"))
    assert is_ok(dep), dep
    before = _snapshot(conn)
    r = call_action(ACTIONS["legal-disburse-trust"], conn, ns(
        company_id=env["company_id"], trust_account_id=ta_id,
        matter_id=matter_id, amount="1500.00", payee="Expert Witness",
        transaction_date="2026-03-07"))
    assert is_error(r), r
    assert r["message"] == (
        f"Insufficient trust balance for matter {matter_id}: "
        "1000.00 available, 1500.00 requested"
    )
    assert _snapshot(conn) == before


def test_disburse_trust_matter_balance_to_zero_is_allowed(conn, env):
    ta_id = env["trust_account_id"]
    matter_id = env["matter_id"]
    dep = call_action(ACTIONS["legal-deposit-trust"], conn, ns(
        company_id=env["company_id"], trust_account_id=ta_id,
        matter_id=None, amount="5000.00", transaction_date="2026-03-05"))
    assert is_ok(dep), dep
    dep = call_action(ACTIONS["legal-deposit-trust"], conn, ns(
        company_id=env["company_id"], trust_account_id=ta_id,
        matter_id=matter_id, amount="1000.00",
        transaction_date="2026-03-06"))
    assert is_ok(dep), dep
    r = call_action(ACTIONS["legal-disburse-trust"], conn, ns(
        company_id=env["company_id"], trust_account_id=ta_id,
        matter_id=matter_id, amount="1000.00", payee="Expert Witness",
        transaction_date="2026-03-07"))
    assert is_ok(r), r
    assert _matter_balance(conn, matter_id) == "0.00"
