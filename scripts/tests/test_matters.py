"""L1 tests for LegalClaw matters domain.

Covers:
  - Client ext: get, update, list (add skipped -- uses cross_skill subprocess)
  - Matters: add, update, get, list, close, reopen
  - Matter parties: add, list
  - Matter summary, client portfolio
"""
import pytest
import sys
import os

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from legal_helpers import (
    call_action, ns, is_ok, is_error, load_db_query,
    seed_company, seed_customer, seed_client_ext, seed_matter,
    seed_naming_series, seed_time_entry, seed_expense,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS


# ── Client Extension Tests ─────────────────────────────────────────────


class TestGetClient:
    """legal-get-client"""

    def test_get_client_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-get-client"], conn,
            ns(client_id=env["client_ext_id"]),
        )
        assert is_ok(result), result
        assert result["id"] == env["client_ext_id"]
        assert result["client_type"] == "individual"
        assert result["name"] == "Jane Client"

    def test_get_client_missing_id(self, conn, env):
        result = call_action(
            ACTIONS["legal-get-client"], conn,
            ns(client_id=None),
        )
        assert is_error(result)

    def test_get_client_not_found(self, conn, env):
        result = call_action(
            ACTIONS["legal-get-client"], conn,
            ns(client_id="nonexistent-id"),
        )
        assert is_error(result)


class TestUpdateClient:
    """legal-update-client"""

    def test_update_client_type(self, conn, env):
        result = call_action(
            ACTIONS["legal-update-client"], conn,
            ns(client_id=env["client_ext_id"], client_type="business"),
        )
        assert is_ok(result), result
        assert "client_type" in result["updated_fields"]

    def test_update_billing_rate(self, conn, env):
        result = call_action(
            ACTIONS["legal-update-client"], conn,
            ns(client_id=env["client_ext_id"], billing_rate="450.00"),
        )
        assert is_ok(result), result
        assert "billing_rate" in result["updated_fields"]

    def test_update_no_fields(self, conn, env):
        result = call_action(
            ACTIONS["legal-update-client"], conn,
            ns(client_id=env["client_ext_id"]),
        )
        assert is_error(result)

    def test_update_client_not_found(self, conn, env):
        result = call_action(
            ACTIONS["legal-update-client"], conn,
            ns(client_id="bad-id", client_type="business"),
        )
        assert is_error(result)


class TestListClients:
    """legal-list-clients"""

    def test_list_clients_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-list-clients"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1
        assert len(result["clients"]) >= 1

    def test_list_clients_empty(self, conn, env):
        # Use a different company with no clients
        cid2 = seed_company(conn, "Empty Firm")
        result = call_action(
            ACTIONS["legal-list-clients"], conn,
            ns(company_id=cid2),
        )
        assert is_ok(result), result
        assert result["count"] == 0


# ── Matter Tests ───────────────────────────────────────────────────────


class TestAddMatter:
    """legal-add-matter"""

    def test_add_matter_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-matter"], conn,
            ns(
                company_id=env["company_id"],
                client_id=env["client_ext_id"],
                title="Contract Dispute",
                practice_area="corporate",
                billing_method="hourly",
                budget="50000",
            ),
        )
        assert is_ok(result), result
        assert result["title"] == "Contract Dispute"
        assert result["practice_area"] == "corporate"
        assert result["billing_method"] == "hourly"
        assert result["matter_status"] == "active"
        assert "id" in result

    def test_add_matter_missing_title(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-matter"], conn,
            ns(
                company_id=env["company_id"],
                client_id=env["client_ext_id"],
            ),
        )
        assert is_error(result)

    def test_add_matter_missing_client(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-matter"], conn,
            ns(
                company_id=env["company_id"],
                title="Test Matter",
            ),
        )
        assert is_error(result)

    def test_add_matter_invalid_practice_area(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-matter"], conn,
            ns(
                company_id=env["company_id"],
                client_id=env["client_ext_id"],
                title="Test",
                practice_area="invalid_area",
            ),
        )
        assert is_error(result)


class TestUpdateMatter:
    """legal-update-matter"""

    def test_update_matter_title(self, conn, env):
        result = call_action(
            ACTIONS["legal-update-matter"], conn,
            ns(matter_id=env["matter_id"], title="Updated Title"),
        )
        assert is_ok(result), result
        assert "title" in result["updated_fields"]

    def test_update_matter_status(self, conn, env):
        result = call_action(
            ACTIONS["legal-update-matter"], conn,
            ns(matter_id=env["matter_id"], matter_status="on_hold"),
        )
        assert is_ok(result), result
        assert "status" in result["updated_fields"]

    def test_update_matter_not_found(self, conn, env):
        result = call_action(
            ACTIONS["legal-update-matter"], conn,
            ns(matter_id="bad-id", title="X"),
        )
        assert is_error(result)

    def test_update_matter_no_fields(self, conn, env):
        result = call_action(
            ACTIONS["legal-update-matter"], conn,
            ns(matter_id=env["matter_id"]),
        )
        assert is_error(result)


class TestGetMatter:
    """legal-get-matter"""

    def test_get_matter_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-get-matter"], conn,
            ns(matter_id=env["matter_id"]),
        )
        assert is_ok(result), result
        assert result["id"] == env["matter_id"]
        assert result["title"] == "Smith v. Jones"

    def test_get_matter_not_found(self, conn, env):
        result = call_action(
            ACTIONS["legal-get-matter"], conn,
            ns(matter_id="nonexistent"),
        )
        assert is_error(result)


class TestListMatters:
    """legal-list-matters"""

    def test_list_matters_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-list-matters"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1

    def test_list_matters_by_client(self, conn, env):
        result = call_action(
            ACTIONS["legal-list-matters"], conn,
            ns(client_id=env["client_ext_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1

    def test_list_matters_by_status(self, conn, env):
        result = call_action(
            ACTIONS["legal-list-matters"], conn,
            ns(company_id=env["company_id"], matter_status="active"),
        )
        assert is_ok(result), result
        assert result["count"] >= 1


class TestCloseMatter:
    """legal-close-matter"""

    def test_close_matter_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-close-matter"], conn,
            ns(matter_id=env["matter_id"]),
        )
        assert is_ok(result), result
        assert result["matter_status"] == "closed"
        assert "closed_date" in result

    def test_close_already_closed(self, conn, env):
        # Close first
        call_action(ACTIONS["legal-close-matter"], conn,
                     ns(matter_id=env["matter_id"]))
        # Try to close again
        result = call_action(
            ACTIONS["legal-close-matter"], conn,
            ns(matter_id=env["matter_id"]),
        )
        assert is_error(result)


class TestReopenMatter:
    """legal-reopen-matter"""

    def test_reopen_matter_ok(self, conn, env):
        # Close first
        call_action(ACTIONS["legal-close-matter"], conn,
                     ns(matter_id=env["matter_id"]))
        # Reopen
        result = call_action(
            ACTIONS["legal-reopen-matter"], conn,
            ns(matter_id=env["matter_id"]),
        )
        assert is_ok(result), result
        assert result["matter_status"] == "active"

    def test_reopen_non_closed(self, conn, env):
        result = call_action(
            ACTIONS["legal-reopen-matter"], conn,
            ns(matter_id=env["matter_id"]),
        )
        assert is_error(result)


# ── Matter Party Tests ─────────────────────────────────────────────────


class TestAddMatterParty:
    """legal-add-matter-party"""

    def test_add_party_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-matter-party"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                party_name="Bob Defendant",
                party_type="defendant",
                role="Primary defendant",
            ),
        )
        assert is_ok(result), result
        assert result["party_name"] == "Bob Defendant"
        assert result["party_type"] == "defendant"

    def test_add_party_missing_name(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-matter-party"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
            ),
        )
        assert is_error(result)

    def test_add_party_invalid_type(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-matter-party"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                party_name="Test",
                party_type="invalid_type",
            ),
        )
        assert is_error(result)


class TestListMatterParties:
    """legal-list-matter-parties"""

    def test_list_parties_ok(self, conn, env):
        # Add a party first
        call_action(
            ACTIONS["legal-add-matter-party"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                party_name="Alice Witness",
                party_type="witness",
            ),
        )
        result = call_action(
            ACTIONS["legal-list-matter-parties"], conn,
            ns(matter_id=env["matter_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1


# ── Matter Summary & Client Portfolio ──────────────────────────────────


class TestMatterSummary:
    """legal-matter-summary"""

    def test_matter_summary_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-matter-summary"], conn,
            ns(matter_id=env["matter_id"]),
        )
        assert is_ok(result), result
        assert result["matter_id"] == env["matter_id"]
        assert "time_entries" in result
        assert "expenses" in result
        assert "trust_balance" in result

    def test_matter_summary_with_data(self, conn, env):
        # Seed some time entries and expenses
        seed_time_entry(conn, env["matter_id"], env["company_id"])
        seed_expense(conn, env["matter_id"], env["company_id"])
        result = call_action(
            ACTIONS["legal-matter-summary"], conn,
            ns(matter_id=env["matter_id"]),
        )
        assert is_ok(result), result
        assert result["time_entries"] >= 1
        assert result["expenses"] >= 1


class TestClientPortfolio:
    """legal-client-portfolio"""

    def test_client_portfolio_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-client-portfolio"], conn,
            ns(client_id=env["client_ext_id"]),
        )
        assert is_ok(result), result
        assert result["client_id"] == env["client_ext_id"]
        assert result["total_matters"] >= 1
        assert result["active_matters"] >= 1

    def test_client_portfolio_not_found(self, conn, env):
        result = call_action(
            ACTIONS["legal-client-portfolio"], conn,
            ns(client_id="bad-id"),
        )
        assert is_error(result)
