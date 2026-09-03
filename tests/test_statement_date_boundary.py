"""Started vs completed date resolution and period-boundary tagging for statement import."""
import unittest
from datetime import date
from unittest import mock

import app as app_mod


class TestResolveSpendingTransactionDates(unittest.TestCase):
    def test_prefers_completed_over_started_and_explicit_date(self):
        primary, started, completed = app_mod._resolve_spending_transaction_dates({
            'date': '2024-06-30',
            'started_date': '2024-06-30',
            'completed_date': '2024-07-01',
        })
        self.assertEqual(primary, date(2024, 7, 1))
        self.assertEqual(started, date(2024, 6, 30))
        self.assertEqual(completed, date(2024, 7, 1))

    def test_revolut_style_header_keys(self):
        primary, started, completed = app_mod._resolve_spending_transaction_dates({
            'Started Date': '30/06/2024',
            'Completed Date': '01/07/2024',
        })
        self.assertEqual(primary, date(2024, 7, 1))
        self.assertEqual(started, date(2024, 6, 30))
        self.assertEqual(completed, date(2024, 7, 1))

    def test_falls_back_to_explicit_date_when_no_completed(self):
        primary, started, completed = app_mod._resolve_spending_transaction_dates({
            'date': '2024-07-15',
            'started_date': '2024-07-14',
        })
        self.assertEqual(primary, date(2024, 7, 15))
        self.assertEqual(started, date(2024, 7, 14))
        self.assertIsNone(completed)

    def test_falls_back_to_started_when_only_started(self):
        primary, started, completed = app_mod._resolve_spending_transaction_dates({
            'started_date': '2024-07-02',
        })
        self.assertEqual(primary, date(2024, 7, 2))
        self.assertEqual(started, date(2024, 7, 2))
        self.assertIsNone(completed)

    def test_missing_dates_return_none(self):
        primary, started, completed = app_mod._resolve_spending_transaction_dates({})
        self.assertIsNone(primary)
        self.assertIsNone(started)
        self.assertIsNone(completed)


class TestNormalizeSpendingTransactionsDates(unittest.TestCase):
    def test_normalize_uses_completed_as_ledger_date(self):
        rows = app_mod._normalize_spending_transactions([{
            'date': '2024-06-30',
            'started_date': '2024-06-30',
            'completed_date': '2024-07-01',
            'description': 'CARD PAYMENT',
            'amount': 12.5,
            'direction': 'outgoing',
        }])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['date'], '2024-07-01')
        self.assertEqual(rows[0]['month'], '2024-07')
        self.assertEqual(rows[0]['started_date'], '2024-06-30')
        self.assertEqual(rows[0]['completed_date'], '2024-07-01')
        self.assertFalse(rows[0]['date_boundary'])


class TestFilterSpendingRowsByPeriodBoundary(unittest.TestCase):
    def test_keeps_and_tags_started_outside_completed_inside(self):
        rows = [
            {
                'date': '2024-07-01',
                'started_date': '2024-06-30',
                'completed_date': '2024-07-01',
                'description': 'Cross-month',
                'amount': 10.0,
                'direction': 'outgoing',
            },
            {
                'date': '2024-07-10',
                'started_date': '2024-07-10',
                'completed_date': '2024-07-10',
                'description': 'Same-day',
                'amount': 5.0,
                'direction': 'outgoing',
            },
            {
                'date': '2024-06-28',
                'started_date': '2024-06-27',
                'completed_date': '2024-06-28',
                'description': 'Fully prior month',
                'amount': 3.0,
                'direction': 'outgoing',
            },
        ]
        kept, dropped, boundary = app_mod._filter_spending_rows_by_period(
            rows, date(2024, 7, 1), date(2024, 7, 31)
        )
        self.assertEqual(dropped, 1)
        self.assertEqual(boundary, 1)
        self.assertEqual(len(kept), 2)
        cross = next(r for r in kept if r['description'] == 'Cross-month')
        same = next(r for r in kept if r['description'] == 'Same-day')
        self.assertTrue(cross['date_boundary'])
        self.assertEqual(cross['date_boundary_reason'], 'started_outside')
        self.assertFalse(same['date_boundary'])

    def test_no_boundary_when_started_also_in_range(self):
        rows = [{
            'date': '2024-07-05',
            'started_date': '2024-07-04',
            'description': 'Both in July',
            'amount': 1.0,
            'direction': 'outgoing',
        }]
        kept, dropped, boundary = app_mod._filter_spending_rows_by_period(
            rows, date(2024, 7, 1), date(2024, 7, 31)
        )
        self.assertEqual(dropped, 0)
        self.assertEqual(boundary, 0)
        self.assertEqual(len(kept), 1)
        self.assertFalse(kept[0]['date_boundary'])

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, '_classify_outgoing_descriptions_llm', return_value={})
    def test_preview_finalize_exposes_boundary_count(self, _classify_mock, _save_mock):
        data = {'users': {}}
        spending = {
            'transactions': [],
            'statements': [],
            'classification_cache': {},
            'classification_overrides': {},
        }
        period = {
            'report_month': '2024-07',
            'period_start': '2024-07-01',
            'period_end': '2024-07-31',
            'period_start_date': date(2024, 7, 1),
            'period_end_date': date(2024, 7, 31),
        }
        raw = [
            {
                'date': '2024-06-30',
                'started_date': '2024-06-30',
                'completed_date': '2024-07-01',
                'description': 'Revolut pending settle',
                'amount': 22.0,
                'direction': 'outgoing',
            },
            {
                'date': '2024-06-15',
                'description': 'Old June spend',
                'amount': 9.0,
                'direction': 'outgoing',
            },
        ]
        result = app_mod._spending_statement_preview_finalize(
            data,
            spending,
            period,
            [],
            {'format': 'csv'},
            False,
            raw,
            None,
        )
        self.assertEqual(result['summary']['filtered_out_count'], 1)
        self.assertEqual(result['summary']['date_boundary_count'], 1)
        self.assertEqual(result['summary']['total_rows'], 1)
        tx = result['transactions'][0]
        self.assertEqual(tx['date'], '2024-07-01')
        self.assertTrue(tx['date_boundary'])
        self.assertEqual(tx['started_date'], '2024-06-30')


class TestBoundaryPreviewUiPresence(unittest.TestCase):
    def test_reconcile_range_and_assets_mention_boundary_handling(self):
        root = __import__('pathlib').Path(__file__).resolve().parents[1]
        rec = (root / 'templates' / 'reconcile.html').read_text(encoding='utf-8')
        js = (root / 'static' / 'spending.js').read_text(encoding='utf-8')
        css = (root / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertIn('id="reconcile-period-start"', rec)
        self.assertIn('id="reconcile-period-end"', rec)
        self.assertIn('Adjust date range', rec)
        self.assertIn('date_boundary', js)
        self.assertIn('preview-boundary-pill', js)
        self.assertIn('Boundary dates:', js)
        self.assertIn('preview-boundary-pill', css)
        self.assertIn('spending-preview-row-boundary', css)


if __name__ == '__main__':
    unittest.main()
