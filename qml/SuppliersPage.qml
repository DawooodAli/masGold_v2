import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// الموردون (Purchasing & Suppliers V1): suppliers with their two independent positions (gold, making), purchase
// invoices with making groups, the two immutable ledgers, and piece-by-piece barcode intake with weight variance.
// Everything shown is formatted by supplierVM / supplierPurchaseVM; no accounting is computed here and no right is
// decided here (the services re-check every permission in the transaction). Positions are always worded with their
// direction (علينا للمورد / لنا عند المورد), never an ambiguous signed number.
Item {
    id: page
    objectName: "suppliersPage"
    signal tripRequested(string tripId)                                    // the marketer statement opens the market-trip custody page on the source trip
    property string pageTitle: ""

    readonly property var vm: typeof supplierVM !== "undefined" ? supplierVM : null
    readonly property var pvm: typeof supplierPurchaseVM !== "undefined" ? supplierPurchaseVM : null
    readonly property var detail: vm ? vm.detail : ({})
    readonly property bool hasSelection: !!vm && vm.selectedSupplierId !== "" && !!detail && detail.id !== undefined
    property int tab: 0
    readonly property var mvm: vm ? vm.marketer : null                    // 055: the marketer custody account (apart from the supplier account)
    readonly property bool goldTabVisible: page.detail.hasAccount !== false || (!!vm && vm.holdingsPanel.visible === true)
    property bool holdDetails: false          // the party's Details section (codes, history) is collapsed by default
    // which holding rows have their «المزيد» row open, keyed by source (kind + pool id): kept here, not in the row, so a refresh that rebuilds the rows does not collapse it
    property var moreOpen: ({})
    function toggleMore(key) {
        var next = {}
        for (var k in moreOpen) next[k] = moreOpen[k]
        next[key] = next[key] !== true
        moreOpen = next
    }

    Theme { id: theme }

    function toneColor(tone) {
        return tone === "shopOwes" ? theme.accountPayable
             : tone === "supplierOwes" ? theme.accountReceivable : theme.textMuted
    }

    // The invoices follow the selected supplier. Opening the page (or returning to it) with a supplier already
    // selected loads its invoices; choosing a supplier does it through supplierVM.selectionChanged below.
    function loadSelectedInvoices() {
        if (visible && vm && vm.canView && pvm && vm.selectedSupplierId !== "") pvm.showSupplier(vm.selectedSupplierId)
    }
    onVisibleChanged: if (visible && vm && vm.canView) { vm.clearMessage(); vm.refresh(); loadSelectedInvoices() }
    Component.onCompleted: if (visible && vm && vm.canView) { vm.refresh(); loadSelectedInvoices() }

    Connections {
        target: page.vm
        function onSupplierSaved(supplierId) {
            if (supplierId !== "" && page.vm.selectedSupplierId === "") page.vm.selectSupplier(supplierId)
        }
        function onSelectionChanged(supplierId) {
            if (supplierId === "" || !page.pvm) return
            page.tab = 0
            page.holdDetails = false
            page.moreOpen = ({})
            page.pvm.showSupplier(supplierId)
        }
        function onPaymentPosted(code) { payDialog.close() }
        function onReversalDone(code) { reverseDialog.close() }
        function onSettlementPosted(code) { goldSettleDialog.close() }
        function onSettlementReversed(code) { reverseSettlementDialog.close() }
        function onHoldingReturned(code) { holdingReturnDialog.close(); logoReturnDialog.close() }
        function onHoldingSettled(code) { holdingSettleDialog.close() }
        function onReceiptReversed(code) { reverseReceiptDialog.close() }
    }
    Connections {
        target: page.pvm
        function onInvoicePosted(invoiceId) { newInvoiceDialog.close(); intakeDialog.openFor(invoiceId) }
        function onDraftSaved(invoiceId) { newInvoiceDialog.close() }
        function onInvoiceReversed(invoiceId) { reverseInvoiceDialog.close(); if (page.vm) page.vm.refresh() }
    }

    Rectangle { anchors.fill: parent; color: theme.bgPrimary }

    // ---------------------------------------------------------------- small building blocks
    // Presentation helpers only. ltr(): keep a reference / barcode / id as one left-to-right unit inside Arabic text
    // (the same isolate the ViewModels use for dates and signed numbers). after(): the value part of a ViewModel
    // "label: value" string, so a labelled tile does not repeat its own label.
    function ltr(text) {
        return text === undefined || text === null || text === "" ? "" : "⁦" + text + "⁩"
    }
    function after(text) {
        var s = String(text === undefined || text === null ? "" : text)
        var i = s.indexOf(": ")
        return i >= 0 ? s.substring(i + 2) : s
    }

    component Txt: Label {
        color: theme.textPrimary
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        horizontalAlignment: Text.AlignRight
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideLeft
        textFormat: Text.PlainText
    }

    // Material buttons carry a 6 px top/bottom inset that shrinks a custom background to ~60 % of the item height
    // (the cause of the clipped tab labels): the insets are cleared so the control is as tall as it says.
    component Btn: Button {
        id: btn
        property bool primary: false
        property color accent: theme.gold
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        implicitHeight: 30
        topInset: 0
        bottomInset: 0
        leftInset: 0
        rightInset: 0
        topPadding: 0
        bottomPadding: 0
        leftPadding: 12
        rightPadding: 12
        hoverEnabled: true
        contentItem: Label {
            text: btn.text
            font: btn.font
            color: !btn.enabled ? theme.textDisabled : btn.primary ? theme.bgPrimary : btn.accent
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: theme.radiusSmall
            color: !btn.enabled ? (btn.primary ? theme.bgTertiary : "transparent")
                 : btn.primary ? (btn.down || btn.hovered ? Qt.lighter(btn.accent, 1.12) : btn.accent)
                 : btn.down ? theme.panelHover : btn.hovered ? theme.bgTertiary : "transparent"
            border.color: btn.visualFocus ? theme.goldBright : btn.enabled ? btn.accent : theme.border
            border.width: btn.visualFocus ? 2 : 1
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
        implicitHeight: 34
        hoverEnabled: true
        background: Rectangle { radius: 4; color: theme.bgPrimary
                                border.color: field.activeFocus ? theme.gold : field.hovered ? theme.goldDark : theme.border }
    }

    // Arabic value on the right, gold drop-down arrow on the left (the application's ComboBox convention).
    component Combo: ComboBox {
        id: combo
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        implicitHeight: 34
        hoverEnabled: true
        delegate: ItemDelegate {
            width: combo.width
            height: 34
            highlighted: combo.highlightedIndex === index
            contentItem: Label { text: modelData[combo.textRole]; font.family: "Cairo"; font.pixelSize: theme.fontBody
                                 color: theme.textPrimary; horizontalAlignment: Text.AlignRight
                                 verticalAlignment: Text.AlignVCenter; elide: Text.ElideLeft }
        }
        indicator: Label {
            x: 10
            y: (combo.height - height) / 2
            text: "▾"
            color: theme.gold
            font.pixelSize: theme.fontBodyStrong
        }
        contentItem: Label {
            text: combo.displayText
            font: combo.font
            color: theme.textPrimary
            horizontalAlignment: Text.AlignRight
            verticalAlignment: Text.AlignVCenter
            rightPadding: 10
            leftPadding: 30
            elide: Text.ElideLeft
        }
        background: Rectangle {
            radius: 4
            color: theme.bgPrimary
            border.color: combo.activeFocus || combo.popup.visible ? theme.gold : combo.hovered ? theme.goldDark : theme.border
        }
    }

    // Supplier position card (gold in grams of the supplier's karat / making in EGP): both cards of a row share one height.
    component Card: Rectangle {
        default property alias content: body.data
        property string title: ""
        property color accent: theme.gold
        Layout.fillWidth: true
        Layout.fillHeight: true
        implicitHeight: body.implicitHeight + 34
        color: theme.bgTertiary
        radius: theme.radiusSmall
        border.color: theme.border
        Rectangle { anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                    anchors.margins: 1; width: 3; radius: 2; color: parent.accent }
        Txt { id: cardTitle; anchors.top: parent.top; anchors.right: parent.right; anchors.topMargin: 4
              anchors.rightMargin: 14; text: parent.title; color: parent.accent; font.bold: true }
        ColumnLayout {
            id: body
            anchors.top: cardTitle.bottom; anchors.left: parent.left; anchors.right: parent.right
            anchors.topMargin: 2; anchors.leftMargin: 12; anchors.rightMargin: 14
            spacing: 4
        }
    }

    // A position sentence with its figure emphasised: direction words and unit/basis in a readable neutral, the number
    // bold and larger in `numberColor` (the colour tells gold weight from making money, never payable from receivable).
    // `text` is the plain sentence (the same words as before).
    component PositionLine: RowLayout {
        property var parts: ({})
        property color numberColor: theme.gold
        readonly property string text: [parts.lead, parts.number, parts.tail].filter(function (x) { return !!x }).join(" ")
        Layout.fillWidth: true
        layoutDirection: Qt.RightToLeft
        spacing: 5
        Txt { text: parent.parts.lead || ""; color: theme.textSecondary; font.pixelSize: theme.fontBodyStrong
              elide: Text.ElideNone }
        Txt { visible: !!parent.parts.number; text: page.ltr(parent.parts.number || ""); color: parent.numberColor
              font.bold: true; font.pixelSize: theme.fontSection + 2; elide: Text.ElideNone }
        Txt { visible: !!parent.parts.tail; text: parent.parts.tail || ""; color: theme.textSecondary
              font.pixelSize: theme.fontBodyStrong; elide: Text.ElideNone }
        Item { Layout.fillWidth: true }
    }

    // One tab of the supplier detail tab bar. 42 px tall, label never clipped, gold top bar + gold label when
    // selected (the selected tab takes the colour of the content below it, so the bar and the content read as one),
    // distinct hover and keyboard-focus states.
    component SegTab: Button {
        id: seg
        property bool on: false
        font.family: "Cairo"
        font.pixelSize: theme.fontBodyStrong
        font.bold: on
        implicitHeight: 42
        implicitWidth: Math.max(150, contentItem.implicitWidth + 44)
        Layout.fillHeight: true
        topInset: 0
        bottomInset: 0
        leftInset: 0
        rightInset: 0
        topPadding: 0
        bottomPadding: 0
        leftPadding: 22
        rightPadding: 22
        hoverEnabled: true
        focusPolicy: Qt.StrongFocus
        contentItem: Label {
            text: seg.text
            font: seg.font
            color: seg.on ? theme.gold : seg.hovered ? theme.textPrimary : theme.textSecondary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideNone
        }
        background: Rectangle {
            color: seg.on ? theme.bgPrimary : seg.down || seg.hovered ? theme.panelHover : "transparent"
            Rectangle { visible: seg.on; anchors.top: parent.top; width: parent.width; height: 3; color: theme.gold }
            Rectangle { visible: !seg.on; anchors.left: parent.left; width: 1; height: parent.height - 14
                        anchors.verticalCenter: parent.verticalCenter; color: theme.border }
            Rectangle { visible: seg.visualFocus; anchors.fill: parent; anchors.margins: 3; radius: 4
                        color: "transparent"; border.color: theme.goldBright; border.width: 1 }
        }
    }

    // A caption over a value (labelled summary / group figures). Equal-width cells inside a GridLayout.
    component Stat: ColumnLayout {
        property string label: ""
        property string value: ""
        property color valueColor: theme.textPrimary
        property bool strong: false
        property alias valueName: statValue.objectName
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        spacing: 1
        Txt { Layout.fillWidth: true; text: parent.label; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
        Txt { id: statValue; Layout.fillWidth: true; text: parent.value; color: parent.valueColor; font.bold: parent.strong
              font.pixelSize: parent.strong ? theme.fontBodyStrong : theme.fontBody }
    }

    // A titled section card of the intake dialog: gold tick + title on the right, an optional status hint on the left.
    component Section: Rectangle {
        id: section
        default property alias content: sectionBody.data
        property string title: ""
        property string hint: ""
        property color hintColor: theme.textMuted
        property int bodySpacing: 8
        Layout.fillWidth: true
        implicitHeight: sectionColumn.implicitHeight + 24
        color: theme.panel
        radius: theme.radiusSmall
        border.color: theme.border
        ColumnLayout {
            id: sectionColumn
            anchors.fill: parent
            anchors.margins: 12
            spacing: 10
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Rectangle { implicitWidth: 3; implicitHeight: 16; radius: 2; color: theme.gold }
                Txt { text: section.title; color: theme.gold; font.bold: true; font.pixelSize: theme.fontBodyStrong }
                Item { Layout.fillWidth: true }
                Txt { visible: section.hint !== ""; text: section.hint; color: section.hintColor; font.bold: true }
            }
            ColumnLayout { id: sectionBody; Layout.fillWidth: true; spacing: section.bodySpacing }
        }
    }

    // Inline "label: value" (compact group cards); wraps as a unit inside a Flow.
    // The horizontal gap to the next pair is part of the pair (Flow.spacing would also add it between wrapped lines).
    component Pair: Item {
        id: pair
        property string label: ""
        property string value: ""
        property color valueColor: theme.textPrimary
        property bool strong: false
        property int gap: 22
        implicitWidth: pairRow.implicitWidth + gap
        implicitHeight: pairRow.implicitHeight
        RowLayout {
            id: pairRow
            anchors.right: parent.right
            spacing: 5
            layoutDirection: Qt.RightToLeft
            Txt { text: pair.label + ":"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone }
            Txt { text: pair.value; color: pair.valueColor; font.bold: pair.strong; elide: Text.ElideNone
                  font.pixelSize: pair.strong ? theme.fontBodyStrong : theme.fontBody }
        }
    }

    // Caption (+ required mark) over a control; cells share equal width inside a GridLayout.
    component FieldBox: ColumnLayout {
        id: box
        property string label: ""
        property bool required: false
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        spacing: 4
        RowLayout {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            spacing: 4
            Txt { text: box.label; color: theme.textSecondary; font.pixelSize: theme.fontTableHeader; font.bold: true }
            Txt { visible: box.required; text: "*"; color: theme.amber; font.bold: true }
            Item { Layout.fillWidth: true }
        }
    }

    component ColHead: Txt { color: theme.textMuted; font.pixelSize: theme.fontTableHeader; font.bold: true }

    // ---------------------------------------------------------------- page body
    // Stable column widths shared by the invoice list header and its rows; a compact set (wrapping secondary lines,
    // taller rows) when the list is narrower than 640 px, i.e. at the smallest supported window.
    readonly property bool compactInvoices: invoiceList.width < 640
    readonly property int colInvoiceWidth: compactInvoices ? 120 : 190
    readonly property int colFiguresWidth: compactInvoices ? 132 : 220
    readonly property int colActionWidth: compactInvoices ? 92 : 112
    // Ledger rows: the rightmost column carries the movement description, the date/time and the invoice reference.
    readonly property int colLedgerWidth: compactInvoices ? 210 : 310
    readonly property int colLedgerValueWidth: 175

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 8
        visible: !!page.vm && page.vm.canView

        RowLayout {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            spacing: 8
            Txt { text: "🤝  الموردون"; color: theme.gold; font.bold: true; font.pixelSize: theme.fontPageTitle }
            Item { Layout.fillWidth: true }
            Txt {
                objectName: "supplierMessage"
                visible: !!page.vm && (page.vm.message !== "" || page.vm.errorMessage !== "")
                text: page.vm ? (page.vm.errorMessage !== "" ? page.vm.errorMessage : page.vm.message) : ""
                color: page.vm && page.vm.errorMessage !== "" ? theme.red : theme.green
                font.bold: true
                elide: Text.ElideRight
                Layout.maximumWidth: 460
            }
            Btn {
                objectName: "newSupplierButton"
                visible: !!page.vm && page.vm.canManage
                text: "＋  مورد جديد"
                primary: true
                onClicked: supplierDialog.openFor(null)
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            layoutDirection: Qt.RightToLeft
            spacing: 12

            // ------------------------------------------------------------ supplier list panel (right)
            Rectangle {
                Layout.preferredWidth: page.width < 960 ? 250 : 310
                Layout.maximumWidth: 330
                Layout.fillHeight: true
                color: theme.bgSecondary
                radius: theme.radiusMedium
                border.color: theme.border
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        Txt { text: "قائمة الموردين"; color: theme.textSecondary; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Txt { text: supplierList.count + ""; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                    }
                    Field {
                        objectName: "supplierSearch"
                        Layout.fillWidth: true
                        placeholderText: "بحث بالاسم أو الهاتف"
                        onTextEdited: if (page.vm) page.vm.search(text)
                    }
                    CheckBox {
                        Layout.alignment: Qt.AlignRight
                        text: "النشطون فقط"
                        font.family: "Cairo"
                        onToggled: if (page.vm) page.vm.setActiveOnly(checked)
                        contentItem: Label { text: parent.text; font: parent.font; color: theme.textSecondary
                                             leftPadding: parent.indicator.width + 6; horizontalAlignment: Text.AlignRight }
                    }
                    Combo {
                        id: roleFilterCombo
                        objectName: "supplierRoleFilter"
                        Layout.fillWidth: true
                        readonly property var choices: [{ role: "", text: "كل الأطراف" }, { role: "SUPPLIER", text: "الموردون" },
                                                        { role: "INTERMEDIARY", text: "المسوقجية" }]
                        model: choices
                        textRole: "text"
                        onActivated: function (i) { if (page.vm) page.vm.setRoleFilter(choices[i].role) }
                    }
                    ListView {
                        id: supplierList
                        objectName: "supplierList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 6
                        model: page.vm ? page.vm.suppliers : []
                        delegate: Rectangle {
                            width: supplierList.width
                            height: supplierColumn.implicitHeight + 16
                            radius: theme.radiusSmall
                            color: modelData.selected ? theme.panelHover : theme.bgTertiary
                            border.color: modelData.selected ? theme.gold : theme.border
                            opacity: modelData.active ? 1 : 0.6
                            MouseArea {
                                anchors.fill: parent
                                onClicked: page.vm.selectSupplier(modelData.id)
                            }
                            ColumnLayout {
                                id: supplierColumn
                                anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 10; anchors.rightMargin: 12
                                spacing: 2
                                RowLayout {
                                    Layout.fillWidth: true
                                    layoutDirection: Qt.RightToLeft
                                    Txt { text: modelData.name; font.bold: true; Layout.fillWidth: true }
                                    Txt { text: modelData.stateText; color: modelData.active ? theme.green : theme.textMuted
                                          font.pixelSize: theme.fontCaption }
                                }
                                Txt { objectName: "supplierRoleText"; Layout.fillWidth: true; text: modelData.roleText; color: theme.gold
                                      font.pixelSize: theme.fontCaption }
                                Txt { Layout.fillWidth: true; visible: modelData.isSupplier; text: modelData.goldText; color: page.toneColor(modelData.goldTone)
                                      font.pixelSize: theme.fontCaption }
                                Txt { Layout.fillWidth: true; visible: modelData.isSupplier; text: modelData.makingText; color: page.toneColor(modelData.makingTone)
                                      font.pixelSize: theme.fontCaption }
                            }
                        }
                        Txt {
                            anchors.centerIn: parent
                            visible: supplierList.count === 0
                            text: page.vm && page.vm.busy ? "جار التحميل..." : "لا يوجد موردون"
                            color: theme.textMuted
                        }
                    }
                }
            }

            // ------------------------------------------------------------ selected supplier (left): one grouped panel
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: theme.bgSecondary
                radius: theme.radiusMedium
                border.color: page.hasSelection ? theme.goldDark : theme.border

                Txt {
                    visible: !page.hasSelection
                    anchors.centerIn: parent
                    text: "اختر موردًا لعرض موقفه وفواتيره وسجلاته."
                    color: theme.textMuted
                }

                // ONE scroll for the whole selected-party content: the sections flow at their natural height (no nested scrolling, no overlapping panels).
                // Supplier account first; intermediary-only parties have no supplier account section.
                Flickable {
                    id: partyScroll
                    objectName: "partyScroll"
                    visible: page.hasSelection
                    anchors.fill: parent
                    anchors.margins: 12
                    clip: true
                    contentWidth: width
                    contentHeight: partyBody.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    flickableDirection: Flickable.VerticalFlick
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    GridLayout {
                        id: partyBody
                        width: partyScroll.width - 14
                        columns: 1
                        rowSpacing: 12

                    // name + code, then the party's actions (side by side when wide, stacked when narrow)
                    GridLayout {
                        Layout.row: 0
                        Layout.fillWidth: true
                        columns: width >= 720 ? 2 : 1
                        layoutDirection: Qt.RightToLeft
                        columnSpacing: 8
                        rowSpacing: 8
                        RowLayout {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            layoutDirection: Qt.RightToLeft
                            spacing: 8
                            Txt { text: page.detail.name || ""; font.bold: true; font.pixelSize: theme.fontPageTitle; color: theme.gold
                                  elide: Text.ElideRight; Layout.maximumWidth: 360 }
                            Rectangle {
                                visible: !!page.detail.code
                                implicitWidth: codeText.implicitWidth + 16
                                implicitHeight: 24
                                radius: 4
                                color: theme.bgTertiary
                                border.color: theme.border
                                Txt { id: codeText; anchors.centerIn: parent; text: page.ltr(page.detail.code || "")
                                      color: theme.textSecondary; font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone }
                            }
                            Rectangle {
                                objectName: "supplierRoleChip"
                                implicitWidth: roleChipText.implicitWidth + 16
                                implicitHeight: 24
                                radius: 4
                                color: theme.bgTertiary
                                border.color: theme.goldDark
                                Txt { id: roleChipText; anchors.centerIn: parent; text: page.detail.roleText || ""; color: theme.gold; font.bold: true
                                      font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone }
                            }
                            Rectangle {
                                objectName: "supplierKaratChip"
                                visible: page.detail.isSupplier !== false
                                implicitWidth: karatChipText.implicitWidth + 16
                                implicitHeight: 24
                                radius: 4
                                color: theme.bgTertiary
                                border.color: page.detail.karatResolved ? theme.goldDark : theme.amber
                                Txt { id: karatChipText; anchors.centerIn: parent; text: page.detail.karatText || ""
                                      color: page.detail.karatResolved ? theme.gold : theme.amber; font.bold: true
                                      font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone }
                            }
                            Item { Layout.fillWidth: true }
                        }
                        RowLayout {
                            Layout.alignment: Qt.AlignRight
                            layoutDirection: Qt.RightToLeft
                            spacing: 8
                            Btn {
                                objectName: "newInvoiceButton"
                                visible: !!page.pvm && page.pvm.canEdit && page.detail.isSupplier !== false
                                enabled: page.hasSelection && page.detail.active === true && page.detail.karatResolved === true
                                text: "＋  فاتورة شراء جديدة"
                                primary: true
                                onClicked: newInvoiceDialog.openFor()
                            }
                            Btn {
                                objectName: "editSupplierButton"
                                visible: !!page.vm && page.vm.canManage
                                text: "تعديل"
                                onClicked: supplierDialog.openFor(page.detail)
                            }
                            Btn {
                                objectName: "toggleSupplierButton"
                                visible: !!page.vm && page.vm.canManage
                                text: page.detail.active ? "إيقاف" : "تفعيل"
                                accent: page.detail.active ? theme.amber : theme.green
                                onClicked: page.vm.setSupplierActive(page.detail.id, !page.detail.active)
                            }
                            Btn {
                                objectName: "deleteSupplierButton"
                                visible: !!page.vm && page.vm.canManage
                                text: "حذف"
                                accent: theme.red
                                onClicked: deleteConfirm.open()
                            }
                        }
                    }

                    // ============================================================ the supplier account: gold and making are two separate positions
                    ColumnLayout {
                        id: accountGroup
                        objectName: "accountGroup"
                        Layout.row: 1
                        Layout.fillWidth: true
                        spacing: 10

                        Txt { objectName: "accountTitle"; Layout.fillWidth: true; visible: page.detail.hasAccount !== false; text: "حساب المورد"; color: theme.gold; font.bold: true
                              font.pixelSize: theme.fontSection }

                        // The two independent positions: gold (physical grams of the supplier's karat) and making (EGP). Equal cards, equal height.
                        RowLayout {
                            objectName: "accountPositions"
                            visible: page.detail.hasAccount !== false
                                     || (!!page.vm && page.vm.holdingsPanel.goldPositionVisible === true)
                            Layout.fillWidth: true
                            Layout.fillHeight: false
                            layoutDirection: Qt.RightToLeft
                            spacing: 10
                            Card {
                                Layout.preferredWidth: 1
                                title: "موقف الذهب"
                                accent: page.toneColor(page.detail.goldTone)
                                Txt { objectName: "goldAccountBalanceLabel"; Layout.fillWidth: true
                                      visible: page.detail.hasAccount !== false; text: "حساب المورد"
                                      color: theme.textSecondary; font.pixelSize: theme.fontTableHeader }
                                PositionLine { objectName: "goldPosition"; visible: page.detail.hasAccount !== false
                                               parts: page.detail.goldParts || ({})
                                               numberColor: theme.balanceKarat21 }
                                Repeater {
                                    visible: page.detail.hasAccount !== false
                                    model: page.detail.goldBreakdown || []
                                    delegate: Txt { Layout.fillWidth: true; text: modelData.text; color: page.toneColor(modelData.tone)
                                                    font.pixelSize: theme.fontTableHeader }
                                }
                                Txt { objectName: "goldNote"; Layout.fillWidth: true
                                      visible: page.detail.hasAccount !== false && !!page.detail.goldNote
                                      text: page.detail.goldNote || ""; color: theme.amber; font.pixelSize: theme.fontTableHeader
                                      wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                // the gold the shop holds at this party as an INTERMEDIARY is a separate subsection, never netted with the supplier account above
                                Rectangle { objectName: "goldHeldDivider"; Layout.fillWidth: true; Layout.topMargin: 6; Layout.preferredHeight: 1
                                            visible: !!page.vm && page.vm.holdingsPanel.goldPositionVisible === true
                                            color: theme.border }
                                Txt { objectName: "goldHeldPosition"; Layout.fillWidth: true; Layout.topMargin: 2
                                      visible: !!page.vm && page.vm.holdingsPanel.goldPositionVisible === true
                                      text: page.vm ? page.vm.holdingsPanel.goldPositionText || "" : ""
                                      color: theme.balanceKarat21; font.bold: true; font.pixelSize: theme.fontBodyStrong
                                      wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                Btn {
                                    objectName: "settleGoldButton"
                                    Layout.alignment: Qt.AlignRight
                                    visible: !!page.vm && page.vm.canSettleGold && page.detail.isSupplier !== false
                                    enabled: page.hasSelection && !page.vm.busy && page.detail.karatResolved === true      // an inactive supplier can still receive it
                                    text: "تسوية ذهب"
                                    primary: true
                                    onClicked: goldSettleDialog.openFor()
                                }
                            }
                            Card {
                                objectName: "makingPositionCard"
                                visible: page.detail.hasAccount !== false
                                Layout.preferredWidth: 1
                                title: "موقف مصنعيات المورد"
                                accent: page.toneColor(page.detail.makingTone)
                                PositionLine { objectName: "makingPosition"; parts: page.detail.makingParts || ({})
                                               numberColor: theme.gold }
                                Btn {
                                    objectName: "payMakingButton"
                                    Layout.alignment: Qt.AlignRight
                                    visible: !!page.vm && page.vm.canPayMaking
                                    enabled: page.hasSelection && !page.vm.busy          // an inactive supplier can still be paid
                                    text: "دفع مصنعيات"
                                    primary: true
                                    onClicked: payDialog.openFor()
                                }
                            }
                        }

                        // Tab bar + its content as one connected card, at the content's natural height (the page scrolls, the lists do not).
                        Rectangle {
                            id: tabFrame
                            Layout.fillWidth: true
                            Layout.preferredHeight: tabFrameColumn.implicitHeight + 2
                            color: theme.bgPrimary
                            radius: theme.radiusSmall
                            border.color: theme.border
                            clip: true

                            ColumnLayout {
                                id: tabFrameColumn
                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                anchors.margins: 1
                                spacing: 0

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 42
                                    color: theme.bgTertiary
                                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.border }
                                    RowLayout {
                                        anchors.fill: parent
                                        layoutDirection: Qt.RightToLeft
                                        spacing: 0
                                        RowLayout {
                                            Layout.fillHeight: true
                                            layoutDirection: Qt.RightToLeft
                                            spacing: 0
                                            uniformCellSizes: true
                                            SegTab { objectName: "tabInvoices"; text: "نظرة عامة"; on: page.tab === 0; onClicked: page.tab = 0 }
                                            SegTab { objectName: "tabGold"; visible: page.goldTabVisible; text: "سجل الذهب"; on: page.tab === 1; onClicked: page.tab = 1 }
                                            SegTab { objectName: "tabMaking"; visible: page.detail.hasAccount !== false; text: "سجل المصنعيات"; on: page.tab === 2; onClicked: page.tab = 2 }
                                        }
                                        Item { Layout.fillWidth: true }
                                    }
                                }

                                Item {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: page.tab === 0 ? (page.detail.hasAccount === false ? overviewHolding.implicitHeight : overviewColumn.implicitHeight) + 20
                                                          : page.tab === 1 ? Math.max(70, goldTab.implicitHeight + 20) : Math.max(70, makingList.contentHeight + 20)

                                    // ---- overview: purchase invoices
                                    ColumnLayout {
                                        id: overviewHolding
                                        objectName: "overviewHolding"
                                        visible: page.tab === 0 && page.detail.hasAccount === false
                                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                        anchors.margins: 10
                                        spacing: 6
                                        Txt { objectName: "intermediaryOnlyNote"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.textSecondary
                                              text: "هذا الطرف مسوقجي فقط؛ لا توجد فواتير شراء." }
                                    }
                                    ColumnLayout {
                                        id: overviewColumn
                                        visible: page.tab === 0 && page.detail.hasAccount !== false
                                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                        anchors.margins: 10
                                        spacing: 6
                                        RowLayout {
                                            Layout.fillWidth: true
                                            layoutDirection: Qt.RightToLeft
                                            Txt { text: "فواتير الشراء"; color: theme.gold; font.bold: true }
                                            Item { Layout.fillWidth: true }
                                            Txt { text: !!page.pvm && page.pvm.invoicesState === "loaded" ? invoiceList.count + " فاتورة" : "…"
                                                  color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                                        }
                                        RowLayout {
                                            visible: invoiceList.count > 0
                                            Layout.fillWidth: true
                                            Layout.leftMargin: 12
                                            Layout.rightMargin: 14
                                            layoutDirection: Qt.RightToLeft
                                            spacing: 12
                                            ColHead { Layout.preferredWidth: page.colInvoiceWidth; text: "الفاتورة والتاريخ" }
                                            ColHead { Layout.preferredWidth: page.colFiguresWidth; text: "العيار والوزن والمصنعيات" }
                                            ColHead { Layout.fillWidth: true; text: "الحالة والإدخال" }
                                            Item { Layout.preferredWidth: page.colActionWidth }
                                        }
                                        ListView {
                                            id: invoiceList
                                            objectName: "supplierInvoiceList"
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: Math.max(invoiceList.count === 0 ? 50 : 0, contentHeight)
                                            interactive: false
                                            spacing: 6
                                            model: page.pvm ? page.pvm.invoices : []
                                            delegate: Rectangle {
                                                width: invoiceList.width
                                                height: page.compactInvoices ? 84 : 64
                                                radius: theme.radiusSmall
                                                color: theme.panel
                                                border.color: theme.border
                                                RowLayout {
                                                    anchors.fill: parent
                                                    anchors.leftMargin: 12; anchors.rightMargin: 14
                                                    layoutDirection: Qt.RightToLeft
                                                    spacing: 12
                                                    ColumnLayout {
                                                        Layout.preferredWidth: page.colInvoiceWidth
                                                        Layout.maximumWidth: page.colInvoiceWidth
                                                        Layout.fillWidth: false
                                                        spacing: 1
                                                        Txt { Layout.fillWidth: true; text: page.ltr(modelData.code); font.bold: true
                                                              color: theme.gold; font.pixelSize: theme.fontBodyStrong }
                                                        Txt { Layout.fillWidth: true; text: page.ltr(modelData.reference) + "  •  " + modelData.date
                                                              color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                                              wrapMode: page.compactInvoices ? Text.WordWrap : Text.NoWrap
                                                              elide: page.compactInvoices ? Text.ElideNone : Text.ElideLeft }
                                                    }
                                                    ColumnLayout {
                                                        Layout.preferredWidth: page.colFiguresWidth
                                                        Layout.maximumWidth: page.colFiguresWidth
                                                        Layout.fillWidth: false
                                                        spacing: 1
                                                        Txt { Layout.fillWidth: true; text: "عيار " + modelData.karat + "  •  " + modelData.weightText + " جرام"
                                                              font.bold: true }
                                                        Txt { Layout.fillWidth: true; text: "المصنعيات: " + page.ltr(modelData.makingText)
                                                              color: theme.textSecondary; font.pixelSize: theme.fontTableHeader
                                                              wrapMode: page.compactInvoices ? Text.WordWrap : Text.NoWrap
                                                              elide: page.compactInvoices ? Text.ElideNone : Text.ElideLeft }
                                                    }
                                                    ColumnLayout {
                                                        Layout.fillWidth: true
                                                        spacing: 1
                                                        Txt { objectName: "invoiceRowStatus"; Layout.fillWidth: true; text: modelData.statusText; font.bold: true
                                                              color: modelData.reversed ? theme.red : modelData.status === "POSTED" ? theme.green : theme.textSecondary }
                                                        Txt { Layout.fillWidth: true; visible: modelData.status === "POSTED"
                                                              text: "القطع: " + modelData.pieceCount + "  •  " + modelData.varianceText
                                                              color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                                              wrapMode: page.compactInvoices ? Text.WordWrap : Text.NoWrap
                                                              elide: page.compactInvoices ? Text.ElideNone : Text.ElideLeft }
                                                    }
                                                    Btn {
                                                        Layout.preferredWidth: page.colActionWidth
                                                        text: modelData.status === "POSTED" && !modelData.reversed ? "إدخال القطع" : "فتح"
                                                        onClicked: intakeDialog.openFor(modelData.id)
                                                    }
                                                }
                                            }
                                            Txt { objectName: "invoiceListState"; anchors.centerIn: parent
                                                  width: parent.width - 40; horizontalAlignment: Text.AlignHCenter
                                                  visible: invoiceList.count === 0
                                                  text: !page.pvm ? "" : page.pvm.invoicesState === "failed" ? "تعذر تحميل الفواتير. " + page.pvm.errorMessage
                                                        : page.pvm.invoicesState === "loaded" ? "لا توجد فواتير" : "جار تحميل الفواتير..."
                                                  color: !!page.pvm && page.pvm.invoicesState === "failed" ? theme.red : theme.textMuted
                                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                        }
                                    }

                                    // ---- gold tab: the supplier account ledger and, apart from it, the shop-owned gold at the intermediary (never combined)
                                    ColumnLayout {
                                        id: goldTab
                                        objectName: "goldTab"
                                        visible: page.tab === 1
                                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                        anchors.margins: 10
                                        spacing: 12
                                        ColumnLayout {
                                            id: goldAccountSection
                                            objectName: "goldAccountSection"
                                            visible: page.detail.hasAccount !== false
                                            Layout.fillWidth: true
                                            spacing: 6
                                            Txt { objectName: "goldAccountTitle"; Layout.fillWidth: true; text: "حساب المورد"; color: theme.gold; font.bold: true
                                                  visible: !!page.vm && page.vm.holdingsPanel.visible === true }
                                    ListView {
                                        id: goldList
                                        objectName: "supplierGoldLedger"
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: Math.max(goldList.count === 0 ? 28 : 0, contentHeight)
                                        interactive: false
                                        spacing: 5
                                        model: page.detail.goldLedger || []
                                        delegate: Rectangle {
                                            width: goldList.width
                                            height: Math.max(50, goldLedgerRow.implicitHeight + 14)       // grows with wrapped text
                                            radius: theme.radiusSmall
                                            color: theme.panel
                                            border.color: theme.border
                                            RowLayout {
                                                id: goldLedgerRow
                                                anchors.left: parent.left; anchors.right: parent.right
                                                anchors.verticalCenter: parent.verticalCenter
                                                anchors.leftMargin: 12; anchors.rightMargin: 14
                                                layoutDirection: Qt.RightToLeft
                                                spacing: 12
                                                // movement description (wraps), then date/time and reference, each kept as a left-to-right unit
                                                ColumnLayout {
                                                    Layout.preferredWidth: page.colLedgerWidth
                                                    Layout.maximumWidth: page.colLedgerWidth
                                                    Layout.fillWidth: false
                                                    spacing: 2
                                                    Txt { Layout.fillWidth: true; text: modelData.movement; font.bold: true
                                                          wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                                    Txt { Layout.fillWidth: true; text: modelData.time + "  •  " + page.ltr(modelData.reference)
                                                          color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                                          wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                                }
                                                // the movement value; in the narrowest window the running balance sits under it
                                                ColumnLayout {
                                                    Layout.fillWidth: page.compactInvoices
                                                    Layout.preferredWidth: page.compactInvoices ? 1 : page.colLedgerValueWidth
                                                    Layout.maximumWidth: page.compactInvoices ? 100000 : page.colLedgerValueWidth
                                                    spacing: 2
                                                    Txt { Layout.fillWidth: true; text: modelData.side + " " + modelData.weight + " جرام عيار " + modelData.karat
                                                          color: page.toneColor(modelData.tone); wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                                    Txt { objectName: "settlementDetail"; Layout.fillWidth: true; visible: modelData.detail !== ""; text: modelData.detail
                                                          color: theme.textSecondary; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                                    Txt { objectName: "settlementState"; Layout.fillWidth: true; visible: modelData.state !== ""; text: modelData.state
                                                          color: theme.amber; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                                    Txt { Layout.fillWidth: true; visible: page.compactInvoices
                                                          text: "الرصيد: " + modelData.balance; color: theme.textSecondary
                                                          font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                                }
                                                Txt { Layout.fillWidth: true; visible: !page.compactInvoices
                                                      text: "الرصيد: " + modelData.balance; color: theme.textSecondary
                                                      font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                                Btn {
                                                    objectName: "reverseSettlementButton"
                                                    visible: !!page.vm && page.vm.canReverseSettlement && modelData.canReverse === true
                                                    text: "عكس"
                                                    accent: theme.red
                                                    onClicked: reverseSettlementDialog.openFor(modelData)
                                                }
                                            }
                                        }
                                        Txt { anchors.centerIn: parent; visible: goldList.count === 0; text: "لا توجد حركات ذهب"
                                              color: theme.textMuted }
                                    }
                                        }
                                        ColumnLayout {
                                            id: holdingsGroup
                                            objectName: "holdingsGroup"
                                            visible: !!page.vm && page.vm.holdingsPanel.visible === true
                                            Layout.fillWidth: true
                                            spacing: 10
                        // Shop-owned gold physically at this intermediary (048 bars, 049 LOGO). Independent of the supplier account of a dual-role party.
                        Card {
                            objectName: "holdingsPanel"
                            Layout.fillHeight: false
                            title: "ذهب المحل عند المسوقجي"
                            accent: theme.amber
                            // one summary line per SOURCE KIND and karat / fineness (never merged across distinct sources)
                            Repeater {
                                model: page.vm ? (page.vm.holdingsPanel.holdGroups || []) : []
                                delegate: Txt { objectName: "holdingGroupRow"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                                color: theme.gold; font.bold: true; text: modelData.text }
                            }
                            Txt { objectName: "holdingsSummary"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.textSecondary
                                  visible: !!page.vm && page.vm.holdingsPanel.empty === true; text: page.vm ? (page.vm.holdingsPanel.summary || "") : "" }
                            Repeater {
                                model: page.vm ? (page.vm.holdingsPanel.pools || []) : []
                                delegate: Rectangle {
                                    id: poolRow
                                    readonly property bool more: page.moreOpen["lot" + modelData.poolId] === true
                                    // A whole-bar return is primary only when current ledger eligibility permits it; settlement stays under «المزيد».
                                    readonly property bool showReturn: !!page.vm && page.vm.canManageHoldings
                                    readonly property bool showSettle: !!page.vm && page.vm.canSettleFromHolding
                                    readonly property string primaryAction: (showReturn && modelData.returnable) ? "return" : ""
                                    Layout.fillWidth: true
                                    implicitHeight: holdRow.implicitHeight + 12
                                    radius: theme.radiusSmall
                                    color: theme.bgSecondary
                                    border.color: theme.border
                                    RowLayout {
                                        id: holdRow
                                        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                        anchors.leftMargin: 10; anchors.rightMargin: 10
                                        layoutDirection: Qt.RightToLeft
                                        spacing: 10
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            Layout.alignment: Qt.AlignTop
                                            spacing: 2
                                            Txt { objectName: "holdingSource"; Layout.fillWidth: true; text: modelData.title; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                            Txt { objectName: "holdingFigures"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.textSecondary
                                                  text: modelData.weight + " جرام • العيار الفعلي " + modelData.fineness }
                                            Txt { objectName: "holdingReason"; Layout.fillWidth: true; visible: modelData.reason !== ""; text: modelData.reason; color: theme.amber
                                                  font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                            Txt { objectName: "holdingHistoryNote"; Layout.fillWidth: true; visible: modelData.historyNote !== ""; text: modelData.historyNote
                                                  color: theme.textSecondary; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                            RowLayout {
                                                id: holdingMoreRow
                                                objectName: "holdingMoreRow"
                                                visible: poolRow.more
                                                Layout.fillWidth: true
                                                layoutDirection: Qt.RightToLeft
                                                spacing: 8
                                                Btn {
                                                    objectName: "holdingSettleButton"
                                                    Layout.preferredHeight: 30                    // compact action buttons, one height
                                                    visible: poolRow.showSettle
                                                    enabled: modelData.settleable === true && !page.vm.settlingFromHolding
                                                    text: "تسوية لمورد"
                                                    onClicked: holdingSettleDialog.openFor("LOT", modelData)
                                                }
                                                Btn {
                                                    objectName: "holdingReturnButton"
                                                    Layout.preferredHeight: 30                    // compact action buttons, one height
                                                    parent: poolRow.primaryAction === "return" ? poolPrimary : holdingMoreRow
                                                    Layout.fillWidth: poolRow.primaryAction === "return"
                                                    primary: poolRow.primaryAction === "return"
                                                    visible: poolRow.showReturn
                                                    enabled: modelData.returnable && !page.vm.returningHolding
                                                    text: "إرجاع السبكة كاملة للعهدة"
                                                    onClicked: holdingReturnDialog.openFor(modelData)
                                                }
                                                Item { Layout.fillWidth: true }
                                            }
                                        }
                                        ColumnLayout {
                                            Layout.alignment: Qt.AlignTop
                                            Layout.preferredWidth: 210
                                            Layout.minimumWidth: 210
                                            spacing: 6
                                            ColumnLayout { id: poolPrimary; Layout.fillWidth: true; spacing: 0 }      // the primary action always sits above «المزيد»
                                            Btn {
                                                objectName: "holdingMoreButton"
                                                Layout.preferredHeight: 30                    // compact action buttons, one height
                                                Layout.fillWidth: true
                                                visible: poolRow.showSettle || (poolRow.showReturn && poolRow.primaryAction !== "return")
                                                text: poolRow.more ? "إخفاء" : "المزيد"
                                                accent: theme.textSecondary
                                                onClicked: page.toggleMore("lot" + modelData.poolId)
                                            }
                                        }
                                    }
                                }
                            }
                            // pooled master-karat LOGO grams: a different kind of source from the assayed bars above
                            Repeater {
                                model: page.vm ? (page.vm.holdingsPanel.logoPools || []) : []
                                delegate: Rectangle {
                                    id: logoPoolRow
                                    readonly property bool more: page.moreOpen["logo" + modelData.poolId] === true
                                    readonly property bool showReturn: !!page.vm && page.vm.canManageHoldings
                                    readonly property bool showSettle: !!page.vm && page.vm.canSettleFromHolding
                                    readonly property string primaryAction: showReturn && modelData.returnable ? "return" : ""
                                    Layout.fillWidth: true
                                    implicitHeight: logoRow.implicitHeight + 12
                                    radius: theme.radiusSmall
                                    color: theme.bgSecondary
                                    border.color: theme.border
                                    RowLayout {
                                        id: logoRow
                                        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                        anchors.leftMargin: 10; anchors.rightMargin: 10
                                        layoutDirection: Qt.RightToLeft
                                        spacing: 10
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            Layout.alignment: Qt.AlignTop
                                            spacing: 2
                                            Txt { objectName: "logoSource"; Layout.fillWidth: true; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                                  text: "لوجو " + modelData.karat }
                                            Txt { objectName: "logoFigures"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.textSecondary
                                                  text: "المتبقي لدى المسوقجي " + modelData.outstanding + " جرام من " + modelData.initial + " جرام مُسلَّمة" }
                                            Txt { objectName: "logoReason"; Layout.fillWidth: true; visible: modelData.reason !== ""; text: modelData.reason; color: theme.amber
                                                  font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                            RowLayout {
                                                objectName: "logoMoreRow"
                                                visible: logoPoolRow.more
                                                Layout.fillWidth: true
                                                layoutDirection: Qt.RightToLeft
                                                spacing: 8
                                                Btn {
                                                    objectName: "logoSettleButton"
                                                    Layout.preferredHeight: 30                    // compact action buttons, one height
                                                    visible: logoPoolRow.showSettle
                                                    enabled: modelData.settleable === true && !page.vm.settlingFromHolding
                                                    text: "تسوية لمورد"
                                                    onClicked: holdingSettleDialog.openFor("LOGO", modelData)
                                                }
                                                Btn {
                                                    objectName: "logoReturnButton"
                                                    Layout.preferredHeight: 30
                                                    parent: logoPoolRow.primaryAction === "return" ? logoPrimary : logoMoreRow
                                                    Layout.fillWidth: logoPoolRow.primaryAction === "return"
                                                    visible: logoPoolRow.showReturn
                                                    enabled: modelData.returnable && !page.vm.returningHolding
                                                    text: "إرجاع ذهب للعهدة"
                                                    primary: logoPoolRow.primaryAction === "return"
                                                    onClicked: logoReturnDialog.openFor(modelData)
                                                }
                                                Item { Layout.fillWidth: true }
                                            }
                                        }
                                        ColumnLayout {
                                            Layout.alignment: Qt.AlignTop
                                            Layout.preferredWidth: 210
                                            Layout.minimumWidth: 210
                                            spacing: 6
                                            ColumnLayout { id: logoPrimary; Layout.fillWidth: true; spacing: 0 }
                                            Btn {
                                                objectName: "logoMoreButton"
                                                Layout.preferredHeight: 30                    // compact action buttons, one height
                                                Layout.fillWidth: true
                                                visible: logoPoolRow.showSettle || (logoPoolRow.showReturn && logoPoolRow.primaryAction !== "return")
                                                text: logoPoolRow.more ? "إخفاء" : "المزيد"
                                                accent: theme.textSecondary
                                                onClicked: page.toggleMore("logo" + modelData.poolId)
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // Pieces physically received at the shop (051): shop stock, assay UNKNOWN, restricted use. Their current stock stays visible; only history is under Details.
                        Card {
                            objectName: "receivedPiecesCard"
                            Layout.fillHeight: false
                            visible: (page.vm ? (page.vm.holdingsPanel.pieces || []) : []).length > 0
                            title: "ذهب مستلم بالمحل"
                            accent: theme.green
                            Txt { objectName: "receivedGroupRow"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.gold; font.bold: true
                                  text: page.vm ? ((page.vm.holdingsPanel.receivedGroup || {}).text || "") + " • " + ((page.vm.holdingsPanel.receivedGroup || {}).assayText || "") : "" }
                            Txt { objectName: "receivedRestriction"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.amber; font.pixelSize: theme.fontTableHeader
                                  text: page.vm ? ((page.vm.holdingsPanel.receivedGroup || {}).restriction || "") : "" }
                            Repeater {
                                model: page.vm ? (page.vm.holdingsPanel.pieces || []) : []
                                delegate: RowLayout {
                                    Layout.fillWidth: true
                                    layoutDirection: Qt.RightToLeft
                                    spacing: 8
                                    Txt { objectName: "receiptRow"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.textSecondary
                                          text: modelData.essential + " • " + modelData.stockText }
                                    ColumnLayout {
                                        Layout.preferredWidth: 180
                                        Layout.minimumWidth: 180
                                        spacing: 6
                                        Btn {
                                            objectName: "receiptMoreButton"
                                            Layout.preferredHeight: 30
                                            Layout.fillWidth: true
                                            visible: !!page.vm && modelData.canReverse === true
                                            text: page.moreOpen["receipt" + modelData.id] === true ? "إخفاء" : "المزيد"
                                            accent: theme.textSecondary
                                            onClicked: page.toggleMore("receipt" + modelData.id)
                                        }
                                        RowLayout {
                                            id: receiptMoreRow
                                            objectName: "receiptMoreRow"
                                            visible: page.moreOpen["receipt" + modelData.id] === true
                                            Layout.fillWidth: true
                                            layoutDirection: Qt.RightToLeft
                                            Btn {
                                                objectName: "receiptReverseButton"
                                                Layout.preferredHeight: 30
                                                visible: !!page.vm && modelData.canReverse === true
                                                text: "تصحيح تسجيل"
                                                accent: theme.red
                                                onClicked: reverseReceiptDialog.openFor(modelData)
                                            }
                                            Item { Layout.fillWidth: true }
                                        }
                                    }
                                }
                            }
                        }

                        // Details: codes, original weights, pure grams, rounding and the whole history. Collapsed by default; no permission is bypassed.
                        Btn {
                            objectName: "holdingDetailsToggle"
                            Layout.preferredHeight: 30                    // compact action buttons, one height
                            Layout.alignment: Qt.AlignRight
                            text: page.holdDetails ? "إخفاء التفاصيل" : "التفاصيل والمراجع"
                            accent: theme.textSecondary
                            onClicked: page.holdDetails = !page.holdDetails
                        }
                        Card {
                            objectName: "holdingDetailsCard"
                            Layout.fillHeight: false
                            visible: page.holdDetails
                            title: "تفاصيل ذهب المسوقجي ومراجعه"
                            accent: theme.textSecondary
                            Txt { objectName: "holdingsDetailsNote"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                  color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                  text: page.vm ? (page.vm.holdingsPanel.note || "") : "" }
                            Txt { objectName: "receiptDetailsNote"; Layout.fillWidth: true; visible: (page.vm ? (page.vm.holdingsPanel.receipts || []) : []).length > 0
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                  text: page.vm ? (page.vm.holdingsPanel.receiptNote || "") : "" }
                            Repeater {
                                model: page.vm ? (page.vm.holdingsPanel.pools || []) : []
                                delegate: Txt { objectName: "holdingRefs"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.textMuted
                                                font.pixelSize: theme.fontTableHeader
                                                text: modelData.source + " • الوزن الأصلي " + modelData.original + " جرام • الذهب الصافي " + page.ltr(modelData.pure) + " • " + modelData.document
                                                      + " • " + modelData.custodyEntry + " • العهدة الأصلية " + modelData.tripCode + " • " + modelData.date
                                                      + (modelData.detailsNote !== "" ? " • " + modelData.detailsNote : "")
                                                      + (modelData.allocationRefs !== "" ? " • مرجع التخصيص السابق " + modelData.allocationRefs : "") }
                            }
                            Repeater {
                                model: page.vm ? (page.vm.holdingsPanel.logoPools || []) : []
                                delegate: Txt { objectName: "logoRefs"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.textMuted
                                                font.pixelSize: theme.fontTableHeader
                                                text: "لوجو " + modelData.karat + " • " + modelData.document + " • " + modelData.custodyEntry + " • العهدة الأصلية " + modelData.tripCode + " • " + modelData.date
                                                      + (modelData.detailsNote !== "" ? " • " + modelData.detailsNote : "") }
                            }
                            Repeater {
                                model: page.vm ? (page.vm.holdingsPanel.logoHistory || []) : []
                                delegate: Txt { objectName: "logoHistoryRow"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                                color: modelData.reversed ? theme.textMuted : theme.textSecondary; font.pixelSize: theme.fontTableHeader
                                                text: modelData.kindText + " " + modelData.code + " • " + modelData.text + (modelData.state !== "" ? " • " + modelData.state : "") }
                            }
                            Txt { objectName: "holdingSettlementsTitle"; Layout.fillWidth: true; text: "تسويات الذهب لموردين من رصيد المسوقجي"; color: theme.gold; font.bold: true
                                  visible: (page.vm ? (page.vm.holdingsPanel.settlements || []) : []).length > 0 }
                            Repeater {
                                model: page.vm ? (page.vm.holdingsPanel.settlements || []) : []
                                delegate: RowLayout {
                                    Layout.fillWidth: true
                                    layoutDirection: Qt.RightToLeft
                                    spacing: 8
                                    Txt { objectName: "holdingSettlementRow"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                          color: modelData.reversed ? theme.textMuted : theme.textSecondary; font.pixelSize: theme.fontTableHeader
                                          text: modelData.code + " • " + modelData.text + (modelData.state !== "" ? " • " + modelData.state : "") }
                                    Btn {
                                        objectName: "holdingSettlementReverseButton"
                                        Layout.preferredHeight: 30                    // compact action buttons, one height
                                        visible: !!page.vm && page.vm.canReverseSettlement && modelData.canReverse === true
                                        text: "تصحيح تسجيل"
                                        accent: theme.red
                                        onClicked: reverseSettlementDialog.openFor({ settlementId: modelData.id, reference: modelData.code, detail: modelData.text,
                                                                                     effect: modelData.effectText, fromHolding: true })
                                    }
                                }
                            }
                            Txt { objectName: "receiptHistoryTitle"; Layout.fillWidth: true; text: "سجل الاستلام الفعلي"; color: theme.gold; font.bold: true
                                  visible: (page.vm ? (page.vm.holdingsPanel.receipts || []) : []).length > 0 }
                            Repeater {
                                model: page.vm ? (page.vm.holdingsPanel.receipts || []) : []
                                delegate: Txt { objectName: "receiptHistoryRow"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                                color: modelData.reversed ? theme.textMuted : theme.textSecondary; font.pixelSize: theme.fontTableHeader
                                                text: modelData.code + " ← " + modelData.piece + " • " + modelData.text + " • " + modelData.stockText
                                                      + (modelData.state !== "" ? " • " + modelData.state : "") }
                            }
                            Txt { Layout.fillWidth: true; text: "سجل التسليم والرجوع"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                  visible: (page.vm ? (page.vm.holdingsPanel.history || []) : []).length > 0 }
                            Repeater {
                                model: page.vm ? (page.vm.holdingsPanel.history || []) : []
                                delegate: Txt { objectName: "holdingHistoryRow"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                                color: modelData.reversed ? theme.textMuted : theme.textSecondary; font.pixelSize: theme.fontTableHeader
                                                text: modelData.kindText + " " + modelData.code + " • " + modelData.text + (modelData.state !== "" ? " • " + modelData.state : "") }
                            }
                        }
                                        }

                                        // ---- marketer custody (055): the shop's CASH and 21K GOLD held in trust: apart from the supplier account, never combined with it
                                        ColumnLayout {
                                            id: marketerGroup
                                            objectName: "marketerGroup"
                                            visible: !!page.mvm && page.mvm.visible === true
                                            Layout.fillWidth: true
                                            spacing: 10
                                            Card {
                                                objectName: "marketerPanel"
                                                Layout.fillHeight: false
                                                title: "عهدة المسوقجي (أمانة للمحل)"
                                                accent: theme.amber
                                                Txt { objectName: "marketerIsolationNote"; Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                                      wrapMode: Text.WordWrap; elide: Text.ElideNone; text: page.mvm ? (page.mvm.account.isolationNote || "") : "" }
                                                RowLayout {
                                                    Layout.fillWidth: true
                                                    layoutDirection: Qt.RightToLeft
                                                    spacing: 10
                                                    Rectangle {
                                                        Layout.preferredWidth: 1; Layout.fillWidth: true; implicitHeight: cashFigure.implicitHeight + 14
                                                        radius: theme.radiusSmall; color: theme.panel; border.color: theme.border
                                                        ColumnLayout {
                                                            id: cashFigure
                                                            anchors.fill: parent; anchors.margins: 8; spacing: 0
                                                            Txt { Layout.fillWidth: true; text: "النقدية لدى المسوقجي"; color: theme.textSecondary; font.pixelSize: theme.fontTableHeader }
                                                            Txt { objectName: "marketerCashValue"; Layout.fillWidth: true; font.bold: true; font.pixelSize: theme.fontSection
                                                                  color: !!page.mvm && page.mvm.account.cashZero ? theme.textMuted : theme.amber
                                                                  text: page.mvm ? (page.mvm.account.cashText || "") : ""; elide: Text.ElideNone }
                                                        }
                                                    }
                                                    Rectangle {
                                                        Layout.preferredWidth: 1; Layout.fillWidth: true; implicitHeight: goldFigure.implicitHeight + 14
                                                        radius: theme.radiusSmall; color: theme.panel; border.color: theme.border
                                                        ColumnLayout {
                                                            id: goldFigure
                                                            anchors.fill: parent; anchors.margins: 8; spacing: 0
                                                            Txt { Layout.fillWidth: true; text: "ذهب لدى المسوقجي (وزنة عيار 21)"; color: theme.textSecondary; font.pixelSize: theme.fontTableHeader }
                                                            Txt { objectName: "marketerGoldValue"; Layout.fillWidth: true; font.bold: true; font.pixelSize: theme.fontSection
                                                                  color: !!page.mvm && page.mvm.account.goldZero ? theme.textMuted : theme.gold
                                                                  text: page.mvm ? (page.mvm.account.goldText || "") : ""; elide: Text.ElideNone }
                                                        }
                                                    }
                                                }
                                                Flow {
                                                    Layout.fillWidth: true
                                                    layoutDirection: Qt.RightToLeft
                                                    spacing: 8
                                                    visible: !!page.mvm && page.mvm.canManage
                                                    Btn { objectName: "marketerCashHandoverButton"; text: "تسليم نقدية"; enabled: !!page.mvm && !page.mvm.writing
                                                          onClicked: marketerDialog.openFor("CASH_HANDOVER") }
                                                    Btn { objectName: "marketerCashReturnButton"; text: "استرجاع نقدية"; enabled: !!page.mvm && !page.mvm.writing
                                                          onClicked: marketerDialog.openFor("CASH_RETURN") }
                                                    Btn { objectName: "marketerPurchaseButton"; text: "شراء ذهب نقدي"; enabled: !!page.mvm && !page.mvm.writing
                                                          onClicked: marketerDialog.openFor("PURCHASE") }
                                                    Btn { objectName: "marketerSaleButton"; text: "بيع ذهب"; enabled: !!page.mvm && !page.mvm.writing
                                                          onClicked: marketerDialog.openFor("SALE") }
                                                    Btn { objectName: "marketerGoldReturnButton"; text: "استرجاع ذهب 21 للمحل"; enabled: !!page.mvm && !page.mvm.writing
                                                          onClicked: marketerDialog.openFor("GOLD_RETURN") }
                                                    Btn { objectName: "marketerSettleButton"; text: "تسليم ذهب لمورد"; visible: !!page.mvm && page.mvm.canSettle
                                                          enabled: !!page.mvm && !page.mvm.writing; onClicked: marketerDialog.openFor("SETTLE") }
                                                }
                                                Txt { objectName: "marketerMessage"; Layout.fillWidth: true; visible: !!page.mvm && page.mvm.message !== ""; color: theme.green
                                                      wrapMode: Text.WordWrap; elide: Text.ElideNone; text: page.mvm ? page.mvm.message : "" }
                                                Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                                      visible: (page.mvm ? (page.mvm.account.documents || []) : []).length > 0; text: "سجل مستندات عهدة المسوقجي" }
                                                Repeater {
                                                    model: page.mvm ? (page.mvm.account.documents || []) : []
                                                    delegate: Rectangle {
                                                        id: marketerDocRow
                                                        required property var modelData
                                                        objectName: "marketerDocumentRow"
                                                        Layout.fillWidth: true
                                                        implicitHeight: marketerDocLine.implicitHeight + 12
                                                        radius: theme.radiusSmall; color: theme.panel; border.color: theme.border
                                                        opacity: modelData.reversed ? 0.6 : 1
                                                        RowLayout {
                                                            id: marketerDocLine
                                                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                                            anchors.leftMargin: 10; anchors.rightMargin: 12
                                                            layoutDirection: Qt.RightToLeft
                                                            spacing: 8
                                                            ColumnLayout {
                                                                Layout.fillWidth: true; spacing: 1
                                                                Txt { Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; font.bold: true
                                                                      text: marketerDocRow.modelData.kindText + "  " + page.ltr(marketerDocRow.modelData.code) }
                                                                Txt { objectName: "marketerDocumentText"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                                                      color: theme.textSecondary; text: marketerDocRow.modelData.text }
                                                                Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                                                      text: marketerDocRow.modelData.date + (marketerDocRow.modelData.state !== "" ? " • " + marketerDocRow.modelData.state : "") }
                                                            }
                                                            Btn { objectName: "marketerReverseButton"; visible: marketerDocRow.modelData.canReverse === true; text: "عكس"; accent: theme.red
                                                                  onClicked: marketerReverseDialog.openFor(marketerDocRow.modelData, false) }
                                                        }
                                                    }
                                                }
                                                Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                                      visible: (page.mvm ? (page.mvm.account.settlements || []) : []).length > 0; text: "تسويات الذهب للموردين من عهدة المسوقجي" }
                                                Repeater {
                                                    model: page.mvm ? (page.mvm.account.settlements || []) : []
                                                    delegate: Rectangle {
                                                        id: marketerSettlementRow
                                                        required property var modelData
                                                        objectName: "marketerSettlementRow"
                                                        Layout.fillWidth: true
                                                        implicitHeight: marketerSettlementLine.implicitHeight + 12
                                                        radius: theme.radiusSmall; color: theme.panel; border.color: theme.border
                                                        opacity: modelData.reversed ? 0.6 : 1
                                                        RowLayout {
                                                            id: marketerSettlementLine
                                                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                                            anchors.leftMargin: 10; anchors.rightMargin: 12
                                                            layoutDirection: Qt.RightToLeft
                                                            spacing: 8
                                                            Txt { Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.textSecondary
                                                                  text: page.ltr(marketerSettlementRow.modelData.code) + " — " + marketerSettlementRow.modelData.text
                                                                        + (marketerSettlementRow.modelData.state !== "" ? " • " + marketerSettlementRow.modelData.state : "") }
                                                            Btn { objectName: "marketerReverseSettlementButton"; visible: marketerSettlementRow.modelData.canReverse === true; text: "عكس"
                                                                  accent: theme.red; onClicked: marketerReverseDialog.openFor(marketerSettlementRow.modelData, true) }
                                                        }
                                                    }
                                                }
                                                Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                                      visible: (page.mvm ? (page.mvm.account.tripLegs || []) : []).length > 0; text: "حركات استحقاقه من رحلات السوق الدفترية (مفسِّرة لتغيّر الرصيد)" }
                                                Repeater {
                                                    model: page.mvm ? (page.mvm.account.tripLegs || []) : []
                                                    delegate: Rectangle {
                                                        id: marketerTripLegRow
                                                        required property var modelData
                                                        objectName: "marketerTripLegRow"
                                                        Layout.fillWidth: true
                                                        implicitHeight: marketerTripLegLine.implicitHeight + 12
                                                        radius: theme.radiusSmall; color: theme.panel; border.color: theme.border
                                                        opacity: modelData.reversal ? 0.6 : 1
                                                        ColumnLayout {
                                                            id: marketerTripLegLine
                                                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                                            anchors.leftMargin: 10; anchors.rightMargin: 12
                                                            spacing: 1
                                                            Txt { Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.textSecondary; text: marketerTripLegRow.modelData.text }
                                                            RowLayout {
                                                                Layout.fillWidth: true
                                                                layoutDirection: Qt.RightToLeft
                                                                spacing: 8
                                                                Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                                                      text: marketerTripLegRow.modelData.date + " • " + marketerTripLegRow.modelData.note }
                                                                Btn { objectName: "marketerTripLegOpen"; text: "فتح الرحلة"; accent: theme.textSecondary; implicitHeight: 26
                                                                      onClicked: page.tripRequested(marketerTripLegRow.modelData.tripId) }
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }

                                    // ---- making ledger
                                    ListView {
                                        id: makingList
                                        objectName: "supplierMakingLedger"
                                        visible: page.tab === 2
                                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                        anchors.margins: 10
                                        height: contentHeight
                                        interactive: false
                                        spacing: 5
                                        model: page.detail.makingLedger || []
                                        delegate: Rectangle {
                                            width: makingList.width
                                            height: Math.max(50, makingLedgerRow.implicitHeight + 14)       // grows with wrapped text
                                            radius: theme.radiusSmall
                                            color: theme.panel
                                            border.color: theme.border
                                            RowLayout {
                                                id: makingLedgerRow
                                                anchors.left: parent.left; anchors.right: parent.right
                                                anchors.verticalCenter: parent.verticalCenter
                                                anchors.leftMargin: 12; anchors.rightMargin: 14
                                                layoutDirection: Qt.RightToLeft
                                                spacing: 12
                                                // movement description (wraps), then date/time and reference, each kept as a left-to-right unit
                                                ColumnLayout {
                                                    Layout.preferredWidth: page.colLedgerWidth
                                                    Layout.maximumWidth: page.colLedgerWidth
                                                    Layout.fillWidth: false
                                                    spacing: 2
                                                    Txt { Layout.fillWidth: true; text: modelData.movement; font.bold: true
                                                          wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                                    Txt { Layout.fillWidth: true; text: modelData.time + "  •  " + page.ltr(modelData.reference)
                                                          color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                                          wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                                    Txt { objectName: "makingPaymentDetail"; Layout.fillWidth: true; visible: modelData.detail !== ""
                                                          text: modelData.detail; color: theme.textSecondary
                                                          font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                                    Txt { objectName: "makingPaymentState"; Layout.fillWidth: true; visible: modelData.state !== ""
                                                          text: modelData.state; color: modelData.reversed ? theme.amber : theme.textMuted
                                                          font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                                }
                                                // the movement value; in the narrowest window the running balance sits under it
                                                ColumnLayout {
                                                    Layout.fillWidth: page.compactInvoices
                                                    Layout.preferredWidth: page.compactInvoices ? 1 : page.colLedgerValueWidth
                                                    Layout.maximumWidth: page.compactInvoices ? 100000 : page.colLedgerValueWidth
                                                    spacing: 2
                                                    Txt { Layout.fillWidth: true; text: modelData.side + " " + modelData.amount + " مصنعيات"
                                                          color: page.toneColor(modelData.tone); wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                                    Txt { Layout.fillWidth: true; visible: page.compactInvoices
                                                          text: "الرصيد: " + modelData.balance; color: theme.textSecondary
                                                          font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                                }
                                                Txt { Layout.fillWidth: true; visible: !page.compactInvoices
                                                      text: "الرصيد: " + modelData.balance; color: theme.textSecondary
                                                      font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                                Btn {
                                                    objectName: "reversePaymentButton"
                                                    visible: !!page.vm && page.vm.canReversePayment && modelData.canReverse === true
                                                    text: "عكس"
                                                    accent: theme.red
                                                    onClicked: reverseDialog.openFor(modelData)
                                                }
                                            }
                                        }
                                        Txt { anchors.horizontalCenter: parent.horizontalCenter; y: 20; visible: makingList.count === 0; text: "لا توجد حركات مصنعيات"
                                              color: theme.textMuted }
                                    }
                                }
                            }
                        }
                    }
                    }
                }
            }
        }
    }

    Txt {
        anchors.centerIn: parent
        visible: !page.vm || !page.vm.canView
        text: "لا تملك صلاحية عرض الموردين."
        color: theme.textMuted
    }

    // ---------------------------------------------------------------- supplier add / edit
    Popup {
        id: supplierDialog
        objectName: "supplierDialog"
        property string editId: ""
        property bool karatLocked: false
        property bool wasSupplier: true
        function openFor(data) {
            editId = data ? data.id : ""
            wasSupplier = data ? data.isSupplier !== false : true
            roleSupplier.checked = data ? data.isSupplier !== false : true
            roleIntermediary.checked = data ? data.isIntermediary === true : false
            karatLocked = !!data && data.karatResolved === true && data.karatLocked === true
            supplierKaratCombo.currentIndex = data ? page.karatChoiceIndex(data.karatId) : 0
            nameField.text = data ? data.name : ""
            phoneField.text = data ? data.phone : ""
            notesField.text = data ? data.notes : ""
            if (page.vm) page.vm.clearMessage()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(460, page.width - 32)
        modal: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 8
            Txt { Layout.fillWidth: true; text: supplierDialog.editId === "" ? "مورد جديد" : "تعديل بيانات المورد"
                  color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection }
            Txt { Layout.fillWidth: true; text: "اسم المورد *" }
            Field { id: nameField; objectName: "supplierNameField"; Layout.fillWidth: true }
            Txt { Layout.fillWidth: true; text: "الدور *" }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 12
                CheckBox { id: roleSupplier; objectName: "supplierRoleSupplier"; text: "مورد"; checked: true; font.family: "Cairo"
                           contentItem: Label { text: parent.text; font: parent.font; color: theme.textSecondary
                                                leftPadding: parent.indicator.width + 6; horizontalAlignment: Text.AlignRight } }
                CheckBox { id: roleIntermediary; objectName: "supplierRoleIntermediary"; text: "مسوقجي"; font.family: "Cairo"
                           contentItem: Label { text: parent.text; font: parent.font; color: theme.textSecondary
                                                leftPadding: parent.indicator.width + 6; horizontalAlignment: Text.AlignRight } }
                Item { Layout.fillWidth: true }
            }
            Txt { objectName: "roleRequiredHint"; Layout.fillWidth: true; visible: !roleSupplier.checked && !roleIntermediary.checked
                  color: theme.amber; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "اختر دورًا واحدًا على الأقل: مورد أو مسوقجي." }
            // Each supplier is dedicated to ONE karat; its invoices inherit it. Locked once an invoice exists. A market intermediary has none.
            Txt { Layout.fillWidth: true; visible: roleSupplier.checked; text: "العيار *" }
            Combo { id: supplierKaratCombo; objectName: "supplierKaratCombo"; Layout.fillWidth: true; visible: roleSupplier.checked
                    model: page.karatChoices; textRole: "label"; enabled: !supplierDialog.karatLocked }
            Txt { Layout.fillWidth: true; visible: supplierDialog.karatLocked && roleSupplier.checked; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                  wrapMode: Text.WordWrap; elide: Text.ElideNone; text: "لا يتغير عيار المورد بعد وجود فاتورة له." }
            Txt { Layout.fillWidth: true; text: "رقم الهاتف" }
            Field { id: phoneField; objectName: "supplierPhoneField"; Layout.fillWidth: true }
            Txt { Layout.fillWidth: true; text: "ملاحظات" }
            Field { id: notesField; objectName: "supplierNotesField"; Layout.fillWidth: true }
            Txt { Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""; text: page.vm ? page.vm.errorMessage : ""
                  color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "saveSupplierButton"
                    text: "حفظ"
                    primary: true
                    enabled: nameField.text.trim().length > 0 && !!page.vm && !page.vm.busy
                             && (roleSupplier.checked || roleIntermediary.checked)
                             && (!roleSupplier.checked
                                 || (supplierDialog.editId !== "" && supplierDialog.wasSupplier) || supplierKaratCombo.currentIndex > 0)
                    onClicked: {
                        var karat = roleSupplier.checked ? page.karatChoices[supplierKaratCombo.currentIndex].id : 0
                        if (supplierDialog.editId === "") page.vm.createParty(nameField.text, phoneField.text, notesField.text, karat,
                                                                              roleSupplier.checked, roleIntermediary.checked)
                        else page.vm.updateParty(supplierDialog.editId, nameField.text, phoneField.text, notesField.text, karat,
                                                 roleSupplier.checked, roleIntermediary.checked)
                        supplierDialog.close()
                    }
                }
                Btn { text: "إلغاء"; accent: theme.textSecondary; onClicked: supplierDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    Popup {
        id: deleteConfirm
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(420, page.width - 32)
        modal: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.red }
        contentItem: ColumnLayout {
            spacing: 10
            Txt { Layout.fillWidth: true; text: "حذف المورد نهائيًا؟"; color: theme.red; font.bold: true }
            Txt { Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "يُحذف المورد غير المستخدم فقط. المورد الذي له فواتير أو حركات يُرفض حذفه (أوقفه بدلاً من ذلك)." }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn { text: "حذف"; accent: theme.red; primary: true
                      onClicked: { deleteConfirm.close(); page.vm.deleteSupplier(page.detail.id) } }
                Btn { text: "إلغاء"; accent: theme.textSecondary; onClicked: deleteConfirm.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }


    // ---------------------------------------------------------------- making payment (دفع مصنعيات)
    // Cash from MAIN_SAFE or a transfer from a selected active bank account. The figures (current / resulting position,
    // even a credit balance) come from supplierVM; nothing is calculated here and the service re-checks every right.
    Popup {
        id: payDialog
        objectName: "payMakingDialog"
        property string requestKey: ""
        property string method: "CASH"
        property var preview: ({ valid: false, error: "", afterText: "", afterParts: ({}), afterTone: "settled", note: "" })
        readonly property var ctx: page.vm ? page.vm.payContext : ({})
        readonly property bool ready: !!ctx && ctx.supplierId !== undefined
        // Entry 0 is a placeholder: a ComboBox whose model arrives after it is created would otherwise silently pick the
        // first account, and a payment must never leave an account the employee did not choose.
        readonly property var bankChoices: [{ id: "", text: "اختر الحساب" }].concat(ready ? ctx.banks : [])
        readonly property bool accountReady: method === "CASH" ? (ready && ctx.safeReady === true)
                                                              : (payBankCombo.currentIndex > 0)
        readonly property bool canSubmit: ready && preview.valid === true && accountReady
                                          && payDateField.text.trim().length > 0 && !!page.vm && !page.vm.paying
        function recompute() {
            if (page.vm) preview = page.vm.previewPayment(payAmountField.text)
        }
        function openFor() {
            if (!page.vm || !page.hasSelection) return
            requestKey = page.vm.newRequestKey()
            method = "CASH"
            payBankCombo.currentIndex = 0
            payAmountField.text = ""
            payDateField.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            payReferenceField.text = ""
            payNotesField.text = ""
            preview = ({ valid: false, error: "", afterText: "", afterParts: ({}), afterTone: "settled", note: "" })
            page.vm.loadPaymentContext(page.detail.id)
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(560, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 600, 590)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection
                  text: "دفع مصنعيات — " + (payDialog.ready ? payDialog.ctx.supplierName : (page.detail.name || ""))
                        + (payDialog.ready && payDialog.ctx.supplierActive === false ? "  (موقوف)" : "") }
            Txt { objectName: "payInactiveNote"; Layout.fillWidth: true; visible: payDialog.ready && payDialog.ctx.supplierActive === false
                  text: "المورد موقوف: لا تُنشأ له فواتير شراء جديدة، لكن يمكن تسجيل دفعة مصنعيات على رصيده."
                  color: theme.amber; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: payColumn.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: payColumn
                    width: parent.width - 12
                    spacing: 6
                    Txt { Layout.fillWidth: true; visible: !payDialog.ready; text: "جارٍ تحميل موقف المصنعيات…"
                          color: theme.textMuted }
                    Rectangle {
                        Layout.fillWidth: true
                        visible: payDialog.ready
                        implicitHeight: currentColumn.implicitHeight + 14
                        radius: 4
                        color: theme.bgTertiary
                        border.color: theme.border
                        ColumnLayout {
                            id: currentColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 10; anchors.rightMargin: 10
                            spacing: 2
                            Txt { Layout.fillWidth: true; text: "موقف المصنعيات الحالي"; color: theme.textMuted
                                  font.pixelSize: theme.fontTableHeader }
                            Txt { objectName: "payCurrentPosition"; Layout.fillWidth: true; text: payDialog.ctx.currentText || ""
                                  color: page.toneColor(payDialog.ctx.currentTone); font.bold: true
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "طريقة الدفع *" }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Btn { objectName: "payMethodCash"; text: "نقدًا من الخزنة الرئيسية"; primary: payDialog.method === "CASH"
                              onClicked: payDialog.method = "CASH" }
                        Btn { objectName: "payMethodBank"; text: "تحويل بنكي"; primary: payDialog.method === "BANK"
                              onClicked: payDialog.method = "BANK" }
                        Item { Layout.fillWidth: true }
                    }
                    Txt { Layout.fillWidth: true; visible: payDialog.method === "CASH" && payDialog.ready
                          text: payDialog.ctx.safeReady === true ? "يُخصم المبلغ من: " + (payDialog.ctx.safeName || "")
                                                                  : "الخزنة الرئيسية غير متاحة."
                          color: payDialog.ctx.safeReady === true ? theme.textSecondary : theme.red
                          wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    Txt { Layout.fillWidth: true; visible: payDialog.method === "BANK"; text: "الحساب البنكي *" }
                    Combo {
                        id: payBankCombo
                        objectName: "payBankCombo"
                        Layout.fillWidth: true
                        visible: payDialog.method === "BANK"
                        model: payDialog.bankChoices
                        textRole: "text"
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "المبلغ (ج) *" }
                            Field { id: payAmountField; objectName: "payAmountField"; Layout.fillWidth: true
                                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                                    onTextChanged: payDialog.recompute() }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "تاريخ الدفع (yyyy-MM-dd) *" }
                            Field { id: payDateField; objectName: "payDateField"; Layout.fillWidth: true }
                        }
                    }
                    Txt { Layout.fillWidth: true; visible: payAmountField.text.trim().length > 0 && payDialog.preview.error !== ""
                          text: payDialog.preview.error; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    Txt { Layout.fillWidth: true; text: "المرجع" }
                    Field { id: payReferenceField; objectName: "payReferenceField"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: payNotesField; objectName: "payNotesField"; Layout.fillWidth: true }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: afterColumn.implicitHeight + 14
                        radius: 4
                        color: theme.bgTertiary
                        border.color: payDialog.preview.valid ? page.toneColor(payDialog.preview.afterTone) : theme.border
                        ColumnLayout {
                            id: afterColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 10; anchors.rightMargin: 10
                            spacing: 2
                            Txt { Layout.fillWidth: true; text: "موقف المصنعيات بعد الدفع"; color: theme.textMuted
                                  font.pixelSize: theme.fontTableHeader }
                            Txt { objectName: "payResultPosition"; Layout.fillWidth: true
                                  text: payDialog.preview.valid ? payDialog.preview.afterText : "—"
                                  color: payDialog.preview.valid ? page.toneColor(payDialog.preview.afterTone) : theme.textMuted
                                  font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "payCreditNote"; Layout.fillWidth: true; visible: payDialog.preview.note !== ""
                                  text: payDialog.preview.note; color: theme.amber; font.pixelSize: theme.fontTableHeader
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                        }
                    }
                }
            }
            Txt { objectName: "payError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmPayMakingButton"
                    text: !!page.vm && page.vm.paying ? "جارٍ الحفظ…" : "تسجيل الدفعة"
                    primary: true
                    enabled: payDialog.canSubmit
                    onClicked: page.vm.payMaking(payDialog.ctx.supplierId, payDialog.method,
                                                 payDialog.method === "BANK" ? payDialog.bankChoices[payBankCombo.currentIndex].id : "",
                                                 payAmountField.text, payDateField.text, payReferenceField.text,
                                                 payNotesField.text, payDialog.requestKey)
                }
                Btn { objectName: "cancelPayMakingButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.paying; onClicked: payDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- reverse a making payment (OWNER only)
    Popup {
        id: reverseDialog
        objectName: "reversePaymentDialog"
        property string paymentId: ""
        property string code: ""
        property string amountText: ""
        property string detailText: ""
        function openFor(row) {
            paymentId = row.paymentId
            code = row.reference
            amountText = row.amount
            detailText = row.detail
            reverseReasonField.text = ""
            if (page.vm) page.vm.clearMessage()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(480, page.width - 24)
        modal: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.red }
        contentItem: ColumnLayout {
            spacing: 8
            Txt { Layout.fillWidth: true; text: "عكس دفعة مصنعيات " + page.ltr(reverseDialog.code); color: theme.red
                  font.bold: true; font.pixelSize: theme.fontSection }
            Txt { Layout.fillWidth: true; text: reverseDialog.amountText + "  •  " + reverseDialog.detailText
                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
            Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                  wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "يُعاد المبلغ إلى نفس الحساب الأصلي ويرجع موقف المصنعيات كما كان. تبقى الدفعة الأصلية في السجل ولا تُعدّل." }
            Txt { Layout.fillWidth: true; text: "سبب العكس *" }
            Field { id: reverseReasonField; objectName: "reverseReasonField"; Layout.fillWidth: true }
            Txt { objectName: "reverseError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmReversePaymentButton"
                    text: !!page.vm && page.vm.reversing ? "جارٍ العكس…" : "عكس الدفعة"
                    accent: theme.red
                    primary: true
                    enabled: reverseReasonField.text.trim().length > 0 && !!page.vm && !page.vm.reversing
                    onClicked: page.vm.reversePayment(reverseDialog.paymentId, reverseReasonField.text)
                }
                Btn { text: "إلغاء"; accent: theme.textSecondary; enabled: !!page.vm && !page.vm.reversing
                      onClicked: reverseDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- reverse a posted supplier purchase invoice (OWNER only, 052)
    // The preview comes from the service (the exact opposite entries and the supplier's balances before / after); QML does no arithmetic.
    Popup {
        id: reverseInvoiceDialog
        objectName: "reverseInvoiceDialog"
        property string invoiceId: ""
        property string code: ""
        readonly property var preview: page.pvm ? page.pvm.reversal : ({})
        readonly property bool loaded: !!preview && preview.id === invoiceId
        function openFor(inv) {
            invoiceId = inv.id
            code = inv.code
            reverseInvoiceReason.text = ""
            if (page.pvm) { page.pvm.clearMessage(); page.pvm.loadReversal(inv.id) }
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(560, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 600, 600)
        modal: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.red }
        contentItem: ColumnLayout {
            spacing: 8
            Txt { Layout.fillWidth: true; text: "عكس فاتورة المورد " + page.ltr(reverseInvoiceDialog.code); color: theme.red
                  font.bold: true; font.pixelSize: theme.fontSection }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: reverseInvoiceBody.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: reverseInvoiceBody
                    width: parent.width - 12
                    spacing: 6
                    Txt { objectName: "reverseInvoiceLoading"; Layout.fillWidth: true; visible: !reverseInvoiceDialog.loaded && (!page.pvm || page.pvm.errorMessage === "")
                          text: "جارٍ تحميل أثر العكس…"; color: theme.textMuted }
                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: reverseInvoiceDialog.loaded
                        spacing: 6
                        Txt { Layout.fillWidth: true; font.bold: true
                              text: reverseInvoiceDialog.loaded ? "المورد: " + reverseInvoiceDialog.preview.supplierName : "" }
                        Txt { Layout.fillWidth: true; color: theme.textSecondary; font.pixelSize: theme.fontTableHeader
                              text: "يُسجَّل عكس كامل بالقيم المخزنة للفاتورة الأصلية، وتبقى الفاتورة وقيودها في السجل دون تعديل." }
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: effectColumn.implicitHeight + 14
                            radius: theme.radiusSmall
                            color: theme.panel
                            border.color: theme.border
                            ColumnLayout {
                                id: effectColumn
                                anchors.fill: parent; anchors.margins: 7
                                spacing: 3
                                Txt { objectName: "reverseInvoiceGoldEffect"; Layout.fillWidth: true; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                      text: reverseInvoiceDialog.loaded ? reverseInvoiceDialog.preview.goldEntryText : "" }
                                Txt { Layout.fillWidth: true; color: theme.textSecondary; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                      text: reverseInvoiceDialog.loaded ? "قبل: " + reverseInvoiceDialog.preview.goldBeforeText : "" }
                                Txt { objectName: "reverseInvoiceGoldAfter"; Layout.fillWidth: true; color: theme.textSecondary; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                      text: reverseInvoiceDialog.loaded ? "بعد: " + reverseInvoiceDialog.preview.goldAfterText : "" }
                                Txt { objectName: "reverseInvoiceMakingEffect"; Layout.fillWidth: true; Layout.topMargin: 4; font.bold: true
                                      wrapMode: Text.WordWrap; elide: Text.ElideNone
                                      text: reverseInvoiceDialog.loaded ? reverseInvoiceDialog.preview.makingEntryText : "" }
                                Txt { Layout.fillWidth: true; color: theme.textSecondary; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                      text: reverseInvoiceDialog.loaded ? "قبل: " + reverseInvoiceDialog.preview.makingBeforeText : "" }
                                Txt { objectName: "reverseInvoiceMakingAfter"; Layout.fillWidth: true; color: theme.textSecondary; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                      text: reverseInvoiceDialog.loaded ? "بعد: " + reverseInvoiceDialog.preview.makingAfterText : "" }
                            }
                        }
                        Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                              text: "لا يمسّ هذا العكس المخزون أو الكسر أو الخزينة أو البنوك أو دفعات المصنعيات أو أي مورد آخر." }
                        Repeater {
                            model: reverseInvoiceDialog.loaded ? reverseInvoiceDialog.preview.blockers : []
                            delegate: Txt { objectName: "reverseInvoiceBlocker"; Layout.fillWidth: true; color: theme.red; font.bold: true
                                            wrapMode: Text.WordWrap; elide: Text.ElideNone; text: modelData }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "سبب العكس *" }
                    Field { id: reverseInvoiceReason; objectName: "reverseInvoiceReason"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "reverseInvoiceError"; Layout.fillWidth: true; visible: !!page.pvm && page.pvm.errorMessage !== ""
                  text: page.pvm ? page.pvm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmReverseInvoiceButton"
                    text: !!page.pvm && page.pvm.reversing ? "جارٍ العكس…" : "تأكيد عكس الفاتورة"
                    accent: theme.red
                    primary: true
                    enabled: reverseInvoiceDialog.loaded && reverseInvoiceDialog.preview.canReverse === true
                             && reverseInvoiceReason.text.trim().length > 0 && !!page.pvm && !page.pvm.reversing
                    onClicked: page.pvm.reverseInvoice(reverseInvoiceDialog.invoiceId, reverseInvoiceReason.text)
                }
                Btn { objectName: "cancelReverseInvoiceButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.pvm && !page.pvm.reversing; onClicked: reverseInvoiceDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- gold settlement from shop Scrap (تسوية ذهب)
    // The delivered gold leaves the counted Scrap once; the supplier is credited in grams of ITS karat. Everything shown here is computed by the
    // service rules through the view model (no arithmetic in QML); the service recomputes and re-checks the available Scrap when it posts.
    Popup {
        id: goldSettleDialog
        objectName: "goldSettleDialog"
        property string requestKey: ""
        readonly property var karatOptions: [{ karatId: "", text: "اختر عيار الذهب المُسلَّم" }].concat(page.vm && page.vm.settlementContext.scrap
                                                                                                  ? page.vm.settlementContext.scrap : [])
        readonly property string karatId: karatOptions[goldSettleKarat.currentIndex] ? karatOptions[goldSettleKarat.currentIndex].karatId : ""
        readonly property var preview: {
            var context = page.vm ? page.vm.settlementContext : ({})                // read, so the preview follows the loaded Scrap
            return page.vm && context.supplierId !== undefined ? page.vm.previewSettlement(karatId, goldSettleWeight.text) : ({})
        }
        function openFor() {
            if (!page.vm || !page.hasSelection) return
            requestKey = page.vm.newRequestKey()
            goldSettleKarat.currentIndex = 0
            goldSettleWeight.text = ""
            goldSettleDate.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            goldSettleReference.text = ""
            goldSettleNotes.text = ""
            page.vm.clearMessage()
            page.vm.loadSettlementContext(page.detail.id)
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(560, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 620, 620)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection
                  text: "تسوية ذهب للمورد " + (page.vm && page.vm.settlementContext.supplierName ? page.vm.settlementContext.supplierName : "") }
            Txt { objectName: "goldSettleRule"; Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                  wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "يُخصم الذهب المُسلَّم فعليًا من كسر المحل المتاح مرة واحدة، ويُحتسب للمورد بعياره: الوزن × عيار المُسلَّم ÷ عيار المورد. لا نقدية ولا مصنعيات ولا بيع أو شراء." }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: goldSettleForm.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: goldSettleForm
                    width: parent.width - 12
                    spacing: 6
                    Txt { objectName: "goldSettleSupplierKarat"; Layout.fillWidth: true; color: theme.amber; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                          text: "عيار حساب المورد: " + (page.vm && page.vm.settlementContext.karatText ? page.vm.settlementContext.karatText : "—") }
                    Txt { Layout.fillWidth: true; text: "عيار الذهب المُسلَّم *" }
                    Combo { id: goldSettleKarat; objectName: "goldSettleKarat"; Layout.fillWidth: true; model: goldSettleDialog.karatOptions; textRole: "text" }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "الوزن المُسلَّم الفعلي (جرام) *" }
                            Field { id: goldSettleWeight; objectName: "goldSettleWeight"; Layout.fillWidth: true; inputMethodHints: Qt.ImhFormattedNumbersOnly }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "التاريخ (yyyy-MM-dd) *" }
                            Field { id: goldSettleDate; objectName: "goldSettleDate"; Layout.fillWidth: true }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: previewColumn.implicitHeight + 14
                        radius: theme.radiusSmall
                        color: theme.bgTertiary
                        border.color: goldSettleDialog.preview.short ? theme.red : theme.border
                        ColumnLayout {
                            id: previewColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 7
                            spacing: 3
                            Txt { objectName: "goldSettleAvailable"; Layout.fillWidth: true; visible: !!goldSettleDialog.preview.availableText
                                  text: "المتاح في كسر المحل: " + goldSettleDialog.preview.availableText; color: theme.textSecondary
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "goldSettleDelivered"; Layout.fillWidth: true; visible: !!goldSettleDialog.preview.deliveredText
                                  text: "المُسلَّم: " + goldSettleDialog.preview.deliveredText; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "goldSettleFormula"; Layout.fillWidth: true; visible: !!goldSettleDialog.preview.formulaText
                                  text: "الحساب: " + goldSettleDialog.preview.formulaText; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "goldSettleCredited"; Layout.fillWidth: true; visible: !!goldSettleDialog.preview.creditedText
                                  text: "يُحتسب للمورد: " + goldSettleDialog.preview.creditedText; color: theme.gold; font.bold: true
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "goldSettleAnalytical"; Layout.fillWidth: true; visible: !!goldSettleDialog.preview.analyticalText
                                  text: "القيمة التحليلية: " + goldSettleDialog.preview.analyticalText + " • فرق التقريب: " + goldSettleDialog.preview.differenceText
                                  color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "goldSettleShort"; Layout.fillWidth: true; visible: goldSettleDialog.preview.short === true
                                  text: "الوزن أكبر من المتاح في كسر المحل."; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "goldSettlePreviewError"; Layout.fillWidth: true; visible: !!goldSettleDialog.preview.error
                                  text: goldSettleDialog.preview.error || ""; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Item { Layout.preferredHeight: 2 }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "المرجع (اختياري)" }
                    Field { id: goldSettleReference; objectName: "goldSettleReference"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: goldSettleNotes; objectName: "goldSettleNotes"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "goldSettleError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmGoldSettleButton"
                    text: !!page.vm && page.vm.settling ? "جارٍ الحفظ…" : "تسجيل التسوية"
                    primary: true
                    enabled: goldSettleDialog.karatId !== "" && goldSettleWeight.text.trim().length > 0 && goldSettleDate.text.trim().length > 0
                             && !!page.vm && !page.vm.settling && goldSettleDialog.preview.short !== true && !goldSettleDialog.preview.error
                    onClicked: page.vm.postSettlement(page.detail.id, goldSettleDialog.karatId, goldSettleWeight.text, goldSettleDate.text,
                                                      goldSettleReference.text, goldSettleNotes.text, goldSettleDialog.requestKey)
                }
                Btn { objectName: "cancelGoldSettleButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.settling; onClicked: goldSettleDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- normal intact return of a whole lot from the intermediary (OWNER only)
    Popup {
        id: holdingReturnDialog
        objectName: "holdingReturnDialog"
        property string poolId: ""
        property string sourceText: ""
        property string figuresText: ""
        property string reviewToken: ""
        property string reviewText: ""
        property bool requiresPhysicalDeclaration: false
        property string requestKey: ""
        function openFor(row) {
            if (!page.vm) return
            poolId = row.poolId
            sourceText = row.source
            figuresText = row.weight + " جرام • العيار الفعلي " + row.fineness + " • العهدة الأصلية " + row.tripCode
            reviewToken = row.returnReviewToken
            reviewText = row.returnReviewText
            requiresPhysicalDeclaration = row.returnReviewRequiresPhysicalDeclaration
            requestKey = page.vm.newRequestKey()
            holdingIntact.checked = false
            holdingReturnDate.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            holdingReturnReference.text = ""
            holdingReturnNotes.text = ""
            page.vm.clearMessage()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(520, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 520, 520)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; text: "إرجاع السبكة كاملة للعهدة"; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection }
            Txt { objectName: "holdingReturnSource"; Layout.fillWidth: true; text: holdingReturnDialog.sourceText; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            Txt { objectName: "holdingReturnFigures"; Layout.fillWidth: true; text: holdingReturnDialog.figuresText; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.textSecondary }
            Txt { objectName: "holdingReturnRule"; Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "ترجع السبكة كاملة إلى عهدتها الأصلية المفتوحة، بنفس هويتها ووزنها وعيارها. لا كسر ولا نقدية ولا مورد. وجود الرصيد وحده لا يثبت أنها نفس السبكة." }
            Flickable {
                Layout.fillWidth: true; Layout.fillHeight: true
                contentHeight: holdingReturnForm.implicitHeight; clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: holdingReturnForm
                    width: parent.width - 12; spacing: 6
                    Txt { objectName: "holdingReturnEventReview"; Layout.fillWidth: true; visible: holdingReturnDialog.reviewText !== ""
                          text: "التسويات والاستلامات المسجلة التي راجعتها:\n" + holdingReturnDialog.reviewText
                          color: theme.textSecondary; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        CheckBox { id: holdingIntact; objectName: "holdingIntactConfirm"; enabled: !!page.vm && !page.vm.returningHolding }
                        Txt { objectName: "holdingIntactText"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; font.bold: true
                              text: holdingReturnDialog.requiresPhysicalDeclaration
                                    ? "أقرّ بأن سجلات القسمة أو الاستلام المادي المعروضة سُجلت بالخطأ ولم تقع فعليًا، وأن هذه السبكة الأصلية نفسها ما زالت سليمة وكاملة وغير مقسمة لدى المسوقجي."
                                    : (page.vm ? page.vm.holdingConfirmationText : "") }
                    }
                    Txt { Layout.fillWidth: true; text: "التاريخ (yyyy-MM-dd) *" }
                    Field { id: holdingReturnDate; objectName: "holdingReturnDate"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "المرجع" }
                    Field { id: holdingReturnReference; objectName: "holdingReturnReference"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: holdingReturnNotes; objectName: "holdingReturnNotes"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "holdingReturnError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmHoldingReturnButton"
                    text: !!page.vm && page.vm.returningHolding ? "جارٍ الحفظ…" : "تسجيل الرجوع"
                    primary: true
                    enabled: holdingIntact.checked && holdingReturnDate.text.trim().length > 0 && !!page.vm && !page.vm.returningHolding
                    onClicked: page.vm.returnHolding(holdingReturnDialog.poolId, holdingIntact.checked, holdingReturnDialog.reviewToken,
                                                     holdingReturnDialog.requiresPhysicalDeclaration && holdingIntact.checked,
                                                     holdingReturnDate.text, holdingReturnReference.text,
                                                     holdingReturnNotes.text, holdingReturnDialog.requestKey)
                }
                Btn { objectName: "cancelHoldingReturnButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.returningHolding; onClicked: holdingReturnDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- return pooled LOGO grams (partial or whole) from the intermediary (OWNER only)
    Popup {
        id: logoReturnDialog
        objectName: "logoReturnDialog"
        property string poolId: ""
        property string sourceText: ""
        property string figuresText: ""
        property string requestKey: ""
        function openFor(row) {
            if (!page.vm) return
            poolId = row.poolId
            sourceText = "لوجو " + row.karat + " • " + row.document
            figuresText = "المتبقي لدى المسوقجي " + row.outstanding + " جرام • العهدة الأصلية " + row.tripCode
            requestKey = page.vm.newRequestKey()
            logoReturnGrams.text = ""
            logoReturnDate.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            logoReturnReference.text = ""
            logoReturnNotes.text = ""
            page.vm.clearMessage()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(520, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 500, 500)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; text: "إرجاع ذهب للعهدة"; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection }
            Txt { objectName: "logoReturnSource"; Layout.fillWidth: true; text: logoReturnDialog.sourceText; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            Txt { objectName: "logoReturnFigures"; Layout.fillWidth: true; text: logoReturnDialog.figuresText; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.textSecondary }
            Txt { objectName: "logoReturnRule"; Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "يرجع إلى عهدته الأصلية المفتوحة بالوزن الذي تكتبه (كليًا أو جزئيًا) حتى المتبقي من هذا المصدر. لا كسر ولا نقدية ولا مورد، ولا تأكيد سبيكة لأنه ذهب بالجرام." }
            Flickable {
                Layout.fillWidth: true; Layout.fillHeight: true
                contentHeight: logoReturnForm.implicitHeight; clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: logoReturnForm
                    width: parent.width - 12; spacing: 6
                    Txt { Layout.fillWidth: true; text: "الوزن العائد الفعلي (جرام) *" }
                    Field { id: logoReturnGrams; objectName: "logoReturnGrams"; Layout.fillWidth: true; inputMethodHints: Qt.ImhFormattedNumbersOnly }
                    Txt { Layout.fillWidth: true; text: "التاريخ (yyyy-MM-dd) *" }
                    Field { id: logoReturnDate; objectName: "logoReturnDate"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "المرجع" }
                    Field { id: logoReturnReference; objectName: "logoReturnReference"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: logoReturnNotes; objectName: "logoReturnNotes"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "logoReturnError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmLogoReturnButton"
                    text: !!page.vm && page.vm.returningHolding ? "جارٍ الحفظ…" : "تسجيل الرجوع"
                    primary: true
                    enabled: logoReturnGrams.text.trim().length > 0 && logoReturnDate.text.trim().length > 0 && !!page.vm && !page.vm.returningHolding
                    onClicked: page.vm.returnLogo(logoReturnDialog.poolId, logoReturnGrams.text, logoReturnDate.text,
                                                  logoReturnReference.text, logoReturnNotes.text, logoReturnDialog.requestKey)
                }
                Btn { objectName: "cancelLogoReturnButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.returningHolding; onClicked: logoReturnDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- supplier gold settlement from a holding at the intermediary (050, OWNER only)
    // ONE source, ONE supplier, ONE quantity. The credit shown is computed by the service rules through the view model (no arithmetic in QML); the
    // service recomputes it and re-checks the balance, the declaration and the rights under its locks. No custody, Scrap, cash or making moves.
    Popup {
        id: holdingSettleDialog
        objectName: "holdingSettleDialog"
        property string poolKind: ""
        property string poolId: ""
        property string requestKey: ""
        property string declarationCode: ""
        readonly property var context: page.vm ? page.vm.holdingSettlementContext : ({})
        readonly property var recipientOptions: [{ id: "", text: "اختر المورد المستلم" }].concat(context.recipients ? context.recipients : [])
        readonly property string supplierId: recipientOptions[holdingSettleSupplier.currentIndex] ? recipientOptions[holdingSettleSupplier.currentIndex].id : ""
        readonly property var preview: {
            var c = page.vm ? page.vm.holdingSettlementContext : ({})                   // read, so the preview follows the loaded context
            return page.vm && c.poolId !== undefined ? page.vm.previewHoldingSettlement(supplierId, holdingSettleQty.text) : ({})
        }
        readonly property var options: preview.options ? preview.options : []
        readonly property bool declarationOk: context.isLot !== true || declarationCode !== ""
        onOptionsChanged: {
            var still = false
            for (var i = 0; i < options.length; i++) if (options[i].code === declarationCode) still = true
            if (!still) declarationCode = ""                                              // never preselected, never kept when no longer applicable
        }
        function openFor(kind, row) {
            if (!page.vm) return
            poolKind = kind
            poolId = row.poolId
            requestKey = page.vm.newRequestKey()
            declarationCode = ""
            holdingSettleSupplier.currentIndex = 0
            holdingSettleQty.text = ""
            holdingSettleDate.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            holdingSettleReference.text = ""
            holdingSettleNotes.text = ""
            holdingSettleSelf.checked = false
            page.vm.clearMessage()
            page.vm.loadHoldingSettlement(kind, row.poolId)
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(580, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 700, 700)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection
                  text: "تسوية ذهب لمورد من رصيد المسوقجي" }
            Txt { objectName: "holdingSettleSource"; Layout.fillWidth: true; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: holdingSettleDialog.context.sourceText ? holdingSettleDialog.context.sourceText : "" }
            Txt { objectName: "holdingSettleAvailable"; Layout.fillWidth: true; color: theme.textSecondary; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  visible: !!holdingSettleDialog.context.availableText
                  text: "المسوقجي: " + (holdingSettleDialog.context.holderName ? holdingSettleDialog.context.holderName : "") + " • المتاح من هذا المصدر: "
                        + (holdingSettleDialog.context.availableText ? holdingSettleDialog.context.availableText : "") }
            Txt { objectName: "holdingSettleRule"; Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                  wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "يُخصم الذهب من رصيد هذا المصدر لدى المسوقجي ويُحتسب للمورد بعياره. لا يتحرك شيء في العهدة أو الكسر أو النقدية أو المصنعيات، ولا تُفتح عهدة مغلقة." }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: holdingSettleForm.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: holdingSettleForm
                    width: parent.width - 12
                    spacing: 6
                    Txt { Layout.fillWidth: true; text: "المورد المستلم *" }
                    Combo { id: holdingSettleSupplier; objectName: "holdingSettleSupplier"; Layout.fillWidth: true
                            model: holdingSettleDialog.recipientOptions; textRole: "text" }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "الوزن المُسلَّم الفعلي (جرام) *" }
                            Field { id: holdingSettleQty; objectName: "holdingSettleQty"; Layout.fillWidth: true; inputMethodHints: Qt.ImhFormattedNumbersOnly }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "التاريخ (yyyy-MM-dd) *" }
                            Field { id: holdingSettleDate; objectName: "holdingSettleDate"; Layout.fillWidth: true }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: holdingPreviewColumn.implicitHeight + 14
                        radius: theme.radiusSmall
                        color: theme.bgTertiary
                        border.color: holdingSettleDialog.preview.short === true ? theme.red : theme.border
                        ColumnLayout {
                            id: holdingPreviewColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 7
                            spacing: 3
                            Txt { objectName: "holdingSettleDelivered"; Layout.fillWidth: true; visible: !!holdingSettleDialog.preview.deliveredText
                                  text: "المُسلَّم: " + holdingSettleDialog.preview.deliveredText; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "holdingSettleFormula"; Layout.fillWidth: true; visible: !!holdingSettleDialog.preview.formulaText
                                  text: "الحساب: " + holdingSettleDialog.preview.formulaText; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "holdingSettleCredited"; Layout.fillWidth: true; visible: !!holdingSettleDialog.preview.creditedText
                                  text: "يُحتسب للمورد: " + holdingSettleDialog.preview.creditedText; color: theme.gold; font.bold: true
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "holdingSettleAnalytical"; Layout.fillWidth: true; visible: !!holdingSettleDialog.preview.analyticalText
                                  text: "القيمة التحليلية: " + holdingSettleDialog.preview.analyticalText + " • فرق التقريب: " + holdingSettleDialog.preview.differenceText
                                  color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "holdingSettleRemaining"; Layout.fillWidth: true; visible: !!holdingSettleDialog.preview.remainingText
                                  text: "المتبقي لدى المسوقجي بعد التسوية: " + holdingSettleDialog.preview.remainingText; color: theme.textSecondary
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "holdingSettlePreviewError"; Layout.fillWidth: true; visible: !!holdingSettleDialog.preview.error
                                  text: holdingSettleDialog.preview.error || ""; color: holdingSettleDialog.preview.short === true ? theme.red : theme.amber
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Item { Layout.preferredHeight: 2 }
                        }
                    }
                    // The physical declaration of a lot: ONLY the applicable choices, none preselected. A full quantity alone never proves a whole bar.
                    Txt { objectName: "holdingSettleDeclarationTitle"; Layout.fillWidth: true; font.bold: true
                          visible: holdingSettleDialog.context.isLot === true && holdingSettleDialog.options.length > 0
                          text: "حالة السبكة الفعلية عند التسليم *" }
                    Repeater {
                        model: holdingSettleDialog.options
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 8
                            RadioButton {
                                objectName: "holdingSettleDeclaration_" + modelData.code
                                checked: holdingSettleDialog.declarationCode === modelData.code
                                enabled: !!page.vm && !page.vm.settlingFromHolding
                                onClicked: holdingSettleDialog.declarationCode = modelData.code
                            }
                            Txt { Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; text: modelData.text
                                  font.bold: modelData.code === "WHOLE_ORIGINAL_BAR" }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: holdingSettleDialog.preview.isSelf === true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        CheckBox { id: holdingSettleSelf; objectName: "holdingSettleSelfConfirm"; enabled: !!page.vm && !page.vm.settlingFromHolding }
                        Txt { objectName: "holdingSettleSelfText"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.amber; font.bold: true
                              text: "المسوقجي هو نفسه المورد المستلم: أؤكد أن ذهب المحل لديه يبقى منفصلًا عن حساب المورد، وأن هذه التسوية تُنقص رصيد الذهب الذي نحاسبه عليه فقط." }
                    }
                    Txt { Layout.fillWidth: true; text: "المرجع (اختياري)" }
                    Field { id: holdingSettleReference; objectName: "holdingSettleReference"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: holdingSettleNotes; objectName: "holdingSettleNotes"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "holdingSettleError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmHoldingSettleButton"
                    text: !!page.vm && page.vm.settlingFromHolding ? "جارٍ الحفظ…" : "تسجيل التسوية"
                    primary: true
                    enabled: holdingSettleDialog.supplierId !== "" && holdingSettleQty.text.trim().length > 0 && holdingSettleDate.text.trim().length > 0
                             && holdingSettleDialog.preview.valid === true && holdingSettleDialog.declarationOk
                             && (holdingSettleDialog.preview.isSelf !== true || holdingSettleSelf.checked) && !!page.vm && !page.vm.settlingFromHolding
                    onClicked: page.vm.postHoldingSettlement(holdingSettleDialog.poolKind, holdingSettleDialog.poolId, holdingSettleDialog.supplierId,
                                                             holdingSettleQty.text, holdingSettleDialog.declarationCode, holdingSettleSelf.checked,
                                                             holdingSettleDate.text, holdingSettleReference.text, holdingSettleNotes.text,
                                                             holdingSettleDialog.requestKey)
                }
                Btn { objectName: "cancelHoldingSettleButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.settlingFromHolding; onClicked: holdingSettleDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- reverse a physical receipt (OWNER only)
    Popup {
        id: reverseReceiptDialog
        objectName: "reverseReceiptDialog"
        property string receiptId: ""
        property string code: ""
        property string detailText: ""
        property string effectText: ""
        function openFor(row) {
            receiptId = row.id
            code = row.code
            detailText = row.text
            effectText = row.effectText || ""
            reverseReceiptReason.text = ""
            if (page.vm) page.vm.clearMessage()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(500, page.width - 24)
        modal: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.red }
        contentItem: ColumnLayout {
            spacing: 8
            Txt { Layout.fillWidth: true; text: "تصحيح تسجيل استلام " + page.ltr(reverseReceiptDialog.code); color: theme.red; font.bold: true; font.pixelSize: theme.fontSection }
            Txt { Layout.fillWidth: true; text: reverseReceiptDialog.detailText; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            Txt { objectName: "reverseReceiptNote"; Layout.fillWidth: true; color: theme.amber; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "أثر التصحيح على الأرصدة: " + reverseReceiptDialog.effectText + " يتم ذلك معًا في عملية واحدة. لا يُثبت التصحيح وحده سلامة السبكة الأصلية؛ بعد عكس كل التسويات والاستلامات واستعادة كامل الرصيد يمكن طلب الرجوع العادي بتأكيد المالك أن السبكة نفسها سليمة كاملة، مع إقراره بأن الاستلام المسجَّل لم يقع فعليًا. يبقى الاستلام الأصلي في السجل ولا يُعدّل." }
            Txt { Layout.fillWidth: true; text: "سبب العكس *" }
            Field { id: reverseReceiptReason; objectName: "reverseReceiptReason"; Layout.fillWidth: true }
            Txt { objectName: "reverseReceiptError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmReverseReceiptButton"
                    text: !!page.vm && page.vm.receiptReversing ? "جارٍ التصحيح…" : "تأكيد التصحيح"
                    accent: theme.red
                    primary: true
                    enabled: reverseReceiptReason.text.trim().length > 0 && !!page.vm && !page.vm.receiptReversing
                    onClicked: page.vm.reverseReceipt(reverseReceiptDialog.receiptId, reverseReceiptReason.text)
                }
                Btn { text: "إلغاء"; accent: theme.textSecondary; enabled: !!page.vm && !page.vm.receiptReversing; onClicked: reverseReceiptDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- reverse a gold settlement (OWNER only)
    Popup {
        id: reverseSettlementDialog
        objectName: "reverseSettlementDialog"
        property string settlementId: ""
        property string code: ""
        property string detailText: ""
        property bool fromHolding: false
        property string effectText: ""
        function openFor(row) {
            effectText = row.effect || ""
            settlementId = row.settlementId
            code = row.reference
            detailText = row.detail
            fromHolding = row.fromHolding === true || row.holdingParty === true
            reverseSettlementReason.text = ""
            if (page.vm) page.vm.clearMessage()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(500, page.width - 24)
        modal: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.red }
        contentItem: ColumnLayout {
            spacing: 8
            Txt { Layout.fillWidth: true; text: (reverseSettlementDialog.fromHolding ? "تصحيح تسجيل تسوية ذهب " : "عكس تسوية ذهب ") + page.ltr(reverseSettlementDialog.code); color: theme.red
                  font.bold: true; font.pixelSize: theme.fontSection }
            Txt { Layout.fillWidth: true; text: reverseSettlementDialog.detailText; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            Txt { objectName: "reverseSettlementNote"; Layout.fillWidth: true; color: theme.amber; font.pixelSize: theme.fontTableHeader
                  wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: reverseSettlementDialog.fromHolding
                        ? "أثر التصحيح على الأرصدة: " + reverseSettlementDialog.effectText + " (كرصيد جرامات فقط: لا يثبت هذا التصحيح سلامة السبكة الفعلية). لا يتحرك شيء في الكسر أو العهدة. تبقى التسوية الأصلية في السجل ولا تُعدّل."
                        : "يُسجَّل أن الذهب المُسلَّم استُرد فعليًا: يعود إلى كسر المحل بنفس الوزن والعيار المُسلَّمين، ويعود رصيد المورد كما كان قبل التسوية بنفس الكمية المحتسبة المخزَّنة. تبقى التسوية الأصلية في السجل ولا تُعدّل." }
            Txt { Layout.fillWidth: true; text: "سبب العكس *" }
            Field { id: reverseSettlementReason; objectName: "reverseSettlementReason"; Layout.fillWidth: true }
            Txt { objectName: "reverseSettlementError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmReverseSettlementButton"
                    text: !!page.vm && page.vm.settlementReversing ? "جارٍ العكس…" : (reverseSettlementDialog.fromHolding ? "تأكيد التصحيح" : "عكس التسوية")
                    accent: theme.red
                    primary: true
                    enabled: reverseSettlementReason.text.trim().length > 0 && !!page.vm && !page.vm.settlementReversing
                    onClicked: page.vm.reverseSettlement(reverseSettlementDialog.settlementId, reverseSettlementReason.text)
                }
                Btn { text: "إلغاء"; accent: theme.textSecondary; enabled: !!page.vm && !page.vm.settlementReversing
                      onClicked: reverseSettlementDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }


    // ---------------------------------------------------------------- marketer custody (055): one dialog for every action of the cycle
    // The cash is the shop's money held in trust and the gold is 21K only; nothing here touches the supplier ledger except the explicit settlement.
    Popup {
        id: marketerDialog
        objectName: "marketerDialog"
        property string mode: "CASH_HANDOVER"
        property string requestKey: ""
        property var lineRows: []
        readonly property bool isCash: mode === "CASH_HANDOVER" || mode === "CASH_RETURN"
        readonly property bool hasWeight: mode === "GOLD_RETURN" || mode === "SALE" || mode === "SETTLE"
        readonly property var priceModes: [{ mode: "TOTAL", text: "إجمالي الصفقة (ج)" }, { mode: "PER_GRAM", text: "سعر الجرام (ج)" }]
        readonly property var counterpartyChoices: [{ id: "", text: mode === "SALE" ? "مشترٍ غير مسجل (اكتب اسمه)" : "بائع غير مسجل (اكتب اسمه)" }]
                                                    .concat(page.mvm ? (page.mvm.account.parties || []) : [])
        readonly property var supplierChoices: [{ id: "", text: "اختر المورد المستلم", self: false }].concat(page.mvm ? (page.mvm.account.recipients || []) : [])
        readonly property string counterpartyId: counterpartyChoices[marketerCounterparty.currentIndex] ? counterpartyChoices[marketerCounterparty.currentIndex].id : ""
        readonly property string supplierId: supplierChoices[marketerSupplier.currentIndex] ? supplierChoices[marketerSupplier.currentIndex].id : ""
        readonly property string priceMode: priceModes[marketerPriceMode.currentIndex].mode
        readonly property bool needsConfirmation: !!page.mvm && page.mvm.errorCode === "PRICE_DEVIATION"
        readonly property var preview: {
            var known = !!page.mvm && page.mvm.visible === true
            if (!known) return ({})
            if (mode === "SALE") return page.mvm.previewSale(marketerWeight.text, priceMode, marketerPrice.text)
            if (mode === "PURCHASE") return page.mvm.previewPurchase(lineRows)
            if (mode === "SETTLE") return page.mvm.previewSettlement(supplierId, marketerWeight.text)
            return ({})
        }
        readonly property string title: mode === "CASH_HANDOVER" ? "تسليم نقدية من الخزنة الرئيسية للمسوقجي"
                                        : mode === "CASH_RETURN" ? "استرجاع نقدية من المسوقجي إلى الخزنة الرئيسية"
                                        : mode === "GOLD_RETURN" ? "استرجاع ذهب عيار 21 من المسوقجي إلى كسر المحل"
                                        : mode === "SALE" ? "بيع ذهب المسوقجي (عيار 21)"
                                        : mode === "PURCHASE" ? "شراء ذهب نقدي بنقدية المسوقجي" : "تسليم ذهب المسوقجي لمورد (تسوية)"
        function collect() {
            var rows = []
            for (var i = 0; i < marketerLines.count; ++i) rows.push({ weight: marketerLines.get(i).weight, price: marketerLines.get(i).price })
            return rows
        }
        function refreshLines() { lineRows = collect() }
        function openFor(kind) {
            if (!page.mvm) return
            mode = kind
            requestKey = page.mvm.newRequestKey()
            marketerAmount.text = ""
            marketerWeight.text = ""
            marketerPrice.text = ""
            marketerPriceMode.currentIndex = 0
            marketerCounterparty.currentIndex = 0
            marketerCounterpartyName.text = ""
            marketerSupplier.currentIndex = 0
            marketerSelfConfirm.checked = false
            marketerDate.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            marketerReference.text = ""
            marketerNotes.text = ""
            marketerLines.clear()
            marketerLines.append({ weight: "", price: "" })
            refreshLines()
            page.mvm.clearMessage()
            page.mvm.loadReferencePrices()
            open()
        }
        function complete() {
            if (marketerDate.text.trim().length === 0) return false
            if (isCash) return marketerAmount.text.trim().length > 0
            if (mode === "GOLD_RETURN") return marketerWeight.text.trim().length > 0
            if (mode === "SALE") return marketerWeight.text.trim().length > 0 && marketerPrice.text.trim().length > 0
                                         && (counterpartyId !== "" || marketerCounterpartyName.text.trim().length > 0)
            if (mode === "SETTLE") return marketerWeight.text.trim().length > 0 && supplierId !== ""
                                          && (!(supplierChoices[marketerSupplier.currentIndex] || {}).self || marketerSelfConfirm.checked)
            if (counterpartyId === "" && marketerCounterpartyName.text.trim().length === 0) return false
            for (var i = 0; i < marketerLines.count; ++i)
                if (marketerLines.get(i).weight.trim().length > 0 && marketerLines.get(i).price.trim().length > 0) return true
            return false
        }
        function submit(acknowledged) {
            var key = requestKey, date = marketerDate.text, ref = marketerReference.text, notes = marketerNotes.text
            if (mode === "CASH_HANDOVER") page.mvm.cashHandover(marketerAmount.text, date, ref, notes, key)
            else if (mode === "CASH_RETURN") page.mvm.cashReturn(marketerAmount.text, date, ref, notes, key)
            else if (mode === "GOLD_RETURN") page.mvm.goldReturn(marketerWeight.text, date, ref, notes, key)
            else if (mode === "SALE") page.mvm.sell(marketerWeight.text, priceMode, marketerPrice.text, counterpartyId, marketerCounterpartyName.text, date, ref, notes, key, acknowledged)
            else if (mode === "PURCHASE") page.mvm.purchase(collect(), counterpartyId, marketerCounterpartyName.text, date, ref, notes, key, acknowledged)
            else page.mvm.settle(supplierId, marketerWeight.text, marketerSelfConfirm.checked, date, ref, notes, key)
        }
        Connections {
            target: page.mvm
            function onPosted(code) { marketerDialog.close() }
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(600, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 680, 680)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.amber }
        ListModel { id: marketerLines }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { objectName: "marketerDialogTitle"; Layout.fillWidth: true; color: theme.amber; font.bold: true; font.pixelSize: theme.fontSection
                  wrapMode: Text.WordWrap; elide: Text.ElideNone; text: marketerDialog.title }
            Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "عهدة المسوقجي أمانة للمحل: وزنة عيار 21 فقط، منفصلة تمامًا عن حساب المورد." }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: marketerForm.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: marketerForm
                    width: parent.width - 12
                    spacing: 6
                    Txt { Layout.fillWidth: true; visible: marketerDialog.isCash; text: "المبلغ (ج) *" }
                    Field { id: marketerAmount; objectName: "marketerAmountField"; Layout.fillWidth: true; visible: marketerDialog.isCash; inputMethodHints: Qt.ImhFormattedNumbersOnly }
                    Txt { Layout.fillWidth: true; visible: marketerDialog.hasWeight; text: "الوزن بالجرام (وزنة عيار 21) *" }
                    Field { id: marketerWeight; objectName: "marketerWeightField"; Layout.fillWidth: true; visible: marketerDialog.hasWeight; inputMethodHints: Qt.ImhFormattedNumbersOnly }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: marketerDialog.mode === "SALE"
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "طريقة التسعير *" }
                            Combo { id: marketerPriceMode; objectName: "marketerPriceModeCombo"; Layout.fillWidth: true; model: marketerDialog.priceModes; textRole: "text" }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "السعر المتفق عليه *" }
                            Field { id: marketerPrice; objectName: "marketerPriceField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhFormattedNumbersOnly }
                        }
                    }
                    Txt { Layout.fillWidth: true; visible: marketerDialog.mode === "SALE" || marketerDialog.mode === "PURCHASE"
                          text: marketerDialog.mode === "SALE" ? "المشتري *" : "البائع *" }
                    Combo { id: marketerCounterparty; objectName: "marketerCounterpartyCombo"; Layout.fillWidth: true
                            visible: marketerDialog.mode === "SALE" || marketerDialog.mode === "PURCHASE"; model: marketerDialog.counterpartyChoices; textRole: "text" }
                    Txt { Layout.fillWidth: true; visible: (marketerDialog.mode === "SALE" || marketerDialog.mode === "PURCHASE") && marketerDialog.counterpartyId === ""
                          text: marketerDialog.mode === "SALE" ? "اسم المشتري *" : "اسم البائع *" }
                    Field { id: marketerCounterpartyName; objectName: "marketerCounterpartyNameField"; Layout.fillWidth: true
                            visible: (marketerDialog.mode === "SALE" || marketerDialog.mode === "PURCHASE") && marketerDialog.counterpartyId === "" }
                    Txt { Layout.fillWidth: true; visible: marketerDialog.mode === "SETTLE"; text: "المورد المستلم *" }
                    Combo { id: marketerSupplier; objectName: "marketerSupplierCombo"; Layout.fillWidth: true; visible: marketerDialog.mode === "SETTLE"
                            model: marketerDialog.supplierChoices; textRole: "text" }
                    CheckBox {
                        id: marketerSelfConfirm
                        objectName: "marketerSelfConfirmBox"
                        Layout.fillWidth: true
                        visible: marketerDialog.mode === "SETTLE" && !!(marketerDialog.supplierChoices[marketerSupplier.currentIndex] || {}).self
                        text: "أؤكد أن المسوقجي هو نفسه المورد المستلم، وأن ذهب المحل لديه (أمانة) ينتقل بهذه التسوية إلى حسابه كمورد."
                    }
                    Txt { Layout.fillWidth: true; visible: marketerDialog.mode === "PURCHASE"; color: theme.gold; font.bold: true
                          text: "الذهب المشترى (وزنة عيار 21): الوزن الفعلي وثمن السطر" }
                    Repeater {
                        model: marketerDialog.mode === "PURCHASE" ? marketerLines : null
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            Field {
                                objectName: "marketerLineWeight_" + index
                                Layout.fillWidth: true
                                placeholderText: "الوزن (جرام)"
                                text: weight
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                onTextEdited: { marketerLines.setProperty(index, "weight", text); marketerDialog.refreshLines() }
                            }
                            Field {
                                objectName: "marketerLinePrice_" + index
                                Layout.fillWidth: true
                                placeholderText: "الثمن (ج)"
                                text: price
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                onTextEdited: { marketerLines.setProperty(index, "price", text); marketerDialog.refreshLines() }
                            }
                        }
                    }
                    Btn { objectName: "marketerAddLineButton"; visible: marketerDialog.mode === "PURCHASE"; text: "＋ سطر ذهب آخر"; accent: theme.textSecondary
                          enabled: marketerLines.count < 8
                          onClicked: { marketerLines.append({ weight: "", price: "" }); marketerDialog.refreshLines() } }
                    Rectangle {
                        Layout.fillWidth: true
                        visible: !marketerDialog.isCash && marketerDialog.mode !== "GOLD_RETURN"
                        implicitHeight: marketerPreview.implicitHeight + 14
                        radius: theme.radiusSmall
                        color: theme.bgTertiary
                        border.color: marketerDialog.preview.warning ? theme.amber : theme.border
                        ColumnLayout {
                            id: marketerPreview
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 7
                            spacing: 3
                            Txt { objectName: "marketerAvailable"; Layout.fillWidth: true; visible: !!marketerDialog.preview.availableText
                                  text: marketerDialog.preview.availableText || ""; color: theme.amber; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "marketerTotal"; Layout.fillWidth: true; visible: !!marketerDialog.preview.totalText
                                  text: "إجمالي الصفقة: " + (marketerDialog.preview.totalText || "") + (marketerDialog.preview.perGramText ? " • سعر الجرام: " + marketerDialog.preview.perGramText : "")
                                  color: theme.gold; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "marketerCredited"; Layout.fillWidth: true; visible: !!marketerDialog.preview.creditedText
                                  text: "يُحتسب للمورد: " + (marketerDialog.preview.creditedText || "") + " (" + (marketerDialog.preview.formulaText || "") + ")"
                                  color: theme.gold; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "marketerReference"; Layout.fillWidth: true; visible: !!marketerDialog.preview.referenceText
                                  text: (marketerDialog.preview.referenceText || "") + (marketerDialog.preview.deviationText ? " • الفرق " + marketerDialog.preview.deviationText : "")
                                  color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "marketerWarning"; Layout.fillWidth: true; visible: marketerDialog.preview.warning === true
                                  text: marketerDialog.preview.warningText || ""; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "marketerPreviewError"; Layout.fillWidth: true; visible: !!marketerDialog.preview.error
                                  text: marketerDialog.preview.error || ""; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Item { Layout.preferredHeight: 2 }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "التاريخ (yyyy-MM-dd) *" }
                    Field { id: marketerDate; objectName: "marketerDateField"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "المرجع (اختياري)" }
                    Field { id: marketerReference; objectName: "marketerReferenceField"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: marketerNotes; objectName: "marketerNotesField"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "marketerError"; Layout.fillWidth: true; visible: !!page.mvm && page.mvm.errorMessage !== ""
                  text: page.mvm ? page.mvm.errorMessage : ""; color: marketerDialog.needsConfirmation ? theme.amber : theme.red
                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmMarketerButton"
                    visible: !marketerDialog.needsConfirmation
                    text: !!page.mvm && page.mvm.writing ? "جارٍ الحفظ…" : "تسجيل"
                    primary: true
                    enabled: marketerDialog.complete() && !!page.mvm && !page.mvm.writing && !marketerDialog.preview.error
                    onClicked: marketerDialog.submit(false)
                }
                Btn {
                    objectName: "acknowledgeMarketerPriceButton"
                    visible: marketerDialog.needsConfirmation
                    text: !!page.mvm && page.mvm.writing ? "جارٍ الحفظ…" : "أؤكد السعر وأسجّل"
                    primary: true
                    accent: theme.amber
                    enabled: !!page.mvm && !page.mvm.writing
                    onClicked: marketerDialog.submit(true)
                }
                Btn { objectName: "cancelMarketerButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.mvm && !page.mvm.writing; onClicked: marketerDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- marketer custody: reverse a document or a settlement (OWNER only)
    Popup {
        id: marketerReverseDialog
        objectName: "marketerReverseDialog"
        property string targetId: ""
        property bool isSettlement: false
        property string detailText: ""
        function openFor(row, settlement) {
            targetId = row.id
            isSettlement = settlement
            detailText = (row.kindText ? row.kindText + " " : "") + row.code + " — " + row.text
            marketerReverseReason.text = ""
            if (page.mvm) page.mvm.clearMessage()
            open()
        }
        Connections {
            target: page.mvm
            function onReversedDone() { marketerReverseDialog.close() }
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(500, page.width - 24)
        modal: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.red }
        contentItem: ColumnLayout {
            spacing: 8
            Txt { Layout.fillWidth: true; text: marketerReverseDialog.isSettlement ? "عكس تسوية ذهب من عهدة المسوقجي" : "عكس مستند عهدة المسوقجي"
                  font.bold: true; font.pixelSize: theme.fontSection }
            Txt { Layout.fillWidth: true; text: marketerReverseDialog.detailText; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            Txt { Layout.fillWidth: true; color: theme.amber; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "يُسجَّل عكس صريح بنفس الأرقام المخزّنة (يبقى المستند الأصلي كما هو). يُرفض العكس إذا كان رصيد النقدية أو الذهب لا يكفي بعده؛ اعكس ما يعتمد عليه أولًا." }
            Txt { Layout.fillWidth: true; text: "سبب العكس *" }
            Field { id: marketerReverseReason; objectName: "marketerReverseReason"; Layout.fillWidth: true }
            Txt { objectName: "marketerReverseError"; Layout.fillWidth: true; visible: !!page.mvm && page.mvm.errorMessage !== ""
                  text: page.mvm ? page.mvm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmMarketerReverseButton"
                    text: !!page.mvm && page.mvm.writing ? "جارٍ العكس…" : "تأكيد العكس"
                    accent: theme.red
                    primary: true
                    enabled: marketerReverseReason.text.trim().length > 0 && !!page.mvm && !page.mvm.writing
                    onClicked: marketerReverseDialog.isSettlement ? page.mvm.reverseSettlement(marketerReverseDialog.targetId, marketerReverseReason.text)
                                                                   : page.mvm.reverseDocument(marketerReverseDialog.targetId, marketerReverseReason.text)
                }
                Btn { objectName: "cancelMarketerReverseButton"; text: "إلغاء"; accent: theme.textSecondary; enabled: !!page.mvm && !page.mvm.writing
                      onClicked: marketerReverseDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- new purchase invoice
    Popup {
        id: newInvoiceDialog
        objectName: "newSupplierInvoiceDialog"
        property string requestKey: ""
        // Edit mode: the SAME form edits a saved draft (same invoice id; saving updates it, never creates another).
        property string editId: ""
        property string editCode: ""
        property string editSupplierId: ""
        property var preview: ({ valid: false, error: "", weightText: "—", makingText: "—", groupMaking: [] })
        // The invoice karat is the supplier's dedicated karat (shown read-only). When editing a saved draft its own saved
        // karat is sent, so the service refuses an incompatible draft instead of silently converting it.
        property int editKaratId: 0
        property int editKaratValue: 0
        readonly property int invoiceKaratId: editId !== "" ? editKaratId : (page.detail.karatId || 0)
        readonly property string invoiceKaratText: editId !== "" ? "عيار " + editKaratValue : (page.detail.karatText || "")
        function collect() {
            var rows = []
            for (var i = 0; i < groupModel.count; ++i) {
                var g = groupModel.get(i)
                rows.push({ weight: g.weight, rate: g.rate, description: g.description })
            }
            return rows
        }
        function recompute() {
            if (page.pvm) preview = page.pvm.previewGroups(collect())
        }
        function openForEdit(form) {
            editId = form.id
            editCode = form.code
            editSupplierId = form.supplierId
            requestKey = ""
            groupModel.clear()
            for (var i = 0; i < form.groups.length; ++i)
                groupModel.append({ weight: form.groups[i].weight, rate: form.groups[i].rate, description: form.groups[i].description })
            referenceField.text = form.reference
            notesField2.text = form.notes
            dateField.text = form.date
            editKaratId = form.karatId
            editKaratValue = form.karatValue
            if (page.pvm) page.pvm.clearMessage()
            recompute()
            open()
        }
        function openFor() {
            editId = ""
            editCode = ""
            editSupplierId = ""
            requestKey = page.pvm ? page.pvm.newRequestKey() : ""
            groupModel.clear()
            groupModel.append({ weight: "", rate: "", description: "" })
            referenceField.text = ""
            notesField2.text = ""
            dateField.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            editKaratId = 0
            editKaratValue = 0
            if (page.pvm) page.pvm.clearMessage()
            recompute()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(760, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 640, 640)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        ListModel { id: groupModel }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection
                  text: (newInvoiceDialog.editId !== "" ? "تعديل المسودة " + page.ltr(newInvoiceDialog.editCode) : "فاتورة شراء جديدة")
                        + " — " + (page.detail.name || "") }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: formColumn.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: formColumn
                    width: parent.width - 12
                    spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "رقم فاتورة المورد" }
                            Field { id: referenceField; objectName: "invoiceReferenceField"; Layout.fillWidth: true }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "التاريخ (yyyy-MM-dd)" }
                            Field { id: dateField; objectName: "invoiceDateField"; Layout.fillWidth: true }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "العيار (عيار المورد)" }
                            Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 34
                                radius: 4
                                color: theme.bgTertiary
                                border.color: theme.border
                                Txt { objectName: "invoiceKaratText"; anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10
                                      text: newInvoiceDialog.invoiceKaratText; color: theme.gold; font.bold: true; elide: Text.ElideNone }
                            }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "المجموعات المصنعية (وزن المورد × مصنعية الجرام)"; color: theme.gold
                          font.bold: true }
                    Repeater {
                        model: groupModel
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            Txt { Layout.preferredWidth: 22; text: (index + 1) + "" ; horizontalAlignment: Text.AlignHCenter
                                  color: theme.textMuted }
                            Field {
                                objectName: "groupWeight_" + index
                                Layout.preferredWidth: 130
                                placeholderText: "الوزن (جرام)"
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                text: model.weight
                                onTextEdited: { groupModel.setProperty(index, "weight", text); newInvoiceDialog.recompute() }
                            }
                            Field {
                                objectName: "groupRate_" + index
                                Layout.preferredWidth: 130
                                placeholderText: "مصنعية ج/جرام"
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                text: model.rate
                                onTextEdited: { groupModel.setProperty(index, "rate", text); newInvoiceDialog.recompute() }
                            }
                            Field {
                                Layout.fillWidth: true
                                placeholderText: "وصف المجموعة (اختياري)"
                                text: model.description
                                onTextEdited: groupModel.setProperty(index, "description", text)
                            }
                            Txt { Layout.preferredWidth: 110
                                  text: newInvoiceDialog.preview.valid && index >= 0 && index < newInvoiceDialog.preview.groupMaking.length
                                        ? newInvoiceDialog.preview.groupMaking[index] : "—"
                                  color: theme.textSecondary }
                            Btn { text: "✕"; accent: theme.red; implicitWidth: 34; leftPadding: 4; rightPadding: 4
                                  enabled: groupModel.count > 1
                                  onClicked: { groupModel.remove(index); newInvoiceDialog.recompute() } }
                        }
                    }
                    Btn {
                        objectName: "addGroupButton"
                        Layout.alignment: Qt.AlignRight
                        text: "＋  مجموعة"
                        enabled: groupModel.count < 50
                        onClicked: { groupModel.append({ weight: "", rate: "", description: "" }); newInvoiceDialog.recompute() }
                    }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: notesField2; objectName: "invoiceNotesField"; Layout.fillWidth: true }
                }
            }
            // The invoice totals computed by the same rules as the service (never by QML).
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: totalsRow.implicitHeight + 12
                radius: theme.radiusSmall
                color: theme.panel
                border.color: theme.border
                RowLayout {
                    id: totalsRow
                    anchors.fill: parent
                    anchors.margins: 6
                    layoutDirection: Qt.RightToLeft
                    spacing: 14
                    Txt { objectName: "invoiceTotalWeight"; text: "وزن المورد: " + newInvoiceDialog.preview.weightText; font.bold: true }
                    Txt { objectName: "invoiceTotalMaking"; text: "إجمالي المصنعيات: " + newInvoiceDialog.preview.makingText
                          color: theme.gold; font.bold: true }
                    Item { Layout.fillWidth: true }
                }
            }
            Txt { Layout.fillWidth: true; visible: !newInvoiceDialog.preview.valid || (!!page.pvm && page.pvm.errorMessage !== "")
                  text: !newInvoiceDialog.preview.valid ? newInvoiceDialog.preview.error : (page.pvm ? page.pvm.errorMessage : "")
                  color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "postInvoiceButton"
                    visible: !!page.pvm && page.pvm.canPost && newInvoiceDialog.editId === ""
                    text: "ترحيل الفاتورة"
                    primary: true
                    enabled: newInvoiceDialog.preview.valid && !!page.pvm && !page.pvm.busy && page.hasSelection
                    onClicked: page.pvm.saveInvoice(page.detail.id, newInvoiceDialog.invoiceKaratId, referenceField.text,
                                                    dateField.text, newInvoiceDialog.collect(), notesField2.text,
                                                    newInvoiceDialog.requestKey, true)
                }
                Btn {
                    objectName: "saveDraftButton"
                    visible: !!page.pvm && page.pvm.canEdit
                    text: newInvoiceDialog.editId !== "" ? "حفظ التعديلات" : "حفظ كمسودة"
                    enabled: newInvoiceDialog.preview.valid && !!page.pvm && !page.pvm.busy && page.hasSelection
                    onClicked: newInvoiceDialog.editId !== ""
                               ? page.pvm.updateDraft(newInvoiceDialog.editId, newInvoiceDialog.editSupplierId,
                                                      newInvoiceDialog.invoiceKaratId, referenceField.text, dateField.text,
                                                      newInvoiceDialog.collect(), notesField2.text)
                               : page.pvm.saveInvoice(page.detail.id, newInvoiceDialog.invoiceKaratId, referenceField.text,
                                                      dateField.text, newInvoiceDialog.collect(), notesField2.text,
                                                      newInvoiceDialog.requestKey, false)
                }
                Btn { text: "إلغاء"; accent: theme.textSecondary; onClicked: newInvoiceDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    readonly property var masterKarats: {
        var rows = typeof masterDataVM !== "undefined" ? masterDataVM.karats : []
        var out = []
        for (var i = 0; i < rows.length; ++i)
            out.push({ id: rows[i].id, karat_value: rows[i].karat_value, label: "عيار " + rows[i].karat_value })
        return out
    }
    // The karats master for the supplier form. Entry 0 is the explicit "not chosen" placeholder: nothing defaults.
    readonly property var karatChoices: {
        var out = [{ id: 0, label: "اختر العيار *" }]
        for (var i = 0; i < masterKarats.length; ++i) out.push({ id: masterKarats[i].id, label: masterKarats[i].label })
        return out
    }
    function karatChoiceIndex(id) {
        for (var i = 0; i < karatChoices.length; ++i) if (karatChoices[i].id === id) return i
        return 0
    }

    // ---------------------------------------------------------------- invoice detail + piece-by-piece intake
    // Layout: a fixed header (reference, supplier, status, actions, close) over ONE scrolling body of sections:
    // invoice summary, making groups, piece form, entered pieces. Presentation only: every value is the one the
    // ViewModel already formats; the actions, validation and request keys are unchanged.
    Popup {
        id: intakeDialog
        objectName: "supplierIntakeDialog"
        property string selectedGroup: ""
        property string pieceKey: ""
        property string postKey: ""
        property string pieceProblem: ""          // the known refusal for the typed weight (refreshPieceProblem)
        readonly property var inv: page.pvm ? page.pvm.detail : ({})
        readonly property bool ready: !!inv && inv.id !== undefined
        // entered-pieces table columns (header and rows share them)
        readonly property int colId: 76
        readonly property int colGroup: 92
        readonly property int colWeight: 112
        readonly property int colBarcode: 150
        readonly property int colStatus: 150
        // One field of the selected group (a plain value, no retained object); `fallback` when none is selected.
        function groupValue(field, fallback) {
            var rows = ready ? inv.groups : []
            for (var i = 0; i < rows.length; ++i) if (rows[i].id === selectedGroup) return rows[i][field]
            return fallback
        }
        // The refusal the service would give for the typed weight in the selected group, from the loaded detail.
        // Called from events only (typing, choosing a group, a fresh detail) — a convenience; the service decides.
        function refreshPieceProblem() {
            intakeDialog.pieceProblem = opened && ready && selectedGroup !== "" && !!page.pvm
                                ? page.pvm.pieceWeightProblem(selectedGroup, weightField.text) : ""
        }
        onInvChanged: refreshPieceProblem()
        onSelectedGroupChanged: refreshPieceProblem()
        function groupNo(id) {
            var rows = ready ? inv.groups : []
            for (var i = 0; i < rows.length; ++i) if (rows[i].id === id) return rows[i].no
            return ""
        }
        // Choosing a group is the start of an entry: bring the piece form into view when it is not fully visible
        // (small windows), and leave the scroll position alone when everything already fits.
        function revealForm() {
            if (!pieceForm.visible) return
            var top = pieceForm.mapToItem(intakeScroll.contentItem, 0, 0).y
            var bottom = top + pieceForm.height
            var maximum = Math.max(0, intakeScroll.contentHeight - intakeScroll.height)
            if (top >= intakeScroll.contentY && bottom <= intakeScroll.contentY + intakeScroll.height) return
            intakeScroll.contentY = Math.min(maximum, Math.max(0, top - 8))
        }
        function openFor(invoiceId) {
            selectedGroup = ""
            pieceKey = page.pvm ? page.pvm.newRequestKey() : ""
            postKey = page.pvm ? page.pvm.newRequestKey() : ""          // one post key per open: a double click is idempotent
            weightField.text = ""
            if (page.pvm) { page.pvm.clearMessage(); page.pvm.openInvoice(invoiceId) }
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(960, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 640, 780)
        modal: true
        padding: 0
        closePolicy: Popup.CloseOnEscape
        onClosed: { intakeDialog.pieceProblem = ""; if (page.pvm) page.pvm.closeInvoice() }
        background: Rectangle { color: theme.bgSecondary; radius: 8; border.color: theme.gold }
        Connections {
            target: page.pvm
            function onStockChanged() {
                intakeDialog.pieceKey = page.pvm.newRequestKey()      // the next piece is a new request
                weightField.text = ""
                weightField.forceActiveFocus()
            }
        }
        contentItem: ColumnLayout {
            spacing: 0

            // ---- 1. header (always visible: the close action never scrolls away)
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 68
                color: theme.bgTertiary
                radius: 8
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 8; color: parent.color }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.border }
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 14
                    anchors.rightMargin: 16
                    layoutDirection: Qt.RightToLeft
                    spacing: 10
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 10
                            Txt { text: intakeDialog.ready ? "فاتورة " + page.ltr(intakeDialog.inv.code) : "..."
                                  color: theme.gold; font.bold: true; font.pixelSize: theme.fontPageTitle; elide: Text.ElideNone }
                            Rectangle {
                                visible: intakeDialog.ready
                                implicitWidth: statusLabel.implicitWidth + 22
                                implicitHeight: 24
                                radius: 12
                                readonly property color tone: intakeDialog.ready && intakeDialog.inv.reversed ? theme.red
                                                              : intakeDialog.ready && intakeDialog.inv.posted ? theme.green : theme.textSecondary
                                color: Qt.rgba(tone.r, tone.g, tone.b, 0.14)
                                border.color: tone
                                Txt { id: statusLabel; anchors.centerIn: parent; text: intakeDialog.ready ? intakeDialog.inv.statusText : ""
                                      color: parent.tone; font.bold: true; font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone }
                            }
                            Item { Layout.fillWidth: true }
                        }
                        Txt { Layout.fillWidth: true; color: theme.textSecondary
                              text: intakeDialog.ready ? "المورد: " + intakeDialog.inv.supplierName
                                    + "  •  رقم فاتورة المورد: " + page.ltr(intakeDialog.inv.reference)
                                    + "  •  " + intakeDialog.inv.date : "" }
                    }
                    Btn { objectName: "intakePostButton"
                          visible: intakeDialog.ready && intakeDialog.inv.draft && !!page.pvm && page.pvm.canPost
                          text: "ترحيل الفاتورة"; primary: true; enabled: !page.pvm.busy
                          onClicked: page.pvm.postInvoice(intakeDialog.inv.id, intakeDialog.postKey) }
                    Btn { objectName: "editDraftButton"
                          visible: intakeDialog.ready && intakeDialog.inv.draft && !!page.pvm && page.pvm.canEdit
                          text: "تعديل المسودة"; enabled: !page.pvm.busy
                          onClicked: {
                              var form = intakeDialog.inv.form            // copied before the details dialog clears its invoice
                              intakeDialog.close()
                              newInvoiceDialog.openForEdit(form)
                          } }
                    Btn { visible: intakeDialog.ready && intakeDialog.inv.draft && !!page.pvm && page.pvm.canEdit
                          text: "إلغاء المسودة"; accent: theme.red; enabled: !page.pvm.busy
                          onClicked: { page.pvm.cancelDraft(intakeDialog.inv.id); intakeDialog.close() } }
                    Btn { objectName: "reverseInvoiceButton"
                          visible: intakeDialog.ready && intakeDialog.inv.canReverse === true && !!page.pvm && page.pvm.canReverse
                          text: "عكس الفاتورة"; accent: theme.red; enabled: !page.pvm.busy
                          onClicked: reverseInvoiceDialog.openFor(intakeDialog.inv) }
                    Btn { objectName: "intakeCloseButton"; text: "إغلاق"; accent: theme.textSecondary; onClicked: intakeDialog.close() }
                }
            }

            // a validation / service error stays visible whatever the scroll position
            Rectangle {
                visible: !!page.pvm && page.pvm.errorMessage !== ""
                Layout.fillWidth: true
                Layout.leftMargin: 14
                Layout.rightMargin: 14
                Layout.topMargin: 10
                implicitHeight: errorText.implicitHeight + 14
                radius: theme.radiusSmall
                color: Qt.rgba(theme.red.r, theme.red.g, theme.red.b, 0.12)
                border.color: theme.red
                Txt { id: errorText; objectName: "intakeError"; anchors.fill: parent; anchors.margins: 7
                      text: page.pvm ? page.pvm.errorMessage : ""; color: theme.red; font.bold: true
                      wrapMode: Text.WordWrap; elide: Text.ElideNone }
            }

            // ---- body: the only scroll area of the dialog
            Flickable {
                id: intakeScroll
                objectName: "intakeScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.leftMargin: 14
                Layout.topMargin: 12
                Layout.bottomMargin: 12
                contentWidth: width
                contentHeight: intakeColumn.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                ColumnLayout {
                    id: intakeColumn
                    width: intakeScroll.width - 14
                    spacing: 12
                    visible: intakeDialog.ready

                    // ---- reversed state: derived from the correction record; the original invoice and its entries are never changed
                    Rectangle {
                        objectName: "invoiceReversedBanner"
                        Layout.fillWidth: true
                        visible: intakeDialog.ready && intakeDialog.inv.reversed === true
                        implicitHeight: reversedColumn.implicitHeight + 16
                        radius: theme.radiusSmall
                        color: Qt.rgba(theme.red.r, theme.red.g, theme.red.b, 0.10)
                        border.color: theme.red
                        ColumnLayout {
                            id: reversedColumn
                            anchors.fill: parent; anchors.margins: 8
                            spacing: 2
                            Txt { objectName: "invoiceReversedTitle"; Layout.fillWidth: true; color: theme.red; font.bold: true
                                  text: intakeDialog.ready ? "هذه الفاتورة معكوسة بالعملية " + page.ltr(intakeDialog.inv.reversalCode) : "" }
                            Txt { Layout.fillWidth: true; color: theme.textSecondary; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                  text: intakeDialog.ready ? "السبب: " + intakeDialog.inv.reversalReason + "  •  " + intakeDialog.inv.reversedAt : "" }
                            Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                  text: "بقيت الفاتورة الأصلية وقيودها في السجل دون تعديل، وسُجّل قيد عكسي بنفس القيم. لا يمكن إدخال قطع منها." }
                        }
                    }

                    // ---- 2. invoice summary: contractual figures (row 1) and shop-scale intake figures (row 2)
                    Section {
                        title: "ملخص الفاتورة"
                        GridLayout {
                            Layout.fillWidth: true
                            columns: width >= 600 ? 4 : 2
                            layoutDirection: Qt.RightToLeft
                            columnSpacing: 12
                            rowSpacing: 10
                            uniformCellWidths: true
                            Stat { label: "العيار"; value: intakeDialog.ready ? "" + intakeDialog.inv.karat : ""; valueName: "intakeKarat"; strong: true }
                            Stat { label: "وزن المورد (جرام)"; value: intakeDialog.ready ? intakeDialog.inv.weightText : ""
                                   valueName: "intakeSupplierWeight"; strong: true }
                            Stat { label: "إجمالي المصنعيات"; value: intakeDialog.ready ? page.ltr(intakeDialog.inv.makingText) : ""
                                   valueName: "intakeMaking"; strong: true; valueColor: theme.gold }
                            Item { Layout.fillWidth: true; Layout.preferredWidth: 1 }          // keeps the contractual figures on row 1
                            Stat { label: "الوزن المُدخل (جرام)"; value: intakeDialog.ready ? intakeDialog.inv.actualText : ""
                                   valueName: "intakeActual"
                                   valueColor: intakeDialog.ready && intakeDialog.inv.overSupplier ? theme.amber : theme.textPrimary }
                            // neutral on purpose: a negative difference during incomplete intake is information, not an error
                            Stat { label: "فرق الوزن (جرام)"; value: intakeDialog.ready ? page.after(intakeDialog.inv.varianceText) : ""
                                   valueName: "intakeVariance" }
                            Stat { label: "المتبقي للإدخال (مرجعي، جرام)"; value: intakeDialog.ready ? page.after(intakeDialog.inv.remainingText) : ""
                                   valueName: "intakeRemaining"
                                   valueColor: intakeDialog.ready && intakeDialog.inv.overSupplier ? theme.amber : theme.textPrimary }
                            Stat { label: "نسبة الإدخال"; value: intakeDialog.ready ? intakeDialog.inv.percentText : ""
                                   valueName: "intakePercent"
                                   valueColor: intakeDialog.ready && intakeDialog.inv.overSupplier ? theme.amber : theme.textPrimary }
                        }
                        Txt { Layout.fillWidth: true; font.pixelSize: theme.fontTableHeader; color: theme.textMuted
                              text: "فرق الوزن للعلم فقط: لا يغيّر رصيد المورد ولا المصنعيات ولا أوزان القطع." }
                    }

                    // ---- 3. making-group selection
                    Section {
                        title: "مجموعات المصنعية"
                        bodySpacing: 6
                        hint: intakeDialog.selectedGroup === "" ? "اختر مجموعة لإدخال قطعة منها"
                              : "المجموعة المختارة: مجموعة " + intakeDialog.groupNo(intakeDialog.selectedGroup)
                        hintColor: intakeDialog.selectedGroup === "" ? theme.amber : theme.gold
                        Repeater {
                            model: intakeDialog.ready ? intakeDialog.inv.groups : []
                            delegate: Rectangle {
                                id: groupCard
                                objectName: "intakeGroup_" + index
                                readonly property bool selected: intakeDialog.selectedGroup === modelData.id
                                Layout.fillWidth: true
                                implicitHeight: groupBody.implicitHeight + 14
                                radius: theme.radiusSmall
                                color: selected ? Qt.rgba(theme.gold.r, theme.gold.g, theme.gold.b, 0.09)
                                       : groupMouse.containsMouse ? theme.panelHover : theme.bgSecondary
                                border.color: selected ? theme.gold : groupMouse.containsMouse ? theme.goldDark : theme.border
                                border.width: selected ? 2 : 1
                                MouseArea { id: groupMouse; anchors.fill: parent; hoverEnabled: true
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: { intakeDialog.selectedGroup = modelData.id; intakeDialog.revealForm() } }
                                ColumnLayout {
                                    id: groupBody
                                    anchors.fill: parent
                                    anchors.margins: 7
                                    anchors.leftMargin: 10
                                    anchors.rightMargin: 10
                                    spacing: 3
                                    RowLayout {
                                        Layout.fillWidth: true
                                        layoutDirection: Qt.RightToLeft
                                        spacing: 8
                                        Rectangle {                        // radio indicator
                                            implicitWidth: 18; implicitHeight: 18; radius: 9; color: "transparent"
                                            border.color: groupCard.selected ? theme.gold : theme.textMuted
                                            border.width: 2
                                            Rectangle { visible: groupCard.selected; anchors.centerIn: parent; width: 8; height: 8
                                                        radius: 4; color: theme.gold }
                                        }
                                        Txt { text: "مجموعة " + modelData.no; font.bold: true; font.pixelSize: theme.fontBodyStrong
                                              color: groupCard.selected ? theme.gold : theme.textPrimary; elide: Text.ElideNone }
                                        Txt { visible: modelData.description !== ""; text: "—  " + modelData.description
                                              color: theme.textSecondary }
                                        Item { Layout.fillWidth: true }
                                        Rectangle {
                                            visible: groupCard.selected
                                            implicitWidth: pickedLabel.implicitWidth + 20
                                            implicitHeight: 22
                                            radius: 11
                                            color: theme.gold
                                            Txt { id: pickedLabel; anchors.centerIn: parent; text: "✓  مختارة للإدخال"
                                                  color: theme.bgPrimary; font.bold: true; font.pixelSize: theme.fontTableHeader
                                                  elide: Text.ElideNone }
                                        }
                                        Txt { visible: !groupCard.selected; text: "اضغط للاختيار"; color: theme.textMuted
                                              font.pixelSize: theme.fontTableHeader }
                                    }
                                    // key contractual values first (prominent), then the five intake figures; both rows wrap naturally
                                    Flow {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: childrenRect.height      // the real wrapped height
                                        layoutDirection: Qt.RightToLeft
                                        spacing: 2
                                        Pair { label: "الفئة (من المصنعية)"; strong: true
                                               value: modelData.categoryResolved ? modelData.categoryText : "غير محددة"
                                               valueColor: modelData.categoryResolved ? theme.textPrimary : theme.amber }
                                        Pair { label: "وزن المورد (جرام)"; value: modelData.weightText; strong: true }
                                        Pair { label: "مصنعية الجرام"; value: page.ltr(modelData.rateText); strong: true }
                                        Pair { label: "إجمالي مصنعية المجموعة"; value: page.ltr(modelData.makingText); strong: true }
                                    }
                                    Flow {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: childrenRect.height      // the real wrapped height
                                        layoutDirection: Qt.RightToLeft
                                        spacing: 2
                                        Pair { label: "عدد القطع"; value: "" + modelData.pieceCount }
                                        Pair { label: "الوزن المُدخل (جرام)"; value: modelData.actualText
                                               valueColor: modelData.overSupplier ? theme.amber : theme.textPrimary }
                                        Pair { label: "فرق الوزن (جرام)"; value: page.after(modelData.varianceText) }
                                        Pair { label: "المتبقي للإدخال (مرجعي، جرام)"; value: page.after(modelData.remainingText)
                                               valueColor: modelData.overSupplier ? theme.amber : theme.textPrimary }
                                        Pair { label: "نسبة الإدخال"; value: modelData.percentText
                                               valueColor: modelData.overSupplier ? theme.amber : theme.textPrimary }
                                    }
                                }
                            }
                        }
                    }

                    // ---- 4. piece-entry form (posted invoices only)
                    Section {
                        id: pieceForm
                        objectName: "pieceForm"
                        visible: intakeDialog.ready && intakeDialog.inv.posted && !!page.pvm && page.pvm.canIntake
                        title: "إدخال قطعة"
                        hint: intakeDialog.selectedGroup === "" ? "اختر المجموعة أولًا"
                              : "إلى مجموعة " + intakeDialog.groupNo(intakeDialog.selectedGroup)
                        hintColor: intakeDialog.selectedGroup === "" ? theme.amber : theme.gold
                        // The tolerance-inclusive allowance of the selected group (separate from the reference remaining
                        // weight above), and the refusal the service would give — known from the loaded detail.
                        // Resolved by the service from the group's making rate and the existing category ranges, read-only here:
                        // the employee never chooses it and the item type never overrides it.
                        Rectangle {
                            objectName: "pieceInheritance"
                            visible: intakeDialog.selectedGroup !== ""
                            Layout.fillWidth: true
                            implicitHeight: inheritRow.implicitHeight + 14
                            radius: theme.radiusSmall
                            color: theme.bgTertiary
                            border.color: theme.border
                            RowLayout {
                                id: inheritRow
                                anchors.fill: parent
                                anchors.margins: 7
                                layoutDirection: Qt.RightToLeft
                                spacing: 16
                                Txt { text: "تحدَّد من مصنعية المجموعة:"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                      elide: Text.ElideNone }
                                Txt { objectName: "pieceInheritedCategory"; font.bold: true; elide: Text.ElideNone
                                      color: intakeDialog.groupValue("categoryResolved", false) === true ? theme.gold : theme.amber
                                      text: intakeDialog.groupValue("categoryResolved", false) === true
                                            ? "الفئة: " + intakeDialog.groupValue("categoryText", "") : "الفئة غير محددة" }
                                Txt { objectName: "pieceInheritedRate"; elide: Text.ElideNone
                                      text: "مصنعية الجرام: " + page.ltr(intakeDialog.groupValue("rateText", "—")) }
                                Txt { objectName: "pieceInheritedKarat"; elide: Text.ElideNone
                                      text: "عيار " + (intakeDialog.ready ? intakeDialog.inv.karat : "") }
                                Item { Layout.fillWidth: true }
                            }
                        }
                        Txt { objectName: "pieceCategoryProblem"; Layout.fillWidth: true; font.bold: true; color: theme.amber
                              wrapMode: Text.WordWrap; elide: Text.ElideNone
                              visible: intakeDialog.selectedGroup !== "" && intakeDialog.groupValue("categoryResolved", false) !== true
                              text: intakeDialog.groupValue("categoryProblem", "") }
                        Txt { objectName: "pieceAllowance"; Layout.fillWidth: true; font.bold: true
                              visible: intakeDialog.groupValue("allowanceLine", "") !== ""
                              text: intakeDialog.groupValue("allowanceLine", "")
                              color: intakeDialog.groupValue("overLimit", false) ? theme.amber : theme.gold }
                        Txt { objectName: "pieceProblem"; Layout.fillWidth: true; font.bold: true; color: theme.red
                              wrapMode: Text.WordWrap; elide: Text.ElideNone
                              visible: intakeDialog.pieceProblem !== "" || intakeDialog.groupValue("overLimit", false) === true
                              text: intakeDialog.pieceProblem !== "" ? intakeDialog.pieceProblem : intakeDialog.groupValue("overLimitText", "") }
                        GridLayout {
                            id: pieceGrid
                            Layout.fillWidth: true
                            columns: width >= 640 ? 3 : (width >= 430 ? 2 : 1)
                            layoutDirection: Qt.RightToLeft
                            columnSpacing: 12
                            rowSpacing: 10
                            uniformCellWidths: true
                            FieldBox {
                                label: "الصنف"; required: true
                                Combo { id: itemTypeCombo; objectName: "pieceItemTypeCombo"; Layout.fillWidth: true
                                        model: typeof masterDataVM !== "undefined" ? masterDataVM.itemTypes : []
                                        textRole: "name" }
                            }
                            FieldBox {
                                label: "الوزن على ميزان المحل"; required: true
                                RowLayout {
                                    Layout.fillWidth: true
                                    layoutDirection: Qt.RightToLeft
                                    spacing: 6
                                    Field { id: weightField; objectName: "pieceWeightField"; Layout.fillWidth: true
                                            inputMethodHints: Qt.ImhFormattedNumbersOnly; placeholderText: "0.000"
                                            onTextChanged: intakeDialog.refreshPieceProblem() }
                                    Txt { text: "جرام"; color: theme.textSecondary; elide: Text.ElideNone }
                                }
                            }
                            FieldBox {
                                label: "المقاس"
                                Field { id: sizeField; objectName: "pieceSizeField"; Layout.fillWidth: true }
                            }
                            FieldBox {
                                label: "المصنع"
                                Combo { id: factoryCombo; objectName: "pieceFactoryCombo"; Layout.fillWidth: true
                                        model: [{ id: 0, name: "غير محدد" }].concat(typeof masterDataVM !== "undefined" ? masterDataVM.factories : [])
                                        textRole: "name" }
                            }
                            FieldBox {
                                label: "الباركود"
                                Combo { id: barcodeMode; objectName: "pieceBarcodeMode"; Layout.fillWidth: true
                                        model: [{ code: "AUTO", name: "توليد تلقائي" }, { code: "MANUAL", name: "إدخال يدوي" },
                                                { code: "NONE", name: "بدون باركود" }]
                                        textRole: "name" }
                            }
                        }
                        FieldBox {
                            visible: barcodeMode.currentIndex === 1
                            label: "قيمة الباركود"; required: true
                            Field { id: manualBarcode; objectName: "pieceManualBarcode"; Layout.fillWidth: true
                                    placeholderText: "قيمة الباركود" }
                        }
                        FieldBox {
                            label: "ملاحظات القطعة (اختياري)"
                            Field { id: pieceNotes; objectName: "pieceNotesField"; Layout.fillWidth: true
                                    placeholderText: "ملاحظات القطعة (اختياري)" }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 10
                            Btn {
                                objectName: "addPieceButton"
                                text: "＋  إدخال القطعة"
                                primary: true
                                implicitWidth: 170
                                enabled: intakeDialog.selectedGroup !== "" && weightField.text.trim().length > 0
                                         && itemTypeCombo.currentIndex >= 0 && !!page.pvm && !page.pvm.busy
                                         && intakeDialog.pieceProblem === ""
                                         && intakeDialog.groupValue("categoryResolved", false) === true
                                onClicked: page.pvm.addPiece(
                                    intakeDialog.inv.id, intakeDialog.selectedGroup,
                                    itemTypeCombo.model[itemTypeCombo.currentIndex].id, weightField.text, intakeDialog.pieceKey,
                                    factoryCombo.model[factoryCombo.currentIndex].id, sizeField.text,
                                    barcodeMode.model[barcodeMode.currentIndex].code, manualBarcode.text, pieceNotes.text)
                            }
                            Txt { objectName: "pieceHint"
                                  visible: intakeDialog.selectedGroup === "" || weightField.text.trim().length === 0
                                           || intakeDialog.groupValue("categoryResolved", false) !== true
                                  text: intakeDialog.selectedGroup === "" ? "اختر المجموعة أولًا"
                                        : intakeDialog.groupValue("categoryResolved", false) !== true ? "الفئة غير محددة"
                                        : "أدخل وزن القطعة"
                                  color: theme.amber; font.bold: true; font.pixelSize: theme.fontTableHeader }
                            Item { Layout.fillWidth: true }
                        }
                        Txt { objectName: "lastPieceText"; Layout.fillWidth: true; color: theme.green; font.bold: true
                              visible: !!page.pvm && page.pvm.lastPiece.inventoryItemId !== undefined
                              text: page.pvm && page.pvm.lastPiece.inventoryItemId !== undefined
                                    ? "آخر قطعة: " + page.ltr("#" + page.pvm.lastPiece.inventoryItemId) + "  •  باركود "
                                      + page.ltr(page.pvm.lastPiece.barcode)
                                      + (page.pvm.lastPiece.categoryCode ? "  •  فئة " + page.ltr(page.pvm.lastPiece.categoryCode) : "")
                                      + "  •  " + page.pvm.lastPiece.weightText
                                      + "  •  مصنعية " + page.ltr(page.pvm.lastPiece.makingText) : ""
                              leftPadding: 10; rightPadding: 10; topPadding: 6; bottomPadding: 6
                              background: Rectangle { radius: theme.radiusSmall; color: Qt.rgba(theme.green.r, theme.green.g, theme.green.b, 0.10)
                                                      border.color: theme.green } }
                    }

                    // ---- 5. entered pieces: labelled columns, rows aligned to them
                    Section {
                        visible: intakeDialog.ready && (intakeDialog.inv.pieces.length > 0 || intakeDialog.inv.posted)
                        title: "القطع المُدخلة"
                        hint: intakeDialog.ready ? intakeDialog.inv.pieces.length + " قطعة" : ""
                        hintColor: theme.textMuted
                        Txt { visible: intakeDialog.ready && intakeDialog.inv.pieces.length === 0; Layout.fillWidth: true
                              text: "لم تُدخل أي قطعة بعد."; color: theme.textMuted }
                        Rectangle {
                            visible: intakeDialog.ready && intakeDialog.inv.pieces.length > 0
                            Layout.fillWidth: true
                            implicitHeight: 28
                            radius: 4
                            color: theme.bgTertiary
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 10; anchors.rightMargin: 12
                                layoutDirection: Qt.RightToLeft
                                spacing: 10
                                ColHead { Layout.preferredWidth: intakeDialog.colId; text: "رقم القطعة" }
                                ColHead { Layout.preferredWidth: intakeDialog.colGroup; text: "المجموعة" }
                                ColHead { Layout.fillWidth: true; Layout.minimumWidth: 120; text: "الصنف / المقاس" }
                                ColHead { Layout.preferredWidth: intakeDialog.colWeight; text: "الوزن (جرام)" }
                                ColHead { Layout.preferredWidth: intakeDialog.colBarcode; text: "الباركود" }
                                ColHead { Layout.preferredWidth: intakeDialog.colStatus; text: "الحالة" }
                            }
                        }
                        Repeater {
                            model: intakeDialog.ready ? intakeDialog.inv.pieces : []
                            delegate: Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 36
                                radius: 4
                                color: index % 2 === 0 ? theme.bgSecondary : "transparent"
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 10; anchors.rightMargin: 12
                                    layoutDirection: Qt.RightToLeft
                                    spacing: 10
                                    Txt { Layout.preferredWidth: intakeDialog.colId; text: page.ltr("#" + modelData.itemId); color: theme.gold }
                                    Txt { Layout.preferredWidth: intakeDialog.colGroup; text: "مجموعة " + modelData.groupNo }
                                    Txt { Layout.fillWidth: true; Layout.minimumWidth: 120
                                          text: modelData.itemType + (modelData.size !== "" ? "  " + modelData.size : "") }
                                    Txt { Layout.preferredWidth: intakeDialog.colWeight; text: modelData.weightText; font.bold: true }
                                    Txt { Layout.preferredWidth: intakeDialog.colBarcode
                                          text: modelData.hasBarcode ? page.ltr(modelData.barcode) : modelData.barcode
                                          color: modelData.hasBarcode ? theme.textPrimary : theme.textMuted }
                                    Txt { Layout.preferredWidth: intakeDialog.colStatus; text: modelData.statusText; color: theme.textSecondary
                                          font.pixelSize: theme.fontTableHeader }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
