import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import (
    EmailDeliveryError,
    VERIFICATION_MAX_ATTEMPTS,
    _database_connection,
    _send_verification_email,
    _request_counts,
    app,
    init_db,
)
from migrate_email_verification import migrate as migrate_email_verification
from sklearn.metrics.pairwise import cosine_similarity
from werkzeug.security import check_password_hash

from recommender import Recommender, _norm


class RecommenderTests(unittest.TestCase):
    def setUp(self):
        self.engine = Recommender()

    def test_ml_profile_ranks_ml_job_first(self):
        res = self.engine.recommend(
            ["Python", "Machine Learning", "PyTorch", "SQL"], experience=2, city="Bengaluru"
        )
        self.assertEqual(res[0]["title"], "Machine Learning Engineer")
        self.assertGreater(res[0]["score"], 80)

    def test_no_skills_gives_low_scores(self):
        res = self.engine.recommend([], experience=0)
        self.assertTrue(all(r["score"] <= 40 for r in res))

    def test_mode_filter(self):
        res = self.engine.recommend(["Python"], mode="Remote")
        self.assertTrue(res and all(r["mode"] == "Remote" for r in res))

    def test_skill_gaps_only_lists_missing_required(self):
        res = self.engine.recommend(["Python"])
        gaps = self.engine.skill_gaps(res)
        self.assertTrue(gaps)
        self.assertNotIn("Python", [g["skill"] for g in gaps])

    def test_scores_in_range(self):
        for r in self.engine.recommend(["SQL", "Excel"], experience=12, min_salary=50):
            self.assertTrue(0 <= r["score"] <= 100)

    def test_skill_aliases_are_normalized(self):
        js_result = self.engine.recommend(["JS"], experience=2)
        ml_result = self.engine.recommend(["ML"], experience=2)
        self.assertGreater(js_result[0]["score"], 0)
        self.assertGreater(ml_result[0]["score"], 0)
        self.assertEqual(_norm("ML"), _norm("Machine Learning"))
        self.assertEqual(_norm("JS"), _norm("JavaScript"))
        self.assertEqual(_norm("React.js"), _norm("React"))
        self.assertNotEqual(_norm("HTML"), _norm("CSS"))
        self.assertNotEqual(_norm("HTML"), _norm("HTML/CSS"))
        js_full_stack = next(
            job for job in self.engine.recommend(["JS"]) if job["id"] == 3
        )
        react_frontend = next(
            job for job in self.engine.recommend(["React.js"]) if job["id"] == 4
        )
        ml_engineer = next(
            job for job in self.engine.recommend(["ML"]) if job["id"] == 1
        )
        self.assertIn("JavaScript", js_full_stack["matched_required"])
        self.assertIn("React", react_frontend["matched_required"])
        self.assertIn("Machine Learning", ml_engineer["matched_required"])

    def test_related_skills_get_partial_credit_without_claiming_required_match(self):
        job = {
            "id": 101,
            "title": "Deep Learning Engineer",
            "company": "Example Labs",
            "city": "Remote",
            "mode": "Remote",
            "exp_min": 0,
            "exp_max": 3,
            "salary_min": 0,
            "salary_max": 20,
            "required": ["TensorFlow"],
            "nice": [],
        }
        engine = Recommender([job])

        related = engine.recommend(["PyTorch"])[0]
        unrelated = engine.recommend(["Python"])[0]

        self.assertEqual(related["missing_required"], ["TensorFlow"])
        self.assertEqual(
            related["related_required"],
            [{"required": "TensorFlow", "candidate": "PyTorch"}],
        )
        self.assertEqual(unrelated["related_required"], [])
        self.assertGreater(related["parts"]["skills"], unrelated["parts"]["skills"])
        self.assertLess(related["parts"]["skills"], 1)

    def test_preferred_mode_is_soft_and_mode_filter_remains_hard(self):
        preferred = self.engine.recommend(["JavaScript"], preferred_mode="Remote")
        remote_job = next(job for job in preferred if job["mode"] == "Remote")
        onsite_job = next(job for job in preferred if job["mode"] == "Onsite")
        self.assertEqual(remote_job["parts"]["preference"], 1.0)
        self.assertLess(onsite_job["parts"]["preference"], 1.0)

        filtered = self.engine.recommend(["JavaScript"], mode="Remote", preferred_mode="Onsite")
        self.assertTrue(filtered)
        self.assertTrue(all(job["mode"] == "Remote" for job in filtered))

    def test_empty_incomplete_and_duplicate_job_data(self):
        self.assertEqual(Recommender([]).recommend(["Python"]), [])

        incomplete = Recommender([{"id": None, "title": "Unspecified role"}])
        result = incomplete.recommend(["Python"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 1)
        self.assertEqual(result[0]["required"], [])
        self.assertTrue(0 <= result[0]["score"] <= 100)

        duplicate = {
            "id": 103,
            "title": "Backend Engineer",
            "company": "Example Labs",
            "city": "Pune",
            "mode": "Hybrid",
            "required": ["Python"],
            "nice": [],
        }
        deduplicated = Recommender([duplicate, {**duplicate, "id": 104}])
        self.assertEqual(len(deduplicated.recommend(["Python"])), 1)

    def test_explanations_and_ranking_are_consistent(self):
        profile = {
            "skills": ["Python", "Machine Learning", "PyTorch", "SQL"],
            "experience": 2,
            "city": "Bengaluru",
            "preferred_mode": "Hybrid",
        }
        first = self.engine.recommend(**profile)
        second = self.engine.recommend(**profile)

        self.assertEqual([job["id"] for job in first], [job["id"] for job in second])
        self.assertEqual(
            first[0]["id"],
            self._legacy_top_job(profile["skills"], profile["experience"], profile["city"]),
        )
        self.assertEqual(
            set(first[0]["parts"]),
            {"skills", "experience", "location", "salary", "preference"},
        )
        self.assertTrue(first[0]["reasons"])
        self.assertTrue(all(0 <= job["score"] <= 100 for job in first))
        self.assertEqual(
            [job["score"] for job in first],
            sorted((job["score"] for job in first), reverse=True),
        )

    def test_skill_gaps_prioritize_frequent_required_skills(self):
        gaps = Recommender.skill_gaps([
            {"id": 1, "score": 90, "missing_required": ["Python", "SQL"]},
            {"id": 2, "score": 80, "missing_required": ["Python"]},
            {"id": 3, "score": 70, "missing_required": ["SQL"]},
        ])
        self.assertEqual({gap["skill"]: gap["jobs"] for gap in gaps}, {"Python": 2, "SQL": 2})
        self.assertIn("priority", gaps[0])
        self.assertEqual(gaps[0]["priority"], 100)
        self.assertTrue(all(0 <= gap["priority"] <= 100 for gap in gaps))

    def _legacy_top_job(self, skills, experience, city):
        """Calculate the pre-upgrade ranking for a fixed-profile regression comparison."""
        user_skills = {_norm(skill) for skill in skills}
        user_vector = self.engine.vectorizer.transform([sorted(user_skills)])
        cosines = cosine_similarity(user_vector, self.engine.job_matrix)[0]
        scored = []

        for job, cosine in zip(self.engine.jobs, cosines):
            required = [skill for skill in job["required"] if _norm(skill) in user_skills]
            nice = [skill for skill in job["nice"] if _norm(skill) in user_skills]
            total_weight = 2 * len(job["required"]) + len(job["nice"])
            overlap = (2 * len(required) + len(nice)) / total_weight if total_weight else 0
            skills_score = 0.7 * overlap + 0.3 * float(cosine)
            exp_score = self.engine._experience_score(experience, job)
            location_score = self.engine._location_score(city, job)
            salary_score = self.engine._salary_score(0, job)
            total = (
                0.60 * skills_score
                + 0.20 * exp_score
                + 0.10 * location_score
                + 0.10 * salary_score
            )
            scored.append((round(total * 100), job))

        scored.sort(key=lambda result: result[0], reverse=True)
        return scored[0][1]["id"]


class ApiTests(unittest.TestCase):
    def setUp(self):
        _request_counts.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_config = {
            key: app.config[key]
            for key in (
                "DATABASE",
                "DATABASE_BACKEND",
                "SECRET_KEY",
                "RESEND_API_KEY",
                "EMAIL_FROM",
                "APP_BASE_URL",
                "OTP_HASH_SECRET",
            )
        }
        app.config.update(
            DATABASE=str(Path(self.temp_dir.name) / "users.db"),
            DATABASE_BACKEND="sqlite",
            SECRET_KEY="test-verification-secret",
            RESEND_API_KEY="re_test_key",
            EMAIL_FROM="Pathfinder Jobs <verify@example.test>",
            APP_BASE_URL="http://localhost:8000",
            OTP_HASH_SECRET="test-otp-hash-secret-with-at-least-32-characters",
        )
        init_db()
        migrate_email_verification()
        self.client = app.test_client()
        self.email_sender_patch = patch("app._send_verification_email")
        self.email_sender = self.email_sender_patch.start()

    def tearDown(self):
        self.email_sender_patch.stop()
        app.config.update(self.previous_config)
        self.temp_dir.cleanup()

    def register_and_verify(self, client, email, password="password123", name="Test User"):
        registered = client.post(
            "/api/register",
            json={"name": name, "email": email, "password": password},
        )
        if registered.status_code == 201:
            code = self.email_sender.call_args.args[2]
            verified = client.post("/api/verify-email", json={"email": email, "code": code})
            if verified.status_code == 200:
                client.post(
                    "/api/login",
                    json={"email": email, "password": password},
                )
        return registered

    def sign_in(self):
        return self.register_and_verify(self.client, "student@example.com")

    def test_health(self):
        self.assertEqual(self.client.get("/api/health").get_json()["status"], "ok")

    def test_recommend_ok(self):
        self.sign_in()
        r = self.client.post(
            "/api/recommend",
            json={
                "skills": ["Python", "SQL"],
                "experience": 1,
                "preferred_mode": "Remote",
            },
        )
        data = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(data["results"]), data["total_jobs"])
        self.assertIn("preference", data["results"][0]["parts"])
        self.assertTrue(data["results"][0]["reasons"])
        self.assertTrue(any(job["mode"] == "Onsite" for job in data["results"]))
        remote = next(job for job in data["results"] if job["mode"] == "Remote")
        self.assertEqual(remote["parts"]["preference"], 1.0)

    def test_empty_profile_recommendation_returns_low_score_results(self):
        self.sign_in()
        response = self.client.post("/api/recommend", json={"skills": []})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["total_matches"], data["total_jobs"])
        self.assertTrue(all(result["score"] <= 40 for result in data["results"]))
        self.assertEqual(data["skill_gaps"], [])

    def test_recommend_bad_body(self):
        self.sign_in()
        self.assertEqual(self.client.post("/api/recommend", json={"skills": "python"}).status_code, 400)
        self.assertEqual(self.client.post("/api/recommend", json={"mode": "Moon"}).status_code, 400)
        self.assertEqual(
            self.client.post("/api/recommend", json={"mode": []}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/api/recommend", json={"preferred_mode": "Moon"}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/api/recommend", json={"preferred_mode": []}).status_code,
            400,
        )

    def test_job_not_found(self):
        self.sign_in()
        self.assertEqual(self.client.get("/api/jobs/9999").status_code, 404)

    def test_recommendations_require_login(self):
        self.assertEqual(self.client.get("/api/skills").status_code, 401)
        self.assertEqual(
            self.client.post("/api/recommend", json={"skills": ["Python"]}).status_code,
            401,
        )

    def test_all_user_data_api_routes_require_authentication(self):
        get_paths = (
            "/api/profile",
            "/api/saved-jobs",
            "/api/skills",
            "/api/cities",
            "/api/jobs",
            "/api/jobs/1",
            "/api/applications",
            "/api/applications/1",
            "/api/application-stats",
            "/api/roadmap",
        )
        for path in get_paths:
            with self.subTest(method="GET", path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

        mutation_requests = (
            ("put", "/api/profile", {}),
            ("put", "/api/jobs/1/interaction", {"status": "saved"}),
            ("post", "/api/applications", {"job_id": 1}),
            ("put", "/api/applications/1", {"status": "Rejected"}),
            ("delete", "/api/applications/1", None),
            ("post", "/api/roadmap", {"job_ids": [1]}),
            ("put", "/api/roadmap/progress", {"skill": "Python", "status": "learning"}),
            ("post", "/api/recommend", {"skills": ["Python"]}),
        )
        for method, path, body in mutation_requests:
            with self.subTest(method=method.upper(), path=path):
                response = getattr(self.client, method)(path, json=body)
                self.assertEqual(response.status_code, 401)

    def test_register_login_and_logout(self):
        registered = self.client.post(
            "/api/register",
            json={
                "name": "Student Example",
                "email": "Student@Example.com",
                "password": "password123",
            },
        )
        self.assertEqual(registered.status_code, 201)
        self.assertEqual(registered.get_json()["email"], "student@example.com")
        self.assertTrue(registered.get_json()["verification_required"])
        self.assertFalse(self.client.get("/api/session").get_json()["authenticated"])
        verification_code = self.email_sender.call_args.args[2]
        verified = self.client.post(
            "/api/verify-email",
            json={"email": "student@example.com", "code": verification_code},
        )
        self.assertEqual(verified.status_code, 200)
        self.assertFalse(self.client.get("/api/session").get_json()["authenticated"])
        self.assertEqual(
            self.client.post(
                "/api/login",
                json={"email": "STUDENT@example.com", "password": "password123"},
            ).status_code,
            200,
        )
        self.assertTrue(self.client.get("/api/session").get_json()["authenticated"])
        self.assertEqual(self.client.get("/api/skills").status_code, 200)
        self.assertEqual(self.client.post("/api/logout").status_code, 200)
        self.assertFalse(self.client.get("/api/session").get_json()["authenticated"])
        self.assertEqual(
            self.client.post(
                "/api/login",
                json={"email": "STUDENT@example.com", "password": "password123"},
            ).status_code,
            200,
        )

    def test_registration_requires_verification_before_login_and_protected_access(self):
        response = self.client.post(
            "/api/register",
            json={
                "name": "New User",
                "email": "new-user@example.com",
                "password": "password123",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.get_json()["verification_required"])
        self.assertTrue(response.get_json()["email_sent"])
        with closing(_database_connection()) as db:
            user = db.execute(
                "SELECT id, name, email_verified FROM users WHERE email = ?",
                ("new-user@example.com",),
            ).fetchone()
            verification = db.execute(
                "SELECT otp_hash, expires_at, attempts FROM email_verifications WHERE user_id = ?",
                (user["id"],),
            ).fetchone()
        code = self.email_sender.call_args.args[2]
        self.assertEqual(user["name"], "New User")
        self.assertEqual(user["email_verified"], 0)
        self.assertEqual(len(code), 6)
        self.assertNotEqual(verification["otp_hash"], code)
        self.assertNotIn(code, response.get_data(as_text=True))
        self.assertNotIn(verification["otp_hash"], response.get_data(as_text=True))
        self.assertGreater(verification["expires_at"], int(time.time()))
        self.assertEqual(verification["attempts"], 0)

        login = self.client.post(
            "/api/login",
            json={"email": "new-user@example.com", "password": "password123"},
        )
        self.assertEqual(login.status_code, 403)
        self.assertEqual(login.get_json()["code"], "email_verification_required")
        with self.client.session_transaction() as current_session:
            current_session["user_id"] = user["id"]
            current_session["email"] = "new-user@example.com"
        protected = self.client.get("/api/jobs")
        self.assertEqual(protected.status_code, 403)
        self.assertEqual(protected.get_json()["code"], "email_verification_required")

    def test_verification_is_single_use_and_does_not_create_a_session(self):
        self.client.post(
            "/api/register",
            json={
                "name": "Verify Me",
                "email": "verify@example.com",
                "password": "password123",
            },
        )
        code = self.email_sender.call_args.args[2]
        malformed = self.client.post(
            "/api/verify-email",
            json={"email": "verify@example.com", "code": "12x456"},
        )
        self.assertEqual(malformed.status_code, 400)
        verification = self.client.post(
            "/api/verify-email",
            json={"email": "verify@example.com", "code": code},
        )
        self.assertEqual(verification.status_code, 200)
        self.assertFalse(self.client.get("/api/session").get_json()["authenticated"])
        self.assertEqual(
            self.client.post(
                "/api/verify-email",
                json={"email": "verify@example.com", "code": code},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/login",
                json={"email": "verify@example.com", "password": "password123"},
            ).status_code,
            200,
        )

    def test_wrong_and_expired_verification_codes_are_rejected(self):
        self.client.post(
            "/api/register",
            json={
                "name": "Wrong Code",
                "email": "wrong-code@example.com",
                "password": "password123",
            },
        )
        code = self.email_sender.call_args.args[2]
        for _ in range(VERIFICATION_MAX_ATTEMPTS):
            response = self.client.post(
                "/api/verify-email",
                json={"email": "wrong-code@example.com", "code": "000000" if code != "000000" else "000001"},
            )
            self.assertEqual(response.status_code, 400)
        limited = self.client.post(
            "/api/verify-email",
            json={"email": "wrong-code@example.com", "code": code},
        )
        self.assertEqual(limited.status_code, 429)

        self.client.post(
            "/api/register",
            json={
                "name": "Expired Code",
                "email": "expired@example.com",
                "password": "password123",
            },
        )
        expired_code = self.email_sender.call_args.args[2]
        with closing(_database_connection()) as db, db:
            db.execute(
                """
                UPDATE email_verifications SET expires_at = ?
                WHERE user_id = (SELECT id FROM users WHERE email = ?)
                """,
                (int(time.time()) - 1, "expired@example.com"),
            )
        expired = self.client.post(
            "/api/verify-email",
            json={"email": "expired@example.com", "code": expired_code},
        )
        self.assertEqual(expired.status_code, 400)

    def test_resend_cooldown_rate_limit_and_old_code_invalidation(self):
        self.client.post(
            "/api/register",
            json={
                "name": "Resend Code",
                "email": "resend@example.com",
                "password": "password123",
            },
        )
        first_code = self.email_sender.call_args.args[2]
        cooldown = self.client.post(
            "/api/resend-verification",
            json={"email": "resend@example.com"},
        )
        self.assertEqual(cooldown.status_code, 202)
        self.assertEqual(self.email_sender.call_count, 1)

        with closing(_database_connection()) as db, db:
            db.execute(
                """
                UPDATE email_verifications SET last_sent_at = ?
                WHERE user_id = (SELECT id FROM users WHERE email = ?)
                """,
                (int(time.time()) - 61, "resend@example.com"),
            )
        resent = self.client.post(
            "/api/resend-verification",
            json={"email": "resend@example.com"},
        )
        self.assertEqual(resent.status_code, 202)
        self.assertEqual(self.email_sender.call_count, 2)
        second_code = self.email_sender.call_args.args[2]
        self.assertNotEqual(first_code, second_code)
        self.assertEqual(
            self.client.post(
                "/api/verify-email",
                json={"email": "resend@example.com", "code": first_code},
            ).status_code,
            400,
        )

        with closing(_database_connection()) as db, db:
            db.execute(
                """
                UPDATE email_verifications
                SET last_sent_at = ?, resend_count = ?, resend_window_started_at = ?
                WHERE user_id = (SELECT id FROM users WHERE email = ?)
                """,
                (int(time.time()) - 61, 5, int(time.time()), "resend@example.com"),
            )
        self.client.post(
            "/api/resend-verification",
            json={"email": "resend@example.com"},
        )
        self.assertEqual(self.email_sender.call_count, 2)

    def test_email_delivery_failure_keeps_account_pending_and_allows_resend(self):
        self.email_sender.side_effect = EmailDeliveryError
        response = self.client.post(
            "/api/register",
            json={
                "name": "Delivery Failure",
                "email": "delivery-failure@example.com",
                "password": "password123",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.get_json()["email_sent"])
        with closing(_database_connection()) as db:
            record = db.execute(
                """
                SELECT v.otp_hash FROM email_verifications AS v
                JOIN users AS u ON u.id = v.user_id WHERE u.email = ?
                """,
                ("delivery-failure@example.com",),
            ).fetchone()
        self.assertIsNone(record["otp_hash"])

        self.email_sender.side_effect = None
        with closing(_database_connection()) as db, db:
            db.execute(
                """
                UPDATE email_verifications SET last_sent_at = ?
                WHERE user_id = (SELECT id FROM users WHERE email = ?)
                """,
                (int(time.time()) - 61, "delivery-failure@example.com"),
            )
        resend = self.client.post(
            "/api/resend-verification",
            json={"email": "delivery-failure@example.com"},
        )
        self.assertEqual(resend.status_code, 202)
        self.assertEqual(self.email_sender.call_count, 2)

    def test_existing_unverified_account_can_verify_on_login(self):
        from werkzeug.security import generate_password_hash

        with closing(_database_connection()) as db, db:
            db.execute(
                """
                INSERT INTO users (name, email, password_hash, email_verified)
                VALUES ('', ?, ?, 0)
                """,
                ("legacy@example.com", generate_password_hash("password123")),
            )
        login = self.client.post(
            "/api/login",
            json={"email": "legacy@example.com", "password": "password123"},
        )
        self.assertEqual(login.status_code, 403)
        self.assertTrue(login.get_json()["email_sent"])
        code = self.email_sender.call_args.args[2]
        self.assertEqual(
            self.client.post(
                "/api/verify-email",
                json={"email": "legacy@example.com", "code": code},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/login",
                json={"email": "legacy@example.com", "password": "password123"},
            ).status_code,
            200,
        )

    def test_pending_code_survives_new_client_and_repeat_migration(self):
        self.client.post(
            "/api/register",
            json={
                "name": "Persistent OTP",
                "email": "persistent@example.com",
                "password": "password123",
            },
        )
        code = self.email_sender.call_args.args[2]
        migrate_email_verification()
        restarted_client = app.test_client()
        response = restarted_client.post(
            "/api/verify-email",
            json={"email": "persistent@example.com", "code": code},
        )
        self.assertEqual(response.status_code, 200)

    def test_resend_email_uses_resend_https_api_and_branded_payload(self):
        from unittest.mock import MagicMock

        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch("app.urllib.request.urlopen", return_value=response) as urlopen:
            _send_verification_email("person@example.test", "A <User>", "012345")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer re_test_key")
        payload = json.loads(request.data)
        self.assertEqual(payload["to"], ["person@example.test"])
        self.assertIn("Pathfinder", payload["html"])
        self.assertIn("A &lt;User&gt;", payload["html"])
        self.assertIn("012345", payload["text"])

    def test_csrf_tokens_guard_registration_login_logout_and_other_mutations(self):
        origin = "http://localhost:8000"
        payload = {"name": "CSRF User", "email": "csrf@example.com", "password": "password123"}
        self.assertEqual(
            self.client.post(
                "/api/register",
                json=payload,
                headers={"Origin": origin},
            ).status_code,
            403,
        )

        csrf = self.client.get("/api/csrf").get_json()["token"]
        valid_headers = {"Origin": origin, "X-CSRF-Token": csrf}
        self.assertEqual(
            self.client.post(
                "/api/register",
                json=payload,
                headers={**valid_headers, "X-CSRF-Token": "invalid"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post("/api/register", json=payload, headers=valid_headers).status_code,
            201,
        )

        verification_code = self.email_sender.call_args.args[2]
        self.assertEqual(
            self.client.post(
                "/api/verify-email",
                json={"email": payload["email"], "code": verification_code},
                headers=valid_headers,
            ).status_code,
            200,
        )
        authenticated_token = self.client.get("/api/csrf").get_json()["token"]
        self.assertNotEqual(authenticated_token, csrf)
        self.assertEqual(
            self.client.post(
                "/api/logout",
                headers={"Origin": origin, "X-CSRF-Token": authenticated_token},
            ).status_code,
            200,
        )

        anonymous_token = self.client.get("/api/csrf").get_json()["token"]
        login = self.client.post(
            "/api/login",
            json=payload,
            headers={"Origin": origin, "X-CSRF-Token": anonymous_token},
        )
        self.assertEqual(login.status_code, 200)

        login_token = self.client.get("/api/csrf").get_json()["token"]
        recommendation = self.client.post(
            "/api/recommend",
            json={"skills": ["Python"]},
            headers={"Origin": origin, "X-CSRF-Token": login_token},
        )
        self.assertEqual(recommendation.status_code, 200)

    def test_authenticated_sessions_are_permanent_and_cookie_is_http_only(self):
        self.register_and_verify(self.client, "session@example.com")
        response = self.client.post(
            "/api/login",
            json={"email": "session@example.com", "password": "password123"},
        )
        self.assertIn("HttpOnly", response.headers.get("Set-Cookie", ""))
        self.assertIn("SameSite=Lax", response.headers.get("Set-Cookie", ""))
        self.assertIn("Expires=", response.headers.get("Set-Cookie", ""))
        with self.client.session_transaction() as current_session:
            self.assertTrue(current_session.permanent)

    def test_password_is_stored_as_a_scrypt_hash(self):
        self.sign_in()
        with closing(sqlite3.connect(app.config["DATABASE"])) as db:
            password_hash = db.execute(
                "SELECT password_hash FROM users WHERE email = ?",
                ("student@example.com",),
            ).fetchone()[0]
        self.assertTrue(password_hash.startswith("scrypt:"))
        self.assertNotEqual(password_hash, "password123")
        self.assertTrue(check_password_hash(password_hash, "password123"))
        self.assertFalse(check_password_hash(password_hash, "incorrect"))

    def test_registration_validation_and_duplicate_email(self):
        invalid = self.client.post(
            "/api/register",
            json={"name": "Invalid", "email": "not-an-email", "password": "short"},
        )
        self.assertEqual(invalid.status_code, 400)
        payload = {
            "name": "Student Example",
            "email": "student@example.com",
            "password": "password123",
        }
        self.assertEqual(self.client.post("/api/register", json=payload).status_code, 201)
        self.assertEqual(self.client.post("/api/register", json=payload).status_code, 409)

    def test_login_rejects_wrong_password(self):
        self.register_and_verify(self.client, "student@example.com")
        self.client.post("/api/logout")
        response = self.client.post(
            "/api/login",
            json={"email": "student@example.com", "password": "wrongpass"},
        )
        self.assertEqual(response.status_code, 401)

    def test_profile_and_job_interaction_persist(self):
        self.sign_in()
        profile = self.client.put(
            "/api/profile",
            json={"skills": ["Python", "SQL"], "exp": 2, "city": "Bengaluru", "mode": "Hybrid", "sal": 10},
        )
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(
            self.client.get("/api/profile").get_json()["profile"]["skills"],
            ["Python", "SQL"],
        )
        saved = self.client.put(
            "/api/jobs/1/interaction",
            json={"status": "saved"},
        )
        self.assertEqual(saved.status_code, 200)
        interactions = self.client.get("/api/saved-jobs").get_json()["interactions"]
        self.assertEqual(len(interactions), 1)
        self.assertEqual(interactions[0]["id"], 1)
        self.assertEqual(
            self.client.delete("/api/jobs/1/interaction").status_code,
            200,
        )

    def test_malformed_profile_interaction_and_recommendation_inputs_return_400(self):
        self.sign_in()
        invalid_requests = (
            ("/api/profile", "put", {"skills": None}),
            ("/api/profile", "put", {"mode": []}),
            ("/api/jobs/1/interaction", "put", {"status": []}),
            ("/api/recommend", "post", {"sort": []}),
            ("/api/recommend", "post", {"city": []}),
            ("/api/recommend", "post", {"experience": True}),
        )
        for path, method, body in invalid_requests:
            with self.subTest(path=path, body=body):
                response = getattr(self.client, method)(path, json=body)
                self.assertEqual(response.status_code, 400)

    def test_database_connections_enforce_foreign_keys(self):
        self.sign_in()
        db = _database_connection()
        try:
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO skill_learning_progress (user_id, skill, status) VALUES (?, ?, ?)",
                    (9999, "Python", "learning"),
                )
        finally:
            db.close()

    def test_application_crud_filters_and_stats(self):
        self.sign_in()
        created = self.client.post(
            "/api/applications",
            json={
                "job_id": 1,
                "status": "Applied",
                "application_date": "2026-03-12",
                "notes": "Follow up next week",
            },
        )
        self.assertEqual(created.status_code, 201)
        application = created.get_json()["application"]
        application_id = application["id"]
        self.assertEqual(application["job"]["id"], 1)
        self.assertEqual(application["notes"], "Follow up next week")
        self.assertEqual(
            self.client.post("/api/applications", json={"job_id": 1}).status_code,
            409,
        )
        self.assertEqual(
            self.client.get("/api/applications").get_json()["applications"][0]["id"],
            application_id,
        )
        self.assertEqual(
            self.client.get("/api/applications?status=Applied&start_date=2026-03-01&end_date=2026-03-31")
            .get_json()["applications"][0]["id"],
            application_id,
        )
        self.assertEqual(
            self.client.get("/api/applications?status=Interview").get_json()["applications"],
            [],
        )
        self.assertEqual(self.client.get("/api/application-stats").get_json()["total"], 1)
        self.assertEqual(
            self.client.get(f"/api/applications/{application_id}").status_code,
            200,
        )

        updated = self.client.put(
            f"/api/applications/{application_id}",
            json={
                "status": "Interview",
                "application_date": "2026-03-15",
                "notes": "Interview scheduled",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["application"]["status"], "Interview")
        self.assertEqual(
            self.client.get("/api/application-stats").get_json()["statuses"]["Interview"],
            1,
        )
        deleted = self.client.delete(f"/api/applications/{application_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/api/applications").get_json()["applications"], [])
        self.assertEqual(self.client.delete(f"/api/applications/{application_id}").status_code, 404)

    def test_application_validation_and_authentication(self):
        self.assertEqual(self.client.get("/api/applications").status_code, 401)
        self.assertEqual(self.client.get("/api/application-stats").status_code, 401)
        self.assertEqual(
            self.client.post("/api/applications", json={"job_id": 1}).status_code,
            401,
        )
        self.assertEqual(self.client.get("/api/applications/1").status_code, 401)
        self.assertEqual(
            self.client.put("/api/applications/1", json={"status": "Rejected"}).status_code,
            401,
        )
        self.assertEqual(self.client.delete("/api/applications/1").status_code, 401)
        self.sign_in()
        invalid_payloads = [
            {"job_id": True},
            {"job_id": 9999},
            {"job_id": 1, "status": "In progress"},
            {"job_id": 1, "application_date": "2026-02-30"},
            {"job_id": 1, "notes": "x" * 2001},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self.assertIn(self.client.post("/api/applications", json=payload).status_code, {400, 404})
        self.assertEqual(self.client.get("/api/applications?status=Unknown").status_code, 400)
        self.assertEqual(self.client.get("/api/applications?start_date=2026-99-99").status_code, 400)
        self.assertEqual(
            self.client.get("/api/applications?start_date=2026-03-02&end_date=2026-03-01").status_code,
            400,
        )
        application_id = self.client.post(
            "/api/applications",
            json={"job_id": 1},
        ).get_json()["application"]["id"]
        for invalid_update in (
            {"status": "Unknown"},
            {"application_date": "2026-02-30"},
            {"notes": "x" * 2001},
        ):
            with self.subTest(invalid_update=invalid_update):
                self.assertEqual(
                    self.client.put(f"/api/applications/{application_id}", json=invalid_update).status_code,
                    400,
                )

    def test_applications_are_isolated_between_users(self):
        self.sign_in()
        created = self.client.post("/api/applications", json={"job_id": 1})
        application_id = created.get_json()["application"]["id"]

        other_user = app.test_client()
        self.assertEqual(
            self.register_and_verify(other_user, "other@example.com").status_code,
            201,
        )
        self.assertEqual(other_user.get("/api/applications").get_json()["applications"], [])
        self.assertEqual(other_user.get(f"/api/applications/{application_id}").status_code, 404)
        self.assertEqual(
            other_user.put(f"/api/applications/{application_id}", json={"status": "Rejected"}).status_code,
            404,
        )
        self.assertEqual(other_user.delete(f"/api/applications/{application_id}").status_code, 404)
        self.assertEqual(
            other_user.get("/api/application-stats").get_json()["total"],
            0,
        )

    def test_saving_same_job_is_idempotent(self):
        self.sign_in()
        for _ in range(2):
            self.assertEqual(
                self.client.put("/api/jobs/1/interaction", json={"status": "saved"}).status_code,
                200,
            )
        interactions = self.client.get("/api/saved-jobs").get_json()["interactions"]
        self.assertEqual(len([job for job in interactions if job["id"] == 1]), 1)

    def test_roadmap_prioritizes_job_gaps_and_recalculates_completed_skills(self):
        self.sign_in()
        self.client.put(
            "/api/profile",
            json={"skills": ["Python", "HTML", "CSS"]},
        )
        response = self.client.post(
            "/api/roadmap",
            json={"job_ids": [6], "skills": ["Python", "HTML", "CSS"]},
        )
        self.assertEqual(response.status_code, 200)
        roadmap = response.get_json()
        self.assertEqual(roadmap["roles"][0]["title"], "Data Scientist")
        self.assertEqual(roadmap["roles"][0]["matched_required"], 1)
        skills = {step["skill"]: step for step in roadmap["steps"]}
        self.assertTrue(
            {"Statistics", "Machine Learning", "SQL", "NumPy", "Pandas"}.issubset(skills)
        )
        self.assertEqual(skills["NumPy"]["stage"], "Beginner")
        self.assertEqual(skills["Machine Learning"]["stage"], "Intermediate")
        self.assertGreater(skills["Statistics"]["estimated_hours"], 0)
        self.assertGreater(skills["SQL"]["priority"], skills["TensorFlow"]["priority"])
        self.assertIn("rough self-study estimates", roadmap["estimate_note"])
        self.assertTrue(skills["Statistics"]["resource"]["url"].startswith("https://"))
        initial_compatibility = roadmap["roles"][0]["compatibility"]

        self.assertEqual(
            self.client.put(
                "/api/roadmap/progress",
                json={"skill": "NumPy", "status": "learning"},
            ).status_code,
            200,
        )
        completed = self.client.put(
            "/api/roadmap/progress",
            json={"skill": "Machine Learning", "status": "completed"},
        )
        self.assertEqual(completed.status_code, 200)
        self.assertIn("Machine Learning", completed.get_json()["profile_skills"])
        updated = self.client.get("/api/roadmap").get_json()
        self.assertEqual(updated["selected_job_ids"], [6])
        self.assertGreater(updated["roles"][0]["compatibility"], initial_compatibility)
        updated_steps = {step["skill"]: step for step in updated["steps"]}
        self.assertEqual(updated_steps["Machine Learning"]["status"], "completed")
        self.assertEqual(updated_steps["NumPy"]["status"], "learning")
        self.assertEqual(updated["stats"]["completed_count"], 1)
        self.assertEqual(updated["stats"]["learning_count"], 1)
        self.assertEqual(
            self.client.put(
                "/api/roadmap/progress",
                json={"skill": "NumPy", "status": "planned"},
            ).get_json()["progress"]["status"],
            "planned",
        )

    def test_roadmap_validation_authentication_and_user_isolation(self):
        self.assertEqual(self.client.get("/api/roadmap").status_code, 401)
        self.assertEqual(
            self.client.post("/api/roadmap", json={"job_ids": [6]}).status_code,
            401,
        )
        self.assertEqual(
            self.client.put(
                "/api/roadmap/progress",
                json={"skill": "SQL", "status": "learning"},
            ).status_code,
            401,
        )

        self.sign_in()
        self.assertEqual(self.client.post("/api/roadmap", json={}).status_code, 400)
        self.assertEqual(
            self.client.post("/api/roadmap", json={"job_ids": [True]}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/api/roadmap", json={"job_ids": [9999]}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/roadmap",
                json={"job_ids": [6], "skills": ["x" * 81]},
            ).status_code,
            400,
        )
        for invalid_status in ("started", [], None):
            with self.subTest(status=invalid_status):
                self.assertEqual(
                    self.client.put(
                        "/api/roadmap/progress",
                        json={"skill": "SQL", "status": invalid_status},
                    ).status_code,
                    400,
                )
        self.assertEqual(
            self.client.put(
                "/api/roadmap/progress",
                json={"skill": "Made Up Skill", "status": "learning"},
            ).status_code,
            400,
        )

        self.client.post(
            "/api/roadmap",
            json={"job_ids": [6], "skills": ["Python"]},
        )
        self.client.put(
            "/api/roadmap/progress",
            json={"skill": "Statistics", "status": "learning"},
        )
        other_user = app.test_client()
        self.assertEqual(
            self.register_and_verify(other_user, "roadmap-other@example.com").status_code,
            201,
        )
        other_roadmap = other_user.get("/api/roadmap").get_json()
        self.assertEqual(other_roadmap["selected_job_ids"], [])
        self.assertEqual(other_roadmap["steps"], [])
        other_user.post(
            "/api/roadmap",
            json={"job_ids": [6], "skills": ["Python"]},
        )
        other_steps = {
            step["skill"]: step
            for step in other_user.get("/api/roadmap").get_json()["steps"]
        }
        self.assertEqual(other_steps["Statistics"]["status"], "planned")
        own_steps = {
            step["skill"]: step
            for step in self.client.get("/api/roadmap").get_json()["steps"]
        }
        self.assertEqual(own_steps["Statistics"]["status"], "learning")


if __name__ == "__main__":
    unittest.main()
