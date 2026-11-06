import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// مراجعات المدير: one place for every review type (inventory, pricing, settlement, discount)
// and the manager's real financial accounts. Reviews are read-only in this stage; account
// setup (new account, activation, one opening balance) is manager-only and audited.
Item {
    id: reviewPage
    objectName: "managementReviewPage"

    property string pageTitle: ""
    // The source of a review / a ledger movement is opened through the application's existing pages.
    signal invoiceRequested(string invoiceId)
    signal customerRequested(string customerId)
    signal reportsRequested()
    signal openingBalanceRequested()
    signal legacyVoidRequested()
    signal scrapAdjustRequested()
    signal claspRequested()
    property bool claspAvailable: false
    signal inventoryRequested()
    property bool legacyVoidAvailable: false
    property bool scrapAdjustAvailable: false
    property bool reportsAvailable: false
    property bool openingAvailable: false

    Theme { id: theme }

    onVisibleChanged: if (visible && reviewsVM.canOpen) reviewsVM.openReviews()

    Connections {
        target: reviewsVM
        function onInvoiceRequested(invoiceId) { reviewPage.invoiceRequested(invoiceId) }
    }
    Connections {
        target: reviewsVM.history
        function onCustomerRequested(customerId) { reviewPage.customerRequested(customerId) }
    }

    Rectangle {
        anchors.fill: parent
        color: theme.bgPrimary
    }

    component Section: ColumnLayout {
        id: section
        property string title: ""
        property int count: 0
        property bool showCount: true
        Layout.fillWidth: true
        spacing: 4
        Label {
            Layout.fillWidth: true
            text: section.showCount ? section.title + "  •  " + section.count : section.title
            color: section.count > 0 ? theme.amber : theme.textMuted
            font.family: "Cairo"
            font.pixelSize: 14
            font.bold: true
            horizontalAlignment: Text.AlignRight
        }
    }

    // A click on the card background opens the review's SOURCE (immutable invoice id). It is the first
    // child, i.e. BELOW the card's content: buttons and the barcode box keep their own clicks and a click
    // on a button never navigates.
    component SourceArea: MouseArea {
        property string invoiceId: ""
        anchors.fill: parent
        hoverEnabled: true
        enabled: invoiceId.length > 0
        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        ToolTip.visible: enabled && containsMouse
        ToolTip.delay: 600
        ToolTip.text: "فتح الفاتورة"
        onClicked: reviewsVM.openSource(invoiceId)
    }

    property string newAccountType: ""
    property bool newAccountOpen: false
    property bool transferOpen: false
    property bool goldDetailsOpen: false
    readonly property var goldPos: reviewsVM.goldPosition
    readonly property bool goldLoaded: goldPos !== undefined && goldPos.summary !== undefined

    // One real account: balance, money in / out, latest movements, and (manager only)
    // activation plus a one-time opening balance while the account has never moved.
    component AccountRow: Rectangle {
        id: accountRow
        property var account: ({})
        property int rowIndex: 0
        Layout.fillWidth: true
        implicitHeight: accountColumn.implicitHeight + 10
        color: accountHover.hovered ? theme.panelHover : theme.bgSecondary
        radius: 4
        HoverHandler { id: accountHover }
        // The whole card opens the account's movement history (activation / opening-balance controls keep their clicks).
        MouseArea {
            objectName: "accountOpenHistory_" + accountRow.rowIndex
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: reviewsVM.history.open(accountRow.account.accountKey, accountRow.account.name)
        }
        ColumnLayout {
            id: accountColumn
            anchors.fill: parent
            anchors.leftMargin: 10
            anchors.rightMargin: 12
            anchors.topMargin: 5
            anchors.bottomMargin: 5
            spacing: 2
            // Summary only: name, type, state and balance. Movements belong to a separate drill-down.
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Label {
                    objectName: "accountName_" + accountRow.rowIndex
                    text: accountRow.account.name || ""
                    color: accountRow.account.active ? theme.textPrimary : theme.textMuted
                    font.family: "Cairo"
                    font.pixelSize: 13
                    font.bold: true
                }
                Rectangle {
                    Layout.preferredHeight: 18
                    Layout.preferredWidth: typeLabel.implicitWidth + 12
                    radius: 3
                    color: "transparent"
                    border.color: theme.border
                    Label {
                        id: typeLabel
                        objectName: "accountType_" + accountRow.rowIndex
                        anchors.centerIn: parent
                        text: accountRow.account.typeText || ""
                        color: theme.textSecondary
                        font.family: "Cairo"
                        font.pixelSize: 10
                    }
                }
                Label {
                    objectName: "accountState_" + accountRow.rowIndex
                    text: accountRow.account.stateText || ""
                    color: accountRow.account.active ? theme.green : theme.amber
                    font.family: "Cairo"
                    font.pixelSize: 11
                }
                Item { Layout.fillWidth: true }
                Button {
                    objectName: "toggleAccount_" + accountRow.rowIndex
                    visible: accountRow.account.canManage === true
                    id: toggleButton
                    text: accountRow.account.active ? "إيقاف" : "تفعيل"
                    font.family: "Cairo"
                    font.pixelSize: 11
                    Layout.preferredHeight: 24
                    topInset: 0
                    bottomInset: 0
                    leftPadding: 10
                    rightPadding: 10
                    enabled: !reviewsVM.accountBusy
                    contentItem: Label {
                        text: toggleButton.text
                        font: toggleButton.font
                        color: toggleButton.enabled ? theme.textSecondary : theme.textDisabled
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: theme.radiusSmall
                        color: toggleButton.down ? theme.panelHover : "transparent"
                        border.color: theme.border
                    }
                    onClicked: reviewsVM.setAccountActive(accountRow.account.accountId, !accountRow.account.active)
                }
                Label {
                    text: "عرض الحركات ◂"
                    color: theme.gold
                    font.family: "Cairo"
                    font.pixelSize: 11
                }
                Label {
                    objectName: "accountBalance_" + accountRow.rowIndex
                    text: accountRow.account.balance || ""
                    color: theme.goldBright
                    font.family: "Cairo"
                    font.pixelSize: 14
                    font.bold: true
                }
            }
            RowLayout {
                Layout.fillWidth: true
                // Only while a one-time opening balance can still be recorded.
                visible: accountRow.account.canInitialize === true
                layoutDirection: Qt.RightToLeft
                spacing: 6
                Label {
                    visible: accountRow.account.canInitialize === true
                    text: "الرصيد الافتتاحي (مرة واحدة)"
                    color: theme.amber
                    font.family: "Cairo"
                    font.pixelSize: 11
                }
                TextField {
                    id: openingField
                    objectName: "openingAmount_" + accountRow.rowIndex
                    visible: accountRow.account.canInitialize === true
                    Layout.preferredWidth: 110
                    font.family: "Cairo"
                    font.pixelSize: 12
                    horizontalAlignment: TextInput.AlignRight
                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                    placeholderText: text.length > 0 ? "" : "المبلغ المعدود"
                }
                Button {
                    objectName: "saveOpening_" + accountRow.rowIndex
                    visible: accountRow.account.canInitialize === true
                    text: "تسجيل"
                    font.family: "Cairo"
                    enabled: !reviewsVM.accountBusy && openingField.text.trim().length > 0
                    onClicked: reviewsVM.initializeOpeningBalance(accountRow.account.ledger, accountRow.account.accountId,
                                                                  openingField.text)
                }
                Item { Layout.fillWidth: true }
            }
        }
    }

    component ActionButton: Button {
        id: actionButton
        property color accent: theme.gold
        font.family: "Cairo"
        font.pixelSize: 12
        font.bold: true
        Layout.preferredHeight: 28
        topInset: 0
        bottomInset: 0
        leftPadding: 12
        rightPadding: 12
        contentItem: Label {
            text: actionButton.text
            font: actionButton.font
            color: actionButton.enabled ? actionButton.accent : theme.textDisabled
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: theme.radiusSmall
            color: actionButton.down ? theme.panelHover : "transparent"
            border.color: actionButton.enabled ? actionButton.accent : theme.border
        }
    }

    // One returned piece waiting for the manager: exchange, invoice, customer, piece facts,
    // the STORED barcode (authoritative, selectable, never elided) and the two outcomes.
    component InspectionCard: Rectangle {
        id: card
        property var review: ({})
        property int rowIndex: 0
        Layout.fillWidth: true
        implicitHeight: cardColumn.implicitHeight + 12
        color: cardSource.containsMouse ? theme.panelHover : theme.bgSecondary
        radius: 4
        SourceArea { id: cardSource; objectName: "inspectionSource_" + card.rowIndex; invoiceId: card.review.invoiceId || "" }
        ColumnLayout {
            id: cardColumn
            anchors.fill: parent
            anchors.leftMargin: 10
            anchors.rightMargin: 12
            anchors.topMargin: 6
            anchors.bottomMargin: 6
            spacing: 2
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 10
                Label { text: card.review.sourceTitle || ""; color: theme.gold; font.family: "Cairo"
                        font.pixelSize: 13; font.bold: true }
                Label { text: "فاتورة " + (card.review.invoice || ""); color: theme.textPrimary; font.family: "Cairo"
                        font.pixelSize: 12 }
                Label { text: card.review.customer || ""; color: theme.textSecondary; font.family: "Cairo"
                        font.pixelSize: 12 }
                Item { Layout.fillWidth: true }
                Label { text: card.review.time || ""; color: theme.textMuted; font.family: "Cairo"; font.pixelSize: 11 }
            }
            Label {
                objectName: "inspectionDetails_" + card.rowIndex
                Layout.fillWidth: true
                text: [card.review.item, card.review.category, card.review.karat ? "عيار " + card.review.karat : "",
                       "الوزن " + card.review.weight, card.review.factory,
                       card.review.size ? "مقاس " + card.review.size : "", card.review.notes]
                      .filter(function(t) { return t && t.length > 0 }).join("  •  ")
                color: theme.textSecondary
                font.family: "Cairo"
                font.pixelSize: 12
                horizontalAlignment: Text.AlignRight
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 10
                Label { text: "الباركود المسجل:"; color: theme.textSecondary; font.family: "Cairo"; font.pixelSize: 12 }
                // Selectable, full value (never elided); a tooltip repeats it on hover.
                TextEdit {
                    id: barcodeValue
                    objectName: "inspectionBarcode_" + card.rowIndex
                    text: card.review.barcode || "غير مسجل"
                    readOnly: true
                    selectByMouse: true
                    color: theme.goldBright
                    selectionColor: theme.gold
                    font.family: "Cairo"
                    font.pixelSize: 13
                    font.bold: true
                    HoverHandler { id: barcodeHover }
                    ToolTip.visible: barcodeHover.hovered && text.length > 0
                    ToolTip.text: text
                }
                Label { text: "الحالة: " + (card.review.statusText || ""); color: theme.amber; font.family: "Cairo"
                        font.pixelSize: 12 }
                Item { Layout.fillWidth: true }
                ActionButton {
                    objectName: "inspectionRestock_" + card.rowIndex
                    visible: reviewsVM.canResolveInspections
                    enabled: !reviewsVM.inspectionBusy
                    text: "إرجاع للمخزون"
                    accent: theme.priceBuy
                    onClicked: confirmDialog.ask(card.review, "RETURN_TO_STOCK")
                }
                ActionButton {
                    objectName: "inspectionScrap_" + card.rowIndex
                    visible: reviewsVM.canResolveInspections
                    enabled: !reviewsVM.inspectionBusy
                    text: "تحويل إلى الكسر"
                    accent: theme.amber
                    onClicked: confirmDialog.ask(card.review, "CONVERT_TO_OLD_GOLD")
                }
            }
        }
    }

    // قطعة استبدال داخل الكسر (032): the returned piece already IS in Scrap (its weight held for this
    // decision). Shows where it is now, the piece / exchange / Scrap facts, the authoritative history
    // and the two decisions. Every value comes from reviewsVM (service rows); nothing is computed here.
    component Fact: ColumnLayout {
        property string label: ""
        property string value: ""
        property color valueColor: theme.textPrimary
        spacing: 0
        Layout.fillWidth: true
        Label { Layout.fillWidth: true; text: parent.label; color: theme.textMuted; font.family: "Cairo"
                font.pixelSize: 11; horizontalAlignment: Text.AlignRight }
        Label { Layout.fillWidth: true; text: parent.value || "—"; color: parent.valueColor; font.family: "Cairo"
                font.pixelSize: 12; font.bold: true; horizontalAlignment: Text.AlignRight; wrapMode: Text.WordWrap }
    }

    component FactGroup: Rectangle {
        id: group
        property string title: ""
        default property alias facts: groupColumn.data
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        implicitHeight: groupColumn.implicitHeight + 12
        color: theme.bgTertiary
        radius: 4
        border.color: theme.cardBorder
        ColumnLayout {
            id: groupColumn
            anchors.fill: parent
            anchors.margins: 6
            spacing: 3
            Label { Layout.fillWidth: true; text: group.title; color: theme.gold; font.family: "Cairo"
                    font.pixelSize: 12; font.bold: true; horizontalAlignment: Text.AlignRight }
        }
    }

    component ScrapPieceCard: Rectangle {
        id: scrapCard
        property var review: ({})
        property int rowIndex: 0
        property bool expanded: false
        Layout.fillWidth: true
        implicitHeight: scrapColumn.implicitHeight + 12
        color: scrapSource.containsMouse ? theme.panelHover : theme.bgSecondary
        radius: theme.radiusMedium
        border.color: theme.cardBorder
        SourceArea { id: scrapSource; objectName: "scrapSource_" + scrapCard.rowIndex; invoiceId: scrapCard.review.invoiceId || "" }
        ColumnLayout {
            id: scrapColumn
            anchors.fill: parent
            anchors.leftMargin: 10
            anchors.rightMargin: 12
            anchors.topMargin: 6
            anchors.bottomMargin: 6
            spacing: 4
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 10
                Label { objectName: "scrapExchange_" + scrapCard.rowIndex; text: scrapCard.review.exchange || ""
                        color: theme.gold; font.family: "Cairo"; font.pixelSize: 13; font.bold: true }
                Label { text: "فاتورة " + (scrapCard.review.invoice || ""); color: theme.textPrimary
                        font.family: "Cairo"; font.pixelSize: 12 }
                Label { text: scrapCard.review.customer || ""; color: theme.textSecondary; font.family: "Cairo"
                        font.pixelSize: 12; elide: Text.ElideLeft; Layout.maximumWidth: 200 }
                Item { Layout.fillWidth: true }
                Label { text: scrapCard.review.time || ""; color: theme.textMuted; font.family: "Cairo"; font.pixelSize: 11 }
            }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 10
                Label { text: [scrapCard.review.item, scrapCard.review.karat ? "عيار " + scrapCard.review.karat : "",
                               scrapCard.review.weight + " جرام"].filter(function(t) { return t && t.length > 0 }).join("  •  ")
                        color: theme.textPrimary; font.family: "Cairo"; font.pixelSize: 12; font.bold: true }
                Label { text: "الباركود:"; color: theme.textSecondary; font.family: "Cairo"; font.pixelSize: 12 }
                TextEdit {
                    objectName: "scrapBarcode_" + scrapCard.rowIndex
                    text: scrapCard.review.barcode || "بدون باركود"
                    readOnly: true
                    selectByMouse: true
                    color: theme.goldBright
                    selectionColor: theme.gold
                    font.family: "Cairo"
                    font.pixelSize: 13
                    font.bold: true
                }
                Label { objectName: "scrapItemId_" + scrapCard.rowIndex; text: "رقم القطعة #" + (scrapCard.review.itemId || "")
                        color: theme.textSecondary; font.family: "Cairo"; font.pixelSize: 12 }
                Item { Layout.fillWidth: true }
                // The SAME stored barcode, reprinted through the barcode printer (never a new one).
                ActionButton {
                    objectName: "scrapReprint_" + scrapCard.rowIndex
                    visible: !!scrapCard.review.barcode
                    text: "إعادة طباعة الباركود"
                    accent: theme.goldBright
                    onClicked: reviewsVM.reprintBarcode(scrapCard.review.itemId)
                }
            }
            // Where the piece physically is now: never «nowhere».
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Rectangle { Layout.preferredWidth: 8; Layout.preferredHeight: 8; radius: 4; color: theme.priceBuy }
                Label { objectName: "scrapDestination_" + scrapCard.rowIndex
                        text: "الموقع الحالي: " + (scrapCard.review.scrapDestination || "")
                        color: theme.priceBuy; font.family: "Cairo"; font.pixelSize: 12; font.bold: true }
                Label { objectName: "scrapState_" + scrapCard.rowIndex
                        text: "محوّلة إلى الكسر — " + (scrapCard.review.scrapState || "")
                        color: theme.amber; font.family: "Cairo"; font.pixelSize: 12 }
                Item { Layout.fillWidth: true }
            }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Button {
                    id: restockButton
                    objectName: "scrapRestock_" + scrapCard.rowIndex
                    visible: reviewsVM.canResolveInspections
                    enabled: !reviewsVM.inspectionBusy
                    text: "إرجاع من الكسر إلى المخزون"
                    font.family: "Cairo"
                    font.pixelSize: 12
                    font.bold: true
                    Layout.preferredHeight: 30
                    topInset: 0
                    bottomInset: 0
                    leftPadding: 14
                    rightPadding: 14
                    contentItem: Label { text: restockButton.text; font: restockButton.font
                                         color: restockButton.enabled ? theme.bgPrimary : theme.textDisabled
                                         horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    background: Rectangle { radius: theme.radiusSmall
                                            color: restockButton.enabled ? (restockButton.down ? Qt.darker(theme.priceBuy, 1.2) : theme.priceBuy)
                                                                         : theme.bgTertiary }
                    onClicked: confirmDialog.ask(scrapCard.review, "RETURN_TO_STOCK")
                }
                ActionButton {
                    objectName: "scrapCommit_" + scrapCard.rowIndex
                    visible: reviewsVM.canResolveInspections
                    enabled: !reviewsVM.inspectionBusy
                    text: "تثبيت في الكسر"
                    accent: theme.amber
                    onClicked: confirmDialog.ask(scrapCard.review, "CONVERT_TO_OLD_GOLD")
                }
                Item { Layout.fillWidth: true }
                ActionButton {
                    objectName: "scrapToggle_" + scrapCard.rowIndex
                    text: scrapCard.expanded ? "إخفاء التفاصيل" : "التفاصيل وسجل الحركة"
                    accent: theme.textSecondary
                    onClicked: scrapCard.expanded = !scrapCard.expanded
                }
            }
            GridLayout {
                objectName: "scrapDetails_" + scrapCard.rowIndex
                visible: scrapCard.expanded
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                columns: scrapCard.width >= 700 ? 3 : 1
                columnSpacing: 8
                rowSpacing: 8
                FactGroup {
                    title: "بيانات القطعة"
                    Fact { label: "الصنف"; value: scrapCard.review.item || "" }
                    Fact { label: "العيار"; value: scrapCard.review.karat || "" }
                    Fact { label: "الوزن"; value: (scrapCard.review.weight || "") + " جرام" }
                    Fact { label: "الباركود"; value: scrapCard.review.barcode || "بدون باركود"; valueColor: theme.goldBright }
                    Fact { label: "رقم القطعة"; value: "#" + (scrapCard.review.itemId || "") }
                }
                FactGroup {
                    title: scrapCard.review.isReturn ? "بيانات المرتجع" : "بيانات الاستبدال"
                    Fact { label: scrapCard.review.isReturn ? "المرتجع" : "الاستبدال"; value: scrapCard.review.exchange || ""
                           valueColor: theme.gold }
                    Fact { label: "الفاتورة الأصلية"; value: scrapCard.review.invoice || "" }
                    Fact { label: "العميل"; value: scrapCard.review.customer || "" }
                    Fact { label: scrapCard.review.isReturn ? "وقت المرتجع" : "وقت الاستبدال"; value: scrapCard.review.time || "" }
                    Fact { label: "سطر الفاتورة الأصلي"; value: scrapCard.review.sourceLine ? "#" + scrapCard.review.sourceLine : "" }
                }
                FactGroup {
                    title: "الكسر الحالي"
                    Fact { label: "المكان"; value: scrapCard.review.scrapDestination || ""; valueColor: theme.priceBuy }
                    Fact { label: "رصيد العيار (فعلي / متاح)"
                           value: (scrapCard.review.karatPhysical || "") + " / " + (scrapCard.review.karatAvailable || "") + " جرام" }
                    Fact { label: "تاريخ الدخول للكسر"; value: scrapCard.review.scrapEnteredAt || "" }
                    Fact { label: "الحالة"; value: scrapCard.review.scrapState || ""; valueColor: theme.amber }
                }
            }
            ColumnLayout {
                objectName: "scrapTimeline_" + scrapCard.rowIndex
                visible: scrapCard.expanded
                Layout.fillWidth: true
                spacing: 2
                Label { Layout.fillWidth: true; text: "سجل الحركة"; color: theme.gold; font.family: "Cairo"
                        font.pixelSize: 12; font.bold: true; horizontalAlignment: Text.AlignRight }
                Repeater {
                    model: scrapCard.review.timeline || []
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Rectangle { Layout.preferredWidth: 6; Layout.preferredHeight: 6; radius: 3; color: theme.gold }
                        Label { objectName: "scrapEvent_" + scrapCard.rowIndex + "_" + index; text: modelData.title
                                color: theme.textPrimary; font.family: "Cairo"; font.pixelSize: 12; font.bold: true }
                        Label { text: modelData.detail; color: theme.textSecondary; font.family: "Cairo"; font.pixelSize: 11
                                Layout.fillWidth: true; horizontalAlignment: Text.AlignRight; elide: Text.ElideLeft }
                        Label { text: [modelData.by, modelData.at].filter(function(t) { return t && t.length > 0 }).join("  •  ")
                                color: theme.textMuted; font.family: "Cairo"; font.pixelSize: 11 }
                    }
                }
            }
        }
    }

    // The manager confirms each outcome (the service refuses a second decision anyway).
    Popup {
        id: confirmDialog
        objectName: "inspectionConfirm"
        property var review: ({})
        property string outcome: ""
        function ask(review, outcome) { confirmDialog.review = review; confirmDialog.outcome = outcome; open() }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(520, reviewPage.width - 32)
        modal: true
        focus: true
        padding: 16
        background: Rectangle { color: theme.bgSecondary; radius: 6; border.color: theme.amber }
        contentItem: ColumnLayout {
            spacing: 10
            Label {
                Layout.fillWidth: true
                text: confirmDialog.review.inScrap
                      ? (confirmDialog.outcome === "RETURN_TO_STOCK" ? "إرجاع من الكسر إلى المخزون" : "تثبيت في الكسر")
                      : (confirmDialog.outcome === "RETURN_TO_STOCK" ? "إرجاع القطعة للمخزون" : "تحويل القطعة إلى الكسر")
                color: theme.gold
                font.family: "Cairo"
                font.pixelSize: 15
                font.bold: true
                horizontalAlignment: Text.AlignRight
            }
            Label {
                objectName: "inspectionConfirmText"
                Layout.fillWidth: true
                text: confirmDialog.review.inScrap
                      ? (confirmDialog.outcome === "RETURN_TO_STOCK"
                         ? "سيتم إخراج وزن القطعة من الكسر وإعادة نفس القطعة إلى المخزون بنفس الباركود. هل تريد المتابعة؟"
                         : "سيتم تثبيت القطعة داخل رصيد الكسر وإتاحتها ككسر عادي. لن تعود قطعة مخزون إلا من خلال مسار مستقل معتمد. هل تريد المتابعة؟")
                      : (confirmDialog.outcome === "RETURN_TO_STOCK"
                         ? "ستعود القطعة للمخزون بنفس الباركود المسجل: " + (confirmDialog.review.barcode || "غير مسجل")
                         : "سيتم تحويل القطعة نهائياً إلى رصيد الكسر، ولن تعود قابلة للبيع بالباركود المسجل. هل تريد المتابعة؟")
                color: theme.textPrimary
                font.family: "Cairo"
                font.pixelSize: 13
                horizontalAlignment: Text.AlignRight
                wrapMode: Text.WordWrap
            }
            Label {
                objectName: "inspectionConfirmFacts"
                Layout.fillWidth: true
                text: "الباركود المسجل: " + (confirmDialog.review.barcode || "غير مسجل")
                      + (confirmDialog.review.inScrap ? "  •  " + (confirmDialog.review.item || "") : "")
                      + "  •  عيار " + (confirmDialog.review.karat || "") + "  •  الوزن " + (confirmDialog.review.weight || "") + " جرام"
                      + (confirmDialog.review.inScrap ? "  •  " + (confirmDialog.review.exchange || "") : "")
                color: confirmDialog.outcome === "RETURN_TO_STOCK" ? theme.priceBuy : theme.amber
                font.family: "Cairo"
                font.pixelSize: 12
                font.bold: true
                horizontalAlignment: Text.AlignRight
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                ActionButton {
                    objectName: "inspectionConfirmYes"
                    text: "متابعة"
                    accent: confirmDialog.outcome === "RETURN_TO_STOCK" ? theme.priceBuy : theme.amber
                    onClicked: {
                        if (confirmDialog.outcome === "RETURN_TO_STOCK") reviewsVM.returnToStock(confirmDialog.review.reviewId)
                        else reviewsVM.convertToOldGold(confirmDialog.review.reviewId)
                        confirmDialog.close()
                    }
                }
                ActionButton {
                    objectName: "inspectionConfirmNo"
                    text: "إلغاء"
                    accent: theme.textSecondary
                    onClicked: confirmDialog.close()
                }
                Item { Layout.fillWidth: true }
            }
        }
    }

    component Row2: Rectangle {
        id: row
        property string title: ""
        property string line1: ""
        property string line2: ""
        property string side: ""
        property string invoiceId: ""
        Layout.fillWidth: true
        implicitHeight: 52
        color: rowSource.containsMouse ? theme.panelHover : theme.bgSecondary
        radius: 4
        SourceArea { id: rowSource; objectName: "reviewSource_" + row.invoiceId; invoiceId: row.invoiceId }
        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: 10
            anchors.rightMargin: 12
            spacing: 0
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                Label { text: row.title; color: theme.textPrimary; font.family: "Cairo"; font.pixelSize: 13; font.bold: true }
                Item { Layout.fillWidth: true }
                Label { text: row.side; color: theme.textMuted; font.family: "Cairo"; font.pixelSize: 11 }
            }
            Label {
                Layout.fillWidth: true
                text: row.line1
                color: theme.textSecondary
                font.family: "Cairo"
                font.pixelSize: 12
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideLeft
            }
            Label {
                Layout.fillWidth: true
                visible: text.length > 0
                text: row.line2
                color: theme.textMuted
                font.family: "Cairo"
                font.pixelSize: 11
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideLeft
            }
        }
    }

    // An unidentified (F5) sale waiting for the manager: link the real piece from stock, take the weight from the Scrap, or record
    // that no source exists. Settling removes the card; the sale-out button asks twice because it changes the Scrap.
    component CaseCard: Rectangle {
        id: caseCard
        property var review: ({})
        property int rowIndex: 0
        property string armed: ""                         // "" | "SCRAP" | "NONE": the action waiting for its second click
        readonly property bool busy: !!review.busy || reviewsVM.inspectionBusy
        Layout.fillWidth: true
        implicitHeight: caseBody.implicitHeight + 20
        color: caseArea.containsMouse ? theme.panelHover : theme.bgSecondary
        border.color: theme.border
        radius: 6
        SourceArea { id: caseArea; objectName: "reviewSource_" + caseCard.review.invoiceId; invoiceId: caseCard.review.invoiceId || "" }
        Rectangle { anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom; width: 4; radius: 2; color: theme.amber }
        Timer { id: disarm; interval: 6000; onTriggered: caseCard.armed = "" }
        ColumnLayout {
            id: caseBody
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 10
            anchors.rightMargin: 16
            spacing: 6
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 10
                Label { text: caseCard.review.title + " — " + caseCard.review.reason; color: theme.textPrimary; font.family: "Cairo"; font.pixelSize: 14; font.bold: true }
                Item { Layout.fillWidth: true }
                Label { text: (caseCard.review.time || "") + (caseCard.review.salesperson ? " • " + caseCard.review.salesperson : ""); color: theme.textMuted; font.family: "Cairo"; font.pixelSize: 11 }
            }
            Label {
                Layout.fillWidth: true
                text: (caseCard.review.details || "") + "  •  " + (caseCard.review.customer || "") + (caseCard.review.notes ? "  •  " + caseCard.review.notes : "")
                color: theme.textSecondary
                font.family: "Cairo"
                font.pixelSize: 12
                horizontalAlignment: Text.AlignRight
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                visible: reviewsVM.canResolveInspections && caseCard.review.canSettle === true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                TextField {
                    id: caseBarcode
                    objectName: "caseBarcode_" + caseCard.rowIndex
                    Layout.preferredWidth: 190
                    placeholderText: "باركود القطعة من المخزن"
                    font.family: "Cairo"
                    horizontalAlignment: TextInput.AlignRight
                }
                ActionButton {
                    objectName: "caseMatch_" + caseCard.rowIndex
                    text: "ربط بالقطعة"
                    accent: theme.green
                    enabled: !caseCard.busy && caseBarcode.text.trim().length > 0
                    onClicked: reviewsVM.matchCaseToStock(caseCard.review.caseId, caseBarcode.text.trim())
                }
                Item { Layout.preferredWidth: 14 }
                ActionButton {
                    objectName: "caseScrap_" + caseCard.rowIndex
                    text: caseCard.armed === "SCRAP" ? "تأكيد: سحب " + caseCard.review.weight + " جرام عيار " + caseCard.review.karat + " من الكسر" : "سحب من الكسر"
                    accent: caseCard.armed === "SCRAP" ? theme.red : theme.gold
                    enabled: !caseCard.busy
                    onClicked: {
                        if (caseCard.armed === "SCRAP") {
                            caseCard.armed = ""
                            reviewsVM.takeCaseFromScrap(caseCard.review.caseId)
                        } else {
                            caseCard.armed = "SCRAP"
                            disarm.restart()
                        }
                    }
                }
                Item { Layout.fillWidth: true }
            }
            RowLayout {
                Layout.fillWidth: true
                visible: reviewsVM.canResolveInspections && caseCard.review.canSettle === true
                layoutDirection: Qt.RightToLeft
                spacing: 8
                TextField {
                    id: caseReason
                    objectName: "caseReason_" + caseCard.rowIndex
                    Layout.fillWidth: true
                    placeholderText: "لا يوجد مصدر للقطعة (لا في المخزن ولا في الكسر): اكتب السبب"
                    font.family: "Cairo"
                    horizontalAlignment: TextInput.AlignRight
                }
                ActionButton {
                    objectName: "caseNoSource_" + caseCard.rowIndex
                    text: caseCard.armed === "NONE" ? "تأكيد: بلا مصدر" : "بلا مصدر"
                    accent: caseCard.armed === "NONE" ? theme.red : theme.textSecondary
                    enabled: !caseCard.busy && caseReason.text.trim().length > 0
                    onClicked: {
                        if (caseCard.armed === "NONE") {
                            caseCard.armed = ""
                            reviewsVM.closeCaseNoSource(caseCard.review.caseId, caseReason.text.trim())
                        } else {
                            caseCard.armed = "NONE"
                            disarm.restart()
                        }
                    }
                }
            }
        }
    }

    // A pricing / settlement / discount review laid out for a quick decision: what happened on the invoice,
    // each category with its rate and a coloured range badge, and «تمت المراجعة» to take it off the list.
    component ReviewCard: Rectangle {
        id: reviewCard
        property var review: ({})
        property int rowIndex: 0
        readonly property var groups: review.groups || []
        readonly property bool outside: groups.some(function (g) { return g.status === "OUT_OF_RANGE" })
        readonly property color tone: outside ? theme.red : theme.amber
        Layout.fillWidth: true
        implicitHeight: cardBody.implicitHeight + 20
        color: cardArea.containsMouse ? theme.panelHover : theme.bgSecondary
        border.color: theme.border
        radius: 6
        SourceArea { id: cardArea; objectName: "reviewSource_" + reviewCard.review.invoiceId; invoiceId: reviewCard.review.invoiceId || "" }
        Rectangle {                                   // colour stripe on the physical right edge
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 4
            radius: 2
            color: reviewCard.tone
        }
        ColumnLayout {
            id: cardBody
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 10
            anchors.rightMargin: 16
            spacing: 6
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 10
                Label { text: reviewCard.review.title || ""; color: theme.textPrimary; font.family: "Cairo"; font.pixelSize: 14; font.bold: true }
                Rectangle {
                    visible: (reviewCard.review.invoice || "").length > 0
                    implicitWidth: invoiceLabel.implicitWidth + 16
                    implicitHeight: 22
                    radius: 11
                    color: "transparent"
                    border.color: theme.gold
                    Label { id: invoiceLabel; anchors.centerIn: parent; text: "فاتورة " + (reviewCard.review.invoice || ""); color: theme.gold; font.family: "Cairo"; font.pixelSize: 12 }
                }
                Item { Layout.fillWidth: true }
                Label { text: (reviewCard.review.time || "") + (reviewCard.review.by ? " • " + reviewCard.review.by : ""); color: theme.textMuted; font.family: "Cairo"; font.pixelSize: 11 }
            }
            Flow {
                Layout.fillWidth: true
                visible: reviewCard.review.price !== undefined && (reviewCard.review.price || "").length > 0
                layoutDirection: Qt.RightToLeft
                spacing: 16
                Label { text: "سعر البيع الفعلي  " + reviewCard.review.price; color: theme.textPrimary; font.family: "Cairo"; font.pixelSize: 13; font.bold: true }
                Label { visible: (reviewCard.review.salesperson || "").length > 0; text: "البائع: " + reviewCard.review.salesperson; color: theme.textSecondary; font.family: "Cairo"; font.pixelSize: 12 }
            }
            Repeater {
                model: reviewCard.groups
                RowLayout {
                    Layout.fillWidth: true
                    layoutDirection: Qt.RightToLeft
                    spacing: 10
                    Label { text: "فئة " + modelData.category; color: theme.textSecondary; font.family: "Cairo"; font.pixelSize: 12; Layout.preferredWidth: 80; horizontalAlignment: Text.AlignRight }
                    Label { text: modelData.average + " ج / جرام"; color: theme.textPrimary; font.family: "Cairo"; font.pixelSize: 13; font.bold: true }
                    Rectangle {
                        readonly property color badge: modelData.status === "OUT_OF_RANGE" ? theme.red
                                                      : modelData.status === "WITHIN_RANGE" ? theme.green : theme.textMuted
                        implicitWidth: badgeLabel.implicitWidth + 16
                        implicitHeight: 22
                        radius: 11
                        color: "transparent"
                        border.color: badge
                        Label { id: badgeLabel; anchors.centerIn: parent; text: modelData.statusText; color: parent.badge; font.family: "Cairo"; font.pixelSize: 11 }
                    }
                    Item { Layout.fillWidth: true }
                }
            }
            Label {
                Layout.fillWidth: true
                visible: reviewCard.groups.length === 0 && (reviewCard.review.details || "").length > 0
                text: reviewCard.review.details || ""
                color: theme.textSecondary
                font.family: "Cairo"
                font.pixelSize: 12
                horizontalAlignment: Text.AlignRight
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                Item { Layout.fillWidth: true }
                ActionButton {
                    objectName: "reviewDone_" + reviewCard.rowIndex
                    visible: reviewsVM.canResolveInspections
                    enabled: !reviewCard.review.busy && !reviewsVM.inspectionBusy
                    text: "✓  تمت المراجعة"
                    accent: theme.green
                    onClicked: reviewsVM.markReviewed(reviewCard.review.reviewId)
                }
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            Label {
                text: reviewPage.pageTitle
                color: theme.gold
                font.family: "Cairo"
                font.pixelSize: 18
                font.bold: true
            }
            Item { Layout.fillWidth: true }
            ActionButton {
                objectName: "reviewsOpenInventory"
                text: "📦  المخزون"
                accent: theme.gold
                onClicked: reviewPage.inventoryRequested()
            }
            ActionButton {
                objectName: "reviewsOpenLegacyVoid"
                visible: reviewPage.legacyVoidAvailable
                text: "🗑  إلغاء فاتورة قديمة"
                accent: theme.red
                onClicked: reviewPage.legacyVoidRequested()
            }
            ActionButton {
                objectName: "reviewsOpenScrapAdjust"
                visible: reviewPage.scrapAdjustAvailable
                text: "⚖  تعديل رصيد الكسر"
                accent: theme.amber
                onClicked: reviewPage.scrapAdjustRequested()
            }
            ActionButton {
                objectName: "reviewsOpenClasp"
                visible: reviewPage.claspAvailable
                text: "🧷  رصيد الرفايع"
                accent: theme.amber
                onClicked: reviewPage.claspRequested()
            }
            ActionButton {
                objectName: "reviewsOpenOpening"
                visible: reviewPage.openingAvailable
                text: "⚖  الرصيد الافتتاحي وجرد الذهب"
                accent: theme.gold
                onClicked: reviewPage.openingBalanceRequested()
            }
            ActionButton {
                objectName: "reviewsOpenReports"
                visible: reviewPage.reportsAvailable
                text: "📊  التقارير"
                accent: theme.gold
                onClicked: reviewPage.reportsRequested()
            }
        }

        // Summary / navigation cards: one per real review category (+ the financial accounts). A click
        // FILTERS the page to that category (click again for everything). Navigation only: pricing and
        // making reviews stay informational records — nothing here approves or rejects.
        Flow {
            objectName: "reviewCards"
            Layout.fillWidth: true
            Layout.preferredWidth: 1            // a Flow's implicit width is its unwrapped row: let it wrap instead
            visible: reviewsVM.canOpen && !reviewsVM.history.isOpen
            layoutDirection: Qt.RightToLeft
            spacing: 8
            Repeater {
                model: reviewsVM.dashboardCards
                Rectangle {
                    id: summaryCard
                    required property var modelData
                    required property int index
                    objectName: "reviewCard_" + modelData.code
                    width: 150
                    height: 50
                    radius: theme.radiusCard
                    color: cardArea.containsMouse ? theme.panelHover : theme.bgSecondary
                    border.width: modelData.selected ? 2 : 1
                    border.color: modelData.selected ? theme.goldBright : modelData.pending ? theme.goldDark : theme.cardBorder
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.rightMargin: 12
                        anchors.leftMargin: 8
                        anchors.topMargin: 3
                        anchors.bottomMargin: 3
                        spacing: 0
                        Label {
                            Layout.fillWidth: true
                            text: summaryCard.modelData.title
                            color: theme.textSecondary
                            font.family: "Cairo"
                            font.pixelSize: 12
                            horizontalAlignment: Text.AlignRight
                            elide: Text.ElideLeft
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            Label {
                                objectName: "reviewCardCount_" + summaryCard.modelData.code
                                text: summaryCard.modelData.code === "GOLD_POSITION" ? "21K" : summaryCard.modelData.count
                                color: summaryCard.modelData.pending ? theme.goldBright : theme.textPrimary
                                font.family: "Cairo"
                                font.pixelSize: 18
                                font.bold: true
                            }
                            Label {
                                Layout.fillWidth: true
                                text: summaryCard.modelData.code === "ACCOUNTS" ? "حساب"
                                      : summaryCard.modelData.code === "GOLD_POSITION" ? "للاطلاع"
                                      : summaryCard.modelData.pending ? "بانتظار المراجعة" : "✓"
                                color: summaryCard.modelData.pending ? theme.gold
                                       : summaryCard.modelData.code === "ACCOUNTS" || summaryCard.modelData.code === "GOLD_POSITION"
                                         ? theme.textMuted : theme.green
                                font.family: "Cairo"
                                font.pixelSize: 11
                                horizontalAlignment: Text.AlignRight
                                elide: Text.ElideLeft
                            }
                        }
                    }
                    MouseArea {
                        id: cardArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: reviewsVM.selectCategory(summaryCard.modelData.code)
                    }
                }
            }
        }

        Label {
            objectName: "reviewNoPermission"
            Layout.fillWidth: true
            visible: !reviewsVM.canOpen || reviewsVM.errorMessage.length > 0
            text: reviewsVM.canOpen ? reviewsVM.errorMessage
                                    : "تفاصيل المراجعات للمدير فقط. العدد ظاهر للجميع للتنبيه."
            color: theme.amber
            font.family: "Cairo"
            horizontalAlignment: Text.AlignRight
            wrapMode: Text.WordWrap
        }

        AccountHistoryPanel {
            history: reviewsVM.history
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: reviewsVM.canOpen && reviewsVM.history.isOpen
        }

        ScrollView {
            objectName: "reviewScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: reviewsVM.canOpen && !reviewsVM.history.isOpen
            clip: true
            contentWidth: availableWidth

            ColumnLayout {
                width: parent.width
                spacing: 12

                Label {
                    objectName: "noPendingReviews"
                    Layout.fillWidth: true
                    visible: reviewsVM.selectedCategory === "" && reviewsVM.totalCount === 0
                    text: "✓ لا توجد مراجعات معلّقة"
                    color: theme.green
                    font.family: "Cairo"
                    font.pixelSize: 13
                    horizontalAlignment: Text.AlignRight
                }

                // موضع الذهب: where ALL shop-owned gold is, in 21K equivalent. Read only; never netted against supplier balances.
                Section {
                    objectName: "goldPositionSection"
                    visible: reviewsVM.canViewGoldPosition && (reviewsVM.selectedCategory === "" || reviewsVM.selectedCategory === "GOLD_POSITION")
                    title: "موضع الذهب — مكافئ عيار 21"
                    showCount: false
                    Label {
                        objectName: "goldPositionError"
                        Layout.fillWidth: true
                        visible: reviewsVM.goldPositionError.length > 0
                        text: reviewsVM.goldPositionError
                        color: theme.red
                        font.family: "Cairo"
                        font.pixelSize: 12
                        horizontalAlignment: Text.AlignRight
                        wrapMode: Text.WordWrap
                    }
                    Label {
                        objectName: "goldUnknownNote"
                        Layout.fillWidth: true
                        visible: reviewPage.goldLoaded && reviewPage.goldPos.hasUnknown
                        text: reviewPage.goldLoaded ? "عيار غير معلوم: " + reviewPage.goldPos.unknown
                                                   + " جرام فعلي — غير داخل في الإجمالي (لا يُستنتج العيار)." : ""
                        color: theme.amber
                        font.family: "Cairo"
                        font.pixelSize: 12
                        horizontalAlignment: Text.AlignRight
                        wrapMode: Text.WordWrap
                    }
                    Repeater {
                        model: reviewPage.goldLoaded ? reviewPage.goldPos.warnings : []
                        Label {
                            required property var modelData
                            objectName: "goldWarning"
                            Layout.fillWidth: true
                            text: "⚠ " + modelData
                            color: theme.red
                            font.family: "Cairo"
                            font.pixelSize: 12
                            horizontalAlignment: Text.AlignRight
                            wrapMode: Text.WordWrap
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        ActionButton {
                            objectName: "goldDetailsToggle"
                            text: reviewPage.goldDetailsOpen ? "إخفاء التفاصيل" : "عرض التفاصيل"
                            accent: theme.gold
                            onClicked: reviewPage.goldDetailsOpen = !reviewPage.goldDetailsOpen
                        }
                        ActionButton {
                            objectName: "goldRefreshButton"
                            text: "تحديث"
                            accent: theme.textSecondary
                            enabled: !reviewsVM.goldPositionBusy
                            onClicked: reviewsVM.loadGoldPosition()
                        }
                        Label {
                            Layout.fillWidth: true
                            text: "للاطلاع فقط — لا يشمل أرصدة الموردين ولا يُخصم منها."
                            color: theme.textMuted
                            font.family: "Cairo"
                            font.pixelSize: 11
                            horizontalAlignment: Text.AlignRight
                        }
                    }
                    ColumnLayout {
                        id: goldDetails
                        objectName: "goldDetailsPanel"
                        Layout.fillWidth: true
                        visible: reviewPage.goldDetailsOpen && reviewPage.goldLoaded
                        spacing: 4
                        Repeater {
                            model: goldDetails.visible ? [
                                {"title": "في المحل", "rows": reviewPage.goldPos.shop},
                                {"title": "في عهدة السوق", "rows": reviewPage.goldPos.custody},
                                {"title": "لدى المسوقجي (ملك المحل)", "rows": reviewPage.goldPos.intermediary}] : []
                            ColumnLayout {
                                id: goldGroup
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 2
                                Label { Layout.fillWidth: true; text: goldGroup.modelData.title; color: theme.gold; font.family: "Cairo"
                                        font.pixelSize: 13; font.bold: true; horizontalAlignment: Text.AlignRight }
                                Repeater {
                                    model: goldGroup.modelData.rows
                                    Label {
                                        required property var modelData
                                        objectName: "goldDetailRow"
                                        Layout.fillWidth: true
                                        text: modelData.label + (modelData.text ? "  —  " + modelData.text : "")
                                              + (modelData.grams ? "  —  " + modelData.grams + " جرام" : "")
                                              + "  —  21K: " + modelData.eq21
                                        color: theme.textSecondary
                                        font.family: "Cairo"
                                        font.pixelSize: 12
                                        horizontalAlignment: Text.AlignRight
                                        wrapMode: Text.WordWrap
                                    }
                                }
                                Label { Layout.fillWidth: true; visible: goldGroup.modelData.rows.length === 0; text: "لا يوجد"
                                        color: theme.textMuted; font.family: "Cairo"; font.pixelSize: 11
                                        horizontalAlignment: Text.AlignRight }
                            }
                        }
                    }
                }

                // Wallet / InstaPay / bank balances live here, never in the MainWindow strip.
                Section {
                    objectName: "financialAccountsSection"
                    visible: (reviewsVM.selectedCategory === "" || reviewsVM.selectedCategory === "ACCOUNTS")
                    title: "الحسابات المالية"
                    count: reviewsVM.financialAccounts.length
                    Repeater {
                        model: reviewsVM.financialAccounts
                        AccountRow {
                            objectName: "financialAccount_" + index
                            account: modelData
                            rowIndex: index
                        }
                    }
                    // تحويل خاص: the owner's private account, another bank / wallet account or the main safe (the service re-checks every right).
                    ActionButton {
                        objectName: "privateTransferToggle"
                        visible: reviewsVM.canTransfer
                        text: reviewPage.transferOpen ? "إخفاء التحويل" : "⇄ تحويل خاص"
                        accent: theme.gold
                        onClicked: { reviewPage.transferOpen = !reviewPage.transferOpen; if (reviewPage.transferOpen) { reviewsVM.loadPrivateAccount(); reviewsVM.loadMarketers() } }
                    }
                    Rectangle {
                        id: transferForm
                        objectName: "transferForm"
                        visible: reviewsVM.canTransfer && reviewPage.transferOpen
                        Layout.fillWidth: true
                        implicitHeight: transferColumn.implicitHeight + 16
                        color: theme.bgSecondary
                        radius: 4
                        border.color: theme.border
                        property string requestKey: reviewsVM.newRequestKey()
                        property bool pending: false
                        readonly property var banks: reviewsVM.transferAccounts.filter(function (a) { return a.bank })
                        readonly property var safeAccount: reviewsVM.transferAccounts.filter(function (a) { return !a.bank })[0]
                        readonly property var modes: reviewsVM.canUsePrivate
                            ? [["TO", "إلى الحساب الخاص"], ["FROM", "من الحساب الخاص"], ["BANK", "إلى بنك / محفظة أخرى"], ["SAFE", "إلى الخزنة الرئيسية"]].concat(reviewsVM.canTransferToMarketer ? [["MKT", "إلى مورد / مسوقجي (عهدة نقدية)"]] : [])
                            : [["BANK", "إلى بنك / محفظة أخرى"], ["SAFE", "إلى الخزنة الرئيسية"]].concat(reviewsVM.canTransferToMarketer ? [["MKT", "إلى مورد / مسوقجي (عهدة نقدية)"]] : [])
                        readonly property string mode: modeCombo.currentIndex >= 0 ? modes[modeCombo.currentIndex][0] : ""
                        Connections {
                            target: reviewsVM
                            function onChanged() {
                                if (transferForm.pending && !reviewsVM.moneyBusy) {
                                    transferForm.pending = false
                                    if (!reviewsVM.accountMessageIsError && reviewsVM.accountMessage.length > 0) {
                                        transferForm.requestKey = reviewsVM.newRequestKey()
                                        transferAmount.text = ""
                                        transferNote.text = ""
                                    }
                                }
                            }
                        }
                        ColumnLayout {
                            id: transferColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 8
                            spacing: 6
                            RowLayout {
                                Layout.fillWidth: true
                                layoutDirection: Qt.RightToLeft
                                spacing: 6
                                Label { text: "نوع التحويل"; color: theme.gold; font.family: "Cairo"; font.pixelSize: 12; font.bold: true }
                                ComboBox {
                                    id: modeCombo
                                    objectName: "transferMode"
                                    Layout.preferredWidth: 190
                                    model: transferForm.modes.map(function (m) { return m[1] })
                                    currentIndex: 0
                                    font.family: "Cairo"
                                }
                                Label { text: transferForm.mode === "FROM" ? "إلى حساب" : "من حساب"; color: theme.textSecondary; font.family: "Cairo"; font.pixelSize: 12 }
                                ComboBox {
                                    id: transferFrom
                                    objectName: "transferFrom"
                                    Layout.preferredWidth: 200
                                    model: transferForm.banks.map(function (a) { return a.name })
                                    currentIndex: model.length > 0 ? 0 : -1
                                    onCountChanged: if (currentIndex < 0 && count > 0) currentIndex = 0
                                    font.family: "Cairo"
                                }
                                Label { visible: transferForm.mode === "BANK"; text: "إلى"; color: theme.textSecondary; font.family: "Cairo"; font.pixelSize: 12 }
                                ComboBox {
                                    id: transferTo
                                    objectName: "transferTo"
                                    visible: transferForm.mode === "BANK"
                                    Layout.preferredWidth: 200
                                    model: transferForm.banks.map(function (a) { return a.name })
                                    currentIndex: model.length > 1 ? 1 : -1
                                    onCountChanged: if (currentIndex < 0 && count > 1) currentIndex = 1
                                    font.family: "Cairo"
                                }
                                Label { visible: transferForm.mode === "MKT"; text: "المورد / المسوقجي"; color: theme.textSecondary; font.family: "Cairo"; font.pixelSize: 12 }
                                ComboBox {
                                    id: transferMarketer
                                    objectName: "transferMarketer"
                                    visible: transferForm.mode === "MKT"
                                    Layout.preferredWidth: 200
                                    model: reviewsVM.marketers
                                    textRole: "label"
                                    currentIndex: count > 0 ? 0 : -1
                                    onCountChanged: if (currentIndex < 0 && count > 0) currentIndex = 0
                                    font.family: "Cairo"
                                }
                                Item { Layout.fillWidth: true }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                layoutDirection: Qt.RightToLeft
                                spacing: 6
                                TextField {
                                    id: transferAmount
                                    objectName: "transferAmount"
                                    Layout.preferredWidth: 130
                                    font.family: "Cairo"
                                    font.pixelSize: 12
                                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                                    horizontalAlignment: TextInput.AlignRight
                                    placeholderText: text.length > 0 ? "" : "المبلغ"
                                }
                                TextField {
                                    id: transferNote
                                    objectName: "transferNote"
                                    Layout.fillWidth: true
                                    font.family: "Cairo"
                                    font.pixelSize: 12
                                    horizontalAlignment: TextInput.AlignRight
                                    placeholderText: text.length > 0 ? "" : "ملاحظة (اختياري)"
                                }
                                ActionButton {
                                    objectName: "transferConfirm"
                                    text: "تنفيذ التحويل"
                                    accent: theme.green
                                    enabled: !reviewsVM.moneyBusy && transferFrom.currentIndex >= 0 && transferAmount.text.trim().length > 0
                                             && (transferForm.mode !== "BANK" || (transferTo.currentIndex >= 0 && transferTo.currentIndex !== transferFrom.currentIndex))
                                             && (transferForm.mode !== "SAFE" || !!transferForm.safeAccount)
                                             && (transferForm.mode !== "MKT" || transferMarketer.currentIndex >= 0)
                                    onClicked: {
                                        var from = transferForm.banks[transferFrom.currentIndex].key
                                        transferForm.pending = true
                                        if (transferForm.mode === "MKT") {
                                            reviewsVM.transferToMarketer(from, reviewsVM.marketers[transferMarketer.currentIndex].id, transferAmount.text.trim(), transferNote.text.trim(), transferForm.requestKey)
                                        } else if (transferForm.mode === "TO" || transferForm.mode === "FROM") {
                                            reviewsVM.privateTransfer(from, transferForm.mode, transferAmount.text.trim(), transferNote.text.trim(), transferForm.requestKey)
                                        } else {
                                            var to = transferForm.mode === "SAFE" ? transferForm.safeAccount.key : transferForm.banks[transferTo.currentIndex].key
                                            reviewsVM.transferBetween(from, to, transferAmount.text.trim(), transferNote.text.trim(), transferForm.requestKey)
                                        }
                                    }
                                }
                            }
                            Label {
                                objectName: "transferMessage"
                                Layout.fillWidth: true
                                visible: text.length > 0
                                text: reviewsVM.accountMessage
                                color: reviewsVM.accountMessageIsError ? theme.red : theme.green
                                font.family: "Cairo"
                                font.pixelSize: 12
                                horizontalAlignment: Text.AlignRight
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                    // الحساب الخاص: balance and every entry (owner only; the service refuses anyone else).
                    Rectangle {
                        objectName: "privateAccountPanel"
                        visible: reviewsVM.canUsePrivate && reviewPage.transferOpen
                        Layout.fillWidth: true
                        implicitHeight: privateColumn.implicitHeight + 16
                        color: theme.bgSecondary
                        radius: 4
                        border.color: theme.goldDark
                        ColumnLayout {
                            id: privateColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 8
                            spacing: 4
                            RowLayout {
                                Layout.fillWidth: true
                                layoutDirection: Qt.RightToLeft
                                spacing: 14
                                Label { text: "الحساب الخاص"; color: theme.gold; font.family: "Cairo"; font.pixelSize: 14; font.bold: true }
                                Label { objectName: "privateBalance"; text: "الرصيد: " + (reviewsVM.privateAccount.balance || "0.00 ج"); color: theme.textPrimary; font.family: "Cairo"; font.pixelSize: 13; font.bold: true }
                                Label { text: "حوّلت له: " + (reviewsVM.privateAccount.totalIn || "0.00 ج"); color: theme.green; font.family: "Cairo"; font.pixelSize: 12 }
                                Label { text: "رجع منه: " + (reviewsVM.privateAccount.totalOut || "0.00 ج"); color: theme.amber; font.family: "Cairo"; font.pixelSize: 12 }
                                Item { Layout.fillWidth: true }
                            }
                            Repeater {
                                model: reviewsVM.privateAccount.entries || []
                                RowLayout {
                                    required property var modelData
                                    required property int index
                                    objectName: "privateEntry_" + index
                                    Layout.fillWidth: true
                                    layoutDirection: Qt.RightToLeft
                                    spacing: 10
                                    opacity: modelData.voided ? 0.5 : 1
                                    Label { text: modelData.text + (modelData.voided ? " (معكوس)" : ""); color: modelData.toPrivate ? theme.green : theme.amber; font.family: "Cairo"; font.pixelSize: 12; Layout.preferredWidth: 130; horizontalAlignment: Text.AlignRight }
                                    Label { text: modelData.account; color: theme.textSecondary; font.family: "Cairo"; font.pixelSize: 12; Layout.preferredWidth: 120; horizontalAlignment: Text.AlignRight }
                                    Label { text: modelData.amount; color: theme.textPrimary; font.family: "Cairo"; font.pixelSize: 12; font.bold: true; Layout.preferredWidth: 110; horizontalAlignment: Text.AlignRight }
                                    Label { text: modelData.date; color: theme.textMuted; font.family: "Cairo"; font.pixelSize: 11 }
                                    Label { text: modelData.note; color: theme.textMuted; font.family: "Cairo"; font.pixelSize: 11; elide: Text.ElideLeft; Layout.fillWidth: true; horizontalAlignment: Text.AlignRight }
                                    Label { text: "الرصيد " + modelData.balance; color: theme.textSecondary; font.family: "Cairo"; font.pixelSize: 11 }
                                }
                            }
                            Label {
                                visible: (reviewsVM.privateAccount.entries || []).length === 0
                                text: "لا توجد تحويلات على الحساب الخاص بعد."
                                color: theme.textMuted
                                font.family: "Cairo"
                                font.pixelSize: 12
                            }
                        }
                    }
                    // New real bank / wallet account (BANK_MANAGE; the service re-checks and audits).
                    ActionButton {
                        objectName: "newAccountToggle"
                        visible: reviewsVM.canSetupAccounts
                        text: reviewPage.newAccountOpen ? "إخفاء إضافة حساب" : "＋ حساب جديد"
                        accent: theme.textSecondary
                        onClicked: reviewPage.newAccountOpen = !reviewPage.newAccountOpen
                    }
                    Rectangle {
                        objectName: "newAccountForm"
                        visible: reviewsVM.canSetupAccounts && reviewPage.newAccountOpen
                        Layout.fillWidth: true
                        implicitHeight: newAccountRow.implicitHeight + 12
                        color: theme.bgSecondary
                        radius: 4
                        border.color: theme.border
                        RowLayout {
                            id: newAccountRow
                            anchors.fill: parent
                            anchors.margins: 6
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            Label { text: "حساب جديد"; color: theme.gold; font.family: "Cairo"; font.pixelSize: 12; font.bold: true }
                            TextField {
                                id: newAccountName
                                objectName: "newAccountName"
                                Layout.preferredWidth: 150
                                font.family: "Cairo"
                                font.pixelSize: 12
                                horizontalAlignment: TextInput.AlignRight
                                placeholderText: text.length > 0 ? "" : "اسم الحساب"
                            }
                            TextField {
                                id: newAccountBank
                                objectName: "newAccountBank"
                                Layout.preferredWidth: 110
                                font.family: "Cairo"
                                font.pixelSize: 12
                                horizontalAlignment: TextInput.AlignRight
                                placeholderText: text.length > 0 ? "" : "البنك / الشبكة"
                            }
                            Repeater {
                                model: [["BANK", "بنك / InstaPay"], ["MOBILE_WALLET", "محفظة"]]
                                Button {
                                    required property var modelData
                                    objectName: "newAccountType_" + modelData[0]
                                    text: modelData[1]
                                    font.family: "Cairo"
                                    font.pixelSize: 11
                                    highlighted: reviewPage.newAccountType === modelData[0]
                                    onClicked: reviewPage.newAccountType = modelData[0]
                                }
                            }
                            TextField {
                                id: newAccountOpening
                                objectName: "newAccountOpening"
                                Layout.preferredWidth: 110
                                font.family: "Cairo"
                                font.pixelSize: 12
                                horizontalAlignment: TextInput.AlignRight
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                placeholderText: text.length > 0 ? "" : "الرصيد الافتتاحي"
                            }
                            Button {
                                objectName: "createAccount"
                                text: "إضافة الحساب"
                                font.family: "Cairo"
                                font.bold: true
                                enabled: !reviewsVM.accountBusy && newAccountName.text.trim().length > 0
                                         && reviewPage.newAccountType.length > 0
                                onClicked: {
                                    reviewsVM.createAccount(newAccountName.text, reviewPage.newAccountType,
                                                            newAccountOpening.text, newAccountBank.text)
                                    newAccountName.text = ""
                                    newAccountBank.text = ""
                                    newAccountOpening.text = ""
                                    reviewPage.newAccountType = ""
                                }
                            }
                            Item { Layout.fillWidth: true }
                        }
                    }
                    Label {
                        objectName: "accountMessage"
                        Layout.fillWidth: true
                        visible: text.length > 0
                        text: reviewsVM.accountMessage
                        color: reviewsVM.accountMessageIsError ? theme.red : theme.green
                        font.family: "Cairo"
                        font.pixelSize: 12
                        horizontalAlignment: Text.AlignRight
                        wrapMode: Text.WordWrap
                    }
                }
                // موافقة مرتجع: a Return after 72 hours (still within day 15). The approval binds to the
                // exact selected goods / grams; no reason is required. It never extends the deadline.
                Section {
                    objectName: "reviewReturnApprovalSection"
                    visible: (reviewsVM.selectedCategory === "" && count > 0) || reviewsVM.selectedCategory === "RETURN_APPROVAL"
                    title: "موافقة مرتجع بعد 72 ساعة"
                    count: reviewsVM.returnApprovals.length
                    Repeater {
                        model: reviewsVM.returnApprovals
                        Rectangle {
                            id: approvalCard
                            required property var modelData
                            required property int index
                            objectName: "returnApproval_" + index
                            Layout.fillWidth: true
                            implicitHeight: approvalColumn.implicitHeight + 12
                            color: approvalSource.containsMouse ? theme.panelHover : theme.bgSecondary
                            radius: 4
                            SourceArea { id: approvalSource; objectName: "approvalSource_" + approvalCard.index
                                         invoiceId: approvalCard.modelData.invoiceId || "" }
                            ColumnLayout {
                                id: approvalColumn
                                anchors.fill: parent
                                anchors.leftMargin: 10
                                anchors.rightMargin: 12
                                anchors.topMargin: 6
                                anchors.bottomMargin: 6
                                spacing: 2
                                RowLayout {
                                    Layout.fillWidth: true
                                    layoutDirection: Qt.RightToLeft
                                    spacing: 10
                                    Label { text: "مرتجع " + approvalCard.modelData.code; color: theme.gold; font.family: "Cairo"
                                            font.pixelSize: 13; font.bold: true }
                                    Label { text: "فاتورة " + approvalCard.modelData.invoice + "  (" + approvalCard.modelData.invoiceDate + ")"
                                            color: theme.textPrimary; font.family: "Cairo"; font.pixelSize: 12 }
                                    Label { text: approvalCard.modelData.customer; color: theme.textSecondary; font.family: "Cairo"
                                            font.pixelSize: 12 }
                                    Item { Layout.fillWidth: true }
                                    Label { text: approvalCard.modelData.age + "  •  " + approvalCard.modelData.deadline
                                            color: theme.amber; font.family: "Cairo"; font.pixelSize: 11 }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: approvalCard.modelData.lines
                                    color: theme.textSecondary
                                    font.family: "Cairo"
                                    font.pixelSize: 12
                                    horizontalAlignment: Text.AlignRight
                                    wrapMode: Text.WordWrap
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    layoutDirection: Qt.RightToLeft
                                    spacing: 10
                                    Label { text: approvalCard.modelData.estimate; color: theme.priceBuy; font.family: "Cairo"
                                            font.pixelSize: 12; font.bold: true }
                                    Label { text: approvalCard.modelData.making; color: theme.textMuted; font.family: "Cairo"
                                            font.pixelSize: 12 }
                                    Label { visible: text.length > 0; text: approvalCard.modelData.notes ? "ملاحظة: " + approvalCard.modelData.notes : ""
                                            color: theme.textMuted; font.family: "Cairo"; font.pixelSize: 11 }
                                    Item { Layout.fillWidth: true }
                                    ActionButton {
                                        objectName: "returnApprove_" + approvalCard.index
                                        visible: reviewsVM.canResolveInspections
                                        enabled: !reviewsVM.inspectionBusy
                                        text: "موافقة"
                                        accent: theme.priceBuy
                                        onClicked: reviewsVM.approveReturn(approvalCard.modelData.reviewId)
                                    }
                                    ActionButton {
                                        objectName: "returnReject_" + approvalCard.index
                                        visible: reviewsVM.canResolveInspections
                                        enabled: !reviewsVM.inspectionBusy
                                        text: "رفض"
                                        accent: theme.red
                                        onClicked: reviewsVM.rejectReturn(approvalCard.modelData.reviewId)
                                    }
                                }
                            }
                        }
                    }
                }
                // قطع الاستبدال داخل الكسر (032): the returned piece is already in Scrap; the manager returns it
                // to stock or commits it there.
                Section {
                    objectName: "reviewScrapSection"
                    title: "قطع مرتجعة (استبدال / مرتجع) — محوّلة إلى الكسر"
                    count: reviewsVM.scrapPieces.length
                    visible: count > 0 && (reviewsVM.selectedCategory === "" || reviewsVM.selectedCategory === "RETURNED_ITEM_INSPECTION")
                    Repeater {
                        model: reviewsVM.scrapPieces
                        ScrapPieceCard {
                            objectName: "scrapPiece_" + index
                            review: modelData
                            rowIndex: index
                        }
                    }
                }
                // فحص القطع المرتجعة: one card per returned piece; the manager decides (no scan needed:
                // the piece is identified by its exchange line and inventory item, shown by its stored barcode).
                Section {
                    objectName: "reviewInspectionSection"
                    visible: (reviewsVM.selectedCategory === "" && count > 0) || reviewsVM.selectedCategory === "RETURNED_ITEM_INSPECTION"
                    title: "فحص قطع مرتجعة"
                    count: reviewsVM.inspections.length
                    Label {
                        objectName: "inspectionMessage"
                        Layout.fillWidth: true
                        visible: text.length > 0
                        text: reviewsVM.inspectionMessage
                        color: reviewsVM.inspectionMessageIsError ? theme.red : theme.green
                        font.family: "Cairo"
                        font.pixelSize: 12
                        horizontalAlignment: Text.AlignRight
                        wrapMode: Text.WordWrap
                    }
                    Repeater {
                        model: reviewsVM.inspections
                        InspectionCard {
                            objectName: "inspection_" + index
                            review: modelData
                            rowIndex: index
                        }
                    }
                }
                Section {
                    objectName: "reviewInventorySection"
                    visible: (reviewsVM.selectedCategory === "" && count > 0) || reviewsVM.selectedCategory === "INVENTORY_MATCH"
                    title: "مراجعة مخزون — مبيعات يدوية"
                    count: reviewsVM.inventoryCases.length
                    Label {
                        objectName: "caseMessage"
                        Layout.fillWidth: true
                        visible: text.length > 0
                        text: reviewsVM.inspectionMessage
                        color: reviewsVM.inspectionMessageIsError ? theme.red : theme.green
                        font.family: "Cairo"
                        font.pixelSize: 12
                        horizontalAlignment: Text.AlignRight
                        wrapMode: Text.WordWrap
                    }
                    Repeater {
                        model: reviewsVM.inventoryCases
                        CaseCard { review: modelData; rowIndex: index }
                    }
                }
                Section {
                    objectName: "reviewMakingSection"
                    visible: (reviewsVM.selectedCategory === "" && count > 0) || reviewsVM.selectedCategory === "MAKING_PRICE"
                    title: "مراجعة تسعير / مصنعية"
                    count: reviewsVM.makingReviews.length
                    Repeater {
                        model: reviewsVM.makingReviews
                        ReviewCard { review: modelData; rowIndex: index }
                    }
                }
                Section {
                    objectName: "reviewSettlementSection"
                    visible: (reviewsVM.selectedCategory === "" && count > 0) || reviewsVM.selectedCategory === "CASH_SETTLEMENT"
                    title: "مراجعة تسوية نقدية"
                    count: reviewsVM.settlementReviews.length
                    Repeater {
                        model: reviewsVM.settlementReviews
                        ReviewCard { review: modelData; rowIndex: index }
                    }
                }
                Section {
                    objectName: "reviewDiscountSection"
                    visible: (reviewsVM.selectedCategory === "" && count > 0) || reviewsVM.selectedCategory === "OLD_GOLD_DISCOUNT"
                    title: "تعديل نسبة خصم الكسر"
                    count: reviewsVM.discountReviews.length
                    Repeater {
                        model: reviewsVM.discountReviews
                        ReviewCard { review: modelData; rowIndex: index }
                    }
                }
            }
        }
    }
}
