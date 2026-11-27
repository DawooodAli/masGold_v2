# Task 13 — Customer History Metadata Typography Pilot

Date: 2026-11-27. Result: **done; one confirmed 10 → 11 px change; all four modules green; no stop condition reached.** Evidence is harness-based (offscreen Qt), not a real application window.

## 1. Starting state
Branch `feature/purchasing-suppliers-v1`, HEAD `393f818`. `git status --porcelain=v1` is identical before and after (same 11 modified files — `qml/Main.qml` was already modified by Task 06 / Task 12 — and the same untracked folders). A copy of the pre-edit `Main.qml` was kept and the final diff against it was checked.

## 2. Pre-edit inspection (target confirmed)
`Main.qml:3688-3695`, inside `component CustomersPage` → profile card → `ListView#customerHistory` (visible on history tabs 1 and 2) → delegate `CustomerCard` → `ColumnLayout#customerHistoryRow` → `CustomerLabel` (no objectName):
```
value: customersPage.historyTab === 2
       ? (modelData.reference || "") + "  |  " + (modelData.date_text || "—")
       : "رقم السجل: " + modelData.id + "  |  " + (modelData.occurred_at || modelData.created_at || "—")
color: theme.textMuted
font.pixelSize: 10
```
It displays reference/date metadata («رقم السجل: N | التاريخ» on the warnings tab, «المرجع | التاريخ» on the Dead Cash tab), muted colour. It is **not** the balance-before/after-write-off value (`:3716-3722`, `textSecondary`, tab 2 only) nor a caption. Layout constraints: `Layout.fillWidth: true`; `CustomerLabel` base wraps (`Text.Wrap`), **no elision**; the delegate is `height: customerHistoryRow.implicitHeight + 20` (self-sizing); the list has a fixed `Layout.preferredHeight` (245 on tabs 1/2) and scrolls.

## 3. Production diff (`qml/Main.qml`, one line)
```diff
@@ -3694 +3694 @@
-                                                font.pixelSize: 10
+                                                font.pixelSize: 11
```
Literal `11` follows the local convention (the customer block uses literal sizes; `Main.qml` has no `fontTableHeader` use). Verified with `diff` against the pre-edit copy: exactly 1 line changed. No test file changed (no existing test asserts this size; a regression test was not requested).

**Balance-before/after element (`Main.qml:3721`) not changed:** still `font.pixelSize: 10` (re-read after the edit); the measurement below also shows its rendered size still 10 px. The neighbouring `:3701` «المرجع» (10 px) and all other history lines are untouched.

## 4. Geometry (real `Main.qml`, Cairo loaded, one selected customer; tab 1 = 2 warning rows, tab 2 = 2 Dead Cash rows with a long reference)
Label width, line count and truncation did not change; the label is one line at all three sizes.

| Viewport | Label (tab 1 / tab 2) | Row height tab 1 | Row height tab 2 | History list rect |
|---|---|---|---|---|
| 1440×900 | 10 → **11 px**, 617 × (19 → 20), 1 line | 91 → 92 | 113 → 114, 135 → 136 | unchanged (649 × 245) |
| 1280×800 | 10 → **11 px**, 524 × (19 → 20), 1 line | 91 → 92 | 113 → 114, 135 → 136 | unchanged (556 × 245) |
| 1080×680 | 10 → **11 px**, 757 × (19 → 20), 1 line | 91 → 92 | 113 → 114, 135 → 136 | unchanged (789 × 245) |

- The label stayed inside its row in all 12 measured cases (2 tabs × 2 rows × 3 sizes); `truncated` stayed false; no wrapping occurred (even the long Dead Cash reference stays on one line in the narrowest 524 px label).
- Each row grows by **exactly 1 px** (the label's line height 19 → 20); the list host keeps its fixed 245 px height and scrolls, so nothing outside the list moved (list rectangle identical before and after).
- The balance-before/after line measured 10 px, 19 px high, before and after.
- At 1080×680 the history list sits below the visible part of the profile scroll area (its scene y is 976, a pre-existing property of the page, reached by scrolling); the check there is against the list and row rectangles, not the window.
- Elision: none exists for this label and none was introduced.

## 5. Screenshots (harness output, not real-window verification)
`t13-before-*` and `t13-after-*` renders for tabs 1 and 2 at the three sizes are in the session scratchpad. The 1440×900 tab-2 "after" render was inspected: both Dead Cash cards show the reference/date line a pixel larger, the balance-before/after line below it still small, nothing clipped. The red banner in these renders is the harness blocking the live database.

## 6. Tests (one process each, offscreen, project `.venv`, no MySQL)
```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -B -m unittest tests.<module>
```
| Module | Ran | Result |
|---|---|---|
| `test_customer_account_qml` | 5 | OK |
| `test_customer_legacy_balance_ui` | 45 | OK |
| `test_customer_deep_link` | 9 | OK |
| `test_dead_cash_qml` | 2 | OK |

The "Live database forbidden" console lines are the harness's intended block.

## 7. Limitations
- Cairo numbers come from a separate read-only measurement script (Cairo loaded before the page is created); the four modules run with the fallback font and do not assert this size.
- Sample data only (2 warnings, 2 Dead Cash entries); real customer history and a real window were not used. Tab 0 (account statement) uses a different component and is not affected.
- The balance-before/after size decision (11 vs 12) remains undecided and untouched.

## 8. Scope confirmation
Production change: `qml/Main.qml`, one line. No other QML, Python, view model, service, repository, database or migration change. All earlier uncommitted work (Task 06 Flow, Task 12 customer list labels, etc.) preserved. No commit, stash, revert or cleanup. Task 14 not started.
