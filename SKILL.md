---
name: legalclaw
version: 1.0.0
description: Legal Practice Management -- matters, time & billing, trust accounting, documents, calendar, conflicts, compliance. 69 actions across 7 domains with IOLTA trust accounting, CLE compliance tracking, conflict checking, and profitability reporting. Built on ERPClaw foundation.
author: AvanSaber / Nikhil Jathar
homepage: https://www.legalclaw.ai
source: https://github.com/avansaber/legalclaw
tier: 4
category: legal
requires: [erpclaw-setup]
database: ~/.openclaw/erpclaw/data.sqlite
user-invocable: true
tags: [legalclaw, legal, law, matter, case, client, attorney, billing, trust, iolta, escrow, invoice, time-entry, expense, document, deadline, calendar, conflict, bar, cle, compliance, profitability, litigation, corporate]
scripts:
  - scripts/db_query.py
metadata: {"openclaw":{"type":"executable","install":{"post":"python3 scripts/db_query.py --action status"},"requires":{"bins":["python3"],"env":[],"optionalEnv":["ERPCLAW_DB_PATH"]},"os":["darwin","linux"]}}
---

# legalclaw

You are a Legal Practice Administrator for LegalClaw, an AI-native legal practice management system built on ERPClaw.
You manage the full legal workflow: client intake, matter management, time tracking, expense recording,
invoice generation, IOLTA/escrow trust accounting, legal document management, court calendar and deadlines,
conflict of interest checking, bar admission tracking, CLE compliance, and practice analytics.
All financial data uses Decimal precision. Trust accounts enforce balance sufficiency checks.

## Security Model

- **Local-only**: All data stored in `~/.openclaw/erpclaw/data.sqlite`
- **No credentials required**: Uses erpclaw_lib shared library (installed by erpclaw-setup)
- **Zero network calls**: No external API calls, no telemetry, no cloud dependencies
- **SQL injection safe**: All queries use parameterized statements
- **Trust accounting safeguards**: Disbursements check sufficient balance, all transactions recorded

### Skill Activation Triggers

Activate this skill when the user mentions: law firm, attorney, lawyer, legal, matter, case,
client intake, retainer, billable hours, time entry, trust account, IOLTA, escrow, legal document,
court filing, deadline, statute of limitations, conflict check, bar admission, CLE, compliance,
deposition, hearing, trial, invoice, billing, opposing counsel.

### Setup (First Use Only)

If the database does not exist or you see "no such table" errors:
```
python3 {baseDir}/../erpclaw-setup/scripts/db_query.py --action initialize-database
python3 {baseDir}/init_db.py
python3 {baseDir}/scripts/db_query.py --action status
```

## Quick Start (Tier 1)

**1. Register a client and create a matter:**
```
--action legal-add-client --company-id {id} --name "Acme Corp" --client-type business --billing-rate "350.00"
--action legal-add-matter --company-id {id} --client-id {id} --title "Contract Dispute" --practice-area litigation --billing-method hourly --billing-rate "400.00"
```

**2. Track time and expenses:**
```
--action legal-add-time-entry --company-id {id} --matter-id {id} --attorney "Jane Smith" --hours "2.5" --te-description "Research case precedents" --rate "400.00"
--action legal-add-expense --company-id {id} --matter-id {id} --expense-amount "150.00" --category filing --expense-description "Court filing fee"
```

**3. Generate and send invoice:**
```
--action legal-generate-invoice --company-id {id} --matter-id {id}
--action legal-send-invoice --invoice-id {id}
--action legal-record-payment --invoice-id {id} --payment-amount "1150.00"
```

**4. Manage trust account:**
```
--action legal-add-trust-account --company-id {id} --trust-name "Client Trust IOLTA" --account-type iolta
--action legal-deposit-trust --company-id {id} --trust-account-id {id} --matter-id {id} --amount "5000.00"
--action legal-disburse-trust --company-id {id} --trust-account-id {id} --matter-id {id} --amount "1200.00" --payee "Expert Witness LLC"
```

## All Actions (Tier 2)

For all actions: `python3 {baseDir}/scripts/db_query.py --action <action> [flags]`

### Matters (14 actions)
| Action | Required Flags | Optional Flags |
|--------|---------------|----------------|
| `legal-add-client` | `--company-id --name` | `--client-type --email --phone --address --tax-id --billing-rate` |
| `legal-update-client` | `--client-id` | `--name --client-type --email --phone --address --tax-id --billing-rate --is-active` |
| `legal-get-client` | `--client-id` | |
| `legal-list-clients` | | `--company-id --search --is-active --limit --offset` |
| `legal-add-matter` | `--company-id --client-id --title` | `--practice-area --billing-method --billing-rate --budget --lead-attorney --description --opened-date --notes` |
| `legal-update-matter` | `--matter-id` | `--title --practice-area --billing-method --billing-rate --budget --lead-attorney --description --matter-status --notes` |
| `legal-get-matter` | `--matter-id` | |
| `legal-list-matters` | | `--company-id --client-id --matter-status --practice-area --search --limit --offset` |
| `legal-add-matter-party` | `--company-id --matter-id --party-name` | `--party-type --role --contact-info --notes` |
| `legal-list-matter-parties` | | `--matter-id --party-type --limit --offset` |
| `legal-close-matter` | `--matter-id` | `--closed-date` |
| `legal-reopen-matter` | `--matter-id` | |
| `legal-matter-summary` | `--matter-id` | |
| `legal-client-portfolio` | `--client-id` | |

### Time & Billing (14 actions)
| Action | Required Flags | Optional Flags |
|--------|---------------|----------------|
| `legal-add-time-entry` | `--company-id --matter-id --attorney --te-description` | `--hours --rate --entry-date --utbms-code --is-billable` |
| `legal-update-time-entry` | `--time-entry-id` | `--attorney --hours --rate --te-description --entry-date --utbms-code --is-billable` |
| `legal-list-time-entries` | | `--matter-id --attorney --is-billed --is-billable --limit --offset` |
| `legal-add-expense` | `--company-id --matter-id --expense-amount` | `--category --expense-date --expense-description --is-billable --receipt-reference` |
| `legal-update-expense` | `--expense-id` | `--expense-amount --category --expense-date --expense-description --is-billable --receipt-reference` |
| `legal-list-expenses` | | `--matter-id --category --is-billed --limit --offset` |
| `legal-generate-invoice` | `--company-id --matter-id` | `--invoice-date --due-date --invoice-format --notes` |
| `legal-get-invoice` | `--invoice-id` | |
| `legal-list-invoices` | | `--matter-id --client-id --invoice-status --company-id --limit --offset` |
| `legal-send-invoice` | `--invoice-id` | |
| `legal-record-payment` | `--invoice-id --payment-amount` | |
| `legal-write-off-invoice` | `--invoice-id` | |
| `legal-billable-utilization-report` | `--company-id` | |
| `legal-ar-aging-report` | `--company-id` | |

### Trust Accounting (10 actions)
| Action | Required Flags | Optional Flags |
|--------|---------------|----------------|
| `legal-add-trust-account` | `--company-id --trust-name` | `--account-type --bank-name --account-number` |
| `legal-get-trust-account` | `--trust-account-id` | |
| `legal-list-trust-accounts` | | `--company-id --account-type --limit --offset` |
| `legal-deposit-trust` | `--company-id --trust-account-id --amount` | `--matter-id --transaction-date --reference --payee --trust-description` |
| `legal-disburse-trust` | `--company-id --trust-account-id --amount --payee` | `--matter-id --transaction-date --reference --trust-description` |
| `legal-transfer-trust` | `--company-id --trust-account-id --to-trust-account-id --amount` | `--transaction-date --reference` |
| `legal-list-trust-transactions` | | `--trust-account-id --matter-id --transaction-type --limit --offset` |
| `legal-trust-reconciliation` | `--trust-account-id` | |
| `legal-trust-balance-report` | `--company-id` | |
| `legal-trust-interest-distribution` | `--company-id --trust-account-id --amount` | `--transaction-date --reference` |

### Documents (10 actions)
| Action | Required Flags | Optional Flags |
|--------|---------------|----------------|
| `legal-add-legal-document` | `--company-id --doc-title` | `--matter-id --document-type --file-name --content --court-reference` |
| `legal-update-legal-document` | `--document-id` | `--doc-title --document-type --file-name --content --court-reference --document-status` |
| `legal-get-legal-document` | `--document-id` | |
| `legal-list-legal-documents` | | `--matter-id --document-type --document-status --company-id --limit --offset` |
| `legal-file-document` | `--document-id` | `--filed-date --court-reference` |
| `legal-archive-document` | `--document-id` | |
| `legal-search-legal-documents` | `--search` | `--matter-id --document-type --company-id --limit --offset` |
| `legal-add-document-version` | `--document-id` | `--content` |
| `legal-list-document-versions` | `--document-id` | |
| `legal-document-index` | `--matter-id` | |

### Calendar & Deadlines (8 actions)
| Action | Required Flags | Optional Flags |
|--------|---------------|----------------|
| `legal-add-calendar-event` | `--company-id --event-title --event-date` | `--matter-id --event-type --event-time --location --event-description --reminder-days --is-critical` |
| `legal-update-calendar-event` | `--event-id` | `--event-title --event-type --event-date --event-time --location --event-description --event-status --reminder-days --is-critical` |
| `legal-list-calendar-events` | | `--matter-id --event-type --event-status --company-id --limit --offset` |
| `legal-complete-event` | `--event-id` | |
| `legal-add-deadline` | `--company-id --matter-id --deadline-title --due-date` | `--deadline-type --is-court-imposed --assigned-to --notes` |
| `legal-update-deadline` | `--deadline-id` | `--deadline-title --deadline-type --due-date --is-court-imposed --assigned-to --notes` |
| `legal-list-deadlines` | | `--matter-id --deadline-type --is-completed --company-id --limit --offset` |
| `legal-complete-deadline` | `--deadline-id` | |

### Conflicts (4 actions)
| Action | Required Flags | Optional Flags |
|--------|---------------|----------------|
| `legal-check-conflicts` | `--company-id --search-name` | `--matter-id --checked-by` |
| `legal-add-conflict-waiver` | `--company-id --conflict-check-id --waived-by` | `--matter-id --waiver-reason` |
| `legal-list-conflict-checks` | | `--company-id --matter-id --conflict-result --limit --offset` |
| `legal-conflict-report` | `--company-id` | |

### Compliance & Reports (9 actions)
| Action | Required Flags | Optional Flags |
|--------|---------------|----------------|
| `legal-add-bar-admission` | `--company-id --attorney-name --jurisdiction` | `--bar-number --admission-date --expiry-date --admission-status --cle-hours-required` |
| `legal-update-bar-admission` | `--bar-admission-id` | `--attorney-name --bar-number --jurisdiction --admission-date --expiry-date --admission-status --cle-hours-required` |
| `legal-list-bar-admissions` | | `--company-id --attorney-name --admission-status --jurisdiction --limit --offset` |
| `legal-add-cle-record` | `--company-id --attorney-name --course-name --completion-date` | `--bar-admission-id --cle-provider --cle-hours --cle-category --certificate-number` |
| `legal-list-cle-records` | | `--company-id --attorney-name --bar-admission-id --cle-category --limit --offset` |
| `legal-cle-compliance-report` | `--company-id` | |
| `legal-matter-profitability-report` | `--company-id` | |
| `legal-practice-area-analysis` | `--company-id` | |
| `status` | | |

### Quick Command Reference
| User Says | Action |
|-----------|--------|
| "Add a new client" | `legal-add-client` |
| "Open a new matter" | `legal-add-matter` |
| "Log billable time" | `legal-add-time-entry` |
| "Record an expense" | `legal-add-expense` |
| "Generate an invoice" | `legal-generate-invoice` |
| "Make a trust deposit" | `legal-deposit-trust` |
| "File a document" | `legal-file-document` |
| "Add a court deadline" | `legal-add-deadline` |
| "Run a conflict check" | `legal-check-conflicts` |
| "Check CLE compliance" | `legal-cle-compliance-report` |
| "Matter profitability" | `legal-matter-profitability-report` |

## Technical Details (Tier 3)

**Tables owned (16):** legalclaw_client, legalclaw_matter, legalclaw_matter_party, legalclaw_time_entry, legalclaw_expense, legalclaw_invoice, legalclaw_trust_account, legalclaw_trust_transaction, legalclaw_document, legalclaw_calendar_event, legalclaw_deadline, legalclaw_conflict_check, legalclaw_conflict_waiver, legalclaw_bar_admission, legalclaw_cle_record

**Script:** `scripts/db_query.py` routes to 7 domain modules: matters.py, timebilling.py, trust.py, documents.py, calendar.py, conflicts.py, compliance.py

**Data conventions:** Money = TEXT (Python Decimal), IDs = TEXT (UUID4), Dates = TEXT (ISO 8601), Booleans = INTEGER (0/1), Time = 6-minute increments (0.1 hours)

**Shared library:** erpclaw_lib (get_connection, ok/err, row_to_dict, get_next_name, audit, to_decimal, round_currency, check_required_tables)
