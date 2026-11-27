# Task 06 — Inventory Responsive Layout Fix

Date: 2026-11-26. Approved by the owner with strict scope after the first stop report (the affected layout is inside `qml/Main.qml`, `component InventoryPage`). Result: **done, tests green**. Harness evidence only; no real-window verification.

## 1. Files changed

| File | Change |
|---|---|
| `qml/Main.qml` | `component InventoryPage` → filter card → the second (date / barcode / actions) `RowLayout` replaced by a `Flow`. Nothing else in the file. |
| `tests/test_inventory_stock_qml.py` | `QQuickFlow` added to the Task 05 container set; one new test (see §6). |
| `docs/ux2/task-06-inventory-responsive-layout.md` | this report (replaces the stop report) |

Start state: branch `feature/purchasing-suppliers-v1`, HEAD `393f818`. `qml/Main.qml` had no uncommitted changes before the edit. The other pre-existing changes (`docs/feature-history.md`, `qml/DashboardPage.qml`, `qml/SuppliersPage.qml`, `tests/test_dashboard.py`, `tests/test_supplier_gold_settlement_ui.py`, untracked `docs/ux/`, `docs/ux2/`, `.claude/skills/*`) were not touched.

## 2. Confirmed root cause

The second `RowLayout` of the filter card holds 11 children whose widths are content-driven (date label, 120 px date field, «اليوم», «كل التواريخ», barcode label, 125 px combo, a stretch spacer, counter label, «إعادة ضبط», «قطع من الكسر», «سجل الطباعة»). A `RowLayout` cannot be narrower than the sum of its children's implicit widths, so its minimum width was **1039 px** (children 974 px + 10 × 6 px spacing, plus rounding). That made `bulkPrintColumn` report an implicit width of 1039 px, and the other two rows (own widths 297 and 604 px) were stretched to the same 1039 px. The column was only 977 px at 1280×800 and 777 px at 1080×680, so every row ran 62 / 262 px past the card, on the right toward the sidebar. At 1440×900 the column is 1137 px and the row fit. The first and third rows were never the cause.

## 3. QML change (exact diff, whitespace ignored; `git diff -w --ignore-blank-lines -U1 -- qml/Main.qml`)

```diff
@@ -1249,10 +1249,14 @@
-                            RowLayout {
+                            // Responsive (Task 06): a right-to-left Flow, not one RowLayout. The old row's minimum width was the sum of its eleven children (1039 px),
+                            // which stretched the whole filter card past the page at 1280 and 1080. Each label stays with its control; the groups wrap as a unit.
+                            Flow {
+                                id: inventoryDateBarcodeFlow
                                 Layout.fillWidth: true
-
-                                layoutDirection:
-                                    Qt.RightToLeft
-
+                                layoutDirection: Qt.RightToLeft
                                 spacing: 6
+                                RowLayout {
+                                    id: inventoryDateGroup
+                                    layoutDirection: Qt.RightToLeft
+                                    spacing: 6
@@ -1326,2 +1327,8 @@
+                                }
+                                RowLayout {
+                                    height: inventoryDateGroup.implicitHeight          // same line height as the button groups, so the label and combo stay centred
+                                    layoutDirection: Qt.RightToLeft
+                                    spacing: 6
@@ -1364,7 +1370,7 @@
-                                Item {
-                                    Layout.fillWidth: true
                                 }
+                                RowLayout {
+                                    height: inventoryDateGroup.implicitHeight
+                                    layoutDirection: Qt.RightToLeft
@@ -1385,2 +1391,3 @@
+                                }
```

The plain `git diff` is larger (+96 / −90 lines) only because the grouped controls moved one indentation level deeper. Their blocks are otherwise unchanged. The ignoring-whitespace diff above shows no change to any control body, `objectName`, text, font, height or handler.

Resulting structure of the row (RTL, first child at the right):
1. `RowLayout#inventoryDateGroup`: «تاريخ إدخال القطعة» label, date field, «اليوم», «كل التواريخ»
2. `RowLayout`: «حالة الباركود» label + its combo
3. `RowLayout`: the «الظاهر: N قطعة» counter
4. «↺ إعادة ضبط» (`inventoryResetFilters`), «♻ قطع من الكسر» (`inventoryScrapOnly`), «🧾 سجل الطباعة», placed directly in the `Flow`.

The stretch spacer was removed. Groups 2 and 3 take the same height as group 1 (`inventoryDateGroup.implicitHeight`, the button line height) so their label and combo stay vertically centred with the buttons, since a `Flow` top-aligns its children. Qt/PySide is 6.11.2 here; `Flow.layoutDirection: Qt.RightToLeft` is supported and was verified by measurement (the first group is at the right edge and the order matches the old row) rather than assumed. The card's existing `Layout.preferredHeight: Math.max(158, bulkPrintColumn.implicitHeight + 18)` grows with the wrapped lines; no card-height edit was needed.

## 4. Before / after geometry (harness, scene coordinates)

| Window | Page width | Row width before | Items past the page edge before | After: rightmost inspected item | After: filter card (y0–y1) |
|---|---|---|---|---|---|
| 1440×900 | 1205 | 1137 | 0 | 1183 (card edge, page 1205) | 334–504 (unchanged) |
| 1280×800 | 1045 | 1039 | 6 (28 px) | 1023 (page 1045) | 334–562 (was 334–504) |
| 1080×680 | 845 | 1039 | 12 (228 px max; `inventoryKaratFilter` 198, `inventoryItemTypeFilter` 59) | 823 (page 845) | 334–562 (was 334–504) |

After the change 55 visible controls/containers are inspected at each size, none is zero-sized, and the leftmost is 22 px from the page left. Lines of the date/barcode/action Flow (centre y): 1440 → one line (all controls through «سجل الطباعة» at x 138–1171); 1280 → two lines («سجل الطباعة» alone on the second); 1080 → two lines («قطع من الكسر» and «سجل الطباعة» on the second). Rows 1 (karat / item type) and 3 (selection actions) are unchanged and now take the real column width. The card ends at y = 562 and the list card starts at y = 576 at 1280 and 1080, so there is no overlap.

## 5. Screenshot evidence (harness)

Offscreen Qt/Material render of the real `Main.qml` with two stock rows, saved in the session scratchpad (`inv-before-*.png`, `inv-after-*.png` at 1080×680, 1280×800 and 1440×900), not in the repository. Before 1080×680: the date/barcode row is cut by the sidebar and the karat/type combos are hidden off the right edge. After: every control is visible inside the page with its label to its right, RTL order preserved, and a clear gap before the sidebar. These are harness renders, **not a real application window**; the emoji glyphs of the buttons render as boxes in the offscreen font.

## 6. Tests (one process per module, offscreen, project `.venv`)

```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -B -m unittest tests.test_inventory_stock_qml
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -B -m unittest tests.test_inventory_stock_qml.InventoryStockPageTests.test_inventory_filter_bar_stays_inside_the_page_area_at_every_size
```

| Module | Executed | Passed | Failed | Errors | Skipped |
|---|---|---|---|---|---|
| `test_inventory_stock_qml` | 23 | 23 | 0 | 0 | 0 |

The Task 05 page-boundary test, red before, now passes at all three sizes with its strictness unchanged (the only change is that a `Flow` also counts as a checked container). New test `test_inventory_filter_card_wraps_without_overlapping_the_list_and_every_filter_works_by_click` (at every size): card bottom above the list top; the date and barcode labels each have their control to their left on the same line; reset and scrap-only enabled; real clicks on «اليوم», «كل التواريخ», `inventoryScrapOnly` and `inventoryResetFilters` reach the right control (date set / cleared, scrap-only reaches the view model, reset clears the date); one line at 1440×900 and more than one at 1080×680; no QML warnings. Other Inventory test modules that exist (`test_inventory_*` for the service and view model) do not load this QML and were not run. No MySQL connection; 0 `Live database forbidden` lines.

## 7. Controls remain present and functional

Present and inspected at every size: `inventoryKaratFilter`, `inventoryItemTypeFilter`, the date label/field/«اليوم»/«كل التواريخ», the barcode-state label/combo, the counter, `inventoryResetFilters`, `inventoryScrapOnly`, «سجل الطباعة», «تحديد الظاهر», «إلغاء التحديد», `inventoryRemoveSelectedButton`, `barcodeReviewPrintButton`. Click behaviour of the date buttons, scrap-only and reset is exercised by the new test; the existing tests keep covering filters reaching the service and reset behaviour (all green). «سجل الطباعة», the barcode-state combo and the selection buttons were checked for presence, geometry and non-overlap only, not clicked in this task.

## 8. Remaining limitations and side effects

- **List area at 1080×680:** the card grew by 58 px, so with two sample rows the list shows about one and a third rows instead of two (it scrolls). This follows from wrapping at the smallest approved size; reducing the stat cards or header area would be a separate decision.
- **Arrangement at 1440×900:** one line as before, but without the stretch spacer the counter and the three buttons now sit directly after the barcode group (right-packed) instead of at the far left.
- Wrapping is at group level (the date group is about 400 px wide); widths below 845 px were not tested.
- Harness only: offscreen render, Material style, fake services, two sample items; no real window, no barcode scanner, no real-data volume.
- A pre-existing unrelated oddity noticed, not touched: `qml/Main.qml` is stored with LF endings and Git warns that it will convert to CRLF on commit (`core.autocrlf`); no line endings were changed by this edit.

## 9. Scope confirmation

Only the second filter row inside `component InventoryPage` was changed in `qml/Main.qml`; the first and third rows were not touched (no new issue was demonstrated). No other QML page or dialog, shared component, theme, Python application code, view model, service, repository, permission, business rule, database, migration or dependency was changed. No commit, stash, revert or cleanup. Not started: typography work or Task 07. Waiting for review.
