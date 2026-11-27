# Task 17 — Gold Settlement Dialog UX Alignment: STOPPED at the decision gate (no edits)

Date: 2026-11-27. **No QML, test or application file was changed.** The comparison shows that `goldSettleDialog` (Suppliers) is not the same business operation as `tripGoldSettleDialog` (Market Trips): it is a multi-operation dialog whose accounting effects and required permissions depend on the chosen source. Per the decision gate, neither dialog was edited. Waiting for owner approval (options in section 5).

## 1. Starting state
Branch `feature/purchasing-suppliers-v1`, HEAD `393f818`. `git status --porcelain=v1` is identical before and after; `qml/SuppliersPage.qml` and `qml/MarketTripsPage.qml` are byte-for-byte what they were at the start (compared with the pre-task copies). Nothing was run that writes data.

## 2. What each dialog does (traced to the services)

### `tripGoldSettleDialog` (`MarketTripsPage.qml:1483`, opened from one custody trip by «تسوية ذهب من العهدة»)
- The custody (trip) is fixed; the employee chooses **a supplier** and **a custody gold source** (karat + designation: LOGO, gold issued without a destination and not melted, or `BAR`, the melted bar).
- Typed: weight, date (default today), reference (optional), notes.
- Preview from `marketTripVM.previewGoldSettlement(supplierId, karatId, designation, weight)`: available in custody, supplier karat, credit = delivered grams × source karat ÷ supplier karat (`rules.settlement_credit`), rounding difference, "short" refusal.
- One call: `marketTripVM.settleGold(tripId, supplierId, karatId, designation, weight, date, reference, notes, requestKey)` → `MarketTripService.settle_supplier_gold(...)` (or `settle_supplier_bar(...)` when the designation is `BAR`).
- Right: `SUPPLIER_GOLD_SETTLE` (checked in the service). Busy: `marketTripVM.writing` (marker `write`).
- Effect (service docstring): one transaction posts the `GOLD_SETTLE` custody entry, the immutable `SG` document (source `CUSTODY_GOLD`, or `TRIP_BAR`), the supplier's negative gold movement in its own karat, the GOLD leg, the entry-settlement link and audit rows. No Scrap, cash, making, bank or lot movement.

### `goldSettleDialog` (`SuppliersPage.qml:2584`, opened from one supplier)
- The supplier is fixed; the employee chooses **a gold source among the sources loaded for that supplier**, filtered by the employee's rights (`supplierVM`, loader around line 567): `MARKET_TRIP_MANAGE` + `SUPPLIER_GOLD_SETTLE` → custody sources; `INTERMEDIARY_HOLDING_MANAGE` + `SUPPLIER_GOLD_SETTLE` → marketer-holding sources; `SUPPLIER_GOLD_SETTLE` alone → other suppliers' gold accounts.
- One call: `supplierVM.postSupplierGoldSettlement(sourceKey, weight, declarationCode, selfConfirmed, date, reference, notes, requestKey)`, which **dispatches on the source type** (`supplier_viewmodel.py:721-738`):

| Source type | Service call | Business operation |
|---|---|---|
| `CUSTODY_GOLD` | `MarketTripService.settle_supplier_gold(trip, supplier, karat, designation, weight, …)` | same as the trip dialog |
| `TRIP_BAR` | `MarketTripService.settle_supplier_bar(trip, supplier, weight, …)` | same as the trip dialog with `BAR` |
| `CUSTODY_LOT` | `MarketTripService.settle_supplier_lot(trip, supplier, lot, …, weight=…)` | a different operation: whole stored lot, credit from the lot's **fineness** (`lot_settlement_credit`, × fineness ÷ 1000 × 24 ÷ supplier karat). In Market Trips it has its own separate dialog (`tripLotSettleDialog`) |
| `HOLDING` | `HoldingSettlementService.settle(pool kind, pool, supplier, weight, declaration, selfConfirmed, …)` | a different operation: gold held by the marketer / intermediary; physical-state declaration for lots; same-party confirmation; deducts the marketer's entitlement, not custody |
| `SUPPLIER` | `SupplierGoldTransferService.transfer(from supplier, to supplier, weight, …)` | a different operation: gold moved between two supplier accounts (`108`), no custody and no marketer |

- A second action in the same dialog: **«تسجيل فرق الوزن»** (`recordCustodyWeightAdjustmentButton`) → `supplierVM.increaseSupplierCustodyWeight(...)` → `MarketTripService.correct_issue_weight(trip, karat, "INCREASE", …, source_kind SHOP_SCRAP | UNSOURCED, target_weight)`: it can **take the difference out of the shop's Scrap into the custody** (refused when Scrap would go negative). The trip dialog has no equivalent action.
- Extra inputs only here: gold-source combo with its own loading / "no sources" states, lot declaration combo, same-party checkbox, weight-adjustment source and reason. Busy markers: `suppliergoldsettle` and `suppliercustodyadjust` (either one disables the dialog).

## 3. Comparison against the gate criteria

| Criterion | Trip dialog | Suppliers dialog | Same? |
|---|---|---|---|
| Business operation | custody gold / melted bar to a supplier | one of five operations, by source | **No** (the dialog is a superset) |
| Source / destination | one trip's custody gold → the chosen supplier's gold account | custody gold, melted bar, stored lot, marketer holding, or another supplier's account → this supplier's account | **No** |
| Karat / weight treatment | delivered grams × source karat ÷ supplier karat (`settlement_credit`); BAR = 21K-equivalent grams | same for custody gold and bar; **fineness-based** for lots and lot-type holdings; transfer between two supplier karats | Only on the shared subset |
| Required fields | supplier, source, weight, date | source, weight, date, plus declaration (lots), same-party confirmation (marketer = supplier), weight-adjustment source + reason | **No** |
| Validation | `short`/error from the preview | `preview.valid`, declaration chosen, same-party confirmation, not adjusting | **No** |
| Ledger / custody / Scrap effects | custody entry + SG document + supplier gold movement; no Scrap | per source: custody entry, **or** marketer-entitlement movement, **or** supplier-to-supplier movement; the weight adjustment moves **shop Scrap** into custody | **No** |
| Permissions | `SUPPLIER_GOLD_SETTLE` | custody sources: `MARKET_TRIP_MANAGE` + `SUPPLIER_GOLD_SETTLE`; holdings: `INTERMEDIARY_HOLDING_MANAGE` + `SUPPLIER_GOLD_SETTLE`; supplier accounts: `SUPPLIER_GOLD_SETTLE`; the weight adjustment is a separate service operation | **No** |
| Busy states | `writing` | `settlingSupplierGold` / `adjustingSupplierCustody` | different flags |
| Keyboard today | none (no focus on open, Escape ignored until a click, Enter does nothing) | same | yes |

Only a subset is equivalent: the custody-gold and melted-bar sources of the Suppliers dialog reach exactly the same two service methods, with the same arguments, as the trip dialog. The other sources, the declaration / same-party steps and the Scrap-moving weight adjustment differ materially in business behavior, accounting effects and permissions. Because the labels and layout of the two dialogs look alike, I did not treat the shared subset as proof that the dialogs are equivalent; the gate says to stop.

## 4. What could be shared without touching any business rule (for the owner's decision)
Interaction-only changes of the kind already approved for the reason dialogs (Task 15) and the payment dialogs (Task 16) are independent of the operation: focus the weight field on open; Return walks the typed fields in order (weight → date → reference → notes) without submitting; Return in the last field calls one `submit()` that is gated by the button's own `enabled` state and makes the same call as the button. In the Suppliers dialog the gate already contains the source choice, the preview, the declaration and the same-party confirmation, so Enter could never post a settlement the button would refuse; the weight-adjustment button would **not** be wired to Enter. I did not implement this because the task says to stop when the operations differ.

Open question that needs the owner: in the Suppliers dialog the weight field is only meaningful after a source is chosen, so "focus the weight on open" is less natural than in the trip dialog (where the first required choice is the supplier). A consistent rule would have to be agreed (for example: focus the source / supplier combo on open, Return from the last typed field submits).

## 5. Options
- **A. Leave both dialogs as they are** (recommended if you want zero risk around gold accounting): the current click flow works and is tested.
- **B. Interaction-only alignment** (focus on open, Return walks the typed fields, Return in the last field submits through one guarded `submit()`; the existing calls and arguments untouched; the weight-adjustment button and every source-specific step untouched). Same pattern and test style as Tasks 15–16. Needs your explicit approval because the dialogs are not business-equivalent.
- **C. Align only the shared subset** — not possible in QML without changing what the Suppliers dialog offers per source; it would need a business decision (for example, removing sources from this dialog) and is outside this task.

## 6. Confirmation
No QML file, test file, view model, service, repository, permission, database or migration was changed; no commit, stash, revert or cleanup; all earlier uncommitted work is intact. No further task started.
