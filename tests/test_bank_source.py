"""Statement bank_source (UI: Source) on import and monthly transactions."""
import unittest
from unittest import mock

import app as app_mod


class TestCollectBankSources(unittest.TestCase):
    def test_collects_unique_case_insensitive(self):
        spending = {
            'statements': [
                {'bank_source': 'Monzo'},
                {'bank_source': 'monzo'},
                {'bank_source': 'Barclays'},
            ],
            'transactions': [
                {'bank_source': 'HSBC'},
                {'bank_source': ''},
                {'bank_source': None},
                {},
            ],
        }
        labels = app_mod._collect_bank_sources(spending)
        self.assertEqual(labels, ['Barclays', 'HSBC', 'Monzo'])

    def test_normalize_blank(self):
        self.assertIsNone(app_mod._normalize_bank_source(''))
        self.assertIsNone(app_mod._normalize_bank_source('   '))
        self.assertIsNone(app_mod._normalize_bank_source(None))
        self.assertEqual(app_mod._normalize_bank_source('  Monzo  '), 'Monzo')


class TestStatementImportBankSource(unittest.TestCase):
    def setUp(self):
        self.client = app_mod.app.test_client()
        self.spending = {
            'transactions': [],
            'statements': [{'id': 'old', 'bank_source': 'Barclays', 'report_month': '2024-01'}],
            'monthly_insights': {},
            'classification_overrides': {},
            'classification_cache': {},
            'daily_budget': {
                'plan': {
                    'income_monthly': 0,
                    'bills_monthly': 0,
                    'savings_percent': 0,
                    'daily_mode': 'fixed',
                    'bill_items': [],
                },
                'goals': [],
            },
        }
        self.data = {'users': {'ivan': {'spending': self.spending}}, 'loans': {}}

    def _login(self):
        with self.client.session_transaction() as sess:
            sess['username'] = 'ivan'

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_import_persists_source_on_tx_and_statement(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        payload = {
            'report_month': '2024-02',
            'period_start': '2024-02-01',
            'period_end': '2024-02-29',
            'file_name': 'monzo.csv',
            'source': 'Monzo',
            'transactions': [
                {
                    'date': '2024-02-10',
                    'description': 'Coffee',
                    'direction': 'outgoing',
                    'amount': 3.5,
                    'category': 'dining',
                },
            ],
        }
        with mock.patch.object(app_mod, '_recompute_monthly_insights'):
            with mock.patch.object(app_mod, 'apply_auto_transfer_pairing_for_month', return_value={}):
                resp = self.client.post('/api/spending/statement/import', json=payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['imported_count'], 1)
        self.assertEqual(body['statement']['bank_source'], 'Monzo')
        self.assertIn('Monzo', body['known_bank_sources'])
        self.assertIn('Barclays', body['known_bank_sources'])
        tx = self.spending['transactions'][0]
        self.assertEqual(tx['bank_source'], 'Monzo')
        self.assertEqual(tx['source'], 'statement')
        save_mock.assert_called()

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_import_without_source_omits_field(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        payload = {
            'report_month': '2024-02',
            'period_start': '2024-02-01',
            'period_end': '2024-02-29',
            'transactions': [
                {
                    'date': '2024-02-11',
                    'description': 'Shop',
                    'direction': 'outgoing',
                    'amount': 12.0,
                    'category': 'shopping',
                },
            ],
        }
        with mock.patch.object(app_mod, '_recompute_monthly_insights'):
            with mock.patch.object(app_mod, 'apply_auto_transfer_pairing_for_month', return_value={}):
                resp = self.client.post('/api/spending/statement/import', json=payload)
        self.assertEqual(resp.status_code, 200)
        tx = self.spending['transactions'][0]
        self.assertNotIn('bank_source', tx)
        self.assertNotIn('bank_source', resp.get_json()['statement'])

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_insights_include_bank_source(self, load_mock, save_mock):
        self.spending['transactions'] = [
            {
                'id': 't1',
                'date': '2024-02-10',
                'month': '2024-02',
                'report_month': '2024-02',
                'description': 'Coffee',
                'amount': 3.5,
                'direction': 'outgoing',
                'category': 'dining',
                'bank_source': 'Monzo',
                'source': 'statement',
            },
        ]
        self.spending['monthly_insights'] = {
            '2024-02': {
                'largest_outgoing': [],
                'budget_action_items': [],
                'net': -3.5,
            },
        }
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/api/spending/insights?month=2024-02')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['transactions'][0]['bank_source'], 'Monzo')
        self.assertIn('Monzo', body['known_bank_sources'])


if __name__ == '__main__':
    unittest.main()
