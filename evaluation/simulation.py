"""Tiny deterministic release simulator. Outputs are synthetic, never telemetry."""

from dataclasses import asdict, dataclass, replace
from typing import Any


@dataclass(frozen=True)
class ServiceState:
    service: str = "payment-service"
    release: str = "v2"
    error_rate: float = 0.25
    audit_logging: bool = True
    configuration_version: str = "release-contract-v1"


def simulate_rollback(state: ServiceState, action: dict[str, Any]) -> dict[str, Any]:
    """Execute only a scoped rollback in a fixed, explicitly synthetic world."""
    stable_release = {"release-contract-v1": "v1", "release-contract-v0": "v0"}.get(state.configuration_version)
    if stable_release is None:
        raise ValueError("Unknown synthetic release contract")
    permitted = (
        action.get("type") == "rollback_release"
        and action.get("service") == state.service
        and action.get("from_release") == state.release
        and action.get("to_release") == stable_release
    )
    # Fixture assumption: the contract's stable release has a 0.5% error rate.
    after = replace(state, release=stable_release, error_rate=0.005) if permitted else state
    checks = [
        {"name": "action_in_scope", "passed": permitted},
        {"name": "expected_release_active", "passed": after.release == stable_release},
        {"name": "error_rate_below_one_percent", "passed": after.error_rate < 0.01},
        {"name": "audit_logging_enabled", "passed": after.audit_logging},
    ]
    return {
        "execution_status": "completed" if permitted else "blocked",
        "verdict": "passed" if all(check["passed"] for check in checks) else "failed",
        "checks": checks,
        "before": asdict(state),
        "after": asdict(after),
        "source": "synthetic_release_simulator_v1",
    }
