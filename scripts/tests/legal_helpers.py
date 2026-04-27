"""Shared helper functions for LegalClaw L1 unit tests.

Provides:
  - DB bootstrap via init_schema.init_db() + init_legalclaw_schema()
  - load_db_query() for explicit module loading (avoids sys.path collisions)
  - call_action() / ns() / is_error() / is_ok()
  - Seed functions for company, customer, client_ext, matter, naming_series
  - build_env() for a complete legal test environment
"""
import argparse
import importlib.util
import io
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)          # legalclaw/scripts/
MODULE_DIR = os.path.dirname(SCRIPTS_DIR)          # legalclaw/
INIT_DB_PATH = os.path.join(MODULE_DIR, "init_db.py")

# Foundation init_schema.py (erpclaw-setup)
SRC_DIR = os.path.dirname(MODULE_DIR)              # source/
ERPCLAW_DIR = os.path.join(SRC_DIR, "erpclaw", "scripts", "erpclaw-setup")
INIT_SCHEMA_PATH = os.path.join(ERPCLAW_DIR, "init_schema.py")

# Make erpclaw_lib importable
ERPCLAW_LIB = os.path.expanduser("~/.openclaw/erpclaw/lib")
if ERPCLAW_LIB not in sys.path:
    sys.path.insert(0, ERPCLAW_LIB)

from erpclaw_lib.db import setup_pragmas

# Make scripts dir importable so domain modules resolve
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def load_db_query():
    """Load legalclaw db_query.py explicitly to avoid sys.path collisions."""
    db_query_path = os.path.join(SCRIPTS_DIR, "db_query.py")
    spec = importlib.util.spec_from_file_location("db_query_legal", db_query_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def init_all_tables(db_path: str):
    """Create foundation tables + legalclaw extension tables.

    1. Runs erpclaw-setup init_schema.init_db()  (core tables)
    2. Runs legalclaw init_db.init_legalclaw_schema()
    """
    # Step 1: Foundation schema
    spec = importlib.util.spec_from_file_location("init_schema", INIT_SCHEMA_PATH)
    schema_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(schema_mod)
    schema_mod.init_db(db_path)

    # Step 2: Legalclaw extension tables
    spec2 = importlib.util.spec_from_file_location("legal_init_db", INIT_DB_PATH)
    legal_mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(legal_mod)
    legal_mod.init_legalclaw_schema(db_path)


class _ConnWrapper:
    """Thin wrapper so conn.company_id works (some actions set it)."""
    def __init__(self, real_conn):
        self._conn = real_conn
        self.company_id = None

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, *a, **kw):
        return self._conn.execute(*a, **kw)

    def executemany(self, *a, **kw):
        return self._conn.executemany(*a, **kw)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._conn.row_factory = value


class _DecimalSum:
    """Custom SQLite aggregate: SUM using Python Decimal for precision."""
    def __init__(self):
        self.total = Decimal("0")

    def step(self, value):
        if value is not None:
            self.total += Decimal(str(value))

    def finalize(self):
        return str(self.total)


def get_conn(db_path: str):
    """Return a wrapped sqlite3.Connection with FK enabled and Row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    setup_pragmas(conn)
    conn.create_aggregate("decimal_sum", 1, _DecimalSum)
    return _ConnWrapper(conn)


# ---------------------------------------------------------------------------
# Action invocation helpers
# ---------------------------------------------------------------------------

def call_action(fn, conn, args) -> dict:
    """Invoke a domain function, capture stdout JSON, return parsed dict."""
    buf = io.StringIO()

    def _fake_exit(code=0):
        raise SystemExit(code)

    try:
        with patch("sys.stdout", buf), patch("sys.exit", side_effect=_fake_exit):
            fn(conn, args)
    except SystemExit:
        pass

    output = buf.getvalue().strip()
    if not output:
        return {"status": "error", "message": "no output captured"}
    return json.loads(output)


def ns(**kwargs) -> argparse.Namespace:
    """Build an argparse.Namespace from keyword args (mimics CLI flags)."""
    defaults = {
        "limit": 50,
        "offset": 0,
        "db_path": None,
        "company_id": None,
        "search": None,
        "notes": None,
        # Matters domain
        "client_id": None,
        "name": None,
        "client_type": None,
        "email": None,
        "phone": None,
        "address": None,
        "tax_id": None,
        "billing_rate": None,
        "is_active": None,
        "matter_id": None,
        "title": None,
        "practice_area": None,
        "description": None,
        "lead_attorney": None,
        "billing_method": None,
        "budget": None,
        "opened_date": None,
        "closed_date": None,
        "matter_status": None,
        "party_name": None,
        "party_type": None,
        "role": None,
        "contact_info": None,
        # Timebilling domain
        "attorney": None,
        "entry_date": None,
        "hours": None,
        "rate": None,
        "te_description": None,
        "utbms_code": None,
        "is_billable": None,
        "is_billed": None,
        "time_entry_id": None,
        "expense_id": None,
        "expense_date": None,
        "expense_amount": None,
        "amount": None,
        "category": None,
        "expense_description": None,
        "receipt_reference": None,
        "invoice_id": None,
        "invoice_date": None,
        "due_date": None,
        "invoice_format": None,
        "invoice_status": None,
        "payment_amount": None,
        # Trust domain
        "trust_account_id": None,
        "trust_name": None,
        "bank_name": None,
        "account_number": None,
        "account_type": None,
        "gl_account_id": None,
        "trust_liability_account_id": None,
        "interest_income_account_id": None,
        "cost_center_id": None,
        "to_trust_account_id": None,
        "transaction_date": None,
        "transaction_type": None,
        "reference": None,
        "payee": None,
        "trust_description": None,
        # Documents domain
        "document_id": None,
        "doc_title": None,
        "document_type": None,
        "file_name": None,
        "content": None,
        "document_status": None,
        "filed_date": None,
        "court_reference": None,
        # Calendar domain
        "event_id": None,
        "event_title": None,
        "event_type": None,
        "event_date": None,
        "event_time": None,
        "location": None,
        "event_description": None,
        "reminder_days": None,
        "is_critical": None,
        "event_status": None,
        "deadline_id": None,
        "deadline_title": None,
        "deadline_type": None,
        "is_court_imposed": None,
        "assigned_to": None,
        "is_completed": None,
        # Conflicts domain
        "search_name": None,
        "checked_by": None,
        "conflict_check_id": None,
        "waived_by": None,
        "waiver_reason": None,
        "conflict_result": None,
        # Compliance domain
        "bar_admission_id": None,
        "attorney_name": None,
        "bar_number": None,
        "jurisdiction": None,
        "admission_date": None,
        "expiry_date": None,
        "admission_status": None,
        "cle_hours_required": None,
        "course_name": None,
        "cle_provider": None,
        "completion_date": None,
        "cle_hours": None,
        "cle_category": None,
        "certificate_number": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def is_error(result: dict) -> bool:
    return result.get("status") == "error"


def is_ok(result: dict) -> bool:
    return result.get("status") == "ok"


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def seed_company(conn, name="Legal Firm LLC", abbr="LF") -> str:
    """Insert a test company and return its ID."""
    cid = _uuid()
    conn.execute(
        """INSERT INTO company (id, name, abbr, default_currency, country,
           fiscal_year_start_month)
           VALUES (?, ?, ?, 'USD', 'United States', 1)""",
        (cid, f"{name} {cid[:6]}", f"{abbr}{cid[:4]}")
    )
    conn.commit()
    return cid


def seed_customer(conn, company_id: str, name="Jane Client",
                  email=None, phone=None) -> str:
    """Insert a core customer and return its ID."""
    cid = _uuid()
    conn.execute(
        """INSERT INTO customer (id, name, company_id, customer_type, status)
           VALUES (?, ?, ?, 'individual', 'active')""",
        (cid, name, company_id)
    )
    conn.commit()
    return cid


def seed_client_ext(conn, customer_id: str, company_id: str,
                    client_type="individual", billing_rate=None) -> str:
    """Insert a legalclaw_client_ext row and return its ID."""
    ext_id = _uuid()
    now = _now()
    conn.execute(
        """INSERT INTO legalclaw_client_ext (
               id, naming_series, customer_id, client_type, billing_rate,
               is_active, company_id, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
        (ext_id, f"LCLI-{ext_id[:6]}", customer_id, client_type,
         billing_rate, company_id, now, now)
    )
    conn.commit()
    return ext_id


def seed_matter(conn, client_id: str, company_id: str,
                title="Test Matter", practice_area="general",
                billing_method="hourly", billing_rate="250.00",
                budget="10000.00") -> str:
    """Insert a legalclaw_matter and return its ID."""
    mid = _uuid()
    now = _now()
    conn.execute(
        """INSERT INTO legalclaw_matter (
               id, naming_series, client_id, matter_number, title,
               practice_area, billing_method, billing_rate, budget,
               billed_amount, collected_amount, trust_balance,
               opened_date, status, company_id, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '0', '0', '0',
                     '2026-01-15', 'active', ?, ?, ?)""",
        (mid, f"LMTR-{mid[:6]}", client_id, f"LMTR-{mid[:6]}", title,
         practice_area, billing_method, billing_rate, budget,
         company_id, now, now)
    )
    conn.commit()
    return mid


def seed_naming_series(conn, company_id: str):
    """Seed naming series for legalclaw entity types."""
    series = [
        ("legalclaw_client_ext", "LCLI-", 0),
        ("legalclaw_matter", "LMTR-", 0),
        ("legalclaw_invoice", "LINV-", 0),
        ("legalclaw_trust_account", "LTRS-", 0),
        ("legalclaw_document", "LDOC-", 0),
        ("legalclaw_bar_admission", "LBAR-", 0),
    ]
    for entity_type, prefix, current in series:
        conn.execute(
            """INSERT OR IGNORE INTO naming_series
               (id, entity_type, prefix, current_value, company_id)
               VALUES (?, ?, ?, ?, ?)""",
            (_uuid(), entity_type, prefix, current, company_id)
        )
    conn.commit()


def seed_account(conn, company_id: str, name="Test Account",
                 root_type="asset", account_type=None,
                 account_number=None) -> str:
    """Insert a GL account and return its ID."""
    aid = _uuid()
    direction = "debit_normal" if root_type in ("asset", "expense") else "credit_normal"
    conn.execute(
        """INSERT INTO account (id, name, account_number, root_type, account_type,
           balance_direction, company_id, depth)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
        (aid, name, account_number or f"ACC-{aid[:6]}", root_type,
         account_type, direction, company_id)
    )
    conn.commit()
    return aid


def seed_fiscal_year(conn, company_id: str,
                     start="2026-01-01", end="2026-12-31") -> str:
    """Insert a fiscal year and return its ID."""
    fid = _uuid()
    conn.execute(
        """INSERT INTO fiscal_year (id, name, start_date, end_date, company_id)
           VALUES (?, ?, ?, ?, ?)""",
        (fid, f"FY-{fid[:6]}", start, end, company_id)
    )
    conn.commit()
    return fid


def seed_cost_center(conn, company_id: str, name="Main CC") -> str:
    """Insert a cost center and return its ID."""
    ccid = _uuid()
    conn.execute(
        """INSERT INTO cost_center (id, name, company_id, is_group)
           VALUES (?, ?, ?, 0)""",
        (ccid, name, company_id)
    )
    conn.commit()
    return ccid


def seed_time_entry(conn, matter_id: str, company_id: str,
                    attorney="J. Smith", hours="2.0", rate="250.00",
                    description="Legal research") -> str:
    """Insert a legalclaw_time_entry and return its ID."""
    te_id = _uuid()
    now = _now()
    amount = str(Decimal(hours) * Decimal(rate))
    conn.execute(
        """INSERT INTO legalclaw_time_entry (
               id, matter_id, attorney, entry_date, hours, rate, amount,
               description, is_billable, is_billed, company_id,
               created_at, updated_at
           ) VALUES (?, ?, ?, '2026-01-20', ?, ?, ?, ?, 1, 0, ?, ?, ?)""",
        (te_id, matter_id, attorney, hours, rate, amount,
         description, company_id, now, now)
    )
    conn.commit()
    return te_id


def seed_expense(conn, matter_id: str, company_id: str,
                 amount="150.00", category="filing",
                 description="Court filing fee") -> str:
    """Insert a legalclaw_expense and return its ID."""
    exp_id = _uuid()
    now = _now()
    conn.execute(
        """INSERT INTO legalclaw_expense (
               id, matter_id, expense_date, amount, category,
               description, is_billable, is_billed, company_id, created_at
           ) VALUES (?, ?, '2026-01-20', ?, ?, ?, 1, 0, ?, ?)""",
        (exp_id, matter_id, amount, category, description,
         company_id, now)
    )
    conn.commit()
    return exp_id


def seed_trust_account(conn, company_id: str, name="Client Trust IOLTA",
                       account_type="iolta",
                       gl_account_id=None, trust_liability_account_id=None,
                       interest_income_account_id=None) -> str:
    """Insert a legalclaw_trust_account and return its ID."""
    ta_id = _uuid()
    now = _now()
    conn.execute(
        """INSERT INTO legalclaw_trust_account (
               id, naming_series, name, account_type, current_balance,
               gl_account_id, trust_liability_account_id, interest_income_account_id,
               company_id, created_at, updated_at
           ) VALUES (?, ?, ?, ?, '0', ?, ?, ?, ?, ?, ?)""",
        (ta_id, f"LTRS-{ta_id[:6]}", name, account_type,
         gl_account_id, trust_liability_account_id, interest_income_account_id,
         company_id, now, now)
    )
    conn.commit()
    return ta_id


def seed_document(conn, matter_id: str, company_id: str,
                  title="Draft Complaint", document_type="pleading") -> str:
    """Insert a legalclaw_document and return its ID."""
    doc_id = _uuid()
    now = _now()
    conn.execute(
        """INSERT INTO legalclaw_document (
               id, naming_series, matter_id, title, document_type,
               version, status, company_id, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, '1', 'draft', ?, ?, ?)""",
        (doc_id, f"LDOC-{doc_id[:6]}", matter_id, title, document_type,
         company_id, now, now)
    )
    conn.commit()
    return doc_id


def build_env(conn) -> dict:
    """Create a complete legal test environment.

    Returns dict with all IDs needed for tests.
    """
    cid = seed_company(conn)
    seed_naming_series(conn, cid)
    fyid = seed_fiscal_year(conn, cid)
    ccid = seed_cost_center(conn, cid)

    # GL accounts for trust
    trust_bank = seed_account(conn, cid, "Trust Bank", "asset", "bank", "1300")
    trust_liability = seed_account(conn, cid, "Trust Liability", "liability",
                                   "equity", "2100")
    interest_income = seed_account(conn, cid, "Interest Income", "income",
                                   "revenue", "4100")

    # Customer (core + ext)
    core_cust = seed_customer(conn, cid, "Jane Client", "jane@test.com", "555-0200")
    client_ext = seed_client_ext(conn, core_cust, cid, billing_rate="300.00")

    # Matter
    matter = seed_matter(conn, client_ext, cid, title="Smith v. Jones",
                         practice_area="litigation", billing_rate="300.00")

    # Trust account (without GL linkage for L1 tests -- trust voucher_types
    # like "Trust Deposit" are not in foundation gl_entry CHECK constraint;
    # GL-linked trust testing requires registering custom voucher types)
    trust_acct = seed_trust_account(conn, cid)

    return {
        "company_id": cid,
        "fiscal_year_id": fyid,
        "cost_center_id": ccid,
        "trust_bank_acct": trust_bank,
        "trust_liability_acct": trust_liability,
        "interest_income_acct": interest_income,
        "core_customer_id": core_cust,
        "client_ext_id": client_ext,
        "matter_id": matter,
        "trust_account_id": trust_acct,
    }
