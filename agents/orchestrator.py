"""Local orchestrator for running the full agent workflow without AWS Step Functions.

Executes the same sequence as the Step Functions state machine:
  Alert -> Ingestion -> Retrieval -> Analysis -> Decision -> [Approval] -> Write Memory

Useful for local development, demo, and testing.

Usage:
    from agents.orchestrator import run_workflow
    result = run_workflow(alert_payload)
"""

import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class WorkflowResult:
    """Complete result from a full workflow execution."""

    def __init__(self):
        self.session_id: str = ""
        self.incident_id: str = ""
        self.ingestion: dict[str, Any] = {}
        self.retrieval: dict[str, Any] = {}
        self.analysis: dict[str, Any] = {}
        self.decision: dict[str, Any] = {}
        self.memory_write: dict[str, Any] = {}
        self.total_elapsed_ms: int = 0
        self.steps_completed: list[str] = []
        self.errors: list[dict[str, Any]] = []

    def to_dict(self) -> dict[str, Any]:
        """Full serialization for display/logging."""
        return {
            "session_id": self.session_id,
            "incident_id": self.incident_id,
            "ingestion": self.ingestion,
            "retrieval": self.retrieval,
            "analysis": self.analysis,
            "decision": self.decision,
            "memory_write": self.memory_write,
            "total_elapsed_ms": self.total_elapsed_ms,
            "steps_completed": self.steps_completed,
            "errors": self.errors,
        }

    @property
    def success(self) -> bool:
        return len(self.errors) == 0 and "write_memory" in self.steps_completed


def run_workflow(
    alert: dict[str, Any],
    session_id: str | None = None,
    skip_approval: bool = True,
    write_memory: bool = True,
) -> WorkflowResult:
    """Execute the full agent workflow locally.

    Args:
        alert: Alert payload with service, severity, description, logs.
        session_id: Optional session ID (generated if not provided).
        skip_approval: If True, skip the human approval gate (for automated testing).
        write_memory: If True, write resolution to memory after decision.

    Returns:
        WorkflowResult with all step outputs.
    """
    from agents.lambda_handlers import (
        ingest_handler,
        retrieval_handler,
        analysis_handler,
        decision_handler,
        write_memory_handler,
    )

    result = WorkflowResult()
    result.session_id = session_id or str(uuid.uuid4())
    start_time = time.time()

    # Inject session_id into alert
    alert_with_session = {**alert, "session_id": result.session_id}

    # Step 1: Ingestion
    logger.info("=" * 60)
    logger.info("STEP 1: Ingesting alert")
    logger.info("=" * 60)
    try:
        result.ingestion = ingest_handler(alert_with_session, None)
        result.incident_id = result.ingestion.get("incident_id", "")
        result.steps_completed.append("ingestion")
        logger.info("  Ingested: incident_id=%s", result.incident_id)
    except Exception as e:
        result.errors.append({"step": "ingestion", "error": str(e)})
        logger.error("  Ingestion FAILED: %s", e)
        result.total_elapsed_ms = int((time.time() - start_time) * 1000)
        return result

    # Step 2: Retrieval
    logger.info("=" * 60)
    logger.info("STEP 2: Retrieval Agent (vector search)")
    logger.info("=" * 60)
    try:
        retrieval_event = {"ingestion": {"Payload": result.ingestion}}
        result.retrieval = retrieval_handler(retrieval_event, None)
        result.steps_completed.append("retrieval")
        logger.info(
            "  Found %d similar incidents, memory_available=%s",
            result.retrieval.get("num_similar", 0),
            result.retrieval.get("memory_available", False),
        )
    except Exception as e:
        result.errors.append({"step": "retrieval", "error": str(e)})
        logger.error("  Retrieval FAILED: %s", e)

    # Step 3: Analysis
    logger.info("=" * 60)
    logger.info("STEP 3: Analysis Agent (Claude reasoning)")
    logger.info("=" * 60)
    try:
        analysis_event = {
            "retrieval": {"Payload": result.retrieval},
            "ingestion": {"Payload": result.ingestion},
        }
        result.analysis = analysis_handler(analysis_event, None)
        result.steps_completed.append("analysis")
        logger.info(
            "  Hypothesis: '%s...'",
            result.analysis.get("hypothesis", "")[:80],
        )
        logger.info("  Confidence: %.2f", result.analysis.get("confidence", 0))
    except Exception as e:
        result.errors.append({"step": "analysis", "error": str(e)})
        logger.error("  Analysis FAILED: %s", e)

    # Step 4: Decision
    logger.info("=" * 60)
    logger.info("STEP 4: Decision Agent (remediation)")
    logger.info("=" * 60)
    try:
        decision_event = {
            "analysis": {"Payload": result.analysis},
            "retrieval": {"Payload": result.retrieval},
            "ingestion": {"Payload": result.ingestion},
        }
        result.decision = decision_handler(decision_event, None)
        result.steps_completed.append("decision")
        logger.info("  Action: '%s...'", result.decision.get("action", "")[:80])
        logger.info(
            "  Type: %s, Requires Approval: %s",
            result.decision.get("action_type"),
            result.decision.get("requires_approval"),
        )
    except Exception as e:
        result.errors.append({"step": "decision", "error": str(e)})
        logger.error("  Decision FAILED: %s", e)

    # Step 5: Approval Gate (skipped in local mode by default)
    if result.decision.get("requires_approval") and not skip_approval:
        logger.info("=" * 60)
        logger.info("STEP 5: Awaiting human approval...")
        logger.info("=" * 60)
        # In local mode, we'd wait for user input
        # For now, auto-approve
        logger.info("  Auto-approved (local mode)")

    # Step 6: Write Memory
    if write_memory:
        logger.info("=" * 60)
        logger.info("STEP 6: Writing resolution to memory")
        logger.info("=" * 60)
        try:
            memory_event = {
                "ingestion": {"Payload": result.ingestion},
                "retrieval": {"Payload": result.retrieval},
                "analysis": {"Payload": result.analysis},
                "decision": {"Payload": result.decision},
            }
            result.memory_write = write_memory_handler(memory_event, None)
            result.steps_completed.append("write_memory")
            logger.info(
                "  Memory entries created: %d",
                result.memory_write.get("memory_entries_created", 0),
            )
        except Exception as e:
            result.errors.append({"step": "write_memory", "error": str(e)})
            logger.error("  Memory write FAILED: %s", e)

    result.total_elapsed_ms = int((time.time() - start_time) * 1000)

    logger.info("=" * 60)
    logger.info("WORKFLOW COMPLETE")
    logger.info("  Steps completed: %s", result.steps_completed)
    logger.info("  Total time: %dms", result.total_elapsed_ms)
    logger.info("  Errors: %d", len(result.errors))
    logger.info("=" * 60)

    return result
