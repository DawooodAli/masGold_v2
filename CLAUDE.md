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

- Schema chain: migrations `001–050` in `database/schema`; next migration is `051`
  unless later repository state proves otherwise. Already-applied migrations are
  immutable, including 019–049. `database/migrations/*.sql` are historical prototypes
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

- **Old-gold discount:** 1.25%–5.00%, default 1.50%, for Mixed AND old-gold-only invoices.
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
- **Line endings:** do not mass-convert files and never normalize migrations (checksums). No
  `.gitattributes` is added; with `core.autocrlf=true` a policy file would cause churn. Deferred.

### Official test baseline

Accepted baseline after the Final Stabilization Pack: **1546 total = 1473 passed + 73 skipped**,
**0 failures, 0 errors**. Skipped tests are the opt-in MySQL/integration tests, which
stay opt-in and are never run against persistent DEV data casually.

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
- **Supplier gold settlement from intermediary holdings (migration 050, `SGxxxxxx`, sources `HOLDING_LOT` / `HOLDING_LOGO`, «تسوية ذهب لمورد من رصيد المسوقجي»):** the OWNER settles ONE supplier with shop-owned gold already held by a مسوقجي: ONE holding source (a 048 assayed-lot pool or a 049 master-karat LOGO pool), ONE supplier and ONE positive quantity of AT MOST THREE decimal places per SG document; partial and repeated allocations are allowed, the same source may settle several suppliers one after another, sources are never merged and no multi-supplier batch exists. One transaction posts the immutable SG header, ONE negative supplier gold movement in the supplier's dedicated karat, exactly ONE GOLD leg (the database refuses a SCRAP leg) and ONE explicit signed holding movement (`SETTLEMENT`, no IH/IG document); nothing moves in custody, Scrap, cash, bank, making, inventory or the shop lot ledger, and the originating custody is neither locked nor reopened (it may be CLOSED). Credit (precision-50 Decimal, the posted figure derived once, straight from the raw result, 0.001 g ROUND_HALF_UP; the six-decimal analytical value and the posted-minus-analytical difference are stored separately): LOGO = q x source karat / supplier karat; lot = q x fineness / 1000 x 24 / supplier karat. Excess quantity, excess precision, a recipient without the supplier role or karat and a credit that rounds to zero are refused atomically; an unknown assay is never inferred. The header keeps the ORIGINAL full-lot weight, assay and melt origin apart from the delivered grams, and the holder, pool and quantity are tied to the holding ledger by composite keys. Physical declaration (lot pools only, stored immutably on the SG header, validated by the service, NEVER preselected): `WHOLE_ORIGINAL_BAR` («تم تسليم نفس السبكة كاملة دون تجزئة») needs the full original quantity, a full balance AND no allocation history of the pool; `GRAM_ENTITLEMENT` (gram entitlement, physical condition unknown) and `PHYSICALLY_DIVIDED` (explicitly confirmed) are always allowed, so a full quantity alone never proves a whole-bar delivery. Allocation history (any GRAM_ENTITLEMENT / PHYSICALLY_DIVIDED SG, reversed ones included) is permanent: it blocks the normal whole-bar return («تم استلام نفس السبكة كاملة دون تجزئة» stays the return's wording) and the `LOT_HOLD` reversal until a future physical-receipt process exists, and the remaining balance is never offered as a whole bar. Whole-delivery history alone blocks neither the confirmed normal return nor a repeat delivery, but any settlement history blocks the `LOT_HOLD` reversal (the refusal names the SG) because that reversal would bypass the physical verification. LOGO pools are pooled grams: no declaration, and their partial-return rules (049) are unchanged. Rights: posting needs `INTERMEDIARY_HOLDING_MANAGE` + `SUPPLIER_GOLD_SETTLE` (no new permission, no `MARKET_TRIP_MANAGE`), rechecked in the transaction; reversal is ONE canonical correction on the SG (`CorrectionService`, source `SUPPLIER_GOLD_SETTLEMENT`) from the holder page or the supplier page alike and needs `TRANSACTION_CORRECT` + `SUPPLIER_GOLD_SETTLE_REVERSE` + `INTERMEDIARY_HOLDING_REVERSE` (`plan.extra_permissions`: EVERY extra right of a source is checked); it restores the STORED supplier grams/Eq21 and the stored delivered grams (explicit `SETTLEMENT_REVERSAL` compensation counted beside the original), checks the resulting pool balance (0 <= balance <= initial) and its 'already reversed' decision is a CURRENT locking read. The holder may be inactive (what it holds can still be settled; new handovers to it stay refused); an inactive recipient and a payable crossing into supplier credit are allowed; a holder that is also the recipient (a dual-role party) needs a service-enforced confirmation that is part of the request digest, and its holdings and its supplier payable stay separate. Lock order: lot (lot pools) -> holder and recipient party IDs in ASCENDING order (once when equal) -> holding source pool, every decision a CURRENT locking read. One request key and hash cover the payload; exact replay (reversed ones included) returns the original linked records before any availability is derived, and a key used by an IH/IG document, a custody entry or another settlement is a REQUEST_CONFLICT. Measured physical receipts, sales, purchases, cash holdings, transfers between intermediaries and multi-supplier batches are NOT implemented.
