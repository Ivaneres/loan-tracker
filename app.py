from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from functools import wraps
from datetime import datetime, date, timedelta
import io
import json
import logging
import os
import re
import smtplib
import uuid
from email.message import EmailMessage
from difflib import SequenceMatcher
from dateutil.relativedelta import relativedelta
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
# Import preview: flag when existing transaction amount is within this many pounds of bank or share.
STATEMENT_AUDIT_MATCH_TOLERANCE = 5.0

# SMTP for monthly bill-upload reminders (optional). If SMTP_HOST is unset, reminders are skipped.
#   SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD
#   SMTP_USE_TLS — default true (STARTTLS)
#   MAIL_FROM — From address
#   FINANCE_TRACKER_NOTIFY_EMAIL — default To when loan has no bill_reminder_email
#   PUBLIC_BASE_URL — e.g. https://tracker.example.com (no trailing slash) for links in emails
BILL_REMINDER_HOUR = 9
BASELINE_SIM_THRESHOLD = 0.75
BASELINE_NOTE_MAX_LEN = 300


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
        'loans': {}  # Dictionary to store multiple loans
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
            
            return existing_data
            
    except (FileNotFoundError, json.JSONDecodeError):
        save_data(default_data)
        return default_data

def save_data(data):
    with open('data.json', 'w') as f:
        json.dump(data, f, indent=2)

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


def _extract_pdf_text(raw: bytes) -> str:
    reader = PdfReader(io.BytesIO(raw))
    parts = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            parts.append(t)
    return '\n'.join(parts)


def _parse_iso_date(s: str):
    if not s or not isinstance(s, str):
        return None
    s = s.strip()[:32]
    try:
        return datetime.strptime(s[:10], '%Y-%m-%d').date()
    except ValueError:
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
    data = json.loads(raw)
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


def _prepare_statement_text_from_upload():
    """Read multipart file from request; return (text, truncated_bool) or (None, error_message)."""
    f = request.files.get('file')
    if not f or not getattr(f, 'filename', None):
        return None, 'No file uploaded'
    raw = f.read()
    if len(raw) > STATEMENT_MAX_BYTES:
        return None, 'File too large (max 2 MB).'
    name = (f.filename or '').lower()
    try:
        if name.endswith('.pdf'):
            text = _extract_pdf_text(raw)
        else:
            text = raw.decode('utf-8', errors='replace')
    except Exception as e:
        logger.exception('statement read failed')
        return None, f'Could not read file: {e}'
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
    data = json.loads(raw)
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
    return render_template('loans.html', loans=loans_summary)

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


if __name__ == '__main__':
    # Schedule initial interest tasks and recurring payment processing for all loans
    data = load_data()
    for loan_id, loan in data['loans'].items():
        schedule_interest_task(loan_id, loan['interest_day'])
        if 'recurring_payments' in loan and loan['recurring_payments']:
            schedule_recurring_payments(loan_id)
    schedule_bill_reminders_job()
    app.run(debug=False, use_reloader=False)  # disable reloader to prevent duplicate schedulers
