"""Content-based job recommendations with explainable skill and profile scoring.

The scorer uses curated exact-synonym normalization and TF-IDF over skill tokens.
Related technologies receive limited credit but remain visible as missing requirements.
"""
import json
import math
import re
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

DATA_FILE = Path(__file__).parent / "data" / "jobs.json"

WEIGHTS = {
    "skills": 0.60,
    "experience": 0.18,
    "location": 0.10,
    "salary": 0.08,
    "preference": 0.04,
}
OVERLAP_TFIDF_SHARE = 0.80
RELATED_SKILL_CREDIT = 0.35

SKILL_ALIASES = {
    "ml": "machine learning",
    "machinelearning": "machine learning",
    "js": "javascript",
    "reactjs": "react",
    "node": "node.js",
    "nodejs": "node.js",
    "html/css": "html/css",
    "htmlcss": "html/css",
    "html css": "html/css",
    "springboot": "spring boot",
    "k8s": "kubernetes",
    "cicd": "ci/cd",
    "ci cd": "ci/cd",
    "powerbi": "power bi",
    "restapi": "rest apis",
    "restapis": "rest apis",
    "rest api": "rest apis",
}

# These are related technologies, not synonyms or interchangeable requirements.
RELATED_SKILL_GROUPS = (
    frozenset({"pytorch", "tensorflow", "keras"}),
    frozenset({"react", "angular", "vue"}),
    frozenset({"aws", "azure", "gcp"}),
)
VALID_MODES = {"Any", "Onsite", "Hybrid", "Remote"}


def load_jobs(path=DATA_FILE):
    with open(path, encoding="utf-8") as f:
        jobs = json.load(f)
    if not isinstance(jobs, list):
        raise ValueError("Job data must contain a JSON list.")
    return jobs


def _norm(skill):
    if not isinstance(skill, str):
        return ""
    raw = re.sub(r"\s+", " ", skill.casefold().strip())
    normalized = re.sub(r"[._-]+", " ", raw)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    compact = re.sub(r"[\s./_-]+", "", raw)
    return (
        SKILL_ALIASES.get(raw)
        or SKILL_ALIASES.get(normalized)
        or SKILL_ALIASES.get(compact)
        or normalized
    )


def _finite_number(value, default=0):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(number):
        return default
    return max(0, number)


def _display_name(value, default):
    return value.strip() if isinstance(value, str) and value.strip() else default


def _skill_list(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []

    skills = []
    seen = set()
    for skill in value:
        if not isinstance(skill, str):
            continue
        display = skill.strip()
        normalized = _norm(display)
        if normalized and normalized not in seen:
            skills.append(display)
            seen.add(normalized)
    return skills


def _mode_value(value):
    if value is None or value == "":
        return "Any"
    if not isinstance(value, str):
        raise ValueError("Mode must be Any, Onsite, Hybrid, or Remote.")
    normalized = value.strip().casefold()
    for mode in VALID_MODES:
        if mode.casefold() == normalized:
            return mode
    raise ValueError("Mode must be Any, Onsite, Hybrid, or Remote.")


def _job_mode(value):
    try:
        return _mode_value(value)
    except ValueError:
        return "Any"


class Recommender:
    def __init__(self, jobs=None):
        source_jobs = load_jobs() if jobs is None else jobs
        if not isinstance(source_jobs, list):
            raise ValueError("Jobs must be a list.")
        self.jobs = self._prepare_jobs(source_jobs)

        docs = [self._job_tokens(job) for job in self.jobs]
        self.vectorizer = None
        self.job_matrix = None
        if any(docs):
            self.vectorizer = TfidfVectorizer(analyzer=lambda tokens: tokens)
            self.job_matrix = self.vectorizer.fit_transform(docs)

    @staticmethod
    def _prepare_jobs(jobs):
        prepared = []
        seen_ids = set()
        seen_roles = set()

        for index, source in enumerate(jobs):
            if not isinstance(source, dict):
                continue
            job = dict(source)
            job_id = job.get("id")
            if isinstance(job_id, int) and not isinstance(job_id, bool) and job_id > 0:
                job["id"] = job_id
            elif isinstance(job_id, str) and job_id.isdecimal() and int(job_id) > 0:
                job["id"] = int(job_id)
            else:
                job["id"] = index + 1
            job["title"] = _display_name(job.get("title"), "Untitled role")
            job["company"] = _display_name(job.get("company"), "Unknown company")
            job["city"] = _display_name(job.get("city"), "Location not specified")
            job["mode"] = _job_mode(job.get("mode"))
            job["required"] = _skill_list(job.get("required"))
            required_norms = {_norm(skill) for skill in job["required"]}
            job["nice"] = [
                skill for skill in _skill_list(job.get("nice"))
                if _norm(skill) not in required_norms
            ]

            job["exp_min"] = _finite_number(job.get("exp_min"), 0)
            job["exp_max"] = max(
                job["exp_min"], _finite_number(job.get("exp_max"), job["exp_min"])
            )
            job["salary_min"] = _finite_number(job.get("salary_min"), 0)
            job["salary_max"] = max(
                job["salary_min"],
                _finite_number(job.get("salary_max"), job["salary_min"]),
            )

            id_key = str(job["id"])
            role_key = tuple(
                value.casefold()
                for value in (job["title"], job["company"], job["city"], job["mode"])
            )
            if id_key in seen_ids or role_key in seen_roles:
                continue
            seen_ids.add(id_key)
            seen_roles.add(role_key)
            prepared.append(job)

        return prepared

    @staticmethod
    def _job_tokens(job):
        required = [_norm(skill) for skill in job.get("required", [])]
        nice = [_norm(skill) for skill in job.get("nice", [])]
        return required + required + nice

    @staticmethod
    def _candidate_skills(skills):
        if skills is None:
            return {}, set()
        if isinstance(skills, str):
            skills = [skills]
        if not isinstance(skills, (list, tuple, set)):
            raise ValueError("Skills must be a list of strings.")

        display_by_norm = {}
        for skill in skills:
            if isinstance(skill, str) and skill.strip():
                display_by_norm.setdefault(_norm(skill), skill.strip())
        display_by_norm.pop("", None)
        return display_by_norm, set(display_by_norm)

    @staticmethod
    def _related_skill(target, user_skills):
        for group in RELATED_SKILL_GROUPS:
            if target in group:
                candidates = sorted((group & user_skills) - {target})
                if candidates:
                    return candidates[0]
        return None

    # ---- helpers exposed to the API ----
    def all_skills(self):
        names = {}
        for job in self.jobs:
            for skill in job["required"] + job["nice"]:
                names.setdefault(_norm(skill), skill)
        return sorted(names.values(), key=str.lower)

    def all_cities(self):
        return sorted({
            job["city"] for job in self.jobs
            if job["mode"] != "Remote" and job["city"] != "Location not specified"
        })

    # ---- scoring parts ----
    @staticmethod
    def _experience_score(exp, job):
        exp = _finite_number(exp, 0)
        if exp < job["exp_min"]:
            return max(0.0, 1 - (job["exp_min"] - exp) * 0.35)
        if exp > job["exp_max"]:
            return max(0.0, 1 - (exp - job["exp_max"]) * 0.2)
        return 1.0

    @staticmethod
    def _location_score(city, job):
        job_mode = _job_mode(job.get("mode"))
        job_city = _display_name(job.get("city"), "Location not specified")
        candidate_city = _display_name(city, "Anywhere")
        if job_mode == "Remote" or job_city.casefold() == "remote":
            return 1.0
        if candidate_city.casefold() == "anywhere":
            return 0.8
        if job_city == "Location not specified":
            return 0.8
        return 1.0 if job_city.casefold() == candidate_city.casefold() else 0.3

    @staticmethod
    def _salary_score(min_salary, job):
        min_salary = _finite_number(min_salary, 0)
        if not min_salary:
            return 1.0
        salary_max = _finite_number(job.get("salary_max"), 0)
        salary_min = _finite_number(job.get("salary_min"), 0)
        if salary_max >= min_salary:
            return 1.0 if salary_min >= min_salary else 0.8
        return max(0.0, salary_max / min_salary - 0.2)

    @staticmethod
    def _preference_score(preferred_mode, job):
        if preferred_mode == "Any":
            return 1.0
        job_mode = _job_mode(job.get("mode"))
        if job_mode == "Any":
            return 0.5
        return 1.0 if job_mode == preferred_mode else 0.35

    def recommend(
        self,
        skills,
        experience=0,
        city="Anywhere",
        mode="Any",
        min_salary=0,
        limit=None,
        preferred_mode="Any",
    ):
        mode_filter = _mode_value(mode)
        preferred_mode = _mode_value(preferred_mode)
        user_display, user_skills = self._candidate_skills(skills)

        if self.vectorizer is not None and user_skills:
            user_vector = self.vectorizer.transform([sorted(user_skills)])
            cosines = cosine_similarity(user_vector, self.job_matrix)[0]
        else:
            cosines = [0.0] * len(self.jobs)

        experience = _finite_number(experience, 0)
        min_salary = _finite_number(min_salary, 0)
        results = []

        for job, cosine in zip(self.jobs, cosines):
            if mode_filter != "Any" and job["mode"] != mode_filter:
                continue

            required_norms = [_norm(skill) for skill in job["required"]]
            nice_norms = [_norm(skill) for skill in job["nice"]]
            matched_required = [
                skill for skill, normalized in zip(job["required"], required_norms)
                if normalized in user_skills
            ]
            matched_nice = [
                skill for skill, normalized in zip(job["nice"], nice_norms)
                if normalized in user_skills
            ]
            missing_required = [
                skill for skill, normalized in zip(job["required"], required_norms)
                if normalized not in user_skills
            ]
            missing_nice = [
                skill for skill, normalized in zip(job["nice"], nice_norms)
                if normalized not in user_skills
            ]

            related_required = []
            for skill, normalized in zip(job["required"], required_norms):
                if normalized in user_skills:
                    continue
                candidate_norm = self._related_skill(normalized, user_skills)
                if candidate_norm:
                    related_required.append({
                        "required": skill,
                        "candidate": user_display[candidate_norm],
                    })

            related_nice = []
            for skill, normalized in zip(job["nice"], nice_norms):
                if normalized in user_skills:
                    continue
                candidate_norm = self._related_skill(normalized, user_skills)
                if candidate_norm:
                    related_nice.append({
                        "required": skill,
                        "candidate": user_display[candidate_norm],
                    })

            total_weight = 3 * len(job["required"]) + len(job["nice"])
            covered_weight = (
                3 * len(matched_required)
                + len(matched_nice)
                + RELATED_SKILL_CREDIT
                * (3 * len(related_required) + len(related_nice))
            )
            overlap = min(1.0, covered_weight / total_weight) if total_weight else 0.0
            skills_score = (
                OVERLAP_TFIDF_SHARE * overlap
                + (1 - OVERLAP_TFIDF_SHARE) * float(cosine)
            )
            parts = {
                "skills": min(1.0, max(0.0, skills_score)),
                "experience": self._experience_score(experience, job),
                "location": self._location_score(city, job),
                "salary": self._salary_score(min_salary, job),
                "preference": self._preference_score(preferred_mode, job),
            }
            total = sum(WEIGHTS[key] * parts[key] for key in WEIGHTS)
            score = max(0, min(100, round(total * 100)))

            reasons = []
            if job["required"]:
                reasons.append(
                    f"Matched {len(matched_required)} of {len(job['required'])} required skills."
                )
            if related_required:
                reasons.append(
                    "Related skills: "
                    + ", ".join(
                        f"{item['candidate']} relates to {item['required']}"
                        for item in related_required
                    )
                    + "; these receive partial credit."
                )
            if missing_required:
                reasons.append("Still missing: " + ", ".join(missing_required) + ".")
            if matched_nice:
                reasons.append("Preferred skills matched: " + ", ".join(matched_nice) + ".")

            if parts["experience"] == 1.0:
                reasons.append(
                    f"Experience fits the {job['exp_min']}–{job['exp_max']} year range."
                )
            else:
                reasons.append(
                    f"Experience compatibility is {round(parts['experience'] * 100)}% "
                    f"for the {job['exp_min']}–{job['exp_max']} year range."
                )
            if parts["location"] == 1.0:
                reasons.append("Location is compatible with the profile.")
            elif parts["location"] < 0.8:
                reasons.append("The job is outside the preferred location.")
            if min_salary and parts["salary"] == 1.0:
                reasons.append("The salary range meets the minimum salary preference.")
            elif min_salary and parts["salary"] < 1.0:
                reasons.append("The salary range is below the minimum salary preference.")
            if preferred_mode != "Any":
                reasons.append(
                    f"{'Matches' if parts['preference'] == 1.0 else 'Does not match'} "
                    f"the preferred {preferred_mode} work mode."
                )

            results.append({
                **job,
                "score": score,
                "parts": {key: round(value, 3) for key, value in parts.items()},
                "matched_required": matched_required,
                "missing_required": missing_required,
                "matched_nice": matched_nice,
                "missing_nice": missing_nice,
                "related_required": related_required,
                "related_nice": related_nice,
                "reasons": reasons,
            })

        results.sort(
            key=lambda result: (
                -result["score"],
                result["title"].casefold(),
                result["company"].casefold(),
                str(result["id"]),
            )
        )
        if limit is not None:
            limit = max(0, int(limit))
            return results[:limit]
        return results

    @staticmethod
    def skill_gaps(results, top_n=5, max_gaps=6):
        """Prioritize required skills missing from high-scoring recommendations."""
        counts = {}
        priorities = {}
        labels = {}
        top_results = results[:max(0, int(top_n))]

        for index, result in enumerate(top_results):
            job_key = result.get("id", index)
            score = _finite_number(result.get("score"), 0) / 100
            related = {
                _norm(item.get("required"))
                for item in result.get("related_required", [])
                if isinstance(item, dict)
            }
            seen = set()
            for skill in result.get("missing_required", []):
                normalized = _norm(skill)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                labels.setdefault(normalized, skill)
                counts.setdefault(normalized, set()).add(job_key)
                closeness = 0.5 if normalized in related else 1.0
                priorities[normalized] = priorities.get(normalized, 0.0) + (
                    2.0 * (0.5 + score) * closeness
                )

        ranked = sorted(
            labels,
            key=lambda skill: (
                -priorities[skill],
                -len(counts[skill]),
                labels[skill].casefold(),
            ),
        )
        max_priority = max(priorities.values(), default=0.0)
        return [
            {
                "skill": labels[skill],
                "jobs": len(counts[skill]),
                "priority": round(100 * priorities[skill] / max_priority) if max_priority else 0,
            }
            for skill in ranked[:max(0, int(max_gaps))]
        ]
