import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Billing section: the new-invoice page and the read-only invoice history.
Item {
    id: workspace
    objectName: "billingWorkspace"

    property string pageTitle: ""
    property int currentTab: 0
    // استبدال (opened from an invoice in History) covers both tabs while it is open.
    readonly property bool exchangeOpen: typeof exchangeVM !== "undefined" && !!exchangeVM && exchangeVM.isOpen
    // مرتجع (opened from an invoice in History) covers both tabs the same way.
    readonly property bool returnOpen: typeof returnVM !== "undefined" && !!returnVM && returnVM.isOpen
    // تصحيح / عكس (managers, from an invoice in History) the same way.
    readonly property bool correctionOpen: typeof correctionVM !== "undefined" && !!correctionVM && correctionVM.isOpen

    Theme { id: theme }

    Rectangle {
        anchors.fill: parent
        color: theme.bgPrimary
    }

    ExchangeWorkspace {
        anchors.fill: parent
        visible: workspace.exchangeOpen
        z: 1
    }

    ReturnWorkspace {
        anchors.fill: parent
        visible: workspace.returnOpen && !workspace.exchangeOpen
        z: 1
    }

    CorrectionWorkspace {
        anchors.fill: parent
        visible: workspace.correctionOpen && !workspace.exchangeOpen && !workspace.returnOpen
        z: 1
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        visible: !workspace.exchangeOpen && !workspace.returnOpen && !workspace.correctionOpen

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 10
            Layout.rightMargin: 18
            Layout.leftMargin: 18
            layoutDirection: Qt.RightToLeft
            spacing: 6

            TabButtonItem { objectName: "billingTabNew"; text: "فاتورة جديدة"; index: 0 }
            TabButtonItem { objectName: "billingTabHistory"; text: "سجل الفواتير"; index: 1 }
            Item { Layout.fillWidth: true }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: workspace.currentTab

            InvoicePage { pageTitle: workspace.pageTitle }
            InvoiceHistoryPage { pageTitle: "🧾 سجل الفواتير" }
        }
    }

    component TabButtonItem: Button {
        id: tab
        property int index: 0
        readonly property bool selected: workspace.currentTab === index
        font.family: "Cairo"
        font.bold: selected
        onClicked: workspace.currentTab = index
        contentItem: Label {
            text: tab.text
            font: tab.font
            color: tab.selected ? theme.gold : theme.textSecondary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            implicitWidth: 120
            implicitHeight: 34
            radius: theme.radiusSmall
            color: tab.selected ? theme.bgTertiary : "transparent"
            border.color: tab.selected ? theme.goldDark : theme.border
            border.width: 1
        }
    }
}
