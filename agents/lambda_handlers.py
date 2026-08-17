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
            INSERT INTO incidents (id, alert_id, severity, service, description, raw_logs, incident_class)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                incident_id,
                alert.get("alert_id", f"alert-{uuid.uuid4().hex[:8]}"),
                alert.get("severity", "medium"),
                alert.get("service", "unknown"),
                alert.get("description", ""),
                json.dumps(alert.get("logs") or alert.get("raw_logs") or []),
                alert.get("incident_class"),
            ),
        )

        logger.info("Ingested incident: %s", incident_id)

    except Exception as e:
        logger.error("Ingestion failed: %s", e)
        incident_id = str(uuid.uuid4())  # Use placeholder for downstream steps
        _save_error_state(session_id, None, "ingestion", str(e))

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
    """Write resolution back to CockroachDB agent memory.

    After incident resolution, writes a structured memory entry for future retrieval.
    This is what enables the agent to perform better on the second encounter.

    Args:
        event: Full workflow state with resolution details.
        context: Lambda context object.

    Returns:
        Confirmation of memory write with entry count.
    """
    # Extract state from workflow
    ingestion = event.get("ingestion", {})
    if "Payload" in ingestion:
        ingestion = ingestion["Payload"]
    retrieval = event.get("retrieval", {})
    if "Payload" in retrieval:
        retrieval = retrieval["Payload"]
    analysis = event.get("analysis", {})
    if "Payload" in analysis:
        analysis = analysis["Payload"]
    decision = event.get("decision", {})
    if "Payload" in decision:
        decision = decision["Payload"]

    session_id = decision.get("session_id") or analysis.get("session_id") or str(uuid.uuid4())
    incident_id = decision.get("incident_id") or analysis.get("incident_id")

    logger.info("Writing resolution memory: session=%s, incident=%s", session_id, incident_id)

    entries_created = 0

    try:
        from agents.db import execute_command
        from agents.bedrock_client import bedrock

        # Write the outcome to agent_memory
        outcome_content = {
            "incident_class": analysis.get("incident_class_guess", "unknown"),
            "service": ingestion.get("service", "unknown"),
            "symptoms_signature": ingestion.get("alert", {}).get("description", "")[:200],
            "root_cause": analysis.get("hypothesis", ""),
            "resolution": decision.get("action", ""),
            "confidence_at_diagnosis": analysis.get("confidence", 0.0),
            "action_type": decision.get("action_type", "unknown"),
            "memory_assisted": retrieval.get("memory_available", False),
            "similar_count": retrieval.get("num_similar", 0),
        }

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
                "outcome",
                json.dumps(outcome_content),
            ),
        )
        entries_created += 1

        # Update the incident record with resolution
        if incident_id:
            execute_command(
                """
                UPDATE incidents
                SET resolution = %s,
                    root_cause = %s,
                    incident_class = COALESCE(incident_class, %s),
                    resolved_at = now()
                WHERE id = %s
                """,
                (
                    decision.get("action", ""),
                    analysis.get("hypothesis", ""),
                    analysis.get("incident_class_guess"),
                    incident_id,
                ),
            )

        # Generate and store resolution embedding for future retrieval
        resolution_text = (
            f"Resolution for {analysis.get('incident_class_guess', 'unknown')} "
            f"in {ingestion.get('service', 'unknown')}: "
            f"{decision.get('action', '')}"
        )

        try:
            embedding = bedrock.generate_embedding(resolution_text)
            vec_str = "[" + ",".join(str(v) for v in embedding) + "]"

            execute_command(
                """
                INSERT INTO incident_embeddings (id, incident_id, embedding, chunk_text, metadata, embedding_type)
                VALUES (%s, %s, %s::VECTOR(1024), %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    incident_id,
                    vec_str,
                    resolution_text,
                    json.dumps({
                        "service": ingestion.get("service"),
                        "incident_class": analysis.get("incident_class_guess"),
                        "type": "resolution",
                        "confidence": analysis.get("confidence", 0.0),
                    }),
                    "resolution",
                ),
            )
            entries_created += 1
        except Exception as e:
            logger.warning("Failed to generate/store resolution embedding: %s", e)

    except Exception as e:
        logger.error("Write memory failed: %s", e)
        _save_error_state(session_id, incident_id, "write_memory", str(e))

    logger.info("Memory write complete: %d entries created", entries_created)

    return {
        "status": "memory_written",
        "session_id": session_id,
        "incident_id": incident_id,
        "memory_entries_created": entries_created,
    }
