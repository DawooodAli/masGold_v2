"""Display labels of the supplier gold ledger (045) and the «مسوقجي» role wording.

The stored movement-type names stay English (history is never rewritten); the Arabic label is chosen at read time by the stable movement code."""
from datetime import datetime
from decimal import Decimal as D
import unittest
from unittest.mock import patch

from app.viewmodels.supplier_viewmodel import SupplierViewModel
from services import supplier_rules as rules
from tests import supplier_fakes
from tests.supplier_fakes import build


def ledger_row(code, name, delta, **extra):
    return {"id": 1, "occurred_at": datetime(2026, 10, 10, 10, 0), "movement_code": code, "movement_name": name, "karat_id": 1, "karat_value": 21,
            "weight_delta_grams": D(delta), "weight_balance_after": D(delta), "source_type": "X", "source_id": 1, "source_reference": "SG000001",
            "actor_name": "المدير", **extra}


class LedgerLabelTests(unittest.TestCase):
    def test_purchase_and_settlement_types_have_arabic_labels_and_unknown_types_keep_their_stored_name(self):
        self.assertEqual(rules.gold_movement_text("PURCHASE_INVOICE", "Supplier Purchase Invoice"), "فاتورة شراء")
        self.assertEqual(rules.gold_movement_text("SETTLEMENT", "Supplier Gold Settlement"), "تسوية ذهب مورد")
        self.assertEqual(rules.gold_movement_text("SETTLEMENT_REVERSAL", "Supplier Gold Settlement Reversal"), "عكس تسوية ذهب مورد")
        self.assertEqual(rules.gold_movement_text(None, "Anything"), "Anything")                                                    # an old row without a code

    def test_the_supplier_detail_and_the_view_model_show_the_translated_labels_while_the_stored_name_is_untouched(self):
        db, session, authorization, suppliers, purchases = build()
        supplier = suppliers.create_supplier("مورد", karat_id=1)
        rows = [ledger_row("SETTLEMENT_REVERSAL", "Supplier Gold Settlement Reversal", "11.667", id=2, reverses_reference=None),
                ledger_row("SETTLEMENT", "Supplier Gold Settlement", "-11.667", settlement_id=1, delivered_weight_grams=D("10.000"),
                           delivered_karat_value=21),
                ledger_row("PURCHASE_INVOICE", "Supplier Purchase Invoice", "100.000", id=3)]
        with patch.object(supplier_fakes.SupplierRepo, "gold_ledger", lambda self, supplier_id, limit=200: rows):
            detail = suppliers.supplier_detail(supplier)
        self.assertEqual([r["movement"] for r in detail["gold_ledger"]], ["عكس تسوية ذهب مورد", "تسوية ذهب مورد", "فاتورة شراء"])
        self.assertEqual([r["movement_name"] for r in rows], ["Supplier Gold Settlement Reversal", "Supplier Gold Settlement", "Supplier Purchase Invoice"])
        vm = SupplierViewModel(session, suppliers)
        vm._detail = detail
        self.assertEqual([r["movement"] for r in vm.detail["goldLedger"]][:2], ["عكس تسوية ذهب مورد", "تسوية ذهب مورد"])


class MarketPartyWordingTests(unittest.TestCase):
    def test_the_role_is_called_masawwaqji_everywhere_in_the_user_facing_texts(self):
        self.assertEqual((rules.role_text(False, True), rules.role_text(True, True)), ("مسوقجي", "مورد • مسوقجي"))
        with self.assertRaises(rules.SupplierRuleError) as context:
            rules.validate_roles(False, False)
        self.assertEqual(str(context.exception), "اختر دورًا واحدًا على الأقل: مورد أو مسوقجي.")
        with self.assertRaises(rules.SupplierRuleError) as context:
            rules.require_supplier_role({"is_supplier": False})
        self.assertIn("مسوقجي فقط", str(context.exception))

    def test_no_user_facing_source_still_says_wasit(self):
        """«وسيط» / «وسطاء» are gone from the displayed texts of the QML, services and view models (internal names are English)."""
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        for folder, pattern in (("qml", "*.qml"), ("services", "*.py"), ("app", "*.py"), ("repositories", "*.py")):
            for path in (root / folder).rglob(pattern):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("وسيط", text, str(path))
                self.assertNotIn("وسطاء", text, str(path))
        qml = (root / "qml" / "SuppliersPage.qml").read_text(encoding="utf-8")
        self.assertIn('{ role: "INTERMEDIARY", text: "المسوقجية" }', qml)                   # the filter keeps its internal value
        self.assertIn('objectName: "supplierRoleIntermediary"; text: "مسوقجي"', qml)         # the checkbox keeps its objectName


if __name__ == "__main__":
    unittest.main()
