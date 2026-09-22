# masGold v2 - Project Instructions

## Project
masGold v2 is an internal gold jewelry shop management system.

Stack:
- Python
- PySide6
- Qt Quick / QML
- MySQL

Architecture:
QML -> ViewModels -> Services -> Repositories -> MySQL

QML must never contain SQL or database business logic.

## Current priority

Current development priority is CUSTOMERS.

Billing is temporarily frozen.
Cash comes after Customers and Billing architecture are stable.

## Working rules

Before changing code:
1. Inspect the actual existing files.
2. Do not assume database schema.
3. Do not create duplicate systems.
4. Preserve working functionality.
5. Do not modify production/legacy data.
6. Do not run migrations unless explicitly approved.
7. Never delete ledger history to fix accounting mistakes.
8. Prefer reversal/correction entries.
9. Resolve movement type IDs by code, never hard-coded numeric IDs.
10. Use DB transactions for multi-ledger operations.

## UI rules

Employee-facing UI is Arabic.

All Arabic UI:
- Cairo font
- RTL
- right-aligned

Form convention:
[value/control] <- [Arabic label on right]

Do not enable global LayoutMirroring because the physical sidebar must remain on the right.

Visual style:
- dark background
- gold accent
- thin borders
- compact rounded cards
- right-side physical navigation menu
- brand: مجوهرات ماس

Internal Python/QML/database identifiers remain English.

## Inventory

inventory_item_id is permanent identity.

Barcode:
- optional
- replaceable
- never permanent identity

inventory status:
- IN_STOCK
- SOLD
- SCRAPPED
- REMOVED

stock classification:
- NEW_STOCK
- OLD_STOCK

OLD_STOCK is still itemized inventory.

## Old Gold - CRITICAL

OLD_GOLD / كسر is NOT itemized inventory.

It is a bulk gram ledger by karat.

Do not give old gold:
- barcode
- item type
- factory
- category
- size

old_gold_movements is append-only.

Positive weight_delta_grams = gold enters shop.
Negative weight_delta_grams = gold leaves ledger.

Customer old-gold purchase movement code:
CUSTOMER_BUY_IN

Manufacturing consumption:
MANUFACTURE_OUT

A manufactured result becomes a normal NEW_STOCK inventory item.

## Customers

Customer ID is permanent identity.

Customer names are NOT unique.

Current customer types:
- REGULAR
- VIP

Customer balance comes from customer_transactions / vw_customer_balances.

Positive balance:
CUSTOMER_OWES_SHOP

Negative balance:
SHOP_OWES_CUSTOMER

Zero:
SETTLED

Current transaction codes:
- SALE_BALANCE_DUE
- BUY_BALANCE_DUE
- CUSTOMER_PAYMENT
- SHOP_PAYMENT
- CUSTOMER_ADVANCE
- ADVANCE_APPLIED
- DEAD_CASH_WRITE_OFF
- DEAD_CASH_REVERSAL
- MANUAL_ADJUSTMENT
- LEGACY_ADJUSTMENT

Never create a second mutable customer balance field.

Customer module should support:
- search
- add/edit
- customer type
- active/inactive
- current balance
- balance semantic status
- last activity
- financial history
- flags
- Dead Cash history/warnings
- invoice history later
- payment/cash history later

## Dead Cash

Dead Cash must preserve accounting history.

Write-off and reversal must be explicit auditable transactions.

Never delete the original customer transactions.

## Billing

Billing is currently frozen while Customers is completed.

Future billing must support:
1. customer buys new gold only
2. customer sells old gold only
3. customer sells old gold and buys new gold in one invoice

Future billing must be atomic.

Do not extend billing schema until the canonical invoice architecture is reviewed.

## Safety

If uncertain about:
- database schema
- accounting direction
- existing architecture
- business meaning

stop and inspect instead of inventing behavior.