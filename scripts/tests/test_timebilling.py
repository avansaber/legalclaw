"""L1 tests for LegalClaw time & billing domain.

Covers:
  - Time entries: add, update, list
  - Expenses: add, update, list
  - Invoice: generate, get, list, send, record-payment, write-off
  - Reports: billable-utilization, ar-aging
"""
import importlib.util
import io
import json
import pytest
import sys
import os
import uuid
from decimal import Decimal
from unittest.mock import patch

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from legal_helpers import (
    call_action, ns, is_ok, is_error, load_db_query,
    seed_time_entry, seed_expense, SRC_DIR,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS

# The router does `from timebilling import ACTIONS`, so the domain module — the
# one that actually holds the `call_skill_action` name F17c's delegation calls —
# is this, not the router. Patching the router would patch nothing.
_timebilling = sys.modules["timebilling"]


# ── F17c: driving the REAL foundation write-off from legalclaw's tests ───────

_FOUNDATION_SCRIPTS = os.path.join(SRC_DIR, "erpclaw", "scripts")


def _load_foundation(domain):
    """Load a foundation domain script by explicit path.

    Same idiom as load_db_query(): two modules both name their entry point
    db_query.py, so importing by name would collide.
    """
    path = os.path.join(_FOUNDATION_SCRIPTS, domain, "db_query.py")
    spec = importlib.util.spec_from_file_location(f"_fnd_{domain}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_linked_sales_invoice(conn, env, legal_invoice_id, total):
    """Give a legal invoice the core sales invoice it is supposed to have.

    Writes what erpclaw-selling writes on submit — the document, its line, its
    voucher-level payment_ledger_entry row and its own balanced GL pair — because
    F17c's delegation posts against a real receivable, not a stub.
    """
    sys.path.insert(0, os.path.join(_FOUNDATION_SCRIPTS, "erpclaw-setup", "lib"))
    from erpclaw_lib.gl_posting import insert_gl_entries

    si_id = str(uuid.uuid4())
    posting_date = "2026-06-01"
    conn.execute(
        "INSERT INTO sales_invoice (id, customer_id, posting_date, grand_total, "
        " total_amount, tax_amount, rounding_adjustment, outstanding_amount, "
        " status, company_id) VALUES (?, ?, ?, ?, ?, '0', '0', ?, 'submitted', ?)",
        (si_id, env["core_customer_id"], posting_date, str(total), str(total),
         str(total), env["company_id"]))
    conn.execute(
        "INSERT INTO sales_invoice_item (id, sales_invoice_id, item_id, "
        " quantity, rate, amount, net_amount) VALUES (?, ?, 'ITEM-1', '1', ?, ?, ?)",
        (str(uuid.uuid4()), si_id, str(total), str(total), str(total)))
    conn.execute(
        "INSERT INTO payment_ledger_entry (id, posting_date, account_id, "
        " party_type, party_id, voucher_type, voucher_id, amount, "
        " amount_in_account_currency, currency, delinked) "
        "VALUES (?, ?, ?, 'customer', ?, 'sales_invoice', ?, ?, ?, 'USD', 0)",
        (str(uuid.uuid4()), posting_date, env["receivable_acct"],
         env["core_customer_id"], si_id, str(total), str(total)))
    insert_gl_entries(
        conn,
        [{"account_id": env["receivable_acct"], "debit": str(total),
          "credit": "0", "party_type": "customer",
          "party_id": env["core_customer_id"], "fiscal_year": "FY"},
         {"account_id": env["interest_income_acct"], "debit": "0",
          "credit": str(total), "cost_center_id": env["cost_center_id"],
          "fiscal_year": "FY"}],
        voucher_type="sales_invoice", voucher_id=si_id,
        posting_date=posting_date, company_id=env["company_id"],
        remarks=f"sales_invoice {si_id}")
    conn.execute("UPDATE legalclaw_invoice SET sales_invoice_id = ? WHERE id = ?",
                 (si_id, legal_invoice_id))
    conn.commit()
    return si_id, env["receivable_acct"]


def _delegate_in_process(conn, monkeypatch):
    """Redirect legalclaw's cross-skill hop to the REAL foundation function.

    call_skill_action shells out to the INSTALLED skill tree, which is neither
    this worktree's code nor this test's database. Running the genuine
    write_off_invoice in-process instead keeps every accounting assertion real
    while still recording exactly which action and flags legalclaw sent.
    """
    payments = _load_foundation("erpclaw-payments")
    captured = {}

    def _in_process(skill_name, action, args=None, db_path=None, timeout=30):
        captured.update(skill=skill_name, action=action, args=dict(args or {}),
                        db_path=db_path)
        flags = args or {}
        buf = io.StringIO()

        def _fake_exit(code=0):
            raise SystemExit(code)

        try:
            with patch("sys.stdout", buf), patch("sys.exit", side_effect=_fake_exit):
                payments.write_off_invoice(conn, ns(
                    voucher_type=flags.get("--voucher-type"),
                    voucher_id=flags.get("--voucher-id"),
                    write_off_amount=flags.get("--write-off-amount"),
                    write_off_account_id=flags.get("--write-off-account-id"),
                    reason=flags.get("--reason"),
                    posting_date=None, cost_center_id=None))
        except SystemExit:
            pass
        result = json.loads(buf.getvalue().strip())
        if result.get("status") == "error":
            raise _timebilling.CrossSkillError(result.get("message", "write-off failed"))
        return result

    monkeypatch.setattr(_timebilling, "call_skill_action", _in_process)
    return captured


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
    """legal-write-off-invoice — Wave G F17c.

    The accounting half is no longer absent. A linked legal invoice delegates to
    the foundation's `write-off-invoice` primitive (F17a), which posts the GL and
    drops the sales invoice's outstanding; an UNLINKED one is refused instead of
    having its balance silently zeroed with nothing behind it.
    """

    def _legal_invoice(self, conn, env):
        seed_time_entry(conn, env["matter_id"], env["company_id"])
        gen = call_action(
            ACTIONS["legal-generate-invoice"], conn,
            ns(company_id=env["company_id"], matter_id=env["matter_id"]),
        )
        assert is_ok(gen), gen
        return gen

    def _row(self, conn, inv_id):
        return dict(conn.execute(
            "SELECT status, balance, sales_invoice_id FROM legalclaw_invoice "
            "WHERE id = ?", (inv_id,)).fetchone())

    # ── the null-link refusal (the shipped-behavior delta) ──────────────────

    def test_write_off_refuses_an_unlinked_invoice_and_writes_nothing(self, conn, env):
        """No linked sales invoice ⇒ nothing in the books to write off.

        This is the case the old action cleared silently: status flipped to
        'written_off' and balance to '0' with no GL, no receivable moved and no
        subledger row. Refusing is the fix; the pin asserts the refusal AND that
        the row is untouched, because a refusal that still wrote would be the
        same defect with a louder message.
        """
        gen = self._legal_invoice(conn, env)
        before = self._row(conn, gen["id"])
        assert before["sales_invoice_id"] is None, \
            "fixture precondition: this legal invoice is unlinked"

        result = call_action(
            ACTIONS["legal-write-off-invoice"], conn,
            ns(invoice_id=gen["id"], write_off_account_id="whatever",
               reason="Client insolvent"),
        )
        assert is_error(result), result
        assert "not linked to an accounting invoice" in result["message"]
        assert "write-off-invoice" in result["suggestion"]

        assert self._row(conn, gen["id"]) == before, "the refusal wrote something"
        assert conn.execute(
            "SELECT COUNT(*) FROM gl_entry").fetchone()[0] == 0

    # ── the linked path delegates, and the delegation is the real primitive ──

    def test_write_off_delegates_to_the_core_action_when_linked(self, conn, env,
                                                                monkeypatch):
        """A linked invoice moves the real books.

        The cross-skill hop is a subprocess against the INSTALLED skill tree, so
        it is redirected in-process to the REAL foundation `write_off_invoice`
        against this same connection. That keeps the accounting assertions honest
        (actual GL, actual outstanding) instead of pinning a canned dict, while
        still capturing the exact flags legalclaw sends.
        """
        gen = self._legal_invoice(conn, env)
        si_id, ar_acct = _seed_linked_sales_invoice(
            conn, env, gen["id"], gen["total_amount"])
        captured = _delegate_in_process(conn, monkeypatch)

        result = call_action(
            ACTIONS["legal-write-off-invoice"], conn,
            ns(invoice_id=gen["id"], write_off_account_id=env["bad_debt_acct"],
               reason="Client insolvent — 2026 review"),
        )
        assert is_ok(result), result

        # The delegation contract: the core action, this invoice, this amount.
        assert captured["skill"] == "erpclaw"
        assert captured["action"] == "write-off-invoice"
        assert captured["args"]["--voucher-type"] == "sales_invoice"
        assert captured["args"]["--voucher-id"] == si_id
        # legalclaw stores its balance at 3dp; money crossing into the GL is
        # normalized to currency precision first, with the value preserved.
        assert captured["args"]["--write-off-amount"] == "500.00"
        assert Decimal(captured["args"]["--write-off-amount"]) == \
            Decimal(gen["total_amount"])
        assert captured["args"]["--write-off-account-id"] == env["bad_debt_acct"]
        assert captured["args"]["--reason"] == "Client insolvent — 2026 review"
        # The core action is in the gated transaction class, so the hop must
        # carry the confirmation or the real subprocess is refused (see
        # test_the_delegation_would_be_refused_without_the_confirmation).
        assert "--user-confirmed" in captured["args"]

        # The core invoice really moved.
        core = dict(conn.execute(
            "SELECT outstanding_amount, status FROM sales_invoice WHERE id = ?",
            (si_id,)).fetchone())
        assert Decimal(core["outstanding_amount"]) == Decimal("0")
        assert core["status"] == "paid"
        gl = conn.execute(
            "SELECT debit, credit, account_id FROM gl_entry "
            " WHERE voucher_type='sales_invoice' AND voucher_id=? "
            "   AND entry_set='write_off'", (si_id,)).fetchall()
        assert len(gl) == 2, "the balanced write-off pair"
        assert sum(Decimal(r["debit"]) for r in gl) == \
            sum(Decimal(r["credit"]) for r in gl) == Decimal(gen["total_amount"])
        assert {r["account_id"] for r in gl} == {ar_acct, env["bad_debt_acct"]}

        # ...and only then was the vertical's own row stamped, from that result.
        legal = self._row(conn, gen["id"])
        assert legal["status"] == "written_off"
        assert Decimal(legal["balance"]) == Decimal("0")
        assert result["sales_invoice_id"] == si_id
        assert result["sales_invoice_status"] == "paid"
        assert result["gl_entries_created"] == 2

    def test_write_off_leaves_the_legal_row_untouched_when_the_core_call_fails(
            self, conn, env, monkeypatch):
        """A failed accounting half must not produce a written-off legal row."""
        gen = self._legal_invoice(conn, env)
        si_id, _ = _seed_linked_sales_invoice(conn, env, gen["id"],
                                              gen["total_amount"])
        before = self._row(conn, gen["id"])

        def _boom(*a, **kw):
            raise _timebilling.CrossSkillError("erpclaw is not installed")
        monkeypatch.setattr(_timebilling, "call_skill_action", _boom)

        result = call_action(
            ACTIONS["legal-write-off-invoice"], conn,
            ns(invoice_id=gen["id"], write_off_account_id=env["bad_debt_acct"],
               reason="Client insolvent"),
        )
        assert is_error(result)
        assert "nothing was written" in result["message"]
        assert self._row(conn, gen["id"]) == before

    def test_the_delegation_would_be_refused_without_the_confirmation(
            self, conn, env, monkeypatch):
        """The flags legalclaw sends are replayed against the REAL router gate.

        `write-off-invoice` joined DANGEROUS_ACTIONS (QA condition 2), so the
        cross-skill subprocess is refused unless the hop carries the flag. This
        rebuilds the exact argv from what the delegation captured and drives the
        genuine `_gate_dangerous_action`, so the pass-through is proven against
        the real gate rather than assumed — and dropping the flag from
        timebilling.py turns this red instead of surfacing on a live box.
        """
        import importlib.util as _il
        import json as _json
        from unittest.mock import patch as _patch

        gen = self._legal_invoice(conn, env)
        _seed_linked_sales_invoice(conn, env, gen["id"], gen["total_amount"])
        captured = _delegate_in_process(conn, monkeypatch)
        assert is_ok(call_action(
            ACTIONS["legal-write-off-invoice"], conn,
            ns(invoice_id=gen["id"], write_off_account_id=env["bad_debt_acct"],
               reason="Client insolvent")))

        spec = _il.spec_from_file_location(
            "_f17_router", os.path.join(_FOUNDATION_SCRIPTS, "db_query.py"))
        router = _il.module_from_spec(spec)
        spec.loader.exec_module(router)
        assert captured["action"] in router.DANGEROUS_ACTIONS

        argv = ["db_query.py", "--action", captured["action"]]
        for key, value in captured["args"].items():
            argv.append(key)
            if value is not None:
                argv.append(str(value))

        # As sent: the gate lets it through.
        with _patch.object(sys, "argv", argv):
            router._gate_dangerous_action(captured["action"])

        # With the confirmation stripped: refused, exactly as a real box would.
        stripped = [a for a in argv if a != "--user-confirmed"]
        buf = io.StringIO()
        with _patch.object(sys, "argv", stripped), \
                _patch("sys.stdout", buf), pytest.raises(SystemExit) as exc:
            router._gate_dangerous_action(captured["action"])
        assert exc.value.code == 2
        assert _json.loads(buf.getvalue())["error"] == "user_confirmation_required"

    def test_write_off_requires_an_account_and_a_reason(self, conn, env):
        """Guessing either is what made the old silent-clear path wrong."""
        gen = self._legal_invoice(conn, env)
        _seed_linked_sales_invoice(conn, env, gen["id"], gen["total_amount"])

        no_account = call_action(
            ACTIONS["legal-write-off-invoice"], conn,
            ns(invoice_id=gen["id"], reason="Client insolvent"))
        assert is_error(no_account)
        assert "--write-off-account-id is required" in no_account["message"]

        no_reason = call_action(
            ACTIONS["legal-write-off-invoice"], conn,
            ns(invoice_id=gen["id"], write_off_account_id=env["bad_debt_acct"],
               reason="   "))
        assert is_error(no_reason)
        assert "--reason is required" in no_reason["message"]

        assert self._row(conn, gen["id"])["status"] != "written_off"

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
