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

    Theme { id: theme }

    onVisibleChanged: if (visible && reviewsVM.canOpen) reviewsVM.openReviews()

    Rectangle {
        anchors.fill: parent
        color: theme.bgPrimary
    }

    component Section: ColumnLayout {
        id: section
        property string title: ""
        property int count: 0
        Layout.fillWidth: true
        spacing: 4
        Label {
            Layout.fillWidth: true
            text: section.title + "  •  " + section.count
            color: section.count > 0 ? theme.amber : theme.textMuted
            font.family: "Cairo"
            font.pixelSize: 14
            font.bold: true
            horizontalAlignment: Text.AlignRight
        }
    }

    property string newAccountType: ""

    // One real account: balance, money in / out, latest movements, and (manager only)
    // activation plus a one-time opening balance while the account has never moved.
    component AccountRow: Rectangle {
        id: accountRow
        property var account: ({})
        property int rowIndex: 0
        Layout.fillWidth: true
        implicitHeight: accountColumn.implicitHeight + 10
        color: theme.bgSecondary
        radius: 4
        Rectangle { anchors.right: parent.right; width: 3; height: parent.height; color: theme.priceBuy; radius: 2 }
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
        color: theme.bgSecondary
        radius: 4
        Rectangle { anchors.right: parent.right; width: 3; height: parent.height; color: theme.amber; radius: 2 }
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
                Label { text: "استبدال " + (card.review.exchange || ""); color: theme.gold; font.family: "Cairo"
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
                text: confirmDialog.outcome === "RETURN_TO_STOCK" ? "إرجاع القطعة للمخزون" : "تحويل القطعة إلى الكسر"
                color: theme.gold
                font.family: "Cairo"
                font.pixelSize: 15
                font.bold: true
                horizontalAlignment: Text.AlignRight
            }
            Label {
                objectName: "inspectionConfirmText"
                Layout.fillWidth: true
                text: confirmDialog.outcome === "RETURN_TO_STOCK"
                      ? "ستعود القطعة للمخزون بنفس الباركود المسجل: " + (confirmDialog.review.barcode || "غير مسجل")
                      : "سيتم تحويل القطعة نهائياً إلى رصيد الكسر، ولن تعود قابلة للبيع بالباركود المسجل. هل تريد المتابعة؟"
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
                      + "  •  عيار " + (confirmDialog.review.karat || "") + "  •  الوزن " + (confirmDialog.review.weight || "") + " جرام"
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
        Layout.fillWidth: true
        implicitHeight: 52
        color: theme.bgSecondary
        radius: 4
        Rectangle { anchors.right: parent.right; width: 3; height: parent.height; color: theme.amber; radius: 2 }
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
            Repeater {
                model: reviewsVM.categories
                Rectangle {
                    Layout.preferredHeight: 28
                    Layout.preferredWidth: chipText.implicitWidth + 16
                    radius: 4
                    color: "transparent"
                    border.color: modelData.count > 0 ? theme.amber : theme.border
                    Label {
                        id: chipText
                        anchors.centerIn: parent
                        text: modelData.title + " • " + modelData.count
                        color: modelData.count > 0 ? theme.amber : theme.textMuted
                        font.family: "Cairo"
                        font.pixelSize: 11
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

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: reviewsVM.canOpen
            clip: true
            contentWidth: availableWidth

            ColumnLayout {
                width: parent.width
                spacing: 12

                // Wallet / InstaPay / bank balances live here, never in the MainWindow strip.
                Section {
                    objectName: "financialAccountsSection"
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
                    // New real bank / wallet account (BANK_MANAGE; the service re-checks and audits).
                    Rectangle {
                        objectName: "newAccountForm"
                        visible: reviewsVM.canSetupAccounts
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
                // فحص القطع المرتجعة: one card per returned piece; the manager decides (no scan needed:
                // the piece is identified by its exchange line and inventory item, shown by its stored barcode).
                Section {
                    objectName: "reviewInspectionSection"
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
                    title: "مراجعة مخزون — مبيعات يدوية"
                    count: reviewsVM.inventoryCases.length
                    Repeater {
                        model: reviewsVM.inventoryCases
                        Row2 {
                            title: modelData.title + " — " + modelData.reason
                            side: modelData.time + " • " + modelData.salesperson
                            line1: modelData.details
                            line2: modelData.customer + (modelData.notes ? " • " + modelData.notes : "")
                        }
                    }
                }
                Section {
                    objectName: "reviewMakingSection"
                    title: "مراجعة تسعير / مصنعية"
                    count: reviewsVM.makingReviews.length
                    Repeater {
                        model: reviewsVM.makingReviews
                        Row2 { title: modelData.title; side: modelData.time + " • " + modelData.by; line1: modelData.details }
                    }
                }
                Section {
                    objectName: "reviewSettlementSection"
                    title: "مراجعة تسوية نقدية"
                    count: reviewsVM.settlementReviews.length
                    Repeater {
                        model: reviewsVM.settlementReviews
                        Row2 { title: modelData.title; side: modelData.time + " • " + modelData.by; line1: modelData.details }
                    }
                }
                Section {
                    objectName: "reviewDiscountSection"
                    title: "تعديل نسبة خصم الكسر"
                    count: reviewsVM.discountReviews.length
                    Repeater {
                        model: reviewsVM.discountReviews
                        Row2 { title: modelData.title; side: modelData.time + " • " + modelData.by; line1: modelData.details }
                    }
                }
            }
        }
    }
}
