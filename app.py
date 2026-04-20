from flask import Flask, render_template, request, redirect, session
from datetime import datetime, timedelta, timezone
import random
import smtplib
from email.mime.text import MIMEText
import psycopg2
import os
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "secret123")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@yacht.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "1234")

TOTAL_TICKETS = 30
BOOKING_CODE_RETRIES = 10

BOOKINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS bookings (
    id BIGSERIAL PRIMARY KEY,
    name TEXT,
    phone TEXT,
    email TEXT,
    tickets INTEGER,
    amount NUMERIC(10, 2),
    code INTEGER,
    status TEXT
)
"""

BOOKINGS_TABLE_MIGRATIONS = (
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS attendees TEXT",
)

OTP_CODES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS otp_codes (
    email TEXT PRIMARY KEY,
    otp INTEGER NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
)
"""


def normalize_database_url(db_url):
    db_url = db_url.strip()

    if "://" not in db_url:
        return db_url

    scheme, remainder = db_url.split("://", 1)

    # Handle passwords that contain reserved URL characters like @ or [].
    if "@" in remainder and "/" in remainder:
        credentials, host_and_path = remainder.rsplit("@", 1)
        if ":" in credentials:
            username, password = credentials.split(":", 1)
            encoded_password = quote(unquote(password.strip()), safe="")
            db_url = f"{scheme}://{username}:{encoded_password}@{host_and_path}"

    parsed = urlsplit(db_url)
    if parsed.hostname and parsed.hostname.endswith("supabase.com"):
        query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query_params.setdefault("sslmode", "require")
        db_url = urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query_params),
            parsed.fragment,
        ))

    return db_url

# =========================
# ✅ DB CONNECTION (SAFE)
# =========================
def get_db_connection():
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")

    if db_url:
        conn = psycopg2.connect(normalize_database_url(db_url))
    else:
        local_config = {
            "dbname": os.environ.get("PGDATABASE"),
            "user": os.environ.get("PGUSER"),
            "password": os.environ.get("PGPASSWORD"),
            "host": os.environ.get("PGHOST", "localhost"),
            "port": os.environ.get("PGPORT", "5432"),
        }

        missing_local = [
            key for key, value in local_config.items()
            if key in {"dbname", "user", "password"} and not value
        ]
        if missing_local:
            raise RuntimeError(
                "Set DATABASE_URL or SUPABASE_DB_URL for deploy, "
                "or configure PGDATABASE/PGUSER/PGPASSWORD for local Postgres."
            )

        conn = psycopg2.connect(**local_config)

    initialize_database(conn)
    return conn


def initialize_database(conn):
    cursor = conn.cursor()

    try:
        cursor.execute(BOOKINGS_TABLE_SQL)
        for statement in BOOKINGS_TABLE_MIGRATIONS:
            cursor.execute(statement)
        cursor.execute(OTP_CODES_TABLE_SQL)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def delete_stored_otp(email):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("DELETE FROM otp_codes WHERE email = %s", (email,))
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def store_otp(email, otp):
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO otp_codes (email, otp, expires_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (email)
            DO UPDATE SET otp = EXCLUDED.otp, expires_at = EXCLUDED.expires_at
            """,
            (email, otp, expires_at),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def verify_stored_otp(email, user_otp):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT otp, expires_at
            FROM otp_codes
            WHERE email = %s
            """,
            (email,),
        )
        row = cursor.fetchone()

        if not row:
            return False

        otp, expires_at = row
        if expires_at < datetime.now(timezone.utc):
            cursor.execute("DELETE FROM otp_codes WHERE email = %s", (email,))
            conn.commit()
            return False

        if int(user_otp) != otp:
            return False

        cursor.execute("DELETE FROM otp_codes WHERE email = %s", (email,))
        conn.commit()
        return True
    finally:
        cursor.close()
        conn.close()


def generate_booking_code():
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        for _ in range(BOOKING_CODE_RETRIES):
            booking_code = random.randint(1000, 9999)
            cursor.execute(
                "SELECT 1 FROM bookings WHERE code = %s LIMIT 1",
                (booking_code,),
            )
            if not cursor.fetchone():
                return booking_code
    finally:
        cursor.close()
        conn.close()

    raise RuntimeError("Could not generate a unique booking code.")


# =========================
# 🔐 ADMIN LOGIN
# =========================
@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect('/admin')

        return "Invalid Admin ❌"

    return render_template('admin_login.html')


# =========================
# 🧠 ADMIN DASHBOARD
# =========================
@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect('/admin-login')

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM bookings ORDER BY id DESC")
        bookings = cursor.fetchall()

        cursor.execute("""
            SELECT
                COUNT(*),
                COALESCE(SUM(amount), 0),
                COALESCE(SUM(CASE WHEN status = 'confirmed' THEN tickets ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0)
            FROM bookings
        """)
        total_bookings, total_revenue, sold, pending_count = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    return render_template(
        'admin.html',
        bookings=bookings,
        total_bookings=total_bookings,
        total_revenue=int(total_revenue),
        sold=sold,
        pending=pending_count,
    )


@app.route('/approve/<int:booking_code>')
def approve_booking(booking_code):
    if not session.get('admin_logged_in'):
        return redirect('/admin-login')

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "UPDATE bookings SET status = 'confirmed' WHERE code = %s",
            (booking_code,),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return redirect('/admin')


# =========================
# 📩 SEND OTP EMAIL
# =========================
def send_otp_email(receiver_email, otp):
    sender_email = os.environ.get("EMAIL_USER")
    app_password = os.environ.get("EMAIL_PASS")

    if not sender_email or not app_password:
        print("Email creds missing ❌")
        return False

    try:
        msg = MIMEText(f"Your OTP is {otp}")
        msg['Subject'] = "OTP Verification"
        msg['From'] = sender_email
        msg['To'] = receiver_email

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, app_password)
        server.sendmail(sender_email, receiver_email, msg.as_string())
        server.quit()

        return True

    except Exception as e:
        print("Email Error:", e)
        return False


# =========================
# 🏠 HOME
# =========================
@app.route('/')
def home():
    return render_template('index.html')


# =========================
# 🔐 LOGIN (OTP)
# =========================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')

        if not email:
            return "Email required ❌"

        otp = random.randint(100000, 999999)
        store_otp(email, otp)

        if send_otp_email(email, otp):
            session['email'] = email
            return redirect('/verify')

        delete_stored_otp(email)
        return "Email send failed ❌"

    return render_template('login.html')


# =========================
# 🔑 VERIFY OTP
# =========================
@app.route('/verify', methods=['GET', 'POST'])
def verify():
    if request.method == 'POST':
        user_otp = request.form.get('otp')
        email = session.get('email')

        if (
            email and
            user_otp and
            user_otp.isdigit() and
            verify_stored_otp(email, user_otp)
        ):
            session['logged_in'] = True
            return redirect('/booking')

        return "Invalid OTP ❌"

    return render_template('verify_otp.html')


# =========================
# 🎟️ BOOKING
# =========================
@app.route('/booking', methods=['GET', 'POST'])
def booking():
    if not session.get('logged_in'):
        return redirect('/login')

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT COALESCE(SUM(tickets),0) FROM bookings WHERE status='confirmed'")
        sold_tickets = cursor.fetchone()[0]

        cursor.close()
        conn.close()

    except Exception as e:
        app.logger.exception("Could not fetch booking stats")
        return render_template('booking.html', available=0, sold=0, db_error=str(e))

    available = max(TOTAL_TICKETS - sold_tickets, 0)

    if request.method == 'POST':
        try:
            tickets = int(request.form.get('tickets'))
        except (TypeError, ValueError):
            return "Invalid ticket input ❌"

        if tickets < 1:
            return "At least 1 ticket required ❌"

        if tickets > available:
            return f"Only {available} tickets left ❌"

        price = 8500
        total = tickets * price

        discount = 0
        if tickets == 6:
            discount = 0.10
        elif tickets >= 7:
            discount = 0.15

        final_amount = int(total - (total * discount))

        session['tickets'] = tickets
        session['amount'] = final_amount

        return redirect('/details')

    return render_template('booking.html', available=available, sold=sold_tickets)


# =========================
# 🧾 DETAILS
# =========================
@app.route('/details', methods=['GET', 'POST'])
def details():
    if not session.get('logged_in'):
        return redirect('/login')

    tickets = session.get('tickets')
    if not tickets:
        return redirect('/booking')

    if request.method == 'POST':
        session['name'] = request.form.get('name')
        session['phone'] = request.form.get('phone')
        session['people'] = request.form.getlist('people[]')

        return redirect('/payment')

    return render_template('details.html', tickets=tickets)


# =========================
# 💳 PAYMENT
# =========================
@app.route('/payment', methods=['GET', 'POST'])
def payment():
    if not session.get('logged_in'):
        return redirect('/login')

    if not all([
        session.get('tickets'),
        session.get('amount'),
        session.get('name'),
        session.get('phone'),
        session.get('email'),
    ]):
        return redirect('/details')

    if request.method == 'POST':
        try:
            booking_code = generate_booking_code()
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO bookings (name, phone, email, tickets, amount, code, status, attendees)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                session.get('name'),
                session.get('phone'),
                session.get('email'),
                session.get('tickets'),
                session.get('amount'),
                booking_code,
                "pending",
                "\n".join(session.get('people', [])),
            ))

            conn.commit()
            cursor.close()
            conn.close()

        except Exception as e:
            return f"DB Error ❌ {e}"

        session['booking_code'] = booking_code
        return redirect('/pending')

    return render_template('payment.html', amount=session.get('amount'))


# =========================
# ⏳ PENDING
# =========================
@app.route('/pending')
def pending():
    return render_template('pending.html', code=session.get('booking_code'))


# =========================
# 🧪 TEST DB
# =========================
@app.route('/test-db')
def test_db():
    if os.environ.get("ALLOW_TEST_DB_ROUTE") != "true":
        return "Not Found", 404

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT 1;")
        result = cursor.fetchone()

        cursor.close()
        conn.close()

        return f"DB Connected ✅ {result}"

    except Exception as e:
        return f"DB Error ❌ {e}"


@app.route('/healthz')
def healthz():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1;")
        cursor.fetchone()
        cursor.close()
        conn.close()
        return {"status": "ok"}, 200
    except Exception as e:
        return {"status": "error", "detail": str(e)}, 500


# =========================
# 🚀 RUN
# =========================
if __name__ == '__main__':
    app.run(debug=True)
