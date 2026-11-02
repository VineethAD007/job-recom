import unittest
import tempfile
from pathlib import Path

from app import app, init_db
from recommender import Recommender


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


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_database = app.config["DATABASE"]
        app.config["DATABASE"] = str(Path(self.temp_dir.name) / "users.db")
        init_db()
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE"] = self.previous_database
        self.temp_dir.cleanup()

    def sign_in(self):
        return self.client.post(
            "/api/register",
            json={"email": "student@example.com", "password": "password123"},
        )

    def test_health(self):
        self.assertEqual(self.client.get("/api/health").get_json()["status"], "ok")

    def test_recommend_ok(self):
        self.sign_in()
        r = self.client.post("/api/recommend", json={"skills": ["Python", "SQL"], "experience": 1})
        data = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(data["results"]), data["total_jobs"])

    def test_recommend_bad_body(self):
        self.sign_in()
        self.assertEqual(self.client.post("/api/recommend", json={"skills": "python"}).status_code, 400)
        self.assertEqual(self.client.post("/api/recommend", json={"mode": "Moon"}).status_code, 400)

    def test_job_not_found(self):
        self.sign_in()
        self.assertEqual(self.client.get("/api/jobs/9999").status_code, 404)

    def test_recommendations_require_login(self):
        self.assertEqual(self.client.get("/api/skills").status_code, 401)
        self.assertEqual(
            self.client.post("/api/recommend", json={"skills": ["Python"]}).status_code,
            401,
        )

    def test_register_login_and_logout(self):
        registered = self.client.post(
            "/api/register",
            json={"email": "Student@Example.com", "password": "password123"},
        )
        self.assertEqual(registered.status_code, 201)
        self.assertEqual(registered.get_json()["email"], "student@example.com")
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

    def test_registration_validation_and_duplicate_email(self):
        invalid = self.client.post(
            "/api/register",
            json={"email": "not-an-email", "password": "short"},
        )
        self.assertEqual(invalid.status_code, 400)
        payload = {"email": "student@example.com", "password": "password123"}
        self.assertEqual(self.client.post("/api/register", json=payload).status_code, 201)
        self.assertEqual(self.client.post("/api/register", json=payload).status_code, 409)

    def test_login_rejects_wrong_password(self):
        self.client.post(
            "/api/register",
            json={"email": "student@example.com", "password": "password123"},
        )
        self.client.post("/api/logout")
        response = self.client.post(
            "/api/login",
            json={"email": "student@example.com", "password": "wrongpass"},
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
