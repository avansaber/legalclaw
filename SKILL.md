---
name: legalclaw
version: 1.0.0
description: Legal Practice Management -- 99 actions across 9 domains. Matters, time & billing, trust/IOLTA accounting, documents, calendar/deadlines, conflicts, compliance, intake, LEDES billing, portal, and analytics.
author: AvanSaber
homepage: https://github.com/avansaber/legalclaw
source: https://github.com/avansaber/legalclaw
tier: 4
category: legal
requires: [erpclaw]
database: ~/.openclaw/erpclaw/data.sqlite
user-invocable: true
tags: [legalclaw, legal, law, matter, case, client, attorney, billing, trust, iolta, escrow, invoice, time-entry, expense, document, deadline, calendar, conflict, bar, cle, compliance, ledes, intake, portal, settlement, contingency]
scripts:
  - scripts/db_query.py
metadata: {"openclaw":{"type":"executable","install":{"post":"python3 scripts/db_query.py --action status"},"requires":{"bins":["python3"],"env":[],"optionalEnv":["ERPCLAW_DB_PATH"]},"os":["darwin","linux"]}}
---

# legalclaw

Legal Practice Administrator for LegalClaw -- AI-native legal practice management on ERPClaw.
Manages client intake, matters, time tracking, expense recording, invoice generation,
IOLTA/escrow trust accounting, legal documents, court calendar/deadlines, conflict checking,
bar admissions, CLE compliance, LEDES billing, client portal, settlements, and analytics.
All financials use Decimal precision. Trust disbursements enforce balance checks.

### Skill Activation Triggers

Activate when user mentions: law firm, attorney, lawyer, legal, matter, case, client intake,
retainer, billable hours, time entry, trust account, IOLTA, escrow, legal document, court filing,
deadline, statute of limitations, conflict check, bar admission, CLE, compliance, deposition,
hearing, trial, invoice, billing, opposing counsel, LEDES, settlement, contingency.

### Setup
```
python3 {baseDir}/../erpclaw/scripts/erpclaw-setup/db_query.py --action initialize-database
python3 {baseDir}/init_db.py
python3 {baseDir}/scripts/db_query.py --action status
```

## Quick Start
```
--action legal-add-client --company-id {id} --name "Acme Corp" --client-type business --billing-rate "350.00"
--action legal-add-matter --company-id {id} --client-id {id} --title "Contract Dispute" --practice-area litigation --billing-method hourly --billing-rate "400.00"
--action legal-add-time-entry --company-id {id} --matter-id {id} --attorney "Jane Smith" --hours "2.5" --te-description "Research" --rate "400.00"
--action legal-generate-invoice --company-id {id} --matter-id {id}
--action legal-deposit-trust --company-id {id} --trust-account-id {id} --matter-id {id} --amount "5000.00"
```

## All 99 Actions

### Matters & Clients (14 actions)
| Action | Description |
|--------|-------------|
| `legal-add-client` | Add client |
| `legal-update-client` | Update client |
| `legal-get-client` | Get client details |
| `legal-list-clients` | List clients |
| `legal-add-matter` | Create matter/case |
| `legal-update-matter` | Update matter |
| `legal-get-matter` | Get matter details |
| `legal-list-matters` | List matters |
| `legal-add-matter-party` | Add party to matter |
| `legal-list-matter-parties` | List matter parties |
| `legal-close-matter` | Close matter |
| `legal-reopen-matter` | Reopen matter |
| `legal-matter-summary` | Matter summary with financials |
| `legal-client-portfolio` | Client portfolio overview |

### Time & Billing (14 actions)
| Action | Description |
|--------|-------------|
| `legal-add-time-entry` | Log billable time |
| `legal-update-time-entry` | Update time entry |
| `legal-list-time-entries` | List time entries |
| `legal-add-expense` | Record expense |
| `legal-update-expense` | Update expense |
| `legal-list-expenses` | List expenses |
| `legal-generate-invoice` | Generate invoice from unbilled |
| `legal-get-invoice` | Get invoice details |
| `legal-list-invoices` | List invoices |
| `legal-send-invoice` | Send/submit invoice |
| `legal-record-payment` | Record invoice payment |
| `legal-write-off-invoice` | Write off invoice |
| `legal-billable-utilization-report` | Billable utilization |
| `legal-ar-aging-report` | AR aging report |

### Trust Accounting (10 actions)
| Action | Description |
|--------|-------------|
| `legal-add-trust-account` | Create trust/IOLTA account |
| `legal-get-trust-account` | Get trust account |
| `legal-list-trust-accounts` | List trust accounts |
| `legal-deposit-trust` | Deposit to trust |
| `legal-disburse-trust` | Disburse from trust |
| `legal-transfer-trust` | Transfer between trust accounts |
| `legal-list-trust-transactions` | List trust transactions |
| `legal-trust-reconciliation` | Reconcile trust account |
| `legal-trust-balance-report` | Trust balance report |
| `legal-trust-interest-distribution` | Distribute trust interest |

### Documents (10 actions)
| Action | Description |
|--------|-------------|
| `legal-add-legal-document` | Add legal document |
| `legal-update-legal-document` | Update document |
| `legal-get-legal-document` | Get document |
| `legal-list-legal-documents` | List documents |
| `legal-file-document` | File document with court |
| `legal-archive-document` | Archive document |
| `legal-search-legal-documents` | Search documents |
| `legal-add-document-version` | Add document version |
| `legal-list-document-versions` | List document versions |
| `legal-document-index` | Document index for matter |

### Calendar & Deadlines (8 actions)
| Action | Description |
|--------|-------------|
| `legal-add-calendar-event` | Add calendar event |
| `legal-update-calendar-event` | Update event |
| `legal-list-calendar-events` | List events |
| `legal-complete-event` | Complete event |
| `legal-add-deadline` | Add deadline |
| `legal-update-deadline` | Update deadline |
| `legal-list-deadlines` | List deadlines |
| `legal-complete-deadline` | Complete deadline |

### Conflicts (4 actions)
| Action | Description |
|--------|-------------|
| `legal-check-conflicts` | Run conflict check |
| `legal-add-conflict-waiver` | Add conflict waiver |
| `legal-list-conflict-checks` | List conflict checks |
| `legal-conflict-report` | Conflict report |

### Intake (5 actions)
| Action | Description |
|--------|-------------|
| `legal-add-intake` | Create client intake |
| `legal-update-intake` | Update intake |
| `legal-list-intakes` | List intakes |
| `legal-convert-intake-to-matter` | Convert intake to matter |
| `legal-intake-conversion-report` | Intake conversion report |

### Compliance (9 actions)
| Action | Description |
|--------|-------------|
| `legal-add-bar-admission` | Add bar admission |
| `legal-update-bar-admission` | Update bar admission |
| `legal-list-bar-admissions` | List bar admissions |
| `legal-add-cle-record` | Add CLE credit |
| `legal-list-cle-records` | List CLE records |
| `legal-cle-compliance-report` | CLE compliance report |
| `legal-matter-profitability-report` | Matter profitability |
| `legal-practice-area-analysis` | Practice area analysis |
| `legal-communication-summary-report` | Communication summary |

### LEDES & Advanced Billing (7 actions)
| Action | Description |
|--------|-------------|
| `legal-generate-invoice-ledes` | Generate LEDES invoice |
| `legal-validate-ledes` | Validate LEDES format |
| `legal-calculate-contingency-fee` | Calculate contingency fee |
| `legal-calculate-sol` | Calculate statute of limitations |
| `legal-list-approaching-sol` | List approaching SOL |
| `legal-check-retainer-balance` | Check retainer balance |
| `legal-set-retainer-threshold` | Set retainer threshold |

### Advanced Features (9 actions)
| Action | Description |
|--------|-------------|
| `legal-generate-replenishment-request` | Generate retainer replenishment |
| `legal-record-settlement` | Record settlement |
| `legal-disburse-settlement` | Disburse settlement funds |
| `legal-settlement-report` | Settlement report |
| `legal-add-communication` | Log communication |
| `legal-list-communications` | List communications |
| `legal-communication-timeline` | Communication timeline |
| `legal-add-task-template` | Create task template |
| `legal-get-task-template` | Get task template |

### Portal & Templates (9 actions)
| Action | Description |
|--------|-------------|
| `legal-list-task-templates` | List task templates |
| `legal-add-task-template-item` | Add template item |
| `legal-apply-task-template` | Apply template to matter |
| `legal-portal-matter-status` | Client views matter status |
| `legal-portal-list-documents` | Client views documents |
| `legal-portal-list-invoices` | Client views invoices |
| `legal-portal-list-trust-activity` | Client views trust activity |
| `legal-portal-send-message` | Client sends message |
| `legal-portal-upload-document` | Client uploads document |

## Technical Details (Tier 3)
**Tables (16):** All use `legalclaw_` prefix. **Script:** `scripts/db_query.py` routes to 9 modules. **Cross-skill:** Invoices create sales_invoice via erpclaw-selling. Payments create payment_entry via erpclaw-payments. **Data:** Money=TEXT(Decimal), IDs=TEXT(UUID4), Time=0.1hr increments.
