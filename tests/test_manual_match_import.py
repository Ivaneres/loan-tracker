"""User-selected manual ↔ statement reconciliation on import."""
import unittest
from unittest import mock

import app as app_mod


def _manual_tx(**kwargs):
    base = {
        'id': kwargs.get('id', 'm1'),
        'date': kwargs.get('date', '2024-01-10'),
        'month': kwargs.get('date', '2024-01-10')[:7],
        'report_month': kwargs.get('date', '2024-01-10')[:7],
        'description': kwargs.get('description', 'Coffee'),
        'amount': kwargs.get('amount', 10.0),
        'direction': 'outgoing',
        'category': 'dining',
        'source': 'manual',
    }
    base.update(kwargs)
    return base


class TestUnmatchedManuals(unittest.TestCase):
    def test_lists_unmatched_manuals_for_report_month(self):
        spending = {
            'transactions': [
                _manual_tx(id='m1', date='2024-02-05'),
                _manual_tx(id='m2', date='2024-02-06', bank_matched=True),
                _manual_tx(id='m3', date='2024-03-01'),
                {'id': 's1', 'date': '2024-02-07', 'source': 'statement', 'amount': 5},
            ],
        }
        out = app_mod._spending_unmatched_manuals(spending, '2024-02')
        self.assertEqual([x['id'] for x in out], ['m1'])


class TestManualMatchImport(unittest.TestCase):
    def setUp(self):
        self.client = app_mod.app.test_client()
        self.manual = _manual_tx(id='m1', date='2024-02-01', amount=10.0, description='Lunch')
        self.spending = {
            'transactions': [self.manual],
            'statements': [],
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
    def test_user_manual_match_claims_when_fuzzy_would_fail(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        payload = {
            'report_month': '2024-02',
            'period_start': '2024-02-01',
            'period_end': '2024-02-29',
            'transactions': [
                {
                    'date': '2024-02-05',
                    'description': 'PRET A MANGER',
                    'direction': 'outgoing',
                    'amount': 10.35,
                    'category': 'dining',
                    'manual_match_id': 'm1',
                },
            ],
        }
        with mock.patch.object(app_mod, '_recompute_monthly_insights'):
            with mock.patch.object(app_mod, 'apply_auto_transfer_pairing_for_month', return_value={}):
                resp = self.client.post('/api/spending/statement/import', json=payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['imported_count'], 0)
        self.assertEqual(body['skipped_duplicates'], 1)
        self.assertTrue(self.manual['bank_matched'])
        self.assertEqual(self.manual['amount'], 10.35)
        self.assertEqual(self.manual['manual_amount'], 10.0)
        self.assertEqual(self.manual['date'], '2024-02-05')
        self.assertEqual(self.manual['manual_date'], '2024-02-01')

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_invalid_manual_match_id_rejected(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        payload = {
            'report_month': '2024-02',
            'period_start': '2024-02-01',
            'period_end': '2024-02-29',
            'transactions': [
                {
                    'date': '2024-02-05',
                    'description': 'SHOP',
                    'direction': 'outgoing',
                    'amount': 10.0,
                    'category': 'dining',
                    'manual_match_id': 'missing',
                },
            ],
        }
        with mock.patch.object(app_mod, '_recompute_monthly_insights'):
            with mock.patch.object(app_mod, 'apply_auto_transfer_pairing_for_month', return_value={}):
                resp = self.client.post('/api/spending/statement/import', json=payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('manual_match_id', resp.get_json().get('error', ''))

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_duplicate_manual_claim_in_one_import_rejected(self, load_mock, save_mock):
        self.spending['transactions'].append(_manual_tx(id='m2', date='2024-02-02', amount=12.0))
        load_mock.return_value = self.data
        self._login()
        payload = {
            'report_month': '2024-02',
            'period_start': '2024-02-01',
            'period_end': '2024-02-29',
            'transactions': [
                {
                    'date': '2024-02-05',
                    'description': 'A',
                    'direction': 'outgoing',
                    'amount': 10.0,
                    'category': 'dining',
                    'manual_match_id': 'm1',
                },
                {
                    'date': '2024-02-06',
                    'description': 'B',
                    'direction': 'outgoing',
                    'amount': 12.0,
                    'category': 'dining',
                    'manual_match_id': 'm1',
                },
            ],
        }
        with mock.patch.object(app_mod, '_recompute_monthly_insights'):
            with mock.patch.object(app_mod, 'apply_auto_transfer_pairing_for_month', return_value={}):
                resp = self.client.post('/api/spending/statement/import', json=payload)
        self.assertEqual(resp.status_code, 400)

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_preview_summary_includes_unmatched_manuals(self, load_mock, save_mock):
        load_mock.return_value = self.data
        spending = self.spending
        rows = [
            {
                'date': '2024-02-10',
                'amount': 20.0,
                'direction': 'outgoing',
                'description': 'UNKNOWN',
            },
        ]
        led, dup_u, fps = app_mod._apply_spending_preview_duplicate_marks('2024-02', rows, spending)
        self.assertEqual(led, 0)
        self.assertEqual(rows[0].get('preview_review_reason'), 'missed')
        unmatched = app_mod._spending_unmatched_manuals(spending, '2024-02')
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]['id'], 'm1')
