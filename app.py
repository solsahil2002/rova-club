from flask import Flask, render_template, request, redirect, session
import random
import smtplib
from email.mime.text import MIMEText
import psycopg2
import os

app = Flask(__name__)
app.secret_key = "secret123"

ADMIN_EMAIL = "admin@yacht.com"
ADMIN_PASSWORD = "1234"

TOTAL_TICKETS = 30
otp_storage = {}

# ✅ DB CONNECTION (SAFE)
def get_db_connection():
    db_url = os.environ.get("DATABASE_URL")

    if not db_url:
        raise Exception("DATABASE_URL not set ❌")

    return psycopg2.connect(db_url, sslmode='require')


# 🔐 ADMIN LOGIN (FIXED)
@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect('/admin')
        else:
            return "Invalid Admin ❌"

    return render_template('admin_login.html')


# 📩 OTP EMAIL
def send_otp_email(receiver_email, otp):
    sender_email = os.environ.get("EMAIL_USER")
    app_password = os.environ.get("EMAIL_PASS")

    msg = MIMEText(f"Your OTP is {otp}")
    msg['Subject'] = "OTP Verification"
    msg['From'] = sender_email
    msg['To'] = receiver_email

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, app_password)
        server.sendmail(sender_email, receiver_email, msg.as_string())
        server.quit()
    except Exception as e:
        print("Email Error:", e)


# 🏠 HOME
@app.route('/')
def home():
    return render_template('index.html')


# 🔐 LOGIN
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')

        otp = random.randint(100000, 999999)
        otp_storage[email] = otp

        send_otp_email(email, otp)
        session['email'] = email

        return redirect('/verify')

    return render_template('login.html')


# 🔑 VERIFY (FIXED)
@app.route('/verify', methods=['GET', 'POST'])
def verify():
    if request.method == 'POST':
        user_otp = request.form.get('otp')
        email = session.get('email')

        if email and user_otp and user_otp.isdigit() and int(user_otp) == otp_storage.get(email):
            session['logged_in'] = True
            return redirect('/booking')
        else:
            return "Invalid OTP ❌"

    return render_template('verify_otp.html')


# 🎟️ BOOKING
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
    except:
        sold_tickets = 0

    available = TOTAL_TICKETS - sold_tickets

    if request.method == 'POST':
        tickets = int(request.form.get('tickets'))

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


# 🧾 DETAILS
@app.route('/details', methods=['GET', 'POST'])
def details():
    if not session.get('logged_in'):
        return redirect('/login')

    tickets = session.get('tickets')

    if request.method == 'POST':
        session['name'] = request.form.get('name')
        session['phone'] = request.form.get('phone')
        session['people'] = request.form.getlist('people[]')

        return redirect('/payment')

    return render_template('details.html', tickets=tickets)


# 💳 PAYMENT
@app.route('/payment', methods=['GET', 'POST'])
def payment():
    if not session.get('logged_in'):
        return redirect('/login')

    if request.method == 'POST':
        booking_code = random.randint(1000, 9999)

        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO bookings (name, phone, email, tickets, amount, code, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                session.get('name'),
                session.get('phone'),
                session.get('email'),
                session.get('tickets'),
                session.get('amount'),
                booking_code,
                "pending"
            ))

            conn.commit()
            cursor.close()
            conn.close()

        except Exception as e:
            return f"DB Error ❌ {e}"

        session['booking_code'] = booking_code
        return redirect('/pending')

    return render_template('payment.html', amount=session.get('amount'))


# ⏳ PENDING
@app.route('/pending')
def pending():
    return render_template('pending.html', code=session.get('booking_code'))


# 🚀 RUN
if __name__ == '__main__':
    app.run(debug=True)