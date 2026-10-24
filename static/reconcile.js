(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function money(amount) {
    var n = Number(amount) || 0;
    var abs = Math.abs(n).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (n < 0 ? '−£' : '£') + abs;
  }
  function shortDay(iso) {
    var raw = String(iso || '').slice(0, 10);
    var d = new Date(raw + 'T12:00:00');
    if (isNaN(d.getTime())) return raw;
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
  }
  function monthLabel(ym) {
    var m = String(ym || '').slice(0, 7);
    if (m.length !== 7) return 'this month';
    var d = new Date(Number(m.slice(0, 4)), Number(m.slice(5, 7)) - 1, 1);
    if (isNaN(d.getTime())) return 'this month';
    return d.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
  }
  function refKey(desc) {
    return String(desc || '').replace(/[^A-Za-z ]/g, ' ').trim().toUpperCase().split(/\s+/).slice(0, 2).join(' ');
  }

  var API = '/api/spending/reconcile/';
  var state = {
    month: String(window.RECONCILE_MONTH || '').slice(0, 7),
    session: null,
    filter: 'review',
    openResolver: null,
    openGroup: null,
    decided: {},
    toastTimer: null,
  };

  function api(path, opts) {
    var options = opts || {};
    return fetch(path, options).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (body) {
        if (!res.ok) {
          var err = new Error((body && body.error) || res.statusText || 'Request failed');
          err.status = res.status;
          throw err;
        }
        return body;
      });
    });
  }
  function postRaw(path, body) {
    return api(API + encodeURIComponent(state.month) + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }).then(function (d) { return d && d.session ? d.session : d; });
  }
  function patchRow(rowId, body) {
    return api(API + encodeURIComponent(state.month) + '/row/' + encodeURIComponent(rowId), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }).then(function (d) { return d && d.session ? d.session : d; });
  }

  function setStatus(msg, isError) {
    var el = $('reconcile-status');
    if (!el) return;
    if (!msg) { el.classList.add('hidden'); el.textContent = ''; el.classList.remove('is-error'); return; }
    el.classList.remove('hidden');
    el.textContent = msg;
    el.classList.toggle('is-error', !!isError);
  }
  function fail(err) { setStatus((err && err.message) || 'Something went wrong', true); }

  function refresh(session) {
    if (session) state.session = session;
    setStatus('');
    renderAll();
  }

  function rows() { return (state.session && state.session.rows) || []; }
  function manuals() { return (state.session && state.session.manuals) || []; }
  function totals() { return (state.session && state.session.totals) || {}; }
  function rowsById() { var m = {}; rows().forEach(function (r) { m[String(r.id)] = r; }); return m; }
  function manualsById() { var m = {}; manuals().forEach(function (x) { m[String(x.id)] = x; }); return m; }
  function unclaimedManuals() { return manuals().filter(function (m) { return m.status === 'unclaimed'; }); }

  function exactSuggestions(row) {
    return ((row && row.suggestions) || []).filter(function (s) {
      if (s.exact_amount === true) return true;
      if (s.exact_amount === false) return false;
      return Math.abs((Number(s.amount) || 0) - (Number(row.amount) || 0)) < 0.005;
    });
  }

  function kindOf(row) {
    if (row.include === false) return 'ignored';
    if (row.ledger_duplicate) return 'duplicate';
    if (String(row.reconcile_mark || '') === 'bill') return 'bill';
    if (row.transfer_pair) return 'transfer';
    if (String(row.direction || '') === 'incoming') return 'incoming';
    if (row.manual_match && (row.manual_match.manual_ids || []).length) return 'matched';
    if (state.decided[String(row.id)]) return 'new';
    if (exactSuggestions(row).length) return 'ask';
    return 'new';
  }

  var STATUS = {
    matched: ['v-matched', 'Matched'], bill: ['v-bill', 'Bill'],
    transfer: ['v-transfer', 'Transfer'], incoming: ['v-in', 'In'],
    new: ['v-new', 'NEW'], ask: ['v-ask', 'Check'],
    ignored: ['v-muted', 'Ignored'], duplicate: ['v-muted', 'Duplicate'],
  };

  function statusCell(kind, row) {
    if (kind === 'ask') {
      var ex = exactSuggestions(row);
      var label = ex.length > 1 ? ex.length + ' exact' : 'Near';
      return '<span class="v v-ask">' + label + '</span>';
    }
    var s = STATUS[kind] || ['v-muted', kind];
    return '<span class="v ' + s[0] + '">' + s[1] + '</span>';
  }

  function entryCell(row, kind) {
    if (kind === 'matched') {
      var mm = row.manual_match || {};
      var mids = mm.manual_ids || [];
      var mans = mids.map(function (id) { return manualsById()[String(id)]; }).filter(Boolean);
      if (!mans.length) return '<span class="rc-none">—</span>';
      if (mans.length === 1) {
        return '<strong>' + esc(mans[0].description) + '</strong> <span class="rc-em">' +
          money(mans[0].amount) + ' · ' + shortDay(mans[0].date) + '</span>';
      }
      var sum = mans.reduce(function (a, m) { return a + (Number(m.amount) || 0); }, 0);
      return '<strong>' + mans.length + ' entries</strong> <span class="rc-em">' + money(sum) + '</span>';
    }
    if (kind === 'bill') return '<span class="rc-none">Bill — not daily spending</span>';
    if (kind === 'transfer') return '<span class="rc-none">↔ transfer</span>';
    if (kind === 'incoming' || kind === 'ignored' || kind === 'duplicate') return '<span class="rc-none">—</span>';
    if (kind === 'new') {
      return '<button type="button" class="rc-resolve" data-resolve="' + esc(row.id) + '">' +
        '<span class="rc-resolve-main"><span class="rc-none">Match an entry…</span></span>' +
        '<span class="rc-caret">▾</span></button>';
    }
    // ask
    var ex = exactSuggestions(row);
    var first = ex[0];
    var inner = first
      ? '<strong>' + esc(first.description) + '</strong> <span class="rc-em">' + money(first.amount) + ' · ' + shortDay(first.date) + '</span>'
      : '<strong>' + (row.suggestions || []).length + ' suggested</strong>';
    return '<button type="button" class="rc-resolve" data-resolve="' + esc(row.id) + '">' +
      '<span class="rc-resolve-main">' + inner + '</span><span class="rc-caret">▾</span></button>';
  }

  function actionsCell(kind) {
    var acts = [];
    if (kind === 'matched') acts.push('<button type="button" class="rc-act" data-unmatch="' + kind + '">Unmatch</button>');
    if (kind === 'matched' || kind === 'new' || kind === 'ask') acts.push('<button type="button" class="rc-act rc-act-bill" data-makebill="1">Make bill</button>');
    return acts.length ? '<div class="rc-acts">' + acts.join('') + '</div>' : '';
  }

  function resolverHtml(row, kind) {
    var cands = (row.suggestions || []).slice();
    var pool = unclaimedManuals();
    var candIds = {};
    cands.forEach(function (s) { (s.ids || []).forEach(function (id) { candIds[String(id)] = true; }); });
    var options = cands.map(function (s) {
      var diffP = Math.abs(Math.round(((Number(s.amount) || 0) - (Number(row.amount) || 0)) * 100));
      var label = s.kind === 'netted' ? esc(s.description) + ' (combined)' : esc(s.description);
      return '<button type="button" class="rc-cand is-suggested" data-link="' + esc((s.ids || []).join('+')) + '">' +
        '<span class="rc-radio"></span><span class="rc-cand-main"><strong>' + label + '</strong>' +
        '<span class="rc-em">' + money(s.amount) + ' · ' + shortDay(s.date) + '</span></span>' +
        (diffP ? '<span class="rc-flag">' + diffP + 'p off</span>' : '') + '</button>';
    }).join('');
    var rest = pool.filter(function (m) { return !candIds[String(m.id)]; }).map(function (m) {
      return '<button type="button" class="rc-cand" data-link="' + esc(m.id) + '">' +
        '<span class="rc-radio"></span><span class="rc-cand-main"><strong>' + esc(m.description) + '</strong>' +
        '<span class="rc-em">' + money(m.amount) + ' · ' + shortDay(m.date) + '</span></span></button>';
    }).join('');
    var empty = (!cands.length && !pool.length) ? '<p class="rc-none" style="padding:0.4rem 0.55rem">No logged entries left to match.</p>' : '';
    return '<div class="rc-resolver">' + options + rest + empty +
      '<button type="button" class="rc-import-new" data-import-new="' + esc(row.id) + '">Import as new</button></div>';
  }

  function rowHtml(row) {
    var kind = kindOf(row);
    var n = refCounts[refKey(row.description)] || 0;
    var badge = (n >= 2 && (kind === 'new' || kind === 'ask'))
      ? ' <button type="button" class="rc-ref-badge" data-ref="' + esc(refKey(row.description)) + '" title="Select all ' + n + ' similar">×' + n + '</button>'
      : '';
    var amtCls = String(row.direction) === 'incoming' ? ' rc-pos' : '';
    var cls = kind === 'new' ? ' class="rc-row-new"' : (kind === 'ask' ? ' class="rc-row-ask"' : '');
    return '<tr' + cls + ' data-id="' + esc(row.id) + '" data-kind="' + kind + '">' +
      '<td><input type="checkbox" class="rc-cbx"' + (row.include === false ? '' : ' checked') + ' aria-label="Include row"></td>' +
      '<td class="rc-desc"><strong>' + esc(row.description) + badge + '</strong>' +
        '<span>' + shortDay(row.date) + ' · ' + esc(row.bank_source || '') + '</span></td>' +
      '<td>' + statusCell(kind, row) + '</td>' +
      '<td>' + entryCell(row, kind) + '</td>' +
      '<td class="rc-amount' + amtCls + '">' + money(row.amount) + '</td>' +
      '<td class="rc-actions-cell">' + actionsCell(kind) + '</td></tr>';
  }

  var refCounts = {};

  function matchesFilter(kind, f) {
    if (f === 'all') return true;
    if (f === 'review') return kind === 'ask' || kind === 'new';
    return kind === f;
  }

  function renderGrid() {
    var list = rows();
    refCounts = {};
    list.forEach(function (r) {
      var k = kindOf(r);
      if (k === 'new' || k === 'ask') refCounts[refKey(r.description)] = (refCounts[refKey(r.description)] || 0) + 1;
    });
    var html = '';
    var shown = 0;
    list.forEach(function (row) {
      var kind = kindOf(row);
      if (!matchesFilter(kind, state.filter)) return;
      shown++;
      html += rowHtml(row);
      if (state.openResolver === String(row.id)) {
        html += '<tr class="rc-resolver-row"><td colspan="6">' + resolverHtml(row, kind) + '</td></tr>';
      }
    });
    $('reconcile-rows').innerHTML = html || '<tr><td colspan="6" class="rc-empty">Nothing here.</td></tr>';
    $('reconcile-count').textContent = shown + ' of ' + list.length + ' rows';
  }

  function renderLeftover() {
    var kept = manuals().filter(function (m) { return m.status === 'unclaimed' || m.status === 'kept' || m.status === 'excluded'; });
    $('reconcile-leftover-count').textContent = kept.length;
    if (!kept.length) {
      $('reconcile-leftover').innerHTML = '<p class="rc-none" style="padding:0.5rem 0.2rem">Nothing left unmatched — every logged entry was matched or ignored.</p>';
      return;
    }
    $('reconcile-leftover').innerHTML = kept.map(function (m) {
      var ignored = m.status === 'excluded';
      var keptSel = !ignored && m.status !== 'excluded';
      return '<div class="rc-leftover-row' + (ignored ? ' is-ignored' : '') + '">' +
        '<div class="rc-desc"><strong>' + esc(m.description) + '</strong><span>' + money(m.amount) + ' · ' + shortDay(m.date) + '</span></div>' +
        '<div class="rc-leftover-actions">' +
          '<button type="button" class="rc-pill' + (keptSel ? ' is-on' : '') + '" data-keep="' + esc(m.id) + '">Keep</button>' +
          '<button type="button" class="rc-pill rc-pill-danger' + (ignored ? ' is-on' : '') + '" data-ignore="' + esc(m.id) + '">Ignore</button>' +
        '</div></div>';
    }).join('');
  }

  function line(k, v, cls) {
    return '<div class="rc-line"><span class="rc-line-k">' + k + '</span><span class="rc-line-v' + (cls ? ' ' + cls : '') + '">' + v + '</span></div>';
  }
  function billsDelta(actual, expected) {
    var d = Math.round((actual - expected) * 100) / 100;
    if (Math.abs(d) < 0.005) return 'on plan';
    return d < 0 ? '<span class="rc-under">' + money(-d) + ' under</span>' : '<span class="rc-over">' + money(d) + ' over</span>';
  }

  function renderEffect() {
    var t = totals();
    var expected = Number((state.session && state.session.expected_bills_total) || 0);
    var bills = Number(t.bill_marked_total || 0);
    $('reconcile-effect').innerHTML =
      line('Statement spend', money(t.bank_total)) +
      line('Matched', money(t.matched_bank_total)) +
      line('New', money(t.unaccounted_bank_total)) +
      '<div class="rc-line"><span class="rc-line-k">Bills</span><span class="rc-line-v">' + money(bills) + '</span></div>' +
      '<div class="rc-sub">Expected ' + money(expected) + ' · ' + billsDelta(bills, expected) + '</div>' +
      line('Transfers', money(t.transfer_total)) +
      line('Money in', money(t.incoming_total), 'rc-pos');
    $('reconcile-daily').innerHTML =
      line('Logged entries', manuals().length) +
      line('Unmatched', t.unmatched_manual_count || 0) +
      line('Ledger rows', t.statement_count || 0);
    var included = rows().filter(function (r) { return r.include !== false && !r.ledger_duplicate; }).length;
    var btn = $('reconcile-confirm-btn');
    btn.textContent = 'Import ' + included + ' row' + (included === 1 ? '' : 's');
    btn.disabled = !(state.session && state.session.auto_match_ran) || state.session.status === 'imported';
  }

  function renderUploads() {
    var list = $('reconcile-upload-list');
    var uploads = (state.session && state.session.uploads) || [];
    if (!uploads.length) {
      list.classList.add('reconcile-upload-list--empty');
      list.innerHTML = '<li class="reconcile-upload-empty">No files yet — add each bank statement for ' + esc(monthLabel(state.month)) + '.</li>';
      return;
    }
    list.classList.remove('reconcile-upload-list--empty');
    list.innerHTML = uploads.map(function (u) {
      var bits = [];
      if (u.bank_source) bits.push(esc(u.bank_source));
      if (u.period_start && u.period_end) bits.push(esc(u.period_start) + ' → ' + esc(u.period_end));
      bits.push((u.row_count || 0) + ' rows');
      var remove = state.session.readonly ? '' :
        '<button type="button" class="reconcile-btn reconcile-btn--ghost reconcile-upload-remove" data-remove-upload="' + esc(u.id) + '">Remove</button>';
      return '<li class="reconcile-upload-item" data-upload-id="' + esc(u.id) + '">' +
        '<span class="reconcile-upload-meta"><strong>' + esc(u.file_name || 'Statement') + '</strong>' +
        '<span class="reconcile-tally-sub">' + bits.join(' · ') + '</span></span>' + remove + '</li>';
    }).join('');
  }

  function renderReadonly() {
    var s = state.session || {};
    var readonly = !!s.readonly;
    $('rc-cockpit').classList.toggle('hidden', readonly || !s.auto_match_ran);
    $('reconcile-composer').classList.toggle('hidden', readonly);
    $('reconcile-range-toggle').classList.toggle('hidden', readonly);
    var foot = document.querySelector('.reconcile-step-foot');
    if (foot) foot.classList.toggle('hidden', readonly);
    $('reconcile-readonly').classList.toggle('hidden', !readonly);
    document.body.classList.toggle('reconcile-readonly', readonly);
    if (readonly) {
      var t = totals();
      $('reconcile-readonly').innerHTML =
        '<h3 class="rc-readonly-h">Imported</h3>' +
        '<ul class="rc-readonly-list">' +
        '<li><strong>' + (s.uploads || []).length + '</strong> files · <strong>' + (t.statement_count || 0) + '</strong> statement rows</li>' +
        '<li><strong>' + money(t.bank_total) + '</strong> outgoing on statements</li>' +
        '<li><strong>' + ((t.auto_matched_count || 0) + (t.user_matched_count || 0)) + '</strong> spending entries matched</li>' +
        (t.bill_marked_count ? '<li><strong>' + t.bill_marked_count + '</strong> rows marked as bills (' + money(t.bill_marked_total) + ')</li>' : '') +
        (t.unaccounted_bank_count ? '<li><strong>' + t.unaccounted_bank_count + '</strong> new outgoing rows imported (' + money(t.unaccounted_bank_total) + ')</li>' : '') +
        '</ul>';
    }
  }

  function renderChip() {
    var chip = $('reconcile-status-chip');
    if (!chip || !state.session) return;
    if (state.session.readonly || state.session.status === 'imported') {
      chip.textContent = 'Imported';
      chip.classList.remove('hidden');
      return;
    }
    if (state.session.auto_match_ran) {
      chip.textContent = 'Matched';
      chip.classList.remove('hidden');
      return;
    }
    chip.classList.add('hidden');
  }

  function renderLead() {
    var lead = $('reconcile-lead');
    if (!lead) return;
    if (state.session && state.session.readonly) { lead.textContent = monthLabel(state.month) + ' is already imported.'; return; }
    lead.textContent = 'Add every bank statement for ' + monthLabel(state.month) + '.';
  }

  function renderSegCounts() {
    // Counts live in the toolbar; tabs stay static labels.
  }

  function renderAll() {
    renderChip();
    renderLead();
    renderUploads();
    renderReadonly();
    if (!(state.session && state.session.readonly)) {
      renderGrid();
      renderLeftover();
      renderEffect();
    }
    renderSegCounts();
    var auto = $('reconcile-auto-match-btn');
    var uploads = (state.session && state.session.uploads) || [];
    if (auto) auto.disabled = !uploads.length || (state.session && state.session.status === 'imported');
    var restart = $('reconcile-restart-match');
    if (restart) restart.classList.toggle('hidden', !(state.session && state.session.auto_match_ran && !state.session.readonly));
  }

  // ---- toast + undo ------------------------------------------------------
  function hideToast() { $('reconcile-toast').classList.remove('show'); state.undoFn = null; }
  function showToast(msg, undoFn) {
    $('reconcile-toast-msg').textContent = msg;
    state.undoFn = undoFn || null;
    var btn = $('reconcile-undo');
    btn.style.display = undoFn ? '' : 'none';
    $('reconcile-toast').classList.add('show');
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(hideToast, 7000);
  }
  function doUndo() {
    var fn = state.undoFn;
    hideToast();
    if (fn) fn();
  }

  // ---- mutations ---------------------------------------------------------
  function linkManual(rowId, idsCsv) {
    var ids = String(idsCsv || '').split('+').filter(Boolean);
    postRaw('/link-manual', { row_id: rowId, manual_ids: ids })
      .then(function (s) { state.openResolver = null; refresh(s); })
      .catch(fail);
  }
  function importAsNew(rowId) {
    state.decided[String(rowId)] = true;
    state.openResolver = null;
    renderAll();
  }
  function unmatch(rowId) {
    var r = rowsById()[String(rowId)];
    var mids = (r && r.manual_match && r.manual_match.manual_ids) || [];
    postRaw('/unlink-manual', { row_id: rowId })
      .then(function (s) {
        refresh(s);
        showToast('Match removed', function () {
          postRaw('/link-manual', { row_id: rowId, manual_ids: mids }).then(function (s2) { refresh(s2); hideToast(); }).catch(fail);
        });
      }).catch(fail);
  }
  function makeBills(rowIds, label) {
    var byId = rowsById();
    var prev = rowIds.map(function (id) {
      var r = byId[String(id)] || {};
      return { id: id, manual_ids: (r.manual_match && r.manual_match.manual_ids) || [] };
    });
    var chain = Promise.resolve();
    prev.forEach(function (p) {
      if (p.manual_ids.length) chain = chain.then(function () { return postRaw('/unlink-manual', { row_id: p.id }); });
    });
    chain
      .then(function () { return postRaw('/mark-bills', { row_ids: rowIds, reconcile_mark: 'bill' }); })
      .then(function (s) {
        refresh(s);
        showToast(label, function () {
          postRaw('/mark-bills', { row_ids: rowIds, reconcile_mark: null }).then(function () {
            var c = Promise.resolve();
            prev.forEach(function (p) {
              if (p.manual_ids.length) c = c.then(function () { return postRaw('/link-manual', { row_id: p.id, manual_ids: p.manual_ids }); });
            });
            return c;
          }).then(function (s2) { if (s2) refresh(s2); hideToast(); }).catch(fail);
        });
      }).catch(fail);
  }
  function setManualStatus(ids, keep) {
    var path = keep ? '/keep-manual' : '/exclude-manual';
    postRaw(path, { manual_ids: ids }).then(refresh).catch(fail);
  }

  // ---- group panel -------------------------------------------------------
  function rowsByRef(ref) {
    return rows().filter(function (r) {
      var k = kindOf(r);
      return (k === 'new' || k === 'ask') && refKey(r.description) === ref;
    });
  }
  function openGroupPanel(row, ref) {
    var trs = rowsByRef(ref);
    if (trs.length < 2) return;
    state.openGroup = ref;
    var list = trs.map(function (t) {
      return '<li><span>' + shortDay(t.date) + '</span><span>' + money(t.amount) + '</span></li>';
    }).join('');
    var anchor = document.querySelector('tr[data-id="' + row.id + '"]');
    if (!anchor) return;
    var panel = document.createElement('tr');
    panel.className = 'rc-group-row';
    panel.setAttribute('data-group', '1');
    panel.innerHTML = '<td colspan="6"><div class="rc-group-panel">' +
      '<div class="rc-group-head">' + trs.length + ' similar <strong>' + esc(ref) + '</strong> rows — mark all as bills?</div>' +
      '<ul class="rc-group-list">' + list + '</ul>' +
      '<div class="rc-group-actions">' +
      '<button type="button" class="reconcile-btn reconcile-btn--primary" data-group-bill="' + esc(ref) + '">Make all ' + trs.length + ' bills</button>' +
      '<button type="button" class="reconcile-btn reconcile-btn--ghost" data-group-cancel="1">Cancel</button>' +
      '</div></div></td>';
    anchor.parentNode.insertBefore(panel, anchor.nextSibling);
    trs.forEach(function (t) {
      var el = document.querySelector('tr[data-id="' + t.id + '"]');
      if (el) el.classList.add('rc-group-sel');
    });
  }
  function closeGroupPanel() {
    var p = document.querySelector('tr[data-group]');
    if (p) p.remove();
    document.querySelectorAll('tr.rc-group-sel').forEach(function (el) { el.classList.remove('rc-group-sel'); });
    state.openGroup = null;
  }

  // ---- events ------------------------------------------------------------
  function bind() {
    var monthInput = $('reconcile-month');
    if (monthInput) {
      monthInput.addEventListener('change', function () {
        state.month = monthInput.value.slice(0, 7);
        window.location.href = '/spending/reconcile?month=' + encodeURIComponent(state.month);
      });
    }
    var sourceList = $('reconcile-source-list');
    if (sourceList && window.KNOWN_BANK_SOURCES) {
      sourceList.innerHTML = window.KNOWN_BANK_SOURCES.map(function (s) { return '<option value="' + esc(s) + '">'; }).join('');
    }
    var fileInput = $('reconcile-file');
    var fileName = $('reconcile-file-name');
    if (fileInput && fileName) {
      fileInput.addEventListener('change', function () {
        var f = fileInput.files && fileInput.files[0];
        fileName.textContent = f ? f.name : 'No file selected';
      });
    }
    var rangeToggle = $('reconcile-range-toggle');
    var rangeFields = $('reconcile-range-fields');
    if (rangeToggle && rangeFields) {
      rangeToggle.addEventListener('click', function () {
        var open = rangeFields.classList.contains('hidden');
        rangeFields.classList.toggle('hidden', !open);
        rangeToggle.setAttribute('aria-expanded', String(open));
        rangeToggle.textContent = open ? 'Hide date range' : 'Adjust date range';
      });
    }
    var rangeReset = $('reconcile-range-reset');
    if (rangeReset) rangeReset.addEventListener('click', syncRangeFromMonth);

    var uploadBtn = $('reconcile-upload-btn');
    if (uploadBtn) uploadBtn.addEventListener('click', doUpload);
    var autoBtn = $('reconcile-auto-match-btn');
    if (autoBtn) autoBtn.addEventListener('click', function () { runAutoMatch(false); });
    var restart = $('reconcile-restart-match');
    if (restart) restart.addEventListener('click', restartMatching);
    var confirmBtn = $('reconcile-confirm-btn');
    if (confirmBtn) confirmBtn.addEventListener('click', doConfirm);
    var undoBtn = $('reconcile-undo');
    if (undoBtn) undoBtn.addEventListener('click', doUndo);

    var seg = $('reconcile-seg');
    if (seg) seg.addEventListener('click', function (e) {
      var b = e.target.closest('button[data-filter]'); if (!b) return;
      state.filter = b.getAttribute('data-filter');
      state.openResolver = null;
      seg.querySelectorAll('button').forEach(function (x) { x.classList.remove('is-active'); });
      b.classList.add('is-active');
      renderGrid();
    });

    document.addEventListener('click', onDocClick, true);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { closeGroupPanel(); state.openResolver = null; renderGrid(); } });

    $('reconcile-upload-list').addEventListener('click', function (e) {
      var b = e.target.closest('[data-remove-upload]'); if (!b) return;
      var id = b.getAttribute('data-remove-upload');
      api(API + encodeURIComponent(state.month) + '/upload/' + encodeURIComponent(id), { method: 'DELETE' })
        .then(function (d) { refresh(d && d.session ? d.session : d); }).catch(fail);
    });
  }

  function onDocClick(e) {
    var el = e.target;
    var badge = el.closest('.rc-ref-badge');
    if (badge) {
      var ref = badge.getAttribute('data-ref');
      var wasOpen = state.openGroup === ref;
      closeGroupPanel();
      if (!wasOpen) openGroupPanel(badge.closest('tr') && rowsById()[badge.closest('tr').getAttribute('data-id')], ref);
      return;
    }
    var groupBill = el.closest('[data-group-bill]');
    if (groupBill) {
      var ref2 = groupBill.getAttribute('data-group-bill');
      var trs = rowsByRef(ref2);
      closeGroupPanel();
      makeBills(trs.map(function (t) { return t.id; }), trs.length + ' rows marked as bills');
      return;
    }
    if (el.closest('[data-group-cancel]')) { closeGroupPanel(); return; }

    var resolve = el.closest('[data-resolve]');
    if (resolve) {
      var rid = resolve.getAttribute('data-resolve');
      state.openResolver = (state.openResolver === rid) ? null : rid;
      renderGrid();
      return;
    }
    var link = el.closest('[data-link]');
    if (link) {
      var rowEl = link.closest('tr');
      var rowId = rowEl && rowEl.getAttribute('data-id');
      linkManual(rowId, link.getAttribute('data-link'));
      return;
    }
    var impNew = el.closest('[data-import-new]');
    if (impNew) { importAsNew(impNew.getAttribute('data-import-new')); return; }

    var unm = el.closest('[data-unmatch]');
    if (unm) { unmatch(unm.closest('tr').getAttribute('data-id')); return; }
    var mb = el.closest('[data-makebill]');
    if (mb) { makeBills([mb.closest('tr').getAttribute('data-id')], 'Marked as bill'); return; }

    var keep = el.closest('[data-keep]');
    if (keep) { setManualStatus([keep.getAttribute('data-keep')], true); return; }
    var ignore = el.closest('[data-ignore]');
    if (ignore) { setManualStatus([ignore.getAttribute('data-ignore')], false); return; }
  }

  function syncRangeFromMonth() {
    var m = state.month; if (m.length !== 7) return;
    var y = Number(m.slice(0, 4)), mo = Number(m.slice(5, 7));
    var last = new Date(y, mo, 0).getDate();
    var s = $('reconcile-period-start'), e = $('reconcile-period-end');
    if (s) s.value = m + '-01';
    if (e) e.value = m + '-' + String(last).padStart(2, '0');
  }

  function doUpload() {
    var fileInput = $('reconcile-file');
    var f = fileInput && fileInput.files && fileInput.files[0];
    if (!f) { setStatus('Choose a file first', true); return; }
    var fd = new FormData();
    fd.append('file', f);
    var src = $('reconcile-source');
    if (src && src.value.trim()) fd.append('bank_source', src.value.trim());
    var ps = $('reconcile-period-start'), pe = $('reconcile-period-end');
    if (ps && ps.value) fd.append('period_start', ps.value);
    if (pe && pe.value) fd.append('period_end', pe.value);
    setStatus('Reading statement…');
    api(API + encodeURIComponent(state.month) + '/upload', { method: 'POST', body: fd })
      .then(function (d) {
        if (src) src.value = '';
        if (fileInput) fileInput.value = '';
        if ($('reconcile-file-name')) $('reconcile-file-name').textContent = 'No file selected';
        refresh(d && d.session ? d.session : d);
      }).catch(fail);
  }

  function runAutoMatch(isRestart) {
    var path = isRestart ? '/restart-match' : '/auto-match';
    setStatus(isRestart ? 'Restarting matching…' : 'Matching…');
    postRaw(path, {}).then(function (s) { refresh(s); }).catch(fail);
  }
  function restartMatching() {
    if (!window.confirm('Restart matching from scratch? This discards every match, transfer, bill mark, and leftover Keep/Ignore choice, then runs Find matches again on the staged files.')) return;
    runAutoMatch(true);
  }
  function doConfirm() {
    postRaw('/review-unclaimed', {})
      .then(function () { return postRaw('/confirm', {}); })
      .then(function (s) { refresh(s); showToast('Imported'); })
      .catch(fail);
  }

  function loadSession() {
    if (!state.month) return;
    setStatus('Loading…');
    api(API + encodeURIComponent(state.month)).then(function (d) {
      refresh(d && d.session ? d.session : d);
      syncRangeFromMonth();
    }).catch(fail);
  }

  document.addEventListener('DOMContentLoaded', function () {
    bind();
    loadSession();
  });
})();
