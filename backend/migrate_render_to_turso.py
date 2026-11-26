import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from app import _database_connection, app, init_db


TABLES = (
    "users",
    "profiles",
    "job_interactions",
    "applications",
    "roadmap_preferences",
    "skill_learning_progress",
)


def _quoted_identifier(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def migrate(source_path):
    source_path = Path(source_path).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite source database does not exist: {source_path}")
    if not app.config["TURSO_DATABASE_URL"] or not app.config["TURSO_AUTH_TOKEN"]:
        raise RuntimeError("Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN before migrating.")

    source_uri = source_path.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source:
        source.execute("BEGIN")
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Source SQLite integrity check failed: {integrity}")

        source_tables = {
            row[0]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        tables = [table for table in TABLES if table in source_tables]
        if "users" not in tables:
            raise RuntimeError("Source database does not contain the users table.")

        source_counts = {
            table: source.execute(
                f"SELECT COUNT(*) FROM {_quoted_identifier(table)}"
            ).fetchone()[0]
            for table in tables
        }
        if source_counts["users"] == 0:
            raise RuntimeError(
                "The source users table is empty; verify the Render DATABASE_PATH."
            )

        previous_backend = app.config["DATABASE_BACKEND"]
        app.config["DATABASE_BACKEND"] = "turso"
        try:
            init_db()
            with closing(_database_connection()) as target:
                target_counts = {
                    table: target.execute(
                        f"SELECT COUNT(*) FROM {_quoted_identifier(table)}"
                    ).fetchone()[0]
                    for table in TABLES
                }
                occupied = [table for table, count in target_counts.items() if count]
                if occupied:
                    raise RuntimeError(
                        "Refusing to migrate into non-empty Turso tables: "
                        + ", ".join(occupied)
                    )

                source_columns = {}
                for table in tables:
                    columns = [
                        row[1]
                        for row in source.execute(
                            f"PRAGMA table_info({_quoted_identifier(table)})"
                        )
                    ]
                    target_columns = {
                        row[1]
                        for row in target.execute(
                            f"PRAGMA table_info({_quoted_identifier(table)})"
                        )
                    }
                    unsupported = sorted(set(columns) - target_columns)
                    if unsupported:
                        raise RuntimeError(
                            f"Source table {table!r} has unsupported columns: "
                            + ", ".join(unsupported)
                        )
                    source_columns[table] = columns

                with target:
                    for table in tables:
                        columns = source_columns[table]
                        column_sql = ", ".join(_quoted_identifier(c) for c in columns)
                        placeholders = ", ".join("?" for _ in columns)
                        insert_sql = (
                            f"INSERT INTO {_quoted_identifier(table)} "
                            f"({column_sql}) VALUES ({placeholders})"
                        )
                        select_sql = (
                            f"SELECT {column_sql} FROM {_quoted_identifier(table)}"
                        )
                        cursor = source.execute(select_sql)
                        while rows := cursor.fetchmany(100):
                            target.executemany(insert_sql, rows)

                    copied_counts = {
                        table: target.execute(
                            f"SELECT COUNT(*) FROM {_quoted_identifier(table)}"
                        ).fetchone()[0]
                        for table in tables
                    }
                    if copied_counts != source_counts:
                        raise RuntimeError(
                            f"Row-count verification failed: source={source_counts}, "
                            f"Turso={copied_counts}"
                        )
        finally:
            app.config["DATABASE_BACKEND"] = previous_backend

    for table, count in source_counts.items():
        print(f"{table}: {count} rows copied")
    for table in TABLES:
        if table not in source_counts:
            print(f"{table}: not present in source; skipped")


def main():
    parser = argparse.ArgumentParser(
        description="Copy the Render SQLite user database into an empty Turso database."
    )
    parser.add_argument(
        "--source",
        default=app.config["DATABASE"],
        help="Path to the Render SQLite database (defaults to DATABASE_PATH).",
    )
    args = parser.parse_args()
    migrate(args.source)


if __name__ == "__main__":
    main()
