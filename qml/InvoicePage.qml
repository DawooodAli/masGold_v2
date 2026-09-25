import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Billing V2 (canonical invoices) — point-of-sale page, converged on the masGold
// prototype (mainWindow_4.ui): thin header strip, sale and old-gold entry rows
// directly above their tables side by side, per-karat gram strips, and a fixed
// money strip. Every amount shown comes from invoiceVM, recalculated by
// InvoiceService; this page only forwards employee input.
Item {
    id: invoicePage
    objectName: "invoicePage"

    property string pageTitle: ""
    property bool activated: false
    readonly property bool keysActive: visible && !confirmDialog.opened && !searchPanel.visible
    readonly property bool compact: width < 900

    Theme { id: theme }

    onVisibleChanged: {
        if (visible && !activated) {
            activated = true
            invoiceVM.activate()
        }
        if (visible)
            focusBarcode()
    }
    Component.onCompleted: {
        if (visible && !activated) {
            activated = true
            invoiceVM.activate()
        }
    }

    // ---------------- بحث عن قطعة (F3): a temporary overlay over the sales table
    function openItemSearch() {
        if (!visible || confirmDialog.opened)
            return
        searchPanel.visible = true
        searchText.forceActiveFocus()
        searchText.selectAll()
        runItemSearch()
    }
    function closeItemSearch() {
        searchPanel.visible = false
        searchDebounce.stop()
        invoiceVM.clearItemSearch()
        focusBarcode()
    }
    function runItemSearch() {
        searchDebounce.stop()
        searchPanel.chosenIndex = -1
        invoiceVM.searchItems(searchText.text, searchPanel.karatId, searchWeight.text, searchTolerance.text,
                              searchSize.text)
    }
    // Explicit choice only: the employee picked this row and confirmed it.
    function confirmSearchedItem() {
        var rows = invoiceVM.searchResults
        var row = searchPanel.chosenIndex
        if (row < 0 || row >= rows.length)
            return
        if (invoiceVM.addSearchedItem(rows[row].inventoryItemId))
            closeItemSearch()
    }

    function focusBarcode() {
        barcodeField.forceActiveFocus()
        barcodeField.selectAll()
    }
    // Prototype flow: scan + Enter adds and refocuses; Enter on an empty field
    // moves on to the money (the next step of the transaction).
    function barcodeEnter() {
        if (barcodeField.text.length === 0) {
            if (invoiceVM.saleCount + invoiceVM.oldGoldCount > 0)
                cashField.forceActiveFocus()
            return
        }
        addBarcode()
    }
    function addBarcode() {
        invoiceVM.addBarcode(barcodeField.text)
        barcodeField.text = ""
        focusBarcode()
    }
    function addOldGold() {
        if (invoiceVM.oldGoldKaratId < 0 || oldGoldWeight.text.length === 0)
            return
        invoiceVM.addOldGold(invoiceVM.oldGoldKaratId, oldGoldWeight.text)
        oldGoldWeight.text = ""
        oldGoldWeight.forceActiveFocus()
    }
    function chooseKarat(karat) {
        var rows = invoiceVM.priceRows
        for (var i = 0; i < rows.length; ++i)
            if (rows[i].karat === karat)
                invoiceVM.setOldGoldKarat(rows[i].karatId)
        oldGoldWeight.forceActiveFocus()
    }
    // One confirmation panel; stale-price / Dead Cash acknowledgements are ticks inside it.
    function startFinalize() {
        if (!invoiceVM.canFinalize)
            return
        staleCheck.checked = false
        deadCashCheck.checked = false
        confirmDialog.open()
    }
    // Prototype-style deletion: click a row, then Delete, or right-click → حذف.
    function removeRow(list, row) {
        if (row < 0 || row >= list.count)
            return
        if (list === saleList)
            invoiceVM.removeSaleLine(row)
        else
            invoiceVM.removeOldGoldLine(row)
        list.currentIndex = Math.min(row, list.count - 1)
    }
    function selectRow(list, row) {
        list.currentIndex = row
        list.forceActiveFocus()
    }
    function startNewInvoice() {
        invoiceVM.newInvoice()
        customerPicker.clear()
        barcodeField.text = ""
        oldGoldWeight.text = ""
        focusBarcode()
    }

    Menu {
        id: rowMenu
        objectName: "rowMenu"
        property var list: null
        property int row: -1
        MenuItem {
            objectName: "rowMenuDelete"
            text: "حذف"
            font.family: "Cairo"
            onTriggered: invoicePage.removeRow(rowMenu.list, rowMenu.row)
        }
    }

    Connections {
        target: invoiceVM
        function onFinalized(invoiceNumber) {
            customerPicker.clear()
            oldGoldWeight.text = ""
            // Sold items and bought old gold change other pages, as V1 refreshed them.
            if (typeof inventoryVM !== "undefined" && inventoryVM)
                inventoryVM.refresh()
            if (typeof oldGoldConversionVM !== "undefined" && oldGoldConversionVM)
                oldGoldConversionVM.refreshBalances()
            invoicePage.focusBarcode()
        }
    }

    // ---------------------------------------------------- keyboard (locked map)
    // F3 بحث عن قطعة • F4 العميل • F5 إدخال يدوي (later) • F6 الكسر • F7 بيع من الكسر (later)
    // F8 المبلغ بالكامل • F9 / Ctrl+S حفظ • F2 فاتورة جديدة • Ctrl+M المصنعية • Alt+1/2/3 العيار
    Shortcut { sequence: "F2"; enabled: invoicePage.keysActive; onActivated: invoicePage.startNewInvoice() }
    Shortcut {
        sequence: "F3"
        enabled: invoicePage.visible && !confirmDialog.opened
        onActivated: invoicePage.openItemSearch()
    }
    Shortcut { sequence: "Esc"; enabled: searchPanel.visible; onActivated: invoicePage.closeItemSearch() }
    Shortcut { sequence: "F4"; enabled: invoicePage.keysActive; onActivated: customerPicker.open() }
    Shortcut { sequence: "F6"; enabled: invoicePage.keysActive; onActivated: oldGoldWeight.forceActiveFocus() }
    Shortcut { sequence: "Ctrl+M"; enabled: invoicePage.keysActive; onActivated: makingField.forceActiveFocus() }
    Shortcut { sequence: "F8"; enabled: invoicePage.keysActive; onActivated: invoiceVM.payFullAmount() }
    Shortcut { sequences: ["F9", "Ctrl+S"]; enabled: invoicePage.keysActive; onActivated: invoicePage.startFinalize() }
    Shortcut { sequence: "Alt+1"; enabled: invoicePage.keysActive; onActivated: invoicePage.chooseKarat("18") }
    Shortcut { sequence: "Alt+2"; enabled: invoicePage.keysActive; onActivated: invoicePage.chooseKarat("21") }
    Shortcut { sequence: "Alt+3"; enabled: invoicePage.keysActive; onActivated: invoicePage.chooseKarat("24") }

    Rectangle {
        anchors.fill: parent
        color: theme.bgPrimary
    }

    // ---------------------------------------------------- components
    // A prototype strip: flat, dark, with a thin coloured accent on the physical right.
    component Strip: Rectangle {
        id: strip
        property color accent: theme.border
        color: theme.bgSecondary
        radius: 4
        Rectangle {
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 3
            radius: 2
            color: strip.accent
        }
    }

    component FieldLabel: Label {
        color: theme.textMuted
        font.family: "Cairo"
        font.pixelSize: 11
        horizontalAlignment: Text.AlignRight
    }

    component AmountField: TextField {
        Layout.preferredHeight: 32
        topPadding: 4
        bottomPadding: 4
        color: theme.textPrimary
        placeholderTextColor: theme.textDisabled
        font.family: "Cairo"
        font.pixelSize: 13
        horizontalAlignment: TextInput.AlignRight
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        selectByMouse: true
        onActiveFocusChanged: if (activeFocus) selectAll()
    }

    component PrimaryButton: Button {
        id: primary
        font.family: "Cairo"
        font.bold: true
        contentItem: Label {
            text: primary.text
            font: primary.font
            color: primary.enabled ? theme.bgPrimary : theme.textDisabled
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            implicitHeight: 38
            radius: theme.radiusSmall
            color: !primary.enabled ? theme.bgTertiary : primary.down ? theme.goldDark : theme.gold
            border.color: primary.enabled ? theme.goldBright : theme.border
            border.width: 1
        }
    }

    component SmallButton: Button {
        id: small
        font.family: "Cairo"
        font.pixelSize: 12
        focusPolicy: Qt.TabFocus
        leftPadding: 10
        rightPadding: 10
        contentItem: Label {
            text: small.text
            font: small.font
            color: small.enabled ? theme.textPrimary : theme.textDisabled
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            implicitHeight: 32
            implicitWidth: 52
            radius: theme.radiusSmall
            color: small.down ? theme.panelHover : theme.bgTertiary
            border.color: small.enabled ? theme.goldDark : theme.border
            border.width: 1
        }
    }

    component RemoveButton: ToolButton {
        id: removeButton
        text: "حذف"
        font.family: "Cairo"
        font.pixelSize: 11
        implicitHeight: 26
        focusPolicy: Qt.NoFocus
        contentItem: Label {
            text: removeButton.text
            font: removeButton.font
            color: theme.red
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    component Cell: Label {
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        color: theme.textPrimary
        font.family: "Cairo"
        font.pixelSize: 12
        horizontalAlignment: Text.AlignRight
        elide: Text.ElideLeft
    }

    component HeadCell: Cell {
        color: theme.textMuted
        font.pixelSize: 10
    }

    component KaratChip: Button {
        id: chip
        property bool selected: false
        property color selectedColor: theme.priceBuy
        Layout.preferredWidth: 36
        Layout.preferredHeight: 30
        focusPolicy: Qt.NoFocus
        font.family: "Cairo"
        font.pixelSize: 12
        font.bold: selected
        contentItem: Label {
            text: chip.text
            font: chip.font
            color: chip.selected ? theme.bgPrimary : theme.textSecondary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: theme.radiusSmall
            color: chip.selected ? chip.selectedColor : theme.bgTertiary
            border.color: chip.selected ? chip.selectedColor : theme.border
        }
    }

    // The prototype's مباع / مشترى strip: grams per karat, dim when zero.
    component WeightStrip: RowLayout {
        id: weights
        property string title: ""
        property var totals: ({})
        property color accent: theme.textPrimary
        layoutDirection: Qt.RightToLeft
        spacing: 12
        Label {
            text: weights.title
            color: theme.textMuted
            font.family: "Cairo"
            font.pixelSize: 11
        }
        Repeater {
            model: ["21", "18", "24"]
            Label {
                objectName: weights.objectName + "_" + modelData
                text: modelData + ": " + (weights.totals[modelData] || "—")
                color: (weights.totals[modelData] || "—") === "—" ? theme.textDisabled : weights.accent
                font.family: "Cairo"
                font.pixelSize: 12
                font.bold: (weights.totals[modelData] || "—") !== "—"
            }
        }
        Item { Layout.fillWidth: true }
    }

    // ---------------------------------------------------- page
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 6

        // ---------------- header strip: salesperson • customer • previous balance • prices
        Strip {
            objectName: "invoiceHeaderStrip"
            Layout.fillWidth: true
            Layout.preferredHeight: 44
            accent: theme.blue

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 8
                anchors.rightMargin: 12
                layoutDirection: Qt.RightToLeft
                spacing: 6

                FieldLabel { text: "البائع" }
                ComboBox {
                    id: salespersonCombo
                    objectName: "invoiceSalesperson"
                    Layout.preferredWidth: invoicePage.compact ? 118 : 150
                    Layout.preferredHeight: 34
                    font.family: "Cairo"
                    font.pixelSize: 12
                    model: invoiceVM.salespeople
                    textRole: "name"
                    valueRole: "employeeId"
                    displayText: currentIndex < 0 ? "اختر البائع" : currentText
                    currentIndex: {
                        var rows = invoiceVM.salespeople
                        for (var i = 0; i < rows.length; ++i)
                            if (rows[i].employeeId === invoiceVM.salespersonId)
                                return i
                        return -1
                    }
                    onActivated: function(index) {
                        invoiceVM.setSalesperson(invoiceVM.salespeople[index].employeeId)
                    }
                }

                FieldLabel { text: "العميل F4" }
                CustomerPicker {
                    id: customerPicker
                    objectName: "invoiceCustomerPicker"
                    Layout.fillWidth: true
                    Layout.maximumWidth: 300
                    Layout.minimumWidth: 160
                    Layout.preferredHeight: 34
                    placeholderText: "بدون عميل (نقدي)"
                    onCustomerSelected: function(customerId) { invoiceVM.setCustomer(customerId) }
                    onSelectionCleared: invoiceVM.setCustomer("")
                    onSelectionInvalidated: function(customerId, reason) { invoiceVM.setCustomer("") }
                }

                ColumnLayout {
                    spacing: 0
                    visible: invoiceVM.customerId.length > 0
                    FieldLabel { text: "رصيد سابق" ; font.pixelSize: 10 }
                    Label {
                        objectName: "customerBalance"
                        text: invoiceVM.customerBalanceText || "…"
                        color: theme.textPrimary
                        font.family: "Cairo"
                        font.pixelSize: 12
                        font.bold: true
                    }
                }
                Rectangle {
                    objectName: "deadCashBanner"
                    visible: invoiceVM.deadCashWarning
                    Layout.preferredHeight: 24
                    Layout.preferredWidth: deadCashText.implicitWidth + 12
                    color: theme.redBackground
                    radius: 4
                    border.color: theme.red
                    Label {
                        id: deadCashText
                        anchors.centerIn: parent
                        text: "ديون معدومة سابقة"
                        color: theme.red
                        font.family: "Cairo"
                        font.pixelSize: 11
                        font.bold: true
                    }
                }
                Item { Layout.fillWidth: true }

                // Prices that apply to each side: sale at the shop's SELL price (gold),
                // old gold at the shop's BUY price (teal).
                GridLayout {
                    objectName: "invoicePriceBar"
                    columns: invoiceVM.priceRows.length + 1
                    rowSpacing: 0
                    columnSpacing: 10
                    layoutDirection: Qt.RightToLeft
                    Label {
                        text: "بيع"
                        color: theme.priceSell
                        font.family: "Cairo"
                        font.pixelSize: 10
                    }
                    Repeater {
                        model: invoiceVM.priceRows
                        Label {
                            objectName: "priceSell_" + modelData.karat
                            text: modelData.karat + ": " + modelData.sellShort
                            color: theme.priceSell
                            font.family: "Cairo"
                            font.pixelSize: 11
                            font.bold: modelData.karat === "21"
                        }
                    }
                    Label {
                        text: "شراء"
                        color: theme.priceBuy
                        font.family: "Cairo"
                        font.pixelSize: 10
                    }
                    Repeater {
                        model: invoiceVM.priceRows
                        Label {
                            objectName: "priceBuy_" + modelData.karat
                            text: modelData.karat + ": " + modelData.buyShort
                            color: theme.priceBuy
                            font.family: "Cairo"
                            font.pixelSize: 11
                            font.bold: modelData.karat === "21"
                        }
                    }
                }
                Label {
                    objectName: "invoicePriceStatus"
                    visible: text.length > 0
                    text: invoiceVM.priceStatusText
                    color: theme.priceWarning
                    font.family: "Cairo"
                    font.pixelSize: 11
                    font.bold: true
                }
            }
        }

        // ---------------- one-line messages (only when present)
        Rectangle {
            objectName: "invoiceSuccessBanner"
            Layout.fillWidth: true
            Layout.preferredHeight: 28
            visible: invoiceVM.hasSuccess
            color: theme.bgSecondary
            radius: 4
            border.color: theme.green
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 6
                anchors.rightMargin: 10
                layoutDirection: Qt.RightToLeft
                spacing: 12
                Label {
                    objectName: "successInvoiceNumber"
                    text: "تم حفظ الفاتورة رقم " + (invoiceVM.success.invoiceNumber || "")
                    color: theme.green
                    font.family: "Cairo"
                    font.pixelSize: 13
                    font.bold: true
                }
                Label {
                    Layout.fillWidth: true
                    text: (invoiceVM.success.netText || "") + " • " + (invoiceVM.success.residualText || "")
                    color: theme.textSecondary
                    font.family: "Cairo"
                    font.pixelSize: 12
                    horizontalAlignment: Text.AlignRight
                    elide: Text.ElideLeft
                }
                RemoveButton {
                    objectName: "successDismiss"
                    text: "إغلاق"
                    onClicked: invoiceVM.dismissSuccess()
                }
            }
        }

        Rectangle {
            objectName: "invoiceErrorBanner"
            Layout.fillWidth: true
            Layout.preferredHeight: 28
            visible: invoiceVM.errorMessage.length > 0
            color: theme.redBackground
            radius: 4
            border.color: theme.red
            RowLayout {
                anchors.fill: parent
                anchors.margins: 3
                anchors.rightMargin: 10
                layoutDirection: Qt.RightToLeft
                Label {
                    objectName: "invoiceErrorText"
                    Layout.fillWidth: true
                    text: invoiceVM.errorMessage
                    color: theme.red
                    font.family: "Cairo"
                    font.pixelSize: 12
                    horizontalAlignment: Text.AlignRight
                    elide: Text.ElideLeft
                }
                SmallButton {
                    objectName: "barcodeNotFoundSearch"
                    visible: invoiceVM.barcodeNotFound
                    Layout.preferredHeight: 22
                    text: "بحث عن قطعة F3"
                    onClicked: { invoiceVM.clearError(); invoicePage.openItemSearch() }
                }
                RemoveButton {
                    text: "إغلاق"
                    onClicked: invoiceVM.clearError()
                }
            }
        }

        // ---------------- body: sales (right) and old gold (left), side by side
        RowLayout {
            id: middleRow
            Layout.fillWidth: true
            Layout.fillHeight: true
            layoutDirection: Qt.RightToLeft
            spacing: 6

            // ======== sales
            Item {
                id: salePane
                objectName: "salePanel"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 3

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 4

                    // Entry row: the barcode field is the page's home.
                    // (F5 إدخال يدوي and F7 بيع من الكسر will sit beside بحث.)
                    Strip {
                        id: saleEntry
                        Layout.fillWidth: true
                        Layout.preferredHeight: 40
                        accent: theme.priceSell

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 6
                            anchors.rightMargin: 10
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            Label {
                                text: "بيع"
                                color: theme.priceSell
                                font.family: "Cairo"
                                font.pixelSize: 13
                                font.bold: true
                            }
                            TextField {
                                id: barcodeField
                                objectName: "invoiceBarcodeField"
                                Layout.fillWidth: true
                                Layout.maximumWidth: 260
                                Layout.minimumWidth: 120
                                Layout.preferredHeight: 32
                                topPadding: 4
                                bottomPadding: 4
                                placeholderText: activeFocus || text.length > 0 ? "" : "الباركود ثم Enter"
                                color: theme.textPrimary
                                placeholderTextColor: theme.textDisabled
                                font.family: "Cairo"
                                font.pixelSize: 13
                                horizontalAlignment: TextInput.AlignRight
                                selectByMouse: true
                                onAccepted: invoicePage.barcodeEnter()
                                background: Rectangle {
                                    radius: 4
                                    color: theme.bgPrimary
                                    border.color: barcodeField.activeFocus ? theme.gold : theme.border
                                    border.width: barcodeField.activeFocus ? 2 : 1
                                }
                            }
                            SmallButton {
                                objectName: "invoiceAddBarcode"
                                text: "إضافة"
                                enabled: barcodeField.text.length > 0
                                onClicked: invoicePage.addBarcode()
                            }
                            SmallButton {
                                objectName: "openItemSearch"
                                text: "بحث F3"
                                onClicked: invoicePage.openItemSearch()
                            }
                            Item { Layout.fillWidth: true }
                            Label {
                                text: invoiceVM.saleCount > 0 ? invoiceVM.saleCount + " قطعة" : ""
                                color: theme.textMuted
                                font.family: "Cairo"
                                font.pixelSize: 11
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        color: theme.bgSecondary
                        radius: 4

                        Rectangle {
                            anchors.top: parent.top
                            width: parent.width
                            height: 2
                            color: theme.priceSell
                            opacity: 0.7
                        }

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 4
                            anchors.topMargin: 6
                            spacing: 2

                            RowLayout {
                                Layout.fillWidth: true
                                Layout.rightMargin: 6
                                Layout.leftMargin: 40
                                layoutDirection: Qt.RightToLeft
                                spacing: 6
                                HeadCell { text: "الصنف"; Layout.preferredWidth: 4 }
                                HeadCell { text: "الباركود"; Layout.preferredWidth: 2 }
                                HeadCell { text: "العيار" }
                                HeadCell { text: "الوزن"; Layout.preferredWidth: 2 }
                                HeadCell { text: "مصنعية/جم"; Layout.preferredWidth: 2 }
                                HeadCell { text: "الإجمالي"; Layout.preferredWidth: 3 }
                            }

                            ListView {
                                id: saleList
                                objectName: "saleList"
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                clip: true
                                spacing: 2
                                model: invoiceVM.saleLinesModel
                                boundsBehavior: Flickable.StopAtBounds
                                ScrollBar.vertical: ScrollBar {}
                                onCountChanged: positionViewAtEnd()
                                currentIndex: -1
                                activeFocusOnTab: true
                                highlightFollowsCurrentItem: false
                                Keys.onDeletePressed: invoicePage.removeRow(saleList, currentIndex)

                                delegate: Rectangle {
                                    objectName: "saleRow_" + index
                                    readonly property bool selected: ListView.isCurrentItem && saleList.activeFocus
                                    width: saleList.width
                                    height: 32
                                    color: selected ? theme.panelHover : (index % 2 ? theme.bgSecondary : theme.bgTertiary)
                                    border.color: selected ? theme.goldDark : "transparent"
                                    radius: 3

                                    // Row selection (Delete removes it) and right-click → حذف.
                                    MouseArea {
                                        anchors.fill: parent
                                        acceptedButtons: Qt.LeftButton | Qt.RightButton
                                        onPressed: function(mouse) {
                                            invoicePage.selectRow(saleList, index)
                                            if (mouse.button === Qt.RightButton) {
                                                rowMenu.list = saleList
                                                rowMenu.row = index
                                                rowMenu.popup()
                                            }
                                        }
                                    }

                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.rightMargin: 6
                                        anchors.leftMargin: 2
                                        layoutDirection: Qt.RightToLeft
                                        spacing: 6
                                        Cell {
                                            Layout.preferredWidth: 4
                                            text: model.itemName + (model.factory ? " • " + model.factory : "")
                                        }
                                        Cell {
                                            Layout.preferredWidth: 2
                                            text: model.barcode || "—"
                                            color: theme.textMuted
                                            font.pixelSize: 11
                                        }
                                        Cell { text: model.karat }
                                        Cell { Layout.preferredWidth: 2; text: model.weight; font.bold: true }
                                        AmountField {
                                            objectName: "saleMaking_" + index
                                            Layout.fillWidth: true
                                            Layout.preferredWidth: 2
                                            Layout.preferredHeight: 26
                                            topPadding: 2
                                            bottomPadding: 2
                                            font.pixelSize: 12
                                            // Empty = the invoice making; typing overrides this line only.
                                            placeholderText: text.length > 0 ? ""
                                                             : (invoiceVM.invoiceMaking.length > 0 ? invoiceVM.invoiceMaking : "—")
                                            text: model.makingValue
                                            color: model.makingOverride ? theme.amber : theme.textPrimary
                                            onTextEdited: invoiceVM.setSaleMaking(index, "PER_GRAM", text)
                                            onAccepted: invoicePage.focusBarcode()
                                            background: Rectangle {
                                                radius: 3
                                                color: "transparent"
                                                border.color: parent.activeFocus ? theme.gold : theme.border
                                            }
                                        }
                                        Cell {
                                            objectName: "saleLineTotal_" + index
                                            Layout.preferredWidth: 3
                                            text: model.lineTotal
                                            color: theme.priceSell
                                            font.bold: true
                                        }
                                        RemoveButton {
                                            objectName: "removeSale_" + index
                                            Layout.preferredWidth: 34
                                            onClicked: invoiceVM.removeSaleLine(index)
                                        }
                                    }
                                }

                                Label {
                                    anchors.centerIn: parent
                                    visible: saleList.count === 0
                                    text: "امسح الباركود — أو F3 للبحث عن قطعة"
                                    color: theme.textDisabled
                                    font.family: "Cairo"
                                    font.pixelSize: 12
                                }
                            }

                            WeightStrip {
                                objectName: "soldWeights"
                                Layout.fillWidth: true
                                Layout.rightMargin: 6
                                title: "مباع (جم)"
                                totals: invoiceVM.weightTotals.sold
                                accent: theme.priceSell
                            }
                        }
                    }
                }

                // ---- بحث عن قطعة: floating over the sales table only (layout underneath is unchanged)
                Rectangle {
                    id: searchPanel
                    objectName: "itemSearchPanel"
                    property int karatId: -1
                    property int chosenIndex: -1
                    property bool filtersOpen: false
                    readonly property int activeFilters: (karatId > 0 ? 1 : 0) + (searchWeight.text.length > 0 ? 1 : 0)
                                                         + (searchSize.text.length > 0 ? 1 : 0)
                    visible: false
                    z: 20
                    anchors.top: parent.top
                    anchors.topMargin: saleEntry.height + 4
                    anchors.right: parent.right
                    anchors.left: parent.left
                    anchors.leftMargin: Math.max(0, parent.width - 560)
                    height: Math.min(parent.height - saleEntry.height - 4, 360)
                    color: theme.bgPrimary
                    radius: 6
                    border.color: theme.gold
                    border.width: 1

                    Connections {
                        target: invoiceVM
                        function onChanged() {
                            if (searchPanel.chosenIndex >= invoiceVM.searchResults.length)
                                searchPanel.chosenIndex = -1
                        }
                    }
                    // Results follow typing; nothing is ever chosen automatically.
                    Timer {
                        id: searchDebounce
                        interval: 250
                        onTriggered: invoicePage.runItemSearch()
                    }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 4

                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            TextField {
                                id: searchText
                                objectName: "itemSearchText"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 32
                                topPadding: 4
                                bottomPadding: 4
                                placeholderText: text.length > 0 ? "" : "ابحث باسم الصنف / المصنع / الباركود"
                                color: theme.textPrimary
                                placeholderTextColor: theme.textDisabled
                                font.family: "Cairo"
                                font.pixelSize: 13
                                horizontalAlignment: TextInput.AlignRight
                                selectByMouse: true
                                onTextEdited: searchDebounce.restart()
                                onAccepted: searchPanel.chosenIndex >= 0 ? invoicePage.confirmSearchedItem()
                                                                         : invoicePage.runItemSearch()
                                Keys.onDownPressed: {
                                    if (searchResultsList.count > 0) {
                                        searchPanel.chosenIndex = 0
                                        searchResultsList.forceActiveFocus()
                                    }
                                }
                            }
                            SmallButton {
                                objectName: "itemSearchFilters"
                                text: searchPanel.activeFilters > 0 ? "فلاتر (" + searchPanel.activeFilters + ")" : "فلاتر"
                                onClicked: searchPanel.filtersOpen = !searchPanel.filtersOpen
                            }
                            RemoveButton {
                                objectName: "itemSearchClose"
                                text: "إغلاق Esc"
                                onClicked: invoicePage.closeItemSearch()
                            }
                        }

                        // Optional filters, shown on demand (or while one is in use).
                        RowLayout {
                            objectName: "itemSearchFilterRow"
                            Layout.fillWidth: true
                            visible: searchPanel.filtersOpen || searchPanel.activeFilters > 0
                            layoutDirection: Qt.RightToLeft
                            spacing: 4
                            Repeater {
                                model: [{ "karatId": -1, "karat": "الكل" }].concat(invoiceVM.priceRows)
                                delegate: KaratChip {
                                    objectName: "searchKarat_" + modelData.karat
                                    Layout.preferredWidth: modelData.karatId < 0 ? 40 : 34
                                    text: modelData.karat
                                    selectedColor: theme.gold
                                    selected: searchPanel.karatId === modelData.karatId
                                    onClicked: { searchPanel.karatId = modelData.karatId; invoicePage.runItemSearch() }
                                }
                            }
                            FieldLabel { text: "الوزن"; Layout.leftMargin: 4 }
                            AmountField {
                                id: searchWeight
                                objectName: "itemSearchWeight"
                                Layout.preferredWidth: 64
                                placeholderText: ""
                                onTextEdited: searchDebounce.restart()
                                onAccepted: invoicePage.runItemSearch()
                            }
                            FieldLabel { text: "±" }
                            AmountField {
                                id: searchTolerance
                                objectName: "itemSearchTolerance"
                                Layout.preferredWidth: 46
                                placeholderText: ""
                                text: "0.05"
                                onTextEdited: searchDebounce.restart()
                                onAccepted: invoicePage.runItemSearch()
                            }
                            FieldLabel { text: "مقاس" }
                            AmountField {
                                id: searchSize
                                objectName: "itemSearchSize"
                                Layout.preferredWidth: 44
                                placeholderText: ""
                                inputMethodHints: Qt.ImhNone
                                onTextEdited: searchDebounce.restart()
                                onAccepted: invoicePage.runItemSearch()
                            }
                            Item { Layout.fillWidth: true }
                        }

                        Label {
                            objectName: "itemSearchMessage"
                            Layout.fillWidth: true
                            visible: text.length > 0
                            text: invoiceVM.searchBusy ? "جارٍ البحث…" : invoiceVM.searchMessage
                            color: theme.textMuted
                            font.family: "Cairo"
                            font.pixelSize: 11
                            horizontalAlignment: Text.AlignRight
                            elide: Text.ElideLeft
                        }

                        ListView {
                            id: searchResultsList
                            objectName: "itemSearchResults"
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            spacing: 2
                            model: invoiceVM.searchResults
                            boundsBehavior: Flickable.StopAtBounds
                            ScrollBar.vertical: ScrollBar {}
                            activeFocusOnTab: true
                            // Nothing is chosen until the employee clicks a row or moves to it with the arrows.
                            onCountChanged: searchPanel.chosenIndex = -1
                            Keys.onDownPressed: {
                                searchPanel.chosenIndex = Math.min(count - 1, searchPanel.chosenIndex + 1)
                                positionViewAtIndex(searchPanel.chosenIndex, ListView.Contain)
                            }
                            Keys.onUpPressed: {
                                if (searchPanel.chosenIndex <= 0) {
                                    searchPanel.chosenIndex = -1
                                    searchText.forceActiveFocus()
                                    return
                                }
                                searchPanel.chosenIndex -= 1
                                positionViewAtIndex(searchPanel.chosenIndex, ListView.Contain)
                            }
                            Keys.onReturnPressed: invoicePage.confirmSearchedItem()
                            Keys.onEnterPressed: invoicePage.confirmSearchedItem()

                            // Type • weight • karat stand out; factory/size next; barcode last and quiet.
                            delegate: Rectangle {
                                objectName: "itemSearchRow_" + index
                                readonly property bool chosen: searchPanel.chosenIndex === index
                                width: searchResultsList.width
                                height: 36
                                radius: 4
                                color: chosen ? theme.panelHover : (index % 2 ? theme.bgSecondary : theme.bgTertiary)
                                border.color: chosen ? theme.gold : "transparent"
                                border.width: chosen ? 2 : 0

                                MouseArea {
                                    anchors.fill: parent
                                    onClicked: {
                                        searchPanel.chosenIndex = index
                                        searchResultsList.forceActiveFocus()
                                    }
                                    onDoubleClicked: {
                                        searchPanel.chosenIndex = index
                                        invoicePage.confirmSearchedItem()
                                    }
                                }
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 8
                                    anchors.rightMargin: 8
                                    layoutDirection: Qt.RightToLeft
                                    spacing: 8
                                    Cell {
                                        objectName: "itemSearchName_" + index
                                        Layout.preferredWidth: 3
                                        text: modelData.itemName
                                        font.pixelSize: 13
                                        font.bold: true
                                    }
                                    Cell {
                                        Layout.preferredWidth: 2
                                        text: modelData.weightText
                                        font.pixelSize: 13
                                        font.bold: true
                                        color: theme.goldBright
                                    }
                                    Cell {
                                        Layout.preferredWidth: 2
                                        text: modelData.karatText
                                    }
                                    Cell {
                                        Layout.preferredWidth: 3
                                        text: modelData.detailText
                                        color: theme.textSecondary
                                        font.pixelSize: 11
                                    }
                                    Cell {
                                        Layout.preferredWidth: 2
                                        text: modelData.barcodeText
                                        color: theme.textDisabled
                                        font.pixelSize: 10
                                    }
                                }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 8
                            PrimaryButton {
                                objectName: "itemSearchConfirm"
                                Layout.preferredWidth: 150
                                Layout.preferredHeight: 32
                                text: "اختيار هذه القطعة"
                                enabled: searchPanel.chosenIndex >= 0
                                onClicked: invoicePage.confirmSearchedItem()
                            }
                            Label {
                                Layout.fillWidth: true
                                text: searchPanel.chosenIndex >= 0 ? "تأكد أنها القطعة التي أمامك"
                                                                   : "اضغط على القطعة التي أمامك أو استخدم الأسهم"
                                color: theme.textMuted
                                font.family: "Cairo"
                                font.pixelSize: 11
                                horizontalAlignment: Text.AlignRight
                                elide: Text.ElideLeft
                            }
                        }
                    }
                }
            }

            // ======== old gold from the customer
            Item {
                objectName: "oldGoldPanel"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 2

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 4

                    Strip {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 40
                        accent: theme.priceBuy

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 6
                            anchors.rightMargin: 10
                            layoutDirection: Qt.RightToLeft
                            spacing: 4
                            Label {
                                text: "كسر F6"
                                color: theme.priceBuy
                                font.family: "Cairo"
                                font.pixelSize: 13
                                font.bold: true
                                Layout.leftMargin: 4
                            }
                            // One click (or Alt+1/2/3) per karat; no karat is ever preselected.
                            Repeater {
                                model: invoiceVM.priceRows
                                delegate: KaratChip {
                                    objectName: "karatChip_" + modelData.karat
                                    text: modelData.karat
                                    selected: invoiceVM.oldGoldKaratId === modelData.karatId
                                    onClicked: invoicePage.chooseKarat(modelData.karat)
                                }
                            }
                            AmountField {
                                id: oldGoldWeight
                                objectName: "oldGoldWeight"
                                Layout.fillWidth: true
                                Layout.minimumWidth: 70
                                placeholderText: text.length > 0 ? ""
                                                 : (invoiceVM.oldGoldKaratId < 0 ? "العيار أولاً" : "الوزن ثم Enter")
                                onAccepted: invoicePage.addOldGold()
                            }
                            SmallButton {
                                objectName: "addOldGold"
                                text: "إضافة"
                                enabled: invoiceVM.oldGoldKaratId >= 0 && oldGoldWeight.text.length > 0
                                onClicked: invoicePage.addOldGold()
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        color: theme.bgSecondary
                        radius: 4

                        Rectangle {
                            anchors.top: parent.top
                            width: parent.width
                            height: 2
                            color: theme.priceBuy
                            opacity: 0.7
                        }

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 4
                            anchors.topMargin: 6
                            spacing: 2

                            RowLayout {
                                Layout.fillWidth: true
                                Layout.rightMargin: 6
                                Layout.leftMargin: 40
                                layoutDirection: Qt.RightToLeft
                                spacing: 6
                                HeadCell { text: "العيار" }
                                HeadCell { text: "الوزن"; Layout.preferredWidth: 2 }
                                HeadCell { text: "القيمة"; Layout.preferredWidth: 3 }
                            }

                            ListView {
                                id: oldGoldList
                                objectName: "oldGoldList"
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                clip: true
                                spacing: 2
                                model: invoiceVM.oldGoldLinesModel
                                boundsBehavior: Flickable.StopAtBounds
                                ScrollBar.vertical: ScrollBar {}
                                onCountChanged: positionViewAtEnd()
                                currentIndex: -1
                                activeFocusOnTab: true
                                highlightFollowsCurrentItem: false
                                Keys.onDeletePressed: invoicePage.removeRow(oldGoldList, currentIndex)

                                delegate: Rectangle {
                                    objectName: "oldGoldRow_" + index
                                    readonly property bool selected: ListView.isCurrentItem && oldGoldList.activeFocus
                                    width: oldGoldList.width
                                    height: 32
                                    color: selected ? theme.panelHover : (index % 2 ? theme.bgSecondary : theme.bgTertiary)
                                    border.color: selected ? theme.goldDark : "transparent"
                                    radius: 3

                                    MouseArea {
                                        anchors.fill: parent
                                        acceptedButtons: Qt.LeftButton | Qt.RightButton
                                        onPressed: function(mouse) {
                                            invoicePage.selectRow(oldGoldList, index)
                                            if (mouse.button === Qt.RightButton) {
                                                rowMenu.list = oldGoldList
                                                rowMenu.row = index
                                                rowMenu.popup()
                                            }
                                        }
                                    }

                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.rightMargin: 6
                                        anchors.leftMargin: 2
                                        layoutDirection: Qt.RightToLeft
                                        spacing: 6
                                        Cell { text: model.karat }
                                        Cell { Layout.preferredWidth: 2; text: model.weight + " جم"; font.bold: true }
                                        Cell {
                                            objectName: "oldGoldLineTotal_" + index
                                            Layout.preferredWidth: 3
                                            text: model.lineTotal
                                            color: theme.priceBuy
                                            font.bold: true
                                        }
                                        RemoveButton {
                                            objectName: "removeOldGold_" + index
                                            Layout.preferredWidth: 34
                                            onClicked: invoiceVM.removeOldGoldLine(index)
                                        }
                                    }
                                }

                                Label {
                                    anchors.centerIn: parent
                                    visible: oldGoldList.count === 0
                                    text: "لا يوجد كسر"
                                    color: theme.textDisabled
                                    font.family: "Cairo"
                                    font.pixelSize: 12
                                }
                            }

                            WeightStrip {
                                objectName: "boughtWeights"
                                Layout.fillWidth: true
                                Layout.rightMargin: 6
                                title: "مشترى (جم)"
                                totals: invoiceVM.weightTotals.bought
                                accent: theme.priceBuy
                            }
                        }
                    }
                }
            }
        }

        Label {
            objectName: "invoiceProblems"
            Layout.fillWidth: true
            visible: text.length > 0
            text: invoiceVM.blockingProblems.length > 0 ? invoiceVM.blockingProblems.join(" • ")
                                                        : invoiceVM.previewError
            color: theme.priceWarning
            font.family: "Cairo"
            font.pixelSize: 12
            horizontalAlignment: Text.AlignRight
            elide: Text.ElideLeft
        }

        // ---------------- money strip (fixed; the operational heart of the invoice)
        // Top row: what the employee types, in typing order, then what remains.
        // Bottom row: the values, the dominant final amount and the save action.
        Strip {
            objectName: "invoiceSummaryBar"
            Layout.fillWidth: true
            Layout.preferredHeight: moneyColumn.implicitHeight + 10
            accent: theme.gold

            ColumnLayout {
                id: moneyColumn
                anchors.fill: parent
                anchors.leftMargin: 8
                anchors.rightMargin: 12
                anchors.topMargin: 5
                anchors.bottomMargin: 5
                spacing: 4

                RowLayout {
                    Layout.fillWidth: true
                    layoutDirection: Qt.RightToLeft
                    spacing: 6

                    FieldLabel { text: "مصنعية/جم" }
                    AmountField {
                        id: makingField
                        objectName: "invoiceMakingField"
                        Layout.preferredWidth: invoicePage.compact ? 66 : 80
                        placeholderText: ""
                        text: invoiceVM.invoiceMaking
                        onTextEdited: invoiceVM.setInvoiceMaking(text)
                        onAccepted: discountField.forceActiveFocus()
                    }
                    FieldLabel { text: "خصم الكسر %" }
                    AmountField {
                        id: discountField
                        objectName: "oldGoldDiscountField"
                        Layout.preferredWidth: 48
                        placeholderText: ""
                        text: invoiceVM.oldGoldDiscountPercent
                        onTextEdited: invoiceVM.setOldGoldDiscountPercent(text)
                        onAccepted: cashField.forceActiveFocus()
                    }
                    Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 24; color: theme.border }
                    FieldLabel {
                        text: invoiceVM.summary.netDirection === "SHOP_PAYS_CUSTOMER" ? "المدفوع نقداً" : "المستلم نقداً"
                    }
                    AmountField {
                        id: cashField
                        objectName: "cashAmountField"
                        Layout.preferredWidth: invoicePage.compact ? 96 : 116
                        placeholderText: ""
                        font.bold: true
                        text: invoiceVM.cashAmount
                        onTextEdited: invoiceVM.setCashAmount(text)
                        onAccepted: invoicePage.startFinalize()
                    }
                    SmallButton {
                        objectName: "payFullButton"
                        text: "كامل F8"
                        enabled: invoiceVM.previewReady && invoiceVM.summary.dueAmount.length > 0
                        onClicked: { invoiceVM.payFullAmount(); cashField.forceActiveFocus() }
                    }
                    FieldLabel { text: "محفظة" }
                    AmountField {
                        id: walletField
                        objectName: "walletAmountField"
                        Layout.preferredWidth: invoicePage.compact ? 66 : 84
                        placeholderText: ""
                        text: invoiceVM.walletAmount
                        onTextEdited: invoiceVM.setWalletAmount(text)
                        onAccepted: invoicePage.startFinalize()
                    }
                    Item { Layout.fillWidth: true }
                    Label {
                        objectName: "summaryResidualLabel"
                        Layout.fillWidth: true
                        Layout.maximumWidth: implicitWidth
                        text: invoiceVM.summary.remainingLabel
                        elide: Text.ElideLeft
                        color: invoiceVM.summary.residualDirection === "NONE" ? theme.green
                               : invoiceVM.summary.residualDirection === "" ? theme.textMuted : theme.amber
                        font.family: "Cairo"
                        font.pixelSize: 11
                    }
                    Label {
                        objectName: "summaryResidual"
                        visible: text.length > 0
                        text: invoiceVM.summary.remainingAmount
                        color: theme.amber
                        font.family: "Cairo"
                        font.pixelSize: 14
                        font.bold: true
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    layoutDirection: Qt.RightToLeft
                    spacing: 14

                    FieldLabel { text: "البيع" }
                    Label {
                        objectName: "summarySale"
                        text: invoiceVM.summary.saleTotal || "—"
                        color: theme.priceSell
                        font.family: "Cairo"
                        font.pixelSize: 14
                        font.bold: true
                    }
                    FieldLabel { text: "الكسر" }
                    Label {
                        objectName: "summaryOldGold"
                        text: invoiceVM.summary.oldGoldTotal || "—"
                        color: theme.priceBuy
                        font.family: "Cairo"
                        font.pixelSize: 14
                        font.bold: true
                    }
                    Item { Layout.fillWidth: true }
                    // The dominant final amount, right before the save action.
                    RowLayout {
                        objectName: "summaryDueBox"
                        property string title: invoiceVM.summary.dueLabel
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Label {
                            text: parent.title
                            color: theme.textSecondary
                            font.family: "Cairo"
                            font.pixelSize: 12
                        }
                        Label {
                            objectName: "summaryDue"
                            text: invoiceVM.summary.dueAmount || "—"
                            color: invoiceVM.summary.netDirection === "SHOP_PAYS_CUSTOMER" ? theme.priceBuy
                                                                                          : theme.goldBright
                            font.family: "Cairo"
                            font.pixelSize: invoicePage.compact ? 24 : 26
                            font.bold: true
                        }
                    }
                    PrimaryButton {
                        objectName: "finalizeInvoice"
                        Layout.preferredWidth: invoicePage.compact ? 110 : 140
                        Layout.preferredHeight: 40
                        font.pixelSize: 14
                        text: invoiceVM.finalizing ? "جارٍ الحفظ…" : "حفظ F9"
                        enabled: invoiceVM.canFinalize
                        onClicked: invoicePage.startFinalize()
                    }
                }
            }
        }
    }

    // ---------------------------------------------------- one confirmation panel
    Dialog {
        id: confirmDialog
        objectName: "confirmFinalizeDialog"
        readonly property bool acknowledged: (!invoiceVM.requiresPriceAcknowledgement || staleCheck.checked)
                                             && (!invoiceVM.requiresDeadCashConfirmation || deadCashCheck.checked)
        function confirm() {
            if (!acknowledged || !invoiceVM.canFinalize)
                return
            close()
            invoiceVM.finalizeInvoice(staleCheck.checked, deadCashCheck.checked)
        }
        title: "حفظ الفاتورة"
        modal: true
        anchors.centerIn: parent
        width: Math.min(420, invoicePage.width - 40)
        padding: 14
        font.family: "Cairo"
        onOpened: confirmButton.forceActiveFocus()
        onClosed: invoicePage.focusBarcode()
        // House dialog frame (as goldPriceDialog in Main.qml): gold title, dark card.
        header: Label {
            text: confirmDialog.title
            color: theme.gold
            font.family: "Cairo"
            font.pixelSize: 15
            font.bold: true
            horizontalAlignment: Text.AlignRight
            topPadding: 12
            leftPadding: 14
            rightPadding: 14
        }
        background: Rectangle {
            color: theme.bgSecondary
            border.color: theme.goldDark
            radius: theme.radiusSmall
        }
        contentItem: ColumnLayout {
            spacing: 4
            Repeater {
                model: [["البيع", invoiceVM.summary.saleTotal], ["الكسر", invoiceVM.summary.oldGoldTotal],
                        [invoiceVM.summary.dueLabel, invoiceVM.summary.dueAmount],
                        [invoiceVM.summary.paidLabel, invoiceVM.summary.paidAmount],
                        [invoiceVM.summary.remainingLabel, invoiceVM.summary.remainingAmount]]
                delegate: RowLayout {
                    Layout.fillWidth: true
                    visible: (modelData[1] || "").length > 0 || index === 4
                    layoutDirection: Qt.RightToLeft
                    Label {
                        text: modelData[0]
                        color: theme.textMuted
                        font.family: "Cairo"
                        font.pixelSize: 13
                    }
                    Item { Layout.fillWidth: true }
                    Label {
                        text: modelData[1] || ""
                        color: index === 2 ? theme.goldBright : theme.textPrimary
                        font.family: "Cairo"
                        font.pixelSize: index === 2 ? 17 : 14
                        font.bold: true
                    }
                }
            }
            CheckBox {
                id: staleCheck
                objectName: "staleAcknowledge"
                Layout.fillWidth: true
                visible: invoiceVM.requiresPriceAcknowledgement
                text: "أسعار الذهب غير محدثة — أوافق على الحفظ بهذه الأسعار"
                font.family: "Cairo"
                LayoutMirroring.enabled: true
            }
            CheckBox {
                id: deadCashCheck
                objectName: "deadCashAcknowledge"
                Layout.fillWidth: true
                visible: invoiceVM.requiresDeadCashConfirmation
                text: "العميل عليه ديون معدومة — أؤكد تسجيل المتبقي على حسابه"
                font.family: "Cairo"
                LayoutMirroring.enabled: true
            }
        }
        footer: RowLayout {
            layoutDirection: Qt.RightToLeft
            spacing: 8
            Item { Layout.preferredWidth: 6; Layout.preferredHeight: 52 }
            PrimaryButton {
                id: confirmButton
                objectName: "confirmFinalize"
                text: "حفظ (Enter)"
                Layout.preferredWidth: 120
                enabled: confirmDialog.acknowledged
                onClicked: confirmDialog.confirm()
                Keys.onReturnPressed: confirmDialog.confirm()
                Keys.onEnterPressed: confirmDialog.confirm()
            }
            Button {
                objectName: "confirmBack"
                text: "رجوع Esc"
                font.family: "Cairo"
                flat: true
                onClicked: confirmDialog.close()
            }
            Item { Layout.fillWidth: true }
        }
    }
}
