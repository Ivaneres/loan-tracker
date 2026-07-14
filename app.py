from flask import Flask, render_template, request, jsonify, redirect, url_for, session, Response, stream_with_context
from functools import wraps
from datetime import datetime, date, timedelta
import copy
import io
import json
import logging
import os
import time
import re
import smtplib
import uuid
from collections import defaultdict
from email.message import EmailMessage
from difflib import SequenceMatcher
from dateutil.relativedelta import relativedelta
from dateutil import parser as dateutil_parser
from calendar import monthrange
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from werkzeug.middleware.proxy_fix import ProxyFix
from pypdf import PdfReader

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[misc, assignment]

logger = logging.getLogger(__name__)

# Bank statement LLM import: max upload size, max chars sent to the model (approximate context limit).
# Environment:
#   OPENAI_API_KEY — required for preview/import (503 if missing).
#   OPENAI_MODEL — optional, default gpt-4o-mini.
#   OPENAI_BASE_URL — optional; use for OpenAI-compatible API endpoints.
STATEMENT_MAX_BYTES = 2 * 1024 * 1024
STATEMENT_MAX_CHARS_FOR_LLM = 100_000
# Spending preview API: cap intermediate-representation strings so responses stay practical.
SPENDING_PIPELINE_EXTRACT_PREVIEW = 14_000
SPENDING_PIPELINE_HINTS_PREVIEW = 18_000
SPENDING_PIPELINE_DIRECTION_HINT_ROWS = 150
SPENDING_PDF_COLUMN_HINTS_BANNER = (
    '\n\n--- COLUMN HINTS (from PDF layout: use money_in vs money_out per row; '
    'do not infer from description) ---\n'
)
# Layout-first extraction: parse LINE_HINT rows locally; small LLM call only for skip-line regexes.
# Fallback compares parsed rows to *eligible* hint lines (non-skipped rows with money), not total hints.
SPENDING_LAYOUT_MIN_HINT_ROWS = 5
SPENDING_LAYOUT_FALLBACK_MIN_PARSED = 3
SPENDING_LAYOUT_FALLBACK_RATIO = 0.18
SPENDING_LAYOUT_EXCERPT_MAX_LINES = 90
SPENDING_LAYOUT_EXCERPT_MAX_CHARS = 14_000
SPENDING_LAYOUT_SAMPLE_HINTS = 12
SPENDING_LAYOUT_MAPPING_LLM_MAX_CHARS = 9000
_SPENDING_LAYOUT_DEFAULT_SKIP_REGEXES = [
    r'(?i)^\s*balance\b',
    r'(?i)brought\s+forward',
    r'(?i)opening\s+balance',
    r'(?i)closing\s+balance',
    r'(?i)^\s*continued\s*$',
    r'(?i)^\s*date\s+description',
    r'(?i)\bpaid\s+in\b.*\bpaid\s+out\b',
    r'(?i)^\s*payment\s+type\s+',
    r'(?i)^\s*sheet\s+\d',
]
_LINE_HINT_ROW_RE = re.compile(
    r'^LINE_HINT money_in=(\d+(?:\.\d+)?|0) money_out=(\d+(?:\.\d+)?|0) \| (.+)$'
)
# Import preview: flag when existing transaction amount is within this many pounds of bank or share.
STATEMENT_AUDIT_MATCH_TOLERANCE = 5.0
# Internal transfer auto/manual pairing: amount difference allowed between legs.
# Tighter for small amounts (1% of larger leg, min 5p, max 50p when larger leg < £50) to avoid
# false auto-matches. Larger transfers still allow up to £5 (fees/FX) via min(5, 1% of leg).
SPENDING_TRANSFER_MIN_AMOUNT_DIFF = 0.05
SPENDING_TRANSFER_LARGE_LEG_PCT = 0.01
SPENDING_TRANSFER_SMALL_LEG_MAX = 50.0
SPENDING_TRANSFER_SMALL_LEG_ABS_CAP = 0.5
SPENDING_TRANSFER_LARGE_LEG_ABS_CAP = 5.0
# Internal transfer matching (e.g. HSBC to Revolut): max calendar gap between legs (day count).
SPENDING_TRANSFER_MAX_DAY_GAP = 1
SPENDING_TRANSFER_UNMATCHED_PREVIEW_MAX = 12
# Anomaly: ignore tiny category spend even if 1.5× prior average (reduces noise).
SPENDING_ANOMALY_MIN_GBP = 20.0
SPENDING_ANOMALY_MIN_OUTGOING_PCT = 0.01

# SMTP for monthly bill-upload reminders (optional). If SMTP_HOST is unset, reminders are skipped.
#   SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD
#   SMTP_USE_TLS — default true (STARTTLS)
#   MAIL_FROM — From address
#   FINANCE_TRACKER_NOTIFY_EMAIL — default To when loan has no bill_reminder_email
#   PUBLIC_BASE_URL — e.g. https://tracker.example.com (no trailing slash) for links in emails
BILL_REMINDER_HOUR = 9
BASELINE_SIM_THRESHOLD = 0.75
BASELINE_NOTE_MAX_LEN = 300
SPENDING_ALLOWED_CATEGORIES = [
    'housing',
    'groceries',
    'transport',
    'utilities',
    'subscriptions',
    'health',
    'dining',
    'entertainment',
    'travel',
    'shopping',
    'debt',
    'savings',
    'other',
    'unclassified',
]
SPENDING_CATEGORY_SET = set(SPENDING_ALLOWED_CATEGORIES)


def _sanitize_note(raw) -> str:
    if raw is None:
        return ''
    return str(raw).strip()[:BASELINE_NOTE_MAX_LEN]


def _parse_baseline_item_row(row: dict) -> dict | None:
    """Single baseline row from client/save payload; returns None if invalid."""
    if not isinstance(row, dict):
        return None
    try:
        ab = float(row.get('amount_bank'))
    except (TypeError, ValueError):
        return None
    if ab <= 0:
        return None
    share = row.get('amount_share')
    if share is None and row.get('amount_default') is not None:
        share = row.get('amount_default')
    if share is None:
        share = round(ab / 2.0, 2)
    else:
        try:
            share = round(float(share), 2)
        except (TypeError, ValueError):
            return None
    if share <= 0:
        return None
    desc = str(row.get('description') or '').strip()[:500]
    if not desc:
        desc = 'Bill'
    cat = row.get('category')
    if cat is not None:
        cat = str(cat).strip()[:120]
        if not cat:
            cat = None
    else:
        cat = None
    return {
        'id': str(row.get('id') or uuid.uuid4()),
        'description': desc,
        'amount_bank': round(ab, 2),
        'amount_share': share,
        'category': cat,
        'note': _sanitize_note(row.get('note')),
    }


def _baseline_item_from_stored(b: dict) -> dict:
    """Normalize stored baseline dict for API/merge output."""
    try:
        ab = float(b.get('amount_bank', 0))
    except (TypeError, ValueError):
        ab = 0.0
    try:
        sh = float(b.get('amount_share', ab / 2.0))
    except (TypeError, ValueError):
        sh = round(ab / 2.0, 2) if ab else 0.0
    desc = str(b.get('description') or '').strip()[:500] or 'Bill'
    cat = b.get('category')
    if cat is not None:
        cat = str(cat)[:120]
    else:
        cat = None
    return {
        'id': str(b.get('id') or uuid.uuid4()),
        'description': desc,
        'amount_bank': round(ab, 2),
        'amount_share': round(sh, 2),
        'category': cat,
        'note': _sanitize_note(b.get('note')),
    }


def merge_baseline_with_candidates(existing: list | None, candidates: list) -> list:
    """
    Combine a new statement's normalized candidates with saved baseline rows.
    Each candidate updates at most one unmatched baseline row (description similarity + bank amount).
    Unmatched existing rows are kept at the end. New candidates with no match become new rows (new id, empty note).
    """
    existing = existing or []
    if not isinstance(candidates, list):
        return [_baseline_item_from_stored(b) for b in existing]

    matched_ids: set[str] = set()
    out: list = []

    for c in candidates:
        if not isinstance(c, dict):
            continue
        try:
            cb = float(c.get('amount_bank', 0))
        except (TypeError, ValueError):
            continue
        if cb <= 0:
            continue
        cd = str(c.get('description') or '')
        best_b = None
        best_sim = -1.0
        for b in existing:
            bid = str(b.get('id') or '')
            if not bid or bid in matched_ids:
                continue
            try:
                ab = float(b.get('amount_bank', 0))
            except (TypeError, ValueError):
                continue
            if not _amounts_close_for_compare(ab, cb):
                continue
            sim = _description_similarity(str(b.get('description', '')), cd)
            if sim >= BASELINE_SIM_THRESHOLD and sim > best_sim:
                best_sim = sim
                best_b = b

        share = c.get('amount_default')
        if share is None:
            try:
                share = round(float(c.get('amount_bank')) / 2.0, 2)
            except (TypeError, ValueError):
                share = round(cb / 2.0, 2)
        else:
            try:
                share = round(float(share), 2)
            except (TypeError, ValueError):
                share = round(cb / 2.0, 2)
        cat = c.get('category')
        if cat is not None:
            cat = str(cat)[:120]
        else:
            cat = None
        desc = str(c.get('description') or '').strip()[:500] or 'Bill'

        if best_b is not None:
            bid = str(best_b.get('id'))
            matched_ids.add(bid)
            out.append({
                'id': bid,
                'description': desc,
                'amount_bank': round(cb, 2),
                'amount_share': share,
                'category': cat,
                'note': _sanitize_note(best_b.get('note')),
            })
        else:
            out.append({
                'id': str(uuid.uuid4()),
                'description': desc,
                'amount_bank': round(cb, 2),
                'amount_share': share,
                'category': cat,
                'note': '',
            })

    for b in existing:
        bid = str(b.get('id') or '')
        if bid and bid not in matched_ids:
            out.append(_baseline_item_from_stored(b))

    return out


def _env_bool(key, default=False):
    v = os.getenv(key)
    if v is None:
        return default
    return v.strip().lower() in ('1', 'true', 'yes', 'on')


app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')
app.config['SESSION_COOKIE_NAME'] = 'finance_tracker_session'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# Behind nginx/Caddy with TLS, set SESSION_COOKIE_SECURE=1 so browsers send the cookie on HTTPS.
app.config['SESSION_COOKIE_SECURE'] = _env_bool('SESSION_COOKIE_SECURE', False)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=31)

# Honour X-Forwarded-* from a reverse proxy so request.scheme / host match what the browser uses.
if _env_bool('TRUST_PROXY', True):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

def load_users():
    """
    Load users from FINANCE_TRACKER_USERS env var:
    "username:password,another:secret"
    Falls back to defaults for local development.
    """
    raw_users = os.getenv('FINANCE_TRACKER_USERS', '').strip()
    if not raw_users:
        return {
            'admin': 'admin123',
            'user': 'user123'
        }

    users = {}
    for pair in raw_users.split(','):
        if ':' not in pair:
            continue
        username, password = pair.split(':', 1)
        username = username.strip()
        password = password.strip()
        if username and password:
            users[username] = password

    return users or {
        'admin': 'admin123',
        'user': 'user123'
    }

USERS = load_users()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

scheduler = BackgroundScheduler()
scheduler.start()

def load_data():
    default_data = {
        'loans': {},  # Dictionary to store multiple loans
        'users': {},
    }
    try:
        with open('data.json', 'r') as f:
            content = f.read().strip()
            if not content:  # Handle empty file
                save_data(default_data)
                return default_data
            
            # Load existing data and merge with defaults to handle missing fields
            existing_data = json.loads(content)
            if 'loans' not in existing_data:
                # Convert old format to new format
                old_data = existing_data
                existing_data = default_data
                if 'loan_amount' in old_data:
                    loan_id = 'loan_1'  # Default ID for the first loan
                    existing_data['loans'][loan_id] = {
                        'name': 'My Loan',  # Default name
                        'loan_amount': old_data.get('loan_amount', 0),
                        'interest_rate': old_data.get('interest_rate', 0),
                        'interest_day': old_data.get('interest_day', 1),
                        'transactions': old_data.get('transactions', [])
                    }
            
            changed = _ensure_data_shape(existing_data)
            if changed:
                save_data(existing_data)
            return existing_data
            
    except (FileNotFoundError, json.JSONDecodeError):
        save_data(default_data)
        return default_data

def save_data(data):
    with open('data.json', 'w') as f:
        json.dump(data, f, indent=2)


def _ensure_data_shape(data: dict) -> bool:
    """Runtime-safe migration for legacy data.json structures."""
    changed = False
    if 'loans' not in data or not isinstance(data.get('loans'), dict):
        data['loans'] = {}
        changed = True
    if 'users' not in data or not isinstance(data.get('users'), dict):
        data['users'] = {}
        changed = True
    return changed


def _ensure_user_spending(data: dict, username: str) -> tuple[dict, bool]:
    changed = False
    users = data.setdefault('users', {})
    if username not in users or not isinstance(users.get(username), dict):
        users[username] = {}
        changed = True
    user_bucket = users[username]
    spending = user_bucket.get('spending')
    if not isinstance(spending, dict):
        spending = {}
        user_bucket['spending'] = spending
        changed = True
    defaults = {
        'statements': [],
        'transactions': [],
        'monthly_insights': {},
        'classification_overrides': {},
        'classification_cache': {},
    }
    for key, default in defaults.items():
        if key not in spending or not isinstance(spending.get(key), type(default)):
            spending[key] = default.copy() if isinstance(default, dict) else list(default)
            changed = True
    return spending, changed


def _normalize_label(s: str) -> str:
    s = (s or '').strip().lower()
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _normalize_spending_direction(raw_direction, description: str, amount: float) -> str:
    d = str(raw_direction or '').strip().lower()
    if d in ('incoming', 'outgoing'):
        return d
    desc = _normalize_label(description)
    incoming_words = ('salary', 'payroll', 'refund', 'interest', 'benefit', 'bonus', 'deposit', 'received')
    outgoing_words = ('debit', 'payment', 'card', 'purchase', 'dd', 'standing order', 'transfer out')
    if any(w in desc for w in incoming_words):
        return 'incoming'
    if any(w in desc for w in outgoing_words):
        return 'outgoing'
    return 'outgoing' if amount >= 0 else 'incoming'


def _spending_optional_money(val) -> float | None:
    if val is None or val == '':
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val) if float(val) > 0 else None
    s = str(val).strip().replace(',', '')
    s = re.sub(r'^[$£€]\s*', '', s)
    if not s:
        return None
    try:
        x = float(s)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def _direction_and_amount_from_money_columns(row: dict) -> tuple[str | None, float | None]:
    """
    Prefer explicit money_in / money_out from the LLM (matches separate Paid in / Paid out columns).
    Aliases: paid_in/paid_out, credit/debit amounts.
    """
    mi = _spending_optional_money(row.get('money_in'))
    mo = _spending_optional_money(row.get('money_out'))
    if mi is None:
        mi = _spending_optional_money(row.get('paid_in'))
    if mo is None:
        mo = _spending_optional_money(row.get('paid_out'))

    if mi is not None and mo is None:
        return 'incoming', mi
    if mo is not None and mi is None:
        return 'outgoing', mo
    if mi is not None and mo is not None:
        if mi > 0 and mo <= 0:
            return 'incoming', mi
        if mo > 0 and mi <= 0:
            return 'outgoing', mo
        # Both positive: rare; prefer LLM direction if it matches one column
        d = str(row.get('direction') or '').strip().lower()
        if d == 'incoming':
            return 'incoming', mi
        if d == 'outgoing':
            return 'outgoing', mo
        return 'outgoing', mo
    return None, None


def _spending_fingerprint(
    report_month: str,
    d_str: str,
    amount: float,
    direction: str,
    description: str,
) -> str:
    desc = _normalize_label(description)
    rm = (report_month or '').strip()[:7]
    return f'{rm}|{d_str}|{direction}|{round(float(amount), 2):.2f}|{desc}'


def _apply_spending_preview_duplicate_marks(
    report_month: str, rows: list, spending: dict
) -> tuple[int, int, list[str]]:
    """
    Set preview_duplicate / preview_duplicate_reason on each preview row, using the
    same fingerprint scheme as import. First occurrence in the upload is kept; later
    matching rows in the file, or any row matching the ledger, are marked duplicate.

    Returns (ledger_duplicate_count, upload_duplicate_count, ledger_fingerprints_for_ui).
    The fingerprint list is restricted to the current report month for a smaller JSON payload.
    """
    tx_store = spending.get('transactions') or []
    ledger_fps = {str(t.get('fingerprint')) for t in tx_store if t.get('fingerprint')}
    rm = (report_month or '').strip()[:7]
    seen: set[str] = set()
    led = 0
    dup_upload = 0
    for r in rows:
        fp = _spending_fingerprint(
            rm,
            str(r.get('date') or ''),
            float(r.get('amount') or 0),
            str(r.get('direction') or 'outgoing'),
            str(r.get('description') or ''),
        )
        if fp in ledger_fps:
            r['preview_duplicate'] = True
            r['preview_duplicate_reason'] = 'ledger'
            led += 1
        elif fp in seen:
            r['preview_duplicate'] = True
            r['preview_duplicate_reason'] = 'upload'
            dup_upload += 1
        else:
            r['preview_duplicate'] = False
            r['preview_duplicate_reason'] = None
            seen.add(fp)
    month_prefix = f'{rm}|' if len(rm) == 7 else None
    client_fps = sorted(fp for fp in ledger_fps if month_prefix and fp.startswith(month_prefix))
    return led, dup_upload, client_fps


def _report_month_for_spending_tx(t: dict) -> str:
    """Bucket key for monthly insights: prefer explicit report_month from import."""
    return str(t.get('report_month') or t.get('month') or '').strip()


def _first_day_of_month(month_key: str) -> date | None:
    mk = (month_key or '').strip()
    if len(mk) == 7:
        try:
            return datetime.strptime(mk + '-01', '%Y-%m-%d').date()
        except ValueError:
            return None
    return _parse_iso_date(mk[:10])


def _last_day_of_month(month_key: str) -> date | None:
    d0 = _first_day_of_month(month_key)
    if d0 is None:
        return None
    last = monthrange(d0.year, d0.month)[1]
    return date(d0.year, d0.month, last)


def _parse_spending_period_from_values(
    report_month_raw: str | None,
    period_start_raw: str | None,
    period_end_raw: str | None,
) -> tuple[dict | None, str | None]:
    """
    Resolve reporting month + inclusive date range for spending imports.
    If only report_month (YYYY-MM) is given, range is full calendar month.
    """
    report_month = (report_month_raw or '').strip()[:7]
    ps = (period_start_raw or '').strip()[:10]
    pe = (period_end_raw or '').strip()[:10]

    if not report_month and not ps and not pe:
        today = date.today()
        report_month = today.strftime('%Y-%m')
        d_start = date(today.year, today.month, 1)
        last = monthrange(today.year, today.month)[1]
        d_end = date(today.year, today.month, last)
    elif report_month and not ps and not pe:
        d0 = _first_day_of_month(report_month)
        if d0 is None:
            return None, 'Invalid report_month (use YYYY-MM).'
        d_start = d0
        d_end = _last_day_of_month(report_month)
        if d_end is None:
            return None, 'Invalid report_month (use YYYY-MM).'
    else:
        d_start = _parse_iso_date(ps) if ps else None
        d_end = _parse_iso_date(pe) if pe else None
        if d_start is None or d_end is None:
            return None, 'period_start and period_end are required as YYYY-MM-DD (or set report_month only).'
        if d_start > d_end:
            return None, 'period_start must be on or before period_end.'
        if (d_end - d_start).days > 400:
            return None, 'Reporting period is too long (max 400 days).'
        if not report_month:
            report_month = d_start.strftime('%Y-%m')

    if d_start > d_end:
        return None, 'period_start must be on or before period_end.'
    if (d_end - d_start).days > 400:
        return None, 'Reporting period is too long (max 400 days).'

    return {
        'report_month': report_month,
        'period_start': d_start.strftime('%Y-%m-%d'),
        'period_end': d_end.strftime('%Y-%m-%d'),
        'period_start_date': d_start,
        'period_end_date': d_end,
    }, None


def _filter_spending_rows_by_period(rows: list, d_start: date, d_end: date) -> tuple[list, int]:
    """Keep rows whose transaction date falls in [d_start, d_end] inclusive."""
    kept = []
    dropped = 0
    for row in rows:
        d = _parse_iso_date(str(row.get('date') or ''))
        if d is None:
            dropped += 1
            continue
        if d < d_start or d > d_end:
            dropped += 1
            continue
        kept.append(row)
    return kept, dropped


def _strip_llm_markdown_fence(s: str) -> str:
    t = (s or '').strip()
    if not t.startswith('```'):
        return t
    lines = t.split('\n')
    if lines and lines[0].startswith('```'):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith('```'):
        lines = lines[:-1]
    return '\n'.join(lines).strip()


def _extract_first_json_object(s: str) -> str:
    """If the model wrapped JSON in prose or fences, keep the outermost {...} block."""
    start = s.find('{')
    if start == -1:
        return s
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return s[start:]


def _escape_raw_controls_in_json_strings(s: str) -> str:
    """
    Models sometimes emit raw newlines/tabs inside JSON string values, which
    standard json.loads rejects. Escape those while respecting backslash escapes.
    """
    out: list[str] = []
    i = 0
    n = len(s)
    in_str = False
    escape = False
    while i < n:
        c = s[i]
        if not in_str:
            if c == '"':
                in_str = True
            out.append(c)
            i += 1
            continue
        if escape:
            out.append(c)
            escape = False
            i += 1
            continue
        if c == '\\':
            out.append(c)
            escape = True
            i += 1
            continue
        if c == '"':
            in_str = False
            out.append(c)
            i += 1
            continue
        if c == '\n':
            out.append('\\n')
            i += 1
            continue
        if c == '\r':
            out.append('\\r')
            i += 1
            continue
        if c == '\t':
            out.append('\\t')
            i += 1
            continue
        o = ord(c)
        if o < 32:
            out.append(f'\\u{o:04x}')
            i += 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def _parse_llm_json_object(raw: str, *, context: str = 'llm') -> tuple[dict, str | None]:
    """
    Parse JSON from an LLM message: tolerate markdown fences, leading prose,
    illegal control characters inside strings, and minor syntax issues (via json-repair).
    Returns (dict, None) on success, or ({}, error_message) on total failure.
    """
    s = (raw or '').strip()
    if not s:
        return {}, None
    s = _strip_llm_markdown_fence(s).lstrip('\ufeff').strip()

    seen: set[str] = set()
    fragments: list[str] = []
    for frag in (s, _extract_first_json_object(s)):
        frag = frag.strip()
        if frag and frag not in seen:
            seen.add(frag)
            fragments.append(frag)
    for frag in list(fragments):
        esc = _escape_raw_controls_in_json_strings(frag)
        if esc != frag and esc not in seen:
            seen.add(esc)
            fragments.append(esc)

    last_err: str | None = None
    for frag in fragments:
        try:
            obj = json.loads(frag)
            if isinstance(obj, dict):
                return obj, None
        except json.JSONDecodeError as e:
            last_err = str(e)

    try:
        from json_repair import repair_json  # type: ignore[import-untyped]
    except ImportError:
        repair_json = None

    if repair_json:
        for frag in fragments:
            try:
                obj = repair_json(frag, return_objects=True)
                if isinstance(obj, dict):
                    return obj, None
            except Exception as e:
                last_err = str(e)

    logger.warning('%s: could not parse model JSON (%s)', context, last_err)
    return {}, last_err or 'invalid JSON'


def _extract_spending_transactions_llm(statement_text: str, period_hint: str | None = None) -> list:
    client = _get_openai_client()
    if not client:
        raise RuntimeError('OPENAI_API_KEY is not set')

    model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini').strip() or 'gpt-4o-mini'
    text = statement_text
    truncated = False
    if len(text) > STATEMENT_MAX_CHARS_FOR_LLM:
        text = text[:STATEMENT_MAX_CHARS_FOR_LLM]
        truncated = True

    system = (
        'You extract ALL individual bank transactions from the statement text into structured JSON. '
        'Return only valid JSON as {"transactions":[{"date":"YYYY-MM-DD","description":"string","amount":number,'
        '"direction":"incoming|outgoing","money_in":number|null,"money_out":number|null}]}. '
        'Rules: (1) amount is always a positive absolute number (same as the money in or money out value for that row). '
        '(2) Many statements use TWO columns: Paid in / Money in / Credits vs Paid out / Money out / Debits. '
        'For those rows, set money_in to the amount in the IN column and money_out to the amount in the OUT column (use null for the empty column). '
        'Direction MUST follow the column: if money_in is set, direction is "incoming"; if money_out is set, direction is "outgoing". '
        'Do NOT infer direction from the merchant description when column hints or two-column layout is present. '
        '(3) If the statement does not use two columns, set money_in and money_out to null and use amount + direction as usual. '
        '(4) Be exhaustive: include every posted transaction line. Do not merge rows; do not omit small items. '
        '(5) Skip only running balances, headers, page numbers, and non-transaction summaries. '
        '(6) Use ISO dates YYYY-MM-DD. If the statement shows DD/MM/YYYY, convert to ISO. '
        '(7) Lines beginning with LINE_HINT are layout hints — use them to fill money_in/money_out and direction correctly.'
    )
    user_msg = 'Bank statement text:\n\n' + text
    if period_hint:
        user_msg += '\n\n' + period_hint
    if truncated:
        user_msg += '\n\n(Note: text was truncated.)'

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_msg},
        ],
        response_format={'type': 'json_object'},
        temperature=0.1,
    )
    raw = completion.choices[0].message.content or '{}'
    data, jerr = _parse_llm_json_object(raw, context='spending_transactions')
    if jerr:
        raise RuntimeError(
            'Could not parse the model response as JSON. This sometimes happens with very long statements '
            'or stray characters in a description. Try a CSV export, narrow the date range, or retry. '
            f'Detail: {jerr}'
        )
    txs = data.get('transactions')
    if txs is None:
        txs = data.get('items') or []
    if not isinstance(txs, list):
        return []
    return txs


def _split_spending_statement_text_for_model(text: str) -> tuple[str, str]:
    """Split model input into plain extracted text and LINE_HINT block (if present)."""
    banner = SPENDING_PDF_COLUMN_HINTS_BANNER
    if banner in text:
        base, _, hints = text.partition(banner)
        return base.rstrip(), hints.lstrip()
    return text, ''


def _parse_line_hint_rows(hints_block: str) -> list[dict]:
    rows: list[dict] = []
    for line in (hints_block or '').splitlines():
        line = line.strip()
        if not line.startswith('LINE_HINT'):
            continue
        m = _LINE_HINT_ROW_RE.match(line)
        if not m:
            continue
        try:
            mi = round(float(m.group(1)), 2)
            mo = round(float(m.group(2)), 2)
        except ValueError:
            continue
        lt = (m.group(3) or '').strip()
        if not lt:
            continue
        rows.append({'money_in': mi, 'money_out': mo, 'line_text': lt})
    return rows


def _compile_spending_skip_line_regexes(patterns: list[str]) -> list[re.Pattern]:
    compiled: list[re.Pattern] = []
    for p in patterns:
        s = str(p).strip()
        if not s:
            continue
        try:
            compiled.append(re.compile(s))
        except re.error:
            logger.warning('skipping invalid layout skip regex: %s', s[:120])
    return compiled


def _merge_spending_layout_skip_regexes(llm_patterns: list | None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for p in _SPENDING_LAYOUT_DEFAULT_SKIP_REGEXES:
        if p not in seen:
            seen.add(p)
            merged.append(p)
    if isinstance(llm_patterns, list):
        for p in llm_patterns:
            s = str(p).strip()
            if s and s not in seen and len(merged) < 28:
                seen.add(s)
                merged.append(s)
    return merged


def _llm_spending_layout_skip_regexes(base_text: str, hint_rows: list[dict]) -> list[str]:
    """Small model call: extra regexes for non-transaction lines (balances, headers)."""
    client = _get_openai_client()
    if not client:
        return _merge_spending_layout_skip_regexes([])

    lines = base_text.splitlines()[:SPENDING_LAYOUT_EXCERPT_MAX_LINES]
    excerpt = '\n'.join(lines)
    if len(excerpt) > SPENDING_LAYOUT_EXCERPT_MAX_CHARS:
        excerpt = excerpt[:SPENDING_LAYOUT_EXCERPT_MAX_CHARS]

    samples = []
    for h in hint_rows[:SPENDING_LAYOUT_SAMPLE_HINTS]:
        samples.append(
            f'LINE_HINT money_in={h["money_in"]} money_out={h["money_out"]} | {h["line_text"]}'
        )
    sample_block = '\n'.join(samples)
    user_body = f'Statement excerpt:\n{excerpt}\n\nSample LINE_HINT rows:\n{sample_block}\n'
    if len(user_body) > SPENDING_LAYOUT_MAPPING_LLM_MAX_CHARS:
        user_body = user_body[:SPENDING_LAYOUT_MAPPING_LLM_MAX_CHARS]

    model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini').strip() or 'gpt-4o-mini'
    system = (
        'You help parse UK/Irish bank statements. The app already knows debit/credit amounts per row from PDF '
        'geometry (LINE_HINT). Your ONLY job is to list extra Python regex patterns for lines that are NOT real '
        'transactions: running balances, section headers, page markers, "brought forward", etc. '
        'Return only valid JSON: {"skip_line_regexes":["regex",...]}. '
        'Each regex is tested against the text AFTER " | " in a LINE_HINT row (the bank line, not the prefix). '
        'Use (?i) for case-insensitive patterns. Prefer anchored patterns (^...) when sensible. '
        'Be conservative: do NOT add broad patterns that could match merchant or payment descriptions '
        '(that would drop real transactions). When unsure, omit the pattern. '
        'Return an empty array if defaults suffice. At most 14 patterns; each under 180 characters.'
    )
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user_body},
            ],
            response_format={'type': 'json_object'},
            temperature=0.1,
        )
        raw = completion.choices[0].message.content or '{}'
        data, jerr = _parse_llm_json_object(raw, context='spending_layout_skip')
        if jerr:
            logger.warning('layout skip LLM JSON failed: %s', jerr)
            return _merge_spending_layout_skip_regexes([])
        return _merge_spending_layout_skip_regexes(data.get('skip_line_regexes'))
    except Exception as e:
        logger.warning('layout skip LLM call failed: %s', e)
        return _merge_spending_layout_skip_regexes([])


def _extract_first_bank_date_from_line(line_text: str):
    """Find the first plausible transaction date and return (date, description_remainder)."""
    line_text = (line_text or '').strip()
    if not line_text:
        return None, ''
    tokens = line_text.split()
    for n in range(1, min(5, len(tokens) + 1)):
        cand = ' '.join(tokens[:n])
        d = _parse_bank_statement_date(cand)
        if d is not None:
            rest = ' '.join(tokens[n:]).strip()
            return d, rest
    for m in re.finditer(r'\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b', line_text):
        sub = m.group(0)
        d = _parse_bank_statement_date(sub)
        if d is not None:
            rest = (line_text[: m.start()] + ' ' + line_text[m.end() :]).strip()
            rest = re.sub(r'\s+', ' ', rest)
            return d, rest
    for m in re.finditer(r'\b(\d{4})-(\d{2})-(\d{2})\b', line_text):
        sub = m.group(0)
        d = _parse_bank_statement_date(sub)
        if d is not None:
            rest = (line_text[: m.start()] + ' ' + line_text[m.end() :]).strip()
            rest = re.sub(r'\s+', ' ', rest)
            return d, rest
    return None, line_text


def _token_is_strict_statement_amount(tok: str) -> bool:
    """True if token looks like a posted currency amount (optional £/$/€, optional commas, .pp)."""
    t = (tok or '').strip()
    t = re.sub(r'^[$£€]\s*', '', t)
    t = t.replace(',', '')
    t = re.sub(r'(?:CR|DR)$', '', t, flags=re.I)
    return bool(re.match(r'^\d+\.\d{2}$', t))


def _description_before_amount_tokens(desc: str) -> str:
    """
    Keep payee/description text after the date, stopping at the first amount-like token.

    Statement rows are usually [date] [description…] [transaction amount(s)] [running balance].
    LINE_HINT already has the debit/credit amounts; the line text repeats them plus balance.
    A left boundary (first money token) matches that layout more reliably than stripping only
    from the right (e.g. odd trailing tokens, or future extra columns).
    """
    if not (desc or '').strip():
        return desc
    parts = desc.split()
    out: list[str] = []
    for tok in parts:
        if _token_is_strict_statement_amount(tok):
            break
        out.append(tok)
    return ' '.join(out).strip()


def _layout_eligible_hint_count(hint_rows: list[dict], skip_pattern_strings: list[str]) -> int:
    """Hint rows that have money and are not skipped — used for fallback yield thresholds."""
    compiled = _compile_spending_skip_line_regexes(skip_pattern_strings)
    n = 0
    for h in hint_rows:
        lt = h.get('line_text') or ''
        if any(p.search(lt) for p in compiled):
            continue
        try:
            mi = round(float(h.get('money_in', 0) or 0), 2)
            mo = round(float(h.get('money_out', 0) or 0), 2)
        except (TypeError, ValueError):
            continue
        if mi <= 0 and mo <= 0:
            continue
        n += 1
    return n


def _raw_transactions_from_line_hints(
    hint_rows: list[dict],
    skip_pattern_strings: list[str],
    *,
    carry_forward_date: bool = True,
) -> list[dict]:
    """Build raw transaction dicts compatible with _normalize_spending_transactions."""
    compiled = _compile_spending_skip_line_regexes(skip_pattern_strings)
    raw: list[dict] = []
    last_d: date | None = None
    for h in hint_rows:
        lt = h.get('line_text') or ''
        if any(p.search(lt) for p in compiled):
            continue
        try:
            mi = round(float(h.get('money_in', 0) or 0), 2)
            mo = round(float(h.get('money_out', 0) or 0), 2)
        except (TypeError, ValueError):
            continue
        if mi <= 0 and mo <= 0:
            continue
        explicit_d, desc = _extract_first_bank_date_from_line(lt)
        if explicit_d is not None:
            d = explicit_d
            last_d = explicit_d
        elif carry_forward_date and last_d is not None:
            d = last_d
            desc = lt.strip()
        else:
            continue
        if not desc:
            desc = 'Bank transaction'
        else:
            desc = _description_before_amount_tokens(desc)
        if not desc:
            desc = 'Bank transaction'
        ds = d.strftime('%Y-%m-%d')
        if mi > 0 and mo > 0:
            raw.append({
                'date': ds,
                'description': desc[:500],
                'money_in': mi,
                'money_out': None,
                'direction': 'incoming',
            })
            raw.append({
                'date': ds,
                'description': desc[:500],
                'money_in': None,
                'money_out': mo,
                'direction': 'outgoing',
            })
        elif mi > 0:
            raw.append({
                'date': ds,
                'description': desc[:500],
                'money_in': mi,
                'money_out': None,
                'direction': 'incoming',
            })
        else:
            raw.append({
                'date': ds,
                'description': desc[:500],
                'money_in': None,
                'money_out': mo,
                'direction': 'outgoing',
            })
    return raw


def _layout_extraction_should_fallback(hint_row_count: int, eligible_count: int, raw_rows: list) -> bool:
    if hint_row_count < SPENDING_LAYOUT_MIN_HINT_ROWS:
        return True
    p = len(raw_rows)
    if p == 0:
        return True
    if eligible_count <= 0:
        return True
    need = max(
        SPENDING_LAYOUT_FALLBACK_MIN_PARSED,
        int(eligible_count * SPENDING_LAYOUT_FALLBACK_RATIO),
    )
    # At least one output per eligible line when lines are single-sided; dual in/out yields 2 per line.
    need = min(need, eligible_count)
    return p < need


def iter_spending_transaction_extraction(statement_text: str, period_hint: str | None = None):
    """
    Yields {'type':'progress','step':str,'message':str} then
    {'type':'result','rows':list,'meta':dict}.
    """
    base, hints_block = _split_spending_statement_text_for_model(statement_text)
    hint_rows = _parse_line_hint_rows(hints_block)

    if len(hint_rows) < SPENDING_LAYOUT_MIN_HINT_ROWS:
        yield {
            'type': 'progress',
            'step': 'llm_extract',
            'message': 'Extracting transactions with the model (not enough layout-derived LINE_HINT rows)…',
        }
        rows = _extract_spending_transactions_llm(statement_text, period_hint)
        yield {
            'type': 'result',
            'rows': rows,
            'meta': {
                'mode': 'llm_full',
                'reason': 'insufficient_layout_hints',
                'hint_row_count': len(hint_rows),
            },
        }
        return

    yield {
        'type': 'progress',
        'step': 'layout_mapping',
        'message': 'Inferring which lines to skip (compact model request on a short excerpt)…',
    }
    skip_patterns = _llm_spending_layout_skip_regexes(base, hint_rows)

    yield {
        'type': 'progress',
        'step': 'layout_parse',
        'message': 'Parsing dates and descriptions from LINE_HINT rows locally…',
    }
    rows = _raw_transactions_from_line_hints(hint_rows, skip_patterns)
    eligible = _layout_eligible_hint_count(hint_rows, skip_patterns)
    layout_retry_defaults = False
    if _layout_extraction_should_fallback(len(hint_rows), eligible, rows):
        yield {
            'type': 'progress',
            'step': 'layout_parse',
            'message': 'Retrying layout parse with default skip patterns only (model-added filters may have been too tight)…',
        }
        defaults_only = _merge_spending_layout_skip_regexes([])
        rows_retry = _raw_transactions_from_line_hints(hint_rows, defaults_only)
        elig_retry = _layout_eligible_hint_count(hint_rows, defaults_only)
        if len(rows_retry) > len(rows) or not _layout_extraction_should_fallback(
            len(hint_rows), elig_retry, rows_retry
        ):
            rows = rows_retry
            eligible = elig_retry
            skip_patterns = defaults_only
            layout_retry_defaults = True

    layout_attempt_count = len(rows)

    if _layout_extraction_should_fallback(len(hint_rows), eligible, rows):
        yield {
            'type': 'progress',
            'step': 'llm_extract',
            'message': 'Layout parse still thin after retries — falling back to full model extraction…',
        }
        rows = _extract_spending_transactions_llm(statement_text, period_hint)
        yield {
            'type': 'result',
            'rows': rows,
            'meta': {
                'mode': 'llm_full',
                'reason': 'layout_low_yield',
                'hint_row_count': len(hint_rows),
                'eligible_hint_count': eligible,
                'layout_attempt_count': layout_attempt_count,
            },
        }
        return

    yield {
        'type': 'result',
        'rows': rows,
        'meta': {
            'mode': 'layout_hints',
            'hint_row_count': len(hint_rows),
            'eligible_hint_count': eligible,
            'raw_row_count': len(rows),
            'skip_pattern_count': len(skip_patterns),
            'retried_defaults_only': layout_retry_defaults,
        },
    }


def _normalize_spending_transactions(raw_list: list) -> list:
    out = []
    for row in raw_list:
        if not isinstance(row, dict):
            continue
        d = _parse_bank_statement_date(str(row.get('date') or row.get('statement_date') or ''))
        if d is None:
            continue
        desc = str(row.get('description') or '').strip()[:500]
        if not desc:
            desc = 'Bank transaction'
        col_dir, col_amt = _direction_and_amount_from_money_columns(row)
        if col_dir is not None and col_amt is not None:
            amount = round(col_amt, 2)
            direction = col_dir
        else:
            try:
                amount = abs(float(row.get('amount')))
            except (TypeError, ValueError):
                continue
            if amount <= 0:
                continue
            direction = _normalize_spending_direction(row.get('direction'), desc, amount)
        out.append({
            'id': str(uuid.uuid4()),
            'date': d.strftime('%Y-%m-%d'),
            'month': d.strftime('%Y-%m'),
            'description': desc,
            'amount': round(amount, 2),
            'direction': direction,
            'category': None,
            'confidence': None,
            'rationale': '',
        })
    out.sort(key=lambda x: (x['date'], x['description']))
    return out


def _classify_outgoing_descriptions_llm(descriptions: list[str]) -> dict:
    if not descriptions:
        return {}
    client = _get_openai_client()
    if not client:
        return {}
    model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini').strip() or 'gpt-4o-mini'
    system = (
        'Classify outgoing personal spending descriptions into one category from this set only: '
        + ', '.join(SPENDING_ALLOWED_CATEGORIES)
        + '. Use travel for hotels, accommodation, holiday rentals, and tourism (tours, attractions, museums, activities while travelling). '
        'Return only valid JSON as {"items":[{"description":"string","category":"string","confidence":number,"reason":"string"}]}. '
        'confidence must be between 0 and 1.'
    )
    user_msg = 'Descriptions:\n' + '\n'.join(f'- {d}' for d in descriptions[:150])
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_msg},
        ],
        response_format={'type': 'json_object'},
        temperature=0.1,
    )
    raw = completion.choices[0].message.content or '{}'
    parsed, jerr = _parse_llm_json_object(raw, context='spending_classify')
    if jerr:
        return {}
    items = parsed.get('items')
    if not isinstance(items, list):
        return {}
    out = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        desc = str(item.get('description') or '').strip()
        if not desc:
            continue
        cat = str(item.get('category') or 'other').strip().lower()
        if cat not in SPENDING_CATEGORY_SET:
            cat = 'other'
        try:
            conf = float(item.get('confidence', 0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        out[_normalize_label(desc)] = {
            'category': cat,
            'confidence': round(conf, 3),
            'rationale': str(item.get('reason') or '').strip()[:240],
        }
    return out


def _apply_outgoing_classification(rows: list, spending: dict) -> bool:
    changed = False
    overrides = spending.get('classification_overrides') or {}
    cache = spending.get('classification_cache') or {}
    unknown_labels = []
    for row in rows:
        if row.get('direction') != 'outgoing':
            row['category'] = None
            continue
        label = _normalize_label(row.get('description', ''))
        if not label:
            row['category'] = 'other'
            row['confidence'] = 0.0
            row['rationale'] = 'fallback'
            continue
        if label in overrides:
            row['category'] = str(overrides[label]).strip().lower() or 'other'
            if row['category'] not in SPENDING_CATEGORY_SET:
                row['category'] = 'other'
            row['confidence'] = 1.0
            row['rationale'] = 'manual override'
            continue
        cached = cache.get(label)
        if isinstance(cached, dict) and cached.get('category') in SPENDING_CATEGORY_SET:
            row['category'] = cached['category']
            row['confidence'] = cached.get('confidence')
            row['rationale'] = cached.get('rationale') or ''
            continue
        unknown_labels.append(label)

    if unknown_labels:
        desc_map = {}
        for row in rows:
            if row.get('direction') != 'outgoing':
                continue
            label = _normalize_label(row.get('description', ''))
            if label and label not in desc_map:
                desc_map[label] = row.get('description', '')
        llm_result = _classify_outgoing_descriptions_llm([desc_map[k] for k in sorted(set(unknown_labels))])
        for label in set(unknown_labels):
            classified = llm_result.get(label)
            if not classified:
                classified = {'category': 'other', 'confidence': 0.0, 'rationale': 'fallback'}
            cache[label] = classified
            changed = True
        spending['classification_cache'] = cache

    for row in rows:
        if row.get('direction') != 'outgoing':
            continue
        label = _normalize_label(row.get('description', ''))
        if label in overrides:
            continue
        c = cache.get(label) if label else None
        if not isinstance(c, dict):
            c = {'category': 'other', 'confidence': 0.0, 'rationale': 'fallback'}
        cat = str(c.get('category') or 'other').strip().lower()
        if cat not in SPENDING_CATEGORY_SET:
            cat = 'other'
        row['category'] = cat
        row['confidence'] = c.get('confidence')
        row['rationale'] = c.get('rationale') or ''
    return changed


def _invalidate_automatic_classification_for_labels(spending: dict, labels: set[str]) -> bool:
    """Drop cached LLM categories for these labels so the next apply will re-query. Skips manual overrides."""
    if not labels:
        return False
    overrides = spending.get('classification_overrides') or {}
    cache = spending.get('classification_cache') or {}
    changed = False
    for label in labels:
        if not label or label in overrides:
            continue
        if label in cache:
            del cache[label]
            changed = True
    if changed:
        spending['classification_cache'] = cache
    return changed


def _sync_outgoing_transactions_from_cache_for_labels(spending: dict, labels: set[str]) -> None:
    """Apply classification_cache to all outgoing transactions whose description label is in ``labels`` (excluding overrides)."""
    if not labels:
        return
    overrides = spending.get('classification_overrides') or {}
    cache = spending.get('classification_cache') or {}
    for t in spending.get('transactions') or []:
        if t.get('direction') != 'outgoing':
            continue
        label = _normalize_label(t.get('description', ''))
        if not label or label not in labels or label in overrides:
            continue
        c = cache.get(label)
        if not isinstance(c, dict):
            c = {'category': 'other', 'confidence': 0.0, 'rationale': 'fallback'}
        cat = str(c.get('category') or 'other').strip().lower()
        if cat not in SPENDING_CATEGORY_SET:
            cat = 'other'
        t['category'] = cat
        t['confidence'] = c.get('confidence')
        t['rationale'] = c.get('rationale') or ''


def _rerun_spending_categorization_for_month(spending: dict, report_month: str) -> dict:
    """
    Re-run LLM categorisation for outgoing lines in ``report_month``.
    Preserves classification_overrides. Updates cache and all transactions sharing refreshed labels.
    """
    rm = str(report_month or '').strip()[:7]
    if len(rm) != 7 or rm[4] != '-':
        return {'ok': False, 'error': 'report_month (YYYY-MM) is required'}
    txs = spending.get('transactions') or []
    month_rows = [t for t in txs if _report_month_for_spending_tx(t) == rm]
    if not month_rows:
        return {'ok': False, 'error': 'No transactions for that month'}
    overrides = spending.get('classification_overrides') or {}
    labels: set[str] = set()
    for t in month_rows:
        if t.get('direction') != 'outgoing':
            continue
        label = _normalize_label(t.get('description', ''))
        if label:
            labels.add(label)
    _invalidate_automatic_classification_for_labels(spending, labels)
    _apply_outgoing_classification(month_rows, spending)
    refreshed = {l for l in labels if l not in overrides}
    _sync_outgoing_transactions_from_cache_for_labels(spending, refreshed)
    _recompute_monthly_insights(spending, None)
    return {'ok': True, 'labels_refreshed': len(refreshed)}


def _spending_is_paired_leg(t: dict) -> bool:
    return bool(t.get('internal_transfer') and t.get('transfer_pair_id'))


def _spending_excluded_from_insight_metrics(t: dict) -> bool:
    """Paired internal-transfer legs and user-flagged rows are omitted from income/outgoing KPIs."""
    if _spending_is_paired_leg(t):
        return True
    return bool(t.get('insights_excluded'))


def _spending_tx_parsed_date(tx: dict) -> date | None:
    return _parse_iso_date(str(tx.get('date') or ''))


def _spending_pair_amounts_match(a: float, b: float) -> bool:
    x = abs(float(a))
    y = abs(float(b))
    diff = abs(x - y)
    hi = max(x, y)
    if hi < 1e-12:
        return diff <= SPENDING_TRANSFER_MIN_AMOUNT_DIFF + 1e-9
    base = max(SPENDING_TRANSFER_MIN_AMOUNT_DIFF, SPENDING_TRANSFER_LARGE_LEG_PCT * hi)
    if hi < SPENDING_TRANSFER_SMALL_LEG_MAX:
        tol = min(SPENDING_TRANSFER_SMALL_LEG_ABS_CAP, base)
    else:
        tol = min(SPENDING_TRANSFER_LARGE_LEG_ABS_CAP, base)
    return diff <= tol + 1e-9


def _spending_pair_dates_within_window(d0: date | None, d1: date | None) -> bool:
    if d0 is None or d1 is None:
        return False
    return abs((d0 - d1).days) <= SPENDING_TRANSFER_MAX_DAY_GAP


def _clear_auto_transfer_pairs_in_month(txs: list, month_key: str) -> int:
    """
    Remove auto-applied internal transfer links for a reporting month. Manual pairs are kept.
    Returns the number of pair bonds cleared (one per pair, not per leg).
    """
    mk = (month_key or '').strip()[:7]
    pair_ids: set[str] = set()
    for t in txs:
        if str(_report_month_for_spending_tx(t)) != mk:
            continue
        if t.get('pairing_source') == 'auto' and t.get('transfer_pair_id'):
            pair_ids.add(str(t['transfer_pair_id']))
    if not pair_ids:
        return 0
    broken = 0
    for t in txs:
        pid = str(t.get('transfer_pair_id') or '')
        if pid in pair_ids:
            t.pop('transfer_pair_id', None)
            t['internal_transfer'] = False
            t.pop('pairing_source', None)
            broken += 1
    return broken // 2


def _suggest_spending_transfer_pairs(outs: list, ins: list) -> list[tuple[dict, dict]]:
    """
    1:1 matching of outgoing vs incoming: amounts within tolerance, dates within
    SPENDING_TRANSFER_MAX_DAY_GAP, each row at most once. Among valid edges, prefer
    **smaller amount gap** (tighter leg match) then **fewer days apart**, then stable ids
    so closer matches are chosen instead of the first by arbitrary sort order.
    """
    if not outs or not ins:
        return []
    candidates: list[tuple[dict, dict, float, int, str, str]] = []
    for o in outs:
        oid = str(o.get('id') or '')
        if not oid:
            continue
        od = _spending_tx_parsed_date(o)
        o_amt = float(o.get('amount', 0))
        for i in ins:
            iid = str(i.get('id') or '')
            if not iid:
                continue
            i_amt = float(i.get('amount', 0))
            if not _spending_pair_amounts_match(o_amt, i_amt):
                continue
            idt = _spending_tx_parsed_date(i)
            if not _spending_pair_dates_within_window(od, idt):
                continue
            amount_diff = abs(abs(o_amt) - abs(i_amt))
            day_gap = 0
            if od and idt:
                day_gap = abs((od - idt).days)
            # Lower keys are better: tighter amount, same-day / closer dates, deterministic tiebreak
            candidates.append((o, i, amount_diff, day_gap, oid, iid))
    candidates.sort(
        key=lambda r: (r[2], r[3], r[4], r[5]),
    )
    used_o: set[str] = set()
    used_in: set[str] = set()
    pairs: list[tuple[dict, dict]] = []
    for o, i, _ad, _dg, _oid, _iid in candidates:
        oid = str(o.get('id') or '')
        iid = str(i.get('id') or '')
        if not oid or not iid or oid in used_o or iid in used_in:
            continue
        used_o.add(oid)
        used_in.add(iid)
        pairs.append((o, i))
    return pairs


def _apply_spending_pair_to_rows(a: dict, b: dict, source: str) -> None:
    pid = str(uuid.uuid4())
    a['transfer_pair_id'] = pid
    b['transfer_pair_id'] = pid
    a['internal_transfer'] = True
    b['internal_transfer'] = True
    a['pairing_source'] = source
    b['pairing_source'] = source


def _unlink_spending_pair(txs: list, tx: dict) -> bool:
    """Clear pairing on tx and its peer. Returns True if a pair was removed."""
    pid = str(tx.get('transfer_pair_id') or '')
    if not pid:
        return False
    found = 0
    for t in txs:
        if str(t.get('transfer_pair_id') or '') == pid:
            t.pop('transfer_pair_id', None)
            t['internal_transfer'] = False
            t.pop('pairing_source', None)
            found += 1
    return found > 0


def _spending_manual_pair_error(a: dict | None, b: dict | None) -> str | None:
    """Return an error string if a and b cannot be manually linked; else None."""
    if not a or not b:
        return 'Transaction not found'
    if str(a.get('id')) == str(b.get('id')):
        return 'Cannot pair a transaction with itself'
    if str(_report_month_for_spending_tx(a)) != str(_report_month_for_spending_tx(b)):
        return 'Transactions must share the same reporting month'
    dirs = {a.get('direction'), b.get('direction')}
    if dirs != {'incoming', 'outgoing'}:
        return 'One transaction must be outgoing and one incoming'
    if a.get('transfer_pair_id') or b.get('transfer_pair_id'):
        return 'Unlink existing pair(s) before creating a new one'
    am1, am2 = float(a.get('amount', 0)), float(b.get('amount', 0))
    if not _spending_pair_amounts_match(am1, am2):
        return 'Amounts must match within configured tolerance'
    d0 = _spending_tx_parsed_date(a)
    d1 = _spending_tx_parsed_date(b)
    if not _spending_pair_dates_within_window(d0, d1):
        return f'Dates must be within {SPENDING_TRANSFER_MAX_DAY_GAP} day(s)'
    return None


def _apply_auto_pairing_to_tx_pool(txs: list, month_key: str) -> dict:
    """
    Re-run auto internal-transfer pairing for all rows in ``txs`` for ``month_key``.
    ``txs`` is typically the full ``spending['transactions']`` list; may also be
    a throwaway list containing only a month's rows for preview simulation.
    """
    _clear_auto_transfer_pairs_in_month(txs, month_key)
    mk = (month_key or '').strip()[:7]
    month_rows = [t for t in txs if str(_report_month_for_spending_tx(t)) == mk]
    unpaired = [t for t in month_rows if not t.get('transfer_pair_id')]
    outs = [t for t in unpaired if t.get('direction') == 'outgoing']
    ins = [t for t in unpaired if t.get('direction') == 'incoming']
    pair_tuples = _suggest_spending_transfer_pairs(outs, ins)
    for o, i in pair_tuples:
        _apply_spending_pair_to_rows(o, i, 'auto')
    unpaired_out = [t for t in month_rows if t.get('direction') == 'outgoing' and not t.get('transfer_pair_id')]
    unpaired_in = [t for t in month_rows if t.get('direction') == 'incoming' and not t.get('transfer_pair_id')]
    return {
        'applied_pairs': len(pair_tuples),
        'unmatched_outgoing_count': len(unpaired_out),
        'unmatched_incoming_count': len(unpaired_in),
        'unmatched_outgoing_total': round(sum(float(t.get('amount', 0)) for t in unpaired_out), 2),
        'unmatched_incoming_total': round(sum(float(t.get('amount', 0)) for t in unpaired_in), 2),
    }


def apply_auto_transfer_pairing_for_month(spending: dict, month_key: str) -> dict:
    """
    Re-run auto internal-transfer pairing for a reporting month. Clears prior auto pairs only,
    then matches unpaired incoming/outgoing. Manual pairs are preserved.
    """
    txs = spending.get('transactions') or []
    return _apply_auto_pairing_to_tx_pool(txs, month_key)


def simulate_spending_transfer_reconciliation_preview(
    spending: dict,
    report_month: str,
    candidate_rows: list,
) -> dict:
    """
    What-if pairing if ``candidate_rows`` are imported, merged with any existing
    stored transactions for the same reporting month. Does not modify stored data.
    """
    mk = (report_month or '').strip()[:7]
    store = spending.get('transactions') or []
    existing = [copy.deepcopy(t) for t in store if str(_report_month_for_spending_tx(t)) == mk]
    cands = [copy.deepcopy(t) for t in candidate_rows]
    for t in cands:
        t['report_month'] = mk
    pool = existing + cands
    stats = _apply_auto_pairing_to_tx_pool(pool, mk)
    month_rows = [t for t in pool if str(_report_month_for_spending_tx(t)) == mk]
    rec = _spending_transfer_reconciliation_for_month(month_rows)
    cand_ids = {str(t.get('id') or '') for t in cands if t.get('id')}
    pairing: dict = {}
    for t in month_rows:
        tid = str(t.get('id') or '')
        if not tid or tid not in cand_ids:
            continue
        pid = str(t.get('transfer_pair_id') or '')
        if not pid:
            pairing[tid] = {
                'paired': False,
                'peer_id': None,
                'peer_is_from_ledger': False,
                'peer_description': None,
            }
            continue
        other = next(
            (
                x
                for x in month_rows
                if str(x.get('id')) != tid
                and str(x.get('transfer_pair_id') or '') == pid
            ),
            None,
        )
        if not other:
            pairing[tid] = {
                'paired': False,
                'peer_id': None,
                'peer_is_from_ledger': False,
                'peer_description': None,
            }
            continue
        oid = str(other.get('id') or '')
        pairing[tid] = {
            'paired': True,
            'peer_id': oid,
            'peer_is_from_ledger': oid not in cand_ids,
            'peer_description': (str(other.get('description') or ''))[:120] or None,
        }
    return {
        'reconciliation': rec,
        'pairing': pairing,
        'ledger_row_count_in_month': len(existing),
        'auto_applied_pairs_in_simulation': stats['applied_pairs'],
    }


def _month_prev(month_key: str, delta: int = 1) -> str | None:
    try:
        d = datetime.strptime(month_key + '-01', '%Y-%m-%d').date()
    except ValueError:
        return None
    prev = d - relativedelta(months=delta)
    return prev.strftime('%Y-%m')


def _month_next(month_key: str, delta: int = 1) -> str | None:
    try:
        d = datetime.strptime(month_key + '-01', '%Y-%m-%d').date()
    except ValueError:
        return None
    nxt = d + relativedelta(months=delta)
    return nxt.strftime('%Y-%m')


SUBSCRIPTION_SIGNAL_WINDOW_MONTHS = 6
SUBSCRIPTION_MAX_AMOUNT_SPREAD_RATIO = 0.25  # (max-min)/mean across months with spend
# Merging lookalike labels (e.g. "HALIFAX" / "HALIFAX DD", "SPOTIFY" / "SPOTIFY LDN")
SUBSCRIPTION_FUZZY_MIN_PREFIX_LEN = 4
SUBSCRIPTION_LABEL_MERGE_AMOUNT_TOL = 0.20  # (max-min)/max when two labels both have spend the same month

# Strip from normalized labels so "VISA SPOTIFY LONDON" and "SPOTIFY" can align (prefix is often not the merchant).
_SUBSCRIPTION_MERGE_STRIP_LEADING = frozenset({
    'atm', 'bac', 'bacs', 'card', 'cash', 'cdr', 'chg', 'cheque', 'chq', 'cont',
    'contactless', 'credit', 'crdt', 'dd', 'debit', 'dep', 'deposit', 'direct', 'ecom',
    'electron', 'faster', 'fee', 'fps', 'from', 'fpi', 'fpo', 'intnl', 'intl', 'mc', 'maestro',
    'mobile', 'online', 'pay', 'payment', 'pmnt', 'pos', 'pmt', 'purch', 'purchase', 'sepa',
    'standing', 'tfr', 'to', 'trf', 'transfer', 'vdp', 'visa', 'withdrawal',
})
_SUBSCRIPTION_MERGE_STRIP_TRAILING = frozenset({
    'crdt', 'credit', 'debit', 'electron', 'eu', 'eur', 'gbr', 'gb', 'intl', 'intnl', 'ldn',
    'lond', 'london', 'maestro', 'mc', 'us', 'usd', 'uk', 'vis', 'visa',
})


def _subscription_labels_trim_for_merge(s: str) -> str:
    """Drop common bank / location noise from the start and end of a normalized label."""
    toks = (s or '').strip().split()
    if not toks:
        return ''
    while toks and toks[0] in _SUBSCRIPTION_MERGE_STRIP_LEADING:
        toks = toks[1:]
    while toks and toks[-1] in _SUBSCRIPTION_MERGE_STRIP_TRAILING:
        toks = toks[:-1]
    return ' '.join(toks)


def _subscription_labels_fuzzy_match(a: str, b: str) -> bool:
    """
    True if ``a`` and ``b`` are the same "merchant" for subscription grouping:
    - exact match, or
    - longer is ``shorter + " …"`` (word-boundary prefix) when the shorter is long enough, or
    - same first word, length ≥ 4 (covers NETFLIX.COM vs NETFLIX LDN, etc.).

    Common bank prefixes (e.g. ``visa``, ``pos``, ``direct debit``) and trailing place/payment
    tokens (``london``, ``vis``) are stripped first so the merchant name lines up.

    Very short strings only match if equal, to avoid spurious links.
    """
    a = _subscription_labels_trim_for_merge((a or '').strip())
    b = _subscription_labels_trim_for_merge((b or '').strip())
    if not a or not b:
        return False
    if a == b:
        return True
    s, t = (a, b) if len(a) <= len(b) else (b, a)
    if len(s) < SUBSCRIPTION_FUZZY_MIN_PREFIX_LEN:
        return False
    if t == s or t.startswith(s + ' '):
        return True
    wa = a.split()
    wb = b.split()
    if wa and wb and len(wa[0]) >= SUBSCRIPTION_FUZZY_MIN_PREFIX_LEN and wa[0] == wb[0]:
        return True
    return False


def _subscription_pair_merge_amounts_ok(
    by_a: dict,
    by_b: dict,
    months_seq: list[str],
    tol: float = SUBSCRIPTION_LABEL_MERGE_AMOUNT_TOL,
) -> bool:
    """
    If two labels both have non-trivial spend in the same month, the two amounts
    must be within ``tol`` of each other (by relative error vs the larger) so we
    do not join unrelated same-prefix charges.
    """
    for m in months_seq:
        try:
            aa = float(by_a.get(m) or 0.0)
            bb = float(by_b.get(m) or 0.0)
        except (TypeError, ValueError):
            continue
        if aa <= 0.005 or bb <= 0.005:
            continue
        lo, hi = (aa, bb) if aa <= bb else (bb, aa)
        if hi <= 0:
            return False
        if (hi - lo) / hi > tol + 1e-9:
            return False
    return True


def _uf_find(parent: list, i: int) -> int:
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _uf_union(parent: list, rank: list, i: int, j: int) -> None:
    ri, rj = _uf_find(parent, i), _uf_find(parent, j)
    if ri == rj:
        return
    if rank[ri] < rank[rj]:
        parent[ri] = rj
    else:
        parent[rj] = ri
        if rank[ri] == rank[rj]:
            rank[ri] += 1


def _merge_subscription_signal_label_groups(
    label_month_totals: dict,
    label_sample_desc: dict,
    months_seq: list[str],
) -> tuple[dict, dict]:
    """
    Union labels that are fuzzy name matches and, when they overlap in a month,
    have similar total amounts. Rebuilds total-by-month and sample descriptions
    (longest original description is kept for display).
    """
    labels = [k for k in label_month_totals.keys() if k]
    n = len(labels)
    if n <= 1:
        return label_month_totals, label_sample_desc
    parent = list(range(n))
    rank = [0] * n
    for i in range(n):
        for j in range(i + 1, n):
            li, lj = labels[i], labels[j]
            if not _subscription_labels_fuzzy_match(li, lj):
                continue
            if not _subscription_pair_merge_amounts_ok(
                label_month_totals[li],
                label_month_totals[lj],
                months_seq,
            ):
                continue
            _uf_union(parent, rank, i, j)

    groups: dict[int, set[str]] = {}
    for i, lab in enumerate(labels):
        r = _uf_find(parent, i)
        groups.setdefault(r, set()).add(lab)

    merged_totals: dict = {}
    merged_desc: dict = {}
    for g in groups.values():
        if len(g) == 1:
            only = next(iter(g))
            merged_totals[only] = dict(label_month_totals[only])
            merged_desc[only] = label_sample_desc.get(only, only)
            continue
        by_month: dict[str, float] = defaultdict(float)
        for lab in g:
            for mk, v in (label_month_totals.get(lab) or {}).items():
                try:
                    by_month[mk] += float(v)
                except (TypeError, ValueError):
                    continue
        # Canonical key: longer normalized name first (most specific for sorting / tests)
        canonical = max(g, key=lambda x: (len(x), x))
        merged_totals[canonical] = dict(by_month)
        best = ''
        for lab in g:
            s = label_sample_desc.get(lab) or lab
            if len(s) > len(best):
                best = s
        merged_desc[canonical] = best
    return merged_totals, merged_desc


def _months_window_ending(month_key: str, n: int) -> list[str]:
    """Chronological list of up to ``n`` calendar months ending at ``month_key`` (inclusive)."""
    out: list[str] = []
    cur = str(month_key or '').strip()[:7]
    if len(cur) != 7 or cur[4] != '-':
        return out
    for _ in range(max(1, n)):
        out.append(cur)
        p = _month_prev(cur, 1)
        if not p:
            break
        cur = p
    out.reverse()
    return out


def _longest_consecutive_month_streak(sorted_months: list[str]) -> int:
    if not sorted_months:
        return 0
    best = 1
    cur_run = 1
    for i in range(1, len(sorted_months)):
        if _month_next(sorted_months[i - 1], 1) == sorted_months[i]:
            cur_run += 1
            best = max(best, cur_run)
        else:
            cur_run = 1
    return best


def _subscription_signals_for_month(spending: dict, month_key: str) -> list:
    """
    Cross-month recurring / subscription-style spend: similar normalized merchant labels
    (prefix / same first word) are merged when monthly amounts are compatible; then
    stable multi-month totals qualify. Excludes internal-transfer legs.
    """
    months_seq = _months_window_ending(month_key, SUBSCRIPTION_SIGNAL_WINDOW_MONTHS)
    if not months_seq:
        return []
    month_set = set(months_seq)
    label_month_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    label_sample_desc: dict[str, str] = {}

    for t in spending.get('transactions') or []:
        if t.get('direction') != 'outgoing':
            continue
        if _spending_excluded_from_insight_metrics(t):
            continue
        rm = _report_month_for_spending_tx(t)
        if rm not in month_set:
            continue
        label = _normalize_label(t.get('description', ''))
        if not label:
            continue
        try:
            amt = float(t.get('amount', 0))
        except (TypeError, ValueError):
            continue
        label_month_totals[label][rm] += amt
        if label not in label_sample_desc:
            label_sample_desc[label] = str(t.get('description', ''))[:120]

    label_month_totals, label_sample_desc = _merge_subscription_signal_label_groups(
        label_month_totals, label_sample_desc, months_seq
    )

    out: list = []
    for label, by_month in label_month_totals.items():
        present = sorted(m for m, v in by_month.items() if v > 0.005)
        if month_key not in present or len(present) < 2:
            continue
        totals = [by_month[m] for m in present]
        mean_t = sum(totals) / len(totals)
        if mean_t <= 0:
            continue
        spread = (max(totals) - min(totals)) / mean_t if len(totals) > 1 else 0.0
        streak = _longest_consecutive_month_streak(present)
        in_window = len([mm for mm in months_seq if mm in present])
        qualifies = (streak >= 2 or len(present) >= 3) and spread <= SUBSCRIPTION_MAX_AMOUNT_SPREAD_RATIO
        if not qualifies:
            continue
        last_amt = round(float(by_month.get(month_key, 0)), 2)
        prev_m = None
        try:
            mi = present.index(month_key)
            if mi > 0:
                prev_m = present[mi - 1]
        except ValueError:
            prev_m = None
        prev_amt = round(float(by_month.get(prev_m, 0)), 2) if prev_m else None
        if prev_amt is not None and prev_amt > 0:
            if last_amt > prev_amt * 1.05:
                trend = 'up'
            elif last_amt < prev_amt * 0.95:
                trend = 'down'
            else:
                trend = 'flat'
        elif prev_m is not None and prev_amt == 0:
            trend = 'up' if last_amt > 0 else 'flat'
        else:
            trend = 'insufficient_history'

        out.append({
            'label': label,
            'display_description': label_sample_desc.get(label, label),
            'months_active': len(present),
            'months_in_window': in_window,
            'consecutive_streak': streak,
            'last_amount': last_amt,
            'total_last_month': last_amt,
            'amount_last_month': last_amt,
            'amount_avg_active_months': round(mean_t, 2),
            'amount_variability': round(spread, 3) if len(totals) > 1 else 0.0,
            'trend': trend,
        })
    out.sort(key=lambda x: (-x['months_active'], -x['amount_last_month']))
    return out[:20]


def _spending_transfer_reconciliation_for_month(month_rows: list) -> dict:
    """Reconciliation metadata for a month's transactions (paired vs unmatched legs)."""
    pair_ids: set[str] = set()
    for t in month_rows:
        if t.get('transfer_pair_id') and t.get('internal_transfer'):
            pair_ids.add(str(t['transfer_pair_id']))
    pair_count = len(pair_ids)
    paired_out = sum(
        float(t.get('amount', 0)) for t in month_rows
        if t.get('direction') == 'outgoing' and str(t.get('transfer_pair_id') or '') in pair_ids
    )
    paired_in = sum(
        float(t.get('amount', 0)) for t in month_rows
        if t.get('direction') == 'incoming' and str(t.get('transfer_pair_id') or '') in pair_ids
    )
    paired_out = round(paired_out, 2)
    paired_in = round(paired_in, 2)
    leg_mismatch = round(abs(paired_out - paired_in), 2)

    uo = [t for t in month_rows if t.get('direction') == 'outgoing' and not _spending_is_paired_leg(t)]
    ui = [t for t in month_rows if t.get('direction') == 'incoming' and not _spending_is_paired_leg(t)]

    def _preview_list(rows: list) -> list:
        out = []
        for t in sorted(
            rows,
            key=lambda x: (str(x.get('date') or ''), -float(x.get('amount', 0))),
        )[:SPENDING_TRANSFER_UNMATCHED_PREVIEW_MAX]:
            out.append({
                'id': t.get('id'),
                'date': t.get('date'),
                'amount': round(float(t.get('amount', 0)), 2),
                'description': (str(t.get('description') or ''))[:120],
            })
        return out

    return {
        'pair_count': pair_count,
        'internal_transfer_outgoing_total': paired_out,
        'paired_incoming_total': paired_in,
        'paired_leg_amount_mismatch': leg_mismatch,
        'unmatched_outgoing': {
            'count': len(uo),
            'total': round(sum(float(t.get('amount', 0)) for t in uo), 2),
            'items': _preview_list(uo),
        },
        'unmatched_incoming': {
            'count': len(ui),
            'total': round(sum(float(t.get('amount', 0)) for t in ui), 2),
            'items': _preview_list(ui),
        },
    }


def _insight_category_amount_map(insight: dict | None) -> dict[str, float]:
    if not isinstance(insight, dict):
        return {}
    m: dict[str, float] = {}
    for row in insight.get('category_breakdown') or []:
        if not isinstance(row, dict):
            continue
        c = str(row.get('category') or 'unclassified').strip().lower()
        try:
            m[c] = float(row.get('amount', 0) or 0)
        except (TypeError, ValueError):
            m[c] = 0.0
    return m


def _spending_report_months_with_data(txs: list) -> set[str]:
    """Calendar months that have at least one transaction (imported / recorded). Excludes
    'empty' previous months that would otherwise be treated as £0 in category baselines."""
    out: set[str] = set()
    for t in txs:
        rm = _report_month_for_spending_tx(t)
        if rm:
            out.add(str(rm))
    return out


def _outgoing_category_total_for_spending_month(txs: list, month_key: str, category: str) -> float:
    cat_target = str(category).strip().lower()
    month_sum = 0.0
    for tx in txs:
        if _report_month_for_spending_tx(tx) != month_key or tx.get('direction') != 'outgoing':
            continue
        if _spending_excluded_from_insight_metrics(tx):
            continue
        tx_cat = str(tx.get('category') or 'unclassified').strip().lower()
        if tx_cat not in SPENDING_CATEGORY_SET:
            tx_cat = 'unclassified'
        if tx_cat == cat_target:
            month_sum += float(tx.get('amount', 0))
    return month_sum


def _trailing_income_outgoing_averages(
    spending: dict, month_key: str, max_prior_months: int
) -> tuple[float | None, float | None, int]:
    imap = spending.get('monthly_insights') or {}
    incomes: list[float] = []
    outgoings: list[float] = []
    for i in range(1, max_prior_months + 1):
        m = _month_prev(month_key, i)
        if not m:
            break
        ins = imap.get(m)
        if not isinstance(ins, dict):
            continue
        try:
            incomes.append(float(ins.get('income_total', 0) or 0))
            outgoings.append(float(ins.get('outgoing_total', 0) or 0))
        except (TypeError, ValueError):
            continue
    n = len(outgoings)
    if n == 0:
        return None, None, 0
    return sum(incomes) / n, sum(outgoings) / n, n


def _build_budget_action_items(
    income: float,
    outgoing: float,
    net: float,
    savings_rate: float | None,
    prev_savings_rate: float | None,
    savings_rate_delta: float | None,
    outgoing_delta: float | None,
    net_delta: float | None,
    outgoing_vs_trailing_6m_pct: float | None,
    anomalies: list[dict],
    subscription_signals: list[dict],
    top_transactions_pareto_pct: float | None,
    category_trends: list[dict],
) -> list[dict]:
    items: list[dict] = []
    if net < 0:
        items.append({
            'kind': 'net_negative',
            'title': 'This month is cash-flow negative',
            'detail': f'Outgoings exceeded income by {abs(net):.2f} after non-transfer items.',
            'amount_hint': round(abs(net), 2),
        })
    if net_delta is not None and net_delta < 0 and net >= 0:
        items.append({
            'kind': 'net_tightening',
            'title': 'Net savings fell vs last month',
            'detail': f'Net is £{net_delta:.2f} lower than the previous month — check higher outgoings or lower income.',
            'amount_hint': round(abs(net_delta), 2),
        })
    if outgoing_delta is not None and outgoing_delta > 0 and len(items) < 6:
        items.append({
            'kind': 'outgoing_up',
            'title': 'Outgoing is higher than last month',
            'detail': f'Up £{outgoing_delta:.2f} vs the previous month — see category trends and largest lines.',
            'amount_hint': round(outgoing_delta, 2),
        })
    if (
        outgoing_vs_trailing_6m_pct is not None
        and outgoing_vs_trailing_6m_pct > 8.0
        and len(items) < 6
    ):
        items.append({
            'kind': 'above_trailing_baseline',
            'title': 'Well above your recent spending norm',
            'detail': f'Outgoing is about {outgoing_vs_trailing_6m_pct:.0f}% higher than your trailing 6-month average (where data exists).',
            'amount_hint': None,
        })
    for a in (anomalies or [])[:2]:
        if len(items) >= 6:
            break
        cat = a.get('category', '')
        n_base = a.get('baseline_months')
        if isinstance(n_base, int) and n_base > 0:
            base_phrase = (
                f'the average over {n_base} prior month(s) with recorded data'
                if n_base != 1
                else 'the prior month with recorded data'
            )
        else:
            base_phrase = 'the recent baseline for this category'
        items.append({
            'kind': 'category_spike',
            'title': f'"{cat}" is unusually high',
            'detail': f'Spent {a.get("delta_pct", 0):.0f}% more than {base_phrase} — worth a look.',
            'amount_hint': a.get('amount'),
        })
    if (
        savings_rate is not None
        and prev_savings_rate is not None
        and savings_rate_delta is not None
        and savings_rate_delta < -2.0
        and len(items) < 5
    ):
        items.append({
            'kind': 'savings_rate_slip',
            'title': 'Savings rate dropped vs last month',
            'detail': f'From {prev_savings_rate:.1f}% to {savings_rate:.1f}% of income kept after outgoings.',
            'amount_hint': None,
        })
    for sig in sorted(
        (subscription_signals or []),
        key=lambda s: -float(s.get('total_last_month') or s.get('last_amount') or 0),
    ):
        if len(items) >= 6:
            break
        if str(sig.get('trend') or '') != 'up':
            continue
        name = (sig.get('display_description') or sig.get('label') or 'A recurring charge')[:80]
        amt = float(sig.get('total_last_month') or sig.get('last_amount') or 0)
        items.append({
            'kind': 'subscription_up',
            'title': f'Recurring: {name}',
            'detail': 'This charge went up compared with the last time you paid it.',
            'amount_hint': round(amt, 2),
        })
    spike_cats = {str(a.get('category') or '').lower() for a in (anomalies or [])}
    for ct in (category_trends or []):
        if len(items) >= 6:
            break
        cname = str(ct.get('category') or '')[:64]
        if cname.lower() in spike_cats:
            continue
        vs3 = ct.get('vs_3m_pct')
        if vs3 is None or vs3 <= 20:
            continue
        items.append({
            'kind': 'category_vs_3m',
            'title': f'"{cname}" is up vs 3-month average',
            'detail': f'About {vs3:.0f}% more than the average in prior months (with data) for this category.',
            'amount_hint': ct.get('amount'),
        })
    if (
        top_transactions_pareto_pct is not None
        and top_transactions_pareto_pct >= 50.0
        and len(items) < 6
    ):
        items.append({
            'kind': 'pareto_concentration',
            'title': 'A few big purchases dominate spend',
            'detail': f'Your 5 largest outgoings are about {top_transactions_pareto_pct:.0f}% of total — review the largest list.',
            'amount_hint': None,
        })
    out: list[dict] = []
    seen: set[str] = set()
    for it in items:
        key = f"{it['kind']}:{it.get('title', '')[:50]}"
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out[:6]


def _compute_monthly_insight(spending: dict, month_key: str) -> dict:
    txs = spending.get('transactions') or []
    months_with_data = _spending_report_months_with_data(txs)
    month_rows = [t for t in txs if _report_month_for_spending_tx(t) == month_key]
    transfer_reconciliation = _spending_transfer_reconciliation_for_month(month_rows)
    # KPIs: exclude internal transfer legs and user-excluded rows
    income_rows = [
        t for t in month_rows
        if t.get('direction') == 'incoming' and not _spending_excluded_from_insight_metrics(t)
    ]
    incoming = round(sum(float(t.get('amount', 0)) for t in income_rows), 2)
    outgoing_rows = [
        t for t in month_rows
        if t.get('direction') == 'outgoing' and not _spending_excluded_from_insight_metrics(t)
    ]
    outgoing = round(sum(float(t.get('amount', 0)) for t in outgoing_rows), 2)
    net = round(incoming - outgoing, 2)
    savings_rate = round((net / incoming) * 100, 2) if incoming > 0 else None

    category_totals = defaultdict(float)
    merchant_totals = defaultdict(float)
    for tx in outgoing_rows:
        cat = str(tx.get('category') or 'unclassified').strip().lower()
        if cat not in SPENDING_CATEGORY_SET:
            cat = 'unclassified'
        category_totals[cat] += float(tx.get('amount', 0))
        merchant_key = _normalize_label(tx.get('description', ''))[:80] or 'unknown'
        merchant_totals[merchant_key] += float(tx.get('amount', 0))

    categories = [
        {'category': k, 'amount': round(v, 2)}
        for k, v in category_totals.items()
    ]
    categories.sort(key=lambda x: x['amount'], reverse=True)
    merchants = [
        {'merchant': k, 'amount': round(v, 2)}
        for k, v in merchant_totals.items()
    ]
    merchants.sort(key=lambda x: x['amount'], reverse=True)

    prev_month = _month_prev(month_key, 1)
    prev_insight = (spending.get('monthly_insights') or {}).get(prev_month) if prev_month else None
    outgoing_delta = None
    net_delta = None
    income_delta = None
    prev_savings_rate: float | None = None
    savings_rate_delta: float | None = None
    if isinstance(prev_insight, dict):
        try:
            outgoing_delta = round(outgoing - float(prev_insight.get('outgoing_total', 0)), 2)
            net_delta = round(net - float(prev_insight.get('net', 0)), 2)
            income_delta = round(incoming - float(prev_insight.get('income_total', 0)), 2)
        except (TypeError, ValueError):
            outgoing_delta = None
            net_delta = None
            income_delta = None
        try:
            psr = prev_insight.get('savings_rate')
            prev_savings_rate = float(psr) if psr is not None else None
        except (TypeError, ValueError):
            prev_savings_rate = None
        if savings_rate is not None and prev_savings_rate is not None:
            savings_rate_delta = round(savings_rate - prev_savings_rate, 2)

    inc3, out3, n3 = _trailing_income_outgoing_averages(spending, month_key, 3)
    inc6, out6, n6 = _trailing_income_outgoing_averages(spending, month_key, 6)
    outgoing_vs_trailing_6m_pct: float | None = None
    if out6 and out6 > 0.005:
        outgoing_vs_trailing_6m_pct = round((outgoing - out6) / out6 * 100.0, 1)
    income_vs_trailing_6m_pct: float | None = None
    if inc6 and inc6 > 0.005:
        income_vs_trailing_6m_pct = round((incoming - inc6) / inc6 * 100.0, 1)

    recurring = []
    count_by_desc = defaultdict(int)
    for tx in outgoing_rows:
        count_by_desc[_normalize_label(tx.get('description', ''))] += 1
    for desc, count in count_by_desc.items():
        if desc and count >= 2:
            recurring.append({'description': desc, 'occurrences': count})

    min_anomaly = max(
        SPENDING_ANOMALY_MIN_GBP,
        outgoing * SPENDING_ANOMALY_MIN_OUTGOING_PCT,
    ) if outgoing > 0 else SPENDING_ANOMALY_MIN_GBP
    prev_cat_map = _insight_category_amount_map(prev_insight if isinstance(prev_insight, dict) else None)
    anomalies = []
    for cat_row in categories:
        cat = cat_row['category']
        # Baseline: mean of *monthly* category spend (totals) for up to 3 previous calendar
        # months, not the mean of individual transaction lines. Only include months that
        # already have at least one recorded transaction — do not treat "no data yet" as £0.
        monthly_totals: list[float] = []
        for i in range(1, 4):
            m = _month_prev(month_key, i)
            if not m or m not in months_with_data:
                continue
            monthly_totals.append(_outgoing_category_total_for_spending_month(txs, m, cat))
        if monthly_totals:
            avg = sum(monthly_totals) / len(monthly_totals)
            amt = float(cat_row['amount'])
            if (
                avg > 0
                and amt > (avg * 1.5)
                and amt >= min_anomaly
            ):
                d_pct = round((amt - avg) / avg * 100.0, 1) if avg > 0 else 0.0
                anomalies.append({
                    'kind': 'category_spike',
                    'category': cat,
                    'amount': round(amt, 2),
                    'baseline_avg': round(avg, 2),
                    'delta_pct': d_pct,
                    'baseline_months': len(monthly_totals),
                })

    largest_src = sorted(outgoing_rows, key=lambda x: -float(x.get('amount', 0)))[:10]
    largest_outgoing = [
        {
            'id': t.get('id'),
            'date': t.get('date'),
            'description': str(t.get('description', ''))[:200],
            'amount': round(float(t.get('amount', 0)), 2),
            'category': str(t.get('category') or 'unclassified'),
        }
        for t in largest_src
    ]

    cat_total_sum = sum(category_totals.values())
    category_breakdown = [
        {
            'category': k,
            'amount': round(v, 2),
            'pct_of_outgoing': round(100.0 * v / cat_total_sum, 1) if cat_total_sum > 0 else 0.0,
        }
        for k, v in sorted(category_totals.items(), key=lambda x: -x[1])
    ]

    category_trends: list[dict] = []
    for cat_row in categories[:6]:
        cat = cat_row['category']
        amt = float(cat_row['amount'])
        prev_amt = float(prev_cat_map.get(str(cat).lower(), 0.0))
        mom_delta = round(amt - prev_amt, 2) if prev_month else None
        mom_delta_pct: float | None = None
        if prev_amt and prev_amt > 0.005:
            mom_delta_pct = round((amt - prev_amt) / prev_amt * 100.0, 1)
        hist_cat_amt: list[float] = []
        for i in range(1, 4):
            m = _month_prev(month_key, i)
            if not m or m not in months_with_data:
                continue
            hist_cat_amt.append(_outgoing_category_total_for_spending_month(txs, m, cat))
        vs_3m_avg: float | None
        if hist_cat_amt:
            vs_3m_avg = sum(hist_cat_amt) / len(hist_cat_amt)
        else:
            vs_3m_avg = None
        vs_3m_pct: float | None = None
        if vs_3m_avg and vs_3m_avg > 0.005:
            vs_3m_pct = round((amt - vs_3m_avg) / vs_3m_avg * 100.0, 1)
        category_trends.append({
            'category': cat,
            'amount': round(amt, 2),
            'mom_delta': mom_delta,
            'mom_delta_pct': mom_delta_pct,
            'trailing_3m_avg': round(vs_3m_avg, 2) if vs_3m_avg is not None else None,
            'vs_3m_pct': vs_3m_pct,
            'trailing_3m_months_count': len(hist_cat_amt),
        })

    sorted_out_amounts = sorted((float(t.get('amount', 0)) for t in outgoing_rows), reverse=True)
    top5_sum = sum(sorted_out_amounts[:5])
    top_transactions_pareto_pct = (
        round(100.0 * top5_sum / outgoing, 1) if outgoing > 0 else None
    )

    subscription_signals = _subscription_signals_for_month(spending, month_key)

    budget_action_items = _build_budget_action_items(
        incoming,
        outgoing,
        net,
        savings_rate,
        prev_savings_rate,
        savings_rate_delta,
        outgoing_delta,
        net_delta,
        outgoing_vs_trailing_6m_pct,
        anomalies,
        subscription_signals,
        top_transactions_pareto_pct,
        category_trends,
    )

    return {
        'month': month_key,
        'transaction_count': len(month_rows),
        'income_total': incoming,
        'outgoing_total': outgoing,
        'net': net,
        'savings_rate': savings_rate,
        'savings_rate_delta_vs_prev_month': savings_rate_delta,
        'top_categories': categories[:6],
        'top_merchants': merchants[:6],
        'outgoing_delta_vs_prev_month': outgoing_delta,
        'income_delta_vs_prev_month': income_delta,
        'net_delta_vs_prev_month': net_delta,
        'income_trailing_avg_3m': None if inc3 is None else round(inc3, 2),
        'outgoing_trailing_avg_3m': None if out3 is None else round(out3, 2),
        'trailing_3m_months_count': n3,
        'income_trailing_avg_6m': None if inc6 is None else round(inc6, 2),
        'outgoing_trailing_avg_6m': None if out6 is None else round(out6, 2),
        'trailing_6m_months_count': n6,
        'outgoing_vs_trailing_6m_pct': outgoing_vs_trailing_6m_pct,
        'income_vs_trailing_6m_pct': income_vs_trailing_6m_pct,
        'category_trends': category_trends,
        'anomalies': anomalies[:6],
        'recurring_candidates': recurring[:8],
        'largest_outgoing': largest_outgoing,
        'category_breakdown': category_breakdown,
        'top_transactions_pareto_pct': top_transactions_pareto_pct,
        'subscription_signals': subscription_signals,
        'budget_action_items': budget_action_items,
        'transfer_reconciliation': transfer_reconciliation,
        'updated_at': datetime.utcnow().isoformat() + 'Z',
    }


def _recompute_monthly_insights(spending: dict, months: set[str] | None = None) -> None:
    txs = spending.get('transactions') or []
    insight_map = spending.get('monthly_insights') or {}
    if months is None:
        months = {str(_report_month_for_spending_tx(t)) for t in txs if _report_month_for_spending_tx(t)}
        for k in list(insight_map.keys()):
            if k not in months:
                insight_map.pop(k, None)
    for m in sorted(months):
        if not m:
            continue
        has_rows = any(_report_month_for_spending_tx(t) == m for t in txs)
        if not has_rows:
            insight_map.pop(m, None)
            continue
        insight_map[m] = _compute_monthly_insight(spending, m)
    spending['monthly_insights'] = insight_map

# --- LLM "savings coach" --------------------------------------------------------------------
SAVINGS_ADVICE_TREND_MAX_PRIOR = 12
SAVINGS_ADVICE_LLM_MAX_CONTEXT_CHARS = 200_000
# Line-level context (capped: third-party still receives real descriptions/amounts).
SAVINGS_ADVICE_FOCAL_TX_MAX = 300
SAVINGS_ADVICE_DESC_MAX = 200
SAVINGS_ADVICE_PRIOR_MONTHS_TX_DETAIL = 2
SAVINGS_ADVICE_PRIOR_TX_PER_MONTH = 60


def _savings_trend_row_compact(ins: dict, month_key: str) -> dict:
    top_raw = ins.get('top_categories') or []
    top3 = []
    for t in top_raw[:3]:
        if not isinstance(t, dict):
            continue
        try:
            amt = round(float(t.get('amount', 0) or 0), 2)
        except (TypeError, ValueError):
            amt = 0.0
        c = str(t.get('category', '') or '').strip()
        if c:
            top3.append({'category': c, 'amount': amt})
    try:
        inc = float(ins.get('income_total', 0) or 0)
    except (TypeError, ValueError):
        inc = 0.0
    try:
        outg = float(ins.get('outgoing_total', 0) or 0)
    except (TypeError, ValueError):
        outg = 0.0
    try:
        net = float(ins.get('net', 0) or 0)
    except (TypeError, ValueError):
        net = 0.0
    sr = ins.get('savings_rate')
    if sr is not None:
        try:
            sr = round(float(sr), 2)
        except (TypeError, ValueError):
            sr = None
    return {
        'month': month_key,
        'income_total': round(inc, 2),
        'outgoing_total': round(outg, 2),
        'net': round(net, 2),
        'savings_rate': sr,
        'top_categories': top3,
    }


def _savings_advice_serialize_tx(t: dict) -> dict:
    desc = str(t.get('description') or '').strip()
    if len(desc) > SAVINGS_ADVICE_DESC_MAX:
        desc = desc[:SAVINGS_ADVICE_DESC_MAX - 1] + '…'
    try:
        amt = round(float(t.get('amount', 0) or 0), 2)
    except (TypeError, ValueError):
        amt = 0.0
    in_kpi = not _spending_excluded_from_insight_metrics(t)
    return {
        'date': str(t.get('date') or '')[:10],
        'description': desc,
        'direction': str(t.get('direction') or ''),
        'amount': amt,
        'category': (str(t.get('category') or '').strip() or None),
        'counts_in_month_totals': in_kpi,
        'internal_transfer_leg': _spending_is_paired_leg(t),
    }


def _collect_savings_advice_tx_for_month(
    spending: dict, month_key: str, max_count: int
) -> tuple[list[dict], dict]:
    """
    Serialize transactions for one report month, newest-first by amount when over max_count
    (keeps the largest lines so limits still allow deeper insights). Sorted by date, then
    description in the final list.
    """
    txs = [t for t in (spending.get('transactions') or []) if _report_month_for_spending_tx(t) == month_key]
    n = len(txs)
    meta: dict = {'count_in_month': n, 'count_sent': 0, 'truncated': False}
    if n == 0:
        return [], meta
    if n <= max_count:
        rows = sorted(txs, key=lambda x: (str(x.get('date') or ''), str(x.get('description') or '')))
        ser = [_savings_advice_serialize_tx(t) for t in rows]
        meta['count_sent'] = n
        return ser, meta
    def _abs_amt(t: dict) -> float:
        try:
            return abs(float(t.get('amount', 0) or 0))
        except (TypeError, ValueError):
            return 0.0
    ranked = sorted(txs, key=_abs_amt, reverse=True)
    kept = ranked[:max_count]
    kept.sort(key=lambda x: (str(x.get('date') or ''), str(x.get('description') or '')))
    ser = [_savings_advice_serialize_tx(t) for t in kept]
    meta['count_sent'] = max_count
    meta['truncated'] = True
    return ser, meta


def _build_savings_advice_context(
    spending: dict, focal_month: str
) -> tuple[dict | None, int, str | None, dict | None]:
    """
    Focal = full stored insight + per-line transactions (capped). Prior months = trend_series
    and optional per-line sample for the two most recent months before focal. Transaction lists
    are sorted by date for the model unless truncation applied (largest |amount| lines kept).
    """
    insight_map = spending.get('monthly_insights') or {}
    focal = insight_map.get(focal_month)
    if not isinstance(focal, dict) or not focal:
        return None, 0, 'no_insight', None
    all_keys = [str(k) for k in insight_map.keys() if k]
    all_sorted_desc = sorted(all_keys, reverse=True)
    priors_newest_first = [m for m in all_sorted_desc if m < focal_month][:SAVINGS_ADVICE_TREND_MAX_PRIOR]
    trend_months_oldest_first = list(reversed(priors_newest_first))
    trend_series: list[dict] = []
    for m in trend_months_oldest_first:
        ins = insight_map.get(m)
        if not isinstance(ins, dict):
            continue
        trend_series.append(_savings_trend_row_compact(ins, m))
    focal_tx, focal_meta = _collect_savings_advice_tx_for_month(
        spending, focal_month, SAVINGS_ADVICE_FOCAL_TX_MAX
    )
    prior_tx_blocks: list[dict] = []
    for pm in priors_newest_first[:SAVINGS_ADVICE_PRIOR_MONTHS_TX_DETAIL]:
        p_rows, p_meta = _collect_savings_advice_tx_for_month(
            spending, pm, SAVINGS_ADVICE_PRIOR_TX_PER_MONTH
        )
        if not p_rows and p_meta.get('count_in_month', 0) == 0:
            continue
        prior_tx_blocks.append({
            'month': pm,
            'meta': p_meta,
            'transactions': p_rows,
        })
    meta_out = {
        'focal_transactions': dict(focal_meta),
        'prior_transaction_months_included': len(prior_tx_blocks),
    }
    context = {
        'focal_month': focal_month,
        'insight_focal': copy.deepcopy(focal),
        'focal_month_transactions': focal_tx,
        'focal_transaction_meta': focal_meta,
        'trend_series': trend_series,
        'trend_months_included': len(trend_series),
        'prior_month_transaction_samples': prior_tx_blocks,
    }
    return context, len(trend_series), None, meta_out


def _validate_savings_advice_payload(d: dict) -> dict | None:
    if not isinstance(d, dict):
        return None
    summary = d.get('summary')
    if not isinstance(summary, str) or not summary.strip():
        return None
    recs = d.get('recommendations')
    if not isinstance(recs, list):
        return None
    out: list[dict] = []
    for r in recs[:8]:
        if not isinstance(r, dict):
            continue
        title = str(r.get('title') or '').strip()
        detail = str(r.get('detail') or '').strip()
        ev = str(r.get('evidence') or '').strip()
        if not title:
            continue
        try:
            pr = int(r.get('priority', 2))
        except (TypeError, ValueError):
            pr = 2
        pr = max(1, min(3, pr))
        out.append({
            'title': title[:300],
            'detail': detail[:2000],
            'priority': pr,
            'evidence': ev[:500],
        })
    if not out:
        return None
    return {
        'summary': summary.strip()[:2000],
        'recommendations': out,
    }


def _generate_savings_advice_with_llm(context: dict) -> tuple[dict | None, str | None, str | None]:
    client = _get_openai_client()
    if not client:
        return None, 'Savings ideas require OPENAI_API_KEY to be set on the server.', None
    model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini').strip() or 'gpt-4o-mini'
    system = (
        'You are a savings coach. The user message is JSON with:\n'
        '- "insight_focal": full monthly spending insight for the selected month.\n'
        '- "focal_month_transactions": individual lines for the focal month (date, description, amount, direction, category; '
        'flags for internal-transfer legs and whether the line counts in month KPI totals). Use these for concrete, line-level insights. '
        'If focal_transaction_meta.truncated is true, the list is not exhaustive—larger lines are prioritised. '
        '- "prior_month_transaction_samples": optional per-line samples for the most recent prior month(s) (capped), same shape.\n'
        '- "trend_series": up to 12 months before the focal month, each with income/outgoing/net, savings_rate, and top 3 category amounts. Oldest to newest.\n'
        'Rules: Only use numbers and facts that appear in the JSON. Do not invent amounts or payees. '
        'For deeper ideas, connect insight_focal and focal_month_transactions (e.g. specific merchants, categories, or large lines). '
        'Prefer also using trend_series and prior month samples when non-empty. '
        'If history is thin, say so briefly—do not fabricate long trends. '
        'Explain internal_transfer_leg and counts_in_month_totals when relevant. '
        'Phrasing: general information only, not regulated financial, tax, or legal advice. No guaranteed returns.\n'
        'Return only valid JSON with: '
        '{"summary": string (1-2 sentences), "recommendations": ['
        '{"title": string, "detail": string, "priority": integer 1-3, "evidence": string} '
        ']} . priority 1 = highest. evidence should cite the data: category, budget_action_items, line descriptions/dates, or trend_series. '
        'Between 1 and 8 recommendations. British English; use £ where relevant.'
    )
    user_body = json.dumps(context, ensure_ascii=False, indent=2)
    if len(user_body) > SAVINGS_ADVICE_LLM_MAX_CONTEXT_CHARS:
        user_body = user_body[:SAVINGS_ADVICE_LLM_MAX_CONTEXT_CHARS] + '\n... [truncated for length]'
    err_note: str | None = None
    for attempt in range(2):
        try:
            extra = ' Reply with the JSON object only, no other text.' if attempt else ''
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': user_body + extra},
                ],
                response_format={'type': 'json_object'},
                temperature=0.15 if attempt == 0 else 0.0,
            )
            raw = completion.choices[0].message.content or '{}'
            parsed, jerr = _parse_llm_json_object(raw, context='savings_advice')
            if jerr:
                err_note = 'Could not parse model response.'
                continue
            val = _validate_savings_advice_payload(parsed)
            if val:
                return val, None, model
            err_note = 'Model response was incomplete.'
        except Exception as e:
            logger.exception('savings advice LLM call failed: %s', e)
            err_note = str(e) or 'LLM call failed'
    return None, err_note or 'Could not generate savings ideas. Try again.', model


def should_apply_interest(target_day):
    today = date.today()
    if today.day == target_day:
        return True
    last_day_of_month = monthrange(today.year, today.month)[1]
    return today.day == last_day_of_month

def apply_monthly_interest(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return
    
    loan = data['loans'][loan_id]
    target_day = loan['interest_day']
    
    if not should_apply_interest(target_day):
        return  # Skip if not the correct day
    
    current_amount = loan['loan_amount']
    rate = loan['interest_rate']
    
    # Calculate monthly interest
    monthly_interest = (current_amount * (rate / 100)) / 12
    
    new_transaction = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'type': 'interest',
        'amount': monthly_interest,
        'description': f'Monthly interest at {rate}% APR (automated)',
        'user': 'system'
    }
    
    loan['transactions'].append(new_transaction)
    loan['loan_amount'] += monthly_interest
    save_data(data)

def schedule_interest_task(loan_id, day_of_month):
    # Remove existing interest jobs for this loan
    job_id = f'monthly_interest_{loan_id}'
    for job in scheduler.get_jobs():
        if job.id == job_id:
            scheduler.remove_job(job_id)
    
    # Schedule new interest job
    if 1 <= day_of_month <= 31:
        if day_of_month >= 28:
            scheduler.add_job(
                lambda: apply_monthly_interest(loan_id),
                CronTrigger(day="28-31"),
                id=job_id,
                replace_existing=True
            )
        else:
            scheduler.add_job(
                lambda: apply_monthly_interest(loan_id),
                CronTrigger(day=str(day_of_month)),
                id=job_id,
                replace_existing=True
            )
    else:
        raise ValueError(f"Invalid day of month: {day_of_month}")

def calculate_loan_stats(transactions):
    total_loan_quantity = 0
    total_paid = 0
    
    for transaction in transactions:
        if transaction['type'] in ['initial', 'addition']:
            total_loan_quantity += transaction['amount']
        elif transaction['type'] == 'repayment':
            total_paid += abs(transaction['amount'])
    
    return total_loan_quantity, total_paid


def _get_openai_client():
    if OpenAI is None:
        return None
    api_key = os.getenv('OPENAI_API_KEY', '').strip()
    if not api_key:
        return None
    base_url = os.getenv('OPENAI_BASE_URL', '').strip() or None
    kwargs = {'api_key': api_key}
    if base_url:
        kwargs['base_url'] = base_url
    return OpenAI(**kwargs)


def _extract_pdf_text(raw: bytes, *, meta_out: dict | None = None) -> str:
    """
    Bank PDFs are often table-heavy; pypdf alone loses column order.
    Prefer pdfplumber (layout-preserving) when available, then fall back to pypdf.
    Use whichever yields more usable characters.
    If meta_out is a dict, it is filled with keys: engine (str), engines (list of {name, non_ws_chars}).
    """
    def _non_ws_len(t: str) -> int:
        return len(re.sub(r'\s+', '', t or ''))

    candidates: list[tuple[str, str]] = []
    try:
        import pdfplumber  # type: ignore[import-untyped]

        parts = []
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page in pdf.pages:
                t = page.extract_text(layout=True, x_tolerance=2, y_tolerance=2)
                if t and t.strip():
                    parts.append(t)
        joined = '\n\n'.join(parts)
        if _non_ws_len(joined) >= 20:
            candidates.append(('pdfplumber', joined))
    except Exception as e:
        logger.debug('pdfplumber extraction skipped: %s', e)

    try:
        reader = PdfReader(io.BytesIO(raw))
        parts = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
        joined = '\n'.join(parts)
        if _non_ws_len(joined) >= 20:
            candidates.append(('pypdf', joined))
    except Exception as e:
        logger.warning('pypdf extraction failed: %s', e)

    if not candidates:
        raise RuntimeError('Could not extract readable text from this PDF. Try a CSV export from your bank.')
    candidates.sort(key=lambda x: _non_ws_len(x[1]), reverse=True)
    best = candidates[0][1]
    if meta_out is not None:
        meta_out['engine'] = candidates[0][0]
        meta_out['engines'] = [{'name': c[0], 'non_ws_chars': _non_ws_len(c[1])} for c in candidates]
    if len(candidates) > 1:
        logger.info(
            'PDF text: using %s (%s chars non-ws); alt had %s',
            candidates[0][0],
            _non_ws_len(best),
            [(c[0], _non_ws_len(c[1])) for c in candidates[1:]],
        )
    return best


def _group_pdf_words_into_lines(words: list, y_tol: float = 3.0) -> list[list]:
    if not words:
        return []
    sorted_w = sorted(words, key=lambda w: (w.get('top', 0), w.get('x0', 0)))
    lines: list[list] = []
    for w in sorted_w:
        t = w.get('top', 0)
        placed = False
        for line in lines:
            if abs(line[0].get('top', 0) - t) <= y_tol:
                line.append(w)
                placed = True
                break
        if not placed:
            lines.append([w])
    for line in lines:
        line.sort(key=lambda w: w.get('x0', 0))
    lines.sort(key=lambda line: line[0].get('top', 0))
    return lines


def _word_as_money_amount(text: str) -> float | None:
    t = (text or '').strip()
    if not t:
        return None
    t = re.sub(r'^[$£€]\s*', '', t)
    t = t.replace(',', '')
    if not re.match(r'^\d+\.\d{2}$', t):
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return v if v > 0 else None


def _word_center_x(w: dict) -> float:
    return (w.get('x0', 0) + w.get('x1', 0)) / 2


def _nearest_column(x: float, inc_x: float | None, out_x: float | None) -> str | None:
    if inc_x is None or out_x is None:
        return None
    mid = (inc_x + out_x) / 2
    d_in = abs(x - inc_x)
    d_out = abs(x - out_x)
    if d_in < d_out:
        return 'incoming'
    if d_out < d_in:
        return 'outgoing'
    return 'incoming' if x < mid else 'outgoing'


_SPENDING_MONEY_RE = re.compile(r'(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})(?![\d])')


def _spending_direction_hints_from_layout_text(raw: bytes) -> list[dict]:
    """
    Robust fallback for PDFs where pdfplumber.extract_words splits each character
    into its own token (common with custom-spacing fonts in bank exports).

    We read page.extract_text(layout=True) and use character-column positions
    as a proxy for horizontal x coordinates. Any page whose header row exposes
    an incoming AND outgoing money column contributes hints.
    """
    try:
        import pdfplumber  # type: ignore[import-untyped]
    except Exception:
        return []

    in_phrases = ('paid in', 'money in', 'amount in', 'amt in', 'cash in')
    out_phrases = ('paid out', 'money out', 'amount out', 'amt out', 'cash out')
    in_singles = ('credits', 'deposits', 'credit', 'deposit')
    out_singles = ('debits', 'withdrawals', 'debit', 'withdrawal')

    def find_header_cols(line: str) -> tuple[int | None, int | None, int | None]:
        low = line.lower()
        inc_c = out_c = bal_c = None
        for phrase in in_phrases:
            i = low.find(phrase)
            if i != -1:
                inc_c = i + len(phrase) // 2
                break
        for phrase in out_phrases:
            i = low.find(phrase)
            if i != -1:
                out_c = i + len(phrase) // 2
                break
        if inc_c is None:
            for phrase in in_singles:
                i = low.find(phrase)
                if i != -1:
                    inc_c = i + len(phrase) // 2
                    break
        if out_c is None:
            for phrase in out_singles:
                i = low.find(phrase)
                if i != -1:
                    out_c = i + len(phrase) // 2
                    break
        i = low.find('balance')
        if i != -1:
            bal_c = i + len('balance') // 2
        return inc_c, out_c, bal_c

    hints: list[dict] = []
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page in pdf.pages:
                text = page.extract_text(layout=True, x_tolerance=2, y_tolerance=2) or ''
                if not text.strip():
                    continue
                lines = text.splitlines()
                inc_c = out_c = bal_c = None
                header_idx = -1
                for idx, line in enumerate(lines[: min(40, len(lines))]):
                    ic, oc, bc = find_header_cols(line)
                    if ic is not None and oc is not None:
                        inc_c, out_c, bal_c = ic, oc, bc
                        header_idx = idx
                        break
                if inc_c is None or out_c is None or inc_c == out_c:
                    continue

                for line in lines[header_idx + 1:]:
                    if not line.strip():
                        continue
                    low = line.lower()
                    # skip repeated header rows
                    if any(p in low for p in in_phrases) and any(p in low for p in out_phrases):
                        continue

                    matches = list(_SPENDING_MONEY_RE.finditer(line))
                    if not matches:
                        continue
                    clean_line = line.strip()[:200]
                    tokens = _hint_tokens_from_line(clean_line)
                    for m in matches:
                        center = (m.start() + m.end()) / 2
                        if bal_c is not None and abs(center - bal_c) <= 3:
                            continue
                        try:
                            amt = float(m.group(0).replace(',', ''))
                        except ValueError:
                            continue
                        if amt <= 0:
                            continue
                        d_inc = abs(center - inc_c)
                        d_out = abs(center - out_c)
                        if d_inc == d_out:
                            continue
                        direction = 'incoming' if d_inc < d_out else 'outgoing'
                        hints.append({
                            'amount': round(amt, 2),
                            'direction': direction,
                            'tokens': tokens,
                            'line_text': clean_line,
                        })
    except Exception as e:
        logger.debug('layout-text hints failed: %s', e)
        return []

    return hints


def _merge_spending_hints(*groups: list[dict]) -> list[dict]:
    seen: set[tuple[float, str, str]] = set()
    merged: list[dict] = []
    for group in groups:
        for h in group or []:
            key = (
                round(float(h.get('amount', 0)), 2),
                str(h.get('direction') or ''),
                str(h.get('line_text') or ''),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(h)
    return merged


def _format_spending_hints_as_line_hints(hints: list[dict]) -> str:
    """
    Group per-line hints into one LINE_HINT row per source line so the model
    sees both money_in and money_out when a single line has both.
    """
    if not hints:
        return ''
    by_line: dict[str, dict] = {}
    order: list[str] = []
    for h in hints:
        line = str(h.get('line_text') or '').strip()
        if not line:
            continue
        if line not in by_line:
            by_line[line] = {'in': None, 'out': None}
            order.append(line)
        slot = 'in' if h.get('direction') == 'incoming' else 'out'
        prev = by_line[line][slot]
        amt = float(h.get('amount', 0))
        # keep the larger amount if duplicate hints for same column
        if prev is None or amt > prev:
            by_line[line][slot] = amt
    out_lines: list[str] = []
    for line in order[:450]:
        cols = by_line[line]
        in_s = f'{cols["in"]:.2f}' if cols['in'] is not None else '0'
        out_s = f'{cols["out"]:.2f}' if cols['out'] is not None else '0'
        out_lines.append(f'LINE_HINT money_in={in_s} money_out={out_s} | {line}')
    return '\n'.join(out_lines)


def _pdfplumber_spending_column_hints(raw: bytes) -> str:
    """
    Infer Paid in / Paid out column positions from header text and emit LINE_HINT rows
    so the model can set direction from geometry, not merchant names.
    """
    try:
        import pdfplumber  # type: ignore[import-untyped]
    except Exception:
        return ''

    out_lines: list[str] = []
    max_lines = 450

    def scan_headers(lines: list) -> tuple[float | None, float | None, float | None]:
        inc_x = None
        out_x = None
        bal_x = None
        in_prefixes = ('paid', 'money', 'amount', 'amt', 'cash')
        out_prefixes = ('paid', 'money', 'amount', 'amt', 'cash')
        header_scan = lines[: min(45, len(lines))]
        for line in header_scan[:25]:
            words = sorted(line, key=lambda w: w.get('x0', 0))
            lowers = [w.get('text', '').lower().rstrip(':') for w in words]
            for i in range(len(words) - 1):
                a, b = lowers[i], lowers[i + 1]
                if a in in_prefixes and b == 'in':
                    inc_x = (words[i]['x0'] + words[i + 1]['x1']) / 2
                if a in out_prefixes and b == 'out':
                    out_x = (words[i]['x0'] + words[i + 1]['x1']) / 2
            for w in words:
                tl = w.get('text', '').lower().rstrip(':')
                if tl == 'balance':
                    bal_x = _word_center_x(w)
                if inc_x is None and tl in ('credits', 'credit', 'deposits', 'deposit'):
                    inc_x = _word_center_x(w)
                if out_x is None and tl in ('debits', 'debit', 'withdrawals', 'withdrawal'):
                    out_x = _word_center_x(w)
        return inc_x, out_x, bal_x

    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page in pdf.pages:
                words = page.extract_words(
                    extra_attrs=['x0', 'x1', 'top', 'bottom'],
                )
                if not words:
                    continue
                lines = _group_pdf_words_into_lines(words)
                inc_x, out_x, bal_x = scan_headers(lines)
                if inc_x is None or out_x is None:
                    # Fallback: split amount columns by largest horizontal gap on this page
                    xs: list[float] = []
                    for line in lines:
                        for w in line:
                            if _word_as_money_amount(w.get('text', '')) is not None:
                                xs.append(_word_center_x(w))
                    xs.sort()
                    if len(xs) >= 6:
                        gaps = [(xs[i + 1] - xs[i], i) for i in range(len(xs) - 1)]
                        biggest, idx = max(gaps, key=lambda g: g[0])
                        if biggest >= 35:
                            left = xs[: idx + 1]
                            right = xs[idx + 1 :]
                            inc_x = sum(left) / len(left)
                            out_x = sum(right) / len(right)

                if inc_x is None or out_x is None:
                    continue

                for line in lines:
                    if len(out_lines) >= max_lines:
                        break
                    money_words = []
                    for w in line:
                        amt = _word_as_money_amount(w.get('text', ''))
                        if amt is None:
                            continue
                        xc = _word_center_x(w)
                        if bal_x is not None and abs(xc - bal_x) < 12:
                            continue
                        money_words.append((amt, xc))
                    if not money_words:
                        continue
                    line_text = ' '.join(w.get('text', '') for w in line).strip()
                    if len(line_text) < 4:
                        continue
                    inc_cands = [(a, xc) for a, xc in money_words if _nearest_column(xc, inc_x, out_x) == 'incoming']
                    out_cands = [(a, xc) for a, xc in money_words if _nearest_column(xc, inc_x, out_x) == 'outgoing']
                    inc_amt = min(inc_cands, key=lambda t: abs(t[1] - inc_x))[0] if inc_cands else None
                    out_amt = min(out_cands, key=lambda t: abs(t[1] - out_x))[0] if out_cands else None
                    if inc_amt is None and out_amt is None:
                        continue
                    in_s = f'{inc_amt:.2f}' if inc_amt is not None else ''
                    out_s = f'{out_amt:.2f}' if out_amt is not None else ''
                    out_lines.append(f'LINE_HINT money_in={in_s or "0"} money_out={out_s or "0"} | {line_text}')
                if len(out_lines) >= max_lines:
                    break
    except Exception as e:
        logger.debug('pdfplumber column hints failed: %s', e)
        out_lines = []

    layout_hints = _spending_direction_hints_from_layout_text(raw)
    layout_text = _format_spending_hints_as_line_hints(layout_hints)
    if out_lines and layout_text:
        return '\n'.join(out_lines) + '\n' + layout_text
    if layout_text:
        return layout_text
    return '\n'.join(out_lines) if out_lines else ''


def _pipeline_text_preview(text: str, max_chars: int) -> dict:
    """API-safe preview of a long string (full lengths + capped content for the UI)."""
    if not text:
        return {'content': '', 'preview_truncated': False, 'total_length': 0}
    total = len(text)
    if total <= max_chars:
        return {'content': text, 'preview_truncated': False, 'total_length': total}
    return {'content': text[:max_chars], 'preview_truncated': True, 'total_length': total}


def _spending_direction_hints_json_sample(hints: list, limit: int) -> list[dict]:
    out: list[dict] = []
    for h in (hints or [])[:limit]:
        toks = h.get('tokens')
        if isinstance(toks, set):
            toks_list = sorted(toks)[:50]
        elif toks:
            toks_list = list(toks)[:50]
        else:
            toks_list = []
        out.append({
            'amount': h.get('amount'),
            'direction': h.get('direction'),
            'line_text': str(h.get('line_text') or '')[:260],
            'match_tokens_sample': toks_list,
        })
    return out


def _build_spending_pdf_statement_text(raw: bytes) -> tuple[str, dict]:
    """
    PDF → plain text plus optional layout-derived LINE_HINT block (same string the model sees,
    before the global char cap). Second return value has pieces for API transparency.
    """
    pdf_meta: dict = {}
    base = _extract_pdf_text(raw, meta_out=pdf_meta)
    hints_str = _pdfplumber_spending_column_hints(raw)
    if hints_str:
        full = base + SPENDING_PDF_COLUMN_HINTS_BANNER + hints_str
    else:
        full = base
    return full, {
        'extracted_text': base,
        'column_hints_block': hints_str,
        'pdf_text_engine': pdf_meta.get('engine'),
        'pdf_engines_considered': pdf_meta.get('engines') or [],
    }


def _extract_pdf_text_for_spending(raw: bytes) -> str:
    text, _ = _build_spending_pdf_statement_text(raw)
    return text


def _hint_tokens_from_line(line_text: str) -> set[str]:
    toks = set(_normalize_label(line_text).split())
    return {t for t in toks if len(t) >= 3 and not t.isdigit()}


def _build_spending_direction_hints(raw: bytes) -> list[dict]:
    """
    Deterministic per-amount direction hints from PDF geometry.
    Returns a list of {'amount': float, 'direction': 'incoming'|'outgoing',
    'tokens': set[str], 'line_text': str} derived from the same column detection
    used for LINE_HINT text. These are used to override LLM direction after extraction.
    """
    try:
        import pdfplumber  # type: ignore[import-untyped]
    except Exception:
        return []

    hints: list[dict] = []

    def scan_headers(lines: list) -> tuple[float | None, float | None, float | None]:
        inc_x = None
        out_x = None
        bal_x = None
        in_prefixes = ('paid', 'money', 'amount', 'amt', 'cash')
        out_prefixes = ('paid', 'money', 'amount', 'amt', 'cash')
        for line in lines[:25]:
            words = sorted(line, key=lambda w: w.get('x0', 0))
            lowers = [w.get('text', '').lower().rstrip(':') for w in words]
            for i in range(len(words) - 1):
                a, b = lowers[i], lowers[i + 1]
                if a in in_prefixes and b == 'in':
                    inc_x = (words[i]['x0'] + words[i + 1]['x1']) / 2
                if a in out_prefixes and b == 'out':
                    out_x = (words[i]['x0'] + words[i + 1]['x1']) / 2
            for w in words:
                tl = w.get('text', '').lower().rstrip(':')
                if tl == 'balance':
                    bal_x = _word_center_x(w)
                if inc_x is None and tl in ('credits', 'credit', 'deposits', 'deposit'):
                    inc_x = _word_center_x(w)
                if out_x is None and tl in ('debits', 'debit', 'withdrawals', 'withdrawal'):
                    out_x = _word_center_x(w)
        return inc_x, out_x, bal_x

    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page in pdf.pages:
                words = page.extract_words(extra_attrs=['x0', 'x1', 'top', 'bottom'])
                if not words:
                    continue
                lines = _group_pdf_words_into_lines(words)
                inc_x, out_x, bal_x = scan_headers(lines)
                if inc_x is None or out_x is None:
                    xs: list[float] = []
                    for line in lines:
                        for w in line:
                            if _word_as_money_amount(w.get('text', '')) is not None:
                                xs.append(_word_center_x(w))
                    xs.sort()
                    if len(xs) >= 6:
                        gaps = [(xs[i + 1] - xs[i], i) for i in range(len(xs) - 1)]
                        biggest, idx = max(gaps, key=lambda g: g[0])
                        if biggest >= 35:
                            left = xs[: idx + 1]
                            right = xs[idx + 1 :]
                            inc_x = sum(left) / len(left)
                            out_x = sum(right) / len(right)
                if inc_x is None or out_x is None:
                    continue
                for line in lines:
                    money_words = []
                    for w in line:
                        amt = _word_as_money_amount(w.get('text', ''))
                        if amt is None:
                            continue
                        xc = _word_center_x(w)
                        if bal_x is not None and abs(xc - bal_x) < 12:
                            continue
                        money_words.append((amt, xc))
                    if not money_words:
                        continue
                    line_text = ' '.join(w.get('text', '') for w in line).strip()
                    if len(line_text) < 4:
                        continue
                    inc_cands = [(a, xc) for a, xc in money_words if _nearest_column(xc, inc_x, out_x) == 'incoming']
                    out_cands = [(a, xc) for a, xc in money_words if _nearest_column(xc, inc_x, out_x) == 'outgoing']
                    inc_amt = min(inc_cands, key=lambda t: abs(t[1] - inc_x))[0] if inc_cands else None
                    out_amt = min(out_cands, key=lambda t: abs(t[1] - out_x))[0] if out_cands else None
                    tokens = _hint_tokens_from_line(line_text)
                    if inc_amt is not None:
                        hints.append({
                            'amount': round(inc_amt, 2),
                            'direction': 'incoming',
                            'tokens': tokens,
                            'line_text': line_text,
                        })
                    if out_amt is not None:
                        hints.append({
                            'amount': round(out_amt, 2),
                            'direction': 'outgoing',
                            'tokens': tokens,
                            'line_text': line_text,
                        })
    except Exception as e:
        logger.debug('direction hints build failed: %s', e)
        hints = []

    layout_hints = _spending_direction_hints_from_layout_text(raw)
    return _merge_spending_hints(hints, layout_hints)


def _reconcile_spending_directions(rows: list, hints: list) -> dict:
    """
    Override each row's direction when PDF geometry indicates otherwise.
    Matching is by amount (to 2dp) plus token overlap with the hint's line text.
    Returns counters {'overridden', 'confirmed', 'unmatched_rows', 'hints_seen'}.
    """
    stats = {
        'overridden': 0,
        'confirmed': 0,
        'unmatched_rows': 0,
        'hints_seen': len(hints) if hints else 0,
    }
    if not rows or not hints:
        return stats

    by_amount: dict[float, list] = {}
    for h in hints:
        by_amount.setdefault(round(float(h['amount']), 2), []).append(h)

    for row in rows:
        try:
            amt = round(float(row.get('amount', 0)), 2)
        except (TypeError, ValueError):
            stats['unmatched_rows'] += 1
            continue
        cands = by_amount.get(amt) or []
        if not cands:
            for delta in (0.01, -0.01):
                cands = by_amount.get(round(amt + delta, 2)) or []
                if cands:
                    break
        if not cands:
            stats['unmatched_rows'] += 1
            continue

        directions = {h['direction'] for h in cands}
        chosen = None
        if len(directions) == 1:
            chosen = next(iter(directions))
        else:
            row_tokens = _hint_tokens_from_line(row.get('description', ''))
            scored = sorted(
                ((len(row_tokens & h['tokens']) if row_tokens else 0, h) for h in cands),
                key=lambda t: t[0],
                reverse=True,
            )
            top_score, top_hint = scored[0]
            second_score = scored[1][0] if len(scored) > 1 else -1
            if top_score > 0 and top_score > second_score:
                chosen = top_hint['direction']

        if not chosen:
            continue
        if row.get('direction') != chosen:
            row['direction'] = chosen
            stats['overridden'] += 1
        else:
            stats['confirmed'] += 1

    return stats


def _parse_iso_date(s: str):
    if not s or not isinstance(s, str):
        return None
    s = s.strip()[:32]
    try:
        return datetime.strptime(s[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _parse_bank_statement_date(s: str) -> date | None:
    """
    Parse dates from LLM output or messy statement text.
    Prefer ISO; then UK-style DD/MM/YYYY; then dateutil as last resort.
    """
    if not s or not isinstance(s, str):
        return None
    raw = s.strip()[:64]
    d = _parse_iso_date(raw)
    if d is not None:
        return d
    m = re.match(r'^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$', raw)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None
    try:
        return dateutil_parser.parse(raw, dayfirst=True).date()
    except (ValueError, TypeError, OverflowError):
        return None


def _description_similarity(a: str, b: str) -> float:
    a = (a or '').lower().strip()
    b = (b or '').lower().strip()
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _extract_transactions_llm(statement_text: str) -> list:
    """
    Sends statement text to OpenAI; returns a list of dicts with statement_date, amount, description, category.
    Third-party: data leaves your server. Set OPENAI_API_KEY (and optionally OPENAI_MODEL, OPENAI_BASE_URL).
    """
    client = _get_openai_client()
    if not client:
        raise RuntimeError('OPENAI_API_KEY is not set')

    model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini').strip() or 'gpt-4o-mini'
    truncated = False
    text = statement_text
    if len(text) > STATEMENT_MAX_CHARS_FOR_LLM:
        text = text[:STATEMENT_MAX_CHARS_FOR_LLM]
        truncated = True

    system = (
        'You extract ONLY outgoing debits from UK bank statements that are shared household bills: '
        'council tax, water, sewerage, electricity, gas, broadband, internet, landline, TV licence, '
        'TV/broadband bundles, home insurance, service charge, or similar utilities. '
        'EXCLUDE: groceries, supermarkets, restaurants, retail, ATM cash, transfers to people, '
        'credit card payments, investments, salary, benefits, general shopping, petrol unless clearly a utility DD. '
        'Return ONLY valid JSON: {"transactions":[{"statement_date":"YYYY-MM-DD","amount":number,"description":string,"category":string|null}]} '
        'where amount is the positive debit amount as on the statement. If nothing matches, return {"transactions":[]}.'
    )
    user_msg = 'Bank statement text:\n\n' + text
    if truncated:
        user_msg += '\n\n(Note: text was truncated; extract from the visible portion only.)'

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_msg},
        ],
        response_format={'type': 'json_object'},
        temperature=0.2,
    )
    raw = completion.choices[0].message.content or '{}'
    data, jerr = _parse_llm_json_object(raw, context='loan_statement_extract')
    if jerr:
        raise RuntimeError(
            'Could not parse the model response as JSON. Try again or use a shorter statement excerpt. '
            f'Detail: {jerr}'
        )
    txs = data.get('transactions')
    if txs is None:
        txs = data.get('items') or []
    if not isinstance(txs, list):
        return []
    return txs


def _normalize_candidates(raw_list: list) -> list:
    out = []
    idx = 0
    for row in raw_list:
        if not isinstance(row, dict):
            continue
        d = _parse_iso_date(str(row.get('statement_date') or row.get('date') or ''))
        if d is None:
            continue
        try:
            amount_bank = float(row.get('amount'))
        except (TypeError, ValueError):
            continue
        if amount_bank <= 0:
            continue
        desc = str(row.get('description') or '').strip()[:500]
        if not desc:
            desc = 'Imported repayment'
        cat = row.get('category')
        if cat is not None:
            cat = str(cat)[:120]
        amount_default = round(amount_bank / 2.0, 2)
        out.append({
            'id': idx,
            'statement_date': d.strftime('%Y-%m-%d'),
            'amount_bank': round(amount_bank, 2),
            'amount_default': amount_default,
            'description': desc,
            'category': cat,
        })
        idx += 1
    return out


def _mark_duplicates(candidates: list, loan: dict) -> None:
    """Set possible_duplicate when a candidate matches any existing loan transaction on date and amount."""
    txs = loan.get('transactions', []) or []
    for c in candidates:
        c['possible_duplicate'] = False
        try:
            bank = float(c.get('amount_bank', 0))
            share = float(c.get('amount_default', 0))
        except (TypeError, ValueError):
            continue
        for tx in txs:
            if tx.get('date') != c['statement_date']:
                continue
            try:
                stored = abs(float(tx['amount']))
            except (TypeError, ValueError):
                continue
            if (
                abs(stored - bank) <= STATEMENT_AUDIT_MATCH_TOLERANCE
                or abs(stored - share) <= STATEMENT_AUDIT_MATCH_TOLERANCE
            ):
                c['possible_duplicate'] = True
                break


def _spending_pipeline_dict_pdf(pdf_parts: dict, hints: list, raw_len: int, combined_text: str) -> dict:
    ext = pdf_parts['extracted_text']
    col = pdf_parts['column_hints_block'] or ''
    return {
        'source_format': 'pdf',
        'lengths': {
            'raw_bytes': raw_len,
            'extracted_text': len(ext),
            'column_hints_block': len(col),
            'combined_before_truncation': len(combined_text),
        },
        'pdf': {
            'text_engine': pdf_parts['pdf_text_engine'],
            'engines_considered': pdf_parts['pdf_engines_considered'],
        },
        'previews': {
            'extracted_text': _pipeline_text_preview(ext, SPENDING_PIPELINE_EXTRACT_PREVIEW),
            'column_hints_block': _pipeline_text_preview(col, SPENDING_PIPELINE_HINTS_PREVIEW),
        },
        'direction_hints': {
            'count': len(hints),
            'sample': _spending_direction_hints_json_sample(hints, SPENDING_PIPELINE_DIRECTION_HINT_ROWS),
        },
    }


def _spending_pipeline_dict_text(name: str, text: str, raw_len: int) -> dict:
    src_fmt = 'csv' if name.endswith('.csv') else 'text'
    return {
        'source_format': src_fmt,
        'lengths': {
            'raw_bytes': raw_len,
            'extracted_text': len(text),
            'column_hints_block': 0,
            'combined_before_truncation': len(text),
        },
        'previews': {
            'extracted_text': _pipeline_text_preview(text, SPENDING_PIPELINE_EXTRACT_PREVIEW),
            'column_hints_block': _pipeline_text_preview('', SPENDING_PIPELINE_HINTS_PREVIEW),
        },
        'direction_hints': {'count': 0, 'sample': []},
        'note': 'Decoded as UTF-8 text; no PDF layout or column-hint pass.',
    }


def _spending_text_hints_pipeline_from_raw(name: str, raw: bytes) -> tuple[str, list, dict]:
    """Full spending parse: text for the model, direction hints, pipeline dict (before truncation)."""
    if name.endswith('.pdf'):
        text, pdf_parts = _build_spending_pdf_statement_text(raw)
        hints = _build_spending_direction_hints(raw)
        pipeline = _spending_pipeline_dict_pdf(pdf_parts, hints, len(raw), text)
        return text, hints, pipeline
    text = raw.decode('utf-8', errors='replace')
    return text, [], _spending_pipeline_dict_text(name, text, len(raw))


def _finalize_spending_upload(text: str, hints: list, pipeline: dict | None) -> tuple[tuple | None, str | None]:
    if not text or len(re.sub(r'\s+', '', text)) < 40:
        return None, 'Extracted text is empty or too short. Try exporting CSV from your bank or another PDF.'
    truncated = False
    if len(text) > STATEMENT_MAX_CHARS_FOR_LLM:
        text = text[:STATEMENT_MAX_CHARS_FOR_LLM]
        truncated = True
    if pipeline is not None:
        pipeline['truncated_for_llm'] = truncated
        pipeline['llm_char_limit'] = STATEMENT_MAX_CHARS_FOR_LLM
        pipeline['lengths']['sent_to_llm'] = len(text)
    return (text, truncated, hints, pipeline), None


def _iter_prepare_spending_raw(name: str, raw: bytes):
    """Yield progress dicts; then yield prep_complete with prep tuple, or error dict."""
    hints: list = []
    pipeline: dict | None = None
    text = ''
    try:
        if name.endswith('.pdf'):
            yield {'type': 'progress', 'step': 'pdf_text', 'message': 'Extracting PDF text (layout-aware)…'}
            pdf_meta: dict = {}
            base = _extract_pdf_text(raw, meta_out=pdf_meta)
            yield {'type': 'progress', 'step': 'pdf_column_hints', 'message': 'Deriving column hints from statement layout…'}
            hints_str = _pdfplumber_spending_column_hints(raw)
            if hints_str:
                text = base + SPENDING_PDF_COLUMN_HINTS_BANNER + hints_str
            else:
                text = base
            pdf_parts = {
                'extracted_text': base,
                'column_hints_block': hints_str,
                'pdf_text_engine': pdf_meta.get('engine'),
                'pdf_engines_considered': pdf_meta.get('engines') or [],
            }
            yield {'type': 'progress', 'step': 'pdf_direction_hints', 'message': 'Deriving credit/debit hints from geometry…'}
            hints = _build_spending_direction_hints(raw)
            pipeline = _spending_pipeline_dict_pdf(pdf_parts, hints, len(raw), text)
        else:
            yield {'type': 'progress', 'step': 'decode_text', 'message': 'Decoding text or CSV…'}
            text = raw.decode('utf-8', errors='replace')
            pipeline = _spending_pipeline_dict_text(name, text, len(raw))
    except Exception as e:
        logger.exception('statement read failed')
        yield {'type': 'error', 'message': f'Could not read file: {e}', 'http_status': 400}
        return

    yield {'type': 'progress', 'step': 'validate', 'message': 'Validating text and applying model size limit…'}
    fin, err = _finalize_spending_upload(text, hints, pipeline)
    if err:
        yield {'type': 'error', 'message': err, 'http_status': 400}
        return
    yield {'type': 'prep_complete', 'prep': fin}


def _spending_statement_preview_finalize(
    data: dict,
    spending: dict,
    period: dict,
    direction_hints: list,
    pipeline: dict,
    truncated_text: bool,
    raw_rows: list,
    extraction_meta: dict | None = None,
) -> dict:
    rows = _normalize_spending_transactions(raw_rows)
    before_ct = len(rows)
    rows, dropped_outside = _filter_spending_rows_by_period(
        rows, period['period_start_date'], period['period_end_date']
    )
    direction_stats = _reconcile_spending_directions(rows, direction_hints)
    cache_changed = _apply_outgoing_classification(rows, spending)
    if cache_changed:
        save_data(data)

    rm = period.get('report_month') or ''
    for r in rows:
        r['report_month'] = rm
    tr_preview = simulate_spending_transfer_reconciliation_preview(spending, rm, rows)
    for r in rows:
        rid = str(r.get('id') or '')
        r['reconciliation'] = (tr_preview.get('pairing') or {}).get(rid) or {
            'paired': False,
            'peer_id': None,
            'peer_is_from_ledger': False,
            'peer_description': None,
        }

    d_led, d_up, dup_ledger_fps = _apply_spending_preview_duplicate_marks(rm, rows, spending)

    incoming_total = round(sum(r['amount'] for r in rows if r.get('direction') == 'incoming'), 2)
    outgoing_total = round(sum(r['amount'] for r in rows if r.get('direction') == 'outgoing'), 2)
    months = sorted({r.get('month') for r in rows if r.get('month')}, reverse=True)
    summary = {
        'report_month': period['report_month'],
        'period_start': period['period_start'],
        'period_end': period['period_end'],
        'total_rows': len(rows),
        'raw_extraction_count': before_ct,
        'filtered_out_count': dropped_outside,
        'incoming_total': incoming_total,
        'outgoing_total': outgoing_total,
        'net': round(incoming_total - outgoing_total, 2),
        'months': months,
        'direction_reconciliation': direction_stats,
        'transfer_reconciliation_preview': {
            'reconciliation': tr_preview.get('reconciliation'),
            'ledger_row_count_in_month': tr_preview.get('ledger_row_count_in_month', 0),
            'auto_applied_pairs_in_simulation': tr_preview.get('auto_applied_pairs_in_simulation', 0),
        },
        'preview_duplicate_ledger': d_led,
        'preview_duplicate_upload': d_up,
        'duplicate_ledger_fingerprints': dup_ledger_fps,
    }
    if extraction_meta:
        summary['extraction'] = extraction_meta
    return {
        'transactions': rows,
        'truncated': truncated_text,
        'pipeline': pipeline,
        'summary': summary,
    }


def _spending_statement_preview_payload(
    text: str,
    truncated_text: bool,
    direction_hints: list,
    pipeline: dict,
    period: dict,
) -> dict:
    """Build the same JSON body as spending_statement_preview after file prep."""
    data = load_data()
    spending, changed = _ensure_user_spending(data, session['username'])
    if changed:
        save_data(data)

    period_hint = (
        f'Prefer transactions that fall on or between {period["period_start"]} and {period["period_end"]} inclusive. '
        f'When in doubt, include the row and use its statement date for the "date" field.'
    )
    raw_rows = None
    extraction_meta = None
    for ev in iter_spending_transaction_extraction(text, period_hint):
        if ev.get('type') == 'result':
            raw_rows = ev.get('rows')
            extraction_meta = ev.get('meta')
    if raw_rows is None:
        raw_rows = []
    return _spending_statement_preview_finalize(
        data,
        spending,
        period,
        direction_hints,
        pipeline,
        truncated_text,
        raw_rows,
        extraction_meta,
    )


def _prepare_statement_text_from_upload(*, for_spending: bool = False):
    """
    Read multipart file from request.
    Returns ((text, truncated_bool, hints_list, pipeline_dict), None) when for_spending=True,
    else ((text, truncated_bool), None). On error returns (None, message).
    hints_list is direction hints from PDF geometry (empty for non-PDF).
    pipeline_dict describes intermediate representations for the spending UI (PDF stages, previews).
    """
    f = request.files.get('file')
    if not f or not getattr(f, 'filename', None):
        return None, 'No file uploaded'
    raw = f.read()
    if len(raw) > STATEMENT_MAX_BYTES:
        return None, 'File too large (max 2 MB).'
    name = (f.filename or '').lower()
    hints: list = []
    pipeline: dict | None = None
    try:
        if for_spending:
            text, hints, pipeline = _spending_text_hints_pipeline_from_raw(name, raw)
        elif name.endswith('.pdf'):
            text = _extract_pdf_text(raw)
        else:
            text = raw.decode('utf-8', errors='replace')
    except Exception as e:
        logger.exception('statement read failed')
        return None, f'Could not read file: {e}'
    if for_spending:
        fin, ferr = _finalize_spending_upload(text, hints, pipeline)
        if ferr:
            return None, ferr
        return fin, None
    if not text or len(re.sub(r'\s+', '', text)) < 40:
        return None, 'Extracted text is empty or too short. Try exporting CSV from your bank or another PDF.'
    truncated = False
    if len(text) > STATEMENT_MAX_CHARS_FOR_LLM:
        text = text[:STATEMENT_MAX_CHARS_FOR_LLM]
        truncated = True
    return (text, truncated), None


def _extract_baseline_statement_llm(statement_text: str) -> tuple[list, str | None]:
    """
    Broader extraction for expected recurring bills + statement period_end.
    Returns (transactions_list, period_end_iso_or_none).
    """
    client = _get_openai_client()
    if not client:
        raise RuntimeError('OPENAI_API_KEY is not set')

    model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini').strip() or 'gpt-4o-mini'
    truncated = False
    text = statement_text
    if len(text) > STATEMENT_MAX_CHARS_FOR_LLM:
        text = text[:STATEMENT_MAX_CHARS_FOR_LLM]
        truncated = True

    system = (
        'You analyse UK bank statement text. Return ONLY valid JSON with keys: '
        '"transactions" (array) and "period_end" (string or null). '
        '"transactions": outgoing debits that could plausibly be recurring monthly household bills '
        '(council tax, water, electricity, gas, broadband, rent DD, insurance, phone, TV licence, service charges, etc.). '
        'Be more inclusive than a strict filter but EXCLUDE: supermarkets, restaurants, pubs, coffee, '
        'ATM cash, transfers to people, salary, benefits, gambling, general shopping unless clearly a utility DD. '
        'Each item: statement_date (YYYY-MM-DD), amount (positive debit), description (string), category (string or null). '
        '"period_end": the statement period end date as YYYY-MM-DD if visible in the text, else null.'
    )
    user_msg = 'Bank statement text:\n\n' + text
    if truncated:
        user_msg += '\n\n(Note: text was truncated.)'

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_msg},
        ],
        response_format={'type': 'json_object'},
        temperature=0.2,
    )
    raw = completion.choices[0].message.content or '{}'
    data, jerr = _parse_llm_json_object(raw, context='baseline_statement')
    if jerr:
        logger.warning('baseline_statement LLM JSON parse failed: %s', jerr)
        return [], None
    txs = data.get('transactions')
    if txs is None:
        txs = data.get('items') or []
    if not isinstance(txs, list):
        txs = []
    pe = data.get('period_end')
    if pe is not None and not isinstance(pe, str):
        pe = str(pe) if pe else None
    if pe:
        d = _parse_iso_date(pe)
        pe = d.strftime('%Y-%m-%d') if d else None
    return txs, pe


def _default_reminder_day_from_period_end(period_end: str | None) -> int:
    d = _parse_iso_date(period_end or '')
    if d is None:
        return 1
    nxt = d + timedelta(days=1)
    return nxt.day


def _amounts_close_for_compare(a: float, b: float) -> bool:
    if abs(a - b) <= 0.05:
        return True
    m = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / m <= 0.02


def _compare_to_baseline(candidates: list, baseline: list | None) -> dict | None:
    if not baseline:
        return None
    missing = []
    amount_changed = []
    used_c = set()

    for b in baseline:
        best_j = None
        best_sim = -1.0
        bd = str(b.get('description', ''))
        for j, c in enumerate(candidates):
            if j in used_c:
                continue
            sim = _description_similarity(bd, c.get('description', ''))
            if sim > best_sim:
                best_sim = sim
                best_j = j
        if best_j is None or best_sim < BASELINE_SIM_THRESHOLD:
            missing.append(b)
            continue
        used_c.add(best_j)
        c = candidates[best_j]
        try:
            ab = float(b.get('amount_bank', 0))
            cb = float(c.get('amount_bank', 0))
        except (TypeError, ValueError):
            missing.append(b)
            used_c.discard(best_j)
            continue
        if not _amounts_close_for_compare(ab, cb):
            amount_changed.append({
                'baseline': b,
                'found': c,
                'expected_amount_bank': round(ab, 2),
                'actual_amount_bank': round(cb, 2),
            })

    new = [candidates[j] for j in range(len(candidates)) if j not in used_c]
    return {'missing': missing, 'new': new, 'amount_changed': amount_changed}


def _get_smtp_config():
    host = os.getenv('SMTP_HOST', '').strip()
    if not host:
        return None
    return {
        'host': host,
        'port': int(os.getenv('SMTP_PORT', '587')),
        'user': os.getenv('SMTP_USER', '').strip(),
        'password': os.getenv('SMTP_PASSWORD', ''),
        'use_tls': _env_bool('SMTP_USE_TLS', True),
        'mail_from': os.getenv('MAIL_FROM', '').strip(),
    }


def _recipient_for_loan(loan: dict) -> str:
    return (loan.get('bill_reminder_email') or '').strip() or os.getenv(
        'FINANCE_TRACKER_NOTIFY_EMAIL', ''
    ).strip()


def _public_app_base_url():
    base = os.getenv('PUBLIC_BASE_URL', '').strip().rstrip('/')
    if base:
        return base
    return request.url_root.rstrip('/') if request else ''


def _send_bill_reminder_email(loan_id: str, loan: dict) -> bool:
    cfg = _get_smtp_config()
    to_addr = _recipient_for_loan(loan)
    if not cfg or not cfg['mail_from'] or not to_addr:
        logger.warning('bill reminder skipped: SMTP or recipient not configured')
        return False
    name = loan.get('name', 'Loan')
    base = os.getenv('PUBLIC_BASE_URL', '').strip().rstrip('/')
    link = f'{base}/loan/{loan_id}' if base else f'/loan/{loan_id}'
    msg = EmailMessage()
    msg['Subject'] = f'Finance tracker: upload statement for "{name}"'
    msg['From'] = cfg['mail_from']
    msg['To'] = to_addr
    msg.set_content(
        f"""Time to upload your latest bank statement for "{name}" and import shared bill repayments.

Open the loan: {link}

If you use expected bills, compare the import preview to your baseline for any changes.
"""
    )
    try:
        with smtplib.SMTP(cfg['host'], cfg['port'], timeout=30) as smtp:
            if cfg['use_tls']:
                smtp.starttls()
            if cfg['user']:
                smtp.login(cfg['user'], cfg['password'])
            smtp.send_message(msg)
        return True
    except Exception:
        logger.exception('SMTP send failed for loan %s', loan_id)
        return False


def tick_bill_reminders():
    """Scheduled daily: send at most one reminder per loan per calendar month on the configured day."""
    data = load_data()
    today = date.today()
    this_month = today.strftime('%Y-%m')
    changed = False
    for loan_id, loan in data['loans'].items():
        if loan.get('deleted'):
            continue
        if not loan.get('bill_baseline'):
            continue
        br = loan.get('bill_reminder') or {}
        day = int(br.get('day_of_month', 1))
        if not 1 <= day <= 31:
            day = 1
        last_day = monthrange(today.year, today.month)[1]
        effective = min(day, last_day)
        if today.day != effective:
            continue
        if br.get('last_sent_month') == this_month:
            continue
        if not _send_bill_reminder_email(loan_id, loan):
            continue
        br['last_sent_month'] = this_month
        loan['bill_reminder'] = br
        changed = True
    if changed:
        save_data(data)


def schedule_bill_reminders_job():
    job_id = 'bill_reminders_global'
    for job in scheduler.get_jobs():
        if job.id == job_id:
            scheduler.remove_job(job_id)
    scheduler.add_job(
        tick_bill_reminders,
        CronTrigger(hour=BILL_REMINDER_HOUR, minute=0),
        id=job_id,
        replace_existing=True,
    )


def _login_form_credentials():
    """Normal form POST; also accept JSON for non-browser clients."""
    username = (request.form.get('username') or '').strip()
    password = (request.form.get('password') or '').strip()
    if username or password:
        return username, password
    if request.is_json:
        data = request.get_json(silent=True) or {}
        return (
            str(data.get('username', '') or '').strip(),
            str(data.get('password', '') or '').strip(),
        )
    return username, password


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username, password = _login_form_credentials()
        if username in USERS and USERS[username] == password:
            session.permanent = True
            session['username'] = username
            return redirect(url_for('index'))
        logger.warning(
            'login failed: user_in_map=%s form_keys=%s content_type=%s',
            username in USERS,
            list(request.form.keys()),
            request.content_type,
        )
        return render_template('login.html', error='Invalid username or password')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    data = load_data()
    loans_summary = []
    for loan_id, loan in data['loans'].items():
        total_loan_quantity, total_paid = calculate_loan_stats(loan['transactions'])
        loans_summary.append({
            'id': loan_id,
            'name': loan['name'],
            'current_balance': loan['loan_amount'],
            'total_loan_quantity': total_loan_quantity,
            'total_paid': total_paid,
            'deleted': loan.get('deleted', False)
        })
    return render_template('loans.html', loans=loans_summary, username=session.get('username'))


@app.route('/spending')
@login_required
def spending_tab():
    data = load_data()
    spending, changed = _ensure_user_spending(data, session['username'])
    if changed:
        save_data(data)
    months = sorted((spending.get('monthly_insights') or {}).keys(), reverse=True)
    return render_template(
        'spending.html',
        username=session.get('username'),
        months=months,
        spending_categories=SPENDING_ALLOWED_CATEGORIES,
    )

@app.route('/loan/<loan_id>')
@login_required
def loan_details(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return redirect(url_for('index'))
    
    loan = data['loans'][loan_id]
    total_loan_quantity, total_paid = calculate_loan_stats(loan['transactions'])
    loan_deleted = loan.get('deleted', False)
    recurring_payments = loan.get('recurring_payments', [])
    bill_baseline = loan.get('bill_baseline') or []
    bill_reminder = loan.get('bill_reminder')
    bill_reminder_email = loan.get('bill_reminder_email') or ''
    smtp_configured = _get_smtp_config() is not None
    default_notify_email = os.getenv('FINANCE_TRACKER_NOTIFY_EMAIL', '').strip()

    return render_template('index.html',
                         loan_id=loan_id,
                         username=session.get('username'),
                         loan_name=loan['name'],
                         loan_amount=loan['loan_amount'],
                         interest_rate=loan['interest_rate'],
                         interest_day=loan['interest_day'],
                         transactions=loan['transactions'],
                         total_loan_quantity=total_loan_quantity,
                         total_paid=total_paid,
                         loan_deleted=loan_deleted,
                         recurring_payments=recurring_payments,
                         bill_baseline=bill_baseline,
                         bill_reminder=bill_reminder,
                         bill_reminder_email=bill_reminder_email,
                         smtp_configured=smtp_configured,
                         default_notify_email=default_notify_email)

@app.route('/api/loan', methods=['POST'])
@login_required
def create_loan():
    data = load_data()
    loan_data = request.get_json()
    
    # Generate a unique ID for the new loan
    loan_id = f"loan_{len(data['loans']) + 1}"
    
    new_loan = {
        'name': loan_data['name'],
        'loan_amount': float(loan_data['loan_amount']),
        'interest_rate': float(loan_data['interest_rate']),
        'interest_day': 1,  # Default to 1st of month
        'transactions': [{
            'date': datetime.now().strftime('%Y-%m-%d'),
            'type': 'initial',
            'amount': float(loan_data['loan_amount']),
            'description': 'Initial loan amount',
            'user': session['username']
        }]
    }
    
    data['loans'][loan_id] = new_loan
    save_data(data)
    schedule_interest_task(loan_id, new_loan['interest_day'])
    
    return jsonify({'id': loan_id, **new_loan})

@app.route('/api/loan/<loan_id>/update_interest_day', methods=['POST'])
@login_required
def update_interest_day(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    
    loan = data['loans'][loan_id]
    new_day = int(request.get_json()['interest_day'])
    old_day = loan['interest_day']
    
    if not 1 <= new_day <= 31:
        return jsonify({'error': 'Day must be between 1 and 31'}), 400
    
    if new_day != old_day:
        # Add the change to transaction history
        new_transaction = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'type': 'settings',
            'amount': 0,
            'description': f'Interest day changed from {old_day} to {new_day}',
            'user': session['username']
        }
        loan['transactions'].append(new_transaction)
        
        # Update the interest day
        loan['interest_day'] = new_day
        save_data(data)
        schedule_interest_task(loan_id, new_day)
    
    return jsonify(loan)

@app.route('/api/loan/<loan_id>/update_interest_rate', methods=['POST'])
@login_required
def update_interest_rate(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    
    loan = data['loans'][loan_id]
    new_rate = float(request.get_json()['interest_rate'])
    old_rate = loan['interest_rate']
    
    if new_rate < 0 or new_rate > 100:
        return jsonify({'error': 'Interest rate must be between 0 and 100'}), 400
    
    if new_rate != old_rate:
        # Add the change to transaction history
        new_transaction = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'type': 'settings',
            'amount': 0,
            'description': f'Interest rate changed from {old_rate:.2f}% to {new_rate:.2f}%',
            'user': session['username']
        }
        loan['transactions'].append(new_transaction)
        
        # Update the interest rate
        loan['interest_rate'] = new_rate
        save_data(data)
    
    return jsonify(loan)

@app.route('/api/loan/<loan_id>/transaction', methods=['POST'])
@login_required
def add_transaction(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    
    loan = data['loans'][loan_id]
    transaction = request.get_json()
    
    amount = float(transaction['amount'])
    transaction_type = transaction['type']
    
    if transaction_type == 'repayment':
        amount = -amount  # Repayments reduce the loan amount
    
    new_transaction = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'type': transaction_type,
        'amount': amount,
        'description': transaction['description'],
        'user': session['username']
    }
    
    loan['transactions'].append(new_transaction)
    loan['loan_amount'] += amount
    
    save_data(data)
    return jsonify(loan)

@app.route('/api/loan/<loan_id>/apply_interest', methods=['POST'])
def apply_interest(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    
    loan = data['loans'][loan_id]
    current_amount = loan['loan_amount']
    rate = loan['interest_rate']
    
    # Calculate monthly interest
    monthly_interest = (current_amount * (rate / 100)) / 12
    
    new_transaction = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'type': 'interest',
        'amount': monthly_interest,
        'description': f'Monthly interest at {rate}% APR',
        'user': session['username']
    }
    
    loan['transactions'].append(new_transaction)
    loan['loan_amount'] += monthly_interest
    
    save_data(data)
    return jsonify(loan)

@app.route('/api/loan/<loan_id>/update_name', methods=['POST'])
@login_required
def update_loan_name(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    
    loan = data['loans'][loan_id]
    new_name = request.get_json()['name']
    old_name = loan['name']
    
    if not new_name or new_name.strip() == '':
        return jsonify({'error': 'Loan name cannot be empty'}), 400
    
    if new_name != old_name:
        # Add the change to transaction history
        new_transaction = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'type': 'settings',
            'amount': 0,
            'description': f'Loan name changed from "{old_name}" to "{new_name}"',
            'user': session['username']
        }
        loan['transactions'].append(new_transaction)
        
        # Update the loan name
        loan['name'] = new_name
        save_data(data)
    
    return jsonify(loan)

def update_loan_status(loan_id, deleted_status, action_description):
    """Helper function to update loan status and add transaction history entry."""
    data = load_data()
    if loan_id not in data['loans']:
        return None, 'Loan not found', 404
    
    # Update loan status
    data['loans'][loan_id]['deleted'] = deleted_status
    
    # Add status change event to transaction history
    new_transaction = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'type': 'settings',
        'amount': 0,
        'description': action_description,
        'user': session['username']
    }
    data['loans'][loan_id]['transactions'].append(new_transaction)
    
    save_data(data)
    return data['loans'][loan_id], f'Loan {action_description.lower()}', 200

@app.route('/api/loan/<loan_id>/<action>', methods=['POST'])
def update_loan_state(loan_id, action):
    try:
        if action not in ['delete', 'recover']:
            return jsonify({'error': 'Invalid action'}), 400
            
        loan_data, message, status_code = update_loan_status(
            loan_id, 
            action == 'delete',  # True for delete, False for recover
            f'Loan {"marked as deleted" if action == "delete" else "recovered from deleted state"}'
        )
        
        if status_code != 200:
            return jsonify({'error': message}), status_code
            
        return jsonify({'message': message})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def process_recurring_payments(loan_id):
    """Process recurring payments that are due today."""
    data = load_data()
    if loan_id not in data['loans']:
        return
        
    loan = data['loans'][loan_id]
    if 'recurring_payments' not in loan:
        return
        
    today = date.today()
    for payment in loan['recurring_payments']:
        next_payment_date = datetime.strptime(payment['next_payment_date'], '%Y-%m-%d').date()
        
        if today >= next_payment_date:
            # Add the payment as a transaction
            new_transaction = {
                'date': today.strftime('%Y-%m-%d'),
                'type': 'repayment',
                'amount': -float(payment['amount']),  # Negative for repayments
                'description': f'Recurring payment ({payment["schedule"]})',
                'user': 'system'  # Automated payment
            }
            loan['transactions'].append(new_transaction)
            loan['loan_amount'] += new_transaction['amount']  # Add negative amount to reduce balance
            
            # Calculate next payment date
            if payment['schedule'] == 'monthly':
                next_payment_date = next_payment_date + relativedelta(months=1)
            elif payment['schedule'] == 'bi-weekly':
                next_payment_date = next_payment_date + relativedelta(weeks=2)
            elif payment['schedule'] == 'weekly':
                next_payment_date = next_payment_date + relativedelta(weeks=1)
                
            # Update next payment date
            payment['next_payment_date'] = next_payment_date.strftime('%Y-%m-%d')
    
    save_data(data)

def schedule_recurring_payments(loan_id):
    """Schedule recurring payment processing for this loan."""
    # Remove existing recurring payment jobs for this loan
    job_id = f'recurring_payments_{loan_id}'
    for job in scheduler.get_jobs():
        if job.id == job_id:
            scheduler.remove_job(job_id)
    
    # Schedule new recurring payment job to run daily
    scheduler.add_job(
        lambda: process_recurring_payments(loan_id),
        CronTrigger(hour=0, minute=0),  # Run at midnight
        id=job_id,
        replace_existing=True
    )

@app.route('/api/loan/<loan_id>/recurring_payment', methods=['POST'])
def add_recurring_payment(loan_id):
    try:
        data = load_data()
        if loan_id not in data['loans']:
            return jsonify({'error': 'Loan not found'}), 404
            
        payment_data = request.get_json()
        amount = float(payment_data['amount'])
        schedule = payment_data['schedule']
        start_date = datetime.strptime(payment_data['start_date'], '%Y-%m-%d').date()
        today = date.today()
        
        # Calculate next payment date based on schedule
        next_payment_date = start_date
        if schedule == 'monthly':
            next_payment_date = start_date + relativedelta(months=1)
        elif schedule == 'bi-weekly':
            next_payment_date = start_date + relativedelta(weeks=2)
        elif schedule == 'weekly':
            next_payment_date = start_date + relativedelta(weeks=1)
        else:
            return jsonify({'error': 'Invalid schedule'}), 400
            
        # Create new recurring payment
        new_payment = {
            'amount': amount,
            'schedule': schedule,
            'start_date': start_date.strftime('%Y-%m-%d'),
            'next_payment_date': next_payment_date.strftime('%Y-%m-%d')
        }
        
        # Initialize recurring_payments list if it doesn't exist
        if 'recurring_payments' not in data['loans'][loan_id]:
            data['loans'][loan_id]['recurring_payments'] = []
            
        data['loans'][loan_id]['recurring_payments'].append(new_payment)
        
        # If start date is today, process the payment immediately
        if start_date == today:
            # Add the payment as a transaction
            new_transaction = {
                'date': today.strftime('%Y-%m-%d'),
                'type': 'repayment',
                'amount': -amount,  # Negative for repayments
                'description': f'Recurring payment ({schedule})',
                'user': 'system'
            }
            data['loans'][loan_id]['transactions'].append(new_transaction)
            data['loans'][loan_id]['loan_amount'] += new_transaction['amount']  # Add negative amount to reduce balance
        
        save_data(data)
        
        # Schedule recurring payment processing
        schedule_recurring_payments(loan_id)
        
        return jsonify({'message': 'Recurring payment added successfully', 'payment': new_payment})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/loan/<loan_id>/recurring_payment/<int:payment_index>', methods=['DELETE'])
def delete_recurring_payment(loan_id, payment_index):
    try:
        data = load_data()
        if loan_id not in data['loans']:
            return jsonify({'error': 'Loan not found'}), 404
            
        if 'recurring_payments' not in data['loans'][loan_id]:
            return jsonify({'error': 'No recurring payments found'}), 404
            
        if payment_index < 0 or payment_index >= len(data['loans'][loan_id]['recurring_payments']):
            return jsonify({'error': 'Invalid payment index'}), 400
            
        # Remove the payment
        deleted_payment = data['loans'][loan_id]['recurring_payments'].pop(payment_index)
        
        # Add a transaction to record the deletion
        new_transaction = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'type': 'settings',
            'amount': 0,
            'description': f'Recurring payment deleted (£{deleted_payment["amount"]:.2f} {deleted_payment["schedule"]})',
            'user': session['username']
        }
        data['loans'][loan_id]['transactions'].append(new_transaction)
        
        save_data(data)
        
        return jsonify({'message': 'Recurring payment deleted successfully', 'payment': deleted_payment})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/loan/<loan_id>/statement/preview', methods=['POST'])
@login_required
def statement_preview(loan_id):
    """
    Upload a bank statement (PDF, CSV, or plain text). Text is sent to OpenAI for extraction.
    Env: OPENAI_API_KEY (required), OPENAI_MODEL (default gpt-4o-mini), OPENAI_BASE_URL (optional).
    """
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    loan = data['loans'][loan_id]
    if loan.get('deleted'):
        return jsonify({'error': 'Loan is deleted'}), 400

    if not _get_openai_client():
        return jsonify({'error': 'Statement import is not configured (set OPENAI_API_KEY).'}), 503

    prep, err = _prepare_statement_text_from_upload()
    if err:
        return jsonify({'error': err}), 400
    text, truncated_text = prep

    try:
        raw_txs = _extract_transactions_llm(text)
    except Exception as e:
        logger.exception('LLM extraction failed')
        return jsonify({'error': str(e)}), 500

    candidates = _normalize_candidates(raw_txs)
    _mark_duplicates(candidates, loan)
    out = {'candidates': candidates, 'truncated': truncated_text}
    bl = loan.get('bill_baseline')
    if bl:
        diff = _compare_to_baseline(candidates, bl)
        if diff is not None:
            out['baseline_diff'] = diff
    return jsonify(out)


@app.route('/api/loan/<loan_id>/statement/import', methods=['POST'])
@login_required
def statement_import(loan_id):
    """Bulk-append repayments with historical dates. Body: {\"transactions\":[{\"date\",\"amount\",\"description\"}]} — amount is positive (your share)."""
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    loan = data['loans'][loan_id]
    if loan.get('deleted'):
        return jsonify({'error': 'Loan is deleted'}), 400

    payload = request.get_json(silent=True) or {}
    rows = payload.get('transactions')
    if not isinstance(rows, list) or not rows:
        return jsonify({'error': 'transactions[] is required and must be non-empty'}), 400

    username = session['username']
    earliest = date(1990, 1, 1)
    latest = date.today() + timedelta(days=1)

    validated = []
    for r in rows:
        if not isinstance(r, dict):
            return jsonify({'error': 'Invalid transaction entry'}), 400
        d = _parse_iso_date(str(r.get('date') or ''))
        if d is None:
            return jsonify({'error': f'Invalid date: {r.get("date")!r}'}), 400
        if d < earliest or d > latest:
            return jsonify({'error': f'Date out of range: {d.isoformat()}'}), 400
        try:
            amt = float(r.get('amount'))
        except (TypeError, ValueError):
            return jsonify({'error': 'Invalid amount'}), 400
        if amt <= 0:
            return jsonify({'error': 'Amount must be positive'}), 400
        desc = str(r.get('description') or 'Imported repayment').strip()[:500]
        if not desc:
            desc = 'Imported repayment'
        validated.append((d.strftime('%Y-%m-%d'), round(amt, 2), desc))

    for d_str, amt, desc in validated:
        stored_amt = -amt
        new_transaction = {
            'date': d_str,
            'type': 'repayment',
            'amount': stored_amt,
            'description': desc,
            'user': username,
        }
        loan['transactions'].append(new_transaction)
        loan['loan_amount'] += stored_amt

    save_data(data)
    return jsonify(loan)


@app.route('/api/loan/<loan_id>/statement/baseline-preview', methods=['POST'])
@login_required
def statement_baseline_preview(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    loan = data['loans'][loan_id]
    if loan.get('deleted'):
        return jsonify({'error': 'Loan is deleted'}), 400

    if not _get_openai_client():
        return jsonify({'error': 'Statement import is not configured (set OPENAI_API_KEY).'}), 503

    prep, err = _prepare_statement_text_from_upload()
    if err:
        return jsonify({'error': err}), 400
    text, truncated_text = prep

    try:
        raw_txs, period_end = _extract_baseline_statement_llm(text)
    except Exception as e:
        logger.exception('baseline LLM extraction failed')
        return jsonify({'error': str(e)}), 500

    candidates = _normalize_candidates(raw_txs)
    inferred_day = _default_reminder_day_from_period_end(period_end)
    return jsonify({
        'candidates': candidates,
        'truncated': truncated_text,
        'inferred_period_end': period_end,
        'inferred_reminder_day': inferred_day,
    })


@app.route('/api/loan/<loan_id>/statement/baseline-save', methods=['POST'])
@login_required
def statement_baseline_save(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    loan = data['loans'][loan_id]
    if loan.get('deleted'):
        return jsonify({'error': 'Loan is deleted'}), 400

    payload = request.get_json(silent=True) or {}
    items = payload.get('items')
    if not isinstance(items, list) or not items:
        return jsonify({'error': 'items[] is required and must be non-empty'}), 400

    day = int(payload.get('day_of_month', 1))
    if not 1 <= day <= 31:
        return jsonify({'error': 'day_of_month must be 1–31'}), 400

    reminder_email = str(payload.get('reminder_email') or '').strip()
    inferred_period_end = payload.get('inferred_period_end')
    if inferred_period_end is not None and inferred_period_end != '':
        d = _parse_iso_date(str(inferred_period_end))
        inferred_period_end = d.strftime('%Y-%m-%d') if d else None
    else:
        inferred_period_end = None

    baseline = []
    for row in items:
        p = _parse_baseline_item_row(row)
        if p:
            baseline.append(p)

    if not baseline:
        return jsonify({'error': 'No valid baseline items'}), 400

    loan['bill_baseline'] = baseline
    loan['bill_reminder_email'] = reminder_email or None
    loan['bill_reminder'] = {
        'day_of_month': day,
        'last_sent_month': (loan.get('bill_reminder') or {}).get('last_sent_month'),
        'inferred_period_end': inferred_period_end,
    }
    save_data(data)
    schedule_bill_reminders_job()
    return jsonify({
        'ok': True,
        'bill_baseline': baseline,
        'bill_reminder': loan['bill_reminder'],
        'bill_reminder_email': loan.get('bill_reminder_email'),
    })


@app.route('/api/loan/<loan_id>/bill-baseline', methods=['GET'])
@login_required
def get_bill_baseline(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    loan = data['loans'][loan_id]
    return jsonify({
        'bill_baseline': loan.get('bill_baseline') or [],
        'bill_reminder': loan.get('bill_reminder'),
        'bill_reminder_email': loan.get('bill_reminder_email'),
        'smtp_configured': _get_smtp_config() is not None,
        'default_notify_email': os.getenv('FINANCE_TRACKER_NOTIFY_EMAIL', '').strip(),
    })


@app.route('/api/loan/<loan_id>/bill-baseline', methods=['PATCH'])
@login_required
def patch_bill_baseline_reminder(loan_id):
    """Update reminder email (and optionally reminder day) without replacing baseline rows."""
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    loan = data['loans'][loan_id]
    if loan.get('deleted'):
        return jsonify({'error': 'Loan is deleted'}), 400

    payload = request.get_json(silent=True) or {}
    if not payload:
        return jsonify({'error': 'JSON body required'}), 400

    if 'reminder_email' in payload:
        re = str(payload.get('reminder_email') or '').strip()
        loan['bill_reminder_email'] = re or None
    if 'day_of_month' in payload:
        day = int(payload.get('day_of_month', 1))
        if not 1 <= day <= 31:
            return jsonify({'error': 'day_of_month must be 1–31'}), 400
        br = dict(loan.get('bill_reminder') or {})
        br['day_of_month'] = day
        loan['bill_reminder'] = br

    if 'reminder_email' not in payload and 'day_of_month' not in payload:
        return jsonify({'error': 'Provide reminder_email and/or day_of_month'}), 400

    save_data(data)
    schedule_bill_reminders_job()
    return jsonify({
        'ok': True,
        'bill_reminder_email': loan.get('bill_reminder_email'),
        'bill_reminder': loan.get('bill_reminder'),
    })


@app.route('/api/loan/<loan_id>/bill-baseline', methods=['PUT'])
@login_required
def put_bill_baseline(loan_id):
    """Replace all baseline items. Optional: day_of_month, reminder_email, inferred_period_end."""
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    loan = data['loans'][loan_id]
    if loan.get('deleted'):
        return jsonify({'error': 'Loan is deleted'}), 400

    payload = request.get_json(silent=True) or {}
    items = payload.get('items')
    if not isinstance(items, list):
        return jsonify({'error': 'items must be an array'}), 400

    baseline = []
    for i, row in enumerate(items):
        p = _parse_baseline_item_row(row)
        if p is None:
            return jsonify({'error': f'Invalid baseline item at index {i}'}), 400
        baseline.append(p)

    loan['bill_baseline'] = baseline

    if 'day_of_month' in payload:
        day = int(payload.get('day_of_month', 1))
        if not 1 <= day <= 31:
            return jsonify({'error': 'day_of_month must be 1–31'}), 400
        br = dict(loan.get('bill_reminder') or {})
        br['day_of_month'] = day
        loan['bill_reminder'] = br
    if 'reminder_email' in payload:
        re = str(payload.get('reminder_email') or '').strip()
        loan['bill_reminder_email'] = re or None
    if 'inferred_period_end' in payload:
        ipe = payload.get('inferred_period_end')
        if ipe is not None and ipe != '':
            d = _parse_iso_date(str(ipe))
            ipe = d.strftime('%Y-%m-%d') if d else None
        else:
            ipe = None
        br = dict(loan.get('bill_reminder') or {})
        br['inferred_period_end'] = ipe
        loan['bill_reminder'] = br

    save_data(data)
    schedule_bill_reminders_job()
    return jsonify({
        'ok': True,
        'bill_baseline': baseline,
        'bill_reminder': loan.get('bill_reminder'),
        'bill_reminder_email': loan.get('bill_reminder_email'),
    })


@app.route('/api/loan/<loan_id>/bill-baseline/item/<item_id>', methods=['DELETE'])
@login_required
def delete_bill_baseline_item(loan_id, item_id):
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    loan = data['loans'][loan_id]
    if loan.get('deleted'):
        return jsonify({'error': 'Loan is deleted'}), 400

    bl = loan.get('bill_baseline') or []
    new_bl = [b for b in bl if str(b.get('id')) != str(item_id)]
    if len(new_bl) == len(bl):
        return jsonify({'error': 'Item not found'}), 404

    loan['bill_baseline'] = new_bl
    save_data(data)
    return jsonify({'ok': True, 'bill_baseline': new_bl})


@app.route('/api/loan/<loan_id>/bill-baseline/item/<item_id>', methods=['PATCH'])
@login_required
def patch_bill_baseline_item(loan_id, item_id):
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    loan = data['loans'][loan_id]
    if loan.get('deleted'):
        return jsonify({'error': 'Loan is deleted'}), 400

    bl = loan.get('bill_baseline') or []
    idx = next((i for i, b in enumerate(bl) if str(b.get('id')) == str(item_id)), None)
    if idx is None:
        return jsonify({'error': 'Item not found'}), 404

    payload = request.get_json(silent=True) or {}
    cur = dict(bl[idx])
    if 'description' in payload:
        d = str(payload.get('description') or '').strip()[:500]
        cur['description'] = d if d else 'Bill'
    if 'amount_bank' in payload:
        try:
            ab = float(payload.get('amount_bank'))
        except (TypeError, ValueError):
            return jsonify({'error': 'Invalid amount_bank'}), 400
        if ab <= 0:
            return jsonify({'error': 'amount_bank must be positive'}), 400
        cur['amount_bank'] = round(ab, 2)
    if 'amount_share' in payload:
        try:
            sh = float(payload.get('amount_share'))
        except (TypeError, ValueError):
            return jsonify({'error': 'Invalid amount_share'}), 400
        if sh <= 0:
            return jsonify({'error': 'amount_share must be positive'}), 400
        cur['amount_share'] = round(sh, 2)
    if 'category' in payload:
        c = payload.get('category')
        cur['category'] = str(c)[:120] if c is not None and str(c).strip() else None
    if 'note' in payload:
        cur['note'] = _sanitize_note(payload.get('note'))

    merged = _parse_baseline_item_row(cur)
    if merged is None:
        return jsonify({'error': 'Invalid item after update'}), 400
    merged['id'] = str(cur.get('id'))

    bl[idx] = merged
    loan['bill_baseline'] = bl
    save_data(data)
    return jsonify({'ok': True, 'item': merged, 'bill_baseline': bl})


@app.route('/api/loan/<loan_id>/bill-baseline/merge-candidates', methods=['POST'])
@login_required
def merge_baseline_candidates(loan_id):
    """POST JSON {\"candidates\": [...]} — same shape as normalized statement candidates. Returns merged baseline-shaped rows."""
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    loan = data['loans'][loan_id]
    if loan.get('deleted'):
        return jsonify({'error': 'Loan is deleted'}), 400

    payload = request.get_json(silent=True) or {}
    candidates = payload.get('candidates')
    if not isinstance(candidates, list):
        return jsonify({'error': 'candidates must be an array'}), 400

    existing = loan.get('bill_baseline') or []
    items = merge_baseline_with_candidates(existing, candidates)
    return jsonify({'items': items})


@app.route('/api/spending/statement/preview', methods=['POST'])
@login_required
def spending_statement_preview():
    if not _get_openai_client():
        return jsonify({'error': 'Statement analysis is not configured (set OPENAI_API_KEY).'}), 503
    prep, err = _prepare_statement_text_from_upload(for_spending=True)
    if err:
        return jsonify({'error': err}), 400
    text, truncated_text, direction_hints, pipeline = prep

    period, perr = _parse_spending_period_from_values(
        request.form.get('report_month'),
        request.form.get('period_start'),
        request.form.get('period_end'),
    )
    if perr:
        return jsonify({'error': perr}), 400

    try:
        body = _spending_statement_preview_payload(
            text, truncated_text, direction_hints, pipeline, period
        )
    except Exception as e:
        logger.exception('spending statement extraction failed')
        return jsonify({'error': str(e)}), 500
    return jsonify(body)


@app.route('/api/spending/statement/preview-stream', methods=['POST'])
@login_required
def spending_statement_preview_stream():
    """NDJSON stream of progress events; ends with {\"type\":\"complete\",\"payload\":{...}}."""
    if not _get_openai_client():

        def err503():
            yield json.dumps({
                'type': 'error',
                'message': 'Statement analysis is not configured (set OPENAI_API_KEY).',
                'http_status': 503,
                'elapsed_ms': 0,
            }) + '\n'

        return Response(stream_with_context(err503()), mimetype='application/x-ndjson', status=503)

    period, perr = _parse_spending_period_from_values(
        request.form.get('report_month'),
        request.form.get('period_start'),
        request.form.get('period_end'),
    )
    if perr:

        def err400():
            yield json.dumps({'type': 'error', 'message': perr, 'http_status': 400, 'elapsed_ms': 0}) + '\n'

        return Response(stream_with_context(err400()), mimetype='application/x-ndjson', status=400)

    f = request.files.get('file')
    if not f or not getattr(f, 'filename', None):

        def err_nf():
            yield json.dumps({'type': 'error', 'message': 'No file uploaded', 'http_status': 400, 'elapsed_ms': 0}) + '\n'

        return Response(stream_with_context(err_nf()), mimetype='application/x-ndjson', status=400)

    name = (f.filename or '').lower()
    # Buffer the full upload before streaming: Werkzeug closes the input stream once the
    # response body is iterated, so f.read() inside the generator raises "I/O on closed file".
    raw = f.read()
    if len(raw) > STATEMENT_MAX_BYTES:

        def err_sz():
            yield json.dumps({
                'type': 'error',
                'message': 'File too large (max 2 MB).',
                'http_status': 400,
                'elapsed_ms': 0,
            }) + '\n'

        return Response(stream_with_context(err_sz()), mimetype='application/x-ndjson', status=400)

    def ndjson_gen():
        t0 = time.perf_counter()

        def elapsed_ms() -> int:
            return int((time.perf_counter() - t0) * 1000)

        def emit(obj: dict) -> str:
            d = dict(obj)
            d.setdefault('elapsed_ms', elapsed_ms())
            return json.dumps(d, default=str) + '\n'

        yield emit({
            'type': 'progress',
            'step': 'file_loaded',
            'message': f'Received file ({len(raw):,} bytes). Parsing statement…',
        })

        prep = None
        for event in _iter_prepare_spending_raw(name, raw):
            et = event.get('type')
            if et == 'progress':
                yield emit(event)
            elif et == 'error':
                yield emit(event)
                return
            elif et == 'prep_complete':
                prep = event.get('prep')
            else:
                logger.warning('unknown prep event: %s', event)

        if not prep:
            yield emit({'type': 'error', 'message': 'Statement preparation failed.', 'http_status': 500})
            return

        text, truncated_text, direction_hints, pipeline = prep

        period_hint = (
            f'Prefer transactions that fall on or between {period["period_start"]} and {period["period_end"]} inclusive. '
            f'When in doubt, include the row and use its statement date for the "date" field.'
        )

        yield emit({'type': 'progress', 'step': 'load_profile', 'message': 'Loading your spending profile…'})
        data = load_data()
        spending, changed = _ensure_user_spending(data, session['username'])
        if changed:
            save_data(data)

        raw_rows = None
        extraction_meta = None
        try:
            for ev in iter_spending_transaction_extraction(text, period_hint):
                et = ev.get('type')
                if et == 'progress':
                    yield emit(ev)
                elif et == 'result':
                    raw_rows = ev.get('rows')
                    extraction_meta = ev.get('meta')
        except Exception as e:
            logger.exception('spending statement stream extraction failed')
            yield emit({'type': 'error', 'message': str(e), 'http_status': 500})
            return

        if raw_rows is None:
            raw_rows = []

        yield emit({
            'type': 'progress',
            'step': 'postprocess',
            'message': 'Normalising rows, filtering dates, reconciling directions, applying categories…',
        })
        try:
            body = _spending_statement_preview_finalize(
                data,
                spending,
                period,
                direction_hints,
                pipeline,
                truncated_text,
                raw_rows,
                extraction_meta,
            )
        except Exception as e:
            logger.exception('spending statement postprocess failed')
            yield emit({'type': 'error', 'message': str(e), 'http_status': 500})
            return

        yield emit({'type': 'progress', 'step': 'done', 'message': 'Preview ready.'})
        yield emit({'type': 'complete', 'payload': body})

    return Response(
        stream_with_context(ndjson_gen()),
        mimetype='application/x-ndjson',
        headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'},
    )


@app.route('/api/spending/statement/import', methods=['POST'])
@login_required
def spending_statement_import():
    payload = request.get_json(silent=True) or {}
    rows = payload.get('transactions')
    if not isinstance(rows, list) or not rows:
        return jsonify({'error': 'transactions[] is required and must be non-empty'}), 400

    period, perr = _parse_spending_period_from_values(
        payload.get('report_month'),
        payload.get('period_start'),
        payload.get('period_end'),
    )
    if perr:
        return jsonify({'error': perr}), 400
    report_month = period['report_month']
    ps_d = period['period_start_date']
    pe_d = period['period_end_date']

    data = load_data()
    spending, changed = _ensure_user_spending(data, session['username'])
    if changed:
        save_data(data)

    validated = []
    for row in rows:
        if not isinstance(row, dict):
            return jsonify({'error': 'Invalid transaction entry'}), 400
        d = _parse_bank_statement_date(str(row.get('date') or ''))
        if d is None:
            return jsonify({'error': f'Invalid date: {row.get("date")!r}'}), 400
        if d < ps_d or d > pe_d:
            return jsonify({'error': f'Transaction date {d.isoformat()} is outside the reporting period.'}), 400
        desc = str(row.get('description') or '').strip()[:500] or 'Bank transaction'
        col_dir, col_amt = _direction_and_amount_from_money_columns(row)
        if col_dir is not None and col_amt is not None:
            amount = round(col_amt, 2)
            direction = col_dir
        else:
            try:
                amount = float(row.get('amount'))
            except (TypeError, ValueError):
                return jsonify({'error': 'Invalid amount'}), 400
            if amount <= 0:
                return jsonify({'error': 'Amount must be positive'}), 400
            amount = round(abs(amount), 2)
            direction = _normalize_spending_direction(row.get('direction'), desc, amount)
        category = row.get('category')
        if direction == 'outgoing':
            category = str(category or 'unclassified').strip().lower()
            if category not in SPENDING_CATEGORY_SET:
                category = 'unclassified'
        else:
            category = None
        rationale = str(row.get('rationale') or '').strip()[:240]
        confidence = row.get('confidence')
        if confidence is not None:
            try:
                confidence = max(0.0, min(1.0, float(confidence)))
            except (TypeError, ValueError):
                confidence = None
        validated.append({
            'date': d.strftime('%Y-%m-%d'),
            'month': d.strftime('%Y-%m'),
            'report_month': report_month,
            'amount': round(amount, 2),
            'description': desc,
            'direction': direction,
            'category': category,
            'confidence': confidence,
            'rationale': rationale,
        })

    tx_store = spending.setdefault('transactions', [])
    statement_store = spending.setdefault('statements', [])
    existing_fingerprints = {
        str(t.get('fingerprint'))
        for t in tx_store
        if t.get('fingerprint')
    }
    statement_id = str(uuid.uuid4())
    now_iso = datetime.utcnow().isoformat() + 'Z'
    inserted = 0
    skipped = 0
    affected_months = set()

    for row in validated:
        fp = _spending_fingerprint(
            report_month, row['date'], row['amount'], row['direction'], row['description']
        )
        if fp in existing_fingerprints:
            skipped += 1
            continue
        existing_fingerprints.add(fp)
        tx = {
            'id': str(uuid.uuid4()),
            'date': row['date'],
            'month': row['month'],
            'report_month': report_month,
            'description': row['description'],
            'amount': row['amount'],
            'direction': row['direction'],
            'category': row['category'],
            'confidence': row['confidence'],
            'rationale': row['rationale'],
            'source_statement_id': statement_id,
            'created_at': now_iso,
            'fingerprint': fp,
        }
        tx_store.append(tx)
        inserted += 1
        affected_months.add(report_month)
        next_month = _month_next(report_month)
        if next_month:
            affected_months.add(next_month)

    statement_record = {
        'id': statement_id,
        'uploaded_at': now_iso,
        'file_name': str(payload.get('file_name') or '').strip()[:200],
        'report_month': report_month,
        'period_start': period['period_start'],
        'period_end': period['period_end'],
        'original_row_count': len(validated),
        'imported_count': inserted,
        'skipped_duplicates': skipped,
        'months': sorted({report_month}),
    }
    statement_store.append(statement_record)
    reconciliation = apply_auto_transfer_pairing_for_month(spending, report_month)
    _recompute_monthly_insights(spending, affected_months if affected_months else None)
    save_data(data)

    return jsonify({
        'ok': True,
        'statement_id': statement_id,
        'imported_count': inserted,
        'skipped_duplicates': skipped,
        'report_month': report_month,
        'period_start': period['period_start'],
        'period_end': period['period_end'],
        'months': sorted({report_month}),
        'reconciliation': reconciliation,
    })


def _normalize_spending_month_key(raw: str | None) -> str | None:
    s = (raw or '').strip()[:7]
    if len(s) != 7 or s[4] != '-':
        return None
    try:
        datetime.strptime(s + '-01', '%Y-%m-%d')
    except ValueError:
        return None
    return s


@app.route('/api/spending/months', methods=['GET'])
@login_required
def spending_months():
    data = load_data()
    spending, changed = _ensure_user_spending(data, session['username'])
    if changed:
        save_data(data)
    months = sorted((spending.get('monthly_insights') or {}).keys(), reverse=True)
    return jsonify({'months': months})


@app.route('/api/spending/metrics-trend', methods=['GET'])
@login_required
def spending_metrics_trend():
    """Chronological series of income, outgoing, and net from stored monthly insights (for charts)."""
    data = load_data()
    spending, changed = _ensure_user_spending(data, session['username'])
    if changed:
        save_data(data)
    insight_map = spending.get('monthly_insights') or {}
    months = sorted(insight_map.keys())
    points = []
    for m in months:
        ins = insight_map.get(m)
        if not isinstance(ins, dict):
            continue
        try:
            income = float(ins.get('income_total', 0) or 0)
            out = float(ins.get('outgoing_total', 0) or 0)
            net = float(ins.get('net', 0) or 0)
        except (TypeError, ValueError):
            continue
        outgoing_by_category: dict[str, float] = {}
        for row in ins.get('category_breakdown') or []:
            if not isinstance(row, dict):
                continue
            c = str(row.get('category', 'unclassified') or 'unclassified')
            try:
                raw_amt = float(row.get('amount', 0) or 0)
            except (TypeError, ValueError):
                continue
            amt = round(raw_amt, 2)
            if amt <= 0:
                continue
            outgoing_by_category[c] = amt
        points.append({
            'month': m,
            'income': round(income, 2),
            'outgoing': round(out, 2),
            'net': round(net, 2),
            'outgoing_by_category': outgoing_by_category,
        })
    return jsonify({'points': points})


@app.route('/api/spending/month/<month>', methods=['DELETE'])
@login_required
def spending_delete_month(month):
    mk = _normalize_spending_month_key(month)
    if not mk:
        return jsonify({'error': 'Invalid month (use YYYY-MM)'}), 400

    data = load_data()
    spending, changed = _ensure_user_spending(data, session['username'])
    if changed:
        save_data(data)

    txs = spending.setdefault('transactions', [])
    before = len(txs)
    txs[:] = [t for t in txs if _report_month_for_spending_tx(t) != mk]
    removed = before - len(txs)

    statements = spending.setdefault('statements', [])
    spending['statements'] = [
        s for s in statements
        if isinstance(s, dict) and str(s.get('report_month') or '').strip()[:7] != mk
    ]

    _recompute_monthly_insights(spending, None)
    save_data(data)

    months = sorted((spending.get('monthly_insights') or {}).keys(), reverse=True)
    return jsonify({
        'ok': True,
        'month': mk,
        'removed_transactions': removed,
        'months': months,
    })


@app.route('/api/spending/insights', methods=['GET'])
@login_required
def spending_insights():
    data = load_data()
    spending, changed = _ensure_user_spending(data, session['username'])
    if changed:
        save_data(data)
    requested_month = (request.args.get('month') or '').strip()
    months = sorted((spending.get('monthly_insights') or {}).keys(), reverse=True)
    if not months:
        return jsonify({'month': None, 'insight': None, 'available_months': []})
    target_month = requested_month or months[0]
    insight_map = spending.get('monthly_insights') or {}
    need_recompute = target_month not in insight_map
    if not need_recompute:
        cur = insight_map.get(target_month)
        if isinstance(cur, dict) and 'largest_outgoing' not in cur:
            need_recompute = True
        if isinstance(cur, dict) and 'budget_action_items' not in cur:
            need_recompute = True
    if need_recompute:
        _recompute_monthly_insights(spending, {target_month})
        save_data(data)
    insight = (spending.get('monthly_insights') or {}).get(target_month)
    month_rows = [t for t in (spending.get('transactions') or []) if _report_month_for_spending_tx(t) == target_month]
    month_rows.sort(key=lambda r: (r.get('date', ''), r.get('description', '')))
    return jsonify({
        'month': target_month,
        'insight': insight,
        'available_months': sorted((spending.get('monthly_insights') or {}).keys(), reverse=True),
        'transactions': month_rows,
    })


@app.route('/api/spending/insights/recompute', methods=['POST'])
@login_required
def spending_insights_recompute():
    """
    Rebuild all stored monthly insight blobs from current transactions.
    Use after app changes or to refresh KPIs, trends, and anomalies.
    """
    data = load_data()
    spending, changed = _ensure_user_spending(data, session['username'])
    if changed:
        save_data(data)
    _recompute_monthly_insights(spending, None)
    save_data(data)
    months = sorted((spending.get('monthly_insights') or {}).keys(), reverse=True)
    return jsonify({'ok': True, 'months': months, 'recomputed': len(months)})


@app.route('/api/spending/savings-advice', methods=['POST'])
@login_required
def spending_savings_advice():
    """
    Data-grounded savings suggestions via LLM. Sends focal insight, capped per-line transactions for
    the focal month (and a small per-line sample for the two most recent prior months), plus trend_series.
    Requires OPENAI_API_KEY. Third party receives this JSON.
    """
    payload = request.get_json(silent=True) or {}
    data = load_data()
    spending, changed = _ensure_user_spending(data, session['username'])
    if changed:
        save_data(data)
    available = sorted((spending.get('monthly_insights') or {}).keys(), reverse=True)
    if not available:
        return jsonify({'error': 'No spending data'}), 404
    month = str(payload.get('month') or '').strip() or available[0]
    if month not in available:
        return jsonify({'error': 'Unknown month or no data for that month.'}), 400
    insight_map = spending.get('monthly_insights') or {}
    need = (month not in insight_map) or (not isinstance(insight_map.get(month), dict)) or (
        isinstance(insight_map.get(month), dict) and 'budget_action_items' not in (insight_map.get(month) or {})
    )
    if need:
        _recompute_monthly_insights(spending, {month})
        save_data(data)
    insight_map = spending.get('monthly_insights') or {}
    if not isinstance(insight_map.get(month), dict):
        return jsonify({'error': 'No insight for that month.'}), 400
    if not _get_openai_client():
        return jsonify({
            'error': 'Savings ideas require OPENAI_API_KEY to be set on the server.',
        }), 503
    context, n_trend, berr, tx_meta = _build_savings_advice_context(spending, month)
    if berr or not context:
        return jsonify({'error': 'Could not build savings context.'}), 400
    advice, llm_err, model_used = _generate_savings_advice_with_llm(context)
    if not advice or llm_err:
        return jsonify({
            'error': llm_err or 'Could not generate savings ideas. Try again.',
        }), 500
    out: dict = {
        'month': month,
        'advice': advice,
        'model': model_used,
        'trend_months_included': n_trend,
    }
    if tx_meta and isinstance(tx_meta, dict):
        ft = tx_meta.get('focal_transactions') or {}
        if isinstance(ft, dict):
            out['focal_transaction_count_in_month'] = ft.get('count_in_month', 0)
            out['focal_transaction_count_sent'] = ft.get('count_sent', 0)
            out['focal_transactions_truncated'] = bool(ft.get('truncated'))
        out['prior_transaction_sample_months'] = tx_meta.get('prior_transaction_months_included', 0)
    return jsonify(out)


@app.route('/api/spending/transaction/<tx_id>/category', methods=['PATCH'])
@login_required
def spending_reclassify(tx_id):
    payload = request.get_json(silent=True) or {}
    category = str(payload.get('category') or '').strip().lower()
    if category not in SPENDING_CATEGORY_SET:
        return jsonify({'error': 'Invalid category'}), 400

    data = load_data()
    spending, changed = _ensure_user_spending(data, session['username'])
    if changed:
        save_data(data)

    txs = spending.setdefault('transactions', [])
    target = next((t for t in txs if str(t.get('id')) == str(tx_id)), None)
    if not target:
        return jsonify({'error': 'Transaction not found'}), 404
    if target.get('direction') != 'outgoing':
        return jsonify({'error': 'Only outgoing transactions can be reclassified'}), 400

    target['category'] = category
    target['confidence'] = 1.0
    target['rationale'] = 'manual override'
    label = _normalize_label(target.get('description', ''))
    if label:
        spending.setdefault('classification_overrides', {})[label] = category

    bucket = target.get('report_month') or target.get('month')
    months_to_recompute = {bucket}
    nxt = _month_next(str(bucket or ''))
    if nxt:
        months_to_recompute.add(nxt)
    _recompute_monthly_insights(spending, {m for m in months_to_recompute if m})
    save_data(data)

    return jsonify({
        'ok': True,
        'transaction': target,
        'insight': (spending.get('monthly_insights') or {}).get(bucket),
    })


@app.route('/api/spending/transaction/<tx_id>/insights', methods=['PATCH'])
@login_required
def spending_transaction_insights_exclude(tx_id):
    """Set ``insights_excluded`` for a non-paired transaction (same effect as linked rows on KPIs)."""
    payload = request.get_json(silent=True) or {}
    raw = payload.get('excluded')
    if raw is None:
        raw = payload.get('insights_excluded')
    if raw is None:
        return jsonify({'error': 'excluded (boolean) is required'}), 400
    if not isinstance(raw, bool):
        return jsonify({'error': 'excluded must be a boolean'}), 400
    excluded = raw

    data = load_data()
    spending, changed = _ensure_user_spending(data, session['username'])
    if changed:
        save_data(data)

    txs = spending.setdefault('transactions', [])
    target = next((t for t in txs if str(t.get('id')) == str(tx_id)), None)
    if not target:
        return jsonify({'error': 'Transaction not found'}), 404
    if _spending_is_paired_leg(target):
        return jsonify({
            'error': 'Linked internal transfers are already excluded; use Unlink if you need them in totals.',
        }), 400

    target['insights_excluded'] = excluded
    bucket = str(_report_month_for_spending_tx(target) or '')
    months_to_recompute = {bucket}
    nxt = _month_next(bucket)
    if nxt:
        months_to_recompute.add(nxt)
    _recompute_monthly_insights(spending, {m for m in months_to_recompute if m})
    save_data(data)

    return jsonify({
        'ok': True,
        'transaction': target,
        'insight': (spending.get('monthly_insights') or {}).get(bucket),
    })


@app.route('/api/spending/transaction/<tx_id>/pair', methods=['PATCH'])
@login_required
def spending_pair_transaction(tx_id):
    payload = request.get_json(silent=True) or {}
    if payload.get('unlink') is True:
        data = load_data()
        spending, changed = _ensure_user_spending(data, session['username'])
        if changed:
            save_data(data)
        txs = spending.setdefault('transactions', [])
        target = next((t for t in txs if str(t.get('id')) == str(tx_id)), None)
        if not target:
            return jsonify({'error': 'Transaction not found'}), 404
        if not target.get('transfer_pair_id'):
            return jsonify({'error': 'Transaction is not paired'}), 400
        if not _unlink_spending_pair(txs, target):
            return jsonify({'error': 'Failed to unlink pair'}), 400
        bucket = str(_report_month_for_spending_tx(target) or '')
        _recompute_monthly_insights(spending, {bucket} if bucket else None)
        save_data(data)
        return jsonify({
            'ok': True,
            'insight': (spending.get('monthly_insights') or {}).get(bucket),
            'reconciliation': _spending_transfer_reconciliation_for_month(
                [t for t in txs if _report_month_for_spending_tx(t) == bucket]
            ),
        })

    peer_id = str(payload.get('peer_id') or '').strip()
    if not peer_id:
        return jsonify({'error': 'peer_id is required, or set unlink: true'}), 400

    data = load_data()
    spending, changed = _ensure_user_spending(data, session['username'])
    if changed:
        save_data(data)
    txs = spending.setdefault('transactions', [])
    a = next((t for t in txs if str(t.get('id')) == str(tx_id)), None)
    b = next((t for t in txs if str(t.get('id')) == peer_id), None)
    err = _spending_manual_pair_error(a, b)
    if err:
        return jsonify({'error': err}), 400
    _apply_spending_pair_to_rows(a, b, 'manual')
    bucket = str(_report_month_for_spending_tx(a) or '')
    _recompute_monthly_insights(spending, {bucket} if bucket else None)
    save_data(data)
    return jsonify({
        'ok': True,
        'transactions': [a, b],
        'insight': (spending.get('monthly_insights') or {}).get(bucket),
        'reconciliation': _spending_transfer_reconciliation_for_month(
            [t for t in txs if _report_month_for_spending_tx(t) == bucket]
        ),
    })


@app.route('/api/spending/recategorize', methods=['POST'])
@login_required
def spending_recategorize_month():
    """Re-run AI categorisation for outgoing transactions in a reporting month (manual overrides kept)."""
    payload = request.get_json(silent=True) or {}
    report_month = str(payload.get('report_month') or '').strip()[:7]
    data = load_data()
    spending, changed = _ensure_user_spending(data, session['username'])
    if changed:
        save_data(data)
    result = _rerun_spending_categorization_for_month(spending, report_month)
    if not result.get('ok'):
        return jsonify({'error': result.get('error', 'Recategorisation failed')}), 400
    save_data(data)
    target_month = report_month
    month_rows = [
        t for t in (spending.get('transactions') or [])
        if _report_month_for_spending_tx(t) == target_month
    ]
    month_rows.sort(key=lambda r: (r.get('date', ''), r.get('description', '')))
    return jsonify({
        'ok': True,
        'report_month': target_month,
        'labels_refreshed': result.get('labels_refreshed', 0),
        'insight': (spending.get('monthly_insights') or {}).get(target_month),
        'transactions': month_rows,
        'available_months': sorted((spending.get('monthly_insights') or {}).keys(), reverse=True),
    })


@app.route('/api/spending/pair/apply', methods=['POST'])
@login_required
def spending_pair_reapply():
    """Re-run auto internal-transfer matching for a reporting month."""
    payload = request.get_json(silent=True) or {}
    report_month = str(payload.get('report_month') or '').strip()[:7]
    if len(report_month) != 7 or report_month[4] != '-':
        return jsonify({'error': 'report_month (YYYY-MM) is required'}), 400
    data = load_data()
    spending, changed = _ensure_user_spending(data, session['username'])
    if changed:
        save_data(data)
    reconciliation = apply_auto_transfer_pairing_for_month(spending, report_month)
    _recompute_monthly_insights(spending, {report_month})
    save_data(data)
    return jsonify({
        'ok': True,
        'reconciliation': reconciliation,
        'insight': (spending.get('monthly_insights') or {}).get(report_month),
    })


if __name__ == '__main__':
    # Schedule initial interest tasks and recurring payment processing for all loans
    data = load_data()
    for loan_id, loan in data['loans'].items():
        schedule_interest_task(loan_id, loan['interest_day'])
        if 'recurring_payments' in loan and loan['recurring_payments']:
            schedule_recurring_payments(loan_id)
    schedule_bill_reminders_job()
    app.run(debug=False, use_reloader=False)  # disable reloader to prevent duplicate schedulers
