# CLAUDE.md — masGold_v2

## Current owner-approved intermediary LOT return policy

- Migration 051's HR/RP schema and historical records remain in place, but new LOT physical-receipt postings are disabled. Exact replay of an already successful request returns its original HR/RP records; legacy receipt history remains readable and valid canonical reversals remain available.
- A normal whole-bar return requires the original pool identity, full restored balance, no effective allocation/receipt or dependent use, the existing OPEN-custody check, and the owner's confirmation that this is the same intact original bar. Reversed `GRAM_ENTITLEMENT` and `WHOLE_ORIGINAL_BAR` settlements need no declaration that they were erroneous. Reversed physical-division and physical-receipt events require one owner declaration that the listed physical events were recorded in error and did not occur, while the same original bar remains intact.
- One confirmation checkbox is bound to the exact server-derived event list and review token and revalidated under transaction locks; its audit metadata is written atomically with the return. `LOT_HOLD` correction guards and LOGO behavior remain unchanged.

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
- account statement (`كشف الحساب`): read-only historical ledger view
- running balance
- warnings
- Dead Cash history
- customer invoices (`فواتير العميل`): separate from the statement
- Customer Account V2 settlements (see "Customer Account V2" below)

The statement/history itself is read-only. Customer Account V2 adds explicit
finalized Customer Account Settlements (`CSxxxxxx`): they may be partial, split
across multiple real payment methods, and oversettlement is refused. Finalized CS
records are immutable; mistakes are corrected with FC/RV, never by editing or
deleting the original CS.

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

At the beginning of each task, verify the actual branch, HEAD and working-tree state
using Git (`git branch --show-current`, `git log --oneline -10`, `git status`); stop and
report unexpected files or commits. Do not treat any commit hash in this document as the
current HEAD. Read both `AGENTS.md` and `CLAUDE.md`.

- Schema chain: migrations `001–079` in `database/schema`; next migration is `080`
  unless later repository state proves otherwise. Already-applied migrations are
  immutable, including 019–056. `database/migrations/*.sql` are historical prototypes
  (see its README) and are never applied. Future schema work needs the next forward migration,
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

### Current rules added by the post-review packs (authoritative)

- **Old-gold discount:** 1.25%–5.00% on a Mixed invoice (any sale line), 1.00%–5.00% on an old-gold-only invoice (owner decision 2026-10-23), default 1.50%.
- **EGP-5:** the customer-facing amount is rounded once, on the final amount; exact economics stay
  exact. For old-gold-only: real money + account allocation = the final rounded amount.
- **Old-gold-only invoices:** the customer is optional when the account allocation is 0 and
  required whenever any account allocation exists. They are save-only (`حفظ فقط`), non-printable and
  have no reprint in History; SALE and MIXED invoices still print on the frozen A5 layout.
  Classification comes from the saved lines (BUY_OLD_GOLD, no sale line), never a new DB flag.
- **Users / Roles / Permissions admin:** `USERS_MANAGE`, service-side, actor from AppSession.
- **Inventory entry and old-gold conversion:** backend-authorized (`INVENTORY_MANAGE` /
  `OLD_GOLD_MANAGE`); the actor is the session user, never QML-supplied. UI gating is a convenience.
- **Financial accounts:** each cash/bank account has a movement ledger/history with its source
  context; accounts may go negative.
- **Dev auto-login:** off by default. The only switch is `MASGOLD_DEV_AUTO_LOGIN=1`; it never
  activates in a frozen build, a production/release `MASGOLD_ENV`, or `masgold_prod`.
- **Re-authentication cooldown:** Dead Cash and settlement-approver password checks use one
  persistent `AuthenticationService` per owning service, so the failure cooldown persists.
- **Frozen Billing V1:** `billingVM` is not registered; the V1 source files remain as history only.
- **Purchasing & Suppliers V1 (migration 034):** supplier accounting has two independent dimensions that are never
  combined: GOLD (each supplier has ONE dedicated karat, 18, 21, 22 or 24, and its invoices inherit it; supplier gold
  balances and running balances are PHYSICAL grams of that karat, with no Eq21 conversion; the supplier invoice weight
  is authoritative; the ledger's `equivalent_21_delta_grams` stays stored beside the original weight and karat as an
  internal compatibility field and never drives a supplier-account amount or direction; Eq21 rules outside supplier
  accounts are unchanged) and MAKING (EGP; per making group
  = group supplier weight x EGP/g). Both are immutable ledgers (`supplier_gold_movements`,
  `supplier_making_movements`); one sign convention: delta > 0 = the shop owes the supplier (علينا للمورد),
  delta < 0 = the supplier owes the shop (لنا عند المورد); balances are SUM(delta), never floored or stored.
  Posting is atomic and idempotent. Barcode intake creates real inventory pieces only through
  `InventoryService.create_item_on_connection` (canonical path, acquisition source `SUPPLIER_PURCHASE` = توريد مورد, never `MARKET_PURCHASE` which is reserved for gold bought on the market, canonical barcode sequence) and
  links invoice -> group -> piece with the group's original making rate. Weight variance (فرق الوزن = barcoded
  weight - supplier weight) is informational only. Rights: `SUPPLIER_VIEW`, `SUPPLIER_MANAGE`,
  `SUPPLIER_INVOICE_EDIT`, `SUPPLIER_INVOICE_POST`, `SUPPLIER_INTAKE` (OWNER/MANAGER, explicit grants).
- **Supplier intake category (automatic):** the inventory category of a supplier-intake piece is resolved by the service
  from the making group's making rate and the existing `item_categories` purchase-making ranges (inclusive at both ends,
  a missing bound is open, unconfigured or inactive categories never match). Nothing is chosen on invoice creation and
  nothing is stored on the group; the item type never overrides it; a supplied category is only checked against it. No
  match, or several, refuses the piece with the exact reason (never guessed). Group intake is also capped at the group's
  supplier weight + 0.333 g (cumulative actual weight, fixed, enforced in the service).
- **Supplier making payments (migration 036):** the supplier MAKING account (EGP) is paid by an immutable payment
  document `SMxxxxxx` (`supplier_making_payments`; money OUT from `MAIN_SAFE`, resolved by its code, or from a selected
  active bank / wallet account) that posts atomically ONE real money movement (`cash_movements` / `bank_movements`) and ONE
  negative `supplier_making_movements` row (type `PAYMENT`). It is not allocated to an invoice, it is never capped at the
  payable balance (the net making position may cross zero into supplier credit), financial accounts may go negative as
  before, and the supplier GOLD account is never touched. Rights: `SUPPLIER_MAKING_PAY` (OWNER, MANAGER) and
  `SUPPLIER_MAKING_PAY_REVERSE` (OWNER only), both re-checked in the transaction; the actor is the AppSession user. The only
  correction is a full reversal (`RVxxxxxx`) through `CorrectionService` (standalone source `SUPPLIER_MAKING_PAYMENT`, which
  needs `TRANSACTION_CORRECT` as well as the OWNER-only right): the exact opposite money movement on the ORIGINAL account plus
  a `PAYMENT_REVERSAL` making movement; the payment and its movements are never rewritten. Idempotent on the request key.
- **Market-trip custody (migration 037):** the OWNER takes cash from `MAIN_SAFE` (resolved by code) and Scrap (existing master
  karat, PHYSICAL grams, never Eq21) to the market. A trip `MTxxxxxx` has insert-only entries `MTxxxxxx-n` (ISSUE / RETURN)
  that post atomically ONE cash movement and/or counted Scrap movements; custody = issued - returned over the entries that were
  not reversed, per karat and for cash, never negative. Issue consumes only AVAILABLE Scrap (manager-review holds excluded);
  gold in custody is shop-owned but unavailable and is shown apart from shop balances. Money paid from custody later must not be
  deducted from Main Safe again. This slice has no consumption (no sale, supplier settlement, merchandise, heating, melting,
  loss or write-off): a trip closes only at exactly zero cash and zero per karat, and the OWNER may reopen it (audited). Right:
  `MARKET_TRIP_MANAGE` (OWNER only, rechecked in each transaction). The only correction is a full reversal (`RVxxxxxx`) through
  `CorrectionService` (source `MARKET_TRIP_ENTRY`, also needs `TRANSACTION_CORRECT`): blocked for a closed trip, for an issue
  that a later return depends on, and for a return whose Scrap the shop has since used.
- **Market-trip melting (migration 039):** every NEW gold line of a custody issue carries the OWNER's explicit designation, `MELT` (للسبك) or `LOGO` (لوجو); custody is tracked per (karat, designation) (both may exist for one karat, their combined issue is checked against available Scrap, shop Scrap stays pooled by karat) and a return preserves the designation and cannot exceed it. Gold written before 039 is `UNCLASSIFIED`: return only, never melted, never reclassified. A `MELT` entry (`MT…-n`) consumes MELT-designated custody (any active master karat, physical grams) and atomically creates assayed output lots: one MAIN lot (fineness in parts per thousand, 0.1 precision, exact `Decimal`, never mapped onto a master karat) plus optional RESIDUE lots whose fineness may be unknown (never inferred); a weight gain needs an explanation. No cash, shop Scrap or supplier movement occurs. The melt keeps immutable analytical snapshots (input weight, nominal pure gold `SUM(weight*karat/24)`, output weight, known pure gold, signed differences outputs - inputs, Eq21 = pure*24/21, six decimals); when any assay is unknown the pure-gold difference is incomplete and no loss is shown. Differences are permanent (also after closure or reversal) and never write off or touch a supplier account. A trip closes only when cash, every (karat, designation) and every assayed lot are zero. A melt is reversed through `CorrectionService` (`MARKET_TRIP_MANAGE` + `TRANSACTION_CORRECT`, reason mandatory) unless a later unreversed entry used its lots; an issue is not reversible while a return or melt depends on it. Assayed lots return to the shop through a `LOT_RETURN` entry (migration 040): a WHOLE lot leaves custody (a melt bar is indivisible: it returns complete or stays; the posted grams are the lot's full outstanding weight derived under the locks, a client-supplied quantity is only compared with it and must equal it; a lot partially returned by an earlier build is refused with an explanation until that return is reversed, never completed or rewritten; no cutting or splitting exists) through a negative `SHOP_RETURN` lot movement and enters the shop's own lot ledger (`market_trip_lot_shop_movements`, insert-only) atomically; the lot keeps its identity, melt reference, kind and exact assay (never mapped onto a master karat; an unassayed residue stays unassayed), and no cash, bank, supplier, inventory-piece or Scrap movement exists. Shop lots are shown apart from the Scrap strip and karat totals. A `LOT_RETURN` is reversed through `CorrectionService` (the exact shop grams leave again through a `RETURN_REVERSAL` movement, the whole lot returns to custody because the entry stops counting; blocked when the shop no longer holds them); an effective lot return blocks the reversal of its melt until it is reversed. A bar in the shop can leave again into a DIFFERENT open custody through a `LOT_ISSUE` entry (migration 041): the whole bar only (the full weight derived under the locks, a client quantity only compared; a partial shop balance or a bar already in a custody is refused), posting one shop OUT (`TRIP_ISSUE`) and one custody IN (`SHOP_ISSUE`) atomically, and returns from there through the same `LOT_RETURN` (the exact opposite transfer). A lot is in the shop or in exactly one custody: its location is derived from the effective movements, never from its origin melt, so the origin trip may stay closed and cycles repeat without special cases; identity, weight, kind, assay and the stored melt analysis never change. A `LOT_ISSUE` is reversed through `CorrectionService` (`ISSUE_REVERSAL` puts the whole bar back into the shop) unless a later effective return depends on it; a return cannot be reversed while its shop stock was reissued elsewhere, and a melt stays protected while any downstream transfer is effective. A NEW custody can be created in one transaction from any combination of MAIN_SAFE cash, Scrap lines and whole shop bars (`MarketTripService.create_trip`): an `ISSUE` entry (cash / Scrap, when present) and a `LOT_ISSUE` entry (bars, when present) post atomically under one request key and hash, so a bar-only custody needs no dummy cash or Scrap and any failing source rolls back the whole creation. Logo heating (`HEAT` entry, migration 042, user-facing «حمّية اللوجو»): LOGO-designated gold of an OPEN custody may be heated without being melted. A document declares the processed input grams and the ACTUAL output grams per karat (one line per karat), keeps the original karat and the LOGO designation, and records only physical weights: no purity, pure-gold or Eq21 figure. A weight gain needs a mandatory explanation, both weights are positive, and the workshop/operator is required (reference optional). The custody of (karat, LOGO) is issued - returned - melted + the effective heat differences, so the remaining physical grams return through the normal Logo return and the difference is permanent investigation evidence, never a custody shortage or write-off; a heat posts no cash, Scrap, supplier, inventory or lot movement and is not part of any melt analysis. Logo custody is pooled per karat: a document does not claim physical identity with an earlier heat, the cumulative effective loss is the sum of non-reversed differences, and any unprocessed figure is a snapshot of that operation. A heat is reversed through `CorrectionService` (`MARKET_TRIP_MANAGE` + `TRANSACTION_CORRECT`, reason mandatory) only while no later effective HEAT or LOGO RETURN of the same karat exists (and the balance stays non-negative); an issue cannot be reversed while a later effective heat of its LOGO karat exists; a reversed document stays readable. Supplier making payment from custody (`SUPPLIER_PAY` entry, migration 043, «دفع مصنعيات من العهدة»): the OWNER pays a supplier's making (EGP) with cash already in an OPEN custody. Custody cash = issued - returned - paid to suppliers (effective entries), the payment is capped at the outstanding custody cash, and no Main Safe or bank movement exists (Main Safe moves only at issue and return). It reuses the supplier SM document and numbering with method CUSTODY (no MONEY leg, one negative making movement, supplier gold untouched; an inactive supplier and a crossing into supplier credit are allowed) and links the entry to the payment (`market_trip_supplier_payments`). It needs MARKET_TRIP_MANAGE and SUPPLIER_MAKING_PAY inside the transaction (OWNER only), one request key and hash (the SM header and the custody entry both carry the key, so a reuse in any other flow conflicts). Its only correction is the full reversal of the custody entry (MARKET_TRIP_MANAGE + TRANSACTION_CORRECT + SUPPLIER_MAKING_PAY_REVERSE), which restores custody cash and the supplier's owed amount from the stored amount and does not return any money to Main Safe; the supplier-side reversal of a CUSTODY payment is refused. Sales, settlement of melt bars, re-melting, assay amendments, pricing and allocation are not implemented (supplier gold settlement of LOGO custody gold exists since 046).
- **Market-party roles (migration 044):** the Supplier Master (`suppliers`, `S…` codes, one identity per party) carries two role flags, `is_supplier` and `is_intermediary`, at least one required; existing rows are supplier-only. The user-facing name of the intermediary role is «مسوقجي» (the internal flag, filter value and identifiers stay `is_intermediary` / `INTERMEDIARY`). A supplier (alone or with the intermediary role) needs a supported active karat at creation (an existing supplier whose karat is still unresolved keeps that state; nothing is guessed). An intermediary-only party has no supplier-account karat and no "unresolved karat" warning; adding the intermediary role never changes or unlocks a supplier karat. Supplier invoices, making payments (direct and from custody) and the gold account require the supplier role, enforced by the services. The supplier role cannot be removed while any invoice (any status), payment or ledger row exists; an inactive supplier stays payable. Intermediary holdings are not implemented yet and will carry their own karat / fineness, never a dedicated account karat. Settlement, holdings, sales, bar allocation and reports are not part of this slice.
- **Direct supplier gold settlement (migration 045, `SGxxxxxx`):** the shop delivers available Scrap of a selected master karat to a SUPPLIER against its gold account. One transaction posts the immutable SG document, one COUNTED Scrap OUT of the delivered karat (the actual physical grams, held manager-review Scrap excluded) and one negative supplier gold movement in the supplier's dedicated karat. Credited grams = delivered grams x delivered karat / supplier karat; the POSTED credit is derived straight from the high-precision value (Decimal, 0.001 g, ROUND_HALF_UP), the six-decimal analytical value and the rounding difference (posted - analytical, from the two stored figures) are stored with the original weight, both karats and the delivered weight. No cash, making, valuation, sale or purchase. OWNER and MANAGER may post (`SUPPLIER_GOLD_SETTLE`, rechecked in the transaction); the party needs the supplier role and a resolved karat; an inactive supplier may receive it and the payable may cross zero into supplier credit. One request key and hash cover the payload. Reversal is OWNER-only (`SUPPLIER_GOLD_SETTLE_REVERSE` + `TRANSACTION_CORRECT`, reason mandatory) through CorrectionService, restores the STORED delivered grams to Scrap and the STORED credit to the supplier (never recalculated), and the document, the supplier gold ledger and the Scrap movement derive the reversed state from that one correction record. The supplier gold ledger labels these movements «تسوية ذهب مورد» / «عكس تسوية ذهب مورد» by movement code at read time (the stored English type names are never rewritten). Lock order: supplier -> Scrap karat. The direct flow's source_kind is SHOP_SCRAP; settlement from market custody (046) is the separate bullet below. Intermediary holdings are not implemented; whole assayed custody-lot settlement is implemented by 047.
- **Supplier gold settlement from market custody (migration 046, `SGxxxxxx` + `MTxxxxxx-n`, «تسوية ذهب من العهدة»):** the OWNER delivers LOGO gold that is already in an OPEN custody to a SUPPLIER against its gold account (only LOGO in this slice; MELT and unclassified gold are outside it, which is a scope of the slice and not a permanent prohibition). One transaction posts a `GOLD_SETTLE` custody entry, the immutable SG document (source `CUSTODY_GOLD`: delivered physical grams and master karat, supplier karat, posted credit, analytical value and rounding difference, with the 045 conversion and rounding rule), the supplier's negative gold movement in its dedicated karat, its GOLD leg, the entry-settlement link (`market_trip_gold_settlements`) and the audit rows. NO second Scrap movement exists (the grams left the shop once, at issue) and no cash, making, bank or lot movement: the database refuses a SCRAP leg on any source but SHOP_SCRAP and keeps the link equal to the document in source, supplier, karat and weight. One source line and one supplier per document; delivery is capped at the effective outstanding of that (karat, LOGO) line; an inactive supplier and a crossing into supplier credit are allowed. Custody gold = issued - returned - melted + effective heating differences - delivered to suppliers, defined ONCE in `services/custody_rules.py` and used by posting, return checks, closure and the correction guards. Posting needs `MARKET_TRIP_MANAGE` and `SUPPLIER_GOLD_SETTLE`, rechecked in the transaction; lock order trip -> supplier; one request key and hash cover the payload (the SG header and the custody entry both carry it). Reversal is only through the custody entry (`MARKET_TRIP_MANAGE` + `TRANSACTION_CORRECT` + `SUPPLIER_GOLD_SETTLE_REVERSE`, reason mandatory, trip OPEN): the entry stops counting, so the stored delivered grams are back in custody, and the stored credit is restored to the supplier; the entry, the SG document and the supplier ledger derive the reversed state from that one correction. The supplier-side (045) reversal refuses a custody-sourced document with guidance naming the custody entry, and an issue or heat cannot be reversed while a later effective settlement of its LOGO karat exists. Whole-bar delivery of assayed lots, multi-supplier allocation and intermediary (مسوقجي) holdings are NOT implemented; unknown fineness is never inferred.
- **22K support (migration 038):** 22K is a normal master karat and a supplier may be dedicated to 18, 21, 22 or 24 (its account stays
  in PHYSICAL grams of that karat, no conversion). The price provider contract stays exactly 18/21/24; `GoldPriceService` publishes
  a derived 22K quote beside them, in the SAME group and activation: buy22/sell22 = the stored approved 24K price * 22 / 24, `Decimal`,
  quantized to 0.0001 `ROUND_HALF_UP` (MANUAL derives from the already rounded 24K, which itself derives from 21K). It is stored as
  `DERIVED` with `derived_from_quote_id` naming that exact 24K quote (invoice snapshots keep their `quote_id`, hence the provenance),
  is never derived at read time, and a 22K row that is not exactly the derivation of its own group's 24K row is ignored (22K then
  has no price: `PRICE_MISSING`; another karat's price is never substituted). A legacy three-karat active set stays usable; AUTO
  republishes unchanged provider prices once if the set lacks 22K; MANUAL gains 22K at the next normal OWNER activation.
- **Supplier purchase invoice reversal (migration 052, `RVxxxxxx`, source `SUPPLIER_PURCHASE_INVOICE`, «عكس فاتورة مورد مرحّلة»):** a POSTED supplier purchase invoice with NO intaken piece is fully reversed through `CorrectionService` (full reversal only, reason mandatory). OWNER only: `SUPPLIER_INVOICE_REVERSE` plus `TRANSACTION_CORRECT`, both rechecked in the transaction (the actor is the session user). One transaction posts the exact opposite of the STORED posting movements (never recomputed): one GOLD movement in the invoice's supplier karat (physical grams, the Eq21 compatibility field negated) and, when the invoice had making, one MAKING movement, both under the new movement type `PURCHASE_INVOICE_REVERSAL`, plus the correction row and its audit event. The invoice, its groups and its original ledger movements are never touched; the stored status stays POSTED and the reversed state is DERIVED from the correction record (list, detail, ledger labels «عكس فاتورة شراء»). Any intaken piece (a real inventory item with its own identity and barcode) refuses the reversal with `INVOICE_HAS_INTAKE` (no cascade); intake on a reversed invoice is refused (`INVOICE_REVERSED`). Lock order invoice -> supplier -> pieces (a CURRENT read under the invoice lock, the same invoice-first order as posting and intake). A second reversal is `ALREADY_REVERSED` (`uq_correction_one_reversal`); the supplier balance may cross zero into supplier credit; no inventory, Scrap, cash, bank, making-payment or other-supplier effect exists. Editing a posted invoice, partial reversal, reversing intaken pieces and supplier returns / debit notes are NOT implemented.
- **Sale of custody gold (migration 053, `MTxxxxxx-n` entry kind `GOLD_SALE`, «بيع ذهب من العهدة»):** the OWNER sells gold that is already in an OPEN custody for cash at a price agreed BY HAND. Sources: an aggregate (karat, designation) line of ANY designation (partial grams, never above the effective outstanding) or ONE WHOLE known-assay lot (SALE_OUT custody lot movement; unknown assay and partial outstanding are refused). The sold grams leave the custody and the agreed TOTAL (EGP, two decimals, authoritative; a per-gram price is multiplied once and rounded HALF_UP) ENTERS the custody CASH (custody cash = issued - returned - paid to suppliers + sale proceeds, defined once in `services/custody_rules.py`); the cash stays in custody (no `cash_movements`, `bank_movements` or Main Safe movement) until a normal return or a supplier payment from custody. A sale NEVER writes a supplier gold / making / settlement / payment ledger, Scrap, inventory or shop-lot movement and is not a settlement: the buyer is only an identity reference (`REGISTERED_PARTY` = any `suppliers` row, which must be ACTIVE and needs no role, or `THIRD_PARTY` = a typed name); the name is stored as a snapshot. Soft price warning: a total whose deviation from the SHOP_BUY reference (master karat price, or pure grams x the 24K price for a lot) exceeds 10% is refused with `PRICE_DEVIATION` until the owner acknowledges it; the reference, deviation and acknowledgement are stored. Right: `MARKET_TRIP_MANAGE` rechecked in the transaction; one request key and hash, exact replay returns the original. The only correction is a full reversal through `CorrectionService` on the entry (`MARKET_TRIP_MANAGE` + `TRANSACTION_CORRECT`, reason mandatory, trip OPEN): the gold (or the same lot) is back in custody and the stored proceeds leave the custody cash; it is blocked by arithmetic when the proceeds were already returned or paid. The sale document is never rewritten. UI: «بيع ذهب من العهدة» dialog in `qml/MarketTripsPage.qml`. Migration 053 was reportedly applied to `masgold_rehearsal` and `masgold_v2` (see "Schema application status").
- **Cash gold purchase (migration 054, entry kind `GOLD_BUY`, «شراء ذهب نقدي»):** the OWNER turns custody CASH into custody GOLD by buying scrap in the market. One transaction posts the entry, the immutable purchase document (`market_trip_gold_buys`) and its scrap lines (`market_trip_gold_buy_lines`: karat, destination MELT or LOGO - never UNCLASSIFIED, actual grams, agreed line price; the total is the exact sum of the lines). Custody cash = issued - returned - paid to suppliers + sale proceeds - purchases and custody gold = ... + purchased, both defined once in `services/custody_rules.py`; the total may not exceed the custody cash (`PURCHASE_EXCEEDS_CASH`). No `cash_movements` / `bank_movements` / Main Safe, Scrap, supplier gold / making / settlement / payment, inventory or lot movement is posted (the gold reaches the shop Scrap only through a normal custody return; LOGO gold can later be delivered to suppliers through the existing custody settlement). The VENDOR is MANDATORY and immutable: an ACTIVE registered party (any role, no role granted) or a typed name, stored as a name snapshot plus a normalized key (`vendor_name_key`) for KPI grouping; database CHECKs refuse a blank name and a wrong party link. A typed vendor can be registered as a new active SUPPLIER party from the dialog (the owner names its account karat; it delegates to `SupplierService`). Soft price warning like the sale: a total more than 10% away from the SHOP_BUY reference of the bought karats is refused with `PRICE_DEVIATION` until the owner acknowledges it (reference, deviation and acknowledgement are stored). Right: `MARKET_TRIP_MANAGE`; one request key and hash. The only correction is a full reversal through `CorrectionService` (`MARKET_TRIP_MANAGE` + `TRANSACTION_CORRECT`, reason mandatory, trip OPEN), blocked by arithmetic when the bought gold was already used. Migration 054 was reportedly applied to `masgold_rehearsal` and `masgold_v2` (see "Schema application status").
- **Gold position / موضع الذهب (manager reviews, read only, no migration):** `services/gold_position_service.py` → `GoldPositionService.position()` shows ALL shop-owned gold in one REPEATABLE READ read-only transaction: Scrap, whole lots in the shop, legacy receipt stock, finished inventory (IN_STOCK) and returned pieces awaiting inspection, EVERY market trip's outstanding gold / lots (`MarketTripRepository.all_trip_ids()` + the display-only `custody_gold_by_trip()` / `custody_lots_by_trip()`: no list limit, a fixed number of aggregate queries, NO locking reads - the locking `custody_gold` / `custody_lots` stay the reads of posting and correction decisions; the single-snapshot property is the design, not yet verified on MySQL), and intermediary holdings (lot and LOGO pools plus each marketer account's 21K grams from `marketer_gold_movements`, read through the same connection and listed as a source of its own). Marketer CASH is never part of it. 21K base: master karat = grams x K / 21; assayed lot = grams x fineness / 1000 x 24 / 21; an unknown assay is NEVER inferred (listed in physical grams, outside the 21K total). Supplier payables/receivables are never read or netted. Rights (all three, rechecked in the transaction): `MANAGEMENT_REVIEW` + `MARKET_TRIP_MANAGE` + `INTERMEDIARY_HOLDING_VIEW`. UI: a section and card inside `qml/ManagementReviewPage.qml` (`reviewsVM.goldPosition`), not a sidebar item.
- **BOOK mode REMOVED (2026-11-01, owner decision; supersedes every BOOK bullet that used to be here and every remaining mention of BOOK below):** the BOOK accounting mode (market-trip book core, reconciliation preview/settlement, Phase 2 market cycle, Phase 3 gain/closure/report) no longer exists in code: `services/book_rules.py`, `book_reconciliation.py`, `book_settlement.py`, `market_trip_book_service.py`, their repository methods, view-model slots, QML dialogs and tests were deleted. Every market trip is a plain custody (`MarketTripService`). The pure 21K-equivalent math that other code still needs lives in `services/eq21_math.py`. Database history stays untouched: migrations 056-059, the `accounting_mode` column, the `market_trip_book_*` tables, the `BK_*` entry kinds and `TRIP_BOOK_IN/OUT` marketer movement types (still used by `ST_BAR_MKT`) remain as history; `position_snapshots.gold_book_eq21` is still written, always 0.000. Do not rebuild BOOK. Later text that says BOOK is 'inactive', 'prepared' or 'Phase N' is historical.
- **Basic reports (packet26, read only, no migration):** `services/report_service.py` -> `ReportService` (`run` / `export_csv`) over `repositories/report_repository.py` -> `ReportRepository` (plain SELECTs, no lock, one statement per read, no query per row; nothing is computed in SQL beyond per-row aggregates that mirror existing screens), `app/viewmodels/reports_viewmodel.py` (`MasGold.Reports`, registered by an import side effect of `customer_picker_viewmodel`, so `main.py` is NOT edited; the QML-created view model receives the real `AppSession`) and `qml/ReportsPage.qml` (the former placeholder page, index 5, nav button visible when the user may open at least one report). Six reports: sales and returns (documents by their own effective date; sale value, old-gold purchase value, return value, exchange difference, collected, paid and account effect are SEPARATE figures; reversed documents stay listed with RV code and date and are totalled apart from effective ones; "sales minus returns" is labelled not-profit; no cost / profit / stock valuation exists), available stock (counts and weights per karat, never added across karats; barcode search; the last linked invoice opens the existing invoice screen), gold movements (one ledger per source and unit - Scrap by karat, pieces, assayed stock, BOOK posted 21K-equivalent, marketer 21K entitlement - never one combined total), customers (balance now, period opening / new debt / collections / returns / closing from the customer ledger), suppliers (gold in each supplier's OWN karat, making in EGP, never converted to 21K, ledger movements with sources and reversals), treasury (per cash / bank account opening, in, out, internal transfers apart, closing; internal transfers are neither income nor expense). Rights: `REPORTS_VIEW` plus the domain view right of each report (treasury: `CASH_VIEW` and / or `BANK_VIEW`; the BOOK / assayed / marketer gold sources need `MARKET_TRIP_MANAGE` / `INTERMEDIARY_HOLDING_VIEW`), re-checked in the transaction. One REPEATABLE READ read-only snapshot per report; every total is computed over the WHOLE filtered result (the page only slices the detail); a result above 50,000 rows is refused, a missing table is shown as unavailable, a failed read is a failure (never an empty report). `export_csv` writes every row (UTF-8 with BOM). The BOOK period attribution of a REVERSED reconciliation / gain is still an open owner decision: the BOOK source shows the CURRENT balance after the effective reconciliations (source balance B, -L and -r apart, merged and validated by `gold_position_service.merge_reconciliations`; a record problem is an error, never a zero) and, separately, the period's documents with their CURRENT reversal state (no historical opening / closing balance is claimed). The sections of a report are decided from the CURRENT database rights (`_Gate`, never the session's older context) before their data is read; a search narrows movements and their totals but never an account balance; the filters applied are printed on screen and in the CSV; legacy invoices keep unknown values unknown («غير معلوم», totals marked partial) and never turn NULL into 0. The marketer statement's BOOK trip legs now open the trip page. Not implemented: profit, cost, stock valuation, printing of reports. Owner review of the reports screen (2026-10-20): the «معنى الأرقام» box is removed from every report (the CSV still carries the meaning lines); the totals are a uniform grid of equal cards (`GridLayout` in `qml/ReportsPage.qml`); the word «فعّال» is not shown on a total (`clean_label` in `app/viewmodels/reports_viewmodel.py`, also in the CSV); and the sales report shows its TOTALS ONLY on screen (`HIDDEN_ON_SCREEN` hides the `documents` table; the service and the CSV still produce every row, so unknown legacy values stay visible there). Owner redesign of the gold report (2026-10-20): the report `gold` is now «حركة الأصناف» (renamed by the owner from «حركة الذهب») (the old five-source report with the BOOK / assayed / marketer sources was removed from the service and its tests): table 1 «حركة البيع حسب الصنف» = the pieces SOLD (`SELL_INVENTORY` lines of FINALIZED, not fully reversed invoices dated in the period) grouped by item type and karat (`ReportRepository.gold_sold_pieces`), filters karat / factory (dynamic options from the service) / weight range (`weight_min`, `weight_max`) / search, one card per karat with the WEIGHT on top and the piece count under it (returns are not netted: this is the sales movement); table 2 «شراء الكسر بالعيار» = counted `CUSTOMER_BUY_IN` minus `CUSTOMER_BUY_IN_REVERSAL` per karat (`scrap_purchases`) with the purchase count. Rights per section: pieces need `INVOICE_VIEW` + `INVENTORY_VIEW`, scrap needs `OLD_GOLD_VIEW` (a missing section is «غير متاح», never a zero; the report opens with either). The old repository reads of the removed sources (`book_*`, `marketer_gold`, `assayed_movements`, `piece_movements`, `scrap_movements`) are left in `ReportRepository` unused. Karat colors (owner review): a table or card that belongs to ONE karat carries `karat`, and `qml/ReportsPage.qml` colors it with the same `balanceKarat18/21/22/24` colors as the top bar (card border and value, table title and a color stripe, a gap between tables). Stock report: cards only (weight on top, count under it, then a row of Scrap cards). Gold movement refinement (owner review): the sales part is ONE TABLE PER KARAT (never grouped across karats), one row per item type inside it (count, grams), and every row has a «رسم بياني» button that opens a MONTHLY chart of that type (`ReportService.gold_chart`, `ReportsViewModel.showChart`, the chart popup in `qml/ReportsPage.qml`): calendar months of the report's period with empty months as zero bars, of that type AND that karat, under the SAME filters as the report (period, factory, weight range, search); it needs the invoice and inventory rights, re-read in the transaction. Customers report redesign (owner review): TWO cards (what WE owe the customers «إجمالي اللي علينا» in the payable tone, what THEY owe us «إجمالي اللي عليهم» in the receivable tone, the app's account colors) and ONE table of the OPEN ITEMS by customer and invoice/document (`ReportService.open_items`: the customer's ledger read oldest-first, a credit consumes the oldest open item because a settlement is not allocated to an invoice, so the rows of a customer add up to his real balance); filters: side (عليهم / علينا), inactive customers, search; the period dates do not apply (current state); a row opens its invoice or the customer; read through ONE statement (`ReportRepository.customer_open_ledger`). Dead Cash is its OWN report «الديون المعدومة (Dead Cash)» beside the customers (owner request; key `deadcash`, right `CUSTOMERS_VIEW`): cards («إجمالي الديون المعدومة» = ACTIVE write-offs, «عدد الديون المعدومة», a «شطب معكوس (غير محسوب)» card when one was reversed; amber tone) and ONE table of the write-offs (customer, date, amount, reason, state; a reversed write-off stays listed and is not counted); filters state / inactive customers / search; all-time (no period); read by `ReportRepository.customer_dead_cash`. The customers report itself no longer contains Dead Cash. Period and number order (owner review): the default period of every report is AUTOMATIC - the first day of the current month up to today (`month_period`, `ReportsViewModel.refreshPeriod`, re-evaluated at every read and when the page is shown) until the employee edits a date, after which it is never moved; amounts in cards AND tables read number-then-unit («4,000.00 ج», `cardValue` in `qml/ReportsPage.qml`: a value with an Arabic unit is not LTR-isolated). Suppliers report redesign (owner review): TOTALS per supplier - one table row per supplier with the gold account balance in HIS OWN karat («ذهب علينا / ذهب لنا», physical grams, never converted to 21K) and the making balance in EGP («مصنعيات علينا / لنا»), cards (making we owe / they owe in the payable / receivable tones, and one gold card per karat in the karat color), balances = the ledger sums up to the end of the report date; the movements table is NOT shown on screen (`HIDDEN_ON_SCREEN`) but stays in the service result and the CSV. Reports of the CURRENT state (stock, customers, Dead Cash, suppliers: `NO_PERIOD`) neither offer the period fields (`ReportsViewModel.periodUsed`) nor print a period line in the applied filters. Treasury report redesign (owner review): cards only - «المبيعات» and «مشتريات الذهب (الكسر)» of the period (the SAME figures as the sales report, need `INVOICE_VIEW`, left out without it), «الإيرادات» (money IN of the selected accounts) and «المصروفات» (money OUT), both WITHOUT the internal transfers, then the closing cash / bank balances and the all-accounts transfer proof; the movement-count / movement-sum cards were removed and the movements table is not shown on screen (`HIDDEN_ON_SCREEN`; the CSV keeps every movement); the accounts table stays. Market custody report (owner review): FOUR cards - «فاقد السبك», «خسية البوتقة», «خسية السوق» (each alone, in 21K-equivalent grams) and «إجمالي الفقد والخسية» in its own row; the summary table is not shown on screen (`HIDDEN_ON_SCREEN`), it stays in the service result and the CSV. Inventory alerts (owner request, 2026-10-21; key `alerts` «تنبيهات المخزون», right `INVENTORY_VIEW`, rule-based, NO AI, current state so no period): (1) DEAD STOCK = a piece IN_STOCK acquired `DEAD_STOCK_DAYS` = 180 days ago or earlier that was never on a non-draft invoice (a piece that came back from a sale is not dead), one table per KARAT by type and factory (count, weight, oldest age) with a colored card per karat; (2) ABOUT TO RUN OUT = per (karat, type, factory, size) sold at least `MIN_SALES` = 3 times in the last `VELOCITY_DAYS` = 90 days (reversed invoices excluded) whose stock / daily sales rate covers fewer than `LOW_COVER_DAYS` = 14 days («نفد» when none is left), needs `INVOICE_VIEW` (left as an unavailable table without it). (3) THE WEIGHTS (owner request, information only, no alert): one table per karat «الأوزان حسب الصنف — عيار K» with ONE-GRAM weight bands (0.01 – 1, 1 – 2, 2 – 3 ...; the lower limit is inside; `WEIGHT_BAND_TOP` = 10 and above in the last band) PER ITEM TYPE inside the karat (owner correction: bands by type and karat, never mixing types): stock count and weight, sales in the last 30 / 90 days and the days covered, so the owner decides by himself (the sales columns need `INVOICE_VIEW`). It only alerts: no price, no action. Planned LATER (owner): indicator-based gold buy / sell hints from the stored price history.
- **Gold price indicators (read only, migration 066 for the history):** report `indicators` «مؤشرات الذهب» (`ReportService._indicators` / `price_chart`, pure formulas in `services/price_indicators.py`, plain reads `ReportRepository.gold_price_daily` / `gold_price_history_legacy`; right: `REPORTS_VIEW` only, prices are public in the top bar). Per karat 24 / 21 / 18 (colored cards): latest SHOP_BUY / SHOP_SELL with change, 30-day average, position inside the last 90 days, daily volatility and a hint (SHOP_BUY in the lower quarter = buying relatively favourable, SHOP_SELL in the upper quarter = selling relatively favourable) always printed with «إرشاد مبني على حركة أسعار المحل المخزنة وليس توصية مالية»; a figure without enough history is «بيانات غير كافية», never invented; no AI and no external formula. Karat 21 has a long series: the old shop's `goldprice` table cleaned by `services/price_history_rules.py` (zero rows rejected, buy>sell swapped, spread above 15% rejected, 3x-median outliers rejected, last entry of the day wins) and imported once into `gold_price_history_legacy` by `tools/import_golddb_prices.py` (insert-only, refuses a non-empty table); legacy days stop the day before the first live day (`merge_series`). The «رسم بياني» button opens a daily chart popup (buy / sell / 30-day average; 30 / 90 / 365 days / all). Migration 066 and the import were applied to `masgold_rehearsal` ONLY (399 daily rows from 949 old rows; 23 rejected, 49 swapped); DEV `masgold_v2` was migrated to 67 and the import applied on 2026-10-22 (see the profit bullet). Tests: `tests/test_price_indicators.py`, `tests/test_price_history_rules.py`, `tests/test_report_service.py` (IndicatorTests), `tests/test_reports_ui.py` (real click), opt-in `tests/test_report_indicators_mysql.py`.
- **Gold profit and position snapshots (migration 067, applied to `masgold_rehearsal` ONLY, level 66 -> 67, rows unchanged; DEV `masgold_v2` was brought from 65 to 67 on 2026-10-22 at the owner's request (logical backup `masgold_v2-before-066-067.sql` taken first, rows unchanged: 21 trips, 76 entries, 16 invoices, 2 customers) and the cleaned old prices were imported (399 daily rows, 2024-01-26 .. 2026-10-21); opt-in real-MySQL test `tests/test_position_snapshots_mysql.py` 2/2, writes inside a rolled-back transaction):** report `profit` «ربح الذهب (مكافئ 21)» (`ReportService._profit`, owner only: `REPORTS_VIEW` + `MANAGEMENT_REVIEW` + `MARKET_TRIP_MANAGE` + `INTERMEDIARY_HOLDING_VIEW`, with a period). The owner's definition: profit = the gold at the END of the period minus the gold at its START, in 21K-equivalent grams (consolidated gold position: stock, Scrap, bars, market custody, intermediary / marketer holdings, BOOK trips; PLUS the net supplier gold: what suppliers hold for the shop counts as shop gold, what the shop owes is subtracted). Cash and bank are shown beside it for information and are NOT inside the profit; the profit also reflects any gold change (purchase with cash, withdrawal). The two figures come from stored STOCKTAKING SHEETS (`position_snapshots`, insert-only, `services/position_snapshot_rules.py` pure, `services/position_snapshot_service.py`, `repositories/position_snapshot_repository.py`), never from a recomputed history: ONE `OPENING` sheet (the owner's initial balance, entered ONCE from Settings «الرصيد الافتتاحي وجرد الذهب», needs `SETTINGS_MANAGE` + the three position rights, refused a second time by the service and by a unique generated column) and `DAILY` sheets taken the first time an authorised user opens the real application each day (never on offscreen / test platforms, never before the initial balance exists) plus `MANUAL` sheets («جرد الآن»). A boundary is stood for by the first sheet of that calendar day, otherwise the latest earlier sheet; a period before the first sheet uses it and says so; one sheet only is «غير متاح», never a zero profit. UI: `PositionOpeningSettings` card in `qml/Main.qml`, `app/viewmodels/position_snapshot_viewmodel.py`, wired in `main.py` (`_attach_position_snapshots`). Tests: `tests/test_position_snapshot_rules.py`, `tests/test_position_snapshot_service.py`, `tests/test_position_snapshots_schema.py`, ProfitTests in `tests/test_report_service.py`, real-click `tests/test_position_opening_ui.py`.
- **Employee KPI report and income / expense report (2026-10-22, read only; migration 068 widens one CHECK):** (1) report `employees` «أداء الموظفين» (`ReportService._employees`, `ReportRepository.employee_lines` / `employees`, right `INVOICE_VIEW`, with a period, filters employee (dynamic options, 0 = «غير محدد») and karat): per employee (the invoice's `employee_id`) one table with one row per KARAT (never added across karats): grams sold (`SELL_INVENTORY`, `SELL_OLD_GOLD`, `SELL_UNIDENTIFIED` lines of FINALIZED invoices, fully reversed invoices excluded and counted apart, returns not netted), the weighted average MAKING per gram (sum of `making_total` / weight of the lines whose making is KNOWN; unknown making is stated in the table note, never zero), the grams of scrap bought (`BUY_OLD_GOLD`) and its weighted average price per gram (line total / weight). The old shop's `employeebill` history was deliberately NOT imported (no karat in it); the five employees of `golddb.employee` other than «علي» (already EMP001 «على») were added to DEV `employees` as EMP002-EMP006 on 2026-10-22 at the owner's request. (2) report `cashflow` «المصروفات والإيرادات» (`ReportService._cashflow`, `ReportRepository.cashflow_movements`, rights `CASH_VIEW` and / or `BANK_VIEW`, a missing ledger is stated and left out): every cash / bank movement of the period named by what posted it and NETTED per name (a reversal or correction lands on the SAME name as its original: it reads `transaction_corrections.source_type`, and for a reversed manual operation its category); money that only changes place (transfers, market custody, the marketer's cash) is a separate table and is neither income nor expense; opening balances are ignored; cards: recorded expenses / recorded income (the categorised manual entries), total in, total out, net. It is NOT profit (no cost). (3) The treasury «حركة نقدية جديدة» (`FinancialAccountService.cash_movement`, 025 / 026 already categorised manual entries) gained five cash-OUT categories by migration 068: UTILITIES «كهرباء ومياه», TRANSPORT «نقل», MAINTENANCE «صيانة», HOSPITALITY «بوفيه وضيافة», STAFF_ADVANCE «سلف موظفين» (existing: EXPENSE, SALARY, RENT, REPAIR, REVENUE); only `chk_fo_category` is widened (applied to `masgold_rehearsal` and, after a logical backup, to DEV `masgold_v2` on 2026-10-22: level 68, rows unchanged), its rollback refuses while one is in use; the list is now MANAGED in Settings «تصنيفات المصروفات والإيرادات» (migration 069, applied to `masgold_rehearsal` and, after a logical backup, to DEV `masgold_v2` on 2026-10-22: level 69, rows unchanged): table `cash_categories` (code, Arabic name, direction IN / OUT / BOTH, active, system) seeded with these ten, `financial_operations.category` references it by foreign key (the fixed CHECK list is gone; the service decides from the table that a category is active and fits the deposit / withdrawal direction); the owner (`SETTINGS_MANAGE`, rechecked in the transaction, audited `CASH_CATEGORY_*`) can ADD (generated code `C`+12 hex), RENAME (history shows the current name), DISABLE / ENABLE and DELETE only a category no operation ever used (a used one is disabled instead; the foreign key refuses its deletion). A database below 069 keeps the fixed list (`financial_account_service.category_table(None)`). Code: `services/cash_category_service.py`, `repositories/cash_category_repository.py`, `app/viewmodels/cash_category_viewmodel.py`, the `CashCategorySettings` card in `qml/Main.qml`; tests `tests/test_cash_categories.py`, `tests/test_cash_categories_schema.py`, real-click `tests/test_cash_categories_ui.py`, opt-in real-MySQL `tests/test_cash_categories_mysql.py` 3/3 on `masgold_rehearsal` (level 69). Tests: `tests/test_report_service.py` (EmployeeKpiTests, CashflowTests), `tests/test_cash_screen.py`, `tests/test_expense_categories_schema.py`, opt-in real-MySQL `tests/test_report_staff_cashflow_mysql.py` 3/3 on `masgold_rehearsal` (level 68).
- **Making cost per category and the making-profit report (2026-10-22, migration 070 applied to `masgold_rehearsal` and, after a logical backup, to DEV `masgold_v2`: level 70, rows unchanged):** the owner sets the actual making COST per gram of each category letter (A B C D L T), ONE number for every karat, in Settings «تكلفة المصنعية للحروف» (`item_category_costs`, `services/category_cost_service.py`, `repositories/category_cost_repository.py`, `app/viewmodels/category_cost_viewmodel.py`, the `CategoryCostSettings` card; `SETTINGS_MANAGE` rechecked in the transaction, audited `CATEGORY_MAKING_COST_SET/CLEARED`). Stock entry FREEZES it: `InventoryService._create_item` writes the category's current cost into `inventory_items.acquisition_making_per_gram` unless the caller gave a real acquisition making (supplier intake keeps its own rate); a later change of the cost never rewrites a piece. Report `makingprofit` «ربح المصنعية» (`ReportService._makingprofit`, `ReportRepository.making_profit_lines` / `category_costs` / `item_categories`, rights `INVOICE_VIEW` + `INVENTORY_VIEW`, with a period, filters letter and karat): for every identified piece SOLD (`SELL_INVENTORY` lines of FINALIZED, not fully reversed invoices) the margin = making charged on the line (`making_total`) - cost (stored piece cost x weight, rounded once to 0.01); a piece with NO stored cost is ESTIMATED from the category's CURRENT cost and its margin is shown apart («منه تقديري», owner decision: a temporary estimate, no data rewritten); a piece with no cost anywhere, or a sale whose making is unknown (legacy), is counted in «قطع بدون حساب» and left out of the margin, never turned into zero. One table per karat (never mixed), rows per letter. SALES ACTIVITY ONLY (owner decision): returns are not netted; F5 / F7 sales without a piece are not in it. A database below 070 still reports with the stored piece costs. Tests: `tests/test_report_service.py` (MakingProfitTests), `tests/test_category_costs.py`, `tests/test_category_costs_schema.py`, real-click `tests/test_category_costs_ui.py`, opt-in real-MySQL `tests/test_making_profit_mysql.py` 2/2 on `masgold_rehearsal` (level 70).
- **Scrap cost report (2026-10-22, read only, no migration):** report `scrapcost` «تكلفة الكسر» (`ReportService._scrapcost`, `ReportRepository.scrap_cost_lines`, right `INVOICE_VIEW`, with a period, filter karat): per KARAT (never added across karats) the grams of scrap bought from customers (`BUY_OLD_GOLD` lines of FINALIZED, not fully reversed invoices) and the value paid (the line total, before the invoice's final EGP-5 rounding), as the weighted average price per gram for the PERIOD and cumulatively from the first stored purchase to the end of the period, with the latest stored SHOP_BUY price of that karat (`gold_price_daily`) and the difference (period average minus that price: positive = paid more than today's buying price). A coloured card per karat. It is a price comparison, NOT a stock valuation (the owner dropped stock valuation). Resale margin from market-custody sales is NOT in this version (a melted bar changes karat and loses weight, so it would be an approximation). Tests: `tests/test_report_service.py` (ScrapCostTests), opt-in read-only `tests/test_scrap_cost_mysql.py` 2/2 on `masgold_rehearsal`.
- **Demand, seasonality and the sales line chart (2026-10-22, migration 071 applied to `masgold_rehearsal` and, after a logical backup, to DEV `masgold_v2`: level 71, rows unchanged):** `sales_history_legacy` is the old shop's `employeebill` imported ONCE as a DAILY history by `tools/import_golddb_sales.py` (`report` / `apply rehearsal|dev`; rules in `services/sales_history_rules.py`: one bill = the row with the highest id, a bill without a usable weight counts as a bill and adds no grams - the 2024 weights are unknown; 5253 bills, 689 days, 38,944 g, weights known only from February 2025; NO karat in the old data, so the series is every karat together; insert-only, rollback refuses while it holds rows). Report `demand` «الموسمية والطلب» (`ReportService._demand` / `demand_chart`, `services/demand_forecast.py` = standard library only, no AI library installed or needed, right `INVOICE_VIEW`, no period): the sold grams per month (the old history up to the day BEFORE the owner's initial balance, then the new system's sale lines from that day; without an initial balance the old history alone), the year-over-year change, a seasonal index per calendar month (each month divided by ITS OWN year's mean so the shop's growth does not pose as seasonality; needs two years with at least six usable months), a 3-month forecast (the last six months de-seasonalised, times the target month's index; confidence «متوسطة» with an index, «منخفضة» without; none below six usable months) and the association between the monthly 21K selling price and the grams measured on MONTH-TO-MONTH CHANGES (slope, Pearson r with a plain strength word, n; none below eight pairs). It is an explainable statistic, not a promise: the unfinished month is never used. The line chart (`reportDemandChartDialog`, Canvas) shows monthly or ISO-weekly sold grams over the year, ONE LINE PER YEAR the owner toggles (up to six, newest gold; default the latest year with data and the one before; unfinished or unknown periods are gaps, never zeros). Tests: `tests/test_demand_forecast.py`, `tests/test_report_service.py` (DemandTests), `tests/test_sales_history_schema.py`, real-click `tests/test_reports_ui.py`, opt-in real-MySQL `tests/test_demand_mysql.py` 2/2 on `masgold_rehearsal`. Seasons, weekday and month-part patterns (`services/season_patterns.py`, standard library only; the same report): from the DAILY grams (days from the first usable weight to yesterday; a day without a bill is a zero only when at least 40% of the days have a bill, otherwise the history is treated as missing data and nothing is produced) the report adds the weekday pattern, the pattern of the four parts of the month and the effect of Ramadan, the week BEFORE each Eid, the Eid week and the three weeks AFTER it (Fitr and Adha) and Mother's Day: the mean daily grams of the window / the mean of the 35 days before and after it leaving out every season window; a year whose window or surroundings are not fully inside the usable days is skipped; the Islamic dates come from the tabular calendar (checked within a day of the observed Eids, windows are a week or more) so they are approximate by design. With two years of data a season has at most two observations, so the table shows each year beside the mean (on the owner's data the weeks AFTER an Eid sell about 1.2 times the surrounding days in both years while the weeks before and during it are not higher). Tests: `tests/test_season_patterns.py`, DemandTests in `tests/test_report_service.py`.
- **Minimum stock levels / reorder alerts (2026-10-22, migration 072 applied to `masgold_rehearsal` and, after a logical backup, to DEV `masgold_v2`: level 72):** the owner sets, in Settings «الحد الأدنى للمخزون» (`stock_min_levels`, `services/stock_min_level_service.py`, `repositories/stock_min_level_repository.py`, `app/viewmodels/stock_min_level_viewmodel.py`, the `StockMinLevelSettings` card; `SETTINGS_MANAGE` rechecked in the transaction, audited `STOCK_MIN_LEVEL_SET/REMOVED`), how many pieces (whole number from 1) and / or grams (positive, three decimals at most) of an item TYPE of a KARAT to keep; one rule per (type, karat), saving again replaces it. The inventory alerts report (`alerts`) gained the table «تحت الحد الأدنى للمخزون» (type, karat, what is in stock, the minimum, the shortage; a type with no stock is short by the whole minimum) and a card; the karat filter applies, a text search never hides a shortage; no rules or a database below 072 add nothing. A rule only alerts: nothing is ordered. Tests: `tests/test_stock_min_levels.py`, `tests/test_stock_min_levels_schema.py`, real-click `tests/test_stock_min_levels_ui.py`, opt-in real-MySQL `tests/test_stock_min_levels_mysql.py` on `masgold_rehearsal`.
- **Temporary cash advances / custody (2026-10-23, migration 074 PREPARED, NOT applied to any database):** «سلف وعهد مؤقتة» in the treasury (`FinancialAccountService.open_advance` / `repay_advance` / `advances`, `FinancialAccountRepository` advance methods, `FinancialAccountsViewModel`, dialog `cashAdvancesDialog` in `qml/FinancialAccountsPage.qml`). Cash leaves the safe through the ordinary manual withdrawal on a NAME: an employee (category `STAFF_ADVANCE`) or a customer's temporary custody (`CUSTOMER_CUSTODY`), tied by `cash_advances` (`SA` + 6 digits, exactly one employee OR one customer, one withdrawal operation) in the SAME transaction; a repayment (`ADVANCE_REPAY`, partial allowed, never above what is owed, into the safe or a bank / wallet account) is `cash_advance_repayments` tied to its own deposit. The outstanding amount is DERIVED (amount - repayments whose operation is not reversed; a reversed withdrawal voids the advance), never stored. The customer ledger is NOT touched (a separate account, not the customer balance). The plain «حركة نقدية جديدة» refuses the three categories (`USE_ADVANCE`) and no longer offers them. An advance with an effective repayment cannot be reversed (`ADVANCE_HAS_REPAYMENTS`, `CorrectionService._plan`); a repayment can. The cash flow report treats all three as money that returns (the «غير الإيراد والمصروف» table), never an expense or income. `tools/attach_advance.py` links an OLD «سلف موظفين» withdrawal (no employee) to an employee after 074 is applied (prod / `masgold_import_*` only, insert-only). Tests: `tests/test_cash_advances.py`, `tests/test_cash_advances_schema.py`, the real-click dialog test in `tests/test_financial_accounts_qml.py`, CashflowAdvanceTests.
- **Automatic barcode and correcting a piece's type / karat (2026-10-23, no migration):** every piece that enters stock now gets a barcode: the typed one, otherwise the next canonical NNYYMMDD value (`InventoryService._create_item` -> `BarcodeRepository.allocate_barcode_value`; supplier intake «بدون باركود» is automatic too). `tools/assign_missing_barcodes.py` (prod / `masgold_import_*`, insert-only) gives the pieces entered before this a barcode. `InventoryService.correct_item` (`INVENTORY_MANAGE`, rechecked in the transaction, mandatory reason, audited `INVENTORY_ITEM_CORRECTED` with before / after) changes the item type and karat of a piece that is still IN_STOCK only (never sold / returned / removed); weight, barcode and movements stay; button «تعديل الصنف والعيار» in the piece details of `qml/Main.qml`. `InventoryService.convert_item_to_scrap` (INVENTORY_MANAGE + OLD_GOLD_MANAGE, mandatory reason, audited `INVENTORY_ITEM_TO_SCRAP`) turns a spoiled IN_STOCK piece into Scrap in one transaction: status SCRAPPED, inventory movement SCRAPPED, counted old-gold IN `STOCK_SCRAP_IN` of its exact karat and weight (button «تحويل إلى الكسر»; no migration, no way back from there). Tests: `tests/test_inventory_stock.py`, `tests/test_inventory_stock_qml.py`.
- **Scrap adjustment by the manager and weight correction (2026-10-23, migration 075 PREPARED, NOT applied to any database):** «تعديل رصيد الكسر» in مراجعات المدير (`services/old_gold_adjustment_service.py`, `repositories/old_gold_adjustment_repository.py`, `app/viewmodels/old_gold_adjustment_viewmodel.py`, `scrapAdjustDialog` in `qml/Main.qml`): right `OLD_GOLD_ADJUST` (existing since 004, rechecked in the transaction; 075 grants it to MANAGER and OWNER), one karat, increase or decrease, mandatory reason; posts one counted `ADJUSTMENT_IN` / `ADJUSTMENT_OUT` movement (reference_type MANUAL_ADJUSTMENT, request key in `movement_group_id`, audited `OLD_GOLD_ADJUSTED`); a decrease can never exceed the AVAILABLE Scrap (held grams excluded), the history is never rewritten. `InventoryService.correct_item` also corrects the WEIGHT of an IN_STOCK piece (the Scrap it came from is corrected separately with this adjustment). Tests: `tests/test_old_gold_adjustment.py`, `tests/test_old_gold_adjust_manager_schema.py`.
- **Borrowed cash (2026-10-23, migration 076 applied to `masgold_rehearsal` and DEV `masgold_v2`; 077 widens `cash_advances.kind` to VARCHAR(12) for THIRD_PARTY, found by a real-MySQL insert):** «سلفة مستلفة»: the shop takes cash from an employee, a customer or an outside person (typed name, kind THIRD_PARTY) and owes it back, no interest. `FinancialAccountService.open_borrow` posts a manual DEPOSIT (category `BORROWED_CASH`) into the chosen cash / bank account and ties it in the same transaction to a `cash_advances` row with `direction = 'BORROW'` (076 adds `direction`, default LEND for every old row, and `lender_name`); `repay_advance` follows the advance's direction (a borrow is repaid by a manual WITHDRAWAL `BORROW_REPAY`, never above what the shop still owes, partial allowed). Same derivation, reversal rules and report treatment as the advances above (money that returns, never income or expense); the customer account card lists a customer's borrowed cash apart from his custody («سلفة منه (علينا)»), never inside his balance. UI: «سلفة مستلفة» in the advances dialog of the treasury. Tests: `tests/test_cash_advances.py` (BorrowedCashTests), `tests/test_borrowed_cash_schema.py`, real-click test in `tests/test_financial_accounts_qml.py`.
- **Supplier custody settlement weight increase (migration 079, prepared in source only):** when a `CUSTODY_GOLD` source with designation `UNCLASSIFIED` is short of the requested physical delivery weight, the OWNER may record the measured increase to the requested total from available shop Scrap or as an evidenced increase with no Scrap movement. The service derives only the deficit under the custody lock; `ST_ADJUST` records the source, reason and exact added grams, the ordinary supplier settlement still rechecks availability under its own locks, and canonical reversal removes an unsourced increase without inventing a Scrap movement. No other custody designation or source is eligible. Migration 079 has not been applied to DEV, rehearsal or production.
- **Stocktake write-off (2026-10-22, migration 073 PREPARED, NOT applied to any database):** after the barcode stocktake (الجرد بالباركود) the OWNER / MANAGER (`STOCKTAKE_ADJUST`, rechecked in the transaction) may approve it: every piece of the counted scope that was not scanned leaves the stock (`inventory_items.inventory_status = 'REMOVED'`, movement `STOCKTAKE_WRITE_OFF`, nothing deleted, no restore button) with a mandatory reason (SOLD_OUTSIDE / MISSING / DATA_ERROR / OTHER + note) and one explicit confirmation; at least one piece must have been scanned. `services/stocktake_adjust_service.py` locks every IN_STOCK piece of the scope and requires scanned + remaining to be EXACTLY that set (else `STOCK_CHANGED`: count again); the reconciliation (expected = found + written off, count and grams, per karat) is stored in `stocktake_adjustments` (`SKxxxxxx`) / `stocktake_adjustment_items`, audited, idempotent on a request key. UI: «اعتماد الجرد وشطب المتبقي» in `qml/StocktakePage.qml` (`stocktakeAdjustVM`). The written-off pieces no longer count in the gold position, so the next position snapshot reflects them. A piece an approved stocktake removed that turns up again is returned with `StocktakeAdjustService.restore_piece` (STOCKTAKE_ADJUST, mandatory reason): REMOVED -> IN_STOCK, a RESTORED movement and an audit row, only for a piece with a STOCKTAKE_WRITE_OFF movement, same barcode; the stored stocktake document is never rewritten; button «إعادة القطعة للمخزون» on the history card of the inventory search (no migration).
- **Manager lock (2026-10-22, owner request, no migration):** مراجعات المدير and the reports (now a section opened from «📊 التقارير» inside the manager reviews; the sidebar button was removed) sit behind a password screen in `qml/Main.qml` (`managerGate`): the signed-in user's OWN login password (the same re-authentication as Dead Cash: `services/manager_access_service.py` -> `AuthenticationService.verify_user_password`, Argon2id, persistent failure cooldown) through `app/viewmodels/manager_gate_viewmodel.py` (`managerGateVM`, one per login, locked again at logout). It stays open while the employee is on those pages and locks 60 seconds after leaving them (`managerRelock`). It is a convenience lock in front of the screens, not a permission: every service still checks its own rights. The UI tests open the lock in the harness `setUp`; `tests/test_manager_gate.py` covers the lock itself.
- **Production database and the old shop's data (2026-10-23, no migration):** `masgold_prod` was built from the chain 001-073 by `python -m tools.build_production --confirm` (a new empty database, an application account `masgold_app` with only SELECT / INSERT / UPDATE / DELETE / EXECUTE / LOCK TABLES on it, settings in the git-ignored `.env.production`); `run_production.bat` starts the program on it (`MASGOLD_ENV_FILE`, only a plain `.env.<name>` file of the project is accepted) while `main.py` stays on DEV. Daily compressed backups of both databases go to `E:\masGold_backups` (`tools/backup_database.py`, Windows tasks at 01:00 and 01:10, verified, 30 days kept). The old program's data (`golddb`, READ ONLY) is copied by insert-only tools that refuse anything but `masgold_prod` or a `masgold_import_*` rehearsal copy (`tools/import_target.py`) and a non-empty target: `import_golddb_customers` (4,593 customers, old number kept as the id), `import_golddb_master` (27 factories, 13 item types, 6 employees), `import_golddb_balances` (one `LEGACY_ADJUSTMENT` opening entry per customer = «متبقي − سداد», customers missing from the list ignored), `import_golddb_stock` (9,662 pieces: 2,130 IN_STOCK, the rest SOLD, and 4,841 FINALIZED LEGACY invoices with their lines; the cash book proves the amount: paid in + left unpaid (rows `C<bill>`) - paid out; the old rows keep the 21K price of the day, so a line's gram price = that x karat / 21; old gold taken in = weight x the buying price - 1.50 % discount; a pure or mixed sale's making = sell total - gold value, spread by weight, left unknown above 25 % of the gold value; modes report / apply / amounts / economics / review). No payment, ledger entry or movement is created for legacy invoices. A legacy invoice is cancelled only from مراجعات المدير (`services/legacy_invoice_service.py`, TRANSACTION_CORRECT, mandatory reason + confirmation): its sold pieces go SOLD -> IN_STOCK with a movement, the invoice becomes VOIDED, the customer's balance is not touched. The opening balance and gold stocktake card moved from Settings into a window opened from مراجعات المدير. It has TWO stages, each once: (1) the scrap per karat 21 / 18 / 24, the main safe's cash and each active bank / wallet account's balance are posted into the real ledgers in one transaction (OPENING_BALANCE movements; an account that already has movements refuses the stage; marker audit `POSITION_OPENING_LEDGERS`); (2) the new gold typed per karat (21K equivalent computed, never typed) plus the optional net supplier gold records the OPENING sheet = new gold + the stage-1 scrap, and closes the card. Opening the program without the login window is a LOCAL workstation choice (`tools/set_auto_sign_in.py`, QSettings, default off; the account is still checked at start); the manager lock keeps asking for the account's password. A bill style choice (current / the old program's look) lives in Settings > Printing.
- **Schema level check:** `services/schema_level.py` holds `REQUIRED_SCHEMA_LEVEL` (the highest migration in `database/schema`; `tests/test_schema_level.py` keeps it equal to the chain, so every new migration must bump it). When a worker query fails with a missing table / column (MySQL 1146 / 1054), `app/workers/invoice_worker.py` asks `schema_problem()`: if the database's highest applied migration is lower, the employee sees «قاعدة البيانات على المستوى X والمطلوب 59: طبّق التحديثات.» (code `SCHEMA_OUTDATED`) instead of the generic invoice error. Read only; a failing check keeps the generic message.
- **Manager reviews, stock entry and private account (2026-10-29, migrations 088-090, applied to `masgold_rehearsal`, DEV and `masgold_prod`):** (1) pricing / settlement / discount reviews are cards with a «تمت المراجعة» button (`ManagementReviewService.mark_reviewed`, MANAGEMENT_REVIEW, the row becomes RESOLVED, audited). (2) Unidentified (F5) sale cases are settled by the manager (089, `inventory_reconciliation_cases.resolution_code`): `MATCH_STOCK` links an IN_STOCK piece of the same karat by barcode (SOLD movement), `FROM_SCRAP` posts the COUNTED INVOICE_SALE_OUT of the case's karat and weight (refused above the available Scrap), `NO_SOURCE` records a mandatory reason and moves nothing; the invoice reversal undoes the stored effect (`CorrectionService._plan_invoice`); exchange-manual cases (no invoice) are not settled here. (3) «إدخال قطعة جديدة» has the entry method «من مجموعة مورد»: `SupplierPurchaseService.open_groups` lists the groups of POSTED, not reversed supplier invoices that still miss more than the 0.333 g tolerance of their supplier weight and are not hidden (088 `supplier_group_intake_closures`, `close_group`, «إخفاء المجموعة»); the karat comes from the supplier and the category from the group's making rate; piece entry no longer exists on the suppliers page (`pieceEntryHere` false). (4) A stocked piece may be moved between the categories A, B and T (`InventoryService.correct_item(category_id=…)`); on 2026-10-29 every IN_STOCK piece was set to B at the owner's request (the old categories are in the audit event `INVENTORY_CATEGORY_BULK_SET`), and the 70 pieces a stocktake had removed were restored to stock. (5) The owner's private account (090 `private_account_entries`, categories PRIVATE_OUT / PRIVATE_IN, right `PRIVATE_ACCOUNT_MANAGE` OWNER only): a transfer is a manual operation on a bank / wallet account tied to its entry, the balance is derived (a reversed operation does not count), shown with its entries in مراجعات المدير; it is neither income nor expense in the cash-flow report. The accounts section of the manager reviews lists banks and wallets only (no Main Safe) and offers «⇄ تحويل خاص»: to / from the private account, to another bank / wallet (`FinancialAccountService.transfer`), to the Main Safe, and to a supplier / marketer cash balance (`MarketerCustodyService.cash_handover_from_account`: bank -> Main Safe -> marketer cash as two audited steps with derived request keys). For the owner the supplier and the marketer are ONE party: its cash balance is the marketer cash account (055), from which gold is bought or sold at a hand-entered price and supplier making is paid (078).
- **Sale below the gold value (2026-11-03, migration 095, applied to DEV `masgold_v2` and `masgold_prod`; prod backup taken first):** a sale whose agreed total is below the gold value is SAVED (no `SALE_BELOW_GOLD` / `AGREED_BELOW_GOLD` refusal any more): its making is NEGATIVE (`Calc.agreed_sale` allows it, `sale_line(making_total=...)` is signed, 095 drops only `chk_invoice_line_making_total` on `invoice_lines`; per-gram making stays non-negative). `InvoiceService._making_groups` marks such a group `below_gold`, status OUT_OF_RANGE, never `needs_approval` (no manager password at the counter), and finalization opens a `MAKING_PRICE` management review «بيع بأقل من قيمة الذهب» with `details.below_gold`. Averages and making reports include the negative making; a Return pays what the customer paid (unchanged) and an Exchange refunds NO making for a negative-making piece (`exchange_service` clamps each piece at 0). The customer is still required. Rollback refuses while any negative making exists. Tests: `tests/test_sale_below_gold_schema.py` and the updated invoice tests.
- **Gold in the shop report (2026-11-03, read only, no migration):** report `goldflow` «الذهب في المحل» (`ReportService._goldflow`, owner rights like `profit`): ONE line per day from the stored position snapshots (first sheet of each day): the actual gold (21K-equivalent grams) and the gold PLUS the shop's money (cash + bank) converted at that day's stored SHOP_BUY 21K price (the latest earlier price when the day has none); cards say «الذهب زاد / نقص» for the period; a day without any price shows no money-as-gold figure. Needs at least two sheets (`masgold_prod` had none until the opening balance is entered). Line charts ride in `result['lines']` (`reports_viewmodel._lines`, Canvas in `qml/ReportsPage.qml`). Tests: `GoldFlowTests` in `tests/test_report_service.py`.
- **iPad reports app (2026-11-03, `webapp/`, read only EXCEPT the Dead Cash write-off below, no migration, standard library only):** a PWA served from this computer for the reports only. `webapp/api.py` -> `WebReports` logs in through the normal `AuthenticationService` (one instance, so the cooldown persists), gives every web session its own verified `AppSession` + `ReportService` (`webapp/sessions.py`: random token, 20 min idle / 12 h absolute, capacity 20) and answers `result_view` (display strings) of `ReportService.run` plus the report pop-up charts: no export, no other write, no other service, filters whitelisted per report. THE ONE WRITE (owner request 2026-11-03): in the customers report (`balances`: every customer shown with his exact balance, biggest first, at most 300; «عليه» = على العميل, «له» = للعميل) a user with `DEAD_CASH_MANAGE` can write a balance off («شطب رصيد») through the existing `DeadCashService.write_off`: the user's CURRENT password is re-verified (shared `AuthenticationService`, failure cooldown), the right is rechecked in the transaction, the expected balance must still match (409 otherwise), the client request key makes it idempotent, silent (no warning flag) as the desktop default, audited with the session user; `POST /api/deadcash/writeoff` and `GET /api/deadcash/context`. HOME PAGE OF CARDS (owner 2026-11-03): after login the page shows one card per module the user may use, from the `MODULES` registry in `webapp/api.py` (add a line there to add a card; `overview['modules']`): «التقارير» (every report no other card claims), «العملاء» (customers + Dead Cash), «حركة الخزنة» (treasury + cash flow, the treasury MOVEMENTS table is shown on the iPad) and «البيع والشراء اليومي» (the sales report, its documents table shown, opens on TODAY; quick period buttons اليوم / أمس / آخر 7 أيام / هذا الشهر on every period report). A report belongs to ONE card; a card with four or more reports (التقارير) shows them as report cards (`GRID_MIN` in `webapp/static/app.js`), a small card keeps buttons. The customers card has a search box: customer by name / mobile / code (all customers, inactive too, with balance side) or an invoice by its EXACT number (opens it) - `GET /api/customer_search`; reports that honour the text filter (`SEARCHABLE` in `webapp/api.py`) get a search field too. Invoices (read only): `GET /api/invoice` = `InvoiceHistoryService.detail` displayed by `InvoiceHistoryViewModel`'s own properties, `GET /api/customer_invoices` = `CustomerAccountService.customer_invoices`; an invoice opens from the documents table, the customer's open items and the customers table button «فواتيره». `webapp/server.py` binds to 127.0.0.1:8765 by default; routes are a fixed whitelist (static files, POST login / logout, GET overview / report / chart: the pop-up charts of the desktop reports - price of a karat, yearly sales lines, monthly sales of a type, the pieces of a row - plus the TradingView live chart in an iframe on the indicators report); cookie HttpOnly + SameSite=Strict (+ Secure behind HTTPS), POSTs need `X-Requested-With: masgold` and a same-origin Origin, bodies capped at 4 KB, CSP and no-store on every answer; the page inserts data with `textContent` only. EMBEDDED in the program (owner 2026-11-03): `webapp/embedded.py` -> `EmbeddedReports` (`webReportsVM`, created by `MasGoldController`, started on program start in the real application only - never offscreen / test platforms, switch `MASGOLD_WEB_REPORTS=0` - and stopped at quit) serves ONE `WebReports` on 127.0.0.1:8765 and on this computer's Tailscale IPv4 address(es) read from `tailscale status --json` (no `tailscale serve`, no funnel, no firewall change; a busy port or a missing / stopped Tailscale only changes the status text). The stable address is the MagicDNS name (`http://<name>.<tailnet>.ts.net:8765`, shown first, then the Tailscale IP) in Settings > «تقارير الأيباد» (`WebReportsSettings` in `qml/Main.qml`, SETTINGS_MANAGE, copy buttons, on / off kept per computer in `LOCALAPPDATA\masGold v2\web_reports.json`, default ON). The web sessions use whatever database the program itself runs on (DEV `main.py`, production `run_production.bat`). `run_web_reports.bat` remains only as a stand-alone start without the program (same port: never both). Never expose it to the public internet. Tests: `tests/test_webapp.py`, `tests/test_embedded_reports.py`, real-click `tests/test_web_reports_ui.py`.
- **Old (paper ledger) customer balance «رصيد قديم» (2026-11-03, no migration):** `CustomerAccountService.add_legacy_balance` (+ `can_add_legacy_balance`, `CustomerAccountRepository.legacy_entry`) puts an OLD balance on a customer's account WITHOUT any cash, bank or wallet movement: one `customer_transactions` row (type `LEGACY_ADJUSTMENT`, reference type `LEGACY_PAPER_BALANCE`, the request key as its reference code, direction OWES = على العميل / CREDIT = للعميل, mandatory note, optional old date kept in the description, never in the future) and one `audit_log` row (`CUSTOMER_LEGACY_BALANCE_ADDED`) in ONE transaction under the customer's lock; OWNER only (`CUSTOMER_BALANCE_ADJUST`, existing since 005, rechecked in the transaction); idempotent on the request key (a different request with it is `REQUEST_CONFLICT`); the row is immutable like every ledger row (a mistake is corrected by an opposite entry). The statement shows it as «حركة قديمة / غير محددة». Desktop: button «إضافة رصيد قديم» + `legacyDialog` in `qml/CustomerAccountPage.qml` (`customerAccountVM.canLegacy` / `beginLegacy` / `legacyAction`, a confirmation step); iPad: the third write of the app, `POST /api/legacy_balance` (current password re-verified) from the customers balances table and the customer search results. Tests: `tests/test_customer_legacy_balance.py`, real-click `tests/test_customer_legacy_balance_ui.py`, `LegacyBalanceTests` in `tests/test_webapp_extras.py`.
- **More iPad cards (2026-11-03):** the home page also shows today's figures (`GET /api/today`: today's sales / scrap purchases / returns / collected, cash and bank balances, the gold position, the live 18 / 21 / 24 prices; each part only for a user who may see it); cards «المخزون» (piece lookup by barcode / number, `GET /api/piece`, plus stock, alerts, weights), «مراجعات المدير» (open reviews, read only, `GET /api/reviews`) and «التحويلات» (`webapp/extras.py`: transfers between ACTIVE bank / wallet accounts and to / from the owner's private account through `FinancialAccountService.transfer` / `private_transfer`, `POST /api/transfer`, the current password re-verified, the main safe is never offered); a customer's statement (`GET /api/statement`, the `CustomerAccountViewModel` row display); a CSV download of any report (`GET /api/export`, the report service's own `export_csv`). The embedded server has an opt-in «تفعيل HTTPS» button in Settings (`tailscale serve --bg`, never a funnel; needs HTTPS certificates enabled once in the Tailscale admin page). Writes of the app: Dead Cash write-off, transfers, old balance - each with the user's current password and its own right.
- **iPad app additions (2026-11-03, no migration):** the home page opens with «الأهم دلوقتي» (`Extras.attention` / `GET /api/attention`: open manager reviews, stock below the minimum, customer debts; polled every 60 s while the page is open, a browser Notification only while the page is open - no Web Push). «مراجعات المدير» has a «تمت المراجعة» button for MAKING_PRICE / CASH_SETTLEMENT / OLD_GOLD_DISCOUNT (`Extras.review_done`, `POST /api/review_done`, `ManagementReviewService.mark_reviewed`, current password). A customer's statement offers «تسوية الحساب» (`Extras.settle`, `POST /api/settle`: ONE real leg, cash or one bank / wallet account, through `CustomerAccountService.finalize`, `CUSTOMER_ACCOUNT_SETTLE`, current password, request key). New cards: «حاسبة الذهب» (client-side estimate from the live per-gram prices only, `buyRaw` / `sellRaw` in `/api/today`) and «سجل النشاط» (`services/audit_view_service.py` -> `AuditViewService.recent`, `repositories/audit_view_repository.py`, read only, `USERS_MANAGE`, last 100 audit_log rows). Share buttons use the iOS share sheet (`navigator.share`) for an invoice summary and a report CSV. Writes of the app are now: Dead Cash write-off, transfers, old balance, review done, customer settlement.
- **Scrap may go negative through a sale (2026-11-03, owner, no migration):** an F7 sale / Exchange F7 replacement from counted Scrap is no longer refused above the counted balance (`InvoiceService._old_gold_shortages` returns no shortage; goods are sometimes bought outside and sold before the price is agreed with the other shop). The balance simply goes negative as the visible record. Other Scrap consumers keep their own refusals (market-trip issue, supplier settlement, manual decrease, manager F5 `FROM_SCRAP` settlement).
- **KPI report (2026-11-08, read only, no migration):** report `kpi` «KPI» (`ReportService._kpi`, pure rules in `services/kpi_rules.py`, `ReportRepository.kpi_lines` / `default_old_gold_discount`, right `INVOICE_VIEW`, with a period; iPad card `kpi`). Compares the period with the previous period of the same length; every item has a status (green / yellow / red / gray = not enough data) and a decision sentence: sold grams, average making per gram (lines of 1 g or more), the old-gold discount actually given against the default, gold-profit speed (position snapshots) and recorded expenses (cash-flow report; gray without the right). Per employee: sale lines whose making is under the piece's cost OR under 80% of the other employees' weighted average of the karat, and scrap lines with a discount under the default; a verdict needs 5 lines. Statuses are indicators for the owner's attention, not proof. Tests: `KpiTests` in `tests/test_report_service.py`, opt-in `tests/test_kpi_mysql.py`. Planned next, one point at a time: persist the stocktake session on disk, day close, bank / InstaPay matching, inventory turnover and a configurable dead-stock age.
- **Corrected invoice amount in reports (2026-11-12, owner, no migration):** a settlement correction (FC) may record more or less money than the invoice (since 2026-10-28). The finalized invoice and its lines are never rewritten; the reports and the home page count the invoice at its LATEST FC amount: delta = latest `settled_total` - the settlement stored in the first FC's `before_json` (only the change the corrections made; payment rounding / cashback differences that existed before are not reinterpreted). Money IN -> the delta goes to the MAKING of the sale lines, money OUT -> to the value paid for the scrap-purchase lines (discount moves the other way), spread by weight, the last line takes the cents (`services/invoice_amount_rules.py`, reads `repositories/invoice_amount_reads.py` mixed into `ReportRepository` and `DashboardRepository`). Applied in employees, KPI, making profit, scrap cost, sales (value, collected / paid and account effect; therefore also the treasury cards and the iPad today figures) and the home page (today's totals, making averages, scrap paid, daily sales bars, month top types). Exchange / Return FCs are not included. The «الذهب في المحل» report converts the money at the 21K SHOP_SELL price (owner 2026-11-12). Tests: `tests/test_invoice_amount_corrections.py`.
- **Gold profit counts the money as gold (2026-11-12, owner, no migration; supersedes "cash and bank are NOT inside the profit" of the profit bullet above):** «ربح الذهب (مكافئ 21)» in the `profit` report AND the KPI's gold-profit item = (closing sheet's gold, already net of the suppliers, + its cash and bank ÷ the SHOP_SELL 21K price of that sheet's day) − the same for the opening sheet, in 21K grams; so gold sold for cash or bought with cash is no profit or loss by itself. One shared rule in `services/report_service.py`: `ReportService._sell21_prices` (the day's stored price, else the latest earlier, else the earliest later) and `_with_money` (money_gold quantized 0.001 HALF_UP); «الذهب في المحل» uses the same two helpers, so the three never disagree. No stored price -> the profit is «غير متاح» (KPI gray), never the gold alone. The profit report still shows the gold-only change («الذهب الفعلي بدون الفلوس — التغير») and the cash / bank changes apart, and its parts table adds «الفلوس بالذهب» and «الإجمالي (ذهب + فلوس بالذهب)». Production 1–12 Nov 2026: gold only −18.247 g, gold + money +3.423 g. Tests: `ProfitTests`, `KpiGoldProfitTests`, `GoldFlowTests` in `tests/test_report_service.py`.
- **Invoice History type / karat filter (2026-11-12, owner, no migration):** «سجل الفواتير» has two more filters, الصنف (active `item_types`) and العيار (active karats), read once per page entry (`InvoiceHistoryService.filter_options` -> `InvoiceRepository.history_filter_options`). The list keeps only the invoices that have ONE saved line of that type AND karat (`search_invoice_history(item_type=, karat=)`: one EXISTS on `invoice_lines`, the stored `item_name_snapshot` compared Arabic-folded so «سبيكه» = «سبيكة»); the whole invoice is listed with all its lines. «مسح» resets every filter and dropdown. The choices have their own `optionsChanged` signal so a search never resets the shown choice. Tests: `tests/test_invoice_history.py`, real-click `tests/test_invoice_history_qml.py`.
- **Mixed invoice printed at its full sale total (2026-11-12, owner, no migration):** ONLY when the customer asks, a SALE + customer-old-gold invoice may be printed with «إجمالي الفاتورة» = its full sale total instead of the net after the old gold: `services/invoice_print_service.py` -> `sale_total_view` (header `sell_total`, or the saved sale lines when a legacy header lacks it, plus an effective exchange difference, rounded ONCE with `round_to_egp5`, so 69,282.23 prints 69,280; no payment line, the old gold is not printed as before; the same A5 layout) through `print_invoice(sale_total=True)`. Offered in the save confirmation (checkbox «اطبع إجمالي البيع كامل (بدون خصم الكسر)» `printSaleTotal`, shown for a mixed invoice only, unticked every time, ignored by «حفظ فقط»; the success-banner retry prints the same paper) and in سجل الفواتير (button «طباعة بإجمالي البيع» `historyReprintSaleTotal`, `InvoiceHistoryService.reprint(sale_total=True)`). The saved invoice, money, ledger and reports never change. The print service's ONE write: after a full-total paper is actually sent, an insert-only `invoice_events` row (PRINTED, or REPRINTED from the history; notes «طباعة بإجمالي البيع», details `print_mode` SALE_TOTAL + `printed_total`); a failure to write it is logged, never a print failure. The history detail shows the latest one in the summary strip («طُبعت بإجمالي البيع»: amount, time, user, count). Tests: `SaleTotalPaperTests` in `tests/test_invoice_print_service.py`, `tests/test_invoice_viewmodel.py`, `tests/test_invoice_history.py`, real-click `tests/test_invoice_page_qml.py` and `tests/test_invoice_history_qml.py`.
- **Line endings:** do not mass-convert files and never normalize migrations (checksums). No
  `.gitattributes` is added; with `core.autocrlf=true` a policy file would cause churn. Deferred.

### Schema application status (recorded evidence, not a fresh verification)

- Migration 056 (market-trip BOOK core), recorded evidence (2026-10-18): the chain 001-056 was built clean on a NEW disposable database (`masgold_chain056_verify_20261018_c2b30d`, level 56), re-running 056 there left the schema unchanged, 056 was then applied once to `masgold_rehearsal` (level 55 -> 56, every existing trip LEGACY) and the two schemas were compared read-only with 0 structural differences. It was NOT applied to DEV (`masgold_v2`) or production by this work and their levels are not verified here. Migration 055's clean-chain build and structural equivalence with `masgold_rehearsal` were verified separately earlier. Behaviour on real MySQL (`masgold_rehearsal`, opt-in): all 19 `BookCoreMySqlTests` passed in one run after the custody display-read repair, plus 3 legacy custody-read checks (`tests/test_custody_display_mysql.py`) and 3 existing hardening nested-read checks; these are focused runs, not a stress test, and not the full regression.
- **Market custody, owner's two-stage model — STAGE 1 implemented (2026-10-20; the BOOK mode is NOT wanted and stays hidden):** the owner records everything AFTER returning from the market. Stage 1 = ISSUE only: cash leaves the MAIN safe and scrap leaves by weight and karat with NO melt/logo designation (`services/market_trip_service.py` → `parse_designation(..., allow_blank=True)` from `_lines` for ISSUE only: stored as `UNCLASSIFIED`, shown «بدون وجهة»; the cash gold purchase still requires MELT or LOGO; no migration, the column already allows UNCLASSIFIED). The safe and the scrap may NEVER go negative: `_issue_resources` refuses an issue above the locked safe balance (`SAFE_SHORTAGE`, new) or above available scrap (`SCRAP_SHORTAGE`, existing). `qml/MarketTripsPage.qml`'s issue dialog no longer has a designation choice. Gold issued this way can only be RETURNED to the shop until stage 2 (melt / supplier / marketer / sale / loss classification, see the owner's spec in the session memory) replaces the old melt/logo flows; existing MELT/LOGO trips keep working unchanged. Tests: the `FakeRepository.cash_balance` fixtures add `db.safe_allowance` (default 10,000,000) so older fixtures keep working; the rule's own tests set it to 0.
- **Market custody STAGE 2a implemented (2026-10-20; migration 060, applied to `masgold_rehearsal` ONLY, NOT to DEV `masgold_v2` which is at 59):** the melt recorded AFTER the owner returns («تسجيل سبك (بعد الرجوع)»). Gold issued without a destination (UNCLASSIFIED) is melted into ONE bar: `services/settlement_rules.py` (pure: theoretical Eq21 = sum(g x K / 21), actual Eq21 = bar weight x fineness / 875 with an INTEGER per-mille fineness, loss = theoretical - actual, weight loss = (melted grams - bar weight) x expected fineness / 875, fineness loss = loss - weight loss, negative = gain; all posted once at 0.001 g half-up), `MarketTripService.settle_melt()` / `preview_settle_melt()`, entry kind `ST_MELT` (LEGACY trips only), tables `market_trip_settle_melts` (bar weight, fineness, theoretical / actual / loss split for the reports), `market_trip_settle_melt_inputs` and the insert-only pool ledger `market_trip_settle_moves` (RAW per karat = grams that left UNCLASSIFIED custody, BAR = Eq21 grams of undistributed bars; a reversed entry stops counting). The BAR pool is part of the custody (`custody["bar"]`; a trip cannot close while it is non-zero), of Gold Position (`bar_eq21`, no physical grams) and is reversed only by the ordinary full reversal (blocked once a later step used the bar; the issue cannot be reversed while its gold is melted). Reads are guarded by `has_settlement_schema()`, so a database below 060 keeps every existing path. Rehearsal evidence (`masgold_build`, real SQL): `tests/test_settle_melt_mysql.py` 6/6 (CHECK refusals, the owner's worked example 44.286 / 43.520 / 0.197 / 0.569, return of the rest, closure refused with the bar, reversal, replay, two-connection race) plus BOOK / 057 / 058-059 / reports modules green (43 tests, the only error is the known `data_locks` privilege of `masgold_build`). The BOOK rehearsal fixtures now fund MAIN_SAFE once per run because the safe may no longer go negative. Not yet built: distribution to suppliers / marketers / sale, cash settlement, remainder classification, issue-weight correction, reports (steps 2b-2g).
- **Market custody STAGE 2b implemented in code (2026-10-20; migration 061 applied to `masgold_rehearsal` ONLY (level 60 -> 61, rows unchanged); DEV `masgold_v2` was brought to 65 on 2026-10-20 (see below)):** distribution of the owner's gold to a SUPPLIER after he returns. Reuses the supplier gold settlement machinery (045/046): (1) the UNMELTED gold (UNCLASSIFIED custody, its OWN karat) goes through the existing `MarketTripService.settle_supplier_gold()` (046; `SETTLEABLE_DESIGNATIONS` now LOGO + UNCLASSIFIED, `chk_mtgs_designation` relaxed by 061); (2) the melted BAR (BAR pool, 21K-equivalent grams) goes through the new `settle_supplier_bar()`: entry kind `ST_BAR_SUP` (LEGACY only), SG source `TRIP_BAR` (the delivered amount is N grams of the 21K master karat, i.e. Eq21), the supplier credit in ITS dedicated karat = N x 21 / supplier karat derived once like 045/046 (`supplier_rules.settlement_credit`), no Scrap / cash / lot movement, a negative BAR move in `market_trip_settle_moves`, the link table `market_trip_bar_supplier_links` (composite keys tie it to the entry and to the SG document), capped at the BAR pool, an inactive supplier and a crossing into supplier credit allowed, rights MARKET_TRIP_MANAGE + SUPPLIER_GOLD_SETTLE rechecked in the transaction, lock order trip -> supplier, exact replay before any refusal. Reversal only through the custody entry (`CorrectionService`, stored credit restored, pool regained; the melt cannot be reversed while its bar was delivered; the supplier-side reversal of a TRIP_BAR document is refused with guidance). The «تسوية ذهب من العهدة» dialog offers all three sources (LOGO / unmelted gold / the bar: designation pseudo-value `BAR`). Tests: service (fakes) 20, schema contract 9, UI with real clicks at 1080x680; the opt-in `tests/test_settle_distribution_mysql.py` ran on `masgold_rehearsal`: 7/7 (CHECK / FK refusals, the bar delivered in the supplier's own karat, replay, reversal restoring the stored credit, melt blocked while its bar was delivered, unmelted gold delivered in its own karat and the trip closing at zero, a two-connection race) with the 2a module 6/6 and the BOOK / 057 / 058-059 / reports modules green (the only error is the known `data_locks` privilege).
- **Market custody STAGE 2c implemented in code (2026-10-20; migration 062 applied to `masgold_rehearsal` ONLY (level 61 -> 62, rows unchanged); DEV `masgold_v2` is at 65 since 2026-10-20 (060-065 applied together, level 59 -> 65, rows unchanged: 21 trips, 67 entries, 16 invoices, 2 customers; a logical backup was taken first); level 62):** after the melt, (1) `ST_BAR_MKT` hands N grams (21K-equivalent) of the BAR pool to a marketer: ONE marketer 055 movement `TRIP_BOOK_IN` tied to the entry, a negative BAR move, no Scrap / cash / supplier movement; the gold stays shop-owned; reversal posts `TRIP_BOOK_IN_REVERSAL` and is refused when the marketer no longer holds it (needs INTERMEDIARY_HOLDING_MANAGE, reverse also INTERMEDIARY_HOLDING_REVERSE). (2) `ST_LOGO` classifies EVERY unmelted remaining gram as LOGO of its own karat (RAW -g, LOGO +g in `market_trip_settle_moves`), after which the existing LOGO flows apply unchanged; reversal is refused when the LOGO was used (`DEPENDENT_LOGO_USE_EXISTS`). Both kinds are LEGACY-only. UI: «تسليم سبيكة لمسوقجي» and «اعتبار المتبقي لوجو» in `qml/MarketTripsPage.qml`. Tests: service module, schema contract `tests/test_market_trip_settlement_marketer_schema.py`, real-click UI; `tests/test_settle_marketer_mysql.py` 6/6 on `masgold_rehearsal` (CHECK refusals, bar to marketer and reversal, melt blocked, LOGO classification and reversal) with the BOOK module green. Next: 2d sale, 2e cash settlement, 2f remainder/closure, 2g reports.
- **Market custody STAGE 2d implemented in code (2026-10-20; migration 063 applied to `masgold_rehearsal` ONLY (with 064 and 065, level 62 -> 65, rows unchanged); DEV `masgold_v2` is at 65 since 2026-10-20 (060-065 applied together, level 59 -> 65, rows unchanged: 21 trips, 67 entries, 16 invoices, 2 customers; a logical backup was taken first); level 63):** the owner SELLS gold after returning, through the existing 053 `sell_gold()`: (1) unmelted gold (`UNCLASSIFIED`, its own karat) already works; (2) NEW source `TRIP_BAR` sells N grams (21K-equivalent) of the melted BAR pool: one negative BAR move (`market_trip_settle_moves`, entry kind `GOLD_SALE`), the sale document keeps karat/designation/lot NULL, the 10% price warning uses the 21K SHOP_BUY reference. The agreed total ENTERS the custody CASH (owner decision (أ): never Main Safe, no cash/bank movement; step 2e then spends it or returns the remainder), no Scrap/supplier/making/lot movement. Reversal = the ordinary full reversal (grams back in the pool, proceeds leave custody cash; blocked when the proceeds were used; the melt stays protected while its bar was sold). UI: the existing «بيع ذهب من العهدة» dialog lists the bar. Tests: service (fakes), schema contract, real-click UI at 1080x680; MySQL: `tests/test_settle_cash_disposal_mysql.py` 9/9 on `masgold_rehearsal` (2026-10-20) covers 063-065 (TRIP_BAR sale and reversal, expense / bank deposit / write-off, purchase without destination, market return, loss, issue-weight correction both ways, CHECK refusals) and the earlier settlement modules plus gold buy/sale re-ran green at level 65 (94 tests, 18 skipped).
- **Market custody STAGE 2e implemented in code (2026-10-20; migration 064 applied to `masgold_rehearsal` ONLY (see 063); level 64):** the custody CASH is settled after the owner returns, on custody level only (owner decision: no general expense report yet): `ST_EXPENSE` (free-text description mandatory), `ST_CASH_BANK` (the remaining cash deposited into an ACTIVE bank / wallet account: ONE bank movement of type `TRIP_CUSTODY_BANK_IN`, accounts may go negative on reversal) and `ST_CASH_OFF` (write-off, reason mandatory). All three share the immutable document `market_trip_settle_cash`, are LEGACY-only, capped at the custody cash (`CASH_EXCEEDS_CUSTODY`), need `MARKET_TRIP_MANAGE` rechecked in the transaction, one request key and hash; no Main Safe movement (cash returns to the safe only through the normal RETURN). Custody cash = issued - returned - paid + proceeds - purchases - settled, defined once in `services/custody_rules.py`. Reversal = the ordinary full reversal (cash back in custody; the bank movement is reversed by the generic money plan); a sale or issue whose cash was settled cannot be reversed (`DEPENDENT_CASH_USE_EXISTS`). The cash gold purchase (054) now takes ANY karat WITHOUT a melt / logo choice (stored `UNCLASSIFIED`, «بدون وجهة»; explicit MELT/LOGO still accepted by the service). UI: «تسوية نقدية العهدة» dialog (`cashSettleDialog`), the buy dialog lost its destination combo. Tests: service (fakes) 13, schema contract 5, real-click UI at 1080x680; MySQL behaviour verified together with 063 (see above). Next: 2f remainder disposal / issue-weight correction / closure, 2g reports.
- **Market custody STAGE 2f implemented in code (2026-10-20; migration 065 applied to `masgold_rehearsal` ONLY (see 063); level 65):** the remainder after the trip and the issue-weight correction. (1) `ST_RET_MKT` «مرتجع سوق للمحل» (`MarketTripService.return_to_shop`): sources RAW (unmelted gold, own karat), LOGO (own karat) or BAR (21K-equivalent grams); each source loses the grams (`market_trip_settle_moves`), counted Scrap gets ONE IN movement per target karat (the bar enters as 21K), legs in `market_trip_entry_gold`, no cash / supplier / lot movement. (2) `ST_LOSS` «خسية» (`record_loss`): the same sources leave the custody with a MANDATORY classification CRUCIBLE (خسية بوتقة) or MARKET (خسية سوق); nothing else moves. Lines are stored in `market_trip_settle_lines` for the 2g reports. (3) `ST_ADJUST` «تصحيح وزن الإخراج» (`correct_issue_weight`, owner decision: BOTH directions, reason mandatory): INCREASE = the real issued weight was higher (Scrap OUT, custody RAW +g, refused with `SCRAP_SHORTAGE` when Scrap would go negative); DECREASE = lower (Scrap IN, custody RAW -g, refused when the unmelted custody gold of that karat is smaller); document `market_trip_settle_adjust`. All LEGACY-only, MARKET_TRIP_MANAGE rechecked in the transaction, one request key and hash, exact replay. Reversal = the ordinary full reversal (the Scrap reversal label follows the stored sign); an INCREASE cannot be reversed once its grams were used (`DEPENDENT_GOLD_USE_EXISTS`); the melt stays protected while its bar was returned / lost (pool arithmetic). Only ISSUE / RETURN legs count as issued / returned grams. UI: «مرتجع سوق / خسية» and «تصحيح وزن الإخراج» dialogs. Tests: service (fakes) 21, schema contract 5, real-click UI at 1080x680; MySQL behaviour verified together with 063 (see above). Next: 2g reports.
- **Market custody STAGE 2g implemented (2026-10-20; read only, NO migration), simplified at the owner's request to TOTALS ONLY:** the report `market` «عهدة السوق والخسائر» (`ReportService._market`, two plain SELECTs in `ReportRepository`: `trip_melts`, `trip_settle_lines`; no lock, one statement each) shows ONE summary table and ONE grand total «إجمالي الفقد والخسية»: melt loss (weight loss + fineness loss), crucible loss and market loss, all in 21K-equivalent grams (the bar is already Eq21; unmelted gold / logo are converted grams x karat / 21, posted once per line at 0.001 g) so the karats can be added; negative = gain. Documents sit in the period by their own date; reversed documents are not counted (their number is shown apart); a missing table marks the total «ناقص», never a zero. Expenses and cash write-offs are deliberately NOT in this report (not gold loss). Right: `REPORTS_VIEW` + `MARKET_TRIP_MANAGE` rechecked in the read transaction. Tests: `tests/test_report_market.py` (fakes), reports UI, opt-in read-only `tests/test_report_market_mysql.py` on `masgold_rehearsal`. UI of the custody page (owner review): the panel is split into «عهدة الذهب» and «عهدة النقدية», each with its own action buttons beside its figures (`goldActions` / `cashActions`); only the general buttons (issue, return, close / reopen, history) and the BOOK buttons stay in the common row. Owner review (2026-10-20): nine older / 2c actions are HIDDEN from the custody page by default (page property `advancedActions: false` in `qml/MarketTripsPage.qml`): own melt «سبك», «حمّية اللوجو», «إخراج سبيكة من المحل», «رجوع ناتج السبك», «تسوية سبيكة من العهدة», «تسليم ذهب لمسوقجي», «تسليم لوجو لمسوقجي», and the two 2c actions «اعتبار المتبقي لوجو» and «تسليم سبيكة لمسوقجي». Services, view-model slots and dialogs are untouched (the history of such documents stays readable and reversible); one switch brings the buttons back; the UI tests set it to true in `open_page` and one test checks the default.
- **Rehearsal gate (2026-10-19, packet28; supersedes the three "PREPARED / not applied" lines below for `masgold_rehearsal` ONLY):** the live session (account `masgold_build`@localhost:3306, which sees only `masgold_rehearsal`) showed level 57 (057 already applied, contiguous 1-57); 058 and 059 were then applied forward to `masgold_rehearsal` (files matched `SHA256SUMS`; level 57 -> 59, existing row counts unchanged). Real-MySQL results on that database: `test_market_trip_book_mysql` 19/19, `test_book_settlement_mysql` 1/1, `test_book_settlement_mysql_races` 1/1, the new `tests/test_book_phase23_reports_mysql.py` 13/13 (058/059 objects, gain record constraints, gain -> reconcile (v3 record round-trip) -> close with finality, gain reversal, the six reports and five gold sources on the real `ReportRepository` with no write, the sales report over three LEGACY invoices inserted in a transaction that is always rolled back (NULL stays unknown, one-side-known shown, stored zeros proven), and five two-connection races: two reconciliations -> one, gain vs reconcile -> exactly one stands, two closures -> one, the same request key by the same actor -> one document, one assayed stock item to two trips -> one). A request key reused by a DIFFERENT actor is a `REQUEST_CONFLICT` by design. `test_custody_display_mysql`: 2 of 3 ran; the snapshot-lock test is NOT RUN because `masgold_build` lacks SELECT on `performance_schema.data_locks` (privileges were not changed). Not covered: other race pairs (supplier / marketer deliveries vs reconcile, close vs cash return, reopen vs reversing BK_RECON), rollback refusal, behaviour of a database below 056/057/058, stress, and any DEV/production database (production remains unverified). **DEV `masgold_v2` (2026-10-19, run by the owner with `dev_migrate.py`, report `evidence/dev/dev-apply.json` of packet28): level 55 -> 59, 056-059 applied forward in order (file hashes equal to the rehearsal-verified ones), row counts unchanged: 19 trips, 62 entries, 14 invoices, 2 customers. This cured the app's `SCHEMA_OUTDATED` message on the market-trips page. Whether a backup was taken first is not recorded here.** BOOK stays INACTIVE. Earlier text below still describes the other databases.
- Migration 059 (BOOK Phase 3: evidenced gain and v3 reconciliation formats) is PREPARED only: not applied to any database; its CHECK / FK / capability behaviour on MySQL is a later verification gate. `REQUIRED_SCHEMA_LEVEL` is 59; the paths that need it are inside the inactive BOOK mode.
- Migration 058 (BOOK Phase 2) is PREPARED only: not applied to any database; its CHECK / FK / capability behaviour on MySQL is a later verification gate. `REQUIRED_SCHEMA_LEVEL` is 58; the paths that need it are inside the inactive BOOK mode.
- Migration 057 (BOOK reconciliation document) is PREPARED only: it has not been applied to any database (rehearsal, DEV or production) and its real-MySQL checks have not been run. `REQUIRED_SCHEMA_LEVEL` is 57, but the path that needs the new table is disabled and the level check is reactive (MySQL 1146 / 1054 only), so a database at level 56 keeps working until that path is enabled; this gate alone does not prove that every operation is compatible with an older schema.
- Migrations 052-055 were reportedly applied to `masgold_rehearsal` and 052-055 to `masgold_v2` by the apply scripts of the session, with checksum / marker / level
  guards; the outputs were not saved as files (only the session transcript has them). No backup was taken (DEV is test data).
- `masgold_rehearsal` received TWO revisions of migration 055 (the first used the source name `HOLDING_MARKETER`, too long for `VARCHAR(12)`; the final text uses
  `HOLDING_MKT` and was re-applied idempotently). `masgold_v2` reportedly received only the final text. A clean application of the final chain, the equivalence of
  rehearsal's "v1 then v2" schema to the final definition, and the current schema levels of either database have NOT been demonstrated.

### Official test baseline

Latest GREEN full run (2026-10-22, the working tree on top of `d08e339` before the UI-review / stocktake commit, every `MASGOLD_*` opt-in variable unset, `.venv` Python, the non-database regression): **4362 tests, OK, 597 skipped (the opt-in rehearsal / DEV categories), EXIT=0**. (Earlier: 4319 tests / 577 skipped at `f36b003`.) It supersedes the older figures below for the current tree; the opt-in real-MySQL modules of migrations 067-072 were run separately and focused on `masgold_rehearsal`.

Last GREEN full run on record (`full7`, 2026-10-16 about 20:16, the working tree just before commit `7afe108`, migration 054): **2858 tests = 2673 passed
+ 185 skipped, EXIT=0**. The figure documented until now (2867 = 2673 + 194) was that run plus the 9 opt-in 054 MySQL tests added afterwards (all skipped without
their opt-in), i.e. an arithmetic extension, not a run. Earlier recorded baselines (1546 / 2751 / 2823) are historical and are superseded.

Latest recorded full run (`full9`, 2026-10-16, working tree = `8e67925` + uncommitted edits, i.e. NOT exactly `0b7a438`): **2972 tests: 2764 passed,
2 failures, 206 skipped, EXIT=1**. Two files (`test_authorization_paths`, `test_customer_deposits`: stale expectations after the marketer work) were
then fixed and re-run on their own (29 tests, OK). That focused rerun does not make `full9` a green full-suite run: **no clean full-suite result for
`0b7a438` (or any later commit) has been established by that evidence**, and no new total is claimed here. Skipped tests are the opt-in
MySQL/integration tests (rehearsal / DEV opt-ins), which stay opt-in and are never run against persistent DEV data casually.
Schema application of 052-055 is recorded from scripts and session transcripts only (see "Schema application status" below); current database levels are not
freshly verified by this document.

History of the full non-database regression runs for the Phase 1 BOOK-core tree (2026-10-18, `.venv` Python, every `MASGOLD_*` opt-in variable unset, working tree = `e7922e2` + uncommitted edits, never a commit): run 1 = 3227 tests, 6 failures (4 stale migration-chain length checks plus 2 UI failures), EXIT=1; run 2 (after the chain checks were updated) = **3227 tests: 2983 passed, 242 skipped, 2 failures, 0 errors, EXIT=1**, the 2 failures being `test_intermediary_holdings_ui.HoldingCustodyPageTests` `test_reversal_from_the_custody_page_restores_the_lot_with_a_compensation` and `test_reversing_a_logo_handover_from_the_custody_page_restores_the_stored_grams`, which fail identically on a clean checkout of `e7922e2` (they clicked the LAST reverse button while trip entries are listed newest-first since `e7922e2`). Those two stale tests were then corrected (the button is chosen from the entry list and verified against its entry reference before the click); neither earlier run is a green run and their numbers are not combined with later focused reruns. Real-MySQL evidence is separate and focused: the 19 BOOK tests, 3 legacy custody-read tests and 3 hardening nested-read tests ran green on `masgold_rehearsal` (see "Schema application status"); the opt-in tests are skipped in every non-DB regression. **Final run 3 (the actual result for this tree, after the two UI tests were corrected, one full non-DB regression, no focused reruns added): 3227 tests: 2985 passed, 242 skipped, 0 failures, 0 errors, EXIT=0** (the working tree is `e7922e2` + uncommitted Phase 1 edits, not a commit; the 242 skips are the opt-in rehearsal / DEV / live-read-only / performance categories only; this is a non-database result and says nothing new about MySQL).

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

- **Whole assayed custody-lot supplier settlement (migration 047):** one whole MAIN or known-assay RESIDUE lot from its current OPEN custody settles one supplier through SG / LOT_SETTLE / CUSTODY_LOT and one negative SUPPLIER_SETTLE custody movement. Weight, assay and original melt identity are immutable; unknown assay, partial outstanding and credit rounding to zero are refused atomically. Precision-50 Decimal credit = weight * fineness/1000 * 24 / supplier karat, posted once directly from the raw result at 0.001 g HALF_UP; six-decimal analysis and posted-minus-analysis difference are separate snapshots. No Scrap, money, making, inventory or shop-lot movement. Inactive suppliers and crossing into credit are allowed. Locks: current trip -> original lots in ascending ID order -> supplier; never lock the origin trip for a reissued lot. Original melt reversal locks its output lots before dependencies. Posting needs MARKET_TRIP_MANAGE + SUPPLIER_GOLD_SETTLE; reversal only through the custody entry also needs TRANSACTION_CORRECT + SUPPLIER_GOLD_SETTLE_REVERSE and an OPEN trip and reason. Reversal restores exact stored supplier grams/Eq21 and the same lot to the same custody by excluding consumption; the permanent melt analysis remains. A fresh key may settle a restored lot; exact-key replay returns the original linked result even after reversal. Manual closure still requires zero effective outstanding. Partial allocation, remainder lots, intermediary holdings, sales, purchases and assay amendments remain outside scope.
- **Whole assayed lot holdings at a market intermediary / مسوقجي (migration 048, `IHxxxxxx`, custody entries `LOT_HOLD` / `HOLD_RETURN`, «تسليم ذهب لمسوقجي» / «ذهب المحل لدى المسوقجي»):** the OWNER hands ONE whole known-assay lot (MAIN or known-assay RESIDUE) of an OPEN custody to ONE ACTIVE party with the intermediary role. The gold stays SHOP-OWNED and physically sits with the party: it is not a supplier payable, a sale, turnover or cash, and a holding pool is a ledger identity, never a new physical bar. One transaction posts the `LOT_HOLD` entry, one negative `HOLD_OUT` custody lot movement (the whole lot leaves custody), an immutable source pool (party, lot, melt origin, weight, assay), the IH document, one positive `HANDOVER` holding movement and the typed link; nothing moves in Scrap, cash, making, supplier ledgers, inventory or the shop lot ledger, and the lot's identity, origin, kind, weight and assay never change. The posted grams are the lot's full weight derived under the locks (unknown assay, a partial quantity or a partially available lot is refused). Sources are never merged, even when their assays match, and nothing is unique per lot across history (a lot may be handed over again). The NORMAL RETURN takes the same whole lot back into the ORIGINAL custody, which must be OPEN (reopening is explicit and audited, never automatic), and needs the OWNER's explicit confirmation «تم استلام نفس السبكة كاملة دون تجزئة» (stored as the code `SAME_BAR_WHOLE_UNDIVIDED` with the confirming user and time; a full balance alone certifies nothing). A custody may close when its OWN outstanding is zero after a handover while the gold stays visibly outstanding at the party; the wording never implies that it returned. Rights, OWNER only: `INTERMEDIARY_HOLDING_VIEW`, `INTERMEDIARY_HOLDING_MANAGE`, `INTERMEDIARY_HOLDING_REVERSE`; handover and return also need `MARKET_TRIP_MANAGE`, a reversal also `TRANSACTION_CORRECT` and a reason, all rechecked in the transaction (the actor is the session user). A reversal goes through `CorrectionService` on the custody entry (`MARKET_TRIP_ENTRY`): the CUSTODY side keeps the established convention (the entry stops counting) while the HOLDING ledger posts an explicit signed compensation movement (`HANDOVER_REVERSAL` / `RETURN_REVERSAL`) and the original movement stays counted (a balance is the SUM of every movement). A handover cannot be reversed while its return is effective, a return cannot be reversed after the lot was used or handed again, a melt or reissue stays protected, newest first, no cascade. New handovers need an active intermediary-role party; what an inactive party holds can still be resolved; ANY holding history (a pool or a document, reversed ones included) keeps the intermediary role and the party row, and a dual-role party's supplier account stays separate and unchanged. Lock order: affected trip -> lots (ascending) -> party -> holding source pool, every decision a CURRENT locking read. One request key covers the custody entry and the IH document, exact replay returns the original linked records before any availability is derived, and a key already used by a custody entry, a holding document or a supplier settlement is a REQUEST_CONFLICT (the direct supplier settlement checks it too). Supplier settlement of a holding and its partial gram allocation are implemented by 050; sales, purchases, cash holdings, measured receipts, replacement lots and reports are NOT implemented.
- **Master-karat LOGO holdings at a market intermediary / مسوقجي (migration 049, `IGxxxxxx`, custody entries `LOGO_HOLD` / `HOLD_BACK`, «تسليم لوجو لمسوقجي»):** pooled PHYSICAL grams of ONE master karat with designation LOGO, shop-owned and visible on the party's page next to the 048 assayed lots (a separate section). It is not an identified bar: no fineness, pure-gold or Eq21 figure, no whole-bar rule and no intact confirmation, and nothing is invented about an original issue or heated piece. Quantities are positive Decimal with at most 0.001 g. A handover of q grams needs an OPEN custody with at least q effective LOGO outstanding of that karat and an ACTIVE intermediary-role party (the party's supplier-account karat is irrelevant): custody -q, a NEW immutable source pool +q (one pool per handover, never merged, even for the same party, karat and custody; the unique `handover_pool_id` lets one pool have ONE original handover), no second Scrap deduction and no cash, making, supplier payable, shop-lot or inventory movement. A normal return names ONE pool, any positive quantity up to that pool's balance (partial returns allowed, no cross-pool allocation), into the ORIGINAL custody, which must be OPEN (reopening is explicit). Custody arithmetic is defined once in `services/custody_rules.py`: issued - returned - melted + heat_delta - supplier_settled - held_out + held_back over effective entries (a reversed entry stops counting); the typed link `market_trip_logo_hold_links` carries the karat and quantity, and the composite keys tie pool, party, karat, ORIGINAL trip, document kind and quantity together. The holding ledger counts every signed movement, a reversal being an explicit compensation row (`HANDOVER_REVERSAL` / `RETURN_REVERSAL`) while the original stays counted. Reversal goes through `CorrectionService` on the custody entry (rights `INTERMEDIARY_HOLDING_REVERSE` + `MARKET_TRIP_MANAGE` + `TRANSACTION_CORRECT` and a reason, OPEN custody) and is decided by arithmetic only, with no blanket newest-first rule: a handover reverses only while its pool still holds its full initial quantity; a return reverses only while custody LOGO, computed without that return, stays non-negative (returned grams that were reused block it); the resulting holding balance must stay between zero and the pool's initial quantity; a double reversal is refused. Issue and heating reversals keep their existing dependency rules plus the same non-negative custody test; pooled custody is never described as tracing an individual issue or heated piece. Rights, locking (trip -> party -> pool, every decision a CURRENT locking read, nested reversal filters included), exact replay before availability, bounded cross-flow request keys (IH, IG, custody entries and direct supplier settlements all conflict on a shared key) and the holding-history role/deletion guards (LOGO pools and documents count, reversed ones too) are those of 048. A custody may close with an external LOGO holding (its own outstanding is zero) and the wording never implies the gold returned. MELT/UNCLASSIFIED handover, sales, purchases, cash holdings, measured receipts and transfers between intermediaries or custodies are NOT implemented (supplier settlement of LOGO holdings exists since 050).
- **Supplier gold settlement from intermediary holdings (migration 050, `SGxxxxxx`, sources `HOLDING_LOT` / `HOLDING_LOGO`, «تسوية ذهب لمورد من رصيد المسوقجي»):** the OWNER settles ONE supplier with shop-owned gold already held by a مسوقجي: ONE holding source (a 048 assayed-lot pool or a 049 master-karat LOGO pool), ONE supplier and ONE positive quantity of AT MOST THREE decimal places per SG document; partial and repeated allocations are allowed, the same source may settle several suppliers one after another, sources are never merged and no multi-supplier batch exists. One transaction posts the immutable SG header, ONE negative supplier gold movement in the supplier's dedicated karat, exactly ONE GOLD leg (the database refuses a SCRAP leg) and ONE explicit signed holding movement (`SETTLEMENT`, no IH/IG document); nothing moves in custody, Scrap, cash, bank, making, inventory or the shop lot ledger, and the originating custody is neither locked nor reopened (it may be CLOSED). Credit (precision-50 Decimal, the posted figure derived once, straight from the raw result, 0.001 g ROUND_HALF_UP; the six-decimal analytical value and the posted-minus-analytical difference are stored separately): LOGO = q x source karat / supplier karat; lot = q x fineness / 1000 x 24 / supplier karat. Excess quantity, excess precision, a recipient without the supplier role or karat and a credit that rounds to zero are refused atomically; an unknown assay is never inferred. The header keeps the ORIGINAL full-lot weight, assay and melt origin apart from the delivered grams, and the holder, pool and quantity are tied to the holding ledger by composite keys. Physical declaration (lot pools only, stored immutably on the SG header, validated by the service, NEVER preselected): `WHOLE_ORIGINAL_BAR` («تم تسليم نفس السبكة كاملة دون تجزئة») needs the full original quantity, a full balance AND no allocation history of the pool; `GRAM_ENTITLEMENT` (gram entitlement, physical condition unknown) and `PHYSICALLY_DIVIDED` (explicitly confirmed) are always allowed, so a full quantity alone never proves a whole-bar delivery. Allocation history (any GRAM_ENTITLEMENT / PHYSICALLY_DIVIDED SG, reversed ones included) is permanent: it blocks the normal whole-bar return («تم استلام نفس السبكة كاملة دون تجزئة» stays the return's wording) and the `LOT_HOLD` reversal until a future physical-receipt process exists [superseded: new LOT physical receipts are now disabled; the owner-approved policy at the top of this file governs the whole-bar return, including the reviewed owner declaration for a reversed physical event], and the remaining balance is never offered as a whole bar. Whole-delivery history alone blocks neither the confirmed normal return nor a repeat delivery, but any settlement history blocks the `LOT_HOLD` reversal (the refusal names the SG) because that reversal would bypass the physical verification. LOGO pools are pooled grams: no declaration, and their partial-return rules (049) are unchanged. Rights: posting needs `INTERMEDIARY_HOLDING_MANAGE` + `SUPPLIER_GOLD_SETTLE` (no new permission, no `MARKET_TRIP_MANAGE`), rechecked in the transaction; reversal is ONE canonical correction on the SG (`CorrectionService`, source `SUPPLIER_GOLD_SETTLEMENT`) from the holder page or the supplier page alike and needs `TRANSACTION_CORRECT` + `SUPPLIER_GOLD_SETTLE_REVERSE` + `INTERMEDIARY_HOLDING_REVERSE` (`plan.extra_permissions`: EVERY extra right of a source is checked); it restores the STORED supplier grams/Eq21 and the stored delivered grams (explicit `SETTLEMENT_REVERSAL` compensation counted beside the original), checks the resulting pool balance (0 <= balance <= initial) and its 'already reversed' decision is a CURRENT locking read. The holder may be inactive (what it holds can still be settled; new handovers to it stay refused); an inactive recipient and a payable crossing into supplier credit are allowed; a holder that is also the recipient (a dual-role party) needs a service-enforced confirmation that is part of the request digest, and its holdings and its supplier payable stay separate. Lock order: lot (lot pools) -> holder and recipient party IDs in ASCENDING order (once when equal) -> holding source pool, every decision a CURRENT locking read. One request key and hash cover the payload; exact replay (reversed ones included) returns the original linked records before any availability is derived, and a key used by an IH/IG document, a custody entry or another settlement is a REQUEST_CONFLICT. Measured physical receipts, sales, purchases, cash holdings, transfers between intermediaries and multi-supplier batches are NOT implemented.
- **Physical receipts from intermediary holdings (migration 051, `HRxxxxxx` documents, `RPxxxxxx` receipt pieces, «استلام فعلي من المسوقجي»):** **SUPERSEDED IN PART by the owner-approved intermediary LOT return policy at the top of this file (current rule): NEW LOT physical-receipt postings are DISABLED; legacy HR/RP history, exact replay of an already successful request and valid canonical reversals remain supported; a reversed physical-division or physical-receipt event requires the reviewed owner declaration before an otherwise eligible whole-bar return. The text below is the HISTORICAL migration-051 design and its "the OWNER receives ... one NEW receipt piece per document" posting description no longer describes what the application accepts.** Historical design: the OWNER receives MEASURED physical grams of ONE assayed-lot (048) pool that has PERMANENT allocation history (a GRAM_ENTITLEMENT / PHYSICALLY_DIVIDED settlement ever, reversed ones included); a pool without it keeps the 048 intact return and is refused with `USE_NORMAL_RETURN`. One pool and one NEW receipt piece per document; sequential partial receipts are allowed; quantities are positive Decimal with at most three decimals; the measured grams must EQUAL the consumed holding grams (one weight column: a mismatch is refused atomically and no discrepancy record exists); unreceived grams stay in holdings (never cleared, never written off). The piece is placed IMMEDIATELY in SHOP receipt stock (an insert-only ledger `receipt_stock_movements`: RECEIVE / RECEIVE_REVERSAL; no locationless asset): visible on the party page, apart from Scrap, inventory, shop lots and karat totals, and UNAVAILABLE for sale, settlement, issue and melt (no such path exists; any future consumer must lock the piece first, take a current stock read and post a negative movement, which is NOT automatic). The piece has a new identity with immutable provenance (receipt, pool, holder, lot, melt origin); the original-bar identity is never restored, allocation guards stay in force and a reversed receipt never reopens the whole return. The first slice records an UNKNOWN assay only: no fineness, no pure-gold figure, no inherited source assay, no new assay permission (recorded/later assay evidence is a separate design); a receipt transfers measured grams and does not certify purity equivalence or discharge an unmeasured purity discrepancy. The pool ledger gets explicit `RECEIPT` / `RECEIPT_REVERSAL` movements (originals stay counted). Rights: INTERMEDIARY_HOLDING_MANAGE to post, rechecked in the transaction; reversal is ONE canonical correction on the HR document (`CorrectionService`, source `HOLDING_RECEIPT`) needing TRANSACTION_CORRECT + INTERMEDIARY_HOLDING_REVERSE; it locks lot -> holder -> pool -> piece, requires the FULL received stock still present, removes the stock and restores the stored holding grams atomically and checks conservation (stock 0, 0 <= pool balance <= initial). Exact replay returns the original HR/RP even after reversal; a key used by another payload, actor, IH/IG/SG document or custody entry is REFUSED. The origin custody is never locked or reopened. All shape CHECKs use explicit AND/OR branches with IS NULL / IS NOT NULL. Direct intact-to-shop returns, recorded assays, discrepancy resolution and any use of receipt stock are NOT implemented. [Still true; and new physical-receipt postings are disabled by the current owner policy.]
- **Marketer custody account (migration 055, `MH`/`MR`/`MB`/`MS` documents, «عهدة المسوقجي»):** the shop's CASH and 21K-ONLY physical gold held in trust by a party with the intermediary role, kept strictly apart from every supplier ledger (a dual-role party's supplier account is never read or written except by the explicit `HOLDING_MKT` supplier gold settlement, credit = grams x 21 / supplier karat, with a self-settlement confirmation). Immutable signed ledgers `marketer_cash_movements` / `marketer_gold_movements`; cash handover/return move Main Safe, gold return moves Scrap, purchase/sale are priced in EGP with a mandatory counterparty and a 10% SHOP_BUY warning; reuses `INTERMEDIARY_HOLDING_MANAGE/VIEW/REVERSE` (+ `SUPPLIER_GOLD_SETTLE` for settlement); reversal is a full `RVxxxxxx` (`MARKETER_DOCUMENT`) decided by non-negative arithmetic. Gold enters only by purchase; trip LOT/LOGO pools (048/049) are not bridged. UI: separated section in the party page (`SuppliersPage.qml`). Stabilization: the marketer's 21K grams are included in the consolidated Gold Position and in the party page's held-gold summary (each counted once, listed as a source of its own; cash never); post-commit signals (`balancesChanged` / `positionChanged` / `partyChanged` / `supplierChanged`) are wired in `main.py`, decided by the operation and never by a page hint; every INSERT of the custody / holding / receipt / settlement / supplier-payment flows refuses a request key that a marketer document owns (`repositories/request_keys.py`), and the marketer flows refuse keys of those flows. There is NO cross-table unique constraint: concurrent reuse of one key in two different flows is not proven impossible by these checks (real-MySQL verification and a shared key registry are open items).
- **Day close and persisted stocktake (2026-11-08):** (1) the barcode stocktake count (scope + scanned ids) is saved to `LOCALAPPDATA\masGold v2\stocktake_session.json` after every scan (`StocktakeViewModel._persist` / `_restore`, real application only, or an injected `session_path` in tests) and re-applied on the CURRENT stock when the screen is opened after a restart; pieces gone meanwhile are dropped and said so; reset/approval deletes the file. (2) «🔒 إقفال اليوم» on the cash page (`DayCloseService`, `DayCloseRepository`, `DayCloseViewModel`, `dayCloseDialog` in `qml/Main.qml`, right `DAY_CLOSE` for OWNER / MANAGER / CASHIER rechecked in the transaction): the employee types the counted cash, the program derives the expected cash (ledger sum of the ACTIVE cash accounts, banks shown apart) and the difference, a non-zero difference needs a reason, the unfinished DRAFT invoices are shown; the insert-only record `day_closes` (migration 100, applied to `masgold_prod` on 2026-11-08 (level 99 -> 100, verified backup taken first), NOT applied to DEV `masgold_v2` or rehearsal; a database below 100 makes the dialog answer «قاعدة البيانات تحتاج تحديث 100») moves no money. Tests: `tests/test_day_close.py` (service fakes + real clicks at all sizes), `tests/test_stocktake_persist.py`. Not built yet: bank / InstaPay matching, per-piece card, catalog, reservations, repair orders, market shopping list (each needs the owner's business rules first).
- **Making cost applies to every piece (2026-11-08, owner, no migration):** in the making-profit report (`ReportService._makingprofit`) and the KPI report (`kpi_rules`) the CURRENT cost of the piece's letter now applies to EVERY piece, in stock or sold, old or new (the replacement piece costs the new making); the piece's stored `acquisition_making_per_gram` is only the fallback for a letter with no cost set, then the default 300 EGP/g as an estimate. Changing a letter cost therefore moves the margins of past periods too (accepted by the owner; a dated cost history would be a later migration). Stock entry still freezes the cost into the piece, unused by these two reports while the letter has a cost. Tests: `MakingProfitTests`, `KpiCostPriorityTests` in `tests/test_report_service.py`.
- **Bar fee, custody-settlement reversal and adjustment reversal (2026-11-08, owner):** (1) the bar fee «أجر السبيكة» (`MarketTripService._post_bar_fee`) goes to the custody cash ONLY, for a supplier as for a marketer: no supplier making movement and no SUPPLIER_MAKING_PAY right; migration 101 relaxed `chk_mtbf_party` (fees posted before it keep their stored deduction and their reversal restores it). (2) A supplier gold settlement delivered from market custody (CUSTODY_GOLD / CUSTODY_LOT / TRIP_BAR) is also reversed from the supplier page: `SupplierGoldSettlementService.reverse` finds its custody entry and calls the canonical `CorrectionService` reversal of that entry (same rights and custody rules, no second path). (3) Supplier account differences (096) have a «عكس» button on their gold / making ledger rows: `SupplierPurchaseService.reverse_account_adjustment` posts the EXACT opposite adjustment row that names the original (`reverses_id`, migration 102, unique), mandatory reason, OWNER only (`SUPPLIER_ACCOUNT_ADJUST`), one reversal per original and a reversal is never reversed; the direction buttons are named «علينا» (added to the supplier balance) and «لنا» (deducted from it). Migrations 101 and 102 were applied to `masgold_prod` on 2026-11-08 (levels 100 -> 102, verified backups first); 102's first attempt failed on a CHECK over the auto-increment id and was re-run after removing it (its guarded steps are re-run safe). NOT applied to DEV `masgold_v2` or rehearsal. Tests: `tests/test_bar_fee_custody_only.py`, `tests/test_supplier_custody_settlement_reverse_here.py`, `AdjustmentReversalTests` and the real-click test in `tests/test_supplier_account_adjustment*.py`.
- **Bar fee without a party; a sold piece comes back with its barcode (2026-11-08, owner):** (1) the bar fee dialog no longer asks for a supplier / marketer: the fee is custody cash only; migration 103 made `market_trip_bar_fees.party_id` / `party_kind` nullable (applied to `masgold_prod`, level 103; the service still accepts an optional party). (2) A piece that was SOLD (typically on an old-program invoice) and comes back is restocked as the SAME piece with the SAME barcode through the existing button «إعادة القطعة للمخزون» on the piece history card (`StocktakeAdjustService.restore_piece`, right STOCKTAKE_ADJUST, mandatory reason, movement `RETURNED_ITEM_RESTOCKED` with reference `PIECE_RESTOCK`, audit): the old invoice and the customer balance are NOT touched; a piece sold on a CURRENT finalized, not fully reversed invoice is refused with `USE_RETURN` (Return / Exchange carry the money). A barcode is still never given to a second piece.
- **Sold piece restocked from the Scrap (2026-11-08, owner clarification):** the piece a customer sold back to the shop goes through the Scrap first, so restocking a SOLD piece with an intact barcode (`StocktakeAdjustService.restore_piece(piece_id, reason, scrap_karat_id)`) also takes the piece's exact grams out of the Scrap karat the owner CHOOSES in the dialog (preselected to the piece's own karat; mandatory; counted `MANUFACTURE_OUT`, refused above the AVAILABLE Scrap with `SCRAP_SHORTAGE`; needs `STOCKTAKE_ADJUST` and `OLD_GOLD_MANAGE`). A piece with no barcode or a damaged one is not restocked this way: it enters as a NEW piece with an automatic barcode through the normal stock entry (from the Scrap if it came back as Scrap). Tests: `tests/test_stocktake_adjust.py`, real-click `tests/test_inventory_stock_qml.py`.
- **Phone camera scanner: speed, orientation, error sound (2026-11-08, `webapp/static/app.js`, not measured on a real phone):** the camera loop no longer waits a fixed 150 ms and no longer holds the next picture while the desktop answers (each picture is read as soon as the previous one is done; the answer plays its own sound when it arrives); the browser's own `BarcodeDetector` is used when it exists (any angle), otherwise ZXing reads the upright picture most of the time and, in turn, turned 90 degrees and tilted 45 degrees either way (`ORIENTATIONS`); continuous autofocus is requested. Sounds are real `<audio>` elements built from generated WAV data (they ignore the iPhone silent switch and keep playing while the camera is on), primed inside the «تشغيل الكاميرا» tap, with Web Audio as a fallback; the error buzz is longer and louder. Only a quick pass per picture (no TRY_HARDER).
- **Payroll «المرتبات» (2026-11-08, migration 104, applied to `masgold_prod` only, level 104; verified backup `masgold_prod_20261108_231111` taken first; NOT applied to DEV `masgold_v2` or rehearsal):** a section of its own under مراجعات المدير (button «💵 المرتبات», `payrollDialog` in `qml/Main.qml`), outside the expenses. Per employee: a monthly salary (`employee_salary_rates`, rate in force = latest effective month <= the month; a paid slip keeps its own figures), bonuses / penalties of a month (`payroll_adjustments`, reason mandatory, an unpaid one can be voided, never deleted) and ONE slip per employee per month (`payroll_slips`). Paying (`PayrollService.pay`, right `PAYROLL_MANAGE`, OWNER only, rechecked in the transaction) posts ONE real withdrawal of the NET = salary + bonuses - penalties - advances deducted from the chosen cash / bank account through `FinancialAccountService._finalize` (category SALARY, hooks `before` / `link`), in the SAME transaction as the slip, the settled adjustments and the advance repayments (oldest advance first, `cash_advance_repayments` tied to the salary operation: 104 replaced its unique key by (advance_id, operation_id)). Net must be > 0; an exact retry returns the first result. Reversing the salary operation (RV) voids the slip (derived, like advances), brings the advances back and frees its bonuses / penalties for the next slip. Cash flow report: a SALARY movement is its own line in the «غير الإيراد والمصروف» table, never a recorded expense; the KPI also leaves out «مرتب» and «فرق فواتير» items. Tests: `tests/test_payroll.py`, real-click `tests/test_payroll_ui.py`, `CashflowSalaryTests`.
- **«الإدارة» area (2026-11-13, owner, UI only, no migration):** the sidebar button formerly named «مراجعات المدير» is now «الإدارة», at the BOTTOM of the sidebar under the separator where the Settings button used to be (page index 10; `reviewsNavButton`, `ManagementReviewPage`, the lock screen and the Settings password card use the new name; the review list inside it, the `MANAGEMENT_REVIEW` right and the services' messages keep the word «مراجعات»). The suppliers page (index 14) and the market custody page (index 15) LEFT the sidebar (`suppliersNavButton` / `marketTripsNavButton` are gone) and are sections of the area next to the reports, opened by the buttons `reviewsOpenSuppliers` (`supplierVM.canView`) and `reviewsOpenMarketTrips` (`marketTripVM.canManage`); both sit behind the same manager lock (`managerGate.gated` now includes 14 and 15) and carry a back button (`suppliersBack`, which returns to the Settings instead when the page was opened from the Settings card «إدارة الموردين»; `marketTripsBack`). The page indices are unchanged. The area's header is a wrapping `Flow` so the nine section buttons never run off the screen at 1080x680 (`ActionButton.implicitHeight` 28 because a Flow ignores `Layout.preferredHeight`). The SETTINGS left the sidebar too (owner, same day): the button `reviewsOpenSettings` opens page 6 and its sub-pages 7 factories / 8 item types / 9 categories / 13 users sit in the same area behind the same lock; `root.inAdminArea` in `qml/Main.qml` is now the ONE list of the area's pages (lock + sidebar highlight), the settings page has a back button `settingsBack` to the area, and the printer choice is the ONE exception (owner, same day): when the cashier is sent there by «حفظ وطباعة» without a bill printer (`SettingsPage.fromBilling`, set only by Billing's `printingSettingsRequested`) the lock is skipped (`managerGate.gated` is false for page 6 + `fromBilling` + `openSection == "printing"`), only the printing section is shown, and «رجوع» / Esc (`leaveSection()`) returns to the invoice page; leaving the page clears the flag, so the settings stay locked for everything else. Tests: `tests/test_manager_gate.py` (lock on 14 / 15 / 6-9 / 13, settings open / back with real clicks, no sidebar settings button, buttons on screen at the three sizes), real-click `tests/test_supplier_ui.py` and `tests/test_market_trip_ui.py`.
- **Retired category «فرق فواتير» (2026-11-13, owner, no migration, no data rewritten):** the owner's category «فرق فواتير» (also spelled «فرق فاتورة» / «فرق فاتوره») was a TEMPORARY workaround until the program could record a settlement that differs from the invoice (Settlement Correction now does). It is retired by NAME in code (`services/cash_category_service.py` -> `RETIRED_NAMES` / `is_retired`, because the owner's categories have generated codes that differ between databases): it is not listed in Settings «تصنيفات المصروفات والإيرادات» (`CashCategoryService.categories`), a new category / rename to that name is refused (`NAME_RETIRED`), `financial_account_service.category_table` marks it inactive (so «حركة نقدية جديدة» neither offers nor accepts it, `TYPE_INVALID`), the cash-flow report (`ReportService._cashflow`) puts it (and its reversals) in the not-income-or-expense bucket instead of the recorded expenses, and its KPI item «فروق الفواتير» was removed from `_kpi_extras` (`kpi_rules.invoice_diff_item` / `is_invoice_difference` stay as unused helpers). The existing operations (12 withdrawals, 144,675 EGP on `masgold_prod` at the time) are untouched: they keep their cash movements and their name in the history (a used category cannot be deleted); cleaning them (correcting the invoices through FC and reversing the withdrawals) is a separate owner decision. Tests: `tests/test_cash_categories.py`, `CashflowRetiredCategoryTests` in `tests/test_report_service.py`, `tests/test_kpi_early_items.py`.
- **WhatsApp invoice PDF: old look, and the send button stays reachable (2026-11-13, owner, no migration):** `InvoicePrintService.share_pdf` (the file handed to WhatsApp from سجل الفواتير) always composes in the old program's look (`bill_overlay.STYLE_PROTOTYPE`) through `compose(invoice, style=...)`, whatever style the workstation prints its PAPER with (Settings > الطباعة keeps governing the printed paper). The answer line of a print / WhatsApp hand-over («تم حفظ الملف في ... واضغط Ctrl+V ثم Enter») used to sit INSIDE the button row of `qml/InvoiceHistoryPage.qml`; its length pushed the buttons off the screen (the send button was at x = -369) and widened the page, so after one send the invoice could not be sent again. It is now its own wrapped line under the button bar (`reprintNote` / `historyReprintMessage`), and `detailScroll` anchors below it. Tests: `SharePdfTests` in `tests/test_invoice_print_service.py`; real-click `test_whatsapp_button_hands_the_pdf_over_by_a_real_click_at_every_size` in `tests/test_invoice_history_qml.py` (the REAL long answer, three sends in a row, every button inside the window at 1440x900 / 1280x800 / 1080x680).
- **A long customer name is never cut on the bill or the PDF (2026-11-13, owner, no migration):** the header name used to be elided with "..." when it did not fit its cell (the third name of «فاطمه محمد عبدالتواب ...» was missing). `services/bill_overlay.py` -> `Op.grow` / `Op.max_lines` / `Op.wrap_pt` and `paint()` -> `fit_long_text`: a name that does not fit first uses the free space to the LEFT of its cell (up to `C_NAME_CELL`, right-aligned; the barcode ends near 115 mm), then steps down to its floor, then WRAPS onto a second line (`NAME_WRAP_PT_PROTOTYPE` 9 pt / `NAME_WRAP_PT_VARIANT_C` 9.5 pt, the label stays on the first line, the block is centred a little lower so it stays clear of the paper's top edge and of the address row); only a single word wider than the whole width could still be elided. A name that fits prints exactly as before (same cell, same centring), the ops' geometry (and so `validate()` against the gold lines) is unchanged because the widening happens only while painting, and both styles (prototype and variant C) use it, so the printed paper and the PDF agree. Tests: `LongCustomerNameTests` and `HeaderSeparatorTests.test_runs_paint_right_to_left_in_order` in `tests/test_invoice_print_service.py`.
- **New barcode label sheet (2026-11-13, owner-approved, no migration; NOT yet printed on real label stock):** `services/label_renderer.py` draws the whole 641 x 310 dot sheet (two rat-tail tags, each a DATA face and a LOGO face either side of the fold, die-cut read from the owner's photo of the blank sheet) with Qt and `PrototypeZplPrinter._build_zpl_label_new` sends it as ONE `^GFA` `:Z64:` graphic under the SAME printer settings block as before (`~SD24`, `^MD15`, `^PR5` unchanged, a separate decision). Data face (160 x 102 dots): weight / factory / karat row, Code 128 (subset C for digits, B otherwise, 1-dot bars, centred, 46 dots high, 79 dots wide for the usual 8 digits instead of Code 39's 129), number and ring size under it, everything inside 10-dot margins; logo face: the old AD mark bitmap (unchanged, cropped 96 x 48) with «مجوهرات ماس» in Cairo ExtraBold (replaces «مجوهرات داوود»), the category letter in a corner at 18 px. Cairo ships with the program (`resources/fonts/Cairo.ttf`, SIL OFL note beside it; `resources` is already bundled by `tools/build/masgold.spec`). The Z64 CRC (CRC-16 CCITT/XMODEM over the base64 text) was verified against the three strings of the old label. A barcode wider than the face (about 16+ characters) is refused with a message instead of printing an unreadable code. The old ZPL layout stays reachable per workstation: Settings > الطباعة > «شكل اللاصق» (`labelStyleCombo`, QSettings `printing/label_style` = `v2` default | `legacy`, `PrinterSettingsViewModel.labelStyle` / `setLabelStyle`, `PrototypeZplPrinter(style=...)`). Rejected after review with the owner: QR (the counter scanner reads the 1D barcode fine; a real QR was rendered and decoded with `webapp/static/zxing.min.js` only as a demonstration) and moving to module 2 bars (needs a 300 dpi printer or a wider tag). Open: printed test sheets at lower darkness (14 / 18 / 22) and a real scan test; the AD mark next to the «ماس» name is the owner's choice. Tests: `tests/test_label_renderer.py`, `tests/test_printer_settings.py`, real-click `test_settings_printing_has_two_independent_printers` in `tests/test_invoice_page_qml.py`.
