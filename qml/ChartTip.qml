import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The hover tooltip of a chart (owner 2026-11-18): the day on top, one row per series with its colour key, its name and its value. The text arrives formatted; nothing is calculated here.
// Place it inside the chart's own parent item, set `anchorX` to the crosshair x and `shown` to the hovered state.
Rectangle {
    id: tip
    property string title: ""
    property var rows: []                  // [{ name, value, color }]
    property real anchorX: 0
    property bool shown: false
    visible: shown && rows.length > 0
    z: 6
    radius: theme.radiusSmall
    color: theme.bgTertiary
    border.color: theme.cardBorder
    width: column.implicitWidth + 20
    height: column.implicitHeight + 14
    x: Math.max(0, Math.min((parent ? parent.width : 0) - width, anchorX - width / 2))
    y: 4

    Theme { id: theme }

    ColumnLayout {
        id: column
        anchors.centerIn: parent
        spacing: 2
        Label {
            Layout.fillWidth: true
            text: tip.title
            color: theme.textMuted
            font.family: theme.fontFamily
            font.pixelSize: theme.fontCaption
            horizontalAlignment: Text.AlignRight
        }
        Repeater {
            model: tip.rows
            delegate: RowLayout {
                required property var modelData
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Rectangle { Layout.preferredWidth: 12; Layout.preferredHeight: 3; radius: 1.5; color: parent.modelData.color }
                Label { text: parent.modelData.name; color: theme.textSecondary; font.family: theme.fontFamily; font.pixelSize: theme.fontTableHeader }
                Item { Layout.fillWidth: true; Layout.minimumWidth: 8 }
                Label { text: parent.modelData.value; color: theme.textPrimary; font.bold: true; font.family: theme.fontFamily; font.pixelSize: theme.fontTableHeader }
            }
        }
    }
}
