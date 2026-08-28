#!/usr/bin/env python3
"""Capture Reconcile triage-queue screenshots (stage → auto-match → queue → confirm)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path('/tmp/lt-reconcile-demo')
LOCAL_SHOT_DIR = ROOT / 'tmp' / 'screenshots' / 'reconcile'
DOCS_SHOT_DIR = ROOT / 'docs' / 'screenshots' / 'reconcile'
BASE = 'http://127.0.0.1:5077'
USER = 'ui'
PASS = 'ui'
PORT = 5077
MONTH = '2024-06'


def wait_ready(url: str, timeout: float = 30.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001
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


def seed_data() -> dict:
    manuals = [
        {
            'id': 'm-lunch',
            'date': f'{MONTH}-05',
            'month': MONTH,
            'report_month': MONTH,
            'description': 'Lunch with Sam',
            'amount': 10.0,
            'direction': 'outgoing',
            'category': 'dining',
            'source': 'manual',
        },
        {
            'id': 'm-fuel',
            'date': f'{MONTH}-06',
            'month': MONTH,
            'report_month': MONTH,
            'description': 'Fuel',
            'amount': 25.0,
            'direction': 'outgoing',
            'category': 'transport',
            'source': 'manual',
        },
        {
            'id': 'm-coffee',
            'date': f'{MONTH}-07',
            'month': MONTH,
            'report_month': MONTH,
            'description': 'Coffee',
            'amount': 3.5,
            'direction': 'outgoing',
            'category': 'dining',
            'source': 'manual',
        },
        {
            'id': 'm-cash',
            'date': f'{MONTH}-12',
            'month': MONTH,
            'report_month': MONTH,
            'description': 'Cash lunch',
            'amount': 8.0,
            'direction': 'outgoing',
            'category': 'dining',
            'source': 'manual',
        },
    ]

    def row(rid, day, desc, amount, direction='outgoing', category='dining'):
        return {
            'id': rid,
            'date': f'{MONTH}-{day:02d}',
            'description': desc,
            'direction': direction,
            'amount': amount,
            'category': category,
            'include': True,
            'manual_match': None,
            'transfer_pair': None,
            'ledger_duplicate': False,
        }

    return {
        'loans': {},
        'users': {
            USER: {
                'spending': {
                    'statements': [],
                    'transactions': manuals,
                    'monthly_insights': {MONTH: {'income_total': 0, 'outgoing_total': 0, 'net': 0}},
                    'classification_overrides': {},
                    'classification_cache': {},
                    'daily_budget': {},
                    'reconcile_sessions': {
                        MONTH: {
                            'report_month': MONTH,
                            'status': 'staging',
                            'auto_match_ran': False,
                            'uploads': [
                                {
                                    'id': 'u-monzo',
                                    'file_name': 'monzo-june.csv',
                                    'bank_source': 'Monzo',
                                    'rows': [
                                        row('r-pret', 5, 'PRET A MANGER', 10.0),
                                        row('r-out', 8, 'To Amex', 100.0, 'outgoing', 'other'),
                                        row('r-bus', 9, 'TFL TRAVEL', 2.9, 'outgoing', 'transport'),
                                    ],
                                },
                                {
                                    'id': 'u-amex',
                                    'file_name': 'amex-june.pdf',
                                    'bank_source': 'Amex',
                                    'rows': [
                                        row('r-shell', 6, 'SHELL GARAGE', 25.0, 'outgoing', 'transport'),
                                        row('r-in', 8, 'From Monzo', 100.0, 'incoming', None),
                                        row('r-costa', 7, 'COSTA COFFEE', 3.5),
                                    ],
                                },
                            ],
                            'excluded_manual_ids': [],
                            'kept_manual_ids': [],
                            'reviewed_unclaimed': False,
                        }
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
            page.wait_for_selector('#home-open-reconcile')
            save_locator(page.locator('.home-reconcile-cta'), '01-home-reconcile-cta.png')

            page.goto(f'{BASE}/spending/reconcile?month={MONTH}')
            page.wait_for_selector('#reconcile-upload-list .reconcile-upload-item')
            expect(page.locator('.reconcile-upload-item')).to_have_count(2)
            save(page, '02-staged-uploads.png')
            save_locator(page.locator('.reconcile-panel--upload'), '03-uploads-strip.png')

            page.click('#reconcile-auto-match-btn')
            page.wait_for_function(
                "() => document.getElementById('reconcile-status-chip')?.dataset.status === 'matched'"
            )
            page.wait_for_timeout(400)
            save(page, '04-after-auto-match.png')
            save_locator(page.locator('#reconcile-tally'), '05-totals-bar.png')

            page.wait_for_selector('#reconcile-focus')
            save_locator(page.locator('#reconcile-workspace'), '06-triage-workspace.png')

            # First queue item (usually auto-matched)
            save_locator(page.locator('#reconcile-focus'), '07-focus-matched.png')
            page.click('[data-action="accept-queue"]')
            page.wait_for_timeout(300)

            # Advance until unmatched or transfer visible
            for _ in range(8):
                focus = page.locator('#reconcile-focus').inner_text()
                if 'Transfer pair' in focus or 'Unmatched' in focus:
                    break
                if page.locator('[data-action="accept-queue"]').count():
                    page.click('[data-action="accept-queue"]')
                    page.wait_for_timeout(250)
                elif page.locator('[data-queue-next]:not([disabled])').count():
                    page.click('[data-queue-next]:not([disabled])')
                    page.wait_for_timeout(250)
                else:
                    break

            if 'Transfer pair' in page.locator('#reconcile-focus').inner_text():
                save_locator(page.locator('#reconcile-workspace'), '08-transfer-pair.png')
                page.click('[data-action="accept-queue"]')
                page.wait_for_timeout(300)

            # Find unmatched row if any
            for _ in range(6):
                if 'Unmatched' in page.locator('#reconcile-focus').inner_text():
                    save_locator(page.locator('#reconcile-workspace'), '09-unmatched-suggestions.png')
                    if page.locator('[data-action="accept-queue"]').count():
                        page.click('[data-action="accept-queue"]')
                    page.wait_for_timeout(300)
                    break
                if page.locator('[data-queue-next]:not([disabled])').count():
                    page.click('[data-queue-next]:not([disabled])')
                    page.wait_for_timeout(250)
                else:
                    break

            # Bulk accept remaining auto-matched if button visible
            if page.locator('#reconcile-bulk-accept-btn:not(.hidden)').count():
                page.click('#reconcile-bulk-accept-btn')
                page.wait_for_timeout(300)

            # Confirm opens manual phase
            page.click('#reconcile-confirm-btn')
            page.wait_for_selector('#reconcile-phase-manuals:not(.hidden)', timeout=10000)
            save(page, '10-unclaimed-manuals.png')

            # Exclude one unclaimed manual
            if page.locator('[data-exclude-manual="m-cash"]').count():
                page.click('[data-exclude-manual="m-cash"]')
                page.wait_for_timeout(400)

            page.check('#reconcile-reviewed-unclaimed')
            page.click('#reconcile-to-summary-btn')
            page.wait_for_selector('#reconcile-phase-summary:not(.hidden)')
            save(page, '11-pre-confirm-summary.png')

            page.click('#reconcile-final-confirm-btn')
            page.wait_for_function(
                "() => document.getElementById('reconcile-status-chip')?.dataset.status === 'imported'",
                timeout=15000,
            )
            page.wait_for_timeout(400)
            save(page, '12-after-confirm.png')

            mobile = browser.new_page(viewport={'width': 390, 'height': 844}, device_scale_factor=2)
            login(mobile)
            mobile.goto(f'{BASE}/')
            mobile.wait_for_selector('#home-open-reconcile')
            save(mobile, '13-home-mobile.png')
            mobile.goto(f'{BASE}/spending/reconcile?month={MONTH}')
            mobile.wait_for_selector('#reconcile-heading')
            save(mobile, '14-reconcile-mobile-imported.png')

            browser.close()
        print('Screenshots written to', DOCS_SHOT_DIR)
        return 0
    finally:
        server.kill()
        try:
            server.wait(timeout=5)
        except Exception:
            pass


if __name__ == '__main__':
    raise SystemExit(main())
