"""Owner 2026-11-08: typing the barcode of a SOLD piece in the Scrap -> new stock entry puts THAT piece back (same barcode); no invoice / customer account is touched."""
import unittest
from contextlib import contextmanager
from decimal import Decimal as D
from unittest.mock import Mock, patch

from services.old_gold_conversion_service import OldGoldConversionService


def make_session():
    from app.session import AppSession
    from services.authentication_service import AuthenticationResult, _VERIFIED
    session = AppSession()
    session._establish(AuthenticationResult(3, None, "مدير", frozenset({"APP_LOGIN", "OLD_GOLD_MANAGE"}), _VERIFIED), int(session.generation))
    return session


class RestockByBarcodeTests(unittest.TestCase):
    def run_convert(self, piece, weight="1.750", karat_id=1):
        from tests.auth_fakes import FakeAuthorization
        session = make_session()
        repository = Mock()
        repository.piece_by_barcode.return_value = piece
        repository.lock_old_gold_balance.return_value = [{"weight_delta_grams": D("10.000")}]

        @contextmanager
        def transaction():
            yield Mock(connection=Mock())
        restore = Mock()
        with patch("services.old_gold_conversion_service.DatabaseTransaction", transaction), \
                patch("services.old_gold_conversion_service.OldGoldConversionRepository", return_value=repository), \
                patch("services.stocktake_adjust_service.StocktakeAdjustService") as stocktake:
            stocktake.return_value.restore_piece = restore
            result = OldGoldConversionService(session=session, authorization=FakeAuthorization(session)).convert_to_new_stock(
                item_type_id=2, karat_id=karat_id, category_id=1, factory_id=0, result_weight_grams=weight,
                consumed_old_gold_grams=weight, size="", barcode_value="JG1", notes="")
        return result, restore, repository

    def test_a_sold_piece_is_put_back_as_the_same_piece(self):
        result, restore, repository = self.run_convert({"id": 7, "inventory_status": "SOLD", "karat_id": 1, "weight_grams": D("1.750")})
        restore.assert_called_once()
        self.assertEqual((restore.call_args.args[0], restore.call_args.args[2]), ("7", "1"))
        self.assertEqual((result["restocked"], result["result_inventory_item_id"]), (True, 7))
        repository.create_new_inventory_item.assert_not_called()

    def test_a_barcode_of_a_piece_in_stock_stays_refused(self):
        with self.assertRaises(ValueError):
            self.run_convert({"id": 7, "inventory_status": "IN_STOCK", "karat_id": 1, "weight_grams": D("1.750")})

    def test_a_different_weight_or_karat_is_refused(self):
        with self.assertRaises(ValueError):
            self.run_convert({"id": 7, "inventory_status": "SOLD", "karat_id": 1, "weight_grams": D("1.750")}, weight="2.000")
        with self.assertRaises(ValueError):
            self.run_convert({"id": 7, "inventory_status": "SOLD", "karat_id": 2, "weight_grams": D("1.750")})


if __name__ == "__main__":
    unittest.main()
