"""Month-scoped reconcile session: stage, auto-match, link, confirm."""
import io
import unittest
from unittest import mock

import app as app_mod


def _manual(**kwargs):
    d = kwargs.get('date', '2024-06-10')
    base = {
        'id': kwargs.get('id', 'm1'),
        'date': d,
        'month': d[:7],
        'report_month': d[:7],
        'description': kwargs.get('description', 'Coffee'),
        'amount': kwargs.get('amount', 4.5),
        'direction': kwargs.get('direction', 'outgoing'),
        'category': kwargs.get('category', 'dining'),
    }
    if 'source' in kwargs:
        if kwargs['source'] is not None:
            base['source'] = kwargs['source']
        # else: omit source for legacy empty-source rows
    else:
        base['source'] = 'manual'
    for k, v in kwargs.items():
        if k == 'source':
            continue
        base[k] = v
    return base


def _stage_row(**kwargs):
    return {
        'id': kwargs.get('id', 'r1'),
        'date': kwargs.get('date', '2024-06-10'),
        'description': kwargs.get('description', 'COSTA COFFEE'),
        'direction': kwargs.get('direction', 'outgoing'),
        'amount': kwargs.get('amount', 4.5),
        'category': kwargs.get('category', 'dining'),
        'include': kwargs.get('include', True),
        'manual_match': kwargs.get('manual_match'),
        'transfer_pair': kwargs.get('transfer_pair'),
        'ledger_duplicate': False,
    }


class TestIsManualSpendingTx(unittest.TestCase):
    def test_empty_source_is_manual(self):
        self.assertTrue(app_mod._is_manual_spending_tx({'source': ''}))
        self.assertTrue(app_mod._is_manual_spending_tx({}))
        self.assertTrue(app_mod._is_manual_spending_tx({'source': 'Manual'}))
        self.assertTrue(app_mod._is_manual_spending_tx({'source': 'manual'}))
        self.assertFalse(app_mod._is_manual_spending_tx({'source': 'statement'}))

    def test_fuzzy_match_accepts_legacy_empty_source(self):
        spending = {'transactions': [_manual(id='legacy', source=None, amount=12.0, description='Shop')]}
        # Rebuild with no source key
        spending['transactions'][0].pop('source', None)
        match = app_mod._daily_budget_fuzzy_match_manual(
            spending,
            date_str='2024-06-10',
            amount=12.0,
            description='TESCO',
            direction='outgoing',
        )
        self.assertIsNotNone(match)
        self.assertEqual(match['id'], 'legacy')


class TestReconcileSessionHelpers(unittest.TestCase):
    def test_ensure_session_and_multi_upload_auto_match(self):
        spending = {
            'transactions': [
                _manual(id='m1', date='2024-06-05', amount=10.0, description='Lunch'),
                _manual(id='m2', date='2024-06-06', amount=25.0, description='Fuel'),
            ],
            'reconcile_sessions': {},
        }
        sess = app_mod._reconcile_ensure_session(spending, '2024-06')
        self.assertEqual(sess['status'], 'staging')
        sess['uploads'] = [
            {
                'id': 'u1',
                'file_name': 'monzo.csv',
                'bank_source': 'Monzo',
                'rows': [
                    _stage_row(id='r1', date='2024-06-05', amount=10.0, description='PRET A MANGER'),
                    _stage_row(
                        id='r_out',
                        date='2024-06-08',
                        amount=100.0,
                        description='To Amex',
                        direction='outgoing',
                        category='other',
                    ),
                ],
            },
            {
                'id': 'u2',
                'file_name': 'amex.csv',
                'bank_source': 'Amex',
                'rows': [
                    _stage_row(id='r2', date='2024-06-06', amount=25.0, description='SHELL GARAGE'),
                    _stage_row(
                        id='r_in',
                        date='2024-06-08',
                        amount=100.0,
                        description='From Monzo',
                        direction='incoming',
                        category=None,
                    ),
                ],
            },
        ]
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['manual_auto_matches'], 2)
        self.assertGreaterEqual(stats['transfer_auto_pairs'], 1)
        self.assertTrue(sess['auto_match_ran'])
        self.assertEqual(sess['status'], 'matched')

        payload = app_mod._reconcile_session_payload(sess, spending)
        self.assertEqual(payload['totals']['auto_matched_count'], 2)
        self.assertEqual(payload['totals']['unmatched_manual_count'], 0)
        self.assertGreaterEqual(payload['totals']['transfer_pair_count'], 1)

        # Both sides of transfer should reference each other
        by_id = {r['id']: r for r in payload['rows']}
        self.assertEqual(by_id['r_out']['transfer_pair']['peer_row_id'], 'r_in')
        self.assertEqual(by_id['r_in']['transfer_pair']['peer_row_id'], 'r_out')

    def test_link_unlink_reassign(self):
        spending = {
            'transactions': [
                _manual(id='m1', amount=5.0, description='A'),
                _manual(id='m2', amount=5.0, description='B'),
            ],
            'reconcile_sessions': {},
        }
        sess = app_mod._reconcile_ensure_session(spending, '2024-06')
        sess['uploads'] = [{
            'id': 'u1',
            'file_name': 'x.csv',
            'rows': [_stage_row(id='r1', amount=5.0, description='BANK')],
        }]
        row = sess['uploads'][0]['rows'][0]
        row['manual_match'] = {'manual_ids': ['m1'], 'via': 'auto'}
        app_mod._reconcile_release_manual_ids(sess, {'m1'}, keep_row_id='r1')
        # Reassign to m2
        app_mod._reconcile_release_manual_ids(sess, {'m2'}, keep_row_id='r1')
        row['manual_match'] = {'manual_ids': ['m2'], 'via': 'user'}
        claimed = app_mod._reconcile_claimed_manual_ids(sess)
        self.assertEqual(claimed, {'m2'})

        app_mod._reconcile_clear_manual_match_on_row(row)
        self.assertEqual(app_mod._reconcile_claimed_manual_ids(sess), set())

    def test_confirm_writes_bank_matched_and_statement_txs(self):
        spending = {
            'transactions': [
                _manual(id='m1', date='2024-06-05', amount=10.0, description='Lunch'),
            ],
            'statements': [],
            'monthly_insights': {},
            'reconcile_sessions': {},
        }
        sess = app_mod._reconcile_ensure_session(spending, '2024-06')
        sess['auto_match_ran'] = True
        sess['status'] = 'matched'
        sess['uploads'] = [{
            'id': 'u1',
            'file_name': 'monzo.csv',
            'bank_source': 'Monzo',
            'rows': [
                _stage_row(
                    id='r1',
                    date='2024-06-05',
                    amount=10.0,
                    description='PRET',
                    manual_match={'manual_ids': ['m1'], 'via': 'auto'},
                ),
                _stage_row(
                    id='r2',
                    date='2024-06-07',
                    amount=3.2,
                    description='BUS',
                    category='transport',
                ),
                _stage_row(
                    id='r_out',
                    date='2024-06-09',
                    amount=50.0,
                    description='To savings',
                    direction='outgoing',
                    category='other',
                    transfer_pair={'peer_row_id': 'r_in', 'via': 'user', 'pair_key': 'pk1'},
                ),
                _stage_row(
                    id='r_in',
                    date='2024-06-09',
                    amount=50.0,
                    description='From current',
                    direction='incoming',
                    category=None,
                    transfer_pair={'peer_row_id': 'r_out', 'via': 'user', 'pair_key': 'pk1'},
                ),
            ],
        }]
        with mock.patch.object(app_mod, 'apply_auto_transfer_pairing_for_month', return_value={'applied_pairs': 0}):
            result = app_mod._reconcile_confirm_session(spending, sess)
        self.assertEqual(result['imported_count'], 3)  # bus + two transfer legs (matched manual not inserted)
        self.assertEqual(result['manual_claims'], 1)
        man = next(t for t in spending['transactions'] if t['id'] == 'm1')
        self.assertTrue(man.get('bank_matched'))
        self.assertEqual(man.get('bank_matched_via'), 'reconcile_auto')
        self.assertEqual(sess['status'], 'imported')
        self.assertEqual(len(spending['statements']), 1)
        # Transfer pair applied
        outs = [t for t in spending['transactions'] if t.get('description') == 'To savings']
        ins = [t for t in spending['transactions'] if t.get('description') == 'From current']
        self.assertEqual(len(outs), 1)
        self.assertEqual(len(ins), 1)
        self.assertTrue(outs[0].get('internal_transfer'))
        self.assertEqual(outs[0].get('transfer_pair_id'), ins[0].get('transfer_pair_id'))


class TestReconcileApi(unittest.TestCase):
    def setUp(self):
        self.client = app_mod.app.test_client()
        self._data = {
            'users': {
                'admin': {
                    'spending': {
                        'transactions': [
                            _manual(id='m1', date='2024-06-10', amount=4.5, description='Coffee'),
                        ],
                        'statements': [],
                        'monthly_insights': {},
                        'classification_overrides': {},
                        'classification_cache': {},
                        'daily_budget': {},
                        'reconcile_sessions': {},
                    }
                }
            },
            'loans': {},
        }

    def _login(self):
        with self.client.session_transaction() as sess:
            sess['username'] = 'admin'

    def test_get_session_and_link_confirm_flow(self):
        self._login()
        with mock.patch.object(app_mod, 'load_data', return_value=self._data), mock.patch.object(
            app_mod, 'save_data'
        ):
            # Seed a staged upload without going through extraction
            spending = self._data['users']['admin']['spending']
            sess = app_mod._reconcile_ensure_session(spending, '2024-06')
            sess['uploads'] = [{
                'id': 'u1',
                'file_name': 't.csv',
                'rows': [_stage_row(id='r1', amount=4.5, description='COSTA')],
            }]

            resp = self.client.get('/api/spending/reconcile/2024-06')
            self.assertEqual(resp.status_code, 200)
            body = resp.get_json()
            self.assertEqual(body['session']['totals']['statement_count'], 1)

            resp = self.client.post(
                '/api/spending/reconcile/2024-06/auto-match',
                json={},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.get_json()['session']['auto_match_ran'])

            resp = self.client.post(
                '/api/spending/reconcile/2024-06/unlink-manual',
                json={'row_id': 'r1'},
            )
            self.assertEqual(resp.status_code, 200)

            resp = self.client.post(
                '/api/spending/reconcile/2024-06/link-manual',
                json={'row_id': 'r1', 'manual_ids': ['m1']},
            )
            self.assertEqual(resp.status_code, 200)
            mm = resp.get_json()['session']['rows'][0]['manual_match']
            self.assertEqual(mm['via'], 'user')

            with mock.patch.object(
                app_mod, 'apply_auto_transfer_pairing_for_month', return_value={'applied_pairs': 0}
            ):
                resp = self.client.post('/api/spending/reconcile/2024-06/confirm', json={})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.get_json()['session']['status'], 'imported')
            man = next(t for t in spending['transactions'] if t['id'] == 'm1')
            self.assertTrue(man.get('bank_matched'))


class TestReconcileUpload(unittest.TestCase):
    def setUp(self):
        self.client = app_mod.app.test_client()
        self._data = {
            'users': {
                'admin': {
                    'spending': {
                        'transactions': [],
                        'statements': [],
                        'monthly_insights': {},
                        'classification_overrides': {},
                        'classification_cache': {},
                        'daily_budget': {},
                        'reconcile_sessions': {},
                    }
                }
            },
            'loans': {},
        }
        with self.client.session_transaction() as sess:
            sess['username'] = 'admin'

    def test_upload_hsbc_csv_with_preamble(self):
        csv_body = (
            'Account name,My Current Account\n'
            'Sort code,40-00-00\n'
            'Date,Description,Paid Out,Paid In,Balance\n'
            '05/06/2024,PRET A MANGER,10.50,,\n'
            '06/06/2024,SALARY,,1500.00,\n'
        )
        with mock.patch.object(app_mod, 'load_data', return_value=self._data), mock.patch.object(
            app_mod, 'save_data'
        ):
            resp = self.client.post(
                '/api/spending/reconcile/2024-06/upload',
                data={
                    'file': (io.BytesIO(csv_body.encode('utf-8')), 'hsbc-june.csv'),
                    'bank_source': 'HSBC',
                },
                content_type='multipart/form-data',
            )
            self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
            body = resp.get_json()
            uploads = body['session']['uploads']
            self.assertEqual(len(uploads), 1)
            self.assertEqual(uploads[0]['file_name'], 'hsbc-june.csv')
            self.assertEqual(uploads[0]['bank_source'], 'HSBC')
            self.assertEqual(uploads[0]['row_count'], 2)


class TestReconcileUiPresence(unittest.TestCase):
    def setUp(self):
        self.client = app_mod.app.test_client()
        with self.client.session_transaction() as sess:
            sess['username'] = 'admin'

    def test_nav_and_reconcile_page(self):
        with mock.patch.object(app_mod, 'load_data', return_value={
            'users': {'admin': {'spending': {}}},
            'loans': {},
        }), mock.patch.object(app_mod, 'save_data'):
            resp = self.client.get('/spending/reconcile?month=2024-06')
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)
            self.assertIn('Reconcile', html)
            self.assertIn('Run auto-match', html)
            self.assertIn('Confirm import', html)
            self.assertIn('reconcile.js', html)

            home = self.client.get('/')
            home_html = home.get_data(as_text=True)
            self.assertIn('Open Reconcile', home_html)
            self.assertIn('/spending/reconcile', home_html)

            nav_daily = self.client.get('/spending/daily')
            self.assertIn('>Reconcile</a>', nav_daily.get_data(as_text=True))

    def test_reconcile_js_refreshes_after_upload(self):
        from pathlib import Path

        js = (Path(app_mod.app.root_path) / 'static' / 'reconcile.js').read_text(encoding='utf-8')
        self.assertIn('refreshFromSession(data)', js)
        self.assertIn("api(`/api/spending/reconcile/${encodeURIComponent(month)}/upload`", js)


if __name__ == '__main__':
    unittest.main()
