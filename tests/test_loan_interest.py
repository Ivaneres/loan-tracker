"""Retrospective loan interest helpers and apply_interest API."""
import unittest
from datetime import date
from unittest import mock

import app as app_mod


class TestResolveInterestAsOfDate(unittest.TestCase):
    def test_clamps_to_last_day_of_short_month(self):
        self.assertEqual(
            app_mod.resolve_interest_as_of_date(2024, 2, 31),
            date(2024, 2, 29),
        )
        self.assertEqual(
            app_mod.resolve_interest_as_of_date(2025, 2, 31),
            date(2025, 2, 28),
        )

    def test_uses_interest_day_when_valid(self):
        self.assertEqual(
            app_mod.resolve_interest_as_of_date(2025, 3, 15),
            date(2025, 3, 15),
        )

    def test_rejects_invalid_month(self):
        with self.assertRaises(ValueError):
            app_mod.resolve_interest_as_of_date(2025, 13, 1)


class TestLoanBalanceAsOfEndOfDay(unittest.TestCase):
    def test_includes_same_day_repayment(self):
        txs = [
            {'date': '2025-01-01', 'type': 'initial', 'amount': 10000.0},
            {'date': '2025-02-01', 'type': 'repayment', 'amount': -200.0},
            {'date': '2025-02-15', 'type': 'repayment', 'amount': -100.0},
        ]
        balance = app_mod.loan_balance_as_of_end_of_day(txs, date(2025, 2, 1))
        self.assertEqual(balance, 9800.0)

    def test_excludes_later_transactions(self):
        txs = [
            {'date': '2025-01-01', 'type': 'initial', 'amount': 10000.0},
            {'date': '2025-03-01', 'type': 'repayment', 'amount': -500.0},
        ]
        balance = app_mod.loan_balance_as_of_end_of_day(txs, date(2025, 2, 1))
        self.assertEqual(balance, 10000.0)

    def test_includes_prior_interest(self):
        txs = [
            {'date': '2025-01-01', 'type': 'initial', 'amount': 12000.0},
            {'date': '2025-01-01', 'type': 'interest', 'amount': 50.0},
            {'date': '2025-01-15', 'type': 'repayment', 'amount': -100.0},
        ]
        balance = app_mod.loan_balance_as_of_end_of_day(txs, date(2025, 2, 1))
        self.assertEqual(balance, 11950.0)


class TestInterestAppliedInMonth(unittest.TestCase):
    def test_detects_interest_in_month(self):
        txs = [
            {'date': '2025-03-01', 'type': 'interest', 'amount': 10.0},
        ]
        self.assertTrue(app_mod.interest_applied_in_month(txs, 2025, 3))
        self.assertFalse(app_mod.interest_applied_in_month(txs, 2025, 2))

    def test_ignores_non_interest(self):
        txs = [
            {'date': '2025-03-01', 'type': 'repayment', 'amount': -10.0},
        ]
        self.assertFalse(app_mod.interest_applied_in_month(txs, 2025, 3))


class TestComputeMonthlyInterestAmount(unittest.TestCase):
    def test_rounds_to_two_decimals(self):
        # 10000 * 5.5% / 12 = 45.8333... → 45.83
        self.assertEqual(app_mod.compute_monthly_interest_amount(10000, 5.5), 45.83)


class TestApplyInterestApi(unittest.TestCase):
    def setUp(self):
        self.client = app_mod.app.test_client()
        self.loan_id = 'loan-test-1'
        self.loan = {
            'name': 'Test Loan',
            'loan_amount': 9500.0,
            'interest_rate': 6.0,
            'interest_day': 1,
            'transactions': [
                {'date': '2025-01-01', 'type': 'initial', 'amount': 10000.0, 'description': 'Initial', 'user': 'ivan'},
                {'date': '2025-02-01', 'type': 'repayment', 'amount': -200.0, 'description': 'Bill', 'user': 'ivan'},
                {'date': '2025-03-15', 'type': 'repayment', 'amount': -300.0, 'description': 'Later', 'user': 'ivan'},
            ],
        }
        self.data = {'loans': {self.loan_id: self.loan}, 'users': {}}

    def _login(self):
        with self.client.session_transaction() as sess:
            sess['username'] = 'ivan'

    def test_retrospective_uses_eod_balance_not_current_amount(self):
        # EOD on 2025-02-01 = 10000 - 200 = 9800; interest at 6% = 49.00
        # Current loan_amount is 9500 (already includes March repayment)
        expected_interest = 49.0
        with mock.patch.object(app_mod, 'load_data', return_value=self.data), \
             mock.patch.object(app_mod, 'save_data') as save_mock:
            self._login()
            resp = self.client.post(
                f'/api/loan/{self.loan_id}/apply_interest',
                json={'year': 2025, 'month': 2},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        interest_txs = [t for t in body['transactions'] if t['type'] == 'interest']
        self.assertEqual(len(interest_txs), 1)
        self.assertEqual(interest_txs[0]['date'], '2025-02-01')
        self.assertEqual(interest_txs[0]['amount'], expected_interest)
        self.assertEqual(body['loan_amount'], 9500.0 + expected_interest)
        save_mock.assert_called_once()

    def test_duplicate_month_blocked(self):
        self.loan['transactions'].append({
            'date': '2025-02-01',
            'type': 'interest',
            'amount': 40.0,
            'description': 'Already applied',
            'user': 'ivan',
        })
        with mock.patch.object(app_mod, 'load_data', return_value=self.data), \
             mock.patch.object(app_mod, 'save_data') as save_mock:
            self._login()
            resp = self.client.post(
                f'/api/loan/{self.loan_id}/apply_interest',
                json={'year': 2025, 'month': 2},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('already been applied', resp.get_json()['error'])
        save_mock.assert_not_called()

    def test_today_path_blocked_when_month_has_interest(self):
        today = date.today()
        self.loan['transactions'].append({
            'date': today.strftime('%Y-%m-%d'),
            'type': 'interest',
            'amount': 10.0,
            'description': 'Already',
            'user': 'ivan',
        })
        with mock.patch.object(app_mod, 'load_data', return_value=self.data), \
             mock.patch.object(app_mod, 'save_data') as save_mock:
            self._login()
            resp = self.client.post(
                f'/api/loan/{self.loan_id}/apply_interest',
                json={},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('already been applied', resp.get_json()['error'])
        save_mock.assert_not_called()

    def test_clamps_interest_day_in_february(self):
        self.loan['interest_day'] = 31
        self.loan['transactions'] = [
            {'date': '2025-01-01', 'type': 'initial', 'amount': 12000.0, 'description': 'Initial', 'user': 'ivan'},
        ]
        self.loan['loan_amount'] = 12000.0
        # Feb 2025 has 28 days; EOD = 12000; 6%/12 = 60.00
        with mock.patch.object(app_mod, 'load_data', return_value=self.data), \
             mock.patch.object(app_mod, 'save_data'):
            self._login()
            resp = self.client.post(
                f'/api/loan/{self.loan_id}/apply_interest',
                json={'year': 2025, 'month': 2},
            )
        self.assertEqual(resp.status_code, 200)
        interest_txs = [t for t in resp.get_json()['transactions'] if t['type'] == 'interest']
        self.assertEqual(interest_txs[0]['date'], '2025-02-28')
        self.assertEqual(interest_txs[0]['amount'], 60.0)


if __name__ == '__main__':
    unittest.main()
