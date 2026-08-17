"""Apply database migrations to CockroachDB in sequential order.

Usage:
    python data/migrations/apply.py
    python data/migrations/apply.py --dry-run
"""

import argparse
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from agents.config import settings


MIGRATIONS_DIR = Path(__file__).parent


def get_migration_files() -> list[Path]:
    """Get all SQL migration files in order."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda p: p.name)


def create_migrations_table(conn: psycopg.Connection) -> None:
    """Create the schema_migrations tracking table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id SERIAL PRIMARY KEY,
            filename STRING NOT NULL UNIQUE,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    conn.commit()


def get_applied_migrations(conn: psycopg.Connection) -> set[str]:
    """Get set of already-applied migration filenames."""
    result = conn.execute("SELECT filename FROM schema_migrations")
    return {row["filename"] for row in result.fetchall()}


def apply_migration(conn: psycopg.Connection, filepath: Path) -> None:
    """Apply a single migration file."""
    sql = filepath.read_text(encoding="utf-8")
    conn.execute(sql)
    conn.execute(
        "INSERT INTO schema_migrations (filename) VALUES (%s)",
        (filepath.name,),
    )
    conn.commit()


def main() -> None:
    """Run pending migrations."""
    parser = argparse.ArgumentParser(description="Apply CockroachDB schema migrations")
    parser.add_argument("--dry-run", action="store_true", help="Show pending migrations without applying")
    args = parser.parse_args()

    print("=" * 50)
    print("SRE Memory Agent - Schema Migrations")
    print("=" * 50)
    print(f"Database: {settings.cockroachdb_host}:{settings.cockroachdb_port}/{settings.cockroachdb_database}")
    print()

    migration_files = get_migration_files()
    print(f"Found {len(migration_files)} migration file(s)")

    try:
        conn = psycopg.connect(settings.cockroachdb_dsn, row_factory=dict_row)
    except Exception as e:
        print(f"ERROR: Could not connect to CockroachDB: {e}")
        print("Ensure your .env is configured and the cluster is running.")
        sys.exit(1)

    with conn:
        create_migrations_table(conn)
        applied = get_applied_migrations(conn)

        pending = [f for f in migration_files if f.name not in applied]
        if not pending:
            print("All migrations are already applied.")
            return

        print(f"{len(pending)} pending migration(s):")
        for f in pending:
            print(f"  - {f.name}")

        if args.dry_run:
            print("\n(dry-run mode — no changes applied)")
            return

        print()
        for filepath in pending:
            print(f"Applying {filepath.name}...", end=" ")
            try:
                apply_migration(conn, filepath)
                print("OK")
            except Exception as e:
                print(f"FAILED: {e}")
                sys.exit(1)

        print()
        print(f"Successfully applied {len(pending)} migration(s).")

    conn.close()


if __name__ == "__main__":
    main()
