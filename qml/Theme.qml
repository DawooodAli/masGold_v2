import QtQuick

QtObject {

    // Owner request 2026-10-31: the application look is chosen in Settings > المظهر (a local choice of this workstation, kept by themeVM).
    // Every theme is a DARK palette: only the backgrounds, the borders and the accent («gold» = the accent of the brand) change, so the
    // semantic colours below (shop buy / shop sell, karats, status, account direction) mean the same thing in every theme.
    readonly property string mode: typeof themeVM !== "undefined" ? themeVM.name : "gold"
    readonly property var palettes: ({
        "gold":   { bg0: "#11151A", bg1: "#181D23", bg2: "#20262E", hover: "#222A33", line: "#303842", card: "#3B4552", a0: "#D0A64A", a1: "#E2BC63", a2: "#A98132" },
        "blue":   { bg0: "#0E141D", bg1: "#141C28", bg2: "#1B2536", hover: "#1F2B3E", line: "#2B3A52", card: "#37486A", a0: "#6FA8F0", a1: "#8FBDF5", a2: "#4A7FC4" },
        "green":  { bg0: "#0F1612", bg1: "#151F19", bg2: "#1C2A22", hover: "#213229", line: "#2B3E33", card: "#375043", a0: "#58C28E", a1: "#7AD6A8", a2: "#3E9A6D" },
        "violet": { bg0: "#14111B", bg1: "#1B1725", bg2: "#241F31", hover: "#2A2338", line: "#3A3150", card: "#4A3F66", a0: "#B08CE8", a1: "#C7A8F2", a2: "#8A66C4" }
    })
    readonly property var pal: palettes[mode] || palettes["gold"]

    readonly property color bgPrimary: pal.bg0
    readonly property color bgSecondary: pal.bg1
    readonly property color bgTertiary: pal.bg2

    readonly property color panel: pal.bg1
    readonly property color panelHover: pal.hover

    readonly property color border: pal.line

    readonly property color gold: pal.a0
    readonly property color goldBright: pal.a1
    readonly property color goldDark: pal.a2

    readonly property color textPrimary: "#F1F2F4"
    readonly property color textSecondary: "#CDD1D6"
    readonly property color textMuted: "#858D97"
    readonly property color textDisabled: "#5E6670"

    readonly property color green: "#77C599"
    readonly property color blue: "#70B0F1"
    readonly property color amber: "#E7AA5B"
    readonly property color red: "#E17C81"
    readonly property color priceBuy: "#73CBB0"
    readonly property color priceSell: "#E2BC63"          // SHOP_SELL is always gold / amber, whatever the theme accent
    readonly property color priceWarning: "#EC9066"
    readonly property color priceError: red

    // MainWindow operational strip: one stable, restrained colour per figure (labels stay visible).
    readonly property color balanceCash: goldBright          // most prominent
    // Owner review (2026-10-22): one harmonised family, chosen on the colour wheel in OKLCH (equal perceived lightness 0.74-0.80, chroma 0.03-0.125): the brand gold (hue 88) and its complement,
    // the trust blue (hue 250), with a slate gray and a cyan-teal between them. Purity reads as neutral gray (18K) -> trust blue (21K) -> teal (22K) -> pure gold (24K). Every colour keeps 6:1 contrast on the cards.
    readonly property color balanceKarat21: "#70B0F1"         // calm blue (the shop's main karat)
    readonly property color balanceKarat18: "#A5B2C3"         // slate gray-blue
    readonly property color balanceKarat22: "#5EC3CE"         // teal
    readonly property color balanceKarat24: "#DDB961"         // muted champagne gold
    // One explicit colour per karat: an unknown karat is muted, never painted as another karat.
    function balanceKaratColor(karat) {
        var k = String(karat)
        return k === "21" ? balanceKarat21 : k === "18" ? balanceKarat18 : k === "22" ? balanceKarat22
             : k === "24" ? balanceKarat24 : textMuted
    }

    // Customer account direction: على العميل (receivable, cool) vs للعميل (payable, warm).
    readonly property color accountReceivable: "#63BFE1"
    readonly property color accountPayable: "#EC9066"

    readonly property color redBackground: "#2D1A1D"

    // Semantic status colours (use these instead of picking amber / red per screen):
    //   statusWarning = a real warning, error or condition that needs the employee's attention (RED)
    //   statusSuccess = valid / completed (GREEN)
    //   statusInfo    = an important highlight that is NOT a warning (GOLD)
    readonly property color statusWarning: red
    readonly property color statusSuccess: green
    readonly property color statusInfo: gold

    readonly property int radiusSmall: 7
    readonly property int radiusMedium: 10
    // Cards (owner review 2026-10-22): separated, rounded, with a lighter border than a field's
    readonly property int radiusCard: 16
    readonly property color cardBorder: pal.card

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
