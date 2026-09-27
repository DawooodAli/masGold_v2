import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// سجل الفواتير (read-only). Every value comes formatted from invoiceHistoryVM; this page only
// forwards search text, filters and selections. No invoice can be changed from here.
Item {
    id: historyPage
    objectName: "invoiceHistoryPage"

    property string pageTitle: ""
    readonly property var vm: typeof invoiceHistoryVM !== "undefined" ? invoiceHistoryVM : null
    readonly property bool detailMode: !!vm && vm.detailOpen
    // Below ~900 px of table the salesperson and time columns step aside (both stay in the detail).
    readonly property bool narrow: width < 1000

    Theme { id: theme }

    onVisibleChanged: if (visible && vm) vm.activate()

    Shortcut {
        sequence: "Esc"
        enabled: historyPage.visible && historyPage.detailMode
        onActivated: historyPage.vm.closeDetail()
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

    component Head: Txt {
        color: theme.textMuted
        font.pixelSize: theme.fontCaption
        font.bold: true
    }

    component Section: Rectangle {
        id: section
        property string title: ""
        property color accent: theme.gold
        default property alias content: sectionBody.data
        Layout.fillWidth: true
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
            spacing: 3
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
                                color: btn.primary ? (btn.hovered ? theme.goldDark : theme.bgTertiary)
                                                   : (btn.hovered ? theme.panelHover : theme.bgTertiary)
                                border.color: btn.primary ? theme.gold : (btn.activeFocus ? theme.gold : theme.border) }
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
        textRole: "text"
        valueRole: "code"
    }

    // ============================================================ LIST
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8
        visible: !historyPage.detailMode

        // Search + filters: one compact row (wraps to two at 1080 px).
        Flow {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            spacing: 6
            Field {
                id: searchField
                objectName: "historySearch"
                width: 260
                placeholderText: activeFocus || text.length ? "" : "رقم الفاتورة أو اسم / تليفون العميل"
                onTextEdited: historyPage.vm.setSearchText(text)
                onAccepted: historyPage.vm.searchNow()
            }
            Btn { objectName: "historyToday"; text: "اليوم"; onClicked: historyPage.vm.setToday() }
            Field { id: fromField; objectName: "historyDateFrom"; width: 104; placeholderText: activeFocus ? "" : "من 2026-09-01"
                    text: historyPage.vm ? historyPage.vm.dateFrom : "" }
            Field { id: toField; objectName: "historyDateTo"; width: 104; placeholderText: activeFocus ? "" : "إلى 2026-09-30"
                    text: historyPage.vm ? historyPage.vm.dateTo : "" }
            Btn { objectName: "historyApplyDates"; text: "تطبيق"; onClicked: historyPage.vm.setDateRange(fromField.text, toField.text) }
            Choice {
                objectName: "historyType"
                width: 118
                model: [{ code: "ALL", text: "كل العمليات" }, { code: "SELL", text: "بيع" },
                        { code: "BUY", text: "شراء كسر" }, { code: "MIXED", text: "بيع مع كسر" }]
                onActivated: historyPage.vm.setTypeFilter(currentValue)
            }
            Choice {
                objectName: "historyStatus"
                width: 124
                model: [{ code: "ALL", text: "كل الحالات" }, { code: "SETTLED", text: "مسددة" },
                        { code: "PARTIAL", text: "مسددة جزئياً" }, { code: "UNSETTLED", text: "غير مسددة" }]
                onActivated: historyPage.vm.setStatusFilter(currentValue)
            }
            Btn { objectName: "historyClear"; text: "مسح"; onClicked: { searchField.text = ""; historyPage.vm.clearFilters() } }
        }

        Txt { objectName: "historyError"; Layout.fillWidth: true; visible: value.length > 0
              value: historyPage.vm ? historyPage.vm.errorMessage : ""; color: theme.red }

        // The table: fixed column widths so values line up; one row per invoice.
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: theme.panel
            radius: theme.radiusSmall
            border.color: theme.border

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 6
                spacing: 2

                RowLayout {
                    Layout.fillWidth: true
                    layoutDirection: Qt.RightToLeft
                    spacing: historyPage.narrow ? 6 : 10
                    Head { value: "رقم الفاتورة"; Layout.preferredWidth: 86 }
                    Head { value: "التاريخ"; Layout.preferredWidth: 84 }
                    Head { value: "الوقت"; Layout.preferredWidth: 42; visible: !historyPage.narrow }
                    Head { value: "العميل"; Layout.fillWidth: true; Layout.minimumWidth: 90 }
                    Head { value: "البائع"; Layout.preferredWidth: 80; visible: !historyPage.narrow }
                    Head { value: "نوع العملية"; Layout.preferredWidth: 78 }
                    Head { value: "إجمالي الفاتورة"; Layout.preferredWidth: 116 }
                    Head { value: "المدفوع فعلياً"; Layout.preferredWidth: 132 }
                    Head { value: "الحالة"; Layout.preferredWidth: 80 }
                }

                ListView {
                    id: list
                    objectName: "historyList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    focus: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: historyPage.vm ? historyPage.vm.results : []
                    ScrollBar.vertical: ScrollBar {}
                    Keys.onReturnPressed: if (currentIndex >= 0) historyPage.vm.openInvoice(model[currentIndex].invoiceId)
                    Keys.onEnterPressed: if (currentIndex >= 0) historyPage.vm.openInvoice(model[currentIndex].invoiceId)
                    delegate: Rectangle {
                        id: row
                        required property var modelData
                        required property int index
                        objectName: "historyRow_" + index
                        width: list.width
                        height: 30
                        radius: 3
                        color: ListView.isCurrentItem ? theme.bgTertiary : index % 2 ? "transparent" : theme.bgSecondary
                        border.color: ListView.isCurrentItem ? theme.goldDark : "transparent"
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 6; anchors.rightMargin: 6
                            layoutDirection: Qt.RightToLeft
                            spacing: historyPage.narrow ? 6 : 10
                            Txt { value: row.modelData.number; color: theme.gold; font.bold: true; Layout.preferredWidth: 86 }
                            Txt { value: row.modelData.date; Layout.preferredWidth: 84 }
                            Txt { value: row.modelData.time; color: theme.textMuted; Layout.preferredWidth: 42
                                  visible: !historyPage.narrow }
                            Txt { value: row.modelData.customer; color: theme.textPrimary; Layout.fillWidth: true
                                  Layout.minimumWidth: 90 }
                            Txt { value: row.modelData.salesperson; color: theme.textMuted; Layout.preferredWidth: 80
                                  visible: !historyPage.narrow }
                            Txt { value: row.modelData.typeText; Layout.preferredWidth: 78 }
                            Txt { value: row.modelData.total; color: theme.textPrimary; font.bold: true
                                  Layout.preferredWidth: 116; horizontalAlignment: Text.AlignRight }
                            Txt { value: row.modelData.moved; Layout.preferredWidth: 132 }
                            Txt { value: row.modelData.statusText
                                  color: row.modelData.statusText === "مسددة" ? theme.green : theme.amber
                                  Layout.preferredWidth: 80 }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: { list.currentIndex = row.index; list.forceActiveFocus() }
                            onDoubleClicked: historyPage.vm.openInvoice(row.modelData.invoiceId)
                        }
                    }
                    Txt {
                        objectName: "historyEmpty"
                        anchors.centerIn: parent
                        visible: value.length > 0
                        value: historyPage.vm ? historyPage.vm.emptyText : ""
                        color: theme.textMuted
                        font.pixelSize: theme.fontBodyStrong
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            spacing: 8
            Btn { objectName: "historyPrevious"; text: "السابق"; enabled: !!historyPage.vm && historyPage.vm.hasPrevious
                  onClicked: historyPage.vm.previousPage() }
            Txt { objectName: "historyPageText"; value: historyPage.vm ? historyPage.vm.pageText : ""; color: theme.textMuted }
            Btn { objectName: "historyNext"; text: "التالي"; enabled: !!historyPage.vm && historyPage.vm.hasMore
                  onClicked: historyPage.vm.nextPage() }
            Item { Layout.fillWidth: true }
            Txt { value: historyPage.vm && historyPage.vm.busy ? "جاري التحميل..." : "انقر مرتين أو Enter لفتح الفاتورة"
                  color: theme.textMuted; font.pixelSize: theme.fontCaption }
        }
    }

    // ============================================================ DETAIL
    Item {
        anchors.fill: parent
        visible: historyPage.detailMode
        readonly property var d: historyPage.vm ? historyPage.vm.detail : ({})

        RowLayout {
            id: detailBar
            anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
            anchors.margins: 12
            layoutDirection: Qt.RightToLeft
            spacing: 8
            Btn { objectName: "historyBack"; text: "رجوع  ⁦Esc⁩"; onClicked: historyPage.vm.closeDetail() }
            Txt { value: parent.parent.d.number ? "فاتورة " + parent.parent.d.number : ""; color: theme.gold
                  font.bold: true; font.pixelSize: theme.fontPageTitle }
            Txt { value: parent.parent.d.legacyLabel || ""; visible: value.length > 0; color: theme.amber }
            Item { Layout.fillWidth: true }
            Txt { objectName: "historyReprintMessage"; value: historyPage.vm ? historyPage.vm.reprintMessage : ""
                  visible: value.length > 0; color: historyPage.vm && historyPage.vm.reprintFailed ? theme.red : theme.green }
            // The one action in V1. Future lifecycle actions get their own area when they exist.
            Btn { objectName: "historyReprint"; primary: true
                  text: historyPage.vm && historyPage.vm.reprintBusy ? "جاري الطباعة..." : "🖨  إعادة طباعة"
                  visible: !!parent.parent.d.canReprint
                  enabled: !!historyPage.vm && !historyPage.vm.reprintBusy
                  onClicked: historyPage.vm.reprint() }
        }

        Txt { objectName: "historyDetailState"; anchors.centerIn: parent
              visible: !!historyPage.vm && (historyPage.vm.detailBusy || historyPage.vm.detailError.length > 0)
              value: !historyPage.vm ? "" : historyPage.vm.detailBusy ? "جاري التحميل..."
                     : (historyPage.vm.detailError ? "تعذر تحميل الفاتورة: " + historyPage.vm.detailError : "")
              color: historyPage.vm && historyPage.vm.detailError ? theme.red : theme.textMuted
              font.pixelSize: theme.fontBodyStrong }

        ScrollView {
            id: detailScroll
            objectName: "historyDetailScroll"
            anchors.top: detailBar.bottom; anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            anchors.margins: 12
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            visible: !!parent.d.number
            readonly property var d: parent.d
            readonly property int cardColumns: width >= 1100 ? 3 : width >= 700 ? 2 : 1

            ColumnLayout {
                width: detailScroll.availableWidth
                spacing: 8

                Txt { Layout.fillWidth: true; visible: value.length > 0; value: detailScroll.d.incompleteText || ""
                      color: theme.amber }

                GridLayout {
                    Layout.fillWidth: true
                    columns: detailScroll.cardColumns
                    layoutDirection: Qt.RightToLeft
                    columnSpacing: 8; rowSpacing: 8

                    Section {
                        objectName: "historyHeaderSection"
                        Layout.alignment: Qt.AlignTop
                        title: "الفاتورة"
                        Pair { label: "رقم الفاتورة"; value: detailScroll.d.number || ""; strong: true; valueColor: theme.gold }
                        Pair { objectName: "historyFinalizedAt"; label: "تاريخ ووقت الحفظ"; value: detailScroll.d.finalizedAt || "" }
                        Pair { label: "نوع العملية"; value: detailScroll.d.typeText || "" }
                        Pair { label: "الحالة"; value: detailScroll.d.settlementText || "" }
                        Pair { label: "البائع"; value: detailScroll.d.salesperson || "" }
                        Pair { label: "حُفظت بواسطة"; value: detailScroll.d.finalizedBy || "" }
                    }
                    Section {
                        objectName: "historyCustomerSection"
                        Layout.alignment: Qt.AlignTop
                        title: "العميل"
                        accent: theme.accountReceivable
                        Pair { objectName: "historyCustomerName"; label: "الاسم"; value: detailScroll.d.customerName || ""; strong: true }
                        Pair { label: "التليفون"; value: detailScroll.d.customerMobile || "—" }
                        Pair { label: "العنوان"; value: detailScroll.d.customerPlace || "—" }
                        Pair { label: "كود العميل"; value: detailScroll.d.customerCode || "—" }
                    }
                    Section {
                        objectName: "historyMoneySection"
                        Layout.alignment: Qt.AlignTop
                        title: "الملخص المالي"
                        accent: theme.priceSell
                        Pair { objectName: "historyInvoiceTotal"; label: "إجمالي الفاتورة"; value: detailScroll.d.invoiceTotal || ""
                               strong: true; valueColor: theme.priceSell }
                        Pair { objectName: "historyMoved"; label: detailScroll.d.movedLabel || ""; value: detailScroll.d.movedAmount || "" }
                        Pair { objectName: "historyAccount"; label: "حساب العميل (هذه الفاتورة)"; value: detailScroll.d.accountText || ""
                               valueColor: detailScroll.d.accountDirection === "CUSTOMER_OWES" ? theme.accountReceivable
                                         : detailScroll.d.accountDirection === "SHOP_OWES" ? theme.accountPayable : theme.textSecondary }
                        Pair { objectName: "historyGoldValue"; label: "قيمة الذهب المباع"; value: detailScroll.d.goldValue || "" }
                        Pair { objectName: "historyAverageMaking"; label: "متوسط المصنعية"; value: detailScroll.d.averageMaking || "" }
                        Pair { objectName: "historyMakingTotal"; label: "إجمالي المصنعية (للمدير)"; visible: !!detailScroll.d.showManager
                               value: detailScroll.d.makingTotal || "" }
                        Pair { label: "صافي الكسر"; visible: (detailScroll.d.oldGoldTotal || "").length > 0
                               value: detailScroll.d.oldGoldTotal || "" }
                    }
                }

                Section {
                    objectName: "historyItemsSection"
                    title: "الأصناف المباعة"
                    RowLayout {
                        Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 10
                        Head { value: "م"; Layout.preferredWidth: 22 }
                        Head { value: "الصنف"; Layout.fillWidth: true; Layout.minimumWidth: 90 }
                        Head { value: "المصنع"; Layout.preferredWidth: 80 }
                        Head { value: "العيار"; Layout.preferredWidth: 40 }
                        Head { value: "الوزن"; Layout.preferredWidth: 80 }
                        Head { value: "العدد"; Layout.preferredWidth: 36 }
                        Head { value: "المقاس"; Layout.preferredWidth: 44 }
                        Head { value: "النوع"; Layout.preferredWidth: 170 }
                        Head { value: "ملاحظات"; Layout.preferredWidth: 120 }
                    }
                    Repeater {
                        model: historyPage.vm ? historyPage.vm.saleLines : []
                        delegate: RowLayout {
                            required property var modelData
                            required property int index
                            objectName: "historySaleLine_" + index
                            Layout.fillWidth: true; layoutDirection: Qt.RightToLeft; spacing: 10
                            Txt { value: modelData.serial; Layout.preferredWidth: 22 }
                            Txt { value: modelData.item; color: theme.textPrimary; Layout.fillWidth: true; Layout.minimumWidth: 90 }
                            Txt { value: modelData.factory; Layout.preferredWidth: 80 }
                            Txt { value: modelData.karat; Layout.preferredWidth: 40 }
                            Txt { value: modelData.weight; color: theme.textPrimary; Layout.preferredWidth: 80 }
                            Txt { value: modelData.qty; Layout.preferredWidth: 36 }
                            Txt { value: modelData.size; Layout.preferredWidth: 44 }
                            Txt { value: modelData.kindText; Layout.preferredWidth: 170
                                  color: modelData.manual ? theme.amber : modelData.fromOldGold ? theme.balanceKarat21 : theme.textMuted
                                  font.pixelSize: theme.fontTableHeader }
                            Txt { value: modelData.notes; color: theme.textMuted; Layout.preferredWidth: 120 }
                        }
                    }
                    Txt { visible: !historyPage.vm || historyPage.vm.saleLines.length === 0; value: "لا توجد أصناف مباعة"
                          color: theme.textMuted }
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: detailScroll.cardColumns
                    layoutDirection: Qt.RightToLeft
                    columnSpacing: 8; rowSpacing: 8

                    Section {
                        objectName: "historyPaymentsSection"
                        Layout.alignment: Qt.AlignTop
                        title: "الدفع الفعلي"
                        accent: theme.priceBuy
                        Repeater {
                            model: historyPage.vm ? historyPage.vm.payments : []
                            delegate: Pair { required property var modelData
                                             label: modelData.method + " (" + modelData.directionText + ")"
                                             value: modelData.amount }
                        }
                        Txt { visible: !historyPage.vm || historyPage.vm.payments.length === 0; value: "لا توجد مبالغ فعلية"
                              color: theme.textMuted }
                    }
                    Section {
                        objectName: "historyOldGoldSection"
                        Layout.alignment: Qt.AlignTop
                        title: "كسر من العميل (داخلي)"
                        accent: theme.balanceKarat21
                        visible: !!historyPage.vm && historyPage.vm.oldGoldLines.length > 0
                        Repeater {
                            model: historyPage.vm ? historyPage.vm.oldGoldLines : []
                            delegate: Pair { required property var modelData
                                             label: "كسر " + modelData.karat + " • " + modelData.weight + " • خصم " + modelData.discount
                                             value: modelData.value }
                        }
                    }
                    Section {
                        objectName: "historyPricesSection"
                        Layout.alignment: Qt.AlignTop
                        title: "سعر الذهب وقت الفاتورة"
                        accent: theme.priceSell
                        Repeater {
                            model: historyPage.vm ? historyPage.vm.priceSnapshots : []
                            delegate: Pair { required property var modelData
                                             label: "عيار " + modelData.karat
                                             value: "بيع " + modelData.sell + "  •  شراء " + modelData.buy }
                        }
                        Txt { visible: !historyPage.vm || historyPage.vm.priceSnapshots.length === 0
                              value: "غير متاح"; color: theme.textMuted }
                    }
                }

                Section {
                    objectName: "historyTimelineSection"
                    title: "سجل العمليات"
                    accent: theme.textMuted
                    Repeater {
                        model: historyPage.vm ? historyPage.vm.timeline : []
                        delegate: Pair { required property var modelData
                                         objectName: "historyEvent_" + modelData.eventType
                                         label: modelData.text + "  •  " + modelData.actor
                                         value: modelData.time }
                    }
                }

                Txt { Layout.fillWidth: true; visible: value.length > 0
                      value: detailScroll.d.notes ? "ملاحظات: " + detailScroll.d.notes : ""; color: theme.textMuted }
                Item { Layout.preferredHeight: 4 }
            }
        }
    }
}
