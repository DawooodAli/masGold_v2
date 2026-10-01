import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// بيان المخزون: what physical stock the shop has now, by karat → item type (count, net weight).
// Read only. Every figure arrives formatted from StockBalanceViewModel (exact Decimal, aggregated in
// MySQL with the canonical current-stock predicate). No calculation, no action on stock here.
Item {
    id: page
    objectName: "stockBalancePage"

    signal backRequested()

    readonly property var vm: typeof stockBalanceVM !== "undefined" ? stockBalanceVM : null

    Theme { id: theme }

    onVisibleChanged: if (visible && vm) vm.refresh()

    Rectangle { anchors.fill: parent; color: theme.bgPrimary }

    component Txt: Label {
        font.family: theme.fontFamily
        font.pixelSize: theme.fontBody
        color: theme.textSecondary
        horizontalAlignment: Text.AlignRight
        elide: Text.ElideLeft
        textFormat: Text.PlainText
    }

    component FilterButton: Button {
        property string code: ""
        objectName: "stockBalanceFilter_" + code
        checkable: true
        checked: !!page.vm && page.vm.filter === code
        font.family: theme.fontFamily
        onClicked: if (page.vm) page.vm.setFilter(code)
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 22
        spacing: 12

        // ------------------------------------------------ header
        RowLayout {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            spacing: 8

            ColumnLayout {
                spacing: 2
                Txt { text: "📊 بيان المخزون"; color: theme.gold; font.pixelSize: 20; font.bold: true }
                Txt { text: "القطع الموجودة فعلياً بالمخزون حسب العيار والصنف — للعرض فقط"; color: theme.textMuted
                      font.pixelSize: 12 }
            }
            Item { Layout.fillWidth: true }
            Button { text: "↻  تحديث"; font.family: theme.fontFamily; onClicked: if (page.vm) page.vm.refresh() }
            Button { objectName: "stockBalanceBack"; text: "←  العودة للمخزون"; font.family: theme.fontFamily
                     onClicked: page.backRequested() }
        }

        // ------------------------------------------------ filter + overall totals
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: totalsRow.implicitHeight + 20
            color: theme.bgSecondary
            radius: theme.radiusSmall
            border.color: theme.border
            border.width: 1

            RowLayout {
                id: totalsRow
                anchors.fill: parent
                anchors.margins: 10
                layoutDirection: Qt.RightToLeft
                spacing: 8

                FilterButton { code: "ALL"; text: "الكل" }
                FilterButton { code: "NEW_STOCK"; text: "بضاعة جديدة" }
                FilterButton { code: "OLD_STOCK"; text: "بضاعة قديمة" }
                Item { Layout.fillWidth: true }
                Txt {
                    objectName: "stockBalanceTotalCount"
                    text: "إجمالي القطع: " + (page.vm ? page.vm.totalCount : 0)
                    color: theme.textPrimary; font.bold: true
                }
                Txt {
                    objectName: "stockBalanceTotalWeight"
                    text: "إجمالي الوزن: " + (page.vm ? page.vm.totalWeightText : "0.000") + " جرام"
                    color: theme.gold; font.bold: true
                }
            }
        }

        Txt {
            visible: !!page.vm && page.vm.errorMessage.length > 0
            Layout.fillWidth: true
            text: page.vm ? page.vm.errorMessage : ""
            color: theme.red
            wrapMode: Text.WordWrap
        }

        // ------------------------------------------------ karat panels (wrap / stack when narrow)
        ScrollView {
            id: scroller
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true

            Flow {
                id: panels
                width: scroller.availableWidth
                spacing: 12
                layoutDirection: Qt.RightToLeft

                readonly property int columns: width >= 1100 ? 3 : (width >= 700 ? 2 : 1)
                readonly property real panelWidth: (width - spacing * (columns - 1)) / columns

                Repeater {
                    model: page.vm ? page.vm.karats : []

                    delegate: Rectangle {
                        id: panel
                        required property var modelData
                        objectName: "stockBalanceKarat_" + modelData.karat_id
                        width: panels.panelWidth
                        height: panelBody.implicitHeight + 20
                        color: theme.panel
                        radius: theme.radiusSmall
                        border.color: theme.border
                        border.width: 1

                        ColumnLayout {
                            id: panelBody
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 10
                            spacing: 4

                            Txt { Layout.fillWidth: true; text: panel.modelData.title; color: theme.gold
                                  font.pixelSize: 16; font.bold: true }

                            RowLayout {           // الصنف | الوزن الصافي | العدد
                                Layout.fillWidth: true
                                layoutDirection: Qt.RightToLeft
                                Txt { Layout.fillWidth: true; text: "الصنف"; color: theme.textMuted }
                                Txt { Layout.preferredWidth: 110; text: "الوزن الصافي"; color: theme.textMuted
                                      horizontalAlignment: Text.AlignLeft }
                                Txt { Layout.preferredWidth: 56; text: "العدد"; color: theme.textMuted
                                      horizontalAlignment: Text.AlignLeft }
                            }
                            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: theme.border }

                            Repeater {
                                model: panel.modelData.rows
                                delegate: RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    layoutDirection: Qt.RightToLeft
                                    Txt { Layout.fillWidth: true; text: modelData.name; color: theme.textPrimary }
                                    Txt { Layout.preferredWidth: 110; text: modelData.weight_text
                                          horizontalAlignment: Text.AlignLeft }
                                    Txt { Layout.preferredWidth: 56; text: String(modelData.count)
                                          horizontalAlignment: Text.AlignLeft }
                                }
                            }

                            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: theme.border }
                            RowLayout {
                                Layout.fillWidth: true
                                layoutDirection: Qt.RightToLeft
                                Txt { Layout.fillWidth: true; text: "الإجمالي"; color: theme.textPrimary; font.bold: true }
                                Txt { objectName: "stockBalanceKaratWeight"; Layout.preferredWidth: 110
                                      text: panel.modelData.weight_text + " جرام"; color: theme.gold; font.bold: true
                                      horizontalAlignment: Text.AlignLeft }
                                Txt { objectName: "stockBalanceKaratCount"; Layout.preferredWidth: 56
                                      text: String(panel.modelData.count); color: theme.gold; font.bold: true
                                      horizontalAlignment: Text.AlignLeft }
                            }
                        }
                    }
                }
            }
        }

        Txt {
            objectName: "stockBalanceEmpty"
            visible: !!page.vm && page.vm.isEmpty
            Layout.alignment: Qt.AlignHCenter
            text: "لا توجد قطع بالمخزون حالياً"
            color: theme.textMuted
            font.pixelSize: 14
        }
    }
}
