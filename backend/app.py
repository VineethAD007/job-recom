"""Flask API + static file server for the job recommendation website.

Run:  python backend/app.py      then open http://localhost:8000
"""
import os
import re
import secrets
import sqlite3
from contextlib import closing
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

from recommender import Recommender

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DATABASE = Path(__file__).resolve().parent / "data" / "users.db"

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    DATABASE=os.environ.get("DATABASE_PATH", str(DATABASE)),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


def init_db():
    database = Path(app.config["DATABASE"])
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as db, db:
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


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return jsonify(error="Please sign in to continue."), 401
        return view(*args, **kwargs)

    return wrapped


init_db()


@app.after_request
def add_cors_headers(resp):
    """Lets the frontend work when opened with VS Code Live Server (port 5500)."""
    origin = request.headers.get("Origin")
    if origin in {"http://localhost:5500", "http://127.0.0.1:5500"}:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Vary"] = "Origin"
    return resp


@app.route("/api/recommend", methods=["OPTIONS"])
def recommend_preflight():
    return "", 204


@app.post("/api/register")
def register():
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
        with closing(sqlite3.connect(app.config["DATABASE"])) as db, db:
            cursor = db.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                (email, generate_password_hash(password)),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        return jsonify(error="An account with that email already exists."), 409

    session.clear()
    session["user_id"] = user_id
    session["email"] = email
    return jsonify(email=email), 201


@app.post("/api/login")
def login():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send a JSON body."), 400
    email = body.get("email")
    password = body.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        return jsonify(error="Enter your email and password."), 400

    with closing(sqlite3.connect(app.config["DATABASE"])) as db:
        user = db.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
    if user is None or not check_password_hash(user[2], password):
        return jsonify(error="Email or password is incorrect."), 401

    session.clear()
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


@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok", jobs=len(engine.jobs))


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
    if mode not in VALID_MODES:
        return jsonify(error="'mode' must be one of Any, Onsite, Hybrid, Remote."), 400

    results = engine.recommend(
        skills=skills_in,
        experience=_to_int(body.get("experience"), 0, 0, 40),
        city=str(body.get("city", "Anywhere"))[:40],
        mode=mode,
        min_salary=_to_int(body.get("min_salary"), 0, 0, 200),
    )
    return jsonify(
        total_jobs=len(engine.jobs),
        results=results,
        skill_gaps=engine.skill_gaps(results) if skills_in else [],
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
