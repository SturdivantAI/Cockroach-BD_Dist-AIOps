"""Secrets management for production deployment.

In production (Lambda), credentials are loaded from AWS Secrets Manager.
In local development, they come from .env file via pydantic-settings.

This module provides a unified interface that works in both environments.
"""

import json
import logging
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_cockroachdb_credentials() -> dict[str, Any]:
    """Retrieve CockroachDB credentials.

    Priority:
    1. AWS Secrets Manager (when COCKROACHDB_SECRET_ARN is set)
    2. Environment variables / .env file (local development)

    Returns:
        Dict with host, port, database, user, password, sslmode.
    """
    import os

    secret_arn = os.environ.get("COCKROACHDB_SECRET_ARN")

    if secret_arn:
        return _get_from_secrets_manager(secret_arn)

    # Fall back to environment / .env
    from agents.config import settings
    return {
        "host": settings.cockroachdb_host,
        "port": settings.cockroachdb_port,
        "database": settings.cockroachdb_database,
        "user": settings.cockroachdb_user,
        "password": settings.cockroachdb_password,
        "sslmode": settings.cockroachdb_ssl_mode,
    }


def _get_from_secrets_manager(secret_arn: str) -> dict[str, Any]:
    """Retrieve credentials from AWS Secrets Manager.

    Args:
        secret_arn: ARN of the secret containing CockroachDB credentials.

    Returns:
        Parsed secret as dict.
    """
    import boto3

    client = boto3.client("secretsmanager")

    try:
        response = client.get_secret_value(SecretId=secret_arn)
        secret_string = response["SecretString"]
        return json.loads(secret_string)
    except Exception as e:
        logger.error("Failed to retrieve secret from Secrets Manager: %s", e)
        raise


def mask_secret(value: str, show_chars: int = 4) -> str:
    """Mask a secret value for logging purposes.

    Args:
        value: Secret string to mask.
        show_chars: Number of characters to show at the end.

    Returns:
        Masked string like '***abcd'.
    """
    if not value or len(value) <= show_chars:
        return "***"
    return "***" + value[-show_chars:]
