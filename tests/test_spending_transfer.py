"""Internal transfer pairing and spending insight tests."""
import unittest
from pathlib import Path
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
    }
    extra_keys = (
        'category', 'confidence', 'rationale', 'fingerprint', 'source_statement_id', 'created_at',
        'internal_transfer', 'pairing_source', 'transfer_pair_id', 'insights_excluded',
    )
    for k, v in kwargs.items():
        if k in base or k in extra_keys:
            base[k] = v
    return base


class TestSpendingPairing(unittest.TestCase):
    def test_suggest_single_pair(self):
        outs = [_tx(id='a', amount=100.0, direction='outgoing', date='2024-01-10')]
        ins = [_tx(id='b', amount=100.0, direction='incoming', date='2024-01-10', description='In')]
        pairs = app_mod._suggest_spending_transfer_pairs(outs, ins)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0]['id'], 'a')
        self.assertEqual(pairs[0][1]['id'], 'b')

    def test_date_gap_too_large_no_pair(self):
        outs = [_tx(id='a', amount=100.0, direction='outgoing', date='2024-01-10')]
        ins = [_tx(
            id='b', amount=100.0, direction='incoming', date='2024-01-20', description='In'
        )]
        pairs = app_mod._suggest_spending_transfer_pairs(outs, ins)
        self.assertEqual(pairs, [])

    def test_date_gap_two_days_no_pair(self):
        """Legs more than one calendar day apart do not auto-link."""
        outs = [_tx(id='a', amount=100.0, direction='outgoing', date='2024-01-10')]
        ins = [_tx(id='b', amount=100.0, direction='incoming', date='2024-01-12', description='In')]
        pairs = app_mod._suggest_spending_transfer_pairs(outs, ins)
        self.assertEqual(pairs, [])

    def test_date_adjacent_days_pair(self):
        outs = [_tx(id='a', amount=100.0, direction='outgoing', date='2024-01-10')]
        ins = [_tx(id='b', amount=100.0, direction='incoming', date='2024-01-11', description='In')]
        pairs = app_mod._suggest_spending_transfer_pairs(outs, ins)
        self.assertEqual(len(pairs), 1)

    def test_amount_mismatch(self):
        outs = [_tx(id='a', amount=100.0, direction='outgoing', date='2024-01-10')]
        ins = [_tx(id='b', amount=50.0, direction='incoming', date='2024-01-10')]
        pairs = app_mod._suggest_spending_transfer_pairs(outs, ins)
        self.assertEqual(pairs, [])

    def test_fee_tolerance_pairs_within_5_pounds(self):
        """e.g. £100 out, £99.50 in (fee) — within 1% of larger leg."""
        outs = [_tx(id='a', amount=100.0, direction='outgoing', date='2024-01-10')]
        ins = [_tx(id='b', amount=99.5, direction='incoming', date='2024-01-10')]
        pairs = app_mod._suggest_spending_transfer_pairs(outs, ins)
        self.assertEqual(len(pairs), 1)

    def test_small_amounts_require_tight_leg_match(self):
        """Sub-£50 legs: at most ~1% and 50p; £5 out vs £4.00 in must not auto-pair."""
        outs = [_tx(id='a', amount=5.0, direction='outgoing', date='2024-01-10')]
        ins = [_tx(id='b', amount=4.0, direction='incoming', date='2024-01-10')]
        pairs = app_mod._suggest_spending_transfer_pairs(outs, ins)
        self.assertEqual(pairs, [])

    def test_small_equal_amounts_still_pair(self):
        outs = [_tx(id='a', amount=3.2, direction='outgoing', date='2024-01-10')]
        ins = [_tx(id='b', amount=3.2, direction='incoming', date='2024-01-10')]
        pairs = app_mod._suggest_spending_transfer_pairs(outs, ins)
        self.assertEqual(len(pairs), 1)

    def test_one_to_one_greedy(self):
        """Both pairs must sit within SPENDING_TRANSFER_MAX_DAY_GAP (same day or adjacent)."""
        outs = [
            _tx(id='a1', amount=50.0, direction='outgoing', date='2024-01-05'),
            _tx(id='a2', amount=100.0, direction='outgoing', date='2024-01-06'),
        ]
        ins = [
            _tx(id='b1', amount=100.0, direction='incoming', date='2024-01-07'),
            _tx(id='b2', amount=50.0, direction='incoming', date='2024-01-05'),
        ]
        pairs = app_mod._suggest_spending_transfer_pairs(outs, ins)
        used_b = {p[1]['id'] for p in pairs}
        self.assertEqual(len(used_b), len(pairs))
        self.assertEqual(len(pairs), 2)

    def test_prefers_tighter_amount_match_over_first_in_list(self):
        """When two incomings both match an outgoing within tolerance, pick the closer amount."""
        outs = [_tx(id='o1', amount=100.0, direction='outgoing', date='2024-01-10')]
        ins = [
            _tx(id='worse', amount=99.0, direction='incoming', date='2024-01-10', description='fee'),
            _tx(id='better', amount=99.9, direction='incoming', date='2024-01-10', description='small fee'),
        ]
        pairs = app_mod._suggest_spending_transfer_pairs(outs, ins)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0]['id'], 'o1')
        self.assertEqual(pairs[0][1]['id'], 'better')

    def test_prefers_same_day_over_adjacent_when_amount_tie(self):
        outs = [_tx(id='o1', amount=100.0, direction='outgoing', date='2024-01-10')]
        ins = [
            _tx(id='nextday', amount=100.0, direction='incoming', date='2024-01-11'),
            _tx(id='sameday', amount=100.0, direction='incoming', date='2024-01-10'),
        ]
        pairs = app_mod._suggest_spending_transfer_pairs(outs, ins)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][1]['id'], 'sameday')

    def test_apply_auto_pairing(self):
        spending = {
            'transactions': [
                _tx(
                    id='1',
                    amount=200.0,
                    direction='outgoing',
                    date='2024-01-10',
                    description='To wallet',
                ),
                _tx(
                    id='2',
                    amount=200.0,
                    direction='incoming',
                    date='2024-01-11',
                    description='From bank',
                ),
            ]
        }
        r = app_mod.apply_auto_transfer_pairing_for_month(spending, '2024-01')
        self.assertEqual(r['applied_pairs'], 1)
        self.assertEqual(r['unmatched_outgoing_count'], 0)
        t1, t2 = spending['transactions']
        self.assertEqual(t1.get('pairing_source'), 'auto')
        self.assertTrue(app_mod._spending_is_paired_leg(t1))

    def test_insight_excludes_paired(self):
        spending = {
            'transactions': [
                {
                    'id': '1',
                    'date': '2024-01-10',
                    'month': '2024-01',
                    'report_month': '2024-01',
                    'direction': 'outgoing',
                    'amount': 200.0,
                    'description': 'xfer out',
                    'category': 'other',
                    'transfer_pair_id': 'p1',
                    'internal_transfer': True,
                },
                {
                    'id': '2',
                    'date': '2024-01-10',
                    'month': '2024-01',
                    'report_month': '2024-01',
                    'direction': 'incoming',
                    'amount': 200.0,
                    'description': 'xfer in',
                    'transfer_pair_id': 'p1',
                    'internal_transfer': True,
                },
                {
                    'id': '3',
                    'date': '2024-01-15',
                    'month': '2024-01',
                    'report_month': '2024-01',
                    'direction': 'outgoing',
                    'amount': 30.0,
                    'description': 'coffee',
                    'category': 'dining',
                },
                {
                    'id': '4',
                    'date': '2024-01-20',
                    'month': '2024-01',
                    'report_month': '2024-01',
                    'direction': 'incoming',
                    'amount': 1000.0,
                    'description': 'salary',
                },
            ]
        }
        ins = app_mod._compute_monthly_insight(spending, '2024-01')
        self.assertEqual(ins['income_total'], 1000.0)
        self.assertEqual(ins['outgoing_total'], 30.0)
        self.assertEqual(ins['net'], 970.0)
        self.assertIn('largest_outgoing', ins)
        self.assertEqual(len(ins['largest_outgoing']), 1)
        self.assertEqual(ins['largest_outgoing'][0]['amount'], 30.0)
        tr = ins.get('transfer_reconciliation')
        self.assertIsNotNone(tr)
        self.assertEqual(tr['pair_count'], 1)
        self.assertEqual(tr['unmatched_outgoing']['count'], 1)

    def test_user_insights_excluded_drops_from_totals(self):
        spending = {
            'transactions': [
                _tx(
                    id='1',
                    amount=50.0,
                    direction='outgoing',
                    description='keep me',
                    category='other',
                    report_month='2024-01',
                ),
                _tx(
                    id='2',
                    amount=25.0,
                    direction='outgoing',
                    description='exclude me',
                    category='other',
                    report_month='2024-01',
                    insights_excluded=True,
                ),
                _tx(
                    id='3',
                    amount=100.0,
                    direction='incoming',
                    description='salary',
                    report_month='2024-01',
                ),
                _tx(
                    id='4',
                    amount=40.0,
                    direction='incoming',
                    description='gift',
                    report_month='2024-01',
                    insights_excluded=True,
                ),
            ],
        }
        ins = app_mod._compute_monthly_insight(spending, '2024-01')
        self.assertEqual(ins['outgoing_total'], 50.0)
        self.assertEqual(ins['income_total'], 100.0)
        self.assertEqual(ins['net'], 50.0)

    def test_reconciliation_helper_pair_count(self):
        rows = [
            _tx(
                id='1',
                amount=10.0,
                direction='outgoing',
                transfer_pair_id='p',
                internal_transfer=True,
            ),
            _tx(
                id='2',
                amount=10.0,
                direction='incoming',
                description='B',
                transfer_pair_id='p',
                internal_transfer=True,
            ),
        ]
        r = app_mod._spending_transfer_reconciliation_for_month(rows)
        self.assertEqual(r['pair_count'], 1)
        self.assertEqual(r['paired_leg_amount_mismatch'], 0.0)


    def test_preview_simulation_merges_ledger_with_file(self):
        """Second statement preview: pairs against rows already in ledger for the month."""
        spending = {
            'transactions': [
                _tx(
                    id='led1',
                    amount=150.0,
                    direction='outgoing',
                    date='2024-01-12',
                    description='HSBC to Revolut',
                ),
            ],
        }
        spending['transactions'][0]['report_month'] = '2024-01'
        candidates = [
            _tx(
                id='c1',
                amount=150.0,
                direction='incoming',
                date='2024-01-12',
                description='Top up from HSBC',
            ),
        ]
        out = app_mod.simulate_spending_transfer_reconciliation_preview(
            spending, '2024-01', candidates
        )
        self.assertEqual(out['ledger_row_count_in_month'], 1)
        self.assertEqual(out['auto_applied_pairs_in_simulation'], 1)
        p = out['pairing']['c1']
        self.assertTrue(p['paired'])
        self.assertTrue(p['peer_is_from_ledger'])

    def test_insight_largest_category_breakdown_pareto(self):
        spending = {
            'transactions': [
                {
                    'id': '1',
                    'date': '2024-01-10',
                    'report_month': '2024-01',
                    'direction': 'outgoing',
                    'amount': 200.0,
                    'description': 'big shop',
                    'category': 'shopping',
                },
                {
                    'id': '2',
                    'date': '2024-01-15',
                    'report_month': '2024-01',
                    'direction': 'outgoing',
                    'amount': 30.0,
                    'description': 'coffee',
                    'category': 'dining',
                },
            ]
        }
        ins = app_mod._compute_monthly_insight(spending, '2024-01')
        self.assertEqual(len(ins['largest_outgoing']), 2)
        self.assertEqual(ins['largest_outgoing'][0]['amount'], 200.0)
        self.assertEqual(ins['top_transactions_pareto_pct'], 100.0)
        cats = {c['category']: c for c in ins['category_breakdown']}
        self.assertIn('shopping', cats)
        self.assertIn('dining', cats)
        self.assertAlmostEqual(cats['shopping']['pct_of_outgoing'], 200 / 230 * 100, places=0)

    def test_subscription_signals_cross_month(self):
        spending = {
            'transactions': [
                {
                    'id': 'a',
                    'date': '2024-01-05',
                    'report_month': '2024-01',
                    'direction': 'outgoing',
                    'amount': 10.0,
                    'description': 'NETFLIX.COM',
                    'category': 'subscriptions',
                },
                {
                    'id': 'b',
                    'date': '2024-02-05',
                    'report_month': '2024-02',
                    'direction': 'outgoing',
                    'amount': 10.0,
                    'description': 'NETFLIX.COM',
                    'category': 'subscriptions',
                },
            ]
        }
        ins_feb = app_mod._compute_monthly_insight(spending, '2024-02')
        sig = ins_feb.get('subscription_signals') or []
        labels = [s.get('label', '') for s in sig]
        self.assertTrue(any('netflix' in lab for lab in labels))
        netflix_row = next(s for s in sig if 'netflix' in s.get('label', ''))
        self.assertGreaterEqual(netflix_row['consecutive_streak'], 2)
        self.assertEqual(netflix_row['months_active'], 2)
        self.assertEqual(netflix_row.get('last_amount'), 10.0)
        self.assertEqual(netflix_row.get('total_last_month'), 10.0)
        self.assertEqual(netflix_row.get('trend'), 'flat')

    def test_subscription_signals_trend_up(self):
        spending = {
            'transactions': [
                {
                    'id': 'a',
                    'date': '2024-01-05',
                    'report_month': '2024-01',
                    'direction': 'outgoing',
                    'amount': 10.0,
                    'description': 'GYM DD',
                    'category': 'health',
                },
                {
                    'id': 'b',
                    'date': '2024-02-05',
                    'report_month': '2024-02',
                    'direction': 'outgoing',
                    'amount': 12.0,
                    'description': 'GYM DD',
                    'category': 'health',
                },
            ]
        }
        ins = app_mod._compute_monthly_insight(spending, '2024-02')
        sig = ins.get('subscription_signals') or []
        gym = next(s for s in sig if 'gym' in s.get('label', ''))
        self.assertEqual(gym.get('trend'), 'up')

    def test_subscription_signals_merges_description_variations(self):
        """HALIFAX / HALIFAX DD and SPOTIFY / SPOTIFY LDN should count as the same line item."""
        spending = {
            'transactions': [
                {
                    'id': 'a',
                    'date': '2024-01-10',
                    'report_month': '2024-01',
                    'direction': 'outgoing',
                    'amount': 5.0,
                    'description': 'HALIFAX',
                    'category': 'bills',
                },
                {
                    'id': 'b',
                    'date': '2024-02-10',
                    'report_month': '2024-02',
                    'direction': 'outgoing',
                    'amount': 5.0,
                    'description': 'HALIFAX DD',
                    'category': 'bills',
                },
                {
                    'id': 'c',
                    'date': '2024-01-20',
                    'report_month': '2024-01',
                    'direction': 'outgoing',
                    'amount': 9.99,
                    'description': 'SPOTIFY LDN',
                    'category': 'subscriptions',
                },
                {
                    'id': 'd',
                    'date': '2024-02-20',
                    'report_month': '2024-02',
                    'direction': 'outgoing',
                    'amount': 9.99,
                    'description': 'SPOTIFY',
                    'category': 'subscriptions',
                },
            ]
        }
        ins = app_mod._compute_monthly_insight(spending, '2024-02')
        sig = ins.get('subscription_signals') or []
        labels = ' '.join(s.get('label', '') for s in sig)
        self.assertIn('halifax', labels)
        self.assertIn('spotify', labels)
        halifax = next(s for s in sig if 'halifax' in s.get('label', ''))
        spotify = next(s for s in sig if 'spotify' in s.get('label', ''))
        self.assertEqual(halifax.get('last_amount'), 5.0)
        self.assertGreaterEqual(halifax.get('consecutive_streak', 0), 2)
        self.assertEqual(spotify.get('last_amount'), 9.99)
        self.assertGreaterEqual(spotify.get('consecutive_streak', 0), 2)

    def test_subscription_signals_no_merge_different_amounts_same_month(self):
        """Prefix-style names are not merged when the same month has two very different amounts."""
        spending = {
            'transactions': [
                {
                    'id': 'a',
                    'date': '2024-01-05',
                    'report_month': '2024-01',
                    'direction': 'outgoing',
                    'amount': 5.0,
                    'description': 'AMAZON',
                    'category': 'shopping',
                },
                {
                    'id': 'b',
                    'date': '2024-01-06',
                    'report_month': '2024-01',
                    'direction': 'outgoing',
                    'amount': 30.0,
                    'description': 'AMAZON PRIME',
                    'category': 'subscriptions',
                },
                {
                    'id': 'c',
                    'date': '2024-02-05',
                    'report_month': '2024-02',
                    'direction': 'outgoing',
                    'amount': 5.0,
                    'description': 'AMAZON',
                    'category': 'shopping',
                },
                {
                    'id': 'd',
                    'date': '2024-02-06',
                    'report_month': '2024-02',
                    'direction': 'outgoing',
                    'amount': 30.0,
                    'description': 'AMAZON PRIME',
                    'category': 'subscriptions',
                },
            ]
        }
        self.assertTrue(app_mod._subscription_labels_fuzzy_match('amazon', 'amazon prime'))
        self.assertFalse(
            app_mod._subscription_pair_merge_amounts_ok(
                {'2024-01': 5.0, '2024-02': 5.0},
                {'2024-01': 30.0, '2024-02': 30.0},
                ['2023-10', '2023-11', '2023-12', '2024-01', '2024-02'],
            )
        )
        ins = app_mod._compute_monthly_insight(spending, '2024-02')
        sig = ins.get('subscription_signals') or []
        amazonish = [s for s in sig if 'amazon' in s.get('label', '')]
        self.assertEqual(len(amazonish), 2)
        self.assertListEqual(
            sorted(x.get('last_amount', 0) for x in amazonish),
            [5.0, 30.0],
        )

    def test_subscription_fuzzy_strips_bank_and_location_noise(self):
        """Leading bank words and trailing city/payment tokens line up the merchant name."""
        sp = app_mod._normalize_label('SPOTIFY')
        for longer in (
            'SPOTIFY LONDON VIS',
            'VISA SPOTIFY LONDON VIS',
            'DIRECT DEBIT SPOTIFY LONDON VIS',
            'POS SPOTIFY LONDON VIS',
        ):
            self.assertTrue(
                app_mod._subscription_labels_fuzzy_match(sp, app_mod._normalize_label(longer)),
                longer,
            )

    def test_subscription_merge_helpers_netflix_siblings(self):
        """First-word match joins NETFLIX… variants when amounts align."""
        self.assertTrue(
            app_mod._subscription_labels_fuzzy_match('netflix com', 'netflix ldn')
        )
        self.assertTrue(
            app_mod._subscription_pair_merge_amounts_ok(
                {'2024-01': 9.0, '2024-02': 9.0},
                {'2024-01': 8.0, '2024-02': 0.0},
                ['2024-01', '2024-02'],
            )
        )
        # 7 vs 10: (10-7)/10 = 0.3 > merge tol 0.25
        self.assertFalse(
            app_mod._subscription_pair_merge_amounts_ok(
                {'2024-01': 7.0},
                {'2024-01': 10.0},
                ['2024-01', '2024-02'],
            )
        )

    def test_subscription_signals_amount_flexibility(self):
        """Utility-style amount drift (~35%) should still count as recurring."""
        spending = {
            'transactions': [
                {
                    'id': 'a',
                    'date': '2024-01-12',
                    'report_month': '2024-01',
                    'direction': 'outgoing',
                    'amount': 70.0,
                    'description': 'OCTOPUS ENERGY',
                    'category': 'utilities',
                },
                {
                    'id': 'b',
                    'date': '2024-02-14',
                    'report_month': '2024-02',
                    'direction': 'outgoing',
                    'amount': 95.0,
                    'description': 'OCTOPUS ENERGY',
                    'category': 'utilities',
                },
                {
                    'id': 'c',
                    'date': '2024-03-13',
                    'report_month': '2024-03',
                    'direction': 'outgoing',
                    'amount': 100.0,
                    'description': 'OCTOPUS ENERGY',
                    'category': 'utilities',
                },
            ]
        }
        # Old spread cap 0.25 would reject: (100-70)/mean ≈ 0.34
        sig = app_mod._subscription_signals_for_month(spending, '2024-03')
        energy = next(s for s in sig if 'octopus' in s.get('label', ''))
        self.assertGreaterEqual(energy['months_active'], 3)
        self.assertEqual(energy.get('last_amount'), 100.0)

    def test_subscription_signals_date_flexibility_month_boundary(self):
        """Posting dates that drift across month boundaries still form a monthly bill."""
        spending = {
            'transactions': [
                {
                    'id': 'a',
                    'date': '2024-01-30',
                    'report_month': '2024-01',
                    'direction': 'outgoing',
                    'amount': 50.0,
                    'description': 'COUNCIL TAX',
                    'category': 'housing',
                },
                {
                    # ~31 days later — lands in March calendar month; old month-streak missed this
                    'id': 'b',
                    'date': '2024-03-01',
                    'report_month': '2024-03',
                    'direction': 'outgoing',
                    'amount': 52.0,
                    'description': 'COUNCIL TAX',
                    'category': 'housing',
                },
            ]
        }
        sig = app_mod._subscription_signals_for_month(spending, '2024-03')
        labels = [s.get('label', '') for s in sig]
        self.assertTrue(any('council' in lab for lab in labels))
        row = next(s for s in sig if 'council' in s.get('label', ''))
        self.assertEqual(row.get('last_amount'), 52.0)

    def test_subscription_signals_day_of_month_drift(self):
        """Same bill a few days earlier/later each month still qualifies."""
        spending = {
            'transactions': [
                {
                    'id': 'a',
                    'date': '2024-01-28',
                    'report_month': '2024-01',
                    'direction': 'outgoing',
                    'amount': 15.99,
                    'description': 'DISNEY PLUS',
                    'category': 'subscriptions',
                },
                {
                    'id': 'b',
                    'date': '2024-02-26',
                    'report_month': '2024-02',
                    'direction': 'outgoing',
                    'amount': 15.99,
                    'description': 'DISNEY PLUS',
                    'category': 'subscriptions',
                },
                {
                    'id': 'c',
                    'date': '2024-03-29',
                    'report_month': '2024-03',
                    'direction': 'outgoing',
                    'amount': 15.99,
                    'description': 'DISNEY PLUS',
                    'category': 'subscriptions',
                },
            ]
        }
        sig = app_mod._subscription_signals_for_month(spending, '2024-03')
        disney = next(s for s in sig if 'disney' in s.get('label', ''))
        self.assertGreaterEqual(disney['months_active'], 3)

    def test_subscription_signals_rejects_noisy_shopping(self):
        """Frequent variable merchant spend should not look like a 2-hit subscription."""
        txs = []
        # Many grocery-like charges across two months
        for i, day in enumerate((3, 7, 12, 18, 22, 27)):
            txs.append({
                'id': f'j{i}',
                'date': f'2024-01-{day:02d}',
                'report_month': '2024-01',
                'direction': 'outgoing',
                'amount': 20.0 + i * 3,
                'description': 'TESCO STORE',
                'category': 'groceries',
            })
        for i, day in enumerate((4, 9, 15, 19, 24, 28)):
            txs.append({
                'id': f'f{i}',
                'date': f'2024-02-{day:02d}',
                'report_month': '2024-02',
                'direction': 'outgoing',
                'amount': 18.0 + i * 4,
                'description': 'TESCO STORE',
                'category': 'groceries',
            })
        spending = {'transactions': txs}
        sig = app_mod._subscription_signals_for_month(spending, '2024-02')
        tesco = [s for s in sig if 'tesco' in s.get('label', '')]
        self.assertEqual(tesco, [])

    def test_subscription_dom_distance_wraps_month_end(self):
        self.assertEqual(app_mod._subscription_dom_distance(30, 1), 2)
        self.assertEqual(app_mod._subscription_dom_distance(5, 8), 3)
        self.assertLessEqual(app_mod._subscription_dom_distance(28, 2), 5)

    def test_subscription_hybrid_bill_includes_flexible_signal(self):
        """Daily-tab hybrid pull should surface interval-based subscription signals."""
        spending = {
            'transactions': [
                {
                    'id': 'a',
                    'date': '2024-01-30',
                    'report_month': '2024-01',
                    'month': '2024-01',
                    'direction': 'outgoing',
                    'amount': 9.99,
                    'description': 'SPOTIFY',
                    'category': 'other',
                },
                {
                    'id': 'b',
                    'date': '2024-03-02',
                    'report_month': '2024-03',
                    'month': '2024-03',
                    'direction': 'outgoing',
                    'amount': 10.49,
                    'description': 'SPOTIFY',
                    'category': 'other',
                },
                {
                    'id': 'c',
                    'date': '2024-03-10',
                    'report_month': '2024-03',
                    'month': '2024-03',
                    'direction': 'incoming',
                    'amount': 2000.0,
                    'description': 'SALARY',
                    'category': None,
                },
            ],
            'monthly_insights': {'2024-03': {'income_total': 2000.0}},
        }
        with mock.patch.object(app_mod, '_llm_flag_regular_bills', return_value=[]):
            est = app_mod._build_hybrid_bill_estimate(spending, '2024-03', use_llm=False)
        labels = {b['label'] for b in est['bill_items']}
        self.assertTrue(any('SPOTIFY' in lab for lab in labels))
        spot = next(b for b in est['bill_items'] if 'SPOTIFY' in b['label'])
        self.assertEqual(spot['source'], 'subscription_signal')

    def test_ledger_and_upload_marks(self):
        fp = app_mod._spending_fingerprint(
            '2024-01', '2024-01-10', 25.5, 'outgoing', 'Coffee shop',
        )
        spending = {
            'transactions': [
                _tx(
                    fingerprint=fp,
                    description='Coffee shop',
                    amount=25.5,
                    direction='outgoing',
                ),
            ],
        }
        rows = [
            {
                'date': '2024-01-10',
                'amount': 25.5,
                'direction': 'outgoing',
                'description': 'Coffee shop',
            },
            {
                'date': '2024-01-10',
                'amount': 25.5,
                'direction': 'outgoing',
                'description': 'Coffee shop',
            },
            {'date': '2024-01-11', 'amount': 10.0, 'direction': 'outgoing', 'description': 'Other'},
        ]
        led, dup_u, client_fps = app_mod._apply_spending_preview_duplicate_marks('2024-01', rows, spending)
        self.assertEqual(led, 2)
        self.assertEqual(dup_u, 0)
        self.assertIn(fp, client_fps)
        self.assertTrue(rows[0]['preview_duplicate'])
        self.assertEqual(rows[0]['preview_duplicate_reason'], 'ledger')
        self.assertTrue(rows[1]['preview_duplicate'])
        self.assertFalse(rows[2]['preview_duplicate'])

    def test_second_row_in_file_only(self):
        spending = {'transactions': []}
        rows = [
            {'date': '2024-01-10', 'amount': 5.0, 'direction': 'outgoing', 'description': 'A'},
            {'date': '2024-01-10', 'amount': 5.0, 'direction': 'outgoing', 'description': 'A'},
        ]
        led, dup_u, _ = app_mod._apply_spending_preview_duplicate_marks('2024-01', rows, spending)
        self.assertEqual(led, 0)
        self.assertEqual(dup_u, 1)
        self.assertFalse(rows[0]['preview_duplicate'])
        self.assertEqual(rows[0].get('preview_review_reason'), 'missed')
        self.assertTrue(rows[1]['preview_duplicate'])
        self.assertEqual(rows[1]['preview_duplicate_reason'], 'upload')

    def test_preview_match_ui_strings(self):
        home = (Path(__file__).resolve().parents[1] / 'templates' / 'home.html').read_text(encoding='utf-8')
        js = (Path(__file__).resolve().parents[1] / 'static' / 'spending.js').read_text(encoding='utf-8')
        css = (Path(__file__).resolve().parents[1] / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertIn('Match', home)
        self.assertIn('Matches manual entry', js)
        self.assertIn('Not in manual', js)
        self.assertIn('Expected bill', js)
        self.assertIn('spending-preview-row-missed', css)
        self.assertIn('preview-review-pill', css)

    def test_insight_extended_fields_with_prior_month(self):
        """MoM deltas, trailing averages, category_trends, budget_action_items shape."""
        spending = {
            'monthly_insights': {
                '2024-01': {
                    'income_total': 1000.0,
                    'outgoing_total': 400.0,
                    'net': 600.0,
                    'savings_rate': 60.0,
                    'category_breakdown': [
                        {'category': 'groceries', 'amount': 400.0, 'pct_of_outgoing': 100.0},
                    ],
                },
            },
            'transactions': [
                _tx(
                    id='a',
                    amount=600.0,
                    direction='outgoing',
                    category='groceries',
                    report_month='2024-02',
                    description='shop',
                ),
                _tx(
                    id='b',
                    amount=2000.0,
                    direction='incoming',
                    report_month='2024-02',
                    description='pay',
                ),
            ],
        }
        ins = app_mod._compute_monthly_insight(spending, '2024-02')
        self.assertEqual(ins.get('income_delta_vs_prev_month'), 1000.0)
        self.assertEqual(ins.get('outgoing_delta_vs_prev_month'), 200.0)
        self.assertIsNotNone(ins.get('income_trailing_avg_3m'))
        self.assertIsNotNone(ins.get('outgoing_trailing_avg_3m'))
        self.assertIn('category_trends', ins)
        self.assertTrue(any(t.get('category') == 'groceries' for t in (ins.get('category_trends') or [])))
        self.assertIn('budget_action_items', ins)
        self.assertIsInstance(ins.get('budget_action_items'), list)
        for a in ins.get('anomalies') or []:
            if a.get('kind') == 'category_spike':
                self.assertIn('delta_pct', a)

    def test_anomaly_uses_monthly_category_total_not_per_line_average(self):
        """Spike baseline = average of prior months' category *totals*, not per-tx means."""
        spending = {
            'monthly_insights': {},
            'transactions': [
                *[
                    _tx(
                        id=f'jan{i}',
                        amount=2.0,
                        direction='outgoing',
                        category='groceries',
                        report_month='2024-01',
                        date=f'2024-01-{10 + i:02d}',
                        description='small',
                    )
                    for i in range(10)
                ],
                *[
                    _tx(
                        id=f'feb{i}',
                        amount=2.0,
                        direction='outgoing',
                        category='groceries',
                        report_month='2024-02',
                        date=f'2024-02-{5 + i:02d}',
                        description='small',
                    )
                    for i in range(5)
                ],
                _tx(
                    id='m',
                    amount=200.0,
                    direction='outgoing',
                    category='groceries',
                    report_month='2024-03',
                    date='2024-03-15',
                    description='march',
                ),
                _tx(
                    id='inc',
                    amount=500.0,
                    direction='incoming',
                    report_month='2024-03',
                    date='2024-03-01',
                    description='x',
                ),
            ],
        }
        ins = app_mod._compute_monthly_insight(spending, '2024-03')
        self.assertEqual(ins.get('outgoing_total'), 200.0)
        gro_anom = [a for a in (ins.get('anomalies') or []) if a.get('category') == 'groceries']
        self.assertTrue(gro_anom, 'expected groceries anomaly')
        b = float(gro_anom[0]['baseline_avg'])
        # Jan total 20, Feb 10; Dec has no transactions so it is excluded — avg = 15 (not 10 with a fake Dec=0)
        self.assertAlmostEqual(b, 15.0, places=2)
        self.assertEqual(gro_anom[0].get('baseline_months'), 2)


if __name__ == '__main__':
    unittest.main()
