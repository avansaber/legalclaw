#!/usr/bin/env python3
"""LegalClaw schema extension -- adds legal practice management tables to the shared database.

AI-native legal practice management: matters, time & billing, trust accounting,
documents, calendar, conflicts, compliance, intake, task templates, settlements
and the communication log.
20 tables across 11 domains, 43 indexes, all prefixed with legalclaw_. (The
pre-conversion docstring said "~16 tables across 7 domains" -- stale on both
counts: domains 8 through 11 and their five tables were added after it was
written.)
legalclaw_client_ext links to core customer(id) via FK -- no shadow client table.

Prerequisite: ERPClaw init_db.py must have run first (creates foundation tables).
Run: python3 init_db.py [db_path]

ADR-0034 phase 2 bulk-39. Schema declared as metadata and provisioned through
`erpclaw_lib.seam`, which emits dialect-correct DDL, replacing a hand-written
``CREATE TABLE`` block opened with ``sqlite3.connect`` that could not run on
PostgreSQL at all. Conversion rules are the pilot's (`erpclaw-esign`): seam
vocabulary only, IDs and every amount stay TEXT on every backend, and
``primary_key=True, nullable=True`` reproduces SQLite's ``id TEXT PRIMARY KEY``
without adding a NOT NULL that never shipped.

Money is the whole point of this module and none of it moves off TEXT: matter
budgets and billed/collected totals, time-entry hours and rates, expenses,
invoice time/expense/total/paid/balance, settlement gross, contingency
percentage, attorney fee, costs advanced and net-to-client, and -- most
consequentially -- ``legalclaw_trust_account.current_balance``,
``legalclaw_trust_transaction.amount`` and ``legalclaw_matter.trust_balance``.
A trust balance is client money held in an IOLTA account; retyping one to a
numeric type would trade exact Decimal strings for float rounding on funds that
are not the firm's to round.

Six columns across the five late tables shipped ``DEFAULT (datetime('now'))``,
which is SQLite's spelling and the single reason this module could not provision
on PostgreSQL even by hand. They are declared with `seam.now_default()`, which
renders ``(datetime('now'))`` on SQLite -- byte-identical to what shipped -- and
``CURRENT_TIMESTAMP`` on PostgreSQL. The fifteen older tables spell the same idea
``CURRENT_TIMESTAMP`` and are transcribed verbatim.

Asymmetries in the shipped DDL are transcribed rather than tidied:

* ``legalclaw_communication.client_id`` is a bare TEXT column, where
  ``legalclaw_matter.client_id`` and ``legalclaw_invoice.client_id`` both
  reference ``legalclaw_client_ext(id)``.
* ``legalclaw_intake.converted_matter_id`` names a matter without referencing
  one, and ``legalclaw_task_template_item.predecessor_item_id`` is a
  self-reference in name only. So are ``legalclaw_time_entry.invoice_id``,
  ``legalclaw_expense.invoice_id`` and ``legalclaw_invoice.sales_invoice_id``.
* ``legalclaw_task_template_item`` is the only table in the module with no
  ``company_id`` at all.
* ``legalclaw_matter_party.matter_id`` is the only foreign key in the module
  carrying ``ON DELETE CASCADE``; every other one leaves the action unspecified,
  including the sibling ``legalclaw_deadline.matter_id``.
* The five late tables spell status-with-a-default as nullable where the older
  fifteen spell it NOT NULL, and only ``legalclaw_client_ext.naming_series``
  carries a default prefix while the other four naming_series columns are bare.

Each of those is a schema decision to make or unmake deliberately; a conversion
is not the place.
"""
import importlib.util
import os
import sys

# Bootstrap the shared lib only when it is not already reachable -- an
# unconditional insert at position 0 overrides a caller that deliberately bound a
# different tree (ADR-0034 phase 2 step 2d).
if importlib.util.find_spec("erpclaw_lib") is None:
    sys.path.insert(0, os.path.join(os.path.expanduser(
        os.environ.get("ERPCLAW_HOME", "~/.openclaw/erpclaw")), "lib"))

from erpclaw_lib.seam import (  # noqa: E402
    CheckConstraint, Column, ForeignKey, Index, Integer, MetaData, Table, Text,
    now_default, provision, reference_table, text,
)

DEFAULT_DB_PATH = os.path.join(os.path.expanduser(os.environ.get("ERPCLAW_HOME", "~/.openclaw/erpclaw")), "data.sqlite")
DISPLAY_NAME = "LegalClaw"

REQUIRED_FOUNDATION = [
    "company", "customer", "naming_series", "audit_log",
]

METADATA = MetaData()

# Foundation tables this module points at but does not own. Declared for foreign
# key resolution only and never created here -- see `seam.reference_table`.
reference_table("company", METADATA)
reference_table("customer", METADATA)
reference_table("account", METADATA)


# ==========================================================
# DOMAIN 1: MATTER MANAGEMENT (3 tables)
# ==========================================================

CLIENT_EXT = Table(
    "legalclaw_client_ext", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("naming_series", Text, server_default=text("'LCLI-'")),
    Column("customer_id", Text, ForeignKey("customer.id"), nullable=False),
    Column("client_type", Text, server_default=text("'individual'")),
    Column("billing_rate", Text),
    Column("is_active", Integer, server_default=text("1")),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint(
        "client_type IN ('individual','business','government','nonprofit')",
        name="ck_legalclaw_client_ext_client_type"),
)

Index("idx_legalclaw_client_ext_company", CLIENT_EXT.c.company_id)
Index("idx_legalclaw_client_ext_customer", CLIENT_EXT.c.customer_id)

MATTER = Table(
    "legalclaw_matter", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("naming_series", Text),
    Column("client_id", Text, ForeignKey("legalclaw_client_ext.id"),
           nullable=False),
    Column("matter_number", Text),
    Column("title", Text, nullable=False),
    Column("practice_area", Text, nullable=False, server_default=text("'general'")),
    Column("description", Text),
    Column("lead_attorney", Text),
    Column("billing_method", Text, nullable=False, server_default=text("'hourly'")),
    Column("billing_rate", Text, server_default=text("'0'")),
    Column("budget", Text, server_default=text("'0'")),
    Column("billed_amount", Text, nullable=False, server_default=text("'0'")),
    Column("collected_amount", Text, nullable=False, server_default=text("'0'")),
    Column("trust_balance", Text, nullable=False, server_default=text("'0'")),
    Column("opened_date", Text, nullable=False, server_default=text("CURRENT_DATE")),
    Column("closed_date", Text),
    Column("status", Text, nullable=False, server_default=text("'active'")),
    Column("notes", Text),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    # The wrapped IN-list carries a space at each wrap point; the CHECK body is
    # compared character for character after whitespace collapse, so the spaces
    # after 'real_estate', and 'estate', are load-bearing.
    CheckConstraint(
        "practice_area IN ('general','corporate','litigation','real_estate',"
        " 'family','criminal','ip','employment','tax','estate',"
        " 'bankruptcy','immigration','other')",
        name="ck_legalclaw_matter_practice_area"),
    CheckConstraint(
        "billing_method IN ('hourly','flat_fee','contingency','retainer','pro_bono')",
        name="ck_legalclaw_matter_billing_method"),
    CheckConstraint(
        "status IN ('active','pending','on_hold','closed','archived')",
        name="ck_legalclaw_matter_status"),
)

Index("idx_legalclaw_matter_company", MATTER.c.company_id)
Index("idx_legalclaw_matter_client", MATTER.c.client_id)
Index("idx_legalclaw_matter_status", MATTER.c.status)

MATTER_PARTY = Table(
    "legalclaw_matter_party", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("matter_id", Text,
           ForeignKey("legalclaw_matter.id", ondelete="CASCADE"), nullable=False),
    Column("party_name", Text, nullable=False),
    Column("party_type", Text, nullable=False, server_default=text("'party'")),
    Column("role", Text),
    Column("contact_info", Text),
    Column("notes", Text),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint(
        "party_type IN ('plaintiff','defendant','witness','expert',"
        " 'opposing_counsel','judge','mediator','party','other')",
        name="ck_legalclaw_matter_party_party_type"),
)

Index("idx_legalclaw_matter_party_matter", MATTER_PARTY.c.matter_id)
Index("idx_legalclaw_matter_party_name", MATTER_PARTY.c.party_name)


# ==========================================================
# DOMAIN 2: TIME & BILLING (3 tables)
# ==========================================================

TIME_ENTRY = Table(
    "legalclaw_time_entry", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("matter_id", Text, ForeignKey("legalclaw_matter.id"), nullable=False),
    Column("attorney", Text, nullable=False),
    Column("entry_date", Text, nullable=False, server_default=text("CURRENT_DATE")),
    Column("hours", Text, nullable=False, server_default=text("'0'")),
    Column("rate", Text, nullable=False, server_default=text("'0'")),
    Column("amount", Text, nullable=False, server_default=text("'0'")),
    Column("description", Text, nullable=False),
    Column("utbms_code", Text),
    Column("is_billable", Integer, nullable=False, server_default=text("1")),
    Column("is_billed", Integer, nullable=False, server_default=text("0")),
    Column("invoice_id", Text),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, server_default=text("CURRENT_TIMESTAMP")),
)

Index("idx_legalclaw_time_entry_matter", TIME_ENTRY.c.matter_id)
Index("idx_legalclaw_time_entry_attorney", TIME_ENTRY.c.attorney)
Index("idx_legalclaw_time_entry_billed", TIME_ENTRY.c.is_billed)

EXPENSE = Table(
    "legalclaw_expense", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("matter_id", Text, ForeignKey("legalclaw_matter.id"), nullable=False),
    Column("expense_date", Text, nullable=False, server_default=text("CURRENT_DATE")),
    Column("amount", Text, nullable=False, server_default=text("'0'")),
    Column("category", Text, nullable=False, server_default=text("'filing'")),
    Column("description", Text),
    Column("is_billable", Integer, nullable=False, server_default=text("1")),
    Column("is_billed", Integer, nullable=False, server_default=text("0")),
    Column("invoice_id", Text),
    Column("receipt_reference", Text),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint(
        "category IN ('filing','courier','copying','expert','travel',"
        " 'postage','research','deposition','mediation','other')",
        name="ck_legalclaw_expense_category"),
)

Index("idx_legalclaw_expense_matter", EXPENSE.c.matter_id)
Index("idx_legalclaw_expense_billed", EXPENSE.c.is_billed)

INVOICE = Table(
    "legalclaw_invoice", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("naming_series", Text),
    Column("matter_id", Text, ForeignKey("legalclaw_matter.id"), nullable=False),
    Column("client_id", Text, ForeignKey("legalclaw_client_ext.id"),
           nullable=False),
    Column("invoice_date", Text, nullable=False, server_default=text("CURRENT_DATE")),
    Column("due_date", Text),
    Column("time_amount", Text, nullable=False, server_default=text("'0'")),
    Column("expense_amount", Text, nullable=False, server_default=text("'0'")),
    Column("total_amount", Text, nullable=False, server_default=text("'0'")),
    Column("paid_amount", Text, nullable=False, server_default=text("'0'")),
    Column("balance", Text, nullable=False, server_default=text("'0'")),
    Column("format", Text, nullable=False, server_default=text("'standard'")),
    Column("status", Text, nullable=False, server_default=text("'draft'")),
    Column("sales_invoice_id", Text),
    Column("notes", Text),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint("format IN ('standard','ledes')",
                    name="ck_legalclaw_invoice_format"),
    CheckConstraint(
        "status IN ('draft','sent','paid','partially_paid','overdue','written_off')",
        name="ck_legalclaw_invoice_status"),
)

Index("idx_legalclaw_invoice_matter", INVOICE.c.matter_id)
Index("idx_legalclaw_invoice_client", INVOICE.c.client_id)
Index("idx_legalclaw_invoice_status", INVOICE.c.status)


# ==========================================================
# DOMAIN 3: TRUST ACCOUNTING (2 tables)
# ==========================================================

TRUST_ACCOUNT = Table(
    "legalclaw_trust_account", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("naming_series", Text),
    Column("name", Text, nullable=False),
    Column("bank_name", Text),
    Column("account_number", Text),
    Column("account_type", Text, nullable=False, server_default=text("'iolta'")),
    # Client money held in trust. TEXT (Decimal) on every backend.
    Column("current_balance", Text, nullable=False, server_default=text("'0'")),
    Column("gl_account_id", Text, ForeignKey("account.id")),
    Column("trust_liability_account_id", Text, ForeignKey("account.id")),
    Column("interest_income_account_id", Text, ForeignKey("account.id")),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint("account_type IN ('iolta','escrow','retainer','other')",
                    name="ck_legalclaw_trust_account_account_type"),
)

Index("idx_legalclaw_trust_account_company", TRUST_ACCOUNT.c.company_id)

TRUST_TRANSACTION = Table(
    "legalclaw_trust_transaction", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("trust_account_id", Text,
           ForeignKey("legalclaw_trust_account.id"), nullable=False),
    Column("matter_id", Text, ForeignKey("legalclaw_matter.id")),
    Column("transaction_type", Text, nullable=False,
           server_default=text("'deposit'")),
    Column("transaction_date", Text, nullable=False,
           server_default=text("CURRENT_DATE")),
    # Movement of client money. TEXT (Decimal) on every backend.
    Column("amount", Text, nullable=False, server_default=text("'0'")),
    Column("reference", Text),
    Column("payee", Text),
    Column("description", Text),
    Column("gl_entry_ids", Text),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint(
        "transaction_type IN ('deposit','disbursement','transfer','interest','fee')",
        name="ck_legalclaw_trust_transaction_transaction_type"),
)

Index("idx_legalclaw_trust_txn_account", TRUST_TRANSACTION.c.trust_account_id)
Index("idx_legalclaw_trust_txn_matter", TRUST_TRANSACTION.c.matter_id)


# ==========================================================
# DOMAIN 4: DOCUMENT MANAGEMENT (1 table)
# ==========================================================

DOCUMENT = Table(
    "legalclaw_document", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("naming_series", Text),
    Column("matter_id", Text, ForeignKey("legalclaw_matter.id")),
    Column("title", Text, nullable=False),
    Column("document_type", Text, nullable=False, server_default=text("'general'")),
    Column("file_name", Text),
    Column("content", Text),
    Column("version", Text, nullable=False, server_default=text("'1'")),
    Column("status", Text, nullable=False, server_default=text("'draft'")),
    Column("filed_date", Text),
    Column("court_reference", Text),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint(
        "document_type IN ('pleading','motion','brief','contract',"
        " 'correspondence','discovery','evidence','order','general','other')",
        name="ck_legalclaw_document_document_type"),
    CheckConstraint("status IN ('draft','review','final','filed','archived')",
                    name="ck_legalclaw_document_status"),
)

Index("idx_legalclaw_document_matter", DOCUMENT.c.matter_id)
Index("idx_legalclaw_document_type", DOCUMENT.c.document_type)
Index("idx_legalclaw_document_status", DOCUMENT.c.status)


# ==========================================================
# DOMAIN 5: CALENDAR & DEADLINES (2 tables)
# ==========================================================

CALENDAR_EVENT = Table(
    "legalclaw_calendar_event", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("matter_id", Text, ForeignKey("legalclaw_matter.id")),
    Column("title", Text, nullable=False),
    Column("event_type", Text, nullable=False, server_default=text("'hearing'")),
    Column("event_date", Text, nullable=False),
    Column("event_time", Text),
    Column("location", Text),
    Column("description", Text),
    Column("reminder_days", Integer, server_default=text("7")),
    Column("is_critical", Integer, nullable=False, server_default=text("0")),
    Column("status", Text, nullable=False, server_default=text("'scheduled'")),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint(
        "event_type IN ('hearing','deposition','filing_deadline',"
        " 'statute_of_limitations','trial','mediation','meeting','other')",
        name="ck_legalclaw_calendar_event_event_type"),
    CheckConstraint(
        "status IN ('scheduled','completed','cancelled','postponed')",
        name="ck_legalclaw_calendar_event_status"),
)

Index("idx_legalclaw_event_matter", CALENDAR_EVENT.c.matter_id)
Index("idx_legalclaw_event_date", CALENDAR_EVENT.c.event_date)

DEADLINE = Table(
    "legalclaw_deadline", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("matter_id", Text, ForeignKey("legalclaw_matter.id"), nullable=False),
    Column("title", Text, nullable=False),
    Column("deadline_type", Text, nullable=False, server_default=text("'filing'")),
    Column("due_date", Text, nullable=False),
    Column("is_court_imposed", Integer, nullable=False, server_default=text("0")),
    Column("assigned_to", Text),
    Column("is_completed", Integer, nullable=False, server_default=text("0")),
    Column("completed_date", Text),
    Column("notes", Text),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint(
        "deadline_type IN ('filing','response','discovery','statute','appeal','other')",
        name="ck_legalclaw_deadline_deadline_type"),
)

Index("idx_legalclaw_deadline_matter", DEADLINE.c.matter_id)
Index("idx_legalclaw_deadline_due", DEADLINE.c.due_date)
Index("idx_legalclaw_deadline_completed", DEADLINE.c.is_completed)


# ==========================================================
# DOMAIN 6: CONFLICT CHECKING (2 tables)
# ==========================================================

CONFLICT_CHECK = Table(
    "legalclaw_conflict_check", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("search_name", Text, nullable=False),
    Column("checked_date", Text, nullable=False, server_default=text("CURRENT_DATE")),
    Column("checked_by", Text),
    Column("matches_found", Integer, nullable=False, server_default=text("0")),
    Column("match_details", Text),
    Column("result", Text, nullable=False, server_default=text("'clear'")),
    Column("matter_id", Text, ForeignKey("legalclaw_matter.id")),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint("result IN ('clear','conflict','potential','waived')",
                    name="ck_legalclaw_conflict_check_result"),
)

Index("idx_legalclaw_conflict_search", CONFLICT_CHECK.c.search_name)

CONFLICT_WAIVER = Table(
    "legalclaw_conflict_waiver", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("conflict_check_id", Text,
           ForeignKey("legalclaw_conflict_check.id"), nullable=False),
    Column("matter_id", Text, ForeignKey("legalclaw_matter.id")),
    Column("waived_by", Text, nullable=False),
    Column("waiver_date", Text, nullable=False, server_default=text("CURRENT_DATE")),
    Column("reason", Text),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
)

Index("idx_legalclaw_waiver_check", CONFLICT_WAIVER.c.conflict_check_id)


# ==========================================================
# DOMAIN 7: COMPLIANCE (2 tables)
# ==========================================================

BAR_ADMISSION = Table(
    "legalclaw_bar_admission", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("attorney_name", Text, nullable=False),
    Column("bar_number", Text),
    Column("jurisdiction", Text, nullable=False),
    Column("admission_date", Text),
    Column("expiry_date", Text),
    Column("status", Text, nullable=False, server_default=text("'active'")),
    Column("cle_hours_required", Text, server_default=text("'0'")),
    Column("cle_hours_completed", Text, server_default=text("'0'")),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint("status IN ('active','inactive','suspended','retired')",
                    name="ck_legalclaw_bar_admission_status"),
)

Index("idx_legalclaw_bar_attorney", BAR_ADMISSION.c.attorney_name)
Index("idx_legalclaw_bar_jurisdiction", BAR_ADMISSION.c.jurisdiction)

CLE_RECORD = Table(
    "legalclaw_cle_record", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("attorney_name", Text, nullable=False),
    Column("bar_admission_id", Text, ForeignKey("legalclaw_bar_admission.id")),
    Column("course_name", Text, nullable=False),
    Column("provider", Text),
    Column("completion_date", Text, nullable=False),
    Column("hours", Text, nullable=False, server_default=text("'0'")),
    Column("category", Text, server_default=text("'general'")),
    Column("certificate_number", Text),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint(
        "category IN ('general','ethics','professionalism','diversity',"
        " 'substance_abuse','other')",
        name="ck_legalclaw_cle_record_category"),
)

Index("idx_legalclaw_cle_attorney", CLE_RECORD.c.attorney_name)
Index("idx_legalclaw_cle_bar", CLE_RECORD.c.bar_admission_id)


# ==========================================================
# DOMAIN 8: CLIENT INTAKE (1 table)
# ==========================================================

INTAKE = Table(
    "legalclaw_intake", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("contact_name", Text, nullable=False),
    Column("contact_email", Text),
    Column("contact_phone", Text),
    Column("inquiry_type", Text),
    Column("practice_area", Text),
    Column("description", Text),
    Column("urgency", Text, server_default=text("'normal'")),
    Column("source", Text),
    Column("conflict_checked", Integer, server_default=text("0")),
    Column("conflict_result", Text),
    Column("assigned_to", Text),
    Column("converted_matter_id", Text),
    Column("status", Text, server_default=text("'new'")),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=now_default()),
    Column("updated_at", Text, server_default=now_default()),
    CheckConstraint("urgency IN ('low','normal','high','urgent')",
                    name="ck_legalclaw_intake_urgency"),
    CheckConstraint(
        "status IN ('new','contacted','qualified','converted','declined','lost')",
        name="ck_legalclaw_intake_status"),
)

Index("idx_legalclaw_intake_company", INTAKE.c.company_id)
Index("idx_legalclaw_intake_status", INTAKE.c.status)
Index("idx_legalclaw_intake_name", INTAKE.c.contact_name)


# ==========================================================
# DOMAIN 9: TASK TEMPLATES (2 tables)
# ==========================================================

TASK_TEMPLATE = Table(
    "legalclaw_task_template", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("name", Text, nullable=False),
    Column("practice_area", Text),
    Column("description", Text),
    Column("task_count", Integer, server_default=text("0")),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=now_default()),
)

Index("idx_legalclaw_tmpl_company", TASK_TEMPLATE.c.company_id)

# The only table in the module with no company_id -- transcribed as shipped.
TASK_TEMPLATE_ITEM = Table(
    "legalclaw_task_template_item", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("template_id", Text,
           ForeignKey("legalclaw_task_template.id"), nullable=False),
    Column("task_name", Text, nullable=False),
    Column("description", Text),
    Column("due_days_offset", Integer, server_default=text("0")),
    Column("assigned_role", Text),
    Column("predecessor_item_id", Text),
    Column("is_required", Integer, server_default=text("1")),
    Column("sort_order", Integer, server_default=text("0")),
    Column("created_at", Text, server_default=now_default()),
)

Index("idx_legalclaw_tmpl_item_tmpl", TASK_TEMPLATE_ITEM.c.template_id)


# ==========================================================
# DOMAIN 10: SETTLEMENT / CONTINGENCY FEE (1 table)
# ==========================================================

SETTLEMENT = Table(
    "legalclaw_settlement", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("matter_id", Text, ForeignKey("legalclaw_matter.id"), nullable=False),
    Column("settlement_date", Text, nullable=False),
    # Settlement proceeds and their split. TEXT (Decimal) on every backend.
    Column("gross_amount", Text, nullable=False, server_default=text("'0'")),
    Column("contingency_pct", Text, nullable=False, server_default=text("'0'")),
    Column("attorney_fee", Text, server_default=text("'0'")),
    Column("costs_advanced", Text, server_default=text("'0'")),
    Column("net_to_client", Text, server_default=text("'0'")),
    Column("payment_method", Text),
    Column("notes", Text),
    Column("status", Text, server_default=text("'pending'")),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=now_default()),
    CheckConstraint("status IN ('pending','disbursed','completed')",
                    name="ck_legalclaw_settlement_status"),
)

Index("idx_legalclaw_settlement_matter", SETTLEMENT.c.matter_id)
Index("idx_legalclaw_settlement_company", SETTLEMENT.c.company_id)
Index("idx_legalclaw_settlement_status", SETTLEMENT.c.status)


# ==========================================================
# DOMAIN 11: COMMUNICATION LOG (1 table)
# ==========================================================

COMMUNICATION = Table(
    "legalclaw_communication", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("matter_id", Text, ForeignKey("legalclaw_matter.id"), nullable=False),
    # Bare TEXT as shipped -- the module's other client_id columns are foreign
    # keys into legalclaw_client_ext; this one is not.
    Column("client_id", Text),
    Column("comm_type", Text, nullable=False),
    Column("direction", Text),
    Column("subject", Text),
    Column("summary", Text),
    Column("duration_minutes", Integer),
    Column("participants", Text),
    Column("date", Text, nullable=False),
    Column("logged_by", Text),
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=now_default()),
    CheckConstraint(
        "comm_type IN ('email','phone','meeting','letter','text','portal')",
        name="ck_legalclaw_communication_comm_type"),
    CheckConstraint("direction IN ('inbound','outbound')",
                    name="ck_legalclaw_communication_direction"),
)

Index("idx_legalclaw_comm_matter", COMMUNICATION.c.matter_id)
Index("idx_legalclaw_comm_type", COMMUNICATION.c.comm_type)
Index("idx_legalclaw_comm_date", COMMUNICATION.c.date)


def _require_foundation(db_path):
    """The pre-conversion installer's foundation probe, asked through the seam.

    The original read ``sqlite_master`` directly, so the guard that exists to
    produce a friendly error was itself SQLite-only. ``seam.table_exists``
    answers on both backends (ADR-0034 bulk-39). The wording is this module's
    own, unchanged.
    """
    from erpclaw_lib import seam

    missing = [t for t in REQUIRED_FOUNDATION if not seam.table_exists(t, db_path)]
    if missing:
        print(f"ERROR: Foundation tables missing: {', '.join(missing)}")
        print("Run erpclaw-setup first: clawhub install erpclaw-setup")
        sys.exit(1)


def init_legalclaw_schema(db_path=None):
    """Create LegalClaw tables and indexes on whichever backend is configured.

    Same contract as before the ADR-0034 conversion: idempotent, and the returned
    counts are what was ACTUALLY created rather than what was declared.
    """
    db_path = db_path or DEFAULT_DB_PATH
    _require_foundation(db_path)
    result = provision(METADATA, db_path)
    return {
        "database": db_path,
        "tables": result["tables"],
        "indexes": result["indexes"],
    }


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH
    result = init_legalclaw_schema(path)
    print(f"{DISPLAY_NAME}: Schema initialized ({result['database']})")
    print(f"  Tables: {result['tables']}")
    print(f"  Indexes: {result['indexes']}")
