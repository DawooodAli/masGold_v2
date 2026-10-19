"""Click diagnostics for the real-click UI harness (tests/test_invoice_page_qml.py).

Every harness click is recorded cheaply (target, geometry, scroll position, overlay state, focus) and whether the target's own signals fired. When a test FAILS the harness
prints the records of clicks that never reached their control, plus the last few clicks, so an intermittent failure arrives with evidence. This module only OBSERVES:
it never retries, waits, filters or changes a click.
"""
from collections import deque
import json
import sys

from PySide6.QtCore import QMetaMethod, QPointF

KEPT = 40                    # clicks remembered per test
TAIL = 4                     # most recent clicks always printed
SIGNALS = ("pressed", "released", "canceled", "clicked", "pressAndHold")


def _name(item):
    return (item.objectName() if item is not None else "") or (item.metaObject().className() if item is not None else "")


def _overlay(window):
    """The window overlay (modal popups and their dimmer live here): how many of its children are visible right now."""
    for child in window.contentItem().childItems():
        if child.metaObject().className().startswith("QQuickOverlay"):
            visible = [c for c in child.childItems() if c.property("visible")]
            return {"visibleChildren": len(visible), "z": child.property("z")}
    return None


def _flick(item):
    parent = item.parentItem()
    while parent is not None and parent.objectName() not in ("partyScroll", "tripDetailScroll"):
        parent = parent.parentItem()
    if parent is None:
        return None
    return {"contentY": parent.property("contentY"), "contentHeight": parent.property("contentHeight"), "viewHeight": parent.height(),
            "moving": parent.property("moving"), "dragging": parent.property("dragging")}


def _has_qquick_mouse_event(item, name):
    meta = item.metaObject()
    signal_name = name.encode("ascii")
    for index in range(meta.methodCount()):
        method = meta.method(index)
        if (method.methodType() == QMetaMethod.MethodType.Signal
                and bytes(method.name()) == signal_name
                and b"QQuickMouseEvent*" in bytes(method.methodSignature())):
            return True
    return False


def hit_chain(window, scene_point):
    """Visible items containing the scene point, shallowest to deepest (the last ones are what a press reaches first)."""
    found = []

    def walk(item):
        if not item.property("visible"):
            return
        local = item.mapFromScene(scene_point)
        if 0 <= local.x() <= item.width() and 0 <= local.y() <= item.height():
            found.append(f"{_name(item)}(z={item.property('z')})")
            for child in item.childItems():
                walk(child)

    walk(window.contentItem())
    return found[-8:]


def begin(test, item, point):
    """Record one click about to be sent and listen for the target's own signals; returns the record for finish()."""
    window = test.root
    record = {"test": test._testMethodName, "target": _name(item), "point": [point.x(), point.y()], "window": [window.width(), window.height()],
              "visible": bool(item.property("visible")), "enabled": bool(item.property("enabled")), "size": [item.width(), item.height()],
              "scroll": _flick(item), "overlayBefore": _overlay(window), "signals": [], "_connections": []}
    for name in SIGNALS:
        signal = getattr(item, name, None)
        if signal is None or _has_qquick_mouse_event(item, name):
            continue
        try:
            record["_connections"].append((signal, signal.connect(lambda *a, name=name: record["signals"].append(name))))
        except (TypeError, RuntimeError):
            pass
    record["_item"] = item
    record["_listened"] = hasattr(item, "clicked")
    if not hasattr(test, "_click_records"):
        test._click_records = deque(maxlen=KEPT)
    test._click_records.append(record)
    return record


def finish(test, record):
    """After the click: stop listening, note whether the control received it and the state left behind."""
    item = record.pop("_item")
    listened = record.pop("_listened")
    for signal, connection in record.pop("_connections"):
        try:
            signal.disconnect(connection)
        except (TypeError, RuntimeError):                      # the click destroyed the control (a closing dialog, a rebuilt row): nothing left to disconnect
            pass
    record["reached"] = ("clicked" in record["signals"]) if listened else None          # None: not a button, nothing to compare
    record["overlayAfter"] = _overlay(test.root)
    if record["reached"] is False:                                                        # the expensive evidence only for a click that was lost
        scene = QPointF(*record["point"])
        try:
            record["hovered"] = item.property("hovered")
        except RuntimeError:
            record["hovered"] = "control destroyed"
        record["hit"] = hit_chain(test.root, scene)
        focus = test.root.activeFocusItem()
        record["focus"] = _name(focus) if focus is not None else None


def report(test, stream=None):
    """Print the diagnostics of a failed test: every lost click, then the most recent clicks."""
    records = list(getattr(test, "_click_records", ()))
    if not records:
        return
    stream = stream or sys.stderr
    lost = [r for r in records if r.get("reached") is False]
    shown = lost + [r for r in records[-TAIL:] if r not in lost]
    print(f"\n--- click diagnostics for failed test {test.id()}: {len(lost)} lost of the last {len(records)} clicks ---", file=stream)
    for record in shown:
        print(("LOST " if record.get("reached") is False else "ok   ") + json.dumps({k: v for k, v in record.items() if not k.startswith("_")},
                                                                                      ensure_ascii=False, default=str), file=stream)
