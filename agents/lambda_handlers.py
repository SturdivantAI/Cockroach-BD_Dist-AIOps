"""AWS Lambda handlers for the SRE Agent Step Functions workflow.

Each handler wraps one agent module and is invoked by the Step Functions state machine.
Includes error handling with state preservation — if any step fails, partial state
is written to agent_memory so knowledge isn't lost.
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from agents.config import settings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _get_or_create_session_id(event: dict[str, Any]) -> str:
    """Extract session_id from workflow state or create a new one."""
    return event.get("session_id") or str(uuid.uuid4())


def _extract_alert(event: dict[str, Any]) -> dict[str, Any]:
    """Extract the original alert payload from workflow state."""
    # Alert may be at top level or nested under ingestion
    if "alert" in event:
        return event["alert"]
    if "ingestion" in event and "Payload" in event["ingestion"]:
        payload = event["ingestion"]["Payload"]
        return payload.get("alert", payload)
    if "ingestion" in event:
        return event["ingestion"].get("alert", event.get("alert", event))
    # Fallback: the event itself might be the alert
    if "service" in event and "description" in event:
        return event
    return event


def _save_error_state(session_id: str, incident_id: str | None, step: str, error: str) -> None:
    """Save error state to agent_memory so partial knowledge isn't lost."""
    try:
        from agents.db import execute_command
        execute_command(
            """
            INSERT INTO agent_memory (id, session_id, incident_id, agent_name, step_type, content)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                session_id,
                incident_id,
                step,
                "observation",
                json.dumps({
                    "error": True,
                    "error_message": str(error)[:500],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }),
            ),
        )
    except Exception as e:
        logger.warning("Failed to save error state: %s", e)


def ingest_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Ingest an alert payload and write to CockroachDB incidents table.

    Args:
        event: Alert payload with service, severity, description, logs.
        context: Lambda context object.

    Returns:
        Ingestion result with incident_id and session_id.
    """
    logger.info("Ingesting alert: %s", json.dumps(event, default=str)[:200])

    session_id = _get_or_create_session_id(event)
    alert = _extract_alert(event)

    try:
        from agents.db import execute_command

        incident_id = str(uuid.uuid4())

        execute_command(
            """
            INSERT INTO incidents (id, alert_id, severity, service, description, raw_logs, incident_class, configuration_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                incident_id,
                alert.get("alert_id", f"alert-{uuid.uuid4().hex[:8]}"),
                alert.get("severity", "medium"),
                alert.get("service", "unknown"),
                alert.get("description", ""),
                json.dumps(alert.get("logs") or alert.get("raw_logs") or []),
                alert.get("incident_class"),
                alert.get("configuration_version"),
            ),
        )

        logger.info("Ingested incident: %s", incident_id)

    except Exception as e:
        logger.error("Ingestion failed: %s", e)
        _save_error_state(session_id, None, "ingestion", str(e))
        raise RuntimeError("Incident ingestion failed; workflow stopped") from e

    return {
        "status": "ingested",
        "incident_id": incident_id,
        "session_id": session_id,
        "alert": alert,
        "service": alert.get("service", "unknown"),
        "severity": alert.get("severity", "medium"),
    }


def retrieval_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Retrieval Agent - Query CockroachDB for similar past incidents.

    Args:
        event: Workflow state including ingestion results.
        context: Lambda context object.

    Returns:
        Retrieved context with similar_incidents and prior_reasoning.
    """
    ingestion = event.get("ingestion", {})
    if "Payload" in ingestion:
        ingestion = ingestion["Payload"]

    session_id = ingestion.get("session_id") or _get_or_create_session_id(event)
    incident_id = ingestion.get("incident_id")
    alert = ingestion.get("alert", _extract_alert(event))

    logger.info("Retrieval agent: session=%s, incident=%s", session_id, incident_id)

    start_time = time.time()

    try:
        from agents.retrieval.retrieval_agent import run as retrieval_run

        result = retrieval_run(
            alert=alert,
            session_id=session_id,
            incident_id=incident_id,
            validation_enabled=event.get("validation_enabled", settings.memory_validation_enabled),
        )

        elapsed_ms = int((time.time() - start_time) * 1000)

        return {
            **result.to_dict(),
            "session_id": session_id,
            "incident_id": incident_id,
            "alert": alert,
            "elapsed_ms": elapsed_ms,
        }

    except Exception as e:
        logger.error("Retrieval agent failed: %s", e)
        _save_error_state(session_id, incident_id, "retrieval", str(e))

        return {
            "similar_incidents": [],
            "prior_reasoning": [],
            "confidence": 0.0,
            "memory_available": False,
            "num_similar": 0,
            "num_prior_reasoning": 0,
            "session_id": session_id,
            "incident_id": incident_id,
            "alert": alert,
            "error": str(e)[:200],
        }


def analysis_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Analysis Agent - Synthesize context into root cause hypothesis.

    Args:
        event: Workflow state including retrieval results.
        context: Lambda context object.

    Returns:
        Structured hypothesis with confidence and reasoning chain.
    """
    retrieval = event.get("retrieval", {})
    if "Payload" in retrieval:
        retrieval = retrieval["Payload"]

    session_id = retrieval.get("session_id") or _get_or_create_session_id(event)
    incident_id = retrieval.get("incident_id")
    alert = retrieval.get("alert", _extract_alert(event))

    logger.info("Analysis agent: session=%s, similar=%d", session_id, retrieval.get("num_similar", 0))

    start_time = time.time()

    try:
        from agents.analysis.analysis_agent import run as analysis_run

        result = analysis_run(
            alert=alert,
            retrieval_context=retrieval,
            session_id=session_id,
            incident_id=incident_id,
        )

        elapsed_ms = int((time.time() - start_time) * 1000)

        return {
            **result.to_dict(),
            "session_id": session_id,
            "incident_id": incident_id,
            "alert": alert,
            "elapsed_ms": elapsed_ms,
        }

    except Exception as e:
        logger.error("Analysis agent failed: %s", e)
        _save_error_state(session_id, incident_id, "analysis", str(e))

        return {
            "hypothesis": f"Analysis failed: {str(e)[:100]}",
            "confidence": 0.0,
            "reasoning_chain": ["Error during analysis"],
            "evidence": [],
            "incident_class_guess": "unknown",
            "suggested_resolution": "",
            "reasoning_steps_count": 0,
            "session_id": session_id,
            "incident_id": incident_id,
            "alert": alert,
            "error": str(e)[:200],
        }


def decision_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Decision Agent - Convert hypothesis into remediation proposal.

    Also handles the task token callback for the human approval gate.
    When called with a task_token in the payload, it stores the token
    for the Streamlit approval button to use.

    Args:
        event: Workflow state including analysis results.
        context: Lambda context object.

    Returns:
        Decision with action, approval requirement, and confidence.
    """
    # Check if this is a task token callback (approval gate)
    if "task_token" in event:
        return _handle_approval_gate(event)

    analysis = event.get("analysis", {})
    if "Payload" in analysis:
        analysis = analysis["Payload"]

    retrieval = event.get("retrieval", {})
    if "Payload" in retrieval:
        retrieval = retrieval["Payload"]

    session_id = analysis.get("session_id") or _get_or_create_session_id(event)
    incident_id = analysis.get("incident_id")
    alert = analysis.get("alert", _extract_alert(event))

    logger.info("Decision agent: session=%s, confidence=%.2f", session_id, analysis.get("confidence", 0))

    start_time = time.time()

    try:
        from agents.decision.decision_agent import run as decision_run

        result = decision_run(
            alert=alert,
            analysis_result=analysis,
            retrieval_context=retrieval,
            session_id=session_id,
            incident_id=incident_id,
        )

        elapsed_ms = int((time.time() - start_time) * 1000)

        return {
            **result.to_dict(),
            "session_id": session_id,
            "incident_id": incident_id,
            "elapsed_ms": elapsed_ms,
        }

    except Exception as e:
        logger.error("Decision agent failed: %s", e)
        _save_error_state(session_id, incident_id, "decision", str(e))

        return {
            "action": f"Escalate (agent error: {str(e)[:80]})",
            "action_type": "escalate",
            "requires_approval": False,
            "runbook_reference": "incident-escalation",
            "estimated_impact": "Manual intervention required",
            "rollback_steps": "N/A",
            "estimated_resolution_time_minutes": 60,
            "risk_level": "high",
            "confidence": 0.0,
            "session_id": session_id,
            "incident_id": incident_id,
            "error": str(e)[:200],
        }


def _handle_approval_gate(event: dict[str, Any]) -> dict[str, Any]:
    """Handle the task token for human approval gate.

    Stores the task token so the Streamlit UI can call SendTaskSuccess.
    """
    task_token = event.get("task_token")
    decision = event.get("decision", {})
    if "Payload" in decision:
        decision = decision["Payload"]

    session_id = decision.get("session_id", "unknown")
    incident_id = decision.get("incident_id")

    logger.info("Approval gate: storing task token for session=%s", session_id)

    # Store task token in agent_memory for the Streamlit UI to retrieve
    try:
        from agents.db import execute_command

        execute_command(
            """
            INSERT INTO agent_memory (id, session_id, incident_id, agent_name, step_type, content)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                session_id,
                incident_id,
                "decision",
                "action",
                json.dumps({
                    "task_token": task_token,
                    "awaiting_approval": True,
                    "action": decision.get("action", ""),
                    "action_type": decision.get("action_type", ""),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }),
            ),
        )
    except Exception as e:
        logger.warning("Failed to store task token: %s", e)

    return {
        "status": "awaiting_approval",
        "task_token": task_token,
        "session_id": session_id,
        "incident_id": incident_id,
    }


def write_memory_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Record an unexecuted proposal without claiming incident recovery.

    Outcome verification and promotion are separate operations. Payload claims
    of approval or success must never turn this handler into a promotion path.
    """
    decision = event.get("decision", {})
    if "Payload" in decision:
        decision = decision["Payload"]
    session_id = decision.get("session_id")
    incident_id = decision.get("incident_id")
    decision_id = decision.get("decision_id")
    if decision.get("error") or not all((session_id, incident_id, decision_id)):
        raise ValueError("Cannot record a proposal without a persisted decision")

    from agents.db import get_connection

    # Check ownership and insert atomically; do not trust caller-supplied action text.
    with get_connection() as conn:
        action_column = "proposed_action" if settings.db_backend == "rds_postgresql" else "resolution_applied"
        row = conn.execute(
            f"SELECT {action_column} AS proposed_action FROM agent_decisions "
            "WHERE id = %s AND incident_id = %s AND session_id = %s",
            (decision_id, incident_id, session_id),
        ).fetchone()
        if row is None:
            raise ValueError("Decision does not belong to this incident and session")
        content = {
            "decision_id": decision_id,
            "proposed_action": row["proposed_action"],
            "execution_status": "proposed",
            "outcome_verified": False,
        }
        conn.execute(
            "INSERT INTO agent_memory "
            "(id, session_id, incident_id, agent_name, step_type, content) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), session_id, incident_id, "decision", "action", json.dumps(content)),
        )
    return {
        "status": "proposal_recorded",
        "session_id": session_id,
        "incident_id": incident_id,
        "decision_id": decision_id,
        "outcome_verified": False,
        "memory_entries_created": 1,
    }
