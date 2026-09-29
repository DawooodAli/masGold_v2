# CLAUDE.md — masGold_v2

This file is the operating contract for Claude Code when working in this repository.

Project root:

`C:\Users\masjewelery\PycharmProjects\masGold_v2`

Claude must also read `AGENTS.md` before making any change.  
If `CLAUDE.md` and `AGENTS.md` appear to conflict, **stop and ask before editing**.

---

# 1. Project

**masGold_v2** is an internal Arabic RTL jewelry / gold-shop management system for **مجوهرات ماس** in Egypt.

Technology:

- Python
- PySide6
- Qt Quick / QML
- MySQL 8
- Windows
- VS Code

Architecture is locked:

`QML → ViewModel → Service → Repository → MySQL`

Rules:

- QML and ViewModels must never contain SQL.
- QML must not contain core business/accounting logic.
- Repositories perform persistence.
- Services enforce business rules and transactions.
- ViewModels expose safe UI state/actions.
- Preserve existing architecture unless explicitly approved.

---

# 2. Working protocol for Claude Code

Before editing:

1. Read this `CLAUDE.md`.
2. Read `AGENTS.md` completely.
3. Inspect the actual current files.
4. Run:
   ```powershell
   git branch --show-current
   git status
   git log --oneline -10
   ```
5. Report the relevant current structure before implementing a substantial change.

Never assume a previous chat description is more current than the local workspace.

Do not fix unrelated issues.

Do not make broad refactors while implementing a narrow feature.

Do not commit unless explicitly instructed.

Do not push without explicit approval.

Do not run destructive SQL.

Do not run migrations unless explicitly approved.

Do not write live financial/sample data merely to test a feature unless explicitly approved.

When reporting code changes, always use:

`File → Class/component → Method`

---

# 3. UI rules — locked

Employee-facing UI is Arabic.

Use:

- Cairo font everywhere
- RTL
- right alignment
- physical sidebar remains on the **right**
- dark/gold masGold design language
- compact cards
- thin borders
- restrained rounded corners

Do **not** globally enable `LayoutMirroring`.

Form convention:

`[control/value] ← [Arabic label on the physical right]`

All of the following must remain Arabic/RTL/right aligned:

- labels
- headings
- placeholders
- ComboBoxes
- TextFields
- TextAreas
- dialogs
- tables
- buttons
- warnings/errors

Do not stretch controls unnecessarily across the full viewport.

Responsive checks should normally include:

- 1440×900
- 1280×800
- 1080×680

Avoid hidden/clipped actions at 1080×680.

---

# 4. Financial / data rules

Use `Decimal` for:

- money
- gold prices
- making charges
- calculated totals
- weights

Never use float for authoritative financial calculations.

BIGINT IDs must cross the QML boundary as decimal strings.

Never hard-code DB movement-type numeric IDs when a stable movement code exists.

Resolve codes dynamically.

---

# 5. Authentication and actor attribution

Authentication Foundation V1 is implemented.

Important concepts:

- `AppSession`
- verified `ActorContext`
- active user
- optional linked employee
- roles/permissions
- `APP_LOGIN`
- `SETTINGS_MANAGE`

QML must never be allowed to provide arbitrary:

- `user_id`
- `employee_id`
- actor
- permission set

Protected writes obtain actor identity from the authenticated session/service layer.

Service-side authorization must not rely solely on QML button visibility.

Current first-run behavior:

```text
No users
→ First Run Owner Setup
→ username fixed to admin
→ owner sets password
→ OWNER role
→ verified session
→ Main app
```

There is no permanent blank-password admin.

Passwords use Argon2id.

Never log passwords, hashes, or API secrets.

---

# 6. Customers V2 — accepted

Customers V2 is implemented.

Features include:

- list/search/filter/pagination
- auto immutable customer code
- Arabic search normalization
- add/edit/deactivate/reactivate
- reusable CustomerPicker
- independent picker state per instance
- customer profile
- read-only account statement
- running balance
- warnings
- Dead Cash history

Customer code:

`C{id:06d}`

Example:

`id=143 → C000143`

Customer permanent identity remains:

`customers.id`

Balance semantics:

- positive → customer owes shop
- negative → shop owes customer
- zero → settled

Running balances must be calculated from the complete ledger before display filters/pagination.

Do not calculate financial running balances in QML.

---

# 7. Inventory / Old Gold — critical distinction

`OLD_STOCK` and `OLD_GOLD` are not the same.

## OLD_STOCK

An individual inventory piece.

Has inventory identity.

## OLD_GOLD / كسر

Bulk grams by karat.

No:

- piece identity
- barcode
- factory
- category
- item type
- size

Old Gold uses a movement ledger.

Positive movement = IN.  
Negative movement = OUT.

Customer sells old gold to shop:

`CUSTOMER_BUY_IN`

Manufacturing consumption:

`MANUFACTURE_OUT`

Resolve movement type by code.

Do not resurrect the obsolete `oldstock_to_newstock` approach.

---

# 8. Billing — current architectural lock

Billing V2 is implemented. Billing V1 persistence is frozen read-only history.

Historical family:

- `billing_invoices`
- `billing_sale_lines`
- `billing_old_gold_lines`
- `billing_invoice_sequences`

Canonical family:

- `invoices`
- `invoice_lines`
- `invoice_payments`
- `invoice_customer_snapshots`
- `invoice_price_snapshots`
- `invoice_documents`
- `invoice_events`

Current architectural lock:

**The `invoices` family is canonical.**

Do not create a third Billing architecture.

Old `billing_*` history remains historical/read-only.

Canonical Billing V2 is implemented (see §16 and the lifecycle baseline below).

Implemented invoice scenarios:

1. sale only
2. old-gold purchase only
3. mixed sale and old-gold purchase in one invoice (distinct from after-sale Exchange)

The entered invoice total is authoritative; payment allocation does not change it
or derived making. Preserve service-side validation of real payments plus explicit
customer-account allocation.

Commercial net:

`N = sale_total - old_gold_purchase_total`

Actual money:

`P = money_received - money_paid`

Residual:

`R = N - P`

`invoice_payments` must represent real money only.

Never convert:

- exchange offset
- unpaid balance
- existing customer credit

into fake cash/payment rows.

---

# 9. Gold Price Engine V1

Gold Price Engine V1 is **complete** (commit `78e5545`, "Complete Gold Price Engine V1").

Existing schema:

- `gold_price_sources`
- `gold_price_quotes`
- `gold_price_activations`
- `active_gold_prices`
- `gold_price_engine_settings`

Implemented areas include:

- manual mode
- GoldTimo provider adapter
- secure provider settings
- Windows Credential Manager / keyring
- Gold Price repository/service/ViewModel
- background worker
- top-bar price display
- Settings controls
- test connection
- AUTO/MANUAL model

Manual price activation is working.

AUTO mode is **active and accepted** as the controlled AUTO activation
(set 2026-09-24 via the "return to AUTO" flow; primary source GOLDTIMO,
refresh 60 s, stale 300 s, max deviation 5%). A normal app run publishes
GoldTimo prices in the background; this is expected.

- Do not switch AUTO/MANUAL without explicit approval.
- Never delete or rewrite historical quotes/activations; they are audit history.
- Test Connection remains fetch-only.

---

# 10. GoldTimo — verified real contract

Verified successful endpoint:

```text
GET https://goldtimo.com/api/v1/price/XAU/EGP/21
```

Required header:

```text
x-api-key: <private key>
```

Real successful response observed:

```text
symbol    : XAU
currency  : EGP
unit      : oz
price     : 221105.51
gram      : 6220
gramBuy   : 6233
gramSell  : 6208
karat     : 21
updatedAt : 2026-09-24T16:02:24+03:00
```

Locked mapping:

```text
GoldTimo gramSell → SHOP_BUY
shop buys from customer

GoldTimo gramBuy → SHOP_SELL
shop sells to customer
```

Do not reverse this.

Top-level:

`unit = oz`

does not invalidate `gramBuy` / `gramSell`.

Those are the per-gram values we consume.

---

# 11. GoldTimo credential storage

GoldTimo API key is not stored in source code or plaintext MySQL.

Normal storage:

Windows Credential Manager / `keyring`

Credential identity:

```text
service: masGold
name: goldtimo_api_key
```

Locked credential precedence:

```text
1. currently entered unsaved Settings key
2. stored Windows keyring key
3. environment fallback
```

Never log or display the secret.

A previous defect was found:

- entered working key returned HTTP 200
- stale stored key returned HTTP 401
- both had length 40
- values differed

`GoldPriceSettingsViewModel.test()` previously cleared the newly entered key before Save, causing Save to retain the stale stored credential.

This workflow was corrected so Test Connection preserves the entered transient key until Save/navigation/logout.

When editing this area, preserve that behavior.

---

# 12. Real GoldTimo fetch-only results

Verified fetch-only values:

```text
Karat   SHOP_BUY   SHOP_SELL   Provider time
18K     5320       5341        16:15:07
21K     6207       6232        16:15:08
24K     7093       7122        16:15:08
```

All returned HTTP 200 using the known-working entered key.

No AUTO activation occurred.

---

# 13. GoldTimo coherent-set rule — implemented

GoldTimo requires separate requests for 18K / 21K / 24K.

Exact equality of all provider timestamps is therefore too strict.

Implemented GoldTimo-specific rule (`services/gold_price_provider.py` →
`validate_quotes()`, `GOLDTIMO_MAX_SKEW_SECONDS = 5`; re-checked on stored rows in
`GoldPriceService._coherent()`). Verified live: skew 0–3 s, coherent set PASS.

```text
earliest = min(updatedAt)
latest   = max(updatedAt)
timestamp_skew = latest - earliest
```

Accept coherent set when:

`timestamp_skew <= 5 seconds`

Reject when:

`timestamp_skew > 5 seconds`

Still require:

- same refresh attempt
- EGP
- returned karat matches requested karat
- existing value validation
- valid provider timestamps
- no stale mixed response

Preserve every original provider timestamp.

Do not fabricate a common timestamp.

If a set-level timestamp is needed, use the newest provider timestamp.

This rule is GoldTimo-specific unless explicitly generalized later.

---

# 14. Gold Price visual semantics — locked

Price colors must distinguish the shop perspective.

Locked mapping:

- `SHOP_BUY / شراء المحل` → green / teal
- `SHOP_SELL / بيع المحل` → gold / amber
- red → error/danger only

Use semantic theme colors rather than one-off hard-coded colors.

Apply consistently to:

- top-bar Gold Price card
- Gold Price dialog
- Settings Test Connection result
- Billing price display

Do not heavily fill entire cells with color.

Prefer colored labels, values, accents, or indicators.

---

# 15. Gold Price UI — complete

Implemented and verified in the real app at 1440×900, 1280×800 and 1080×680:

- Gold Price provider settings use the Settings section/card language
  (`SettingsSectionCard` / `SettingsCard` inline components in `qml/Main.qml`).
- Top-bar card: `عيار 21` / `شراء …  بيع …` / `تلقائي • GoldTimo • منذ …`,
  with correct Arabic singular/dual/plural age text.
- Test Connection result card: formatted prices, local update time, timestamp skew,
  key source, red error state for a rejected set; the page auto-scrolls to it.
- Stored-key Save → restart → Test Connection verified: KEYRING, HTTP 200 ×3, PASS.

Test Connection remains fetch-only. It must not write quotes, write activations
or change AUTO/MANUAL mode.

Billing price snapshots are implemented. `GoldPriceService._billing_set()` uses the
newest provider timestamp for set provenance and preserves each karat's timestamp.

Previously reported Authentication issue (reproduce before any separate fix):

- `FirstRunOwnerSetup.qml` / `Login.qml` null `stage`/`busy` warnings on
  shutdown → separate Authentication cleanup task.

---

# 16. Current implemented lifecycle modules

Canonical Billing V2, Invoice History and calibrated A5 printing are implemented.
Exchange V1 includes Scan/F3/F5/F7 replacements and settlement. Returned Item
Inspection, Return V1 and Reversal / Correction V1 are implemented and committed.
The detailed lifecycle baseline and implementation map below describe their scope.

Preserve the `invoices` architecture and frozen `billing_*` history. Do not treat
completed modules as future implementation tasks or extend Cash/Bank without a
specific request. Do not change Gold Price AUTO/MANUAL state without approval.

---

# 17. Validation expectations

For implementation changes, run relevant:

- Gold Price tests
- full test suite
- Python compile/import checks
- QML lint
- offscreen QML checks

Include real-click visibility checks at 1080×680 to prevent recurrence of clipped buttons.

Do not rely only on programmatic signal emission for UI actions.

---

# 18. Claude Code behavior for this repository

Claude should operate conservatively.

Before a substantial implementation, provide a brief plan.

Prefer narrow edits.

Use local project tests and actual project structure.

When SQL/database behavior matters:

- inspect live definitions read-only first if needed
- do not guess schema
- do not mutate production data without approval

When a bug appears:

1. reproduce
2. identify exact cause
3. apply smallest safe fix
4. add regression test
5. validate UI at real target sizes

Do not report a feature as complete merely because unit tests pass if the user-visible flow has not been checked.

---

# 19. Start-of-task verification

Read both instruction files, inspect actual files and recent Git history, and verify
branch, HEAD and working tree against the task baseline. Report unexpected changes
before proceeding. For substantial changes, explain the existing implementation and
proposed file/class/method changes first. Preserve validated architecture when old
roadmap text is stale; report ambiguities rather than inventing business behavior.

---

# 20. Completed roadmap

- Authentication, Customers, Inventory and Old Gold foundations: completed.
- Gold Price Engine, GoldTimo connectivity/coherence, UI and controlled AUTO: completed.
- Canonical Billing V2 and payment-account settlement workflows: completed.
- Invoice History, archive/reprint and calibrated A5 printing: completed.
- Exchange V1, Scan/F3/F5/F7 replacement flows and settlement: completed.
- Returned Item Inspection: completed.
- Return V1: completed.
- Reversal / Correction V1: completed.
- Customer Account V2 (CS settlements, migration 024): completed.
- Customer Invoices tab and automatic Customers refresh: completed.

The old sequential Billing → payments → archive → reversal roadmap is superseded
by these implementations. No new future module is selected by this document;
continue only with the owner's specified task.

---

## Implemented lifecycle baseline

Completed: Gold Price Engine, Canonical Billing V2, Invoice History, Exchange V1
(including replacement paths and settlement), Returned Item Inspection, Return V1,
Reversal / Correction V1, Customer Account V2 and Customer Invoices. Preserve these implementations; new work requires a
specific task, not completion of the obsolete Billing roadmap.

### Billing V2 and Invoice History

- Canonical persistence is the `invoices` family; `billing_*` remains frozen history.
- Billing supports sale, customer old-gold purchase, and mixed invoices. Finalization
  atomically posts snapshots, inventory/old-gold effects, real payments, customer
  ledger entries and audit events.
- Counted old-gold movements affect available grams; reference-only legacy movements
  do not. Keep customer purchases, F7 sales, Returns, Exchanges and reversals distinct.
- The entered invoice total is authoritative; payment splits do not change it or
  its derived making. Real payments and explicit customer-account allocation must
  satisfy the service's settlement rules. Account allocation is not a cash payment.
- Physical cash posts to `cash_movements`; mobile wallet, InstaPay and bank transfer
  post to `bank_movements` on the selected account. Preserve the implemented paths.
- Invoice History provides read-only search, detail, lifecycle timeline and reprint.
  It uses stored header totals, snapshots and finalized-event values, never today's
  prices or reconstructed making. Unknown legacy values remain unknown.
- Calibrated A5 invoice printing is implemented; preserve its physical layout.

### Exchange V1

- An Exchange is a linked after-sale lifecycle transaction, not a new sale or an
  edit of the original invoice. Source pieces must be identified `SELL_INVENTORY`
  lines. F5/F7 source sale lines and customer old-gold purchases are not exchangeable.
- Replacement paths: Scan barcode; F3 inventory search; F5 manual unidentified
  replacement with reconciliation case; F7 replacement from counted old-gold grams.
- Same-karat differences use actual grams and that karat's quote; mixed karats use
  pure-24K grams and the actual 24K quote. Positive gold difference uses SHOP_SELL;
  negative uses SHOP_BUY. Preserve the service's making and refund rules.
- Settlement uses direction-aware real payment legs plus optional explicit
  customer-account allocation, totaling the difference exactly. Zero difference
  creates neither money nor customer-account entries.
- Identified returned pieces move `SOLD → RETURNED_PENDING`; identified replacements
  move `IN_STOCK → SOLD`. F7 replacements consume counted old-gold grams.
- Exchange and Return prevent simultaneous reuse of the same sold piece/grams.
  Transaction locks, service validation and database constraints protect active
  lifecycle use; a valid reversal releases the reversed lifecycle use.

### Returned Item Inspection

- Exchange and Return share `RETURNED_ITEM_INSPECTION` management reviews.
- The stored barcode is displayed; no physical scan is required. A damaged/missing
  physical label does not remove the stored identity.
- A manager with `MANAGEMENT_REVIEW` chooses `RETURN_TO_STOCK`:
  `RETURNED_PENDING → IN_STOCK`, using the same item and same stored barcode;
  or `CONVERT_TO_OLD_GOLD`: `RETURNED_PENDING → CONVERTED_TO_OLD_GOLD` plus counted
  old-gold IN of the exact karat/weight. The converted item is no longer sellable;
  its barcode remains historical.
- Resolution is atomic and cannot be applied twice.

### Return V1

- A Return has its own RT reference and immutable finalized history linked to the
  original invoice. Multiple Returns are allowed while eligible items/grams remain.
- Value is returned grams × current SHOP_BUY of the same karat, snapshotted per line.
- At <=72h after original finalization, refund historical making attributable to
  the returned grams. Missing historical making is refused, never invented.
- At >72h, no making refund; manager approval of the exact selection is required.
- The end-of-day-15 deadline is enforced by `services/return_service.py` → `eligibility()`:
  `now < midnight(invoice local date + 15 days)`. Approval never extends it.
- Multi-payment OUT legs plus explicit customer-account allocation must equal the
  Return total. Payment accounts may become negative. Reason/notes are optional.
- Identified pieces return whole: `SOLD → RETURNED_PENDING`, then inspection.
- F7 `SELL_OLD_GOLD` supports partial-weight Return; exact grams restore counted
  old gold directly. F5 unidentified sales and customer old-gold purchases are refused.
- Finalized Returns cannot be casually canceled; use Reversal / Correction.

### Reversal / Correction V1

- `TRANSACTION_CORRECT` is assigned to MANAGER/OWNER only; authorization is rechecked
  in the transaction using AppSession. A reason is mandatory.
- Full Reversal (`RVxxxxxx`) reverses exact stored posted effects of an Invoice,
  Exchange or Return, including its effective settlement; never use current prices.
- Original finalized records remain immutable. Corrections are new linked records
  with ledger movements and audit history, not destructive rewrites.
- Downstream dependencies block reversal (including disposed returned pieces);
  resolve eligible dependencies newest first, with no automatic cascade.
- Settlement Correction (`FCxxxxxx`) changes only financial allocation, posting
  differences by account/customer. It does not change items, grams, gold, making
  or the authoritative total. Corrections chain from the latest effective settlement.
- After full reversal, another reversal and further settlement correction are blocked.

### Customer Account V2 (migration 024)

- Canonical customer balance source: `customer_transactions` / `vw_customer_balances`.
  Positive → `على العميل`; negative → `للعميل`; zero → `الحساب متوازن`.
- Customer Account Settlement (`CSxxxxxx`, source `CUSTOMER_ACCOUNT_SETTLEMENT`, no
  invoice) records real money against an existing customer balance. Partial and split
  multi-leg settlements are supported; oversettlement is refused; financial accounts
  may become negative; concurrent settlements are protected by transaction locks.
- CS is correctable through Settlement Correction (FC) and Full Reversal (RV).
- Permission `CUSTOMER_ACCOUNT_SETTLE`: OWNER, MANAGER, CASHIER (not SALESPERSON),
  rechecked service-side from AppSession.
- Statement shows running balance from the complete ledger with source/reference mapping.

### Customer Invoices and refresh

- The Customers area separates `كشف الحساب` (actual customer-ledger movements only) from
  `فواتير العميل` (ALL finalized invoices for the canonical `customer_id`, including
  fully paid invoices with no customer-account effect).
- Server-side 50+1 pagination, newest first, invoice-number search, inclusive date
  filters and transaction-type classification. The clickable invoice number opens the
  existing Invoice History detail (exact string IDs; no duplicate detail page).
- Customers/account/invoices refresh automatically on page entry/return, customer
  selection, and finalized Invoice, Exchange, Return, CS, RV/FC and relevant manager
  dispositions. No timer polling. Refresh preserves selection, tab, filters and page
  where practical; manual refresh remains a fallback.

### Implementation map

- `services/invoice_service.py` → `InvoiceService` → `preview()` / `finalize()`;
  `repositories/invoice_repository.py` → `InvoiceRepository`.
- `services/invoice_history_service.py` → `InvoiceHistoryService` → `search()` /
  `detail()` / `reprint()`.
- `services/exchange_service.py` → `ExchangeService` → replacement lookup/search,
  `preview()` / `finalize()`; `app/viewmodels/exchange_viewmodel.py` →
  `ExchangeViewModel`; `qml/ExchangeWorkspace.qml` → Scan/F3/F5/F7 workspace.
- `services/management_review_service.py` → `ManagementReviewService` → returned-item
  `return_to_stock()` / `convert_to_old_gold()`; `qml/ManagementReviewPage.qml` → stored barcode and inspection actions.
- `services/return_service.py` → `eligibility()` / `ReturnService` → approval and
  `finalize()`; `repositories/invoice_return_repository.py` → `InvoiceReturnRepository`.
- `services/correction_service.py` → `CorrectionService` → `reverse()` /
  `correct_settlement()`; `services/correction_rules.py`
  → RV/FC references; `repositories/correction_repository.py` → `CorrectionRepository`.
- `services/customer_account_service.py` → `CustomerAccountService` → `statement()` /
  `customer_invoices()` / `finalize()`; `repositories/customer_account_repository.py`
  → `CustomerAccountRepository`; `app/viewmodels/customer_account_viewmodel.py` →
  `CustomerAccountViewModel`.

## Repository, schema and execution discipline

Baseline for this documentation sync: branch `feature/billing-v2`, implementation
HEAD `e798badc56d0d82874e756efe72c4ce28fc24e67` (`feat(customers): add invoice history and automatic refresh`).
Before substantial work, check branch, HEAD and `git status`; stop and report unexpected
files or commits. Inspect actual files and recent history rather than assuming this
baseline remains the latest forever. Read both `AGENTS.md` and `CLAUDE.md`.

- Schema chain: migrations `001–024` in `database/schema`; next migration is `025`
  unless later repository state proves otherwise. Already-applied migrations are
  immutable, including 019–024. Future schema work needs the next forward migration,
  guarded rollback, `SHA256SUMS`, chain tooling and appropriate migration/rehearsal tests.
- Normal DEV database: `masgold_v2`. Never mutate `golddb`. Rehearsal/prod databases
  (`masgold_rehearsal`, `masgold_prod`) require an explicit task request. Before an
  approved real migration or destructive-capable validation, verify the database
  using both configuration and the live session. Never apply migrations without approval.
- `Note for password.txt` is local-only: leave it untouched, untracked and excluded.
  Do not open, print, stage, commit it or remove its exclusion. Never expose secrets.
- No commit or push without explicit approval. Keep changes within the requested scope.
- Preserve finalized invoice, Exchange, Return and correction history. Use explicit
  correction transactions; do not silently rewrite/delete historical records.

### Official test baseline

At the implementation baseline above: **1015 total = 975 passed + 40 skipped**,
**0 failures, 0 errors, 0 runtime QML warnings**. This is the supplied
confirmed baseline, not a fresh execution during this documentation sync.
Standalone `tests/test_invoice_service.py`: 44 tests pass.

Intended suite command (use project `.venv` Python):

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Run focused checks and relevant regression for implementation changes. Documentation-only
changes require diff/consistency checks; no full suite is required.

### VS Code Codex sandbox

Python `tempfile` / directories created with mode `0o700` can become inaccessible
inside this Windows VS Code Codex sandbox. This is an environment issue, not a
project bug. Do not modify project code/tests to work around it. When it blocks a
legitimate test/tool, use the narrow required command outside the sandbox through
the approval mechanism. No Administrator elevation, ACL changes, Defender changes
or broad access. Distinguish environment failures from application failures.
