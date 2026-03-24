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

    const initialBalance = document.getElementById('current-balance') || document.getElementById('current-balance-mobile');
    const initialRate = document.getElementById('interest-rate-input') || document.getElementById('interest-rate-input-mobile');
    if (initialBalance && initialRate && initialBalance.textContent.trim() === '£0.00' && initialRate.value === '0.00') {
        const loanAmount = prompt('Please enter the initial loan amount:');
        const interestRate = prompt('Please enter the annual interest rate (%):');
        if (loanAmount && interestRate) {
            initializeLoan(parseFloat(loanAmount), parseFloat(interestRate));
        }
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