import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import turso_serverless

from app import _database_connection, app, init_db
from migrate_render_to_turso import migrate


class TursoMigrationTests(unittest.TestCase):
    def test_turso_connection_uses_dbapi_rows_and_enables_foreign_keys(self):
        original_config = {
            key: app.config[key]
            for key in ("DATABASE_BACKEND", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN")
        }
        app.config.update(
            DATABASE_BACKEND="turso",
            TURSO_DATABASE_URL="turso://connection-test",
            TURSO_AUTH_TOKEN="test-token",
        )
        try:
            connection = sqlite3.connect(":memory:")
            with patch(
                "turso_serverless.connect",
                return_value=connection,
            ) as connect:
                remote = _database_connection()
                self.assertIs(remote.row_factory, turso_serverless.Row)
                self.assertEqual(
                    remote.execute("PRAGMA foreign_keys").fetchone()[0],
                    1,
                )
                connect.assert_called_once_with(
                    "turso://connection-test",
                    auth_token="test-token",
                )
                remote.close()

            app.config["TURSO_AUTH_TOKEN"] = ""
            with self.assertRaisesRegex(RuntimeError, "TURSO_DATABASE_URL"):
                _database_connection()
        finally:
            app.config.update(original_config)

    def test_copies_render_sqlite_rows_and_refuses_a_second_import(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "render.db"
            target_path = Path(temp_dir) / "turso.db"
            original_config = {
                key: app.config[key]
                for key in ("DATABASE", "DATABASE_BACKEND", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN")
            }
            app.config.update(
                DATABASE=str(source_path),
                DATABASE_BACKEND="sqlite",
                TURSO_DATABASE_URL="turso://migration-test",
                TURSO_AUTH_TOKEN="test-token",
            )
            try:
                init_db()
                with closing(sqlite3.connect(source_path)) as source, source:
                    source.execute(
                        "INSERT INTO users (id, email, password_hash) VALUES (7, ?, ?)",
                        ("learner@example.test", "hashed-password"),
                    )
                    source.execute(
                        "INSERT INTO profiles (user_id, data) VALUES (7, ?)",
                        ('{"skills":["Python"]}',),
                    )
                    source.execute(
                        "INSERT INTO job_interactions (user_id, job_id, status) VALUES (7, 1, 'saved')"
                    )
                    source.execute(
                        """
                        INSERT INTO applications
                            (id, user_id, job_id, status, application_date, notes)
                        VALUES (11, 7, 1, 'Applied', '2026-11-24', 'Follow up')
                        """
                    )
                    source.execute(
                        "INSERT INTO roadmap_preferences (user_id, job_ids) VALUES (7, '[1]')"
                    )
                    source.execute(
                        """
                        INSERT INTO skill_learning_progress (user_id, skill, status)
                        VALUES (7, 'SQL', 'learning')
                        """
                    )

                with patch(
                    "turso_serverless.connect",
                    side_effect=lambda _url, auth_token: sqlite3.connect(target_path),
                ) as connect:
                    migrate(source_path)
                    with self.assertRaisesRegex(RuntimeError, "non-empty Turso tables"):
                        migrate(source_path)

                self.assertGreaterEqual(connect.call_count, 4)
                with closing(sqlite3.connect(target_path)) as target:
                    self.assertEqual(
                        target.execute("SELECT email FROM users WHERE id = 7").fetchone()[0],
                        "learner@example.test",
                    )
                    self.assertEqual(
                        target.execute(
                            "SELECT notes FROM applications WHERE id = 11"
                        ).fetchone()[0],
                        "Follow up",
                    )
                    for table in (
                        "users",
                        "profiles",
                        "job_interactions",
                        "applications",
                        "roadmap_preferences",
                        "skill_learning_progress",
                    ):
                        self.assertEqual(
                            target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                            1,
                            table,
                        )
            finally:
                app.config.update(original_config)


if __name__ == "__main__":
    unittest.main()
