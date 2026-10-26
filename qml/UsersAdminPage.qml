import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Settings → المستخدمون والصلاحيات (USERS_MANAGE). Users, activation, their roles, the grants of each role and
// credentials. Everything shown comes formatted from userAdminVM; every change is re-checked by the service,
// which also re-verifies the administrator's CURRENT password. No password is kept here: a password field is
// cleared the moment it is sent. The OWNER role is shown but fixed; USERS_MANAGE can only belong to OWNER.
Item {
    id: page
    objectName: "userAdminPage"
    property string pageTitle: ""
    signal backRequested()

    readonly property var vm: typeof userAdminVM !== "undefined" ? userAdminVM : null
    readonly property var selectedUser: {
        if (!vm) return null
        var rows = vm.users
        for (var i = 0; i < rows.length; ++i)
            if (rows[i].id === vm.selectedUserId) return rows[i]
        return null
    }
    property string roleTab: "MANAGER"
    // The role checkboxes of the selected user are a draft until «حفظ الأدوار».
    property var rolesDraft: []
    property string draftKey: ""

    Theme { id: theme }

    onVisibleChanged: if (visible && vm) { vm.clearMessage(); vm.refresh() }
    Component.onCompleted: if (visible && vm) vm.refresh()

    function syncDraft() {
        var u = page.selectedUser
        var key = u ? u.id + ":" + JSON.stringify(u.roleCodes) : ""
        if (key !== page.draftKey) {
            page.draftKey = key
            page.rolesDraft = u ? u.roleCodes.slice() : []
        }
    }
    Connections { target: page.vm; function onChanged() { page.syncDraft() } }

    function draftHas(code) { return page.rolesDraft.indexOf(code) >= 0 }
    function toggleDraft(code, on) {
        var copy = page.rolesDraft.slice()
        var at = copy.indexOf(code)
        if (on && at < 0) copy.push(code)
        if (!on && at >= 0) copy.splice(at, 1)
        page.rolesDraft = copy
    }
    function roleGranted(role, code) { return role.permissions.indexOf(code) >= 0 }

    Rectangle { anchors.fill: parent; color: theme.bgPrimary }

    component Txt: Label {
        color: theme.textPrimary
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        horizontalAlignment: Text.AlignRight
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideLeft
        textFormat: Text.PlainText
    }

    // Unified app button (same look as the invoice-history «Btn»): a real hover state (the
    // default Button ignores the mouse until hoverEnabled is set), a pressed state, short
    // colour transitions and breathing padding. A coloured accent keeps its meaning on a
    // dark chip; a plain action stays quiet until it is hovered.
    component Btn: Button {
        id: btn
        property bool primary: false
        property color accent: theme.gold
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        implicitHeight: 32
        hoverEnabled: true
        padding: 12
        contentItem: Label {
            text: btn.text
            font.family: btn.font.family
            font.pixelSize: btn.font.pixelSize
            font.bold: btn.primary
            color: !btn.enabled ? theme.textDisabled
                   : btn.primary ? (btn.hovered ? theme.goldBright : btn.accent)
                   : btn.accent.toString() !== theme.gold.toString() ? (btn.hovered ? theme.textPrimary : btn.accent)
                   : btn.hovered ? theme.textPrimary : theme.textSecondary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            Behavior on color { ColorAnimation { duration: 120 } }
        }
        background: Rectangle {
            radius: theme.radiusSmall
            color: !btn.enabled ? theme.bgSecondary
                   : btn.primary ? (btn.down ? Qt.darker(btn.accent, 1.3) : btn.hovered ? "#3A2F1A" : "#2A2214")
                   : btn.down ? theme.bgPrimary : btn.hovered ? theme.panelHover : theme.bgTertiary
            border.width: 1
            border.color: !btn.enabled ? theme.border
                          : btn.primary || btn.accent.toString() !== theme.gold.toString() ? (btn.down ? theme.goldBright : btn.accent)
                          : (btn.hovered || btn.activeFocus) ? theme.goldDark : theme.border
            Behavior on color { ColorAnimation { duration: 120 } }
            Behavior on border.color { ColorAnimation { duration: 120 } }
        }
    }

    component Field: TextField {
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        color: theme.textPrimary
        placeholderTextColor: theme.textDisabled
        horizontalAlignment: TextInput.AlignRight
        selectByMouse: true
        implicitHeight: 32
        background: Rectangle { radius: 4; color: theme.bgSecondary
                                border.color: parent.activeFocus ? theme.gold : theme.border }
    }

    component Card: Rectangle {
        default property alias content: body.data
        property string title: ""
        property color accent: theme.gold
        Layout.fillWidth: true
        implicitHeight: body.implicitHeight + 46
        color: theme.panel
        radius: theme.radiusCard
        border.color: theme.cardBorder
        Txt { id: cardTitle; anchors.top: parent.top; anchors.right: parent.right; anchors.topMargin: 8
              anchors.rightMargin: 14; text: parent.title; color: parent.accent; font.bold: true
              font.pixelSize: theme.fontSection }
        ColumnLayout {
            id: body
            anchors.top: cardTitle.bottom; anchors.left: parent.left; anchors.right: parent.right
            anchors.topMargin: 6; anchors.leftMargin: 12; anchors.rightMargin: 14
            spacing: 6
        }
    }

    // ---------------------------------------------------------------- the administrator's password prompt
    Popup {
        id: passwordPrompt
        objectName: "userAdminPasswordDialog"
        property var action: null
        property string question: ""
        function ask(text, callback) { question = text; action = callback; passwordInput.text = ""; open() }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(460, page.width - 32)
        modal: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        onClosed: passwordInput.text = ""
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 10
            Txt { Layout.fillWidth: true; text: "تأكيد بكلمة مرورك"; color: theme.gold; font.bold: true
                  font.pixelSize: theme.fontSection }
            Txt { Layout.fillWidth: true; text: passwordPrompt.question; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            Field {
                id: passwordInput
                objectName: "userAdminPasswordField"
                Layout.fillWidth: true
                echoMode: TextInput.Password
                placeholderText: passwordInput.text.length > 0 ? "" : "كلمة مرورك الحالية"
                onAccepted: confirmButton.clicked()
            }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    id: confirmButton
                    objectName: "userAdminPasswordConfirm"
                    text: "تأكيد"
                    primary: true
                    enabled: passwordInput.text.length > 0 && !!page.vm && !page.vm.busy
                    onClicked: {
                        var password = passwordInput.text
                        passwordInput.text = ""                 // never kept
                        var run = passwordPrompt.action
                        passwordPrompt.close()
                        if (run) run(password)
                    }
                }
                Btn { text: "إلغاء"; accent: theme.textSecondary; onClicked: passwordPrompt.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- reset password
    Popup {
        id: resetDialog
        objectName: "userAdminResetDialog"
        function begin() { newPassword.text = ""; confirmPassword.text = ""; resetReason.text = ""; resetAdmin.text = ""; open() }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(500, page.width - 32)
        modal: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        onClosed: { newPassword.text = ""; confirmPassword.text = ""; resetAdmin.text = "" }
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 8
            Txt { Layout.fillWidth: true; font.bold: true; color: theme.gold; font.pixelSize: theme.fontSection
                  text: "إعادة تعيين كلمة مرور " + (page.selectedUser ? page.selectedUser.username : "") }
            Field { id: newPassword; objectName: "userAdminNewPassword"; Layout.fillWidth: true
                    echoMode: TextInput.Password; placeholderText: text.length > 0 ? "" : "كلمة المرور الجديدة" }
            Field { id: confirmPassword; objectName: "userAdminConfirmPassword"; Layout.fillWidth: true
                    echoMode: TextInput.Password; placeholderText: text.length > 0 ? "" : "تأكيد كلمة المرور" }
            Field { id: resetReason; objectName: "userAdminResetReason"; Layout.fillWidth: true
                    placeholderText: text.length > 0 ? "" : "السبب (إلزامي)" }
            Field { id: resetAdmin; objectName: "userAdminResetAdmin"; Layout.fillWidth: true
                    echoMode: TextInput.Password; placeholderText: text.length > 0 ? "" : "كلمة مرورك الحالية" }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "userAdminResetConfirm"
                    text: "إعادة التعيين"
                    primary: true
                    enabled: newPassword.text.length > 0 && confirmPassword.text.length > 0
                             && resetReason.text.trim().length > 0 && resetAdmin.text.length > 0
                             && !!page.vm && !page.vm.busy
                    onClicked: {
                        page.vm.resetPassword(page.selectedUser.id, newPassword.text, confirmPassword.text,
                                              resetReason.text, resetAdmin.text)
                        newPassword.text = ""; confirmPassword.text = ""; resetAdmin.text = ""
                        resetDialog.close()
                    }
                }
                Btn { text: "إلغاء"; accent: theme.textSecondary; onClicked: resetDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- new user
    Popup {
        id: createDialog
        objectName: "userAdminCreateDialog"
        function begin() {
            newUsername.text = ""; newDisplay.text = ""; createPassword.text = ""; createConfirm.text = ""
            createAdmin.text = ""; createRole.currentIndex = 0; createEmployee.currentIndex = 0; open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(500, page.width - 32)
        modal: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        onClosed: { createPassword.text = ""; createConfirm.text = ""; createAdmin.text = "" }
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 8
            Txt { Layout.fillWidth: true; text: "مستخدم جديد"; font.bold: true; color: theme.gold
                  font.pixelSize: theme.fontSection }
            Field { id: newUsername; objectName: "userAdminNewUsername"; Layout.fillWidth: true
                    placeholderText: text.length > 0 ? "" : "اسم المستخدم (إنجليزي)" }
            Field { id: newDisplay; objectName: "userAdminNewDisplay"; Layout.fillWidth: true
                    placeholderText: text.length > 0 ? "" : "اسم العرض" }
            ComboBox {
                id: createRole
                objectName: "userAdminNewRole"
                Layout.fillWidth: true
                font.family: "Cairo"
                model: page.vm ? page.vm.roles.filter(function(r) { return r.active }) : []
                textRole: "name"
                valueRole: "code"
            }
            ComboBox {
                id: createEmployee
                objectName: "userAdminNewEmployee"
                Layout.fillWidth: true
                font.family: "Cairo"
                model: [{ id: "", name: "بدون ربط بموظف" }].concat(page.vm ? page.vm.employees : [])
                textRole: "name"
                valueRole: "id"
            }
            Field { id: createPassword; objectName: "userAdminCreatePassword"; Layout.fillWidth: true
                    echoMode: TextInput.Password; placeholderText: text.length > 0 ? "" : "كلمة المرور" }
            Field { id: createConfirm; objectName: "userAdminCreateConfirm"; Layout.fillWidth: true
                    echoMode: TextInput.Password; placeholderText: text.length > 0 ? "" : "تأكيد كلمة المرور" }
            Field { id: createAdmin; objectName: "userAdminCreateAdmin"; Layout.fillWidth: true
                    echoMode: TextInput.Password; placeholderText: text.length > 0 ? "" : "كلمة مرورك الحالية" }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "userAdminCreateConfirmButton"
                    text: "إنشاء"
                    primary: true
                    enabled: newUsername.text.trim().length > 0 && newDisplay.text.trim().length > 0
                             && createPassword.text.length > 0 && createConfirm.text.length > 0
                             && createAdmin.text.length > 0 && createRole.currentIndex >= 0 && !!page.vm && !page.vm.busy
                    onClicked: {
                        page.vm.createUser(newUsername.text, newDisplay.text, createEmployee.currentValue || "",
                                           createRole.currentValue, createPassword.text, createConfirm.text,
                                           createAdmin.text)
                        createPassword.text = ""; createConfirm.text = ""; createAdmin.text = ""
                        createDialog.close()
                    }
                }
                Btn { text: "إلغاء"; accent: theme.textSecondary; onClicked: createDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ================================================================ PAGE
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            spacing: 10
            Txt { text: "المستخدمون والصلاحيات"; color: theme.gold; font.bold: true; font.pixelSize: theme.fontPageTitle }
            Txt { Layout.fillWidth: true; text: "إدارة المستخدمين وأدوارهم وصلاحيات كل دور (للمالك فقط)"
                  color: theme.textMuted }
            Btn { objectName: "userAdminCreate"; text: "+ مستخدم جديد"; primary: true
                  enabled: !!page.vm && page.vm.canManage && !page.vm.busy; onClicked: createDialog.begin() }
            Btn { objectName: "userAdminRefresh"; text: "تحديث"; accent: theme.textSecondary
                  enabled: !!page.vm && !page.vm.busy; onClicked: page.vm.refresh() }
            Btn { objectName: "userAdminBack"; text: "← العودة للإعدادات"; accent: theme.textSecondary
                  onClicked: page.backRequested() }
        }

        Txt {
            objectName: "userAdminNoPermission"
            Layout.fillWidth: true
            visible: !page.vm || !page.vm.canManage
            text: "إدارة المستخدمين والصلاحيات للمالك فقط."
            color: theme.statusWarning
            font.bold: true
        }
        Txt {
            objectName: "userAdminError"
            Layout.fillWidth: true
            visible: text.length > 0
            text: page.vm ? page.vm.errorMessage : ""
            color: theme.statusWarning
            font.bold: true
            wrapMode: Text.WordWrap
            elide: Text.ElideNone
        }
        Txt {
            objectName: "userAdminMessage"
            Layout.fillWidth: true
            visible: text.length > 0
            text: page.vm ? page.vm.message : ""
            color: theme.statusSuccess
            font.bold: true
        }

        ScrollView {
            id: scroll
            objectName: "userAdminScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !!page.vm && page.vm.canManage
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            ColumnLayout {
                width: scroll.availableWidth
                spacing: 10

                // ------------------------------------------------ users
                Card {
                    objectName: "userAdminUsers"
                    title: "المستخدمون"
                    Repeater {
                        model: page.vm ? page.vm.users : []
                        Rectangle {
                            id: userRow
                            required property var modelData
                            objectName: "userAdminUserRow_" + modelData.id
                            Layout.fillWidth: true
                            implicitHeight: 44
                            radius: 4
                            color: modelData.selected ? theme.bgTertiary : userArea.containsMouse ? theme.panelHover : theme.bgSecondary
                            border.color: modelData.selected ? theme.gold : theme.border
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
                                        Txt { text: userRow.modelData.displayName; font.bold: true; font.pixelSize: 13 }
                                        Txt { text: userRow.modelData.username; color: theme.gold }
                                        Txt { visible: userRow.modelData.isSelf; text: "(أنت)"; color: theme.textMuted }
                                        Item { Layout.fillWidth: true }
                                    }
                                    Txt { Layout.fillWidth: true
                                          text: userRow.modelData.rolesText + "  •  " + userRow.modelData.employee
                                                + "  •  آخر دخول: " + userRow.modelData.lastLogin
                                          color: theme.textMuted; font.pixelSize: 11 }
                                }
                                Txt {
                                    objectName: "userAdminState_" + userRow.modelData.id
                                    text: userRow.modelData.stateText
                                    color: userRow.modelData.active ? theme.statusSuccess : theme.textMuted
                                    font.bold: true
                                }
                            }
                            MouseArea { id: userArea; anchors.fill: parent; hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: page.vm.selectUser(userRow.modelData.id) }
                        }
                    }
                }

                // ------------------------------------------------ the selected user
                Card {
                    objectName: "userAdminSelected"
                    visible: !!page.selectedUser
                    title: page.selectedUser ? "المستخدم: " + page.selectedUser.displayName : ""
                    accent: theme.accountReceivable

                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Btn {
                            objectName: "userAdminToggleActive"
                            visible: !!page.selectedUser
                            text: page.selectedUser && page.selectedUser.active ? "تعطيل المستخدم" : "تفعيل المستخدم"
                            accent: page.selectedUser && page.selectedUser.active ? theme.statusWarning : theme.statusSuccess
                            // Self-deactivation is refused by the service as well; the button just does not offer it.
                            enabled: !!page.selectedUser && !(page.selectedUser.isSelf && page.selectedUser.active)
                                     && !!page.vm && !page.vm.busy
                            onClicked: {
                                var user = page.selectedUser
                                var on = !user.active
                                passwordPrompt.ask((on ? "تفعيل " : "تعطيل ") + user.username + " — أدخل كلمة مرورك للتأكيد.",
                                                   function(password) { page.vm.setUserActive(user.id, on, password) })
                            }
                        }
                        Btn {
                            objectName: "userAdminResetPassword"
                            text: "إعادة تعيين كلمة المرور"
                            accent: theme.gold
                            enabled: !!page.vm && !page.vm.busy
                            onClicked: resetDialog.begin()
                        }
                        Item { Layout.fillWidth: true }
                    }
                    Txt { text: "الأدوار"; color: theme.textMuted; font.bold: true }
                    Flow {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 14
                        Repeater {
                            model: page.vm ? page.vm.roles.filter(function(r) { return r.active }) : []
                            CheckBox {
                                id: roleBox
                                required property var modelData
                                objectName: "userAdminRoleCheck_" + modelData.code
                                text: modelData.name
                                font.family: "Cairo"
                                font.pixelSize: theme.fontBody
                                checked: page.draftHas(modelData.code)
                                enabled: !!page.selectedUser && !page.selectedUser.isSelf
                                onToggled: page.toggleDraft(modelData.code, checked)
                                contentItem: Label {
                                    text: roleBox.text; font: roleBox.font; color: theme.textPrimary
                                    leftPadding: roleBox.indicator.width + 6; verticalAlignment: Text.AlignVCenter
                                }
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Btn {
                            objectName: "userAdminSaveRoles"
                            text: "حفظ الأدوار"
                            primary: true
                            enabled: !!page.selectedUser && !page.selectedUser.isSelf && page.rolesDraft.length > 0
                                     && JSON.stringify(page.rolesDraft.slice().sort())
                                        !== JSON.stringify(page.selectedUser.roleCodes.slice().sort())
                                     && !!page.vm && !page.vm.busy
                            onClicked: {
                                var user = page.selectedUser
                                var draft = page.rolesDraft.slice()
                                passwordPrompt.ask("تغيير أدوار " + user.username + " — أدخل كلمة مرورك للتأكيد.",
                                                   function(password) { page.vm.setUserRoles(user.id, draft, password) })
                            }
                        }
                        Txt { Layout.fillWidth: true; visible: !!page.selectedUser && page.selectedUser.isSelf
                              text: "لا يمكنك تعديل أدوار حسابك أو تعطيله."; color: theme.textMuted }
                    }
                }

                // ------------------------------------------------ roles and permissions
                Card {
                    objectName: "userAdminRoles"
                    title: "الأدوار والصلاحيات"
                    accent: theme.priceBuy
                    Flow {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Repeater {
                            model: page.vm ? page.vm.roles : []
                            Btn {
                                required property var modelData
                                objectName: "userAdminRoleTab_" + modelData.code
                                text: modelData.name + (modelData.locked ? " 🔒" : "") + "  (" + modelData.userCount + ")"
                                accent: page.roleTab === modelData.code ? theme.gold : theme.textSecondary
                                onClicked: page.roleTab = modelData.code
                            }
                        }
                    }
                    Txt {
                        Layout.fillWidth: true
                        visible: text.length > 0
                        text: {
                            var roles = page.vm ? page.vm.roles : []
                            for (var i = 0; i < roles.length; ++i)
                                if (roles[i].code === page.roleTab && roles[i].locked)
                                    return "صلاحيات المالك ثابتة (يملك كل الصلاحيات صراحةً) ولا تُعدّل من الشاشة."
                            return ""
                        }
                        color: theme.textMuted
                        wrapMode: Text.WordWrap
                        elide: Text.ElideNone
                    }
                    Repeater {
                        model: page.vm ? page.vm.roles.filter(function(r) { return r.code === page.roleTab }) : []
                        ColumnLayout {
                            id: roleBlock
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 2
                            Repeater {
                                model: page.vm ? page.vm.permissions : []
                                Rectangle {
                                    id: permRow
                                    required property var modelData
                                    Layout.fillWidth: true
                                    implicitHeight: 34
                                    radius: 3
                                    color: "transparent"
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 6
                                        anchors.rightMargin: 6
                                        layoutDirection: Qt.RightToLeft
                                        spacing: 10
                                        CheckBox {
                                            id: grantBox
                                            objectName: "userAdminPerm_" + permRow.modelData.code
                                            checked: page.roleGranted(roleBlock.modelData, permRow.modelData.code)
                                            enabled: !roleBlock.modelData.locked && !permRow.modelData.ownerOnly
                                                     && !!page.vm && !page.vm.busy
                                            onToggled: {
                                                var want = checked
                                                checked = page.roleGranted(roleBlock.modelData, permRow.modelData.code)
                                                passwordPrompt.ask((want ? "منح " : "سحب ") + permRow.modelData.name
                                                                   + " — " + roleBlock.modelData.name + ". أدخل كلمة مرورك للتأكيد.",
                                                                   function(password) {
                                                                       page.vm.setRolePermission(roleBlock.modelData.code,
                                                                           permRow.modelData.code, want, password) })
                                            }
                                        }
                                        Txt { text: permRow.modelData.name; font.bold: true }
                                        Txt { text: permRow.modelData.code; color: theme.textMuted; font.pixelSize: 10 }
                                        Txt { visible: permRow.modelData.ownerOnly; text: "للمالك فقط"; color: theme.gold
                                              font.pixelSize: 10 }
                                        Item { Layout.fillWidth: true }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
