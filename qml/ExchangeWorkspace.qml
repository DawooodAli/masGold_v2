import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// استبدال (Exchange V1). Opened from an invoice in سجل الفواتير. Every value is computed by
// ExchangeService and formatted by exchangeVM; this page only forwards selections and typing.
// A finished exchange is shown inside the invoice's History detail (no second, repeating page).
Item {
    id: exchangePage
    objectName: "exchangeWorkspace"

    readonly property var vm: typeof exchangeVM !== "undefined" ? exchangeVM : null
    readonly property var h: vm ? vm.header : ({})
    readonly property var s: vm ? vm.summary : ({})
    readonly property var st: vm ? vm.settlement : ({})
    readonly property var totals: vm ? vm.replacementTotals : ({})
    readonly property bool wide: width >= 1000
    readonly property bool editable: !!vm && !vm.done

    Theme { id: theme }

    Shortcut {
        sequence: "Esc"
        enabled: exchangePage.visible && !!exchangePage.vm && !exchangePage.vm.saving
        onActivated: exchangePage.vm.searchOpen ? exchangePage.vm.closeSearch() : exchangePage.vm.close()
    }
    Shortcut {
        sequence: "F3"
        enabled: exchangePage.visible && exchangePage.editable
        onActivated: exchangePage.vm.openSearch()
    }
    // The same entry keys as Billing: F5 إدخال يدوي, F7 من الكسر.
    Shortcut {
        sequence: "F5"
        enabled: exchangePage.visible && exchangePage.editable
        onActivated: exchangePage.vm.openPanel("manual")
    }
    Shortcut {
        sequence: "F7"
        enabled: exchangePage.visible && exchangePage.editable
        onActivated: exchangePage.vm.openPanel("stock")
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
        radius: theme.radiusSmall
        border.color: theme.border
        Rectangle { anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                    anchors.margins: 1; width: 3; radius: 2; color: section.accent }
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

    component Choice: ComboBox {
        implicitHeight: 30
        font.family: theme.fontFamily
        font.pixelSize: theme.fontBody
    }

    // Count, total grams and grams per karat (as under Billing's sold list).
    component WeightTotals: Rectangle {
        id: totalsRoot
        property var summary: ({})
        property color accent: theme.gold
        Layout.fillWidth: true
        Layout.preferredHeight: 28
        radius: 4
        color: theme.bgTertiary
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10
            layoutDirection: Qt.RightToLeft; spacing: 14
            Txt { value: (totalsRoot.summary.count || "0") + " قطعة"; color: theme.textMuted }
            Txt { value: "الإجمالي " + (totalsRoot.summary.total || ""); color: totalsRoot.accent; font.bold: true
                  font.pixelSize: theme.fontBodyStrong }
            Repeater {
                model: totalsRoot.summary.karats || []
                delegate: Txt { required property var modelData
                                value: "عيار " + modelData.karat + ": " + modelData.weight; color: theme.textSecondary }
            }
            Item { Layout.fillWidth: true }
        }
    }

    // ============================================================ TOP BAR
    RowLayout {
        id: topBar
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        anchors.margins: 12
        layoutDirection: Qt.RightToLeft
        spacing: 10
        Btn { objectName: "exchangeBack"; text: "رجوع  ⁦Esc⁩"; enabled: !!exchangePage.vm && !exchangePage.vm.saving
              onClicked: exchangePage.vm.close() }
        Txt { objectName: "exchangeTitle"; color: theme.gold; font.bold: true; font.pixelSize: theme.fontPageTitle
              value: exchangePage.h.number ? "استبدال — فاتورة " + exchangePage.h.number : "استبدال" }
        Txt { visible: !!exchangePage.h.number; color: theme.textSecondary; font.pixelSize: theme.fontBodyStrong
              value: exchangePage.h.customerName ? "العميل: " + exchangePage.h.customerName : "" }
        Item { Layout.fillWidth: true }
        Txt { visible: !!exchangePage.h.windowText; value: exchangePage.h.windowText || ""
              color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
    }

    Txt { objectName: "exchangeState"; anchors.centerIn: parent
          visible: !!exchangePage.vm && (exchangePage.vm.busy
                   || (exchangePage.vm.errorMessage.length > 0 && !exchangePage.h.number))
          value: !exchangePage.vm ? "" : exchangePage.vm.busy ? "جاري التحميل..." : exchangePage.vm.errorMessage
          color: exchangePage.vm && !exchangePage.vm.busy ? theme.red : theme.textMuted
          font.pixelSize: theme.fontBodyStrong }

    // ============================================================ ENTRY
    ScrollView {
        id: editScroll
        objectName: "exchangeEditScroll"
        visible: !!exchangePage.h.number
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
                    RowLayout {
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                        Item { Layout.preferredWidth: 14 }
                        Head { value: "الصنف"; Layout.fillWidth: true }
                        Head { value: "عيار"; Layout.preferredWidth: 34 }
                        Head { value: "الوزن"; Layout.preferredWidth: 78 }
                        Head { value: "الصافي"; Layout.preferredWidth: 78 }
                    }
                    Repeater {
                        model: exchangePage.vm ? exchangePage.vm.lines : []
                        delegate: Rectangle {
                            id: lineRow
                            required property var modelData
                            required property int index
                            objectName: "exchangeLine_" + index
                            Layout.fillWidth: true
                            Layout.preferredHeight: lineCol.implicitHeight + 8
                            radius: 3
                            color: modelData.selected ? theme.panelHover : (index % 2 ? theme.bgSecondary : theme.bgTertiary)
                            border.color: modelData.selected ? theme.accountPayable : "transparent"
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
                                                opacity: lineRow.modelData.exchangeable ? 1 : 0
                                                color: lineRow.modelData.selected ? theme.accountPayable : "transparent"
                                                border.color: lineRow.modelData.selected ? theme.accountPayable : theme.textMuted }
                                    Txt { value: lineRow.modelData.item; color: lineRow.modelData.exchangeable
                                              ? theme.textPrimary : theme.textMuted
                                          font.bold: true; font.pixelSize: theme.fontBodyStrong; Layout.fillWidth: true }
                                    Txt { value: lineRow.modelData.karat; Layout.preferredWidth: 34 }
                                    Txt { value: lineRow.modelData.weight; color: theme.textPrimary; font.bold: true
                                          Layout.preferredWidth: 78 }
                                    Txt { value: lineRow.modelData.pure; color: theme.textMuted
                                          font.pixelSize: theme.fontTableHeader; Layout.preferredWidth: 78 }
                                }
                                Txt { Layout.fillWidth: true; visible: value.length > 0; value: lineRow.modelData.reason
                                      color: theme.amber; font.pixelSize: theme.fontCaption; elide: Text.ElideRight }
                            }
                            MouseArea {
                                anchors.fill: parent
                                enabled: lineRow.modelData.exchangeable && exchangePage.editable
                                cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: exchangePage.vm.toggleReturned(lineRow.modelData.lineId)
                            }
                        }
                    }
                    Txt { value: "القطعة المرتجعة تُسجل «بانتظار الفحص» ولا تعود للبيع مباشرة."
                          color: theme.textMuted; font.pixelSize: theme.fontCaption; Layout.fillWidth: true
                          elide: Text.ElideRight }
                }

                // -------------------------------------------- replacements: Billing's sell section, reused
                Section {
                    objectName: "exchangeReplacementSection"
                    title: "القطع البديلة (بيع)"
                    accent: theme.priceSell
                    RowLayout {
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 4
                        BarcodeField {
                            id: scanField
                            objectName: "exchangeScan"
                            Layout.fillWidth: true
                            Layout.maximumWidth: 220
                            Layout.minimumWidth: 100
                            implicitHeight: 30
                            enabled: exchangePage.editable
                            font.family: theme.fontFamily
                            font.pixelSize: theme.fontBodyStrong
                            color: theme.textPrimary
                            placeholderTextColor: theme.textDisabled
                            horizontalAlignment: TextInput.AlignRight
                            selectByMouse: true
                            placeholderText: activeFocus || text.length ? "" : "الباركود"
                            background: Rectangle { radius: 4; color: theme.bgPrimary
                                                    border.color: scanField.activeFocus ? theme.gold : theme.border
                                                    border.width: scanField.activeFocus ? 2 : 1 }
                            onAccepted: { exchangePage.vm.addReplacementBarcode(text); text = "" }
                        }
                        Btn { objectName: "exchangeAddReplacement"; text: "إضافة"
                              enabled: exchangePage.editable && scanField.text.length > 0
                              onClicked: { exchangePage.vm.addReplacementBarcode(scanField.text); scanField.text = "" } }
                        Btn { objectName: "exchangeOpenSearch"; text: "بحث F3"; enabled: exchangePage.editable
                              onClicked: exchangePage.vm.openSearch() }
                        Btn { objectName: "exchangeOpenManual"; text: "يدوي F5"; enabled: exchangePage.editable
                              onClicked: exchangePage.vm.openPanel("manual") }
                        Btn { objectName: "exchangeOpenStock"; text: "من الكسر F7"; enabled: exchangePage.editable
                              onClicked: exchangePage.vm.openPanel("stock") }
                        Item { Layout.fillWidth: true }
                    }

                    // ---- F5 إدخال يدوي / F7 من الكسر: Billing's fields, validated by the service (Billing's rules)
                    Rectangle {
                        id: describedPanel
                        objectName: "exchangeDescribedPanel"
                        readonly property bool manualMode: !!exchangePage.vm && exchangePage.vm.panel === "manual"
                        visible: !!exchangePage.vm && exchangePage.vm.panel.length > 0
                        // Each opening (F5 or F7) starts clean: nothing carried over from the other panel.
                        onManualModeChanged: reset()
                        onVisibleChanged: if (visible) reset()
                        function reset() {
                            dType.currentIndex = 0; dFactory.currentIndex = 0; dReason.currentIndex = 0
                            dWeight.text = ""; dDescription.text = ""; dSize.text = ""; dNotes.text = ""
                        }
                        Layout.fillWidth: true
                        Layout.preferredHeight: describedCol.implicitHeight + 12
                        radius: 4
                        color: theme.bgSecondary
                        border.color: manualMode ? theme.amber : theme.balanceKarat21
                        ColumnLayout {
                            id: describedCol
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            anchors.margins: 6
                            spacing: 4
                            Txt { value: describedPanel.manualMode ? "إدخال يدوي — قطعة غير محددة في المخزون (تُرسل للمراجعة)"
                                                                   : "من الكسر — وزن من رصيد الكسر المتاح قبل الاستبدال"
                                  color: describedPanel.manualMode ? theme.amber : theme.balanceKarat21; font.bold: true }
                            Flow {
                                Layout.fillWidth: true
                                layoutDirection: Qt.RightToLeft
                                spacing: 4
                                Choice { id: dType; objectName: "exchangeManualType"; width: 120
                                         model: exchangePage.vm ? exchangePage.vm.itemTypes : []
                                         textRole: "name"; valueRole: "id" }
                                Choice { id: dKarat; objectName: "exchangeDescribedKarat"; width: 150
                                         model: exchangePage.vm ? exchangePage.vm.karats : []
                                         textRole: "name"; valueRole: "id"
                                         displayText: currentIndex >= 0 && model.length > currentIndex
                                                      ? model[currentIndex].name + (describedPanel.manualMode ? ""
                                                        : "  (" + model[currentIndex].stock + ")") : "" }
                                Field { id: dWeight; objectName: "exchangeDescribedWeight"; width: 84
                                        placeholderText: activeFocus || text.length ? "" : "الوزن"
                                        validator: RegularExpressionValidator { regularExpression: /^[0-9]{0,5}([.][0-9]{0,3})?$/ } }
                                Choice { id: dFactory; objectName: "exchangeManualFactory"; width: 110
                                         model: exchangePage.vm ? exchangePage.vm.factories : []
                                         textRole: "name"; valueRole: "id" }
                                Field { id: dDescription; objectName: "exchangeDescribedDescription"; width: 130
                                        placeholderText: activeFocus || text.length ? "" : "الوصف" }
                                Field { id: dSize; objectName: "exchangeManualSize"; width: 74; visible: describedPanel.manualMode
                                        placeholderText: activeFocus || text.length ? "" : "المقاس" }
                                Choice { id: dReason; objectName: "exchangeManualReason"; width: 170; visible: describedPanel.manualMode
                                         model: exchangePage.vm ? exchangePage.vm.reasons : []
                                         textRole: "text"; valueRole: "code" }
                                Field { id: dNotes; objectName: "exchangeManualNotes"; width: 150
                                        placeholderText: activeFocus || text.length ? "" : "ملاحظات" }
                                Btn { objectName: "exchangeDescribedAdd"; primary: true; text: "إضافة"
                                      onClicked: {
                                          var fields = { karatId: dKarat.currentValue || 0, weight: dWeight.text,
                                                         itemTypeId: dType.currentValue || 0, factoryId: dFactory.currentValue || 0,
                                                         description: dDescription.text, size: dSize.text, notes: dNotes.text,
                                                         reasonCode: dReason.currentValue || "" }
                                          if (describedPanel.manualMode) exchangePage.vm.addManual(fields)
                                          else exchangePage.vm.addFromOldGold(fields)
                                          dWeight.text = ""; dDescription.text = ""; dSize.text = ""; dNotes.text = ""
                                      } }
                                Btn { objectName: "exchangeDescribedClose"; text: "إغلاق"; onClicked: exchangePage.vm.closePanel() }
                            }
                        }
                    }
                    Txt { objectName: "exchangeScanError"; Layout.fillWidth: true; visible: value.length > 0
                          value: exchangePage.vm ? exchangePage.vm.scanError : ""; color: theme.red }

                    // ---- بحث عن قطعة (IN_STOCK only; the employee chooses the physical piece)
                    Rectangle {
                        objectName: "exchangeSearchPanel"
                        visible: !!exchangePage.vm && exchangePage.vm.searchOpen
                        Layout.fillWidth: true
                        Layout.preferredHeight: searchCol.implicitHeight + 12
                        radius: 4
                        color: theme.bgSecondary
                        border.color: theme.goldDark
                        ColumnLayout {
                            id: searchCol
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            anchors.margins: 6
                            spacing: 4
                            Flow {
                                Layout.fillWidth: true
                                layoutDirection: Qt.RightToLeft
                                spacing: 4
                                Field { id: searchText; objectName: "exchangeSearchText"; width: 150
                                        placeholderText: activeFocus || text.length ? "" : "الصنف أو الباركود"
                                        onAccepted: searchGo.clicked() }
                                Choice { id: searchKarat; objectName: "exchangeSearchKarat"; width: 118
                                         model: [{ k: 0, t: "كل العيارات" }, { k: 21, t: "عيار 21" },
                                                 { k: 18, t: "عيار 18" }, { k: 24, t: "عيار 24" }]
                                         textRole: "t"; valueRole: "k" }
                                Field { id: searchWeight; objectName: "exchangeSearchWeight"; width: 78
                                        placeholderText: activeFocus || text.length ? "" : "الوزن"
                                        validator: RegularExpressionValidator { regularExpression: /^[0-9]{0,4}([.][0-9]{0,3})?$/ }
                                        onAccepted: searchGo.clicked() }
                                Field { id: searchTolerance; objectName: "exchangeSearchTolerance"; width: 64; text: "0.05"
                                        validator: RegularExpressionValidator { regularExpression: /^[0-9]?([.][0-9]{0,3})?$/ } }
                                Btn { id: searchGo; objectName: "exchangeSearchGo"; primary: true; text: "بحث"
                                      onClicked: exchangePage.vm.runSearch(searchText.text, searchKarat.currentValue || 0,
                                                                           searchWeight.text, searchTolerance.text) }
                                Btn { objectName: "exchangeSearchClose"; text: "إغلاق"; onClicked: exchangePage.vm.closeSearch() }
                            }
                            Txt { Layout.fillWidth: true; visible: value.length > 0; color: theme.textMuted
                                  value: !exchangePage.vm ? "" : exchangePage.vm.searching ? "جاري البحث..."
                                         : exchangePage.vm.searchMessage }
                            Repeater {
                                model: exchangePage.vm ? exchangePage.vm.searchResults : []
                                delegate: Rectangle {
                                    id: resultRow
                                    required property var modelData
                                    required property int index
                                    objectName: "exchangeSearchRow_" + index
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 30
                                    radius: 3
                                    color: resultHover.hovered ? theme.panelHover : (index % 2 ? "transparent" : theme.bgTertiary)
                                    HoverHandler { id: resultHover }
                                    RowLayout {
                                        anchors.fill: parent; anchors.leftMargin: 4; anchors.rightMargin: 6
                                        layoutDirection: Qt.RightToLeft; spacing: 8
                                        Txt { value: resultRow.modelData.item; color: theme.textPrimary; font.bold: true
                                              Layout.fillWidth: true }
                                        Txt { value: "عيار " + resultRow.modelData.karat }
                                        Txt { value: resultRow.modelData.weight; color: theme.textPrimary; font.bold: true }
                                        Txt { value: resultRow.modelData.factory; color: theme.textMuted }
                                        Txt { value: resultRow.modelData.barcode; color: theme.textMuted
                                              font.pixelSize: theme.fontTableHeader }
                                        Btn { objectName: "exchangeSearchPick_" + resultRow.index
                                              text: "اختيار"; implicitHeight: 24
                                              onClicked: exchangePage.vm.addFromSearch(resultRow.modelData.itemId) }
                                    }
                                }
                            }
                        }
                    }

                    // ---- the chosen pieces (Billing's invoice-items table)
                    RowLayout {
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 8
                        Head { value: "الصنف"; Layout.fillWidth: true }
                        Head { value: "الباركود"; Layout.preferredWidth: 76; visible: exchangePage.width >= 1200 }
                        Head { value: "عيار"; Layout.preferredWidth: 34 }
                        Head { value: "الوزن"; Layout.preferredWidth: 78 }
                        Head { value: "مصنعية ج/جرام"; Layout.preferredWidth: 92; visible: !!exchangePage.s.makingRequired }
                        Item { Layout.preferredWidth: 52 }
                    }
                    Repeater {
                        model: exchangePage.vm ? exchangePage.vm.replacements : []
                        delegate: Rectangle {
                            id: repRow
                            required property var modelData
                            required property int index
                            objectName: "exchangeReplacement_" + index
                            Layout.fillWidth: true
                            Layout.preferredHeight: 34
                            radius: 3
                            color: index % 2 ? theme.bgSecondary : theme.bgTertiary
                            RowLayout {
                                anchors.fill: parent; anchors.leftMargin: 2; anchors.rightMargin: 6
                                layoutDirection: Qt.RightToLeft; spacing: 8
                                Txt { value: repRow.modelData.item; color: theme.textPrimary; font.bold: true
                                      font.pixelSize: theme.fontBodyStrong; Layout.fillWidth: true }
                                // How the piece was added (Billing's tags): يدوي in amber, من الكسر in the karat colour.
                                Txt { objectName: "exchangeReplacementSource_" + repRow.index; value: repRow.modelData.sourceText
                                      visible: repRow.modelData.manual || repRow.modelData.fromOldGold
                                      color: repRow.modelData.manual ? theme.amber : theme.balanceKarat21
                                      font.pixelSize: theme.fontCaption }
                                Txt { value: repRow.modelData.barcode || "—"; color: theme.textMuted
                                      font.pixelSize: theme.fontTableHeader; Layout.preferredWidth: 76
                                      visible: exchangePage.width >= 1200 }
                                Txt { value: repRow.modelData.karat; Layout.preferredWidth: 34 }
                                Txt { value: repRow.modelData.weight; color: theme.textPrimary; font.bold: true
                                      Layout.preferredWidth: 78 }
                                // Making is typed only when the customer receives more gold.
                                Field { objectName: "exchangeMaking_" + repRow.index
                                        Layout.preferredWidth: 92
                                        visible: !!exchangePage.s.makingRequired
                                        enabled: exchangePage.editable
                                        text: repRow.modelData.making
                                        placeholderText: activeFocus || text.length ? "" : "مصنعية"
                                        validator: RegularExpressionValidator { regularExpression: /^[0-9]{0,6}([.][0-9]{0,2})?$/ }
                                        onTextEdited: exchangePage.vm.setMaking(repRow.modelData.itemId, text) }
                                Btn { text: "حذف"; implicitWidth: 52; implicitHeight: 26; enabled: exchangePage.editable
                                      onClicked: exchangePage.vm.removeReplacement(repRow.modelData.itemId) }
                            }
                        }
                    }
                    Txt { visible: !exchangePage.vm || exchangePage.vm.replacements.length === 0
                          value: "امسح الباركود — أو F3 بحث • F5 يدوي • F7 من الكسر"; color: theme.textDisabled
                          Layout.alignment: Qt.AlignHCenter }
                    WeightTotals { objectName: "exchangeReplacementTotals"
                                   visible: !!exchangePage.vm && exchangePage.vm.replacements.length > 0
                                   summary: exchangePage.totals; accent: theme.priceSell }
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
                          font.pixelSize: theme.fontBodyStrong; color: exchangePage.directionColor(exchangePage.s.direction) }
                    Item { Layout.fillWidth: true }
                    Txt { objectName: "exchangeFinalAmount"; value: exchangePage.s.finalAmount || ""; font.bold: true
                          font.pixelSize: theme.fontNumber; color: exchangePage.directionColor(exchangePage.s.direction) }
                }
                Txt { objectName: "exchangePending"; Layout.fillWidth: true; value: exchangePage.s.pendingText || ""
                      color: theme.amber }
                Txt { objectName: "exchangeReview"; Layout.fillWidth: true; visible: value.length > 0
                      value: exchangePage.s.reviewText || ""; color: theme.amber; font.pixelSize: theme.fontCaption }
            }


            Repeater {
                model: exchangePage.vm ? exchangePage.vm.problems : []
                delegate: Txt { required property var modelData
                                Layout.fillWidth: true; value: "• " + modelData; color: theme.red }
            }
            Item { Layout.preferredHeight: 4 }
        }
    }

    // ============================================================ SETTLEMENT + SAVE (fixed, always on screen)
    // The money is settled where the result is read: never below the fold, never on another page.
    Rectangle {
        id: actionBar
        objectName: "exchangeSettlementBar"
        visible: !!exchangePage.h.number
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.margins: 12
        height: visible ? barCol.implicitHeight + 14 : 0
        radius: theme.radiusSmall
        color: theme.panel
        border.color: exchangePage.st.direction === "CUSTOMER_OWES" ? theme.accountReceivable
                      : exchangePage.st.direction === "SHOP_OWES" ? theme.accountPayable : theme.border

        ColumnLayout {
            id: barCol
            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: 10; anchors.rightMargin: 12
            spacing: 6

            // 1. the result: who owes whom, and how much
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 12
                Txt { objectName: "exchangeResultDirection"; font.bold: true; font.pixelSize: theme.fontSection
                      value: exchangePage.st.resultLabel || (exchangePage.vm && exchangePage.vm.previewBusy
                                                             ? "جاري الحساب..." : "اختر القطع لحساب الفرق")
                      color: exchangePage.directionColor(exchangePage.st.direction) }
                Txt { objectName: "exchangeResultAmount"; visible: value.length > 0; value: exchangePage.st.due || ""
                      font.bold: true; font.pixelSize: theme.fontNumber
                      color: exchangePage.directionColor(exchangePage.st.direction) }
                Item { Layout.fillWidth: true }
                Txt { objectName: "exchangeRemaining"; visible: value.length > 0; value: exchangePage.st.remainingText || ""
                      color: exchangePage.st.complete ? theme.green : theme.amber; font.bold: true }
            }

            // 2. how it is settled: one real-money leg (the direction follows the result) + optional account
            Flow {
                objectName: "exchangeMoneySection"
                Layout.fillWidth: true
                visible: !!exchangePage.st.needsMoney
                layoutDirection: Qt.RightToLeft
                spacing: 6
                Txt { height: 30; value: exchangePage.st.moneyLabel || ""; color: theme.textSecondary; font.bold: true }
                Choice {
                    objectName: "exchangeMethod"
                    width: 120
                    enabled: exchangePage.editable
                    model: exchangePage.vm ? exchangePage.vm.methods : []
                    textRole: "text"; valueRole: "code"
                    // Always shows the ViewModel's method (نقدي by default, as in Billing).
                    currentIndex: exchangePage.vm ? exchangePage.vm.methods.findIndex(function(m) {
                                                        return m.code === exchangePage.vm.method }) : 0
                    onActivated: exchangePage.vm.setMethod(currentValue)
                }
                // Only the accounts eligible for the chosen method (bank for InstaPay / transfer, wallet for محفظة).
                Choice {
                    objectName: "exchangeBankAccount"
                    width: 160
                    visible: !!exchangePage.vm && exchangePage.vm.methodNeedsAccount
                    enabled: exchangePage.editable
                    model: exchangePage.vm ? [{ id: "", name: "اختر الحساب" }].concat(exchangePage.vm.bankAccounts) : []
                    textRole: "name"; valueRole: "id"
                    onActivated: exchangePage.vm.setBankAccount(currentValue)
                }
                Field { objectName: "exchangeAmount"; width: 120
                        enabled: exchangePage.editable
                        text: exchangePage.vm ? exchangePage.vm.amount : ""
                        placeholderText: activeFocus || text.length ? "" : "المبلغ الفعلي"
                        validator: RegularExpressionValidator { regularExpression: /^[0-9]{0,9}([.][0-9]{0,2})?$/ }
                        onTextEdited: exchangePage.vm.setAmount(text) }
                Btn { objectName: "exchangePayFull"; text: "المبلغ كاملاً"
                      enabled: exchangePage.editable
                      onClicked: exchangePage.vm.payFullAmount() }
                // Explicit customer-account part (never silent debt / credit); only with a registered customer.
                Txt { height: 30; visible: !!exchangePage.h.hasCustomer; value: exchangePage.st.accountLabel || ""
                      color: theme.textMuted }
                Field { objectName: "exchangeAccountAmount"; width: 110
                        visible: !!exchangePage.h.hasCustomer
                        enabled: exchangePage.editable
                        text: exchangePage.vm ? exchangePage.vm.accountAmount : ""
                        placeholderText: activeFocus || text.length ? "" : "0.00"
                        validator: RegularExpressionValidator { regularExpression: /^[0-9]{0,9}([.][0-9]{0,2})?$/ }
                        onTextEdited: exchangePage.vm.setAccountAmount(text) }
            }

            // 3. save
            RowLayout {
                Layout.fillWidth: true
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
                // Why Save is disabled (the first blocking problem), or the save result.
                Txt { objectName: "exchangeMessage"; Layout.maximumWidth: exchangePage.width * 0.65
                      visible: value.length > 0
                      value: !exchangePage.vm ? "" : exchangePage.vm.done ? exchangePage.vm.doneMessage + "  (رجوع لعرضه في الفاتورة)"
                             : (exchangePage.vm.errorMessage || (exchangePage.vm.problems.length ? exchangePage.vm.problems[0] : ""))
                      color: exchangePage.vm && exchangePage.vm.done ? theme.green : theme.red }
            }
        }
    }
}
