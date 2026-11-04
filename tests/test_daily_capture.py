"""Phone capture ingest: token auth, dedup, confirm/dismiss, no double-count."""
import os
import unittest
from unittest import mock

import app as app_mod

TOKENS = 'ivan:tok-ivan,user:tok-user'


def _auth(token):
    return {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}


class DailyCaptureBase(unittest.TestCase):
    def setUp(self):
        self.data = {
            'users': {
                'ivan': {'spending': {}},
                'user': {'spending': {}},
            },
            'loans': {},
        }
        self.client = app_mod.app.test_client()
        app_mod._DAILY_CAPTURE_HITS.clear()
        self._env = mock.patch.dict(os.environ, {'DAILY_CAPTURE_TOKENS': TOKENS})
        self._env.start()
        self.addCleanup(self._env.stop)
        self._load = mock.patch.object(app_mod, 'load_data', return_value=self.data)
        self._load.start()
        self.addCleanup(self._load.stop)
        self._save = mock.patch.object(app_mod, 'save_data')
        self._save.start()
        self.addCleanup(self._save.stop)

    def _login(self, username='ivan'):
        with self.client.session_transaction() as sess:
            sess['username'] = username

    def _capture(self, token, **payload):
        body = {'amount': 12.34, 'title': 'TESCO STORES', 'source': 'wallet'}
        body.update(payload)
        return self.client.post(
            '/api/spending/daily/capture', json=body, headers=_auth(token)
        )

    def _spending(self, username='ivan'):
        return self.data['users'][username]['spending']


class TestCaptureAuth(DailyCaptureBase):
    def test_missing_token_is_unauthorized(self):
        resp = self.client.post('/api/spending/daily/capture', json={'amount': 1, 'title': 'X'})
        self.assertEqual(resp.status_code, 401)

    def test_wrong_token_is_unauthorized(self):
        resp = self._capture('nope')
        self.assertEqual(resp.status_code, 401)

    def test_valid_token_creates_pending_capture(self):
        resp = self._capture('tok-ivan', capture_id='c1')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()['duplicate'])
        self._login('ivan')
        status = self.client.get('/api/spending/daily/status').get_json()
        self.assertEqual(len(status['pending_captures']), 1)
        cap = status['pending_captures'][0]
        self.assertEqual(cap['id'], 'c1')
        self.assertEqual(cap['amount'], 12.34)
        self.assertEqual(cap['title'], 'TESCO STORES')
        self.assertEqual(cap['source'], 'wallet')

    def test_capture_only_visible_to_token_owner(self):
        self._capture('tok-ivan', capture_id='c1')
        self._login('user')
        status = self.client.get('/api/spending/daily/status').get_json()
        self.assertEqual(status['pending_captures'], [])

    def test_rate_limit_returns_429(self):
        with mock.patch.object(app_mod, '_DAILY_CAPTURE_RATE', 2):
            self.assertEqual(self._capture('tok-ivan', capture_id='a', amount=1.0).status_code, 200)
            self.assertEqual(self._capture('tok-ivan', capture_id='b', amount=2.0).status_code, 200)
            self.assertEqual(self._capture('tok-ivan', capture_id='c', amount=3.0).status_code, 429)


class TestCaptureValidation(DailyCaptureBase):
    def test_rejects_non_positive_amount(self):
        resp = self._capture('tok-ivan', amount=0)
        self.assertEqual(resp.status_code, 400)

    def test_rejects_missing_title(self):
        resp = self._capture('tok-ivan', title='', merchant='', description='')
        self.assertEqual(resp.status_code, 400)

    def test_rejects_invalid_date(self):
        resp = self._capture('tok-ivan', date='not-a-date')
        self.assertEqual(resp.status_code, 400)

    def test_future_date_is_clamped_to_today(self):
        resp = self._capture('tok-ivan', date='2999-01-01')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['capture']['date'], app_mod._daily_budget_today().isoformat())


class TestCaptureDedup(DailyCaptureBase):
    def test_same_capture_id_is_idempotent(self):
        first = self._capture('tok-ivan', capture_id='c1').get_json()
        second = self._capture('tok-ivan', capture_id='c1').get_json()
        self.assertTrue(second['duplicate'])
        self.assertEqual(second['capture']['id'], 'c1')
        self.assertEqual(second['capture']['created_at'], first['capture']['created_at'])
        self.assertEqual(len(self._spending()['daily_captures']), 1)

    def test_same_day_amount_title_collapses(self):
        self._capture('tok-ivan', capture_id='c1', title='Tesco')
        resp = self._capture('tok-ivan', capture_id='c2', title='TESCO')
        self.assertTrue(resp.get_json()['duplicate'])
        self.assertEqual(len(self._spending()['daily_captures']), 1)

    def test_different_amount_stays_separate(self):
        self._capture('tok-ivan', capture_id='c1', amount=5.0)
        resp = self._capture('tok-ivan', capture_id='c2', amount=6.0)
        self.assertFalse(resp.get_json()['duplicate'])
        self.assertEqual(len(self._spending()['daily_captures']), 2)


class TestCaptureConfirm(DailyCaptureBase):
    def test_entry_with_capture_id_accepts_and_links(self):
        self._capture('tok-ivan', capture_id='c1', amount=12.34, title='Tesco')
        self._login('ivan')
        resp = self.client.post('/api/spending/daily/entry', json={
            'amount': 12.34, 'title': 'Tesco', 'category': 'groceries',
            'date': app_mod._daily_budget_today().isoformat(), 'capture_id': 'c1',
        })
        self.assertEqual(resp.status_code, 200)
        tx = resp.get_json()['transaction']
        self.assertEqual(tx['capture_id'], 'c1')
        cap = self._spending()['daily_captures'][0]
        self.assertEqual(cap['status'], 'accepted')
        self.assertEqual(cap['transaction_id'], tx['id'])
        status = self.client.get('/api/spending/daily/status').get_json()
        self.assertEqual(status['pending_captures'], [])

    def test_entry_links_existing_manual_without_double_count(self):
        today = app_mod._daily_budget_today().isoformat()
        self._capture('tok-ivan', capture_id='c1', amount=12.34, title='Tesco', date=today)
        self._login('ivan')
        first = self.client.post('/api/spending/daily/entry', json={
            'amount': 12.34, 'title': 'Tesco', 'category': 'groceries', 'date': today,
        }).get_json()['transaction']
        # User already logged it manually, then confirms the capture.
        resp = self.client.post('/api/spending/daily/entry', json={
            'amount': 12.34, 'title': 'Tesco', 'category': 'groceries',
            'date': today, 'capture_id': 'c1',
        }).get_json()
        self.assertTrue(resp.get('linked_existing'))
        self.assertEqual(resp['transaction']['id'], first['id'])
        outgoing = [
            t for t in self._spending()['transactions']
            if t.get('source') == 'manual' and t.get('direction') == 'outgoing'
        ]
        self.assertEqual(len(outgoing), 1)

    def test_confirming_accepted_capture_again_creates_normal_entry(self):
        self._capture('tok-ivan', capture_id='c1', amount=12.34, title='Tesco')
        self._login('ivan')
        self.client.post('/api/spending/daily/entry', json={
            'amount': 12.34, 'title': 'Tesco', 'category': 'groceries', 'capture_id': 'c1',
        })
        # A second confirm is just a normal manual entry (capture no longer pending).
        resp = self.client.post('/api/spending/daily/entry', json={
            'amount': 12.34, 'title': 'Tesco', 'category': 'groceries', 'capture_id': 'c1',
        }).get_json()
        self.assertFalse(resp.get('linked_existing'))


class TestCaptureDismiss(DailyCaptureBase):
    def test_dismiss_removes_from_pending(self):
        self._capture('tok-ivan', capture_id='c1')
        self._login('ivan')
        resp = self.client.post('/api/spending/daily/capture/c1/dismiss')
        self.assertEqual(resp.status_code, 200)
        status = resp.get_json()['status']
        self.assertEqual(status['pending_captures'], [])
        self.assertEqual(self._spending()['daily_captures'][0]['status'], 'dismissed')

    def test_dismiss_unknown_capture_404(self):
        self._login('ivan')
        resp = self.client.post('/api/spending/daily/capture/missing/dismiss')
        self.assertEqual(resp.status_code, 404)

    def test_dismiss_requires_login(self):
        self._capture('tok-ivan', capture_id='c1')
        resp = self.client.post('/api/spending/daily/capture/c1/dismiss')
        self.assertEqual(resp.status_code, 302)


class TestCaptureSetupPage(DailyCaptureBase):
    def test_setup_page_requires_login(self):
        resp = self.client.get('/spending/daily/captures')
        self.assertEqual(resp.status_code, 302)

    def test_setup_page_shows_token_and_endpoint(self):
        self._login('ivan')
        html = self.client.get('/spending/daily/captures').get_data(as_text=True)
        self.assertIn('id="capture-token"', html)
        self.assertIn('tok-ivan', html)          # revealed on demand by the page JS
        self.assertIn('/api/spending/daily/capture', html)
        self.assertIn('id="capture-test"', html)
        self.assertNotIn('Server not set up yet', html)

    def test_setup_page_flags_missing_server_token(self):
        with mock.patch.dict(os.environ, {'DAILY_CAPTURE_TOKENS': ''}):
            self._login('ivan')
            html = self.client.get('/spending/daily/captures').get_data(as_text=True)
        self.assertIn('Server not set up yet', html)

    def test_setup_page_hides_token_of_other_user(self):
        self._login('user')
        html = self.client.get('/spending/daily/captures').get_data(as_text=True)
        self.assertIn('tok-user', html)
        self.assertNotIn('tok-ivan', html)

    def test_test_capture_endpoint_creates_pending(self):
        self._login('ivan')
        resp = self.client.post('/api/spending/daily/capture/test')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['capture']['title'], 'Test capture')
        status = self.client.get('/api/spending/daily/status').get_json()
        self.assertEqual(len(status['pending_captures']), 1)
        self.assertEqual(status['pending_captures'][0]['source'], 'other')

    def test_test_capture_requires_login(self):
        resp = self.client.post('/api/spending/daily/capture/test')
        self.assertEqual(resp.status_code, 302)


class TestCapturePrune(DailyCaptureBase):
    def test_prune_caps_records_and_keeps_newest(self):
        with mock.patch.object(app_mod, 'DAILY_CAPTURE_MAX', 3), \
                mock.patch.object(app_mod, 'DAILY_CAPTURE_PENDING_KEEP', 3):
            for i in range(5):
                self._capture('tok-ivan', capture_id=f'c{i}', amount=float(i + 1))
        caps = self._spending()['daily_captures']
        self.assertLessEqual(len(caps), 3)
        ids = {c['id'] for c in caps}
        self.assertIn('c4', ids)   # newest kept
        self.assertNotIn('c0', ids)  # oldest dropped

    def test_prune_prefers_keeping_pending_over_resolved(self):
        for i in range(3):
            self._capture('tok-ivan', capture_id=f'old{i}', amount=float(i + 1))
        self._login('ivan')
        self.client.post('/api/spending/daily/capture/old0/dismiss')
        self.client.post('/api/spending/daily/capture/old1/dismiss')
        with mock.patch.object(app_mod, 'DAILY_CAPTURE_MAX', 2), \
                mock.patch.object(app_mod, 'DAILY_CAPTURE_PENDING_KEEP', 2):
            self._capture('tok-ivan', capture_id='fresh', amount=99.0)
        ids = {c['id'] for c in self._spending()['daily_captures']}
        self.assertIn('fresh', ids)


if __name__ == '__main__':
    unittest.main()
