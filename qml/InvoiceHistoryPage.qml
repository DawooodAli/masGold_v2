import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// سجل الفواتير (read-only). Every value comes formatted from invoiceHistoryVM; this page only
// forwards search text, filters and selections. No invoice can be changed from here.
Item {
    id: historyPage
    objectName: "invoiceHistoryPage"

    property string pageTitle: ""
    signal customerRequested(string customerId)
    readonly property var vm: typeof invoiceHistoryVM !== "undefined" ? invoiceHistoryVM : null
    readonly property bool detailMode: !!vm && vm.detailOpen
    // Three display levels of the invoice table, chosen by THIS page's width (the page is the
    // window minus the sidebar), so every level fits its window exactly:
    //   full     >= 1160  every column (time and salesperson included);
    //   compact  <  1160  the time and salesperson columns step aside (both stay in the detail);
    //   mini     <  1040  the operation type steps aside too; the factory and the amount (the
    //                      last column) always stay.
    readonly property bool compact: width < 1160
    readonly property bool mini: width < 1040
    // The invoice list is this page's reading surface: its cells are a bit bigger than the default
    // body text, the header row has its own size, and one invoice occupies grouped rows.
    readonly property int fontTable: theme.fontBody + 2
    readonly property int fontTableHead: theme.fontTableHeader + 2
    readonly property real tableRowH: mini ? 34 : 38
    // Table gutters: one spacing value shared by the header and every cell row (columns match).
    readonly property real tableSpacing: compact ? 6 : 8

    Theme { id: theme }

    onVisibleChanged: if (visible && vm) { vm.activate(); syncDates(); focusSearch() }
    // Entry always reads fresh data while preserving the applied filters.
    Component.onCompleted: if (visible && vm) { vm.activate(); syncDates() }
    // The date pickers hold a draft until تطبيق; after any range change they show the applied range.
    function syncDates() { if (vm) { fromField.value = vm.dateFrom; toField.value = vm.dateTo } }
    // Scanner-friendly: the search box has focus when History opens and after closing an invoice.
    function focusSearch() { if (!detailMode) Qt.callLater(function() { searchField.forceActiveFocus() }) }
    onDetailModeChanged: focusSearch()

    Shortcut {
        sequence: "Esc"
        enabled: historyPage.visible && historyPage.detailMode
        onActivated: historyPage.vm.closeDetail()
    }

    Rectangle { anchors.fill: parent; color: theme.bgPrimary }

    // ------------------------------------------------------------ building blocks
    component Txt: Label {
        property string value: ""
        property int cellSize: 0       // > 0: a bigger cell font (the invoice-list table)
        text: value
        font.family: theme.fontFamily
        font.pixelSize: cellSize > 0 ? cellSize : theme.fontBody
        color: theme.textSecondary
        horizontalAlignment: Text.AlignRight
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideLeft
        wrapMode: Text.NoWrap
        textFormat: Text.PlainText
    }

    component Head: Txt {
        color: theme.textMuted
        font.pixelSize: cellSize > 0 ? cellSize : theme.fontCaption
        font.bold: true
    }

    component Section: Rectangle {
        id: section
        property string title: ""
        property color accent: theme.gold
        default property alias content: sectionBody.data
        Layout.fillWidth: true
        Layout.preferredHeight: sectionBody.implicitHeight + 42
        color: theme.panel
        radius: theme.radiusCard
        border.color: theme.cardBorder
        Txt { id: sectionTitle; anchors.top: parent.top; anchors.right: parent.right; anchors.topMargin: 7
              anchors.rightMargin: 14; value: section.title; color: section.accent; font.bold: true
              font.pixelSize: theme.fontSection }
        ColumnLayout {
            id: sectionBody
            anchors.top: sectionTitle.bottom; anchors.left: parent.left; anchors.right: parent.right
            anchors.topMargin: 4; anchors.leftMargin: 12; anchors.rightMargin: 14
            spacing: 3
        }
    }

    component Pair: RowLayout {
        property string label: ""
        property string value: ""
        property color valueColor: theme.textPrimary
        property bool strong: false
        Layout.fillWidth: true
        layoutDirection: Qt.RightToLeft
        spacing: 8
        Txt { value: parent.label; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
        Item { Layout.fillWidth: true }
        Txt { value: parent.value; color: parent.valueColor; font.bold: parent.strong
              font.pixelSize: parent.strong ? theme.fontBodyStrong : theme.fontBody }
    }

    // One control height for every field, combo and button of the filter rows (owner review 2026-10-22).
    readonly property int ctlH: 34

    // Owner visual review (2026-10-26): one button style for the whole page — a real hover (Qt's
    // Button ignores the mouse until hoverEnabled is set, so the old hover styling never showed),
    // a pressed state, short colour transitions, breathing padding, and a gold TEXT on the primary
    // action so the brand accent reads at a glance. The gold border alone is not enough on a
    // dark panel.
    component Btn: Button {
        id: btn
        property bool primary: false
        // A gold-outlined action that stays clearly readable on a dark panel without the
        // filled background of a primary button (the «رجوع Esc» escape hatch).
        property bool goldOutline: false
        implicitHeight: historyPage.ctlH
        height: historyPage.ctlH
        hoverEnabled: true
        padding: 12
        contentItem: Label {
            text: btn.text
            font.family: theme.fontFamily
            font.pixelSize: theme.fontBody
            font.bold: btn.primary || btn.goldOutline
            color: !btn.enabled ? theme.textDisabled
                   : btn.primary || btn.goldOutline ? (btn.hovered ? theme.goldBright : theme.gold)
                   : btn.hovered ? theme.textPrimary : theme.textSecondary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            Behavior on color { ColorAnimation { duration: 120 } }
        }
        background: Rectangle {
            radius: theme.radiusSmall
            // The primary action sits on a dark gold-tinted chip; a plain action on the normal
            // panel chip; both press DOWN into the page background when clicked.
            color: !btn.enabled ? theme.bgSecondary
                   : btn.primary ? (btn.down ? theme.goldDark : btn.hovered ? "#3A2F1A" : "#2A2214")
                   : btn.down ? theme.bgPrimary : btn.hovered ? theme.panelHover : theme.bgTertiary
            border.width: 1
            border.color: !btn.enabled ? theme.border
                          : btn.primary ? (btn.down ? theme.goldBright : theme.gold)
                          : btn.goldOutline ? (btn.down ? theme.goldBright : theme.gold)
                          : (btn.hovered || btn.activeFocus) ? theme.goldDark : theme.border
            Behavior on color { ColorAnimation { duration: 120 } }
            Behavior on border.color { ColorAnimation { duration: 120 } }
        }
    }

    component Field: TextField {
        implicitHeight: historyPage.ctlH
        height: historyPage.ctlH
        font.family: theme.fontFamily
        font.pixelSize: theme.fontBody
        color: theme.textPrimary
        placeholderTextColor: theme.textDisabled
        horizontalAlignment: TextInput.AlignRight
        selectByMouse: true
        background: Rectangle { radius: 4; color: theme.bgPrimary
                                border.color: parent.activeFocus ? theme.gold : theme.border }
    }

    component Choice: ComboBox {
        implicitHeight: historyPage.ctlH
        height: historyPage.ctlH
        font.family: theme.fontFamily
        font.pixelSize: theme.fontBody
        textRole: "text"
        valueRole: "code"
    }

    // ============================================================ LIST
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8
        visible: !historyPage.detailMode

        // Search + filters: one compact row (wraps to two at 1080 px).
        Flow {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            spacing: 6
            // Typed or scanned (the printed invoice barcode). Names stay Arabic; a scanner burst is
            // rebuilt from its physical keys (BarcodeField, mixed). Enter: exact number opens it.
            BarcodeField {
                id: searchField
                objectName: "historySearch"
                mixed: true
                width: 260
                implicitHeight: historyPage.ctlH
                height: historyPage.ctlH
                font.family: theme.fontFamily
                font.pixelSize: theme.fontBody
                color: theme.textPrimary
                placeholderTextColor: theme.textDisabled
                horizontalAlignment: TextInput.AlignRight
                selectByMouse: true
                background: Rectangle { radius: 4; color: theme.bgPrimary
                                        border.color: searchField.activeFocus ? theme.gold : theme.border }
                placeholderText: activeFocus || text.length ? "" : "رقم الفاتورة أو اسم / تليفون العميل"
                onTextEdited: historyPage.vm.setSearchText(text)
                onAccepted: historyPage.vm.submitSearch(text)
            }
            // اليوم: the default view (active on open); gold while today is the range.
            Btn { objectName: "historyToday"; text: "📅 اليوم"; primary: !!historyPage.vm && historyPage.vm.todayActive
                  onClicked: { historyPage.vm.setToday(); historyPage.syncDates() } }
            DateField { id: fromField; objectName: "historyDateFrom"; placeholder: "من"; width: 138; height: historyPage.ctlH }
            DateField { id: toField; objectName: "historyDateTo"; placeholder: "إلى"; width: 138; height: historyPage.ctlH }
            // One query with the chosen range (custom range: اليوم is no longer active).
            Btn { objectName: "historyApplyDates"; text: "✔ تطبيق"
                  onClicked: { historyPage.vm.setDateRange(fromField.value, toField.value); historyPage.syncDates() } }
            Choice {
                id: typeChoice
                objectName: "historyType"
                width: 118
                model: [{ code: "ALL", text: "كل العمليات" }, { code: "SELL", text: "بيع" },
                        { code: "BUY", text: "شراء كسر" }, { code: "MIXED", text: "بيع مع كسر" }]
                onActivated: historyPage.vm.setTypeFilter(currentValue)
            }
            Choice {
                id: statusChoice
                objectName: "historyStatus"
                width: 124
                model: [{ code: "ALL", text: "كل الحالات" }, { code: "SETTLED", text: "مسددة" },
                        { code: "PARTIAL", text: "مسددة جزئياً" }, { code: "UNSETTLED", text: "غير مسددة" }]
                onActivated: historyPage.vm.setStatusFilter(currentValue)
            }
            // الصنف / العيار: only the invoices that have a line of that type and karat (the whole invoice is listed).
            Choice {
                id: itemTypeChoice
                objectName: "historyItemType"
                width: 124
                model: historyPage.vm ? historyPage.vm.itemTypeChoices : []
                onActivated: historyPage.vm.setItemTypeFilter(currentValue)
            }
            Choice {
                id: karatChoice
                objectName: "historyKarat"
                width: 112
                model: historyPage.vm ? historyPage.vm.karatChoices : []
                onActivated: historyPage.vm.setKaratFilter(currentValue)
            }
            Btn { objectName: "historyClear"; text: "✖ مسح"
                  onClicked: { searchField.text = ""; historyPage.vm.clearFilters(); historyPage.syncDates()
                               typeChoice.currentIndex = 0; statusChoice.currentIndex = 0
                               itemTypeChoice.currentIndex = 0; karatChoice.currentIndex = 0 } }
            Txt { objectName: "historyRangeText"; height: historyPage.ctlH; value: historyPage.vm ? historyPage.vm.rangeText : ""
                  color: historyPage.vm && historyPage.vm.todayActive ? theme.gold : theme.textSecondary; font.bold: true }
        }

        // رقم الفاتورة: an exact lookup that opens the invoice directly, whatever the date range is.
        RowLayout {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            spacing: 6
            Txt { value: "رقم الفاتورة"; color: theme.textSecondary; Layout.alignment: Qt.AlignVCenter }
            Field {
                id: numberField
                objectName: "historyNumberField"
                Layout.preferredWidth: 170
                placeholderText: activeFocus || text.length ? "" : "مثال: 26100601"
                onTextEdited: if (historyPage.vm) historyPage.vm.clearLookupMessage()
                onAccepted: historyPage.vm.openByNumber(text)
            }
            Btn {
                objectName: "historyNumberOpen"
                text: "🔍 بحث / فتح"
                primary: true
                enabled: !!historyPage.vm && !historyPage.vm.lookupBusy
                onClicked: historyPage.vm.openByNumber(numberField.text)
            }
            Txt {
                objectName: "historyNumberMessage"
                Layout.fillWidth: true
                visible: value.length > 0
                value: historyPage.vm ? historyPage.vm.lookupMessage : ""
                color: theme.red
                font.bold: true
            }
            Item { Layout.fillWidth: true; visible: !historyPage.vm || historyPage.vm.lookupMessage.length === 0 }
        }

        Txt { objectName: "historyError"; Layout.fillWidth: true; visible: value.length > 0
              value: historyPage.vm ? historyPage.vm.errorMessage : ""; color: theme.red }

        // The table: fixed column widths so values line up; one row per invoice.
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: theme.panel
            radius: theme.radiusSmall
            border.color: theme.border

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 6
                spacing: 2

                RowLayout {
                    // Layout.preferredWidth: 0 caps this row's implicit width (Qt then shrinks the
                    // fill-width cells instead of the whole column overflowing the window).
                    Layout.fillWidth: true
                    Layout.preferredWidth: 0
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    layoutDirection: Qt.RightToLeft
                    spacing: historyPage.tableSpacing
                    // EVERY column is fillWidth: on a wide window the free space is spread across
                    // the whole table instead of pooling into one huge half-empty column.
                    Head { value: "رقم الفاتورة"; cellSize: historyPage.fontTableHead
                           Layout.fillWidth: true; Layout.preferredWidth: 76; Layout.minimumWidth: 74 }
                    Head { value: "التاريخ"; cellSize: historyPage.fontTableHead
                           Layout.fillWidth: true; Layout.preferredWidth: 74 }
                    Head { value: "الوقت"; cellSize: historyPage.fontTableHead
                           Layout.fillWidth: true; Layout.preferredWidth: 36
                           visible: !historyPage.compact }
                    Head { value: "العميل"; cellSize: historyPage.fontTableHead
                           Layout.fillWidth: true; Layout.preferredWidth: historyPage.mini ? 104 : 130
                           Layout.minimumWidth: 80 }
                    Head { value: "البائع"; cellSize: historyPage.fontTableHead
                           Layout.fillWidth: true; Layout.preferredWidth: 70
                           visible: !historyPage.compact }
                    Head { value: "نوع العملية"; cellSize: historyPage.fontTableHead
                           Layout.fillWidth: true; Layout.preferredWidth: 62; visible: !historyPage.mini }
                    Head { value: "الصنف"; cellSize: historyPage.fontTableHead
                           Layout.fillWidth: true; Layout.preferredWidth: historyPage.mini ? 124 : 150
                           Layout.minimumWidth: 80 }
                    Head { value: "العيار"; cellSize: historyPage.fontTableHead
                           Layout.fillWidth: true; Layout.preferredWidth: 42 }
                    Head { value: "الوزن (ج)"; cellSize: historyPage.fontTableHead
                           Layout.fillWidth: true; Layout.preferredWidth: 58 }
                    Head { value: "المصنع"; cellSize: historyPage.fontTableHead
                           Layout.fillWidth: true; Layout.preferredWidth: historyPage.mini ? 92 : 104 }
                    Head { value: "المبلغ"; cellSize: historyPage.fontTableHead
                           Layout.fillWidth: true; Layout.preferredWidth: historyPage.mini ? 108 : 114 }
                }

                ListView {
                    id: list
                    objectName: "historyList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    focus: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: historyPage.vm ? historyPage.vm.results : []
                    ScrollBar.vertical: ScrollBar {}
                    Keys.onReturnPressed: if (currentIndex >= 0) historyPage.vm.openInvoice(model[currentIndex].invoiceId)
                    Keys.onEnterPressed: if (currentIndex >= 0) historyPage.vm.openInvoice(model[currentIndex].invoiceId)
                    delegate: Item {
                        id: row
                        required property var modelData
                        required property int index
                        objectName: "historyRow_" + index
                        width: list.width
                        height: group.height
                        // ONE invoice = ONE group of rows (the old-style layout): the first row carries the
                        // invoice's own figures, every row carries one saved line (الصنف / العيار / الوزن /
                        // المصنع). An invoice whose lines are unknown shows its first row alone.
                        readonly property var lines: (modelData.lines && modelData.lines.length)
                                                     ? modelData.lines : [{}]
                        readonly property real rowH: historyPage.tableRowH
                        readonly property bool current: ListView.isCurrentItem

                        Rectangle {                // one background across the whole group
                            anchors.fill: parent
                            radius: 3
                            color: row.current ? theme.bgTertiary
                                                : row.index % 2 ? "transparent" : theme.bgSecondary
                            border.color: row.current ? theme.goldDark : "transparent"
                        }
                        Column {
                            id: group
                            width: parent.width
                            spacing: 0
                            Repeater {
                                model: row.lines
                                delegate: Rectangle {
                                    id: line
                                    required property var modelData
                                    required property int index
                                    width: row.width
                                    height: row.rowH
                                    color: "transparent"
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 6; anchors.rightMargin: 6
                                        layoutDirection: Qt.RightToLeft
                                        spacing: historyPage.tableSpacing
                                        // The invoice's own cells are filled on the FIRST row of the group
                                        // only and stay blank afterwards: the columns remain aligned, so the
                                        // invoice reads as one merged block (exactly like the old program).
                                        // Every cell is fillWidth with the header's exact width, so the
                                        // free space of a wide window spreads across the whole table.
                                        Txt { cellSize: historyPage.fontTable; font.bold: true; color: theme.gold
                                              value: line.index === 0 ? row.modelData.number : ""
                                              objectName: line.index === 0 ? "historyNumber_" + row.index : ""
                                              Layout.fillWidth: true
                                              Layout.preferredWidth: 76; Layout.minimumWidth: 74 }
                                        Txt { cellSize: historyPage.fontTable
                                              value: line.index === 0 ? row.modelData.date : ""
                                              Layout.fillWidth: true; Layout.preferredWidth: 74 }
                                        Txt { cellSize: historyPage.fontTable; color: theme.textMuted
                                              value: line.index === 0 ? row.modelData.time : ""
                                              visible: !historyPage.compact
                                              Layout.fillWidth: true; Layout.preferredWidth: 36 }
                                        Txt { cellSize: historyPage.fontTable; color: theme.textPrimary
                                              value: line.index === 0 ? row.modelData.customer : ""
                                              Layout.fillWidth: true
                                              Layout.preferredWidth: historyPage.mini ? 104 : 130
                                              Layout.minimumWidth: 80 }
                                        Txt { cellSize: historyPage.fontTable; color: theme.textMuted
                                              value: line.index === 0 ? row.modelData.salesperson : ""
                                              visible: !historyPage.compact
                                              Layout.fillWidth: true; Layout.preferredWidth: 70 }
                                        Txt { cellSize: historyPage.fontTable
                                              value: line.index === 0 ? row.modelData.typeText : ""
                                              visible: !historyPage.mini
                                              Layout.fillWidth: true; Layout.preferredWidth: 62 }
                                        // THIS row's saved line: الصنف (coloured like the old program —
                                        // أخضر = بيع المحل، برتقالي = شراء/كسر) then its karat, weight
                                        // and REAL factory.
                                        Txt { cellSize: historyPage.fontTable; font.bold: true
                                              color: (line.modelData && line.modelData.exchanged) ? theme.red
                                                   : (line.modelData && line.modelData.buy) ? theme.amber : theme.green
                                              value: (line.modelData && line.modelData.name) || ""
                                              Layout.fillWidth: true
                                              Layout.preferredWidth: historyPage.mini ? 124 : 150
                                              Layout.minimumWidth: 80 }
                                        // The karat carries the top bar's karat colour (18 / 21 / 22 / 24).
                                        Txt { cellSize: historyPage.fontTable; font.bold: true
                                              color: { var k = Number(line.modelData && line.modelData.karat);
                                                      (line.modelData && line.modelData.exchanged) ? theme.red
                                                      : k === 18 ? theme.balanceKarat18
                                                      : k === 21 ? theme.balanceKarat21
                                                      : k === 22 ? theme.balanceKarat22
                                                      : k === 24 ? theme.balanceKarat24 : theme.textSecondary }
                                              value: (line.modelData && line.modelData.karat) || ""
                                              Layout.fillWidth: true; Layout.preferredWidth: 42 }
                                        // الوزن: weight of a shop-sold line in the normal white,
                                        // the CUSTOMER'S SCRAP weight (شراء كسر) in amber — the same
                                        // scrap colour as its الصنف, so the two are told apart.
                                        Txt { cellSize: historyPage.fontTable
                                              color: (line.modelData && line.modelData.exchanged) ? theme.red
                                                     : (line.modelData && line.modelData.buy) ? theme.amber : theme.textPrimary
                                              font.bold: true
                                              value: (line.modelData && line.modelData.weight) || ""
                                              Layout.fillWidth: true; Layout.preferredWidth: 58 }
                                        // The REAL factory of the line (its own snapshot), never money.
                                        Txt { cellSize: historyPage.fontTable; color: theme.textSecondary
                                              value: (line.modelData && line.modelData.factory) || ""
                                              Layout.fillWidth: true
                                              Layout.preferredWidth: historyPage.mini ? 92 : 104 }
                                        // The LAST column: the plain amount — green = money INTO the
                                        // shop, red = money OUT of it (the colour says it all).
                                        Txt { cellSize: historyPage.fontTable; font.bold: true
                                              color: row.modelData.out ? theme.red : theme.green
                                              value: line.index === 0 ? row.modelData.moved : ""
                                              Layout.fillWidth: true
                                              Layout.preferredWidth: historyPage.mini ? 108 : 114 }
                                    }
                                    Rectangle {        // one hairline UNDER the whole invoice group
                                        visible: line.index === row.lines.length - 1
                                        anchors.bottom: parent.bottom
                                        anchors.left: parent.left; anchors.right: parent.right
                                        anchors.leftMargin: 6; anchors.rightMargin: 6
                                        height: 1
                                        color: theme.border
                                    }
                                }
                            }
                            // An exchanged invoice: its date and «الإجمالي + فرق الاستبدال» for reference.
                            Rectangle {
                                objectName: "historyExchangeNote_" + row.index
                                visible: !!row.modelData.exchangeText
                                width: row.width
                                height: visible ? row.rowH : 0
                                color: "transparent"
                                Txt { anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
                                      cellSize: historyPage.fontTable; color: theme.amber; font.bold: true
                                      value: row.modelData.exchangeText }
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: { list.currentIndex = row.index; list.forceActiveFocus() }
                            onDoubleClicked: historyPage.vm.openInvoice(row.modelData.invoiceId)
                        }
                    }
                    Txt {
                        objectName: "historyEmpty"
                        anchors.centerIn: parent
                        visible: value.length > 0
                        value: historyPage.vm ? historyPage.vm.emptyText : ""
                        color: theme.textMuted
                        font.pixelSize: theme.fontBodyStrong
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            spacing: 8
            Btn { objectName: "historyPrevious"; text: "السابق"; enabled: !!historyPage.vm && historyPage.vm.hasPrevious
                  onClicked: historyPage.vm.previousPage() }
            Txt { objectName: "historyPageText"; value: historyPage.vm ? historyPage.vm.pageText : ""; color: theme.textMuted }
            Btn { objectName: "historyNext"; text: "التالي"; enabled: !!historyPage.vm && historyPage.vm.hasMore
                  onClicked: historyPage.vm.nextPage() }
            Item { Layout.fillWidth: true }
            Txt { value: historyPage.vm && historyPage.vm.busy ? "جاري التحميل..." : "انقر مرتين أو Enter لفتح الفاتورة"
                  color: theme.textMuted; font.pixelSize: theme.fontCaption }
        }
    }

    // ============================================================ DETAIL
    // One operational page: the key figures first, then what was sold (weights), the customer's old
    // gold (its own section), this invoice's exchanges inline, and the money / prices / timeline.
    Item {
        id: detailRoot
        objectName: "historyDetailRoot"
        anchors.fill: parent
        visible: historyPage.detailMode
        readonly property var d: historyPage.vm ? historyPage.vm.detail : ({})
        readonly property var sold: historyPage.vm ? historyPage.vm.soldSummary : ({})
        readonly property var oldGold: historyPage.vm ? historyPage.vm.oldGoldSummary : ({})
        property bool trackingExpanded: false          // the original / returned / remaining card is folded by default
        readonly property bool wide: width >= 1100
        // Sold pieces beside customer old gold (only when both exist and two columns stay readable).
        // Compact sold-table widths: half width beside the old gold, or a narrow window.
        readonly property bool tableCompact: itemsPaired || width < 1100
        // One row height for both tables; the shorter paired table is padded up to the longer one.
        readonly property int rowHeight: 30
        readonly property int targetRows: historyPage.vm
                                          ? Math.max(historyPage.vm.saleLines.length, historyPage.vm.oldGoldLines.length) : 0
        readonly property bool itemsPaired: !!historyPage.vm && historyPage.vm.saleLines.length > 0
                                            && !!oldGold.count && width >= 1000

        function directionColor(direction) {
            return direction === "CUSTOMER_OWES" ? theme.accountReceivable
                 : direction === "SHOP_OWES" ? theme.accountPayable : theme.textSecondary
        }

        RowLayout {
            id: detailBar
            anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
            anchors.margins: 12
            layoutDirection: Qt.RightToLeft
            spacing: 8
            Txt { value: detailRoot.d.number ? "فاتورة " + detailRoot.d.number : ""; color: theme.gold
                  font.bold: true; font.pixelSize: theme.fontPageTitle }
            Txt { value: detailRoot.d.legacyLabel || ""; visible: value.length > 0; color: theme.amber }
            Txt { objectName: "historyReversed"; value: detailRoot.d.reversedText || ""; visible: value.length > 0
                  color: theme.red; font.bold: true }
            Item { Layout.fillWidth: true }
            BusyIndicator { objectName: "historyReprintSpinner"
                  visible: !!historyPage.vm && historyPage.vm.reprintBusy; running: visible
                  Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
            Txt { objectName: "historyReprintMessage"; value: historyPage.vm ? historyPage.vm.reprintMessage : ""
                  visible: value.length > 0
                  color: !historyPage.vm ? theme.green : historyPage.vm.reprintBusy ? theme.gold
                         : historyPage.vm.reprintFailed ? theme.red : theme.green }
            // Lifecycle actions: استبدال (its own permission; the service re-checks every rule).
            Btn { objectName: "historyExchange"
                  text: "استبدال"
                  visible: !!detailRoot.d.canExchange && typeof exchangeVM !== "undefined"
                  onClicked: exchangeVM.open(detailRoot.d.invoiceId) }
            // مرتجع: its own permission; the service re-checks every rule (windows, weights, approval).
            Btn { objectName: "historyReturn"
                  text: "مرتجع"
                  visible: !!detailRoot.d.canReturn && typeof returnVM !== "undefined"
                  onClicked: returnVM.open(detailRoot.d.invoiceId) }
            // تصحيح / عكس: managers only (TRANSACTION_CORRECT, re-checked by the service).
            Btn { objectName: "historyCorrect"
                  text: "تصحيح / عكس"
                  visible: !!detailRoot.d.canCorrect && typeof correctionVM !== "undefined"
                  onClicked: correctionVM.open(detailRoot.d.invoiceId) }
            // تغيير البائع: a manager corrects a wrong seller name (TRANSACTION_CORRECT, re-checked by the seller service); nothing else on the invoice changes.
            Btn { objectName: "historyChangeSeller"
                  text: "تغيير البائع"
                  visible: !!detailRoot.d.canChangeSeller
                  onClicked: sellerDialog.begin() }
            Btn { objectName: "historyReprint"; primary: true
                  text: historyPage.vm && historyPage.vm.reprintBusy ? "جاري الطباعة..." : "إعادة طباعة"
                  visible: !!detailRoot.d.canReprint
                  enabled: !!historyPage.vm && !historyPage.vm.reprintBusy
                  onClicked: historyPage.vm.reprint() }
            // Esc escape hatch, parked at the far LEFT edge of the bar (last item in an RTL row)
            // so it never mixes with the lifecycle actions, and gold-outlined to stay readable.
            Item { Layout.preferredWidth: 18 }
            Btn { objectName: "historyBack"; goldOutline: true
                  text: "رجوع  ⁦Esc⁩"
                  onClicked: historyPage.vm.closeDetail() }
        }

        Txt { objectName: "historyDetailState"; anchors.centerIn: parent
              visible: !!historyPage.vm && (historyPage.vm.detailBusy || historyPage.vm.detailError.length > 0)
              value: !historyPage.vm ? "" : historyPage.vm.detailBusy ? "جاري التحميل..."
                     : (historyPage.vm.detailError ? "تعذر تحميل الفاتورة: " + historyPage.vm.detailError : "")
              color: historyPage.vm && historyPage.vm.detailError ? theme.red : theme.textMuted
              font.pixelSize: theme.fontBodyStrong }

        ScrollView {
            id: detailScroll
            objectName: "historyDetailScroll"
            anchors.top: detailBar.bottom; anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            anchors.margins: 12
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            visible: !!detailRoot.d.number
            readonly property var d: detailRoot.d

            ColumnLayout {
                width: detailScroll.availableWidth
                spacing: 8

                Txt { Layout.fillWidth: true; visible: value.length > 0; value: detailScroll.d.incompleteText || ""
                      color: theme.amber }

                // ============ 1. العميل | الذهب والمصنعية | سعر الذهب وقت الفاتورة (compact, one row)
                GridLayout {
                    objectName: "historyTopRow"
                    Layout.fillWidth: true
                    // Pure شراء كسر has no gold / making card: two cards share the width.
                    columns: detailRoot.width >= 1000 ? (detailScroll.d.isOldGoldOnly ? 2 : 3)
                             : (detailRoot.width >= 700 ? 2 : 1)
                    layoutDirection: Qt.RightToLeft
                    columnSpacing: 8; rowSpacing: 8

                    Section {
                        objectName: "historyCustomerSection"
                        Layout.fillHeight: true             // same top / bottom line as its neighbours
                        Layout.preferredWidth: 3
                        title: "العميل"
                        accent: theme.accountReceivable
                        Flow {
                            objectName: "historyCustomerBand"
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 18
                            ColumnLayout {
                                width: Math.min(implicitWidth, parent.width)
                                spacing: 0
                                Txt { Layout.alignment: Qt.AlignRight; value: "الاسم"; color: theme.textMuted; font.pixelSize: theme.fontCaption }
                                CustomerNameLink { objectName: "historyCustomerName"; Layout.fillWidth: true
                                    text: detailScroll.d.customerName || ""; font.bold: true
                                    customerId: detailScroll.d.customerId || ""
                                    onCustomerRequested: function(customerId) { historyPage.customerRequested(customerId) } }
                            }
                            Figure { label: "التليفون"; value: detailScroll.d.customerMobile || "—" }
                            Figure { label: "العنوان"; value: detailScroll.d.customerPlace || "—" }
                            Figure { label: "البائع"; value: detailScroll.d.salesperson || "—" }
                            Figure { label: "حُفظت بواسطة"; value: detailScroll.d.finalizedBy || "—" }
                        }
                    }
                    Section {
                        objectName: "historyMoneySection"
                        Layout.fillHeight: true             // same top / bottom line as its neighbours
                        Layout.preferredWidth: 2
                        // Nothing was sold from the shop: no gold value / making to show (no empty card).
                        visible: !detailScroll.d.isOldGoldOnly
                        title: "الذهب والمصنعية"
                        accent: theme.priceSell
                        Flow {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 18
                            Figure { objectName: "historyGoldValue"; label: "قيمة الذهب المباع"; value: detailScroll.d.goldValue || "" }
                            Figure { objectName: "historyAverageMaking"; label: "متوسط المصنعية"; value: detailScroll.d.averageMaking || "" }
                        }
                    }
                    Section {
                        objectName: "historyPricesSection"
                        Layout.fillHeight: true             // same top / bottom line as its neighbours
                        Layout.preferredWidth: 2
                        visible: !!historyPage.vm && historyPage.vm.priceSnapshots.length > 0
                        title: "سعر الذهب وقت الفاتورة"
                        accent: theme.priceSell
                        // The invoice's own date / time first (the moment these prices were in force).
                        Txt {
                            objectName: "historyPricesTimestamp"
                            Layout.fillWidth: true
                            visible: value.length > 0
                            value: historyPage.vm ? historyPage.vm.priceSnapshotTime : ""
                            color: theme.textSecondary
                            font.bold: true
                        }
                        // Only the karats used on this invoice, at the page's normal value size.
                        Repeater {
                            model: historyPage.vm ? historyPage.vm.priceSnapshots : []
                            delegate: RowLayout {
                                required property var modelData
                                objectName: "historyPrice_" + modelData.karat
                                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 10
                                Txt { value: "عيار " + modelData.karat; color: theme.textPrimary; font.bold: true
                                      font.pixelSize: theme.fontSection }
                                Item { Layout.fillWidth: true }
                                Txt { objectName: "historyPriceSell_" + modelData.karat; value: "بيع " + modelData.sell
                                      color: theme.priceSell; font.bold: true; font.pixelSize: theme.fontSection }
                                Txt { objectName: "historyPriceBuy_" + modelData.karat; value: "شراء " + modelData.buy
                                      color: theme.priceBuy; font.bold: true; font.pixelSize: theme.fontSection }
                            }
                        }
                    }
                }

                // ============ 2. what changed hands: shop-sold pieces (right) | the customer's old gold (left).
                // Both present (MIXED, from the lines): one row of two equal cards; one present: full width.
                GridLayout {
                    objectName: "historyItemsRow"
                    Layout.fillWidth: true
                    columns: detailRoot.itemsPaired ? 2 : 1
                    layoutDirection: Qt.RightToLeft
                    columnSpacing: 8; rowSpacing: 8
                // ============ 2. shop-sold pieces (weights first)
                Section {
                    objectName: "historyItemsSection"
                    Layout.preferredWidth: 1; Layout.fillHeight: true      // equal halves when paired
                    // Nothing sold from the shop (e.g. شراء كسر): no empty block at all.
                    visible: !!historyPage.vm && historyPage.vm.saleLines.length > 0
                    title: "الأصناف المباعة من المحل"
                    accent: theme.priceSell
                    // ONE table definition for full width and for the half-width (paired / narrow) mode: every column
                    // stays visible; width pressure is absorbed by compact widths and elide + tooltip, never by
                    // dropping المصنع / المقاس / ملاحظات.
                    RowLayout {
                        objectName: "historySoldHeader"
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: detailRoot.tableCompact ? 6 : 10
                        Head { value: "م"; Layout.preferredWidth: detailRoot.tableCompact ? 14 : 20 }
                        Head { value: "الصنف"; Layout.fillWidth: true; Layout.minimumWidth: detailRoot.tableCompact ? 50 : 90 }
                        Head { value: "العيار"; Layout.preferredWidth: detailRoot.tableCompact ? 30 : 44 }
                        Head { value: "الوزن"; Layout.preferredWidth: detailRoot.tableCompact ? 66 : 86 }
                        Head { value: "المصنع"; Layout.preferredWidth: detailRoot.tableCompact ? 54 : 80 }
                        Head { value: "المقاس"; Layout.preferredWidth: detailRoot.tableCompact ? 36 : 50 }
                        Head { value: "النوع"; Layout.preferredWidth: detailRoot.tableCompact ? 70 : 150 }
                        Head { value: "الباركود"; Layout.preferredWidth: detailRoot.tableCompact ? 50 : 80 }
                        Item { visible: !!detailRoot.d.canCorrect; Layout.preferredWidth: detailRoot.tableCompact ? 50 : 60 }
                        Head { value: "ملاحظات"; Layout.preferredWidth: detailRoot.tableCompact ? 60 : 110 }
                    }
                    Repeater {
                        model: historyPage.vm ? historyPage.vm.saleLines : []
                        delegate: Rectangle {
                            id: soldRow
                            required property var modelData
                            required property int index
                            objectName: "historySaleLine_" + index
                            Layout.fillWidth: true
                            Layout.preferredHeight: detailRoot.rowHeight
                            radius: 3
                            color: index % 2 ? "transparent" : theme.bgSecondary
                            RowLayout {
                                anchors.fill: parent
                                layoutDirection: Qt.RightToLeft; spacing: detailRoot.tableCompact ? 6 : 10
                                Txt { value: soldRow.modelData.serial; color: theme.textMuted
                                      Layout.preferredWidth: detailRoot.tableCompact ? 14 : 20 }
                                Txt { value: soldRow.modelData.item; color: theme.textPrimary; font.bold: true
                                      font.pixelSize: theme.fontBodyStrong; Layout.fillWidth: true
                                      Layout.minimumWidth: detailRoot.tableCompact ? 50 : 90 }
                                Txt { value: soldRow.modelData.karat; Layout.preferredWidth: detailRoot.tableCompact ? 30 : 44 }
                                Txt { value: soldRow.modelData.weight; color: theme.textPrimary; font.bold: true
                                      font.pixelSize: theme.fontBodyStrong; Layout.preferredWidth: detailRoot.tableCompact ? 66 : 86 }
                                Txt { objectName: "historySoldFactory_" + soldRow.index; value: soldRow.modelData.factory || "—"
                                      Layout.preferredWidth: detailRoot.tableCompact ? 54 : 80
                                      ToolTip.visible: factoryHover.hovered && truncated; ToolTip.text: value
                                      HoverHandler { id: factoryHover } }
                                Txt { objectName: "historySoldSize_" + soldRow.index; value: soldRow.modelData.size || "—"
                                      Layout.preferredWidth: detailRoot.tableCompact ? 36 : 50 }
                                Txt { value: soldRow.modelData.kindText; Layout.preferredWidth: detailRoot.tableCompact ? 70 : 150
                                      color: soldRow.modelData.manual ? theme.amber
                                           : soldRow.modelData.fromOldGold ? theme.balanceKarat21 : theme.textMuted
                                      font.pixelSize: theme.fontTableHeader
                                      ToolTip.visible: kindHover.hovered && truncated; ToolTip.text: value
                                      HoverHandler { id: kindHover } }
                                // Barcode: aggressive elide, the full value in the tooltip.
                                Txt { value: soldRow.modelData.barcode || "—"; color: theme.textMuted
                                      font.pixelSize: theme.fontTableHeader; Layout.preferredWidth: detailRoot.tableCompact ? 50 : 80
                                      ToolTip.visible: barcodeHover.hovered && truncated; ToolTip.text: value
                                      HoverHandler { id: barcodeHover } }
                                Item { visible: !!detailRoot.d.canCorrect; Layout.preferredWidth: detailRoot.tableCompact ? 50 : 60; Layout.fillHeight: true
                                       Btn { objectName: "historyPieceCorrect_" + soldRow.index; text: "تصحيح"; anchors.fill: parent
                                             visible: soldRow.modelData.identified && soldRow.modelData.lineId.length > 0
                                             onClicked: pieceDialog.begin(soldRow.modelData) } }
                                // Notes: one line (never a giant row), elided, full text in the tooltip.
                                Txt { objectName: "historySoldNotes_" + soldRow.index; value: soldRow.modelData.notes || "—"
                                      color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                      Layout.preferredWidth: detailRoot.tableCompact ? 60 : 110
                                      ToolTip.visible: notesHover.hovered && truncated; ToolTip.text: value
                                      HoverHandler { id: notesHover } }
                            }
                        }
                    }
                    // Visual-only filler rows (paired mode): never data, never counted, never saved.
                    Repeater {
                        objectName: "historySoldFillers"
                        model: detailRoot.itemsPaired && historyPage.vm
                               ? Math.max(0, detailRoot.targetRows - historyPage.vm.saleLines.length) : 0
                        delegate: Item { required property int index
                                         objectName: "historySoldFiller_" + index
                                         Layout.fillWidth: true; Layout.preferredHeight: detailRoot.rowHeight }
                    }
                    WeightTotals { objectName: "historySoldTotals"; visible: (detailRoot.sold.count || "0") !== "0"
                                   summary: detailRoot.sold; noun: "قطعة"; accent: theme.priceSell }
                }

                // ============ 3. the customer's old gold: never mixed with sold pieces
                Section {
                    objectName: "historyOldGoldSection"
                    Layout.preferredWidth: 1; Layout.fillHeight: true
                    visible: !!detailRoot.oldGold.count
                    title: "كسر مشترى من العميل"
                    accent: theme.balanceKarat21
                    RowLayout {
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 10
                        Head { value: "العيار"; Layout.preferredWidth: 44 }
                        Head { value: "الوزن"; Layout.preferredWidth: 86 }
                        Head { value: "السعر"; Layout.preferredWidth: 90 }
                        Head { value: "الخصم"; Layout.preferredWidth: 70 }
                        Item { Layout.fillWidth: true }
                        Head { value: "القيمة"; Layout.preferredWidth: 110 }
                    }
                    Repeater {
                        model: historyPage.vm ? historyPage.vm.oldGoldLines : []
                        // Same fixed row height as the sold table, so paired tables line up row for row.
                        delegate: Rectangle {
                            id: oldRow
                            required property var modelData
                            required property int index
                            objectName: "historyOldGoldLine_" + index
                            Layout.fillWidth: true
                            Layout.preferredHeight: detailRoot.rowHeight
                            radius: 3
                            color: index % 2 ? "transparent" : theme.bgSecondary
                            RowLayout {
                                anchors.fill: parent
                                layoutDirection: Qt.RightToLeft; spacing: 10
                                Txt { value: oldRow.modelData.karat; Layout.preferredWidth: 44 }
                                Txt { value: oldRow.modelData.weight; color: theme.textPrimary; font.bold: true
                                      font.pixelSize: theme.fontBodyStrong; Layout.preferredWidth: 86 }
                                Txt { value: oldRow.modelData.price; Layout.preferredWidth: 90 }
                                Txt { value: oldRow.modelData.discount; Layout.preferredWidth: 70 }
                                Item { Layout.fillWidth: true }
                                Txt { value: oldRow.modelData.value; color: theme.textPrimary; Layout.preferredWidth: 110 }
                            }
                        }
                    }
                    // Visual-only filler rows (paired mode): the totals strips of both cards sit on one line.
                    Repeater {
                        objectName: "historyOldGoldFillers"
                        model: detailRoot.itemsPaired && historyPage.vm
                               ? Math.max(0, detailRoot.targetRows - historyPage.vm.oldGoldLines.length) : 0
                        delegate: Item { required property int index
                                         objectName: "historyOldGoldFiller_" + index
                                         Layout.fillWidth: true; Layout.preferredHeight: detailRoot.rowHeight }
                    }
                    WeightTotals { objectName: "historyOldGoldTotals"; summary: detailRoot.oldGold; noun: "بند"; accent: theme.balanceKarat21
                                   extra: detailRoot.oldGold.value ? "القيمة " + detailRoot.oldGold.value : "" }
                }
                }

                // ============ 2c. ONE financial / transaction strip, right under the invoice (owner 2026-10-28)
                Rectangle {
                    objectName: "historyHeaderSection"
                    Layout.fillWidth: true
                    Layout.preferredHeight: figures.implicitHeight + 20
                    color: theme.panel
                    radius: theme.radiusSmall
                    border.color: theme.goldDark
                    Flow {
                        id: figures
                        objectName: "historySummaryStrip"
                        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: 14; anchors.rightMargin: 14
                        layoutDirection: Qt.RightToLeft
                        spacing: 28
                        Figure { objectName: "historyInvoiceTotal"; label: "إجمالي الفاتورة"; value: detailScroll.d.invoiceTotal || ""
                                 valueColor: theme.priceSell; big: true }
                        // Direction from the saved payments (المستلم من العميل / المدفوع للعميل / لا يوجد دفع فعلي).
                        Figure { objectName: "historyMoved"; label: detailScroll.d.movedLabel || ""
                                 value: detailScroll.d.movedAmount || "—"; note: detailScroll.d.paymentMethods || "" }
                        Figure { objectName: "historyAccount"; label: "حساب العميل (هذه الفاتورة)"; value: detailScroll.d.accountText || ""
                                 valueColor: detailRoot.directionColor(detailScroll.d.accountDirection) }
                        Figure { objectName: "historyFinalizedAt"; label: "تاريخ ووقت الحفظ"; value: detailScroll.d.finalizedAt || "" }
                        Figure { objectName: "historyOperation"; label: "العملية"
                                 value: (detailScroll.d.typeText || "") + " • " + (detailScroll.d.settlementText || "") }
                    }
                }

                // The exchanges and the invoice as saved before them: two cards side by side (owner 2026-10-28).
                RowLayout {
                    objectName: "historyExchangeRow"
                    Layout.fillWidth: true
                    layoutDirection: Qt.RightToLeft
                    spacing: 10
                    // ============ 4. exchanges of this invoice (inline, compact; only exchange facts)
                    // One card per exchange: a second exchange is a third card, and so on.
                    Repeater {
                        model: historyPage.vm ? historyPage.vm.exchanges : []
                        delegate: Item {
                            id: exchangeCard
                            required property var modelData
                            required property int index
                            Layout.preferredWidth: 1; Layout.fillWidth: true; Layout.alignment: Qt.AlignTop
                            implicitHeight: card.Layout.preferredHeight
                            Section {
                              id: card
                              objectName: "historyExchangesSection"
                              anchors.fill: parent
                              title: "استبدال " + exchangeCard.modelData.code
                              accent: theme.accountPayable
                              ColumnLayout {
                                id: exchangeBlock
                                property var modelData: exchangeCard.modelData
                                property int index: exchangeCard.index
                                objectName: "historyExchange_" + index
                                Layout.fillWidth: true
                                spacing: 1
                                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: theme.border
                                            visible: exchangeBlock.index > 0 }
                                RowLayout {
                                    Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 10
                                    Txt { value: exchangeBlock.modelData.code; color: theme.gold; font.bold: true
                                          font.pixelSize: theme.fontBodyStrong }
                                    Txt { value: exchangeBlock.modelData.time + "  •  " + exchangeBlock.modelData.actor
                                          color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                                    Item { Layout.fillWidth: true }
                                    Txt { value: exchangeBlock.modelData.directionText; font.bold: true
                                          color: detailRoot.directionColor(exchangeBlock.modelData.direction) }
                                    Txt { value: exchangeBlock.modelData.amount; font.bold: true; font.pixelSize: theme.fontSection
                                          color: detailRoot.directionColor(exchangeBlock.modelData.direction) }
                                }
                                // One labelled line per fact (owner 2026-10-28: no running prose).
                                Pair { label: "القطعة المرتجعة"; valueColor: theme.accountPayable; strong: true
                                       value: exchangeBlock.modelData.returned.map(function(p) {
                                           return p.item + " عيار " + p.karat + " — " + p.weight }).join("  |  ") }
                                Pair { label: "القطعة البديلة"; valueColor: theme.priceSell; strong: true
                                       value: exchangeBlock.modelData.replacements.map(function(p) {
                                           return p.item + " عيار " + p.karat + " — " + p.weight
                                                  + (p.source ? " (" + p.source + ")" : "") }).join("  |  ") }
                                Pair { label: "فرق الوزن"; visible: value.length > 0
                                       value: exchangeBlock.modelData.deltaText.replace("فرق الوزن ", "").replace("فرق الذهب الصافي ", "") }
                                Pair { label: "سعر الجرام"; visible: value.length > 0
                                       value: exchangeBlock.modelData.priceText }
                                Pair { label: "قيمة فرق الذهب"; visible: value.length > 0
                                       value: exchangeBlock.modelData.goldText.replace("قيمة فرق الذهب ", "") }
                                Pair { label: "المصنعية"; visible: value.length > 0
                                       value: exchangeBlock.modelData.makingText }
                                Pair { objectName: "historyExchangeMoney_" + exchangeBlock.index
                                       label: exchangeBlock.modelData.moneyTitle; visible: exchangeBlock.modelData.money.length > 0
                                       value: exchangeBlock.modelData.money }
                                Pair { label: "حساب العميل"; visible: value.length > 0
                                       value: exchangeBlock.modelData.accountText.replace("حساب العميل: ", "") }
                                Pair { objectName: "historyExchangeDisposition_" + exchangeBlock.index
                                       label: "حالة القطعة المرتجعة"; visible: value.length > 0; valueColor: theme.amber
                                       value: exchangeBlock.modelData.pendingText }
                            }
                              }
                            }
                        }

                    // ============ 3b. the invoice as saved, before its exchange (the sections above show the current one)
                    Section {
                        objectName: "historyBeforeExchangeSection"
                        Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
                        visible: !!historyPage.vm && historyPage.vm.exchangedInvoice
                        title: "الفاتورة قبل الاستبدال"
                        accent: theme.textMuted
                        Repeater {
                            model: historyPage.vm ? historyPage.vm.beforeExchange.rows || [] : []
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 14
                                Txt { value: modelData.item; font.bold: true }
                                Txt { value: "عيار " + modelData.karat + " — " + modelData.weight }
                                Txt { value: modelData.barcode; color: theme.textMuted }
                                Item { Layout.fillWidth: true }
                                Txt { value: modelData.value }
                            }
                        }
                        Txt {
                            objectName: "historyBeforeExchangeTotal"
                            value: historyPage.vm && historyPage.vm.exchangedInvoice
                                   ? "إجمالي الأصلية " + historyPage.vm.beforeExchange.total + "  •  " + historyPage.vm.beforeExchange.deltaText
                                     + "  •  الإجمالي بعد الاستبدال " + historyPage.vm.beforeExchange.currentTotal : ""
                            color: theme.gold; font.bold: true
                        }
                    }
                }

                // ============ 4c. Corrections (RV reversal / FC settlement correction), beneath the original
                Section {
                    objectName: "historyCorrectionsSection"
                    visible: !!historyPage.vm && historyPage.vm.corrections.length > 0
                    title: detailRoot.d.reversedText || "التصحيحات"
                    accent: theme.red
                    Repeater {
                        model: historyPage.vm ? historyPage.vm.corrections : []
                        delegate: ColumnLayout {
                            id: correctionBlock
                            required property var modelData
                            required property int index
                            objectName: "historyCorrection_" + index
                            Layout.fillWidth: true
                            spacing: 1
                            RowLayout {
                                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 10
                                Txt { value: correctionBlock.modelData.title; font.bold: true
                                      color: correctionBlock.modelData.isReversal ? theme.red : theme.amber }
                                Txt { value: correctionBlock.modelData.time + "  •  " + correctionBlock.modelData.by
                                      color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                                Item { Layout.fillWidth: true }
                            }
                            Txt { Layout.fillWidth: true; value: correctionBlock.modelData.detail; color: theme.textPrimary
                                  font.pixelSize: theme.fontBodyStrong; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Repeater {
                                model: correctionBlock.modelData.after.length > 0
                                       ? [{ "label": "قبل", "text": correctionBlock.modelData.before, "color": theme.textSecondary },
                                          { "label": "بعد", "text": correctionBlock.modelData.after, "color": theme.gold }]
                                       : []
                                delegate: RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 10
                                    Txt { Layout.preferredWidth: 46; value: modelData.label; color: theme.textMuted
                                          font.pixelSize: theme.fontBody }
                                    Txt { Layout.fillWidth: true; value: modelData.text; color: modelData.color
                                          font.pixelSize: theme.fontSection; font.bold: true
                                          wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                }
                            }
                            Txt { Layout.fillWidth: true; visible: value.length > 0
                                  value: "الأثر على الحسابات:  " + correctionBlock.modelData.posted
                                  color: theme.textSecondary; font.pixelSize: theme.fontBody; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { Layout.fillWidth: true; value: correctionBlock.modelData.reason; color: theme.textPrimary
                                  font.pixelSize: theme.fontBody; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                        }
                    }
                }

                // ============ 4d. After the invoice (REQ-BILL-12): the original stays; later movements apart
                Section {
                    objectName: "historyPostInvoiceSection"
                    visible: !!historyPage.vm && !!historyPage.vm.postInvoice.visible
                    title: "حركات حساب العميل اللاحقة"
                    accent: theme.accountReceivable
                    Txt { objectName: "historyPostOriginal"; Layout.fillWidth: true
                          value: historyPage.vm && historyPage.vm.postInvoice.visible ? historyPage.vm.postInvoice.originalText : ""
                          color: theme.textSecondary; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    Txt { objectName: "historyPostCurrent"; Layout.fillWidth: true
                          value: historyPage.vm && historyPage.vm.postInvoice.visible ? historyPage.vm.postInvoice.currentText : ""
                          color: historyPage.vm && historyPage.vm.postInvoice.settled ? theme.green : theme.amber
                          font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    Repeater {
                        model: historyPage.vm && historyPage.vm.postInvoice.visible ? historyPage.vm.postInvoice.rows : []
                        delegate: RowLayout {
                            required property var modelData
                            required property int index
                            objectName: "historyPostRow_" + index
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 10
                            Txt { value: modelData.reference; font.bold: true; color: theme.textPrimary }
                            Txt { value: modelData.type; color: theme.textSecondary }
                            Txt { value: modelData.amount + " ج"; color: theme.textPrimary }
                            Txt { visible: value.length > 0; value: modelData.methods; color: theme.textMuted
                                  font.pixelSize: theme.fontTableHeader }
                            Txt { value: modelData.time + "  •  " + modelData.actor; color: theme.textMuted
                                  font.pixelSize: theme.fontTableHeader }
                            Item { Layout.fillWidth: true }
                        }
                    }
                }

                // ============ 4b. Returns of this invoice (the invoice above stays exactly as finalized)
                Section {
                    objectName: "historyReturnsSection"
                    visible: !!historyPage.vm && historyPage.vm.returns.length > 0
                    title: "المرتجعات"
                    accent: theme.accountPayable
                    Repeater {
                        model: historyPage.vm ? historyPage.vm.returns : []
                        delegate: ColumnLayout {
                            id: returnBlock
                            required property var modelData
                            required property int index
                            objectName: "historyReturn_" + index
                            Layout.fillWidth: true
                            spacing: 1
                            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: theme.border
                                        visible: returnBlock.index > 0 }
                            Flow {
                                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 10
                                Txt { value: returnBlock.modelData.code; color: theme.gold; font.bold: true
                                      font.pixelSize: theme.fontBodyStrong }
                                Txt { objectName: "historyReturnHeadline_" + returnBlock.index
                                      value: returnBlock.modelData.headline; color: theme.textPrimary; font.bold: true }
                                Txt { value: returnBlock.modelData.time + "  •  " + returnBlock.modelData.actor
                                      color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                                Txt { value: "للعميل"; font.bold: true; color: theme.accountPayable }
                                Txt { objectName: "historyReturnTotal_" + returnBlock.index
                                      value: returnBlock.modelData.total; font.bold: true; font.pixelSize: theme.fontSection
                                      color: theme.accountPayable }
                            }
                            // One labelled line per fact (owner 2026-11-02: no running prose), the same `Pair` as the exchange card.
                            Repeater {
                                model: returnBlock.modelData.pieces
                                delegate: ColumnLayout {
                                    id: pieceBlock
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: 1
                                    Pair { label: "القطعة المرتجعة"; valueColor: theme.accountPayable; strong: true
                                           value: pieceBlock.modelData.item + " عيار " + pieceBlock.modelData.karat + " — " + pieceBlock.modelData.weight
                                                  + (pieceBlock.modelData.partial ? " من " + pieceBlock.modelData.sold : "") }
                                    Pair { label: "الباركود"; visible: value.length > 0
                                           value: (pieceBlock.modelData.barcode || "")
                                                  + (pieceBlock.modelData.itemId ? " (#" + pieceBlock.modelData.itemId + ")" : "") }
                                    Pair { label: "القيمة التاريخية"; visible: pieceBlock.modelData.historical
                                           value: pieceBlock.modelData.valuation.replace("القيمة التاريخية المرتجعة: ", "") }
                                    Pair { label: "سعر الاسترداد"; visible: !pieceBlock.modelData.historical && value.length > 0
                                           value: pieceBlock.modelData.price }
                                    Pair { label: "قيمة الذهب"; visible: !pieceBlock.modelData.historical
                                           value: pieceBlock.modelData.gold }
                                    Pair { label: "المصنعية المستردة"; visible: !pieceBlock.modelData.historical
                                           value: pieceBlock.modelData.making }
                                }
                            }
                            Txt { objectName: "historyReturnValuation_" + returnBlock.index
                                  Layout.fillWidth: true; value: returnBlock.modelData.valuation.split("  •  ").join("
")
                                  wrapMode: Text.WordWrap; color: theme.accountPayable; font.pixelSize: theme.fontBodyStrong }
                            Txt { objectName: "historyReturnStatus_" + returnBlock.index
                                  Layout.fillWidth: true; value: returnBlock.modelData.status
                                  wrapMode: Text.WordWrap; color: theme.amber }
                            Repeater {
                                objectName: "historyReturnMoney_" + returnBlock.index
                                model: returnBlock.modelData.money.length > 0
                                       ? returnBlock.modelData.money.substring(returnBlock.modelData.money.indexOf(": ") + 2).split(" • ") : []
                                delegate: Pair { required property var modelData; required property int index
                                                 label: index === 0 ? returnBlock.modelData.money.substring(0, returnBlock.modelData.money.indexOf(": ")) : ""
                                                 value: modelData; strong: true }
                            }
                            Pair { label: "حساب العميل"; visible: value.length > 0
                                   value: returnBlock.modelData.accountText.replace("للعميل على حسابه ", "") }
                            Txt { objectName: "historyReturnDisposition_" + returnBlock.index
                                  Layout.fillWidth: true; visible: value.length > 0
                                  value: returnBlock.modelData.dispositionText.split("  •  ").join("
")
                                  color: theme.amber; font.pixelSize: theme.fontBody; wrapMode: Text.WordWrap }
                        }
                    }
                }

                // ============ 6. the original / returned / remaining of each sold piece: the LAST card, after the total strip
                Section {
                    id: trackingSection
                    objectName: "historyReturnTrackingSection"
                    title: "الأصلي / المرتجع / المتبقي"
                    visible: !!historyPage.vm && historyPage.vm.returnTracking.length > 0
                    Btn { objectName: "historyTrackingToggle"; text: detailRoot.trackingExpanded ? "إخفاء" : "عرض"
                          onClicked: detailRoot.trackingExpanded = !detailRoot.trackingExpanded }
                    Repeater {
                        model: historyPage.vm && detailRoot.trackingExpanded ? historyPage.vm.returnTracking : []
                        delegate: ColumnLayout {
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true
                            spacing: 3
                            Txt { Layout.fillWidth: true; value: modelData.item + (modelData.barcode ? " — " + modelData.barcode : "")
                                  font.bold: true; color: theme.gold }
                            Txt { objectName: "historyTrackingOriginal_" + index; Layout.fillWidth: true
                                  value: modelData.original; wrapMode: Text.WordWrap }
                            Txt { objectName: "historyTrackingReturned_" + index; Layout.fillWidth: true
                                  value: modelData.returned; wrapMode: Text.WordWrap; color: theme.accountPayable }
                            Txt { objectName: "historyTrackingRemaining_" + index; Layout.fillWidth: true
                                  value: modelData.remaining; wrapMode: Text.WordWrap }
                        }
                    }
                }

                Txt { Layout.fillWidth: true; visible: value.length > 0
                      value: detailScroll.d.notes ? "ملاحظات: " + detailScroll.d.notes : ""; color: theme.textMuted }
                Item { Layout.preferredHeight: 4 }
            }
        }
    }

    // One key figure: small muted label above a strong value.
    component Figure: ColumnLayout {
        property string label: ""
        property string value: ""
        property color valueColor: theme.textPrimary
        property bool big: false
        property string note: ""            // optional small secondary line (e.g. the payment method)
        spacing: 0
        Txt { Layout.alignment: Qt.AlignRight; value: parent.label; color: theme.textMuted; font.pixelSize: theme.fontCaption }
        Txt { Layout.alignment: Qt.AlignRight; value: parent.value; color: parent.valueColor; font.bold: true
              font.pixelSize: parent.big ? theme.fontNumber : theme.fontBodyStrong }
        Txt { Layout.alignment: Qt.AlignRight; visible: value.length > 0; value: parent.note; color: theme.textMuted
              font.pixelSize: theme.fontCaption }
    }

    // Count, total grams and grams per karat under a table.
    component WeightTotals: Rectangle {
        id: totalsRoot
        property var summary: ({})
        property string noun: ""
        property color accent: theme.gold
        property string extra: ""
        Layout.fillWidth: true
        Layout.preferredHeight: 28
        radius: 4
        color: theme.bgTertiary
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10
            layoutDirection: Qt.RightToLeft; spacing: 14
            Txt { value: (totalsRoot.summary.count || "0") + " " + totalsRoot.noun; color: theme.textMuted }
            Txt { value: "الإجمالي " + (totalsRoot.summary.total ? totalsRoot.summary.total + " جرام" : ""); color: totalsRoot.accent; font.bold: true
                  font.pixelSize: theme.fontBodyStrong }
            Repeater {
                model: totalsRoot.summary.karats || []
                delegate: Txt { required property var modelData
                                value: "عيار " + modelData.karat + ": " + modelData.weight + " جرام"; color: theme.textSecondary }
            }
            Item { Layout.fillWidth: true }
            Txt { value: totalsRoot.extra; visible: value.length > 0; color: theme.textPrimary }
        }
    }

    Popup {
        id: sellerDialog
        objectName: "historySellerDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(440, historyPage.width - 24)
        modal: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        function begin() {
            sellerReason.text = ""
            if (historyPage.vm) historyPage.vm.loadSellers()
            open()
        }
        Connections {
            target: historyPage.vm
            function onChanged() { if (historyPage.vm && historyPage.vm.sellerChanged && sellerDialog.opened) sellerDialog.close() }
        }
        contentItem: ColumnLayout {
            spacing: 8
            Txt { Layout.fillWidth: true; value: "تغيير البائع"; color: theme.gold; font.bold: true }
            Txt { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: theme.textSecondary
                  value: "البائع الحالي: " + ((historyPage.vm && historyPage.vm.detail.salesperson) || "—") + ". يتغير اسم البائع فقط؛ باقي الفاتورة كما هي." }
            Txt { Layout.fillWidth: true; value: "البائع الجديد *" }
            ComboBox { id: sellerCombo; objectName: "historySellerCombo"; Layout.fillWidth: true
                       model: historyPage.vm ? historyPage.vm.sellerChoices : []
                       textRole: "name"; font.family: "Cairo" }
            Txt { Layout.fillWidth: true; value: "السبب *" }
            TextField { id: sellerReason; objectName: "historySellerReason"; Layout.fillWidth: true; placeholderText: "مثلاً: اسم البائع اتكتب بالغلط"
                        font.family: "Cairo"; horizontalAlignment: TextInput.AlignRight }
            Txt { objectName: "historySellerMessage"; Layout.fillWidth: true; wrapMode: Text.WordWrap
                  visible: value.length > 0; color: theme.red
                  value: !!historyPage.vm && !historyPage.vm.sellerChanged ? historyPage.vm.sellerMessage : "" }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn { objectName: "historySellerSave"; primary: true; text: "حفظ"
                      enabled: sellerCombo.currentIndex >= 0 && sellerReason.text.trim().length > 0 && !!historyPage.vm
                      onClicked: historyPage.vm.changeSeller(String(historyPage.vm.sellerChoices[sellerCombo.currentIndex].id), sellerReason.text) }
                Btn { objectName: "historySellerCancel"; text: "إلغاء"; onClicked: sellerDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    Popup {
        id: pieceDialog
        objectName: "historyPieceDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(460, historyPage.width - 24)
        modal: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        property var line: ({})
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        function begin(line) {
            pieceDialog.line = line
            pieceBarcode.text = ""; pieceReason.text = ""; pieceConfirm.checked = false
            if (historyPage.vm) historyPage.vm.resetPieceCorrection()
            open()
            pieceBarcode.forceActiveFocus()
        }
        Connections {
            target: historyPage.vm
            function onChanged() { if (historyPage.vm && historyPage.vm.pieceCorrected && pieceDialog.opened) pieceDialog.close() }
        }
        contentItem: ColumnLayout {
            spacing: 8
            Txt { Layout.fillWidth: true; value: "تصحيح باركود قطعة مباعة"; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection }
            Pair { label: "القطعة"; strong: true
                   value: (pieceDialog.line.item || "—") + " — عيار " + (pieceDialog.line.karat || "") + " — " + (pieceDialog.line.weight || "") }
            Pair { label: "الباركود المسجل (الخطأ)"; valueColor: theme.red; strong: true; value: pieceDialog.line.barcode || "—" }
            Txt { Layout.fillWidth: true; value: "الباركود الصحيح (القطعة التي خرجت فعلًا) *" }
            BarcodeField { id: pieceBarcode; objectName: "historyPieceBarcode"; Layout.fillWidth: true; Layout.preferredHeight: 40
                           placeholderText: "امسح أو اكتب الباركود الصحيح"; font.family: "Cairo"; horizontalAlignment: TextInput.AlignRight }
            Txt { Layout.fillWidth: true; value: "السبب *" }
            TextField { id: pieceReason; objectName: "historyPieceReason"; Layout.fillWidth: true; placeholderText: "مثلاً: اتمسح باركود قطعة غير التي سُلّمت"
                        font.family: "Cairo"; horizontalAlignment: TextInput.AlignRight }
            Txt { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: theme.textSecondary
                  value: "يجب أن تكون القطعة الصحيحة بالمخزون ونفس العيار والوزن. السعر والإجمالي والمدفوع لا يتغيرون؛ القطعة المسجلة بالخطأ ترجع للمخزون." }
            CheckBox { id: pieceConfirm; objectName: "historyPieceConfirm"; Layout.fillWidth: true
                       text: "أؤكد أن القطعة الصحيحة هي التي سُلّمت للعميل"; font.family: "Cairo" }
            Txt { objectName: "historyPieceMessage"; Layout.fillWidth: true; wrapMode: Text.WordWrap
                  visible: value.length > 0; color: theme.red
                  value: !!historyPage.vm && !historyPage.vm.pieceCorrected ? historyPage.vm.pieceMessage : "" }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn { objectName: "historyPieceSave"; primary: true; text: "تصحيح الباركود"
                      enabled: !!historyPage.vm && pieceBarcode.text.trim().length > 0 && pieceReason.text.trim().length > 0 && pieceConfirm.checked
                      onClicked: historyPage.vm.correctPiece(pieceDialog.line.lineId, pieceBarcode.text, pieceReason.text, pieceConfirm.checked) }
                Btn { objectName: "historyPieceCancel"; text: "إلغاء"; onClicked: pieceDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }
}
