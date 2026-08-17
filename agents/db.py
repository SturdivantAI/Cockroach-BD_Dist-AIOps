"""Database connection management for CockroachDB."""

import contextlib
from collections.abc import Generator
from typing import Any

import psycopg
from psycopg.rows import dict_row

from agents.config import settings


def get_connection() -> psycopg.Connection:
    """Create a new database connection to CockroachDB."""
    return psycopg.connect(settings.cockroachdb_dsn, row_factory=dict_row)


@contextlib.contextmanager
def get_cursor() -> Generator[psycopg.Cursor[dict[str, Any]], None, None]:
    """Context manager that yields a database cursor with automatic commit/rollback."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            yield cur
            conn.commit()


def execute_query(query: str, params: tuple | None = None) -> list[dict[str, Any]]:
    """Execute a query and return all results as dictionaries."""
    with get_cursor() as cur:
        cur.execute(query, params)
        if cur.description:
            return cur.fetchall()
        return []


def execute_command(query: str, params: tuple | None = None) -> int:
    """Execute a command (INSERT/UPDATE/DELETE) and return rows affected."""
    with get_cursor() as cur:
        cur.execute(query, params)
        return cur.rowcount
