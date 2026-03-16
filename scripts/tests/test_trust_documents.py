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
            ns(trust_account_id=env["trust_account_id"]),
        )
        assert is_ok(result), result
        assert result["book_balance"] == "5000.00"
        assert result["is_reconciled"] is True


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
