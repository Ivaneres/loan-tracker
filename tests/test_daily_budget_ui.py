"""UI presence checks for daily budget Plan fields (no browser required)."""
import unittest
from unittest import mock

import app as app_mod


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


if __name__ == '__main__':
    unittest.main()
