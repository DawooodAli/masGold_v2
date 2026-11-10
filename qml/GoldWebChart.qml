import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A live gold chart (TradingView widget, XAUUSD) embedded with QtWebEngine. When the embedded browser is not available (a test run, a missing module, the main process did not start it) the area shows a plain button that opens the chart in the browser.
Item {
    id: chart
    objectName: "goldWebChart"
    implicitHeight: 340
    property string symbol: "OANDA:XAUUSD"
    property string compare: ""
    property bool news: false
    readonly property bool enabled: typeof webChartEnabled !== "undefined" && webChartEnabled
    readonly property bool embedded: loader.status === Loader.Ready

    Theme { id: theme }

    Loader {
        id: loader
        anchors.fill: parent
        active: chart.enabled
        source: "GoldWebView.qml"
        onLoaded: { item.symbol = Qt.binding(function() { return chart.symbol }); item.compare = Qt.binding(function() { return chart.compare }); item.news = chart.news }
    }
    ColumnLayout {
        anchors.centerIn: parent
        visible: !chart.embedded
        spacing: 8
        Label { Layout.alignment: Qt.AlignHCenter; color: theme.textMuted; font.family: theme.fontFamily; text: "الشارت المباشر غير متاح داخل البرنامج الآن."
                horizontalAlignment: Text.AlignHCenter }
        Button {
            objectName: "goldWebChartOpen"
            Layout.alignment: Qt.AlignHCenter
            text: "فتح الشارت في المتصفح"
            onClicked: Qt.openUrlExternally("https://www.tradingview.com/chart/?symbol=" + encodeURIComponent(chart.symbol))
        }
    }
}
