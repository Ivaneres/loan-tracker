"""Thorough coverage of reconcile Find matches (`_reconcile_run_auto_match`).

Complements unit-level fuzzy boundaries in test_manual_match_boundaries.py by
exercising the full session algorithm: multi-pass singles, title tie-breaks,
netted manuals/banks, transfers, and skip guards.
"""
from __future__ import annotations

import unittest
from unittest import mock

import app as app_mod


def _manual(**kwargs):
    d = kwargs.get('date', '2024-07-10')
    base = {
        'id': kwargs.get('id', 'm1'),
        'date': d,
        'month': d[:7],
        'report_month': d[:7],
        'description': kwargs.get('description', 'Spend'),
        'amount': kwargs.get('amount', 10.0),
        'direction': kwargs.get('direction', 'outgoing'),
        'category': kwargs.get('category', 'dining'),
        'source': kwargs.get('source', 'manual'),
    }
    for k, v in kwargs.items():
        if k == 'source' and v is None:
            base.pop('source', None)
            continue
        base[k] = v
    return base


def _row(**kwargs):
    return {
        'id': kwargs.get('id', 'r1'),
        'date': kwargs.get('date', '2024-07-10'),
        'description': kwargs.get('description', 'BANK'),
        'direction': kwargs.get('direction', 'outgoing'),
        'amount': kwargs.get('amount', 10.0),
        'category': kwargs.get('category', 'dining'),
        'include': kwargs.get('include', True),
        'manual_match': kwargs.get('manual_match'),
        'transfer_pair': kwargs.get('transfer_pair'),
        'ledger_duplicate': kwargs.get('ledger_duplicate', False),
        'reconcile_mark': kwargs.get('reconcile_mark'),
    }


def _session(month: str, manuals: list, uploads: list) -> tuple[dict, dict]:
    spending = {
        'transactions': manuals,
        'reconcile_sessions': {},
    }
    sess = app_mod._reconcile_ensure_session(spending, month)
    sess['uploads'] = uploads
    return sess, spending


def _matched_ids(sess: dict) -> dict[str, list[str]]:
    out = {}
    for _, _, _, _, row in app_mod._reconcile_iter_rows(sess):
        mm = row.get('manual_match') or {}
        if mm.get('manual_ids'):
            out[str(row.get('id'))] = [str(x) for x in mm['manual_ids']]
    return out


def _transfer_pairs(sess: dict) -> set[tuple[str, str]]:
    pairs = set()
    for _, _, _, _, row in app_mod._reconcile_iter_rows(sess):
        tp = row.get('transfer_pair') or {}
        if not tp:
            continue
        a, b = str(row.get('id')), str(tp.get('peer_row_id') or '')
        pairs.add(tuple(sorted((a, b))))
    return pairs


class TestReconcileAutoMatchSingles(unittest.TestCase):
    def test_unique_date_amount_ignores_unrelated_titles(self):
        sess, spending = _session(
            '2024-07',
            [_manual(id='m1', date='2024-07-23', amount=3.10, description='Tube')],
            [{'id': 'u1', 'file_name': 'a.csv', 'bank_source': 'Amex', 'rows': [
                _row(id='r1', date='2024-07-24', amount=3.10,
                     description='TFL TRAVEL CHARGE TFL.GOV.UK/CP'),
            ]}],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['manual_auto_matches'], 1)
        self.assertEqual(_matched_ids(sess), {'r1': ['m1']})

    def test_date_slack_boundary_accepts_plus_three_rejects_plus_four(self):
        manuals = [_manual(id='m1', date='2024-07-10', amount=12.0, description='Shop')]
        ok_sess, ok_sp = _session(
            '2024-07', manuals,
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r-ok', date='2024-07-13', amount=12.0, description='TESCO'),
            ]}],
        )
        far_sess, far_sp = _session(
            '2024-07',
            [_manual(id='m1', date='2024-07-10', amount=12.0, description='Shop')],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r-far', date='2024-07-14', amount=12.0, description='TESCO'),
            ]}],
        )
        self.assertEqual(app_mod._reconcile_run_auto_match(ok_sess, ok_sp)['manual_auto_matches'], 1)
        self.assertEqual(app_mod._reconcile_run_auto_match(far_sess, far_sp)['manual_auto_matches'], 0)

    def test_statement_before_manual_never_auto_matches(self):
        sess, spending = _session(
            '2024-07',
            [_manual(id='m1', date='2024-07-10', amount=8.0, description='Lunch')],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r1', date='2024-07-09', amount=8.0, description='PRET'),
            ]}],
        )
        self.assertEqual(app_mod._reconcile_run_auto_match(sess, spending)['manual_auto_matches'], 0)

    def test_amount_must_be_exact(self):
        sess, spending = _session(
            '2024-07',
            [_manual(id='m1', date='2024-07-10', amount=10.0, description='Lunch')],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r1', date='2024-07-10', amount=10.02, description='PRET'),
            ]}],
        )
        self.assertEqual(app_mod._reconcile_run_auto_match(sess, spending)['manual_auto_matches'], 0)

    def test_skips_bank_matched_and_statement_sourced_ledger_rows(self):
        sess, spending = _session(
            '2024-07',
            [
                _manual(id='m-done', date='2024-07-10', amount=5.0, description='Old', bank_matched=True),
                _manual(id='m-bank', date='2024-07-10', amount=5.0, description='Ledger bank', source='statement'),
                _manual(id='m-ok', date='2024-07-11', amount=7.0, description='Fresh'),
            ],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r1', date='2024-07-10', amount=5.0, description='ANY'),
                _row(id='r2', date='2024-07-11', amount=7.0, description='ANY'),
            ]}],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['manual_auto_matches'], 1)
        self.assertEqual(_matched_ids(sess), {'r2': ['m-ok']})

    def test_legacy_empty_source_counts_as_manual(self):
        legacy = _manual(id='m-legacy', date='2024-07-10', amount=4.5, description='Coffee', source=None)
        sess, spending = _session(
            '2024-07',
            [legacy],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r1', date='2024-07-10', amount=4.5, description='COSTA'),
            ]}],
        )
        self.assertEqual(app_mod._reconcile_run_auto_match(sess, spending)['manual_auto_matches'], 1)
        self.assertEqual(_matched_ids(sess)['r1'], ['m-legacy'])

    def test_title_breaks_same_amount_collision(self):
        sess, spending = _session(
            '2024-07',
            [
                _manual(id='m-costa', date='2024-07-10', amount=4.5, description='Costa'),
                _manual(id='m-shell', date='2024-07-10', amount=4.5, description='Shell'),
            ],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r1', date='2024-07-10', amount=4.5, description='COSTA COFFEE CAMBRIDGE'),
            ]}],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['manual_auto_matches'], 1)
        self.assertEqual(_matched_ids(sess), {'r1': ['m-costa']})

    def test_weak_titles_leave_collision_unmatched(self):
        sess, spending = _session(
            '2024-07',
            [
                _manual(id='m-tube', date='2024-07-23', amount=3.10, description='Tube'),
                _manual(id='m-coffee', date='2024-07-22', amount=3.10, description='Coffee'),
            ],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r1', date='2024-07-24', amount=3.10,
                     description='TFL TRAVEL CHARGE TFL.GOV.UK/CP'),
            ]}],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['manual_auto_matches'], 0)
        self.assertEqual(_matched_ids(sess), {})

    def test_one_manual_not_claimed_by_two_bank_rows(self):
        sess, spending = _session(
            '2024-07',
            [_manual(id='m1', date='2024-07-10', amount=9.0, description='Lunch')],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r1', date='2024-07-10', amount=9.0, description='PRET A'),
                _row(id='r2', date='2024-07-11', amount=9.0, description='PRET B'),
            ]}],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['manual_auto_matches'], 1)
        matched = _matched_ids(sess)
        self.assertEqual(len(matched), 1)
        self.assertEqual(list(matched.values())[0], ['m1'])

    def test_skips_excluded_and_duplicate_rows(self):
        sess, spending = _session(
            '2024-07',
            [
                _manual(id='m1', date='2024-07-10', amount=6.0, description='A'),
                _manual(id='m2', date='2024-07-11', amount=7.0, description='B'),
            ],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r-skip', date='2024-07-10', amount=6.0, description='A', include=False),
                _row(id='r-dup', date='2024-07-11', amount=7.0, description='B DUP'),
                _row(id='r-ok', date='2024-07-11', amount=7.0, description='B BANK'),
            ]}],
        )
        real_apply = app_mod._reconcile_apply_duplicate_marks

        def apply_preserve_dup(sess_arg, spending_arg, report_month):
            real_apply(sess_arg, spending_arg, report_month)
            for _, _, _, _, row in app_mod._reconcile_iter_rows(sess_arg):
                if str(row.get('id')) == 'r-dup':
                    row['ledger_duplicate'] = True

        with mock.patch.object(app_mod, '_reconcile_apply_duplicate_marks', side_effect=apply_preserve_dup):
            stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['manual_auto_matches'], 1)
        self.assertEqual(_matched_ids(sess), {'r-ok': ['m2']})
        by_id = {r['id']: r for _, _, _, _, r in app_mod._reconcile_iter_rows(sess)}
        self.assertIsNone(by_id['r-skip'].get('manual_match'))
        self.assertIsNone(by_id['r-dup'].get('manual_match'))

class TestReconcileAutoMatchMultiPass(unittest.TestCase):
    def test_second_pass_after_unique_claim_unlocks_ambiguous_row(self):
        sess, spending = _session(
            '2024-07',
            [
                _manual(id='m-tube', date='2024-07-23', amount=3.10, description='Tube'),
                _manual(id='m-coffee', date='2024-07-22', amount=3.10, description='Coffee'),
            ],
            [
                {'id': 'u-amex', 'file_name': 'amex.csv', 'bank_source': 'Amex', 'rows': [
                    _row(id='r-tfl', date='2024-07-24', amount=3.10,
                         description='TFL TRAVEL CHARGE TFL.GOV.UK/CP'),
                ]},
                {'id': 'u-hsbc', 'file_name': 'hsbc.csv', 'bank_source': 'HSBC', 'rows': [
                    _row(id='r-costa', date='2024-07-22', amount=3.10, description='COSTA COFFEE'),
                ]},
            ],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['manual_auto_matches'], 2)
        self.assertEqual(_matched_ids(sess), {
            'r-tfl': ['m-tube'],
            'r-costa': ['m-coffee'],
        })

    def test_single_pass_cap_leaves_ambiguous_row_unmatched(self):
        sess, spending = _session(
            '2024-07',
            [
                _manual(id='m-tube', date='2024-07-23', amount=3.10, description='Tube'),
                _manual(id='m-coffee', date='2024-07-22', amount=3.10, description='Coffee'),
            ],
            [
                {'id': 'u-amex', 'file_name': 'amex.csv', 'rows': [
                    _row(id='r-tfl', date='2024-07-24', amount=3.10,
                         description='TFL TRAVEL CHARGE TFL.GOV.UK/CP'),
                ]},
                {'id': 'u-hsbc', 'file_name': 'hsbc.csv', 'rows': [
                    _row(id='r-costa', date='2024-07-22', amount=3.10, description='COSTA COFFEE'),
                ]},
            ],
        )
        with mock.patch.object(app_mod, 'RECONCILE_AUTO_MATCH_SINGLE_PASSES', 1):
            stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['manual_auto_matches'], 1)
        self.assertEqual(_matched_ids(sess), {'r-costa': ['m-coffee']})

    def test_three_pass_cascade_unlocks_chain(self):
        """Each bank row starts ambiguous; unique day-1 claim cascades through later days."""
        sess, spending = _session(
            '2024-07',
            [
                _manual(id='m-a', date='2024-07-01', amount=5.0, description='Alpha'),
                _manual(id='m-b', date='2024-07-02', amount=5.0, description='Beta'),
                _manual(id='m-c', date='2024-07-03', amount=5.0, description='Gamma'),
            ],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                # Sees all three manuals in the 3-day window → ambiguous until later passes.
                _row(id='r3', date='2024-07-03', amount=5.0, description='CARD 3'),
                _row(id='r2', date='2024-07-02', amount=5.0, description='CARD 2'),
                # Only m-a is on/before 01 Jul → unique unlock.
                _row(id='r1', date='2024-07-01', amount=5.0, description='CARD 1'),
            ]}],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['manual_auto_matches'], 3)
        self.assertEqual(_matched_ids(sess), {
            'r1': ['m-a'],
            'r2': ['m-b'],
            'r3': ['m-c'],
        })

    def test_unique_first_order_matches_in_one_pass(self):
        """Same data as the TFL case, but Costa first — no second pass needed."""
        sess, spending = _session(
            '2024-07',
            [
                _manual(id='m-tube', date='2024-07-23', amount=3.10, description='Tube'),
                _manual(id='m-coffee', date='2024-07-22', amount=3.10, description='Coffee'),
            ],
            [
                {'id': 'u-hsbc', 'file_name': 'hsbc.csv', 'rows': [
                    _row(id='r-costa', date='2024-07-22', amount=3.10, description='COSTA COFFEE'),
                ]},
                {'id': 'u-amex', 'file_name': 'amex.csv', 'rows': [
                    _row(id='r-tfl', date='2024-07-24', amount=3.10,
                         description='TFL TRAVEL CHARGE TFL.GOV.UK/CP'),
                ]},
            ],
        )
        with mock.patch.object(app_mod, 'RECONCILE_AUTO_MATCH_SINGLE_PASSES', 1):
            stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['manual_auto_matches'], 2)
        self.assertEqual(_matched_ids(sess), {
            'r-tfl': ['m-tube'],
            'r-costa': ['m-coffee'],
        })


class TestReconcileAutoMatchNetted(unittest.TestCase):
    def test_netted_manuals_when_parts_sum_exactly_in_date_window(self):
        sess, spending = _session(
            '2024-07',
            [
                _manual(id='m1', date='2024-07-10', amount=9.5, description='Lunch'),
                _manual(id='m2', date='2024-07-10', amount=15.5, description='Coffee'),
            ],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r1', date='2024-07-12', amount=25.0, description='CARD PAYMENT'),
            ]}],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['manual_auto_matches'], 0)
        self.assertEqual(stats['netted_manual_matches'], 1)
        self.assertEqual(sorted(_matched_ids(sess)['r1']), ['m1', 'm2'])
        mm = sess['uploads'][0]['rows'][0]['manual_match']
        self.assertEqual(mm['kind'], 'netted_manuals')
        self.assertEqual(mm['via'], 'auto')

    def test_netted_manuals_rejects_parts_spread_across_month(self):
        sess, spending = _session(
            '2024-07',
            [
                _manual(id='m1', date='2024-07-01', amount=11.5, description='Lunch'),
                _manual(id='m2', date='2024-07-20', amount=3.5, description='Coffee'),
            ],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r1', date='2024-07-20', amount=15.0, description='CARD'),
            ]}],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['netted_manual_matches'], 0)
        # Neither part alone equals £15, so nothing singles either.
        self.assertEqual(stats['manual_auto_matches'], 0)
        self.assertEqual(_matched_ids(sess), {})

    def test_netted_banks_close_dates_group(self):
        sess, spending = _session(
            '2024-07',
            [_manual(id='m1', date='2024-07-08', amount=15.0, description='Lunch')],
            [
                {'id': 'u1', 'file_name': 'hsbc.csv', 'bank_source': 'HSBC', 'rows': [
                    _row(id='r1', date='2024-07-08', amount=11.5, description='PRET'),
                ]},
                {'id': 'u2', 'file_name': 'amex.csv', 'bank_source': 'Amex', 'rows': [
                    _row(id='r2', date='2024-07-09', amount=3.5, description='COSTA'),
                ]},
            ],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['netted_bank_matches'], 1)
        by_id = {r['id']: r for _, _, _, _, r in app_mod._reconcile_iter_rows(sess)}
        self.assertEqual(by_id['r1']['manual_match']['kind'], 'netted_banks')
        self.assertEqual(by_id['r2']['manual_match']['kind'], 'netted_banks')
        self.assertEqual(by_id['r1']['manual_match']['group_key'], by_id['r2']['manual_match']['group_key'])

    def test_netted_banks_far_dates_rejected(self):
        sess, spending = _session(
            '2024-07',
            [_manual(id='m1', date='2024-07-31', amount=15.0, description='Lunch')],
            [
                {'id': 'u1', 'file_name': 'hsbc.csv', 'bank_source': 'HSBC', 'rows': [
                    _row(id='r1', date='2024-07-16', amount=11.5, description='PRET'),
                ]},
                {'id': 'u2', 'file_name': 'amex.csv', 'bank_source': 'Amex', 'rows': [
                    _row(id='r2', date='2024-07-08', amount=3.5, description='COSTA'),
                ]},
            ],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['netted_bank_matches'], 0)
        self.assertEqual(_matched_ids(sess), {})


class TestReconcileAutoMatchTransfers(unittest.TestCase):
    def test_pairs_equal_amount_same_day_transfer(self):
        sess, spending = _session(
            '2024-07',
            [],
            [
                {'id': 'u1', 'file_name': 'hsbc.csv', 'bank_source': 'HSBC', 'rows': [
                    _row(id='r-out', date='2024-07-08', amount=200.0, description='To Amex',
                         direction='outgoing', category='other'),
                ]},
                {'id': 'u2', 'file_name': 'amex.csv', 'bank_source': 'Amex', 'rows': [
                    _row(id='r-in', date='2024-07-08', amount=200.0, description='From HSBC',
                         direction='incoming', category=None),
                ]},
            ],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['transfer_auto_pairs'], 1)
        self.assertEqual(_transfer_pairs(sess), {('r-in', 'r-out')})

    def test_does_not_pair_when_day_gap_too_large(self):
        sess, spending = _session(
            '2024-07',
            [],
            [
                {'id': 'u1', 'file_name': 'hsbc.csv', 'rows': [
                    _row(id='r-out', date='2024-07-01', amount=50.0, description='To Amex',
                         direction='outgoing'),
                ]},
                {'id': 'u2', 'file_name': 'amex.csv', 'rows': [
                    _row(id='r-in', date='2024-07-10', amount=50.0, description='From HSBC',
                         direction='incoming'),
                ]},
            ],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['transfer_auto_pairs'], 0)
        self.assertEqual(_transfer_pairs(sess), set())


class TestReconcileAutoMatchPriority(unittest.TestCase):
    def test_single_exact_beats_netted_combo_for_same_manual(self):
        """A lone exact single claims the manual before netting can reuse it."""
        sess, spending = _session(
            '2024-07',
            [
                _manual(id='m-exact', date='2024-07-10', amount=25.0, description='Big'),
                _manual(id='m-a', date='2024-07-10', amount=10.0, description='Part A'),
                _manual(id='m-b', date='2024-07-10', amount=15.0, description='Part B'),
            ],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r-exact', date='2024-07-10', amount=25.0, description='BIG SHOP'),
                _row(id='r-net', date='2024-07-11', amount=25.0, description='CARD'),
            ]}],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['manual_auto_matches'], 1)
        self.assertEqual(stats['netted_manual_matches'], 1)
        self.assertEqual(_matched_ids(sess)['r-exact'], ['m-exact'])
        self.assertEqual(sorted(_matched_ids(sess)['r-net']), ['m-a', 'm-b'])

    def test_sets_session_matched_flags(self):
        sess, spending = _session(
            '2024-07',
            [_manual(id='m1', date='2024-07-10', amount=1.0, description='X')],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r1', date='2024-07-10', amount=1.0, description='X'),
            ]}],
        )
        app_mod._reconcile_run_auto_match(sess, spending)
        self.assertTrue(sess['auto_match_ran'])
        self.assertEqual(sess['status'], 'matched')


class TestReconcileAutoMarkBills(unittest.TestCase):
    def _spending_with_bills(self, manuals, bill_items, uploads, month='2024-07'):
        spending = {
            'transactions': manuals,
            'daily_budget': {'plan': {'bill_items': bill_items}},
            'reconcile_sessions': {},
        }
        sess = app_mod._reconcile_ensure_session(spending, month)
        sess['uploads'] = uploads
        return sess, spending

    def test_exact_label_and_amount_auto_marks_bill(self):
        sess, spending = self._spending_with_bills(
            [],
            [{'label': 'Netflix', 'amount': 15.99, 'category': 'subscriptions', 'included': True}],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r1', date='2024-07-05', amount=15.99, description='NETFLIX.COM'),
                _row(id='r2', date='2024-07-06', amount=4.50, description='COFFEE'),
            ]}],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['bill_auto_marks'], 1)
        by_id = {r['id']: r for r in sess['uploads'][0]['rows']}
        self.assertEqual(by_id['r1'].get('reconcile_mark'), 'bill')
        self.assertIsNone(by_id['r2'].get('reconcile_mark'))

    def test_amount_must_match_exactly_for_auto_bill(self):
        sess, spending = self._spending_with_bills(
            [],
            [{'label': 'Rent', 'amount': 800.0, 'category': 'housing', 'included': True}],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r1', date='2024-07-01', amount=801.0, description='RENT PAYMENT'),
            ]}],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['bill_auto_marks'], 0)
        self.assertIsNone(sess['uploads'][0]['rows'][0].get('reconcile_mark'))

    def test_weak_label_similarity_does_not_auto_mark(self):
        sess, spending = self._spending_with_bills(
            [],
            [{'label': 'Council Tax', 'amount': 120.0, 'category': 'housing', 'included': True}],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r1', date='2024-07-01', amount=120.0, description='TESCO STORES'),
            ]}],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['bill_auto_marks'], 0)

    def test_manual_match_wins_over_bill_auto_mark(self):
        sess, spending = self._spending_with_bills(
            [_manual(id='m1', date='2024-07-05', amount=15.99, description='Netflix')],
            [{'label': 'Netflix', 'amount': 15.99, 'category': 'subscriptions', 'included': True}],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r1', date='2024-07-05', amount=15.99, description='NETFLIX.COM'),
            ]}],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['manual_auto_matches'], 1)
        self.assertEqual(stats['bill_auto_marks'], 0)
        self.assertEqual(_matched_ids(sess), {'r1': ['m1']})
        self.assertIsNone(sess['uploads'][0]['rows'][0].get('reconcile_mark'))

    def test_one_plan_bill_claimed_per_row(self):
        sess, spending = self._spending_with_bills(
            [],
            [{'label': 'Spotify', 'amount': 10.99, 'category': 'subscriptions', 'included': True}],
            [{'id': 'u1', 'file_name': 'a.csv', 'rows': [
                _row(id='r1', date='2024-07-01', amount=10.99, description='SPOTIFY'),
                _row(id='r2', date='2024-07-15', amount=10.99, description='SPOTIFY PREMIUM'),
            ]}],
        )
        stats = app_mod._reconcile_run_auto_match(sess, spending)
        self.assertEqual(stats['bill_auto_marks'], 1)
        marks = [r.get('reconcile_mark') for r in sess['uploads'][0]['rows']]
        self.assertEqual(marks.count('bill'), 1)


if __name__ == '__main__':
    unittest.main()
