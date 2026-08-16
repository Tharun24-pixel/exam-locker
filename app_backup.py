from flask import (
    Flask,
    render_template,
    request,
    redirect,
    session,
    flash,
    send_file,
    url_for
)
import sqlite3
import os
import io
import qrcode
from datetime import datetime

# --------------------------------------------------
# APP CONFIGURATION
# --------------------------------------------------

app = Flask(__name__)
app.secret_key = "examlock-secret-key-2026"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "examlock.db")

# Cloudflare public URL
CLOUDFLARE_URL = "https://athletic-shopper-shed-erik.trycloudflare.com"


# --------------------------------------------------
# DATABASE CONNECTION
# --------------------------------------------------

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------
# DATABASE INITIALIZATION
# --------------------------------------------------

def init_db():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id TEXT UNIQUE NOT NULL,
            exam TEXT NOT NULL,
            subject TEXT NOT NULL,
            status TEXT DEFAULT 'SECURED',
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id TEXT,
            action TEXT,
            username TEXT,
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT,
            severity TEXT DEFAULT 'LOW',
            created_at TEXT
        )
    """)

    # Demo paper
    existing = conn.execute(
        "SELECT * FROM papers WHERE paper_id=?",
        ("EX-2FA0427E",)
    ).fetchone()

    if not existing:

        conn.execute("""
            INSERT INTO papers
            (paper_id, exam, subject, status, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            "EX-2FA0427E",
            "SSLC",
            "DBMS",
            "SECURED",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

    conn.commit()
    conn.close()


# --------------------------------------------------
# LOGIN
# --------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if username == "admin" and password == "admin123":

            session["username"] = username
            session["role"] = "Administrator"

            return redirect(url_for("dashboard"))

        flash("Invalid username or password", "error")

    return render_template("login.html")


# --------------------------------------------------
# LOGOUT
# --------------------------------------------------

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))


# --------------------------------------------------
# DASHBOARD
# --------------------------------------------------

@app.route("/dashboard")
def dashboard():

    if "username" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    # All papers
    papers = conn.execute("""
        SELECT *
        FROM papers
        ORDER BY id DESC
    """).fetchall()

    # All logs
    logs = conn.execute("""
        SELECT *
        FROM logs
        ORDER BY id DESC
    """).fetchall()

    # Secured papers
    secured_papers = conn.execute("""
        SELECT COUNT(*)
        FROM papers
        WHERE status='SECURED'
    """).fetchone()[0]

    # QR verification count
    verified_scans = conn.execute("""
        SELECT COUNT(*)
        FROM logs
        WHERE action='QR Verification'
    """).fetchone()[0]

    # Active alerts
    active_alerts = conn.execute("""
        SELECT COUNT(*)
        FROM alerts
    """).fetchone()[0]

    # Total events
    total_events = conn.execute("""
        SELECT COUNT(*)
        FROM logs
    """).fetchone()[0]

    conn.close()

    return render_template(
        "dashboard.html",
        papers=papers,
        logs=logs,
        secured_papers=secured_papers,
        verified_scans=verified_scans,
        active_alerts=active_alerts,
        total_events=total_events,
        username=session["username"],
        role=session["role"]
    )


# --------------------------------------------------
# VERIFY PAPER
# --------------------------------------------------

@app.route("/verify/<paper_id>")
def verify_paper(paper_id):

    conn = get_db()

    paper = conn.execute("""
        SELECT *
        FROM papers
        WHERE paper_id=?
    """, (paper_id,)).fetchone()

    # --------------------------------------------------
    # PAPER NOT FOUND
    # --------------------------------------------------

    if paper is None:

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn.execute("""
            INSERT INTO alerts
            (message, severity, created_at)
            VALUES (?, ?, ?)
        """, (
            f"Unknown paper verification attempt: {paper_id}",
            "HIGH",
            now
        ))

        conn.execute("""
            INSERT INTO logs
            (paper_id, action, username, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            paper_id,
            "Failed Verification",
            session.get("username", "Guest"),
            now
        ))

        conn.commit()
        conn.close()

        return render_template(
            "dashboard.html",
            papers=[],
            logs=[],
            secured_papers=0,
            verified_scans=0,
            active_alerts=1,
            total_events=1,
            username=session.get("username", "Guest"),
            role=session.get("role", "User"),
            verification=None,
            error="Paper not found"
        )

    # --------------------------------------------------
    # SUCCESSFUL VERIFICATION
    # --------------------------------------------------

    conn.execute("""
        INSERT INTO logs
        (paper_id, action, username, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        paper_id,
        "QR Verification",
        session.get("username", "Guest"),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()

    # Get updated data
    papers = conn.execute("""
        SELECT *
        FROM papers
        ORDER BY id DESC
    """).fetchall()

    logs = conn.execute("""
        SELECT *
        FROM logs
        ORDER BY id DESC
    """).fetchall()

    secured_papers = conn.execute("""
        SELECT COUNT(*)
        FROM papers
        WHERE status='SECURED'
    """).fetchone()[0]

    verified_scans = conn.execute("""
        SELECT COUNT(*)
        FROM logs
        WHERE action='QR Verification'
    """).fetchone()[0]

    active_alerts = conn.execute("""
        SELECT COUNT(*)
        FROM alerts
    """).fetchone()[0]

    total_events = conn.execute("""
        SELECT COUNT(*)
        FROM logs
    """).fetchone()[0]

    conn.close()

    return render_template(
        "dashboard.html",
        papers=papers,
        logs=logs,
        secured_papers=secured_papers,
        verified_scans=verified_scans,
        active_alerts=active_alerts,
        total_events=total_events,
        username=session.get("username", "Guest"),
        role=session.get("role", "User"),
        verification=paper
    )


# --------------------------------------------------
# QR CODE GENERATOR
# --------------------------------------------------

@app.route("/qr/<paper_id>")
def generate_qr(paper_id):

    # Public verification URL
    verify_url = f"{CLOUDFLARE_URL}/verify/{paper_id}"

    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=4
    )

    qr.add_data(verify_url)
    qr.make(fit=True)

    image = qr.make_image(
        fill_color="black",
        back_color="white"
    )

    img = io.BytesIO()

    image.save(img, "PNG")

    img.seek(0)

    return send_file(
        img,
        mimetype="image/png",
        download_name=f"{paper_id}.png"
    )


# --------------------------------------------------
# CREATE NEW PAPER
# --------------------------------------------------

@app.route("/add-paper", methods=["POST"])
def add_paper():

    if "username" not in session:
        return redirect(url_for("login"))

    paper_id = request.form.get("paper_id", "").strip()
    exam = request.form.get("exam", "").strip()
    subject = request.form.get("subject", "").strip()

    # Validate fields
    if not paper_id or not exam or not subject:

        flash(
            "All fields are required",
            "error"
        )

        return redirect(url_for("dashboard"))

    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO papers
            (paper_id, exam, subject, status, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            paper_id,
            exam,
            subject,
            "SECURED",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

        conn.commit()

        flash(
            "Paper added successfully",
            "success"
        )

    except sqlite3.IntegrityError:

        flash(
            "Paper ID already exists",
            "error"
        )

    conn.close()

    return redirect(url_for("dashboard"))


# --------------------------------------------------
# RUN APPLICATION
# --------------------------------------------------

if __name__ == "__main__":

    # Initialize database
    init_db()

    print("")
    print("======================================")
    print("       EXAMLOCK SECURITY SYSTEM")
    print("======================================")
    print("Examination Security System")
    print("Local Server: http://127.0.0.1:5000")
    print("Cloudflare Tunnel ready")
    print("======================================")
    print("")

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )

