"""Daily Budget plan math, modes, underspend, and manual-import dedup."""
import unittest
from datetime import date
from unittest import mock

import app as app_mod


def _tx(**kwargs):
    base = {
        'id': kwargs.get('id', 'x'),
        'date': kwargs.get('date', '2024-01-10'),
        'month': '2024-01',
        'report_month': '2024-01',
        'description': kwargs.get('description', 'Test'),
        'amount': kwargs.get('amount', 0.0),
        'direction': kwargs.get('direction', 'outgoing'),
        'category': kwargs.get('category', 'dining'),
        'source': kwargs.get('source', 'manual'),
    }
    for k, v in kwargs.items():
        base[k] = v
    return base


class TestDailyBudgetPlanFigures(unittest.TestCase):
    def test_savings_percent_reduces_discretionary(self):
        figures = app_mod._daily_budget_plan_figures({
            'income_monthly': 3000,
            'bills_monthly': 1000,
            'savings_percent': 20,
            'daily_mode': 'fixed',
            'bill_items': [],
        })
        self.assertEqual(figures['savings_monthly'], 600.0)
        self.assertEqual(figures['discretionary_monthly'], 1400.0)

    def test_bill_items_override_bills_monthly(self):
        figures = app_mod._daily_budget_plan_figures({
            'income_monthly': 2000,
            'bills_monthly': 999,
            'savings_percent': 10,
            'bill_items': [
                {'label': 'Rent', 'amount': 800, 'included': True},
                {'label': 'Gym', 'amount': 40, 'included': False},
            ],
        })
        self.assertEqual(figures['bills_monthly'], 800.0)
        self.assertEqual(figures['savings_monthly'], 200.0)
        self.assertEqual(figures['discretionary_monthly'], 1000.0)


class TestDailyBudgetModes(unittest.TestCase):
    def test_fixed_limit_constant(self):
        figures = {
            'discretionary_monthly': 310.0,
            'daily_mode': 'fixed',
        }
        spend = {'2024-01-01': 5.0, '2024-01-02': 100.0}
        limits = app_mod._daily_budget_day_limits(figures, '2024-01', spend, date(2024, 1, 5))
        self.assertEqual(limits['2024-01-01'], 10.0)
        self.assertEqual(limits['2024-01-15'], 10.0)

    def test_envelope_tightens_after_overspend(self):
        figures = {
            'discretionary_monthly': 300.0,
            'daily_mode': 'envelope',
        }
        # Day 1: spend all 300 on first day → later days ~0
        spend = {'2024-01-01': 300.0}
        limits = app_mod._daily_budget_day_limits(figures, '2024-01', spend, date(2024, 1, 3))
        self.assertAlmostEqual(limits['2024-01-01'], round(300.0 / 31, 2))
        self.assertEqual(limits['2024-01-02'], 0.0)

    def test_carry_surplus_rolls_forward(self):
        figures = {
            'discretionary_monthly': 310.0,
            'daily_mode': 'carry_surplus',
        }
        spend = {'2024-01-01': 0.0}
        limits = app_mod._daily_budget_day_limits(figures, '2024-01', spend, date(2024, 1, 2))
        self.assertEqual(limits['2024-01-01'], 10.0)
        self.assertEqual(limits['2024-01-02'], 20.0)


class TestDailyBudgetStatus(unittest.TestCase):
    def test_bill_categories_ignored_in_daily_spend(self):
        spending = {
            'daily_budget': {
                'plan': {
                    'income_monthly': 3100,
                    'bills_monthly': 0,
                    'savings_percent': 0,
                    'daily_mode': 'fixed',
                    'bill_items': [],
                },
                'goals': [],
            },
            'transactions': [
                _tx(id='1', date='2024-01-15', amount=50, category='dining'),
                _tx(id='2', date='2024-01-15', amount=900, category='housing'),
            ],
            'monthly_insights': {},
        }
        status = app_mod._daily_budget_status(spending, as_of=date(2024, 1, 15))
        self.assertEqual(status['spent_today'], 50.0)
        self.assertEqual(status['daily_limit'], 100.0)
        self.assertEqual(status['remaining_today'], 50.0)

    def test_underspend_saved_sums_daily_leftover(self):
        spending = {
            'daily_budget': {
                'plan': {
                    'income_monthly': 310,
                    'bills_monthly': 0,
                    'savings_percent': 0,
                    'daily_mode': 'fixed',
                    'bill_items': [],
                },
                'goals': [],
            },
            'transactions': [
                _tx(id='1', date='2024-01-01', amount=2, category='dining'),
                _tx(id='2', date='2024-01-02', amount=2, category='dining'),
            ],
            'monthly_insights': {},
        }
        status = app_mod._daily_budget_status(spending, as_of=date(2024, 1, 2))
        # limit 10/day; leftover 8+8
        self.assertEqual(status['underspend_saved'], 16.0)


class TestManualImportDedup(unittest.TestCase):
    def test_fuzzy_match_manual_by_date_amount_and_label(self):
        spending = {
            'transactions': [
                _tx(
                    id='m1',
                    date='2024-01-10',
                    amount=12.5,
                    description='Costa',
                    source='manual',
                    category='dining',
                ),
            ],
        }
        match = app_mod._daily_budget_fuzzy_match_manual(
            spending,
            date_str='2024-01-10',
            amount=12.5,
            description='COSTA COFFEE CAMBRIDGE',
            direction='outgoing',
        )
        self.assertIsNotNone(match)
        self.assertEqual(match['id'], 'm1')

    def test_import_skips_and_claims_manual(self):
        spending = {
            'transactions': [
                _tx(
                    id='m1',
                    date='2024-02-05',
                    amount=4.5,
                    description='Coffee',
                    source='manual',
                    category='dining',
                    fingerprint='old',
                ),
            ],
            'statements': [],
            'monthly_insights': {},
            'daily_budget': {},
        }
        manual = spending['transactions'][0]
        row = {
            'date': '2024-02-05',
            'amount': 4.5,
            'description': 'COFFEE SHOP LTD',
            'direction': 'outgoing',
            'category': 'dining',
        }
        fp = app_mod._spending_fingerprint('2024-02', '2024-02-05', 4.5, 'outgoing', row['description'])
        app_mod._daily_budget_claim_manual_match(manual, row, 'stmt-1', fp)
        self.assertTrue(manual['bank_matched'])
        self.assertEqual(manual['fingerprint'], fp)
        self.assertEqual(manual['source_statement_id'], 'stmt-1')


class TestHybridBillsEstimate(unittest.TestCase):
    def test_category_rollup_without_llm(self):
        spending = {
            'transactions': [
                _tx(
                    id='1', date='2024-03-01', amount=1000, category='housing',
                    report_month='2024-03', month='2024-03', direction='outgoing',
                    source='statement', description='ACME LETTINGS',
                ),
                _tx(
                    id='2', date='2024-03-02', amount=80, category='utilities',
                    report_month='2024-03', month='2024-03', direction='outgoing',
                    source='statement', description='WATER CO',
                ),
                _tx(
                    id='3', date='2024-03-03', amount=2500, category=None,
                    report_month='2024-03', month='2024-03', direction='incoming',
                    source='statement', description='SALARY',
                ),
            ],
            'monthly_insights': {
                '2024-03': {'income_total': 2500.0},
            },
        }
        with mock.patch.object(app_mod, '_llm_flag_regular_bills', return_value=[]):
            est = app_mod._build_hybrid_bill_estimate(spending, '2024-03', use_llm=False)
        self.assertEqual(est['income_monthly'], 2500.0)
        self.assertEqual(est['bills_monthly'], 1080.0)
        labels = {b['label'] for b in est['bill_items']}
        self.assertIn('ACME LETTINGS', labels)
        self.assertIn('WATER CO', labels)


if __name__ == '__main__':
    unittest.main()
