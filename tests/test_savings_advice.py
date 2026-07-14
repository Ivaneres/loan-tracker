"""Savings advice context and validation (no LLM calls)."""
import unittest
from unittest import mock

import app as app_mod


def _minimal_insight(month: str, income: float, outgoing: float, top_cats: list[dict] | None = None) -> dict:
    net = round(income - outgoing, 2)
    sr = round((net / income) * 100, 2) if income > 0 else None
    return {
        'month': month,
        'income_total': income,
        'outgoing_total': outgoing,
        'net': net,
        'savings_rate': sr,
        'top_categories': top_cats
        or [{'category': 'groceries', 'amount': round(outgoing * 0.3, 2)}],
    }


class TestSavingsAdviceContext(unittest.TestCase):
    def test_trend_row_compact_top3(self):
        ins = {
            'income_total': 3000.0,
            'outgoing_total': 1000.0,
            'net': 2000.0,
            'savings_rate': 66.67,
            'top_categories': [
                {'category': 'rent', 'amount': 500.0},
                {'category': 'food', 'amount': 100.0},
                {'category': 'bus', 'amount': 20.0},
                {'category': 'other', 'amount': 5.0},
            ],
        }
        row = app_mod._savings_trend_row_compact(ins, '2024-03')
        self.assertEqual(row['month'], '2024-03')
        self.assertEqual(len(row['top_categories']), 3)
        self.assertEqual(row['top_categories'][0]['category'], 'rent')

    def test_build_context_missing_focal(self):
        spending = {'monthly_insights': {'2024-01': _minimal_insight('2024-01', 100, 50)}}
        ctx, n, err, _meta = app_mod._build_savings_advice_context(spending, '2024-99')
        self.assertIsNone(ctx)
        self.assertEqual(n, 0)
        self.assertEqual(err, 'no_insight')

    def test_build_context_trend_order_newest_priors_capped_oldest_first(self):
        """Priors are months strictly before focal; series is chronological (oldest first)."""
        spending = {
            'monthly_insights': {
                '2023-10': _minimal_insight('2023-10', 1000, 400),
                '2023-11': _minimal_insight('2023-11', 1000, 500),
                '2024-01': _minimal_insight('2024-01', 1000, 600),
            },
        }
        ctx, n, err, _meta = app_mod._build_savings_advice_context(spending, '2024-01')
        self.assertIsNone(err)
        self.assertEqual(n, 2)
        self.assertEqual(ctx['focal_month'], '2024-01')
        series = ctx['trend_series']
        self.assertEqual(len(series), 2)
        self.assertEqual(series[0]['month'], '2023-10')
        self.assertEqual(series[1]['month'], '2023-11')
        self.assertEqual(ctx['trend_months_included'], 2)
        # focal insight is present and deep-copied
        self.assertIn('income_total', ctx['insight_focal'])
        self.assertIsNot(ctx['insight_focal'], spending['monthly_insights']['2024-01'])

    def test_focal_mutation_does_not_alias(self):
        spending = {'monthly_insights': {'2024-01': _minimal_insight('2024-01', 100, 50)}}
        ctx, _, _, _ = app_mod._build_savings_advice_context(spending, '2024-01')
        ctx['insight_focal']['income_total'] = 99999
        self.assertEqual(spending['monthly_insights']['2024-01']['income_total'], 100)

    def test_focal_month_includes_serialized_transactions(self):
        spending = {
            'monthly_insights': {'2024-01': _minimal_insight('2024-01', 100, 50)},
            'transactions': [
                {
                    'id': '1',
                    'date': '2024-01-05',
                    'report_month': '2024-01',
                    'month': '2024-01',
                    'description': 'COFFEE',
                    'amount': 20.0,
                    'direction': 'outgoing',
                },
                {
                    'id': '2',
                    'date': '2024-01-10',
                    'report_month': '2024-01',
                    'month': '2024-01',
                    'description': 'BREAD',
                    'amount': 5.0,
                    'direction': 'outgoing',
                },
            ],
        }
        ctx, _, err, _ = app_mod._build_savings_advice_context(spending, '2024-01')
        self.assertIsNone(err)
        self.assertEqual(len(ctx['focal_month_transactions']), 2)
        self.assertFalse(ctx['focal_transaction_meta']['truncated'])
        descs = {x['description'] for x in ctx['focal_month_transactions']}
        self.assertIn('COFFEE', descs)
        self.assertIn('BREAD', descs)

    def test_focal_tx_truncation_keeps_largest_magnitudes(self):
        with mock.patch.object(app_mod, 'SAVINGS_ADVICE_FOCAL_TX_MAX', 2):
            txs = []
            for i in range(4):
                txs.append({
                    'id': str(i),
                    'date': f'2024-01-0{i + 1}',
                    'report_month': '2024-01',
                    'month': '2024-01',
                    'description': f'T{i}',
                    'amount': float(10 * (i + 1)),
                    'direction': 'outgoing',
                })
            spending = {
                'monthly_insights': {'2024-01': _minimal_insight('2024-01', 100, 200)},
                'transactions': txs,
            }
            ctx, _, err, _ = app_mod._build_savings_advice_context(spending, '2024-01')
        self.assertIsNone(err)
        self.assertTrue(ctx['focal_transaction_meta']['truncated'])
        self.assertEqual(ctx['focal_transaction_meta']['count_in_month'], 4)
        self.assertEqual(len(ctx['focal_month_transactions']), 2)
        amts = {abs(t['amount']) for t in ctx['focal_month_transactions']}
        self.assertEqual(amts, {40.0, 30.0})

    def test_prior_month_includes_line_samples(self):
        spending = {
            'monthly_insights': {
                '2023-12': _minimal_insight('2023-12', 100, 20),
                '2024-01': _minimal_insight('2024-01', 100, 50),
            },
            'transactions': [
                {
                    'id': 'a',
                    'date': '2023-12-15',
                    'report_month': '2023-12',
                    'description': 'OLD',
                    'amount': 10.0,
                    'direction': 'outgoing',
                },
                {
                    'id': 'b',
                    'date': '2024-01-10',
                    'report_month': '2024-01',
                    'description': 'NEW',
                    'amount': 5.0,
                    'direction': 'outgoing',
                },
            ],
        }
        ctx, _, err, txm = app_mod._build_savings_advice_context(spending, '2024-01')
        self.assertIsNone(err)
        self.assertEqual(len(ctx['prior_month_transaction_samples']), 1)
        self.assertEqual(ctx['prior_month_transaction_samples'][0]['month'], '2023-12')
        self.assertEqual(len(ctx['prior_month_transaction_samples'][0]['transactions']), 1)
        self.assertEqual(ctx['prior_month_transaction_samples'][0]['transactions'][0]['description'], 'OLD')
        self.assertEqual(txm.get('prior_transaction_months_included'), 1)


class TestSavingsAdviceValidation(unittest.TestCase):
    def test_validate_good(self):
        d = {
            'summary': 'Test.',
            'recommendations': [
                {'title': 'A', 'detail': 'B', 'priority': 1, 'evidence': 'x'},
            ],
        }
        v = app_mod._validate_savings_advice_payload(d)
        self.assertIsNotNone(v)
        self.assertEqual(v['recommendations'][0]['priority'], 1)

    def test_validate_rejects_empty_recs(self):
        d = {'summary': 'Only summary', 'recommendations': []}
        self.assertIsNone(app_mod._validate_savings_advice_payload(d))

    def test_priority_clamped(self):
        d = {
            'summary': 'S',
            'recommendations': [{'title': 'T', 'detail': 'D', 'priority': 99, 'evidence': ''}],
        }
        v = app_mod._validate_savings_advice_payload(d)
        self.assertEqual(v['recommendations'][0]['priority'], 3)
