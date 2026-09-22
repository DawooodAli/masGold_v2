import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ApplicationWindow {
    id: root

    visible: true

    width: 1280
    height: 800

    minimumWidth: 1080
    minimumHeight: 680

    title: "مجوهرات ماس"

    // Global application font. Qt Quick Controls inherit this font,
    // including Button, ComboBox popup items, TextField, TextArea and Label.
    font.family: "Cairo"
    font.pixelSize: 12

    color: theme.bgPrimary


    // ========================================================
    // QT CONTROLS THEME
    // Keep application background unchanged
    // ========================================================

    Material.theme: Material.Dark

    Material.primary:
        theme.goldDark

    Material.accent:
        theme.gold

    Material.foreground:
        theme.textPrimary


    palette.button:
        theme.bgTertiary

    palette.buttonText:
        theme.textPrimary

    palette.text:
        theme.textPrimary

    palette.placeholderText:
        theme.textMuted

    palette.highlight:
        theme.gold

    palette.highlightedText:
        theme.bgPrimary
    property int currentPage: 0

    // UI rule: Arabic text uses Cairo and is right-aligned/RTL in controls.
    // Do not enable global LayoutMirroring because the physical sidebar must stay on the right.

    // Selected inventory record for the read-only details dialog.
    property var selectedInventoryItem: ({})


    Theme {
        id: theme
    }

    // ========================================================
    // CATEGORY MODEL FOR ITEM TYPES
    // ========================================================

    ListModel {
        id: categoryChoiceModel
    }


    function rebuildCategoryChoiceModel() {

        categoryChoiceModel.clear()

        categoryChoiceModel.append({
            "categoryId": 0,
            "label": "بدون فئة افتراضية"
        })

        var rows = masterDataVM.categories

        for (var i = 0; i < rows.length; i++) {

            var row = rows[i]

            var labelText = row.code

            if (row.name && row.name !== row.code) {
                labelText += " — " + row.name
            }

            categoryChoiceModel.append({
                "categoryId": Number(row.id),
                "label": labelText
            })
        }
    }


    function categoryIndexById(categoryId) {

        var wantedId = Number(categoryId || 0)

        for (
            var i = 0;
            i < categoryChoiceModel.count;
            i++
        ) {

            if (
                Number(
                    categoryChoiceModel
                    .get(i)
                    .categoryId
                ) === wantedId
            ) {
                return i
            }
        }

        return 0
    }


    // ========================================================
    // MAIN LAYOUT
    // ========================================================

    RowLayout {
        anchors.fill: parent
        spacing: 0


        // ====================================================
        // MAIN CONTENT
        // ====================================================

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true

            spacing: 0


            // =================================================
            // TOP BAR
            // =================================================

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 68

                color: theme.bgSecondary

                border.color: theme.border
                border.width: 1


                RowLayout {
                    anchors.fill: parent

                    anchors.leftMargin: 20
                    anchors.rightMargin: 20

                    spacing: 12


                    Label {
                        text:
                            pageStack.currentItem
                            ? pageStack.currentItem.pageTitle
                            : ""

                        color: theme.gold

                        font.family: "Cairo"
                        font.pixelSize: 20
                        font.bold: true

                        horizontalAlignment:
                            Text.AlignRight
                    }


                    Item {
                        Layout.fillWidth: true
                    }


                    Rectangle {
                        Layout.preferredWidth: 220
                        Layout.preferredHeight: 42

                        radius: theme.radiusSmall

                        color: theme.bgTertiary

                        border.color: theme.border
                        border.width: 1


                        Row {
                            anchors.centerIn: parent

                            spacing: 10


                            Label {
                                text: "عيار 21"

                                color: theme.gold

                                font.family: "Cairo"
                                font.bold: true
                                font.pixelSize: 13
                            }


                            Label {
                                text: "السعر غير متاح"

                                color: theme.textMuted

                                font.family: "Cairo"
                                font.pixelSize: 12
                            }
                        }
                    }


                    Rectangle {
                        width: 9
                        height: 9

                        radius: 5

                        color:
                            masterDataVM.errorMessage.length > 0
                            ? theme.red
                            : theme.green
                    }


                    Label {
                        text:
                            masterDataVM.errorMessage.length > 0
                            ? "خطأ في الاتصال"
                            : "متصل"

                        color:
                            masterDataVM.errorMessage.length > 0
                            ? theme.red
                            : theme.textMuted

                        font.family: "Cairo"
                        font.pixelSize: 12
                    }
                }
            }
            // ========================================================
            // INVENTORY PAGE
            // ========================================================

            // ========================================================
    // BILLING PAGE
    // Unified sale + old-gold purchase in one invoice.
    // Inspired by the prototype's two-sided bill, rebuilt for v2 ledger rules.
    // ========================================================

    component BillingPage: Item {
        id: billingPage

        property string pageTitle: ""

        function money(value) {
            var number = Number(value || 0)
            return number.toLocaleString(
                Qt.locale("en_US"),
                "f",
                2
            ) + " ج"
        }

        function weight(value) {
            return Number(value || 0).toFixed(3) + " جم"
        }

        function syncPrices() {
            billingVM.updateBasePrices(
                billingSellPrice21.text,
                billingBuyPrice21.text
            )
        }

        Rectangle {
            anchors.fill: parent
            color: theme.bgPrimary
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 18
            spacing: 10

            // ----------------------------------------------------
            // HEADER + CUSTOMER + EMPLOYEE + BASE PRICES
            // ----------------------------------------------------

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 142

                color: theme.bgSecondary
                radius: theme.radiusSmall
                border.color: theme.border
                border.width: 1

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 13
                    spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 12

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 0

                            Label {
                                Layout.fillWidth: true
                                text: "🧾 الفواتير"
                                color: theme.gold
                                font.family: "Cairo"
                                font.pixelSize: 19
                                font.bold: true
                                horizontalAlignment: Text.AlignRight
                            }

                            Label {
                                Layout.fillWidth: true
                                text: "بيع بضاعة وشراء كسر من العميل داخل نفس الفاتورة"
                                color: theme.textMuted
                                font.family: "Cairo"
                                font.pixelSize: 10
                                horizontalAlignment: Text.AlignRight
                            }
                        }

                        Label {
                            text:
                                billingVM.lastInvoiceCode.length > 0
                                ? "آخر فاتورة: " + billingVM.lastInvoiceCode
                                : "فاتورة جديدة"
                            color: theme.textSecondary
                            font.family: "Cairo"
                            font.bold: true
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 10

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3

                            Label {
                                Layout.fillWidth: true
                                text: "اسم العميل *"
                                color: theme.textSecondary
                                font.family: "Cairo"
                                horizontalAlignment: Text.AlignRight
                            }

                            TextField {
                                id: billingCustomerName
                                Layout.fillWidth: true
                                Layout.preferredHeight: 40
                                placeholderText: "اسم العميل"
                                color: theme.textPrimary
                                placeholderTextColor: theme.textDisabled
                                font.family: "Cairo"
                                horizontalAlignment: TextInput.AlignRight
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3

                            Label {
                                Layout.fillWidth: true
                                text: "الموظف المسؤول *"
                                color: theme.textSecondary
                                font.family: "Cairo"
                                horizontalAlignment: Text.AlignRight
                            }

                            ComboBox {
                                id: billingEmployeeCombo
                                Layout.fillWidth: true
                                Layout.preferredHeight: 40
                                model: masterDataVM.employees
                                textRole: "name"
                                valueRole: "id"
                                currentIndex: -1
                                displayText:
                                    currentIndex >= 0
                                    ? currentText
                                    : "اختر الموظف"
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3

                            Label {
                                Layout.fillWidth: true
                                text: "سعر بيع 21 / جم *"
                                color: theme.green
                                font.family: "Cairo"
                                horizontalAlignment: Text.AlignRight
                            }

                            TextField {
                                id: billingSellPrice21
                                Layout.fillWidth: true
                                Layout.preferredHeight: 40
                                placeholderText: "مثال: 6500"
                                color: theme.textPrimary
                                placeholderTextColor: theme.textDisabled
                                font.family: "Cairo"
                                horizontalAlignment: TextInput.AlignRight
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                onEditingFinished: billingPage.syncPrices()
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3

                            Label {
                                Layout.fillWidth: true
                                text: "سعر شراء 21 / جم *"
                                color: theme.amber
                                font.family: "Cairo"
                                horizontalAlignment: Text.AlignRight
                            }

                            TextField {
                                id: billingBuyPrice21
                                Layout.fillWidth: true
                                Layout.preferredHeight: 40
                                placeholderText: "مثال: 6400"
                                color: theme.textPrimary
                                placeholderTextColor: theme.textDisabled
                                font.family: "Cairo"
                                horizontalAlignment: TextInput.AlignRight
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                onEditingFinished: billingPage.syncPrices()
                            }
                        }
                    }
                }
            }

            // ----------------------------------------------------
            // TWO SIDES OF THE SAME BILL
            // ----------------------------------------------------

            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                layoutDirection: Qt.RightToLeft
                spacing: 10

                // =================================================
                // SALE SIDE
                // =================================================

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    color: theme.bgSecondary
                    radius: theme.radiusSmall
                    border.color: theme.border
                    border.width: 1

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 8

                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft

                            Label {
                                Layout.fillWidth: true
                                text: "بيع للعميل"
                                color: theme.green
                                font.family: "Cairo"
                                font.pixelSize: 15
                                font.bold: true
                                horizontalAlignment: Text.AlignRight
                            }

                            Label {
                                text: billingVM.saleCount + " قطعة"
                                color: theme.textMuted
                                font.family: "Cairo"
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 7

                            TextField {
                                id: billingBarcodeField
                                Layout.fillWidth: true
                                Layout.preferredHeight: 42
                                placeholderText: "امسح أو اكتب الباركود"
                                color: theme.textPrimary
                                placeholderTextColor: theme.textDisabled
                                font.family: "Cairo"
                                horizontalAlignment: TextInput.AlignRight

                                onAccepted: {
                                    billingPage.syncPrices()
                                    if (
                                        billingVM.addSaleByBarcode(
                                            text,
                                            billingSellPrice21.text
                                        )
                                    ) {
                                        text = ""
                                    }
                                }
                            }

                            Button {
                                text: "＋ إضافة"
                                font.family: "Cairo"
                                onClicked: {
                                    billingPage.syncPrices()
                                    if (
                                        billingVM.addSaleByBarcode(
                                            billingBarcodeField.text,
                                            billingSellPrice21.text
                                        )
                                    ) {
                                        billingBarcodeField.text = ""
                                        billingBarcodeField.forceActiveFocus()
                                    }
                                }
                            }
                        }

                        Label {
                            Layout.fillWidth: true
                            text:
                                "سعر 18 و24 يُحسب تلقائياً كنسبة من سعر 21، كما في منطق النموذج الأصلي."
                            color: theme.textDisabled
                            font.family: "Cairo"
                            font.pixelSize: 9
                            horizontalAlignment: Text.AlignRight
                            wrapMode: Text.WordWrap
                        }

                        ListView {
                            id: billingSaleList
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            spacing: 6
                            model: billingVM.saleLines

                            delegate: Rectangle {
                                required property var modelData
                                required property int index

                                width: billingSaleList.width
                                height: 82
                                color: theme.bgTertiary
                                radius: 6
                                border.color: theme.border
                                border.width: 1

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.margins: 9
                                    layoutDirection: Qt.RightToLeft
                                    spacing: 8

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 1

                                        Label {
                                            Layout.fillWidth: true
                                            text:
                                                (modelData.item_type_name || "قطعة")
                                                + " — عيار "
                                                + modelData.karat_value
                                            color: theme.textPrimary
                                            font.family: "Cairo"
                                            font.bold: true
                                            horizontalAlignment: Text.AlignRight
                                        }

                                        Label {
                                            Layout.fillWidth: true
                                            text:
                                                "#" + modelData.inventory_item_id
                                                + "  •  "
                                                + billingPage.weight(modelData.weight_grams)
                                                + "  •  "
                                                + (modelData.barcode_value || "بدون باركود")
                                            color: theme.textMuted
                                            font.family: "Cairo"
                                            font.pixelSize: 9
                                            horizontalAlignment: Text.AlignRight
                                        }

                                        Label {
                                            Layout.fillWidth: true
                                            text:
                                                "ذهب/جم: "
                                                + billingPage.money(modelData.gold_price_per_gram)
                                                + "   •   الإجمالي: "
                                                + billingPage.money(modelData.line_total)
                                            color: theme.green
                                            font.family: "Cairo"
                                            font.pixelSize: 10
                                            horizontalAlignment: Text.AlignRight
                                        }
                                    }

                                    ColumnLayout {
                                        Layout.preferredWidth: 118
                                        spacing: 2

                                        Label {
                                            Layout.fillWidth: true
                                            text: "مصنعية / جم"
                                            color: theme.textMuted
                                            font.family: "Cairo"
                                            font.pixelSize: 9
                                            horizontalAlignment: Text.AlignRight
                                        }

                                        TextField {
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 34
                                            text: Number(modelData.making_per_gram || 0).toFixed(2)
                                            color: theme.textPrimary
                                            font.family: "Cairo"
                                            horizontalAlignment: TextInput.AlignRight
                                            inputMethodHints: Qt.ImhFormattedNumbersOnly
                                            onEditingFinished:
                                                billingVM.setSaleMakingPerGram(
                                                    index,
                                                    text
                                                )
                                        }
                                    }

                                    Button {
                                        text: "✕"
                                        flat: true
                                        font.family: "Cairo"
                                        onClicked:
                                            billingVM.removeSaleLine(index)
                                    }
                                }
                            }
                        }

                        Label {
                            visible: billingVM.saleCount === 0
                            Layout.fillWidth: true
                            text: "لا توجد قطع مباعة في الفاتورة حتى الآن."
                            color: theme.textDisabled
                            font.family: "Cairo"
                            horizontalAlignment: Text.AlignHCenter
                        }
                    }
                }

                // =================================================
                // OLD GOLD BUY SIDE
                // =================================================

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    color: theme.bgSecondary
                    radius: theme.radiusSmall
                    border.color: theme.border
                    border.width: 1

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 8

                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft

                            Label {
                                Layout.fillWidth: true
                                text: "شراء كسر من العميل"
                                color: theme.amber
                                font.family: "Cairo"
                                font.pixelSize: 15
                                font.bold: true
                                horizontalAlignment: Text.AlignRight
                            }

                            Label {
                                text: billingVM.oldGoldCount + " حركة"
                                color: theme.textMuted
                                font.family: "Cairo"
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 7

                            ComboBox {
                                id: billingOldGoldKarat
                                Layout.preferredWidth: 130
                                Layout.preferredHeight: 42
                                model: masterDataVM.karats
                                textRole: "name"
                                valueRole: "id"
                                currentIndex: -1
                                displayText:
                                    currentIndex >= 0
                                    ? currentText
                                    : "العيار"
                            }

                            TextField {
                                id: billingOldGoldWeight
                                Layout.fillWidth: true
                                Layout.preferredHeight: 42
                                placeholderText: "الوزن بالجرام"
                                color: theme.textPrimary
                                placeholderTextColor: theme.textDisabled
                                font.family: "Cairo"
                                horizontalAlignment: TextInput.AlignRight
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                            }

                            Button {
                                text: "＋ كسر"
                                font.family: "Cairo"
                                onClicked: {
                                    billingPage.syncPrices()

                                    var karatId =
                                        billingOldGoldKarat.currentIndex >= 0
                                        ? Number(billingOldGoldKarat.currentValue)
                                        : 0

                                    if (
                                        billingVM.addOldGold(
                                            karatId,
                                            billingOldGoldWeight.text,
                                            billingBuyPrice21.text,
                                            billingOldGoldNotes.text
                                        )
                                    ) {
                                        billingOldGoldWeight.text = ""
                                        billingOldGoldNotes.text = ""
                                        billingOldGoldWeight.forceActiveFocus()
                                    }
                                }
                            }
                        }

                        TextField {
                            id: billingOldGoldNotes
                            Layout.fillWidth: true
                            Layout.preferredHeight: 38
                            placeholderText: "ملاحظات الكسر — اختياري"
                            color: theme.textPrimary
                            placeholderTextColor: theme.textDisabled
                            font.family: "Cairo"
                            horizontalAlignment: TextInput.AlignRight
                        }

                        Label {
                            Layout.fillWidth: true
                            text:
                                "الكسر يُسجل بالجرامات فقط. لا باركود ولا مصنع ولا صنف."
                            color: theme.textDisabled
                            font.family: "Cairo"
                            font.pixelSize: 9
                            horizontalAlignment: Text.AlignRight
                        }

                        ListView {
                            id: billingOldGoldList
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            spacing: 6
                            model: billingVM.oldGoldLines

                            delegate: Rectangle {
                                required property var modelData
                                required property int index

                                width: billingOldGoldList.width
                                height: 74
                                color: theme.bgTertiary
                                radius: 6
                                border.color: theme.border
                                border.width: 1

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.margins: 9
                                    layoutDirection: Qt.RightToLeft
                                    spacing: 8

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 1

                                        Label {
                                            Layout.fillWidth: true
                                            text:
                                                "كسر عيار "
                                                + modelData.karat_value
                                                + " — "
                                                + billingPage.weight(modelData.weight_grams)
                                            color: theme.textPrimary
                                            font.family: "Cairo"
                                            font.bold: true
                                            horizontalAlignment: Text.AlignRight
                                        }

                                        Label {
                                            Layout.fillWidth: true
                                            text:
                                                "شراء/جم: "
                                                + billingPage.money(modelData.buy_price_per_gram)
                                                + "   •   القيمة: "
                                                + billingPage.money(modelData.line_total)
                                            color: theme.amber
                                            font.family: "Cairo"
                                            font.pixelSize: 10
                                            horizontalAlignment: Text.AlignRight
                                        }

                                        Label {
                                            visible:
                                                String(modelData.notes || "").length > 0
                                            Layout.fillWidth: true
                                            text: modelData.notes || ""
                                            color: theme.textMuted
                                            font.family: "Cairo"
                                            font.pixelSize: 9
                                            horizontalAlignment: Text.AlignRight
                                        }
                                    }

                                    Button {
                                        text: "✕"
                                        flat: true
                                        font.family: "Cairo"
                                        onClicked:
                                            billingVM.removeOldGoldLine(index)
                                    }
                                }
                            }
                        }

                        Label {
                            visible: billingVM.oldGoldCount === 0
                            Layout.fillWidth: true
                            text: "لا يوجد كسر مشتَرَى في الفاتورة حتى الآن."
                            color: theme.textDisabled
                            font.family: "Cairo"
                            horizontalAlignment: Text.AlignHCenter
                        }
                    }
                }
            }

            // ----------------------------------------------------
            // SETTLEMENT / NET
            // ----------------------------------------------------

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 122

                color: theme.bgSecondary
                radius: theme.radiusSmall
                border.color:
                    billingVM.netDue > 0
                    ? theme.green
                    : billingVM.netDue < 0
                      ? theme.amber
                      : theme.border
                border.width: 1

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    layoutDirection: Qt.RightToLeft
                    spacing: 14

                    ColumnLayout {
                        Layout.preferredWidth: 185
                        spacing: 1
                        Label {
                            Layout.fillWidth: true
                            text: "إجمالي البيع"
                            color: theme.textMuted
                            font.family: "Cairo"
                            horizontalAlignment: Text.AlignRight
                        }
                        Label {
                            Layout.fillWidth: true
                            text: billingPage.money(billingVM.saleTotal)
                            color: theme.green
                            font.family: "Cairo"
                            font.pixelSize: 18
                            font.bold: true
                            horizontalAlignment: Text.AlignRight
                        }
                    }

                    ColumnLayout {
                        Layout.preferredWidth: 185
                        spacing: 1
                        Label {
                            Layout.fillWidth: true
                            text: "إجمالي شراء الكسر"
                            color: theme.textMuted
                            font.family: "Cairo"
                            horizontalAlignment: Text.AlignRight
                        }
                        Label {
                            Layout.fillWidth: true
                            text: billingPage.money(billingVM.oldGoldTotal)
                            color: theme.amber
                            font.family: "Cairo"
                            font.pixelSize: 18
                            font.bold: true
                            horizontalAlignment: Text.AlignRight
                        }
                    }

                    Rectangle {
                        Layout.preferredWidth: 1
                        Layout.fillHeight: true
                        Layout.topMargin: 12
                        Layout.bottomMargin: 12
                        color: theme.border
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1

                        Label {
                            Layout.fillWidth: true
                            text: "الصافي"
                            color: theme.textMuted
                            font.family: "Cairo"
                            horizontalAlignment: Text.AlignRight
                        }

                        Label {
                            Layout.fillWidth: true
                            text:
                                billingVM.netDue > 0
                                ? "على العميل  " + billingPage.money(billingVM.netDue)
                                : billingVM.netDue < 0
                                  ? "لصالح العميل  " + billingPage.money(Math.abs(billingVM.netDue))
                                  : "الحساب متعادل"
                            color:
                                billingVM.netDue > 0
                                ? theme.green
                                : billingVM.netDue < 0
                                  ? theme.amber
                                  : theme.gold
                            font.family: "Cairo"
                            font.pixelSize: 20
                            font.bold: true
                            horizontalAlignment: Text.AlignRight
                        }

                        TextField {
                            id: billingInvoiceNotes
                            Layout.fillWidth: true
                            Layout.preferredHeight: 36
                            placeholderText: "ملاحظات الفاتورة — اختياري"
                            color: theme.textPrimary
                            placeholderTextColor: theme.textDisabled
                            font.family: "Cairo"
                            horizontalAlignment: TextInput.AlignRight
                        }
                    }

                    ColumnLayout {
                        Layout.preferredWidth: 180
                        spacing: 6

                        Button {
                            Layout.fillWidth: true
                            text: "💾 حفظ الفاتورة"
                            font.family: "Cairo"
                            font.bold: true
                            enabled:
                                !billingVM.busy
                                && masterDataVM.employees.length > 0

                            onClicked: {
                                billingPage.syncPrices()

                                var employeeId =
                                    billingEmployeeCombo.currentIndex >= 0
                                    ? Number(billingEmployeeCombo.currentValue)
                                    : 0

                                var saved = billingVM.finalizeInvoice(
                                    billingCustomerName.text,
                                    employeeId,
                                    billingSellPrice21.text,
                                    billingBuyPrice21.text,
                                    billingInvoiceNotes.text
                                )

                                if (saved) {
                                    inventoryVM.refresh()
                                    oldGoldConversionVM.refreshBalances()
                                    billingBarcodeField.text = ""
                                    billingOldGoldWeight.text = ""
                                    billingOldGoldNotes.text = ""
                                    billingInvoiceNotes.text = ""
                                    billingCustomerName.text = ""
                                    billingEmployeeCombo.currentIndex = -1
                                }
                            }
                        }

                        Button {
                            Layout.fillWidth: true
                            text: "مسح المسودة"
                            font.family: "Cairo"
                            onClicked: billingVM.clearDraft()
                        }
                    }
                }
            }

            Label {
                visible: billingVM.actionMessage.length > 0
                Layout.fillWidth: true
                text: billingVM.actionMessage
                color:
                    billingVM.actionSuccess
                    ? theme.green
                    : theme.red
                font.family: "Cairo"
                font.bold: true
                horizontalAlignment: Text.AlignRight
                wrapMode: Text.WordWrap
            }
        }

        Component.onCompleted: {
            masterDataVM.refresh()
            billingVM.clearActionMessage()
        }
    }


    component InventoryPage: Item {
                id: inventoryPage

                property string pageTitle: ""

                // Bulk barcode selection.
                property var selectedItemIds: []

                // Inventory list filters used for bulk barcode work.
                // Empty date = all dates.
                property string entryDateFilter: ""
                // 0 = all, 1 = without barcode, 2 = with barcode.
                property int barcodeFilterMode: 0


                function pad2(value) {
                    return value < 10
                        ? "0" + value
                        : String(value)
                }


                function todayText() {
                    var now = new Date()

                    return (
                        now.getFullYear()
                        + "-"
                        + pad2(now.getMonth() + 1)
                        + "-"
                        + pad2(now.getDate())
                    )
                }


                function dateOnly(value) {
                    if (
                        value === null
                        || value === undefined
                    ) {
                        return ""
                    }

                    var textValue =
                        String(value)

                    return textValue.length >= 10
                        ? textValue.substring(0, 10)
                        : textValue
                }


                function filteredItems() {
                    var result = []
                    var source = inventoryVM.items

                    for (
                        var index = 0;
                        index < source.length;
                        index++
                    ) {
                        var item = source[index]

                        if (
                            entryDateFilter.length > 0
                            && dateOnly(
                                item.acquired_at
                            ) !== entryDateFilter
                        ) {
                            continue
                        }

                        if (
                            barcodeFilterMode === 1
                            && item.barcode_value
                        ) {
                            continue
                        }

                        if (
                            barcodeFilterMode === 2
                            && !item.barcode_value
                        ) {
                            continue
                        }

                        result.push(item)
                    }

                    return result
                }


                function itemId(value) {
                    return Number(value || 0)
                }


                function isSelected(value) {
                    return selectedItemIds.indexOf(
                        itemId(value)
                    ) >= 0
                }


                function setSelected(value, selected) {
                    var idValue = itemId(value)

                    if (idValue <= 0) {
                        return
                    }

                    var copy =
                        selectedItemIds.slice()

                    var index =
                        copy.indexOf(idValue)

                    if (
                        selected
                        && index < 0
                    ) {
                        copy.push(idValue)
                    }

                    if (
                        !selected
                        && index >= 0
                    ) {
                        copy.splice(index, 1)
                    }

                    selectedItemIds = copy
                }


                function clearSelection() {
                    selectedItemIds = []
                }


                function selectAllVisible() {
                    var ids = []
                    var items = filteredItems()

                    for (
                        var index = 0;
                        index < items.length;
                        index++
                    ) {
                        ids.push(
                            itemId(
                                items[index]
                                .inventory_item_id
                            )
                        )
                    }

                    selectedItemIds = ids
                }


                function selectWithoutBarcode() {
                    barcodeFilterMode = 1

                    var ids = []
                    var items = filteredItems()

                    for (
                        var index = 0;
                        index < items.length;
                        index++
                    ) {
                        ids.push(
                            itemId(
                                items[index]
                                .inventory_item_id
                            )
                        )
                    }

                    selectedItemIds = ids
                }


                function openBarcodeQueue() {
                    if (
                        selectedItemIds.length === 0
                    ) {
                        return
                    }

                    if (
                        barcodeVM.prepareQueue(
                            selectedItemIds
                        )
                    ) {
                        barcodePrintQueueDialog.open()
                    }
                }


                function weightText(value) {

                    var number = Number(value || 0)

                    return number.toFixed(3) + " جم"
                }


                Rectangle {
                    anchors.fill: parent
                    color: theme.bgPrimary
                }


                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 22
                    spacing: 14


                    // =================================================
                    // HEADER
                    // =================================================

                    RowLayout {
                        Layout.fillWidth: true


                        ColumnLayout {
                            spacing: 2


                            Label {
                                Layout.fillWidth: true

                                text: "📦 إدارة المخزون"

                                color: theme.gold

                                font.family: "Cairo"
                                font.pixelSize: 20
                                font.bold: true

                                horizontalAlignment:
                                    Text.AlignRight
                            }


                            Label {
                                Layout.fillWidth: true

                                text:
                                    "البضاعة الجديدة والقديمة والقطع بدون باركود"

                                color: theme.textMuted

                                font.family: "Cairo"
                                font.pixelSize: 12

                                horizontalAlignment:
                                    Text.AlignRight
                            }
                        }


                        Item {
                            Layout.fillWidth: true
                        }


                        Button {
                            text: "＋  إدخال قطعة جديدة"

                            font.family: "Cairo"
                            font.bold: true

                            onClicked:
                                newInventoryItemDialog.openForNewItem()
                        }


                        Button {
                            text: "↻  تحديث"

                            font.family: "Cairo"

                            onClicked:
                                inventoryVM.refresh()
                        }
                    }


                    // =================================================
                    // SUMMARY
                    // =================================================

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10


                        SummaryBox {
                            title: "إجمالي القطع"

                            value:
                                inventoryVM.itemCount.toString()

                            accentColor:
                                theme.gold
                        }


                        SummaryBox {
                            title: "بضاعة جديدة"

                            value:
                                inventoryVM.newStockCount.toString()

                            accentColor:
                                theme.green
                        }


                        SummaryBox {
                            title: "بضاعة قديمة"

                            value:
                                inventoryVM.oldStockCount.toString()

                            accentColor:
                                theme.blue
                        }


                        SummaryBox {
                            title: "بدون باركود"

                            value:
                                inventoryVM.withoutBarcodeCount.toString()

                            accentColor:
                                inventoryVM.withoutBarcodeCount > 0
                                ? theme.amber
                                : theme.textMuted
                        }
                    }


                    // =================================================
                    // TOTAL WEIGHT
                    // =================================================

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 56

                        color: theme.bgSecondary

                        radius: theme.radiusSmall

                        border.color: theme.border
                        border.width: 1


                        RowLayout {
                            anchors.fill: parent

                            anchors.leftMargin: 16
                            anchors.rightMargin: 16


                            Label {
                                text:
                                    "إجمالي وزن المخزون"

                                color: theme.textMuted

                                font.family: "Cairo"
                            }


                            Item {
                                Layout.fillWidth: true
                            }


                            Label {
                                text:
                                    inventoryPage.weightText(
                                        inventoryVM.totalWeight
                                    )

                                color: theme.gold

                                font.family: "Cairo"
                                font.pixelSize: 18
                                font.bold: true
                            }
                        }
                    }


                    // =================================================
                    // SEARCH
                    // =================================================

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 66

                        color: theme.bgSecondary

                        radius: theme.radiusSmall

                        border.color: theme.border
                        border.width: 1


                        RowLayout {
                            anchors.fill: parent

                            anchors.leftMargin: 12
                            anchors.rightMargin: 12

                            spacing: 8


                            TextField {
                                id: inventorySearchField

                                Layout.fillWidth: true
                                Layout.preferredHeight: 44

                                placeholderText:
                                    "ابحث بالباركود، رقم القطعة، الوزن، الصنف، المصنع أو الفئة..."

                                font.family: "Cairo"

                                horizontalAlignment:
                                    TextInput.AlignRight

                                onAccepted:
                                    inventoryVM.search(text)
                            }


                            Button {
                                text: "🔍  بحث"

                                font.family: "Cairo"

                                onClicked:
                                    inventoryVM.search(
                                        inventorySearchField.text
                                    )
                            }


                            Button {
                                text: "مسح"

                                font.family: "Cairo"

                                enabled:
                                    inventorySearchField.text.length > 0

                                onClicked: {

                                    inventorySearchField.text = ""

                                    inventoryVM.clearSearch()
                                }
                            }
                        }
                    }


                    // =================================================
                    // BULK BARCODE PRINT
                    // =================================================

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 118

                        color: theme.bgSecondary

                        radius: theme.radiusSmall

                        border.color:
                            inventoryPage.selectedItemIds.length > 0
                            ? theme.goldDark
                            : theme.border

                        border.width: 1


                        ColumnLayout {
                            anchors.fill: parent

                            anchors.leftMargin: 12
                            anchors.rightMargin: 12
                            anchors.topMargin: 9
                            anchors.bottomMargin: 9

                            spacing: 7


                            // -----------------------------------------
                            // DATE + BARCODE FILTERS
                            // -----------------------------------------

                            RowLayout {
                                Layout.fillWidth: true

                                layoutDirection:
                                    Qt.RightToLeft

                                spacing: 8


                                Label {
                                    text: "تاريخ إدخال القطعة"

                                    color: theme.textSecondary

                                    font.family: "Cairo"
                                    font.pixelSize: 11

                                    horizontalAlignment:
                                        Text.AlignRight
                                }


                                TextField {
                                    id: entryDateFilterField

                                    Layout.preferredWidth: 145
                                    Layout.preferredHeight: 38

                                    text:
                                        inventoryPage.entryDateFilter

                                    placeholderText:
                                        "YYYY-MM-DD"

                                    font.family: "Cairo"

                                    horizontalAlignment:
                                        TextInput.AlignRight

                                    onEditingFinished: {
                                        inventoryPage.entryDateFilter =
                                            text.trim()

                                        inventoryPage.clearSelection()
                                    }
                                }


                                Button {
                                    text: "اليوم"

                                    font.family: "Cairo"

                                    onClicked: {
                                        inventoryPage.entryDateFilter =
                                            inventoryPage.todayText()

                                        entryDateFilterField.text =
                                            inventoryPage.entryDateFilter

                                        inventoryPage.clearSelection()
                                    }
                                }


                                Button {
                                    text: "كل التواريخ"

                                    font.family: "Cairo"

                                    onClicked: {
                                        inventoryPage.entryDateFilter = ""
                                        entryDateFilterField.text = ""
                                        inventoryPage.clearSelection()
                                    }
                                }


                                Label {
                                    text: "حالة الباركود"

                                    color: theme.textSecondary

                                    font.family: "Cairo"
                                    font.pixelSize: 11

                                    horizontalAlignment:
                                        Text.AlignRight
                                }


                                ComboBox {
                                    id: inventoryBarcodeFilter

                                    Layout.preferredWidth: 150
                                    Layout.preferredHeight: 38

                                    model: [
                                        "الكل",
                                        "بدون باركود",
                                        "بباركود"
                                    ]

                                    currentIndex:
                                        inventoryPage.barcodeFilterMode

                                    font.family: "Cairo"

                                    onActivated: {
                                        inventoryPage.barcodeFilterMode =
                                            currentIndex

                                        inventoryPage.clearSelection()
                                    }
                                }


                                Item {
                                    Layout.fillWidth: true
                                }


                                Label {
                                    text:
                                        "الظاهر: "
                                        + inventoryPage.filteredItems().length
                                        + " قطعة"

                                    color: theme.textMuted

                                    font.family: "Cairo"
                                    font.pixelSize: 10

                                    horizontalAlignment:
                                        Text.AlignRight
                                }


                                Button {
                                    text: "🧾 سجل الطباعة"

                                    font.family: "Cairo"

                                    onClicked:
                                        barcodePrintHistoryDialog.openForToday()
                                }
                            }


                            // -----------------------------------------
                            // SELECTION ACTIONS
                            // -----------------------------------------

                            RowLayout {
                                Layout.fillWidth: true

                                layoutDirection:
                                    Qt.RightToLeft

                                spacing: 8


                                Button {
                                    text: "تحديد الظاهر"

                                    font.family: "Cairo"

                                    enabled:
                                        inventoryPage.filteredItems().length > 0

                                    onClicked:
                                        inventoryPage.selectAllVisible()
                                }


                                Button {
                                    text: "تحديد بدون باركود"

                                    font.family: "Cairo"

                                    enabled:
                                        inventoryPage.filteredItems().length > 0

                                    onClicked:
                                        inventoryPage.selectWithoutBarcode()
                                }


                                Button {
                                    text: "إلغاء التحديد"

                                    font.family: "Cairo"

                                    enabled:
                                        inventoryPage.selectedItemIds.length > 0

                                    onClicked:
                                        inventoryPage.clearSelection()
                                }


                                Item {
                                    Layout.fillWidth: true
                                }


                                Label {
                                    text:
                                        "تم تحديد "
                                        + inventoryPage.selectedItemIds.length
                                        + " قطعة"

                                    color:
                                        inventoryPage.selectedItemIds.length > 0
                                        ? theme.gold
                                        : theme.textMuted

                                    font.family: "Cairo"
                                    font.bold:
                                        inventoryPage.selectedItemIds.length > 0

                                    horizontalAlignment:
                                        Text.AlignRight
                                }


                                Button {
                                    text: "🖨  معاينة وطباعة"

                                    font.family: "Cairo"
                                    font.bold: true

                                    enabled:
                                        inventoryPage.selectedItemIds.length > 0
                                        && !barcodeVM.busy

                                    onClicked:
                                        inventoryPage.openBarcodeQueue()
                                }
                            }
                        }
                    }


                    // =================================================
                    // ERROR
                    // =================================================

                    Rectangle {
                        visible:
                            inventoryVM.errorMessage.length > 0

                        Layout.fillWidth: true
                        Layout.preferredHeight: 48

                        radius: theme.radiusSmall

                        color: theme.redBackground

                        border.color: theme.red
                        border.width: 1


                        Label {
                            anchors.fill: parent
                            anchors.margins: 10

                            text:
                                inventoryVM.errorMessage

                            color: theme.red

                            font.family: "Cairo"

                            horizontalAlignment:
                                Text.AlignRight

                            verticalAlignment:
                                Text.AlignVCenter
                        }
                    }


                    // =================================================
                    // LIST PANEL
                    // =================================================

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                        color: theme.bgSecondary

                        radius: theme.radiusSmall

                        border.color: theme.border
                        border.width: 1


                        // ---------------------------------------------
                        // EMPTY STATE
                        // ---------------------------------------------

                        ColumnLayout {
                            visible:
                                inventoryPage.filteredItems().length === 0
                                && inventoryVM.errorMessage.length === 0

                            anchors.centerIn: parent

                            spacing: 8


                            Label {
                                text: "📦"

                                font.pixelSize: 40

                                Layout.alignment:
                                    Qt.AlignHCenter
                            }


                            Label {
                                text:
                                    (
                                        inventorySearchField.text.length > 0
                                        || inventoryPage.entryDateFilter.length > 0
                                        || inventoryPage.barcodeFilterMode !== 0
                                    )
                                    ? "لا توجد قطع مطابقة للفلاتر الحالية"
                                    : "المخزون فارغ حالياً"

                                color: theme.textMuted

                                font.family: "Cairo"
                                font.pixelSize: 14

                                Layout.alignment:
                                    Qt.AlignHCenter
                            }


                            Label {
                                visible:
                                    inventorySearchField.text.length === 0
                                    && inventoryPage.entryDateFilter.length === 0
                                    && inventoryPage.barcodeFilterMode === 0

                                text:
                                    "سيتم تسجيل القطع هنا عند بدء إدخال المخزون."

                                color: theme.textDisabled

                                font.family: "Cairo"
                                font.pixelSize: 11

                                Layout.alignment:
                                    Qt.AlignHCenter
                            }
                        }


                        // ---------------------------------------------
                        // ITEMS
                        // ---------------------------------------------

                        ListView {
                            id: inventoryList

                            anchors.fill: parent
                            anchors.margins: 12

                            clip: true
                            spacing: 8

                            model:
                                inventoryPage.filteredItems()


                            delegate: Rectangle {
                                id: inventoryRow

                                required property var modelData

                                width:
                                    inventoryList.width

                                height: 116

                                color:
                                    inventoryMouse.containsMouse
                                    ? theme.panelHover
                                    : theme.bgTertiary

                                radius:
                                    theme.radiusSmall

                                border.color:
                                    inventoryPage.isSelected(
                                        modelData.inventory_item_id
                                    )
                                    ? theme.gold
                                    : theme.border

                                border.width:
                                    inventoryPage.isSelected(
                                        modelData.inventory_item_id
                                    )
                                    ? 2
                                    : 1


                                RowLayout {
                                    anchors.fill: parent

                                    anchors.leftMargin: 16
                                    anchors.rightMargin: 58

                                    spacing: 14


                                    // =================================
                                    // ITEM ID
                                    // =================================

                                    Rectangle {
                                        Layout.preferredWidth: 64
                                        Layout.preferredHeight: 64

                                        radius: 8

                                        color:
                                            theme.bgSecondary

                                        border.color:
                                            theme.goldDark

                                        border.width: 1


                                        Column {
                                            anchors.centerIn: parent

                                            spacing: 1


                                            Label {
                                                anchors.horizontalCenter:
                                                    parent.horizontalCenter

                                                text: "#"

                                                color:
                                                    theme.textDisabled

                                                font.family: "Cairo"
                                                font.pixelSize: 10
                                            }


                                            Label {
                                                anchors.horizontalCenter:
                                                    parent.horizontalCenter

                                                text:
                                                    modelData.inventory_item_id

                                                color:
                                                    theme.gold

                                                font.family: "Cairo"
                                                font.pixelSize: 16
                                                font.bold: true
                                            }
                                        }
                                    }


                                    // =================================
                                    // MAIN INFO
                                    // =================================

                                    ColumnLayout {
                                        Layout.fillWidth: true

                                        spacing: 2


                                        Label {
                                            Layout.fillWidth: true

                                            text:
                                                modelData.item_type_name
                                                || "صنف غير محدد"

                                            color:
                                                theme.textPrimary

                                            font.family: "Cairo"
                                            font.pixelSize: 14
                                            font.bold: true

                                            horizontalAlignment:
                                                Text.AlignRight
                                        }


                                        Label {
                                            Layout.fillWidth: true

                                            text:
                                                "عيار "
                                                + (
                                                    modelData.karat_value
                                                    || "—"
                                                )
                                                + "   •   "
                                                + inventoryPage.weightText(
                                                    modelData.weight_grams
                                                )

                                            color:
                                                theme.textSecondary

                                            font.family: "Cairo"
                                            font.pixelSize: 11

                                            horizontalAlignment:
                                                Text.AlignRight
                                        }


                                        Label {
                                            Layout.fillWidth: true

                                            text:
                                                "المصنع: "
                                                + (
                                                    modelData.factory_name
                                                    || "—"
                                                )
                                                + "   •   الفئة: "
                                                + (
                                                    modelData.category_code
                                                    || "—"
                                                )

                                            color:
                                                theme.textMuted

                                            font.family: "Cairo"
                                            font.pixelSize: 11

                                            horizontalAlignment:
                                                Text.AlignRight
                                        }


                                        Label {
                                            visible:
                                                modelData.size !== null
                                                && modelData.size !== undefined
                                                && String(
                                                    modelData.size
                                                ).length > 0

                                            Layout.fillWidth: true

                                            text:
                                                "المقاس: "
                                                + modelData.size

                                            color:
                                                theme.textDisabled

                                            font.family: "Cairo"
                                            font.pixelSize: 10

                                            horizontalAlignment:
                                                Text.AlignRight
                                        }
                                    }


                                    // =================================
                                    // STOCK CLASS
                                    // =================================

                                    Rectangle {
                                        Layout.preferredWidth: 110
                                        Layout.preferredHeight: 32

                                        radius: 6

                                        color:
                                            modelData.stock_classification
                                            === "OLD_STOCK"
                                            ? "#29241A"
                                            : "#16271F"

                                        border.color:
                                            modelData.stock_classification
                                            === "OLD_STOCK"
                                            ? theme.amber
                                            : theme.green

                                        border.width: 1


                                        Label {
                                            anchors.centerIn: parent

                                            text:
                                                modelData.stock_classification
                                                === "OLD_STOCK"
                                                ? "بضاعة قديمة"
                                                : "بضاعة جديدة"

                                            color:
                                                modelData.stock_classification
                                                === "OLD_STOCK"
                                                ? theme.amber
                                                : theme.green

                                            font.family: "Cairo"
                                            font.pixelSize: 10
                                            font.bold: true
                                        }
                                    }


                                    // =================================
                                    // BARCODE STATE
                                    // =================================

                                    Rectangle {
                                        Layout.preferredWidth: 155
                                        Layout.preferredHeight: 46

                                        radius: 6

                                        color:
                                            modelData.barcode_value
                                            ? theme.bgSecondary
                                            : "#29241A"

                                        border.color:
                                            modelData.barcode_value
                                            ? theme.border
                                            : theme.amber

                                        border.width: 1


                                        Column {
                                            anchors.centerIn: parent

                                            spacing: 1


                                            Label {
                                                anchors.horizontalCenter:
                                                    parent.horizontalCenter

                                                text:
                                                    modelData.barcode_value
                                                    ? "الباركود"
                                                    : "⚠ بدون باركود"

                                                color:
                                                    modelData.barcode_value
                                                    ? theme.textMuted
                                                    : theme.amber

                                                font.family: "Cairo"
                                                font.pixelSize: 9
                                                font.bold:
                                                    !modelData.barcode_value
                                            }


                                            Label {
                                                visible:
                                                    Boolean(
                                                        modelData.barcode_value
                                                    )

                                                anchors.horizontalCenter:
                                                    parent.horizontalCenter

                                                text:
                                                    modelData.barcode_value
                                                    || ""

                                                color:
                                                    theme.textPrimary

                                                font.family: "Cairo"
                                                font.pixelSize: 11
                                            }
                                        }
                                    }
                                }


                                MouseArea {
                                    id: inventoryMouse

                                    anchors.fill: parent

                                    hoverEnabled: true

                                    cursorShape:
                                        Qt.PointingHandCursor

                                    onClicked: {
                                        root.selectedInventoryItem = modelData
                                        inventoryItemDetailsDialog.open()
                                    }
                                }


                                CheckBox {
                                    id: inventorySelectBox

                                    z: 5

                                    anchors.right:
                                        parent.right

                                    anchors.rightMargin: 14

                                    anchors.verticalCenter:
                                        parent.verticalCenter

                                    checked:
                                        inventoryPage.isSelected(
                                            modelData.inventory_item_id
                                        )

                                    onToggled:
                                        inventoryPage.setSelected(
                                            modelData.inventory_item_id,
                                            checked
                                        )

                                    ToolTip.visible:
                                        hovered

                                    ToolTip.text:
                                        checked
                                        ? "إلغاء تحديد القطعة"
                                        : "تحديد للطباعة"
                                }
                            }
                        }
                    }
                }
            }

            // =================================================
            // PAGES
            // =================================================

            StackLayout {
                id: pageStack

                Layout.fillWidth: true
                Layout.fillHeight: true

                currentIndex:
                    root.currentPage


                PlaceholderPage {
                    pageTitle: "🏠 الرئيسية"
                    description: "ملخص حركة المحل اليوم"
                }


                BillingPage {
                    pageTitle: "🧾 الفواتير"
                }


                InventoryPage {
                    pageTitle: "📦 المخزون"
                }


                PlaceholderPage {
                    pageTitle: "👥 العملاء"
                    description: "بيانات العملاء والحسابات والحركات"
                }


                PlaceholderPage {
                    pageTitle: "💰 النقدية"
                    description: "النقدية الفعلية والبنوك والحركات"
                }


                PlaceholderPage {
                    pageTitle: "📊 التقارير"
                    description: "التحليل والرسوم البيانية والتوقعات"
                }


                SettingsPage {
                    pageTitle: "⚙️ الإعدادات"
                }


                FactoriesPage {
                    pageTitle: "🏭 المصانع"
                }


                ItemTypesPage {
                    pageTitle: "💍 الأصناف"
                }
                CategoriesPage {
                    pageTitle: "🏷️ الفئات"}

            }
        }


        // ====================================================
        // RIGHT SIDEBAR
        // ====================================================

        Rectangle {
            Layout.preferredWidth: 235
            Layout.fillHeight: true

            color: theme.bgSecondary

            border.color: theme.border
            border.width: 1


            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 7


                Label {
                    text: "مجوهرات ماس"

                    color: theme.gold

                    font.family: "Cairo"
                    font.pixelSize: 23
                    font.bold: true

                    horizontalAlignment: Text.AlignHCenter

                    Layout.fillWidth: true
                    Layout.topMargin: 15
                }


                Label {
                    text: "ثقة الدهب فى مكانها "

                    color: theme.textMuted

                    font.family: "Cairo"
                    font.pixelSize: 11

                    horizontalAlignment: Text.AlignHCenter

                    Layout.fillWidth: true
                    Layout.bottomMargin: 22
                }


                NavButton {
                    text: "🏠  الرئيسية"
                    selected: root.currentPage === 0

                    onClicked:
                        root.currentPage = 0
                }


                NavButton {
                    text: "🧾  الفواتير"
                    selected: root.currentPage === 1

                    onClicked:
                        root.currentPage = 1
                }


                NavButton {
                    text: "📦  المخزون"
                    selected: root.currentPage === 2

                    onClicked:
                        root.currentPage = 2
                }


                NavButton {
                    text: "👥  العملاء"
                    selected: root.currentPage === 3

                    onClicked:
                        root.currentPage = 3
                }


                NavButton {
                    text: "💰  النقدية"
                    selected: root.currentPage === 4

                    onClicked:
                        root.currentPage = 4
                }


                NavButton {
                    text: "📊  التقارير"
                    selected: root.currentPage === 5

                    onClicked:
                        root.currentPage = 5
                }


                Item {
                    Layout.fillHeight: true
                }


                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 1

                    color: theme.border

                    Layout.bottomMargin: 5
                }


                NavButton {
                    text: "⚙️  الإعدادات"

                    selected:
                        root.currentPage === 6
                        || root.currentPage === 7
                        || root.currentPage === 8
                        || root.currentPage === 9
                    onClicked:
                        root.currentPage = 6
                }


                Label {
                    text: "masGold v2"

                    color: theme.textDisabled

                    font.family: "Cairo"
                    font.pixelSize: 10

                    horizontalAlignment: Text.AlignHCenter

                    Layout.fillWidth: true
                    Layout.topMargin: 8
                    Layout.bottomMargin: 4
                }
            }
        }
    }


    // ========================================================
    // NAV BUTTON
    // ========================================================

    component NavButton: Button {

        property bool selected: false

        Layout.fillWidth: true
        Layout.preferredHeight: 44

        flat: true
        hoverEnabled: true


        contentItem: Label {

            text: parent.text

            color:
                parent.selected
                ? theme.goldBright
                : theme.textSecondary

            font.family: "Cairo"
            font.pixelSize: 14
            font.bold: parent.selected

            horizontalAlignment: Text.AlignRight
            verticalAlignment: Text.AlignVCenter

            rightPadding: 14
        }


        background: Rectangle {

            radius: theme.radiusSmall

            color:
                parent.selected
                ? theme.bgTertiary
                : parent.hovered
                    ? theme.panelHover
                    : "transparent"

            border.width:
                parent.selected
                ? 1
                : 0

            border.color:
                parent.selected
                ? theme.goldDark
                : "transparent"


            Rectangle {
                visible: parent.parent.selected

                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter

                width: 3
                height: parent.height - 12

                radius: 2

                color: theme.gold
            }
        }
    }


    // ========================================================
    // PLACEHOLDER PAGE
    // ========================================================

    component PlaceholderPage: Item {

        property string pageTitle: ""
        property string description: ""


        Rectangle {
            anchors.fill: parent
            color: theme.bgPrimary
        }


        ColumnLayout {
            anchors.centerIn: parent
            spacing: 10


            Label {
                text: pageTitle

                color: theme.gold

                font.family: "Cairo"
                font.pixelSize: 27
                font.bold: true

                Layout.alignment: Qt.AlignHCenter
            }


            Label {
                text: description

                color: theme.textMuted

                font.family: "Cairo"
                font.pixelSize: 14

                Layout.alignment: Qt.AlignHCenter
            }
        }
    }


    // ========================================================
    // SETTINGS PAGE
    // ========================================================

    component SettingsPage: Item {

        property string pageTitle: ""


        Rectangle {
            anchors.fill: parent
            color: theme.bgPrimary
        }


        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 22
            spacing: 14


            Label {
                text: "⚙️ البيانات الأساسية"

                color: theme.gold

                font.family: "Cairo"
                font.pixelSize: 19
                font.bold: true
            }


            Label {
                text: "إدارة الفئات والمصانع والأصناف"

                color: theme.textMuted

                font.family: "Cairo"
                font.pixelSize: 12
            }


            RowLayout {
                Layout.fillWidth: true
                spacing: 10


                SettingsCard {
                    title: "الفئات"

                    value:
                        masterDataVM.categoryCount.toString()

                    subtitle:
                        masterDataVM.disabledCategoryCount > 0
                        ? masterDataVM.disabledCategoryCount
                          + " فئة معطلة"
                        : masterDataVM.categoryCodes

                    accentColor: theme.gold
                    clickable: true

                    onClicked: {
                        masterDataVM.refresh()
                        root.currentPage = 9
                    }
                }

                SettingsCard {
                    title: "المصانع"

                    value:
                        masterDataVM.factoryCount.toString()

                    subtitle:
                        masterDataVM.disabledFactoryCount > 0
                        ? masterDataVM.disabledFactoryCount
                          + " مصنع معطل"
                        : "إدارة المصانع"

                    accentColor: theme.blue
                    clickable: true

                    onClicked: {
                        masterDataVM.refresh()
                        root.currentPage = 7
                    }
                }


                SettingsCard {
                    title: "الأصناف"

                    value:
                        masterDataVM.itemTypeCount.toString()

                    subtitle:
                        masterDataVM.disabledItemTypeCount > 0
                        ? masterDataVM.disabledItemTypeCount
                          + " صنف معطل"
                        : "إدارة أنواع الأصناف"

                    accentColor: theme.green
                    clickable: true

                    onClicked: {
                        masterDataVM.refresh()
                        root.currentPage = 8
                    }
                }
            }


            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 64

                Layout.topMargin: 8

                color: theme.bgSecondary

                radius: theme.radiusSmall

                border.color: theme.border
                border.width: 1


                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 14
                    anchors.rightMargin: 14
                    spacing: 12


                    Rectangle {
                        width: 9
                        height: 9

                        radius: 5

                        color:
                            masterDataVM.errorMessage.length > 0
                            ? theme.red
                            : theme.green
                    }


                    Label {
                        text:
                            masterDataVM.errorMessage.length > 0
                            ? masterDataVM.errorMessage
                            : "البيانات متصلة بقاعدة البيانات"

                        color:
                            masterDataVM.errorMessage.length > 0
                            ? theme.red
                            : theme.textMuted

                        font.family: "Cairo"
                        font.pixelSize: 12

                        elide: Text.ElideRight

                        Layout.fillWidth: true
                    }


                    Button {
                        text: "↻  تحديث"

                        font.family: "Cairo"

                        onClicked:
                            masterDataVM.refresh()
                    }
                }
            }


            Item {
                Layout.fillHeight: true
            }
        }
    }


    // ========================================================
    // FACTORIES PAGE
    // ========================================================

    component FactoriesPage: Item {
        id: factoriesPage

        property string pageTitle: ""
        property bool showDisabled: false


        Rectangle {
            anchors.fill: parent
            color: theme.bgPrimary
        }


        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 22
            spacing: 14


            RowLayout {
                Layout.fillWidth: true


                ColumnLayout {
                    spacing: 2


                    Label {
                        text: "🏭 إدارة المصانع"

                        color: theme.gold

                        font.family: "Cairo"
                        font.pixelSize: 20
                        font.bold: true
                    }


                    Label {
                        text:
                            "إضافة وتعديل وتعطيل وإعادة تفعيل المصانع"

                        color: theme.textMuted

                        font.family: "Cairo"
                        font.pixelSize: 12
                    }
                }


                Item {
                    Layout.fillWidth: true
                }


                Button {
                    text: "↻  تحديث"

                    font.family: "Cairo"

                    onClicked:
                        masterDataVM.refresh()
                }


                Button {
                    text: "←  العودة للإعدادات"

                    font.family: "Cairo"

                    onClicked:
                        root.currentPage = 6
                }
            }


            RowLayout {
                Layout.fillWidth: true
                spacing: 10


                SummaryBox {
                    title: "المصانع النشطة"
                    value: masterDataVM.factoryCount.toString()
                    accentColor: theme.green
                }


                SummaryBox {
                    title: "المصانع المعطلة"
                    value: masterDataVM.disabledFactoryCount.toString()
                    accentColor: theme.red
                }


                SummaryBox {
                    title: "إجمالي المصانع"
                    value: masterDataVM.allFactoryCount.toString()
                    accentColor: theme.blue
                }
            }


            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 62

                color: theme.bgSecondary

                radius: theme.radiusSmall

                border.color: theme.border
                border.width: 1


                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 12
                    spacing: 8


                    Button {
                        text:
                            "✓  النشطة  "
                            + masterDataVM.factoryCount

                        checkable: true
                        checked: !factoriesPage.showDisabled

                        onClicked:
                            factoriesPage.showDisabled = false
                    }


                    Button {
                        text:
                            "⛔  المعطلة  "
                            + masterDataVM.disabledFactoryCount

                        checkable: true
                        checked: factoriesPage.showDisabled

                        onClicked:
                            factoriesPage.showDisabled = true
                    }


                    Item {
                        Layout.fillWidth: true
                    }


                    Button {
                        text: "＋  مصنع جديد"

                        font.family: "Cairo"
                        font.bold: true

                        onClicked: {
                            factoryNameField.text = ""
                            factoryCodeField.text = ""
                            factoryNotesField.text = ""
                            factoryDialogError.text = ""

                            newFactoryDialog.open()

                            factoryNameField.forceActiveFocus()
                        }
                    }
                }
            }


            ActionMessageBox {}


            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true

                color: theme.bgSecondary

                radius: theme.radiusSmall

                border.color: theme.border
                border.width: 1


                ColumnLayout {
                    visible:
                        factoriesPage.showDisabled
                        ? masterDataVM.disabledFactoryCount === 0
                        : masterDataVM.factoryCount === 0

                    anchors.centerIn: parent
                    spacing: 8


                    Label {
                        text:
                            factoriesPage.showDisabled
                            ? "✓"
                            : "🏭"

                        color:
                            factoriesPage.showDisabled
                            ? theme.green
                            : theme.gold

                        font.pixelSize: 38

                        Layout.alignment: Qt.AlignHCenter
                    }


                    Label {
                        text:
                            factoriesPage.showDisabled
                            ? "لا توجد مصانع معطلة"
                            : "لا توجد مصانع نشطة"

                        color: theme.textMuted

                        font.family: "Cairo"
                        font.pixelSize: 14

                        Layout.alignment: Qt.AlignHCenter
                    }
                }


                ListView {
                    id: factoriesList

                    visible:
                        factoriesPage.showDisabled
                        ? masterDataVM.disabledFactoryCount > 0
                        : masterDataVM.factoryCount > 0

                    anchors.fill: parent
                    anchors.margins: 12

                    clip: true
                    spacing: 7

                    model:
                        factoriesPage.showDisabled
                        ? masterDataVM.inactiveFactories
                        : masterDataVM.factories


                    delegate: Rectangle {
                        id: factoryRow

                        required property var modelData

                        width: factoriesList.width
                        height: 76

                        radius: theme.radiusSmall

                        color:
                            factoryMouse.containsMouse
                            && !factoriesPage.showDisabled
                            ? theme.panelHover
                            : theme.bgTertiary

                        border.color:
                            factoriesPage.showDisabled
                            ? theme.red
                            : theme.border

                        border.width: 1


                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 16
                            anchors.rightMargin: 16
                            spacing: 15


                            Rectangle {
                                Layout.preferredWidth: 42
                                Layout.preferredHeight: 42

                                radius: 21

                                color: theme.bgSecondary

                                border.color:
                                    factoriesPage.showDisabled
                                    ? theme.red
                                    : theme.goldDark

                                border.width: 1


                                Label {
                                    anchors.centerIn: parent

                                    text:
                                        factoriesPage.showDisabled
                                        ? "⛔"
                                        : "🏭"

                                    font.pixelSize: 17
                                }
                            }


                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2


                                Label {
                                    text:
                                        modelData.name
                                        ? modelData.name
                                        : "—"

                                    color:
                                        factoriesPage.showDisabled
                                        ? theme.textMuted
                                        : theme.textPrimary

                                    font.family: "Cairo"
                                    font.pixelSize: 14
                                    font.bold: true
                                }


                                Label {
                                    text:
                                        modelData.code
                                        ? "الكود: "
                                          + modelData.code
                                        : "بدون كود"

                                    color: theme.textMuted

                                    font.family: "Cairo"
                                    font.pixelSize: 11
                                }


                                Label {
                                    visible:
                                        modelData.notes
                                        && modelData.notes.length > 0

                                    text:
                                        modelData.notes
                                        ? modelData.notes
                                        : ""

                                    color: theme.textDisabled

                                    font.family: "Cairo"
                                    font.pixelSize: 10

                                    elide: Text.ElideRight

                                    Layout.fillWidth: true
                                }
                            }


                            Label {
                                text: "#" + modelData.id

                                color: theme.textDisabled

                                font.family: "Cairo"
                                font.pixelSize: 10
                            }


                            Button {
                                visible: factoriesPage.showDisabled

                                text: "↻  إعادة تفعيل"

                                font.family: "Cairo"

                                onClicked: {

                                    reactivateFactoryDialog.factoryId =
                                        Number(modelData.id)

                                    reactivateFactoryDialog.factoryName =
                                        modelData.name
                                        ? modelData.name
                                        : ""

                                    reactivateFactoryDialog.open()
                                }
                            }


                            Label {
                                visible:
                                    !factoriesPage.showDisabled

                                text: "تعديل  ›"

                                color: theme.gold

                                font.family: "Cairo"
                                font.pixelSize: 11
                            }
                        }


                        MouseArea {
                            id: factoryMouse

                            anchors.fill: parent

                            enabled:
                                !factoriesPage.showDisabled

                            hoverEnabled: true

                            cursorShape:
                                enabled
                                ? Qt.PointingHandCursor
                                : Qt.ArrowCursor

                            onClicked:
                                editFactoryDialog.openFactory(
                                    modelData
                                )
                        }
                    }
                }
            }
        }
    }


    // ========================================================
    // ITEM TYPES PAGE
    // ========================================================

    component ItemTypesPage: Item {
        id: itemTypesPage

        property string pageTitle: ""
        property bool showDisabled: false


        Rectangle {
            anchors.fill: parent
            color: theme.bgPrimary
        }


        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 22
            spacing: 14


            RowLayout {
                Layout.fillWidth: true


                ColumnLayout {
                    spacing: 2


                    Label {
                        text: "💍 إدارة الأصناف"

                        color: theme.gold

                        font.family: "Cairo"
                        font.pixelSize: 20
                        font.bold: true
                    }


                    Label {
                        text:
                            "إدارة أنواع المشغولات والفئة الافتراضية لكل صنف"

                        color: theme.textMuted

                        font.family: "Cairo"
                        font.pixelSize: 12
                    }
                }


                Item {
                    Layout.fillWidth: true
                }


                Button {
                    text: "↻  تحديث"

                    font.family: "Cairo"

                    onClicked:
                        masterDataVM.refresh()
                }


                Button {
                    text: "←  العودة للإعدادات"

                    font.family: "Cairo"

                    onClicked:
                        root.currentPage = 6
                }
            }


            RowLayout {
                Layout.fillWidth: true
                spacing: 10


                SummaryBox {
                    title: "الأصناف النشطة"
                    value: masterDataVM.itemTypeCount.toString()
                    accentColor: theme.green
                }


                SummaryBox {
                    title: "الأصناف المعطلة"
                    value: masterDataVM.disabledItemTypeCount.toString()
                    accentColor: theme.red
                }


                SummaryBox {
                    title: "إجمالي الأصناف"
                    value: masterDataVM.allItemTypeCount.toString()
                    accentColor: theme.blue
                }
            }


            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 62

                color: theme.bgSecondary

                radius: theme.radiusSmall

                border.color: theme.border
                border.width: 1


                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 12
                    spacing: 8


                    Button {
                        text:
                            "✓  النشطة  "
                            + masterDataVM.itemTypeCount

                        checkable: true
                        checked: !itemTypesPage.showDisabled

                        onClicked:
                            itemTypesPage.showDisabled = false
                    }


                    Button {
                        text:
                            "⛔  المعطلة  "
                            + masterDataVM.disabledItemTypeCount

                        checkable: true
                        checked: itemTypesPage.showDisabled

                        onClicked:
                            itemTypesPage.showDisabled = true
                    }


                    Item {
                        Layout.fillWidth: true
                    }


                    Button {
                        text: "＋  صنف جديد"

                        font.family: "Cairo"
                        font.bold: true

                        onClicked: {

                            root.rebuildCategoryChoiceModel()

                            newItemTypeNameField.text = ""
                            newItemTypeCodeField.text = ""
                            newItemTypeNotesField.text = ""

                            newItemTypeCategory.currentIndex = 0

                            newItemTypeError.text = ""

                            newItemTypeDialog.open()

                            newItemTypeNameField
                                .forceActiveFocus()
                        }
                    }
                }
            }


            ActionMessageBox {}


            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true

                color: theme.bgSecondary

                radius: theme.radiusSmall

                border.color: theme.border
                border.width: 1


                ColumnLayout {
                    visible:
                        itemTypesPage.showDisabled
                        ? masterDataVM.disabledItemTypeCount === 0
                        : masterDataVM.itemTypeCount === 0

                    anchors.centerIn: parent
                    spacing: 8


                    Label {
                        text:
                            itemTypesPage.showDisabled
                            ? "✓"
                            : "💍"

                        color:
                            itemTypesPage.showDisabled
                            ? theme.green
                            : theme.gold

                        font.pixelSize: 38

                        Layout.alignment: Qt.AlignHCenter
                    }


                    Label {
                        text:
                            itemTypesPage.showDisabled
                            ? "لا توجد أصناف معطلة"
                            : "لا توجد أصناف نشطة"

                        color: theme.textMuted

                        font.family: "Cairo"
                        font.pixelSize: 14

                        Layout.alignment: Qt.AlignHCenter
                    }


                    Label {
                        visible:
                            !itemTypesPage.showDisabled

                        text:
                            "اضغط على «صنف جديد» لإضافة أول صنف"

                        color: theme.textDisabled

                        font.family: "Cairo"
                        font.pixelSize: 11

                        Layout.alignment: Qt.AlignHCenter
                    }
                }


                ListView {
                    id: itemTypesList

                    visible:
                        itemTypesPage.showDisabled
                        ? masterDataVM.disabledItemTypeCount > 0
                        : masterDataVM.itemTypeCount > 0

                    anchors.fill: parent
                    anchors.margins: 12

                    clip: true
                    spacing: 7

                    model:
                        itemTypesPage.showDisabled
                        ? masterDataVM.inactiveItemTypes
                        : masterDataVM.itemTypes


                    delegate: Rectangle {
                        id: itemTypeRow

                        required property var modelData

                        width: itemTypesList.width
                        height: 82

                        radius: theme.radiusSmall

                        color:
                            itemTypeMouse.containsMouse
                            && !itemTypesPage.showDisabled
                            ? theme.panelHover
                            : theme.bgTertiary

                        border.color:
                            itemTypesPage.showDisabled
                            ? theme.red
                            : theme.border

                        border.width: 1


                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 16
                            anchors.rightMargin: 16
                            spacing: 15


                            Rectangle {
                                Layout.preferredWidth: 42
                                Layout.preferredHeight: 42

                                radius: 21

                                color: theme.bgSecondary

                                border.color:
                                    itemTypesPage.showDisabled
                                    ? theme.red
                                    : theme.green

                                border.width: 1


                                Label {
                                    anchors.centerIn: parent

                                    text:
                                        itemTypesPage.showDisabled
                                        ? "⛔"
                                        : "💍"

                                    font.pixelSize: 17
                                }
                            }


                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2


                                Label {
                                    text:
                                        modelData.name
                                        ? modelData.name
                                        : "—"

                                    color:
                                        itemTypesPage.showDisabled
                                        ? theme.textMuted
                                        : theme.textPrimary

                                    font.family: "Cairo"
                                    font.pixelSize: 14
                                    font.bold: true
                                }


                                Label {
                                    text:
                                        modelData.code
                                        ? "الكود: "
                                          + modelData.code
                                        : "بدون كود"

                                    color: theme.textMuted

                                    font.family: "Cairo"
                                    font.pixelSize: 11
                                }


                                Label {
                                    text:
                                        modelData.default_category_code
                                        ? "الفئة الافتراضية: "
                                          + modelData.default_category_code
                                        : "بدون فئة افتراضية"

                                    color:
                                        modelData.default_category_code
                                        ? theme.gold
                                        : theme.textDisabled

                                    font.family: "Cairo"
                                    font.pixelSize: 10
                                }


                                Label {
                                    visible:
                                        modelData.notes
                                        && modelData.notes.length > 0

                                    text:
                                        modelData.notes
                                        ? modelData.notes
                                        : ""

                                    color: theme.textDisabled

                                    font.family: "Cairo"
                                    font.pixelSize: 10

                                    elide: Text.ElideRight

                                    Layout.fillWidth: true
                                }
                            }


                            Label {
                                text:
                                    "#" + modelData.id

                                color: theme.textDisabled

                                font.family: "Cairo"
                                font.pixelSize: 10
                            }


                            Button {
                                visible:
                                    itemTypesPage.showDisabled

                                text:
                                    "↻  إعادة تفعيل"

                                font.family: "Cairo"

                                onClicked: {

                                    reactivateItemTypeDialog.itemTypeId =
                                        Number(modelData.id)

                                    reactivateItemTypeDialog.itemTypeName =
                                        modelData.name
                                        ? modelData.name
                                        : ""

                                    reactivateItemTypeDialog.open()
                                }
                            }


                            Label {
                                visible:
                                    !itemTypesPage.showDisabled

                                text:
                                    "تعديل  ›"

                                color: theme.gold

                                font.family: "Cairo"
                                font.pixelSize: 11
                            }
                        }


                        MouseArea {
                            id: itemTypeMouse

                            anchors.fill: parent

                            enabled:
                                !itemTypesPage.showDisabled

                            hoverEnabled: true

                            cursorShape:
                                enabled
                                ? Qt.PointingHandCursor
                                : Qt.ArrowCursor

                            onClicked:
                                editItemTypeDialog.openItemType(
                                    modelData
                                )
                        }
                    }
                }
            }
        }
    }

    // ========================================================
    // CATEGORIES PAGE
    // ========================================================

    component CategoriesPage: Item {
    id: categoriesPage

    property string pageTitle: ""
    property bool showDisabled: false


    Rectangle {
        anchors.fill: parent
        color: theme.bgPrimary
    }


    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 22
        spacing: 14


        // ------------------------------------------------
        // HEADER
        // ------------------------------------------------

        RowLayout {
            Layout.fillWidth: true


            ColumnLayout {
                spacing: 2


                Label {
                    text: "🏷️ إدارة الفئات"

                    Layout.fillWidth: true

                    color: theme.gold

                    font.family: "Cairo"
                    font.pixelSize: 20
                    font.bold: true

                    horizontalAlignment:
                        Text.AlignRight
                }


                Label {
                    text:
                        "قواعد المصنعية وحدود موافقة المدير"

                    Layout.fillWidth: true

                    color: theme.textMuted

                    font.family: "Cairo"
                    font.pixelSize: 12

                    horizontalAlignment:
                        Text.AlignRight
                }
            }


            Item {
                Layout.fillWidth: true
            }


            Button {
                text: "↻  تحديث"

                font.family: "Cairo"

                onClicked:
                    masterDataVM.refresh()
            }


            Button {
                text: "←  العودة للإعدادات"

                font.family: "Cairo"

                onClicked:
                    root.currentPage = 6
            }
        }


        // ------------------------------------------------
        // SUMMARY
        // ------------------------------------------------

        RowLayout {
            Layout.fillWidth: true
            spacing: 10


            SummaryBox {
                title: "الفئات النشطة"

                value:
                    masterDataVM.categoryCount.toString()

                accentColor:
                    theme.green
            }


            SummaryBox {
                title: "الفئات المعطلة"

                value:
                    masterDataVM.disabledCategoryCount.toString()

                accentColor:
                    theme.red
            }


            SummaryBox {
                title: "إجمالي الفئات"

                value:
                    masterDataVM.allCategoryCount.toString()

                accentColor:
                    theme.gold
            }
        }


        // ------------------------------------------------
        // TOOLBAR
        // ------------------------------------------------

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 62

            color: theme.bgSecondary

            radius: theme.radiusSmall

            border.color: theme.border
            border.width: 1


            RowLayout {
                anchors.fill: parent

                anchors.leftMargin: 12
                anchors.rightMargin: 12

                spacing: 8


                Button {
                    text:
                        "✓  النشطة  "
                        + masterDataVM.categoryCount

                    checkable: true

                    checked:
                        !categoriesPage.showDisabled

                    onClicked:
                        categoriesPage.showDisabled = false
                }


                Button {
                    text:
                        "⛔  المعطلة  "
                        + masterDataVM.disabledCategoryCount

                    checkable: true

                    checked:
                        categoriesPage.showDisabled

                    onClicked:
                        categoriesPage.showDisabled = true
                }


                Item {
                    Layout.fillWidth: true
                }


                Label {
                    text:
                        "الفئات الأساسية ثابتة ولا يتم إنشاء فئات جديدة"

                    color: theme.textDisabled

                    font.family: "Cairo"
                    font.pixelSize: 10
                }
            }
        }


        ActionMessageBox {}


        // ------------------------------------------------
        // CATEGORY LIST
        // ------------------------------------------------

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true

            color: theme.bgSecondary

            radius: theme.radiusSmall

            border.color: theme.border
            border.width: 1


            ColumnLayout {
                visible:
                    categoriesPage.showDisabled
                    && masterDataVM.disabledCategoryCount === 0

                anchors.centerIn: parent

                spacing: 8


                Label {
                    text: "✓"

                    color: theme.green

                    font.pixelSize: 38

                    Layout.alignment:
                        Qt.AlignHCenter
                }


                Label {
                    text:
                        "لا توجد فئات معطلة"

                    color: theme.textMuted

                    font.family: "Cairo"
                    font.pixelSize: 14

                    Layout.alignment:
                        Qt.AlignHCenter
                }
            }


            ListView {
                id: categoriesList

                visible:
                    categoriesPage.showDisabled
                    ? masterDataVM.disabledCategoryCount > 0
                    : masterDataVM.categoryCount > 0

                anchors.fill: parent
                anchors.margins: 12

                clip: true
                spacing: 8

                model:
                    categoriesPage.showDisabled
                    ? masterDataVM.inactiveCategories
                    : masterDataVM.categories


                delegate: Rectangle {
                    id: categoryRow

                    required property var modelData

                    width:
                        categoriesList.width

                    height: 112

                    radius:
                        theme.radiusSmall

                    color:
                        categoryMouse.containsMouse
                        && !categoriesPage.showDisabled
                        ? theme.panelHover
                        : theme.bgTertiary

                    border.color:
                        categoriesPage.showDisabled
                        ? theme.red
                        : theme.border

                    border.width: 1


                    RowLayout {
                        anchors.fill: parent

                        anchors.leftMargin: 16
                        anchors.rightMargin: 16

                        spacing: 16


                        // Category code circle
                        Rectangle {
                            Layout.preferredWidth: 58
                            Layout.preferredHeight: 58

                            radius: 29

                            color: theme.bgSecondary

                            border.color:
                                categoriesPage.showDisabled
                                ? theme.red
                                : theme.gold

                            border.width: 2


                            Label {
                                anchors.centerIn: parent

                                text:
                                    modelData.code
                                    ? modelData.code
                                    : "—"

                                color:
                                    categoriesPage.showDisabled
                                    ? theme.textMuted
                                    : theme.gold

                                font.family: "Cairo"
                                font.pixelSize: 20
                                font.bold: true
                            }
                        }


                        ColumnLayout {
                            Layout.fillWidth: true

                            spacing: 3


                            Label {
                                Layout.fillWidth: true

                                text:
                                    modelData.name
                                    ? modelData.name
                                    : "الفئة "
                                      + modelData.code

                                color:
                                    categoriesPage.showDisabled
                                    ? theme.textMuted
                                    : theme.textPrimary

                                font.family: "Cairo"
                                font.pixelSize: 14
                                font.bold: true

                                horizontalAlignment:
                                    Text.AlignRight
                            }


                            Label {
                                Layout.fillWidth: true

                                text:
                                    "مصنعية الشراء: "
                                    + (
                                        modelData.purchase_making_min_per_gram
                                        !== null
                                        && modelData.purchase_making_min_per_gram
                                        !== undefined

                                        ? modelData.purchase_making_min_per_gram

                                        : "—"
                                    )
                                    + "  ←  "
                                    + (
                                        modelData.purchase_making_max_per_gram
                                        !== null
                                        && modelData.purchase_making_max_per_gram
                                        !== undefined

                                        ? modelData.purchase_making_max_per_gram

                                        : "—"
                                    )

                                color:
                                    theme.textMuted

                                font.family: "Cairo"
                                font.pixelSize: 11

                                horizontalAlignment:
                                    Text.AlignRight
                            }


                            Label {
                                Layout.fillWidth: true

                                text:
                                    "مصنعية البيع: "
                                    + (
                                        modelData.sale_making_min_per_gram
                                        !== null
                                        && modelData.sale_making_min_per_gram
                                        !== undefined

                                        ? modelData.sale_making_min_per_gram

                                        : "—"
                                    )
                                    + "  ←  "
                                    + (
                                        modelData.sale_making_max_per_gram
                                        !== null
                                        && modelData.sale_making_max_per_gram
                                        !== undefined

                                        ? modelData.sale_making_max_per_gram

                                        : "—"
                                    )

                                color:
                                    theme.textMuted

                                font.family: "Cairo"
                                font.pixelSize: 11

                                horizontalAlignment:
                                    Text.AlignRight
                            }


                            Label {
                                Layout.fillWidth: true

                                text:
                                    modelData.manager_approval_below_per_gram
                                    !== null
                                    && modelData.manager_approval_below_per_gram
                                    !== undefined

                                    ? "⚠ موافقة المدير أقل من: "
                                      + modelData.manager_approval_below_per_gram
                                      + " ج/جم"

                                    : "لا يوجد حد موافقة مدير"

                                color:
                                    modelData.manager_approval_below_per_gram
                                    !== null
                                    && modelData.manager_approval_below_per_gram
                                    !== undefined

                                    ? theme.amber
                                    : theme.textDisabled

                                font.family: "Cairo"
                                font.pixelSize: 10

                                horizontalAlignment:
                                    Text.AlignRight
                            }
                        }


                        Button {
                            visible:
                                categoriesPage.showDisabled

                            text:
                                "↻  إعادة تفعيل"

                            font.family:
                                "Cairo"

                            onClicked: {

                                reactivateCategoryDialog.categoryId =
                                    Number(modelData.id)

                                reactivateCategoryDialog.categoryCode =
                                    modelData.code || ""

                                reactivateCategoryDialog.open()
                            }
                        }


                        Label {
                            visible:
                                !categoriesPage.showDisabled

                            text: "تعديل  ›"

                            color: theme.gold

                            font.family: "Cairo"
                            font.pixelSize: 11
                        }
                    }


                    MouseArea {
                        id: categoryMouse

                        anchors.fill: parent

                        enabled:
                            !categoriesPage.showDisabled

                        hoverEnabled: true

                        cursorShape:
                            enabled
                            ? Qt.PointingHandCursor
                            : Qt.ArrowCursor

                        onClicked:
                            editCategoryDialog.openCategory(
                                modelData
                            )
                    }
                }
            }
        }
    }
}
    // ========================================================
    // EDIT CATEGORY
    // ========================================================

    Dialog {
    id: editCategoryDialog

    property int categoryId: 0
    property string categoryCode: ""

    modal: true
    dim: true

    Overlay.modal: Rectangle {
        color: "#80000000"
    }

    width: 620
    padding: 0


    x:
        Math.round(
            (root.width - width) / 2
        )

    y:
        Math.round(
            (root.height - height) / 2
        )


    function valueText(value) {

        if (
            value === null
            || value === undefined
        ) {
            return ""
        }

        return String(value)
    }


    function openCategory(categoryData) {

        categoryId =
            Number(categoryData.id)

        categoryCode =
            categoryData.code || ""


        categoryPurchaseMin.text =
            valueText(
                categoryData
                .purchase_making_min_per_gram
            )

        categoryPurchaseMax.text =
            valueText(
                categoryData
                .purchase_making_max_per_gram
            )

        categorySaleMin.text =
            valueText(
                categoryData
                .sale_making_min_per_gram
            )

        categorySaleMax.text =
            valueText(
                categoryData
                .sale_making_max_per_gram
            )

        categoryManagerThreshold.text =
            valueText(
                categoryData
                .manager_approval_below_per_gram
            )

        categoryNotes.text =
            categoryData.notes || ""

        editCategoryError.text = ""

        open()
    }


    background: Rectangle {
        color: theme.bgSecondary

        radius: 10

        border.color:
            theme.border

        border.width: 1
    }


    contentItem: ColumnLayout {
        spacing: 14


        // Header
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 62

            color: theme.bgTertiary

            radius: 10


            RowLayout {
                anchors.fill: parent

                anchors.leftMargin: 18
                anchors.rightMargin: 18


                Label {
                    text:
                        "🏷️  تعديل الفئة "
                        + editCategoryDialog.categoryCode

                    color: theme.gold

                    font.family: "Cairo"
                    font.pixelSize: 17
                    font.bold: true
                }


                Item {
                    Layout.fillWidth: true
                }


                Button {
                    text: "✕"

                    flat: true

                    onClicked:
                        editCategoryDialog.close()
                }
            }
        }


        ColumnLayout {
            Layout.fillWidth: true

            Layout.leftMargin: 22
            Layout.rightMargin: 22

            spacing: 8


            // Purchase title
            Label {
                Layout.fillWidth: true

                text: "مصنعية الشراء — لكل جرام"

                color: theme.gold

                font.family: "Cairo"
                font.bold: true

                horizontalAlignment:
                    Text.AlignRight
            }


            RowLayout {
                Layout.fillWidth: true

                spacing: 10


                ColumnLayout {
                    Layout.fillWidth: true


                    Label {
                        Layout.fillWidth: true

                        text: "الحد الأدنى"

                        color:
                            theme.textSecondary

                        font.family: "Cairo"

                        horizontalAlignment:
                            Text.AlignRight
                    }


                    TextField {
                        id: categoryPurchaseMin

                        Layout.fillWidth: true

                        placeholderText: "اختياري"

                        horizontalAlignment:
                            TextInput.AlignRight

                        inputMethodHints:
                            Qt.ImhFormattedNumbersOnly
                    }
                }


                ColumnLayout {
                    Layout.fillWidth: true


                    Label {
                        Layout.fillWidth: true

                        text: "الحد الأقصى"

                        color:
                            theme.textSecondary

                        font.family: "Cairo"

                        horizontalAlignment:
                            Text.AlignRight
                    }


                    TextField {
                        id: categoryPurchaseMax

                        Layout.fillWidth: true

                        placeholderText: "اختياري"

                        horizontalAlignment:
                            TextInput.AlignRight

                        inputMethodHints:
                            Qt.ImhFormattedNumbersOnly
                    }
                }
            }


            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1

                color: theme.border
            }


            // Sale title
            Label {
                Layout.fillWidth: true

                text: "مصنعية البيع — لكل جرام"

                color: theme.gold

                font.family: "Cairo"
                font.bold: true

                horizontalAlignment:
                    Text.AlignRight
            }


            RowLayout {
                Layout.fillWidth: true

                spacing: 10


                ColumnLayout {
                    Layout.fillWidth: true


                    Label {
                        Layout.fillWidth: true

                        text: "الحد الأدنى"

                        color:
                            theme.textSecondary

                        font.family: "Cairo"

                        horizontalAlignment:
                            Text.AlignRight
                    }


                    TextField {
                        id: categorySaleMin

                        Layout.fillWidth: true

                        placeholderText: "اختياري"

                        horizontalAlignment:
                            TextInput.AlignRight

                        inputMethodHints:
                            Qt.ImhFormattedNumbersOnly
                    }
                }


                ColumnLayout {
                    Layout.fillWidth: true


                    Label {
                        Layout.fillWidth: true

                        text: "الحد الأقصى"

                        color:
                            theme.textSecondary

                        font.family: "Cairo"

                        horizontalAlignment:
                            Text.AlignRight
                    }


                    TextField {
                        id: categorySaleMax

                        Layout.fillWidth: true

                        placeholderText: "اختياري"

                        horizontalAlignment:
                            TextInput.AlignRight

                        inputMethodHints:
                            Qt.ImhFormattedNumbersOnly
                    }
                }
            }


            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1

                color: theme.border
            }


            Label {
                Layout.fillWidth: true

                text:
                    "موافقة المدير مطلوبة عند البيع بأقل من"

                color: theme.amber

                font.family: "Cairo"
                font.bold: true

                horizontalAlignment:
                    Text.AlignRight
            }


            TextField {
                id: categoryManagerThreshold

                Layout.fillWidth: true
                Layout.preferredHeight: 44

                placeholderText:
                    "اتركه فارغاً إذا لم يوجد حد"

                horizontalAlignment:
                    TextInput.AlignRight

                inputMethodHints:
                    Qt.ImhFormattedNumbersOnly
            }


            Label {
                Layout.fillWidth: true

                text:
                    "جنيه مصنعية لكل جرام — عند النزول تحت هذا الرقم سيطلب النظام موافقة مدير."

                color: theme.textDisabled

                font.family: "Cairo"
                font.pixelSize: 10

                wrapMode:
                    Text.WordWrap

                horizontalAlignment:
                    Text.AlignRight
            }


            Label {
                Layout.fillWidth: true

                text: "ملاحظات"

                color:
                    theme.textSecondary

                font.family: "Cairo"

                horizontalAlignment:
                    Text.AlignRight
            }


            TextArea {
                id: categoryNotes

                Layout.fillWidth: true
                Layout.preferredHeight: 80

                placeholderText:
                    "ملاحظات اختيارية..."

                font.family: "Cairo"

                horizontalAlignment:
                    TextEdit.AlignRight

                wrapMode:
                    TextEdit.Wrap
            }


            Label {
                id: editCategoryError

                visible:
                    text.length > 0

                Layout.fillWidth: true

                color: theme.red

                font.family: "Cairo"

                wrapMode:
                    Text.WordWrap

                horizontalAlignment:
                    Text.AlignRight
            }
        }


        RowLayout {
            Layout.fillWidth: true

            Layout.leftMargin: 22
            Layout.rightMargin: 22
            Layout.bottomMargin: 20

            spacing: 10


            Button {
                text: "⛔  تعطيل الفئة"

                font.family: "Cairo"

                onClicked: {

                    disableCategoryDialog.categoryId =
                        editCategoryDialog.categoryId

                    disableCategoryDialog.categoryCode =
                        editCategoryDialog.categoryCode

                    disableCategoryDialog.open()
                }
            }


            Item {
                Layout.fillWidth: true
            }


            Button {
                text: "إلغاء"

                font.family: "Cairo"

                onClicked:
                    editCategoryDialog.close()
            }


            Button {
                text: "💾  حفظ القواعد"

                font.family: "Cairo"
                font.bold: true


                onClicked: {

                    editCategoryError.text = ""

                    var saved =
                        masterDataVM.updateCategoryRules(
                            editCategoryDialog.categoryId,
                            categoryPurchaseMin.text,
                            categoryPurchaseMax.text,
                            categorySaleMin.text,
                            categorySaleMax.text,
                            categoryManagerThreshold.text,
                            categoryNotes.text
                        )


                    if (saved) {

                        editCategoryDialog.close()

                    } else {

                        editCategoryError.text =
                            masterDataVM.actionMessage
                    }
                }
            }
        }
    }
}
    // ========================================================
    // DISABLE CATEGORY
    // ========================================================

    Dialog {
    id: disableCategoryDialog

    property int categoryId: 0
    property string categoryCode: ""

    modal: true
    dim: true

    Overlay.modal: Rectangle {
        color: "#80000000"
    }

    width: 430


    ColumnLayout {
        spacing: 14


        Label {
            Layout.fillWidth: true

            text: "⛔  تعطيل الفئة"

            color: theme.red

            font.family: "Cairo"
            font.pixelSize: 17
            font.bold: true

            horizontalAlignment:
                Text.AlignHCenter
        }


        Label {
            Layout.fillWidth: true

            text:
                "هل تريد تعطيل الفئة «"
                + disableCategoryDialog.categoryCode
                + "»؟"

            color: theme.textPrimary

            font.family: "Cairo"

            horizontalAlignment:
                Text.AlignHCenter
        }


        Label {
            Layout.fillWidth: true

            text:
                "لن يتم حذف الفئة أو تاريخها، "
                + "لكنها لن تكون متاحة للعمليات الجديدة."

            color: theme.textMuted

            font.family: "Cairo"
            font.pixelSize: 11

            wrapMode:
                Text.WordWrap

            horizontalAlignment:
                Text.AlignHCenter
        }


        RowLayout {
            Layout.fillWidth: true


            Button {
                text: "إلغاء"

                onClicked:
                    disableCategoryDialog.close()
            }


            Item {
                Layout.fillWidth: true
            }


            Button {
                text: "تعطيل"

                onClicked: {

                    var result =
                        masterDataVM.disableCategory(
                            disableCategoryDialog.categoryId
                        )


                    if (result) {

                        disableCategoryDialog.close()

                        editCategoryDialog.close()

                        categoriesPage.showDisabled =
                            true
                    }
                }
            }
        }
    }
}
    // ========================================================
    // REACTIVATE CATEGORY
    // ========================================================

    Dialog {
        id: reactivateCategoryDialog

        property int categoryId: 0
        property string categoryCode: ""

        modal: true
        dim: true

        Overlay.modal: Rectangle {
            color: "#80000000"
        }

        width: 430


        ColumnLayout {
            spacing: 14


            Label {
                Layout.fillWidth: true

                text: "↻  إعادة تفعيل الفئة"

                color: theme.green

                font.family: "Cairo"
                font.pixelSize: 17
                font.bold: true

                horizontalAlignment:
                    Text.AlignHCenter
            }


            Label {
                Layout.fillWidth: true

                text:
                    "إعادة تفعيل الفئة «"
                    + reactivateCategoryDialog.categoryCode
                    + "»؟"

                color: theme.textPrimary

                font.family: "Cairo"

                horizontalAlignment:
                    Text.AlignHCenter
            }


            RowLayout {
                Layout.fillWidth: true


                Button {
                    text: "إلغاء"

                    onClicked:
                        reactivateCategoryDialog.close()
                }


                Item {
                    Layout.fillWidth: true
                }


                Button {
                    text: "إعادة التفعيل"

                    onClicked: {

                        var result =
                            masterDataVM.reactivateCategory(
                                reactivateCategoryDialog.categoryId
                            )


                        if (result) {

                            reactivateCategoryDialog.close()

                            categoriesPage.showDisabled =
                                false
                        }
                    }
                }
            }
        }
    }
    // ========================================================
    // SUMMARY BOX
    // ========================================================

    component SummaryBox: Rectangle {
        id: summaryBox

        property string title: ""
        property string value: ""

        property color accentColor:
            theme.gold


        Layout.fillWidth: true
        Layout.preferredHeight: 76

        color: theme.bgSecondary

        radius: theme.radiusSmall

        border.color: theme.border
        border.width: 1


        RowLayout {
            anchors.fill: parent

            anchors.leftMargin: 16
            anchors.rightMargin: 16


            Label {
                text: summaryBox.title

                color: theme.textMuted

                font.family: "Cairo"
                font.pixelSize: 12
            }


            Item {
                Layout.fillWidth: true
            }


            Label {
                text: summaryBox.value

                color: summaryBox.accentColor

                font.family: "Cairo"
                font.pixelSize: 24
                font.bold: true
            }
        }
    }


    // ========================================================
    // ACTION MESSAGE
    // ========================================================

    component ActionMessageBox: Rectangle {

        visible:
            masterDataVM.actionMessage.length > 0

        Layout.fillWidth: true
        Layout.preferredHeight: 46

        radius: theme.radiusSmall

        color:
            masterDataVM.actionSuccess
            ? "#16271F"
            : theme.redBackground

        border.color:
            masterDataVM.actionSuccess
            ? theme.green
            : theme.red

        border.width: 1


        Label {
            anchors.fill: parent
            anchors.margins: 10

            text:
                masterDataVM.actionMessage

            color:
                masterDataVM.actionSuccess
                ? theme.green
                : theme.red

            font.family: "Cairo"
            font.pixelSize: 12

            horizontalAlignment: Text.AlignRight
            verticalAlignment: Text.AlignVCenter
        }
    }


    // ========================================================
    // SETTINGS CARD
    // ========================================================

    component SettingsCard: Rectangle {
        id: settingsCardRoot

        signal clicked()

        property string title: ""
        property string value: ""
        property string subtitle: ""

        property color accentColor:
            theme.gold

        property bool clickable: false


        Layout.fillWidth: true
        Layout.preferredHeight: 125


        color:
            cardMouse.containsMouse
            && clickable
            ? theme.panelHover
            : theme.bgSecondary

        radius: theme.radiusSmall

        border.color:
            cardMouse.containsMouse
            && clickable
            ? accentColor
            : theme.border

        border.width: 1


        Rectangle {
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right

            height: 3

            color: accentColor

            radius: 2
        }


        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 3


            Label {
                text: settingsCardRoot.title

                color: theme.textMuted

                font.family: "Cairo"
                font.pixelSize: 12

                Layout.alignment: Qt.AlignHCenter
            }


            Label {
                text: settingsCardRoot.value

                color:
                    settingsCardRoot.accentColor

                font.family: "Cairo"
                font.pixelSize: 23
                font.bold: true

                Layout.alignment: Qt.AlignHCenter
            }


            Label {
                text: settingsCardRoot.subtitle

                color: theme.textSecondary

                font.family: "Cairo"
                font.pixelSize: 11

                horizontalAlignment: Text.AlignHCenter

                Layout.fillWidth: true
                Layout.alignment: Qt.AlignHCenter
            }
        }


        MouseArea {
            id: cardMouse

            anchors.fill: parent

            enabled:
                settingsCardRoot.clickable

            hoverEnabled: true

            cursorShape:
                settingsCardRoot.clickable
                ? Qt.PointingHandCursor
                : Qt.ArrowCursor

            onClicked:
                settingsCardRoot.clicked()
        }
    }


    // ========================================================
    // NEW FACTORY DIALOG
    // ========================================================

    Dialog {
        id: newFactoryDialog

        modal: true
        dim: true
        Overlay.modal: Rectangle {
        color: "#80000000"}
        width: 500

        x:
            Math.round(
                (root.width - width) / 2
            )

        y:
            Math.round(
                (root.height - height) / 2
            )

        padding: 0

        closePolicy:
            Popup.CloseOnEscape


        background: Rectangle {
            color: theme.bgSecondary

            radius: 10

            border.color: theme.border
            border.width: 1
        }


        contentItem: ColumnLayout {
            spacing: 14


            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 62

                color: theme.bgTertiary
                radius: 10


                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 18
                    anchors.rightMargin: 18


                    Label {
                        text:
                            "🏭  إضافة مصنع جديد"

                        color: theme.gold

                        font.family: "Cairo"
                        font.pixelSize: 17
                        font.bold: true
                    }


                    Item {
                        Layout.fillWidth: true
                    }


                    Button {
                        text: "✕"
                        flat: true

                        onClicked:
                            newFactoryDialog.close()
                    }
                }
            }


            ColumnLayout {
                Layout.fillWidth: true

                Layout.leftMargin: 22
                Layout.rightMargin: 22

                spacing: 8


                Label {
                    text: "اسم المصنع *"

                    Layout.fillWidth: true

                    color: theme.textSecondary

                    font.family: "Cairo"
                    font.pixelSize: 12

                    horizontalAlignment:
                        Text.AlignRight
                }


                TextField {
                    id: factoryNameField

                    Layout.fillWidth: true
                    Layout.preferredHeight: 44

                    placeholderText: "مثال: مصنع ماس"

                    color: theme.textPrimary

                    placeholderTextColor:
                        theme.textDisabled

                    font.family: "Cairo"
                    font.pixelSize: 13

                    horizontalAlignment:
                        TextInput.AlignRight
                }


                Label {
                    text: "الكود"

                    Layout.fillWidth: true

                    color: theme.textSecondary

                    font.family: "Cairo"
                    font.pixelSize: 12

                    horizontalAlignment:
                        Text.AlignRight
                }


                TextField {
                    id: factoryCodeField

                    Layout.fillWidth: true
                    Layout.preferredHeight: 44

                    placeholderText: "اختياري"

                    color: theme.textPrimary

                    font.family: "Cairo"
                    font.pixelSize: 13

                    horizontalAlignment:
                        TextInput.AlignRight
                }


                Label {
                    text: "ملاحظات"

                    Layout.fillWidth: true

                    color: theme.textSecondary

                    font.family: "Cairo"
                    font.pixelSize: 12

                    horizontalAlignment:
                        Text.AlignRight
                }


                TextArea {
                    id: factoryNotesField

                    Layout.fillWidth: true
                    Layout.preferredHeight: 90

                    color: theme.textPrimary

                    font.family: "Cairo"

                    horizontalAlignment:
                        TextEdit.AlignRight

                    wrapMode: TextEdit.Wrap
                }


                Label {
                    id: factoryDialogError

                    visible: text.length > 0

                    Layout.fillWidth: true

                    color: theme.red

                    font.family: "Cairo"

                    horizontalAlignment:
                        Text.AlignRight

                    wrapMode: Text.WordWrap
                }
            }


            RowLayout {
                Layout.fillWidth: true

                Layout.leftMargin: 22
                Layout.rightMargin: 22
                Layout.bottomMargin: 20

                spacing: 10


                Item {
                    Layout.fillWidth: true
                }


                Button {
                    text: "إلغاء"

                    onClicked:
                        newFactoryDialog.close()
                }


                Button {
                    text: "💾  حفظ المصنع"

                    onClicked: {

                        factoryDialogError.text = ""

                        if (
                            factoryNameField.text
                            .trim()
                            .length === 0
                        ) {

                            factoryDialogError.text =
                                "اسم المصنع مطلوب."

                            return
                        }


                        var saved =
                            masterDataVM.createFactory(
                                factoryNameField.text,
                                factoryCodeField.text,
                                factoryNotesField.text
                            )


                        if (saved) {

                            newFactoryDialog.close()

                            factoriesPage.showDisabled =
                                false

                        } else {

                            factoryDialogError.text =
                                masterDataVM.actionMessage
                        }
                    }
                }
            }
        }
    }


    // ========================================================
    // EDIT FACTORY
    // ========================================================

    Dialog {
        id: editFactoryDialog

        property int factoryId: 0

        modal: true
        dim: true
        Overlay.modal: Rectangle {
        color: "#80000000"}
        width: 500


        function openFactory(factoryData) {

            factoryId =
                Number(factoryData.id)

            editFactoryNameField.text =
                factoryData.name || ""

            editFactoryCodeField.text =
                factoryData.code || ""

            editFactoryNotesField.text =
                factoryData.notes || ""

            editFactoryError.text = ""

            open()
        }


        ColumnLayout {
            spacing: 10


            Label {
                text: "✏️ تعديل المصنع"

                Layout.fillWidth: true

                color: theme.gold

                font.family: "Cairo"
                font.bold: true

                horizontalAlignment:
                    Text.AlignRight
            }


            TextField {
                id: editFactoryNameField

                Layout.fillWidth: true

                placeholderText: "اسم المصنع"

                horizontalAlignment:
                    TextInput.AlignRight
            }


            TextField {
                id: editFactoryCodeField

                Layout.fillWidth: true

                placeholderText: "الكود"

                horizontalAlignment:
                    TextInput.AlignRight
            }


            TextArea {
                id: editFactoryNotesField

                Layout.fillWidth: true
                Layout.preferredHeight: 90

                horizontalAlignment:
                    TextEdit.AlignRight
            }


            Label {
                id: editFactoryError

                visible: text.length > 0

                Layout.fillWidth: true

                color: theme.red

                horizontalAlignment:
                    Text.AlignRight
            }


            RowLayout {

                Button {
                    text: "⛔ تعطيل"

                    onClicked: {

                        disableFactoryDialog.factoryId =
                            editFactoryDialog.factoryId

                        disableFactoryDialog.factoryName =
                            editFactoryNameField.text

                        disableFactoryDialog.open()
                    }
                }


                Item {
                    Layout.fillWidth: true
                }


                Button {
                    text: "حفظ"

                    onClicked: {

                        var saved =
                            masterDataVM.updateFactory(
                                editFactoryDialog.factoryId,
                                editFactoryNameField.text,
                                editFactoryCodeField.text,
                                editFactoryNotesField.text
                            )


                        if (saved) {

                            editFactoryDialog.close()

                        } else {

                            editFactoryError.text =
                                masterDataVM.actionMessage
                        }
                    }
                }
            }
        }
    }


    // ========================================================
    // DISABLE FACTORY
    // ========================================================

    Dialog {
        id: disableFactoryDialog

        property int factoryId: 0
        property string factoryName: ""

        modal: true
        dim: true
        Overlay.modal: Rectangle {
        color: "#80000000"}


        ColumnLayout {

            Label {
                text:
                    "تعطيل المصنع «"
                    + disableFactoryDialog.factoryName
                    + "»؟"

                font.family: "Cairo"
            }


            RowLayout {

                Button {
                    text: "إلغاء"

                    onClicked:
                        disableFactoryDialog.close()
                }


                Button {
                    text: "تعطيل"

                    onClicked: {

                        var result =
                            masterDataVM.disableFactory(
                                disableFactoryDialog.factoryId
                            )


                        if (result) {

                            disableFactoryDialog.close()
                            editFactoryDialog.close()

                            factoriesPage.showDisabled =
                                true
                        }
                    }
                }
            }
        }
    }


    // ========================================================
    // REACTIVATE FACTORY
    // ========================================================

    Dialog {
        id: reactivateFactoryDialog

        property int factoryId: 0
        property string factoryName: ""

        modal: true
        dim: true
        Overlay.modal: Rectangle {
        color: "#80000000"}


        ColumnLayout {

            Label {
                text:
                    "إعادة تفعيل المصنع «"
                    + reactivateFactoryDialog.factoryName
                    + "»؟"

                font.family: "Cairo"
            }


            RowLayout {

                Button {
                    text: "إلغاء"

                    onClicked:
                        reactivateFactoryDialog.close()
                }


                Button {
                    text: "إعادة التفعيل"

                    onClicked: {

                        var result =
                            masterDataVM.reactivateFactory(
                                reactivateFactoryDialog.factoryId
                            )


                        if (result) {

                            reactivateFactoryDialog.close()

                            factoriesPage.showDisabled =
                                false
                        }
                    }
                }
            }
        }
    }


    // ========================================================
    // NEW ITEM TYPE
    // ========================================================

    Dialog {
        id: newItemTypeDialog

        modal: true
        dim: true
        Overlay.modal: Rectangle {
        color: "#80000000"}

        width: 520

        x:
            Math.round(
                (root.width - width) / 2
            )

        y:
            Math.round(
                (root.height - height) / 2
            )

        padding: 0


        background: Rectangle {
            color: theme.bgSecondary

            radius: 10

            border.color: theme.border
            border.width: 1
        }


        contentItem: ColumnLayout {
            spacing: 14


            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 62

                color: theme.bgTertiary

                radius: 10


                Label {
                    anchors.centerIn: parent

                    text:
                        "💍  إضافة صنف جديد"

                    color: theme.gold

                    font.family: "Cairo"
                    font.pixelSize: 17
                    font.bold: true
                }
            }


            ColumnLayout {
                Layout.fillWidth: true

                Layout.leftMargin: 22
                Layout.rightMargin: 22

                spacing: 8


                Label {
                    text: "اسم الصنف *"

                    Layout.fillWidth: true

                    color: theme.textSecondary

                    font.family: "Cairo"

                    horizontalAlignment:
                        Text.AlignRight
                }


                TextField {
                    id: newItemTypeNameField

                    Layout.fillWidth: true
                    Layout.preferredHeight: 44

                    placeholderText: "مثال: خاتم"

                    font.family: "Cairo"

                    horizontalAlignment:
                        TextInput.AlignRight
                }


                Label {
                    text: "الكود"

                    Layout.fillWidth: true

                    color: theme.textSecondary

                    font.family: "Cairo"

                    horizontalAlignment:
                        Text.AlignRight
                }


                TextField {
                    id: newItemTypeCodeField

                    Layout.fillWidth: true
                    Layout.preferredHeight: 44

                    placeholderText: "اختياري"

                    font.family: "Cairo"

                    horizontalAlignment:
                        TextInput.AlignRight
                }


                Label {
                    text: "الفئة الافتراضية"

                    Layout.fillWidth: true

                    color: theme.textSecondary

                    font.family: "Cairo"

                    horizontalAlignment:
                        Text.AlignRight
                }


                ComboBox {
                    id: newItemTypeCategory

                    Layout.fillWidth: true
                    Layout.preferredHeight: 44

                    model: categoryChoiceModel

                    textRole: "label"

                    font.family: "Cairo"

                    LayoutMirroring.enabled: true
                    LayoutMirroring.childrenInherit: true
                }


                Label {
                    text:
                        "يمكن تغيير الفئة عند تسجيل قطعة فعلية في المخزون."

                    Layout.fillWidth: true

                    color: theme.textDisabled

                    font.family: "Cairo"
                    font.pixelSize: 10

                    horizontalAlignment:
                        Text.AlignRight

                    wrapMode: Text.WordWrap
                }


                Label {
                    text: "ملاحظات"

                    Layout.fillWidth: true

                    color: theme.textSecondary

                    font.family: "Cairo"

                    horizontalAlignment:
                        Text.AlignRight
                }


                TextArea {
                    id: newItemTypeNotesField

                    Layout.fillWidth: true
                    Layout.preferredHeight: 90

                    placeholderText:
                        "ملاحظات اختيارية..."

                    font.family: "Cairo"

                    horizontalAlignment:
                        TextEdit.AlignRight

                    wrapMode: TextEdit.Wrap
                }


                Label {
                    id: newItemTypeError

                    visible: text.length > 0

                    Layout.fillWidth: true

                    color: theme.red

                    font.family: "Cairo"

                    horizontalAlignment:
                        Text.AlignRight

                    wrapMode: Text.WordWrap
                }
            }


            RowLayout {
                Layout.fillWidth: true

                Layout.leftMargin: 22
                Layout.rightMargin: 22
                Layout.bottomMargin: 20

                spacing: 10


                Item {
                    Layout.fillWidth: true
                }


                Button {
                    text: "إلغاء"

                    font.family: "Cairo"

                    onClicked:
                        newItemTypeDialog.close()
                }


                Button {
                    text: "💾  حفظ الصنف"

                    font.family: "Cairo"
                    font.bold: true


                    onClicked: {

                        newItemTypeError.text = ""

                        if (
                            newItemTypeNameField.text
                            .trim()
                            .length === 0
                        ) {

                            newItemTypeError.text =
                                "اسم الصنف مطلوب."

                            newItemTypeNameField
                                .forceActiveFocus()

                            return
                        }


                        var categoryId = 0

                        if (
                            newItemTypeCategory.currentIndex >= 0
                        ) {

                            categoryId =
                                Number(
                                    categoryChoiceModel
                                    .get(
                                        newItemTypeCategory
                                        .currentIndex
                                    )
                                    .categoryId
                                )
                        }


                        var saved =
                            masterDataVM.createItemType(
                                newItemTypeNameField.text,
                                newItemTypeCodeField.text,
                                categoryId,
                                newItemTypeNotesField.text
                            )


                        if (saved) {

                            newItemTypeDialog.close()

                            itemTypesPage.showDisabled =
                                false

                        } else {

                            newItemTypeError.text =
                                masterDataVM.actionMessage
                        }
                    }
                }
            }
        }
    }


    // ========================================================
    // EDIT ITEM TYPE
    // ========================================================

    Dialog {
        id: editItemTypeDialog

        property int itemTypeId: 0

        modal: true
        dim: true
        Overlay.modal: Rectangle {
        color: "#80000000"}

        width: 520

        x:
            Math.round(
                (root.width - width) / 2
            )

        y:
            Math.round(
                (root.height - height) / 2
            )

        padding: 0


        function openItemType(itemData) {

            root.rebuildCategoryChoiceModel()

            itemTypeId =
                Number(itemData.id)

            editItemTypeNameField.text =
                itemData.name || ""

            editItemTypeCodeField.text =
                itemData.code || ""

            editItemTypeNotesField.text =
                itemData.notes || ""

            editItemTypeCategory.currentIndex =
                root.categoryIndexById(
                    itemData.default_category_id
                )

            editItemTypeError.text = ""

            open()
        }


        background: Rectangle {
            color: theme.bgSecondary

            radius: 10

            border.color: theme.border
            border.width: 1
        }


        contentItem: ColumnLayout {
            spacing: 14


            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 62

                color: theme.bgTertiary

                radius: 10


                Label {
                    anchors.centerIn: parent

                    text:
                        "✏️  تعديل الصنف"

                    color: theme.gold

                    font.family: "Cairo"
                    font.pixelSize: 17
                    font.bold: true
                }
            }


            ColumnLayout {
                Layout.fillWidth: true

                Layout.leftMargin: 22
                Layout.rightMargin: 22

                spacing: 8


                Label {
                    text:
                        "رقم الصنف: #"
                        + editItemTypeDialog.itemTypeId

                    Layout.fillWidth: true

                    color: theme.textDisabled

                    font.family: "Cairo"
                    font.pixelSize: 10

                    horizontalAlignment:
                        Text.AlignRight
                }


                Label {
                    text: "اسم الصنف *"

                    Layout.fillWidth: true

                    color: theme.textSecondary

                    font.family: "Cairo"

                    horizontalAlignment:
                        Text.AlignRight
                }


                TextField {
                    id: editItemTypeNameField

                    Layout.fillWidth: true
                    Layout.preferredHeight: 44

                    font.family: "Cairo"

                    horizontalAlignment:
                        TextInput.AlignRight
                }


                Label {
                    text: "الكود"

                    Layout.fillWidth: true

                    color: theme.textSecondary

                    font.family: "Cairo"

                    horizontalAlignment:
                        Text.AlignRight
                }


                TextField {
                    id: editItemTypeCodeField

                    Layout.fillWidth: true
                    Layout.preferredHeight: 44

                    font.family: "Cairo"

                    horizontalAlignment:
                        TextInput.AlignRight
                }


                Label {
                    text: "الفئة الافتراضية"

                    Layout.fillWidth: true

                    color: theme.textSecondary

                    font.family: "Cairo"

                    horizontalAlignment:
                        Text.AlignRight
                }


                ComboBox {
                    id: editItemTypeCategory

                    Layout.fillWidth: true
                    Layout.preferredHeight: 44

                    model: categoryChoiceModel

                    textRole: "label"

                    font.family: "Cairo"

                    LayoutMirroring.enabled: true
                    LayoutMirroring.childrenInherit: true
                }


                Label {
                    text: "ملاحظات"

                    Layout.fillWidth: true

                    color: theme.textSecondary

                    font.family: "Cairo"

                    horizontalAlignment:
                        Text.AlignRight
                }


                TextArea {
                    id: editItemTypeNotesField

                    Layout.fillWidth: true
                    Layout.preferredHeight: 90

                    font.family: "Cairo"

                    horizontalAlignment:
                        TextEdit.AlignRight

                    wrapMode: TextEdit.Wrap
                }


                Label {
                    id: editItemTypeError

                    visible: text.length > 0

                    Layout.fillWidth: true

                    color: theme.red

                    font.family: "Cairo"

                    horizontalAlignment:
                        Text.AlignRight

                    wrapMode: Text.WordWrap
                }
            }


            RowLayout {
                Layout.fillWidth: true

                Layout.leftMargin: 22
                Layout.rightMargin: 22
                Layout.bottomMargin: 20

                spacing: 10


                Button {
                    text:
                        "⛔  تعطيل الصنف"

                    font.family: "Cairo"

                    onClicked: {

                        disableItemTypeDialog.itemTypeId =
                            editItemTypeDialog.itemTypeId

                        disableItemTypeDialog.itemTypeName =
                            editItemTypeNameField.text

                        disableItemTypeDialog.open()
                    }
                }


                Item {
                    Layout.fillWidth: true
                }


                Button {
                    text: "إلغاء"

                    font.family: "Cairo"

                    onClicked:
                        editItemTypeDialog.close()
                }


                Button {
                    text:
                        "💾  حفظ التعديلات"

                    font.family: "Cairo"
                    font.bold: true


                    onClicked: {

                        editItemTypeError.text = ""

                        if (
                            editItemTypeNameField.text
                            .trim()
                            .length === 0
                        ) {

                            editItemTypeError.text =
                                "اسم الصنف مطلوب."

                            return
                        }


                        var categoryId = 0

                        if (
                            editItemTypeCategory.currentIndex >= 0
                        ) {

                            categoryId =
                                Number(
                                    categoryChoiceModel
                                    .get(
                                        editItemTypeCategory
                                        .currentIndex
                                    )
                                    .categoryId
                                )
                        }


                        var saved =
                            masterDataVM.updateItemType(
                                editItemTypeDialog.itemTypeId,
                                editItemTypeNameField.text,
                                editItemTypeCodeField.text,
                                categoryId,
                                editItemTypeNotesField.text
                            )


                        if (saved) {

                            editItemTypeDialog.close()

                        } else {

                            editItemTypeError.text =
                                masterDataVM.actionMessage
                        }
                    }
                }
            }
        }
    }


    // ========================================================
    // DISABLE ITEM TYPE
    // ========================================================

    Dialog {
        id: disableItemTypeDialog

        property int itemTypeId: 0
        property string itemTypeName: ""

        modal: true
        dim: true
        Overlay.modal: Rectangle {
        color: "#80000000"}
        width: 430


        ColumnLayout {
            spacing: 14


            Label {
                Layout.fillWidth: true

                text: "⛔  تعطيل الصنف"

                color: theme.red

                font.family: "Cairo"
                font.pixelSize: 17
                font.bold: true

                horizontalAlignment:
                    Text.AlignHCenter
            }


            Label {
                Layout.fillWidth: true

                text:
                    "هل تريد تعطيل الصنف «"
                    + disableItemTypeDialog.itemTypeName
                    + "»؟"

                color: theme.textPrimary

                font.family: "Cairo"

                horizontalAlignment:
                    Text.AlignHCenter

                wrapMode:
                    Text.WordWrap
            }


            Label {
                Layout.fillWidth: true

                text:
                    "لن يتم حذف الصنف أو تاريخه، "
                    + "ويمكن إعادة تفعيله لاحقاً."

                color: theme.textMuted

                font.family: "Cairo"
                font.pixelSize: 11

                horizontalAlignment:
                    Text.AlignHCenter

                wrapMode:
                    Text.WordWrap
            }


            RowLayout {

                Button {
                    text: "إلغاء"

                    onClicked:
                        disableItemTypeDialog.close()
                }


                Item {
                    Layout.fillWidth: true
                }


                Button {
                    text: "تعطيل"

                    onClicked: {

                        var result =
                            masterDataVM.disableItemType(
                                disableItemTypeDialog.itemTypeId
                            )


                        if (result) {

                            disableItemTypeDialog.close()
                            editItemTypeDialog.close()

                            itemTypesPage.showDisabled =
                                true
                        }
                    }
                }
            }
        }
    }


    // ========================================================
    // REACTIVATE ITEM TYPE
    // ========================================================

    Dialog {
        id: reactivateItemTypeDialog

        property int itemTypeId: 0
        property string itemTypeName: ""

        modal: true
        dim: true
        Overlay.modal: Rectangle {
        color: "#80000000"}
        width: 430


        ColumnLayout {
            spacing: 14


            Label {
                Layout.fillWidth: true

                text:
                    "↻  إعادة تفعيل الصنف"

                color: theme.green

                font.family: "Cairo"
                font.pixelSize: 17
                font.bold: true

                horizontalAlignment:
                    Text.AlignHCenter
            }


            Label {
                Layout.fillWidth: true

                text:
                    "إعادة تفعيل الصنف «"
                    + reactivateItemTypeDialog.itemTypeName
                    + "»؟"

                color: theme.textPrimary

                font.family: "Cairo"

                horizontalAlignment:
                    Text.AlignHCenter

                wrapMode:
                    Text.WordWrap
            }


            RowLayout {

                Button {
                    text: "إلغاء"

                    onClicked:
                        reactivateItemTypeDialog.close()
                }


                Item {
                    Layout.fillWidth: true
                }


                Button {
                    text: "إعادة التفعيل"

                    onClicked: {

                        var result =
                            masterDataVM.reactivateItemType(
                                reactivateItemTypeDialog.itemTypeId
                            )


                        if (result) {

                            reactivateItemTypeDialog.close()

                            itemTypesPage.showDisabled =
                                false
                        }
                    }
                }
            }
        }
    }



    // ========================================================
    // NEW INVENTORY ITEM
    // ========================================================

    Dialog {
        id: newInventoryItemDialog

        modal: true
        dim: true

        Overlay.modal: Rectangle {
            color: "#80000000"
        }

        width:
            Math.min(
                root.width - 60,
                720
            )

        height:
            Math.min(
                root.height - 60,
                680
            )

        x:
            Math.round(
                (root.width - width) / 2
            )

        y:
            Math.round(
                (root.height - height) / 2
            )

        padding: 0

        closePolicy:
            Popup.CloseOnEscape


        background: Rectangle {
            color: theme.bgSecondary

            radius: 10

            border.color: theme.border
            border.width: 1
        }


        function selectedOldGoldBalance() {

            if (karatCombo.currentIndex < 0) {
                return 0
            }

            return oldGoldConversionVM.balanceForKarat(
                Number(
                    karatCombo.currentValue
                )
            )
        }


        function resultWeightNumber() {

            var value = Number(
                weightField.text
            )

            return isNaN(value)
                ? 0
                : value
        }


        function consumedOldGoldNumber() {

            var value = Number(
                oldGoldConsumedWeightField.text
            )

            return isNaN(value)
                ? 0
                : value
        }


        function findCategoryIndex(categoryId) {

            var wantedId = Number(categoryId || 0)

            if (wantedId <= 0) {
                return -1
            }

            var rows = masterDataVM.categories

            for (var i = 0; i < rows.length; i++) {

                if (
                    Number(rows[i].id)
                    === wantedId
                ) {
                    return i
                }
            }

            return -1
        }


        function applyDefaultCategory() {

            if (itemTypeCombo.currentIndex < 0) {
                categoryCombo.currentIndex = -1
                return
            }

            var itemType =
                masterDataVM.itemTypes[
                    itemTypeCombo.currentIndex
                ]

            if (
                !itemType
                || !itemType.default_category_id
            ) {
                categoryCombo.currentIndex = -1
                return
            }

            categoryCombo.currentIndex =
                findCategoryIndex(
                    itemType.default_category_id
                )
        }


        function resetForm() {

            employeeCombo.currentIndex = -1
            itemTypeCombo.currentIndex = -1
            karatCombo.currentIndex = -1
            categoryCombo.currentIndex = -1
            factoryCombo.currentIndex = -1

            weightField.text = ""
            sizeField.text = ""
            barcodeField.text = ""
            oldGoldConsumedWeightField.text = ""

            stockClassificationCombo.currentIndex = 0

            notesField.text = ""
            inventoryEntryError.text = ""

            inventoryVM.clearActionMessage()
            oldGoldConversionVM.clearActionMessage()
        }


        function openForNewItem() {

            masterDataVM.refresh()
            oldGoldConversionVM.refreshBalances()
            resetForm()
            open()
        }


        contentItem: ColumnLayout {
            spacing: 0


            // ------------------------------------------------
            // HEADER
            // ------------------------------------------------

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 66

                color: theme.bgTertiary

                radius: 10


                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 20
                    anchors.rightMargin: 20

                    spacing: 10


                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1


                        Label {
                            Layout.fillWidth: true

                            text: "📦 إدخال قطعة جديدة"

                            color: theme.gold

                            font.family: "Cairo"
                            font.pixelSize: 18
                            font.bold: true

                            horizontalAlignment:
                                Text.AlignRight
                        }


                        Label {
                            Layout.fillWidth: true

                            text:
                                "إدخال بضاعة جديدة أو قطعة محولة من الكسر — الباركود اختياري"

                            color: theme.textMuted

                            font.family: "Cairo"
                            font.pixelSize: 10

                            horizontalAlignment:
                                Text.AlignRight
                        }
                    }


                    Button {
                        text: "✕"
                        flat: true

                        onClicked:
                            newInventoryItemDialog.close()
                    }
                }
            }


            ScrollView {
                id: inventoryEntryScroll

                Layout.fillWidth: true
                Layout.fillHeight: true

                clip: true


                ColumnLayout {
                    width:
                        Math.max(
                            0,
                            inventoryEntryScroll.availableWidth - 36
                        )

                    x: 18

                    spacing: 11


                    Item {
                        Layout.preferredHeight: 4
                    }


                    // ------------------------------------------------
                    // EMPLOYEE
                    // ------------------------------------------------

                    Label {
                        Layout.fillWidth: true

                        text: "الموظف المسؤول *"

                        color: theme.textSecondary

                        font.family: "Cairo"
                        font.pixelSize: 12

                        horizontalAlignment:
                            Text.AlignRight
                    }


                    ComboBox {
                        id: employeeCombo

                        Layout.fillWidth: true
                        Layout.preferredHeight: 44

                        model:
                            masterDataVM.employees

                        textRole: "name"
                        valueRole: "id"

                        currentIndex: -1

                        displayText:
                            currentIndex >= 0
                            ? currentText
                            : "اختر الموظف"

                    }


                    Label {
                        visible:
                            masterDataVM.employees.length === 0

                        Layout.fillWidth: true

                        text:
                            "⚠ لا يوجد موظفون نشطون. يجب إضافة موظف قبل إدخال المخزون."

                        color: theme.amber

                        font.family: "Cairo"
                        font.pixelSize: 11

                        horizontalAlignment:
                            Text.AlignRight

                        wrapMode:
                            Text.WordWrap
                    }


                    // ------------------------------------------------
                    // ITEM TYPE + KARAT
                    // ------------------------------------------------

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12


                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 5


                            Label {
                                Layout.fillWidth: true

                                text: "الصنف *"

                                color: theme.textSecondary

                                font.family: "Cairo"
                                font.pixelSize: 12

                                horizontalAlignment:
                                    Text.AlignRight
                            }


                            ComboBox {
                                id: itemTypeCombo

                                Layout.fillWidth: true
                                Layout.preferredHeight: 44

                                model:
                                    masterDataVM.itemTypes

                                textRole: "name"
                                valueRole: "id"

                                currentIndex: -1

                                displayText:
                                    currentIndex >= 0
                                    ? currentText
                                    : "اختر الصنف"


                                onActivated:
                                    newInventoryItemDialog
                                    .applyDefaultCategory()
                            }
                        }


                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 5


                            Label {
                                Layout.fillWidth: true

                                text: "العيار *"

                                color: theme.textSecondary

                                font.family: "Cairo"
                                font.pixelSize: 12

                                horizontalAlignment:
                                    Text.AlignRight
                            }


                            ComboBox {
                                id: karatCombo

                                Layout.fillWidth: true
                                Layout.preferredHeight: 44

                                model:
                                    masterDataVM.karats

                                textRole: "name"
                                valueRole: "id"

                                currentIndex: -1

                                displayText:
                                    currentIndex >= 0
                                    ? currentText
                                    : "اختر العيار"


                                onActivated:
                                    oldGoldConversionVM.refreshBalances()
                            }
                        }
                    }


                    // ------------------------------------------------
                    // WEIGHT + SIZE
                    // ------------------------------------------------

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12


                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 5


                            Label {
                                Layout.fillWidth: true

                                text: "الوزن بالجرام *"

                                color: theme.textSecondary

                                font.family: "Cairo"
                                font.pixelSize: 12

                                horizontalAlignment:
                                    Text.AlignRight
                            }


                            TextField {
                                id: weightField

                                Layout.fillWidth: true
                                Layout.preferredHeight: 44

                                placeholderText:
                                    "مثال: 4.350"

                                color: theme.textPrimary

                                placeholderTextColor:
                                    theme.textDisabled

                                font.family: "Cairo"
                                font.pixelSize: 13

                                horizontalAlignment:
                                    TextInput.AlignRight

                                inputMethodHints:
                                    Qt.ImhFormattedNumbersOnly
                            }
                        }


                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 5


                            Label {
                                Layout.fillWidth: true

                                text: "المقاس"

                                color: theme.textSecondary

                                font.family: "Cairo"
                                font.pixelSize: 12

                                horizontalAlignment:
                                    Text.AlignRight
                            }


                            TextField {
                                id: sizeField

                                Layout.fillWidth: true
                                Layout.preferredHeight: 44

                                placeholderText: "اختياري"

                                color: theme.textPrimary

                                placeholderTextColor:
                                    theme.textDisabled

                                font.family: "Cairo"

                                horizontalAlignment:
                                    TextInput.AlignRight
                            }
                        }
                    }


                    // ------------------------------------------------
                    // CATEGORY + FACTORY
                    // ------------------------------------------------

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12


                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 5


                            Label {
                                Layout.fillWidth: true

                                text: "الفئة"

                                color: theme.textSecondary

                                font.family: "Cairo"
                                font.pixelSize: 12

                                horizontalAlignment:
                                    Text.AlignRight
                            }


                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 5


                                ComboBox {
                                    id: categoryCombo

                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 44

                                    model:
                                        masterDataVM.categories

                                    textRole: "code"
                                    valueRole: "id"

                                    currentIndex: -1

                                    displayText:
                                        currentIndex >= 0
                                        ? (
                                            currentText
                                            + " — "
                                            + masterDataVM.categories[
                                                currentIndex
                                            ].name
                                          )
                                        : "بدون فئة"

                                }


                                Button {
                                    text: "×"

                                    Layout.preferredWidth: 42
                                    Layout.preferredHeight: 44

                                    onClicked:
                                        categoryCombo.currentIndex = -1
                                }
                            }
                        }


                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 5


                            Label {
                                Layout.fillWidth: true

                                text: "المصنع"

                                color: theme.textSecondary

                                font.family: "Cairo"
                                font.pixelSize: 12

                                horizontalAlignment:
                                    Text.AlignRight
                            }


                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 5


                                ComboBox {
                                    id: factoryCombo

                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 44

                                    model:
                                        masterDataVM.factories

                                    textRole: "name"
                                    valueRole: "id"

                                    currentIndex: -1

                                    displayText:
                                        currentIndex >= 0
                                        ? currentText
                                        : "بدون مصنع"

                                }


                                Button {
                                    text: "×"

                                    Layout.preferredWidth: 42
                                    Layout.preferredHeight: 44

                                    onClicked:
                                        factoryCombo.currentIndex = -1
                                }
                            }
                        }
                    }


                    // ------------------------------------------------
                    // STOCK CLASSIFICATION + BARCODE
                    // ------------------------------------------------

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12


                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 5


                            Label {
                                Layout.fillWidth: true

                                text: "مصدر القطعة *"

                                color: theme.textSecondary

                                font.family: "Cairo"
                                font.pixelSize: 12

                                horizontalAlignment:
                                    Text.AlignRight
                            }


                            ComboBox {
                                id: stockClassificationCombo

                                Layout.fillWidth: true
                                Layout.preferredHeight: 44

                                model: [
                                    "إدخال بضاعة جديدة",
                                    "محولة من الكسر"
                                ]

                                currentIndex: 0


                                onActivated: {

                                    inventoryEntryError.text = ""

                                    if (currentIndex === 1) {
                                        oldGoldConversionVM
                                        .refreshBalances()
                                    } else {
                                        oldGoldConsumedWeightField.text = ""
                                    }
                                }
                            }
                        }


                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 5


                            Label {
                                Layout.fillWidth: true

                                text: "الباركود"

                                color: theme.textSecondary

                                font.family: "Cairo"
                                font.pixelSize: 12

                                horizontalAlignment:
                                    Text.AlignRight
                            }


                            TextField {
                                id: barcodeField

                                Layout.fillWidth: true
                                Layout.preferredHeight: 44

                                placeholderText:
                                    "اختياري — يمكن تركه فارغاً"

                                color: theme.textPrimary

                                placeholderTextColor:
                                    theme.textDisabled

                                font.family: "Cairo"

                                horizontalAlignment:
                                    TextInput.AlignRight
                            }
                        }
                    }


                    // ------------------------------------------------
                    // OLD GOLD GRAMS — only for "محولة من الكسر"
                    // ------------------------------------------------

                    Rectangle {
                        visible:
                            stockClassificationCombo.currentIndex === 1

                        Layout.fillWidth: true
                        Layout.preferredHeight: 190

                        color: theme.bgTertiary

                        radius: theme.radiusSmall

                        border.color:
                            theme.goldDark

                        border.width: 1


                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 12

                            spacing: 8


                            Label {
                                Layout.fillWidth: true

                                text:
                                    "تحويل من رصيد الكسر"

                                color: theme.gold

                                font.family: "Cairo"
                                font.pixelSize: 13
                                font.bold: true

                                horizontalAlignment:
                                    Text.AlignRight
                            }


                            Label {
                                Layout.fillWidth: true

                                text:
                                    karatCombo.currentIndex >= 0
                                    ? (
                                        "الرصيد المتاح — "
                                        + karatCombo.currentText
                                        + ": "
                                        + newInventoryItemDialog
                                          .selectedOldGoldBalance()
                                          .toFixed(3)
                                        + " جم"
                                      )
                                    : "اختر العيار أولاً لعرض رصيد الكسر."

                                color:
                                    karatCombo.currentIndex >= 0
                                    && newInventoryItemDialog
                                       .selectedOldGoldBalance() > 0
                                    ? theme.green
                                    : theme.amber

                                font.family: "Cairo"
                                font.bold: true

                                horizontalAlignment:
                                    Text.AlignRight
                            }


                            RowLayout {
                                Layout.fillWidth: true

                                layoutDirection:
                                    Qt.RightToLeft

                                spacing: 12


                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 5


                                    Label {
                                        Layout.fillWidth: true

                                        text:
                                            "وزن الكسر المسحوب بالجرام *"

                                        color:
                                            theme.textSecondary

                                        font.family: "Cairo"
                                        font.pixelSize: 12

                                        horizontalAlignment:
                                            Text.AlignRight
                                    }


                                    TextField {
                                        id:
                                            oldGoldConsumedWeightField

                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 44

                                        placeholderText:
                                            "مثال: 10.000"

                                        color:
                                            theme.textPrimary

                                        placeholderTextColor:
                                            theme.textDisabled

                                        font.family:
                                            "Cairo"

                                        horizontalAlignment:
                                            TextInput.AlignRight

                                        inputMethodHints:
                                            Qt.ImhFormattedNumbersOnly
                                    }
                                }


                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 5


                                    Label {
                                        Layout.fillWidth: true

                                        text:
                                            "فرق الوزن"

                                        color:
                                            theme.textSecondary

                                        font.family: "Cairo"
                                        font.pixelSize: 12

                                        horizontalAlignment:
                                            Text.AlignRight
                                    }


                                    Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 44

                                        color:
                                            theme.bgSecondary

                                        radius: 6

                                        border.color:
                                            theme.border

                                        border.width: 1


                                        Label {
                                            anchors.fill: parent
                                            anchors.margins: 10

                                            text:
                                                (
                                                    newInventoryItemDialog
                                                    .resultWeightNumber()
                                                    -
                                                    newInventoryItemDialog
                                                    .consumedOldGoldNumber()
                                                ).toFixed(3)
                                                + " جم"

                                            color:
                                                Math.abs(
                                                    newInventoryItemDialog
                                                    .resultWeightNumber()
                                                    -
                                                    newInventoryItemDialog
                                                    .consumedOldGoldNumber()
                                                ) > 0.0005
                                                ? theme.amber
                                                : theme.green

                                            font.family:
                                                "Cairo"

                                            font.bold:
                                                true

                                            horizontalAlignment:
                                                Text.AlignRight

                                            verticalAlignment:
                                                Text.AlignVCenter
                                        }
                                    }
                                }
                            }


                            Label {
                                Layout.fillWidth: true

                                text:
                                    "سيتم خصم وزن الكسر من الرصيد حسب العيار، وإنشاء القطعة الحالية دائماً كـ بضاعة جديدة."

                                color: theme.textMuted

                                font.family: "Cairo"
                                font.pixelSize: 10

                                wrapMode:
                                    Text.WordWrap

                                horizontalAlignment:
                                    Text.AlignRight
                            }
                        }
                    }


                    // ------------------------------------------------
                    // NOTES
                    // ------------------------------------------------

                    Label {
                        Layout.fillWidth: true

                        text: "ملاحظات"

                        color: theme.textSecondary

                        font.family: "Cairo"
                        font.pixelSize: 12

                        horizontalAlignment:
                            Text.AlignRight
                    }


                    TextArea {
                        id: notesField

                        Layout.fillWidth: true
                        Layout.preferredHeight: 76

                        placeholderText: "اختياري"

                        color: theme.textPrimary

                        placeholderTextColor:
                            theme.textDisabled

                        font.family: "Cairo"

                        horizontalAlignment:
                            TextEdit.AlignRight

                        wrapMode:
                            TextEdit.Wrap
                    }


                    Label {
                        id: inventoryEntryError

                        visible:
                            text.length > 0

                        Layout.fillWidth: true

                        color: theme.red

                        font.family: "Cairo"
                        font.pixelSize: 12
                        font.bold: true

                        horizontalAlignment:
                            Text.AlignRight

                        wrapMode:
                            Text.WordWrap
                    }


                    Item {
                        Layout.preferredHeight: 6
                    }
                }
            }


            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 68

                color: theme.bgTertiary


                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 18
                    anchors.rightMargin: 18

                    spacing: 10


                    Label {
                        Layout.fillWidth: true

                        text:
                            "* الحقول المطلوبة"

                        color: theme.textDisabled

                        font.family: "Cairo"
                        font.pixelSize: 10

                        horizontalAlignment:
                            Text.AlignRight
                    }


                    Button {
                        text: "إلغاء"

                        onClicked:
                            newInventoryItemDialog.close()
                    }


                    Button {
                        text: "💾  حفظ القطعة"

                        enabled:
                            masterDataVM.employees.length > 0

                        font.family: "Cairo"
                        font.bold: true

                        onClicked: {

                            inventoryEntryError.text = ""


                            var employeeId =
                                employeeCombo.currentIndex >= 0
                                ? Number(
                                    employeeCombo.currentValue
                                  )
                                : 0

                            var itemTypeId =
                                itemTypeCombo.currentIndex >= 0
                                ? Number(
                                    itemTypeCombo.currentValue
                                  )
                                : 0

                            var karatId =
                                karatCombo.currentIndex >= 0
                                ? Number(
                                    karatCombo.currentValue
                                  )
                                : 0

                            var categoryId =
                                categoryCombo.currentIndex >= 0
                                ? Number(
                                    categoryCombo.currentValue
                                  )
                                : 0

                            var factoryId =
                                factoryCombo.currentIndex >= 0
                                ? Number(
                                    factoryCombo.currentValue
                                  )
                                : 0

                            var saved = false


                            if (
                                stockClassificationCombo
                                .currentIndex === 0
                            ) {
                                saved =
                                    inventoryVM.createInventoryItem(
                                        employeeId,
                                        itemTypeId,
                                        karatId,
                                        categoryId,
                                        factoryId,
                                        weightField.text,
                                        sizeField.text,
                                        "NEW_STOCK",
                                        barcodeField.text,
                                        notesField.text
                                    )

                                if (!saved) {
                                    inventoryEntryError.text =
                                        inventoryVM.actionMessage
                                }

                            } else {
                                saved =
                                    oldGoldConversionVM
                                    .convertToNewStock(
                                        employeeId,
                                        itemTypeId,
                                        karatId,
                                        categoryId,
                                        factoryId,
                                        weightField.text,
                                        oldGoldConsumedWeightField.text,
                                        sizeField.text,
                                        barcodeField.text,
                                        notesField.text
                                    )

                                if (!saved) {
                                    inventoryEntryError.text =
                                        oldGoldConversionVM
                                        .actionMessage
                                }
                            }


                            if (saved) {

                                inventoryVM.refresh()
                                oldGoldConversionVM.refreshBalances()

                                newInventoryItemDialog.close()
                            }
                        }
                    }
                }
            }
        }
    }


    // ========================================================
    // INVENTORY ITEM DETAILS — READ ONLY
    // ========================================================

    Dialog {
        id: inventoryItemDetailsDialog

        modal: true
        dim: true
        padding: 0

        width: Math.min(root.width - 60, 760)
        height: Math.min(root.height - 60, 680)

        x: Math.round((root.width - width) / 2)
        y: Math.round((root.height - height) / 2)

        closePolicy:
            Popup.CloseOnEscape
            | Popup.CloseOnPressOutside

        Overlay.modal: Rectangle {
            color: "#80000000"
        }

        background: Rectangle {
            color: theme.bgSecondary
            radius: 10
            border.color: theme.border
            border.width: 1
        }

        function valueOrDash(value) {
            if (
                value === null
                || value === undefined
                || String(value).trim().length === 0
            ) {
                return "—"
            }
            return String(value)
        }

        function weightText(value) {
            var numberValue = Number(value)
            return isNaN(numberValue)
                ? "—"
                : numberValue.toFixed(3) + " جم"
        }

        function stockClassText(value) {
            if (value === "NEW_STOCK")
                return "بضاعة جديدة"
            if (value === "OLD_STOCK")
                return "بضاعة قديمة"
            return valueOrDash(value)
        }

        function statusText(value) {
            if (value === "IN_STOCK")
                return "بالمخزون"
            if (value === "SOLD")
                return "مباعة"
            if (value === "SCRAPPED")
                return "محولة / مستبعدة"
            if (value === "REMOVED")
                return "مستبعدة"
            return valueOrDash(value)
        }

        function sourceText(value) {
            if (value === "MANUAL_ENTRY")
                return "إدخال يدوي"
            if (value === "MARKET_PURCHASE")
                return "شراء من السوق"
            if (value === "OLD_GOLD_MANUFACTURE")
                return "محولة من الكسر"
            if (value === "LEGACY_MIGRATION")
                return "بيانات مرحّلة"
            return valueOrDash(value)
        }

        contentItem: ColumnLayout {
            spacing: 0

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 92

                color: theme.bgTertiary
                radius: 10

                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    height: 1
                    color: theme.border
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 20
                    anchors.rightMargin: 20
                    spacing: 14

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Label {
                            Layout.fillWidth: true
                            text:
                                "تفاصيل القطعة  #"
                                + inventoryItemDetailsDialog.valueOrDash(
                                    root.selectedInventoryItem.inventory_item_id
                                  )
                            color: theme.gold
                            font.pixelSize: 20
                            font.bold: true
                            horizontalAlignment: Text.AlignRight
                        }

                        Label {
                            Layout.fillWidth: true
                            text:
                                inventoryItemDetailsDialog.valueOrDash(
                                    root.selectedInventoryItem.item_type_name
                                )
                                + "  •  عيار "
                                + inventoryItemDetailsDialog.valueOrDash(
                                    root.selectedInventoryItem.karat_value
                                )
                            color: theme.textSecondary
                            font.pixelSize: 12
                            horizontalAlignment: Text.AlignRight
                        }
                    }

                    Rectangle {
                        Layout.preferredWidth: 112
                        Layout.preferredHeight: 34
                        radius: 7

                        color:
                            root.selectedInventoryItem.stock_classification
                            === "OLD_STOCK"
                            ? "#29241A"
                            : "#16271F"

                        border.color:
                            root.selectedInventoryItem.stock_classification
                            === "OLD_STOCK"
                            ? theme.amber
                            : theme.green
                        border.width: 1

                        Label {
                            anchors.centerIn: parent
                            text:
                                inventoryItemDetailsDialog.stockClassText(
                                    root.selectedInventoryItem
                                    .stock_classification
                                )
                            color:
                                root.selectedInventoryItem.stock_classification
                                === "OLD_STOCK"
                                ? theme.amber
                                : theme.green
                            font.pixelSize: 10
                            font.bold: true
                        }
                    }
                }
            }

            ScrollView {
                id: inventoryDetailsScroll

                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true

                ScrollBar.horizontal.policy:
                    ScrollBar.AlwaysOff

                ColumnLayout {
                    width:
                        Math.max(
                            0,
                            inventoryDetailsScroll.availableWidth - 36
                        )
                    x: 18
                    spacing: 12

                    Item {
                        Layout.preferredHeight: 4
                    }

                    Rectangle {
                        id: inventoryDetailsDataCard

                        Layout.fillWidth: true

                        // Let the card grow with Cairo text and all rows.
                        // This prevents the last rows from spilling into Notes.
                        Layout.preferredHeight:
                            inventoryDetailsGrid.implicitHeight + 32

                        Layout.minimumHeight:
                            inventoryDetailsGrid.implicitHeight + 32

                        color: theme.bgTertiary
                        radius: theme.radiusSmall
                        border.color: theme.border
                        border.width: 1

                        GridLayout {
                            id: inventoryDetailsGrid

                            // Project rule: Arabic form labels stay on the right.
                            layoutDirection: Qt.RightToLeft

                            anchors.top: parent.top
                            anchors.left: parent.left
                            anchors.right: parent.right

                            anchors.topMargin: 16
                            anchors.leftMargin: 16
                            anchors.rightMargin: 16

                            columns: 2
                            columnSpacing: 30
                            rowSpacing: 12

                            Label {
                                Layout.fillWidth: true
                                text: "الوزن"
                                color: theme.textMuted
                                horizontalAlignment: Text.AlignRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text:
                                    inventoryItemDetailsDialog.weightText(
                                        root.selectedInventoryItem.weight_grams
                                    )
                                color: theme.textPrimary
                                font.bold: true
                                horizontalAlignment: Text.AlignRight
                            }

                            Label {
                                Layout.fillWidth: true
                                text: "الباركود"
                                color: theme.textMuted
                                horizontalAlignment: Text.AlignRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text:
                                    root.selectedInventoryItem.barcode_value
                                    ? root.selectedInventoryItem.barcode_value
                                    : "بدون باركود"
                                color:
                                    root.selectedInventoryItem.barcode_value
                                    ? theme.textPrimary
                                    : theme.amber
                                horizontalAlignment: Text.AlignRight
                            }

                            Label {
                                Layout.fillWidth: true
                                text: "الحالة"
                                color: theme.textMuted
                                horizontalAlignment: Text.AlignRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text:
                                    inventoryItemDetailsDialog.statusText(
                                        root.selectedInventoryItem
                                        .inventory_status
                                    )
                                color: theme.green
                                horizontalAlignment: Text.AlignRight
                            }

                            Label {
                                Layout.fillWidth: true
                                text: "الصنف"
                                color: theme.textMuted
                                horizontalAlignment: Text.AlignRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text:
                                    inventoryItemDetailsDialog.valueOrDash(
                                        root.selectedInventoryItem
                                        .item_type_name
                                    )
                                color: theme.textPrimary
                                horizontalAlignment: Text.AlignRight
                            }

                            Label {
                                Layout.fillWidth: true
                                text: "العيار"
                                color: theme.textMuted
                                horizontalAlignment: Text.AlignRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text:
                                    inventoryItemDetailsDialog.valueOrDash(
                                        root.selectedInventoryItem.karat_value
                                    )
                                color: theme.textPrimary
                                horizontalAlignment: Text.AlignRight
                            }

                            Label {
                                Layout.fillWidth: true
                                text: "الفئة"
                                color: theme.textMuted
                                horizontalAlignment: Text.AlignRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text:
                                    inventoryItemDetailsDialog.valueOrDash(
                                        root.selectedInventoryItem.category_code
                                    )
                                    + (
                                        root.selectedInventoryItem.category_name
                                        ? " — "
                                          + root.selectedInventoryItem
                                            .category_name
                                        : ""
                                      )
                                color: theme.textPrimary
                                horizontalAlignment: Text.AlignRight
                            }

                            Label {
                                Layout.fillWidth: true
                                text: "المصنع"
                                color: theme.textMuted
                                horizontalAlignment: Text.AlignRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text:
                                    inventoryItemDetailsDialog.valueOrDash(
                                        root.selectedInventoryItem.factory_name
                                    )
                                color: theme.textPrimary
                                horizontalAlignment: Text.AlignRight
                            }

                            Label {
                                Layout.fillWidth: true
                                text: "المقاس"
                                color: theme.textMuted
                                horizontalAlignment: Text.AlignRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text:
                                    inventoryItemDetailsDialog.valueOrDash(
                                        root.selectedInventoryItem.size
                                    )
                                color: theme.textPrimary
                                horizontalAlignment: Text.AlignRight
                            }

                            Label {
                                Layout.fillWidth: true
                                text: "مصدر الإدخال"
                                color: theme.textMuted
                                horizontalAlignment: Text.AlignRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text:
                                    inventoryItemDetailsDialog.sourceText(
                                        root.selectedInventoryItem
                                        .acquisition_source
                                    )
                                color: theme.textPrimary
                                horizontalAlignment: Text.AlignRight
                            }

                            Label {
                                Layout.fillWidth: true
                                text: "تاريخ الإدخال"
                                color: theme.textMuted
                                horizontalAlignment: Text.AlignRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text:
                                    inventoryItemDetailsDialog.valueOrDash(
                                        root.selectedInventoryItem.acquired_at
                                    )
                                color: theme.textPrimary

                                wrapMode: Text.WrapAnywhere

                                horizontalAlignment: Text.AlignRight
                            }

                            Label {
                                Layout.fillWidth: true
                                text: "رقم الموظف"
                                color: theme.textMuted
                                horizontalAlignment: Text.AlignRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text:
                                    inventoryItemDetailsDialog.valueOrDash(
                                        root.selectedInventoryItem
                                        .entered_by_employee_id
                                    )
                                color: theme.textPrimary
                                horizontalAlignment: Text.AlignRight
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight:
                            Math.max(
                                90,
                                inventoryDetailsNotes.implicitHeight + 48
                            )

                        color: theme.bgTertiary
                        radius: theme.radiusSmall
                        border.color: theme.border
                        border.width: 1

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 5

                            Label {
                                Layout.fillWidth: true
                                text: "الملاحظات"
                                color: theme.textMuted
                                horizontalAlignment: Text.AlignRight
                            }

                            Label {
                                id: inventoryDetailsNotes

                                Layout.fillWidth: true
                                text:
                                    inventoryItemDetailsDialog.valueOrDash(
                                        root.selectedInventoryItem.notes
                                    )
                                color: theme.textSecondary
                                wrapMode: Text.WordWrap
                                horizontalAlignment: Text.AlignRight
                            }
                        }
                    }

                    Item {
                        Layout.preferredHeight: 4
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 64

                color: theme.bgTertiary
                border.color: theme.border
                border.width: 1

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 18
                    anchors.rightMargin: 18
                    spacing: 10

                    Label {
                        Layout.fillWidth: true
                        text:
                            "عرض للقراءة فقط — التعديل والحركات ستكون إجراءات مستقلة."
                        color: theme.textDisabled
                        font.pixelSize: 10
                        horizontalAlignment: Text.AlignRight
                    }

                    Button {
                        text: "إغلاق"
                        onClicked:
                            inventoryItemDetailsDialog.close()
                    }
                }
            }
        }
    }


    // ========================================================
    // BULK BARCODE PRINT QUEUE
    // ========================================================

    Dialog {
        id: barcodePrintQueueDialog

        modal: true
        dim: true
        padding: 0

        width:
            Math.min(
                root.width - 70,
                900
            )

        height:
            Math.min(
                root.height - 70,
                720
            )

        x:
            Math.round(
                (root.width - width) / 2
            )

        y:
            Math.round(
                (root.height - height) / 2
            )

        closePolicy:
            Popup.CloseOnEscape

        Overlay.modal: Rectangle {
            color: "#80000000"
        }

        onClosed:
            barcodeVM.clearQueue()

        background: Rectangle {
            color: theme.bgSecondary

            radius: 10

            border.color:
                theme.border

            border.width: 1
        }


        contentItem: ColumnLayout {
            spacing: 0


            // ------------------------------------------------
            // HEADER
            // ------------------------------------------------

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 78

                color: theme.bgTertiary

                radius: 10

                border.color:
                    theme.border

                border.width: 1


                RowLayout {
                    anchors.fill: parent

                    anchors.leftMargin: 18
                    anchors.rightMargin: 18

                    layoutDirection:
                        Qt.RightToLeft

                    spacing: 10


                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2


                        Label {
                            Layout.fillWidth: true

                            text:
                                "🖨  قائمة طباعة الباركود"

                            color:
                                theme.gold

                            font.family: "Cairo"
                            font.pixelSize: 18
                            font.bold: true

                            horizontalAlignment:
                                Text.AlignRight
                        }


                        Label {
                            Layout.fillWidth: true

                            text:
                                "نفس تصميم وطريقة طباعة البروتوتايب — باركودان في كل لاصقة"

                            color:
                                theme.textMuted

                            font.family: "Cairo"
                            font.pixelSize: 11

                            horizontalAlignment:
                                Text.AlignRight
                        }
                    }


                    Button {
                        text: "✕"

                        flat: true

                        onClicked:
                            barcodePrintQueueDialog.close()
                    }
                }
            }


            // ------------------------------------------------
            // SUMMARY
            // ------------------------------------------------

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 86

                Layout.leftMargin: 16
                Layout.rightMargin: 16
                Layout.topMargin: 14

                color: theme.bgTertiary

                radius: theme.radiusSmall

                border.color:
                    theme.border

                border.width: 1


                RowLayout {
                    anchors.fill: parent

                    anchors.leftMargin: 14
                    anchors.rightMargin: 14

                    layoutDirection:
                        Qt.RightToLeft

                    spacing: 10


                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Label {
                            Layout.fillWidth: true

                            text: "القطع المحددة"

                            color: theme.textMuted

                            font.family: "Cairo"
                            font.pixelSize: 10

                            horizontalAlignment:
                                Text.AlignRight
                        }

                        Label {
                            Layout.fillWidth: true

                            text:
                                barcodeVM.queueCount.toString()

                            color: theme.gold

                            font.family: "Cairo"
                            font.pixelSize: 18
                            font.bold: true

                            horizontalAlignment:
                                Text.AlignRight
                        }
                    }


                    Rectangle {
                        width: 1
                        Layout.fillHeight: true
                        Layout.topMargin: 14
                        Layout.bottomMargin: 14
                        color: theme.border
                    }


                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Label {
                            Layout.fillWidth: true

                            text: "باركود موجود"

                            color: theme.textMuted

                            font.family: "Cairo"
                            font.pixelSize: 10

                            horizontalAlignment:
                                Text.AlignRight
                        }

                        Label {
                            Layout.fillWidth: true

                            text:
                                barcodeVM.existingBarcodeCount.toString()

                            color: theme.green

                            font.family: "Cairo"
                            font.pixelSize: 18
                            font.bold: true

                            horizontalAlignment:
                                Text.AlignRight
                        }
                    }


                    Rectangle {
                        width: 1
                        Layout.fillHeight: true
                        Layout.topMargin: 14
                        Layout.bottomMargin: 14
                        color: theme.border
                    }


                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Label {
                            Layout.fillWidth: true

                            text: "سيتم إنشاء باركود"

                            color: theme.textMuted

                            font.family: "Cairo"
                            font.pixelSize: 10

                            horizontalAlignment:
                                Text.AlignRight
                        }

                        Label {
                            Layout.fillWidth: true

                            text:
                                barcodeVM.missingBarcodeCount.toString()

                            color:
                                barcodeVM.missingBarcodeCount > 0
                                ? theme.amber
                                : theme.textMuted

                            font.family: "Cairo"
                            font.pixelSize: 18
                            font.bold: true

                            horizontalAlignment:
                                Text.AlignRight
                        }
                    }


                    Rectangle {
                        width: 1
                        Layout.fillHeight: true
                        Layout.topMargin: 14
                        Layout.bottomMargin: 14
                        color: theme.border
                    }


                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Label {
                            Layout.fillWidth: true

                            text: "اللاصقات الفعلية"

                            color: theme.textMuted

                            font.family: "Cairo"
                            font.pixelSize: 10

                            horizontalAlignment:
                                Text.AlignRight
                        }

                        Label {
                            Layout.fillWidth: true

                            text:
                                barcodeVM.physicalLabelCount.toString()

                            color: theme.blue

                            font.family: "Cairo"
                            font.pixelSize: 18
                            font.bold: true

                            horizontalAlignment:
                                Text.AlignRight
                        }
                    }
                }
            }


            // ------------------------------------------------
            // ODD ITEM NOTICE
            // ------------------------------------------------

            Rectangle {
                visible:
                    barcodeVM.hasOddItem

                Layout.fillWidth: true
                Layout.preferredHeight: 46

                Layout.leftMargin: 16
                Layout.rightMargin: 16
                Layout.topMargin: 8

                color: "#29241A"

                radius: theme.radiusSmall

                border.color:
                    theme.amber

                border.width: 1


                Label {
                    anchors.fill: parent
                    anchors.margins: 10

                    text:
                        "⚠ العدد فردي: آخر لاصقة ستحتوي قطعة واحدة، والجهة الثانية ستظل فارغة."

                    color:
                        theme.amber

                    font.family: "Cairo"

                    horizontalAlignment:
                        Text.AlignRight

                    verticalAlignment:
                        Text.AlignVCenter
                }
            }


            // ------------------------------------------------
            // PAIR PREVIEW
            // ------------------------------------------------

            ListView {
                id: barcodePairList

                Layout.fillWidth: true
                Layout.fillHeight: true

                Layout.leftMargin: 16
                Layout.rightMargin: 16
                Layout.topMargin: 10

                clip: true
                spacing: 8

                model:
                    barcodeVM.physicalLabelCount


                delegate: Rectangle {
                    required property int index

                    property int leftIndex:
                        index * 2

                    property int rightIndex:
                        leftIndex + 1

                    property var leftItem:
                        leftIndex < barcodeVM.queueItems.length
                        ? barcodeVM.queueItems[leftIndex]
                        : ({})

                    property var rightItem:
                        rightIndex < barcodeVM.queueItems.length
                        ? barcodeVM.queueItems[rightIndex]
                        : ({})

                    width:
                        barcodePairList.width

                    height: 112

                    color:
                        theme.bgTertiary

                    radius:
                        theme.radiusSmall

                    border.color:
                        theme.border

                    border.width: 1


                    RowLayout {
                        anchors.fill: parent

                        anchors.margins: 10

                        spacing: 10


                        // Left physical position
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.fillHeight: true

                            color:
                                theme.bgSecondary

                            radius: 6

                            border.color:
                                theme.border

                            border.width: 1


                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 10

                                spacing: 2


                                Label {
                                    Layout.fillWidth: true

                                    text:
                                        "قطعة #"
                                        + (
                                            leftItem.inventory_item_id
                                            || "—"
                                        )

                                    color: theme.gold

                                    font.family: "Cairo"
                                    font.bold: true

                                    horizontalAlignment:
                                        Text.AlignRight
                                }


                                Label {
                                    Layout.fillWidth: true

                                    text:
                                        (
                                            leftItem.item_type_name
                                            || "—"
                                        )
                                        + "  •  عيار "
                                        + (
                                            leftItem.karat_value
                                            || "—"
                                        )
                                        + "  •  "
                                        + Number(
                                            leftItem.weight_grams
                                            || 0
                                        ).toFixed(3)
                                        + " جم"

                                    color:
                                        theme.textSecondary

                                    font.family: "Cairo"
                                    font.pixelSize: 10

                                    horizontalAlignment:
                                        Text.AlignRight
                                }


                                Label {
                                    Layout.fillWidth: true

                                    text:
                                        leftItem.barcode_value
                                        ? "الباركود: "
                                          + leftItem.barcode_value
                                        : "سيتم إنشاء باركود جديد عند الطباعة"

                                    color:
                                        leftItem.barcode_value
                                        ? theme.green
                                        : theme.amber

                                    font.family: "Cairo"
                                    font.pixelSize: 10

                                    horizontalAlignment:
                                        Text.AlignRight
                                }
                            }
                        }


                        // Right physical position
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.fillHeight: true

                            color:
                                rightItem.inventory_item_id
                                ? theme.bgSecondary
                                : "#14181D"

                            radius: 6

                            border.color:
                                rightItem.inventory_item_id
                                ? theme.border
                                : theme.textDisabled

                            border.width: 1


                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 10

                                spacing: 2


                                Label {
                                    Layout.fillWidth: true

                                    text:
                                        rightItem.inventory_item_id
                                        ? "قطعة #"
                                          + rightItem.inventory_item_id
                                        : "فارغ"

                                    color:
                                        rightItem.inventory_item_id
                                        ? theme.gold
                                        : theme.textDisabled

                                    font.family: "Cairo"
                                    font.bold: true

                                    horizontalAlignment:
                                        Text.AlignRight
                                }


                                Label {
                                    visible:
                                        Boolean(
                                            rightItem.inventory_item_id
                                        )

                                    Layout.fillWidth: true

                                    text:
                                        (
                                            rightItem.item_type_name
                                            || "—"
                                        )
                                        + "  •  عيار "
                                        + (
                                            rightItem.karat_value
                                            || "—"
                                        )
                                        + "  •  "
                                        + Number(
                                            rightItem.weight_grams
                                            || 0
                                        ).toFixed(3)
                                        + " جم"

                                    color:
                                        theme.textSecondary

                                    font.family: "Cairo"
                                    font.pixelSize: 10

                                    horizontalAlignment:
                                        Text.AlignRight
                                }


                                Label {
                                    visible:
                                        Boolean(
                                            rightItem.inventory_item_id
                                        )

                                    Layout.fillWidth: true

                                    text:
                                        rightItem.barcode_value
                                        ? "الباركود: "
                                          + rightItem.barcode_value
                                        : "سيتم إنشاء باركود جديد عند الطباعة"

                                    color:
                                        rightItem.barcode_value
                                        ? theme.green
                                        : theme.amber

                                    font.family: "Cairo"
                                    font.pixelSize: 10

                                    horizontalAlignment:
                                        Text.AlignRight
                                }
                            }
                        }
                    }
                }
            }


            // ------------------------------------------------
            // RESULT / ERROR
            // ------------------------------------------------

            Rectangle {
                visible:
                    barcodeVM.actionMessage.length > 0

                Layout.fillWidth: true

                Layout.preferredHeight:
                    Math.max(
                        46,
                        barcodeActionLabel.implicitHeight + 20
                    )

                Layout.leftMargin: 16
                Layout.rightMargin: 16
                Layout.topMargin: 8

                radius:
                    theme.radiusSmall

                color:
                    barcodeVM.actionSuccess
                    ? "#16271F"
                    : theme.redBackground

                border.color:
                    barcodeVM.actionSuccess
                    ? theme.green
                    : theme.red

                border.width: 1


                Label {
                    id: barcodeActionLabel

                    anchors.fill: parent
                    anchors.margins: 10

                    text:
                        barcodeVM.actionMessage

                    color:
                        barcodeVM.actionSuccess
                        ? theme.green
                        : theme.red

                    font.family: "Cairo"

                    wrapMode:
                        Text.WordWrap

                    horizontalAlignment:
                        Text.AlignRight

                    verticalAlignment:
                        Text.AlignVCenter
                }
            }


            // ------------------------------------------------
            // FOOTER
            // ------------------------------------------------

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 70

                Layout.topMargin: 10

                color: theme.bgTertiary

                border.color:
                    theme.border

                border.width: 1


                RowLayout {
                    anchors.fill: parent

                    anchors.leftMargin: 18
                    anchors.rightMargin: 18

                    layoutDirection:
                        Qt.RightToLeft

                    spacing: 10


                    Label {
                        Layout.fillWidth: true

                        text:
                            "الطابعة: ZDesigner GK420t"

                        color:
                            theme.textMuted

                        font.family: "Cairo"
                        font.pixelSize: 10

                        horizontalAlignment:
                            Text.AlignRight
                    }


                    Button {
                        text:
                            barcodeVM.busy
                            ? "جاري الطباعة..."
                            : "🖨  طباعة الآن"

                        font.family: "Cairo"
                        font.bold: true

                        enabled:
                            barcodeVM.queueCount > 0
                            && !barcodeVM.busy

                        onClicked: {
                            var printed =
                                barcodeVM.printQueue()

                            // Refresh even on printer failure:
                            // new barcodes may already be committed
                            // and must become visible immediately.
                            inventoryVM.refresh()

                            if (printed) {
                                inventoryPage.clearSelection()
                                barcodePrintQueueDialog.close()
                            }
                        }
                    }


                    Button {
                        text: "إلغاء"

                        font.family: "Cairo"

                        enabled:
                            !barcodeVM.busy

                        onClicked:
                            barcodePrintQueueDialog.close()
                    }
                }
            }
        }
    }


    // ========================================================
    // BARCODE PRINT HISTORY
    // ========================================================

    Dialog {
        id: barcodePrintHistoryDialog

        property string selectedDate: ""

        modal: true
        dim: true
        padding: 0

        width:
            Math.min(
                root.width - 70,
                980
            )

        height:
            Math.min(
                root.height - 70,
                720
            )

        x:
            Math.round(
                (root.width - width) / 2
            )

        y:
            Math.round(
                (root.height - height) / 2
            )

        closePolicy:
            Popup.CloseOnEscape

        Overlay.modal: Rectangle {
            color: "#80000000"
        }


        function pad2(value) {
            return value < 10
                ? "0" + value
                : String(value)
        }


        function todayText() {
            var now = new Date()

            return (
                now.getFullYear()
                + "-"
                + pad2(now.getMonth() + 1)
                + "-"
                + pad2(now.getDate())
            )
        }


        function statusText(value) {
            if (value === "PRINTED") {
                return "تمت الطباعة"
            }

            if (value === "FAILED") {
                return "فشلت الطباعة"
            }

            if (value === "STARTED") {
                return "بدأت ولم تكتمل"
            }

            return value || "—"
        }


        function statusColor(value) {
            if (value === "PRINTED") {
                return theme.green
            }

            if (value === "FAILED") {
                return theme.red
            }

            return theme.amber
        }


        function openForToday() {
            selectedDate = todayText()

            barcodeHistoryDateField.text =
                selectedDate

            barcodeVM.loadPrintHistory(
                selectedDate
            )

            open()
        }


        background: Rectangle {
            color: theme.bgSecondary

            radius: 10

            border.color:
                theme.border

            border.width: 1
        }


        contentItem: ColumnLayout {
            spacing: 0


            // ------------------------------------------------
            // HEADER
            // ------------------------------------------------

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 78

                color: theme.bgTertiary

                radius: 10

                border.color:
                    theme.border

                border.width: 1


                RowLayout {
                    anchors.fill: parent

                    anchors.leftMargin: 18
                    anchors.rightMargin: 18

                    layoutDirection:
                        Qt.RightToLeft

                    spacing: 10


                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2


                        Label {
                            Layout.fillWidth: true

                            text: "🧾 سجل طباعة الباركود"

                            color: theme.gold

                            font.family: "Cairo"
                            font.pixelSize: 18
                            font.bold: true

                            horizontalAlignment:
                                Text.AlignRight
                        }


                        Label {
                            Layout.fillWidth: true

                            text:
                                "كل عملية طباعة محفوظة كدفعة مستقلة ويمكن الرجوع إليها لاحقاً"

                            color: theme.textMuted

                            font.family: "Cairo"
                            font.pixelSize: 11

                            horizontalAlignment:
                                Text.AlignRight
                        }
                    }


                    Button {
                        text: "✕"

                        flat: true

                        onClicked:
                            barcodePrintHistoryDialog.close()
                    }
                }
            }


            // ------------------------------------------------
            // DATE FILTER
            // ------------------------------------------------

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 62

                Layout.leftMargin: 16
                Layout.rightMargin: 16
                Layout.topMargin: 12

                color: theme.bgTertiary

                radius: theme.radiusSmall

                border.color: theme.border
                border.width: 1


                RowLayout {
                    anchors.fill: parent

                    anchors.leftMargin: 12
                    anchors.rightMargin: 12

                    layoutDirection:
                        Qt.RightToLeft

                    spacing: 8


                    Label {
                        text: "تاريخ الطباعة"

                        color: theme.textSecondary

                        font.family: "Cairo"

                        horizontalAlignment:
                            Text.AlignRight
                    }


                    TextField {
                        id: barcodeHistoryDateField

                        Layout.preferredWidth: 150
                        Layout.preferredHeight: 38

                        placeholderText: "YYYY-MM-DD"

                        font.family: "Cairo"

                        horizontalAlignment:
                            TextInput.AlignRight

                        onEditingFinished:
                            barcodePrintHistoryDialog.selectedDate =
                                text.trim()
                    }


                    Button {
                        text: "اليوم"

                        font.family: "Cairo"

                        onClicked: {
                            barcodePrintHistoryDialog.selectedDate =
                                barcodePrintHistoryDialog.todayText()

                            barcodeHistoryDateField.text =
                                barcodePrintHistoryDialog.selectedDate

                            barcodeVM.loadPrintHistory(
                                barcodePrintHistoryDialog.selectedDate
                            )
                        }
                    }


                    Button {
                        text: "كل التواريخ"

                        font.family: "Cairo"

                        onClicked: {
                            barcodePrintHistoryDialog.selectedDate = ""
                            barcodeHistoryDateField.text = ""

                            barcodeVM.loadPrintHistory("")
                        }
                    }


                    Button {
                        text: "تحديث"

                        font.family: "Cairo"

                        enabled:
                            !barcodeVM.busy

                        onClicked: {
                            barcodePrintHistoryDialog.selectedDate =
                                barcodeHistoryDateField.text.trim()

                            barcodeVM.loadPrintHistory(
                                barcodePrintHistoryDialog.selectedDate
                            )
                        }
                    }


                    Item {
                        Layout.fillWidth: true
                    }


                    Label {
                        text:
                            barcodeVM.printHistoryCount
                            + " دفعة"

                        color: theme.textMuted

                        font.family: "Cairo"

                        horizontalAlignment:
                            Text.AlignRight
                    }
                }
            }


            // ------------------------------------------------
            // HISTORY LIST
            // ------------------------------------------------

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true

                Layout.leftMargin: 16
                Layout.rightMargin: 16
                Layout.topMargin: 10

                color: theme.bgTertiary

                radius: theme.radiusSmall

                border.color: theme.border
                border.width: 1


                Label {
                    visible:
                        barcodeVM.printHistoryCount === 0
                        && !barcodeVM.busy

                    anchors.centerIn: parent

                    text:
                        "لا توجد دفعات طباعة في التاريخ المحدد"

                    color: theme.textMuted

                    font.family: "Cairo"
                }


                ListView {
                    id: barcodeHistoryList

                    anchors.fill: parent
                    anchors.margins: 10

                    clip: true
                    spacing: 7

                    model:
                        barcodeVM.printHistory


                    delegate: Rectangle {
                        required property var modelData

                        width:
                            barcodeHistoryList.width

                        height: 92

                        color:
                            theme.bgSecondary

                        radius: 7

                        border.color:
                            theme.border

                        border.width: 1


                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 12

                            layoutDirection:
                                Qt.RightToLeft

                            spacing: 12


                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2


                                Label {
                                    Layout.fillWidth: true

                                    text:
                                        modelData.batch_code
                                        || "—"

                                    color: theme.gold

                                    font.family: "Cairo"
                                    font.bold: true

                                    horizontalAlignment:
                                        Text.AlignRight
                                }


                                Label {
                                    Layout.fillWidth: true

                                    text:
                                        "التاريخ: "
                                        + (
                                            modelData.requested_at
                                            || "—"
                                        )
                                        + "   •   القطع: "
                                        + (
                                            modelData.item_count
                                            || 0
                                        )
                                        + "   •   اللاصقات: "
                                        + (
                                            modelData.physical_label_count
                                            || 0
                                        )

                                    color: theme.textSecondary

                                    font.family: "Cairo"
                                    font.pixelSize: 10

                                    horizontalAlignment:
                                        Text.AlignRight
                                }


                                Label {
                                    visible:
                                        Boolean(
                                            modelData.reprint_of_batch_code
                                        )

                                    Layout.fillWidth: true

                                    text:
                                        "إعادة طباعة من: "
                                        + modelData.reprint_of_batch_code

                                    color: theme.textMuted

                                    font.family: "Cairo"
                                    font.pixelSize: 9

                                    horizontalAlignment:
                                        Text.AlignRight
                                }
                            }


                            Rectangle {
                                Layout.preferredWidth: 130
                                Layout.preferredHeight: 34

                                radius: 6

                                color: "#14181D"

                                border.color:
                                    barcodePrintHistoryDialog.statusColor(
                                        modelData.status
                                    )

                                border.width: 1


                                Label {
                                    anchors.centerIn: parent

                                    text:
                                        barcodePrintHistoryDialog.statusText(
                                            modelData.status
                                        )

                                    color:
                                        barcodePrintHistoryDialog.statusColor(
                                            modelData.status
                                        )

                                    font.family: "Cairo"
                                    font.pixelSize: 10
                                    font.bold: true
                                }
                            }


                            Button {
                                text: "🖨 إعادة الطباعة"

                                font.family: "Cairo"

                                enabled:
                                    (
                                        modelData.status === "PRINTED"
                                        || modelData.status === "FAILED"
                                    )
                                    && !barcodeVM.busy

                                onClicked: {
                                    barcodeVM.reprintJob(
                                        Number(
                                            modelData.id
                                        )
                                    )

                                    inventoryVM.refresh()
                                }
                            }
                        }
                    }
                }
            }


            // ------------------------------------------------
            // ACTION MESSAGE
            // ------------------------------------------------

            Rectangle {
                visible:
                    barcodeVM.actionMessage.length > 0

                Layout.fillWidth: true

                Layout.preferredHeight:
                    Math.max(
                        44,
                        barcodeHistoryActionLabel.implicitHeight + 18
                    )

                Layout.leftMargin: 16
                Layout.rightMargin: 16
                Layout.topMargin: 8

                radius: theme.radiusSmall

                color:
                    barcodeVM.actionSuccess
                    ? "#16271F"
                    : theme.redBackground

                border.color:
                    barcodeVM.actionSuccess
                    ? theme.green
                    : theme.red

                border.width: 1


                Label {
                    id: barcodeHistoryActionLabel

                    anchors.fill: parent
                    anchors.margins: 9

                    text:
                        barcodeVM.actionMessage

                    color:
                        barcodeVM.actionSuccess
                        ? theme.green
                        : theme.red

                    font.family: "Cairo"

                    wrapMode:
                        Text.WordWrap

                    horizontalAlignment:
                        Text.AlignRight

                    verticalAlignment:
                        Text.AlignVCenter
                }
            }


            // ------------------------------------------------
            // FOOTER
            // ------------------------------------------------

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 62

                Layout.topMargin: 10

                color: theme.bgTertiary

                border.color: theme.border
                border.width: 1


                RowLayout {
                    anchors.fill: parent

                    anchors.leftMargin: 18
                    anchors.rightMargin: 18

                    layoutDirection:
                        Qt.RightToLeft

                    spacing: 10


                    Label {
                        Layout.fillWidth: true

                        text:
                            "السجل لا يغيّر الباركودات القديمة؛ إعادة الطباعة تستخدم نفس الباركود الفعّال."

                        color: theme.textDisabled

                        font.family: "Cairo"
                        font.pixelSize: 10

                        horizontalAlignment:
                            Text.AlignRight
                    }


                    Button {
                        text: "إغلاق"

                        font.family: "Cairo"

                        onClicked:
                            barcodePrintHistoryDialog.close()
                    }
                }
            }
        }
    }

}
