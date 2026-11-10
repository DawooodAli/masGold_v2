import QtQuick
import QtWebEngine

// The live gold chart: TradingView's free advanced-chart widget (XAUUSD) in an embedded browser. Loaded only through GoldWebChart.qml, which falls back to a plain link when this module is missing.
Item {
    id: view
    property string symbol: "OANDA:XAUUSD"
    property string compare: ""            // an optional second symbol drawn on its own price scale
    property bool news: false              // the free TradingView news timeline for the symbol instead of a chart
    function build() {
        if (view.news) {
            var nc = {feedMode: "symbol", symbol: view.symbol, colorTheme: "dark", isTransparent: false, displayMode: "regular", width: "100%", height: "100%", locale: "ar_AE"}
            return "<html><body style='margin:0;background:#0f1115'><div class='tradingview-widget-container' style='height:100vh;width:100%'>"
                + "<div class='tradingview-widget-container__widget' style='height:100%;width:100%'></div>"
                + "<script type='text/javascript' src='https://s3.tradingview.com/external-embedding/embed-widget-timeline.js' async>"
                + JSON.stringify(nc) + "</script></div></body></html>"
        }
        var conf = {autosize: true, symbol: view.symbol, interval: "D", timezone: "Africa/Cairo", theme: "dark", style: "1", locale: "ar_AE",
                    allow_symbol_change: false, hide_side_toolbar: true, calendar: false, support_host: "https://www.tradingview.com"}
        if (view.compare.length > 0) conf.compare_symbols = [{symbol: view.compare, position: "NewPriceScale"}]
        return "<html><body style='margin:0;background:#0f1115'><div class='tradingview-widget-container' style='height:100vh;width:100%'>"
            + "<div class='tradingview-widget-container__widget' style='height:100%;width:100%'></div>"
            + "<script type='text/javascript' src='https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js' async>"
            + JSON.stringify(conf) + "</script></div></body></html>"
    }
    WebEngineView {
        id: web
        anchors.fill: parent
        backgroundColor: "#0f1115"
        Component.onCompleted: loadHtml(view.build(), "https://www.tradingview.com")
    }
    onSymbolChanged: if (web.url !== undefined) web.loadHtml(view.build(), "https://www.tradingview.com")
    onCompareChanged: if (web.url !== undefined) web.loadHtml(view.build(), "https://www.tradingview.com")
}
