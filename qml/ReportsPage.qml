import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import MasGold.Reports 1.0

// التقارير الأساسية: المبيعات والمرتجعات، المخزون المتاح، حركة المخزون والذهب، العملاء، الموردون، الخزنة والبنوك.
// Read only. Every figure arrives formatted from ReportsViewModel (exact Decimal computed by ReportService over the WHOLE filtered result);
// QML renders, filters, pages and asks the main window to open the source document. It never sums or classifies anything.
Item {
    id: page
    objectName: "reportsPage"

    property string pageTitle: ""
    readonly property alias vm: reportsVM
    readonly property bool canView: reportsVM.canView
    signal invoiceRequested(string invoiceId)
    signal customerRequested(string customerId)
    signal tripRequested(string tripId)
    signal backRequested()

    Theme { id: theme }

    ReportsViewModel {
        id: reportsVM
        objectName: "reportsVM"
        session: typeof appSession !== "undefined" ? appSession : null
        onOpenRequested: function (kind, identifier, extra) {
            if (kind === "INVOICE") page.invoiceRequested(identifier)
            else if (kind === "CUSTOMER") page.customerRequested(identifier)
            else if (kind === "TRIP") page.tripRequested(identifier)
        }
    }

    onVisibleChanged: if (visible && reportsVM.canView) { if (reportsVM.report === "") reportsVM.select(reportsVM.reports[0].key); else reportsVM.refreshPeriod() }

    function ltr(text) {
        return text === undefined || text === null || text === "" ? "" : "⁦" + text + "⁩"
    }
    // the karat colors of the whole app (the top bar balances); 0 = no karat
    function karatColor(k) {
        return k === 18 ? theme.balanceKarat18 : k === 21 ? theme.balanceKarat21 : k === 22 ? theme.balanceKarat22 : k === 24 ? theme.balanceKarat24 : theme.gold
    }
    // the tone of an owed-amount card: what WE owe (payable) and what THEY owe (receivable), the same account colors as the rest of the app
    function toneColor(t) {
        return t === "payable" ? theme.accountPayable : t === "receivable" ? theme.accountReceivable : t === "deadcash" ? theme.amber : ""
    }
    // a card value that carries an Arabic unit ("6 قطعة", "69.400 جرام") keeps the Arabic reading order; a plain number stays LTR
    function cardValue(text) {
        return /[\u0600-\u06FF]/.test(String(text)) ? String(text) : ltr(text)
    }
    // the totals grouped for the grid rows (a card keeps its global order for its object name)
    readonly property var totalGroups: {
        var out = [], index = {}
        var totals = reportsVM.result.totals || []
        for (var i = 0; i < totals.length; ++i) {
            var g = totals[i].group || ""
            if (!(g in index)) { index[g] = out.length; out.push([]) }
            out[index[g]].push({ label: totals[i].label, value: totals[i].value, note: totals[i].note, karat: totals[i].karat || 0, tone: totals[i].tone || "", order: i })
        }
        return out
    }

    Rectangle { anchors.fill: parent; color: theme.bgPrimary }

    // ---------------------------------------------------------------- the sales line chart of one year (months or ISO weeks) with the year before for comparison
    Popup {
        id: demandChartDialog
        objectName: "reportDemandChartDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(900, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 500, 500)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        onClosed: reportsVM.closeDemandChart()
        Connections {
            target: reportsVM
            function onChanged() { if (!!reportsVM.demandChart.granularity && !demandChartDialog.opened) demandChartDialog.open(); demandCanvas.requestPaint() }
        }
        background: Rectangle { color: theme.bgSecondary; radius: theme.radiusCard; border.color: theme.gold; border.width: 2 }
        contentItem: ColumnLayout {
            spacing: 8
            Label { objectName: "reportDemandChartTitle"; Layout.fillWidth: true; font.bold: true; font.pixelSize: theme.fontSection; color: theme.gold; horizontalAlignment: Text.AlignRight
                    text: (reportsVM.demandChart.granularity === "W" ? "المبيعات الأسبوعية" : "المبيعات الشهرية") + " على مدار السنة (جرام، كل الأعيار)" }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 6
                Btn { objectName: "reportDemandMonths"; text: "شهري"; primary: reportsVM.demandChart.granularity === "M"; onClicked: reportsVM.showDemandChart("M", (reportsVM.demandChart.selected || []).join(",")) }
                Btn { objectName: "reportDemandWeeks"; text: "أسبوعي"; primary: reportsVM.demandChart.granularity === "W"; onClicked: reportsVM.showDemandChart("W", (reportsVM.demandChart.selected || []).join(",")) }
                Label { color: theme.textMuted; text: "قارن السنوات:" }
                Repeater {
                    model: reportsVM.demandChart.available || []
                    delegate: Btn {
                        required property var modelData
                        objectName: "reportDemandYear_" + modelData
                        text: modelData
                        primary: (reportsVM.demandChart.selected || []).indexOf(modelData) >= 0
                        onClicked: {
                            var now = (reportsVM.demandChart.selected || []).slice()
                            var at = now.indexOf(modelData)
                            if (at >= 0) { if (now.length > 1) now.splice(at, 1) } else now.push(modelData)
                            reportsVM.showDemandChart(reportsVM.demandChart.granularity, now.join(","))
                        }
                    }
                }
                Item { Layout.fillWidth: true }
                Repeater {
                    model: reportsVM.demandChart.lines || []
                    delegate: Label { required property var modelData; required property int index; objectName: "reportDemandLegend_" + modelData.year; text: "━ " + modelData.year; font.bold: true
                                      color: demandCanvas.palette[index % demandCanvas.palette.length] }
                }
            }
            Label { objectName: "reportDemandChartLoading"; Layout.fillWidth: true; visible: !!reportsVM.demandChart.loading; color: theme.textMuted; text: "جارٍ التحميل…"; horizontalAlignment: Text.AlignRight }
            Label { objectName: "reportDemandChartError"; Layout.fillWidth: true; visible: !!reportsVM.demandChart.error; color: theme.red; text: reportsVM.demandChart.error || ""; wrapMode: Text.WordWrap }
            Label { objectName: "reportDemandChartEmpty"; Layout.fillWidth: true; visible: !reportsVM.demandChart.loading && !reportsVM.demandChart.error && reportsVM.demandChart.hasData === false; color: theme.textMuted
                    text: "لا توجد بيانات مبيعات بأوزان معروفة لهذه السنة."; horizontalAlignment: Text.AlignRight }
            Canvas {
                id: demandCanvas
                objectName: "reportDemandChartCanvas"
                Layout.fillWidth: true
                Layout.fillHeight: true
                readonly property var palette: ["#DDB961", "#70B0F1", "#5EC3CE", "#A5B2C3", "#77C599", "#E7AA5B"]
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.reset()
                    var labels = reportsVM.demandChart.labels || []
                    if (labels.length < 2) return
                    var lines = reportsVM.demandChart.lines || []
                    var left = 52, right = 8, top = 8, bottom = 22
                    var w = width - left - right, h = height - top - bottom
                    var high = reportsVM.demandChart.high || 0
                    if (high <= 0) high = 1
                    function x(i) { return left + w * i / (labels.length - 1) }
                    function y(v) { return top + h * (1 - v / high) }
                    ctx.strokeStyle = "#303842"; ctx.fillStyle = "#858D97"; ctx.font = "11px sans-serif"; ctx.lineWidth = 1
                    for (var g = 0; g <= 4; ++g) {
                        var gv = high * g / 4
                        ctx.beginPath(); ctx.moveTo(left, y(gv)); ctx.lineTo(left + w, y(gv)); ctx.stroke()
                        ctx.fillText(Math.round(gv).toString(), 4, y(gv) + 4)
                    }
                    var step = labels.length > 20 ? 4 : 1
                    for (var i = 0; i < labels.length; i += step) ctx.fillText(labels[i], x(i) - 10, height - 6)
                    function line(values, color, dash) {
                        ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.setLineDash(dash)
                        ctx.beginPath()
                        var started = false
                        for (var k = 0; k < values.length; ++k) {
                            var v = values[k]
                            if (v === null || v === undefined) { started = false; continue }
                            if (!started) { ctx.moveTo(x(k), y(v)); started = true } else ctx.lineTo(x(k), y(v))
                        }
                        ctx.stroke()
                        ctx.setLineDash([])
                        ctx.fillStyle = color
                        for (var j = 0; j < values.length; ++j) if (values[j] !== null && values[j] !== undefined) { ctx.beginPath(); ctx.arc(x(j), y(values[j]), 2.5, 0, 6.2832); ctx.fill() }
                    }
                    for (var li = lines.length - 1; li >= 0; --li) line(lines[li].values, palette[li % palette.length], li === 0 ? [] : [6, 4])
                }
            }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                Btn { objectName: "reportDemandChartClose"; text: "إغلاق"; accent: theme.textSecondary; onClicked: demandChartDialog.close() }
                Item { Layout.fillWidth: true }
                Label { color: theme.textSecondary; text: "أعلى نقطة " + (reportsVM.demandChart.highText || "") + " جرام" }
            }
        }
    }

    // ---------------------------------------------------------------- the daily price chart of one karat (shop buy, shop sell and the 30-day average)
    Popup {
        id: priceChartDialog
        objectName: "reportPriceChartDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(820, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 480, 480)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        onClosed: reportsVM.closePriceChart()
        Connections {
            target: reportsVM
            function onChanged() { if (!!reportsVM.priceChart.karat && !priceChartDialog.opened) priceChartDialog.open(); priceCanvas.requestPaint() }
        }
        background: Rectangle { color: theme.bgSecondary; radius: theme.radiusCard; border.color: page.karatColor(parseInt(reportsVM.priceChart.karat || 0)); border.width: 2 }
        contentItem: ColumnLayout {
            spacing: 8
            Label { objectName: "reportPriceChartTitle"; Layout.fillWidth: true; text: "سعر الذهب اليومي — عيار " + (reportsVM.priceChart.karat || ""); color: page.karatColor(parseInt(reportsVM.priceChart.karat || 0)); font.bold: true
                    font.pixelSize: theme.fontSection; horizontalAlignment: Text.AlignRight }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 6
                Repeater {
                    model: [{ days: 30, text: "30 يوم" }, { days: 90, text: "90 يوم" }, { days: 365, text: "سنة" }, { days: 0, text: "الكل" }]
                    delegate: Btn {
                        required property var modelData
                        objectName: "reportPriceRange_" + modelData.days
                        text: modelData.text
                        primary: reportsVM.priceChart.days === modelData.days
                        onClicked: reportsVM.showPriceChart(reportsVM.priceChart.karat, modelData.days)
                    }
                }
                Item { Layout.fillWidth: true }
                Label { color: theme.textMuted; font.pixelSize: theme.fontTableHeader; text: "ذهبي = بيع المحل • أخضر مزرق = شراء المحل • رمادي متقطع = متوسط 30 يوم" }
            }
            Label { objectName: "reportPriceChartLoading"; Layout.fillWidth: true; visible: !!reportsVM.priceChart.loading; color: theme.textMuted; text: "جارٍ التحميل…"; horizontalAlignment: Text.AlignRight }
            Label { objectName: "reportPriceChartError"; Layout.fillWidth: true; visible: !!reportsVM.priceChart.error; color: theme.red; text: reportsVM.priceChart.error || ""; wrapMode: Text.WordWrap }
            Canvas {
                id: priceCanvas
                objectName: "reportPriceChartCanvas"
                Layout.fillWidth: true
                Layout.fillHeight: true
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.reset()
                    var points = reportsVM.priceChart.points || []
                    if (points.length < 2) return
                    var left = 52, right = 8, top = 8, bottom = 20
                    var w = width - left - right, h = height - top - bottom
                    var low = reportsVM.priceChart.low, high = reportsVM.priceChart.high
                    if (high <= low) high = low + 1
                    function x(i) { return left + w * i / (points.length - 1) }
                    function y(v) { return top + h * (1 - (v - low) / (high - low)) }
                    ctx.strokeStyle = "#303842"; ctx.fillStyle = "#858D97"; ctx.font = "11px sans-serif"; ctx.lineWidth = 1
                    for (var g = 0; g <= 4; ++g) {
                        var gv = low + (high - low) * g / 4
                        ctx.beginPath(); ctx.moveTo(left, y(gv)); ctx.lineTo(left + w, y(gv)); ctx.stroke()
                        ctx.fillText(Math.round(gv).toString(), 4, y(gv) + 4)
                    }
                    ctx.fillText(points[0].date, left, height - 4)
                    ctx.fillText(points[points.length - 1].date, left + w - 62, height - 4)
                    function line(key, color, dash) {
                        ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.setLineDash(dash)
                        ctx.beginPath()
                        var started = false
                        for (var i = 0; i < points.length; ++i) {
                            var v = points[i][key]
                            if (v === null || v === undefined) continue
                            if (!started) { ctx.moveTo(x(i), y(v)); started = true } else ctx.lineTo(x(i), y(v))
                        }
                        ctx.stroke()
                    }
                    line("sell", "#E2BC63", [])
                    line("buy", "#73CBB0", [])
                    line("average", "#858D97", [6, 4])
                }
            }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                Btn { objectName: "reportPriceChartClose"; text: "إغلاق"; accent: theme.textSecondary; onClicked: priceChartDialog.close() }
                Item { Layout.fillWidth: true }
                Label { color: theme.textSecondary; text: (reportsVM.priceChart.first || "") + " → " + (reportsVM.priceChart.last || "") + "  •  أدنى " + (reportsVM.priceChart.lowText || "") + " • أعلى " + (reportsVM.priceChart.highText || "") }
            }
        }
    }

    // ---------------------------------------------------------------- the monthly sales chart of one item type (follows the report's filters)
    Popup {
        id: chartDialog
        objectName: "reportChartDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(760, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 460, 460)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        onClosed: reportsVM.closeChart()
        Connections {
            target: reportsVM
            function onChanged() { if (!!reportsVM.chart.type && !chartDialog.opened) chartDialog.open() }
        }
        background: Rectangle { color: theme.bgSecondary; radius: theme.radiusCard; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 8
            Label { objectName: "reportChartTitle"; Layout.fillWidth: true; text: "المبيعات الشهرية — " + (reportsVM.chart.type || ""); color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection; horizontalAlignment: Text.AlignRight }
            Label { objectName: "reportChartApplied"; Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignRight
                    visible: !!reportsVM.chart.applied; text: (reportsVM.chart.applied || []).join(" • ") }
            Label { objectName: "reportChartLoading"; Layout.fillWidth: true; visible: !!reportsVM.chart.loading; color: theme.textMuted; text: "جارٍ التحميل…"; horizontalAlignment: Text.AlignRight }
            Label { objectName: "reportChartError"; Layout.fillWidth: true; visible: !!reportsVM.chart.error; color: theme.red; text: reportsVM.chart.error || ""; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignRight }
            Label { objectName: "reportChartTotal"; Layout.fillWidth: true; visible: !reportsVM.chart.loading && !reportsVM.chart.error && !!reportsVM.chart.type; color: theme.textSecondary
                    text: "الإجمالي: " + (reportsVM.chart.weight || "") + " • " + (reportsVM.chart.count || "0") + " قطعة"; horizontalAlignment: Text.AlignRight }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: Math.max(width, barRow.implicitWidth)
                clip: true
                ScrollBar.horizontal: ScrollBar {}
                Row {
                    id: barRow
                    objectName: "reportChartBars"
                    height: parent.height - 14
                    spacing: 10
                    layoutDirection: Qt.LeftToRight
                    Repeater {
                        model: reportsVM.chart.points || []
                        delegate: Item {
                            required property var modelData
                            objectName: "reportChartBar_" + modelData.month
                            width: 86
                            height: barRow.height
                            Column {
                                anchors.fill: parent
                                spacing: 3
                                Item { width: parent.width; height: parent.height - 78
                                    Rectangle {
                                        anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter
                                        width: 40; height: Math.max(modelData.ratio > 0 ? 3 : 0, parent.height * modelData.ratio)
                                        color: theme.gold; radius: 3
                                    }
                                }
                                Label { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: modelData.weight; color: theme.gold; font.pixelSize: theme.fontTableHeader; font.bold: true; wrapMode: Text.WordWrap }
                                Label { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: modelData.count + " قطعة"; color: theme.textSecondary; font.pixelSize: theme.fontTableHeader }
                                Label { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: modelData.month; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                            }
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                Btn { objectName: "reportChartClose"; text: "إغلاق"; accent: theme.textSecondary; onClicked: chartDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    component Txt: Label {
        color: theme.textPrimary
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        horizontalAlignment: Text.AlignRight
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.WordWrap
        textFormat: Text.PlainText
    }

    component Btn: Button {
        id: btn
        property bool primary: false
        property color accent: theme.gold
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        implicitHeight: 32
        topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0
        topPadding: 0; bottomPadding: 0; leftPadding: 14; rightPadding: 14
        hoverEnabled: true
        contentItem: Label {
            text: btn.text; font: btn.font
            color: !btn.enabled ? theme.textDisabled : btn.primary ? theme.bgPrimary : btn.accent
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: theme.radiusSmall
            color: !btn.enabled ? "transparent" : btn.primary ? (btn.down || btn.hovered ? Qt.lighter(btn.accent, 1.12) : btn.accent) : btn.down ? theme.panelHover : btn.hovered ? theme.bgTertiary : "transparent"
            border.color: btn.enabled ? btn.accent : theme.border
        }
    }

    component Field: TextField {
        id: field
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        color: theme.textPrimary
        placeholderTextColor: theme.textDisabled
        horizontalAlignment: TextInput.AlignRight
        selectByMouse: true
        implicitHeight: 32
        background: Rectangle { radius: 4; color: theme.bgPrimary; border.color: field.activeFocus ? theme.gold : theme.border }
    }

    component Combo: ComboBox {
        id: combo
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        implicitHeight: 32
        delegate: ItemDelegate {
            width: combo.width
            height: 32
            contentItem: Label { text: modelData[combo.textRole]; font.family: "Cairo"; font.pixelSize: theme.fontBody; color: theme.textPrimary
                                 horizontalAlignment: Text.AlignRight; verticalAlignment: Text.AlignVCenter; elide: Text.ElideLeft }
        }
        indicator: Label { x: 10; y: (combo.height - height) / 2; text: "▾"; color: theme.gold }
        contentItem: Label { text: combo.displayText; font: combo.font; color: theme.textPrimary; horizontalAlignment: Text.AlignRight
                             verticalAlignment: Text.AlignVCenter; rightPadding: 10; leftPadding: 28; elide: Text.ElideLeft }
        background: Rectangle { radius: 4; color: theme.bgPrimary; border.color: combo.activeFocus || combo.popup.visible ? theme.gold : theme.border }
    }

    Flickable {
        id: scroller
        objectName: "reportsScroller"
        anchors.fill: parent
        anchors.margins: 18
        contentWidth: width
        contentHeight: body.implicitHeight + 24
        clip: true
        ScrollBar.vertical: ScrollBar {}

        ColumnLayout {
            id: body
            width: scroller.width - 14
            spacing: 10

            // ---------------------------------------------------------------- header + report choice
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                Txt { text: "📊 التقارير"; color: theme.gold; font.bold: true; font.pixelSize: 20 }
                Item { Layout.fillWidth: true }
                Btn { objectName: "reportsBack"; text: "←  مراجعات المدير"; onClicked: page.backRequested() }
            }
            Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                  text: "للعرض فقط — الأرقام من دفاتر البرنامج وكل إجمالي يشمل كامل نتائج الفلتر وليس الصفحة المعروضة فقط." }
            // الأزرار في ثلاث مجموعات: الذهب • النقدية • العام
            Repeater {
                model: [{ "name": "الذهب", "keys": ["stock", "alerts", "indicators", "gold", "scrapcost", "demand", "profit", "market"] },
                        { "name": "النقدية", "keys": ["treasury", "cashflow", "makingprofit", "deadcash"] },
                        { "name": "عام", "keys": [] }]
                delegate: RowLayout {
                    id: groupRow
                    required property var modelData
                    required property int index
                    readonly property var members: reportsVM.reports.filter(function(r) {
                        if (groupRow.index < 2) return groupRow.modelData.keys.indexOf(r.key) >= 0
                        return groupRow.modelData.keys.length === 0 && ["stock", "alerts", "indicators", "gold", "scrapcost", "demand", "profit", "market", "treasury", "cashflow", "makingprofit", "deadcash"].indexOf(r.key) < 0
                    })
                    visible: members.length > 0
                    Layout.fillWidth: true
                    layoutDirection: Qt.RightToLeft
                    spacing: 8
                    Txt { Layout.preferredWidth: 64; Layout.alignment: Qt.AlignTop; text: groupRow.modelData.name; color: theme.gold; font.bold: true }
                    Flow {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        layoutDirection: Qt.RightToLeft
                        spacing: 6
                        Repeater {
                            model: groupRow.members
                            delegate: Btn {
                                required property var modelData
                                objectName: "reportChoice_" + modelData.key
                                text: modelData.title
                                primary: reportsVM.report === modelData.key
                                onClicked: reportsVM.select(modelData.key)
                            }
                        }
                    }
                }
            }
            Txt { objectName: "reportsNoAccess"; Layout.fillWidth: true; visible: !reportsVM.canView; color: theme.amber; text: "ليس لديك صلاحية عرض أي تقرير." }

            // ---------------------------------------------------------------- filters
            Rectangle {
                Layout.fillWidth: true
                visible: reportsVM.report !== ""
                implicitHeight: filterFlow.implicitHeight + 16
                radius: theme.radiusCard; color: theme.bgSecondary; border.color: theme.cardBorder
                Flow {
                    id: filterFlow
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 8
                    layoutDirection: Qt.RightToLeft
                    spacing: 8
                    Txt { text: "من"; height: 32; visible: reportsVM.periodUsed }
                    DateField { objectName: "reportFrom"; visible: reportsVM.periodUsed; value: reportsVM.filters.from || ""; onPicked: function (v) { reportsVM.setFilter("from", v) } }
                    Txt { text: "إلى"; height: 32; visible: reportsVM.periodUsed }
                    DateField { objectName: "reportTo"; visible: reportsVM.periodUsed; value: reportsVM.filters.to || ""; onPicked: function (v) { reportsVM.setFilter("to", v) } }
                    Field { objectName: "reportText"; width: 190; placeholderText: "بحث (رقم، اسم، باركود…)"; text: reportsVM.filters.text || ""
                            onTextEdited: reportsVM.setFilter("text", text); onAccepted: reportsVM.apply() }
                    Repeater {
                        model: reportsVM.filterSpecs
                        delegate: Row {
                            id: specRow
                            required property var modelData
                            layoutDirection: Qt.RightToLeft
                            spacing: 4
                            Txt { text: specRow.modelData.label; height: 32 }
                            Field {
                                objectName: "reportFilterText_" + specRow.modelData.key
                                visible: specRow.modelData.type === "text"
                                width: 110
                                text: reportsVM.filters[specRow.modelData.key] || ""
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                onTextEdited: reportsVM.setFilter(specRow.modelData.key, text)
                                onAccepted: reportsVM.apply()
                            }
                            Combo {
                                objectName: "reportFilter_" + specRow.modelData.key
                                visible: specRow.modelData.type !== "text"
                                width: 170
                                model: specRow.modelData.options
                                textRole: "text"
                                currentIndex: {
                                    var current = reportsVM.filters[specRow.modelData.key]
                                    for (var i = 0; i < specRow.modelData.options.length; ++i) if (specRow.modelData.options[i].value === current) return i
                                    return 0
                                }
                                onActivated: function (i) { reportsVM.setFilter(specRow.modelData.key, specRow.modelData.options[i].value) }
                            }
                        }
                    }
                    Btn { objectName: "reportApply"; text: "عرض"; primary: true; enabled: !reportsVM.busy; onClicked: reportsVM.apply() }
                    Btn { objectName: "reportExport"; text: "تصدير CSV"; enabled: !reportsVM.busy && !!reportsVM.result.tables; onClicked: reportsVM.exportCsv() }
                }
            }

            Txt { objectName: "reportApplied"; Layout.fillWidth: true; visible: !!reportsVM.result.applied && reportsVM.result.applied.length > 0; color: theme.textMuted
                  font.pixelSize: theme.fontTableHeader; text: "الفلاتر المطبقة: " + (reportsVM.result.applied || []).join(" • ") }

            // ---------------------------------------------------------------- states
            Txt { objectName: "reportLoading"; Layout.fillWidth: true; visible: reportsVM.busy; color: theme.textMuted; text: "جارٍ التحميل…" }
            Txt { objectName: "reportError"; Layout.fillWidth: true; visible: reportsVM.errorMessage !== ""; color: theme.red; text: reportsVM.errorMessage }
            Txt { objectName: "reportMessage"; Layout.fillWidth: true; visible: reportsVM.message !== ""; color: theme.green; text: reportsVM.message }

            // ---------------------------------------------------------------- meaning + totals
            // the totals: one uniform grid per group (the cards of a group share a row), as many columns as fit
            Repeater {
                model: page.totalGroups
                delegate: GridLayout {
                    id: totalsGrid
                    required property var modelData
                    Layout.fillWidth: true
                    layoutDirection: Qt.RightToLeft
                    columns: Math.max(1, Math.floor((body.width + 8) / 280))
                    columnSpacing: 8
                    rowSpacing: 8
                    Repeater {
                        model: totalsGrid.modelData
                        delegate: Rectangle {
                            id: totalCard
                            required property var modelData
                            objectName: "reportTotal_" + modelData.order
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            Layout.fillHeight: true
                            implicitHeight: totalColumn.implicitHeight + 20
                            radius: theme.radiusCard; color: theme.panel
                            border.color: totalCard.modelData.karat ? page.karatColor(totalCard.modelData.karat) : totalCard.modelData.tone ? page.toneColor(totalCard.modelData.tone) : theme.border
                            border.width: totalCard.modelData.karat || totalCard.modelData.tone ? 2 : 1
                            ColumnLayout {
                                id: totalColumn
                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 8
                                spacing: 2
                                Txt { Layout.fillWidth: true; text: totalCard.modelData.label; color: theme.textSecondary; font.pixelSize: theme.fontBodyStrong; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                Txt { objectName: "reportTotalValue_" + totalCard.modelData.order; Layout.fillWidth: true; text: page.cardValue(totalCard.modelData.value)
                                      color: totalCard.modelData.karat ? page.karatColor(totalCard.modelData.karat) : totalCard.modelData.tone ? page.toneColor(totalCard.modelData.tone) : theme.gold; font.bold: true; font.pixelSize: 20 }
                                Txt { Layout.fillWidth: true; visible: totalCard.modelData.note !== ""; text: totalCard.modelData.note; color: theme.textMuted; font.pixelSize: theme.fontBody; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            }
                        }
                    }
                }
            }

            // ---------------------------------------------------------------- tables
            Repeater {
                model: reportsVM.result.tables || []
                delegate: ColumnLayout {
                    id: tableBlock
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    spacing: 3
                    Item { Layout.preferredHeight: tableBlock.index > 0 ? 10 : 0 }                                  // a gap that separates the tables
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Rectangle { visible: tableBlock.modelData.karat > 0; Layout.preferredWidth: 6; Layout.preferredHeight: 22; radius: 3; color: page.karatColor(tableBlock.modelData.karat) }
                        Txt { Layout.fillWidth: true; text: tableBlock.modelData.title + (tableBlock.modelData.paged ? "  (" + tableBlock.modelData.rowCount + ")" : "")
                              color: tableBlock.modelData.karat > 0 ? page.karatColor(tableBlock.modelData.karat) : theme.gold; font.bold: true; font.pixelSize: theme.fontSection }
                    }
                    Txt { objectName: "reportTableUnavailable_" + tableBlock.index; Layout.fillWidth: true; visible: !tableBlock.modelData.available; color: theme.amber
                          text: "غير متاح: " + tableBlock.modelData.reason }
                    Txt { objectName: "reportTableEmpty_" + tableBlock.index; Layout.fillWidth: true; visible: tableBlock.modelData.available && tableBlock.modelData.rows.length === 0
                          color: theme.textMuted; text: "لا توجد بيانات مطابقة." }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: tableBlock.modelData.available && tableBlock.modelData.rows.length > 0
                        layoutDirection: Qt.RightToLeft
                        spacing: 4
                        Repeater {
                            model: tableBlock.modelData.columns
                            delegate: Txt { required property var modelData; Layout.fillWidth: true; Layout.preferredWidth: 1; text: modelData.title; color: theme.textMuted
                                            font.pixelSize: theme.fontBodyStrong; font.bold: true; horizontalAlignment: modelData.kind === "text" ? Text.AlignRight : Text.AlignHCenter }
                        }
                        Item { Layout.preferredWidth: 78 }
                    }
                    Repeater {
                        model: tableBlock.modelData.rows
                        delegate: Rectangle {
                            id: dataRow
                            required property var modelData
                            required property int index
                            objectName: "reportRow_" + tableBlock.index + "_" + index
                            Layout.fillWidth: true
                            implicitHeight: rowLine.implicitHeight + 10
                            radius: 3; color: index % 2 ? theme.bgSecondary : theme.panel
                            opacity: modelData.dim ? 0.6 : 1
                            RowLayout {
                                id: rowLine
                                anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 6; anchors.rightMargin: 6
                                layoutDirection: Qt.RightToLeft
                                spacing: 4
                                Repeater {
                                    model: dataRow.modelData.cells
                                    delegate: Txt {
                                        required property string modelData
                                        required property int index
                                        Layout.fillWidth: true; Layout.preferredWidth: 1
                                        text: tableBlock.modelData.columns[index].kind === "text" ? modelData : page.cardValue(modelData)
                                        font.pixelSize: theme.fontBodyStrong
                                        horizontalAlignment: tableBlock.modelData.columns[index].kind === "text" ? Text.AlignRight : Text.AlignHCenter
                                    }
                                }
                                Btn {
                                    objectName: "reportOpen_" + tableBlock.index + "_" + dataRow.index
                                    Layout.preferredWidth: 78
                                    visible: !!dataRow.modelData.ref.type
                                    text: dataRow.modelData.refText || "فتح"
                                    accent: theme.textSecondary
                                    implicitHeight: 26
                                    onClicked: reportsVM.open(dataRow.modelData.ref.type, dataRow.modelData.ref.id, dataRow.modelData.ref.extra || "")
                                }
                                Item { Layout.preferredWidth: dataRow.modelData.ref.type ? 0 : 78 }
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: tableBlock.modelData.paged && (tableBlock.modelData.page > 1 || tableBlock.modelData.hasMore)
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Btn { objectName: "reportNext"; text: "التالي ←"; enabled: tableBlock.modelData.hasMore && !reportsVM.busy; onClicked: reportsVM.run(tableBlock.modelData.page + 1) }
                        Txt { objectName: "reportPageLabel"; text: "صفحة " + tableBlock.modelData.page + " من " + Math.max(1, Math.ceil(tableBlock.modelData.rowCount / tableBlock.modelData.pageSize)) }
                        Btn { objectName: "reportPrevious"; text: "→ السابق"; enabled: tableBlock.modelData.page > 1 && !reportsVM.busy; onClicked: reportsVM.run(tableBlock.modelData.page - 1) }
                    }
                }
            }
        }
    }
}
