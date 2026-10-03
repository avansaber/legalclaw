"""L1 depth tests (m448): behavioural evidence for six portal/intake/settlement actions.

Each action below already had a test that proved the wrong thing — the
contract suite (testing/integration/contract/test_legalclaw_contract.py) only
proves routability ("Unknown action" not in error), and for
legal-record-settlement / legal-settlement-report the split-arithmetic file
(test_settlement_split.py) already proves the stored split. What was missing:

- legal-portal-send-message: no module test at all; only a routability probe.
- legal-portal-upload-document: no module test at all; only a routability probe.
- legal-update-intake: no module test at all; only a routability probe.
- legal-set-retainer-threshold: no module test at all; only a routability probe.
- legal-record-settlement: covered for the split, but not for ledger
  non-reach (no gl_entry legs may exist) nor for matter-row immutability.
- legal-settlement-report: covered for totals, but not for read-only-ness
  (byte-identical DB afterwards) nor for refusal.

Every effect test below reads the stored row(s) back and compares exact
values; every refusal test asserts the truthful message AND a byte-identical
database afterwards. Money is text: exact Decimal strings, never float.

Ledger note (applies to all six): none of these actions reaches the general
ledger — no gl_entry rows are written by any of them — so no two-leg balance
assertion can hold here. The tests pin gl_entry count unchanged instead.

FINDING (legal-set-retainer-threshold): the action persists nothing on the
trust account row — the schema has no threshold/minimum_balance column, so the
threshold lives only in one audit_log.new_values JSON blob and in the echoed
response. TestRetainerThreshold documents that real behaviour and is marked
accordingly; it is deliberately not fixed here.
"""
import json
import os
import sys
from datetime import date
from decimal import Decimal

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from legal_helpers import (  # noqa: E402
    call_action, ns, is_ok, is_error, load_db_query,
    seed_company, seed_customer, seed_client_ext, seed_matter,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS


def _snapshot(conn, tables):
    """Byte-identical dump of the given tables, ordered by id."""
    return {
        table: [tuple(row) for row in conn.execute(
            "SELECT * FROM %s ORDER BY id" % table).fetchall()]
        for table in tables
    }


def _count(conn, table):
    return conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]


# -- legal-portal-send-message (stored row) ---------------------------------

class TestPortalSendMessage:
    """legal-portal-send-message writes one legalclaw_communication row."""

    def test_message_is_stored_exactly(self, conn, env):
        gl_before = _count(conn, "gl_entry")
        audit_before = _count(conn, "audit_log")
        matter_before = _snapshot(conn, ["legalclaw_matter"])

        r = call_action(ACTIONS["legal-portal-send-message"], conn, ns(
            company_id=env["company_id"], matter_id=env["matter_id"],
            client_id=env["client_ext_id"],
            subject="When is the hearing?",
            description="Client asks for the hearing date."))
        assert is_ok(r), r
        assert r["direction"] == "inbound"
        assert r["type"] == "portal"

        row = conn.execute(
            "SELECT matter_id, client_id, comm_type, direction, subject, "
            "summary, duration_minutes, participants, date, logged_by, "
            "company_id FROM legalclaw_communication WHERE id = ?",
            (r["communication_id"],)).fetchone()
        assert row is not None
        assert tuple(row) == (
            env["matter_id"], env["client_ext_id"], "portal", "inbound",
            "When is the hearing?", "Client asks for the hearing date.",
            None, None, date.today().isoformat(), "client_portal",
            env["company_id"])
        assert _count(conn, "legalclaw_communication") == 1
        # The matter the message belongs to is unchanged.
        assert _snapshot(conn, ["legalclaw_matter"]) == matter_before
        # This action writes no audit row and does not reach the ledger.
        assert _count(conn, "audit_log") == audit_before
        assert _count(conn, "gl_entry") == gl_before

    def test_refuses_missing_subject_without_writing(self, conn, env):
        before = _snapshot(conn, ["legalclaw_communication", "audit_log"])
        r = call_action(ACTIONS["legal-portal-send-message"], conn, ns(
            company_id=env["company_id"], matter_id=env["matter_id"],
            description="No subject here."))
        assert is_error(r), r
        assert r["message"] == "--subject is required"
        assert _snapshot(conn, ["legalclaw_communication", "audit_log"]) == before


# -- legal-portal-upload-document (stored row) -------------------------------

class TestPortalUploadDocument:
    """legal-portal-upload-document writes one legalclaw_document row."""

    def test_upload_is_stored_exactly(self, conn, env):
        gl_before = _count(conn, "gl_entry")
        audit_before = _count(conn, "audit_log")

        r = call_action(ACTIONS["legal-portal-upload-document"], conn, ns(
            company_id=env["company_id"], matter_id=env["matter_id"],
            doc_title="Client photo evidence",
            file_name="photo.jpg", content="binary-placeholder"))
        assert is_ok(r), r
        assert r["matter_id"] == env["matter_id"]
        assert r["title"] == "Client photo evidence"
        assert r["document_status"] == "draft"

        row = conn.execute(
            "SELECT matter_id, title, document_type, file_name, content, "
            "version, status, company_id FROM legalclaw_document WHERE id = ?",
            (r["document_id"],)).fetchone()
        assert row is not None
        assert tuple(row) == (
            env["matter_id"], "Client photo evidence", "general",
            "photo.jpg", "binary-placeholder", "1", "draft",
            env["company_id"])
        assert _count(conn, "legalclaw_document") == 1
        # This action writes no audit row and does not reach the ledger.
        assert _count(conn, "audit_log") == audit_before
        assert _count(conn, "gl_entry") == gl_before

    def test_refuses_missing_title_without_writing(self, conn, env):
        before = _snapshot(conn, ["legalclaw_document", "audit_log"])
        r = call_action(ACTIONS["legal-portal-upload-document"], conn, ns(
            company_id=env["company_id"], matter_id=env["matter_id"]))
        assert is_error(r), r
        assert r["message"] == "--doc-title is required"
        assert _snapshot(conn, ["legalclaw_document", "audit_log"]) == before


# -- legal-record-settlement (stored row) ------------------------------------
# The split arithmetic itself is pinned in test_settlement_split.py; these
# tests pin what that file does not: the matter row is untouched, the ledger
# is untouched (no legs, so no balance to assert), and a refusal is
# byte-identical across every table the action could reach.

class TestRecordSettlementDepth:
    """legal-record-settlement stores the split without touching matter/ledger."""

    def test_record_leaves_matter_and_ledger_untouched(self, conn, env):
        matter_before = _snapshot(conn, ["legalclaw_matter"])
        gl_before = _snapshot(conn, ["gl_entry"])

        r = call_action(ACTIONS["legal-record-settlement"], conn, ns(
            company_id=env["company_id"], matter_id=env["matter_id"],
            gross_amount="60000.00", contingency_pct="25",
            costs_advanced="1500.00", settlement_date="2026-04-01",
            payment_method="check"))
        assert is_ok(r), r
        assert r["attorney_fee"] == "15000.00"
        assert r["net_to_client"] == "43500.00"

        row = conn.execute(
            "SELECT gross_amount, contingency_pct, attorney_fee, "
            "costs_advanced, net_to_client, status FROM legalclaw_settlement "
            "WHERE id = ?", (r["settlement_id"],)).fetchone()
        # Money is text: exact strings, cross-checked with Decimal arithmetic.
        assert tuple(row) == ("60000.00", "25", "15000.00", "1500.00",
                              "43500.00", "pending")
        assert (Decimal(row["attorney_fee"]) + Decimal(row["costs_advanced"])
                + Decimal(row["net_to_client"]) == Decimal(row["gross_amount"]))
        # The matter row is unchanged (from what to what: identical).
        assert _snapshot(conn, ["legalclaw_matter"]) == matter_before
        # This action does not reach the general ledger: no legs exist, so no
        # two-leg balance assertion can hold; the ledger must be identical.
        assert _snapshot(conn, ["gl_entry"]) == gl_before

    def test_refuses_zero_gross_without_writing(self, conn, env):
        before = _snapshot(conn, ["legalclaw_settlement", "legalclaw_matter",
                                  "audit_log", "gl_entry"])
        r = call_action(ACTIONS["legal-record-settlement"], conn, ns(
            company_id=env["company_id"], matter_id=env["matter_id"],
            gross_amount="0", contingency_pct="30"))
        assert is_error(r), r
        assert r["message"] == "--gross-amount must be greater than zero"
        assert _snapshot(conn, ["legalclaw_settlement", "legalclaw_matter",
                                "audit_log", "gl_entry"]) == before


# -- legal-set-retainer-threshold (stored row: the row does NOT change) -------
# FINDING, deliberately not fixed: legalclaw_trust_account has no threshold /
# minimum_balance column, so this action persists nothing on the row. The
# threshold is recorded only in one audit_log.new_values blob and echoed in
# the response. The test below pins that real behaviour: row byte-identical,
# exactly one audit row with the exact threshold JSON.

class TestRetainerThreshold:
    """legal-set-retainer-threshold records the threshold in audit only."""

    def test_threshold_lands_in_audit_not_on_row(self, conn, env):
        row_before = conn.execute(
            "SELECT * FROM legalclaw_trust_account WHERE id = ?",
            (env["trust_account_id"],)).fetchone()
        assert row_before is not None
        assert row_before["current_balance"] == "0"
        gl_before = _count(conn, "gl_entry")
        audit_before = _count(conn, "audit_log")

        r = call_action(ACTIONS["legal-set-retainer-threshold"], conn, ns(
            company_id=env["company_id"],
            trust_account_id=env["trust_account_id"], amount="5000.00"))
        assert is_ok(r), r
        assert r["minimum_balance_threshold"] == "5000.00"
        assert Decimal(r["minimum_balance_threshold"]) == Decimal("5000.00")
        assert r["current_balance"] == "0"

        row_after = conn.execute(
            "SELECT * FROM legalclaw_trust_account WHERE id = ?",
            (env["trust_account_id"],)).fetchone()
        # FINDING: the trust account row is byte-identical — the threshold is
        # not stored on it (there is no column for it in the schema).
        assert tuple(row_after) == tuple(row_before)
        # Exactly one audit row carries the threshold, as exact text.
        assert _count(conn, "audit_log") == audit_before + 1
        audit_row = conn.execute(
            "SELECT new_values FROM audit_log ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert json.loads(audit_row["new_values"]) == {
            "minimum_balance": "5000.00"}
        # No ledger movement for setting a threshold.
        assert _count(conn, "gl_entry") == gl_before

    def test_refuses_unknown_account_without_writing(self, conn, env):
        before = _snapshot(conn, ["legalclaw_trust_account", "audit_log"])
        r = call_action(ACTIONS["legal-set-retainer-threshold"], conn, ns(
            company_id=env["company_id"],
            trust_account_id="no-such-trust-account", amount="5000.00"))
        assert is_error(r), r
        assert r["message"] == "Trust account no-such-trust-account not found"
        assert _snapshot(conn, ["legalclaw_trust_account", "audit_log"]) == before


# -- legal-settlement-report (read-only aggregate over stored rows) -----------
# The totals arithmetic is pinned in test_settlement_split.py; these tests pin
# what that file does not: the report writes nothing (byte-identical DB) and
# refuses cleanly for a missing/unknown company.

class TestSettlementReportDepth:
    """legal-settlement-report aggregates stored rows and writes nothing."""

    def _seed_two_settlements(self, conn, env):
        a = call_action(ACTIONS["legal-record-settlement"], conn, ns(
            company_id=env["company_id"], matter_id=env["matter_id"],
            gross_amount="60000.00", contingency_pct="25",
            costs_advanced="1500.00", settlement_date="2026-04-01"))
        assert is_ok(a), a
        b = call_action(ACTIONS["legal-record-settlement"], conn, ns(
            company_id=env["company_id"], matter_id=env["matter_id"],
            gross_amount="100.50", contingency_pct="25",
            settlement_date="2026-04-05"))
        assert is_ok(b), b
        dep = call_action(ACTIONS["legal-deposit-trust"], conn, ns(
            company_id=env["company_id"], trust_account_id=env["trust_account_id"],
            matter_id=env["matter_id"], amount="60000.00",
            transaction_date="2026-03-01"))
        assert is_ok(dep), dep
        dis = call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
            settlement_id=a["settlement_id"], company_id=env["company_id"],
            trust_account_id=env["trust_account_id"]))
        assert is_ok(dis), dis
        # A settlement in another company must not leak into this report.
        other_co = seed_company(conn, name="Other Firm", abbr="OF")
        other_cust = seed_customer(conn, other_co, "Other Client")
        other_ext = seed_client_ext(conn, other_cust, other_co)
        other_matter = seed_matter(conn, other_ext, other_co)
        other = call_action(ACTIONS["legal-record-settlement"], conn, ns(
            company_id=other_co, matter_id=other_matter,
            gross_amount="5000.00", contingency_pct="40",
            settlement_date="2026-04-03"))
        assert is_ok(other), other
        return a["settlement_id"], b["settlement_id"]

    def test_report_aggregates_stored_rows_and_writes_nothing(self, conn, env):
        sid_a, sid_b = self._seed_two_settlements(conn, env)
        before = _snapshot(conn, ["legalclaw_settlement", "legalclaw_matter",
                                  "audit_log", "gl_entry"])

        r = call_action(ACTIONS["legal-settlement-report"], conn, ns(
            company_id=env["company_id"]))
        assert is_ok(r), r
        assert r["settlement_count"] == 2
        # Money is text: exact strings over the rows seeded above.
        assert r["total_gross"] == "60100.50"
        assert r["total_attorney_fees"] == "15025.13"
        assert r["total_costs_advanced"] == "1500.00"
        assert r["total_net_to_clients"] == "43575.37"
        by_id = {s["id"]: s["status"] for s in r["settlements"]}
        assert by_id == {sid_a: "disbursed", sid_b: "pending"}
        # Read-only: the report wrote no row, changed no row, audited nothing,
        # and never reached the ledger.
        assert _snapshot(conn, ["legalclaw_settlement", "legalclaw_matter",
                                "audit_log", "gl_entry"]) == before

    def test_refuses_unknown_company_without_writing(self, conn, env):
        self._seed_two_settlements(conn, env)
        before = _snapshot(conn, ["legalclaw_settlement", "legalclaw_matter",
                                  "audit_log", "gl_entry"])
        r = call_action(ACTIONS["legal-settlement-report"], conn, ns(
            company_id="no-such-company"))
        assert is_error(r), r
        assert r["message"] == "Company no-such-company not found"
        assert _snapshot(conn, ["legalclaw_settlement", "legalclaw_matter",
                                "audit_log", "gl_entry"]) == before


# -- legal-update-intake (stored row) ------------------------------------------

class TestUpdateIntake:
    """legal-update-intake changes exactly the given fields on the intake row."""

    def _add_intake(self, conn, env):
        r = call_action(ACTIONS["legal-add-intake"], conn, ns(
            company_id=env["company_id"], contact_name="M448 Caller",
            contact_email="caller@example.com", urgency="normal",
            practice_area="litigation", description="Slip and fall"))
        assert is_ok(r), r
        return r["intake_id"]

    def test_update_changes_exactly_the_given_fields(self, conn, env):
        i_id = self._add_intake(conn, env)
        row_before = conn.execute(
            "SELECT * FROM legalclaw_intake WHERE id = ?", (i_id,)).fetchone()
        gl_before = _count(conn, "gl_entry")
        audit_before = _count(conn, "audit_log")

        r = call_action(ACTIONS["legal-update-intake"], conn, ns(
            company_id=env["company_id"], intake_id=i_id,
            contact_phone="555-0148", urgency="high",
            intake_status="qualified"))
        assert is_ok(r), r
        assert sorted(r["updated_fields"]) == ["contact_phone", "status", "urgency"]

        row = conn.execute(
            "SELECT contact_name, contact_email, contact_phone, urgency, "
            "status, practice_area, description, company_id "
            "FROM legalclaw_intake WHERE id = ?", (i_id,)).fetchone()
        # From what to what: phone None -> "555-0148", normal -> high,
        # new -> qualified; everything else identical to before.
        assert tuple(row) == (
            "M448 Caller", "caller@example.com", "555-0148", "high",
            "qualified", "litigation", "Slip and fall", env["company_id"])
        assert row["contact_name"] == row_before["contact_name"]
        assert row["contact_email"] == row_before["contact_email"]
        assert row["company_id"] == row_before["company_id"]
        assert _count(conn, "legalclaw_intake") == 1
        assert _count(conn, "audit_log") == audit_before + 1
        # Intake updates never reach the ledger.
        assert _count(conn, "gl_entry") == gl_before

    def test_refuses_bad_status_without_writing(self, conn, env):
        i_id = self._add_intake(conn, env)
        row_before = conn.execute(
            "SELECT * FROM legalclaw_intake WHERE id = ?", (i_id,)).fetchone()
        audit_before = _count(conn, "audit_log")

        r = call_action(ACTIONS["legal-update-intake"], conn, ns(
            company_id=env["company_id"], intake_id=i_id,
            intake_status="bogus"))
        assert is_error(r), r
        assert r["message"] == "Invalid status: bogus"
        assert tuple(conn.execute(
            "SELECT * FROM legalclaw_intake WHERE id = ?", (i_id,)).fetchone()
        ) == tuple(row_before)
        assert _count(conn, "audit_log") == audit_before
