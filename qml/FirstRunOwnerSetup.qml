import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ApplicationWindow {
    id: setupWindow
    objectName: "firstRunOwnerSetup"
    visible: authenticationVM.stage === "CHECKING_SETUP" || authenticationVM.stage === "FIRST_RUN_SETUP"
    width: 640
    height: 620
    minimumWidth: 480
    minimumHeight: 540
    title: "مجوهرات ماس — إعداد حساب المالك"
    font.family: "Cairo"
    font.pixelSize: 13
    color: theme.bgPrimary
    Material.theme: Material.Dark
    Material.accent: theme.gold
    onClosing: Qt.quit()
    Theme { id: theme }

    function submit() {
        if (authenticationVM.busy)
            return
        var password = passwordField.text
        var confirmation = confirmationField.text
        passwordField.clear()
        confirmationField.clear()
        authenticationVM.createFirstOwner(displayField.text, password, confirmation,
                                          employeeCombo.currentValue || "")
        password = ""
        confirmation = ""
    }
    Connections {
        target: authenticationVM
        function onChanged() {
            passwordField.clear()
            confirmationField.clear()
            if (authenticationVM.stage !== "FIRST_RUN_SETUP") {
                displayField.clear()
                employeeCombo.currentIndex = 0
            }
        }
    }
    component SetupLabel: Label {
        font.family: "Cairo"
        color: theme.textPrimary
        horizontalAlignment: Text.AlignRight
        wrapMode: Text.Wrap
    }
    component SetupField: TextField {
        Layout.fillWidth: true
        font.family: "Cairo"
        horizontalAlignment: Text.AlignRight
        enabled: !authenticationVM.busy
        implicitHeight: 44
    }
    ScrollView {
        anchors.fill: parent
        anchors.margins: 24
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            width: parent.width
            spacing: 14
            SetupLabel {
                Layout.fillWidth: true
                text: "\u200fإعداد حساب المالك"
                font.pixelSize: 23
                font.bold: true
                color: theme.gold
            }
            SetupLabel {
                Layout.fillWidth: true
                text: authenticationVM.stage === "CHECKING_SETUP"
                      ? "\u200fجارٍ التحقق من إعداد التطبيق…"
                      : "\u200fهذه هي المرة الأولى لتشغيل masGold"
            }
            GridLayout {
                visible: authenticationVM.stage === "FIRST_RUN_SETUP"
                Layout.fillWidth: true
                columns: 2
                layoutDirection: Qt.LeftToRight
                columnSpacing: 12
                rowSpacing: 12
                SetupLabel {
                    objectName: "setupUsername"
                    Layout.fillWidth: true
                    text: "admin"
                    font.bold: true
                }
                SetupLabel { text: "\u200fاسم المستخدم" }
                SetupField { id: displayField; objectName: "setupDisplayName"; maximumLength: 100 }
                SetupLabel { text: "\u200fاسم العرض" }
                SetupField {
                    id: passwordField
                    objectName: "setupPassword"
                    maximumLength: 1024
                    echoMode: TextInput.Password
                    inputMethodHints: Qt.ImhHiddenText | Qt.ImhNoPredictiveText | Qt.ImhSensitiveData
                    onAccepted: confirmationField.forceActiveFocus()
                }
                SetupLabel { text: "\u200fكلمة المرور الجديدة" }
                SetupField {
                    id: confirmationField
                    objectName: "setupConfirmation"
                    maximumLength: 1024
                    echoMode: TextInput.Password
                    inputMethodHints: Qt.ImhHiddenText | Qt.ImhNoPredictiveText | Qt.ImhSensitiveData
                    onAccepted: setupWindow.submit()
                }
                SetupLabel { text: "\u200fتأكيد كلمة المرور" }
                ComboBox {
                    id: employeeCombo
                    objectName: "setupEmployee"
                    Layout.fillWidth: true
                    enabled: !authenticationVM.busy
                    font.family: "Cairo"
                    model: [{"id": "", "label": "دون ربط بموظف"}].concat(authenticationVM.employees)
                    textRole: "label"
                    valueRole: "id"
                    currentIndex: 0
                    contentItem: SetupLabel {
                        text: "\u200f" + employeeCombo.displayText
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideLeft
                        wrapMode: Text.NoWrap
                    }
                    delegate: ItemDelegate {
                        required property var modelData
                        width: employeeCombo.width
                        contentItem: SetupLabel { text: "\u200f" + modelData.label }
                    }
                }
                SetupLabel { text: "\u200fربط بموظف (اختياري)" }
            }
            SetupLabel {
                Layout.fillWidth: true
                visible: authenticationVM.stage === "FIRST_RUN_SETUP"
                text: "\u200fاستخدم كلمة مرور لا تقل عن ٦ أحرف. لا يتم ربط موظف إلا باختيارك."
                color: theme.textSecondary
            }
            SetupLabel {
                Layout.fillWidth: true
                objectName: "setupError"
                visible: authenticationVM.errorMessage.length > 0
                text: "\u200f" + authenticationVM.errorMessage
                color: theme.red
            }
            Button {
                objectName: "createFirstOwnerButton"
                Layout.fillWidth: true
                visible: authenticationVM.stage === "FIRST_RUN_SETUP"
                enabled: !authenticationVM.busy && displayField.text.trim().length > 0
                         && passwordField.text.length >= 6 && confirmationField.text.length >= 6
                text: "إنشاء حساب المالك وبدء البرنامج"
                font.family: "Cairo"
                contentItem: SetupLabel { text: "\u200f" + parent.text; verticalAlignment: Text.AlignVCenter }
                onClicked: setupWindow.submit()
            }
            Button {
                Layout.fillWidth: true
                visible: authenticationVM.errorMessage.length > 0
                enabled: !authenticationVM.busy
                text: "إعادة التحقق من الإعداد"
                font.family: "Cairo"
                contentItem: SetupLabel { text: "\u200f" + parent.text; verticalAlignment: Text.AlignVCenter }
                onClicked: authenticationVM.checkStartup()
            }
            BusyIndicator { running: authenticationVM.busy; visible: running; Layout.alignment: Qt.AlignRight }
        }
    }
}
