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
    readonly property var recon: vm ? vm.reconciliation : ({})                       // R1: the READ-ONLY reconciliation preview of the selected BOOK trip
    property bool reconMore: false
    readonly property var ctx: vm ? vm.context : ({})
    readonly property bool hasSelection: !!vm && vm.selectedTripId !== "" && !!detail && detail.id !== undefined

    Theme { id: theme }

    // The archive (history + corrections) is closed whenever another trip is shown.
    property bool archiveOpen: false
    readonly property string archiveKey: detail && detail.id !== undefined ? String(detail.id) : ""
    onArchiveKeyChanged: archiveOpen = false

    // One short line per holding: what was handed over and to whom (no batch codes, assays or document numbers).
    function holdingSummary(row) {
        var party = row.party || ""
        var source = row.source || ""
        var text = row.text || ""
        var grams = ""
        var remaining = /المتبقي ([0-9.,]+) من/.exec(text)
        if (remaining) grams = remaining[1]
        else {
            var plain = /• ([0-9.,]+) جرام/.exec(text)
            if (plain) grams = plain[1]
        }
        var what = source.indexOf("لوجو") === 0 ? source : "سبيكة"
        return what + (grams !== "" ? " — " + grams + " جرام" : "") + " ← " + party
    }


    onVisibleChanged: if (visible && vm && vm.canManage) { vm.clearMessage(); vm.refresh() }
    Component.onCompleted: if (visible && vm && vm.canManage) vm.refresh()

    Connections {
        target: page.vm
        function onEntryPosted(code) { entryDialog.close(); bookDialog.close(); meltDialog.close(); lotReturnDialog.close(); lotIssueDialog.close(); heatDialog.close(); payDialog.close(); goldSettleDialog.close(); goldSaleDialog.close(); goldBuyDialog.close(); lotSettleDialog.close(); lotHoldDialog.close(); logoHoldDialog.close() }
        function onActionDone(operation) { reasonDialog.close(); if (operation === "reconcile") reconApprovalDialog.close() }
        function onChanged() {
            if (reconApprovalDialog.opened && (page.vm.selectedTripId !== reconApprovalDialog.tripId || !page.vm.reconciliationEnabled)) reconApprovalDialog.close()
        }
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
        Txt { Layout.fillWidth: true; Layout.preferredWidth: 1; text: parent.melted; elide: Text.ElideNone; wrapMode: Text.WordWrap }
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
                // «النشطة» = OPEN trips (default), «الأرشيف» = CLOSED trips: the same status, filtered; nothing is moved or deleted
                RowLayout {
                    Layout.fillWidth: true
                    layoutDirection: Qt.RightToLeft
                    spacing: 6
                    Btn {
                        objectName: "tripTabActive"
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        text: "النشطة"
                        primary: !!page.vm && page.vm.tripTab === "ACTIVE"
                        onClicked: page.vm.showTripTab("ACTIVE")
                    }
                    Btn {
                        objectName: "tripTabArchive"
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        text: "الأرشيف"
                        primary: !!page.vm && page.vm.tripTab === "ARCHIVE"
                        onClicked: page.vm.showTripTab("ARCHIVE")
                    }
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
                        objectName: "tripRowItem"
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
                                Txt { objectName: "tripModeBadge"; visible: modelData.modeText !== ""; text: modelData.modeText; color: theme.gold
                                      font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone }
                                Item { Layout.fillWidth: true }
                                Txt { text: modelData.statusText; color: modelData.status === "OPEN" ? theme.green : theme.textMuted
                                      elide: Text.ElideNone }
                            }
                            Txt { Layout.fillWidth: true; text: modelData.outstandingText; color: theme.textSecondary
                                  font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "tripClosedDate"; Layout.fillWidth: true; visible: modelData.closedDate !== ""; text: "أُغلقت: " + modelData.closedDate
                                  color: theme.textMuted; font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone }
                        }
                    }
                    Txt { objectName: "tripListEmpty"; anchors.centerIn: parent; visible: tripList.count === 0
                          text: !!page.vm && page.vm.tripTab === "ARCHIVE" ? "لا توجد عهدة مغلقة في الأرشيف" : "لا توجد عهدة نشطة"; color: theme.textMuted }
                }
                // pages of the shown tab (the filter and the order are applied before the page is cut)
                RowLayout {
                    objectName: "tripPager"
                    Layout.fillWidth: true
                    visible: !!page.vm && (page.vm.tripHasNext || page.vm.tripHasPrevious)
                    layoutDirection: Qt.RightToLeft
                    spacing: 6
                    Btn { objectName: "tripPagePrevious"; Layout.fillWidth: true; Layout.preferredWidth: 1; text: "السابقة"; enabled: !!page.vm && page.vm.tripHasPrevious
                          onClicked: page.vm.previousTripPage() }
                    Txt { objectName: "tripPageLabel"; text: "صفحة " + (page.vm ? page.vm.tripPage : 1); color: theme.textSecondary; elide: Text.ElideNone }
                    Btn { objectName: "tripPageNext"; Layout.fillWidth: true; Layout.preferredWidth: 1; text: "التالية"; enabled: !!page.vm && page.vm.tripHasNext
                          onClicked: page.vm.nextTripPage() }
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
                           && !lotReturnDialog.visible && !lotIssueDialog.visible && !heatDialog.visible && !payDialog.visible && !goldSettleDialog.visible && !lotSettleDialog.visible && !lotHoldDialog.visible && !logoHoldDialog.visible && !bookDialog.visible
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }

            Txt {
                Layout.fillWidth: true
                visible: !page.hasSelection
                text: "اختر عهدة من القائمة أو ابدأ عهدة سوق جديدة."
                color: theme.textMuted
            }

            Flickable {
                objectName: "tripDetailScroll"
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
                        Txt { objectName: "tripMode"; visible: !!page.detail.isBook; text: "دفتري"; color: theme.gold; font.bold: true; elide: Text.ElideNone }
                        Item { Layout.fillWidth: true }
                    }
                    // A BOOK trip: ONE gold number, the book balance in 21K equivalent (an accounting figure, not physical stock)
                    Section {
                        objectName: "bookBalanceSection"
                        title: "رصيد دفتري (مكافئ عيار 21)"
                        accent: theme.gold
                        visible: !!page.detail.isBook
                        Txt { objectName: "bookBalancePosted"; Layout.fillWidth: true; text: (page.detail.book && page.detail.book.balanceText) || ""
                              color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection; elide: Text.ElideNone }
                        Txt { objectName: "bookBalanceAnalytical"; Layout.fillWidth: true
                              text: "القيمة التحليلية الدقيقة: " + page.ltr((page.detail.book && page.detail.book.analytical) || "") + " • المتاح للحركة: " + ((page.detail.book && page.detail.book.available) || "")
                              color: theme.textSecondary; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                        Txt { objectName: "bookBalanceDrift"; Layout.fillWidth: true; text: (page.detail.book && page.detail.book.driftText) || ""
                              color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                        Txt { objectName: "bookBalanceNote"; Layout.fillWidth: true; text: (page.detail.book && page.detail.book.note) || ""
                              color: theme.amber; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                        Repeater {
                            model: (page.detail.book && page.detail.book.stockItems) || []
                            delegate: Txt { objectName: "bookStockItem"; Layout.fillWidth: true; text: "مخزون مرتجع بعيار فعلي: " + modelData.text
                                            color: theme.textSecondary; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                        }
                    }
                    // R1: the READ-ONLY reconciliation preview (مطابقة العهدة). It records, approves, saves and closes nothing; the trip closure stays unavailable.
                    Section {
                        objectName: "bookReconSection"
                        title: "مطابقة العهدة (معاينة فقط)"
                        accent: theme.gold
                        visible: !!page.detail.isBook
                        Txt { objectName: "bookReconLoading"; Layout.fillWidth: true; visible: !!page.recon.loading
                              text: "جارٍ تحميل المطابقة…"; color: theme.textMuted; elide: Text.ElideNone }
                        Txt { objectName: "bookReconError"; Layout.fillWidth: true; visible: !!page.recon.error; text: page.recon.error || ""
                              color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4
                            visible: !!page.recon.tripId && !page.recon.error && !page.recon.loading
                            Txt { Layout.fillWidth: true; text: "الرصيد التحليلي غير المسوّى"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone }
                            Txt { objectName: "bookReconA"; Layout.fillWidth: true; text: page.ltr(page.recon.aText || ""); color: theme.gold; font.bold: true
                                  font.pixelSize: theme.fontSection; elide: Text.ElideNone }
                            Txt { objectName: "bookReconANote"; Layout.fillWidth: true; text: page.recon.aNote || ""; color: theme.textSecondary
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { Layout.fillWidth: true; text: "الرصيد المرحّل"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone }
                            Txt { objectName: "bookReconB"; Layout.fillWidth: true; text: page.ltr(page.recon.bText || ""); color: theme.textPrimary; elide: Text.ElideNone }
                            Txt { Layout.fillWidth: true; text: "قيمة الفاقد المقترحة عند الاعتماد"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone }
                            Txt { objectName: "bookReconLoss"; Layout.fillWidth: true; text: page.ltr(page.recon.lossText || ""); color: theme.amber; font.bold: true; elide: Text.ElideNone }
                            Txt { objectName: "bookReconLossNote"; Layout.fillWidth: true; text: page.recon.lossNote || ""; color: theme.textMuted
                                  font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { Layout.fillWidth: true; text: "فرق تقريب الترحيل"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone }
                            Txt { objectName: "bookReconRounding"; Layout.fillWidth: true; text: page.ltr(page.recon.roundingText || "") + "  —  " + (page.recon.roundingNote || "")
                                  color: theme.textSecondary; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "bookReconEffect"; Layout.fillWidth: true; visible: !!page.recon.effectText; text: page.recon.effectText || ""; color: theme.amber
                                  font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "bookReconStatus"; Layout.fillWidth: true; text: page.recon.statusText || ""
                                  color: page.recon.trustworthy ? theme.green : theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Repeater {
                                model: page.recon.diagnostics || []
                                delegate: Txt { objectName: "bookReconDiagnostic"; Layout.fillWidth: true; text: modelData.text; color: theme.red
                                                font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            }
                            Txt { objectName: "bookReconClosureNote"; Layout.fillWidth: true; text: page.recon.closureNote || ""; color: theme.amber
                                  font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Btn { objectName: "openReconciliationApproval"; Layout.fillWidth: true; text: "مراجعة واعتماد التسوية"; primary: true
                                  visible: !!page.vm && page.vm.reconciliationEnabled
                                  enabled: !!page.vm && !page.vm.busy && !!page.detail.open && !!page.recon.trustworthy && !!page.recon.lossAvailable
                                  onClicked: reconApprovalDialog.openFor() }
                            Txt { objectName: "reconciliationReceipt"; Layout.fillWidth: true; visible: !!page.vm && page.vm.lastReconciliation.trip_id === page.vm.selectedTripId
                                  text: page.vm && page.vm.lastReconciliation.code ? "المستند " + page.ltr(page.vm.lastReconciliation.code) + " • التاريخ " + page.ltr(page.vm.lastReconciliation.effective_date) + "\nالرصيد التحليلي " + page.ltr(page.vm.lastReconciliation.a_star) + " • المرحّل " + page.ltr(page.vm.lastReconciliation.b_star)
                                       + " • الفاقد " + page.ltr(page.vm.lastReconciliation.loss_posted) + " • فرق التقريب " + page.ltr(page.vm.lastReconciliation.rounding_settlement)
                                       + " جرام مكافئ عيار 21\nالسبب: " + (page.vm.lastReconciliation.reason || "بدون سبب")
                                       + (page.vm.lastReconciliation.note ? " — " + page.vm.lastReconciliation.note : "") : ""
                                  color: theme.green; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Btn { objectName: "bookReconMoreToggle"; Layout.fillWidth: true; accent: theme.textMuted
                                  text: page.reconMore ? "إخفاء التفاصيل الحسابية" : "تفاصيل حسابية وحدود الفحص"; onClicked: page.reconMore = !page.reconMore }
                            ColumnLayout {
                                objectName: "bookReconMore"
                                Layout.fillWidth: true
                                spacing: 3
                                visible: page.reconMore
                                Txt { objectName: "bookReconDetails"; Layout.fillWidth: true; text: page.ltr(page.recon.detailsText || ""); color: theme.textSecondary
                                      font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                Txt { Layout.fillWidth: true; text: page.recon.countsText || ""; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone }
                                Txt { Layout.fillWidth: true; visible: !!page.recon.cashText; text: page.recon.cashText || ""; color: theme.textMuted
                                      font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone }
                                Repeater {
                                    model: page.recon.coverage || []
                                    delegate: Txt { objectName: "bookReconCoverage"; Layout.fillWidth: true; text: "• " + modelData; color: theme.textMuted
                                                    font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                }
                            }
                        }
                    }
                    // THE primary panel of the page: what is outside the shop now (cash and every karat / designation), right under the title.
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
                            Txt { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "سُبك / مدفوع"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                            Txt { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "المتبقي"; color: theme.textMuted; font.pixelSize: theme.fontTableHeader }
                        }
                        FigureRow {
                            objectName: "custodyCashRow"
                            label: "النقدية"
                            issued: page.detail.cash ? page.detail.cash.issued : ""
                            returned: page.detail.cash ? page.detail.cash.returned : ""
                            melted: page.detail.cash && (page.detail.cash.hasPaid || page.detail.cash.hasReceived || page.detail.cash.hasSpent)
                                    ? [page.detail.cash.hasPaid ? "مدفوع " + page.detail.cash.paid : "",
                                       page.detail.cash.hasReceived ? "من البيع +" + page.detail.cash.received : "",
                                       page.detail.cash.hasSpent ? "مشتريات −" + page.detail.cash.spent : ""].filter(function(t) { return t !== "" }).join(" • ")
                                    : "—"
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
                                melted: (modelData.heated !== "" ? "حمّية " + modelData.heated + " جرام" : modelData.melted + " جرام")
                                        + (modelData.settled !== "" ? " • سُلِّم لمورد " + modelData.settled + " جرام" : "")
                                        + (modelData.sold !== "" ? " • بيع " + modelData.sold + " جرام" : "")
                                        + (modelData.purchased !== "" ? " • مشترى +" + modelData.purchased + " جرام" : "")
                                outstanding: modelData.outstanding + " جرام"
                                zero: modelData.zero
                            }
                        }
                    }
                    Flow {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Btn { objectName: "bookIssueButton"; text: "إخراج ذهب من الكسر (دفتري)"; visible: !!page.detail.open && !!page.detail.isBook
                              enabled: !!page.vm && !page.vm.writing
                              onClicked: bookDialog.openFor("BK_ISSUE", page.detail.id) }
                        Btn { objectName: "bookBuyButton"; text: "شراء ذهب (دفتري)"; visible: !!page.detail.open && !!page.detail.isBook
                              enabled: !!page.vm && !page.vm.writing
                              onClicked: bookDialog.openFor("BK_BUY", page.detail.id) }
                        Btn { objectName: "bookSellButton"; text: "بيع ذهب (دفتري)"; visible: !!page.detail.open && !!page.detail.isBook
                              enabled: !!page.vm && !page.vm.writing
                              onClicked: bookDialog.openFor("BK_SELL", page.detail.id) }
                        Btn { objectName: "bookReturnButton"; text: "رجوع ذهب للمحل (دفتري)"; visible: !!page.detail.open && !!page.detail.isBook
                              enabled: !!page.vm && !page.vm.writing
                              onClicked: bookDialog.openFor("BK_RETURN", page.detail.id) }
                        Btn { objectName: "issueMoreButton"; text: page.detail.isBook ? "تحويل نقدية من الخزنة للعهدة" : "إخراج عهدة"; visible: !!page.detail.open
                              enabled: !!page.vm && !page.vm.writing
                              onClicked: entryDialog.openFor("ISSUE", page.detail.id) }
                        Btn { objectName: "returnButton"; text: page.detail.isBook ? "إرجاع نقدية إلى الخزنة" : "تسجيل رجوع"; visible: !!page.detail.open
                              enabled: !!page.vm && !page.vm.writing
                              onClicked: entryDialog.openFor("RETURN", page.detail.id) }
                        Btn { objectName: "lotIssueButton"; text: "إخراج سبكة من المحل"; visible: !!page.detail.open && !page.detail.isBook
                              enabled: !!page.vm && !page.vm.writing && page.vm.shopLots.length > 0
                              onClicked: lotIssueDialog.openFor(page.detail.id) }
                        Btn { objectName: "lotReturnButton"; text: "رجوع ناتج السبك"; visible: !!page.detail.open && !page.detail.isBook
                              enabled: !!page.detail.lotReturnAvailable && !!page.vm && !page.vm.writing
                              onClicked: lotReturnDialog.openFor(page.detail.id) }
                        Btn { objectName: "custodyLotSettleButton"; text: "تسوية سبكة من العهدة"; visible: !!page.detail.open && !page.detail.isBook
                              enabled: !!page.detail.lotSettleAvailable && !!page.vm && !page.vm.writing
                              onClicked: lotSettleDialog.openFor(page.detail.id) }
                        Btn { objectName: "custodyLotHoldButton"; text: "تسليم ذهب لمسوقجي"; visible: !!page.detail.holdVisible && !page.detail.isBook
                              enabled: !!page.detail.holdAvailable && !!page.vm && !page.vm.writing
                              onClicked: lotHoldDialog.openFor(page.detail.id) }
                        Btn { objectName: "custodyLogoHoldButton"; text: "تسليم لوجو لمسوقجي"; visible: !!page.detail.holdVisible && !page.detail.isBook
                              enabled: !!page.detail.logoHoldAvailable && !!page.vm && !page.vm.writing
                              onClicked: logoHoldDialog.openFor(page.detail.id) }
                        Btn { objectName: "custodyGoldBuyButton"; text: "شراء ذهب نقدي"; visible: !!page.detail.open && !page.detail.isBook
                              enabled: !!page.detail.buyAvailable && !!page.vm && !page.vm.writing
                              onClicked: goldBuyDialog.openFor(page.detail.id) }
                        Btn { objectName: "custodySaleButton"; text: "بيع ذهب من العهدة"; visible: !!page.detail.open && !page.detail.isBook
                              enabled: !!page.detail.saleAvailable && !!page.vm && !page.vm.writing
                              onClicked: goldSaleDialog.openFor(page.detail.id) }
                        Btn { objectName: "custodyPayButton"; text: "دفع مصنعيات من العهدة"; visible: !!page.detail.open
                              enabled: !!page.detail.payAvailable && !!page.vm && !page.vm.writing
                              onClicked: payDialog.openFor(page.detail.id) }
                        Btn { objectName: "custodyGoldSettleButton"; text: "تسوية ذهب من العهدة"; visible: !!page.detail.open && !page.detail.isBook
                              enabled: !!page.detail.goldSettleAvailable && !!page.vm && !page.vm.writing
                              onClicked: goldSettleDialog.openFor(page.detail.id) }
                        Btn { objectName: "heatButton"; text: "حمّية اللوجو"; visible: !!page.detail.open && !page.detail.isBook
                              enabled: !!page.detail.heatAvailable && !!page.vm && !page.vm.writing
                              onClicked: heatDialog.openFor(page.detail.id) }
                        Btn { objectName: "meltButton"; text: "سبك"; visible: !!page.detail.open && !page.detail.isBook
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
                    // Shop-owned gold that left this custody for an intermediary (مسوقجي): ONE read-only line per holding (what and to whom).
                    Section {
                        objectName: "externalHoldingsSection"
                        title: "ذهب لدى المسوقجي (ملك المحل)"
                        accent: theme.amber
                        visible: (page.detail.externalHoldings || []).length > 0
                        Repeater {
                            model: page.detail.externalHoldings || []
                            delegate: Txt { objectName: "externalHoldingRow"; Layout.fillWidth: true; text: page.holdingSummary(modelData)
                                            wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.textSecondary }
                        }
                    }

                    // ---- archive: the history and its corrections (the reverse actions live here); closed until it is asked for
                    Btn {
                        objectName: "archiveToggle"
                        Layout.fillWidth: true
                        accent: theme.gold
                        text: (page.archiveOpen ? "إخفاء سجل الحركات" : "سجل الحركات (عرض الحركات وعكس عملية)")
                              + "  •  " + ((page.detail.entries || []).length + (page.detail.heats || []).length + (page.detail.melts || []).length)
                        onClicked: page.archiveOpen = !page.archiveOpen
                    }
                    ColumnLayout {
                        id: archivePanel
                        objectName: "archivePanel"
                        Layout.fillWidth: true
                        visible: page.archiveOpen
                        spacing: 8

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
                                                  color: modelData.kind === "ISSUE" || modelData.kind === "LOT_ISSUE" ? theme.amber : modelData.kind === "MELT" ? theme.gold : theme.green
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
                                            onClicked: reasonDialog.openFor("REVERSE", modelData.id, modelData.code, modelData.reverseNote)
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
                            objectName: "heatsSection"
                            title: "سجل حمّية اللوجو والفروق (دائم)"
                            accent: theme.gold
                            visible: (page.detail.heats || []).length > 0
                            Txt { objectName: "heatTotals"; Layout.fillWidth: true; visible: (page.detail.heatTotals || []).length > 0
                                  text: "الفرق التراكمي للحمّيات الفعّالة — " + (page.detail.heatTotals || []).map(function (t) { return t.text }).join(" | ")
                                  color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Repeater {
                                model: page.detail.heats || []
                                delegate: Rectangle {
                                    Layout.fillWidth: true
                                    id: heatCard
                                    readonly property bool dead: modelData.reversed === true
                                    implicitHeight: heatColumn.implicitHeight + 14
                                    radius: theme.radiusSmall
                                    // A reversed heat is history only: a neutral card whose measurements stay readable.
                                    color: heatCard.dead ? theme.bgSecondary : theme.bgTertiary
                                    border.color: theme.border
                                    ColumnLayout {
                                        id: heatColumn
                                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                        anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 7
                                        spacing: 3
                                        Txt { objectName: "heatTitle"; Layout.fillWidth: true; font.bold: true; elide: Text.ElideNone
                                              color: heatCard.dead ? theme.textSecondary : theme.textPrimary
                                              text: "حمّية اللوجو " + page.ltr(modelData.code) + "  •  " + modelData.workshop + "  •  " + modelData.date }
                                        Txt { objectName: "heatReversedNotice"; Layout.fillWidth: true; visible: modelData.notice !== ""; text: modelData.notice
                                              color: theme.textPrimary; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                        Txt { objectName: "heatState"; Layout.fillWidth: true; visible: modelData.state !== ""
                                              text: modelData.state + (modelData.reason !== "" ? "  •  السبب: " + modelData.reason : "")
                                              color: theme.textSecondary; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                        Repeater {
                                            model: modelData.lines
                                            delegate: Txt { objectName: "heatLine"; Layout.fillWidth: true; text: modelData.text
                                                            color: heatCard.dead ? theme.textSecondary : theme.textPrimary; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                        }
                                        Txt { objectName: "heatTotal"; Layout.fillWidth: true; text: modelData.total; font.bold: true
                                              color: heatCard.dead ? theme.textSecondary : theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                        Txt { Layout.fillWidth: true; visible: modelData.reference !== ""; text: "المرجع: " + modelData.reference
                                              color: theme.textMuted; font.pixelSize: theme.fontTableHeader; elide: Text.ElideNone }
                                        Txt { Layout.fillWidth: true; visible: modelData.gainExplanation !== ""; text: "سبب زيادة الوزن: " + modelData.gainExplanation
                                              color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                        Item { Layout.preferredHeight: 4 }
                                    }
                                }
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
        readonly property bool withLots: !isReturn && tripId === ""        // only a NEW custody can start with existing shop bars
        readonly property bool cashOnly: tripId !== "" && !!page.detail.isBook   // a BOOK trip takes and returns CASH here; its gold has its own dialog
        readonly property bool hasQuantity: cashField.text.trim().length > 0 || hasGold() || (withLots && lotLines().length > 0)
        function lotLines() {
            var rows = []
            for (var i = 0; i < createLotModel.count; ++i) {
                var g = createLotModel.get(i)
                if (g.picked) rows.push({ lotId: g.lotId })
            }
            return rows
        }
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
            createLotModel.clear()
            var lots = page.vm.shopLots || []
            for (var i = 0; i < lots.length; ++i)
                createLotModel.append({ lotId: lots[i].lotId, ref: lots[i].ref, kindText: lots[i].kindText, fineness: lots[i].fineness,
                                        weight: lots[i].fullWeight, issuable: lots[i].issuable, reason: lots[i].reason, picked: false })
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
        ListModel { id: createLotModel }
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
                    Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; visible: !entryDialog.cashOnly
                          text: entryDialog.isReturn ? "الذهب المُرجَع (اختر السطر من العهدة ثم الوزن)" : "الذهب (كسر): العيار والوجهة والوزن الفعلي بالجرام" }
                    Repeater {
                        model: entryDialog.cashOnly ? null : goldModel
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
                    Btn { objectName: "addGoldRowButton"; text: "＋ سطر ذهب آخر"; accent: theme.textSecondary; visible: !entryDialog.cashOnly
                          enabled: goldModel.count < 8
                          onClicked: goldModel.append({ karatIndex: 0, designationIndex: 0, lineIndex: 0, weight: "" }) }
                    // ---- existing shop bars (a separate source from the Scrap karat lines): whole, read-only weight and assay
                    Txt { objectName: "createLotsTitle"; Layout.fillWidth: true; visible: entryDialog.withLots; color: theme.gold; font.bold: true
                          text: "سبائك موجودة بالمحل (تخرج كاملة بنفس هويتها ووزنها وعيارها)" }
                    Txt { objectName: "createLotsEmpty"; Layout.fillWidth: true; visible: entryDialog.withLots && createLotModel.count === 0
                          text: "لا توجد سبائك بالمحل"; color: theme.textMuted }
                    Repeater {
                        model: entryDialog.withLots ? createLotModel : null
                        delegate: Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: createLotRow.implicitHeight + 12
                            radius: theme.radiusSmall
                            color: theme.bgTertiary
                            border.color: picked ? theme.gold : theme.border
                            RowLayout {
                                id: createLotRow
                                anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 10; anchors.rightMargin: 10
                                layoutDirection: Qt.RightToLeft
                                spacing: 10
                                CheckBox {
                                    objectName: "createLotSelect_" + index
                                    enabled: issuable && !!page.vm && !page.vm.writing
                                    checked: picked
                                    onToggled: createLotModel.setProperty(index, "picked", checked)
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 2
                                    Txt { objectName: "createLotRef_" + index; Layout.fillWidth: true; font.bold: true; elide: Text.ElideNone
                                          text: page.ltr(ref) + "  " + kindText }
                                    Txt { objectName: "createLotInfo_" + index; Layout.fillWidth: true; color: theme.textSecondary; wrapMode: Text.WordWrap
                                          elide: Text.ElideNone; text: "العيار الفعلي: " + fineness + " • الوزن الكامل: " + weight + " جرام" }
                                    Txt { objectName: "createLotReason_" + index; Layout.fillWidth: true; visible: !issuable; text: reason
                                          color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                }
                            }
                        }
                    }
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
                        else if (entryDialog.withLots)
                            page.vm.createCustody(cashField.text, entryDialog.collect(), entryDialog.lotLines(), dateField.text,
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

    // ---------------------------------------------------------------- BOOK operations (056): issue from Scrap / buy / sell / return to the shop
    // A BOOK trip keeps ONE gold number, the book balance in 21K equivalent. Every line states its physical facts (weight and a master karat OR an assay in
    // permille); every figure below is computed by the service rules through marketTripVM (never QML arithmetic). Normal use never reaches this dialog: no
    // production path creates a BOOK trip, so its buttons exist only on a BOOK trip.
    Popup {
        id: bookDialog
        objectName: "tripBookDialog"
        property string kind: "BK_ISSUE"
        property string tripId: ""
        property string requestKey: ""
        readonly property bool priced: kind === "BK_BUY" || kind === "BK_SELL"
        readonly property bool hasCounterparty: priced
        readonly property var karatChoices: [{ id: "", text: "اختر العيار" }].concat(page.ctx.karats || [])
        readonly property var basisChoices: kind === "BK_ISSUE" ? [{ id: "MASTER_KARAT", text: "عيار معياري" }]
                                                                   : [{ id: "MASTER_KARAT", text: "عيار معياري" }, { id: "FINENESS", text: "عيار فعلي (بالألف)" }]
        readonly property var partyChoices: [{ id: "", text: kind === "BK_BUY" ? "بائع غير مسجل (اكتب اسمه)" : "طرف آخر غير مسجل (اكتب اسمه)" }].concat(page.detail.saleBuyers || [])
        readonly property string partyId: partyChoices[bookParty.currentIndex] ? partyChoices[bookParty.currentIndex].id : ""
        readonly property bool needsConfirmation: !!page.vm && page.vm.errorCode === "PRICE_DEVIATION"
        property var lineRows: []
        readonly property var preview: {
            var known = page.detail.id !== undefined && page.detail.isBook === true
            return known && page.vm ? page.vm.previewBook(kind, lineRows) : ({})
        }
        readonly property string title: kind === "BK_ISSUE" ? "إخراج ذهب من كسر المحل (دفتري)" : kind === "BK_BUY" ? "شراء ذهب (دفتري)"
                                      : kind === "BK_SELL" ? "بيع ذهب (دفتري)" : "رجوع ذهب للمحل (دفتري)"
        readonly property string rule: kind === "BK_ISSUE" ? "يخرج الذهب من كسر المحل المتاح بنفس العيار المعياري (دون المحجوز لمراجعة المدير) ويدخل الرصيد الدفتري بمكافئ عيار 21."
                                     : kind === "BK_BUY" ? "تُدفع قيمة الذهب من نقدية هذه العهدة (لا من الخزنة) ويدخل الرصيد الدفتري بمكافئ عيار 21. اسم البائع إجباري."
                                     : kind === "BK_SELL" ? "يُباع الذهب بحد أقصى الرصيد الدفتري ويدخل ثمنه نقدية هذه العهدة. اسم المشتري إجباري."
                                     : "العيار المعياري يعود إلى كسر المحل بنفس العيار؛ أي عيار فعلي آخر يصبح بند مخزون مرتجع محدد الهوية بوزنه وعياره وأصله."
        function collect() {
            var rows = []
            for (var i = 0; i < bookModel.count; ++i) {
                var g = bookModel.get(i)
                var basis = basisChoices[g.basisIndex] ? basisChoices[g.basisIndex].id : "MASTER_KARAT"
                rows.push({ basis: basis, karatId: basis === "MASTER_KARAT" && g.karatIndex > 0 ? karatChoices[g.karatIndex].id : "",
                            fineness: basis === "FINENESS" ? g.fineness : "", weight: g.weight, price: priced ? g.price : "" })
            }
            return rows
        }
        function refreshLines() { lineRows = collect() }
        function openFor(what, trip) {
            if (!page.vm) return
            kind = what
            tripId = trip
            requestKey = page.vm.newRequestKey()
            bookParty.currentIndex = 0
            bookName.text = ""
            bookDate.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            bookReference.text = ""
            bookNotes.text = ""
            bookModel.clear()
            bookModel.append({ basisIndex: 0, karatIndex: 0, fineness: "", weight: "", price: "" })
            refreshLines()
            page.vm.clearMessage()
            page.vm.refresh()
            if (priced) page.vm.loadReferencePrices()
            open()
        }
        function complete() {
            if (bookDate.text.trim().length === 0) return false
            if (hasCounterparty && !(partyId !== "" || bookName.text.trim().length > 0)) return false
            for (var i = 0; i < bookModel.count; ++i) {
                var g = bookModel.get(i)
                var basis = basisChoices[g.basisIndex] ? basisChoices[g.basisIndex].id : "MASTER_KARAT"
                if (g.weight.trim().length > 0 && (basis === "FINENESS" ? g.fineness.trim().length > 0 : g.karatIndex > 0) && (!priced || g.price.trim().length > 0))
                    return true
            }
            return false
        }
        function submit(acknowledged) {
            if (kind === "BK_ISSUE") page.vm.bookIssue(tripId, collect(), bookDate.text, bookNotes.text, requestKey)
            else if (kind === "BK_BUY") page.vm.bookBuy(tripId, collect(), partyId, bookName.text, bookDate.text, bookReference.text, bookNotes.text, requestKey, acknowledged)
            else if (kind === "BK_SELL") page.vm.bookSell(tripId, collect(), partyId, bookName.text, bookDate.text, bookReference.text, bookNotes.text, requestKey, acknowledged)
            else page.vm.bookReturn(tripId, collect(), bookDate.text, bookNotes.text, requestKey)
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(640, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 680, 680)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        ListModel { id: bookModel }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { objectName: "bookDialogTitle"; Layout.fillWidth: true; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection; text: bookDialog.title }
            Txt { objectName: "bookDialogRule"; Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: bookDialog.rule }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: bookForm.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: bookForm
                    width: parent.width - 12
                    spacing: 6
                    Txt { Layout.fillWidth: true; visible: bookDialog.hasCounterparty; text: bookDialog.kind === "BK_BUY" ? "البائع *" : "المشتري *" }
                    Combo { id: bookParty; objectName: "bookCounterpartyCombo"; Layout.fillWidth: true; visible: bookDialog.hasCounterparty
                            model: bookDialog.partyChoices; textRole: "text" }
                    Txt { Layout.fillWidth: true; visible: bookDialog.hasCounterparty && bookDialog.partyId === ""; text: "الاسم *" }
                    Field { id: bookName; objectName: "bookNameField"; Layout.fillWidth: true; visible: bookDialog.hasCounterparty && bookDialog.partyId === "" }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "التاريخ (yyyy-MM-dd) *" }
                            Field { id: bookDate; objectName: "bookDateField"; Layout.fillWidth: true }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2; visible: bookDialog.hasCounterparty
                            Txt { Layout.fillWidth: true; text: "المرجع (اختياري)" }
                            Field { id: bookReference; objectName: "bookReferenceField"; Layout.fillWidth: true }
                        }
                    }
                    Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true
                          text: bookDialog.priced ? "الذهب: أسلوب العيار والعيار والوزن الفعلي وثمن السطر" : "الذهب: أسلوب العيار والعيار والوزن الفعلي" }
                    Repeater {
                        model: bookModel
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            Combo {
                                objectName: "bookBasis_" + index
                                Layout.preferredWidth: 150
                                model: bookDialog.basisChoices
                                textRole: "text"
                                currentIndex: basisIndex
                                onActivated: function (i) { bookModel.setProperty(index, "basisIndex", i); bookDialog.refreshLines() }
                            }
                            Combo {
                                objectName: "bookKarat_" + index
                                Layout.preferredWidth: 110
                                visible: bookDialog.basisChoices[basisIndex] && bookDialog.basisChoices[basisIndex].id === "MASTER_KARAT"
                                model: bookDialog.karatChoices
                                textRole: "text"
                                currentIndex: karatIndex
                                onActivated: function (i) { bookModel.setProperty(index, "karatIndex", i); bookDialog.refreshLines() }
                            }
                            Field {
                                objectName: "bookFineness_" + index
                                Layout.preferredWidth: 110
                                visible: bookDialog.basisChoices[basisIndex] && bookDialog.basisChoices[basisIndex].id === "FINENESS"
                                placeholderText: "العيار (‰)"
                                text: fineness
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                onTextEdited: { bookModel.setProperty(index, "fineness", text); bookDialog.refreshLines() }
                            }
                            Field {
                                objectName: "bookWeight_" + index
                                Layout.fillWidth: true
                                placeholderText: "الوزن (جرام)"
                                text: weight
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                onTextEdited: { bookModel.setProperty(index, "weight", text); bookDialog.refreshLines() }
                            }
                            Field {
                                objectName: "bookPrice_" + index
                                Layout.fillWidth: true
                                visible: bookDialog.priced
                                placeholderText: "الثمن (ج)"
                                text: price
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                onTextEdited: { bookModel.setProperty(index, "price", text); bookDialog.refreshLines() }
                            }
                        }
                    }
                    Btn { objectName: "addBookLineButton"; text: "＋ سطر آخر"; accent: theme.textSecondary
                          enabled: bookModel.count < 8
                          onClicked: { bookModel.append({ basisIndex: 0, karatIndex: 0, fineness: "", weight: "", price: "" }); bookDialog.refreshLines() } }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: bookPreviewColumn.implicitHeight + 14
                        radius: theme.radiusSmall
                        color: theme.bgTertiary
                        border.color: bookDialog.preview.warning ? theme.amber : theme.border
                        ColumnLayout {
                            id: bookPreviewColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 7
                            spacing: 3
                            Txt { objectName: "bookPreviewBalance"; Layout.fillWidth: true; visible: !!bookDialog.preview.balanceText
                                  text: bookDialog.preview.balanceText || ""; color: theme.amber; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Repeater {
                                model: bookDialog.preview.lines || []
                                delegate: Txt { objectName: "bookPreviewLine_" + index; Layout.fillWidth: true; text: modelData; color: theme.textSecondary
                                                font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            }
                            Txt { objectName: "bookPreviewTotal"; Layout.fillWidth: true; visible: !!bookDialog.preview.totalPosted
                                  text: bookDialog.preview.totalPosted || ""; color: theme.gold; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "bookPreviewPrice"; Layout.fillWidth: true; visible: !!bookDialog.preview.totalText
                                  text: "إجمالي الثمن: " + (bookDialog.preview.totalText || "") + (bookDialog.preview.referenceText ? " • " + bookDialog.preview.referenceText : "")
                                        + (bookDialog.preview.deviationText ? " • الفرق " + bookDialog.preview.deviationText : "")
                                  color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "bookPreviewWarning"; Layout.fillWidth: true; visible: bookDialog.preview.warning === true
                                  text: bookDialog.preview.warningText || ""; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "bookPreviewError"; Layout.fillWidth: true; visible: !!bookDialog.preview.error
                                  text: bookDialog.preview.error || ""; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Item { Layout.preferredHeight: 2 }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: bookNotes; objectName: "bookNotesField"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "bookDialogError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: bookDialog.needsConfirmation ? theme.amber : theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmBookButton"
                    visible: !bookDialog.needsConfirmation
                    text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : "تسجيل"
                    primary: true
                    enabled: bookDialog.complete() && !!page.vm && !page.vm.writing && !bookDialog.preview.error
                    onClicked: bookDialog.submit(false)
                }
                Btn {
                    objectName: "acknowledgeBookPriceButton"
                    visible: bookDialog.needsConfirmation
                    text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : "أؤكد الأسعار وأسجّل"
                    primary: true
                    accent: theme.amber
                    enabled: !!page.vm && !page.vm.writing
                    onClicked: bookDialog.submit(true)
                }
                Btn { objectName: "cancelBookButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.writing; onClicked: bookDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- pay a supplier's making with custody cash (دفع مصنعيات من العهدة)
    // The cash is already outside Main Safe: nothing moves in Main Safe or a bank; the custody cash and the supplier's making position drop together.
    Popup {
        id: payDialog
        objectName: "tripSupplierPayDialog"
        property string tripId: ""
        property string requestKey: ""
        readonly property var supplierChoices: [{ id: "", text: "اختر المورد" }].concat(page.ctx.suppliers || [])
        function openFor(trip) {
            if (!page.vm) return
            tripId = trip
            requestKey = page.vm.newRequestKey()
            payDateField.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            payAmountField.text = ""
            payReferenceField.text = ""
            payNotesField.text = ""
            paySupplierCombo.currentIndex = 0
            page.vm.clearMessage()
            page.vm.refresh()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(560, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 520, 520)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection; text: "دفع مصنعيات من العهدة" }
            Txt { objectName: "custodyPayRule"; Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "يُدفع للمورد من النقدية الموجودة فعلًا في هذه العهدة ولا يتجاوز المتبقي منها. لا تتحرك الخزنة الرئيسية ولا أي حساب بنكي، وتنقص مستحقات المورد (المصنعيات) بنفس المبلغ ويمكن أن تصبح له رصيدًا دائنًا. لا يتغير حساب ذهب المورد." }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: payForm.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: payForm
                    width: parent.width - 12
                    spacing: 6
                    Txt { objectName: "custodyPayOutstandingText"; Layout.fillWidth: true; color: theme.amber; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                          text: "النقدية المتبقية في العهدة: " + ((page.detail.cash || {}).outstanding || "—") }
                    Txt { Layout.fillWidth: true; text: "المورد *" }
                    Combo {
                        id: paySupplierCombo
                        objectName: "custodyPaySupplierCombo"
                        Layout.fillWidth: true
                        model: payDialog.supplierChoices
                        textRole: "text"
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "المبلغ (ج) *" }
                            Field { id: payAmountField; objectName: "custodyPayAmountField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhFormattedNumbersOnly }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "التاريخ (yyyy-MM-dd) *" }
                            Field { id: payDateField; objectName: "custodyPayDateField"; Layout.fillWidth: true }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "المرجع (اختياري)" }
                    Field { id: payReferenceField; objectName: "custodyPayReferenceField"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: payNotesField; objectName: "custodyPayNotesField"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "custodyPayEntryError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmCustodyPayButton"
                    text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : "تسجيل الدفعة"
                    primary: true
                    enabled: paySupplierCombo.currentIndex > 0 && payAmountField.text.trim().length > 0 && payDateField.text.trim().length > 0
                             && !!page.vm && !page.vm.writing
                    onClicked: page.vm.paySupplier(payDialog.tripId, payDialog.supplierChoices[paySupplierCombo.currentIndex].id, payAmountField.text,
                                                   payDateField.text, payReferenceField.text, payNotesField.text, payDialog.requestKey)
                }
                Btn { objectName: "cancelCustodyPayButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.writing; onClicked: payDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // Whole stored lot: the service derives physical facts, credit and eligibility.
    Popup {
        id: lotSettleDialog
        objectName: "tripLotSettleDialog"
        property string tripId: ""
        property string requestKey: ""
        readonly property var supplierChoices: [{id: "", text: "اختر المورد"}].concat(page.ctx.goldSuppliers || [])
        readonly property var lotChoices: [{id: "", text: "اختر الشحنة الكاملة"}].concat(page.detail.lotSettleSources || [])
        readonly property string supplierId: supplierChoices[lotSettleSupplier.currentIndex] ? supplierChoices[lotSettleSupplier.currentIndex].id : ""
        readonly property string lotId: lotChoices[lotSettleLot.currentIndex] ? lotChoices[lotSettleLot.currentIndex].id : ""
        readonly property var preview: {
            var loaded = page.detail.id !== undefined && page.ctx.goldSuppliers !== undefined
            return loaded && page.vm ? page.vm.previewLotSettlement(supplierId, lotId) : ({})
        }
        function openFor(trip) {
            if (!page.vm) return
            tripId = trip
            requestKey = page.vm.newRequestKey()
            lotSettleSupplier.currentIndex = 0
            lotSettleLot.currentIndex = 0
            lotSettleDate.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            lotSettleReference.text = ""
            lotSettleNotes.text = ""
            page.vm.clearMessage()
            page.vm.refresh()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(560, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 600, 600)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; text: "تسوية سبكة من العهدة"; color: theme.gold; font.bold: true }
            Txt { Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "تُسلَّم الشحنة كاملة بوزنها وعيارها الفعلي المخزّنين. يُحتسب للمورد: الوزن × العيار بالألف ÷ 1000 × 24 ÷ عيار حسابه. لا حركة بكسر المحل أو النقدية أو المصنعيات." }
            Flickable {
                Layout.fillWidth: true; Layout.fillHeight: true
                contentHeight: lotSettleForm.implicitHeight; clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: lotSettleForm
                    width: parent.width - 12; spacing: 6
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Combo { id: lotSettleSupplier; objectName: "custodyLotSettleSupplierCombo"; Layout.fillWidth: true; model: lotSettleDialog.supplierChoices; textRole: "text" }
                        Txt { text: "المورد *" }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Combo { id: lotSettleLot; objectName: "custodyLotSettleLotCombo"; Layout.fillWidth: true; model: lotSettleDialog.lotChoices; textRole: "text" }
                        Txt { text: "الشحنة الكاملة *" }
                    }
                    Txt { objectName: "custodyLotSettleFacts"; Layout.fillWidth: true; text: lotSettleDialog.preview.facts || ""; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    Txt { objectName: "custodyLotSettleCredit"; Layout.fillWidth: true; text: lotSettleDialog.preview.credit || ""; color: theme.gold; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    Txt { objectName: "custodyLotSettleAnalysis"; Layout.fillWidth: true; text: lotSettleDialog.preview.analysis || ""; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    Txt { objectName: "custodyLotSettlePreviewError"; Layout.fillWidth: true; text: lotSettleDialog.preview.error || ""; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Field { id: lotSettleDate; objectName: "custodyLotSettleDateField"; Layout.fillWidth: true }
                        Txt { text: "التاريخ *" }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Field { id: lotSettleReference; objectName: "custodyLotSettleReferenceField"; Layout.fillWidth: true }
                        Txt { text: "المرجع" }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Field { id: lotSettleNotes; objectName: "custodyLotSettleNotesField"; Layout.fillWidth: true }
                        Txt { text: "ملاحظات" }
                    }
                }
            }
            Txt { Layout.fillWidth: true; text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                Btn { objectName: "confirmCustodyLotSettleButton"; text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : "تسجيل التسوية"; primary: true
                      enabled: lotSettleDialog.preview.valid === true && lotSettleDate.text.trim().length > 0 && !!page.vm && !page.vm.writing
                      onClicked: page.vm.settleLot(lotSettleDialog.tripId, lotSettleDialog.supplierId, lotSettleDialog.lotId,
                                                  lotSettleDate.text, lotSettleReference.text, lotSettleNotes.text, lotSettleDialog.requestKey) }
                Btn { objectName: "cancelCustodyLotSettleButton"; text: "إلغاء"; enabled: !!page.vm && !page.vm.writing; onClicked: lotSettleDialog.close() }
            }
        }
    }

    // ---------------------------------------------------------------- hand a whole assayed lot to an intermediary (تسليم ذهب لمسوقجي)
    // The service derives the full weight under the locks; nothing here sends a quantity.
    Popup {
        id: lotHoldDialog
        objectName: "tripLotHoldDialog"
        property string tripId: ""
        property string requestKey: ""
        readonly property var partyChoices: [{id: "", text: "اختر المسوقجي"}].concat(page.ctx.holdParties || [])
        readonly property var lotChoices: [{id: "", text: "اختر الشحنة الكاملة"}].concat(page.detail.holdSources || [])
        readonly property string partyId: partyChoices[lotHoldParty.currentIndex] ? partyChoices[lotHoldParty.currentIndex].id : ""
        readonly property string lotId: lotChoices[lotHoldLot.currentIndex] ? lotChoices[lotHoldLot.currentIndex].id : ""
        readonly property var preview: {
            var loaded = page.detail.id !== undefined && page.ctx.holdParties !== undefined
            return loaded && page.vm ? page.vm.previewHandover(partyId, lotId) : ({})
        }
        function openFor(trip) {
            if (!page.vm) return
            tripId = trip
            requestKey = page.vm.newRequestKey()
            lotHoldParty.currentIndex = 0
            lotHoldLot.currentIndex = 0
            lotHoldDate.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            lotHoldReference.text = ""
            lotHoldNotes.text = ""
            page.vm.clearMessage()
            page.vm.refresh()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(560, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 580, 580)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; text: "تسليم ذهب لمسوقجي"; color: theme.gold; font.bold: true }
            Txt { objectName: "custodyLotHoldRule"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "تُسلَّم الشحنة كاملة بوزنها وعيارها الفعلي المخزّنين (معلوم العيار فقط) وتصير ذهبًا للمحل لدى المسوقجي. لا نقدية ولا كسر ولا مصنعيات ولا دين على المورد." }
            Flickable {
                Layout.fillWidth: true; Layout.fillHeight: true
                contentHeight: lotHoldForm.implicitHeight; clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: lotHoldForm
                    width: parent.width - 12; spacing: 6
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Combo { id: lotHoldParty; objectName: "custodyLotHoldPartyCombo"; Layout.fillWidth: true; model: lotHoldDialog.partyChoices; textRole: "text" }
                        Txt { text: "المسوقجي *" }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Combo { id: lotHoldLot; objectName: "custodyLotHoldLotCombo"; Layout.fillWidth: true; model: lotHoldDialog.lotChoices; textRole: "text" }
                        Txt { text: "الشحنة الكاملة *" }
                    }
                    Txt { objectName: "custodyLotHoldFacts"; Layout.fillWidth: true; text: lotHoldDialog.preview.facts || ""; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    Txt { objectName: "custodyLotHoldNote"; Layout.fillWidth: true; text: lotHoldDialog.preview.note || ""; color: theme.gold; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    Txt { objectName: "custodyLotHoldPreviewError"; Layout.fillWidth: true; text: lotHoldDialog.preview.error || ""; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Field { id: lotHoldDate; objectName: "custodyLotHoldDateField"; Layout.fillWidth: true }
                        Txt { text: "التاريخ *" }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Field { id: lotHoldReference; objectName: "custodyLotHoldReferenceField"; Layout.fillWidth: true }
                        Txt { text: "المرجع" }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Field { id: lotHoldNotes; objectName: "custodyLotHoldNotesField"; Layout.fillWidth: true }
                        Txt { text: "ملاحظات" }
                    }
                }
            }
            Txt { objectName: "custodyLotHoldError"; Layout.fillWidth: true; text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                Btn { objectName: "confirmCustodyLotHoldButton"; text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : "تسجيل التسليم"; primary: true
                      enabled: lotHoldDialog.preview.valid === true && lotHoldDate.text.trim().length > 0 && !!page.vm && !page.vm.writing
                      onClicked: page.vm.handOverLot(lotHoldDialog.tripId, lotHoldDialog.partyId, lotHoldDialog.lotId,
                                                     lotHoldDate.text, lotHoldReference.text, lotHoldNotes.text, lotHoldDialog.requestKey) }
                Btn { objectName: "cancelCustodyLotHoldButton"; text: "إلغاء"; enabled: !!page.vm && !page.vm.writing; onClicked: lotHoldDialog.close() }
            }
        }
    }

    // ---------------------------------------------------------------- hand pooled LOGO grams to an intermediary (تسليم لوجو لمسوقجي)
    // Physical grams of one master karat; the service derives the effective custody availability. No bar, no assay.
    Popup {
        id: logoHoldDialog
        objectName: "tripLogoHoldDialog"
        property string tripId: ""
        property string requestKey: ""
        readonly property var partyChoices: [{id: "", text: "اختر المسوقجي"}].concat(page.ctx.holdParties || [])
        readonly property var karatChoices: [{karatId: "", text: "اختر عيار اللوجو"}].concat(page.detail.logoHoldSources || [])
        readonly property string partyId: partyChoices[logoHoldParty.currentIndex] ? partyChoices[logoHoldParty.currentIndex].id : ""
        readonly property string karatId: karatChoices[logoHoldKarat.currentIndex] ? karatChoices[logoHoldKarat.currentIndex].karatId : ""
        readonly property var preview: {
            var loaded = page.detail.id !== undefined && page.ctx.holdParties !== undefined
            return loaded && page.vm ? page.vm.previewLogoHandover(partyId, karatId, logoHoldGrams.text) : ({})
        }
        function openFor(trip) {
            if (!page.vm) return
            tripId = trip
            requestKey = page.vm.newRequestKey()
            logoHoldParty.currentIndex = 0
            logoHoldKarat.currentIndex = 0
            logoHoldGrams.text = ""
            logoHoldDate.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            logoHoldReference.text = ""
            logoHoldNotes.text = ""
            page.vm.clearMessage()
            page.vm.refresh()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(560, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 580, 580)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; text: "تسليم لوجو لمسوقجي"; color: theme.gold; font.bold: true }
            Txt { objectName: "custodyLogoHoldRule"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "لوجو بالجرام الفعلي لعيار واحد من العهدة (غير محدد الهوية ولا العيار الفعلي): يخرج من العهدة ويصير ذهبًا للمحل لدى المسوقجي، وكل تسليم مصدر مستقل. لا كسر ولا نقدية ولا مصنعيات ولا دين على المورد." }
            Flickable {
                Layout.fillWidth: true; Layout.fillHeight: true
                contentHeight: logoHoldForm.implicitHeight; clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: logoHoldForm
                    width: parent.width - 12; spacing: 6
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Combo { id: logoHoldParty; objectName: "custodyLogoHoldPartyCombo"; Layout.fillWidth: true; model: logoHoldDialog.partyChoices; textRole: "text" }
                        Txt { text: "المسوقجي *" }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Combo { id: logoHoldKarat; objectName: "custodyLogoHoldKaratCombo"; Layout.fillWidth: true; model: logoHoldDialog.karatChoices; textRole: "text" }
                        Txt { text: "عيار اللوجو *" }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Field { id: logoHoldGrams; objectName: "custodyLogoHoldGramsField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhFormattedNumbersOnly }
                        Txt { text: "الوزن الفعلي (جرام) *" }
                    }
                    Txt { objectName: "custodyLogoHoldFacts"; Layout.fillWidth: true; text: logoHoldDialog.preview.facts || ""; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    Txt { objectName: "custodyLogoHoldPreviewError"; Layout.fillWidth: true; text: logoHoldDialog.preview.error || ""; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Field { id: logoHoldDate; objectName: "custodyLogoHoldDateField"; Layout.fillWidth: true }
                        Txt { text: "التاريخ *" }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Field { id: logoHoldReference; objectName: "custodyLogoHoldReferenceField"; Layout.fillWidth: true }
                        Txt { text: "المرجع" }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Field { id: logoHoldNotes; objectName: "custodyLogoHoldNotesField"; Layout.fillWidth: true }
                        Txt { text: "ملاحظات" }
                    }
                }
            }
            Txt { objectName: "custodyLogoHoldError"; Layout.fillWidth: true; text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true; layoutDirection: Qt.RightToLeft
                Btn { objectName: "confirmCustodyLogoHoldButton"; text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : "تسجيل التسليم"; primary: true
                      enabled: logoHoldDialog.preview.valid === true && logoHoldDate.text.trim().length > 0 && !!page.vm && !page.vm.writing
                      onClicked: page.vm.handOverLogo(logoHoldDialog.tripId, logoHoldDialog.partyId, logoHoldDialog.karatId, logoHoldGrams.text,
                                                      logoHoldDate.text, logoHoldReference.text, logoHoldNotes.text, logoHoldDialog.requestKey) }
                Btn { objectName: "cancelCustodyLogoHoldButton"; text: "إلغاء"; enabled: !!page.vm && !page.vm.writing; onClicked: logoHoldDialog.close() }
            }
        }
    }

    // ---------------------------------------------------------------- deliver LOGO custody gold to a supplier (تسوية ذهب من العهدة)
    // The gold is already outside the shop: nothing moves in the shop Scrap, Main Safe or a bank, and no lot or making moves. The custody gold and the
    // supplier's gold account (in ITS karat) change together. Every figure below is computed by the service rules through marketTripVM.
    Popup {
        id: goldSettleDialog
        objectName: "tripGoldSettleDialog"
        property string tripId: ""
        property string requestKey: ""
        readonly property var supplierChoices: [{ id: "", text: "اختر المورد" }].concat(page.ctx.goldSuppliers || [])
        readonly property var sourceChoices: [{ key: "", karatId: "", designation: "", text: "اختر مصدر الذهب من العهدة" }].concat(page.detail.goldSettleSources || [])
        readonly property string supplierId: supplierChoices[goldSettleSupplier.currentIndex] ? supplierChoices[goldSettleSupplier.currentIndex].id : ""
        readonly property var source: sourceChoices[goldSettleSource.currentIndex] ? sourceChoices[goldSettleSource.currentIndex] : ({})
        readonly property var preview: {
            var known = page.detail.id !== undefined && page.ctx.goldSuppliers !== undefined            // read, so the preview follows the loaded custody and suppliers
            return known && page.vm ? page.vm.previewGoldSettlement(supplierId, source.karatId || "", source.designation || "", goldSettleWeight.text) : ({})
        }
        function openFor(trip) {
            if (!page.vm) return
            tripId = trip
            requestKey = page.vm.newRequestKey()
            goldSettleSupplier.currentIndex = 0
            goldSettleSource.currentIndex = 0
            goldSettleWeight.text = ""
            goldSettleDate.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            goldSettleReference.text = ""
            goldSettleNotes.text = ""
            page.vm.clearMessage()
            page.vm.refresh()
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(560, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 640, 640)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection; text: "تسوية ذهب من العهدة" }
            Txt { objectName: "custodyGoldSettleRule"; Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "يُسلَّم للمورد ذهب لوجو موجود فعلًا في هذه العهدة ولا يتجاوز المتبقي منه، ويُحتسب في حسابه بعياره: الوزن × عيار المُسلَّم ÷ عيار المورد. لا يُخصم من كسر المحل مرة ثانية، ولا نقدية ولا مصنعيات ولا شحنات سبك." }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: goldSettleForm.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: goldSettleForm
                    width: parent.width - 12
                    spacing: 6
                    Txt { Layout.fillWidth: true; text: "المورد *" }
                    Combo { id: goldSettleSupplier; objectName: "custodyGoldSettleSupplierCombo"; Layout.fillWidth: true; model: goldSettleDialog.supplierChoices; textRole: "text" }
                    Txt { Layout.fillWidth: true; text: "مصدر الذهب من العهدة *" }
                    Combo { id: goldSettleSource; objectName: "custodyGoldSettleSourceCombo"; Layout.fillWidth: true; model: goldSettleDialog.sourceChoices; textRole: "text" }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "الوزن المُسلَّم الفعلي (جرام) *" }
                            Field { id: goldSettleWeight; objectName: "custodyGoldSettleWeightField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhFormattedNumbersOnly }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "التاريخ (yyyy-MM-dd) *" }
                            Field { id: goldSettleDate; objectName: "custodyGoldSettleDateField"; Layout.fillWidth: true }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: goldSettlePreview.implicitHeight + 14
                        radius: theme.radiusSmall
                        color: theme.bgTertiary
                        border.color: goldSettleDialog.preview.short ? theme.red : theme.border
                        ColumnLayout {
                            id: goldSettlePreview
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 7
                            spacing: 3
                            Txt { objectName: "custodyGoldSettleAvailable"; Layout.fillWidth: true; visible: !!goldSettleDialog.preview.availableText
                                  text: "المتاح من هذا المصدر في العهدة: " + goldSettleDialog.preview.availableText; color: theme.amber; font.bold: true
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "custodyGoldSettleSupplierKarat"; Layout.fillWidth: true; visible: !!goldSettleDialog.preview.supplierKaratText
                                  text: "عيار حساب المورد: " + goldSettleDialog.preview.supplierKaratText; color: theme.textSecondary
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "custodyGoldSettleDelivered"; Layout.fillWidth: true; visible: !!goldSettleDialog.preview.deliveredText
                                  text: "المُسلَّم: " + goldSettleDialog.preview.deliveredText; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "custodyGoldSettleFormula"; Layout.fillWidth: true; visible: !!goldSettleDialog.preview.formulaText
                                  text: "الحساب: " + goldSettleDialog.preview.formulaText; color: theme.textMuted; font.pixelSize: theme.fontTableHeader
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "custodyGoldSettleCredited"; Layout.fillWidth: true; visible: !!goldSettleDialog.preview.creditedText
                                  text: "يُحتسب للمورد: " + goldSettleDialog.preview.creditedText; color: theme.gold; font.bold: true
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "custodyGoldSettleAnalytical"; Layout.fillWidth: true; visible: !!goldSettleDialog.preview.analyticalText
                                  text: "القيمة التحليلية: " + goldSettleDialog.preview.analyticalText + " • فرق التقريب: " + goldSettleDialog.preview.differenceText
                                  color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "custodyGoldSettleShort"; Layout.fillWidth: true; visible: goldSettleDialog.preview.short === true
                                  text: "الوزن أكبر من المتاح من هذا المصدر في العهدة."; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "custodyGoldSettlePreviewError"; Layout.fillWidth: true; visible: !!goldSettleDialog.preview.error
                                  text: goldSettleDialog.preview.error || ""; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Item { Layout.preferredHeight: 2 }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "المرجع (اختياري)" }
                    Field { id: goldSettleReference; objectName: "custodyGoldSettleReferenceField"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: goldSettleNotes; objectName: "custodyGoldSettleNotesField"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "custodyGoldSettleError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmCustodyGoldSettleButton"
                    text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : "تسجيل التسوية"
                    primary: true
                    enabled: goldSettleDialog.supplierId !== "" && !!goldSettleDialog.source.karatId && goldSettleWeight.text.trim().length > 0
                             && goldSettleDate.text.trim().length > 0 && !!page.vm && !page.vm.writing
                             && goldSettleDialog.preview.short !== true && !goldSettleDialog.preview.error
                    onClicked: page.vm.settleGold(goldSettleDialog.tripId, goldSettleDialog.supplierId, goldSettleDialog.source.karatId,
                                                  goldSettleDialog.source.designation, goldSettleWeight.text, goldSettleDate.text,
                                                  goldSettleReference.text, goldSettleNotes.text, goldSettleDialog.requestKey)
                }
                Btn { objectName: "cancelCustodyGoldSettleButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.writing; onClicked: goldSettleDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- cash gold purchase (شراء ذهب نقدي)
    // Custody CASH becomes custody GOLD: nothing moves in the Main Safe, a bank, the shop Scrap or any supplier account. The vendor is mandatory.
    // Every figure below is computed by the service rules through marketTripVM; a total far from the reference needs confirmation.
    Popup {
        id: goldBuyDialog
        objectName: "tripGoldBuyDialog"
        property string tripId: ""
        property string requestKey: ""
        property string pendingVendor: ""
        readonly property var karatChoices: [{ id: "", text: "اختر العيار" }].concat(page.ctx.karats || [])
        readonly property var designationChoices: page.ctx.designations || []
        readonly property var vendorChoices: [{ id: "", text: "بائع غير مسجل (اكتب اسمه)" }].concat(page.detail.saleBuyers || [])
        readonly property string vendorId: vendorChoices[goldBuyVendor.currentIndex] ? vendorChoices[goldBuyVendor.currentIndex].id : ""
        readonly property bool needsConfirmation: !!page.vm && page.vm.errorCode === "PRICE_DEVIATION"
        property var lineRows: []
        readonly property var preview: {
            var known = page.detail.id !== undefined && page.ctx.safeName !== undefined
            return known && page.vm ? page.vm.previewBuy(lineRows) : ({})
        }
        function defaultDesignation() {
            for (var i = 0; i < designationChoices.length; ++i)
                if (designationChoices[i].id === "LOGO") return i
            return 0
        }
        function collect() {
            var rows = []
            for (var i = 0; i < buyModel.count; ++i) {
                var g = buyModel.get(i)
                rows.push({ karatId: g.karatIndex > 0 ? karatChoices[g.karatIndex].id : "",
                            designation: designationChoices[g.designationIndex] ? designationChoices[g.designationIndex].id : "",
                            weight: g.weight, price: g.price })
            }
            return rows
        }
        function refreshLines() { lineRows = collect() }
        function openFor(trip) {
            if (!page.vm) return
            tripId = trip
            requestKey = page.vm.newRequestKey()
            pendingVendor = ""
            goldBuyVendor.currentIndex = 0
            goldBuyName.text = ""
            goldBuyRegisterKarat.currentIndex = 0
            goldBuyDate.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            goldBuyReference.text = ""
            goldBuyNotes.text = ""
            buyModel.clear()
            buyModel.append({ karatIndex: 0, designationIndex: defaultDesignation(), weight: "", price: "" })
            refreshLines()
            page.vm.clearMessage()
            page.vm.refresh()
            page.vm.loadReferencePrices()
            open()
        }
        function submit(acknowledged) {
            page.vm.buyGold(tripId, collect(), vendorId, goldBuyName.text, goldBuyDate.text, goldBuyReference.text, goldBuyNotes.text,
                            requestKey, acknowledged)
        }
        function complete() {
            if (!(vendorId !== "" || goldBuyName.text.trim().length > 0) || goldBuyDate.text.trim().length === 0) return false
            for (var i = 0; i < buyModel.count; ++i) {
                var g = buyModel.get(i)
                if (g.karatIndex > 0 && g.weight.trim().length > 0 && g.price.trim().length > 0) return true
            }
            return false
        }
        function selectPendingVendor() {
            if (pendingVendor === "") return
            for (var i = 0; i < vendorChoices.length; ++i)
                if (vendorChoices[i].id === pendingVendor) { goldBuyVendor.currentIndex = i; pendingVendor = ""; return }
        }
        // after the combo has taken the new model (it resets its index when the model changes)
        onVendorChoicesChanged: Qt.callLater(selectPendingVendor)
        onPendingVendorChanged: Qt.callLater(selectPendingVendor)
        Connections {
            target: page.vm
            function onVendorRegistered(id) { goldBuyDialog.pendingVendor = id }
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(600, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 680, 680)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        ListModel { id: buyModel }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection; text: "شراء ذهب نقدي" }
            Txt { objectName: "custodyGoldBuyRule"; Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "تُدفع قيمة الذهب من نقدية هذه العهدة (لا من الخزنة) ويدخل الذهب العهدة بنفس العيار والوزن الفعلي. اسم البائع إجباري. لا يتأثر حساب أي مورد." }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: goldBuyForm.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: goldBuyForm
                    width: parent.width - 12
                    spacing: 6
                    Txt { Layout.fillWidth: true; text: "البائع *" }
                    Combo { id: goldBuyVendor; objectName: "custodyGoldBuyVendorCombo"; Layout.fillWidth: true; model: goldBuyDialog.vendorChoices; textRole: "text" }
                    Txt { Layout.fillWidth: true; visible: goldBuyDialog.vendorId === ""; text: "اسم البائع *" }
                    Field { id: goldBuyName; objectName: "custodyGoldBuyNameField"; Layout.fillWidth: true; visible: goldBuyDialog.vendorId === "" }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: goldBuyDialog.vendorId === "" && goldBuyName.text.trim().length > 0
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Txt { text: "تسجيله طرفًا جديدًا (مورد) بعيار حساب:"; color: theme.textSecondary; font.pixelSize: theme.fontTableHeader }
                        Combo { id: goldBuyRegisterKarat; objectName: "custodyGoldBuyRegisterKaratCombo"; Layout.preferredWidth: 130
                                model: goldBuyDialog.karatChoices; textRole: "text" }
                        Btn { objectName: "registerGoldBuyVendorButton"; text: "تسجيل كطرف جديد"; accent: theme.textSecondary
                              enabled: goldBuyRegisterKarat.currentIndex > 0 && !!page.vm && !page.vm.writing
                              onClicked: page.vm.registerVendor(goldBuyName.text, goldBuyDialog.karatChoices[goldBuyRegisterKarat.currentIndex].id) }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "التاريخ (yyyy-MM-dd) *" }
                            Field { id: goldBuyDate; objectName: "custodyGoldBuyDateField"; Layout.fillWidth: true }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "المرجع (اختياري)" }
                            Field { id: goldBuyReference; objectName: "custodyGoldBuyReferenceField"; Layout.fillWidth: true }
                        }
                    }
                    Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; text: "الذهب المشترى: العيار والوجهة والوزن الفعلي وثمن السطر" }
                    Repeater {
                        model: buyModel
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            Combo {
                                objectName: "custodyGoldBuyKarat_" + index
                                Layout.preferredWidth: 110
                                model: goldBuyDialog.karatChoices
                                textRole: "text"
                                currentIndex: karatIndex
                                onActivated: function (i) { buyModel.setProperty(index, "karatIndex", i); goldBuyDialog.refreshLines() }
                            }
                            Combo {
                                objectName: "custodyGoldBuyDesignation_" + index
                                Layout.preferredWidth: 100
                                model: goldBuyDialog.designationChoices
                                textRole: "text"
                                currentIndex: designationIndex
                                onActivated: function (i) { buyModel.setProperty(index, "designationIndex", i); goldBuyDialog.refreshLines() }
                            }
                            Field {
                                objectName: "custodyGoldBuyWeight_" + index
                                Layout.fillWidth: true
                                placeholderText: "الوزن (جرام)"
                                text: weight
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                onTextEdited: { buyModel.setProperty(index, "weight", text); goldBuyDialog.refreshLines() }
                            }
                            Field {
                                objectName: "custodyGoldBuyPrice_" + index
                                Layout.fillWidth: true
                                placeholderText: "الثمن (ج)"
                                text: price
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                onTextEdited: { buyModel.setProperty(index, "price", text); goldBuyDialog.refreshLines() }
                            }
                        }
                    }
                    Btn { objectName: "addGoldBuyLineButton"; text: "＋ سطر ذهب آخر"; accent: theme.textSecondary
                          enabled: buyModel.count < 8
                          onClicked: { buyModel.append({ karatIndex: 0, designationIndex: goldBuyDialog.defaultDesignation(), weight: "", price: "" }); goldBuyDialog.refreshLines() } }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: goldBuyPreview.implicitHeight + 14
                        radius: theme.radiusSmall
                        color: theme.bgTertiary
                        border.color: goldBuyDialog.preview.warning ? theme.amber : theme.border
                        ColumnLayout {
                            id: goldBuyPreview
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 7
                            spacing: 3
                            Txt { objectName: "custodyGoldBuyAvailable"; Layout.fillWidth: true; visible: !!goldBuyDialog.preview.availableText
                                  text: goldBuyDialog.preview.availableText || ""; color: theme.amber; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "custodyGoldBuyTotal"; Layout.fillWidth: true; visible: !!goldBuyDialog.preview.totalText
                                  text: "إجمالي الشراء: " + (goldBuyDialog.preview.totalText || ""); color: theme.gold; font.bold: true
                                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "custodyGoldBuyReference"; Layout.fillWidth: true; visible: !!goldBuyDialog.preview.referenceText
                                  text: (goldBuyDialog.preview.referenceText || "") + (goldBuyDialog.preview.deviationText ? " • الفرق " + goldBuyDialog.preview.deviationText : "")
                                  color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "custodyGoldBuyWarning"; Layout.fillWidth: true; visible: goldBuyDialog.preview.warning === true
                                  text: goldBuyDialog.preview.warningText || ""; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "custodyGoldBuyPreviewError"; Layout.fillWidth: true; visible: !!goldBuyDialog.preview.error
                                  text: goldBuyDialog.preview.error || ""; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Item { Layout.preferredHeight: 2 }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: goldBuyNotes; objectName: "custodyGoldBuyNotesField"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "custodyGoldBuyError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: goldBuyDialog.needsConfirmation ? theme.amber : theme.red
                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmCustodyGoldBuyButton"
                    visible: !goldBuyDialog.needsConfirmation
                    text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : "تسجيل الشراء"
                    primary: true
                    enabled: goldBuyDialog.complete() && !!page.vm && !page.vm.writing && !goldBuyDialog.preview.error
                    onClicked: goldBuyDialog.submit(false)
                }
                Btn {
                    objectName: "acknowledgeCustodyGoldBuyPriceButton"
                    visible: goldBuyDialog.needsConfirmation
                    text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : "أؤكد الأسعار وأسجّل الشراء"
                    primary: true
                    accent: theme.amber
                    enabled: !!page.vm && !page.vm.writing
                    onClicked: goldBuyDialog.submit(true)
                }
                Btn { objectName: "cancelCustodyGoldBuyButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.writing; onClicked: goldBuyDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- sell custody gold for cash (بيع ذهب العهدة)
    // The sold grams leave the custody and the agreed price ENTERS the custody cash: nothing moves in the Main Safe, a bank, the shop Scrap or any
    // supplier account. Every figure below is computed by the service rules through marketTripVM; a price far from the reference needs confirmation.
    Popup {
        id: goldSaleDialog
        objectName: "tripGoldSaleDialog"
        property string tripId: ""
        property string requestKey: ""
        readonly property var sourceChoices: [{ key: "", kind: "", karatId: "", designation: "", lotId: "", text: "اختر الذهب المباع من العهدة" }].concat(page.detail.saleSources || [])
        readonly property var buyerChoices: [{ id: "", text: "طرف آخر غير مسجل (اكتب اسمه)" }].concat(page.detail.saleBuyers || [])
        readonly property var modeChoices: [{ mode: "TOTAL", text: "إجمالي الصفقة (ج)" }, { mode: "PER_GRAM", text: "سعر الجرام (ج)" }]
        readonly property var source: sourceChoices[goldSaleSource.currentIndex] ? sourceChoices[goldSaleSource.currentIndex] : ({})
        readonly property string buyerId: buyerChoices[goldSaleBuyer.currentIndex] ? buyerChoices[goldSaleBuyer.currentIndex].id : ""
        readonly property string mode: modeChoices[goldSaleMode.currentIndex].mode
        readonly property bool isLot: source.kind === "CUSTODY_LOT"
        readonly property bool needsConfirmation: !!page.vm && page.vm.errorCode === "PRICE_DEVIATION"
        readonly property var preview: {
            var known = page.detail.id !== undefined && page.ctx.safeName !== undefined              // read, so the preview follows the loaded custody and context
            return known && page.vm && source.kind ? page.vm.previewSale(source.kind, source.karatId || "", source.designation || "", source.lotId || "",
                                                                         goldSaleWeight.text, mode, goldSalePrice.text) : ({})
        }
        function openFor(trip) {
            if (!page.vm) return
            tripId = trip
            requestKey = page.vm.newRequestKey()
            goldSaleSource.currentIndex = 0
            goldSaleBuyer.currentIndex = 0
            goldSaleMode.currentIndex = 0
            goldSaleWeight.text = ""
            goldSalePrice.text = ""
            goldSaleName.text = ""
            goldSaleDate.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            goldSaleReference.text = ""
            goldSaleNotes.text = ""
            page.vm.clearMessage()
            page.vm.refresh()
            page.vm.loadReferencePrices()
            open()
        }
        function submit(acknowledged) {
            page.vm.sellGold(tripId, source.kind, source.karatId || "", source.designation || "", source.lotId || "", goldSaleWeight.text, mode,
                             goldSalePrice.text, buyerId, goldSaleName.text, goldSaleDate.text, goldSaleReference.text, goldSaleNotes.text,
                             requestKey, acknowledged)
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(560, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 660, 660)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection; text: "بيع ذهب من العهدة" }
            Txt { objectName: "custodyGoldSaleRule"; Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "يُباع ذهب موجود فعلًا في هذه العهدة بالسعر الذي اتفقت عليه، ويدخل الثمن في نقدية العهدة (لا في الخزنة ولا في حساب أي مورد). السبيكة تُباع كاملة." }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: goldSaleForm.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: goldSaleForm
                    width: parent.width - 12
                    spacing: 6
                    Txt { Layout.fillWidth: true; text: "الذهب المباع *" }
                    Combo { id: goldSaleSource; objectName: "custodyGoldSaleSourceCombo"; Layout.fillWidth: true; model: goldSaleDialog.sourceChoices; textRole: "text" }
                    Txt { Layout.fillWidth: true; visible: !goldSaleDialog.isLot; text: "الوزن المباع الفعلي (جرام) *" }
                    Field { id: goldSaleWeight; objectName: "custodyGoldSaleWeightField"; Layout.fillWidth: true; visible: !goldSaleDialog.isLot
                            inputMethodHints: Qt.ImhFormattedNumbersOnly }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "طريقة التسعير *" }
                            Combo { id: goldSaleMode; objectName: "custodyGoldSaleModeCombo"; Layout.fillWidth: true; model: goldSaleDialog.modeChoices; textRole: "text" }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "السعر المتفق عليه *" }
                            Field { id: goldSalePrice; objectName: "custodyGoldSalePriceField"; Layout.fillWidth: true; inputMethodHints: Qt.ImhFormattedNumbersOnly }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: goldSalePreview.implicitHeight + 14
                        radius: theme.radiusSmall
                        color: theme.bgTertiary
                        border.color: goldSaleDialog.preview.warning ? theme.amber : theme.border
                        ColumnLayout {
                            id: goldSalePreview
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 7
                            spacing: 3
                            Txt { objectName: "custodyGoldSaleAvailable"; Layout.fillWidth: true; visible: !!goldSaleDialog.preview.availableText
                                  text: goldSaleDialog.preview.availableText || ""; color: theme.amber; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "custodyGoldSaleTotal"; Layout.fillWidth: true; visible: !!goldSaleDialog.preview.totalText
                                  text: "إجمالي الصفقة: " + (goldSaleDialog.preview.totalText || "") + " • سعر الجرام: " + (goldSaleDialog.preview.perGramText || "")
                                  color: theme.gold; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "custodyGoldSaleReference"; Layout.fillWidth: true; visible: !!goldSaleDialog.preview.referenceText
                                  text: (goldSaleDialog.preview.referenceText || "") + (goldSaleDialog.preview.deviationText ? " • الفرق " + goldSaleDialog.preview.deviationText : "")
                                  color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "custodyGoldSaleWarning"; Layout.fillWidth: true; visible: goldSaleDialog.preview.warning === true
                                  text: goldSaleDialog.preview.warningText || ""; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Txt { objectName: "custodyGoldSalePreviewError"; Layout.fillWidth: true; visible: !!goldSaleDialog.preview.error
                                  text: goldSaleDialog.preview.error || ""; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                            Item { Layout.preferredHeight: 2 }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "المشتري *" }
                    Combo { id: goldSaleBuyer; objectName: "custodyGoldSaleBuyerCombo"; Layout.fillWidth: true; model: goldSaleDialog.buyerChoices; textRole: "text" }
                    Txt { Layout.fillWidth: true; visible: goldSaleDialog.buyerId === ""; text: "اسم المشتري *" }
                    Field { id: goldSaleName; objectName: "custodyGoldSaleNameField"; Layout.fillWidth: true; visible: goldSaleDialog.buyerId === "" }
                    Txt { Layout.fillWidth: true; text: "التاريخ (yyyy-MM-dd) *" }
                    Field { id: goldSaleDate; objectName: "custodyGoldSaleDateField"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "المرجع (اختياري)" }
                    Field { id: goldSaleReference; objectName: "custodyGoldSaleReferenceField"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: goldSaleNotes; objectName: "custodyGoldSaleNotesField"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "custodyGoldSaleError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: goldSaleDialog.needsConfirmation ? theme.amber : theme.red
                  wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmCustodyGoldSaleButton"
                    visible: !goldSaleDialog.needsConfirmation
                    text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : "تسجيل البيع"
                    primary: true
                    enabled: !!goldSaleDialog.source.kind && goldSalePrice.text.trim().length > 0 && goldSaleDate.text.trim().length > 0
                             && (goldSaleDialog.isLot || goldSaleWeight.text.trim().length > 0)
                             && (goldSaleDialog.buyerId !== "" || goldSaleName.text.trim().length > 0)
                             && !!page.vm && !page.vm.writing && !goldSaleDialog.preview.error
                    onClicked: goldSaleDialog.submit(false)
                }
                Btn {
                    objectName: "acknowledgeCustodyGoldSalePriceButton"
                    visible: goldSaleDialog.needsConfirmation
                    text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : "أؤكد السعر وأسجّل البيع"
                    primary: true
                    accent: theme.amber
                    enabled: !!page.vm && !page.vm.writing
                    onClicked: goldSaleDialog.submit(true)
                }
                Btn { objectName: "cancelCustodyGoldSaleButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.writing; onClicked: goldSaleDialog.close() }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ---------------------------------------------------------------- Logo heating (حمّية اللوجو)
    // Weights only: the processed LOGO grams leave and the ACTUAL resulting grams return under the same karat and designation.
    Popup {
        id: heatDialog
        objectName: "tripHeatDialog"
        property string tripId: ""
        property string requestKey: ""
        readonly property var stockChoices: [{ karatId: "", text: "اختر العيار" }].concat(page.detail.heatStock || [])
        function openFor(trip) {
            if (!page.vm) return
            tripId = trip
            requestKey = page.vm.newRequestKey()
            heatDateField.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            heatWorkshopField.text = ""
            heatReferenceField.text = ""
            heatGainField.text = ""
            heatNotesField.text = ""
            heatModel.clear()
            heatModel.append({ karatIndex: 0, inputWeight: "", outputWeight: "" })
            page.vm.clearMessage()
            page.vm.refresh()
            open()
        }
        function lines() {
            var rows = []
            for (var i = 0; i < heatModel.count; ++i) {
                var g = heatModel.get(i)
                if (g.inputWeight.trim().length > 0 || g.outputWeight.trim().length > 0 || g.karatIndex > 0)
                    rows.push({ karatId: g.karatIndex > 0 ? stockChoices[g.karatIndex].karatId : "", inputWeight: g.inputWeight, outputWeight: g.outputWeight })
            }
            return rows
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(620, page.width - 24)
        height: Math.min(Overlay.overlay ? Overlay.overlay.height - 24 : 600, 600)
        modal: true
        padding: 14
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        ListModel { id: heatModel }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection; text: "حمّية اللوجو" }
            Txt { objectName: "heatRule"; Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "يُسخَّن ذهب «لوجو» فقط ويبقى بنفس العيار ونفس الوجهة. سجّل الوزن قبل الحمّية والوزن الفعلي بعدها: يُحفظ الفرق نهائيًا كمرجع للمراجعة (ليس شطبًا ولا عجزًا) ويُرجَع الوزن المتبقي فعليًا بالرجوع العادي. لا تتحرك نقدية ولا كسر ولا مورد، ولا تُسجَّل أي نقاوة." }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: heatForm.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: heatForm
                    width: parent.width - 12
                    spacing: 6
                    Txt { objectName: "heatStockText"; Layout.fillWidth: true; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone
                          text: "المتاح للحمّية الآن: " + ((page.detail.heatStock || []).map(function (m) { return m.text }).join(" • ") || "لا يوجد") }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "تاريخ الحمّية (yyyy-MM-dd) *" }
                            Field { id: heatDateField; objectName: "heatDateField"; Layout.fillWidth: true }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 1; Layout.fillWidth: true; spacing: 2
                            Txt { Layout.fillWidth: true; text: "الورشة / المسؤول *" }
                            Field { id: heatWorkshopField; objectName: "heatWorkshopField"; Layout.fillWidth: true }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "السطور: العيار والوزن المُسخَّن قبل الحمّية والوزن الفعلي بعدها (جرام)"; color: theme.gold; font.bold: true }
                    Repeater {
                        model: heatModel
                        delegate: ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            RowLayout {
                                Layout.fillWidth: true
                                layoutDirection: Qt.RightToLeft
                                spacing: 8
                                Combo {
                                    objectName: "heatKarat_" + index
                                    Layout.preferredWidth: 230
                                    model: heatDialog.stockChoices
                                    textRole: "text"
                                    currentIndex: karatIndex
                                    onActivated: function (i) { heatModel.setProperty(index, "karatIndex", i) }
                                }
                                Field {
                                    objectName: "heatInput_" + index
                                    Layout.fillWidth: true
                                    placeholderText: "قبل الحمّية (جرام)"
                                    text: inputWeight
                                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                                    onTextEdited: heatModel.setProperty(index, "inputWeight", text)
                                }
                                Field {
                                    objectName: "heatOutput_" + index
                                    Layout.fillWidth: true
                                    placeholderText: "بعد الحمّية (جرام)"
                                    text: outputWeight
                                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                                    onTextEdited: heatModel.setProperty(index, "outputWeight", text)
                                }
                            }
                            Txt { objectName: "heatDiff_" + index; Layout.fillWidth: true
                                  readonly property string diff: !!page.vm ? page.vm.heatDifference(inputWeight, outputWeight) : ""
                                  visible: diff !== ""; text: "الفرق: " + diff; color: theme.amber; elide: Text.ElideNone }
                        }
                    }
                    Btn { objectName: "addHeatLineButton"; text: "＋ عيار آخر"; accent: theme.textSecondary
                          enabled: heatModel.count < 6
                          onClicked: heatModel.append({ karatIndex: 0, inputWeight: "", outputWeight: "" }) }
                    Txt { Layout.fillWidth: true; text: "سبب زيادة الوزن (مطلوب إذا زاد الوزن بعد الحمّية)" }
                    Field { id: heatGainField; objectName: "heatGainField"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "المرجع (اختياري)" }
                    Field { id: heatReferenceField; objectName: "heatReferenceField"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: heatNotesField; objectName: "heatNotesField"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "heatEntryError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmHeatButton"
                    text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : "تسجيل الحمّية"
                    primary: true
                    enabled: heatDateField.text.trim().length > 0 && heatDialog.lines().length > 0 && !!page.vm && !page.vm.writing
                    onClicked: page.vm.heatCustody(heatDialog.tripId, heatDialog.lines(), heatDateField.text, heatWorkshopField.text,
                                                   heatReferenceField.text, heatGainField.text, heatNotesField.text, heatDialog.requestKey)
                }
                Btn { objectName: "cancelHeatButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.writing; onClicked: heatDialog.close() }
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

    // ---------------------------------------------------------------- reissue whole shop bars into this custody
    // Selection only: the bar leaves the shop stock complete (identity, weight and assay unchanged) and enters THIS custody.
    Popup {
        id: lotIssueDialog
        objectName: "tripLotIssueDialog"
        property string tripId: ""
        property string requestKey: ""
        function openFor(trip) {
            if (!page.vm) return
            tripId = trip
            requestKey = page.vm.newRequestKey()
            lotIssueDate.text = Qt.formatDate(new Date(), "yyyy-MM-dd")
            lotIssueNotes.text = ""
            issuePickModel.clear()
            var lots = page.vm.shopLots || []
            for (var i = 0; i < lots.length; ++i)
                issuePickModel.append({ lotId: lots[i].lotId, ref: lots[i].ref, kindText: lots[i].kindText, fineness: lots[i].fineness,
                                        weight: lots[i].fullWeight, issuable: lots[i].issuable, reason: lots[i].reason, picked: false })
            page.vm.clearMessage()
            page.vm.refresh()
            open()
        }
        function lines() {
            var rows = []
            for (var i = 0; i < issuePickModel.count; ++i) {
                var g = issuePickModel.get(i)
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
        ListModel { id: issuePickModel }
        contentItem: ColumnLayout {
            spacing: 6
            Txt { Layout.fillWidth: true; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection; text: "إخراج سبكة من المحل إلى هذه العهدة" }
            Txt { objectName: "lotIssueRule"; Layout.fillWidth: true; color: theme.amber; font.bold: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "تخرج السبكة كاملة ولا يمكن إخراج جزء منها" }
            Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontTableHeader; wrapMode: Text.WordWrap; elide: Text.ElideNone
                  text: "اختر السبائك الموجودة بالمحل: تبقى بنفس هويتها ووزنها وعيارها الفعلي، وتكون في مكان واحد فقط (المحل أو عهدة واحدة). لا تتحرك نقدية ولا كسر ولا حساب مورد." }
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: lotIssueColumn.implicitHeight
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: lotIssueColumn
                    width: parent.width - 12
                    spacing: 6
                    Txt { Layout.fillWidth: true; text: "تاريخ الإخراج (yyyy-MM-dd) *" }
                    Field { id: lotIssueDate; objectName: "lotIssueDateField"; Layout.fillWidth: true }
                    Txt { Layout.fillWidth: true; visible: issuePickModel.count === 0; text: "لا توجد سبائك بالمحل لإخراجها"; color: theme.textMuted }
                    Repeater {
                        model: issuePickModel
                        delegate: Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: lotIssueRow.implicitHeight + 12
                            radius: theme.radiusSmall
                            color: theme.bgTertiary
                            border.color: picked ? theme.gold : theme.border
                            RowLayout {
                                id: lotIssueRow
                                anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 10; anchors.rightMargin: 10
                                layoutDirection: Qt.RightToLeft
                                spacing: 10
                                CheckBox {
                                    objectName: "lotIssueSelect_" + index
                                    enabled: issuable && !!page.vm && !page.vm.writing
                                    checked: picked
                                    onToggled: issuePickModel.setProperty(index, "picked", checked)
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 2
                                    Txt { objectName: "lotIssueRef_" + index; Layout.fillWidth: true; font.bold: true; elide: Text.ElideNone
                                          text: page.ltr(ref) + "  " + kindText }
                                    Txt { objectName: "lotIssueInfo_" + index; Layout.fillWidth: true; color: theme.textSecondary; wrapMode: Text.WordWrap
                                          elide: Text.ElideNone; text: "العيار الفعلي: " + fineness + " • الوزن الكامل: " + weight + " جرام" }
                                    Txt { objectName: "lotIssueReason_" + index; Layout.fillWidth: true; visible: !issuable; text: reason
                                          color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                }
                            }
                        }
                    }
                    Txt { Layout.fillWidth: true; text: "ملاحظات" }
                    Field { id: lotIssueNotes; objectName: "lotIssueNotesField"; Layout.fillWidth: true }
                }
            }
            Txt { objectName: "lotIssueError"; Layout.fillWidth: true; visible: !!page.vm && page.vm.errorMessage !== ""
                  text: page.vm ? page.vm.errorMessage : ""; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Btn {
                    objectName: "confirmLotIssueButton"
                    text: !!page.vm && page.vm.writing ? "جارٍ الحفظ…" : "إخراج السبائك المحددة"
                    primary: true
                    enabled: lotIssueDialog.hasSelection && lotIssueDate.text.trim().length > 0 && !!page.vm && !page.vm.writing
                    onClicked: page.vm.issueLots(lotIssueDialog.tripId, lotIssueDialog.lines(), lotIssueDate.text, lotIssueNotes.text,
                                                 lotIssueDialog.requestKey)
                }
                Btn { objectName: "cancelLotIssueButton"; text: "إلغاء"; accent: theme.textSecondary
                      enabled: !!page.vm && !page.vm.writing; onClicked: lotIssueDialog.close() }
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
    Dialog {
        id: reconApprovalDialog
        objectName: "reconciliationApprovalDialog"
        property string tripId: ""
        property string requestKey: ""
        readonly property var approval: page.vm ? page.vm.reconciliationApproval : ({})
        readonly property bool ready: !!approval.sourceDigest && approval.tripId === tripId && tripId === page.vm.selectedTripId
        readonly property var reason: approval.reasons ? approval.reasons[reconReason.currentIndex] : null
        function openFor() {
            tripId = page.vm.selectedTripId
            requestKey = page.vm.newRequestKey()
            reconReason.currentIndex = 0
            reconNote.text = ""
            reconDate.text = ""
            reconComplete.checked = false
            reconLoss.checked = false
            reconRounding.checked = false
            page.vm.clearMessage()
            page.vm.prepareReconciliation()
            open()
        }
        onReadyChanged: {
            reconComplete.checked = false; reconLoss.checked = false; reconRounding.checked = false
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(660, page.width - 24)
        height: Math.min(620, page.height - 36)
        modal: true
        padding: 16
        closePolicy: page.vm && page.vm.writing ? Popup.NoAutoClose : Popup.CloseOnEscape
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.gold }
        contentItem: ColumnLayout {
            spacing: 8
            Txt { objectName: "reconciliationApprovalTitle"; Layout.fillWidth: true; text: "اعتماد تسوية العهدة"; color: theme.gold; font.bold: true; font.pixelSize: theme.fontSection }
            Flickable {
                id: approvalScroll
                objectName: "approvalScroll"
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                contentWidth: width; contentHeight: approvalForm.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { }
                ColumnLayout {
                    id: approvalForm
                    width: approvalScroll.width
                    spacing: 8
                    Txt { Layout.fillWidth: true; text: "راجع اكتمال تسجيل الحركات قبل الاعتماد. الاعتماد يسجّل الفاقد المعترف به وفرق التقريب؛ لا يغلق العهدة ولا ينقل ذهبًا أو نقدية."; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    Txt { objectName: "reconciliationApprovalError"; visible: text.length > 0; Layout.fillWidth: true; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone
                          text: reconApprovalDialog.approval.error || (page.vm ? page.vm.errorMessage : "") }
                    Txt { Layout.fillWidth: true; visible: !!reconApprovalDialog.approval.loading; text: reconApprovalDialog.approval.loading ? "جارٍ إعداد المعاينة…" : ""; color: theme.textMuted }
                    ColumnLayout {
                        Layout.fillWidth: true; visible: reconApprovalDialog.ready; spacing: 8
                        Txt { objectName: "approvalFigures"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.gold
                              text: "الرصيد التحليلي: " + page.ltr(reconApprovalDialog.approval.a || "") + "\nالرصيد المرحّل: " + page.ltr(reconApprovalDialog.approval.b || "") + "\nالفاقد المرحّل: " + page.ltr(reconApprovalDialog.approval.loss || "") + "\nفرق التقريب (ليس فاقدًا ولا زيادة): " + page.ltr(reconApprovalDialog.approval.rounding || "") + "\nجميع القيم بجرام مكافئ عيار 21" }
                        Txt { Layout.fillWidth: true; text: reconApprovalDialog.approval.reasonRequired ? "سبب التسوية (إلزامي)" : "سبب التسوية (اختياري)" }
                        Combo { id: reconReason; objectName: "approvalReason"; Layout.fillWidth: true; model: reconApprovalDialog.approval.reasons || []; textRole: "text"; enabled: !page.vm.writing }
                        Txt { Layout.fillWidth: true; text: reconApprovalDialog.reason && reconApprovalDialog.reason.noteRequired ? "ملاحظة توضيحية (إلزامية)" : "ملاحظة توضيحية (اختيارية)" }
                        Field { id: reconNote; objectName: "approvalNote"; Layout.fillWidth: true; maximumLength: 1000; enabled: !page.vm.writing }
                        Txt { Layout.fillWidth: true; text: "التاريخ الفعلي — اتركه فارغًا لاعتماد يوم الخادم" }
                        Field { id: reconDate; objectName: "approvalDate"; Layout.fillWidth: true; placeholderText: reconApprovalDialog.approval.defaultDate || "YYYY-MM-DD"; maximumLength: 10; enabled: !page.vm.writing }
                        Txt { Layout.fillWidth: true; text: "أقدم تاريخ مسموح: " + page.ltr(reconApprovalDialog.approval.dateFloor || "") + " • اليوم: " + page.ltr(reconApprovalDialog.approval.defaultDate || ""); color: theme.textMuted; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                        CheckBox { id: reconComplete; objectName: "approvalRecordingComplete"; Layout.fillWidth: true; enabled: !page.vm.writing; font.family: "Cairo"
                                   indicator: Rectangle { implicitWidth: 18; implicitHeight: 18; x: reconComplete.width - width; y: (reconComplete.height - height) / 2; radius: 3
                                               border.color: theme.gold; color: reconComplete.checked ? theme.gold : theme.bgPrimary
                                               Canvas { anchors.fill: parent; property bool checked: reconComplete.checked; onCheckedChanged: requestPaint()
                                                        onPaint: { var c = getContext("2d"); c.clearRect(0, 0, width, height); if (checked) { c.strokeStyle = theme.bgPrimary; c.lineWidth = 2; c.beginPath(); c.moveTo(4, 9); c.lineTo(8, 13); c.lineTo(14, 5); c.stroke(); } } } }
                                   text: (reconApprovalDialog.approval.confirmations || {}).recording_complete || ""; contentItem: Txt { text: reconComplete.text; wrapMode: Text.WordWrap; elide: Text.ElideNone; rightPadding: reconComplete.indicator.width + 8; leftPadding: 0 } }
                        CheckBox { id: reconLoss; objectName: "approvalLossRecognised"; Layout.fillWidth: true; enabled: !page.vm.writing; font.family: "Cairo"
                                   indicator: Rectangle { implicitWidth: 18; implicitHeight: 18; x: reconLoss.width - width; y: (reconLoss.height - height) / 2; radius: 3
                                               border.color: theme.gold; color: reconLoss.checked ? theme.gold : theme.bgPrimary
                                               Canvas { anchors.fill: parent; property bool checked: reconLoss.checked; onCheckedChanged: requestPaint()
                                                        onPaint: { var c = getContext("2d"); c.clearRect(0, 0, width, height); if (checked) { c.strokeStyle = theme.bgPrimary; c.lineWidth = 2; c.beginPath(); c.moveTo(4, 9); c.lineTo(8, 13); c.lineTo(14, 5); c.stroke(); } } } }
                                   text: (reconApprovalDialog.approval.confirmations || {}).loss_recognised_as_posted || ""; contentItem: Txt { text: reconLoss.text; wrapMode: Text.WordWrap; elide: Text.ElideNone; rightPadding: reconLoss.indicator.width + 8; leftPadding: 0 } }
                        CheckBox { id: reconRounding; objectName: "approvalRoundingSeparate"; Layout.fillWidth: true; enabled: !page.vm.writing; font.family: "Cairo"
                                   indicator: Rectangle { implicitWidth: 18; implicitHeight: 18; x: reconRounding.width - width; y: (reconRounding.height - height) / 2; radius: 3
                                               border.color: theme.gold; color: reconRounding.checked ? theme.gold : theme.bgPrimary
                                               Canvas { anchors.fill: parent; property bool checked: reconRounding.checked; onCheckedChanged: requestPaint()
                                                        onPaint: { var c = getContext("2d"); c.clearRect(0, 0, width, height); if (checked) { c.strokeStyle = theme.bgPrimary; c.lineWidth = 2; c.beginPath(); c.moveTo(4, 9); c.lineTo(8, 13); c.lineTo(14, 5); c.stroke(); } } } }
                                   text: (reconApprovalDialog.approval.confirmations || {}).rounding_separate || ""; contentItem: Txt { text: reconRounding.text; wrapMode: Text.WordWrap; elide: Text.ElideNone; rightPadding: reconRounding.indicator.width + 8; leftPadding: 0 } }
                    }
                }
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                Btn { objectName: "refreshApprovalPreview"; text: "إعادة قراءة المعاينة"; visible: !reconApprovalDialog.ready && !reconApprovalDialog.approval.loading; enabled: !page.vm.writing; onClicked: page.vm.prepareReconciliation() }
                Btn { objectName: "confirmReconciliationApproval"; text: page.vm && page.vm.writing ? "جارٍ الاعتماد…" : "اعتماد التسوية"; primary: true
                      enabled: reconApprovalDialog.ready && !page.vm.writing && reconComplete.checked && reconLoss.checked && reconRounding.checked
                               && (!reconApprovalDialog.approval.reasonRequired || !!(reconApprovalDialog.reason && reconApprovalDialog.reason.id !== ""))
                               && (!(reconApprovalDialog.reason && reconApprovalDialog.reason.noteRequired) || reconNote.text.trim().length > 0)
                      onClicked: page.vm.approveReconciliation(reconApprovalDialog.tripId, reconApprovalDialog.reason ? reconApprovalDialog.reason.id : "", reconNote.text, reconDate.text,
                                {recording_complete: reconComplete.checked, loss_recognised_as_posted: reconLoss.checked, rounding_separate: reconRounding.checked}, reconApprovalDialog.requestKey) }
                Btn { objectName: "cancelReconciliationApproval"; text: "إلغاء"; enabled: !page.vm.writing; onClicked: reconApprovalDialog.close() }
            }
        }
    }

    Popup {
        id: reasonDialog
        objectName: "tripReasonDialog"
        property string mode: "REVERSE"
        property string targetId: ""
        property string targetCode: ""
        property string targetNote: ""
        function openFor(kind, id, code, note) {
            mode = kind
            targetId = id
            targetCode = code
            targetNote = note || ""
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
            Txt { objectName: "tripReasonNote"; Layout.fillWidth: true; visible: reasonDialog.mode === "REVERSE" && reasonDialog.targetNote !== ""
                  text: reasonDialog.targetNote; color: theme.amber; wrapMode: Text.WordWrap; elide: Text.ElideNone }
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
