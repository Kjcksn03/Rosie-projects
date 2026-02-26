import os
import sqlite3
import json
import re
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, g, send_from_directory)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'vip-medical-dev-secret-2024')
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB
DATABASE = os.path.join(os.path.dirname(__file__), 'clinic_tracker.db')

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx', 'txt'}

DEPARTMENTS = [
    'Strategic Growth', 'Marketing', 'IT', 'Inventory', 'Operations',
    'Accounting / Accounts Payable', 'Credentialing', 'RODs & Clinic Leads',
    'Malpractice (Unit)', 'Special Operations', 'Referrals', 'HR'
]

TIME_PHASES = ['Pre-Lease', 'Post-Lease', '90 Days Out', '60 Days Out', '30 Days Out', 'Opening Week', 'Post-Opening']
STATUSES = ['Not Started', 'In Progress', 'Complete', 'Blocked']

# ─── Database ───────────────────────────────────────────────────────────────

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    return (rv[0] if rv else None) if one else rv

def execute_db(query, args=()):
    db = get_db()
    cur = db.execute(query, args)
    db.commit()
    return cur.lastrowid

def init_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'team_member',
            department TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS clinics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            opening_date DATE,
            status TEXT DEFAULT 'Active',
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_template INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clinic_id INTEGER,
            name TEXT NOT NULL,
            department TEXT NOT NULL,
            time_phase TEXT,
            due_date DATE,
            status TEXT DEFAULT 'Not Started',
            assignees TEXT DEFAULT '[]',
            order_index INTEGER DEFAULT 0,
            is_template INTEGER DEFAULT 0,
            template_offset_days INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (clinic_id) REFERENCES clinics(id)
        );
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES tasks(id),
            FOREIGN KEY (author_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            original_name TEXT NOT NULL,
            uploaded_by INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            link TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clinic_id INTEGER,
            task_id INTEGER,
            user_id INTEGER,
            action TEXT NOT NULL,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    db.commit()

    # Seed admin user
    admin = db.execute("SELECT id FROM users WHERE username='kelly'").fetchone()
    if not admin:
        db.execute(
            "INSERT INTO users (username, password_hash, full_name, role) VALUES (?,?,?,?)",
            ('kelly', generate_password_hash('VIPAdmin1!'), 'Kelly (Admin)', 'admin')
        )
        db.commit()

    # Seed template clinic
    tmpl = db.execute("SELECT id FROM clinics WHERE is_template=1").fetchone()
    if not tmpl:
        cur = db.execute(
            "INSERT INTO clinics (name, status, is_template) VALUES (?,?,?)",
            ('Master Template', 'Template', 1)
        )
        tmpl_id = cur.lastrowid
        db.commit()
        seed_template_tasks(db, tmpl_id)

    db.close()

def seed_template_tasks(db, clinic_id):
    tasks = []
    # Strategic Growth
    tasks += [
        ('Identify target market and location demographics', 'Strategic Growth', 'Pre-Lease', -120, 0),
        ('Conduct feasibility study and ROI analysis', 'Strategic Growth', 'Pre-Lease', -110, 1),
        ('Secure lease agreement and negotiate terms', 'Strategic Growth', 'Pre-Lease', -90, 2),
        ('Define clinic service offerings and capacity plan', 'Strategic Growth', 'Post-Lease', -75, 3),
        ('Set clinic launch KPIs and success metrics', 'Strategic Growth', 'Post-Lease', -60, 4),
        ('Create grand opening event strategy', 'Strategic Growth', '30 Days Out', -30, 5),
        ('Post-opening growth review (30 days)', 'Strategic Growth', 'Post-Opening', 30, 6),
    ]
    # Marketing
    tasks += [
        ('Develop clinic brand identity and local marketing plan', 'Marketing', 'Post-Lease', -80, 0),
        ('Design clinic signage, banners, and printed materials', 'Marketing', '60 Days Out', -60, 1),
        ('Build clinic-specific landing page / microsite', 'Marketing', '60 Days Out', -55, 2),
        ('Set up Google Business Profile and online listings', 'Marketing', '60 Days Out', -50, 3),
        ('Launch social media accounts for clinic', 'Marketing', '30 Days Out', -35, 4),
        ('Run pre-launch ads and promotions', 'Marketing', '30 Days Out', -28, 5),
        ('Coordinate grand opening PR and media outreach', 'Marketing', 'Opening Week', -7, 6),
        ('Post-opening patient review campaign', 'Marketing', 'Post-Opening', 14, 7),
    ]
    # IT
    tasks += [
        ('Assess IT infrastructure needs for new clinic', 'IT', 'Post-Lease', -80, 0),
        ('Order and configure workstations, tablets, printers', 'IT', '60 Days Out', -60, 1),
        ('Set up clinic network (internet, VoIP, Wi-Fi)', 'IT', '60 Days Out', -55, 2),
        ('Install and configure EHR / practice management software', 'IT', '30 Days Out', -35, 3),
        ('Configure scheduling system and patient portal', 'IT', '30 Days Out', -30, 4),
        ('Set up security cameras and access control', 'IT', '30 Days Out', -25, 5),
        ('Conduct full IT systems test and staff walkthrough', 'IT', 'Opening Week', -5, 6),
        ('Go-live support on opening day', 'IT', 'Opening Week', 0, 7),
    ]
    # Inventory
    tasks += [
        ('Create master inventory list for clinic type', 'Inventory', 'Post-Lease', -75, 0),
        ('Source and negotiate with medical supply vendors', 'Inventory', '60 Days Out', -60, 1),
        ('Order medical equipment (exam tables, diagnostic tools)', 'Inventory', '60 Days Out', -55, 2),
        ('Order office and administrative supplies', 'Inventory', '30 Days Out', -35, 3),
        ('Receive and inspect all equipment deliveries', 'Inventory', '30 Days Out', -20, 4),
        ('Set up supply room and inventory tracking system', 'Inventory', 'Opening Week', -7, 5),
        ('Verify controlled substance storage compliance', 'Inventory', 'Opening Week', -5, 6),
    ]
    # Operations
    tasks += [
        ('Draft clinic standard operating procedures (SOPs)', 'Operations', 'Post-Lease', -70, 0),
        ('Hire clinical and administrative staff', 'Operations', '60 Days Out', -60, 1),
        ('Schedule staff onboarding and orientation', 'Operations', '30 Days Out', -30, 2),
        ('Conduct mock patient flow walkthrough', 'Operations', '30 Days Out', -21, 3),
        ('Set up patient scheduling templates', 'Operations', '30 Days Out', -20, 4),
        ('Complete OSHA and safety training for all staff', 'Operations', 'Opening Week', -7, 5),
        ('Final operational readiness review', 'Operations', 'Opening Week', -2, 6),
    ]
    # Accounting / Accounts Payable
    tasks += [
        ('Set up clinic cost center / chart of accounts', 'Accounting / Accounts Payable', 'Post-Lease', -70, 0),
        ('Establish vendor payment accounts and billing setup', 'Accounting / Accounts Payable', '60 Days Out', -55, 1),
        ('Set up payroll for new clinic staff', 'Accounting / Accounts Payable', '30 Days Out', -30, 2),
        ('Configure insurance billing and payer contracts', 'Accounting / Accounts Payable', '30 Days Out', -28, 3),
        ('Establish petty cash and clinic operating funds', 'Accounting / Accounts Payable', 'Opening Week', -7, 4),
        ('First payroll cycle confirmation', 'Accounting / Accounts Payable', 'Post-Opening', 14, 5),
    ]
    # Credentialing
    tasks += [
        ('Identify all providers requiring credentialing', 'Credentialing', 'Post-Lease', -80, 0),
        ('Submit provider credentialing applications to payers', 'Credentialing', '90 Days Out', -90, 1),
        ('Obtain clinic NPI (Type 2)', 'Credentialing', '90 Days Out', -85, 2),
        ('Register clinic with state medical board', 'Credentialing', '60 Days Out', -60, 3),
        ('Follow up on pending credentialing applications', 'Credentialing', '30 Days Out', -30, 4),
        ('Confirm all providers credentialed before opening', 'Credentialing', 'Opening Week', -7, 5),
        ('File any post-opening credentialing updates', 'Credentialing', 'Post-Opening', 30, 6),
    ]
    # RODs & Clinic Leads
    tasks += [
        ('Assign Regional Operations Director (ROD) to clinic', 'RODs & Clinic Leads', 'Post-Lease', -70, 0),
        ('Hire and onboard Clinic Lead / Office Manager', 'RODs & Clinic Leads', '60 Days Out', -60, 1),
        ('ROD conducts site visit and readiness assessment', 'RODs & Clinic Leads', '30 Days Out', -21, 2),
        ('Clinic Lead completes leadership training', 'RODs & Clinic Leads', '30 Days Out', -20, 3),
        ('Define escalation and communication protocols', 'RODs & Clinic Leads', '30 Days Out', -14, 4),
        ('Pre-opening staff meeting led by Clinic Lead', 'RODs & Clinic Leads', 'Opening Week', -3, 5),
        ('Week 1 daily check-in calls with ROD', 'RODs & Clinic Leads', 'Post-Opening', 1, 6),
    ]
    # Malpractice (Unit)
    tasks += [
        ('Identify malpractice insurance carrier and policy type', 'Malpractice (Unit)', 'Post-Lease', -80, 0),
        ('Submit application for clinic malpractice coverage', 'Malpractice (Unit)', '90 Days Out', -85, 1),
        ('Obtain certificates of insurance for all providers', 'Malpractice (Unit)', '60 Days Out', -55, 2),
        ('Review policy exclusions and coverage limits', 'Malpractice (Unit)', '30 Days Out', -28, 3),
        ('Confirm malpractice coverage active before opening', 'Malpractice (Unit)', 'Opening Week', -7, 4),
        ('File any updated policy documentation post-opening', 'Malpractice (Unit)', 'Post-Opening', 30, 5),
    ]
    # Special Operations
    tasks += [
        ('Coordinate buildout / construction oversight', 'Special Operations', 'Post-Lease', -80, 0),
        ('Obtain Certificate of Occupancy', 'Special Operations', '60 Days Out', -60, 1),
        ('Schedule and pass health department inspection', 'Special Operations', '30 Days Out', -25, 2),
        ('Coordinate DEA registration for clinic', 'Special Operations', '60 Days Out', -50, 3),
        ('Ensure ADA compliance review completed', 'Special Operations', '30 Days Out', -30, 4),
        ('Coordinate soft-open / friends & family testing day', 'Special Operations', 'Opening Week', -3, 5),
    ]
    # Referrals
    tasks += [
        ('Map referral network in clinic\'s geographic area', 'Referrals', '60 Days Out', -60, 0),
        ('Reach out to local PCPs and specialists for referral agreements', 'Referrals', '30 Days Out', -35, 1),
        ('Develop referral packet / welcome kit for partners', 'Referrals', '30 Days Out', -28, 2),
        ('Set up referral tracking in EHR', 'Referrals', '30 Days Out', -21, 3),
        ('Host referral partner meet-and-greet at clinic', 'Referrals', 'Opening Week', -1, 4),
        ('Review first-month referral volume report', 'Referrals', 'Post-Opening', 30, 5),
    ]
    # HR
    tasks += [
        ('Define staffing plan and job descriptions for clinic', 'HR', 'Post-Lease', -75, 0),
        ('Post job listings and begin recruitment', 'HR', '60 Days Out', -65, 1),
        ('Complete interviews and extend offers', 'HR', '60 Days Out', -50, 2),
        ('Complete background checks and onboarding paperwork', 'HR', '30 Days Out', -35, 3),
        ('Enroll new staff in benefits and payroll', 'HR', '30 Days Out', -28, 4),
        ('Conduct HR orientation and policy review', 'HR', '30 Days Out', -21, 5),
        ('Confirm all staff credentialed, licensed, and cleared', 'HR', 'Opening Week', -7, 6),
        ('90-day new hire check-ins scheduled', 'HR', 'Post-Opening', 14, 7),
    ]

    for (name, dept, phase, offset, order) in tasks:
        db.execute(
            '''INSERT INTO tasks (clinic_id, name, department, time_phase, status, order_index,
               is_template, template_offset_days) VALUES (?,?,?,?,?,?,?,?)''',
            (clinic_id, name, dept, phase, 'Not Started', order, 1, offset)
        )
    db.commit()

# ─── Auth helpers ────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = query_db("SELECT * FROM users WHERE id=?", [session['user_id']], one=True)
        if not user or user['role'] != 'admin':
            flash('Admin access required.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

def current_user():
    if 'user_id' in session:
        return query_db("SELECT * FROM users WHERE id=?", [session['user_id']], one=True)
    return None

def unread_notification_count():
    if 'user_id' not in session:
        return 0
    row = query_db("SELECT COUNT(*) as cnt FROM notifications WHERE user_id=? AND is_read=0",
                   [session['user_id']], one=True)
    return row['cnt'] if row else 0

app.jinja_env.globals['current_user'] = current_user
app.jinja_env.globals['unread_count'] = unread_notification_count
app.jinja_env.filters['from_json'] = json.loads

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def notify_user(user_id, message, link=None):
    execute_db("INSERT INTO notifications (user_id, message, link) VALUES (?,?,?)",
               (user_id, message, link))

def log_activity(clinic_id, task_id, user_id, action, detail=None):
    execute_db(
        "INSERT INTO activity_log (clinic_id, task_id, user_id, action, detail) VALUES (?,?,?,?,?)",
        (clinic_id, task_id, user_id, action, detail)
    )

def can_edit_task(user, task):
    if user['role'] == 'admin':
        return True
    if user['role'] == 'dept_head' and user['department'] == task['department']:
        return True
    if user['role'] == 'team_member':
        assignees = json.loads(task['assignees'] or '[]')
        if str(user['id']) in [str(a) for a in assignees]:
            return True
    return False

# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = query_db("SELECT * FROM users WHERE username=?", [username], one=True)
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['role'] = user['role']
            session['full_name'] = user['full_name']
            return redirect(url_for('index'))
        flash('Invalid username or password.', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    clinics = query_db("SELECT * FROM clinics WHERE is_template=0 ORDER BY created_at DESC")
    clinic_stats = []
    for clinic in clinics:
        total = query_db("SELECT COUNT(*) as cnt FROM tasks WHERE clinic_id=?", [clinic['id']], one=True)['cnt']
        done = query_db("SELECT COUNT(*) as cnt FROM tasks WHERE clinic_id=? AND status='Complete'", [clinic['id']], one=True)['cnt']
        blocked = query_db("SELECT COUNT(*) as cnt FROM tasks WHERE clinic_id=? AND status='Blocked'", [clinic['id']], one=True)['cnt']
        overdue = query_db(
            "SELECT COUNT(*) as cnt FROM tasks WHERE clinic_id=? AND due_date < date('now') AND status NOT IN ('Complete')",
            [clinic['id']], one=True)['cnt']
        pct = round((done / total * 100) if total > 0 else 0)
        clinic_stats.append({'clinic': dict(clinic), 'total': total, 'done': done, 'pct': pct, 'blocked': blocked, 'overdue': overdue})
    return render_template('index.html', clinic_stats=clinic_stats)

@app.route('/clinic/new', methods=['GET', 'POST'])
@admin_required
def new_clinic():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        opening_date = request.form.get('opening_date', '')
        if not name:
            flash('Clinic name is required.', 'error')
            return render_template('new_clinic.html')
        # Create clinic
        clinic_id = execute_db(
            "INSERT INTO clinics (name, opening_date, created_by) VALUES (?,?,?)",
            (name, opening_date or None, session['user_id'])
        )
        # Copy template tasks
        tmpl = query_db("SELECT * FROM clinics WHERE is_template=1", one=True)
        if tmpl:
            tmpl_tasks = query_db("SELECT * FROM tasks WHERE clinic_id=?", [tmpl['id']])
            for t in tmpl_tasks:
                due = None
                if opening_date and t['template_offset_days'] is not None:
                    od = datetime.strptime(opening_date, '%Y-%m-%d')
                    due = (od + timedelta(days=t['template_offset_days'])).strftime('%Y-%m-%d')
                execute_db(
                    '''INSERT INTO tasks (clinic_id, name, department, time_phase, due_date, status,
                       order_index, template_offset_days) VALUES (?,?,?,?,?,?,?,?)''',
                    (clinic_id, t['name'], t['department'], t['time_phase'], due,
                     'Not Started', t['order_index'], t['template_offset_days'])
                )
        log_activity(clinic_id, None, session['user_id'], 'Clinic Created', name)
        flash(f'Clinic "{name}" created successfully!', 'success')
        return redirect(url_for('clinic_dashboard', clinic_id=clinic_id))
    return render_template('new_clinic.html')

@app.route('/clinic/<int:clinic_id>')
@login_required
def clinic_dashboard(clinic_id):
    clinic = query_db("SELECT * FROM clinics WHERE id=?", [clinic_id], one=True)
    if not clinic:
        flash('Clinic not found.', 'error')
        return redirect(url_for('index'))
    tasks = query_db("SELECT * FROM tasks WHERE clinic_id=? ORDER BY department, order_index", [clinic_id])
    # Build department stats
    dept_stats = {}
    for dept in DEPARTMENTS:
        dept_tasks = [t for t in tasks if t['department'] == dept]
        total = len(dept_tasks)
        done = sum(1 for t in dept_tasks if t['status'] == 'Complete')
        pct = round((done / total * 100) if total > 0 else 0)
        dept_stats[dept] = {'total': total, 'done': done, 'pct': pct}

    total_all = len(tasks)
    done_all = sum(1 for t in tasks if t['status'] == 'Complete')
    pct_all = round((done_all / total_all * 100) if total_all > 0 else 0)

    today = datetime.now().date()
    overdue = [t for t in tasks if t['due_date'] and datetime.strptime(t['due_date'], '%Y-%m-%d').date() < today and t['status'] not in ('Complete',)]
    blocked = [t for t in tasks if t['status'] == 'Blocked']

    activity = query_db(
        '''SELECT al.*, u.full_name, t.name as task_name FROM activity_log al
           LEFT JOIN users u ON al.user_id=u.id
           LEFT JOIN tasks t ON al.task_id=t.id
           WHERE al.clinic_id=? ORDER BY al.created_at DESC LIMIT 20''',
        [clinic_id]
    )
    return render_template('clinic_dashboard.html',
        clinic=clinic, tasks=tasks, dept_stats=dept_stats,
        pct_all=pct_all, overdue=overdue, blocked=blocked,
        activity=activity, departments=DEPARTMENTS, today=str(today))

@app.route('/clinic/<int:clinic_id>/tasks')
@login_required
def clinic_tasks(clinic_id):
    clinic = query_db("SELECT * FROM clinics WHERE id=?", [clinic_id], one=True)
    if not clinic:
        return redirect(url_for('index'))
    dept_filter = request.args.get('dept', '')
    phase_filter = request.args.get('phase', '')
    status_filter = request.args.get('status', '')
    q = "SELECT * FROM tasks WHERE clinic_id=?"
    args = [clinic_id]
    if dept_filter:
        q += " AND department=?"; args.append(dept_filter)
    if phase_filter:
        q += " AND time_phase=?"; args.append(phase_filter)
    if status_filter:
        q += " AND status=?"; args.append(status_filter)
    q += " ORDER BY department, order_index"
    tasks = query_db(q, args)
    users = query_db("SELECT id, full_name, department FROM users ORDER BY full_name")
    today_str = str(datetime.now().date())
    return render_template('tasks.html', clinic=clinic, tasks=tasks, users=users,
                           departments=DEPARTMENTS, time_phases=TIME_PHASES, statuses=STATUSES,
                           dept_filter=dept_filter, phase_filter=phase_filter, status_filter=status_filter,
                           today_str=today_str)

@app.route('/clinic/<int:clinic_id>/task/new', methods=['GET', 'POST'])
@login_required
def new_task(clinic_id):
    user = current_user()
    clinic = query_db("SELECT * FROM clinics WHERE id=?", [clinic_id], one=True)
    if not clinic:
        return redirect(url_for('index'))
    if user['role'] not in ('admin', 'dept_head'):
        flash('Permission denied.', 'error')
        return redirect(url_for('clinic_tasks', clinic_id=clinic_id))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        dept = request.form.get('department', '')
        phase = request.form.get('time_phase', '')
        due = request.form.get('due_date', '') or None
        status = request.form.get('status', 'Not Started')
        assignees = json.dumps(request.form.getlist('assignees'))
        execute_db(
            '''INSERT INTO tasks (clinic_id, name, department, time_phase, due_date, status, assignees)
               VALUES (?,?,?,?,?,?,?)''',
            (clinic_id, name, dept, phase, due, status, assignees)
        )
        log_activity(clinic_id, None, session['user_id'], 'Task Created', name)
        flash('Task created.', 'success')
        return redirect(url_for('clinic_tasks', clinic_id=clinic_id))
    users = query_db("SELECT id, full_name FROM users ORDER BY full_name")
    return render_template('task_form.html', clinic=clinic, task=None, users=users,
                           departments=DEPARTMENTS, time_phases=TIME_PHASES, statuses=STATUSES)

@app.route('/task/<int:task_id>', methods=['GET', 'POST'])
@login_required
def task_detail(task_id):
    task = query_db("SELECT * FROM tasks WHERE id=?", [task_id], one=True)
    if not task:
        flash('Task not found.', 'error')
        return redirect(url_for('index'))
    clinic = query_db("SELECT * FROM clinics WHERE id=?", [task['clinic_id']], one=True)
    user = current_user()
    users = query_db("SELECT id, full_name, department FROM users ORDER BY full_name")
    notes = query_db(
        '''SELECT n.*, u.full_name FROM notes n JOIN users u ON n.author_id=u.id
           WHERE n.task_id=? ORDER BY n.created_at''', [task_id])
    attachments = query_db(
        '''SELECT a.*, u.full_name FROM attachments a JOIN users u ON a.uploaded_by=u.id
           WHERE a.task_id=? ORDER BY a.created_at''', [task_id])

    if request.method == 'POST':
        action = request.form.get('action')
        if not can_edit_task(user, task):
            flash('You do not have permission to update this task.', 'error')
            return redirect(url_for('task_detail', task_id=task_id))

        if action == 'update':
            name = request.form.get('name', task['name'])
            dept = request.form.get('department', task['department'])
            phase = request.form.get('time_phase', task['time_phase'])
            due = request.form.get('due_date', '') or None
            status = request.form.get('status', task['status'])
            assignees = json.dumps(request.form.getlist('assignees'))
            old_status = task['status']
            execute_db(
                '''UPDATE tasks SET name=?, department=?, time_phase=?, due_date=?, status=?,
                   assignees=?, updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                (name, dept, phase, due, status, assignees, task_id)
            )
            if old_status != status:
                log_activity(task['clinic_id'], task_id, session['user_id'],
                             'Status Changed', f'{old_status} → {status}')
                # Notify assignees of status change
                for uid in json.loads(assignees):
                    if int(uid) != session['user_id']:
                        notify_user(int(uid), f'Task "{name}" status changed to {status}',
                                   f'/task/{task_id}')
            flash('Task updated.', 'success')
            return redirect(url_for('task_detail', task_id=task_id))

        elif action == 'add_note':
            content = request.form.get('content', '').strip()
            if content:
                execute_db("INSERT INTO notes (task_id, author_id, content) VALUES (?,?,?)",
                           (task_id, session['user_id'], content))
                log_activity(task['clinic_id'], task_id, session['user_id'],
                             'Note Added', content[:80])
                # @mention notifications
                mentions = re.findall(r'@(\w+)', content)
                for username in mentions:
                    mentioned = query_db("SELECT id FROM users WHERE username=?", [username], one=True)
                    if mentioned and mentioned['id'] != session['user_id']:
                        notify_user(mentioned['id'],
                                   f'{user["full_name"]} mentioned you in task "{task["name"]}"',
                                   f'/task/{task_id}')
                # Notify department members (dept_head or members in that dept)
                dept_users = query_db(
                    "SELECT id FROM users WHERE department=? AND id != ?",
                    [task['department'], session['user_id']])
                for du in dept_users:
                    notify_user(du['id'],
                               f'New note on task "{task["name"]}" in {task["department"]}',
                               f'/task/{task_id}')
                flash('Note added.', 'success')
            return redirect(url_for('task_detail', task_id=task_id))

        elif action == 'upload':
            if 'file' in request.files:
                f = request.files['file']
                if f and f.filename and allowed_file(f.filename):
                    filename = secure_filename(f.filename)
                    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                    saved_name = f'{timestamp}_{filename}'
                    f.save(os.path.join(app.config['UPLOAD_FOLDER'], saved_name))
                    execute_db(
                        "INSERT INTO attachments (task_id, filename, original_name, uploaded_by) VALUES (?,?,?,?)",
                        (task_id, saved_name, filename, session['user_id'])
                    )
                    log_activity(task['clinic_id'], task_id, session['user_id'], 'File Uploaded', filename)
                    flash('File uploaded.', 'success')
                else:
                    flash('Invalid file type.', 'error')
            return redirect(url_for('task_detail', task_id=task_id))

    assignee_ids = json.loads(task['assignees'] or '[]')
    return render_template('task_detail.html', task=task, clinic=clinic, user=user,
                           users=users, notes=notes, attachments=attachments,
                           assignee_ids=[str(a) for a in assignee_ids],
                           departments=DEPARTMENTS, time_phases=TIME_PHASES, statuses=STATUSES,
                           can_edit=can_edit_task(user, task))

@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/notifications')
@login_required
def notifications():
    notifs = query_db(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
        [session['user_id']])
    execute_db("UPDATE notifications SET is_read=1 WHERE user_id=?", [session['user_id']])
    return render_template('notifications.html', notifications=notifs)

@app.route('/api/notifications/count')
@login_required
def notif_count():
    row = query_db("SELECT COUNT(*) as cnt FROM notifications WHERE user_id=? AND is_read=0",
                   [session['user_id']], one=True)
    return jsonify({'count': row['cnt'] if row else 0})

@app.route('/clinic/<int:clinic_id>/quickcheck')
@login_required
def quick_check(clinic_id):
    dept = request.args.get('dept', DEPARTMENTS[0])
    clinic = query_db("SELECT * FROM clinics WHERE id=?", [clinic_id], one=True)
    recent_tasks = query_db(
        '''SELECT t.*, n.content as last_note, n.created_at as note_time, u.full_name as note_author
           FROM tasks t
           LEFT JOIN notes n ON n.id = (SELECT id FROM notes WHERE task_id=t.id ORDER BY created_at DESC LIMIT 1)
           LEFT JOIN users u ON n.author_id=u.id
           WHERE t.clinic_id=? AND t.department=?
           ORDER BY t.updated_at DESC LIMIT 10''',
        [clinic_id, dept])
    return render_template('quick_check.html', clinic=clinic, dept=dept,
                           tasks=recent_tasks, departments=DEPARTMENTS)

@app.route('/admin/users')
@admin_required
def admin_users():
    users = query_db("SELECT * FROM users ORDER BY full_name")
    return render_template('admin_users.html', users=users, departments=DEPARTMENTS)

@app.route('/admin/users/new', methods=['POST'])
@admin_required
def admin_new_user():
    username = request.form.get('username', '').strip()
    full_name = request.form.get('full_name', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'team_member')
    dept = request.form.get('department', '') or None
    if not username or not full_name or not password:
        flash('All fields required.', 'error')
        return redirect(url_for('admin_users'))
    existing = query_db("SELECT id FROM users WHERE username=?", [username], one=True)
    if existing:
        flash('Username already exists.', 'error')
        return redirect(url_for('admin_users'))
    execute_db(
        "INSERT INTO users (username, password_hash, full_name, role, department) VALUES (?,?,?,?,?)",
        (username, generate_password_hash(password), full_name, role, dept)
    )
    flash(f'User {full_name} created.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    if user_id == session['user_id']:
        flash('Cannot delete yourself.', 'error')
        return redirect(url_for('admin_users'))
    execute_db("DELETE FROM users WHERE id=?", [user_id])
    flash('User deleted.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/template')
@admin_required
def template_tasks():
    tmpl = query_db("SELECT * FROM clinics WHERE is_template=1", one=True)
    if not tmpl:
        flash('Template not found.', 'error')
        return redirect(url_for('index'))
    tasks = query_db("SELECT * FROM tasks WHERE clinic_id=? ORDER BY department, order_index", [tmpl['id']])
    users_list = query_db("SELECT id, full_name FROM users ORDER BY full_name")
    return render_template('template.html', clinic=tmpl, tasks=tasks, users=users_list,
                           departments=DEPARTMENTS, time_phases=TIME_PHASES, statuses=STATUSES)

@app.route('/template/task/new', methods=['POST'])
@admin_required
def template_new_task():
    tmpl = query_db("SELECT * FROM clinics WHERE is_template=1", one=True)
    name = request.form.get('name', '').strip()
    dept = request.form.get('department', '')
    phase = request.form.get('time_phase', '')
    offset = int(request.form.get('template_offset_days', 0))
    if name and dept:
        execute_db(
            '''INSERT INTO tasks (clinic_id, name, department, time_phase, is_template, template_offset_days)
               VALUES (?,?,?,?,?,?)''',
            (tmpl['id'], name, dept, phase, 1, offset)
        )
        flash('Template task added.', 'success')
    return redirect(url_for('template_tasks'))

@app.route('/template/task/<int:task_id>/delete', methods=['POST'])
@admin_required
def template_delete_task(task_id):
    execute_db("DELETE FROM tasks WHERE id=?", [task_id])
    flash('Template task deleted.', 'success')
    return redirect(url_for('template_tasks'))

@app.route('/clinic/<int:clinic_id>/delete', methods=['POST'])
@admin_required
def delete_clinic(clinic_id):
    clinic = query_db("SELECT * FROM clinics WHERE id=?", [clinic_id], one=True)
    if clinic:
        execute_db("DELETE FROM notes WHERE task_id IN (SELECT id FROM tasks WHERE clinic_id=?)", [clinic_id])
        execute_db("DELETE FROM attachments WHERE task_id IN (SELECT id FROM tasks WHERE clinic_id=?)", [clinic_id])
        execute_db("DELETE FROM tasks WHERE clinic_id=?", [clinic_id])
        execute_db("DELETE FROM activity_log WHERE clinic_id=?", [clinic_id])
        execute_db("DELETE FROM clinics WHERE id=?", [clinic_id])
        flash(f'Clinic "{clinic["name"]}" deleted.', 'success')
    return redirect(url_for('index'))

# ─── Due-date notifications check (called on each request for simplicity) ────

@app.before_request
def check_due_notifications():
    if 'user_id' not in session:
        return
    if not request.endpoint or request.endpoint in ('static', 'notif_count'):
        return
    # Check once per session per day
    last_check = session.get('due_check_date')
    today = datetime.now().strftime('%Y-%m-%d')
    if last_check == today:
        return
    session['due_check_date'] = today
    uid = session['user_id']
    # Tasks assigned to user due within 3 days
    three_days = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')
    due_tasks = query_db(
        '''SELECT t.* FROM tasks t WHERE t.due_date <= ? AND t.due_date >= ?
           AND t.status NOT IN ('Complete') AND t.assignees LIKE ?''',
        [three_days, today, f'%{uid}%'])
    for t in due_tasks:
        # Avoid duplicate notifications
        existing = query_db(
            "SELECT id FROM notifications WHERE user_id=? AND message LIKE ? AND created_at >= date('now', '-1 day')",
            [uid, f'%{t["name"]}%'], one=True)
        if not existing:
            notify_user(uid, f'Task "{t["name"]}" is due soon ({t["due_date"]})', f'/task/{t["id"]}')

# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    with app.app_context():
        init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
else:
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    with app.app_context():
        init_db()
