import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// تصحيح / عكس (Reversal / Correction V1) — managers only. Opened from an invoice in سجل الفواتير.
// The manager chooses the transaction and ONE mode explicitly; everything shown is computed by
// CorrectionService and formatted by correctionVM. A reason is mandatory and a confirmation
// always precedes execution. The original transaction is never edited or deleted.
Item {
    signal customerRequested(string customerId)
    id: correctionPage
    objectName: "correctionWorkspace"

    readonly property var vm: typeof correctionVM !== "undefined" ? correctionVM : null
    readonly property var h: vm ? vm.header : ({})
    readonly property var rv: vm ? vm.reversal : ({})
    readonly property var st: vm ? vm.settlement : ({})
    readonly property bool editable: !!vm && !vm.done

    Theme { id: theme }

    Shortcut {
        sequence: "Esc"
        enabled: correctionPage.visible && !!correctionPage.vm && !correctionPage.vm.saving
        onActivated: correctionPage.vm.confirming ? correctionPage.vm.cancelConfirm() : correctionPage.vm.close()
    }

    Rectangle { anchors.fill: parent; color: theme.bgPrimary }

    component Txt: Label {
        property string value: ""
        text: value
        font.family: theme.fontFamily
        font.pixelSize: theme.fontBody
        color: theme.textSecondary
        horizontalAlignment: Text.AlignRight
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideLeft
        textFormat: Text.PlainText
    }

    component Section: Rectangle {
        id: section
        property string title: ""
        property color accent: theme.gold
        default property alias content: body.data
        Layout.fillWidth: true
        Layout.preferredHeight: body.implicitHeight + 44
        color: theme.panel
        radius: theme.radiusCard
        border.color: theme.cardBorder
        Rectangle { anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                    anchors.topMargin: 16; anchors.bottomMargin: 16; anchors.rightMargin: 1; width: 3; radius: 2; color: section.accent }
        Txt { id: sectionTitle; anchors.top: parent.top; anchors.right: parent.right; anchors.topMargin: 8
              anchors.rightMargin: 14; value: section.title; color: section.accent; font.bold: true
              font.pixelSize: theme.fontSection }
        ColumnLayout {
            id: body
            anchors.top: sectionTitle.bottom; anchors.left: parent.left; anchors.right: parent.right
            anchors.topMargin: 6; anchors.leftMargin: 12; anchors.rightMargin: 14
            spacing: 4
        }
    }

    component Btn: Button {
        id: btn
        property bool primary: false
        property bool selected: false
        property color accent: theme.gold
        implicitHeight: 30
        contentItem: Label { text: btn.text; font.family: theme.fontFamily; font.pixelSize: theme.fontBody
                             font.bold: btn.primary || btn.selected
                             color: !btn.enabled ? theme.textDisabled : btn.selected ? btn.accent : theme.textPrimary
                             horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { implicitWidth: 72; radius: theme.radiusSmall
                                color: btn.hovered ? theme.panelHover : theme.bgTertiary
                                border.color: btn.selected || (btn.primary && btn.enabled) ? btn.accent : theme.border }
    }

    component Field: TextField {
        implicitHeight: 30
        font.family: theme.fontFamily
        font.pixelSize: theme.fontBody
        color: theme.textPrimary
        placeholderTextColor: theme.textDisabled
        horizontalAlignment: TextInput.AlignRight
        selectByMouse: true
        background: Rectangle { radius: 4; color: theme.bgPrimary
                                border.color: parent.activeFocus ? theme.gold : theme.border }
    }

    component Lines: Repeater {
        id: linesRoot
        property color tone: theme.textSecondary
        delegate: Txt { required property var modelData
                        Layout.fillWidth: true; value: "• " + modelData; color: linesRoot.tone
                        wrapMode: Text.WordWrap; elide: Text.ElideNone }
    }

    // ============================================================ TOP BAR
    RowLayout {
        id: topBar
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        anchors.margins: 12
        layoutDirection: Qt.RightToLeft
        spacing: 10
        Btn { objectName: "correctionBack"; text: "رجوع  ⁦Esc⁩"; enabled: !!correctionPage.vm && !correctionPage.vm.saving
              onClicked: correctionPage.vm.close() }
        Txt { objectName: "correctionTitle"; color: theme.red; font.bold: true; font.pixelSize: theme.fontPageTitle
              Layout.fillWidth: true
              value: correctionPage.h.title || "تصحيح / عكس" }
        CustomerNameLink { objectName: "correctionCustomerName"; visible: !!correctionPage.h.number
              text: correctionPage.h.customer ? "العميل: " + correctionPage.h.customer : ""
              customerId: correctionPage.h.customerId || ""
              onCustomerRequested: function(customerId) { correctionPage.customerRequested(customerId) } }
        Item { Layout.fillWidth: true }
    }

    // Which transaction: the invoice itself, or one of its Exchanges / Returns.
    Flow {
        id: targetRow
        objectName: "correctionTargets"
        anchors.top: topBar.bottom; anchors.left: parent.left; anchors.right: parent.right
        anchors.leftMargin: 12; anchors.rightMargin: 12; anchors.topMargin: 4
        layoutDirection: Qt.RightToLeft
        spacing: 6
        Repeater {
            model: correctionPage.vm ? correctionPage.vm.targets : []
            delegate: Btn { required property var modelData
                            objectName: "correctionTarget_" + modelData.index
                            text: modelData.text + (modelData.state ? "  (" + modelData.state + ")" : "")
                            selected: modelData.selected; enabled: correctionPage.editable
                            onClicked: correctionPage.vm.selectTarget(modelData.index) }
        }
    }

    // The two modes — an explicit choice (never a reversal by accident when only the account is wrong).
    RowLayout {
        id: modeRow
        anchors.top: targetRow.bottom; anchors.left: parent.left; anchors.right: parent.right
        anchors.leftMargin: 12; anchors.rightMargin: 12; anchors.topMargin: 8
        layoutDirection: Qt.RightToLeft
        spacing: 8
        Btn { objectName: "correctionModeReversal"; text: "عكس العملية بالكامل"; accent: theme.red
              selected: !!correctionPage.vm && correctionPage.vm.mode === "REVERSAL"; enabled: correctionPage.editable
              onClicked: correctionPage.vm.setMode("REVERSAL") }
        Btn { objectName: "correctionModeSettlement"; text: "تصحيح طريقة التسوية"; accent: theme.amber
              visible: !correctionPage.vm || correctionPage.vm.settlementAllowed
              selected: !!correctionPage.vm && correctionPage.vm.mode === "SETTLEMENT"; enabled: correctionPage.editable
              onClicked: correctionPage.vm.setMode("SETTLEMENT") }
        Item { Layout.fillWidth: true }
    }

    ScrollView {
        id: scroll
        objectName: "correctionScroll"
        anchors.top: modeRow.bottom; anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: actionBar.top
        anchors.margins: 12
        clip: true
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            width: scroll.availableWidth
            spacing: 8

            Txt { visible: !!correctionPage.vm && !correctionPage.vm.mode; Layout.fillWidth: true
                  value: "اختر العملية ثم نوع التصحيح."; color: theme.textMuted }

            // -------------------------------------------- FULL REVERSAL preview
            Section {
                objectName: "correctionReversalPreview"
                visible: !!correctionPage.vm && correctionPage.vm.mode === "REVERSAL" && !!correctionPage.rv.title
                title: "معاينة العكس — " + (correctionPage.rv.title || "")
                accent: theme.red
                CustomerNameLink { objectName: "correctionPreviewCustomerName"; Layout.fillWidth: true
                    text: "العميل: " + (correctionPage.rv.customer || "")
                    customerId: correctionPage.rv.customerId || ""
                    onCustomerRequested: function(customerId) { correctionPage.customerRequested(customerId) } }
                Txt { Layout.fillWidth: true; value: "التاريخ: " + (correctionPage.rv.date || "")
                      + "  •  إجمالي الفاتورة الأصلي: " + (correctionPage.rv.total || "")
                      color: theme.textPrimary }
                Txt { value: "الحركة المالية العكسية"; color: theme.textMuted; font.bold: true }
                Lines { model: correctionPage.rv.money || []; tone: theme.textPrimary }
                Lines { model: correctionPage.rv.account || []; tone: theme.textPrimary }
                Txt { visible: (correctionPage.rv.stock || []).length > 0; value: "المخزون"; color: theme.textMuted; font.bold: true }
                Lines { model: correctionPage.rv.stock || [] }
                Txt { visible: (correctionPage.rv.gold || []).length > 0; value: "رصيد الكسر"; color: theme.textMuted; font.bold: true }
                Lines { model: correctionPage.rv.gold || [] }
                Lines { model: correctionPage.rv.extra || [] }
                // Why it cannot be reversed (newest dependent transaction first).
                Repeater {
                    model: correctionPage.rv.blockers || []
                    delegate: Txt { required property var modelData
                                    objectName: "correctionBlocker"
                                    Layout.fillWidth: true; value: modelData; color: theme.red; font.bold: true
                                    wrapMode: Text.WordWrap; elide: Text.ElideNone }
                }
            }

            // -------------------------------------------- SETTLEMENT CORRECTION
            Section {
                objectName: "correctionSettlement"
                visible: !!correctionPage.vm && correctionPage.vm.mode === "SETTLEMENT" && !!correctionPage.st.total
                title: "تصحيح طريقة التسوية — " + (correctionPage.st.directionText || "")
                accent: theme.amber
                Txt { Layout.fillWidth: true; value: "الإجمالي (لا يتغير): " + (correctionPage.st.total || ""); color: theme.textPrimary
                      font.bold: true }
                Txt { Layout.fillWidth: true; value: "المسجل حالياً: " + (correctionPage.st.current || "")
                      wrapMode: Text.WordWrap; elide: Text.ElideNone }
                Txt { value: "التسوية الصحيحة"; color: theme.textMuted; font.bold: true }
                Repeater {
                    model: correctionPage.vm ? correctionPage.vm.legs : []
                    delegate: RowLayout {
                        id: legRow
                        required property var modelData
                        required property int index
                        objectName: "correctionLeg_" + index
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 6
                        ComboBox {
                            objectName: "correctionLegMethod_" + legRow.index
                            Layout.preferredWidth: 120; implicitHeight: 30; font.family: theme.fontFamily
                            enabled: correctionPage.editable
                            model: correctionPage.vm ? correctionPage.vm.methods : []
                            textRole: "text"; valueRole: "code"
                            currentIndex: correctionPage.vm ? correctionPage.vm.methods.findIndex(function(m) {
                                                                  return m.code === legRow.modelData.method }) : 0
                            onActivated: correctionPage.vm.setLegMethod(legRow.modelData.key, currentValue)
                        }
                        ComboBox {
                            objectName: "correctionLegAccount_" + legRow.index
                            Layout.preferredWidth: 160; implicitHeight: 30; font.family: theme.fontFamily
                            visible: legRow.modelData.needsAccount; enabled: correctionPage.editable
                            model: [{ id: "", name: "اختر الحساب" }].concat(legRow.modelData.accounts)
                            textRole: "name"; valueRole: "id"
                            currentIndex: Math.max(0, model.findIndex(function(a) { return a.id === legRow.modelData.bank }))
                            onActivated: correctionPage.vm.setLegAccount(legRow.modelData.key, currentValue)
                        }
                        Field { objectName: "correctionLegAmount_" + legRow.index; Layout.preferredWidth: 160
                                leftPadding: 8; rightPadding: 8          // room for 120,000.00 without a smaller font
                                enabled: correctionPage.editable; text: legRow.modelData.amount
                                validator: RegularExpressionValidator { regularExpression: /^[0-9]{0,9}([.][0-9]{0,2})?$/ }
                                onTextEdited: correctionPage.vm.setLegAmount(legRow.modelData.key, text) }
                        Btn { objectName: "correctionLegRemove_" + legRow.index; text: "حذف"; enabled: correctionPage.editable
                              visible: correctionPage.vm && correctionPage.vm.legs.length > 1
                              onClicked: correctionPage.vm.removeLeg(legRow.modelData.key) }
                        Item { Layout.fillWidth: true }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 6
                    Btn { objectName: "correctionAddLeg"; text: "إضافة طريقة دفع"; enabled: correctionPage.editable
                          onClicked: correctionPage.vm.addLeg() }
                    Txt { visible: !!correctionPage.st.hasCustomer; value: "حساب العميل"; color: theme.textMuted }
                    Field { objectName: "correctionAccountAmount"; Layout.preferredWidth: 150; visible: !!correctionPage.st.hasCustomer
                            leftPadding: 8; rightPadding: 8
                            enabled: correctionPage.editable; text: correctionPage.vm ? correctionPage.vm.accountAmount : ""
                            placeholderText: activeFocus || text.length ? "" : "0.00"
                            validator: RegularExpressionValidator { regularExpression: /^[0-9]{0,9}([.][0-9]{0,2})?$/ }
                            onTextEdited: correctionPage.vm.setAccountAmount(text) }
                    Item { Layout.fillWidth: true }
                    Txt { objectName: "correctionRemaining"; value: correctionPage.st.remainingText || ""; font.bold: true
                          color: correctionPage.st.complete ? theme.green : theme.amber }
                }
                Txt { visible: (correctionPage.st.deltas || []).length > 0; value: "الحركات التي ستُسجل"; color: theme.textMuted
                      font.bold: true }
                Lines { model: correctionPage.st.deltas || []; tone: theme.textPrimary }
                Lines { model: correctionPage.st.problems || []; tone: theme.red }
            }
        }
    }

    // ============================================================ REASON + EXECUTE (always on screen)
    Rectangle {
        id: actionBar
        objectName: "correctionActionBar"
        visible: !!correctionPage.h.number
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.margins: 12
        height: visible ? barCol.implicitHeight + 14 : 0
        radius: theme.radiusSmall
        color: theme.panel
        border.color: theme.red
        ColumnLayout {
            id: barCol
            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: 10; anchors.rightMargin: 12
            spacing: 6
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                Txt { value: "سبب التصحيح (إلزامي)"; color: theme.red; font.bold: true }
                Field { objectName: "correctionReason"; Layout.fillWidth: true; enabled: correctionPage.editable
                        text: correctionPage.vm ? correctionPage.vm.reason : ""
                        placeholderText: activeFocus || text.length ? "" : "مثال: تم تسجيل الفاتورة بالخطأ"
                        onTextEdited: correctionPage.vm.setReason(text) }
            }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                Btn { objectName: "correctionExecute"; primary: true; accent: theme.red
                      text: correctionPage.vm && correctionPage.vm.saving ? "جاري التنفيذ..."
                            : correctionPage.vm && correctionPage.vm.mode === "SETTLEMENT" ? "تنفيذ تصحيح التسوية" : "تنفيذ العكس"
                      enabled: !!correctionPage.vm && correctionPage.vm.canExecute
                      onClicked: correctionPage.vm.requestConfirm() }
                Item { Layout.fillWidth: true }
                Txt { objectName: "correctionMessage"; Layout.maximumWidth: correctionPage.width * 0.7; visible: value.length > 0
                      value: !correctionPage.vm ? "" : correctionPage.vm.done ? correctionPage.vm.doneMessage
                             : correctionPage.vm.errorMessage || correctionPage.vm.notice
                               || (correctionPage.vm.mode && correctionPage.vm.reasonMissing ? "اكتب سبب التصحيح" : "")
                      color: correctionPage.vm && (correctionPage.vm.done || (!correctionPage.vm.errorMessage && correctionPage.vm.notice))
                             ? theme.green : theme.red }
            }
        }
    }

    // Strong confirmation: exactly what will happen; nothing is deleted.
    Popup {
        id: confirm
        objectName: "correctionConfirm"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(560, correctionPage.width - 32)
        modal: true
        visible: !!correctionPage.vm && correctionPage.vm.confirming
        closePolicy: Popup.CloseOnEscape
        onClosed: if (correctionPage.vm && correctionPage.vm.confirming) correctionPage.vm.cancelConfirm()
        padding: 16
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.red }
        contentItem: ColumnLayout {
            spacing: 10
            Label { Layout.fillWidth: true; text: "تأكيد التصحيح"; color: theme.red; font.family: theme.fontFamily
                    font.pixelSize: 15; font.bold: true; horizontalAlignment: Text.AlignRight }
            Label { objectName: "correctionConfirmText"; Layout.fillWidth: true
                    text: correctionPage.vm ? correctionPage.vm.confirmText : ""
                    color: theme.textPrimary; font.family: theme.fontFamily; font.pixelSize: 13
                    horizontalAlignment: Text.AlignRight; wrapMode: Text.WordWrap }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                Btn { objectName: "correctionConfirmYes"; text: "تنفيذ"; primary: true; accent: theme.red
                      onClicked: correctionPage.vm.execute() }
                Btn { objectName: "correctionConfirmNo"; text: "إلغاء"; onClicked: correctionPage.vm.cancelConfirm() }
                Item { Layout.fillWidth: true }
            }
        }
    }
}
