import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import app, init_db
from migrate_email_verification import migrate


class EmailVerificationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_database = app.config["DATABASE"]
        self.previous_backend = app.config["DATABASE_BACKEND"]
        app.config["DATABASE_BACKEND"] = "sqlite"
        app.config["DATABASE"] = str(Path(self.temp_dir.name) / "legacy.db")
        init_db()
        with closing(sqlite3.connect(app.config["DATABASE"])) as db, db:
            db.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
                (17, "existing@example.test", "existing-password-hash"),
            )

    def tearDown(self):
        app.config["DATABASE"] = self.previous_database
        app.config["DATABASE_BACKEND"] = self.previous_backend
        self.temp_dir.cleanup()

    def test_existing_users_are_preserved_and_require_verification(self):
        result = migrate()
        self.assertEqual(result["added_columns"], ["email_verified", "name"])

        with closing(sqlite3.connect(app.config["DATABASE"])) as db:
            user = db.execute(
                """
                SELECT id, email, password_hash, name, email_verified
                FROM users WHERE id = ?
                """,
                (17,),
            ).fetchone()
            verification_table = db.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'email_verifications'
                """
            ).fetchone()
        self.assertEqual(
            user,
            (17, "existing@example.test", "existing-password-hash", "", 0),
        )
        self.assertIsNotNone(verification_table)

    def test_migration_is_repeatable_without_changing_user_data(self):
        migrate()
        self.assertEqual(migrate()["added_columns"], [])
        with closing(sqlite3.connect(app.config["DATABASE"])) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute(
                    "SELECT email_verified FROM users WHERE id = ?", (17,)
                ).fetchone()[0],
                0,
            )


if __name__ == "__main__":
    unittest.main()
