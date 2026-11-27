# masGold_v2 — UX 2.0 Baseline Verification Report (Task 02)

Date: 2026-11-26. Scope: verification only. **No application code, database, configuration, dependency or existing user change was modified by this task.** The only repository change is this report (`docs/ux2/`, new).

Evidence tags used throughout:

- **[M]** measured: a command or script was actually executed for this report.
- **[R]** read directly in the source (file and line given; lines drift with edits).
- **[V]** looked at in a screenshot produced for this report.
- **[D]** derived by reasoning from [M]/[R] facts; not observed running.
- **[U]** unknown / not verified.

The four approved UX decisions (inventory stays in Administration, gradual font increase with screen-level regression checks, dialog redesign for consistency and fewer steps, keep the Luxury Enterprise Hybrid identity) are inputs to this task. Nothing here implements or contradicts them.

---

## 1. Repository state and commit

| Item | Value |
|---|---|
| Branch | `feature/purchasing-suppliers-v1` [M `git branch --show-current`] |
| HEAD | `393f81860d9546fa3bcb16c57d89bbed4d3c8c92` — "Invoice customer change posts compensating ledger rows (migration 110)" [M] |
| Staged | none [M `git diff --cached`] |
| Stashes | none [M `git stash list`] |
| Modified (unstaged) | `docs/feature-history.md`, `qml/DashboardPage.qml`, `tests/test_dashboard.py` — the home-page regrouping made earlier in the same session, **before** Task 01/02; left exactly as found |
| Untracked | `.claude/skills/qt-qml/`, `.claude/skills/qt-ui-design/`, `.claude/skills/ui-ux-pro-max/`, `docs/ux/` (Task 01 audit) and, after this task, `docs/ux2/` [M `git status --porcelain=v1`] |
| Tree unchanged by the test run | `git status --porcelain=v1` was identical before and after the full suite and after all measurements (only `docs/ux2/` was added at the end) [M] |

Test configuration found [M/R]:

- No `pytest.ini`, `pyproject.toml`, `setup.cfg`, `tox.ini` or `conftest.py`. The framework is **`unittest`** with the project interpreter `.venv\Scripts\python.exe` (Python 3.13.1).
- Documented procedure (`CLAUDE.md` → "Test baseline"): the single-process `discover` run is **order dependent** (Qt style / deleted-object pollution); run **each test module in its own process** (`python -B -m unittest tests.<module>`) for a reliable result. Opt-in real-MySQL modules stay skipped unless their `MASGOLD_*` variable is set.
- The environment variable `MASGOLD_REHEARSAL_PASSWORD` is present in the user's shell. No opt-in switch (`MASGOLD_*_DEV`, `MASGOLD_BOOK_REHEARSAL`, `MASGOLD_REHEARSAL_DB_TESTS`, …) was set. As a precaution that variable was **removed from the child-process environment only** (the user's shell was not changed; its value is not recorded here).
- `config/database_config.py` loads `.env` with `override=True`, so the DB host cannot be redirected through the environment. DB isolation therefore relies on the tests' own guards (see §3).

## 2. Exact test commands and results

### 2.1 Commands (all executed)

Per-module run (313 modules = every `tests/test_*.py`), 3 modules in parallel, 900 s timeout per module, driven by a scratch runner kept outside the repository:

```
env -u MASGOLD_REHEARSAL_PASSWORD QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8 \
    .venv/Scripts/python.exe -B -m unittest tests.<module>
```

Collection count (no test executed by this command):

```
env -u MASGOLD_REHEARSAL_PASSWORD QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -B -c \
  "import unittest; s=unittest.defaultTestLoader.discover('tests'); print(s.countTestCases())"
```

### 2.2 Results [M]

| Quantity | Value |
|---|---|
| Test modules run / modules with status OK | 313 / **313** (no FAILED, no timeout, no module without a result line) |
| Tests executed (sum of each module's `Ran N`) | **4,576** |
| Passed | **4,272** (executed minus skipped) |
| Failures / errors | **0 / 0** |
| Skipped | **304** (300 in 44 `*_mysql` modules; 2 in `test_operational_balances` and 1 in `test_invoice_history`, all opt-in real-database switches; 1 in `test_manager_gate`: "the fake session cannot sign out") |
| Collected by single-process discovery (not executed) | 4,600, 0 import failures |
| Summed per-module process time | 6,176 s (wall-clock was not recorded). Slowest: `test_market_trip_ui` 427 s, `test_marketer_custody_ui` 416 s, `test_gold_position_ui` 398 s, `test_customer_legacy_balance_ui` 383 s, `test_invoice_page_qml` 361 s |

**Not executed (stated plainly):**

- The single-process full discovery run (`python -m unittest discover -s tests -v`). `CLAUDE.md` calls it order dependent; only its *collection count* was taken.
- Every opt-in real-MySQL test (the 304 skips above): they need database switches and a live database, which this task must not touch.
- The 24-test difference between "collected 4,600" and "executed 4,576" is unexplained [U] (likely `load_tests` filters or classes imported across modules).

## 3. Test failures and environment blockers

- **Failures and errors: none.** No collection errors, no timeouts.
- **Blockers: none.** The suite ran without changing the environment.
- **Database isolation** [M]: the 313 logs contain no connection error of any real MySQL server (`Access denied`, `Can't connect`, driver errors: 0 matches). 13 module logs contain `AssertionError: Live database forbidden`: the offscreen harness's guard refused attempted connections (for example background view-model loaders); those tests still passed. Whether any test could ever reach the real DB without its guard was not examined [U].
- **Log noise** (passing tests): about 1,470 `Invoice operation=… exception=AssertionError` lines in the five most frequent kinds (`cash-category` ×692, `category-cost` ×655, `account` ×55, `stock-min-level` ×51, `strip` ×20) from the same guard. They make failures harder to spot in raw output.
- **Ordering** [D]: because modules ran in separate processes, a green result here says nothing about the single-process order dependence that `CLAUDE.md` warns about.

## 4. UX-relevant coverage matrix

Executed counts are from the run in §2 (module `Ran N`). "Covers" and "Gaps" come from reading test names and greps over `tests/`; gap statements marked [M] were established by a measurement, others are heuristics.

| Area | Existing tests (modules → executed) | What they exercise | Gaps found |
|---|---|---|---|
| Dashboard rendering | `test_dashboard` → 52, all OK | page fits at 3 sizes with screenshots, cards, charts, permissions, home regrouping (uncommitted test) | none for layout; the regrouped home page rests on an **uncommitted** test |
| Main navigation and administration access | `test_manager_gate` 13 (1 skip), `test_authentication_qml` 10, `test_post_review_qml` 20, `test_gold_position_ui` 4 → 102 | manager lock open/locked/idle, nav button order and visibility at 3 sizes, login flows | A single test visiting all 17 pages was not found (not exhaustively searched); Administration hub buttons (`reviewsOpen*`) are exercised page by page |
| Inventory page rendering and filter layout | `test_inventory_stock_qml` 21, `test_barcode_input` 13, `test_scrap_ux_fixes` 15, `test_stock_min_levels_ui` 2, `test_category_costs_ui` 2, `test_clasp_stock` 6 → 65 | filters, header actions, rows, new-item / correct / scrap / restore dialogs with real clicks at 3 sizes | **[M] The filter-bar test passes while the bar overflows the page area at 1080×680**: it asserts `inventoryKaratFilter` / `inventoryItemTypeFilter` are inside the *window* (`tests/test_inventory_stock_qml.py:150-155`, helper `assert_inside_window` in `tests/test_invoice_page_qml.py:455`) but the sidebar occupies the right 235 px, so a control can be inside the window yet outside (or under) the page area. See §6.2 |
| Customers page | `test_customer_account_qml` 5, `test_customer_deep_link` 9, `test_customer_legacy_balance_ui` 2, `test_customer_merge_qml`, `test_dead_cash_qml` 2, `test_dead_cash_account_qml` 8, `test_customer_picker` → 88 | statement / invoices tabs, settlements, deposits, legacy balance, merge, dead cash | No test mentions the dialog objectNames `customerFormDialog`, `customerStatusDialog`, `writeoffListDialog` [M grep]; no page-level overflow sweep for the Customers page itself |
| Suppliers page | `test_supplier_ui` 33, `test_party_page_layout_ui` 17, `test_supplier_payment_ui` 12, `test_supplier_gold_settlement_ui` 8, `test_supplier_invoice_reversal_ui` 3, plus roles, adjustments, receipts, transfers, holdings (13 modules) → 169 | real-click dialog flows with `assert_inside_window` at 3 sizes | No mention of `reverseBarFeeDialog`, `adjustGroupDialog`, `marketerReverseDialog`, `deleteConfirm` [M grep] |
| Market trips page | `test_market_trip_ui` 37, `…_phase0_ui`, `…_gold_buy_ui`, `…_gold_sale_ui`, `…_gold_settle_ui` 16, `…_lot_settle_ui` (6 modules) → 91 | trip lifecycle dialogs, gold buy/sale/settle at 3 sizes | No mention of `tripBarFeeDialog` [M grep] |
| Dialog open / close / confirm / cancel | spread over the modules above | open, confirm and cancel by real clicks: yes, for 88 of 102 named dialogs (their `objectName` appears in some test) | 14 named dialogs have no test mentioning their own objectName (child names not searched, so this is a heuristic): `historySellerDialog`, `goldAutoConfirmation`, `customerFormDialog`, `writeoffListDialog`, `customerStatusDialog`, `systemSettingsDialog`, `inventoryBarcodeDialog`, `inventoryRemoveDialog`, `tripBarFeeDialog`, `reportDemandChartDialog`, `reverseBarFeeDialog`, `adjustGroupDialog`, `marketerReverseDialog`, `userAdminCreateDialog`; 8 more dialogs have no `objectName` at all (the six master-data new/disable dialogs, `barcodePrintHistoryDialog`, `deleteConfirm`) |
| Dialog keyboard behaviour | `test_invoice_page_qml`, `test_invoice_history_qml`, `test_barcode_input`, `test_inventory_stock_qml` (1), `test_supplier_gold_settlement_ui` (1), `test_post_review_qml` (1) | Esc / Enter / Tab appear in only 6 of 51 UI-driving test files; as dialog tests, only `inspectionConfirm` (Esc) and the invoice `customerSuggestions` popup | **No existing test covers Escape, Enter, Tab or initial focus for any Suppliers, MarketTrips, Customers, Cash or Inventory dialog** [M grep for `Key_Escape/Key_Return/Key_Enter/Key_Tab`] |
| Responsive layout at supported sizes | 44 of 51 UI-driving test files loop over `SIZES` (1440×900, 1280×800, 1080×680) | position-in-window assertions, screenshots when `MASGOLD_UI_REVIEW_DIR` is set | Assertions use the window, not the page area (see Inventory); no global every-page sweep; no assertion on text truncation (`Label.truncated`) |

Static detection of "UI-driving" test files (imports of the shared harness, `grabWindow`, `operational_engine`): 51 files, 544 test methods defined. [Task 01 reported 46 / 422 by file-name glob; the two counts use different rules.]

## 5. Dialog workflow inventory

### 5.1 Population and what was measured

- **110 `Dialog` / `Popup` / `SettingsDialog` blocks** (41 `Dialog`, 60 `Popup`, 9 `SettingsDialog`) plus 1 `Menu` = the 111 of Task 01 [M]. 102 have an `objectName`. Suppliers 22 and MarketTrips 21 hold 43 of them (197 input controls in total).
- Static facts per dialog come from reading the source (§5.3, §5.4, Appendix A). Runtime behaviour comes from opening each named dialog **empty** (no row data) in the offscreen harness with fake services and a blocked database (1280×800 for behaviour; 1440×900, 1280×800, 1080×680 for geometry). `customerPickerPopup` did not open on a bare `open()` and is excluded from runtime figures. Usage frequency of any dialog is **unknown** (no telemetry exists in the repository) and is not estimated anywhere in this report.

### 5.2 Dismissal and focus behaviour [M, harness]

| Type | Opened | Esc closes right after open | Click outside closes | Keyboard focus right after open |
|---|---|---|---|---|
| `Dialog` | 35 | **35 / 35** | 9 / 35 | the dialog itself |
| `SettingsDialog` | 9 | **9 / 9** | 9 / 9 | the dialog itself |
| `Popup` | 57 | **1 / 57** (`inspectionConfirm`, the only popup that sets `focus: true`) | 4 / 57 (`customerSuggestions`, `approvalPopup`, `inspectionConfirm`, `tradeTripPicker`) | stays on the page underneath: `invoiceBarcodeField` for 31, `stocktakeScanField` for 24; 2 hold their own focus |

Consequences, all [M] unless tagged:

- **56 popups ignore Escape until the user clicks inside them**; 54 of those 56 declare `closePolicy: Popup.CloseOnEscape` (alone or with click-outside) in source and still do not react, because they hold no keyboard focus. After a click into a text field, 48 of the 49 popups that contain one close on Esc (`historySellerDialog` did not; focus landed on a combo box). 7 popups contain no text field (`correctionConfirm`, `customerSuggestions`, `tradeTripPicker`, `reportDemandChartDialog`, `reportPiecesDialog`, `reportChartDialog`, `supplierIntakeDialog`): nothing inside them can take focus, so Esc never closes them by keyboard.
- **Enter-to-submit does not exist** in Suppliers or MarketTrips: 0 `onAccepted` and 0 `Keys.on…` handlers in the 43 dialogs [M grep]. Only 7 dialogs in the whole app have any such hook: `customerPickerPopup`, `customerSuggestions`, `approvalPopup`, `confirmFinalizeDialog`, `legacyVoidDialog`, `stocktakeConfirmDialog`, `userAdminPasswordDialog`.
- **No dialog moves focus to its first field on open** in Suppliers or MarketTrips: 0 `onOpened` handlers, and the only `forceActiveFocus` inside the 43 dialogs is `weightField.forceActiveFocus()` in `supplierIntakeDialog` (`SuppliersPage.qml:3906`), a step inside the intake flow, not an on-open focus [R]. [D] With focus left on a page control, keystrokes typed or scanned right after opening go to that underlying control, not to the dialog. This was not exercised with a real scanner [U].
- Tab order is Qt's default; there is no `KeyNavigation` anywhere in QML [M, Task 01]. Tab behaviour inside dialogs was not exercised [U].
- **Modal dim is inconsistent:** only 16 of 110 dialogs define `Overlay.modal` (dark `#80000000`); the other 94 declare none and take the Material default, which renders as a **light grey** veil over the dark UI in the harness [V: `dlg-customerFormDialog-1080x680.png` vs `dlg-newInventoryItemDialog-1080x680.png`]. The real application also sets `QQuickStyle.setStyle("Material")` (`main.py:523`) [R], but its appearance was not viewed [U].

### 5.3 Suppliers page (`qml/SuppliersPage.qml`, page index 14)

How users get there [D from `Main.qml` / `ManagementReviewPage.qml`]: sidebar «الإدارة» → manager lock → «🤝 الموردون» → pick a supplier → (tabs «نظرة عامة» / «سجل الذهب» / «سجل المصنعية») → row or header button → dialog. That is at least 4 clicks plus the password before any dialog opens. Every dialog below is a single screen (no multi-step state) [R]; none has a `step`/`confirming` property.

Columns: "Inputs" counts input controls in the block; "Required" lists labels carrying `*`; "Close policy" is the declared `closePolicy` ("Qt default" = not declared); "Keyboard (static)" says whether the block defines Enter handling; "Runtime" is §5.2's measurement for that dialog (`Esc ✔/✘`, click-outside `✔/✘`, where focus sat after open).

| Dialog (`objectName`) | Opened from | Inputs | Required (`*`) fields | Primary action | Close policy | Keyboard (static) | Runtime (harness) | Test files referencing it |
|---|---|---|---|---|---|---|---|---|
| `supplierDialog` | «＋  مورد جديد» (SuppliersPage:429); «تعديل» (SuppliersPage:634) | 6 | اسم المورد, الدور, العيار | «حفظ» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | supplier_roles_ui, supplier_ui |
| `deleteConfirm` | «حذف» (SuppliersPage:648) | 0 | — | «حذف» | Esc only | Enter: none | not run | none found |
| `payMakingDialog` | «دفع مصنعيات» (SuppliersPage:753) | 8 | طريقة الدفع, الحساب البنكي, عهدة السوق المفتوحة, المسوقجي صاحب النقدية, المبلغ (ج), تاريخ الدفع | «تسجيل الدفعة» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | supplier_payment_ui |
| `receiveMakingDialog` | «استلام مصنعيات من المورد» (SuppliersPage:761) | 5 | طريقة الاستلام, الحساب البنكي, المبلغ (ج), تاريخ الاستلام | «تسجيل الاستلام» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | supplier_making_receipt_ui |
| `reversePaymentDialog` | «عكس» (SuppliersPage:1554) | 1 | سبب العكس | «عكس الدفعة» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | supplier_payment_ui |
| `accountAdjustDialog` | «تسوية فروق الذهب» (SuppliersPage:717); «تسوية فروق المصنعيات» (SuppliersPage:769) | 2 | الوزن (جرام), المبلغ (ج), السبب | «تأكيد التسوية» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | supplier_account_adjustment_ui |
| `reverseAdjustmentDialog` | «عكس» (SuppliersPage:1012); «عكس» (SuppliersPage:1568) | 1 | السبب | «تأكيد العكس» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | supplier_account_adjustment_ui |
| `reverseBarFeeDialog` | «عكس» (SuppliersPage:1561) | 1 | السبب | «تأكيد العكس» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | none found |
| `adjustGroupDialog` | «تعديل وزن المجموعة» (SuppliersPage:4127) | 2 | الوزن (جرام), السبب | «تأكيد التعديل» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | none found |
| `adjustRateDialog` | «تعديل المصنعية» (SuppliersPage:4131) | 2 | مصنعية الجرام الجديدة (ج), السبب | «تأكيد التعديل» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | supplier_ui |
| `reverseInvoiceDialog` | «عكس الفاتورة» (SuppliersPage:3972) | 1 | سبب العكس | «تأكيد عكس الفاتورة» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | supplier_invoice_reversal_ui |
| `goldSettleDialog` | «تسوية ذهب» (SuppliersPage:709) | 9 | مصدر الذهب, الوزن المُسلَّم الفعلي (جرام), التاريخ, إقرار الحالة الفعلية للسبكة, مصدر فرق الوزن, سبب تصحيح الوزن | «تسجيل التسوية» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | supplier_gold_settlement_ui, supplier_gold_transfer_ui |
| `holdingReturnDialog` | «إرجاع السبكة كاملة للعهدة» (SuppliersPage:1095) | 4 | التاريخ | «تسجيل الرجوع» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | holding_receipt_ui, holding_settlement_ui, intermediary_holdings_ui |
| `logoReturnDialog` | «إرجاع ذهب للعهدة» (SuppliersPage:1172) | 4 | الوزن العائد الفعلي (جرام), التاريخ | «تسجيل الرجوع» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | intermediary_holdings_ui |
| `holdingSettleDialog` | «تسوية لمورد» (SuppliersPage:1084); «تسوية لمورد» (SuppliersPage:1161) | 6 | المورد المستلم, الوزن المُسلَّم الفعلي (جرام), التاريخ, حالة السبكة الفعلية عند التسليم | «تسجيل التسوية» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | holding_settlement_ui, party_page_layout_ui |
| `reverseReceiptDialog` | «تصحيح تسجيل» (SuppliersPage:1242) | 1 | سبب العكس | «تأكيد التصحيح» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | holding_receipt_ui |
| `reverseSettlementDialog` | «عكس» (SuppliersPage:998); «تصحيح تسجيل» (SuppliersPage:1312) | 1 | سبب العكس | «تأكيد التصحيح»»«عكس التسوية» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | holding_settlement_ui, party_page_layout_ui, supplier_gold_settlement_ui |
| `reverseTransferDialog` | «عكس» (SuppliersPage:1005) | 1 | سبب العكس | «عكس التسوية» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | supplier_gold_transfer_ui |
| `marketerDialog` | «تسليم نقدية» (SuppliersPage:1386); «استرجاع نقدية» (SuppliersPage:1388) | 13 | المبلغ (ج), الوزن بالجرام (وزنة عيار 21), طريقة التسعير, السعر المتفق عليه, المشتري, البائع, اسم المشتري, اسم البائع, المورد المستلم, التاريخ | «تسجيل» / «أؤكد السعر وأسجّل» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | marketer_custody_ui |
| `marketerReverseDialog` | «عكس» (SuppliersPage:1428); «عكس» (SuppliersPage:1454) | 1 | سبب العكس | «تأكيد العكس» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | none found |
| `newSupplierInvoiceDialog` | «＋  فاتورة شراء جديدة» (SuppliersPage:628); «تعديل المسودة» (SuppliersPage:3964) | 6 | — | «ترحيل الفاتورة» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | supplier_ui |
| `supplierIntakeDialog` | «?» (SuppliersPage:83); «القطع: » (SuppliersPage:911) | 6 | — | «ترحيل الفاتورة» / «＋  إدخال القطعة» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | supplier_invoice_reversal_ui, supplier_ui |

### 5.4 Market trips page (`qml/MarketTripsPage.qml`, page index 15)

Path [D]: «الإدارة» → manager lock → «🧭 عهدة السوق» → select a trip → action button → dialog. Single-screen dialogs throughout; `tripReasonDialog` is one shared dialog for "reopen custody" and "reverse".

| Dialog (`objectName`) | Opened from | Inputs | Required (`*`) fields | Primary action | Close policy | Keyboard (static) | Runtime (harness) | Test files referencing it |
|---|---|---|---|---|---|---|---|---|
| `tradeTripPicker` | «?» (MarketTripsPage:37) | 0 | — | — | Qt default | Enter: none | Esc ✘, outside ✔, focus: BarcodeField:invoiceBarcodeField | market_trip_gold_sale_ui |
| `tripEntryDialog` | «＋  عهدة سوق جديدة» (MarketTripsPage:322); «إخراج عهدة» (MarketTripsPage:634) | 7 | التاريخ | «تسجيل الرجوع»»«إخراج العهدة» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_phase0_ui, market_trip_ui |
| `tripSupplierPayDialog` | «دفع مصنعيات من العهدة» (MarketTripsPage:622) | 5 | المورد, المبلغ (ج), التاريخ | «تسجيل الدفعة» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_ui |
| `tripLotSettleDialog` | «تسوية سبكة من العهدة» (MarketTripsPage:569) | 5 | المورد, الشحنة الكاملة, التاريخ | «تسجيل التسوية» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_lot_settle_ui |
| `tripLotHoldDialog` | «تسليم ذهب لمسوقجي» (MarketTripsPage:575) | 5 | المسوقجي, الشحنة الكاملة, التاريخ | «تسجيل التسليم» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | intermediary_holdings_ui |
| `tripLogoHoldDialog` | «تسليم لوجو لمسوقجي» (MarketTripsPage:578) | 6 | المسوقجي, عيار اللوجو, الوزن الفعلي (جرام), التاريخ | «تسجيل التسليم» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | intermediary_holdings_ui |
| `tripGoldSettleDialog` | «تسوية ذهب من العهدة» (MarketTripsPage:566) | 6 | المورد, مصدر الذهب من العهدة, الوزن المُسلَّم الفعلي (جرام), التاريخ | «تسجيل التسوية» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_gold_settle_ui |
| `tripGoldBuyDialog` | «?» (MarketTripsPage:49); «شراء ذهب نقدي» (MarketTripsPage:554) | 11 | البائع, اسم البائع, التاريخ, طريقة الدفع, الحساب البنكي / المحفظة | «تسجيل الشراء» / «أؤكد الأسعار وأسجّل الشراء» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_gold_buy_ui, market_trip_gold_sale_ui |
| `tripGoldSaleDialog` | «?» (MarketTripsPage:50); «بيع ذهب من العهدة» (MarketTripsPage:557) | 11 | الذهب المباع, الوزن المباع الفعلي (جرام), طريقة التسعير, السعر المتفق عليه, المشتري, اسم المشتري, التاريخ, طريقة الدفع, الحساب البنكي / المحفظة | «تسجيل البيع» / «أؤكد السعر وأسجّل البيع» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_gold_sale_ui, market_trip_gold_settle_ui |
| `tripHeatDialog` | «حمّية اللوجو» (MarketTripsPage:548) | 8 | تاريخ الحمّية, الورشة / المسؤول | «تسجيل الحمّية» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_ui |
| `tripDisposeDialog` | «مرتجع سوق / خسية» (MarketTripsPage:560) | 5 | العملية, المصدر, الوزن (جرام), التاريخ | «تسجيل» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_gold_settle_ui |
| `tripAdjustDialog` | «تصحيح وزن الإخراج» (MarketTripsPage:563) | 5 | العيار, الاتجاه, فرق الوزن (جرام), السبب, التاريخ | «تسجيل» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_gold_settle_ui |
| `tripBarFeeDialog` | «أجر السبيكة» (MarketTripsPage:625) | 4 | المبلغ (ج), التاريخ | «تسجيل» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | none found |
| `tripCashSettleDialog` | «تسوية نقدية العهدة» (MarketTripsPage:616); «مصروف من العهدة» (MarketTripsPage:619) | 6 | نوع التسوية, المبلغ (ج), الحساب, البيان, السبب, التاريخ | «تسجيل» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_gold_settle_ui |
| `tripClassifyLogoDialog` | «اعتبار المتبقي لوجو» (MarketTripsPage:551) | 2 | التاريخ | «اعتبارها لوجو» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_gold_settle_ui |
| `tripBarMarketerDialog` | «تسليم سبيكة لمسوقجي» (MarketTripsPage:572) | 4 | المسوقجي, الوزن (جرام، مكافئ عيار 21), التاريخ | «تسليم السبيكة» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_gold_settle_ui |
| `tripSettleMeltDialog` | «تسجيل سبك (بعد الرجوع)» (MarketTripsPage:545) | 7 | تاريخ السبك, الورشة / المسؤول, وزن السبك الفعلي (جرام), عيار السبيكة بالألف (رقم صحيح) | «تسجيل السبك» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_ui |
| `tripMeltDialog` | «سبك» (MarketTripsPage:542) | 12 | تاريخ السبك, الورشة / المسؤول, الوزن الفعلي (جرام) | «تسجيل السبك» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_ui |
| `tripLotIssueDialog` | «إخراج سبكة من المحل» (MarketTripsPage:536) | 3 | تاريخ الإخراج | «إخراج السبائك المحددة» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_ui |
| `tripLotReturnDialog` | «رجوع ناتج السبك» (MarketTripsPage:539) | 3 | تاريخ الرجوع | «إرجاع الشحنات المحددة» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | market_trip_ui |
| `tripReasonDialog` | «إعادة فتح العهدة» (MarketTripsPage:643); «عكس» (MarketTripsPage:743) | 1 | السبب | «عكس الحركة»»«إعادة الفتح» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | intermediary_holdings_ui, market_trip_gold_buy_ui, market_trip_gold_sale_ui |

### 5.5 Obvious duplicated or confusing interactions [R/M]

1. **Ten near-identical "reason-only" confirmation dialogs** — one input, one required reason, one red confirm and Cancel: Suppliers `reversePaymentDialog`, `reverseAdjustmentDialog`, `reverseBarFeeDialog`, `reverseInvoiceDialog`, `reverseReceiptDialog`, `reverseSettlementDialog`, `reverseTransferDialog`, `marketerReverseDialog`; MarketTrips `tripReasonDialog`; Main `reverseDeadCashDialog`. Same skeleton, but they differ in details by static string checks of their source: confirm button accent red in 6 of 10, title coloured red in 3 of 10, cancel disabled while busy in 5 of 10, a warning/effect line (amber) in 8 of 10; widths 460–560 px. Two have no test mentioning them (`reverseBarFeeDialog`, `marketerReverseDialog`).
2. **The same business action is reachable from two pages with two different dialogs.** "Pay a supplier's making from custody cash": Suppliers `payMakingDialog` (button «دفع مصنعيات», 8 inputs, 6 required, a method chooser CUSTODY / MARKETER / BANK, calls `page.vm.payMaking`) and MarketTrips `tripSupplierPayDialog` (button «دفع مصنعيات من العهدة», 5 inputs, 3 required, calls `page.vm.paySupplier`). Likewise gold settlement from custody: `goldSettleDialog` (Suppliers, 9 inputs) and `tripGoldSettleDialog` (MarketTrips, 6 inputs). Whether the two paths call the same service is **[U]**; the audit only establishes that the user sees two forms for one goal.
3. **Typed dates.** 51 labels in these two pages ask for `yyyy-MM-dd` typed by hand (Suppliers 16, MarketTrips 35) while the app has a calendar `DateField` used only by Cash, History and Reports.
4. **All 43 popups declare Esc-only closing (click-outside deliberately off), yet Esc does not work until focus is inside them** (§5.2), and no dialog takes initial focus; the keyboard path is unreliable in every one of them.
5. **Confirm-step wording differs by flow** (`حفظ`, `تسجيل الدفعة`, `عكس التسوية`, `تأكيد التسوية`, `أؤكد الأسعار وأسجّل الشراء`, …) — acceptable for money flows, but there is no shared pattern.
6. **Very large dialogs:** `newInventoryItemDialog` is 1,477 source lines with 11 inputs and 6 required; `supplierIntakeDialog` 537 lines; `barcodePrintQueueDialog` 832 lines and `barcodePrintHistoryDialog` 671 lines are `Dialog`s (the latter has no `objectName`).

### 5.6 Other major workflows (summary; full table in Appendix A)

| Workflow | Dialogs (objectName) | Screens | Notes |
|---|---|---|---|
| New / finalize invoice | `confirmFinalizeDialog`, `makingConfirmDialog`, `clearInvoiceDialog`, `approvalPopup`, `customerSuggestions` | 1 form → confirm (finalize also reads F9 / Ctrl+S) | only invoice dialogs have Enter/Esc tests |
| Customers | `customerFormDialog`, `customerStatusDialog`, `customerMergeDialog` (form → confirm), `writeOffDialog` (step state, 2+ screens), `reverseDeadCashDialog` | 1–3 | `customerFormDialog`, `customerStatusDialog`, `writeoffListDialog` have no test reference |
| Customer account | `customerAccountPaymentDialog`, `customerDepositDialog` and `customerLegacyDialog` (both «متابعة» → «تأكيد») | 1–2 | |
| Cash | `cashMovementDialog` («متابعة» → «تأكيد»), `transferDialog`, `customerCashDialog`, `cashDetailDialog`, `dayCloseDialog` | 1–2 | |
| Inventory (inside الإدارة) | `newInventoryItemDialog`, `inventoryItemDetailsDialog`, `inventoryItemCorrectDialog`, `inventoryRestoreDialog`, `inventoryItemScrapDialog`, `inventoryBarcodeDialog`, `inventoryRemoveDialog`, `barcodePrintQueueDialog`, `barcodePrintHistoryDialog` | 1 each | page stays under the manager lock, as approved |
| Invoice history changes | `historySellerDialog`, `historyCustomerDialog`, `historyPieceDialog` | 1 | 2 required marks each |
| Users | `userAdminCreateDialog`, `userAdminResetDialog`, `userAdminPasswordDialog` | 1 | |
| Management review | `inspectionConfirm` | 1 (confirm) | the only popup that closes on Esc |

## 6. Font and layout baseline

Fonts were **not** changed. All figures are as of HEAD plus the uncommitted home-page edit.

### 6.1 Small-font locations [M static + R]

- 81 lines set an effective **9–10 px** size (literal 9/10 or `theme.fontCaption`; the dashboard's `+2` boost lines are excluded). Task 01 counted 58 because it counted literals only. By file: `Main.qml` 45, `CustomerPicker.qml` 5, `ExchangeWorkspace.qml` 5, `InvoicePage.qml` 5, `InvoiceHistoryPage.qml` 4, `SuppliersPage.qml` 4, `ReturnWorkspace.qml` 3, others ≤ 2 each. A further 287 uses are 11 px (`fontTableHeader` or literal 11) and 252 literals are 11–12 px.
- 30 of the 81 are inside 13 dialogs. Largest: `barcodePrintQueueDialog` 9, `barcodePrintHistoryDialog` 4, `customerPickerPopup` 3, `newInventoryItemDialog` 3, `customerFormDialog` 2, `inventoryItemDetailsDialog` 2.
- Candidates that plausibly affect readability (hint, status and warning text rather than decoration): the two barcode print dialogs, the customer picker, helper text in `newInventoryItemDialog` and `customerFormDialog`, exchange/return explanatory lines (`ExchangeWorkspace.qml:325`), «للمالك فقط» captions in `UsersAdminPage.qml:581/583`. Whether any of them *materially* hurts readability is **[U]**: no user test or on-device check was done.

### 6.2 Inventory filter bar overflow [M]

Controls of the Inventory page against the page area (the area left of the 235 px sidebar), harness data (empty inventory):

| Size | Page area width | Control | Spans (x) | Beyond the page area by |
|---|---|---|---|---|
| 1080×680 | 845 | `inventoryKaratFilter` | 951 – 1043 | **198 px** (wholly under the sidebar zone) |
| 1080×680 | 845 | `inventoryItemTypeFilter` | 784 – 904 | **59 px** (cut at the page edge) [V: `pages/p02-inventory-1080x680.png`] |
| 1280×800 | 1045 | «تحديد الظاهر» button | — | 1 control flagged by the overflow counter |
| 1440×900 | 1205 | — | — | none |

All of these lie inside the *window*, which is why `assert_inside_window` does not catch them (§4). The overflow is a layout property, so it is not expected to depend on data, but it was measured only with empty data [U].

### 6.3 Page-level overflow and text clipping at the three sizes [M]

17 pages × 3 sizes in the harness (fake services; pages needing permissions or data show permission or empty states: Administration, Reports, Users, Suppliers, Market custody, Global price and Home were **not** meaningfully populated, so their zero is not evidence): horizontal overflow of buttons / fields / combos beyond the page area: **Inventory only** (4 controls at 1080×680, 1 at 1280×800). QML warnings while opening the pages: 0. Truncated (elided) visible labels: **1 in total** — the Settings page's gold-source description at 1080×680.

### 6.4 Dialog sizing and content overflow [M, harness, dialogs opened empty]

- 101 of 102 named dialogs opened at each of the three sizes; none exceeded the window width; none was taller than the window. **30 dialogs reach ≥ 600 px at 1080×680** (they cap themselves at window − 24/40 px and scroll): `tripGoldSaleDialog`, `tripGoldBuyDialog`, `supplierIntakeDialog`, `marketerDialog`, `holdingSettleDialog`, `customerCashDialog` (all 656 px), `tripSettleMeltDialog`, `tripMeltDialog`, `tripGoldSettleDialog`, `newSupplierInvoiceDialog`, `newInventoryItemDialog`, `customerFormDialog` (640 px), …; at 1440×900 only 2 reach ≥ 800 px.
- One control sat beyond the window bottom at 1080×680: `custodyGoldSaleNotesField` in `tripGoldSaleDialog` — probably scrolled content [U].
- **Measurement artifact, not a defect:** `historySellerDialog`, `historyCustomerDialog`, `historyPieceDialog` computed a width of −24 px because their width is `page.width − 24` and the history tab was not the visible one; their "1 truncated label" figures are invalid. They are only openable when the page is visible, and they are covered by the existing real-click tests.
- Dialogs were opened with **no row data**: real content (long lists, long Arabic notes) can only make them taller or wider.

## 7. Recommended first dialog pilot

**Pilot: `reverseSettlementDialog`** (`qml/SuppliersPage.qml:3187`, 57 lines) — then, in order, the other reason-only dialogs (`reverseTransferDialog`, `reversePaymentDialog`, …). Chosen from observable friction and risk, not frequency (frequency is unknown):

| Criterion | Evidence |
|---|---|
| Friction is real and measurable | Escape does nothing right after open (popup without focus); focus stays on a page control (§5.2); no Enter confirmation [M/R]; typing therefore starts with a click into the reason field [D]; confirm styling differs between the 10 sibling dialogs (§5.5) |
| Representative | Single screen, 1 input, 1 required reason, one confirm and Cancel; the same skeleton as 9 other dialogs, so a pilot teaches the pattern for 10 |
| Low blast radius | 2 openers (`SuppliersPage.qml:998` «عكس», `:1312` «تصحيح تسجيل») and one slot call `page.vm.reverseSettlement(settlementId, reason)` that need not change |
| Existing safety net | 3 test modules reference it (`test_supplier_gold_settlement_ui`, `test_holding_settlement_ui`, `test_party_page_layout_ui`), the dialog has two modes («عكس التسوية» and «تأكيد التصحيح»); whether each module clicks through both modes was not verified |
| Money-safety | Reversal confirmation with a mandatory reason; the pilot touches presentation, focus and keyboard only, not the reversal service, its arguments or its permissions |
| Fits the approved decisions | Dialog consistency and fewer steps (initial focus on the reason field, Enter to confirm, Esc to cancel) without changing the visual identity |

Not first, and why:

- `tripReasonDialog`: widest test net (6 modules) but also the widest blast radius — one dialog serving "reopen" and "reverse" with 4 button labels.
- `reverseTransferDialog`: simplest, but only 1 test module.
- `payMakingDialog` / `tripSupplierPayDialog`: duplicated action across two pages (§5.5 item 2) needs an owner decision first.
- `tripGoldBuyDialog` (11 inputs, price-confirmation button), `supplierIntakeDialog` (537 lines), `newInventoryItemDialog` (1,477 lines): too large and money-critical for a first change.

Suggested guard for the pilot: tests first (Esc right after open, focus lands in the reason field, Enter confirms only when the reason is non-empty and not busy), then the change; the existing three modules must stay green.

## 8. Risks and prerequisites for implementation

Prerequisites (before any UX 2.0 code change):

1. **Commit or park the existing uncommitted work** (`DashboardPage.qml`, `tests/test_dashboard.py`, `docs/feature-history.md`) so that UX 2.0 diffs are reviewable. This task did not touch them.
2. **Fix the test blind spot first:** add an assertion that controls are inside the *page area*, not just the window (used by the inventory filter test and any font-increase checks). Without it, larger fonts can push controls under the sidebar and still pass.
3. Decide the **modal primitive**: `Dialog` closes on Esc by default (44 of 44 measured) while `Popup` does not (1 of 57); a consistent dialog design must set focus explicitly or standardise on `Dialog`.
4. Decide the **overlay dim** (light-grey default on 94 dialogs vs dark on 16): a look decision that must stay inside the Luxury Enterprise Hybrid identity.
5. Keep the **per-module test procedure** (§2) as the regression gate; budget about 100 minutes of summed process time (about 6,176 s) for the full run, shorter with parallel workers.
6. Capture screenshot baselines per screen at the three sizes with `MASGOLD_UI_REVIEW_DIR` before the first font change.

Risks:

- Font increases: the inventory header/filter bar already overflows at 1080×680; more pages (Suppliers, MarketTrips, History) are dense. A gradual rollout needs the page-area assertion above [D].
- Focus changes in popups interact with barcode scanners: if focus moves into a dialog, scans no longer reach the page's `BarcodeField`, and if it does not, scans reach the page while a dialog is open [D]; behaviour with real scanner hardware was not tested [U].
- Money-critical dialogs: any shared dialog component must forward ids and reasons unchanged to the existing view-model slots; tests that click `confirm…Button` objectNames depend on those names staying stable (2,083 `objectName`s in QML, Task 01).
- 14 named dialogs and 8 unnamed dialogs have no test referencing them (§4); changing them is unguarded until tests exist.
- The duplicated "pay making" / "gold settle" paths (§5.5 item 2) and the manager-lock policy are product decisions, not UI details; both are out of scope here and unchanged.
- Test-run noise (`Live database forbidden`) and the 24-test collected/executed gap (§2.2) should be understood before using test counts as a regression metric.

## 9. Verified findings versus assumptions

| Verified by execution or direct measurement | Assumed, derived or unverified |
|---|---|
| 313/313 modules OK, 4,576 executed, 304 skipped, 0 failures/errors; collected 4,600 | The 24-test difference; single-process ordering behaviour |
| Popups ignore Esc until a click inside (1 of 57 closes); Dialogs close on Esc (44 of 44) | Real-app behaviour with real keyboard focus and scanner; Tab order inside dialogs |
| 0 Enter handlers and 0 initial-focus handlers in the 43 Suppliers/MarketTrips dialogs | Usage frequency of any dialog (unknown) |
| Inventory karat/item-type filters 198 px / 59 px beyond the page area at 1080×680; existing test passes anyway | Overflow with real inventory data |
| 94 of 110 dialogs have no explicit modal dim; light-grey veil in harness screenshots | The look of the dim in the real window |
| 81 lines at 9–10 px effective; 30 inside dialogs | Whether specific small texts materially hurt readability |
| 101 dialogs open within the window at all 3 sizes (empty content) | Heights/widths with real data; the one control beyond the window in `tripGoldSaleDialog` |
| Test modules referencing each dialog (objectName grep) | Coverage of child controls by name; test quality |

---

## Appendix A — Other named dialogs (full static table)

Same columns as §5.3. "unknown" in *Opened from* means the opener was not found by the static search (for example opened through a function in another component).

| Dialog (`objectName`) | Opened from | Inputs | Required (`*`) fields | Primary action | Close policy | Keyboard (static) | Runtime (harness) | Test files referencing it |
|---|---|---|---|---|---|---|---|---|
| `correctionConfirm` | unknown | 0 | — | «تنفيذ» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | correction_direction_ui |
| `customerDepositDialog` | «عربون» (CustomerAccountPage:116); «استرداد عربون» (CustomerAccountPage:118) | 2 | — | — | conditional | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | customer_account_qml |
| `customerLegacyDialog` | «?» (CustomerAccountPage:25); «إضافة رصيد قديم» (CustomerAccountPage:120) | 4 | — | — | conditional | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | customer_legacy_balance_ui |
| `customerAccountPaymentDialog` | «تسوية الحساب» (CustomerAccountPage:114) | 4 | — | — | conditional | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | customer_account_qml |
| `customerPickerPopup` | «?» (CustomerPicker:54) | 1 | — | — | conditional | Enter: yes | not run | customer_picker |
| `calendar` | «?» (DateField:52) | 0 | — | — | Qt default | Enter: none | not run | none found |
| `cashMovementDialog` | «مصروفات وإيرادات» (FinancialAccountsPage:234) | 4 | — | «متابعة» / «تأكيد» | conditional | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | financial_accounts_qml |
| `transferDialog` | «⇄ تحويل بين الحسابات» (FinancialAccountsPage:240) | 4 | — | «تحويل» | conditional | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | financial_accounts_qml |
| `customerCashDialog` | «حركة نقدية عملاء» (FinancialAccountsPage:237) | 5 | — | «تسجيل» | conditional | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | financial_accounts_qml |
| `cashDetailDialog` | unknown | 0 | — | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:Dialog | financial_accounts_qml |
| `historySellerDialog` | «تغيير البائع» (InvoiceHistoryPage:617) | 2 | البائع الجديد, السبب | «حفظ» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | none found |
| `historyCustomerDialog` | «تغيير العميل» (InvoiceHistoryPage:725) | 3 | العميل الجديد, السبب | «حفظ» | Esc only | Enter: none | Esc ✘, outside ✘, focus: QQuickPopupItem:Popup | return_history_ux |
| `historyPieceDialog` | «تصحيح» (InvoiceHistoryPage:854) | 3 | الباركود الصحيح (القطعة التي خرجت فعلًا), السبب | «تصحيح الباركود» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | invoice_history_qml |
| `makingConfirmDialog` | «?» (InvoicePage:1978) | 0 | — | — | Esc only | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | billing_review_qml |
| `clearInvoiceDialog` | «تصفير الفاتورة» (InvoicePage:2579) | 0 | — | — | Esc only | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | billing_review_qml |
| `customerSuggestions` | unknown | 0 | — | — | conditional | Enter: yes | Esc ✘, outside ✔, focus: BarcodeField:invoiceBarcodeField | invoice_page_qml, invoice_viewmodel |
| `approvalPopup` | «?» (InvoicePage:1978); «موافقة المدير» (InvoicePage:2460) | 2 | — | — | Qt default | Enter: yes | Esc ✘, outside ✔, focus: BarcodeField:invoiceBarcodeField | billing_review_qml |
| `confirmFinalizeDialog` | «?» (InvoicePage:207) | 3 | — | — | Qt default | Enter: yes | Esc ✔, outside ✔, focus: QQuickPopupItem:Dialog | invoice_page_qml, post_review_qml |
| `customerNavigationDialog` | «?» (Main:70) | 0 | — | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:Dialog | customer_deep_link |
| `goldPriceDialog` | «?» (Main:204); «?» (Main:2567) | 3 | — | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:Dialog | gold_price_qml |
| `goldManualConfirmation` | «اعتماد السعر اليدوي» (Main:489) | 0 | — | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:Dialog | gold_price_qml |
| `goldAutoConfirmation` | «?» (Main:206); «تلقائي» (Main:284) | 0 | — | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:Dialog | none found |
| `customerFormDialog` | unknown | 2 | الاسم | — | Esc only | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | none found |
| `writeoffListDialog` | «المشطوب» (Main:3092) | 0 | — | — | Esc only | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | none found |
| `customerStatusDialog` | «?» (Main:3059) | 0 | — | — | Esc only | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | none found |
| `writeOffDialog` | «?» (Main:3594) | 2 | مبلغ الشطب, سبب شطب الرصيد, كلمة المرور | — | conditional | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | dead_cash_account_qml, dead_cash_qml |
| `customerMergeDialog` | «دمج / حذف» (Main:3422) | 1 | سبب الدمج | — | conditional | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | customer_merge_qml |
| `reverseDeadCashDialog` | «?» (Main:3582); «عكس الشطب» (Main:3742) | 1 | سبب العكس, كلمة المرور | — | conditional | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | dead_cash_account_qml, dead_cash_qml |
| `editCategoryDialog` | unknown | 8 | كود الفئة | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:Dialog | master_data_qml |
| `disableCategoryDialog` | «⛔  تعطيل الفئة» (Main:7881) | 0 | — | — | Qt default | Enter: none | not run | none found |
| `reactivateCategoryDialog` | «↻  إعادة تفعيل» (Main:7236) | 0 | — | — | Qt default | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | master_data_qml |
| `deleteMasterDialog` | unknown | 0 | — | — | Esc only | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | master_data_qml |
| `legacyVoidDialog` | «?» (Main:2298) | 3 | — | — | Qt default | Enter: yes | Esc ✔, outside ✔, focus: QQuickPopupItem:SettingsDialog* | legacy_invoice_void |
| `scrapAdjustDialog` | «?» (Main:2300) | 3 | — | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:SettingsDialog* | old_gold_adjustment |
| `claspStockDialog` | «🧷  رصيد الرفايع» (Main:1004); «?» (Main:2304) | 3 | — | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:SettingsDialog* | clasp_stock |
| `dayCloseDialog` | «?» (Main:2241) | 3 | — | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:SettingsDialog* | day_close |
| `payrollDialog` | «?» (Main:2302) | 5 | — | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:SettingsDialog* | payroll_ui |
| `positionOpeningDialog` | «?» (Main:2306) | 0 | — | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:SettingsDialog | position_opening_ui |
| `categoryCostsDialog` | «🏷  تكلفة المصنعية للحروف» (Main:1011) | 0 | — | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:SettingsDialog | category_costs_ui |
| `stockMinLevelsDialog` | «📉  الحد الأدنى للمخزون» (Main:1018) | 0 | — | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:SettingsDialog | stock_min_levels_ui |
| `systemSettingsDialog` | «?» (Main:5624) | 0 | — | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:SettingsDialog | none found |
| `newFactoryDialog` | «＋  مصنع جديد» (Main:5923) | 3 | اسم المصنع | — | Esc only | Enter: none | not run | none found |
| `editFactoryDialog` | unknown | 3 | اسم المصنع | — | Esc only | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | master_data_qml |
| `disableFactoryDialog` | «⛔  تعطيل المصنع» (Main:9624) | 0 | — | — | Qt default | Enter: none | not run | none found |
| `reactivateFactoryDialog` | «↻  إعادة تفعيل» (Main:6154) | 0 | — | — | Qt default | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | master_data_qml |
| `newItemTypeDialog` | «?» (Main:6390) | 4 | اسم الصنف | — | Qt default | Enter: none | not run | none found |
| `editItemTypeDialog` | unknown | 4 | اسم الصنف | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:Dialog | master_data_qml |
| `disableItemTypeDialog` | «⛔  تعطيل الصنف» (Main:10430) | 0 | — | — | Qt default | Enter: none | not run | none found |
| `reactivateItemTypeDialog` | «?» (Main:6658) | 0 | — | — | Qt default | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | master_data_qml |
| `newInventoryItemDialog` | «＋  إدخال قطعة جديدة» (Main:963) | 11 | طريقة الإدخال, مجموعة المورد, الصنف, العيار, الوزن بالجرام, وزن الكسر المسحوب بالجرام | — | Esc only | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | inventory_stock_qml, post_review_qml, scrap_ux_fixes |
| `inventoryItemDetailsDialog` | «?» (Main:2065) | 0 | — | — | Esc only | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:Dialog | inventory_stock_qml |
| `inventoryItemCorrectDialog` | «تعديل الصنف والعيار» (Main:12746) | 6 | — | — | Esc only | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | inventory_stock_qml |
| `inventoryRestoreDialog` | «إعادة القطعة للمخزون» (Main:1645) | 2 | — | — | Esc only | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | inventory_stock_qml |
| `inventoryItemScrapDialog` | «تحويل إلى الكسر» (Main:12730) | 1 | — | — | Esc only | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | inventory_stock_qml |
| `inventoryBarcodeDialog` | «تعديل الباركود» (Main:12738) | 2 | — | — | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | none found |
| `inventoryRemoveDialog` | «🗑  حذف المحدد» (Main:1472) | 1 | — | — | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | none found |
| `barcodePrintQueueDialog` | «?» (Main:877) | 0 | — | — | Esc only | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | inventory_stock_qml |
| `barcodePrintHistoryDialog` | «🧾 سجل الطباعة» (Main:1422) | 1 | — | — | Esc only | Enter: none | not run | none found |
| `inspectionConfirm` | unknown | 0 | — | — | Qt default | Enter: none | Esc ✔, outside ✔, focus: QQuickPopupItem:Popup | exchange_scrap_hold_qml, post_review_qml |
| `reportDemandChartDialog` | unknown | 0 | — | — | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | none found |
| `reportPiecesDialog` | unknown | 0 | — | — | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | reports_ui |
| `reportChartDialog` | unknown | 0 | — | — | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:invoiceBarcodeField | reports_ui |
| `stocktakeConfirmDialog` | «?» (StocktakePage:100); «↺  إعادة الضبط» (StocktakePage:303) | 0 | — | — | Esc only | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | inventory_stock_qml |
| `stocktakeApproveDialog` | «?» (StocktakePage:28) | 3 | — | — | Esc only | Enter: none | Esc ✔, outside ✘, focus: QQuickPopupItem:Dialog | inventory_stock_qml |
| `userAdminPasswordDialog` | unknown | 1 | — | «تأكيد» | Esc only | Enter: yes | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | post_review_qml |
| `userAdminResetDialog` | «إعادة تعيين كلمة المرور» (UsersAdminPage:456) | 4 | — | «إعادة التعيين» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | post_review_qml |
| `userAdminCreateDialog` | «+ مستخدم جديد» (UsersAdminPage:325) | 7 | — | «إنشاء» | Esc only | Enter: none | Esc ✘, outside ✘, focus: BarcodeField:stocktakeScanField | none found |

## Appendix B — Method and reproduction

Nothing below was added to the repository; scripts lived in the session scratchpad.

- Repository state: `git branch --show-current`, `git rev-parse HEAD`, `git status --porcelain=v1`, `git diff --cached`, `git stash list`.
- Full run: a runner that launched `unittest tests.<module>` for each of the 313 `tests/test_*.py` modules in its own process (commands in §2.1), parsed each module's final `Ran N … / OK / FAILED` line, and kept the logs.
- Dialog inventory: a static scan of every `Dialog | Popup | SettingsDialog` block in `qml/*.qml` (objectName, openers in the same file — `Main.qml` dialogs are searched across files —, required `*` labels, primary-button texts, `onAccepted` / `Keys.on…`, `closePolicy`, test references by `objectName`).
- Runtime behaviour and geometry: a subclass of the project's own offscreen harness (`tests.test_dashboard.DashboardPageTests`: real `Main.qml`, fake services, blocked database) that opened each named dialog by `objectName`, pressed Escape (`QTest.keyClick`), clicked at the window corner, read the active focus item, clicked into the first text field and pressed Escape again, and, at 1440×900 / 1280×800 / 1080×680, read popup size, position, interactive controls beyond the window and truncated labels. Screenshots of 12 dialogs and the page captures were viewed from the scratchpad; the 102-dialog behaviour table was not screenshotted.
- Inventory filter geometry: the same harness, mapping each `inventory*` control to the page stack and comparing with `pageStack.width`.
- Small fonts: regular-expression scan of `qml/*.qml` for `pixelSize` 9/10 and `theme.fontCaption` (excluding `boost` lines), attributed to dialog blocks by line range.
- Limits: regular-expression scans are not a QML parser; the harness uses fake data and the offscreen platform (glyph metrics and emoji differ from Windows); the dialogs were opened empty.
