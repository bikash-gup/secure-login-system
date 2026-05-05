from flask import Flask, render_template, request, redirect, session
import sqlite3
import bcrypt
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "secret123"

# ================= ADMIN =================
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

# ================= DATABASE =================
def init_db():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password_hash BLOB
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS login_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        ip_address TEXT,
        status TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS blocked_ips (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ip_address TEXT UNIQUE,
        blocked_until TEXT
    )
    """)

    conn.commit()
    conn.close()

init_db()

# ================= HELPERS =================
def log_attempt(username, ip, status):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO login_attempts(username, ip_address, status) VALUES (?, ?, ?)",
        (username, ip, status)
    )
    conn.commit()
    conn.close()


def is_ip_blocked(ip):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT blocked_until FROM blocked_ips WHERE ip_address=?", (ip,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return False

    try:
        return datetime.fromisoformat(row[0]) > datetime.now()
    except:
        return False


def block_ip(ip):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    blocked_until = (datetime.now() + timedelta(minutes=15)).isoformat()

    cursor.execute("""
    INSERT OR REPLACE INTO blocked_ips(ip_address, blocked_until)
    VALUES (?, ?)
    """, (ip, blocked_until))

    conn.commit()
    conn.close()


def count_failed_user(username):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
    SELECT COUNT(*) FROM login_attempts
    WHERE username=? AND status='failed'
    AND datetime(timestamp) > datetime('now','-5 minutes')
    """, (username,))

    count = cursor.fetchone()[0]
    conn.close()
    return count


def count_failed_ip(ip):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
    SELECT COUNT(*) FROM login_attempts
    WHERE ip_address=? AND status='failed'
    AND datetime(timestamp) > datetime('now','-5 minutes')
    """, (ip,))

    count = cursor.fetchone()[0]
    conn.close()
    return count

# ================= ROUTES =================
@app.route("/")
def home():
    return redirect("/login")


# ================= LOGIN =================
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form["username"].strip()
        password = request.form["password"].strip()
        ip = request.remote_addr

        # ADMIN LOGIN
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session.clear()
            session["admin"] = True
            return redirect("/admin")

        # BLOCK CHECK
        if is_ip_blocked(ip):
            return "IP temporarily blocked"

        if count_failed_user(username) >= 5:
            return "Account locked"

        if count_failed_ip(ip) >= 5:
            block_ip(ip)
            return "IP blocked"

        # USER CHECK
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()

        cursor.execute("SELECT password_hash FROM users WHERE username=?", (username,))
        user = cursor.fetchone()
        conn.close()

        if not user:
            log_attempt(username, ip, "failed")
            return "User not found"

        if bcrypt.checkpw(password.encode(), user[0]):
            log_attempt(username, ip, "success")
            session.clear()
            session["user"] = username
            return redirect("/dashboard")

        log_attempt(username, ip, "failed")
        return "Invalid credentials"

    return render_template("login.html")


# ================= REGISTER =================
@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]
        confirm = request.form["confirm_password"]

        if password != confirm:
            return "Passwords do not match"

        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())

        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()

        try:
            cursor.execute(
                "INSERT INTO users(username, password_hash) VALUES (?, ?)",
                (username, hashed)
            )
            conn.commit()
        except:
            return "User already exists"
        finally:
            conn.close()

        return redirect("/login")

    return render_template("register.html")


# ================= DASHBOARD =================
@app.route("/dashboard")
def dashboard():
    if "user" in session and not session.get("admin"):
        return render_template("dashboard.html", user=session["user"])
    return redirect("/login")


# ================= ADMIN =================
@app.route("/admin")
def admin():

    if not session.get("admin"):
        return redirect("/login")

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    # LOGS
    cursor.execute("SELECT * FROM login_attempts ORDER BY id DESC")
    logs = cursor.fetchall()

    # BLOCKED IPS
    cursor.execute("SELECT * FROM blocked_ips")
    blocked = cursor.fetchall()

    # ================= ATTACK BURST PATTERN =================
    burst = {}
    for log in logs:
        minute = log[4][:16]  # YYYY-MM-DD HH:MM
        burst[minute] = burst.get(minute, 0) + 1

    sorted_burst = sorted(burst.items())[-10:]
    burst_labels = [b[0] for b in sorted_burst]
    burst_values = [b[1] for b in sorted_burst]

    # ================= USERNAME TARGETING HEAT =================
    cursor.execute("SELECT username FROM users")
    registered_users = set([row[0] for row in cursor.fetchall()])

    heat = {}
    for log in logs:
        if log[3] == "failed":
            user = log[1]
            heat[user] = heat.get(user, 0) + 1

    sorted_heat = sorted(heat.items(), key=lambda x: x[1], reverse=True)

    user_labels = [item[0] for item in sorted_heat[:10]]
    user_values = [item[1] for item in sorted_heat[:10]]

    user_colors = []
    for user in user_labels:
        if user in registered_users:
            user_colors.append("#198754")  # green
        else:
            user_colors.append("#dc3545")  # red

    conn.close()

    return render_template(
        "admin.html",
        logs=logs,
        blocked=blocked,
        sim_status=session.pop("sim_status", None),
        burst_labels=burst_labels,
        burst_values=burst_values,
        user_labels=user_labels,
        user_values=user_values,
        user_colors=user_colors
    )


# ================= BLOCK =================
@app.route("/block_ip/<ip>")
def block(ip):
    if not session.get("admin"):
        return redirect("/login")

    block_ip(ip)
    return redirect("/admin")


# ================= UNBLOCK =================
@app.route("/unblock_ip/<ip>")
def unblock(ip):
    if not session.get("admin"):
        return redirect("/login")

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM blocked_ips WHERE ip_address=?", (ip,))
    conn.commit()
    conn.close()

    return redirect("/admin")


# ================= SIMULATION =================
@app.route("/simulate_attack", methods=["POST"])
def simulate_attack():

    if not session.get("admin"):
        return redirect("/login")

    ip = request.form["target_ip"]

    for _ in range(5):
        log_attempt("sim_user", ip, "failed")

    if count_failed_ip(ip) >= 5:
        block_ip(ip)

    session["sim_status"] = f"Attack simulated for {ip}"

    return redirect("/admin")


# ================= LOGOUT =================
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ================= RUN =================
if __name__ == "__main__":
    app.run(debug=True)