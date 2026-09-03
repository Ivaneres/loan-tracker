"""Playwright: unclaimed-manuals gutter panel tracks the preview table on scroll."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path('/tmp/lt-unclaimed-gutter-scroll-test')
BASE = 'http://127.0.0.1:5083'
USER = 'ui'
PASS = 'ui'
PORT = 5083


def _wait_ready(url: str, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.2)
    raise RuntimeError(f'Server not ready at {url}: {last_err}')


def _seed(month: str) -> dict:
    manuals = []
    for i, (day, amt, desc) in enumerate(
        [
            (8, 9.5, 'Lunch with Sam'),
            (11, 10.0, 'Cafe stop'),
            (15, 6.5, 'Sandwich'),
            (15, 3.5, 'Drink'),
            (20, 22.0, 'Weekly shop'),
            (22, 4.25, 'Bus'),
        ],
        start=1,
    ):
        manuals.append(
            {
                'id': f'manual-{i}',
                'date': f'{month}-{day:02d}',
                'month': month,
                'report_month': month,
                'description': desc,
                'amount': amt,
                'direction': 'outgoing',
                'category': 'dining',
                'source': 'manual',
                'created_at': f'{month}-{day:02d}T12:00:00Z',
            }
        )
    return {
        'users': {
            USER: {
                'spending': {
                    'transactions': manuals,
                    'statements': [],
                    'monthly_insights': {},
                    'daily_budget': {'plan': {}, 'goals': []},
                }
            }
        },
        'loans': {},
    }


def _preview_payload(month: str, period_start: str, period_end: str) -> dict:
    unmatched = [
        {
            'id': t['id'],
            'date': t['date'],
            'amount': t['amount'],
            'description': t['description'],
            'direction': 'outgoing',
            'category': 'dining',
        }
        for t in _seed(month)['users'][USER]['spending']['transactions']
    ]
    # Enough rows that the import card is tall; page still scrolls with spotlight + form.
    rows = []
    for i in range(12):
        day = 10 + (i % 18)
        rows.append(
            {
                'id': f'p-{i}',
                'date': f'{month}-{day:02d}',
                'description': f'STATEMENT ROW {i + 1} MERCHANT',
                'amount': round(5.0 + i * 1.25, 2),
                'direction': 'outgoing',
                'category': 'dining',
                'preview_duplicate': False,
                'preview_duplicate_reason': None,
                'preview_review_reason': 'missed',
                'preview_manual_suggestions': [],
                'reconciliation': {'paired': False},
            }
        )
    return {
        'transactions': rows,
        'summary': {
            'report_month': month,
            'period_start': period_start,
            'period_end': period_end,
            'total_rows': len(rows),
            'incoming_total': 0.0,
            'outgoing_total': round(sum(r['amount'] for r in rows), 2),
            'net': round(-sum(r['amount'] for r in rows), 2),
            'preview_duplicate_ledger': 0,
            'preview_duplicate_upload': 0,
            'preview_missed_manual': len(rows),
            'preview_expected_bill': 0,
            'duplicate_ledger_fingerprints': [],
            'unmatched_manuals': unmatched,
        },
        'pipeline': {},
        'truncated': False,
    }


def _install_preview_mock(page, payload: dict) -> None:
    def handle(route):
        body = (
            json.dumps({'type': 'progress', 'step': 'done', 'message': 'Preview ready.'})
            + '\n'
            + json.dumps({'type': 'complete', 'payload': payload})
            + '\n'
        )
        route.fulfill(status=200, content_type='application/x-ndjson', body=body)

    page.route('**/api/spending/statement/preview-stream', handle)


def _flush_gutter_sync(page) -> None:
    """Fire scroll + wait two animation frames so scheduleUnclaimedManualsGutterPosition runs."""
    page.evaluate(
        """() => new Promise((resolve) => {
            window.dispatchEvent(new Event('scroll'));
            requestAnimationFrame(() => requestAnimationFrame(resolve));
        })"""
    )


def _geometry(page) -> dict:
    return page.evaluate(
        """() => {
            const panel = document.getElementById('import-unclaimed-manuals');
            const table = document.querySelector('.import-preview-scroll');
            if (!panel || !table) return null;
            const pr = panel.getBoundingClientRect();
            const tr = table.getBoundingClientRect();
            return {
                panelTop: pr.top,
                panelLeft: pr.left,
                panelVisible: getComputedStyle(panel).visibility !== 'hidden',
                offscreen: panel.classList.contains('import-unclaimed-manuals--offscreen'),
                tableTop: tr.top,
                tableBottom: tr.bottom,
                tableRight: tr.right,
                vh: window.innerHeight,
                scrollY: window.scrollY,
                fixed: getComputedStyle(panel).position === 'fixed',
            };
        }"""
    )


@unittest.skip('Home import preview was replaced by /spending/reconcile')
class TestUnclaimedManualsGutterScroll(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise unittest.SkipTest(f'playwright not installed: {exc}') from exc

        today = date.today()
        cls.month = today.strftime('%Y-%m')
        cls.period_start = today.replace(day=1).isoformat()
        if today.month == 12:
            cls.period_end = date(today.year, 12, 31).isoformat()
        else:
            nxt = date(today.year, today.month + 1, 1)
            cls.period_end = date.fromordinal(nxt.toordinal() - 1).isoformat()

        APP_DIR.mkdir(parents=True, exist_ok=True)
        (APP_DIR / 'data.json').write_text(
            json.dumps(_seed(cls.month), indent=2) + '\n', encoding='utf-8'
        )
        cls.csv_path = APP_DIR / 'hsbc.csv'
        cls.csv_path.write_text(
            f'Date,Description,Amount\n{cls.month}-12,PRET,10.35\n',
            encoding='utf-8',
        )

        env = os.environ.copy()
        env['FINANCE_TRACKER_USERS'] = f'{USER}:{PASS}'
        env['PYTHONPATH'] = str(ROOT) + (
            os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else ''
        )
        cls.server = subprocess.Popen(
            [
                sys.executable,
                '-c',
                (
                    'import sys; '
                    f'sys.path.insert(0, {str(ROOT)!r}); '
                    'import app; '
                    f"app.app.run(host='127.0.0.1', port={PORT}, debug=False, use_reloader=False)"
                ),
            ],
            cwd=str(APP_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_ready(f'{BASE}/login')
        except Exception:
            cls.server.terminate()
            raise

    @classmethod
    def tearDownClass(cls):
        server = getattr(cls, 'server', None)
        if not server:
            return
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    def test_gutter_panel_tracks_table_and_hides_when_offscreen(self):
        from playwright.sync_api import sync_playwright

        payload = _preview_payload(self.month, self.period_start, self.period_end)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1800, 'height': 900})
            page.goto(f'{BASE}/login')
            page.fill('input[name="username"]', USER)
            page.fill('input[name="password"]', PASS)
            page.click('button[type="submit"]')
            page.wait_for_url(f'{BASE}/')

            _install_preview_mock(page, payload)
            page.goto(f'{BASE}/')
            page.fill('#report-period-month', self.month)
            page.fill('#period-start', self.period_start)
            page.fill('#period-end', self.period_end)
            page.set_input_files('#spending-file', str(self.csv_path))
            page.click('#spending-preview-btn')
            page.wait_for_selector(
                '#import-unclaimed-manuals:not(.hidden)',
                state='attached',
                timeout=15000,
            )
            page.wait_for_selector('.import-preview-scroll .preview-tx-row', timeout=10000)
            page.locator('.import-preview-scroll').scroll_into_view_if_needed()
            _flush_gutter_sync(page)
            # Preview may first sync while the table is still below the fold.
            page.wait_for_function(
                """() => {
                    const panel = document.getElementById('import-unclaimed-manuals');
                    return panel
                        && !panel.classList.contains('hidden')
                        && !panel.classList.contains('import-unclaimed-manuals--offscreen')
                        && getComputedStyle(panel).position === 'fixed';
                }""",
                timeout=5000,
            )

            geo = _geometry(page)
            self.assertIsNotNone(geo)
            self.assertTrue(geo['fixed'], 'panel should be position:fixed in wide gutter mode')
            self.assertFalse(geo['offscreen'])
            self.assertTrue(geo['panelVisible'])
            self.assertGreaterEqual(geo['panelLeft'], geo['tableRight'] - 2)
            self.assertLess(
                abs(geo['panelTop'] - geo['tableTop']),
                4,
                f"initial align panelTop={geo['panelTop']} tableTop={geo['tableTop']}",
            )

            # Scroll down: panel top should follow the table's viewport top (until clamped).
            page.evaluate('window.scrollBy(0, 220)')
            _flush_gutter_sync(page)
            mid = _geometry(page)
            self.assertIsNotNone(mid)
            self.assertFalse(mid['offscreen'])
            self.assertGreater(mid['scrollY'], 100)
            expected_top = max(16, min(mid['tableTop'], mid['vh'] - 16 - 140))
            self.assertLess(
                abs(mid['panelTop'] - expected_top),
                4,
                f"after scroll panelTop={mid['panelTop']} expected={expected_top} tableTop={mid['tableTop']}",
            )
            # Table should have moved up in the viewport; panel tracks or clamps at the top margin.
            self.assertLess(mid['tableTop'], geo['tableTop'] - 80)
            if mid['tableTop'] > 16:
                self.assertLess(abs(mid['panelTop'] - mid['tableTop']), 4)
            else:
                self.assertLess(abs(mid['panelTop'] - 16), 1)

            # Scroll until the preview table is fully above the viewport → offscreen.
            page.evaluate(
                """() => {
                    let pad = document.getElementById('gutter-scroll-pad');
                    if (!pad) {
                        pad = document.createElement('div');
                        pad.id = 'gutter-scroll-pad';
                        pad.style.height = '1600px';
                        document.body.appendChild(pad);
                    }
                    const table = document.querySelector('.import-preview-scroll');
                    const bottom = table.getBoundingClientRect().bottom + window.scrollY;
                    window.scrollTo(0, bottom + 80);
                }"""
            )
            _flush_gutter_sync(page)
            gone = _geometry(page)
            self.assertIsNotNone(gone)
            self.assertLess(gone['tableBottom'], 16)
            self.assertTrue(gone['offscreen'], f'expected offscreen class: {gone}')
            self.assertFalse(gone['panelVisible'])

            # Scroll back so the table is visible again → panel returns.
            page.locator('.import-preview-scroll').scroll_into_view_if_needed()
            _flush_gutter_sync(page)
            back = _geometry(page)
            self.assertIsNotNone(back)
            self.assertFalse(back['offscreen'])
            self.assertTrue(back['panelVisible'])
            expected_back = max(16, min(back['tableTop'], back['vh'] - 16 - 140))
            self.assertLess(abs(back['panelTop'] - expected_back), 4)

            # Narrow viewport: gutter mode off — panel should not stay fixed in the gutter.
            page.set_viewport_size({'width': 1100, 'height': 900})
            _flush_gutter_sync(page)
            narrow = _geometry(page)
            self.assertIsNotNone(narrow)
            self.assertFalse(narrow['fixed'])

            browser.close()


if __name__ == '__main__':
    unittest.main()
