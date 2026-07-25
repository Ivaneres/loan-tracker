(function () {
  'use strict';

  const money = (n) => {
    const v = Number(n);
    if (!Number.isFinite(v)) return '£0.00';
    return (
      (v < 0 ? '−' : '') +
      '£' +
      Math.abs(v).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    );
  };

  const $ = (id) => document.getElementById(id);

  let flashTimer = null;

  function showFeedback(el, msg, kind) {
    if (!el) return;
    el.textContent = msg || '';
    el.classList.toggle('hidden', !msg);
    el.classList.toggle('db-flash--error', kind === 'error');
    el.classList.toggle('db-flash--ok', kind === 'ok');
    el.classList.toggle('db-plan-feedback--error', kind === 'error');
    el.classList.toggle('db-plan-feedback--ok', kind === 'ok');
    if (msg) {
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }

  const flash = (msg, kind, targetId) => {
    const el = $(targetId || 'db-status-msg');
    showFeedback(el, msg, kind);
    if (!targetId && msg) {
      const planFb = $('plan-save-feedback');
      if (planFb && !$('panel-plan').hidden) {
        showFeedback(planFb, msg, kind);
      }
    }
    if (msg && kind !== 'error') {
      clearTimeout(flashTimer);
      flashTimer = setTimeout(() => {
        if (el && el.textContent === msg) showFeedback(el, '', null);
        const planFb = $('plan-save-feedback');
        if (planFb && planFb.textContent === msg) showFeedback(planFb, '', null);
      }, 5000);
    }
  };

  function formatSavedAt(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleString('en-GB', {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function setPlanSavedLabel(iso) {
    const el = $('plan-last-saved');
    if (!el) return;
    const label = formatSavedAt(iso);
    if (!label) {
      el.classList.add('hidden');
      el.textContent = '';
      return;
    }
    el.textContent = 'Last saved ' + label;
    el.classList.remove('hidden');
  }

  function pulsePlanMath() {
    const box = $('plan-math');
    if (!box) return;
    box.classList.remove('db-plan-math--saved');
    void box.offsetWidth;
    box.classList.add('db-plan-math--saved');
    setTimeout(() => box.classList.remove('db-plan-math--saved'), 1200);
  }
  let state = null;
  let billItems = [];
  let entryDate = '';
  let viewDate = '';

  function localISODate(d) {
    const dt = d instanceof Date ? d : new Date();
    const y = dt.getFullYear();
    const m = String(dt.getMonth() + 1).padStart(2, '0');
    const day = String(dt.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
  }

  function addDaysISO(iso, delta) {
    const d = new Date(String(iso) + 'T12:00:00');
    d.setDate(d.getDate() + delta);
    return localISODate(d);
  }

  function clampToToday(iso) {
    const today = localISODate();
    const next = String(iso || '').trim() || today;
    return next > today ? today : next;
  }

  function dateOffsetLabel(iso) {
    const today = localISODate();
    if (iso === today) return 'today';
    if (iso === addDaysISO(today, -1)) return 'yesterday';
    if (iso === addDaysISO(today, -2)) return '2 days ago';
    const d = new Date(iso + 'T12:00:00');
    return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
  }

  function updateAddButtonLabel() {
    const btn = $('db-add-btn');
    if (!btn) return;
    const today = localISODate();
    if (entryDate === today) {
      btn.textContent = 'Add spend';
    } else if (entryDate === addDaysISO(today, -1)) {
      btn.textContent = 'Add for yesterday';
    } else if (entryDate === addDaysISO(today, -2)) {
      btn.textContent = 'Add for 2 days ago';
    } else {
      btn.textContent = 'Add for ' + dateOffsetLabel(entryDate);
    }
  }

  function syncChipRow(rowId, selectedIso) {
    const chips = $(rowId);
    if (!chips) return;
    const today = localISODate();
    chips.querySelectorAll('.db-date-chip').forEach((btn) => {
      const offset = Number(btn.dataset.offset);
      const chipDate = addDaysISO(today, offset);
      btn.classList.toggle('db-date-chip--selected', selectedIso === chipDate);
    });
  }

  function syncEntryDateControls() {
    const today = localISODate();
    syncChipRow('db-date-chips', entryDate);
    const input = $('db-entry-date');
    if (input) {
      input.max = today;
      input.value = entryDate;
    }
    const hidden = $('db-entry-date-value');
    if (hidden) hidden.value = entryDate;
    updateAddButtonLabel();
  }

  function syncViewDateControls() {
    const today = localISODate();
    syncChipRow('db-view-date-chips', viewDate);
    const input = $('db-view-date');
    if (input) {
      input.max = today;
      input.value = viewDate;
    }
  }

  function syncDateControls() {
    syncEntryDateControls();
    syncViewDateControls();
  }

  function selectEntryDate(iso) {
    entryDate = clampToToday(iso);
    syncEntryDateControls();
  }

  async function selectViewDate(iso, opts) {
    const options = opts || {};
    const next = clampToToday(iso);
    const changed = next !== viewDate;
    viewDate = next;
    syncViewDateControls();
    if (options.refresh === false || !changed) return;
    try {
      await refresh(viewDate);
    } catch (e) {
      flash(e.message, 'error');
    }
  }

  async function api(url, options) {
    const res = await fetch(url, {
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      ...options,
    });
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      data = null;
    }
    if (!res.ok) {
      const err = new Error((data && data.error) || res.statusText || 'Request failed');
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function setPanel(name) {
    document.querySelectorAll('.db-panel-tab').forEach((btn) => {
      const on = btn.dataset.panel === name;
      btn.classList.toggle('db-panel-tab--active', on);
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    ['today', 'plan', 'goals'].forEach((id) => {
      const panel = $('panel-' + id);
      if (!panel) return;
      const on = id === name;
      panel.classList.toggle('hidden', !on);
      panel.hidden = !on;
    });
    if (name === 'goals') scrollDayChartToToday();
  }

  function renderToday(status) {
    const remaining = status.remaining_today;
    const limit = status.daily_limit;
    const spent = status.spent_today;
    $('db-remaining').textContent = money(remaining);
    $('db-remaining').classList.toggle('db-hero-value--over', remaining < 0);
    $('db-daily-limit').textContent = money(limit);
    $('db-spent-today').textContent = money(spent);
    $('db-underspend').textContent = money(status.underspend_saved);
    const pct = limit > 0 ? Math.min(100, Math.max(0, (spent / limit) * 100)) : spent > 0 ? 100 : 0;
    const bar = $('db-remaining-bar');
    if (bar) {
      bar.style.width = pct + '%';
      bar.classList.toggle('db-hero-bar-fill--over', remaining < 0);
    }
    const d = status.as_of ? new Date(status.as_of + 'T12:00:00') : new Date();
    $('db-hero-date').textContent = d.toLocaleDateString('en-GB', {
      weekday: 'long',
      day: 'numeric',
      month: 'long',
    });

    const list = $('db-today-list');
    const empty = $('db-today-empty');
    const txs = status.transactions_today || [];
    list.innerHTML = '';
    empty.classList.toggle('hidden', txs.length > 0);
    txs.forEach((tx) => {
      const li = document.createElement('li');
      li.className = 'db-today-item';
      const meta = document.createElement('div');
      meta.className = 'db-today-item-main';
      meta.innerHTML =
        '<strong></strong><span class="db-today-cat"></span>';
      meta.querySelector('strong').textContent = tx.description || 'Spend';
      meta.querySelector('.db-today-cat').textContent = tx.category || '';
      const amt = document.createElement('div');
      amt.className = 'db-today-item-amt';
      amt.textContent = money(tx.amount);
      li.appendChild(meta);
      li.appendChild(amt);
      if (tx.source === 'manual') {
        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'db-today-del';
        del.setAttribute('aria-label', 'Delete');
        del.textContent = '×';
        del.addEventListener('click', () => deleteEntry(tx.id));
        li.appendChild(del);
      }
      list.appendChild(li);
    });
  }

  function modeLabel(mode) {
    if (mode === 'fixed') return 'Fixed';
    if (mode === 'carry_surplus') return 'Carry surplus';
    return 'Envelope';
  }

  function daysPhrase(n) {
    const count = Number(n) || 0;
    return count === 1 ? '1 day' : count + ' days';
  }

  function signedMoney(n) {
    const v = Number(n) || 0;
    if (v > 0) return '+' + money(v);
    return money(v);
  }

  function renderPaceMath(status) {
    const box = $('pace-math');
    if (!box) return;
    const math = status.pace_projection || {};
    const mode = math.mode || (status.plan && status.plan.daily_mode) || 'envelope';
    const pool =
      Number(
        math.window_pool != null
          ? math.window_pool
          : math.discretionary_monthly != null
            ? math.discretionary_monthly
            : status.plan && status.plan.discretionary_monthly
      ) || 0;
    const spentSoFar = Number(math.spent_so_far != null ? math.spent_so_far : status.spent_mtd) || 0;
    const remaining =
      Number(
        math.remaining_after_today != null
          ? math.remaining_after_today
          : status.discretionary_remaining_month
      ) || 0;
    const daysElapsed = Number(math.days_elapsed != null ? math.days_elapsed : 0) || 0;
    const daysAfter = Number(math.days_after_today != null ? math.days_after_today : 0) || 0;
    const base = Number(math.base_daily != null ? math.base_daily : 0) || 0;
    const projected = Number(math.projected_daily != null ? math.projected_daily : 0) || 0;
    const vsBase = Number(math.projected_vs_base != null ? math.projected_vs_base : projected - base) || 0;
    const paceTarget = Number(math.pace_target_spend != null ? math.pace_target_spend : 0) || 0;
    const paceDelta = Number(math.pace_delta != null ? math.pace_delta : paceTarget - spentSoFar) || 0;
    const midStart = !!math.mid_period_start;

    let rows = '';
    rows +=
      '<div class="db-plan-math-row db-plan-math-row--income">' +
      '<span>Starting pool this cycle</span><strong>' +
      money(pool) +
      '</strong></div>';
    rows +=
      '<div class="db-plan-math-row db-plan-math-row--sub">' +
      '<span><span class="db-plan-math-op" aria-hidden="true">−</span> Spent so far (' +
      daysPhrase(daysElapsed) +
      ')</span><strong>' +
      money(spentSoFar) +
      '</strong></div>';
    rows +=
      '<div class="db-plan-math-row db-plan-math-row--sub">' +
      '<span><span class="db-plan-math-op" aria-hidden="true">=</span> Remaining after today</span><strong>' +
      money(remaining) +
      '</strong></div>';

    if (daysAfter > 0) {
      rows +=
        '<div class="db-plan-math-row db-plan-math-row--sub">' +
        '<span><span class="db-plan-math-op" aria-hidden="true">÷</span> Days left after today</span><strong>' +
        daysPhrase(daysAfter) +
        '</strong></div>';
      rows +=
        '<div class="db-plan-math-row db-plan-math-row--result">' +
        '<span><span class="db-plan-math-op" aria-hidden="true">=</span> Projected daily going forward</span><strong>' +
        money(projected) +
        '</strong></div>';
    } else {
      rows +=
        '<div class="db-plan-math-row db-plan-math-row--result">' +
        '<span><span class="db-plan-math-op" aria-hidden="true">=</span> Left for today (last day)</span><strong>' +
        money(projected) +
        '</strong></div>';
    }

    rows +=
      '<div class="db-plan-math-row db-plan-math-row--note">' +
      '<span>Original base daily</span><strong>' +
      money(base) +
      '</strong></div>';

    if (mode !== 'fixed') {
      const changeLabel =
        vsBase > 0.005 ? 'Looser than base' : vsBase < -0.005 ? 'Tighter than base' : 'In line with base';
      rows +=
        '<div class="db-plan-math-row db-plan-math-row--note">' +
        '<span>' +
        changeLabel +
        '</span><strong>' +
        signedMoney(vsBase) +
        ' / day</strong></div>';
    }

    let paceNote;
    if (paceDelta > 0.005) {
      paceNote = 'Under pace by ' + money(paceDelta) + ' so far (on-pace was ' + money(paceTarget) + ').';
    } else if (paceDelta < -0.005) {
      paceNote = 'Over pace by ' + money(Math.abs(paceDelta)) + ' so far (on-pace was ' + money(paceTarget) + ').';
    } else {
      paceNote = 'On pace so far (target ' + money(paceTarget) + ' across ' + daysPhrase(daysElapsed) + ').';
    }

    if (mode === 'fixed') {
      paceNote =
        'Fixed mode keeps ' +
        money(base) +
        ' / day regardless of spend. ' +
        paceNote;
    } else if (mode === 'carry_surplus') {
      paceNote =
        'Carry mode rolls unused days forward; the projection above is the average left if you spend evenly. ' +
        paceNote;
    } else {
      paceNote = 'Envelope mode re-splits what’s left across remaining days. ' + paceNote;
    }

    if (midStart) {
      paceNote += ' Starting pool is pro-rated from your tracking start.';
    }

    rows += '<p class="db-limit-math-note">' + paceNote + '</p>';

    box.innerHTML =
      '<p class="db-plan-math-title">Mode · ' + modeLabel(mode) + '</p>' + rows;
  }

  function renderPlanMath(plan) {
    const income = Number(plan.income_monthly) || 0;
    const bills = Number(plan.bills_monthly) || 0;
    const pct = Number(plan.savings_percent) || 0;
    const savings = Math.round(income * (pct / 100) * 100) / 100;
    const disc = Math.max(0, Math.round((income - bills - savings) * 100) / 100);
    const days = (state && (state.days_in_period || state.days_in_month)) || 30;
    const base = days ? Math.round((disc / days) * 100) / 100 : 0;
    const pctLabel = Number.isFinite(pct) ? String(pct).replace(/\.0$/, '') + '%' : '0%';
    const daysLabel = days === 1 ? '1 day' : days + ' days';
    $('plan-math').innerHTML =
      '<p class="db-plan-math-title">Monthly breakdown</p>' +
      '<div class="db-plan-math-row db-plan-math-row--income">' +
      '<span>Total monthly income</span><strong>' +
      money(income) +
      '</strong></div>' +
      '<div class="db-plan-math-row db-plan-math-row--sub">' +
      '<span><span class="db-plan-math-op" aria-hidden="true">−</span> Bills (outgoing)</span><strong>' +
      money(bills) +
      '</strong></div>' +
      '<div class="db-plan-math-row db-plan-math-row--sub">' +
      '<span><span class="db-plan-math-op" aria-hidden="true">−</span> Reserved savings (' +
      pctLabel +
      ')</span><strong>' +
      money(savings) +
      '</strong></div>' +
      '<div class="db-plan-math-row db-plan-math-row--result">' +
      '<span><span class="db-plan-math-op" aria-hidden="true">=</span> Available discretionary</span><strong>' +
      money(disc) +
      '</strong></div>' +
      '<div class="db-plan-math-row db-plan-math-row--note">' +
      '<span>About per day (base · ' +
      daysLabel +
      ')</span><strong>' +
      money(base) +
      '</strong></div>';
  }

  function renderBillList() {
    const list = $('plan-bill-list');
    const empty = $('plan-bills-empty');
    list.innerHTML = '';
    empty.classList.toggle('hidden', billItems.length > 0);
    billItems.forEach((b, idx) => {
      const li = document.createElement('li');
      li.className = 'db-bill-item';
      const check = document.createElement('input');
      check.type = 'checkbox';
      check.checked = b.included !== false;
      check.addEventListener('change', () => {
        billItems[idx].included = check.checked;
        syncBillsTotal();
      });
      const body = document.createElement('div');
      body.className = 'db-bill-body';
      const title = document.createElement('strong');
      title.textContent = b.label || 'Bill';
      const sub = document.createElement('small');
      sub.textContent = (b.category || '') + (b.source ? ' · ' + b.source.replace('_', ' ') : '');
      body.appendChild(title);
      body.appendChild(sub);
      const amt = document.createElement('span');
      amt.className = 'db-bill-amt';
      amt.textContent = money(b.amount);
      const rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'db-today-del';
      rm.textContent = '×';
      rm.addEventListener('click', () => {
        billItems.splice(idx, 1);
        renderBillList();
        syncBillsTotal();
      });
      li.appendChild(check);
      li.appendChild(body);
      li.appendChild(amt);
      li.appendChild(rm);
      list.appendChild(li);
    });
    syncBillsTotal();
  }

  function syncBillsTotal() {
    const total = billItems
      .filter((b) => b.included !== false)
      .reduce((s, b) => s + (Number(b.amount) || 0), 0);
    const plan = {
      income_monthly: Number($('plan-income').value) || 0,
      bills_monthly: total,
      savings_percent: Number($('plan-savings-pct').value) || 0,
    };
    renderPlanMath(plan);
  }

  function fillPlanForm(plan) {
    $('plan-income').value = plan.income_monthly != null ? plan.income_monthly : '';
    $('plan-savings-pct').value = plan.savings_percent != null ? plan.savings_percent : 20;
    const payDayEl = $('plan-pay-day');
    if (payDayEl) {
      payDayEl.value = plan.pay_day != null ? plan.pay_day : 1;
    }
    const trackingEl = $('plan-tracking-from');
    if (trackingEl) {
      trackingEl.value = plan.tracking_from || '';
    }
    const mode = plan.daily_mode || 'envelope';
    document.querySelectorAll('input[name="daily_mode"]').forEach((r) => {
      r.checked = r.value === mode;
    });
    billItems = Array.isArray(plan.bill_items) ? plan.bill_items.map((b) => ({ ...b })) : [];
    if (plan.source_month) {
      const sel = $('plan-source-month');
      if (sel) sel.value = plan.source_month;
    }
    renderBillList();
    renderPlanMath({
      income_monthly: plan.income_monthly,
      bills_monthly: plan.bills_monthly,
      savings_percent: plan.savings_percent,
    });
    setPlanSavedLabel(plan.updated_at);
  }

  let selectedDayKey = null;
  let lastDayStatus = null;
  let dayTooltipOpen = false;

  function formatDayLabel(iso) {
    if (!iso) return '';
    const d = new Date(iso + 'T12:00:00');
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('en-GB', {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
    });
  }

  function dayStatusName(day) {
    const lim = Number(day.limit) || 0;
    const spent = Number(day.spent) || 0;
    return (
      day.status ||
      (spent <= 0 ? 'clear' : spent > lim + 0.005 ? 'over' : spent >= lim - 0.005 ? 'exact' : 'under')
    );
  }

  function dayDeltaLabel(day) {
    const lim = Number(day.limit) || 0;
    const spent = Number(day.spent) || 0;
    const under = Number(day.underspend) || Math.max(0, lim - spent);
    const over = Number(day.overspend) || Math.max(0, spent - lim);
    const status = dayStatusName(day);
    if (status === 'clear') return 'Saved ' + money(lim);
    if (status === 'over') return 'Over ' + money(over);
    if (status === 'exact') return 'Exact';
    return 'Saved ' + money(under);
  }

  function buildDayInsightSummary(insights, days) {
    const n = Number(insights && insights.days_elapsed) || (days && days.length) || 0;
    if (!n) return 'No days yet this period — log spends on Today to see the pattern.';
    const under = Number(insights.days_under) || 0;
    const over = Number(insights.days_over) || 0;
    const streak = Number(insights.under_streak) || 0;
    const parts = [
      'Under on ' + under + ' of ' + n + ' day' + (n === 1 ? '' : 's'),
    ];
    if (streak >= 2) {
      parts.push(streak + '-day streak');
    }
    const best = insights.best_day;
    if (best && Number(best.underspend) > 0) {
      parts.push('best day saved ' + money(best.underspend));
    }
    if (over > 0) {
      parts.push(over + ' over (' + money(insights.overspend_total) + ')');
    } else if (under === n) {
      parts.push('every day on track');
    }
    return parts.join(' · ');
  }

  function hideDayTooltip() {
    const tip = document.querySelector('#goals-day-tooltip');
    if (tip) tip.setAttribute('visibility', 'hidden');
    dayTooltipOpen = false;
    selectedDayKey = null;
    highlightSelectedDot();
  }

  function showDayTooltip(day, dot, chartWidth, chartHeight, padT, padR) {
    const tip = document.querySelector('#goals-day-tooltip');
    if (!tip || !dot || !day) return;

    const hit = dot.querySelector('.db-day-dot-hit');
    if (!hit) return;
    const cx = Number(hit.getAttribute('cx'));
    const cy = Number(hit.getAttribute('cy'));
    if (!Number.isFinite(cx) || !Number.isFinite(cy)) return;

    const status = dayStatusName(day);
    const boxW = 118;
    const boxH = 50;
    let x = cx + 12;
    let y = cy - boxH - 10;
    if (x + boxW > chartWidth - padR) x = cx - boxW - 12;
    if (x < 4) x = 4;
    if (y < padT) y = cy + 14;
    if (y + boxH > chartHeight - 6) y = Math.max(padT, cy - boxH - 10);

    tip.setAttribute('transform', 'translate(' + x.toFixed(1) + ' ' + y.toFixed(1) + ')');
    tip.querySelector('.db-day-tooltip-box').setAttribute('width', String(boxW));
    tip.querySelector('.db-day-tooltip-box').setAttribute('height', String(boxH));
    tip.querySelector('.db-day-tooltip-title').textContent = formatDayLabel(day.date);
    tip.querySelector('.db-day-tooltip-amounts').textContent =
      money(day.spent) + ' / ' + money(day.limit);
    const delta = tip.querySelector('.db-day-tooltip-delta');
    delta.textContent = dayDeltaLabel(day);
    delta.setAttribute('class', 'db-day-tooltip-delta db-day-tooltip-delta--' + status);
    tip.setAttribute('visibility', 'visible');
    dayTooltipOpen = true;
  }

  function toggleDayTooltip(day, dot, chartWidth, chartHeight, padT, padR) {
    if (dayTooltipOpen && selectedDayKey === day.date) {
      hideDayTooltip();
      return;
    }
    selectedDayKey = day.date;
    highlightSelectedDot();
    showDayTooltip(day, dot, chartWidth, chartHeight, padT, padR);
    scrollDayChartToDate(day.date);
  }

  function highlightSelectedDot() {
    const chart = $('goals-day-chart');
    if (!chart) return;
    chart.querySelectorAll('.db-day-dot').forEach((dot) => {
      const on = dot.dataset.date === selectedDayKey;
      dot.classList.toggle('db-day-dot--selected', on);
      dot.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }
  const DAY_CHART_SLOT = 44;
  const DAY_CHART_HIT_RADIUS = 22;
  const DAY_CHART_MARK_RADIUS = 6.5;
  // Keep a little air past the last day mark so it isn't clipped at the viewport edge.
  const DAY_CHART_RIGHT_INSET = 14;
  let dayChartScrollBound = false;

  function dayChartMarkContentX(mark, scroll) {
    const scrollRect = scroll.getBoundingClientRect();
    const markRect = mark.getBoundingClientRect();
    if (!scrollRect.width || !markRect.width) return null;
    return {
      left: markRect.left - scrollRect.left + scroll.scrollLeft,
      center: markRect.left + markRect.width / 2 - scrollRect.left + scroll.scrollLeft,
      right: markRect.right - scrollRect.left + scroll.scrollLeft,
    };
  }

  function dayChartScrollRange(scroll, chart) {
    const nativeMax = Math.max(0, scroll.scrollWidth - scroll.clientWidth);
    const marks = chart ? chart.querySelectorAll('.db-day-dot-mark') : [];
    if (!marks.length || !scroll.clientWidth) {
      return { min: 0, max: nativeMax };
    }
    const last = dayChartMarkContentX(marks[marks.length - 1], scroll);
    if (!last) return { min: 0, max: nativeMax };

    // Left: content start (keeps £ axis labels visible). Right: last day mark inset
    // from the viewport edge — no empty pad beyond that.
    const min = 0;
    const max = Math.max(
      min,
      Math.min(last.right - scroll.clientWidth + DAY_CHART_RIGHT_INSET, nativeMax)
    );
    return { min, max };
  }

  function clampDayChartScroll(target, scroll, chart) {
    const range = dayChartScrollRange(scroll, chart || $('goals-day-chart'));
    return Math.max(range.min, Math.min(target, range.max));
  }

  function bindDayChartScrollLimits() {
    const scroll = $('goals-day-chart-scroll');
    if (!scroll || dayChartScrollBound) return;
    dayChartScrollBound = true;
    let framing = false;
    const enforce = () => {
      const chart = $('goals-day-chart');
      const next = clampDayChartScroll(scroll.scrollLeft, scroll, chart);
      if (Math.abs(next - scroll.scrollLeft) > 0.5) scroll.scrollLeft = next;
    };
    scroll.addEventListener(
      'scroll',
      () => {
        if (framing) return;
        framing = true;
        requestAnimationFrame(() => {
          framing = false;
          enforce();
        });
      },
      { passive: true }
    );
  }

  function scrollDayChartToDate(dateKey, attempt) {
    const scroll = $('goals-day-chart-scroll');
    const chart = $('goals-day-chart');
    if (!scroll || !chart || !dateKey) return;
    const dot = chart.querySelector('.db-day-dot[data-date="' + dateKey + '"]');
    if (!dot) return;
    const mark = dot.querySelector('.db-day-dot-mark') || dot;
    const scrollRect = scroll.getBoundingClientRect();
    const dotRect = mark.getBoundingClientRect();
    // Panel may still be hidden / not laid out — retry a few frames.
    if (!scrollRect.width || !dotRect.width) {
      if ((attempt || 0) < 8) {
        requestAnimationFrame(() => scrollDayChartToDate(dateKey, (attempt || 0) + 1));
      }
      return;
    }

    // Use rendered positions so SVG viewBox scaling stays correct.
    const pos = dayChartMarkContentX(mark, scroll);
    if (!pos) return;
    const ideal = pos.center - scroll.clientWidth / 2;
    scroll.scrollLeft = clampDayChartScroll(ideal, scroll, chart);
  }

  function scrollDayChartToToday(days) {
    const today = (days || (lastDayStatus && lastDayStatus.days) || []).find((d) => d.is_today);
    const dateKey = today ? today.date : null;
    if (!dateKey) return;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => scrollDayChartToDate(dateKey));
    });
  }

  function renderDayLineChart(days) {
    const wrap = $('goals-day-linewrap');
    const chart = $('goals-day-chart');
    const yAxis = $('goals-day-yaxis');
    if (!wrap || !chart) return;

    if (!days.length) {
      wrap.classList.add('hidden');
      wrap.hidden = true;
      chart.innerHTML = '';
      chart.style.width = '';
      chart.style.minWidth = '';
      chart.style.height = '';
      if (yAxis) {
        yAxis.innerHTML = '';
        yAxis.removeAttribute('viewBox');
        yAxis.style.height = '';
      }
      hideDayTooltip();
      return;
    }

    const reopenDate = dayTooltipOpen ? selectedDayKey : null;

    wrap.classList.remove('hidden');
    wrap.hidden = false;
    bindDayChartScrollLimits();

    // Y labels live in a fixed sidebar; scrolling chart only needs a small left inset.
    const axisW = 40;
    const padL = 12;
    const padR = 20 + DAY_CHART_RIGHT_INSET;
    const padT = 16;
    const padB = 36;
    const plotW = days.length <= 1 ? DAY_CHART_SLOT : (days.length - 1) * DAY_CHART_SLOT;
    const width = padL + padR + plotW;
    const height = 188;
    const plotH = height - padT - padB;
    const peak = Math.max(
      1,
      ...days.map((d) => Math.max(Number(d.limit) || 0, Number(d.spent) || 0))
    );
    const xAt = (i) => padL + (days.length === 1 ? plotW / 2 : i * DAY_CHART_SLOT);
    const yAt = (v) => padT + plotH - (Math.max(0, Number(v) || 0) / peak) * plotH;

    const spendPts = days.map((d, i) => ({
      x: xAt(i),
      y: yAt(d.spent),
      day: d,
    }));
    const limitPts = days.map((d, i) => ({ x: xAt(i), y: yAt(d.limit) }));
    const line = (pts) =>
      pts.map((p, i) => (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ');

    const tickVals = [0, peak / 2, peak].map((v) => Math.round(v * 100) / 100);
    const formatTick = (v) =>
      v >= 100
        ? '£' + Math.round(v)
        : '£' + v.toLocaleString('en-GB', { maximumFractionDigits: v % 1 ? 2 : 0 });

    const gridLines = tickVals
      .map((v) => {
        const y = yAt(v);
        return (
          '<line class="db-day-grid" x1="' +
          padL +
          '" x2="' +
          (width - padR) +
          '" y1="' +
          y.toFixed(1) +
          '" y2="' +
          y.toFixed(1) +
          '"></line>'
        );
      })
      .join('');

    if (yAxis) {
      yAxis.setAttribute('viewBox', '0 0 ' + axisW + ' ' + height);
      yAxis.setAttribute('preserveAspectRatio', 'xMaxYMid meet');
      yAxis.style.height = height + 'px';
      yAxis.innerHTML = tickVals
        .map((v) => {
          const y = yAt(v);
          return (
            '<text class="db-day-axis" x="' +
            (axisW - 4) +
            '" y="' +
            (y + 3).toFixed(1) +
            '" text-anchor="end">' +
            formatTick(v) +
            '</text>'
          );
        })
        .join('');
    }

    const xLabels = days
      .map((d, i) => {
        const isToday = !!d.is_today;
        return (
          '<text class="db-day-axis db-day-axis--x' +
          (isToday ? ' db-day-axis--today' : '') +
          '" x="' +
          xAt(i).toFixed(1) +
          '" y="' +
          (height - 10) +
          '" text-anchor="middle">' +
          String(d.date).slice(-2) +
          '</text>'
        );
      })
      .join('');

    chart.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    chart.setAttribute('preserveAspectRatio', 'xMinYMid meet');
    chart.style.width = width + 'px';
    chart.style.minWidth = width + 'px';
    chart.style.height = height + 'px';
    chart.innerHTML =
      gridLines +
      '<path class="db-day-limit-line" d="' +
      line(limitPts) +
      '" fill="none"></path>' +
      '<path class="db-day-spend-line" d="' +
      line(spendPts) +
      '" fill="none"></path>' +
      spendPts
        .map((p) => {
          const status = dayStatusName(p.day);
          const selected = p.day.date === selectedDayKey;
          return (
            '<g class="db-day-dot' +
            (status === 'over' ? ' db-day-dot--over' : '') +
            (p.day.is_today ? ' db-day-dot--today' : '') +
            (selected ? ' db-day-dot--selected' : '') +
            '" data-date="' +
            p.day.date +
            '" tabindex="0" role="button" aria-pressed="' +
            (selected ? 'true' : 'false') +
            '" aria-label="' +
            formatDayLabel(p.day.date) +
            ': spent ' +
            money(p.day.spent) +
            ' of ' +
            money(p.day.limit) +
            '">' +
            '<circle class="db-day-dot-hit" cx="' +
            p.x.toFixed(1) +
            '" cy="' +
            p.y.toFixed(1) +
            '" r="' +
            DAY_CHART_HIT_RADIUS +
            '"></circle>' +
            '<circle class="db-day-dot-mark" cx="' +
            p.x.toFixed(1) +
            '" cy="' +
            p.y.toFixed(1) +
            '" r="' +
            DAY_CHART_MARK_RADIUS +
            '"></circle>' +
            '</g>'
          );
        })
        .join('') +
      xLabels +
      '<g id="goals-day-tooltip" class="db-day-tooltip" visibility="hidden" pointer-events="none">' +
      '<rect class="db-day-tooltip-box" x="0" y="0" width="118" height="50" rx="8" ry="8"></rect>' +
      '<text class="db-day-tooltip-title" x="10" y="15"></text>' +
      '<text class="db-day-tooltip-amounts" x="10" y="31"></text>' +
      '<text class="db-day-tooltip-delta" x="10" y="44"></text>' +
      '</g>';

    chart.querySelectorAll('.db-day-dot').forEach((dot) => {
      const day = days.find((d) => d.date === dot.dataset.date);
      if (!day) return;
      const open = (ev) => {
        ev.stopPropagation();
        toggleDayTooltip(day, dot, width, height, padT, padR);
      };
      dot.addEventListener('click', open);
      dot.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          open(ev);
        }
      });
    });

    const todayDate = (days.find((d) => d.is_today) || days[days.length - 1]).date;
    const scrollDate = reopenDate || todayDate;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        scrollDayChartToDate(scrollDate);
        if (reopenDate) {
          selectedDayKey = reopenDate;
          const dot = chart.querySelector('.db-day-dot[data-date="' + reopenDate + '"]');
          const day = days.find((d) => d.date === reopenDate);
          if (dot && day) {
            highlightSelectedDot();
            showDayTooltip(day, dot, width, height, padT, padR);
          }
        }
      });
    });
  }

  function renderDayByDay(status) {
    lastDayStatus = status;
    const days = status.days || [];
    const insights = status.day_insights || {};
    const insightEl = $('goals-day-insight');
    const statsEl = $('goals-day-stats');

    if (insightEl) {
      insightEl.textContent = buildDayInsightSummary(insights, days);
    }

    if (statsEl) {
      if (!days.length) {
        statsEl.classList.add('hidden');
        statsEl.hidden = true;
        statsEl.innerHTML = '';
      } else {
        statsEl.classList.remove('hidden');
        statsEl.hidden = false;
        const cells = [
          { label: 'Under', value: String(Number(insights.days_under) || 0) },
          { label: 'Over', value: String(Number(insights.days_over) || 0) },
          { label: 'Clear', value: String(Number(insights.days_clear) || 0) },
          { label: 'Avg spend', value: money(insights.avg_spent) },
          { label: 'Streak', value: String(Number(insights.under_streak) || 0) },
        ];
        statsEl.innerHTML = cells
          .map(
            (c) =>
              '<div class="db-day-stat"><span class="db-day-stat-label">' +
              c.label +
              '</span><span class="db-day-stat-value"></span></div>'
          )
          .join('');
        Array.from(statsEl.querySelectorAll('.db-day-stat-value')).forEach((el, i) => {
          el.textContent = cells[i].value;
        });
      }
    }

    if (!days.length) {
      selectedDayKey = null;
      renderDayLineChart([]);
      hideDayTooltip();
      return;
    }

    if (!days.some((d) => d.date === selectedDayKey)) {
      const today = days.find((d) => d.is_today);
      selectedDayKey = today ? today.date : days[days.length - 1].date;
    }

    renderDayLineChart(days);
  }

  function renderGoals(status) {
    $('goals-saved').textContent = money(status.underspend_saved);
    const goals = status.goals || [];
    const list = $('goals-list');
    const empty = $('goals-empty');
    list.innerHTML = '';
    empty.classList.toggle('hidden', goals.length > 0);
    const saved = Number(status.underspend_saved) || 0;
    goals.forEach((g) => {
      const target = Number(g.target_amount) || 1;
      const pct = Math.min(100, Math.round((saved / target) * 1000) / 10);
      const li = document.createElement('li');
      li.className = 'db-goal-card';
      li.innerHTML =
        '<div class="db-goal-top"><strong></strong><button type="button" class="db-today-del" aria-label="Delete goal">×</button></div>' +
        '<div class="db-goal-targets"><span></span><span></span></div>' +
        '<div class="db-goal-bar"><div class="db-goal-bar-fill"></div></div>';
      li.querySelector('strong').textContent = g.name;
      li.querySelector('.db-goal-targets').children[0].textContent = money(saved) + ' of ' + money(target);
      li.querySelector('.db-goal-targets').children[1].textContent = pct + '%';
      li.querySelector('.db-goal-bar-fill').style.width = pct + '%';
      li.querySelector('button').addEventListener('click', () => deleteGoal(g.id));
      list.appendChild(li);
    });

    renderDayByDay(status);
    renderPaceMath(status);
  }

  function applyStatus(status) {
    state = status;
    if (status && status.as_of) {
      viewDate = status.as_of;
      syncViewDateControls();
    }
    renderToday(status);
    renderGoals(status);
    if (status.plan) {
      // Don't clobber bill edits if user is mid-edit on plan — only seed when first load
    }
  }

  async function refresh(dateStr) {
    const iso = clampToToday(dateStr || viewDate || localISODate());
    viewDate = iso;
    const q = '?date=' + encodeURIComponent(iso);
    const data = await api('/api/spending/daily/status' + q);
    applyStatus(data);
    if (data && data.as_of) {
      viewDate = data.as_of;
    }
    syncViewDateControls();
    return data;
  }

  async function loadPlan() {
    const data = await api('/api/spending/daily/plan');
    fillPlanForm(data.plan || {});
  }

  async function deleteEntry(id) {
    try {
      await api('/api/spending/daily/entry/' + encodeURIComponent(id), { method: 'DELETE' });
      await refresh(viewDate);
      flash('Deleted', 'ok');
    } catch (e) {
      flash(e.message, 'error');
    }
  }

  async function deleteGoal(id) {
    try {
      await api('/api/spending/daily/goals/' + encodeURIComponent(id), { method: 'DELETE' });
      await refresh();
      flash('Goal removed', 'ok');
    } catch (e) {
      flash(e.message, 'error');
    }
  }

  function bind() {
    document.querySelectorAll('.db-panel-tab').forEach((btn) => {
      btn.addEventListener('click', () => setPanel(btn.dataset.panel));
    });

    document.addEventListener('click', (ev) => {
      if (!ev.target.closest('.db-day-dot')) hideDayTooltip();
    });
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') hideDayTooltip();
    });

    const titleInput = $('db-title');
    const titleClear = $('db-title-clear');
    const setTitle = (value) => {
      titleInput.value = value;
      titleClear.classList.toggle('hidden', !value);
    };
    const categoryTitle = (category) => {
      const label = String(category || '').replace(/_/g, ' ');
      return label ? label.charAt(0).toUpperCase() + label.slice(1) : '';
    };
    let lastAutoTitle = categoryTitle($('db-category').value);
    titleInput.addEventListener('input', () => {
      titleClear.classList.toggle('hidden', !titleInput.value);
    });
    titleClear.addEventListener('click', () => {
      setTitle('');
      lastAutoTitle = '';
      titleInput.focus();
    });
    setTitle(lastAutoTitle);

    const grid = $('db-cat-grid');
    if (grid) {
      grid.addEventListener('click', (ev) => {
        const btn = ev.target.closest('.db-cat');
        if (!btn) return;
        grid.querySelectorAll('.db-cat').forEach((b) => b.classList.remove('db-cat--selected'));
        btn.classList.add('db-cat--selected');
        $('db-category').value = btn.dataset.category;
        const nextAutoTitle = categoryTitle(btn.dataset.category);
        const current = String(titleInput.value || '');
        if (!current.trim() || current === lastAutoTitle) {
          setTitle(nextAutoTitle);
        }
        lastAutoTitle = nextAutoTitle;
      });
    }

    const dateChips = $('db-date-chips');
    if (dateChips) {
      dateChips.addEventListener('click', (ev) => {
        const btn = ev.target.closest('.db-date-chip');
        if (!btn) return;
        const offset = Number(btn.dataset.offset);
        selectEntryDate(addDaysISO(localISODate(), offset));
      });
    }
    const dateInput = $('db-entry-date');
    if (dateInput) {
      dateInput.addEventListener('change', () => {
        selectEntryDate(dateInput.value || localISODate());
      });
    }

    const viewChips = $('db-view-date-chips');
    if (viewChips) {
      viewChips.addEventListener('click', (ev) => {
        const btn = ev.target.closest('.db-date-chip');
        if (!btn) return;
        const offset = Number(btn.dataset.offset);
        selectViewDate(addDaysISO(localISODate(), offset));
      });
    }
    const viewDateInput = $('db-view-date');
    if (viewDateInput) {
      viewDateInput.addEventListener('change', () => {
        selectViewDate(viewDateInput.value || localISODate());
      });
    }

    $('db-entry-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const raw = String($('db-amount').value || '').replace(/,/g, '').trim();
      const amount = Number(raw);
      if (!Number.isFinite(amount) || amount <= 0) {
        flash('Enter a valid amount', 'error');
        return;
      }
      const title = String($('db-title').value || '').trim();
      if (!title) {
        flash('Add a short title', 'error');
        return;
      }
      const spendDate = clampToToday(
        ($('db-entry-date-value') && $('db-entry-date-value').value) || entryDate || localISODate()
      );
      const btn = $('db-add-btn');
      btn.disabled = true;
      try {
        const data = await api('/api/spending/daily/entry', {
          method: 'POST',
          body: JSON.stringify({
            amount,
            title,
            category: $('db-category').value || 'other',
            date: spendDate,
          }),
        });
        entryDate = spendDate;
        // Jump the spends viewer to the day just logged so the new item is visible.
        viewDate = (data.status && data.status.as_of) || spendDate;
        applyStatus(data.status);
        syncDateControls();
        $('db-amount').value = '';
        lastAutoTitle = categoryTitle($('db-category').value);
        setTitle(lastAutoTitle);
        $('db-amount').focus();
        const when = dateOffsetLabel(spendDate);
        flash(when === 'today' ? 'Logged' : 'Logged for ' + when, 'ok');
      } catch (e) {
        flash(e.message, 'error');
      } finally {
        btn.disabled = false;
        updateAddButtonLabel();
      }
    });

    ['plan-income', 'plan-savings-pct'].forEach((id) => {
      const el = $(id);
      if (el) el.addEventListener('input', syncBillsTotal);
    });

    $('plan-save-btn').addEventListener('click', async () => {
      const saveBtn = $('plan-save-btn');
      const modeEl = document.querySelector('input[name="daily_mode"]:checked');
      const body = {
        income_monthly: Number($('plan-income').value) || 0,
        savings_percent: Number($('plan-savings-pct').value) || 0,
        daily_mode: modeEl ? modeEl.value : 'envelope',
        bill_items: billItems,
        source_month: $('plan-source-month').value || null,
        pay_day: Number($('plan-pay-day') && $('plan-pay-day').value) || 1,
        tracking_from: ($('plan-tracking-from') && $('plan-tracking-from').value) || null,
      };
      const prevLabel = saveBtn.textContent;
      saveBtn.disabled = true;
      saveBtn.textContent = 'Saving…';
      showFeedback($('plan-save-feedback'), '', null);
      try {
        const data = await api('/api/spending/daily/plan', {
          method: 'PUT',
          body: JSON.stringify(body),
        });
        if (data.status) applyStatus(data.status);
        fillPlanForm(data.plan);
        pulsePlanMath();
        const savedMsg = 'Plan saved — daily limit updated on Today.';
        flash(savedMsg, 'ok', 'plan-save-feedback');
        saveBtn.textContent = 'Saved';
        saveBtn.classList.add('db-cta--success');
        setTimeout(() => {
          saveBtn.textContent = prevLabel;
          saveBtn.classList.remove('db-cta--success');
        }, 2000);
      } catch (e) {
        flash(e.message, 'error', 'plan-save-feedback');
        saveBtn.textContent = prevLabel;
      } finally {
        saveBtn.disabled = false;
      }
    });

    $('plan-pull-btn').addEventListener('click', async () => {
      const month = $('plan-source-month').value || undefined;
      const btn = $('plan-pull-btn');
      const prevLabel = btn.textContent;
      btn.disabled = true;
      btn.textContent = 'Pulling…';
      try {
        const data = await api('/api/spending/daily/plan/from-statements', {
          method: 'POST',
          body: JSON.stringify({ month, use_llm: true, apply: false }),
        });
        const est = data.estimate || {};
        if (est.income_monthly != null) $('plan-income').value = est.income_monthly;
        billItems = Array.isArray(est.bill_items) ? est.bill_items.map((b) => ({ ...b })) : [];
        if (est.month) $('plan-source-month').value = est.month;
        renderBillList();
        const llmNote = est.llm_flagged_count
          ? ' · AI flagged ' + est.llm_flagged_count + ' regular bill(s)'
          : '';
        flash('Loaded from ' + (est.month || 'statements') + llmNote + '. Review bills, then Save plan.', 'ok', 'plan-save-feedback');
      } catch (e) {
        flash(e.message, 'error', 'plan-save-feedback');
      } finally {
        btn.disabled = false;
        btn.textContent = prevLabel;
      }
    });

    $('bill-add-btn').addEventListener('click', () => {
      const label = String($('bill-add-label').value || '').trim();
      const amount = Number($('bill-add-amount').value);
      if (!label || !Number.isFinite(amount) || amount <= 0) {
        flash('Add a bill name and amount', 'error');
        return;
      }
      billItems.push({
        id: 'local-' + Date.now(),
        label,
        amount,
        category: 'other',
        included: true,
        source: 'manual',
      });
      $('bill-add-label').value = '';
      $('bill-add-amount').value = '';
      renderBillList();
    });

    $('goal-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      try {
        await api('/api/spending/daily/goals', {
          method: 'POST',
          body: JSON.stringify({
            name: $('goal-name').value,
            target_amount: Number($('goal-target').value),
          }),
        });
        $('goal-name').value = '';
        $('goal-target').value = '';
        await refresh();
        flash('Goal added', 'ok');
      } catch (e) {
        flash(e.message, 'error');
      }
    });
  }

  document.addEventListener('DOMContentLoaded', async () => {
    entryDate = localISODate();
    viewDate = localISODate();
    syncDateControls();
    bind();
    try {
      await refresh(viewDate);
      await loadPlan();
    } catch (e) {
      flash(e.message || 'Failed to load daily budget', 'error');
    }
  });
})();
