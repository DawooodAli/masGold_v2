import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import MasGold.Reports 1.0

// التقارير الأساسية: المبيعات والمرتجعات، المخزون المتاح، حركة المخزون والذهب، العملاء، الموردون، الخزنة والبنوك.
// Read only. Every figure arrives formatted from ReportsViewModel (exact Decimal computed by ReportService over the WHOLE filtered result);
// QML renders, filters, pages and asks the main window to open the source document. It never sums or classifies anything.
Item {
    id: page
    objectName: "reportsPage"

    property string pageTitle: ""
    readonly property alias vm: reportsVM
    readonly property bool canView: reportsVM.canView
    signal invoiceRequested(string invoiceId)
    signal customerRequested(string customerId)
    signal tripRequested(string tripId)

    Theme { id: theme }

    ReportsViewModel {
        id: reportsVM
        objectName: "reportsVM"
        session: typeof appSession !== "undefined" ? appSession : null
        onOpenRequested: function (kind, identifier, extra) {
            if (kind === "INVOICE") page.invoiceRequested(identifier)
            else if (kind === "CUSTOMER") page.customerRequested(identifier)
            else if (kind === "TRIP") page.tripRequested(identifier)
        }
    }

    onVisibleChanged: if (visible && reportsVM.canView && reportsVM.report === "") reportsVM.select(reportsVM.reports[0].key)

    function ltr(text) {
        return text === undefined || text === null || text === "" ? "" : "⁦" + text + "⁩"
    }

    Rectangle { anchors.fill: parent; color: theme.bgPrimary }

    component Txt: Label {
        color: theme.textPrimary
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        horizontalAlignment: Text.AlignRight
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.WordWrap
        textFormat: Text.PlainText
    }

    component Btn: Button {
        id: btn
        property bool primary: false
        property color accent: theme.gold
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        implicitHeight: 32
        topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0
        topPadding: 0; bottomPadding: 0; leftPadding: 14; rightPadding: 14
        hoverEnabled: true
        contentItem: Label {
            text: btn.text; font: btn.font
            color: !btn.enabled ? theme.textDisabled : btn.primary ? theme.bgPrimary : btn.accent
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: theme.radiusSmall
            color: !btn.enabled ? "transparent" : btn.primary ? (btn.down || btn.hovered ? Qt.lighter(btn.accent, 1.12) : btn.accent) : btn.down ? theme.panelHover : btn.hovered ? theme.bgTertiary : "transparent"
            border.color: btn.enabled ? btn.accent : theme.border
        }
    }

    component Field: TextField {
        id: field
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        color: theme.textPrimary
        placeholderTextColor: theme.textDisabled
        horizontalAlignment: TextInput.AlignRight
        selectByMouse: true
        implicitHeight: 32
        background: Rectangle { radius: 4; color: theme.bgPrimary; border.color: field.activeFocus ? theme.gold : theme.border }
    }

    component Combo: ComboBox {
        id: combo
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        implicitHeight: 32
        delegate: ItemDelegate {
            width: combo.width
            height: 32
            contentItem: Label { text: modelData[combo.textRole]; font.family: "Cairo"; font.pixelSize: theme.fontBody; color: theme.textPrimary
                                 horizontalAlignment: Text.AlignRight; verticalAlignment: Text.AlignVCenter; elide: Text.ElideLeft }
        }
        indicator: Label { x: 10; y: (combo.height - height) / 2; text: "▾"; color: theme.gold }
        contentItem: Label { text: combo.displayText; font: combo.font; color: theme.textPrimary; horizontalAlignment: Text.AlignRight
                             verticalAlignment: Text.AlignVCenter; rightPadding: 10; leftPadding: 28; elide: Text.ElideLeft }
        background: Rectangle { radius: 4; color: theme.bgPrimary; border.color: combo.activeFocus || combo.popup.visible ? theme.gold : theme.border }
    }

    Flickable {
        id: scroller
        objectName: "reportsScroller"
        anchors.fill: parent
        anchors.margins: 18
        contentWidth: width
        contentHeight: body.implicitHeight + 24
        clip: true
        ScrollBar.vertical: ScrollBar {}

        ColumnLayout {
            id: body
            width: scroller.width - 14
            spacing: 10

            // ---------------------------------------------------------------- header + report choice
            Txt { Layout.fillWidth: true; text: "📊 التقارير"; color: theme.gold; font.bold: true; font.pixelSize: 20 }
            Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                  text: "للعرض فقط — الأرقام من دفاتر البرنامج وكل إجمالي يشمل كامل نتائج الفلتر وليس الصفحة المعروضة فقط." }
            Flow {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 6
                Repeater {
                    model: reportsVM.reports
                    delegate: Btn {
                        required property var modelData
                        objectName: "reportChoice_" + modelData.key
                        text: modelData.title
                        primary: reportsVM.report === modelData.key
                        onClicked: reportsVM.select(modelData.key)
                    }
                }
            }
            Txt { objectName: "reportsNoAccess"; Layout.fillWidth: true; visible: !reportsVM.canView; color: theme.amber; text: "ليس لديك صلاحية عرض أي تقرير." }

            // ---------------------------------------------------------------- filters
            Rectangle {
                Layout.fillWidth: true
                visible: reportsVM.report !== ""
                implicitHeight: filterFlow.implicitHeight + 16
                radius: theme.radiusSmall; color: theme.bgSecondary; border.color: theme.border
                Flow {
                    id: filterFlow
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 8
                    layoutDirection: Qt.RightToLeft
                    spacing: 8
                    Txt { text: "من"; height: 32 }
                    DateField { objectName: "reportFrom"; value: reportsVM.filters.from || ""; onPicked: function (v) { reportsVM.setFilter("from", v) } }
                    Txt { text: "إلى"; height: 32 }
                    DateField { objectName: "reportTo"; value: reportsVM.filters.to || ""; onPicked: function (v) { reportsVM.setFilter("to", v) } }
                    Field { objectName: "reportText"; width: 190; placeholderText: "بحث (رقم، اسم، باركود…)"; text: reportsVM.filters.text || ""
                            onTextEdited: reportsVM.setFilter("text", text); onAccepted: reportsVM.apply() }
                    Repeater {
                        model: reportsVM.filterSpecs
                        delegate: Row {
                            id: specRow
                            required property var modelData
                            layoutDirection: Qt.RightToLeft
                            spacing: 4
                            Txt { text: specRow.modelData.label; height: 32 }
                            Combo {
                                objectName: "reportFilter_" + specRow.modelData.key
                                width: 170
                                model: specRow.modelData.options
                                textRole: "text"
                                currentIndex: {
                                    var current = reportsVM.filters[specRow.modelData.key]
                                    for (var i = 0; i < specRow.modelData.options.length; ++i) if (specRow.modelData.options[i].value === current) return i
                                    return 0
                                }
                                onActivated: function (i) { reportsVM.setFilter(specRow.modelData.key, specRow.modelData.options[i].value) }
                            }
                        }
                    }
                    Btn { objectName: "reportApply"; text: "عرض"; primary: true; enabled: !reportsVM.busy; onClicked: reportsVM.apply() }
                    Btn { objectName: "reportExport"; text: "تصدير CSV"; enabled: !reportsVM.busy && !!reportsVM.result.tables; onClicked: reportsVM.exportCsv() }
                }
            }

            Txt { objectName: "reportApplied"; Layout.fillWidth: true; visible: !!reportsVM.result.applied && reportsVM.result.applied.length > 0; color: theme.textMuted
                  font.pixelSize: theme.fontTableHeader; text: "الفلاتر المطبقة: " + (reportsVM.result.applied || []).join(" • ") }

            // ---------------------------------------------------------------- states
            Txt { objectName: "reportLoading"; Layout.fillWidth: true; visible: reportsVM.busy; color: theme.textMuted; text: "جارٍ التحميل…" }
            Txt { objectName: "reportError"; Layout.fillWidth: true; visible: reportsVM.errorMessage !== ""; color: theme.red; text: reportsVM.errorMessage }
            Txt { objectName: "reportMessage"; Layout.fillWidth: true; visible: reportsVM.message !== ""; color: theme.green; text: reportsVM.message }

            // ---------------------------------------------------------------- meaning + totals
            Rectangle {
                Layout.fillWidth: true
                visible: !!reportsVM.result.meaning && reportsVM.result.meaning.length > 0
                implicitHeight: meaningColumn.implicitHeight + 14
                radius: theme.radiusSmall; color: theme.bgTertiary; border.color: theme.border
                ColumnLayout {
                    id: meaningColumn
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 8
                    spacing: 2
                    Txt { text: "معنى الأرقام"; color: theme.gold; font.bold: true }
                    Repeater {
                        model: reportsVM.result.meaning || []
                        delegate: Txt { objectName: "reportMeaning_" + index; Layout.fillWidth: true; required property var modelData; required property int index; text: "• " + modelData
                                        color: theme.textSecondary; font.pixelSize: theme.fontTableHeader }
                    }
                }
            }
            Flow {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 6
                Repeater {
                    model: reportsVM.result.totals || []
                    delegate: Rectangle {
                        id: totalCard
                        required property var modelData
                        required property int index
                        objectName: "reportTotal_" + index
                        width: Math.min(260, body.width)
                        implicitHeight: totalColumn.implicitHeight + 12
                        radius: theme.radiusSmall; color: theme.panel; border.color: theme.border
                        ColumnLayout {
                            id: totalColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 8; anchors.rightMargin: 8
                            spacing: 1
                            Txt { Layout.fillWidth: true; text: totalCard.modelData.label; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                            Txt { objectName: "reportTotalValue_" + totalCard.index; Layout.fillWidth: true; text: page.ltr(totalCard.modelData.value); color: theme.gold; font.bold: true }
                            Txt { Layout.fillWidth: true; visible: totalCard.modelData.note !== ""; text: totalCard.modelData.note; color: theme.textMuted; font.pixelSize: 11 }
                        }
                    }
                }
            }

            // ---------------------------------------------------------------- tables
            Repeater {
                model: reportsVM.result.tables || []
                delegate: ColumnLayout {
                    id: tableBlock
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    spacing: 3
                    Txt { Layout.fillWidth: true; text: tableBlock.modelData.title + (tableBlock.modelData.paged ? "  (" + tableBlock.modelData.rowCount + ")" : ""); color: theme.gold; font.bold: true }
                    Txt { objectName: "reportTableUnavailable_" + tableBlock.index; Layout.fillWidth: true; visible: !tableBlock.modelData.available; color: theme.amber
                          text: "غير متاح: " + tableBlock.modelData.reason }
                    Txt { objectName: "reportTableEmpty_" + tableBlock.index; Layout.fillWidth: true; visible: tableBlock.modelData.available && tableBlock.modelData.rows.length === 0
                          color: theme.textMuted; text: "لا توجد بيانات مطابقة." }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: tableBlock.modelData.available && tableBlock.modelData.rows.length > 0
                        layoutDirection: Qt.RightToLeft
                        spacing: 4
                        Repeater {
                            model: tableBlock.modelData.columns
                            delegate: Txt { required property var modelData; Layout.fillWidth: true; Layout.preferredWidth: 1; text: modelData.title; color: theme.textMuted
                                            font.pixelSize: theme.fontTableHeader; font.bold: true; horizontalAlignment: Text.AlignRight }
                        }
                        Item { Layout.preferredWidth: 78 }
                    }
                    Repeater {
                        model: tableBlock.modelData.rows
                        delegate: Rectangle {
                            id: dataRow
                            required property var modelData
                            required property int index
                            objectName: "reportRow_" + tableBlock.index + "_" + index
                            Layout.fillWidth: true
                            implicitHeight: rowLine.implicitHeight + 10
                            radius: 3; color: index % 2 ? theme.bgSecondary : theme.panel
                            opacity: modelData.dim ? 0.6 : 1
                            RowLayout {
                                id: rowLine
                                anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 6; anchors.rightMargin: 6
                                layoutDirection: Qt.RightToLeft
                                spacing: 4
                                Repeater {
                                    model: dataRow.modelData.cells
                                    delegate: Txt {
                                        required property string modelData
                                        required property int index
                                        Layout.fillWidth: true; Layout.preferredWidth: 1
                                        text: tableBlock.modelData.columns[index].kind === "text" ? modelData : page.ltr(modelData)
                                        font.pixelSize: theme.fontTableHeader
                                        horizontalAlignment: tableBlock.modelData.columns[index].kind === "text" ? Text.AlignRight : Text.AlignLeft
                                    }
                                }
                                Btn {
                                    objectName: "reportOpen_" + tableBlock.index + "_" + dataRow.index
                                    Layout.preferredWidth: 78
                                    visible: !!dataRow.modelData.ref.type
                                    text: dataRow.modelData.refText || "فتح"
                                    accent: theme.textSecondary
                                    implicitHeight: 26
                                    onClicked: reportsVM.open(dataRow.modelData.ref.type, dataRow.modelData.ref.id, "")
                                }
                                Item { Layout.preferredWidth: dataRow.modelData.ref.type ? 0 : 78 }
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: tableBlock.modelData.paged && (tableBlock.modelData.page > 1 || tableBlock.modelData.hasMore)
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Btn { objectName: "reportNext"; text: "التالي ←"; enabled: tableBlock.modelData.hasMore && !reportsVM.busy; onClicked: reportsVM.run(tableBlock.modelData.page + 1) }
                        Txt { objectName: "reportPageLabel"; text: "صفحة " + tableBlock.modelData.page + " من " + Math.max(1, Math.ceil(tableBlock.modelData.rowCount / tableBlock.modelData.pageSize)) }
                        Btn { objectName: "reportPrevious"; text: "→ السابق"; enabled: tableBlock.modelData.page > 1 && !reportsVM.busy; onClicked: reportsVM.run(tableBlock.modelData.page - 1) }
                    }
                }
            }
        }
    }
}
