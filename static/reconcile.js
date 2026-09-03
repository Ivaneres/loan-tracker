(function () {
  'use strict';

  function $(id) {
    return document.getElementById(id);
  }

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

  function setStatus(message, isError) {
    const el = $('reconcile-status');
    if (!el) return;
    if (!message) {
      el.classList.add('hidden');
      el.textContent = '';
      el.classList.remove('is-error');
      return;
    }
    el.classList.remove('hidden');
    el.textContent = message;
    el.classList.toggle('is-error', !!isError);
  }

  let month = String(window.RECONCILE_MONTH || '').slice(0, 7);
  let sessionData = null;
  let queueIndex = 0;
  let phase = 'bank';
  let selectedSuggestion = null;
  let acceptedQueue = new Set();

  function api(path, opts) {
    const options = opts || {};
    return fetch(path, options).then(async (res) => {
      let body = null;
      try {
        body = await res.json();
      } catch (e) {
        body = null;
      }
      if (!res.ok) {
        const err = new Error((body && body.error) || res.statusText || 'Request failed');
        err.status = res.status;
        err.body = body;
        throw err;
      }
      return body;
    });
  }

  function rowsById() {
    const map = new Map();
    ((sessionData && sessionData.rows) || []).forEach((r) => map.set(String(r.id), r));
    return map;
  }

  function manualsById() {
    const map = new Map();
    ((sessionData && sessionData.manuals) || []).forEach((m) => map.set(String(m.id), m));
    return map;
  }

  function refreshFromSession(payload) {
    sessionData = payload && payload.session ? payload.session : payload;
    renderAll();
  }

  function loadSession() {
    if (!month) return Promise.resolve();
    setStatus('Loading…');
    return api(`/api/spending/reconcile/${encodeURIComponent(month)}`)
      .then((data) => {
        setStatus('');
        refreshFromSession(data);
      })
      .catch((err) => setStatus(err.message || 'Failed to load', true));
  }

  function activeQueue() {
    return ((sessionData && sessionData.queue) || []).filter((item, idx) => !acceptedQueue.has(String(idx)));
  }

  function renderChip() {
    const chip = $('reconcile-status-chip');
    if (!chip || !sessionData) return;
    const st = sessionData.status || 'staging';
    chip.textContent = st;
    chip.dataset.status = st;
  }

  function renderUploads() {
    const list = $('reconcile-upload-list');
    if (!list) return;
    const uploads = (sessionData && sessionData.uploads) || [];
    if (!uploads.length) {
      list.classList.add('reconcile-upload-list--empty');
      list.innerHTML = '<li class="reconcile-upload-empty">No statements staged yet.</li>';
      return;
    }
    list.classList.remove('reconcile-upload-list--empty');
    list.innerHTML = uploads
      .map((u) => {
        const src = u.bank_source ? ` · ${escapeHtml(u.bank_source)}` : '';
        const range =
          u.period_start && u.period_end
            ? ` · ${escapeHtml(u.period_start)} → ${escapeHtml(u.period_end)}`
            : '';
        const remove = sessionData && sessionData.readonly
          ? ''
          : `<button type="button" class="reconcile-btn reconcile-btn--ghost reconcile-upload-remove" data-remove-upload="${escapeHtml(u.id)}">Remove</button>`;
        return `<li class="reconcile-upload-item" data-upload-id="${escapeHtml(u.id)}">
          <span class="reconcile-upload-meta"><strong>${escapeHtml(u.file_name || 'Statement')}</strong>${src}
          <span class="reconcile-tally-sub">${range} · ${u.row_count || 0} rows</span></span>
          ${remove}
        </li>`;
      })
      .join('');
  }

  function renderTally() {
    const box = $('reconcile-tally');
    const workspace = $('reconcile-workspace');
    const viewAll = $('reconcile-view-all');
    if (!box || !workspace) return;
    const t = (sessionData && sessionData.totals) || null;
    const uploads = (sessionData && sessionData.uploads) || [];
    if (!uploads.length || !t) {
      box.classList.add('hidden');
      workspace.classList.add('hidden');
      if (viewAll) viewAll.classList.add('hidden');
      return;
    }
    box.classList.remove('hidden');
    workspace.classList.remove('hidden');
    if (viewAll) viewAll.classList.remove('hidden');
    const bankPct = t.bank_total > 0 ? Math.round((t.matched_bank_total / t.bank_total) * 100) : 0;
    const manPct = t.manual_total > 0 ? Math.round((t.matched_manual_total / t.manual_total) * 100) : 0;
    const align = t.matched_aligned
      ? `<span class="reconcile-tally-align reconcile-tally-align--ok">✓ ${formatMoney(t.matched_bank_total)} aligned</span>`
      : `<span class="reconcile-tally-align reconcile-tally-align--warn">${formatMoney(t.unmatched_bank_total)} bank · ${formatMoney(t.unmatched_manual_total)} manual gap</span>`;
    box.innerHTML = `
      <div class="reconcile-stat">
        <span class="reconcile-stat-label">Bank statements</span>
        <div class="reconcile-stat-line">
          <span class="reconcile-stat-value">${formatMoney(t.bank_total)}</span>
          <span class="reconcile-stat-count">${t.statement_count || 0} rows</span>
        </div>
        <div class="reconcile-tally-bar"><span style="width:${bankPct}%"></span></div>
        <div class="reconcile-tally-sub">Matched ${formatMoney(t.matched_bank_total)} · Left ${formatMoney(t.unmatched_bank_total)} · Transfers ${formatMoney(t.transfer_total || 0)}</div>
      </div>
      <div class="reconcile-tally-mid">${align}</div>
      <div class="reconcile-stat">
        <span class="reconcile-stat-label">Manual entries</span>
        <div class="reconcile-stat-line">
          <span class="reconcile-stat-value">${formatMoney(t.manual_total)}</span>
          <span class="reconcile-stat-count">${t.manual_count || 0} entries</span>
        </div>
        <div class="reconcile-tally-bar reconcile-tally-bar--manual"><span style="width:${manPct}%"></span></div>
        <div class="reconcile-tally-sub">Matched ${formatMoney(t.matched_manual_total)} · Unclaimed ${formatMoney(t.unmatched_manual_total)} · Excluded ${formatMoney(t.excluded_manual_total || 0)}</div>
      </div>`;
  }

  function renderUnclaimedChip() {
    const wrap = $('reconcile-unclaimed-chip-wrap');
    const chip = $('reconcile-unclaimed-chip');
    if (!wrap || !chip || !sessionData) return;
    const n = (sessionData.totals && sessionData.totals.unmatched_manual_count) || 0;
    if (!sessionData.auto_match_ran) {
      wrap.classList.add('hidden');
      return;
    }
    wrap.classList.remove('hidden');
    if (n === 0) {
      chip.textContent = 'All manuals matched';
      chip.className = 'reconcile-alert-chip reconcile-alert-chip--ok';
    } else {
      chip.textContent = `${n} manual${n === 1 ? '' : 's'} unclaimed`;
      chip.className = 'reconcile-alert-chip reconcile-alert-chip--warn';
    }
  }

  function sourcePill(src) {
    const value = String(src || '').trim();
    if (!value) return '<span class="reconcile-source-pill reconcile-source-pill--empty" aria-hidden="true">—</span>';
    return `<span class="reconcile-source-pill">${escapeHtml(value)}</span>`;
  }

  function txRow(role, name, amount, date, source, extraClass) {
    const cls = extraClass ? ` reconcile-tx--${extraClass}` : '';
    return `<div class="reconcile-tx${cls}">
      <span class="reconcile-tx-role">${escapeHtml(role)}</span>
      <span class="reconcile-tx-name">${escapeHtml(name || '')}</span>
      <span class="reconcile-tx-meta">
        <span class="reconcile-tx-amt">${formatMoney(amount)}</span>
        <span class="reconcile-tx-date">${escapeHtml(date || '')}</span>
        ${sourcePill(source)}
      </span>
    </div>`;
  }

  function setTriageIdle(idle) {
    const bank = $('reconcile-phase-bank');
    if (bank) bank.classList.toggle('reconcile-triage--idle', !!idle);
  }

  function renderFocusItem(item) {
    const focus = $('reconcile-focus');
    const suggestions = $('reconcile-suggestions');
    const actions = $('reconcile-actions');
    const progress = $('reconcile-queue-progress');
    const nav = $('reconcile-queue-nav');
    if (!focus || !item) {
      setTriageIdle(true);
      if (focus) focus.innerHTML = '<p class="reconcile-empty">Queue complete — review unclaimed manuals or confirm.</p>';
      if (suggestions) suggestions.innerHTML = '';
      if (actions) actions.innerHTML = '';
      if (nav) nav.innerHTML = '';
      return;
    }
    setTriageIdle(false);
    const rows = rowsById();
    const manuals = manualsById();
    const q = activeQueue();
    const pos = queueIndex + 1;
    if (progress) progress.textContent = `${pos} of ${q.length}`;

    if (item.kind === 'transfer' || item.kind === 'netted_banks') {
      const ids = item.row_ids || [];
      const legs = ids.map((id) => rows.get(String(id))).filter(Boolean);
      const title = item.kind === 'transfer' ? 'Transfer pair — review' : 'Netted match — review';
      focus.innerHTML = `<div class="reconcile-focus-title">${title}</div>${legs
        .map((leg) => txRow('Bank', leg.description, leg.amount, leg.date, leg.bank_source))
        .join('')}`;
      if (item.kind === 'netted_banks') {
        const mid = (item.manual_ids || [])[0];
        const man = manuals.get(String(mid));
        if (man) {
          focus.innerHTML += txRow('Manual', man.description, man.amount, man.date, '', 'manual');
        }
      }
      if (suggestions) suggestions.innerHTML = '';
      if (actions) {
        actions.innerHTML = `
          <button type="button" class="reconcile-btn reconcile-btn--primary" data-action="accept-queue">Confirm</button>
          <button type="button" class="reconcile-btn reconcile-btn--ghost" data-action="unlink-queue">Not a match</button>`;
      }
    } else if (item.kind === 'matched') {
      const row = rows.get(String(item.row_id));
      const mids = item.manual_ids || [];
      const parts = mids.map((id) => manuals.get(String(id))).filter(Boolean);
      focus.innerHTML = row
        ? `<div class="reconcile-focus-title reconcile-focus-title--matched">Auto / linked match</div>
           ${txRow('Bank', row.description, row.amount, row.date, row.bank_source)}`
        : '';
      if (parts.length) {
        focus.innerHTML += parts
          .map((p) => txRow('Manual', p.description, p.amount, p.date, '', 'manual'))
          .join('');
      }
      if (suggestions) suggestions.innerHTML = '';
      if (actions) {
        actions.innerHTML = `
          <button type="button" class="reconcile-btn reconcile-btn--primary" data-action="accept-queue">Accept</button>
          <button type="button" class="reconcile-btn reconcile-btn--ghost" data-action="unlink-row" data-row-id="${escapeHtml(item.row_id)}">Unlink</button>`;
      }
    } else if (item.kind === 'unmatched') {
      const row = rows.get(String(item.row_id));
      selectedSuggestion = (row && row.suggestions && row.suggestions[0]) || null;
      focus.innerHTML = row
        ? `<div class="reconcile-focus-title">Unmatched</div>
           ${txRow('Bank', row.description, row.amount, row.date, row.bank_source)}`
        : '';
      const sugs = (row && row.suggestions) || [];
      if (suggestions) {
        suggestions.innerHTML = sugs.length
          ? `<p class="reconcile-suggest-label">Suggested matches</p>${sugs
              .map((s, i) => {
                const netted = s.kind === 'netted';
                const label = netted ? `${escapeHtml(s.description)} (netted)` : escapeHtml(s.description);
                const sel = i === 0 ? ' reconcile-suggest-card--selected' : '';
                return `<button type="button" class="reconcile-suggest-card${sel}" data-suggest-idx="${i}">
                  ${label} · ${formatMoney(s.amount)} · ${escapeHtml(s.date || '')}
                </button>`;
              })
              .join('')}`
          : '<p class="reconcile-suggest-empty">No manual suggestions — import as new or exclude.</p>';
      }
      if (actions) {
        actions.innerHTML = `
          <button type="button" class="reconcile-btn reconcile-btn--primary" data-action="link-suggest" data-row-id="${escapeHtml(item.row_id)}">Link match</button>
          <button type="button" class="reconcile-btn reconcile-btn--ghost" data-action="accept-queue">Skip — import as new</button>
          <button type="button" class="reconcile-btn reconcile-btn--ghost" data-action="exclude-row" data-row-id="${escapeHtml(item.row_id)}">Exclude</button>`;
      }
    }

    if (nav) {
      nav.innerHTML = `
        <button type="button" class="reconcile-btn reconcile-btn--ghost" data-queue-prev ${queueIndex <= 0 ? 'disabled' : ''}>← Prev</button>
        <button type="button" class="reconcile-btn reconcile-btn--ghost" data-queue-next ${queueIndex >= q.length - 1 ? 'disabled' : ''}>Next →</button>`;
    }
  }

  function renderManualPhase() {
    const list = $('reconcile-unclaimed-list');
    if (!list || !sessionData) return;
    const manuals = (sessionData.manuals || []).filter((m) => m.status === 'unclaimed');
    if (!manuals.length) {
      list.innerHTML = '<li class="reconcile-unclaimed-empty">No unclaimed manuals.</li>';
      return;
    }
    list.innerHTML = manuals
      .map(
        (m) => `<li class="reconcile-unclaimed-item">
        <span>${escapeHtml(m.description)} · ${formatMoney(m.amount)} · ${escapeHtml(m.date)}</span>
        <span class="reconcile-unclaimed-actions">
          <button type="button" class="reconcile-btn reconcile-btn--ghost" data-keep-manual="${escapeHtml(m.id)}">Keep unclaimed</button>
          <button type="button" class="reconcile-btn reconcile-btn--danger" data-exclude-manual="${escapeHtml(m.id)}">Exclude from reconcile</button>
        </span>
      </li>`,
      )
      .join('');
    const reviewed = $('reconcile-reviewed-unclaimed');
    if (reviewed) reviewed.checked = !!sessionData.reviewed_unclaimed;
  }

  function renderSummaryPhase() {
    const body = $('reconcile-summary-body');
    const t = (sessionData && sessionData.totals) || {};
    if (!body) return;
    body.innerHTML = `
      <p>Bank: ${formatMoney(t.bank_total)} · Manual: ${formatMoney(t.manual_total)}</p>
      <p>Matched: ${formatMoney(t.matched_bank_total)} · Unmatched bank: ${formatMoney(t.unmatched_bank_total)}</p>
      <p>Unclaimed manuals: ${t.unmatched_manual_count || 0} (${formatMoney(t.unmatched_manual_total || 0)}) · Excluded: ${t.excluded_manual_count || 0}</p>`;
  }

  function renderPhases() {
    const bank = $('reconcile-phase-bank');
    const manuals = $('reconcile-phase-manuals');
    const summary = $('reconcile-phase-summary');
    if (!bank || !manuals || !summary) return;
    bank.classList.toggle('hidden', phase !== 'bank');
    manuals.classList.toggle('hidden', phase !== 'manuals');
    summary.classList.toggle('hidden', phase !== 'summary');
    const progress = $('reconcile-queue-progress');
    if (phase === 'bank') {
      const q = activeQueue();
      if (queueIndex >= q.length) queueIndex = Math.max(0, q.length - 1);
      if (q.length === 0 && sessionData && sessionData.auto_match_ran) {
        phase = 'manuals';
        renderPhases();
        return;
      }
      renderFocusItem(q[queueIndex] || null);
    } else if (phase === 'manuals') {
      if (progress) progress.textContent = 'Unclaimed manuals';
      renderManualPhase();
    } else {
      if (progress) progress.textContent = 'Summary';
      renderSummaryPhase();
    }
  }

  function renderActionsBar() {
    const autoBtn = $('reconcile-auto-match-btn');
    const confirmBtn = $('reconcile-confirm-btn');
    const bulkBtn = $('reconcile-bulk-accept-btn');
    const uploads = (sessionData && sessionData.uploads) || [];
    if (autoBtn) autoBtn.disabled = !uploads.length || sessionData.status === 'imported';
    if (confirmBtn) confirmBtn.disabled = !sessionData || !sessionData.auto_match_ran || sessionData.status === 'imported';
    if (bulkBtn) {
      const n = (sessionData && sessionData.totals && sessionData.totals.auto_matched_count) || 0;
      bulkBtn.classList.toggle('hidden', !n || sessionData.status === 'imported');
      bulkBtn.textContent = `Accept all auto-matched (${n})`;
    }
  }

  function rowMatchLabel(row) {
    if (!row) return '—';
    if (row.transfer_pair) return 'Transfer';
    if (row.manual_match) return 'Matched';
    if (row.ledger_duplicate) return 'Duplicate';
    if (row.include === false) return 'Excluded';
    return 'Unmatched';
  }

  function renderViewAll() {
    const panel = $('reconcile-view-all-panel');
    if (!panel || panel.classList.contains('hidden')) return;
    const rows = (sessionData && sessionData.rows) || [];
    if (!rows.length) {
      panel.innerHTML = '<p class="reconcile-empty">No statement rows yet.</p>';
      return;
    }
    panel.innerHTML = `<table class="reconcile-rows-table">
      <thead><tr><th>Source</th><th>Description</th><th>Amount</th><th>Date</th><th>Match</th></tr></thead>
      <tbody>${rows
        .map(
          (r) => `<tr>
            <td>${escapeHtml(r.bank_source || '')}</td>
            <td>${escapeHtml(r.description || '')}</td>
            <td class="reconcile-tx-amt">${formatMoney(r.amount)}</td>
            <td>${escapeHtml(r.date || '')}</td>
            <td>${rowMatchLabel(r)}</td>
          </tr>`,
        )
        .join('')}</tbody>
    </table>`;
  }

  function renderReadonly() {
    const readonly = !!(sessionData && sessionData.readonly);
    document.body.classList.toggle('reconcile-readonly', readonly);
    const lead = $('reconcile-lead');
    if (lead) {
      lead.textContent = readonly
        ? 'This month is imported. You can review the session; uploads are locked.'
        : 'Upload every statement for the month, run auto-match, review the queue, then confirm.';
    }
    ['reconcile-upload-btn', 'reconcile-auto-match-btn', 'reconcile-confirm-btn', 'reconcile-file', 'reconcile-source', 'reconcile-period-start', 'reconcile-period-end', 'reconcile-range-toggle', 'reconcile-range-reset'].forEach((id) => {
      const el = $(id);
      if (el) el.disabled = readonly;
    });
  }

  function renderAll() {
    renderChip();
    renderReadonly();
    renderUploads();
    renderTally();
    renderUnclaimedChip();
    renderPhases();
    renderActionsBar();
    renderViewAll();
  }

  function post(path, body) {
    return api(`/api/spending/reconcile/${encodeURIComponent(month)}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }).then(refreshFromSession);
  }

  function monthRangeFromValue(ym) {
    const m = String(ym || '').slice(0, 7);
    if (m.length !== 7 || m[4] !== '-') return null;
    const y = Number(m.slice(0, 4));
    const mo = Number(m.slice(5, 7));
    if (!y || !mo) return null;
    const start = `${m}-01`;
    const last = new Date(y, mo, 0).getDate();
    const end = `${m}-${String(last).padStart(2, '0')}`;
    return { start, end };
  }

  function syncRangeFromMonth() {
    const range = monthRangeFromValue(month);
    const startEl = $('reconcile-period-start');
    const endEl = $('reconcile-period-end');
    if (!range || !startEl || !endEl) return;
    startEl.value = range.start;
    endEl.value = range.end;
  }

  function bindEvents() {
    const monthInput = $('reconcile-month');
    if (monthInput) {
      monthInput.addEventListener('change', () => {
        month = monthInput.value.slice(0, 7);
        window.location.href = `/spending/reconcile?month=${encodeURIComponent(month)}`;
      });
    }

    const sourceList = $('reconcile-source-list');
    if (sourceList && window.KNOWN_BANK_SOURCES) {
      sourceList.innerHTML = window.KNOWN_BANK_SOURCES.map((s) => `<option value="${escapeHtml(s)}">`).join('');
    }

    const fileInputEl = $('reconcile-file');
    const fileNameEl = $('reconcile-file-name');
    if (fileInputEl && fileNameEl) {
      fileInputEl.addEventListener('change', () => {
        const f = fileInputEl.files && fileInputEl.files[0];
        fileNameEl.textContent = f ? f.name : 'No file selected';
      });
    }

    const viewAllToggle = $('reconcile-view-all-toggle');
    const viewAllPanel = $('reconcile-view-all-panel');
    if (viewAllToggle && viewAllPanel) {
      viewAllToggle.addEventListener('click', () => {
        const open = viewAllPanel.classList.contains('hidden');
        viewAllPanel.classList.toggle('hidden', !open);
        viewAllToggle.setAttribute('aria-expanded', String(open));
        viewAllToggle.textContent = open ? 'Hide all rows' : 'View all rows';
        if (open) renderViewAll();
      });
    }

    const rangeToggle = $('reconcile-range-toggle');
    const rangeFields = $('reconcile-range-fields');
    if (rangeToggle && rangeFields) {
      rangeToggle.addEventListener('click', () => {
        const open = rangeFields.classList.contains('hidden');
        rangeFields.classList.toggle('hidden', !open);
        rangeToggle.setAttribute('aria-expanded', String(open));
        rangeToggle.textContent = open ? 'Hide date range' : 'Adjust date range';
      });
    }
    const rangeReset = $('reconcile-range-reset');
    if (rangeReset) {
      rangeReset.addEventListener('click', () => syncRangeFromMonth());
    }
    syncRangeFromMonth();

    const uploadBtn = $('reconcile-upload-btn');
    if (uploadBtn) {
      uploadBtn.addEventListener('click', () => {
        const fileInput = $('reconcile-file');
        const sourceInput = $('reconcile-source');
        if (!fileInput || !fileInput.files || !fileInput.files[0]) {
          setStatus('Choose a statement file first.', true);
          return;
        }
        const fd = new FormData();
        fd.append('file', fileInput.files[0]);
        if (sourceInput && sourceInput.value.trim()) fd.append('bank_source', sourceInput.value.trim());
        const startEl = $('reconcile-period-start');
        const endEl = $('reconcile-period-end');
        if (startEl && startEl.value) fd.append('period_start', startEl.value);
        if (endEl && endEl.value) fd.append('period_end', endEl.value);
        setStatus('Extracting…');
        api(`/api/spending/reconcile/${encodeURIComponent(month)}/upload`, { method: 'POST', body: fd })
          .then((data) => {
            refreshFromSession(data);
            const uploads = (data && data.session && data.session.uploads) || [];
            const added = uploads[uploads.length - 1];
            if (added && added.file_name) {
              const n = Number(added.row_count) || 0;
              setStatus(`Added ${added.file_name} (${n} row${n === 1 ? '' : 's'}).`);
            } else {
              setStatus('Statement added.');
            }
            fileInput.value = '';
            const fileName = $('reconcile-file-name');
            if (fileName) fileName.textContent = 'No file selected';
          })
          .catch((err) => setStatus(err.message || 'Upload failed', true));
      });
    }

    const uploadList = $('reconcile-upload-list');
    if (uploadList) {
      uploadList.addEventListener('click', (ev) => {
        const btn = ev.target.closest('[data-remove-upload]');
        if (!btn) return;
        const id = btn.getAttribute('data-remove-upload');
        api(`/api/spending/reconcile/${encodeURIComponent(month)}/upload/${encodeURIComponent(id)}`, {
          method: 'DELETE',
        })
          .then((data) => {
            refreshFromSession(data);
            setStatus('Upload removed.');
          })
          .catch((err) => setStatus(err.message || 'Remove failed', true));
      });
    }

    const autoBtn = $('reconcile-auto-match-btn');
    if (autoBtn) {
      autoBtn.addEventListener('click', () => {
        setStatus('Running auto-match…');
        post('/auto-match')
          .then(() => {
            queueIndex = 0;
            acceptedQueue = new Set();
            phase = 'bank';
            setStatus('Auto-match complete.');
          })
          .catch((err) => setStatus(err.message || 'Auto-match failed', true));
      });
    }

    const bulkBtn = $('reconcile-bulk-accept-btn');
    if (bulkBtn) {
      bulkBtn.addEventListener('click', () => {
        const q = (sessionData && sessionData.queue) || [];
        q.forEach((item, idx) => {
          if (item.kind === 'matched') acceptedQueue.add(String(idx));
        });
        renderPhases();
      });
    }

    const workspace = $('reconcile-workspace');
    if (workspace) {
      workspace.addEventListener('click', (ev) => {
        const suggest = ev.target.closest('[data-suggest-idx]');
        if (suggest) {
          document.querySelectorAll('.reconcile-suggest-card').forEach((el) => el.classList.remove('reconcile-suggest-card--selected'));
          suggest.classList.add('reconcile-suggest-card--selected');
          const idx = Number(suggest.getAttribute('data-suggest-idx'));
          const q = activeQueue();
          const item = q[queueIndex];
          const row = item && rowsById().get(String(item.row_id));
          selectedSuggestion = row && row.suggestions ? row.suggestions[idx] : null;
          return;
        }

        if (ev.target.closest('[data-queue-prev]')) {
          queueIndex = Math.max(0, queueIndex - 1);
          renderPhases();
          return;
        }
        if (ev.target.closest('[data-queue-next]')) {
          const q = activeQueue();
          queueIndex = Math.min(q.length - 1, queueIndex + 1);
          renderPhases();
          return;
        }

        const accept = ev.target.closest('[data-action="accept-queue"]');
        if (accept) {
          acceptedQueue.add(String(queueIndex));
          queueIndex = Math.min(queueIndex, activeQueue().length - 1);
          renderPhases();
          return;
        }

        const unlink = ev.target.closest('[data-action="unlink-row"]');
        if (unlink) {
          const rowId = unlink.getAttribute('data-row-id');
          post('/unlink-manual', { row_id: rowId }).catch((err) => setStatus(err.message, true));
          return;
        }

        const unlinkQ = ev.target.closest('[data-action="unlink-queue"]');
        if (unlinkQ) {
          const q = activeQueue();
          const item = q[queueIndex];
          if (!item) return;
          const rowId = item.row_id || (item.row_ids && item.row_ids[0]);
          if (item.kind === 'transfer') {
            post('/unlink-transfer', { row_id: rowId }).catch((err) => setStatus(err.message, true));
          } else {
            post('/unlink-manual', { row_id: rowId }).catch((err) => setStatus(err.message, true));
          }
          return;
        }

        const link = ev.target.closest('[data-action="link-suggest"]');
        if (link) {
          const rowId = link.getAttribute('data-row-id');
          const sug = selectedSuggestion;
          if (!sug) {
            setStatus('Select a suggestion first.', true);
            return;
          }
          const manualIds = sug.ids || (sug.id ? String(sug.id).split('+') : []);
          post('/link-manual', { row_id: rowId, manual_ids: manualIds })
            .then(() => {
              acceptedQueue.add(String(queueIndex));
              renderPhases();
            })
            .catch((err) => setStatus(err.message, true));
          return;
        }

        const exclude = ev.target.closest('[data-action="exclude-row"]');
        if (exclude) {
          const rowId = exclude.getAttribute('data-row-id');
          api(`/api/spending/reconcile/${encodeURIComponent(month)}/row/${encodeURIComponent(rowId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ include: false }),
          })
            .then(refreshFromSession)
            .then(() => {
              acceptedQueue.add(String(queueIndex));
              renderPhases();
            })
            .catch((err) => setStatus(err.message, true));
        }
      });
    }

    const unclaimedList = $('reconcile-unclaimed-list');
    if (unclaimedList) {
      unclaimedList.addEventListener('click', (ev) => {
        const ex = ev.target.closest('[data-exclude-manual]');
        if (ex) {
          post('/exclude-manual', { manual_id: ex.getAttribute('data-exclude-manual') }).catch((err) =>
            setStatus(err.message, true),
          );
          return;
        }
        const keep = ev.target.closest('[data-keep-manual]');
        if (keep) {
          post('/keep-manual', { manual_id: keep.getAttribute('data-keep-manual') }).catch((err) =>
            setStatus(err.message, true),
          );
        }
      });
    }

    const reviewed = $('reconcile-reviewed-unclaimed');
    if (reviewed) {
      reviewed.addEventListener('change', () => {
        post('/review-unclaimed', { reviewed: reviewed.checked }).catch(() => {});
      });
    }

    const toSummary = $('reconcile-to-summary-btn');
    if (toSummary) {
      toSummary.addEventListener('click', () => {
        phase = 'summary';
        renderPhases();
      });
    }

    const confirmBtn = $('reconcile-confirm-btn');
    const finalConfirm = $('reconcile-final-confirm-btn');
    const doConfirm = () => {
      setStatus('Importing…');
      post('/confirm')
        .then(() => setStatus('Import complete.'))
        .catch((err) => setStatus(err.message || 'Confirm failed', true));
    };
    if (confirmBtn) confirmBtn.addEventListener('click', () => {
      phase = 'manuals';
      renderPhases();
    });
    if (finalConfirm) finalConfirm.addEventListener('click', doConfirm);

    const chip = $('reconcile-unclaimed-chip');
    if (chip) {
      chip.addEventListener('click', () => {
        phase = 'manuals';
        renderPhases();
      });
    }
  }

  bindEvents();
  loadSession();
})();
