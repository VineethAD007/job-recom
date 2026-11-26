"""Flask API + static file server for the job recommendation website.

Run:  python backend/app.py      then open http://localhost:8000
"""
import json
import math
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from datetime import date
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

from recommender import Recommender, _norm

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DATABASE = Path(__file__).resolve().parent / "data" / "users.db"

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    DATABASE=os.environ.get("DATABASE_PATH", str(DATABASE)),
    DATABASE_BACKEND=os.environ.get("DATABASE_BACKEND", "sqlite").strip().lower(),
    TURSO_DATABASE_URL=os.environ.get("TURSO_DATABASE_URL", ""),
    TURSO_AUTH_TOKEN=os.environ.get("TURSO_AUTH_TOKEN", ""),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "").lower() in {"1", "true", "yes"},
    PERMANENT_SESSION_LIFETIME=int(os.environ.get("SESSION_LIFETIME_SECONDS", "28800")),
    MAX_CONTENT_LENGTH=64 * 1024,
)

_request_counts = defaultdict(list)
_csrf_token = "csrf_token"


def _database_connection():
    backend = app.config["DATABASE_BACKEND"]
    if backend == "sqlite":
        connection = sqlite3.connect(app.config["DATABASE"])
        connection.row_factory = sqlite3.Row
    elif backend == "turso":
        database_url = app.config["TURSO_DATABASE_URL"]
        auth_token = app.config["TURSO_AUTH_TOKEN"]
        if not database_url or not auth_token:
            raise RuntimeError(
                "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are required for the Turso backend."
            )
        try:
            import turso_serverless
        except ImportError as exc:
            raise RuntimeError(
                "Install turso-serverless to use the Turso database backend."
            ) from exc
        connection = turso_serverless.connect(database_url, auth_token=auth_token)
        connection.row_factory = turso_serverless.Row
    else:
        raise RuntimeError("DATABASE_BACKEND must be either 'sqlite' or 'turso'.")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _database_integrity_errors():
    if app.config["DATABASE_BACKEND"] == "turso":
        import turso_serverless

        return (sqlite3.IntegrityError, turso_serverless.IntegrityError)
    return (sqlite3.IntegrityError,)


def init_db():
    if app.config["DATABASE_BACKEND"] == "sqlite":
        database = Path(app.config["DATABASE"])
        database.parent.mkdir(parents=True, exist_ok=True)
    with closing(_database_connection()) as db, db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS job_interactions (
                user_id INTEGER NOT NULL,
                job_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, job_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                CHECK(status IN ('saved', 'dismissed', 'applied'))
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                job_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                application_date TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, job_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                CHECK(status IN ('Interested', 'Applied', 'Assessment', 'Interview', 'Offered', 'Rejected'))
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS roadmap_preferences (
                user_id INTEGER PRIMARY KEY,
                job_ids TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_learning_progress (
                user_id INTEGER NOT NULL,
                skill TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, skill),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                CHECK(status IN ('learning', 'completed'))
            )
            """
        )


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return jsonify(error="Please sign in to continue."), 401
        return view(*args, **kwargs)

    return wrapped


init_db()


def _rate_limited(key, limit=20, window=60):
    now = time.monotonic()
    timestamps = _request_counts[key]
    timestamps[:] = [timestamp for timestamp in timestamps if now - timestamp < window]
    if len(timestamps) >= limit:
        return True
    timestamps.append(now)
    return False


@app.before_request
def validate_mutating_requests():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if _csrf_token not in session:
        session[_csrf_token] = secrets.token_urlsafe(32)
    supplied = request.headers.get("X-CSRF-Token")
    if request.headers.get("Origin") is None:
        return None
    if not supplied or not secrets.compare_digest(supplied, session[_csrf_token]):
        return jsonify(error="Invalid or missing CSRF token."), 403
    return None


@app.after_request
def add_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; font-src https://fonts.gstatic.com; img-src 'self' data:"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(error="Request body is too large."), 413


@app.errorhandler(404)
def api_not_found(error):
    if request.path.startswith("/api/"):
        return jsonify(error="API endpoint not found."), 404
    return error


@app.after_request
def add_cors_headers(resp):
    """Lets the frontend work when opened with VS Code Live Server (port 5500)."""
    origin = request.headers.get("Origin")
    if origin in {"http://localhost:5500", "http://127.0.0.1:5500"}:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        resp.headers["Vary"] = "Origin"
    return resp


@app.route("/api/<path:_name>", methods=["OPTIONS"])
def api_preflight(_name):
    return "", 204


@app.get("/api/csrf")
def csrf_token():
    if _csrf_token not in session:
        session[_csrf_token] = secrets.token_urlsafe(32)
    return jsonify(token=session[_csrf_token])


@app.post("/api/register")
def register():
    if _rate_limited("register:" + request.remote_addr, limit=30, window=300):
        return jsonify(error="Too many registration attempts. Please try again later."), 429
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send a JSON body."), 400

    email = body.get("email")
    password = body.get("password")
    if (
        not isinstance(email, str)
        or len(email.strip()) > 254
        or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip())
    ):
        return jsonify(error="Enter a valid email address."), 400
    if not isinstance(password, str) or len(password) < 8 or len(password) > 128:
        return jsonify(error="Password must be between 8 and 128 characters."), 400

    email = email.strip().lower()
    try:
        with closing(_database_connection()) as db, db:
            cursor = db.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                (email, generate_password_hash(password)),
            )
            user_id = cursor.lastrowid
    except _database_integrity_errors():
        return jsonify(error="An account with that email already exists."), 409

    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    session["email"] = email
    return jsonify(email=email), 201


@app.post("/api/login")
def login():
    if _rate_limited("login:" + request.remote_addr, limit=12, window=300):
        return jsonify(error="Too many login attempts. Please try again later."), 429
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send a JSON body."), 400
    email = body.get("email")
    password = body.get("password")
    if (
        not isinstance(email, str)
        or len(email.strip()) > 254
        or not isinstance(password, str)
        or len(password) > 128
    ):
        return jsonify(error="Enter your email and password."), 400

    with closing(_database_connection()) as db:
        user = db.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
    if user is None or not check_password_hash(user[2], password):
        return jsonify(error="Email or password is incorrect."), 401

    session.clear()
    session.permanent = True
    session["user_id"] = user[0]
    session["email"] = user[1]
    return jsonify(email=user[1])


@app.get("/api/session")
def session_status():
    if "user_id" not in session:
        return jsonify(authenticated=False)
    return jsonify(authenticated=True, email=session["email"])


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(status="ok")

engine = Recommender()
VALID_MODES = {"Any", "Onsite", "Hybrid", "Remote"}


def _to_int(value, default, lo, hi):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _is_integer_input(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value) and value.is_integer()
    return isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()) is not None


APPLICATION_STATUSES = ("Interested", "Applied", "Assessment", "Interview", "Offered", "Rejected")
ROADMAP_STATUSES = {"planned", "learning", "completed"}

ROADMAP_PREREQUISITES = {
    "machine learning": ("NumPy", "Pandas", "Statistics"),
    "tensorflow": ("Machine Learning",),
    "pytorch": ("Machine Learning",),
    "nlp": ("Machine Learning",),
    "transformers": ("NLP",),
    "react": ("JavaScript", "HTML/CSS"),
    "node.js": ("JavaScript",),
    "spring boot": ("Java",),
    "docker": ("Linux",),
    "kubernetes": ("Docker",),
    "ci/cd": ("Git",),
    "terraform": ("AWS",),
    "aws": ("Linux", "Networking"),
    "power bi": ("SQL",),
    "android": ("Kotlin",),
    "opencv": ("Python", "NumPy"),
    "data visualization": ("Statistics",),
}

ROADMAP_BEGINNER_SKILLS = {
    "python", "sql", "statistics", "numpy", "pandas", "java", "javascript",
    "html/css", "data structures", "git", "linux", "networking", "excel",
    "kotlin",
}
ROADMAP_ADVANCED_SKILLS = {
    "pytorch", "tensorflow", "transformers", "kubernetes", "microservices",
    "security",
}

ROADMAP_HOURS = {
    "python": 40,
    "sql": 20,
    "statistics": 30,
    "numpy": 15,
    "pandas": 20,
    "machine learning": 60,
    "tensorflow": 30,
    "pytorch": 30,
    "data visualization": 18,
    "aws": 35,
    "kubernetes": 30,
}

FREE_LEARNING_RESOURCES = {
    "python": {
        "title": "The Python Tutorial",
        "url": "https://docs.python.org/3/tutorial/",
        "provider": "Python documentation",
    },
    "statistics": {
        "title": "OpenIntro Statistics (free textbook)",
        "url": "https://www.openintro.org/book/os/",
        "provider": "OpenIntro",
    },
    "numpy": {
        "title": "NumPy learning resources",
        "url": "https://numpy.org/learn/",
        "provider": "NumPy",
    },
    "pandas": {
        "title": "Getting started tutorials",
        "url": "https://pandas.pydata.org/docs/getting_started/intro_tutorials/",
        "provider": "pandas",
    },
    "sql": {
        "title": "Interactive SQL lessons",
        "url": "https://sqlbolt.com/",
        "provider": "SQLBolt",
    },
    "machine learning": {
        "title": "Machine Learning Crash Course",
        "url": "https://developers.google.com/machine-learning/crash-course",
        "provider": "Google",
    },
    "tensorflow": {
        "title": "TensorFlow tutorials",
        "url": "https://www.tensorflow.org/tutorials",
        "provider": "TensorFlow",
    },
    "aws": {
        "title": "Free digital training",
        "url": "https://aws.amazon.com/training/digital/",
        "provider": "AWS Skill Builder",
    },
    "data visualization": {
        "title": "Matplotlib tutorials",
        "url": "https://matplotlib.org/stable/tutorials/index.html",
        "provider": "Matplotlib",
    },
    "linux": {
        "title": "Linux Journey",
        "url": "https://linuxjourney.com/",
        "provider": "Linux Journey",
    },
    "javascript": {
        "title": "JavaScript Guide",
        "url": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide",
        "provider": "MDN",
    },
    "react": {
        "title": "Quick Start",
        "url": "https://react.dev/learn",
        "provider": "React",
    },
    "docker": {
        "title": "Docker Learn",
        "url": "https://docs.docker.com/learn/",
        "provider": "Docker",
    },
    "git": {
        "title": "Pro Git book",
        "url": "https://git-scm.com/book/en/v2",
        "provider": "Git",
    },
    "power bi": {
        "title": "Power BI training",
        "url": "https://learn.microsoft.com/en-us/training/powerplatform/power-bi",
        "provider": "Microsoft Learn",
    },
}


def _valid_application_date(value):
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _application_json(row):
    application = dict(row)
    job = next((item for item in engine.jobs if item["id"] == application["job_id"]), None)
    if job is not None:
        application["job"] = job
    return application


def _read_user_profile(user_id):
    with closing(_database_connection()) as db:
        row = db.execute(
            "SELECT data FROM profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
    if row is None:
        return {}
    try:
        profile = json.loads(row["data"])
    except (json.JSONDecodeError, TypeError):
        return {}
    return profile if isinstance(profile, dict) else {}


def _roadmap_skill_labels():
    labels = {_norm(skill): skill for skill in engine.all_skills()}
    for parent, prerequisites in ROADMAP_PREREQUISITES.items():
        labels.setdefault(parent, parent.title())
        labels.update({_norm(skill): skill for skill in prerequisites})
    return labels


def _roadmap_response(job_ids, skills, progress):
    selected_jobs = [job for job in engine.jobs if job["id"] in job_ids]
    normalized_skills = {_norm(skill) for skill in skills if _norm(skill)}
    labels = _roadmap_skill_labels()
    entries = {}

    def add_entry(
        skill,
        required_for=(),
        preferred_for=(),
        prerequisite_for=(),
        prerequisite_weight=0,
    ):
        normalized = _norm(skill)
        if not normalized:
            return
        entry = entries.setdefault(normalized, {
            "skill": labels.get(normalized, skill),
            "required_for": set(),
            "preferred_for": set(),
            "prerequisite_for": set(),
            "prerequisite_weight": 0,
        })
        entry["required_for"].update(required_for)
        entry["preferred_for"].update(preferred_for)
        entry["prerequisite_for"].update(prerequisite_for)
        entry["prerequisite_weight"] = max(
            entry["prerequisite_weight"], prerequisite_weight
        )

    missing_roots = []
    for job in selected_jobs:
        for skill in job["required"]:
            normalized = _norm(skill)
            if normalized not in normalized_skills:
                add_entry(skill, required_for=(job["title"],))
                missing_roots.append((skill, "required"))
            elif progress.get(normalized) in {"learning", "completed"}:
                add_entry(skill, required_for=(job["title"],))
        for skill in job["nice"]:
            normalized = _norm(skill)
            if normalized not in normalized_skills:
                add_entry(skill, preferred_for=(job["title"],))
                missing_roots.append((skill, "preferred"))
            elif progress.get(normalized) in {"learning", "completed"}:
                add_entry(skill, preferred_for=(job["title"],))

    def add_prerequisites(skill, visited, parent_weight):
        normalized = _norm(skill)
        if normalized in visited:
            return
        visited.add(normalized)
        for prerequisite in ROADMAP_PREREQUISITES.get(normalized, ()):
            prerequisite_norm = _norm(prerequisite)
            if prerequisite_norm not in normalized_skills or progress.get(prerequisite_norm) in {"learning", "completed"}:
                add_entry(
                    prerequisite,
                    prerequisite_for=(skill,),
                    prerequisite_weight=parent_weight * 0.75,
                )
            add_prerequisites(prerequisite, visited, parent_weight * 0.75)

    for skill, requirement_type in missing_roots:
        add_prerequisites(skill, set(), 3 if requirement_type == "required" else 1)

    visible_skills = {
        _norm(skill)
        for job in selected_jobs
        for skill in job["required"] + job["nice"]
    }
    for normalized, status in progress.items():
        if status in {"learning", "completed"} and (
            normalized in visible_skills or normalized in entries
        ):
            add_entry(labels.get(normalized, normalized.title()))

    recommendations = engine.recommend(
        skills=skills,
        experience=0,
        city="Anywhere",
        mode="Any",
        min_salary=0,
        preferred_mode="Any",
    )
    recommendations_by_id = {job["id"]: job for job in recommendations}
    roles = []
    for job in selected_jobs:
        match = recommendations_by_id[job["id"]]
        matched_count = sum(_norm(skill) in normalized_skills for skill in job["required"])
        required_count = len(job["required"])
        roles.append({
            "id": job["id"],
            "title": job["title"],
            "company": job["company"],
            "compatibility": round(match["parts"]["skills"] * 100),
            "matched_required": matched_count,
            "required_count": required_count,
            "required_coverage": round(100 * matched_count / required_count) if required_count else 100,
        })

    for normalized, entry in entries.items():
        is_direct = bool(entry["required_for"] or entry["preferred_for"])
        direct_weight = 3 * len(entry["required_for"]) + len(entry["preferred_for"])
        entry["priority_weight"] = max(
            direct_weight, entry["prerequisite_weight"]
        )
        entry["is_prerequisite"] = not is_direct and bool(entry["prerequisite_for"])
        entry["status"] = progress.get(normalized, "planned")
        entry["stage"] = (
            "Beginner" if normalized in ROADMAP_BEGINNER_SKILLS
            else "Advanced" if normalized in ROADMAP_ADVANCED_SKILLS
            else "Intermediate"
        )
        entry["estimated_hours"] = ROADMAP_HOURS.get(normalized, 20)
        entry["resource"] = FREE_LEARNING_RESOURCES.get(normalized)

    max_priority = max((entry["priority_weight"] for entry in entries.values()), default=0)
    steps = []
    for normalized, entry in entries.items():
        required_for = sorted(entry["required_for"])
        preferred_for = sorted(entry["preferred_for"])
        prerequisite_for = sorted(entry["prerequisite_for"])
        if entry["is_prerequisite"]:
            reason = "Suggested prerequisite for " + ", ".join(prerequisite_for) + "."
        elif required_for:
            reason = "Required by " + ", ".join(required_for) + " in the selected job data."
        else:
            reason = "Preferred by " + ", ".join(preferred_for) + " in the selected job data."
        steps.append({
            "skill": entry["skill"],
            "stage": entry["stage"],
            "status": entry["status"],
            "priority": round(100 * entry["priority_weight"] / max_priority) if max_priority else 0,
            "priority_weight": entry["priority_weight"],
            "required_for": required_for,
            "preferred_for": preferred_for,
            "prerequisite_for": prerequisite_for,
            "is_prerequisite": entry["is_prerequisite"],
            "reason": reason,
            "estimated_hours": entry["estimated_hours"],
            "resource": entry["resource"],
        })
    steps.sort(key=lambda step: (
        ("Beginner", "Intermediate", "Advanced").index(step["stage"]),
        -step["priority_weight"],
        step["skill"].casefold(),
    ))

    stages = {
        stage: [step for step in steps if step["stage"] == stage]
        for stage in ("Beginner", "Intermediate", "Advanced")
    }
    completed_count = sum(step["status"] == "completed" for step in steps)
    learning_count = sum(step["status"] == "learning" for step in steps)
    return {
        "role_options": [
            {
                "id": job["id"],
                "title": job["title"],
                "company": job["company"],
                "required": job["required"],
            }
            for job in engine.jobs
        ],
        "selected_job_ids": job_ids,
        "roles": roles,
        "steps": steps,
        "stages": stages,
        "stats": {
            "target_count": len(roles),
            "average_compatibility": round(
                sum(role["compatibility"] for role in roles) / len(roles)
            ) if roles else 0,
            "completed_count": completed_count,
            "learning_count": learning_count,
            "remaining_count": len(steps) - completed_count,
            "estimated_hours_remaining": sum(
                step["estimated_hours"] for step in steps
                if step["status"] != "completed"
            ),
        },
        "estimate_note": "Effort values are rough self-study estimates in focused hours, not guarantees.",
    }


@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok", jobs=len(engine.jobs))


@app.get("/api/profile")
@login_required
def get_profile():
    with closing(_database_connection()) as db:
        row = db.execute(
            "SELECT data FROM profiles WHERE user_id = ?", (session["user_id"],)
        ).fetchone()
    if row is None:
        return jsonify(profile=None)
    try:
        return jsonify(profile=json.loads(row["data"]))
    except (json.JSONDecodeError, TypeError):
        return jsonify(profile=None)


@app.put("/api/profile")
@login_required
def save_profile():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send a JSON profile."), 400
    if len(request.data) > 16 * 1024:
        return jsonify(error="Profile is too large."), 413

    allowed = {"skills", "exp", "city", "mode", "sal"}
    clean = {key: body[key] for key in allowed if key in body}
    skills_in = clean.get("skills", [])
    if (
        not isinstance(skills_in, list)
        or not all(isinstance(skill, str) and len(skill) <= 80 for skill in skills_in)
    ):
        return jsonify(error="'skills' must be a list of strings."), 400
    clean["skills"] = skills_in[:50]
    for key in ("exp", "sal"):
        if key in clean and not _is_integer_input(clean[key]):
            return jsonify(error=f"'{key}' must be an integer."), 400
    clean["exp"] = _to_int(clean.get("exp"), 0, 0, 40)
    clean["sal"] = _to_int(clean.get("sal"), 0, 0, 200)
    mode = clean.get("mode", "Any")
    if not isinstance(mode, str) or mode not in VALID_MODES:
        return jsonify(error="Invalid work mode."), 400
    if not isinstance(clean.get("city", "Anywhere"), str):
        return jsonify(error="Invalid preferred city."), 400
    clean["city"] = str(clean.get("city", "Anywhere"))[:40]

    with closing(_database_connection()) as db, db:
        db.execute(
            """
            INSERT INTO profiles (user_id, data, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                data = excluded.data,
                updated_at = CURRENT_TIMESTAMP
            """,
            (session["user_id"], json.dumps(clean, ensure_ascii=False)),
        )
    return jsonify(profile=clean)


@app.get("/api/saved-jobs")
@login_required
def saved_jobs():
    with closing(_database_connection()) as db:
        rows = db.execute(
            "SELECT job_id, status FROM job_interactions WHERE user_id = ?",
            (session["user_id"],),
        ).fetchall()
    by_id = {row[0]: row[1] for row in rows}
    return jsonify(interactions=[
        {**job, "status": by_id[job["id"]]}
        for job in engine.jobs
        if job["id"] in by_id
    ])


@app.get("/api/skills")
@login_required
def skills():
    return jsonify(engine.all_skills())


@app.get("/api/cities")
@login_required
def cities():
    return jsonify(engine.all_cities())


@app.get("/api/jobs")
@login_required
def jobs():
    return jsonify(engine.jobs)


@app.get("/api/jobs/<int:job_id>")
@login_required
def job_detail(job_id):
    for j in engine.jobs:
        if j["id"] == job_id:
            return jsonify(j)
    return jsonify(error="Job not found"), 404


@app.put("/api/jobs/<int:job_id>/interaction")
@login_required
def set_job_interaction(job_id):
    if not any(job["id"] == job_id for job in engine.jobs):
        return jsonify(error="Job not found"), 404
    body = request.get_json(silent=True)
    status = body.get("status") if isinstance(body, dict) else None
    if not isinstance(status, str) or status not in {"saved", "dismissed", "applied"}:
        return jsonify(error="'status' must be saved, dismissed, or applied."), 400
    with closing(_database_connection()) as db, db:
        db.execute(
            """
            INSERT INTO job_interactions (user_id, job_id, status)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, job_id) DO UPDATE SET
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP
            """,
            (session["user_id"], job_id, status),
        )
    return jsonify(status=status, job_id=job_id)


@app.delete("/api/jobs/<int:job_id>/interaction")
@login_required
def delete_job_interaction(job_id):
    with closing(_database_connection()) as db, db:
        db.execute(
            "DELETE FROM job_interactions WHERE user_id = ? AND job_id = ?",
            (session["user_id"], job_id),
        )
    return jsonify(status="removed", job_id=job_id)


@app.get("/api/applications")
@login_required
def list_applications():
    status = request.args.get("status")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    if status and status not in APPLICATION_STATUSES:
        return jsonify(error="Invalid application status."), 400
    if start_date and not _valid_application_date(start_date):
        return jsonify(error="'start_date' must be an ISO date (YYYY-MM-DD)."), 400
    if end_date and not _valid_application_date(end_date):
        return jsonify(error="'end_date' must be an ISO date (YYYY-MM-DD)."), 400
    if start_date and end_date and start_date > end_date:
        return jsonify(error="'start_date' must not be after 'end_date'."), 400

    clauses = ["user_id = ?"]
    values = [session["user_id"]]
    if status:
        clauses.append("status = ?")
        values.append(status)
    if start_date:
        clauses.append("application_date >= ?")
        values.append(start_date)
    if end_date:
        clauses.append("application_date <= ?")
        values.append(end_date)

    with closing(_database_connection()) as db:
        rows = db.execute(
            "SELECT * FROM applications WHERE "
            + " AND ".join(clauses)
            + " ORDER BY application_date DESC, updated_at DESC",
            values,
        ).fetchall()
    return jsonify(applications=[_application_json(row) for row in rows])


@app.get("/api/applications/<int:application_id>")
@login_required
def get_application(application_id):
    with closing(_database_connection()) as db:
        row = db.execute(
            "SELECT * FROM applications WHERE id = ? AND user_id = ?",
            (application_id, session["user_id"]),
        ).fetchone()
    if row is None:
        return jsonify(error="Application not found."), 404
    return jsonify(application=_application_json(row))


@app.post("/api/applications")
@login_required
def create_application():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send a JSON body."), 400
    job_id = body.get("job_id")
    if isinstance(job_id, bool) or not isinstance(job_id, int):
        return jsonify(error="'job_id' must be an integer."), 400
    if not any(job["id"] == job_id for job in engine.jobs):
        return jsonify(error="Job not found."), 404

    status = body.get("status", "Interested")
    application_date = body.get("application_date", date.today().isoformat())
    notes = body.get("notes", "")
    if status not in APPLICATION_STATUSES:
        return jsonify(error="Invalid application status."), 400
    if not _valid_application_date(application_date):
        return jsonify(error="'application_date' must be an ISO date (YYYY-MM-DD)."), 400
    if not isinstance(notes, str) or len(notes) > 2000:
        return jsonify(error="'notes' must be a string of at most 2000 characters."), 400

    try:
        with closing(_database_connection()) as db, db:
            cursor = db.execute(
                """
                INSERT INTO applications (user_id, job_id, status, application_date, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session["user_id"], job_id, status, application_date, notes.strip()),
            )
            row = db.execute(
                "SELECT * FROM applications WHERE id = ? AND user_id = ?",
                (cursor.lastrowid, session["user_id"]),
            ).fetchone()
    except _database_integrity_errors():
        return jsonify(error="This job is already in your application tracker."), 409
    return jsonify(application=_application_json(row)), 201


@app.put("/api/applications/<int:application_id>")
@login_required
def update_application(application_id):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send a JSON body."), 400
    with closing(_database_connection()) as db:
        existing = db.execute(
            "SELECT * FROM applications WHERE id = ? AND user_id = ?",
            (application_id, session["user_id"]),
        ).fetchone()
    if existing is None:
        return jsonify(error="Application not found."), 404

    status = body.get("status", existing["status"])
    application_date = body.get("application_date", existing["application_date"])
    notes = body.get("notes", existing["notes"])
    if status not in APPLICATION_STATUSES:
        return jsonify(error="Invalid application status."), 400
    if not _valid_application_date(application_date):
        return jsonify(error="'application_date' must be an ISO date (YYYY-MM-DD)."), 400
    if not isinstance(notes, str) or len(notes) > 2000:
        return jsonify(error="'notes' must be a string of at most 2000 characters."), 400

    with closing(_database_connection()) as db, db:
        db.execute(
            """
            UPDATE applications
            SET status = ?, application_date = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
            """,
            (status, application_date, notes.strip(), application_id, session["user_id"]),
        )
        row = db.execute(
            "SELECT * FROM applications WHERE id = ? AND user_id = ?",
            (application_id, session["user_id"]),
        ).fetchone()
    return jsonify(application=_application_json(row))


@app.delete("/api/applications/<int:application_id>")
@login_required
def delete_application(application_id):
    with closing(_database_connection()) as db, db:
        cursor = db.execute(
            "DELETE FROM applications WHERE id = ? AND user_id = ?",
            (application_id, session["user_id"]),
        )
    if cursor.rowcount == 0:
        return jsonify(error="Application not found."), 404
    return jsonify(status="removed", application_id=application_id)


@app.get("/api/application-stats")
@login_required
def application_stats():
    with closing(_database_connection()) as db:
        rows = db.execute(
            "SELECT status, COUNT(*) AS count FROM applications WHERE user_id = ? GROUP BY status",
            (session["user_id"],),
        ).fetchall()
    counts = {status: 0 for status in APPLICATION_STATUSES}
    counts.update({row["status"]: row["count"] for row in rows})
    return jsonify(total=sum(counts.values()), statuses=counts)


@app.get("/api/roadmap")
@login_required
def get_roadmap():
    user_id = session["user_id"]
    with closing(_database_connection()) as db:
        preference = db.execute(
            "SELECT job_ids FROM roadmap_preferences WHERE user_id = ?", (user_id,)
        ).fetchone()
        progress_rows = db.execute(
            "SELECT skill, status FROM skill_learning_progress WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    try:
        job_ids = json.loads(preference["job_ids"]) if preference else []
    except (json.JSONDecodeError, TypeError):
        job_ids = []
    if not isinstance(job_ids, list) or not all(
        isinstance(job_id, int) and not isinstance(job_id, bool)
        for job_id in job_ids
    ):
        job_ids = []
    else:
        valid_job_ids = {job["id"] for job in engine.jobs}
        job_ids = [job_id for job_id in job_ids if job_id in valid_job_ids]
    profile = _read_user_profile(user_id)
    skills = profile.get("skills", [])
    if not isinstance(skills, list) or not all(isinstance(skill, str) for skill in skills):
        skills = []
    progress = {_norm(row["skill"]): row["status"] for row in progress_rows}
    return jsonify(_roadmap_response(job_ids, skills, progress))


@app.post("/api/roadmap")
@login_required
def build_roadmap():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send a JSON body."), 400
    job_ids = body.get("job_ids")
    valid_job_ids = {job["id"] for job in engine.jobs}
    if (
        not isinstance(job_ids, list)
        or not job_ids
        or len(job_ids) > len(valid_job_ids)
        or any(
            isinstance(job_id, bool)
            or not isinstance(job_id, int)
            or job_id not in valid_job_ids
            for job_id in job_ids
        )
    ):
        return jsonify(error="'job_ids' must be a non-empty list of available job IDs."), 400
    job_ids = list(dict.fromkeys(job_ids))

    profile = _read_user_profile(session["user_id"])
    skills = body.get("skills", profile.get("skills", []))
    if (
        not isinstance(skills, list)
        or len(skills) > 50
        or any(not isinstance(skill, str) or len(skill.strip()) > 80 for skill in skills)
    ):
        return jsonify(error="'skills' must be a list of at most 50 skill names."), 400
    skills = list(dict.fromkeys(skill.strip() for skill in skills if skill.strip()))

    with closing(_database_connection()) as db, db:
        db.execute(
            """
            INSERT INTO roadmap_preferences (user_id, job_ids, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                job_ids = excluded.job_ids,
                updated_at = CURRENT_TIMESTAMP
            """,
            (session["user_id"], json.dumps(job_ids)),
        )
        progress_rows = db.execute(
            "SELECT skill, status FROM skill_learning_progress WHERE user_id = ?",
            (session["user_id"],),
        ).fetchall()
    progress = {_norm(row["skill"]): row["status"] for row in progress_rows}
    return jsonify(_roadmap_response(job_ids, skills, progress))


@app.put("/api/roadmap/progress")
@login_required
def update_roadmap_progress():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send a JSON body."), 400
    skill = body.get("skill")
    status = body.get("status")
    if not isinstance(skill, str) or not skill.strip() or len(skill.strip()) > 80:
        return jsonify(error="'skill' must be a non-empty skill name of at most 80 characters."), 400
    if not isinstance(status, str) or status not in ROADMAP_STATUSES:
        return jsonify(error="'status' must be planned, learning, or completed."), 400

    skill_labels = _roadmap_skill_labels()
    normalized = _norm(skill)
    if normalized not in skill_labels:
        return jsonify(error="Choose a skill from the available job and roadmap data."), 400
    canonical_skill = skill_labels[normalized]
    user_id = session["user_id"]
    if status == "completed":
        profile_skills = _read_user_profile(user_id).get("skills", [])
        if not isinstance(profile_skills, list):
            profile_skills = []
        already_known = any(
            isinstance(existing, str) and _norm(existing) == normalized
            for existing in profile_skills
        )
        if not already_known and len(profile_skills) >= 50:
            return jsonify(error="Your profile already has 50 skills. Remove one before completing another."), 409

    with closing(_database_connection()) as db, db:
        if status == "planned":
            db.execute(
                "DELETE FROM skill_learning_progress WHERE user_id = ? AND skill = ?",
                (user_id, canonical_skill),
            )
        else:
            db.execute(
                """
                INSERT INTO skill_learning_progress (user_id, skill, status)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, skill) DO UPDATE SET
                    status = excluded.status,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, canonical_skill, status),
            )
        if status == "completed":
            row = db.execute(
                "SELECT data FROM profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
            try:
                profile = json.loads(row["data"]) if row else {}
            except (json.JSONDecodeError, TypeError):
                profile = {}
            if not isinstance(profile, dict):
                profile = {}
            profile_skills = profile.get("skills", [])
            if not isinstance(profile_skills, list):
                profile_skills = []
            if not any(
                isinstance(existing, str) and _norm(existing) == normalized
                for existing in profile_skills
            ):
                profile_skills.append(canonical_skill)
            profile["skills"] = profile_skills[:50]
            db.execute(
                """
                INSERT INTO profiles (user_id, data, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    data = excluded.data,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, json.dumps(profile, ensure_ascii=False)),
            )

        updated_profile = db.execute(
            "SELECT data FROM profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
    try:
        profile_skills = json.loads(updated_profile["data"]).get("skills", []) if updated_profile else []
    except (json.JSONDecodeError, TypeError, AttributeError):
        profile_skills = []
    return jsonify(
        progress={"skill": canonical_skill, "status": status},
        profile_skills=profile_skills,
    )


@app.post("/api/recommend")
@login_required
def recommend():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send a JSON body."), 400

    raw_skills = body.get("skills", [])
    if not isinstance(raw_skills, list) or not all(isinstance(s, str) for s in raw_skills):
        return jsonify(error="'skills' must be a list of strings."), 400
    skills_in = [s.strip()[:40] for s in raw_skills if s.strip()][:50]

    mode = body.get("mode", "Any")
    if not isinstance(mode, str) or mode not in VALID_MODES:
        return jsonify(error="'mode' must be one of Any, Onsite, Hybrid, Remote."), 400

    preferred_mode = body.get("preferred_mode", "Any")
    if not isinstance(preferred_mode, str) or preferred_mode not in VALID_MODES:
        return jsonify(error="'preferred_mode' must be one of Any, Onsite, Hybrid, or Remote."), 400

    for key in ("experience", "min_salary", "limit", "offset"):
        if key in body and not _is_integer_input(body[key]):
            return jsonify(error=f"'{key}' must be an integer."), 400
    city = body.get("city", "Anywhere")
    if not isinstance(city, str):
        return jsonify(error="'city' must be a string."), 400

    limit = _to_int(body.get("limit"), 50, 1, 50)
    offset = _to_int(body.get("offset"), 0, 0, 10000)
    sort = body.get("sort", "score")
    if not isinstance(sort, str) or sort not in {"score", "salary", "title"}:
        return jsonify(error="'sort' must be one of score, salary, title."), 400

    all_results = engine.recommend(
        skills=skills_in,
        experience=_to_int(body.get("experience"), 0, 0, 40),
        city=city[:40],
        mode=mode,
        min_salary=_to_int(body.get("min_salary"), 0, 0, 200),
        preferred_mode=preferred_mode,
    )
    if sort == "salary":
        all_results.sort(key=lambda result: (result["salary_max"], result["salary_min"]), reverse=True)
    elif sort == "title":
        all_results.sort(key=lambda result: result["title"].lower())

    results = all_results[offset:offset + limit]
    return jsonify(
        total_jobs=len(engine.jobs),
        total_matches=len(all_results),
        limit=limit,
        offset=offset,
        results=results,
        skill_gaps=engine.skill_gaps(results) if skills_in else [],
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
