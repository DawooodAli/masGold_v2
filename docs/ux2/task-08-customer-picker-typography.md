# Task 08 — Customer Picker Typography Pilot

Date: 2026-11-26. Approved with strict scope. Result: **done; all tests green; no stop condition reached.** Harness evidence only (no real application window).

## 1. Files changed

| File | Change |
|---|---|
| `qml/CustomerPicker.qml` | 6 lines (below) |
| `tests/test_customer_picker.py` | +165 / −1: geometry helpers (`scene_rect`, `inside`, `text_items`, `px`, `stub_customers`, `sample_rows`, `selected_card_findings`, `result_rows_findings`) and one new test |
| `tests/test_return_history_ux.py` | +104 / −1: one new host test and 3 small helpers inside `HistoryUxQmlTests` |
| `docs/ux2/task-08-customer-picker-typography.md` | this report |

Start state: branch `feature/purchasing-suppliers-v1`, HEAD `393f818`. The three files above had no uncommitted changes before. After the task `git status --porcelain=v1` differs from the start only by these three files (` M qml/CustomerPicker.qml`, ` M tests/test_customer_picker.py`, ` M tests/test_return_history_ux.py`). The pre-existing user changes (`docs/feature-history.md`, `qml/DashboardPage.qml`, `qml/Main.qml` with the Task 06 Flow, `qml/SuppliersPage.qml`, `tests/test_dashboard.py`, `tests/test_inventory_stock_qml.py`, `tests/test_supplier_gold_settlement_ui.py`, untracked `.claude/skills/*`, `docs/ux/`, `docs/ux2/`) were not touched.

## 2. Exact production diff (`git diff -U0 -- qml/CustomerPicker.qml`)

```diff
@@ -154 +154 @@
-                implicitHeight: picker.hasSelection ? 48 : 40
+                implicitHeight: picker.hasSelection ? 52 : 40
@@ -174 +174 @@
-                        font.pixelSize: 10
+                        font.pixelSize: 12        (selected-customer balance line)
@@ -187 +187 @@
-            font.pixelSize: 10
+            font.pixelSize: 11                    (picker error message)
@@ -310 +310 @@
-                            font.pixelSize: 10
+                            font.pixelSize: 11    (result row: id / status line)
@@ -316 +316 @@
-                            font.pixelSize: 10
+                            font.pixelSize: 11    (result row: phone / place line)
@@ -328 +328 @@
-                            font.pixelSize: 10
+                            font.pixelSize: 11    (result row: active-warning count)
```

(The parenthetical notes are added here for reading; the file contains only the numbers.) The file keeps its original CRLF line endings. `PickerLabel`'s base size (12), every `objectName`, handler, binding, colour, weight and spacing are unchanged.

## 3. Before / after geometry (standalone real `CustomerPicker.qml`, Cairo loaded, mocked service)

Measured at 1440×900, 1280×800 and 1080×680 (identical numbers at all three: the component does not depend on the window size).

| Item | Before | After |
|---|---|---|
| Selected button height | 48 | **52** |
| Padding top / bottom | 2 / 2 | 2 / 2 |
| Selected content (name 12 bold + 2 + balance) | 23 + 2 + 19 (10 px) = **44** of 44 available | 23 + 2 + 23 (12 px) = **48** of 48 available |
| Balance line | 10 px, 19 px tall | 12 px, 23 px tall |
| Result popup (x, y, w, h) | 24, 72, 430, 530 | 24, 76, 430, 530 (moves down by the 4 px taller button; still inside every window: bottom 606 ≤ 680) |
| Result row heights (two sample rows) | 129, 108 | 132, 110 (+3, +2) |
| Error / id-status / phone-place / warnings lines | 10 px | 11 px |

The selected content fits the 52 px button with **no spare pixel** (48 of 48), exactly as the audit predicted; the test asserts `content ≤ padded button`, so any later font or padding change that adds one pixel will fail it.

## 4. Screenshots (harness renders, not a real window)

Saved in the session scratchpad (`pk-before2-*` / `pk-after2-*`, plus `pk-before-*` / `pk-after-*`): `selected` crops of the selected-customer button and full-window `popup` renders at 1440×900, 1280×800 and 1080×680. In the 1080×680 popup render the balance line of the selected card is now the same size as the result rows' balance lines; the id/status, phone/place and warning lines are slightly larger; rows grow by 2–3 px; nothing is clipped. Real Cairo font loaded in the script; the offscreen window background differs from the application's.

## 5. Tests

Commands (one process each, offscreen, project `.venv`):

```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -B -m unittest tests.test_customer_picker
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -B -m unittest tests.test_return_history_ux
```

| Module | Executed | Passed | Failed | Errors | Skipped |
|---|---|---|---|---|---|
| `test_customer_picker` | 14 (13 existing + 1 new) | 14 | 0 | 0 | 0 |
| `test_return_history_ux` | 20 (19 existing + 1 new) | 20 | 0 | 0 | 0 |

No MySQL connection (the database layer is patched / blocked in both).

New tests:
- `tests/test_customer_picker.py` → `CustomerPickerQmlTests.test_selected_card_error_and_result_rows_fit_with_the_pilot_font_sizes_at_every_size`. Loads the real component with the bundled `Cairo.ttf` (as the application does). Verifies: error line 11 px and inside the picker; selected button height 52; balance line 12 px; content height ≤ padded button; every line of the card inside the padded button rectangle; popup rectangle inside the window at 1440×900, 1280×800, 1080×680; every result-row line inside its own row; id/status, phone/place and warning lines 11 px; balance lines 12 px; no QML warnings.
- `tests/test_return_history_ux.py` → `HistoryUxQmlTests.test_ux_customer_picker_hosts_keep_the_pilot_card_and_popup_inside_their_boundary_at_every_size`. For each host and each size: a customer is selected, the picker must lie inside **its host's own boundary** (page-stack child rectangle for the page, the dialog's content rectangle for dialogs, never the window rectangle), the same card and row checks run, the popup stays inside the window, and each dialog's content stays inside the window.

Sensitivity check: with the original `CustomerPicker.qml` temporarily restored, both new tests fail (`button height 48.0 != 52`, `balance line is 10 px, expected 12`, `10 != 11`); the edited file was put back and verified byte for byte.

## 6. Hosts

| Host | Picker `objectName` | Boundary used | Tested |
|---|---|---|---|
| Customers page quick picker (`Main.qml`) | `customersQuickPicker` | page-stack child (page index 3) | yes, 3 sizes |
| Customers merge dialog (`Main.qml`) | `customerMergePicker` | dialog content rectangle (opened by its own `begin()`) | yes, 3 sizes |
| Financial accounts cash dialog (`FinancialAccountsPage.qml`) | `customerCashCustomer` | dialog content rectangle (entry section shown by setting `mode`, no view-model call) | yes, 3 sizes |
| Invoice-history change-customer dialog (`InvoiceHistoryPage.qml`) | `historyCustomerPicker` | dialog content rectangle | yes, 3 sizes |

All four were reachable without any production change. Not tested: the pickers' host handlers on selection (`onCustomerSelected` etc.), because the customer is selected on the picker's view model directly to keep the hosts' services out of the test; and no host was loaded with real customer data.

## 7. Limitations

- In the real-`Main.qml` host harness the font is the Qt fallback (the harness does not run `main.py`'s `app.setFont`, and Cairo is not loaded there), so the host checks prove the picker's fixed 52 px height, the 12/11 px sizes, containment in the host boundary and the popup in the window, while the exact Cairo line-height fit (44 → 48 px) is proved by the standalone test only.
- The 0 px of spare space in the selected card (48 of 48) depends on the Cairo line heights measured here (23 px at 12 px bold, 23 px at 12 px regular); a different font would change it.
- The merge dialog's `preview` binding stays inert in the test because no customer is open in the Customers view model; its preview text was not exercised.
- Harness noise: opening the Customers page with the blocked database logs `Customer operation failed`; this is the page, not the picker, and the new test passes with it.
- No real window, real customer data or real barcode/scanner flow was checked. RTL alignment and elision were not changed; the right alignment of the lines is visible in the screenshots.

## 8. Scope confirmation

Production change: `qml/CustomerPicker.qml` only (6 lines). Test changes only in the two approved files. No `Main.qml`, `Theme.qml`, `DashboardPage.qml`, `SuppliersPage.qml`, Inventory or Task 06 file, other QML, Python application code, view model, service, repository, permission, database, migration or dependency was changed. No commit, stash, revert or cleanup. The Suppliers typography and any other page are not started; waiting for your review.
