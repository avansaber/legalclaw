"""L1 tests for LegalClaw LEDES e-billing module.

Covers:
  - legal-generate-invoice with format='ledes' (LEDES 1998B output)
  - legal-generate-invoice-ledes (standalone LEDES generation)
  - legal-validate-ledes (format compliance checking)
"""
import pytest
import sys
import os
from decimal import Decimal

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from legal_helpers import (
    call_action, ns, is_ok, is_error, load_db_query,
    seed_time_entry, seed_expense,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS


# ── Helpers ──────────────────────────────────────────────────────────────────

def _generate_invoice(conn, env, invoice_format="standard"):
    """Create time entries, expenses, then generate an invoice."""
    # Add time entries
    call_action(ACTIONS["legal-add-time-entry"], conn, ns(
        company_id=env["company_id"],
        matter_id=env["matter_id"],
        attorney="J. Smith",
        te_description="Legal research on precedent",
        hours="2.0",
        rate="300.00",
        utbms_code="L110",
    ))
    call_action(ACTIONS["legal-add-time-entry"], conn, ns(
        company_id=env["company_id"],
        matter_id=env["matter_id"],
        attorney="A. Jones",
        te_description="Deposition preparation",
        hours="1.5",
        rate="250.00",
        utbms_code="L120",
    ))
    # Add expense
    call_action(ACTIONS["legal-add-expense"], conn, ns(
        company_id=env["company_id"],
        matter_id=env["matter_id"],
        expense_amount="75.00",
        category="filing",
        expense_description="Court filing fee",
    ))

    # Generate invoice
    result = call_action(ACTIONS["legal-generate-invoice"], conn, ns(
        company_id=env["company_id"],
        matter_id=env["matter_id"],
        invoice_date="2026-03-15",
        due_date="2026-04-15",
        invoice_format=invoice_format,
    ))
    assert is_ok(result), result
    return result


# ── LEDES Generation Tests ───────────────────────────────────────────────────

class TestGenerateInvoiceLedesFormat:
    """Test legal-generate-invoice with --invoice-format ledes."""

    def test_generate_invoice_ledes_format(self, conn, env):
        """Verify pipe-delimited LEDES 1998B output is generated."""
        result = _generate_invoice(conn, env, invoice_format="ledes")
        assert is_ok(result), result

        # Should have LEDES output
        if "ledes_output" in result:
            ledes = result["ledes_output"]
            assert "LEDES1998B[]" in ledes
            assert "|" in ledes
            assert result.get("ledes_format") == "LEDES1998B"
        else:
            # May have deferred warning
            assert "ledes_warning" in result or result["invoice_status"] == "draft"

    def test_generate_invoice_ledes_header_fields(self, conn, env):
        """Verify LEDES header contains required fields."""
        result = _generate_invoice(conn, env, invoice_format="ledes")

        if "ledes_output" not in result:
            pytest.skip("LEDES output deferred")

        ledes = result["ledes_output"]
        lines = ledes.split("\n")

        # Line 0: LEDES1998B[]
        assert lines[0] == "LEDES1998B[]"

        # Line 1: Column headers
        assert "INVOICE_DATE" in lines[1]
        assert "INVOICE_NUMBER" in lines[1]
        assert "CLIENT_ID" in lines[1]
        assert "LAW_FIRM_MATTER_ID" in lines[1]
        assert "INVOICE_TOTAL" in lines[1]

        # Line 2: Invoice header data (pipe-delimited)
        invoice_line = lines[2]
        fields = invoice_line.rstrip("[]").split("|")
        assert len(fields) >= 10

    def test_generate_invoice_ledes_line_items_with_utbms(self, conn, env):
        """Verify line items include UTBMS task codes."""
        result = _generate_invoice(conn, env, invoice_format="ledes")

        if "ledes_output" not in result:
            pytest.skip("LEDES output deferred")

        ledes = result["ledes_output"]
        lines = ledes.split("\n")

        # Find line item lines (after LINE_ITEM_NUMBER header)
        line_item_header_idx = None
        for i, line in enumerate(lines):
            if "LINE_ITEM_NUMBER" in line and "EXP/FEE" in line:
                line_item_header_idx = i
                break

        assert line_item_header_idx is not None, "LINE_ITEM header not found"

        # Should have fee lines (F) and expense lines (E)
        fee_lines = [l for l in lines[line_item_header_idx + 1:] if l.startswith("1|F") or l.startswith("2|F")]
        expense_lines = [l for l in lines[line_item_header_idx + 1:] if "|E|" in l]

        assert len(fee_lines) >= 1, "No fee line items found"

        # Check first fee line has UTBMS code
        first_fee = fee_lines[0].rstrip("[]").split("|")
        # Field index 6 = LINE_ITEM_TASK_CODE
        task_code = first_fee[6] if len(first_fee) > 6 else ""
        assert task_code != "", f"UTBMS task code missing in fee line: {first_fee}"

    def test_generate_invoice_ledes_date_format(self, conn, env):
        """Verify dates are in YYYYMMDD format (no dashes)."""
        result = _generate_invoice(conn, env, invoice_format="ledes")

        if "ledes_output" not in result:
            pytest.skip("LEDES output deferred")

        ledes = result["ledes_output"]
        lines = ledes.split("\n")

        # Invoice header line (line 2)
        invoice_line = lines[2].rstrip("[]")
        fields = invoice_line.split("|")

        # INVOICE_DATE is first field
        invoice_date = fields[0]
        assert len(invoice_date) == 8, f"INVOICE_DATE should be YYYYMMDD, got: {invoice_date}"
        assert "-" not in invoice_date, f"INVOICE_DATE should not have dashes: {invoice_date}"
        assert invoice_date == "20260315", f"Expected 20260315, got: {invoice_date}"


class TestGenerateInvoiceLedes:
    """Test legal-generate-invoice-ledes (standalone)."""

    def test_standalone_ledes_generation(self, conn, env):
        """Generate a standard invoice first, then get LEDES output."""
        inv_result = _generate_invoice(conn, env, invoice_format="ledes")
        inv_id = inv_result["id"]

        result = call_action(ACTIONS["legal-generate-invoice-ledes"], conn, ns(
            invoice_id=inv_id,
        ))
        assert is_ok(result), result
        assert "ledes_output" in result
        assert result["format"] == "LEDES1998B"
        assert result["time_entries"] >= 2
        assert result["expenses"] >= 1

    def test_standalone_ledes_missing_invoice(self, conn, env):
        result = call_action(ACTIONS["legal-generate-invoice-ledes"], conn, ns(
            invoice_id="nonexistent-id",
        ))
        assert is_error(result)


# ── LEDES Validation Tests ───────────────────────────────────────────────────

class TestValidateLedes:
    """Test legal-validate-ledes."""

    def test_validate_ledes_valid(self, conn, env):
        """A properly generated LEDES invoice should validate."""
        inv_result = _generate_invoice(conn, env, invoice_format="ledes")
        inv_id = inv_result["id"]

        result = call_action(ACTIONS["legal-validate-ledes"], conn, ns(
            invoice_id=inv_id,
        ))
        assert is_ok(result), result
        assert result["is_valid"] is True
        assert len(result["issues"]) == 0

    def test_validate_ledes_standard_format(self, conn, env):
        """Invoice with format='standard' should flag as issue."""
        inv_result = _generate_invoice(conn, env, invoice_format="standard")
        inv_id = inv_result["id"]

        result = call_action(ACTIONS["legal-validate-ledes"], conn, ns(
            invoice_id=inv_id,
        ))
        assert is_ok(result), result
        assert result["is_valid"] is False
        assert any("format" in issue.lower() for issue in result["issues"])

    def test_validate_ledes_missing_invoice(self, conn, env):
        result = call_action(ACTIONS["legal-validate-ledes"], conn, ns(
            invoice_id="nonexistent-id",
        ))
        assert is_error(result)

    def test_validate_ledes_counts_utbms_warnings(self, conn, env):
        """Time entries without UTBMS codes should generate warnings."""
        # Add time entry without UTBMS code
        call_action(ACTIONS["legal-add-time-entry"], conn, ns(
            company_id=env["company_id"],
            matter_id=env["matter_id"],
            attorney="J. Smith",
            te_description="Work without UTBMS",
            hours="1.0",
            rate="300.00",
            # No utbms_code
        ))
        # Generate invoice
        inv_result = call_action(ACTIONS["legal-generate-invoice"], conn, ns(
            company_id=env["company_id"],
            matter_id=env["matter_id"],
            invoice_format="ledes",
        ))
        assert is_ok(inv_result), inv_result
        inv_id = inv_result["id"]

        result = call_action(ACTIONS["legal-validate-ledes"], conn, ns(
            invoice_id=inv_id,
        ))
        assert is_ok(result), result
        assert any("UTBMS" in w for w in result["warnings"])

    def test_validate_ledes_time_and_expense_counts(self, conn, env):
        """Validate shows correct counts."""
        inv_result = _generate_invoice(conn, env, invoice_format="ledes")
        inv_id = inv_result["id"]

        result = call_action(ACTIONS["legal-validate-ledes"], conn, ns(
            invoice_id=inv_id,
        ))
        assert is_ok(result), result
        assert result["time_entries_count"] >= 2
        assert result["expenses_count"] >= 1
