"""L1 tests for LegalClaw time & billing domain.

Covers:
  - Time entries: add, update, list
  - Expenses: add, update, list
  - Invoice: generate, get, list, send, record-payment, write-off
  - Reports: billable-utilization, ar-aging
"""
import pytest
import sys
import os

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from legal_helpers import (
    call_action, ns, is_ok, is_error, load_db_query,
    seed_time_entry, seed_expense,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS


# ── Time Entry Tests ───────────────────────────────────────────────────


class TestAddTimeEntry:
    """legal-add-time-entry"""

    def test_add_time_entry_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-time-entry"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                attorney="J. Smith",
                te_description="Legal research on precedent cases",
                hours="2.5",
                rate="300.00",
            ),
        )
        assert is_ok(result), result
        assert result["attorney"] == "J. Smith"
        assert result["hours"] == "2.5"
        assert result["rate"] == "300.00"
        from decimal import Decimal
        assert Decimal(result["amount"]) == Decimal("750")
        assert result["is_billable"] == 1

    def test_add_time_entry_non_billable(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-time-entry"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                attorney="J. Smith",
                te_description="Pro bono work",
                hours="1.0",
                is_billable="0",
            ),
        )
        assert is_ok(result), result
        assert result["is_billable"] == 0

    def test_add_time_entry_uses_matter_rate(self, conn, env):
        """When no explicit rate, should fall back to matter billing_rate."""
        result = call_action(
            ACTIONS["legal-add-time-entry"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                attorney="A. Jones",
                te_description="Deposition preparation",
                hours="1.0",
            ),
        )
        assert is_ok(result), result
        # Matter billing_rate is 300.00
        assert result["rate"] == "300.00"
        from decimal import Decimal
        assert Decimal(result["amount"]) == Decimal("300")

    def test_add_time_entry_missing_attorney(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-time-entry"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                te_description="Work",
                hours="1.0",
            ),
        )
        assert is_error(result)

    def test_add_time_entry_missing_description(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-time-entry"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                attorney="J. Smith",
                hours="1.0",
            ),
        )
        assert is_error(result)


class TestUpdateTimeEntry:
    """legal-update-time-entry"""

    def test_update_time_entry_hours(self, conn, env):
        te_id = seed_time_entry(conn, env["matter_id"], env["company_id"])
        result = call_action(
            ACTIONS["legal-update-time-entry"], conn,
            ns(time_entry_id=te_id, hours="3.0"),
        )
        assert is_ok(result), result
        assert "hours" in result["updated_fields"]
        assert "amount" in result["updated_fields"]

    def test_update_time_entry_rate(self, conn, env):
        te_id = seed_time_entry(conn, env["matter_id"], env["company_id"])
        result = call_action(
            ACTIONS["legal-update-time-entry"], conn,
            ns(time_entry_id=te_id, rate="400.00"),
        )
        assert is_ok(result), result
        assert "rate" in result["updated_fields"]

    def test_update_time_entry_not_found(self, conn, env):
        result = call_action(
            ACTIONS["legal-update-time-entry"], conn,
            ns(time_entry_id="bad-id", hours="1.0"),
        )
        assert is_error(result)

    def test_update_billed_entry_fails(self, conn, env):
        te_id = seed_time_entry(conn, env["matter_id"], env["company_id"])
        # Mark as billed directly
        conn.execute("UPDATE legalclaw_time_entry SET is_billed = 1 WHERE id = ?",
                      (te_id,))
        conn.commit()
        result = call_action(
            ACTIONS["legal-update-time-entry"], conn,
            ns(time_entry_id=te_id, hours="5.0"),
        )
        assert is_error(result)


class TestListTimeEntries:
    """legal-list-time-entries"""

    def test_list_time_entries_ok(self, conn, env):
        seed_time_entry(conn, env["matter_id"], env["company_id"])
        result = call_action(
            ACTIONS["legal-list-time-entries"], conn,
            ns(matter_id=env["matter_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1

    def test_list_time_entries_by_attorney(self, conn, env):
        seed_time_entry(conn, env["matter_id"], env["company_id"],
                        attorney="Specific Attorney")
        result = call_action(
            ACTIONS["legal-list-time-entries"], conn,
            ns(attorney="Specific Attorney"),
        )
        assert is_ok(result), result
        assert result["count"] >= 1


# ── Expense Tests ──────────────────────────────────────────────────────


class TestAddExpense:
    """legal-add-expense"""

    def test_add_expense_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-expense"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                expense_amount="250.00",
                category="filing",
                expense_description="Court filing fee",
            ),
        )
        assert is_ok(result), result
        assert result["amount"] == "250.00"
        assert result["category"] == "filing"

    def test_add_expense_missing_amount(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-expense"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                category="filing",
            ),
        )
        assert is_error(result)

    def test_add_expense_invalid_category(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-expense"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                expense_amount="100.00",
                category="invalid_cat",
            ),
        )
        assert is_error(result)


class TestUpdateExpense:
    """legal-update-expense"""

    def test_update_expense_amount(self, conn, env):
        exp_id = seed_expense(conn, env["matter_id"], env["company_id"])
        result = call_action(
            ACTIONS["legal-update-expense"], conn,
            ns(expense_id=exp_id, expense_amount="200.00"),
        )
        assert is_ok(result), result
        assert "amount" in result["updated_fields"]

    def test_update_expense_not_found(self, conn, env):
        result = call_action(
            ACTIONS["legal-update-expense"], conn,
            ns(expense_id="bad-id", expense_amount="100.00"),
        )
        assert is_error(result)

    def test_update_billed_expense_fails(self, conn, env):
        exp_id = seed_expense(conn, env["matter_id"], env["company_id"])
        conn.execute("UPDATE legalclaw_expense SET is_billed = 1 WHERE id = ?",
                      (exp_id,))
        conn.commit()
        result = call_action(
            ACTIONS["legal-update-expense"], conn,
            ns(expense_id=exp_id, expense_amount="999.00"),
        )
        assert is_error(result)


class TestListExpenses:
    """legal-list-expenses"""

    def test_list_expenses_ok(self, conn, env):
        seed_expense(conn, env["matter_id"], env["company_id"])
        result = call_action(
            ACTIONS["legal-list-expenses"], conn,
            ns(matter_id=env["matter_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1


# ── Invoice Tests ──────────────────────────────────────────────────────


class TestGenerateInvoice:
    """legal-generate-invoice"""

    def test_generate_invoice_ok(self, conn, env):
        seed_time_entry(conn, env["matter_id"], env["company_id"])
        seed_expense(conn, env["matter_id"], env["company_id"])
        result = call_action(
            ACTIONS["legal-generate-invoice"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
            ),
        )
        assert is_ok(result), result
        assert result["time_entries_count"] >= 1
        assert result["expenses_count"] >= 1
        assert result["invoice_status"] == "draft"
        # total = time(2h * 250) + expense(150)
        assert "total_amount" in result

    def test_generate_invoice_no_unbilled(self, conn, env):
        result = call_action(
            ACTIONS["legal-generate-invoice"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
            ),
        )
        assert is_error(result)


class TestGetInvoice:
    """legal-get-invoice"""

    def test_get_invoice_ok(self, conn, env):
        seed_time_entry(conn, env["matter_id"], env["company_id"])
        gen = call_action(
            ACTIONS["legal-generate-invoice"], conn,
            ns(company_id=env["company_id"], matter_id=env["matter_id"]),
        )
        assert is_ok(gen), gen
        result = call_action(
            ACTIONS["legal-get-invoice"], conn,
            ns(invoice_id=gen["id"]),
        )
        assert is_ok(result), result
        assert result["id"] == gen["id"]
        assert "time_entries" in result

    def test_get_invoice_not_found(self, conn, env):
        result = call_action(
            ACTIONS["legal-get-invoice"], conn,
            ns(invoice_id="bad-id"),
        )
        assert is_error(result)


class TestListInvoices:
    """legal-list-invoices"""

    def test_list_invoices_ok(self, conn, env):
        seed_time_entry(conn, env["matter_id"], env["company_id"])
        call_action(
            ACTIONS["legal-generate-invoice"], conn,
            ns(company_id=env["company_id"], matter_id=env["matter_id"]),
        )
        result = call_action(
            ACTIONS["legal-list-invoices"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1


class TestSendInvoice:
    """legal-send-invoice"""

    def test_send_invoice_ok(self, conn, env):
        seed_time_entry(conn, env["matter_id"], env["company_id"])
        gen = call_action(
            ACTIONS["legal-generate-invoice"], conn,
            ns(company_id=env["company_id"], matter_id=env["matter_id"]),
        )
        assert is_ok(gen), gen
        result = call_action(
            ACTIONS["legal-send-invoice"], conn,
            ns(invoice_id=gen["id"]),
        )
        assert is_ok(result), result
        assert result["invoice_status"] == "sent"

    def test_send_already_sent(self, conn, env):
        seed_time_entry(conn, env["matter_id"], env["company_id"])
        gen = call_action(
            ACTIONS["legal-generate-invoice"], conn,
            ns(company_id=env["company_id"], matter_id=env["matter_id"]),
        )
        call_action(ACTIONS["legal-send-invoice"], conn,
                     ns(invoice_id=gen["id"]))
        result = call_action(
            ACTIONS["legal-send-invoice"], conn,
            ns(invoice_id=gen["id"]),
        )
        assert is_error(result)


class TestRecordPayment:
    """legal-record-payment"""

    def test_record_full_payment(self, conn, env):
        te_id = seed_time_entry(conn, env["matter_id"], env["company_id"],
                                hours="1.0", rate="100.00")
        gen = call_action(
            ACTIONS["legal-generate-invoice"], conn,
            ns(company_id=env["company_id"], matter_id=env["matter_id"]),
        )
        assert is_ok(gen), gen
        # Send first
        call_action(ACTIONS["legal-send-invoice"], conn,
                     ns(invoice_id=gen["id"]))
        result = call_action(
            ACTIONS["legal-record-payment"], conn,
            ns(
                invoice_id=gen["id"],
                payment_amount=gen["total_amount"],
                company_id=env["company_id"],
            ),
        )
        assert is_ok(result), result
        assert result["invoice_status"] == "paid"
        from decimal import Decimal
        assert Decimal(result["balance"]) == Decimal("0")

    def test_record_partial_payment(self, conn, env):
        seed_time_entry(conn, env["matter_id"], env["company_id"],
                        hours="2.0", rate="200.00")
        gen = call_action(
            ACTIONS["legal-generate-invoice"], conn,
            ns(company_id=env["company_id"], matter_id=env["matter_id"]),
        )
        assert is_ok(gen), gen
        call_action(ACTIONS["legal-send-invoice"], conn,
                     ns(invoice_id=gen["id"]))
        result = call_action(
            ACTIONS["legal-record-payment"], conn,
            ns(
                invoice_id=gen["id"],
                payment_amount="100.00",
                company_id=env["company_id"],
            ),
        )
        assert is_ok(result), result
        assert result["invoice_status"] == "partially_paid"

    def test_record_payment_overpay(self, conn, env):
        seed_time_entry(conn, env["matter_id"], env["company_id"],
                        hours="1.0", rate="100.00")
        gen = call_action(
            ACTIONS["legal-generate-invoice"], conn,
            ns(company_id=env["company_id"], matter_id=env["matter_id"]),
        )
        assert is_ok(gen), gen
        call_action(ACTIONS["legal-send-invoice"], conn,
                     ns(invoice_id=gen["id"]))
        result = call_action(
            ACTIONS["legal-record-payment"], conn,
            ns(
                invoice_id=gen["id"],
                payment_amount="999999.00",
                company_id=env["company_id"],
            ),
        )
        assert is_error(result)

    def test_record_payment_missing_amount(self, conn, env):
        result = call_action(
            ACTIONS["legal-record-payment"], conn,
            ns(invoice_id="some-id"),
        )
        assert is_error(result)


class TestWriteOffInvoice:
    """legal-write-off-invoice"""

    def test_write_off_ok(self, conn, env):
        seed_time_entry(conn, env["matter_id"], env["company_id"])
        gen = call_action(
            ACTIONS["legal-generate-invoice"], conn,
            ns(company_id=env["company_id"], matter_id=env["matter_id"]),
        )
        assert is_ok(gen), gen
        result = call_action(
            ACTIONS["legal-write-off-invoice"], conn,
            ns(invoice_id=gen["id"]),
        )
        assert is_ok(result), result
        assert result["invoice_status"] == "written_off"

    def test_write_off_already_paid(self, conn, env):
        seed_time_entry(conn, env["matter_id"], env["company_id"],
                        hours="1.0", rate="100.00")
        gen = call_action(
            ACTIONS["legal-generate-invoice"], conn,
            ns(company_id=env["company_id"], matter_id=env["matter_id"]),
        )
        call_action(ACTIONS["legal-send-invoice"], conn,
                     ns(invoice_id=gen["id"]))
        call_action(ACTIONS["legal-record-payment"], conn,
                     ns(invoice_id=gen["id"], payment_amount=gen["total_amount"],
                        company_id=env["company_id"]))
        result = call_action(
            ACTIONS["legal-write-off-invoice"], conn,
            ns(invoice_id=gen["id"]),
        )
        assert is_error(result)


# ── Report Tests ───────────────────────────────────────────────────────


class TestBillableUtilizationReport:
    """legal-billable-utilization-report"""

    def test_utilization_report_ok(self, conn, env):
        seed_time_entry(conn, env["matter_id"], env["company_id"],
                        attorney="Partner A", hours="8.0")
        result = call_action(
            ACTIONS["legal-billable-utilization-report"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1
        assert result["attorneys"][0]["attorney"] == "Partner A"


class TestArAgingReport:
    """legal-ar-aging-report"""

    def test_ar_aging_empty(self, conn, env):
        result = call_action(
            ACTIONS["legal-ar-aging-report"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_outstanding"] == "0"
