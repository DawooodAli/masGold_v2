# masGold v2 — Project Instructions

## 1. Project

masGold v2 is an internal management system for a gold jewelry shop.

Technology:
- Python
- PySide6
- Qt Quick / QML
- MySQL

Architecture:

`QML → ViewModel → Service → Repository → MySQL`

QML and ViewModels must never contain SQL. Services own authoritative business
calculations and lifecycle validation; repositories own persistence. Use Decimal
for money/weight and decimal strings for BIGINT IDs crossing into QML. Protected
writes obtain their actor from AppSession, never arbitrary QML-supplied identity.

---

## 2. Critical working rule

ALWAYS inspect the actual current project files before making changes.

Do not assume that code discussed previously has already been installed.

The files currently present in the workspace are the source of truth.

Before changing database-dependent code:
- inspect the actual schema
- inspect existing foreign keys
- inspect existing movement-type codes
- reuse existing tables and architecture

Do not invent missing schema.

---

## 3. Current implemented baseline

Gold Price Engine, Billing V2, Invoice History, Exchange V1, Returned Item Inspection,
Return V1, Reversal / Correction V1, Customer Account V2 and Customer Invoices are
implemented and committed. Authentication,
Customers, Inventory and Old Gold foundations are implemented. Controlled Gold Price
AUTO activation is accepted; do not change AUTO/MANUAL without explicit approval.

The canonical invoice family is `invoices`; `billing_*` is frozen read-only history.
Payment-account posting and invoice printing are implemented. Do not extend Cash/Bank
or other modules beyond the requested task. See the lifecycle baseline below.

---

## 4. Change workflow

Before implementing a non-trivial change:

1. Inspect relevant files.
2. Explain what already exists.
3. Identify exactly which files/classes/methods need changing.
4. Propose the change.
5. Only then modify files.

When reporting changes, use:

File → Class → Method/component

Example:

repositories/customer_repository.py
→ CustomerRepository
→ search_customers()

Do not give vague placement instructions.

---

## 5. Database safety

This application contains financial and inventory history.

Rules:

- Never delete ledger history to correct a mistake.
- Use reversal/correction movements.
- Never modify legacy production data.
- Never invent missing historical information.
- Never run destructive SQL without explicit approval.
- Never run database migrations without explicit approval.
- Never create duplicate tables when suitable tables already exist.
- Resolve movement types by stable CODE, not numeric ID.
- Use transactions for operations touching multiple ledgers/tables.

Do not hard-code IDs such as:

movement_type_id = 1

Instead query by code.

---

## 6. UI rules — LOCKED

Employee-facing UI is Arabic.

All Arabic UI must use:

- font: Cairo
- RTL text direction
- right alignment

This includes:
- Labels
- TextFields
- TextAreas
- ComboBoxes
- dialogs
- buttons
- tables
- placeholders
- titles

Form convention:

[value/control] ← [Arabic label on right]

Do NOT enable global LayoutMirroring because the physical sidebar must remain on the right.

Visual style:

- dark background
- gold accents
- thin borders
- compact rounded cards
- serious jewelry-shop appearance
- physical navigation sidebar on the RIGHT

Brand:

مجوهرات ماس

Internal Python/QML/database identifiers remain English.

---

## 7. Terminology

Inventory = المخزون

Billing = الفواتير

Cash = النقدية

Old Stock = بضاعة قديمة

Old Gold = كسر

Factory = المصنع

Item Type = الصنف

Category = الفئة

Dead Cash = الديون المعدومة / الرصيد المشطوب depending on context

---

## 8. Inventory identity

Permanent inventory identity:

inventory_item_id

Barcode is:

- optional
- replaceable
- not permanent identity

An inventory item can exist without a barcode.

Do not design inventory logic around barcode as primary identity.

Employee attribution is required for new inventory writes.

---

## 9. Inventory states

stock_classification:

- NEW_STOCK
- OLD_STOCK

inventory_status:

- IN_STOCK
- SOLD
- RETURNED_PENDING
- CONVERTED_TO_OLD_GOLD
- SCRAPPED
- REMOVED

OLD_STOCK means an individual physical inventory item.

OLD_STOCK is NOT the same thing as OLD_GOLD.

---

## 10. Old Gold / كسر — CRITICAL

OLD_GOLD is bulk gold measured in grams by karat.

It is NOT itemized inventory.

Old Gold does NOT have:

- barcode
- item type
- category
- factory
- size

Primary ledger:

old_gold_movements

Positive weight_delta_grams:
gold enters old-gold balance

Negative weight_delta_grams:
gold leaves old-gold balance

Movement codes are resolved dynamically.

Important codes:

CUSTOMER_BUY_IN
= shop buys old gold from customer

MANUFACTURE_OUT
= old-gold grams consumed to manufacture new stock

A manufactured result becomes a normal inventory_items row:

stock_classification = NEW_STOCK

acquisition_source = OLD_GOLD_MANUFACTURE

Consumed old-gold weight and resulting item weight may differ.

Both values must be preserved.

---

## 11. Customer identity

Customer permanent identity:

customers.id

Customer names are NOT unique.

Never use customer name as permanent identity.

customer_code is separate and may be unique.

Current customer types:

REGULAR
VIP

---

## 12. Customer balance — CRITICAL

Customer financial balance is ledger-derived.

Source:

customer_transactions

Current derived view:

vw_customer_balances

Do NOT add a mutable balance column to customers.

Meaning:

balance > 0
= CUSTOMER_OWES_SHOP
= customer owes money to the shop

balance < 0
= SHOP_OWES_CUSTOMER
= shop owes money to customer / customer has credit

balance = 0
= SETTLED

UI should express the meaning clearly, not only show +/-.

Examples:

+8,500
→ على العميل للمحل

-3,200
→ للعميل عند المحل

0
→ الحساب متزن

---

## 13. Customer transaction types

Core customer transaction codes (not an exhaustive list of lifecycle/correction codes):

SALE_BALANCE_DUE
Purchase meaning:
unpaid amount from sale
direction INCREASE

BUY_BALANCE_DUE
shop buys from customer and still owes customer
direction DECREASE

CUSTOMER_PAYMENT
customer pays shop
direction DECREASE

SHOP_PAYMENT
shop pays customer
direction INCREASE

CUSTOMER_ADVANCE
customer deposits advance
direction DECREASE

ADVANCE_APPLIED
customer credit is consumed
direction INCREASE

DEAD_CASH_WRITE_OFF
direction DECREASE

DEAD_CASH_REVERSAL
direction INCREASE

MANUAL_ADJUSTMENT
direction EITHER

LEGACY_ADJUSTMENT
direction EITHER

Never infer direction from the English label alone.
Respect the existing ledger convention.

---

## 14. Customer subsystem

Existing important database objects include:

customers
customer_types
customer_transactions
customer_transaction_types
customer_flags
dead_cash_writeoffs
vw_customer_balances

Current customer types:

REGULAR
VIP

Customers V2 supports search, customer codes, editing, activation, profiles and
ledger-derived statements. Preserve these customer data/display requirements:

- search
- customer ID/code
- add
- edit
- activate/deactivate
- type
- mobile
- place
- notes
- current balance
- balance meaning/status
- last activity
- transaction history
- flags
- Dead Cash warning/history
- invoice history
- payment/cash history

The statement (`كشف الحساب`) and customer invoices (`فواتير العميل`) are separate,
read-only historical views. Customer Account V2 adds explicit finalized Customer
Account Settlements (`CSxxxxxx`): partial or split across multiple real payment
methods, oversettlement refused. Finalized CS records are immutable; correct mistakes
with FC/RV, never by editing or deleting the original CS.

Do not fabricate invoice or payment history if those parts are not yet connected.

---

## 15. Customer flags

customer_flags are persistent audit-style warnings/flags.

Do not delete historical flags.

Use active/inactive lifecycle where appropriate.

Dead Cash flags may link through:

related_dead_cash_id

Respect the existing unique active-flag behavior.

---

## 16. Dead Cash

Dead Cash must preserve history.

Never erase the original receivable.

Write-off:

- creates an explicit customer transaction
- creates a dead_cash_writeoffs record
- can create/activate warning flag

Reversal:

- creates a reversal transaction
- records reversal metadata
- does not delete the original write-off

Dead Cash write-off and reversal are implemented; preserve them and change them only on a
specific task.

---

## 17. Billing — Canonical Billing V2 (implemented)

Billing V2 supports sale-only, customer old-gold purchase-only and mixed invoices.
Finalization is atomic. The invoice total is authoritative; allocation across actual
payments and customer account does not change that total or derived making.

Customer old gold enters `old_gold_movements` using `CUSTOMER_BUY_IN`; never create
inventory items for that bulk purchase. Commercial net is sale total minus old-gold
purchase total. Distinguish this mixed invoice from the implemented linked after-sale
Exchange lifecycle described below.

---

## 18. Billing architecture — canonical family decided

The database contains both families:

billing_invoices
billing_sale_lines
billing_old_gold_lines
(and billing_invoice_sequences)
→ FROZEN historical/read-only Billing V1 data.
Do not write new invoices to these tables and do not alter their history.

invoices
invoice_lines
invoice_payments
invoice_customer_snapshots
invoice_price_snapshots
invoice_documents
invoice_events
→ CANONICAL architecture for Billing V2.

Do not create a third invoice architecture.

Before changing Billing V2:
inspect the actual current schema (columns, keys, constraints,
movement/transaction codes) and the existing Billing code.
Do not assume undocumented details; ask when something is unclear.

---

## 19. Gold prices

Gold prices support frozen invoice snapshots. Preserve each karat's original provider
timestamp; set-level provenance uses the newest provider timestamp.

Do not assume current market price when reviewing historical invoices.

Important existing tables include:

gold_price_quotes
active_gold_prices
gold_price_sources
gold_price_activations

Do not change price architecture unless explicitly requested.

---

## 20. Payments / Cash

Invoice totals and actual payments are separate. Implemented payment methods include
physical cash, mobile wallet, InstaPay and bank transfer, with split payments and
explicit customer-account allocation. Customer credit/unpaid balance and exchange
offset must never become fake payment rows.

Existing objects include `payment_methods`, `invoice_payments`, `cash_accounts`,
`cash_movements`, `bank_accounts` and `bank_movements`. Real payments post atomically
to their ledgers. Preserve the implemented settlement workflows; no unrelated
Cash/Bank expansion without an explicit task.

---

## 21. Printing

Do not redesign physical print dimensions without checking the existing/prototype implementation.

Barcode printing currently targets:

ZDesigner GK420t

Existing implementation uses ZPL and two jewelry labels side by side.

Preserve known working physical layout unless explicitly requested.

---

## 22. Python/QML quality

After Python changes:

- run syntax checks
- catch import errors
- preserve existing layers

After QML changes:

- preserve Cairo
- preserve RTL/right alignment
- preserve sidebar on right
- avoid duplicate component IDs
- inspect runtime errors

Prefer editing the existing complete Main.qml rather than creating disconnected duplicate screens.

---

## 23. Concurrency / transactions

Financial, inventory and old-gold operations must be safe under concurrent use.

When changing balance-sensitive code:

- use transactions
- lock appropriate rows/resources
- do not rely only on UI validation

---

## 24. Legacy data

Legacy production database/history may be incomplete.

Rules:

- preserve what can be proven
- do not invent missing customer/employee/invoice relationships
- migration must remain auditable
- legacy data should be treated carefully/read-only until migration is deliberately performed

---

## 25. Current task behavior

At the start of every new task:

1. Read this AGENTS.md.
2. Inspect actual current files.
3. Do not assume previously generated files were installed.
4. Do not make schema changes unless requested.
5. Do not expand scope beyond the requested feature.

If something is ambiguous:

STOP and ask/inspect rather than inventing business behavior.

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

- Schema chain: migrations `001–047` in `database/schema`; next migration is `048`
  unless later repository state proves otherwise. Already-applied migrations are
  immutable, including 019–046. `database/migrations/*.sql` are historical prototypes
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
