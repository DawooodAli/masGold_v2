import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: page
    objectName: "customerAccountPage"
    property var vm: typeof customerAccountVM !== "undefined" ? customerAccountVM : null
    // embedded: the page sits inside the customer's file (the host draws the tabs, the balance and the write-off button).
    property bool embedded: false
    signal invoiceRequested(string invoiceId)
    signal correctionRequested(string settlementId)
    signal depositCorrectionRequested(string depositId)
    signal deadCashRequested(string customerId)
    signal deadCashReversalRequested(string customerId, string deadCashId, string reference, string amountText)
    // The write-off action follows the existing Customers eligibility (permission + receivable) and is only
    // offered while that state belongs to THIS account's customer; the host re-synchronises before acting.
    readonly property bool deadCashAvailable: !!page.vm && page.vm.customerId.length > 0
        && typeof customerVM !== "undefined" && customerVM.selectedCustomerId === page.vm.customerId
        && customerVM.canWriteOff
    color: theme.bgPrimary
    Theme { id: theme }
    property string appliedFilters: ""
    // The customers page puts the «إضافة رصيد قديم» button under the customer's name and opens this page's dialog from there.
    function openLegacy() { if (page.vm && page.vm.canLegacy && !page.vm.busy) legacyDialog.begin() }
    function syncFilters() {
        if (!page.vm) return
        var f=page.vm.filters
        var key=page.vm.customerId+":"+page.vm.activeTab+JSON.stringify(f)
        if (key===appliedFilters) return
        appliedFilters=key
        search.text=f.search; start.text=f.start; end.text=f.end
        source.currentIndex=source.indexOfValue(f.source)
    }
    Component.onCompleted: Qt.callLater(syncFilters)
    Connections {
        target: page.vm
        function onChanged() { Qt.callLater(page.syncFilters) }
        function onInvoiceRequested(invoiceId) { page.invoiceRequested(invoiceId) }
        function onCorrectionRequested(settlementId) { page.correctionRequested(settlementId) }
        function onDepositCorrectionRequested(depositId) { page.depositCorrectionRequested(depositId) }
        function onFinalized(customerId) { depositDialog.close(); legacyDialog.close() }
    }
    component Txt: Label {
        color: theme.textPrimary; font.family: "Cairo"; font.pixelSize: 13
        horizontalAlignment: Text.AlignRight; LayoutMirroring.enabled: false
        wrapMode: Text.Wrap; Layout.fillWidth: true
    }
    component Field: TextField {
        font.family: "Cairo"; horizontalAlignment: TextInput.AlignRight
        color: theme.textPrimary; selectByMouse: true
        background: Rectangle { color: theme.bgSecondary; radius: 5; border.color: theme.border }
    }
    component Btn: Button {
        id: accountButton
        property bool goldOutline: false
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        implicitHeight: 32
        hoverEnabled: true
        padding: 12
        contentItem: Label {
            text: accountButton.text
            font.family: accountButton.font.family
            font.pixelSize: accountButton.font.pixelSize
            font.bold: accountButton.checked || accountButton.goldOutline
            color: !accountButton.enabled ? theme.textDisabled
                   : accountButton.checked || accountButton.goldOutline ? (accountButton.hovered ? theme.goldBright : theme.gold)
                   : accountButton.hovered ? theme.textPrimary : theme.textSecondary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            Behavior on color { ColorAnimation { duration: 120 } }
        }
        background: Rectangle {
            radius: theme.radiusSmall
            color: !accountButton.enabled ? theme.bgSecondary
                   : accountButton.checked ? (accountButton.down ? theme.goldDark : accountButton.hovered ? "#3A2F1A" : "#2A2214")
                   : accountButton.down ? theme.bgPrimary : accountButton.hovered ? theme.panelHover : theme.bgTertiary
            border.width: 1
            border.color: !accountButton.enabled ? theme.border
                          : accountButton.checked || accountButton.goldOutline ? (accountButton.down ? theme.goldBright : theme.gold)
                          : (accountButton.hovered || accountButton.activeFocus) ? theme.goldDark : theme.border
            Behavior on color { ColorAnimation { duration: 120 } }
            Behavior on border.color { ColorAnimation { duration: 120 } }
        }
    }
    component Combo: ComboBox {
        id: accountCombo
        font.family: "Cairo"
        contentItem: Txt { text: accountCombo.displayText; verticalAlignment: Text.AlignVCenter; rightPadding: 25 }
        delegate: ItemDelegate { id: accountChoice; width: accountCombo.width; text: modelData.text || modelData.name || ""
            font.family: "Cairo"; contentItem: Txt { text: accountChoice.text } }
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: page.embedded ? 0 : 12; spacing: 7
        RowLayout {
            visible: !page.embedded
            Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
            Txt { text: page.vm ? page.vm.header : ""; font.bold: true; font.pixelSize: 18 }
            Btn { text: "تحديث"; enabled: page.vm && !page.vm.busy; onClicked: page.vm.refresh() }
            Item { Layout.fillWidth: true }
            Btn { goldOutline: true; text: "رجوع للعملاء"; enabled: page.vm && !page.vm.busy; onClicked: page.vm.close() }
        }
        RowLayout {
            visible: !page.embedded
            Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
            Txt { objectName: "accountBalance"; text: page.vm ? page.vm.balance : ""; color: theme.gold; font.pixelSize: 20 }
            Txt { text: "آخر حركة: " + (page.vm ? page.vm.lastMovement : "") }
        }
        Txt { text: page.vm ? page.vm.message : ""; visible: text.length>0; color: theme.gold }
        RowLayout {
            Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
            Btn { objectName: "customerAccountSettle"; text: "تسوية الحساب"; enabled: page.vm && !page.vm.busy && page.vm.canSettle
                onClicked: { paymentDialog.open(); if (!page.vm.legs.length) page.vm.addLeg() } }
            Btn { objectName: "customerDeposit"; text: "عربون"; visible: page.vm && page.vm.canDeposit
                enabled: page.vm && !page.vm.busy; onClicked: depositDialog.begin("IN") }
            Btn { objectName: "customerDepositRefund"; text: "استرداد عربون"; visible: page.vm && page.vm.canDeposit
                enabled: page.vm && !page.vm.busy; onClicked: depositDialog.begin("OUT") }
            Btn { objectName: "customerLegacyBalance"; text: "إضافة رصيد قديم"; visible: page.vm && page.vm.canLegacy && !page.embedded
                enabled: page.vm && !page.vm.busy; onClicked: legacyDialog.begin() }
            Btn { objectName: "customerAccountDeadCash"; text: "⛔ تحويل إلى رصيد مشطوب"
                visible: page.deadCashAvailable
                enabled: page.vm && !page.vm.busy && typeof customerVM !== "undefined" && !customerVM.busy
                onClicked: page.deadCashRequested(page.vm.customerId) }
            Item { Layout.fillWidth: true }
        }
        RowLayout {
            visible: !page.embedded
            Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
            Btn { objectName: "customerAccountStatementTab"; text: "كشف الحساب"; checkable: true
                checked: page.vm && page.vm.activeTab===0; enabled: page.vm && !page.vm.busy
                onClicked: page.vm.setTab(0) }
            Btn { objectName: "customerInvoicesTab"; text: "فواتير العميل"; checkable: true
                checked: page.vm && page.vm.activeTab===1; enabled: page.vm && !page.vm.busy
                onClicked: page.vm.setTab(1) }
            Item { Layout.fillWidth: true }
        }
        RowLayout {
            Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
            Field { id: search; Layout.fillWidth: true; placeholderText: "بحث بالمرجع / الفاتورة" }
            Combo { id: source; Layout.preferredWidth: 155; model: page.vm ? page.vm.sources : []; textRole: "text"; valueRole: "code" }
            Field { id: start; Layout.preferredWidth: 110; placeholderText: "من YYYY-MM-DD" }
            Field { id: end; Layout.preferredWidth: 110; placeholderText: "إلى YYYY-MM-DD" }
            Btn { text: "عرض"; enabled: page.vm && !page.vm.busy
                onClicked: page.vm.filter(start.text,end.text,source.currentValue || "",search.text) }
        }
        ListView {
            id: statement
            objectName: "customerAccountStatement"
            visible: page.vm && page.vm.activeTab===0
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 110
            clip: true; spacing: 5; model: page.vm ? page.vm.rows : []
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOn }
            delegate: Rectangle {
                required property var modelData
                width: statement.width; height: facts.implicitHeight+16; radius: 6
                color: theme.bgSecondary; border.color: theme.border
                ColumnLayout {
                    id: facts; anchors.left: parent.left; anchors.right: parent.right; anchors.margins: 8; y: 8; spacing: 2
                    RowLayout {
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                        Txt { text: modelData.source+" "+modelData.reference+" "+modelData.correctedSource; font.bold: true }
                        Btn { objectName: "accountInvoice_"+modelData.key
                            visible: modelData.invoiceId.length>0
                            text: "فاتورة "+modelData.invoiceNumber
                            onClicked: page.vm.openInvoice(modelData.invoiceId) }
                        Btn { text: "تصحيح / عكس "+modelData.settlementId
                            visible: page.vm && page.vm.canCorrect && modelData.settlementId.length>0
                            onClicked: page.vm.openCorrection(modelData.settlementId) }
                        Btn { objectName: "accountDepositReverse_"+modelData.key; text: "عكس "+modelData.reference
                            visible: page.vm && page.vm.canCorrect && modelData.depositId.length>0
                            onClicked: page.vm.openDepositCorrection(modelData.depositId) }
                        // عكس الشطب sits on the write-off movement itself (hidden once reversed / without the right).
                        Btn { objectName: "accountDeadCashReverse_"+modelData.key; text: "عكس الشطب"
                            visible: modelData.deadCashId.length>0 && !modelData.deadCashReversed
                                     && typeof customerVM !== "undefined" && customerVM.canReverseDeadCash
                            enabled: !!page.vm && !page.vm.busy
                            onClicked: page.deadCashReversalRequested(page.vm.customerId, modelData.deadCashId,
                                                                      modelData.reference, modelData.amountValue) }
                    }
                    RowLayout {
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                        Txt { text: modelData.amount; color: theme.gold }
                        Txt { text: "الرصيد بعد الحركة — "+modelData.balance }
                        Txt { text: modelData.date+" · "+modelData.actor }
                    }
                    Txt { text: modelData.description; visible: text.length>0 }
                    Txt { text: modelData.payments; visible: text.length>0; color: theme.gold }
                    Txt { text: modelData.notes; visible: text.length>0 }
                }
            }
        }
        ListView {
            id: invoiceList; objectName: "customerInvoicesList"
            visible: page.vm && page.vm.activeTab===1
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 110
            clip: true; spacing: 5; model: page.vm ? page.vm.invoiceRows : []
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOn }
            delegate: Rectangle {
                required property var modelData
                width: invoiceList.width; height: 40
                color: theme.bgSecondary; radius: 6; border.color: theme.border
                // one compact line per invoice: number, date, kind, total, status
                RowLayout {
                    id: invoiceFacts; anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8
                    layoutDirection: Qt.RightToLeft; spacing: 10
                    Btn { objectName: "customerInvoice_"+modelData.invoiceId; implicitHeight: 28
                        text: "فاتورة "+modelData.number
                        onClicked: page.vm.openInvoice(modelData.invoiceId) }
                    Txt { text: modelData.date; Layout.fillWidth: false; Layout.preferredWidth: 150; wrapMode: Text.NoWrap }
                    Txt { text: modelData.kind; Layout.fillWidth: false; Layout.preferredWidth: 70; wrapMode: Text.NoWrap }
                    Txt { text: "الإجمالي: "+modelData.total; color: theme.gold; Layout.fillWidth: false; Layout.preferredWidth: 170; wrapMode: Text.NoWrap }
                    Txt { text: modelData.status; wrapMode: Text.NoWrap }
                }
            }
            Txt { anchors.centerIn: parent; visible: invoiceList.count===0 && page.vm && !page.vm.busy
                text: "لا توجد فواتير مطابقة للفلاتر." }
        }
        RowLayout {
            Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
            Btn { text: "السابق"; enabled: page.vm && !page.vm.busy && page.vm.pageNumber>1; onClicked: page.vm.page(-1) }
            Txt { text: "صفحة "+(page.vm ? page.vm.pageNumber : 1) }
            Btn { text: "التالي"; enabled: page.vm && !page.vm.busy && page.vm.hasMore; onClicked: page.vm.page(1) }
        }
    }
    // عربون / استرداد عربون — cash only (الخزنة), amount and an optional note; confirmation before posting.
    Dialog {
        id: depositDialog; objectName: "customerDepositDialog"
        property string direction: "IN"
        property bool confirming: false
        function begin(d) { direction = d; confirming = false; depositAmount.text = ""; depositNote.text = ""
            if (d === "OUT") page.vm.beginRefund(); else page.vm.beginDeposit(); open() }
        parent: Overlay.overlay; anchors.centerIn: parent; width: Math.min(Overlay.overlay.width-24,460)   // centred on the window, never on the (taller) embedded page
        modal: true; title: direction === "IN" ? "عربون" : "استرداد عربون"; font.family: "Cairo"
        closePolicy: page.vm && page.vm.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        background: Rectangle { color: theme.bgPrimary; radius: 8; border.color: theme.goldDark }
        contentItem: ColumnLayout {
            Txt { objectName: "customerRefundBalance"
                text: page.vm ? (depositDialog.direction === "OUT" ? page.vm.refundBalance : page.vm.balance) : ""
                color: theme.gold; font.bold: true }
            Txt { objectName: "customerRefundAvailability"; visible: depositDialog.direction === "OUT"
                text: page.vm ? page.vm.refundAvailability : ""; color: theme.amber }
            Field { id: depositAmount; objectName: "customerDepositAmount"; Layout.fillWidth: true
                placeholderText: "المبلغ"; enabled: !depositDialog.confirming && page.vm && !page.vm.busy
                    && (depositDialog.direction === "IN" || page.vm.refundHasCapacity)
                onTextChanged: if (page.vm && depositDialog.direction === "OUT") page.vm.setRefundAmount(text) }
            Txt { objectName: "customerRefundAmountMessage"; visible: depositDialog.direction === "OUT" && text.length>0
                text: page.vm ? page.vm.refundAmountMessage : ""; color: theme.red }
            Field { id: depositNote; objectName: "customerDepositNote"; Layout.fillWidth: true
                placeholderText: "ملاحظة اختيارية"; enabled: !depositDialog.confirming }
            Txt { objectName: "customerDepositConfirmText"; visible: depositDialog.confirming; color: theme.amber
                text: (depositDialog.direction === "IN" ? "سيتم استلام عربون " : "سيتم رد ") + depositAmount.text
                      + " ج نقداً " + (depositDialog.direction === "IN" ? "من العميل." : "للعميل من العربون.") }
            Txt { text: page.vm ? page.vm.message : ""; visible: text.length>0; color: theme.red }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                Btn { objectName: "customerDepositNext"; text: "متابعة"; visible: !depositDialog.confirming
                    enabled: page.vm && !page.vm.busy && (depositDialog.direction === "OUT"
                        ? page.vm.canRefund : depositAmount.text.trim().length>0)
                    onClicked: depositDialog.confirming = true }
                Btn { objectName: "customerDepositConfirm"; text: "تأكيد"; visible: depositDialog.confirming
                    enabled: page.vm && !page.vm.busy && (depositDialog.direction === "IN" || page.vm.canRefund)
                    onClicked: page.vm.depositAction(depositDialog.direction, depositAmount.text, depositNote.text) }
                Btn { text: "تعديل"; visible: depositDialog.confirming; enabled: page.vm && !page.vm.busy
                    onClicked: depositDialog.confirming = false }
                Item { Layout.fillWidth: true }
                Btn { text: "إغلاق"; enabled: page.vm && !page.vm.busy; onClicked: depositDialog.close() }
            }
        }
    }
    // رصيد قديم من دفتر ورقي: an OLD balance put on the customer's account WITHOUT any cash / bank / wallet movement; confirmation before posting.
    Dialog {
        id: legacyDialog; objectName: "customerLegacyDialog"
        property bool confirming: false
        function begin() { confirming = false; legacyAmount.text = ""; legacyDate.text = ""; legacyNote.text = ""; legacyDirection.currentIndex = 0; page.vm.beginLegacy(); open() }
        parent: Overlay.overlay; anchors.centerIn: parent; width: Math.min(Overlay.overlay.width-24,460)
        modal: true; title: "إضافة رصيد قديم"; font.family: "Cairo"
        closePolicy: page.vm && page.vm.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        background: Rectangle { color: theme.bgPrimary; radius: 8; border.color: theme.goldDark }
        contentItem: ColumnLayout {
            Txt { text: "رصيد قديم من الدفاتر الورقية يُسجَّل على حساب العميل فقط، ولا يؤثر على الخزنة أو البنوك أو المحافظ."; color: theme.textSecondary }
            Txt { objectName: "customerLegacyCurrent"; text: page.vm ? "الرصيد الحالي: " + page.vm.balance : ""; color: theme.gold; font.bold: true }
            ComboBox { id: legacyDirection; objectName: "customerLegacyDirection"; Layout.fillWidth: true; font.family: "Cairo"; enabled: !legacyDialog.confirming
                model: [{ "code": "OWES", "text": "على العميل (مديونية)" }, { "code": "CREDIT", "text": "للعميل (له عندنا)" }]; textRole: "text"; valueRole: "code" }
            Field { id: legacyAmount; objectName: "customerLegacyAmount"; Layout.fillWidth: true; placeholderText: "المبلغ"; enabled: !legacyDialog.confirming && page.vm && !page.vm.busy }
            Field { id: legacyDate; objectName: "customerLegacyDate"; Layout.fillWidth: true; placeholderText: "تاريخ الدين القديم YYYY-MM-DD (اختياري)"; enabled: !legacyDialog.confirming }
            Field { id: legacyNote; objectName: "customerLegacyNote"; Layout.fillWidth: true; placeholderText: "البيان (إجباري) مثلاً: دفتر ورقي 2023"; enabled: !legacyDialog.confirming }
            Txt { objectName: "customerLegacyConfirmText"; visible: legacyDialog.confirming; color: theme.amber
                text: "سيتم تسجيل " + legacyAmount.text + " ج " + (legacyDirection.currentValue === "OWES" ? "على العميل" : "للعميل") + " كرصيد قديم، بدون أي حركة نقدية." }
            Txt { objectName: "customerLegacyError"; text: page.vm ? page.vm.message : ""; visible: text.length > 0; color: theme.red }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                Btn { objectName: "customerLegacyNext"; text: "متابعة"; visible: !legacyDialog.confirming
                    enabled: page.vm && !page.vm.busy && legacyAmount.text.trim().length > 0 && legacyNote.text.trim().length > 0
                    onClicked: legacyDialog.confirming = true }
                Btn { objectName: "customerLegacyConfirm"; text: "تأكيد"; visible: legacyDialog.confirming; enabled: page.vm && !page.vm.busy
                    onClicked: page.vm.legacyAction(legacyDirection.currentValue, legacyAmount.text, legacyDate.text, legacyNote.text) }
                Btn { text: "تعديل"; visible: legacyDialog.confirming; enabled: page.vm && !page.vm.busy; onClicked: legacyDialog.confirming = false }
                Item { Layout.fillWidth: true }
                Btn { text: "إغلاق"; enabled: page.vm && !page.vm.busy; onClicked: legacyDialog.close() }
            }
        }
    }
    Dialog {
        id: paymentDialog; objectName: "customerAccountPaymentDialog"
        parent: Overlay.overlay; anchors.centerIn: parent; width: Math.min(Overlay.overlay.width-24,760); height: Math.min(Overlay.overlay.height-24,500)
        modal: true; title: "تسوية الحساب"; font.family: "Cairo"
        closePolicy: page.vm && page.vm.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        background: Rectangle { color: theme.bgPrimary; radius: 8; border.color: theme.goldDark }
        contentItem: ColumnLayout {
            Txt { text: page.vm ? page.vm.balance : ""; color: theme.gold; font.bold: true }
            Txt { text: page.vm ? page.vm.message : ""; visible: text.length>0 }
            ListView {
                id: legs; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 8
                model: page.vm ? page.vm.legs : []; ScrollBar.vertical: ScrollBar {}
                delegate: RowLayout {
                    id: leg; required property var modelData; width: legs.width; height: 46
                    layoutDirection: Qt.RightToLeft
                    function save() { page.vm.editLeg(modelData.key,method.currentValue || "PHYSICAL_CASH",bank.currentValue || "",amount.text) }
                    Combo { id: method; Layout.preferredWidth: 130; model: page.vm ? page.vm.methods : []; textRole: "text"; valueRole: "code"
                        Component.onCompleted: currentIndex = indexOfValue(leg.modelData.method)
                        onActivated: { bank.currentIndex=0; leg.save() } }
                    Combo { id: bank; Layout.fillWidth: true; model: [{id:"",name:"اختر الحساب"}].concat(page.vm ? page.vm.accounts : [])
                        textRole: "name"; valueRole: "id"; enabled: method.currentValue!=="PHYSICAL_CASH"
                        Component.onCompleted: currentIndex = indexOfValue(leg.modelData.bank)
                        onActivated: leg.save() }
                    Field { id: amount; Layout.preferredWidth: 115; placeholderText: "المبلغ"; text: leg.modelData.amount
                        onTextEdited: leg.save() }
                    Btn { text: "حذف"; onClicked: page.vm.removeLeg(leg.modelData.key) }
                }
            }
            Field { id: note; Layout.fillWidth: true; placeholderText: "ملاحظة اختيارية" }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                Btn { text: "إضافة طريقة دفع"; enabled: page.vm && !page.vm.busy; onClicked: page.vm.addLeg() }
                Item { Layout.fillWidth: true }
                Btn { text: "إغلاق"; enabled: page.vm && !page.vm.busy; onClicked: paymentDialog.close() }
                Btn { objectName: "customerAccountFinalize"; text: "تأكيد التسوية"; enabled: page.vm && !page.vm.busy && page.vm.canSettle && page.vm.legs.length>0
                    onClicked: page.vm.finalize(note.text) }
            }
        }
    }
}
