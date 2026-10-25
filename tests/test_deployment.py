"""Hosted live access and non-destructive schema initialization."""

import ssl
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from data import bootstrap_postgresql
from frontend.access import live_access_allowed


@pytest.mark.parametrize("expected,supplied,allowed", [
    ("", "", False), ("", "anything", False),
    ("correct-code", "", False), ("correct-code", "wrong", False),
    ("correct-code", "correct-code", True),
    ("correct-code", "unicode-α", False),
])
def test_hosted_live_requires_configured_matching_code(expected: str, supplied: str, allowed: bool) -> None:
    assert live_access_allowed("hosted", expected, supplied) is allowed


def test_local_form_remains_available() -> None:
    assert live_access_allowed("local", "", "")


def test_bundled_ca_contains_certificates_not_private_keys() -> None:
    bundle = Path(__file__).resolve().parents[1] / "certs/aws-rds-global.pem"
    assert b"PRIVATE KEY" not in bundle.read_bytes()
    ssl.create_default_context(cafile=str(bundle))


def test_bootstrap_refuses_existing_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap_postgresql.settings, "db_backend", "rds_postgresql")
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = [{"tablename": "incidents"}]
    monkeypatch.setattr(bootstrap_postgresql, "get_connection", lambda: conn)
    with pytest.raises(RuntimeError, match="initialization refused"):
        bootstrap_postgresql.main()
    assert conn.execute.call_count == 1
