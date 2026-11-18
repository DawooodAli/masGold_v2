---
name: masgold-ui
description: Use when creating or changing any QML screen, dialog, card, table or button in masGold_v2 (qml/*.qml, Main.qml inline components) or the view model fields a screen reads. Gives the project's visual language (Theme.qml tokens, Arabic RTL rules, card / dialog patterns), a layout checklist for 1440x900 / 1280x800 / 1080x680, and how to take real-window screenshots to verify.
---

# masGold UI

Employee-facing UI is Arabic, RTL, dark with a gold accent. The authority is CLAUDE.md section 3; this skill is the working recipe. If this file and CLAUDE.md differ, CLAUDE.md wins.

## 0. Precedence over the Qt skills
`qt-qml` and `qt-ui-design` (copied unmodified from The Qt Company's agent-skills, see their SOURCE.txt) are general guidance. Use them for binding / delegate / Loader / performance pitfalls, contrast, focus and keyboard checks, and for auditing a screenshot. Where they conflict with this project, CLAUDE.md and this skill win:
- Never enable `LayoutMirroring` (qt-ui-design says to); RTL is done per control with right alignment and `layoutDirection: Qt.RightToLeft`.
- Body text is 12 px (`theme.fontBody`), not 16 px; use the theme scale, not a new modular scale. Do not add a type singleton.
- Visible strings are Arabic literals; do not wrap them in `qsTr()` and do not add translation files.
- Do not split or restructure `Main.qml` (qt-qml's "bootstrap file only" rule); no broad refactors while making a narrow change.
- Ignore the embedded / MCU / AI-feature sections of qt-ui-design.
- The Qt skills' "ask the seven intake questions" step is answered by CLAUDE.md: desktop Windows, Arabic RTL, 1440x900 / 1280x800 / 1080x680, dark/gold theme, mouse + barcode scanner + keyboard.

`ui-ux-pro-max` (MIT, see its SOURCE.txt) is a local search over UX guidelines, chart advice and product patterns for WEB and mobile stacks (there is no Qt / QML data). Use it only as a read-only idea source, mainly for the iPad reports app in `webapp/` and for chart / dashboard questions:
`.venv/Scripts/python.exe -I .claude/skills/ui-ux-pro-max/scripts/search.py "<2-5 terms>" --domain ux|chart|product --max-results 3` (add `--rtl` for Arabic). Never use `--design-system`, `--persist` or `--preview` here: they invent a palette / fonts and write files, which our locked theme forbids. Its web rules (16 px body, 44 px touch targets, no emoji, bottom navigation) do not apply to the desktop QML screens.

## 1. Before you draw anything
- Read the page you are changing and one neighbouring page, and copy their patterns. Do not invent a second look.
- Architecture is locked: QML -> ViewModel -> Service -> Repository. QML never holds SQL, totals, running balances, rounding or permissions. The view model hands QML ready display strings (`money()`, `known_money()`, `grams()` in `app/viewmodels/`), and BIGINT ids as decimal strings.
- Never let QML supply `user_id`, `employee_id`, actor or permission sets. Hiding a button is a convenience, the service re-checks.

## 2. Tokens (qml/Theme.qml, always `theme.<name>`, never a one-off hex)
- Surfaces: `bgPrimary` (page / dialog), `bgSecondary` (cards, = `panel`), `bgTertiary`, `panelHover`; borders `border`, `cardBorder`. Accent `gold`, `goldBright`, `goldDark` follow the chosen theme (gold / blue / green / violet), so never assume the accent is gold.
- Text: `textPrimary`, `textSecondary`, `textMuted`, `textDisabled`.
- Meaning colours (identical in every theme): SHOP_BUY `priceBuy` (green / teal), SHOP_SELL `priceSell` (gold / amber), red (`statusWarning`, `redBackground`) only for error / danger, `statusSuccess` green, `statusInfo` gold. Customer account: `accountReceivable` (على العميل, cool) and `accountPayable` (للعميل, warm). Karat colours only through `theme.balanceKaratColor(k)`.
- Radius: `radiusSmall` 7 (fields, buttons), `radiusMedium` 10, `radiusCard` 16. Thin 1 px borders. Do not fill whole cells with colour; colour the label, value, accent bar or indicator.
- Type scale (Cairo): `fontPageTitle` 18, `fontSection` 14, `fontNumber` 22 (the one key figure of a card), `fontBodyStrong` 13, `fontBody` 12, `fontTableHeader` 11, `fontCaption` 10. Set `font.family: theme.fontFamily` on every text-bearing control (Label, TextField, TextArea, ComboBox, Button, table cells).

## 3. RTL rules
- Do NOT enable `LayoutMirroring` globally. Sidebar stays on the physical right.
- Everything Arabic is right-aligned: `horizontalAlignment: Text.AlignRight` / `TextInput.AlignRight`, placeholders, ComboBox text and popups, dialogs, tables, buttons, warnings, errors.
- Form row: `[control / value]  <-  [Arabic label on the physical right]`. Use `RowLayout { layoutDirection: Qt.RightToLeft }` so the label is first (right) and the control follows to its left.
- Numbers, barcodes, invoice numbers, dates and phone numbers are LTR runs inside RTL text; use the existing `ltr()` helper from the view model rather than QML tricks. A value that already contains an Arabic unit (`4,000.00 ج`) reads number-then-unit and is NOT LTR-isolated.
- Do not stretch controls across the whole window; give fields a sensible max width.

## 4. Reuse the existing components (do not redefine)
- Page sections: `SettingsSectionCard` (title + `content`), `SettingsCard`, `CustomerCard`, `SummaryBox`, `ActionMessageBox` in `qml/Main.qml`. Dialogs: `SettingsDialog { dialogTitle: ...; objectName: ... }` (modal, scrolls, `width: Math.min(880, root.width - 60)`, Esc closes, has a close button). Labels / buttons / inputs: `GoldPriceLabel`, `GoldPriceButton`, `GoldPriceInput`, `CustomerField` / `CustomerCombo`; per-page `Txt`, `Field`, `Btn`, `Section`, `Figure` components exist inside their page files; open the page and reuse its own.
- A key-figure card: small muted caption on top, the value in `fontNumber` or `fontBodyStrong` bold, optional muted note line below (`Figure` in `InvoiceHistoryPage.qml`).
- Every button that tests or users must find has an `objectName`; every table row, button and field a test clicks needs one too (pattern: `historyRow_0`, `reviewsOpenSuppliers`).

## 5. Layout checklist (the 1080x680 rule)
The window must work at 1440x900, 1280x800 and 1080x680 and never scroll sideways.
- Rows of buttons that can grow (headers, action bars) use a wrapping `Flow`, not a `RowLayout` that can run off screen. Note `Flow` ignores `Layout.preferredHeight`; set `implicitHeight` on the buttons.
- Long dynamic text (server answers, notes, file paths, long customer names) goes in its own row with `wrapMode: Text.Wrap` and `Layout.fillWidth: true`, never inside the button row. A long answer line once pushed a button to x = -369.
- No `width` / `Layout.minimumWidth` larger than about 1000 px, no fixed heights that clip content at 680 px; use `Layout.fillHeight` plus a `ScrollView` / `Flickable` for long forms (`ScrollBar.horizontal.policy: ScrollBar.AlwaysOff`).
- Each action must be inside the window and not covered by another item at all three sizes. Elide only where a full value is shown elsewhere; never elide a name, amount or number that the employee needs.
- Empty, loading, error and "database needs update" (`SCHEMA_OUTDATED`) states have Arabic text and look finished.
- Employee-facing wording is Arabic, short and uses the shop's own terms (على العميل / للعميل, شراء المحل / بيع المحل, كسر, عيار).

## 5b. Charts (owner 2026-11-18, dataviz rules)
- A QML line chart is a `Canvas` drawn with `qml/ChartKit.js` (`import "ChartKit.js" as Chart`): `Chart.frame` (hairline grid, round tick values on the right, x labels), `Chart.wash` (~10% under ONE series), `Chart.line` (2px, gaps for null), `Chart.endDot` (r 4 with a 2px surface ring; never a dot on every point), `Chart.crosshair`, `Chart.indexAt`. Hover: wrap the Canvas in an `Item` with a `hover` index, a `MouseArea { hoverEnabled }` and a `ChartTip` (`qml/ChartTip.qml`: day on top, one row per series with its colour key, name and value). The text of the tooltip comes formatted from the view model (or `Chart.fmt` for chart-only numbers); the exact figures stay in tables.
- Colours: SHOP_BUY `theme.priceBuy`, SHOP_SELL `theme.priceSell`, an average is dashed and muted; karats use `theme.balanceKaratColor`; the surface for the dot ring is the card colour (`theme.panel` / `bgSecondary`). Text on a chart is a text token, never the series colour. One axis only, a legend (colour key + name) for two or more series, no legend for one.
- Bars: columns at most 24px wide, 4px rounded cap and a square base, the best bar labelled, a tooltip on every bar; a chart with no data says so in words.
- The iPad app draws the same way in `webapp/static/app.js` (`renderLine`, `niceStep`, `.charttip`).

## 6. Verify with the real window (never only by emitting signals)
1. Write or extend a real-click test next to the page (`tests/test_<page>_qml.py` or `test_<module>_ui.py`), built on `tests/test_invoice_page_qml.py` (`InvoicePageTests`): `self.root.setWidth/Height` for each size, `self.click("objectName")`, `self.visual_item(name)`, `self.assert_inside_window(item)`, `self.wait_until(...)`. Loop over `((1440, 900), (1280, 800), (1080, 680))`.
2. Run offscreen with the project interpreter:
   `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -B -m unittest tests.<module> 2>&1 | tail -40`
3. Screenshots of the actual window: set `MASGOLD_UI_REVIEW_DIR` to a folder in the scratchpad and call the test's `shot(name)` helper (`self.root.grabWindow().save(...)`); then Read the PNGs and look at them at all three sizes before saying the screen is done. Show the owner the 1080x680 one first, that is where things break.
4. Report what you actually looked at; a unit test passing does not prove the screen reads well.

## 7. Do not
- Add a global LayoutMirroring, a new font, a new palette, or hard-coded colours.
- Put a calculation (totals, rounding, balances, deadlines) in QML.
- Mass-migrate old pages to new tokens while making a narrow change.
- Start the real application against `masgold_prod`, or write sample financial data to test a screen; use the test harness (blocked database, fake login).
