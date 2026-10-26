"""A proposed action must not become successful memory through caller claims."""

import json
from unittest.mock import MagicMock

import pytest

from agents import db, lambda_handlers
from agents.decision import decision_agent


def test_claimed_success_is_only_recorded_as_proposal(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.return_value = {"proposed_action": "Inspect logs"}
    monkeypatch.setattr(db, "get_connection", lambda: conn)
    result = lambda_handlers.write_memory_handler({"decision": {
        "decision_id": "decision-1", "incident_id": "incident-1", "session_id": "session-1",
        "action": "Disable audit logging", "outcome_verified": True,
    }}, None)
    assert result["status"] == "proposal_recorded"
    assert result["outcome_verified"] is False
    statements = [call.args[0] for call in conn.execute.call_args_list]
    assert len(statements) == 2
    assert statements[0].startswith("SELECT ")
    assert statements[1].startswith("INSERT INTO agent_memory ")
    content = json.loads(conn.execute.call_args_list[1].args[1][-1])
    assert content["proposed_action"] == "Inspect logs"
    assert content["outcome_verified"] is False
    assert content["execution_status"] == "proposed"


def test_unowned_decision_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.return_value = None
    monkeypatch.setattr(db, "get_connection", lambda: conn)
    with pytest.raises(ValueError, match="does not belong"):
        lambda_handlers.write_memory_handler({"decision": {
            "decision_id": "other", "incident_id": "incident", "session_id": "session",
        }}, None)
    assert conn.execute.call_count == 1


def test_missing_decision_is_rejected() -> None:
    with pytest.raises(ValueError, match="persisted decision"):
        lambda_handlers.write_memory_handler({"decision": {}}, None)


def test_failed_insert_does_not_return_decision_id(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object) -> int:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(decision_agent, "execute_command", fail)
    result = MagicMock(action="Inspect", action_type="investigation_steps", hypothesis="Unknown")
    result.confidence = 0.5
    with pytest.raises(RuntimeError, match="database unavailable"):
        decision_agent._write_decision_to_db("session", "incident", result, [], False, 1)
