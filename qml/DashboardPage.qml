import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// الرئيسية: what happened today, at a glance. Read-only: every figure comes formatted from
// DashboardViewModel (today's finalized invoices, COUNTED old-gold ledger) and GoldPriceViewModel.
// No SQL, no calculation here.
Item {
    id: dashboard
    objectName: "dashboardPage"

    property string pageTitle: ""
    signal navigateTo(int page)

    readonly property var vm: typeof dashboardVM !== "undefined" ? dashboardVM : null
    readonly property var price: typeof goldPriceVM !== "undefined" ? goldPriceVM : null
    readonly property bool sales: !!vm && vm.canSeeSales
    readonly property bool wide: width >= 1000
    readonly property int boost: 2                  // owner review (2026-10-22): the dashboard reads from a distance, every themed size is two points bigger

    Theme { id: theme }

    onVisibleChanged: if (visible && vm) vm.refresh()

    Rectangle { anchors.fill: parent; color: theme.bgPrimary }

    // ------------------------------------------------------------ building blocks
    component Txt: Label {
        property string value: ""
        text: value
        font.family: theme.fontFamily
        font.pixelSize: (theme.fontBody + dashboard.boost)
        color: theme.textSecondary
        horizontalAlignment: Text.AlignRight
        elide: Text.ElideLeft
        wrapMode: Text.NoWrap
        textFormat: Text.PlainText
    }

    // A separated, rounded card (owner review 2026-10-22: "separated, squarish, curved at the sides"): a generous radius, a soft offset shadow behind it and its accent colour all round the edge. It is an Item so the shadow can sit outside the face without changing the card's size in the layout.
    component Card: Item {
        id: card
        property string title: ""
        property color accent: theme.gold
        property string badge: ""
        default property alias content: body.data
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.preferredHeight: body.implicitHeight + 70
        Rectangle {                                                    // the soft shadow: the face shifted down, dark and translucent
            anchors.fill: face; anchors.topMargin: 5; anchors.bottomMargin: -5; anchors.leftMargin: 2; anchors.rightMargin: 2
            radius: face.radius; color: "#000000"; opacity: 0.4
        }
        Rectangle {
            id: face
            anchors.fill: parent; anchors.bottomMargin: 5
            color: theme.panel
            radius: theme.radiusCard
            border.color: Qt.alpha(card.accent, 0.85)                       // the card's colour runs all the way round its edge (owner review), a little softened
            border.width: 2
            RowLayout {
                id: head
                anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
                anchors.topMargin: 14; anchors.leftMargin: 18; anchors.rightMargin: 20
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Txt { value: card.title; color: card.accent; font.bold: true; font.pixelSize: theme.fontSection + dashboard.boost }
                Item { Layout.fillWidth: true }
                Txt { value: card.badge; visible: value.length > 0; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
            }
            ColumnLayout {
                id: body
                anchors.top: head.bottom; anchors.left: parent.left; anchors.right: parent.right
                anchors.topMargin: 10; anchors.leftMargin: 18; anchors.rightMargin: 20
                spacing: 4
            }
        }
    }

    // One "label ...... value" line, label on the physical right.
    component Line: RowLayout {
        property string label: ""
        property string value: ""
        property color valueColor: theme.textPrimary
        property bool strong: false
        Layout.fillWidth: true
        layoutDirection: Qt.RightToLeft
        spacing: 8
        Txt { value: parent.label; color: theme.textMuted; font.pixelSize: (theme.fontTableHeader + dashboard.boost) }
        Item { Layout.fillWidth: true }
        Txt { value: parent.value; color: parent.valueColor; font.bold: parent.strong
              font.pixelSize: parent.strong ? (theme.fontBodyStrong + dashboard.boost) : (theme.fontBody + dashboard.boost) }
    }

    component Empty: Txt {
        Layout.fillWidth: true
        color: theme.textMuted
        font.pixelSize: (theme.fontTableHeader + dashboard.boost)
    }

    component Action: Button {
        id: action
        property int page: 0
        Layout.fillWidth: true
        implicitHeight: 38
        onClicked: dashboard.navigateTo(action.page)
        contentItem: Label {
            text: action.text
            font.family: theme.fontFamily; font.pixelSize: (theme.fontBody + dashboard.boost); font.bold: true
            color: action.enabled ? theme.textPrimary : theme.textDisabled
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: theme.radiusSmall
            color: action.hovered ? theme.panelHover : theme.bgTertiary
            border.color: action.activeFocus ? theme.gold : theme.border
        }
    }

    // ------------------------------------------------------------ page
    ScrollView {
        id: scroll
        anchors.fill: parent
        anchors.margins: 18
        clip: true
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            width: scroll.availableWidth
            spacing: 16

            RowLayout {
                Layout.fillWidth: true
                layoutDirection: Qt.RightToLeft
                spacing: 10
                Txt { value: "🏠 الرئيسية"; color: theme.textPrimary; font.bold: true; font.pixelSize: (theme.fontPageTitle + dashboard.boost) }
                Txt { objectName: "dashboardDate"; value: dashboard.vm ? dashboard.vm.dateText : ""
                      color: theme.textMuted; font.pixelSize: (theme.fontBody + dashboard.boost) }
                Item { Layout.fillWidth: true }
                Txt { objectName: "dashboardError"; value: dashboard.vm ? dashboard.vm.errorMessage : ""
                      visible: value.length > 0; color: theme.red; font.pixelSize: (theme.fontTableHeader + dashboard.boost) }
                Txt { value: dashboard.vm && dashboard.vm.busy ? "جاري التحديث..." : ""
                      color: theme.textMuted; font.pixelSize: (theme.fontCaption + dashboard.boost) }
            }

            GridLayout {
                id: grid
                Layout.fillWidth: true
                columns: dashboard.wide ? 3 : 2
                layoutDirection: Qt.RightToLeft
                columnSpacing: 16
                rowSpacing: 16

                // --- سعر الذهب (the engine's own formatted values)
                Card {
                    objectName: "priceCard"
                    title: "سعر الذهب • عيار 21"
                    accent: theme.priceSell
                    // Readable from across the shop: the two 21K numbers dominate the card; the labels,
                    // source and freshness stay small. Same order as the Main.qml price strip: shop BUY
                    // (شراء المحل) on the right, shop SELL (بيع المحل) on the left.
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Repeater {
                            model: [
                                { name: "dashboardPriceBuy", label: "شراء المحل", color: theme.priceBuy,
                                  value: dashboard.price && dashboard.price.headlineBuy ? dashboard.price.headlineBuy : "—" },
                                { name: "dashboardPriceSell", label: "بيع المحل", color: theme.priceSell,
                                  value: dashboard.price && dashboard.price.headlineSell ? dashboard.price.headlineSell : "—" }
                            ]
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                implicitHeight: zone.implicitHeight + 8
                                radius: theme.radiusSmall
                                color: theme.bgSecondary
                                ColumnLayout {
                                    id: zone
                                    anchors.centerIn: parent
                                    spacing: -4
                                    Label {
                                        objectName: modelData.name
                                        Layout.alignment: Qt.AlignHCenter
                                        text: modelData.value
                                        color: modelData.color
                                        font.family: theme.fontFamily
                                        font.pixelSize: theme.fontPriceHero
                                        font.bold: true
                                        textFormat: Text.PlainText
                                    }
                                    Label {
                                        Layout.alignment: Qt.AlignHCenter
                                        text: modelData.label
                                        color: theme.textMuted
                                        font.family: theme.fontFamily
                                        font.pixelSize: (theme.fontBody + dashboard.boost)
                                    }
                                }
                            }
                        }
                    }
                    Txt { objectName: "dashboardPriceStatus"; Layout.fillWidth: true
                          value: !dashboard.price ? "السعر غير متاح"
                                 : dashboard.price.simpleStatus || "السعر غير متاح"
                          color: !dashboard.price || dashboard.price.status === "ERROR" ? theme.priceError
                                 : dashboard.price.status === "STALE_ALLOWED" ? theme.priceWarning : theme.textMuted
                          font.pixelSize: (theme.fontTableHeader + dashboard.boost) }
                }

                // --- مؤشر سعر عيار 21 (every logged-in user): the shop's own stored prices, the same formulas and wording as the indicators report
                Card {
                    objectName: "priceHintCard"
                    title: "مؤشر سعر عيار 21"
                    accent: theme.gold
                    Line { objectName: "priceHintAverage"; label: "متوسط 30 يوم (شراء)"
                           value: dashboard.vm && dashboard.vm.priceHint.average ? dashboard.vm.priceHint.average : "—"; strong: true }
                    Line { objectName: "priceHintVersus"; label: "السعر الآن مقابل المتوسط"
                           value: dashboard.vm ? dashboard.vm.priceHint.versus : "" }
                    Line { objectName: "priceHintAction"; label: "الإرشاد"
                           value: dashboard.vm ? dashboard.vm.priceHint.action : ""; strong: true
                           valueColor: !dashboard.vm ? theme.textMuted
                                      : dashboard.vm.priceHint.tone === "buy" ? theme.priceBuy
                                      : dashboard.vm.priceHint.tone === "sell" ? theme.priceSell : theme.textMuted }
                    Txt { Layout.fillWidth: true; wrapMode: Text.WordWrap
                          value: "إرشاد مبني على حركة أسعار المحل المخزنة وليس توصية مالية"
                          color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
                }

                // --- شغل اليوم (billing permission)
                Card {
                    objectName: "todayCard"
                    visible: dashboard.sales
                    title: "شغل اليوم"
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Txt { objectName: "invoiceCount"; value: dashboard.vm ? String(dashboard.vm.invoiceCount) : "0"
                              color: theme.textPrimary; font.bold: true; font.pixelSize: (theme.fontNumber + dashboard.boost) + 6 }
                        ColumnLayout {
                            spacing: 0
                            Txt { value: "فاتورة"; color: theme.textMuted; font.pixelSize: (theme.fontTableHeader + dashboard.boost) }
                            Txt { objectName: "invoiceBreakdown"; value: dashboard.vm ? dashboard.vm.invoiceBreakdown : ""
                                  color: theme.textSecondary; font.pixelSize: (theme.fontCaption + dashboard.boost) }
                        }
                        Item { Layout.fillWidth: true }
                    }
                    Line { objectName: "sellTotal"; label: "قيمة المبيعات"; strong: true; valueColor: theme.priceSell
                           value: dashboard.vm ? dashboard.vm.sellTotalText : "" }
                    Line { objectName: "buyTotal"; label: "قيمة شراء الكسر"; valueColor: theme.priceBuy
                           value: dashboard.vm ? dashboard.vm.buyTotalText : "" }
                    Txt { objectName: "unidentifiedToday"; Layout.fillWidth: true
                          visible: dashboard.vm && dashboard.vm.unidentifiedCount > 0
                          value: dashboard.vm ? "⚠ قطع بيع يدوية تحتاج مطابقة: " + dashboard.vm.unidentifiedCount : ""
                          color: theme.amber; font.pixelSize: (theme.fontTableHeader + dashboard.boost) }
                }

                // --- مبيعات اليوم بالوزن (billing permission)
                Card {
                    objectName: "soldWeightCard"
                    visible: dashboard.sales
                    title: "مبيعات اليوم بالوزن"
                    Repeater {
                        model: dashboard.vm ? dashboard.vm.soldByKarat : []
                        delegate: ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 0
                            Line { label: "عيار " + modelData.karat + "  •  " + modelData.pieces
                                   value: modelData.grams; strong: true
                                   valueColor: theme.balanceKaratColor(modelData.karat) }
                            Txt { Layout.fillWidth: true; visible: modelData.fromOldGold.length > 0
                                  value: modelData.fromOldGold; color: theme.textMuted; font.pixelSize: (theme.fontCaption + dashboard.boost) }
                        }
                    }
                    Empty { objectName: "soldWeightEmpty"; value: "لم يُبع ذهب اليوم بعد"
                            visible: !dashboard.vm || dashboard.vm.soldByKarat.length === 0 }
                }

                // --- الكسر (every logged-in user, same ledger as the top strip + today's flow)
                Card {
                    objectName: "oldGoldCard"
                    title: "الكسر (رصيد + حركة اليوم)"
                    accent: theme.balanceKarat21
                    Repeater {
                        model: dashboard.vm ? dashboard.vm.oldGold : []
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 8
                            Txt { value: "كسر " + modelData.karat; font.bold: true; font.pixelSize: (theme.fontBody + dashboard.boost)
                                  color: theme.balanceKaratColor(modelData.karat) }
                            Txt { value: modelData.balance; font.bold: true; font.pixelSize: (theme.fontBodyStrong + dashboard.boost)
                                  color: modelData.zero ? theme.textMuted : theme.textPrimary }
                            Item { Layout.fillWidth: true }
                            Txt { value: modelData.inText; visible: value.length > 0; color: theme.green
                                  font.pixelSize: (theme.fontTableHeader + dashboard.boost) }
                            Txt { value: modelData.outText; visible: value.length > 0; color: theme.amber
                                  font.pixelSize: (theme.fontTableHeader + dashboard.boost) }
                            Txt { value: modelData.todayText; visible: value.length > 0; color: theme.textMuted
                                  font.pixelSize: (theme.fontCaption + dashboard.boost) }
                        }
                    }
                    Empty { value: "جاري التحميل..."; visible: !dashboard.vm || !dashboard.vm.ready }
                }

                // --- مبيعات الشهر (billing permission): the month so far per karat against the same span of the same month LAST YEAR (owner request 2026-10-25)
                Card {
                    objectName: "monthCard"
                    visible: dashboard.sales
                    title: dashboard.vm ? dashboard.vm.monthTitle : "مبيعات الشهر"
                    accent: theme.priceSell
                    Repeater {
                        model: dashboard.vm ? dashboard.vm.monthByKarat : []
                        delegate: ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 0
                            Line { label: "عيار " + modelData.karat + "  •  " + modelData.pieces; value: modelData.grams; strong: true
                                   valueColor: theme.balanceKaratColor(modelData.karat) }
                            Txt { Layout.fillWidth: true; value: modelData.versus; font.pixelSize: theme.fontCaption + dashboard.boost
                                  color: modelData.direction === "up" ? theme.green : modelData.direction === "down" ? theme.amber : theme.textMuted }
                        }
                    }
                    Empty { objectName: "monthEmpty"; value: "لا مبيعات هذا الشهر بعد"; visible: !dashboard.vm || dashboard.vm.monthByKarat.length === 0 }
                }

                // --- أكثر الأصناف مبيعًا هذا الشهر (billing permission): one row per item type, never summed across types (owner request 2026-10-25)
                Card {
                    objectName: "topTypesCard"
                    visible: dashboard.sales
                    title: "أكثر الأصناف مبيعًا هذا الشهر"
                    accent: theme.priceSell
                    Repeater {
                        model: dashboard.vm ? dashboard.vm.topTypes : []
                        delegate: ColumnLayout {
                            required property var modelData
                            required property int index
                            objectName: "topType_" + index
                            Layout.fillWidth: true
                            spacing: 0
                            Line { label: modelData.type; value: modelData.value; strong: true }
                            Txt { Layout.fillWidth: true; value: modelData.pieces + " • " + modelData.grams
                                  color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
                        }
                    }
                    Empty { objectName: "topTypesEmpty"; value: "لا مبيعات هذا الشهر بعد"
                            visible: !dashboard.vm || dashboard.vm.topTypes.length === 0 }
                }

                // --- تحت الحد الأدنى للمخزون (inventory permission): the rules the owner set in Settings
                Card {
                    objectName: "belowMinCard"
                    visible: !!dashboard.vm && dashboard.vm.canSeeStock
                    title: "تحت الحد الأدنى للمخزون"
                    accent: !!dashboard.vm && dashboard.vm.belowMin.length > 0 ? theme.amber : theme.textMuted
                    badge: dashboard.vm && dashboard.vm.belowMin.length > 0 ? String(dashboard.vm.belowMin.length) : ""
                    Repeater {
                        model: dashboard.vm ? dashboard.vm.belowMin : []
                        delegate: Line { required property var modelData; label: modelData.title; value: modelData.short; valueColor: theme.amber; strong: true }
                    }
                    Empty { objectName: "belowMinEmpty"; value: "كل الأصناف فوق حدها الأدنى ✓ (أو لم تُحدَّد حدود في الإعدادات)"; visible: !dashboard.vm || dashboard.vm.belowMin.length === 0
                            wrapMode: Text.WordWrap }
                    Action { objectName: "dashboardOpenAlerts"; text: "فتح التقارير (تنبيهات المخزون)"; page: 5; visible: !!dashboard.vm && dashboard.vm.belowMin.length > 0 }
                }

                // --- أرصدة العملاء (customers permission): who owes and who is owed, from the customer ledger.
                // Only customers with account activity from the first day of the current month; a settled account disappears on its own.
                Card {
                    objectName: "customerBalancesCard"
                    visible: !!dashboard.vm && dashboard.vm.canSeeBalances
                    title: "أرصدة العملاء (من أول الشهر)"
                    accent: theme.gold
                    badge: dashboard.vm && dashboard.vm.customerBalances.length > 0 ? String(dashboard.vm.customerBalances.length) : ""
                    Repeater {
                        model: dashboard.vm ? dashboard.vm.customerBalances : []
                        delegate: ColumnLayout {
                            required property var modelData
                            required property int index
                            objectName: "customerBalance_" + index
                            Layout.fillWidth: true
                            spacing: 0
                            // Distinguished by COLOUR only (owner request 2026-10-25): RED = على العميل (he owes), BLUE = للعميل (we owe).
                            Line { label: modelData.name + (modelData.code ? "  (" + modelData.code + ")" : "")
                                   value: modelData.balance
                                   valueColor: modelData.owes ? theme.red : theme.blue
                                   strong: true }
                        }
                    }
                    Empty { objectName: "customerBalancesEmpty"; value: "لا يوجد عملاء برصيد غير متعادل عليهم أو علينا"
                            visible: !dashboard.vm || dashboard.vm.customerBalances.length === 0
                            wrapMode: Text.WordWrap }
                    Txt { Layout.fillWidth: true; wrapMode: Text.WordWrap
                          value: "العملاء الذين لديهم حركة حساب من أول الشهر الحالي فقط؛ عند تسوية الحساب يُشال الاسم تلقائيًا"
                          color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
                }

                // --- ديون متأخرة (customers permission): open owed items at least N days old, oldest-first reading of the customer ledger
                Card {
                    objectName: "overdueCard"
                    visible: !!dashboard.vm && dashboard.vm.canSeeBalances
                    title: "ديون متأخرة (أكثر من " + (dashboard.vm ? dashboard.vm.overdueDebts.days : 0) + " يوم)"
                    accent: !!dashboard.vm && dashboard.vm.overdueDebts.count > 0 ? theme.red : theme.textMuted
                    badge: dashboard.vm && dashboard.vm.overdueDebts.count > 0 ? String(dashboard.vm.overdueDebts.count) : ""
                    Line { objectName: "overdueTotal"; label: "إجمالي المتأخر"; value: dashboard.vm ? dashboard.vm.overdueDebts.total : ""; strong: true
                           valueColor: dashboard.vm && dashboard.vm.overdueDebts.count > 0 ? theme.red : theme.textMuted
                           visible: !!dashboard.vm && dashboard.vm.overdueDebts.count > 0 }
                    Repeater {
                        model: dashboard.vm ? dashboard.vm.overdueDebts.rows : []
                        delegate: ColumnLayout {
                            required property var modelData
                            required property int index
                            objectName: "overdueRow_" + index
                            Layout.fillWidth: true
                            spacing: 0
                            Line { label: modelData.name; value: modelData.amount; strong: true; valueColor: theme.red }
                            Txt { Layout.fillWidth: true; value: modelData.age; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
                        }
                    }
                    Empty { objectName: "overdueEmpty"; value: "لا توجد ديون متأخرة ✓"
                            visible: !dashboard.vm || dashboard.vm.overdueDebts.count === 0 }
                }

                // --- سعر شراء الكسر اليوم (billing permission): the average paid per gram today against the latest stored SHOP_BUY of that karat
                Card {
                    objectName: "scrapTodayCard"
                    visible: dashboard.sales
                    title: "سعر شراء الكسر اليوم"
                    accent: theme.priceBuy
                    Repeater {
                        model: dashboard.vm ? dashboard.vm.scrapToday : []
                        delegate: ColumnLayout {
                            required property var modelData
                            required property int index
                            objectName: "scrapToday_" + index
                            Layout.fillWidth: true
                            spacing: 0
                            Line { label: "عيار " + modelData.karat + "  •  " + modelData.grams; value: modelData.average; strong: true
                                   valueColor: theme.balanceKaratColor(modelData.karat) }
                            Txt { Layout.fillWidth: true; wrapMode: Text.WordWrap
                                  value: "سعر الشراء الآن " + modelData.price + (modelData.diff ? "  •  الفرق " + modelData.diff : "")
                                  color: modelData.over ? theme.amber : theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
                        }
                    }
                    Empty { objectName: "scrapTodayEmpty"; value: "لم يُشترَ كسر اليوم بعد"
                            visible: !dashboard.vm || dashboard.vm.scrapToday.length === 0 }
                }

                // --- قطع راكدة (inventory permission): IN_STOCK pieces never on an invoice for N days, per karat
                Card {
                    objectName: "deadStockCard"
                    visible: !!dashboard.vm && dashboard.vm.canSeeStock
                    title: "قطع راكدة (أكثر من " + (dashboard.vm ? dashboard.vm.deadStock.days : 0) + " يوم)"
                    accent: !!dashboard.vm && dashboard.vm.deadStock.rows.length > 0 ? theme.amber : theme.textMuted
                    Repeater {
                        model: dashboard.vm ? dashboard.vm.deadStock.rows : []
                        delegate: ColumnLayout {
                            required property var modelData
                            required property int index
                            objectName: "deadStock_" + index
                            Layout.fillWidth: true
                            spacing: 0
                            Line { label: "عيار " + modelData.karat + "  •  " + modelData.pieces; value: modelData.grams; strong: true
                                   valueColor: theme.balanceKaratColor(modelData.karat) }
                            Txt { Layout.fillWidth: true; value: modelData.oldest; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
                        }
                    }
                    Empty { objectName: "deadStockEmpty"; value: "لا توجد قطع راكدة ✓"
                            visible: !dashboard.vm || dashboard.vm.deadStock.rows.length === 0 }
                    Action { objectName: "dashboardOpenDeadStock"; text: "فتح التقارير (تنبيهات المخزون)"; page: 5
                             visible: !!dashboard.vm && dashboard.vm.deadStock.rows.length > 0 }
                }
            }


            Item { Layout.preferredHeight: 4 }
        }
    }
}
