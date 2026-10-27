import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Billing V2 (canonical invoices) — point-of-sale page in the masGold prototype
// layout: thin header strip (salesperson, customer), sale and old-gold entry rows
// above their side-by-side tables with per-karat gram totals, and a fixed money
// strip grouped as the salesman works: sale → old gold → settlement → save.
// Every amount shown comes from invoiceVM (recalculated by InvoiceService);
// this page only forwards employee input. Gold prices are shown in the
// MainWindow top bar, not here.
Item {
    id: invoicePage
    objectName: "invoicePage"

    property string pageTitle: ""
    property bool activated: false
    readonly property bool overlayOpen: searchPanel.visible || manualPanel.visible || stockPanel.visible
                                        || approvalPopup.opened
    readonly property bool keysActive: visible && !confirmDialog.opened && !overlayOpen
    readonly property bool compact: width < 900
    // The sale and old-gold money blocks share one line only when both fit beside the final panel.
    readonly property bool stackMoney: width < 1250

    Theme { id: theme }

    onVisibleChanged: {
        if (visible && !activated) {
            activated = true
            invoiceVM.activate()
        } else if (visible) {
            invoiceVM.refreshReference()       // payment methods / accounts activated meanwhile
        }
        if (visible)
            focusBarcode()
    }

    // BUG-BILL-08: the ONE draft reset (فاتورة جديدة, تصفير الفاتورة, after a save) rebinds every
    // money field, so a value typed in the previous draft can never survive or be sent again.
    Connections {
        target: invoiceVM
        function onDraftReset() {
            invoiceTotalField.text = Qt.binding(function() { return invoiceVM.invoiceTotal })
            accountField.text = Qt.binding(function() { return invoiceVM.accountAmount })
            settlementReason.text = Qt.binding(function() { return invoiceVM.settlementReason })
            invoicePage.clearCustomerFields()
            oldGoldWeight.text = ""
        }
    }
    Component.onCompleted: {
        if (visible && !activated) {
            activated = true
            invoiceVM.activate()
        }
    }

    // ---------------- overlays: F3 search, F5 manual, F7 from old-gold stock
    function closeOverlays() {
        searchPanel.visible = false
        manualPanel.visible = false
        stockPanel.visible = false
        searchDebounce.stop()
    }
    function openItemSearch() {
        if (!visible || confirmDialog.opened)
            return
        closeOverlays()
        searchPanel.visible = true
        searchText.forceActiveFocus()
        searchText.selectAll()
        invoiceVM.resetSearch()
        searchWeight.text = ""
        searchSize.text = ""
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
        invoiceVM.searchStock(searchText.text, searchWeight.text, searchTolerance.text, searchSize.text)
    }
    function resetItemSearch() {
        searchText.text = ""
        searchWeight.text = ""
        searchSize.text = ""
        searchTolerance.text = "0.05"
        invoiceVM.resetSearch()
        runItemSearch()
        searchText.forceActiveFocus()
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
    function openManual() {
        if (!visible || confirmDialog.opened)
            return
        closeOverlays()
        manualPanel.reset()
        manualPanel.visible = true
        manualType.forceActiveFocus()
    }
    function openStockSale() {
        if (!visible || confirmDialog.opened)
            return
        closeOverlays()
        stockPanel.reset()
        stockPanel.visible = true
        stockType.forceActiveFocus()                    // the first field; الوصف comes last
    }

    function focusLastLegMethod() {
        const combo = invoicePage.findVisual(invoicePage, "legMethod_" + (invoiceVM.paymentLegsModel.rowCount() - 1))
        if (combo)
            combo.forceActiveFocus()
    }
    function focusPayment() {
        const field = invoicePage.findVisual(invoicePage, invoiceVM.totalMode ? "invoiceTotalField" : "legAmount_0")
        if (field)
            field.forceActiveFocus()
    }
    function findVisual(node, name) {
        if (node.objectName === name)
            return node
        for (let i = 0; i < node.children.length; ++i) {
            const found = findVisual(node.children[i], name)
            if (found)
                return found
        }
        return null
    }
    function focusBarcode() {
        barcodeField.forceActiveFocus()
        barcodeField.selectAll()
    }
    // Prototype flow: scan + Enter adds and refocuses; Enter on an empty field
    // moves on to the real money.
    function barcodeEnter() {
        if (barcodeField.text.length === 0) {
            if (invoiceVM.saleCount > 0 || invoiceVM.oldGoldCount > 0)
                invoicePage.focusPayment()
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
    function commitMoneyFields() {
        for (let i = 0; i < 3; ++i) {
            const leg = invoicePage.findVisual(invoicePage, "legAmount_" + i)
            if (leg)
                leg.commit()
        }
        accountField.commit()
    }
    function startFinalize() {
        // A discount or an amount still being typed is committed first (a rejected value is never saved).
        discountField.commit()
        commitMoneyFields()
        if (!invoiceVM.canFinalize) {
            // No anonymous invoice: take the employee straight to the missing customer name.
            if (invoiceVM.customerMissing)
                customerName.forceActiveFocus()
            return
        }
        staleCheck.checked = false
        deadCashCheck.checked = false
        confirmDialog.open()
    }
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
    function clearCustomerFields() {
        customerName.text = ""
        customerMobile.text = ""
        customerPlace.text = ""
        suggestionsPopup.close()
    }
    function startNewInvoice() {
        invoiceVM.newInvoice()
        clearCustomerFields()
        barcodeField.text = ""
        oldGoldWeight.text = ""
        closeOverlays()
        focusBarcode()
    }
    function typedCustomer() {
        // Typed details describe a new customer until an existing one is chosen.
        invoiceVM.setNewCustomer(customerName.text, customerMobile.text, customerPlace.text)
    }
    function chooseSuggestion(row) {
        var rows = invoiceVM.customerSuggestions
        if (row < 0 || row >= rows.length)
            return
        customerName.text = rows[row].name
        customerMobile.text = rows[row].mobile
        customerPlace.text = rows[row].place
        invoiceVM.chooseCustomer(rows[row].customerId)
        suggestionsPopup.close()
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
            invoicePage.clearCustomerFields()
            oldGoldWeight.text = ""
            // Sold items, old gold and reviews change other pages.
            if (typeof inventoryVM !== "undefined" && inventoryVM)
                inventoryVM.refresh()
            if (typeof oldGoldConversionVM !== "undefined" && oldGoldConversionVM)
                oldGoldConversionVM.refreshBalances()
            if (typeof reviewsVM !== "undefined" && reviewsVM)
                reviewsVM.refresh()
            invoicePage.focusBarcode()
        }
    }

    // ---------------------------------------------------- keyboard (locked map)
    // F2 فاتورة جديدة • F3 بحث عن قطعة • F4 العميل • F5 إدخال يدوي • F6 الكسر • F7 بيع من الكسر
    // F8 المبلغ بالكامل (كسر فقط) • F9 / Ctrl+S حفظ • Ctrl+M المبلغ • Alt+1/2/3 العيار • Esc إغلاق
    Shortcut { sequence: "F2"; enabled: invoicePage.keysActive; onActivated: invoicePage.startNewInvoice() }
    Shortcut { sequence: "F3"; enabled: invoicePage.visible && !confirmDialog.opened; onActivated: invoicePage.openItemSearch() }
    Shortcut { sequence: "F5"; enabled: invoicePage.visible && !confirmDialog.opened; onActivated: invoicePage.openManual() }
    Shortcut { sequence: "F7"; enabled: invoicePage.visible && !confirmDialog.opened; onActivated: invoicePage.openStockSale() }
    Shortcut {
        sequence: "Esc"
        enabled: invoicePage.visible && (searchPanel.visible || manualPanel.visible || stockPanel.visible)
        onActivated: { invoicePage.closeOverlays(); invoiceVM.clearItemSearch(); invoicePage.focusBarcode() }
    }
    Shortcut { sequence: "F4"; enabled: invoicePage.keysActive; onActivated: customerName.forceActiveFocus() }
    Shortcut { sequence: "F6"; enabled: invoicePage.keysActive; onActivated: oldGoldWeight.forceActiveFocus() }
    Shortcut { sequence: "Ctrl+M"; enabled: invoicePage.keysActive; onActivated: invoicePage.focusPayment() }
    // F8: the full amount, only when one exists (old gold only); inactive otherwise (Ctrl+M reaches the amount).
    Shortcut { sequence: "F8"; enabled: invoicePage.keysActive && invoiceVM.fullAmountAvailable; onActivated: { invoiceVM.payFullAmount(); invoicePage.focusPayment() } }
    Shortcut { sequences: ["F9", "Ctrl+S"]; enabled: invoicePage.keysActive; onActivated: invoicePage.startFinalize() }
    Shortcut { sequence: "Alt+1"; enabled: invoicePage.keysActive; onActivated: invoicePage.chooseKarat("18") }
    Shortcut { sequence: "Alt+2"; enabled: invoicePage.keysActive; onActivated: invoicePage.chooseKarat("21") }
    Shortcut { sequence: "Alt+3"; enabled: invoicePage.keysActive; onActivated: invoicePage.chooseKarat("24") }

    Rectangle {
        anchors.fill: parent
        color: theme.bgPrimary
    }

    // ---------------------------------------------------- components
    component Strip: Rectangle {
        id: strip
        property color accent: theme.border
        color: theme.bgSecondary
        radius: 4
    }

    component FieldLabel: Label {
        color: theme.textMuted
        font.family: "Cairo"
        font.pixelSize: 12
        horizontalAlignment: Text.AlignRight
    }

    component Value: Label {
        color: theme.textPrimary
        font.family: "Cairo"
        font.pixelSize: 14
        font.bold: true
    }

    component AmountField: TextField {
        Layout.preferredHeight: 30
        topPadding: 3
        bottomPadding: 3
        color: theme.textPrimary
        placeholderTextColor: theme.textDisabled
        font.family: "Cairo"
        font.pixelSize: 13
        horizontalAlignment: TextInput.AlignRight
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        selectByMouse: true
        // The value when this edit began: a refused final value goes back to it (never a typed prefix).
        property string editStart: ""
        onActiveFocusChanged: if (activeFocus) { editStart = text; selectAll() }
    }

    component TextInputField: TextField {
        Layout.preferredHeight: 30
        topPadding: 3
        bottomPadding: 3
        color: theme.textPrimary
        placeholderTextColor: theme.textDisabled
        font.family: "Cairo"
        font.pixelSize: 13
        horizontalAlignment: TextInput.AlignRight
        selectByMouse: true
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
            implicitHeight: 34
            radius: theme.radiusSmall
            color: !primary.enabled ? theme.bgTertiary : primary.down ? theme.goldDark : theme.gold
            border.color: primary.enabled ? theme.goldBright : theme.border
            border.width: 1
        }
    }

    component SmallButton: Button {
        id: small
        property bool checkedLook: false
        property color checkedColor: theme.gold
        font.family: "Cairo"
        font.pixelSize: 12
        focusPolicy: Qt.TabFocus
        leftPadding: 8
        rightPadding: 8
        contentItem: Label {
            text: small.text
            font: small.font
            color: small.checkedLook ? theme.bgPrimary : small.enabled ? theme.textPrimary : theme.textDisabled
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            implicitHeight: 30
            implicitWidth: 44
            radius: theme.radiusSmall
            color: small.checkedLook ? small.checkedColor : small.down ? theme.panelHover : theme.bgTertiary
            border.color: small.checkedLook ? small.checkedColor : small.enabled ? theme.goldDark : theme.border
            border.width: 1
        }
    }

    component RemoveButton: ToolButton {
        id: removeButton
        text: "حذف"
        font.family: "Cairo"
        font.pixelSize: 11
        implicitHeight: 24
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
        font.pixelSize: 13
        horizontalAlignment: Text.AlignRight
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideLeft
    }

    component HeadCell: Cell {
        color: theme.textSecondary
        font.pixelSize: 12
        font.bold: true
    }

    component KaratChip: SmallButton {
        property bool selected: false
        Layout.preferredWidth: 36
        focusPolicy: Qt.NoFocus
        checkedLook: selected
        checkedColor: theme.priceBuy
        font.bold: selected
    }

    component ChoiceCombo: ComboBox {
        id: combo
        property string placeholder: ""
        Layout.preferredHeight: 30
        font.family: "Cairo"
        font.pixelSize: 12
        textRole: "name"
        valueRole: "id"
        displayText: currentIndex < 0 ? placeholder : currentText
    }

    // مباع / مشترى grams per karat: always visible, dim at zero, strong when used.
    component WeightStrip: RowLayout {
        id: weights
        property string title: ""
        property var totals: ({})
        property color accent: theme.textPrimary
        layoutDirection: Qt.RightToLeft
        spacing: 6
        Repeater {
            model: ["18", "21", "22", "24"]
            Rectangle {
                readonly property string value: weights.totals[modelData] || "—"
                Layout.fillWidth: true
                Layout.preferredHeight: 24
                radius: 3
                color: value === "—" ? "transparent" : theme.bgTertiary
                border.color: value === "—" ? theme.border : weights.accent
                Label {
                    objectName: weights.objectName + "_" + modelData
                    anchors.centerIn: parent
                    text: weights.title + " " + modelData + "K: " + parent.value + (parent.value === "—" ? "" : " جرام")
                    color: parent.value === "—" ? theme.textDisabled : weights.accent
                    font.family: "Cairo"
                    font.pixelSize: 12
                    font.bold: parent.value !== "—"
                }
            }
        }
    }

    // A floating panel over the sales table (the invoice layout underneath never moves).
    component Overlay: Rectangle {
        visible: false
        z: 20
        color: theme.bgPrimary
        radius: 6
        border.color: theme.gold
        border.width: 1
    }

    // ---------------------------------------------------- page
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 6

        // ---------------- header strip: salesperson • customer (name / phone / area) • account
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
                    Layout.preferredWidth: invoicePage.compact ? 104 : 140
                    Layout.preferredHeight: 32
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
                TextInputField {
                    id: customerName
                    objectName: "customerNameField"
                    Layout.fillWidth: true
                    Layout.maximumWidth: 200
                    Layout.minimumWidth: 110
                    placeholderText: text.length > 0 ? "" : "الاسم أو التليفون"
                    onTextEdited: {
                        customerDebounce.target = customerName
                        customerDebounce.restart()
                        invoicePage.typedCustomer()
                    }
                    Keys.onDownPressed: if (suggestionsPopup.opened) suggestionList.forceActiveFocus()
                    onAccepted: suggestionsPopup.opened && invoiceVM.customerSuggestions.length > 0
                                ? invoicePage.chooseSuggestion(0) : invoicePage.focusBarcode()
                }
                TextInputField {
                    id: customerMobile
                    objectName: "customerMobileField"
                    Layout.preferredWidth: invoicePage.compact ? 112 : 124
                    placeholderText: text.length > 0 ? "" : "التليفون"
                    inputMethodHints: Qt.ImhDialableCharactersOnly
                    onTextEdited: {
                        customerDebounce.target = customerMobile
                        customerDebounce.restart()
                        invoicePage.typedCustomer()
                    }
                    onAccepted: invoicePage.focusBarcode()
                }
                TextInputField {
                    id: customerPlace
                    objectName: "customerPlaceField"
                    Layout.preferredWidth: invoicePage.compact ? 70 : 100
                    placeholderText: text.length > 0 ? "" : "المنطقة"
                    onTextEdited: invoicePage.typedCustomer()
                    onAccepted: invoicePage.focusBarcode()
                }
                Timer {
                    id: customerDebounce
                    property var target: null
                    interval: 250
                    onTriggered: {
                        // Suggestions arrive from a background search; the popup opens when they do.
                        suggestionsPopup.anchorField = target
                        invoiceVM.searchCustomers(target ? target.text : "")
                    }
                }
                ColumnLayout {
                    spacing: 0
                    visible: invoiceVM.customerInfo.state !== "NONE"
                    Label {
                        objectName: "customerAccount"
                        text: invoiceVM.customerInfo.accountText
                        color: invoiceVM.customerInfo.state === "NEW" ? theme.blue : theme.textPrimary
                        font.family: "Cairo"
                        font.pixelSize: invoiceVM.customerInfo.state === "NEW" ? 11 : 13
                        font.bold: true
                    }
                    Label {
                        visible: text.length > 0
                        text: invoiceVM.customerInfo.code || (invoiceVM.customerInfo.sameMobile
                              ? "يوجد عميل بنفس الرقم: " + invoiceVM.customerInfo.sameMobile : "")
                        color: invoiceVM.customerInfo.sameMobile ? theme.statusWarning : theme.textMuted
                        font.family: "Cairo"
                        font.pixelSize: 10
                    }
                }
                Rectangle {
                    objectName: "deadCashBanner"
                    visible: invoiceVM.customerInfo.deadCash === true
                    Layout.preferredHeight: 22
                    Layout.preferredWidth: deadCashText.implicitWidth + 10
                    // A caution, never a sales block (red stays reserved for errors).
                    color: "transparent"
                    radius: 4
                    border.color: theme.statusWarning
                    Label {
                        id: deadCashText
                        anchors.centerIn: parent
                        text: "⚠️ تنبيه: سبق شطب رصيد لهذا العميل."
                        color: theme.statusWarning
                        font.family: "Cairo"
                        font.pixelSize: 10
                        font.bold: true
                    }
                }
                SmallButton {
                    objectName: "customerClear"
                    visible: invoiceVM.customerInfo.state !== "NONE" || customerName.text.length > 0
                    text: "مسح"
                    onClicked: { invoicePage.clearCustomerFields(); invoiceVM.clearCustomer() }
                }
                Item { Layout.fillWidth: true }
                Label {
                    objectName: "invoicePriceStatus"
                    visible: text.length > 0
                    text: invoiceVM.priceStale ? invoiceVM.priceStatusText : ""
                    color: theme.statusWarning
                    font.family: "Cairo"
                    font.pixelSize: 11
                    font.bold: true
                }
            }
        }

        Label {
            objectName: "invoiceProblems"
            Layout.fillWidth: true
            visible: text.length > 0
            text: invoiceVM.blockingProblems.length > 0 ? invoiceVM.blockingProblems.join(" • ")
                                                        : invoiceVM.previewError
            color: theme.statusWarning
            font.family: "Cairo"
            font.pixelSize: 12
            horizontalAlignment: Text.AlignRight
            elide: Text.ElideLeft
        }

        // ---------------- money strip: new gold • old gold • real money | final amount + save
        // No price is typed: the real money decides the sale (effective = O + P_in − P_out + L).
        Strip {
            objectName: "invoiceSummaryBar"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.max(moneyColumn.implicitHeight, finalPanel.implicitHeight) + 10
            accent: theme.gold

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 8
                anchors.rightMargin: 12
                anchors.topMargin: 5
                anchors.bottomMargin: 5
                layoutDirection: Qt.RightToLeft
                spacing: 10

                ColumnLayout {
                    id: moneyColumn
                    Layout.fillWidth: true
                    spacing: 4

                    // Row 1: new gold (right) and old gold bought (left, only when present);
                    // stacked in two lines on a narrow window so the final panel keeps its room.
                    GridLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        columns: invoicePage.stackMoney ? 1 : 4
                        rowSpacing: 4
                        columnSpacing: 6

                        RowLayout {
                            objectName: "salePricingBlock"
                            Layout.fillWidth: true
                            visible: invoiceVM.saleCount > 0
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            FieldLabel { text: invoicePage.compact ? "ذهب الجديد" : "قيمة ذهب الجديد" }
                            Value { objectName: "summaryGoldValue"; text: invoiceVM.summary.goldValue || "—"; color: theme.textSecondary; font.pixelSize: 15 }
                            FieldLabel { text: invoicePage.compact ? "سعر البيع" : "سعر البيع الفعلي" }
                            Value { objectName: "summaryEffectiveSale"; text: invoiceVM.summary.effectiveSale || "—"; color: theme.textSecondary; font.pixelSize: 15 }
                            // The one making figure the salesman needs: average per gram.
                            Rectangle {
                                Layout.preferredHeight: 30
                                Layout.preferredWidth: averageRow.implicitWidth + 14
                                radius: theme.radiusSmall
                                color: theme.bgTertiary
                                border.color: invoiceVM.summary.makingStatus === "OUT_OF_RANGE" ? theme.statusWarning : theme.goldDark
                                RowLayout {
                                    id: averageRow
                                    anchors.centerIn: parent
                                    layoutDirection: Qt.RightToLeft
                                    spacing: 6
                                    FieldLabel { text: invoicePage.compact ? "م. المصنعية" : "متوسط المصنعية"; color: theme.textSecondary }
                                    Value {
                                        objectName: "summaryAverageMaking"
                                        text: invoiceVM.summary.averageMaking || "—"
                                        color: theme.goldBright
                                        font.pixelSize: 19
                                    }
                                }
                            }
                            Label {
                                objectName: "makingReviewFlag"
                                visible: text.length > 0
                                // The one flexible item of this row: it gives way (elides) before anything
                                // else, so the larger labels never push the Save panel off screen.
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                Layout.preferredWidth: implicitWidth
                                elide: Text.ElideLeft
                                ToolTip.visible: truncated && makingFlagHover.hovered
                                ToolTip.text: text
                                HoverHandler { id: makingFlagHover }
                                text: invoiceVM.summary.makingNotice
                                color: invoiceVM.summary.makingStatus === "OUT_OF_RANGE" ? theme.statusWarning : theme.textMuted
                                font.family: "Cairo"
                                font.pixelSize: 14
                                font.bold: invoiceVM.summary.makingStatus === "OUT_OF_RANGE"
                            }
                        }
                        Item { visible: !invoicePage.stackMoney; Layout.fillWidth: true }
                        Rectangle { visible: oldGoldBlock.visible && invoiceVM.saleCount > 0 && !invoicePage.stackMoney; Layout.preferredWidth: 1; Layout.preferredHeight: 22; color: theme.border }
                        RowLayout {
                            id: oldGoldBlock
                            objectName: "oldGoldBlock"
                            visible: invoiceVM.oldGoldCount > 0
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            FieldLabel { text: invoicePage.compact ? "الكسر" : "قيمة الكسر" }
                            Value { objectName: "summaryOldGoldGross"; text: invoiceVM.summary.oldGoldGross || "—"; color: theme.textSecondary; font.pixelSize: 15 }
                            FieldLabel { text: "خصم %" }
                            AmountField {
                                id: discountField
                                objectName: "oldGoldDiscountField"
                                // Room for 1.25 / 2.00 / 5.00 without shrinking the font (default padding
                                // alone ate most of the old 50 px).
                                Layout.preferredWidth: 72
                                leftPadding: 6
                                rightPadding: 6
                                placeholderText: ""
                                font.pixelSize: 15
                                color: invoiceVM.summary.discountChanged ? theme.amber : theme.textPrimary
                                text: invoiceVM.oldGoldDiscountPercent
                                // Validated only when committed (Enter, focus lost, before save); an invalid
                                // value is replaced by the last valid one and never reaches the preview.
                                function commit() {
                                    if (text !== invoiceVM.oldGoldDiscountPercent)
                                        invoiceVM.commitOldGoldDiscount(text)
                                    text = Qt.binding(function() { return invoiceVM.oldGoldDiscountPercent })
                                }
                                onEditingFinished: commit()
                                onAccepted: invoicePage.focusPayment()
                            }
                            Label {
                                objectName: "discountWarning"
                                visible: text.length > 0
                                text: invoiceVM.discountWarning
                                color: theme.statusWarning
                                font.family: "Cairo"
                                font.pixelSize: 14
                            }
                            FieldLabel { text: invoicePage.compact ? "الصافي" : "صافي الكسر" }
                            Value { objectName: "summaryOldGold"; text: invoiceVM.summary.oldGoldTotal || "—"; color: theme.priceBuy }
                            Label {
                                objectName: "discountChangedFlag"
                                visible: invoiceVM.summary.discountChanged
                                text: invoicePage.compact ? "مُعدّل" : "خصم مُعدّل"
                                color: theme.amber
                                font.family: "Cairo"
                                font.pixelSize: 14
                            }
                        }
                    }

                    // إجمالي الفاتورة: the ONE commercial total (with sold pieces). The payment legs and
                    // the customer account below only allocate it; they never change it.
                    RowLayout {
                        objectName: "invoiceTotalRow"
                        visible: invoiceVM.totalMode
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 6
                        FieldLabel { text: "إجمالي الفاتورة"; color: theme.textPrimary; font.bold: true }
                        AmountField {
                            id: invoiceTotalField
                            objectName: "invoiceTotalField"
                            Layout.preferredWidth: invoicePage.compact ? 110 : 130
                            placeholderText: ""
                            font.bold: true
                            text: invoiceVM.invoiceTotal
                            onTextEdited: invoiceVM.setInvoiceTotal(text)
                            // A refused total is replaced by the last valid one when editing ends.
                            onEditingFinished: {
                                invoiceVM.commitInvoiceTotal()          // mixed invoice: nearest EGP 5, once
                                text = Qt.binding(function() { return invoiceVM.invoiceTotal })
                            }
                            onAccepted: invoicePage.findVisual(invoicePage, "legAmount_0").forceActiveFocus()
                        }
                        Label {
                            objectName: "remainingNow"
                            visible: text.length > 0
                            text: invoiceVM.remainingText
                            color: theme.amber
                            font.family: "Cairo"
                            font.pixelSize: 15
                            font.bold: true
                        }
                        Label {
                            objectName: "totalRoundedNote"
                            visible: text.length > 0
                            text: invoiceVM.totalRoundedNote
                            color: theme.amber
                            font.family: "Cairo"
                            font.pixelSize: 14
                        }
                        Label {
                            objectName: "paymentWarning"
                            visible: text.length > 0
                            Layout.fillWidth: true
                            text: invoiceVM.paymentWarning
                            color: theme.red
                            font.family: "Cairo"
                            font.pixelSize: 14
                            elide: Text.ElideLeft
                            horizontalAlignment: Text.AlignRight
                        }
                        Item { Layout.fillWidth: !invoiceVM.paymentWarning }
                    }

                    // Row 2+: real money — the direction in words, then one row per payment leg.
                    Flow {
                        id: legFlow
                        objectName: "paymentLegsFlow"
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                    Repeater {
                        id: legRepeater
                        model: invoiceVM.paymentLegsModel
                        RowLayout {
                            id: legRow
                            objectName: "paymentLeg_" + legIndex
                            required property int legIndex
                            required property string method
                            required property string account
                            required property string amount
                            required property bool needsAccount
                            required property var accounts
                            required property string unavailableText
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6

                            // Direction: fixed words, or an explicit choice for an exchange.
                            Label {
                                objectName: "paymentDirectionLabel"
                                visible: legRow.legIndex === 0 && !invoiceVM.directionChoosable
                                text: invoiceVM.paymentLabel
                                color: invoiceVM.paymentDirection === "OUT" ? theme.priceBuy : theme.priceSell
                                font.family: "Cairo"
                                font.pixelSize: 15
                                font.bold: true
                            }
                            SmallButton {
                                objectName: "directionIn"
                                Layout.preferredHeight: 34
                                visible: legRow.legIndex === 0 && invoiceVM.directionChoosable
                                text: "المستلم من العميل"
                                font.pixelSize: 14
                                checkedLook: invoiceVM.paymentDirection === "IN"
                                onClicked: invoiceVM.setPaymentDirection("IN")
                            }
                            SmallButton {
                                objectName: "directionOut"
                                Layout.preferredHeight: 34
                                visible: legRow.legIndex === 0 && invoiceVM.directionChoosable
                                text: "المدفوع للعميل"
                                font.pixelSize: 14
                                checkedLook: invoiceVM.paymentDirection === "OUT"
                                checkedColor: theme.priceBuy
                                onClicked: invoiceVM.setPaymentDirection("OUT")
                            }
                            Item { visible: legRow.legIndex > 0; Layout.preferredWidth: 20 }
                            ChoiceCombo {
                                id: methodCombo
                            Layout.preferredHeight: 32
                                objectName: "legMethod_" + legRow.legIndex
                                popup.onAboutToShow: invoiceVM.refreshReference()
                                Layout.preferredWidth: invoicePage.compact ? 124 : 150
                                // All four supported methods stay visible; one without an active
                                // account is shown dimmed and cannot be saved.
                                model: invoiceVM.paymentMethods
                                textRole: "text"
                                valueRole: "code"
                                popup.width: Math.max(width, 170)
                                delegate: ItemDelegate {
                                    required property var modelData
                                    required property int index
                                    objectName: "legMethodOption_" + modelData.code
                                    width: methodCombo.popup.width
                                    text: modelData.available ? modelData.text : modelData.text + " • غير مفعّل"
                                    font.family: "Cairo"
                                    font.pixelSize: 15
                                    opacity: modelData.available ? 1 : 0.55
                                    highlighted: methodCombo.highlightedIndex === index
                                }
                                currentIndex: {
                                    const rows = invoiceVM.paymentMethods
                                    for (let i = 0; i < rows.length; ++i)
                                        if (rows[i].code === legRow.method)
                                            return i
                                    return -1
                                }
                                onActivated: invoiceVM.setLegMethod(legRow.legIndex, currentValue)
                            }
                            ChoiceCombo {
                                objectName: "legAccount_" + legRow.legIndex
                            Layout.preferredHeight: 32
                                visible: legRow.needsAccount
                                Layout.preferredWidth: invoicePage.compact ? 114 : 172
                                model: legRow.accounts
                                popup.width: Math.max(width, 200)          // full account names in the list
                                placeholder: "اختر الحساب"
                                currentIndex: {
                                    const rows = legRow.accounts
                                    for (let i = 0; i < rows.length; ++i)
                                        if (rows[i].id === legRow.account)
                                            return i
                                    return -1
                                }
                                onActivated: invoiceVM.setLegAccount(legRow.legIndex, currentValue)
                            }
                            Label {
                                objectName: "legUnavailable_" + legRow.legIndex
                                visible: legRow.unavailableText.length > 0
                                text: legRow.unavailableText
                                color: theme.statusWarning
                                font.family: "Cairo"
                                font.pixelSize: 14
                            }
                            AmountField {
                                id: legAmount
                            Layout.preferredHeight: 32
                                objectName: "legAmount_" + legRow.legIndex
                                Layout.preferredWidth: invoicePage.compact ? 98 : 136
                                placeholderText: ""
                                font.bold: true
                                text: legRow.amount
                                // Every keystroke recalculates at once (remaining, على العميل, Save). A value
                                // that would exceed إجمالي الفاتورة is refused; when editing ends the field
                                // shows the last valid value again.
                                function commit() {
                                    if (text !== legRow.amount) {
                                        invoiceVM.setLegAmount(legRow.legIndex, text)
                                        if (text !== legRow.amount)        // refused: back to before this edit
                                            invoiceVM.revertLegAmount(legRow.legIndex, editStart)
                                    }
                                    text = Qt.binding(function() { return legRow.amount })
                                }
                                onTextEdited: invoiceVM.setLegAmount(legRow.legIndex, text)
                                onEditingFinished: commit()
                                Connections {
                                    target: invoiceVM
                                    function onDraftReset() { legAmount.text = Qt.binding(function() { return legRow.amount }) }
                                }
                                onAccepted: invoicePage.startFinalize()
                            }
                            RemoveButton {
                                objectName: "removeLeg_" + legRow.legIndex
                                visible: legRow.legIndex > 0
                                onClicked: invoiceVM.removePaymentLeg(legRow.legIndex)
                            }
                            SmallButton {
                                objectName: "payFullButton"
                                Layout.preferredHeight: 34
                                // Only when a full amount exists (old gold only); never a dead button.
                                visible: legRow.legIndex === 0 && invoiceVM.fullAmountAvailable
                                text: "كامل F8"
                                enabled: invoiceVM.fullAmountAvailable
                                onClicked: { invoiceVM.payFullAmount(); legAmount.forceActiveFocus() }
                            }
                            SmallButton {
                                objectName: "addPaymentLeg"
                                Layout.preferredHeight: 34
                                visible: legRow.legIndex === legRepeater.count - 1 && legRepeater.count < 3
                                enabled: invoiceVM.canAddPayment
                                text: invoiceVM.totalMode ? "+ إضافة" : "+ طريقة"
                                font.pixelSize: 14
                                onClicked: {
                                    // Adding a leg rebuilds these rows (this button included): keep the page,
                                    // then focus the NEW leg's method once the rows exist.
                                    const page = invoicePage
                                    invoiceVM.addPaymentLeg()
                                    Qt.callLater(page.focusLastLegMethod)
                                }
                            }
                            Item { Layout.fillWidth: true }
                        }
                    }
                    }

                    // The customer account is a different concept from the money above: a thin rule
                    // keeps the two blocks visually apart.
                    Rectangle {
                        objectName: "accountSeparator"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: theme.border
                    }

                    // Last row: money deliberately left on the customer account and, for old gold
                    // only, the explicit decision about a rounding gap.
                    Flow {
                        id: accountRow
                        objectName: "accountRow"
                        // على العميل = receivable (cool), للعميل = payable (warm); the words stay explicit.
                        readonly property color tone: invoiceVM.accountDirection === "CUSTOMER_OWES"
                                                      ? theme.accountReceivable
                                                      : invoiceVM.accountDirection === "SHOP_OWES"
                                                        ? theme.accountPayable : theme.textSecondary
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 6
                        // حساب العميل: a separate ledger entry with its own direction; it never changes
                        // the sale price, the making or the money above.
                        FieldLabel { text: "حساب العميل"; color: theme.textPrimary; font.bold: true }
                        SmallButton {
                            objectName: "accountCustomerOwes"
                            text: "على العميل"
                            font.pixelSize: 12
                            // The account part follows the money direction: only one side is offered.
                            enabled: invoiceVM.accountDirection === "CUSTOMER_OWES"
                            checkedLook: enabled
                            checkedColor: theme.accountReceivable
                            onClicked: invoiceVM.setAccountDirection("CUSTOMER_OWES")
                        }
                        SmallButton {
                            objectName: "accountShopOwes"
                            text: "للعميل"
                            font.pixelSize: 12
                            enabled: invoiceVM.accountDirection === "SHOP_OWES"
                            checkedLook: enabled
                            checkedColor: theme.accountPayable
                            onClicked: invoiceVM.setAccountDirection("SHOP_OWES")
                        }
                        FieldLabel { text: "المبلغ" }
                        AmountField {
                            id: accountField
                            height: 32                                  // a Flow child: the Layout height below is not applied
                            Layout.preferredHeight: 32
                            objectName: "accountAmountField"
                            Layout.preferredWidth: invoicePage.compact ? 84 : 110
                            width: invoicePage.compact ? 84 : 110
                            placeholderText: ""
                            text: invoiceVM.accountAmount
                            // على العميل = total − real payments, recalculated automatically (owner rule).
                            readOnly: invoiceVM.accountAutomatic
                            function commit() {
                                if (text !== invoiceVM.accountAmount)
                                    invoiceVM.setAccountAmount(text)
                                text = Qt.binding(function() { return invoiceVM.accountAmount })
                            }
                            onTextEdited: if (!invoiceVM.accountAutomatic) invoiceVM.setAccountAmount(text)
                            onEditingFinished: commit()
                            onAccepted: invoicePage.startFinalize()
                        }
                        Label {
                            objectName: "requiredNow"
                            visible: text.length > 0
                            text: invoiceVM.requiredNowText
                            color: theme.amber
                            font.family: "Cairo"
                            font.pixelSize: 12
                            font.bold: true
                        }
                        Label {
                            objectName: "accountEffect"
                            visible: text.length > 0 && !invoiceVM.hasDifference
                            text: invoiceVM.accountEffectText
                            color: accountRow.tone
                            font.family: "Cairo"
                            font.pixelSize: 12
                        }
                        Label {
                            text: "الباقي:"
                            visible: invoiceVM.hasDifference
                            color: theme.amber
                            font.family: "Cairo"
                            font.pixelSize: 13
                            font.bold: true
                        }
                        Label {
                            objectName: "settlementDifference"
                            visible: invoiceVM.hasDifference
                            text: invoiceVM.summary.differenceAmount
                            color: theme.amber
                            font.family: "Cairo"
                            font.pixelSize: 15
                            font.bold: true
                        }
                        SmallButton {
                            objectName: "differenceToAccount"
                            visible: invoiceVM.hasDifference
                            text: invoicePage.compact ? "للحساب" : "إضافته للحساب"
                            checkedColor: theme.blue
                            onClicked: invoiceVM.setSettlementChoice("ACCOUNT")
                        }
                        TextInputField {
                            id: settlementReason
                            objectName: "settlementReason"
                            visible: invoiceVM.settlementNeedsReason
                            Layout.preferredWidth: 110
                            width: 110
                            placeholderText: text.length > 0 ? "" : "سبب التسوية"
                            text: invoiceVM.settlementReason
                            onTextEdited: invoiceVM.setSettlementReason(text)
                        }
                        SmallButton {
                            objectName: "requestMakingApproval"
                            visible: invoiceVM.makingNeedsApproval
                            text: invoiceVM.makingCanConfirm ? "اعتماد المصنعية" : "موافقة المدير (المصنعية)"
                            // The right to approve never approves by itself: an explicit confirmation.
                            onClicked: invoiceVM.makingCanConfirm ? makingConfirmDialog.open() : approvalPopup.open()
                        }
                        SmallButton {
                            objectName: "requestApproval"
                            visible: invoiceVM.settlementNeedsReason && !invoiceVM.approverOk && invoiceVM.settlementNeedsApproval
                            text: "موافقة المدير"
                            onClicked: approvalPopup.open()
                        }
                        Label {
                            objectName: "approverText"
                            visible: text.length > 0
                            text: invoiceVM.approverText
                            color: invoiceVM.approverOk ? theme.green : theme.red
                            font.family: "Cairo"
                            font.pixelSize: 12
                        }
                        Item { Layout.fillWidth: true }
                    }
                }

                Rectangle { Layout.preferredWidth: 1; Layout.fillHeight: true; color: theme.border }

                // Final action: the money direction and amount, then save (physical left).
                ColumnLayout {
                    id: finalPanel
                    objectName: "summaryDueBox"
                    property string title: invoiceVM.summary.dueLabel
                    Layout.preferredWidth: invoicePage.compact ? 200 : 240
                    Layout.minimumWidth: 160
                    Layout.alignment: Qt.AlignVCenter
                    spacing: 1
                    // إجمالي الفاتورة: the commercial amount (P + L) the making is based on.
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        visible: invoiceVM.invoiceTotalText.length > 0
                        Label {
                            text: "إجمالي الفاتورة"
                            color: theme.textSecondary
                            font.family: "Cairo"
                            font.pixelSize: 15
                            font.bold: true
                        }
                        Item { Layout.fillWidth: true }
                        Label {
                            objectName: "summaryInvoiceTotal"
                            text: invoiceVM.invoiceTotalText
                            color: theme.textPrimary
                            font.family: "Cairo"
                            font.pixelSize: invoicePage.compact ? 16 : 19
                            font.bold: true
                        }
                    }
                    Label {
                        objectName: "summaryDueLabel"
                        Layout.fillWidth: true
                        text: finalPanel.title
                        color: invoiceVM.paymentDirection === "OUT" ? theme.priceBuy : theme.priceSell
                        font.family: "Cairo"
                        font.pixelSize: 16
                        font.bold: true
                        horizontalAlignment: Text.AlignRight
                    }
                    Label {
                        objectName: "summaryDue"
                        Layout.fillWidth: true
                        text: invoiceVM.summary.dueAmount || "—"
                        color: invoiceVM.paymentDirection === "OUT" ? theme.priceBuy : theme.goldBright
                        font.family: "Cairo"
                        font.pixelSize: invoicePage.compact ? 21 : 25
                        font.bold: true
                        horizontalAlignment: Text.AlignRight
                        fontSizeMode: Text.HorizontalFit
                        minimumPixelSize: 14
                    }
                    // How the invoice total is allocated: only the methods used, then the account part.
                    Flow {
                        id: breakdownFlow
                        objectName: "paymentBreakdown"
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1            // the panel keeps its own width; a long row wraps instead of pushing it
                        Layout.minimumWidth: 0
                        visible: invoiceVM.paymentBreakdown.length > 0
                        layoutDirection: Qt.RightToLeft
                        spacing: 6
                        Repeater {
                            model: invoiceVM.paymentBreakdown
                            Label {
                                required property var modelData
                                required property int index
                                objectName: "breakdownRow_" + index
                                width: Math.min(implicitWidth, breakdownFlow.width)
                                wrapMode: Text.WordWrap
                                horizontalAlignment: Text.AlignRight
                                // RLM: a row starting with "InstaPay" stays right-to-left.
                                text: "‏" + modelData.label + ": " + modelData.amount
                                color: modelData.kind === "CUSTOMER_OWES" ? theme.accountReceivable
                                     : modelData.kind === "SHOP_OWES" ? theme.accountPayable : theme.textSecondary
                                font.family: "Cairo"
                                font.pixelSize: 12
                                font.bold: modelData.kind !== "MONEY"
                            }
                        }
                    }
                    Label {
                        objectName: "makingApprovalNote"
                        Layout.fillWidth: true
                        visible: text.length > 0
                        text: invoiceVM.makingApprovalNote
                        color: theme.statusWarning
                        font.family: "Cairo"
                        font.pixelSize: 14
                        wrapMode: Text.WordWrap
                        horizontalAlignment: Text.AlignRight
                    }
                    // تصفير الفاتورة: the same reset as F2 فاتورة جديدة, after a confirmation.
                    SmallButton {
                        objectName: "clearInvoiceButton"
                        Layout.fillWidth: true
                        text: "تصفير الفاتورة"
                        onClicked: clearInvoiceDialog.open()
                    }
                    PrimaryButton {
                        objectName: "finalizeInvoice"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 36
                        font.pixelSize: 17
                        text: invoiceVM.finalizing ? "جارٍ الحفظ…" : "حفظ F9"
                        enabled: invoiceVM.canFinalize
                        onClicked: invoicePage.startFinalize()
                    }
                }
            }
        }

        // ---------------- one-line messages (only when present)
        Rectangle {
            objectName: "invoiceSuccessBanner"
            Layout.fillWidth: true
            Layout.preferredHeight: 26
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
                    text: [invoiceVM.success.netText || "", invoiceVM.success.residualText || "",
                           invoiceVM.success.notes || ""].filter(function(v) { return v.length > 0 }).join(" • ")
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

        // Printing is a separate, non-blocking step after the save above: a failure here
        // never means the invoice itself failed — إعادة المحاولة sends it again, any time.
        Rectangle {
            objectName: "printStatusBanner"
            Layout.fillWidth: true
            Layout.preferredHeight: 26
            visible: invoiceVM.hasSuccess && (invoiceVM.printBusy || invoiceVM.printFailed || invoiceVM.printSucceeded)
            color: theme.bgSecondary
            radius: 4
            border.color: invoiceVM.printFailed ? theme.statusWarning : theme.green
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 6
                anchors.rightMargin: 10
                layoutDirection: Qt.RightToLeft
                spacing: 12
                BusyIndicator {
                    objectName: "printBusyIndicator"
                    visible: invoiceVM.printBusy
                    running: visible
                    Layout.preferredWidth: 20
                    Layout.preferredHeight: 20
                }
                Label {
                    objectName: "printStatusText"
                    Layout.fillWidth: true
                    text: invoiceVM.printStatusText
                    color: invoiceVM.printFailed ? theme.statusWarning : theme.textSecondary
                    font.family: "Cairo"
                    font.pixelSize: 12
                    horizontalAlignment: Text.AlignRight
                    elide: Text.ElideLeft
                }
                SmallButton {
                    objectName: "printRetry"
                    visible: invoiceVM.printFailed && !invoiceVM.printBusy
                    // Fits the one-line banner (Material insets would otherwise overflow it).
                    Layout.preferredHeight: 22
                    topInset: 0
                    bottomInset: 0
                    topPadding: 0
                    bottomPadding: 0
                    font.pixelSize: 11
                    text: "إعادة المحاولة"
                    onClicked: invoiceVM.retryPrint()
                }
                RemoveButton {
                    objectName: "printDismiss"
                    visible: !invoiceVM.printBusy
                    text: "إغلاق"
                    onClicked: invoiceVM.dismissPrintResult()
                }
            }
        }

        Rectangle {
            objectName: "invoiceErrorBanner"
            Layout.fillWidth: true
            Layout.preferredHeight: 26
            visible: invoiceVM.errorMessage.length > 0
            color: theme.redBackground
            radius: 4
            border.color: theme.red
            RowLayout {
                anchors.fill: parent
                anchors.margins: 2
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
                SmallButton {
                    objectName: "barcodeNotFoundManual"
                    visible: invoiceVM.barcodeNotFound
                    Layout.preferredHeight: 22
                    text: "إدخال يدوي F5"
                    onClicked: { invoiceVM.clearError(); invoicePage.openManual() }
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

                    Strip {
                        id: saleEntry
                        Layout.fillWidth: true
                        Layout.preferredHeight: 32
                        accent: theme.priceSell

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 6
                            anchors.rightMargin: 10
                            layoutDirection: Qt.RightToLeft
                            spacing: 4
                            Label {
                                text: "بيع"
                                color: theme.priceSell
                                font.family: "Cairo"
                                font.pixelSize: 13
                                font.bold: true
                            }
                            BarcodeField {
                                id: barcodeField
                                objectName: "invoiceBarcodeField"
                                Layout.fillWidth: true
                                Layout.maximumWidth: 220
                                Layout.minimumWidth: 100
                                Layout.preferredHeight: 30
                                topPadding: 3
                                bottomPadding: 3
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
                            SmallButton {
                                objectName: "openManualEntry"
                                text: "يدوي F5"
                                onClicked: invoicePage.openManual()
                            }
                            SmallButton {
                                objectName: "openStockSale"
                                text: "من الكسر F7"
                                onClicked: invoicePage.openStockSale()
                            }
                            Item { Layout.fillWidth: true }
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
                                Layout.leftMargin: 34
                                layoutDirection: Qt.RightToLeft
                                spacing: 6
                                // No per-line total for the employee (the saved lines keep every value).
                                HeadCell { text: "الصنف"; Layout.preferredWidth: 5 }
                                HeadCell { text: "الباركود"; Layout.preferredWidth: 3 }
                                HeadCell { text: "عيار"; Layout.preferredWidth: 1 }
                                HeadCell { text: "الوزن"; Layout.preferredWidth: 2 }
                                HeadCell { text: "المصنع"; Layout.preferredWidth: 2 }
                                HeadCell { text: "المقاس"; Layout.preferredWidth: 2 }
                                HeadCell { text: "ملاحظات"; Layout.preferredWidth: 3 }
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
                                    id: saleRow
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
                                        RowLayout {
                                            Layout.fillWidth: true
                                            Layout.preferredWidth: 5
                                            layoutDirection: Qt.RightToLeft
                                            spacing: 4
                                            Label {
                                                Layout.fillWidth: true
                                                text: model.itemName
                                                color: theme.textPrimary
                                                font.family: "Cairo"
                                                font.pixelSize: 13
                                                font.bold: true
                                                horizontalAlignment: Text.AlignRight
                                                elide: Text.ElideLeft
                                            }
                                            Rectangle {
                                                objectName: "saleTag_" + index
                                                visible: model.tag.length > 0
                                                Layout.preferredHeight: 18
                                                Layout.preferredWidth: tagText.implicitWidth + 8
                                                radius: 3
                                                color: "transparent"
                                                border.color: model.kind === "UNIDENTIFIED" ? theme.amber : theme.priceBuy
                                                Label {
                                                    id: tagText
                                                    anchors.centerIn: parent
                                                    text: model.tag
                                                    color: model.kind === "UNIDENTIFIED" ? theme.amber : theme.priceBuy
                                                    font.family: "Cairo"
                                                    font.pixelSize: 10
                                                }
                                            }
                                        }
                                        Cell {
                                            Layout.preferredWidth: 3
                                            text: model.barcode || "—"
                                            color: theme.textMuted
                                            font.pixelSize: 11
                                            elide: Text.ElideRight
                                        }
                                        Cell { Layout.preferredWidth: 1; text: model.karat }
                                        Cell { Layout.preferredWidth: 2; text: model.weight; font.bold: true }
                                        Cell { Layout.preferredWidth: 2; text: model.factory || "—"; color: theme.textSecondary }
                                        Cell {
                                            objectName: "saleSize_" + index
                                            Layout.preferredWidth: 2
                                            text: model.size || "—"
                                            color: theme.textSecondary
                                        }
                                        Cell {
                                            objectName: "saleNotes_" + index
                                            Layout.preferredWidth: 3
                                            text: model.notes
                                            color: theme.textSecondary
                                            font.pixelSize: 12
                                            ToolTip.visible: notesHover.hovered && truncated
                                            ToolTip.text: model.notes
                                            HoverHandler { id: notesHover }
                                        }
                                        RemoveButton {
                                            objectName: "removeSale_" + index
                                            Layout.preferredWidth: 30
                                            onClicked: invoiceVM.removeSaleLine(index)
                                        }
                                    }
                                }

                                Label {
                                    anchors.centerIn: parent
                                    visible: saleList.count === 0
                                    text: "امسح الباركود — أو F3 بحث • F5 يدوي • F7 من الكسر"
                                    color: theme.textDisabled
                                    font.family: "Cairo"
                                    font.pixelSize: 12
                                }
                            }

                            WeightStrip {
                                objectName: "soldWeights"
                                Layout.fillWidth: true
                                Layout.margins: 2
                                title: "مباع"
                                totals: invoiceVM.weightTotals.sold
                                accent: theme.priceSell
                            }
                        }
                    }
                }

                // ---- F3 بحث عن قطعة: الصنف → العيار → المصنع → الوزن أو المقاس
                Overlay {
                    id: searchPanel
                    objectName: "itemSearchPanel"
                    property int chosenIndex: -1
                    anchors.top: parent.top
                    anchors.topMargin: saleEntry.height + 4
                    anchors.right: parent.right
                    anchors.left: parent.left
                    anchors.leftMargin: Math.max(0, parent.width - 600)
                    height: Math.min(parent.height - saleEntry.height - 4, 400)

                    Connections {
                        target: invoiceVM
                        function onChanged() {
                            if (searchPanel.chosenIndex >= invoiceVM.searchResults.length)
                                searchPanel.chosenIndex = -1
                        }
                    }
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
                            TextInputField {
                                id: searchText
                                objectName: "itemSearchText"
                                Layout.fillWidth: true
                                placeholderText: text.length > 0 ? "" : "ابحث باسم الصنف / المصنع / الباركود (اختياري)"
                                // Names in Arabic, or a scanned barcode: only a scanner burst (Enter-terminated)
                                // is rebuilt from its physical scan codes (see BarcodeField / app/barcode_input.py).
                                property var strokes: []
                                Keys.onPressed: function(event) {
                                    if (typeof barcodeInput === "undefined"
                                            || (event.modifiers & (Qt.ControlModifier | Qt.AltModifier)))
                                        return
                                    if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                                        const scanned = barcodeInput.fromBurst(strokes)
                                        if (scanned.length > 0) {
                                            text = scanned
                                            searchPanel.chosenIndex = -1      // search the real barcode, not the Arabic text
                                        }
                                        strokes = []
                                        return
                                    }
                                    if (!event.text || event.text.length === 0) { strokes = []; return }
                                    const now = Date.now()
                                    const last = strokes.length ? strokes[strokes.length - 1].t : now
                                    strokes = (now - last > 100 ? [] : strokes).concat([{ t: now, code: event.nativeScanCode,
                                        shift: (event.modifiers & Qt.ShiftModifier) !== 0 }])
                                }
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
                                objectName: "itemSearchReset"
                                text: "مسح البحث"
                                onClicked: invoicePage.resetItemSearch()
                            }
                            RemoveButton {
                                objectName: "itemSearchClose"
                                text: "إغلاق Esc"
                                onClicked: invoicePage.closeItemSearch()
                            }
                        }

                        // Each level lists only what IN_STOCK stock actually has; choosing a
                        // higher level resets the levels below it.
                        Flow {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 4
                            FieldLabel { text: "الصنف:"; height: 28; verticalAlignment: Text.AlignVCenter }
                            Repeater {
                                model: invoiceVM.searchFacets.itemTypes
                                SmallButton {
                                    objectName: "searchType_" + modelData.id
                                    height: 28
                                    text: modelData.name + " (" + modelData.count + ")"
                                    checkedLook: invoiceVM.searchTypeId === modelData.id
                                    onClicked: {
                                        invoiceVM.setSearchType(invoiceVM.searchTypeId === modelData.id ? 0 : modelData.id)
                                        invoicePage.runItemSearch()
                                    }
                                }
                            }
                        }
                        Flow {
                            Layout.fillWidth: true
                            visible: invoiceVM.searchTypeId > 0
                            layoutDirection: Qt.RightToLeft
                            spacing: 4
                            FieldLabel { text: "العيار:"; height: 28; verticalAlignment: Text.AlignVCenter }
                            Repeater {
                                model: invoiceVM.searchFacets.karats
                                SmallButton {
                                    objectName: "searchKarat_" + modelData.name
                                    height: 28
                                    text: modelData.name + " (" + modelData.count + ")"
                                    checkedLook: invoiceVM.searchKaratId === modelData.id
                                    onClicked: {
                                        invoiceVM.setSearchKarat(invoiceVM.searchKaratId === modelData.id ? 0 : modelData.id)
                                        invoicePage.runItemSearch()
                                    }
                                }
                            }
                        }
                        Flow {
                            Layout.fillWidth: true
                            visible: invoiceVM.searchKaratId > 0
                            layoutDirection: Qt.RightToLeft
                            spacing: 4
                            FieldLabel { text: "المصنع:"; height: 28; verticalAlignment: Text.AlignVCenter }
                            Repeater {
                                model: invoiceVM.searchFacets.factories
                                SmallButton {
                                    objectName: "searchFactory_" + modelData.id
                                    height: 28
                                    text: modelData.name + " (" + modelData.count + ")"
                                    checkedLook: invoiceVM.searchFactoryId === modelData.id
                                    onClicked: {
                                        invoiceVM.setSearchFactory(invoiceVM.searchFactoryId === modelData.id ? 0 : modelData.id)
                                        invoicePage.runItemSearch()
                                    }
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 4
                            FieldLabel { text: "الوزن" }
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
                                Layout.preferredWidth: 54
                                placeholderText: ""
                                text: "0.05"
                                onTextEdited: searchDebounce.restart()
                                onAccepted: invoicePage.runItemSearch()
                            }
                            FieldLabel { text: "أو المقاس" }
                            AmountField {
                                id: searchSize
                                objectName: "itemSearchSize"
                                Layout.preferredWidth: 44
                                placeholderText: ""
                                inputMethodHints: Qt.ImhNone
                                onTextEdited: searchDebounce.restart()
                                onAccepted: invoicePage.runItemSearch()
                            }
                            Label {
                                objectName: "itemSearchPath"
                                Layout.fillWidth: true
                                text: invoiceVM.searchPath
                                color: theme.gold
                                font.family: "Cairo"
                                font.pixelSize: 11
                                font.bold: true
                                horizontalAlignment: Text.AlignLeft
                                elide: Text.ElideRight
                            }
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

                            delegate: Rectangle {
                                objectName: "itemSearchRow_" + index
                                readonly property bool chosen: searchPanel.chosenIndex === index
                                width: searchResultsList.width
                                height: 34
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
                                    Cell { Layout.preferredWidth: 3; text: modelData.itemName; font.pixelSize: 13; font.bold: true }
                                    Cell { Layout.preferredWidth: 2; text: modelData.weightText; font.pixelSize: 13; font.bold: true; color: theme.goldBright }
                                    Cell { Layout.preferredWidth: 2; text: modelData.karatText }
                                    Cell { Layout.preferredWidth: 4; text: modelData.detailText; color: theme.textSecondary; font.pixelSize: 12 }
                                    Cell { Layout.preferredWidth: 2; text: modelData.barcodeText; color: theme.textMuted; font.pixelSize: 11 }
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
                                text: "اختيار هذه القطعة"
                                enabled: searchPanel.chosenIndex >= 0
                                onClicked: invoicePage.confirmSearchedItem()
                            }
                            SmallButton {
                                objectName: "itemSearchToManual"
                                text: "لم أجد القطعة — إدخال يدوي F5"
                                onClicked: invoicePage.openManual()
                            }
                            Item { Layout.fillWidth: true }
                        }
                    }
                }

                // ---- F5 إدخال يدوي: the piece is sold, stock identity is reviewed later
                Overlay {
                    id: manualPanel
                    objectName: "manualPanel"
                    property int karatId: -1
                    property string reason: ""
                    function reset() {
                        manualType.currentIndex = -1
                        manualFactory.currentIndex = -1
                        manualWeight.text = ""
                        manualSize.text = ""
                        manualNotes.text = ""
                        karatId = -1
                        reason = ""
                        manualError.text = ""
                    }
                    function add() {
                        var typeId = manualType.currentIndex >= 0 ? invoiceVM.itemTypes[manualType.currentIndex].id : 0
                        var factoryId = manualFactory.currentIndex >= 0 ? invoiceVM.factories[manualFactory.currentIndex].id : 0
                        var error = invoiceVM.addManualLine(typeId, karatId, factoryId, manualWeight.text, manualSize.text,
                                                            manualNotes.text, reason)
                        manualError.text = error
                        if (error.length === 0) {
                            visible = false
                            invoicePage.focusBarcode()
                        }
                    }
                    anchors.top: parent.top
                    anchors.topMargin: saleEntry.height + 4
                    anchors.right: parent.right
                    anchors.left: parent.left
                    anchors.leftMargin: Math.max(0, parent.width - 520)
                    height: manualColumn.implicitHeight + 20

                    ColumnLayout {
                        id: manualColumn
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 6
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            Label {
                                text: "إدخال يدوي — القطعة تُسجل للمراجعة"
                                color: theme.amber
                                font.family: "Cairo"
                                font.pixelSize: 13
                                font.bold: true
                            }
                            Item { Layout.fillWidth: true }
                            RemoveButton { text: "إغلاق Esc"; onClicked: { manualPanel.visible = false; invoicePage.focusBarcode() } }
                        }
                        Label {
                            objectName: "manualHint"
                            Layout.fillWidth: true
                            text: "إذا أمكن تحديد القطعة من البحث F3، استخدم القطعة المسجلة بدل الإدخال اليدوي."
                            wrapMode: Text.WordWrap
                            color: theme.textMuted
                            font.family: "Cairo"
                            font.pixelSize: 11
                            horizontalAlignment: Text.AlignRight
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            FieldLabel { text: "الصنف" }
                            ChoiceCombo { id: manualType; objectName: "manualType"; Layout.preferredWidth: 130; model: invoiceVM.itemTypes; placeholder: "اختر الصنف" }
                            FieldLabel { text: "العيار" }
                            Repeater {
                                model: invoiceVM.priceRows
                                KaratChip {
                                    objectName: "manualKarat_" + modelData.karat
                                    text: modelData.karat
                                    selected: manualPanel.karatId === modelData.karatId
                                    onClicked: manualPanel.karatId = modelData.karatId
                                }
                            }
                            Item { Layout.fillWidth: true }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            FieldLabel { text: "الوزن" }
                            AmountField { id: manualWeight; objectName: "manualWeight"; Layout.preferredWidth: 70; placeholderText: ""; onAccepted: manualPanel.add() }
                            FieldLabel { text: "المصنع" }
                            ChoiceCombo { id: manualFactory; objectName: "manualFactory"; Layout.preferredWidth: 110; model: invoiceVM.factories; placeholder: "غير معروف" }
                            FieldLabel { text: "المقاس" }
                            AmountField { id: manualSize; objectName: "manualSize"; Layout.preferredWidth: 46; placeholderText: ""; inputMethodHints: Qt.ImhNone }
                            Item { Layout.fillWidth: true }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 4
                            FieldLabel { text: "السبب"; Layout.alignment: Qt.AlignTop }
                            // Why the exact piece could not be identified.
                            Flow {
                                Layout.fillWidth: true
                                layoutDirection: Qt.RightToLeft
                                spacing: 4
                                Repeater {
                                    model: invoiceVM.manualReasons
                                    SmallButton {
                                        objectName: "manualReason_" + modelData.code
                                        text: modelData.text
                                        checkedLook: manualPanel.reason === modelData.code
                                        checkedColor: theme.amber
                                        onClicked: manualPanel.reason = modelData.code
                                    }
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            TextInputField { id: manualNotes; objectName: "manualNotes"; Layout.fillWidth: true; placeholderText: text.length > 0 ? "" : manualPanel.reason === "OTHER" ? "اكتب السبب (مطلوب)" : "وصف / ملاحظات (اختياري)"; onAccepted: manualPanel.add() }
                            PrimaryButton { objectName: "manualAdd"; Layout.preferredWidth: 90; text: "إضافة"; onClicked: manualPanel.add() }
                        }
                        Label { id: manualError; objectName: "manualError"; Layout.fillWidth: true; visible: text.length > 0; color: theme.statusWarning; font.family: "Cairo"; font.pixelSize: 11; horizontalAlignment: Text.AlignRight }
                    }
                }

                // ---- F7 بيع من الكسر: weight sold from the old-gold stock (no piece identity)
                Overlay {
                    id: stockPanel
                    objectName: "stockSalePanel"
                    property int karatId: -1
                    function reset() {
                        stockType.currentIndex = -1
                        stockFactory.currentIndex = -1
                        stockDescription.text = ""
                        stockWeight.text = ""
                        stockSize.text = ""
                        stockNotes.text = ""
                        karatId = -1
                        stockError.text = ""
                    }
                    function add() {
                        var typeId = stockType.currentIndex >= 0 ? invoiceVM.itemTypes[stockType.currentIndex].id : 0
                        var factoryId = stockFactory.currentIndex >= 0 ? invoiceVM.factories[stockFactory.currentIndex].id : 0
                        var error = invoiceVM.addOldGoldStockLine(typeId, stockDescription.text, karatId, factoryId,
                                                                  stockWeight.text, stockSize.text, stockNotes.text)
                        stockError.text = error
                        if (error.length === 0) {
                            visible = false
                            invoicePage.focusBarcode()
                        }
                    }
                    anchors.top: parent.top
                    anchors.topMargin: saleEntry.height + 4
                    anchors.right: parent.right
                    anchors.left: parent.left
                    anchors.leftMargin: Math.max(0, parent.width - 520)
                    height: stockColumn.implicitHeight + 20

                    ColumnLayout {
                        id: stockColumn
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 6
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            Label {
                                text: "بيع من الكسر — يُخصم الوزن من رصيد الكسر"
                                color: theme.priceBuy
                                font.family: "Cairo"
                                font.pixelSize: 13
                                font.bold: true
                            }
                            Item { Layout.fillWidth: true }
                            RemoveButton { text: "إغلاق Esc"; onClicked: { stockPanel.visible = false; invoicePage.focusBarcode() } }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            // Entry order: الصنف → العيار → الوزن → المصنع → المقاس → الملاحظات → الوصف → إضافة
                            FieldLabel { text: "الصنف" }
                            ChoiceCombo { id: stockType; objectName: "stockType"; Layout.preferredWidth: 130; model: invoiceVM.itemTypes; placeholder: "اختياري" }
                            Item { Layout.fillWidth: true }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 4
                            FieldLabel { text: "العيار" }
                            Repeater {
                                model: invoiceVM.priceRows
                                KaratChip {
                                    objectName: "stockKarat_" + modelData.karat
                                    text: modelData.karat
                                    selected: stockPanel.karatId === modelData.karatId
                                    onClicked: stockPanel.karatId = modelData.karatId
                                }
                            }
                            Label {
                                objectName: "stockAvailable"
                                text: {
                                    var rows = invoiceVM.oldGoldStock
                                    for (var i = 0; i < rows.length; ++i)
                                        if (rows[i].karatId === stockPanel.karatId)
                                            return "المتاح: " + rows[i].grams
                                    return ""
                                }
                                color: theme.priceBuy
                                font.family: "Cairo"
                                font.pixelSize: 12
                                font.bold: true
                            }
                            Item { Layout.fillWidth: true }
                        }
                        // Weight held for a manager decision is physically in Scrap but not usable (032).
                        Label {
                            objectName: "stockHeldBreakdown"
                            Layout.fillWidth: true
                            visible: text.length > 0
                            text: {
                                var rows = invoiceVM.oldGoldStock
                                for (var i = 0; i < rows.length; ++i)
                                    if (rows[i].karatId === stockPanel.karatId)
                                        return rows[i].breakdown || ""
                                return ""
                            }
                            color: theme.amber
                            font.family: "Cairo"
                            font.pixelSize: 12
                            horizontalAlignment: Text.AlignRight
                            wrapMode: Text.WordWrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            FieldLabel { text: "الوزن" }
                            AmountField { id: stockWeight; objectName: "stockWeight"; Layout.preferredWidth: 70; placeholderText: ""; onAccepted: stockPanel.add() }
                            FieldLabel { text: "المصنع" }
                            ChoiceCombo { id: stockFactory; objectName: "stockFactory"; Layout.preferredWidth: 110; model: invoiceVM.factories; placeholder: "اختياري" }
                            FieldLabel { text: "المقاس" }
                            AmountField { id: stockSize; objectName: "stockSize"; Layout.preferredWidth: 46; placeholderText: ""; inputMethodHints: Qt.ImhNone }
                            Item { Layout.fillWidth: true }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            FieldLabel { text: "ملاحظات" }
                            TextInputField { id: stockNotes; objectName: "stockNotes"; Layout.fillWidth: true; placeholderText: text.length > 0 ? "" : "اختياري"; onAccepted: stockPanel.add() }
                        }
                        // الوصف: the last input, immediately before إضافة.
                        RowLayout {
                            objectName: "stockDescriptionRow"
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            FieldLabel { text: "الوصف" }
                            TextInputField { id: stockDescription; objectName: "stockDescription"; Layout.fillWidth: true; placeholderText: text.length > 0 ? "" : "مثال: دبلة سادة"; onAccepted: stockPanel.add() }
                            PrimaryButton { objectName: "stockAdd"; Layout.preferredWidth: 90; text: "إضافة"; onClicked: stockPanel.add() }
                        }
                        Label { id: stockError; objectName: "stockError"; Layout.fillWidth: true; visible: text.length > 0; color: theme.statusWarning; font.family: "Cairo"; font.pixelSize: 11; horizontalAlignment: Text.AlignRight }
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
                        Layout.preferredHeight: 32
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
                                Layout.leftMargin: 34
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
                                        Cell { Layout.preferredWidth: 2; text: model.weight; font.bold: true }
                                        Cell {
                                            objectName: "oldGoldLineTotal_" + index
                                            Layout.preferredWidth: 3
                                            text: model.lineTotal
                                            color: theme.priceBuy
                                            font.bold: true
                                        }
                                        RemoveButton {
                                            objectName: "removeOldGold_" + index
                                            Layout.preferredWidth: 30
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
                                Layout.margins: 2
                                title: "مشترى"
                                totals: invoiceVM.weightTotals.bought
                                accent: theme.priceBuy
                            }
                        }
                    }
                }
            }
        }

    }

    Dialog {
        id: makingConfirmDialog
        objectName: "makingConfirmDialog"
        modal: true
        anchors.centerIn: parent
        width: Math.min(440, invoicePage.width - 40)
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.panel; radius: 10; border.color: theme.amber; border.width: 1 }
        contentItem: Label {
            objectName: "makingConfirmText"
            text: invoiceVM.makingConfirmText
            color: theme.textPrimary
            font.family: "Cairo"
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignRight
        }
        footer: RowLayout {
            layoutDirection: Qt.RightToLeft
            spacing: 8
            Item { Layout.preferredWidth: 6; Layout.preferredHeight: 48 }
            PrimaryButton {
                objectName: "makingConfirmApprove"
                text: "اعتماد البيع"
                onClicked: { invoiceVM.confirmMaking(); makingConfirmDialog.close() }
            }
            Button {
                objectName: "makingConfirmCancel"
                text: "إلغاء"
                font.family: "Cairo"
                flat: true
                onClicked: makingConfirmDialog.close()
            }
            Item { Layout.fillWidth: true }
        }
    }

    Dialog {
        id: clearInvoiceDialog
        objectName: "clearInvoiceDialog"
        modal: true
        anchors.centerIn: parent
        width: Math.min(420, invoicePage.width - 40)
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.panel; radius: 10; border.color: theme.amber; border.width: 1 }
        contentItem: Label {
            text: "سيتم مسح جميع بيانات الفاتورة الحالية. هل تريد المتابعة؟"
            color: theme.textPrimary
            font.family: "Cairo"
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignRight
        }
        footer: RowLayout {
            layoutDirection: Qt.RightToLeft
            spacing: 8
            Item { Layout.preferredWidth: 6; Layout.preferredHeight: 48 }
            PrimaryButton {
                objectName: "clearInvoiceConfirm"
                text: "تصفير"
                onClicked: { clearInvoiceDialog.close(); invoicePage.startNewInvoice() }
            }
            Button {
                objectName: "clearInvoiceCancel"
                text: "إلغاء"
                font.family: "Cairo"
                flat: true
                onClicked: clearInvoiceDialog.close()
            }
            Item { Layout.fillWidth: true }
        }
    }

    // ---------------------------------------------------- customer suggestions (under the typed field)
    Popup {
        id: suggestionsPopup
        objectName: "customerSuggestions"
        property var anchorField: null
        function openBelow(field) {
            var point = field.mapToItem(invoicePage, 0, field.height + 2)
            x = Math.max(8, Math.min(point.x + field.width - width, invoicePage.width - width - 8))
            y = point.y
            if (invoiceVM.customerSuggestions.length > 0)
                open()
        }
        width: 340
        height: Math.min(suggestionList.contentHeight + 12, 260)
        padding: 6
        focus: false
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        background: Rectangle { color: theme.bgSecondary; border.color: theme.goldDark; radius: 4 }
        Connections {
            target: invoiceVM
            function onChanged() {
                var field = suggestionsPopup.anchorField
                if (suggestionsPopup.opened && invoiceVM.customerSuggestions.length === 0)
                    suggestionsPopup.close()
                else if (!suggestionsPopup.opened && field && field.activeFocus && field.text.length >= 2
                         && invoiceVM.customerSuggestions.length > 0 && invoiceVM.customerInfo.state !== "EXISTING")
                    suggestionsPopup.openBelow(field)
            }
        }
        ListView {
            id: suggestionList
            objectName: "customerSuggestionList"
            anchors.fill: parent
            clip: true
            spacing: 2
            model: invoiceVM.customerSuggestions
            Keys.onReturnPressed: invoicePage.chooseSuggestion(currentIndex)
            delegate: Rectangle {
                objectName: "customerSuggestion_" + index
                width: suggestionList.width
                height: 40
                radius: 3
                color: suggestionList.currentIndex === index && suggestionList.activeFocus ? theme.panelHover : theme.bgTertiary
                MouseArea { anchors.fill: parent; onClicked: invoicePage.chooseSuggestion(index) }
                ColumnLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 8
                    anchors.rightMargin: 8
                    spacing: 0
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        Label { text: modelData.name; color: theme.textPrimary; font.family: "Cairo"; font.pixelSize: 13; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Label { text: modelData.accountText; color: theme.textPrimary; font.family: "Cairo"; font.pixelSize: 12; font.bold: true }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: [modelData.mobile, modelData.place, modelData.code, modelData.deadCash ? "⚠️ سبق شطب رصيد" : ""]
                              .filter(function(v) { return v.length > 0 }).join(" • ")
                        color: modelData.deadCash ? theme.statusWarning : theme.textMuted
                        font.family: "Cairo"
                        font.pixelSize: 10
                        horizontalAlignment: Text.AlignRight
                    }
                }
            }
        }
    }

    // ---------------------------------------------------- manager approval (settlement above the limit)
    Popup {
        id: approvalPopup
        objectName: "approvalPopup"
        modal: true
        anchors.centerIn: parent
        width: 300
        padding: 14
        onOpened: { approverPassword.text = ""; approverUser.forceActiveFocus() }
        background: Rectangle { color: theme.bgSecondary; border.color: theme.amber; radius: theme.radiusSmall }
        ColumnLayout {
            anchors.fill: parent
            spacing: 6
            Label { Layout.fillWidth: true; text: "موافقة المدير على التسوية"; color: theme.amber; font.family: "Cairo"; font.pixelSize: 14; font.bold: true; horizontalAlignment: Text.AlignRight }
            TextInputField { id: approverUser; objectName: "approverUser"; Layout.fillWidth: true; placeholderText: text.length > 0 ? "" : "اسم المستخدم" }
            TextInputField { id: approverPassword; objectName: "approverPassword"; Layout.fillWidth: true; echoMode: TextInput.Password; placeholderText: text.length > 0 ? "" : "كلمة المرور"; onAccepted: approveButton.clicked() }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                PrimaryButton {
                    id: approveButton
                    objectName: "approverConfirm"
                    text: "موافقة"
                    onClicked: {
                        invoiceVM.verifyApprover(approverUser.text, approverPassword.text)
                        approverPassword.text = ""
                        approvalPopup.close()
                    }
                }
                Button { text: "إلغاء"; flat: true; font.family: "Cairo"; onClicked: approvalPopup.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------- one confirmation panel
    Dialog {
        id: confirmDialog
        objectName: "confirmFinalizeDialog"
        readonly property bool acknowledged: (!invoiceVM.requiresPriceAcknowledgement || staleCheck.checked)
                                             && (!invoiceVM.requiresDeadCashConfirmation || deadCashCheck.checked)
        function confirm(wantPrint) {
            if (!acknowledged || !invoiceVM.canFinalize)
                return
            close()
            invoiceVM.finalizeInvoice(staleCheck.checked, deadCashCheck.checked, !!wantPrint)
        }
        title: "حفظ الفاتورة"
        modal: true
        anchors.centerIn: parent
        width: Math.min(460, invoicePage.width - 40)
        padding: 14
        font.family: "Cairo"
        onOpened: { billPrinterMissing.text = ""; confirmButton.forceActiveFocus() }
        onClosed: invoicePage.focusBarcode()
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
                model: [["سعر البيع الفعلي", invoiceVM.saleCount > 0 ? invoiceVM.summary.saleTotal : ""],
                        ["صافي الكسر", invoiceVM.oldGoldCount > 0 ? invoiceVM.summary.oldGoldTotal : ""],
                        ["متوسط المصنعية", invoiceVM.summary.averageMaking],
                        [invoiceVM.summary.paidLabel, invoiceVM.summary.paidAmount],
                        [invoiceVM.settlementChoice === "SETTLE" ? "تنازل من العميل"
                                                                 : invoiceVM.summary.remainingLabel,
                         invoiceVM.settlementChoice === "SETTLE" ? invoiceVM.summary.differenceAmount
                                                                 : invoiceVM.summary.remainingAmount]]
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
                        color: index === 3 ? theme.goldBright : theme.textPrimary
                        font.family: "Cairo"
                        font.pixelSize: index === 3 ? 17 : 14
                        font.bold: true
                    }
                }
            }
            // Old gold only: what was not paid and not put on the customer account is waived by the customer.
            Label {
                objectName: "settlementWaiverWarning"
                Layout.fillWidth: true
                visible: invoiceVM.settlementChoice === "SETTLE" && invoiceVM.hasDifference
                wrapMode: Text.WordWrap
                text: "⚠ الباقي " + invoiceVM.summary.differenceAmount + " ج لن يُدفع للعميل ولن يُسجَّل على حسابه — "
                      + "بالحفظ يُعتبر تنازلًا من العميل عن هذا المبلغ."
                color: theme.amber
                font.family: "Cairo"
                font.pixelSize: 13
                font.bold: true
                horizontalAlignment: Text.AlignRight
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
            // حفظ وطباعة needs a usable Bill printer: said BEFORE anything is saved.
            RowLayout {
                objectName: "billPrinterMissingRow"
                Layout.fillWidth: true
                visible: billPrinterMissing.text.length > 0
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Label {
                    id: billPrinterMissing
                    objectName: "billPrinterMissing"
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    color: theme.statusWarning
                    font.family: "Cairo"
                    font.pixelSize: 12
                    font.bold: true
                    horizontalAlignment: Text.AlignRight
                }
                SmallButton {
                    objectName: "openPrintingSettings"
                    text: "إعدادات الطباعة"
                    font.pixelSize: 11
                    onClicked: { confirmDialog.close(); invoiceVM.openPrintingSettings() }
                }
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
            // Enter keeps meaning exactly what it always meant: save, no side effect.
            // Printing needs an explicit click — never a side effect of mashing Enter.
            PrimaryButton {
                id: confirmButton
                objectName: "confirmFinalize"
                text: invoiceVM.printable ? "حفظ فقط (Enter)" : "حفظ (Enter)"
                Layout.preferredWidth: 150
                enabled: confirmDialog.acknowledged
                onClicked: confirmDialog.confirm(false)
                Keys.onReturnPressed: confirmDialog.confirm(false)
                Keys.onEnterPressed: confirmDialog.confirm(false)
            }
            SmallButton {
                objectName: "confirmSaveAndPrint"
                visible: invoiceVM.printable           // old gold only: save only, there is nothing to print
                text: "حفظ وطباعة"
                Layout.preferredWidth: 100
                enabled: confirmDialog.acknowledged
                onClicked: {
                    // No Bill printer: say so and save nothing (حفظ فقط still works).
                    billPrinterMissing.text = invoiceVM.billPrinterProblem()
                    if (billPrinterMissing.text.length === 0)
                        confirmDialog.confirm(true)
                }
            }
            Button {
                objectName: "confirmBack"
                text: "إلغاء (Esc)"
                font.family: "Cairo"
                flat: true
                onClicked: confirmDialog.close()
            }
            Item { Layout.fillWidth: true }
        }
    }
}
