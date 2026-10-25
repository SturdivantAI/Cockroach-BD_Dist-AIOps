"""Initialize a fresh PostgreSQL database; refuse to overwrite existing tables."""

from pathlib import Path

from agents.config import settings
from agents.db import get_connection


def main() -> None:
    if settings.db_backend != "rds_postgresql":
        raise ValueError("Set DB_BACKEND=rds_postgresql for this schema")
    schema = Path(__file__).parent / "migrations/postgresql/001_initial.sql"
    with get_connection() as conn:
        conn.autocommit = True
        existing = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s", ("public",)
        ).fetchall()
        if existing:
            raise RuntimeError("Database already contains tables; initialization refused")
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(schema.read_text(encoding="utf-8"))
    print("PostgreSQL schema initialized; no fixtures or operational data loaded")


if __name__ == "__main__":
    main()
