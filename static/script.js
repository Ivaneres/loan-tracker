/**
 * Client-side filter for the audit log table (#transactions-table).
 * Compares row dates as YYYY-MM-DD strings (lexicographic order matches chronological).
 */
function applyAuditLogFilters() {
    const tbody = document.querySelector('#transactions-table tbody');
    if (!tbody) return;

    const searchEl = document.getElementById('audit-search');
    const opEl = document.getElementById('audit-date-op');
    const singleEl = document.getElementById('audit-date-single');
    const startEl = document.getElementById('audit-date-start');
    const endEl = document.getElementById('audit-date-end');

    const q = (searchEl && searchEl.value) ? searchEl.value.trim().toLowerCase() : '';
    const op = (opEl && opEl.value) ? opEl.value : '';
    const single = singleEl && singleEl.value ? singleEl.value : '';
    const start = startEl && startEl.value ? startEl.value : '';
    const end = endEl && endEl.value ? endEl.value : '';

    function rowMatchesSearch(tr) {
        if (!q) return true;
        const text = tr.textContent.replace(/\s+/g, ' ').trim().toLowerCase();
        return text.includes(q);
    }

    function rowMatchesDate(tr) {
        const rowDate = tr.getAttribute('data-row-date') || '';
        if (!op) return true;
        if (op === 'between') {
            if (!start || !end) return true;
            const lo = start <= end ? start : end;
            const hi = start <= end ? end : start;
            return rowDate >= lo && rowDate <= hi;
        }
        if (!single) return true;
        if (op === 'lt') return rowDate < single;
        if (op === 'gt') return rowDate > single;
        if (op === 'eq') return rowDate === single;
        return true;
    }

    tbody.querySelectorAll('tr.audit-log-row').forEach((tr) => {
        const show = rowMatchesSearch(tr) && rowMatchesDate(tr);
        tr.style.display = show ? '' : 'none';
    });
}

function syncAuditDateFilterControls() {
    const opEl = document.getElementById('audit-date-op');
    const singleWrap = document.getElementById('audit-date-single-wrap');
    const betweenWrap = document.getElementById('audit-date-between-wrap');
    if (!opEl || !singleWrap || !betweenWrap) return;
    const op = opEl.value;
    const isBetween = op === 'between';
    singleWrap.classList.toggle('hidden', isBetween);
    betweenWrap.classList.toggle('hidden', !isBetween);
}

document.addEventListener('DOMContentLoaded', function() {
    const loanId = window.location.pathname.split('/').pop();

    const el = {
        loanNameDisplay: document.getElementById('loan-name-display'),
        loanNameDisplayMobile: document.getElementById('loan-name-display-mobile'),
        editLoanNameButton: document.getElementById('edit-loan-name'),
        editLoanNameButtonMobile: document.getElementById('edit-loan-name-mobile'),
        loanNameEdit: document.getElementById('loan-name-edit'),
        loanNameInput: document.getElementById('loan-name-input'),
        saveLoanNameButton: document.getElementById('save-loan-name'),
        cancelLoanNameButton: document.getElementById('cancel-loan-name'),
        interestRateInput: document.getElementById('interest-rate-input'),
        interestRateInputMobile: document.getElementById('interest-rate-input-mobile'),
        editInterestRateButton: document.getElementById('edit-interest-rate'),
        editInterestRateButtonMobile: document.getElementById('edit-interest-rate-mobile'),
        applyInterestRateButton: document.getElementById('apply-interest-rate'),
        applyInterestRateButtonMobile: document.getElementById('apply-interest-rate-mobile'),
        interestDayInput: document.getElementById('interest-day'),
        interestDayInputMobile: document.getElementById('interest-day-mobile'),
        editInterestDayButton: document.getElementById('edit-interest-day'),
        editInterestDayButtonMobile: document.getElementById('edit-interest-day-mobile'),
        applyInterestDayButton: document.getElementById('apply-interest-day'),
        applyInterestDayButtonMobile: document.getElementById('apply-interest-day-mobile'),
        transactionForm: document.getElementById('transaction-form'),
        transactionFormMobile: document.getElementById('transaction-form-mobile'),
        applyInterestButton: document.getElementById('apply-interest'),
        applyInterestButtonMobile: document.getElementById('apply-interest-mobile'),
        deleteLoanButton: document.getElementById('deleteLoanBtn'),
        deleteLoanButtonMobile: document.getElementById('deleteLoanBtnMobile'),
        recurringPaymentForm: document.getElementById('recurring-payment-form'),
        recurringPaymentFormMobile: document.getElementById('recurring-payment-form-mobile')
    };

    const desktopRate = el.interestRateInput;
    const mobileRate = el.interestRateInputMobile;
    const desktopDay = el.interestDayInput;
    const mobileDay = el.interestDayInputMobile;
    let originalLoanName = el.loanNameDisplay?.textContent || el.loanNameDisplayMobile?.textContent || '';
    let originalInterestRate = desktopRate?.value || mobileRate?.value || '0';
    let originalInterestDay = desktopDay?.value || mobileDay?.value || '1';

    function setLoanName(name) {
        if (el.loanNameDisplay) el.loanNameDisplay.textContent = name;
        if (el.loanNameDisplayMobile) el.loanNameDisplayMobile.textContent = name;
    }

    function resetLoanNameUI() {
        if (el.loanNameDisplay) el.loanNameDisplay.style.display = 'block';
        if (el.editLoanNameButton) el.editLoanNameButton.style.display = 'inline-block';
        if (el.loanNameEdit) el.loanNameEdit.style.display = 'none';
    }

    if (el.editLoanNameButton) {
        el.editLoanNameButton.addEventListener('click', function() {
            if (!el.loanNameDisplay || !el.loanNameEdit || !el.loanNameInput) return;
            el.loanNameDisplay.style.display = 'none';
            el.editLoanNameButton.style.display = 'none';
            el.loanNameEdit.style.display = 'flex';
            el.loanNameInput.value = originalLoanName;
            el.loanNameInput.focus();
            el.loanNameInput.select();
        });
    }

    async function submitLoanName(newName) {
        if (newName === '') {
            alert('Loan name cannot be empty');
            return;
        }
        if (newName === originalLoanName) {
            resetLoanNameUI();
            return;
        }

        try {
            const response = await fetch(`/api/loan/${loanId}/update_name`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName })
            });
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to update loan name');
            }
            await response.json();
            originalLoanName = newName;
            setLoanName(newName);
            resetLoanNameUI();
        } catch (error) {
            console.error('Error:', error);
            alert(error.message || 'Failed to update loan name');
            resetLoanNameUI();
        }
    }

    if (el.saveLoanNameButton && el.loanNameInput) {
        el.saveLoanNameButton.addEventListener('click', function() {
            submitLoanName(el.loanNameInput.value.trim());
        });
    }

    if (el.cancelLoanNameButton) {
        el.cancelLoanNameButton.addEventListener('click', resetLoanNameUI);
    }

    if (el.loanNameInput) {
        el.loanNameInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && el.saveLoanNameButton) {
                el.saveLoanNameButton.click();
            } else if (e.key === 'Escape' && el.cancelLoanNameButton) {
                el.cancelLoanNameButton.click();
            }
        });
    }

    if (el.editLoanNameButtonMobile) {
        el.editLoanNameButtonMobile.addEventListener('click', async function() {
            const newName = prompt('Enter new loan name:', originalLoanName);
            if (newName === null) return;
            await submitLoanName(newName.trim());
        });
    }

    function validateRange(input, min, max) {
        if (!input) return;
        input.addEventListener('input', function(e) {
            const value = parseFloat(e.target.value);
            if (Number.isNaN(value)) return;
            if (value < min) e.target.value = min;
            if (value > max) e.target.value = max;
        });
    }

    function setRateUI(editing) {
        [desktopRate, mobileRate].forEach((input) => {
            if (input) input.disabled = !editing;
        });
        if (el.editInterestRateButton) el.editInterestRateButton.style.display = editing ? 'none' : 'inline-block';
        if (el.applyInterestRateButton) el.applyInterestRateButton.style.display = editing ? 'inline-block' : 'none';
        if (el.editInterestRateButtonMobile) el.editInterestRateButtonMobile.style.display = editing ? 'none' : 'inline-block';
        if (el.applyInterestRateButtonMobile) el.applyInterestRateButtonMobile.style.display = editing ? 'inline-block' : 'none';
    }

    function setDayUI(editing) {
        [desktopDay, mobileDay].forEach((input) => {
            if (input) input.disabled = !editing;
        });
        if (el.editInterestDayButton) el.editInterestDayButton.style.display = editing ? 'none' : 'inline-block';
        if (el.applyInterestDayButton) el.applyInterestDayButton.style.display = editing ? 'inline-block' : 'none';
        if (el.editInterestDayButtonMobile) el.editInterestDayButtonMobile.style.display = editing ? 'none' : 'inline-block';
        if (el.applyInterestDayButtonMobile) el.applyInterestDayButtonMobile.style.display = editing ? 'inline-block' : 'none';
    }

    function syncRateValue(value) {
        if (desktopRate) desktopRate.value = value;
        if (mobileRate) mobileRate.value = value;
    }

    function syncDayValue(value) {
        if (desktopDay) desktopDay.value = value;
        if (mobileDay) mobileDay.value = value;
    }

    validateRange(desktopRate, 0, 100);
    validateRange(mobileRate, 0, 100);
    validateRange(desktopDay, 1, 31);
    validateRange(mobileDay, 1, 31);

    async function updateInterestRate() {
        const source = desktopRate || mobileRate;
        if (!source) return;
        const newValue = parseFloat(source.value);
        if (newValue === parseFloat(originalInterestRate)) {
            setRateUI(false);
            return;
        }

        try {
            const response = await fetch(`/api/loan/${loanId}/update_interest_rate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ interest_rate: newValue })
            });
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to update interest rate');
            }
            const data = await response.json();
            originalInterestRate = data.interest_rate.toFixed(2);
            updateUI(data);
            setRateUI(false);
        } catch (error) {
            console.error('Error:', error);
            alert(error.message || 'Failed to update interest rate');
            syncRateValue(originalInterestRate);
            setRateUI(false);
        }
    }

    async function updateInterestDay() {
        const source = desktopDay || mobileDay;
        if (!source) return;
        const newValue = parseInt(source.value, 10);
        if (newValue === parseInt(originalInterestDay, 10)) {
            setDayUI(false);
            return;
        }

        try {
            const response = await fetch(`/api/loan/${loanId}/update_interest_day`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ interest_day: newValue })
            });
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to update interest day');
            }
            const data = await response.json();
            originalInterestDay = String(data.interest_day);
            updateUI(data);
            setDayUI(false);
        } catch (error) {
            console.error('Error:', error);
            alert(error.message || 'Failed to update interest day');
            syncDayValue(originalInterestDay);
            setDayUI(false);
        }
    }

    [el.editInterestRateButton, el.editInterestRateButtonMobile].forEach((button) => {
        if (!button) return;
        button.addEventListener('click', function() {
            originalInterestRate = (desktopRate || mobileRate)?.value || originalInterestRate;
            setRateUI(true);
            if (desktopRate) desktopRate.focus();
            if (!desktopRate && mobileRate) mobileRate.focus();
        });
    });

    [el.applyInterestRateButton, el.applyInterestRateButtonMobile].forEach((button) => {
        if (!button) return;
        button.addEventListener('click', updateInterestRate);
    });

    [desktopRate, mobileRate].forEach((input) => {
        if (!input) return;
        input.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                syncRateValue(originalInterestRate);
                setRateUI(false);
            } else if (e.key === 'Enter') {
                updateInterestRate();
            }
        });
    });

    [el.editInterestDayButton, el.editInterestDayButtonMobile].forEach((button) => {
        if (!button) return;
        button.addEventListener('click', function() {
            originalInterestDay = (desktopDay || mobileDay)?.value || originalInterestDay;
            setDayUI(true);
            if (desktopDay) desktopDay.focus();
            if (!desktopDay && mobileDay) mobileDay.focus();
        });
    });

    [el.applyInterestDayButton, el.applyInterestDayButtonMobile].forEach((button) => {
        if (!button) return;
        button.addEventListener('click', updateInterestDay);
    });

    [desktopDay, mobileDay].forEach((input) => {
        if (!input) return;
        input.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                syncDayValue(originalInterestDay);
                setDayUI(false);
            } else if (e.key === 'Enter') {
                updateInterestDay();
            }
        });
    });

    async function submitTransaction(amountId, typeId, descriptionId, form) {
        const amount = document.getElementById(amountId)?.value;
        const type = document.getElementById(typeId)?.value;
        const description = document.getElementById(descriptionId)?.value;
        try {
            const response = await fetch(`/api/loan/${loanId}/transaction`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ amount, type, description })
            });
            const data = await response.json();
            updateUI(data);
            form.reset();
        } catch (error) {
            console.error('Error:', error);
            alert('Failed to add transaction');
        }
    }

    if (el.transactionForm) {
        el.transactionForm.addEventListener('submit', function(e) {
            e.preventDefault();
            submitTransaction('amount', 'type', 'description', el.transactionForm);
        });
    }

    if (el.transactionFormMobile) {
        el.transactionFormMobile.addEventListener('submit', function(e) {
            e.preventDefault();
            submitTransaction('amount-mobile', 'type-mobile', 'description-mobile', el.transactionFormMobile);
        });
    }

    async function applyMonthlyInterest() {
        try {
            const response = await fetch(`/api/loan/${loanId}/apply_interest`, { method: 'POST' });
            const data = await response.json();
            updateUI(data);
        } catch (error) {
            console.error('Error:', error);
            alert('Failed to apply interest');
        }
    }

    [el.applyInterestButton, el.applyInterestButtonMobile].forEach((button) => {
        if (button) button.addEventListener('click', applyMonthlyInterest);
    });

    async function deleteOrRecoverLoan(button) {
        const isRecovering = button.textContent.trim() === 'Recover Loan';
        const action = isRecovering ? 'recover' : 'delete';
        const confirmationMessage = isRecovering
            ? 'Are you sure you want to recover this loan?'
            : 'Are you sure you want to delete this loan?';
        if (!confirm(confirmationMessage)) return;
        try {
            const response = await fetch(`/api/loan/${loanId}/${action}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            if (!response.ok) throw new Error(`Failed to ${action} loan`);
            window.location.href = '/';
        } catch (error) {
            console.error(`Error ${action}ing loan:`, error);
            alert(`Failed to ${action} loan. Please try again.`);
        }
    }

    [el.deleteLoanButton, el.deleteLoanButtonMobile].forEach((button) => {
        if (button) button.addEventListener('click', function() { deleteOrRecoverLoan(button); });
    });

    async function submitRecurringPayment(amountId, scheduleId, startDateId, form) {
        const amount = document.getElementById(amountId)?.value;
        const schedule = document.getElementById(scheduleId)?.value;
        const startDate = document.getElementById(startDateId)?.value;
        try {
            const response = await fetch(`/api/loan/${loanId}/recurring_payment`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ amount, schedule, start_date: startDate })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Failed to add recurring payment');
            form.reset();
            alert(data.message);
            window.location.reload();
        } catch (error) {
            console.error('Error:', error);
            alert(error.message || 'Failed to add recurring payment');
        }
    }

    if (el.recurringPaymentForm) {
        el.recurringPaymentForm.addEventListener('submit', function(e) {
            e.preventDefault();
            submitRecurringPayment('recurring-amount', 'recurring-schedule', 'recurring-start-date', el.recurringPaymentForm);
        });
    }

    if (el.recurringPaymentFormMobile) {
        el.recurringPaymentFormMobile.addEventListener('submit', function(e) {
            e.preventDefault();
            submitRecurringPayment('recurring-amount-mobile', 'recurring-schedule-mobile', 'recurring-start-date-mobile', el.recurringPaymentFormMobile);
        });
    }

    async function deleteRecurringPayment(paymentIndex) {
        if (!confirm('Are you sure you want to delete this recurring payment?')) return;
        try {
            const response = await fetch(`/api/loan/${loanId}/recurring_payment/${paymentIndex}`, { method: 'DELETE' });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Failed to delete recurring payment');
            alert(data.message);
            window.location.reload();
        } catch (error) {
            console.error('Error:', error);
            alert(error.message || 'Failed to delete recurring payment');
        }
    }

    document.querySelectorAll('.delete-recurring-btn, .delete-recurring-btn-mobile').forEach((button) => {
        button.addEventListener('click', function() {
            deleteRecurringPayment(this.dataset.id);
        });
    });

    const statementFile = document.getElementById('statement-file');
    const statementPreviewBtn = document.getElementById('statement-preview-btn');
    const statementPreviewStatus = document.getElementById('statement-preview-status');
    const statementImportResults = document.getElementById('statement-import-results');
    const statementTruncatedHint = document.getElementById('statement-truncated-hint');
    const statementImportTbody = document.getElementById('statement-import-tbody');
    const statementImportBtn = document.getElementById('statement-import-btn');
    const statementClearBtn = document.getElementById('statement-clear-btn');

    function showStatementStatus(msg, isError) {
        if (!statementPreviewStatus) return;
        if (!msg) {
            statementPreviewStatus.style.display = 'none';
            return;
        }
        statementPreviewStatus.style.display = 'block';
        statementPreviewStatus.textContent = msg;
        statementPreviewStatus.className = 'text-sm mb-2 ' + (isError ? 'text-red-600' : 'text-gray-600');
    }

    function clearStatementRows() {
        if (statementImportTbody) statementImportTbody.innerHTML = '';
        if (statementImportResults) statementImportResults.style.display = 'none';
        renderBaselineDiff(null);
    }

    function renderBaselineDiff(diff) {
        const panel = document.getElementById('baseline-diff-panel');
        const elM = document.getElementById('baseline-diff-missing');
        const elN = document.getElementById('baseline-diff-new');
        const elC = document.getElementById('baseline-diff-changed');
        if (!panel || !elM || !elN || !elC) return;
        if (!diff || (!diff.missing?.length && !diff.new?.length && !diff.amount_changed?.length)) {
            panel.style.display = 'none';
            elM.textContent = '';
            elN.textContent = '';
            elC.textContent = '';
            return;
        }
        panel.style.display = 'block';
        elM.textContent = diff.missing && diff.missing.length
            ? 'Missing vs baseline: ' + diff.missing.map((b) => `${b.description} (bank £${b.amount_bank})`).join('; ')
            : '';
        elN.textContent = diff.new && diff.new.length
            ? 'Possible new bills: ' + diff.new.map((c) => `${c.description} (bank £${c.amount_bank})`).join('; ')
            : '';
        elC.textContent = diff.amount_changed && diff.amount_changed.length
            ? 'Amount changed: ' + diff.amount_changed.map((x) =>
                `${x.baseline.description}: expected bank £${x.expected_amount_bank}, actual £${x.actual_amount_bank}`
            ).join('; ')
            : '';
    }

    function renderStatementCandidates(candidates) {
        if (!statementImportTbody || !statementImportResults) return;
        statementImportTbody.innerHTML = '';
        candidates.forEach((c) => {
            const tr = document.createElement('tr');
            if (c.possible_duplicate) tr.classList.add('bg-amber-50');
            tr.dataset.date = c.statement_date;
            tr.dataset.description = c.description;

            const tdCheck = document.createElement('td');
            tdCheck.className = 'px-4 py-2';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.className = 'statement-row-check';
            cb.checked = true;
            tdCheck.appendChild(cb);

            const tdDate = document.createElement('td');
            tdDate.className = 'px-4 py-2 whitespace-nowrap';
            tdDate.textContent = c.statement_date;

            const tdDesc = document.createElement('td');
            tdDesc.className = 'px-4 py-2';
            const wrap = document.createElement('div');
            wrap.textContent = c.description;
            tdDesc.appendChild(wrap);
            if (c.possible_duplicate) {
                const badge = document.createElement('span');
                badge.className = 'ml-2 text-xs text-amber-800 font-semibold';
                badge.textContent = 'Matches existing transaction';
                tdDesc.appendChild(badge);
            }

            const tdBank = document.createElement('td');
            tdBank.className = 'px-4 py-2 whitespace-nowrap';
            const bankVal = typeof c.amount_bank === 'number' ? c.amount_bank : parseFloat(c.amount_bank);
            tdBank.textContent = Number.isFinite(bankVal) ? bankVal.toFixed(2) : String(c.amount_bank);

            const tdShare = document.createElement('td');
            tdShare.className = 'px-4 py-2';
            const inp = document.createElement('input');
            inp.type = 'number';
            inp.step = '0.01';
            inp.min = '0.01';
            inp.className = 'statement-row-amount border border-gray-300 rounded px-2 py-1 w-32';
            const defShare = c.amount_default != null ? c.amount_default : (Number.isFinite(bankVal) ? bankVal / 2 : 0);
            inp.value = Number(defShare).toFixed(2);
            tdShare.appendChild(inp);

            tr.appendChild(tdCheck);
            tr.appendChild(tdDate);
            tr.appendChild(tdDesc);
            tr.appendChild(tdBank);
            tr.appendChild(tdShare);
            statementImportTbody.appendChild(tr);
        });
        statementImportResults.style.display = 'block';
    }

    if (statementPreviewBtn && statementFile) {
        statementPreviewBtn.addEventListener('click', async function() {
            const file = statementFile.files && statementFile.files[0];
            if (!file) {
                alert('Choose a file first.');
                return;
            }
            showStatementStatus('Loading…', false);
            statementPreviewBtn.disabled = true;
            clearStatementRows();
            if (statementTruncatedHint) statementTruncatedHint.style.display = 'none';
            try {
                const fd = new FormData();
                fd.append('file', file);
                const response = await fetch(`/api/loan/${loanId}/statement/preview`, {
                    method: 'POST',
                    body: fd
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(data.error || `Preview failed (${response.status})`);
                }
                if (statementTruncatedHint) {
                    statementTruncatedHint.style.display = data.truncated ? 'block' : 'none';
                }
                if (!data.candidates || !data.candidates.length) {
                    showStatementStatus('No matching household bill debits found. Try another export or add transactions manually.', false);
                    renderBaselineDiff(data.baseline_diff || null);
                    return;
                }
                showStatementStatus(`Found ${data.candidates.length} candidate(s). Review and import.`, false);
                renderStatementCandidates(data.candidates);
                renderBaselineDiff(data.baseline_diff || null);
            } catch (e) {
                console.error(e);
                showStatementStatus(e.message || 'Preview failed', true);
            } finally {
                statementPreviewBtn.disabled = false;
            }
        });
    }

    if (statementImportBtn) {
        statementImportBtn.addEventListener('click', async function() {
            const tbody = document.getElementById('statement-import-tbody');
            if (!tbody) return;
            const transactions = [];
            tbody.querySelectorAll('tr').forEach((tr) => {
                const cb = tr.querySelector('.statement-row-check');
                if (!cb || !cb.checked) return;
                const inp = tr.querySelector('.statement-row-amount');
                const amt = parseFloat(inp && inp.value);
                if (Number.isNaN(amt) || amt <= 0) return;
                transactions.push({
                    date: tr.dataset.date,
                    amount: amt,
                    description: tr.dataset.description || 'Imported repayment'
                });
            });
            if (!transactions.length) {
                alert('Select at least one row with a positive amount.');
                return;
            }
            statementImportBtn.disabled = true;
            try {
                const response = await fetch(`/api/loan/${loanId}/statement/import`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ transactions })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.error || 'Import failed');
                updateUI(data);
                clearStatementRows();
                if (statementTruncatedHint) statementTruncatedHint.style.display = 'none';
                showStatementStatus(`Imported ${transactions.length} repayment(s).`, false);
            } catch (e) {
                console.error(e);
                alert(e.message || 'Import failed');
            } finally {
                statementImportBtn.disabled = false;
            }
        });
    }

    if (statementClearBtn) {
        statementClearBtn.addEventListener('click', function() {
            clearStatementRows();
            if (statementTruncatedHint) statementTruncatedHint.style.display = 'none';
            showStatementStatus('', false);
        });
    }

    const baselineFile = document.getElementById('baseline-file');
    const baselinePreviewBtn = document.getElementById('baseline-preview-btn');
    const baselinePreviewStatus = document.getElementById('baseline-preview-status');
    const baselineSetupResults = document.getElementById('baseline-setup-results');
    const baselineCandidateTbody = document.getElementById('baseline-candidate-tbody');
    const baselineReminderDay = document.getElementById('baseline-reminder-day');
    const baselineReminderEmail = document.getElementById('baseline-reminder-email');
    const baselineSaveBtn = document.getElementById('baseline-save-btn');
    const baselineClearBtn = document.getElementById('baseline-clear-btn');
    const baselinePeriodHint = document.getElementById('baseline-period-hint');
    const baselineSavedTbody = document.getElementById('baseline-saved-tbody');
    const baselineSavedSaveBtn = document.getElementById('baseline-saved-save-btn');
    const expectedBillsSection = document.getElementById('expected-bills-section');
    const baselineMergeModal = document.getElementById('baseline-merge-modal');
    const baselineModalReplaceBtn = document.getElementById('baseline-modal-replace-btn');
    const baselineModalMergeBtn = document.getElementById('baseline-modal-merge-btn');
    const baselineModalCancelBtn = document.getElementById('baseline-modal-cancel-btn');
    const baselineMergeModalBackdrop = document.getElementById('baseline-merge-modal-backdrop');

    let baselinePreviewState = { periodEnd: null, candidates: [] };
    let pendingBaselineFile = null;

    function getSavedBaselineSnapshot() {
        if (!baselineSavedTbody) return '[]';
        const rows = [];
        baselineSavedTbody.querySelectorAll('tr').forEach((tr) => {
            const id = tr.dataset.itemId || '';
            const descInp = tr.querySelector('.baseline-saved-desc');
            const bankInp = tr.querySelector('.baseline-saved-bank');
            const shareInp = tr.querySelector('.baseline-saved-share');
            const catInp = tr.querySelector('.baseline-saved-cat');
            const noteInp = tr.querySelector('.baseline-saved-note');
            const ab = parseFloat(bankInp && bankInp.value);
            const sh = parseFloat(shareInp && shareInp.value);
            rows.push({
                id,
                description: (descInp && descInp.value.trim()) || '',
                amount_bank: Number.isFinite(ab) ? Math.round(ab * 100) / 100 : null,
                amount_share: Number.isFinite(sh) ? Math.round(sh * 100) / 100 : null,
                category: (catInp && catInp.value.trim()) || '',
                note: (noteInp && noteInp.value.trim()) || ''
            });
        });
        rows.sort((a, b) => String(a.id).localeCompare(String(b.id)));
        return JSON.stringify(rows);
    }

    let baselineSavedInitialSnapshot = baselineSavedTbody ? getSavedBaselineSnapshot() : '';

    function getReminderEmailSnapshot() {
        return baselineReminderEmail ? baselineReminderEmail.value.trim() : '';
    }

    let baselineReminderEmailInitial = getReminderEmailSnapshot();

    function updateBaselineEmailDisplayText() {
        const el = document.getElementById('baseline-email-display');
        if (!el || !expectedBillsSection) return;
        const v = getReminderEmailSnapshot();
        const def = (expectedBillsSection.dataset.defaultNotifyEmail || '').trim();
        el.textContent = v || def || 'not set';
    }

    function updateBaselineDirtyState() {
        const baselineCount = expectedBillsSection
            ? parseInt(expectedBillsSection.dataset.baselineCount, 10) || 0
            : 0;
        const tableDirty = baselineSavedTbody
            ? getSavedBaselineSnapshot() !== baselineSavedInitialSnapshot
            : false;
        const emailDirty = getReminderEmailSnapshot() !== baselineReminderEmailInitial;

        if (baselineSavedSaveBtn && baselineSavedTbody) {
            baselineSavedSaveBtn.classList.toggle('hidden', !(tableDirty || emailDirty));
        }

        const emailSaveBtn = document.getElementById('baseline-reminder-email-save-btn');
        if (emailSaveBtn) {
            if (baselineCount > 0) {
                emailSaveBtn.classList.add('hidden');
            } else {
                emailSaveBtn.classList.toggle('hidden', !emailDirty);
            }
        }
    }

    function closeBaselineMergeModal() {
        pendingBaselineFile = null;
        if (baselineMergeModal) baselineMergeModal.classList.add('hidden');
    }

    function openBaselineMergeModal(file) {
        pendingBaselineFile = file;
        if (baselineMergeModal) baselineMergeModal.classList.remove('hidden');
    }

    async function runBaselinePreviewUpload(file, useMerge) {
        showBaselineStatus('Loading…', false);
        if (baselinePreviewBtn) baselinePreviewBtn.disabled = true;
        clearBaselinePreview();
        try {
            const fd = new FormData();
            fd.append('file', file);
            const response = await fetch(`/api/loan/${loanId}/statement/baseline-preview`, {
                method: 'POST',
                body: fd
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `Preview failed (${response.status})`);
            }
            if (!data.candidates || !data.candidates.length) {
                showBaselineStatus('No candidate bill lines found. Try another export.', false);
                return;
            }
            let rowsForTable = data.candidates;
            if (useMerge) {
                const mergeRes = await fetch(`/api/loan/${loanId}/bill-baseline/merge-candidates`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ candidates: data.candidates })
                });
                const mergeData = await mergeRes.json().catch(() => ({}));
                if (!mergeRes.ok) {
                    throw new Error(mergeData.error || `Merge failed (${mergeRes.status})`);
                }
                rowsForTable = mergeData.items || [];
            }
            baselinePreviewState = {
                periodEnd: data.inferred_period_end || null,
                candidates: rowsForTable
            };
            if (baselineReminderDay) {
                baselineReminderDay.value = String(data.inferred_reminder_day != null ? data.inferred_reminder_day : 1);
            }
            if (baselinePeriodHint) {
                if (data.inferred_period_end) {
                    baselinePeriodHint.textContent = `Inferred statement period end: ${data.inferred_period_end}. Reminder day defaults to the next calendar day.`;
                    baselinePeriodHint.style.display = 'block';
                } else {
                    baselinePeriodHint.style.display = 'none';
                }
            }
            showBaselineStatus(`Found ${rowsForTable.length} line(s). Uncheck false positives, then save.`, false);
            renderBaselineCandidates(rowsForTable);
        } catch (e) {
            console.error(e);
            showBaselineStatus(e.message || 'Preview failed', true);
        } finally {
            if (baselinePreviewBtn) baselinePreviewBtn.disabled = false;
        }
    }

    function showBaselineStatus(msg, isError) {
        if (!baselinePreviewStatus) return;
        if (!msg) {
            baselinePreviewStatus.style.display = 'none';
            return;
        }
        baselinePreviewStatus.style.display = 'block';
        baselinePreviewStatus.textContent = msg;
        baselinePreviewStatus.className = 'text-sm mb-2 ' + (isError ? 'text-red-600' : 'text-gray-600');
    }

    function renderBaselineCandidates(candidates) {
        if (!baselineCandidateTbody || !baselineSetupResults) return;
        baselineCandidateTbody.innerHTML = '';
        candidates.forEach((c) => {
            const tr = document.createElement('tr');
            const dateStr = c.statement_date || '';
            tr.dataset.date = dateStr;
            tr.dataset.description = c.description || '';
            tr.dataset.amountBank = String(c.amount_bank != null ? c.amount_bank : '');
            tr.dataset.category = c.category != null && c.category !== '' ? String(c.category) : '';
            if (c.id != null && c.id !== '') {
                tr.dataset.rowId = String(c.id);
            }

            const tdCheck = document.createElement('td');
            tdCheck.className = 'px-4 py-2';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.className = 'baseline-row-check';
            cb.checked = true;
            tdCheck.appendChild(cb);

            const tdDate = document.createElement('td');
            tdDate.className = 'px-4 py-2 whitespace-nowrap';
            tdDate.textContent = dateStr || '—';

            const tdDesc = document.createElement('td');
            tdDesc.className = 'px-4 py-2';
            tdDesc.textContent = c.description || '';

            const tdCat = document.createElement('td');
            tdCat.className = 'px-4 py-2 text-sm text-gray-700 max-w-[140px] break-words';
            tdCat.textContent = c.category != null && String(c.category).trim() ? String(c.category) : '—';

            const tdBank = document.createElement('td');
            tdBank.className = 'px-4 py-2 whitespace-nowrap';
            const bankVal = typeof c.amount_bank === 'number' ? c.amount_bank : parseFloat(c.amount_bank);
            tdBank.textContent = Number.isFinite(bankVal) ? bankVal.toFixed(2) : String(c.amount_bank);

            const tdShare = document.createElement('td');
            tdShare.className = 'px-4 py-2';
            const inp = document.createElement('input');
            inp.type = 'number';
            inp.step = '0.01';
            inp.min = '0.01';
            inp.className = 'baseline-row-amount border border-gray-300 rounded px-2 py-1 w-32';
            let defShare = c.amount_share;
            if (defShare == null && c.amount_default != null) defShare = c.amount_default;
            if (defShare == null && Number.isFinite(bankVal)) defShare = bankVal / 2;
            if (defShare == null) defShare = 0;
            inp.value = Number(defShare).toFixed(2);
            tdShare.appendChild(inp);

            const tdNote = document.createElement('td');
            tdNote.className = 'px-4 py-2';
            const noteInp = document.createElement('input');
            noteInp.type = 'text';
            noteInp.maxLength = 300;
            noteInp.className = 'baseline-row-note border border-gray-300 rounded px-2 py-1 w-full max-w-xs';
            noteInp.value = c.note != null ? String(c.note) : '';
            noteInp.placeholder = 'Optional';
            tdNote.appendChild(noteInp);

            tr.appendChild(tdCheck);
            tr.appendChild(tdDate);
            tr.appendChild(tdDesc);
            tr.appendChild(tdCat);
            tr.appendChild(tdBank);
            tr.appendChild(tdShare);
            tr.appendChild(tdNote);
            baselineCandidateTbody.appendChild(tr);
        });
        baselineSetupResults.style.display = 'block';
    }

    function clearBaselinePreview() {
        if (baselineCandidateTbody) baselineCandidateTbody.innerHTML = '';
        if (baselineSetupResults) baselineSetupResults.style.display = 'none';
        if (baselinePeriodHint) baselinePeriodHint.style.display = 'none';
        baselinePreviewState = { periodEnd: null, candidates: [] };
    }

    if (baselinePreviewBtn && baselineFile) {
        baselinePreviewBtn.addEventListener('click', async function() {
            const file = baselineFile.files && baselineFile.files[0];
            if (!file) {
                alert('Choose a file first.');
                return;
            }
            const baselineCount = expectedBillsSection
                ? parseInt(expectedBillsSection.dataset.baselineCount, 10) || 0
                : 0;
            if (baselineCount > 0) {
                openBaselineMergeModal(file);
                return;
            }
            await runBaselinePreviewUpload(file, false);
        });
    }

    if (baselineModalReplaceBtn) {
        baselineModalReplaceBtn.addEventListener('click', async function() {
            const f = pendingBaselineFile;
            closeBaselineMergeModal();
            if (f) await runBaselinePreviewUpload(f, false);
        });
    }
    if (baselineModalMergeBtn) {
        baselineModalMergeBtn.addEventListener('click', async function() {
            const f = pendingBaselineFile;
            closeBaselineMergeModal();
            if (f) await runBaselinePreviewUpload(f, true);
        });
    }
    if (baselineModalCancelBtn) {
        baselineModalCancelBtn.addEventListener('click', closeBaselineMergeModal);
    }
    if (baselineMergeModalBackdrop) {
        baselineMergeModalBackdrop.addEventListener('click', closeBaselineMergeModal);
    }
    document.addEventListener('keydown', function(ev) {
        if (ev.key !== 'Escape' || !baselineMergeModal || baselineMergeModal.classList.contains('hidden')) return;
        closeBaselineMergeModal();
    });

    if (baselineSaveBtn && baselineCandidateTbody) {
        baselineSaveBtn.addEventListener('click', async function() {
            const items = [];
            baselineCandidateTbody.querySelectorAll('tr').forEach((tr) => {
                const cb = tr.querySelector('.baseline-row-check');
                if (!cb || !cb.checked) return;
                const inp = tr.querySelector('.baseline-row-amount');
                const noteInp = tr.querySelector('.baseline-row-note');
                const amt = parseFloat(inp && inp.value);
                if (Number.isNaN(amt) || amt <= 0) return;
                const ab = parseFloat(tr.dataset.amountBank || '0');
                if (Number.isNaN(ab) || ab <= 0) return;
                const cat = (tr.dataset.category || '').trim();
                const row = {
                    description: tr.dataset.description || 'Bill',
                    amount_bank: ab,
                    amount_share: amt,
                    category: cat || null,
                    note: (noteInp && noteInp.value) ? noteInp.value.trim() : ''
                };
                if (tr.dataset.rowId) row.id = tr.dataset.rowId;
                items.push(row);
            });
            if (!items.length) {
                alert('Select at least one row with a positive share amount.');
                return;
            }
            const day = parseInt(baselineReminderDay && baselineReminderDay.value, 10);
            if (Number.isNaN(day) || day < 1 || day > 31) {
                alert('Reminder day must be 1–31.');
                return;
            }
            baselineSaveBtn.disabled = true;
            try {
                const response = await fetch(`/api/loan/${loanId}/statement/baseline-save`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        items,
                        day_of_month: day,
                        reminder_email: baselineReminderEmail ? baselineReminderEmail.value.trim() : '',
                        inferred_period_end: baselinePreviewState.periodEnd
                    })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.error || 'Save failed');
                window.location.reload();
            } catch (e) {
                console.error(e);
                alert(e.message || 'Save failed');
            } finally {
                baselineSaveBtn.disabled = false;
            }
        });
    }

    if (baselineClearBtn) {
        baselineClearBtn.addEventListener('click', function() {
            clearBaselinePreview();
            showBaselineStatus('', false);
        });
    }

    if (baselineSavedTbody) {
        baselineSavedTbody.addEventListener('input', updateBaselineDirtyState);
        baselineSavedTbody.addEventListener('change', updateBaselineDirtyState);
    }
    if (baselineReminderEmail) {
        baselineReminderEmail.addEventListener('input', updateBaselineDirtyState);
        baselineReminderEmail.addEventListener('change', updateBaselineDirtyState);
    }
    updateBaselineDirtyState();

    if (baselineSavedSaveBtn && baselineSavedTbody) {
        baselineSavedSaveBtn.addEventListener('click', async function() {
            const items = [];
            baselineSavedTbody.querySelectorAll('tr').forEach((tr) => {
                const id = tr.dataset.itemId;
                const descInp = tr.querySelector('.baseline-saved-desc');
                const bankInp = tr.querySelector('.baseline-saved-bank');
                const shareInp = tr.querySelector('.baseline-saved-share');
                const catInp = tr.querySelector('.baseline-saved-cat');
                const noteInp = tr.querySelector('.baseline-saved-note');
                const ab = parseFloat(bankInp && bankInp.value);
                const sh = parseFloat(shareInp && shareInp.value);
                if (!id || Number.isNaN(ab) || ab <= 0 || Number.isNaN(sh) || sh <= 0) return;
                const cat = (catInp && catInp.value.trim()) || null;
                items.push({
                    id,
                    description: (descInp && descInp.value.trim()) || 'Bill',
                    amount_bank: ab,
                    amount_share: sh,
                    category: cat,
                    note: (noteInp && noteInp.value) ? noteInp.value.trim() : ''
                });
            });
            if (!items.length) {
                alert('Add at least one valid row, or delete the section from the last remaining row via Delete.');
                return;
            }
            baselineSavedSaveBtn.disabled = true;
            try {
                const response = await fetch(`/api/loan/${loanId}/bill-baseline`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        items,
                        reminder_email: baselineReminderEmail ? baselineReminderEmail.value.trim() : ''
                    })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.error || 'Save failed');
                window.location.reload();
            } catch (e) {
                console.error(e);
                alert(e.message || 'Save failed');
            } finally {
                baselineSavedSaveBtn.disabled = false;
            }
        });
    }

    const baselineReminderEmailSaveBtn = document.getElementById('baseline-reminder-email-save-btn');
    if (baselineReminderEmailSaveBtn) {
        baselineReminderEmailSaveBtn.addEventListener('click', async function() {
            baselineReminderEmailSaveBtn.disabled = true;
            try {
                const response = await fetch(`/api/loan/${loanId}/bill-baseline`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        reminder_email: baselineReminderEmail ? baselineReminderEmail.value.trim() : ''
                    })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.error || 'Save failed');
                baselineReminderEmailInitial = getReminderEmailSnapshot();
                updateBaselineEmailDisplayText();
                updateBaselineDirtyState();
            } catch (e) {
                console.error(e);
                alert(e.message || 'Save failed');
            } finally {
                baselineReminderEmailSaveBtn.disabled = false;
            }
        });
    }

    const baselineSavedWrap = document.getElementById('baseline-saved-wrap');
    if (baselineSavedWrap) {
        baselineSavedWrap.addEventListener('click', async function(ev) {
            const btn = ev.target.closest && ev.target.closest('.baseline-saved-delete');
            if (!btn) return;
            const tr = btn.closest('tr');
            if (!tr || !tr.dataset.itemId) return;
            if (!confirm('Remove this expected bill from the baseline?')) return;
            try {
                const response = await fetch(`/api/loan/${loanId}/bill-baseline/item/${encodeURIComponent(tr.dataset.itemId)}`, {
                    method: 'DELETE'
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.error || 'Delete failed');
                window.location.reload();
            } catch (e) {
                console.error(e);
                alert(e.message || 'Delete failed');
            }
        });
    }

    const initialBalance = document.getElementById('current-balance') || document.getElementById('current-balance-mobile');
    const initialRate = document.getElementById('interest-rate-input') || document.getElementById('interest-rate-input-mobile');
    if (initialBalance && initialRate && initialBalance.textContent.trim() === '£0.00' && initialRate.value === '0.00') {
        const loanAmount = prompt('Please enter the initial loan amount:');
        const interestRate = prompt('Please enter the annual interest rate (%):');
        if (loanAmount && interestRate) {
            initializeLoan(parseFloat(loanAmount), parseFloat(interestRate));
        }
    }

    const auditSearch = document.getElementById('audit-search');
    const auditDateOp = document.getElementById('audit-date-op');
    const auditDateSingle = document.getElementById('audit-date-single');
    const auditDateStart = document.getElementById('audit-date-start');
    const auditDateEnd = document.getElementById('audit-date-end');
    if (auditSearch) {
        auditSearch.addEventListener('input', applyAuditLogFilters);
    }
    [auditDateOp, auditDateSingle, auditDateStart, auditDateEnd].forEach((el) => {
        if (el) el.addEventListener('change', applyAuditLogFilters);
    });
    if (auditDateOp) {
        auditDateOp.addEventListener('change', syncAuditDateFilterControls);
        syncAuditDateFilterControls();
    }
});

async function initializeLoan(amount, interestRate) {
    try {
        const response = await fetch('/api/loan', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                name: 'New Loan',
                loan_amount: amount,
                interest_rate: interestRate
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to initialize loan');
        }

        const data = await response.json();
        window.location.href = `/loan/${data.id}`;
    } catch (error) {
        console.error('Error:', error);
        alert(error.message || 'Failed to initialize loan');
    }
}

function updateUI(data) {
    const formattedAmount = `£${parseFloat(data.loan_amount).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    const rate = data.interest_rate.toFixed(2);
    const day = String(data.interest_day);

    const balanceDesktop = document.getElementById('current-balance');
    const balanceMobile = document.getElementById('current-balance-mobile');
    if (balanceDesktop) balanceDesktop.textContent = formattedAmount;
    if (balanceMobile) balanceMobile.textContent = formattedAmount;

    const rateDesktop = document.getElementById('interest-rate-input');
    const rateMobile = document.getElementById('interest-rate-input-mobile');
    if (rateDesktop) rateDesktop.value = rate;
    if (rateMobile) rateMobile.value = rate;

    const dayDesktop = document.getElementById('interest-day');
    const dayMobile = document.getElementById('interest-day-mobile');
    if (dayDesktop) dayDesktop.value = day;
    if (dayMobile) dayMobile.value = day;

    const desktopTbody = document.querySelector('#transactions-table tbody');
    const mobileTbody = document.querySelector('#transactions-table-mobile tbody');
    if (desktopTbody) desktopTbody.innerHTML = '';
    if (mobileTbody) mobileTbody.innerHTML = '';

    data.transactions.reverse().forEach((transaction) => {
        const prettyType = transaction.type.charAt(0).toUpperCase() + transaction.type.slice(1);
        const amountClass = transaction.type === 'repayment' ? 'text-red-600' : '';
        const amountText = `£${Math.abs(transaction.amount).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

        if (desktopTbody) {
            const desktopRow = document.createElement('tr');
            desktopRow.className = 'audit-log-row';
            desktopRow.setAttribute('data-row-date', transaction.date);
            desktopRow.innerHTML = `
                <td class="px-6 py-4 whitespace-nowrap">${transaction.date}</td>
                <td class="px-6 py-4 whitespace-nowrap">${prettyType}</td>
                <td class="px-6 py-4 whitespace-nowrap ${amountClass}">${amountText}</td>
                <td class="px-6 py-4 whitespace-nowrap">${transaction.description}</td>
                <td class="px-6 py-4 whitespace-nowrap">${transaction.user || ''}</td>
            `;
            desktopTbody.appendChild(desktopRow);
        }

        if (mobileTbody) {
            const mobileRow = document.createElement('tr');
            mobileRow.className = 'audit-log-row';
            mobileRow.setAttribute('data-row-date', transaction.date);
            mobileRow.innerHTML = `
                <td class="px-2 py-1">${transaction.date}</td>
                <td class="px-2 py-1">${prettyType}</td>
                <td class="px-2 py-1 ${amountClass}">${amountText}</td>
                <td class="px-2 py-1">${transaction.description}</td>
                <td class="px-2 py-1">${transaction.user || ''}</td>
            `;
            mobileTbody.appendChild(mobileRow);
        }
    });
    applyAuditLogFilters();
}

function toggleVersion() {
    const isMobile = window.innerWidth <= 640;
    const desktop = document.getElementById('desktop-version');
    const mobile = document.getElementById('mobile-version');
    if (desktop && mobile) {
        if (isMobile) {
            desktop.classList.add('hidden');
            mobile.classList.remove('hidden');
        } else {
            desktop.classList.remove('hidden');
            mobile.classList.add('hidden');
        }
    }
}
window.addEventListener('DOMContentLoaded', toggleVersion);
window.addEventListener('resize', toggleVersion);