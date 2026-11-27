import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Visual pass (2026-11-27): the shared look of an empty list. A faint ring with a dash, a title and an optional hint, centred in its container, so an empty
// table reads as "nothing here yet" and not as a layout that failed to load. Display only: no data, no logic.
ColumnLayout {
    id: emptyState
    property string title: ""
    property string hint: ""

    Theme { id: theme }

    spacing: 6

    Rectangle {
        Layout.alignment: Qt.AlignHCenter
        Layout.preferredWidth: 38
        Layout.preferredHeight: 38
        radius: width / 2
        color: "transparent"
        border.color: theme.border
        border.width: 1
        Rectangle {
            anchors.centerIn: parent
            width: 14
            height: 2
            radius: 1
            color: theme.textDisabled
        }
    }
    Label {
        Layout.fillWidth: true
        visible: emptyState.title.length > 0
        text: emptyState.title
        color: theme.textSecondary
        font.family: "Cairo"
        font.pixelSize: theme.fontBodyStrong
        font.bold: true
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
    }
    Label {
        Layout.fillWidth: true
        visible: emptyState.hint.length > 0
        text: emptyState.hint
        color: theme.textMuted
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
    }
}
