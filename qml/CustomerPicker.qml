import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import MasGold.Customers 1.0

Control {
    id: picker
    objectName: "customerPicker"

    property bool allowInactive: false
    property bool clearable: true
    property string placeholderText: "اختيار عميل"
    readonly property string selectedCustomerId: pickerVM.selectedCustomerId
    readonly property var selectedCustomer: pickerVM.selectedCustomer
    readonly property bool hasSelection: pickerVM.hasSelection
    readonly property bool busy: pickerVM.busy
    readonly property string errorMessage: pickerVM.errorMessage
    property bool componentReady: false

    signal customerSelected(string customerId)
    signal selectionCleared()
    signal selectionInvalidated(string customerId, string reason)

    implicitWidth: 430
    implicitHeight: contentItem.implicitHeight
    font.family: "Cairo"

    Theme { id: pickerTheme }
    // The picker belongs to the Customers page: its service re-checks CUSTOMERS_VIEW with this session.
    CustomerPickerViewModel { id: pickerVM; session: typeof appSession !== "undefined" ? appSession : null }

    onAllowInactiveChanged: {
        if (componentReady)
            pickerVM.setAllowInactive(allowInactive)
    }
    Component.onCompleted: {
        componentReady = true
        pickerVM.setAllowInactive(allowInactive)
    }

    Connections {
        target: pickerVM
        function onSelectionInvalidated(customerId, reason) {
            picker.selectionInvalidated(customerId, reason)
        }
    }

    function open() {
        if (!enabled || busy)
            return
        pickerVM.setAllowInactive(allowInactive)
        searchField.text = pickerVM.searchText
        resultsList.currentIndex = -1
        resultsPopup.open()
        pickerVM.refresh()
        searchField.forceActiveFocus()
    }

    function close() {
        resultsPopup.close()
    }

    function clear() {
        if (busy)
            return
        var hadSelection = hasSelection
        pickerVM.clearSelection()
        if (hadSelection)
            selectionCleared()
    }

    function setCustomerId(customerId) {
        if (!customerId) {
            clear()
            return !hasSelection
        }
        if (pickerVM.selectCustomer(String(customerId))) {
            close()
            customerSelected(selectedCustomerId)
            return true
        }
        return false
    }

    function runSearch() {
        resultsList.currentIndex = -1
        pickerVM.search(searchField.text)
    }

    function chooseHighlighted() {
        if (resultsList.currentIndex >= 0 && resultsList.currentIndex < pickerVM.results.length)
            setCustomerId(pickerVM.results[resultsList.currentIndex].customer_id)
    }

    component PickerLabel: Label {
        property string value: ""
        text: "\u200f" + value
        textFormat: Text.PlainText
        font.family: "Cairo"
        font.pixelSize: 12
        color: pickerTheme.textPrimary
        horizontalAlignment: Text.AlignRight
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.Wrap
    }

    component PickerButton: Button {
        id: button
        font.family: "Cairo"
        implicitHeight: 38
        leftPadding: 10
        rightPadding: 10
        contentItem: PickerLabel {
            value: button.text
            color: button.enabled ? pickerTheme.textPrimary : pickerTheme.textDisabled
        }
        background: Rectangle {
            radius: pickerTheme.radiusSmall
            color: button.down ? pickerTheme.goldDark : button.hovered ? pickerTheme.panelHover : pickerTheme.bgTertiary
            border.color: button.activeFocus ? pickerTheme.gold : pickerTheme.border
        }
    }

    contentItem: ColumnLayout {
        spacing: 4
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            PickerButton {
                visible: picker.clearable && picker.hasSelection
                text: "مسح"
                enabled: !picker.busy
                onClicked: picker.clear()
            }
            PickerButton {
                objectName: "customerPickerOpenButton"
                Layout.fillWidth: true
                implicitHeight: picker.hasSelection ? 48 : 40
                topPadding: 2
                bottomPadding: 2
                enabled: !picker.busy
                onClicked: picker.open()
                contentItem: ColumnLayout {
                    spacing: 2
                    PickerLabel {
                        Layout.fillWidth: true
                        value: picker.hasSelection
                               ? (picker.selectedCustomer.customer_code || "—") + " | " + (picker.selectedCustomer.name || "")
                               : picker.placeholderText
                        font.bold: picker.hasSelection
                        wrapMode: Text.NoWrap
                        elide: Text.ElideLeft
                    }
                    PickerLabel {
                        Layout.fillWidth: true
                        visible: picker.hasSelection
                        value: (picker.selectedCustomer.balance_text || "—") + " — " + (picker.selectedCustomer.balance_meaning || "")
                        font.pixelSize: 10
                        color: pickerTheme.gold
                        wrapMode: Text.NoWrap
                        elide: Text.ElideLeft
                    }
                }
            }
        }
        PickerLabel {
            Layout.fillWidth: true
            visible: picker.errorMessage.length > 0 && !resultsPopup.opened
            value: picker.errorMessage
            color: pickerTheme.red
            font.pixelSize: 10
        }
    }

    Popup {
        id: resultsPopup
        objectName: "customerPickerPopup"
        parent: Overlay.overlay
        width: Math.min(Math.max(picker.width, 430), parent ? parent.width - 24 : 430)
        height: Math.min(530, parent ? parent.height - 24 : 530)
        // Placed each time it opens: mapToItem() is not re-evaluated when the page
        // layout moves the picker, so a declarative binding could go off-screen.
        function place() {
            if (!parent)
                return
            var origin = picker.mapToItem(parent, 0, 0)
            x = Math.max(12, Math.min(origin.x, parent.width - width - 12))
            var below = origin.y + picker.height + 4
            var above = origin.y - height - 4
            y = below + height <= parent.height - 12 ? below
              : above >= 12 ? above
              : Math.max(12, parent.height - height - 12)
        }
        onAboutToShow: place()
        padding: 12
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle {
            color: pickerTheme.bgSecondary
            radius: pickerTheme.radiusSmall
            border.color: pickerTheme.goldDark
        }
        contentItem: ColumnLayout {
            spacing: 8
            RowLayout {
                Layout.fillWidth: true
                PickerButton {
                    text: "إغلاق"
                    onClicked: picker.close()
                }
                PickerLabel {
                    Layout.fillWidth: true
                    value: picker.allowInactive ? "اختيار عميل — يشمل غير النشطين" : "اختيار عميل نشط"
                    color: pickerTheme.gold
                    font.bold: true
                }
            }
            RowLayout {
                Layout.fillWidth: true
                PickerButton {
                    text: "بحث"
                    enabled: !picker.busy
                    onClicked: picker.runSearch()
                }
                TextField {
                    id: searchField
                    objectName: "customerPickerSearchField"
                    Layout.fillWidth: true
                    implicitHeight: 40
                    maximumLength: 255
                    placeholderText: "الاسم، الهاتف، المكان، الكود أو رقم العميل"
                    font.family: "Cairo"
                    horizontalAlignment: Text.AlignRight
                    selectByMouse: true
                    color: pickerTheme.textPrimary
                    placeholderTextColor: pickerTheme.textMuted
                    enabled: !picker.busy
                    onAccepted: picker.runSearch()
                    Keys.onDownPressed: {
                        if (pickerVM.results.length) {
                            resultsList.currentIndex = 0
                            resultsList.forceActiveFocus()
                        }
                    }
                    background: Rectangle {
                        color: pickerTheme.bgPrimary
                        radius: pickerTheme.radiusSmall
                        border.color: searchField.activeFocus ? pickerTheme.gold : pickerTheme.border
                    }
                }
            }
            PickerLabel {
                Layout.fillWidth: true
                visible: picker.errorMessage.length > 0
                value: picker.errorMessage
                color: pickerTheme.red
            }
            ListView {
                id: resultsList
                objectName: "customerPickerResults"
                Layout.fillWidth: true
                Layout.fillHeight: true
                model: pickerVM.results
                currentIndex: -1
                clip: true
                spacing: 6
                keyNavigationEnabled: true
                ScrollBar.vertical: ScrollBar {}
                Keys.onReturnPressed: picker.chooseHighlighted()
                Keys.onEnterPressed: picker.chooseHighlighted()
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    width: resultsList.width - 12
                    height: resultContent.implicitHeight + 18
                    color: ListView.isCurrentItem ? pickerTheme.bgTertiary : pickerTheme.bgPrimary
                    radius: pickerTheme.radiusSmall
                    border.color: ListView.isCurrentItem ? pickerTheme.gold : pickerTheme.border
                    ColumnLayout {
                        id: resultContent
                        anchors.fill: parent
                        anchors.margins: 9
                        spacing: 2
                        PickerLabel {
                            Layout.fillWidth: true
                            value: (modelData.customer_code || "—") + " | " + (modelData.name || "")
                            font.bold: true
                        }
                        PickerLabel {
                            Layout.fillWidth: true
                            value: "رقم العميل: " + modelData.customer_id + " | " + (modelData.status_label || "") + " | " + (modelData.type_label || "—")
                            color: modelData.is_active ? pickerTheme.textMuted : pickerTheme.amber
                            font.pixelSize: 10
                        }
                        PickerLabel {
                            Layout.fillWidth: true
                            value: "الهاتف: " + (modelData.mobile || "—") + " | المكان: " + (modelData.place || "—")
                            color: pickerTheme.textSecondary
                            font.pixelSize: 10
                        }
                        PickerLabel {
                            Layout.fillWidth: true
                            value: (modelData.balance_text || "—") + " — " + (modelData.balance_meaning || "")
                            color: pickerTheme.gold
                        }
                        PickerLabel {
                            Layout.fillWidth: true
                            visible: (modelData.active_warning_count || 0) > 0
                            value: "تحذيرات نشطة: " + (modelData.active_warning_count || 0)
                            color: pickerTheme.amber
                            font.pixelSize: 10
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        enabled: !picker.busy
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            resultsList.currentIndex = index
                            picker.setCustomerId(modelData.customer_id)
                        }
                    }
                }
                PickerLabel {
                    anchors.fill: parent
                    anchors.margins: 12
                    visible: resultsList.count === 0
                    value: picker.busy ? "جارٍ البحث…" : picker.errorMessage.length ? "تعذر تحميل النتائج." : "لا توجد نتائج مطابقة."
                    color: pickerTheme.textMuted
                }
            }
            RowLayout {
                Layout.fillWidth: true
                PickerButton {
                    text: "التالي"
                    enabled: !picker.busy && pickerVM.hasMore
                    onClicked: {
                        resultsList.currentIndex = -1
                        pickerVM.loadPage(pickerVM.page + 1)
                    }
                }
                PickerLabel {
                    Layout.fillWidth: true
                    value: "صفحة " + pickerVM.page
                    color: pickerTheme.textMuted
                }
                PickerButton {
                    text: "السابق"
                    enabled: !picker.busy && pickerVM.page > 1
                    onClicked: {
                        resultsList.currentIndex = -1
                        pickerVM.loadPage(pickerVM.page - 1)
                    }
                }
            }
        }
    }
}
