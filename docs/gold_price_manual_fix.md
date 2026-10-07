# Gold Price V1: manual activation and dialog correction

## Reproduced cause and trace

The existing database activation path works. Inspection confirmed the enabled
`MANUAL` source, active 18/21/24 karats, the admin's active `APP_LOGIN` and
`SETTINGS_MANAGE` grants, the real table definitions/foreign keys, and no price
table triggers. No migration or replacement table is required.

The reproducible failure was the save action's placement: the preview button
only calculated derived values; the separate final activation button was below
the scroll viewport. In the 1080×680 empty-state reproduction after preview,
the save button occupied y=595–635 while the viewport ended at y=598. Only
about three pixels were visible. Existing tests invoked `clicked` directly via
`QMetaObject`, so they passed without proving an employee could reach the button.
The old workflow also had no separate confirmation dialog or success message.

The inspected path was:

1. QML passed BUY, SELL and reason into `previewManual()` correctly.
2. `preview_manual()` returned three Decimal-derived string prices without writes.
3. The final button existed and called `confirmManual()`, but was clipped in
   the small-window case. Preview itself intentionally did not call activation.
4. `confirmManual()` could silently return when the preview/permission checks
   failed, or when `_start()` rejected a busy request.
5. When reached, `GoldPriceWorker` correctly forwarded the initiating session
   actor to `activate_manual()`, which revalidated permission and preserved reason.
6. Repository insertion of quotes, activations, active pointers, audit records
   and MANUAL mode succeeded in a real MySQL diagnostic transaction that was
   deliberately rolled back. No source, authorization, Decimal, or SQL failure
   was reproduced for a valid request.
7. A separate reliability gap existed after commit: `activate_manual()` opened
   another connection through `snapshot()`. Failure of that read could report
   an already-saved activation as an error while leaving the UI unchanged.

These findings distinguish the reproduced UI defect from a hypothetical SQL
failure. The changes do not invent missing production prices or attribute an
unobserved database error to the employee's original attempt.

## File → class/component → method

| File | Class/component | Change |
| --- | --- | --- |
| `qml/Main.qml` | `goldPriceDialog`, `GoldPriceGrid` | bounded centered content, compact aligned RTL grids, single empty state, 260px maximum inputs, compact reason field, grouped footer |
| same file | `goldConfirmManualButton`, `goldManualConfirmation` | visible gold `اعتماد السعر اليدوي` outside scrolling content; separate confirm/cancel with the derived set and reason |
| `app/viewmodels/gold_price_viewmodel.py` | `GoldPriceViewModel.confirmManual()` | explicit Arabic rejection feedback and boolean dispatch result |
| same file | `previewManual()`, `_completed()`, `successMessage` | reason validation, committed data immediately replaces current prices/top bar, success feedback, preserve preview on failed save |
| `app/workers/gold_price_worker.py` | `GoldPriceWorker.run()`, `_report_failure()` | Arabic employee errors and diagnostic operation/type/code/frame locations without raw payloads or credentials |
| `services/gold_price_service.py` | `GoldPriceService.activate_manual()` | verify/read the new coherent active group inside the activation transaction, return its snapshot only after successful commit; no second connection after commit |
| same file | `_manual_value()`, `_manual_quotes()` | compare original Decimal BUY/SELL before four-place rounding can conceal a reversed spread |
| `tests/gold_price_database_fixture.py` | `PriceDatabase`, SQL adapter | isolated disk-backed database using the real repository and authorization queries |
| `tests/test_gold_price_persistence.py` | `GoldPricePersistenceTests` | durable commit, new-service restart reads, pointers/history/audit, permission revocation, invalid inputs, second-pointer and commit rollback |
| `tests/test_gold_price_qml.py` | panel/worker tests | real mouse clicks, explicit confirmation/cancellation, compact empty state, fixed action visibility, Arabic errors and database-backed immediate UI refresh |

`GoldPriceRepository.append_publication()/set_mode()`, `AppSession`, and
`AuthorizationService.require_permission()` were inspected and reused. Their
production implementations needed no modification for this correction.

## Layout rationale

The dialog is capped at 620px, with centered content capped at 548px; its height
fits its contents up to the available window height. Current and preview prices
share a three-column RTL grid, with consistently right-aligned decimal strings.
Manual labels occupy the right column, with fields at most 260px on the left.
The 54px reason field scrolls for longer text. The preview button is secondary;
the final gold button and success/error feedback remain in the fixed footer.
At smaller heights, the content scrolls without hiding the save action.

Cairo remains specified in application QML; no global LayoutMirroring was
added and the navigation sidebar remains physically on the right.

## Verification

- Full Gold Price suite: **79 passed**.
- Full project suite: **188 passed**.
- Real mouse-click/offscreen checks at **1440×900, 1280×800, 1080×680**.
- Python syntax/import checks and `git diff --check`: passed.
- QML lint executed on the actual complete `Main.qml`: no syntax errors; the
  updated dialog has only unqualified-access diagnostics for runtime context
  properties/outer component context. No remaining property or layout diagnostic
  in the updated dialog. The complete file still reports 1,063 warnings;
  the checked-in HEAD baseline already reports 992. This is not a claim of a
  warning-free full application. (The lint summary was a local review artifact under the git-ignored
  `reports/` folder; it is not versioned.)
- Real MySQL rollback-only verification passed before and after the fix:
  three quotes, three activations, three matching active pointers, MANUAL mode,
  authenticated actor, employee metadata, reason and the returned snapshot.
  Production quote/activation/active-price counts remain zero after rollback.
- Durable commit/restart and injected rollback tests use an isolated SQLite
  file with the real service/repository path and a small SQL dialect adapter.
  They do not change production data or production schema.

Commands:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_gold_price*.py' -q
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Lib\site-packages\PySide6\qmllint.exe --json - qml/Main.qml
```

The offscreen renderer did not enumerate Cairo. Visual test processes explicitly
load a system Arial fallback for Arabic glyphs and realistic text geometry;
the shipped application font declarations remain Cairo. Review screenshots were produced locally under the
git-ignored `reports/gold_price_dialog_review/` folder (not versioned): preview at 1440x900, 1280x800 and
1080x680, the active preview and the confirmation at 1080x680.


## Manual acceptance

1. Restart the app, sign in with an authorized account, and open the price panel.
2. Enter BUY `6125`, SELL `6225`, reason `مراجعة المالك`. Press
   `عرض الأسعار المشتقة`. No write occurs yet. Expected BUY/SELL:
   18K `5250.0000 / 5335.7143`; 21K `6125.0000 / 6225.0000`;
   24K `7000.0000 / 7114.2857`.
3. Press `اعتماد السعر اليدوي`. Cancel once and verify nothing changes. Press
   it again, review the confirmation, then press `تأكيد الاعتماد`.
4. Expect Arabic success feedback, mode/source MANUAL, the three prices in the
   current grid, and immediate top-bar update. Restart and verify the same set.
5. Refresh while MANUAL: AUTO must not replace the set. A new manual activation
   must append history and move all three pointers together.
6. Blank reason, nonpositive values, reversed BUY/SELL, expired session and
   missing management permission must reject activation with Arabic feedback.
7. Repeat the preview/confirmation at all three requested window sizes; the
   primary button must remain visible without scrolling to find it.

Billing persistence, Cash/Bank, and production schema were not changed.
No commit was created. Sample prices were not activated in the shop database.
