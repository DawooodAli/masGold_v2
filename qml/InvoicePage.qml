import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Billing V2 (canonical invoices). Every amount shown comes from invoiceVM,
// recalculated by InvoiceService; this page only forwards employee input.
Item {
    id: invoicePage
    objectName: "invoicePage"

    property string pageTitle: ""
    property bool activated: false
    property bool staleAcknowledged: false
    property bool deadCashConfirmed: false

    Theme { id: theme }

    onVisibleChanged: {
        if (visible && !activated) {
            activated = true
            invoiceVM.activate()
        }
    }
    Component.onCompleted: {
        if (visible && !activated) {
            activated = true
            invoiceVM.activate()
        }
    }

    // Stale price -> Dead Cash -> final confirmation -> finalize.
    function startFinalize() {
        staleAcknowledged = false
        deadCashConfirmed = false
        continueFinalize()
    }
    function continueFinalize() {
        if (invoiceVM.requiresPriceAcknowledgement && !staleAcknowledged) {
            stalePriceDialog.open()
            return
        }
        if (invoiceVM.requiresDeadCashConfirmation && !deadCashConfirmed) {
            deadCashDialog.open()
            return
        }
        confirmDialog.open()
    }
    function startNewInvoice() {
        invoiceVM.newInvoice()
        customerPicker.clear()
        barcodeField.text = ""
        oldGoldWeight.text = ""
    }

    Connections {
        target: invoiceVM
        function onFinalized(invoiceNumber) {
            customerPicker.clear()
            // Sold items and bought old gold change other pages, as V1 refreshed them.
            if (typeof inventoryVM !== "undefined" && inventoryVM)
                inventoryVM.refresh()
            if (typeof oldGoldConversionVM !== "undefined" && oldGoldConversionVM)
                oldGoldConversionVM.refreshBalances()
            successDialog.open()
        }
    }

    Rectangle {
        anchors.fill: parent
        color: theme.bgPrimary
    }

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
        Layout.preferredHeight: 38
        color: theme.textPrimary
        placeholderTextColor: theme.textDisabled
        font.family: "Cairo"
        horizontalAlignment: TextInput.AlignRight
        inputMethodHints: Qt.ImhFormattedNumbersOnly
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
            implicitHeight: 40
            radius: theme.radiusSmall
            color: !primary.enabled ? theme.bgTertiary : primary.down ? theme.goldDark : theme.gold
            border.color: primary.enabled ? theme.goldBright : theme.border
            border.width: 1
        }
    }

    component RemoveButton: ToolButton {
        id: removeButton
        text: "حذف"
        font.family: "Cairo"
        font.pixelSize: 11
        contentItem: Label {
            text: removeButton.text
            font: removeButton.font
            color: theme.red
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    component SummaryValue: ColumnLayout {
        property alias title: titleLabel.text
        property alias value: valueLabel.text
        property alias valueColor: valueLabel.color
        property alias valueObjectName: valueLabel.objectName
        spacing: 0
        Layout.fillWidth: true
        Label {
            id: titleLabel
            Layout.fillWidth: true
            color: theme.textMuted
            font.family: "Cairo"
            font.pixelSize: 10
            horizontalAlignment: Text.AlignRight
            elide: Text.ElideRight
        }
        Label {
            id: valueLabel
            Layout.fillWidth: true
            color: theme.textPrimary
            font.family: "Cairo"
            font.pixelSize: 13
            font.bold: true
            horizontalAlignment: Text.AlignRight
            elide: Text.ElideLeft
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 8

        // ---------------------------------------------------- header + price bar
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
                Layout.preferredHeight: 38
                Layout.preferredWidth: priceRow.implicitWidth + 20
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

        // ---------------------------------------------------- scrolling body
        ScrollView {
            id: bodyScroll
            objectName: "invoiceScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth

            ColumnLayout {
                width: bodyScroll.availableWidth
                spacing: 8

                // ---------------- customer + salesperson
                Card {
                    Layout.fillWidth: true
                    Layout.preferredHeight: partiesColumn.implicitHeight + 20

                    ColumnLayout {
                        id: partiesColumn
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 6

                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 10

                            FieldLabel { text: "العميل" }
                            CustomerPicker {
                                id: customerPicker
                                objectName: "invoiceCustomerPicker"
                                Layout.fillWidth: true
                                Layout.maximumWidth: 420
                                placeholderText: "بدون عميل (نقدي)"
                                onCustomerSelected: function(customerId) { invoiceVM.setCustomer(customerId) }
                                onSelectionCleared: invoiceVM.setCustomer("")
                                onSelectionInvalidated: function(customerId, reason) { invoiceVM.setCustomer("") }
                            }

                            FieldLabel { text: "البائع" }
                            ComboBox {
                                id: salespersonCombo
                                objectName: "invoiceSalesperson"
                                Layout.preferredWidth: 190
                                Layout.preferredHeight: 40
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
                            Item { Layout.fillWidth: true }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 10
                            visible: invoiceVM.customerId.length > 0 && invoiceVM.customerBalanceText.length > 0

                            FieldLabel { text: "رصيد العميل الحالي:" }
                            Label {
                                objectName: "customerBalance"
                                text: invoiceVM.customerBalanceText
                                color: theme.textSecondary
                                font.family: "Cairo"
                                font.pixelSize: 12
                            }
                            FieldLabel { text: "بعد الفاتورة:" }
                            Label {
                                objectName: "customerBalanceAfter"
                                text: invoiceVM.customerBalanceAfterText
                                color: theme.textSecondary
                                font.family: "Cairo"
                                font.pixelSize: 12
                            }
                            Item { Layout.fillWidth: true }
                        }

                        Rectangle {
                            objectName: "deadCashBanner"
                            Layout.fillWidth: true
                            Layout.preferredHeight: 32
                            visible: invoiceVM.deadCashWarning
                            color: theme.redBackground
                            radius: theme.radiusSmall
                            border.color: theme.red
                            Label {
                                anchors.fill: parent
                                anchors.margins: 8
                                text: "تحذير: على هذا العميل ديون معدومة سابقة. أي مديونية جديدة تحتاج تأكيداً صريحاً."
                                color: theme.red
                                font.family: "Cairo"
                                font.pixelSize: 12
                                horizontalAlignment: Text.AlignRight
                                verticalAlignment: Text.AlignVCenter
                                elide: Text.ElideLeft
                            }
                        }
                    }
                }

                // ---------------- sale table
                Card {
                    Layout.fillWidth: true
                    Layout.preferredHeight: saleColumn.implicitHeight + 20

                    ColumnLayout {
                        id: saleColumn
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 6

                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 8
                            Label {
                                text: "بيع من المخزون"
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
                                Layout.preferredWidth: 220
                                Layout.preferredHeight: 38
                                placeholderText: "امسح أو اكتب الباركود"
                                color: theme.textPrimary
                                placeholderTextColor: theme.textDisabled
                                font.family: "Cairo"
                                horizontalAlignment: TextInput.AlignRight
                                onAccepted: {
                                    invoiceVM.addBarcode(text)
                                    text = ""
                                }
                            }
                            Button {
                                objectName: "invoiceAddBarcode"
                                text: "إضافة"
                                font.family: "Cairo"
                                enabled: barcodeField.text.length > 0
                                onClicked: {
                                    invoiceVM.addBarcode(barcodeField.text)
                                    barcodeField.text = ""
                                    barcodeField.forceActiveFocus()
                                }
                            }
                        }

                        Repeater {
                            model: invoiceVM.saleLinesModel
                            delegate: Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 46
                                color: theme.bgTertiary
                                radius: theme.radiusSmall

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 8
                                    anchors.rightMargin: 8
                                    layoutDirection: Qt.RightToLeft
                                    spacing: 8

                                    Label {
                                        Layout.preferredWidth: 150
                                        text: model.itemName + "  " + model.barcode
                                        color: theme.textPrimary
                                        font.family: "Cairo"
                                        font.pixelSize: 12
                                        elide: Text.ElideLeft
                                        horizontalAlignment: Text.AlignRight
                                    }
                                    Label {
                                        text: "ع" + model.karat + "  " + model.weight
                                        color: theme.textSecondary
                                        font.family: "Cairo"
                                        font.pixelSize: 11
                                    }
                                    ComboBox {
                                        objectName: "saleMakingMode_" + index
                                        Layout.preferredWidth: 104
                                        Layout.preferredHeight: 36
                                        font.family: "Cairo"
                                        font.pixelSize: 11
                                        model: [{ "value": "PER_GRAM", "text": "مصنعية/جم" },
                                                { "value": "TOTAL", "text": "مصنعية إجمالي" }]
                                        textRole: "text"
                                        valueRole: "value"
                                        currentIndex: model.makingMode === "TOTAL" ? 1 : 0
                                        onActivated: function(i) {
                                            invoiceVM.setSaleMaking(index, i === 1 ? "TOTAL" : "PER_GRAM",
                                                                    makingField.text)
                                        }
                                    }
                                    AmountField {
                                        id: makingField
                                        objectName: "saleMaking_" + index
                                        Layout.preferredWidth: 90
                                        placeholderText: "المصنعية"
                                        text: model.makingValue
                                        onTextEdited: invoiceVM.setSaleMaking(index, model.makingMode, text)
                                    }
                                    Item { Layout.fillWidth: true }
                                    Label {
                                        objectName: "saleLineTotal_" + index
                                        text: model.lineTotal
                                        color: theme.priceSell
                                        font.family: "Cairo"
                                        font.pixelSize: 12
                                        font.bold: true
                                    }
                                    RemoveButton {
                                        objectName: "removeSale_" + index
                                        onClicked: invoiceVM.removeSaleLine(index)
                                    }
                                }
                            }
                        }
                        Label {
                            visible: invoiceVM.saleCount === 0
                            Layout.fillWidth: true
                            text: "لا توجد قطع مباعة."
                            color: theme.textDisabled
                            font.family: "Cairo"
                            font.pixelSize: 11
                            horizontalAlignment: Text.AlignRight
                        }
                    }
                }

                // ---------------- old gold table
                Card {
                    Layout.fillWidth: true
                    Layout.preferredHeight: oldColumn.implicitHeight + 20

                    ColumnLayout {
                        id: oldColumn
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 6

                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 8
                            Label {
                                text: "شراء كسر من العميل"
                                color: theme.priceBuy
                                font.family: "Cairo"
                                font.pixelSize: 14
                                font.bold: true
                            }
                            Label {
                                text: invoiceVM.oldGoldCount + " بند"
                                color: theme.textMuted
                                font.family: "Cairo"
                                font.pixelSize: 11
                            }
                            Item { Layout.fillWidth: true }
                            ComboBox {
                                id: oldGoldKarat
                                objectName: "oldGoldKarat"
                                Layout.preferredWidth: 124
                                Layout.preferredHeight: 38
                                font.family: "Cairo"
                                model: invoiceVM.priceRows
                                textRole: "karat"
                                valueRole: "karatId"
                                displayText: currentIndex < 0 ? "اختر العيار" : "عيار " + currentText
                                // Bound to the view-model: a model reload can never preselect a karat.
                                currentIndex: {
                                    var rows = invoiceVM.priceRows
                                    for (var i = 0; i < rows.length; ++i)
                                        if (rows[i].karatId === invoiceVM.oldGoldKaratId)
                                            return i
                                    return -1
                                }
                                onActivated: function(index) {
                                    invoiceVM.setOldGoldKarat(invoiceVM.priceRows[index].karatId)
                                }
                            }
                            AmountField {
                                id: oldGoldWeight
                                objectName: "oldGoldWeight"
                                Layout.preferredWidth: 110
                                placeholderText: "الوزن (جم)"
                            }
                            Button {
                                objectName: "addOldGold"
                                text: "إضافة"
                                font.family: "Cairo"
                                enabled: invoiceVM.oldGoldKaratId >= 0 && oldGoldWeight.text.length > 0
                                onClicked: {
                                    invoiceVM.addOldGold(invoiceVM.oldGoldKaratId, oldGoldWeight.text)
                                    oldGoldWeight.text = ""
                                }
                            }
                        }

                        Repeater {
                            model: invoiceVM.oldGoldLinesModel
                            delegate: Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 46
                                color: theme.bgTertiary
                                radius: theme.radiusSmall

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 8
                                    anchors.rightMargin: 8
                                    layoutDirection: Qt.RightToLeft
                                    spacing: 8

                                    Label {
                                        text: "ع" + model.karat + "  " + model.weight + " جم"
                                        color: theme.textPrimary
                                        font.family: "Cairo"
                                        font.pixelSize: 12
                                    }
                                    Label {
                                        text: model.grossTotal
                                        color: theme.textSecondary
                                        font.family: "Cairo"
                                        font.pixelSize: 11
                                    }
                                    ComboBox {
                                        objectName: "oldGoldDiscountType_" + index
                                        Layout.preferredWidth: 100
                                        Layout.preferredHeight: 36
                                        font.family: "Cairo"
                                        font.pixelSize: 11
                                        model: [{ "value": "NONE", "text": "بدون خصم" },
                                                { "value": "PERCENT", "text": "خصم %" },
                                                { "value": "AMOUNT", "text": "خصم مبلغ" }]
                                        textRole: "text"
                                        valueRole: "value"
                                        currentIndex: model.discountType === "PERCENT" ? 1
                                                      : model.discountType === "AMOUNT" ? 2 : 0
                                        onActivated: function(i) {
                                            invoiceVM.setOldGoldDiscount(index, ["NONE", "PERCENT", "AMOUNT"][i],
                                                                         discountField.text)
                                        }
                                    }
                                    AmountField {
                                        id: discountField
                                        objectName: "oldGoldDiscountValue_" + index
                                        Layout.preferredWidth: 80
                                        visible: model.discountType !== "NONE"
                                        placeholderText: model.discountType === "PERCENT" ? "%" : "المبلغ"
                                        text: model.discountValue
                                        onTextEdited: invoiceVM.setOldGoldDiscount(index, model.discountType, text)
                                    }
                                    Item { Layout.fillWidth: true }
                                    Label {
                                        objectName: "oldGoldLineTotal_" + index
                                        text: model.lineTotal
                                        color: theme.priceBuy
                                        font.family: "Cairo"
                                        font.pixelSize: 12
                                        font.bold: true
                                    }
                                    RemoveButton {
                                        objectName: "removeOldGold_" + index
                                        onClicked: invoiceVM.removeOldGoldLine(index)
                                    }
                                }
                            }
                        }
                        Label {
                            visible: invoiceVM.oldGoldCount === 0
                            Layout.fillWidth: true
                            text: "لا يوجد كسر مشترى."
                            color: theme.textDisabled
                            font.family: "Cairo"
                            font.pixelSize: 11
                            horizontalAlignment: Text.AlignRight
                        }
                    }
                }

                // ---------------- payments
                Card {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 62

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 10
                        layoutDirection: Qt.RightToLeft
                        spacing: 10

                        Label {
                            text: invoiceVM.paymentLabel
                            color: theme.textPrimary
                            font.family: "Cairo"
                            font.pixelSize: 13
                            font.bold: true
                        }
                        FieldLabel { text: "نقدي" }
                        AmountField {
                            id: cashField
                            objectName: "cashAmountField"
                            Layout.preferredWidth: 140
                            placeholderText: "0.00"
                            text: invoiceVM.cashAmount
                            onTextEdited: invoiceVM.setCashAmount(text)
                        }
                        FieldLabel { text: "محفظة موبايل" }
                        AmountField {
                            id: walletField
                            objectName: "walletAmountField"
                            Layout.preferredWidth: 140
                            placeholderText: "0.00"
                            text: invoiceVM.walletAmount
                            onTextEdited: invoiceVM.setWalletAmount(text)
                        }
                        Item { Layout.fillWidth: true }
                    }
                }

                Label {
                    objectName: "invoiceProblems"
                    Layout.fillWidth: true
                    visible: text.length > 0
                    text: invoiceVM.blockingProblems.length > 0 ? invoiceVM.blockingProblems.join("\n")
                                                                : invoiceVM.previewError
                    color: theme.priceWarning
                    font.family: "Cairo"
                    font.pixelSize: 12
                    horizontalAlignment: Text.AlignRight
                    wrapMode: Text.WordWrap
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

        // ---------------------------------------------------- summary + action (always visible)
        Card {
            objectName: "invoiceSummaryBar"
            Layout.fillWidth: true
            Layout.preferredHeight: 64

            RowLayout {
                anchors.fill: parent
                anchors.margins: 8
                layoutDirection: Qt.RightToLeft
                spacing: 10

                SummaryValue {
                    title: "المبيعات"
                    value: invoiceVM.summary.saleTotal
                    valueColor: theme.priceSell
                    valueObjectName: "summarySale"
                }
                SummaryValue {
                    title: "الكسر"
                    value: invoiceVM.summary.oldGoldTotal
                    valueColor: theme.priceBuy
                    valueObjectName: "summaryOldGold"
                }
                SummaryValue {
                    title: "الصافي"
                    value: invoiceVM.summary.netText
                    valueObjectName: "summaryNet"
                }
                SummaryValue {
                    title: invoiceVM.summary.netDirection === "SHOP_PAYS_CUSTOMER" ? "المدفوع" : "المستلم"
                    value: invoiceVM.summary.netDirection === "SHOP_PAYS_CUSTOMER" ? invoiceVM.summary.paidText
                                                                                  : invoiceVM.summary.receivedText
                    valueObjectName: "summaryPaid"
                }
                SummaryValue {
                    title: "المتبقي"
                    value: invoiceVM.summary.residualText
                    valueColor: invoiceVM.summary.residualDirection === "NONE" ? theme.green : theme.amber
                    valueObjectName: "summaryResidual"
                }
                PrimaryButton {
                    objectName: "finalizeInvoice"
                    Layout.preferredWidth: 130
                    Layout.preferredHeight: 44
                    text: invoiceVM.finalizing ? "جارٍ الحفظ…" : "إتمام الفاتورة"
                    enabled: invoiceVM.canFinalize
                    onClicked: invoicePage.startFinalize()
                }
            }
        }
    }

    // ---------------------------------------------------- dialogs
    component ConfirmDialog: Dialog {
        id: confirmDialogFrame
        padding: 16
        // House dialog frame (as goldPriceDialog in Main.qml): gold title, dark card.
        header: Label {
            text: confirmDialogFrame.title
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
        property alias message: messageLabel.text
        property alias confirmText: confirmButton.text
        property alias confirmObjectName: confirmButton.objectName
        signal confirmed()
        modal: true
        anchors.centerIn: parent
        width: Math.min(460, invoicePage.width - 40)
        font.family: "Cairo"
        // A layout breaks the width -> wrapped height -> implicitHeight loop
        // that a bare word-wrapped Label causes in the Material Dialog.
        contentItem: ColumnLayout {
            Label {
                id: messageLabel
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignRight
                color: theme.textPrimary
                font.family: "Cairo"
                font.pixelSize: 13
            }
        }
        footer: RowLayout {
            layoutDirection: Qt.RightToLeft
            spacing: 8
            Item { Layout.preferredWidth: 8; Layout.preferredHeight: 56 }
            PrimaryButton {
                id: confirmButton
                onClicked: { close(); confirmed() }
            }
            Button {
                text: "رجوع"
                font.family: "Cairo"
                flat: true
                onClicked: close()
            }
            Item { Layout.fillWidth: true }
        }
    }

    ConfirmDialog {
        id: stalePriceDialog
        objectName: "stalePriceDialog"
        title: "تأكيد السعر"
        message: "أسعار الذهب قديمة وليست محدثة. هل تريد إتمام الفاتورة بهذه الأسعار؟"
        confirmText: "متابعة بهذه الأسعار"
        confirmObjectName: "stalePriceConfirm"
        onConfirmed: { invoicePage.staleAcknowledged = true; invoicePage.continueFinalize() }
    }

    ConfirmDialog {
        id: deadCashDialog
        objectName: "deadCashDialog"
        title: "تأكيد مديونية جديدة"
        message: "على هذا العميل ديون معدومة سابقة، والفاتورة ستنشئ مديونية جديدة: "
                 + invoiceVM.summary.residualText + ". هل تريد المتابعة؟"
        confirmText: "تأكيد المديونية"
        confirmObjectName: "deadCashConfirm"
        onConfirmed: { invoicePage.deadCashConfirmed = true; invoicePage.continueFinalize() }
    }

    ConfirmDialog {
        id: confirmDialog
        objectName: "confirmFinalizeDialog"
        title: "إتمام الفاتورة"
        message: "المبيعات: " + invoiceVM.summary.saleTotal
                 + "\nالكسر: " + invoiceVM.summary.oldGoldTotal
                 + "\nالصافي: " + invoiceVM.summary.netText
                 + "\n" + invoiceVM.summary.residualText
                 + "\n\nلا يمكن تعديل الفاتورة بعد الإتمام."
        confirmText: "إتمام وحفظ"
        confirmObjectName: "confirmFinalize"
        onConfirmed: invoiceVM.finalizeInvoice(invoicePage.staleAcknowledged, invoicePage.deadCashConfirmed)
    }

    Dialog {
        id: successDialog
        objectName: "invoiceSuccessDialog"
        modal: true
        anchors.centerIn: parent
        width: Math.min(440, invoicePage.width - 40)
        title: "تم حفظ الفاتورة"
        font.family: "Cairo"
        padding: 16
        // House dialog frame (as goldPriceDialog in Main.qml): gold title, dark card.
        header: Label {
            text: successDialog.title
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
            Label {
                objectName: "successInvoiceNumber"
                Layout.fillWidth: true
                text: "رقم الفاتورة: " + (invoiceVM.success.invoiceNumber || "")
                color: theme.goldBright
                font.family: "Cairo"
                font.pixelSize: 18
                font.bold: true
                horizontalAlignment: Text.AlignRight
            }
            Repeater {
                model: [["المبيعات", invoiceVM.success.saleTotal], ["الكسر", invoiceVM.success.oldGoldTotal],
                        ["الصافي", invoiceVM.success.netText], ["المستلم", invoiceVM.success.receivedText],
                        ["المدفوع", invoiceVM.success.paidText], ["الحالة", invoiceVM.success.residualText]]
                delegate: Label {
                    Layout.fillWidth: true
                    text: modelData[0] + ": " + (modelData[1] || "")
                    color: theme.textPrimary
                    font.family: "Cairo"
                    font.pixelSize: 13
                    horizontalAlignment: Text.AlignRight
                }
            }
        }
        footer: RowLayout {
            layoutDirection: Qt.RightToLeft
            Item { Layout.preferredWidth: 8; Layout.preferredHeight: 56 }
            PrimaryButton {
                objectName: "successNewInvoice"
                text: "فاتورة جديدة"
                onClicked: { successDialog.close(); invoicePage.startNewInvoice() }
            }
            Item { Layout.fillWidth: true }
        }
    }
}
