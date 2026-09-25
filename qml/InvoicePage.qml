import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Billing V2 (canonical invoices) — one-screen salesman page.
// Sale and old-gold tables side by side, money strip always visible.
// Every amount shown comes from invoiceVM, recalculated by InvoiceService;
// this page only forwards employee input.
Item {
    id: invoicePage
    objectName: "invoicePage"

    property string pageTitle: ""
    property bool activated: false
    readonly property bool keysActive: visible && !confirmDialog.opened

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

    function focusBarcode() {
        barcodeField.forceActiveFocus()
        barcodeField.selectAll()
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

    function startNewInvoice() {
        invoiceVM.newInvoice()
        customerPicker.clear()
        barcodeField.text = ""
        oldGoldWeight.text = ""
        focusBarcode()
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

    // ---------------------------------------------------- keyboard
    Shortcut { sequence: "F2"; enabled: invoicePage.keysActive; onActivated: invoicePage.startNewInvoice() }
    Shortcut { sequence: "F4"; enabled: invoicePage.keysActive; onActivated: customerPicker.open() }
    Shortcut { sequence: "F6"; enabled: invoicePage.keysActive; onActivated: oldGoldWeight.forceActiveFocus() }
    // F3 بحث عن قطعة, F5 إدخال يدوي and F7 بيع من الكسر are reserved for later stages.
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
    component Card: Rectangle {
        color: theme.bgSecondary
        radius: theme.radiusSmall
        border.color: theme.border
        border.width: 1
    }

    component FieldLabel: Label {
        color: theme.textMuted
        font.family: "Cairo"
        font.pixelSize: 11
        horizontalAlignment: Text.AlignRight
    }

    component AmountField: TextField {
        Layout.preferredHeight: 36
        color: theme.textPrimary
        placeholderTextColor: theme.textDisabled
        font.family: "Cairo"
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
            implicitHeight: 40
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
        contentItem: Label {
            text: small.text
            font: small.font
            color: small.enabled ? theme.textPrimary : theme.textDisabled
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            implicitHeight: 36
            implicitWidth: 64
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
        implicitHeight: 28
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

    component SummaryValue: ColumnLayout {
        property alias title: titleLabel.text
        property alias value: valueLabel.text
        property alias valueColor: valueLabel.color
        property alias valueObjectName: valueLabel.objectName
        property alias valueSize: valueLabel.font.pixelSize
        spacing: 0
        Label {
            id: titleLabel
            Layout.alignment: Qt.AlignRight
            color: theme.textMuted
            font.family: "Cairo"
            font.pixelSize: 10
        }
        Label {
            id: valueLabel
            Layout.alignment: Qt.AlignRight
            color: theme.textPrimary
            font.family: "Cairo"
            font.pixelSize: 13
            font.bold: true
            text: ""
        }
    }

    // The prototype's مباع / مشترى strip: grams per karat, dim when zero.
    component WeightStrip: RowLayout {
        id: strip
        property string title: ""
        property var totals: ({})
        property color accent: theme.textPrimary
        layoutDirection: Qt.RightToLeft
        spacing: 12
        Label {
            text: strip.title
            color: theme.textMuted
            font.family: "Cairo"
            font.pixelSize: 11
        }
        Repeater {
            model: ["21", "18", "24"]
            Label {
                objectName: strip.objectName + "_" + modelData
                text: modelData + ": " + (strip.totals[modelData] || "—")
                color: (strip.totals[modelData] || "—") === "—" ? theme.textDisabled : strip.accent
                font.family: "Cairo"
                font.pixelSize: 12
                font.bold: (strip.totals[modelData] || "—") !== "—"
            }
        }
        Item { Layout.fillWidth: true }
    }

    // ---------------------------------------------------- page
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        // ---------------- header: title + prices
        RowLayout {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            spacing: 10

            Label {
                text: invoicePage.pageTitle
                color: theme.textPrimary
                font.family: "Cairo"
                font.pixelSize: 18
                font.bold: true
            }
            Item { Layout.fillWidth: true }
            Rectangle {
                objectName: "invoicePriceBar"
                Layout.preferredHeight: 32
                Layout.preferredWidth: priceRow.implicitWidth + 20
                Layout.maximumWidth: invoicePage.width - 180
                clip: true
                color: theme.bgSecondary
                radius: theme.radiusSmall
                border.color: invoiceVM.priceStale ? theme.priceWarning : theme.border
                border.width: 1

                RowLayout {
                    id: priceRow
                    anchors.centerIn: parent
                    layoutDirection: Qt.RightToLeft
                    spacing: 12
                    Repeater {
                        model: invoiceVM.priceRows
                        delegate: RowLayout {
                            layoutDirection: Qt.RightToLeft
                            spacing: 4
                            Label {
                                text: "عيار " + modelData.karat
                                color: theme.textSecondary
                                font.family: "Cairo"
                                font.pixelSize: 11
                            }
                            Label {
                                objectName: "priceBuy_" + modelData.karat
                                text: "شراء " + modelData.buy
                                color: theme.priceBuy
                                font.family: "Cairo"
                                font.pixelSize: 11
                            }
                            Label {
                                objectName: "priceSell_" + modelData.karat
                                text: "بيع " + modelData.sell
                                color: theme.priceSell
                                font.family: "Cairo"
                                font.pixelSize: 11
                            }
                        }
                    }
                    Label {
                        objectName: "invoicePriceStatus"
                        visible: text.length > 0
                        text: invoiceVM.priceStatusText
                        color: invoiceVM.priceStale || invoiceVM.priceRows.length === 0
                               ? theme.priceWarning : theme.textMuted
                        font.family: "Cairo"
                        font.pixelSize: 11
                    }
                }
            }
        }

        // ---------------- salesperson + customer
        Card {
            Layout.fillWidth: true
            Layout.preferredHeight: 52

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                layoutDirection: Qt.RightToLeft
                spacing: 8

                FieldLabel { text: "البائع" }
                ComboBox {
                    id: salespersonCombo
                    objectName: "invoiceSalesperson"
                    Layout.preferredWidth: 150
                    Layout.preferredHeight: 38
                    font.family: "Cairo"
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

                FieldLabel { text: "العميل (F4)" }
                CustomerPicker {
                    id: customerPicker
                    objectName: "invoiceCustomerPicker"
                    Layout.fillWidth: true
                    Layout.maximumWidth: 320
                    Layout.minimumWidth: 180
                    placeholderText: "بدون عميل (نقدي)"
                    onCustomerSelected: function(customerId) { invoiceVM.setCustomer(customerId) }
                    onSelectionCleared: invoiceVM.setCustomer("")
                    onSelectionInvalidated: function(customerId, reason) { invoiceVM.setCustomer("") }
                }

                Label {
                    objectName: "customerBalance"
                    visible: invoiceVM.customerId.length > 0 && text.length > 0
                    text: invoiceVM.customerBalanceText
                    color: theme.textSecondary
                    font.family: "Cairo"
                    font.pixelSize: 12
                }
                Rectangle {
                    objectName: "deadCashBanner"
                    visible: invoiceVM.deadCashWarning
                    Layout.preferredHeight: 28
                    Layout.preferredWidth: deadCashText.implicitWidth + 16
                    color: theme.redBackground
                    radius: theme.radiusSmall
                    border.color: theme.red
                    Label {
                        id: deadCashText
                        anchors.centerIn: parent
                        text: "تحذير: عليه ديون معدومة سابقة"
                        color: theme.red
                        font.family: "Cairo"
                        font.pixelSize: 11
                        font.bold: true
                    }
                }
                Item { Layout.fillWidth: true }
            }
        }

        // ---------------- success banner (replaces the success dialog)
        Rectangle {
            objectName: "invoiceSuccessBanner"
            Layout.fillWidth: true
            Layout.preferredHeight: 34
            visible: invoiceVM.hasSuccess
            color: theme.bgSecondary
            radius: theme.radiusSmall
            border.color: theme.green
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 8
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
            Layout.preferredHeight: 32
            visible: invoiceVM.errorMessage.length > 0
            color: theme.redBackground
            radius: theme.radiusSmall
            border.color: theme.red
            RowLayout {
                anchors.fill: parent
                anchors.margins: 6
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
                RemoveButton {
                    text: "إغلاق"
                    onClicked: invoiceVM.clearError()
                }
            }
        }

        // ---------------- sale + old gold, side by side
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            layoutDirection: Qt.RightToLeft
            spacing: 8

            // ======== sale (right)
            Card {
                objectName: "salePanel"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 3
                border.color: theme.goldDark

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 6

                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 6
                        Label {
                            text: "المبيعات"
                            color: theme.priceSell
                            font.family: "Cairo"
                            font.pixelSize: 14
                            font.bold: true
                        }
                        Label {
                            text: invoiceVM.saleCount + " قطعة"
                            color: theme.textMuted
                            font.family: "Cairo"
                            font.pixelSize: 11
                        }
                        Item { Layout.fillWidth: true }
                        TextField {
                            id: barcodeField
                            objectName: "invoiceBarcodeField"
                            Layout.fillWidth: true
                            Layout.maximumWidth: 230
                            Layout.minimumWidth: 120
                            Layout.preferredHeight: 36
                            placeholderText: "امسح الباركود ثم Enter"
                            color: theme.textPrimary
                            placeholderTextColor: theme.textDisabled
                            font.family: "Cairo"
                            horizontalAlignment: TextInput.AlignRight
                            selectByMouse: true
                            onAccepted: invoicePage.addBarcode()
                        }
                        SmallButton {
                            objectName: "invoiceAddBarcode"
                            text: "إضافة"
                            enabled: barcodeField.text.length > 0
                            onClicked: invoicePage.addBarcode()
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.rightMargin: 6
                        Layout.leftMargin: 44
                        layoutDirection: Qt.RightToLeft
                        spacing: 6
                        HeadCell { text: "الصنف"; Layout.preferredWidth: 3 }
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
                        spacing: 3
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
                            height: 38
                            color: selected ? theme.panelHover : theme.bgTertiary
                            border.color: selected ? theme.goldDark : "transparent"
                            radius: 5

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
                                    Layout.preferredWidth: 3
                                    text: model.itemName + (model.factory ? " • " + model.factory : "")
                                          + "  " + model.barcode
                                }
                                Cell { text: model.karat }
                                Cell { Layout.preferredWidth: 2; text: model.weight }
                                AmountField {
                                    id: lineMaking
                                    objectName: "saleMaking_" + index
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 2
                                    Layout.preferredHeight: 32
                                    font.pixelSize: 12
                                    // Empty = the invoice making; typing overrides this line only.
                                    placeholderText: invoiceVM.invoiceMaking.length > 0 ? invoiceVM.invoiceMaking : "—"
                                    text: model.makingValue
                                    color: model.makingOverride ? theme.amber : theme.textPrimary
                                    onTextEdited: invoiceVM.setSaleMaking(index, "PER_GRAM", text)
                                    onAccepted: invoicePage.focusBarcode()
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
                                    Layout.preferredWidth: 36
                                    onClicked: invoiceVM.removeSaleLine(index)
                                }
                            }
                        }

                        Label {
                            anchors.centerIn: parent
                            visible: saleList.count === 0
                            text: "امسح باركود القطعة لإضافتها"
                            color: theme.textDisabled
                            font.family: "Cairo"
                            font.pixelSize: 12
                        }
                    }

                    WeightStrip {
                        objectName: "soldWeights"
                        Layout.fillWidth: true
                        title: "مباع (جم)"
                        totals: invoiceVM.weightTotals.sold
                        accent: theme.priceSell
                    }
                }
            }

            // ======== old gold bought from the customer (left)
            Card {
                objectName: "oldGoldPanel"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 2

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 6

                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 6
                        Label {
                            text: "كسر من العميل (F6)"
                            color: theme.priceBuy
                            font.family: "Cairo"
                            font.pixelSize: 14
                            font.bold: true
                        }
                        Item { Layout.fillWidth: true }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 4
                        FieldLabel { text: "العيار (Alt+1/2/3)" }
                        // One click per karat; no karat is ever preselected.
                        Repeater {
                            model: invoiceVM.priceRows
                            delegate: Button {
                                id: chip
                                objectName: "karatChip_" + modelData.karat
                                readonly property bool selected: invoiceVM.oldGoldKaratId === modelData.karatId
                                Layout.preferredWidth: 46
                                Layout.preferredHeight: 34
                                focusPolicy: Qt.NoFocus
                                text: modelData.karat
                                font.family: "Cairo"
                                font.bold: selected
                                onClicked: invoicePage.chooseKarat(modelData.karat)
                                contentItem: Label {
                                    text: chip.text
                                    font: chip.font
                                    color: chip.selected ? theme.bgPrimary : theme.textSecondary
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                                background: Rectangle {
                                    radius: theme.radiusSmall
                                    color: chip.selected ? theme.priceBuy : theme.bgTertiary
                                    border.color: chip.selected ? theme.priceBuy : theme.border
                                }
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 4
                        AmountField {
                            id: oldGoldWeight
                            objectName: "oldGoldWeight"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 60
                            placeholderText: invoiceVM.oldGoldKaratId < 0 ? "اختر العيار أولاً" : "الوزن بالجرام ثم Enter"
                            onAccepted: invoicePage.addOldGold()
                        }
                        SmallButton {
                            objectName: "addOldGold"
                            text: "إضافة"
                            enabled: invoiceVM.oldGoldKaratId >= 0 && oldGoldWeight.text.length > 0
                            onClicked: invoicePage.addOldGold()
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.rightMargin: 6
                        Layout.leftMargin: 44
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
                        spacing: 3
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
                            height: 38
                            color: selected ? theme.panelHover : theme.bgTertiary
                            border.color: selected ? theme.goldDark : "transparent"
                            radius: 5

                            // Row selection (Delete removes it) and right-click → حذف.
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
                                Cell { Layout.preferredWidth: 2; text: model.weight + " جم" }
                                Cell {
                                    objectName: "oldGoldLineTotal_" + index
                                    Layout.preferredWidth: 3
                                    text: model.lineTotal
                                    color: theme.priceBuy
                                    font.bold: true
                                }
                                RemoveButton {
                                    objectName: "removeOldGold_" + index
                                    Layout.preferredWidth: 36
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
                        title: "مشترى (جم)"
                        totals: invoiceVM.weightTotals.bought
                        accent: theme.priceBuy
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

        // ---------------- money strip (always visible)
        Card {
            objectName: "invoiceSummaryBar"
            Layout.fillWidth: true
            Layout.preferredHeight: 112
            border.color: theme.goldDark

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 6

                RowLayout {
                    Layout.fillWidth: true
                    layoutDirection: Qt.RightToLeft
                    spacing: 8

                    FieldLabel { text: "المصنعية/جم" }
                    AmountField {
                        id: makingField
                        objectName: "invoiceMakingField"
                        Layout.preferredWidth: 84
                        placeholderText: ""
                        text: invoiceVM.invoiceMaking
                        onTextEdited: invoiceVM.setInvoiceMaking(text)
                        onAccepted: discountField.forceActiveFocus()
                    }
                    FieldLabel { text: "خصم الكسر %" }
                    AmountField {
                        id: discountField
                        objectName: "oldGoldDiscountField"
                        Layout.preferredWidth: 60
                        placeholderText: ""
                        text: invoiceVM.oldGoldDiscountPercent
                        onTextEdited: invoiceVM.setOldGoldDiscountPercent(text)
                        onAccepted: cashField.forceActiveFocus()
                    }
                    Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 30; color: theme.border }
                    SummaryValue {
                        title: "إجمالي البيع"
                        value: invoiceVM.summary.saleTotal || "—"
                        valueColor: theme.priceSell
                        valueObjectName: "summarySale"
                    }
                    SummaryValue {
                        title: "قيمة الكسر"
                        value: invoiceVM.summary.oldGoldTotal || "—"
                        valueColor: theme.priceBuy
                        valueObjectName: "summaryOldGold"
                    }
                    Item { Layout.fillWidth: true }
                    SummaryValue {
                        objectName: "summaryDueBox"
                        title: invoiceVM.summary.dueLabel
                        value: invoiceVM.summary.dueAmount || "—"
                        valueSize: 20
                        valueColor: invoiceVM.summary.netDirection === "SHOP_PAYS_CUSTOMER" ? theme.priceBuy
                                                                                            : theme.goldBright
                        valueObjectName: "summaryDue"
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    layoutDirection: Qt.RightToLeft
                    spacing: 8

                    FieldLabel {
                        text: invoiceVM.summary.netDirection === "SHOP_PAYS_CUSTOMER" ? "المدفوع للعميل نقداً"
                                                                                     : "المستلم نقداً"
                    }
                    AmountField {
                        id: cashField
                        objectName: "cashAmountField"
                        Layout.preferredWidth: 112
                        placeholderText: ""
                        text: invoiceVM.cashAmount
                        onTextEdited: invoiceVM.setCashAmount(text)
                        onAccepted: invoicePage.startFinalize()
                    }
                    SmallButton {
                        objectName: "payFullButton"
                        text: "المبلغ كاملاً F8"
                        enabled: invoiceVM.previewReady && invoiceVM.summary.dueAmount.length > 0
                        onClicked: { invoiceVM.payFullAmount(); cashField.forceActiveFocus() }
                    }
                    FieldLabel { text: "محفظة" }
                    AmountField {
                        id: walletField
                        objectName: "walletAmountField"
                        Layout.preferredWidth: 84
                        placeholderText: ""
                        text: invoiceVM.walletAmount
                        onTextEdited: invoiceVM.setWalletAmount(text)
                        onAccepted: invoicePage.startFinalize()
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        Label {
                            objectName: "summaryResidualLabel"
                            Layout.fillWidth: true
                            text: invoiceVM.summary.remainingLabel
                            color: invoiceVM.summary.residualDirection === "NONE" ? theme.green
                                   : invoiceVM.summary.residualDirection === "" ? theme.textMuted : theme.amber
                            font.family: "Cairo"
                            font.pixelSize: 11
                            horizontalAlignment: Text.AlignRight
                            elide: Text.ElideLeft
                        }
                        Label {
                            objectName: "summaryResidual"
                            Layout.fillWidth: true
                            text: invoiceVM.summary.remainingAmount
                            visible: text.length > 0
                            color: theme.amber
                            font.family: "Cairo"
                            font.pixelSize: 14
                            font.bold: true
                            horizontalAlignment: Text.AlignRight
                        }
                    }
                    PrimaryButton {
                        objectName: "finalizeInvoice"
                        Layout.preferredWidth: 150
                        Layout.preferredHeight: 42
                        text: invoiceVM.finalizing ? "جارٍ الحفظ…" : "حفظ الفاتورة F9"
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
        width: Math.min(460, invoicePage.width - 40)
        padding: 16
        font.family: "Cairo"
        onOpened: confirmButton.forceActiveFocus()
        onClosed: invoicePage.focusBarcode()
        // House dialog frame (as goldPriceDialog in Main.qml): gold title, dark card.
        header: Label {
            text: confirmDialog.title
            color: theme.gold
            font.family: "Cairo"
            font.pixelSize: 16
            font.bold: true
            horizontalAlignment: Text.AlignRight
            topPadding: 14
            leftPadding: 16
            rightPadding: 16
        }
        background: Rectangle {
            color: theme.bgSecondary
            border.color: theme.goldDark
            radius: theme.radiusSmall
        }
        contentItem: ColumnLayout {
            spacing: 6
            Repeater {
                model: [["إجمالي البيع", invoiceVM.summary.saleTotal], ["قيمة الكسر", invoiceVM.summary.oldGoldTotal],
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
                        color: theme.textPrimary
                        font.family: "Cairo"
                        font.pixelSize: 14
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
            Label {
                Layout.fillWidth: true
                text: "لا يمكن تعديل الفاتورة بعد الحفظ."
                color: theme.textMuted
                font.family: "Cairo"
                font.pixelSize: 11
                horizontalAlignment: Text.AlignRight
            }
        }
        footer: RowLayout {
            layoutDirection: Qt.RightToLeft
            spacing: 8
            Item { Layout.preferredWidth: 8; Layout.preferredHeight: 56 }
            PrimaryButton {
                id: confirmButton
                objectName: "confirmFinalize"
                text: "حفظ الفاتورة"
                enabled: confirmDialog.acknowledged
                onClicked: confirmDialog.confirm()
                Keys.onReturnPressed: confirmDialog.confirm()
                Keys.onEnterPressed: confirmDialog.confirm()
            }
            Button {
                objectName: "confirmBack"
                text: "رجوع"
                font.family: "Cairo"
                flat: true
                onClicked: confirmDialog.close()
            }
            Item { Layout.fillWidth: true }
        }
    }
}
