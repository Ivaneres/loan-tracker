"""Markup/CSS checks that Monthly spending does not page-scroll horizontally."""
import unittest
from pathlib import Path
from unittest import mock

import app as app_mod

ROOT = Path(__file__).resolve().parents[1]
STYLE = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')


class TestMonthlySpendingOverflowMarkup(unittest.TestCase):
    def setUp(self):
        self.client = app_mod.app.test_client()
        self.data = {
            'users': {
                'ivan': {
                    'spending': {
                        'transactions': [],
                        'monthly_insights': {},
                        'statements': [],
                    }
                }
            },
            'loans': {},
        }

    def _login(self):
        with self.client.session_transaction() as sess:
            sess['username'] = 'ivan'

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_transactions_table_uses_scroll_shell(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/spending')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('class="spending-tx-scroll"', html)
        self.assertIn('id="spending-insight-transactions-table"', html)
        self.assertIn('class="spending-tx-table', html)

    def test_css_locks_page_x_and_keeps_table_display(self):
        self.assertIn('html:has(body.spending-page)', STYLE)
        self.assertIn('body.spending-page', STYLE)
        self.assertIn('overflow-x: hidden', STYLE)
        self.assertIn('.spending-tx-scroll', STYLE)
        self.assertIn('.spending-tx-scroll > table.spending-tx-table', STYLE)
        self.assertIn('display: table', STYLE)


if __name__ == '__main__':
    unittest.main()
