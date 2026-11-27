# Task 07 — Typography Audit and Safe Scaling Plan

Read-only audit. Written 2026-11-26. No application, test, theme or QML file was changed; nothing was committed, stashed or reverted.
Evidence tags: **[M]** measured, **[R]** read in source, **[D]** derived from measured values, **[U]** unknown / not verified.

## 0. Repository state

| | Value |
|---|---|
| Branch | `feature/purchasing-suppliers-v1` |
| HEAD | `393f818` (Invoice customer change posts compensating ledger rows, migration 110) |
| Working tree before | ` M docs/feature-history.md`, ` M qml/DashboardPage.qml`, ` M qml/Main.qml` (Task 06 Flow), ` M qml/SuppliersPage.qml` (Task 04), ` M tests/test_dashboard.py`, ` M tests/test_inventory_stock_qml.py`, ` M tests/test_supplier_gold_settlement_ui.py`; untracked `.claude/skills/*`, `docs/ux/`, `docs/ux2/` |
| Working tree after | identical (`git status --porcelain=v1` diffed before/after: unchanged) apart from this report as a new file in `docs/ux2/` |

Inventory's Task 06 `Flow` (`qml/Main.qml`, `component InventoryPage`, filter card) was only read.

## 1. Methods and commands

All scripts ran from the session scratchpad, outside the repository, and wrote only there.

1. **Static scan** (`typo_static.py`, project `.venv`, isolated mode): every `font.pixelSize:` / `pixelSize:` occurrence in `qml/*.qml`, including inline ones on a line with other properties, comments excluded; classified `literal` / `theme` (a single `theme.fontX` token) / `expr` (anything else); the enclosing QML type found by brace tracking; `objectName` and text found within ±10 lines. Expressions were evaluated where they reduce to arithmetic (theme tokens substituted; `dashboard.boost` = 2 in `DashboardPage.qml`); the remaining 13 are reported as unresolved.
2. **Runtime scan** (`typo_runtime.py`): the real `Main.qml` in the offscreen test harness (fake services, blocked database, manager lock open) at 1280×800; effective pixel size of every visible text-bearing item on each of the 17 pages.
3. **Font metrics**: `QFontMetrics` of the bundled `resources/fonts/Cairo.ttf` at 10–22 px (line height and the width of «الرصيد: 1,250.50 ج»).
4. **Layout counters**: regex counts of numeric fixed heights, fixed `Layout.*Width` values and `elide:` declarations.
5. `git branch`, `git log`, `git status --porcelain=v1` before and after.

## 2. Current typography inventory

### 2.1 Declarations (re-measured on the current source) [M]

| Measure | Now | Baseline (Task 01) |
|---|---|---|
| `pixelSize` declarations | **916** in 23 of 29 QML files | (not stated) |
| Literal sizes | **476** | 476 (identical) |
| Single theme token | 359 | |
| Other expressions | 81 (64 mention a theme token) | 440 expressions total (359 + 81 = 440, identical); "412 theme-based" is not reproduced exactly (359 + 64 = 423; counting method of the baseline unknown) |
| Literal 9–10 px | **58** (one 9 px, 57 at 10 px) | 58 (identical) |
| Literal 11–12 px | **252** (116 + 136) | 252 (identical) |

Literal sizes: 9×1, 10×57, 11×116, 12×136, 13×49, 14×29, 15×22, 16×6, 17×15, 18×19, 19×3, 20×14, 22×1, 23×2, 24×2, 38×3, 40×1. Theme tokens (`qml/Theme.qml:86-93`): `fontTableHeader` 11 ×162, `fontSection` 14 ×71, `fontBody` 12 ×53, `fontBodyStrong` 13 ×36, `fontCaption` 10 ×23, `fontPageTitle` 18 ×9, `fontNumber` 22 ×4, `fontPriceHero` 34 ×1.

Effective size over all 916 declarations (expressions evaluated; lower bound for the 13 unresolved): **≤10 px: 85; 11 px: 281; 12 px: 220; 13–14 px: 202; ≥15 px: 115; unresolved: 13.** So 366 of 916 declarations (40%) are at 11 px or smaller.

### 2.2 Where the small sizes are [M]

| Area | Decls | ≤10 | 11 | 12 | 13–14 | ≥15 |
|---|---|---|---|---|---|---|
| `SuppliersPage.qml` | 125 | 4 | **82** | 8 | 26 | 4 |
| `ManagementReviewPage.qml` | 101 | 1 | 23 | 56 | 18 | 3 |
| `Main.qml` shared boxes + dialogs (8331–end) | 98 | **23** | 19 | 26 | 6 | 24 |
| `InvoicePage.qml` | 95 | 6 | 12 | 28 | 31 | 18 |
| `MarketTripsPage.qml` | 72 | 0 | **44** | 5 | 22 | 0 |
| `DashboardPage.qml` (already +2 "boost") | 50 | 0 | 2 | 26 | 16 | 6 |
| `InvoiceHistoryPage.qml` | 43 | 6 | 9 | 8 | 19 | 1 |
| `Main.qml` Factories/ItemTypes/Categories (5764–8330) | 40 | 7 | 11 | 3 | 6 | 13 |
| `Main.qml` Customers (2861–4513) | 31 | **11** | 8 | 3 | 1 | 8 |
| `Main.qml` InventoryPage (671–2732) | 32 | 3 | 11 | 5 | 7 | 6 |
| `Main.qml` Settings (4514–5763) | 32 | 1 | 26 | 2 | 0 | 3 |
| `FinancialAccountsPage.qml` | 21 | 2 | 3 | 5 | 7 | 3 |
| `CustomerPicker.qml` | 6 | **5** | 0 | 1 | 0 | 0 |
| others (Exchange 5, Return 3, Users 2, Stocktake 2, DateField 2, ChartTip 1) | | 15 | | | | |

By element type (declarations): `Txt` 362 (24 ≤10, 157 at 11), `Label` 355 (44 ≤10, 62 at 11), `GoldPriceLabel` 59 (42 at 11), `CustomerLabel` 30 (11 ≤10), `TextField` 22 (all 12–14), `Button` 20 (14 at 12), `ComboBox` 10 (all 12). **Input text, buttons and combos are not the small ones: the small sizes are labels, notes, ledger detail and status text.** [M]

### 2.3 The dashboard is already the reference for "read from a distance"

`DashboardPage.qml:26` `readonly property int boost: 2` (owner review 2026-10-22) adds 2 px to every themed size; it has no declaration below 11 px. `InvoiceHistoryPage.qml:25-26` does the same locally (`fontTable = fontBody + 2`, `fontTableHead = fontTableHeader + 2`). The codebase therefore already accepts "+2 on reading-heavy pages, done per page" as a pattern. [R]

### 2.4 Runtime view (harness, 1280×800, fake data) [M]

1083 text items across the 17 pages: 20 at 10 px, 105 at 11, 498 at 12, 81 at 13, 136 at 14, 78 at 15, and 165 above that. Per page the text at ≤10 px is 1–2 items (the sidebar footer «masGold v2» at 10 px on every page and a few labels); Inventory shows 5 labels at 11 px (the filter labels) and one at 10 px (the «الظاهر: N قطعة» counter). Suppliers and Market Trips show one visible text item because the harness has no supplier data, so for them the static scan is the evidence. Sidebar: `NavButton` text is declared 12 px but the visible label inside it measures 14 px (selected: bold) [M]; the gold-price card labels are 11, 13, 14 and 20 px.

### 2.5 Font facts that matter for layout [M]

Cairo line heights (px) at font size 10 / 11 / 12 / 13 / 14 / 15 / 16 / 18 = **19 / 20 / 23 / 24 / 26 / 29 / 30 / 33**. Width of «الرصيد: 1,250.50 ج»: 78 / 81 / 92 / 98 / 105 / 109 / 119 / 130 px. A step 10→11 costs about +1 px height and +4% width; 11→12 costs +3 px and +14% width; 12→13 +1 px and +7%. The real application sets `QFont("Cairo", 11)` (`main.py:527`), but the harness does not; controls that declare no size can therefore differ between the harness and the real app (see §9).

### 2.6 Consistency of the same role across pages [R]/[M]

| Role | Sizes seen (examples) | Comment |
|---|---|---|
| Page title | 18 (`fontPageTitle`: `FinancialAccountsPage.qml:221`, `InvoiceHistoryPage.qml:590`) vs **20** (`Main.qml:922` Inventory, `StockBalancePage.qml:53`) | 2 px apart for the same role |
| Section / card title | 14 (`fontSection`, 71 uses) vs 15 (`AccountHistoryPanel.qml` title) vs 16–17 | mostly tokenised |
| Table header | 11 (`FinancialAccountsPage.qml:119-122`, in a 28 px band), 13 (`InvoiceHistoryPage.qml:26`), **14** (`InvoicePage.qml:433` `HeadCell`), 10 (`InvoiceHistoryPage.qml:67` `Head` default) | four different sizes |
| Table cell | 12 default, 14 (`InvoiceHistoryPage.qml:25`), **15** (`InvoicePage.qml:423` `Cell`) | |
| Key figure / balance | 22 (`fontNumber`), 20 (`CustomerAccountPage.qml:107`), 18 (`:99`, `Main.qml` write-off balance), 15–19 (`InvoicePage.qml:2508`, sidebar 15/18), 14 (`AccountHistoryPanel.qml:158`) | six values for one role |
| Customer balance line | 12 bold in the Customers list (`Main.qml:3320`), 12 in the picker result row, **10** in the picker's selected card (`CustomerPicker.qml:174`) | same data, 10 vs 12 |
| Helper / status / note | 10 (`fontCaption`), 11 (`fontTableHeader`, used as a note size in 81 places of `SuppliersPage.qml`) | the "table header" token is used for ledger detail, warnings and hints |
| Warning / validation text | 10 (`InvoicePage.qml:617`, `:653`, `ReturnWorkspace.qml:354`), 11 (`SuppliersPage.qml:1639,1769`), 12 (default) | |

Not every small size is a defect: dense metadata (§4, P3) is legitimately small. The defect is the **role inconsistency** and the money/warning text that sits at 10 px.

## 3. Findings and risk classification

Suggested sizes respect the fixed heights listed in §5.

### P1 — frequently read or operationally important text that is too small

| # | File:line | Component / objectName | Now | Suggest | Reason |
|---|---|---|---|---|---|
| 1 | `CustomerPicker.qml:174` | selected-customer balance line (`PickerLabel` in `customerPickerOpenButton`) | 10 | 12 | A customer's money position shown 2 px smaller than the same value in the result list (`:320`, 12). Card height must grow (§6) |
| 2 | `CustomerPicker.qml:328` | active-warnings count in a result row | 10 | 11 | A customer warning (amber) |
| 3 | `CustomerPicker.qml:187` | picker error message | 10 | 11 | an error the user must read |
| 4 | `InvoicePage.qml:653` | `deadCashText` («⚠️ تنبيه: سبق شطب رصيد لهذا العميل.») | 10 bold | 11 | operational caution on every invoice for that customer; badge is 22 px high (`:641`), 12 px would need ≥ 26 |
| 5 | `InvoicePage.qml:617` | customer code / same-mobile warning | 10 | 11 | |
| 6 | `InvoicePage.qml:2730` | customer search result line (mobile, place, code, warnings) | 10 | 11 | disambiguates customers with the same name |
| 7 | `Main.qml:3694,3721` | Customers history rows: reference/date line and «الرصيد قبل الشطب» (`CustomerLabel`) | 10 | 11 | write-off history values |
| 8 | `ReturnWorkspace.qml:353-354` (`priceWarning`) and `ExchangeWorkspace.qml:622-623` (`exchangeReview`) | price warning / review text (amber, `fontCaption`) | 10 | 11 | price and review warnings |
| 9 | `SuppliersPage.qml:983-992`, `1525-1548` | settlement and making-payment rows: detail/state and «الرصيد:» lines | 11 | 12 | balance after each ledger movement |
| 10 | `SuppliersPage.qml:679,687`, `1361,1373`, `1795,1889,1997,2058` | supplier gold-account balance label, marketer cash/gold values, «موقف المصنعيات» positions | 11 | 12 | key money / gold positions |
| 11 | `MarketTripsPage.qml:850` | `meltAnalysisValue` (and its label) | 11 | 12 | analysis result values |

### P2 — secondary text that would benefit from a modest increase

| # | File:line | Component | Now | Suggest | Reason |
|---|---|---|---|---|---|
| 1 | `CustomerPicker.qml:310,316` | result row: id/status line, phone/place line | 10 | 11 | secondary but read to choose |
| 2 | `Main.qml:3290,3311,3355` | Customers list: status label, «آخر حركة», «صفحة» | 10 | 11 | |
| 3 | `FinancialAccountsPage.qml:119-122` | movement-table header band (28 px) | 11 | 12 | header of a daily-use table; fits (line 23 px) |
| 4 | `InvoiceHistoryPage.qml:551,711` | `historyNext` pagination, `historyCustomerName` label | 10 | 11 | |
| 5 | `ExchangeWorkspace.qml:314,325,545,738` | explanatory lines, replacement source, calculating status | 10 | 11 | |
| 6 | `StocktakePage.qml:359,400` | batch hint, status | 10 | 11 | |
| 7 | `SuppliersPage.qml` (~60 remaining uses of `fontTableHeader` as a note: 1068-1070, 1207, 1268-1292, 2478-2505, 3162-3280, 4039-4377) | explanatory notes in dialogs and rows | 11 | 12 only after the P1 pilot proves safe | wrapping text; dialogs are 500 px wide |
| 8 | `Main.qml:1960` | inventory item classification tag (new/old stock, bold) | 10 | 11 | a status the stock clerk scans |

### P3 — compact metadata where the current size may be intentional (leave as is for now)

`Main.qml:1386` (Inventory counter «الظاهر: N قطعة», inside the Task 06 filter card), `Main.qml:2715` (sidebar footer «masGold v2», hidden under 720 px height), `Main.qml:14537` (the only 9 px: «إعادة طباعة من» in the print-history dialog), the barcode-print dialog captions `Main.qml:13404-13998` (muted column captions), `Main.qml:8487,8498` (danger-zone notes), `UsersAdminPage.qml:581,583` («للمالك فقط» chips), `InvoicePage.qml:1029` (`tagText` chip), `ChartTip.qml:34`, `DateField.qml:38,82` (calendar day names and placeholder), the `#id` labels in Factories / ItemTypes (`Main.qml:6139,6641`). Chips and tooltips have fixed shapes; enlarging them needs a design decision, not a size bump.

## 4. Safe scaling proposal: five semantic roles (no global system)

Roles are a reading aid for the owner; none is implemented here and no new theme token is introduced by this task.

| Role | Observed now | Proposed | Intended usage | Layout risks | Where |
|---|---|---|---|---|---|
| **A. Money / balance / position text** | 10 (picker card), 11 (supplier balance lines), 12 (customer list), 14–22 figures | **12 minimum** for lines that carry a balance or a position; leave 14–22 key figures | any text that states an amount owed, a balance after a movement or a gold position | fixed-height rows (30/34/44/48), `elide` clipping of long Arabic lines, +14% width from 11→12 | CustomerPicker, Suppliers ledger lines, Market Trips values, Customers lists |
| **B. Warning and validation text** | 10–11 | **11 minimum**, keep colour; 12 where the container is not fixed-height | cautions, errors, acknowledgements | badges with fixed height (dead-cash badge 22 px), wrapped lines in 500 px dialogs | InvoicePage, Return/Exchange, picker, dialogs |
| **C. Secondary row text** (code, phone, place, status, date meta) | 10–11 | **11** (10 → 11) | metadata read to identify a row | +1 px line height, +4% width: low | pickers, Customers, History, Stocktake |
| **D. Table header / field label** | 10–14 (§2.6) | converge on **11–12** locally; no cross-page change in the pilot | column headers and labels above inputs | header bands (28 px) and fixed column widths; filter-bar wrapping | Financial accounts, Inventory filters (do not touch), Suppliers |
| **E. Compact metadata** | 9–10 | **keep** | chips, tooltips, footers, captions | none | P3 list |

Rules: raise only inside one component at a time; never change a size and a container together in the same step unless the container is fixed-height (then change the container by the minimum measured amount); keep bold weights; do not shrink anything; do not change the 12 px base of `Txt` / buttons / inputs.

## 5. Layout impact assessment

Counters over the current source [M]: **406** numeric fixed-height declarations (most common 34 ×60, 30 ×33, 44 ×30, 40 ×26, 38 ×25, 28 ×18), **500** fixed `Layout.preferred/min/maxWidth` values, **418** `elide:` declarations; 151 inline components are reused across pages.

| Risk | Where it would show | Evidence | Viewports |
|---|---|---|---|
| Button height/width | `Btn` components use `implicitHeight: 30` (Suppliers, MarketTrips, InvoiceHistory), `AccountHistoryPanel` buttons 28; Cairo line height at 12 px is 23, 13 px 24, 14 px 26 | a 13 px button text fits 30; 14 px+ needs padding | all |
| Table clipping / row height | `AccountHistoryPanel` rows fixed 48 (13 bold + 11 → 24 + 20 = 44); `FinancialAccountsPage` header band 28; `InvoicePage` `Cell`/`HeadCell` already 15/14 | a +1 px step keeps these rows; 12 px for the second line leaves 1 px (24 + 23 = 47 of 48) | 1080×680 first |
| Dialog overflow | `SuppliersPage` dialogs `width: Math.min(500, page.width - 24)`; text wraps, height grows; 43 of them were measured in Task 02 | a 12 px note wraps ~14% earlier | 1080×680 |
| Filter-bar wrapping | Inventory filter `Flow` (Task 06) now wraps at 1280 and 1080; larger labels would wrap earlier and make the card taller, shrinking the list (already ~1⅓ rows at 1080×680 with two items) | **do not touch** | 1280, 1080 |
| Header / sidebar crowding | gold-price card has 11, 13, 14, 20 px lines; the sidebar footer hides under 720 px height; `NavButton` is 36/40 px high with a 14 px visible label | not a pilot candidate | 1080×680 |
| RTL alignment | `PickerLabel` / `CustomerLabel` / `Txt` right-align with `‏` prefixes; no `LayoutMirroring`; font size does not change alignment, but wider text can reverse the elide side (`ElideLeft` is used in most rows) | only a visual check at 3 sizes proves it | all |

Inventory's Task 06 fix stays untouched; its labels (11 px) and counter (10 px) are on the P3/"do not touch" list.

## 6. Pilot recommendation (exactly one): `qml/CustomerPicker.qml`

**Why.** One self-contained 375-line file; only 5 literal declarations to change; the component is reused (Customers quick picker `Main.qml:3112`, merge dialog `Main.qml:4349`, cash customer picker `FinancialAccountsPage.qml:468`, invoice-history customer dialog `InvoiceHistoryPage.qml:1368`); its small text is the customer's money (P1) and warnings; its result rows are auto-height; it has a standalone QML test that loads the real component without `Main.qml` (`tests/test_customer_picker.py:241`). It is not in the dashboard (uncommitted work), Inventory (Task 06), Suppliers (Task 04 dialog) or `Main.qml` pages.

**Boundaries.** `qml/CustomerPicker.qml` only: `component PickerLabel` usages at lines 174, 187, 310, 316, 328, and `implicitHeight: picker.hasSelection ? 48 : 40` at line 154. The `PickerLabel` base size (12, line 112) and every other line stay.

**Proposed changes.**

| Line | Element | Now → New |
|---|---|---|
| 174 | selected-customer balance line | 10 → **12** |
| 154 | `customerPickerOpenButton.implicitHeight` when a customer is selected | 48 → **52** |
| 187 | error message under the picker | 10 → **11** |
| 310 | result row: id / status / type line | 10 → **11** |
| 316 | result row: phone / place line | 10 → **11** |
| 328 | result row: active-warnings count | 10 → **11** |

**Geometry check [D].** Selected card: name line 12 bold = 23 px + spacing 2 + balance line; padding 2 top and bottom. Today 23 + 2 + 19 (10 px) = 44 = 48 − 4 (it fits exactly). At 11 px it would be 45 > 44, at 12 px 48 > 44, so the height must rise to 52 for a 12 px balance line (a 4 px taller card in four hosts; hosts use `Layout.fillWidth` / `maximumHeight: implicitHeight`). Result rows size themselves (`height: resultContent.implicitHeight + 18`), so +1 px per 11 px line is absorbed; the popup (530 px tall, scrollable) shows slightly fewer rows.

**Expected visual result.** The selected customer's balance reads at the same size as in the result list and the Customers page; phone, place, status and warning counts are slightly larger; no layout change elsewhere; no colour or weight change.

**Tests that must protect it.**
1. Extend `tests/test_customer_picker.py` `CustomerPickerQmlTests` (real component, no database): with a selected customer, the balance label's bottom ≤ the button's bottom minus padding; its `font.pixelSize` is 12; a result row's labels do not exceed the row; the popup stays inside a 1080×680 window.
2. A host check at 1440×900 / 1280×800 / 1080×680 through `tests/test_return_history_ux.py` (history customer dialog) and the Customers page picker (`customersQuickPicker`): picker inside its dialog or page area (use the Task 05 page-rectangle helper pattern, not the window rectangle).
3. Existing: all of `tests.test_customer_picker` and `tests.test_return_history_ux`, each in its own process. Note: no existing test loads the Customers quick picker, the merge dialog picker or the cash-page picker (grep of `tests/` finds only the history-dialog picker), so the host checks in item 2 for those three are new tests, not extensions.

**Must remain untouched.** `Main.qml` (including the picker's host call sites and `font.pixelSize: 11` on `customersQuickPicker`), `DashboardPage.qml`, the Inventory filter card, `SuppliersPage.qml`, `Theme.qml`, `CustomerPickerViewModel` and every service, repository, permission and test other than the ones named above. No dialog redesign.

## 7. Limitations and unverified assumptions

- Static counts give each declaration, not each rendered item (a delegate counts once); effective sizes of the 13 unresolved declarations and of controls that declare no size are unknown. [U]
- The harness does not run `main.py`'s `app.setFont(QFont("Cairo", 11))`: controls without an explicit size (some `Button`, `TextField`, `ComboBox`, `CheckBox` text) may render larger in the real app than the 12 px measured here. The real window was not inspected. [U]
- Runtime figures use fake data; Suppliers, Market Trips, dialogs and list rows with real Arabic text were not measured at runtime. [U]
- Frequency of use is not measured; "frequently read" is inferred from the screen's role (billing, customers, cash, balances). [U]
- Line heights come from `QFontMetrics` of Cairo, not from a rendered QML item; actual `Label` heights may differ by a pixel. The pilot geometry in §6 must be confirmed by the tests above. [D]
- No screenshots were produced for this task.
- Suggested sizes are proposals for owner review, not measured to be better to read.

## 8. Concrete implementation proposal for owner approval (Task 08)

**Task 08 — Customer Picker typography pilot.** Edit only `qml/CustomerPicker.qml` (6 values: lines 154, 174, 187, 310, 316, 328 as in §6) and add the focused assertions to `tests/test_customer_picker.py`; run `tests.test_customer_picker` and `tests.test_return_history_ux` one process each, plus the three viewport sizes for the two host pages; produce before/after harness screenshots of the selected card and the result popup; report the measured heights against the §6 calculation and stop if the measured selected-card content does not fit in 52 px. **Not included:** any other file, token, dialog or page; the 11 → 12 move of Suppliers ledger lines (P1 #9–11, a separate later pilot after this one is reviewed); the Inventory filter area; the dashboard; the sidebar; and the Theme scale.

Awaiting your approval; nothing was implemented.
