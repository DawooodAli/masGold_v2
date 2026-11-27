# Task 05 — Inventory Page-Boundary Layout Test

Test-only. Written 2026-11-26. The new test is **red on purpose**: it exposes the existing overflow. No layout was changed.

## 1. Files changed

| File | Change |
|---|---|
| `tests/test_inventory_stock_qml.py` | +`QPointF` import; new helpers and one new test `test_inventory_filter_bar_stays_inside_the_page_area_at_every_size` in `InventoryStockPageTests` (placed before `test_inventory_header_actions_fit`). No existing test or helper was changed (`assert_inside_window` in the shared harness is untouched). |
| `docs/ux2/task-05-inventory-boundary-test.md` | this report |

Start state: branch `feature/purchasing-suppliers-v1`, HEAD `393f818`. Pre-existing uncommitted changes (`docs/feature-history.md`, `qml/DashboardPage.qml`, `qml/SuppliersPage.qml` from Task 04, `tests/test_dashboard.py`, `tests/test_supplier_gold_settlement_ui.py`, untracked `docs/ux/`, `docs/ux2/`, `.claude/skills/*`) were not touched.

## 2. How the usable page rectangle is identified (no fixed sidebar width)

- **Page rectangle** = the scene rectangle of the live `inventoryPage` item (`InventoryPage` in `qml/Main.qml`, a child of `pageStack`). It is read at each window size. It equals `pageStack` at all three sizes: 1205 / 1045 / 845 px wide at 1440 / 1280 / 1080.
- **Reserved regions** = found by walking up from `inventoryPage` and collecting every visible, non-empty sibling of the page or of any ancestor that does not intersect the page. In the real window this yields one region, the sidebar (an unnamed `QQuickRectangle`): `(1205, 0, 1440, 900)`, `(1045, 0, 1280, 800)`, `(845, 0, 1080, 680)`. The test fails if no reserved region is found, if the page is wider than the window, or if the page rectangle itself intersects a reserved region.
- **Overflow** is computed on all four sides, with no tolerance (values are rounded to 3 decimals only to remove floating-point noise). A control's overlap with the reserved region is named in the failure text.

## 3. Controls inspected

All visible items above the list in the page's top-level column, i.e. header, settings row, search card and the filter/bulk-print card (the list view that owns `inventoryListWheel` is excluded: it scrolls in its own viewport; a control's internal parts are not inspected):

- Controls: `Button`, `ComboBox`, `TextField`, `TextArea`, `CheckBox`, `Switch`, `RadioButton`, `SpinBox`, `Label`.
- Containers: `RowLayout`, `ColumnLayout`, `Rectangle`.
- A visible item with no usable geometry (width or height ≤ 0) is reported as a finding, never skipped.
- The test asserts that `inventoryKaratFilter`, `inventoryItemTypeFilter`, `inventoryResetFilters` and `inventoryScrapOnly` are among the inspected items.
- Geometry is sampled until two consecutive samples are identical; otherwise the test fails with "geometry never became stable".

## 4. Window sizes

1440×900, 1280×800, 1080×680 (`harness.SIZES`), each a `subTest`. Result is identical when the sizes are visited in reverse order (checked with a scratch probe), so it does not depend on resize order.

## 5. Findings (measured; page rectangle is `(0, 0, W_page, H)`)

**1440×900** — page `(0, 0, 1205, 900)`: no overflow.

**1280×800** — page `(0, 0, 1045, 800)`, sidebar `(1045, 0, 1280, 800)`: 6 items, all overflow to the right by **28 px** into the sidebar:

| Item | Geometry (x0, y0, x1, y1) |
|---|---|
| RowLayout `inventoryKaratTypeFilters` | (34, 343, 1073, 377) |
| Label «العيار» | (1051, 350, 1073, 370) |
| RowLayout (date + barcode filters, unnamed) | (34, 384, 1073, 436) |
| Label «تاريخ إدخال القطعة» | (990, 400, 1073, 420) |
| RowLayout (selection actions, unnamed) | (34, 443, 1073, 495) |
| Button «تحديد الظاهر» | (963, 443, 1073, 495) |

Both known combo boxes are inside the page at this size (`inventoryKaratFilter` right edge 1043, `inventoryItemTypeFilter` 904); the single item Task 02 flagged at this size is most likely the «العيار» label or its row (inferred from the 28 px match; Task 02's item name was not re-checked).

**1080×680** — page `(0, 0, 845, 680)`, sidebar `(845, 0, 1080, 680)`: 12 items overflow to the right:

| Item | Geometry | Over by |
|---|---|---|
| RowLayout `inventoryKaratTypeFilters` | (34, 343, 1073, 377) | 228 px |
| Label «العيار» | (1051, 350, 1073, 370) | 228 px |
| **ComboBox `inventoryKaratFilter`** | (951, 343, 1043, 377) | **198 px** |
| Label «الصنف» | (912, 350, 943, 370) | 98 px |
| **ComboBox `inventoryItemTypeFilter`** | (784, 343, 904, 377) | **59 px** |
| RowLayout (date + barcode, unnamed) | (34, 384, 1073, 436) | 228 px |
| Label «تاريخ إدخال القطعة» | (990, 400, 1073, 420) | 228 px |
| TextField (date, unnamed) | (864, 391, 984, 429) | 139 px |
| Button «اليوم» | (785, 384, 858, 436) | 13 px |
| RowLayout (selection actions, unnamed) | (34, 443, 1073, 495) | 228 px |
| Button «تحديد الظاهر» | (963, 443, 1073, 495) | 228 px |
| Button «إلغاء التحديد» | (846, 443, 955, 495) | 110 px |

The 198 px and 59 px values reproduce the Task 02 baseline. Every overflow is on the right (toward the sidebar); none on the left, top or bottom.

**Observation for Task 06 (hypothesis, not verified):** the three filter rows measure 1039 px wide (x 34 → 1073) at both 1280 and 1080, while their parent column is 977 / 777 px wide. A width that does not follow the window suggests a content-driven minimum width of the filter card. The cause was not investigated here.

## 6. Commands and results

```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -B -m unittest tests.test_inventory_stock_qml
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -B -m unittest tests.test_inventory_stock_qml.InventoryStockPageTests.test_inventory_filter_bar_stays_inside_the_page_area_at_every_size
```

| Module | Executed | Passed | Failed | Errors | Skipped |
|---|---|---|---|---|---|
| `test_inventory_stock_qml` | 22 | 21 | 1 (the new test; 2 failing sub-tests: 1280×800 and 1080×680) | 0 | 0 |

The 21 existing tests, including the window-based `test_review_fixes_card_filters_history_and_digits`, stay green (they still pass because they compare with the window, which is exactly the blind spot). The two runs above gave identical findings. `assert_inside_window` is not a shared helper I changed, so no other module is affected and none was run. No MySQL connection was made; the real-MySQL opt-in modules were not run.

First run of the new test: 3 errors, `AttributeError: 'QObject' object has no attribute 'parentItem'` from my own helper (`inventoryListWheel` is a `WheelHandler`, not an item). That was a test-infrastructure defect, fixed by using the handler's parent list view; it is not an application result.

## 7. Expected failures

The new test fails at 1280×800 and 1080×680 by design, with the findings in §5; it passes at 1440×900. It was not marked `expectedFailure`, skipped or loosened, so the failure text stays visible. The module stays red until Task 06 fixes the layout.

## 8. Harness limitations

- Offscreen Qt, Material style, fake services, empty inventory list; list rows are not measured.
- Visible-only: controls hidden in this state (the history card, the unpermitted buttons) are not inspected. The harness grants `INVENTORY_MANAGE` and `OLD_GOLD_MANAGE`, so the permission-gated header button is included.
- The reserved region is described only by its class and rectangle (the sidebar rectangle has no `objectName`).
- Container checks include layouts and rectangles; the page's own full-size background rectangle equals the page rectangle and passes.
- Popups opened from this page (ComboBox lists, dialogs) are out of scope.
- No real-window screenshot was taken.

## 9. Confirmation

Only `tests/test_inventory_stock_qml.py` and this report changed in this task. No QML file, `Main.qml`, Python application code, view model, service, repository, database, migration, permission, business rule, theme, shared component or dependency was changed. No commit, stash, revert or cleanup. No layout was fixed or rearranged; waiting for review before Task 06.
