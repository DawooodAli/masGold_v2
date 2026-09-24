# masGold v2 — Project Instructions

## 1. Project

masGold v2 is an internal management system for a gold jewelry shop.

Technology:
- Python
- PySide6
- Qt Quick / QML
- MySQL

Architecture:

QML
→ ViewModels
→ Services
→ Repositories
→ MySQL

QML must never contain SQL or database business logic.

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

## 3. Current development priority

CURRENT PRIORITY:

CANONICAL BILLING V2 (on the `invoices` family)

Customers, Gold Price Engine V1 and the controlled Gold Price AUTO
activation are complete.

The old `billing_*` family stays frozen as read-only history.

Cash comes later.

Do not extend Cash/Bank unless explicitly requested.

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

Existing transaction codes:

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

Customer module should eventually support:

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

Do not implement Dead Cash actions until explicitly requested.

Read-only display is safe.

---

## 17. Billing — CURRENTLY FROZEN

Do not continue Billing unless explicitly requested.

Future Billing must support in ONE invoice/window:

1. customer buys new gold only
2. customer sells old gold only
3. customer sells old gold and buys new gold in the same transaction

Old gold bought from a customer enters:

old_gold_movements

using:

CUSTOMER_BUY_IN

Do not create inventory items for purchased old gold.

Exchange settlement concept:

new-gold sale total
− old-gold purchase total
= net settlement

Net may mean:

customer owes shop

or

shop owes customer

Billing finalization must eventually be atomic.

---

## 18. Important Billing warning

The database currently contains both families:

billing_invoices
billing_sale_lines
billing_old_gold_lines

AND

invoices
invoice_lines
invoice_payments
invoice_customer_snapshots
invoice_price_snapshots
invoice_documents
invoice_events

Do NOT assume which family is canonical.

Before future Billing work:
inspect both architectures and decide deliberately.

Do not create a third invoice architecture.

---

## 19. Gold prices

Gold prices must eventually support frozen invoice snapshots.

Do not assume current market price when reviewing historical invoices.

Important existing tables include:

gold_price_quotes
active_gold_prices
gold_price_sources
gold_price_activations

Do not change price architecture unless explicitly requested.

---

## 20. Payments / Cash

Invoice and payment are separate concepts.

Do not treat invoice total as cash automatically.

Future payment methods may include:

- cash
- bank
- card
- InstaPay
- mixed
- remaining customer balance

Existing database objects include:

payment_methods
invoice_payments
cash_accounts
cash_movements
bank_accounts
bank_movements

Cash implementation is not current priority.

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