"""Boundary integration tests for statement ↔ manual fuzzy matching."""
import copy
import unittest
from datetime import datetime, timedelta

import app as app_mod


def _tx(**kwargs):
    base = {
        'id': kwargs.get('id', 'm-boundary'),
        'date': kwargs.get('date', '2024-01-10'),
        'month': kwargs.get('date', '2024-01-10')[:7],
        'report_month': kwargs.get('date', '2024-01-10')[:7],
        'description': kwargs.get('description', 'Coffee shop'),
        'amount': kwargs.get('amount', 10.0),
        'direction': kwargs.get('direction', 'outgoing'),
        'category': kwargs.get('category', 'dining'),
        'source': kwargs.get('source', 'manual'),
    }
    for k, v in kwargs.items():
        base[k] = v
    return base


def _shift_date(iso_date: str, days: int) -> str:
    d = datetime.strptime(iso_date, '%Y-%m-%d').date()
    return (d + timedelta(days=days)).strftime('%Y-%m-%d')


def _fuzzy_match(spending: dict, *, manual_date: str, stmt_date: str, manual_amt: float, stmt_amt: float,
                 description: str = 'COSTA COFFEE CAMBRIDGE', direction: str = 'outgoing'):
    return app_mod._daily_budget_fuzzy_match_manual(
        spending,
        date_str=stmt_date,
        amount=stmt_amt,
        description=description,
        direction=direction,
    )


def _preview_match(spending: dict, *, stmt_date: str, stmt_amt: float,
                   description: str = 'COSTA COFFEE CAMBRIDGE', direction: str = 'outgoing') -> str | None:
    rows = [{
        'date': stmt_date,
        'amount': stmt_amt,
        'direction': direction,
        'description': description,
    }]
    report_month = stmt_date[:7]
    app_mod._apply_spending_preview_duplicate_marks(report_month, rows, spending)
    if rows[0].get('preview_duplicate') and rows[0].get('preview_duplicate_reason') == 'manual':
        return 'manual'
    if rows[0].get('preview_review_reason') == 'missed':
        return 'missed'
    return None


def _import_outcome(spending: dict, *, stmt_date: str, stmt_amt: float,
                    description: str = 'COSTA COFFEE CAMBRIDGE', direction: str = 'outgoing') -> str:
    spending = copy.deepcopy(spending)
    row = {
        'date': stmt_date,
        'amount': stmt_amt,
        'description': description,
        'direction': direction,
        'category': 'dining',
    }
    report_month = stmt_date[:7]
    fp = app_mod._spending_fingerprint(report_month, row['date'], row['amount'], row['direction'], row['description'])
    manual = _fuzzy_match(
        spending,
        manual_date=spending['transactions'][0]['date'],
        stmt_date=stmt_date,
        manual_amt=spending['transactions'][0]['amount'],
        stmt_amt=stmt_amt,
        description=description,
        direction=direction,
    )
    if manual is not None:
        app_mod._daily_budget_claim_manual_match(manual, row, 'stmt-boundary', fp)
        return 'claimed'
    return 'inserted'


# Each case: category, label, manual overrides, statement fields, expect_match, notes
BOUNDARY_CASES = [
    # Date boundaries (amount fixed at £10.00)
    ('date', 'same day', {'date': '2024-01-10'}, {'date': '2024-01-10'}, True, 'Δ0'),
    ('date', '+1 day (stmt after manual)', {'date': '2024-01-10'}, {'date': '2024-01-11'}, True, 'Δ+1'),
    ('date', '+2 days', {'date': '2024-01-10'}, {'date': '2024-01-12'}, True, 'Δ+2'),
    ('date', '+3 days (max slack)', {'date': '2024-01-10'}, {'date': '2024-01-13'}, True, 'Δ+3 boundary'),
    ('date', '+4 days (over slack)', {'date': '2024-01-10'}, {'date': '2024-01-14'}, False, 'Δ+4 rejected'),
    ('date', '−1 day (stmt before manual)', {'date': '2024-01-10'}, {'date': '2024-01-09'}, False, 'backward rejected'),
    ('date', '−3 days (stmt before manual)', {'date': '2024-01-10'}, {'date': '2024-01-07'}, False, 'backward rejected'),
    # Amount boundaries (same date)
    ('amount', 'exact', {'amount': 10.0}, {'amount': 10.0}, True, '£0.00'),
    ('amount', '£0.15 drift (max tol)', {'amount': 10.0}, {'amount': 10.15}, True, 'primary tol boundary'),
    ('amount', '£0.16 drift (over tol)', {'amount': 10.0}, {'amount': 10.16}, False, 'over £0.15'),
    ('amount', '£0.02 penny rounding', {'amount': 10.0}, {'amount': 10.02}, True, 'penny slack'),
    ('amount', '£0.03 (within primary tol)', {'amount': 9.97}, {'amount': 10.0}, True, 'within £0.15'),
    ('amount', '1% relative (at limit)', {'amount': 100.0}, {'amount': 101.01}, True, '1.00% of max amount'),
    ('amount', '1% relative (over limit)', {'amount': 100.0}, {'amount': 101.02}, False, '1.01% of max amount'),
    ('amount', 'old loose tol (£0.35)', {'amount': 10.0}, {'amount': 10.35}, False, 'would match old £0.50 rule'),
    ('amount', 'large drift (£5)', {'amount': 10.0}, {'amount': 15.0}, False, 'clear mismatch'),
    # Guards
    ('guard', 'direction mismatch', {'direction': 'outgoing'}, {'direction': 'incoming'}, False, 'incoming stmt'),
    ('guard', 'already bank_matched', {'bank_matched': True}, {}, False, 'skip reconciled'),
    ('guard', 'ambiguous same day/amount', {
        'extra_manuals': [_tx(id='m2', date='2024-01-10', amount=10.0, description='Other shop', source='manual')],
    }, {}, False, 'two manuals, no label tie-break'),
    # Combined realistic
    ('combo', 'HSBC-style +3d + tip', {'date': '2024-03-11', 'amount': 9.97}, {'date': '2024-03-14', 'amount': 10.05}, True, 'late post + small tip'),
    ('combo', 'HSBC-style +3d + large tip', {'date': '2024-03-11', 'amount': 9.97}, {'date': '2024-03-14', 'amount': 10.35}, False, 'late post but amount too far'),
    ('combo', 'early manual log, on-time stmt', {'date': '2024-06-01', 'amount': 25.0}, {'date': '2024-06-01', 'amount': 25.0}, True, 'same-day grocery'),
]


def _build_spending(manual_overrides: dict) -> dict:
    extra = manual_overrides.pop('extra_manuals', [])
    manual = _tx(id='m-boundary', **{k: v for k, v in manual_overrides.items() if k != 'extra_manuals'})
    return {'transactions': [manual, *extra], 'daily_budget': {}, 'statements': []}


def _evaluate_case(case: tuple) -> dict:
    category, label, manual_overrides, stmt_overrides, expect_match, notes = case
    manual_kwargs = dict(manual_overrides)
    spending = _build_spending(manual_kwargs)
    manual = spending['transactions'][0]
    stmt_date = stmt_overrides.get('date', manual['date'])
    stmt_amt = float(stmt_overrides.get('amount', manual['amount']))
    direction = stmt_overrides.get('direction', manual.get('direction', 'outgoing'))
    description = stmt_overrides.get('description', 'COSTA COFFEE CAMBRIDGE')

    fuzzy = _fuzzy_match(
        spending,
        manual_date=manual['date'],
        stmt_date=stmt_date,
        manual_amt=float(manual['amount']),
        stmt_amt=stmt_amt,
        description=description,
        direction=direction,
    )
    fuzzy_ok = (fuzzy is not None) == expect_match

    preview = _preview_match(spending, stmt_date=stmt_date, stmt_amt=stmt_amt, description=description, direction=direction)
    preview_ok = (preview == 'manual') == expect_match

    import_ok = True
    import_outcome = '—'
    if category != 'guard' or label in ('already bank_matched', 'ambiguous same day/amount'):
        if label == 'already bank_matched':
            import_ok = True  # N/A — import should not claim
            import_outcome = 'skipped'
        elif label == 'ambiguous same day/amount':
            import_ok = _import_outcome(spending, stmt_date=stmt_date, stmt_amt=stmt_amt) == 'inserted'
            import_outcome = 'inserted'
        else:
            outcome = _import_outcome(spending, stmt_date=stmt_date, stmt_amt=stmt_amt, description=description, direction=direction)
            import_outcome = outcome
            import_ok = (outcome == 'claimed') == expect_match
    else:
        import_outcome = 'n/a'

    return {
        'category': category,
        'label': label,
        'manual_date': manual['date'],
        'stmt_date': stmt_date,
        'manual_amt': float(manual['amount']),
        'stmt_amt': stmt_amt,
        'expect': 'match' if expect_match else 'no match',
        'fuzzy': 'match' if fuzzy else 'no match',
        'preview': preview or 'no match',
        'import': import_outcome,
        'fuzzy_ok': fuzzy_ok,
        'preview_ok': preview_ok,
        'import_ok': import_ok,
        'pass': fuzzy_ok and preview_ok and import_ok,
        'notes': notes,
    }


def generate_boundary_report() -> str:
    rows = [_evaluate_case(c) for c in BOUNDARY_CASES]
    passed = sum(1 for r in rows if r['pass'])
    total = len(rows)
    lines = [
        f'**Boundary integration: {passed}/{total} cases passed**',
        '',
        '| Category | Scenario | Manual | Statement | Expected | Fuzzy | Preview | Import | Pass | Notes |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |',
    ]
    for r in rows:
        manual = f"{r['manual_date']}, £{r['manual_amt']:.2f}"
        stmt = f"{r['stmt_date']}, £{r['stmt_amt']:.2f}"
        tick = '✅' if r['pass'] else '❌'
        lines.append(
            f"| {r['category']} | {r['label']} | {manual} | {stmt} | {r['expect']} "
            f"| {r['fuzzy']} | {r['preview']} | {r['import']} | {tick} | {r['notes']} |"
        )
    return '\n'.join(lines)


class TestManualMatchBoundaryIntegration(unittest.TestCase):
    def test_boundary_matrix(self):
        failures = []
        for case in BOUNDARY_CASES:
            with self.subTest(case=case[1]):
                result = _evaluate_case(case)
                if not result['pass']:
                    failures.append(
                        f"{result['label']}: fuzzy={result['fuzzy_ok']} preview={result['preview_ok']} import={result['import_ok']}"
                    )
        self.assertEqual(failures, [], 'Boundary failures:\n' + '\n'.join(failures))

    def test_date_helper_slack_constants(self):
        self.assertEqual(app_mod.DAILY_BUDGET_MANUAL_MATCH_DATE_SLACK_DAYS, 3)
        self.assertEqual(app_mod.DAILY_BUDGET_MANUAL_MATCH_AMOUNT_TOL, 0.15)

    def test_manual_match_dates_close_boundaries(self):
        base = '2024-01-10'
        for delta in range(0, 4):
            self.assertTrue(
                app_mod._manual_match_dates_close(base, _shift_date(base, delta)),
                f'+{delta} should match',
            )
        self.assertFalse(app_mod._manual_match_dates_close(base, _shift_date(base, 4)))
        self.assertFalse(app_mod._manual_match_dates_close(base, _shift_date(base, -1)))

    def test_manual_match_amounts_close_boundaries(self):
        self.assertTrue(app_mod._manual_match_amounts_close(10.0, 10.15))
        self.assertFalse(app_mod._manual_match_amounts_close(10.0, 10.16))
        self.assertTrue(app_mod._manual_match_amounts_close(10.0, 10.02))
        self.assertTrue(app_mod._manual_match_amounts_close(100.0, 101.01))
        self.assertFalse(app_mod._manual_match_amounts_close(100.0, 101.02))
        self.assertFalse(app_mod._manual_match_amounts_close(10.0, 10.35))


if __name__ == '__main__':
    print(generate_boundary_report())
