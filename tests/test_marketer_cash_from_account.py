"""Marketer cash handover from a bank / wallet account: the account -> Main Safe transfer and the ordinary handover, two audited steps with derived request keys."""
from contextlib import contextmanager
from unittest.mock import Mock, patch
from uuid import uuid4
import unittest

from services.market_trip_service import TripError
from services.marketer_custody_service import MarketerCustodyService


class Repo:
    def __init__(self, connection):
        pass

    def main_safe(self):
        return {"id": 1, "is_active": 1}


class FromAccountTests(unittest.TestCase):
    def make(self):
        service = MarketerCustodyService.__new__(MarketerCustodyService)
        service._session = Mock()
        service._writer = lambda expected: "actor"
        service._repository = Repo

        @contextmanager
        def transaction():
            yield object()
        service._transaction = transaction
        service.cash_handover = Mock(return_value={"code": "MH000001"})
        return service

    def test_a_bank_source_transfers_to_the_safe_then_hands_over_with_derived_keys(self):
        service, key = self.make(), str(uuid4())
        with patch("services.financial_account_service.FinancialAccountService") as fin:
            result = service.cash_handover_from_account("7", "BANK:3", "500", "2026-10-29", "", "ملاحظة", key)
        self.assertEqual(result, {"code": "MH000001"})
        args = fin.return_value.transfer.call_args
        self.assertEqual(args.args[:3], ("BANK:3", "CASH:1", "500.00"))
        transfer_key = args.args[4]
        handover_key = service.cash_handover.call_args.args[5]
        self.assertNotEqual(transfer_key, handover_key)
        self.assertEqual(service.cash_handover.call_args.args[1], "500.00")
        again = self.make()
        with patch("services.financial_account_service.FinancialAccountService") as fin2:
            again.cash_handover_from_account("7", "BANK:3", "500", "2026-10-29", "", "ملاحظة", key)
        self.assertEqual((fin2.return_value.transfer.call_args.args[4], again.cash_handover.call_args.args[5]), (transfer_key, handover_key))   # a retry repeats the same keys

    def test_the_main_safe_source_is_the_ordinary_handover(self):
        service = self.make()
        with patch("services.financial_account_service.FinancialAccountService") as fin:
            service.cash_handover_from_account("7", "CASH:1", "500", "2026-10-29", "", "", str(uuid4()))
        fin.return_value.transfer.assert_not_called()
        service.cash_handover.assert_called_once()

    def test_a_failed_handover_says_the_money_is_in_the_main_safe(self):
        service = self.make()
        service.cash_handover = Mock(side_effect=TripError("PARTY_INACTIVE", "المسوقجي غير نشط."))
        with patch("services.financial_account_service.FinancialAccountService"):
            with self.assertRaises(TripError) as refused:
                service.cash_handover_from_account("7", "BANK:3", "500", "2026-10-29", "", "", str(uuid4()))
        self.assertEqual(refused.exception.code, "HANDOVER_AFTER_TRANSFER_FAILED")
        self.assertIn("وصل المبلغ للخزنة الرئيسية", str(refused.exception))

    def test_a_refused_transfer_hands_nothing_over(self):
        service = self.make()
        with patch("services.financial_account_service.FinancialAccountService") as fin:
            fin.return_value.transfer.side_effect = PermissionError("لا صلاحية")
            with self.assertRaises(PermissionError):
                service.cash_handover_from_account("7", "BANK:3", "500", "2026-10-29", "", "", str(uuid4()))
        service.cash_handover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
