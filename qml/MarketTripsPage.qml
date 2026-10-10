import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// عهدة السوق (Market custody): the OWNER takes cash from the Main Safe and Scrap (existing karat, physical grams, with an explicit
// designation للسبك / لوجو) to the market, melts designated gold into assayed lots and brings back what was not used. Everything shown is formatted by marketTripVM; no custody is computed here and no right is
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
        function onEntryPosted(code) { entryDialog.close(); meltDialog.close(); lotReturnDialog.close() }
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

    // One row of figures: a label and four values (issued / returned / melted / outstanding).
    component FigureRow: RowLayout {
        property string label: ""
        property string issued: ""
        property string returned: ""
        property string melted: ""
        property string outstanding: ""
        property bool zero: false
        Layout.fillWidth: true
        layoutDirection: Qt.RightToLeft
        spacing: 8
        Txt { Layout.preferredWidth: 150; text: parent.label; font.bold: true; elide: Text.ElideNone }
        Txt { Layout.fillWidth: true; Layout.preferredWidth: 1; text: parent.issued; elide: Text.ElideNone }
        Txt { Layout.fillWidth: true; Layout.preferredWidth: 1; text: parent.returned; elide: Text.ElideNone }
        Txt { Layout.fillWidth: true; Layout.preferredWidth: 1; text: parent.melted; elide: Text.ElideNone }
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
                // ناتج السبك بالمحل: lots the shop holds after a return from the market. Apart from the Scrap strip, the karat totals and custody.
                Rectangle {
                    objectName: "shopLotsSection"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 250
                    color: theme.panel
                    radius: theme.radiusSmall
                    border.color: theme.border
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 4
                        Txt { Layout.fillWidth: true; text: "ناتج السبك بالمحل"; color: theme.gold; font.bold: true; font.pixelSize: theme.fontBodyStrong }
                        Txt { objectName: "shopLotSummary"; Layout.fillWidth: true; visible: !!page.vm && page.vm.shopLotSummary !== ""
                              text: page.vm ? page.vm.shopLotSummary : ""; color: theme.textSecondary; font.pixelSize: theme.fontTableHeader
                              wrapMode: Text.WordWrap; elide: Text.ElideNone }
                        ListView {
                            id: shopLotList
                            objectName: "shopLotList"
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            spacing: 4
                            model: page.vm ? page.vm.shopLots : []
                            delegate: Rectangle {
                                width: shopLotList.width
                                height: shopLotCol.implicitHeight + 10
                                radius: theme.radiusSmall
                                color: theme.bgTertiary
                                border.color: theme.border
                                ColumnLayout {
                                    id: shopLotCol
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                    anchors.leftMargin: 8; anchors.rightMargin: 8
                                    spacing: 1
                                    Txt { objectName: "shopLotRef"; Layout.fillWidth: true; text: page.ltr(modelData.ref) + "  " + modelData.kindText
                                          font.bold: true; font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone; wrapMode: Text.WordWrap }
                                    Txt { objectName: "shopLotFigures"; Layout.fillWidth: true; font.pixelSize: theme.fontTableHeader
                                          text: modelData.weight + " جرام • العيار الفعلي " + modelData.fineness + " • صافي " + page.ltr(modelData.pure)
                                          color: theme.textSecondary; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                }
                            }
                            Txt { anchors.centerIn: parent; visible: shopLotList.count === 0; text: "لا يوجد ناتج سبك بالمحل"; color: theme.textMuted }
                        }
                    }
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
            Txt { objectName: "tripError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== "" && !entryDialog.visible && !reasonDialog.visible && !meltDialog.visible
                           && !lotReturnDialog.visible
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
                        Btn { objectName: "lotReturnButton"; text: "رجوع ناتج السبك"; visible: !!page.detail.open
                              enabled: !!page.detail.lotReturnAvailable && !!page.vm && !page.vm.writing
                              onClicked: lotReturnDialog.openFor(page.detail.id) }
                        Btn { objectName: "meltButton"; text: "سبك"; visible: !!page.detail.open
                              enabled: !!page.detail.meltAvailable && !!page.vm && !page.vm.writing
                              onClicked: meltDialog.openFor(page.detail.id) }
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
                            Item { Layout.preferredWidth: 150 }
                            Txt { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "أُخرج"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                            Txt { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "رجع"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                            Txt { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "سُبك"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                            Txt { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "المتبقي"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                        }
                        FigureRow {
                            objectName: "custodyCashRow"
                            label: "النقدية"
                            issued: page.detail.cash ? page.detail.cash.issued : ""
                            returned: page.detail.cash ? page.detail.cash.returned : ""
                            melted: "—"
                            outstanding: page.detail.cash ? page.detail.cash.outstanding : ""
                            zero: page.detail.cash ? page.detail.cash.zero : true
                        }
                        Repeater {
                            model: page.detail.gold || []
                            delegate: FigureRow {
                                objectName: "custodyGoldRow_" + modelData.karat + "_" + modelData.designation
                                label: "ذهب " + modelData.label
                                issued: modelData.issued + " جرام"
                                returned: modelData.returned + " جرام"
                                melted: modelData.melted + " جرام"
                                outstanding: modelData.outstanding + " جرام"
                                zero: modelData.zero
                            }
                        }
                    }
                    // Assayed lots made by melting: never mixed with master-karat custody.
                    Section {
                        objectName: "assayedLotsSection"
                        title: "شحنات السبك المعايَرة (منفصلة عن عهدة العيارات)"
                        accent: theme.gold
                        visible: (page.detail.lots || []).length > 0
                        Repeater {
                            model: page.detail.lots || []
                            delegate: Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: lotRow.implicitHeight + 12
                                radius: theme.radiusSmall
                                color: theme.bgTertiary
                                border.color: theme.border
                                ColumnLayout {
                                    id: lotRow
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                    anchors.leftMargin: 10; anchors.rightMargin: 10
                                    spacing: 2
                                    Txt { objectName: "lotTitle"; Layout.fillWidth: true; text: modelData.title; font.bold: true; elide: Text.ElideNone }
                                    Txt { objectName: "lotFigures"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                          text: "المتبقي " + modelData.outstanding + " جرام • الوزن " + modelData.weight + " • العيار الفعلي "
                                                + modelData.fineness + " • الذهب الصافي " + page.ltr(modelData.pure)
                                          color: theme.textSecondary }
                                }
                            }
                        }
                        Txt { objectName: "lotNote"; Layout.fillWidth: true; text: page.detail.lotNote || ""; color: theme.amber
                              font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
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
                                              color: modelData.kind === "ISSUE" ? theme.amber : modelData.kind === "MELT" ? theme.gold : theme.green
                                              elide: Text.ElideNone }
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
                    Section {
                        objectName: "meltsSection"
                        title: "سجل السبك والتحليل (دائم)"
                        accent: theme.gold
                        visible: (page.detail.melts || []).length > 0
                        Repeater {
                            model: page.detail.melts || []
                            delegate: Rectangle {
                                Layout.fillWidth: true
                                id: meltCard
                                readonly property bool dead: modelData.reversed === true
                                implicitHeight: meltColumn.implicitHeight + 14
                                radius: theme.radiusSmall
                                // A reversed melt is history only: a neutral card (no accent colours) whose text stays readable.
                                color: meltCard.dead ? theme.bgSecondary : theme.bgTertiary
                                border.color: theme.border
                                ColumnLayout {
                                    id: meltColumn
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                    anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 7
                                    spacing: 3
                                    Txt { objectName: "meltTitle"; Layout.fillWidth: true; font.bold: true; elide: Text.ElideNone
                                          color: meltCard.dead ? theme.textSecondary : theme.textPrimary
                                          text: "سبك " + page.ltr(modelData.code) + "  •  " + modelData.workshop + "  •  " + modelData.date }
                                    Txt { objectName: "meltReversedNotice"; Layout.fillWidth: true; visible: modelData.notice !== ""; text: modelData.notice
                                          color: theme.textPrimary; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                    Txt { objectName: "meltState"; Layout.fillWidth: true; visible: modelData.state !== ""
                                          text: modelData.state + (modelData.reason !== "" ? "  •  السبب: " + modelData.reason : "")
                                          color: theme.textSecondary; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                    Txt { objectName: "meltInputs"; Layout.fillWidth: true; text: "المدخلات: " + modelData.inputsText
                                          wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                    Repeater {
                                        model: modelData.lots
                                        delegate: Txt { objectName: "meltLot"; Layout.fillWidth: true; text: modelData.title + ": " + modelData.text
                                                        color: theme.textSecondary; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                    }
                                    Txt { Layout.fillWidth: true; visible: modelData.assay !== ""; text: "مرجع المعايرة: " + modelData.assay
                                          color: theme.textMuted; font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone }
                                    Txt { Layout.fillWidth: true; visible: modelData.gainExplanation !== ""; text: "سبب زيادة الوزن: " + modelData.gainExplanation
                                          color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                    Repeater {
                                        model: modelData.analysis
                                        delegate: RowLayout {
                                            Layout.fillWidth: true
                                            layoutDirection: Qt.RightToLeft
                                            spacing: 8
                                            Txt { Layout.preferredWidth: 250; text: modelData.label; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                                  elide: Text.ElideNone }
                                            Txt { objectName: "meltAnalysisValue"; Layout.fillWidth: true; text: modelData.value; wrapMode: Text.WordWrap
                                                  color: meltCard.dead ? theme.textSecondary
                                                         : modelData.value.indexOf("غير مكتمل") === 0 ? theme.amber : theme.textPrimary
                                                  elide: Text.ElideNone }
                                        }
                                    }
                                    Item { Layout.preferredHeight: 4 }
                                }
                            }
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
        readonly property var designationChoices: [{ id: "", text: "اختر الوجهة" }].concat(page.ctx.designations || [])
        readonly property var lineChoices: [{ key: "", text: "اختر من العهدة" }].concat(page.detail.returnLines || [])
        readonly property bool hasQuantity: cashField.text.trim().length > 0 || hasGold()
        function hasGold() {
            for (var i = 0; i < goldModel.count; ++i)
                if (goldModel.get(i).weight.trim().length > 0) return true
            return false
        }
        function collect() {
            var rows = []
            for (var i = 0; i < goldModel.count; ++i) {
                var g = goldModel.get(i)
                if (isReturn) {
                    if (g.weight.trim().length > 0 || g.lineIndex > 0) {
                        var line = g.lineIndex > 0 ? lineChoices[g.lineIndex] : null
                        rows.push({ karatId: line ? line.karatId : "", designation: line ? line.designation : "", weight: g.weight })
                    }
                } else if (g.weight.trim().length > 0 || g.karatIndex > 0 || g.designationIndex > 0) {
                    rows.push({ karatId: g.karatIndex > 0 ? karatChoices[g.karatIndex].id : "",
                                designation: g.designationIndex > 0 ? designationChoices[g.designationIndex].id : "", weight: g.weight })
                }
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
            goldModel.append({ karatIndex: 0, designationIndex: 0, lineIndex: 0, weight: "" })
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
                                              : "تُخصم النقدية من الخزنة الرئيسية مرة واحدة ويُخرج الذهب من كسر المحل المتاح فقط (دون المحجوز لمراجعة المدير). حدّد وجهة كل سطر ذهب: للسبك أو لوجو. كلاهما عهدة وليس دفعة." }
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
                    Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true
                          text: entryDialog.isReturn ? "الذهب المُرجَع (اختر السطر من العهدة ثم الوزن)" : "الذهب (كسر): العيار والوجهة والوزن الفعلي بالجرام" }
                    Repeater {
                        model: goldModel
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 8
                            Combo {
                                objectName: "tripGoldLine_" + index
                                visible: entryDialog.isReturn
                                Layout.preferredWidth: entryDialog.isReturn ? 270 : 0
                                model: entryDialog.lineChoices
                                textRole: "text"
                                currentIndex: lineIndex
                                onActivated: function (i) { goldModel.setProperty(index, "lineIndex", i) }
                            }
                            Combo {
                                objectName: "tripGoldKarat_" + index
                                visible: !entryDialog.isReturn
                                Layout.preferredWidth: entryDialog.isReturn ? 0 : 130
                                model: entryDialog.karatChoices
                                textRole: "text"
                                currentIndex: karatIndex
                                onActivated: function (i) { goldModel.setProperty(index, "karatIndex", i) }
                            }
                            Combo {
                                objectName: "tripGoldDesignation_" + index
                                visible: !entryDialog.isReturn
                                Layout.preferredWidth: entryDialog.isReturn ? 0 : 130
                                model: entryDialog.designationChoices
                                textRole: "text"
                                currentIndex: designationIndex
                                onActivated: function (i) { goldModel.setProperty(index, "designationIndex", i) }
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
                    Btn { objectName: "addGoldRowButton"; text: "＋ سطر ذهب آخر"; accent: theme.textSecondary
                          enabled: goldModel.count < 8
                          onClicked: goldModel.append({ karatIndex: 0, designationIndex: 0, lineIndex: 0, weight: "" }) }
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

    // ---------------------------------------------------------------- melting: designated custody -> assayed lots
    Popup {
        id: meltDialog
        objectName: "tripMeltDialog"
        property string tripId: ""
        property string requestKey: ""
        readonly property var stockChoices: [{ karatId: "", text: "اختر العيار" }].concat(page.detail.meltStock || [])
        function openFor(trip) {
            if (!page.vm) return
            tripId = trip
            requestKey = page.vm.newRequestKey()
            meltDateField.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            workshopField.text = ""
            assayField.text = ""
            outputWeightField.text = ""
            outputFinenessField.text = ""
            gainField.text = ""
            meltNotesField.text = ""
            inputModel.clear()
            inputModel.append({ karatIndex: 0, weight: "" })
            residueModel.clear()
            page.vm.clearMessage()
            page.vm.refresh()
            open()
        }
        function inputs() {
            var rows = []
            for (var i = 0; i < inputModel.count; ++i) {
                var g = inputModel.get(i)
                if (g.weight.trim().length > 0 || g.karatIndex > 0)
                    rows.push({ karatId: g.karatIndex > 0 ? stockChoices[g.karatIndex].karatId : "", weight: g.weight })
            }
            return rows
        }
        function residues() {
            var rows = []
            for (var i = 0; i < residueModel.count; ++i) {
                var r = residueModel.get(i)
                if (r.weight.trim().length > 0 || r.fineness.trim().length > 0 || r.label.trim().length > 0)
                    rows.push({ weight: r.weight, fineness: r.fineness, label: r.label })
            }
            return rows
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(620, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 640, 640)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        ListModel { id: inputModel }
        ListModel { id: residueModel }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection; text: "سبك ذهب من العهدة" }
            Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "يُسبك ذهب العهدة المخصص «للسبك» فقط (أي عيار، بوزنه الفعلي) ولا يُستهلك ذهب «لوجو» ولا كسر المحل. لا تتحرك أي نقدية ولا حساب مورد. يُحفظ فرق الوزن والذهب الصافي للتحليل نهائيًا." }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: meltColumnForm.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: meltColumnForm
                    width: parent.width - 12
                    spacing: 6
                    Txt { objectName: "meltStockText"; Layout.fillWidth: true; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone
                          text: "المتاح للسبك: " + ((page.detail.meltStock || []).map(function (m) { return m.text }).join(" • ") || "لا يوجد") }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "تاريخ السبك (yyyy-MM-dd) *" }
                            Field { id: meltDateField; objectName: "meltDateField"; Layout.fillWidth: true }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "الورشة / المسؤول *" }
                            Field { id: workshopField; objectName: "meltWorkshopField"; Layout.fillWidth: true }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "مدخلات السبك: العيار والوزن الفعلي بالجرام"; color: theme.gold; font.bold: true }
                    Repeater {
                        model: inputModel
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 8
                            Combo {
                                objectName: "meltInputKarat_" + index
                                Layout.preferredWidth: 260
                                model: meltDialog.stockChoices
                                textRole: "text"
                                currentIndex: karatIndex
                                onActivated: function (i) { inputModel.setProperty(index, "karatIndex", i) }
                            }
                            Field {
                                objectName: "meltInputWeight_" + index
                                Layout.fillWidth: true
                                placeholderText: "الوزن (جرام)"
                                text: weight
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                onTextEdited: inputModel.setProperty(index, "weight", text)
                            }
                        }
                    }
                    Btn { objectName: "addMeltInputButton"; text: "＋ عيار آخر"; accent: theme.textSecondary
                          enabled: inputModel.count < 6
                          onClicked: inputModel.append({ karatIndex: 0, weight: "" }) }
                    Txt { Layout.fillWidth: true; text: "الناتج الرئيسي (معايَر)"; color: theme.gold; font.bold: true }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "الوزن الفعلي (جرام) *" }
                            Field { id: outputWeightField; objectName: "meltOutputWeight"; Layout.fillWidth: true; inputMethodHints: Qt.ImhFormattedNumbersOnly }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "العيار الفعلي بالألف * (مثل 870 أو 870.5)" }
                            Field { id: outputFinenessField; objectName: "meltOutputFineness"; Layout.fillWidth: true; inputMethodHints: Qt.ImhFormattedNumbersOnly }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "رواسب قابلة للاسترداد (اختياري) — اترك العيار فارغًا إن كان غير معروف"; color: theme.gold; font.bold: true }
                    Repeater {
                        model: residueModel
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 8
                            Field {
                                objectName: "meltResidueLabel_" + index
                                Layout.preferredWidth: 130
                                placeholderText: "الوصف"
                                text: label
                                onTextEdited: residueModel.setProperty(index, "label", text)
                            }
                            Field {
                                objectName: "meltResidueWeight_" + index
                                Layout.fillWidth: true
                                placeholderText: "الوزن (جرام)"
                                text: weight
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                onTextEdited: residueModel.setProperty(index, "weight", text)
                            }
                            Field {
                                objectName: "meltResidueFineness_" + index
                                Layout.fillWidth: true
                                placeholderText: "العيار بالألف (غير معروف)"
                                text: fineness
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                onTextEdited: residueModel.setProperty(index, "fineness", text)
                            }
                        }
                    }
                    Btn { objectName: "addResidueButton"; text: "＋ راسب"; accent: theme.textSecondary
                          enabled: residueModel.count < 10
                          onClicked: residueModel.append({ weight: "", fineness: "", label: "" }) }
                    Txt { Layout.fillWidth: true; text: "سبب زيادة الوزن (مطلوب إذا زاد وزن النواتج عن المدخلات)" }
                    Field { id: gainField; objectName: "meltGainField"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "مرجع المعايرة (اختياري)" }
                    Field { id: assayField; objectName: "meltAssayField"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: meltNotesField; objectName: "meltNotesField"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "meltEntryError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmMeltButton"
                    text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : "تسجيل السبك"
                    primary: true
                    enabled: meltDateField.text.trim().length > 0 && !!page.vm && !page.vm.writing
                    onClicked: page.vm.meltCustody(meltDialog.tripId, meltDialog.inputs(), meltDateField.text, workshopField.text,
                                                   outputWeightField.text, outputFinenessField.text, meltDialog.residues(),
                                                   assayField.text, gainField.text, meltNotesField.text, meltDialog.requestKey)
                }
                Btn { objectName: "cancelMeltButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.writing; onClicked: meltDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- return whole melt lots to the shop (ناتج السبك بالمحل)
    // A melt bar is indivisible: the dialog only SELECTS lots. Weight and assay are read-only; the service posts the full weight.
    Popup {
        id: lotReturnDialog
        objectName: "tripLotReturnDialog"
        property string tripId: ""
        property string requestKey: ""
        function openFor(trip) {
            if (!page.vm) return
            tripId = trip
            requestKey = page.vm.newRequestKey()
            lotReturnDate.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            lotReturnNotes.text = ""
            lotPickModel.clear()
            var lots = page.detail.returnableLots || []
            for (var i = 0; i < lots.length; ++i)
                lotPickModel.append({ lotId: lots[i].lotId, ref: lots[i].ref, kindText: lots[i].kindText, fineness: lots[i].fineness,
                                      weight: lots[i].weight, returnable: lots[i].returnable, reason: lots[i].reason, picked: false })
            page.vm.clearMessage()
            page.vm.refresh()
            open()
        }
        function lines() {
            var rows = []
            for (var i = 0; i < lotPickModel.count; ++i) {
                var g = lotPickModel.get(i)
                if (g.picked) rows.push({ lotId: g.lotId })
            }
            return rows
        }
        readonly property bool hasSelection: lines().length > 0
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(620, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 600, 600)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        ListModel { id: lotPickModel }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection; text: "رجوع ناتج السبك إلى المحل" }
            Txt { objectName: "lotReturnRule"; Layout.fillWidth: true; color: theme.amber; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "ترجع السبكة كاملة ولا يمكن إرجاع جزء منها" }
            Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "اختر الشحنات التي تعود إلى المحل: تدخل مخزون «ناتج السبك بالمحل» بنفس هويتها ووزنها الكامل وعيارها الفعلي، ولا تتحول إلى عيار 18/21/22/24 ولا تدخل كسر المحل. لا تتحرك نقدية ولا حساب مورد." }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: lotReturnColumn.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: lotReturnColumn
                    width: parent.width - 12
                    spacing: 6
                    Txt { Layout.fillWidth: true; text: "تاريخ الرجوع (yyyy-MM-dd) *" }
                    Field { id: lotReturnDate; objectName: "lotReturnDateField"; Layout.fillWidth: true }
                    Repeater {
                        model: lotPickModel
                        delegate: Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: lotReturnRow.implicitHeight + 12
                            radius: theme.radiusSmall
                            color: theme.bgTertiary
                            border.color: picked ? theme.gold : theme.border
                            RowLayout {
                                id: lotReturnRow
                                anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 10; anchors.rightMargin: 10
                                layoutDirection: Qt.RightToLeft
                                spacing: 10
                                CheckBox {
                                    objectName: "lotReturnSelect_" + index
                                    enabled: returnable && !!page.vm && !page.vm.writing
                                    checked: picked
                                    onToggled: lotPickModel.setProperty(index, "picked", checked)
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 2
                                    Txt { objectName: "lotReturnRef_" + index; Layout.fillWidth: true; font.bold: true; elide: Text.ElideNone
                                          text: page.ltr(ref) + "  " + kindText }
                                    Txt { objectName: "lotReturnInfo_" + index; Layout.fillWidth: true; color: theme.textSecondary; wrapMode: Text.WordWrap
                                          elide: Text.ElideNone
                                          text: "العيار الفعلي: " + fineness + " • الوزن الكامل: " + weight + " جرام • يتبقى في العهدة بعد الرجوع: 0.000" }
                                    Txt { objectName: "lotReturnReason_" + index; Layout.fillWidth: true; visible: !returnable; text: reason
                                          color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                }
                            }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: lotReturnNotes; objectName: "lotReturnNotesField"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "lotReturnError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmLotReturnButton"
                    text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : "إرجاع الشحنات المحددة"
                    primary: true
                    enabled: lotReturnDialog.hasSelection && lotReturnDate.text.trim().length > 0 && !!page.vm && !page.vm.writing
                    onClicked: page.vm.returnLots(lotReturnDialog.tripId, lotReturnDialog.lines(), lotReturnDate.text, lotReturnNotes.text,
                                                  lotReturnDialog.requestKey)
                }
                Btn { objectName: "cancelLotReturnButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.writing; onClicked: lotReturnDialog.close() }
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
                        ? "تبقى الحركة الأصلية في السجل وتُسجل حركات معاكسة مرتبطة بها، ويبقى تحليل السبك محفوظًا. لا يُعكس إخراج بعده رجوع أو سبك قائم، ولا يُعكس سبك رجعت شحنته إلى المحل قبل عكس الرجوع، ولا تُعكس حركة في عهدة مغلقة."
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
