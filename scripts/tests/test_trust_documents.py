"""L1 tests for LegalClaw trust accounting + document management domains.

Covers:
  Trust:
    - Trust accounts: add, get, list
    - Trust transactions: deposit, disburse, transfer, list, interest-distribution
    - Trust reports: reconciliation, balance-report
  Documents:
    - Documents: add, update, get, list, file, archive
    - Document versions: add-version, list-versions
    - Document index, search
"""
import pytest
import sys
import os

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from legal_helpers import (
    call_action, ns, is_ok, is_error, load_db_query,
    seed_trust_account, seed_document, seed_matter, seed_client_ext,
    seed_customer, seed_company, seed_naming_series, seed_account,
    seed_fiscal_year, seed_cost_center,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS

from erpclaw_lib.query import P, Q, Table  # noqa: E402


# ── Trust Account Tests ────────────────────────────────────────────────


class TestAddTrustAccount:
    """legal-add-trust-account"""

    def test_add_trust_account_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-trust-account"], conn,
            ns(
                company_id=env["company_id"],
                trust_name="Client Trust IOLTA",
                account_type="iolta",
                bank_name="First National Bank",
                account_number="12345678",
            ),
        )
        assert is_ok(result), result
        assert result["name"] == "Client Trust IOLTA"
        assert result["account_type"] == "iolta"
        assert result["current_balance"] == "0"

    def test_add_trust_account_missing_name(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-trust-account"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_error(result)

    def test_add_trust_account_with_gl(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-trust-account"], conn,
            ns(
                company_id=env["company_id"],
                trust_name="Escrow Account",
                account_type="escrow",
                gl_account_id=env["trust_bank_acct"],
                trust_liability_account_id=env["trust_liability_acct"],
            ),
        )
        assert is_ok(result), result
        assert result["gl_account_id"] == env["trust_bank_acct"]


class TestGetTrustAccount:
    """legal-get-trust-account"""

    def test_get_trust_account_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-get-trust-account"], conn,
            ns(trust_account_id=env["trust_account_id"]),
        )
        assert is_ok(result), result
        assert result["id"] == env["trust_account_id"]

    def test_get_trust_account_not_found(self, conn, env):
        result = call_action(
            ACTIONS["legal-get-trust-account"], conn,
            ns(trust_account_id="nonexistent"),
        )
        assert is_error(result)


class TestListTrustAccounts:
    """legal-list-trust-accounts"""

    def test_list_trust_accounts_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-list-trust-accounts"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1


# ── Trust Transaction Tests ────────────────────────────────────────────


class TestDepositTrust:
    """legal-deposit-trust"""

    def test_deposit_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="5000.00",
                matter_id=env["matter_id"],
                reference="CHK-1001",
                payee="Jane Client",
            ),
        )
        assert is_ok(result), result
        assert result["transaction_type"] == "deposit"
        assert result["amount"] == "5000.00"
        assert result["new_balance"] == "5000.00"

    def test_deposit_zero_amount(self, conn, env):
        result = call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="0",
            ),
        )
        assert is_error(result)

    def test_deposit_missing_amount(self, conn, env):
        result = call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
            ),
        )
        assert is_error(result)


class TestDisburseTrust:
    """legal-disburse-trust"""

    def test_disburse_ok(self, conn, env):
        # Deposit first
        call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="10000.00",
                matter_id=env["matter_id"],
            ),
        )
        result = call_action(
            ACTIONS["legal-disburse-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="3000.00",
                payee="Expert Witness Inc",
                matter_id=env["matter_id"],
            ),
        )
        assert is_ok(result), result
        assert result["transaction_type"] == "disbursement"
        assert result["new_balance"] == "7000.00"
        matter_table = Table("legalclaw_matter")
        matter_query = Q.from_(matter_table).select(matter_table.trust_balance).where(matter_table.id == P())
        stored_balance = conn.execute(matter_query.get_sql(), (env["matter_id"],)).fetchone()["trust_balance"]
        assert stored_balance == "7000.00"

    def test_disburse_insufficient(self, conn, env):
        result = call_action(
            ACTIONS["legal-disburse-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="999999.00",
                payee="Test",
            ),
        )
        assert is_error(result)

    def test_disburse_missing_payee(self, conn, env):
        # Deposit first
        call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="5000.00",
            ),
        )
        result = call_action(
            ACTIONS["legal-disburse-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="100.00",
            ),
        )
        assert is_error(result)


class TestTransferTrust:
    """legal-transfer-trust"""

    def test_transfer_ok(self, conn, env):
        # Deposit into source
        call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="8000.00",
            ),
        )
        # Create destination account
        dest = seed_trust_account(conn, env["company_id"], name="Escrow Account",
                                  account_type="escrow")
        result = call_action(
            ACTIONS["legal-transfer-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                to_trust_account_id=dest,
                amount="2000.00",
            ),
        )
        assert is_ok(result), result
        assert result["from_new_balance"] == "6000.00"
        assert result["to_new_balance"] == "2000.00"

    def test_transfer_insufficient(self, conn, env):
        dest = seed_trust_account(conn, env["company_id"], name="Dest")
        result = call_action(
            ACTIONS["legal-transfer-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                to_trust_account_id=dest,
                amount="999999.00",
            ),
        )
        assert is_error(result)


class TestListTrustTransactions:
    """legal-list-trust-transactions"""

    def test_list_transactions_ok(self, conn, env):
        call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="1000.00",
            ),
        )
        result = call_action(
            ACTIONS["legal-list-trust-transactions"], conn,
            ns(trust_account_id=env["trust_account_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1


class TestTrustReconciliation:
    """legal-trust-reconciliation"""

    @staticmethod
    def _state(conn):
        tables = (
            "legalclaw_trust_account",
            "legalclaw_trust_transaction",
            "legalclaw_matter",
            "audit_log",
        )
        return {
            table: [
                dict(row) for row in conn.execute(
                    f"SELECT * FROM {table} ORDER BY id"
                ).fetchall()
            ]
            for table in tables
        }

    def test_reconciliation_ok(self, conn, env):
        call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="5000.00",
                matter_id=env["matter_id"],
            ),
        )
        result = call_action(
            ACTIONS["legal-trust-reconciliation"], conn,
            ns(
                trust_account_id=env["trust_account_id"],
                statement_balance="5000.00",
            ),
        )
        assert is_ok(result), result
        assert result["book_balance"] == "5000.00"
        assert result["is_reconciled"] is True

    def test_three_way_reconciled_exact(self, conn, env):
        matter_two = seed_matter(
            conn, env["client_ext_id"], env["company_id"],
            title="Second Matter",
        )
        call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="100.10",
                matter_id=env["matter_id"],
            ),
        )
        call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="200.20",
                matter_id=matter_two,
            ),
        )
        result = call_action(
            ACTIONS["legal-trust-reconciliation"], conn,
            ns(
                trust_account_id=env["trust_account_id"],
                statement_balance="300.30",
            ),
        )
        assert is_ok(result), result
        assert result["statement_balance"] == "300.30"
        assert result["book_balance"] == "300.30"
        assert result["calculated_balance"] == "300.30"
        assert result["client_ledger_total"] == "300.30"
        assert result["statement_to_book_difference"] == "0.00"
        assert result["book_to_client_difference"] == "0.00"
        assert result["is_reconciled"] is True

    def test_transfer_reconciles_source_and_destination(self, conn, env):
        destination = seed_trust_account(
            conn, env["company_id"], name="Transfer Destination",
            account_type="escrow",
        )
        deposited = call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="100.10",
                matter_id=env["matter_id"],
            ),
        )
        assert is_ok(deposited), deposited
        transferred = call_action(
            ACTIONS["legal-transfer-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                to_trust_account_id=destination,
                amount="40.05",
            ),
        )
        assert is_ok(transferred), transferred

        source = call_action(
            ACTIONS["legal-trust-reconciliation"], conn,
            ns(
                trust_account_id=env["trust_account_id"],
                statement_balance="60.05",
            ),
        )
        assert is_ok(source), source
        assert source["book_balance"] == "60.05"
        assert source["calculated_balance"] == "60.05"
        assert source["client_ledger_total"] == "60.05"
        assert source["total_deposits"] == "100.10"
        assert source["total_withdrawals"] == "40.05"
        assert source["is_reconciled"] is True
        source_account_activity = next(
            row for row in source["client_ledger"]
            if row["matter_id"] is None
        )
        assert source_account_activity["balance"] == "-40.05"

        target = call_action(
            ACTIONS["legal-trust-reconciliation"], conn,
            ns(
                trust_account_id=destination,
                statement_balance="40.05",
            ),
        )
        assert is_ok(target), target
        assert target["book_balance"] == "40.05"
        assert target["calculated_balance"] == "40.05"
        assert target["client_ledger_total"] == "40.05"
        assert target["total_deposits"] == "40.05"
        assert target["total_withdrawals"] == "0.00"
        assert target["is_reconciled"] is True

    def test_interest_is_account_level_client_activity(self, conn, env):
        interest = call_action(
            ACTIONS["legal-trust-interest-distribution"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="12.50",
                transaction_date="2026-03-31",
            ),
        )
        assert is_ok(interest), interest
        result = call_action(
            ACTIONS["legal-trust-reconciliation"], conn,
            ns(
                trust_account_id=env["trust_account_id"],
                statement_balance="12.50",
            ),
        )
        assert is_ok(result), result
        assert result["book_balance"] == "12.50"
        assert result["calculated_balance"] == "12.50"
        assert result["client_ledger_total"] == "12.50"
        assert result["total_deposits"] == "12.50"
        assert result["total_withdrawals"] == "0.00"
        assert result["client_ledger"] == [{
            "matter_id": None,
            "title": "Account-level activity",
            "deposits": "12.50",
            "withdrawals": "0.00",
            "balance": "12.50",
        }]
        assert result["is_reconciled"] is True

    def test_no_matter_disbursement_keeps_client_shortfall_unreconciled(
            self, conn, env):
        deposited = call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="1000.00",
                matter_id=env["matter_id"],
            ),
        )
        assert is_ok(deposited), deposited
        disbursed = call_action(
            ACTIONS["legal-disburse-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="300.00",
                payee="Unassigned Payee",
            ),
        )
        assert is_ok(disbursed), disbursed

        result = call_action(
            ACTIONS["legal-trust-reconciliation"], conn,
            ns(
                trust_account_id=env["trust_account_id"],
                statement_balance="700.00",
            ),
        )
        assert is_ok(result), result
        assert result["book_balance"] == "700.00"
        assert result["calculated_balance"] == "700.00"
        assert result["client_ledger_total"] == "1000.00"
        assert result["book_to_client_difference"] == "-300.00"
        assert result["is_reconciled"] is False
        assert all(
            row["matter_id"] is not None
            for row in result["client_ledger"]
        )

    def test_no_matter_deposit_stays_unassigned_and_unreconciled(
            self, conn, env):
        deposited = call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="1000.00",
            ),
        )
        assert is_ok(deposited), deposited

        result = call_action(
            ACTIONS["legal-trust-reconciliation"], conn,
            ns(
                trust_account_id=env["trust_account_id"],
                statement_balance="1000.00",
            ),
        )
        assert is_ok(result), result
        assert result["book_balance"] == "1000.00"
        assert result["calculated_balance"] == "1000.00"
        assert result["client_ledger_total"] == "0.00"
        assert result["book_to_client_difference"] == "1000.00"
        assert result["client_ledger"] == []
        assert result["is_reconciled"] is False

    def test_transaction_company_mismatch_refuses(self, conn, env):
        deposited = call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="25.00",
                matter_id=env["matter_id"],
            ),
        )
        assert is_ok(deposited), deposited
        other_company = seed_company(
            conn, name="Other Legal Firm", abbr="OLF",
        )
        conn.execute(
            "UPDATE legalclaw_trust_transaction SET company_id = ?",
            (other_company,),
        )
        conn.commit()

        result = call_action(
            ACTIONS["legal-trust-reconciliation"], conn,
            ns(
                trust_account_id=env["trust_account_id"],
                statement_balance="25.00",
            ),
        )
        assert is_error(result), result
        assert "company does not match the trust account" in result["message"]

    def test_matter_company_mismatch_refuses(self, conn, env):
        deposited = call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="25.00",
                matter_id=env["matter_id"],
            ),
        )
        assert is_ok(deposited), deposited
        other_company = seed_company(
            conn, name="Other Matter Firm", abbr="OMF",
        )
        conn.execute(
            "UPDATE legalclaw_matter SET company_id = ? WHERE id = ?",
            (other_company, env["matter_id"]),
        )
        conn.commit()

        result = call_action(
            ACTIONS["legal-trust-reconciliation"], conn,
            ns(
                trust_account_id=env["trust_account_id"],
                statement_balance="25.00",
            ),
        )
        assert is_error(result), result
        assert (
            "company does not match its trust account and matter"
            in result["message"]
        )

    def test_independent_statement_book_and_client_drift(self, conn, env):
        deposited = call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="100.00",
                matter_id=env["matter_id"],
            ),
        )
        assert is_ok(deposited), deposited
        conn.execute(
            "UPDATE legalclaw_trust_account SET current_balance = ? "
            "WHERE id = ?",
            ("99.98", env["trust_account_id"]),
        )
        conn.commit()

        result = call_action(
            ACTIONS["legal-trust-reconciliation"], conn,
            ns(
                trust_account_id=env["trust_account_id"],
                statement_balance="99.99",
            ),
        )
        assert is_ok(result), result
        assert result["statement_balance"] == "99.99"
        assert result["book_balance"] == "99.98"
        assert result["calculated_balance"] == "100.00"
        assert result["client_ledger_total"] == "100.00"
        assert result["statement_to_book_difference"] == "0.01"
        assert result["book_to_client_difference"] == "-0.02"
        assert result["is_reconciled"] is False

    def test_successful_reconciliation_is_read_only(self, conn, env):
        deposited = call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="100.10",
                matter_id=env["matter_id"],
            ),
        )
        assert is_ok(deposited), deposited
        interest = call_action(
            ACTIONS["legal-trust-interest-distribution"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="7.25",
                transaction_date="2026-03-31",
            ),
        )
        assert is_ok(interest), interest
        before = self._state(conn)
        result = call_action(
            ACTIONS["legal-trust-reconciliation"], conn,
            ns(
                trust_account_id=env["trust_account_id"],
                statement_balance="107.35",
            ),
        )
        assert is_ok(result), result
        assert result["is_reconciled"] is True
        assert self._state(conn) == before

    def test_ambiguous_transfer_direction_refuses_without_writes(
            self, conn, env):
        destination = seed_trust_account(
            conn, env["company_id"], name="Legacy Destination",
        )
        deposited = call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="50.00",
                matter_id=env["matter_id"],
            ),
        )
        assert is_ok(deposited), deposited
        transferred = call_action(
            ACTIONS["legal-transfer-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                to_trust_account_id=destination,
                amount="10.00",
            ),
        )
        assert is_ok(transferred), transferred
        conn.execute(
            "UPDATE legalclaw_trust_transaction SET description = ? "
            "WHERE trust_account_id = ? AND transaction_type = ?",
            ("Legacy transfer", env["trust_account_id"], "transfer"),
        )
        conn.commit()
        before = self._state(conn)
        result = call_action(
            ACTIONS["legal-trust-reconciliation"], conn,
            ns(
                trust_account_id=env["trust_account_id"],
                statement_balance="40.00",
            ),
        )
        assert is_error(result), result
        assert "stored direction is ambiguous" in result["message"]
        assert self._state(conn) == before

    def test_statement_mismatch_not_reconciled(self, conn, env):
        matter_two = seed_matter(
            conn, env["client_ext_id"], env["company_id"],
            title="Second Matter",
        )
        call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="100.10",
                matter_id=env["matter_id"],
            ),
        )
        call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="200.20",
                matter_id=matter_two,
            ),
        )
        result = call_action(
            ACTIONS["legal-trust-reconciliation"], conn,
            ns(
                trust_account_id=env["trust_account_id"],
                statement_balance="300.29",
            ),
        )
        assert is_ok(result), result
        assert result["is_reconciled"] is False
        assert result["statement_balance"] == "300.29"
        assert result["book_balance"] == "300.30"
        assert result["statement_to_book_difference"] == "-0.01"

    def test_missing_statement_refuses_and_no_write(self, conn, env):
        call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="100.10",
                matter_id=env["matter_id"],
            ),
        )
        trust_account_table = Table("legalclaw_trust_account")
        trust_txn_table = Table("legalclaw_trust_transaction")
        matter_table = Table("legalclaw_matter")
        audit_table = Table("audit_log")
        before_accounts = conn.execute(
            Q.from_(trust_account_table).select(trust_account_table.id, trust_account_table.current_balance).get_sql()
        ).fetchall()
        before_txns = conn.execute(
            Q.from_(trust_txn_table).select(trust_txn_table.id, trust_txn_table.amount).get_sql()
        ).fetchall()
        before_matters = conn.execute(
            Q.from_(matter_table).select(matter_table.id, matter_table.trust_balance).get_sql()
        ).fetchall()
        before_audits = conn.execute(
            Q.from_(audit_table).select(audit_table.id).get_sql()
        ).fetchall()
        result = call_action(
            ACTIONS["legal-trust-reconciliation"], conn,
            ns(trust_account_id=env["trust_account_id"]),
        )
        assert is_error(result), result
        after_accounts = conn.execute(
            Q.from_(trust_account_table).select(trust_account_table.id, trust_account_table.current_balance).get_sql()
        ).fetchall()
        after_txns = conn.execute(
            Q.from_(trust_txn_table).select(trust_txn_table.id, trust_txn_table.amount).get_sql()
        ).fetchall()
        after_matters = conn.execute(
            Q.from_(matter_table).select(matter_table.id, matter_table.trust_balance).get_sql()
        ).fetchall()
        after_audits = conn.execute(
            Q.from_(audit_table).select(audit_table.id).get_sql()
        ).fetchall()
        assert [dict(r) for r in after_accounts] == [dict(r) for r in before_accounts]
        assert [dict(r) for r in after_txns] == [dict(r) for r in before_txns]
        assert [dict(r) for r in after_matters] == [dict(r) for r in before_matters]
        assert [dict(r) for r in after_audits] == [dict(r) for r in before_audits]


class TestTrustBalanceReport:
    """legal-trust-balance-report"""

    def test_balance_report_ok(self, conn, env):
        # Deposit to create trust balance on matter
        call_action(
            ACTIONS["legal-deposit-trust"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="3000.00",
                matter_id=env["matter_id"],
            ),
        )
        result = call_action(
            ACTIONS["legal-trust-balance-report"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1
        assert result["total_trust_balance"] == "3000.00"


class TestTrustInterestDistribution:
    """legal-trust-interest-distribution"""

    def test_interest_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-trust-interest-distribution"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="50.00",
                cost_center_id=env["cost_center_id"],
            ),
        )
        assert is_ok(result), result
        assert result["transaction_type"] == "interest"
        assert result["amount"] == "50.00"

    def test_interest_zero(self, conn, env):
        result = call_action(
            ACTIONS["legal-trust-interest-distribution"], conn,
            ns(
                company_id=env["company_id"],
                trust_account_id=env["trust_account_id"],
                amount="0",
            ),
        )
        assert is_error(result)


# ── Document Tests ─────────────────────────────────────────────────────


class TestAddDocument:
    """legal-add-legal-document"""

    def test_add_document_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-legal-document"], conn,
            ns(
                company_id=env["company_id"],
                doc_title="Complaint - Smith v. Jones",
                document_type="pleading",
                matter_id=env["matter_id"],
                file_name="complaint_v1.pdf",
            ),
        )
        assert is_ok(result), result
        assert result["title"] == "Complaint - Smith v. Jones"
        assert result["document_type"] == "pleading"
        assert result["document_status"] == "draft"
        assert result["version"] == "1"

    def test_add_document_missing_title(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-legal-document"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_error(result)

    def test_add_document_invalid_type(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-legal-document"], conn,
            ns(
                company_id=env["company_id"],
                doc_title="Test",
                document_type="invalid_type",
            ),
        )
        assert is_error(result)


class TestUpdateDocument:
    """legal-update-legal-document"""

    def test_update_document_title(self, conn, env):
        doc_id = seed_document(conn, env["matter_id"], env["company_id"])
        result = call_action(
            ACTIONS["legal-update-legal-document"], conn,
            ns(document_id=doc_id, doc_title="Updated Title"),
        )
        assert is_ok(result), result
        assert "title" in result["updated_fields"]

    def test_update_document_status(self, conn, env):
        doc_id = seed_document(conn, env["matter_id"], env["company_id"])
        result = call_action(
            ACTIONS["legal-update-legal-document"], conn,
            ns(document_id=doc_id, document_status="review"),
        )
        assert is_ok(result), result
        assert "status" in result["updated_fields"]

    def test_update_archived_fails(self, conn, env):
        doc_id = seed_document(conn, env["matter_id"], env["company_id"])
        conn.execute("UPDATE legalclaw_document SET status = 'archived' WHERE id = ?",
                      (doc_id,))
        conn.commit()
        result = call_action(
            ACTIONS["legal-update-legal-document"], conn,
            ns(document_id=doc_id, doc_title="New Title"),
        )
        assert is_error(result)


class TestGetDocument:
    """legal-get-legal-document"""

    def test_get_document_ok(self, conn, env):
        doc_id = seed_document(conn, env["matter_id"], env["company_id"])
        result = call_action(
            ACTIONS["legal-get-legal-document"], conn,
            ns(document_id=doc_id),
        )
        assert is_ok(result), result
        assert result["id"] == doc_id

    def test_get_document_not_found(self, conn, env):
        result = call_action(
            ACTIONS["legal-get-legal-document"], conn,
            ns(document_id="bad-id"),
        )
        assert is_error(result)


class TestListDocuments:
    """legal-list-legal-documents"""

    def test_list_documents_ok(self, conn, env):
        seed_document(conn, env["matter_id"], env["company_id"])
        result = call_action(
            ACTIONS["legal-list-legal-documents"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1

    def test_list_by_type(self, conn, env):
        seed_document(conn, env["matter_id"], env["company_id"],
                      document_type="motion")
        result = call_action(
            ACTIONS["legal-list-legal-documents"], conn,
            ns(company_id=env["company_id"], document_type="motion"),
        )
        assert is_ok(result), result
        assert result["count"] >= 1


class TestFileDocument:
    """legal-file-document"""

    def test_file_document_ok(self, conn, env):
        doc_id = seed_document(conn, env["matter_id"], env["company_id"])
        result = call_action(
            ACTIONS["legal-file-document"], conn,
            ns(document_id=doc_id, court_reference="CASE-2026-001"),
        )
        assert is_ok(result), result
        assert result["document_status"] == "filed"

    def test_file_already_filed(self, conn, env):
        doc_id = seed_document(conn, env["matter_id"], env["company_id"])
        call_action(ACTIONS["legal-file-document"], conn,
                     ns(document_id=doc_id))
        result = call_action(
            ACTIONS["legal-file-document"], conn,
            ns(document_id=doc_id),
        )
        assert is_error(result)


class TestArchiveDocument:
    """legal-archive-document"""

    def test_archive_document_ok(self, conn, env):
        doc_id = seed_document(conn, env["matter_id"], env["company_id"])
        result = call_action(
            ACTIONS["legal-archive-document"], conn,
            ns(document_id=doc_id),
        )
        assert is_ok(result), result
        assert result["document_status"] == "archived"

    def test_archive_already_archived(self, conn, env):
        doc_id = seed_document(conn, env["matter_id"], env["company_id"])
        call_action(ACTIONS["legal-archive-document"], conn,
                     ns(document_id=doc_id))
        result = call_action(
            ACTIONS["legal-archive-document"], conn,
            ns(document_id=doc_id),
        )
        assert is_error(result)


class TestSearchDocuments:
    """legal-search-legal-documents"""

    def test_search_documents_ok(self, conn, env):
        seed_document(conn, env["matter_id"], env["company_id"],
                      title="Motion to Dismiss")
        result = call_action(
            ACTIONS["legal-search-legal-documents"], conn,
            ns(search="Dismiss", company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1

    def test_search_missing_term(self, conn, env):
        result = call_action(
            ACTIONS["legal-search-legal-documents"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_error(result)


class TestAddDocumentVersion:
    """legal-add-document-version"""

    def test_add_version_ok(self, conn, env):
        doc_id = seed_document(conn, env["matter_id"], env["company_id"])
        result = call_action(
            ACTIONS["legal-add-document-version"], conn,
            ns(document_id=doc_id, content="Updated content v2"),
        )
        assert is_ok(result), result
        assert result["previous_version"] == "1"
        assert result["new_version"] == "2"

    def test_add_version_archived_fails(self, conn, env):
        doc_id = seed_document(conn, env["matter_id"], env["company_id"])
        conn.execute("UPDATE legalclaw_document SET status = 'archived' WHERE id = ?",
                      (doc_id,))
        conn.commit()
        result = call_action(
            ACTIONS["legal-add-document-version"], conn,
            ns(document_id=doc_id),
        )
        assert is_error(result)


class TestListDocumentVersions:
    """legal-list-document-versions"""

    def test_list_versions_ok(self, conn, env):
        doc_id = seed_document(conn, env["matter_id"], env["company_id"])
        result = call_action(
            ACTIONS["legal-list-document-versions"], conn,
            ns(document_id=doc_id),
        )
        assert is_ok(result), result
        assert result["document_id"] == doc_id
        assert result["current_version"] == "1"


class TestDocumentIndex:
    """legal-document-index"""

    def test_document_index_ok(self, conn, env):
        seed_document(conn, env["matter_id"], env["company_id"],
                      title="Complaint", document_type="pleading")
        seed_document(conn, env["matter_id"], env["company_id"],
                      title="Contract", document_type="contract")
        result = call_action(
            ACTIONS["legal-document-index"], conn,
            ns(matter_id=env["matter_id"]),
        )
        assert is_ok(result), result
        assert result["total_documents"] >= 2
        assert "by_type" in result

    def test_document_index_missing_matter(self, conn, env):
        result = call_action(
            ACTIONS["legal-document-index"], conn,
            ns(),
        )
        assert is_error(result)
