#!/usr/bin/env python3
"""Capture Daily Budget screenshots for bill-pull diff UX."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path('/tmp/lt-bill-pull-diff-demo')
SHOT_DIR = Path('/tmp/cursor/artifacts/screenshots')
LOCAL_SHOT_DIR = ROOT / 'tmp' / 'screenshots' / 'bill-pull-diff'
DOCS_SHOT_DIR = ROOT / 'docs' / 'screenshots' / 'bill-pull-diff'
BASE = 'http://127.0.0.1:5071'
USER = 'ui'
PASS = 'ui'
PORT = 5071


def wait_ready(url: str, timeout: float = 25.0) -> None:
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
    for dest in (SHOT_DIR, LOCAL_SHOT_DIR, DOCS_SHOT_DIR):
        dest.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(dest / name), full_page=full_page)


def save_locator(locator, name: str) -> None:
    for dest in (SHOT_DIR, LOCAL_SHOT_DIR, DOCS_SHOT_DIR):
        dest.mkdir(parents=True, exist_ok=True)
        locator.screenshot(path=str(dest / name))


def _tx(date: str, amount: float, description: str, category: str, *, direction='outgoing'):
    rm = date[:7]
    return {
        'id': str(uuid.uuid4()),
        'date': date,
        'month': rm,
        'report_month': rm,
        'description': description,
        'amount': amount,
        'direction': direction,
        'category': category,
        'confidence': 1.0,
        'rationale': 'demo',
        'source': 'statement',
        'created_at': date + 'T12:00:00Z',
        'fingerprint': f'demo|{description}|{date}|{amount}',
    }


def seed_data() -> dict:
    # Existing curated bills (baseline for diff):
    # - Rent 1000 → pull will show 1100 (changed)
    # - Spotify 10.99 → same
    # - Gym 30 → removed by pull (no matching txs)
    # Pull will also add Netflix (new) and Octopus (new / signal)
    txs = [
        _tx('2024-01-01', 1000.0, 'ACME LETTINGS', 'housing'),
        _tx('2024-02-01', 1000.0, 'ACME LETTINGS', 'housing'),
        _tx('2024-03-01', 1100.0, 'ACME LETTINGS', 'housing'),
        _tx('2024-01-05', 10.99, 'SPOTIFY', 'other'),
        _tx('2024-02-05', 10.99, 'SPOTIFY', 'other'),
        _tx('2024-03-05', 10.99, 'SPOTIFY', 'other'),
        _tx('2024-01-12', 70.0, 'OCTOPUS ENERGY', 'other'),
        _tx('2024-02-14', 88.0, 'OCTOPUS ENERGY', 'other'),
        _tx('2024-03-13', 95.0, 'OCTOPUS ENERGY', 'other'),
        _tx('2024-01-08', 15.99, 'NETFLIX.COM', 'other'),
        _tx('2024-02-08', 15.99, 'NETFLIX.COM', 'other'),
        _tx('2024-03-08', 15.99, 'NETFLIX.COM', 'other'),
        _tx('2024-03-25', 2800.0, 'ACME PAYROLL', None, direction='incoming'),
        _tx('2024-03-18', 40.0, 'SAINSBURY', 'groceries'),
    ]
    return {
        'loans': {},
        'users': {
            USER: {
                'spending': {
                    'statements': [
                        {
                            'id': 'stmt-2024-03',
                            'report_month': '2024-03',
                            'period_start': '2024-03-01',
                            'period_end': '2024-03-31',
                            'file_name': 'demo.csv',
                        }
                    ],
                    'transactions': txs,
                    'monthly_insights': {
                        '2024-03': {'income_total': 2800.0},
                        '2024-02': {'income_total': 2800.0},
                        '2024-01': {'income_total': 2800.0},
                    },
                    'classification_overrides': {},
                    'classification_cache': {},
                    'daily_budget': {
                        'plan': {
                            'income_monthly': 2800.0,
                            'bills_monthly': 1040.99,
                            'savings_percent': 10.0,
                            'daily_mode': 'fixed',
                            'pay_day': 1,
                            'tracking_from': '2024-03-01',
                            'underspend_priority': 'debt_first',
                            'source_month': '2024-02',
                            'bill_items': [
                                {
                                    'id': 'b-rent',
                                    'label': 'ACME LETTINGS',
                                    'amount': 1000.0,
                                    'category': 'housing',
                                    'included': True,
                                    'source': 'manual',
                                },
                                {
                                    'id': 'b-spot',
                                    'label': 'SPOTIFY',
                                    'amount': 10.99,
                                    'category': 'subscriptions',
                                    'included': True,
                                    'source': 'manual',
                                },
                                {
                                    'id': 'b-gym',
                                    'label': 'GYM DD',
                                    'amount': 30.0,
                                    'category': 'other',
                                    'included': True,
                                    'source': 'manual',
                                },
                            ],
                            'updated_at': '2024-03-01T00:00:00Z',
                        },
                        'goals': [],
                        'overspend_decisions': {},
                        'overspend_debt': None,
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


def open_plan(page) -> None:
    page.goto(f'{BASE}/spending/daily')
    page.click('.db-panel-tab[data-panel="plan"]')
    page.wait_for_selector('#plan-pull-btn')
    page.wait_for_selector('#plan-bill-list .db-bill-item')


def main() -> int:
    for d in (SHOT_DIR, LOCAL_SHOT_DIR, DOCS_SHOT_DIR, APP_DIR):
        d.mkdir(parents=True, exist_ok=True)

    (APP_DIR / 'data.json').write_text(json.dumps(seed_data(), indent=2) + '\n', encoding='utf-8')

    env = os.environ.copy()
    env['FINANCE_TRACKER_USERS'] = f'{USER}:{PASS}'
    env['PYTHONPATH'] = str(ROOT) + (
        os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else ''
    )

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
            page = browser.new_page(
                viewport={'width': 390, 'height': 844},
                device_scale_factor=2,
            )
            login(page)
            open_plan(page)

            # 1) Starting curated list
            expect(page.locator('#plan-bill-list')).to_contain_text('ACME LETTINGS')
            expect(page.locator('#plan-bill-list')).to_contain_text('GYM DD')
            save(page, '01-plan-before-pull.png')
            save_locator(page.locator('.db-pull'), '01b-bills-before-detail.png')

            # 2) Select month + pull
            page.select_option('#plan-source-month', '2024-03')
            save_locator(page.locator('.db-pull-toolbar'), '02-month-selected.png')
            page.click('#plan-pull-btn')
            page.wait_for_selector('#plan-bill-diff-summary:not(.hidden)')
            expect(page.locator('#plan-bill-diff-summary')).to_contain_text('new')
            expect(page.locator('#plan-bill-diff-summary')).to_contain_text('changed')
            expect(page.locator('#plan-bill-diff-summary')).to_contain_text('removed')
            expect(page.locator('.db-bill-item--new').first).to_be_visible()
            expect(page.locator('.db-bill-item--changed').first).to_be_visible()
            expect(page.locator('#plan-bill-removed-wrap:not(.hidden)')).to_be_visible()
            expect(page.locator('#plan-bill-removed')).to_contain_text('GYM DD')

            save(page, '03-plan-after-pull-diff.png')
            save_locator(page.locator('#plan-bill-diff-summary'), '03b-diff-summary.png')
            save_locator(page.locator('#plan-bill-list'), '03c-bill-list-badges.png')
            save_locator(page.locator('#plan-bill-removed-wrap'), '03d-removed-section.png')

            # 3) Restore removed gym
            page.click('#plan-bill-removed .db-bill-restore')
            page.wait_for_function(
                """() => {
                  const list = document.getElementById('plan-bill-list');
                  return list && (list.textContent || '').includes('GYM DD');
                }"""
            )
            expect(page.locator('#plan-bill-list')).to_contain_text('GYM DD')
            save(page, '04-after-restore-gym.png')
            save_locator(page.locator('.db-pull'), '04b-after-restore-detail.png')

            # 4) Save plan
            page.click('#plan-save-btn')
            page.wait_for_selector('#plan-save-feedback:not(.hidden)')
            expect(page.locator('#plan-save-feedback')).to_contain_text('Plan saved')
            # Diff chrome clears after fillPlanForm from the save response
            expect(page.locator('#plan-bill-diff-summary')).to_be_hidden()
            save(page, '05-after-save.png')

            desktop = browser.new_page(viewport={'width': 1280, 'height': 900})
            login(desktop)
            open_plan(desktop)
            desktop.select_option('#plan-source-month', '2024-03')
            desktop.click('#plan-pull-btn')
            desktop.wait_for_selector('#plan-bill-diff-summary:not(.hidden)')
            save(desktop, '06-desktop-after-pull.png')

            browser.close()
        print('Screenshots written to', DOCS_SHOT_DIR)
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == '__main__':
    raise SystemExit(main())
