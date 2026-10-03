"""L1 depth tests for the 12 intake / portal / SOL actions routed via intake.py.

Every action named here already exists in ``intake.py``; eleven of them had no
test at all in this tree and one (``legal-disburse-settlement``) is covered by
``test_settlement_split.py`` for the happy path. Each class below proves what
its action does to (or reads from) the database instead of the response shape:

- write actions: the exact stored row afterwards (exact TEXT values, money as
  exact ``Decimal`` strings), what changed from what to what, and what did not
  change (full-table snapshots, including ``audit_log``);
- read-only actions: the response matches the stored rows read back through the
  same connection, filtering and scoping are exact (other companies, other
  matters, wrong statuses excluded), ordering is exact, and the database is
  identical before and after;
- refusal actions: the refusal message is truthful and the database is
  byte-identical afterwards (a refusal that half-writes is worse than none).

Ledger note (applies to all 12): all of these actions except
``legal-disburse-settlement`` stay off the ledger. ``legal-disburse-settlement``
now reaches the ledger when the trust account is GL-linked, so no
balanced-legs assertion can hold for the others. Stated once here instead of
twelve times below.

The three pure-filter lists (``legal-list-intakes``,
``legal-list-communications``, ``legal-list-task-templates``) perform no input
validation at all (every filter is optional, no required argument), so no
refusal path exists for them; their second test documents the truthful empty
result plus zero writes instead.
"""
import sys
import os
from datetime import date, timedelta
from decimal import Decimal

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from legal_helpers import (  # noqa: E402
    call_action, ns, is_ok, is_error, load_db_query,
    seed_company, seed_customer, seed_client_ext, seed_matter,
    seed_trust_account, seed_document,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS

SNAPSHOT_TABLES = (
    "audit_log",
    "legalclaw_intake",
    "legalclaw_task_template",
    "legalclaw_task_template_item",
    "legalclaw_settlement",
    "legalclaw_communication",
    "legalclaw_calendar_event",
    "legalclaw_matter",
    "legalclaw_client_ext",
    "legalclaw_document",
    "legalclaw_invoice",
    "legalclaw_trust_account",
    "legalclaw_trust_transaction",
)


def _snapshot(conn):
    return {
        table: sorted(
            repr(tuple(row))
            for row in conn.execute("SELECT * FROM %s" % table).fetchall()
        )
        for table in SNAPSHOT_TABLES
    }


def _row(conn, table, row_id):
    return conn.execute(
        "SELECT * FROM %s WHERE id = ?" % table, (row_id,)
    ).fetchone()


# -- legal-disburse-settlement (stored-row effect) ---------------------------


class TestDisburseSettlementDepth:

    def _record(self, conn, env):
        r = call_action(ACTIONS["legal-record-settlement"], conn, ns(
            company_id=env["company_id"], matter_id=env["matter_id"],
            gross_amount="100000.00", contingency_pct="33.33",
            costs_advanced="2500.00", settlement_date="2026-03-02",
            payment_method="check"))
        assert is_ok(r), r
        return r["settlement_id"]

    def test_disburse_keeps_exact_money_and_flips_status(self, conn, env):
        sid = self._record(conn, env)
        before = _row(conn, "legalclaw_settlement", sid)
        assert before["status"] == "pending"
        dep = call_action(ACTIONS["legal-deposit-trust"], conn, ns(
            company_id=env["company_id"], trust_account_id=env["trust_account_id"],
            matter_id=env["matter_id"], amount="100000.00",
            transaction_date="2026-03-01"))
        assert is_ok(dep), dep
        audits = len(conn.execute("SELECT * FROM audit_log").fetchall())

        r = call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
            settlement_id=sid, company_id=env["company_id"],
            trust_account_id=env["trust_account_id"]))
        assert is_ok(r), r
        assert r["settlement_status"] == "disbursed"

        after = _row(conn, "legalclaw_settlement", sid)
        assert after["status"] == "disbursed"
        assert after["gross_amount"] == "100000.00"
        assert after["contingency_pct"] == "33.33"
        assert after["attorney_fee"] == "33330.00"
        assert after["costs_advanced"] == "2500.00"
        assert after["net_to_client"] == "64170.00"
        assert (Decimal(after["attorney_fee"]) + Decimal(after["costs_advanced"])
                + Decimal(after["net_to_client"]) == Decimal(after["gross_amount"]))
        assert len(conn.execute("SELECT * FROM audit_log").fetchall()) == audits + 4

    def test_refuses_missing_settlement_id_byte_identical(self, conn, env):
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-disburse-settlement"], conn, ns(
            settlement_id=None, company_id=env["company_id"]))
        assert is_error(r), r
        assert r["message"] == "--settlement-id is required"
        assert _snapshot(conn) == before


# -- legal-generate-replenishment-request (read-only over stored rows) --------


class TestGenerateReplenishmentRequestDepth:

    def _balances(self, conn, env):
        conn.execute("UPDATE legalclaw_trust_account SET current_balance = ? WHERE id = ?",
                     ("1000.00", env["trust_account_id"]))
        low2 = seed_trust_account(conn, env["company_id"], name="Escrow Two")
        conn.execute("UPDATE legalclaw_trust_account SET current_balance = ? WHERE id = ?",
                     ("2500.00", low2))
        other_co = seed_company(conn, name="Other Firm")
        other_ta = seed_trust_account(conn, other_co, name="Other Trust")
        conn.execute("UPDATE legalclaw_trust_account SET current_balance = ? WHERE id = ?",
                     ("100.00", other_ta))
        conn.commit()
        return low2

    def test_only_below_threshold_accounts_with_exact_deficit(self, conn, env):
        self._balances(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-generate-replenishment-request"], conn, ns(
            company_id=env["company_id"], amount="2000.00"))
        assert is_ok(r), r
        assert r["requests_count"] == 1
        req = r["requests"][0]
        assert req["trust_account_id"] == env["trust_account_id"]
        assert req["current_balance"] == "1000.00"
        assert req["threshold"] == "2000.00"
        assert req["replenishment_amount"] == "1000.00"
        assert (Decimal(req["threshold"]) - Decimal(req["current_balance"])
                == Decimal(req["replenishment_amount"]))
        stored = _row(conn, "legalclaw_trust_account", env["trust_account_id"])
        assert stored["current_balance"] == "1000.00"
        assert _snapshot(conn) == before

    def test_refuses_missing_amount_byte_identical(self, conn, env):
        self._balances(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-generate-replenishment-request"], conn, ns(
            company_id=env["company_id"], amount=None))
        assert is_error(r), r
        assert r["message"] == "--amount is required (threshold amount)"
        assert _snapshot(conn) == before


# -- legal-get-task-template (read-only over stored rows) --------------------


class TestGetTaskTemplateDepth:

    def _template(self, conn, env):
        t = call_action(ACTIONS["legal-add-task-template"], conn, ns(
            company_id=env["company_id"], name="Litigation Kickoff",
            practice_area="litigation", description="Opening checklist"))
        assert is_ok(t), t
        tid = t["template_id"]
        items = [("File complaint", 2, "paralegal"), ("Send hold letter", 0, "attorney"),
                 ("Open file", 1, "clerk")]
        for name, order, role in items:
            r = call_action(ACTIONS["legal-add-task-template-item"], conn, ns(
                company_id=env["company_id"], template_id=tid, task_name=name,
                description="step " + name, due_days_offset=order,
                assigned_role=role, sort_order=order))
            assert is_ok(r), r
        other = call_action(ACTIONS["legal-add-task-template"], conn, ns(
            company_id=env["company_id"], name="Other Template"))
        assert is_ok(other), other
        lonely = call_action(ACTIONS["legal-add-task-template-item"], conn, ns(
            company_id=env["company_id"], template_id=other["template_id"],
            task_name="Elsewhere", sort_order=0))
        assert is_ok(lonely), lonely
        return tid

    def test_template_and_items_match_stored_rows_in_order(self, conn, env):
        tid = self._template(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-get-task-template"], conn, ns(template_id=tid))
        assert is_ok(r), r
        assert r["name"] == "Litigation Kickoff"
        assert r["practice_area"] == "litigation"
        assert r["description"] == "Opening checklist"
        assert r["task_count"] == 3
        stored = _row(conn, "legalclaw_task_template", tid)
        assert (stored["name"], stored["practice_area"], stored["task_count"]) == (
            "Litigation Kickoff", "litigation", 3)
        assert [i["task_name"] for i in r["items"]] == [
            "Send hold letter", "Open file", "File complaint"]
        first = r["items"][0]
        assert first["due_days_offset"] == 0
        assert first["assigned_role"] == "attorney"
        assert "Elsewhere" not in [i["task_name"] for i in r["items"]]
        assert _snapshot(conn) == before

    def test_refuses_missing_template_id_byte_identical(self, conn, env):
        self._template(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-get-task-template"], conn, ns(template_id=None))
        assert is_error(r), r
        assert r["message"] == "--template-id is required"
        assert _snapshot(conn) == before

    def test_refuses_unknown_template_byte_identical(self, conn, env):
        self._template(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-get-task-template"], conn, ns(template_id="no-such-template"))
        assert is_error(r), r
        assert r["message"] == "Task template no-such-template not found"
        assert _snapshot(conn) == before


# -- legal-intake-conversion-report (read-only aggregate over stored rows) ----


class TestIntakeConversionReportDepth:

    def _intakes(self, conn, env):
        ids = []
        for name, area in [("Ava Stone", "litigation"), ("Ben Cole", "corporate"),
                           ("Cara Dove", "litigation"), ("Dan Fox", "estate")]:
            r = call_action(ACTIONS["legal-add-intake"], conn, ns(
                company_id=env["company_id"], contact_name=name, practice_area=area))
            assert is_ok(r), r
            ids.append(r["intake_id"])
        for iid, status in [(ids[0], "converted"), (ids[1], "declined")]:
            u = call_action(ACTIONS["legal-update-intake"], conn, ns(
                company_id=env["company_id"], intake_id=iid, intake_status=status))
            assert is_ok(u), u
        other_co = seed_company(conn, name="Other Firm")
        o = call_action(ACTIONS["legal-add-intake"], conn, ns(
            company_id=other_co, contact_name="Outsider"))
        assert is_ok(o), o
        return ids

    def test_counts_and_rate_match_stored_statuses(self, conn, env):
        ids = self._intakes(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-intake-conversion-report"], conn, ns(
            company_id=env["company_id"]))
        assert is_ok(r), r
        assert r["total_intakes"] == 4
        assert r["converted"] == 1
        assert r["conversion_rate_pct"] == "25.0"
        assert r["by_status"] == {"new": 2, "converted": 1, "declined": 1}
        assert _row(conn, "legalclaw_intake", ids[0])["status"] == "converted"
        assert _row(conn, "legalclaw_intake", ids[1])["status"] == "declined"
        assert _row(conn, "legalclaw_intake", ids[2])["status"] == "new"
        assert _snapshot(conn) == before

    def test_refuses_missing_company_byte_identical(self, conn, env):
        self._intakes(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-intake-conversion-report"], conn, ns(company_id=None))
        assert is_error(r), r
        assert r["message"] == "--company-id is required"
        assert _snapshot(conn) == before


# -- legal-list-approaching-sol (read-only over stored rows) -----------------


class TestListApproachingSolDepth:

    def _events(self, conn, env):
        def add(title, etype, edate, matter=None):
            r = call_action(ACTIONS["legal-add-calendar-event"], conn, ns(
                company_id=env["company_id"], event_title=title, event_type=etype,
                event_date=edate, matter_id=matter or env["matter_id"]))
            assert is_ok(r), r
            return r["id"]

        today = date.today()
        inside = add("SOL inside", "statute_of_limitations", (today + timedelta(days=30)).isoformat())
        add("SOL far", "statute_of_limitations", (today + timedelta(days=90)).isoformat())
        add("SOL past", "statute_of_limitations", (today - timedelta(days=1)).isoformat())
        add("Hearing", "hearing", (today + timedelta(days=10)).isoformat())
        done = add("SOL done", "statute_of_limitations", (today + timedelta(days=10)).isoformat())
        conn.execute("UPDATE legalclaw_calendar_event SET status = 'completed' WHERE id = ?",
                     (done,))
        other_co = seed_company(conn, name="Other Firm")
        other_cust = seed_customer(conn, other_co, "Other Client")
        other_ext = seed_client_ext(conn, other_cust, other_co)
        other_matter = seed_matter(conn, other_ext, other_co)
        o = call_action(ACTIONS["legal-add-calendar-event"], conn, ns(
            company_id=other_co, event_title="SOL elsewhere",
            event_type="statute_of_limitations",
            event_date=(today + timedelta(days=10)).isoformat(), matter_id=other_matter))
        assert is_ok(o), o
        conn.commit()
        return inside

    def test_only_scheduled_sol_inside_window_with_exact_countdown(self, conn, env):
        inside = self._events(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-list-approaching-sol"], conn, ns(
            company_id=env["company_id"], reminder_days=60))
        assert is_ok(r), r
        assert r["within_days"] == 60
        assert r["approaching_count"] == 1
        row = r["matters"][0]
        want_date = (date.today() + timedelta(days=30)).isoformat()
        assert row["event_type"] == "statute_of_limitations"
        assert row["event_date"] == want_date
        assert row["days_until_sol"] == 30
        assert row["matter_title"] == "Smith v. Jones"
        stored = _row(conn, "legalclaw_calendar_event", inside)
        assert (stored["event_date"], stored["event_type"], stored["status"]) == (
            want_date, "statute_of_limitations", "scheduled")
        assert _snapshot(conn) == before

    def test_refuses_missing_company_byte_identical(self, conn, env):
        self._events(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-list-approaching-sol"], conn, ns(
            company_id=None, reminder_days=60))
        assert is_error(r), r
        assert r["message"] == "--company-id is required"
        assert _snapshot(conn) == before


# -- legal-list-communications (read-only over stored rows) ------------------


class TestListCommunicationsDepth:

    def _comms(self, conn, env):
        other_matter = seed_matter(conn, env["client_ext_id"], env["company_id"],
                                   title="Other Matter")
        made = []
        for m, ctype, direction, subject, cdate in [
                (env["matter_id"], "email", "outbound", "Engagement letter", "2026-03-01"),
                (env["matter_id"], "phone", "inbound", "Client call", "2026-03-05"),
                (other_matter, "email", "outbound", "Other matter mail", "2026-03-03")]:
            r = call_action(ACTIONS["legal-add-communication"], conn, ns(
                company_id=env["company_id"], matter_id=m, comm_type=ctype,
                direction=direction, subject=subject, comm_date=cdate))
            assert is_ok(r), r
            made.append(r["communication_id"])
        return made

    def test_matter_and_type_filters_match_stored_rows_in_order(self, conn, env):
        made = self._comms(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-list-communications"], conn, ns(
            matter_id=env["matter_id"]))
        assert is_ok(r), r
        assert r["count"] == 2
        assert [c["subject"] for c in r["communications"]] == ["Client call", "Engagement letter"]
        first = r["communications"][0]
        assert (first["comm_type"], first["direction"], first["date"]) == (
            "phone", "inbound", "2026-03-05")
        stored = _row(conn, "legalclaw_communication", made[1])
        assert (stored["subject"], stored["comm_type"], stored["direction"]) == (
            "Client call", "phone", "inbound")
        typed = call_action(ACTIONS["legal-list-communications"], conn, ns(
            matter_id=env["matter_id"], comm_type="email"))
        assert is_ok(typed), typed
        assert [c["subject"] for c in typed["communications"]] == ["Engagement letter"]
        assert _snapshot(conn) == before

    def test_unknown_company_returns_empty_without_writing(self, conn, env):
        # No input validation exists on this action (all filters optional), so
        # no refusal path can hold; the truthful behaviour is an empty list.
        self._comms(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-list-communications"], conn, ns(
            company_id="no-such-company"))
        assert is_ok(r), r
        assert r["count"] == 0
        assert r["communications"] == []
        assert _snapshot(conn) == before


# -- legal-list-intakes (read-only over stored rows) -------------------------


class TestListIntakesDepth:

    def _intakes(self, conn, env):
        ids = []
        for name, area, created in [("Ava Stone", "litigation", "2026-01-10T10:00:00Z"),
                                   ("Ben Cole", "corporate", "2026-01-11T10:00:00Z"),
                                   ("Cara Dove", "litigation", "2026-01-12T10:00:00Z")]:
            r = call_action(ACTIONS["legal-add-intake"], conn, ns(
                company_id=env["company_id"], contact_name=name, practice_area=area))
            assert is_ok(r), r
            ids.append(r["intake_id"])
            conn.execute("UPDATE legalclaw_intake SET created_at = ? WHERE id = ?",
                         (created, r["intake_id"]))
        u = call_action(ACTIONS["legal-update-intake"], conn, ns(
            company_id=env["company_id"], intake_id=ids[1], intake_status="contacted"))
        assert is_ok(u), u
        other_co = seed_company(conn, name="Other Firm")
        o = call_action(ACTIONS["legal-add-intake"], conn, ns(
            company_id=other_co, contact_name="Ava Stone"))
        assert is_ok(o), o
        conn.commit()
        return ids

    def test_filters_and_order_match_stored_rows(self, conn, env):
        self._intakes(conn, env)
        before = _snapshot(conn)
        all_r = call_action(ACTIONS["legal-list-intakes"], conn, ns(
            company_id=env["company_id"]))
        assert is_ok(all_r), all_r
        assert all_r["count"] == 3
        assert [i["contact_name"] for i in all_r["intakes"]] == [
            "Cara Dove", "Ben Cole", "Ava Stone"]
        by_status = call_action(ACTIONS["legal-list-intakes"], conn, ns(
            company_id=env["company_id"], intake_status="contacted"))
        assert is_ok(by_status), by_status
        assert [i["contact_name"] for i in by_status["intakes"]] == ["Ben Cole"]
        by_area = call_action(ACTIONS["legal-list-intakes"], conn, ns(
            company_id=env["company_id"], practice_area="litigation"))
        assert is_ok(by_area), by_area
        assert [i["contact_name"] for i in by_area["intakes"]] == ["Cara Dove", "Ava Stone"]
        by_search = call_action(ACTIONS["legal-list-intakes"], conn, ns(
            company_id=env["company_id"], search="Stone"))
        assert is_ok(by_search), by_search
        assert [i["contact_name"] for i in by_search["intakes"]] == ["Ava Stone"]
        assert _snapshot(conn) == before

    def test_unknown_company_returns_empty_without_writing(self, conn, env):
        # No input validation exists on this action (all filters optional), so
        # no refusal path can hold; the truthful behaviour is an empty list.
        self._intakes(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-list-intakes"], conn, ns(company_id="no-such-company"))
        assert is_ok(r), r
        assert r["count"] == 0
        assert r["intakes"] == []
        assert _snapshot(conn) == before


# -- legal-list-task-templates (read-only over stored rows) ------------------


class TestListTaskTemplatesDepth:

    def _templates(self, conn, env):
        for name, area in [("Alpha Discovery", "litigation"), ("Beta Closing", "corporate")]:
            r = call_action(ACTIONS["legal-add-task-template"], conn, ns(
                company_id=env["company_id"], name=name, practice_area=area))
            assert is_ok(r), r
        other_co = seed_company(conn, name="Other Firm")
        o = call_action(ACTIONS["legal-add-task-template"], conn, ns(
            company_id=other_co, name="Alpha Discovery"))
        assert is_ok(o), o

    def test_company_and_area_filters_match_stored_rows_in_order(self, conn, env):
        self._templates(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-list-task-templates"], conn, ns(
            company_id=env["company_id"]))
        assert is_ok(r), r
        assert r["count"] == 2
        assert [t["name"] for t in r["task_templates"]] == ["Alpha Discovery", "Beta Closing"]
        assert r["task_templates"][0]["practice_area"] == "litigation"
        filtered = call_action(ACTIONS["legal-list-task-templates"], conn, ns(
            company_id=env["company_id"], practice_area="corporate"))
        assert is_ok(filtered), filtered
        assert [t["name"] for t in filtered["task_templates"]] == ["Beta Closing"]
        assert _snapshot(conn) == before

    def test_unknown_company_returns_empty_without_writing(self, conn, env):
        # No input validation exists on this action (all filters optional), so
        # no refusal path can hold; the truthful behaviour is an empty list.
        self._templates(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-list-task-templates"], conn, ns(
            company_id="no-such-company"))
        assert is_ok(r), r
        assert r["count"] == 0
        assert r["task_templates"] == []
        assert _snapshot(conn) == before


# -- legal-portal-list-documents (read-only over stored rows) ----------------


class TestPortalListDocumentsDepth:

    def _docs(self, conn, env):
        other_matter = seed_matter(conn, env["client_ext_id"], env["company_id"],
                                   title="Other Matter")
        d_filed = seed_document(conn, env["matter_id"], env["company_id"],
                                title="Complaint", document_type="pleading")
        conn.execute("UPDATE legalclaw_document SET status = 'filed', filed_date = ?, "
                     "created_at = ? WHERE id = ?",
                     ("2026-02-10", "2026-02-10T10:00:00Z", d_filed))
        d_final = seed_document(conn, env["matter_id"], env["company_id"],
                                title="Fee Agreement", document_type="contract")
        conn.execute("UPDATE legalclaw_document SET status = 'final', "
                     "created_at = ? WHERE id = ?",
                     ("2026-02-11T10:00:00Z", d_final))
        d_draft = seed_document(conn, env["matter_id"], env["company_id"], title="Draft Memo")
        conn.execute("UPDATE legalclaw_document SET created_at = ? WHERE id = ?",
                     ("2026-02-12T10:00:00Z", d_draft))
        d_arch = seed_document(conn, env["matter_id"], env["company_id"], title="Old Brief")
        conn.execute("UPDATE legalclaw_document SET status = 'archived', "
                     "created_at = ? WHERE id = ?",
                     ("2026-02-13T10:00:00Z", d_arch))
        d_other = seed_document(conn, other_matter, env["company_id"], title="Elsewhere")
        conn.execute("UPDATE legalclaw_document SET status = 'filed', filed_date = ?, "
                     "created_at = ? WHERE id = ?",
                     ("2026-02-10", "2026-02-10T10:00:00Z", d_other))
        conn.commit()
        return d_filed, d_final

    def test_only_final_and_filed_documents_with_exact_fields(self, conn, env):
        d_filed, d_final = self._docs(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-portal-list-documents"], conn, ns(
            matter_id=env["matter_id"]))
        assert is_ok(r), r
        assert r["count"] == 2
        by_id = {d["id"]: d for d in r["documents"]}
        assert set(by_id) == {d_filed, d_final}
        assert by_id[d_filed]["status"] == "filed"
        assert by_id[d_filed]["filed_date"] == "2026-02-10"
        assert by_id[d_filed]["title"] == "Complaint"
        assert by_id[d_final]["status"] == "final"
        assert by_id[d_final]["title"] == "Fee Agreement"
        stored = _row(conn, "legalclaw_document", d_filed)
        assert (stored["title"], stored["status"], stored["filed_date"]) == (
            "Complaint", "filed", "2026-02-10")
        assert _snapshot(conn) == before

    def test_refuses_missing_matter_id_byte_identical(self, conn, env):
        self._docs(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-portal-list-documents"], conn, ns(matter_id=None))
        assert is_error(r), r
        assert r["message"] == "--matter-id is required"
        assert _snapshot(conn) == before


# -- legal-portal-list-invoices (read-only over stored rows) -----------------


class TestPortalListInvoicesDepth:

    def _invoices(self, conn, env):
        other_cust = seed_customer(conn, env["company_id"], "Other Client")
        other_ext = seed_client_ext(conn, other_cust, env["company_id"])
        other_matter = seed_matter(conn, other_ext, env["company_id"], title="Other Matter")
        rows = [
            ("inv-1", env["matter_id"], env["client_ext_id"], "2026-03-01", "2026-04-01",
             "975.00", "400.00", "575.00", "sent"),
            ("inv-2", env["matter_id"], env["client_ext_id"], "2026-02-01", "2026-03-01",
             "250.00", "0", "250.00", "draft"),
            ("inv-3", other_matter, other_ext, "2026-03-01", "2026-04-01",
             "100.00", "0", "100.00", "sent"),
        ]
        for iid, mid, cid, idate, ddate, total, paid, bal, status in rows:
            conn.execute(
                "INSERT INTO legalclaw_invoice (id, matter_id, client_id, invoice_date, "
                "due_date, time_amount, expense_amount, total_amount, paid_amount, "
                "balance, format, status, company_id) "
                "VALUES (?, ?, ?, ?, ?, '0', '0', ?, ?, ?, 'standard', ?, ?)",
                (iid, mid, cid, idate, ddate, total, paid, bal, status, env["company_id"]))
        conn.commit()

    def test_invoices_match_stored_money_and_order(self, conn, env):
        self._invoices(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-portal-list-invoices"], conn, ns(
            client_id=env["client_ext_id"]))
        assert is_ok(r), r
        assert r["count"] == 2
        assert [i["id"] for i in r["invoices"]] == ["inv-1", "inv-2"]
        first = r["invoices"][0]
        assert first["total_amount"] == "975.00"
        assert first["paid_amount"] == "400.00"
        assert first["balance"] == "575.00"
        assert first["status"] == "sent"
        assert (Decimal(first["total_amount"]) - Decimal(first["paid_amount"])
                == Decimal(first["balance"]))
        stored = _row(conn, "legalclaw_invoice", "inv-1")
        assert (stored["total_amount"], stored["paid_amount"], stored["balance"]) == (
            "975.00", "400.00", "575.00")
        assert _snapshot(conn) == before

    def test_refuses_missing_client_id_byte_identical(self, conn, env):
        self._invoices(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-portal-list-invoices"], conn, ns(client_id=None))
        assert is_error(r), r
        assert r["message"] == "--client-id is required"
        assert _snapshot(conn) == before


# -- legal-portal-list-trust-activity (read-only over stored rows) -----------


class TestPortalListTrustActivityDepth:

    def _activity(self, conn, env):
        other_matter = seed_matter(conn, env["client_ext_id"], env["company_id"],
                                   title="Other Matter")
        for mid, tdate, amount, desc in [
                (env["matter_id"], "2026-02-01", "5000.00", "Retainer"),
                (env["matter_id"], "2026-03-01", "1500.00", "Top-up"),
                (other_matter, "2026-02-15", "700.00", "Other matter funds")]:
            r = call_action(ACTIONS["legal-deposit-trust"], conn, ns(
                company_id=env["company_id"], trust_account_id=env["trust_account_id"],
                matter_id=mid, amount=amount, transaction_date=tdate,
                trust_description=desc))
            assert is_ok(r), r

    def test_activity_matches_stored_transactions_in_order(self, conn, env):
        self._activity(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-portal-list-trust-activity"], conn, ns(
            matter_id=env["matter_id"]))
        assert is_ok(r), r
        assert r["count"] == 2
        assert [t["transaction_date"] for t in r["trust_activity"]] == [
            "2026-03-01", "2026-02-01"]
        first = r["trust_activity"][0]
        assert first["transaction_type"] == "deposit"
        assert first["amount"] == "1500.00"
        assert first["description"] == "Top-up"
        assert Decimal(first["amount"]) == Decimal("1500.00")
        db_rows = conn.execute(
            "SELECT id, transaction_type, transaction_date, amount, description "
            "FROM legalclaw_trust_transaction WHERE matter_id = ? "
            "ORDER BY transaction_date DESC", (env["matter_id"],)).fetchall()
        assert [(t["transaction_type"], t["transaction_date"], t["amount"],
                 t["description"]) for t in db_rows] == [
            ("deposit", "2026-03-01", "1500.00", "Top-up"),
            ("deposit", "2026-02-01", "5000.00", "Retainer")]
        assert [t["id"] for t in db_rows] == [t["id"] for t in r["trust_activity"]]
        assert _snapshot(conn) == before

    def test_refuses_missing_matter_id_byte_identical(self, conn, env):
        self._activity(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-portal-list-trust-activity"], conn, ns(matter_id=None))
        assert is_error(r), r
        assert r["message"] == "--matter-id is required"
        assert _snapshot(conn) == before


# -- legal-portal-matter-status (read-only over stored rows) -----------------


class TestPortalMatterStatusDepth:

    def _matters(self, conn, env):
        second = seed_matter(conn, env["client_ext_id"], env["company_id"],
                             title="Estate Plan")
        conn.execute("UPDATE legalclaw_matter SET practice_area = 'estate', status = 'closed', "
                     "opened_date = '2026-02-01' WHERE id = ?", (second,))
        other_cust = seed_customer(conn, env["company_id"], "Other Client")
        other_ext = seed_client_ext(conn, other_cust, env["company_id"])
        seed_matter(conn, other_ext, env["company_id"], title="Other Client Matter")
        conn.commit()
        return second

    def test_matters_match_stored_rows_in_opened_order(self, conn, env):
        second = self._matters(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-portal-matter-status"], conn, ns(
            client_id=env["client_ext_id"]))
        assert is_ok(r), r
        assert r["count"] == 2
        assert [m["id"] for m in r["matters"]] == [second, env["matter_id"]]
        first = r["matters"][0]
        assert (first["title"], first["practice_area"], first["status"],
                first["opened_date"]) == ("Estate Plan", "estate", "closed", "2026-02-01")
        stored = _row(conn, "legalclaw_matter", second)
        assert (stored["title"], stored["practice_area"], stored["status"]) == (
            "Estate Plan", "estate", "closed")
        assert _snapshot(conn) == before

    def test_refuses_missing_client_id_byte_identical(self, conn, env):
        self._matters(conn, env)
        before = _snapshot(conn)
        r = call_action(ACTIONS["legal-portal-matter-status"], conn, ns(client_id=None))
        assert is_error(r), r
        assert r["message"] == "--client-id is required"
        assert _snapshot(conn) == before
