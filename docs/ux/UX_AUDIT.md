# masGold_v2 — Full Application UX Audit (Task 01)

Audit date: 2026-11-26. Scope: read-only. This task changed **no source code, no database, no business logic, no dependency** and did no refactoring or redesign. The only file this task added to the repository is this document.

How to read this file: every statement is tagged by how it was obtained.

- **[M]** measured by a script or command run for this audit (method in section 9).
- **[R]** read directly in the source (file and line are given; line numbers are those of the working tree at audit time and drift with edits).
- **[V]** looked at in a screenshot produced for this audit (only the screenshots listed in section 8.2).
- **[D]** derived by reasoning from [R]/[M] facts. Not observed running.
- **[U]** not verified. Listed so that nothing is silently assumed.

---

## 0. Baseline and state verified

| Item | Value |
|---|---|
| Branch / HEAD | `feature/purchasing-suppliers-v1` / `393f818` [M: `git`] |
| Working tree at audit time | Uncommitted, **not made by this task**: `qml/DashboardPage.qml`, `tests/test_dashboard.py`, `docs/feature-history.md` (the home-page regrouping done earlier in the same session, before Task 01 was issued). Untracked: `.claude/skills/qt-qml/`, `.claude/skills/qt-ui-design/`, `.claude/skills/ui-ux-pro-max/` [M: `git status`] |
| Effect on the audit | The home page (index 0) is measured **as it is in the working tree** (grouped under three labelled rows). All other QML files equal HEAD. QML line totals below include those uncommitted edits (DashboardPage.qml: 1,014 lines at HEAD, 1,039 in the working tree). |
| Database | **Not touched and not inspected.** No schema level, row count or live data is asserted anywhere in this document. |
| Application run | The real application was **not** launched. All visual evidence comes from the project's offscreen test harness (blocked database, fake services; see 8.2). |
| Prior audit | `docs/ui-audit-dashboard.md` (2026-11-18, dashboard only). Not relied on; its "777 lines" for `DashboardPage.qml` is stale (1,014 lines at HEAD). |

---

## 1. Inventory

### 1.1 Shell (`qml/Main.qml`, 14,724 lines — 41 % of all QML)

[R] The window has a minimum size of 1080×680 (`Main.qml:14-15`); `main.py:109-110` clamps it to the screen. The physical **right-hand sidebar** is 235 px wide (`Main.qml` ~2480). From top to bottom it holds:

1. Shop name «مجوهرات ماس» and tagline (the tagline is hidden below 720 px height).
2. Six `NavButton`s: `🏠 الرئيسية` (0), `🧾 الفواتير` (1), `💰 حركة النقدية` (4, only when `financialAccountsVM.canView`), `👥 العملاء` (3), `🌍 السعر العالمي` (16), `🔔 الإدارة` (10, with an amber count when reviews are pending).
3. A balance strip: cash and counted old gold per karat (21, 18, 24; the 22K box is hidden), under `GridLayout#balanceStrip`.
4. A gold price card (sell / buy 21K) with `تلقائي | يدوي` mode buttons; clicking opens `goldPriceDialog`.
5. Signed-in user name and `خروج`.

The page area is a `StackLayout#pageStack` (`Main.qml:2188`) with 17 children indexed 0–16, hidden while `managerGate` is shown.

### 1.2 Pages (page stack)

| Idx | Title | Component → file | Main view models referenced [R] | Sidebar? |
|---|---|---|---|---|
| 0 | الرئيسية | `DashboardPage` (mode `home`) → `qml/DashboardPage.qml` | `dashboardVM`, `goldPriceVM` | yes |
| 1 | الفواتير | `BillingWorkspace` → `qml/BillingWorkspace.qml` (tabs «فاتورة جديدة» / «سجل الفواتير»; Exchange, Return and Correction workspaces replace the tab view full-page) | `invoiceVM`, `invoiceHistoryVM`, `exchangeVM`, `returnVM`, `correctionVM` | yes |
| 2 | المخزون | `InventoryPage` (inline component, `Main.qml:671`) | `inventoryVM`, `barcodeVM`, `oldGoldConversionVM` | **no** (via الإدارة) |
| 3 | العملاء | `CustomersPage` (inline, `Main.qml:2989`) embedding `CustomerAccountPage.qml` | `customerVM`, `customerAccountVM` | yes |
| 4 | حركة النقدية | `FinancialAccountsPage` → `qml/FinancialAccountsPage.qml` | `financialAccountsVM`, `dayCloseVM` | yes (conditional) |
| 5 | التقارير | `ReportsPage` → `qml/ReportsPage.qml` | `reportsVM` | **no** |
| 6 | الإعدادات | `SettingsPage` (inline, `Main.qml:5495`) | many (36 `*VM` names referenced in `Main.qml`) | **no** |
| 7 | المصانع | `FactoriesPage` (inline, `Main.qml:5758`) | `masterDataVM` | **no** |
| 8 | الأصناف | `ItemTypesPage` (inline, `Main.qml:6219`) | `masterDataVM` | **no** |
| 9 | الفئات | `CategoriesPage` (inline, `Main.qml:6723`) | `masterDataVM` | **no** |
| 10 | الإدارة | `ManagementReviewPage` → `qml/ManagementReviewPage.qml` | `reviewsVM` | yes |
| 11 | بيان المخزون | `StockBalancePage` → `qml/StockBalancePage.qml` | `stockBalanceVM` | **no** |
| 12 | الجرد بالباركود | `StocktakePage` → `qml/StocktakePage.qml` | `stocktakeVM`, `stocktakeAdjustVM` | **no** |
| 13 | المستخدمون والصلاحيات | `UsersAdminPage` → `qml/UsersAdminPage.qml` | `userAdminVM` | **no** |
| 14 | الموردون | `SuppliersPage` → `qml/SuppliersPage.qml` (4,378 lines) | `supplierVM`, `supplierPurchaseVM`, `marketTripVM`, `masterDataVM` | **no** |
| 15 | عهدة السوق | `MarketTripsPage` → `qml/MarketTripsPage.qml` (3,254 lines) | `marketTripVM` | **no** |
| 16 | السعر العالمي | `DashboardPage` (mode `gold`) | `dashboardVM`, `goldPriceVM` | yes |

Outside the stack: `Login.qml`, `FirstRunOwnerSetup.qml` (first-run owner creation). The iPad reports web app (`webapp/`) is a separate web UI and is **not** covered (section 8.3).

Sub-structure of pages [R]:

- **Reports (5):** 19 reports in `services/report_service.py` (`REPORT_TITLES`): sales, stock, alerts, stock weights, indicators, item movement, employees, making profit, scrap cost, demand, customers, dead cash, suppliers, treasury, cash flow, market, gold profit, gold flow, KPI.
- **Settings (6):** 13 `SettingsCard`s declared on the landing view, opening sections (theme, gold price, printing, password, categories, backup, web reports, system settings, etc.), plus sub-pages 7, 8, 9, 13, and Suppliers management (14 in manage mode).
- **الإدارة (10):** 10 action buttons (`reviewsOpen…`: inventory, legacy invoice void, payroll, scrap adjust, clasp stock, opening balance, settings, suppliers, market custody, reports) and review cards for six categories (`MAKING_PRICE`, `RETURNED_ITEM_INSPECTION`, `RETURN_APPROVAL`, `INVENTORY_MATCH`, `CASH_SETTLEMENT`, `OLD_GOLD_DISCOUNT`) plus `ACCOUNTS` and `GOLD_POSITION` (`management_review_viewmodel.py:25-29`).
- **Customers (3):** header buttons `كشف حساب / تسوية`, `إضافة عميل`, `تحديث`, `المشطوب`; history tabs `كشف الحساب`, `التحذيرات`, `الرصيد المشطوب`, `الفواتير` (`Main.qml` ~3519-3541); the embedded `CustomerAccountPage` adds `تسوية الحساب`, `عربون`, `استرداد عربون`, `إضافة رصيد قديم`, `⛔ تحويل إلى رصيد مشطوب`.
- **Suppliers (14):** `SegTab`s `نظرة عامة` / `سجل الذهب` / `سجل المصنعية` (`SuppliersPage.qml:804-806`).

### 1.3 Modal surfaces (dialogs, popups, menu)

[M] 111 top-level modal objects in `qml/*.qml`: **41 `Dialog`, 60 `Popup`, 9 `SettingsDialog`, 1 `Menu`**.

| File | Count | Examples (objectName) |
|---|---|---|
| `Main.qml` | 40 | customer form / status / merge / write-off / reverse dead cash, gold price + two gold-mode confirmations, master-data edit / reactivate / delete (factories, item types, categories), inventory item new / details / correct / restore / scrap / barcode / remove, barcode print queue, `legacyVoidDialog`, `scrapAdjustDialog`, `claspStockDialog`, `dayCloseDialog`, `payrollDialog`, `positionOpeningDialog`, `categoryCostsDialog`, `stockMinLevelsDialog`, `systemSettingsDialog` |
| `SuppliersPage.qml` | 22 | `supplierDialog`, `payMakingDialog`, `receiveMakingDialog`, `goldSettleDialog`, `holdingReturnDialog`, `holdingSettleDialog`, `reverse…Dialog` ×8, `newSupplierInvoiceDialog`, `supplierIntakeDialog`, `marketerDialog` |
| `MarketTripsPage.qml` | 21 | `tripEntryDialog`, `tripSupplierPayDialog`, `tripLotSettleDialog`, `tripGoldBuyDialog`, `tripGoldSaleDialog`, `tripHeatDialog`, `tripMeltDialog`, … `tripReasonDialog` |
| `InvoicePage.qml` | 6 | `confirmFinalizeDialog`, `makingConfirmDialog`, `clearInvoiceDialog`, `approvalPopup`, `customerSuggestions`, `rowMenu` |
| `FinancialAccountsPage.qml` | 4 | `cashMovementDialog`, `transferDialog`, `customerCashDialog`, `cashDetailDialog` |
| `UsersAdminPage`, `ReportsPage`, `InvoiceHistoryPage`, `CustomerAccountPage` | 3 each | password / reset / create user; report chart & pieces; seller / customer / piece change; deposit / legacy balance / settlement |
| `StocktakePage` | 2 | `stocktakeConfirmDialog`, `stocktakeApproveDialog` |
| `ManagementReviewPage`, `CorrectionWorkspace`, `CustomerPicker`, `DateField` | 1 each | `inspectionConfirm`, `correctionConfirm`, `customerPickerPopup`, date popup |

Width rule is not uniform [M: `dialogs.txt` scan of the first 16 lines of each block]: most dialogs in `Main.qml`, `InvoicePage`, `ReportsPage`, `StocktakePage` and some others use `Math.min(N, page.width − K)`; fixed widths 300, 340, 360, 430, 500, 520 exist; for ~45 modals (mostly Suppliers and MarketTrips popups) no `width:` appears in the first 16 lines, so their width source is [U].

### 1.4 Navigation paths (derived from code, not click-tested) [D]

"Steps" = sidebar/button clicks after login, excluding scanning/typing. A **manager lock** (`managerGate`, section 2 F1) additionally applies to every page marked 🔒.

| Destination | Path | Steps |
|---|---|---|
| New invoice | Sidebar الفواتير (tab «فاتورة جديدة» is index 0) | 1 |
| Invoice history / detail | الفواتير → «سجل الفواتير» → row | 2 + row |
| Exchange / Return / Correction | history → detail → action (opens full-page workspace) | ≥ 3 |
| Customers, cash movement, global gold price, home | sidebar | 1 |
| Gold price dialog | click price card in sidebar | 1 |
| Day close (`dayCloseDialog`) | حركة النقدية → button | 2 |
| Inventory 🔒 | الإدارة → «المخزون» | 2 |
| Stock balance / barcode stocktake / min levels / category costs 🔒 | الإدارة → المخزون → header button | 3 |
| Suppliers 🔒, Market custody 🔒, Reports 🔒 | الإدارة → button | 2 |
| Payroll, scrap adjust, clasp stock, legacy void, opening balance (dialogs) | الإدارة → button | 2 |
| Settings section, e.g. gold price source 🔒 | الإدارة → الإعدادات → card (→ sub-section) | 3–4 |
| Factories / item types / categories / users 🔒 | الإدارة → الإعدادات → card | 3 |
| Management review decision | الإدارة → card → review row → action → confirm | ≥ 4 |

### 1.5 Core workflows (from code and the project documents; not executed in this audit)

1. **Sale / old-gold purchase / mixed invoice** (`InvoicePage.qml`, `invoiceVM`): customer (F4), barcode scan, F3 search, F5 manual, F7 from scrap, F6 scrap grams, payment legs + customer-account allocation, price-staleness checkbox, finalize confirm (F9 / Ctrl+S), print (`حفظ فقط` for old-gold-only invoices).
2. **Invoice history** (`InvoiceHistoryPage.qml`): search/filter, detail, timeline, reprint, change seller, change customer, change returned piece.
3. **After-sale lifecycle** (`ExchangeWorkspace`, `ReturnWorkspace`, `CorrectionWorkspace`): Exchange (Scan / F3 / F5 / F7), Return, Full Reversal (RV) and Settlement Correction (FC), each with a confirmation popup.
4. **Customers**: create / edit / deactivate / merge / delete, statement, invoices tab, deposits, legacy balance, settlement (CS), dead cash write-off and reversal, warnings.
5. **Cash**: cash and bank movements, transfers, customer cash in/out, day close.
6. **Management**: six review categories, returned-item inspection, accounts, gold position, payroll, scrap and clasp adjustments, opening balance.
7. **Inventory**: new item entry (including old-gold conversion), details, correction, restore, scrap, barcode print queue, barcode stocktake and approval, stock balance, minimum levels.
8. **Suppliers & market custody**: supplier invoices, making/gold payments and receipts, settlements, holdings, marketer account, trips with ~20 entry types, reversal dialogs.
9. **Reports** (19), **Settings** (gold price provider, printing, password, backup, web reports, categories, theme, system), **Users/roles**, **Gold price** (manual / auto, test connection).

---

## 2. UX friction and redundant interactions

Severity: **H** = affects a daily or money-handling flow, **M** = recurring annoyance or risk, **L** = polish. Each item says how it was established.

| # | Finding | Evidence | Sev |
|---|---|---|---|
| F1 | **12 of 17 pages sit behind the manager lock**, including the daily-use ones (inventory, stock balance, stocktake, reports, suppliers, market custody). `inAdminArea` covers pages 2, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15. Re-entry of the login password is required to unlock and again after idle (`IDLE_LOCK_SECONDS = 900`). This is an owner decision (2026-11-13, comment at `Main.qml:62`), so any change is a policy question, not a UI one. A docstring at `manager_gate_viewmodel.py:1` still says "60 seconds", contradicting the constant at line 9. | [R] `Main.qml:62-66,2122`; `manager_gate_viewmodel.py:1,9` | H |
| F2 | **Sidebar exposes 5–6 destinations; everything else is 2–4 steps deep** (table 1.4). Inventory, a daily screen, is not in the sidebar. The الإدارة page doubles as a navigation hub with 10 buttons; the Settings landing adds 13 cards. | [R]/[D] | H |
| F3 | **Modal sprawl and two primitives.** 111 modal objects: `Dialog` (41) + `SettingsDialog` (9) vs `Popup` (60, all of Suppliers/MarketTrips/Reports/History/Users are Popups). No shared form-dialog component; each defines its own header, width rule and button order. Behaviour differences between Dialog and Popup (focus trap, Esc, overlay dim) were **not** audited per instance. | [M] | H |
| F4 | **Duplicate entry points on the Customers page:** «كشف حساب / تسوية» (header) and the tab «كشف الحساب»; «المشطوب» (header) and the tab «الرصيد المشطوب»; the embedded account page repeats «فواتير العميل» in its own (hidden when embedded) tab bar while the host has «الفواتير». | [R] `Main.qml` ~3073, 3090, 3519-3541; `CustomerAccountPage.qml:9,128-135` | M |
| F5 | **Possibly contradictory label:** `Main.qml:3548` shows «السجل للعرض فقط» (read-only log) next to the history tabs, while the embedded `CustomerAccountPage` exposes settlement, deposit, refund and legacy-balance buttons. Whether the label is visible together with those buttons depends on the active tab; **[U] not verified in a running UI.** | [R] | M |
| F6 | **Inconsistent back affordances.** Six identical «رجوع  Esc» labels plus «←  الإدارة» (`settingsBack`) and page-specific `onBackRequested` targets (reports → 10; stock balance and stocktake → 2; suppliers → 6 or 10 depending on mode; users → 6). `Shortcut { sequence: "Esc" }` exists only in `CorrectionWorkspace`, `ExchangeWorkspace`, `ReturnWorkspace`, `InvoiceHistoryPage`, `InvoicePage` and `SettingsPage` (`Main.qml:5510`). | [R] | M |
| F7 | **Keyboard support is concentrated in billing.** F2–F9, Ctrl+M, Ctrl+S, Alt+1/2/3 exist only in `InvoicePage.qml:284-301` and F3/F5/F7/Esc in `ExchangeWorkspace.qml`. No `KeyNavigation`, no Tab-order handling, no `Accessible.*` property anywhere in QML. `activeFocus` styling appears 67 times, `focusPolicy` 4 times. | [M] | M |
| F8 | **Repeated hand-wired navigation:** the same "open invoice in history" block (`currentPage = 1; billingWorkspace.currentTab = 1; invoiceHistoryVM.openInvoice(id)`) is copied in four handlers of `Main.qml` (`onInvoiceRequested`). A related `onCorrectionRequested` handler does the same for the correction workspace. Behaviour drift risk, not a user-visible defect today. | [M] `grep -c` = 4 | L |
| F9 | **Two-step confirmations vary by flow:** deposit and legacy balance use `متابعة` → `تأكيد`; settlement `تأكيد التسوية`; invoice finalize opens `confirmFinalizeDialog` and may add `makingConfirmDialog`, `approvalPopup` and a stale-price checkbox; Return/Exchange/Correction use a Popup. Safety is appropriate for money, but the wording and layout of the confirm step are not shared. Click counts per flow were not measured. | [R] | M |
| F10 | **Inventory filter bar overflows horizontally at 1080×680** in the harness: the item-type filter combo, a text filter and a date button extend beyond the page's content width (4 controls at 1080×680, 1 — «تحديد الظاهر» — at 1280×800, 0 at 1440×900). The screenshot shows a bordered control at the right edge of the filter card cut off. Data in the harness is empty, so the real overflow with data is [U]. | [M]+[V] `pages/p02-inventory-1080x680.png` | H |
| F11 | **Dense, tiny text.** 58 literal font sizes are 9–10 px (the type scale's smallest token is `fontCaption` = 10); another 252 literals are 11–12 px. Combined with a 1080×680 minimum window this limits readability, particularly for Arabic in Cairo. Real-world legibility was not user-tested. | [M] | M |
| F12 | **Emoji as icons.** 155 emoji characters (99 in `Main.qml`) are used in nav buttons, titles and buttons. Cairo has no emoji glyphs; rendering depends on the OS fallback font. In the offscreen harness they render as box glyphs (e.g. in the sidebar and page titles). On the real Windows target this is [U]. | [M]+[V] | M |
| F13 | **Home page** (working-tree state): the grid of ~10 equal-weight cards was regrouped under three labelled rows. HEAD is the old flat grid. At 1080×680 the first screen shows today's figures, two follow-up cards and the start of a third. Whether employees find the new order better is not tested. | [V] `home/before-*.png`, `home/after-*.png` | L |
| F14 | **Permission/empty states are plain text.** In the harness (user without management permissions) the pages for الإدارة, المستخدمون, الموردون and عهدة السوق appear (in the low-resolution contact sheet) as a single short line of text, some of it red. If that is so, they give no next step (who to ask, which permission is missing); the exact wording was not read. | [V] `pages/m2.png` | L |

---

## 3. RTL and UI consistency findings

### 3.1 Rules checked against `CLAUDE.md` §3

| Rule | Result |
|---|---|
| No global `LayoutMirroring` | [M] No global enable. `Main.qml:101` documents the rule. Local use exists: `LayoutMirroring.enabled: true` (+ `childrenInherit`) in `Main.qml:9979-9980, 10353-10354` and `InvoicePage.qml:2861, 2896, 2905`; `enabled: false` in `CustomerAccountPage.qml:46`, `FinancialAccountsPage.qml:45`, `SuppliersPage.qml:1852`. What each local use mirrors was **not** examined. |
| Physical right sidebar | [V] The sidebar is on the physical right in every screenshot viewed. How the root layout achieves it was not traced. |
| Cairo everywhere | [M] 631 explicit `font.family: "Cairo"` literals, 66 `theme.fontFamily`. `Main.qml` and some pages hard-code the literal (350 in `Main.qml`, 0 `theme.fontFamily`); pages added later use the theme token. Blocks without any `font.family`: TextField 9 %, ComboBox 21 %, Label 14 % [M heuristic, whole-block match]. These inherit the application font [U]. |
| Right alignment | [M heuristic] explicit alignment in 98 % of 68 raw `TextField`s, 100 % of 8 `TextArea`s, **65 % of 429 raw `Label`s**. For ComboBox the alignment lives in `contentItem`, so this heuristic cannot decide (0 %); ComboBox text/popup alignment is **[U]**. |
| Form row: control left, label on the physical right | [R] used by `CustomerFormRow`, `Pair`, `FieldLabel`; compliance across all forms not audited. |
| `qsTr` / translation | [M] 0 `qsTr` — consistent with the project rule (Arabic literals). |
| Dialogs, tables, warnings RTL | [V] only for the screenshots listed in 8.2. |
| Responsive minimum | [R] window minimum 1080×680. [M] `width` / `preferredWidth` / `minimumWidth` literals of 1000 or more: 2, both in `Main.qml`. |


### 3.2 Visual-token consistency

| Aspect | Measurement [M] | Observation |
|---|---|---|
| Token source | `qml/Theme.qml` (95 lines): 35 colour tokens, radius tokens `radiusSmall` 7 / `radiusMedium` 10 / `radiusCard` 16, 8 type tokens (10–34) | Single source exists. |
| Hex colours outside `Theme.qml` | 59 literals in 13 files: `Main.qml` 27, `ReportsPage.qml` 8, `GoldWebView.qml` 5, the other ten files 1–2 each. Repeated values: `#80000000` ×16 (overlay dim), `#3A2F1A` ×10, `#2A2214` ×10, `#16271F` ×5, `#29241A` ×4 | A scrim token and 4 repeated surface colours are not tokenised. |
| Font sizes | 476 numeric `pixelSize` literals vs 440 non-literal expressions (412 of them start with `theme.`). `Main.qml`: 243 literals, 0 theme tokens; `ManagementReviewPage.qml`: 101 literals vs 0 (and 110 `"Cairo"` literals); `InvoicePage.qml`: 91 vs 0. `DashboardPage`, `ExchangeWorkspace`, `ReturnWorkspace`: 0 literals; `CorrectionWorkspace` 2, `StockBalancePage` 4, `StocktakePage` 4. | Two eras of code coexist. Literal values: 9, 10 (57×), 11 (116×), 12 (136×), 13, 14, 15, 16, 17, 18, 19, 20, 22–24, 38, 40. |
| Radius | 214 `radius:` lines are expressions; numeric literals: 4 (65×), 6 (36×), 3 (24×), 10 (22×), 11 (7×), 2, 8. Theme small = 7, none of 4/6/3 equal it. | Visible differences of 1–3 px; token not used for most radii. |
| Control heights | `implicitHeight` literals 24, 30, 32, 34, 36, 38, 40; `Layout.preferredHeight` literals 30, 34, 40, 44, 92. | Same role (button, field) varies across pages; 6 buttons at 24 px. |
| Dashboard `boost` | `DashboardPage` adds `+2` pt to every themed size (`boost: 2`, owner decision 2026-10-22). | Intentional; breaks 1:1 with other pages. |

### 3.3 Architecture-boundary observations (QML)

- [M] No SQL in QML (one grep hit is a comment, `Main.qml:826`).
- [R] Arithmetic on weights exists in QML (floats): `Main.qml:11896-11905` computes `resultWeightNumber() − consumedOldGoldNumber()` in the *new inventory item* dialog and renders `.toFixed(3)`; formatting helpers `toFixed(...)` appears 8 times in `Main.qml` (e.g. line 886 `weightText`, 11755, 12269, 12780, 13024, 13733, 13842). 30 `Math.round` calls exist in `Main.qml`; the two at 7320/9100 and similar are **not classified** (positioning vs value) [U]. Whether any of these gates a save or only displays a hint was **not** verified. Reported because `CLAUDE.md` §4 requires Decimal for weights and forbids core logic in QML.

---

## 4. Reusable component inventory

### 4.1 Shared top-level QML files (`qml/`)

| Component | Role | Used by [R] |
|---|---|---|
| `Theme.qml` | token singleton instance (`Theme {}` is instantiated per file) | every page |
| `CustomerPicker.qml` | searchable customer chooser with popup | Main (2), FinancialAccounts, InvoiceHistory |
| `BarcodeField.qml` | barcode input with scanner handling | InvoicePage, InvoiceHistory, Stocktake, ManagementReview, Main (3), ExchangeWorkspace |
| `DateField.qml` | date input with a calendar popup | FinancialAccounts (2), InvoiceHistory (2), Reports (2) |
| `CustomerNameLink.qml` | clickable customer name (Enter/Space) | multiple |
| `AccountHistoryPanel.qml` | account ledger panel | financial accounts |
| `ChartKit.js`, `ChartTip.qml` | canvas chart helpers and tooltip | Dashboard, Reports |
| `GoldWebView.qml`, `GoldWebChart.qml` | embedded web charts | Dashboard |
| `BillingWorkspace.qml` | tabbed host for invoice, history and the three after-sale workspaces | Main |

### 4.2 Inline components defined in `Main.qml` (38)

`GoldPriceLabel/Button/Input/Grid`, `BalanceBox`, `NavButton`, `CustomerLabel/Button/Field/Combo/Card/Detail/FormRow/MessageBox`, `SettingsSectionCard`, `SettingsCard`, `SettingsDialog`, `SummaryBox`, `ActionMessageBox`, `DisabledDeleteZone`, `PrinterChoice`, ten `*Settings` section cards, and the page components `InventoryPage`, `CustomersPage`, `SettingsPage`, `FactoriesPage`, `ItemTypesPage`, `CategoriesPage`.

### 4.3 Components re-declared in several files [M]

| Name | Declared in (files) | Note |
|---|---|---|
| `Txt` | 13 | bodies are **not** identical (the earlier audit found 9 distinct bodies; re-count not repeated) |
| `Btn` | 10 | |
| `Field` | 10 | |
| `Section` | 7 | |
| `Combo` | 5 | |
| `Pair` | 4 | |
| `Card`, `Stat` | 3 and 2 | `DashboardPage`, `SuppliersPage`, `UsersAdminPage` |
| `Head`, `Choice`, `WeightTotals`, `BarcodeEntry`, `MoneyField` | 2–3 | Exchange / Return / History / Suppliers / Reports |

Shared-by-copy parallel families: `Customer*` (Main), `GoldPrice*` (Main), `Setup*` (FirstRun), `Login*`, `Picker*`, `Invoice` family (`FieldLabel`, `AmountField`, `PrimaryButton`, `SmallButton`, …). Roughly six distinct "label / input / button" families therefore exist.

### 4.4 What is reused well

Charts (`ChartKit.js`, `ChartTip.qml`), `CustomerPicker` (independent state per instance), `BarcodeField`, `DateField`, and the `SettingsDialog` + `SettingsSectionCard` pair for settings.

---

## 5. Prioritised improvements

Ranking = business impact on daily/money flows ÷ implementation risk. All items are **recommendations only**; none was started. "Risk" refers to regression of money/legal flows and test churn, given the architecture lock (QML → ViewModel → Service → Repository).

| Rank | Improvement | Why (evidence) | Impact | Risk | Needs owner decision? |
|---|---|---|---|---|---|
| 1 | Add one automated **every-page × three-size overflow guard** in the test harness (prototype: the scratch measurement used here) | F10 shows an overflow reached HEAD; existing tests cover pages individually (46 UI/QML test files) but not a global sweep | High | Low (tests only) | No |
| 2 | Wrap the inventory filter bar so it reflows at 1080 px | F10 | High (daily screen) | Low (layout only, one file) | No |
| 3 | Define a **shared form-dialog wrapper** (width rule, header, Esc, Save/Cancel order) and adopt it first in Suppliers and MarketTrips (43 popups) | F3 | High | Medium (many files; adopt incrementally) | No |
| 4 | Extract the duplicated primitives (`Txt`, `Btn`, `Field`, `Combo`, `Section`, `Pair`, `Card`, `Stat`) into shared files and migrate page by page | §4.3: 13/10/10 duplicate declarations | High (consistency, maintenance) | Medium–High (touches most pages; each has real-click tests) | No (architecture-neutral) |
| 5 | Replace hex literals and repeated surface colours with tokens (incl. a `scrim` token for `#80000000`) | §3.2: 59 literals | Medium | Low | No |
| 6 | Move literal font sizes and radii to the type/radius scale; review the 58 uses at 9–10 px for legibility | F11, §3.2 | Medium | Medium (visible change at each site) | Yes — the scale is a locked UI rule; the owner said on 2026-11-18 that locked look rules may change when it clearly improves the look (look only, never money or architecture rules) |
| 7 | Customers page: unify duplicate entry points and clarify the "read-only" label | F4, F5 | Medium | Low | No |
| 8 | Standardise back/Esc behaviour and labels across pages | F6 | Medium | Low | No |
| 9 | Review the manager-lock placement for inventory/stock pages (policy only: UI could keep the lock but show unlock state and return target) | F1, F2 | High | Medium (security-adjacent) | **Yes** |
| 10 | Surface frequently used management destinations (inventory, stock balance) closer to the sidebar | F2 | High | Medium | **Yes** |
| 11 | Extend keyboard focus order, `Accessible` names and shortcuts beyond billing | F7 | Medium | Medium | No |
| 12 | Verify emoji rendering on the Windows target; replace with theme-coloured text/icons if they fall back to boxes | F12 | Medium | Low | Look change — owner decision |
| 13 | Classify the QML float arithmetic on weights (display-only vs gating) and move any gating arithmetic to the service/view model | §3.3 | Medium (rule compliance) | Medium | No |
| 14 | Replace the repeated "open invoice in history" handlers with one function | F8 | Low | Low | No |
| 15 | Give permission/empty states a next step | F14 | Low | Low | No |

Suggested order: 1 → 2 → 7 → 8 → 5 → 3 → (owner decisions 9, 10) → 4 → 6/11/12/13.

---

## 6. Architecture and affected-file references

- Flow: `qml/*.qml` → `app/viewmodels/*.py` (about 45 modules) → `services/*.py` (about 95 modules; counts come from directory listings) → `repositories/` → MySQL. View models are exposed to QML by `main.py` (`create_operational_viewmodels`, `ApplicationController`).
- Page stack and navigation state: `Main.qml` — `currentPage`, `inAdminArea` (lines 63-66), `pageStack` (2188), sidebar (~2480+), `managerGate` view model `app/viewmodels/manager_gate_viewmodel.py`.
- Largest files: `Main.qml` 14,724; `SuppliersPage.qml` 4,378; `MarketTripsPage.qml` 3,254; `InvoicePage.qml` 2,950; `ManagementReviewPage.qml` 1,725; `InvoiceHistoryPage.qml` 1,460.
- Files affected by each improvement: 1 → `tests/` harness (`tests/test_invoice_page_qml.py` helpers `item`, `visible_item`, `SIZES`); 2 → inventory section of `Main.qml` (component `InventoryPage`, from line 671); 3, 4 → `SuppliersPage.qml`, `MarketTripsPage.qml`, `Main.qml`, new shared QML files; 5, 6 → `Theme.qml` and every page listed in §3.2; 7 → `Main.qml` ~3066-3560, `CustomerAccountPage.qml`; 8 → `Main.qml` (settings, users, stock pages), each page's `onBackRequested` in the stack (lines 2188-2380); 9, 10 → `Main.qml:62-66, 2122`, `ManagementReviewPage.qml`, `manager_gate_viewmodel.py`.
- Constraints that bound any change: Arabic RTL, no global `LayoutMirroring`, Cairo, right sidebar, 1440×900 / 1280×800 / 1080×680 checks, BIGINT ids as strings, actor never from QML, `objectName`s relied upon by tests (2,083 in QML) must be preserved.

---

## 7. Baseline metrics [M]

Static, from `qml/*.qml` (working tree at audit time; scripts in 9). "Per-file" tables are in the session scratchpad and are reproducible with the commands in 9.

| Metric | Value |
|---|---|
| QML files / lines | 29 `.qml` + 1 `.js`; 35,955 `.qml` lines (Main 14,724 = 41 %) |
| Pages in stack / sidebar entries / pages behind manager lock | 17 / 6 / 12 |
| Modal objects (Dialog / Popup / SettingsDialog / Menu) | 111 (41 / 60 / 9 / 1) |
| Inline `component` declarations | 151 (Main 38, InvoicePage 14, SuppliersPage 13, ManagementReviewPage 11, …; includes the home page's new `SectionTitle`) |
| Names declared in more than one file | 13 (`Txt` ×13, `Btn` ×10, `Field` ×10, …) |
| `objectName`s (testability) | 2,083 |
| Buttons / text inputs / combos (declaration count) | 638 / 327 / 111 |
| Hex colours outside Theme / Theme colour tokens | 59 / 35 |
| Numeric font sizes / token sizes / sizes ≤ 10 px | 476 / 440 / 58 |
| Emoji characters | 155 |
| `qsTr` / `LayoutMirroring` uses / `Accessible.` uses / `KeyNavigation` | 0 / 11 (local) / 0 / 0 |
| `Shortcut`/`Keys.on…` | 41 (InvoicePage 25) |
| `ToolTip` uses / `Flow` (wrapping) / `ScrollView`+`Flickable` | 28 / 35 / 44 |
| `activeFocus` / `placeholderText` / `closePolicy` | 67 / 197 / 83 |
| UI/QML test files / test methods defined | 46 / 422 (counted statically, **not executed in this audit**) |
| Window minimum | 1080×680 |

Harness measurement (horizontal overflow of buttons / inputs / combos inside the page area; harness data, so pages needing permissions or data are under-represented):

| Page | 1440×900 | 1280×800 | 1080×680 |
|---|---|---|---|
| Billing (1) | 0 | 0 | 0 |
| Inventory (2) | 0 | 1 | **4** |
| Customers (3) | 0 | 0 | 0 |
| Cash movement (4) | 0 | 0 | 0 |
| Settings landing (6) | 0 | 0 | 0 |
| Factories / item types / categories (7–9) | 0 | 0 | 0 |
| Stock balance (11), Stocktake (12) | 0 | 0 | 0 |

No QML warnings were emitted while opening the 17 pages at 3 sizes [M]. Pages 0, 5, 10, 13, 14, 15, 16 produced a home/summary view, a permission message or an empty state in the harness, so their overflow figure (0) is **not** evidence that the real pages fit [U].

**Not measurable here** (no instrumentation or users): task completion time, click counts for real tasks, error rates, learnability, satisfaction, perceived performance, real data volume behaviour. Navigation depth (1.4) is derived, not click-tested.

---

## 8. Coverage and limits

### 8.1 What was inspected

- Source reading: `Main.qml` shell, page stack, sidebar, navigation guard, settings landing, customers header/tabs, dialog list; `BillingWorkspace.qml`; `ManagementReviewPage.qml` action row; shortcuts in all files; `CustomerAccountPage.qml` buttons; `Theme.qml`; `manager_gate_viewmodel.py`; `management_review_viewmodel.py`; `report_service.py` report titles.
- Static scans over every `qml/*.qml` (section 9).
- Harness screenshots and measurements (8.2).

### 8.2 Screenshots actually viewed

Produced offscreen into the session scratchpad (outside the repository), then viewed:

- Home page: `home/before-tall.png` (1440, before the regrouping), `home/after-tall.png`, `home/after-1080.png`, `home/after2-tall.png` (1080 wide, grouped).
- `pages/p02-inventory-1080x680.png` (full resolution).
- Contact sheets at low resolution: `pages/m1.png` (billing, inventory, customers, cash, reports, settings at 1080×680) and `pages/m2.png` (management, stock balance, stocktake, users, suppliers, market custody). Text in the contact sheets is **too small to read**; they were used only to see whether a page showed content, a permission message or an empty state.
- The other ~40 captured images (1440×900 and 1280×800 variants) were generated but **not viewed**.

### 8.3 Not inspected (explicit)

- The real application with real data and permissions; login and first-run screens (`Login.qml`, `FirstRunOwnerSetup.qml`) were not rendered or viewed.
- Pages with meaningful content under the full-permission set: الإدارة (10), التقارير (5; the 19 reports), المستخدمون (13), الموردون (14), عهدة السوق (15), السعر العالمي (16) — only permission/empty states were rendered.
- Every dialog and popup (111) — none was opened; widths and contents come from source only.
- The iPad reports web app (`webapp/`, including `static/`), printed output and A5 invoice layout, barcode label printing, and the tray/exit flow.
- Real ComboBox popups and text alignment, real scroll behaviour, hover/pressed/disabled states, animation, colour contrast ratios, performance, DPI scaling above 100 %, touch, screen readers.
- Whether the tests in the 46 UI/QML test files currently pass: **none was executed for this audit.** (A separate home-page task earlier in the session ran `tests.test_dashboard`; that is outside this audit and its last run was interrupted.)
- Database, services, repositories and business rules: not audited; the architecture notes come from `CLAUDE.md` and file reading only.
- Whether the existing manager-lock, return-window and other business rules are correct: out of scope.

### 8.4 Known limits of the measurements

- Counts of controls and blocks come from regular expressions over source text (not a QML parser): they over- or under-count for nested/aliased types and for custom components. The `Label`/`TextField` alignment percentages in 3.1 are heuristics.
- The overflow metric counts only items whose class name contains Button, TextField, ComboBox, CheckBox, TextArea, RadioButton, Switch, SpinBox, TabButton, Btn, Field, Combo or Action and that sit inside the page stack; inner scrolled content is counted by position and may produce false positives or negatives.
- Harness fonts differ from a real Windows run (emoji boxes, glyph metrics).

---

## 9. Method and reproduction

Only read-only commands and scratch scripts in the session scratchpad were used (nothing was written into the repository except this file).

- State: `git branch --show-current`, `git log --oneline -3`, `git status -s`, `wc -l qml/*.qml`.
- `ux_metrics.py`: per-file counts (dialogs, inline components, hex literals, font families and sizes, objectNames, buttons/inputs, emoji, shortcuts, `LayoutMirroring`, `ToolTip`, `Flow`, scroll containers, large widths, confirm words, alignment) → `ux_metrics.json`.
- `dialogs.py`: top-level `Dialog | Popup | SettingsDialog | Menu` blocks with objectName and the first `width:` found in 16 lines.
- `rtl_scan.py`: brace-matched `TextField | TextArea | ComboBox | Label` blocks checked for alignment and `font.family`.
- `audit_pages.py` (subclass of `tests.test_dashboard.DashboardPageTests`, offscreen, blocked database): visits pages 0–16 at 1440×900, 1280×800, 1080×680; saves screenshots; counts horizontally overflowing interactive items; collects QML warnings.
- `montage.py`: composes low-resolution contact sheets with `QPainter`.
- Targeted `grep`/`sed` for line references quoted above.

Reproduce metrics with the scratch scripts, or re-run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -B -m unittest <module>` using a subclass as above. Line numbers drift whenever `Main.qml` changes.
