"""Preview near-miss suggestions for missed statement rows (UI dismiss only)."""
import unittest
from datetime import date

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


class TestManualMatchSuggestions(unittest.TestCase):
    def test_suggests_near_miss_beyond_auto_match_tol(self):
        spending = {
            'transactions': [
                _manual_tx(id='m1', date='2024-02-08', amount=9.5, description='Lunch with Sam'),
            ],
        }
        # +4 days and £0.85 tip — too far for auto-match, within suggest window
        suggestions = app_mod._daily_budget_suggest_manual_matches(
            spending,
            date_str='2024-02-12',
            amount=10.35,
            description='PRET A MANGER LONDON BRIDGE',
            direction='outgoing',
        )
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]['id'], 'm1')
        self.assertEqual(suggestions[0]['date_delta_days'], 4)
        self.assertEqual(suggestions[0]['amount_delta'], 0.85)

        match = app_mod._daily_budget_fuzzy_match_manual(
            spending,
            date_str='2024-02-12',
            amount=10.35,
            description='PRET A MANGER LONDON BRIDGE',
            direction='outgoing',
        )
        self.assertIsNone(match)

    def test_suggest_limit_and_ranking(self):
        spending = {
            'transactions': [
                _manual_tx(id='far', date='2024-02-01', amount=10.0, description='Other'),
                _manual_tx(id='close', date='2024-02-10', amount=10.2, description='Costa'),
                _manual_tx(id='mid', date='2024-02-09', amount=11.0, description='Coffee'),
                _manual_tx(id='extra', date='2024-02-08', amount=12.0, description='Cafe'),
            ],
        }
        suggestions = app_mod._daily_budget_suggest_manual_matches(
            spending,
            date_str='2024-02-10',
            amount=10.0,
            description='COSTA COFFEE',
            direction='outgoing',
            limit=3,
        )
        self.assertLessEqual(len(suggestions), 3)
        self.assertEqual(suggestions[0]['id'], 'close')

    def test_preview_missed_row_includes_suggestions(self):
        spending = {
            'transactions': [
                _manual_tx(id='m1', date='2024-02-08', amount=9.5, description='Lunch with Sam'),
                _manual_tx(id='m2', date='2024-02-14', amount=48.0, description='Weekly shop'),
            ],
        }
        rows = [
            {
                'date': '2024-02-12',
                'amount': 10.35,
                'direction': 'outgoing',
                'description': 'PRET A MANGER',
            },
            {
                'date': '2024-02-14',
                'amount': 48.0,
                'direction': 'outgoing',
                'description': 'TESCO STORES',
            },
        ]
        led, _, _, _ = app_mod._apply_spending_preview_duplicate_marks('2024-02', rows, spending)
        self.assertEqual(led, 1)
        self.assertEqual(rows[1]['preview_duplicate_reason'], 'manual')
        self.assertEqual(rows[0]['preview_review_reason'], 'missed')
        suggestions = rows[0].get('preview_manual_suggestions') or []
        self.assertTrue(any(s['id'] == 'm1' for s in suggestions))
        self.assertEqual(rows[1].get('preview_manual_suggestions'), [])

    def test_suggest_excludes_already_bank_matched(self):
        spending = {
            'transactions': [
                _manual_tx(id='m1', date='2024-02-08', amount=9.5, bank_matched=True),
            ],
        }
        suggestions = app_mod._daily_budget_suggest_manual_matches(
            spending,
            date_str='2024-02-10',
            amount=9.5,
            description='Lunch',
            direction='outgoing',
        )
        self.assertEqual(suggestions, [])

    def test_suggests_exact_netted_multi_manual(self):
        spending = {
            'transactions': [
                _manual_tx(id='m1', date='2024-03-10', amount=9.5, description='Lunch with Sam'),
                _manual_tx(id='m2', date='2024-03-10', amount=15.5, description='Coffee run'),
                _manual_tx(id='m3', date='2024-03-09', amount=8.0, description='Snack'),
            ],
        }
        # Statement £25.00 = £9.50 + £15.50 exactly; no single near £25
        suggestions = app_mod._daily_budget_suggest_manual_matches(
            spending,
            date_str='2024-03-12',
            amount=25.0,
            description='CARD PAYMENT MERCHANT',
            direction='outgoing',
        )
        netted = [s for s in suggestions if s.get('kind') == 'netted']
        self.assertTrue(netted, suggestions)
        top = netted[0]
        self.assertEqual(sorted(top['ids']), ['m1', 'm2'])
        self.assertEqual(top['amount'], 25.0)
        self.assertEqual(top['amount_delta'], 0.0)
        self.assertEqual(len(top['parts']), 2)

    def test_netted_requires_exact_sum(self):
        spending = {
            'transactions': [
                _manual_tx(id='m1', date='2024-03-10', amount=9.5, description='A'),
                _manual_tx(id='m2', date='2024-03-10', amount=15.4, description='B'),
            ],
        }
        # £9.50 + £15.40 = £24.90 ≠ £25.00
        suggestions = app_mod._daily_budget_suggest_manual_matches(
            spending,
            date_str='2024-03-12',
            amount=25.0,
            description='CARD PAYMENT',
            direction='outgoing',
        )
        self.assertFalse(any(s.get('kind') == 'netted' for s in suggestions), suggestions)

    def test_netted_three_parts(self):
        spending = {
            'transactions': [
                _manual_tx(id='a', date='2024-04-01', amount=5.0, description='A'),
                _manual_tx(id='b', date='2024-04-01', amount=7.0, description='B'),
                _manual_tx(id='c', date='2024-04-02', amount=8.0, description='C'),
            ],
        }
        suggestions = app_mod._daily_budget_suggest_manual_matches(
            spending,
            date_str='2024-04-03',
            amount=20.0,
            description='SHOP',
            direction='outgoing',
        )
        netted = [s for s in suggestions if s.get('kind') == 'netted']
        self.assertTrue(netted)
        self.assertEqual(sorted(netted[0]['ids']), ['a', 'b', 'c'])


class TestUnmatchedManualsSidebar(unittest.TestCase):
    def test_lists_month_manuals_excluding_bank_matched(self):
        spending = {
            'transactions': [
                _manual_tx(id='m1', date='2024-02-08', amount=10.0, description='Keep'),
                _manual_tx(id='m2', date='2024-02-09', amount=5.0, description='Done', bank_matched=True),
                _manual_tx(id='m3', date='2024-01-15', amount=3.0, description='Other month'),
            ],
        }
        rows = app_mod._spending_unmatched_manuals(spending, '2024-02')
        self.assertEqual([r['id'] for r in rows], ['m1'])

    def test_preview_summary_excludes_auto_claimed(self):
        spending = {
            'transactions': [
                _manual_tx(id='m1', date='2024-02-10', amount=12.0, description='Exact match'),
                _manual_tx(id='m2', date='2024-02-11', amount=7.5, description='Still open'),
            ],
            'outgoing_classification_cache': {},
        }
        data = {'users': {}}
        period = {
            'report_month': '2024-02',
            'period_start': '2024-02-01',
            'period_end': '2024-02-29',
            'period_start_date': date(2024, 2, 1),
            'period_end_date': date(2024, 2, 29),
        }
        raw = [
            {
                'date': '2024-02-11',
                'description': 'BANK REF EXACT',
                'amount': 12.0,
                'direction': 'outgoing',
            },
            {
                'date': '2024-02-12',
                'description': 'UNKNOWN SHOP',
                'amount': 20.0,
                'direction': 'outgoing',
            },
        ]
        result = app_mod._spending_statement_preview_finalize(
            data, spending, period, [], {'format': 'csv'}, False, raw, None,
        )
        ids = [m['id'] for m in result['summary']['unmatched_manuals']]
        self.assertEqual(ids, ['m2'])
        self.assertEqual(result['transactions'][0]['preview_duplicate_reason'], 'manual')

    def test_home_assets_mention_unclaimed_sidebar(self):
        root = __import__('pathlib').Path(__file__).resolve().parents[1]
        home = (root / 'templates' / 'home.html').read_text(encoding='utf-8')
        js = (root / 'static' / 'spending.js').read_text(encoding='utf-8')
        css = (root / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertIn('import-unclaimed-manuals', home)
        self.assertIn('unmatched_manuals', js)
        self.assertIn('renderUnclaimedManuals', js)
        self.assertIn('import-unclaimed-manuals', css)
        self.assertIn('position: fixed', css)
        self.assertIn('50% + 560px', css)
