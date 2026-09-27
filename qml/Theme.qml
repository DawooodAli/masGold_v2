import QtQuick

QtObject {

    // Main prototype-inspired dark palette

    readonly property color bgPrimary: "#11151A"
    readonly property color bgSecondary: "#181D23"
    readonly property color bgTertiary: "#20262E"

    readonly property color panel: "#181D23"
    readonly property color panelHover: "#222A33"

    readonly property color border: "#303842"

    readonly property color gold: "#D0A64A"
    readonly property color goldBright: "#E2BC63"
    readonly property color goldDark: "#A98132"

    readonly property color textPrimary: "#F1F2F4"
    readonly property color textSecondary: "#CDD1D6"
    readonly property color textMuted: "#858D97"
    readonly property color textDisabled: "#5E6670"

    readonly property color green: "#54B689"
    readonly property color blue: "#5AA7E8"
    readonly property color amber: "#E4A853"
    readonly property color red: "#E06C75"
    readonly property color priceBuy: "#60C9AF"
    readonly property color priceSell: goldBright
    readonly property color priceWarning: "#E89A68"
    readonly property color priceError: red

    // MainWindow operational strip: one stable, restrained colour per figure (labels stay visible).
    readonly property color balanceCash: goldBright          // most prominent
    readonly property color balanceKarat21: "#4CC7BE"         // turquoise
    readonly property color balanceKarat18: "#6E9FE6"         // blue
    readonly property color balanceKarat24: "#B08CDE"         // violet

    // Customer account direction: على العميل (receivable, cool) vs للعميل (payable, warm).
    readonly property color accountReceivable: "#4FC3E8"
    readonly property color accountPayable: "#F08A4B"

    readonly property color redBackground: "#2D1A1D"

    readonly property int radiusSmall: 7
    readonly property int radiusMedium: 10

    // Typography scale (Cairo, pixels). The sizes already used most across the app, named so
    // new screens use a few deliberate steps instead of ad-hoc numbers. Existing pages are
    // not mass-migrated; they adopt these as they are touched.
    readonly property string fontFamily: "Cairo"
    readonly property int fontPageTitle: 18        // page heading
    readonly property int fontSection: 14          // card / section title
    readonly property int fontNumber: 22           // the one key figure of a card
    readonly property int fontBodyStrong: 13       // emphasised body values
    readonly property int fontBody: 12             // body text, table rows
    readonly property int fontTableHeader: 11      // table headers, field labels
    readonly property int fontCaption: 10          // helper / status text
    readonly property int fontPriceHero: 34        // the dashboard's 21K sell / buy prices (read from a distance)
}
