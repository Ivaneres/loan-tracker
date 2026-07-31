function formatMoney(amount) {
    const n = Number(amount) || 0;
    return `£${n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function escapeHtml(s) {
    return String(s || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/** Match app.py _normalize_label for import duplicate fingerprints. */
function normalizeSpendingLabel(s) {
    let t = String(s || '')
        .trim()
        .toLowerCase();
    t = t.replace(/[^a-z0-9 ]+/g, ' ');
    t = t.replace(/\s+/g, ' ').trim();
    return t;
}

/**
 * Match app._spending_fingerprint for the current stats month (import uses the same string).
 * Used when the user changes direction in the preview to re-check duplicates.
 */
function spendingFingerprintForPreview(reportMonth, dateStr, amount, direction, description) {
    const rm = String(reportMonth || '')
        .trim()
        .slice(0, 7);
    const d = normalizeSpendingLabel(description);
    const a = Number(amount);
    const af = Number.isFinite(a) ? a.toFixed(2) : '0.00';
    return `${rm}|${dateStr}|${direction}|${af}|${d}`;
}

function setStatus(message, isError) {
    const el = document.getElementById('spending-status');
    if (!el) return;
    if (!message) {
        el.classList.add('hidden');
        el.textContent = '';
        return;
    }
    el.classList.remove('hidden');
    el.textContent = message;
    el.className = `text-sm mt-3 ${isError ? 'text-red-600' : 'text-gray-600'}`;
}

/** Banner under Monthly Insights (re-categorisation, etc.). opts.busy = in-progress styling. */
function setInsightActionStatus(message, isError, opts) {
    const el = document.getElementById('insight-action-status');
    if (!el) return;
    const busy = opts && opts.busy;
    if (!message) {
        el.classList.add('hidden');
        el.textContent = '';
        el.removeAttribute('aria-busy');
        return;
    }
    el.classList.remove('hidden');
    el.textContent = message;
    if (isError) {
        el.setAttribute('aria-busy', 'false');
        el.className =
            'text-sm mt-3 mb-0 rounded-md px-3 py-2 border border-red-200 bg-red-50 text-red-800';
    } else if (busy) {
        el.setAttribute('aria-busy', 'true');
        el.className =
            'text-sm mt-3 mb-0 rounded-md px-3 py-2 border border-amber-200 bg-amber-50 text-amber-950';
    } else {
        el.setAttribute('aria-busy', 'false');
        el.className =
            'text-sm mt-3 mb-0 rounded-md px-3 py-2 border border-green-200 bg-green-50 text-green-900';
    }
}

/** Human-readable duration for progress UI (ms → e.g. "2m 15s"). */
function formatDuration(ms) {
    const n = Number(ms) || 0;
    if (n < 1000) return `${Math.max(0, Math.round(n / 100) / 10)}s`;
    const s = Math.floor(n / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const rs = s % 60;
    return `${m}m ${rs}s`;
}

/** @param {number|null|undefined} prev */
function formatPctVsPrev(current, prev) {
    if (prev == null || !Number.isFinite(Number(prev)) || Math.abs(prev) < 0.005) {
        return null;
    }
    const p = ((Number(current) - Number(prev)) / Number(prev)) * 100.0;
    if (!Number.isFinite(p)) return null;
    return `${p >= 0 ? '+' : ''}${p.toFixed(1)}%`;
}

const BUDGET_ACTIONS_INITIAL = 3;
const SAVINGS_COACH_INITIAL = 4;

function setDeltaEl(el, delta, { higherIsWorse } = { higherIsWorse: true }) {
    if (!el) return;
    if (delta == null || !Number.isFinite(Number(delta))) {
        el.classList.add('hidden');
        el.textContent = '';
        return;
    }
    const d = Number(delta);
    const abs = formatMoney(Math.abs(d));
    el.classList.remove('hidden', 'spending-metric__delta--up', 'spending-metric__delta--down', 'spending-metric__delta--neutral');
    if (d === 0) {
        el.classList.add('spending-metric__delta--neutral');
        el.textContent = 'Same as last month';
        return;
    }
    const worse = (d > 0 && higherIsWorse) || (d < 0 && !higherIsWorse);
    el.classList.add(worse ? 'spending-metric__delta--up' : 'spending-metric__delta--down');
    const direction = d > 0 ? 'up' : 'down';
    el.textContent = `${direction === 'up' ? 'Up' : 'Down'} ${abs} vs last month`;
}

/**
 * POST multipart to preview-stream; parses NDJSON lines. Invokes onEvent for each object.
 * Resolves with the final payload object or rejects on error / network failure.
 */
function postSpendingPreviewStream(formData, onEvent) {
    return new Promise((resolve, reject) => {
        let settled = false;
        const finish = (fn) => {
            if (settled) return;
            settled = true;
            fn();
        };

        function handleLine(line) {
            const t = line.trim();
            if (!t) return;
            let obj;
            try {
                obj = JSON.parse(t);
            } catch (e) {
                return;
            }
            if (onEvent) onEvent(obj);
            if (obj.type === 'complete') finish(() => resolve(obj.payload));
            if (obj.type === 'error') finish(() => reject(new Error(obj.message || 'Preview failed')));
        }

        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/spending/statement/preview-stream');
        let lastLen = 0;
        let buffer = '';

        xhr.onprogress = () => {
            const chunk = xhr.responseText.slice(lastLen);
            lastLen = xhr.responseText.length;
            buffer += chunk;
            let idx;
            while ((idx = buffer.indexOf('\n')) >= 0) {
                const line = buffer.slice(0, idx);
                buffer = buffer.slice(idx + 1);
                handleLine(line);
            }
        };

        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable && onEvent) {
                onEvent({
                    type: 'upload',
                    loaded: e.loaded,
                    total: e.total,
                    pct: Math.min(100, Math.round((100 * e.loaded) / e.total)),
                });
            }
        };

        xhr.onerror = () => finish(() => reject(new Error('Network error')));

        xhr.onload = () => {
            const chunk = xhr.responseText.slice(lastLen);
            lastLen = xhr.responseText.length;
            buffer += chunk;
            let idx;
            while ((idx = buffer.indexOf('\n')) >= 0) {
                const line = buffer.slice(0, idx);
                buffer = buffer.slice(idx + 1);
                handleLine(line);
            }
            const tail = buffer.trim();
            if (tail) handleLine(tail);
            buffer = '';
            if (!settled && xhr.status >= 400) {
                finish(() => reject(new Error(`Preview failed (${xhr.status})`)));
            } else if (!settled) {
                finish(() => reject(new Error('Incomplete response from server')));
            }
        };

        xhr.send(formData);
    });
}

function ymdFromDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
}

/** First and last calendar day for YYYY-MM (from input type="month"). */
function monthRangeFromYearMonth(ym) {
    if (!ym || ym.length < 7) return null;
    const parts = ym.split('-').map((x) => parseInt(x, 10));
    const y = parts[0];
    const mo = parts[1];
    if (!y || !mo) return null;
    const start = new Date(y, mo - 1, 1);
    const end = new Date(y, mo, 0);
    return { start: ymdFromDate(start), end: ymdFromDate(end) };
}

function syncPeriodRangeFromMonthInput() {
    const monthEl = document.getElementById('report-period-month');
    const startEl = document.getElementById('period-start');
    const endEl = document.getElementById('period-end');
    if (!monthEl || !startEl || !endEl) return;
    const range = monthRangeFromYearMonth(monthEl.value);
    if (!range) return;
    startEl.value = range.start;
    endEl.value = range.end;
}

function getSpendingPeriodPayload() {
    const monthEl = document.getElementById('report-period-month');
    const startEl = document.getElementById('period-start');
    const endEl = document.getElementById('period-end');
    const reportMonth = monthEl && monthEl.value ? monthEl.value : '';
    const periodStart = startEl && startEl.value ? startEl.value : '';
    const periodEnd = endEl && endEl.value ? endEl.value : '';
    return { report_month: reportMonth, period_start: periodStart, period_end: periodEnd };
}

function categoryOptions(selected) {
    const categories = Array.isArray(window.SPENDING_CATEGORIES) ? window.SPENDING_CATEGORIES : [];
    return categories
        .filter((c) => c && c !== 'unclassified')
        .map((c) => `<option value="${c}" ${c === selected ? 'selected' : ''}>${c}</option>`)
        .join('');
}

function insightTxCompare(a, b, key) {
    switch (key) {
        case 'date':
            return String(a.date || '').localeCompare(String(b.date || ''));
        case 'description':
            return String(a.description || '')
                .toLowerCase()
                .localeCompare(String(b.description || '').toLowerCase(), undefined, { sensitivity: 'base' });
        case 'source':
            return String(a.bank_source || '')
                .toLowerCase()
                .localeCompare(String(b.bank_source || '').toLowerCase(), undefined, { sensitivity: 'base' });
        case 'direction':
            return String(a.direction || '').localeCompare(String(b.direction || ''));
        case 'amount':
            return (Number(a.amount) || 0) - (Number(b.amount) || 0);
        case 'transfer': {
            const pa = a.internal_transfer && a.transfer_pair_id ? 1 : 0;
            const pb = b.internal_transfer && b.transfer_pair_id ? 1 : 0;
            return pa - pb;
        }
        case 'category':
            return String(a.category || '')
                .toLowerCase()
                .localeCompare(String(b.category || '').toLowerCase(), undefined, { sensitivity: 'base' });
        default:
            return 0;
    }
}

function sortInsightTransactions(rows, key, dir) {
    const m = dir === 'desc' ? -1 : 1;
    return [...rows].sort((a, b) => {
        const c = insightTxCompare(a, b, key);
        if (c !== 0) return m * c;
        return String(a.id || '').localeCompare(String(b.id || ''));
    });
}

function readInsightTransactionFilters() {
    const g = (id) => (document.getElementById(id) && document.getElementById(id).value) || '';
    return {
        date: g('insight-tx-filter-date'),
        description: g('insight-tx-filter-description'),
        source: g('insight-tx-filter-source'),
        direction: g('insight-tx-filter-direction'),
        amount: g('insight-tx-filter-amount'),
        transfer: g('insight-tx-filter-transfer'),
        category: g('insight-tx-filter-category'),
        insights: g('insight-tx-filter-insights'),
    };
}

function insightTransactionFiltersAreActive(f) {
    if (!f) return false;
    return !!(
        (f.date && f.date.trim()) ||
        (f.description && f.description.trim()) ||
        (f.amount && f.amount.trim()) ||
        f.source ||
        f.direction ||
        f.transfer ||
        f.category ||
        f.insights
    );
}

function applyInsightTransactionFilters(rows, f) {
    const qd = (f.date || '').trim().toLowerCase();
    const qdesc = (f.description || '').trim().toLowerCase();
    const qamt = (f.amount || '').trim();
    return (rows || []).filter((tx) => {
        if (qd && !String(tx.date || '').toLowerCase().includes(qd)) {
            return false;
        }
        if (qdesc && !String(tx.description || '').toLowerCase().includes(qdesc)) {
            return false;
        }
        if (f.source) {
            const src = String(tx.bank_source || '').trim();
            if (f.source === '__none__') {
                if (src) return false;
            } else if (src.toLowerCase() !== f.source.toLowerCase()) {
                return false;
            }
        }
        if (f.direction && String(tx.direction || '') !== f.direction) {
            return false;
        }
        if (qamt) {
            const n = Number(tx.amount);
            if (!Number.isFinite(n)) {
                return false;
            }
            const fs = n.toFixed(2);
            if (!fs.includes(qamt) && !String(tx.amount).includes(qamt)) {
                return false;
            }
        }
        if (f.transfer) {
            const p = tx.internal_transfer && tx.transfer_pair_id;
            if (f.transfer === 'paired' && !p) {
                return false;
            }
            if (f.transfer === 'unpaired' && p) {
                return false;
            }
        }
        if (f.category) {
            const c = String(tx.category || 'unclassified');
            if (c.toLowerCase() !== f.category.toLowerCase()) {
                return false;
            }
        }
        if (f.insights) {
            const isPaired = Boolean(tx.internal_transfer && tx.transfer_pair_id);
            const userExcl = tx.insights_excluded === true;
            if (f.insights === 'included' && (isPaired || userExcl)) {
                return false;
            }
            if (f.insights === 'excluded' && (isPaired || !userExcl)) {
                return false;
            }
            if (f.insights === 'paired' && !isPaired) {
                return false;
            }
        }
        return true;
    });
}

function populateInsightTxCategoryFilter(transactions) {
    const sel = document.getElementById('insight-tx-filter-category');
    if (!sel) return;
    const prev = sel.value;
    const categories = Array.isArray(window.SPENDING_CATEGORIES) ? window.SPENDING_CATEGORIES : [];
    const byLower = new Map();
    const addCat = (c) => {
        const raw = c != null && String(c).trim() ? String(c).trim() : 'unclassified';
        if (raw.toLowerCase() === 'unclassified') {
            return;
        }
        const low = raw.toLowerCase();
        if (!byLower.has(low)) {
            byLower.set(low, raw);
        }
    };
    categories.forEach((c) => addCat(c));
    if (Array.isArray(transactions)) {
        transactions.forEach((tx) => addCat(tx && tx.category));
    }
    const rest = Array.from(byLower.values());
    rest.sort((a, b) => String(a).toLowerCase().localeCompare(String(b).toLowerCase()));
    sel.innerHTML = '<option value="">All</option>';
    const added = new Set();
    rest.forEach((c) => {
        const o = document.createElement('option');
        o.value = c;
        o.textContent = c;
        sel.appendChild(o);
        added.add(String(c).toLowerCase());
    });
    if (!added.has('unclassified')) {
        const o = document.createElement('option');
        o.value = 'unclassified';
        o.textContent = 'unclassified';
        sel.appendChild(o);
    }
    if (prev && Array.from(sel.options).some((op) => op.value === prev)) {
        sel.value = prev;
    } else {
        sel.value = '';
    }
}

function populateInsightTxSourceFilter(transactions) {
    const sel = document.getElementById('insight-tx-filter-source');
    if (!sel) return;
    const prev = sel.value;
    const byLower = new Map();
    let hasBlank = false;
    (transactions || []).forEach((tx) => {
        const raw = tx && tx.bank_source != null ? String(tx.bank_source).trim() : '';
        if (!raw) {
            hasBlank = true;
            return;
        }
        const low = raw.toLowerCase();
        if (!byLower.has(low)) {
            byLower.set(low, raw);
        }
    });
    const labels = Array.from(byLower.values());
    labels.sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
    sel.innerHTML = '<option value="">All</option>';
    labels.forEach((label) => {
        const o = document.createElement('option');
        o.value = label;
        o.textContent = label;
        sel.appendChild(o);
    });
    if (hasBlank) {
        const o = document.createElement('option');
        o.value = '__none__';
        o.textContent = '(none)';
        sel.appendChild(o);
    }
    if (prev && Array.from(sel.options).some((op) => op.value === prev)) {
        sel.value = prev;
    } else {
        sel.value = '';
    }
}

function clearInsightTransactionFilters() {
    [
        'insight-tx-filter-date',
        'insight-tx-filter-description',
        'insight-tx-filter-amount',
    ].forEach((id) => {
        const el = document.getElementById(id);
        if (el) {
            el.value = '';
        }
    });
    [
        'insight-tx-filter-source',
        'insight-tx-filter-direction',
        'insight-tx-filter-transfer',
        'insight-tx-filter-category',
        'insight-tx-filter-insights',
    ].forEach((id) => {
        const el = document.getElementById(id);
        if (el) {
            el.value = '';
        }
    });
}

function setInsightTxFilterStatus(filteredCount, totalCount) {
    const st = document.getElementById('insight-tx-filter-status');
    if (!st) {
        return;
    }
    const f = readInsightTransactionFilters();
    if (!insightTransactionFiltersAreActive(f)) {
        st.textContent = '';
        st.classList.add('hidden');
        return;
    }
    st.classList.remove('hidden');
    if (totalCount === 0) {
        st.textContent = '';
        st.classList.add('hidden');
        return;
    }
    if (filteredCount === 0) {
        st.textContent = 'No transactions match the current filters. Try clearing or relaxing them.';
        return;
    }
    if (filteredCount < totalCount) {
        st.textContent = `Showing ${filteredCount} of ${totalCount} transaction${
            totalCount === 1 ? '' : 's'
        } in this month.`;
        return;
    }
    st.textContent = `All ${totalCount} transaction${
        totalCount === 1 ? '' : 's'
    } match the filters.`;
}

function setInsightTxFilterRollup(filtered) {
    const el = document.getElementById('insight-tx-filter-rollup');
    if (!el) {
        return;
    }
    if (!filtered || filtered.length === 0) {
        el.textContent = '';
        el.removeAttribute('title');
        el.removeAttribute('aria-label');
        el.classList.add('hidden');
        return;
    }
    let incoming = 0;
    let outgoing = 0;
    filtered.forEach((tx) => {
        const raw = Number(tx.amount);
        const amt = Number.isFinite(raw) ? Math.abs(raw) : 0;
        const d = String(tx.direction || '').toLowerCase();
        if (d === 'incoming') {
            incoming += amt;
        } else {
            outgoing += amt;
        }
    });
    const net = incoming - outgoing;
    const netStr = formatMoney(net);
    el.textContent = netStr;
    el.setAttribute(
        'title',
        'Net of rows in view (incoming − outgoing). Updates when you change column filters.',
    );
    el.setAttribute('aria-label', `Net for rows in view: ${netStr}`);
    el.classList.remove('hidden');
}

function renderLargestOutgoing(items) {
    const el = document.getElementById('largest-outgoing-list');
    if (!el) return;
    el.innerHTML = '';
    if (!items || !items.length) {
        const li = document.createElement('li');
        li.className = 'text-gray-400';
        li.textContent = 'No outgoing spend this month';
        el.appendChild(li);
        return;
    }
    const maxAmt = Math.max(...items.map((x) => Number(x.amount) || 0), 1);
    items.forEach((row) => {
        const li = document.createElement('li');
        const amt = Number(row.amount) || 0;
        const pct = Math.min(100, Math.round((100 * amt) / maxAmt));
        li.innerHTML = `
            <div class="flex justify-between gap-2 text-gray-800">
                <span class="truncate pr-2" title="${escapeHtml(row.description)}">${escapeHtml(row.description)}</span>
                <span class="shrink-0 font-medium">${formatMoney(amt)}</span>
            </div>
            <div class="text-xs text-gray-500 mt-0.5">${escapeHtml(row.date || '')} · ${escapeHtml(String(row.category || ''))}</div>
            <div class="mt-1 h-2 bg-gray-100 rounded overflow-hidden">
                <div class="h-full bg-red-400 rounded" style="width: ${pct}%"></div>
            </div>
        `;
        el.appendChild(li);
    });
}

function renderCategoryBreakdown(items) {
    const el = document.getElementById('category-breakdown-list');
    if (!el) return;
    el.innerHTML = '';
    if (!items || !items.length) {
        const li = document.createElement('li');
        li.className = 'text-gray-400';
        li.textContent = '—';
        el.appendChild(li);
        return;
    }
    items.forEach((row) => {
        const li = document.createElement('li');
        const pct = Math.min(100, Math.max(0, Number(row.pct_of_outgoing) || 0));
        li.innerHTML = `
            <div class="flex justify-between gap-2">
                <span class="capitalize text-gray-800">${escapeHtml(String(row.category))}</span>
                <span class="text-gray-600">${formatMoney(row.amount)} (${pct}%)</span>
            </div>
            <div class="mt-1 h-2 bg-gray-100 rounded overflow-hidden">
                <div class="h-full bg-indigo-400 rounded" style="width: ${pct}%"></div>
            </div>
        `;
        el.appendChild(li);
    });
}

/** Plain-language blurb for month-to-month change (subscription signals). */
function subscriptionTrendPhrase(trend) {
    switch (trend) {
        case 'up':
            return 'A bit more than the previous month you were charged';
        case 'down':
            return 'A bit less than the previous month you were charged';
        case 'flat':
            return 'About the same as the previous month you were charged';
        case 'insufficient_history':
            return null;
        default:
            return null;
    }
}

/** Human-readable note for how much the amount jumps between months (ratio 0–1). */
function subscriptionVariabilityPhrase(ratio) {
    const v = Number(ratio);
    if (!Number.isFinite(v) || v <= 0) {
        return 'The amount has looked consistent so far';
    }
    const pct = Math.round(v * 100);
    if (pct <= 8) {
        return 'The amount is usually about the same each month';
    }
    if (pct <= 18) {
        return 'The amount shifts a little from month to month';
    }
    return 'The amount can vary from month to month, but the charge still shows up regularly';
}

/** How many subscription-signal cards show before the user expands the section. */
const SUBSCRIPTION_SIGNALS_VISIBLE_DEFAULT = 5;

/**
 * @returns {HTMLDivElement}
 */
function createSubscriptionSignalCard(row) {
    const card = document.createElement('div');
    card.className = 'border border-gray-200 rounded-lg p-3 bg-gray-50/80 shadow-sm';
    const windowMonths = 6;
    const title = escapeHtml(String(row.display_description || '—'));
    const amt = row.total_last_month != null ? row.total_last_month : row.amount_last_month;
    const monthsInWindow = row.months_in_window != null ? row.months_in_window : row.months_active;
    const mCount = Number(monthsInWindow) || 0;
    const avg = row.amount_avg_active_months;
    const streak = Number(row.consecutive_streak) || 0;
    const trendLine = subscriptionTrendPhrase(row.trend || '');
    const varyLine = subscriptionVariabilityPhrase(row.amount_variability);

    const streakLine =
        streak >= 2
            ? ` At one point this ran for <span class="text-gray-700 font-medium">${streak} month${
                  streak === 1 ? '' : 's'
              }</span> in a row.`
            : '';

    const inner = [
        `<div class="font-medium text-gray-900 mb-1">${title}</div>`,
        `<div class="text-gray-800">This month: <span class="font-semibold tabular-nums">${formatMoney(amt)}</span></div>`,
        `<div class="text-gray-600 mt-1.5 leading-relaxed">In <span class="text-gray-800 font-medium">${mCount}</span> of the last ${windowMonths} months you had a charge from this name. On months when it appears, you typically spend about <span class="text-gray-800 font-medium tabular-nums">${formatMoney(avg)}</span>.${streakLine}</div>`,
    ];
    if (trendLine) {
        inner.push(`<div class="text-gray-600 mt-1.5">${trendLine}.</div>`);
    } else if (row.trend === 'insufficient_history') {
        inner.push(
            '<div class="text-gray-500 text-xs mt-1">Not enough earlier charges in this list to say if it went up or down from last time.</div>',
        );
    }
    inner.push(`<div class="text-gray-500 text-xs mt-2">${varyLine}.</div>`);
    card.innerHTML = inner.join('');
    return card;
}

function setSubscriptionSignalsToggleUI(expanded) {
    const wrap = document.getElementById('subscription-signals-toggle-wrap');
    const btn = document.getElementById('subscription-signals-toggle-btn');
    if (!wrap || !btn) return;
    const total = Number(btn.dataset.total) || 0;
    const initial = Number(btn.dataset.initial) || SUBSCRIPTION_SIGNALS_VISIBLE_DEFAULT;
    const extra = total - initial;
    if (total <= initial || extra <= 0) {
        wrap.classList.add('hidden');
        btn.textContent = '';
        return;
    }
    wrap.classList.remove('hidden');
    btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    if (expanded) {
        btn.textContent = 'Show fewer';
    } else {
        btn.textContent = `Show all ${total} matches (${extra} more)`;
    }
}

function updateSubscriptionSignalsExpanded(expanded) {
    document.querySelectorAll('.subscription-signal-card-extra').forEach((node) => {
        node.classList.toggle('hidden', !expanded);
    });
    setSubscriptionSignalsToggleUI(expanded);
}

function renderSubscriptionSignals(items) {
    const el = document.getElementById('subscription-signals-list');
    if (!el) return;
    el.innerHTML = '';
    if (!items || !items.length) {
        const p = document.createElement('p');
        p.className = 'text-gray-500 text-sm';
        p.textContent =
            'Nothing to show here yet. After you have spending in a few different months, we can pick out names that keep turning up on your statements.';
        el.appendChild(p);
        const wrap = document.getElementById('subscription-signals-toggle-wrap');
        if (wrap) wrap.classList.add('hidden');
        return;
    }
    const initial = SUBSCRIPTION_SIGNALS_VISIBLE_DEFAULT;
    items.forEach((row, i) => {
        const card = createSubscriptionSignalCard(row);
        if (i >= initial) {
            card.classList.add('subscription-signal-card-extra', 'hidden');
        }
        el.appendChild(card);
    });
    const btn = document.getElementById('subscription-signals-toggle-btn');
    if (btn) {
        btn.dataset.total = String(items.length);
        btn.dataset.initial = String(Math.min(initial, items.length));
    }
    if (items.length > initial) {
        updateSubscriptionSignalsExpanded(false);
    } else {
        const wrap = document.getElementById('subscription-signals-toggle-wrap');
        if (wrap) wrap.classList.add('hidden');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const reportPeriodMonth = document.getElementById('report-period-month');
    const periodResetBtn = document.getElementById('period-reset-btn');
    const fileInput = document.getElementById('spending-file');
    const previewBtn = document.getElementById('spending-preview-btn');
    const importBtn = document.getElementById('spending-import-btn');
    const clearBtn = document.getElementById('spending-clear-btn');
    const previewWrap = document.getElementById('spending-preview-wrap');
    const previewTbody = document.getElementById('spending-preview-tbody');
    const previewIncludeAll = document.getElementById('preview-include-all');
    const previewSummary = document.getElementById('spending-preview-summary');
    const monthSelect = document.getElementById('insight-month-select');
    const recategorizeMonthBtn = document.getElementById('recategorize-month-btn');
    const recomputeInsightsBtn = document.getElementById('recompute-insights-btn');
    const deleteMonthBtn = document.getElementById('delete-month-btn');
    const insightEmpty = document.getElementById('insight-empty');
    const insightContent = document.getElementById('insight-content');

    let insightTxSortState = { key: 'date', dir: 'asc' };
    let insightTransactionsSnapshot = [];
    /** Set of existing ledger fingerprint strings (same import scheme); used when reconciling dups after direction edit. */
    let spendingPreviewLedgerFps = null;
    let spendingMetricsChart = null;
    let spendingCategoryStackChart = null;
    /** @type {string|null} */
    let spendingChartSelectedMonth = null;
    let savingsCoachExpanded = false;
    /** @type {string|null} */
    let savingsCoachResultFor = null;
    /** @type {object|null} */
    let lastSavingsAdviceData = null;
    const CHART_STACK_COLORS = [
        '#0d7a62', '#065f4c', '#2a9d8f', '#5c6f6a', '#c2410c', '#1e3a8a',
        '#0e7490', '#3f6212', '#b45309', '#4f46e5', '#9d174d', '#64748b', '#0f1c1a', '#14b8a6',
    ];

    function labelForStackCategoryKey(key) {
        const s = String(key || 'unknown');
        return s.length ? s.charAt(0).toUpperCase() + s.slice(1) : s;
    }

    if (typeof Chart !== 'undefined' && !window.__spendingMonthLinePlugin) {
        window.__spendingMonthLinePlugin = {
            id: 'spendingSelectedMonthLine',
            afterDatasetsDraw(chart) {
                const idx = chart.config._spendingSelectedIdx;
                if (idx == null || idx < 0) return;
                if (!chart.chartArea) return;
                const xScale = chart.scales.x;
                if (!xScale) return;
                const labels = chart.data.labels || [];
                if (idx >= labels.length) return;
                let x;
                try {
                    x = xScale.getPixelForValue(labels[idx]);
                } catch (e) {
                    return;
                }
                const { top, bottom } = chart.chartArea;
                const ctx = chart.ctx;
                ctx.save();
                ctx.beginPath();
                ctx.strokeStyle = 'rgba(71, 85, 105, 0.45)';
                ctx.lineWidth = 1;
                ctx.setLineDash([5, 5]);
                ctx.moveTo(x, top);
                ctx.lineTo(x, bottom);
                ctx.stroke();
                ctx.restore();
            },
        };
        Chart.register(window.__spendingMonthLinePlugin);
    }

    const MIN_CATEGORY_SPEND_FLOOR = 0.01;

    /**
     * Category keys with real spend in at least one month, ordered by total
     * across the time range. Ignores zero amounts so unused labels never get a band.
     */
    function spendingAllCategoryKeysByTotal(points) {
        const sums = {};
        (points || []).forEach((p) => {
            const ob = p.outgoing_by_category || {};
            Object.keys(ob).forEach((k) => {
                const v = Number(ob[k]);
                if (!Number.isFinite(v) || v < MIN_CATEGORY_SPEND_FLOOR) {
                    return;
                }
                sums[k] = (sums[k] || 0) + v;
            });
        });
        return Object.keys(sums)
            .filter((k) => (sums[k] || 0) >= MIN_CATEGORY_SPEND_FLOOR)
            .sort((a, b) => sums[b] - sums[a]);
    }

    function bindChartToolbar() {
        const apply = () => {
            if (!spendingMetricsChart) return;
            const d = spendingMetricsChart.data.datasets;
            const inc = document.getElementById('chart-ds-income');
            const out = document.getElementById('chart-ds-outgoing');
            const nt = document.getElementById('chart-ds-net');
            if (d[0]) d[0].hidden = !(inc && inc.checked);
            if (d[1]) d[1].hidden = !(out && out.checked);
            if (d[2]) d[2].hidden = !(nt && nt.checked);
            spendingMetricsChart.update();
        };
        ['chart-ds-income', 'chart-ds-outgoing', 'chart-ds-net'].forEach((id) => {
            document.getElementById(id)?.addEventListener('change', apply);
        });
    }
    bindChartToolbar();

    function setCategoryStackDatasetVisible(chart, index, visible) {
        if (!chart) return;
        if (typeof chart.setDatasetVisibility === 'function') {
            chart.setDatasetVisibility(index, visible);
        } else {
            chart.getDatasetMeta(index).hidden = !visible;
        }
    }

    function hideSpendingCategoryCustomLegend() {
        const wrap = document.getElementById('spending-category-legend-wrap');
        const cbs = document.getElementById('spending-category-legend-cbs');
        if (wrap) wrap.classList.add('hidden');
        if (cbs) cbs.innerHTML = '';
    }

    function rebuildSpendingCategoryStackLegend() {
        const chart = spendingCategoryStackChart;
        const wrap = document.getElementById('spending-category-legend-wrap');
        const cbs = document.getElementById('spending-category-legend-cbs');
        if (!cbs) return;
        cbs.innerHTML = '';
        if (!chart || !wrap) {
            if (wrap) wrap.classList.add('hidden');
            return;
        }
        wrap.classList.remove('hidden');
        chart.data.datasets.forEach((ds, i) => {
            const meta = chart.getDatasetMeta(i);
            const visible = meta.hidden !== true;
            const id = `spending-cat-legend-cb-${i}`;
            const label = document.createElement('label');
            label.className = 'spending-category-legend-item';
            const input = document.createElement('input');
            input.type = 'checkbox';
            input.className = 'category-stack-legend-cb';
            input.dataset.datasetIndex = String(i);
            input.id = id;
            input.checked = visible;
            input.setAttribute('aria-label', `Show “${(ds.label || 'category').replace(/"/g, '')}” in chart`);
            const sw = document.createElement('span');
            sw.className = 'spending-category-legend-swatch';
            const col = typeof ds.backgroundColor === 'string' ? ds.backgroundColor : '#64748b';
            sw.style.setProperty('--swatch', col);
            const cap = document.createElement('span');
            cap.textContent = ds.label || '';
            label.appendChild(input);
            label.appendChild(sw);
            label.appendChild(cap);
            cbs.appendChild(label);
        });
    }

    async function refreshSpendingMetricsChart(selectedMonth) {
        const wrap = document.getElementById('spending-metrics-chart-wrap');
        const stackBlock = document.getElementById('spending-category-stack-block');
        const stackWrap = document.getElementById('spending-category-chart-wrap');
        const canvas = document.getElementById('spending-metrics-chart');
        const stackCanvas = document.getElementById('spending-category-stack-chart');
        const sr = document.getElementById('spending-chart-sr');
        if (!wrap || !canvas || typeof Chart === 'undefined') return;
        spendingChartSelectedMonth = selectedMonth && String(selectedMonth).length >= 7 ? String(selectedMonth).slice(0, 7) : null;
        try {
            const response = await fetch('/api/spending/metrics-trend');
            const data = await response.json().catch(() => ({}));
            if (!response.ok) return;
            const points = data.points || [];
            if (points.length === 0) {
                wrap.classList.add('hidden');
                if (stackBlock) stackBlock.classList.add('hidden');
                if (stackWrap) stackWrap.classList.add('hidden');
                if (spendingMetricsChart) {
                    spendingMetricsChart.destroy();
                    spendingMetricsChart = null;
                }
                if (spendingCategoryStackChart) {
                    spendingCategoryStackChart.destroy();
                    spendingCategoryStackChart = null;
                }
                hideSpendingCategoryCustomLegend();
                if (sr) sr.innerHTML = '';
                return;
            }
            wrap.classList.remove('hidden');
            const labels = points.map((p) => p.month);
            const income = points.map((p) => p.income);
            const outgo = points.map((p) => p.outgoing);
            const net = points.map((p) => p.net);
            let selIdx = -1;
            if (spendingChartSelectedMonth) {
                const j = labels.indexOf(spendingChartSelectedMonth);
                selIdx = j >= 0 ? j : -1;
            } else if (labels.length) {
                selIdx = labels.length - 1;
            }

            if (spendingMetricsChart) {
                spendingMetricsChart.destroy();
                spendingMetricsChart = null;
            }
            const ctx = canvas.getContext('2d');
            spendingMetricsChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels,
                    datasets: [
                        {
                            label: 'Income',
                            data: income,
                            borderColor: '#15803d',
                            backgroundColor: 'rgba(21, 128, 45, 0.12)',
                            borderWidth: 2,
                            fill: false,
                            tension: 0.2,
                            pointRadius: 3,
                            hidden: !document.getElementById('chart-ds-income')?.checked,
                        },
                        {
                            label: 'Outgoing',
                            data: outgo,
                            borderColor: '#b91c1c',
                            backgroundColor: 'rgba(185, 28, 28, 0.12)',
                            borderWidth: 2,
                            fill: false,
                            tension: 0.2,
                            pointRadius: 3,
                            hidden: !document.getElementById('chart-ds-outgoing')?.checked,
                        },
                        {
                            label: 'Net',
                            data: net,
                            borderColor: '#0d7a62',
                            backgroundColor: 'rgba(13, 122, 98, 0.12)',
                            borderWidth: 2,
                            fill: false,
                            tension: 0.2,
                            pointRadius: 3,
                            hidden: !document.getElementById('chart-ds-net')?.checked,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { position: 'top', labels: { usePointStyle: true, boxWidth: 8 } },
                        tooltip: {
                            callbacks: {
                                label: (c) => {
                                    const v = c.parsed.y;
                                    if (v == null || !Number.isFinite(Number(v))) {
                                        return `${c.dataset.label}:`;
                                    }
                                    let line = `${c.dataset.label}: ${formatMoney(v)}`;
                                    const i = c.dataIndex;
                                    const ds = c.dataset.data;
                                    if (
                                        i > 0
                                        && (c.dataset.label === 'Outgoing' || c.dataset.label === 'Net')
                                        && Array.isArray(ds)
                                    ) {
                                        const prev = Number(ds[i - 1]);
                                        if (Number.isFinite(prev) && Math.abs(prev) > 1e-6) {
                                            const pc = ((Number(v) - prev) / Math.abs(prev)) * 100.0;
                                            line += ` (${pc >= 0 ? '+' : ''}${pc.toFixed(1)}% vs prior month)`;
                                        }
                                    }
                                    return line;
                                },
                            },
                        },
                    },
                    scales: {
                        x: {
                            title: { display: true, text: 'Month' },
                            grid: { display: false },
                        },
                        y: {
                            beginAtZero: false,
                            grace: '8%',
                            ticks: {
                                callback: (v) => {
                                    if (typeof v !== 'number' && typeof v !== 'string') return '';
                                    return `£${Number(v).toLocaleString('en-GB', { maximumFractionDigits: 0 })}`;
                                },
                            },
                        },
                    },
                },
            });
            spendingMetricsChart.config._spendingSelectedIdx = selIdx;

            if (spendingCategoryStackChart) {
                spendingCategoryStackChart.destroy();
                spendingCategoryStackChart = null;
            }
            const categoryKeys = stackCanvas ? spendingAllCategoryKeysByTotal(points) : [];
            if (!categoryKeys.length) {
                if (stackBlock) stackBlock.classList.add('hidden');
                if (stackWrap) stackWrap.classList.add('hidden');
                hideSpendingCategoryCustomLegend();
            } else if (stackCanvas) {
                if (stackBlock) stackBlock.classList.remove('hidden');
                if (stackWrap) stackWrap.classList.remove('hidden');
                const sctx = stackCanvas.getContext('2d');
                const datasets = categoryKeys.map((key, di) => ({
                    label: labelForStackCategoryKey(key),
                    data: points.map((p) => {
                        const ob = p.outgoing_by_category || {};
                        return Number(ob[key]) || 0;
                    }),
                    backgroundColor: CHART_STACK_COLORS[di % CHART_STACK_COLORS.length],
                    stack: 'out',
                }));
                spendingCategoryStackChart = new Chart(sctx, {
                    type: 'bar',
                    data: { labels, datasets },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { mode: 'index', intersect: false },
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                filter: (item) => {
                                    const y = item.parsed && item.parsed.y;
                                    return y != null && Number(y) > 0.01;
                                },
                                callbacks: {
                                    label: (c) => {
                                        const v = c.parsed.y;
                                        if (v == null || !Number.isFinite(Number(v)) || v <= 0) {
                                            return null;
                                        }
                                        return `${c.dataset.label}: ${formatMoney(v)}`;
                                    },
                                },
                            },
                        },
                        scales: {
                            x: { stacked: true, grid: { display: false } },
                            y: {
                                stacked: true,
                                beginAtZero: true,
                                ticks: {
                                    callback: (v) => {
                                        if (typeof v !== 'number' && typeof v !== 'string') return '';
                                        return `£${Number(v).toLocaleString('en-GB', { maximumFractionDigits: 0 })}`;
                                    },
                                },
                            },
                        },
                    },
                });
                spendingCategoryStackChart.config._spendingSelectedIdx = selIdx;
                rebuildSpendingCategoryStackLegend();
            }

            if (sr) {
                const rows = points
                    .map(
                        (p) =>
                            `<tr><td>${escapeHtml(p.month)}</td><td class="text-right">${formatMoney(
                                p.income,
                            )}</td><td class="text-right">${formatMoney(p.outgoing)}</td><td class="text-right">${formatMoney(
                                p.net,
                            )}</td></tr>`,
                    )
                    .join('');
                sr.innerHTML = `<table class="w-full text-xs" style="border-collapse:collapse"><caption class="text-left">Trend data (same as chart)</caption><thead><tr><th scope="col">Month</th><th scope="col" class="text-right">Income</th><th scope="col" class="text-right">Outgoing</th><th scope="col" class="text-right">Net</th></tr></thead><tbody>${rows}</tbody></table>`;
            }
            const aria = document.getElementById('spending-line-chart-aria');
            if (aria) {
                const last = points[points.length - 1];
                aria.textContent = `Chart data: ${
                    points.length
                } month(s). Latest ${last.month}: income ${formatMoney(last.income)}, outgoing ${formatMoney(
                    last.outgoing,
                )}, net ${formatMoney(last.net)}.`;
            }
        } catch (e) {
            /* keep prior chart if any */
        }
    }

    function spendingPreviewMatchCell(isDup, dupReason, reviewReason) {
        if (isDup) {
            let label = 'Same as row in file';
            if (dupReason === 'ledger') label = 'Already in ledger';
            else if (dupReason === 'manual') label = 'Matches manual entry';
            return `<span class="preview-duplicate-pill" data-reason="${escapeHtml(dupReason)}">${escapeHtml(label)}</span>`;
        }
        if (reviewReason === 'missed') {
            return '<span class="preview-review-pill" data-reason="missed" title="On the statement but not in your manual spends — review">Not in manual</span>';
        }
        if (reviewReason === 'expected_bill') {
            return '<span class="preview-review-pill" data-reason="expected_bill" title="Matches an expected monthly bill">Expected bill</span>';
        }
        return '<span class="preview-match-empty text-gray-300">—</span>';
    }

    function formatPreviewShortDate(iso) {
        const raw = String(iso || '').trim().slice(0, 10);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) return '';
        const d = new Date(`${raw}T12:00:00`);
        if (Number.isNaN(d.getTime())) return '';
        return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
    }

    function collapsePreviewRows() {
        if (!previewTbody) return;
        previewTbody.querySelectorAll('tr.preview-tx-row--open').forEach((row) => {
            row.classList.remove('preview-tx-row--open');
            row.setAttribute('aria-expanded', 'false');
        });
    }

    function togglePreviewRow(row) {
        if (!row) return;
        const willOpen = !row.classList.contains('preview-tx-row--open');
        collapsePreviewRows();
        row.classList.toggle('preview-tx-row--open', willOpen);
        row.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    }

    function recomputeSpendingPreviewDuplicates() {
        if (!previewTbody) return;
        const period = getSpendingPeriodPayload();
        const rm = String(period.report_month || '')
            .trim()
            .slice(0, 7);
        if (rm.length !== 7) return;
        const ledger = spendingPreviewLedgerFps || new Set();
        const seen = new Set();
        previewTbody.querySelectorAll('tr').forEach((tr) => {
            const dirEl = tr.querySelector('.preview-direction');
            const direction = (dirEl && dirEl.value) || tr.dataset.direction || 'outgoing';
            tr.dataset.direction = direction;
            const dStr = tr.dataset.date || '';
            const amount = parseFloat(tr.dataset.amount || '0');
            const desc = tr.dataset.description || '';
            const fp = spendingFingerprintForPreview(rm, dStr, amount, direction, desc);
            const origDir = tr.dataset.origDirection || direction;
            const serverDupReason = tr.dataset.serverDupReason || '';
            const serverReview = tr.dataset.serverReviewReason || '';
            let isDup = false;
            let reason = '';
            let reviewReason = '';
            if (ledger.has(fp)) {
                isDup = true;
                reason = 'ledger';
            } else if (seen.has(fp)) {
                isDup = true;
                reason = 'upload';
            } else if (direction === origDir && serverDupReason === 'manual') {
                // Client cannot re-run fuzzy manual match; preserve server mark when direction unchanged.
                isDup = true;
                reason = 'manual';
                seen.add(fp);
            } else {
                seen.add(fp);
                if (direction === origDir) {
                    reviewReason = serverReview;
                } else if (direction === 'outgoing') {
                    reviewReason = 'missed';
                }
            }
            tr.classList.toggle('spending-preview-row-duplicate', isDup);
            tr.classList.toggle('spending-preview-row-missed', !isDup && reviewReason === 'missed');
            const badgeCell = tr.querySelector('.preview-duplicate-cell');
            if (badgeCell) {
                badgeCell.innerHTML = spendingPreviewMatchCell(isDup, reason, reviewReason);
            }
            const include = tr.querySelector('.preview-include');
            if (include) {
                if (isDup) include.checked = false;
                else include.checked = true;
            }
        });
        syncPreviewIncludeAll();
    }

    function syncPreviewIncludeAll() {
        if (!previewIncludeAll || !previewTbody) return;
        const boxes = [...previewTbody.querySelectorAll('.preview-include')];
        if (!boxes.length) {
            previewIncludeAll.checked = false;
            previewIncludeAll.indeterminate = false;
            return;
        }
        const n = boxes.filter((c) => c.checked).length;
        previewIncludeAll.checked = n === boxes.length;
        previewIncludeAll.indeterminate = n > 0 && n < boxes.length;
    }

    function updateInsightSortIndicators() {
        document.querySelectorAll('.insight-sort-btn').forEach((btn) => {
            const key = btn.getAttribute('data-sort-key');
            const span = btn.querySelector('.sort-ind');
            const active = insightTxSortState.key === key;
            if (active) {
                btn.setAttribute('aria-pressed', 'true');
                if (span) span.textContent = insightTxSortState.dir === 'asc' ? '↑' : '↓';
            } else {
                btn.setAttribute('aria-pressed', 'false');
                if (span) span.textContent = '';
            }
        });
    }

    function renderInsightTransactionsBody() {
        const tbody = document.getElementById('insight-transactions-tbody');
        if (!tbody) return;
        const totalCount = insightTransactionsSnapshot.length;
        const f = readInsightTransactionFilters();
        const filtered = applyInsightTransactionFilters(insightTransactionsSnapshot, f);
        setInsightTxFilterStatus(filtered.length, totalCount);
        setInsightTxFilterRollup(filtered);
        const sorted = sortInsightTransactions(filtered, insightTxSortState.key, insightTxSortState.dir);
        tbody.innerHTML = '';
        if (totalCount > 0 && sorted.length === 0) {
            const tr = document.createElement('tr');
            tr.innerHTML = `<td colspan="8" class="px-3 py-6 text-center text-sm text-gray-500">No transactions match the column filters.</td>`;
            tbody.appendChild(tr);
            return;
        }
        sorted.forEach((tx) => {
            const tr = document.createElement('tr');
            const canEdit = tx.direction === 'outgoing';
            const isPaired = tx.internal_transfer && tx.transfer_pair_id;
            const userExcl = tx.insights_excluded === true;
            if (isPaired || userExcl) {
                tr.classList.add('spending-insight-tx-muted');
            }
            const sourceLabel = String(tx.bank_source || '').trim();
            const sourceCell = sourceLabel
                ? `<td class="px-3 py-2 whitespace-nowrap">${escapeHtml(sourceLabel)}</td>`
                : '<td class="px-3 py-2 text-gray-400">—</td>';
            const insightsCol = isPaired
                ? '<span class="text-xs" title="Not counted in income, outgoing, or category totals">Not in totals (linked)</span>'
                : userExcl
                  ? `<button type="button" class="insight-tx-exclude-btn" data-tx-id="${tx.id}" data-want-exclude="false" title="Show in stats above">Include in totals</button>`
                  : `<button type="button" class="insight-tx-exclude-btn" data-tx-id="${tx.id}" data-want-exclude="true" title="Same as linked: omit from stats above">Exclude from totals</button>`;
            tr.innerHTML = `
                        <td class="px-3 py-2 whitespace-nowrap">${tx.date}</td>
                        <td class="px-3 py-2">${tx.description}</td>
                        ${sourceCell}
                        <td class="px-3 py-2 capitalize">${tx.direction}</td>
                        <td class="px-3 py-2 whitespace-nowrap spending-insight-tx-amount">${Number(tx.amount).toFixed(2)}</td>
                        <td class="px-3 py-2 text-sm align-top">
                            ${
                                isPaired
                                    ? `<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
                                        <span class="text-indigo-800 font-medium">Paired</span>
                                        <button type="button" class="unlink-pair inline-flex items-center border border-gray-300 rounded bg-white hover:bg-gray-50 text-gray-800 text-xs font-semibold py-1 px-2 shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-1" data-tx-id="${tx.id}">Unlink</button>
                                      </div>`
                                    : '<span class="text-gray-400">—</span>'
                            }
                        </td>
                        <td class="px-3 py-2">
                            ${canEdit
                                ? `<select data-tx-id="${tx.id}" class="reclassify-select border border-gray-300 rounded px-2 py-1 bg-white text-sm">${categoryOptions(tx.category || 'other')}</select>`
                                : '<span class="text-gray-400">—</span>'}
                        </td>
                        <td class="px-3 py-2 text-sm align-top whitespace-nowrap">${insightsCol}</td>
                    `;
            tbody.appendChild(tr);
        });
        bindReclassifyHandlers();
        bindUnlinkPairHandlers();
        bindInsightExcludeHandlers();
    }

    function onInsightSortClick(key) {
        if (!key) return;
        if (insightTxSortState.key === key) {
            insightTxSortState.dir = insightTxSortState.dir === 'asc' ? 'desc' : 'asc';
        } else {
            insightTxSortState.key = key;
            insightTxSortState.dir = key === 'amount' || key === 'transfer' ? 'desc' : 'asc';
        }
        updateInsightSortIndicators();
        renderInsightTransactionsBody();
    }

    function clearPreview() {
        if (previewTbody) previewTbody.innerHTML = '';
        if (previewIncludeAll) {
            previewIncludeAll.checked = false;
            previewIncludeAll.indeterminate = false;
        }
        if (previewWrap) previewWrap.classList.add('hidden');
        if (previewSummary) {
            previewSummary.classList.add('hidden');
            previewSummary.textContent = '';
            previewSummary.innerHTML = '';
        }
        const pipePanel = document.getElementById('spending-pipeline-panel');
        if (pipePanel) {
            pipePanel.classList.add('hidden');
            pipePanel.open = false;
        }
        const pipeSum = document.getElementById('spending-pipeline-summary');
        if (pipeSum) pipeSum.textContent = '';
        const pipeNote = document.getElementById('spending-pipeline-note');
        if (pipeNote) {
            pipeNote.textContent = '';
            pipeNote.classList.add('hidden');
        }
        ['spending-pipeline-extract', 'spending-pipeline-hints', 'spending-pipeline-direction'].forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.textContent = '';
        });
        if (importBtn) importBtn.disabled = true;
        spendingPreviewLedgerFps = null;
        const homeNext = document.getElementById('home-import-next');
        if (homeNext) {
            homeNext.classList.add('hidden');
            homeNext.innerHTML = '';
        }
    }

    function fillPipelinePre(el, previewObj) {
        if (!el) return;
        if (!previewObj || (!previewObj.content && !(previewObj.total_length > 0))) {
            el.textContent = '—';
            return;
        }
        let t = previewObj.content || '';
        if (previewObj.preview_truncated) {
            t += `\n\n… Preview shortened (${previewObj.total_length.toLocaleString()} characters total).`;
        }
        el.textContent = t;
    }

    function renderPipeline(pipeline) {
        const panel = document.getElementById('spending-pipeline-panel');
        const summaryEl = document.getElementById('spending-pipeline-summary');
        const noteEl = document.getElementById('spending-pipeline-note');
        if (!panel || !summaryEl) return;
        if (!pipeline) {
            panel.classList.add('hidden');
            return;
        }
        panel.classList.remove('hidden');
        const fmt = pipeline.source_format || 'unknown';
        const lens = pipeline.lengths || {};
        const pdf = pipeline.pdf || {};
        const engines = Array.isArray(pdf.engines_considered) ? pdf.engines_considered : [];
        const engLine = engines.length
            ? engines.map((e) => `${e.name}: ${e.non_ws_chars.toLocaleString()} non-ws chars`).join('; ')
            : '';
        const trunc = pipeline.truncated_for_llm
            ? `Yes — only the first ${(pipeline.llm_char_limit || 0).toLocaleString()} characters were sent to the model.`
            : 'No';
        const parts = [
            `Format: ${fmt}.`,
            pdf.text_engine ? `PDF text extraction: ${pdf.text_engine}.` : null,
            engLine ? `Engines compared: ${engLine}.` : null,
            `Combined text length before model cap: ${(lens.combined_before_truncation ?? 0).toLocaleString()} chars.`,
            `Truncated for model: ${trunc}`,
            lens.sent_to_llm != null ? `Sent to model: ${lens.sent_to_llm.toLocaleString()} chars.` : null,
            lens.column_hints_block != null && lens.column_hints_block > 0
                ? `Layout hint block: ${lens.column_hints_block.toLocaleString()} chars.`
                : null,
            pipeline.direction_hints && pipeline.direction_hints.count != null
                ? `Direction hints derived: ${pipeline.direction_hints.count}.`
                : null,
        ].filter(Boolean);
        summaryEl.textContent = parts.join(' ');
        if (noteEl) {
            if (pipeline.note) {
                noteEl.textContent = pipeline.note;
                noteEl.classList.remove('hidden');
            } else {
                noteEl.textContent = '';
                noteEl.classList.add('hidden');
            }
        }
        fillPipelinePre(document.getElementById('spending-pipeline-extract'), pipeline.previews && pipeline.previews.extracted_text);
        fillPipelinePre(document.getElementById('spending-pipeline-hints'), pipeline.previews && pipeline.previews.column_hints_block);
        const dirPre = document.getElementById('spending-pipeline-direction');
        if (dirPre) {
            const sample = pipeline.direction_hints && pipeline.direction_hints.sample;
            const count = pipeline.direction_hints && pipeline.direction_hints.count;
            if (sample && sample.length) {
                const header =
                    count > sample.length
                        ? `Showing ${sample.length} of ${count} hint row(s).\n\n`
                        : `${sample.length} hint row(s).\n\n`;
                dirPre.textContent = header + JSON.stringify(sample, null, 2);
            } else {
                dirPre.textContent = '—';
            }
        }
    }

    function renderPreview(transactions, summary, pipeline) {
        if (!previewTbody || !previewWrap) return;
        const dupLedgerList = (summary && summary.duplicate_ledger_fingerprints) || [];
        spendingPreviewLedgerFps = new Set(Array.isArray(dupLedgerList) ? dupLedgerList : []);
        previewTbody.innerHTML = '';
        transactions.forEach((tx) => {
            const tr = document.createElement('tr');
            tr.className = 'preview-tx-row';
            tr.tabIndex = 0;
            tr.setAttribute('aria-expanded', 'false');
            tr.dataset.id = tx.id;
            tr.dataset.date = tx.date;
            tr.dataset.description = tx.description;
            tr.dataset.direction = tx.direction;
            tr.dataset.amount = String(tx.amount);
            tr.dataset.category = tx.category || '';
            tr.dataset.rationale = tx.rationale || '';
            tr.dataset.confidence = tx.confidence == null ? '' : String(tx.confidence);
            if (tx.started_date) tr.dataset.startedDate = tx.started_date;
            if (tx.completed_date) tr.dataset.completedDate = tx.completed_date;
            const isDup = tx.preview_duplicate === true;
            const isBoundary = tx.date_boundary === true;
            const reviewReason = (tx.preview_review_reason && String(tx.preview_review_reason)) || '';
            if (isDup) tr.classList.add('spending-preview-row-duplicate');
            if (!isDup && reviewReason === 'missed') tr.classList.add('spending-preview-row-missed');
            if (isBoundary) tr.classList.add('spending-preview-row-boundary');
            const dupReason = (tx.preview_duplicate_reason && String(tx.preview_duplicate_reason)) || '';
            tr.dataset.origDirection = String(tx.direction || 'outgoing');
            tr.dataset.serverDupReason = dupReason;
            tr.dataset.serverReviewReason = reviewReason;
            const dupCell = spendingPreviewMatchCell(isDup, dupReason, reviewReason);

            const startedLabel = tx.started_date ? String(tx.started_date) : '';
            const boundaryCell = isBoundary
                ? `<span class="preview-boundary-pill" title="Started outside the selected range; ledger date uses completed/settled date">Started ${escapeHtml(startedLabel || 'outside range')}</span>`
                : '';
            const dateInner = boundaryCell
                ? `<div class="preview-date-stack"><span class="preview-date-primary">${escapeHtml(String(tx.date || ''))}</span>${boundaryCell}</div>`
                : `<span class="preview-date-primary">${escapeHtml(String(tx.date || ''))}</span>`;
            const shortDate = formatPreviewShortDate(tx.date);
            const shortDateHtml = shortDate
                ? `<span class="preview-tx-date-short"> · ${escapeHtml(shortDate)}</span>`
                : '';

            const rec = tx.reconciliation || {};
            const transferCell =
                rec.paired === true
                    ? `<span class="text-indigo-700" title="After import, would pair with the other leg">Paired${
                          rec.peer_is_from_ledger ? ' <span class="text-gray-500">(to ledger)</span>' : ''
                      }${
                          rec.peer_description
                              ? `<br><span class="text-xs text-gray-600">${escapeHtml(rec.peer_description)}</span>`
                              : ''
                      }</span>`
                    : '<span class="text-gray-500">Unpaired</span>';
            // Duplicates stay unchecked; boundary rows are included by default.
            const includeChecked = !isDup;
            const amt = Number(tx.amount);
            const amtStr = Number.isFinite(amt) ? amt.toFixed(2) : escapeHtml(tx.amount);
            tr.innerHTML = `
                <td class="px-3 py-2 preview-tx-include">
                    <input type="checkbox" class="preview-include" ${includeChecked ? 'checked' : ''} aria-label="Include in import">
                </td>
                <td class="px-3 py-2 align-top preview-duplicate-cell preview-tx-match text-sm" data-label="Match">${dupCell}</td>
                <td class="px-3 py-2 align-top whitespace-nowrap preview-tx-date preview-tx-detail" data-label="Date">${dateInner}</td>
                <td class="px-3 py-2 preview-tx-desc" data-label="Description">
                    <span class="preview-tx-ref">${escapeHtml(String(tx.description || ''))}</span>${shortDateHtml}
                </td>
                <td class="px-3 py-2 whitespace-nowrap preview-tx-dir preview-tx-detail" data-label="Direction">
                    <select class="preview-direction border border-gray-300 rounded px-2 py-1 bg-white text-sm">
                        <option value="outgoing" ${tx.direction === 'outgoing' ? 'selected' : ''}>Outgoing</option>
                        <option value="incoming" ${tx.direction === 'incoming' ? 'selected' : ''}>Incoming</option>
                    </select>
                </td>
                <td class="px-3 py-2 whitespace-nowrap preview-tx-amount" data-label="Amount (£)">${amtStr}</td>
                <td class="px-3 py-2 text-sm align-top max-w-xs preview-tx-transfer preview-tx-detail" data-label="Transfer">${transferCell}</td>
                <td class="px-3 py-2 preview-category-cell preview-tx-cat preview-tx-detail" data-label="Category">
                    ${tx.direction === 'outgoing'
                        ? `<select class="preview-category border border-gray-300 rounded px-2 py-1 bg-white text-sm">${categoryOptions(tx.category || 'other')}</select>`
                        : '<span class="text-gray-400">—</span>'}
                </td>
                <td class="px-2 py-2 preview-tx-toggle">
                    <span class="preview-tx-chevron" aria-hidden="true"></span>
                    <span class="sr-only">Show details</span>
                </td>
            `;
            previewTbody.appendChild(tr);
        });
        previewTbody.querySelectorAll('tr').forEach((tr) => {
            const directionSelect = tr.querySelector('.preview-direction');
            if (!directionSelect) return;
            directionSelect.addEventListener('change', () => {
                const newDir = directionSelect.value;
                tr.dataset.direction = newDir;
                const cell = tr.querySelector('.preview-category-cell');
                if (cell) {
                    if (newDir === 'outgoing') {
                        const current = tr.dataset.category || 'other';
                        cell.innerHTML = `<select class="preview-category border border-gray-300 rounded px-2 py-1 bg-white text-sm">${categoryOptions(current)}</select>`;
                    } else {
                        cell.innerHTML = '<span class="text-gray-400">—</span>';
                    }
                }
                recomputeSpendingPreviewDuplicates();
            });
        });
        previewWrap.classList.remove('hidden');
        if (previewSummary && summary) {
            previewSummary.classList.remove('hidden');
            const metaBits = [];
            if (summary.report_month) metaBits.push(`Stats month: ${summary.report_month}`);
            if (summary.period_start && summary.period_end) {
                metaBits.push(`Range: ${summary.period_start} → ${summary.period_end}`);
            }
            if (summary.filtered_out_count != null && summary.filtered_out_count > 0) {
                metaBits.push(`Dropped outside range: ${summary.filtered_out_count}`);
            }
            if (summary.date_boundary_count != null && summary.date_boundary_count > 0) {
                metaBits.push(
                    `Boundary dates: ${summary.date_boundary_count} (started outside range; included — uncheck to skip)`
                );
            }
            if (summary.raw_extraction_count != null && summary.total_rows != null) {
                metaBits.push(`Kept ${summary.total_rows} / ${summary.raw_extraction_count} extracted`);
            }
            const rec = summary.direction_reconciliation;
            if (rec && rec.hints_seen) {
                metaBits.push(
                    `Layout-corrected directions: ${rec.overridden}/${rec.overridden + rec.confirmed} (${rec.unmatched_rows} rows unmatched)`
                );
            }
            const ex = summary.extraction;
            if (ex && ex.mode === 'layout_hints') {
                metaBits.push(
                    `Extraction: layout hints (${ex.raw_row_count ?? 0} raw row(s) from ${ex.hint_row_count ?? 0} hint line(s); compact filter model)`
                );
            } else if (ex && ex.mode === 'llm_full') {
                const r = ex.reason === 'layout_low_yield' ? 'layout parse thin; used full model' : 'used full model';
                metaBits.push(`Extraction: ${r}`);
            }
            const trP = summary.transfer_reconciliation_preview;
            if (trP && trP.reconciliation) {
                const trec = trP.reconciliation;
                const ledgerN = trP.ledger_row_count_in_month;
                let trLine = `Transfer match preview: ${trec.pair_count || 0} pair(s) in full-month simulation`;
                if (ledgerN > 0) {
                    trLine += ` (${ledgerN} row(s) already in ledger for this month + this file)`;
                } else {
                    trLine += ' (this file only)';
                }
                trLine += `, ${trP.auto_applied_pairs_in_simulation ?? 0} new auto-pair(s) in sim`;
                metaBits.push(trLine);
            }
            const flags = [];
            const dl = Number(summary.preview_duplicate_ledger) || 0;
            const du = Number(summary.preview_duplicate_upload) || 0;
            const missedN = Number(summary.preview_missed_manual) || 0;
            const billN = Number(summary.preview_expected_bill) || 0;
            if (dl > 0 || du > 0) {
                const parts = [];
                if (dl) parts.push(`matches ledger/manual: ${dl}`);
                if (du) parts.push(`repeated in file: ${du}`);
                flags.push({
                    kind: 'dup',
                    text: `Duplicates: ${parts.join(', ')} (unchecked; include if not a dupe)`,
                });
            }
            if (missedN > 0) {
                flags.push({
                    kind: 'missed',
                    text: `Not in manual: ${missedN} (highlighted — review missed spends)`,
                });
            }
            if (billN > 0) {
                flags.push({ kind: 'bill', text: `Expected bills: ${billN}` });
            }
            const totals = [
                { label: 'Rows', value: String(summary.total_rows ?? 0) },
                { label: 'In', value: formatMoney(summary.incoming_total) },
                { label: 'Out', value: formatMoney(summary.outgoing_total) },
                { label: 'Net', value: formatMoney(summary.net) },
            ];
            previewSummary.innerHTML = `
                <div class="preview-summary-totals">
                    ${totals
                        .map(
                            (t) =>
                                `<div class="preview-summary-stat"><span class="preview-summary-stat-label">${escapeHtml(t.label)}</span><span class="preview-summary-stat-value">${escapeHtml(t.value)}</span></div>`
                        )
                        .join('')}
                </div>
                ${
                    metaBits.length
                        ? `<p class="preview-summary-meta">${escapeHtml(metaBits.join(' · '))}</p>`
                        : ''
                }
                ${
                    flags.length
                        ? `<ul class="preview-summary-flags">${flags
                              .map(
                                  (f) =>
                                      `<li class="preview-summary-flag preview-summary-flag--${escapeHtml(f.kind)}">${escapeHtml(f.text)}</li>`
                              )
                              .join('')}</ul>`
                        : ''
                }
            `;
        }
        if (importBtn) importBtn.disabled = transactions.length === 0;
        syncPreviewIncludeAll();
        renderPipeline(pipeline);
    }

    async function loadMonths(targetMonth) {
        try {
            const response = await fetch('/api/spending/months');
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.error || 'Could not load months');
            const months = data.months || [];
            if (monthSelect) {
                monthSelect.innerHTML = '';
                if (!months.length) {
                    monthSelect.innerHTML = '<option value="">No data yet</option>';
                } else {
                    months.forEach((m) => {
                        const opt = document.createElement('option');
                        opt.value = m;
                        opt.textContent = m;
                        monthSelect.appendChild(opt);
                    });
                    monthSelect.value = targetMonth && months.includes(targetMonth) ? targetMonth : months[0];
                }
            }
            if (deleteMonthBtn) {
                deleteMonthBtn.disabled = !months.length || !monthSelect || !monthSelect.value;
            }
            if (recategorizeMonthBtn) {
                recategorizeMonthBtn.disabled = !months.length || !monthSelect || !monthSelect.value;
            }
            if (recomputeInsightsBtn) {
                recomputeInsightsBtn.disabled = !months.length;
            }
            if (monthSelect && monthSelect.value) {
                await loadInsight(monthSelect.value);
            } else {
                if (insightEmpty) insightEmpty.classList.remove('hidden');
                if (insightContent) insightContent.classList.add('hidden');
                await refreshSpendingMetricsChart(null);
            }
        } catch (e) {
            setStatus(e.message || 'Failed to load months', true);
        }
    }

    function renderSimpleList(elId, items, formatter) {
        const el = document.getElementById(elId);
        if (!el) return;
        el.innerHTML = '';
        if (!items || !items.length) {
            const li = document.createElement('li');
            li.className = 'spending-muted';
            li.textContent = 'No data';
            el.appendChild(li);
            return;
        }
        items.forEach((item) => {
            const li = document.createElement('li');
            li.textContent = formatter(item);
            el.appendChild(li);
        });
    }

    let budgetActionsExpanded = false;

    function applySpendingInsightKpis(ins) {
        const incomeEl = document.getElementById('kpi-income');
        const outEl = document.getElementById('kpi-outgoing');
        const netEl = document.getElementById('kpi-net');
        const savEl = document.getElementById('kpi-savings-rate');
        if (incomeEl) incomeEl.textContent = formatMoney(ins.income_total);
        if (outEl) outEl.textContent = formatMoney(ins.outgoing_total);
        if (netEl) netEl.textContent = formatMoney(ins.net);
        if (savEl) {
            savEl.textContent = ins.savings_rate == null ? '—' : `${Number(ins.savings_rate).toFixed(2)}%`;
        }
        setDeltaEl(document.getElementById('kpi-income-delta'), ins.income_delta_vs_prev_month, { higherIsWorse: false });
        setDeltaEl(document.getElementById('kpi-outgoing-delta'), ins.outgoing_delta_vs_prev_month, { higherIsWorse: true });
        setDeltaEl(document.getElementById('kpi-net-delta'), ins.net_delta_vs_prev_month, { higherIsWorse: false });
        const sub = document.getElementById('kpi-savings-sub');
        const n = Number(ins.net);
        if (sub) {
            if (Number.isFinite(n)) {
                sub.classList.remove('hidden');
                sub.textContent = n >= 0 ? 'Net positive this month' : 'Net negative this month';
            } else {
                sub.classList.add('hidden');
            }
        }
        const srd = ins.savings_rate_delta_vs_prev_month;
        const sd = document.getElementById('kpi-savings-delta');
        if (sd) {
            if (srd != null && Number.isFinite(Number(srd)) && Number(srd) !== 0) {
                const v = Number(srd);
                sd.classList.remove('hidden', 'spending-metric__delta--up', 'spending-metric__delta--down');
                if (v > 0) {
                    sd.classList.add('spending-metric__delta--down');
                    sd.textContent = `Savings rate up ${v.toFixed(1)}pp vs last month`;
                } else {
                    sd.classList.add('spending-metric__delta--up');
                    sd.textContent = `Savings rate down ${Math.abs(v).toFixed(1)}pp vs last month`;
                }
            } else {
                sd.classList.add('hidden');
                sd.textContent = '';
            }
        }
        const mom = document.getElementById('kpi-mom-summary');
        if (mom) {
            const parts = [];
            if (ins.outgoing_delta_vs_prev_month != null && Number(ins.outgoing_delta_vs_prev_month) !== 0) {
                const o = Number(ins.outgoing_delta_vs_prev_month);
                const prevO = Number(ins.outgoing_total) - o;
                const pc = formatPctVsPrev(ins.outgoing_total, prevO);
                parts.push(
                    `Outgoing is ${o > 0 ? 'higher' : 'lower'} than last month${pc ? ` (${pc} vs last month’s outgoing total)` : ''}.`,
                );
            }
            if (ins.net_delta_vs_prev_month != null && Number(ins.net_delta_vs_prev_month) !== 0) {
                const d = Number(ins.net_delta_vs_prev_month);
                parts.push(`Net is ${d > 0 ? 'higher' : 'lower'} than last month by ${formatMoney(Math.abs(d))}.`);
            }
            if (parts.length) {
                mom.classList.remove('hidden');
                mom.textContent = parts.join(' ');
            } else {
                mom.classList.add('hidden');
                mom.textContent = '';
            }
        }
        const trl = document.getElementById('kpi-trailing-line');
        if (trl) {
            const o6 = ins.outgoing_trailing_avg_6m;
            const n6 = ins.trailing_6m_months_count;
            if (o6 != null && n6) {
                trl.classList.remove('hidden');
                let t = `Trailing 6m average outgoing: ${formatMoney(o6)} (from ${n6} prior month(s)).`;
                if (ins.outgoing_vs_trailing_6m_pct != null) {
                    const p = ins.outgoing_vs_trailing_6m_pct;
                    t += ` This month is ${p >= 0 ? '+' : ''}${p.toFixed(1)}% vs that average.`;
                }
                if (ins.income_trailing_avg_3m != null) {
                    t += ` 3m avg income: ${formatMoney(ins.income_trailing_avg_3m)}.`;
                }
                trl.textContent = t;
            } else {
                trl.classList.add('hidden');
                trl.textContent = '';
            }
        }
    }

    function renderTopCategoriesWithTrends(top, trends) {
        const el = document.getElementById('top-categories-list');
        if (!el) return;
        el.innerHTML = '';
        if (!top || !top.length) {
            const li = document.createElement('li');
            li.className = 'spending-muted';
            li.textContent = 'No data';
            el.appendChild(li);
            return;
        }
        const tmap = {};
        (trends || []).forEach((t) => {
            tmap[String(t.category).toLowerCase()] = t;
        });
        const max = Math.max(1, ...top.map((x) => Number(x.amount) || 0));
        top.forEach((row) => {
            const tr = tmap[String(row.category).toLowerCase()];
            const li = document.createElement('li');
            const amt = Number(row.amount) || 0;
            const w = Math.min(100, Math.round((100 * amt) / max));
            const extra = [];
            if (tr && tr.mom_delta != null && Number(tr.mom_delta) !== 0) {
                const d = Number(tr.mom_delta);
                extra.push(
                    `MoM ${d > 0 ? '↑' : '↓'} ${formatMoney(Math.abs(d))}${
                        tr.mom_delta_pct != null
                            ? ` (${tr.mom_delta_pct >= 0 ? '+' : ''}${tr.mom_delta_pct}%)`
                            : ''
                    }`,
                );
            }
            if (tr && tr.vs_3m_pct != null) {
                extra.push(`vs 3m avg ${tr.vs_3m_pct >= 0 ? '+' : ''}${tr.vs_3m_pct}%`);
            }
            li.innerHTML = `
            <div class="flex justify-between gap-2">
                <span class="capitalize font-medium" style="color: var(--text-color)">${escapeHtml(String(row.category))}</span>
                <span class="tabular-nums shrink-0">${formatMoney(amt)}</span>
            </div>
            ${
                extra.length
                    ? `<div class="text-xs mt-0.5" style="color: var(--text-color); opacity:0.8">${extra.join(' · ')}</div>`
                    : ''
            }
            <div class="mt-1 h-2 rounded overflow-hidden" style="background: var(--background-color)">
                <div class="h-full rounded" style="width: ${w}%; background: var(--primary-color); opacity: 0.6"></div>
            </div>`;
            el.appendChild(li);
        });
    }

    function renderTopMerchantsBars(merchants) {
        const el = document.getElementById('top-merchants-list');
        if (!el) return;
        el.innerHTML = '';
        if (!merchants || !merchants.length) {
            const li = document.createElement('li');
            li.className = 'spending-muted';
            li.textContent = 'No data';
            el.appendChild(li);
            return;
        }
        const max = Math.max(1, ...merchants.map((m) => Number(m.amount) || 0));
        merchants.forEach((row) => {
            const li = document.createElement('li');
            const amt = Number(row.amount) || 0;
            const w = Math.min(100, Math.round((100 * amt) / max));
            li.innerHTML = `
            <div class="flex justify-between gap-2">
                <span class="font-medium" style="color: var(--text-color)">${escapeHtml(String(row.merchant))}</span>
                <span class="tabular-nums shrink-0">${formatMoney(amt)}</span>
            </div>
            <div class="mt-1 h-2 rounded overflow-hidden" style="background: var(--background-color)">
                <div class="h-full rounded" style="width: ${w}%; background: #64748b; opacity: 0.55"></div>
            </div>`;
            el.appendChild(li);
        });
    }

    function renderBudgetActionItems(items) {
        const wrap = document.getElementById('budget-actions-wrap');
        const list = document.getElementById('budget-actions-list');
        const tw = document.getElementById('budget-actions-toggle-wrap');
        const btn = document.getElementById('budget-actions-toggle-btn');
        if (!wrap || !list) return;
        list.innerHTML = '';
        if (!items || !items.length) {
            wrap.classList.add('hidden');
            return;
        }
        wrap.classList.remove('hidden');
        const initial = BUDGET_ACTIONS_INITIAL;
        const total = items.length;
        const showN = budgetActionsExpanded ? total : Math.min(initial, total);
        items.slice(0, showN).forEach((it) => {
            const li = document.createElement('li');
            const hint = it.amount_hint != null ? ` · ${formatMoney(it.amount_hint)}` : '';
            li.innerHTML = `<div class="spending-budget-item-title">${escapeHtml(it.title)}${escapeHtml(hint)}</div><div class="spending-budget-item-detail">${escapeHtml(
                it.detail,
            )}</div>`;
            list.appendChild(li);
        });
        if (total > initial && tw && btn) {
            tw.classList.remove('hidden');
            btn.setAttribute('aria-expanded', budgetActionsExpanded ? 'true' : 'false');
            btn.textContent = budgetActionsExpanded ? 'Show fewer' : `Show all ${total} (${total - initial} more)`;
        } else if (tw) {
            tw.classList.add('hidden');
        }
    }

    function clearSavingsCoachResult() {
        savingsCoachResultFor = null;
        lastSavingsAdviceData = null;
        savingsCoachExpanded = false;
        const res = document.getElementById('savings-coach-result');
        const list = document.getElementById('savings-coach-list');
        const sum = document.getElementById('savings-coach-summary');
        const meta = document.getElementById('savings-coach-meta');
        const st = document.getElementById('savings-coach-status');
        const regen = document.getElementById('savings-coach-regenerate-btn');
        const tw = document.getElementById('savings-coach-toggle-wrap');
        if (res) res.classList.add('hidden');
        if (list) list.innerHTML = '';
        if (sum) sum.textContent = '';
        if (meta) meta.textContent = '';
        if (st) {
            st.classList.add('hidden');
            st.textContent = '';
            st.removeAttribute('aria-busy');
        }
        if (regen) regen.classList.add('hidden');
        if (tw) tw.classList.add('hidden');
        const gen = document.getElementById('savings-coach-generate-btn');
        if (gen) {
            gen.removeAttribute('aria-busy');
        }
    }

    function resetSavingsCoachIfMonthChanged(month) {
        if (savingsCoachResultFor && savingsCoachResultFor !== month) {
            clearSavingsCoachResult();
        }
    }

    function setSavingsCoachStatus(msg, { busy, isError } = {}) {
        const st = document.getElementById('savings-coach-status');
        if (!st) return;
        if (!msg) {
            st.classList.add('hidden');
            st.textContent = '';
            st.removeAttribute('aria-busy');
            return;
        }
        st.classList.remove('hidden');
        st.textContent = msg;
        st.setAttribute('aria-busy', busy ? 'true' : 'false');
        st.className = isError
            ? 'savings-coach-status text-sm mb-2 text-red-700'
            : 'savings-coach-status text-sm mb-2';
    }

    function updateSavingsCoachMonthHint(m) {
        const h = document.getElementById('savings-coach-hint');
        if (h) {
            h.textContent = `Sends ${m} insights, individual transaction lines (capped) for that month, a small line-item sample for recent prior months, and multi-month trend totals. Data is sent to the configured AI; not your full raw bank file.`;
        }
    }

    function renderSavingsAdvicePayload(data) {
        lastSavingsAdviceData = data;
        const advice = data && data.advice;
        if (!advice) return;
        const summary = document.getElementById('savings-coach-summary');
        const list = document.getElementById('savings-coach-list');
        const meta = document.getElementById('savings-coach-meta');
        const res = document.getElementById('savings-coach-result');
        const regen = document.getElementById('savings-coach-regenerate-btn');
        const tw = document.getElementById('savings-coach-toggle-wrap');
        const tbtn = document.getElementById('savings-coach-toggle-btn');
        if (summary) summary.textContent = advice.summary || '';
        if (list) {
            list.innerHTML = '';
            const recs = (advice.recommendations && advice.recommendations.slice()) || [];
            const n = recs.length;
            const showN = savingsCoachExpanded ? n : Math.min(SAVINGS_COACH_INITIAL, n);
            const priLabel = (p) => {
                if (p === 1) return 'High';
                if (p === 3) return 'Low';
                return 'Med';
            };
            recs.slice(0, showN).forEach((r) => {
                const li = document.createElement('li');
                const pr = Number(r.priority);
                const p = pr >= 1 && pr <= 3 ? pr : 2;
                li.innerHTML = `<div class="spending-budget-item-title"><span class="savings-coach-priority savings-coach-priority--${p}">${escapeHtml(
                    priLabel(p),
                )}</span>${escapeHtml(r.title || '')}</div>
                <div class="spending-budget-item-detail">${escapeHtml(r.detail || '')}</div>
                ${
                    r.evidence
                        ? `<div class="spending-budget-item-detail--evidence">Linked: ${escapeHtml(r.evidence)}</div>`
                        : ''
                }`;
                list.appendChild(li);
            });
            if (tw && tbtn && n > SAVINGS_COACH_INITIAL) {
                tw.classList.remove('hidden');
                tbtn.setAttribute('aria-expanded', savingsCoachExpanded ? 'true' : 'false');
                tbtn.textContent = savingsCoachExpanded
                    ? 'Show fewer'
                    : `Show all ${n} (${n - SAVINGS_COACH_INITIAL} more)`;
            } else if (tw) {
                tw.classList.add('hidden');
            }
        }
        if (meta) {
            const parts = [];
            if (data.model) parts.push(`Model: ${data.model}`);
            if (typeof data.trend_months_included === 'number') {
                parts.push(`Prior months in trend: ${data.trend_months_included}`);
            }
            const sent = data.focal_transaction_count_sent;
            const total = data.focal_transaction_count_in_month;
            if (typeof sent === 'number' && typeof total === 'number') {
                if (data.focal_transactions_truncated && total > sent) {
                    parts.push(`Transaction lines: ${sent} of ${total} (largest amounts kept)`);
                } else {
                    parts.push(`Transaction lines: ${sent}`);
                }
            }
            if (typeof data.prior_transaction_sample_months === 'number' && data.prior_transaction_sample_months > 0) {
                parts.push(`Prior month line samples: ${data.prior_transaction_sample_months}`);
            }
            meta.textContent = parts.join(' · ');
        }
        if (res) res.classList.remove('hidden');
        if (regen) {
            regen.classList.remove('hidden');
            regen.disabled = false;
        }
        savingsCoachResultFor = data.month || null;
    }

    async function requestSavingsAdvice() {
        const m = monthSelect && monthSelect.value;
        if (!m) return;
        const gen = document.getElementById('savings-coach-generate-btn');
        const regen = document.getElementById('savings-coach-regenerate-btn');
        if (gen) {
            gen.disabled = true;
            gen.setAttribute('aria-busy', 'true');
        }
        if (regen) regen.disabled = true;
        setSavingsCoachStatus('Generating suggestions…', { busy: true, isError: false });
        try {
            const response = await fetch('/api/spending/savings-advice', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ month: m }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                const err =
                    data.error ||
                    (response.status === 503
                        ? 'This feature needs OPENAI_API_KEY on the server.'
                        : 'Could not generate savings ideas. Try again.');
                setSavingsCoachStatus(err, { busy: false, isError: true });
                return;
            }
            setSavingsCoachStatus('', { busy: false });
            savingsCoachExpanded = false;
            renderSavingsAdvicePayload(data);
        } catch (e) {
            setSavingsCoachStatus(e.message || 'Request failed', { busy: false, isError: true });
        } finally {
            if (gen) {
                gen.disabled = false;
                gen.removeAttribute('aria-busy');
            }
            if (regen) regen.disabled = false;
        }
    }

    function bindReclassifyHandlers() {
        document.querySelectorAll('.reclassify-select').forEach((select) => {
            select.addEventListener('change', async () => {
                const txId = select.getAttribute('data-tx-id');
                if (!txId) return;
                const category = select.value;
                try {
                    const response = await fetch(`/api/spending/transaction/${encodeURIComponent(txId)}/category`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ category }),
                    });
                    const data = await response.json().catch(() => ({}));
                    if (!response.ok) throw new Error(data.error || 'Reclassification failed');
                    setStatus('Category updated.', false);
                    if (monthSelect && monthSelect.value) {
                        await loadInsight(monthSelect.value);
                    }
                } catch (e) {
                    setStatus(e.message || 'Reclassification failed', true);
                }
            });
        });
    }

    function bindUnlinkPairHandlers() {
        document.querySelectorAll('.unlink-pair').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const txId = btn.getAttribute('data-tx-id');
                if (!txId) return;
                if (!confirm('Unlink this internal transfer? Both legs will be unpaired; category totals will update.')) {
                    return;
                }
                try {
                    const response = await fetch(`/api/spending/transaction/${encodeURIComponent(txId)}/pair`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ unlink: true }),
                    });
                    const resData = await response.json().catch(() => ({}));
                    if (!response.ok) throw new Error(resData.error || 'Unlink failed');
                    setStatus('Internal transfer unlinked.', false);
                    if (monthSelect && monthSelect.value) {
                        await loadInsight(monthSelect.value);
                    }
                } catch (e) {
                    setStatus(e.message || 'Unlink failed', true);
                }
            });
        });
    }

    function bindInsightExcludeHandlers() {
        document.querySelectorAll('.insight-tx-exclude-btn').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const txId = btn.getAttribute('data-tx-id');
                if (!txId) return;
                const wantExclude = btn.getAttribute('data-want-exclude') === 'true';
                btn.disabled = true;
                try {
                    const response = await fetch(`/api/spending/transaction/${encodeURIComponent(txId)}/insights`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ excluded: wantExclude }),
                    });
                    const resData = await response.json().catch(() => ({}));
                    if (!response.ok) throw new Error(resData.error || 'Update failed');
                    setStatus(
                        wantExclude
                            ? 'This transaction is excluded from month totals. Use “Include in totals” to undo.'
                            : 'This transaction is included in month totals again.',
                        false,
                    );
                    if (monthSelect && monthSelect.value) {
                        await loadInsight(monthSelect.value);
                    }
                } catch (e) {
                    setStatus(e.message || 'Update failed', true);
                } finally {
                    btn.disabled = false;
                }
            });
        });
    }

    async function loadInsight(month) {
        if (!month) return;
        resetSavingsCoachIfMonthChanged(month);
        try {
            const response = await fetch(`/api/spending/insights?month=${encodeURIComponent(month)}`);
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.error || 'Could not load insights');
            if (!data.insight) {
                if (insightEmpty) insightEmpty.classList.remove('hidden');
                if (insightContent) insightContent.classList.add('hidden');
                const trPanel = document.getElementById('transfer-reconciliation-panel');
                if (trPanel) trPanel.classList.add('hidden');
                const kpiNote = document.getElementById('kpi-exclude-note');
                if (kpiNote) kpiNote.classList.add('hidden');
                const scw = document.getElementById('savings-coach-wrap');
                if (scw) scw.classList.add('hidden');
                clearSavingsCoachResult();
                const scg = document.getElementById('savings-coach-generate-btn');
                if (scg) scg.disabled = true;
                insightTransactionsSnapshot = [];
                clearInsightTransactionFilters();
                loadInsight._lastLoadedMonth = null;
                populateInsightTxCategoryFilter();
                populateInsightTxSourceFilter();
                const tbodyEmpty = document.getElementById('insight-transactions-tbody');
                if (tbodyEmpty) {
                    tbodyEmpty.innerHTML = '';
                }
                const st = document.getElementById('insight-tx-filter-status');
                if (st) {
                    st.textContent = '';
                    st.classList.add('hidden');
                }
                const roll = document.getElementById('insight-tx-filter-rollup');
                if (roll) {
                    roll.textContent = '';
                    roll.removeAttribute('title');
                    roll.removeAttribute('aria-label');
                    roll.classList.add('hidden');
                }
                await refreshSpendingMetricsChart(month);
                return;
            }
            if (insightEmpty) insightEmpty.classList.add('hidden');
            if (insightContent) insightContent.classList.remove('hidden');

            const heroKicker = document.getElementById('spending-hero-kicker');
            if (heroKicker) heroKicker.textContent = month;

            const kpiNote = document.getElementById('kpi-exclude-note');
            if (kpiNote) {
                kpiNote.classList.remove('hidden');
            }
            const trRecon = data.insight && data.insight.transfer_reconciliation;
            const trPanel = document.getElementById('transfer-reconciliation-panel');
            const trSum = document.getElementById('transfer-reconciliation-summary');
            if (trPanel && trSum) {
                if (trRecon) {
                    trPanel.classList.remove('hidden');
                    const mismatch =
                        (trRecon.paired_leg_amount_mismatch || 0) > 0.01
                            ? ` Paired leg mismatch: ${formatMoney(trRecon.paired_leg_amount_mismatch)} (check FX/fees or unlink).`
                            : '';
                    trSum.textContent = `${trRecon.pair_count || 0} internal transfer pair(s); one outgoing leg total ${formatMoney(
                        trRecon.internal_transfer_outgoing_total || 0,
                    )}.${mismatch} Unmatched: ${trRecon.unmatched_outgoing && trRecon.unmatched_incoming
                        ? `${trRecon.unmatched_outgoing.count} outgoing ${formatMoney(trRecon.unmatched_outgoing.total)}; ${trRecon.unmatched_incoming.count} incoming ${formatMoney(trRecon.unmatched_incoming.total)}.`
                        : '—'}`;
                } else {
                    trPanel.classList.add('hidden');
                    trSum.textContent = '';
                }
            }

            applySpendingInsightKpis(data.insight);
            budgetActionsExpanded = false;
            renderBudgetActionItems((data.insight && data.insight.budget_action_items) || []);
            renderTopCategoriesWithTrends(
                data.insight.top_categories,
                (data.insight && data.insight.category_trends) || [],
            );
            renderTopMerchantsBars(data.insight.top_merchants);
            renderSimpleList('anomalies-list', data.insight.anomalies, (x) => {
                if (x.kind === 'category_spike' && x.delta_pct != null) {
                    const n = x.baseline_months;
                    const baseHint =
                        typeof n === 'number' && n > 0
                            ? ` (avg of ${n} prior month(s) with data: ${formatMoney(x.baseline_avg)})`
                            : ` (avg ${formatMoney(x.baseline_avg)})`;
                    return `${x.category}: ${formatMoney(x.amount)} (~${x.delta_pct}% above${baseHint})`;
                }
                return `${x.category}: ${formatMoney(x.amount)} (baseline ${formatMoney(x.baseline_avg)})`;
            });
            renderSimpleList('recurring-list', data.insight.recurring_candidates, (x) => `${x.description} (${x.occurrences} tx)`);

            const paretoEl = document.getElementById('pareto-summary');
            if (paretoEl) {
                const p = data.insight.top_transactions_pareto_pct;
                if (p != null && typeof p === 'number') {
                    paretoEl.textContent = `Top 5 outgoing transactions account for about ${p}% of non-transfer spend this month.`;
                    paretoEl.classList.remove('hidden');
                } else {
                    paretoEl.classList.add('hidden');
                    paretoEl.textContent = '';
                }
            }

            renderLargestOutgoing((data.insight && data.insight.largest_outgoing) || []);
            renderCategoryBreakdown((data.insight && data.insight.category_breakdown) || []);
            renderSubscriptionSignals((data.insight && data.insight.subscription_signals) || []);

            if (loadInsight._lastLoadedMonth !== month) {
                insightTxSortState = { key: 'date', dir: 'asc' };
            }
            loadInsight._lastLoadedMonth = month;
            insightTransactionsSnapshot = data.transactions || [];
            populateInsightTxCategoryFilter(insightTransactionsSnapshot);
            populateInsightTxSourceFilter(insightTransactionsSnapshot);
            updateInsightSortIndicators();
            renderInsightTransactionsBody();
            loadInsight._budgetItems = (data.insight && data.insight.budget_action_items) || [];
            const scwOk = document.getElementById('savings-coach-wrap');
            if (scwOk) scwOk.classList.remove('hidden');
            const scgOk = document.getElementById('savings-coach-generate-btn');
            if (scgOk) {
                scgOk.disabled = false;
                scgOk.setAttribute('aria-label', `Generate savings ideas for ${month}`);
            }
            updateSavingsCoachMonthHint(month);
            await refreshSpendingMetricsChart(month);
        } catch (e) {
            setStatus(e.message || 'Failed to load insights', true);
        }
    }

    if (reportPeriodMonth && !reportPeriodMonth.value) {
        const now = new Date();
        reportPeriodMonth.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    }
    syncPeriodRangeFromMonthInput();
    if (reportPeriodMonth) {
        reportPeriodMonth.addEventListener('change', syncPeriodRangeFromMonthInput);
    }
    if (periodResetBtn) {
        periodResetBtn.addEventListener('click', syncPeriodRangeFromMonthInput);
    }

    if (previewBtn && fileInput) {
        previewBtn.addEventListener('click', async () => {
            const file = fileInput.files && fileInput.files[0];
            if (!file) {
                setStatus('Choose a statement file first.', true);
                return;
            }
            const period = getSpendingPeriodPayload();
            if (!period.report_month || !period.period_start || !period.period_end) {
                setStatus('Choose a stats month and reporting period (start/end dates).', true);
                return;
            }
            previewBtn.disabled = true;
            clearPreview();
            let previewTick = null;
            let tickState = { msg: 'Preparing…', serverTotal: 0, stepStart: Date.now() };
            const armTick = (msg, serverTotal) => {
                tickState = { msg, serverTotal: serverTotal || 0, stepStart: Date.now() };
            };
            const refreshTick = () => {
                const stepMs = Date.now() - tickState.stepStart;
                const slow = stepMs >= 5 * 60 * 1000 ? ' — over 5 minutes on this step' : '';
                setStatus(
                    `${tickState.msg} · This step: ${formatDuration(stepMs)} · Server elapsed: ${formatDuration(tickState.serverTotal)}${slow}`,
                    false,
                );
            };
            armTick('Preparing upload…', 0);
            refreshTick();
            previewTick = setInterval(refreshTick, 1000);
            try {
                const fd = new FormData();
                fd.append('file', file);
                fd.append('report_month', period.report_month);
                fd.append('period_start', period.period_start);
                fd.append('period_end', period.period_end);
                const data = await postSpendingPreviewStream(fd, (ev) => {
                    if (ev.type === 'upload') {
                        armTick(`Uploading file (${ev.pct}%)`, 0);
                        refreshTick();
                        return;
                    }
                    if (ev.type === 'progress') {
                        armTick(ev.message, ev.elapsed_ms);
                        refreshTick();
                    }
                });
                renderPreview(data.transactions || [], data.summary || null, data.pipeline || null);
                setStatus(`Preview ready: ${(data.transactions || []).length} transaction(s).`, false);
            } catch (e) {
                setStatus(e.message || 'Preview failed', true);
            } finally {
                if (previewTick) clearInterval(previewTick);
                previewBtn.disabled = false;
            }
        });
    }

    function prependRecentStatement(statement) {
        if (!statement) return;
        const list = document.getElementById('home-statements-list');
        if (!list) return;
        const empty = document.getElementById('home-statements-empty');

        const fileName = statement.file_name || 'Statement';
        const reportMonth = statement.report_month || '—';
        const bankSource = statement.bank_source ? String(statement.bank_source).trim() : '';
        const periodStart = statement.period_start;
        const periodEnd = statement.period_end;
        const importedCount = statement.imported_count || 0;
        const uploadedAt = statement.uploaded_at ? String(statement.uploaded_at).slice(0, 10) : '';

        const sourceHtml = bankSource ? ` · ${escapeHtml(bankSource)}` : '';
        const periodHtml = periodStart && periodEnd
            ? ` <span class="spending-muted">(${escapeHtml(periodStart)} → ${escapeHtml(periodEnd)})</span>`
            : '';
        const uploadedHtml = uploadedAt ? ` · ${escapeHtml(uploadedAt)}` : '';

        const li = document.createElement('li');
        li.innerHTML =
            `<span><strong>${escapeHtml(fileName)}</strong> · ${escapeHtml(reportMonth)}${sourceHtml}${periodHtml}</span>` +
            `<span class="spending-muted">${importedCount} imported${uploadedHtml}</span>`;

        list.insertBefore(li, list.firstChild);
        list.hidden = false;
        if (empty) empty.hidden = true;

        while (list.children.length > 8) {
            list.removeChild(list.lastChild);
        }
    }

    function syncStatementSourceOptions(sources) {
        const list = document.getElementById('statement-source-options');
        if (!list) return;
        const labels = Array.isArray(sources) ? sources : [];
        const byLower = new Map();
        labels.forEach((raw) => {
            const label = String(raw || '').trim();
            if (!label) return;
            const low = label.toLowerCase();
            if (!byLower.has(low)) byLower.set(low, label);
        });
        const sorted = Array.from(byLower.values()).sort((a, b) =>
            a.toLowerCase().localeCompare(b.toLowerCase()),
        );
        list.innerHTML = '';
        sorted.forEach((label) => {
            const opt = document.createElement('option');
            opt.value = label;
            list.appendChild(opt);
        });
        window.KNOWN_BANK_SOURCES = sorted;
    }

    function readStatementSourceInput() {
        const el = document.getElementById('statement-source');
        if (!el) return '';
        return String(el.value || '').trim().slice(0, 80);
    }

    if (importBtn) {
        importBtn.addEventListener('click', async () => {
            if (!previewTbody) return;
            const selected = [];
            previewTbody.querySelectorAll('tr').forEach((tr) => {
                const cb = tr.querySelector('.preview-include');
                if (!cb || !cb.checked) return;
                const categorySelect = tr.querySelector('.preview-category');
                const amount = parseFloat(tr.dataset.amount || '0');
                if (!Number.isFinite(amount) || amount <= 0) return;
                selected.push({
                    date: tr.dataset.date,
                    description: tr.dataset.description || '',
                    direction: tr.dataset.direction || 'outgoing',
                    amount,
                    category: categorySelect ? categorySelect.value : null,
                    confidence: tr.dataset.confidence ? parseFloat(tr.dataset.confidence) : null,
                    rationale: tr.dataset.rationale || '',
                });
            });
            if (!selected.length) {
                setStatus('Select at least one transaction to import.', true);
                return;
            }

            importBtn.disabled = true;
            try {
                const period = getSpendingPeriodPayload();
                if (!period.report_month || !period.period_start || !period.period_end) {
                    setStatus('Choose a stats month and reporting period (start/end dates).', true);
                    return;
                }
                const fileName = fileInput && fileInput.files && fileInput.files[0] ? fileInput.files[0].name : '';
                const source = readStatementSourceInput();
                const payload = {
                    transactions: selected,
                    file_name: fileName,
                    report_month: period.report_month,
                    period_start: period.period_start,
                    period_end: period.period_end,
                };
                if (source) {
                    payload.source = source;
                }
                const response = await fetch('/api/spending/statement/import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.error || 'Import failed');
                let msg = `Imported ${data.imported_count} transaction(s); skipped ${data.skipped_duplicates} duplicate(s).`;
                const rec = data.reconciliation;
                if (rec) {
                    msg += ` Internal transfers: ${rec.applied_pairs} pair(s) auto-matched; unmatched ${rec.unmatched_outgoing_count} out / ${rec.unmatched_incoming_count} in.`;
                }
                setStatus(msg, false);
                clearPreview();
                prependRecentStatement(data.statement);
                if (Array.isArray(data.known_bank_sources)) {
                    syncStatementSourceOptions(data.known_bank_sources);
                } else if (source) {
                    const prev = Array.isArray(window.KNOWN_BANK_SOURCES) ? window.KNOWN_BANK_SOURCES.slice() : [];
                    prev.push(source);
                    syncStatementSourceOptions(prev);
                }
                const homeNext = document.getElementById('home-import-next');
                const reportMonth = data.report_month || (data.months && data.months.length ? data.months[0] : null);
                if (homeNext && reportMonth) {
                    homeNext.classList.remove('hidden');
                    homeNext.innerHTML =
                        `Imported into <strong>${reportMonth}</strong>. ` +
                        `<a href="/spending?month=${encodeURIComponent(reportMonth)}">Open Monthly insights</a>` +
                        `<a href="/spending/daily">Update Daily plan</a>`;
                }
                if (monthSelect) {
                    await loadMonths(reportMonth);
                }
            } catch (e) {
                setStatus(e.message || 'Import failed', true);
            } finally {
                importBtn.disabled = false;
            }
        });
    }

    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            clearPreview();
            setStatus('', false);
            if (fileInput) fileInput.value = '';
        });
    }

    if (previewIncludeAll && previewTbody) {
        previewIncludeAll.addEventListener('change', () => {
            const checked = previewIncludeAll.checked;
            previewTbody.querySelectorAll('.preview-include').forEach((cb) => {
                cb.checked = checked;
            });
            previewIncludeAll.indeterminate = false;
        });
        previewTbody.addEventListener('change', (e) => {
            if (!e.target.classList.contains('preview-include')) return;
            syncPreviewIncludeAll();
        });
        previewTbody.addEventListener('click', (e) => {
            if (e.target.closest('input, select, label, a, button')) return;
            const row = e.target.closest('tr.preview-tx-row');
            if (!row) return;
            togglePreviewRow(row);
        });
        previewTbody.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            if (e.target.closest('input, select, label, a, button')) return;
            const row = e.target.closest('tr.preview-tx-row');
            if (!row) return;
            e.preventDefault();
            togglePreviewRow(row);
        });
    }

    if (monthSelect) {
        monthSelect.addEventListener('change', async () => {
            setInsightActionStatus('', false);
            if (deleteMonthBtn) {
                deleteMonthBtn.disabled = !monthSelect.value;
            }
            if (recategorizeMonthBtn) {
                recategorizeMonthBtn.disabled = !monthSelect.value;
            }
            if (recomputeInsightsBtn) {
                recomputeInsightsBtn.disabled = !monthSelect.options || monthSelect.options.length <= 1;
            }
            if (monthSelect.value) {
                await loadInsight(monthSelect.value);
            }
        });
    }

    if (recomputeInsightsBtn) {
        const recomputeDefault = 'Recompute insights';
        recomputeInsightsBtn.addEventListener('click', async () => {
            if (recomputeInsightsBtn.disabled) return;
            recomputeInsightsBtn.disabled = true;
            recomputeInsightsBtn.textContent = 'Recomputing…';
            setInsightActionStatus('Recalculating monthly insights from your transactions…', false, { busy: true });
            setStatus('Recalculating insights…', false);
            const keep = monthSelect && monthSelect.value;
            try {
                const response = await fetch('/api/spending/insights/recompute', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                });
                const resData = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(resData.error || 'Recompute failed');
                const n = resData.recomputed;
                const done = `Done. ${typeof n === 'number' ? `${n} month(s) refreshed. ` : ''}Charts and KPIs are up to date.`;
                setInsightActionStatus(done, false, { busy: false });
                setStatus(done, false);
                await loadMonths(keep);
            } catch (e) {
                const errMsg = e.message || 'Recompute failed';
                setInsightActionStatus(errMsg, true, { busy: false });
                setStatus(errMsg, true);
            } finally {
                recomputeInsightsBtn.textContent = recomputeDefault;
                recomputeInsightsBtn.disabled = !monthSelect || !monthSelect.options || monthSelect.options.length <= 1;
            }
        });
    }

    if (recategorizeMonthBtn && monthSelect) {
        const recategorizeBtnDefault = 'Re-run categorisation';
        let recategorizeTick = null;
        recategorizeMonthBtn.addEventListener('click', async () => {
            const m = monthSelect.value;
            if (!m) return;
            if (
                !confirm(
                    `Re-run AI categorisation for ${m}? Outgoing lines in this month are re-classified; descriptions you set manually stay as-is.`,
                )
            ) {
                return;
            }
            const t0 = Date.now();
            const armProgressTick = () => {
                if (recategorizeTick) clearInterval(recategorizeTick);
                const tick = () => {
                    const msg = `Re-categorisation in progress (${formatDuration(Date.now() - t0)}) — calling the AI for new labels…`;
                    setInsightActionStatus(msg, false, { busy: true });
                    setStatus(msg, false);
                };
                tick();
                recategorizeTick = setInterval(tick, 1000);
            };
            recategorizeMonthBtn.disabled = true;
            recategorizeMonthBtn.textContent = 'Re-categorising…';
            armProgressTick();
            try {
                const response = await fetch('/api/spending/recategorize', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ report_month: m }),
                });
                const resData = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(resData.error || 'Recategorisation failed');
                const n = resData.labels_refreshed;
                const doneMsg =
                    typeof n === 'number'
                        ? `Categorisation finished: ${n} merchant label(s) re-scored. Totals below are updated.`
                        : 'Categorisation finished. Totals below are updated.';
                setInsightActionStatus(doneMsg, false, { busy: false });
                setStatus(doneMsg, false);
                await loadInsight(m);
            } catch (e) {
                const errMsg = e.message || 'Recategorisation failed';
                setInsightActionStatus(errMsg, true, { busy: false });
                setStatus(errMsg, true);
            } finally {
                if (recategorizeTick) {
                    clearInterval(recategorizeTick);
                    recategorizeTick = null;
                }
                recategorizeMonthBtn.textContent = recategorizeBtnDefault;
                recategorizeMonthBtn.disabled = !monthSelect.value;
            }
        });
    }

    const reapplyPairBtn = document.getElementById('reapply-pair-btn');
    if (reapplyPairBtn && monthSelect) {
        reapplyPairBtn.addEventListener('click', async () => {
            const m = monthSelect.value;
            if (!m) return;
            reapplyPairBtn.disabled = true;
            setStatus('Re-running transfer matching…', false);
            try {
                const response = await fetch('/api/spending/pair/apply', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ report_month: m }),
                });
                const resData = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(resData.error || 'Re-run failed');
                const r = resData.reconciliation;
                setStatus(
                    r
                        ? `Transfer matching: ${r.applied_pairs} pair(s); unmatched ${r.unmatched_outgoing_count} out / ${r.unmatched_incoming_count} in.`
                        : 'Transfer matching updated.',
                    false,
                );
                await loadInsight(m);
            } catch (e) {
                setStatus(e.message || 'Re-run failed', true);
            } finally {
                reapplyPairBtn.disabled = false;
            }
        });
    }

    if (deleteMonthBtn && monthSelect) {
        deleteMonthBtn.addEventListener('click', async () => {
            const m = monthSelect.value;
            if (!m) return;
            if (
                !confirm(
                    `Delete all spending data for ${m}? This removes transactions and insights for that month and cannot be undone.`,
                )
            ) {
                return;
            }
            deleteMonthBtn.disabled = true;
            setStatus('Deleting…', false);
            try {
                const response = await fetch(`/api/spending/month/${encodeURIComponent(m)}`, {
                    method: 'DELETE',
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.error || 'Delete failed');
                setStatus(
                    data.removed_transactions
                        ? `Removed ${data.removed_transactions} transaction(s) for ${data.month}.`
                        : `No transactions left for ${data.month}.`,
                    false,
                );
                await loadMonths(null);
            } catch (e) {
                setStatus(e.message || 'Delete failed', true);
                if (deleteMonthBtn) deleteMonthBtn.disabled = false;
            }
        });
    }

    document.querySelectorAll('.insight-sort-btn').forEach((btn) => {
        btn.addEventListener('click', () => onInsightSortClick(btn.getAttribute('data-sort-key')));
    });

    const insightTxTable = document.getElementById('spending-insight-transactions-table');
    if (insightTxTable) {
        insightTxTable.addEventListener('input', (e) => {
            const t = e.target;
            if (t && t.classList && t.classList.contains('spending-tx-header-filter')) {
                renderInsightTransactionsBody();
            }
        });
        insightTxTable.addEventListener('change', (e) => {
            const t = e.target;
            if (t && t.classList && t.classList.contains('spending-tx-header-filter')) {
                renderInsightTransactionsBody();
            }
        });
    }
    const insightTxFilterClear = document.getElementById('insight-tx-filter-clear');
    if (insightTxFilterClear) {
        insightTxFilterClear.addEventListener('click', () => {
            clearInsightTransactionFilters();
            renderInsightTransactionsBody();
        });
    }
    populateInsightTxCategoryFilter();
    populateInsightTxSourceFilter();
    if (Array.isArray(window.KNOWN_BANK_SOURCES) && window.KNOWN_BANK_SOURCES.length) {
        syncStatementSourceOptions(window.KNOWN_BANK_SOURCES);
    }

    const subscriptionToggleBtn = document.getElementById('subscription-signals-toggle-btn');
    if (subscriptionToggleBtn) {
        subscriptionToggleBtn.addEventListener('click', () => {
            const wasExpanded = subscriptionToggleBtn.getAttribute('aria-expanded') === 'true';
            updateSubscriptionSignalsExpanded(!wasExpanded);
        });
    }

    const budgetToggleBtn = document.getElementById('budget-actions-toggle-btn');
    if (budgetToggleBtn) {
        budgetToggleBtn.addEventListener('click', () => {
            budgetActionsExpanded = !budgetActionsExpanded;
            renderBudgetActionItems(loadInsight._budgetItems || []);
        });
    }

    loadInsight._budgetItems = [];

    const categoryLegendCbs = document.getElementById('spending-category-legend-cbs');
    if (categoryLegendCbs) {
        categoryLegendCbs.addEventListener('change', (e) => {
            const t = e.target;
            if (!t || !t.classList || !t.classList.contains('category-stack-legend-cb')) return;
            const chart = spendingCategoryStackChart;
            if (!chart) return;
            const idx = parseInt(t.dataset.datasetIndex, 10);
            if (Number.isNaN(idx)) return;
            setCategoryStackDatasetVisible(chart, idx, t.checked);
            chart.update();
        });
    }
    const categoryStackShowAll = document.getElementById('category-stack-show-all');
    const categoryStackHideAll = document.getElementById('category-stack-hide-all');
    if (categoryStackShowAll) {
        categoryStackShowAll.addEventListener('click', () => {
            const chart = spendingCategoryStackChart;
            if (!chart) return;
            for (let i = 0; i < chart.data.datasets.length; i++) {
                setCategoryStackDatasetVisible(chart, i, true);
            }
            chart.update();
            document.querySelectorAll('.category-stack-legend-cb').forEach((cb) => {
                cb.checked = true;
            });
        });
    }
    if (categoryStackHideAll) {
        categoryStackHideAll.addEventListener('click', () => {
            const chart = spendingCategoryStackChart;
            if (!chart) return;
            for (let i = 0; i < chart.data.datasets.length; i++) {
                setCategoryStackDatasetVisible(chart, i, false);
            }
            chart.update();
            document.querySelectorAll('.category-stack-legend-cb').forEach((cb) => {
                cb.checked = false;
            });
        });
    }

    const savingsGen = document.getElementById('savings-coach-generate-btn');
    const savingsRegen = document.getElementById('savings-coach-regenerate-btn');
    const savingsToggle = document.getElementById('savings-coach-toggle-btn');
    if (savingsGen) savingsGen.addEventListener('click', () => requestSavingsAdvice());
    if (savingsRegen) savingsRegen.addEventListener('click', () => requestSavingsAdvice());
    if (savingsToggle) {
        savingsToggle.addEventListener('click', () => {
            if (!lastSavingsAdviceData) return;
            savingsCoachExpanded = !savingsCoachExpanded;
            renderSavingsAdvicePayload(lastSavingsAdviceData);
        });
    }

    const initialMonth = (() => {
        try {
            const q = new URLSearchParams(window.location.search).get('month');
            return q || null;
        } catch (e) {
            return null;
        }
    })();
    if (monthSelect) {
        loadMonths(initialMonth);
    }
});
