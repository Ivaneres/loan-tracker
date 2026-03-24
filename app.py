from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from functools import wraps
from datetime import datetime, date, timedelta
import json
import os
from dateutil.relativedelta import relativedelta
from calendar import monthrange
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'  # Change this in production
app.config['SESSION_COOKIE_NAME'] = 'finance_tracker_session'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False  # Set True if serving over HTTPS
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=31)

def load_users():
    """
    Load users from FINANCE_TRACKER_USERS env var:
    "username:password,another:secret"
    Falls back to defaults for local development.
    """
    raw_users = os.getenv('FINANCE_TRACKER_USERS', '').strip()
    if not raw_users:
        return {
            'admin': 'admin123',
            'user': 'user123'
        }

    users = {}
    for pair in raw_users.split(','):
        if ':' not in pair:
            continue
        username, password = pair.split(':', 1)
        username = username.strip()
        password = password.strip()
        if username and password:
            users[username] = password

    return users or {
        'admin': 'admin123',
        'user': 'user123'
    }

USERS = load_users()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

scheduler = BackgroundScheduler()
scheduler.start()

def load_data():
    default_data = {
        'loans': {}  # Dictionary to store multiple loans
    }
    try:
        with open('data.json', 'r') as f:
            content = f.read().strip()
            if not content:  # Handle empty file
                save_data(default_data)
                return default_data
            
            # Load existing data and merge with defaults to handle missing fields
            existing_data = json.loads(content)
            if 'loans' not in existing_data:
                # Convert old format to new format
                old_data = existing_data
                existing_data = default_data
                if 'loan_amount' in old_data:
                    loan_id = 'loan_1'  # Default ID for the first loan
                    existing_data['loans'][loan_id] = {
                        'name': 'My Loan',  # Default name
                        'loan_amount': old_data.get('loan_amount', 0),
                        'interest_rate': old_data.get('interest_rate', 0),
                        'interest_day': old_data.get('interest_day', 1),
                        'transactions': old_data.get('transactions', [])
                    }
            
            return existing_data
            
    except (FileNotFoundError, json.JSONDecodeError):
        save_data(default_data)
        return default_data

def save_data(data):
    with open('data.json', 'w') as f:
        json.dump(data, f, indent=2)

def should_apply_interest(target_day):
    today = date.today()
    if today.day == target_day:
        return True
    last_day_of_month = monthrange(today.year, today.month)[1]
    return today.day == last_day_of_month

def apply_monthly_interest(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return
    
    loan = data['loans'][loan_id]
    target_day = loan['interest_day']
    
    if not should_apply_interest(target_day):
        return  # Skip if not the correct day
    
    current_amount = loan['loan_amount']
    rate = loan['interest_rate']
    
    # Calculate monthly interest
    monthly_interest = (current_amount * (rate / 100)) / 12
    
    new_transaction = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'type': 'interest',
        'amount': monthly_interest,
        'description': f'Monthly interest at {rate}% APR (automated)',
        'user': 'system'
    }
    
    loan['transactions'].append(new_transaction)
    loan['loan_amount'] += monthly_interest
    save_data(data)

def schedule_interest_task(loan_id, day_of_month):
    # Remove existing interest jobs for this loan
    job_id = f'monthly_interest_{loan_id}'
    for job in scheduler.get_jobs():
        if job.id == job_id:
            scheduler.remove_job(job_id)
    
    # Schedule new interest job
    if 1 <= day_of_month <= 31:
        if day_of_month >= 28:
            scheduler.add_job(
                lambda: apply_monthly_interest(loan_id),
                CronTrigger(day="28-31"),
                id=job_id,
                replace_existing=True
            )
        else:
            scheduler.add_job(
                lambda: apply_monthly_interest(loan_id),
                CronTrigger(day=str(day_of_month)),
                id=job_id,
                replace_existing=True
            )
    else:
        raise ValueError(f"Invalid day of month: {day_of_month}")

def calculate_loan_stats(transactions):
    total_loan_quantity = 0
    total_paid = 0
    
    for transaction in transactions:
        if transaction['type'] in ['initial', 'addition']:
            total_loan_quantity += transaction['amount']
        elif transaction['type'] == 'repayment':
            total_paid += abs(transaction['amount'])
    
    return total_loan_quantity, total_paid

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = (request.form.get('password') or '').strip()
        
        if username in USERS and USERS[username] == password:
            session.permanent = True
            session['username'] = username
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Invalid username or password')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    data = load_data()
    loans_summary = []
    for loan_id, loan in data['loans'].items():
        total_loan_quantity, total_paid = calculate_loan_stats(loan['transactions'])
        loans_summary.append({
            'id': loan_id,
            'name': loan['name'],
            'current_balance': loan['loan_amount'],
            'total_loan_quantity': total_loan_quantity,
            'total_paid': total_paid,
            'deleted': loan.get('deleted', False)
        })
    return render_template('loans.html', loans=loans_summary)

@app.route('/loan/<loan_id>')
@login_required
def loan_details(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return redirect(url_for('index'))
    
    loan = data['loans'][loan_id]
    total_loan_quantity, total_paid = calculate_loan_stats(loan['transactions'])
    loan_deleted = loan.get('deleted', False)
    recurring_payments = loan.get('recurring_payments', [])
    
    return render_template('index.html',
                         loan_id=loan_id,
                         loan_name=loan['name'],
                         loan_amount=loan['loan_amount'],
                         interest_rate=loan['interest_rate'],
                         interest_day=loan['interest_day'],
                         transactions=loan['transactions'],
                         total_loan_quantity=total_loan_quantity,
                         total_paid=total_paid,
                         loan_deleted=loan_deleted,
                         recurring_payments=recurring_payments)

@app.route('/api/loan', methods=['POST'])
@login_required
def create_loan():
    data = load_data()
    loan_data = request.get_json()
    
    # Generate a unique ID for the new loan
    loan_id = f"loan_{len(data['loans']) + 1}"
    
    new_loan = {
        'name': loan_data['name'],
        'loan_amount': float(loan_data['loan_amount']),
        'interest_rate': float(loan_data['interest_rate']),
        'interest_day': 1,  # Default to 1st of month
        'transactions': [{
            'date': datetime.now().strftime('%Y-%m-%d'),
            'type': 'initial',
            'amount': float(loan_data['loan_amount']),
            'description': 'Initial loan amount',
            'user': session['username']
        }]
    }
    
    data['loans'][loan_id] = new_loan
    save_data(data)
    schedule_interest_task(loan_id, new_loan['interest_day'])
    
    return jsonify({'id': loan_id, **new_loan})

@app.route('/api/loan/<loan_id>/update_interest_day', methods=['POST'])
@login_required
def update_interest_day(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    
    loan = data['loans'][loan_id]
    new_day = int(request.get_json()['interest_day'])
    old_day = loan['interest_day']
    
    if not 1 <= new_day <= 31:
        return jsonify({'error': 'Day must be between 1 and 31'}), 400
    
    if new_day != old_day:
        # Add the change to transaction history
        new_transaction = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'type': 'settings',
            'amount': 0,
            'description': f'Interest day changed from {old_day} to {new_day}',
            'user': session['username']
        }
        loan['transactions'].append(new_transaction)
        
        # Update the interest day
        loan['interest_day'] = new_day
        save_data(data)
        schedule_interest_task(loan_id, new_day)
    
    return jsonify(loan)

@app.route('/api/loan/<loan_id>/update_interest_rate', methods=['POST'])
@login_required
def update_interest_rate(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    
    loan = data['loans'][loan_id]
    new_rate = float(request.get_json()['interest_rate'])
    old_rate = loan['interest_rate']
    
    if new_rate < 0 or new_rate > 100:
        return jsonify({'error': 'Interest rate must be between 0 and 100'}), 400
    
    if new_rate != old_rate:
        # Add the change to transaction history
        new_transaction = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'type': 'settings',
            'amount': 0,
            'description': f'Interest rate changed from {old_rate:.2f}% to {new_rate:.2f}%',
            'user': session['username']
        }
        loan['transactions'].append(new_transaction)
        
        # Update the interest rate
        loan['interest_rate'] = new_rate
        save_data(data)
    
    return jsonify(loan)

@app.route('/api/loan/<loan_id>/transaction', methods=['POST'])
@login_required
def add_transaction(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    
    loan = data['loans'][loan_id]
    transaction = request.get_json()
    
    amount = float(transaction['amount'])
    transaction_type = transaction['type']
    
    if transaction_type == 'repayment':
        amount = -amount  # Repayments reduce the loan amount
    
    new_transaction = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'type': transaction_type,
        'amount': amount,
        'description': transaction['description'],
        'user': session['username']
    }
    
    loan['transactions'].append(new_transaction)
    loan['loan_amount'] += amount
    
    save_data(data)
    return jsonify(loan)

@app.route('/api/loan/<loan_id>/apply_interest', methods=['POST'])
def apply_interest(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    
    loan = data['loans'][loan_id]
    current_amount = loan['loan_amount']
    rate = loan['interest_rate']
    
    # Calculate monthly interest
    monthly_interest = (current_amount * (rate / 100)) / 12
    
    new_transaction = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'type': 'interest',
        'amount': monthly_interest,
        'description': f'Monthly interest at {rate}% APR',
        'user': session['username']
    }
    
    loan['transactions'].append(new_transaction)
    loan['loan_amount'] += monthly_interest
    
    save_data(data)
    return jsonify(loan)

@app.route('/api/loan/<loan_id>/update_name', methods=['POST'])
@login_required
def update_loan_name(loan_id):
    data = load_data()
    if loan_id not in data['loans']:
        return jsonify({'error': 'Loan not found'}), 404
    
    loan = data['loans'][loan_id]
    new_name = request.get_json()['name']
    old_name = loan['name']
    
    if not new_name or new_name.strip() == '':
        return jsonify({'error': 'Loan name cannot be empty'}), 400
    
    if new_name != old_name:
        # Add the change to transaction history
        new_transaction = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'type': 'settings',
            'amount': 0,
            'description': f'Loan name changed from "{old_name}" to "{new_name}"',
            'user': session['username']
        }
        loan['transactions'].append(new_transaction)
        
        # Update the loan name
        loan['name'] = new_name
        save_data(data)
    
    return jsonify(loan)

def update_loan_status(loan_id, deleted_status, action_description):
    """Helper function to update loan status and add transaction history entry."""
    data = load_data()
    if loan_id not in data['loans']:
        return None, 'Loan not found', 404
    
    # Update loan status
    data['loans'][loan_id]['deleted'] = deleted_status
    
    # Add status change event to transaction history
    new_transaction = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'type': 'settings',
        'amount': 0,
        'description': action_description,
        'user': session['username']
    }
    data['loans'][loan_id]['transactions'].append(new_transaction)
    
    save_data(data)
    return data['loans'][loan_id], f'Loan {action_description.lower()}', 200

@app.route('/api/loan/<loan_id>/<action>', methods=['POST'])
def update_loan_state(loan_id, action):
    try:
        if action not in ['delete', 'recover']:
            return jsonify({'error': 'Invalid action'}), 400
            
        loan_data, message, status_code = update_loan_status(
            loan_id, 
            action == 'delete',  # True for delete, False for recover
            f'Loan {"marked as deleted" if action == "delete" else "recovered from deleted state"}'
        )
        
        if status_code != 200:
            return jsonify({'error': message}), status_code
            
        return jsonify({'message': message})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def process_recurring_payments(loan_id):
    """Process recurring payments that are due today."""
    data = load_data()
    if loan_id not in data['loans']:
        return
        
    loan = data['loans'][loan_id]
    if 'recurring_payments' not in loan:
        return
        
    today = date.today()
    for payment in loan['recurring_payments']:
        next_payment_date = datetime.strptime(payment['next_payment_date'], '%Y-%m-%d').date()
        
        if today >= next_payment_date:
            # Add the payment as a transaction
            new_transaction = {
                'date': today.strftime('%Y-%m-%d'),
                'type': 'repayment',
                'amount': -float(payment['amount']),  # Negative for repayments
                'description': f'Recurring payment ({payment["schedule"]})',
                'user': 'system'  # Automated payment
            }
            loan['transactions'].append(new_transaction)
            loan['loan_amount'] += new_transaction['amount']  # Add negative amount to reduce balance
            
            # Calculate next payment date
            if payment['schedule'] == 'monthly':
                next_payment_date = next_payment_date + relativedelta(months=1)
            elif payment['schedule'] == 'bi-weekly':
                next_payment_date = next_payment_date + relativedelta(weeks=2)
            elif payment['schedule'] == 'weekly':
                next_payment_date = next_payment_date + relativedelta(weeks=1)
                
            # Update next payment date
            payment['next_payment_date'] = next_payment_date.strftime('%Y-%m-%d')
    
    save_data(data)

def schedule_recurring_payments(loan_id):
    """Schedule recurring payment processing for this loan."""
    # Remove existing recurring payment jobs for this loan
    job_id = f'recurring_payments_{loan_id}'
    for job in scheduler.get_jobs():
        if job.id == job_id:
            scheduler.remove_job(job_id)
    
    # Schedule new recurring payment job to run daily
    scheduler.add_job(
        lambda: process_recurring_payments(loan_id),
        CronTrigger(hour=0, minute=0),  # Run at midnight
        id=job_id,
        replace_existing=True
    )

@app.route('/api/loan/<loan_id>/recurring_payment', methods=['POST'])
def add_recurring_payment(loan_id):
    try:
        data = load_data()
        if loan_id not in data['loans']:
            return jsonify({'error': 'Loan not found'}), 404
            
        payment_data = request.get_json()
        amount = float(payment_data['amount'])
        schedule = payment_data['schedule']
        start_date = datetime.strptime(payment_data['start_date'], '%Y-%m-%d').date()
        today = date.today()
        
        # Calculate next payment date based on schedule
        next_payment_date = start_date
        if schedule == 'monthly':
            next_payment_date = start_date + relativedelta(months=1)
        elif schedule == 'bi-weekly':
            next_payment_date = start_date + relativedelta(weeks=2)
        elif schedule == 'weekly':
            next_payment_date = start_date + relativedelta(weeks=1)
        else:
            return jsonify({'error': 'Invalid schedule'}), 400
            
        # Create new recurring payment
        new_payment = {
            'amount': amount,
            'schedule': schedule,
            'start_date': start_date.strftime('%Y-%m-%d'),
            'next_payment_date': next_payment_date.strftime('%Y-%m-%d')
        }
        
        # Initialize recurring_payments list if it doesn't exist
        if 'recurring_payments' not in data['loans'][loan_id]:
            data['loans'][loan_id]['recurring_payments'] = []
            
        data['loans'][loan_id]['recurring_payments'].append(new_payment)
        
        # If start date is today, process the payment immediately
        if start_date == today:
            # Add the payment as a transaction
            new_transaction = {
                'date': today.strftime('%Y-%m-%d'),
                'type': 'repayment',
                'amount': -amount,  # Negative for repayments
                'description': f'Recurring payment ({schedule})',
                'user': 'system'
            }
            data['loans'][loan_id]['transactions'].append(new_transaction)
            data['loans'][loan_id]['loan_amount'] += new_transaction['amount']  # Add negative amount to reduce balance
        
        save_data(data)
        
        # Schedule recurring payment processing
        schedule_recurring_payments(loan_id)
        
        return jsonify({'message': 'Recurring payment added successfully', 'payment': new_payment})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/loan/<loan_id>/recurring_payment/<int:payment_index>', methods=['DELETE'])
def delete_recurring_payment(loan_id, payment_index):
    try:
        data = load_data()
        if loan_id not in data['loans']:
            return jsonify({'error': 'Loan not found'}), 404
            
        if 'recurring_payments' not in data['loans'][loan_id]:
            return jsonify({'error': 'No recurring payments found'}), 404
            
        if payment_index < 0 or payment_index >= len(data['loans'][loan_id]['recurring_payments']):
            return jsonify({'error': 'Invalid payment index'}), 400
            
        # Remove the payment
        deleted_payment = data['loans'][loan_id]['recurring_payments'].pop(payment_index)
        
        # Add a transaction to record the deletion
        new_transaction = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'type': 'settings',
            'amount': 0,
            'description': f'Recurring payment deleted (£{deleted_payment["amount"]:.2f} {deleted_payment["schedule"]})',
            'user': session['username']
        }
        data['loans'][loan_id]['transactions'].append(new_transaction)
        
        save_data(data)
        
        return jsonify({'message': 'Recurring payment deleted successfully', 'payment': deleted_payment})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Schedule initial interest tasks and recurring payment processing for all loans
    data = load_data()
    for loan_id, loan in data['loans'].items():
        schedule_interest_task(loan_id, loan['interest_day'])
        if 'recurring_payments' in loan and loan['recurring_payments']:
            schedule_recurring_payments(loan_id)
    app.run(debug=False, use_reloader=False)  # disable reloader to prevent duplicate schedulers
