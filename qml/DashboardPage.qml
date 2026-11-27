import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "ChartKit.js" as Chart

// الرئيسية: what happened today, at a glance. Read-only: every figure comes formatted from
// DashboardViewModel (today's finalized invoices, COUNTED old-gold ledger) and GoldPriceViewModel.
// No SQL, no calculation here.
Item {
    id: dashboard
    // Two instances of this page exist (owner 2026-11-18): the home page (mode "home") and «السعر العالمي» (mode "gold"), which carries only the live gold chart and
    // the 21K price chart that used to sit on the home page.
    property string mode: "home"
    readonly property bool goldMode: mode === "gold"
    objectName: goldMode ? "goldMovementPage" : "dashboardPage"

    property string pageTitle: ""
    signal navigateTo(int page)

    readonly property var vm: typeof dashboardVM !== "undefined" ? dashboardVM : null
    readonly property var price: typeof goldPriceVM !== "undefined" ? goldPriceVM : null
    readonly property bool sales: !!vm && vm.canSeeSales
    // Owner 2026-11-10: five older cards are hidden by default (gold price, price indicator, scrap balance, sold weight, scrap buying price). One switch brings them back; their data and tests are untouched.
    property bool showRetired: false
    readonly property bool wide: width >= 1000
    readonly property int boost: 2                  // owner review (2026-10-22): the dashboard reads from a distance, every themed size is two points bigger

    Theme { id: theme }                                            // the light / navy experiment was rolled back (owner 2026-11-18)

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
        property color titleColor: theme.gold                           // the card colour is the colour of its title (owner 2026-11-18); a card that needs the employee's attention turns it to the warning colour
        property string badge: ""
        property bool stretch: true                                    // owner 2026-11-18: the cards of one row are level (the uneven heights looked untidy); set false for a card that must keep its content height
        default property alias content: body.data
        Layout.fillWidth: true
        Layout.fillHeight: card.stretch
        Layout.alignment: Qt.AlignTop
        Layout.preferredHeight: body.implicitHeight + 56
        Rectangle {                                                    // the soft shadow: the face shifted down, dark and translucent
            anchors.fill: face; anchors.topMargin: 5; anchors.bottomMargin: -5; anchors.leftMargin: 2; anchors.rightMargin: 2
            radius: face.radius; color: "#000000"; opacity: 0.28
        }
        Rectangle {
            id: face
            anchors.fill: parent; anchors.bottomMargin: 5
            color: theme.panel
            radius: theme.radiusCard
            // Owner 2026-11-18 (look pilot): a thin neutral edge; the card colour is the colour of its title (no outline and no marker).
            border.color: theme.cardBorder
            border.width: 1
            RowLayout {
                id: head
                anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
                anchors.topMargin: 9; anchors.leftMargin: 16; anchors.rightMargin: 18
                layoutDirection: Qt.RightToLeft
                spacing: 8
                Txt { value: card.title; color: card.titleColor; font.bold: true; font.pixelSize: theme.fontSection + dashboard.boost }
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

    // A group label between two rows of cards (owner 2026-11-26: the home page reads in order of importance): a muted title and a hairline, spanning the whole grid.
    component SectionTitle: RowLayout {
        property string value: ""
        Layout.fillWidth: true
        Layout.topMargin: 6
        visible: !dashboard.goldMode
        layoutDirection: Qt.RightToLeft
        spacing: 10
        Txt { value: parent.value; color: theme.textMuted; font.bold: true; font.pixelSize: theme.fontBody + dashboard.boost }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: theme.border }
    }

    component Empty: Txt {
        Layout.fillWidth: true
        color: theme.textMuted
        font.pixelSize: (theme.fontTableHeader + dashboard.boost)
    }

    // One key figure: small label, a big value, an optional note under it. The value / note text items carry the given object names.
    component Stat: ColumnLayout {
        id: stat
        property string label: ""
        property string value: ""
        property string note: ""
        property string valueName: ""
        property string noteName: ""
        property color tone: theme.textPrimary
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        spacing: 0
        Txt { Layout.fillWidth: true; value: stat.label; color: theme.textMuted; font.pixelSize: theme.fontTableHeader + dashboard.boost }
        Txt { objectName: stat.valueName; Layout.fillWidth: true; value: stat.value; color: stat.tone; font.bold: true
              font.pixelSize: theme.fontNumber + dashboard.boost + 8 }
        Txt { objectName: stat.noteName; Layout.fillWidth: true; value: stat.note; visible: stat.note.length > 0; color: theme.textMuted
              font.pixelSize: theme.fontCaption + dashboard.boost }
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

    // ------------------------------------------------------------ gold news ticker (owner 2026-11-18): the headlines scroll along the bottom of the page instead of a card.
    // Four copies of the headline strip are laid side by side and the strip slides one copy's width, so the loop is seamless. It moves in the reading direction of Arabic
    // (left to right), pauses while the mouse is over it and a click on a headline opens its article.
    Rectangle {
        id: ticker
        objectName: "newsTicker"
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        readonly property var items: dashboard.vm ? dashboard.vm.goldNews.items : []
        visible: items.length > 0 && !dashboard.goldMode
        height: visible ? 36 : 0
        color: theme.bgSecondary
        border.color: theme.cardBorder; border.width: 1

        Rectangle {
            id: tickerTag
            anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
            width: tagText.implicitWidth + 28
            color: theme.gold
            Txt { id: tagText; anchors.centerIn: parent; value: "أخبار الذهب"; color: theme.bgPrimary; font.bold: true; elide: Text.ElideNone
                  font.pixelSize: theme.fontBody + dashboard.boost }
        }
        Item {
            id: lane
            anchors.left: parent.left; anchors.right: tickerTag.left; anchors.top: parent.top; anchors.bottom: parent.bottom
            clip: true
            HoverHandler { id: laneHover }
            Row {
                id: strip
                objectName: "newsTickerStrip"
                height: parent.height
                readonly property real copyWidth: width / 4
                Repeater {
                    model: ticker.items.length > 0 ? ticker.items.concat(ticker.items, ticker.items, ticker.items) : []
                    delegate: Item {
                        id: headlineItem
                        required property var modelData
                        objectName: "newsTickerItem"
                        height: strip.height
                        width: headlineText.implicitWidth + 56
                        Rectangle { anchors.verticalCenter: parent.verticalCenter; anchors.left: parent.left; anchors.leftMargin: 24; width: 6; height: 6; rotation: 45; color: theme.gold }
                        Txt { id: headlineText; anchors.verticalCenter: parent.verticalCenter; anchors.right: parent.right; anchors.rightMargin: 0
                              value: headlineItem.modelData.title; color: theme.goldBright; elide: Text.ElideNone; font.pixelSize: theme.fontBody + dashboard.boost }
                        TapHandler { onTapped: if (headlineItem.modelData.link) Qt.openUrlExternally(headlineItem.modelData.link) }
                    }
                }
                NumberAnimation on x {
                    id: tickerAnimation
                    running: ticker.visible && strip.copyWidth > 0 && !laneHover.hovered
                    from: -strip.copyWidth; to: 0
                    duration: Math.max(1000, strip.copyWidth * 22)                 // about 45 px per second
                    loops: Animation.Infinite
                }
            }
        }
    }

    // ------------------------------------------------------------ page
    ScrollView {
        id: scroll
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.bottom: ticker.visible ? ticker.top : parent.bottom
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
                Txt { value: dashboard.goldMode ? "🌍 السعر العالمي" : "🏠 الرئيسية"; color: theme.textPrimary; font.bold: true; font.pixelSize: (theme.fontPageTitle + dashboard.boost) }
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
                columns: dashboard.goldMode ? 1 : (dashboard.wide ? 3 : 2)         // «السعر العالمي»: the two charts stand under each other
                layoutDirection: Qt.RightToLeft
                columnSpacing: 14
                rowSpacing: 10

                // --- شغل اليوم (billing permission): the day's key figures side by side, the first thing on the page (owner 2026-11-18: the earlier layout decisions are cancelled)
                Card {
                    objectName: "todayCard"
                    visible: (dashboard.sales) && !dashboard.goldMode
                    Layout.columnSpan: grid.columns
                    title: "شغل اليوم"
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 16
                        Stat { label: "عدد الفواتير"; valueName: "invoiceCount"; noteName: "invoiceBreakdown"
                               value: dashboard.vm ? String(dashboard.vm.invoiceCount) : "0"
                               note: dashboard.vm ? dashboard.vm.invoiceBreakdown : "" }
                        Rectangle { Layout.fillHeight: true; Layout.preferredWidth: 1; color: theme.border }
                        Stat { label: "قيمة المبيعات"; valueName: "sellTotal"; tone: theme.priceSell
                               value: dashboard.vm ? dashboard.vm.sellTotalText : "" }
                        Rectangle { Layout.fillHeight: true; Layout.preferredWidth: 1; color: theme.border }
                        Stat { label: "قيمة شراء الكسر"; valueName: "buyTotal"; tone: theme.priceBuy
                               value: dashboard.vm ? dashboard.vm.buyTotalText : "" }
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: dashboard.wide ? 3 : 2
                        layoutDirection: Qt.RightToLeft
                        columnSpacing: 24
                        rowSpacing: 0
                        Repeater {
                            model: dashboard.vm ? dashboard.vm.makingAverages : []
                            delegate: Line { required property var modelData; objectName: "makingAvg_" + modelData.key
                                             label: modelData.label; value: modelData.text; valueColor: theme.priceSell }
                        }
                    }
                }

                // --- سعر عيار 21 آخر 30 يومًا (the stored daily prices; shop BUY green, shop SELL amber) + a link to a live external chart
                Card {
                    id: priceChartCard
                    objectName: "priceChartCard"
                    stretch: true
                    title: "سعر عيار 21 — آخر 30 يومًا"
                    accent: theme.priceBuy
                    visible: dashboard.goldMode
                    Item {
                        id: priceBox
                        Layout.fillWidth: true
                        Layout.preferredHeight: 170
                        property int hover: -1
                        Canvas {
                            id: priceLine
                            objectName: "dashboardPriceCanvas"
                            anchors.fill: parent
                            readonly property var chart: dashboard.vm ? dashboard.vm.priceChart : ({})
                            property int hover: priceBox.hover
                            property var geo: null
                            onChartChanged: requestPaint()
                            onWidthChanged: requestPaint()
                            onHoverChanged: requestPaint()
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                var c = chart; if (!c || !c.available) return
                                var f = Chart.frame(ctx, width, height, c.low, c.high, { count: c.buy.length, labels: c.labels, maxLabels: 5, grid: "" + theme.border, text: "" + theme.textMuted })
                                geo = f
                                var surface = "" + theme.panel, sell = "" + theme.priceSell, buy = "" + theme.priceBuy
                                Chart.wash(ctx, f, c.buy, buy)
                                Chart.line(ctx, f, c.ma90, "" + theme.textMuted, [2, 4])       // the 90-day moving average of the shop BUY price (dotted, muted); days without 90 stored days behind them are a gap
                                Chart.line(ctx, f, c.ma, "" + theme.textSecondary, [5, 4])     // the 30-day moving average of the shop BUY price (dashed); days without enough history are a gap
                                Chart.line(ctx, f, c.sell, sell)
                                Chart.line(ctx, f, c.buy, buy)
                                Chart.endDot(ctx, f, c.sell, sell, surface)
                                Chart.endDot(ctx, f, c.buy, buy, surface)
                                if (hover >= 0 && hover < c.buy.length) {
                                    Chart.crosshair(ctx, f, hover, "" + theme.textMuted)
                                    Chart.dot(ctx, f.x(hover), f.y(c.sell[hover]), sell, surface)
                                    Chart.dot(ctx, f.x(hover), f.y(c.buy[hover]), buy, surface)
                                }
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            hoverEnabled: true
                            onPositionChanged: function (m) { priceBox.hover = dashboard.vm && priceLine.geo ? Chart.indexAt(priceLine.geo, m.x, dashboard.vm.priceChart.buy.length) : -1 }
                            onExited: priceBox.hover = -1
                        }
                        ChartTip {
                            objectName: "dashboardPriceTip"
                            shown: priceBox.hover >= 0 && !!dashboard.vm && dashboard.vm.priceChart.available
                            anchorX: shown && priceLine.geo ? priceLine.geo.x(priceBox.hover) : 0
                            title: shown ? dashboard.vm.priceChart.labels[priceBox.hover] : ""
                            rows: !shown ? [] : [{ name: "شراء المحل", value: dashboard.vm.priceChart.buyTexts[priceBox.hover], color: "" + theme.priceBuy },
                                                 { name: "بيع المحل", value: dashboard.vm.priceChart.sellTexts[priceBox.hover], color: "" + theme.priceSell }]
                                .concat(dashboard.vm.priceChart.maTexts[priceBox.hover] ? [{ name: "متوسط 30 يوم", value: dashboard.vm.priceChart.maTexts[priceBox.hover], color: "" + theme.textSecondary }] : [])
                                .concat(dashboard.vm.priceChart.ma90Texts[priceBox.hover] ? [{ name: "متوسط 90 يوم", value: dashboard.vm.priceChart.ma90Texts[priceBox.hover], color: "" + theme.textMuted }] : [])
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
                    Flow {
                        objectName: "priceChartLegend"
                        Layout.fillWidth: true
                        visible: !!dashboard.vm && dashboard.vm.priceChart.available
                        layoutDirection: Qt.RightToLeft
                        spacing: 10                                                      // wraps to a second row when the card is narrow
                        Repeater {
                            // the key of an average repeats its dash (the 30-day average is dashed, the 90-day average dotted), so the lines never differ by colour alone
                            model: [{ c: "" + theme.priceBuy, t: "شراء المحل", on: 0, off: 0 }, { c: "" + theme.priceSell, t: "بيع المحل", on: 0, off: 0 },
                                    { c: "" + theme.textSecondary, t: "متوسط 30 يوم (شراء)", on: 5, off: 4 }, { c: "" + theme.textMuted, t: "متوسط 90 يوم (شراء)", on: 2, off: 4 }]
                            delegate: RowLayout {
                                required property var modelData
                                objectName: "priceChartLegend_" + modelData.t
                                spacing: 5
                                Item {
                                    Layout.preferredWidth: 18; Layout.preferredHeight: 3
                                    clip: true
                                    Repeater {
                                        model: parent.parent.modelData.on > 0 ? Math.ceil(18 / (parent.parent.modelData.on + parent.parent.modelData.off)) : 1
                                        delegate: Rectangle {
                                            required property int index
                                            readonly property var key: parent.parent.modelData
                                            x: key.on > 0 ? index * (key.on + key.off) : 0
                                            width: key.on > 0 ? key.on : 18
                                            height: 3; radius: 1.5; color: key.c
                                        }
                                    }
                                }
                                Txt { value: parent.modelData.t; color: theme.textSecondary; font.pixelSize: theme.fontCaption + dashboard.boost }
                            }
                        }
                    }
                }

                // --- الشارت المباشر للذهب (TradingView XAUUSD داخل البرنامج؛ رابط بديل لو المتصفح المضمّن غير متاح)
                Card {
                    objectName: "liveGoldChartCard"
                    stretch: true
                    Layout.columnSpan: 1
                    title: "الشارت المباشر للذهب (XAUUSD)"
                    accent: theme.gold
                    visible: dashboard.goldMode
                    GoldWebChart { Layout.fillWidth: true; Layout.preferredHeight: dashboard.goldMode ? 420 : 260 }
                }

                // --- سعر الذهب (the engine's own formatted values)
                Card {
                    objectName: "priceCard"
                    visible: (dashboard.showRetired) && !dashboard.goldMode
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
                    visible: (dashboard.showRetired) && !dashboard.goldMode
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

                // --- مبيعات اليوم بالوزن (billing permission)
                Card {
                    objectName: "soldWeightCard"
                    visible: (dashboard.showRetired && dashboard.sales) && !dashboard.goldMode
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
                    visible: (dashboard.showRetired) && !dashboard.goldMode
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

                // --- سعر شراء الكسر اليوم (billing permission): the average paid per gram today against the latest stored SHOP_BUY of that karat
                Card {
                    objectName: "scrapTodayCard"
                    visible: (dashboard.showRetired && dashboard.sales) && !dashboard.goldMode
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

                // --- section label: follow
                SectionTitle { objectName: "dashboardSection_follow"; Layout.columnSpan: grid.columns; value: "محتاج متابعة" }

                // --- ديون متأخرة (customers permission): open owed items at least N days old, oldest-first reading of the customer ledger
                Card {
                    objectName: "overdueCard"
                    visible: (!!dashboard.vm && dashboard.vm.canSeeBalances) && !dashboard.goldMode
                    title: "ديون متأخرة (أكثر من " + (dashboard.vm ? dashboard.vm.overdueDebts.days : 0) + " يوم)"
                    accent: !!dashboard.vm && dashboard.vm.overdueDebts.count > 0 ? theme.red : theme.textMuted
                    titleColor: !!dashboard.vm && dashboard.vm.overdueDebts.count > 0 ? theme.statusWarning : theme.gold
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

                // --- أرصدة العملاء (customers permission): who owes and who is owed, from the customer ledger.
                // Only customers with account activity from the first day of the current month; a settled account disappears on its own.
                Card {
                    objectName: "customerBalancesCard"
                    visible: (!!dashboard.vm && dashboard.vm.canSeeBalances) && !dashboard.goldMode
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

                // --- أرصدة العملاء: لنا وعلينا (customers permission): one stacked bar of what customers owe the shop against what the shop owes them, over every unsettled account
                Card {
                    id: splitCard
                    objectName: "balanceSplitCard"
                    Layout.columnSpan: grid.columns === 2 ? 2 : 1           // the last card of a group takes the whole row when the grid has two columns (no half-empty row)
                    visible: (!!dashboard.vm && dashboard.vm.canSeeBalances) && !dashboard.goldMode
                    title: "أرصدة العملاء: لنا وعلينا"
                    accent: theme.gold
                    readonly property var split: dashboard.vm ? dashboard.vm.balanceSplit : ({ available: false, owedRatio: 0, creditRatio: 0 })
                    Item {
                        id: splitBar
                        objectName: "balanceSplitBar"
                        Layout.fillWidth: true
                        Layout.topMargin: 6
                        Layout.preferredHeight: 20
                        visible: splitCard.split.available
                        Rectangle {                                                     // على العميل on the physical right, as the first item of an RTL row
                            objectName: "balanceSplitOwedSegment"
                            anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                            width: splitCard.split.owedRatio > 0 ? Math.max(6, (splitBar.width - 2) * splitCard.split.owedRatio) : 0
                            radius: 6; color: theme.accountReceivable
                        }
                        Rectangle {
                            objectName: "balanceSplitCreditSegment"
                            anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                            width: splitCard.split.creditRatio > 0 ? Math.max(6, (splitBar.width - 2) * splitCard.split.creditRatio) : 0
                            radius: 6; color: theme.accountPayable
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 6
                        layoutDirection: Qt.RightToLeft
                        spacing: 16
                        visible: splitCard.split.available
                        Repeater {
                            model: [{ key: "owed", label: "على العميل", color: "" + theme.accountReceivable },
                                    { key: "credit", label: "للعميل", color: "" + theme.accountPayable }]
                            delegate: ColumnLayout {
                                id: splitSide
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                spacing: 0
                                RowLayout {
                                    Layout.fillWidth: true
                                    layoutDirection: Qt.RightToLeft
                                    spacing: 6
                                    Rectangle { Layout.preferredWidth: 10; Layout.preferredHeight: 10; radius: 3; color: splitSide.modelData.color }
                                    Txt { value: splitSide.modelData.label; color: theme.textMuted; font.pixelSize: theme.fontTableHeader + dashboard.boost }
                                    Item { Layout.fillWidth: true }
                                }
                                Txt { objectName: "balanceSplit_" + splitSide.modelData.key; Layout.fillWidth: true; value: splitCard.split[splitSide.modelData.key + "Text"]
                                      color: theme.textPrimary; font.bold: true; font.pixelSize: theme.fontNumber + dashboard.boost }
                                Txt { objectName: "balanceSplitNote_" + splitSide.modelData.key; Layout.fillWidth: true
                                      value: splitCard.split[splitSide.modelData.key + "Count"] + " • " + splitCard.split[splitSide.modelData.key + "Percent"]
                                      color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
                            }
                        }
                    }
                    Empty { objectName: "balanceSplitEmpty"; value: "لا توجد أرصدة غير متعادلة على العملاء أو لهم"; wrapMode: Text.WordWrap
                            visible: !splitCard.split.available }
                    Txt { objectName: "balanceSplitCaption"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                          value: "كل العملاء الذين رصيد حسابهم غير متعادل (من دفتر حساب العميل)"
                          color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
                }

                // --- section label: days
                SectionTitle { objectName: "dashboardSection_days"; Layout.columnSpan: grid.columns; value: "حركة آخر الأيام" }

                // --- المبيعات آخر 14 يومًا (billing permission): one bar per day, the length is the share of the best day
                Card {
                    objectName: "salesDaysCard"
                    visible: (dashboard.sales) && !dashboard.goldMode
                    title: "المبيعات آخر 14 يومًا (جنيه)"
                    accent: theme.priceSell
                    Item {
                        id: daysBox
                        Layout.fillWidth: true
                        Layout.preferredHeight: 190
                        readonly property var days: dashboard.vm ? dashboard.vm.salesDays : []
                        readonly property bool anyData: days.some(function (d) { return d.ratio > 0 })
                        Txt { objectName: "salesDaysEmpty"; anchors.centerIn: parent; visible: daysBox.days.length > 0 && !daysBox.anyData; value: "لا توجد مبيعات في هذه الفترة"; color: theme.textMuted }
                        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.bottomMargin: 20; height: 1; color: theme.border }
                        RowLayout {
                            anchors.fill: parent
                            spacing: 3
                            layoutDirection: Qt.LeftToRight
                            Repeater {
                                model: daysBox.days
                                delegate: Item {
                                    id: dayCell
                                    required property var modelData
                                    required property int index
                                    objectName: "salesDay_" + index
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    readonly property real room: height - 20 - 22
                                    Rectangle {
                                        id: dayBar
                                        width: Math.min(24, parent.width - 4)
                                        height: Math.max(dayCell.modelData.ratio > 0 ? 3 : 0, dayCell.room * dayCell.modelData.ratio)
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        anchors.bottom: parent.bottom
                                        anchors.bottomMargin: 21
                                        radius: 4
                                        color: dayCell.modelData.best ? theme.priceSell : Qt.alpha(theme.priceSell, 0.5)
                                        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: Math.min(4, parent.height); color: parent.color }   // the base is square, only the cap is rounded
                                    }
                                    Txt {
                                        visible: dayCell.modelData.best
                                        anchors.horizontalCenter: dayBar.horizontalCenter
                                        anchors.bottom: dayBar.top
                                        anchors.bottomMargin: 2
                                        width: 80
                                        horizontalAlignment: Text.AlignHCenter
                                        elide: Text.ElideNone
                                        value: dayCell.modelData.text
                                        color: theme.textPrimary
                                        font.bold: true
                                        font.pixelSize: theme.fontCaption + dashboard.boost
                                    }
                                    Txt {
                                        visible: (daysBox.days.length - 1 - dayCell.index) % 2 === 0        // every second day, the last day always labelled
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        anchors.bottom: parent.bottom
                                        width: 40
                                        horizontalAlignment: Text.AlignHCenter
                                        elide: Text.ElideNone
                                        value: dayCell.modelData.label
                                        color: theme.textMuted
                                        font.pixelSize: theme.fontCaption + dashboard.boost - 1
                                    }
                                    MouseArea {
                                        id: dayHover
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        ToolTip.visible: containsMouse
                                        ToolTip.delay: 0
                                        ToolTip.text: dayCell.modelData.label + "  •  " + dayCell.modelData.text
                                    }
                                }
                            }
                        }
                    }
                }

                // --- الكسر المشترى مقابل المبيع آخر 14 يوم (billing permission): two columns per day, 21K-equivalent grams (the service converts; the caption says so)
                Card {
                    objectName: "scrapSoldCard"
                    visible: (dashboard.sales) && !dashboard.goldMode
                    title: "الكسر المشترى مقابل المبيع آخر 14 يوم"
                    accent: theme.gold
                    Item {
                        id: scrapBox
                        objectName: "scrapSoldChart"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 190
                        property int hover: -1
                        readonly property var chart: dashboard.vm ? dashboard.vm.scrapSoldChart : ({ available: false, days: [] })
                        readonly property var days: chart.days
                        Txt { objectName: "scrapSoldEmpty"; anchors.centerIn: parent; visible: !scrapBox.chart.available; value: "لا يوجد شراء كسر أو بيع ذهب في آخر 14 يوم"; color: theme.textMuted }
                        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.bottomMargin: 20; height: 1; color: theme.border }
                        RowLayout {
                            id: scrapRow
                            anchors.fill: parent
                            spacing: 3
                            layoutDirection: Qt.LeftToRight
                            visible: scrapBox.chart.available
                            Repeater {
                                id: scrapDays
                                model: scrapBox.days
                                delegate: Item {
                                    id: scrapCell
                                    required property var modelData
                                    required property int index
                                    objectName: "scrapSoldDay_" + index
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    readonly property real room: height - 20 - 22
                                    readonly property real barWidth: Math.min(12, (width - 6) / 2)
                                    Rectangle { anchors.fill: parent; anchors.bottomMargin: 21; radius: 4; color: theme.textMuted; opacity: scrapBox.hover === scrapCell.index ? 0.1 : 0 }
                                    Repeater {
                                        model: [{ key: "bought", color: "" + theme.priceBuy, side: 1 }, { key: "sold", color: "" + theme.priceSell, side: -1 }]
                                        delegate: Item {
                                            id: column
                                            required property var modelData
                                            readonly property real ratio: scrapCell.modelData[modelData.key + "Ratio"]
                                            readonly property bool best: scrapCell.modelData[modelData.key + "Best"]
                                            objectName: "scrapSold_" + modelData.key + "_" + scrapCell.index
                                            width: scrapCell.barWidth
                                            height: Math.max(ratio > 0 ? 3 : 0, scrapCell.room * ratio)
                                            x: scrapCell.width / 2 + (modelData.side > 0 ? 1 : -1 - width)       // the first series (scrap bought) on the right of the pair, as in the legend
                                            y: scrapCell.height - 21 - height
                                            Rectangle {
                                                anchors.fill: parent
                                                radius: 4
                                                color: column.modelData.color
                                                Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: Math.min(4, parent.height); color: parent.color }   // square base, rounded cap
                                            }
                                            Txt {
                                                visible: column.best
                                                anchors.horizontalCenter: parent.horizontalCenter
                                                anchors.bottom: parent.top
                                                anchors.bottomMargin: 2
                                                width: 80
                                                horizontalAlignment: Text.AlignHCenter
                                                elide: Text.ElideNone
                                                value: scrapCell.modelData[column.modelData.key + "Text"]
                                                color: theme.textPrimary
                                                font.bold: true
                                                font.pixelSize: theme.fontCaption + dashboard.boost
                                            }
                                        }
                                    }
                                    Txt {
                                        visible: (scrapBox.days.length - 1 - scrapCell.index) % 2 === 0        // every second day, the last day always labelled
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        anchors.bottom: parent.bottom
                                        width: 40
                                        horizontalAlignment: Text.AlignHCenter
                                        elide: Text.ElideNone
                                        value: scrapCell.modelData.label
                                        color: theme.textMuted
                                        font.pixelSize: theme.fontCaption + dashboard.boost - 1
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        onEntered: scrapBox.hover = scrapCell.index
                                        onExited: if (scrapBox.hover === scrapCell.index) scrapBox.hover = -1
                                    }
                                }
                            }
                        }
                        ChartTip {
                            objectName: "scrapSoldTip"
                            shown: scrapBox.hover >= 0 && scrapBox.hover < scrapBox.days.length && scrapBox.chart.available
                            anchorX: shown && scrapDays.itemAt(scrapBox.hover) ? scrapDays.itemAt(scrapBox.hover).x + scrapDays.itemAt(scrapBox.hover).width / 2 : 0
                            title: shown ? scrapBox.days[scrapBox.hover].label : ""
                            rows: !shown ? [] : [{ name: "كسر مشترى", value: scrapBox.days[scrapBox.hover].boughtText, color: "" + theme.priceBuy },
                                                 { name: "ذهب مباع", value: scrapBox.days[scrapBox.hover].soldText, color: "" + theme.priceSell }]
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        layoutDirection: Qt.RightToLeft
                        spacing: 14
                        Repeater {
                            model: [{ c: "" + theme.priceBuy, t: "كسر مشترى من العملاء" }, { c: "" + theme.priceSell, t: "ذهب مباع" }]
                            delegate: RowLayout {
                                required property var modelData
                                spacing: 5
                                Rectangle { Layout.preferredWidth: 10; Layout.preferredHeight: 10; radius: 3; color: parent.modelData.c }
                                Txt { value: parent.modelData.t; color: theme.textSecondary; font.pixelSize: theme.fontCaption + dashboard.boost }
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }
                    Txt { objectName: "scrapSoldTotals"; Layout.fillWidth: true; visible: scrapBox.chart.available; wrapMode: Text.WordWrap; elide: Text.ElideNone
                          value: "الإجمالي: اشتريت " + scrapBox.chart.boughtTotal + " • بعت " + scrapBox.chart.soldTotal
                          color: theme.textSecondary; font.pixelSize: theme.fontCaption + dashboard.boost }
                    Txt { objectName: "scrapSoldCaption"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone
                          value: scrapBox.chart.caption
                          color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
                }
                // --- فرق شراء/بيع المحل آخر 30 يومًا (من الأسعار المخزنة، بيوضح لو الهامش بيضيق)
                Card {
                    objectName: "spreadChartCard"
                    Layout.columnSpan: grid.columns === 2 ? 2 : 1           // the last card of a group takes the whole row when the grid has two columns (no half-empty row)
                    title: "فرق شراء/بيع عيار 21 — آخر 30 يومًا"
                    accent: theme.priceSell
                    visible: !dashboard.goldMode
                    Item {
                        id: spreadBox
                        Layout.fillWidth: true
                        Layout.preferredHeight: 150
                        property int hover: -1
                        Canvas {
                            id: spreadCanvas
                            objectName: "dashboardSpreadCanvas"
                            anchors.fill: parent
                            readonly property var chart: dashboard.vm ? dashboard.vm.priceChart : ({})
                            property int hover: spreadBox.hover
                            property var geo: null
                            onChartChanged: requestPaint()
                            onWidthChanged: requestPaint()
                            onHoverChanged: requestPaint()
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                var c = chart; if (!c || !c.available) return
                                var sp = c.spread, lo = 1e18, hi = -1e18                                          // the spread series comes from the view model (exact Decimal difference)
                                for (var i = 0; i < sp.length; i++) { lo = Math.min(lo, sp[i]); hi = Math.max(hi, sp[i]) }
                                var f = Chart.frame(ctx, width, height, lo, hi, { count: sp.length, labels: c.labels, maxLabels: 5, grid: "" + theme.border, text: "" + theme.textMuted })
                                geo = f
                                var surface = "" + theme.panel, tone = "" + theme.priceSell
                                Chart.wash(ctx, f, sp, tone)
                                Chart.line(ctx, f, sp, tone)
                                Chart.endDot(ctx, f, sp, tone, surface)
                                if (hover >= 0 && hover < sp.length) { Chart.crosshair(ctx, f, hover, "" + theme.textMuted); Chart.dot(ctx, f.x(hover), f.y(sp[hover]), tone, surface) }
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            hoverEnabled: true
                            onPositionChanged: function (m) { spreadBox.hover = dashboard.vm && spreadCanvas.geo ? Chart.indexAt(spreadCanvas.geo, m.x, dashboard.vm.priceChart.buy.length) : -1 }
                            onExited: spreadBox.hover = -1
                        }
                        ChartTip {
                            shown: spreadBox.hover >= 0 && !!dashboard.vm && dashboard.vm.priceChart.available
                            anchorX: shown && spreadCanvas.geo ? spreadCanvas.geo.x(spreadBox.hover) : 0
                            title: shown ? dashboard.vm.priceChart.labels[spreadBox.hover] : ""
                            rows: !shown ? [] : [{ name: "الفرق بين البيع والشراء", value: dashboard.vm.priceChart.spreadTexts[spreadBox.hover], color: "" + theme.priceSell }]
                        }
                    }
                    Txt { objectName: "spreadChartEmpty"; Layout.fillWidth: true; visible: !dashboard.vm || !dashboard.vm.priceChart.available
                          value: "لا توجد أسعار مخزنة كافية للرسم"; color: theme.textMuted }
                    Txt { objectName: "spreadChartNote"; Layout.fillWidth: true; visible: !!dashboard.vm && dashboard.vm.priceChart.available; color: theme.textMuted
                          font.pixelSize: theme.fontCaption + dashboard.boost
                          value: dashboard.vm && dashboard.vm.priceChart.available ? "أقل فرق / أكبر فرق: " + dashboard.vm.priceChart.spreadMinText + " / " + dashboard.vm.priceChart.spreadMaxText + " ج" : "" }
                }

                // --- section label: reference
                SectionTitle { objectName: "dashboardSection_reference"; Layout.columnSpan: grid.columns; value: "مرجع وأسعار" }

                // --- مؤشرات سريعة (owner 2026-11-10): النهارده مقابل نفس اليوم الأسبوع اللي فات، الكسر مقابل المبيع بالعيار، ومدى سعر عيار 21 في آخر سنة
                Card {
                    objectName: "insightsCard"
                    title: "مؤشرات سريعة"
                    accent: theme.gold
                    visible: !dashboard.goldMode
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
                    Txt { Layout.fillWidth: true; value: "قراءة الأسعار (من أسعارك المخزنة)"; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost
                          visible: !!dashboard.vm && dashboard.vm.readings.offered }
                    Txt { objectName: "readingGold"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; visible: !!dashboard.vm && dashboard.vm.readings.offered
                          color: dashboard.vm && dashboard.vm.readings.gold.tone === "buy" ? theme.priceBuy : dashboard.vm && dashboard.vm.readings.gold.tone === "sell" ? theme.priceSell : theme.textSecondary
                          value: dashboard.vm ? dashboard.vm.readings.gold.text : "" }
                    Txt { objectName: "readingUsd"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; visible: !!dashboard.vm && dashboard.vm.readings.offered
                          value: dashboard.vm ? dashboard.vm.readings.usd.text : "" }
                    Txt { objectName: "readingEur"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; visible: !!dashboard.vm && dashboard.vm.readings.offered
                          value: dashboard.vm ? dashboard.vm.readings.eur.text : "" }
                    Empty { objectName: "insightsEmpty"; value: "لا توجد بيانات كافية بعد"
                            visible: !!dashboard.vm && !dashboard.vm.insights.hasWeek && !dashboard.vm.insights.hasRange && dashboard.vm.insights.scrap.length === 0 }
                }

                // --- أسعار العملات (GoldTimo /rates, public market prices: every logged-in user): how many pounds one unit of each chosen currency is worth. Choices live in Settings.
                Card {
                    objectName: "ratesCard"
                    visible: (!!dashboard.vm && dashboard.vm.currencyRates.offered) && !dashboard.goldMode
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

                // (the price-alert card was removed from the home page on 2026-11-18 at the owner request; the view-model slots stay)

                // --- الإشارة الفنية وإزاي أقرأ الشارت (owner 2026-11-10): عدّاد التحليل الفني للذهب + دليل قراءة بسيط
                Card {
                    objectName: "chartGuideCard"
                    Layout.columnSpan: grid.columns === 2 ? 2 : 1           // the last card of a group takes the whole row when the grid has two columns (no half-empty row)
                    title: "الإشارة الفنية للذهب • إزاي أقرأ الشارت"
                    accent: theme.priceSell
                    visible: !dashboard.goldMode
                    Txt { Layout.fillWidth: true; value: "الإشارة الفنية للذهب (من «بيع قوي» إلى «شراء قوي»)"; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost }
                    GoldWebChart { objectName: "goldTechnicalGauge"; technical: true; Layout.fillWidth: true; Layout.preferredHeight: 300 }
                    Txt { objectName: "chartGuide"; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: theme.textMuted; font.pixelSize: theme.fontCaption + dashboard.boost
                          value: "إزاي أقرأ الشارت قبل ما أقرر: (1) عدّاد التحليل الفني ملخص مؤشرات تلقائي من «بيع قوي» إلى «شراء قوي»؛ يوم واحد للقصير وأسبوع/شهر للأبعد، واتفاق الاتنين أقوى من واحد. (2) لو الذهب قريب من أعلى مدى في الشارت فده وقت مناسب للبيع والتسعير بحذر وإيقاف شراء الكسر بسعر عالي؛ لو قريب من أدنى مدى فهو فرصة لشراء الكسر والمخزون. (3) الدولار/الجنيه لو بيطلع فسعر الذهب محليًا بيطلع حتى لو الأونصة ثابتة. (4) ده إرشاد مش توصية مالية: قرر مع المخزون والسيولة وسعر المحل الفعلي." }
                }

            }


            Item { Layout.preferredHeight: 4 }
        }
    }
}
