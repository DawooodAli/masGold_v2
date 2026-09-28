import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A date picker for filters: a box showing the full date (2026-09-28) that opens a month
// calendar. `value` is "yyyy-MM-dd" or "" (no date). Picking a day only sets the value; the page
// applies it (تطبيق) — nothing is queried from here.
Rectangle {
    id: field
    property string value: ""
    property string placeholder: ""
    property date shownMonth: new Date()
    signal picked(string value)

    implicitHeight: 30
    implicitWidth: 138
    height: 30
    radius: 4
    color: theme.bgPrimary
    border.color: calendar.visible || area.containsMouse ? theme.gold : theme.border

    Theme { id: theme }

    function parse(text) {
        var parts = (text || "").split("-")
        return parts.length === 3 ? new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2])) : new Date()
    }
    function two(n) { return (n < 10 ? "0" : "") + n }
    function format(d) { return d.getFullYear() + "-" + two(d.getMonth() + 1) + "-" + two(d.getDate()) }
    function setValue(text) { field.value = text; field.picked(text) }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 8; anchors.rightMargin: 8
        layoutDirection: Qt.RightToLeft
        spacing: 6
        Label { text: field.placeholder; visible: text.length > 0; font.family: theme.fontFamily
                font.pixelSize: theme.fontCaption; color: theme.textMuted }
        // The full date, left-to-right, never clipped.
        Label { Layout.fillWidth: true
                text: field.value.length ? "⁦" + field.value + "⁩" : "اختر التاريخ"
                font.family: theme.fontFamily; font.pixelSize: theme.fontBody
                color: field.value.length ? theme.textPrimary : theme.textDisabled
                horizontalAlignment: Text.AlignRight; verticalAlignment: Text.AlignVCenter }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: { field.shownMonth = field.parse(field.value); calendar.open() }
    }

    Popup {
        id: calendar
        objectName: field.objectName + "Calendar"
        y: field.height + 4
        x: field.width - width
        width: 270
        padding: 8
        focus: true
        background: Rectangle { color: theme.panel; radius: theme.radiusSmall; border.color: theme.goldDark }

        contentItem: ColumnLayout {
            spacing: 4
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                Button { objectName: field.objectName + "Previous"; text: "السابق"; flat: true; font.family: theme.fontFamily
                         onClicked: field.shownMonth = new Date(field.shownMonth.getFullYear(), field.shownMonth.getMonth() - 1, 1) }
                Label { Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; color: theme.gold; font.bold: true
                        font.family: theme.fontFamily
                        text: "⁦" + field.shownMonth.getFullYear() + "-" + field.two(field.shownMonth.getMonth() + 1) + "⁩" }
                Button { objectName: field.objectName + "Next"; text: "التالي"; flat: true; font.family: theme.fontFamily
                         onClicked: field.shownMonth = new Date(field.shownMonth.getFullYear(), field.shownMonth.getMonth() + 1, 1) }
            }
            DayOfWeekRow {
                Layout.fillWidth: true
                locale: grid.locale
                delegate: Label { text: shortName; horizontalAlignment: Text.AlignHCenter
                                  font.family: theme.fontFamily; font.pixelSize: theme.fontCaption; color: theme.textMuted }
            }
            MonthGrid {
                id: grid
                objectName: field.objectName + "Grid"
                Layout.fillWidth: true
                month: field.shownMonth.getMonth()
                year: field.shownMonth.getFullYear()
                locale: Qt.locale("ar_EG")
                delegate: Rectangle {
                    implicitWidth: 34
                    implicitHeight: 26
                    radius: 4
                    color: field.value === field.format(model.date) ? theme.goldDark
                         : (model.today ? theme.bgTertiary : "transparent")
                    opacity: model.month === grid.month ? 1 : 0.35
                    Label { anchors.centerIn: parent; text: model.day; font.family: theme.fontFamily
                            font.pixelSize: theme.fontBody; color: theme.textPrimary }
                }
                onClicked: function(date) { field.setValue(field.format(date)); calendar.close() }
            }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                Button { objectName: field.objectName + "Today"; text: "اليوم"; flat: true; font.family: theme.fontFamily
                         onClicked: { field.setValue(field.format(new Date())); calendar.close() } }
                Item { Layout.fillWidth: true }
                Button { objectName: field.objectName + "Clear"; text: "بدون تاريخ"; flat: true; font.family: theme.fontFamily
                         onClicked: { field.setValue(""); calendar.close() } }
            }
        }
    }
}
