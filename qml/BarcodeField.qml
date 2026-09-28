import QtQuick
import QtQuick.Controls

// A barcode entry that does not depend on the Windows keyboard layout (see app/barcode_input.py).
// The scanner types through the physical keys; under the Arabic layout those keys produce Arabic
// letters. Each key's hardware scan code is mapped through a fixed US table instead.
//   mixed: false  → barcode-only field: every character key is mapped (manual typing gives ASCII too).
//   mixed: true   → a field that also takes Arabic text (item search): ordinary typing is untouched;
//                   only an entry that arrived as a scanner burst is rebuilt from its scan codes on Enter.
// Enter / Backspace / arrows / selection keep their normal behaviour; onAccepted still fires.
TextField {
    id: field
    property bool mixed: false
    property var strokes: []
    readonly property var codec: typeof barcodeInput !== "undefined" ? barcodeInput : null

    inputMethodHints: mixed ? Qt.ImhNone : (Qt.ImhPreferLatin | Qt.ImhNoPredictiveText)

    Keys.onPressed: function(event) {
        if (!field.codec || (event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)))
            return
        const shift = (event.modifiers & Qt.ShiftModifier) !== 0
        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            if (field.mixed) {
                const scanned = field.codec.fromBurst(field.strokes)
                if (scanned.length > 0)
                    field.text = scanned
            }
            field.strokes = []
            return                                   // not accepted: onAccepted runs as before
        }
        if (field.mixed) {
            if (!event.text || event.text.length === 0) {
                field.strokes = []
                return
            }
            const now = Date.now()
            const last = field.strokes.length ? field.strokes[field.strokes.length - 1].t : now
            const kept = now - last > 100 ? [] : field.strokes
            field.strokes = kept.concat([{ t: now, code: event.nativeScanCode, shift: shift }])
            return                                   // ordinary (Arabic) typing is never changed
        }
        const ch = field.codec.charFor(event.nativeScanCode, shift)
        if (ch.length > 0) {
            if (field.selectedText.length > 0)
                field.remove(field.selectionStart, field.selectionEnd)
            field.insert(field.cursorPosition, ch)
            event.accepted = true
        }
    }
}
