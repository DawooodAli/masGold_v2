import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// عهدة السوق (Market custody core): the OWNER takes cash from the Main Safe and Scrap (existing karat, physical grams) to the
// market and brings back what was not used. Everything shown is formatted by marketTripVM; no custody is computed here and no right is
// decided here (the service re-checks the OWNER right in every transaction). Shop balances and custody balances are always apart.
Item {
    id: page
    objectName: "marketTripsPage"
    property string pageTitle: ""

    readonly property var vm: typeof marketTripVM !== "undefined" ? marketTripVM : null
    readonly property var detail: vm ? vm.detail : ({})
    readonly property var ctx: vm ? vm.context : ({})
    readonly property bool hasSelection: !!vm && vm.selectedTripId !== "" && !!detail && detail.id !== undefined

    Theme { id: theme }

    onVisibleChanged: if (visible && vm && vm.canManage) { vm.clearMessage(); vm.refresh() }
    Component.onCompleted: if (visible && vm && vm.canManage) vm.refresh()

    Connections {
        target: page.vm
        function onEntryPosted(code) { entryDialog.close() }
        function onActionDone(operation) { reasonDialog.close() }
    }

    Rectangle { anchors.fill: parent; color: theme.bgPrimary }

    function ltr(text) {
        return text === undefined || text === null || text === "" ? "" : "⁦" + text + "⁩"
    }

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
        implicitHeight: 34
        topInset: 0
        bottomInset: 0
        leftInset: 0
        rightInset: 0
        topPadding: 0
        bottomPadding: 0
        leftPadding: 16
        rightPadding: 16
        hoverEnabled: true
        contentItem: Label {
            text: btn.text
            font: btn.font
            color: !btn.enabled ? theme.textDisabled : btn.primary ? theme.bgPrimary : btn.accent
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: theme.radiusSmall
            color: !btn.enabled ? (btn.primary ? theme.bgTertiary : "transparent")
                 : btn.primary ? (btn.down || btn.hovered ? Qt.lighter(btn.accent, 1.12) : btn.accent)
                 : btn.down ? theme.panelHover : btn.hovered ? theme.bgTertiary : "transparent"
            border.color: btn.visualFocus ? theme.goldBright : btn.enabled ? btn.accent : theme.border
            border.width: btn.visualFocus ? 2 : 1
        }
    }

    component Field: TextField {
        id: field
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        color: theme.textPrimary
        placeholderTextColor: theme.textDisabled
        horizontalAlignment: TextInput.AlignRight
        selectByMouse: true
        implicitHeight: 34
        hoverEnabled: true
        background: Rectangle { radius: 4; color: theme.bgPrimary
                                border.color: field.activeFocus ? theme.gold : field.hovered ? theme.goldDark : theme.border }
    }

    component Combo: ComboBox {
        id: combo
        font.family: "Cairo"
        font.pixelSize: theme.fontBody
        implicitHeight: 34
        hoverEnabled: true
        delegate: ItemDelegate {
            width: combo.width
            height: 34
            highlighted: combo.highlightedIndex === index
            contentItem: Label { text: modelData[combo.textRole]; font.family: "Cairo"; font.pixelSize: theme.fontBody
                                 color: theme.textPrimary; horizontalAlignment: Text.AlignRight
                                 verticalAlignment: Text.AlignVCenter; elide: Text.ElideLeft }
        }
        indicator: Label { x: 10; y: (combo.height - height) / 2; text: "▾"; color: theme.gold; font.pixelSize: theme.fontBodyStrong }
        contentItem: Label {
            text: combo.displayText
            font: combo.font
            color: theme.textPrimary
            horizontalAlignment: Text.AlignRight
            verticalAlignment: Text.AlignVCenter
            rightPadding: 10
            leftPadding: 30
            elide: Text.ElideLeft
        }
        background: Rectangle {
            radius: 4
            color: theme.bgPrimary
            border.color: combo.activeFocus || combo.popup.visible ? theme.gold : combo.hovered ? theme.goldDark : theme.border
        }
    }

    // A titled card (gold tick + title on the right).
    component Section: Rectangle {
        id: section
        default property alias content: sectionBody.data
        property string title: ""
        property color accent: theme.gold
        Layout.fillWidth: true
        implicitHeight: sectionColumn.implicitHeight + 20
        color: theme.panel
        radius: theme.radiusSmall
        border.color: theme.border
        ColumnLayout {
            id: sectionColumn
            anchors.fill: parent
            anchors.margins: 10
            spacing: 6
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Rectangle { implicitWidth: 3; implicitHeight: 16; radius: 2; color: section.accent }
                Txt { text: section.title; color: section.accent; font.bold: true; font.pixelSize: theme.fontBodyStrong }
                Item { Layout.fillWidth: true }
            }
            ColumnLayout { id: sectionBody; Layout.fillWidth: true; spacing: 4 }
        }
    }

    // One row of figures: a label and up to three values (issued / returned / outstanding).
    component FigureRow: RowLayout {
        property string label: ""
        property string issued: ""
        property string returned: ""
        property string outstanding: ""
        property bool zero: false
        Layout.fillWidth: true
        layoutDirection: Qt.RightToLeft
        spacing: 8
        Txt { Layout.preferredWidth: 96; text: parent.label; font.bold: true; elide: Text.ElideNone }
        Txt { Layout.fillWidth: true; Layout.preferredWidth: 1; text: parent.issued; elide: Text.ElideNone }
        Txt { Layout.fillWidth: true; Layout.preferredWidth: 1; text: parent.returned; elide: Text.ElideNone }
        Txt { objectName: "figureOutstanding"; Layout.fillWidth: true; Layout.preferredWidth: 1; text: parent.outstanding
              font.bold: true; color: parent.zero ? theme.textMuted : theme.amber; elide: Text.ElideNone }
    }

    // ---------------------------------------------------------------- page body
    RowLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10
        layoutDirection: Qt.RightToLeft
        visible: !!page.vm && page.vm.canManage

        // ---- trips list (physical right)
        Rectangle {
            Layout.preferredWidth: 270
            Layout.fillHeight: true
            color: theme.bgSecondary
            radius: theme.radiusSmall
            border.color: theme.border
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 10
                spacing: 8
                Txt { Layout.fillWidth: true; text: "عهدة السوق"; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection }
                Btn {
                    objectName: "newTripButton"
                    Layout.fillWidth: true
                    text: "＋  عهدة سوق جديدة"
                    primary: true
                    onClicked: entryDialog.openFor("ISSUE", "")
                }
                ListView {
                    id: tripList
                    objectName: "tripList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 5
                    model: page.vm ? page.vm.trips : []
                    delegate: Rectangle {
                        width: tripList.width
                        height: Math.max(58, tripRow.implicitHeight + 14)
                        radius: theme.radiusSmall
                        color: modelData.selected ? theme.panelHover : theme.panel
                        border.color: modelData.selected ? theme.gold : theme.border
                        MouseArea { anchors.fill: parent; onClicked: page.vm.selectTrip(modelData.id) }
                        ColumnLayout {
                            id: tripRow
                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 10; anchors.rightMargin: 10
                            spacing: 2
                            RowLayout {
                                Layout.fillWidth: true
                                layoutDirection: Qt.RightToLeft
                                Txt { text: page.ltr(modelData.code); font.bold: true; elide: Text.ElideNone }
                                Item { Layout.fillWidth: true }
                                Txt { text: modelData.statusText; color: modelData.status === "OPEN" ? theme.green : theme.textMuted
                                      elide: Text.ElideNone }
                            }
                            Txt { Layout.fillWidth: true; text: modelData.outstandingText; color: theme.textSecondary
                                  font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                        }
                    }
                    Txt { anchors.centerIn: parent; visible: tripList.count === 0; text: "لا توجد عهدة سوق مسجلة بعد"; color: theme.textMuted }
                }
            }
        }

        // ---- the selected trip (physical left)
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8

            Txt { objectName: "tripMessage"; Layout.fillWidth: true; visible: !!page.vm && page.vm.message !== ""
                  text: page.vm ? page.vm.message : ""; color: theme.green; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            Txt { objectName: "tripError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== "" && !entryDialog.visible && !reasonDialog.visible
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }

            Txt {
                Layout.fillWidth: true
                visible: !page.hasSelection
                text: "اختر عهدة من القائمة أو ابدأ عهدة سوق جديدة."
                color: theme.textMuted
            }

            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: page.hasSelection
                contentHeight: detailColumn.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: detailColumn
                    width: parent.width - 12
                    spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Txt { objectName: "tripTitle"; text: "عهدة " + page.ltr(page.detail.code || ""); color: theme.gold; font.bold: true
                              font.pixelSize: theme.fontSection; elide: Text.ElideNone }
                        Txt { objectName: "tripStatus"; text: page.detail.statusText || ""; color: page.detail.open ? theme.green : theme.textMuted
                              font.bold: true; elide: Text.ElideNone }
                        Item { Layout.fillWidth: true }
                    }
                    Flow {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Btn { objectName: "issueMoreButton"; text: "إخراج عهدة"; visible: !!page.detail.open
                              enabled: !!page.vm && !page.vm.writing
                              onClicked: entryDialog.openFor("ISSUE", page.detail.id) }
                        Btn { objectName: "returnButton"; text: "تسجيل رجوع"; visible: !!page.detail.open
                              enabled: !!page.vm && !page.vm.writing
                              onClicked: entryDialog.openFor("RETURN", page.detail.id) }
                        Btn { objectName: "closeTripButton"; text: "إغلاق العهدة"; visible: !!page.detail.open; accent: theme.green
                              enabled: !!page.detail.closable && !!page.vm && !page.vm.writing
                              onClicked: page.vm.closeTrip(page.detail.id) }
                        Btn { objectName: "reopenTripButton"; text: "إعادة فتح العهدة"; visible: page.detail.open === false; accent: theme.amber
                              enabled: !!page.vm && !page.vm.writing
                              onClicked: reasonDialog.openFor("REOPEN", page.detail.id, "") }
                    }
                    Txt { objectName: "closeHint"; Layout.fillWidth: true; visible: !!page.detail.open && !!page.detail.closeHint
                          text: page.detail.closeHint || ""; color: page.detail.closable ? theme.green : theme.amber
                          wrapMode: Text.WordWrap; elide: Text.ElideNone }

                    // CUSTODY (outside the shop) and SHOP balances are two different things: never mixed.
                    Section {
                        title: "في العهدة (خارج المحل)"
                        accent: theme.amber
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 8
                            Item { Layout.preferredWidth: 96 }
                            Txt { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "أُخرج"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                            Txt { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "رجع"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                            Txt { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "المتبقي"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                        }
                        FigureRow {
                            objectName: "custodyCashRow"
                            label: "النقدية"
                            issued: page.detail.cash ? page.detail.cash.issued : ""
                            returned: page.detail.cash ? page.detail.cash.returned : ""
                            outstanding: page.detail.cash ? page.detail.cash.outstanding : ""
                            zero: page.detail.cash ? page.detail.cash.zero : true
                        }
                        Repeater {
                            model: page.detail.gold || []
                            delegate: FigureRow {
                                objectName: "custodyGoldRow_" + modelData.karat
                                label: "ذهب عيار " + modelData.karat
                                issued: modelData.issued + " جرام"
                                returned: modelData.returned + " جرام"
                                outstanding: modelData.outstanding + " جرام"
                                zero: modelData.zero
                            }
                        }
                    }
                    Section {
                        title: "رصيد المحل (لا يشمل العهدة)"
                        Txt { objectName: "shopCash"; Layout.fillWidth: true
                              text: "الخزنة الرئيسية: " + (page.ctx.safeBalanceText || "—"); elide: Text.ElideNone }
                        Repeater {
                            model: page.ctx.shop || []
                            delegate: Txt { Layout.fillWidth: true; text: modelData.text; color: theme.textSecondary; elide: Text.ElideNone }
                        }
                    }
                    Txt { objectName: "tripLimitation"; Layout.fillWidth: true; text: page.vm ? page.vm.limitation : ""
                          color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }

                    Section {
                        title: "سجل الحركات"
                        Repeater {
                            model: page.detail.entries || []
                            delegate: Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: Math.max(52, entryRow.implicitHeight + 14)
                                radius: theme.radiusSmall
                                color: theme.bgTertiary
                                border.color: theme.border
                                RowLayout {
                                    id: entryRow
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                    anchors.leftMargin: 10; anchors.rightMargin: 10
                                    layoutDirection: Qt.RightToLeft
                                    spacing: 10
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 2
                                        Txt { Layout.fillWidth: true; text: modelData.kindText + "  " + page.ltr(modelData.code); font.bold: true
                                              color: modelData.kind === "ISSUE" ? theme.amber : theme.green; elide: Text.ElideNone }
                                        Txt { objectName: "entryWhat"; Layout.fillWidth: true; text: modelData.what; wrapMode: Text.WordWrap
                                              elide: Text.ElideNone }
                                        Txt { Layout.fillWidth: true; text: modelData.date + "  •  " + modelData.time + "  •  " + modelData.actor
                                              color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                        Txt { objectName: "entryState"; Layout.fillWidth: true; visible: modelData.state !== ""
                                              text: modelData.state; color: theme.amber; font.pixelSize: theme.fontTableHeader
                                              wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                    }
                                    Btn {
                                        objectName: "reverseEntryButton"
                                        visible: !!page.vm && modelData.canReverse === true
                                        text: "عكس"
                                        accent: theme.red
                                        onClicked: reasonDialog.openFor("REVERSE", modelData.id, modelData.code)
                                    }
                                }
                            }
                        }
                        Repeater {
                            model: page.detail.events || []
                            delegate: Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                            text: modelData.text + "  •  " + modelData.time + (modelData.actor ? "  •  " + modelData.actor : "")
                                                  + (modelData.reason ? "  •  " + modelData.reason : "")
                                            wrapMode: Text.WordWrap; elide: Text.ElideNone }
                        }
                    }
                }
            }
        }
    }

    Txt {
        anchors.centerIn: parent
        visible: !page.vm || !page.vm.canManage
        text: "عهدة السوق للمالك فقط."
        color: theme.textMuted
    }

    // ---------------------------------------------------------------- issue / return entry
    Popup {
        id: entryDialog
        objectName: "tripEntryDialog"
        property string mode: "ISSUE"
        property string tripId: ""
        property string requestKey: ""
        readonly property bool isReturn: mode === "RETURN"
        readonly property var karatChoices: [{ id: "", text: "اختر العيار" }].concat(page.ctx.karats || [])
        readonly property bool hasQuantity: cashField.text.trim().length > 0 || hasGold()
        function hasGold() {
            for (var i = 0; i < goldModel.count; ++i)
                if (goldModel.get(i).weight.trim().length > 0 && goldModel.get(i).karatIndex > 0) return true
            return false
        }
        function collect() {
            var rows = []
            for (var i = 0; i < goldModel.count; ++i) {
                var g = goldModel.get(i)
                if (g.weight.trim().length > 0 || g.karatIndex > 0)
                    rows.push({ karatId: g.karatIndex > 0 ? karatChoices[g.karatIndex].id : "", weight: g.weight })
            }
            return rows
        }
        function openFor(kind, trip) {
            if (!page.vm) return
            mode = kind
            tripId = trip
            requestKey = page.vm.newRequestKey()
            cashField.text = ""
            dateField.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            notesField.text = ""
            goldModel.clear()
            goldModel.append({ karatIndex: 0, weight: "" })
            page.vm.clearMessage()
            page.vm.refresh()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(560, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 620, 600)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        ListModel { id: goldModel }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection
                  text: entryDialog.isReturn ? "تسجيل رجوع من العهدة" : (entryDialog.tripId === "" ? "عهدة سوق جديدة" : "إخراج عهدة") }
            Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: entryDialog.isReturn ? "يُعاد المبلغ إلى الخزنة الرئيسية والذهب إلى كسر المحل بنفس العيار. لا يتجاوز الرجوع المتبقي في العهدة."
                                              : "تُخصم النقدية من الخزنة الرئيسية مرة واحدة ويُخرج الذهب من كسر المحل المتاح فقط (دون المحجوز لمراجعة المدير). كلاهما عهدة وليس دفعة." }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: entryColumn.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: entryColumn
                    width: parent.width - 12
                    spacing: 6
                    Txt { Layout.fillWidth: true; visible: entryDialog.isReturn && !!page.detail.outstandingText
                          text: "المتبقي في العهدة: " + (page.detail.outstandingText || ""); color: theme.amber
                          wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    Txt { Layout.fillWidth: true; visible: !entryDialog.isReturn
                          text: "الخزنة الرئيسية: " + (page.ctx.safeBalanceText || "—"); color: theme.textSecondary; elide: Text.ElideNone }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "النقدية (ج)" }
                            Field { id: cashField; objectName: "tripCashField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhFormattedNumbersOnly }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "التاريخ (yyyy-MM-dd) *" }
                            Field { id: dateField; objectName: "tripDateField"; Layout.fillWidth: true }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "الذهب (كسر) بالعيار والوزن الفعلي بالجرام"; color: theme.gold; font.bold: true }
                    Repeater {
                        model: goldModel
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 8
                            Combo {
                                objectName: "tripGoldKarat_" + index
                                Layout.preferredWidth: 150
                                model: entryDialog.karatChoices
                                textRole: "text"
                                currentIndex: karatIndex
                                onActivated: function (i) { goldModel.setProperty(index, "karatIndex", i) }
                            }
                            Field {
                                objectName: "tripGoldWeight_" + index
                                Layout.fillWidth: true
                                placeholderText: "الوزن (جرام)"
                                text: weight
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                onTextEdited: goldModel.setProperty(index, "weight", text)
                            }
                        }
                    }
                    Btn { objectName: "addGoldRowButton"; text: "＋ عيار آخر"; accent: theme.textSecondary
                          enabled: goldModel.count < 4
                          onClicked: goldModel.append({ karatIndex: 0, weight: "" }) }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: notesField; objectName: "tripNotesField"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "tripEntryError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmTripEntryButton"
                    text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : (entryDialog.isReturn ? "تسجيل الرجوع" : "إخراج العهدة")
                    primary: true
                    enabled: entryDialog.hasQuantity && dateField.text.trim().length > 0 && !!page.vm && !page.vm.writing
                    onClicked: {
                        if (entryDialog.isReturn)
                            page.vm.returnCustody(entryDialog.tripId, cashField.text, entryDialog.collect(), dateField.text,
                                                  notesField.text, entryDialog.requestKey)
                        else
                            page.vm.issue(entryDialog.tripId, cashField.text, entryDialog.collect(), dateField.text,
                                          notesField.text, entryDialog.requestKey)
                    }
                }
                Btn { objectName: "cancelTripEntryButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.writing; onClicked: entryDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- reason (reopen a closed trip / reverse an entry)
    Popup {
        id: reasonDialog
        objectName: "tripReasonDialog"
        property string mode: "REVERSE"
        property string targetId: ""
        property string targetCode: ""
        function openFor(kind, id, code) {
            mode = kind
            targetId = id
            targetCode = code
            reasonField.text = ""
            if (page.vm) page.vm.clearMessage()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(480, page.width - 24)
        modal: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: reasonDialog.mode === "REVERSE" ? theme.red : theme.amber }
        contentItem: ColumnLayout {
            spacing: 8
            Txt { Layout.fillWidth: true; font.bold: true; font.pixelSize: theme.fontSection
                  color: reasonDialog.mode === "REVERSE" ? theme.red : theme.amber
                  text: reasonDialog.mode === "REVERSE" ? "عكس الحركة " + page.ltr(reasonDialog.targetCode) : "إعادة فتح العهدة" }
            Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: reasonDialog.mode === "REVERSE"
                        ? "تبقى الحركة الأصلية في السجل وتُسجل حركات معاكسة مرتبطة بها. لا يُعكس إخراج بعده رجوع قائم، ولا تُعكس حركة في عهدة مغلقة."
                        : "تعود العهدة مفتوحة لإخراج عهدة أو تسجيل رجوع أو عكس. لا تتغير أي أرصدة." }
            Txt { Layout.fillWidth: true; text: "السبب *" }
            Field { id: reasonField; objectName: "tripReasonField"; Layout.fillWidth: true }
            Txt { objectName: "tripReasonError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmTripReasonButton"
                    text: !!page.vm && page.vm.writing ? "جارٍ التنفيذ…" : (reasonDialog.mode === "REVERSE" ? "عكس الحركة" : "إعادة الفتح")
                    accent: reasonDialog.mode === "REVERSE" ? theme.red : theme.amber
                    primary: true
                    enabled: reasonField.text.trim().length > 0 && !!page.vm && !page.vm.writing
                    onClicked: reasonDialog.mode === "REVERSE" ? page.vm.reverseEntry(reasonDialog.targetId, reasonField.text)
                                                                : page.vm.reopenTrip(reasonDialog.targetId, reasonField.text)
                }
                Btn { objectName: "cancelTripReasonButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.writing; onClicked: reasonDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }
}
