"""Global quick-log: the entry form lives in a modal on every page.

The modal reuses the Daily form's element ids (#db-entry-form and friends) so
daily_budget.js keeps binding to it unchanged; on other pages spend_modal.js
binds it. These tests lock in the markup contract.
"""
import unittest
from pathlib import Path
from unittest import mock

import app as app_mod

ROOT = Path(__file__).resolve().parents[1]
SPEND_MODAL_JS = (ROOT / 'static' / 'spend_modal.js').read_text(encoding='utf-8')
STYLE_CSS = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')

# Pages rendered through partials/app_header.html, plus loan detail (index.html),
# which builds its own chrome.
APP_PAGES = ('/', '/spending', '/spending/daily', '/spending/search', '/spending/reconcile', '/loans')


class TestSpendModal(unittest.TestCase):
    def setUp(self):
        self.client = app_mod.app.test_client()
        self.spending = {
            'transactions': [],
            'statements': [],
            'monthly_insights': {},
            'manual_match_ignore': {},
            'classification_cache': {},
            'daily_budget': {},
        }
        self.data = {'users': {'ivan': {'spending': self.spending}}, 'loans': {}}

    def _login(self):
        with self.client.session_transaction() as sess:
            sess['username'] = 'ivan'

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_modal_present_on_every_app_page(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        for path in APP_PAGES:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)
            html = resp.get_data(as_text=True)
            self.assertIn('id="spend-modal"', html, path)
            self.assertIn('id="spend-fab"', html, path)
            self.assertIn('id="db-entry-form"', html, path)
            self.assertIn('spend_modal.js', html, path)

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_daily_entry_form_only_lives_in_the_modal(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        html = self.client.get('/spending/daily').get_data(as_text=True)
        # Exactly one entry form on the page, and it is inside the modal.
        self.assertEqual(html.count('id="db-entry-form"'), 1)
        self.assertLess(html.index('id="spend-modal"'), html.index('id="db-entry-form"'))
        # The Today panel no longer carries the inline form.
        today_panel = html.split('id="panel-today"', 1)[1].split('id="panel-plan"', 1)[0]
        self.assertNotIn('id="db-entry-form"', today_panel)

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_form_fields_match_the_daily_form(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        html = self.client.get('/').get_data(as_text=True)
        modal = html.split('id="spend-modal"', 1)[1]
        for control in ('id="db-amount"', 'id="db-title"', 'id="db-entry-date"',
                        'id="db-cat-grid"', 'id="db-category"', 'id="db-add-btn"'):
            self.assertIn(control, modal, control)
        # Category buttons come from the shared context variable.
        self.assertIn('data-category="dining"', modal)
        self.assertIn('db-cat--selected', modal)

    def test_form_is_owned_by_the_modal_module_only(self):
        # One implementation of the log-spend form: spend_modal.js owns it on every
        # page; daily_budget.js must not bind it (that would double-post).
        self.assertIn('/api/spending/daily/entry', SPEND_MODAL_JS)
        self.assertIn('tally:spend-logged', SPEND_MODAL_JS)
        daily = (ROOT / 'static' / 'daily_budget.js').read_text(encoding='utf-8')
        for needle in ("$('db-entry-form')", 'db-title-suggestions', 'db-cat-grid',
                       'updateAddButtonLabel', 'renderTitleSuggestions'):
            self.assertNotIn(needle, daily, needle)
        self.assertIn("addEventListener('tally:spend-logged'", daily)

    def test_modal_traps_focus(self):
        # aria-modal="true" needs the background made inert.
        self.assertIn('aria-modal="true"',
                      (ROOT / 'templates' / 'partials' / 'spend_modal.html').read_text(encoding='utf-8'))
        self.assertIn('setBackgroundInert', SPEND_MODAL_JS)
        self.assertIn("setAttribute('inert'", SPEND_MODAL_JS)

    def test_loan_detail_builds_the_same_chrome(self):
        # Loan detail hand-rolls its header, so it must include the chrome itself.
        html = (ROOT / 'templates' / 'index.html').read_text(encoding='utf-8')
        for piece in ("partials/app_rail.html", "partials/app_tabbar.html",
                      "partials/spend_modal.html", "spend_modal.js",
                      'href="/spending/search"'):
            self.assertIn(piece, html, piece)

    def test_old_top_nav_partial_is_gone(self):
        self.assertFalse((ROOT / 'templates' / 'partials' / 'app_nav.html').exists())


class TestMonthlyTierHiddenState(unittest.TestCase):
    """Regression: the mobile flex layout must not defeat Tailwind's .hidden.

    `body.spending-page #insight-content` (element + id + class) out-specifies
    `.hidden` (class), which would show a zeroed dashboard to a user with no
    insights. `.spending-page .hidden` restores the convention used on the
    other page classes.
    """

    def test_spending_page_hidden_rule_is_important(self):
        marker = '.spending-page .hidden'
        self.assertIn(marker, STYLE_CSS)
        rule = STYLE_CSS[STYLE_CSS.index(marker):STYLE_CSS.index(marker) + 120]
        self.assertIn('display: none !important', rule)

    def test_flex_rule_is_scoped_to_the_insights_container(self):
        self.assertIn('body.spending-page #insight-content { display: flex', STYLE_CSS)


if __name__ == '__main__':
    unittest.main()
