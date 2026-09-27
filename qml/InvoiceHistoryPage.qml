import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Invoice history and invoice detail (read-only). All text is formatted by
// invoiceHistoryVM; this page only forwards filters and selections.
Item {
    id: historyPage
    objectName: "invoiceHistoryPage"

    property string pageTitle: ""
    property bool activated: false

    Theme { id: theme }

    onVisibleChanged: {
        if (visible && !activated) {
            activated = true
            invoiceHistoryVM.activate()
        }
    }

    function runSearch() {
        invoiceHistoryVM.search(numberField.text, dateFromField.text, dateToField.text,
                                ["ALL", "FINALIZED", "VOIDED"][statusCombo.currentIndex])
    }

    Rectangle {
        anchors.fill: parent
        color: theme.bgPrimary
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 10

        Label {
            Layout.fillWidth: true
            text: historyPage.pageTitle
            color: theme.gold
            font.family: "Cairo"
            font.pixelSize: 20
            font.bold: true
            horizontalAlignment: Text.AlignRight
        }

        // ---------------------------------------------------------- filters
        Card {
            Layout.fillWidth: true
            implicitHeight: filters.implicitHeight + 24

            Flow {
                id: filters
                anchors.fill: parent
                anchors.margins: 12
                layoutDirection: Qt.RightToLeft
                spacing: 10

                FilterBox {
                    label: "رقم الفاتورة"
                    TextField {
                        id: numberField
                        objectName: "historyNumber"
                        width: 130
                        font.family: "Cairo"
                        color: theme.textPrimary
                        placeholderText: "رقم أو جزء منه"
                        placeholderTextColor: theme.textDisabled
                        horizontalAlignment: TextInput.AlignRight
                        selectByMouse: true
                        onAccepted: historyPage.runSearch()
                    }
                }
                FilterBox {
                    label: "العميل"
                    CustomerPicker {
                        id: customerPicker
                        objectName: "historyCustomerPicker"
                        width: 230
                        allowInactive: true
                        placeholderText: "كل العملاء"
                        onCustomerSelected: function(customerId) { invoiceHistoryVM.setCustomer(customerId) }
                        onSelectionCleared: invoiceHistoryVM.setCustomer("")
                        onSelectionInvalidated: function(customerId, reason) { invoiceHistoryVM.setCustomer("") }
                    }
                }
                FilterBox {
                    label: "من تاريخ"
                    DateField { id: dateFromField; objectName: "historyDateFrom" }
                }
                FilterBox {
                    label: "إلى تاريخ"
                    DateField { id: dateToField; objectName: "historyDateTo" }
                }
                FilterBox {
                    label: "الحالة"
                    ComboBox {
                        id: statusCombo
                        objectName: "historyStatus"
                        width: 110
                        font.family: "Cairo"
                        model: ["الكل", "نهائية", "ملغاة"]
                    }
                }
                FilterBox {
                    label: " "
                    PrimaryButton {
                        objectName: "historySearch"
                        width: 90
                        text: "بحث"
                        enabled: !invoiceHistoryVM.busy
                        onClicked: historyPage.runSearch()
                    }
                }
            }
        }

        Label {
            Layout.fillWidth: true
            visible: invoiceHistoryVM.errorMessage !== ""
            objectName: "historyError"
            text: invoiceHistoryVM.errorMessage
            color: theme.red
            font.family: "Cairo"
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignRight
        }

        // ---------------------------------------------------------- results
        Card {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 10
                spacing: 4

                ResultRow {
                    Layout.fillWidth: true
                    header: true
                    cells: ["رقم الفاتورة", "التاريخ", "النوع", "العميل", "البائع", "الصافي", "الحالة"]
                }

                ListView {
                    id: resultsList
                    objectName: "historyResults"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 2
                    model: invoiceHistoryVM.results
                    ScrollBar.vertical: ScrollBar {}

                    delegate: ItemDelegate {
                        id: rowDelegate
                        objectName: "historyRow_" + modelData.number
                        width: resultsList.width
                        height: modelData.isLegacy ? 52 : 38
                        padding: 0
                        onClicked: invoiceHistoryVM.openInvoice(modelData.invoiceId)
                        background: Rectangle {
                            color: rowDelegate.hovered ? theme.panelHover : theme.bgTertiary
                            radius: 4
                        }
                        contentItem: ColumnLayout {
                            spacing: 0
                            ResultRow {
                                Layout.fillWidth: true
                                cells: [modelData.number, modelData.date, modelData.typeText, modelData.customerText,
                                        modelData.salesperson, modelData.netText, modelData.statusText]
                            }
                            Label {
                                Layout.fillWidth: true
                                Layout.rightMargin: 8
                                visible: modelData.isLegacy
                                text: modelData.legacyLabel
                                color: theme.amber
                                font.family: "Cairo"
                                font.pixelSize: 11
                                horizontalAlignment: Text.AlignRight
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    layoutDirection: Qt.RightToLeft
                    spacing: 8

                    Label {
                        objectName: "historyPageText"
                        text: invoiceHistoryVM.busy ? "جارٍ التحميل…" : invoiceHistoryVM.pageText
                        color: theme.textMuted
                        font.family: "Cairo"
                    }
                    Item { Layout.fillWidth: true }
                    Button {
                        objectName: "historyPrevious"
                        text: "السابق"
                        font.family: "Cairo"
                        enabled: invoiceHistoryVM.hasPrevious && !invoiceHistoryVM.busy
                        onClicked: invoiceHistoryVM.previousPage()
                    }
                    Button {
                        objectName: "historyNext"
                        text: "التالي"
                        font.family: "Cairo"
                        enabled: invoiceHistoryVM.hasMore && !invoiceHistoryVM.busy
                        onClicked: invoiceHistoryVM.nextPage()
                    }
                }
            }
        }
    }

    // -------------------------------------------------------------- detail
    Connections {
        target: invoiceHistoryVM
        function onChanged() {
            if (invoiceHistoryVM.detailOpen && !detailDialog.opened && !detailDialog.visible)
                detailDialog.open()
            else if (!invoiceHistoryVM.detailOpen && detailDialog.visible)
                detailDialog.close()
        }
    }

    Dialog {
        id: detailDialog
        objectName: "invoiceDetailDialog"
        readonly property var d: invoiceHistoryVM.detail
        modal: true
        anchors.centerIn: parent
        width: Math.min(historyPage.width - 24, 920)
        height: historyPage.height - 24
        padding: 16
        closePolicy: Popup.CloseOnEscape
        onClosed: if (invoiceHistoryVM.detailOpen) invoiceHistoryVM.closeDetail()
        header: Label {
            text: detailDialog.d.number ? "فاتورة رقم " + detailDialog.d.number : "تفاصيل الفاتورة"
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
        footer: RowLayout {
            layoutDirection: Qt.RightToLeft
            Item { Layout.fillWidth: true }
            Button {
                objectName: "invoiceDetailClose"
                Layout.margins: 12
                text: "إغلاق"
                font.family: "Cairo"
                onClicked: invoiceHistoryVM.closeDetail()
            }
        }

        ScrollView {
            id: detailScroll
            objectName: "invoiceDetailScroll"
            anchors.fill: parent
            clip: true
            contentWidth: availableWidth

            ColumnLayout {
                width: detailScroll.availableWidth
                spacing: 10

                Label {
                    Layout.fillWidth: true
                    visible: invoiceHistoryVM.busy && !detailDialog.d.number
                    text: "جارٍ تحميل الفاتورة…"
                    color: theme.textMuted
                    font.family: "Cairo"
                    horizontalAlignment: Text.AlignRight
                }
                Label {
                    Layout.fillWidth: true
                    objectName: "invoiceDetailError"
                    visible: invoiceHistoryVM.detailError !== ""
                    text: invoiceHistoryVM.detailError
                    color: theme.red
                    font.family: "Cairo"
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignRight
                }

                Rectangle {
                    Layout.fillWidth: true
                    objectName: "invoiceDetailLegacy"
                    visible: detailDialog.d.isLegacy === true
                    implicitHeight: legacyText.implicitHeight + 14
                    color: "transparent"
                    border.color: theme.amber
                    radius: theme.radiusSmall
                    Label {
                        id: legacyText
                        anchors.fill: parent
                        anchors.margins: 7
                        text: (detailDialog.d.legacyLabel || "")
                              + (detailDialog.d.legacyNumber ? " • رقم النظام القديم " + detailDialog.d.legacyNumber : "")
                              + " • للعرض فقط"
                        color: theme.amber
                        font.family: "Cairo"
                        font.bold: true
                        wrapMode: Text.WordWrap
                        horizontalAlignment: Text.AlignRight
                    }
                }

                Section {
                    title: "بيانات الفاتورة"
                    visible: !!detailDialog.d.number
                    InfoGrid {
                        items: [["رقم الفاتورة", detailDialog.d.number], ["التاريخ", detailDialog.d.date],
                                ["النوع", detailDialog.d.typeText], ["الحالة", detailDialog.d.statusText],
                                ["البائع", detailDialog.d.salesperson], ["التسوية", detailDialog.d.settlementText],
                                ["أنشأها", detailDialog.d.createdBy], ["أتمها", detailDialog.d.finalizedBy],
                                ["وقت الإتمام", detailDialog.d.finalizedAt], ["ملاحظات", detailDialog.d.notes]]
                    }
                }

                Section {
                    title: "العميل (وقت الفاتورة)"
                    visible: !!detailDialog.d.number
                    InfoGrid {
                        items: [["العميل", detailDialog.d.customerName], ["الكود", detailDialog.d.customerCode],
                                ["الموبايل", detailDialog.d.customerMobile], ["المكان", detailDialog.d.customerPlace],
                                ["الرصيد قبل", detailDialog.d.balanceBefore], ["الرصيد بعد", detailDialog.d.balanceAfter]]
                    }
                }

                Section {
                    objectName: "invoiceDetailSaleLines"
                    title: "أصناف البيع"
                    visible: invoiceHistoryVM.saleLines.length > 0
                    ResultRow { Layout.fillWidth: true; header: true
                        cells: ["الباركود", "الصنف", "العيار", "الوزن", "سعر الجرام", "المصنعية", "الإجمالي"] }
                    Repeater {
                        model: invoiceHistoryVM.saleLines
                        ResultRow { Layout.fillWidth: true
                            cells: [modelData.barcode, modelData.itemName, modelData.karat, modelData.weight,
                                    modelData.goldPrice, modelData.making, modelData.lineTotal] }
                    }
                }

                Section {
                    objectName: "invoiceDetailOldGold"
                    title: "كسر مشترى"
                    visible: invoiceHistoryVM.oldGoldLines.length > 0
                    ResultRow { Layout.fillWidth: true; header: true
                        cells: ["العيار", "الوزن", "سعر الجرام", "القيمة", "الخصم", "الإجمالي"] }
                    Repeater {
                        model: invoiceHistoryVM.oldGoldLines
                        ResultRow { Layout.fillWidth: true
                            cells: [modelData.karat, modelData.weight, modelData.goldPrice, modelData.gross,
                                    modelData.discount, modelData.lineTotal] }
                    }
                }

                Section {
                    title: "بنود أخرى"
                    visible: invoiceHistoryVM.otherLines.length > 0
                    Repeater {
                        model: invoiceHistoryVM.otherLines
                        ResultRow { Layout.fillWidth: true
                            cells: [modelData.itemName, modelData.karat, modelData.weight, modelData.lineTotal] }
                    }
                }

                Section {
                    objectName: "invoiceDetailTotals"
                    title: "الإجماليات والتسوية"
                    visible: !!detailDialog.d.number
                    InfoGrid {
                        items: [["إجمالي البيع", detailDialog.d.saleTotal], ["إجمالي الكسر", detailDialog.d.oldGoldTotal],
                                ["الصافي (N)", detailDialog.d.netText], ["المستلم", detailDialog.d.receivedText],
                                ["المدفوع", detailDialog.d.paidText], ["المتبقي (R)", detailDialog.d.residualText]]
                    }
                }

                Section {
                    objectName: "invoiceDetailPayments"
                    title: "المدفوعات"
                    visible: !!detailDialog.d.number
                    EmptyNote { visible: invoiceHistoryVM.payments.length === 0; text: "لا توجد مدفوعات نقدية." }
                    Repeater {
                        model: invoiceHistoryVM.payments
                        ResultRow { Layout.fillWidth: true
                            cells: [modelData.method, modelData.directionText, modelData.amount, modelData.time,
                                    modelData.reference] }
                    }
                }

                Section {
                    objectName: "invoiceDetailPrices"
                    title: "أسعار الذهب وقت الفاتورة"
                    visible: !!detailDialog.d.number
                    EmptyNote { visible: invoiceHistoryVM.prices.length === 0; text: "لا توجد أسعار محفوظة." }
                    Repeater {
                        model: invoiceHistoryVM.prices
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 14
                            Label { text: "عيار " + modelData.karat; color: theme.textPrimary; font.family: "Cairo"; font.bold: true }
                            Label { text: "شراء " + modelData.buy; color: theme.priceBuy; font.family: "Cairo" }
                            Label { text: "بيع " + modelData.sell; color: theme.priceSell; font.family: "Cairo" }
                            Label { text: modelData.source; color: theme.textMuted; font.family: "Cairo" }
                            Label { Layout.fillWidth: true; text: modelData.providerTime; color: theme.textMuted
                                    font.family: "Cairo"; font.pixelSize: 11; horizontalAlignment: Text.AlignRight
                                    elide: Text.ElideLeft }
                        }
                    }
                }

                Section {
                    objectName: "invoiceDetailEvents"
                    title: "سجل الأحداث"
                    visible: !!detailDialog.d.number
                    Repeater {
                        model: invoiceHistoryVM.events
                        ResultRow { Layout.fillWidth: true
                            cells: [modelData.eventText, modelData.time, modelData.by, modelData.notes] }
                    }
                }
            }
        }
    }

    // -------------------------------------------------------------- components
    component Card: Rectangle {
        color: theme.bgSecondary
        radius: theme.radiusSmall
        border.color: theme.border
        border.width: 1
    }

    component FilterBox: Column {
        id: filterBox
        property string label: ""
        spacing: 2
        Label {
            anchors.right: parent.right
            text: filterBox.label
            color: theme.textMuted
            font.family: "Cairo"
            font.pixelSize: 11
        }
    }

    component DateField: TextField {
        width: 118
        font.family: "Cairo"
        color: theme.textPrimary
        placeholderText: "2026-09-25"
        placeholderTextColor: theme.textDisabled
        horizontalAlignment: TextInput.AlignRight
        selectByMouse: true
        onAccepted: historyPage.runSearch()
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
        }
        background: Rectangle {
            implicitHeight: 40
            radius: theme.radiusSmall
            color: !primary.enabled ? theme.bgTertiary : primary.down ? theme.goldDark : theme.gold
            border.color: primary.enabled ? theme.goldBright : theme.border
            border.width: 1
        }
    }

    component ResultRow: RowLayout {
        id: resultRow
        property var cells: []
        property bool header: false
        layoutDirection: Qt.RightToLeft
        spacing: 6
        Repeater {
            model: resultRow.cells
            Label {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.leftMargin: 4
                Layout.rightMargin: 4
                text: modelData === undefined || modelData === null ? "" : String(modelData)
                color: resultRow.header ? theme.textMuted : theme.textPrimary
                font.family: "Cairo"
                font.pixelSize: resultRow.header ? 11 : 12
                font.bold: resultRow.header
                elide: Text.ElideLeft
                horizontalAlignment: Text.AlignRight
            }
        }
    }

    component Section: ColumnLayout {
        id: section
        property string title: ""
        Layout.fillWidth: true
        spacing: 4
        Label {
            Layout.fillWidth: true
            text: section.title
            color: theme.gold
            font.family: "Cairo"
            font.bold: true
            horizontalAlignment: Text.AlignRight
        }
    }

    component EmptyNote: Label {
        Layout.fillWidth: true
        color: theme.textMuted
        font.family: "Cairo"
        horizontalAlignment: Text.AlignRight
    }

    component InfoGrid: GridLayout {
        id: infoGrid
        property var items: []
        Layout.fillWidth: true
        columns: width > 620 ? 3 : 2
        layoutDirection: Qt.RightToLeft
        columnSpacing: 16
        rowSpacing: 4
        Repeater {
            model: infoGrid.items
            RowLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                layoutDirection: Qt.RightToLeft
                spacing: 6
                Label { text: modelData[0]; color: theme.textMuted; font.family: "Cairo"; font.pixelSize: 11 }
                Label {
                    Layout.fillWidth: true
                    text: modelData[1] === undefined || modelData[1] === "" ? "—" : modelData[1]
                    color: theme.textPrimary
                    font.family: "Cairo"
                    elide: Text.ElideLeft
                    horizontalAlignment: Text.AlignRight
                }
            }
        }
    }
}
