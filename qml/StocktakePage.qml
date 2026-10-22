import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// الجرد بالباركود: scan every piece on the shelf. A scanned barcode leaves المتبقي (right) and appears
// in تم الجرد (left). The session lives in StocktakeViewModel only — nothing in stock changes.
Item {
    id: page
    objectName: "stocktakePage"

    signal backRequested()

    readonly property var vm: typeof stocktakeVM !== "undefined" ? stocktakeVM : null
    readonly property var adjustVM: typeof stocktakeAdjustVM !== "undefined" ? stocktakeAdjustVM : null
    property string doneText: ""

    // mode "all": approve the whole count; mode "single": write one remaining piece off.
    function openApprove(mode, itemId) {
        page.doneText = ""
        approveDialog.mode = mode
        approveDialog.singleId = itemId || ""
        page.adjustVM.prepare()
        reasonBox.currentIndex = 0
        noteField.text = ""
        confirmCheck.checked = false
        approveDialog.open()
    }
    readonly property bool wide: width >= 900            // one-row controls
    readonly property bool sideBySide: width >= 700      // المتبقي | تم الجرد next to each other

    Theme { id: theme }

    Connections {
        target: page.adjustVM
        ignoreUnknownSignals: true
        function onApproved(code, count, grams) {
            page.doneText = "✓ تم اعتماد الجرد " + code + " وشطب " + count + " قطعة (" + grams + " جرام) من المخزون."
            approveDialog.close()
            if (page.vm) {
                if (approveDialog.mode === "single") page.vm.dropRemaining([approveDialog.singleId])
                else page.vm.start()
            }
        }
    }

    // Opening the screen begins a fresh session and puts the cursor in the scanner field.
    onVisibleChanged: {
        if (visible && vm) {
            vm.start()
            scanField.forceActiveFocus()
        }
    }

    function refocus() { scanField.text = ""; scanField.forceActiveFocus() }

    function idAt(options, index) { return index >= 0 && options.length > index ? Number(options[index].id) : 0 }

    function requestFilters() {
        if (!vm) return
        var itemTypeId = idAt(vm.itemTypeOptions, itemTypeFilter.currentIndex)
        var karatId = idAt(vm.karatOptions, karatFilter.currentIndex)
        if (itemTypeId === vm.itemTypeId && karatId === vm.karatId) return
        if (!vm.applyFilters(itemTypeId, karatId, false)) {
            confirmDialog.action = "filters"           // scans exist: confirm before a new scope
            confirmDialog.open()
            return
        }
        refocus()
    }

    function syncFilters() {
        if (!vm) return
        var types = vm.itemTypeOptions, karats = vm.karatOptions
        for (var i = 0; i < types.length; i++) if (Number(types[i].id) === vm.itemTypeId) itemTypeFilter.currentIndex = i
        for (var j = 0; j < karats.length; j++) if (Number(karats[j].id) === vm.karatId) karatFilter.currentIndex = j
    }

    Rectangle { anchors.fill: parent; color: theme.bgPrimary }

    component Txt: Label {
        font.family: theme.fontFamily
        font.pixelSize: theme.fontBody
        color: theme.textSecondary
        horizontalAlignment: Text.AlignRight
        elide: Text.ElideLeft
        textFormat: Text.PlainText
    }

    // One side of the count: header totals + compact rows.
    component SidePanel: Rectangle {
        id: side
        property string title: ""
        property color accent: theme.gold
        property var rows: []
        property int count: 0
        property string weightText: "0.000"
        property string listName: ""
        property bool deletable: false
        signal deleteRequested(string itemId)
        color: theme.panel
        radius: theme.radiusCard
        border.color: theme.cardBorder
        border.width: 1

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 10
            spacing: 6

            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                Txt { text: side.title; color: side.accent; font.pixelSize: 16; font.bold: true }
                Item { Layout.fillWidth: true }
                Txt { objectName: side.listName + "Count"; text: side.count + " قطعة"; color: theme.textPrimary
                      font.bold: true }
                Txt { objectName: side.listName + "Weight"; text: side.weightText + " جرام"; color: side.accent
                      font.bold: true }
            }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                Txt { Layout.preferredWidth: 120; text: "الباركود"; color: theme.textMuted }
                Txt { Layout.fillWidth: true; text: "الصنف"; color: theme.textMuted }
                Txt { Layout.preferredWidth: 72; text: "الوزن"; color: theme.textMuted }
                Txt { Layout.preferredWidth: 44; text: "العيار"; color: theme.textMuted }
                Txt { Layout.preferredWidth: 50; text: "المقاس"; color: theme.textMuted }
                Item { Layout.preferredWidth: side.deletable ? 34 : 0 }
            }
            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: theme.border }
            ListView {
                objectName: side.listName
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: side.rows
                spacing: 2
                delegate: RowLayout {
                    required property var modelData
                    width: ListView.view.width
                    layoutDirection: Qt.RightToLeft
                    Txt { Layout.preferredWidth: 120; text: modelData.barcode
                          color: modelData.has_barcode ? theme.textPrimary : theme.amber }
                    Txt { Layout.fillWidth: true; text: modelData.item_type; color: theme.textPrimary }
                    Txt { Layout.preferredWidth: 72; text: modelData.weight_text }
                    Txt { Layout.preferredWidth: 44; text: modelData.karat }
                    Txt { Layout.preferredWidth: 50; text: modelData.size }
                    Button {
                        objectName: side.listName + "Delete_" + modelData.id
                        visible: side.deletable
                        Layout.preferredWidth: side.deletable ? 34 : 0
                        Layout.preferredHeight: 24
                        text: "🗑"
                        padding: 0
                        font.family: theme.fontFamily
                        onClicked: side.deleteRequested(modelData.id)
                        background: Rectangle { radius: theme.radiusSmall; color: parent.hovered ? theme.panelHover : "transparent"; border.color: theme.red }
                    }
                }
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 22
        spacing: 10

        // ------------------------------------------------ header
        RowLayout {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            spacing: 8
            ColumnLayout {
                spacing: 2
                Txt { text: "🔎 الجرد بالباركود"; color: theme.gold; font.pixelSize: 20; font.bold: true }
                Txt { text: "امسح كل قطعة موجودة — المخزون لا يتغيّر إلا بعد اعتماد الجرد"; color: theme.textMuted
                      font.pixelSize: 12 }
            }
            Item { Layout.fillWidth: true }
            Button { objectName: "stocktakeBack"; text: "←  العودة للمخزون"; font.family: theme.fontFamily
                     onClicked: page.backRequested() }
        }

        // ------------------------------------------------ scanner + scope
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: controls.implicitHeight + 20
            color: theme.bgSecondary
            radius: theme.radiusSmall
            border.color: theme.border
            border.width: 1

            GridLayout {
                id: controls
                anchors.fill: parent
                anchors.margins: 10
                layoutDirection: Qt.RightToLeft
                columns: page.wide ? 6 : 2
                columnSpacing: 8
                rowSpacing: 8

                BarcodeField {
                    id: scanField
                    objectName: "stocktakeScanField"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 44
                    Layout.columnSpan: page.wide ? 1 : 2
                    placeholderText: "امسح الباركود هنا..."
                    font.family: theme.fontFamily
                    horizontalAlignment: TextInput.AlignRight
                    onAccepted: {
                        if (!page.vm) return
                        page.vm.scan(text)
                        page.refocus()                    // no mouse between scans
                    }
                }
                ComboBox {
                    id: itemTypeFilter
                    objectName: "stocktakeItemTypeFilter"
                    Layout.preferredWidth: 180
                    model: page.vm ? page.vm.itemTypeOptions : []
                    textRole: "name"
                    font.family: theme.fontFamily
                    onActivated: page.requestFilters()
                }
                ComboBox {
                    id: karatFilter
                    objectName: "stocktakeKaratFilter"
                    Layout.preferredWidth: 150
                    model: page.vm ? page.vm.karatOptions : []
                    textRole: "name"
                    font.family: theme.fontFamily
                    onActivated: page.requestFilters()
                }
                Button {
                    objectName: "stocktakeReset"
                    text: "↺  إعادة الضبط"
                    font.family: theme.fontFamily
                    onClicked: {
                        if (!page.vm) return
                        if (!page.vm.reset(false)) {
                            confirmDialog.action = "reset"
                            confirmDialog.open()
                            return
                        }
                        page.refocus()
                    }
                }
                Button {
                    objectName: "stocktakeApprove"
                    visible: !!page.adjustVM && page.adjustVM.canAdjust
                    enabled: !!page.vm && page.vm.scannedCount > 0 && page.vm.remainingCount > 0 && page.vm.withoutBarcodeCount === 0
                    text: "✔  اعتماد الجرد وشطب المتبقي"
                    font.family: theme.fontFamily
                    onClicked: page.openApprove("all", "")
                }
                Txt {
                    objectName: "stocktakeMessage"
                    Layout.fillWidth: true
                    Layout.columnSpan: page.wide ? 1 : 2
                    text: page.vm ? page.vm.message : ""
                    color: !page.vm ? theme.textMuted
                           : page.vm.messageKind === "ok" ? theme.green
                           : page.vm.messageKind === "warning" ? theme.amber : theme.red
                    font.bold: true
                }
            }
        }

        // ------------------------------------------------ per-karat live totals
        Flow {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            spacing: 8
            Repeater {
                model: page.vm ? page.vm.karatTotals : []
                delegate: Rectangle {
                    required property var modelData
                    width: karatText.implicitWidth + 20
                    height: karatText.implicitHeight + 10
                    radius: theme.radiusSmall
                    color: theme.bgSecondary
                    border.color: theme.border
                    Txt {
                        id: karatText
                        anchors.centerIn: parent
                        text: modelData.title + " — المتبقي " + modelData.remaining_count + " / "
                              + modelData.remaining_weight_text + " جرام • تم الجرد " + modelData.scanned_count
                              + " / " + modelData.scanned_weight_text + " جرام"
                        color: theme.textPrimary
                        font.pixelSize: theme.fontCaption
                    }
                }
            }
        }

        Txt {
            objectName: "stocktakeDone"
            visible: page.doneText.length > 0
            Layout.fillWidth: true
            text: page.doneText
            color: theme.green
            font.bold: true
            wrapMode: Text.WordWrap
        }

        Txt {
            visible: !!page.vm && (page.vm.errorMessage.length > 0 || page.vm.withoutBarcodeCount > 0)
            Layout.fillWidth: true
            text: !page.vm ? "" : page.vm.errorMessage.length > 0 ? page.vm.errorMessage
                  : "⚠ " + page.vm.withoutBarcodeCount + " قطعة بدون باركود لا يمكن مسحها — تظل في المتبقي، واعتماد الجرد كله متوقف حتى تحذفها واحدة واحدة (🗑) أو تعطيها باركود."
            color: page.vm && page.vm.errorMessage.length > 0 ? theme.red : theme.amber
            wrapMode: Text.WordWrap
        }

        // ------------------------------------------------ RIGHT: المتبقي • LEFT: تم الجرد
        GridLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            layoutDirection: Qt.RightToLeft            // first child on the physical right
            columns: page.sideBySide ? 2 : 1
            columnSpacing: 12
            rowSpacing: 12

            SidePanel {
                objectName: "stocktakeRemainingPanel"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 160
                title: "المتبقي"
                accent: theme.amber
                listName: "stocktakeRemaining"
                deletable: !!page.adjustVM && page.adjustVM.canAdjust
                onDeleteRequested: function(itemId) { page.openApprove("single", itemId) }
                rows: page.vm ? page.vm.remainingRows : []
                count: page.vm ? page.vm.remainingCount : 0
                weightText: page.vm ? page.vm.remainingWeightText : "0.000"
            }
            SidePanel {
                objectName: "stocktakeScannedPanel"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 160
                title: "تم الجرد"
                accent: theme.green
                listName: "stocktakeScanned"
                rows: page.vm ? page.vm.scannedRows : []
                count: page.vm ? page.vm.scannedCount : 0
                weightText: page.vm ? page.vm.scannedWeightText : "0.000"
            }
        }

        Txt {
            objectName: "stocktakeEmpty"
            visible: !!page.vm && page.vm.loaded && page.vm.remainingCount === 0 && page.vm.scannedCount === 0
                     && page.vm.errorMessage.length === 0
            Layout.alignment: Qt.AlignHCenter
            text: "لا توجد قطع بالمخزون ضمن هذا الجرد"
            color: theme.textMuted
        }
    }

    // Changing scope / resetting discards the scans: always confirmed first.
    Dialog {
        id: confirmDialog
        objectName: "stocktakeConfirmDialog"
        property string action: ""
        modal: true
        anchors.centerIn: parent
        width: Math.min(440, page.width - 40)
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 10; border.color: theme.amber; border.width: 1 }
        onRejected: page.syncFilters()
        onClosed: page.refocus()                     // back to the scanner once the popup is gone

        ColumnLayout {
            anchors.fill: parent
            spacing: 12
            Txt {
                Layout.fillWidth: true
                text: confirmDialog.action === "reset"
                      ? "سيتم مسح ما تم جرده وإرجاع كل القطع إلى المتبقي. هل تريد المتابعة؟"
                      : "تغيير الصنف أو العيار يبدأ جرداً جديداً ويمسح ما تم جرده. هل تريد المتابعة؟"
                color: theme.textPrimary
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                Button { objectName: "stocktakeConfirmCancel"; text: "إلغاء"; font.family: theme.fontFamily
                         onClicked: confirmDialog.reject() }
                Item { Layout.fillWidth: true }
                Button {
                    objectName: "stocktakeConfirmOk"
                    text: "متابعة"
                    font.family: theme.fontFamily
                    onClicked: {
                        if (confirmDialog.action === "reset")
                            page.vm.reset(true)
                        else
                            page.vm.applyFilters(page.idAt(page.vm.itemTypeOptions, itemTypeFilter.currentIndex),
                                                 page.idAt(page.vm.karatOptions, karatFilter.currentIndex), true)
                        confirmDialog.close()
                    }
                }
            }
        }
    }

    // اعتماد الجرد: the reconciliation per karat (expected = found + written off), a mandatory reason and one explicit confirmation. The service re-derives every figure under locks.
    Dialog {
        id: approveDialog
        objectName: "stocktakeApproveDialog"
        modal: true
        anchors.centerIn: parent
        width: Math.min(560, page.width - 40)
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: theme.radiusCard; border.color: theme.amber; border.width: 1 }
        property string mode: "all"
        property string singleId: ""
        readonly property var pieceInfo: mode === "single" && page.vm ? page.vm.pieceInfo(singleId) : ({})
        readonly property var reasons: [{ code: "SOLD_OUTSIDE", text: "اتباعت خارج السيستم" }, { code: "MISSING", text: "مفقودة" },
                                        { code: "DATA_ERROR", text: "خطأ في تسجيل المخزون" }, { code: "OTHER", text: "سبب آخر (اكتب ملاحظة)" }]
        onClosed: page.refocus()

        ColumnLayout {
            anchors.fill: parent
            spacing: 10
            Txt { text: approveDialog.mode === "single" ? "حذف قطعة من المخزون" : "اعتماد الجرد وشطب المتبقي"; color: theme.amber; font.pixelSize: 17; font.bold: true; Layout.fillWidth: true }
            Txt {
                objectName: "stocktakePieceInfo"
                visible: approveDialog.mode === "single"
                Layout.fillWidth: true
                text: approveDialog.pieceInfo.barcode ? (approveDialog.pieceInfo.item_type + " • باركود " + approveDialog.pieceInfo.barcode + " • عيار " + approveDialog.pieceInfo.karat + " • " + approveDialog.pieceInfo.weight_text + " جرام") : ""
                color: theme.textPrimary
                font.bold: true
                wrapMode: Text.WordWrap
            }
            Txt {
                Layout.fillWidth: true
                text: approveDialog.mode === "single" ? "القطعة ستخرج من المخزون (حالة «مستبعدة») ولن تُباع. لا يُحذف شيء من السجل، لكن لا يوجد زر لإرجاعها."
                      : "القطع التي لم تُمسح ستخرج من المخزون (حالة «مستبعدة») ولن تُباع. لا يُحذف شيء من السجل، لكن لا يوجد زر لإرجاعها."
                color: theme.textPrimary
                wrapMode: Text.WordWrap
            }
            Repeater {
                model: page.vm && approveDialog.mode === "all" ? page.vm.karatTotals : []
                delegate: Txt {
                    required property var modelData
                    Layout.fillWidth: true
                    text: modelData.title + " — المفروض " + (modelData.remaining_count + modelData.scanned_count) + " قطعة • الموجود " + modelData.scanned_count + " ("
                          + modelData.scanned_weight_text + " جرام) • المشطوب " + modelData.remaining_count + " (" + modelData.remaining_weight_text + " جرام)"
                    color: theme.textPrimary
                    wrapMode: Text.WordWrap
                }
            }
            Txt {
                visible: approveDialog.mode === "all"
                Layout.fillWidth: true
                text: "الإجمالي المشطوب: " + (page.vm ? page.vm.remainingCount : 0) + " قطعة • " + (page.vm ? page.vm.remainingWeightText : "0.000") + " جرام"
                color: theme.amber
                font.bold: true
            }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                Txt { text: "السبب"; color: theme.textSecondary }
                ComboBox {
                    id: reasonBox
                    objectName: "stocktakeReason"
                    Layout.preferredWidth: 240
                    model: approveDialog.reasons
                    textRole: "text"
                    font.family: theme.fontFamily
                }
            }
            TextField {
                id: noteField
                objectName: "stocktakeNote"
                Layout.fillWidth: true
                placeholderText: "ملاحظة (إجبارية عند «سبب آخر»)"
                font.family: theme.fontFamily
                horizontalAlignment: TextInput.AlignRight
                maximumLength: 300
            }
            CheckBox {
                id: confirmCheck
                objectName: "stocktakeConfirmCheck"
                Layout.fillWidth: true
                text: "أؤكد أن القطع المتبقية غير موجودة فعلاً وأريد شطبها من المخزون"
                font.family: theme.fontFamily
            }
            Txt {
                objectName: "stocktakeApproveMessage"
                Layout.fillWidth: true
                visible: text.length > 0
                text: page.adjustVM ? page.adjustVM.message : ""
                color: page.adjustVM && page.adjustVM.messageIsError ? theme.red : theme.green
                font.bold: true
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                Button { objectName: "stocktakeApproveCancel"; text: "إلغاء"; font.family: theme.fontFamily; onClicked: approveDialog.close() }
                Item { Layout.fillWidth: true }
                Button {
                    objectName: "stocktakeApproveOk"
                    text: "✔  اعتماد وشطب"
                    enabled: confirmCheck.checked && !!page.adjustVM && !page.adjustVM.busy
                    font.family: theme.fontFamily
                    onClicked: approveDialog.mode === "single"
                               ? page.adjustVM.writeOff([approveDialog.singleId], approveDialog.reasons[reasonBox.currentIndex].code, noteField.text, confirmCheck.checked)
                               : page.adjustVM.approve(page.vm.approvalPayload(), approveDialog.reasons[reasonBox.currentIndex].code, noteField.text, confirmCheck.checked)
                }
            }
        }
    }
}
