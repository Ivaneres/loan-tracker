#!/usr/bin/env python3
"""Capture Home Statements consolidation screenshots (list → expand → Reconcile)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path('/tmp/lt-home-statements-demo')
LOCAL_SHOT_DIR = ROOT / 'tmp' / 'screenshots' / 'home-statements'
DOCS_SHOT_DIR = ROOT / 'docs' / 'screenshots' / 'home-statements'
BASE = 'http://127.0.0.1:5088'
USER = 'ui'
PASS = 'ui'
PORT = 5088


def wait_ready(url: str, timeout: float = 30.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status < 500:
                    return
        except Exception as exc:  # pragma: no cover
            last = exc
            time.sleep(0.25)
    raise RuntimeError(f'Server not ready at {url}: {last}')


def save(page, name: str, *, full_page: bool = True) -> None:
    for dest in (LOCAL_SHOT_DIR, DOCS_SHOT_DIR):
        dest.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(dest / name), full_page=full_page)


def save_locator(locator, name: str) -> None:
    for dest in (LOCAL_SHOT_DIR, DOCS_SHOT_DIR):
        dest.mkdir(parents=True, exist_ok=True)
        locator.screenshot(path=str(dest / name))


def bank_tx(tid, month, day, amount, desc, source='Monzo'):
    return {
        'id': tid,
        'date': f'{month}-{day:02d}',
        'month': month,
        'report_month': month,
        'description': desc,
        'amount': amount,
        'direction': 'outgoing',
        'category': 'dining',
        'source': source,
    }


def session(month, status, uploads=0, auto_match=False):
    rows = [{'id': f'{month}-r{i}', 'date': f'{month}-01', 'description': 'Row', 'amount': 1, 'direction': 'outgoing', 'include': True} for i in range(uploads)]
    return {
        'report_month': month,
        'status': status,
        'auto_match_ran': auto_match,
        'uploads': [
            {
                'id': f'u-{month}-{i}',
                'file_name': f'{month}-stmt-{i + 1}.csv',
                'bank_source': 'Monzo',
                'rows': rows[:1] if i == 0 else [],
            }
            for i in range(max(uploads, 0))
        ] if uploads else [],
        'excluded_manual_ids': [],
        'kept_manual_ids': [],
        'reviewed_unclaimed': status == 'imported',
    }


def seed_data() -> dict:
    txs = [
        bank_tx('b-aug-1', '2026-08', 4, 2140.5, 'Shop', 'Amex'),
        bank_tx('b-jul-1', '2026-07', 12, 1988.0, 'Rent', 'Monzo'),
        bank_tx('b-jun-1', '2026-06', 3, 2310.75, 'Holiday', 'Amex'),
        bank_tx('b-may-1', '2026-05', 8, 1820.2, 'Groceries', 'Monzo'),
        bank_tx('b-apr-1', '2026-04', 2, 2050.0, 'Flights', 'Amex'),
        bank_tx('b-jan-1', '2026-01', 9, 1764.4, 'Council tax', 'Monzo'),
    ]
    insights = {
        '2026-08': {'outgoing_total': 2140.5, 'income_total': 2800, 'net': 659.5, 'savings_rate': 12.4},
        '2026-07': {'outgoing_total': 1988.0, 'income_total': 2800, 'net': 812.0, 'savings_rate': 15.0},
        '2026-06': {'outgoing_total': 2310.75, 'income_total': 2800, 'net': 489.25, 'savings_rate': 8.0},
        '2026-05': {'outgoing_total': 1820.2, 'income_total': 2800, 'net': 979.8, 'savings_rate': 18.0},
        '2026-04': {'outgoing_total': 2050.0, 'income_total': 2800, 'net': 750.0, 'savings_rate': 11.0},
        '2026-01': {'outgoing_total': 1764.4, 'income_total': 2800, 'net': 1035.6, 'savings_rate': 20.0},
    }
    return {
        'loans': {
            'loan-1': {'name': 'Car', 'loan_amount': 4200, 'deleted': False, 'transactions': []},
            'loan-2': {'name': 'Study', 'loan_amount': 4300, 'deleted': False, 'transactions': []},
        },
        'users': {
            USER: {
                'spending': {
                    'statements': [],
                    'transactions': txs,
                    'monthly_insights': insights,
                    'classification_overrides': {},
                    'classification_cache': {},
                    'daily_budget': {
                        'plan': {
                            'income_monthly': 2800,
                            'bills_monthly': 900,
                            'savings_percent': 10,
                        },
                    },
                    'reconcile_sessions': {
                        '2026-09': session('2026-09', 'staging', uploads=2, auto_match=True),
                        '2026-08': session('2026-08', 'imported', uploads=1, auto_match=True),
                        '2026-07': session('2026-07', 'imported', uploads=1, auto_match=True),
                        '2026-06': session('2026-06', 'imported', uploads=1, auto_match=True),
                    },
                }
            }
        },
    }


def login(page) -> None:
    page.goto(f'{BASE}/login')
    page.fill('#username', USER)
    page.fill('#password', PASS)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda u: '/login' not in u)


def main() -> int:
    for d in (LOCAL_SHOT_DIR, DOCS_SHOT_DIR, APP_DIR):
        d.mkdir(parents=True, exist_ok=True)

    (APP_DIR / 'data.json').write_text(json.dumps(seed_data(), indent=2) + '\n', encoding='utf-8')

    env = os.environ.copy()
    env['FINANCE_TRACKER_USERS'] = f'{USER}:{PASS}'
    env['PYTHONPATH'] = str(ROOT) + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')

    server = subprocess.Popen(
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
        wait_ready(f'{BASE}/login')
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            login(page)

            page.goto(f'{BASE}/')
            page.wait_for_selector('#home-month-list')
            save(page, '01-home-desktop.png')
            save_locator(page.locator('.home-spotlight'), '02-spotlight.png')
            save_locator(page.locator('section[aria-labelledby="home-statements-heading"]'), '03-statements-five.png')

            expect(page.locator('#home-month-list li:not(.hidden) .home-month')).to_have_count(5)
            page.click('#home-months-toggle')
            page.wait_for_timeout(200)
            expect(page.locator('#home-month-list li:not(.hidden) .home-month')).to_have_count(9)
            save_locator(page.locator('section[aria-labelledby="home-statements-heading"]'), '04-statements-all.png')

            page.locator('.home-month-pill--imported_without_recon').first.click()
            page.wait_for_url('**/spending/reconcile?month=2026-05')
            page.wait_for_selector('#reconcile-composer, .reconcile-composer')
            save(page, '05-reconcile-legacy-month.png')

            page.goto(f'{BASE}/spending/reconcile?month=2026-08')
            page.wait_for_function(
                "() => document.getElementById('reconcile-lead')?.textContent.includes('imported')"
            )
            save_locator(page.locator('.reconcile-section'), '06-reconcile-readonly.png')

            page.goto(f'{BASE}/spending/reconcile?month=2026-09')
            page.wait_for_selector('#reconcile-range-toggle')
            page.click('#reconcile-range-toggle')
            page.wait_for_selector('#reconcile-range-fields:not(.hidden)')
            save_locator(page.locator('.reconcile-stage'), '07-reconcile-date-range.png')

            page.set_viewport_size({'width': 390, 'height': 844})
            page.goto(f'{BASE}/')
            page.wait_for_selector('#home-month-list')
            save(page, '08-home-mobile.png', full_page=True)

            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
    print(f'Saved screenshots to {DOCS_SHOT_DIR}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
