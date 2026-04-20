from flask import Flask, render_template, request, redirect, session
import random
import smtplib
from email.mime.text import MIMEText
import mysql.connector

app = Flask(__name__)
app.secret_key = "secret123"

ADMIN_EMAIL = "admin@yacht.com"
ADMIN_PASSWORD = "1234"

TOTAL_TICKETS = 30
sold_tickets = 0
otp_storage = {}

@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect('/admin')
        else:
            return "Invalid Admin Credentials ❌"

    return render_template('admin_login.html')

# 🔌 MySQL Connection
def get_db_connection():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="yacht_db"
    )

# 📩 OTP EMAIL
def send_otp_email(receiver_email, otp):
    sender_email = "solsahil786@gmail.com"
    app_password = "sjzcixjmvoljrgtv"

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

# 🔑 VERIFY
@app.route('/verify', methods=['GET', 'POST'])
def verify():
    if request.method == 'POST':
        user_otp = request.form.get('otp')
        email = session.get('email')

        if email and int(user_otp) == otp_storage.get(email):
            session['logged_in'] = True
            return redirect('/booking')
        else:
            return "Invalid OTP ❌"

    return render_template('verify_otp.html')

# 🎟️ BOOKING
@app.route('/booking', methods=['GET', 'POST'])
def booking():
    global sold_tickets

    if not session.get('logged_in'):
        return redirect('/login')

    available = TOTAL_TICKETS - sold_tickets

    if request.method == 'POST':
        tickets = int(request.form.get('tickets'))

        if tickets > available:
            return f"Only {available} tickets left ❌"

        price = 8500
        total = tickets * price

        if tickets == 6:
            discount = 0.10
        elif tickets >= 7:
            discount = 0.15
        else:
            discount = 0

        final_amount = total - (total * discount)

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

        session['booking_code'] = booking_code

        return redirect('/pending')

    return render_template('payment.html', amount=session.get('amount'))

# ⏳ PENDING
@app.route('/pending')
def pending():
    return render_template('pending.html', code=session.get('booking_code'))

# 🧑‍💻 ADMIN
@app.route('/admin')
def admin():

    # 🔐 SECURITY (IMPORTANT)
    if not session.get('admin_logged_in'):
        return redirect('/admin-login')

    conn = get_db_connection()
    cursor = conn.cursor()

    # 📋 All bookings
    cursor.execute("SELECT * FROM bookings")
    bookings = cursor.fetchall()

    # 📊 Stats
    cursor.execute("SELECT COUNT(*) FROM bookings")
    total_bookings = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(amount) FROM bookings WHERE status='confirmed'")
    total_revenue = cursor.fetchone()[0] or 0

    cursor.execute("SELECT SUM(tickets) FROM bookings WHERE status='confirmed'")
    sold = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM bookings WHERE status='pending'")
    pending = cursor.fetchone()[0]

    cursor.close()
    conn.close()

    return render_template(
        'admin.html',
        bookings=bookings,
        total_bookings=total_bookings,
        total_revenue=total_revenue,
        sold=sold,
        pending=pending
    )
    
def send_confirmation_email(to_email, name, code, tickets):
    sender_email = "solsahil786@gmail.com"
    app_password = "sjzcixjmvoljrgtv"

    subject = "🎉 Booking Confirmed - Yacht Party"
    
    body = f"""
Hello {name},

Your booking has been CONFIRMED ✅

🎟️ Booking Code: {code}
👥 Tickets: {tickets}

Please show this code at entry.

Enjoy the Yacht Party 🛥️🔥

- Team
"""

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = to_email

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, app_password)
        server.sendmail(sender_email, to_email, msg.as_string())
        server.quit()
        print("Confirmation email sent ✅")
    except Exception as e:
        print("Email error:", e)
        
        
# ✅ APPROVE
@app.route('/approve/<int:code>')
def approve(code):

    # 🔐 SECURITY CHECK (VERY IMPORTANT)
    if not session.get('admin_logged_in'):
        return redirect('/admin-login')

    conn = get_db_connection()
    cursor = conn.cursor()

    # ✅ 1. Update status
    cursor.execute(
        "UPDATE bookings SET status='confirmed' WHERE code=%s",
        (code,)
    )
    conn.commit()

    # 📊 2. Get booking details (email + name + tickets)
    cursor.execute(
        "SELECT name, email, tickets FROM bookings WHERE code=%s",
        (code,)
    )
    data = cursor.fetchone()

    name = data[0]
    email = data[1]
    tickets = data[2]

    cursor.close()
    conn.close()

    # 📩 3. Send confirmation email
    send_confirmation_email(email, name, code, tickets)

    return redirect('/admin')

# 🚀 RUN
if __name__ == '__main__':
    app.run(debug=True)