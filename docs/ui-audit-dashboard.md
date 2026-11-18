# Dashboard UI audit (Phase 1, step 1) — 2026-11-18

Read-only audit of the main dashboard before any redesign. No code was changed by this step.

## Entry point and data flow

- Page: `qml/DashboardPage.qml` (777 lines), created in `qml/Main.qml` (`DashboardPage { ... }` in the page stack, index 0, sidebar button «🏠 الرئيسية»).
- View model: `app/viewmodels/dashboard_viewmodel.py` -> `DashboardViewModel` (`dashboardVM`, created by `main.py` through `_dashboard_factory(self.session)`; `activate()` refreshes when the page is entered, `shutdown()` on logout).
- Service: `services/dashboard_service.py` -> `DashboardService` (read-only aggregates; money as Decimal, display strings built here / in the view model).
- Repository: `repositories/dashboard_repository.py` -> `DashboardRepository` (plain SELECTs, `invoice_date` base like the reports; shared price series with the reports in `repositories/price_series_reads.py`).
- Permissions: each block is gated by `canSeeSales`, `canSeeBalances`, `canSeeStock` (service-side checks, QML hiding is only a convenience).

## What the page shows today

- «شغل اليوم» hero card (invoice count, sales, scrap purchases) first; price chart (21K buy/sell + average), spread chart, sales-days columns, customer balances, overdue debts, making averages, currency rates, below-minimum stock, dead stock, insights, gold news, live gold web chart.
- Five older cards (price, price hint, sold weight, old gold, scrap today) are hidden behind `showRetired: false` (owner 2026-11-10).
- Charts use `qml/ChartKit.js` + `qml/ChartTip.qml` (shared, dataviz rules). Cards have a neutral 1 px `theme.cardBorder`.

## Design system that already exists

- `qml/Theme.qml` (92 lines) is the single token source: colours (`bgPrimary/Secondary/Tertiary`, `gold*`, text, meaning colours `priceBuy/priceSell`, `accountReceivable/Payable`, karat colours), radii (7 / 10 / 16), Cairo type scale (`fontPageTitle` 18 ... `fontCaption` 10). Four dark accent palettes (gold, blue, green, violet) chosen by `themeVM`.
- Page-local inline components, repeated per page: `Txt`, `Card`, `Line`, `Empty`, `Stat`, `Action` in DashboardPage; `Txt/Btn/Field/Section/Pair` in other pages (9 distinct bodies of `Txt` across 13 files, so they are NOT interchangeable).
- Shared inline components in `Main.qml`: `NavButton`, `SettingsSectionCard`, `SettingsCard`, `SummaryBox`, `SettingsDialog`, `GoldPrice*`, `CustomerField/Combo`.

## Shell around the dashboard (Main.qml)

- Header strip: shop identity, date, user, live balances (cash, karat balances, gold price card).
- Sidebar on the physical RIGHT (locked), buttons: الرئيسية، الفواتير، حركة النقدية، العملاء، الإدارة (reviews, suppliers, market custody, reports and settings are sections inside الإدارة behind the manager lock).

## Constraints that apply to any redesign

- CLAUDE.md section 3: dark/gold masGold look, Cairo, RTL, physical sidebar on the right, no global `LayoutMirroring`. The owner allowed look changes that clearly improve the UI (memory, 2026-11-18); money, architecture and permission rules are unchanged.
- UI only: no accounting, schema, migration, database data or workflow change.
- 1440x900, 1280x800, 1080x680 must not clip.

## Gaps against the Phase 1 brief

- No light/navy palette exists: `textPrimary` etc. are fixed light colours, so a light background needs a full second token set, not only the dashboard.
- Quick actions: the page has an `Action` component and sidebar navigation; there is no dedicated quick-action row for supplier / customer / treasury movements.
- No `AppColors / AppTypography / AppSpacing / AppButton / AppCard / AppTable / AppInput / AppDialog / AppNavigation` files; `Theme.qml` plays the colour + type + radius role.
