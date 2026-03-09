#!/usr/bin/env python3
"""LegalClaw schema extension -- adds legal practice management tables to the shared database.

AI-native legal practice management: matters, time & billing, trust accounting,
documents, calendar, conflicts, compliance.
~16 tables across 7 domains, all prefixed with legalclaw_.
legalclaw_client_ext links to core customer(id) via FK -- no shadow client table.

Prerequisite: ERPClaw init_db.py must have run first (creates foundation tables).
Run: python3 init_db.py [db_path]
"""
import os
import sqlite3
import sys


DEFAULT_DB_PATH = os.path.expanduser("~/.openclaw/erpclaw/data.sqlite")
DISPLAY_NAME = "LegalClaw"

REQUIRED_FOUNDATION = [
    "company", "customer", "naming_series", "audit_log",
]


def init_legalclaw_schema(db_path=None):
    db_path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")

    # Verify ERPClaw foundation
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    missing = [t for t in REQUIRED_FOUNDATION if t not in tables]
    if missing:
        print(f"ERROR: Foundation tables missing: {', '.join(missing)}")
        print("Run erpclaw-setup first: clawhub install erpclaw-setup")
        conn.close()
        sys.exit(1)

    conn.executescript("""
        -- ==========================================================
        -- LegalClaw Domain Tables
        -- ~16 tables, 7 domains, legalclaw_ prefix
        -- Convention: TEXT for IDs (UUID4), TEXT for money (Decimal),
        --             TEXT for dates (ISO-8601)
        -- ==========================================================


        -- ==========================================================
        -- DOMAIN 1: MATTER MANAGEMENT (3 tables)
        -- ==========================================================

        CREATE TABLE IF NOT EXISTS legalclaw_client_ext (
            id              TEXT PRIMARY KEY,
            naming_series   TEXT DEFAULT 'LCLI-',
            customer_id     TEXT NOT NULL REFERENCES customer(id),
            client_type     TEXT DEFAULT 'individual'
                            CHECK(client_type IN ('individual','business','government','nonprofit')),
            billing_rate    TEXT,
            is_active       INTEGER DEFAULT 1,
            company_id      TEXT NOT NULL REFERENCES company(id),
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_legalclaw_client_ext_company
            ON legalclaw_client_ext(company_id);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_client_ext_customer
            ON legalclaw_client_ext(customer_id);

        CREATE TABLE IF NOT EXISTS legalclaw_matter (
            id              TEXT PRIMARY KEY,
            naming_series   TEXT,
            client_id       TEXT NOT NULL REFERENCES legalclaw_client_ext(id),
            matter_number   TEXT,
            title           TEXT NOT NULL,
            practice_area   TEXT NOT NULL DEFAULT 'general'
                            CHECK(practice_area IN ('general','corporate','litigation','real_estate',
                                  'family','criminal','ip','employment','tax','estate',
                                  'bankruptcy','immigration','other')),
            description     TEXT,
            lead_attorney   TEXT,
            billing_method  TEXT NOT NULL DEFAULT 'hourly'
                            CHECK(billing_method IN ('hourly','flat_fee','contingency','retainer','pro_bono')),
            billing_rate    TEXT DEFAULT '0',
            budget          TEXT DEFAULT '0',
            billed_amount   TEXT NOT NULL DEFAULT '0',
            collected_amount TEXT NOT NULL DEFAULT '0',
            trust_balance   TEXT NOT NULL DEFAULT '0',
            opened_date     TEXT NOT NULL DEFAULT (date('now')),
            closed_date     TEXT,
            status          TEXT NOT NULL DEFAULT 'active'
                            CHECK(status IN ('active','pending','on_hold','closed','archived')),
            notes           TEXT,
            company_id      TEXT NOT NULL REFERENCES company(id),
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_legalclaw_matter_company
            ON legalclaw_matter(company_id);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_matter_client
            ON legalclaw_matter(client_id);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_matter_status
            ON legalclaw_matter(status);

        CREATE TABLE IF NOT EXISTS legalclaw_matter_party (
            id              TEXT PRIMARY KEY,
            matter_id       TEXT NOT NULL REFERENCES legalclaw_matter(id) ON DELETE CASCADE,
            party_name      TEXT NOT NULL,
            party_type      TEXT NOT NULL DEFAULT 'party'
                            CHECK(party_type IN ('plaintiff','defendant','witness','expert',
                                  'opposing_counsel','judge','mediator','party','other')),
            role            TEXT,
            contact_info    TEXT,
            notes           TEXT,
            company_id      TEXT NOT NULL REFERENCES company(id),
            created_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_legalclaw_matter_party_matter
            ON legalclaw_matter_party(matter_id);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_matter_party_name
            ON legalclaw_matter_party(party_name);


        -- ==========================================================
        -- DOMAIN 2: TIME & BILLING (3 tables)
        -- ==========================================================

        CREATE TABLE IF NOT EXISTS legalclaw_time_entry (
            id              TEXT PRIMARY KEY,
            matter_id       TEXT NOT NULL REFERENCES legalclaw_matter(id),
            attorney        TEXT NOT NULL,
            entry_date      TEXT NOT NULL DEFAULT (date('now')),
            hours           TEXT NOT NULL DEFAULT '0',
            rate            TEXT NOT NULL DEFAULT '0',
            amount          TEXT NOT NULL DEFAULT '0',
            description     TEXT NOT NULL,
            utbms_code      TEXT,
            is_billable     INTEGER NOT NULL DEFAULT 1,
            is_billed       INTEGER NOT NULL DEFAULT 0,
            invoice_id      TEXT,
            company_id      TEXT NOT NULL REFERENCES company(id),
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_legalclaw_time_entry_matter
            ON legalclaw_time_entry(matter_id);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_time_entry_attorney
            ON legalclaw_time_entry(attorney);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_time_entry_billed
            ON legalclaw_time_entry(is_billed);

        CREATE TABLE IF NOT EXISTS legalclaw_expense (
            id              TEXT PRIMARY KEY,
            matter_id       TEXT NOT NULL REFERENCES legalclaw_matter(id),
            expense_date    TEXT NOT NULL DEFAULT (date('now')),
            amount          TEXT NOT NULL DEFAULT '0',
            category        TEXT NOT NULL DEFAULT 'filing'
                            CHECK(category IN ('filing','courier','copying','expert','travel',
                                  'postage','research','deposition','mediation','other')),
            description     TEXT,
            is_billable     INTEGER NOT NULL DEFAULT 1,
            is_billed       INTEGER NOT NULL DEFAULT 0,
            invoice_id      TEXT,
            receipt_reference TEXT,
            company_id      TEXT NOT NULL REFERENCES company(id),
            created_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_legalclaw_expense_matter
            ON legalclaw_expense(matter_id);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_expense_billed
            ON legalclaw_expense(is_billed);

        CREATE TABLE IF NOT EXISTS legalclaw_invoice (
            id              TEXT PRIMARY KEY,
            naming_series   TEXT,
            matter_id       TEXT NOT NULL REFERENCES legalclaw_matter(id),
            client_id       TEXT NOT NULL REFERENCES legalclaw_client_ext(id),
            invoice_date    TEXT NOT NULL DEFAULT (date('now')),
            due_date        TEXT,
            time_amount     TEXT NOT NULL DEFAULT '0',
            expense_amount  TEXT NOT NULL DEFAULT '0',
            total_amount    TEXT NOT NULL DEFAULT '0',
            paid_amount     TEXT NOT NULL DEFAULT '0',
            balance         TEXT NOT NULL DEFAULT '0',
            format          TEXT NOT NULL DEFAULT 'standard'
                            CHECK(format IN ('standard','ledes')),
            status          TEXT NOT NULL DEFAULT 'draft'
                            CHECK(status IN ('draft','sent','paid','partially_paid','overdue','written_off')),
            sales_invoice_id TEXT,
            notes           TEXT,
            company_id      TEXT NOT NULL REFERENCES company(id),
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_legalclaw_invoice_matter
            ON legalclaw_invoice(matter_id);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_invoice_client
            ON legalclaw_invoice(client_id);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_invoice_status
            ON legalclaw_invoice(status);


        -- ==========================================================
        -- DOMAIN 3: TRUST ACCOUNTING (2 tables)
        -- ==========================================================

        CREATE TABLE IF NOT EXISTS legalclaw_trust_account (
            id              TEXT PRIMARY KEY,
            naming_series   TEXT,
            name            TEXT NOT NULL,
            bank_name       TEXT,
            account_number  TEXT,
            account_type    TEXT NOT NULL DEFAULT 'iolta'
                            CHECK(account_type IN ('iolta','escrow','retainer','other')),
            current_balance TEXT NOT NULL DEFAULT '0',
            gl_account_id   TEXT REFERENCES account(id),
            trust_liability_account_id TEXT REFERENCES account(id),
            interest_income_account_id TEXT REFERENCES account(id),
            company_id      TEXT NOT NULL REFERENCES company(id),
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_legalclaw_trust_account_company
            ON legalclaw_trust_account(company_id);

        CREATE TABLE IF NOT EXISTS legalclaw_trust_transaction (
            id                  TEXT PRIMARY KEY,
            trust_account_id    TEXT NOT NULL REFERENCES legalclaw_trust_account(id),
            matter_id           TEXT REFERENCES legalclaw_matter(id),
            transaction_type    TEXT NOT NULL DEFAULT 'deposit'
                                CHECK(transaction_type IN ('deposit','disbursement','transfer','interest','fee')),
            transaction_date    TEXT NOT NULL DEFAULT (date('now')),
            amount              TEXT NOT NULL DEFAULT '0',
            reference           TEXT,
            payee               TEXT,
            description         TEXT,
            gl_entry_ids        TEXT,
            company_id          TEXT NOT NULL REFERENCES company(id),
            created_at          TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_legalclaw_trust_txn_account
            ON legalclaw_trust_transaction(trust_account_id);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_trust_txn_matter
            ON legalclaw_trust_transaction(matter_id);


        -- ==========================================================
        -- DOMAIN 4: DOCUMENT MANAGEMENT (1 table)
        -- ==========================================================

        CREATE TABLE IF NOT EXISTS legalclaw_document (
            id              TEXT PRIMARY KEY,
            naming_series   TEXT,
            matter_id       TEXT REFERENCES legalclaw_matter(id),
            title           TEXT NOT NULL,
            document_type   TEXT NOT NULL DEFAULT 'general'
                            CHECK(document_type IN ('pleading','motion','brief','contract',
                                  'correspondence','discovery','evidence','order','general','other')),
            file_name       TEXT,
            content         TEXT,
            version         TEXT NOT NULL DEFAULT '1',
            status          TEXT NOT NULL DEFAULT 'draft'
                            CHECK(status IN ('draft','review','final','filed','archived')),
            filed_date      TEXT,
            court_reference TEXT,
            company_id      TEXT NOT NULL REFERENCES company(id),
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_legalclaw_document_matter
            ON legalclaw_document(matter_id);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_document_type
            ON legalclaw_document(document_type);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_document_status
            ON legalclaw_document(status);


        -- ==========================================================
        -- DOMAIN 5: CALENDAR & DEADLINES (2 tables)
        -- ==========================================================

        CREATE TABLE IF NOT EXISTS legalclaw_calendar_event (
            id              TEXT PRIMARY KEY,
            matter_id       TEXT REFERENCES legalclaw_matter(id),
            title           TEXT NOT NULL,
            event_type      TEXT NOT NULL DEFAULT 'hearing'
                            CHECK(event_type IN ('hearing','deposition','filing_deadline',
                                  'statute_of_limitations','trial','mediation','meeting','other')),
            event_date      TEXT NOT NULL,
            event_time      TEXT,
            location        TEXT,
            description     TEXT,
            reminder_days   INTEGER DEFAULT 7,
            is_critical     INTEGER NOT NULL DEFAULT 0,
            status          TEXT NOT NULL DEFAULT 'scheduled'
                            CHECK(status IN ('scheduled','completed','cancelled','postponed')),
            company_id      TEXT NOT NULL REFERENCES company(id),
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_legalclaw_event_matter
            ON legalclaw_calendar_event(matter_id);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_event_date
            ON legalclaw_calendar_event(event_date);

        CREATE TABLE IF NOT EXISTS legalclaw_deadline (
            id              TEXT PRIMARY KEY,
            matter_id       TEXT NOT NULL REFERENCES legalclaw_matter(id),
            title           TEXT NOT NULL,
            deadline_type   TEXT NOT NULL DEFAULT 'filing'
                            CHECK(deadline_type IN ('filing','response','discovery','statute','appeal','other')),
            due_date        TEXT NOT NULL,
            is_court_imposed INTEGER NOT NULL DEFAULT 0,
            assigned_to     TEXT,
            is_completed    INTEGER NOT NULL DEFAULT 0,
            completed_date  TEXT,
            notes           TEXT,
            company_id      TEXT NOT NULL REFERENCES company(id),
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_legalclaw_deadline_matter
            ON legalclaw_deadline(matter_id);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_deadline_due
            ON legalclaw_deadline(due_date);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_deadline_completed
            ON legalclaw_deadline(is_completed);


        -- ==========================================================
        -- DOMAIN 6: CONFLICT CHECKING (2 tables)
        -- ==========================================================

        CREATE TABLE IF NOT EXISTS legalclaw_conflict_check (
            id              TEXT PRIMARY KEY,
            search_name     TEXT NOT NULL,
            checked_date    TEXT NOT NULL DEFAULT (date('now')),
            checked_by      TEXT,
            matches_found   INTEGER NOT NULL DEFAULT 0,
            match_details   TEXT,
            result          TEXT NOT NULL DEFAULT 'clear'
                            CHECK(result IN ('clear','conflict','potential','waived')),
            matter_id       TEXT REFERENCES legalclaw_matter(id),
            company_id      TEXT NOT NULL REFERENCES company(id),
            created_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_legalclaw_conflict_search
            ON legalclaw_conflict_check(search_name);

        CREATE TABLE IF NOT EXISTS legalclaw_conflict_waiver (
            id              TEXT PRIMARY KEY,
            conflict_check_id TEXT NOT NULL REFERENCES legalclaw_conflict_check(id),
            matter_id       TEXT REFERENCES legalclaw_matter(id),
            waived_by       TEXT NOT NULL,
            waiver_date     TEXT NOT NULL DEFAULT (date('now')),
            reason          TEXT,
            company_id      TEXT NOT NULL REFERENCES company(id),
            created_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_legalclaw_waiver_check
            ON legalclaw_conflict_waiver(conflict_check_id);


        -- ==========================================================
        -- DOMAIN 7: COMPLIANCE (2 tables)
        -- ==========================================================

        CREATE TABLE IF NOT EXISTS legalclaw_bar_admission (
            id                  TEXT PRIMARY KEY,
            attorney_name       TEXT NOT NULL,
            bar_number          TEXT,
            jurisdiction        TEXT NOT NULL,
            admission_date      TEXT,
            expiry_date         TEXT,
            status              TEXT NOT NULL DEFAULT 'active'
                                CHECK(status IN ('active','inactive','suspended','retired')),
            cle_hours_required  TEXT DEFAULT '0',
            cle_hours_completed TEXT DEFAULT '0',
            company_id          TEXT NOT NULL REFERENCES company(id),
            created_at          TEXT DEFAULT (datetime('now')),
            updated_at          TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_legalclaw_bar_attorney
            ON legalclaw_bar_admission(attorney_name);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_bar_jurisdiction
            ON legalclaw_bar_admission(jurisdiction);

        CREATE TABLE IF NOT EXISTS legalclaw_cle_record (
            id                  TEXT PRIMARY KEY,
            attorney_name       TEXT NOT NULL,
            bar_admission_id    TEXT REFERENCES legalclaw_bar_admission(id),
            course_name         TEXT NOT NULL,
            provider            TEXT,
            completion_date     TEXT NOT NULL,
            hours               TEXT NOT NULL DEFAULT '0',
            category            TEXT DEFAULT 'general'
                                CHECK(category IN ('general','ethics','professionalism','diversity',
                                      'substance_abuse','other')),
            certificate_number  TEXT,
            company_id          TEXT NOT NULL REFERENCES company(id),
            created_at          TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_legalclaw_cle_attorney
            ON legalclaw_cle_record(attorney_name);
        CREATE INDEX IF NOT EXISTS idx_legalclaw_cle_bar
            ON legalclaw_cle_record(bar_admission_id);
    """)

    conn.commit()
    conn.close()
    print(f"{DISPLAY_NAME}: Schema initialized ({db_path})")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH
    init_legalclaw_schema(path)
