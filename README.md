# Pathfinder Jobs: Job Recommendation System

A small full-stack project. The user enters skills, experience, city, work style and minimum salary. The Flask backend scores every job and the frontend shows the ranked list with explanations and skill gaps.

## Project structure

```
job-recommender/
├── backend/
│   ├── app.py                 Flask API, also serves the frontend
│   ├── recommender.py         Scoring engine (content-based filtering, TF-IDF)
│   ├── test_recommender.py    Unit tests for the engine and the API
│   ├── migrate_email_verification.py  Explicit email-verification schema migration
│   ├── test_email_verification_migration.py  Migration safety tests
│   └── data/jobs.json         Example job openings
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js                 Calls POST /api/recommend
├── .vscode/launch.json        Run and test from VS Code (F5)
├── requirements.txt
└── README.md
```

## Run it in VS Code

1. Install Python 3.10 or newer, then open the `job-recommender` folder in VS Code (File > Open Folder).
2. Open a terminal (Ctrl + `) and create a virtual environment:

   Windows:
   ```
   python -m venv venv
   venv\Scripts\activate
   ```
   macOS / Linux:
   ```
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install the dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Apply the local email-verification schema migration:
   ```
   python backend/migrate_email_verification.py
   ```
   Back up an existing `backend/data/users.db` first. The migration preserves accounts and password hashes, but existing accounts become unverified and must complete email verification before accessing protected features. For a test run, point `DATABASE_PATH` at a disposable database instead of migrating your existing user database.
5. Configure `RESEND_API_KEY`, `EMAIL_FROM`, `APP_BASE_URL`, and `OTP_HASH_SECRET` in your local environment to send verification emails.
6. Start the server:
   ```
   python backend/app.py
   ```
7. Open http://localhost:8000 in your browser. Create an account, verify its email, then sign in to use the recommender.

Accounts are stored in `backend/data/users.db`; passwords are stored as hashes. To keep sessions valid across server restarts, set a persistent `SECRET_KEY` environment variable before starting the server. If it is not set, a temporary key is generated at startup and existing sessions expire when the server restarts.

For deployment, set `SECRET_KEY` to a long random value and set `COOKIE_SECURE=true` when using HTTPS. Optionally set `SESSION_LIFETIME_SECONDS` and `DATABASE_PATH`.

## Use Turso with Render

The app uses local SQLite by default. For remote storage, create a **Turso** database (the `turso_serverless` driver does not connect to legacy libSQL databases) and configure `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` as Render environment secrets.

### New Render Free deployment

Render Free web services have an ephemeral filesystem and do not provide Shell/SSH access. A local `users.db` can disappear when the service spins down, restarts, or redeploys. To store new accounts reliably:

1. Create a new, empty Turso database and set `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, and `DATABASE_BACKEND=turso` in Render's Environment settings.
2. Deploy this version of the app. New accounts and user data will be written to Turso. `DATABASE_PATH` is only used for local SQLite mode.

If you need old data from a Free Render instance, do not redeploy until you have obtained a copy of the SQLite file. Free services have no Shell/SSH to run the migration utility against that file. If you have a backup or download, run the utility from your computer with the downloaded file and Turso credentials set in your environment:

```
python backend/migrate_render_to_turso.py --source /path/to/users.db
```

### Migrate from a Render service with Shell access

For a service with Shell access and an available SQLite file, pause writes and keep `DATABASE_BACKEND=sqlite` while migrating. Create an empty Turso database, set `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`, then run from the repository root:

```
python backend/migrate_render_to_turso.py
```

Pass `--source /path/to/users.db` if the database is not at `DATABASE_PATH`. The script copies users, profiles, job interactions, applications, roadmap preferences, and learning progress. It preserves user IDs and password hashes, verifies row counts, and refuses to copy into tables that already contain data. After it reports the copied counts, set `DATABASE_BACKEND=turso` and redeploy. Keep the source backup until you verify existing accounts and data.

The migration utility copies rows through Turso's API, so it does not require WAL mode. WAL is required for Turso's physical SQLite-file import workflow. Do not put Turso tokens in source control or paste them into chat.

For local development, leave `DATABASE_BACKEND` unset (or set it to `sqlite`); the app continues to use `DATABASE_PATH`.

## Email verification

New accounts must verify their email with a single-use six-digit code before login. Codes expire after 10 minutes; incorrect attempts, per-account resend cooldowns, and hourly resend limits are enforced by the backend and stored in the configured database. Existing accounts are kept and migrated as unverified. On their next valid login, a code is sent to the email address already on the account; protected APIs also check verification in the database, so an old session cannot bypass the requirement.

Configure these Render environment variables:

- `RESEND_API_KEY`: a secret API key created in the Resend dashboard.
- `EMAIL_FROM`: a sender using a domain verified in Resend, for example `Pathfinder Jobs <auth@example.com>`.
- `APP_BASE_URL`: the public HTTPS URL of this app, without a trailing slash.
- `OTP_HASH_SECRET`: a long, random secret kept stable across restarts; it is used to HMAC verification codes.
- `SECRET_KEY`: the existing Flask session secret, also kept stable across restarts.
- `DATABASE_BACKEND=turso`, `TURSO_DATABASE_URL`, and `TURSO_AUTH_TOKEN` for the deployed Turso database.

Generate `OTP_HASH_SECRET` with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and keep the result only in Render's environment settings. It must remain stable so outstanding codes remain verifiable after a restart.

To configure Resend, create an account, generate an API key in the [API Keys dashboard](https://resend.com/api-keys), and [add a domain](https://resend.com/docs/add-a-domain.md). Publish the exact DNS records Resend shows for domain verification and email authentication; record names and values depend on your DNS provider and domain, so do not copy generic values. Wait for the domain to show as verified before using it in `EMAIL_FROM`. The code sends mail through Resend's [HTTPS Email API](https://resend.com/docs/api-reference/emails/send-email); it does not use SMTP.

### Apply the email-verification database migration

The migration is explicit and repeatable; application startup does not apply it. Back up the production database first. From a trusted local checkout, set the deployed Turso connection variables in your shell and explicitly set `DATABASE_BACKEND=turso` before running the migration. Without that setting the script defaults to SQLite and will migrate the local SQLite database instead. Never put credentials in source control:

# PowerShell, from the repository root:
```
$env:DATABASE_BACKEND = "turso"
$env:TURSO_DATABASE_URL = "<your Turso database URL>"
$env:TURSO_AUTH_TOKEN = "<your Turso auth token>"
python backend/migrate_email_verification.py
```

The migration adds `name` and `email_verified` columns to `users` and creates `email_verifications` for hashed codes, expirations, attempts, and resend counters. Existing user rows, IDs, email addresses, and password hashes are retained; existing rows receive an empty name and `email_verified=0`, so those users must verify before using protected features. The script is safe to rerun. Run it against a backup or staging database first, confirm its output, and only then deploy the new application version. Do not deploy the verification-enabled code before the database migration has completed.

After deployment, test registration with an email address you control, verify the received code, and confirm login succeeds. Email-provider responses are not logged, and a delivery failure leaves the account pending so the user can request another code after the cooldown. The repository does not contain production credentials; delivery cannot be confirmed until Resend and Render are configured.

The frontend can be served by Flask on port 8000 or by VS Code Live Server on port 5500. Live Server requests are allowed only from localhost ports 5500/127.0.0.1:5500.

You can also press F5 in VS Code and choose "Run Job Recommender (Flask)". The Live Server extension on port 5500 is also supported while the Flask server is running.

## Run the tests

```
cd backend
python -m unittest -v
```

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/register` | Create an unverified account and send a verification code |
| POST | `/api/login` | Sign in |
| POST | `/api/logout` | Sign out |
| GET | `/api/session` | Check the current sign-in session |
| POST | `/api/verify-email` | Verify a six-digit email code |
| POST | `/api/resend-verification` | Request another email verification code |
| GET | `/api/health` | Server status |
| GET | `/api/skills` | All skills in the job data |
| GET | `/api/cities` | Cities with onsite or hybrid jobs |
| GET | `/api/jobs` | All jobs (sign-in required) |
| GET | `/api/jobs/<id>` | One job (sign-in required) |
| POST | `/api/recommend` | Ranked matches for a profile (sign-in required) |
| GET | `/api/csrf` | Create/read the CSRF token |
| GET/PUT | `/api/profile` | Read or save a user's profile |
| GET | `/api/saved-jobs` | Read saved, dismissed or applied jobs |
| PUT/DELETE | `/api/jobs/<id>/interaction` | Set or remove a job interaction |
| GET | `/api/applications` | List a user's applications; supports `status`, `start_date` and `end_date` filters |
| GET/POST | `/api/applications/<id>` / `/api/applications` | Read or create an application |
| PUT/DELETE | `/api/applications/<id>` | Update or remove an application |
| GET | `/api/application-stats` | Application counts by status for the signed-in user |
| GET/POST | `/api/roadmap` | Read saved roadmap or build it for selected job IDs |
| PUT | `/api/roadmap/progress` | Mark a roadmap skill planned, learning or completed |

Example request:

```json
POST /api/recommend
{
  "skills": ["Python", "SQL", "Machine Learning"],
  "experience": 1,
  "city": "Bengaluru",
  "mode": "Any",
  "min_salary": 8
}
```

The response has `results` (each job with `score`, component `parts`, matched/missing skills, related skills and human-readable `reasons`) and `skill_gaps`. The optional `preferred_mode` request field scores work-mode compatibility softly; `mode` remains the hard result filter for existing clients.

The application tracker provides a Kanban board and activity timeline for Interested, Applied, Assessment, Interview, Offered and Rejected jobs. Each user can track a job once, set its application date and keep notes up to 2,000 characters. Application endpoints require sign-in, and all reads, changes, counts and filters are scoped to the current account. Application dates use `YYYY-MM-DD`; list filters are inclusive.

The career roadmap compares a user's profile skills with the required and preferred skills on selected job listings. It ranks shared requirements first and labels inferred learning prerequisites separately. Per-user role choices and progress are stored in SQLite; completing a skill also adds it to the user's profile so job recommendations recalculate. Learning-hour values are rough focused-study estimates, not guarantees. Resource links point to curated free learning materials, and the feature does not promise certifications or employment outcomes.

## How the recommender works

```
match = 0.60 * skills + 0.18 * experience + 0.10 * location
        + 0.08 * salary + 0.04 * work-mode preference
skills = 0.8 * weighted coverage + 0.2 * TF-IDF cosine similarity
```

- **Skill normalization:** explicit aliases canonicalize `ML`/`Machine Learning`, `JS`/`JavaScript`, and `React.js`/`React`. HTML and CSS are kept distinct from each other and from the combined `HTML/CSS` requirement.
- **Related skills:** a small curated taxonomy covers close alternatives such as PyTorch/TensorFlow/Keras, React/Angular/Vue, and AWS/Azure/GCP. These earn 35% of exact-match coverage but remain listed as missing requirements; they are not treated as synonyms.
- **Weighted coverage:** required skills weigh 3, preferred skills weigh 1. A related-skill match receives 35% of that skill's weight.
- **TF-IDF cosine:** jobs are vectors of normalized skill tokens (required skills repeated twice). This adds lexical, corpus-specific weighting; it does not infer context or unseen synonyms.
- **Experience:** full score inside the job's range, reduced by 0.35 per year below it and 0.2 per year above it.
- **Location and salary:** remote/compatible locations score highest; an unset location is neutral. Salary is scored against the requested minimum.
- **Work preference:** the profile's mode is a soft scoring factor; the recommendation-page mode chips remain a separate hard filter.
- **Explainability:** each result includes normalized component scores in `parts`, exact and related skill lists, missing requirements, and text `reasons`. Scores are clamped to 0–100 and ties are deterministically ordered.
- **Duplicate/incomplete data:** duplicate role/company/location/mode listings are collapsed. Missing job fields receive neutral defaults; empty job lists are supported.
- **Skill gaps:** the top five recommendations contribute missing required skills. Higher-scoring jobs count more, related-but-missing skills count less, and each gap includes a 0–100 priority relative to the strongest current gap plus the number of jobs.

This is a local, deterministic content-based recommender, not a hosted LLM or a learned embedding model. Its semantic behavior is limited to the curated aliases and related-skill groups; TF-IDF only captures token overlap. It cannot infer arbitrary synonyms, candidate seniority from resumes, or job context that is absent from the job data. The job data also contains no descriptions or application URLs.

## Ideas to extend it

- Replace `jobs.json` with a database (SQLite with SQLAlchemy, or PostgreSQL).
- Add user accounts and a "save job" button, then use saved jobs for collaborative filtering (users who saved the same jobs get each other's recommendations).
- Parse a resume (PDF) to fill in the skills automatically.
- Evaluate a locally hosted sentence-embedding model if broader contextual skill matching is needed.
- Pull live listings from a job board API.
