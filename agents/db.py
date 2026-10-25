"""Database connections for the legacy backend and RDS PostgreSQL."""

import contextlib
import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from agents.config import settings


def get_connection() -> psycopg.Connection:
    """Connect with verified TLS; fetch managed RDS credentials without logging them."""
    if settings.db_backend == "rds_postgresql":
        import boto3

        if not settings.rds_host or not settings.rds_database or not settings.rds_secret_arn:
            raise ValueError("RDS host, database, and secret ARN must be configured")
        certificate = Path(settings.rds_sslrootcert).expanduser()
        if not certificate.is_absolute():
            certificate = Path(__file__).resolve().parent.parent / certificate
        if not settings.rds_sslrootcert or not certificate.is_file():
            raise ValueError("RDS certificate bundle must point to an existing file")
        response = boto3.client(
            "secretsmanager", region_name=settings.aws_region
        ).get_secret_value(SecretId=settings.rds_secret_arn)
        try:
            credentials = json.loads(response["SecretString"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("RDS secret must contain a JSON credentials object") from None
        if not isinstance(credentials, dict) or any(
            not isinstance(credentials.get(key), str) or not credentials[key]
            for key in ("username", "password")
        ):
            raise ValueError("RDS secret must contain nonempty username and password strings")
        return psycopg.connect(
            host=settings.rds_host,
            port=settings.rds_port,
            dbname=settings.rds_database,
            user=credentials["username"],
            password=credentials["password"],
            sslmode="verify-full",
            sslrootcert=str(certificate),
            connect_timeout=settings.db_connect_timeout,
            row_factory=dict_row,
        )
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
