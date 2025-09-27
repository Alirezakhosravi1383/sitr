import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import sqlite3
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode

# --- Paths ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
QR_FOLDER = os.path.join(BASE_DIR, 'static', 'qrcodes')
DB_PATH = os.path.join(BASE_DIR, 'database.db')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(QR_FOLDER, exist_ok=True)

# --- Flask app ---
app = Flask(__name__)
app.secret_key = 'replace-this-with-a-random-secret'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# --- Database helpers ---
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sid TEXT UNIQUE NOT NULL,
        name TEXT,
        major TEXT,
        year TEXT,
        status TEXT,
        extra TEXT,
        photo_path TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()
    conn.close()

init_db()

def ensure_admin():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", ('admin',))
    if not cur.fetchone():
        cur.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    ('admin', generate_password_hash('admin')))
        conn.commit()
    conn.close()
ensure_admin()

# --- Auth helper ---
def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapper

# --- QR generation ---
def generate_qr(sid, target_url):
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(target_url)
    qr.make(fit=True)
    img = qr.make_image()
    out_path = os.path.join(QR_FOLDER, f"{sid}.png")
    img.save(out_path)
    # مسیر وب برای HTML
    return f"/static/qrcodes/{sid}.png"

# --- Routes ---
@app.route('/')
def home():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        uname = request.form.get('username')
        pwd = request.form.get('password')
        if not uname or not pwd:
            flash('نام کاربری و رمز لازم است', 'danger')
            return redirect(url_for('login'))
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = ?", (uname,))
        row = cur.fetchone()
        conn.close()
        if row and check_password_hash(row['password_hash'], pwd):
            session['user'] = uname
            flash('ورود موفق', 'success')
            return redirect(url_for('dashboard'))
        flash('نام کاربری یا رمز اشتباه است', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    session.pop('user', None)
    flash('خروج انجام شد', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM students ORDER BY created_at DESC")
    students = cur.fetchall()
    conn.close()
    return render_template('dashboard.html', students=students)

@app.route('/students')
@login_required
def list_students():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM students ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return render_template('list.html', students=rows)

@app.route('/student/<sid>')
@login_required
def view_student(sid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM students WHERE sid = ?", (sid,))
    row = cur.fetchone()
    conn.close()
    if not row:
        flash('پرونده یافت نشد', 'danger')
        return redirect(url_for('dashboard'))

    qr_path = None
    possible_qr = os.path.join(QR_FOLDER, f"{sid}.png")
    if os.path.exists(possible_qr):
        qr_path = f"/static/qrcodes/{sid}.png"

    return render_template('view_student.html', student=row, qr=qr_path)

# --- Create student with photo & QR ---
@app.route('/create', methods=['GET','POST'])
@login_required
def create():
    photo_file = None
    qr_file = None

    if request.method == 'POST':
        sid = (request.form.get('sid') or '').strip()
        name = (request.form.get('name') or '').strip()
        major = (request.form.get('major') or '').strip()
        year = (request.form.get('year') or '').strip()
        status = (request.form.get('status') or 'active').strip()
        extra = (request.form.get('extra') or '').strip()

        photo = request.files.get('photo')
        if photo and photo.filename:
            filename = secure_filename(f"{sid}_{photo.filename}")
            p = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            photo.save(p)
            photo_file = f"/static/uploads/{filename}"

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("""INSERT INTO students (sid, name, major, year, status, extra, photo_path)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (sid, name, major, year, status, extra, photo_file))
            conn.commit()
        except sqlite3.IntegrityError:
            flash('شناسه تکراری است', 'danger')
            conn.close()
            return redirect(url_for('create'))
        conn.close()

        # generate QR code pointing to view page
        target = url_for('view_student', sid=sid, _external=True)
        qr_file = generate_qr(sid, target)
        flash('پرونده ساخته شد', 'success')
        return render_template('create.html', photo=photo_file, qr=qr_file)

    return render_template('create.html', photo=None, qr=None)

# --- Edit / Delete student ---
@app.route('/edit/<sid>', methods=['GET','POST'])
@login_required
def edit(sid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM students WHERE sid = ?", (sid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        flash('پرونده یافت نشد', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        name = (request.form.get('name') or row['name']).strip()
        major = (request.form.get('major') or row['major']).strip()
        year = (request.form.get('year') or row['year']).strip()
        extra = (request.form.get('extra') or row['extra']).strip()
        status = (request.form.get('status') or row['status']).strip()

        photo = request.files.get('photo')
        photo_path = row['photo_path']
        if photo and photo.filename:
            filename = secure_filename(f"{sid}_{photo.filename}")
            p = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            photo.save(p)
            photo_path = f"/static/uploads/{filename}"

        cur.execute("""UPDATE students SET name=?, major=?, year=?, status=?, extra=?, photo_path=? WHERE sid=?""",
                    (name, major, year, status, extra, photo_path, sid))
        conn.commit()
        conn.close()
        flash('ویرایش با موفقیت انجام شد', 'success')
        return redirect(url_for('view_student', sid=sid))

    conn.close()
    return render_template('edit.html', student=row)

@app.route('/delete/<sid>', methods=['POST'])
@login_required
def delete(sid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM students WHERE sid = ?", (sid,))
    conn.commit()
    conn.close()
    q = os.path.join(QR_FOLDER, f"{sid}.png")
    if os.path.exists(q):
        os.remove(q)
    flash('پرونده حذف شد', 'info')
    return redirect(url_for('list_students'))

# --- User management ---
@app.route('/add_user', methods=['GET','POST'])
@login_required
def add_user():
    conn = get_db()
    if request.method == 'POST':
        uname = (request.form.get('username') or '').strip()
        pwd = (request.form.get('password') or '').strip()
        if not uname or not pwd:
            flash('نام کاربری و رمز لازم است', 'danger')
            return redirect(url_for('add_user'))
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                        (uname, generate_password_hash(pwd)))
            conn.commit()
            flash('کاربر جدید ساخته شد', 'success')
        except sqlite3.IntegrityError:
            flash('نام کاربری تکراری است', 'danger')
        return redirect(url_for('add_user'))

    users = conn.execute("SELECT username FROM users ORDER BY id ASC").fetchall()
    conn.close()
    return render_template('add_user.html', users=users)

@app.route('/delete_user/<username>', methods=['POST'])
@login_required
def delete_user(username):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE username=?", (username,))
    conn.commit()
    conn.close()
    flash(f'کاربر {username} حذف شد', 'info')
    return redirect(url_for('add_user'))

# --- API ---
@app.route('/api/find', methods=['GET'])
def api_find():
    sid = (request.args.get('sid','') or '').strip()
    if not sid:
        return jsonify({'error':'no sid'}), 400
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM students WHERE sid = ?", (sid,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'error':'not found'}), 404
    return jsonify(dict(row))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
