"""Workflow completion is proposal persistence, never permission or recovery."""

from unittest.mock import Mock

import pytest

from agents import lambda_handlers as handlers
from agents.orchestrator import run_workflow


@pytest.fixture
def stages(monkeypatch: pytest.MonkeyPatch) -> dict[str, Mock]:
    values = {
        "ingest_handler": {"incident_id": "incident", "session_id": "session"},
        "retrieval_handler": {"memory_available": False},
        "analysis_handler": {"hypothesis": "Regression"},
        "decision_handler": {"decision_id": "decision", "requires_approval": True},
        "write_memory_handler": {"status": "proposal_recorded"},
    }
    mocks = {}
    for name, value in values.items():
        mocks[name] = Mock(return_value=value)
        monkeypatch.setattr(handlers, name, mocks[name])
    return mocks


def test_completed_workflow_does_not_execute_or_autoapprove(stages: dict[str, Mock]) -> None:
    result = run_workflow({"service": "api"}, validation_enabled=True, skip_approval=True)
    assert result.success
    assert result.status == "proposal_recorded"
    assert result.execution_status == "not_executed"
    assert result.approval_status == "pending"
    event = stages["retrieval_handler"].call_args.args[0]
    assert event["validation_enabled"] is True


def test_ingestion_failure_stops_all_downstream_calls(stages: dict[str, Mock]) -> None:
    stages["ingest_handler"].side_effect = RuntimeError("DB unavailable")
    result = run_workflow({"service": "api"})
    assert not result.success
    stages["retrieval_handler"].assert_not_called()
    stages["write_memory_handler"].assert_not_called()


def test_handler_error_is_not_a_completed_analysis(stages: dict[str, Mock]) -> None:
    stages["analysis_handler"].return_value = {"error": "Invalid model response"}
    result = run_workflow({"service": "api"})
    assert result.status == "failed"
    assert "analysis" not in result.steps_completed
    stages["decision_handler"].assert_not_called()


def test_missing_decision_id_cannot_be_recorded_as_success(stages: dict[str, Mock]) -> None:
    stages["decision_handler"].return_value = {"action": "roll back"}
    result = run_workflow({"service": "api"})
    assert not result.success
    stages["write_memory_handler"].assert_not_called()


def test_proposal_ready_without_final_memory_write(stages: dict[str, Mock]) -> None:
    result = run_workflow({"service": "api"}, write_memory=False)
    assert result.success and result.status == "proposal_ready"
    stages["write_memory_handler"].assert_not_called()
