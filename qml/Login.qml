import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ApplicationWindow {
    id: loginWindow
    objectName: "loginWindow"
    visible: authenticationVM.stage === "LOGIN"
    width: 580
    height: 470
    minimumWidth: 460
    minimumHeight: 440
    title: "مجوهرات ماس — تسجيل الدخول"
    color: theme.bgPrimary
    font.family: "Cairo"
    font.pixelSize: 13
    Material.theme: Material.Dark
    Material.accent: theme.gold
    onClosing: Qt.quit()
    Theme { id: theme }

    function submit() {
        if (authenticationVM.busy)
            return
        var credential = passwordField.text
        passwordField.clear()
        authenticationVM.login(usernameField.text, credential)
        credential = ""
    }
    Connections {
        target: authenticationVM
        function onChanged() { passwordField.clear() }
        function onLoggedOut() { usernameField.clear(); passwordField.clear() }
    }
    component LoginLabel: Label {
        font.family: "Cairo"
        color: theme.textPrimary
        horizontalAlignment: Text.AlignRight
        Layout.fillWidth: true
        wrapMode: Text.Wrap
    }
    Rectangle {
        anchors.centerIn: parent
        width: Math.min(parent.width - 40, 480)
        height: form.implicitHeight + 36
        color: theme.bgSecondary
        radius: theme.radiusMedium
        border.color: theme.border
        ColumnLayout {
            id: form
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 18
            spacing: 12
            LoginLabel {
                text: "\u200fمجوهرات ماس"
                color: theme.gold
                font.pixelSize: 24
                font.bold: true
            }
            LoginLabel { text: "\u200fتسجيل الدخول إلى التطبيق" }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.LeftToRight
                TextField {
                    id: usernameField
                    objectName: "loginUsername"
                    Layout.fillWidth: true
                    enabled: !authenticationVM.busy
                    maximumLength: 100
                    font.family: "Cairo"
                    horizontalAlignment: Text.AlignRight
                    placeholderText: "اسم المستخدم"
                    onAccepted: passwordField.forceActiveFocus()
                    Component.onCompleted: forceActiveFocus()
                }
                LoginLabel { Layout.fillWidth: false; Layout.preferredWidth: 110; text: "\u200fاسم المستخدم" }
            }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.LeftToRight
                TextField {
                    id: passwordField
                    objectName: "loginPassword"
                    Layout.fillWidth: true
                    enabled: !authenticationVM.busy
                    maximumLength: 1024
                    echoMode: TextInput.Password
                    inputMethodHints: Qt.ImhHiddenText | Qt.ImhNoPredictiveText | Qt.ImhSensitiveData
                    font.family: "Cairo"
                    horizontalAlignment: Text.AlignRight
                    placeholderText: "كلمة المرور"
                    onAccepted: loginWindow.submit()
                }
                LoginLabel { Layout.fillWidth: false; Layout.preferredWidth: 110; text: "\u200fكلمة المرور" }
            }
            LoginLabel {
                objectName: "loginError"
                text: "\u200f" + authenticationVM.errorMessage
                visible: authenticationVM.errorMessage.length > 0
                color: theme.red
            }
            Button {
                objectName: "loginButton"
                Layout.fillWidth: true
                enabled: !authenticationVM.busy && usernameField.text.length > 0 && passwordField.text.length > 0
                font.family: "Cairo"
                text: authenticationVM.busy ? "جارٍ التحقق…" : "تسجيل الدخول"
                contentItem: LoginLabel { text: "\u200f" + parent.text; verticalAlignment: Text.AlignVCenter }
                onClicked: loginWindow.submit()
            }
            BusyIndicator {
                Layout.alignment: Qt.AlignRight
                Layout.preferredHeight: 28
                Layout.preferredWidth: 28
                running: authenticationVM.busy
                visible: running
            }
        }
    }
}
