#!/usr/bin/env python3
"""Capture Daily Budget screenshots for category title suggestion chips."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path("/tmp/lt-title-suggestions-demo")
SHOT_DIR = Path("/tmp/cursor/artifacts/screenshots")
LOCAL_SHOT_DIR = ROOT / "tmp" / "screenshots" / "title-suggestions"
DOCS_SHOT_DIR = ROOT / "docs" / "screenshots" / "title-suggestions"
BASE = "http://127.0.0.1:5068"
USER = "ui"
PASS = "ui"
PORT = 5068


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
    raise RuntimeError(f"Server not ready at {url}: {last}")


def save(page, name: str, *, full_page: bool = True) -> None:
    for dest in (SHOT_DIR, LOCAL_SHOT_DIR, DOCS_SHOT_DIR):
        dest.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(dest / name), full_page=full_page)


def save_locator(locator, name: str) -> None:
    for dest in (SHOT_DIR, LOCAL_SHOT_DIR, DOCS_SHOT_DIR):
        dest.mkdir(parents=True, exist_ok=True)
        locator.screenshot(path=str(dest / name))


def _tx(day: date, description: str, category: str, amount: float = 4.5) -> dict:
    tx_id = str(uuid.uuid4())
    return {
        "id": tx_id,
        "date": day.isoformat(),
        "month": day.strftime("%Y-%m"),
        "report_month": day.strftime("%Y-%m"),
        "description": description,
        "amount": amount,
        "direction": "outgoing",
        "category": category,
        "confidence": 1.0,
        "rationale": "demo title suggestion",
        "source": "manual",
        "created_at": day.isoformat() + "T12:00:00Z",
        "fingerprint": f"demo|{tx_id}",
    }


def seed_data(today: date) -> dict:
    # Enough history that dining → Coffee / Lunch / Snack and transport → Bus / Train / Uber.
    days = [today - timedelta(days=n) for n in range(1, 12)]
    txs = [
        _tx(days[0], "Coffee", "dining", 3.2),
        _tx(days[1], "Coffee", "dining", 3.4),
        _tx(days[2], "coffee", "dining", 3.1),
        _tx(days[3], "Lunch", "dining", 8.5),
        _tx(days[4], "Lunch", "dining", 9.0),
        _tx(days[5], "Snack", "dining", 2.5),
        _tx(days[6], "Dining", "dining", 12.0),  # bare category label — should be skipped
        _tx(days[7], "Bus", "transport", 2.0),
        _tx(days[8], "Bus", "transport", 2.0),
        _tx(days[9], "Train", "transport", 4.5),
        _tx(days[10], "Uber", "transport", 11.0),
    ]
    return {
        "loans": {},
        "users": {
            USER: {
                "spending": {
                    "statements": [],
                    "transactions": txs,
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
                            "tracking_from": (today.replace(day=1)).isoformat(),
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
                }
            }
        },
    }


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
        json.dumps(seed_data(today), indent=2) + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["FINANCE_TRACKER_USERS"] = f"{USER}:{PASS}"
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
                viewport={"width": 390, "height": 844},
                device_scale_factor=2,
            )
            desktop = browser.new_page(viewport={"width": 1280, "height": 900})

            login(mobile)
            login(desktop)

            mobile.goto(f"{BASE}/spending/daily")
            mobile.wait_for_selector("#db-title-suggestions:not(.hidden)")
            expect(mobile.locator("#db-title-suggestions")).to_contain_text("Coffee")
            expect(mobile.locator("#db-title-suggestions")).to_contain_text("Lunch")
            expect(mobile.locator("#db-title-suggestions")).to_contain_text("Snack")
            save(mobile, "01-mobile-dining-suggestions.png")
            save_locator(
                mobile.locator("#db-entry-form"),
                "01b-entry-form-dining.png",
            )

            mobile.locator('.db-cat[data-category="transport"]').click()
            mobile.wait_for_function(
                """() => {
                  const wrap = document.getElementById('db-title-suggestions');
                  if (!wrap || wrap.classList.contains('hidden')) return false;
                  const text = wrap.textContent || '';
                  return text.includes('Bus') && text.includes('Train') && text.includes('Uber');
                }""",
                timeout=10000,
            )
            save(mobile, "02-mobile-transport-suggestions.png")
            save_locator(
                mobile.locator("#db-entry-form"),
                "02b-entry-form-transport.png",
            )

            # Tap a suggestion so the selected chip state is visible.
            mobile.locator('.db-title-chip', has_text="Coffee").count()  # warm locator
            mobile.locator('.db-cat[data-category="dining"]').click()
            mobile.wait_for_function(
                """() => (document.getElementById('db-title-suggestions')?.textContent || '')
                  .includes('Coffee')""",
                timeout=10000,
            )
            mobile.locator("#db-title-suggestions .db-title-chip", has_text="Coffee").click()
            mobile.wait_for_function(
                "() => document.getElementById('db-title').value === 'Coffee'"
            )
            expect(
                mobile.locator("#db-title-suggestions .db-title-chip--selected")
            ).to_contain_text("Coffee")
            save_locator(
                mobile.locator(".db-field").filter(has=mobile.locator("#db-title")),
                "03-title-chip-selected.png",
            )

            desktop.goto(f"{BASE}/spending/daily")
            desktop.wait_for_selector("#db-title-suggestions:not(.hidden)")
            expect(desktop.locator("#db-title-suggestions")).to_contain_text("Coffee")
            save(desktop, "04-desktop-dining-suggestions.png")

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
