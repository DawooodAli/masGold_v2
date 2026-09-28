import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// استبدال (Exchange V1). Opened from an invoice in سجل الفواتير. Every value is computed by
// ExchangeService and formatted by exchangeVM; this page only forwards selections and typing.
// mode "edit": a new exchange; mode "view": one finished exchange, read-only.
Item {
    id: exchangePage
    objectName: "exchangeWorkspace"

    readonly property var vm: typeof exchangeVM !== "undefined" ? exchangeVM : null
    readonly property bool viewMode: !!vm && vm.mode === "view"
    readonly property var h: vm ? vm.header : ({})
    readonly property var s: vm ? vm.summary : ({})
    readonly property var d: vm ? vm.detail : ({})
    readonly property bool wide: width >= 1000

    Theme { id: theme }

    Shortcut {
        sequence: "Esc"
        enabled: exchangePage.visible && !!exchangePage.vm && !exchangePage.vm.saving
        onActivated: exchangePage.vm.close()
    }

    Rectangle { anchors.fill: parent; color: theme.bgPrimary }

    function directionColor(direction) {
        return direction === "CUSTOMER_OWES" ? theme.accountReceivable
             : direction === "SHOP_OWES" ? theme.accountPayable : theme.textSecondary
    }

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

    component Head: Txt { color: theme.textMuted; font.pixelSize: theme.fontCaption; font.bold: true }

    component Section: Rectangle {
        id: section
        property string title: ""
        property color accent: theme.gold
        default property alias content: sectionBody.data
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        Layout.preferredHeight: sectionBody.implicitHeight + 42
        color: theme.panel
        radius: theme.radiusSmall
        border.color: theme.border
        Rectangle { anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                    anchors.margins: 1; width: 3; radius: 2; color: section.accent }
        Txt { id: sectionTitle; anchors.top: parent.top; anchors.right: parent.right; anchors.topMargin: 7
              anchors.rightMargin: 14; value: section.title; color: section.accent; font.bold: true
              font.pixelSize: theme.fontSection }
        ColumnLayout {
            id: sectionBody
            anchors.top: sectionTitle.bottom; anchors.left: parent.left; anchors.right: parent.right
            anchors.topMargin: 4; anchors.leftMargin: 12; anchors.rightMargin: 14
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
        background: Rectangle { implicitWidth: 76; radius: theme.radiusSmall
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
        spacing: 8
        Btn { objectName: "exchangeBack"; text: "رجوع  ⁦Esc⁩"; enabled: !!exchangePage.vm && !exchangePage.vm.saving
              onClicked: exchangePage.vm.close() }
        Txt { objectName: "exchangeTitle"; color: theme.gold; font.bold: true; font.pixelSize: theme.fontPageTitle
              value: exchangePage.viewMode ? (exchangePage.d.code ? "استبدال " + exchangePage.d.code : "")
                                           : (exchangePage.h.number ? "استبدال — فاتورة " + exchangePage.h.number : "استبدال") }
        Txt { visible: !exchangePage.viewMode && !!exchangePage.h.number; color: theme.textMuted
              value: exchangePage.h.customerName ? "العميل: " + exchangePage.h.customerName : "" }
        Item { Layout.fillWidth: true }
        Txt { visible: !exchangePage.viewMode && !!exchangePage.h.windowText; value: exchangePage.h.windowText || ""
              color: theme.textMuted; font.pixelSize: theme.fontCaption }
    }

    Txt { objectName: "exchangeState"; anchors.centerIn: parent
          visible: !!exchangePage.vm && (exchangePage.vm.busy
                   || (exchangePage.vm.errorMessage.length > 0 && !exchangePage.h.number && !exchangePage.d.code))
          value: !exchangePage.vm ? "" : exchangePage.vm.busy ? "جاري التحميل..." : exchangePage.vm.errorMessage
          color: exchangePage.vm && !exchangePage.vm.busy ? theme.red : theme.textMuted
          font.pixelSize: theme.fontBodyStrong }

    // ============================================================ EDIT (new exchange)
    ScrollView {
        id: editScroll
        objectName: "exchangeEditScroll"
        visible: !exchangePage.viewMode && !!exchangePage.h.number
        anchors.top: topBar.bottom; anchors.left: parent.left; anchors.right: parent.right
        anchors.bottom: actionBar.top
        anchors.margins: 12
        clip: true
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            width: editScroll.availableWidth
            spacing: 8

            GridLayout {
                Layout.fillWidth: true
                columns: exchangePage.wide ? 2 : 1
                layoutDirection: Qt.RightToLeft
                columnSpacing: 8; rowSpacing: 8

                // -------------------------------------------- returned (from the original invoice)
                Section {
                    objectName: "exchangeReturnedSection"
                    title: "القطع المرتجعة من الفاتورة"
                    accent: theme.accountPayable
                    Repeater {
                        model: exchangePage.vm ? exchangePage.vm.lines : []
                        delegate: Rectangle {
                            id: lineRow
                            required property var modelData
                            required property int index
                            objectName: "exchangeLine_" + index
                            Layout.fillWidth: true
                            Layout.preferredHeight: lineCol.implicitHeight + 10
                            radius: 4
                            color: modelData.selected ? theme.bgTertiary : "transparent"
                            border.color: modelData.selected ? theme.accountPayable : theme.border
                            opacity: modelData.exchangeable ? 1 : 0.75
                            ColumnLayout {
                                id: lineCol
                                anchors.left: parent.left; anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 8; anchors.rightMargin: 8
                                spacing: 0
                                RowLayout {
                                    Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                                    // Drawn check box (Cairo has no ☑ glyph).
                                    Rectangle { Layout.preferredWidth: 14; Layout.preferredHeight: 14; radius: 3
                                                visible: lineRow.modelData.exchangeable
                                                color: lineRow.modelData.selected ? theme.accountPayable : "transparent"
                                                border.color: lineRow.modelData.selected ? theme.accountPayable : theme.textMuted }
                                    Item { Layout.preferredWidth: 14; visible: !lineRow.modelData.exchangeable }
                                    Txt { value: lineRow.modelData.item; color: theme.textPrimary; Layout.fillWidth: true
                                          Layout.minimumWidth: 60 }
                                    Txt { value: "عيار " + lineRow.modelData.karat }
                                    Txt { value: lineRow.modelData.weight; color: theme.textPrimary }
                                    Txt { value: "صافي " + lineRow.modelData.pure; color: theme.textMuted
                                          font.pixelSize: theme.fontTableHeader }
                                }
                                Txt { Layout.fillWidth: true; visible: value.length > 0; value: lineRow.modelData.reason
                                      color: theme.amber; font.pixelSize: theme.fontCaption; elide: Text.ElideRight }
                            }
                            MouseArea {
                                anchors.fill: parent
                                enabled: lineRow.modelData.exchangeable && !!exchangePage.vm && !exchangePage.vm.done
                                cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: exchangePage.vm.toggleReturned(lineRow.modelData.lineId)
                            }
                        }
                    }
                    Txt { value: "القطعة المرتجعة تُسجل «بانتظار الفحص» ولا تعود للبيع مباشرة."
                          color: theme.textMuted; font.pixelSize: theme.fontCaption; Layout.fillWidth: true
                          elide: Text.ElideRight }
                }

                // -------------------------------------------- replacements (scanned stock pieces)
                Section {
                    objectName: "exchangeReplacementSection"
                    title: "القطع البديلة"
                    accent: theme.priceSell
                    RowLayout {
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 6
                        BarcodeField {
                            id: scanField
                            objectName: "exchangeScan"
                            Layout.fillWidth: true
                            Layout.maximumWidth: 260
                            implicitHeight: 30
                            enabled: !!exchangePage.vm && !exchangePage.vm.done
                            font.family: theme.fontFamily
                            font.pixelSize: theme.fontBody
                            color: theme.textPrimary
                            placeholderTextColor: theme.textDisabled
                            horizontalAlignment: TextInput.AlignRight
                            selectByMouse: true
                            placeholderText: activeFocus || text.length ? "" : "امسح باركود القطعة البديلة"
                            background: Rectangle { radius: 4; color: theme.bgPrimary
                                                    border.color: scanField.activeFocus ? theme.gold : theme.border }
                            onAccepted: { exchangePage.vm.addReplacementBarcode(text); text = "" }
                        }
                        Btn { objectName: "exchangeAddReplacement"; text: "إضافة"
                              enabled: !!exchangePage.vm && !exchangePage.vm.done && scanField.text.length > 0
                              onClicked: { exchangePage.vm.addReplacementBarcode(scanField.text); scanField.text = "" } }
                        Item { Layout.fillWidth: true }
                    }
                    Txt { objectName: "exchangeScanError"; Layout.fillWidth: true; visible: value.length > 0
                          value: exchangePage.vm ? exchangePage.vm.scanError : ""; color: theme.red }
                    Repeater {
                        model: exchangePage.vm ? exchangePage.vm.replacements : []
                        delegate: RowLayout {
                            id: repRow
                            required property var modelData
                            required property int index
                            objectName: "exchangeReplacement_" + index
                            Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                            Txt { value: repRow.modelData.item; color: theme.textPrimary; Layout.fillWidth: true
                                  Layout.minimumWidth: 60 }
                            Txt { value: "عيار " + repRow.modelData.karat }
                            Txt { value: repRow.modelData.weight; color: theme.textPrimary }
                            Txt { value: "صافي " + repRow.modelData.pure; color: theme.textMuted
                                  font.pixelSize: theme.fontTableHeader }
                            // Making is typed only when the customer receives more pure gold.
                            Field { objectName: "exchangeMaking_" + repRow.index
                                    Layout.preferredWidth: 92
                                    visible: !!exchangePage.s.makingRequired
                                    enabled: !!exchangePage.vm && !exchangePage.vm.done
                                    text: repRow.modelData.making
                                    placeholderText: activeFocus || text.length ? "" : "مصنعية ج/جم"
                                    validator: RegularExpressionValidator { regularExpression: /^[0-9]{0,6}([.][0-9]{0,2})?$/ }
                                    onTextEdited: exchangePage.vm.setMaking(repRow.modelData.itemId, text) }
                            Btn { text: "حذف"; enabled: !!exchangePage.vm && !exchangePage.vm.done
                                  onClicked: exchangePage.vm.removeReplacement(repRow.modelData.itemId) }
                        }
                    }
                    Txt { visible: !exchangePage.vm || exchangePage.vm.replacements.length === 0
                          value: "لم تُضف قطعة بديلة بعد"; color: theme.textMuted }
                }
            }

            // -------------------------------------------- the calculation (from the service)
            Section {
                objectName: "exchangeSummarySection"
                visible: !!exchangePage.s.finalAmount
                title: "حساب الاستبدال"
                accent: theme.gold
                GridLayout {
                    Layout.fillWidth: true
                    columns: exchangePage.wide ? 2 : 1
                    layoutDirection: Qt.RightToLeft
                    columnSpacing: 24; rowSpacing: 2
                    Pair { label: "الذهب الصافي المرتجع"; value: exchangePage.s.returnedPure || "" }
                    Pair { label: "الذهب الصافي البديل"; value: exchangePage.s.replacementPure || "" }
                    Pair { objectName: "exchangeDelta"; label: exchangePage.s.deltaLabel || ""; value: exchangePage.s.delta || ""
                           strong: true }
                    Pair { objectName: "exchangePrice"; label: "السعر المستخدم"; value: exchangePage.s.priceText || "" }
                    Pair { objectName: "exchangeGoldAmount"; label: "قيمة فرق الذهب"; value: exchangePage.s.goldAmount || "" }
                    Pair { objectName: "exchangeMaking"; visible: (exchangePage.s.makingLabel || "").length > 0
                           label: exchangePage.s.makingLabel || ""; value: exchangePage.s.makingValue || ""
                           valueColor: exchangePage.s.makingMissing ? theme.amber : theme.textPrimary }
                }
                Txt { objectName: "exchangeRefundExpired"; Layout.fillWidth: true; visible: value.length > 0
                      value: exchangePage.s.expiredText || ""; color: theme.textMuted }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: theme.border }
                RowLayout {
                    Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 10
                    Txt { value: "المبلغ النهائي"; color: theme.textMuted; font.pixelSize: theme.fontBodyStrong }
                    Txt { objectName: "exchangeDirection"; value: exchangePage.s.directionText || ""; font.bold: true
                          color: exchangePage.directionColor(exchangePage.s.direction) }
                    Item { Layout.fillWidth: true }
                    Txt { objectName: "exchangeFinalAmount"; value: exchangePage.s.finalAmount || ""; font.bold: true
                          font.pixelSize: theme.fontSection; color: exchangePage.directionColor(exchangePage.s.direction) }
                }
                Txt { objectName: "exchangePending"; Layout.fillWidth: true; value: exchangePage.s.pendingText || ""
                      color: theme.amber }
                Txt { objectName: "exchangeReview"; Layout.fillWidth: true; visible: value.length > 0
                      value: exchangePage.s.reviewText || ""; color: theme.amber; font.pixelSize: theme.fontCaption }
            }

            // -------------------------------------------- settlement (only when something is owed)
            Section {
                objectName: "exchangeMoneySection"
                visible: !!exchangePage.s.finalAmount && exchangePage.s.direction !== "NONE"
                title: exchangePage.s.moneyDirectionText ? "التسوية — " + exchangePage.s.moneyDirectionText : "التسوية"
                accent: theme.priceBuy
                Flow {
                    Layout.fillWidth: true
                    layoutDirection: Qt.RightToLeft
                    spacing: 6
                    Choice {
                        id: methodChoice
                        objectName: "exchangeMethod"
                        width: 130
                        enabled: !!exchangePage.vm && !exchangePage.vm.done
                        model: exchangePage.vm ? exchangePage.vm.methods : []
                        textRole: "text"; valueRole: "code"
                        onActivated: exchangePage.vm.setMethod(currentValue)
                    }
                    Choice {
                        id: bankChoice
                        objectName: "exchangeBankAccount"
                        width: 170
                        visible: !!exchangePage.vm && exchangePage.vm.methodNeedsAccount
                        enabled: !!exchangePage.vm && !exchangePage.vm.done
                        model: exchangePage.vm ? [{ id: "", name: "اختر الحساب" }].concat(exchangePage.vm.bankAccounts) : []
                        textRole: "name"; valueRole: "id"
                        onActivated: exchangePage.vm.setBankAccount(currentValue)
                    }
                    Field { objectName: "exchangeAmount"; width: 130
                            enabled: !!exchangePage.vm && !exchangePage.vm.done
                            text: exchangePage.vm ? exchangePage.vm.amount : ""
                            placeholderText: activeFocus || text.length ? "" : "المبلغ الفعلي"
                            validator: RegularExpressionValidator { regularExpression: /^[0-9]{0,9}([.][0-9]{0,2})?$/ }
                            onTextEdited: exchangePage.vm.setAmount(text) }
                    Btn { objectName: "exchangePayFull"; text: "المبلغ كاملاً"
                          enabled: !!exchangePage.vm && !exchangePage.vm.done
                          onClicked: exchangePage.vm.payFullAmount() }
                    Field { objectName: "exchangeAccountAmount"; width: 150
                            visible: !!exchangePage.h.hasCustomer
                            enabled: !!exchangePage.vm && !exchangePage.vm.done
                            text: exchangePage.vm ? exchangePage.vm.accountAmount : ""
                            placeholderText: activeFocus || text.length ? "" : (exchangePage.s.direction === "CUSTOMER_OWES"
                                                                 ? "على حساب العميل" : "لحساب العميل")
                            validator: RegularExpressionValidator { regularExpression: /^[0-9]{0,9}([.][0-9]{0,2})?$/ }
                            onTextEdited: exchangePage.vm.setAccountAmount(text) }
                }
            }

            Repeater {
                model: exchangePage.vm ? exchangePage.vm.problems : []
                delegate: Txt { required property var modelData
                                Layout.fillWidth: true; value: "• " + modelData; color: theme.red }
            }
            Item { Layout.preferredHeight: 4 }
        }
    }

    // Always visible under the form (never clipped at 1080×680).
    RowLayout {
        id: actionBar
        visible: !exchangePage.viewMode && !!exchangePage.h.number
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.margins: 12
        height: visible ? 34 : 0
        layoutDirection: Qt.RightToLeft
        spacing: 8
        Btn { objectName: "exchangeSave"; primary: true
              text: exchangePage.vm && exchangePage.vm.saving ? "جاري الحفظ..." : "حفظ الاستبدال"
              enabled: !!exchangePage.vm && exchangePage.vm.canFinalize
              onClicked: exchangePage.vm.finalize() }
        CheckBox { objectName: "exchangePriceAck"; visible: !!exchangePage.s.needsAck
                   text: "أقر بسعر الذهب الحالي"; font.family: theme.fontFamily
                   onToggled: exchangePage.vm.setPriceAcknowledged(checked) }
        Txt { objectName: "exchangeCalculating"; visible: !!exchangePage.vm && exchangePage.vm.previewBusy
              value: "جاري الحساب..."; color: theme.textMuted; font.pixelSize: theme.fontCaption }
        Item { Layout.fillWidth: true }
        Txt { objectName: "exchangeMessage"; Layout.maximumWidth: exchangePage.width * 0.6
              visible: value.length > 0
              value: !exchangePage.vm ? "" : exchangePage.vm.done ? exchangePage.vm.doneMessage
                     : (exchangePage.h.number ? exchangePage.vm.errorMessage : "")
              color: exchangePage.vm && exchangePage.vm.done ? theme.green : theme.red }
    }

    // ============================================================ VIEW (read-only)
    ScrollView {
        id: viewScroll
        objectName: "exchangeViewScroll"
        visible: exchangePage.viewMode && !!exchangePage.d.code
        anchors.top: topBar.bottom; anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.margins: 12
        clip: true
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            width: viewScroll.availableWidth
            spacing: 8

            GridLayout {
                Layout.fillWidth: true
                columns: exchangePage.wide ? 2 : 1
                layoutDirection: Qt.RightToLeft
                columnSpacing: 8; rowSpacing: 8

                Section {
                    objectName: "exchangeViewHeader"
                    title: "الاستبدال"
                    Pair { label: "رقم الاستبدال"; value: exchangePage.d.code || ""; strong: true; valueColor: theme.gold }
                    Pair { label: "الفاتورة الأصلية"; value: exchangePage.d.sourceNumber || "" }
                    Pair { label: "التاريخ والوقت"; value: exchangePage.d.occurredAt || "" }
                    Pair { label: "بواسطة"; value: exchangePage.d.actor || "" }
                }
                Section {
                    objectName: "exchangeViewMoney"
                    title: "النتيجة"
                    accent: theme.priceSell
                    Pair { label: exchangePage.d.deltaLabel || ""; value: exchangePage.d.delta || "" }
                    Pair { label: "السعر المستخدم"; visible: (exchangePage.d.priceText || "").length > 0
                           value: exchangePage.d.priceText || "" }
                    Pair { label: "قيمة فرق الذهب"; value: exchangePage.d.goldAmount || "" }
                    Pair { label: "المصنعية"; visible: (exchangePage.d.makingText || "").length > 0
                           value: exchangePage.d.makingText || "" }
                    Pair { objectName: "exchangeViewFinal"; label: exchangePage.d.directionText || ""
                           value: exchangePage.d.finalAmount || ""; strong: true
                           valueColor: exchangePage.directionColor(exchangePage.d.direction) }
                    Pair { label: "حساب العميل"; value: exchangePage.d.accountText || "" }
                }
            }
            GridLayout {
                Layout.fillWidth: true
                columns: exchangePage.wide ? 2 : 1
                layoutDirection: Qt.RightToLeft
                columnSpacing: 8; rowSpacing: 8
                Section {
                    title: "القطع المرتجعة — " + (exchangePage.d.returnedPure || "")
                    accent: theme.accountPayable
                    Repeater { model: exchangePage.d.returned || []
                               delegate: Pair { required property var modelData
                                                label: modelData.item + " • عيار " + modelData.karat
                                                value: modelData.weight + "  (صافي " + modelData.pure + ")" } }
                    Txt { value: exchangePage.d.pendingText || ""; color: theme.amber }
                }
                Section {
                    title: "القطع البديلة — " + (exchangePage.d.replacementPure || "")
                    accent: theme.priceSell
                    Repeater { model: exchangePage.d.replacements || []
                               delegate: Pair { required property var modelData
                                                label: modelData.item + " • عيار " + modelData.karat
                                                value: modelData.weight + "  (صافي " + modelData.pure + ")" } }
                }
            }
            Section {
                title: "الدفع الفعلي"
                accent: theme.priceBuy
                Repeater { model: exchangePage.d.payments || []
                           delegate: Txt { required property var modelData
                                           Layout.fillWidth: true; value: modelData; color: theme.textPrimary } }
            }
            Txt { Layout.fillWidth: true; visible: value.length > 0
                  value: exchangePage.d.notes ? "ملاحظات: " + exchangePage.d.notes : ""; color: theme.textMuted }
        }
    }
}
