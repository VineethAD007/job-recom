# Pathfinder Jobs: Job Recommendation System

A small full-stack project. The user enters skills, experience, city, work style and minimum salary. The Flask backend scores every job and the frontend shows the ranked list with explanations and skill gaps.

## Project structure

```
job-recommender/
├── backend/
│   ├── app.py                 Flask API, also serves the frontend
│   ├── recommender.py         Scoring engine (content-based filtering, TF-IDF)
│   ├── test_recommender.py    Unit tests for the engine and the API
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
4. Start the server:
   ```
   python backend/app.py
   ```
5. Open http://localhost:8000 in your browser. Create an account, then sign in to use the recommender.

Accounts are stored in `backend/data/users.db`; passwords are stored as hashes. To keep sessions valid across server restarts, set a persistent `SECRET_KEY` environment variable before starting the server. If it is not set, a temporary key is generated at startup and existing sessions expire when the server restarts.

You can also press F5 in VS Code and choose "Run Job Recommender (Flask)". The Live Server extension on port 5500 is also supported while the Flask server is running.

## Run the tests

```
cd backend
python -m unittest -v
```

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/register` | Create an account and sign in |
| POST | `/api/login` | Sign in |
| POST | `/api/logout` | Sign out |
| GET | `/api/session` | Check the current sign-in session |
| GET | `/api/health` | Server status |
| GET | `/api/skills` | All skills in the job data |
| GET | `/api/cities` | Cities with onsite or hybrid jobs |
| GET | `/api/jobs` | All jobs (sign-in required) |
| GET | `/api/jobs/<id>` | One job (sign-in required) |
| POST | `/api/recommend` | Ranked matches for a profile (sign-in required) |

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

The response has `results` (each job with `score`, `parts`, `matched_required`, `missing_required` and so on) and `skill_gaps`.

## How the recommender works

```
match  = 0.60 * skills + 0.20 * experience + 0.10 * location + 0.10 * salary
skills = 0.7 * weighted overlap + 0.3 * TF-IDF cosine similarity
```

- **Weighted overlap:** required skills weigh 2, nice-to-have skills weigh 1. The score is the share of total weight you cover.
- **TF-IDF cosine:** each job is a vector of its skills (required skills repeated twice). Rare skills such as OpenCV get a higher weight than common ones such as SQL.
- **Experience:** full score inside the job's range, reduced by 0.35 per year below it and 0.2 per year above it.
- **Location:** remote jobs and jobs in the chosen city score 1.0, jobs elsewhere 0.3.
- **Salary:** full score when the job's top salary reaches your minimum.
- **Skill gaps:** missing required skills counted across the top 5 matches.

## Ideas to extend it

- Replace `jobs.json` with a database (SQLite with SQLAlchemy, or PostgreSQL).
- Add user accounts and a "save job" button, then use saved jobs for collaborative filtering (users who saved the same jobs get each other's recommendations).
- Parse a resume (PDF) to fill in the skills automatically.
- Use sentence embeddings so "ML" and "Machine Learning" match.
- Pull live listings from a job board API.
