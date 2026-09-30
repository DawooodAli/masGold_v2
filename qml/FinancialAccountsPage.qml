import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// الخزنة / حركة النقدية — the daily cash screen (prototype cash_flow_page): النقدية الداخلة on the right,
// النقدية الخارجة on the left, each row = movement name + amount only. Every name, amount and total comes
// formatted from financialAccountsVM; nothing is calculated here. No bank / wallet balances on this page.
Rectangle {
    id: page
    objectName: "financialAccountsPage"
    property string pageTitle: ""
    property var vm: typeof financialAccountsVM !== "undefined" ? financialAccountsVM : null
    signal invoiceRequested(string invoiceId)
    signal correctionRequested(string operationId)
    color: theme.bgPrimary
    Theme { id: theme }

    onVisibleChanged: if (page.vm) { if (visible) page.vm.activate(); else page.vm.deactivate() }
    Component.onCompleted: if (visible && page.vm) page.vm.activate()

    property string appliedFilters: ""
    function syncFilters() {
        if (!page.vm) return
        var f = page.vm.filters
        var key = JSON.stringify(f) + page.vm.labels.length
        if (key === appliedFilters) return
        appliedFilters = key
        start.text = f.start; end.text = f.end
        labelFilter.currentIndex = Math.max(0, labelFilter.indexOfValue(f.label))
    }

    Connections {
        target: page.vm
        function onChanged() { Qt.callLater(page.syncFilters) }
        function onInvoiceRequested(invoiceId) { page.invoiceRequested(invoiceId) }
        function onCorrectionRequested(operationId) { detailDialog.close(); page.correctionRequested(operationId) }
        function onFinalized(code) { movementDialog.close() }
    }

    component Txt: Label {
        color: theme.textPrimary; font.family: "Cairo"; font.pixelSize: theme.fontBody
        horizontalAlignment: Text.AlignRight; LayoutMirroring.enabled: false
        elide: Text.ElideLeft
    }
    component Field: TextField {
        font.family: "Cairo"; font.pixelSize: theme.fontBody; horizontalAlignment: TextInput.AlignRight
        color: theme.textPrimary; selectByMouse: true; implicitHeight: 34
        background: Rectangle { color: theme.bgSecondary; radius: theme.radiusSmall; border.color: theme.border }
    }
    component Btn: Button {
        id: btn
        property bool primary: false
        property bool chosen: false
        font.family: "Cairo"; font.pixelSize: theme.fontBody; implicitHeight: 34
        contentItem: Label { text: btn.text; font: btn.font; horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            color: !btn.enabled ? theme.textDisabled : btn.primary ? theme.bgPrimary : btn.chosen ? theme.gold : theme.textPrimary }
        background: Rectangle { radius: theme.radiusSmall
            color: btn.primary && btn.enabled ? theme.gold : btn.hovered ? theme.panelHover : theme.bgTertiary
            border.color: btn.primary || btn.chosen ? theme.gold : theme.border }
    }
    component Combo: ComboBox {
        id: combo
        font.family: "Cairo"; font.pixelSize: theme.fontBody; implicitHeight: 34
        contentItem: Txt { text: combo.displayText; verticalAlignment: Text.AlignVCenter; rightPadding: 8; leftPadding: 26 }
        background: Rectangle { color: theme.bgSecondary; radius: theme.radiusSmall; border.color: theme.border }
        delegate: ItemDelegate { width: combo.width; font.family: "Cairo"
            contentItem: Txt { text: modelData.text || modelData.name || "" } }
    }

    // One cash table: title, «الحركة | المبلغ», rows, total.
    component CashTable: Rectangle {
        id: table
        property string title: ""
        property color accent: theme.green
        property var rows: []
        property string total: ""
        property string totalLabel: ""
        property string prefix: ""
        color: theme.bgSecondary; radius: theme.radiusMedium; border.color: theme.border
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 10; spacing: 6
            Txt { text: table.title; color: table.accent; font.bold: true; font.pixelSize: theme.fontSection
                  horizontalAlignment: Text.AlignHCenter; Layout.fillWidth: true }
            Rectangle {
                Layout.fillWidth: true; implicitHeight: 28; color: theme.bgTertiary; radius: theme.radiusSmall
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10; layoutDirection: Qt.RightToLeft
                    spacing: 8
                    Txt { text: "الحركة"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                          Layout.fillWidth: true; Layout.preferredWidth: 68 }
                    Txt { text: "المبلغ"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                          horizontalAlignment: Text.AlignRight; Layout.fillWidth: true; Layout.preferredWidth: 32 }
                }
            }
            ListView {
                id: list; objectName: table.prefix + "List"
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 2
                model: table.rows
                ScrollBar.vertical: ScrollBar {}
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    objectName: table.prefix + "Row_" + index
                    width: list.width; height: 34; radius: theme.radiusSmall
                    color: rowArea.containsMouse && modelData.linked ? theme.panelHover : "transparent"
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10; layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Txt { objectName: table.prefix + "Label_" + index; text: modelData.label
                              Layout.fillWidth: true; Layout.preferredWidth: 68 }
                        Txt { objectName: table.prefix + "Amount_" + index; text: modelData.amount; font.bold: true
                              color: table.accent; horizontalAlignment: Text.AlignRight
                              Layout.fillWidth: true; Layout.preferredWidth: 32 }
                    }
                    MouseArea { id: rowArea; anchors.fill: parent; hoverEnabled: true; enabled: modelData.linked
                                cursorShape: modelData.linked ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: page.vm.openRow(modelData.key) }
                }
                Txt { anchors.centerIn: parent; visible: list.count === 0 && !!page.vm && !page.vm.busy
                      text: "لا توجد حركات في هذه الفترة"; color: theme.textMuted }
            }
            // The summary bar: its amount is the strongest element of the table (~1.5× a row).
            Rectangle {
                objectName: table.prefix + "TotalBar"
                Layout.fillWidth: true; implicitHeight: totalRow.implicitHeight + 16
                radius: theme.radiusSmall; color: theme.bgTertiary; border.color: table.accent; border.width: 1
                RowLayout {
                    id: totalRow
                    anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10; layoutDirection: Qt.RightToLeft
                    spacing: 8
                    Txt { objectName: table.prefix + "TotalLabel"; text: table.totalLabel; font.bold: true
                          font.pixelSize: theme.fontSection; color: table.accent
                          Layout.fillWidth: true; Layout.preferredWidth: 55 }
                    Txt { objectName: table.prefix + "Total"; text: table.total; font.bold: true
                          font.pixelSize: Math.round(theme.fontBody * 1.5); color: table.accent
                          horizontalAlignment: Text.AlignRight; Layout.fillWidth: true; Layout.preferredWidth: 45 }
                }
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 14; spacing: 10

        // -------------------------------------------------------------- header: the safe and its balance
        Rectangle {
            Layout.fillWidth: true; implicitHeight: headRow.implicitHeight + 18
            color: theme.bgSecondary; radius: theme.radiusMedium; border.color: theme.border
            RowLayout {
                id: headRow; anchors.fill: parent; anchors.margins: 9; layoutDirection: Qt.RightToLeft; spacing: 12
                ColumnLayout {
                    objectName: "cashSafeBlock"
                    spacing: 2
                    Txt { objectName: "cashAccountName"; text: page.vm && page.vm.accountName ? page.vm.accountName : "الخزنة"
                          font.pixelSize: theme.fontPageTitle; font.bold: true; color: theme.gold; Layout.alignment: Qt.AlignRight }
                    RowLayout {
                        layoutDirection: Qt.RightToLeft; spacing: 10; Layout.alignment: Qt.AlignRight
                        Txt { text: "الرصيد الحالي"; color: theme.textMuted; font.pixelSize: theme.fontBody
                              Layout.alignment: Qt.AlignVCenter }
                        Txt { objectName: "cashBalance"; text: page.vm ? page.vm.balanceText : "—"
                              font.pixelSize: theme.fontNumber; font.bold: true; Layout.alignment: Qt.AlignVCenter
                              color: page.vm && page.vm.balanceNegative ? theme.amber : theme.balanceCash }
                    }
                }
                Item { Layout.fillWidth: true }
                Btn { objectName: "cashNewMovement"; text: "حركة نقدية جديدة"; primary: true
                      visible: !!page.vm && page.vm.canOperate; enabled: !page.vm.saving
                      onClicked: movementDialog.begin() }
                Btn { objectName: "cashRefresh"; text: "تحديث"; enabled: !!page.vm && !page.vm.busy
                      onClicked: page.vm.refresh() }
            }
        }

        // -------------------------------------------------------------- minimal filters
        Flow {
            objectName: "cashFilters"
            Layout.fillWidth: true; spacing: 6; layoutDirection: Qt.RightToLeft
            Txt { text: "من"; height: 34; verticalAlignment: Text.AlignVCenter }
            Field { id: start; objectName: "cashStart"; width: 115; placeholderText: "YYYY-MM-DD" }
            Txt { text: "إلى"; height: 34; verticalAlignment: Text.AlignVCenter }
            Field { id: end; objectName: "cashEnd"; width: 115; placeholderText: "YYYY-MM-DD" }
            Combo { id: labelFilter; objectName: "cashLabelFilter"; width: 160
                    model: page.vm ? page.vm.labels : []; textRole: "text"; valueRole: "code" }
            Btn { objectName: "cashApplyFilter"; text: "عرض"; enabled: !!page.vm && !page.vm.busy
                  onClicked: page.vm.filter(start.text, end.text, labelFilter.currentValue || "") }
            Txt { objectName: "cashMessage"; height: 34; verticalAlignment: Text.AlignVCenter
                  text: page.vm ? (page.vm.errorMessage || page.vm.message) : ""; visible: text.length > 0
                  color: page.vm && page.vm.errorMessage ? theme.red : theme.green }
        }

        // -------------------------------------------------------------- the two tables (داخل right, خارج left)
        RowLayout {
            objectName: "cashTables"
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12; layoutDirection: Qt.RightToLeft
            CashTable {
                objectName: "cashInTable"; prefix: "cashIn"
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1
                title: "📥 النقدية الداخلة"; accent: theme.green; totalLabel: "إجمالي النقدية الداخلة"
                rows: page.vm ? page.vm.inRows : []; total: page.vm ? page.vm.inTotal : ""
            }
            CashTable {
                objectName: "cashOutTable"; prefix: "cashOut"
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1
                title: "📤 النقدية الخارجة"; accent: theme.accountPayable; totalLabel: "إجمالي النقدية الخارجة"
                rows: page.vm ? page.vm.outRows : []; total: page.vm ? page.vm.outTotal : ""
            }
        }
    }

    // ------------------------------------------------------------------ حركة نقدية جديدة
    Dialog {
        id: movementDialog; objectName: "cashMovementDialog"
        property string direction: "OUT"
        property bool confirming: false
        readonly property var types: page.vm && opened ? page.vm.typesFor(direction) : []
        readonly property var chosen: kind.currentIndex >= 0 && kind.currentIndex < types.length ? types[kind.currentIndex] : null
        readonly property string confirmation: page.vm && opened && chosen
            ? page.vm.confirmText(direction, chosen.code, amount.text, bank.currentValue || "") : ""
        function begin() {
            direction = "OUT"; confirming = false; amount.text = ""; note.text = ""
            page.vm.beginMovement()
            kind.currentIndex = 0; bank.currentIndex = -1
            open()
        }
        function setDirection(value) { direction = value; kind.currentIndex = 0; bank.currentIndex = -1 }
        anchors.centerIn: parent; width: Math.min(page.width - 24, 480)
        modal: true; font.family: "Cairo"
        closePolicy: page.vm && page.vm.saving ? Popup.NoAutoClose : Popup.CloseOnEscape
        onClosed: if (page.vm) page.vm.cancelMovement()
        background: Rectangle { color: theme.bgPrimary; radius: theme.radiusMedium; border.color: theme.goldDark }
        contentItem: ColumnLayout {
            spacing: 8
            Txt { text: "حركة نقدية جديدة"; font.bold: true; font.pixelSize: theme.fontSection; color: theme.gold
                  Layout.fillWidth: true }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                Txt { text: "الاتجاه"; Layout.preferredWidth: 80 }
                Btn { objectName: "cashDirectionIn"; text: "داخل"; chosen: movementDialog.direction === "IN"
                      enabled: !movementDialog.confirming; Layout.fillWidth: true; onClicked: movementDialog.setDirection("IN") }
                Btn { objectName: "cashDirectionOut"; text: "خارج"; chosen: movementDialog.direction === "OUT"
                      enabled: !movementDialog.confirming; Layout.fillWidth: true; onClicked: movementDialog.setDirection("OUT") }
            }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                Txt { text: "نوع الحركة"; Layout.preferredWidth: 80 }
                Combo { id: kind; objectName: "cashMovementType"; Layout.fillWidth: true; enabled: !movementDialog.confirming
                        model: movementDialog.types; textRole: "text"; valueRole: "code"
                        onActivated: bank.currentIndex = -1 }
            }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                visible: !!movementDialog.chosen && movementDialog.chosen.needsBank
                Txt { text: "الحساب"; Layout.preferredWidth: 80 }
                Combo { id: bank; objectName: "cashBankAccount"; Layout.fillWidth: true; enabled: !movementDialog.confirming
                        displayText: currentIndex < 0 ? "اختر البنك / المحفظة" : currentText
                        model: page.vm ? page.vm.bankAccounts : []; textRole: "name"; valueRole: "key" }
            }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                Txt { text: "المبلغ"; Layout.preferredWidth: 80 }
                Field { id: amount; objectName: "cashAmount"; Layout.fillWidth: true; placeholderText: "0.00"
                        enabled: !movementDialog.confirming; inputMethodHints: Qt.ImhFormattedNumbersOnly }
            }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                Txt { text: "ملاحظة"; Layout.preferredWidth: 80 }
                Field { id: note; objectName: "cashNote"; Layout.fillWidth: true; placeholderText: "اختياري"
                        enabled: !movementDialog.confirming }
            }
            Txt { objectName: "cashConfirmText"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.bold: true
                  visible: movementDialog.confirming; text: movementDialog.confirmation; color: theme.amber }
            Txt { Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.red
                  text: page.vm ? page.vm.errorMessage : ""; visible: text.length > 0 }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                Btn { objectName: "cashMovementNext"; primary: true; visible: !movementDialog.confirming; text: "متابعة"
                      enabled: movementDialog.confirmation.length > 0; onClicked: movementDialog.confirming = true }
                Btn { objectName: "cashMovementConfirm"; primary: true; visible: movementDialog.confirming
                      text: page.vm && page.vm.saving ? "جارٍ التسجيل…" : "تأكيد"; enabled: !!page.vm && !page.vm.saving
                      onClicked: page.vm.submit(movementDialog.direction, movementDialog.chosen.code, amount.text,
                                                note.text, bank.currentValue || "") }
                Btn { text: "تعديل"; visible: movementDialog.confirming; enabled: !!page.vm && !page.vm.saving
                      onClicked: movementDialog.confirming = false }
                Item { Layout.fillWidth: true }
                Btn { objectName: "cashMovementCancel"; text: "إلغاء"; enabled: !!page.vm && !page.vm.saving
                      onClicked: movementDialog.close() }
            }
        }
    }

    // ------------------------------------------------------------------ manual movement detail (immutable)
    Dialog {
        id: detailDialog; objectName: "cashDetailDialog"
        readonly property var d: page.vm ? page.vm.detail : ({})
        visible: !!d.code
        anchors.centerIn: parent; width: Math.min(page.width - 24, 460)
        modal: true; font.family: "Cairo"
        onClosed: if (page.vm) page.vm.closeOperation()
        background: Rectangle { color: theme.bgPrimary; radius: theme.radiusMedium; border.color: theme.goldDark }
        contentItem: ColumnLayout {
            spacing: 5
            Txt { text: (detailDialog.d.typeText || "") + " " + (detailDialog.d.code || ""); font.bold: true
                  font.pixelSize: theme.fontSection; color: theme.gold; Layout.fillWidth: true }
            Txt { text: "المبلغ: " + (detailDialog.d.amount || ""); Layout.fillWidth: true }
            Txt { text: "من: " + detailDialog.d.from; visible: !!detailDialog.d.from; Layout.fillWidth: true }
            Txt { text: "إلى: " + detailDialog.d.to; visible: !!detailDialog.d.to; Layout.fillWidth: true }
            Txt { text: "ملاحظة: " + (detailDialog.d.reason || ""); wrapMode: Text.Wrap; Layout.fillWidth: true }
            Txt { text: (detailDialog.d.date || "") + " · " + (detailDialog.d.actor || ""); color: theme.textMuted
                  Layout.fillWidth: true }
            Txt { objectName: "cashDetailReversed"; visible: !!detailDialog.d.reversedBy; color: theme.amber
                  wrapMode: Text.Wrap; Layout.fillWidth: true
                  text: "معكوسة بـ " + (detailDialog.d.reversedBy || "") + " — " + (detailDialog.d.reversalReason || "") }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                Btn { objectName: "cashDetailReverse"; text: "عكس العملية"; visible: !!detailDialog.d.canReverse
                      onClicked: page.vm.reverseOperation() }
                Item { Layout.fillWidth: true }
                Btn { text: "إغلاق"; onClicked: detailDialog.close() }
            }
        }
    }
}
