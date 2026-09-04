(function () {
  'use strict';

  function $(id) {
    return document.getElementById(id);
  }

  function formatMoney(amount) {
    const n = Number(amount) || 0;
    return `£${n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  function round2(n) {
    return Math.round((Number(n) || 0) * 100) / 100;
  }

  function currentMonth() {
    const input = $('reconcile-month');
    const raw = (input && input.value) || window.RECONCILE_MONTH || month || '';
    return String(raw).slice(0, 7);
  }

  function monthLabel(ym) {
    const m = String(ym || '').slice(0, 7);
    if (m.length !== 7) return 'this month';
    const d = new Date(Number(m.slice(0, 4)), Number(m.slice(5, 7)) - 1, 1);
    if (Number.isNaN(d.getTime())) return 'this month';
    return d.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
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
  let cursor = 0;
  let step = 'upload';
  let selectedSuggestion = null;
  let seenOrder = [];
  let decidedKeys = new Set();
  let undoStack = [];

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

  function resetReviewState() {
    seenOrder = [];
    decidedKeys = new Set();
    undoStack = [];
    cursor = 0;
    selectedSuggestion = null;
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
        sessionData = data && data.session ? data.session : data;
        step = deriveInitialStep();
        renderAll();
      })
      .catch((err) => setStatus(err.message || 'Failed to load', true));
  }

  function unclaimedManuals() {
    return ((sessionData && sessionData.manuals) || []).filter((m) => m.status === 'unclaimed' || m.status === 'excluded' || m.status === 'kept');
  }

  function leftoverCount() {
    return ((sessionData && sessionData.totals && sessionData.totals.unmatched_manual_count) || 0);
  }

  function suggestionIsExact(row, s) {
    if (!row || !s) return false;
    if (s.exact_amount === true) return true;
    if (s.exact_amount === false) return false;
    return Math.abs(round2(s.amount) - round2(row.amount)) < 0.005;
  }

  function exactSuggestions(row) {
    return ((row && row.suggestions) || []).filter((s) => suggestionIsExact(row, s));
  }

  function itemKey(item) {
    if (!item) return '';
    if (item.item_key) return String(item.item_key);
    if (item.kind === 'transfer') {
      const ids = (item.row_ids || []).map(String).slice().sort();
      return `transfer:${item.pair_key || ids.join('+')}`;
    }
    if (item.kind === 'netted_banks') {
      return `netted:${item.group_key || (item.row_ids || []).map(String).slice().sort().join('+')}`;
    }
    return `${item.kind}:${item.row_id || ''}`;
  }

  function needsDecision(item) {
    if (!item) return false;
    if (item.kind === 'transfer' || item.kind === 'netted_banks') return true;
    if (item.kind === 'unmatched') {
      const row = rowsById().get(String(item.row_id));
      if (!row || row.include === false) return false;
      if (String(row.reconcile_mark || '') === 'bill') return false;
      return exactSuggestions(row).length > 0;
    }
    return false;
  }

  function allReviewItems() {
    const items = ((sessionData && sessionData.queue) || []).filter(needsDecision);
    const rank = (item) => {
      if (item.kind === 'unmatched') return 0;
      if (item.kind === 'transfer') return 1;
      return 2;
    };
    return items.slice().sort((a, b) => rank(a) - rank(b));
  }

  function syncSeenOrder() {
    const items = allReviewItems();
    const keyToItem = new Map(items.map((it) => [itemKey(it), it]));
    const live = new Set(keyToItem.keys());
    items.forEach((it) => {
      const k = itemKey(it);
      if (!seenOrder.includes(k)) seenOrder.push(k);
    });
    seenOrder = seenOrder.filter((k) => live.has(k) || decidedKeys.has(k));
    return { items, keyToItem, live };
  }

  function remainingEntries() {
    const { keyToItem } = syncSeenOrder();
    return seenOrder
      .filter((k) => !decidedKeys.has(k) && keyToItem.has(k))
      .map((k) => ({ key: k, item: keyToItem.get(k) }));
  }

  function currentReviewEntry() {
    const rem = remainingEntries();
    if (!rem.length) return null;
    if (cursor >= rem.length) cursor = Math.max(0, rem.length - 1);
    if (cursor < 0) cursor = 0;
    return rem[cursor] || null;
  }

  function progressCounts() {
    const rem = remainingEntries();
    const done = decidedKeys.size;
    const total = done + rem.length;
    const pos = rem.length ? cursor + 1 : 0;
    return { current: done + pos, total, remaining: rem.length, done };
  }

  function markDecided(key) {
    if (key) decidedKeys.add(key);
    cursor = 0;
  }

  function rollbackDecide(key) {
    decidedKeys.delete(key);
  }

  function afterDecideOrStay() {
    if (!remainingEntries().length) {
      goStep('confirm');
      return;
    }
    renderAll();
  }

  function deriveInitialStep() {
    if (!sessionData) return 'upload';
    if (sessionData.readonly) return 'confirm';
    if (!sessionData.auto_match_ran) return 'upload';
    if (remainingEntries().length) return 'review';
    return 'confirm';
  }

  function goStep(next) {
    step = next;
    if (step === 'review' && !remainingEntries().length) {
      step = 'confirm';
    }
    renderAll();
  }

  function renderChip() {
    const chip = $('reconcile-status-chip');
    if (!chip || !sessionData) return;
    const st = sessionData.status || 'staging';
    chip.dataset.status = st;
    if (sessionData.readonly || st === 'imported') {
      chip.textContent = 'Imported';
      chip.classList.remove('hidden');
      return;
    }
    chip.classList.add('hidden');
    chip.textContent = '';
  }

  function renderSteps() {
    const section = document.querySelector('.reconcile-section');
    if (section) section.dataset.step = step;
    document.querySelectorAll('#reconcile-steps .reconcile-steps-item').forEach((el) => {
      const key = el.getAttribute('data-go-step');
      el.classList.toggle('is-current', key === step);
      el.classList.toggle('is-done', (key === 'upload' && step !== 'upload') || (key === 'review' && step === 'confirm'));
    });
    const upload = $('reconcile-step-upload');
    const review = $('reconcile-step-review');
    const confirm = $('reconcile-step-confirm');
    if (upload) upload.classList.toggle('hidden', step !== 'upload');
    if (review) review.classList.toggle('hidden', step !== 'review');
    if (confirm) confirm.classList.toggle('hidden', step !== 'confirm');
  }

  function renderLead() {
    const lead = $('reconcile-lead');
    if (!lead) return;
    const label = monthLabel(currentMonth());
    if (sessionData && sessionData.readonly) {
      lead.textContent = `${label} is already imported. You can review the totals below.`;
      return;
    }
    if (step === 'upload') {
      lead.textContent = `Add every bank statement for ${label}.`;
    } else if (step === 'review') {
      lead.textContent = 'Check exact matches, transfers, and combined rows. Near-misses wait until import.';
    } else {
      lead.textContent = `Last check before importing into ${label}.`;
    }
  }

  function renderUploads() {
    const list = $('reconcile-upload-list');
    if (!list) return;
    const uploads = (sessionData && sessionData.uploads) || [];
    if (!uploads.length) {
      list.classList.add('reconcile-upload-list--empty');
      list.innerHTML = `<li class="reconcile-upload-empty">No files yet — add each bank statement for ${escapeHtml(monthLabel(currentMonth()))}.</li>`;
      return;
    }
    list.classList.remove('reconcile-upload-list--empty');
    list.innerHTML = uploads
      .map((u) => {
        const bits = [];
        if (u.bank_source) bits.push(escapeHtml(u.bank_source));
        if (u.period_start && u.period_end) {
          bits.push(`${escapeHtml(u.period_start)} → ${escapeHtml(u.period_end)}`);
        }
        bits.push(`${u.row_count || 0} rows`);
        const remove = sessionData && sessionData.readonly
          ? ''
          : `<button type="button" class="reconcile-btn reconcile-btn--ghost reconcile-upload-remove" data-remove-upload="${escapeHtml(u.id)}">Remove</button>`;
        return `<li class="reconcile-upload-item" data-upload-id="${escapeHtml(u.id)}">
          <span class="reconcile-upload-meta"><strong>${escapeHtml(u.file_name || 'Statement')}</strong>
          <span class="reconcile-tally-sub">${bits.join(' · ')}</span></span>
          ${remove}
        </li>`;
      })
      .join('');
  }

  function formatDay(iso) {
    const raw = String(iso || '').slice(0, 10);
    const d = new Date(`${raw}T12:00:00`);
    if (Number.isNaN(d.getTime())) return raw;
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  }

  function renderRecap() {
    const box = $('reconcile-recap');
    if (!box || !sessionData) return;
    box.classList.toggle('hidden', step !== 'review');
    if (step !== 'review') {
      box.innerHTML = '';
      return;
    }
    const t = sessionData.totals || {};
    const matched = Number(t.auto_matched_count || 0) + Number(t.user_matched_count || 0);
    const entry = currentReviewEntry();
    const kind = entry && entry.item && entry.item.kind;
    let now = 'Check anything that looks uncertain';
    if (kind === 'transfer') now = 'Check this transfer';
    else if (kind === 'netted_banks') now = 'Check this combined match';
    else if (kind === 'unmatched') now = 'Does this match your spending?';
    const done = matched ? `<p class="reconcile-recap-note">${matched} already matched</p>` : '';
    box.innerHTML = `<p class="reconcile-recap-hero">${now}</p>${done}`;
  }

  function sourcePill(src) {
    const value = String(src || '').trim();
    if (!value) return '<span class="reconcile-source-pill reconcile-source-pill--empty" aria-hidden="true">—</span>';
    return `<span class="reconcile-source-pill">${escapeHtml(value)}</span>`;
  }

  function txRow(role, name, amount, date, source, extraClass, flags) {
    const opts = flags || {};
    const cls = extraClass ? ` reconcile-tx--${extraClass}` : '';
    const amtCls = opts.amountDiff ? ' reconcile-tx-amt--diff' : '';
    const dateCls = opts.dateDiff ? ' reconcile-tx-date--diff' : '';
    const dateLabel = opts.prettyDate ? formatDay(date) : (date || '');
    return `<div class="reconcile-tx${cls}">
      <span class="reconcile-tx-role">${escapeHtml(role)}</span>
      <span class="reconcile-tx-name">${escapeHtml(name || '')}</span>
      <span class="reconcile-tx-meta">
        <span class="reconcile-tx-amt${amtCls}">${formatMoney(amount)}</span>
        <span class="reconcile-tx-date${dateCls}">${escapeHtml(dateLabel)}</span>
        ${sourcePill(source)}
      </span>
    </div>`;
  }

  function setTriageIdle(idle) {
    const bank = $('reconcile-phase-bank');
    if (bank) bank.classList.toggle('reconcile-triage--idle', !!idle);
  }

  function snapshotItem(item) {
    return {
      kind: item.kind,
      row_id: item.row_id,
      row_ids: (item.row_ids || []).slice(),
      manual_ids: (item.manual_ids || []).slice(),
      pair_key: item.pair_key,
      group_key: item.group_key,
      item_key: item.item_key,
    };
  }

  function renderFocusItem(entry) {
    const focus = $('reconcile-focus');
    const suggestions = $('reconcile-suggestions');
    const actions = $('reconcile-actions');
    const nav = $('reconcile-queue-nav');
    const item = entry && entry.item;
    if (!focus || !item) {
      setTriageIdle(true);
      if (focus) focus.innerHTML = '';
      if (suggestions) suggestions.innerHTML = '';
      if (actions) actions.innerHTML = '';
      if (nav) nav.innerHTML = '';
      return;
    }
    setTriageIdle(false);
    const rows = rowsById();
    const manuals = manualsById();
    const progress = progressCounts();
    const rem = remainingEntries();

    if (item.kind === 'transfer' || item.kind === 'netted_banks') {
      const ids = item.row_ids || [];
      const legs = ids.map((id) => rows.get(String(id))).filter(Boolean);
      const title = item.kind === 'transfer' ? 'Looks like a transfer' : 'Several statement rows add up to this';
      const pretty = { prettyDate: true };
      focus.innerHTML = `<div class="reconcile-focus-title">${title}</div>${legs
        .map((leg) => txRow('Bank', leg.description, leg.amount, leg.date, leg.bank_source, '', pretty))
        .join('')}`;
      if (item.kind === 'netted_banks') {
        const mid = (item.manual_ids || [])[0];
        const man = manuals.get(String(mid));
        if (man) {
          focus.innerHTML += txRow('Spending', man.description, man.amount, man.date, '', 'manual', pretty);
        }
      }
      if (suggestions) suggestions.innerHTML = '';
      if (actions) {
        actions.innerHTML = `
          <button type="button" class="reconcile-btn reconcile-btn--primary" data-action="accept-queue">Looks right</button>
          <button type="button" class="reconcile-btn reconcile-btn--ghost" data-action="unlink-queue">Not a match</button>`;
      }
    } else if (item.kind === 'unmatched') {
      const row = rows.get(String(item.row_id));
      const sugs = exactSuggestions(row);
      selectedSuggestion = sugs[0] || null;
      focus.innerHTML = row
        ? `<div class="reconcile-focus-title">Possible match</div>
           ${txRow('Bank', row.description, row.amount, row.date, row.bank_source, '', { prettyDate: true })}`
        : '';
      if (suggestions) {
        suggestions.innerHTML = sugs.length
          ? `<p class="reconcile-suggest-label">Your spending</p>${sugs
              .map((s, i) => {
                const netted = s.kind === 'netted';
                const label = netted ? `${escapeHtml(s.description)} (combined)` : escapeHtml(s.description);
                const sel = i === 0 ? ' reconcile-suggest-card--selected' : '';
                return `<button type="button" class="reconcile-suggest-card${sel}" data-suggest-idx="${i}">
                  ${label} · ${formatMoney(s.amount)} · ${escapeHtml(s.date || '')}
                </button>`;
              })
              .join('')}`
          : '';
      }
      if (actions) {
        actions.innerHTML = `
          <button type="button" class="reconcile-btn reconcile-btn--primary" data-action="link-suggest" data-row-id="${escapeHtml(item.row_id)}">Looks right</button>
          <button type="button" class="reconcile-btn reconcile-btn--ghost" data-action="accept-queue">Not a match</button>
          <button type="button" class="reconcile-btn reconcile-btn--ghost" data-action="mark-bill" data-row-id="${escapeHtml(item.row_id)}">This is a bill</button>`;
      }
    }

    if (nav) {
      const canUndo = undoStack.length > 0;
      const backBtn = canUndo
        ? `<button type="button" class="reconcile-btn reconcile-btn--ghost" data-queue-undo>Undo</button>`
        : `<button type="button" class="reconcile-btn reconcile-btn--ghost" data-queue-prev ${cursor <= 0 ? 'disabled' : ''}>← Prev</button>`;
      nav.innerHTML = `<span class="reconcile-queue-progress">${progress.current} of ${progress.total}</span>
        ${backBtn}
        <button type="button" class="reconcile-btn reconcile-btn--ghost" data-queue-next ${cursor >= rem.length - 1 ? 'disabled' : ''}>Skip →</button>`;
    }
  }

  function leftoverChoiceButtons(m) {
    const ignored = m.status === 'excluded';
    const kept = m.status === 'kept' || m.status === 'unclaimed';
    const keepCls = kept && !ignored ? ' is-selected' : '';
    const ignoreCls = ignored ? ' is-selected' : '';
    return `<button type="button" class="reconcile-btn reconcile-btn--ghost reconcile-choice-btn${keepCls}" data-keep-manual="${escapeHtml(m.id)}">Keep</button>
      <button type="button" class="reconcile-btn reconcile-btn--danger reconcile-choice-btn${ignoreCls}" data-exclude-manual="${escapeHtml(m.id)}">Ignore</button>`;
  }

  function summaryLine(row, actionsHtml) {
    return `<li class="reconcile-unclaimed-item">
      <span class="reconcile-unclaimed-copy">
        <strong>${escapeHtml(row.description || '')}</strong>
        <span class="reconcile-unclaimed-meta">${formatMoney(row.amount)} · ${escapeHtml(formatDay(row.date))}${row.bank_source ? ` · ${escapeHtml(row.bank_source)}` : ''}</span>
      </span>
      ${actionsHtml || ''}
    </li>`;
  }

  function matchedEntries() {
    const rows = (sessionData && sessionData.rows) || [];
    const manuals = manualsById();
    const byId = rowsById();
    const seenNet = new Set();
    const out = [];
    rows.forEach((row) => {
      if (row.include === false || row.ledger_duplicate) return;
      if (row.transfer_pair) return;
      const mm = row.manual_match || {};
      const mids = mm.manual_ids || [];
      if (!mids.length) return;
      const spends = mids.map((id) => manuals.get(String(id))).filter(Boolean).map((m) => ({
        description: m.description,
        amount: m.amount,
        date: m.date,
      }));
      if (mm.kind === 'netted_banks') {
        const gk = String(mm.group_key || '');
        if (gk && seenNet.has(gk)) return;
        if (gk) seenNet.add(gk);
        const banks = (mm.row_ids || [row.id]).map((id) => byId.get(String(id))).filter(Boolean).map((leg) => ({
          description: leg.description,
          amount: leg.amount,
          date: leg.date,
          bank_source: leg.bank_source,
        }));
        out.push({ date: (banks[0] && banks[0].date) || row.date, banks, spends });
        return;
      }
      out.push({
        date: row.date,
        banks: [{ description: row.description, amount: row.amount, date: row.date, bank_source: row.bank_source }],
        spends,
      });
    });
    out.sort((a, b) => String(a.date || '').localeCompare(String(b.date || '')));
    return out;
  }

  function matchedItemHtml(m) {
    const banks = m.banks || [];
    const spends = m.spends || [];
    const bankTotal = banks.reduce((sum, b) => sum + round2(b.amount), 0);
    const spendTotal = spends.reduce((sum, s) => sum + round2(s.amount), 0);
    const amountDiff = Math.abs(bankTotal - spendTotal) >= 0.005;
    const bankDates = new Set(banks.map((b) => String(b.date || '').slice(0, 10)).filter(Boolean));
    const spendDates = new Set(spends.map((s) => String(s.date || '').slice(0, 10)).filter(Boolean));
    const dateDiff = [...bankDates].some((d) => !spendDates.has(d)) || [...spendDates].some((d) => !bankDates.has(d));
    const flags = { prettyDate: true, amountDiff, dateDiff };
    const bankRows = banks.map((b) => txRow('Bank', b.description, b.amount, b.date, b.bank_source, '', flags)).join('');
    const spendRows = spends.length
      ? spends.map((s) => txRow('Spending', s.description, s.amount, s.date, '', 'manual', flags)).join('')
      : txRow('Spending', 'Spending', spendTotal, '', '', 'manual', flags);
    return `<li class="reconcile-match-pair">${bankRows}${spendRows}</li>`;
  }

  function matchedSampleHtml(items) {
    if (!items.length) {
      return '<p class="reconcile-unclaimed-empty">No spending matches yet.</p>';
    }
    const sample = items.slice(0, 5);
    const rest = items.slice(5);
    let html = `<ul class="reconcile-unclaimed-list">${sample.map(matchedItemHtml).join('')}</ul>`;
    if (rest.length) {
      html += `<details class="reconcile-matched-more">
        <summary>Show all ${items.length} matches</summary>
        <ul class="reconcile-unclaimed-list">${rest.map(matchedItemHtml).join('')}</ul>
      </details>`;
    }
    return html;
  }

  function renderSummaryPhase() {
    const body = $('reconcile-summary-body');
    const t = (sessionData && sessionData.totals) || {};
    const uploads = (sessionData && sessionData.uploads) || [];
    if (!body) return;
    const matched = Number(t.auto_matched_count || 0) + Number(t.user_matched_count || 0);
    const leftover = leftoverCount();
    const ignored = Number(t.excluded_manual_count || 0);
    const files = uploads.length;
    const bills = (sessionData && sessionData.expected_bills) || [];
    const expectedTotal = Number((sessionData && sessionData.expected_bills_total) || 0);
    const markedRows = ((sessionData && sessionData.rows) || []).filter(
      (r) => r.include !== false && String(r.reconcile_mark || '') === 'bill',
    );
    const unaccounted = ((sessionData && sessionData.rows) || []).filter((r) => {
      if (r.include === false || r.ledger_duplicate) return false;
      if (String(r.direction || '') !== 'outgoing') return false;
      if (r.manual_match || r.transfer_pair) return false;
      if (String(r.reconcile_mark || '') === 'bill') return false;
      return true;
    });
    const manuals = unclaimedManuals();
    const expectedList = bills.length
      ? `<ul class="reconcile-unclaimed-list">${bills
          .map((b) => `<li class="reconcile-unclaimed-item"><span class="reconcile-unclaimed-copy"><strong>${escapeHtml(b.label)}</strong><span class="reconcile-unclaimed-meta">${formatMoney(b.amount)}${b.category ? ` · ${escapeHtml(b.category)}` : ''}</span></span></li>`)
          .join('')}</ul>`
      : '<p class="reconcile-unclaimed-empty">No monthly bills in Daily → Plan.</p>';

    if (sessionData && sessionData.readonly) {
      const keptManuals = manuals.filter((m) => m.status === 'unclaimed' || m.status === 'kept');
      const ignoredManuals = manuals.filter((m) => m.status === 'excluded');
      const incomingRows = ((sessionData && sessionData.rows) || []).filter((r) => {
        if (r.include === false || r.ledger_duplicate) return false;
        return String(r.direction || '') === 'incoming' && !r.transfer_pair;
      });
      const incomingN = t.incoming_count != null ? Number(t.incoming_count) : incomingRows.length;
      const incomingAmt = t.incoming_total != null
        ? Number(t.incoming_total)
        : incomingRows.reduce((sum, r) => sum + round2(r.amount), 0);
      const billsN = Number(t.bill_marked_count || markedRows.length);
      const newN = Number(t.unaccounted_bank_count || unaccounted.length);
      const transferN = Number(t.transfer_pair_count || 0);
      const ignoredBank = Number(t.ignored_bank_count || 0);
      const leftoverListRo = keptManuals.length
        ? `<ul class="reconcile-unclaimed-list">${keptManuals
            .map((m) => `<li class="reconcile-unclaimed-item is-keep">
              <span class="reconcile-unclaimed-copy">
                <strong>${escapeHtml(m.description)}</strong>
                <span class="reconcile-unclaimed-meta">${formatMoney(m.amount)} · ${escapeHtml(formatDay(m.date))}</span>
              </span>
            </li>`)
            .join('')}</ul>`
        : '';
      const ignoredListRo = ignoredManuals.length
        ? `<ul class="reconcile-unclaimed-list">${ignoredManuals
            .map((m) => `<li class="reconcile-unclaimed-item is-ignored">
              <span class="reconcile-unclaimed-copy">
                <strong>${escapeHtml(m.description)}</strong>
                <span class="reconcile-unclaimed-meta">${formatMoney(m.amount)} · ${escapeHtml(formatDay(m.date))}</span>
              </span>
            </li>`)
            .join('')}</ul>`
        : '';
      const billNoteRo = billsN || bills.length
        ? `<p class="reconcile-recap-note">Marked ${formatMoney(t.bill_marked_total)} · planned ${formatMoney(expectedTotal)}.</p>`
        : '';
      body.innerHTML = `
        <h3 class="reconcile-panel-title">Imported</h3>
        <ul class="reconcile-summary-list">
          <li><strong>${files}</strong> ${files === 1 ? 'file' : 'files'} · <strong>${t.statement_count || 0}</strong> statement rows</li>
          <li><strong>${formatMoney(t.bank_total)}</strong> outgoing on statements</li>
          ${incomingN ? `<li><strong>${incomingN}</strong> incoming ${incomingN === 1 ? 'row' : 'rows'} (${formatMoney(incomingAmt)}) — salary, refunds, and other money in</li>` : ''}
          ${transferN ? `<li><strong>${transferN}</strong> ${transferN === 1 ? 'transfer' : 'transfers'} (${formatMoney(t.transfer_total)})</li>` : ''}
          <li><strong>${matched}</strong> spending ${matched === 1 ? 'entry' : 'entries'} matched (${formatMoney(t.matched_bank_total)})</li>
          ${billsN ? `<li><strong>${billsN}</strong> ${billsN === 1 ? 'row' : 'rows'} marked as bills (${formatMoney(t.bill_marked_total)})</li>` : ''}
          ${newN ? `<li><strong>${newN}</strong> new outgoing ${newN === 1 ? 'row' : 'rows'} imported (${formatMoney(t.unaccounted_bank_total)})</li>` : ''}
          ${keptManuals.length ? `<li><strong>${keptManuals.length}</strong> spending ${keptManuals.length === 1 ? 'entry' : 'entries'} kept unmatched</li>` : ''}
          ${ignoredManuals.length ? `<li><strong>${ignoredManuals.length}</strong> spending ${ignoredManuals.length === 1 ? 'entry' : 'entries'} ignored</li>` : ''}
          ${ignoredBank ? `<li><strong>${ignoredBank}</strong> statement ${ignoredBank === 1 ? 'row' : 'rows'} ignored</li>` : ''}
        </ul>
        ${keptManuals.length ? `<section class="reconcile-summary-block"><h4>Unmatched spending kept</h4>${leftoverListRo}</section>` : ''}
        ${ignoredManuals.length ? `<section class="reconcile-summary-block"><h4>Ignored spending</h4>${ignoredListRo}</section>` : ''}
        ${billsN || bills.length ? `<section class="reconcile-summary-block">
          <h4>Bills</h4>
          ${billNoteRo}
          <div class="reconcile-bill-compare">
            <div class="reconcile-bill-col"><h5>You marked as bills</h5>${markedRows.length
              ? `<ul class="reconcile-unclaimed-list">${markedRows.map((r) => summaryLine(r, '')).join('')}</ul>`
              : '<p class="reconcile-unclaimed-empty">None.</p>'}</div>
            <div class="reconcile-bill-col"><h5>Expected this month</h5>${expectedList}</div>
          </div>
        </section>` : ''}`;
      return;
    }

    const leftoverBulk = manuals.length
      ? `<div class="reconcile-bulk-actions">
          <button type="button" class="reconcile-btn reconcile-btn--ghost" data-keep-all-manuals>Keep all</button>
          <button type="button" class="reconcile-btn reconcile-btn--danger" data-ignore-all-manuals>Ignore all</button>
        </div>`
      : '';
    const leftoverList = manuals.length
      ? `<ul class="reconcile-unclaimed-list" id="reconcile-unclaimed-list">${manuals
          .map((m) => `<li class="reconcile-unclaimed-item${m.status === 'excluded' ? ' is-ignored' : ' is-keep'}">
            <span class="reconcile-unclaimed-copy">
              <strong>${escapeHtml(m.description)}</strong>
              <span class="reconcile-unclaimed-meta">${formatMoney(m.amount)} · ${escapeHtml(formatDay(m.date))}</span>
            </span>
            <span class="reconcile-unclaimed-actions">${leftoverChoiceButtons(m)}</span>
          </li>`)
          .join('')}</ul>`
      : '<p class="reconcile-unclaimed-empty">Nothing left unmatched — everything you logged was matched or ignored.</p>';

    const markedList = markedRows.length
      ? `<ul class="reconcile-unclaimed-list">${markedRows
          .map((r) => summaryLine(r, `<span class="reconcile-unclaimed-actions">
            <button type="button" class="reconcile-btn reconcile-btn--ghost" data-unmark-bill-row="${escapeHtml(r.id)}">Not a bill</button>
          </span>`))
          .join('')}</ul>`
      : '<p class="reconcile-unclaimed-empty">You didn’t mark any statement rows as bills.</p>';
    const markedTotal = Number(t.bill_marked_total || 0);
    const billDiff = Math.abs(markedTotal - expectedTotal);
    const billNote = bills.length || markedRows.length
      ? `<p class="reconcile-recap-note${billDiff >= 0.01 && bills.length && markedRows.length ? ' reconcile-recap-note--warn' : ''}">Marked ${formatMoney(markedTotal)} · planned ${formatMoney(expectedTotal)}${billDiff >= 0.01 && bills.length ? ' — these don’t match yet' : ''}.</p>`
      : '';

    const unaccountedList = unaccounted.length
      ? `<ul class="reconcile-unclaimed-list">${unaccounted
          .map((r) => `<li class="reconcile-unclaimed-item">
            <span class="reconcile-unclaimed-copy">
              <strong>${escapeHtml(r.description)}</strong>
              <span class="reconcile-unclaimed-meta">${formatMoney(r.amount)} · ${escapeHtml(formatDay(r.date))}${r.bank_source ? ` · ${escapeHtml(r.bank_source)}` : ''}</span>
            </span>
            <span class="reconcile-unclaimed-actions">
              <button type="button" class="reconcile-btn reconcile-btn--ghost" data-mark-bill-row="${escapeHtml(r.id)}">This is a bill</button>
              <button type="button" class="reconcile-btn reconcile-btn--ghost" data-ignore-row="${escapeHtml(r.id)}">Ignore</button>
            </span>
          </li>`)
          .join('')}</ul>`
      : '<p class="reconcile-unclaimed-empty">Every leftover statement row is either matched, a transfer, or marked as a bill.</p>';

    body.innerHTML = `
      <h3 class="reconcile-panel-title">Ready to import</h3>
      <ul class="reconcile-summary-list">
        <li><strong>${files}</strong> ${files === 1 ? 'file' : 'files'} · <strong>${t.statement_count || 0}</strong> statement rows (${formatMoney(t.bank_total)})</li>
        <li><strong>${matched}</strong> spending ${matched === 1 ? 'entry' : 'entries'} matched</li>
        <li><strong>${unaccounted.length}</strong> unaccounted statement ${unaccounted.length === 1 ? 'row' : 'rows'} added as new</li>
        ${leftover ? `<li><strong>${leftover}</strong> spending ${leftover === 1 ? 'entry stays' : 'entries stay'} unmatched unless you ignore them below</li>` : ''}
        ${ignored ? `<li><strong>${ignored}</strong> ignored</li>` : ''}
      </ul>
      <section class="reconcile-summary-block">
        <h4>Matched spending</h4>
        <p class="reconcile-panel-desc">Bank statement vs the spending you logged. Amount or date is highlighted when they don’t match exactly.</p>
        ${matchedSampleHtml(matchedEntries())}
      </section>
      <section class="reconcile-summary-block">
        <div class="reconcile-summary-block-head">
          <h4>Unmatched spending</h4>
          ${leftoverBulk}
        </div>
        <p class="reconcile-panel-desc">You logged these, but they didn’t show up on the statements. Keep them, or ignore any that weren’t real.</p>
        ${leftoverList}
      </section>
      <section class="reconcile-summary-block">
        <h4>Bills</h4>
        <p class="reconcile-panel-desc">Statement rows you marked as a bill — these aren’t logged as daily spending. Compared with monthly bills on Daily → Plan.</p>
        ${billNote}
        <div class="reconcile-bill-compare">
          <div class="reconcile-bill-col"><h5>You marked as bills</h5>${markedList}</div>
          <div class="reconcile-bill-col"><h5>Expected this month</h5>${expectedList}</div>
        </div>
      </section>
      <section class="reconcile-summary-block">
        <h4>Unaccounted statement rows</h4>
        <p class="reconcile-panel-desc">These weren’t matched to spending you logged. They’ll import as new, unless you mark them as a bill or ignore them.</p>
        ${unaccountedList}
      </section>`;
  }

  function renderPhases() {
    const bank = $('reconcile-phase-bank');
    if (bank) bank.classList.toggle('hidden', step !== 'review');
    if (step === 'review') {
      renderFocusItem(currentReviewEntry());
    } else if (step === 'confirm') {
      renderSummaryPhase();
    }
  }

  function renderActionsBar() {
    const autoBtn = $('reconcile-auto-match-btn');
    const uploads = (sessionData && sessionData.uploads) || [];
    if (autoBtn) autoBtn.disabled = !uploads.length || (sessionData && sessionData.status === 'imported');
    const finalBtn = $('reconcile-final-confirm-btn');
    if (finalBtn) {
      finalBtn.disabled = !sessionData || !sessionData.auto_match_ran || sessionData.status === 'imported';
      finalBtn.classList.toggle('hidden', !!(sessionData && sessionData.readonly));
    }
    const confirmBack = $('reconcile-confirm-back');
    if (confirmBack) confirmBack.classList.toggle('hidden', !!(sessionData && sessionData.readonly));
  }

  function rowMatchLabel(row) {
    if (!row) return '—';
    if (String(row.reconcile_mark || '') === 'bill') return 'Bill';
    if (row.transfer_pair) return 'Transfer';
    if (row.manual_match) return 'Matched';
    if (row.ledger_duplicate) return 'Duplicate';
    if (row.include === false) return 'Ignored';
    return 'New';
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
    ['reconcile-upload-btn', 'reconcile-auto-match-btn', 'reconcile-file', 'reconcile-source', 'reconcile-period-start', 'reconcile-period-end', 'reconcile-range-toggle', 'reconcile-range-reset'].forEach((id) => {
      const el = $(id);
      if (el) el.disabled = readonly;
    });
  }

  function renderAll() {
    if (step === 'review' && sessionData && sessionData.auto_match_ran && !remainingEntries().length) {
      step = 'confirm';
    }
    const backToFiles = $('reconcile-back-to-files');
    if (backToFiles) backToFiles.classList.toggle('hidden', step !== 'review');
    const viewAll = $('reconcile-view-all');
    if (viewAll) viewAll.classList.toggle('hidden', step !== 'review');
    renderChip();
    renderReadonly();
    renderLead();
    renderSteps();
    renderUploads();
    renderRecap();
    renderPhases();
    renderActionsBar();
    renderViewAll();
  }

  function postRaw(path, body) {
    return api(`/api/spending/reconcile/${encodeURIComponent(month)}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  }

  function post(path, body) {
    return postRaw(path, body).then(refreshFromSession);
  }

  function patchRow(rowId, body) {
    return api(`/api/spending/reconcile/${encodeURIComponent(month)}/row/${encodeURIComponent(rowId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }).then(refreshFromSession);
  }

  function patchRowsLastRefresh(rowIds, body) {
    const ids = (rowIds || []).map(String).filter(Boolean);
    if (!ids.length) return Promise.resolve();
    return ids.reduce((chain, id, i) => chain.then(() => {
      return api(`/api/spending/reconcile/${encodeURIComponent(month)}/row/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      }).then((data) => {
        if (i === ids.length - 1) refreshFromSession(data);
      });
    }), Promise.resolve());
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

  function acceptCurrent() {
    const entry = currentReviewEntry();
    if (!entry) return;
    undoStack.push({ type: 'accept', key: entry.key });
    markDecided(entry.key);
    afterDecideOrStay();
  }

  function undoLast() {
    const rec = undoStack.pop();
    if (!rec) return;
    rollbackDecide(rec.key);
    cursor = 0;
    const fail = (err) => {
      undoStack.push(rec);
      markDecided(rec.key);
      setStatus(err.message || 'Undo failed', true);
      renderAll();
    };
    if (rec.type === 'accept') {
      renderAll();
      return;
    }
    if (rec.type === 'unlink') {
      if (rec.item && rec.item.kind === 'transfer') {
        post('/link-transfer', { row_id_a: rec.item.row_ids[0], row_id_b: rec.item.row_ids[1] }).catch(fail);
        return;
      }
      post('/relink-netted', {
        row_ids: rec.item.row_ids,
        manual_ids: rec.item.manual_ids,
        group_key: rec.item.group_key,
        via: 'auto',
      }).catch(fail);
      return;
    }
    if (rec.type === 'link') {
      post('/unlink-manual', { row_id: rec.row_id }).catch(fail);
      return;
    }
    if (rec.type === 'bill') {
      patchRowsLastRefresh(rec.row_ids, { reconcile_mark: null }).catch(fail);
      return;
    }
    renderAll();
  }

  function bindEvents() {
    const monthInput = $('reconcile-month');
    if (monthInput) {
      month = currentMonth();
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
        viewAllToggle.textContent = open ? 'Hide statement rows' : 'Show all statement rows';
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
        setStatus('Finding matches…');
        postRaw('/auto-match')
          .then((data) => {
            resetReviewState();
            sessionData = data && data.session ? data.session : data;
            setStatus('');
            goStep(remainingEntries().length ? 'review' : 'confirm');
          })
          .catch((err) => setStatus(err.message || 'Matching failed', true));
      });
    }

    const steps = $('reconcile-steps');
    if (steps) {
      steps.addEventListener('click', (ev) => {
        const item = ev.target.closest('[data-go-step]');
        if (!item || (sessionData && sessionData.readonly)) return;
        const target = item.getAttribute('data-go-step');
        if (target === 'upload') {
          goStep('upload');
          return;
        }
        if (!sessionData || !sessionData.auto_match_ran) return;
        if (target === 'review') {
          goStep(remainingEntries().length ? 'review' : 'confirm');
          return;
        }
        if (target === 'confirm') goStep('confirm');
      });
    }

    const backToFiles = $('reconcile-back-to-files');
    if (backToFiles) {
      backToFiles.addEventListener('click', () => goStep('upload'));
    }

    const confirmBack = $('reconcile-confirm-back');
    if (confirmBack) {
      confirmBack.addEventListener('click', () => {
        if (remainingEntries().length) goStep('review');
        else goStep('upload');
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
          const entry = currentReviewEntry();
          const row = entry && rowsById().get(String(entry.item.row_id));
          const sugs = exactSuggestions(row);
          selectedSuggestion = sugs[idx] || null;
          return;
        }

        if (ev.target.closest('[data-queue-undo]')) {
          undoLast();
          return;
        }
        if (ev.target.closest('[data-queue-prev]')) {
          cursor = Math.max(0, cursor - 1);
          renderAll();
          return;
        }
        if (ev.target.closest('[data-queue-next]')) {
          const rem = remainingEntries();
          cursor = Math.min(rem.length - 1, cursor + 1);
          renderAll();
          return;
        }

        const accept = ev.target.closest('[data-action="accept-queue"]');
        if (accept) {
          acceptCurrent();
          return;
        }

        const unlinkQ = ev.target.closest('[data-action="unlink-queue"]');
        if (unlinkQ) {
          const entry = currentReviewEntry();
          if (!entry) return;
          const item = entry.item;
          const rowId = item.row_id || (item.row_ids && item.row_ids[0]);
          undoStack.push({ type: 'unlink', key: entry.key, item: snapshotItem(item) });
          markDecided(entry.key);
          const req = item.kind === 'transfer'
            ? post('/unlink-transfer', { row_id: rowId })
            : post('/unlink-manual', { row_id: rowId });
          req.catch((err) => {
            undoStack.pop();
            rollbackDecide(entry.key);
            setStatus(err.message, true);
            renderAll();
          });
          return;
        }

        const link = ev.target.closest('[data-action="link-suggest"]');
        if (link) {
          const entry = currentReviewEntry();
          const rowId = link.getAttribute('data-row-id');
          const sug = selectedSuggestion;
          if (!sug) {
            setStatus('Select a match first.', true);
            return;
          }
          const manualIds = sug.ids || (sug.id ? String(sug.id).split('+') : []);
          if (entry) {
            undoStack.push({ type: 'link', key: entry.key, row_id: rowId });
            markDecided(entry.key);
          }
          post('/link-manual', { row_id: rowId, manual_ids: manualIds }).catch((err) => {
            if (entry) {
              undoStack.pop();
              rollbackDecide(entry.key);
            }
            setStatus(err.message, true);
            renderAll();
          });
          return;
        }

        const bill = ev.target.closest('[data-action="mark-bill"]');
        if (bill) {
          const entry = currentReviewEntry();
          if (!entry || entry.item.kind !== 'unmatched') return;
          const rowId = bill.getAttribute('data-row-id') || entry.item.row_id;
          undoStack.push({ type: 'bill', key: entry.key, row_ids: [rowId] });
          markDecided(entry.key);
          patchRow(rowId, { reconcile_mark: 'bill' }).catch((err) => {
            undoStack.pop();
            rollbackDecide(entry.key);
            setStatus(err.message, true);
            renderAll();
          });
        }
      });
    }

    const summaryBody = $('reconcile-summary-body');
    if (summaryBody) {
      summaryBody.addEventListener('click', (ev) => {
        const keepAll = ev.target.closest('[data-keep-all-manuals]');
        if (keepAll) {
          const ids = unclaimedManuals().map((m) => m.id).filter(Boolean);
          if (!ids.length) return;
          post('/keep-manual', { manual_ids: ids, keep: true }).catch((err) =>
            setStatus(err.message, true),
          );
          return;
        }
        const ignoreAll = ev.target.closest('[data-ignore-all-manuals]');
        if (ignoreAll) {
          const ids = unclaimedManuals().map((m) => m.id).filter(Boolean);
          if (!ids.length) return;
          post('/exclude-manual', { manual_ids: ids }).catch((err) =>
            setStatus(err.message, true),
          );
          return;
        }
        const ex = ev.target.closest('[data-exclude-manual]');
        if (ex) {
          post('/exclude-manual', { manual_id: ex.getAttribute('data-exclude-manual') }).catch((err) =>
            setStatus(err.message, true),
          );
          return;
        }
        const keep = ev.target.closest('[data-keep-manual]');
        if (keep) {
          post('/keep-manual', { manual_id: keep.getAttribute('data-keep-manual'), keep: true }).catch((err) =>
            setStatus(err.message, true),
          );
          return;
        }
        const ignoreRow = ev.target.closest('[data-ignore-row]');
        if (ignoreRow) {
          patchRow(ignoreRow.getAttribute('data-ignore-row'), { include: false }).catch((err) =>
            setStatus(err.message, true),
          );
          return;
        }
        const markBillRow = ev.target.closest('[data-mark-bill-row]');
        if (markBillRow) {
          patchRow(markBillRow.getAttribute('data-mark-bill-row'), { reconcile_mark: 'bill' }).catch((err) =>
            setStatus(err.message, true),
          );
          return;
        }
        const unmarkBill = ev.target.closest('[data-unmark-bill-row]');
        if (unmarkBill) {
          patchRow(unmarkBill.getAttribute('data-unmark-bill-row'), { reconcile_mark: null }).catch((err) =>
            setStatus(err.message, true),
          );
        }
      });
    }

    const finalConfirm = $('reconcile-final-confirm-btn');
    if (finalConfirm) {
      finalConfirm.addEventListener('click', () => {
        setStatus('Importing…');
        const ready = leftoverCount() > 0 && !(sessionData && sessionData.reviewed_unclaimed)
          ? postRaw('/review-unclaimed', { reviewed: true }).then((data) => {
              sessionData = data && data.session ? data.session : data;
            })
          : Promise.resolve();
        ready
          .then(() => post('/confirm'))
          .then(() => {
            setStatus('');
            goStep('confirm');
          })
          .catch((err) => setStatus(err.message || 'Import failed', true));
      });
    }
  }

  bindEvents();
  loadSession();
})();
