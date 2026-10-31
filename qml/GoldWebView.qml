import QtQuick
import QtWebEngine

// The live gold chart: TradingView's free advanced-chart widget (XAUUSD) in an embedded browser. Loaded only through GoldWebChart.qml, which falls back to a plain link when this module is missing.
Item {
    WebEngineView {
        anchors.fill: parent
        backgroundColor: "#0f1115"
        Component.onCompleted: loadHtml(
            "<html><body style='margin:0;background:#0f1115'><div class='tradingview-widget-container' style='height:100vh;width:100%'>"
            + "<div class='tradingview-widget-container__widget' style='height:100%;width:100%'></div>"
            + "<script type='text/javascript' src='https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js' async>"
            + "{\"autosize\":true,\"symbol\":\"OANDA:XAUUSD\",\"interval\":\"D\",\"timezone\":\"Africa/Cairo\",\"theme\":\"dark\",\"style\":\"1\",\"locale\":\"ar_AE\","
            + "\"allow_symbol_change\":false,\"hide_side_toolbar\":true,\"calendar\":false,\"support_host\":\"https://www.tradingview.com\"}"
            + "</script></div></body></html>", "https://www.tradingview.com")
    }
}
