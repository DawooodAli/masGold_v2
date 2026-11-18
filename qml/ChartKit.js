.pragma library
// Shared drawing helpers of the Canvas charts (owner 2026-11-18, dataviz rules): a hairline grid with round tick values on the right (the reading side of an RTL page), 2px lines,
// a ~10% wash under a series, ONE end dot with a surface ring (never a dot on every point), a crosshair for the hovered day. Pure drawing: no data is read or calculated here
// except scaling to the canvas; every figure shown as text comes formatted from the view model.

function niceStep(range, count) {
    var raw = range / Math.max(count, 1)
    if (!(raw > 0)) return 1
    var mag = Math.pow(10, Math.floor(Math.log10(raw)))
    var unit = raw / mag
    return (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 5 ? 5 : 10) * mag
}

function fmt(v, decimals) {
    var parts = Math.abs(v).toFixed(decimals === undefined ? 0 : decimals).split(".")
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",")
    return (v < 0 ? "-" : "") + parts.join(".")
}

// Grid + tick labels (right) + x labels. o: {left,right,top,bottom, grid, text, count (points), labels, maxLabels, zeroBase, font}
// Returns the geometry {x(i), y(v), left, top, cw, ch, low, high, n}.
function frame(ctx, W, H, low, high, o) {
    var left = o.left === undefined ? 8 : o.left
    var right = o.right === undefined ? 56 : o.right
    var top = o.top === undefined ? 10 : o.top
    var bottom = o.bottom === undefined ? 22 : o.bottom
    var n = Math.max(o.count || 2, 2)
    var cw = Math.max(10, W - left - right)
    var ch = Math.max(10, H - top - bottom)
    if (o.zeroBase) { low = 0; high = high > 0 ? high * 1.08 : 1 }
    else {
        var pad = (high - low) * 0.1
        if (pad <= 0) pad = Math.max(1, Math.abs(high) * 0.05)
        low -= pad; high += pad
    }
    var step = niceStep(high - low, 4)
    var first = Math.ceil(low / step - 1e-9) * step
    var decimals = step < 1 ? 2 : 0
    function x(i) { return left + cw * i / (n - 1) }
    function y(v) { return top + ch * (1 - (v - low) / (high - low)) }
    ctx.lineWidth = 1
    ctx.font = (o.font || "11px Cairo")
    ctx.textAlign = "left"
    for (var gv = first; gv <= high + 1e-9; gv += step) {
        var gy = Math.round(y(gv)) + 0.5
        ctx.strokeStyle = o.grid
        ctx.beginPath(); ctx.moveTo(left, gy); ctx.lineTo(left + cw, gy); ctx.stroke()
        ctx.fillStyle = o.text
        ctx.fillText(fmt(gv, decimals), left + cw + 8, gy + 4)
    }
    var labels = o.labels || []
    if (labels.length) {
        var every = Math.max(1, Math.ceil(labels.length / (o.maxLabels || 7)))
        ctx.fillStyle = o.text
        ctx.textAlign = "center"
        for (var i = 0; i < labels.length; i += every) ctx.fillText(labels[i], Math.min(Math.max(x(i), left + 16), left + cw - 16), H - 6)
        ctx.textAlign = "left"
    }
    return { x: x, y: y, left: left, top: top, cw: cw, ch: ch, low: low, high: high, n: n }
}

// One series as a 2px line; a null / undefined value is a gap, never a zero.
function line(ctx, f, values, color, dash) {
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.lineCap = "round"
    ctx.setLineDash(dash || [])
    ctx.beginPath()
    var started = false
    for (var k = 0; k < values.length; ++k) {
        var v = values[k]
        if (v === null || v === undefined) { started = false; continue }
        if (!started) { ctx.moveTo(f.x(k), f.y(v)); started = true } else ctx.lineTo(f.x(k), f.y(v))
    }
    ctx.stroke()
    ctx.setLineDash([])
}

// The ~10% wash under a series (continuous runs only).
function wash(ctx, f, values, color) {
    var start = -1
    function close(end) {
        if (start < 0 || end - start < 1) { start = -1; return }
        ctx.beginPath(); ctx.moveTo(f.x(start), f.top + f.ch)
        for (var k = start; k <= end; ++k) ctx.lineTo(f.x(k), f.y(values[k]))
        ctx.lineTo(f.x(end), f.top + f.ch); ctx.closePath()
        ctx.globalAlpha = 0.1; ctx.fillStyle = color; ctx.fill(); ctx.globalAlpha = 1
        start = -1
    }
    for (var i = 0; i < values.length; ++i) {
        var ok = values[i] !== null && values[i] !== undefined
        if (ok && start < 0) start = i
        if (!ok && start >= 0) close(i - 1)
    }
    close(values.length - 1)
}

function dot(ctx, px, py, color, surface) {
    ctx.beginPath(); ctx.arc(px, py, 6, 0, 6.2832); ctx.fillStyle = surface; ctx.fill()        // the 2px surface ring
    ctx.beginPath(); ctx.arc(px, py, 4, 0, 6.2832); ctx.fillStyle = color; ctx.fill()
}

function endDot(ctx, f, values, color, surface) {
    for (var k = values.length - 1; k >= 0; --k) {
        if (values[k] !== null && values[k] !== undefined) { dot(ctx, f.x(k), f.y(values[k]), color, surface); return }
    }
}

function crosshair(ctx, f, index, color) {
    ctx.strokeStyle = color; ctx.lineWidth = 1
    var px = Math.round(f.x(index)) + 0.5
    ctx.beginPath(); ctx.moveTo(px, f.top); ctx.lineTo(px, f.top + f.ch); ctx.stroke()
}

// The index of the point under a mouse x (clamped), -1 when there is nothing to hover.
function indexAt(f, px, count) {
    if (!f || count < 2) return -1
    return Math.max(0, Math.min(count - 1, Math.round((px - f.left) / f.cw * (count - 1))))
}
