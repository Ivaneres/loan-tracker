"""Global spending transaction search (API + Search page)."""
import unittest
from pathlib import Path
from unittest import mock

import app as app_mod

ROOT = Path(__file__).resolve().parents[1]
STYLE = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
SEARCH_JS = (ROOT / 'static' / 'spending_search.js').read_text(encoding='utf-8')


def _tx(
    tx_id,
    *,
    date,
    description,
    amount,
    direction='outgoing',
    category='dining',
    bank_source=None,
    report_month=None,
):
    month = report_month or date[:7]
    row = {
        'id': tx_id,
        'date': date,
        'month': month,
        'report_month': month,
        'description': description,
        'amount': amount,
        'direction': direction,
        'category': category if direction == 'outgoing' else None,
        'source': 'statement',
        'fingerprint': f'fp|{tx_id}',
    }
    if bank_source:
        row['bank_source'] = bank_source
    return row


SAMPLE_TXS = [
    _tx('a', date='2024-01-05', description='COFFEE SHOP', amount=3.4, bank_source='Monzo'),
    _tx('b', date='2024-02-10', description='SUPERMARKET', amount=42.1, category='groceries', bank_source='Monzo'),
    _tx('c', date='2024-02-12', description='SALARY', amount=2800.0, direction='incoming', category=None, bank_source='Barclays'),
    _tx('d', date='2024-03-01', description='TRAIN TICKET', amount=18.5, category='transport', bank_source='Barclays'),
    _tx('e', date='2024-03-15', description='CASH MACHINE', amount=40.0, category='other'),
    _tx('f', date='2025-06-20', description='Coffee Beans', amount=12.0, category='groceries', bank_source='HSBC'),
]


class TestSearchSpendingTransactionsHelper(unittest.TestCase):
    def test_free_text_matches_description_case_insensitive(self):
        rows = app_mod._search_spending_transactions(SAMPLE_TXS, q='coffee')
        ids = [r['id'] for r in rows]
        self.assertEqual(ids, ['f', 'a'])

    def test_filters_by_date_range_and_direction(self):
        rows = app_mod._search_spending_transactions(
            SAMPLE_TXS,
            date_from='2024-02-01',
            date_to='2024-02-28',
            direction='incoming',
        )
        self.assertEqual([r['id'] for r in rows], ['c'])

    def test_filters_by_category_and_amount(self):
        rows = app_mod._search_spending_transactions(
            SAMPLE_TXS,
            category='groceries',
            min_amount=10,
            max_amount=50,
        )
        ids = [r['id'] for r in rows]
        self.assertEqual(ids, ['f', 'b'])

    def test_bank_source_none(self):
        rows = app_mod._search_spending_transactions(SAMPLE_TXS, bank_source='__none__')
        self.assertEqual([r['id'] for r in rows], ['e'])

    def test_sorted_newest_first(self):
        rows = app_mod._search_spending_transactions(SAMPLE_TXS, category='groceries')
        self.assertEqual([r['id'] for r in rows], ['f', 'b'])
        self.assertEqual([r['date'] for r in rows], ['2025-06-20', '2024-02-10'])


class TestTransactionSearchApi(unittest.TestCase):
    def setUp(self):
        self.client = app_mod.app.test_client()
        self.spending = {
            'transactions': list(SAMPLE_TXS),
            'statements': [],
            'monthly_insights': {
                '2024-01': {},
                '2024-02': {},
                '2024-03': {},
                '2025-06': {},
            },
            'classification_overrides': {},
            'classification_cache': {},
            'daily_budget': {},
        }
        self.data = {'users': {'ivan': {'spending': self.spending}}, 'loans': {}}

    def _login(self):
        with self.client.session_transaction() as sess:
            sess['username'] = 'ivan'

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_empty_without_criteria(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/api/spending/transactions/search')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertFalse(body['searched'])
        self.assertEqual(body['total'], 0)
        self.assertEqual(body['transactions'], [])

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_search_q_across_months(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/api/spending/transactions/search?q=coffee')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body['searched'])
        self.assertEqual(body['total'], 2)
        self.assertEqual([t['id'] for t in body['transactions']], ['f', 'a'])
        self.assertIn('Monzo', body['known_bank_sources'])

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_pagination(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/api/spending/transactions/search?direction=outgoing&limit=2&offset=0')
        body = resp.get_json()
        self.assertEqual(body['total'], 5)
        self.assertEqual(len(body['transactions']), 2)
        resp2 = self.client.get('/api/spending/transactions/search?direction=outgoing&limit=2&offset=2')
        body2 = resp2.get_json()
        self.assertEqual(len(body2['transactions']), 2)
        ids = {t['id'] for t in body['transactions']} | {t['id'] for t in body2['transactions']}
        self.assertEqual(len(ids), 4)

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_requires_login(self, load_mock, save_mock):
        load_mock.return_value = self.data
        resp = self.client.get('/api/spending/transactions/search?q=coffee')
        self.assertIn(resp.status_code, (302, 401))


class TestTransactionSearchPage(unittest.TestCase):
    def setUp(self):
        self.client = app_mod.app.test_client()
        self.data = {
            'users': {
                'ivan': {
                    'spending': {
                        'transactions': list(SAMPLE_TXS),
                        'statements': [{'bank_source': 'Monzo'}],
                        'monthly_insights': {},
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
    def test_search_page_markup(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/spending/search')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="tx-search-form"', html)
        self.assertIn('id="tx-search-q"', html)
        self.assertIn('id="tx-search-results-table"', html)
        self.assertIn('spending_search.js', html)
        self.assertIn('Search transactions', html)
        self.assertIn('href="/spending/search"', html)
        self.assertIn('app-nav-active', html)

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_nav_includes_search_on_other_pages(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        for path in ('/', '/spending', '/spending/daily', '/loans'):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)
            html = resp.get_data(as_text=True)
            self.assertIn('href="/spending/search"', html)

    def test_assets_present(self):
        self.assertIn('grid-template-columns: repeat(5, minmax(0, 1fr))', STYLE)
        self.assertIn('.search-q-input', STYLE)
        self.assertIn('search-tx-row', STYLE)
        self.assertIn('search-tx-row--open', STYLE)
        self.assertIn('body.search-page .search-tx-tbody tr.search-tx-row', STYLE)
        self.assertIn('/api/spending/transactions/search', SEARCH_JS)
        self.assertIn('tx-search-tbody', SEARCH_JS)
        self.assertIn('search-tx-row', SEARCH_JS)
        self.assertIn('toggleRow', SEARCH_JS)
        self.assertIn('aria-expanded', SEARCH_JS)
        self.assertIn('data-label', SEARCH_JS)
        self.assertIn('formatShortDate', SEARCH_JS)
        self.assertIn('search-tx-date-short', SEARCH_JS)
        self.assertIn('search-tx-date-short', STYLE)

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_search_page_has_expand_hint(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/spending/search')
        html = resp.get_data(as_text=True)
        self.assertIn('id="tx-search-expand-hint"', html)
        self.assertIn('Tap a row for details', html)
        self.assertIn('search-tx-toggle-col', html)


if __name__ == '__main__':
    unittest.main()
