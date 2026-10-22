import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// مرتجع (Return V1). Opened from an invoice in سجل الفواتير. Every value is computed by
// ReturnService and formatted by returnVM; this page only forwards selections and typing.
// A finished Return is shown inside the invoice's History detail.
Item {
    signal customerRequested(string customerId)
    id: returnPage
    objectName: "returnWorkspace"

    readonly property var vm: typeof returnVM !== "undefined" ? returnVM : null
    readonly property var h: vm ? vm.header : ({})
    readonly property var s: vm ? vm.summary : ({})
    readonly property var st: vm ? vm.settlement : ({})
    readonly property bool editable: !!vm && !vm.done && !h.expired

    Theme { id: theme }

    // The returned-grams column fits 999.999 in the large weight font (measured, never a guessed width).
    TextMetrics { id: weightColumnText; font.family: theme.fontFamily; font.pixelSize: theme.fontSection + 2
                  font.bold: true; text: "999.999" }
    readonly property int weightColumn: Math.max(110, Math.ceil(weightColumnText.advanceWidth) + 36)

    Shortcut {
        sequence: "Esc"
        enabled: returnPage.visible && !!returnPage.vm && !returnPage.vm.saving
        onActivated: returnPage.vm.close()
    }

    Rectangle { anchors.fill: parent; color: theme.bgPrimary }

    // ------------------------------------------------------------ building blocks
    component Txt: Label {
        property string value: ""
        text: value
        font.family: theme.fontFamily
        font.pixelSize: theme.fontBody
        color: theme.textSecondary
        horizontalAlignment: Text.AlignRight
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideLeft
        wrapMode: Text.NoWrap
        textFormat: Text.PlainText
    }

    component Head: Txt { color: theme.textMuted; font.pixelSize: theme.fontTableHeader; font.bold: true }

    component Section: Rectangle {
        id: section
        property string title: ""
        property color accent: theme.gold
        default property alias content: sectionBody.data
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        Layout.preferredHeight: sectionBody.implicitHeight + 44
        color: theme.panel
        radius: theme.radiusCard
        border.color: theme.cardBorder
        Rectangle { anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                    anchors.topMargin: 16; anchors.bottomMargin: 16; anchors.rightMargin: 1; width: 3; radius: 2; color: section.accent }
        Txt { id: sectionTitle; anchors.top: parent.top; anchors.right: parent.right; anchors.topMargin: 8
              anchors.rightMargin: 14; value: section.title; color: section.accent; font.bold: true
              font.pixelSize: theme.fontSection }
        ColumnLayout {
            id: sectionBody
            anchors.top: sectionTitle.bottom; anchors.left: parent.left; anchors.right: parent.right
            anchors.topMargin: 6; anchors.leftMargin: 12; anchors.rightMargin: 14
            spacing: 4
        }
    }

    component Pair: RowLayout {
        property string label: ""
        property string value: ""
        property color valueColor: theme.textPrimary
        property bool strong: false
        Layout.fillWidth: true
        layoutDirection: Qt.RightToLeft
        spacing: 8
        Txt { value: parent.label; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
        Item { Layout.fillWidth: true }
        Txt { value: parent.value; color: parent.valueColor; font.bold: parent.strong
              font.pixelSize: parent.strong ? theme.fontBodyStrong : theme.fontBody }
    }

    component Btn: Button {
        id: btn
        property bool primary: false
        implicitHeight: 30
        contentItem: Label { text: btn.text; font.family: theme.fontFamily; font.pixelSize: theme.fontBody
                             font.bold: btn.primary; color: btn.enabled ? theme.textPrimary : theme.textDisabled
                             horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { implicitWidth: 72; radius: theme.radiusSmall
                                color: btn.primary && btn.enabled ? (btn.hovered ? theme.goldDark : theme.bgTertiary)
                                                                  : (btn.hovered ? theme.panelHover : theme.bgTertiary)
                                border.color: btn.primary && btn.enabled ? theme.gold
                                              : (btn.activeFocus ? theme.gold : theme.border) }
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

    // UX-RETURN-02: numbers keep their large bold figures; the FIELD grows to fit them (never the font shrinking).
    // Money fits 9,999,999.99; weight fits 999.999.
    component MoneyField: Field {
        id: moneyField
        font.pixelSize: theme.fontSection + 4
        font.bold: true
        implicitHeight: 40
        leftPadding: 12
        rightPadding: 12
        Layout.preferredWidth: Math.ceil(moneyWidest.advanceWidth) + leftPadding + rightPadding + 8
        TextMetrics { id: moneyWidest; font: moneyField.font; text: "9,999,999.99" }
    }
    component WeightField: Field {
        id: weightField
        font.pixelSize: theme.fontSection + 2
        font.bold: true
        implicitHeight: 36
        leftPadding: 10
        rightPadding: 10
        TextMetrics { id: weightWidest; font: weightField.font; text: "999.999" }
        readonly property int fitWidth: Math.ceil(weightWidest.advanceWidth) + leftPadding + rightPadding + 8
    }

    component Choice: ComboBox {
        implicitHeight: 30
        font.family: theme.fontFamily
        font.pixelSize: theme.fontBody
    }

    // ============================================================ TOP BAR
    RowLayout {
        id: topBar
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        anchors.margins: 12
        layoutDirection: Qt.RightToLeft
        spacing: 10
        Btn { objectName: "returnBack"; text: "رجوع  ⁦Esc⁩"; enabled: !!returnPage.vm && !returnPage.vm.saving
              onClicked: returnPage.vm.close() }
        Txt { objectName: "returnTitle"; color: theme.gold; font.bold: true; font.pixelSize: theme.fontPageTitle
              value: returnPage.h.number ? "مرتجع — فاتورة " + returnPage.h.number : "مرتجع" }
        CustomerNameLink { objectName: "returnCustomerName"; visible: !!returnPage.h.number
              text: returnPage.h.customerName ? "العميل: " + returnPage.h.customerName : ""
              customerId: returnPage.h.customerId || ""
              onCustomerRequested: function(customerId) { returnPage.customerRequested(customerId) } }
        Txt { visible: !!returnPage.h.returnCode; value: returnPage.h.returnCode || ""; color: theme.goldBright
              font.bold: true }
        Item { Layout.fillWidth: true }
        Txt { objectName: "returnDeadline"; visible: !!returnPage.h.number; value: returnPage.h.deadlineText || ""
              color: returnPage.h.expired ? theme.red : theme.textMuted; font.pixelSize: theme.fontTableHeader }
    }

    // Eligibility: the making window, the manager approval and the deadline — never inferred by the employee.
    Rectangle {
        id: stateStrip
        objectName: "returnEligibility"
        visible: !!returnPage.h.number
        anchors.top: topBar.bottom; anchors.left: parent.left; anchors.right: parent.right
        anchors.leftMargin: 12; anchors.rightMargin: 12; anchors.topMargin: 6
        height: visible ? 32 : 0
        radius: theme.radiusSmall
        color: theme.bgTertiary
        border.color: returnPage.h.expired ? theme.red : returnPage.h.windowOpen ? theme.green : theme.amber
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 12
            layoutDirection: Qt.RightToLeft; spacing: 16
            Txt { objectName: "returnWindowText"; font.bold: true
                  value: returnPage.h.expired ? returnPage.h.expiredText : (returnPage.h.windowText || "")
                  color: returnPage.h.expired ? theme.red : returnPage.h.windowOpen ? theme.green : theme.amber }
            Txt { objectName: "returnApprovalNeeded"; visible: !!returnPage.h.approvalRequired
                  value: "يحتاج موافقة المدير"; color: theme.amber; font.bold: true }
            Txt { objectName: "returnApprovalState"; visible: value.length > 0 && returnPage.s.approvalState !== "REQUIRED"
                  value: returnPage.vm ? returnPage.vm.approvalText : ""
                  color: returnPage.s.approvalState === "APPROVED" ? theme.green
                         : returnPage.s.approvalState === "REJECTED" ? theme.red : theme.amber }
            Item { Layout.fillWidth: true }
            Txt { value: returnPage.h.ageText || ""; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
        }
    }

    Txt { objectName: "returnState"; anchors.centerIn: parent
          visible: !!returnPage.vm && (returnPage.vm.busy || (returnPage.vm.errorMessage.length > 0 && !returnPage.h.number))
          value: !returnPage.vm ? "" : returnPage.vm.busy ? "جاري التحميل..." : returnPage.vm.errorMessage
          color: returnPage.vm && !returnPage.vm.busy ? theme.red : theme.textMuted
          font.pixelSize: theme.fontBodyStrong }

    // ============================================================ ENTRY
    ScrollView {
        id: editScroll
        objectName: "returnEditScroll"
        visible: !!returnPage.h.number
        anchors.top: stateStrip.bottom; anchors.left: parent.left; anchors.right: parent.right
        anchors.bottom: actionBar.top
        anchors.margins: 12
        clip: true
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            width: editScroll.availableWidth
            spacing: 8

            Section {
                objectName: "returnLinesSection"
                title: "ما يرده العميل من الفاتورة"
                accent: theme.accountPayable
                RowLayout {
                    Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                    Item { Layout.preferredWidth: 14 }
                    Head { value: "الصنف"; Layout.fillWidth: true }
                    Head { value: "عيار"; Layout.preferredWidth: 34 }
                    Head { value: "المباع"; Layout.preferredWidth: 70 }
                    Head { value: "مستخدم"; Layout.preferredWidth: 70 }
                    Head { value: "المتاح"; Layout.preferredWidth: 70 }
                    Head { value: "المرتجع"; Layout.preferredWidth: returnPage.weightColumn }
                    Head { value: "القيمة"; Layout.preferredWidth: 100 }
                    Head { value: "طريقة الحساب"; Layout.preferredWidth: 110 }
                }
                Repeater {
                    model: returnPage.vm ? returnPage.vm.lines : []
                    delegate: Rectangle {
                        id: lineRow
                        required property var modelData
                        required property int index
                        objectName: "returnLine_" + index
                        Layout.fillWidth: true
                        Layout.preferredHeight: lineCol.implicitHeight + 8
                        radius: 3
                        color: modelData.selected ? theme.panelHover : (index % 2 ? theme.bgSecondary : theme.bgTertiary)
                        border.color: modelData.selected ? theme.accountPayable : "transparent"
                        MouseArea {
                            anchors.fill: parent
                            enabled: lineRow.modelData.returnable && returnPage.editable
                            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                            onClicked: returnPage.vm.toggleLine(lineRow.modelData.lineId)
                        }
                        ColumnLayout {
                            id: lineCol
                            anchors.left: parent.left; anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 6; anchors.rightMargin: 6
                            spacing: 0
                            RowLayout {
                                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                                // Drawn check box (Cairo has no ☑ glyph).
                                Rectangle { Layout.preferredWidth: 14; Layout.preferredHeight: 14; radius: 3
                                            opacity: lineRow.modelData.returnable ? 1 : 0
                                            color: lineRow.modelData.selected ? theme.accountPayable : "transparent"
                                            border.color: lineRow.modelData.selected ? theme.accountPayable : theme.textMuted }
                                Txt { value: lineRow.modelData.item + (lineRow.modelData.barcode ? "  " + lineRow.modelData.barcode : "")
                                      color: lineRow.modelData.returnable ? theme.textPrimary : theme.textMuted
                                      font.bold: true; font.pixelSize: theme.fontBodyStrong; Layout.fillWidth: true }
                                Txt { value: lineRow.modelData.karat; Layout.preferredWidth: 34 }
                                Txt { value: lineRow.modelData.sold; Layout.preferredWidth: 70 }
                                Txt { value: lineRow.modelData.consumed; color: theme.textMuted; Layout.preferredWidth: 70 }
                                Txt { value: lineRow.modelData.remaining; color: theme.textPrimary; font.bold: true
                                      Layout.preferredWidth: 70 }
                                // F7 grams: typed (partial allowed); an identified piece is always whole.
                                Item {
                                    Layout.preferredWidth: returnPage.weightColumn; Layout.preferredHeight: 36
                                    WeightField {
                                        objectName: "returnWeight_" + lineRow.index
                                        anchors.fill: parent
                                        visible: lineRow.modelData.partial && lineRow.modelData.selected
                                        enabled: returnPage.editable
                                        text: lineRow.modelData.weight
                                        validator: RegularExpressionValidator { regularExpression: /^[0-9]{0,6}([.][0-9]{0,3})?$/ }
                                        onTextEdited: returnPage.vm.setWeight(lineRow.modelData.lineId, text)
                                    }
                                    Txt { anchors.fill: parent; visible: !(lineRow.modelData.partial && lineRow.modelData.selected)
                                          value: lineRow.modelData.selected ? lineRow.modelData.weight : ""
                                          color: theme.accountPayable; font.bold: true }
                                }
                                Txt { value: lineRow.modelData.valueText; color: theme.priceBuy; Layout.preferredWidth: 100 }
                                Txt { value: lineRow.modelData.makingText; color: theme.textMuted
                                      font.pixelSize: theme.fontCaption; Layout.preferredWidth: 110 }
                            }
                            // Physical identity: stored barcode (or «بدون باركود»), #item, item type.
                            Txt { objectName: "returnIdentity_" + lineRow.index; Layout.fillWidth: true
                                  visible: value.length > 0; value: lineRow.modelData.identity
                                  color: theme.goldBright; font.pixelSize: theme.fontBody }
                            // A replacement received in an Exchange: the piece the customer holds now.
                            Txt { objectName: "returnCurrentPiece_" + lineRow.index; Layout.fillWidth: true
                                  visible: value.length > 0; value: lineRow.modelData.exchangeText
                                  color: theme.priceBuy; font.bold: true; font.pixelSize: theme.fontBody }
                            Txt { Layout.fillWidth: true; visible: value.length > 0; value: lineRow.modelData.reason
                                  color: theme.amber; font.pixelSize: theme.fontCaption; elide: Text.ElideRight }
                        }
                    }
                }
            }

            Section {
                objectName: "returnCalculationSection"
                visible: !!returnPage.s.total
                title: returnPage.s.modeText || "حساب المرتجع"
                accent: theme.priceBuy
                Repeater {
                    model: returnPage.s.lines || []
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 10
                        Txt { value: modelData.item + (modelData.identity ? "  —  " + modelData.identity : "")
                              color: theme.textPrimary; Layout.fillWidth: true; elide: Text.ElideLeft }
                        Txt { value: "عيار " + modelData.karat; Layout.preferredWidth: 60 }
                        Txt { value: modelData.weight + " جرام"; Layout.preferredWidth: 90 }
                        Txt { visible: !returnPage.s.historical; value: "× " + modelData.price; color: theme.priceBuy
                              Layout.preferredWidth: 110 }
                        Txt { value: modelData.value; color: theme.textPrimary; Layout.preferredWidth: 110 }
                    }
                }
                Pair { objectName: "returnBasis"; label: "قيمة الاسترداد قبل التقريب"; value: returnPage.s.basis || "" }
                Pair { objectName: "returnRounding"; label: "التقريب (أقرب 5 جنيه)"; value: returnPage.s.rounding || "" }
                Pair { objectName: "returnTotal"; label: "إجمالي المرتجع"; value: returnPage.s.total || ""; strong: true
                       valueColor: theme.accountPayable }
                Txt { Layout.fillWidth: true; visible: value.length > 0; value: returnPage.s.priceWarning || ""
                      color: theme.statusWarning; font.pixelSize: theme.fontCaption }
            }

            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                Txt { value: "ملاحظات (اختياري)"; color: theme.textMuted }
                Field { objectName: "returnNotes"; Layout.fillWidth: true; enabled: returnPage.editable
                        onTextEdited: returnPage.vm.setNotes(text) }
            }

            Repeater {
                model: returnPage.vm ? returnPage.vm.problems : []
                delegate: Txt { required property var modelData
                                Layout.fillWidth: true; value: "• " + modelData; color: theme.red }
            }
            Item { Layout.preferredHeight: 4 }
        }
    }

    // ============================================================ SETTLEMENT + ACTIONS (fixed, always on screen)
    Rectangle {
        id: actionBar
        objectName: "returnSettlementBar"
        visible: !!returnPage.h.number
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.margins: 12
        height: visible ? barCol.implicitHeight + 14 : 0
        radius: theme.radiusSmall
        color: theme.panel
        border.color: theme.accountPayable

        ColumnLayout {
            id: barCol
            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: 10; anchors.rightMargin: 12
            spacing: 6

            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 12
                Txt { value: "إجمالي المستحق للعميل"; font.bold: true; font.pixelSize: theme.fontSection
                      color: theme.accountPayable }
                Txt { objectName: "returnDue"; value: returnPage.st.due || (returnPage.vm && returnPage.vm.previewBusy
                                                                      ? "جاري الحساب..." : "اختر ما يرده العميل")
                      font.bold: true; font.pixelSize: theme.fontNumber; color: theme.accountPayable }
                Item { Layout.fillWidth: true }
                Txt { objectName: "returnRemaining"; visible: value.length > 0; value: returnPage.st.remainingText || ""
                      color: returnPage.st.complete ? theme.green : theme.amber; font.bold: true }
            }

            // Real money paid to the customer: one or more rows (Billing-style), + an explicit account part.
            Repeater {
                model: returnPage.vm ? returnPage.vm.legs : []
                delegate: RowLayout {
                    id: legRow
                    required property var modelData
                    required property int index
                    objectName: "returnLeg_" + index
                    Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 6
                    Txt { value: legRow.index === 0 ? "المدفوع للعميل" : ""; Layout.preferredWidth: 96
                          color: theme.textSecondary; font.bold: true }
                    Choice {
                        objectName: "returnLegMethod_" + legRow.index
                        Layout.preferredWidth: 120
                        enabled: returnPage.editable
                        model: returnPage.vm ? returnPage.vm.methods : []
                        textRole: "text"; valueRole: "code"
                        currentIndex: returnPage.vm ? returnPage.vm.methods.findIndex(function(m) {
                                                          return m.code === legRow.modelData.method }) : 0
                        onActivated: returnPage.vm.setLegMethod(legRow.modelData.key, currentValue)
                    }
                    Choice {
                        objectName: "returnLegAccount_" + legRow.index
                        Layout.preferredWidth: 160
                        visible: legRow.modelData.needsAccount
                        enabled: returnPage.editable
                        model: [{ id: "", name: "اختر الحساب" }].concat(legRow.modelData.accounts)
                        textRole: "name"; valueRole: "id"
                        currentIndex: Math.max(0, model.findIndex(function(a) { return a.id === legRow.modelData.bank }))
                        onActivated: returnPage.vm.setLegAccount(legRow.modelData.key, currentValue)
                    }
                    MoneyField { objectName: "returnLegAmount_" + legRow.index
                            enabled: returnPage.editable
                            text: legRow.modelData.amount
                            placeholderText: activeFocus || text.length ? "" : "المبلغ"
                            validator: RegularExpressionValidator { regularExpression: /^[0-9]{0,9}([.][0-9]{0,2})?$/ }
                            onTextEdited: returnPage.vm.setLegAmount(legRow.modelData.key, text) }
                    Btn { objectName: "returnLegRemove_" + legRow.index; text: "حذف"
                          visible: returnPage.vm && returnPage.vm.legs.length > 1; enabled: returnPage.editable
                          onClicked: returnPage.vm.removeLeg(legRow.modelData.key) }
                    Item { Layout.fillWidth: true }
                }
            }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 6
                Btn { objectName: "returnAddLeg"; text: "إضافة طريقة دفع"; enabled: returnPage.editable
                      onClicked: returnPage.vm.addLeg() }
                Btn { objectName: "returnPayRemaining"; text: "المتبقي"; enabled: returnPage.editable
                      onClicked: returnPage.vm.payRemaining() }
                Txt { visible: !!returnPage.h.hasCustomer; value: "للعميل على حسابه"; color: theme.textMuted }
                MoneyField { objectName: "returnAccountAmount"
                        visible: !!returnPage.h.hasCustomer
                        enabled: returnPage.editable
                        text: returnPage.vm ? returnPage.vm.accountAmount : ""
                        placeholderText: activeFocus || text.length ? "" : "0.00"
                        validator: RegularExpressionValidator { regularExpression: /^[0-9]{0,9}([.][0-9]{0,2})?$/ }
                        onTextEdited: returnPage.vm.setAccountAmount(text) }
                Item { Layout.fillWidth: true }
            }

            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                Btn { objectName: "returnSave"; primary: true
                      text: returnPage.vm && returnPage.vm.saving ? "جاري الحفظ..." : "تنفيذ المرتجع"
                      enabled: !!returnPage.vm && returnPage.vm.canFinalize
                      onClicked: returnPage.vm.finalize() }
                Btn { objectName: "returnRequestApproval"; text: "طلب موافقة المدير"
                      visible: !!returnPage.h.approvalRequired && !(returnPage.vm && returnPage.vm.done)
                      enabled: !!returnPage.vm && returnPage.vm.canRequestApproval
                      onClicked: returnPage.vm.requestApproval() }
                Btn { objectName: "returnRefresh"; text: "تحديث"
                      visible: returnPage.s.approvalState === "PENDING"
                      onClicked: returnPage.vm.refresh() }
                // Only before finalize (a finalized Return is never canceled here).
                Btn { objectName: "returnCancel"; text: "إلغاء المرتجع"
                      visible: !!returnPage.vm && returnPage.vm.canCancel
                      onClicked: returnPage.vm.cancelReturn() }
                CheckBox { objectName: "returnPriceAck"; visible: !!returnPage.s.needsAck
                           text: "أقر بسعر الذهب الحالي"; font.family: theme.fontFamily
                           onToggled: returnPage.vm.setPriceAcknowledged(checked) }
                Item { Layout.fillWidth: true }
                Txt { objectName: "returnMessage"; Layout.maximumWidth: returnPage.width * 0.55
                      visible: value.length > 0
                      value: !returnPage.vm ? "" : returnPage.vm.done ? returnPage.vm.doneMessage + "  (رجوع لعرضه في الفاتورة)"
                             : (returnPage.vm.errorMessage || returnPage.vm.infoMessage
                                || (returnPage.vm.problems.length ? returnPage.vm.problems[0] : ""))
                      color: returnPage.vm && (returnPage.vm.done || (!returnPage.vm.errorMessage && returnPage.vm.infoMessage))
                             ? theme.green : theme.red }
            }
        }
    }
}
