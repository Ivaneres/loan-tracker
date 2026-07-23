"""UI presence checks for daily budget Plan fields (no browser required)."""
import unittest
from pathlib import Path
from unittest import mock

import app as app_mod

ROOT = Path(__file__).resolve().parents[1]


class TestDailyBudgetPlanUiMarkup(unittest.TestCase):
    def setUp(self):
        self.client = app_mod.app.test_client()
        self.data = {'users': {'ivan': {'spending': {}}}, 'loans': {}}

    def _login(self):
        with self.client.session_transaction() as sess:
            sess['username'] = 'ivan'

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_plan_tab_renders_pay_day_and_tracking_from(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/spending/daily')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="plan-pay-day"', html)
        self.assertIn('id="plan-tracking-from"', html)
        self.assertIn('Payday (day of month)', html)
        self.assertIn('Tracking from', html)
        self.assertIn('Budget cycle runs from this day', html)

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_plan_tab_shows_income_first_breakdown_copy(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/spending/daily')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="plan-math"', html)
        self.assertIn(
            'Start from your monthly income. We subtract bills and reserved savings',
            html,
        )

        js = (ROOT / 'static' / 'daily_budget.js').read_text(encoding='utf-8')
        for needle in (
            'Total monthly income',
            'Bills (outgoing)',
            'Reserved savings (',
            'Available discretionary',
            'About per day (base',
            'Monthly breakdown',
        ):
            self.assertIn(needle, js)

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_today_tab_shows_limit_math_section(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/spending/daily')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="limit-math"', html)
        self.assertIn('Today’s limit math', html)
        self.assertIn('leftover discretionary into today’s daily limit', html)

        js = (ROOT / 'static' / 'daily_budget.js').read_text(encoding='utf-8')
        for needle in (
            'renderLimitMath',
            'Spent earlier this cycle',
            'Days left (incl. today)',
            'Today’s daily limit',
            'Unused from yesterday',
        ):
            self.assertIn(needle, js)


if __name__ == '__main__':
    unittest.main()
