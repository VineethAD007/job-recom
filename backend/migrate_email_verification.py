from contextlib import closing

from app import _database_connection, init_db


def migrate():
    init_db()
    with closing(_database_connection()) as db, db:
        columns = {
            row[1]
            for row in db.execute('PRAGMA table_info("users")').fetchall()
        }
        if "name" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN name TEXT NOT NULL DEFAULT ''")
        if "email_verified" not in columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0"
            )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS email_verifications (
                user_id INTEGER PRIMARY KEY,
                otp_hash TEXT,
                expires_at INTEGER,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_sent_at INTEGER,
                resend_window_started_at INTEGER NOT NULL DEFAULT 0,
                resend_count INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
    return {"added_columns": sorted({"name", "email_verified"} - columns)}


if __name__ == "__main__":
    result = migrate()
    print("Email verification schema is ready.")
    if result["added_columns"]:
        print("Added user columns: " + ", ".join(result["added_columns"]))
    else:
        print("User columns were already present.")
