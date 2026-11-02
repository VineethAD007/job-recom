"""Content-based job recommender.

Final score (0-100) = 0.60 * skills + 0.20 * experience + 0.10 * location + 0.10 * salary

skills = 0.7 * weighted overlap + 0.3 * TF-IDF cosine similarity
  - weighted overlap: required skills weigh 2, nice-to-have skills weigh 1
  - TF-IDF cosine: rare skills (e.g. OpenCV) count for more than common ones (e.g. SQL)
"""
import json
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

DATA_FILE = Path(__file__).parent / "data" / "jobs.json"

WEIGHTS = {"skills": 0.60, "experience": 0.20, "location": 0.10, "salary": 0.10}
OVERLAP_SHARE = 0.7  # remaining 0.3 comes from TF-IDF cosine


def load_jobs(path=DATA_FILE):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _norm(skill):
    return skill.strip().lower()


class Recommender:
    def __init__(self, jobs=None):
        self.jobs = jobs if jobs is not None else load_jobs()
        # Each job document is a list of skill tokens; required skills appear twice
        docs = [self._job_tokens(j) for j in self.jobs]
        self.vectorizer = TfidfVectorizer(analyzer=lambda tokens: tokens)
        self.job_matrix = self.vectorizer.fit_transform(docs)

    @staticmethod
    def _job_tokens(job):
        req = [_norm(s) for s in job["required"]]
        nice = [_norm(s) for s in job["nice"]]
        return req + req + nice

    # ---- helpers exposed to the API ----
    def all_skills(self):
        names = {}
        for j in self.jobs:
            for s in j["required"] + j["nice"]:
                names[_norm(s)] = s
        return sorted(names.values(), key=str.lower)

    def all_cities(self):
        return sorted({j["city"] for j in self.jobs if j["mode"] != "Remote"})

    # ---- scoring parts ----
    @staticmethod
    def _experience_score(exp, job):
        if exp < job["exp_min"]:
            return max(0.0, 1 - (job["exp_min"] - exp) * 0.35)
        if exp > job["exp_max"]:
            return max(0.0, 1 - (exp - job["exp_max"]) * 0.2)
        return 1.0

    @staticmethod
    def _location_score(city, job):
        if job["mode"] == "Remote":
            return 1.0
        if not city or city == "Anywhere":
            return 0.8
        return 1.0 if job["city"] == city else 0.3

    @staticmethod
    def _salary_score(min_salary, job):
        if not min_salary:
            return 1.0
        if job["salary_max"] >= min_salary:
            return 1.0 if job["salary_min"] >= min_salary else 0.8
        return max(0.0, job["salary_max"] / min_salary - 0.2)

    def recommend(self, skills, experience=0, city="Anywhere", mode="Any", min_salary=0, limit=None):
        user_skills = {_norm(s) for s in skills if s and s.strip()}
        user_vec = self.vectorizer.transform([sorted(user_skills)])
        cosines = cosine_similarity(user_vec, self.job_matrix)[0]

        results = []
        for job, cos in zip(self.jobs, cosines):
            if mode not in ("Any", "", None) and job["mode"] != mode:
                continue

            req_hit = [s for s in job["required"] if _norm(s) in user_skills]
            nice_hit = [s for s in job["nice"] if _norm(s) in user_skills]
            total_w = 2 * len(job["required"]) + len(job["nice"])
            hit_w = 2 * len(req_hit) + len(nice_hit)
            overlap = hit_w / total_w if total_w else 0.0
            skills_score = OVERLAP_SHARE * overlap + (1 - OVERLAP_SHARE) * float(cos)

            parts = {
                "skills": skills_score,
                "experience": self._experience_score(experience, job),
                "location": self._location_score(city, job),
                "salary": self._salary_score(min_salary, job),
            }
            total = sum(WEIGHTS[k] * parts[k] for k in WEIGHTS)

            results.append({
                **job,
                "score": round(total * 100),
                "parts": {k: round(v, 3) for k, v in parts.items()},
                "matched_required": req_hit,
                "missing_required": [s for s in job["required"] if s not in req_hit],
                "matched_nice": nice_hit,
                "missing_nice": [s for s in job["nice"] if s not in nice_hit],
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit] if limit else results

    @staticmethod
    def skill_gaps(results, top_n=5, max_gaps=6):
        """Required skills the user lacks, counted across the best matches."""
        counts = {}
        for r in results[:top_n]:
            for s in r["missing_required"]:
                counts[s] = counts.get(s, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [{"skill": s, "jobs": n} for s, n in ranked[:max_gaps]]
