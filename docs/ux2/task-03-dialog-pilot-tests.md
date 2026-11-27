# Task 03 — Dialog Pilot Characterization Tests

Target: `reverseSettlementDialog` (a `Popup`, `qml/SuppliersPage.qml:3188`). Test-only task. Written 2026-11-26.

## 1. Files changed

| File | Change |
|---|---|
| `tests/test_supplier_gold_settlement_ui.py` | +172 / −1 lines: import `Q_ARG`; new `RecordingReverseStub`; 8 new test methods plus helpers inside `GoldSettlementPageTests` |
| `docs/ux2/task-03-dialog-pilot-tests.md` | this report (new) |

No QML, Python application code, service, ViewModel, repository, database, permission or migration file was touched. `git status` afterwards lists only the three changes that existed before the task (`docs/feature-history.md`, `qml/DashboardPage.qml`, `tests/test_dashboard.py`), the untracked folders that existed before, `docs/ux/`, `docs/ux2/` and the one test file above. No MySQL connection was made (0 `Live database forbidden` log lines in the three modules). No dependency was added.

## 2. Existing coverage reused (inspected, not duplicated, not edited)

| Module | What it already covers for the dialog |
|---|---|
| `test_holding_settlement_ui` (`test_the_holder_page_reverses_through_the_one_canonical_correction_…`) | Real click on `holdingSettlementReverseButton` → dialog opens → note text → type reason → click `confirmReverseSettlementButton` → real service posts the correction → dialog closes. Mode: «تأكيد التصحيح» (holding). Mouse only. |
| `test_party_page_layout_ui` (`test_the_correction_dialog_states_the_exact_balances_and_is_inside_the_window`) | «تأكيد التصحيح» note text and that the reason field and confirm button are inside the window at 3 sizes. Closes by `close()`. |
| `test_supplier_gold_settlement_ui` (`test_the_reversal_dialog_fits_at_every_target_size`) | Opens via `open` (not `openFor`) at 3 sizes; geometry only. |
| Not covered before | The supplier-ledger opener `reverseSettlementButton` (`SuppliersPage.qml:998`) is clicked by no test; no existing test covers keyboard focus, Escape, Enter or the busy state of this dialog. |

New tests reuse the existing harness (`InvoicePageTests` → `GoldSettlementPageTests`): real `Main.qml`, blocked database, `open_supplier()`, `type_in()`, `click()`, `wait_until()`.

## 3. Tests added (all in `GoldSettlementPageTests`)

Each runs both modes through `subTest`: «عكس التسوية» (`fromHolding=false`) and «تأكيد التصحيح» (`fromHolding=true`). Window 1280×800.

| Test | Covers | Result today |
|---|---|---|
| `test_reversal_dialog_modes_are_told_apart_by_their_button_text` | The two modes are distinguishable: confirm-button text and the note text («رصيد المسوقجي» only in the holding mode). | pass |
| `test_reversal_dialog_reason_field_stays_editable` | `reverseSettlementReason` visible, enabled, not `readOnly`, empty on open; a real click then real key events put text in it. | pass |
| `test_reversal_dialog_takes_focus_into_the_reason_field_on_open` | After `openFor()`, `activeFocusItem` is `reverseSettlementReason`, its `activeFocus` is true and `invoiceBarcodeField.activeFocus` is false. | **fail (expected)** |
| `test_reversal_dialog_closes_on_an_immediate_escape_without_submitting` | Open, press Escape with no click first → dialog closes; no service call; the page button `settleGoldButton` can be clicked afterwards. | **fail (expected)** |
| `test_reversal_button_click_submits_the_id_and_reason_once` | Existing behaviour: click on `confirmReverseSettlementButton` reaches the service once with `("7", reason)`; dialog closes. Proves the fixture observes id and reason. | pass |
| `test_reversal_dialog_submits_once_on_enter_with_a_valid_reason` | Click into the reason field, type, press Return → exactly one call `("7", "خطأ في تسجيل المورد")`, dialog closes. | **fail (expected)** |
| `test_reversal_dialog_does_not_submit_an_empty_or_blank_reason_on_enter` | Reason `""` and `"   "`, Return → no call, dialog stays open. | pass (guard) |
| `test_reversal_dialog_does_not_submit_on_enter_while_a_reversal_is_running` | `svm._busy` contains `"setrev"` (the view model's own marker, the same technique as `tests/test_market_trip_phase0.py`), `settlementReversing` is true, Return → no call, dialog stays open. | pass (guard) |

Helpers: `reversal_setup`, `open_reversal` (calls the dialog's own `openFor()` with the same map the two real openers pass), `close_reversal`, `focus_name`, `settle_calls` (waits 350 ms so a duplicate worker call would be seen), `drop_reversal_dialog`; stub `RecordingReverseStub.reverse(settlement_id, reason, _expected_actor=None)`.

## 4. Commands and results (one process per module, offscreen, project `.venv`)

```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -B -m unittest tests.<module>
```

| Module | Executed | Passed | Failed | Errors | Skipped |
|---|---|---|---|---|---|
| `test_supplier_gold_settlement_ui` | 16 (8 existing + 8 new) | 13 | 3 | 0 | 0 |
| `test_holding_settlement_ui` | 10 | 10 | 0 | 0 | 0 |
| `test_party_page_layout_ui` | 17 | 17 | 0 | 0 | 0 |
| **Total** | **43** | **40** | **3** | **0** | **0** |

The 3 failed tests contain 6 failed sub-tests (3 tests × 2 modes). All 8 pre-existing tests of the first module and all tests of the other two modules stay green. The real-MySQL opt-in modules were not run.

First run: 8 errors, all `AttributeError: 'NoneType' object has no attribute 'findChild'` from my own `addCleanup` helper running after the harness had released the window. That was a defect in the new test code, not application behaviour; fixed with a guard, then the figures above came from the second full run.

## 5. Expected failures (current missing behaviour)

| Test | Failure text | Meaning |
|---|---|---|
| focus on open (both modes) | `'invoiceBarcodeField' != 'reverseSettlementReason'` | The popup takes no focus; the page's barcode field keeps it. |
| immediate Escape (both modes) | `the dialog did not close on Escape` | Escape does nothing until focus is inside the popup. |
| Enter with a valid reason (both modes) | `Enter did not submit the reversal` | `reverseSettlementReason` has no `onAccepted` handler. |

No other new assertion fails. The Enter test clicks the field first, so it fails only for the missing Enter handler, not for the missing focus.

## 6. Harness limitations

- **Passing guards are not vacuous-proof today.** The empty/blank-reason and busy-state Enter tests pass now because Enter does nothing at all. They become meaningful only once Enter submits. I could not prove the "green after the change" path of any of the five behaviour tests without editing QML, which this task forbids.
- **Openers.** The new tests open the dialog through `openFor()` with the map the openers pass; they do not click `reverseSettlementButton` or `holdingSettlementReverseButton`. The holding opener is clicked by the existing holding test; the supplier-ledger opener is clicked by no test (a full ledger row would be needed).
- **Boundary.** "Reaches the view-model slot unchanged" is verified one step behind the slot: the service stub receives exactly what `SupplierViewModel.reverseSettlement` passes (the slot cannot be replaced from Python once QML holds it). The real view model, worker thread and `_on_setrev` run unchanged.
- **Enter key.** Only `Qt.Key_Return` is exercised, not the keypad `Qt.Key_Enter`.
- **Busy state.** Simulated by the view model's `setrev` marker; it is not a live worker in flight.
- **Window size.** The behaviour tests run at 1280×800 only; the size-dependent fit is covered by the existing tests.
- **Focus identification** works reliably (`root.activeFocusItem()` returned `invoiceBarcodeField` and, in the editable test, accepted typed text), so no stop condition was reached.

## 7. Stop conditions

None triggered: focus is identifiable, the stub observes the settlement id and reason, the two modes are distinguishable, and no test needed the database or production-code changes.
