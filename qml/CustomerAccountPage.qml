import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: page
    objectName: "customerAccountPage"
    property var vm: typeof customerAccountVM !== "undefined" ? customerAccountVM : null
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
        function onFinalized(customerId) { depositDialog.close() }
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
        font.family: "Cairo"
        contentItem: Txt { text: accountButton.text; verticalAlignment: Text.AlignVCenter
            font.bold: accountButton.checked; color: accountButton.checked ? theme.gold : theme.textPrimary }
        background: Rectangle { color: theme.bgTertiary; radius: 5; border.color: accountButton.checked ? theme.gold : theme.goldDark }
    }
    component Combo: ComboBox {
        id: accountCombo
        font.family: "Cairo"
        contentItem: Txt { text: accountCombo.displayText; verticalAlignment: Text.AlignVCenter; rightPadding: 25 }
        delegate: ItemDelegate { id: accountChoice; width: accountCombo.width; text: modelData.text || modelData.name || ""
            font.family: "Cairo"; contentItem: Txt { text: accountChoice.text } }
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 12; spacing: 7
        RowLayout {
            Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
            Txt { text: page.vm ? page.vm.header : ""; font.bold: true; font.pixelSize: 18 }
            Btn { text: "تحديث"; enabled: page.vm && !page.vm.busy; onClicked: page.vm.refresh() }
            Btn { text: "رجوع للعملاء"; enabled: page.vm && !page.vm.busy; onClicked: page.vm.close() }
        }
        RowLayout {
            Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
            Txt { objectName: "accountBalance"; text: page.vm ? page.vm.balance : ""; color: theme.gold; font.pixelSize: 20 }
            Txt { text: "آخر حركة: " + (page.vm ? page.vm.lastMovement : "") }
            Btn { objectName: "customerAccountDeadCash"; text: "⛔ تحويل إلى رصيد مشطوب"
                visible: page.deadCashAvailable
                enabled: page.vm && !page.vm.busy && typeof customerVM !== "undefined" && !customerVM.busy
                onClicked: page.deadCashRequested(page.vm.customerId) }
        }
        Rectangle {
            objectName: "customerCustodyCard"
            visible: !!page.vm && page.vm.custody.length > 0
            Layout.fillWidth: true; implicitHeight: custodyColumn.implicitHeight + 14
            color: theme.bgSecondary; radius: 6; border.color: theme.cardBorder
            ColumnLayout {
                id: custodyColumn; anchors.left: parent.left; anchors.right: parent.right; anchors.margins: 8; y: 7; spacing: 3
                Txt { objectName: "customerCustodyTotal"; font.bold: true; color: theme.amber
                      text: page.vm ? "عهدة مؤقتة مفتوحة (خارج الرصيد): " + page.vm.custodyTotal : "" }
                Repeater {
                    model: page.vm ? page.vm.custody : []
                    delegate: Txt { required property var modelData; color: theme.textMuted; font.pixelSize: 12
                        text: modelData.code + " • " + modelData.date + " • المبلغ " + modelData.amount + " • المسدد " + modelData.repaid + " • المتبقي " + modelData.owed
                              + (modelData.note.length > 0 ? " • " + modelData.note : "") }
                }
            }
        }
        Txt { text: page.vm ? page.vm.message : ""; visible: text.length>0; color: theme.gold }
        RowLayout {
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
            ScrollBar.vertical: ScrollBar {}
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
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                required property var modelData
                width: invoiceList.width; height: invoiceFacts.implicitHeight+16
                color: theme.bgSecondary; radius: 6; border.color: theme.border
                ColumnLayout {
                    id: invoiceFacts; anchors.left: parent.left; anchors.right: parent.right
                    anchors.margins: 8; y: 8
                    RowLayout {
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                        Btn { objectName: "customerInvoice_"+modelData.invoiceId
                            text: "فاتورة "+modelData.number
                            onClicked: page.vm.openInvoice(modelData.invoiceId) }
                        Txt { text: modelData.date }
                        Txt { text: modelData.kind }
                    }
                    RowLayout {
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                        Txt { text: "إجمالي الفاتورة: "+modelData.total; color: theme.gold }
                        Txt { text: modelData.status }
                    }
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
            Btn { objectName: "customerAccountSettle"; text: "تسوية الحساب"; enabled: page.vm && !page.vm.busy && page.vm.canSettle
                onClicked: { paymentDialog.open(); if (!page.vm.legs.length) page.vm.addLeg() } }
            Btn { objectName: "customerDeposit"; text: "عربون"; visible: page.vm && page.vm.canDeposit
                enabled: page.vm && !page.vm.busy; onClicked: depositDialog.begin("IN") }
            Btn { objectName: "customerDepositRefund"; text: "استرداد عربون"; visible: page.vm && page.vm.canDeposit
                enabled: page.vm && !page.vm.busy; onClicked: depositDialog.begin("OUT") }
        }
    }
    // عربون / استرداد عربون — cash only (الخزنة), amount and an optional note; confirmation before posting.
    Dialog {
        id: depositDialog; objectName: "customerDepositDialog"
        property string direction: "IN"
        property bool confirming: false
        function begin(d) { direction = d; confirming = false; depositAmount.text = ""; depositNote.text = ""
            if (d === "OUT") page.vm.beginRefund(); else page.vm.beginDeposit(); open() }
        anchors.centerIn: parent; width: Math.min(page.width-24,460)
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
    Dialog {
        id: paymentDialog; objectName: "customerAccountPaymentDialog"
        anchors.centerIn: parent; width: Math.min(page.width-24,760); height: Math.min(page.height-24,500)
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
