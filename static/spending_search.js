(function () {
    'use strict';

    const PAGE_SIZE = 100;
    let debounceTimer = null;
    let currentOffset = 0;
    let lastTotal = 0;
    let lastFilters = null;
    let requestSeq = 0;

    function $(id) {
        return document.getElementById(id);
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function readFilters() {
        return {
            q: ($('tx-search-q') && $('tx-search-q').value) || '',
            date_from: ($('tx-search-date-from') && $('tx-search-date-from').value) || '',
            date_to: ($('tx-search-date-to') && $('tx-search-date-to').value) || '',
            direction: ($('tx-search-direction') && $('tx-search-direction').value) || '',
            category: ($('tx-search-category') && $('tx-search-category').value) || '',
            source: ($('tx-search-source') && $('tx-search-source').value) || '',
            min_amount: ($('tx-search-min-amount') && $('tx-search-min-amount').value) || '',
            max_amount: ($('tx-search-max-amount') && $('tx-search-max-amount').value) || '',
        };
    }

    function filtersHaveCriterion(f) {
        return !!(
            (f.q && f.q.trim()) ||
            f.date_from ||
            f.date_to ||
            f.direction ||
            f.category ||
            f.source ||
            (f.min_amount && String(f.min_amount).trim()) ||
            (f.max_amount && String(f.max_amount).trim())
        );
    }

    function buildQuery(f, offset) {
        const params = new URLSearchParams();
        if (f.q && f.q.trim()) params.set('q', f.q.trim());
        if (f.date_from) params.set('date_from', f.date_from);
        if (f.date_to) params.set('date_to', f.date_to);
        if (f.direction) params.set('direction', f.direction);
        if (f.category) params.set('category', f.category);
        if (f.source) params.set('source', f.source);
        if (f.min_amount && String(f.min_amount).trim()) params.set('min_amount', String(f.min_amount).trim());
        if (f.max_amount && String(f.max_amount).trim()) params.set('max_amount', String(f.max_amount).trim());
        params.set('limit', String(PAGE_SIZE));
        params.set('offset', String(offset || 0));
        return params;
    }

    function syncUrl(f, offset) {
        const params = buildQuery(f, offset);
        const qs = params.toString();
        const next = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
        if (next !== `${window.location.pathname}${window.location.search}`) {
            window.history.replaceState({}, '', next);
        }
    }

    function applyUrlToForm() {
        const params = new URLSearchParams(window.location.search);
        const set = (id, key) => {
            const el = $(id);
            if (!el) return;
            const v = params.get(key);
            if (v != null) el.value = v;
        };
        set('tx-search-q', 'q');
        set('tx-search-date-from', 'date_from');
        if (!params.get('date_from') && params.get('from')) set('tx-search-date-from', 'from');
        set('tx-search-date-to', 'date_to');
        if (!params.get('date_to') && params.get('to')) set('tx-search-date-to', 'to');
        set('tx-search-direction', 'direction');
        set('tx-search-category', 'category');
        set('tx-search-source', 'source');
        set('tx-search-min-amount', 'min_amount');
        set('tx-search-max-amount', 'max_amount');
        const off = parseInt(params.get('offset') || '0', 10);
        currentOffset = Number.isFinite(off) && off > 0 ? off : 0;
    }

    function populateSourceSelect(known) {
        const sel = $('tx-search-source');
        if (!sel) return;
        const prev = sel.value;
        const labels = Array.isArray(known) ? known.slice() : [];
        labels.sort((a, b) => String(a).toLowerCase().localeCompare(String(b).toLowerCase()));
        sel.innerHTML = '<option value="">All</option>';
        labels.forEach((label) => {
            const o = document.createElement('option');
            o.value = label;
            o.textContent = label;
            sel.appendChild(o);
        });
        const none = document.createElement('option');
        none.value = '__none__';
        none.textContent = '(none)';
        sel.appendChild(none);
        if (prev && Array.from(sel.options).some((op) => op.value === prev)) {
            sel.value = prev;
        }
    }

    function setStatus(text) {
        const st = $('tx-search-status');
        if (!st) return;
        st.textContent = text || '';
    }

    function setRollup(rows) {
        const el = $('tx-search-rollup');
        if (!el) return;
        if (!rows || !rows.length) {
            el.textContent = '';
            el.classList.add('hidden');
            return;
        }
        let out = 0;
        let inc = 0;
        let nOut = 0;
        let nInc = 0;
        rows.forEach((tx) => {
            const n = Number(tx.amount);
            if (!Number.isFinite(n)) return;
            if (tx.direction === 'incoming') {
                inc += n;
                nInc += 1;
            } else {
                out += n;
                nOut += 1;
            }
        });
        const parts = [];
        if (nOut) parts.push(`Outgoing £${out.toFixed(2)} (${nOut})`);
        if (nInc) parts.push(`Incoming £${inc.toFixed(2)} (${nInc})`);
        if (!parts.length) {
            el.textContent = '';
            el.classList.add('hidden');
            return;
        }
        el.textContent = `This page: ${parts.join(' · ')}`;
        el.classList.remove('hidden');
    }

    function updatePager(total, offset) {
        const wrap = $('tx-search-pager');
        const prev = $('tx-search-prev');
        const next = $('tx-search-next');
        const label = $('tx-search-page-label');
        if (!wrap || !prev || !next || !label) return;
        if (total <= PAGE_SIZE) {
            wrap.classList.add('hidden');
            return;
        }
        wrap.classList.remove('hidden');
        const start = total === 0 ? 0 : offset + 1;
        const end = Math.min(offset + PAGE_SIZE, total);
        label.textContent = `${start}–${end} of ${total}`;
        prev.disabled = offset <= 0;
        next.disabled = offset + PAGE_SIZE >= total;
    }

    function renderRows(rows) {
        const tbody = $('tx-search-tbody');
        if (!tbody) return;
        if (!rows || !rows.length) {
            tbody.innerHTML = '';
            setExpandHint(false);
            return;
        }
        tbody.innerHTML = rows
            .map((tx) => {
                const month = String(tx.report_month || tx.month || '').slice(0, 7);
                const monthLink = month
                    ? `<a class="spending-skip search-tx-month-link" href="/spending?month=${encodeURIComponent(month)}">${escapeHtml(month)}</a>`
                    : '—';
                const src = tx.bank_source ? escapeHtml(tx.bank_source) : '<span class="spending-muted">—</span>';
                const cat =
                    tx.direction === 'outgoing'
                        ? escapeHtml(tx.category || 'unclassified')
                        : '<span class="spending-muted">—</span>';
                const muted =
                    (tx.internal_transfer && tx.transfer_pair_id) || tx.insights_excluded === true
                        ? ' search-tx-row--muted spending-insight-tx-muted'
                        : '';
                const amt = Number(tx.amount);
                const amtStr = Number.isFinite(amt) ? amt.toFixed(2) : escapeHtml(tx.amount);
                const dir = String(tx.direction || '');
                const desc = escapeHtml(tx.description || '');
                return `<tr class="search-tx-row border-t${muted}" style="border-color: var(--border-color);" data-tx-id="${escapeHtml(tx.id || '')}" tabindex="0" aria-expanded="false">
                    <td class="search-tx-date px-3 py-2 whitespace-nowrap" data-label="Date">${escapeHtml(tx.date || '')}</td>
                    <td class="search-tx-desc px-3 py-2" data-label="Description">${desc}</td>
                    <td class="search-tx-source px-3 py-2 search-tx-detail" data-label="Source">${src}</td>
                    <td class="search-tx-dir px-3 py-2 whitespace-nowrap search-tx-detail" data-label="Direction">${escapeHtml(dir)}</td>
                    <td class="search-tx-amount px-3 py-2 whitespace-nowrap spending-insight-tx-amount" data-label="Amount (£)">${amtStr}</td>
                    <td class="search-tx-cat px-3 py-2 whitespace-nowrap search-tx-detail" data-label="Category">${cat}</td>
                    <td class="search-tx-month px-3 py-2 whitespace-nowrap search-tx-detail" data-label="Month">${monthLink}</td>
                    <td class="search-tx-toggle px-2 py-2">
                        <span class="search-tx-chevron" aria-hidden="true"></span>
                        <span class="sr-only">Show details</span>
                    </td>
                </tr>`;
            })
            .join('');
        setExpandHint(true);
    }

    function isMobileSearchLayout() {
        return window.matchMedia && window.matchMedia('(max-width: 640px)').matches;
    }

    function setExpandHint(hasRows) {
        const hint = $('tx-search-expand-hint');
        if (!hint) return;
        if (hasRows && isMobileSearchLayout()) {
            hint.classList.remove('hidden');
        } else {
            hint.classList.add('hidden');
        }
    }

    function collapseAllRows(except) {
        document.querySelectorAll('#tx-search-tbody tr.search-tx-row--open').forEach((row) => {
            if (except && row === except) return;
            row.classList.remove('search-tx-row--open');
            row.setAttribute('aria-expanded', 'false');
            const sr = row.querySelector('.search-tx-toggle .sr-only');
            if (sr) sr.textContent = 'Show details';
        });
    }

    function toggleRow(row) {
        if (!row || !isMobileSearchLayout()) return;
        const willOpen = !row.classList.contains('search-tx-row--open');
        collapseAllRows(willOpen ? row : null);
        row.classList.toggle('search-tx-row--open', willOpen);
        row.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
        const sr = row.querySelector('.search-tx-toggle .sr-only');
        if (sr) sr.textContent = willOpen ? 'Hide details' : 'Show details';
    }

    function bindRowExpand() {
        const tbody = $('tx-search-tbody');
        if (!tbody || tbody.dataset.expandBound === '1') return;
        tbody.dataset.expandBound = '1';
        tbody.addEventListener('click', (e) => {
            if (e.target.closest('a.search-tx-month-link')) return;
            const row = e.target.closest('tr.search-tx-row');
            if (!row || !tbody.contains(row)) return;
            toggleRow(row);
        });
        tbody.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            if (e.target.closest('a.search-tx-month-link')) return;
            const row = e.target.closest('tr.search-tx-row');
            if (!row || !tbody.contains(row)) return;
            e.preventDefault();
            toggleRow(row);
        });
        if (window.matchMedia) {
            window.matchMedia('(max-width: 640px)').addEventListener('change', () => {
                if (!isMobileSearchLayout()) {
                    collapseAllRows();
                }
                setExpandHint(!!$('tx-search-tbody') && $('tx-search-tbody').children.length > 0);
            });
        }
    }

    async function runSearch(opts) {
        const options = opts || {};
        const f = readFilters();
        if (!filtersHaveCriterion(f)) {
            lastFilters = f;
            lastTotal = 0;
            currentOffset = 0;
            renderRows([]);
            setRollup([]);
            updatePager(0, 0);
            setStatus('Type a query or set a filter, then search.');
            if (!options.skipUrl) syncUrl(f, 0);
            return;
        }
        if (typeof options.offset === 'number') {
            currentOffset = options.offset;
        }
        lastFilters = f;
        const seq = ++requestSeq;
        setStatus('Searching…');
        const params = buildQuery(f, currentOffset);
        try {
            const resp = await fetch(`/api/spending/transactions/search?${params.toString()}`, {
                credentials: 'same-origin',
                headers: { Accept: 'application/json' },
            });
            const data = await resp.json().catch(() => ({}));
            if (seq !== requestSeq) return;
            if (!resp.ok) {
                setStatus((data && data.error) || 'Search failed. Try again.');
                renderRows([]);
                setRollup([]);
                updatePager(0, 0);
                return;
            }
            if (Array.isArray(data.known_bank_sources)) {
                populateSourceSelect(data.known_bank_sources);
            }
            const rows = data.transactions || [];
            lastTotal = Number(data.total) || 0;
            renderRows(rows);
            setRollup(rows);
            updatePager(lastTotal, currentOffset);
            if (!data.searched) {
                setStatus('Type a query or set a filter, then search.');
            } else if (lastTotal === 0) {
                setStatus('No transactions match. Try a broader query or clear a filter.');
            } else if (lastTotal <= PAGE_SIZE) {
                setStatus(
                    `Found ${lastTotal} transaction${lastTotal === 1 ? '' : 's'} across all months.`
                );
            } else {
                setStatus(
                    `Found ${lastTotal} transactions across all months. Showing ${PAGE_SIZE} per page.`
                );
            }
            if (!options.skipUrl) syncUrl(f, currentOffset);
        } catch (err) {
            if (seq !== requestSeq) return;
            setStatus('Network error while searching. Try again.');
            renderRows([]);
            setRollup([]);
            updatePager(0, 0);
        }
    }

    function scheduleSearch() {
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            currentOffset = 0;
            runSearch({ offset: 0 });
        }, 280);
    }

    function clearForm() {
        [
            'tx-search-q',
            'tx-search-date-from',
            'tx-search-date-to',
            'tx-search-min-amount',
            'tx-search-max-amount',
        ].forEach((id) => {
            const el = $(id);
            if (el) el.value = '';
        });
        ['tx-search-direction', 'tx-search-category', 'tx-search-source'].forEach((id) => {
            const el = $(id);
            if (el) el.value = '';
        });
        currentOffset = 0;
        runSearch({ offset: 0 });
        const q = $('tx-search-q');
        if (q) q.focus();
    }

    function bind() {
        populateSourceSelect(window.KNOWN_BANK_SOURCES || []);
        applyUrlToForm();

        const form = $('tx-search-form');
        if (form) {
            form.addEventListener('submit', (e) => {
                e.preventDefault();
                if (debounceTimer) clearTimeout(debounceTimer);
                currentOffset = 0;
                runSearch({ offset: 0 });
            });
        }

        const q = $('tx-search-q');
        if (q) {
            q.addEventListener('input', scheduleSearch);
        }

        [
            'tx-search-date-from',
            'tx-search-date-to',
            'tx-search-direction',
            'tx-search-category',
            'tx-search-source',
            'tx-search-min-amount',
            'tx-search-max-amount',
        ].forEach((id) => {
            const el = $(id);
            if (!el) return;
            el.addEventListener('change', () => {
                currentOffset = 0;
                runSearch({ offset: 0 });
            });
            if (el.tagName === 'INPUT') {
                el.addEventListener('input', scheduleSearch);
            }
        });

        const clearBtn = $('tx-search-clear');
        if (clearBtn) clearBtn.addEventListener('click', clearForm);

        const prev = $('tx-search-prev');
        if (prev) {
            prev.addEventListener('click', () => {
                currentOffset = Math.max(0, currentOffset - PAGE_SIZE);
                runSearch({ offset: currentOffset });
            });
        }
        const next = $('tx-search-next');
        if (next) {
            next.addEventListener('click', () => {
                currentOffset = currentOffset + PAGE_SIZE;
                runSearch({ offset: currentOffset });
            });
        }

        if (filtersHaveCriterion(readFilters())) {
            runSearch({ offset: currentOffset, skipUrl: true });
        }

        bindRowExpand();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }
})();
