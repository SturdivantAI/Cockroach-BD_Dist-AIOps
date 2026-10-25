"""Create one simulation fixture and verify ledger-backed memory gating.

Writes five linked records in one transaction. No inference or embedding calls.
Run using the configured RDS connection; each invocation creates a fresh fixture.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from agents.db import get_connection
from agents.memory_validation import incident_fingerprint, load_verifications, validate_context
from evaluation.simulation import ServiceState, simulate_rollback


def create_fixture(
    configuration_version: str = "release-contract-v1",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Persist a fresh synthetic execution and return its ledger-backed memory."""
    incident_id, decision_id, execution_id, verification_id, session_id, memory_id = (
        str(uuid.uuid4()) for _ in range(6)
    )
    started = datetime.now(timezone.utc)
    target = {"release-contract-v1": "v1", "release-contract-v0": "v0"}.get(configuration_version)
    if target is None:
        raise ValueError("Unknown fixture configuration version")
    action = {
        "type": "rollback_release", "service": "payment-service",
        "from_release": "v2", "to_release": target,
    }
    outcome = simulate_rollback(ServiceState(configuration_version=configuration_version), action)
    if outcome["verdict"] != "passed":
        raise RuntimeError("Smoke fixture failed simulation; no evidence was stored")
    finished = datetime.now(timezone.utc)
    hypothesis = "Synthetic regression introduced by release v2."
    proposal = f"Roll back payment-service from release v2 to {target}."
    scope = {"service": "payment-service", "configuration_version": configuration_version}
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO incidents (id, alert_id, severity, service, description, raw_logs, "
            "root_cause, resolution, incident_class, resolved_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (incident_id, "simulation-smoke-" + session_id, "high", "payment-service",
             "Synthetic fixture: error rate increased after release v2.",
             json.dumps({"source": outcome["source"], "before": outcome["before"]}),
             hypothesis, proposal, "release_regression", finished),
        ).fetchone()
        if row is None:
            raise RuntimeError("Fixture insert did not return an incident")
        # Timestamp conversion matches retrieval serialization before fingerprinting.
        item = {key: row[key] for key in (
            "service", "severity", "description", "root_cause", "resolution", "incident_class"
        )}
        item.update(incident_id=incident_id, resolved_at=row["resolved_at"].isoformat(),
                    configuration_version=configuration_version)
        conn.execute(
            "INSERT INTO agent_decisions (id,incident_id,session_id,hypothesis,confidence,proposed_action) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (decision_id, incident_id, session_id, hypothesis, 0.5, proposal),
        )
        conn.execute(
            "INSERT INTO action_executions (id,decision_id,execution_mode,status,action_payload,"
            "run_identifier,started_at,finished_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (execution_id, decision_id, "simulation", outcome["execution_status"],
             json.dumps(action), session_id, started, finished),
        )
        evidence = {
            **scope, "source": outcome["source"], "before": outcome["before"],
            "after": outcome["after"], "incident_fingerprint": incident_fingerprint(item),
        }
        conn.execute(
            "INSERT INTO outcome_verifications (id,execution_id,execution_mode,method,verdict,"
            "verifier,checks,evidence) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (verification_id, execution_id, "simulation", "deterministic_simulation", outcome["verdict"],
             outcome["source"], json.dumps(outcome["checks"]), json.dumps(evidence)),
        )
        content = {**scope, "decision_id": decision_id, "proposed_action": proposal}
        conn.execute(
            "INSERT INTO agent_memory (id,session_id,incident_id,agent_name,step_type,content) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (memory_id, session_id, incident_id, "decision", "outcome", json.dumps(content)),
        )
    receipts = load_verifications([incident_id])
    prior = {
        "memory_id": memory_id, "incident_id": incident_id, "session_id": session_id,
        "step_type": "outcome", "content": content,
    }
    return scope, item, prior, receipts


def main() -> None:
    scope, item, prior, receipts = create_fixture()
    cases = [
        ("supported_simulation", scope, item, [prior], True, (1, 1)),
        ("altered_memory", scope, {**item, "resolution": "Disable audit logging."}, [], True, (0, 0)),
        ("configuration_mismatch", {**scope, "configuration_version": "release-contract-v2"}, item, [prior], True, (0, 0)),
        ("simulation_disabled", scope, item, [prior], False, (0, 0)),
    ]
    for name, alert, candidate, memories, allow, expected in cases:
        incidents, accepted_prior, report = validate_context(
            alert, [candidate], memories, receipts, allow_simulation=allow,
        )
        if (len(incidents), len(accepted_prior)) != expected:
            raise RuntimeError(f"{name} failed: {report}")
        print(f"PASS {name}: {report}")
    print("Fixture incident ID:", item["incident_id"])
    print("All results are synthetic simulation evidence, not operational recovery.")


if __name__ == "__main__":
    main()
