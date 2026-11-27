# Task 11 — Typography Targets Revalidation (read-only)

Date: 2026-11-27. **No file other than this report was created or changed.** Method: for every candidate the declaration was located, its enclosing element was read in full, and the displayed text, role, objectName and effective size were taken from that element — never from a nearby name. For Suppliers a small read-only script matched each declaration to its enclosing element by brace matching (so it cannot attribute a size to a neighbour). Cairo line heights/widths were measured with `QFontMetricsF` (read-only), not with a running page.

## 1. Repository state
| | Value |
|---|---|
| Branch / HEAD | `feature/purchasing-suppliers-v1` / `393f818` |
| `git status --porcelain=v1` before | ` M docs/feature-history.md`, ` M qml/CustomerPicker.qml`, ` M qml/DashboardPage.qml`, ` M qml/Main.qml`, ` M qml/SuppliersPage.qml`, ` M tests/test_customer_picker.py`, ` M tests/test_dashboard.py`, ` M tests/test_inventory_stock_qml.py`, ` M tests/test_return_history_ux.py`, ` M tests/test_supplier_gold_settlement_ui.py`, ` M tests/test_supplier_ui.py`; untracked `.claude/skills/*`, `docs/ux/`, `docs/ux2/` |
| After | identical (compared line by line) |

Task 07 line numbers still match the current files for every candidate below (InvoicePage, Return/Exchange, FinancialAccounts, InvoiceHistory, Stocktake, `Main.qml`).

## 2. Cairo reference numbers (measured, not assumed)
Line height by pixel size, regular = bold: **10 → 19, 11 → 20, 12 → 23, 13 → 24** px. Text widths grow ≈ 3–5 % from 10 → 11 and ≈ 14 % from 11 → 12 (hinted metrics).

## 3. Evidence table (one row per component)
`Txt` bases: `InvoicePage` uses plain `Label` with explicit sizes; `Return/Exchange` `Txt` = `fontBody` 12, **NoWrap + ElideLeft**; `FinancialAccounts` / `InvoiceHistory` / `Stocktake` `Txt` = `fontBody` 12; `Main.qml` `CustomerLabel` = 12, wraps (`Text.Wrap`). Every "Current size" below is the declaration on the element itself (no inherited surprises: each of these elements declares its own pixel size).

### P1
| # | Task 07 ref | File:line | Component path | objectName | Displayed text | Role | Now | Proposed | Validity | Tests that render the host | Risk |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | P1 #5 | `InvoicePage.qml:617` | `invoiceHeaderStrip` → `RowLayout` → `ColumnLayout` → `Label` | none | customer **code** (muted) or «يوجد عميل بنفس الرقم: …» (statusWarning) | metadata, or warning when same-mobile | 10 | 11 | **Valid** (one element, two roles) | `test_invoice_page_qml` (host page; none names the label) | the strip is 44 px: account line 13 bold (24) + this line 19 = 43 → 44 at 11 px: **0 px spare** |
| 2 | P1 #4 | `InvoicePage.qml:653` | `Rectangle#deadCashBanner` (22 px) → `Label#deadCashText` | `deadCashBanner` (banner), label has id only | «⚠️ تنبيه: سبق شطب رصيد لهذا العميل.» | warning, bold | 10 | 11 | **Valid** (12 would be wrong: 23 px line > 22 px badge) | `test_dead_cash_qml`, `test_invoice_page_qml` (neither names the banner) | line 19 → 20 inside a 22 px badge with a 1 px border (inner 20): **0 px spare**; width 166 → 174 px (the badge sizes itself) |
| 3 | P1 #6 | `InvoicePage.qml:2730` | `ListView#customerSuggestionList` → delegate `Rectangle#customerSuggestion_N` (40 px) → `ColumnLayout` → `Label` | none (delegate named) | «موبايل • منطقة • كود • ⚠️ سبق شطب رصيد» | metadata / warning (colour by state) | 10 | 11 | **Valid** | `test_invoice_page_qml` (`customerSuggestion_`) | delegate is 40 px but name line 13 bold (24) + this line (19) = 43 **already exceeds 40**; at 11 → 44. Existing tightness must be measured before any change |
| 4 | P1 #7 | `Main.qml:3694` | `customersPage` → `ListView#customerHistory` → delegate `CustomerCard` → `ColumnLayout` → `CustomerLabel` | none | «رقم السجل: … \| التاريخ» (tabs 0/1) or «المرجع \| التاريخ» (tab 2) | metadata (muted) | 10 | 11 | **Valid** | `test_customer_account_qml`, `test_dead_cash_qml`, `test_customer_deep_link`, `test_customer_legacy_balance_ui` (page; no history row named) | delegate height = content + 20, label wraps: low |
| 5 | P1 #7 | `Main.qml:3721` | same delegate, tab 2 only | none | «الرصيد قبل الشطب: … \| بعده: …» | **balance value** (textSecondary) | 10 | 11 (Task 07 P1) | **Valid, but role conflict**: it states a balance, and Task 07 §4 role A says balance lines need 12 | same as #4 | wraps; 12 px width ≈ 223 px for a typical line |
| 6 | P1 #8 | `ReturnWorkspace.qml:353-354` | `returnPage` → summary `ColumnLayout` → `Txt` | **none** (`priceWarning` is the view-model key, not an objectName) | `returnPage.s.priceWarning` (statusWarning) | warning | 10 (`fontCaption`) | 11 | **Valid** (one element; declaration on its second line) | `test_return_v2_ui` (renders the summary; does not assert this line) | `Txt` is NoWrap + ElideLeft: a longer warning elides earlier, it does not wrap |
| 7 | P1 #8 | `ExchangeWorkspace.qml:622-623` | `exchangePage` → summary `ColumnLayout` → `Txt` | `exchangeReview` | `exchangePage.s.reviewText` (amber) | warning / review | 10 (`fontCaption`) | 11 | **Valid** | `test_exchange_review_fixes` (exchange workspace; does not name `exchangeReview`) | NoWrap + ElideLeft, as #6 (sample sentence 270 → 283 px) |

### P2
| # | Task 07 ref | File:line | Component path | objectName | Displayed text | Role | Now | Proposed | Validity | Tests | Risk |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | P2 #2 | `Main.qml:3290` | `customerList` → delegate `CustomerCard` → row → `CustomerLabel` | none | `status_label` («نشط» …) | metadata (status) | 10 | 11 | **Valid** | customers-page modules above | row height = content + 12: grows by 1 px |
| 9 | P2 #2 | `Main.qml:3311` | same delegate, second row | none | «آخر حركة: …» | metadata | 10 | 11 | **Valid** | same | `Layout.maximumWidth: 150`, NoWrap, **ElideLeft**: sample 124 → 130 px (long ones already elide) |
| 10 | P2 #2 | `Main.qml:3355` | customers list footer row → `CustomerLabel` | none | «صفحة N · حتى 50 عميل» | pagination metadata | 10 | 11 | **Valid** | same | wraps; low |
| 11a | P2 #3 | `FinancialAccountsPage.qml:119` (continues on 120) | `CashTable` → header band `Rectangle` (28 px) → `RowLayout` → `Txt` | none | «الحركة» (130 px column) | table header | 11 | 12 | **Valid** | `test_financial_accounts_qml` (page; headers not named) | line 20 → 23 in a 28 px band: 5 px spare; word is 30 px in 130 |
| 11b | P2 #3 | `:121` | same | none | «المرجع» (fill) | table header | 11 | 12 | **Valid** | same | fill width, low |
| 11c | P2 #3 | `:122` | same | none | «المبلغ» (110 px column) | table header | 11 | 12 | **Valid** | same | word is 27 px in 110 |
| 12 | P2 #4 | `InvoiceHistoryPage.qml:551` | `historyPage` → paging `RowLayout` → `Txt` | none | «جاري التحميل…» / «انقر مرتين أو Enter لفتح الفاتورة» | status / hint | 10 (`fontCaption`) | 11 | **Incorrect mapping**: Task 07 named `historyNext`; the declaration belongs to the hint text. `historyNext` (the button, `:547`) declares no pixel size. The hint itself is valid as a hint. | `test_invoice_history_qml` (`historyNext`, not the hint) | hint 135 → 137 px; low |
| 13 | P2 #4 | `InvoiceHistoryPage.qml:711` | `historyCustomerBand` → `ColumnLayout` → `Txt` | none | caption «الاسم» | **caption** | 10 (`fontCaption`) | 11 | **Incorrect mapping**: Task 07 named `historyCustomerName`; that element is a `CustomerNameLink` (`:712`) already **13 px** (`fontBodyStrong`, `CustomerNameLink.qml`). The 10 px belongs to the caption above it. | `test_customer_deep_link` (`historyCustomerName`) | do not resize a caption to match its value |
| 14 | P2 #5 | `ExchangeWorkspace.qml:314` | returned-line `Rectangle` → `ColumnLayout` → `Txt` | none | per-line `reason` (statusWarning, why not exchangeable) | warning / reason | 10 | 11 | **Valid** | `test_exchange_review_fixes` | ElideRight, NoWrap, `fillWidth` |
| 15 | P2 #5 | `ExchangeWorkspace.qml:325` | returned-items panel → `Txt` | none | «القطعة المرتجعة تدخل الكسر بوزنها الفعلي وتنتظر قرار المدير…» | explanatory note | 10 | 11 | **Valid** | same | ElideRight, NoWrap: a 11 px note elides earlier |
| 16 | P2 #5 | `ExchangeWorkspace.qml:545` | `exchangeReplacement_N` (34 px) → `RowLayout` → `Txt` | `exchangeReplacementSource_N` | «يدوي» / «من الكسر» | metadata (source tag, amber / karat colour) | 10 | 11 | **Valid** | none names it (`test_exchange_review_fixes` covers the workspace) | 20 px line in a 34 px row |
| 17 | P2 #5 | `ExchangeWorkspace.qml:738` | save-bar `RowLayout` → `Txt` | `exchangeCalculating` | «جاري الحساب…» | status | 10 | 11 | **Valid** | none names it | low |
| 18 | P2 #6 | `StocktakePage.qml:359` | batch-size row → `Txt` | none | «(0 = بدون دفعات)» | hint | 10 | 11 | **Valid** | `test_inventory_stock_qml` (`stocktakeBatchSize`, neighbour) | 71 → 77 px |
| 19 | P2 #6 | `StocktakePage.qml:400` | karat-totals `Flow` → chip `Rectangle` → `Txt#karatText` | none (id only) | «عيار 21 — المتبقي … / … جرام • تم الجرد … / … جرام» | live **count/weight status** (value) | 10 | 11 | **Valid** | `test_inventory_stock_qml`, `test_inventory_stock` (view model) | chip sizes itself (+10 px, +1 px high); a `Flow`, wraps to a new line |
| 20 | P2 #8 | `Main.qml:1960` | Inventory list → delegate row → classification `Rectangle` (84 × 24) → `Label` | none | «بضاعة قديمة» / «بضاعة جديدة» (bold) | status tag | 10 bold | 11 bold | **Valid** | `test_inventory_stock_qml` (list; tag text not asserted) | 20 px line in 24 px chip; width 62 → 63 of 84: fits |

### Not re-opened (rows already settled or closed by an earlier task)
| Task 07 item | Status |
|---|---|
| P1 #1–#3 and P2 #1 (`CustomerPicker.qml`: balance 12, error / warnings / id-status / phone-place 11) | **Already implemented** (Task 08) |
| P1 #9, #10 (`SuppliersPage.qml`) | **Partly implemented / corrected** (Task 09 Option A: 687, 988, 992, 1544, 1548 → 12); the rest were captions/values (see Task 09) or the 4 detail/state lines left at 11 |
| P1 #11 (`MarketTripsPage.qml:850`) | **Incorrect mapping** (label, value already 12), closed without change (Task 10) |

## 4. Suppliers P2 #7 — "~60 notes", gated "only after the P1 pilot proves safe"
Task 07 gave ranges, not elements. The matching script finds **77** remaining `font.pixelSize: theme.fontTableHeader` declarations in `SuppliersPage.qml` (more than the "~60" in Task 07) and classifies each by its own element (lines below are current):

| Role | Lines (element objectName where present) |
|---|---|
| Captions / labels / headers (**do not resize** to match values) | 315, 366, 384, 390, 679 `goldAccountBalanceLabel`, 1326, 1361, 1373, 1400, 1432, 1458, 1795, 1889, 1997, 2058, 4208 |
| List / detail row metadata and counts | 457, 592, 602, 614, 840, 881, 893, 904, 971, 1525, 1424, 1480 |
| Ledger detail / state text (Task 09 Option B, not chosen) | 983 `settlementDetail`, 985 `settlementState`, 1529 `makingPaymentDetail`, 1532 `makingPaymentState` |
| Amber warnings / reasons | 691 `goldNote`, 1068 `holdingReason`, 1148 `logoReason`, 1207 `receivedRestriction`, 1639 `roleRequiredHint`, 1769 `payInactiveNote`, 1895 `payCreditNote`, 2064, 3162 `reverseReceiptNote`, 3224 `reverseSettlementNote`, 3280, 3582, 4313 `pieceHint` |
| Muted / secondary explanatory notes | 1070 `holdingHistoryNote`, 1268 `holdingsDetailsNote`, 1271 `receiptDetailsNote`, 1276 `holdingRefs`, 1285 `logoRefs`, 1349 `marketerIsolationNote`, 1645, 2122, 2478, 2505, 2598 `goldSettleRule`, 2827 `holdingReturnRule`, 2914 `logoReturnRule`, 3012 `holdingSettleRule`, 3403, 4039, 4073 |
| History rows | 1292 `logoHistoryRow`, 1304 `holdingSettlementRow`, 1322 `receiptHistoryRow`, 1331 `holdingHistoryRow` |
| Formula / analytical / review lines in dialogs | 2678 `goldSettleFormula`, 2685 `goldSettleAnalytical`, 2838 `holdingReturnEventReview`, 3057 `holdingSettleFormula`, 3064 `holdingSettleAnalytical`, 3504 `marketerReference` |
| Other (chip / status / checkbox text) | 1851 `paySamePartyConfirm` (CheckBox label), 3954, 4130 (`✓ مختارة للإدخال`), 4142, 4377 |

Mapping is **valid** for the notes (each declaration belongs to its own element); **validity of the resize is not established**: these are mostly wrapping text in 500 px dialogs (+14 % width at 12 px), the pilot precondition ("after the P1 pilot proves safe") is met only for the five ledger / breakdown lines of Task 09, and there is no per-element test. Existing tests that name some of them: `test_holding_settlement_ui`, `test_party_page_layout_ui`, `test_supplier_gold_settlement_ui` (`reverseSettlementNote`, `holdingReason`, `receivedRestriction`), `test_holding_receipt_ui` (`reverseReceiptNote`), `test_supplier_ui` / `test_supplier_karat` / `test_supplier_roles` (`goldNote`). Classified **valid but unscheduled**; a size recommendation needs its own audit by dialog.

## 5. Summary by class
| Class | Items |
|---|---|
| **Valid** (mapping verified, resize still a candidate) | #1–#11c, #14–#20 and the Suppliers notes (grouped, unscheduled) |
| **Incorrect mapping** | #12 (`historyNext` is not the 10 px element; the hint is), #13 (`historyCustomerName` is 13 px; the 10 px is the «الاسم» caption), Task 07 P1 #11 (Task 10) |
| **Already implemented** | CustomerPicker P1 #1–#3 and P2 #1 (Task 08); Suppliers ledger/breakdown five lines (Task 09) |
| **Unresolved / needs owner clarification** | #5 (11 as in Task 07 P1, or 12 as in the role-A rule, for the balance-before/after line); #12 (resize the hint text? it is a different element from the one Task 07 named); #13 (resize a caption? not recommended — a label must not be resized only to match its value) |

Unlisted siblings noticed while reading (not candidates, not in Task 07, not recommended): `InvoicePage.qml:633` `manualWarningText` (10 bold, in the same 22 px badge family as #2), `ReturnWorkspace.qml:313` and `:324` (10 px, same class as #6), `Main.qml:3701` «المرجع» (10 px, textSecondary, history tab 0).

## 6. Corrected sequence (priority order, each step one component and one task)
1. **Customers page metadata** — `Main.qml` #8, #9, #10, #4 (status, «آخر حركة», «صفحة», history reference/date line): all rows self-size, no fixed heights; `Main.qml` is dirty with the Task 06 `Flow`, so the task must name its scope. #5 only after the owner picks 11 or 12.
2. **Return / Exchange warnings** — #6 and #7 (`fontCaption` → 11): both NoWrap + ElideLeft, so measure elision of a realistic long message first; the tests exist (`test_return_v2_ui`, `test_exchange_review_fixes`) but need a width/elision assertion.
3. **Financial Accounts header** — #11a–c 11 → 12: 5 px of slack, narrowest risk, but the headers have no objectName.
4. **Invoice page** — #2 (11 only, never 12), #1, #3: all tight (0 px spare in the 22 and 44 px containers, 43 > 40 already in the suggestion row); needs a measured container decision, hence last of the Invoice items and not before a Cairo-based test.
5. **Exchange secondary text, Stocktake, Inventory tag** — #14–#20: small, independent, low risk.
6. **Suppliers notes** — only with a separate audit per dialog; not scheduled.

No implementation was started; Task 12 is not proposed. Waiting for owner review.
