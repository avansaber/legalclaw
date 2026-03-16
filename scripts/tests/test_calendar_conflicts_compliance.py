"""L1 tests for LegalClaw calendar, conflicts, and compliance domains.

Covers:
  Calendar:
    - Events: add, update, list, complete
    - Deadlines: add, update, list, complete
  Conflicts:
    - Conflict checks: check, list, report
    - Conflict waivers: add
  Compliance:
    - Bar admissions: add, update, list
    - CLE records: add, list
    - Reports: cle-compliance, matter-profitability, practice-area-analysis
    - Status
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


# ── Calendar Event Tests ───────────────────────────────────────────────


class TestAddCalendarEvent:
    """legal-add-calendar-event"""

    def test_add_event_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-calendar-event"], conn,
            ns(
                company_id=env["company_id"],
                event_title="Smith v. Jones Hearing",
                event_type="hearing",
                event_date="2026-03-15",
                event_time="09:00",
                location="Courtroom 5A",
                matter_id=env["matter_id"],
                is_critical="1",
            ),
        )
        assert is_ok(result), result
        assert result["title"] == "Smith v. Jones Hearing"
        assert result["event_type"] == "hearing"
        assert result["event_status"] == "scheduled"
        assert result["is_critical"] == 1

    def test_add_event_missing_title(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-calendar-event"], conn,
            ns(company_id=env["company_id"], event_date="2026-03-15"),
        )
        assert is_error(result)

    def test_add_event_missing_date(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-calendar-event"], conn,
            ns(company_id=env["company_id"], event_title="Test Event"),
        )
        assert is_error(result)

    def test_add_event_invalid_type(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-calendar-event"], conn,
            ns(
                company_id=env["company_id"],
                event_title="Test",
                event_date="2026-03-15",
                event_type="invalid_type",
            ),
        )
        assert is_error(result)


class TestUpdateCalendarEvent:
    """legal-update-calendar-event"""

    def test_update_event_title(self, conn, env):
        add_res = call_action(
            ACTIONS["legal-add-calendar-event"], conn,
            ns(
                company_id=env["company_id"],
                event_title="Original Title",
                event_date="2026-04-01",
            ),
        )
        assert is_ok(add_res), add_res
        result = call_action(
            ACTIONS["legal-update-calendar-event"], conn,
            ns(event_id=add_res["id"], event_title="Updated Title"),
        )
        assert is_ok(result), result
        assert "title" in result["updated_fields"]

    def test_update_event_status(self, conn, env):
        add_res = call_action(
            ACTIONS["legal-add-calendar-event"], conn,
            ns(
                company_id=env["company_id"],
                event_title="Test Event",
                event_date="2026-04-01",
            ),
        )
        result = call_action(
            ACTIONS["legal-update-calendar-event"], conn,
            ns(event_id=add_res["id"], event_status="postponed"),
        )
        assert is_ok(result), result
        assert "status" in result["updated_fields"]

    def test_update_event_not_found(self, conn, env):
        result = call_action(
            ACTIONS["legal-update-calendar-event"], conn,
            ns(event_id="bad-id", event_title="X"),
        )
        assert is_error(result)


class TestListCalendarEvents:
    """legal-list-calendar-events"""

    def test_list_events_ok(self, conn, env):
        call_action(
            ACTIONS["legal-add-calendar-event"], conn,
            ns(
                company_id=env["company_id"],
                event_title="Hearing",
                event_date="2026-05-01",
                matter_id=env["matter_id"],
            ),
        )
        result = call_action(
            ACTIONS["legal-list-calendar-events"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1

    def test_list_events_by_matter(self, conn, env):
        call_action(
            ACTIONS["legal-add-calendar-event"], conn,
            ns(
                company_id=env["company_id"],
                event_title="Deposition",
                event_date="2026-05-15",
                matter_id=env["matter_id"],
            ),
        )
        result = call_action(
            ACTIONS["legal-list-calendar-events"], conn,
            ns(matter_id=env["matter_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1


class TestCompleteEvent:
    """legal-complete-event"""

    def test_complete_event_ok(self, conn, env):
        add_res = call_action(
            ACTIONS["legal-add-calendar-event"], conn,
            ns(
                company_id=env["company_id"],
                event_title="Client Meeting",
                event_date="2026-03-01",
            ),
        )
        result = call_action(
            ACTIONS["legal-complete-event"], conn,
            ns(event_id=add_res["id"]),
        )
        assert is_ok(result), result
        assert result["event_status"] == "completed"

    def test_complete_already_completed(self, conn, env):
        add_res = call_action(
            ACTIONS["legal-add-calendar-event"], conn,
            ns(
                company_id=env["company_id"],
                event_title="Meeting",
                event_date="2026-03-01",
            ),
        )
        call_action(ACTIONS["legal-complete-event"], conn,
                     ns(event_id=add_res["id"]))
        result = call_action(
            ACTIONS["legal-complete-event"], conn,
            ns(event_id=add_res["id"]),
        )
        assert is_error(result)


# ── Deadline Tests ─────────────────────────────────────────────────────


class TestAddDeadline:
    """legal-add-deadline"""

    def test_add_deadline_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-deadline"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                deadline_title="File Response Brief",
                due_date="2026-04-15",
                deadline_type="response",
                is_court_imposed="1",
                assigned_to="J. Smith",
            ),
        )
        assert is_ok(result), result
        assert result["title"] == "File Response Brief"
        assert result["deadline_type"] == "response"
        assert result["is_court_imposed"] == 1

    def test_add_deadline_missing_title(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-deadline"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                due_date="2026-04-15",
            ),
        )
        assert is_error(result)

    def test_add_deadline_missing_due_date(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-deadline"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                deadline_title="Test",
            ),
        )
        assert is_error(result)


class TestUpdateDeadline:
    """legal-update-deadline"""

    def test_update_deadline_ok(self, conn, env):
        add_res = call_action(
            ACTIONS["legal-add-deadline"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                deadline_title="File Motion",
                due_date="2026-05-01",
            ),
        )
        result = call_action(
            ACTIONS["legal-update-deadline"], conn,
            ns(deadline_id=add_res["id"], due_date="2026-05-15"),
        )
        assert is_ok(result), result
        assert "due_date" in result["updated_fields"]

    def test_update_deadline_not_found(self, conn, env):
        result = call_action(
            ACTIONS["legal-update-deadline"], conn,
            ns(deadline_id="bad-id", due_date="2026-06-01"),
        )
        assert is_error(result)


class TestListDeadlines:
    """legal-list-deadlines"""

    def test_list_deadlines_ok(self, conn, env):
        call_action(
            ACTIONS["legal-add-deadline"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                deadline_title="Discovery Cutoff",
                due_date="2026-06-01",
            ),
        )
        result = call_action(
            ACTIONS["legal-list-deadlines"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1


class TestCompleteDeadline:
    """legal-complete-deadline"""

    def test_complete_deadline_ok(self, conn, env):
        add_res = call_action(
            ACTIONS["legal-add-deadline"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                deadline_title="File Answer",
                due_date="2026-04-01",
            ),
        )
        result = call_action(
            ACTIONS["legal-complete-deadline"], conn,
            ns(deadline_id=add_res["id"]),
        )
        assert is_ok(result), result
        assert result["deadline_status"] == "completed"
        assert "completed_date" in result

    def test_complete_already_completed(self, conn, env):
        add_res = call_action(
            ACTIONS["legal-add-deadline"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                deadline_title="Task",
                due_date="2026-04-01",
            ),
        )
        call_action(ACTIONS["legal-complete-deadline"], conn,
                     ns(deadline_id=add_res["id"]))
        result = call_action(
            ACTIONS["legal-complete-deadline"], conn,
            ns(deadline_id=add_res["id"]),
        )
        assert is_error(result)


# ── Conflict Check Tests ──────────────────────────────────────────────


class TestCheckConflicts:
    """legal-check-conflicts"""

    def test_check_conflicts_clear(self, conn, env):
        result = call_action(
            ACTIONS["legal-check-conflicts"], conn,
            ns(
                company_id=env["company_id"],
                search_name="Unknown Person XYZ",
                checked_by="Conflicts Clerk",
            ),
        )
        assert is_ok(result), result
        assert result["result"] == "clear"
        assert result["matches_found"] == 0

    def test_check_conflicts_potential(self, conn, env):
        # Add a matter party first so we get a match
        call_action(
            ACTIONS["legal-add-matter-party"], conn,
            ns(
                company_id=env["company_id"],
                matter_id=env["matter_id"],
                party_name="Bob Defendant",
                party_type="defendant",
            ),
        )
        result = call_action(
            ACTIONS["legal-check-conflicts"], conn,
            ns(
                company_id=env["company_id"],
                search_name="Bob",
                checked_by="Conflicts Clerk",
            ),
        )
        assert is_ok(result), result
        assert result["result"] == "potential"
        assert result["matches_found"] >= 1

    def test_check_conflicts_missing_name(self, conn, env):
        result = call_action(
            ACTIONS["legal-check-conflicts"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_error(result)


class TestAddConflictWaiver:
    """legal-add-conflict-waiver"""

    def test_add_waiver_ok(self, conn, env):
        # Run a conflict check first
        check = call_action(
            ACTIONS["legal-check-conflicts"], conn,
            ns(
                company_id=env["company_id"],
                search_name="Test Name",
                checked_by="Clerk",
            ),
        )
        assert is_ok(check), check
        result = call_action(
            ACTIONS["legal-add-conflict-waiver"], conn,
            ns(
                company_id=env["company_id"],
                conflict_check_id=check["id"],
                waived_by="Managing Partner",
                waiver_reason="No material conflict",
            ),
        )
        assert is_ok(result), result
        assert result["result"] == "waived"

    def test_add_waiver_missing_check(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-conflict-waiver"], conn,
            ns(
                company_id=env["company_id"],
                conflict_check_id="bad-id",
                waived_by="Partner",
            ),
        )
        assert is_error(result)


class TestListConflictChecks:
    """legal-list-conflict-checks"""

    def test_list_checks_ok(self, conn, env):
        call_action(
            ACTIONS["legal-check-conflicts"], conn,
            ns(
                company_id=env["company_id"],
                search_name="Anyone",
            ),
        )
        result = call_action(
            ACTIONS["legal-list-conflict-checks"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1


class TestConflictReport:
    """legal-conflict-report"""

    def test_conflict_report_ok(self, conn, env):
        call_action(
            ACTIONS["legal-check-conflicts"], conn,
            ns(company_id=env["company_id"], search_name="Someone"),
        )
        result = call_action(
            ACTIONS["legal-conflict-report"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_checks"] >= 1
        assert "by_result" in result


# ── Bar Admission Tests ───────────────────────────────────────────────


class TestAddBarAdmission:
    """legal-add-bar-admission"""

    def test_add_bar_admission_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-bar-admission"], conn,
            ns(
                company_id=env["company_id"],
                attorney_name="John Smith",
                jurisdiction="California",
                bar_number="CA-123456",
                admission_date="2020-01-15",
                cle_hours_required="25",
            ),
        )
        assert is_ok(result), result
        assert result["attorney_name"] == "John Smith"
        assert result["jurisdiction"] == "California"
        assert result["admission_status"] == "active"

    def test_add_bar_admission_missing_attorney(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-bar-admission"], conn,
            ns(company_id=env["company_id"], jurisdiction="CA"),
        )
        assert is_error(result)

    def test_add_bar_admission_missing_jurisdiction(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-bar-admission"], conn,
            ns(company_id=env["company_id"], attorney_name="Test"),
        )
        assert is_error(result)


class TestUpdateBarAdmission:
    """legal-update-bar-admission"""

    def test_update_bar_status(self, conn, env):
        add_res = call_action(
            ACTIONS["legal-add-bar-admission"], conn,
            ns(
                company_id=env["company_id"],
                attorney_name="Test Attorney",
                jurisdiction="New York",
            ),
        )
        result = call_action(
            ACTIONS["legal-update-bar-admission"], conn,
            ns(bar_admission_id=add_res["id"], admission_status="inactive"),
        )
        assert is_ok(result), result
        assert "status" in result["updated_fields"]

    def test_update_bar_not_found(self, conn, env):
        result = call_action(
            ACTIONS["legal-update-bar-admission"], conn,
            ns(bar_admission_id="bad-id", jurisdiction="TX"),
        )
        assert is_error(result)


class TestListBarAdmissions:
    """legal-list-bar-admissions"""

    def test_list_bar_admissions_ok(self, conn, env):
        call_action(
            ACTIONS["legal-add-bar-admission"], conn,
            ns(
                company_id=env["company_id"],
                attorney_name="Partner A",
                jurisdiction="Illinois",
            ),
        )
        result = call_action(
            ACTIONS["legal-list-bar-admissions"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1


# ── CLE Record Tests ──────────────────────────────────────────────────


class TestAddCleRecord:
    """legal-add-cle-record"""

    def test_add_cle_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-cle-record"], conn,
            ns(
                company_id=env["company_id"],
                attorney_name="John Smith",
                course_name="Ethics in Digital Age",
                completion_date="2026-02-15",
                cle_hours="3.0",
                cle_category="ethics",
                cle_provider="State Bar Association",
                certificate_number="CLE-2026-001",
            ),
        )
        assert is_ok(result), result
        assert result["course_name"] == "Ethics in Digital Age"
        assert result["hours"] == "3.0"
        assert result["category"] == "ethics"

    def test_add_cle_linked_to_bar(self, conn, env):
        bar = call_action(
            ACTIONS["legal-add-bar-admission"], conn,
            ns(
                company_id=env["company_id"],
                attorney_name="Jane Doe",
                jurisdiction="Texas",
                cle_hours_required="15",
            ),
        )
        assert is_ok(bar), bar
        result = call_action(
            ACTIONS["legal-add-cle-record"], conn,
            ns(
                company_id=env["company_id"],
                attorney_name="Jane Doe",
                bar_admission_id=bar["id"],
                course_name="Trial Advocacy",
                completion_date="2026-03-01",
                cle_hours="5.0",
            ),
        )
        assert is_ok(result), result
        # Verify bar admission hours were updated
        bar_row = conn.execute(
            "SELECT cle_hours_completed FROM legalclaw_bar_admission WHERE id = ?",
            (bar["id"],)
        ).fetchone()
        assert float(bar_row["cle_hours_completed"]) >= 5.0

    def test_add_cle_missing_course(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-cle-record"], conn,
            ns(
                company_id=env["company_id"],
                attorney_name="Test",
                completion_date="2026-01-01",
            ),
        )
        assert is_error(result)

    def test_add_cle_missing_date(self, conn, env):
        result = call_action(
            ACTIONS["legal-add-cle-record"], conn,
            ns(
                company_id=env["company_id"],
                attorney_name="Test",
                course_name="Course",
            ),
        )
        assert is_error(result)


class TestListCleRecords:
    """legal-list-cle-records"""

    def test_list_cle_ok(self, conn, env):
        call_action(
            ACTIONS["legal-add-cle-record"], conn,
            ns(
                company_id=env["company_id"],
                attorney_name="Smith",
                course_name="General CLE",
                completion_date="2026-01-01",
                cle_hours="2.0",
            ),
        )
        result = call_action(
            ACTIONS["legal-list-cle-records"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1


# ── Compliance & Analysis Reports ─────────────────────────────────────


class TestCleComplianceReport:
    """legal-cle-compliance-report"""

    def test_compliance_report_ok(self, conn, env):
        # Add a bar admission with CLE requirement
        bar = call_action(
            ACTIONS["legal-add-bar-admission"], conn,
            ns(
                company_id=env["company_id"],
                attorney_name="Compliant Attorney",
                jurisdiction="Oregon",
                cle_hours_required="10",
            ),
        )
        # Add CLE records
        call_action(
            ACTIONS["legal-add-cle-record"], conn,
            ns(
                company_id=env["company_id"],
                attorney_name="Compliant Attorney",
                bar_admission_id=bar["id"],
                course_name="Course 1",
                completion_date="2026-01-15",
                cle_hours="10",
            ),
        )
        result = call_action(
            ACTIONS["legal-cle-compliance-report"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_attorneys"] >= 1
        # Should be compliant (completed >= required)
        compliant_atty = [a for a in result["attorneys"]
                          if a["attorney_name"] == "Compliant Attorney"]
        assert len(compliant_atty) == 1
        assert compliant_atty[0]["is_compliant"] is True


class TestMatterProfitabilityReport:
    """legal-matter-profitability-report"""

    def test_profitability_report_ok(self, conn, env):
        seed_time_entry(conn, env["matter_id"], env["company_id"],
                        hours="5.0", rate="300.00")
        result = call_action(
            ACTIONS["legal-matter-profitability-report"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1
        assert result["matters"][0]["matter_id"] == env["matter_id"]


class TestPracticeAreaAnalysis:
    """legal-practice-area-analysis"""

    def test_practice_area_ok(self, conn, env):
        result = call_action(
            ACTIONS["legal-practice-area-analysis"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["count"] >= 1
        # env creates a litigation matter
        areas = [a["practice_area"] for a in result["practice_areas"]]
        assert "litigation" in areas


class TestStatus:
    """status"""

    def test_status_ok(self, conn, env):
        result = call_action(
            ACTIONS["status"], conn,
            ns(),
        )
        assert is_ok(result), result
        assert result["skill"] == "legalclaw"
        assert result["actions_available"] == 69
