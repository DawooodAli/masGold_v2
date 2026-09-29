"""Shared read-side rules of Reversal / Correction V1 (023), used by Billing lifecycles and History.

A full reversal writes a REVERSED event on the original invoice (for an invoice, an Exchange or a
Return of it); the event names its source. A reversed invoice takes no further lifecycle step.
"""
import json


def correction_code(correction_type, correction_id):
    """RV + 6-digit id (full reversal) / FC + 6-digit id (settlement correction)."""
    return f"{'RV' if correction_type == 'FULL_REVERSAL' else 'FC'}{int(correction_id):06d}"


def _details(event):
    value = event.get("details_json")
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value or {}


def reversal_of(invoice, source_type="INVOICE", source_id=None):
    """The RV code that reversed this source (the invoice itself by default), from its events; else None."""
    source_id = str(source_id if source_id is not None else invoice.get("invoice_id"))
    for event in invoice.get("events", []) or []:
        if event.get("event_type") != "REVERSED":
            continue
        details = _details(event)
        if details.get("source_type") == source_type and str(details.get("source_id")) == source_id:
            return details.get("correction_code")
    return None
