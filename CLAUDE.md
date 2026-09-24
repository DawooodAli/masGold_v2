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

- QML must never contain SQL.
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

Do not push.

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
- weights where precision matters

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

Billing V1 exists but persistence is temporary/frozen.

Temporary family:

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

Locked future decision:

**The `invoices` family is canonical.**

Do not create a third Billing architecture.

Old `billing_*` history must remain historical/read-only after cutover.

Canonical Billing V2 is now the active implementation target (see §16).

Billing scenarios eventually required:

1. sale only
2. old-gold purchase only
3. exchange in one invoice

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
- future Billing price display

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

Known carry-overs (outside Gold Price V1):

- `GoldPriceService._billing_set()` provenance uses the first row's provider
  timestamp; revisit with Billing V2 (set-level timestamp = newest).
- `FirstRunOwnerSetup.qml` / `Login.qml` null `stage`/`busy` warnings on
  shutdown → separate Authentication cleanup task.

---

# 16. CURRENT NEXT TASK — Canonical Billing V2

Gold Price Engine V1 is complete. The active priority is **Canonical Billing V2**
on the `invoices` family (see §8).

Before implementing:

1. Inspect the actual `invoices` family schema and the frozen `billing_*` family.
2. Inspect the existing Billing V1 code (`services/billing_service.py`,
   `repositories/billing_repository.py`, `app/viewmodels/billing_viewmodel.py`,
   Billing screens in `qml/Main.qml`).
3. Propose the Billing V2 plan (scenarios, settlement N/P/R, price snapshots via
   `GoldPriceService.get_billing_price_set()` / `lock_and_validate_for_finalization()`,
   atomic finalization) and wait for approval.

Rules:

- Do not create a third Billing architecture.
- Keep old `billing_*` history read-only.
- Do not modify Cash/Bank unless explicitly requested.
- Do not change Gold Price AUTO/MANUAL state.
- Do not commit unless explicitly asked.

---

# 17. Validation expectations

Run relevant:

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

# 19. First Claude Code prompt

After placing this file at the repository root as `CLAUDE.md`, start Claude Code and give it:

```text
Read CLAUDE.md and AGENTS.md completely.

Inspect the current masGold_v2 workspace.

Do not modify files yet.
Do not write to the database.
Do not run migrations.

Confirm:

- current Git branch/status
- Authentication V1 + first-run admin
- Customers V2
- Gold Price Engine V1 complete, AUTO active and accepted
- Canonical Billing V2 is the current task (§16)
- both Billing families (`invoices` canonical, `billing_*` frozen history)

Then report:

1. current branch/status
2. relevant Billing files and schema objects
3. whether CLAUDE.md and AGENTS.md were both found/read
4. any conflict or ambiguity

Do not implement yet.
```

---

# 20. Current project priority

```text
Authentication ✅
Customers ✅
Inventory ✅
Old Gold ✅
Gold Price MANUAL ✅
GoldTimo connectivity ✅
Gold Price coherent-set + UI polish ✅
Controlled AUTO activation ✅
Canonical Billing V2 ← CURRENT
        ↓
Payments / Cash / Bank
        ↓
Invoice archive / printing
        ↓
Reversal / settlement enhancements
```
