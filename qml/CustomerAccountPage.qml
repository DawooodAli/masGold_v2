import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: page
    objectName: "customerAccountPage"
    property var vm: typeof customerAccountVM !== "undefined" ? customerAccountVM : null
    signal invoiceRequested(string invoiceId)
    signal correctionRequested(string settlementId)
    color: theme.bgPrimary
    Theme { id: theme }
    Connections {
        target: page.vm
        function onInvoiceRequested(invoiceId) { page.invoiceRequested(invoiceId) }
        function onCorrectionRequested(settlementId) { page.correctionRequested(settlementId) }
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
        font.family: "Cairo"
        contentItem: Txt { text: parent.text; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { color: theme.bgTertiary; radius: 5; border.color: theme.goldDark }
    }
    component Combo: ComboBox {
        font.family: "Cairo"
        contentItem: Txt { text: parent.displayText; verticalAlignment: Text.AlignVCenter; rightPadding: 25 }
        delegate: ItemDelegate { width: parent.width; text: modelData.text || modelData.name || ""
            font.family: "Cairo"; contentItem: Txt { text: parent.text } }
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
        }
        Txt { text: page.vm ? page.vm.message : ""; visible: text.length>0; color: theme.gold }
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
        RowLayout {
            Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
            Btn { text: "السابق"; enabled: page.vm && !page.vm.busy && page.vm.pageNumber>1; onClicked: page.vm.page(-1) }
            Txt { text: "صفحة "+(page.vm ? page.vm.pageNumber : 1) }
            Btn { text: "التالي"; enabled: page.vm && !page.vm.busy && page.vm.hasMore; onClicked: page.vm.page(1) }
            Btn { objectName: "customerAccountSettle"; text: "تسوية الحساب"; enabled: page.vm && !page.vm.busy && page.vm.canSettle
                onClicked: { paymentDialog.open(); if (!page.vm.legs.length) page.vm.addLeg() } }
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
