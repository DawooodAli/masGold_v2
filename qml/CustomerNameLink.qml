import QtQuick
import QtQuick.Controls

// Identity is supplied by the source ViewModel; the application owns navigation.
Label {
    id: customerLink
    property string customerId: ""
    signal customerRequested(string customerId)
    Theme { id: linkTheme }
    font.family: "Cairo"
    font.pixelSize: linkTheme.fontBodyStrong
    font.underline: customerId.length > 0 && (pointer.containsMouse || activeFocus)
    color: customerId.length > 0 ? linkTheme.gold : linkTheme.textPrimary
    horizontalAlignment: Text.AlignRight
    wrapMode: Text.WordWrap
    textFormat: Text.PlainText
    activeFocusOnTab: customerId.length > 0
    Keys.onReturnPressed: if (customerId.length > 0) customerRequested(customerId)
    Keys.onSpacePressed: if (customerId.length > 0) customerRequested(customerId)
    MouseArea {
        id: pointer
        anchors.fill: parent
        enabled: customerLink.customerId.length > 0
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: customerLink.customerRequested(customerLink.customerId)
    }
}
