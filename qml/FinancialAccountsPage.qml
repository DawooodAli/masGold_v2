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
    signal customerRequested(string customerId)
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
        start.value = f.start; end.value = f.end
        labelFilter.currentIndex = Math.max(0, labelFilter.indexOfValue(f.label))
    }

    Connections {
        target: page.vm
        function onChanged() { Qt.callLater(page.syncFilters) }
        function onInvoiceRequested(invoiceId) { page.invoiceRequested(invoiceId) }
        function onCorrectionRequested(operationId) { detailDialog.close(); page.correctionRequested(operationId) }
        function onCustomerRequested(customerId) { page.customerRequested(customerId) }
        function onFinalized(code) { movementDialog.close(); transferDialog.close() }
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
    // Unified app button (same look as the invoice-history «Btn»): a real hover state (the
    // default Button ignores the mouse until hoverEnabled is set), a pressed state, short
    // colour transitions and breathing padding. `chosen` marks the selected account chip.
    component Btn: Button {
        id: btn
        property bool primary: false
        property bool chosen: false
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        implicitHeight: 34
        hoverEnabled: true
        padding: 12
        contentItem: Label {
            text: btn.text
            font.family: btn.font.family
            font.pixelSize: btn.font.pixelSize
            font.bold: btn.primary || btn.chosen
            color: !btn.enabled ? theme.textDisabled
                   : btn.primary || btn.chosen ? (btn.hovered ? theme.goldBright : theme.gold)
                   : btn.hovered ? theme.textPrimary : theme.textSecondary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            Behavior on color { ColorAnimation { duration: 120 } }
        }
        background: Rectangle {
            radius: theme.radiusSmall
            color: !btn.enabled ? theme.bgSecondary
                   : btn.primary || btn.chosen ? (btn.down ? theme.goldDark : btn.hovered ? "#3A2F1A" : "#2A2214")
                   : btn.down ? theme.bgPrimary : btn.hovered ? theme.panelHover : theme.bgTertiary
            border.width: 1
            border.color: !btn.enabled ? theme.border
                          : btn.primary || btn.chosen ? (btn.down ? theme.goldBright : theme.gold)
                          : (btn.hovered || btn.activeFocus) ? theme.goldDark : theme.border
            Behavior on color { ColorAnimation { duration: 120 } }
            Behavior on border.color { ColorAnimation { duration: 120 } }
        }
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
        property string bankTotal: ""            // bank / wallet movements of the period: shown below, never inside the safe total
        property string totalLabel: ""
        property string prefix: ""
        color: theme.bgSecondary; radius: theme.radiusCard; border.color: theme.cardBorder
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
                          Layout.preferredWidth: 130; Layout.maximumWidth: 130 }
                    Txt { text: "المرجع"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; Layout.fillWidth: true }
                    Txt { text: "المبلغ"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                          horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 110; Layout.maximumWidth: 110 }
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
                    width: list.width; height: 42; radius: theme.radiusSmall
                    color: rowArea.containsMouse && modelData.linked ? theme.panelHover : "transparent"
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10; layoutDirection: Qt.RightToLeft
                        spacing: 8
                        // 1) الحركة: the movement name only.
                        Txt { objectName: table.prefix + "Label_" + index; text: modelData.label; font.bold: true
                              elide: Text.ElideRight; Layout.preferredWidth: 130; Layout.maximumWidth: 130; Layout.alignment: Qt.AlignVCenter }
                        // 2) المرجع: FO / invoice number / RV..., then the customer and the detail.
                        RowLayout {
                            objectName: table.prefix + "Info_" + index
                            Layout.fillWidth: true; spacing: 8; layoutDirection: Qt.RightToLeft
                            Txt { objectName: table.prefix + "Ref_" + index; text: modelData.reference
                                  visible: text.length > 0; color: modelData.linked ? theme.gold : theme.textSecondary
                                  font.underline: modelData.linked && rowArea.containsMouse
                                  Layout.alignment: Qt.AlignVCenter; Layout.maximumWidth: 210 }
                            Txt { objectName: table.prefix + "Customer_" + index; text: modelData.customer
                                  visible: text.length > 0; color: theme.textMuted; Layout.fillWidth: true
                                  Layout.alignment: Qt.AlignVCenter
                                  font.underline: modelData.customerId.length > 0 && customerArea.containsMouse
                                  MouseArea { id: customerArea; anchors.fill: parent; hoverEnabled: true
                                              enabled: modelData.customerId.length > 0
                                              cursorShape: modelData.customerId.length > 0 ? Qt.PointingHandCursor : Qt.ArrowCursor
                                              onClicked: page.vm.openCustomer(modelData.customerId) } }
                            Txt { objectName: table.prefix + "Detail_" + index; text: modelData.detail
                                  visible: text.length > 0; color: theme.amber; elide: Text.ElideRight
                                  Layout.alignment: Qt.AlignVCenter; Layout.maximumWidth: 200 }
                            Item { Layout.fillWidth: true; visible: modelData.customer.length === 0 }
                        }
                        // 3) المبلغ: the amount, and under it the bank / wallet it went through (never the safe).
                        ColumnLayout {
                            Layout.preferredWidth: 110; Layout.maximumWidth: 110; spacing: 0; Layout.alignment: Qt.AlignVCenter
                            Txt { objectName: table.prefix + "Amount_" + index; text: modelData.amount; font.bold: true
                                  color: table.accent; horizontalAlignment: Text.AlignLeft; Layout.fillWidth: true }
                            Txt { objectName: table.prefix + "Account_" + index; text: "🏦 " + modelData.account
                                  visible: modelData.external; color: theme.gold; font.pixelSize: theme.fontCaption
                                  horizontalAlignment: Text.AlignLeft; Layout.fillWidth: true }
                        }
                    }
                    // z: -1 keeps the row click UNDER the customer link (the link wins where they overlap).
                    MouseArea { id: rowArea; anchors.fill: parent; z: -1; hoverEnabled: true; enabled: modelData.linked
                                cursorShape: modelData.linked ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: page.vm.openRow(modelData.key) }
                }
                Txt { anchors.centerIn: parent; visible: list.count === 0 && !!page.vm && !page.vm.busy
                      text: "لا توجد حركات في هذه الفترة"; color: theme.textMuted }
            }
            RowLayout {
                objectName: table.prefix + "BankTotal"
                visible: table.bankTotal.length > 0; Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                Txt { text: "🏦 عن طريق البنوك والمحافظ (خارج رصيد الخزنة)"; color: theme.gold; Layout.fillWidth: true }
                Txt { text: table.bankTotal; color: theme.gold; font.bold: true }
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
            color: theme.bgSecondary; radius: theme.radiusCard; border.color: theme.cardBorder
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
                Btn { objectName: "cashNewMovement"; text: "مصروفات وإيرادات"; primary: true
                      visible: !!page.vm && page.vm.canOperate; enabled: !page.vm.saving
                      onClicked: movementDialog.begin() }
                Btn { objectName: "cashCustomerCash"; text: "حركة نقدية عملاء"
                      visible: !!page.vm && page.vm.canOperate
                      onClicked: customerCashDialog.begin() }
                Btn { objectName: "cashTransfer"; text: "⇄ تحويل بين الحسابات"
                      visible: !!page.vm && page.vm.canOperate
                      onClicked: transferDialog.begin() }
                Btn { objectName: "cashRefresh"; text: "تحديث"; enabled: !!page.vm && !page.vm.busy
                      onClicked: page.vm.refresh() }
            }
        }

        // -------------------------------------------------------------- minimal filters
        Flow {
            objectName: "cashFilters"
            Layout.fillWidth: true; spacing: 6; layoutDirection: Qt.RightToLeft
            Txt { text: "من"; height: 34; verticalAlignment: Text.AlignVCenter }
            DateField { id: start; objectName: "cashStart"; width: 138; height: 34 }
            Txt { text: "إلى"; height: 34; verticalAlignment: Text.AlignVCenter }
            DateField { id: end; objectName: "cashEnd"; width: 138; height: 34 }
            Combo { id: labelFilter; objectName: "cashLabelFilter"; width: 160
                    model: page.vm ? page.vm.labels : []; textRole: "text"; valueRole: "code" }
            Btn { objectName: "cashApplyFilter"; text: "عرض"; enabled: !!page.vm && !page.vm.busy
                  onClicked: page.vm.filter(start.value, end.value, labelFilter.currentValue || "") }
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
                bankTotal: page.vm ? page.vm.inBankTotal : ""
            }
            CashTable {
                objectName: "cashOutTable"; prefix: "cashOut"
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1
                title: "📤 النقدية الخارجة"; accent: theme.accountPayable; totalLabel: "إجمالي النقدية الخارجة"
                rows: page.vm ? page.vm.outRows : []; total: page.vm ? page.vm.outTotal : ""
                bankTotal: page.vm ? page.vm.outBankTotal : ""
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

    // ------------------------------------------------------------------ تحويل بين الحسابات: Main Safe <-> banks <-> wallets (one TR document, never income or expense)
    Dialog {
        id: transferDialog; objectName: "transferDialog"
        readonly property var accounts: [{ "key": page.vm && page.vm.accountKey ? page.vm.accountKey : "", "name": page.vm && page.vm.accountName ? page.vm.accountName : "الخزنة" }].concat(page.vm ? page.vm.bankAccounts : [])
        function begin() { trAmount.text = ""; trNote.text = ""; trFrom.currentIndex = 0; trTo.currentIndex = accounts.length > 1 ? 1 : 0; page.vm.prepareTransfer(); open(); trAmount.forceActiveFocus() }
        anchors.centerIn: parent; width: Math.min(page.width - 24, 640); modal: true; font.family: "Cairo"
        closePolicy: page.vm && page.vm.transferBusy ? Popup.NoAutoClose : Popup.CloseOnEscape
        background: Rectangle { color: theme.bgPrimary; radius: theme.radiusMedium; border.color: theme.goldDark }
        contentItem: ColumnLayout {
            spacing: 10
            Txt { text: "تحويل بين الحسابات"; font.bold: true; font.pixelSize: theme.fontSection; color: theme.gold; Layout.fillWidth: true }
            Txt { Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.textMuted; font.pixelSize: theme.fontCaption
                  text: "تحويل مبلغ من حساب إلى آخر (الخزنة الرئيسية أو بنك أو محفظة). ليس إيرادًا ولا مصروفًا، ويظهر في حركة الحسابين." }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                Txt { text: "من حساب"; Layout.preferredWidth: 80 }
                Combo { id: trFrom; objectName: "cashTransferFrom"; Layout.fillWidth: true; implicitHeight: 38
                        model: transferDialog.accounts; textRole: "name"; valueRole: "key" }
            }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                Txt { text: "إلى حساب"; Layout.preferredWidth: 80 }
                Combo { id: trTo; objectName: "cashTransferTo"; Layout.fillWidth: true; implicitHeight: 38
                        model: transferDialog.accounts; textRole: "name"; valueRole: "key" }
            }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                Txt { text: "المبلغ"; Layout.preferredWidth: 80 }
                Field { id: trAmount; objectName: "cashTransferAmount"; Layout.preferredWidth: 200; implicitHeight: 38; font.bold: true
                        placeholderText: "0"; inputMethodHints: Qt.ImhDigitsOnly
                        validator: RegularExpressionValidator { regularExpression: /^[0-9]{0,12}$/ } }
                Txt { text: "ج"; color: theme.textMuted }
                Item { Layout.fillWidth: true }
            }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                Txt { text: "ملاحظة"; Layout.preferredWidth: 80 }
                Field { id: trNote; objectName: "cashTransferNote"; Layout.fillWidth: true; placeholderText: "اختياري" }
            }
            Txt { objectName: "transferSame"; Layout.fillWidth: true; color: theme.amber; text: "اختر حسابين مختلفين."
                  visible: trFrom.currentIndex === trTo.currentIndex }
            Txt { objectName: "transferError"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.red
                  text: page.vm ? page.vm.errorMessage : ""; visible: text.length > 0 }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                Btn { objectName: "transferSave"; primary: true
                      text: page.vm && page.vm.transferBusy ? "جارٍ التحويل…" : "تحويل"
                      enabled: !!page.vm && !page.vm.transferBusy && trFrom.currentIndex !== trTo.currentIndex && Number(trAmount.text) > 0
                      onClicked: page.vm.saveTransfer(String(trFrom.currentValue || ""), String(trTo.currentValue || ""), trAmount.text, trNote.text) }
                Btn { objectName: "transferCancel"; text: "إلغاء"; enabled: !!page.vm && !page.vm.transferBusy; onClicked: transferDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ------------------------------------------------------------------ نقدية داخلة وخارجة: a customer pays / deposits (IN) or takes money (OUT)
    // The money goes through ANY cash / bank / wallet account and the movement lands in the customer's statement. Two tables:
    // the customers who owe the shop (right) and the customers the shop owes (left); the search narrows both.
    Dialog {
        id: customerCashDialog; objectName: "customerCashDialog"
        property string mode: ""                      // "" (lists only) | IN | OUT
        function begin() { mode = ""; searchBox.text = ""; page.vm.loadCustomerCash(""); open() }
        function startEntry(direction, customerId) {
            mode = direction; ccAmount.text = ""; ccNote.text = ""; ccAccount.currentIndex = 0; page.vm.prepareCustomerCash()
            if (customerId) ccCustomer.setCustomerId(customerId); else ccCustomer.clear()
            ccAmount.forceActiveFocus()
        }
        anchors.centerIn: parent; width: page.width - 24; height: page.height - 24
        modal: true; font.family: "Cairo"
        closePolicy: page.vm && page.vm.customerCashBusy ? Popup.NoAutoClose : Popup.CloseOnEscape
        background: Rectangle { color: theme.bgPrimary; radius: theme.radiusMedium; border.color: theme.goldDark }
        contentItem: ColumnLayout {
            spacing: 10
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                Txt { text: "حركة نقدية عملاء"; font.bold: true; font.pixelSize: theme.fontSection; color: theme.gold }
                Field { id: searchBox; objectName: "customerCashSearch"; Layout.fillWidth: true; placeholderText: "ابحث باسم العميل أو التليفون"
                        onTextEdited: page.vm.loadCustomerCash(text) }
                Btn { objectName: "customerCashIn"; text: "نقدية داخلة"; chosen: customerCashDialog.mode === "IN"
                      onClicked: customerCashDialog.startEntry("IN", "") }
                Btn { objectName: "customerCashOut"; text: "نقدية خارجة"; chosen: customerCashDialog.mode === "OUT"
                      onClicked: customerCashDialog.startEntry("OUT", "") }
                Btn { objectName: "customerCashClose"; text: "إغلاق"; enabled: !!page.vm && !page.vm.customerCashBusy; onClicked: customerCashDialog.close() }
            }

            // ---- the entry form (a customer, an amount in whole pounds, the account the money goes through)
            Rectangle {
                visible: customerCashDialog.mode !== ""; Layout.fillWidth: true
                implicitHeight: entryCol.implicitHeight + 16
                color: theme.bgSecondary; radius: theme.radiusSmall; border.color: customerCashDialog.mode === "IN" ? theme.priceBuy : theme.amber
                ColumnLayout {
                    id: entryCol
                    anchors.fill: parent; anchors.margins: 8; spacing: 8
                    Txt { Layout.fillWidth: true; font.bold: true; color: customerCashDialog.mode === "IN" ? theme.priceBuy : theme.amber
                          text: customerCashDialog.mode === "IN"
                                ? "نقدية داخلة: العميل يسدّد أو يدفع عربونًا — تزيد الحساب المختار ويقل ما عليه"
                                : "نقدية خارجة: العميل يأخذ فلوسًا — تنقص الحساب المختار ويزيد ما عليه" }
                    RowLayout {
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                        Txt { text: "العميل"; Layout.preferredWidth: 70 }
                        CustomerPicker { id: ccCustomer; objectName: "customerCashCustomer"; Layout.fillWidth: true; clearable: true }
                    }
                    RowLayout {
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                        Txt { text: "المبلغ"; Layout.preferredWidth: 70 }
                        Field { id: ccAmount; objectName: "customerCashAmount"; Layout.preferredWidth: 200; implicitHeight: 38; font.bold: true
                                placeholderText: "0"; inputMethodHints: Qt.ImhDigitsOnly
                                validator: RegularExpressionValidator { regularExpression: /^[0-9]{0,12}$/ } }      // whole pounds only
                        Txt { text: customerCashDialog.mode === "IN" ? "يدخل في" : "يخرج من"; Layout.preferredWidth: 70 }
                        Combo { id: ccAccount; objectName: "customerCashAccount"; Layout.preferredWidth: 240; implicitHeight: 38
                                model: [{ "key": page.vm && page.vm.accountKey ? page.vm.accountKey : "", "name": page.vm && page.vm.accountName ? page.vm.accountName : "الخزنة" }].concat(page.vm ? page.vm.bankAccounts : [])
                                textRole: "name"; valueRole: "key"; currentIndex: 0 }
                        Txt { text: "ملاحظة"; Layout.preferredWidth: 60 }
                        Field { id: ccNote; objectName: "customerCashNote"; Layout.fillWidth: true; placeholderText: "اختياري" }
                    }
                    Txt { Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.red; text: page.vm ? page.vm.errorMessage : ""; visible: text.length > 0 }
                    RowLayout {
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                        Btn { objectName: "customerCashSave"; primary: true
                              text: page.vm && page.vm.customerCashBusy ? "جارٍ التسجيل…" : "تسجيل"
                              enabled: !!page.vm && !page.vm.customerCashBusy && ccCustomer.hasSelection && Number(ccAmount.text) > 0
                              onClicked: page.vm.saveCustomerCash(customerCashDialog.mode, ccCustomer.selectedCustomerId,
                                                                  String(ccAccount.currentValue || ""), ccAmount.text, ccNote.text) }
                        Btn { text: "إلغاء"; enabled: !!page.vm && !page.vm.customerCashBusy; onClicked: customerCashDialog.mode = "" }
                        Item { Layout.fillWidth: true }
                    }
                }
            }

            // ---- two tables side by side: عليهم فلوس (right) | لهم فلوس (left)
            RowLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; layoutDirection: Qt.RightToLeft; spacing: 10
                Repeater {
                    model: [{ "objectName": "customerCashOwes", "title": "عليهم فلوس (للمحل)", "tone": theme.red, "side": "owes" },
                            { "objectName": "customerCashOwed", "title": "لهم فلوس (على المحل)", "tone": theme.blue, "side": "owed" },
                            { "objectName": "customerCashSettled", "title": "رصيدهم صفر", "tone": theme.gold, "side": "settled" }]
                    delegate: Rectangle {
                        id: sideCard
                        required property var modelData
                        Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1
                        visible: sideCard.modelData.side !== "settled" || (searchBox.text.length > 0 && sideCard.rows.length > 0)      // a zero balance is listed only while searching
                        // Each side wears its own colour: a stronger frame plus a wash of the tone,
                        // so the IN (blue) and OUT (orange) tables never read as the same box.
                        color: Qt.tint(theme.bgSecondary, Qt.rgba(sideCard.modelData.tone.r,
                                                                 sideCard.modelData.tone.g,
                                                                 sideCard.modelData.tone.b, 0.07))
                        radius: theme.radiusSmall
                        border.color: sideCard.modelData.tone
                        border.width: 2
                        readonly property var rows: !page.vm ? [] : sideCard.modelData.side === "owes" ? page.vm.customerOwes : sideCard.modelData.side === "owed" ? page.vm.customerOwed : page.vm.customerSettled
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 8; spacing: 6
                            RowLayout {
                                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                                Txt { text: sideCard.modelData.title; font.bold: true; color: sideCard.modelData.tone; font.pixelSize: theme.fontSection }
                                Item { Layout.fillWidth: true }
                                Txt { objectName: sideCard.modelData.objectName + "Count"; text: sideCard.rows.length + " عميل"; color: theme.textMuted }
                            }
                            Txt { objectName: sideCard.modelData.objectName + "Empty"; visible: sideCard.rows.length === 0; Layout.fillWidth: true
                                  text: searchBox.text.length > 0 ? "لا يوجد عميل له رصيد (عليه أو له) بهذا الاسم أو الرقم هنا." : "لا يوجد."; color: theme.textMuted }
                            ListView {
                                id: sideList; objectName: sideCard.modelData.objectName
                                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 4
                                model: sideCard.rows
                                delegate: Rectangle {
                                    id: custRow
                                    required property var modelData
                                    required property int index
                                    width: sideList.width; height: 40; radius: 4; color: theme.bgPrimary
                                    RowLayout {
                                        anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8; layoutDirection: Qt.RightToLeft; spacing: 8
                                        Txt { objectName: sideCard.modelData.objectName + "Name_" + custRow.index; text: custRow.modelData.name
                                              font.bold: true; Layout.fillWidth: true }
                                        Txt { text: custRow.modelData.last_at || ""; color: theme.textMuted; Layout.preferredWidth: 120 }
                                        Txt { objectName: sideCard.modelData.objectName + "Amount_" + custRow.index
                                              text: Number(custRow.modelData.amount).toLocaleString(Qt.locale("en_US"), "f", 0) + " ج"
                                              font.bold: true; color: sideCard.modelData.tone; Layout.preferredWidth: 110 }
                                    }
                                    MouseArea { anchors.fill: parent
                                                onClicked: customerCashDialog.startEntry(sideCard.modelData.side === "owes" ? "IN" : "OUT", custRow.modelData.customer_id) }
                                }
                            }
                        }
                    }
                }
            }
        }
        // a successful save returns to the refreshed lists
        Connections {
            target: page.vm
            function onFinalized() { if (customerCashDialog.opened) { customerCashDialog.mode = ""; page.vm.loadCustomerCash(searchBox.text) } }
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
