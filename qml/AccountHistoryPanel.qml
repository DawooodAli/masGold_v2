import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// سجل حركات حساب مالي (بنك / InstaPay / محفظة): every ledger movement behind the balance, newest first,
// each with its source. All text comes formatted from AccountHistoryViewModel (`history`); references and
// customer names navigate by immutable ids only (existing Invoice History / customer details).
ColumnLayout {
    id: panel
    objectName: "accountHistory"
    property var history: null
    spacing: 8

    Theme { id: theme }

    component HistoryButton: Button {
        id: historyButton
        property color accent: theme.gold
        font.family: "Cairo"
        font.pixelSize: 12
        font.bold: true
        Layout.preferredHeight: 28
        topInset: 0
        bottomInset: 0
        leftPadding: 12
        rightPadding: 12
        contentItem: Label {
            text: historyButton.text
            font: historyButton.font
            color: historyButton.enabled ? historyButton.accent : theme.textDisabled
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: theme.radiusSmall
            color: historyButton.down ? theme.panelHover : "transparent"
            border.color: historyButton.enabled ? historyButton.accent : theme.border
        }
    }

    RowLayout {
        Layout.fillWidth: true
        layoutDirection: Qt.RightToLeft
        spacing: 10
        HistoryButton { objectName: "accountHistoryBack"; text: "رجوع ▸"; accent: theme.textSecondary
                        onClicked: panel.history.close() }
        Label { objectName: "accountHistoryTitle"; text: "حركات الحساب — " + panel.history.accountName
                color: theme.gold; font.family: "Cairo"; font.pixelSize: 15; font.bold: true }
        Label { text: "الرصيد الحالي"; color: theme.textMuted; font.family: "Cairo"; font.pixelSize: 12 }
        Label { objectName: "accountHistoryBalance"; text: panel.history.balanceText
                color: theme.goldBright; font.family: "Cairo"; font.pixelSize: 15; font.bold: true }
        Item { Layout.fillWidth: true }
        Repeater {
            model: [["", "الكل"], ["IN", "وارد"], ["OUT", "منصرف"]]
            HistoryButton {
                required property var modelData
                objectName: "accountHistoryFilter_" + (modelData[0] || "ALL")
                text: modelData[1]
                accent: panel.history.direction === modelData[0] ? theme.gold : theme.textSecondary
                onClicked: panel.history.setDirection(modelData[0])
            }
        }
    }

    Label {
        objectName: "accountHistoryMessage"
        Layout.fillWidth: true
        visible: text.length > 0
        text: panel.history.errorMessage || (panel.history.busy ? "جارٍ التحميل..."
              : panel.history.empty ? "لا توجد حركات لهذا الحساب" : "")
        color: panel.history.errorMessage ? theme.red : theme.textMuted
        font.family: "Cairo"
        font.pixelSize: 12
        horizontalAlignment: Text.AlignRight
    }

    ListView {
        id: historyList
        objectName: "accountHistoryList"
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        spacing: 3
        model: panel.history.rows
        ScrollBar.vertical: ScrollBar {}
        delegate: Rectangle {
            id: moveRow
            required property var modelData
            required property int index
            objectName: "accountMovement_" + index
            width: historyList.width
            height: 48
            radius: 4
            color: theme.bgSecondary
            Rectangle { anchors.right: parent.right; width: 3; height: parent.height; radius: 2
                        color: moveRow.modelData.incoming ? theme.green : theme.accountPayable }
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 12
                layoutDirection: Qt.RightToLeft
                spacing: 10
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Label { text: moveRow.modelData.label; color: theme.textPrimary; font.family: "Cairo"
                                font.pixelSize: 13; font.bold: true }
                        Label {
                            objectName: "accountMovementRef_" + moveRow.index
                            visible: text.length > 0
                            text: moveRow.modelData.reference
                            color: moveRow.modelData.invoiceId.length > 0 ? theme.gold : theme.textSecondary
                            font.family: "Cairo"
                            font.pixelSize: 12
                            font.underline: moveRow.modelData.invoiceId.length > 0 && refArea.containsMouse
                            MouseArea {
                                id: refArea
                                objectName: "accountMovementRefArea_" + moveRow.index
                                anchors.fill: parent
                                hoverEnabled: true
                                enabled: moveRow.modelData.invoiceId.length > 0
                                cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: panel.history.openInvoice(moveRow.modelData.invoiceId)
                            }
                        }
                        Label {
                            objectName: "accountMovementCustomer_" + moveRow.index
                            visible: text.length > 0
                            Layout.fillWidth: true
                            text: moveRow.modelData.customer
                            color: theme.textMuted
                            font.family: "Cairo"
                            font.pixelSize: 12
                            elide: Text.ElideLeft
                            horizontalAlignment: Text.AlignRight
                            font.underline: moveRow.modelData.customerId.length > 0 && customerArea.containsMouse
                            MouseArea {
                                id: customerArea
                                anchors.fill: parent
                                hoverEnabled: true
                                enabled: moveRow.modelData.customerId.length > 0
                                cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: panel.history.openCustomer(moveRow.modelData.customerId)
                            }
                        }
                        Item { Layout.fillWidth: true; visible: moveRow.modelData.customer.length === 0 }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: [moveRow.modelData.time, moveRow.modelData.actor,
                               "الرصيد بعد الحركة " + moveRow.modelData.balanceAfter]
                              .filter(function(v) { return v && v.length > 0 }).join("  •  ")
                        color: theme.textMuted
                        font.family: "Cairo"
                        font.pixelSize: 11
                        horizontalAlignment: Text.AlignRight
                        elide: Text.ElideLeft
                    }
                }
                Label { text: moveRow.modelData.directionText
                        color: moveRow.modelData.incoming ? theme.green : theme.accountPayable
                        font.family: "Cairo"; font.pixelSize: 11 }
                Label {
                    objectName: "accountMovementAmount_" + moveRow.index
                    Layout.minimumWidth: 100
                    text: moveRow.modelData.amount
                    color: moveRow.modelData.incoming ? theme.green : theme.accountPayable
                    font.family: "Cairo"
                    font.pixelSize: 14
                    font.bold: true
                    horizontalAlignment: Text.AlignRight
                }
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        layoutDirection: Qt.RightToLeft
        spacing: 8
        HistoryButton { objectName: "accountHistoryNewer"; text: "الأحدث"; visible: panel.history.page > 1
                        onClicked: panel.history.previousPage() }
        Label { text: "صفحة " + panel.history.page; color: theme.textMuted; font.family: "Cairo"; font.pixelSize: 12 }
        HistoryButton { objectName: "accountHistoryOlder"; text: "الأقدم"; visible: panel.history.hasMore
                        onClicked: panel.history.nextPage() }
        Item { Layout.fillWidth: true }
    }
}
