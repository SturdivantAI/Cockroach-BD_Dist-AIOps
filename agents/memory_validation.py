"""Conservative evidence gate for incident and prior-agent memory.

This policy checks recorded evidence, not factual truth of arbitrary prose.
Only trusted verifier writers should have permission to create ledger entries.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from agents.db import execute_query


def incident_fingerprint(item: dict[str, Any]) -> str:
    """Bind the incident prose passed downstream to the verified snapshot."""
    fields = ("service", "severity", "description", "root_cause", "resolution", "incident_class", "resolved_at", "configuration_version")
    snapshot = {key: item.get(key) for key in fields}
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()


def load_verifications(incident_ids: list[str]) -> list[dict[str, Any]]:
    """Load the latest verification per decision, including failed verdicts."""
    if not incident_ids:
        return []
    return execute_query(
        """
        SELECT DISTINCT ON (d.id)
            d.id AS decision_id, d.incident_id, d.session_id,
            d.proposed_action, d.hypothesis, e.status AS execution_status,
            e.execution_mode, e.finished_at, v.id AS verification_id,
            v.verdict, v.method, v.checks, v.evidence, v.verified_at
        FROM agent_decisions d
        JOIN action_executions e ON e.decision_id = d.id
        JOIN outcome_verifications v ON v.execution_id = e.id
        WHERE d.incident_id = ANY(%s::UUID[])
        ORDER BY d.id, v.verified_at DESC, v.id DESC
        """,
        (incident_ids,),
    )


def _receipt_reason(
    receipt: dict[str, Any], alert: dict[str, Any], now: datetime,
    max_age_days: int, allow_simulation: bool,
) -> str | None:
    if receipt.get("execution_status") != "completed" or receipt.get("verdict") != "passed":
        return "execution_or_verification_not_passed"
    mode = receipt.get("execution_mode")
    method = receipt.get("method")
    if mode == "simulation":
        if not allow_simulation:
            return "simulation_evidence_not_enabled"
        if method != "deterministic_simulation":
            return "verification_method_mismatch"
    elif mode != "operational" or method not in ("operational_checks", "human_confirmation"):
        return "verification_method_mismatch"
    timestamp = receipt.get("verified_at")
    finished = receipt.get("finished_at")
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        return "invalid_verification_time"
    if not isinstance(finished, datetime) or finished.tzinfo is None or finished > timestamp:
        return "invalid_execution_time"
    if timestamp > now or finished > now or now - finished > timedelta(days=max_age_days):
        return "stale_or_future_evidence"
    checks = receipt.get("checks")
    if not isinstance(checks, list) or not checks or not all(
        isinstance(check, dict) and check.get("passed") is True for check in checks
    ):
        return "checks_not_passed"
    evidence = receipt.get("evidence")
    if not isinstance(evidence, dict) or not alert.get("service") or evidence.get("service") != alert["service"]:
        return "service_scope_mismatch"
    if alert.get("configuration_version") is not None and (
        evidence.get("configuration_version") != alert["configuration_version"]
    ):
        return "configuration_mismatch"
    return None


def validate_context(
    alert: dict[str, Any], incidents: list[dict[str, Any]], prior: list[dict[str, Any]],
    receipts: list[dict[str, Any]], *, max_age_days: int = 30,
    allow_simulation: bool = False, now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Filter both channels against ledger evidence and return inspectable reasons."""
    now = now or datetime.now(timezone.utc)
    accepted_incidents: list[dict[str, Any]] = []
    accepted_prior: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    for channel, items, accepted in (
        ("incident", incidents, accepted_incidents), ("prior_agent", prior, accepted_prior)
    ):
        for item in items:
            item_id = item.get("incident_id") if channel == "incident" else item.get("memory_id")
            content = item.get("content", {}) if channel == "prior_agent" else item
            if not isinstance(content, dict):
                content = {}
            matches = []
            for receipt in receipts:
                if str(receipt.get("incident_id")) != str(item.get("incident_id")):
                    continue
                if channel == "incident":
                    if not item.get("resolution") or item["resolution"] != receipt.get("proposed_action"):
                        continue
                    if item.get("root_cause") != receipt.get("hypothesis"):
                        continue
                    evidence = receipt.get("evidence")
                    if not isinstance(evidence, dict) or evidence.get("incident_fingerprint") != incident_fingerprint(item):
                        continue
                else:
                    if str(content.get("decision_id")) != str(receipt.get("decision_id")):
                        continue
                    if str(item.get("session_id")) != str(receipt.get("session_id")):
                        continue
                    # Admit only the verified action/outcome, not arbitrary prior prose.
                    action = content.get("proposed_action", content.get("action", content.get("resolution")))
                    if item.get("step_type") not in ("action", "outcome") or action != receipt.get("proposed_action"):
                        continue
                matches.append(receipt)
            selected = None
            reason = "no_matching_verification"
            for receipt in matches:
                failure = _receipt_reason(receipt, alert, now, max_age_days, allow_simulation)
                if failure is None:
                    selected = receipt
                    reason = "verified_evidence_matches_scope"
                    break
                reason = failure
            if selected is not None:
                # Sanitize prior prose: only the ledger-backed action reaches the model.
                safe = dict(item)
                if channel == "prior_agent":
                    safe["content"] = {
                        "decision_id": str(selected["decision_id"]),
                        "verified_action": selected["proposed_action"],
                        "verification_id": str(selected["verification_id"]),
                        "evidence_scope": selected["execution_mode"],
                        "configuration_version": selected["evidence"].get("configuration_version"),
                    }
                accepted.append(safe)
            report.append({
                "channel": channel, "item_id": str(item_id),
                "verdict": "accept" if selected is not None else "withhold", "reason": reason,
                "verification_id": str(selected["verification_id"]) if selected else None,
                "evidence_scope": selected["execution_mode"] if selected else None,
            })
    return accepted_incidents, accepted_prior, report
