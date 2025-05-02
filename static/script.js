document.addEventListener('DOMContentLoaded', function() {
    const transactionForm = document.getElementById('transaction-form');
    const applyInterestButton = document.getElementById('apply-interest');
    const interestDayInput = document.getElementById('interest-day');
    const editInterestDayButton = document.getElementById('edit-interest-day');
    const applyInterestDayButton = document.getElementById('apply-interest-day');
    const interestRateInput = document.getElementById('interest-rate-input');
    const editInterestRateButton = document.getElementById('edit-interest-rate');
    const applyInterestRateButton = document.getElementById('apply-interest-rate');
    
    // Loan name editing elements
    const loanNameDisplay = document.getElementById('loan-name-display');
    const editLoanNameButton = document.getElementById('edit-loan-name');
    const loanNameEdit = document.getElementById('loan-name-edit');
    const loanNameInput = document.getElementById('loan-name-input');
    const saveLoanNameButton = document.getElementById('save-loan-name');
    const cancelLoanNameButton = document.getElementById('cancel-loan-name');
    
    // Get the loan_id from the URL
    const loanId = window.location.pathname.split('/').pop();
    
    let originalInterestDay = interestDayInput.value;
    let originalInterestRate = interestRateInput.value;
    let originalLoanName = loanNameDisplay.textContent;

    // Handle loan name edit button
    if (editLoanNameButton) {
        editLoanNameButton.addEventListener('click', function() {
            loanNameDisplay.style.display = 'none';
            editLoanNameButton.style.display = 'none';
            loanNameEdit.style.display = 'flex';
            loanNameInput.value = originalLoanName;
            loanNameInput.focus();
            loanNameInput.select();
        });
    }

    // Handle save loan name button
    if (saveLoanNameButton) {
        saveLoanNameButton.addEventListener('click', async function() {
            const newName = loanNameInput.value.trim();
            
            if (newName === '') {
                alert('Loan name cannot be empty');
                return;
            }
            
            if (newName === originalLoanName) {
                // No change, just reset the UI
                resetLoanNameUI();
                return;
            }

            try {
                const response = await fetch(`/api/loan/${loanId}/update_name`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ name: newName })
                });

                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.error || 'Failed to update loan name');
                }

                const data = await response.json();
                originalLoanName = newName;
                loanNameDisplay.textContent = newName;
                resetLoanNameUI();
            } catch (error) {
                console.error('Error:', error);
                alert(error.message || 'Failed to update loan name');
                resetLoanNameUI();
            }
        });
    }

    // Handle cancel loan name button
    if (cancelLoanNameButton) {
        cancelLoanNameButton.addEventListener('click', function() {
            resetLoanNameUI();
        });
    }

    // Handle Enter key in loan name input
    if (loanNameInput) {
        loanNameInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                saveLoanNameButton.click();
            } else if (e.key === 'Escape') {
                cancelLoanNameButton.click();
            }
        });
    }

    function resetLoanNameUI() {
        loanNameDisplay.style.display = 'block';
        editLoanNameButton.style.display = 'inline-block';
        loanNameEdit.style.display = 'none';
    }

    // Initialize loan if empty
    if (document.getElementById('current-balance').textContent === '£0.00' &&
        document.getElementById('interest-rate-input').value === '0.00') {
        const loanAmount = prompt('Please enter the initial loan amount:');
        const interestRate = prompt('Please enter the annual interest rate (%):');
        
        if (loanAmount && interestRate) {
            initializeLoan(parseFloat(loanAmount), parseFloat(interestRate));
        }
    }

    // Handle interest rate edit button
    if (editInterestRateButton) {
        editInterestRateButton.addEventListener('click', function() {
            interestRateInput.disabled = false;
            editInterestRateButton.style.display = 'none';
            applyInterestRateButton.style.display = 'inline-block';
            originalInterestRate = interestRateInput.value;
            interestRateInput.focus();
        });
    }

    // Handle interest rate input validation
    if (interestRateInput) {
        interestRateInput.addEventListener('input', function(e) {
            const value = parseFloat(e.target.value);
            
            if (value < 0) {
                e.target.value = 0;
            } else if (value > 100) {
                e.target.value = 100;
            }
        });
    }

    // Handle interest rate apply button
    if (applyInterestRateButton) {
        applyInterestRateButton.addEventListener('click', async function() {
            const newValue = parseFloat(interestRateInput.value);
            
            if (newValue === parseFloat(originalInterestRate)) {
                // No change, just reset the UI
                resetInterestRateUI();
                return;
            }

            try {
                const response = await fetch(`/api/loan/${loanId}/update_interest_rate`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ interest_rate: newValue })
                });

                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.error || 'Failed to update interest rate');
                }

                const data = await response.json();
                updateUI(data);
                resetInterestRateUI();
            } catch (error) {
                console.error('Error:', error);
                alert(error.message || 'Failed to update interest rate');
                // Revert to original value on error
                interestRateInput.value = originalInterestRate;
                resetInterestRateUI();
            }
        });
    }

    // Handle ESC key to cancel interest rate edit
    if (interestRateInput) {
        interestRateInput.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                interestRateInput.value = originalInterestRate;
                resetInterestRateUI();
            } else if (e.key === 'Enter') {
                applyInterestRateButton.click();
            }
        });
    }

    function resetInterestRateUI() {
        interestRateInput.disabled = true;
        editInterestRateButton.style.display = 'inline-block';
        applyInterestRateButton.style.display = 'none';
    }

    // Handle interest day edit button
    if (editInterestDayButton) {
        editInterestDayButton.addEventListener('click', function() {
            interestDayInput.disabled = false;
            editInterestDayButton.style.display = 'none';
            applyInterestDayButton.style.display = 'inline-block';
            originalInterestDay = interestDayInput.value;
            interestDayInput.focus();
        });
    }

    // Handle interest day input validation
    if (interestDayInput) {
        interestDayInput.addEventListener('input', function(e) {
            const value = parseInt(e.target.value);
            
            // Validate input
            if (value < 1) {
                e.target.value = 1;
            } else if (value > 31) {
                e.target.value = 31;
            }
        });
    }

    // Handle interest day apply button
    if (applyInterestDayButton) {
        applyInterestDayButton.addEventListener('click', async function() {
            const newValue = parseInt(interestDayInput.value);
            
            if (newValue === parseInt(originalInterestDay)) {
                // No change, just reset the UI
                resetInterestDayUI();
                return;
            }

            try {
                const response = await fetch(`/api/loan/${loanId}/update_interest_day`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ interest_day: newValue })
                });

                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.error || 'Failed to update interest day');
                }

                const data = await response.json();
                updateUI(data);
                resetInterestDayUI();
            } catch (error) {
                console.error('Error:', error);
                alert(error.message || 'Failed to update interest day');
                // Revert to original value on error
                interestDayInput.value = originalInterestDay;
                resetInterestDayUI();
            }
        });
    }

    // Handle ESC key to cancel edit
    if (interestDayInput) {
        interestDayInput.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                interestDayInput.value = originalInterestDay;
                resetInterestDayUI();
            } else if (e.key === 'Enter') {
                applyInterestDayButton.click();
            }
        });
    }

    function resetInterestDayUI() {
        interestDayInput.disabled = true;
        editInterestDayButton.style.display = 'inline-block';
        applyInterestDayButton.style.display = 'none';
    }

    // Handle transaction form submission
    transactionForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const amount = document.getElementById('amount').value;
        const type = document.getElementById('type').value;
        const description = document.getElementById('description').value;

        try {
            const response = await fetch(`/api/loan/${loanId}/transaction`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ amount, type, description })
            });

            const data = await response.json();
            updateUI(data);
            transactionForm.reset();
        } catch (error) {
            console.error('Error:', error);
            alert('Failed to add transaction');
        }
    });

    // Handle applying monthly interest
    applyInterestButton.addEventListener('click', async function() {
        try {
            const response = await fetch(`/api/loan/${loanId}/apply_interest`, {
                method: 'POST'
            });

            const data = await response.json();
            updateUI(data);
        } catch (error) {
            console.error('Error:', error);
            alert('Failed to apply interest');
        }
    });

    // Delete/Recover loan functionality
    document.getElementById('deleteLoanBtn').addEventListener('click', async function() {
        const isRecovering = this.textContent.trim() === 'Recover Loan';
        const action = isRecovering ? 'recover' : 'delete';
        const confirmationMessage = isRecovering 
            ? 'Are you sure you want to recover this loan?'
            : 'Are you sure you want to delete this loan?';

        if (confirm(confirmationMessage)) {
            try {
                const response = await fetch(`/api/loan/${loanId}/${action}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                });

                if (!response.ok) {
                    throw new Error(`Failed to ${action} loan`);
                }

                // Redirect to the loans list page
                window.location.href = '/';
            } catch (error) {
                console.error(`Error ${action}ing loan:`, error);
                alert(`Failed to ${action} loan. Please try again.`);
            }
        }
    });

    // Handle recurring payment form submission
    const recurringPaymentForm = document.getElementById('recurring-payment-form');
    if (recurringPaymentForm) {
        recurringPaymentForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const amount = document.getElementById('recurring-amount').value;
            const schedule = document.getElementById('recurring-schedule').value;
            const startDate = document.getElementById('recurring-start-date').value;

            try {
                const response = await fetch(`/api/loan/${loanId}/recurring_payment`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ amount, schedule, start_date: startDate })
                });

                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.error || 'Failed to add recurring payment');
                }

                // Add new payment to the table
                const tbody = document.querySelector('#recurring-payments-table tbody');
                
                // Clear the "No recurring payments" message if it exists
                if (tbody.children.length === 1 && tbody.children[0].textContent.includes('No recurring payments')) {
                    tbody.innerHTML = '';
                }
                
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td class="px-6 py-4 whitespace-nowrap">£${parseFloat(data.payment.amount).toFixed(2)}</td>
                    <td class="px-6 py-4 whitespace-nowrap">${data.payment.schedule.charAt(0).toUpperCase() + data.payment.schedule.slice(1)}</td>
                    <td class="px-6 py-4 whitespace-nowrap">${data.payment.start_date}</td>
                    <td class="px-6 py-4 whitespace-nowrap">${data.payment.next_payment_date}</td>
                    <td class="px-6 py-4 whitespace-nowrap">
                        <button class="delete-recurring-btn bg-red-500 hover:bg-red-700 text-white font-bold py-1 px-3 rounded" data-id="${tbody.children.length}">Delete</button>
                    </td>
                `;
                tbody.appendChild(row);
                
                // Add event listener to the new delete button
                const deleteButton = row.querySelector('.delete-recurring-btn');
                deleteButton.addEventListener('click', async function() {
                    const paymentIndex = this.dataset.id;
                    
                    if (confirm('Are you sure you want to delete this recurring payment?')) {
                        try {
                            const response = await fetch(`/api/loan/${loanId}/recurring_payment/${paymentIndex}`, {
                                method: 'DELETE'
                            });

                            const data = await response.json();
                            
                            if (!response.ok) {
                                throw new Error(data.error || 'Failed to delete recurring payment');
                            }

                            // Remove the row from the table
                            this.closest('tr').remove();
                            
                            // If no payments left, show the "No recurring payments" message
                            if (tbody.children.length === 0) {
                                tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;">No recurring payments set up.</td></tr>';
                            } else {
                                // Update indices for remaining delete buttons
                                const remainingButtons = document.querySelectorAll('.delete-recurring-btn');
                                remainingButtons.forEach((btn, index) => {
                                    btn.dataset.id = index;
                                });
                            }
                            
                            // Show success message
                            alert(data.message);
                        } catch (error) {
                            console.error('Error:', error);
                            alert(error.message || 'Failed to delete recurring payment');
                        }
                    }
                });
                
                // Reset form
                recurringPaymentForm.reset();
                
                // Show success message
                alert(data.message);
            } catch (error) {
                console.error('Error:', error);
                alert(error.message || 'Failed to add recurring payment');
            }
        });
    }

    // Handle delete recurring payment buttons
    const deleteRecurringButtons = document.querySelectorAll('.delete-recurring-btn');
    deleteRecurringButtons.forEach(button => {
        button.addEventListener('click', async function() {
            const paymentIndex = this.dataset.id;
            const tbody = document.querySelector('#recurring-payments-table tbody');
            
            if (confirm('Are you sure you want to delete this recurring payment?')) {
                try {
                    const response = await fetch(`/api/loan/${loanId}/recurring_payment/${paymentIndex}`, {
                        method: 'DELETE'
                    });

                    const data = await response.json();
                    
                    if (!response.ok) {
                        throw new Error(data.error || 'Failed to delete recurring payment');
                    }

                    // Remove the row from the table
                    this.closest('tr').remove();
                    
                    // If no payments left, show the "No recurring payments" message
                    if (tbody.children.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;">No recurring payments set up.</td></tr>';
                    } else {
                        // Update indices for remaining delete buttons
                        const remainingButtons = document.querySelectorAll('.delete-recurring-btn');
                        remainingButtons.forEach((btn, index) => {
                            btn.dataset.id = index;
                        });
                    }
                    
                    // Show success message
                    alert(data.message);
                } catch (error) {
                    console.error('Error:', error);
                    alert(error.message || 'Failed to delete recurring payment');
                }
            }
        });
    });
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
    document.getElementById('current-balance').textContent = `£${parseFloat(data.loan_amount).toLocaleString('en-GB', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    document.getElementById('interest-rate-input').value = data.interest_rate.toFixed(2);
    
    // Update transaction table
    const tbody = document.querySelector('#transactions-table tbody');
    tbody.innerHTML = '';

    data.transactions.reverse().forEach(transaction => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td class="px-6 py-4 whitespace-nowrap">${transaction.date}</td>
            <td class="px-6 py-4 whitespace-nowrap">${transaction.type.charAt(0).toUpperCase() + transaction.type.slice(1)}</td>
            <td class="px-6 py-4 whitespace-nowrap ${transaction.type === 'repayment' ? 'text-red-600' : ''}">
                £${Math.abs(transaction.amount).toLocaleString('en-GB', {minimumFractionDigits: 2, maximumFractionDigits: 2})}
            </td>
            <td class="px-6 py-4 whitespace-nowrap">${transaction.description}</td>
            <td class="px-6 py-4 whitespace-nowrap">${transaction.user}</td>
        `;
        tbody.appendChild(row);
    });
} 