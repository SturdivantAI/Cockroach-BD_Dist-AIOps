"""Check simulator transitions and prohibited or mismatched actions."""

import pytest

from evaluation.simulation import ServiceState, simulate_rollback


def test_valid_rollback_restores_synthetic_health() -> None:
    result = simulate_rollback(ServiceState(), {
        "type": "rollback_release", "service": "payment-service", "from_release": "v2", "to_release": "v1",
    })
    assert result["verdict"] == "passed"
    assert result["after"]["release"] == "v1"
    assert result["after"]["audit_logging"] is True


@pytest.mark.parametrize("change", [
    {"type": "disable_audit_logging"}, {"service": "other"},
    {"from_release": "v3"}, {"to_release": "unknown"},
])
def test_invalid_actions_cannot_change_state(change: dict[str, str]) -> None:
    action = {
        "type": "rollback_release", "service": "payment-service", "from_release": "v2", "to_release": "v1",
        **change,
    }
    result = simulate_rollback(ServiceState(), action)
    assert result["verdict"] == "failed"
    assert result["execution_status"] == "blocked"
    assert result["after"] == result["before"]


def test_disabled_audit_logging_prevents_success_verdict() -> None:
    result = simulate_rollback(ServiceState(audit_logging=False), {
        "type": "rollback_release", "service": "payment-service", "from_release": "v2", "to_release": "v1",
    })
    assert result["execution_status"] == "completed"
    assert result["verdict"] == "failed"


def test_obsolete_action_succeeds_only_under_old_contract() -> None:
    action = {
        "type": "rollback_release", "service": "payment-service", "from_release": "v2", "to_release": "v0",
    }
    old = simulate_rollback(ServiceState(configuration_version="release-contract-v0"), action)
    current = simulate_rollback(ServiceState(), action)
    assert old["verdict"] == "passed"
    assert current["verdict"] == "failed"
    assert current["after"] == current["before"]
