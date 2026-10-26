"""Exercise configuration preservation at ingestion and retrieval boundaries."""

from datetime import timedelta
from unittest.mock import Mock

import pytest

from agents import db, lambda_handlers
from agents.memory_validation import incident_fingerprint, validate_context
from agents.retrieval import retrieval_agent
from tests.test_memory_validation import NOW, fixture


def test_ingestion_persists_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    write = Mock()
    monkeypatch.setattr(db, "execute_command", write)
    result = lambda_handlers.ingest_handler({
        "service": "api", "description": "errors", "configuration_version": "v1",
    }, None)
    query, parameters = write.call_args.args
    assert "configuration_version" in query
    assert parameters[-1] == "v1"
    assert result["alert"]["configuration_version"] == "v1"


@pytest.mark.parametrize("stored_version,expected", [("v1", True), ("v2", False), (None, False)])
def test_retrieved_configuration_preserves_receipt_binding(
    monkeypatch: pytest.MonkeyPatch, stored_version: str | None, expected: bool,
) -> None:
    item, _, receipt = fixture()
    item.update(configuration_version="v1", severity="high", incident_class="release_regression",
                resolved_at=(NOW - timedelta(hours=2)).isoformat())
    receipt["evidence"].update(configuration_version="v1", incident_fingerprint=incident_fingerprint(item))
    row = {**item, "configuration_version": stored_version, "alert_id": "old-alert",
           "chunk_text": "historical incident", "metadata": {}, "embedding_type": "incident",
           "distance": 0.1, "resolved_at": NOW - timedelta(hours=2)}

    def query(sql: str, parameters: tuple) -> list[dict]:
        assert "i.configuration_version" in sql
        assert parameters[-1] == 5
        return [row]

    monkeypatch.setattr(retrieval_agent, "execute_query", query)
    candidates = retrieval_agent._vector_search([0.0] * 1024)
    assert candidates[0]["configuration_version"] == stored_version
    accepted, _, report = validate_context(
        {"service": "api", "configuration_version": "v1"}, candidates, [], [receipt], now=NOW,
    )
    assert bool(accepted) is expected
    assert report[0]["verdict"] == ("accept" if expected else "withhold")
