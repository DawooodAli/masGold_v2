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
    onClosing: Qt.quit()

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
    function openCustomerDetails(customerId) {
        if (!customerVM.openCustomerDetails(customerId)) {
            customerNavigationDialog.message = customerVM.actionMessage || "تعذر فتح بيانات العميل."
            customerNavigationDialog.open()
            return false
        }
        customerAccountVM.close()
        customersWorkspace.showProfile = true
        customersWorkspace.skipEntryRefresh = currentPage !== 3
        currentPage = 3
        Qt.callLater(function() { customersWorkspace.revealDetails() })
        return true
    }
    Dialog {
        id: customerNavigationDialog
        objectName: "customerNavigationDialog"
        property string message: ""
        anchors.centerIn: parent
        width: Math.min(root.width - 24, 420)
        modal: true
        title: "بيانات العميل"
        font.family: "Cairo"
        background: Rectangle { color: theme.bgPrimary; radius: 8; border.color: theme.goldDark }
        contentItem: ColumnLayout {
            CustomerLabel { objectName: "customerNavigationMessage"; Layout.fillWidth: true
                value: customerNavigationDialog.message; color: theme.red }
            CustomerButton { Layout.alignment: Qt.AlignRight; text: "إغلاق"
                onClicked: customerNavigationDialog.close() }
        }
    }
    // Master Data messages belong to one page: leaving it clears them (no timer polling).
    onCurrentPageChanged: masterDataVM.clearAction()

    // UI rule: Arabic text uses Cairo and is right-aligned/RTL in controls.
    // Do not enable global LayoutMirroring because the physical sidebar must stay on the right.

    // Selected inventory record for the read-only details dialog.
    property var selectedInventoryItem: ({})


    Theme {
        id: theme
    }

    component GoldPriceLabel: Label {
        property string value: ""
        text: "\u200f" + value
        font.family: "Cairo"
        font.pixelSize: 12
        color: theme.textPrimary
        horizontalAlignment: Text.AlignRight
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.WordWrap
    }

    component GoldPriceButton: Button {
        id: priceButton
        property bool primaryAction: false
        implicitHeight: 36
        leftPadding: 12
        rightPadding: 12
        font.family: "Cairo"
        contentItem: GoldPriceLabel {
            value: priceButton.text
            color: !priceButton.enabled ? theme.textMuted : priceButton.primaryAction ? theme.bgPrimary : theme.textPrimary
        }
        background: Rectangle {
            color: priceButton.primaryAction && priceButton.enabled ? (priceButton.down ? theme.goldDark : theme.gold) : theme.bgTertiary
            border.color: priceButton.primaryAction ? theme.goldDark : theme.border
            radius: theme.radiusSmall
        }
    }

    component GoldPriceInput: TextField {
        font.family: "Cairo"
        horizontalAlignment: Text.AlignRight
        verticalAlignment: Text.AlignVCenter
        topPadding: 6; bottomPadding: 6; leftPadding: 10; rightPadding: 10
        topInset: 0; bottomInset: 0
        selectByMouse: true
        placeholderText: text.length ? "" : "\u200f0.00"
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        color: theme.textPrimary
        background: Rectangle {
            color: theme.bgTertiary
            border.color: parent.activeFocus ? theme.gold : theme.border
            radius: theme.radiusSmall
        }
    }

    component GoldPriceGrid: GridLayout {
        id: priceGrid
        property var priceRows: []
        readonly property string derivedText: {
            for (var i = 0; i < priceRows.length; ++i)
                if (priceRows[i].derived === true) return "عيار " + priceRows[i].karat + ": " + priceRows[i].derived_note
            return ""
        }
        columns: 3
        columnSpacing: 18
        rowSpacing: 5
        layoutDirection: Qt.RightToLeft
        GoldPriceLabel { Layout.preferredWidth: 72; value: "العيار"; color: theme.gold }
        GoldPriceLabel { Layout.fillWidth: true; value: "شراء المحل"; color: theme.priceBuy }
        GoldPriceLabel { Layout.fillWidth: true; value: "بيع المحل"; color: theme.priceSell }
        // 22K is never quoted by the provider: its prices are derived from the approved 24K quote and are labelled as such.
        GoldPriceLabel {
            objectName: priceGrid.objectName + "-derivedNote"
            Layout.columnSpan: 3
            Layout.fillWidth: true
            visible: priceGrid.derivedText !== ""
            value: priceGrid.derivedText
            color: theme.textMuted
            font.pixelSize: 12
            wrapMode: Text.WordWrap
        }
        Repeater {
            model: priceGrid.priceRows.length * 3
            delegate: GoldPriceLabel {
                required property int index
                readonly property int column: index % 3
                readonly property var row: priceGrid.priceRows[Math.floor(index / 3)]
                objectName: priceGrid.objectName + "-" + column + "-" + row.karat
                Layout.fillWidth: column !== 0
                Layout.preferredWidth: column === 0 ? 72 : 145
                value: column === 0 ? "عيار " + row.karat
                       : "\u2066" + (column === 1 ? row.shop_buy_price_per_gram : row.shop_sell_price_per_gram) + "\u2069"
                wrapMode: Text.NoWrap
                font.bold: column === 0
                color: column === 1 ? theme.priceBuy : column === 2 ? theme.priceSell : theme.textSecondary
            }
        }
    }

    Dialog {
        id: goldPriceDialog
        objectName: "goldPriceDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(root.width - 40, 620)
        height: Math.min(root.height - 40, goldBody.implicitHeight + header.implicitHeight
                         + footer.implicitHeight + topPadding + bottomPadding + 12)
        modal: true
        padding: 16
        font.family: "Cairo"
        onOpened: {
            goldBuy21.text = ""
            goldSell21.text = ""
            goldManualReason.text = ""
            goldPriceVM.invalidatePreview()
        }
        onClosed: goldManualConfirmation.close()
        header: GoldPriceLabel {
            value: "أسعار الذهب"
            color: theme.gold
            font.bold: true
            font.pixelSize: 20
            leftPadding: 24; rightPadding: 24; topPadding: 12; bottomPadding: 4
        }
        background: Rectangle {
            color: theme.bgSecondary
            border.color: theme.goldDark
            radius: theme.radiusSmall
        }
        contentItem: ScrollView {
            id: goldPriceScroll
            objectName: "goldPriceScroll"
            clip: true
            contentWidth: availableWidth
            contentHeight: goldBody.implicitHeight
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                id: goldBody
                objectName: "goldPriceBody"
                width: Math.min(goldPriceScroll.availableWidth, 548)
                x: (goldPriceScroll.availableWidth - width) / 2
                spacing: 10
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: goldStatus.implicitHeight + 20
                    color: theme.bgTertiary
                    radius: theme.radiusSmall
                    border.color: theme.border
                    ColumnLayout {
                        id: goldStatus
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 5
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 16
                            GoldPriceLabel {
                                value: "الحالة: " + goldPriceVM.statusText
                                color: goldPriceVM.status === "ERROR" ? theme.priceError : goldPriceVM.status === "STALE_ALLOWED" ? theme.priceWarning : theme.textSecondary
                            }
                            GoldPriceLabel { value: "الوضع: " + goldPriceVM.modeText }
                            GoldPriceLabel { value: "المصدر: " + goldPriceVM.source }
                            Item { Layout.fillWidth: true }
                        }
                        GoldPriceLabel {
                            Layout.fillWidth: true
                            visible: goldPriceVM.prices.length > 0
                            value: "آخر تحديث: \u2066" + goldPriceVM.activatedAt + "\u2069  |  عمر السعر: " + goldPriceVM.ageText
                            color: theme.textSecondary
                            font.pixelSize: 11
                            ToolTip.visible: goldStatusHover.hovered
                            ToolTip.text: "وقت المصدر: " + goldPriceVM.providerTimestamp + "\nوقت الجلب: " + goldPriceVM.fetchedAt
                            HoverHandler { id: goldStatusHover }
                        }
                    }
                }
                GoldPriceLabel {
                    objectName: "goldPriceEmptyState"
                    Layout.fillWidth: true
                    visible: goldPriceVM.prices.length === 0
                    value: "لا توجد أسعار ذهب معتمدة حالياً"
                    color: theme.textMuted
                    topPadding: 4; bottomPadding: 4
                }
                GoldPriceGrid {
                    objectName: "goldCurrentPrices"
                    Layout.fillWidth: true
                    visible: goldPriceVM.prices.length > 0
                    priceRows: goldPriceVM.prices
                }
                GoldPriceLabel {
                    Layout.fillWidth: true
                    value: "جنيه مصري / جرام · الشراء والبيع من منظور المحل"
                    color: theme.textMuted
                    font.pixelSize: 11
                }
                Repeater {
                    model: goldPriceVM.warnings
                    delegate: GoldPriceLabel {
                        required property string modelData
                        Layout.fillWidth: true
                        value: modelData
                        color: theme.gold
                        font.pixelSize: 11
                    }
                }
                ColumnLayout {
                    objectName: "goldPriceManagement"
                    Layout.fillWidth: true
                    visible: goldPriceVM.canManage
                    enabled: !goldPriceVM.busy
                    spacing: 8
                    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: theme.border }
                    GoldPriceLabel {
                        Layout.fillWidth: true
                        value: "السعر اليدوي — عيار 21"
                        font.bold: true
                        color: theme.gold
                    }
                    GridLayout {
                        Layout.alignment: Qt.AlignRight
                        Layout.fillWidth: true
                        Layout.maximumWidth: 430
                        columns: 2
                        layoutDirection: Qt.RightToLeft
                        columnSpacing: 12
                        rowSpacing: 6
                        GoldPriceLabel { Layout.preferredWidth: 158; value: "شراء المحل — عيار 21"; color: theme.priceBuy }
                        GoldPriceInput {
                            id: goldBuy21
                            objectName: "goldBuy21"
                            Layout.fillWidth: true
                            Layout.maximumWidth: 260
                            Layout.preferredHeight: 34
                            maximumLength: 32
                            onTextChanged: goldPriceVM.invalidatePreview()
                        }
                        GoldPriceLabel { Layout.preferredWidth: 158; value: "بيع المحل — عيار 21"; color: theme.priceSell }
                        GoldPriceInput {
                            id: goldSell21
                            objectName: "goldSell21"
                            Layout.fillWidth: true
                            Layout.maximumWidth: 260
                            Layout.preferredHeight: 34
                            maximumLength: 32
                            onTextChanged: goldPriceVM.invalidatePreview()
                        }
                        GoldPriceLabel { Layout.preferredWidth: 158; value: "سبب التغيير" }
                        ScrollView {
                            Layout.fillWidth: true
                            Layout.maximumWidth: 260
                            Layout.preferredHeight: 54
                            clip: true
                            TextArea {
                                id: goldManualReason
                                objectName: "goldManualReason"
                                font.family: "Cairo"
                                font.pixelSize: 12
                                horizontalAlignment: Text.AlignRight
                                topPadding: 8; bottomPadding: 8; leftPadding: 10; rightPadding: 10
                                topInset: 0; bottomInset: 0
                                wrapMode: TextEdit.Wrap
                                selectByMouse: true
                                placeholderText: text.length ? "" : "\u200fسبب التغيير (مطلوب)"
                                onTextChanged: goldPriceVM.invalidatePreview()
                                background: Rectangle {
                                    color: theme.bgTertiary
                                    border.color: goldManualReason.activeFocus ? theme.gold : theme.border
                                    radius: theme.radiusSmall
                                }
                            }
                        }
                    }
                    GoldPriceButton {
                        objectName: "goldPreviewButton"
                        Layout.alignment: Qt.AlignRight
                        text: "عرض الأسعار المشتقة"
                        onClicked: goldPriceVM.previewManual(goldBuy21.text, goldSell21.text, goldManualReason.text)
                    }
                    Rectangle {
                        objectName: "goldManualPreviewCard"
                        Layout.fillWidth: true
                        visible: goldPriceVM.previewReady
                        implicitHeight: goldPreviewContent.implicitHeight + 20
                        color: theme.bgTertiary
                        border.color: theme.goldDark
                        radius: theme.radiusSmall
                        ColumnLayout {
                            id: goldPreviewContent
                            anchors.fill: parent
                            anchors.margins: 10
                            spacing: 6
                            GoldPriceLabel {
                                Layout.fillWidth: true
                                value: "الأسعار المشتقة — راجعها ثم اعتمد السعر اليدوي"
                                color: theme.gold
                                font.pixelSize: 11
                            }
                            GoldPriceGrid { objectName: "goldPreviewPrices"; Layout.fillWidth: true; priceRows: goldPriceVM.previewPrices }
                        }
                    }
                }
            }
        }
        // Save and feedback stay outside the scroll area at every supported size.
        footer: Item {
            implicitHeight: goldFooter.implicitHeight + 20
            ColumnLayout {
                id: goldFooter
                width: Math.min(parent.width - 32, 548)
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: parent.top
                spacing: 6
                GoldPriceLabel {
                    objectName: "goldPriceError"
                    Layout.fillWidth: true
                    visible: goldPriceVM.errorMessage.length > 0
                    value: goldPriceVM.errorMessage
                    color: theme.red
                }
                GoldPriceLabel {
                    objectName: "goldPriceSuccess"
                    Layout.fillWidth: true
                    visible: goldPriceVM.successMessage.length > 0
                    value: goldPriceVM.successMessage
                    color: theme.green
                }
                GoldPriceLabel {
                    Layout.fillWidth: true
                    visible: goldPriceVM.busy
                    value: "جارٍ تحديث الأسعار…"
                    color: theme.textMuted
                }
                RowLayout {
                    Layout.alignment: Qt.AlignRight
                    layoutDirection: Qt.LeftToRight
                    spacing: 6
                    GoldPriceButton { text: "إغلاق"; onClicked: goldPriceDialog.close() }
                    GoldPriceButton {
                        text: "تحديث"
                        enabled: !goldPriceVM.busy
                        onClicked: goldPriceVM.refresh()
                    }
                    GoldPriceButton {
                        objectName: "goldReturnAutoButton"
                        visible: goldPriceVM.canManage && goldPriceVM.mode === "MANUAL"
                        enabled: !goldPriceVM.busy
                        text: "العودة للتلقائي"
                        onClicked: goldAutoConfirmation.open()
                    }
                    GoldPriceButton {
                        objectName: "goldConfirmManualButton"
                        visible: goldPriceVM.canManage && goldPriceVM.previewReady
                        enabled: !goldPriceVM.busy
                        primaryAction: true
                        text: "اعتماد السعر اليدوي"
                        onClicked: goldManualConfirmation.open()
                    }
                }
            }
        }
    }

    Dialog {
        id: goldManualConfirmation
        objectName: "goldManualConfirmation"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(root.width - 64, 500)
        modal: true
        padding: 16
        font.family: "Cairo"
        background: Rectangle { color: theme.bgSecondary; border.color: theme.goldDark; radius: theme.radiusSmall }
        header: GoldPriceLabel { value: "تأكيد اعتماد السعر اليدوي"; padding: 16; color: theme.gold; font.bold: true }
        contentItem: ColumnLayout {
            spacing: 10
            GoldPriceGrid { Layout.fillWidth: true; priceRows: goldPriceVM.previewPrices }
            GoldPriceLabel {
                Layout.fillWidth: true
                value: "سيتم حفظ أسعار العيارات الثلاثة والتحويل إلى الوضع اليدوي."
            }
            GoldPriceLabel {
                Layout.fillWidth: true
                value: "السبب: " + goldManualReason.text
                maximumLineCount: 3
                elide: Text.ElideRight
                color: theme.textSecondary
            }
        }
        footer: RowLayout {
            layoutDirection: Qt.RightToLeft
            spacing: 8
            GoldPriceButton {
                objectName: "goldApplyManualButton"
                text: "تأكيد الاعتماد"
                primaryAction: true
                enabled: goldPriceVM.canManage && goldPriceVM.previewReady && !goldPriceVM.busy
                onClicked: {
                    if (goldPriceVM.confirmManual())
                        goldManualConfirmation.close()
                }
            }
            GoldPriceButton {
                objectName: "goldCancelManualButton"
                text: "إلغاء"
                onClicked: goldManualConfirmation.close()
            }
            Item { Layout.fillWidth: true }
        }
        Connections {
            target: goldPriceVM
            function onChanged() {
                if (!goldPriceVM.previewReady || !goldPriceVM.canManage)
                    goldManualConfirmation.close()
            }
        }
    }

    Dialog {
        id: goldAutoConfirmation
        objectName: "goldAutoConfirmation"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(root.width - 64, 500)
        modal: true
        padding: 16
        font.family: "Cairo"
        background: Rectangle { color: theme.bgSecondary; border.color: theme.goldDark; radius: theme.radiusSmall }
        header: GoldPriceLabel { value: "تأكيد العودة إلى التلقائي"; padding: 16; color: theme.gold; font.bold: true }
        contentItem: GoldPriceLabel {
            value: "سيُسمح للمصدر التلقائي باعتماد أسعار جديدة. تظل آخر أسعار معتمدة محفوظة إذا تعذر جلب مجموعة صحيحة."
        }
        footer: RowLayout {
            layoutDirection: Qt.RightToLeft
            spacing: 8
            GoldPriceButton {
                objectName: "goldConfirmAutoButton"
                text: "تأكيد العودة للتلقائي"
                enabled: goldPriceVM.canManage && !goldPriceVM.busy
                onClicked: {
                    goldPriceVM.returnToAuto("")
                    goldAutoConfirmation.close()
                }
            }
            GoldPriceButton { text: "إلغاء"; onClicked: goldAutoConfirmation.close() }
            Item { Layout.fillWidth: true }
        }
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
                objectName: "topBar"
                Layout.fillWidth: true
                Layout.preferredHeight: 68

                color: theme.bgSecondary

                border.color: theme.border
                border.width: 1


                RowLayout {
                    anchors.fill: parent

                    anchors.leftMargin: root.width < 1200 ? 10 : 16
                    anchors.rightMargin: root.width < 1200 ? 10 : 16

                    spacing: root.width < 1400 ? 6 : 10


                    // Physical left: the user and logout. The header is compact below 1400 px (no page
                    // title, tighter spacing / fonts, 300 px price card); below 1200 px "متصل" is the dot alone.
                    Label {
                        objectName: "topUserName"
                        text: "\u200f" + appSession.displayName
                        font.family: "Cairo"
                        color: theme.textSecondary
                        horizontalAlignment: Text.AlignLeft
                        elide: Text.ElideLeft
                        Layout.maximumWidth: 110
                        Layout.minimumWidth: 36
                    }
                    Button {
                        id: logoutButton
                        objectName: "logoutButton"
                        font.family: "Cairo"
                        font.pixelSize: root.width < 1400 ? 12 : 13
                        leftPadding: root.width < 1200 ? 6 : root.width < 1400 ? 8 : 12
                        rightPadding: root.width < 1200 ? 6 : root.width < 1400 ? 8 : 12
                        // Compact: the short word keeps the one-line balances and the price card whole.
                        text: root.width < 1400 ? "خروج" : "تسجيل الخروج"
                        contentItem: Label {
                            text: "\u200f" + logoutButton.text
                            font: logoutButton.font
                            color: theme.textPrimary
                            horizontalAlignment: Text.AlignRight
                            verticalAlignment: Text.AlignVCenter
                        }
                        onClicked: authenticationVM.logout()
                    }

                    Item {
                        Layout.fillWidth: true
                    }

                    // Centre: operational balances only — shop cash and COUNTED old gold 21 / 18 / 24, each in
                    // its own compact box (the karat is the box's headline). Wallet / InstaPay / bank balances
                    // are in مراجعات المدير. Below 1400 / 1200 px the boxes tighten; no value is ever hidden.
                    RowLayout {
                        id: balanceStrip
                        objectName: "balanceStrip"
                        layoutDirection: Qt.RightToLeft
                        spacing: root.width < 1200 ? 4 : 6
                        readonly property int labelSize: root.width < 1200 ? 10 : 11
                        readonly property int karatSize: root.width < 1200 ? 15 : root.width < 1400 ? 16 : 18
                        readonly property int valueSize: root.width < 1200 ? 12 : root.width < 1400 ? 13 : 14
                        readonly property bool failed: balancesVM.errorMessage.length > 0
                        ToolTip.visible: failed && stripHover.hovered
                        ToolTip.text: "تعذر تحميل الأرصدة"
                        HoverHandler { id: stripHover }

                        BalanceBox {
                            objectName: "balanceBoxCash"
                            accent: theme.balanceCash
                            caption: "النقدية"
                            valueName: "balanceCash"
                            value: balancesVM.cashText
                            zero: balancesVM.cashZero
                        }
                        Repeater {
                            model: balancesVM.oldGold
                            BalanceBox {
                                required property var modelData
                                objectName: "balanceBox_" + modelData.karat
                                // 21 turquoise • 18 blue • 22 amber • 24 violet; a zero balance is muted, never hidden.
                                accent: theme.balanceKaratColor(modelData.karat)
                                caption: "كسر"
                                karat: modelData.karat
                                valueName: "balanceKarat_" + modelData.karat
                                value: modelData.text
                                zero: modelData.zero
                            }
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                    }

                    Label {
                        // The sidebar already marks the page; below 1400 px the space goes to the balances.
                        visible: root.width >= 1400 && text.length > 0
                        text:
                            pageStack.currentItem
                            ? pageStack.currentItem.pageTitle
                            : ""

                        color: theme.gold

                        font.family: "Cairo"
                        font.pixelSize: 20
                        font.bold: true

                        Layout.maximumWidth: 140
                        Layout.minimumWidth: 0
                        elide: Text.ElideLeft

                        horizontalAlignment:
                            Text.AlignRight
                    }

                    Rectangle {
                        objectName: "goldPriceArea"
                        Layout.preferredWidth: root.width < 1200 ? 250 : root.width < 1400 ? 290 : 340
                        Layout.minimumWidth: Layout.preferredWidth
                        Layout.preferredHeight: 54

                        radius: theme.radiusSmall

                        color: theme.bgTertiary

                        border.color: theme.border
                        border.width: 1


                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 6
                            spacing: 0
                            RowLayout {
                                Layout.fillWidth: true
                                objectName: "goldPriceHeadline"
                                property string text: goldPriceVM.headline
                                layoutDirection: Qt.RightToLeft
                                spacing: 10
                                // Label small, price strong: عيار 21 • شراء 6,209 • بيع 6,234 (no provider name).
                                GoldPriceLabel { value: "عيار 21"; color: theme.gold; font.bold: true
                                                 font.pixelSize: root.width < 1200 ? 12 : 13; wrapMode: Text.NoWrap }
                                GoldPriceLabel { value: "شراء"; color: theme.priceBuy; font.pixelSize: 11; wrapMode: Text.NoWrap }
                                GoldPriceLabel {
                                    objectName: "goldTopBuy"
                                    value: goldPriceVM.headlineBuy; color: theme.priceBuy
                                    font.bold: true; font.pixelSize: root.width < 1200 ? 15 : 17; wrapMode: Text.NoWrap
                                }
                                GoldPriceLabel { value: "بيع"; color: theme.priceSell; font.pixelSize: 11; wrapMode: Text.NoWrap }
                                GoldPriceLabel {
                                    objectName: "goldTopSell"
                                    value: goldPriceVM.headlineSell; color: theme.priceSell
                                    font.bold: true; font.pixelSize: root.width < 1200 ? 15 : 17; wrapMode: Text.NoWrap
                                }
                                Item { Layout.fillWidth: true }
                            }
                            GoldPriceLabel {
                                objectName: "goldTopStatus"
                                Layout.fillWidth: true
                                value: goldPriceVM.simpleStatus
                                color: goldPriceVM.status === "ERROR" ? theme.priceError
                                       : goldPriceVM.status === "STALE_ALLOWED" ? theme.priceWarning : theme.textMuted
                                font.pixelSize: 11
                                elide: Text.ElideLeft
                                wrapMode: Text.NoWrap
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: goldPriceDialog.open()
                        }
                    }


                    Rectangle {
                        objectName: "connectionDot"
                        width: 9
                        height: 9

                        radius: 5

                        color:
                            masterDataVM.errorMessage.length > 0
                            ? theme.red
                            : theme.green
                    }


                    Label {
                        objectName: "connectionStatus"
                        // Below 1200 px the green dot alone says "متصل"; an error is always spelled out.
                        visible: root.width >= 1200 || masterDataVM.errorMessage.length > 0
                        Layout.minimumWidth: implicitWidth
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

    // FROZEN Billing V1, DEAD CODE: never instantiated, and its `billingVM` is no longer registered (see
    // main.py) — instantiating this component would fail on the missing context property by design.
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
            return Number(value || 0).toFixed(3)
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
                                text: "سعر بيع 21 / جرام *"
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
                                text: "سعر شراء 21 / جرام *"
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
                                                "ذهب/جرام: "
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
                                            text: "مصنعية / جرام"
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
                                                "شراء/جرام: "
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

                    if (selected) {
                        var source = inventoryVM.items

                        for (var i = 0; i < source.length; i++) {
                            if (
                                itemId(source[i].inventory_item_id) === idValue
                                && !hasBarcode(source[i])
                            ) {
                                return
                            }
                        }
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


                // A piece without a stored barcode (بدون باركود) is never printable: it has no checkbox,
                // Select All skips it and it can never enter Review & Print (the service refuses it too).
                function hasBarcode(item) {
                    return Boolean(item && item.barcode_value)
                }


                function statusText(value) {
                    if (value === "IN_STOCK")
                        return "في المخزون"
                    if (value === "RETURNED_PENDING")
                        return "بانتظار الفحص"
                    return value || "—"
                }


                function selectAllVisible() {
                    var ids = []
                    var items = filteredItems()

                    for (
                        var index = 0;
                        index < items.length;
                        index++
                    ) {
                        if (!hasBarcode(items[index]))
                            continue

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

                    return number.toFixed(3)
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
                            objectName: "newInventoryItemButton"
                            text: "＋  إدخال قطعة جديدة"

                            // Convenience only: the services re-check INVENTORY_MANAGE / OLD_GOLD_MANAGE.
                            visible: inventoryVM.canManage || oldGoldConversionVM.canManage

                            font.family: "Cairo"
                            font.bold: true

                            onClicked:
                                newInventoryItemDialog.openForNewItem()
                        }


                        Button {
                            objectName: "openStockBalanceButton"
                            text: "📊  بيان المخزون"
                            font.family: "Cairo"
                            onClicked: root.currentPage = 11
                        }


                        Button {
                            objectName: "openStocktakeButton"
                            text: "🔎  الجرد بالباركود"
                            font.family: "Cairo"
                            onClicked: root.currentPage = 12
                        }


                        Button {
                            text: "↻  تحديث"

                            font.family: "Cairo"

                            onClicked:
                                inventoryVM.refresh()
                        }
                    }

                    // إعدادات المخزون: each opens its own window (SETTINGS_MANAGE is rechecked by the services).
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        visible: (typeof categoryCostVM !== "undefined" && categoryCostVM.canManage) || (typeof stockMinLevelVM !== "undefined" && stockMinLevelVM.canManage)
                        Button {
                            objectName: "openCategoryCostsButton"
                            visible: typeof categoryCostVM !== "undefined" && categoryCostVM.canManage
                            text: "🏷  تكلفة المصنعية للحروف"
                            font.family: "Cairo"
                            onClicked: categoryCostsDialog.open()
                        }
                        Button {
                            objectName: "openStockMinLevelsButton"
                            visible: typeof stockMinLevelVM !== "undefined" && stockMinLevelVM.canManage
                            text: "📉  الحد الأدنى للمخزون"
                            font.family: "Cairo"
                            onClicked: stockMinLevelsDialog.open()
                        }
                        Item { Layout.fillWidth: true }
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
                                    "إجمالي الوزن المكافئ عيار 21"

                                color: theme.textMuted

                                font.family: "Cairo"
                            }


                            Item {
                                Layout.fillWidth: true
                            }


                            Label {
                                objectName: "inventoryTotalWeight21"
                                text: inventoryVM.totalWeight21Text + " جرام"

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


                            BarcodeField {
                                id: inventorySearchField
                                mixed: true            // names in Arabic, or a scanned barcode

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
                        Layout.preferredHeight: 158

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
                            // KARAT + ITEM TYPE (server-side, combine with the search)
                            // -----------------------------------------

                            RowLayout {
                                objectName: "inventoryKaratTypeFilters"
                                Layout.fillWidth: true
                                layoutDirection: Qt.RightToLeft
                                spacing: 8

                                Label {
                                    text: "العيار"
                                    color: theme.textSecondary
                                    font.family: "Cairo"
                                    font.pixelSize: 11
                                }

                                ComboBox {
                                    id: inventoryKaratFilter
                                    objectName: "inventoryKaratFilter"
                                    Layout.preferredWidth: 92
                                    Layout.preferredHeight: 34
                                    font.family: "Cairo"
                                    textRole: "name"
                                    model: [{ "id": 0, "name": "الكل" }].concat(
                                        masterDataVM.karats.map(function(k) {
                                            return { "id": k.id, "name": String(k.karat_value) } }))
                                    onActivated: inventoryVM.setFilters(
                                        Number(model[currentIndex].id),
                                        Number(inventoryItemTypeFilter.model[inventoryItemTypeFilter.currentIndex].id))
                                }

                                Label {
                                    text: "الصنف"
                                    color: theme.textSecondary
                                    font.family: "Cairo"
                                    font.pixelSize: 11
                                }

                                ComboBox {
                                    id: inventoryItemTypeFilter
                                    objectName: "inventoryItemTypeFilter"
                                    Layout.preferredWidth: 120
                                    Layout.preferredHeight: 34
                                    font.family: "Cairo"
                                    textRole: "name"
                                    model: [{ "id": 0, "name": "الكل" }].concat(
                                        masterDataVM.itemTypes.map(function(t) {
                                            return { "id": t.id, "name": t.name } }))
                                    onActivated: inventoryVM.setFilters(
                                        Number(inventoryKaratFilter.model[inventoryKaratFilter.currentIndex].id),
                                        Number(model[currentIndex].id))
                                }

                                Item { Layout.fillWidth: true }
                            }


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
                                    objectName: "barcodeReviewPrintButton"
                                    text: "🖨  مراجعة وطباعة"

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

                        // An exact barcode / item number that is no longer current stock (e.g. SOLD):
                        // read-only traceability, never part of the current-stock list or totals.
                        Rectangle {
                            objectName: "inventoryHistoryCard"
                            visible: inventoryVM.hasHistoryPiece
                            z: 2                                   // above the (empty) stock list
                            anchors.centerIn: parent
                            width: Math.min(parent.width - 24, 560)
                            height: historyColumn.implicitHeight + 24
                            radius: theme.radiusSmall
                            color: theme.bgTertiary
                            border.color: theme.amber
                            border.width: 1

                            ColumnLayout {
                                id: historyColumn
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: 12
                                spacing: 4

                                Label {
                                    objectName: "inventoryHistoryTitle"
                                    Layout.fillWidth: true
                                    text: "القطعة " + (inventoryVM.historyPiece.itemNumber || "")
                                          + " — " + (inventoryVM.historyPiece.status || "")
                                          + " (ليست ضمن المخزون الحالي)"
                                    color: theme.amber
                                    font.family: "Cairo"
                                    font.bold: true
                                    horizontalAlignment: Text.AlignRight
                                }
                                Label {
                                    objectName: "inventoryHistoryDetails"
                                    Layout.fillWidth: true
                                    text: "الباركود: " + (inventoryVM.historyPiece.barcode || "—")
                                          + "  •  " + (inventoryVM.historyPiece.itemType || "")
                                          + "  •  عيار " + (inventoryVM.historyPiece.karat || "")
                                          + "  •  " + (inventoryVM.historyPiece.weight || "") + " جرام"
                                          + "  •  المصنع: " + (inventoryVM.historyPiece.factory || "—")
                                          + "  •  الفئة: " + (inventoryVM.historyPiece.category || "—")
                                    color: theme.textPrimary
                                    font.family: "Cairo"
                                    wrapMode: Text.WordWrap
                                    horizontalAlignment: Text.AlignRight
                                }
                                Label {
                                    objectName: "inventoryHistorySale"
                                    Layout.fillWidth: true
                                    visible: (inventoryVM.historyPiece.invoiceNumber || "").length > 0
                                    text: "الفاتورة: " + (inventoryVM.historyPiece.invoiceNumber || "")
                                          + "  •  " + (inventoryVM.historyPiece.invoiceDate || "")
                                          + "  •  العميل: " + (inventoryVM.historyPiece.customer || "—")
                                    color: theme.textSecondary
                                    font.family: "Cairo"
                                    wrapMode: Text.WordWrap
                                    horizontalAlignment: Text.AlignRight
                                }
                                Button {
                                    objectName: "inventoryHistoryOpenInvoice"
                                    Layout.alignment: Qt.AlignRight
                                    visible: (inventoryVM.historyPiece.invoiceId || "").length > 0
                                    text: "فتح الفاتورة"
                                    font.family: "Cairo"
                                    onClicked: {
                                        root.currentPage = 1
                                        billingWorkspace.currentTab = 1
                                        invoiceHistoryVM.openInvoice(inventoryVM.historyPiece.invoiceId)
                                    }
                                }
                            }
                        }

                        ColumnLayout {
                            visible:
                                inventoryPage.filteredItems().length === 0
                                && inventoryVM.errorMessage.length === 0
                                && !inventoryVM.hasHistoryPiece

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
                                        || inventoryVM.karatFilterId > 0
                                        || inventoryVM.itemTypeFilterId > 0
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

                                readonly property bool printable:
                                    inventoryPage.hasBarcode(modelData)

                                width:
                                    inventoryList.width

                                // One compact line (factory / category elide, the barcode box fits any value).
                                height: 42

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

                                    anchors.leftMargin: 12
                                    anchors.rightMargin: 46

                                    layoutDirection: Qt.RightToLeft

                                    spacing: 10


                                    Label {
                                        objectName: "inventoryItemNumber"
                                        Layout.preferredWidth: 52

                                        text: "#" + modelData.inventory_item_id

                                        color: theme.gold

                                        font.family: "Cairo"
                                        font.pixelSize: 13
                                        font.bold: true

                                        horizontalAlignment: Text.AlignRight
                                    }


                                    Label {
                                        Layout.preferredWidth: 104

                                        text:
                                            modelData.item_type_name
                                            || "صنف غير محدد"

                                        color: theme.textPrimary

                                        font.family: "Cairo"
                                        font.pixelSize: 13
                                        font.bold: true

                                        elide: Text.ElideLeft
                                        horizontalAlignment: Text.AlignRight
                                    }


                                    Label {
                                        objectName: "inventoryKaratWeight"
                                        Layout.preferredWidth: 138

                                        text:
                                            "عيار "
                                            + (modelData.karat_value || "—")
                                            + "  •  "
                                            + inventoryPage.weightText(
                                                modelData.weight_grams
                                            )
                                            + " جرام"

                                        color: theme.textSecondary

                                        font.family: "Cairo"
                                        font.pixelSize: 12

                                        elide: Text.ElideLeft
                                        horizontalAlignment: Text.AlignRight
                                    }


                                    Label {
                                        Layout.fillWidth: true

                                        text:
                                            (modelData.factory_name || "—")
                                            + "  •  "
                                            + (modelData.category_code || "—")
                                            + (
                                                modelData.size !== null
                                                && modelData.size !== undefined
                                                && String(modelData.size).length > 0
                                                ? "  •  مقاس " + modelData.size
                                                : ""
                                            )

                                        color: theme.textMuted

                                        font.family: "Cairo"
                                        font.pixelSize: 11

                                        elide: Text.ElideLeft
                                        horizontalAlignment: Text.AlignRight
                                    }


                                    Rectangle {
                                        Layout.preferredWidth: 84
                                        Layout.preferredHeight: 24

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


                                    Rectangle {
                                        objectName: "inventoryBarcodeBox"
                                        Layout.preferredWidth: 150
                                        Layout.maximumWidth: 150
                                        Layout.preferredHeight: 26

                                        // A long barcode never spills over its neighbours or the checkbox:
                                        // the text fits (down to 9 px), then elides in the middle; the full
                                        // value stays stored and is shown on hover.
                                        clip: true
                                        ToolTip.visible: Boolean(modelData.barcode_value) && barcodeHover.hovered
                                        ToolTip.text: modelData.barcode_value || ""
                                        HoverHandler { id: barcodeHover }

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


                                        Label {
                                            visible: !modelData.barcode_value

                                            anchors.centerIn: parent

                                            text: "⚠ بدون باركود"

                                            color: theme.amber

                                            font.family: "Cairo"
                                            font.pixelSize: 11
                                            font.bold: true
                                        }


                                        Label {
                                            visible:
                                                Boolean(
                                                    modelData.barcode_value
                                                )

                                            objectName: "inventoryBarcodeText"
                                            anchors.centerIn: parent
                                            width: parent.width - 12
                                            horizontalAlignment: Text.AlignHCenter

                                            text:
                                                modelData.barcode_value
                                                || ""

                                            color:
                                                theme.textPrimary

                                            font.family: "Cairo"
                                            font.pixelSize: 12
                                            fontSizeMode: Text.HorizontalFit
                                            minimumPixelSize: 9
                                            elide: Text.ElideMiddle
                                        }
                                    }


                                    Label {
                                        objectName: "inventoryStatusText"
                                        Layout.preferredWidth: 78

                                        text:
                                            inventoryPage.statusText(
                                                modelData.inventory_status
                                            )

                                        color:
                                            modelData.inventory_status === "IN_STOCK"
                                            ? theme.green
                                            : theme.amber

                                        font.family: "Cairo"
                                        font.pixelSize: 11

                                        horizontalAlignment: Text.AlignRight
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
                                    objectName: "inventorySelectBox"

                                    // بدون باركود: no checkbox at all (nothing to print, no label consumed).
                                    visible: inventoryRow.printable
                                    enabled: inventoryRow.printable

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


                // index 0: الرئيسية — today's activity (read-only).
                DashboardPage {
                    pageTitle: "🏠 الرئيسية"
                    onNavigateTo: function(page) { root.currentPage = page }
                }


                // Billing V2 (canonical invoices). The frozen V1 BillingPage
                // component above is kept unchanged but no longer shown.
                // Billing: فاتورة جديدة + سجل الفواتير (read-only history) as two tabs of one workspace.
                BillingWorkspace {
                    id: billingWorkspace
                    pageTitle: "🧾 الفواتير"
                    onCustomerRequested: function(customerId) { root.openCustomerDetails(customerId) }
                }


                InventoryPage {
                    id: inventoryPage
                    objectName: "inventoryPage"
                    pageTitle: "📦 المخزون"
                }


                CustomersPage {
                    id: customersWorkspace
                    pageTitle: "👥 العملاء"
                }


                // index 4: الخزنة — daily cash in / out and operational cash movements.
                FinancialAccountsPage {
                    pageTitle: "💰 الخزنة"
                    onCategoriesRequested: cashCategoriesDialog.open()
                    onInvoiceRequested: function(invoiceId) {
                        root.currentPage = 1
                        billingWorkspace.currentTab = 1
                        invoiceHistoryVM.openInvoice(invoiceId)
                    }
                    onCorrectionRequested: function(operationId) {
                        root.currentPage = 1
                        correctionVM.openFinancialOperation(operationId)
                    }
                    onCustomerRequested: function(customerId) { root.openCustomerDetails(customerId) }
                }


                // index 5: التقارير — the basic reports (read only): sales, stock, gold, customers, suppliers, treasury
                ReportsPage {
                    id: reportsPage
                    pageTitle: "📊 التقارير"
                    onInvoiceRequested: function(invoiceId) {
                        root.currentPage = 1
                        billingWorkspace.currentTab = 1
                        invoiceHistoryVM.openInvoice(invoiceId)
                    }
                    onCustomerRequested: function(customerId) { root.openCustomerDetails(customerId) }
                    onTripRequested: function(tripId) {
                        root.currentPage = 15
                        if (typeof marketTripVM !== "undefined") marketTripVM.selectTrip(tripId)
                    }
                }


                SettingsPage {
                    pageTitle: "⚙️ الإعدادات"
                }


                FactoriesPage {
                    id: factoriesPage
                    objectName: "factoriesPage"
                    pageTitle: "🏭 المصانع"
                }


                ItemTypesPage {
                    id: itemTypesPage
                    objectName: "itemTypesPage"
                    pageTitle: "💍 الأصناف"
                }
                CategoriesPage {
                    id: categoriesPage
                    objectName: "categoriesPage"
                    pageTitle: "🏷️ الفئات"}

                // index 10: one review area for every management review type
                ManagementReviewPage {
                    pageTitle: "مراجعات المدير"
                    onInvoiceRequested: function(invoiceId) {
                        root.currentPage = 1
                        billingWorkspace.currentTab = 1
                        invoiceHistoryVM.openInvoice(invoiceId)
                    }
                    onCustomerRequested: function(customerId) { root.openCustomerDetails(customerId) }
                }

                // index 11: بيان المخزون — current stock by karat → item type (read only)
                StockBalancePage {
                    onBackRequested: root.currentPage = 2
                }

                // index 12: الجرد بالباركود — transient barcode counting session (read only)
                StocktakePage {
                    onBackRequested: root.currentPage = 2
                }

                // index 13: Settings → المستخدمون والصلاحيات (USERS_MANAGE, owner only)
                UsersAdminPage {
                    objectName: "usersAdminPage"
                    pageTitle: "المستخدمون والصلاحيات"
                    onBackRequested: root.currentPage = 6
                }

                // index 14: الموردون — supplier positions (gold / making), purchase invoices and barcode intake
                SuppliersPage {
                    objectName: "suppliersPage"
                    pageTitle: "🤝 الموردون"
                    onTripRequested: function(tripId) {
                        root.currentPage = 15
                        if (typeof marketTripVM !== "undefined") marketTripVM.selectTrip(tripId)
                    }
                }

                // index 15: عهدة السوق — custody of cash and Scrap taken to the market (OWNER only)
                MarketTripsPage {
                    objectName: "marketTripsPage"
                    pageTitle: "🧭 عهدة السوق"
                }

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
                    selected: root.currentPage === 2 || root.currentPage === 11 || root.currentPage === 12

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
                    objectName: "cashNavButton"
                    // الخزنة: shown to users who may see the cash ledger (the service re-checks).
                    visible: typeof financialAccountsVM !== "undefined" && financialAccountsVM.canView
                    text: "💰  الخزنة"
                    selected: root.currentPage === 4

                    onClicked:
                        root.currentPage = 4
                }


                NavButton {
                    objectName: "suppliersNavButton"
                    // الموردون: shown to users who may view suppliers (the services re-check every right).
                    visible: typeof supplierVM !== "undefined" && supplierVM.canView
                    text: "🤝  الموردون"
                    selected: root.currentPage === 14

                    onClicked:
                        root.currentPage = 14
                }


                NavButton {
                    objectName: "marketTripsNavButton"
                    // عهدة السوق: owner only (the service re-checks the right in every transaction).
                    visible: typeof marketTripVM !== "undefined" && marketTripVM.canManage
                    text: "🧭  عهدة السوق"
                    selected: root.currentPage === 15

                    onClicked:
                        root.currentPage = 15
                }


                NavButton {
                    objectName: "reportsNavButton"
                    // Shown when the user may open at least one report (each report re-checks its own rights in the service).
                    visible: reportsPage.canView
                    text: "📊  التقارير"
                    selected: root.currentPage === 5

                    onClicked:
                        root.currentPage = 5
                }


                // مراجعات المدير • N — visible to everyone; amber while reviews are open.
                NavButton {
                    id: reviewsNav
                    objectName: "reviewsNavButton"
                    text: "🔔  مراجعات المدير" + (typeof reviewsVM !== "undefined" && reviewsVM.totalCount > 0
                                                  ? "  •  " + reviewsVM.totalCount : "")
                    selected: root.currentPage === 10
                    onClicked: root.currentPage = 10
                    Rectangle {
                        anchors.left: parent.left
                        anchors.leftMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        width: 8
                        height: 8
                        radius: 4
                        visible: typeof reviewsVM !== "undefined" && reviewsVM.totalCount > 0
                        color: theme.amber
                    }
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
                        || root.currentPage === 13
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

    // One header balance: accent bar, small caption (+ big karat), then the value.
    component BalanceBox: Rectangle {
        id: box
        property color accent: theme.gold
        property string caption: ""
        property string karat: ""
        property string valueName: ""
        property string value: ""
        property bool zero: false
        Layout.preferredHeight: 46
        Layout.preferredWidth: boxColumn.implicitWidth + (root.width < 1200 ? 14 : 20)
        Layout.minimumWidth: Layout.preferredWidth
        radius: theme.radiusSmall
        color: theme.bgTertiary
        border.color: balanceStrip.failed ? theme.red : theme.border
        border.width: 1
        Rectangle { anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                    anchors.margins: 1; width: 3; radius: 2; color: box.accent; opacity: box.zero ? 0.5 : 1 }
        Column {
            id: boxColumn
            anchors.centerIn: parent
            anchors.horizontalCenterOffset: -1
            spacing: -3
            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                layoutDirection: Qt.RightToLeft
                spacing: 4
                Label {
                    anchors.baseline: karatLabel.visible ? karatLabel.baseline : undefined
                    text: box.caption
                    color: theme.textMuted
                    font.family: "Cairo"
                    font.pixelSize: balanceStrip.labelSize
                }
                Label {
                    id: karatLabel
                    visible: box.karat.length > 0
                    text: box.karat
                    color: box.accent
                    opacity: box.zero ? 0.6 : 1
                    font.family: "Cairo"
                    font.pixelSize: balanceStrip.karatSize
                    font.bold: true
                }
            }
            Label {
                objectName: box.valueName
                anchors.horizontalCenter: parent.horizontalCenter
                text: box.value
                color: box.karat.length ? theme.textPrimary : box.accent        // cash: the gold figure
                opacity: box.zero ? 0.45 : 1
                font.family: "Cairo"
                font.pixelSize: balanceStrip.valueSize
                font.bold: true
            }
        }
    }

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

    // CUSTOMERS V1 BEGIN
    // Customer controls keep Arabic direction local to this screen.
    component CustomerLabel: Label {
        property string value: ""
        text: "\u200f" + value
        textFormat: Text.PlainText
        font.family: "Cairo"
        font.pixelSize: 12
        color: theme.textPrimary
        horizontalAlignment: Text.AlignRight
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.Wrap
    }

    component CustomerButton: Button {
        id: customerButton
        font.family: "Cairo"
        implicitHeight: 38
        leftPadding: 12
        rightPadding: 12
        contentItem: CustomerLabel {
            value: customerButton.text
            color: customerButton.enabled ? theme.textPrimary : theme.textDisabled
        }
        background: Rectangle {
            radius: theme.radiusSmall
            color: customerButton.down || customerButton.checked
                   ? theme.goldDark : customerButton.hovered ? theme.panelHover : theme.bgTertiary
            border.color: customerButton.checked ? theme.gold : theme.border
        }
    }

    component CustomerField: TextField {
        font.family: "Cairo"
        horizontalAlignment: Text.AlignRight
        selectByMouse: true
        color: theme.textPrimary
        placeholderTextColor: theme.textMuted
        implicitHeight: 40
        leftPadding: 10
        rightPadding: 10
        background: Rectangle {
            radius: theme.radiusSmall
            color: theme.bgPrimary
            border.color: parent.activeFocus ? theme.gold : theme.border
        }
    }

    component CustomerCombo: ComboBox {
        id: customerCombo
        font.family: "Cairo"
        textRole: "label"
        valueRole: "value"
        implicitHeight: 40
        leftPadding: 28
        rightPadding: 10
        contentItem: CustomerLabel {
            value: customerCombo.displayText
            elide: Text.ElideLeft
            wrapMode: Text.NoWrap
        }
        delegate: ItemDelegate {
            width: customerCombo.width
            highlighted: customerCombo.highlightedIndex === index
            font.family: "Cairo"
            contentItem: CustomerLabel {
                value: String(modelData[customerCombo.textRole] || "")
            }
        }
        indicator: CustomerLabel {
            x: 10
            y: (customerCombo.height - height) / 2
            value: "▾"
            color: theme.gold
        }
        background: Rectangle {
            radius: theme.radiusSmall
            color: theme.bgPrimary
            border.color: customerCombo.activeFocus ? theme.gold : theme.border
        }
    }

    component CustomerCard: Rectangle {
        color: theme.bgSecondary
        radius: theme.radiusCard
        border.color: theme.cardBorder
    }

    component CustomerDetail: RowLayout {
        property string caption: ""
        property string value: ""
        property int captionWidth: 95
        spacing: 10
        CustomerLabel {
            Layout.fillWidth: true
            value: parent.value.length ? parent.value : "—"
        }
        CustomerLabel {
            Layout.preferredWidth: parent.captionWidth
            value: parent.caption
            color: theme.textMuted
        }
    }

    component CustomerFormRow: RowLayout {
        property string caption: ""
        property alias text: customerFormField.text
        property alias maximumLength: customerFormField.maximumLength
        spacing: 12
        CustomerField {
            id: customerFormField
            Layout.fillWidth: true
        }
        CustomerLabel {
            Layout.preferredWidth: 100
            value: parent.caption
            color: theme.textSecondary
        }
    }

    component CustomerMessageBox: CustomerCard {
        property string message: ""
        property bool success: false
        Layout.fillWidth: true
        implicitHeight: customerMessage.implicitHeight + 20
        visible: message.length > 0
        border.color: success ? theme.green : theme.red
        CustomerLabel {
            id: customerMessage
            anchors.fill: parent
            anchors.margins: 10
            value: parent.message
            color: parent.success ? theme.green : theme.red
        }
    }

    component CustomersPage: Item {
        CustomerAccountPage {
            anchors.fill: parent
            z: 10
            visible: typeof customerAccountVM !== "undefined" && customerAccountVM.isOpen && !customersPage.showProfile
            onInvoiceRequested: function(invoiceId) {
                root.currentPage = 1
                billingWorkspace.currentTab = 1
                invoiceHistoryVM.openInvoice(invoiceId)
            }
            onCorrectionRequested: function(settlementId) {
                root.currentPage = 1
                correctionVM.openCustomerSettlement(settlementId)
            }
            onDepositCorrectionRequested: function(depositId) {
                root.currentPage = 1
                correctionVM.openCustomerDeposit(depositId)
            }
            // The ONE write-off flow (customerVM.openWriteOff / writeOffDialog) acts on customerVM's selection,
            // so it is pinned to the account's immutable customer id first; any mismatch refuses to open it.
            // عكس الشطب from the write-off row itself: the ONE existing reversal dialog (reason + password).
            onDeadCashReversalRequested: function(customerId, deadCashId, reference, amountText) {
                if (!customerId || customerAccountVM.customerId !== customerId)
                    return
                if (customerVM.selectedCustomerId !== customerId && !customerVM.selectCustomer(customerId))
                    return
                if (customerVM.selectedCustomerId !== customerId || !customerVM.canReverseDeadCash)
                    return
                reverseDeadCashDialog.begin(deadCashId, reference, amountText)
            }
            onDeadCashRequested: function(customerId) {
                if (!customerId || customerAccountVM.customerId !== customerId)
                    return
                if (customerVM.selectedCustomerId !== customerId && !customerVM.selectCustomer(customerId))
                    return
                if (customerVM.selectedCustomerId !== customerId || customerAccountVM.customerId !== customerId)
                    return
                if (!customerVM.canWriteOff)
                    return
                if (customerVM.openWriteOff() && customerVM.selectedCustomerId === customerId)
                    writeOffDialog.begin()
                else
                    customerVM.closeWriteOff()
            }
        }

        id: customersPage
        objectName: "customersPage"
        property string pageTitle: ""
        property bool initialized: false
        property int historyTab: 0
        property bool skipEntryRefresh: false
        property bool showProfile: false
        function revealDetails() {
            historyTab = 0
            customerBodyScroll.contentItem.contentY = Math.max(0, customerProfileCard.y)
        }
        readonly property var customer: customerVM.selectedCustomer || ({})
        readonly property var statement: customerVM.statementSummary || ({})
        readonly property var filterTypes: [{"id": 0, "label": "كل أنواع العملاء"}].concat(customerVM.customerTypes)

        function initialize() {
            if (visible) {
                initialized = true
                if (skipEntryRefresh) { skipEntryRefresh = false; customerVM.refreshList(); return }
                customerVM.refresh()
                customerAccountVM.refresh()
            }
        }

        function typeIndex(rows, typeId) {
            for (var i = 0; i < rows.length; ++i) {
                if (rows[i].id === typeId)
                    return i
            }
            return 0
        }

        function applyFilters() {
            customerVM.setFilters(
                customerStatusFilter.currentValue || "ALL",
                customerTypeFilter.currentValue || 0,
                customerBalanceFilter.currentValue || "ALL"
            )
        }

        function requestStatusChange() {
            var customerId = customerVM.selectedCustomerId
            // Refresh the profile and warnings immediately before confirmation.
            if (!customerVM.selectCustomer(customerId))
                return
            customerStatusDialog.customerId = customerId
            customerStatusDialog.customerName = String(customerVM.selectedCustomer.name || "")
            customerStatusDialog.deactivating = !!customerVM.selectedCustomer.is_active
            customerStatusDialog.hasBalance = !!customerVM.selectedCustomer.balance_nonzero
            customerStatusDialog.balanceText = String(customerVM.selectedCustomer.balance_text || "")
            customerStatusDialog.balanceMeaning = String(customerVM.selectedCustomer.balance_meaning || "")
            customerStatusDialog.flagCount = customerVM.activeFlags.length
            customerVM.clearActionMessage()
            customerStatusDialog.open()
        }

        onVisibleChanged: initialize()
        Component.onCompleted: initialize()

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 16
            spacing: 9

            RowLayout {
                Layout.fillWidth: true
                CustomerButton {
                    text: "\u0643\u0634\u0641 \u062d\u0633\u0627\u0628 / \u062a\u0633\u0648\u064a\u0629"
                    enabled: !!customerVM.selectedCustomerId
                    onClicked: { customersPage.showProfile = false; customerAccountVM.open(customerVM.selectedCustomerId) }
                }
                CustomerButton {
                    objectName: "addCustomerButton"
                    text: "إضافة عميل"
                    enabled: !customerVM.busy
                    onClicked: customerFormDialog.openCreate()
                }
                CustomerButton {
                    text: "تحديث"
                    enabled: !customerVM.busy
                    onClicked: customerVM.refresh()
                }
                Item { Layout.fillWidth: true }
                CustomerLabel {
                    value: customerVM.busy ? "جارٍ التحميل…" : "بيانات العملاء وحساباتهم"
                    color: theme.gold
                    font.pixelSize: 18
                    font.bold: true
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                CustomerPicker {
                    objectName: "customersQuickPicker"
                    Layout.fillWidth: true
                    Layout.fillHeight: false
                    Layout.maximumHeight: implicitHeight
                    Layout.maximumWidth: 580
                    placeholderText: "ابحث واختر عميلاً لفتح ملفه"
                    onCustomerSelected: function(customerId) {
                        customersPage.historyTab = 0
                        customerVM.selectCustomer(customerId)
                    }
                }
                CustomerLabel {
                    value: "انتقال إلى عميل"
                    color: theme.textSecondary
                }
            }

            RowLayout {
                Layout.fillWidth: true
                CustomerButton {
                    text: "مسح البحث"
                    enabled: !customerVM.busy
                    onClicked: {
                        customerSearchField.clear()
                        customerVM.clearSearch()
                    }
                }
                CustomerButton {
                    text: "بحث"
                    enabled: !customerVM.busy
                    onClicked: customerVM.search(customerSearchField.text)
                }
                CustomerField {
                    id: customerSearchField
                    objectName: "customerSearchField"
                    Layout.fillWidth: true
                    placeholderText: "بحث بالاسم أو الهاتف أو المكان أو الكود أو رقم العميل"
                    maximumLength: 255
                    onAccepted: customerVM.search(text)
                }
            }

            RowLayout {
                Layout.fillWidth: true
                CustomerButton {
                    text: "إلغاء الفلاتر"
                    enabled: !customerVM.busy
                    onClicked: {
                        customerSearchField.clear()
                        customerStatusFilter.currentIndex = 0
                        customerTypeFilter.currentIndex = 0
                        customerBalanceFilter.currentIndex = 0
                        customerVM.resetFilters()
                    }
                }
                CustomerCombo {
                    id: customerBalanceFilter
                    objectName: "customerBalanceFilter"
                    Layout.fillWidth: true
                    model: [
                        {"label": "كل الأرصدة", "value": "ALL"},
                        {"label": "على العميل للمحل", "value": "CUSTOMER_OWES_SHOP"},
                        {"label": "للعميل عند المحل", "value": "SHOP_OWES_CUSTOMER"},
                        {"label": "الحساب متزن", "value": "SETTLED"}
                    ]
                    enabled: !customerVM.busy
                    onActivated: customersPage.applyFilters()
                }
                CustomerCombo {
                    id: customerTypeFilter
                    objectName: "customerTypeFilter"
                    Layout.preferredWidth: 150
                    model: customersPage.filterTypes
                    valueRole: "id"
                    enabled: !customerVM.busy
                    onActivated: customersPage.applyFilters()
                }
                CustomerCombo {
                    id: customerStatusFilter
                    objectName: "customerStatusFilter"
                    Layout.preferredWidth: 150
                    model: [
                        {"label": "كل العملاء", "value": "ALL"},
                        {"label": "النشطون فقط", "value": "ACTIVE"},
                        {"label": "غير النشطين فقط", "value": "INACTIVE"}
                    ]
                    enabled: !customerVM.busy
                    onActivated: customersPage.applyFilters()
                }
            }

            CustomerMessageBox { message: customerVM.errorMessage }
            CustomerMessageBox {
                message: customerVM.actionMessage
                success: customerVM.actionSuccess
            }

            ScrollView {
                id: customerBodyScroll
                objectName: "customerBodyScroll"
                implicitHeight: 0
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                GridLayout {
                    id: customerBody
                    width: customerBodyScroll.availableWidth
                    columns: width >= 950 ? 2 : 1
                    layoutDirection: Qt.RightToLeft
                    columnSpacing: 12
                    rowSpacing: 12
                    readonly property bool wide: columns === 2

                    CustomerCard {
                        Layout.fillWidth: true
                        Layout.preferredWidth: customerBody.wide ? customerBody.width * 0.42 : customerBody.width
                        Layout.alignment: Qt.AlignTop
                        Layout.preferredHeight: Math.max(300, customerBodyScroll.availableHeight)
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 8
                            CustomerLabel {
                                Layout.fillWidth: true
                                value: "العملاء — " + customerVM.customerCount + " في هذه الصفحة"
                                color: theme.gold
                                font.bold: true
                            }
                            ListView {
                                id: customerList
                                objectName: "customerList"
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                clip: true
                                spacing: 7
                                model: customerVM.customers
                                ScrollBar.vertical: ScrollBar {}
                                delegate: CustomerCard {
                                    required property var modelData
                                    width: customerList.width - 12
                                    // Compact: two lines (identity / contact + balance) instead of eight.
                                    height: customerRow.implicitHeight + 12
                                    color: customerVM.selectedCustomerId === modelData.id ? theme.bgTertiary : theme.bgPrimary
                                    border.color: customerVM.selectedCustomerId === modelData.id ? theme.gold : theme.border
                                    ColumnLayout {
                                        id: customerRow
                                        anchors.fill: parent
                                        anchors.topMargin: 6
                                        anchors.bottomMargin: 6
                                        anchors.leftMargin: 10
                                        anchors.rightMargin: 10
                                        spacing: 1
                                        RowLayout {
                                            Layout.fillWidth: true
                                            layoutDirection: Qt.RightToLeft
                                            spacing: 8
                                            CustomerLabel {
                                                objectName: "customerRowName"
                                                Layout.fillWidth: true
                                                value: String(modelData.name || "")
                                                font.bold: true
                                                font.pixelSize: 13
                                                wrapMode: Text.NoWrap
                                                elide: Text.ElideLeft
                                            }
                                            CustomerLabel {
                                                value: "#" + modelData.id + "  ·  " + (modelData.customer_code || "—")
                                                color: theme.gold
                                                font.pixelSize: 11
                                                wrapMode: Text.NoWrap
                                            }
                                            CustomerLabel {
                                                value: String(modelData.status_label || "")
                                                color: modelData.is_active ? theme.green : theme.textMuted
                                                font.pixelSize: 10
                                                wrapMode: Text.NoWrap
                                            }
                                        }
                                        RowLayout {
                                            Layout.fillWidth: true
                                            layoutDirection: Qt.RightToLeft
                                            spacing: 8
                                            CustomerLabel {
                                                objectName: "customerRowContact"
                                                Layout.fillWidth: true
                                                value: [modelData.mobile, modelData.type_label, modelData.place]
                                                       .filter(function(v) { return v && String(v).length > 0 }).join("  ·  ") || "—"
                                                color: theme.textSecondary
                                                font.pixelSize: 11
                                                wrapMode: Text.NoWrap
                                                elide: Text.ElideLeft
                                            }
                                            CustomerLabel {
                                                value: "آخر حركة: " + (modelData.last_transaction_text || "—")
                                                color: theme.textMuted
                                                font.pixelSize: 10
                                                wrapMode: Text.NoWrap
                                                elide: Text.ElideLeft
                                                Layout.maximumWidth: 150
                                            }
                                            CustomerLabel {
                                                objectName: "customerRowBalance"
                                                value: String(modelData.balance_text || "") + " — " + (modelData.balance_meaning || "")
                                                color: modelData.balance_nonzero ? theme.gold : theme.green
                                                font.pixelSize: 12
                                                font.bold: true
                                                wrapMode: Text.NoWrap
                                            }
                                        }
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        enabled: !customerVM.busy
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            customersPage.historyTab = 0
                                            customerVM.selectCustomer(modelData.id)
                                        }
                                    }
                                }
                                CustomerLabel {
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    visible: customerList.count === 0
                                    value: customerVM.busy ? "جارٍ تحميل العملاء…" : customerVM.errorMessage.length ? "تعذر تحميل قائمة العملاء." : "لا توجد نتائج مطابقة."
                                    color: theme.textMuted
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                CustomerButton {
                                    text: "التالي"
                                    enabled: !customerVM.busy && customerVM.hasMoreCustomers
                                    onClicked: customerVM.loadPage(customerVM.page + 1)
                                }
                                CustomerLabel {
                                    Layout.fillWidth: true
                                    value: "صفحة " + customerVM.page + " · حتى " + customerVM.pageSize + " عميل"
                                    color: theme.textMuted
                                    font.pixelSize: 10
                                }
                                CustomerButton {
                                    text: "السابق"
                                    enabled: !customerVM.busy && customerVM.page > 1
                                    onClicked: customerVM.loadPage(customerVM.page - 1)
                                }
                            }
                        }
                    }

                    CustomerCard {
                        id: customerProfileCard
                        objectName: "customerProfileCard"
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        Layout.preferredWidth: customerBody.wide ? customerBody.width * 0.58 : customerBody.width
                        Layout.preferredHeight: Math.max(680, customerProfile.implicitHeight + 24)
                        ColumnLayout {
                            id: customerProfile
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 12
                            spacing: 8
                            CustomerLabel {
                                objectName: "customerProfileName"
                                Layout.fillWidth: true
                                value: customerVM.hasSelection ? String(customersPage.customer.name || "") : "ملف العميل"
                                color: theme.gold
                                font.pixelSize: 18
                                font.bold: true
                            }
                            CustomerMessageBox { message: customerVM.profileErrorMessage }
                            CustomerLabel {
                                Layout.fillWidth: true
                                visible: !customerVM.hasSelection
                                value: "اختر عميلاً من القائمة لعرض بياناته ورصيده وسجله."
                                color: theme.textMuted
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: customerVM.hasSelection
                                spacing: 5
                                RowLayout {
                                    Layout.fillWidth: true
                                    CustomerButton {
                                        text: "تعديل البيانات"
                                        enabled: !customerVM.busy
                                        onClicked: customerFormDialog.openEdit(customerVM.selectedCustomerId)
                                    }
                                    CustomerButton {
                                        objectName: "customerStatusButton"
                                        text: customersPage.customer.is_active ? "تعطيل العميل" : "إعادة التفعيل"
                                        enabled: !customerVM.busy
                                        onClicked: customersPage.requestStatusChange()
                                    }
                                    CustomerLabel {
                                        Layout.fillWidth: true
                                        value: String(customersPage.customer.status_label || "")
                                        color: customersPage.customer.is_active ? theme.green : theme.textMuted
                                    }
                                }
                                CustomerLabel {
                                    Layout.fillWidth: true
                                    value: "رقم العميل: " + String(customersPage.customer.id || "")
                                    color: theme.gold
                                    font.pixelSize: 14
                                    font.bold: true
                                }
                                CustomerDetail {
                                    Layout.fillWidth: true
                                    caption: "الكود"
                                    value: String(customersPage.customer.customer_code || "")
                                }
                                CustomerDetail {
                                    Layout.fillWidth: true
                                    caption: "النوع"
                                    value: String(customersPage.customer.type_label || "")
                                }
                                CustomerDetail {
                                    Layout.fillWidth: true
                                    caption: "الهاتف"
                                    value: String(customersPage.customer.mobile || "")
                                }
                                CustomerDetail {
                                    Layout.fillWidth: true
                                    caption: "المكان"
                                    value: String(customersPage.customer.place || "")
                                }
                                CustomerDetail {
                                    Layout.fillWidth: true
                                    caption: "آخر حركة مالية"
                                    value: String(customersPage.customer.last_transaction_text || "")
                                }
                                CustomerLabel {
                                    Layout.fillWidth: true
                                    value: "الرصيد: " + (customersPage.customer.balance_text || "—") + " — " + (customersPage.customer.balance_meaning || "")
                                    color: customersPage.customer.balance_nonzero ? theme.gold : theme.green
                                    font.bold: true
                                }
                                CustomerCard {
                                    objectName: "deadCashWarningBanner"
                                    Layout.fillWidth: true
                                    visible: !!customersPage.customer.has_dead_cash_history || !!customersPage.customer.has_active_dead_cash_warning
                                    implicitHeight: deadCashBannerColumn.implicitHeight + 16
                                    color: theme.bgPrimary
                                    border.color: customersPage.customer.dead_cash_attention ? theme.amber : theme.border
                                    ColumnLayout {
                                        id: deadCashBannerColumn
                                        anchors.fill: parent
                                        anchors.margins: 8
                                        spacing: 2
                                        CustomerLabel {
                                            objectName: "deadCashWarningText"
                                            Layout.fillWidth: true
                                            value: String(customersPage.customer.dead_cash_message || "")
                                            color: customersPage.customer.dead_cash_attention ? theme.amber : theme.textMuted
                                            font.bold: true
                                        }
                                        CustomerLabel {
                                            objectName: "deadCashSummaryText"
                                            Layout.fillWidth: true
                                            visible: (customerVM.deadCashSummary.count || 0) > 0
                                            value: (customerVM.deadCashSummary.countText || "") + "  |  " + (customerVM.deadCashSummary.totalText || "")
                                                   + "  |  " + (customerVM.deadCashSummary.lastText || "")
                                            color: theme.textSecondary
                                            font.pixelSize: 11
                                        }
                                        CustomerLabel {
                                            Layout.fillWidth: true
                                            visible: !!customerVM.deadCashSummary.reversedText
                                            value: customerVM.deadCashSummary.reversedText || ""
                                            color: theme.textMuted
                                            font.pixelSize: 10
                                        }
                                    }
                                }
                                CustomerButton {
                                    objectName: "writeOffButton"
                                    Layout.fillWidth: true
                                    visible: customerVM.canWriteOff
                                    enabled: !customerVM.busy
                                    text: "⛔ تحويل إلى رصيد مشطوب"
                                    onClicked: if (customerVM.openWriteOff()) writeOffDialog.begin()
                                }
                                CustomerDetail {
                                    Layout.fillWidth: true
                                    caption: "ملاحظات"
                                    value: String(customersPage.customer.notes || "")
                                }
                                CustomerLabel {
                                    Layout.fillWidth: true
                                    visible: (customersPage.customer.active_warning_count || 0) > 0
                                    value: "تنبيه: لدى العميل " + (customersPage.customer.active_warning_count || 0) + " تحذير نشط. راجع تبويب التحذيرات."
                                    color: theme.amber
                                }
                                CustomerDetail {
                                    Layout.fillWidth: true
                                    caption: "التحذيرات النشطة"
                                    captionWidth: 135
                                    value: String(customersPage.customer.active_warning_count || 0)
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    layoutDirection: Qt.RightToLeft
                                    CustomerButton {
                                        Layout.fillWidth: true
                                        text: "كشف الحساب"
                                        checked: customersPage.historyTab === 0
                                        onClicked: customerAccountVM.open(customerVM.selectedCustomerId)
                                    }
                                    CustomerButton {
                                        Layout.fillWidth: true
                                        text: "التحذيرات"
                                        checked: customersPage.historyTab === 1
                                        onClicked: customersPage.historyTab = 1
                                    }
                                    CustomerButton {
                                        objectName: "deadCashHistoryTab"
                                        Layout.fillWidth: true
                                        visible: customerVM.canViewDeadCash        // detail needs DEAD_CASH_VIEW
                                        text: "الرصيد المشطوب"
                                        checked: customersPage.historyTab === 2
                                        onClicked: customersPage.historyTab = 2
                                    }
                                }
                                CustomerLabel {
                                    Layout.fillWidth: true
                                    value: "السجل للعرض فقط"
                                    color: theme.textMuted
                                    font.pixelSize: 10
                                }
                                CustomerButton {
                                    Layout.fillWidth: true
                                    visible: customersPage.historyTab === 0
                                    text: "\u0641\u062a\u062d \u0643\u0634\u0641 \u0627\u0644\u062d\u0633\u0627\u0628 \u0648\u0627\u0644\u062a\u0633\u0648\u064a\u0629"
                                    onClicked: customerAccountVM.open(customerVM.selectedCustomerId)
                                }
                                ListView {
                                    id: customerHistory
                                    visible: customersPage.historyTab !== 0
                                    objectName: "customerHistory"
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: customersPage.historyTab === 0 ? 330 : 245
                                    clip: true
                                    spacing: 6
                                    model: customersPage.historyTab === 0 ? customerVM.transactions : customersPage.historyTab === 1 ? customerVM.activeFlags : customerVM.deadCashHistory
                                    ScrollBar.vertical: ScrollBar {}
                                    delegate: CustomerCard {
                                        required property var modelData
                                        width: customerHistory.width - 12
                                        height: customerHistoryRow.implicitHeight + 20
                                        color: theme.bgPrimary
                                        ColumnLayout {
                                            id: customerHistoryRow
                                            anchors.fill: parent
                                            anchors.margins: 10
                                            spacing: 3
                                            CustomerLabel {
                                                Layout.fillWidth: true
                                                value: customersPage.historyTab === 0
                                                       ? (modelData.type_label || "حركة") + " — " + (modelData.amount_text || "—")
                                                       : customersPage.historyTab === 1
                                                         ? (modelData.type_label || "تحذير") + " — " + (modelData.severity_label || "")
                                                         : "المبلغ المشطوب: " + (modelData.amount_text || "—") + " — " + (modelData.status_label || "")
                                                color: customersPage.historyTab === 1 ? theme.amber : theme.gold
                                                font.bold: true
                                            }
                                            CustomerLabel {
                                                Layout.fillWidth: true
                                                value: customersPage.historyTab === 2
                                                       ? (modelData.reference || "") + "  |  " + (modelData.date_text || "—")
                                                       : "رقم السجل: " + modelData.id + "  |  " + (modelData.occurred_at || modelData.created_at || "—")
                                                color: theme.textMuted
                                                font.pixelSize: 10
                                            }
                                            CustomerLabel {
                                                Layout.fillWidth: true
                                                visible: customersPage.historyTab === 0
                                                value: "المرجع: " + (modelData.reference_code || modelData.reference_id || "—")
                                                color: theme.textSecondary
                                                font.pixelSize: 10
                                            }
                                            CustomerLabel {
                                                Layout.fillWidth: true
                                                visible: customersPage.historyTab === 0
                                                value: String(modelData.direction_label || "")
                                                color: theme.textSecondary
                                            }
                                            CustomerLabel {
                                                Layout.fillWidth: true
                                                visible: customersPage.historyTab === 0
                                                value: "الرصيد بعد الحركة: " + (modelData.running_balance_text || "—") + " — " + (modelData.running_balance_meaning || "")
                                                color: theme.gold
                                                font.bold: true
                                            }
                                            CustomerLabel {
                                                Layout.fillWidth: true
                                                visible: customersPage.historyTab === 2
                                                value: "الرصيد قبل الشطب: " + (modelData.balance_before_text || "—") + " | بعده: " + (modelData.balance_after_text || "—")
                                                color: theme.textSecondary
                                                font.pixelSize: 10
                                            }

                                            CustomerLabel {
                                                Layout.fillWidth: true
                                                value: String(modelData.reason || modelData.description || modelData.notes || "—")
                                            }
                                            CustomerLabel {
                                                Layout.fillWidth: true
                                                visible: customersPage.historyTab === 2
                                                         && !!modelData.notes
                                                         && modelData.notes !== (modelData.reason || modelData.description || modelData.notes)
                                                value: "ملاحظات: " + String(modelData.notes || "")
                                                color: theme.textSecondary
                                            }
                                            CustomerLabel {
                                                Layout.fillWidth: true
                                                visible: customersPage.historyTab === 2 && !!modelData.is_reversed
                                                value: "عُكس الشطب: " + (modelData.reversed_at || "—") + " | " + (modelData.reversal_reason || "—")
                                                color: theme.textMuted
                                                font.pixelSize: 10
                                            }
                                            CustomerButton {
                                                objectName: "reverseDeadCash_" + (modelData.reference || "")
                                                visible: customersPage.historyTab === 2 && !modelData.is_reversed && customerVM.canReverseDeadCash
                                                enabled: !customerVM.busy
                                                text: "عكس الشطب"
                                                onClicked: reverseDeadCashDialog.begin(modelData.id, modelData.reference, modelData.amount_text)
                                            }
                                        }
                                    }
                                    CustomerLabel {
                                        anchors.fill: parent
                                        anchors.margins: 10
                                        visible: customerHistory.count === 0
                                        value: customerVM.profileErrorMessage.length || (customersPage.historyTab === 0 && customerVM.statementErrorMessage.length && Object.keys(customersPage.statement).length === 0) ? "تعذر تحميل السجل." : customersPage.historyTab === 0 ? "لا توجد حركات مطابقة للفترة والنوع المحددين." : customersPage.historyTab === 1 ? "لا توجد تحذيرات نشطة." : "لا يوجد سجل شطب."
                                        color: theme.textMuted
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    visible: customersPage.historyTab === 2
                                    CustomerButton {
                                        text: "التالي"
                                        enabled: !customerVM.busy && (customersPage.historyTab === 0 ? Object.keys(customersPage.statement).length > 0 && customerVM.hasMoreTransactions : customerVM.hasMoreDeadCash)
                                        onClicked: {
                                            if (customersPage.historyTab === 0)
                                                customerVM.loadTransactionsPage(customerVM.transactionsPage + 1)
                                            else
                                                customerVM.loadDeadCashPage(customerVM.deadCashPage + 1)
                                        }
                                    }
                                    CustomerLabel {
                                        Layout.fillWidth: true
                                        value: "صفحة " + (customersPage.historyTab === 0 ? customerVM.transactionsPage : customerVM.deadCashPage)
                                        color: theme.textMuted
                                    }
                                    CustomerButton {
                                        text: "السابق"
                                        enabled: !customerVM.busy && (customersPage.historyTab !== 0 || Object.keys(customersPage.statement).length > 0) && (customersPage.historyTab === 0 ? customerVM.transactionsPage : customerVM.deadCashPage) > 1
                                        onClicked: {
                                            if (customersPage.historyTab === 0)
                                                customerVM.loadTransactionsPage(customerVM.transactionsPage - 1)
                                            else
                                                customerVM.loadDeadCashPage(customerVM.deadCashPage - 1)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        Dialog {
            id: customerFormDialog
            objectName: "customerFormDialog"
            parent: Overlay.overlay
            anchors.centerIn: parent
            width: Math.min(650, root.width - 60)
            height: Math.min(690, root.height - 40)
            modal: true
            closePolicy: Popup.CloseOnEscape
            property string customerId: ""
            readonly property bool editing: customerId.length > 0
            property string storedCustomerCode: ""
            property int existingTypeId: 0
            readonly property var allowedTypes: {
                var rows = [{"id": 0, "label": "غير محدد"}]
                var types = customerVM.customerTypes
                var includesExisting = existingTypeId === 0
                for (var i = 0; i < types.length; ++i) {
                    if (types[i].is_active || (editing && types[i].id === existingTypeId)) {
                        rows.push(types[i])
                        if (types[i].id === existingTypeId)
                            includesExisting = true
                    }
                }
                // A failed/stale type lookup must never clear an existing type.
                if (editing && !includesExisting)
                    rows.push({"id": existingTypeId, "label": "النوع الحالي (غير متاح في القائمة)"})
                return rows
            }

            function fill(row) {
                customerNameInput.text = String(row.name || "")
                storedCustomerCode = String(row.customer_code || "")
                customerMobileInput.text = String(row.mobile || "")
                customerPlaceInput.text = String(row.place || "")
                customerNotesInput.text = String(row.notes || "")
                existingTypeId = row.customer_type_id || 0
                customerFormType.currentIndex = customersPage.typeIndex(allowedTypes, existingTypeId)
            }

            function openCreate() {
                customerId = ""
                fill({})
                customerVM.clearActionMessage()
                open()
            }

            function openEdit(id) {
                if (!customerVM.beginEdit(id))
                    return
                customerId = id
                fill(customerVM.editCustomer)
                customerVM.clearActionMessage()
                open()
            }

            background: CustomerCard { border.color: theme.goldDark }
            header: CustomerLabel {
                value: customerFormDialog.editing ? "تعديل بيانات العميل" : "إضافة عميل"
                padding: 16
                color: theme.gold
                font.pixelSize: 18
                font.bold: true
            }
            contentItem: ScrollView {
                id: customerFormScroll
                clip: true
                contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: customerFormScroll.availableWidth
                    spacing: 10
                    CustomerLabel {
                        Layout.fillWidth: true
                        visible: customerFormDialog.editing
                        value: "رقم العميل: " + customerFormDialog.customerId
                        color: theme.textMuted
                    }
                    CustomerFormRow {
                        id: customerNameInput
                        objectName: "customerNameInput"
                        Layout.fillWidth: true
                        caption: "الاسم *"
                        maximumLength: 150
                    }
                    CustomerDetail {
                        Layout.fillWidth: true
                        visible: customerFormDialog.editing
                        caption: "الكود"
                        value: customerFormDialog.storedCustomerCode
                    }
                    CustomerLabel {
                        Layout.fillWidth: true
                        visible: !customerFormDialog.editing
                        value: "يُنشأ كود العميل تلقائياً بعد الحفظ."
                        color: theme.textMuted
                        font.pixelSize: 10
                    }
                    CustomerFormRow {
                        id: customerMobileInput
                        Layout.fillWidth: true
                        caption: "الهاتف"
                        maximumLength: 50
                    }
                    CustomerFormRow {
                        id: customerPlaceInput
                        Layout.fillWidth: true
                        caption: "المكان"
                        maximumLength: 150
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        CustomerCombo {
                            id: customerFormType
                            Layout.fillWidth: true
                            model: customerFormDialog.allowedTypes
                            valueRole: "id"
                        }
                        CustomerLabel {
                            Layout.preferredWidth: 100
                            value: "النوع"
                            color: theme.textSecondary
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        ScrollView {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 110
                            clip: true
                            TextArea {
                                id: customerNotesInput
                                font.family: "Cairo"
                                horizontalAlignment: Text.AlignRight
                                wrapMode: TextEdit.Wrap
                                selectByMouse: true
                                placeholderText: "ملاحظات العميل"
                                color: theme.textPrimary
                                background: Rectangle {
                                    color: theme.bgPrimary
                                    radius: theme.radiusSmall
                                    border.color: customerNotesInput.activeFocus ? theme.gold : theme.border
                                }
                            }
                        }
                        CustomerLabel {
                            Layout.preferredWidth: 100
                            Layout.alignment: Qt.AlignTop
                            value: "ملاحظات"
                            color: theme.textSecondary
                        }
                    }
                    CustomerMessageBox {
                        message: customerVM.actionMessage
                        success: customerVM.actionSuccess
                    }
                    CustomerLabel {
                        Layout.fillWidth: true
                        value: "الاسم مطلوب. يمكن تكرار الأسماء؛ لكل عميل رقم مستقل."
                        color: theme.textMuted
                        font.pixelSize: 10
                    }
                }
            }
            footer: RowLayout {
                spacing: 10
                CustomerButton {
                    Layout.leftMargin: 16
                    Layout.bottomMargin: 12
                    text: "إلغاء"
                    enabled: !customerVM.busy
                    onClicked: customerFormDialog.close()
                }
                Item { Layout.fillWidth: true }
                CustomerButton {
                    objectName: "saveCustomerButton"
                    Layout.rightMargin: 16
                    Layout.bottomMargin: 12
                    text: "حفظ"
                    enabled: !customerVM.busy && customerNameInput.text.trim().length > 0 && customerFormType.currentIndex >= 0
                    onClicked: {
                        var saved
                        if (customerFormDialog.editing) {
                            saved = customerVM.updateCustomer(
                                customerFormDialog.customerId, customerNameInput.text,
                                customerMobileInput.text,
                                customerPlaceInput.text, customerFormType.currentValue,
                                customerNotesInput.text)
                        } else {
                            saved = customerVM.createCustomer(
                                customerNameInput.text,
                                customerMobileInput.text, customerPlaceInput.text,
                                customerFormType.currentValue, customerNotesInput.text)
                        }
                        if (saved)
                            customerFormDialog.close()
                    }
                }
            }
        }

        Dialog {
            id: customerStatusDialog
            objectName: "customerStatusDialog"
            parent: Overlay.overlay
            anchors.centerIn: parent
            width: Math.min(570, root.width - 60)
            modal: true
            closePolicy: Popup.CloseOnEscape
            property string customerId: ""
            property string customerName: ""
            property bool deactivating: false
            property bool hasBalance: false
            property string balanceText: ""
            property string balanceMeaning: ""
            property int flagCount: 0
            background: CustomerCard { border.color: theme.goldDark }
            header: CustomerLabel {
                value: customerStatusDialog.deactivating ? "تأكيد تعطيل العميل" : "تأكيد إعادة التفعيل"
                padding: 16
                color: theme.gold
                font.pixelSize: 18
                font.bold: true
            }
            contentItem: ColumnLayout {
                spacing: 12
                CustomerLabel {
                    Layout.fillWidth: true
                    value: customerStatusDialog.customerName + " — رقم " + customerStatusDialog.customerId
                    font.bold: true
                }
                CustomerLabel {
                    Layout.fillWidth: true
                    visible: customerStatusDialog.deactivating && customerStatusDialog.hasBalance
                    value: "تحذير: لدى العميل رصيد غير صفري: " + customerStatusDialog.balanceText + " — " + customerStatusDialog.balanceMeaning
                    color: theme.amber
                }
                CustomerLabel {
                    Layout.fillWidth: true
                    visible: customerStatusDialog.deactivating && customerStatusDialog.flagCount > 0
                    value: "تحذير: لدى العميل " + customerStatusDialog.flagCount + " تحذير نشط."
                    color: theme.amber
                }
                CustomerLabel {
                    Layout.fillWidth: true
                    value: customerStatusDialog.deactivating
                           ? "يمكن تعطيل العميل مع وجود رصيد أو تحذيرات. ستبقى بياناته ورصيده وجميع حركاته محفوظة. هل تريد المتابعة؟"
                           : "سيصبح العميل نشطاً مرة أخرى. هل تريد المتابعة؟"
                }
                CustomerMessageBox {
                    message: customerVM.actionMessage
                    success: customerVM.actionSuccess
                }
            }
            footer: RowLayout {
                spacing: 10
                CustomerButton {
                    Layout.leftMargin: 16
                    Layout.bottomMargin: 12
                    text: "إلغاء"
                    enabled: !customerVM.busy
                    onClicked: customerStatusDialog.close()
                }
                Item { Layout.fillWidth: true }
                CustomerButton {
                    objectName: "confirmCustomerStatusButton"
                    Layout.rightMargin: 16
                    Layout.bottomMargin: 12
                    text: customerStatusDialog.deactivating ? "تأكيد التعطيل" : "إعادة التفعيل"
                    enabled: !customerVM.busy
                    onClicked: {
                        var saved = customerStatusDialog.deactivating
                                ? customerVM.deactivateCustomer(customerStatusDialog.customerId)
                                : customerVM.reactivateCustomer(customerStatusDialog.customerId)
                        if (saved)
                            customerStatusDialog.close()
                    }
                }
            }
        }

        // تحويل إلى رصيد مشطوب: amount (default = full balance, editable) → review → password → تأكيد.
        Dialog {
            id: writeOffDialog
            objectName: "writeOffDialog"
            parent: Overlay.overlay
            anchors.centerIn: parent
            width: Math.min(600, root.width - 60)
            modal: true
            closePolicy: customerVM.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
            property int step: 0                 // 0 = amount + reason, 1 = review + password
            readonly property var preview: customerVM.writeOffPreview(writeOffAmountRow.text)
            function begin() {
                step = 0
                writeOffAmountRow.text = customerVM.writeOff.defaultAmount || ""
                writeOffReasonRow.text = ""
                writeOffPassword.text = ""
                open()
            }
            onClosed: { writeOffPassword.text = ""; customerVM.closeWriteOff() }
            background: CustomerCard { border.color: theme.amber }
            header: CustomerLabel {
                value: "⛔ تحويل إلى رصيد مشطوب"
                padding: 16
                color: theme.amber
                font.pixelSize: 18
                font.bold: true
            }
            contentItem: ColumnLayout {
                spacing: 10
                CustomerDetail { Layout.fillWidth: true; caption: "العميل"; value: customerVM.writeOff.name || "" }
                CustomerDetail {
                    objectName: "writeOffBalance"
                    Layout.fillWidth: true; caption: "على العميل"; value: customerVM.writeOff.balanceText || ""
                }
                CustomerFormRow {
                    id: writeOffAmountRow
                    objectName: "writeOffAmount"
                    visible: writeOffDialog.step === 0
                    Layout.fillWidth: true
                    caption: "مبلغ الشطب *"
                }
                CustomerDetail {
                    objectName: "writeOffAfter"
                    Layout.fillWidth: true; caption: "المتبقي بعد الشطب"
                    value: writeOffDialog.preview.valid ? writeOffDialog.preview.afterText : "—"
                }
                CustomerFormRow {
                    id: writeOffReasonRow
                    objectName: "writeOffReason"
                    visible: writeOffDialog.step === 0
                    Layout.fillWidth: true
                    caption: "سبب شطب الرصيد *"
                    maximumLength: 1000
                }
                CustomerLabel {
                    Layout.fillWidth: true
                    visible: writeOffDialog.step === 0
                    value: "هذه العملية لا تحذف الفواتير أو التاريخ. سيتم تخفيض الرصيد النشط للعميل وتسجيل المبلغ في سجل الرصيد المشطوب."
                    color: theme.textSecondary
                    font.pixelSize: 11
                }
                CustomerLabel {
                    objectName: "writeOffConfirmText"
                    Layout.fillWidth: true
                    visible: writeOffDialog.step === 1
                    value: writeOffDialog.preview.confirmText || ""
                    color: theme.amber
                    font.bold: true
                }
                RowLayout {
                    visible: writeOffDialog.step === 1
                    Layout.fillWidth: true
                    spacing: 12
                    CustomerField {
                        id: writeOffPassword
                        objectName: "writeOffPassword"
                        Layout.fillWidth: true
                        echoMode: TextInput.Password
                        placeholderText: "كلمة مرورك الحالية"
                    }
                    CustomerLabel { Layout.preferredWidth: 100; value: "كلمة المرور *"; color: theme.textSecondary }
                }
                CustomerLabel {
                    Layout.fillWidth: true
                    visible: writeOffDialog.step === 0 && writeOffAmountRow.text.length > 0 && !writeOffDialog.preview.valid
                    value: writeOffDialog.preview.message || ""
                    color: theme.red
                }
                CustomerMessageBox {
                    visible: !customerVM.actionSuccess && customerVM.actionMessage.length > 0
                    message: customerVM.actionMessage
                    success: false
                }
            }
            footer: RowLayout {
                spacing: 10
                CustomerButton {
                    Layout.leftMargin: 16
                    Layout.bottomMargin: 12
                    text: writeOffDialog.step === 1 ? "رجوع" : "إلغاء"
                    enabled: !customerVM.busy
                    onClicked: writeOffDialog.step === 1 ? writeOffDialog.step = 0 : writeOffDialog.close()
                }
                Item { Layout.fillWidth: true }
                CustomerButton {
                    objectName: "writeOffNext"
                    visible: writeOffDialog.step === 0
                    Layout.rightMargin: 16
                    Layout.bottomMargin: 12
                    text: "متابعة"
                    enabled: writeOffDialog.preview.valid && writeOffReasonRow.text.trim().length > 0
                    onClicked: writeOffDialog.step = 1
                }
                CustomerButton {
                    objectName: "writeOffConfirm"
                    visible: writeOffDialog.step === 1
                    Layout.rightMargin: 16
                    Layout.bottomMargin: 12
                    text: customerVM.busy ? "جارٍ التنفيذ…" : "تأكيد الشطب"
                    enabled: !customerVM.busy && writeOffPassword.text.length > 0
                    onClicked: if (customerVM.submitWriteOff(writeOffAmountRow.text, writeOffReasonRow.text, writeOffPassword.text))
                                   writeOffDialog.close()
                                else writeOffPassword.text = ""
                }
            }
        }

        // عكس الشطب: a mistake is reversed in full (reason + password); the write-off stays as معكوس.
        Dialog {
            id: reverseDeadCashDialog
            objectName: "reverseDeadCashDialog"
            parent: Overlay.overlay
            anchors.centerIn: parent
            width: Math.min(560, root.width - 60)
            modal: true
            closePolicy: customerVM.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
            property string deadCashId: ""
            property string reference: ""
            property string amountText: ""
            function begin(id, reference, amountText) {
                deadCashId = id; reverseDeadCashDialog.reference = reference
                reverseDeadCashDialog.amountText = amountText
                reverseReasonRow.text = ""; reversePassword.text = ""
                open()
            }
            onClosed: reversePassword.text = ""
            background: CustomerCard { border.color: theme.goldDark }
            header: CustomerLabel {
                value: "عكس الشطب " + reverseDeadCashDialog.reference
                padding: 16
                color: theme.gold
                font.pixelSize: 18
                font.bold: true
            }
            contentItem: ColumnLayout {
                spacing: 10
                CustomerLabel {
                    Layout.fillWidth: true
                    value: "سيعود مبلغ " + reverseDeadCashDialog.amountText + " ج إلى رصيد العميل. يبقى سجل الشطب محفوظاً بحالة «معكوس»."
                    color: theme.amber
                }
                CustomerFormRow { id: reverseReasonRow; objectName: "reverseDeadCashReason"; Layout.fillWidth: true; caption: "سبب العكس *"; maximumLength: 1000 }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    CustomerField {
                        id: reversePassword
                        objectName: "reverseDeadCashPassword"
                        Layout.fillWidth: true
                        echoMode: TextInput.Password
                        placeholderText: "كلمة مرورك الحالية"
                    }
                    CustomerLabel { Layout.preferredWidth: 100; value: "كلمة المرور *"; color: theme.textSecondary }
                }
                CustomerMessageBox {
                    visible: !customerVM.actionSuccess && customerVM.actionMessage.length > 0
                    message: customerVM.actionMessage
                    success: false
                }
            }
            footer: RowLayout {
                spacing: 10
                CustomerButton {
                    Layout.leftMargin: 16
                    Layout.bottomMargin: 12
                    text: "إلغاء"
                    enabled: !customerVM.busy
                    onClicked: reverseDeadCashDialog.close()
                }
                Item { Layout.fillWidth: true }
                CustomerButton {
                    objectName: "reverseDeadCashConfirm"
                    Layout.rightMargin: 16
                    Layout.bottomMargin: 12
                    text: "تأكيد العكس"
                    enabled: !customerVM.busy && reverseReasonRow.text.trim().length > 0 && reversePassword.text.length > 0
                    onClicked: if (customerVM.reverseDeadCash(reverseDeadCashDialog.deadCashId, reverseReasonRow.text,
                                                              reversePassword.text))
                                   reverseDeadCashDialog.close()
                               else reversePassword.text = ""
                }
            }
        }
    }

    // CUSTOMERS V1 END

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

    component SettingsSectionCard: Rectangle {
        id: sectionCard
        property string sectionTitle: ""
        property color accentColor: theme.gold
        property alias content: sectionBody.data
        implicitHeight: sectionLayout.implicitHeight + 28
        color: theme.bgSecondary
        radius: theme.radiusCard
        border.color: theme.cardBorder
        border.width: 1
        Rectangle {
            anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
            anchors.leftMargin: 22; anchors.rightMargin: 22                       // the line stays inside the straight part of the rounded edge
            height: 3; radius: 2; color: sectionCard.accentColor
        }
        ColumnLayout {
            id: sectionLayout
            anchors.fill: parent; anchors.margins: 14; spacing: 10
            GoldPriceLabel {
                Layout.fillWidth: true; visible: sectionCard.sectionTitle.length > 0
                value: sectionCard.sectionTitle; color: sectionCard.accentColor
                font.pixelSize: 15; font.bold: true
            }
            ColumnLayout { id: sectionBody; Layout.fillWidth: true; spacing: 10 }
        }
    }

    // Settings → الطباعة: two independent printers of THIS workstation (local settings, not shop data).
    // Billing's حفظ وطباعة without a Bill printer → Settings (الطباعة is the first settings section).
    Connections {
        target: typeof invoiceVM !== "undefined" ? invoiceVM : null
        ignoreUnknownSignals: true
        function onPrintingSettingsRequested() { root.currentPage = 6 }
    }

    component PrinterChoice: SettingsSectionCard {
        id: printerChoice
        property string kind: "bill"                 // bill | label
        readonly property var vm: typeof printerSettingsVM !== "undefined" ? printerSettingsVM : null
        readonly property var choice: vm ? (kind === "bill" ? vm.billPrinter : vm.labelPrinter) : ({})
        color: theme.bgTertiary
        accentColor: kind === "bill" ? theme.gold : theme.blue
        sectionTitle: kind === "bill" ? "طابعة الفواتير" : "طابعة الباركود / الليبل"
        content: [
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 10
                GoldPriceLabel { value: "الطابعة"; color: theme.textSecondary }
                ComboBox {
                    id: printerCombo
                    objectName: printerChoice.kind === "bill" ? "billPrinterCombo" : "labelPrinterCombo"
                    Layout.preferredWidth: 300
                    font.family: "Cairo"
                    model: printerChoice.vm ? printerChoice.vm.printers : []
                    currentIndex: model.indexOf ? model.indexOf(printerChoice.choice.name || "") : -1
                    displayText: currentIndex >= 0 ? currentText
                                 : (printerChoice.choice.name ? printerChoice.choice.name + " (غير متاحة)" : "اختر الطابعة")
                    onActivated: function(index) {
                        if (printerChoice.kind === "bill")
                            printerChoice.vm.setBillPrinter(model[index])
                        else
                            printerChoice.vm.setLabelPrinter(model[index])
                    }
                }
                GoldPriceLabel {
                    objectName: printerChoice.kind === "bill" ? "billPrinterStatus" : "labelPrinterStatus"
                    value: !printerChoice.choice.name ? "لم تُحدد" : printerChoice.choice.status || ""
                    color: printerChoice.choice.available ? theme.green : theme.amber
                    font.bold: true
                }
                Item { Layout.fillWidth: true }
            },
            GoldPriceLabel {
                Layout.fillWidth: true
                value: "الورق: " + (printerChoice.vm ? (printerChoice.kind === "bill" ? printerChoice.vm.billFormat
                                                                                    : printerChoice.vm.labelFormat) : "")
                color: theme.textMuted; font.pixelSize: 11
            },
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                GoldPriceButton {
                    objectName: printerChoice.kind === "bill" ? "testBillPrint" : "testLabelPrint"
                    text: printerChoice.kind === "bill" ? (printerChoice.vm && printerChoice.vm.billTestBusy
                                                           ? "جاري إرسال الطباعة..." : "طباعة فاتورة تجريبية")
                                                        : "طباعة ليبل تجريبي"
                    enabled: !!printerChoice.choice.available
                             && !(printerChoice.kind === "bill" && printerChoice.vm && printerChoice.vm.billTestBusy)
                    onClicked: printerChoice.kind === "bill" ? printerChoice.vm.testBillPrint()
                                                             : printerChoice.vm.testLabelPrint()
                }
                Item { Layout.fillWidth: true }
            }
        ]
    }

    component PrintingSettings: SettingsSectionCard {
        id: printing
        objectName: "settingsPrinting"
        sectionTitle: "الطباعة"
        accentColor: theme.gold
        readonly property var vm: typeof printerSettingsVM !== "undefined" ? printerSettingsVM : null
        content: [
            GoldPriceLabel {
                Layout.fillWidth: true
                value: "اختيار الطابعة خاص بهذا الجهاز؛ طابعة الفواتير مستقلة تماماً عن طابعة الباركود."
                color: theme.textMuted; font.pixelSize: 11
            },
            PrinterChoice { kind: "bill"; Layout.fillWidth: true },
            PrinterChoice { kind: "label"; Layout.fillWidth: true },
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                GoldPriceLabel {
                    objectName: "printingMessage"
                    Layout.fillWidth: true
                    visible: value.length > 0
                    value: printing.vm ? printing.vm.message : ""
                    color: printing.vm && printing.vm.messageIsError ? theme.priceError : theme.green
                }
                GoldPriceButton {
                    objectName: "refreshPrinters"
                    text: "تحديث قائمة الطابعات"
                    onClicked: printing.vm.refreshPrinters()
                }
            }
        ]
    }

    // Settings → الرصيد الافتتاحي: the owner's initial gold / cash balance, entered ONCE (the gold profit report starts from it), plus a manual stocktaking sheet.
    component PositionOpeningSettings: SettingsSectionCard {
        id: opening
        objectName: "settingsPositionOpening"
        sectionTitle: "الرصيد الافتتاحي وجرد الذهب"
        accentColor: theme.gold
        readonly property var vm: typeof positionSnapshotVM !== "undefined" ? positionSnapshotVM : null
        visible: !!vm && vm.canManage
        Component.onCompleted: if (vm && vm.canManage) vm.refresh()
        content: [
            GoldPriceLabel {
                Layout.fillWidth: true; wrapMode: Text.WordWrap
                value: "يُسجَّل مرة واحدة عند بدء التشغيل الفعلي. ذهب المحل = مخزون + كسر + سبائك بمكافئ عيار 21، وذهب الموردين بالإشارة (موجب = لنا عندهم، سالب = علينا). بعد التسجيل لا يتغير، ويأخذ البرنامج جردًا تلقائيًا كل يوم."
                color: theme.textMuted; font.pixelSize: 11
            },
            GoldPriceLabel {
                objectName: "openingRecorded"; Layout.fillWidth: true; wrapMode: Text.WordWrap
                visible: !!opening.vm && opening.vm.hasOpening
                value: opening.vm ? opening.vm.openingText : ""
                color: theme.green
            },
            GridLayout {
                objectName: "openingFields"
                visible: !!opening.vm && opening.vm.loaded && !opening.vm.hasOpening
                columns: 2; layoutDirection: Qt.RightToLeft
                Layout.fillWidth: true; columnSpacing: 12; rowSpacing: 8
                enabled: !!opening.vm && !opening.vm.busy
                GoldPriceLabel { Layout.preferredWidth: 150; value: "ذهب المحل (جرام مكافئ 21)" }
                GoldPriceInput { id: openingGold; objectName: "openingGold"; Layout.fillWidth: true; Layout.maximumWidth: 300; Layout.preferredHeight: 34 }
                GoldPriceLabel { Layout.preferredWidth: 150; value: "ذهب الموردين صافي (+ لنا / − علينا)" }
                GoldPriceInput { id: openingSuppliers; objectName: "openingSuppliers"; Layout.fillWidth: true; Layout.maximumWidth: 300; Layout.preferredHeight: 34 }
                GoldPriceLabel { Layout.preferredWidth: 150; value: "الكاش (الخزنة) ج" }
                GoldPriceInput { id: openingCash; objectName: "openingCash"; Layout.fillWidth: true; Layout.maximumWidth: 300; Layout.preferredHeight: 34 }
                GoldPriceLabel { Layout.preferredWidth: 150; value: "البنوك والمحافظ ج" }
                GoldPriceInput { id: openingBank; objectName: "openingBank"; Layout.fillWidth: true; Layout.maximumWidth: 300; Layout.preferredHeight: 34 }
            },
            CheckBox {
                id: openingConfirm; objectName: "openingConfirm"
                visible: !!opening.vm && opening.vm.loaded && !opening.vm.hasOpening
                text: "أؤكد أن الأرقام صحيحة وأنها لا تتغير بعد التسجيل"
                font.family: "Cairo"; font.pixelSize: 12
                contentItem: Text { text: openingConfirm.text; font: openingConfirm.font; color: theme.textPrimary; horizontalAlignment: Text.AlignRight; leftPadding: 4; rightPadding: openingConfirm.indicator.width + 6; verticalAlignment: Text.AlignVCenter }
            },
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                GoldPriceLabel {
                    objectName: "openingMessage"; Layout.fillWidth: true; visible: value.length > 0
                    value: opening.vm ? opening.vm.message : ""
                    color: opening.vm && opening.vm.messageIsError ? theme.priceError : theme.green
                }
                GoldPriceButton {
                    objectName: "openingSave"; text: "تسجيل الرصيد الافتتاحي"
                    visible: !!opening.vm && opening.vm.loaded && !opening.vm.hasOpening
                    enabled: openingConfirm.checked && openingGold.text.length > 0 && !!opening.vm && !opening.vm.busy
                    onClicked: opening.vm.saveOpening(openingGold.text, openingSuppliers.text, openingCash.text, openingBank.text)
                }
                GoldPriceButton {
                    objectName: "openingTakeNow"; text: "جرد الآن"
                    visible: !!opening.vm && opening.vm.hasOpening
                    enabled: !!opening.vm && !opening.vm.busy
                    onClicked: opening.vm.takeNow()
                }
            }
        ]
    }

    // Settings → تصنيفات المصروفات والإيرادات: the categories of the treasury's «حركة نقدية جديدة» (add, rename, disable / enable, delete while unused).
    component CashCategorySettings: SettingsSectionCard {
        id: categories
        objectName: "settingsCashCategories"
        sectionTitle: "تصنيفات المصروفات والإيرادات"
        accentColor: theme.gold
        readonly property var vm: typeof cashCategoryVM !== "undefined" ? cashCategoryVM : null
        property string editing: ""          // the code whose name is being edited
        property string confirmDelete: ""    // the code whose deletion awaits the second click
        visible: !!vm && vm.canManage
        Component.onCompleted: if (vm && vm.canManage) vm.refresh()
        content: [
            GoldPriceLabel {
                Layout.fillWidth: true; wrapMode: Text.WordWrap
                value: "تظهر هذه التصنيفات في «حركة نقدية جديدة» بالخزنة. التصنيف المستخدم في عمليات سابقة لا يُحذف، يمكن تعطيله فقط (فيختفي من القائمة ويبقى اسمه في السجل)."
                color: theme.textMuted; font.pixelSize: 11
            },
            Repeater {
                model: categories.vm ? categories.vm.categories : []
                delegate: RowLayout {
                    required property var modelData
                    objectName: "categoryRow_" + modelData.code
                    Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                    GoldPriceLabel {
                        Layout.preferredWidth: 190; visible: categories.editing !== modelData.code
                        value: modelData.name; color: modelData.active ? theme.textPrimary : theme.textMuted
                        objectName: "categoryName_" + modelData.code
                    }
                    GoldPriceInput {
                        id: renameField; objectName: "categoryRenameField_" + modelData.code
                        Layout.preferredWidth: 190; Layout.preferredHeight: 34
                        visible: categories.editing === modelData.code; text: modelData.name
                        placeholderText: ""
                    }
                    GoldPriceLabel { Layout.preferredWidth: 80; value: modelData.direction_text; color: theme.textSecondary }
                    GoldPriceLabel { Layout.preferredWidth: 70; value: modelData.active ? "نشط" : "معطّل"; color: modelData.active ? theme.green : theme.textMuted }
                    Item { Layout.fillWidth: true }
                    GoldPriceButton {
                        objectName: "categoryEdit_" + modelData.code; visible: categories.editing !== modelData.code
                        text: "تعديل الاسم"; enabled: !categories.vm.busy
                        onClicked: { categories.confirmDelete = ""; categories.editing = modelData.code }
                    }
                    GoldPriceButton {
                        objectName: "categorySave_" + modelData.code; visible: categories.editing === modelData.code
                        text: "حفظ الاسم"; enabled: !categories.vm.busy && renameField.text.trim().length > 0
                        onClicked: { categories.vm.rename(modelData.code, renameField.text); categories.editing = "" }
                    }
                    GoldPriceButton {
                        objectName: "categoryToggle_" + modelData.code
                        text: modelData.active ? "تعطيل" : "تفعيل"; enabled: !categories.vm.busy
                        onClicked: categories.vm.setActive(modelData.code, !modelData.active)
                    }
                    GoldPriceButton {
                        objectName: "categoryDelete_" + modelData.code
                        visible: !modelData.used
                        text: categories.confirmDelete === modelData.code ? "تأكيد الحذف" : "حذف"; enabled: !categories.vm.busy
                        onClicked: {
                            if (categories.confirmDelete === modelData.code) { categories.confirmDelete = ""; categories.vm.remove(modelData.code) }
                            else { categories.editing = ""; categories.confirmDelete = modelData.code }
                        }
                    }
                }
            },
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                enabled: !!categories.vm && !categories.vm.busy
                GoldPriceInput { id: cashCategoryNewName; objectName: "cashCategoryNewName"; Layout.preferredWidth: 220; Layout.preferredHeight: 34; placeholderText: "اسم تصنيف جديد" }
                ComboBox {
                    id: cashCategoryNewDirection; objectName: "cashCategoryNewDirection"; Layout.preferredWidth: 130; Layout.preferredHeight: 34
                    model: [{ text: "خرج (مصروف)", value: "OUT" }, { text: "دخل (إيراد)", value: "IN" }, { text: "الاتجاهين", value: "BOTH" }]
                    textRole: "text"; font.family: "Cairo"
                }
                GoldPriceButton {
                    objectName: "cashCategoryAdd"; text: "إضافة تصنيف"; enabled: cashCategoryNewName.text.trim().length > 0
                    onClicked: { categories.vm.add(cashCategoryNewName.text, cashCategoryNewDirection.model[cashCategoryNewDirection.currentIndex].value); cashCategoryNewName.text = "" }
                }
                Item { Layout.fillWidth: true }
            },
            GoldPriceLabel {
                objectName: "categoryMessage"; Layout.fillWidth: true; visible: value.length > 0
                value: categories.vm ? categories.vm.message : ""
                color: categories.vm && categories.vm.messageIsError ? theme.priceError : theme.green
            }
        ]
    }

    // Settings → تكلفة المصنعية للحروف: the owner's actual making cost per gram of each category (one number for every karat), frozen into a piece at stock entry.
    component CategoryCostSettings: SettingsSectionCard {
        id: costs
        objectName: "settingsCategoryCosts"
        sectionTitle: "تكلفة المصنعية للحروف"
        accentColor: theme.gold
        readonly property var vm: typeof categoryCostVM !== "undefined" ? categoryCostVM : null
        visible: !!vm && vm.canManage
        Component.onCompleted: if (vm && vm.canManage) vm.refresh()
        content: [
            GoldPriceLabel {
                Layout.fillWidth: true; wrapMode: Text.WordWrap
                value: "التكلفة الفعلية لمصنعية الجرام لكل حرف (رقم واحد لكل الأعيار). تُثبَّت في القطعة وقت إدخالها، فتغييرها لا يغيّر القطع القديمة. القطع القديمة بدون تكلفة تُقدَّر بالتكلفة الحالية في «ربح المصنعية»."
                color: theme.textMuted; font.pixelSize: 11
            },
            Repeater {
                model: costs.vm ? costs.vm.categories : []
                delegate: RowLayout {
                    required property var modelData
                    objectName: "costRow_" + modelData.code
                    Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                    GoldPriceLabel { Layout.preferredWidth: 60; value: modelData.code; font.bold: true; color: theme.gold }
                    GoldPriceInput {
                        id: costField; objectName: "costField_" + modelData.code
                        Layout.preferredWidth: 130; Layout.preferredHeight: 34
                        text: modelData.cost; placeholderText: "غير محدد"
                    }
                    GoldPriceLabel { Layout.preferredWidth: 60; value: "ج/جرام"; color: theme.textSecondary }
                    GoldPriceLabel { Layout.fillWidth: true; value: modelData.range; color: theme.textMuted; font.pixelSize: 11 }
                    GoldPriceButton {
                        objectName: "costSave_" + modelData.code; text: "حفظ"
                        enabled: !costs.vm.busy && costField.text.trim().length > 0
                        onClicked: costs.vm.setCost(modelData.id, costField.text)
                    }
                    GoldPriceButton {
                        objectName: "costClear_" + modelData.code; text: "مسح"; visible: modelData.hasCost
                        enabled: !costs.vm.busy
                        onClicked: costs.vm.clearCost(modelData.id)
                    }
                }
            },
            GoldPriceLabel {
                objectName: "costMessage"; Layout.fillWidth: true; visible: value.length > 0
                value: costs.vm ? costs.vm.message : ""
                color: costs.vm && costs.vm.messageIsError ? theme.priceError : theme.green
            }
        ]
    }

    // Settings → الحد الأدنى للمخزون: how many pieces / grams of an item type and karat the owner wants to keep (the inventory alerts list what is below).
    component StockMinLevelSettings: SettingsSectionCard {
        id: levels
        objectName: "settingsStockMinLevels"
        sectionTitle: "الحد الأدنى للمخزون"
        accentColor: theme.gold
        readonly property var vm: typeof stockMinLevelVM !== "undefined" ? stockMinLevelVM : null
        visible: !!vm && vm.canManage
        Component.onCompleted: if (vm && vm.canManage) vm.refresh()
        content: [
            GoldPriceLabel {
                Layout.fillWidth: true; wrapMode: Text.WordWrap
                value: "حدد لكل صنف وعيار أقل كمية تريد الاحتفاظ بها (قطع و/أو جرامات). تظهر الأصناف الأقل من حدها في «تنبيهات المخزون». لا يُطلب شيء تلقائيًا."
                color: theme.textMuted; font.pixelSize: 11
            },
            Repeater {
                model: levels.vm ? levels.vm.levels : []
                delegate: RowLayout {
                    required property var modelData
                    objectName: "levelRow_" + modelData.id
                    Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                    GoldPriceLabel { Layout.preferredWidth: 150; value: modelData.type + " — عيار " + modelData.karat; font.bold: true }
                    GoldPriceLabel { Layout.preferredWidth: 160; value: "الحد: " + modelData.minimum; color: theme.textSecondary }
                    GoldPriceLabel { Layout.fillWidth: true; value: "الموجود: " + modelData.have; color: modelData.below ? theme.priceError : theme.green }
                    GoldPriceButton { objectName: "levelDelete_" + modelData.id; text: "حذف"; enabled: !levels.vm.busy; onClicked: levels.vm.remove(modelData.id) }
                }
            },
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                enabled: !!levels.vm && !levels.vm.busy
                ComboBox {
                    id: levelType; objectName: "levelType"; Layout.preferredWidth: 160; Layout.preferredHeight: 34
                    model: levels.vm ? levels.vm.itemTypes : []; textRole: "name"; font.family: "Cairo"
                }
                ComboBox {
                    id: levelKarat; objectName: "levelKarat"; Layout.preferredWidth: 110; Layout.preferredHeight: 34
                    model: levels.vm ? levels.vm.karats : []; textRole: "name"; font.family: "Cairo"
                }
                GoldPriceInput { id: levelPieces; objectName: "levelPieces"; Layout.preferredWidth: 100; Layout.preferredHeight: 34; placeholderText: "الحد بالقطع" }
                GoldPriceInput { id: levelGrams; objectName: "levelGrams"; Layout.preferredWidth: 110; Layout.preferredHeight: 34; placeholderText: "الحد بالجرام" }
                GoldPriceButton {
                    objectName: "levelSave"; text: "حفظ الحد"
                    enabled: levelType.currentIndex >= 0 && levelKarat.currentIndex >= 0 && (levelPieces.text.trim().length > 0 || levelGrams.text.trim().length > 0)
                    onClicked: { levels.vm.save(levels.vm.itemTypes[levelType.currentIndex].id, levels.vm.karats[levelKarat.currentIndex].id, levelPieces.text, levelGrams.text); levelPieces.text = ""; levelGrams.text = "" }
                }
                Item { Layout.fillWidth: true }
            },
            GoldPriceLabel {
                objectName: "levelMessage"; Layout.fillWidth: true; visible: value.length > 0
                value: levels.vm ? levels.vm.message : ""
                color: levels.vm && levels.vm.messageIsError ? theme.priceError : theme.green
            }
        ]
    }

    component GoldProviderSettings: SettingsSectionCard {
        property alias resultCard: testResultCard
        id: providerPanel
        objectName: "goldProviderSettings"
        property var vm: typeof goldSettingsVM !== "undefined" ? goldSettingsVM : null
        sectionTitle: "إعدادات مصدر أسعار الذهب"
        onVisibleChanged: {
            providerKey.clear()
            if (visible && vm && vm.canManage) vm.load()
        }
        Component.onCompleted: { if (visible && vm && vm.canManage) vm.load() }
        Connections {
            target: providerPanel.vm
            function onClearSecret() { providerKey.clear() }
            function onLoaded() {
                providerUrl.text = providerPanel.vm.data.base_url || "https://goldtimo.com/api/v1"
                providerTimeout.text = providerPanel.vm.data.timeout || "10"
                providerRefresh.text = providerPanel.vm.data.refresh || "60"
                providerStale.text = providerPanel.vm.data.stale || "300"
            }
        }
        content: [
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 12
                GoldPriceLabel { value: "حالة المفتاح"; color: theme.textMuted }
                GoldPriceLabel {
                    objectName: "providerKeyStatus"; Layout.fillWidth: true
                    value: !providerPanel.vm || !providerPanel.vm.canManage ? "يتطلب صلاحية الإدارة" :
                        providerPanel.vm.data.stored ? "مفتاح API محفوظ" :
                        providerPanel.vm.data.configured ? "مُعد من بيئة التطوير" : "لم يُحفظ مفتاح API"
                    color: providerPanel.vm && providerPanel.vm.data.stored ? theme.green : theme.textSecondary
                }
            },
            GridLayout {
                id: providerFields
                objectName: "providerFields"
                columns: providerPanel.width >= 760 ? 4 : 2
                layoutDirection: Qt.RightToLeft
                Layout.fillWidth: true; Layout.maximumWidth: Number.POSITIVE_INFINITY
                Layout.alignment: Qt.AlignRight; columnSpacing: 12; rowSpacing: 8
                enabled: providerPanel.vm && providerPanel.vm.canManage && !providerPanel.vm.busy
                GoldPriceLabel { Layout.preferredWidth: 112; value: "المصدر التلقائي" }
                ComboBox {
                    objectName: "providerSource"; Layout.fillWidth: true; Layout.maximumWidth: 300
                    Layout.preferredHeight: 34; model: ["GoldTimo"]; font.family: "Cairo"
                    contentItem: GoldPriceLabel { value: "GoldTimo"; rightPadding: 28 }
                    background: Rectangle { color: theme.bgTertiary; radius: theme.radiusSmall; border.color: theme.border }
                }
                GoldPriceLabel { Layout.preferredWidth: 112; value: "رابط الخدمة" }
                GoldPriceInput {
                    id: providerUrl; objectName: "providerUrl"; Layout.fillWidth: true
                    Layout.maximumWidth: 300; Layout.preferredHeight: 34
                    font.pixelSize: 11; inputMethodHints: Qt.ImhUrlCharactersOnly
                    text: "https://goldtimo.com/api/v1"
                }
                GoldPriceLabel { Layout.preferredWidth: 112; value: "مهلة الاتصال / ث" }
                GoldPriceInput { id: providerTimeout; objectName: "providerTimeout"; Layout.fillWidth: true; Layout.maximumWidth: 300; Layout.preferredHeight: 34; text: "10" }
                GoldPriceLabel { Layout.preferredWidth: 112; value: "فترة التحديث / ث" }
                GoldPriceInput { id: providerRefresh; objectName: "providerRefresh"; Layout.fillWidth: true; Layout.maximumWidth: 300; Layout.preferredHeight: 34; text: "60" }
                GoldPriceLabel { Layout.preferredWidth: 112; value: "حد قِدم السعر / ث" }
                GoldPriceInput { id: providerStale; objectName: "providerStale"; Layout.fillWidth: true; Layout.maximumWidth: 300; Layout.preferredHeight: 34; text: "300" }
                GoldPriceLabel { Layout.preferredWidth: 112; value: "مفتاح API" }
                GoldPriceInput {
                    id: providerKey; objectName: "providerApiKey"; Layout.fillWidth: true
                    Layout.maximumWidth: 300; Layout.preferredHeight: 34
                    echoMode: TextInput.Password
                    inputMethodHints: Qt.ImhSensitiveData | Qt.ImhNoPredictiveText
                    placeholderText: text.length ? "" : "أدخل مفتاحاً جديداً لتغييره"
                }
            },
            GoldPriceLabel {
                Layout.fillWidth: true; font.pixelSize: 11; color: theme.textMuted
                value: "الحفظ يحدد GoldTimo كمصدر أساسي ويحافظ على الوضع الحالي. اختبار الاتصال لا يعتمد أي أسعار."
            },
            Flow {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                enabled: providerPanel.vm && providerPanel.vm.canManage && !providerPanel.vm.busy
                GoldPriceButton {
                    objectName: "testProviderConnection"; text: "اختبار الاتصال"
                    onClicked: providerPanel.vm.test(providerUrl.text, providerTimeout.text, providerKey.text)
                }
                GoldPriceButton {
                    objectName: "saveProviderSettings"; text: "حفظ الإعدادات"; primaryAction: true
                    onClicked: providerPanel.vm.save(providerUrl.text, providerTimeout.text, providerRefresh.text, providerStale.text, providerKey.text)
                }
                GoldPriceButton { objectName: "deleteProviderKey"; text: "حذف مفتاح API"; onClicked: providerPanel.vm.delete() }
            },
            GoldPriceLabel {
                Layout.fillWidth: true; visible: value.length > 0
                value: providerPanel.vm && providerPanel.vm.busy ? "جارٍ تنفيذ العملية…" : ""
                color: theme.textMuted
            },
            GoldPriceLabel {
                objectName: "providerFeedback"; Layout.fillWidth: true
                visible: value.length > 0 && !(providerPanel.vm && providerPanel.vm.result.source)
                value: providerPanel.vm ? providerPanel.vm.message : ""
                color: providerPanel.vm && providerPanel.vm.isError ? theme.priceError : theme.green
            },
            SettingsSectionCard {
                id: testResultCard
                objectName: "providerTestResult"; Layout.fillWidth: true
                readonly property var testResult: providerPanel.vm ? providerPanel.vm.result : ({})
                visible: !!testResult.source
                sectionTitle: testResult.title_text || ""
                accentColor: testResult.passed === false ? theme.priceError : theme.green
                color: theme.bgTertiary
                content: [
                    GoldPriceLabel {
                        objectName: "providerTestError"; Layout.fillWidth: true
                        visible: value.length > 0; color: theme.priceError
                        value: testResultCard.testResult.passed === false ? (testResultCard.testResult.error || "") : ""
                    },
                    GoldPriceLabel { Layout.fillWidth: true; value: "GoldTimo"; color: theme.textSecondary },
                    GoldPriceGrid {
                        objectName: "providerTestPrices"; Layout.fillWidth: true
                        Layout.maximumWidth: 460; Layout.alignment: Qt.AlignRight
                        priceRows: testResultCard.testResult.display_prices || []
                    },
                    GoldPriceLabel {
                        objectName: "providerTestTimestamp"; Layout.fillWidth: true; font.pixelSize: 11; color: theme.textMuted
                        value: "آخر تحديث: " + (testResultCard.testResult.provider_time_text || "—")
                    },
                    GoldPriceLabel {
                        objectName: "providerTestSkew"; Layout.fillWidth: true; font.pixelSize: 11
                        color: testResultCard.testResult.passed === false ? theme.priceWarning : theme.textMuted
                        value: "فرق التوقيت بين الأسعار: " + (testResultCard.testResult.skew_text || "—") + " ثانية"
                    },
                    GoldPriceLabel {
                        objectName: "providerTestCredential"; Layout.fillWidth: true; font.pixelSize: 11; color: theme.textMuted
                        value: "مصدر المفتاح: " + (testResultCard.testResult.credential_source_text || "—")
                    }
                ]
            }
        ]
    }

    component SettingsPage: Item {
        property string pageTitle: ""
        Rectangle { anchors.fill: parent; color: theme.bgPrimary }
        onVisibleChanged: if (visible && typeof userAdminVM !== "undefined" && userAdminVM.canManage) userAdminVM.refresh()
        // The Test Connection result card is the last item on this page:
        // once the layout settles, scroll to the end so the whole card shows.
        Connections {
            target: typeof goldSettingsVM !== "undefined" ? goldSettingsVM : null
            function onTestCompleted() { settingsResultScroll.restart() }
        }
        // Billing's "إعدادات الطباعة": الطباعة is the last section, so scroll to the end.
        Connections {
            target: typeof invoiceVM !== "undefined" ? invoiceVM : null
            ignoreUnknownSignals: true
            function onPrintingSettingsRequested() { printingScroll.restart() }
        }
        Timer {
            id: settingsResultScroll
            interval: 50
            onTriggered: {
                // Bring the whole Test Connection result card into view (it is no longer the last card).
                var flick = settingsScroll.contentItem
                var card = goldProvider.resultCard
                var bottom = card.mapToItem(flick.contentItem, 0, card.height).y
                flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height, bottom - flick.height + 12))
            }
        }
        Timer {
            id: printingScroll
            interval: 60
            onTriggered: {
                var flick = settingsScroll.contentItem
                var top = printingSettings.mapToItem(flick.contentItem, 0, 0).y
                flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height, top - 8))
            }
        }
        ScrollView {
            id: settingsScroll; objectName: "settingsScroll"
            anchors.fill: parent; anchors.margins: 16; clip: true
            contentWidth: availableWidth
            contentHeight: settingsBody.implicitHeight
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                id: settingsBody
                width: Math.min(settingsScroll.availableWidth, 1180)
                x: settingsScroll.availableWidth - width
                spacing: 12
                GoldPriceLabel {
                    Layout.fillWidth: true; value: "الإعدادات والبيانات الأساسية"
                    color: theme.gold; font.pixelSize: 19; font.bold: true
                }
                GoldPriceLabel { Layout.fillWidth: true; value: "إدارة بيانات المحل ومصدر أسعار الذهب"; color: theme.textMuted }
                GridLayout {
                    Layout.fillWidth: true; columns: width < 520 ? 1 : (width < 700 ? 2 : 4)
                    layoutDirection: Qt.RightToLeft; columnSpacing: 10; rowSpacing: 10
                    SettingsCard {
                        objectName: "settingsCategories"
                        title: "الفئات"; value: masterDataVM.categoryCount.toString()
                        subtitle: masterDataVM.disabledCategoryCount > 0 ? masterDataVM.disabledCategoryCount + " فئة معطلة" : masterDataVM.categoryCodes
                        accentColor: theme.gold; clickable: true; Layout.preferredHeight: 92
                        onClicked: { masterDataVM.refresh(); root.currentPage = 9 }
                    }
                    SettingsCard {
                        objectName: "settingsFactories"
                        title: "المصانع"; value: masterDataVM.factoryCount.toString()
                        subtitle: masterDataVM.disabledFactoryCount > 0 ? masterDataVM.disabledFactoryCount + " مصنع معطل" : "إدارة المصانع"
                        accentColor: theme.blue; clickable: true; Layout.preferredHeight: 92
                        onClicked: { masterDataVM.refresh(); root.currentPage = 7 }
                    }
                    // Owner only (USERS_MANAGE, re-checked by the service on every call).
                    SettingsCard {
                        objectName: "settingsUsers"
                        visible: typeof userAdminVM !== "undefined" && userAdminVM.canManage
                        title: "المستخدمون والصلاحيات"; value: userAdminVM.users.length.toString()
                        subtitle: "المستخدمون وأدوارهم وصلاحيات كل دور"
                        accentColor: theme.accountReceivable; clickable: true; Layout.preferredHeight: 92
                        onClicked: { userAdminVM.refresh(); root.currentPage = 13 }
                    }
                    SettingsCard {
                        objectName: "settingsItemTypes"
                        title: "الأصناف"; value: masterDataVM.itemTypeCount.toString()
                        subtitle: masterDataVM.disabledItemTypeCount > 0 ? masterDataVM.disabledItemTypeCount + " صنف معطل" : "إدارة أنواع الأصناف"
                        accentColor: theme.green; clickable: true; Layout.preferredHeight: 92
                        onClicked: { masterDataVM.refresh(); root.currentPage = 8 }
                    }
                }
                SettingsSectionCard {
                    Layout.fillWidth: true
                    accentColor: masterDataVM.errorMessage.length > 0 ? theme.priceError : theme.green
                    content: RowLayout {
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 12
                        Rectangle { implicitWidth: 8; implicitHeight: 8; radius: 4; color: masterDataVM.errorMessage.length > 0 ? theme.priceError : theme.green }
                        GoldPriceLabel {
                            Layout.fillWidth: true
                            value: masterDataVM.errorMessage.length > 0 ? masterDataVM.errorMessage : "البيانات متصلة بقاعدة البيانات"
                            color: masterDataVM.errorMessage.length > 0 ? theme.priceError : theme.textMuted
                        }
                        GoldPriceButton { text: "تحديث"; onClicked: masterDataVM.refresh() }
                    }
                }
                GoldProviderSettings { id: goldProvider; Layout.fillWidth: true }
                PrintingSettings { id: printingSettings; Layout.fillWidth: true }
                PositionOpeningSettings { id: positionOpening; Layout.fillWidth: true }
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


            ActionMessageBox { scope: "factories"; objectName: "factoriesMessage" }


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
                                        !!modelData.notes
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

                            Rectangle {
                                Layout.preferredWidth: 1
                                Layout.preferredHeight: 46
                                color: theme.border
                                visible: factoriesPage.showDisabled
                            }

                            DisabledDeleteZone {
                                visible: factoriesPage.showDisabled
                                everUsed: modelData.ever_used !== false
                                buttonName: "deleteDisabledFactory"
                                blockedName: "deleteBlockedFactory"
                                onDeleteRequested: deleteMasterDialog.ask("factory", Number(modelData.id), modelData.name || "")
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


            ActionMessageBox { scope: "itemTypes"; objectName: "itemTypesMessage" }


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
                                        !!modelData.notes
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

                            Rectangle {
                                Layout.preferredWidth: 1
                                Layout.preferredHeight: 46
                                color: theme.border
                                visible: itemTypesPage.showDisabled
                            }

                            DisabledDeleteZone {
                                visible: itemTypesPage.showDisabled
                                everUsed: modelData.ever_used !== false
                                buttonName: "deleteDisabledItemType"
                                blockedName: "deleteBlockedItemType"
                                onDeleteRequested: deleteMasterDialog.ask("itemType", Number(modelData.id), modelData.name || "")
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


                Button {
                    objectName: "newCategoryButton"
                    text: "＋  فئة جديدة"

                    font.family: "Cairo"
                    font.bold: true

                    onClicked:
                        editCategoryDialog.openNew()
                }
            }
        }


        ActionMessageBox { scope: "categories"; objectName: "categoriesMessage" }


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
                                      + " ج/جرام"

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

                            Button {
                                objectName: "deleteDisabledCategory"
                                id: deleteDisabledCategory
                                visible: categoriesPage.showDisabled
                                flat: true
                                text: "🗑  حذف نهائي"
                                font.family: "Cairo"
                                font.pixelSize: 11
                                contentItem: Label {
                                    text: deleteDisabledCategory.text; color: theme.red; font: deleteDisabledCategory.font
                                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                                }
                                onClicked: deleteMasterDialog.ask("category", Number(modelData.id), modelData.code || "")
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
    property bool creating: false          // + فئة جديدة uses the same rule form

    objectName: "editCategoryDialog"
    modal: true
    dim: true

    Overlay.modal: Rectangle {
        color: "#80000000"
    }

    width: Math.min(620, root.width - 40)
    height: Math.min(implicitHeight, root.height - 24)
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


    function openNew() {
        creating = true
        categoryId = 0
        categoryCode = ""
        newCategoryCode.text = ""
        newCategoryName.text = ""
        categoryPurchaseMin.text = ""
        categoryPurchaseMax.text = ""
        categorySaleMin.text = ""
        categorySaleMax.text = ""
        categoryManagerThreshold.text = ""
        categoryNotes.text = ""
        editCategoryError.text = ""
        open()
        newCategoryCode.forceActiveFocus()
    }


    function openCategory(categoryData) {

        creating = false

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
                        editCategoryDialog.creating
                        ? "🏷️  فئة جديدة"
                        : "🏷️  تعديل الفئة "
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


        ScrollView {
            id: categoryFormScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredHeight: categoryFormFields.implicitHeight
            contentWidth: availableWidth
            clip: true

        ColumnLayout {
            id: categoryFormFields
            x: 22
            width: categoryFormScroll.availableWidth - 44

            spacing: 8


            // + فئة جديدة: code (unique, like A/B/C) and an optional display name
            RowLayout {
                visible: editCategoryDialog.creating
                Layout.fillWidth: true
                spacing: 10

                ColumnLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: "كود الفئة *"
                        color: theme.textSecondary
                        font.family: "Cairo"
                        horizontalAlignment: Text.AlignRight
                    }
                    TextField {
                        id: newCategoryCode
                        objectName: "newCategoryCode"
                        Layout.fillWidth: true
                        placeholderText: "مثال: E"
                        maximumLength: 20
                        font.family: "Cairo"
                        horizontalAlignment: TextInput.AlignRight
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: "اسم الفئة"
                        color: theme.textSecondary
                        font.family: "Cairo"
                        horizontalAlignment: Text.AlignRight
                    }
                    TextField {
                        id: newCategoryName
                        objectName: "newCategoryName"
                        Layout.fillWidth: true
                        placeholderText: "اختياري — الكود افتراضياً"
                        maximumLength: 100
                        font.family: "Cairo"
                        horizontalAlignment: TextInput.AlignRight
                    }
                }
            }


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
        }


        RowLayout {
            Layout.fillWidth: true

            Layout.leftMargin: 22
            Layout.rightMargin: 22
            Layout.bottomMargin: 20

            spacing: 10


            Button {
                visible: !editCategoryDialog.creating
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

            Button {
                objectName: "deleteCategoryButton"
                id: deleteCategoryButton
                visible: !editCategoryDialog.creating
                flat: true
                text: "🗑  حذف نهائي"
                font.family: "Cairo"
                font.pixelSize: 11
                contentItem: Label {
                    text: deleteCategoryButton.text; color: theme.red; font: deleteCategoryButton.font
                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                }
                onClicked: deleteMasterDialog.ask("category", editCategoryDialog.categoryId,
                                                  editCategoryDialog.categoryCode)
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
                objectName: "saveCategoryButton"
                text: editCategoryDialog.creating ? "💾  حفظ الفئة" : "💾  حفظ القواعد"

                font.family: "Cairo"
                font.bold: true


                onClicked: {

                    editCategoryError.text = ""

                    if (editCategoryDialog.creating) {
                        if (newCategoryCode.text.trim().length === 0) {
                            editCategoryError.text = "كود الفئة مطلوب."
                            return
                        }
                        if (masterDataVM.createCategory(
                                newCategoryCode.text, newCategoryName.text,
                                categoryPurchaseMin.text, categoryPurchaseMax.text,
                                categorySaleMin.text, categorySaleMax.text,
                                categoryManagerThreshold.text, categoryNotes.text)) {
                            editCategoryDialog.close()
                            categoriesPage.showDisabled = false
                        } else {
                            editCategoryError.text = masterDataVM.actionMessage
                        }
                        return
                    }

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
        objectName: "reactivateCategoryDialog"

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
    // PERMANENT DELETE — shared confirmation
    // ========================================================

    Dialog {
        id: deleteMasterDialog
        objectName: "deleteMasterDialog"

        property string kind: ""          // factory / itemType / category
        property int entityId: 0
        property string entityLabel: ""

        function ask(kindName, id, label) {
            kind = kindName
            entityId = Number(id)
            entityLabel = label || ""
            deleteMasterError.text = ""
            open()
        }

        modal: true
        dim: true
        Overlay.modal: Rectangle { color: "#80000000" }
        width: Math.min(460, root.width - 40)
        x: Math.round((root.width - width) / 2)
        y: Math.round((root.height - height) / 2)
        closePolicy: Popup.CloseOnEscape

        background: Rectangle {
            color: theme.bgSecondary
            radius: 10
            border.color: theme.red
            border.width: 1
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: 12

            Label {
                Layout.fillWidth: true
                text: "🗑  حذف نهائي"
                color: theme.red
                font.family: "Cairo"
                font.pixelSize: 17
                font.bold: true
                horizontalAlignment: Text.AlignRight
            }

            Label {
                Layout.fillWidth: true
                text: "«" + deleteMasterDialog.entityLabel + "»"
                color: theme.textPrimary
                font.family: "Cairo"
                font.bold: true
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideLeft
            }

            Label {
                objectName: "deleteMasterQuestion"
                Layout.fillWidth: true
                text: deleteMasterDialog.kind === "factory"
                      ? "سيتم حذف المصنع نهائيًا ولا يمكن التراجع عن ذلك. هل أنت متأكد؟"
                      : deleteMasterDialog.kind === "itemType"
                        ? "سيتم حذف الصنف نهائيًا ولا يمكن التراجع عن ذلك. هل أنت متأكد؟"
                        : "سيتم حذف الفئة نهائيًا ولا يمكن التراجع عن ذلك. هل أنت متأكد؟"
                color: theme.textSecondary
                font.family: "Cairo"
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignRight
            }

            Label {
                id: deleteMasterError
                objectName: "deleteMasterError"
                visible: text.length > 0
                Layout.fillWidth: true
                color: theme.red
                font.family: "Cairo"
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignRight
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                Button {
                    objectName: "deleteMasterCancel"
                    text: "إلغاء"
                    font.family: "Cairo"
                    onClicked: deleteMasterDialog.close()
                }

                Item { Layout.fillWidth: true }

                Button {
                    objectName: "deleteMasterConfirm"
                    id: deleteMasterConfirm
                    text: "حذف نهائي"
                    font.family: "Cairo"
                    font.bold: true
                    background: Rectangle {
                        radius: 6
                        color: deleteMasterConfirm.down ? Qt.darker(theme.red, 1.3) : theme.red
                    }
                    contentItem: Label {
                        text: deleteMasterConfirm.text; color: "white"; font: deleteMasterConfirm.font
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                    }
                    onClicked: {
                        var kind = deleteMasterDialog.kind
                        var ok = kind === "factory" ? masterDataVM.deleteFactory(deleteMasterDialog.entityId)
                               : kind === "itemType" ? masterDataVM.deleteItemType(deleteMasterDialog.entityId)
                               : masterDataVM.deleteCategory(deleteMasterDialog.entityId)
                        if (ok) {
                            deleteMasterDialog.close()
                            if (kind === "factory") editFactoryDialog.close()
                            else if (kind === "itemType") editItemTypeDialog.close()
                            else editCategoryDialog.close()
                        } else {
                            deleteMasterError.text = masterDataVM.actionMessage
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

        radius: theme.radiusCard

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
        id: actionMessageBox

        // The Master Data page that owns this box: a message from another page never shows here.
        property string scope: ""

        visible:
            masterDataVM.actionMessage.length > 0
            && masterDataVM.actionScope === scope

        // A success note fades after a few seconds; an error stays while the user is on the page.
        Timer {
            id: actionMessageTimer
            interval: 6000
            onTriggered: if (masterDataVM.actionSuccess && masterDataVM.actionScope === actionMessageBox.scope)
                             masterDataVM.clearAction()
        }
        Connections {
            target: masterDataVM
            function onActionChanged() {
                if (masterDataVM.actionSuccess && masterDataVM.actionScope === actionMessageBox.scope
                        && masterDataVM.actionMessage.length > 0)
                    actionMessageTimer.restart()
                else
                    actionMessageTimer.stop()
            }
        }

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


    // The permanent-delete area of a DISABLED Master Data row: a separate danger zone, never beside the
    // ordinary actions. A record that was ever used shows the reason instead of a button (the service
    // refuses the delete regardless of this display).
    component DisabledDeleteZone: Rectangle {
        id: deleteZone

        property bool everUsed: true
        property string buttonName: ""
        property string blockedName: ""

        signal deleteRequested()

        Layout.preferredWidth: 250
        Layout.preferredHeight: 62
        radius: theme.radiusSmall
        color: "transparent"
        border.color: deleteZone.everUsed ? theme.border : theme.red
        border.width: 1

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 6
            spacing: 2

            Label {
                Layout.fillWidth: true
                text: "منطقة الإجراءات الحساسة"
                color: deleteZone.everUsed ? theme.textDisabled : theme.red
                font.family: "Cairo"
                font.pixelSize: 10
                horizontalAlignment: Text.AlignRight
            }

            Label {
                objectName: deleteZone.blockedName
                visible: deleteZone.everUsed
                Layout.fillWidth: true
                text: "لا يمكن الحذف النهائي لأن السجل استُخدم سابقًا."
                color: theme.textMuted
                font.family: "Cairo"
                font.pixelSize: 10
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignRight
            }

            Button {
                id: deleteZoneButton
                objectName: deleteZone.buttonName
                visible: !deleteZone.everUsed
                Layout.fillWidth: true
                Layout.preferredHeight: 28
                flat: true
                text: "🗑  حذف نهائي"
                font.family: "Cairo"
                font.pixelSize: 11
                contentItem: Label {
                    text: deleteZoneButton.text; color: theme.red; font: deleteZoneButton.font
                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                }
                onClicked: deleteZone.deleteRequested()
            }
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

        radius: theme.radiusCard

        border.color:
            cardMouse.containsMouse
            && clickable
            ? accentColor
            : theme.cardBorder

        border.width: 1


        Rectangle {
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: 22
            anchors.rightMargin: 22

            height: 3

            color: accentColor

            radius: 2
        }


        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 3


            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                GoldPriceLabel { Layout.fillWidth: true; value: settingsCardRoot.title; color: theme.textMuted }
                GoldPriceLabel { value: settingsCardRoot.value; color: settingsCardRoot.accentColor; font.pixelSize: 22; font.bold: true }
            }
            GoldPriceLabel {
                objectName: settingsCardRoot.objectName + "Subtitle"
                value: settingsCardRoot.subtitle; color: theme.textSecondary
                font.pixelSize: 11; Layout.fillWidth: true
                wrapMode: Text.NoWrap; elide: Text.ElideLeft
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



    // نافذة إعدادات تفتح بزر من صفحتها (الخزنة / المخزون): the card inside is the same Settings card, so every service-side right (SETTINGS_MANAGE) stays as it was.
    component SettingsDialog: Dialog {
        id: settingsDialog
        property string dialogTitle: ""
        default property alias body: dialogBody.data
        modal: true
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(880, root.width - 60)
        height: Math.min(root.height - 60, 760)
        padding: 14
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: theme.bgPrimary; radius: theme.radiusCard; border.color: theme.cardBorder; border.width: 1 }
        header: Rectangle {
            implicitHeight: 46; color: "transparent"
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 18; layoutDirection: Qt.RightToLeft
                GoldPriceLabel { Layout.fillWidth: true; value: settingsDialog.dialogTitle; color: theme.gold; font.pixelSize: 17; font.bold: true }
                GoldPriceButton { objectName: settingsDialog.objectName + "Close"; text: "✕  إغلاق"; onClicked: settingsDialog.close() }
            }
        }
        contentItem: ScrollView {
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout { id: dialogBody; width: parent.width; spacing: 10 }
        }
    }

    SettingsDialog {
        id: cashCategoriesDialog
        objectName: "cashCategoriesDialog"
        dialogTitle: "تصنيفات المصروفات والإيرادات"
        onOpened: if (typeof cashCategoryVM !== "undefined" && cashCategoryVM.canManage) cashCategoryVM.refresh()
        CashCategorySettings { Layout.fillWidth: true; sectionTitle: "" }
    }

    SettingsDialog {
        id: categoryCostsDialog
        objectName: "categoryCostsDialog"
        dialogTitle: "تكلفة المصنعية للحروف"
        onOpened: if (typeof categoryCostVM !== "undefined" && categoryCostVM.canManage) categoryCostVM.refresh()
        CategoryCostSettings { Layout.fillWidth: true; sectionTitle: "" }
    }

    SettingsDialog {
        id: stockMinLevelsDialog
        objectName: "stockMinLevelsDialog"
        dialogTitle: "الحد الأدنى للمخزون"
        onOpened: if (typeof stockMinLevelVM !== "undefined" && stockMinLevelVM.canManage) stockMinLevelVM.refresh()
        StockMinLevelSettings { Layout.fillWidth: true; sectionTitle: "" }
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

                Layout.leftMargin: 30
                Layout.rightMargin: 30

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

                Layout.leftMargin: 30
                Layout.rightMargin: 30
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
        objectName: "editFactoryDialog"

        property int factoryId: 0

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

                    text: "✏️  تعديل المصنع"

                    color: theme.gold

                    font.family: "Cairo"
                    font.pixelSize: 17
                    font.bold: true
                }
            }


            ColumnLayout {
                Layout.fillWidth: true

                Layout.leftMargin: 30
                Layout.rightMargin: 30

                spacing: 8


                Label {
                    text:
                        "رقم المصنع: #"
                        + editFactoryDialog.factoryId

                    Layout.fillWidth: true

                    color: theme.textDisabled

                    font.family: "Cairo"
                    font.pixelSize: 10

                    horizontalAlignment:
                        Text.AlignRight
                }


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
                    id: editFactoryNameField
                    objectName: "editFactoryNameField"

                    Layout.fillWidth: true
                    Layout.preferredHeight: 44

                    color: theme.textPrimary

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
                    id: editFactoryCodeField

                    Layout.fillWidth: true
                    Layout.preferredHeight: 44

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
                    id: editFactoryNotesField

                    Layout.fillWidth: true
                    Layout.preferredHeight: 90

                    color: theme.textPrimary

                    font.family: "Cairo"

                    horizontalAlignment:
                        TextEdit.AlignRight

                    wrapMode: TextEdit.Wrap
                }


                Label {
                    id: editFactoryError

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

                Layout.leftMargin: 30
                Layout.rightMargin: 30
                Layout.bottomMargin: 20

                spacing: 10


                Button {
                    objectName: "disableFactoryButton"
                    text: "⛔  تعطيل المصنع"

                    font.family: "Cairo"

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
                    text: "إلغاء"

                    font.family: "Cairo"

                    onClicked:
                        editFactoryDialog.close()
                }


                Button {
                    objectName: "saveFactoryEditButton"
                    text: "💾  حفظ التعديلات"

                    font.family: "Cairo"
                    font.bold: true

                    onClicked: {

                        editFactoryError.text = ""

                        if (
                            editFactoryNameField.text
                            .trim()
                            .length === 0
                        ) {

                            editFactoryError.text =
                                "اسم المصنع مطلوب."

                            return
                        }


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
        objectName: "reactivateFactoryDialog"

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

                Layout.leftMargin: 30
                Layout.rightMargin: 30

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

                Layout.leftMargin: 30
                Layout.rightMargin: 30
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
        objectName: "editItemTypeDialog"

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

                Layout.leftMargin: 30
                Layout.rightMargin: 30

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
                    objectName: "editItemTypeNameField"

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

                Layout.leftMargin: 30
                Layout.rightMargin: 30
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
        objectName: "reactivateItemTypeDialog"

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
        objectName: "newInventoryItemDialog"

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
            inventoryEntrySuccess.text = ""

            inventoryVM.clearActionMessage()
            oldGoldConversionVM.clearActionMessage()
        }


        // UX-INV-ADD-01: a batch of pieces is entered in one go. After a successful save only the
        // piece-specific fields are cleared (weight, size, barcode, notes, the Scrap weight used); the batch
        // choices (item type, karat, category, factory, entry method) stay, and the weight field takes focus.
        function prepareNextPiece(message) {

            weightField.text = ""
            sizeField.text = ""
            barcodeField.text = ""
            oldGoldConsumedWeightField.text = ""
            notesField.text = ""

            inventoryEntryError.text = ""
            inventoryEntrySuccess.text = message || "تم حفظ القطعة."

            weightField.forceActiveFocus()
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

                    // No responsible / receiving employee: manual entry and the old-gold conversion
                    // record the authenticated actor (owner decisions 2026-10-01 / 2026-10-02).


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
                                objectName: "inventoryItemTypeCombo"

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
                                objectName: "inventoryKaratCombo"

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
                                objectName: "inventoryWeightField"

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
                                objectName: "inventorySizeField"

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
                                    objectName: "inventoryCategoryCombo"

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
                                    objectName: "inventoryFactoryCombo"

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

                                text: "طريقة الإدخال *"

                                color: theme.textSecondary

                                font.family: "Cairo"
                                font.pixelSize: 12

                                horizontalAlignment:
                                    Text.AlignRight
                            }


                            ComboBox {
                                id: stockClassificationCombo
                                objectName: "inventoryEntryMethodCombo"

                                Layout.fillWidth: true
                                Layout.preferredHeight: 44

                                model: [
                                    "إدخال يدوي — بضاعة جديدة",
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


                            BarcodeField {
                                id: barcodeField
                                objectName: "inventoryBarcodeField"

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


                            // Weight held for a manager decision is physically in Scrap but not usable (032).
                            Label {
                                objectName: "conversionHeldBreakdown"
                                Layout.fillWidth: true
                                visible: text.length > 0

                                text:
                                    karatCombo.currentIndex >= 0
                                    ? oldGoldConversionVM.scrapBreakdownForKarat(
                                          Number(karatCombo.currentValue))
                                    : ""

                                color: theme.amber
                                font.family: "Cairo"
                                font.pixelSize: 12
                                wrapMode: Text.WordWrap

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
                                        objectName: "inventoryScrapWeightField"

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
                                                + " جرام"

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
                        objectName: "inventoryNotesField"

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


                    // The last saved piece (the dialog stays open for the next one).
                    Label {
                        id: inventoryEntrySuccess
                        objectName: "inventoryEntrySuccess"

                        visible:
                            text.length > 0

                        Layout.fillWidth: true

                        color: theme.green

                        font.family: "Cairo"
                        font.pixelSize: 12
                        font.bold: true

                        horizontalAlignment:
                            Text.AlignRight

                        wrapMode:
                            Text.WordWrap
                    }


                    Label {
                        id: inventoryEntryError
                        objectName: "inventoryEntryError"

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


                    Label {
                        objectName: "inventoryEntryNoRight"

                        visible: !saveInventoryItem.enabled

                        text: "لا تملك صلاحية هذا النوع من الإدخال."

                        color: theme.statusWarning

                        font.family: "Cairo"
                        font.pixelSize: 11
                        font.bold: true
                    }


                    Button {
                        text: "إلغاء"

                        onClicked:
                            newInventoryItemDialog.close()
                    }


                    Button {
                        id: saveInventoryItem
                        objectName: "saveInventoryItemButton"
                        text: "💾  حفظ القطعة"

                        // Manual entry needs INVENTORY_MANAGE, a piece converted from Scrap OLD_GOLD_MANAGE
                        // (a hint: the services refuse the write without the right, whatever this screen shows).
                        enabled: stockClassificationCombo.currentIndex === 0
                                 ? inventoryVM.canManage
                                 : oldGoldConversionVM.canManage

                        font.family: "Cairo"
                        font.bold: true

                        onClicked: {

                            inventoryEntryError.text = ""
                            inventoryEntrySuccess.text = ""


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


                            // A barcode is stored exactly; anything outside the barcode set is refused
                            // (never silently "corrected" into another piece's barcode).
                            if (barcodeField.text.length > 0
                                    && barcodeInput.problem(barcodeField.text).length > 0) {
                                inventoryEntryError.text = barcodeInput.problem(barcodeField.text)
                                return
                            }
                            if (barcodeField.text.length > 0)
                                barcodeField.text = barcodeInput.canonical(barcodeField.text)
                            if (
                                stockClassificationCombo
                                .currentIndex === 0
                            ) {
                                saved =
                                    inventoryVM.createInventoryItem(
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

                                // Stay open for the next piece (closed only by ✕ / إلغاء).
                                newInventoryItemDialog.prepareNextPiece(
                                    stockClassificationCombo.currentIndex === 0
                                    ? inventoryVM.actionMessage
                                    : oldGoldConversionVM.actionMessage
                                )
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
        objectName: "inventoryItemDetailsDialog"

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
                : numberValue.toFixed(3)
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
            if (value === "SUPPLIER_PURCHASE")
                return "توريد مورد"
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
                                text: "طريقة الإدخال"
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
                                text: "نوع البضاعة"
                                color: theme.textMuted
                                horizontalAlignment: Text.AlignRight
                            }
                            Label {
                                objectName: "inventoryDetailsStockClass"
                                Layout.fillWidth: true
                                text:
                                    inventoryItemDetailsDialog.stockClassText(
                                        root.selectedInventoryItem
                                        .stock_classification
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
        objectName: "barcodePrintQueueDialog"

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
                                        + " جرام"

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
                                        + " جرام"

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
                        objectName: "printBarcodeNowButton"
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
