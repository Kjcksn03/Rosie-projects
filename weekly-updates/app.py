import os
import sqlite3
import csv
import io
from datetime import date, timedelta
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, session, g, Response, jsonify)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'vip-weekly-updates-secret-2024')

DATABASE = os.path.join(os.path.dirname(__file__), 'weekly_updates.db')


# ── DB helpers ──────────────────────────────────────────────────────────────

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


def init_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT,
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS clinics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            state TEXT,
            opening_date TEXT,
            assigned_pm_id INTEGER REFERENCES users(id),
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clinic_id INTEGER NOT NULL REFERENCES clinics(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            week_ending TEXT NOT NULL,
            status TEXT NOT NULL,
            completed_this_week TEXT,
            next_week TEXT,
            blockers TEXT,
            approvals_needed TEXT,
            help_needed TEXT,
            timeline_changes TEXT,
            cost_changes TEXT,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(clinic_id, week_ending)
        );
    """)
    # Seed admin user
    existing = db.execute("SELECT id FROM users WHERE username='kelly'").fetchone()
    if not existing:
        db.execute(
            "INSERT INTO users (username, password_hash, full_name, email, is_admin) VALUES (?,?,?,?,1)",
            ('kelly', generate_password_hash('VIPAdmin1!'), 'Kelly Jackson',
             'kelly.jackson@vipmedicalgroup.com')
        )
    db.commit()
    db.close()


# ── Date helpers ─────────────────────────────────────────────────────────────

def nearest_friday(d=None):
    if d is None:
        d = date.today()
    # weekday(): Mon=0 … Fri=4 … Sun=6
    days_ahead = 4 - d.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return (d + timedelta(days=days_ahead)).isoformat()


def current_week_ending():
    return nearest_friday()


# ── Auth ─────────────────────────────────────────────────────────────────────

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
        if not session.get('is_admin'):
            flash('Admin access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


def get_current_user():
    if 'user_id' not in session:
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE LOWER(username)=?", (username,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['user_id'] = user['id']
            session['is_admin'] = bool(user['is_admin'])
            session['full_name'] = user['full_name']
            return redirect(url_for('dashboard'))
        flash('Invalid username or password.', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    week = current_week_ending()
    user = get_current_user()

    if session.get('is_admin'):
        # All clinics with current week status
        clinics = db.execute("""
            SELECT c.*, u.full_name as pm_name,
                   upd.status as week_status, upd.id as update_id
            FROM clinics c
            LEFT JOIN users u ON c.assigned_pm_id = u.id
            LEFT JOIN updates upd ON upd.clinic_id = c.id AND upd.week_ending = ?
            WHERE c.active = 1
            ORDER BY c.name
        """, (week,)).fetchall()

        # Submission stats
        all_pms = db.execute("""
            SELECT DISTINCT u.id, u.full_name
            FROM users u
            JOIN clinics c ON c.assigned_pm_id = u.id
            WHERE u.is_admin = 0 AND c.active = 1
        """).fetchall()

        submitted_ids = db.execute("""
            SELECT DISTINCT u.user_id FROM updates u
            WHERE u.week_ending = ?
        """, (week,)).fetchall()
        submitted_set = {r['user_id'] for r in submitted_ids}

        stats = {
            'total': len(clinics),
            'on_track': sum(1 for c in clinics if c['week_status'] == 'On Track'),
            'at_risk': sum(1 for c in clinics if c['week_status'] == 'At Risk'),
            'delayed': sum(1 for c in clinics if c['week_status'] == 'Delayed'),
            'missing': sum(1 for c in clinics if not c['update_id']),
        }

        pm_status = [{'pm': pm, 'submitted': pm['id'] in submitted_set} for pm in all_pms]

        return render_template('admin_dashboard.html',
                               clinics=clinics, week=week, stats=stats,
                               pm_status=pm_status, user=user)
    else:
        # Team member view
        my_clinics = db.execute("""
            SELECT c.*, upd.status as week_status, upd.id as update_id
            FROM clinics c
            LEFT JOIN updates upd ON upd.clinic_id = c.id AND upd.week_ending = ?
            WHERE c.assigned_pm_id = ? AND c.active = 1
            ORDER BY c.name
        """, (week, session['user_id'])).fetchall()

        missing = [c for c in my_clinics if not c['update_id']]

        recent_updates = db.execute("""
            SELECT u.*, c.name as clinic_name
            FROM updates u
            JOIN clinics c ON c.id = u.clinic_id
            WHERE u.user_id = ?
            ORDER BY u.week_ending DESC, u.submitted_at DESC
            LIMIT 20
        """, (session['user_id'],)).fetchall()

        return render_template('member_dashboard.html',
                               my_clinics=my_clinics, week=week,
                               missing=missing, recent_updates=recent_updates,
                               user=user)


# ── Submit / Edit Update ──────────────────────────────────────────────────────

@app.route('/update/new', methods=['GET', 'POST'])
@login_required
def new_update():
    db = get_db()
    week = request.args.get('week', current_week_ending())
    clinic_id = request.args.get('clinic_id', type=int)

    if session.get('is_admin'):
        clinics = db.execute("SELECT * FROM clinics WHERE active=1 ORDER BY name").fetchall()
    else:
        clinics = db.execute(
            "SELECT * FROM clinics WHERE assigned_pm_id=? AND active=1 ORDER BY name",
            (session['user_id'],)
        ).fetchall()

    selected_clinic = None
    if clinic_id:
        selected_clinic = db.execute("SELECT * FROM clinics WHERE id=?", (clinic_id,)).fetchone()
        # Check existing
        existing = db.execute(
            "SELECT * FROM updates WHERE clinic_id=? AND week_ending=?",
            (clinic_id, week)
        ).fetchone()
        if existing:
            return redirect(url_for('edit_update', update_id=existing['id']))

    if request.method == 'POST':
        cid = request.form.get('clinic_id', type=int)
        week_end = request.form.get('week_ending')
        status = request.form.get('status')
        completed = request.form.get('completed_this_week', '')
        next_wk = request.form.get('next_week', '')
        blockers = request.form.get('blockers', '')
        approvals = request.form.get('approvals_needed', '')
        help_needed = request.form.get('help_needed', '')

        # Verify PM owns this clinic (or is admin)
        clinic = db.execute("SELECT * FROM clinics WHERE id=?", (cid,)).fetchone()
        if not clinic:
            flash('Clinic not found.', 'error')
            return redirect(url_for('dashboard'))
        if not session.get('is_admin') and clinic['assigned_pm_id'] != session['user_id']:
            flash('Not authorized for this clinic.', 'error')
            return redirect(url_for('dashboard'))

        try:
            db.execute("""
                INSERT INTO updates
                (clinic_id, user_id, week_ending, status, completed_this_week,
                 next_week, blockers, approvals_needed, help_needed)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (cid, session['user_id'], week_end, status, completed,
                  next_wk, blockers, approvals, help_needed))
            db.commit()
            flash('Update submitted successfully!', 'success')
        except sqlite3.IntegrityError:
            flash('An update for this clinic and week already exists. Edit it instead.', 'error')
        return redirect(url_for('dashboard'))

    return render_template('update_form.html', clinics=clinics, week=week,
                           selected_clinic=selected_clinic, update=None, user=get_current_user())


@app.route('/update/<int:update_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_update(update_id):
    db = get_db()
    upd = db.execute("SELECT * FROM updates WHERE id=?", (update_id,)).fetchone()
    if not upd:
        flash('Update not found.', 'error')
        return redirect(url_for('dashboard'))
    if not session.get('is_admin') and upd['user_id'] != session['user_id']:
        flash('Not authorized.', 'error')
        return redirect(url_for('dashboard'))

    clinic = db.execute("SELECT * FROM clinics WHERE id=?", (upd['clinic_id'],)).fetchone()

    if request.method == 'POST':
        db.execute("""
            UPDATE updates SET status=?, completed_this_week=?, next_week=?,
            blockers=?, approvals_needed=?, help_needed=?
            WHERE id=?
        """, (
            request.form.get('status'),
            request.form.get('completed_this_week', ''),
            request.form.get('next_week', ''),
            request.form.get('blockers', ''),
            request.form.get('approvals_needed', ''),
            request.form.get('help_needed', ''),
            update_id
        ))
        db.commit()
        flash('Update saved.', 'success')
        return redirect(url_for('dashboard'))

    return render_template('update_form.html', clinics=[clinic], week=upd['week_ending'],
                           selected_clinic=clinic, update=upd, user=get_current_user())


# ── Admin: Users ──────────────────────────────────────────────────────────────

@app.route('/admin/users')
@admin_required
def admin_users():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY full_name").fetchall()
    return render_template('admin_users.html', users=users, user=get_current_user())


@app.route('/admin/users/add', methods=['POST'])
@admin_required
def admin_add_user():
    db = get_db()
    username = request.form.get('username', '').strip().lower()
    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')
    is_admin = 1 if request.form.get('is_admin') else 0

    if not username or not full_name or not password:
        flash('Username, full name, and password are required.', 'error')
        return redirect(url_for('admin_users'))

    try:
        db.execute(
            "INSERT INTO users (username, password_hash, full_name, email, is_admin) VALUES (?,?,?,?,?)",
            (username, generate_password_hash(password), full_name, email, is_admin)
        )
        db.commit()
        flash(f'User {full_name} added.', 'success')
    except sqlite3.IntegrityError:
        flash('Username already exists.', 'error')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    if user_id == session['user_id']:
        flash("Can't delete yourself.", 'error')
        return redirect(url_for('admin_users'))
    db = get_db()
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    flash('User deleted.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/reset-password', methods=['POST'])
@admin_required
def admin_reset_password(user_id):
    db = get_db()
    new_pass = request.form.get('new_password', '')
    if not new_pass:
        flash('Password cannot be empty.', 'error')
        return redirect(url_for('admin_users'))
    db.execute("UPDATE users SET password_hash=? WHERE id=?",
               (generate_password_hash(new_pass), user_id))
    db.commit()
    flash('Password reset.', 'success')
    return redirect(url_for('admin_users'))


# ── Admin: Clinics ────────────────────────────────────────────────────────────

@app.route('/admin/clinics')
@admin_required
def admin_clinics():
    db = get_db()
    clinics = db.execute("""
        SELECT c.*, u.full_name as pm_name
        FROM clinics c LEFT JOIN users u ON c.assigned_pm_id = u.id
        WHERE c.active = 1 ORDER BY c.name
    """).fetchall()
    users = db.execute("SELECT * FROM users WHERE is_admin=0 ORDER BY full_name").fetchall()
    return render_template('admin_clinics.html', clinics=clinics, users=users,
                           user=get_current_user())


@app.route('/admin/clinics/add', methods=['POST'])
@admin_required
def admin_add_clinic():
    db = get_db()
    name = request.form.get('name', '').strip()
    state = request.form.get('state', '').strip()
    opening_date = request.form.get('opening_date', '').strip() or None
    pm_id = request.form.get('assigned_pm_id', type=int) or None

    if not name:
        flash('Clinic name is required.', 'error')
        return redirect(url_for('admin_clinics'))

    db.execute("INSERT INTO clinics (name, state, opening_date, assigned_pm_id) VALUES (?,?,?,?)",
               (name, state, opening_date, pm_id))
    db.commit()
    flash(f'Clinic "{name}" added.', 'success')
    return redirect(url_for('admin_clinics'))


@app.route('/admin/clinics/<int:clinic_id>/edit', methods=['POST'])
@admin_required
def admin_edit_clinic(clinic_id):
    db = get_db()
    name = request.form.get('name', '').strip()
    state = request.form.get('state', '').strip()
    opening_date = request.form.get('opening_date', '').strip() or None
    pm_id = request.form.get('assigned_pm_id', type=int) or None

    db.execute("""
        UPDATE clinics SET name=?, state=?, opening_date=?, assigned_pm_id=?
        WHERE id=?
    """, (name, state, opening_date, pm_id, clinic_id))
    db.commit()
    flash('Clinic updated.', 'success')
    return redirect(url_for('admin_clinics'))


@app.route('/admin/clinics/<int:clinic_id>/delete', methods=['POST'])
@admin_required
def admin_delete_clinic(clinic_id):
    db = get_db()
    db.execute("UPDATE clinics SET active=0 WHERE id=?", (clinic_id,))
    db.commit()
    flash('Clinic removed.', 'success')
    return redirect(url_for('admin_clinics'))


# ── Admin: Reports ────────────────────────────────────────────────────────────

@app.route('/admin/report')
@admin_required
def admin_report():
    db = get_db()
    week = request.args.get('week', current_week_ending())
    pm_filter = request.args.get('pm_id', type=int)
    status_filter = request.args.get('status', '')

    query = """
        SELECT u.*, c.name as clinic_name, c.state, c.opening_date,
               usr.full_name as pm_name
        FROM updates u
        JOIN clinics c ON c.id = u.clinic_id
        JOIN users usr ON usr.id = u.user_id
        WHERE u.week_ending = ?
    """
    params = [week]
    if pm_filter:
        query += " AND u.user_id = ?"
        params.append(pm_filter)
    if status_filter:
        query += " AND u.status = ?"
        params.append(status_filter)
    query += " ORDER BY c.name, usr.full_name"

    updates = db.execute(query, params).fetchall()
    all_pms = db.execute("SELECT * FROM users WHERE is_admin=0 ORDER BY full_name").fetchall()

    # Available weeks
    weeks = db.execute(
        "SELECT DISTINCT week_ending FROM updates ORDER BY week_ending DESC LIMIT 52"
    ).fetchall()

    return render_template('admin_report.html', updates=updates, week=week,
                           all_pms=all_pms, weeks=weeks,
                           pm_filter=pm_filter, status_filter=status_filter,
                           user=get_current_user())


@app.route('/admin/report/csv')
@admin_required
def admin_report_csv():
    db = get_db()
    week = request.args.get('week', current_week_ending())
    pm_filter = request.args.get('pm_id', type=int)
    status_filter = request.args.get('status', '')

    query = """
        SELECT c.name as clinic_name, c.state, c.opening_date,
               usr.full_name as pm_name, u.week_ending, u.status,
               u.completed_this_week, u.next_week, u.blockers,
               u.approvals_needed, u.help_needed, u.submitted_at
        FROM updates u
        JOIN clinics c ON c.id = u.clinic_id
        JOIN users usr ON usr.id = u.user_id
        WHERE u.week_ending = ?
    """
    params = [week]
    if pm_filter:
        query += " AND u.user_id = ?"
        params.append(pm_filter)
    if status_filter:
        query += " AND u.status = ?"
        params.append(status_filter)
    query += " ORDER BY c.name"

    rows = db.execute(query, params).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Clinic', 'State', 'Opening Date', 'Project Manager',
                     'Week Ending', 'Status', 'Completed This Week',
                     'Next Week', 'Blockers', 'Approvals Needed',
                     'Help Needed', 'Submitted At'])
    for r in rows:
        writer.writerow(list(r))

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=weekly_updates_{week}.csv'}
    )


# ── Profile / Password ────────────────────────────────────────────────────────

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    db = get_db()
    user = get_current_user()
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new_pass = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        if not check_password_hash(user['password_hash'], current):
            flash('Current password is incorrect.', 'error')
        elif new_pass != confirm:
            flash('New passwords do not match.', 'error')
        elif len(new_pass) < 6:
            flash('Password must be at least 6 characters.', 'error')
        else:
            db.execute("UPDATE users SET password_hash=? WHERE id=?",
                       (generate_password_hash(new_pass), session['user_id']))
            db.commit()
            flash('Password changed successfully.', 'success')
    return render_template('profile.html', user=user)


# ── Init & Run ────────────────────────────────────────────────────────────────

with app.app_context():
    init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)

@app.route('/update/bulk', methods=['GET', 'POST'])
@login_required
def bulk_update():
    db = get_db()
    week = request.args.get('week', current_week_ending())

    if session.get('is_admin'):
        clinics = db.execute("SELECT * FROM clinics WHERE active=1 ORDER BY name").fetchall()
    else:
        clinics = db.execute(
            "SELECT * FROM clinics WHERE assigned_pm_id=? AND active=1 ORDER BY name",
            (session['user_id'],)
        ).fetchall()

    # Get existing updates for this week
    existing = {}
    for c in clinics:
        u = db.execute("SELECT * FROM updates WHERE clinic_id=? AND week_ending=?",
                       (c['id'], week)).fetchone()
        if u:
            existing[c['id']] = dict(u)

    if request.method == 'POST':
        week_end = request.form.get('week_ending')
        submitted = 0
        for c in clinics:
            status = request.form.get(f'status_{c["id"]}')
            if not status:
                continue  # skip if not filled in
            completed = request.form.get(f'completed_{c["id"]}', '')
            next_wk = request.form.get(f'next_week_{c["id"]}', '')
            blockers = request.form.get(f'blockers_{c["id"]}', '')
            approvals = request.form.get(f'approvals_{c["id"]}', '')
            help_needed = request.form.get(f'help_{c["id"]}', '')
            timeline = request.form.get(f'timeline_{c["id"]}', '')
            cost = request.form.get(f'cost_{c["id"]}', '')

            existing_u = db.execute("SELECT id FROM updates WHERE clinic_id=? AND week_ending=?",
                                    (c['id'], week_end)).fetchone()
            if existing_u:
                db.execute("""UPDATE updates SET status=?, completed_this_week=?, next_week=?,
                    blockers=?, approvals_needed=?, help_needed=?,
                    timeline_changes=?, cost_changes=?
                    WHERE id=?""",
                    (status, completed, next_wk, blockers, approvals, help_needed,
                     timeline, cost, existing_u['id']))
            else:
                db.execute("""INSERT INTO updates
                    (clinic_id, user_id, week_ending, status, completed_this_week,
                     next_week, blockers, approvals_needed, help_needed, timeline_changes, cost_changes)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (c['id'], session['user_id'], week_end, status, completed,
                     next_wk, blockers, approvals, help_needed, timeline, cost))
            submitted += 1
        db.commit()
        flash(f'✅ Updates submitted for {submitted} clinic(s)!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('bulk_update.html', clinics=clinics, week=week,
                           existing=existing, user=get_current_user())
