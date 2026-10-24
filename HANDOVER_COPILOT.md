# masGold_v2 — handover to Copilot (2026-10-24)

Read `CLAUDE.md` and `AGENTS.md` first: they are the operating contract and the full rule history (schema, business rules, UI rules). This file only says **where things stand** and **what to watch for**. If this file and the two contract files disagree, stop and ask the owner (Ali).

## 1. The project and the owner
- Internal Arabic RTL jewelry / gold-shop system for «مجوهرات ماس». Python + PySide6 / QML + MySQL 8, Windows. Architecture `QML → ViewModel → Service → Repository → MySQL` (no SQL in QML / ViewModels; services enforce rules and re-check rights inside the transaction; actor always from `AppSession`).
- The owner writes Egyptian Arabic, is the only user, and decides business rules. Answer him in Arabic, short, plain. Do not commit, push or apply migrations unless he says so; he pushes himself. Never open or print `Note for password.txt`; `vui_out.zip` stays untracked.
- Money / weights are `Decimal`; BIGINT ids cross QML as strings; Cairo font, RTL, sidebar on the right, check 1440×900 / 1280×800 / 1080×680 with real clicks.

## 2. Repository state
- Branch `feature/purchasing-suppliers-v1`, HEAD `3cfb53d`, **14 commits ahead of origin (not pushed; the owner pushes)**. Working tree clean except the untracked `vui_out.zip`.
- Last full non-DB regression on record: 4429 tests on `fba0fe1` (1 failure since fixed). **No full run since**; the later work was verified with focused modules only (see §5). Run `.\.venv\Scripts\python.exe -B -m unittest discover -s tests` before anything is released.
- Test baseline numbers inside `CLAUDE.md` are historical; do not trust them as current.

## 3. Databases (levels checked 2026-10-23)
| database | role | level |
|---|---|---|
| `masgold_prod` | the shop's real data, run with `run_production.bat` | **77** (074-077 applied by the owner with `tools/migrate_production`, backup first) |
| `masgold_v2` | DEV, `main.py` | 77 |
| `masgold_rehearsal` | disposable rehearsal (`masgold_build` account) | 77 |
- Chain is `database/schema/001…077`; **next migration is 078**. Applied migrations are immutable: fix mistakes with a new forward migration (077 fixed a too-narrow column of 076). Every new migration needs the rollback file, `SHA256SUMS` line, `REQUIRED_SCHEMA_LEVEL` (`services/schema_level.py`), `tools/schema_rehearsal/chain.py` `LAST`, and the chain-length assertions in many `tests/*schema*.py` (a bump script pattern is easy: search for `range(1, 78)` / `(77, 77)`).
- Never touch `golddb` (the old program's data, read only). Never apply anything to `masgold_prod` yourself; give the owner the command: `python -m tools.migrate_production check|apply` (it backs up first).
- Daily backups to `E:\masGold_backups` (Windows tasks 01:00 DEV, 01:10 prod).

## 4. What was built in the last sessions (all committed)
- Production database + old-program data import (customers, master data, balances, stock, legacy invoices), manager lock (password screen in front of مراجعات المدير, reports, inventory, opening balance), auto sign-in choice, two-stage opening balance.
- Stocktake write-off (073) and **restoring a piece that a stocktake removed** (`StocktakeAdjustService.restore_piece`, button on the inventory search card).
- **Cash advances / custody (074)**: employee advance, customer temporary custody (separate from the customer balance), partial repayments; treasury dialog «سلف وعهد مؤقتة»; customer account shows the open custody apart from the balance. `tools/attach_advance.py` linked the old F0000001 (75 EGP) to employee «شلاطة» in prod.
- **Borrowed cash (076/077)**: the shop borrows from an employee / customer / outside person, no interest; direction `BORROW` in `cash_advances`.
- **Scrap adjustment by the manager (075)**: `OLD_GOLD_ADJUST` granted to MANAGER; dialog in مراجعات المدير; decrease never above available Scrap.
- Inventory: every piece entering stock gets a barcode (typed or automatic NNYYMMDD), `tools/assign_missing_barcodes.py` fixed 5 old pieces in prod; piece correction (type, karat, weight) and **convert a spoiled piece to Scrap** (`InventoryService.correct_item` / `convert_item_to_scrap`).
- Old-gold discount: 1.00–5.00 % on an old-gold-only invoice, 1.25–5.00 % as soon as the invoice sells something (default 1.50).
- UI: the coloured side strips on cards were removed program-wide (one uniform border).
- Reports (many) and the market-custody stages were finished earlier; see `CLAUDE.md`.

## 5. How to verify (focused runs that were green)
`tests.test_cash_advances`, `test_cash_advances_schema`, `test_borrowed_cash_schema`, `test_old_gold_adjustment`, `test_old_gold_adjust_manager_schema`, `test_inventory_stock`, `test_inventory_stock_qml`, `test_financial_accounts(_qml)`, `test_cash_screen`, `test_customer_account(_qml)`, `test_stocktake_adjust`, `test_invoice_workflow_service`, `test_invoice_viewmodel`, `test_schema_chain`, `test_schema_level`, `test_stabilization` (it checks the docs mention the current schema level and next migration: keep `CLAUDE.md` / `AGENTS.md` in step). UI tests run offscreen and take minutes: run them as modules, not all at once.
- Real-MySQL checks use the opt-in `tests/*_mysql.py` files and ad-hoc scripts on `masgold_rehearsal` inside a transaction that is rolled back (that is how the 077 column-width bug was found: fakes do not catch SQL / schema mistakes, so always try new SQL once on rehearsal).

## 6. Pitfalls learned (save yourself time)
- QML: a function inside a Dialog that assigns `kind = …` can hit another item's **id** `kind` (use distinctive property names). `theme.fontSmall` does not exist (`fontCaption`, `fontTableHeader`, `fontBody`). A `ComboBox` resets `currentIndex` to 0 when its model arrives.
- `assert_inside_window` fails for an item below the fold of a scrolling page; the real click helper scrolls, so assert visibility instead.
- Fake repositories in tests must be extended with every new repository method (several fakes: `tests/test_financial_accounts.py` FinRepo, `tests/test_inventory_stock.py` Repo, `tests/test_stocktake_adjust.py`).
- Migrations: SQL files are checksummed; do not normalise line endings; do not edit an applied file.
- `inventory_repository` has one allowed spelling of the stock predicate (`CURRENT_STOCK`): a test fails if you retype `inventory_status = 'IN_STOCK'` there.
- `MYSQL` collation `utf8mb4_unicode_ci`; Decimal half-up rounding to match MySQL `ROUND`.

## 7. Open items / not done
- The owner has not yet pushed the 14 local commits or opened the GitHub PR.
- Owner still has to: do the physical stocktake, add bank / wallet accounts (بنك، فودافون كاش) before opening-balance stage 1, enter the opening stages, review the remaining legacy invoices (`E:\masGold_review\invoices_to_review.csv`).
- Throwaway DBs `masgold_import_t1`, `masgold_import_t2` can be dropped (ask first).
- Known limitations: position snapshot reads in two transactions; the old `Authentication` shutdown warnings (`FirstRunOwnerSetup.qml` / `Login.qml`); the BOOK market-trip mode stays inactive and hidden.
- Ideas the owner may raise next: a full regression run, packaging / installer for the shop PC, more reports.
