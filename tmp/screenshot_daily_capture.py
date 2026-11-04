#!/usr/bin/env python3
"""Step-by-step playthrough for phone captures on the Daily screen.

Flow: empty prompt → in-app setup page (token reveal + test button) → a capture
arrives → tap fills the form → Add spend → phone POSTs two taps → dismiss one.
Mobile + desktop crops.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import date
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path("/tmp/lt-daily-capture-demo")
SHOT_DIR = Path("/tmp/cursor/artifacts/screenshots")
LOCAL_SHOT_DIR = ROOT / "tmp" / "screenshots" / "daily-capture"
DOCS_SHOT_DIR = ROOT / "docs" / "screenshots" / "daily-capture"
BASE = "http://127.0.0.1:5069"
USER = "ui"
PASS = "ui"
TOKEN = "demo-capture-token-please-rotate"
PORT = 5069


def wait_ready(url: str, timeout: float = 25.0) -> None:
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
    raise RuntimeError(f"Server not ready at {url}: {last}")


def save(page, name: str, *, full_page: bool = True) -> None:
    for dest in (SHOT_DIR, LOCAL_SHOT_DIR, DOCS_SHOT_DIR):
        dest.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(dest / name), full_page=full_page)


def save_locator(locator, name: str) -> None:
    for dest in (SHOT_DIR, LOCAL_SHOT_DIR, DOCS_SHOT_DIR):
        dest.mkdir(parents=True, exist_ok=True)
        locator.screenshot(path=str(dest / name))


def seed_data(today: date) -> dict:
    return {
        "loans": {},
        "users": {
            USER: {
                "spending": {
                    "statements": [],
                    "transactions": [],
                    "monthly_insights": {},
                    "classification_overrides": {},
                    "classification_cache": {},
                    "daily_budget": {
                        "plan": {
                            "income_monthly": 3000.0,
                            "bills_monthly": 1000.0,
                            "savings_percent": 10.0,
                            "daily_mode": "fixed",
                            "pay_day": 1,
                            "tracking_from": today.replace(day=1).isoformat(),
                            "underspend_priority": "debt_first",
                            "source_month": None,
                            "bill_items": [
                                {
                                    "label": "Rent",
                                    "amount": 1000.0,
                                    "category": "housing",
                                    "included": True,
                                }
                            ],
                            "updated_at": today.isoformat() + "T00:00:00Z",
                        },
                        "goals": [],
                        "overspend_decisions": {},
                        "overspend_debt": None,
                    },
                    "daily_captures": [],
                }
            }
        },
    }


def phone_capture(**payload) -> dict:
    """Simulate the phone automation: POST with the bearer token."""
    body = {"source": "wallet", "capture_id": str(uuid.uuid4())}
    body.update(payload)
    req = urllib.request.Request(
        f"{BASE}/api/spending/daily/capture",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.load(resp)


def login(page) -> None:
    page.goto(f"{BASE}/login")
    page.fill("#username", USER)
    page.fill("#password", PASS)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda u: "/login" not in u)


def main() -> int:
    for d in (SHOT_DIR, LOCAL_SHOT_DIR, DOCS_SHOT_DIR, APP_DIR):
        d.mkdir(parents=True, exist_ok=True)

    today = date.today()
    (APP_DIR / "data.json").write_text(
        json.dumps(seed_data(today), indent=2) + "\n", encoding="utf-8"
    )

    env = os.environ.copy()
    env["FINANCE_TRACKER_USERS"] = f"{USER}:{PASS}"
    env["DAILY_CAPTURE_TOKENS"] = f"{USER}:{TOKEN}"
    env["PYTHONPATH"] = str(ROOT) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )

    server = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                f"sys.path.insert(0, {str(ROOT)!r}); "
                "import app; "
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
        wait_ready(f"{BASE}/login")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            mobile = browser.new_page(
                viewport={"width": 390, "height": 844}, device_scale_factor=2
            )
            desktop = browser.new_page(viewport={"width": 1280, "height": 900})

            login(mobile)
            login(desktop)

            # 1. Baseline: nothing captured yet, prompt to set up.
            mobile.goto(f"{BASE}/spending/daily")
            mobile.wait_for_selector("#db-captures-note")
            expect(mobile.locator("#db-captures-note")).to_contain_text("Set up phone captures")
            save(mobile, "01-mobile-empty-prompt.png")
            save_locator(mobile.locator("#db-captures"), "01b-empty-panel.png")

            # 2. In-app setup page (how to enable MacroDroid).
            mobile.locator('a.db-captures-setup').click()
            mobile.wait_for_url(lambda u: u.endswith("/spending/daily/captures"))
            mobile.wait_for_selector("#capture-heading")
            save(mobile, "02-mobile-setup-page.png")

            # 2b. Token reveal.
            mobile.locator("#capture-token-reveal").click()
            mobile.wait_for_function(
                f"() => document.getElementById('capture-token').textContent === '{TOKEN}'"
            )
            save_locator(mobile.locator(".capture-token"), "02b-setup-token-revealed.png")

            # 2c. Send a test capture from the page.
            mobile.locator("#capture-test").click()
            mobile.wait_for_function(
                """() => (document.getElementById('capture-test-msg')?.textContent || '')
                  .includes('Sent')"""
            )
            save_locator(
                mobile.locator(".capture-step").last, "02c-setup-test-sent.png"
            )

            # 3. Back on Daily the test capture is waiting.
            mobile.goto(f"{BASE}/spending/daily")
            mobile.wait_for_selector("#db-captures-list .db-capture-card")
            expect(mobile.locator("#db-captures-list")).to_contain_text("Test capture")
            save(mobile, "03-mobile-capture-arrived.png")
            save_locator(mobile.locator("#db-captures"), "03b-capture-panel.png")

            # 4. Tap it -> entry form pre-filled (amount, title, date).
            mobile.locator(".db-capture-main").first.click()
            mobile.wait_for_function(
                "() => document.getElementById('db-title').value === 'Test capture'"
            )
            expect(mobile.locator("#db-amount")).to_have_value("1.00")
            save_locator(mobile.locator(".db-today-compose"), "04-tapped-form-filled.png")

            # 5. Pick a category and Add spend -> capture accepted, spend listed.
            mobile.locator('.db-cat[data-category="groceries"]').click()
            mobile.locator("#db-add-btn").click()
            mobile.wait_for_function(
                """() => document.querySelectorAll('#db-captures-list .db-capture-card').length === 0"""
            )
            expect(mobile.locator("#db-today-list")).to_contain_text("Test capture")
            save(mobile, "05-mobile-after-add.png")
            save_locator(mobile.locator(".db-today-list-wrap"), "05b-spend-listed.png")

            # 6. Phone posts two taps; dismiss one via the × button.
            phone_capture(amount=3.4, title="PRET A MANGER", source="wallet")
            phone_capture(amount=1.75, title="TFL TRAVEL CH", source="bank")
            mobile.reload()
            mobile.wait_for_function(
                """() => document.querySelectorAll('#db-captures-list .db-capture-card').length === 2"""
            )
            expect(mobile.locator("#db-captures-count")).to_contain_text("2 new")
            save_locator(mobile.locator("#db-captures"), "06-two-captures.png")
            mobile.locator('[data-capture-dismiss]').first.click()
            mobile.wait_for_function(
                """() => document.querySelectorAll('#db-captures-list .db-capture-card').length === 1"""
            )
            save(mobile, "06b-mobile-dismissed.png")

            # 7. Desktop view of the panel.
            phone_capture(amount=8.9, title="COSTA COFFEE", source="wallet")
            desktop.goto(f"{BASE}/spending/daily")
            desktop.wait_for_selector("#db-captures-list .db-capture-card")
            save(desktop, "07-desktop-captures.png")

            browser.close()

        print(f"Screenshots written to {DOCS_SHOT_DIR}")
        print(f"Also: {SHOT_DIR} and {LOCAL_SHOT_DIR}")
        return 0
    except Exception:
        try:
            if server.poll() is None:
                server.terminate()
            out, _ = server.communicate(timeout=3)
            if out:
                print("--- server log ---", file=sys.stderr)
                print(out[-5000:], file=sys.stderr)
        except Exception:
            pass
        raise
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
