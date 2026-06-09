from flask import Flask, render_template, request, redirect, jsonify
import psycopg2
import psycopg2.extras
import os
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
DATABASE_URL      = os.getenv("DATABASE_URL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CASE_LIFETIME_HOURS = 24
UPLOAD_FOLDER     = os.getenv("UPLOAD_FOLDER", os.path.join(os.path.dirname(__file__), "static", "uploads"))
MAX_FILES         = 3
ALLOWED_IMAGE     = {"jpg", "jpeg", "png", "gif", "webp"}
ALLOWED_VIDEO     = {"mp4", "webm", "mov"}
ALLOWED_EXT       = ALLOWED_IMAGE | ALLOWED_VIDEO

CATEGORIES = [
    ("roommate",  "🏠 Roommate Drama"),
    ("food",      "🍕 Food Crime"),
    ("betrayal",  "🗡 Betrayal"),
    ("workplace", "💼 Workplace"),
    ("family",    "👨‍👩‍👧 Family"),
    ("online",    "💻 Online"),
    ("noise",     "🔊 Noise Complaint"),
    ("money",     "💸 Money"),
    ("other",     "⚖ Other"),
]

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ── DB CONNECTION ─────────────────────────────────────────────────────
def get_db_connection():
    # Render sets DATABASE_URL with postgres:// — psycopg2 needs postgresql://
    url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    conn = psycopg2.connect(url)
    return conn


def initialize_database():
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cases (
            id               SERIAL PRIMARY KEY,
            plaintiff        TEXT NOT NULL,
            defendant        TEXT NOT NULL,
            charge           TEXT NOT NULL,
            evidence         TEXT NOT NULL,
            category         TEXT DEFAULT 'other',
            guilty_votes     INTEGER DEFAULT 0,
            not_guilty_votes INTEGER DEFAULT 0,
            life_sentence_votes INTEGER DEFAULT 0,
            react_lol        INTEGER DEFAULT 0,
            react_rage       INTEGER DEFAULT 0,
            react_shock      INTEGER DEFAULT 0,
            react_salute     INTEGER DEFAULT 0,
            defendant_reply  TEXT DEFAULT NULL,
            status           TEXT DEFAULT 'open',
            created_at       TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS case_media (
            id          SERIAL PRIMARY KEY,
            case_id     INTEGER NOT NULL REFERENCES cases(id),
            filename    TEXT NOT NULL,
            media_type  TEXT NOT NULL,
            uploaded_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


initialize_database()


# ── HELPERS ──────────────────────────────────────────────────────────
def row_to_dict(row, cursor):
    """Convert a psycopg2 row to a dict using cursor description."""
    if row is None:
        return None
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def rows_to_dicts(rows, cursor):
    if not rows:
        return []
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def file_media_type(filename):
    ext = filename.rsplit(".", 1)[1].lower()
    return "video" if ext in ALLOWED_VIDEO else "image"

def is_expired(case):
    """Works with both dicts and dict-like objects."""
    raw = case["created_at"] if isinstance(case, dict) else case.get("created_at")
    if not raw:
        return False
    if isinstance(raw, datetime):
        created = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    else:
        try:
            created = datetime.strptime(str(raw)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return False
    return (datetime.now(timezone.utc) - created).total_seconds() / 3600 >= CASE_LIFETIME_HOURS

def case_status(case):
    return "closed" if is_expired(case) else (case.get("status") or "open")

def time_remaining(case):
    raw = case["created_at"] if isinstance(case, dict) else case.get("created_at")
    if not raw:
        return None
    if isinstance(raw, datetime):
        created = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    else:
        try:
            created = datetime.strptime(str(raw)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    seconds_left = (CASE_LIFETIME_HOURS * 3600) - (datetime.now(timezone.utc) - created).total_seconds()
    if seconds_left <= 0:
        return None
    hours, rem = divmod(int(seconds_left), 3600)
    mins = rem // 60
    return f"{hours}h {mins}m remaining" if hours > 0 else f"{mins}m remaining"

def category_label(key):
    return next((label for k, label in CATEGORIES if k == key), "⚖ Other")


# ── HOME ─────────────────────────────────────────────────────────────
@app.route("/")
def home():
    sort     = request.args.get("sort", "recent")
    cat      = request.args.get("cat", "")
    search_q = request.args.get("q", "").strip()

    if sort == "hot":
        order = "(guilty_votes + not_guilty_votes + life_sentence_votes) DESC"
    elif sort == "controversial":
        order = "ABS(guilty_votes - not_guilty_votes) ASC, (guilty_votes + not_guilty_votes + life_sentence_votes) DESC"
    else:
        order = "created_at DESC"

    where_parts, params = [], []
    if cat:
        where_parts.append("category = %s")
        params.append(cat)
    if search_q:
        where_parts.append("(plaintiff ILIKE %s OR defendant ILIKE %s OR charge ILIKE %s)")
        params += [f"%{search_q}%"] * 3

    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute(f"SELECT * FROM cases {where_sql} ORDER BY {order}", params)
    raw_cases = rows_to_dicts(cur.fetchall(), cur)

    cur.execute("SELECT COUNT(*) AS total_cases, SUM(guilty_votes+not_guilty_votes+life_sentence_votes) AS total_votes FROM cases")
    ov = row_to_dict(cur.fetchone(), cur)
    cur.close()
    conn.close()

    cases, closed_count = [], 0
    for c in raw_cases:
        s = case_status(c)
        if s == "closed":
            closed_count += 1
        cases.append({"row": c, "status": s, "time_remaining": time_remaining(c),
                      "cat_label": category_label(c.get("category") or "other")})

    stats = {
        "total_cases":  ov["total_cases"]  or 0,
        "total_votes":  ov["total_votes"]  or 0,
        "closed_cases": closed_count,
    }
    return render_template("index.html", cases=cases, sort=sort, stats=stats,
                           categories=CATEGORIES, active_cat=cat, search_q=search_q)


# ── CREATE ───────────────────────────────────────────────────────────
@app.route("/create", methods=["GET", "POST"])
def create():
    if request.method == "POST":
        plaintiff = request.form["plaintiff"]
        defendant = request.form["defendant"]
        charge    = request.form["charge"]
        evidence  = request.form["evidence"]
        category  = request.form.get("category", "other")
        if category not in [k for k, _ in CATEGORIES]:
            category = "other"

        conn = get_db_connection()
        cur  = conn.cursor()
        cur.execute(
            "INSERT INTO cases (plaintiff, defendant, charge, evidence, category) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (plaintiff, defendant, charge, evidence, category)
        )
        case_id = cur.fetchone()[0]

        files = request.files.getlist("media")
        saved = 0
        for f in files:
            if saved >= MAX_FILES:
                break
            if not f or not f.filename or not allowed_file(f.filename):
                continue
            ext   = f.filename.rsplit(".", 1)[1].lower()
            fname = f"{uuid.uuid4().hex}.{ext}"
            f.save(os.path.join(UPLOAD_FOLDER, fname))
            cur.execute(
                "INSERT INTO case_media (case_id, filename, media_type) VALUES (%s,%s,%s)",
                (case_id, fname, file_media_type(f.filename))
            )
            saved += 1

        conn.commit()
        cur.close()
        conn.close()
        return redirect(f"/case/{case_id}")

    return render_template("create.html", categories=CATEGORIES)


# ── CASE DETAIL ──────────────────────────────────────────────────────
@app.route("/case/<int:case_id>")
def case_details(case_id):
    conn = get_db_connection()
    cur  = conn.cursor()

    cur.execute("SELECT * FROM cases WHERE id = %s", (case_id,))
    case = row_to_dict(cur.fetchone(), cur)
    if case is None:
        cur.close(); conn.close()
        return render_template("404.html"), 404

    cur.execute("SELECT * FROM case_media WHERE case_id = %s ORDER BY uploaded_at ASC", (case_id,))
    media = rows_to_dicts(cur.fetchall(), cur)
    cur.close()
    conn.close()

    return render_template(
        "case.html",
        case=case,
        media=media,
        comments=[],
        status=case_status(case),
        time_remaining=time_remaining(case),
        api_key=ANTHROPIC_API_KEY,
        cat_label=category_label(case.get("category") or "other"),
    )


# ── VOTE ─────────────────────────────────────────────────────────────
@app.route("/vote/<int:case_id>/<verdict>", methods=["POST"])
def vote(case_id, verdict):
    columns = {"guilty": "guilty_votes", "not_guilty": "not_guilty_votes", "life_sentence": "life_sentence_votes"}
    if verdict not in columns:
        return redirect(f"/case/{case_id}")

    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM cases WHERE id = %s", (case_id,))
    case = row_to_dict(cur.fetchone(), cur)

    if case and not is_expired(case):
        col = columns[verdict]
        cur.execute(f"UPDATE cases SET {col} = {col} + 1 WHERE id = %s", (case_id,))
        conn.commit()

    cur.close()
    conn.close()
    return redirect(f"/case/{case_id}")


# ── REACT ────────────────────────────────────────────────────────────
@app.route("/react/<int:case_id>/<reaction>", methods=["POST"])
def react(case_id, reaction):
    valid = {"lol": "react_lol", "rage": "react_rage", "shock": "react_shock", "salute": "react_salute"}
    if reaction not in valid:
        return jsonify({"error": "invalid"}), 400

    col  = valid[reaction]
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute(f"UPDATE cases SET {col} = {col} + 1 WHERE id = %s", (case_id,))
    conn.commit()
    cur.execute(f"SELECT {col} FROM cases WHERE id = %s", (case_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return jsonify({"count": row[0] if row else 0})


# ── DEFENDANT REPLY ──────────────────────────────────────────────────
@app.route("/reply/<int:case_id>", methods=["POST"])
def defendant_reply(case_id):
    reply = request.form.get("reply", "").strip()
    if not reply:
        return redirect(f"/case/{case_id}")

    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("SELECT defendant_reply, created_at FROM cases WHERE id = %s", (case_id,))
    row  = cur.fetchone()

    if row and row[0] is None:
        case_stub = {"defendant_reply": row[0], "created_at": row[1]}
        if not is_expired(case_stub):
            cur.execute("UPDATE cases SET defendant_reply = %s WHERE id = %s", (reply[:1000], case_id))
            conn.commit()

    cur.close()
    conn.close()
    return redirect(f"/case/{case_id}")


# ── STATS ────────────────────────────────────────────────────────────
@app.route("/stats")
def stats():
    conn = get_db_connection()
    cur  = conn.cursor()

    cur.execute("SELECT * FROM cases")
    all_cases = rows_to_dicts(cur.fetchall(), cur)

    cur.execute("""
        SELECT COUNT(*) AS total_cases,
            SUM(guilty_votes+not_guilty_votes+life_sentence_votes) AS total_votes,
            SUM(guilty_votes) AS total_guilty,
            SUM(not_guilty_votes) AS total_not_guilty,
            SUM(life_sentence_votes) AS total_life,
            SUM(react_lol+react_rage+react_shock+react_salute) AS total_reactions
        FROM cases
    """)
    ov_raw = row_to_dict(cur.fetchone(), cur)

    cur.execute("""
        SELECT *, (guilty_votes+not_guilty_votes+life_sentence_votes) AS total
        FROM cases ORDER BY total DESC LIMIT 1
    """)
    hottest = row_to_dict(cur.fetchone(), cur)

    cur.execute("""
        SELECT *, (guilty_votes+not_guilty_votes+life_sentence_votes) AS total,
            ABS(guilty_votes-not_guilty_votes) AS spread
        FROM cases WHERE (guilty_votes+not_guilty_votes+life_sentence_votes) >= 3
        ORDER BY spread ASC, total DESC LIMIT 1
    """)
    most_controversial = row_to_dict(cur.fetchone(), cur)

    cur.execute("SELECT * FROM cases ORDER BY life_sentence_votes DESC LIMIT 1")
    most_dramatic = row_to_dict(cur.fetchone(), cur)

    cur.close()
    conn.close()

    closed_count = sum(1 for c in all_cases if is_expired(c))
    overview = {
        "total_cases":      ov_raw["total_cases"]      or 0,
        "total_votes":      ov_raw["total_votes"]      or 0,
        "total_guilty":     ov_raw["total_guilty"]     or 0,
        "total_not_guilty": ov_raw["total_not_guilty"] or 0,
        "total_life":       ov_raw["total_life"]       or 0,
        "closed_cases":     closed_count,
        "total_reactions":  ov_raw["total_reactions"]  or 0,
    }
    return render_template("stats.html", overview=overview, hottest=hottest,
        most_controversial=most_controversial, most_dramatic=most_dramatic)


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(debug=True)