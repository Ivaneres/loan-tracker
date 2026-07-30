"""
End-to-end integration cases for monthly recurring bill detection.

Exercises ``POST /api/spending/daily/plan/from-statements`` (Daily tab pull)
with realistic multi-month ledgers. Print a markdown results table:

    python3 -m tests.test_recurring_detection_e2e
"""
from __future__ import annotations

import io
import os
import sys
import traceback
import unittest
from dataclasses import dataclass, field
from typing import Callable
from unittest import mock

import app as app_mod


def _tx(
    *,
    id: str,
    date: str,
    amount: float,
    description: str,
    direction: str = 'outgoing',
    category: str | None = 'other',
    report_month: str | None = None,
    **extra,
) -> dict:
    rm = report_month or date[:7]
    row = {
        'id': id,
        'date': date,
        'month': rm,
        'report_month': rm,
        'description': description,
        'amount': amount,
        'direction': direction,
        'category': category,
        'source': 'statement',
    }
    row.update(extra)
    return row


def _salary(month: str, amount: float = 2800.0) -> dict:
    return _tx(
        id=f'sal-{month}',
        date=f'{month}-25',
        amount=amount,
        description='ACME PAYROLL',
        direction='incoming',
        category=None,
        report_month=month,
    )


@dataclass
class CaseResult:
    case_id: str
    title: str
    expect: str
    outcome: str  # PASS / FAIL / ERROR
    detail: str = ''
    bill_labels: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


@dataclass
class Case:
    case_id: str
    title: str
    expect: str
    build_transactions: Callable[[], list[dict]]
    focal_month: str
    check: Callable[[dict], None]
    income_total: float = 2800.0


def _labels(estimate: dict) -> list[str]:
    return [str(b.get('label') or '') for b in (estimate.get('bill_items') or [])]


def _items_matching(estimate: dict, needle: str) -> list[dict]:
    n = needle.lower()
    return [
        b for b in (estimate.get('bill_items') or [])
        if n in str(b.get('label') or '').lower()
    ]


def _require_label(estimate: dict, needle: str) -> dict:
    items = _items_matching(estimate, needle)
    if not items:
        raise AssertionError(f'{needle!r} missing from bill_items={_labels(estimate)}')
    return items[0]


def _forbid_subscription(estimate: dict, needle: str) -> None:
    bad = [
        b for b in (estimate.get('bill_items') or [])
        if b.get('source') == 'subscription_signal'
        and needle.lower() in str(b.get('label') or '').lower()
    ]
    if bad:
        raise AssertionError(f'{needle!r} wrongly flagged as subscription_signal: {bad}')


def _forbid_label(estimate: dict, needle: str) -> None:
    if _items_matching(estimate, needle):
        raise AssertionError(f'{needle!r} should not appear in bill_items={_labels(estimate)}')


def _build_cases() -> list[Case]:
    cases: list[Case] = []

    def add(case_id, title, expect, txs_fn, focal, check, income=2800.0):
        cases.append(Case(case_id, title, expect, txs_fn, focal, check, income))

    # --- Should detect -------------------------------------------------
    def check_01(est):
        item = _require_label(est, 'spotify')
        if item.get('source') != 'subscription_signal':
            raise AssertionError(f"expected subscription_signal, got {item.get('source')}")
        if abs(float(item.get('amount') or 0) - 10.99) > 0.01:
            raise AssertionError(f"amount {item.get('amount')}")

    add(
        'E2E-01',
        'Stable subscription (same day/amount)',
        'SPOTIFY appears as subscription_signal',
        lambda: [
            _salary('2024-01'), _salary('2024-02'), _salary('2024-03'),
            _tx(id='s1', date='2024-01-05', amount=10.99, description='SPOTIFY', category='other'),
            _tx(id='s2', date='2024-02-05', amount=10.99, description='SPOTIFY', category='other'),
            _tx(id='s3', date='2024-03-05', amount=10.99, description='SPOTIFY', category='other'),
        ],
        '2024-03',
        check_01,
    )

    add(
        'E2E-02',
        'Utility amount drift (~35%)',
        'OCTOPUS ENERGY detected despite amount spread',
        lambda: [
            _salary('2024-01'), _salary('2024-02'), _salary('2024-03'),
            _tx(id='e1', date='2024-01-12', amount=70.0, description='OCTOPUS ENERGY', category='other'),
            _tx(id='e2', date='2024-02-14', amount=95.0, description='OCTOPUS ENERGY', category='other'),
            _tx(id='e3', date='2024-03-13', amount=100.0, description='OCTOPUS ENERGY', category='other'),
        ],
        '2024-03',
        lambda est: _require_label(est, 'octopus'),
    )

    add(
        'E2E-03',
        'Month-boundary posting dates',
        'COUNCIL TAX Jan 30 → Mar 1 still detected',
        lambda: [
            _salary('2024-01'), _salary('2024-03'),
            _tx(id='c1', date='2024-01-30', amount=120.0, description='COUNCIL TAX', category='other'),
            _tx(id='c2', date='2024-03-01', amount=122.0, description='COUNCIL TAX', category='other'),
        ],
        '2024-03',
        lambda est: _require_label(est, 'council'),
    )

    add(
        'E2E-04',
        'Day-of-month drift (± a few days)',
        'DISNEY PLUS detected across 28/26/29',
        lambda: [
            _salary('2024-01'), _salary('2024-02'), _salary('2024-03'),
            _tx(id='d1', date='2024-01-28', amount=15.99, description='DISNEY PLUS', category='other'),
            _tx(id='d2', date='2024-02-26', amount=15.99, description='DISNEY PLUS', category='other'),
            _tx(id='d3', date='2024-03-29', amount=15.99, description='DISNEY PLUS', category='other'),
        ],
        '2024-03',
        lambda est: _require_label(est, 'disney'),
    )

    def check_05(est):
        items = _items_matching(est, 'spotify')
        if len(items) != 1:
            raise AssertionError(f'expected 1 spotify item, got {items}')

    add(
        'E2E-05',
        'Description variants merge',
        'SPOTIFY / SPOTIFY LDN merge into one recurring line',
        lambda: [
            _salary('2024-01'), _salary('2024-02'),
            _tx(id='a', date='2024-01-20', amount=9.99, description='SPOTIFY LDN', category='other'),
            _tx(id='b', date='2024-02-20', amount=9.99, description='SPOTIFY', category='other'),
        ],
        '2024-02',
        check_05,
    )

    def check_06(est):
        item = _require_label(est, 'lettings')
        if item.get('source') != 'category':
            raise AssertionError(f"expected category, got {item.get('source')}")

    add(
        'E2E-06',
        'Bill-category rent still rolls up',
        'ACME LETTINGS from housing category (source=category)',
        lambda: [
            _salary('2024-03'),
            _tx(
                id='r1', date='2024-03-01', amount=1100.0,
                description='ACME LETTINGS', category='housing',
            ),
        ],
        '2024-03',
        check_06,
    )

    add(
        'E2E-07',
        'Skipped month with matching DOM',
        'GYM DD Jan→Mar (Feb missing) still qualifies via DOM skip',
        lambda: [
            _salary('2024-01'), _salary('2024-03'),
            _tx(id='g1', date='2024-01-10', amount=29.0, description='GYM DD', category='other'),
            _tx(id='g2', date='2024-03-10', amount=29.0, description='GYM DD', category='other'),
        ],
        '2024-03',
        lambda est: _require_label(est, 'gym'),
    )

    add(
        'E2E-08',
        'Visa/bank noise stripped for merge',
        'VISA NETFLIX LONDON matches NETFLIX.COM across months',
        lambda: [
            _salary('2024-01'), _salary('2024-02'),
            _tx(id='n1', date='2024-01-08', amount=15.99, description='NETFLIX.COM', category='other'),
            _tx(
                id='n2', date='2024-02-08', amount=15.99,
                description='VISA NETFLIX LONDON', category='other',
            ),
        ],
        '2024-02',
        lambda est: _require_label(est, 'netflix'),
    )

    # --- Should reject / not over-detect --------------------------------
    add(
        'E2E-09',
        'Noisy grocery merchant',
        'TESCO STORE not flagged as subscription_signal',
        lambda: [
            _salary('2024-01'), _salary('2024-02'),
            *[
                _tx(
                    id=f't1{i}', date=f'2024-01-{d:02d}', amount=20.0 + i * 3,
                    description='TESCO STORE', category='groceries',
                )
                for i, d in enumerate((3, 7, 12, 18, 22, 27))
            ],
            *[
                _tx(
                    id=f't2{i}', date=f'2024-02-{d:02d}', amount=18.0 + i * 4,
                    description='TESCO STORE', category='groceries',
                )
                for i, d in enumerate((4, 9, 15, 19, 24, 28))
            ],
        ],
        '2024-02',
        lambda est: _forbid_subscription(est, 'tesco'),
    )

    add(
        'E2E-10',
        'One-off focal-month purchase',
        'Single AMAZON MARKETPLACE charge not a subscription',
        lambda: [
            _salary('2024-03'),
            _tx(
                id='a1', date='2024-03-15', amount=42.0,
                description='AMAZON MARKETPLACE', category='shopping',
            ),
        ],
        '2024-03',
        lambda est: _forbid_subscription(est, 'amazon'),
    )

    add(
        'E2E-11',
        'Two coffees, dissimilar days',
        'COSTA Jan 3 + Feb 22 not treated as monthly bill',
        lambda: [
            _salary('2024-01'), _salary('2024-02'),
            _tx(id='c1', date='2024-01-03', amount=4.5, description='COSTA COFFEE', category='dining'),
            _tx(id='c2', date='2024-02-22', amount=4.5, description='COSTA COFFEE', category='dining'),
        ],
        '2024-02',
        lambda est: _forbid_label(est, 'costa'),
    )

    add(
        'E2E-12',
        'Amount variance too high',
        'PHONE CO £20 → £80 rejected as recurring',
        lambda: [
            _salary('2024-01'), _salary('2024-02'), _salary('2024-03'),
            _tx(id='p1', date='2024-01-09', amount=20.0, description='PHONE CO', category='other'),
            _tx(id='p2', date='2024-02-09', amount=50.0, description='PHONE CO', category='other'),
            _tx(id='p3', date='2024-03-09', amount=80.0, description='PHONE CO', category='other'),
        ],
        '2024-03',
        lambda est: _forbid_subscription(est, 'phone'),
    )

    add(
        'E2E-13',
        'Paired internal transfers excluded',
        'Linked transfer legs do not become bills',
        lambda: [
            _salary('2024-01'), _salary('2024-02'),
            _tx(
                id='o1', date='2024-01-15', amount=200.0, description='TO SAVINGS',
                category='other', internal_transfer=True, transfer_pair_id='p1',
            ),
            _tx(
                id='i1', date='2024-01-15', amount=200.0, description='FROM CURRENT',
                direction='incoming', category=None,
                internal_transfer=True, transfer_pair_id='p1',
            ),
            _tx(
                id='o2', date='2024-02-15', amount=200.0, description='TO SAVINGS',
                category='other', internal_transfer=True, transfer_pair_id='p2',
            ),
            _tx(
                id='i2', date='2024-02-15', amount=200.0, description='FROM CURRENT',
                direction='incoming', category=None,
                internal_transfer=True, transfer_pair_id='p2',
            ),
        ],
        '2024-02',
        lambda est: _forbid_label(est, 'savings'),
    )

    def check_14(est):
        if len(_items_matching(est, 'prime')) < 1:
            raise AssertionError('AMAZON PRIME missing')
        merged = [
            b for b in (est.get('bill_items') or [])
            if 'amazon' in str(b.get('label') or '').lower()
            and abs(float(b.get('amount') or 0) - 13.99) < 0.01
        ]
        if merged:
            raise AssertionError(f'Amazon+Prime wrongly merged: {merged}')

    add(
        'E2E-14',
        'Amazon vs Amazon Prime stay separate',
        'Different same-month amounts do not merge; Prime can still signal',
        lambda: [
            _salary('2024-01'), _salary('2024-02'),
            _tx(id='a1', date='2024-01-05', amount=5.0, description='AMAZON', category='shopping'),
            _tx(id='p1', date='2024-01-06', amount=8.99, description='AMAZON PRIME', category='other'),
            _tx(id='a2', date='2024-02-05', amount=5.0, description='AMAZON', category='shopping'),
            _tx(id='p2', date='2024-02-06', amount=8.99, description='AMAZON PRIME', category='other'),
        ],
        '2024-02',
        check_14,
    )

    # --- API / plan wiring ---------------------------------------------
    def check_15(est):
        if abs(float(est.get('income_monthly') or 0) - 2750.0) > 0.01:
            raise AssertionError(f"income {est.get('income_monthly')}")

    add(
        'E2E-15',
        'Income seeded from statement month',
        'estimate.income_monthly matches payroll total',
        lambda: [
            _salary('2024-03', 2750.0),
            _tx(id='s1', date='2024-01-05', amount=10.0, description='NETFLIX', category='other'),
            _tx(id='s2', date='2024-02-05', amount=10.0, description='NETFLIX', category='other'),
            _tx(id='s3', date='2024-03-05', amount=10.0, description='NETFLIX', category='other'),
            _salary('2024-01', 2750.0),
            _salary('2024-02', 2750.0),
        ],
        '2024-03',
        check_15,
        income=2750.0,
    )

    def check_16(est):
        items = _items_matching(est, 'spotify')
        if len(items) != 1:
            raise AssertionError(f'duplicate spotify lines: {items}')
        if items[0].get('source') != 'category':
            raise AssertionError('expected category source for categorized SPOTIFY')

    add(
        'E2E-16',
        'Category + signal no double count',
        'Already-categorized SPOTIFY not duplicated as subscription_signal',
        lambda: [
            _salary('2024-01'), _salary('2024-02'), _salary('2024-03'),
            _tx(id='s1', date='2024-01-05', amount=10.99, description='SPOTIFY', category='subscriptions'),
            _tx(id='s2', date='2024-02-05', amount=10.99, description='SPOTIFY', category='subscriptions'),
            _tx(id='s3', date='2024-03-05', amount=10.99, description='SPOTIFY', category='subscriptions'),
        ],
        '2024-03',
        check_16,
    )

    def check_17(est):
        _require_label(est, 'lettings')
        _require_label(est, 'octopus')
        _require_label(est, 'youtube')
        _forbid_label(est, 'sainsbury')
        _forbid_label(est, 'uber')

    add(
        'E2E-17',
        'Mixed ledger: rent + utilities + streaming',
        'Pull returns housing category + flexible energy signal + streaming',
        lambda: [
            _salary('2024-01'), _salary('2024-02'), _salary('2024-03'),
            _tx(id='r3', date='2024-03-01', amount=1200.0, description='ACME LETTINGS', category='housing'),
            _tx(id='e1', date='2024-01-12', amount=72.0, description='OCTOPUS ENERGY', category='other'),
            _tx(id='e2', date='2024-02-15', amount=88.0, description='OCTOPUS ENERGY', category='other'),
            _tx(id='e3', date='2024-03-14', amount=91.0, description='OCTOPUS ENERGY', category='other'),
            _tx(id='s1', date='2024-01-07', amount=6.99, description='YOUTUBE PREMIUM', category='other'),
            _tx(id='s2', date='2024-02-07', amount=6.99, description='YOUTUBE PREMIUM', category='other'),
            _tx(id='s3', date='2024-03-07', amount=6.99, description='YOUTUBE PREMIUM', category='other'),
            _tx(id='n1', date='2024-03-10', amount=35.0, description='SAINSBURY', category='groceries'),
            _tx(id='n2', date='2024-03-18', amount=12.0, description='UBER TRIP', category='transport'),
        ],
        '2024-03',
        check_17,
    )

    return cases


def _run_case_via_api(case: Case) -> CaseResult:
    client = app_mod.app.test_client()
    txs = case.build_transactions()
    spending = {
        'transactions': txs,
        'statements': [],
        'monthly_insights': {
            case.focal_month: {'income_total': case.income_total},
        },
        'daily_budget': {
            'plan': {
                'income_monthly': 0,
                'bills_monthly': 0,
                'savings_percent': 0,
                'daily_mode': 'fixed',
                'bill_items': [],
            },
            'goals': [],
        },
    }
    data = {'users': {'ivan': {'spending': spending}}, 'loans': {}}

    try:
        with mock.patch.object(app_mod, 'load_data', return_value=data):
            with mock.patch.object(app_mod, 'save_data'):
                with mock.patch.object(app_mod, '_llm_flag_regular_bills', return_value=[]):
                    with client.session_transaction() as sess:
                        sess['username'] = 'ivan'
                    resp = client.post(
                        '/api/spending/daily/plan/from-statements',
                        json={'month': case.focal_month, 'use_llm': False, 'apply': False},
                    )
        if resp.status_code != 200:
            return CaseResult(
                case.case_id, case.title, case.expect, 'FAIL',
                detail=f'HTTP {resp.status_code}: {resp.get_json()}',
            )
        body = resp.get_json() or {}
        if not body.get('ok'):
            return CaseResult(
                case.case_id, case.title, case.expect, 'FAIL',
                detail=f'ok=false: {body}',
            )
        estimate = body.get('estimate') or {}
        case.check(estimate)
        return CaseResult(
            case.case_id,
            case.title,
            case.expect,
            'PASS',
            detail='ok',
            bill_labels=_labels(estimate),
            sources=[str(b.get('source') or '') for b in (estimate.get('bill_items') or [])],
        )
    except AssertionError as e:
        return CaseResult(
            case.case_id, case.title, case.expect, 'FAIL', detail=str(e) or 'assertion failed',
        )
    except Exception as e:
        return CaseResult(
            case.case_id, case.title, case.expect, 'ERROR',
            detail=f'{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}',
        )


def run_all_cases() -> list[CaseResult]:
    return [_run_case_via_api(c) for c in _build_cases()]


def results_markdown_table(results: list[CaseResult]) -> str:
    lines = [
        '| Case | Scenario | Expected | Result | Notes |',
        '| --- | --- | --- | --- | --- |',
    ]
    reject_hints = (
        'not', 'reject', 'noise', 'One-off', 'coffees', 'transfers', 'variance', 'grocery',
    )
    for r in results:
        notes = (r.detail or '').replace('|', '\\|').replace('\n', ' ')
        if r.outcome == 'PASS' and r.bill_labels:
            short = ', '.join(r.bill_labels[:4])
            if len(r.bill_labels) > 4:
                short += f', +{len(r.bill_labels) - 4} more'
            notes = f'bills: {short}'
        elif r.outcome == 'PASS':
            if any(h.lower() in (r.title + ' ' + r.expect).lower() for h in reject_hints):
                notes = 'no bill lines (as expected)'
            else:
                notes = 'ok'
        lines.append(
            f'| `{r.case_id}` | {r.title} | {r.expect} | **{r.outcome}** | {notes} |'
        )
    passed = sum(1 for r in results if r.outcome == 'PASS')
    failed = sum(1 for r in results if r.outcome != 'PASS')
    lines.append('')
    lines.append(
        f'**Summary:** {passed}/{len(results)} passed'
        + (f', {failed} failed' if failed else '')
        + '.'
    )
    return '\n'.join(lines)


class TestRecurringDetectionE2E(unittest.TestCase):
    """unittest wrapper so CI / ``python -m unittest`` picks these up."""

    @classmethod
    def setUpClass(cls):
        cls.results = run_all_cases()
        cls._by_id = {r.case_id: r for r in cls.results}

    def _assert_case(self, case_id: str):
        r = self._by_id[case_id]
        if r.outcome != 'PASS':
            self.fail(f'{case_id} {r.outcome}: {r.detail}')

    def test_e2e_01_stable_subscription(self):
        self._assert_case('E2E-01')

    def test_e2e_02_utility_amount_drift(self):
        self._assert_case('E2E-02')

    def test_e2e_03_month_boundary(self):
        self._assert_case('E2E-03')

    def test_e2e_04_day_of_month_drift(self):
        self._assert_case('E2E-04')

    def test_e2e_05_description_variants(self):
        self._assert_case('E2E-05')

    def test_e2e_06_housing_category_rollup(self):
        self._assert_case('E2E-06')

    def test_e2e_07_skipped_month_dom(self):
        self._assert_case('E2E-07')

    def test_e2e_08_bank_noise_merge(self):
        self._assert_case('E2E-08')

    def test_e2e_09_noisy_grocery(self):
        self._assert_case('E2E-09')

    def test_e2e_10_one_off_purchase(self):
        self._assert_case('E2E-10')

    def test_e2e_11_dissimilar_day_coffees(self):
        self._assert_case('E2E-11')

    def test_e2e_12_high_amount_variance(self):
        self._assert_case('E2E-12')

    def test_e2e_13_internal_transfers(self):
        self._assert_case('E2E-13')

    def test_e2e_14_amazon_prime_separate(self):
        self._assert_case('E2E-14')

    def test_e2e_15_income_seeded(self):
        self._assert_case('E2E-15')

    def test_e2e_16_no_double_count(self):
        self._assert_case('E2E-16')

    def test_e2e_17_mixed_realistic_ledger(self):
        self._assert_case('E2E-17')

    def test_e2e_18_apply_persists_plan(self):
        """apply=true writes bill_items onto the daily budget plan."""
        client = app_mod.app.test_client()
        txs = [
            _salary('2024-02'), _salary('2024-03'),
            _tx(id='s1', date='2024-02-05', amount=10.99, description='SPOTIFY', category='other'),
            _tx(id='s2', date='2024-03-05', amount=10.99, description='SPOTIFY', category='other'),
        ]
        spending = {
            'transactions': txs,
            'statements': [],
            'monthly_insights': {'2024-03': {'income_total': 2800.0}},
            'daily_budget': {
                'plan': {
                    'income_monthly': 0,
                    'bills_monthly': 0,
                    'savings_percent': 10,
                    'daily_mode': 'fixed',
                    'bill_items': [],
                },
                'goals': [],
            },
        }
        data = {'users': {'ivan': {'spending': spending}}, 'loans': {}}
        with mock.patch.object(app_mod, 'load_data', return_value=data):
            with mock.patch.object(app_mod, 'save_data') as save_mock:
                with mock.patch.object(app_mod, '_llm_flag_regular_bills', return_value=[]):
                    with mock.patch.object(app_mod, '_daily_budget_status', return_value={'ok': True}):
                        with client.session_transaction() as sess:
                            sess['username'] = 'ivan'
                        resp = client.post(
                            '/api/spending/daily/plan/from-statements',
                            json={'month': '2024-03', 'use_llm': False, 'apply': True},
                        )
        self.assertEqual(resp.status_code, 200, resp.get_json())
        body = resp.get_json()
        self.assertTrue(body.get('ok'))
        plan = body.get('plan') or {}
        self.assertTrue(any('SPOTIFY' in str(b.get('label') or '') for b in (plan.get('bill_items') or [])))
        self.assertEqual(plan.get('source_month'), '2024-03')
        self.assertGreater(float(plan.get('bills_monthly') or 0), 0)
        save_mock.assert_called()


def main(argv: list[str] | None = None) -> int:
    results = run_all_cases()
    suite = unittest.defaultTestLoader.loadTestsFromName(
        'tests.test_recurring_detection_e2e.TestRecurringDetectionE2E.test_e2e_18_apply_persists_plan'
    )
    buf = io.StringIO()
    runner = unittest.TextTestRunner(stream=buf, verbosity=0)
    apply_result = runner.run(suite)
    apply_outcome = 'PASS' if apply_result.wasSuccessful() else 'FAIL'
    apply_detail = 'plan.bill_items persisted' if apply_outcome == 'PASS' else buf.getvalue().strip()
    results.append(CaseResult(
        'E2E-18',
        'apply=true persists daily plan',
        'bill_items + source_month saved on plan',
        apply_outcome,
        detail=apply_detail,
        bill_labels=[],
    ))

    table = results_markdown_table(results)
    out_path = 'tmp/recurring_detection_e2e_results.md'
    os.makedirs('tmp', exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('# Recurring detection E2E results\n\n')
        f.write('Endpoint: `POST /api/spending/daily/plan/from-statements` (`use_llm: false`).\n\n')
        f.write(table)
        f.write('\n')

    print(table)
    print(f'\nWrote {out_path}', file=sys.stderr)
    return 0 if all(r.outcome == 'PASS' for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
