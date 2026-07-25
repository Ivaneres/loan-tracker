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
        limits = app_mod._daily_budget_day_limits(
            figures, spend, date(2024, 1, 1), date(2024, 1, 31),
        )
        self.assertEqual(limits['2024-01-01'], 10.0)
        self.assertEqual(limits['2024-01-15'], 10.0)

    def test_envelope_tightens_after_overspend(self):
        figures = {
            'discretionary_monthly': 300.0,
            'daily_mode': 'envelope',
        }
        # Day 1: spend all 300 on first day → later days ~0
        spend = {'2024-01-01': 300.0}
        limits = app_mod._daily_budget_day_limits(
            figures, spend, date(2024, 1, 1), date(2024, 1, 31),
        )
        self.assertAlmostEqual(limits['2024-01-01'], round(300.0 / 31, 2))
        self.assertEqual(limits['2024-01-02'], 0.0)

    def test_carry_surplus_rolls_forward(self):
        figures = {
            'discretionary_monthly': 310.0,
            'daily_mode': 'carry_surplus',
        }
        spend = {'2024-01-01': 0.0}
        limits = app_mod._daily_budget_day_limits(
            figures, spend, date(2024, 1, 1), date(2024, 1, 31),
        )
        self.assertEqual(limits['2024-01-01'], 10.0)
        self.assertEqual(limits['2024-01-02'], 20.0)

    def test_envelope_mid_period_start_does_not_inflate_limit(self):
        """Without pacing_start, empty early days leave full pool ÷ days left.

        With mid-period pacing_start, assume average prior spend so join-day
        limit ≈ base daily (disc / days_in_period).
        """
        figures = {
            'discretionary_monthly': 310.0,
            'daily_mode': 'envelope',
        }
        spend = {}
        inflated = app_mod._daily_budget_day_limits(
            figures, spend, date(2024, 1, 1), date(2024, 1, 31),
        )
        self.assertEqual(inflated['2024-01-15'], round(310.0 / 17, 2))

        paced = app_mod._daily_budget_day_limits(
            figures, spend, date(2024, 1, 1), date(2024, 1, 31),
            pacing_start=date(2024, 1, 15),
        )
        self.assertEqual(paced['2024-01-14'], 0.0)
        self.assertEqual(paced['2024-01-15'], 10.0)

    def test_carry_surplus_mid_period_start_skips_phantom_roll(self):
        figures = {
            'discretionary_monthly': 310.0,
            'daily_mode': 'carry_surplus',
        }
        spend = {}
        limits = app_mod._daily_budget_day_limits(
            figures, spend, date(2024, 1, 1), date(2024, 1, 31),
            pacing_start=date(2024, 1, 15),
        )
        self.assertEqual(limits['2024-01-14'], 0.0)
        self.assertEqual(limits['2024-01-15'], 10.0)
        self.assertEqual(limits['2024-01-16'], 20.0)  # unused day 15 rolls once

    def test_pay_period_spans_month_boundary(self):
        # Payday 25: 25 Jan – 24 Feb = 31 days → £10/day on £310
        figures = {
            'discretionary_monthly': 310.0,
            'daily_mode': 'fixed',
        }
        limits = app_mod._daily_budget_day_limits(
            figures, {}, date(2024, 1, 25), date(2024, 2, 24),
        )
        self.assertEqual(limits['2024-01-25'], 10.0)
        self.assertEqual(limits['2024-02-01'], 10.0)
        self.assertEqual(limits['2024-02-24'], 10.0)
        self.assertNotIn('2024-01-24', limits)


class TestDailyBudgetPayPeriod(unittest.TestCase):
    def test_pay_day_1_is_calendar_month(self):
        start, end = app_mod._daily_budget_pay_period(date(2024, 1, 15), 1)
        self.assertEqual(start, date(2024, 1, 1))
        self.assertEqual(end, date(2024, 1, 31))

    def test_pay_day_25_current_and_previous_cycle(self):
        start, end = app_mod._daily_budget_pay_period(date(2024, 1, 30), 25)
        self.assertEqual(start, date(2024, 1, 25))
        self.assertEqual(end, date(2024, 2, 24))

        start, end = app_mod._daily_budget_pay_period(date(2024, 1, 10), 25)
        self.assertEqual(start, date(2023, 12, 25))
        self.assertEqual(end, date(2024, 1, 24))

    def test_pay_day_31_clamps_in_february(self):
        start, end = app_mod._daily_budget_pay_period(date(2024, 2, 29), 31)
        self.assertEqual(start, date(2024, 2, 29))  # leap year clamp
        self.assertEqual(end, date(2024, 3, 30))


class TestDailyBudgetTrackingFrom(unittest.TestCase):
    def test_pacing_start_clamps_within_period(self):
        plan = {'tracking_from': '2024-01-15'}
        self.assertEqual(
            app_mod._daily_budget_pacing_start(plan, date(2024, 1, 1), date(2024, 1, 31)),
            date(2024, 1, 15),
        )
        # Tracking from a previous period → start of this period
        self.assertEqual(
            app_mod._daily_budget_pacing_start(plan, date(2024, 2, 1), date(2024, 2, 29)),
            date(2024, 2, 1),
        )

    def test_status_excludes_pre_tracking_days_from_underspend(self):
        spending = {
            'daily_budget': {
                'plan': {
                    'income_monthly': 310,
                    'bills_monthly': 0,
                    'savings_percent': 0,
                    'daily_mode': 'fixed',
                    'pay_day': 1,
                    'tracking_from': '2024-01-15',
                    'bill_items': [],
                },
                'goals': [],
            },
            'transactions': [
                _tx(id='1', date='2024-01-15', amount=3, category='dining'),
            ],
            'monthly_insights': {},
        }
        status = app_mod._daily_budget_status(spending, as_of=date(2024, 1, 15))
        self.assertEqual(status['pacing_start'], '2024-01-15')
        self.assertEqual(status['period_start'], '2024-01-01')
        self.assertEqual(status['period_end'], '2024-01-31')
        self.assertEqual(status['daily_limit'], 10.0)
        self.assertEqual(status['remaining_today'], 7.0)
        # Only day 15 counts — not 14 phantom clear days × £10
        self.assertEqual(status['underspend_saved'], 7.0)
        self.assertEqual(status['day_insights']['days_elapsed'], 1)
        self.assertEqual(len(status['days']), 1)
        # Pro-rated remaining pool: 17/31 * 310 − 3
        self.assertEqual(
            status['discretionary_remaining_month'],
            round(310.0 * 17 / 31, 2) - 3.0,
        )

    def test_status_with_pay_day_includes_cross_month_spend(self):
        spending = {
            'daily_budget': {
                'plan': {
                    'income_monthly': 310,
                    'bills_monthly': 0,
                    'savings_percent': 0,
                    'daily_mode': 'fixed',
                    'pay_day': 25,
                    'bill_items': [],
                },
                'goals': [],
            },
            'transactions': [
                _tx(id='1', date='2024-01-26', amount=5, category='dining', month='2024-01'),
                _tx(id='2', date='2024-02-01', amount=7, category='dining', month='2024-02'),
            ],
            'monthly_insights': {},
        }
        status = app_mod._daily_budget_status(spending, as_of=date(2024, 2, 1))
        self.assertEqual(status['pay_day'], 25)
        self.assertEqual(status['period_start'], '2024-01-25')
        self.assertEqual(status['period_end'], '2024-02-24')
        self.assertEqual(status['days_in_period'], 31)
        self.assertEqual(status['daily_limit'], 10.0)
        self.assertEqual(status['spent_mtd'], 12.0)
        self.assertEqual(status['spent_today'], 7.0)


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

    def test_pace_projection_envelope_tightens_after_overspend(self):
        spending = {
            'daily_budget': {
                'plan': {
                    'income_monthly': 310,
                    'bills_monthly': 0,
                    'savings_percent': 0,
                    'daily_mode': 'envelope',
                    'pay_day': 1,
                    'bill_items': [],
                },
                'goals': [],
            },
            'transactions': [
                _tx(id='1', date='2024-01-01', amount=40, category='dining'),
                _tx(id='2', date='2024-01-02', amount=20, category='dining'),
            ],
            'monthly_insights': {},
        }
        status = app_mod._daily_budget_status(spending, as_of=date(2024, 1, 2))
        proj = status['pace_projection']
        self.assertEqual(proj['mode'], 'envelope')
        self.assertEqual(proj['window_pool'], 310.0)
        self.assertEqual(proj['spent_so_far'], 60.0)
        self.assertEqual(proj['remaining_after_today'], 250.0)
        self.assertEqual(proj['days_elapsed'], 2)
        self.assertEqual(proj['days_after_today'], 29)  # 3 Jan → 31 Jan
        self.assertEqual(proj['base_daily'], 10.0)
        self.assertEqual(proj['projected_daily'], round(250.0 / 29, 2))
        self.assertEqual(proj['projected_vs_base'], round(proj['projected_daily'] - 10.0, 2))
        self.assertEqual(proj['pace_target_spend'], 20.0)
        self.assertEqual(proj['pace_delta'], -40.0)  # 20 on-pace − 60 spent

    def test_pace_projection_fixed_keeps_base_daily(self):
        spending = {
            'daily_budget': {
                'plan': {
                    'income_monthly': 310,
                    'bills_monthly': 0,
                    'savings_percent': 0,
                    'daily_mode': 'fixed',
                    'pay_day': 1,
                    'bill_items': [],
                },
                'goals': [],
            },
            'transactions': [
                _tx(id='1', date='2024-01-01', amount=50, category='dining'),
            ],
            'monthly_insights': {},
        }
        status = app_mod._daily_budget_status(spending, as_of=date(2024, 1, 15))
        proj = status['pace_projection']
        self.assertEqual(proj['mode'], 'fixed')
        self.assertEqual(proj['base_daily'], 10.0)
        self.assertEqual(proj['projected_daily'], 10.0)
        self.assertEqual(proj['projected_vs_base'], 0.0)
        self.assertEqual(proj['spent_so_far'], 50.0)
        self.assertEqual(proj['remaining_after_today'], 260.0)
        self.assertEqual(proj['days_elapsed'], 15)
        self.assertEqual(proj['pace_target_spend'], 150.0)
        self.assertEqual(proj['pace_delta'], 100.0)

    def test_pace_projection_carry_reports_remaining_average(self):
        spending = {
            'daily_budget': {
                'plan': {
                    'income_monthly': 310,
                    'bills_monthly': 0,
                    'savings_percent': 0,
                    'daily_mode': 'carry_surplus',
                    'pay_day': 1,
                    'bill_items': [],
                },
                'goals': [],
            },
            'transactions': [
                _tx(id='1', date='2024-01-01', amount=0, category='dining'),
            ],
            'monthly_insights': {},
        }
        status = app_mod._daily_budget_status(spending, as_of=date(2024, 1, 2))
        proj = status['pace_projection']
        self.assertEqual(proj['mode'], 'carry_surplus')
        self.assertEqual(proj['carry_from_yesterday'], 10.0)
        self.assertEqual(proj['daily_limit'], 20.0)
        self.assertEqual(proj['spent_so_far'], 0.0)
        self.assertEqual(proj['remaining_after_today'], 310.0)
        self.assertEqual(proj['projected_daily'], round(310.0 / 29, 2))

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

    def test_day_insights_track_under_over_clear_and_streak(self):
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
                _tx(id='1', date='2024-01-01', amount=2, category='dining'),   # under, saved 8
                _tx(id='2', date='2024-01-02', amount=15, category='dining'),  # over by 5
                # 2024-01-03 clear
                _tx(id='3', date='2024-01-04', amount=1, category='dining'),   # under, saved 9 → best
            ],
            'monthly_insights': {},
        }
        status = app_mod._daily_budget_status(spending, as_of=date(2024, 1, 4))
        insights = status['day_insights']
        self.assertEqual(insights['days_elapsed'], 4)
        self.assertEqual(insights['days_over'], 1)
        self.assertEqual(insights['days_clear'], 1)
        self.assertEqual(insights['days_under'], 3)  # under + clear + under
        self.assertEqual(insights['under_streak'], 2)  # clear + under at end
        self.assertEqual(insights['overspend_total'], 5.0)
        self.assertEqual(insights['best_day']['date'], '2024-01-04')
        self.assertEqual(insights['best_day']['underspend'], 9.0)
        self.assertEqual(insights['worst_day']['date'], '2024-01-02')
        self.assertEqual(insights['worst_day']['overspend'], 5.0)

        by_date = {d['date']: d for d in status['days']}
        self.assertEqual(by_date['2024-01-01']['status'], 'under')
        self.assertEqual(by_date['2024-01-02']['status'], 'over')
        self.assertEqual(by_date['2024-01-03']['status'], 'clear')
        self.assertTrue(by_date['2024-01-04']['is_today'])


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


class TestDailyEntryCreate(unittest.TestCase):
    def setUp(self):
        self.client = app_mod.app.test_client()
        self.spending = {
            'transactions': [],
            'statements': [],
            'monthly_insights': {},
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
        }
        self.data = {'users': {'ivan': {'spending': self.spending}}, 'loans': {}}

    def _login(self):
        with self.client.session_transaction() as sess:
            sess['username'] = 'ivan'

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_allows_duplicate_title_on_same_day(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        payload = {
            'amount': 4.5,
            'title': 'Coffee',
            'category': 'dining',
            'date': '2024-01-10',
        }
        with mock.patch.object(app_mod, '_recompute_monthly_insights'):
            first = self.client.post('/api/spending/daily/entry', json=payload)
            second = self.client.post('/api/spending/daily/entry', json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(self.spending['transactions']), 2)
        self.assertEqual(self.spending['transactions'][0]['description'], 'Coffee')
        self.assertEqual(self.spending['transactions'][1]['description'], 'Coffee')
        self.assertNotEqual(
            self.spending['transactions'][0]['fingerprint'],
            self.spending['transactions'][1]['fingerprint'],
        )
        save_mock.assert_called()

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_creates_entry_for_past_date_and_returns_that_day_status(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        payload = {
            'amount': 12.0,
            'title': 'Lunch',
            'category': 'dining',
            'date': '2024-01-08',
        }
        with mock.patch.object(app_mod, '_recompute_monthly_insights'):
            resp = self.client.post('/api/spending/daily/entry', json=payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['transaction']['date'], '2024-01-08')
        self.assertEqual(body['status']['as_of'], '2024-01-08')
        self.assertEqual(body['status']['spent_today'], 12.0)
        self.assertEqual(self.spending['transactions'][0]['date'], '2024-01-08')

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_rejects_future_entry_date(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        future = (app_mod.date.today() + app_mod.timedelta(days=2)).strftime('%Y-%m-%d')
        resp = self.client.post(
            '/api/spending/daily/entry',
            json={'amount': 5, 'title': 'Soon', 'category': 'dining', 'date': future},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('future', (resp.get_json() or {}).get('error', '').lower())
        self.assertEqual(self.spending['transactions'], [])


class TestDailyPlanTrackingFromApi(unittest.TestCase):
    def setUp(self):
        self.client = app_mod.app.test_client()
        self.spending = {
            'transactions': [],
            'statements': [],
            'monthly_insights': {},
            'daily_budget': {
                'plan': {
                    'income_monthly': 0,
                    'bills_monthly': 0,
                    'savings_percent': 20,
                    'daily_mode': 'envelope',
                    'tracking_from': None,
                    'bill_items': [],
                    'updated_at': None,
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
    def test_first_save_defaults_tracking_from_to_today(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.put('/api/spending/daily/plan', json={
            'income_monthly': 3100,
            'savings_percent': 0,
            'daily_mode': 'envelope',
            'bill_items': [],
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['plan']['tracking_from'], date.today().isoformat())
        self.assertEqual(
            self.spending['daily_budget']['plan']['tracking_from'],
            date.today().isoformat(),
        )

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_explicit_tracking_from_and_clear(self, load_mock, save_mock):
        self.spending['daily_budget']['plan']['updated_at'] = '2024-01-01T00:00:00Z'
        load_mock.return_value = self.data
        self._login()
        resp = self.client.put('/api/spending/daily/plan', json={
            'tracking_from': '2024-01-10',
            'pay_day': 25,
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['plan']['tracking_from'], '2024-01-10')
        self.assertEqual(body['plan']['pay_day'], 25)

        resp2 = self.client.put('/api/spending/daily/plan', json={
            'tracking_from': None,
        })
        self.assertEqual(resp2.status_code, 200)
        self.assertIsNone(resp2.get_json()['plan']['tracking_from'])
        self.assertEqual(resp2.get_json()['plan']['pay_day'], 25)

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_rejects_invalid_pay_day(self, load_mock, save_mock):
        self.spending['daily_budget']['plan']['updated_at'] = '2024-01-01T00:00:00Z'
        load_mock.return_value = self.data
        self._login()
        resp = self.client.put('/api/spending/daily/plan', json={'pay_day': 40})
        self.assertEqual(resp.status_code, 400)


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
