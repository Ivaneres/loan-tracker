"""API integration: preview suggest picks persist as bank_matched across imports."""
from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

import app as app_mod


def _manual(**kwargs):
    date = kwargs.get('date', '2024-05-08')
    base = {
        'id': kwargs.get('id', 'm1'),
        'date': date,
        'month': date[:7],
        'report_month': date[:7],
        'description': kwargs.get('description', 'Manual'),
        'amount': kwargs.get('amount', 10.0),
        'direction': 'outgoing',
        'category': 'dining',
        'source': 'manual',
    }
    base.update(kwargs)
    return base


class TestManualSuggestClaimMultiStatementFlow(unittest.TestCase):
    """End-to-end-ish API flow: preview → claim on import → next preview omits them."""

    def setUp(self):
        self.spending = {
            'transactions': [
                _manual(id='manual-pret', date='2024-05-08', amount=9.5, description='Lunch with Sam'),
                _manual(id='manual-cafe', date='2024-05-11', amount=10.0, description='Cafe stop'),
                _manual(id='manual-a', date='2024-05-15', amount=6.5, description='Sandwich'),
                _manual(id='manual-b', date='2024-05-15', amount=3.5, description='Drink'),
                _manual(id='manual-open', date='2024-05-20', amount=12.0, description='Still open'),
            ],
            'statements': [],
            'monthly_insights': {},
            'outgoing_classification_cache': {},
            'daily_budget': {'plan': {'bill_items': []}, 'goals': []},
        }
        self.data = {'users': {'ivan': {'spending': self.spending}}, 'loans': {}}
        self.client = app_mod.app.test_client()
        with self.client.session_transaction() as sess:
            sess['username'] = 'ivan'

    def test_full_flow_single_and_netted_claims_then_second_preview(self):
        period = {
            'report_month': '2024-05',
            'period_start': '2024-05-01',
            'period_end': '2024-05-31',
            'period_start_date': date(2024, 5, 1),
            'period_end_date': date(2024, 5, 31),
        }
        # --- Statement 1 preview: all five manuals unclaimed ---
        preview1 = app_mod._spending_statement_preview_finalize(
            self.data,
            self.spending,
            period,
            [],
            {'format': 'csv'},
            False,
            [
                {
                    'date': '2024-05-12',
                    'description': 'PRET A MANGER',
                    'amount': 10.35,
                    'direction': 'outgoing',
                },
                {
                    'date': '2024-05-16',
                    'description': 'CARD PAYMENT CAFE',
                    'amount': 10.0,
                    'direction': 'outgoing',
                },
                {
                    'date': '2024-05-18',
                    'description': 'SALARY',
                    'amount': 2000.0,
                    'direction': 'incoming',
                },
            ],
            None,
        )
        unmatched_ids = [m['id'] for m in preview1['summary']['unmatched_manuals']]
        self.assertEqual(
            set(unmatched_ids),
            {'manual-pret', 'manual-cafe', 'manual-a', 'manual-b', 'manual-open'},
        )
        pret_row = next(r for r in preview1['transactions'] if 'PRET' in r['description'])
        self.assertEqual(pret_row.get('preview_review_reason'), 'missed')
        self.assertTrue(pret_row.get('preview_manual_suggestions'))

        # --- Import: claim pret (near-miss) + netted a+b; insert salary only ---
        with mock.patch.object(app_mod, 'load_data', return_value=self.data), mock.patch.object(
            app_mod, 'save_data'
        ) as save_mock:
            resp = self.client.post(
                '/api/spending/statement/import',
                json={
                    'report_month': '2024-05',
                    'period_start': '2024-05-01',
                    'period_end': '2024-05-31',
                    'file_name': 'hsbc-1.csv',
                    'transactions': [
                        {
                            'date': '2024-05-18',
                            'description': 'SALARY',
                            'amount': 2000.0,
                            'direction': 'incoming',
                        }
                    ],
                    'manual_reconcile_claims': [
                        {
                            'manual_ids': ['manual-pret'],
                            'date': '2024-05-12',
                            'amount': 10.35,
                            'description': 'PRET A MANGER',
                            'direction': 'outgoing',
                        },
                        {
                            'manual_ids': ['manual-a', 'manual-b'],
                            'date': '2024-05-16',
                            'amount': 10.0,
                            'description': 'CARD PAYMENT CAFE',
                            'direction': 'outgoing',
                        },
                    ],
                },
            )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        body = resp.get_json()
        self.assertEqual(body['imported_count'], 1)
        self.assertEqual(body['manual_suggest_claims'], 2)
        save_mock.assert_called()

        by_id = {t['id']: t for t in self.spending['transactions']}
        self.assertTrue(by_id['manual-pret']['bank_matched'])
        self.assertEqual(by_id['manual-pret'].get('bank_matched_via'), 'preview_suggest')
        self.assertEqual(by_id['manual-pret']['amount'], 10.35)  # single near-miss adjusted
        self.assertEqual(by_id['manual-pret'].get('manual_amount'), 9.5)
        self.assertTrue(by_id['manual-a']['bank_matched'])
        self.assertTrue(by_id['manual-b']['bank_matched'])
        self.assertEqual(by_id['manual-a']['amount'], 6.5)  # netted amounts preserved
        self.assertEqual(by_id['manual-b']['amount'], 3.5)
        self.assertFalse(by_id['manual-cafe'].get('bank_matched'))
        self.assertFalse(by_id['manual-open'].get('bank_matched'))

        # --- Statement 2 preview: claimed manuals gone from sidebar / suggest pool ---
        preview2 = app_mod._spending_statement_preview_finalize(
            self.data,
            self.spending,
            period,
            [],
            {'format': 'csv'},
            False,
            [
                {
                    'date': '2024-05-12',
                    'description': 'PRET A MANGER AGAIN',
                    'amount': 10.35,
                    'direction': 'outgoing',
                },
                {
                    'date': '2024-05-13',
                    'description': 'COSTA COFFEE',
                    'amount': 10.15,
                    'direction': 'outgoing',
                },
            ],
            None,
        )
        unmatched2 = [m['id'] for m in preview2['summary']['unmatched_manuals']]
        self.assertEqual(unmatched2, ['manual-cafe', 'manual-open'])
        pret2 = next(r for r in preview2['transactions'] if 'PRET' in r['description'])
        suggest_ids = []
        for s in pret2.get('preview_manual_suggestions') or []:
            if s.get('ids'):
                suggest_ids.extend(s['ids'])
            elif s.get('id'):
                suggest_ids.extend(str(s['id']).split('+'))
        self.assertNotIn('manual-pret', suggest_ids)
        self.assertNotIn('manual-a', suggest_ids)
        self.assertNotIn('manual-b', suggest_ids)

    def test_claims_only_import_without_selected_rows(self):
        with mock.patch.object(app_mod, 'load_data', return_value=self.data), mock.patch.object(
            app_mod, 'save_data'
        ):
            resp = self.client.post(
                '/api/spending/statement/import',
                json={
                    'report_month': '2024-05',
                    'period_start': '2024-05-01',
                    'period_end': '2024-05-31',
                    'transactions': [],
                    'manual_reconcile_claims': [
                        {
                            'manual_ids': ['manual-open'],
                            'date': '2024-05-21',
                            'amount': 12.0,
                            'description': 'SHOP',
                            'direction': 'outgoing',
                        }
                    ],
                },
            )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        body = resp.get_json()
        self.assertEqual(body['imported_count'], 0)
        self.assertEqual(body['manual_suggest_claims'], 1)
        self.assertTrue(
            next(t for t in self.spending['transactions'] if t['id'] == 'manual-open')['bank_matched']
        )


if __name__ == '__main__':
    unittest.main()
