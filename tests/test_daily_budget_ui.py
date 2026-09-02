"""UI presence checks for daily budget Plan fields (no browser required)."""
import unittest
from pathlib import Path
from unittest import mock

import app as app_mod

ROOT = Path(__file__).resolve().parents[1]


class TestDailyBudgetPlanUiMarkup(unittest.TestCase):
    def setUp(self):
        self.client = app_mod.app.test_client()
        self.data = {'users': {'ivan': {'spending': {}}}, 'loans': {}}

    def _login(self):
        with self.client.session_transaction() as sess:
            sess['username'] = 'ivan'

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_today_tab_renders_past_date_shortcuts(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/spending/daily')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="db-date-chips"', html)
        self.assertIn('id="db-entry-date"', html)
        self.assertIn('id="db-entry-date-value"', html)
        self.assertIn('id="db-view-date-chips"', html)
        self.assertIn('id="db-view-date"', html)
        self.assertIn('data-offset="0"', html)
        self.assertIn('data-offset="-1"', html)
        self.assertIn('data-offset="-2"', html)
        self.assertIn('Yesterday', html)
        self.assertIn('2 days ago', html)
        self.assertIn('Or pick a date', html)
        self.assertIn('id="db-today-list-title"', html)
        self.assertIn('>Spends</h2>', html)
        self.assertIn('<legend>Show</legend>', html)

        js = (ROOT / 'static' / 'daily_budget.js').read_text(encoding='utf-8')
        for needle in (
            'selectEntryDate',
            'selectViewDate',
            'date: spendDate',
            'Add for yesterday',
            'db-view-date-chips',
            'viewDate',
        ):
            self.assertIn(needle, js)
        self.assertNotIn('Yesterday’s spends', js)

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_plan_tab_renders_pay_day_and_tracking_from(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/spending/daily')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="plan-pay-day"', html)
        self.assertIn('id="plan-tracking-from"', html)
        self.assertIn('Payday (day of month)', html)
        self.assertIn('Tracking from', html)
        self.assertIn('Budget cycle runs from this day', html)

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_plan_tab_shows_income_first_breakdown_copy(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/spending/daily')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="plan-math"', html)
        self.assertIn(
            'Start from your monthly income. We subtract bills and reserved savings',
            html,
        )

        js = (ROOT / 'static' / 'daily_budget.js').read_text(encoding='utf-8')
        for needle in (
            'Total monthly income',
            'Bills (outgoing)',
            'Reserved savings (',
            'Available discretionary',
            'About per day (base',
            'Monthly breakdown',
        ):
            self.assertIn(needle, js)

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_goals_tab_shows_pace_projection_section(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/spending/daily')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="pace-math"', html)
        self.assertIn('Spending vs projection', html)
        self.assertIn('How spend so far this pay period changes the daily allowance', html)
        self.assertNotIn('id="limit-math"', html)

        js = (ROOT / 'static' / 'daily_budget.js').read_text(encoding='utf-8')
        for needle in (
            'renderPaceMath',
            'Starting pool this cycle',
            'Spent so far (',
            'Projected daily going forward',
            'Original base daily',
        ):
            self.assertIn(needle, js)

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_title_suggestion_chips_hooks_present(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/spending/daily')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="db-title-suggestions"', html)
        self.assertIn('Common references for this category', html)

        js = (ROOT / 'static' / 'daily_budget.js').read_text(encoding='utf-8')
        for needle in (
            'renderTitleSuggestions',
            'common_titles_by_category',
            'db-title-chip',
            'db-title-suggestions',
        ):
            self.assertIn(needle, js)

        css = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertIn('.db-title-suggestions', css)
        self.assertIn('.db-title-chip', css)

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_overspend_debt_ui_hooks_present(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/spending/daily')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="db-overspend-prompt"', html)
        self.assertIn('id="db-debt-note"', html)
        self.assertIn('id="goals-debt-card"', html)
        self.assertIn('id="goals-debt-writeoff"', html)
        self.assertIn('name="underspend_priority"', html)
        self.assertIn('Leftover priority', html)

        js = (ROOT / 'static' / 'daily_budget.js').read_text(encoding='utf-8')
        for needle in (
            'decideOverspend',
            'writeOffDebt',
            '/api/spending/daily/overspend/decision',
            '/api/spending/daily/overspend/write-off',
            'underspend_priority',
            'renderDebtNote',
            'renderDebtCard',
            'allocation',
            'refreshGoals',
            'cycle_is_live',
            'past savings',
            'goals-alloc-bar',
        ):
            self.assertIn(needle, js)
        self.assertIn('id="goals-alloc-bar"', html)
        self.assertIn('id="goals-cycle-chips"', html)
        self.assertIn('id="goals-underspend"', html)


if __name__ == '__main__':
    unittest.main()
