import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// الموردون (Purchasing & Suppliers V1): suppliers with their two independent positions (gold, making), purchase
// invoices with making groups, the two immutable ledgers, and piece-by-piece barcode intake with weight variance.
// Everything shown is formatted by supplierVM / supplierPurchaseVM; no accounting is computed here and no right is
// decided here (the services re-check every permission in the transaction). Positions are always worded with their
// direction (علينا للمورد / لنا عند المورد), never an ambiguous signed number.
Item {
    id: page
    objectName: "suppliersPage"
    property string pageTitle: ""

    readonly property var vm: typeof supplierVM !== "undefined" ? supplierVM : null
    readonly property var pvm: typeof supplierPurchaseVM !== "undefined" ? supplierPurchaseVM : null
    readonly property var detail: vm ? vm.detail : ({})
    readonly property bool hasSelection: !!vm && vm.selectedSupplierId !== "" && !!detail && detail.id !== undefined
    property int tab: 0

    Theme { id: theme }

    function toneColor(tone) {
        return tone === "shopOwes" ? theme.accountPayable
             : tone === "supplierOwes" ? theme.accountReceivable : theme.textMuted
    }

    onVisibleChanged: if (visible && vm && vm.canView) { vm.clearMessage(); vm.refresh() }
    Component.onCompleted: if (visible && vm && vm.canView) vm.refresh()

    Connections {
        target: page.vm
        function onSupplierSaved(supplierId) {
            if (supplierId !== "" && page.vm.selectedSupplierId === "") page.vm.selectSupplier(supplierId)
        }
    }
    Connections {
        target: page.pvm
        function onInvoicePosted(invoiceId) { newInvoiceDialog.close(); intakeDialog.openFor(invoiceId) }
        function onDraftSaved(invoiceId) { newInvoiceDialog.close() }
    }

    Rectangle { anchors.fill: parent; color: theme.bgPrimary }

    // ---------------------------------------------------------------- small building blocks
    component Txt: Label {
        color: theme.textPrimary
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        horizontalAlignment: Text.AlignRight
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideLeft
        textFormat: Text.PlainText
    }

    component Btn: Button {
        id: btn
        property bool primary: false
        property color accent: theme.gold
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        implicitHeight: 32
        leftPadding: 14
        rightPadding: 14
        contentItem: Label {
            text: btn.text
            font: btn.font
            color: !btn.enabled ? theme.textDisabled : btn.primary ? theme.bgPrimary : btn.accent
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: theme.radiusSmall
            color: btn.primary && btn.enabled ? btn.accent : btn.down ? theme.panelHover : "transparent"
            border.color: btn.enabled ? btn.accent : theme.border
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

    component Combo: ComboBox {
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        implicitHeight: 32
        delegate: ItemDelegate {
            width: parent ? parent.width : 100
            contentItem: Label { text: modelData[textRole]; font.family: "Cairo"; font.pixelSize: theme.fontBody
                                 color: theme.textPrimary; horizontalAlignment: Text.AlignRight }
        }
        contentItem: Label {
            text: parent.displayText
            font: parent.font
            color: theme.textPrimary
            horizontalAlignment: Text.AlignRight
            verticalAlignment: Text.AlignVCenter
            rightPadding: 8
            leftPadding: 24
            elide: Text.ElideLeft
        }
        background: Rectangle { radius: 4; color: theme.bgSecondary; border.color: theme.border }
    }

    component Card: Rectangle {
        default property alias content: body.data
        property string title: ""
        property color accent: theme.gold
        Layout.fillWidth: true
        implicitHeight: body.implicitHeight + 40
        color: theme.panel
        radius: theme.radiusSmall
        border.color: theme.border
        Rectangle { anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                    anchors.margins: 1; width: 3; radius: 2; color: parent.accent }
        Txt { id: cardTitle; anchors.top: parent.top; anchors.right: parent.right; anchors.topMargin: 6
              anchors.rightMargin: 14; text: parent.title; color: parent.accent; font.bold: true }
        ColumnLayout {
            id: body
            anchors.top: cardTitle.bottom; anchors.left: parent.left; anchors.right: parent.right
            anchors.topMargin: 4; anchors.leftMargin: 12; anchors.rightMargin: 14
            spacing: 4
        }
    }

    component TabBtn: Button {
        id: tabBtn
        property bool on: false
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        implicitHeight: 30
        contentItem: Label { text: tabBtn.text; font: tabBtn.font; horizontalAlignment: Text.AlignHCenter
                             color: tabBtn.on ? theme.bgPrimary : theme.textSecondary }
        background: Rectangle { radius: theme.radiusSmall; color: tabBtn.on ? theme.gold : "transparent"
                                border.color: tabBtn.on ? theme.gold : theme.border }
    }

    // ---------------------------------------------------------------- page body
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 8
        visible: !!page.vm && page.vm.canView

        RowLayout {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            spacing: 8
            Txt { text: "🤝  الموردون"; color: theme.gold; font.bold: true; font.pixelSize: theme.fontPageTitle }
            Item { Layout.fillWidth: true }
            Txt {
                objectName: "supplierMessage"
                visible: !!page.vm && (page.vm.message !== "" || page.vm.errorMessage !== "")
                text: page.vm ? (page.vm.errorMessage !== "" ? page.vm.errorMessage : page.vm.message) : ""
                color: page.vm && page.vm.errorMessage !== "" ? theme.red : theme.green
                font.bold: true
                elide: Text.ElideRight
                Layout.maximumWidth: 460
            }
            Btn {
                objectName: "newSupplierButton"
                visible: !!page.vm && page.vm.canManage
                text: "＋  مورد جديد"
                primary: true
                onClicked: supplierDialog.openFor(null)
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            layoutDirection: Qt.RightToLeft
            spacing: 10

            // ------------------------------------------------------------ supplier list (right)
            ColumnLayout {
                Layout.preferredWidth: 310
                Layout.maximumWidth: 330
                Layout.fillHeight: true
                spacing: 6
                Field {
                    objectName: "supplierSearch"
                    Layout.fillWidth: true
                    placeholderText: "بحث بالاسم أو الهاتف"
                    onTextEdited: if (page.vm) page.vm.search(text)
                }
                CheckBox {
                    Layout.alignment: Qt.AlignRight
                    text: "النشطون فقط"
                    font.family: "Cairo"
                    onToggled: if (page.vm) page.vm.setActiveOnly(checked)
                    contentItem: Label { text: parent.text; font: parent.font; color: theme.textSecondary
                                         leftPadding: parent.indicator.width + 6; horizontalAlignment: Text.AlignRight }
                }
                ListView {
                    id: supplierList
                    objectName: "supplierList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 5
                    model: page.vm ? page.vm.suppliers : []
                    delegate: Rectangle {
                        width: supplierList.width
                        height: supplierColumn.implicitHeight + 14
                        radius: theme.radiusSmall
                        color: modelData.selected ? theme.panelHover : theme.panel
                        border.color: modelData.selected ? theme.gold : theme.border
                        opacity: modelData.active ? 1 : 0.6
                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                page.vm.selectSupplier(modelData.id)
                                page.pvm.showSupplier(modelData.id)
                                page.tab = 0
                            }
                        }
                        ColumnLayout {
                            id: supplierColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 10; anchors.rightMargin: 12
                            spacing: 2
                            RowLayout {
                                Layout.fillWidth: true
                                layoutDirection: Qt.RightToLeft
                                Txt { text: modelData.name; font.bold: true; Layout.fillWidth: true }
                                Txt { text: modelData.stateText; color: modelData.active ? theme.green : theme.textMuted
                                      font.pixelSize: theme.fontCaption }
                            }
                            Txt { Layout.fillWidth: true; text: modelData.goldText; color: page.toneColor(modelData.goldTone)
                                  font.pixelSize: theme.fontCaption }
                            Txt { Layout.fillWidth: true; text: modelData.makingText; color: page.toneColor(modelData.makingTone)
                                  font.pixelSize: theme.fontCaption }
                        }
                    }
                    Txt {
                        anchors.centerIn: parent
                        visible: supplierList.count === 0
                        text: page.vm && page.vm.busy ? "جار التحميل..." : "لا يوجد موردون"
                        color: theme.textMuted
                    }
                }
            }

            // ------------------------------------------------------------ selected supplier (left)
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 8

                Txt {
                    visible: !page.hasSelection
                    Layout.fillWidth: true
                    Layout.topMargin: 40
                    horizontalAlignment: Text.AlignHCenter
                    text: "اختر موردًا لعرض موقفه وفواتيره وسجلاته."
                    color: theme.textMuted
                }

                RowLayout {
                    visible: page.hasSelection
                    Layout.fillWidth: true
                    layoutDirection: Qt.RightToLeft
                    spacing: 6
                    Txt { text: page.detail.name || ""; font.bold: true; font.pixelSize: theme.fontSection; color: theme.gold }
                    Txt { text: page.detail.code || ""; color: theme.textMuted }
                    Item { Layout.fillWidth: true }
                    Btn {
                        objectName: "newInvoiceButton"
                        visible: !!page.pvm && page.pvm.canEdit
                        enabled: page.hasSelection && page.detail.active === true
                        text: "＋  فاتورة شراء جديدة"
                        primary: true
                        onClicked: newInvoiceDialog.openFor()
                    }
                    Btn {
                        objectName: "editSupplierButton"
                        visible: !!page.vm && page.vm.canManage
                        text: "تعديل"
                        onClicked: supplierDialog.openFor(page.detail)
                    }
                    Btn {
                        objectName: "toggleSupplierButton"
                        visible: !!page.vm && page.vm.canManage
                        text: page.detail.active ? "إيقاف" : "تفعيل"
                        accent: page.detail.active ? theme.amber : theme.green
                        onClicked: page.vm.setSupplierActive(page.detail.id, !page.detail.active)
                    }
                    Btn {
                        objectName: "deleteSupplierButton"
                        visible: !!page.vm && page.vm.canManage
                        text: "حذف"
                        accent: theme.red
                        onClicked: deleteConfirm.open()
                    }
                }

                // The two independent positions: gold (Eq21 + per karat) and making (EGP).
                RowLayout {
                    visible: page.hasSelection
                    Layout.fillWidth: true
                    layoutDirection: Qt.RightToLeft
                    spacing: 8
                    Card {
                        Layout.preferredWidth: 1
                        Layout.fillWidth: true
                        title: "موقف الذهب"
                        accent: page.toneColor(page.detail.goldTone)
                        Txt { objectName: "goldPosition"; Layout.fillWidth: true; text: page.detail.goldEq21Text || ""
                              font.bold: true; color: page.toneColor(page.detail.goldTone); font.pixelSize: theme.fontBodyStrong }
                        Repeater {
                            model: page.detail.goldLines || []
                            delegate: Txt { Layout.fillWidth: true; text: modelData.text; color: page.toneColor(modelData.tone)
                                            font.pixelSize: theme.fontCaption }
                        }
                    }
                    Card {
                        Layout.preferredWidth: 1
                        Layout.fillWidth: true
                        title: "موقف المصنعيات"
                        accent: page.toneColor(page.detail.makingTone)
                        Txt { objectName: "makingPosition"; Layout.fillWidth: true; text: page.detail.makingText || ""
                              font.bold: true; color: page.toneColor(page.detail.makingTone); font.pixelSize: theme.fontBodyStrong }
                    }
                }

                RowLayout {
                    visible: page.hasSelection
                    Layout.fillWidth: true
                    layoutDirection: Qt.RightToLeft
                    spacing: 6
                    TabBtn { objectName: "tabInvoices"; text: "فواتير الشراء"; on: page.tab === 0; onClicked: page.tab = 0 }
                    TabBtn { objectName: "tabGold"; text: "سجل الذهب"; on: page.tab === 1; onClicked: page.tab = 1 }
                    TabBtn { objectName: "tabMaking"; text: "سجل المصنعيات"; on: page.tab === 2; onClicked: page.tab = 2 }
                    Item { Layout.fillWidth: true }
                }

                // ---- invoices
                ListView {
                    id: invoiceList
                    objectName: "supplierInvoiceList"
                    visible: page.hasSelection && page.tab === 0
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 5
                    model: page.pvm ? page.pvm.invoices : []
                    delegate: Rectangle {
                        width: invoiceList.width
                        height: 54
                        radius: theme.radiusSmall
                        color: theme.panel
                        border.color: theme.border
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10; anchors.rightMargin: 12
                            layoutDirection: Qt.RightToLeft
                            spacing: 10
                            ColumnLayout {
                                Layout.preferredWidth: 150
                                spacing: 0
                                Txt { Layout.fillWidth: true; text: modelData.code; font.bold: true; color: theme.gold }
                                Txt { Layout.fillWidth: true; text: modelData.reference + "  •  " + modelData.date
                                      color: theme.textMuted; font.pixelSize: theme.fontCaption }
                            }
                            ColumnLayout {
                                Layout.preferredWidth: 170
                                spacing: 0
                                Txt { Layout.fillWidth: true; text: "عيار " + modelData.karat + " — " + modelData.weightText }
                                Txt { Layout.fillWidth: true; text: "مصنعيات: " + modelData.makingText
                                      color: theme.textSecondary; font.pixelSize: theme.fontCaption }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                Txt { Layout.fillWidth: true; text: modelData.statusText
                                      color: modelData.status === "POSTED" ? theme.green : theme.textSecondary; font.bold: true }
                                Txt { Layout.fillWidth: true; visible: modelData.status === "POSTED"
                                      text: "القطع: " + modelData.pieceCount + "  •  " + modelData.varianceText
                                      color: theme.textMuted; font.pixelSize: theme.fontCaption }
                            }
                            Btn {
                                text: modelData.status === "POSTED" ? "إدخال القطع" : "فتح"
                                onClicked: intakeDialog.openFor(modelData.id)
                            }
                        }
                    }
                    Txt { anchors.centerIn: parent; visible: invoiceList.count === 0; text: "لا توجد فواتير"
                          color: theme.textMuted }
                }

                // ---- gold ledger
                ListView {
                    id: goldList
                    objectName: "supplierGoldLedger"
                    visible: page.hasSelection && page.tab === 1
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 4
                    model: page.detail.goldLedger || []
                    delegate: Rectangle {
                        width: goldList.width
                        height: 46
                        radius: theme.radiusSmall
                        color: theme.panel
                        border.color: theme.border
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10; anchors.rightMargin: 12
                            layoutDirection: Qt.RightToLeft
                            spacing: 8
                            ColumnLayout {
                                Layout.preferredWidth: 150
                                spacing: 0
                                Txt { Layout.fillWidth: true; text: modelData.movement; font.bold: true }
                                Txt { Layout.fillWidth: true; text: modelData.time + "  •  " + modelData.reference
                                      color: theme.textMuted; font.pixelSize: theme.fontCaption }
                            }
                            Txt { Layout.preferredWidth: 190; text: modelData.side + " " + modelData.weight + " جرام عيار " + modelData.karat
                                  color: page.toneColor(modelData.tone) }
                            Txt { Layout.fillWidth: true; text: "الرصيد: " + modelData.balance; color: theme.textSecondary
                                  font.pixelSize: theme.fontCaption }
                        }
                    }
                    Txt { anchors.centerIn: parent; visible: goldList.count === 0; text: "لا توجد حركات ذهب"
                          color: theme.textMuted }
                }

                // ---- making ledger
                ListView {
                    id: makingList
                    objectName: "supplierMakingLedger"
                    visible: page.hasSelection && page.tab === 2
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 4
                    model: page.detail.makingLedger || []
                    delegate: Rectangle {
                        width: makingList.width
                        height: 46
                        radius: theme.radiusSmall
                        color: theme.panel
                        border.color: theme.border
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10; anchors.rightMargin: 12
                            layoutDirection: Qt.RightToLeft
                            spacing: 8
                            ColumnLayout {
                                Layout.preferredWidth: 150
                                spacing: 0
                                Txt { Layout.fillWidth: true; text: modelData.movement; font.bold: true }
                                Txt { Layout.fillWidth: true; text: modelData.time + "  •  " + modelData.reference
                                      color: theme.textMuted; font.pixelSize: theme.fontCaption }
                            }
                            Txt { Layout.preferredWidth: 190; text: modelData.side + " " + modelData.amount + " مصنعيات"
                                  color: page.toneColor(modelData.tone) }
                            Txt { Layout.fillWidth: true; text: "الرصيد: " + modelData.balance; color: theme.textSecondary
                                  font.pixelSize: theme.fontCaption }
                        }
                    }
                    Txt { anchors.centerIn: parent; visible: makingList.count === 0; text: "لا توجد حركات مصنعيات"
                          color: theme.textMuted }
                }
            }
        }
    }

    Txt {
        anchors.centerIn: parent
        visible: !page.vm || !page.vm.canView
        text: "لا تملك صلاحية عرض الموردين."
        color: theme.textMuted
    }

    // ---------------------------------------------------------------- supplier add / edit
    Popup {
        id: supplierDialog
        objectName: "supplierDialog"
        property string editId: ""
        function openFor(data) {
            editId = data ? data.id : ""
            nameField.text = data ? data.name : ""
            phoneField.text = data ? data.phone : ""
            notesField.text = data ? data.notes : ""
            if (page.vm) page.vm.clearMessage()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(460, page.width - 32)
        modal: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 8
            Txt { Layout.fillWidth: true; text: supplierDialog.editId === "" ? "مورد جديد" : "تعديل بيانات المورد"
                  color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection }
            Txt { Layout.fillWidth: true; text: "اسم المورد *" }
            Field { id: nameField; objectName: "supplierNameField"; Layout.fillWidth: true }
            Txt { Layout.fillWidth: true; text: "رقم الهاتف" }
            Field { id: phoneField; objectName: "supplierPhoneField"; Layout.fillWidth: true }
            Txt { Layout.fillWidth: true; text: "ملاحظات" }
            Field { id: notesField; objectName: "supplierNotesField"; Layout.fillWidth: true }
            Txt { Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""; text: page.vm ? page.vm.errorMessage : ""
                  color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "saveSupplierButton"
                    text: "حفظ"
                    primary: true
                    enabled: nameField.text.trim().length > 0 && !!page.vm && !page.vm.busy
                    onClicked: {
                        if (supplierDialog.editId === "") page.vm.createSupplier(nameField.text, phoneField.text, notesField.text)
                        else page.vm.updateSupplier(supplierDialog.editId, nameField.text, phoneField.text, notesField.text)
                        supplierDialog.close()
                    }
                }
                Btn { text: "إلغاء"; accent: theme.textSecondary; onClicked: supplierDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    Popup {
        id: deleteConfirm
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(420, page.width - 32)
        modal: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.red }
        contentItem: ColumnLayout {
            spacing: 10
            Txt { Layout.fillWidth: true; text: "حذف المورد نهائيًا؟"; color: theme.red; font.bold: true }
            Txt { Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "يُحذف المورد غير المستخدم فقط. المورد الذي له فواتير أو حركات يُرفض حذفه (أوقفه بدلاً من ذلك)." }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn { text: "حذف"; accent: theme.red; primary: true
                      onClicked: { deleteConfirm.close(); page.vm.deleteSupplier(page.detail.id) } }
                Btn { text: "إلغاء"; accent: theme.textSecondary; onClicked: deleteConfirm.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- new purchase invoice
    Popup {
        id: newInvoiceDialog
        objectName: "newSupplierInvoiceDialog"
        property string requestKey: ""
        property var preview: ({ valid: false, error: "", weightText: "—", makingText: "—", eq21Text: "—", groupMaking: [] })
        readonly property int karatValue: karatCombo.currentIndex >= 0 && page.masterKarats.length > karatCombo.currentIndex
                                          ? page.masterKarats[karatCombo.currentIndex].karat_value : 0
        function collect() {
            var rows = []
            for (var i = 0; i < groupModel.count; ++i) {
                var g = groupModel.get(i)
                rows.push({ weight: g.weight, rate: g.rate, description: g.description })
            }
            return rows
        }
        function recompute() {
            if (page.pvm) preview = page.pvm.previewGroups(collect(), karatValue)
        }
        function openFor() {
            requestKey = page.pvm ? page.pvm.newRequestKey() : ""
            groupModel.clear()
            groupModel.append({ weight: "", rate: "", description: "" })
            referenceField.text = ""
            notesField2.text = ""
            dateField.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            karatCombo.currentIndex = Math.max(0, page.indexOfKarat(21))
            if (page.pvm) page.pvm.clearMessage()
            recompute()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(760, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 640, 640)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        ListModel { id: groupModel }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection
                  text: "فاتورة شراء جديدة — " + (page.detail.name || "") }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: formColumn.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: formColumn
                    width: parent.width - 12
                    spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "رقم فاتورة المورد" }
                            Field { id: referenceField; objectName: "invoiceReferenceField"; Layout.fillWidth: true }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "التاريخ (yyyy-MM-dd)" }
                            Field { id: dateField; objectName: "invoiceDateField"; Layout.fillWidth: true }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "العيار (فاتورة واحدة = عيار واحد) *" }
                            Combo {
                                id: karatCombo
                                objectName: "invoiceKaratCombo"
                                Layout.fillWidth: true
                                model: page.masterKarats
                                textRole: "label"
                                onActivated: newInvoiceDialog.recompute()
                            }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "المجموعات المصنعية (وزن المورد × مصنعية الجرام)"; color: theme.gold
                          font.bold: true }
                    Repeater {
                        model: groupModel
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            Txt { Layout.preferredWidth: 22; text: (index + 1) + "" ; horizontalAlignment: Text.AlignHCenter
                                  color: theme.textMuted }
                            Field {
                                objectName: "groupWeight_" + index
                                Layout.preferredWidth: 130
                                placeholderText: "الوزن (جرام)"
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                text: model.weight
                                onTextEdited: { groupModel.setProperty(index, "weight", text); newInvoiceDialog.recompute() }
                            }
                            Field {
                                objectName: "groupRate_" + index
                                Layout.preferredWidth: 130
                                placeholderText: "مصنعية ج/جرام"
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                text: model.rate
                                onTextEdited: { groupModel.setProperty(index, "rate", text); newInvoiceDialog.recompute() }
                            }
                            Field {
                                Layout.fillWidth: true
                                placeholderText: "وصف المجموعة (اختياري)"
                                text: model.description
                                onTextEdited: groupModel.setProperty(index, "description", text)
                            }
                            Txt { Layout.preferredWidth: 110
                                  text: newInvoiceDialog.preview.valid && index < newInvoiceDialog.preview.groupMaking.length
                                        ? newInvoiceDialog.preview.groupMaking[index] : "—"
                                  color: theme.textSecondary }
                            Btn { text: "✕"; accent: theme.red; implicitWidth: 34; leftPadding: 4; rightPadding: 4
                                  enabled: groupModel.count > 1
                                  onClicked: { groupModel.remove(index); newInvoiceDialog.recompute() } }
                        }
                    }
                    Btn {
                        objectName: "addGroupButton"
                        Layout.alignment: Qt.AlignRight
                        text: "＋  مجموعة"
                        enabled: groupModel.count < 50
                        onClicked: { groupModel.append({ weight: "", rate: "", description: "" }); newInvoiceDialog.recompute() }
                    }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: notesField2; objectName: "invoiceNotesField"; Layout.fillWidth: true }
                }
            }
            // The invoice totals computed by the same rules as the service (never by QML).
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: totalsRow.implicitHeight + 12
                radius: theme.radiusSmall
                color: theme.panel
                border.color: theme.border
                RowLayout {
                    id: totalsRow
                    anchors.fill: parent
                    anchors.margins: 6
                    layoutDirection: Qt.RightToLeft
                    spacing: 14
                    Txt { objectName: "invoiceTotalWeight"; text: "وزن المورد: " + newInvoiceDialog.preview.weightText; font.bold: true }
                    Txt { objectName: "invoiceTotalEq21"; text: "مكافئ عيار 21: " + newInvoiceDialog.preview.eq21Text
                          color: theme.textSecondary }
                    Txt { objectName: "invoiceTotalMaking"; text: "إجمالي المصنعيات: " + newInvoiceDialog.preview.makingText
                          color: theme.gold; font.bold: true }
                    Item { Layout.fillWidth: true }
                }
            }
            Txt { Layout.fillWidth: true; visible: !newInvoiceDialog.preview.valid || (!!page.pvm && page.pvm.errorMessage !== "")
                  text: !newInvoiceDialog.preview.valid ? newInvoiceDialog.preview.error : (page.pvm ? page.pvm.errorMessage : "")
                  color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "postInvoiceButton"
                    visible: !!page.pvm && page.pvm.canPost
                    text: "ترحيل الفاتورة"
                    primary: true
                    enabled: newInvoiceDialog.preview.valid && !!page.pvm && !page.pvm.busy && page.hasSelection
                    onClicked: page.pvm.saveInvoice(page.detail.id, page.karatIdAt(karatCombo.currentIndex), referenceField.text,
                                                    dateField.text, newInvoiceDialog.collect(), notesField2.text,
                                                    newInvoiceDialog.requestKey, true)
                }
                Btn {
                    objectName: "saveDraftButton"
                    visible: !!page.pvm && page.pvm.canEdit
                    text: "حفظ كمسودة"
                    enabled: newInvoiceDialog.preview.valid && !!page.pvm && !page.pvm.busy && page.hasSelection
                    onClicked: page.pvm.saveInvoice(page.detail.id, page.karatIdAt(karatCombo.currentIndex), referenceField.text,
                                                    dateField.text, newInvoiceDialog.collect(), notesField2.text,
                                                    newInvoiceDialog.requestKey, false)
                }
                Btn { text: "إلغاء"; accent: theme.textSecondary; onClicked: newInvoiceDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    readonly property var masterKarats: {
        var rows = typeof masterDataVM !== "undefined" ? masterDataVM.karats : []
        var out = []
        for (var i = 0; i < rows.length; ++i)
            out.push({ id: rows[i].id, karat_value: rows[i].karat_value, label: "عيار " + rows[i].karat_value })
        return out
    }
    function indexOfKarat(value) {
        for (var i = 0; i < masterKarats.length; ++i) if (masterKarats[i].karat_value === value) return i
        return 0
    }
    function karatIdAt(index) { return index >= 0 && index < masterKarats.length ? masterKarats[index].id : 0 }

    // ---------------------------------------------------------------- invoice detail + piece-by-piece intake
    Popup {
        id: intakeDialog
        objectName: "supplierIntakeDialog"
        property string selectedGroup: ""
        property string pieceKey: ""
        property string postKey: ""
        readonly property var inv: page.pvm ? page.pvm.detail : ({})
        readonly property bool ready: !!inv && inv.id !== undefined
        function openFor(invoiceId) {
            selectedGroup = ""
            pieceKey = page.pvm ? page.pvm.newRequestKey() : ""
            postKey = page.pvm ? page.pvm.newRequestKey() : ""          // one post key per open: a double click is idempotent
            weightField.text = ""
            if (page.pvm) { page.pvm.clearMessage(); page.pvm.openInvoice(invoiceId) }
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(900, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 640, 660)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        onClosed: if (page.pvm) page.pvm.closeInvoice()
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        Connections {
            target: page.pvm
            function onStockChanged() {
                intakeDialog.pieceKey = page.pvm.newRequestKey()      // the next piece is a new request
                weightField.text = ""
                weightField.forceActiveFocus()
            }
        }
        contentItem: ColumnLayout {
            spacing: 6
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Txt { text: intakeDialog.ready ? "فاتورة " + intakeDialog.inv.code + " — " + intakeDialog.inv.supplierName : "..."
                      color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection }
                Txt { visible: intakeDialog.ready; text: intakeDialog.ready ? intakeDialog.inv.statusText : ""
                      color: intakeDialog.ready && intakeDialog.inv.posted ? theme.green : theme.textSecondary; font.bold: true }
                Item { Layout.fillWidth: true }
                Btn { objectName: "intakePostButton"
                      visible: intakeDialog.ready && intakeDialog.inv.draft && !!page.pvm && page.pvm.canPost
                      text: "ترحيل الفاتورة"; primary: true; enabled: !page.pvm.busy
                      onClicked: page.pvm.postInvoice(intakeDialog.inv.id, intakeDialog.postKey) }
                Btn { visible: intakeDialog.ready && intakeDialog.inv.draft && !!page.pvm && page.pvm.canEdit
                      text: "إلغاء المسودة"; accent: theme.red; enabled: !page.pvm.busy
                      onClicked: { page.pvm.cancelDraft(intakeDialog.inv.id); intakeDialog.close() } }
                Btn { text: "إغلاق"; accent: theme.textSecondary; onClicked: intakeDialog.close() }
            }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: intakeColumn.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: intakeColumn
                    width: parent.width - 12
                    spacing: 6
                    visible: intakeDialog.ready

                    // Contractual truth vs shop-scale truth (variance is information, never a balance).
                    Card {
                        title: "الفاتورة"
                        Txt { Layout.fillWidth: true; objectName: "intakeSummary"
                              text: intakeDialog.ready ? "عيار " + intakeDialog.inv.karat + "  •  وزن المورد " + intakeDialog.inv.weightText
                                    + "  •  مكافئ عيار 21: " + intakeDialog.inv.eq21Text + "  •  إجمالي المصنعيات: "
                                    + intakeDialog.inv.makingText : "" }
                        Txt { Layout.fillWidth: true; objectName: "intakeProgress"
                              text: intakeDialog.ready ? "الوزن المُدخل (فعلي): " + intakeDialog.inv.actualText + "  •  "
                                    + intakeDialog.inv.varianceText + "  •  " + intakeDialog.inv.remainingText
                                    + "  •  " + intakeDialog.inv.percentText : ""
                              color: intakeDialog.ready && intakeDialog.inv.overSupplier ? theme.amber : theme.textSecondary }
                        Txt { Layout.fillWidth: true; font.pixelSize: theme.fontCaption; color: theme.textMuted
                              text: "فرق الوزن للعلم فقط: لا يغيّر رصيد المورد ولا المصنعيات ولا أوزان القطع." }
                    }
                    Txt { Layout.fillWidth: true; text: "المجموعات (اضغط مجموعة لإدخال قطعة منها)"; color: theme.gold; font.bold: true }
                    Repeater {
                        model: intakeDialog.ready ? intakeDialog.inv.groups : []
                        delegate: Rectangle {
                            objectName: "intakeGroup_" + index
                            Layout.fillWidth: true
                            implicitHeight: groupColumn.implicitHeight + 12
                            radius: theme.radiusSmall
                            color: intakeDialog.selectedGroup === modelData.id ? theme.panelHover : theme.panel
                            border.color: intakeDialog.selectedGroup === modelData.id ? theme.gold : theme.border
                            MouseArea { anchors.fill: parent; onClicked: intakeDialog.selectedGroup = modelData.id }
                            ColumnLayout {
                                id: groupColumn
                                anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 10; anchors.rightMargin: 12
                                spacing: 1
                                Txt { Layout.fillWidth: true; font.bold: true
                                      text: "مجموعة " + modelData.no + (modelData.description !== "" ? " — " + modelData.description : "")
                                            + "  •  " + modelData.weightText + " @ " + modelData.rateText
                                            + "  •  مصنعيات " + modelData.makingText }
                                Txt { Layout.fillWidth: true; font.pixelSize: theme.fontCaption
                                      color: modelData.overSupplier ? theme.amber : theme.textSecondary
                                      text: "القطع: " + modelData.pieceCount + "  •  المُدخل: " + modelData.actualText + "  •  "
                                            + modelData.varianceText + "  •  " + modelData.remainingText + "  •  " + modelData.percentText }
                            }
                        }
                    }

                    // ---- the piece form (posted invoices only)
                    Card {
                        visible: intakeDialog.ready && intakeDialog.inv.posted && !!page.pvm && page.pvm.canIntake
                        title: "إدخال قطعة"
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            ColumnLayout {
                                Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                                Txt { Layout.fillWidth: true; text: "الصنف *" }
                                Combo { id: itemTypeCombo; objectName: "pieceItemTypeCombo"; Layout.fillWidth: true
                                        model: typeof masterDataVM !== "undefined" ? masterDataVM.itemTypes : []
                                        textRole: "name" }
                            }
                            ColumnLayout {
                                Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                                Txt { Layout.fillWidth: true; text: "الوزن على ميزان المحل (جرام) *" }
                                Field { id: weightField; objectName: "pieceWeightField"; Layout.fillWidth: true
                                        inputMethodHints: Qt.ImhFormattedNumbersOnly }
                            }
                            ColumnLayout {
                                Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                                Txt { Layout.fillWidth: true; text: "المقاس" }
                                Field { id: sizeField; objectName: "pieceSizeField"; Layout.fillWidth: true }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            ColumnLayout {
                                Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                                Txt { Layout.fillWidth: true; text: "الفئة" }
                                Combo { id: categoryCombo; objectName: "pieceCategoryCombo"; Layout.fillWidth: true
                                        model: [{ id: 0, name: "حسب الصنف" }].concat(typeof masterDataVM !== "undefined" ? masterDataVM.categories : [])
                                        textRole: "name" }
                            }
                            ColumnLayout {
                                Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                                Txt { Layout.fillWidth: true; text: "المصنع" }
                                Combo { id: factoryCombo; objectName: "pieceFactoryCombo"; Layout.fillWidth: true
                                        model: [{ id: 0, name: "غير محدد" }].concat(typeof masterDataVM !== "undefined" ? masterDataVM.factories : [])
                                        textRole: "name" }
                            }
                            ColumnLayout {
                                Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                                Txt { Layout.fillWidth: true; text: "الباركود" }
                                Combo { id: barcodeMode; objectName: "pieceBarcodeMode"; Layout.fillWidth: true
                                        model: [{ code: "AUTO", name: "توليد تلقائي" }, { code: "MANUAL", name: "إدخال يدوي" },
                                                { code: "NONE", name: "بدون باركود" }]
                                        textRole: "name" }
                            }
                        }
                        Field { id: manualBarcode; objectName: "pieceManualBarcode"; Layout.fillWidth: true
                                visible: barcodeMode.currentIndex === 1; placeholderText: "قيمة الباركود" }
                        Field { id: pieceNotes; objectName: "pieceNotesField"; Layout.fillWidth: true; placeholderText: "ملاحظات القطعة (اختياري)" }
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 8
                            Btn {
                                objectName: "addPieceButton"
                                text: "＋  إدخال القطعة"
                                primary: true
                                enabled: intakeDialog.selectedGroup !== "" && weightField.text.trim().length > 0
                                         && itemTypeCombo.currentIndex >= 0 && !!page.pvm && !page.pvm.busy
                                onClicked: page.pvm.addPiece(
                                    intakeDialog.inv.id, intakeDialog.selectedGroup,
                                    itemTypeCombo.model[itemTypeCombo.currentIndex].id, weightField.text, intakeDialog.pieceKey,
                                    categoryCombo.model[categoryCombo.currentIndex].id,
                                    factoryCombo.model[factoryCombo.currentIndex].id, sizeField.text,
                                    barcodeMode.model[barcodeMode.currentIndex].code, manualBarcode.text, pieceNotes.text)
                            }
                            Txt { visible: intakeDialog.selectedGroup === ""; text: "اختر المجموعة أولًا"; color: theme.amber
                                  font.pixelSize: theme.fontCaption }
                            Item { Layout.fillWidth: true }
                        }
                        Txt { objectName: "lastPieceText"; Layout.fillWidth: true; color: theme.green; font.bold: true
                              visible: !!page.pvm && page.pvm.lastPiece.inventoryItemId !== undefined
                              text: page.pvm && page.pvm.lastPiece.inventoryItemId !== undefined
                                    ? "آخر قطعة: #" + page.pvm.lastPiece.inventoryItemId + "  •  باركود " + page.pvm.lastPiece.barcode
                                      + "  •  " + page.pvm.lastPiece.weightText + "  •  مصنعية " + page.pvm.lastPiece.makingText : "" }
                    }

                    Txt { Layout.fillWidth: true; visible: intakeDialog.ready && intakeDialog.inv.pieces.length > 0
                          text: "القطع المُدخلة"; color: theme.gold; font.bold: true }
                    Repeater {
                        model: intakeDialog.ready ? intakeDialog.inv.pieces : []
                        delegate: Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 34
                            radius: theme.radiusSmall
                            color: theme.panel
                            border.color: theme.border
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 10; anchors.rightMargin: 12
                                layoutDirection: Qt.RightToLeft
                                spacing: 10
                                Txt { Layout.preferredWidth: 70; text: "#" + modelData.itemId; color: theme.gold }
                                Txt { Layout.preferredWidth: 90; text: "مجموعة " + modelData.groupNo }
                                Txt { Layout.preferredWidth: 120; text: modelData.itemType + (modelData.size !== "" ? " " + modelData.size : "") }
                                Txt { Layout.preferredWidth: 80; text: modelData.weightText }
                                Txt { Layout.fillWidth: true; text: modelData.barcode
                                      color: modelData.hasBarcode ? theme.textPrimary : theme.textMuted }
                                Txt { Layout.preferredWidth: 110; text: modelData.statusText; color: theme.textSecondary
                                      font.pixelSize: theme.fontCaption }
                            }
                        }
                    }
                    Txt { Layout.fillWidth: true; visible: !!page.pvm && page.pvm.errorMessage !== ""
                          text: page.pvm ? page.pvm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap
                          elide: Text.ElideNone }
                }
            }
        }
    }
}
