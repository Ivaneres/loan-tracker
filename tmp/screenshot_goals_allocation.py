#!/usr/bin/env python3
"""Capture Daily Budget Goals allocation-bar playthrough screenshots."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path("/tmp/lt-goals-allocation-demo")
SHOT_DIR = Path("/tmp/cursor/artifacts/screenshots")
LOCAL_SHOT_DIR = ROOT / "tmp" / "screenshots" / "goals-allocation"
DOCS_SHOT_DIR = ROOT / "docs" / "screenshots" / "goals-allocation"
BASE = "http://127.0.0.1:5071"
USER = "ui"
PASS = "ui"
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
    raise RuntimeError(f"Server not ready at {url}: {last}")


def save(page, name: str, *, full_page: bool = True) -> None:
    for dest in (SHOT_DIR, LOCAL_SHOT_DIR, DOCS_SHOT_DIR):
        dest.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(dest / name), full_page=full_page)


def save_locator(locator, name: str) -> None:
    for dest in (SHOT_DIR, LOCAL_SHOT_DIR, DOCS_SHOT_DIR):
        dest.mkdir(parents=True, exist_ok=True)
        locator.screenshot(path=str(dest / name))


def month_start(d: date) -> date:
    return d.replace(day=1)


def prev_month_start(d: date) -> date:
    return month_start(d - timedelta(days=1))


def month_end(d: date) -> date:
    return d.replace(day=monthrange(d.year, d.month)[1])


def cycle_label(start: date, end: date) -> str:
    return f"{start.day} {start.strftime('%b')} – {end.day} {end.strftime('%b')}"


def _tx(day: date, description: str, amount: float, category: str = "dining") -> dict:
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
        "rationale": "demo allocation",
        "source": "manual",
        "created_at": day.isoformat() + "T12:00:00Z",
        "fingerprint": f"demo|{tx_id}",
    }


def seed_data(today: date) -> dict:
    live_start = month_start(today)
    prev_start = prev_month_start(live_start)
    older_start = prev_month_start(prev_start)
    tracking = older_start
    return {
        "users": {
            USER: {
                "spending": {
                    "transactions": [
                        _tx(older_start + timedelta(days=10), "Holiday", 1850.0, "entertainment"),
                        _tx(prev_start + timedelta(days=8), "Weekend away", 1350.0, "entertainment"),
                        _tx(live_start, "Groceries", 400.0, "groceries"),
                    ],
                    "statements": [],
                    "monthly_insights": {},
                    "daily_budget": {
                        "plan": {
                            "income_monthly": 2500,
                            "bills_monthly": 800,
                            "savings_percent": 20,
                            "daily_mode": "fixed",
                            "pay_day": 1,
                            "tracking_from": tracking.isoformat(),
                            "underspend_priority": "debt_first",
                            "bill_items": [
                                {
                                    "id": "rent",
                                    "label": "Rent",
                                    "amount": 800,
                                    "category": "housing",
                                    "included": True,
                                    "source": "manual",
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
        "loans": {},
    }


def login(page) -> None:
    page.goto(f"{BASE}/login")
    page.fill("#username", USER)
    page.fill("#password", PASS)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda u: "/login" not in u)


def open_goals(page) -> None:
    page.goto(f"{BASE}/spending/daily")
    page.wait_for_selector("#db-remaining")
    page.locator('.db-panel-tab[data-panel="goals"]').click()
    page.wait_for_selector("#panel-goals:not(.hidden)")
    page.wait_for_selector("#goals-alloc-bar")
    page.wait_for_function(
        """() => {
          const bar = document.getElementById('goals-alloc-bar');
          return bar && bar.children.length > 0;
        }"""
    )


def main() -> int:
    for d in (SHOT_DIR, LOCAL_SHOT_DIR, DOCS_SHOT_DIR, APP_DIR):
        d.mkdir(parents=True, exist_ok=True)

    today = date.today()
    live_start = month_start(today)
    prev_start = prev_month_start(live_start)
    older_start = prev_month_start(prev_start)
    prev_label = cycle_label(prev_start, month_end(prev_start))
    older_label = cycle_label(older_start, month_end(older_start))
    live_label = cycle_label(live_start, month_end(live_start))

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
            mobile.wait_for_selector("#db-remaining")
            save(mobile, "01-today-home.png")

            open_goals(mobile)
            expect(mobile.locator("#goals-cycle-chips")).to_contain_text(live_label)
            expect(mobile.locator("#goals-alloc-spent")).to_contain_text("£400.00 used")
            save(mobile, "02-goals-live-on-track.png")
            save_locator(mobile.locator("#goals-alloc"), "03-alloc-bar-on-track.png")
            save_locator(mobile.locator("#goals-cycle-picker"), "04-cycle-chips.png")

            mobile.locator("#goals-cycle-chips .db-date-chip", has_text=prev_label).click()
            mobile.wait_for_function(
                "() => (document.getElementById('goals-alloc-spent')?.textContent || '').includes('£1,350.00')"
            )
            expect(mobile.locator("#goals-cycle-hint")).to_be_visible()
            save(mobile, "05-goals-previous-into-savings.png")
            save_locator(mobile.locator("#goals-alloc"), "06-alloc-bar-into-savings.png")

            mobile.locator("#goals-cycle-chips .db-date-chip", has_text=older_label).click()
            mobile.wait_for_function(
                "() => (document.getElementById('goals-alloc-spent')?.textContent || '').includes('£1,850.00')"
            )
            expect(mobile.locator("#goals-alloc-rows")).to_contain_text("Past capability")
            save_locator(mobile.locator("#goals-alloc"), "07-alloc-bar-past-capability.png")

            mobile.locator("#goals-cycle-chips .db-date-chip", has_text=live_label).click()
            mobile.wait_for_function(
                "() => (document.getElementById('goals-alloc-spent')?.textContent || '').includes('£400.00')"
            )
            mobile.fill("#goal-name", "Emergency fund")
            mobile.fill("#goal-target", "500")
            mobile.locator('#goal-form button[type="submit"]').click()
            mobile.wait_for_selector("#goals-list .db-goal-card")
            expect(mobile.locator("#goals-list")).to_contain_text("Emergency fund")
            save(mobile, "08-goal-added.png")

            desktop.goto(f"{BASE}/spending/daily")
            desktop.wait_for_selector("#db-remaining")
            desktop.locator('.db-panel-tab[data-panel="goals"]').click()
            desktop.wait_for_selector("#panel-goals:not(.hidden)")
            desktop.wait_for_function(
                """() => {
                  const bar = document.getElementById('goals-alloc-bar');
                  return bar && bar.children.length > 0;
                }"""
            )
            expect(desktop.locator("#goals-alloc-spent")).to_contain_text("£400.00 used")
            save(desktop, "09-desktop-goals-live.png")
            save_locator(desktop.locator("#goals-alloc"), "10-desktop-alloc-bar.png")

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
