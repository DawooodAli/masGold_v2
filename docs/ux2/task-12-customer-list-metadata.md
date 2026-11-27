# Task 12 — Customer List Metadata Typography Pilot

Date: 2026-11-27. Result: **done; all four modules green; no stop condition reached.** Evidence is harness-based (offscreen Qt), not a real application window.

## 1. Starting state
| | Value |
|---|---|
| Branch / HEAD | `feature/purchasing-suppliers-v1` / `393f818` |
| `git status --porcelain=v1` before | ` M docs/feature-history.md`, ` M qml/CustomerPicker.qml`, ` M qml/DashboardPage.qml`, ` M qml/Main.qml`, ` M qml/SuppliersPage.qml`, ` M tests/test_customer_picker.py`, ` M tests/test_dashboard.py`, ` M tests/test_inventory_stock_qml.py`, ` M tests/test_return_history_ux.py`, ` M tests/test_supplier_gold_settlement_ui.py`, ` M tests/test_supplier_ui.py`; untracked `.claude/skills/*`, `docs/ux/`, `docs/ux2/` |
| After | identical list (`qml/Main.qml` was already modified by the Task 06 Flow; nothing new appears) |

`qml/Main.qml` carried prior uncommitted work (Task 06 and others). A backup of the pre-edit file was kept in the session temp folder and the final diff against it was checked line by line.

## 2. The three declarations (confirmed against the source before editing)
All three still matched Task 11: each belongs to the element named below, and each was 10 px. The `CustomerLabel` base is 12 px and wraps; each target overrides it with its own literal.

| # | Line | Enclosing path | objectName | Displayed text | Old → new |
|---|---|---|---|---|---|
| 1 | 3290 | `customerList` → delegate `CustomerCard` → `ColumnLayout#customerRow` → first `RowLayout` → `CustomerLabel` | none | `modelData.status_label` («نشط» / «غير نشط») | `font.pixelSize: 10` → `11` |
| 2 | 3311 | same delegate → second `RowLayout` → `CustomerLabel` (`Layout.maximumWidth: 150`, NoWrap, ElideLeft) | none | «آخر حركة: …» | `10` → `11` |
| 3 | 3355 | Customers list panel footer `RowLayout` → `CustomerLabel` (`Layout.fillWidth: true`) | none | «صفحة N · حتى 100 عميل» | `10` → `11` |

Token choice: `Main.qml` contains **no** use of `theme.fontTableHeader`, and the sibling labels of these same rows use literal `font.pixelSize: 11` (e.g. the `#id · code` label two lines above #1), so the literal `11` follows the component's own convention. No token was introduced.

## 3. Diff
Production (`qml/Main.qml`, exactly three lines, verified with `diff` against the pre-edit copy: 3 lines changed, nothing else):
```diff
@@ -3290 +3290 @@   (status_label)
-                                                font.pixelSize: 10
+                                                font.pixelSize: 11
@@ -3311 +3311 @@   («آخر حركة»)
-                                                font.pixelSize: 10
+                                                font.pixelSize: 11
@@ -3355 +3355 @@   (pagination footer)
-                                    font.pixelSize: 10
+                                    font.pixelSize: 11
```
Test diff: **none**. No existing test asserts these sizes, and the task did not ask for a new test; the sizes and geometry were verified with a separate read-only measurement script (below). Not changed: customer history lines (~3694, ~3721), InventoryPage / Task 06 Flow, `DashboardPage.qml`, `Theme.qml`, any other file, view model, service or database.

## 4. Before / after geometry (real `Main.qml`, Cairo loaded, 3 sample customers: normal, inactive with a long last-activity text, empty last activity)
Same numbers at every viewport for the labels and rows; only the pagination width follows the window.

| Element | Before | After |
|---|---|---|
| Status label «نشط» / «غير نشط» | 10 px, 21 × 19 / 35 × 19 | **11 px**, 22 × 20 / 38 × 20; inside its row |
| «آخر حركة: 2026-11-20 14:30» | 10 px, 123 × 19 | **11 px**, 126 × 20; not elided |
| «آخر حركة: …» (long text) | 10 px, **150** wide, elided (implicit 288) | **11 px**, **150** wide, elided (implicit 299): maximum width and ElideLeft behaviour unchanged |
| «آخر حركة: —» | 10 px, 50 × 19 | **11 px**, 52 × 20 |
| Pagination «صفحة 1 · حتى 100 عميل» | 10 px, implicit 103 × 19 | **11 px**, implicit 109 × 20; inside its footer row and the window |
| Customer row height | 60 | **60** (no change) |
| List height (1440 / 1280 / 1080) | 475 / 375 / 255 | **475 / 375 / 255** |

At 1440×900, 1280×800 and 1080×680 every measured label was fully inside its row (status, last activity) or inside its parent and the window (pagination); no label wrapped (NoWrap), nothing overlapped, no row or list moved. The name line (13 px bold) and the contact / balance lines keep the row at 60 px, so the extra 1 px of the metadata lines does not change the row.

## 5. Screenshot evidence
Harness renders at the three sizes, before and after, saved in the session scratchpad (`t12-before-*.png`, `t12-after-*.png`); the 1080×680 "after" render was inspected: three customer cards, metadata lines readable, last-activity text elided only for the long sample, footer with «السابق» / «التالي» intact. The red error banner in these renders is the harness blocking the live database, not a result of the change. Not a real application window.

## 6. Tests (one process each, offscreen, project `.venv`, no MySQL)
```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -B -m unittest tests.<module>
```
| Module | Ran | Result |
|---|---|---|
| `test_customer_account_qml` | 5 | OK |
| `test_dead_cash_qml` | 2 | OK |
| `test_customer_deep_link` | 9 | OK |
| `test_customer_legacy_balance_ui` | 45 | OK |

The "Live database forbidden" lines in the console are the harness's intended block.

## 7. Preservation
Only the three lines above changed in `Main.qml`; the Task 06 Flow and every other pre-existing modification are untouched (status list identical before and after). No commit, stash, revert, reset or cleanup; no database, migration or dependency; nothing from Task 13 started.

## 8. Limitations
- Cairo numbers come from the measurement script (Cairo loaded before the page is created); the four test modules run with the fallback font and do not assert these font sizes. A regression test for the 11 px sizes was not added (not requested).
- Measured with three sample customers and a long last-activity text; real customer data and a real window were not used.
- The elided long text is shown by `ElideLeft` as before; which characters are cut was not reviewed beyond the unchanged 150 px maximum.
- The Customers history lines (~3694 / ~3721) and the balance-line decision (11 vs 12) are intentionally untouched.
