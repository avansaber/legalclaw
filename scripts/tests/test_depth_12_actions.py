"""L1 depth tests: 12 legalclaw actions proven by database effect, not envelope.

Each action below already had a test that proved the wrong thing -- a
routability probe in testing/integration/contract/test_legalclaw_contract.py
("Unknown action" not in the error) and/or a CRUD smoke probe in
testing/integration/smoke/test_legalclaw_auto.py (routable + listable). Neither
observes the database, so an action could return a perfectly shaped response
while writing nothing, and every one of its tests would pass. The tests in this
file read the rows back from the tables and compare exact values.

Per-action depth signal (stored row vs ledger effect):
- legal-add-client: stored rows (customer + legalclaw_client_ext). No GL legs.
- legal-add-communication: stored row (legalclaw_communication). No GL legs.
- legal-add-intake: stored row (legalclaw_intake). No GL legs.
- legal-add-task-template: stored row (legalclaw_task_template). No GL legs.
- legal-add-task-template-item: stored rows (item + parent task_count). No GL legs.
- legal-apply-task-template: stored rows (legalclaw_deadline x N). No GL legs.
- legal-calculate-contingency-fee: PURE (writes nothing; absence of rows asserted).
- legal-calculate-sol: PURE (writes nothing; absence of rows asserted).
- legal-check-retainer-balance: READ-ONLY (writes nothing; absence asserted).
- legal-communication-summary-report: READ-ONLY (writes nothing; absence asserted).
- legal-communication-timeline: READ-ONLY (writes nothing; absence asserted).
- legal-convert-intake-to-matter: stored + changed rows (matter insert, intake
  status transition). No GL legs.

Ledger note, stated once so no later reader adds an assertion that cannot hold:
none of these 12 actions reaches the ledger. intake.py imports only db,
decimal, response, audit and query helpers (no GL posting call exists on these
paths), and the client path delegates to erpclaw-selling add-customer, which
writes the customer row but posts no entries. Every test below therefore asserts
stored rows (or their absence), never debit/credit legs.

Money discipline: monetary values are TEXT columns holding exact Decimal
renderings; every assertion compares exact strings. No float, no round(), no
approximation.
"""
import argparse
import importlib.util
import io
import json
import os
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from legal_helpers import (  # noqa: E402
    call_action, ns, is_ok, is_error, load_db_query,
    seed_company, seed_naming_series, SRC_DIR,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS

_matters = sys.modules["matters"]


# ---------------------------------------------------------------------------
# Observation helpers (plain COUNT(*) over a fixed table allowlist -- no
# sqlite_master, no PRAGMA, no information_schema anywhere in this file)
# ---------------------------------------------------------------------------

_TRACKED_TABLES = (
    "company",
    "customer",
    "naming_series",
    "legalclaw_client_ext",
    "legalclaw_matter",
    "legalclaw_matter_party",
    "legalclaw_intake",
    "legalclaw_task_template",
    "legalclaw_task_template_item",
    "legalclaw_settlement",
    "legalclaw_communication",
    "legalclaw_deadline",
    "legalclaw_trust_account",
    "legalclaw_trust_transaction",
    "legalclaw_time_entry",
    "legalclaw_expense",
    "legalclaw_invoice",
    "legalclaw_document",
    "legalclaw_calendar_event",
    "audit_log",
)


def _snapshot(conn):
    """Row counts per tracked table; the byte-identity proxy for refusals."""
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in _TRACKED_TABLES
    }


def _assert_counts(before, after, deltas=None):
    """Every tracked table is unchanged except the listed exact deltas."""
    deltas = deltas or {}
    for table, count in before.items():
        expected = count + deltas.get(table, 0)
        assert after[table] == expected, (table, count, after[table])


def _row(conn, table, row_id, columns="*"):
    return conn.execute(
        f"SELECT {columns} FROM {table} WHERE id = ?", (row_id,)
    ).fetchone()


def _fake_exit(code=0):
    raise SystemExit(code)


# ---------------------------------------------------------------------------
# Cross-skill delegation for legal-add-client
# ---------------------------------------------------------------------------

_SELLING = None


def _selling():
    """Load the REAL erpclaw-selling router in-process (explicit path load)."""
    global _SELLING
    if _SELLING is None:
        path = os.path.join(SRC_DIR, "erpclaw", "scripts", "erpclaw-selling", "db_query.py")
        spec = importlib.util.spec_from_file_location("_fnd_selling", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _SELLING = module
    return _SELLING


def _delegate_customer_create(monkeypatch, conn):
    """Redirect matters.create_customer to the genuine add-customer in-process.

    The cross-skill hop shells out to the INSTALLED skill tree, which is neither
    this worktree's code nor this test's database, so the module suite
    historically skipped legal-add-client altogether ("add skipped -- uses
    cross_skill subprocess"). Running the real add-customer against the test
    connection keeps the customer-row assertions honest while still proving what
    legalclaw itself wrote (the ext row). Same idiom as
    test_timebilling._delegate_in_process.
    """
    selling = _selling()

    def _in_process(customer_name, company_id=None, customer_type="company",
                    email=None, phone=None, db_path=None, timeout=30):
        buf = io.StringIO()
        args = argparse.Namespace(
            name=customer_name, company_id=company_id,
            customer_type=customer_type, customer_group=None,
            payment_terms_id=None, credit_limit=None, tax_id=None,
            exempt_from_sales_tax=None, primary_address=None,
            primary_contact=None, email=email, phone=phone,
            default_price_list_id=None, custom_fields=None,
        )
        try:
            with patch("sys.stdout", buf), patch("sys.exit", side_effect=_fake_exit):
                selling.add_customer(conn, args)
        except SystemExit:
            pass
        data = json.loads(buf.getvalue().strip())
        if data.get("status") == "error":
            raise _matters.CrossSkillError(data.get("message", "add-customer failed"))
        return data

    monkeypatch.setattr(_matters, "create_customer", _in_process)


def _seed_trust_balance(conn, company_id, name, balance):
    """Setup-only trust account insert with an explicit TEXT balance.

    Mirrors legal_helpers.seed_trust_account; the helper always writes '0', and
    no action under test sets a balance, so varied balances must be seeded.
    """
    from datetime import datetime, timezone
    ta_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """INSERT INTO legalclaw_trust_account (
               id, naming_series, name, account_type, current_balance,
               gl_account_id, trust_liability_account_id,
               interest_income_account_id, company_id, created_at, updated_at
           ) VALUES (?, ?, ?, 'iolta', ?, NULL, NULL, NULL, ?, ?, ?)""",
        (ta_id, f"LTRS-{ta_id[:6]}", name, balance, company_id, now, now),
    )
    conn.commit()
    return ta_id


# ---------------------------------------------------------------------------
# legal-add-client -- stored rows (customer + ext)
# ---------------------------------------------------------------------------

class TestAddClientDepth:
    def test_writes_customer_and_ext_rows(self, conn, env, monkeypatch):
        _delegate_customer_create(monkeypatch, conn)
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-add-client"], conn, ns(
            company_id=env["company_id"], name="Depth Client",
            client_type="business", email="depth@test.com", phone="555-0111",
            billing_rate="450.00",
        ))
        assert is_ok(result), result

        customer = _row(conn, "customer", result["customer_id"])
        assert customer["name"] == "Depth Client"
        assert customer["customer_type"] == "company"
        assert customer["company_id"] == env["company_id"]

        ext = _row(conn, "legalclaw_client_ext", result["id"])
        assert ext["customer_id"] == result["customer_id"]
        assert ext["client_type"] == "business"
        assert ext["billing_rate"] == "450.00"
        assert Decimal(ext["billing_rate"]) == Decimal("450.00")
        assert ext["is_active"] == 1
        assert ext["company_id"] == env["company_id"]
        assert ext["naming_series"].startswith("LCLI-")

        # naming_series +1: the real add-customer upserts its own series row;
        # audit_log +2: one row each for the customer and the ext insert.
        _assert_counts(before, _snapshot(conn), {
            "customer": 1, "legalclaw_client_ext": 1, "naming_series": 1,
            "audit_log": 2,
        })

    def test_refuses_missing_name_and_writes_nothing(self, conn, env, monkeypatch):
        _delegate_customer_create(monkeypatch, conn)
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-add-client"], conn, ns(
            company_id=env["company_id"],
        ))
        assert is_error(result), result
        assert result["message"] == "--name is required"
        _assert_counts(before, _snapshot(conn))


# ---------------------------------------------------------------------------
# legal-add-intake -- stored row
# ---------------------------------------------------------------------------

class TestAddIntakeDepth:
    def test_writes_intake_row_exactly(self, conn, env):
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-add-intake"], conn, ns(
            company_id=env["company_id"], contact_name="Walker Caller",
            contact_email="walker@test.com", contact_phone="555-0133",
            inquiry_type="consultation", practice_area="family",
            description="Custody consult", urgency="high", source="referral",
        ))
        assert is_ok(result), result
        assert result["intake_status"] == "new"

        row = _row(conn, "legalclaw_intake", result["intake_id"])
        assert row["contact_name"] == "Walker Caller"
        assert row["contact_email"] == "walker@test.com"
        assert row["contact_phone"] == "555-0133"
        assert row["inquiry_type"] == "consultation"
        assert row["practice_area"] == "family"
        assert row["description"] == "Custody consult"
        assert row["urgency"] == "high"
        assert row["source"] == "referral"
        assert row["status"] == "new"
        assert row["conflict_checked"] == 0
        assert row["conflict_result"] is None
        assert row["converted_matter_id"] is None
        assert row["company_id"] == env["company_id"]

        _assert_counts(before, _snapshot(conn), {
            "legalclaw_intake": 1, "audit_log": 1,
        })

    def test_refuses_missing_contact_name_and_writes_nothing(self, conn, env):
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-add-intake"], conn, ns(
            company_id=env["company_id"],
        ))
        assert is_error(result), result
        assert result["message"] == "--contact-name is required"
        _assert_counts(before, _snapshot(conn))


# ---------------------------------------------------------------------------
# legal-convert-intake-to-matter -- stored + changed rows
# ---------------------------------------------------------------------------

class TestConvertIntakeToMatterDepth:
    def _make_intake(self, conn, env):
        result = call_action(ACTIONS["legal-add-intake"], conn, ns(
            company_id=env["company_id"], contact_name="Walker Potential",
            practice_area="family", description="Custody consult",
            assigned_to="R. Rao",
        ))
        assert is_ok(result), result
        return result["intake_id"]

    def test_creates_matter_and_marks_intake_converted(self, conn, env):
        intake_id = self._make_intake(conn, env)
        before = _snapshot(conn)
        intake_before = _row(conn, "legalclaw_intake", intake_id)
        assert intake_before["status"] == "new"

        result = call_action(ACTIONS["legal-convert-intake-to-matter"], conn, ns(
            company_id=env["company_id"], intake_id=intake_id,
            client_id=env["client_ext_id"],
        ))
        assert is_ok(result), result
        assert result["intake_status"] == "converted"
        matter_id = result["matter_id"]

        matter = _row(conn, "legalclaw_matter", matter_id)
        assert matter["title"] == "Matter for Walker Potential"
        assert matter["practice_area"] == "family"
        assert matter["description"] == "Custody consult"
        assert matter["lead_attorney"] == "R. Rao"
        assert matter["billing_method"] == "hourly"
        assert matter["billing_rate"] == "0"
        assert matter["budget"] == "0"
        assert matter["billed_amount"] == "0"
        assert matter["collected_amount"] == "0"
        assert matter["trust_balance"] == "0"
        assert matter["status"] == "active"
        assert matter["client_id"] == env["client_ext_id"]
        assert matter["company_id"] == env["company_id"]
        assert matter["opened_date"] == date.today().isoformat()
        assert matter["closed_date"] is None

        intake_after = _row(conn, "legalclaw_intake", intake_id)
        assert intake_after["status"] == "converted"
        assert intake_after["converted_matter_id"] == matter_id
        assert intake_after["contact_name"] == intake_before["contact_name"]
        assert intake_after["practice_area"] == intake_before["practice_area"]
        assert intake_after["description"] == intake_before["description"]

        _assert_counts(before, _snapshot(conn), {
            "legalclaw_matter": 1, "audit_log": 1,
        })

    def test_refuses_double_convert_and_changes_nothing(self, conn, env):
        intake_id = self._make_intake(conn, env)
        first = call_action(ACTIONS["legal-convert-intake-to-matter"], conn, ns(
            company_id=env["company_id"], intake_id=intake_id,
            client_id=env["client_ext_id"],
        ))
        assert is_ok(first), first

        before = _snapshot(conn)
        again = call_action(ACTIONS["legal-convert-intake-to-matter"], conn, ns(
            company_id=env["company_id"], intake_id=intake_id,
            client_id=env["client_ext_id"],
        ))
        assert is_error(again), again
        assert again["message"] == f"Intake already converted to matter {first['matter_id']}"
        _assert_counts(before, _snapshot(conn))
        row = _row(conn, "legalclaw_intake", intake_id)
        assert (row["status"], row["converted_matter_id"]) == (
            "converted", first["matter_id"])


# ---------------------------------------------------------------------------
# legal-add-task-template -- stored row
# ---------------------------------------------------------------------------

class TestAddTaskTemplateDepth:
    def test_writes_template_row_exactly(self, conn, env):
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-add-task-template"], conn, ns(
            company_id=env["company_id"], name="Litigation Kickoff",
            practice_area="litigation", description="Standard opening tasks",
        ))
        assert is_ok(result), result

        row = _row(conn, "legalclaw_task_template", result["template_id"])
        assert row["name"] == "Litigation Kickoff"
        assert row["practice_area"] == "litigation"
        assert row["description"] == "Standard opening tasks"
        assert row["task_count"] == 0
        assert row["company_id"] == env["company_id"]

        _assert_counts(before, _snapshot(conn), {
            "legalclaw_task_template": 1, "audit_log": 1,
        })

    def test_refuses_missing_name_and_writes_nothing(self, conn, env):
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-add-task-template"], conn, ns(
            company_id=env["company_id"],
        ))
        assert is_error(result), result
        assert result["message"] == "--name is required"
        _assert_counts(before, _snapshot(conn))


# ---------------------------------------------------------------------------
# legal-add-task-template-item -- stored row + parent counter
# ---------------------------------------------------------------------------

class TestAddTaskTemplateItemDepth:
    def _make_template(self, conn, env):
        result = call_action(ACTIONS["legal-add-task-template"], conn, ns(
            company_id=env["company_id"], name="Closing Checklist",
        ))
        assert is_ok(result), result
        return result["template_id"]

    def test_writes_item_and_bumps_task_count(self, conn, env):
        template_id = self._make_template(conn, env)
        assert _row(conn, "legalclaw_task_template", template_id)["task_count"] == 0
        before = _snapshot(conn)

        result = call_action(ACTIONS["legal-add-task-template-item"], conn, ns(
            company_id=env["company_id"], template_id=template_id,
            task_name="File closing statement", description="With the court",
            due_days_offset="14", assigned_role="paralegal", sort_order="2",
        ))
        assert is_ok(result), result

        item = _row(conn, "legalclaw_task_template_item", result["template_item_id"])
        assert item["template_id"] == template_id
        assert item["task_name"] == "File closing statement"
        assert item["description"] == "With the court"
        assert item["due_days_offset"] == 14
        assert item["assigned_role"] == "paralegal"
        assert item["is_required"] == 1
        assert item["sort_order"] == 2

        assert _row(conn, "legalclaw_task_template", template_id)["task_count"] == 1

        _assert_counts(before, _snapshot(conn), {
            "legalclaw_task_template_item": 1, "audit_log": 1,
        })

    def test_refuses_unknown_template_and_writes_nothing(self, conn, env):
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-add-task-template-item"], conn, ns(
            company_id=env["company_id"], template_id="no-such-template",
            task_name="Ghost task",
        ))
        assert is_error(result), result
        assert result["message"] == "Task template no-such-template not found"
        _assert_counts(before, _snapshot(conn))


# ---------------------------------------------------------------------------
# legal-apply-task-template -- stored rows (deadlines)
# ---------------------------------------------------------------------------

class TestApplyTaskTemplateDepth:
    def _make_template_with_items(self, conn, env):
        template = call_action(ACTIONS["legal-add-task-template"], conn, ns(
            company_id=env["company_id"], name="Discovery Sprint",
        ))
        assert is_ok(template), template
        template_id = template["template_id"]
        first = call_action(ACTIONS["legal-add-task-template-item"], conn, ns(
            company_id=env["company_id"], template_id=template_id,
            task_name="Send interrogatories", description="First wave",
            due_days_offset="0", assigned_role="associate", sort_order="1",
        ))
        second = call_action(ACTIONS["legal-add-task-template-item"], conn, ns(
            company_id=env["company_id"], template_id=template_id,
            task_name="Review responses", description="Second wave",
            due_days_offset="7", assigned_role="partner", sort_order="2",
        ))
        assert is_ok(first) and is_ok(second), (first, second)
        return template_id

    def test_creates_one_deadline_per_item(self, conn, env):
        template_id = self._make_template_with_items(conn, env)
        before = _snapshot(conn)
        today = date.today()
        expected = {
            "Send interrogatories": (today.isoformat(), "associate", "First wave"),
            "Review responses": ((today + timedelta(days=7)).isoformat(), "partner", "Second wave"),
        }

        result = call_action(ACTIONS["legal-apply-task-template"], conn, ns(
            company_id=env["company_id"], template_id=template_id,
            matter_id=env["matter_id"],
        ))
        assert is_ok(result), result
        assert result["deadlines_created"] == 2

        rows = conn.execute(
            "SELECT id, matter_id, title, deadline_type, due_date, "
            "assigned_to, is_completed, notes, company_id "
            "FROM legalclaw_deadline WHERE matter_id = ? ORDER BY due_date ASC",
            (env["matter_id"],),
        ).fetchall()
        assert len(rows) == 2
        for row in rows:
            due, role, notes = expected[row["title"]]
            assert row["due_date"] == due
            assert row["deadline_type"] == "filing"
            assert row["assigned_to"] == role
            assert row["notes"] == notes
            assert row["is_completed"] == 0
            assert row["company_id"] == env["company_id"]

        assert _row(conn, "legalclaw_task_template", template_id)["task_count"] == 2

        _assert_counts(before, _snapshot(conn), {
            "legalclaw_deadline": 2, "audit_log": 1,
        })

    def test_refuses_empty_template_and_writes_nothing(self, conn, env):
        template = call_action(ACTIONS["legal-add-task-template"], conn, ns(
            company_id=env["company_id"], name="Empty Template",
        ))
        template_id = template["template_id"]
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-apply-task-template"], conn, ns(
            company_id=env["company_id"], template_id=template_id,
            matter_id=env["matter_id"],
        ))
        assert is_error(result), result
        assert result["message"] == f"Template {template_id} has no items"
        _assert_counts(before, _snapshot(conn))


# ---------------------------------------------------------------------------
# legal-calculate-contingency-fee -- PURE (no writes; absence asserted)
# ---------------------------------------------------------------------------

class TestCalculateContingencyFeeDepth:
    def test_exact_split_and_writes_nothing(self, conn, env):
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-calculate-contingency-fee"], conn, ns(
            gross_amount="100000.00", contingency_pct="33.33",
            costs_advanced="2500.00",
        ))
        assert is_ok(result), result
        assert result["gross_amount"] == "100000.00"
        assert result["contingency_pct"] == "33.33"
        assert result["attorney_fee"] == "33330.00"
        assert result["costs_advanced"] == "2500.00"
        assert result["net_to_client"] == "64170.00"
        assert Decimal(result["attorney_fee"]) == Decimal("33330.00")
        assert Decimal(result["net_to_client"]) == Decimal("64170.00")
        # Pure calculation: no settlement row, not even an audit row.
        _assert_counts(before, _snapshot(conn))

    def test_refuses_zero_gross_and_changes_nothing(self, conn, env):
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-calculate-contingency-fee"], conn, ns(
            gross_amount="0", contingency_pct="30",
        ))
        assert is_error(result), result
        assert result["message"] == "--gross-amount must be greater than zero"
        _assert_counts(before, _snapshot(conn))


# ---------------------------------------------------------------------------
# legal-calculate-sol -- PURE (no writes; absence asserted)
# ---------------------------------------------------------------------------

class TestCalculateSolDepth:
    def test_exact_expiry_and_writes_nothing(self, conn, env):
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-calculate-sol"], conn, ns(
            jurisdiction="CA", claim_type="personal_injury",
            incident_date="2024-01-01",
        ))
        assert is_ok(result), result
        assert result["jurisdiction"] == "CA"
        assert result["claim_type"] == "personal_injury"
        assert result["incident_date"] == "2024-01-01"
        assert result["sol_years"] == 2
        assert result["sol_expiry_date"] == "2025-12-31"
        assert result["is_expired"] is True
        assert result["days_remaining"] == 0
        # Pure calculation: nothing stored, not even an audit row.
        _assert_counts(before, _snapshot(conn))

    def test_refuses_unknown_claim_type_and_changes_nothing(self, conn, env):
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-calculate-sol"], conn, ns(
            jurisdiction="CA", claim_type="alchemy",
            incident_date="2024-01-01",
        ))
        assert is_error(result), result
        assert result["message"] == (
            "Unknown claim type: alchemy. Supported: contract_written, "
            "contract_oral, personal_injury, property_damage, fraud, "
            "malpractice, debt_collection"
        )
        _assert_counts(before, _snapshot(conn))


# ---------------------------------------------------------------------------
# legal-check-retainer-balance -- READ-ONLY (no writes; absence asserted)
# ---------------------------------------------------------------------------

class TestCheckRetainerBalanceDepth:
    def test_flags_only_accounts_below_threshold(self, conn, env):
        low_id = _seed_trust_balance(conn, env["company_id"], "Small Retainer", "250.75")
        high_id = _seed_trust_balance(conn, env["company_id"], "Flush Retainer", "1500.00")
        before = _snapshot(conn)

        result = call_action(ACTIONS["legal-check-retainer-balance"], conn, ns(
            company_id=env["company_id"], amount="1000.00",
        ))
        assert is_ok(result), result
        assert result["threshold"] == "1000.00"
        assert result["accounts_below_threshold"] == 2

        by_id = {account["id"]: account for account in result["accounts"]}
        # The seeded '0'-balance env account and the 250.75 account are below;
        # the 1500.00 account must NOT be reported.
        assert high_id not in by_id
        assert by_id[env["trust_account_id"]]["current_balance"] == "0"
        assert by_id[env["trust_account_id"]]["deficit"] == "1000.00"
        assert by_id[low_id]["current_balance"] == "250.75"
        assert by_id[low_id]["deficit"] == "749.25"
        assert Decimal(by_id[low_id]["deficit"]) == Decimal("749.25")
        # Lowest balance first.
        assert result["accounts"][0]["id"] == env["trust_account_id"]

        # Read-only: balances and audit trail untouched by the check itself.
        _assert_counts(before, _snapshot(conn))
        assert _row(conn, "legalclaw_trust_account", low_id)["current_balance"] == "250.75"

    def test_refuses_missing_company_and_changes_nothing(self, conn, env):
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-check-retainer-balance"], conn, ns(
            amount="1000.00",
        ))
        assert is_error(result), result
        assert result["message"] == "--company-id is required"
        _assert_counts(before, _snapshot(conn))


# ---------------------------------------------------------------------------
# legal-add-communication -- stored row
# ---------------------------------------------------------------------------

class TestAddCommunicationDepth:
    def test_writes_communication_row_exactly(self, conn, env):
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-add-communication"], conn, ns(
            company_id=env["company_id"], matter_id=env["matter_id"],
            client_id=env["client_ext_id"], comm_type="email",
            direction="outbound", subject="Fee update", summary="Sent invoice",
            duration_minutes="45", participants="jane@test.com",
            comm_date="2026-03-04", logged_by="R. Rao",
        ))
        assert is_ok(result), result

        row = _row(conn, "legalclaw_communication", result["communication_id"])
        assert row["matter_id"] == env["matter_id"]
        assert row["client_id"] == env["client_ext_id"]
        assert row["comm_type"] == "email"
        assert row["direction"] == "outbound"
        assert row["subject"] == "Fee update"
        assert row["summary"] == "Sent invoice"
        assert row["duration_minutes"] == 45
        assert row["participants"] == "jane@test.com"
        assert row["date"] == "2026-03-04"
        assert row["logged_by"] == "R. Rao"
        assert row["company_id"] == env["company_id"]

        _assert_counts(before, _snapshot(conn), {
            "legalclaw_communication": 1, "audit_log": 1,
        })

    def test_refuses_bad_comm_type_and_writes_nothing(self, conn, env):
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-add-communication"], conn, ns(
            company_id=env["company_id"], matter_id=env["matter_id"],
            comm_type="pigeon",
        ))
        assert is_error(result), result
        assert result["message"] == (
            "Invalid comm type: pigeon. Must be one of: "
            "email, phone, meeting, letter, text, portal"
        )
        _assert_counts(before, _snapshot(conn))


# ---------------------------------------------------------------------------
# legal-communication-timeline -- READ-ONLY (no writes; absence asserted)
# ---------------------------------------------------------------------------

class TestCommunicationTimelineDepth:
    def _log(self, conn, env, subject, comm_date, comm_type="email"):
        result = call_action(ACTIONS["legal-add-communication"], conn, ns(
            company_id=env["company_id"], matter_id=env["matter_id"],
            comm_type=comm_type, direction="outbound",
            subject=subject, comm_date=comm_date,
        ))
        assert is_ok(result), result

    def test_returns_date_ordered_timeline(self, conn, env):
        self._log(conn, env, "third", "2026-03-03")
        self._log(conn, env, "first", "2026-03-01", comm_type="phone")
        self._log(conn, env, "second", "2026-03-02")
        before = _snapshot(conn)

        result = call_action(ACTIONS["legal-communication-timeline"], conn, ns(
            matter_id=env["matter_id"],
        ))
        assert is_ok(result), result
        assert result["matter_id"] == env["matter_id"]
        assert result["total_communications"] == 3
        assert [entry["subject"] for entry in result["timeline"]] == [
            "first", "second", "third"]
        assert [entry["date"] for entry in result["timeline"]] == [
            "2026-03-01", "2026-03-02", "2026-03-03"]
        assert result["timeline"][0]["comm_type"] == "phone"
        for entry in result["timeline"]:
            assert entry["matter_id"] == env["matter_id"]
            assert entry["company_id"] == env["company_id"]

        # Read-only: the timeline itself stores nothing.
        _assert_counts(before, _snapshot(conn))

    def test_refuses_missing_matter_and_changes_nothing(self, conn, env):
        before = _snapshot(conn)
        result = call_action(ACTIONS["legal-communication-timeline"], conn, ns())
        assert is_error(result), result
        assert result["message"] == "--matter-id is required"
        _assert_counts(before, _snapshot(conn))


# ---------------------------------------------------------------------------
# legal-communication-summary-report -- READ-ONLY (no writes; absence asserted)
# ---------------------------------------------------------------------------

class TestCommunicationSummaryReportDepth:
    def test_aggregates_exactly_what_was_logged(self, conn, env):
        logs = [
            ("email", "outbound"), ("email", "outbound"),
            ("email", "inbound"), ("phone", "inbound"),
        ]
        for index, (comm_type, direction) in enumerate(logs):
            result = call_action(ACTIONS["legal-add-communication"], conn, ns(
                company_id=env["company_id"], matter_id=env["matter_id"],
                comm_type=comm_type, direction=direction,
                subject=f"note-{index}", comm_date=f"2026-03-0{index + 1}",
            ))
            assert is_ok(result), result
        before = _snapshot(conn)

        result = call_action(
            ACTIONS["legal-communication-summary-report"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["company_id"] == env["company_id"]
        assert result["total_communications"] == 4
        assert result["by_type"] == {"email": 3, "phone": 1}
        assert result["by_direction"] == {"outbound": 2, "inbound": 2}

        # Read-only: the report stores nothing.
        _assert_counts(before, _snapshot(conn))

    def test_refuses_missing_company_and_changes_nothing(self, conn, env):
        before = _snapshot(conn)
        result = call_action(
            ACTIONS["legal-communication-summary-report"], conn, ns())
        assert is_error(result), result
        assert result["message"] == "--company-id is required"
        _assert_counts(before, _snapshot(conn))
