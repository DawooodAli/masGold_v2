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
    // Owner 2026-11-10: five older cards are hidden by default (gold price, price indicator, scrap balance, sold weight, scrap buying price). One switch brings them back; their data and tests are untouched.
    property bool showRetired: false
    readonly property bool wide: width >= 1000
    readonly property int boost: 1                  // owner review (2026-10-22): the dashboard reads from a distance, every themed size is two points bigger

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
        Layout.preferredHeight: body.implicitHeight + 56
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
                anchors.topMargin: 9; anchors.leftMargin: 16; anchors.rightMargin: 18
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Txt { value: card.title; color: card.accent; font.bold: true; font.pixelSize: theme.fontSection + dashboard.boost }
                Item { Layout.fillWidth: true }
                Txt { value: card.badge; visible: value.length > 0; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
            }
            ColumnLayout {
                id: body
                anchors.top: head.bottom; anchors.left: parent.left; anchors.right: parent.right
                anchors.topMargin: 5; anchors.leftMargin: 16; anchors.rightMargin: 18
                spacing: 2
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
        onClicked: if (action.page >= 0) dashboard.navigateTo(action.page)
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
                columnSpacing: 14
                rowSpacing: 10

                // --- سعر عيار 21 آخر 30 يومًا (the stored daily prices; shop BUY green, shop SELL amber) + a link to a live external chart
                Card {
                    id: priceChartCard
                    objectName: "priceChartCard"
                    title: "سعر عيار 21 — آخر 30 يومًا"
                    accent: theme.priceBuy
                    Canvas {
                        id: priceLine
                        objectName: "dashboardPriceCanvas"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 150
                        readonly property var chart: dashboard.vm ? dashboard.vm.priceChart : ({})
                        onChartChanged: requestPaint()
                        onWidthChanged: requestPaint()
                        onPaint: {
                            var ctx = getContext("2d"); ctx.reset()
                            var c = chart; if (!c || !c.available) return
                            var span = Math.max(1e-9, c.high - c.low), n = c.buy.length
                            function xs(i) { return width - 4 - (width - 8) * (n - 1 - i) / (n - 1) }
                            function ys(v) { return height - 6 - (height - 12) * (v - c.low) / span }
                            var lines = [[c.sell, "" + theme.priceSell], [c.buy, "" + theme.priceBuy]]
                            for (var k = 0; k < lines.length; k++) {
                                ctx.strokeStyle = lines[k][1]; ctx.lineWidth = 2; ctx.beginPath()
                                for (var i = 0; i < n; i++) { if (i === 0) ctx.moveTo(xs(i), ys(lines[k][0][i])); else ctx.lineTo(xs(i), ys(lines[k][0][i])) }
                                ctx.stroke()
                            }
                            // 30-day moving average of the shop BUY price (dashed, muted); days without enough history are a gap
                            ctx.strokeStyle = "" + theme.textPrimary; ctx.lineWidth = 1.5; ctx.setLineDash([5, 4]); ctx.beginPath()
                            var pen = false
                            for (var j = 0; j < n; j++) {
                                var m = c.ma ? c.ma[j] : null
                                if (m === null || m === undefined) { pen = false; continue }
                                if (!pen) { ctx.moveTo(xs(j), ys(m)); pen = true } else ctx.lineTo(xs(j), ys(m))
                            }
                            ctx.stroke()
                        }
                    }
                    Txt { objectName: "priceChartEmpty"; Layout.fillWidth: true; visible: !dashboard.vm || !dashboard.vm.priceChart.available
                          value: "لا توجد أسعار مخزنة كافية للرسم"; color: theme.textMuted }
                    Txt { objectName: "priceChartReading"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; font.bold: true
                          visible: !!dashboard.vm && dashboard.vm.priceChart.available && dashboard.vm.priceHint.available
                          color: dashboard.vm && dashboard.vm.priceHint.tone === "buy" ? theme.priceBuy : dashboard.vm && dashboard.vm.priceHint.tone === "sell" ? theme.priceSell : theme.textSecondary
                          value: dashboard.vm ? "قراءة: " + dashboard.vm.priceHint.action + " — " + dashboard.vm.priceHint.versus : "" }
                    Line { visible: !!dashboard.vm && dashboard.vm.priceChart.available; label: "أدنى / أعلى"
                           value: dashboard.vm ? dashboard.vm.priceChart.lowText + " / " + dashboard.vm.priceChart.highText : "" }
                    Txt { visible: !!dashboard.vm && dashboard.vm.priceChart.available; Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost
                          value: "أخضر: شراء المحل • ذهبي: بيع المحل • منقّط: المتوسط المتحرك 30 يوم (شراء)" }
                }

                // --- الشارت المباشر للذهب (TradingView XAUUSD داخل البرنامج؛ رابط بديل لو المتصفح المضمّن غير متاح)
                Card {
                    objectName: "liveGoldChartCard"
                    Layout.columnSpan: dashboard.wide ? 2 : 1
                    title: "الشارت المباشر للذهب (XAUUSD)"
                    accent: theme.gold
                    GoldWebChart { Layout.fillWidth: true; Layout.preferredHeight: 340 }
                }

                // --- سعر الذهب (the engine's own formatted values)
                Card {
                    objectName: "priceCard"
                    visible: dashboard.showRetired
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
                    visible: dashboard.showRetired
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
                    Repeater {
                        model: dashboard.vm ? dashboard.vm.makingAverages : []
                        delegate: Line { required property var modelData; objectName: "makingAvg_" + modelData.key
                                         label: modelData.label; value: modelData.text; valueColor: theme.priceSell }
                    }
                    Txt { objectName: "unidentifiedToday"; Layout.fillWidth: true
                          visible: dashboard.vm && dashboard.vm.unidentifiedCount > 0
                          value: dashboard.vm ? "⚠ قطع بيع يدوية تحتاج مطابقة: " + dashboard.vm.unidentifiedCount : ""
                          color: theme.amber; font.pixelSize: (theme.fontTableHeader + dashboard.boost) }
                }

                // --- مبيعات اليوم بالوزن (billing permission)
                Card {
                    objectName: "soldWeightCard"
                    visible: dashboard.showRetired && dashboard.sales
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
                    visible: dashboard.showRetired
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

                // --- أسعار العملات (GoldTimo /rates, public market prices: every logged-in user): how many pounds one unit of each chosen currency is worth. Choices live in Settings.
                Card {
                    objectName: "ratesCard"
                    visible: !!dashboard.vm && dashboard.vm.currencyRates.offered
                    title: "أسعار العملات (بالجنيه)"
                    accent: theme.priceBuy
                    Repeater {
                        model: dashboard.vm ? dashboard.vm.currencyRates.rows : []
                        delegate: Line { required property var modelData
                                         objectName: "rateRow_" + modelData.code
                                         label: modelData.name + "  •  " + modelData.code; value: modelData.price; strong: true }
                    }
                    Empty { objectName: "ratesEmpty"; Layout.fillWidth: true; wrapMode: Text.WordWrap
                            value: dashboard.vm && dashboard.vm.currencyRates.error.length > 0 ? dashboard.vm.currencyRates.error : "لا توجد أسعار عملات الآن"
                            visible: !!dashboard.vm && dashboard.vm.currencyRates.rows.length === 0 }
                    Txt { objectName: "ratesUpdated"; Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost
                          visible: !!dashboard.vm && dashboard.vm.currencyRates.rows.length > 0
                          value: "GoldTimo • آخر تحديث " + (dashboard.vm ? dashboard.vm.currencyRates.updated : "")
                                 + (dashboard.vm && dashboard.vm.currencyRates.error.length > 0 ? " • " + dashboard.vm.currencyRates.error : "") }
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
                            RowLayout {
                                Layout.fillWidth: true
                                layoutDirection: Qt.RightToLeft
                                spacing: 8
                                Txt { Layout.fillWidth: true; value: modelData.age; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
                                Button {
                                    objectName: "overdueCopy_" + index
                                    text: "نسخ رسالة تذكير"
                                    font.family: theme.fontFamily
                                    onClicked: if (dashboard.vm) dashboard.vm.copyReminder(index)
                                }
                            }
                        }
                    }
                    Empty { objectName: "overdueEmpty"; value: "لا توجد ديون متأخرة ✓"
                            visible: !dashboard.vm || dashboard.vm.overdueDebts.count === 0 }
                }

                // --- سعر شراء الكسر اليوم (billing permission): the average paid per gram today against the latest stored SHOP_BUY of that karat
                Card {
                    objectName: "scrapTodayCard"
                    visible: dashboard.showRetired && dashboard.sales
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
                                  value: "سعر الشراء الآن " + modelData.price + (modelData.diff ? "  •  الفرق " + modelData.diff : "") + (modelData.discount ? "  •  " + modelData.discount : "")
                                  color: modelData.over ? theme.amber : theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
                        }
                    }
                    Empty { objectName: "scrapTodayEmpty"; value: "لم يُشترَ كسر اليوم بعد"
                            visible: !dashboard.vm || dashboard.vm.scrapToday.length === 0 }
                }

                // --- المبيعات آخر 14 يومًا (billing permission): one bar per day, the length is the share of the best day
                Card {
                    objectName: "salesDaysCard"
                    visible: dashboard.sales
                    title: "المبيعات آخر 14 يومًا (جنيه)"
                    accent: theme.priceSell
                    Repeater {
                        model: dashboard.vm ? dashboard.vm.salesDays : []
                        delegate: RowLayout {
                            required property var modelData
                            required property int index
                            objectName: "salesDay_" + index
                            Layout.fillWidth: true
                            layoutDirection: Qt.RightToLeft
                            spacing: 6
                            Txt { Layout.preferredWidth: 46; value: modelData.label; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
                            Item {
                                Layout.fillWidth: true; Layout.preferredHeight: 12
                                Rectangle { anchors.right: parent.right; height: parent.height; radius: 3; color: theme.priceSell
                                            width: Math.max(modelData.ratio > 0 ? 3 : 0, parent.width * modelData.ratio) }
                            }
                            Txt { Layout.preferredWidth: 96; value: modelData.text; font.pixelSize: theme.fontCaption + dashboard.boost; horizontalAlignment: Text.AlignLeft }
                        }
                    }
                }


                // --- فرق شراء/بيع المحل آخر 30 يومًا (من الأسعار المخزنة، بيوضح لو الهامش بيضيق)
                Card {
                    objectName: "spreadChartCard"
                    title: "فرق شراء/بيع عيار 21 — آخر 30 يومًا"
                    accent: theme.priceSell
                    Canvas {
                        objectName: "dashboardSpreadCanvas"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 150
                        readonly property var chart: dashboard.vm ? dashboard.vm.priceChart : ({})
                        onChartChanged: requestPaint()
                        onWidthChanged: requestPaint()
                        onPaint: {
                            var ctx = getContext("2d"); ctx.reset()
                            var c = chart; if (!c || !c.available) return
                            var n = c.buy.length, sp = [], lo = 1e18, hi = -1e18
                            for (var i = 0; i < n; i++) { var d = c.sell[i] - c.buy[i]; sp.push(d); lo = Math.min(lo, d); hi = Math.max(hi, d) }
                            var span = Math.max(1e-9, hi - lo)
                            ctx.strokeStyle = "" + theme.priceSell; ctx.lineWidth = 2; ctx.beginPath()
                            for (var j = 0; j < n; j++) {
                                var x = width - 4 - (width - 8) * (n - 1 - j) / Math.max(1, n - 1), y = height - 6 - (height - 12) * (sp[j] - lo) / span
                                if (j === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                            }
                            ctx.stroke()
                        }
                    }
                    Txt { objectName: "spreadChartEmpty"; Layout.fillWidth: true; visible: !dashboard.vm || !dashboard.vm.priceChart.available
                          value: "لا توجد أسعار مخزنة كافية للرسم"; color: theme.textMuted }
                    Txt { objectName: "spreadChartNote"; Layout.fillWidth: true; visible: !!dashboard.vm && dashboard.vm.priceChart.available; color: theme.textMuted
                          font.pixelSize: theme.fontCaption + dashboard.boost
                          value: dashboard.vm && dashboard.vm.priceChart.available ? "أقل فرق / أكبر فرق: " + (Math.min.apply(null, dashboard.vm.priceChart.sell.map(function(v, i) { return v - dashboard.vm.priceChart.buy[i] }))).toFixed(0) + " / " + (Math.max.apply(null, dashboard.vm.priceChart.sell.map(function(v, i) { return v - dashboard.vm.priceChart.buy[i] }))).toFixed(0) + " ج" : "" }
                }

                // --- مؤشرات سريعة (owner 2026-11-10): النهارده مقابل نفس اليوم الأسبوع اللي فات، الكسر مقابل المبيع بالعيار، ومدى سعر عيار 21 في آخر سنة
                Card {
                    objectName: "insightsCard"
                    title: "مؤشرات سريعة"
                    accent: theme.gold
                    Txt { Layout.fillWidth: true; value: "النهارده مقابل نفس اليوم الأسبوع اللي فات"; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost; visible: !!dashboard.vm && dashboard.vm.insights.hasWeek }
                    Line { objectName: "insightWeekToday"; visible: !!dashboard.vm && dashboard.vm.insights.hasWeek; label: "مبيعات النهارده"; value: dashboard.vm ? dashboard.vm.insights.weekToday : ""; strong: true }
                    Line { objectName: "insightWeekBefore"; visible: !!dashboard.vm && dashboard.vm.insights.hasWeek; label: "نفس اليوم الأسبوع اللي فات"; value: dashboard.vm ? dashboard.vm.insights.weekBefore : "" }
                    Line { objectName: "insightWeekChange"; visible: !!dashboard.vm && dashboard.vm.insights.hasWeek && dashboard.vm.insights.weekChange.length > 0; label: "التغيّر"
                           value: dashboard.vm ? dashboard.vm.insights.weekChange : ""; valueColor: dashboard.vm && dashboard.vm.insights.weekUp ? theme.priceBuy : theme.priceSell }
                    Txt { Layout.fillWidth: true; value: "الكسر المشترى مقابل المبيع النهارده (بالجرام، كل عيار لوحده)"; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost
                          visible: !!dashboard.vm && dashboard.vm.insights.scrap.length > 0 }
                    Repeater {
                        model: dashboard.vm ? dashboard.vm.insights.scrap : []
                        delegate: Line { required property var modelData
                                         objectName: "insightScrap_" + modelData.karat
                                         label: "عيار " + modelData.karat; value: "اشتريت " + modelData.bought + " • بعت " + modelData.sold; strong: true }
                    }
                    Txt { Layout.fillWidth: true; value: "سعر شراء المحل لعيار 21 في آخر " + (dashboard.vm ? dashboard.vm.insights.rangeDays : 0) + " يوم"; color: theme.textMuted
                          font.pixelSize: theme.fontCaption + dashboard.boost; visible: !!dashboard.vm && dashboard.vm.insights.hasRange }
                    Line { objectName: "insightRangeLow"; visible: !!dashboard.vm && dashboard.vm.insights.hasRange; label: "أقل / أعلى"
                           value: dashboard.vm ? dashboard.vm.insights.rangeLow + " / " + dashboard.vm.insights.rangeHigh : "" }
                    Line { objectName: "insightRangeNow"; visible: !!dashboard.vm && dashboard.vm.insights.hasRange; label: "الآن"
                           value: dashboard.vm ? dashboard.vm.insights.rangeNow + "  (" + dashboard.vm.insights.rangePosition + "% من المدى)" : ""; strong: true }
                    Rectangle {
                        visible: !!dashboard.vm && dashboard.vm.insights.hasRange
                        Layout.fillWidth: true; Layout.preferredHeight: 8; radius: 4; color: theme.border
                        Rectangle { width: parent.width * (dashboard.vm ? dashboard.vm.insights.rangePosition : 0) / 100; height: parent.height; radius: 4; color: theme.priceBuy; anchors.right: parent.right }
                    }
                    Empty { objectName: "insightsEmpty"; value: "لا توجد بيانات كافية بعد"
                            visible: !!dashboard.vm && !dashboard.vm.insights.hasWeek && !dashboard.vm.insights.hasRange && dashboard.vm.insights.scrap.length === 0 }
                }

                // --- تنبيه السعر (إعداد خاص بهذا الجهاز): حد أدنى لشراء المحل وحد أعلى لبيع المحل، عيار 21
                Card {
                    id: alertCard
                    objectName: "priceAlertCard"
                    title: "تنبيه السعر • عيار 21"
                    accent: !!dashboard.vm && dashboard.vm.priceAlert.triggered ? theme.red : theme.priceBuy
                    property string message: ""
                    Repeater {
                        model: dashboard.vm ? dashboard.vm.priceAlert.lines : []
                        delegate: Txt { required property var modelData
                                        objectName: "priceAlertLine"
                                        Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; font.bold: true
                                        value: "🔔 " + modelData.text; color: modelData.kind === "buy" ? theme.priceBuy : theme.priceSell }
                    }
                    Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost; wrapMode: Text.WordWrap; elide: Text.ElideNone
                          visible: !!dashboard.vm && !dashboard.vm.priceAlert.triggered
                          value: "لا يوجد تنبيه الآن. اكتب حدًا للشراء وحدًا للبيع (اترك الخانة فارغة لإلغائه)." }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Txt { value: "شراء المحل ≤" }
                        TextField { id: alertBuy; objectName: "priceAlertBuy"; Layout.fillWidth: true; horizontalAlignment: Text.AlignRight; font.family: theme.fontFamily
                                    placeholderText: "مثال 6000"; text: dashboard.vm ? dashboard.vm.priceAlert.buyBelow : ""; inputMethodHints: Qt.ImhFormattedNumbersOnly }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 8
                        Txt { value: "بيع المحل ≥" }
                        TextField { id: alertSell; objectName: "priceAlertSell"; Layout.fillWidth: true; horizontalAlignment: Text.AlignRight; font.family: theme.fontFamily
                                    placeholderText: "مثال 6400"; text: dashboard.vm ? dashboard.vm.priceAlert.sellAbove : ""; inputMethodHints: Qt.ImhFormattedNumbersOnly }
                    }
                    Button {
                        objectName: "priceAlertSave"
                        text: "حفظ التنبيه"
                        font.family: theme.fontFamily
                        onClicked: alertCard.message = dashboard.vm ? dashboard.vm.savePriceAlert(alertBuy.text, alertSell.text) : ""
                    }
                    Txt { objectName: "priceAlertMessage"; Layout.fillWidth: true; visible: alertCard.message.length > 0; value: alertCard.message; color: theme.red; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                }

                // --- الإشارة الفنية وإزاي أقرأ الشارت (owner 2026-11-10): عدّاد التحليل الفني للذهب + دليل قراءة بسيط
                Card {
                    objectName: "chartGuideCard"
                    Layout.columnSpan: 1
                    title: "الإشارة الفنية للذهب • إزاي أقرأ الشارت"
                    accent: theme.priceSell
                    Txt { Layout.fillWidth: true; value: "الإشارة الفنية للذهب (من «بيع قوي» إلى «شراء قوي»)"; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
                    GoldWebChart { objectName: "goldTechnicalGauge"; technical: true; Layout.fillWidth: true; Layout.preferredHeight: 300 }
                    Txt { objectName: "chartGuide"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost
                          value: "إزاي أقرأ الشارت قبل ما أقرر: (1) عدّاد التحليل الفني ملخص مؤشرات تلقائي من «بيع قوي» إلى «شراء قوي»؛ يوم واحد للقصير وأسبوع/شهر للأبعد، واتفاق الاتنين أقوى من واحد. (2) لو الذهب قريب من أعلى مدى في الشارت فده وقت مناسب للبيع والتسعير بحذر وإيقاف شراء الكسر بسعر عالي؛ لو قريب من أدنى مدى فهو فرصة لشراء الكسر والمخزون. (3) الدولار/الجنيه لو بيطلع فسعر الذهب محليًا بيطلع حتى لو الأونصة ثابتة. (4) ده إرشاد مش توصية مالية: قرر مع المخزون والسيولة وسعر المحل الفعلي." }
                }

                // --- أخبار بتأثر على الذهب (Investing.com بالعربي، RSS مجاني؛ الضغط على الخبر يفتحه في المتصفح)
                Card {
                    objectName: "goldNewsCard"
                    Layout.columnSpan: 1
                    title: "أخبار بتأثر على الذهب"
                    accent: theme.gold
                    Repeater {
                        model: dashboard.vm ? dashboard.vm.goldNews.items : []
                        delegate: Item {
                            required property var modelData
                            objectName: "goldNewsItem"
                            Layout.fillWidth: true
                            implicitHeight: headline.implicitHeight + stamp.implicitHeight + 8
                            Txt { id: headline; anchors.left: parent.left; anchors.right: parent.right; value: modelData.title; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                  color: theme.textPrimary; font.pixelSize: theme.fontBody + dashboard.boost }
                            Txt { id: stamp; anchors.left: parent.left; anchors.right: parent.right; anchors.top: headline.bottom; value: modelData.time; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: Qt.openUrlExternally(modelData.link) }
                        }
                    }
                    Txt { objectName: "goldNewsEmpty"; Layout.fillWidth: true; color: theme.textMuted; wrapMode: Text.WordWrap
                          visible: !!dashboard.vm && !dashboard.vm.goldNews.available
                          value: dashboard.vm && dashboard.vm.goldNews.error.length > 0 ? dashboard.vm.goldNews.error : "جاري تحميل الأخبار..." }
                    Txt { Layout.fillWidth: true; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost
                          visible: !!dashboard.vm && dashboard.vm.goldNews.available
                          value: "Investing.com • آخر تحديث " + (dashboard.vm ? dashboard.vm.goldNews.updated : "") }
                }

            }


            Item { Layout.preferredHeight: 4 }
        }
    }
}
