#!/usr/bin/env python3
"""
Thorough Amex / amount-sign evidence:
1. Parse a matrix of CSV samples locally → RESULTS.md + evidence.json
2. Real (unmocked) Home import previews for Amex + Revolut CSVs → screenshots
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as app_mod  # noqa: E402

APP_DIR = Path("/tmp/lt-amex-amount-sign-demo")
SHOT_DIR = Path("/tmp/cursor/artifacts/screenshots")
LOCAL_SHOT_DIR = ROOT / "tmp" / "screenshots" / "amex-amount-sign"
DOCS_SHOT_DIR = ROOT / "docs" / "screenshots" / "amex-amount-sign"
BASE = "http://127.0.0.1:5077"
USER = "ui"
PASS = "ui"
PORT = 5077

MONTH = "2026-07"
PERIOD_START = "2026-07-01"
PERIOD_END = "2026-07-31"


SAMPLES: list[dict] = [
    {
        "id": "user-reported-amex",
        "label": "User-reported Amex 2-row sample",
        "expect_sign": "positive_is_outgoing",
        "expect": [
            ("GO AHEAD GROUP          LONDON", "outgoing", 36.6),
            ("SAINSBURY'S SUPERMARKET CAMBRIDGE", "outgoing", 19.55),
        ],
        "csv": (
            "Date,Description,Amount\n"
            "31/07/2026,GO AHEAD GROUP          LONDON,36.60\n"
            "31/07/2026,SAINSBURY'S SUPERMARKET CAMBRIDGE,19.55\n"
        ),
    },
    {
        "id": "amex-with-payment",
        "label": "Amex charges + payment credit",
        "expect_sign": "positive_is_outgoing",
        "expect": [
            ("TESCO STORES", "outgoing", 24.99),
            ("PAYMENT RECEIVED - THANK YOU", "incoming", 150.0),
            ("COFFEE SHOP", "outgoing", 4.5),
        ],
        "csv": (
            "Date,Description,Amount\n"
            "01/07/2026,TESCO STORES,24.99\n"
            "02/07/2026,PAYMENT RECEIVED - THANK YOU,-150.00\n"
            "03/07/2026,COFFEE SHOP,4.50\n"
        ),
    },
    {
        "id": "amex-currency-parens",
        "label": "Amex with £ symbols, commas, paren credit",
        "expect_sign": "positive_is_outgoing",
        "expect": [
            ("WAITROSE", "outgoing", 12.4),
            ("AMAZON.CO.UK", "outgoing", 1234.56),
            ("MERCHANT REFUND", "incoming", 12.0),
        ],
        "csv": (
            "Date,Description,Amount\n"
            "01/07/2026,WAITROSE,£12.40\n"
            '02/07/2026,AMAZON.CO.UK,"1,234.56"\n'
            "03/07/2026,MERCHANT REFUND,(12.00)\n"
        ),
    },
    {
        "id": "amex-extra-columns",
        "label": "Amex-style Card Member / Account # columns",
        "expect_sign": "positive_is_outgoing",
        "expect": [
            ("TESCO STORES 2897", "outgoing", 28.91),
            ("TFL TRAVEL CHARGE", "outgoing", 3.5),
            ("PAYMENT RECEIVED - THANK YOU", "incoming", 200.0),
        ],
        "csv": (
            "Date,Description,Card Member,Account #,Amount\n"
            "20/07/2026,TESCO STORES 2897,IVAN E,XXXX-1234,28.91\n"
            "21/07/2026,TFL TRAVEL CHARGE,IVAN E,XXXX-1234,3.50\n"
            "22/07/2026,PAYMENT RECEIVED - THANK YOU,IVAN E,XXXX-1234,-200.00\n"
        ),
    },
    {
        "id": "revolut-signed",
        "label": "Revolut-like negative spend",
        "expect_sign": "negative_is_outgoing",
        "expect": [
            ("REVOLUT*COFFEE", "outgoing", 4.8),
            ("SUPERMARKET", "outgoing", 32.15),
            ("TOPUP JOHN", "incoming", 50.0),
        ],
        "csv": (
            "Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance\n"
            "CARD_PAYMENT,Current,2026-07-01 09:00:00,2026-07-01 09:05:00,REVOLUT*COFFEE,-4.80,0,GBP,COMPLETED,100\n"
            "CARD_PAYMENT,Current,2026-07-02 12:00:00,2026-07-02 12:00:00,SUPERMARKET,-32.15,0,GBP,COMPLETED,67.85\n"
            "TOPUP,Current,2026-07-03 08:00:00,2026-07-03 08:00:00,TOPUP JOHN,50.00,0,GBP,COMPLETED,117.85\n"
        ),
    },
    {
        "id": "accounting-generic",
        "label": "Generic Date/Description/Amount accounting (neg=out)",
        "expect_sign": "negative_is_outgoing",
        "expect": [
            ("Coffee", "outgoing", 4.8),
            ("Rent", "outgoing", 850.0),
            ("Salary", "incoming", 2800.0),
        ],
        "csv": (
            "Date,Description,Amount\n"
            "2026-07-01,Coffee,-4.80\n"
            "2026-07-02,Rent,-850.00\n"
            "2026-07-03,Salary,2800.00\n"
        ),
    },
    {
        "id": "money-columns",
        "label": "Paid in / Paid out columns",
        "expect_sign": "absolute",
        "expect": [
            ("Salary", "incoming", 2000.0),
            ("Groceries", "outgoing", 45.2),
        ],
        "csv": (
            "Date,Description,Paid in,Paid out\n"
            "01/07/2026,Salary,2000.00,\n"
            "02/07/2026,Groceries,,45.20\n"
        ),
    },
    {
        "id": "direction-column",
        "label": "Explicit Direction column",
        "expect_sign": None,  # inference skipped; direction cell wins
        "expect": [
            ("Shop", "outgoing", 12.5),
            ("Refund", "incoming", 3.0),
        ],
        "csv": (
            "Date,Description,Amount,Direction\n"
            "01/07/2026,Shop,12.50,Debit\n"
            "02/07/2026,Refund,3.00,Credit\n"
        ),
    },
]


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


def seed_data() -> dict:
    return {
        "users": {
            USER: {
                "spending": {
                    "transactions": [],
                    "statements": [],
                    "monthly_insights": {},
                    "classification_overrides": {},
                    "classification_cache": {},
                    "outgoing_classification_cache": {},
                    "daily_budget": {
                        "plan": {
                            "income_monthly": 0,
                            "bills_monthly": 0,
                            "savings_percent": 0,
                            "daily_mode": "fixed",
                            "bill_items": [],
                        },
                        "goals": [],
                    },
                }
            }
        },
        "loans": {},
    }


def run_matrix() -> list[dict]:
    app_mod._TABULAR_HEADER_MAP_CACHE.clear()
    results: list[dict] = []
    for sample in SAMPLES:
        parsed = app_mod._try_parse_tabular_spending_transactions(
            sample["csv"], allow_llm=False
        )
        ok = parsed is not None
        rows, meta = parsed if parsed else ([], {})
        by_desc = {r["description"]: r for r in rows}
        row_checks = []
        for desc, direction, amount in sample["expect"]:
            got = by_desc.get(desc)
            pass_row = (
                got is not None
                and got.get("direction") == direction
                and abs(float(got.get("amount") or 0) - amount) < 0.001
            )
            row_checks.append(
                {
                    "description": desc,
                    "expected_direction": direction,
                    "expected_amount": amount,
                    "got_direction": None if got is None else got.get("direction"),
                    "got_amount": None if got is None else got.get("amount"),
                    "pass": pass_row,
                }
            )
        sign_ok = True
        if sample["expect_sign"] is not None:
            sign_ok = meta.get("amount_sign") == sample["expect_sign"]
        sample_pass = ok and sign_ok and all(c["pass"] for c in row_checks)
        results.append(
            {
                "id": sample["id"],
                "label": sample["label"],
                "pass": sample_pass,
                "amount_sign": meta.get("amount_sign"),
                "expected_sign": sample["expect_sign"],
                "header_map_source": meta.get("header_map_source"),
                "row_count": len(rows),
                "rows": row_checks,
            }
        )
    return results


def write_results(results: list[dict], ui_notes: list[str]) -> None:
    DOCS_SHOT_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_SHOT_DIR.mkdir(parents=True, exist_ok=True)
    evidence = {
        "matrix": results,
        "ui_notes": ui_notes,
        "all_matrix_pass": all(r["pass"] for r in results),
    }
    for dest in (DOCS_SHOT_DIR, LOCAL_SHOT_DIR, SHOT_DIR):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "evidence.json").write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
        )

    lines = [
        "# Amex amount-sign — thorough sample matrix + UI playthrough",
        "",
        f"Matrix: **{'PASS' if evidence['all_matrix_pass'] else 'FAIL'}** "
        f"({sum(1 for r in results if r['pass'])}/{len(results)} samples)",
        "",
        "| Sample | amount_sign | Rows | Result |",
        "| --- | --- | --- | --- |",
    ]
    for r in results:
        status = "PASS" if r["pass"] else "FAIL"
        lines.append(
            f"| `{r['id']}` {r['label']} | `{r.get('amount_sign')}` "
            f"(expected `{r.get('expected_sign')}`) | {r['row_count']} | **{status}** |"
        )
    lines.extend(["", "## Row-level checks", ""])
    for r in results:
        lines.append(f"### {r['label']} (`{r['id']}`)")
        lines.append("")
        for c in r["rows"]:
            mark = "PASS" if c["pass"] else "FAIL"
            lines.append(
                f"- [{mark}] `{c['description']}` → "
                f"expected {c['expected_direction']} £{c['expected_amount']}, "
                f"got {c['got_direction']} £{c['got_amount']}"
            )
        lines.append("")
    if ui_notes:
        lines.extend(["## UI playthrough", ""])
        for note in ui_notes:
            lines.append(f"- {note}")
        lines.append("")
    text = "\n".join(lines)
    for dest in (DOCS_SHOT_DIR, LOCAL_SHOT_DIR):
        (dest / "RESULTS.md").write_text(text + "\n", encoding="utf-8")


def login(page) -> None:
    page.goto(f"{BASE}/login")
    page.fill('input[name="username"]', USER)
    page.fill('input[name="password"]', PASS)
    page.click('button[type="submit"]')
    page.wait_for_url(f"{BASE}/")


def fill_period(page) -> None:
    page.fill("#report-period-month", MONTH)
    page.fill("#period-start", PERIOD_START)
    page.fill("#period-end", PERIOD_END)


def preview_csv(page, csv_path: Path, source: str) -> None:
    fill_period(page)
    page.fill("#statement-source", source)
    page.set_input_files("#spending-file", str(csv_path))
    page.click("#spending-preview-btn")
    page.wait_for_selector("#spending-preview-wrap:not(.hidden)", timeout=30000)


def assert_preview_direction(page, description: str, direction: str) -> None:
    rows = page.locator("#spending-preview-tbody tr")
    count = rows.count()
    found = False
    for i in range(count):
        row = rows.nth(i)
        desc = row.locator(".preview-tx-ref").inner_text().strip()
        if description not in desc:
            continue
        selected = row.locator(".preview-direction").input_value()
        assert selected == direction, f"{description}: expected {direction}, got {selected}"
        found = True
        break
    assert found, f"Preview row not found for {description!r}"


def main() -> int:
    for d in (SHOT_DIR, LOCAL_SHOT_DIR, DOCS_SHOT_DIR, APP_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for old in DOCS_SHOT_DIR.glob("*.png"):
        old.unlink()
    for old in LOCAL_SHOT_DIR.glob("*.png"):
        old.unlink()

    matrix = run_matrix()
    assert all(r["pass"] for r in matrix), json.dumps(matrix, indent=2)

    amex_csv = APP_DIR / "amex-user-sample.csv"
    amex_pay_csv = APP_DIR / "amex-with-payment.csv"
    revolut_csv = APP_DIR / "revolut-sample.csv"
    amex_csv.write_text(SAMPLES[0]["csv"], encoding="utf-8")
    amex_pay_csv.write_text(SAMPLES[1]["csv"], encoding="utf-8")
    revolut_csv.write_text(SAMPLES[4]["csv"], encoding="utf-8")

    (APP_DIR / "data.json").write_text(
        json.dumps(seed_data(), indent=2) + "\n", encoding="utf-8"
    )

    env = os.environ.copy()
    env["FINANCE_TRACKER_USERS"] = f"{USER}:{PASS}"
    # Force tabular-only path (no OpenAI) so evidence is deterministic.
    env.pop("OPENAI_API_KEY", None)
    env["PYTHONPATH"] = str(ROOT) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["DATA_FILE"] = str(APP_DIR / "data.json")

    # app.py uses DATA_PATH / relative data.json from cwd — run from APP_DIR.
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
    ui_notes: list[str] = []
    try:
        wait_ready(f"{BASE}/login")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            login(page)

            # 1. Empty import form (July period ready)
            page.goto(f"{BASE}/")
            page.wait_for_selector("#import")
            fill_period(page)
            save(page, "01-home-import-ready.png")
            save_locator(page.locator("#import"), "01b-import-section.png")
            ui_notes.append("Home import ready with July 2026 period")

            # 2. Amex user sample selected
            page.fill("#statement-source", "Amex")
            page.set_input_files("#spending-file", str(amex_csv))
            save_locator(page.locator("#import"), "02-amex-csv-selected.png")
            ui_notes.append("Selected user-reported Amex CSV + Source=Amex")

            # 3. Real preview — charges must be Outgoing
            page.click("#spending-preview-btn")
            page.wait_for_selector("#spending-preview-wrap:not(.hidden)", timeout=30000)
            expect(page.locator("#spending-preview-wrap")).to_contain_text("GO AHEAD GROUP")
            expect(page.locator("#spending-preview-wrap")).to_contain_text("SAINSBURY")
            assert_preview_direction(page, "GO AHEAD GROUP", "outgoing")
            assert_preview_direction(page, "SAINSBURY", "outgoing")
            summary = page.locator("#spending-preview-summary").inner_text()
            assert "Out" in summary or "outgoing" in summary.lower() or "£" in summary
            save_locator(page.locator("#import"), "03-amex-preview-outgoing.png")
            save_locator(
                page.locator("#spending-preview-wrap"),
                "03b-amex-preview-table.png",
            )
            # Expand first row so Direction select is clearly visible on evidence
            page.locator("#spending-preview-tbody tr").first.click()
            save_locator(
                page.locator("#spending-preview-wrap"),
                "03c-amex-row-expanded-direction.png",
            )
            ui_notes.append(
                "Amex user sample preview: both charges classified Outgoing "
                f"(summary snippet: {summary[:160]!r})"
            )

            # 4. Amex with payment credit
            page.goto(f"{BASE}/")
            page.wait_for_selector("#import")
            preview_csv(page, amex_pay_csv, "American Express")
            assert_preview_direction(page, "TESCO STORES", "outgoing")
            assert_preview_direction(page, "PAYMENT RECEIVED", "incoming")
            assert_preview_direction(page, "COFFEE SHOP", "outgoing")
            save_locator(
                page.locator("#spending-preview-wrap"),
                "04-amex-payment-mixed-directions.png",
            )
            ui_notes.append(
                "Amex charges+payment: TESCO/COFFEE outgoing, PAYMENT RECEIVED incoming"
            )

            # 5. Revolut control — negatives still outgoing
            page.goto(f"{BASE}/")
            page.wait_for_selector("#import")
            preview_csv(page, revolut_csv, "Revolut")
            assert_preview_direction(page, "REVOLUT*COFFEE", "outgoing")
            assert_preview_direction(page, "SUPERMARKET", "outgoing")
            assert_preview_direction(page, "TOPUP JOHN", "incoming")
            save_locator(
                page.locator("#spending-preview-wrap"),
                "05-revolut-control-preview.png",
            )
            ui_notes.append(
                "Revolut control: negative spend still Outgoing; TOPUP Incoming"
            )

            # 6. Import Amex user sample end-to-end
            page.goto(f"{BASE}/")
            page.wait_for_selector("#import")
            preview_csv(page, amex_csv, "Amex")
            page.click("#spending-import-btn")
            page.wait_for_selector("#home-import-next:not(.hidden)", timeout=20000)
            expect(page.locator("#spending-status")).to_contain_text("Imported")
            save_locator(page.locator("#import"), "06-amex-after-import.png")
            save(page, "06b-home-after-amex-import.png")
            ui_notes.append("Imported Amex user sample successfully")

            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    write_results(matrix, ui_notes)
    print(f"Wrote evidence to {DOCS_SHOT_DIR}")
    print(f"Matrix pass: {all(r['pass'] for r in matrix)} ({len(matrix)} samples)")
    for n in ui_notes:
        print(f"  - {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
