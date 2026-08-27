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


# ============================================================
# EXAMLOCK SECURITY SYSTEM
# ============================================================

app = Flask(__name__)

app.secret_key = "examlock-secret-key-2026"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "examlock.db")


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# DATABASE INITIALIZATION + MIGRATION
# ============================================================

def init_db():

    conn = get_db()

    # --------------------------------------------------------
    # PAPERS TABLE
    # --------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id TEXT UNIQUE NOT NULL,
            university TEXT DEFAULT '',
            exam TEXT NOT NULL,
            subject TEXT NOT NULL,
            status TEXT DEFAULT 'SECURED',
            created_at TEXT
        )
    """)

    # --------------------------------------------------------
    # CHECK OLD PAPERS TABLE COLUMNS
    # --------------------------------------------------------

    paper_columns = [
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(papers)"
        ).fetchall()
    ]

    # Add University to OLD database
    if "university" not in paper_columns:

        conn.execute("""
            ALTER TABLE papers
            ADD COLUMN university TEXT DEFAULT ''
        """)

    # Add created_at if old database doesn't have it
    if "created_at" not in paper_columns:

        conn.execute("""
            ALTER TABLE papers
            ADD COLUMN created_at TEXT
        """)

    # --------------------------------------------------------
    # LOGS TABLE
    # --------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id TEXT,
            action TEXT,
            username TEXT,
            created_at TEXT
        )
    """)

    # --------------------------------------------------------
    # FIX OLD LOGS DATABASE
    # --------------------------------------------------------

    log_columns = [
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(logs)"
        ).fetchall()
    ]

    if "created_at" not in log_columns:

        conn.execute("""
            ALTER TABLE logs
            ADD COLUMN created_at TEXT
        """)

    if "username" not in log_columns:

        conn.execute("""
            ALTER TABLE logs
            ADD COLUMN username TEXT
        """)

    if "action" not in log_columns:

        conn.execute("""
            ALTER TABLE logs
            ADD COLUMN action TEXT
        """)

    # --------------------------------------------------------
    # ALERTS TABLE
    # --------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT,
            severity TEXT DEFAULT 'LOW',
            created_at TEXT
        )
    """)

    # --------------------------------------------------------
    # DEMO PAPER
    # --------------------------------------------------------

    demo = conn.execute(
        """
        SELECT *
        FROM papers
        WHERE paper_id=?
        """,
        ("EX-2FA0427E",)
    ).fetchone()

    if demo is None:

        conn.execute("""
            INSERT INTO papers (
                paper_id,
                university,
                exam,
                subject,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "EX-2FA0427E",
            "Demo University",
            "SSLC",
            "DBMS",
            "SECURED",
            now()
        ))

    # --------------------------------------------------------
    # FIX EMPTY UNIVERSITY VALUES
    # --------------------------------------------------------

    conn.execute("""
        UPDATE papers
        SET university = 'Not Provided'
        WHERE university IS NULL
        OR university = ''
    """)

    # --------------------------------------------------------
    # COMMIT
    # --------------------------------------------------------

    conn.commit()
    conn.close()


# ============================================================
# INITIALIZE DATABASE
# IMPORTANT FOR RENDER / GUNICORN
# ============================================================

init_db()


# ============================================================
# LOGIN
# ============================================================

@app.route("/", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        ).strip()

        # ----------------------------------------------------
        # ADMIN LOGIN
        # ----------------------------------------------------

        if (
            username == "admin"
            and password in ["admin123", "Nexora"]
        ):

            session["username"] = username
            session["role"] = "Administrator"

            return redirect(
                url_for("dashboard")
            )

        flash(
            "Invalid username or password",
            "error"
        )

    return render_template(
        "login.html"
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# ============================================================
# ADMIN DASHBOARD
# ============================================================

@app.route("/dashboard")
def dashboard():

    if "username" not in session:

        return redirect(
            url_for("login")
        )

    conn = get_db()

    # --------------------------------------------------------
    # ALL PAPERS
    # --------------------------------------------------------

    papers = conn.execute("""
        SELECT
            id,
            paper_id,
            university,
            exam,
            subject,
            status,
            created_at
        FROM papers
        ORDER BY id DESC
    """).fetchall()

    # --------------------------------------------------------
    # RECENT VERIFICATION LOGS
    # --------------------------------------------------------

    logs = conn.execute("""
        SELECT
            id,
            paper_id,
            action,
            username,
            created_at
        FROM logs
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()

    # --------------------------------------------------------
    # SECURED PAPERS
    # --------------------------------------------------------

    secured_papers = conn.execute("""
        SELECT COUNT(*)
        FROM papers
        WHERE status = 'SECURED'
    """).fetchone()[0]

    # --------------------------------------------------------
    # QR VERIFICATIONS
    # --------------------------------------------------------

    verified_scans = conn.execute("""
        SELECT COUNT(*)
        FROM logs
        WHERE action = 'QR Verification'
    """).fetchone()[0]

    # --------------------------------------------------------
    # ACTIVE ALERTS
    # --------------------------------------------------------

    active_alerts = conn.execute("""
        SELECT COUNT(*)
        FROM alerts
    """).fetchone()[0]

    # --------------------------------------------------------
    # TOTAL EVENTS
    # --------------------------------------------------------

    total_events = conn.execute("""
        SELECT COUNT(*)
        FROM logs
    """).fetchone()[0]

    conn.close()

    # --------------------------------------------------------
    # SEND DATA TO DASHBOARD
    # --------------------------------------------------------

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


# ============================================================
# QR VERIFICATION
# ============================================================

@app.route("/verify/<paper_id>")
def verify_paper(paper_id):

    conn = get_db()

    paper = conn.execute("""
        SELECT
            id,
            paper_id,
            university,
            exam,
            subject,
            status,
            created_at
        FROM papers
        WHERE paper_id = ?
    """, (paper_id,)).fetchone()

    timestamp = now()

    # --------------------------------------------------------
    # PAPER NOT FOUND
    # --------------------------------------------------------

    if paper is None:

        # Create alert
        conn.execute("""
            INSERT INTO alerts (
                message,
                severity,
                created_at
            )
            VALUES (?, ?, ?)
        """, (
            f"Unknown paper verification attempt: {paper_id}",
            "HIGH",
            timestamp
        ))

        # Create failed verification log
        conn.execute("""
            INSERT INTO logs (
                paper_id,
                action,
                username,
                created_at
            )
            VALUES (?, ?, ?, ?)
        """, (
            paper_id,
            "Failed Verification",
            "Exam Center",
            timestamp
        ))

        conn.commit()
        conn.close()

        return render_template(
            "verification.html",
            paper=None,
            error="Paper not found"
        ), 404

    # --------------------------------------------------------
    # SUCCESSFUL VERIFICATION
    # --------------------------------------------------------

    conn.execute("""
        INSERT INTO logs (
            paper_id,
            action,
            username,
            created_at
        )
        VALUES (?, ?, ?, ?)
    """, (
        paper_id,
        "QR Verification",
        "Exam Center",
        timestamp
    ))

    conn.commit()
    conn.close()

    # --------------------------------------------------------
    # SHOW ONLY SCANNED PAPER
    # --------------------------------------------------------

    return render_template(
        "verification.html",
        paper=paper,
        error=None
    )


# ============================================================
# QR CODE GENERATOR
# ============================================================

@app.route("/qr/<paper_id>")
def generate_qr(paper_id):

    conn = get_db()

    paper = conn.execute("""
        SELECT *
        FROM papers
        WHERE paper_id = ?
    """, (paper_id,)).fetchone()

    conn.close()

    if paper is None:

        return "Paper not found", 404

    # --------------------------------------------------------
    # CURRENT WEBSITE URL
    # --------------------------------------------------------

    base_url = request.host_url.rstrip("/")

    verify_url = (
        f"{base_url}/verify/{paper_id}"
    )

    # --------------------------------------------------------
    # CREATE QR
    # --------------------------------------------------------

    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=4
    )

    qr.add_data(verify_url)

    qr.make(
        fit=True
    )

    image = qr.make_image(
        fill_color="black",
        back_color="white"
    )

    # --------------------------------------------------------
    # SAVE QR TO MEMORY
    # --------------------------------------------------------

    buffer = io.BytesIO()

    image.save(
        buffer,
        format="PNG"
    )

    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="image/png",
        download_name=f"{paper_id}.png"
    )


# ============================================================
# ADD / SECURE NEW PAPER
# ============================================================

@app.route(
    "/add-paper",
    methods=["POST"]
)
def add_paper():

    # --------------------------------------------------------
    # LOGIN CHECK
    # --------------------------------------------------------

    if "username" not in session:

        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------
    # GET FORM DATA
    # --------------------------------------------------------

    paper_id = request.form.get(
        "paper_id",
        ""
    ).strip()

    university = request.form.get(
        "university",
        ""
    ).strip()

    exam = request.form.get(
        "exam",
        ""
    ).strip()

    subject = request.form.get(
        "subject",
        ""
    ).strip()

    # --------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------

    if (
        not paper_id
        or not university
        or not exam
        or not subject
    ):

        flash(
            "Paper ID, University, Exam and Subject are required",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO papers (
                paper_id,
                university,
                exam,
                subject,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            paper_id,
            university,
            exam,
            subject,
            "SECURED",
            now()
        ))

        # ----------------------------------------------------
        # ADD EVENT LOG
        # ----------------------------------------------------

        conn.execute("""
            INSERT INTO logs (
                paper_id,
                action,
                username,
                created_at
            )
            VALUES (?, ?, ?, ?)
        """, (
            paper_id,
            "Paper Secured",
            session["username"],
            now()
        ))

        conn.commit()

        flash(
            "Paper secured successfully",
            "success"
        )

    except sqlite3.IntegrityError:

        flash(
            "Paper ID already exists",
            "error"
        )

    except Exception as e:

        conn.rollback()

        flash(
            f"Database error: {str(e)}",
            "error"
        )

    finally:

        conn.close()

    return redirect(
        url_for("dashboard")
    )


# ============================================================
# OLD CREATE-PAPER SUPPORT
# ============================================================

@app.route(
    "/create-paper",
    methods=["POST"]
)
def create_paper():

    return add_paper()


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    return "ExamLock is running successfully."


# ============================================================
# SERVER
# ============================================================

if __name__ == "__main__":

    print("")
    print("======================================")
    print("       EXAMLOCK SECURITY SYSTEM")
    print("======================================")
    print("Examination Security System")
    print("Local Server: http://127.0.0.1:5000")
    print("Admin Login: admin / admin123")
    print("======================================")
    print("")

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
