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

  function renderPlanMath(plan) {
    const income = Number(plan.income_monthly) || 0;
    const bills = Number(plan.bills_monthly) || 0;
    const pct = Number(plan.savings_percent) || 0;
    const savings = Math.round(income * (pct / 100) * 100) / 100;
    const disc = Math.max(0, Math.round((income - bills - savings) * 100) / 100);
    const days = (state && state.days_in_month) || 30;
    const base = days ? Math.round((disc / days) * 100) / 100 : 0;
    $('plan-math').innerHTML =
      '<div class="db-plan-math-row"><span>Reserved savings</span><strong>' +
      money(savings) +
      '</strong></div>' +
      '<div class="db-plan-math-row"><span>Discretionary this month</span><strong>' +
      money(disc) +
      '</strong></div>' +
      '<div class="db-plan-math-row"><span>About per day (base)</span><strong>' +
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
  let dayViewMode = 'chart';
  let lastDayStatus = null;

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
    if (status === 'clear') return 'saved ' + money(lim);
    if (status === 'over') return 'over ' + money(over);
    if (status === 'exact') return 'exact';
    return 'saved ' + money(under);
  }

  function setDayViewMode(mode) {
    dayViewMode = mode === 'numbers' ? 'numbers' : 'chart';
    document.querySelectorAll('.db-day-view-btn').forEach((btn) => {
      const on = btn.dataset.dayView === dayViewMode;
      btn.classList.toggle('db-day-view-btn--active', on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    if (lastDayStatus) renderDayByDay(lastDayStatus);
  }

  function buildDayInsightSummary(insights, days) {
    const n = Number(insights && insights.days_elapsed) || (days && days.length) || 0;
    if (!n) return 'No days yet this month — log spends on Today to see the pattern.';
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

  function renderDayDetail(day) {
    const detail = $('goals-day-detail');
    if (!detail) return;
    if (!day) {
      detail.hidden = true;
      detail.innerHTML = '';
      return;
    }
    const lim = Number(day.limit) || 0;
    const spent = Number(day.spent) || 0;
    const under = Number(day.underspend) || 0;
    const over = Number(day.overspend) || Math.max(0, spent - lim);
    const status = day.status || (spent <= 0 ? 'clear' : spent > lim ? 'over' : 'under');
    let outcome;
    if (status === 'clear') {
      outcome = 'Clear day — saved the full ' + money(lim) + ' allowance';
    } else if (status === 'over') {
      outcome = 'Over by ' + money(over);
    } else if (status === 'exact') {
      outcome = 'Hit the limit exactly';
    } else {
      outcome = 'Saved ' + money(under) + ' toward underspend';
    }
    detail.hidden = false;
    detail.innerHTML =
      '<div class="db-day-detail-top">' +
      '<strong></strong>' +
      '<span class="db-day-detail-status db-day-detail-status--' +
      status +
      '"></span>' +
      '</div>' +
      '<div class="db-day-detail-grid">' +
      '<div><span class="db-meta-label">Spent</span><span class="db-meta-value"></span></div>' +
      '<div><span class="db-meta-label">Limit</span><span class="db-meta-value"></span></div>' +
      '</div>' +
      '<p class="db-day-detail-outcome"></p>';
    detail.querySelector('strong').textContent = formatDayLabel(day.date);
    detail.querySelector('.db-day-detail-status').textContent =
      status === 'clear' ? 'Clear' : status === 'over' ? 'Over' : status === 'exact' ? 'Exact' : 'Under';
    detail.querySelectorAll('.db-meta-value')[0].textContent = money(spent);
    detail.querySelectorAll('.db-meta-value')[1].textContent = money(lim);
    detail.querySelector('.db-day-detail-outcome').textContent = outcome;
  }

  function renderDayByDay(status) {
    lastDayStatus = status;
    const days = status.days || [];
    const insights = status.day_insights || {};
    const insightEl = $('goals-day-insight');
    const statsEl = $('goals-day-stats');
    const legendEl = $('goals-day-legend');
    const chart = $('goals-day-chart');
    const numbers = $('goals-day-numbers');
    const showNumbers = dayViewMode === 'numbers';

    if (insightEl) {
      insightEl.textContent = buildDayInsightSummary(insights, days);
    }

    if (statsEl) {
      if (!days.length) {
        statsEl.hidden = true;
        statsEl.innerHTML = '';
      } else {
        statsEl.hidden = false;
        const cells = [
          { label: 'Under', value: String(Number(insights.days_under) || 0) },
          { label: 'Over', value: String(Number(insights.days_over) || 0) },
          { label: 'Clear', value: String(Number(insights.days_clear) || 0) },
          {
            label: 'Avg spend',
            value: money(insights.avg_spent),
          },
          {
            label: 'Streak',
            value: String(Number(insights.under_streak) || 0),
          },
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

    if (legendEl) {
      legendEl.classList.toggle('hidden', !days.length || showNumbers);
      legendEl.hidden = !days.length || showNumbers;
    }
    if (chart) {
      const hideChart = showNumbers || !days.length;
      chart.classList.toggle('hidden', hideChart);
      chart.hidden = hideChart;
      chart.innerHTML = '';
    }
    if (numbers) {
      const hideNumbers = !showNumbers || !days.length;
      numbers.classList.toggle('hidden', hideNumbers);
      numbers.hidden = hideNumbers;
      numbers.innerHTML = '';
    }

    if (!days.length) {
      selectedDayKey = null;
      renderDayDetail(null);
      return;
    }

    const peak = days.reduce((m, day) => Math.max(m, Number(day.limit) || 0, Number(day.spent) || 0), 1);
    let focus = days.find((d) => d.date === selectedDayKey);
    if (!focus) {
      focus = days.find((d) => d.is_today) || days[days.length - 1];
      selectedDayKey = focus ? focus.date : null;
    }

    const selectDay = (day) => {
      selectedDayKey = day.date;
      renderDayByDay(status);
    };

    if (!showNumbers && chart) {
      days.forEach((day) => {
        const lim = Number(day.limit) || 0;
        const spent = Number(day.spent) || 0;
        const statusName = dayStatusName(day);
        const spendH = Math.round((spent / peak) * 100);
        const limH = Math.round((lim / peak) * 100);
        const col = document.createElement('button');
        col.type = 'button';
        col.className =
          'db-day-col' +
          (day.is_today ? ' db-day-col--today' : '') +
          (day.date === selectedDayKey ? ' db-day-col--selected' : '') +
          (Number(day.weekday) >= 5 ? ' db-day-col--weekend' : '');
        col.setAttribute('role', 'listitem');
        col.setAttribute(
          'aria-label',
          formatDayLabel(day.date) +
            ': spent ' +
            money(spent) +
            ' of ' +
            money(lim) +
            ', ' +
            statusName
        );
        col.dataset.date = day.date;
        col.innerHTML =
          '<div class="db-day-bars">' +
          '<div class="db-day-lim" style="height:' +
          limH +
          '%"></div>' +
          '<div class="db-day-spend db-day-spend--' +
          statusName +
          (spent > lim ? ' db-day-spend--over' : '') +
          '" style="height:' +
          Math.max(spendH, spent > 0 ? 4 : 0) +
          '%"></div>' +
          '</div>' +
          '<span>' +
          String(day.date).slice(-2) +
          '</span>';
        col.addEventListener('click', () => selectDay(day));
        chart.appendChild(col);
      });
    }

    if (showNumbers && numbers) {
      days.forEach((day) => {
        const lim = Number(day.limit) || 0;
        const spent = Number(day.spent) || 0;
        const statusName = dayStatusName(day);
        const li = document.createElement('li');
        li.className =
          'db-day-number-row' +
          (day.date === selectedDayKey ? ' db-day-number-row--selected' : '') +
          (day.is_today ? ' db-day-number-row--today' : '');
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'db-day-number-btn';
        btn.setAttribute(
          'aria-label',
          formatDayLabel(day.date) +
            ': spent ' +
            money(spent) +
            ' of ' +
            money(lim) +
            ', ' +
            statusName
        );
        btn.innerHTML =
          '<span class="db-day-number-date"></span>' +
          '<span class="db-day-number-spent"></span>' +
          '<span class="db-day-number-limit"></span>' +
          '<span class="db-day-number-delta db-day-number-delta--' +
          statusName +
          '"></span>';
        btn.querySelector('.db-day-number-date').textContent = formatDayLabel(day.date);
        btn.querySelector('.db-day-number-spent').textContent = money(spent);
        btn.querySelector('.db-day-number-limit').textContent = 'of ' + money(lim);
        btn.querySelector('.db-day-number-delta').textContent = dayDeltaLabel(day);
        btn.addEventListener('click', () => selectDay(day));
        li.appendChild(btn);
        numbers.appendChild(li);
      });
    }

    renderDayDetail(focus);
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
  }

  function applyStatus(status) {
    state = status;
    renderToday(status);
    renderGoals(status);
    if (status.plan) {
      // Don't clobber bill edits if user is mid-edit on plan — only seed when first load
    }
  }

  async function refresh() {
    const data = await api('/api/spending/daily/status');
    applyStatus(data);
    return data;
  }

  async function loadPlan() {
    const data = await api('/api/spending/daily/plan');
    fillPlanForm(data.plan || {});
  }

  async function deleteEntry(id) {
    try {
      const data = await api('/api/spending/daily/entry/' + encodeURIComponent(id), { method: 'DELETE' });
      applyStatus(data.status);
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

    document.querySelectorAll('.db-day-view-btn').forEach((btn) => {
      btn.addEventListener('click', () => setDayViewMode(btn.dataset.dayView));
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
    titleInput.addEventListener('input', () => {
      titleClear.classList.toggle('hidden', !titleInput.value);
    });
    titleClear.addEventListener('click', () => {
      setTitle('');
      titleInput.focus();
    });
    setTitle(categoryTitle($('db-category').value));

    const grid = $('db-cat-grid');
    if (grid) {
      grid.addEventListener('click', (ev) => {
        const btn = ev.target.closest('.db-cat');
        if (!btn) return;
        grid.querySelectorAll('.db-cat').forEach((b) => b.classList.remove('db-cat--selected'));
        btn.classList.add('db-cat--selected');
        $('db-category').value = btn.dataset.category;
        setTitle(categoryTitle(btn.dataset.category));
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
      const btn = $('db-add-btn');
      btn.disabled = true;
      try {
        const data = await api('/api/spending/daily/entry', {
          method: 'POST',
          body: JSON.stringify({
            amount,
            title,
            category: $('db-category').value || 'other',
          }),
        });
        applyStatus(data.status);
        $('db-amount').value = '';
        setTitle(categoryTitle($('db-category').value));
        $('db-amount').focus();
        flash('Logged', 'ok');
      } catch (e) {
        flash(e.message, 'error');
      } finally {
        btn.disabled = false;
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
        fillPlanForm(data.plan);
        if (data.status) applyStatus(data.status);
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
    bind();
    try {
      await refresh();
      await loadPlan();
    } catch (e) {
      flash(e.message || 'Failed to load daily budget', 'error');
    }
  });
})();
