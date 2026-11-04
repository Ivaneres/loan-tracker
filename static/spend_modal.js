/* Global quick-log modal.
   On the Daily page (#db-entry-form exists and daily_budget.js owns it) this only
   handles opening/closing and reacting to "tally:spend-logged".
   On every other page it also binds the form and posts to the same endpoint. */
(function () {
    'use strict';

    var fab = document.getElementById('spend-fab');
    var modal = document.getElementById('spend-modal');
    if (!fab || !modal) return;

    var form = document.getElementById('db-entry-form');
    var amountEl = document.getElementById('db-amount');
    var titleEl = document.getElementById('db-title');
    var titleClear = document.getElementById('db-title-clear');
    var suggestionsEl = document.getElementById('db-title-suggestions');
    var categoryEl = document.getElementById('db-category');
    var catGrid = document.getElementById('db-cat-grid');
    var dateChips = document.getElementById('db-date-chips');
    var dateInput = document.getElementById('db-entry-date');
    var dateValue = document.getElementById('db-entry-date-value');
    var addBtn = document.getElementById('db-add-btn');

    // daily_budget.js already binds the form on the Daily page.
    var ownsForm = !document.body.classList.contains('db-page');
    var lastFocus = null;
    var entryDate = '';
    var suggestionsLoaded = false;
    var lastAutoTitle = '';

    function categoryLabel(cat) {
        var label = String(cat || '').replace(/_/g, ' ');
        return label ? label.charAt(0).toUpperCase() + label.slice(1) : '';
    }

    function isoToday() {
        var d = new Date();
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    }

    function addDays(iso, days) {
        var d = new Date(iso + 'T12:00:00');
        d.setDate(d.getDate() + days);
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    }

    function syncDates() {
        if (!entryDate) entryDate = isoToday();
        if (dateValue) dateValue.value = entryDate;
        if (dateInput) {
            dateInput.max = isoToday();
            dateInput.value = entryDate;
        }
        if (dateChips) {
            dateChips.querySelectorAll('.db-date-chip').forEach(function (btn) {
                btn.classList.toggle('db-date-chip--selected', addDays(isoToday(), Number(btn.dataset.offset)) === entryDate);
            });
        }
        if (addBtn) addBtn.textContent = entryDate === isoToday() ? 'Add spend' : 'Add for ' + entryDate;
    }

    function toast(message) {
        var el = document.createElement('div');
        el.className = 'spend-toast';
        el.setAttribute('role', 'status');
        el.textContent = message;
        document.body.appendChild(el);
        setTimeout(function () { el.classList.add('spend-toast--out'); }, 2200);
        setTimeout(function () { el.remove(); }, 2700);
    }

    function open() {
        lastFocus = document.activeElement;
        modal.hidden = false;
        document.body.classList.add('spend-modal-open');
        // On the Daily page daily_budget.js owns the form and its date state.
        if (ownsForm) {
            syncDates();
            lastAutoTitle = categoryLabel(categoryEl ? categoryEl.value : '');
            if (amountEl) amountEl.value = '';
            if (titleEl && !titleEl.value.trim()) titleEl.value = lastAutoTitle;
            loadSuggestions();
        }
        if (amountEl) setTimeout(function () { amountEl.focus(); }, 30);
    }

    function close() {
        modal.hidden = true;
        document.body.classList.remove('spend-modal-open');
        if (lastFocus && lastFocus.focus) lastFocus.focus();
    }

    function renderSuggestions(byCat) {
        if (!suggestionsEl || !titleEl || !categoryEl) return;
        var titles = (byCat && byCat[categoryEl.value]) || [];
        suggestionsEl.innerHTML = '';
        if (!titles.length) {
            suggestionsEl.classList.add('hidden');
            return;
        }
        var current = String(titleEl.value || '').trim();
        titles.slice(0, 3).forEach(function (title) {
            var label = String(title || '').trim();
            if (!label) return;
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'db-title-chip' + (current === label ? ' db-title-chip--selected' : '');
            btn.dataset.title = label;
            btn.textContent = label;
            suggestionsEl.appendChild(btn);
        });
        suggestionsEl.classList.toggle('hidden', !suggestionsEl.childElementCount);
    }

    function loadSuggestions() {
        if (suggestionsLoaded) return;
        suggestionsLoaded = true;
        fetch('/api/spending/daily/status', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (data && data.common_titles_by_category) renderSuggestions(data.common_titles_by_category);
            })
            .catch(function () { /* suggestions are optional */ });
    }

    fab.addEventListener('click', open);
    modal.querySelectorAll('[data-spend-close]').forEach(function (el) {
        el.addEventListener('click', close);
    });
    document.addEventListener('keydown', function (ev) {
        if (ev.key === 'Escape' && !modal.hidden) close();
    });
    document.addEventListener('tally:spend-logged', function () {
        if (!modal.hidden) close();
    });

    // ---- Form binding (non-Daily pages only) ----
    if (!ownsForm || !form) return;

    catGrid && catGrid.addEventListener('click', function (ev) {
        var btn = ev.target.closest('.db-cat');
        if (!btn) return;
        catGrid.querySelectorAll('.db-cat').forEach(function (b) { b.classList.remove('db-cat--selected'); });
        btn.classList.add('db-cat--selected');
        if (categoryEl) categoryEl.value = btn.dataset.category;
        var nextAutoTitle = categoryLabel(btn.dataset.category);
        var current = String(titleEl ? titleEl.value : '');
        if (titleEl && (!current.trim() || current === lastAutoTitle)) titleEl.value = nextAutoTitle;
        lastAutoTitle = nextAutoTitle;
        if (suggestionsEl) suggestionsEl.classList.add('hidden');
        loadSuggestionsFor(categoryEl ? categoryEl.value : '');
    });

    function loadSuggestionsFor(cat) {
        fetch('/api/spending/daily/status', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (data && data.common_titles_by_category) {
                    suggestionsLoaded = true;
                    renderSuggestions(data.common_titles_by_category);
                }
            })
            .catch(function () {});
    }

    suggestionsEl && suggestionsEl.addEventListener('click', function (ev) {
        var btn = ev.target.closest('.db-title-chip');
        if (!btn || !titleEl) return;
        titleEl.value = btn.dataset.title || btn.textContent || '';
        if (titleClear) titleClear.classList.remove('hidden');
        suggestionsEl.querySelectorAll('.db-title-chip').forEach(function (b) { b.classList.remove('db-title-chip--selected'); });
        btn.classList.add('db-title-chip--selected');
        titleEl.focus();
    });

    titleEl && titleEl.addEventListener('input', function () {
        if (titleClear) titleClear.classList.toggle('hidden', !titleEl.value);
    });
    titleClear && titleClear.addEventListener('click', function () {
        if (titleEl) { titleEl.value = ''; titleEl.focus(); }
        titleClear.classList.add('hidden');
    });

    dateChips && dateChips.addEventListener('click', function (ev) {
        var btn = ev.target.closest('.db-date-chip');
        if (!btn) return;
        entryDate = addDays(isoToday(), Number(btn.dataset.offset));
        syncDates();
    });
    dateInput && dateInput.addEventListener('change', function () {
        entryDate = dateInput.value || isoToday();
        syncDates();
    });

    form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        var amount = Number(String(amountEl.value || '').replace(/,/g, '').trim());
        if (!isFinite(amount) || amount <= 0) { toast('Enter a valid amount'); return; }
        var title = String(titleEl.value || '').trim();
        if (!title) { toast('Add a short title'); return; }
        if (addBtn) addBtn.disabled = true;
        fetch('/api/spending/daily/entry', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                amount: amount,
                title: title,
                category: (categoryEl && categoryEl.value) || 'other',
                date: entryDate || isoToday()
            })
        })
            .then(function (r) {
                return r.json().then(function (body) {
                    if (!r.ok) throw new Error(body && body.error ? body.error : 'Could not log spend');
                    return body;
                });
            })
            .then(function (body) {
                toast('Logged');
                document.dispatchEvent(new CustomEvent('tally:spend-logged', { detail: body }));
            })
            .catch(function (err) { toast(err.message || 'Could not log spend'); })
            .finally(function () { if (addBtn) addBtn.disabled = false; });
    });
})();
