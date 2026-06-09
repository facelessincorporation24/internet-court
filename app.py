from flask import Flask, render_template, request, redirect, jsonify
import sqlite3
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
# On Render with a persistent disk, set DATABASE_PATH=/var/data/database.db
# On the free tier (ephemeral), this just falls back to a local file
DATABASE = os.getenv("DATABASE_PATH", "database.db")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CASE_LIFETIME_HOURS = 24


def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database():
    conn = get_db_connection()
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r") as f:
        conn.executescript(f.read())
    # Add new columns to existing databases without breaking them
    for col, default in [
        ("react_lol",    "0"),
        ("react_rage",   "0"),
        ("react_shock",  "0"),
        ("react_salute", "0"),
        ("status",       "'open'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE cases ADD COLUMN {col} INTEGER DEFAULT {default}")
        except Exception:
            pass
    conn.commit()
    conn.close()


initialize_database()


def is_expired(case_row):
    """Return True if the case was created more than CASE_LIFETIME_HOURS ago."""
    raw = case_row["created_at"]
    if not raw:
        return False
    # SQLite stores timestamps as "YYYY-MM-DD HH:MM:SS" — parse it as UTC
    try:
        created = datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
    return age_hours >= CASE_LIFETIME_HOURS


def case_status(case_row):
    """'closed' if expired, otherwise the stored status (default 'open')."""
    if is_expired(case_row):
        return "closed"
    return case_row["status"] or "open"


def time_remaining(case_row):
    """Human-readable time left before closing, or None if already closed."""
    raw = case_row["created_at"]
    if not raw:
        return None
    try:
        created = datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    seconds_left = (CASE_LIFETIME_HOURS * 3600) - (datetime.now(timezone.utc) - created).total_seconds()
    if seconds_left <= 0:
        return None
    hours, rem = divmod(int(seconds_left), 3600)
    mins = rem // 60
    if hours > 0:
        return f"{hours}h {mins}m remaining"
    return f"{mins}m remaining"


# ── HOME ──────────────────────────────────────────────────────────────
@app.route("/")
def home():
    sort = request.args.get("sort", "recent")
    conn = get_db_connection()

    if sort == "hot":
        order = "(guilty_votes + not_guilty_votes + life_sentence_votes) DESC"
    elif sort == "controversial":
        # cases where the top two options are close
        order = "ABS(guilty_votes - not_guilty_votes) ASC, (guilty_votes + not_guilty_votes + life_sentence_votes) DESC"
    else:
        order = "created_at DESC"

    raw_cases = conn.execute(f"SELECT * FROM cases ORDER BY {order}").fetchall()
    overview  = conn.execute("""
        SELECT
            COUNT(*) AS total_cases,
            SUM(guilty_votes + not_guilty_votes + life_sentence_votes) AS total_votes
        FROM cases
    """).fetchone()
    conn.close()

    # Attach computed status so templates don't need to call Python helpers
    cases = []
    closed_count = 0
    for c in raw_cases:
        status = case_status(c)
        if status == "closed":
            closed_count += 1
        cases.append({"row": c, "status": status, "time_remaining": time_remaining(c)})

    stats = {
        "total_cases":  overview["total_cases"]  or 0,
        "total_votes":  overview["total_votes"]  or 0,
        "closed_cases": closed_count,
    }
    return render_template("index.html", cases=cases, sort=sort, stats=stats)


# ── CREATE ────────────────────────────────────────────────────────────
@app.route("/create", methods=["GET", "POST"])
def create():
    if request.method == "POST":
        plaintiff = request.form["plaintiff"]
        defendant = request.form["defendant"]
        charge    = request.form["charge"]
        evidence  = request.form["evidence"]

        conn = get_db_connection()
        conn.execute(
            "INSERT INTO cases (plaintiff, defendant, charge, evidence) VALUES (?, ?, ?, ?)",
            (plaintiff, defendant, charge, evidence)
        )
        conn.commit()
        conn.close()
        return redirect("/")

    return render_template("create.html")


# ── CASE DETAIL ───────────────────────────────────────────────────────
@app.route("/case/<int:case_id>")
def case_details(case_id):
    conn = get_db_connection()
    case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    conn.close()

    if case is None:
        return render_template("404.html"), 404

    status  = case_status(case)
    t_left  = time_remaining(case)
    return render_template(
        "case.html",
        case=case,
        comments=[],
        status=status,
        time_remaining=t_left,
        api_key=ANTHROPIC_API_KEY,
    )


# ── VOTE ──────────────────────────────────────────────────────────────
@app.route("/vote/<int:case_id>/<verdict>", methods=["POST"])
def vote(case_id, verdict):
    columns = {
        "guilty":        "guilty_votes",
        "not_guilty":    "not_guilty_votes",
        "life_sentence": "life_sentence_votes",
    }
    if verdict not in columns:
        return redirect(f"/case/{case_id}")

    col = columns[verdict]
    conn = get_db_connection()

    # Block votes on expired cases
    case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None or is_expired(case):
        conn.close()
        return redirect(f"/case/{case_id}")

    conn.execute(f"UPDATE cases SET {col} = {col} + 1 WHERE id = ?", (case_id,))
    conn.commit()
    conn.close()
    return redirect(f"/case/{case_id}")


# ── REACT ─────────────────────────────────────────────────────────────
@app.route("/react/<int:case_id>/<reaction>", methods=["POST"])
def react(case_id, reaction):
    valid = {"lol": "react_lol", "rage": "react_rage", "shock": "react_shock", "salute": "react_salute"}
    if reaction not in valid:
        return jsonify({"error": "invalid"}), 400

    col = valid[reaction]
    conn = get_db_connection()
    conn.execute(f"UPDATE cases SET {col} = {col} + 1 WHERE id = ?", (case_id,))
    conn.commit()
    row = conn.execute(f"SELECT {col} FROM cases WHERE id = ?", (case_id,)).fetchone()
    conn.close()
    return jsonify({"count": row[col]})


# ── STATS ─────────────────────────────────────────────────────────────
@app.route("/stats")
def stats():
    conn = get_db_connection()
    all_cases = conn.execute("SELECT * FROM cases").fetchall()
    overview_raw = conn.execute("""
        SELECT
            COUNT(*) AS total_cases,
            SUM(guilty_votes + not_guilty_votes + life_sentence_votes) AS total_votes,
            SUM(guilty_votes) AS total_guilty,
            SUM(not_guilty_votes) AS total_not_guilty,
            SUM(life_sentence_votes) AS total_life,
            SUM(react_lol + react_rage + react_shock + react_salute) AS total_reactions
        FROM cases
    """).fetchone()

    closed_count = sum(1 for c in all_cases if is_expired(c))

    overview = {
        "total_cases":     overview_raw["total_cases"]     or 0,
        "total_votes":     overview_raw["total_votes"]     or 0,
        "total_guilty":    overview_raw["total_guilty"]    or 0,
        "total_not_guilty":overview_raw["total_not_guilty"]or 0,
        "total_life":      overview_raw["total_life"]      or 0,
        "closed_cases":    closed_count,
        "total_reactions": overview_raw["total_reactions"] or 0,
    }

    hottest = conn.execute("""
        SELECT *, (guilty_votes + not_guilty_votes + life_sentence_votes) AS total
        FROM cases ORDER BY total DESC LIMIT 1
    """).fetchone()

    most_controversial = conn.execute("""
        SELECT *, (guilty_votes + not_guilty_votes + life_sentence_votes) AS total,
            ABS(guilty_votes - not_guilty_votes) AS spread
        FROM cases
        WHERE (guilty_votes + not_guilty_votes + life_sentence_votes) >= 3
        ORDER BY spread ASC, total DESC
        LIMIT 1
    """).fetchone()

    most_dramatic = conn.execute(
        "SELECT * FROM cases ORDER BY life_sentence_votes DESC LIMIT 1"
    ).fetchone()

    conn.close()
    return render_template("stats.html",
        overview=overview,
        hottest=hottest,
        most_controversial=most_controversial,
        most_dramatic=most_dramatic,
    )


# ── 404 ───────────────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404

@app.route("/wipe-database")
def wipe_database():
    conn = get_db_connection()
    conn.execute("DELETE FROM cases")
    conn.execute("DELETE FROM sqlite_sequence WHERE name='cases' ")
    conn.commit()
    conn.close()
    
    return "Database wiped"
if __name__ == "__main__":
    app.run()