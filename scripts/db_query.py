#!/usr/bin/env python3
"""LegalClaw -- db_query.py (unified router)

AI-native legal practice management.
Routes all actions across 7 domain modules: matters, timebilling, trust, documents, calendar, conflicts, compliance.

Usage: python3 db_query.py --action <action-name> [--flags ...]
Output: JSON to stdout, exit 0 on success, exit 1 on error.
"""
import argparse
import json
import os
import sys

# Add shared lib to path
try:
    sys.path.insert(0, os.path.expanduser("~/.openclaw/erpclaw/lib"))
    from erpclaw_lib.db import get_connection, ensure_db_exists, DEFAULT_DB_PATH
    from erpclaw_lib.validation import check_input_lengths
    from erpclaw_lib.response import ok, err
    from erpclaw_lib.dependencies import check_required_tables
    from erpclaw_lib.args import SafeArgumentParser, check_unknown_args
except ImportError:
    import json as _json
    print(_json.dumps({
        "status": "error",
        "error": "ERPClaw foundation not installed. Install erpclaw-setup first: clawhub install erpclaw-setup",
        "suggestion": "clawhub install erpclaw-setup"
    }))
    sys.exit(1)

# Add this script's directory so domain modules can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matters import ACTIONS as MATTERS_ACTIONS
from timebilling import ACTIONS as TIMEBILLING_ACTIONS
from trust import ACTIONS as TRUST_ACTIONS
from documents import ACTIONS as DOCUMENTS_ACTIONS
from calendar import ACTIONS as CALENDAR_ACTIONS
from conflicts import ACTIONS as CONFLICTS_ACTIONS
from compliance import ACTIONS as COMPLIANCE_ACTIONS
from ledes import ACTIONS as LEDES_ACTIONS

# ---------------------------------------------------------------------------
# Merge all domain actions into one router
# ---------------------------------------------------------------------------
SKILL = "legalclaw"
REQUIRED_TABLES = ["company", "customer", "legalclaw_client_ext"]

ACTIONS = {}
ACTIONS.update(MATTERS_ACTIONS)
ACTIONS.update(TIMEBILLING_ACTIONS)
ACTIONS.update(TRUST_ACTIONS)
ACTIONS.update(DOCUMENTS_ACTIONS)
ACTIONS.update(CALENDAR_ACTIONS)
ACTIONS.update(CONFLICTS_ACTIONS)
ACTIONS.update(COMPLIANCE_ACTIONS)
ACTIONS.update(LEDES_ACTIONS)


def main():
    parser = SafeArgumentParser(description="legalclaw")
    parser.add_argument("--action", required=True, choices=sorted(ACTIONS.keys()))
    parser.add_argument("--db-path", default=None)

    # -- Shared IDs --
    parser.add_argument("--company-id")

    # -- Shared --
    parser.add_argument("--search")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--notes")

    # == MATTERS domain ==
    parser.add_argument("--client-id")
    parser.add_argument("--name")
    parser.add_argument("--client-type")
    parser.add_argument("--email")
    parser.add_argument("--phone")
    parser.add_argument("--address")
    parser.add_argument("--tax-id")
    parser.add_argument("--billing-rate")
    parser.add_argument("--is-active")
    parser.add_argument("--matter-id")
    parser.add_argument("--title")
    parser.add_argument("--practice-area")
    parser.add_argument("--description")
    parser.add_argument("--lead-attorney")
    parser.add_argument("--billing-method")
    parser.add_argument("--budget")
    parser.add_argument("--opened-date")
    parser.add_argument("--closed-date")
    parser.add_argument("--matter-status")
    parser.add_argument("--party-name")
    parser.add_argument("--party-type")
    parser.add_argument("--role")
    parser.add_argument("--contact-info")

    # == TIMEBILLING domain ==
    parser.add_argument("--attorney")
    parser.add_argument("--entry-date")
    parser.add_argument("--hours")
    parser.add_argument("--rate")
    parser.add_argument("--te-description")
    parser.add_argument("--utbms-code")
    parser.add_argument("--is-billable")
    parser.add_argument("--is-billed")
    parser.add_argument("--time-entry-id")
    parser.add_argument("--expense-id")
    parser.add_argument("--expense-date")
    parser.add_argument("--expense-amount")
    parser.add_argument("--amount")
    parser.add_argument("--category")
    parser.add_argument("--expense-description")
    parser.add_argument("--receipt-reference")
    parser.add_argument("--invoice-id")
    parser.add_argument("--invoice-date")
    parser.add_argument("--due-date")
    parser.add_argument("--invoice-format")
    parser.add_argument("--invoice-status")
    parser.add_argument("--payment-amount")

    # == TRUST domain ==
    parser.add_argument("--trust-account-id")
    parser.add_argument("--trust-name")
    parser.add_argument("--bank-name")
    parser.add_argument("--account-number")
    parser.add_argument("--account-type")
    parser.add_argument("--gl-account-id")
    parser.add_argument("--trust-liability-account-id")
    parser.add_argument("--interest-income-account-id")
    parser.add_argument("--cost-center-id")
    parser.add_argument("--to-trust-account-id")
    parser.add_argument("--transaction-date")
    parser.add_argument("--transaction-type")
    parser.add_argument("--reference")
    parser.add_argument("--payee")
    parser.add_argument("--trust-description")

    # == DOCUMENTS domain ==
    parser.add_argument("--document-id")
    parser.add_argument("--doc-title")
    parser.add_argument("--document-type")
    parser.add_argument("--file-name")
    parser.add_argument("--content")
    parser.add_argument("--document-status")
    parser.add_argument("--filed-date")
    parser.add_argument("--court-reference")

    # == CALENDAR domain ==
    parser.add_argument("--event-id")
    parser.add_argument("--event-title")
    parser.add_argument("--event-type")
    parser.add_argument("--event-date")
    parser.add_argument("--event-time")
    parser.add_argument("--location")
    parser.add_argument("--event-description")
    parser.add_argument("--reminder-days")
    parser.add_argument("--is-critical")
    parser.add_argument("--event-status")
    parser.add_argument("--deadline-id")
    parser.add_argument("--deadline-title")
    parser.add_argument("--deadline-type")
    parser.add_argument("--is-court-imposed")
    parser.add_argument("--assigned-to")
    parser.add_argument("--is-completed")

    # == CONFLICTS domain ==
    parser.add_argument("--search-name")
    parser.add_argument("--checked-by")
    parser.add_argument("--conflict-check-id")
    parser.add_argument("--waived-by")
    parser.add_argument("--waiver-reason")
    parser.add_argument("--conflict-result")

    # == COMPLIANCE domain ==
    parser.add_argument("--bar-admission-id")
    parser.add_argument("--attorney-name")
    parser.add_argument("--bar-number")
    parser.add_argument("--jurisdiction")
    parser.add_argument("--admission-date")
    parser.add_argument("--expiry-date")
    parser.add_argument("--admission-status")
    parser.add_argument("--cle-hours-required")
    parser.add_argument("--course-name")
    parser.add_argument("--cle-provider")
    parser.add_argument("--completion-date")
    parser.add_argument("--cle-hours")
    parser.add_argument("--cle-category")
    parser.add_argument("--certificate-number")

    args, unknown = parser.parse_known_args()
    check_unknown_args(parser, unknown)
    check_input_lengths(args)

    db_path = args.db_path or DEFAULT_DB_PATH
    ensure_db_exists(db_path)
    conn = get_connection(db_path)

    _dep = check_required_tables(conn, REQUIRED_TABLES)
    if _dep:
        _dep["suggestion"] = "clawhub install erpclaw-setup && clawhub install legalclaw"
        print(json.dumps(_dep, indent=2))
        conn.close()
        sys.exit(1)

    try:
        ACTIONS[args.action](conn, args)
    except Exception as e:
        conn.rollback()
        sys.stderr.write(f"[{SKILL}] {e}\n")
        err(str(e))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
