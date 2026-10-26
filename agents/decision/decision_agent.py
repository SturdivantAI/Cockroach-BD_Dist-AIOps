"""Decision Agent - Converts hypothesis into an actionable remediation plan.

This agent:
1. Receives hypothesis + confidence + evidence from Analysis Agent
2. Applies confidence gating:
   - confidence >= 0.7 AND similar resolution succeeded before -> automated remediation
   - confidence < 0.7 -> investigation steps only
3. Outputs action with requires_approval flag
4. Writes decision to agent_decisions table in CockroachDB

The human approval gate integrates with Step Functions task tokens.
"""

import json
import logging
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from agents.inference import invoke_text
from agents.config import settings
from agents.db import execute_command

logger = logging.getLogger(__name__)


class DecisionPayload(BaseModel):
    action: str = Field(min_length=1)
    action_type: Literal["automated_remediation", "investigation_steps", "escalate"]
    requires_approval: bool
    runbook_reference: str
    estimated_impact: str
    rollback_steps: str
    estimated_resolution_time_minutes: int = Field(ge=0)
    risk_level: Literal["low", "medium", "high"]


DECISION_SYSTEM_PROMPT = """You are an SRE Decision Agent. Based on a root cause hypothesis \
and available evidence, you propose a concrete remediation plan. You determine whether the \
action is safe for automated execution or requires human approval.

You MUST respond with valid JSON only."""

DECISION_PROMPT_TEMPLATE = """Based on this analysis, propose a remediation plan.

## Hypothesis
{hypothesis}

## Confidence: {confidence}

## Evidence
{evidence_section}

## Suggested Resolution (from analysis)
{suggested_resolution}

## Prior Successful Resolutions (from memory)
{prior_resolutions}

## Instructions
Propose a specific remediation action. Consider:
- If confidence >= 0.7 AND a similar resolution succeeded before, propose automated remediation
- If confidence < 0.7, propose investigation steps only (manual)
- Always include rollback steps for automated actions

Respond with EXACTLY this JSON:
{{
  "action": "<specific remediation action to take>",
  "action_type": "<automated_remediation|investigation_steps|escalate>",
  "requires_approval": <true if action_type is automated_remediation, false for investigation>,
  "runbook_reference": "<relevant runbook or procedure name>",
  "estimated_impact": "<what will happen when this action is executed>",
  "rollback_steps": "<how to undo if the action makes things worse>",
  "estimated_resolution_time_minutes": <integer>,
  "risk_level": "<low|medium|high>"
}}"""


class DecisionResult:
    """Structured result from the Decision Agent."""

    def __init__(
        self,
        action: str,
        action_type: str,
        requires_approval: bool,
        runbook_reference: str,
        estimated_impact: str,
        rollback_steps: str,
        estimated_resolution_time_minutes: int,
        risk_level: str,
        confidence: float,
        hypothesis: str,
    ):
        self.action = action
        self.action_type = action_type
        self.requires_approval = requires_approval
        self.runbook_reference = runbook_reference
        self.estimated_impact = estimated_impact
        self.rollback_steps = rollback_steps
        self.estimated_resolution_time_minutes = estimated_resolution_time_minutes
        self.risk_level = risk_level
        self.confidence = confidence
        self.hypothesis = hypothesis
        self.decision_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for Step Functions payload."""
        return {
            "decision_id": self.decision_id,
            "action": self.action,
            "action_type": self.action_type,
            "requires_approval": self.requires_approval,
            "runbook_reference": self.runbook_reference,
            "estimated_impact": self.estimated_impact,
            "rollback_steps": self.rollback_steps,
            "estimated_resolution_time_minutes": self.estimated_resolution_time_minutes,
            "risk_level": self.risk_level,
            "confidence": self.confidence,
        }


def _format_evidence_section(evidence: list[dict[str, Any]]) -> str:
    """Format evidence entries for the prompt."""
    if not evidence:
        return "No structured evidence available."

    lines = []
    for e in evidence[:8]:
        lines.append(
            f"- [{e.get('source', 'unknown')}] {e.get('content', '')[:150]} "
            f"(relevance: {e.get('relevance', 'n/a')})"
        )
    return "\n".join(lines)


def _format_prior_resolutions(similar_incidents: list[dict[str, Any]]) -> str:
    """Extract and format prior successful resolutions from memory."""
    if not similar_incidents:
        return "No prior resolutions available in memory."

    resolutions = []
    for inc in similar_incidents[:3]:
        if inc.get("resolution"):
            resolutions.append(
                f"- Service: {inc.get('service')} | "
                f"Class: {inc.get('incident_class')} | "
                f"Resolution: {inc.get('resolution')}"
            )

    if not resolutions:
        return "No prior resolutions available in memory."

    return "\n".join(resolutions)


def _apply_confidence_gating(
    confidence: float,
    similar_incidents: list[dict[str, Any]],
) -> tuple[bool, str]:
    """Apply confidence threshold gating logic.

    Returns:
        (should_automate, reason)
    """
    threshold = settings.confidence_threshold

    has_prior_resolution = any(
        inc.get("resolution") and inc.get("similarity_score", 0) >= 0.7
        for inc in similar_incidents
    )

    if confidence >= threshold and has_prior_resolution:
        return True, (
            f"Confidence ({confidence:.2f}) >= threshold ({threshold}) "
            f"AND similar resolution succeeded previously"
        )
    elif confidence >= threshold:
        return True, (
            f"Confidence ({confidence:.2f}) >= threshold ({threshold}) "
            f"but no prior resolution found — requiring approval for safety"
        )
    else:
        return False, (
            f"Confidence ({confidence:.2f}) < threshold ({threshold}) — "
            f"proposing investigation steps only"
        )


def _parse_decision_response(response_text: str) -> dict[str, Any]:
    """Parse Claude's JSON decision response."""
    text = response_text.strip()

    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass

    raise ValueError("Decision returned invalid JSON")


def _write_decision_to_db(
    session_id: str,
    incident_id: str | None,
    result: DecisionResult,
    memory_items_used: list[str],
    memory_enabled: bool,
    reasoning_steps: int,
) -> str:
    """Write decision to agent_decisions table. Returns decision ID."""
    if not incident_id:
        raise ValueError("A persisted incident ID is required to record a decision")
    decision_id = str(uuid.uuid4())
    action_column = "proposed_action" if settings.db_backend == "rds_postgresql" else "resolution_applied"

    # Format memory_items_used as PostgreSQL UUID array
    uuid_array = "{" + ",".join(memory_items_used) + "}" if memory_items_used else "{}"

    try:
        execute_command(
            f"""
            INSERT INTO agent_decisions
                (id, incident_id, session_id, hypothesis, confidence, evidence,
                 reasoning_steps, {action_column}, memory_items_used, memory_enabled)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::UUID[], %s)
            """,
            (
                decision_id,
                incident_id,
                session_id,
                result.hypothesis,
                result.confidence,
                json.dumps([{"action": result.action, "type": result.action_type}]),
                reasoning_steps,
                result.action,
                uuid_array,
                memory_enabled,
            ),
        )
    except Exception as e:
        logger.error("Failed to persist decision (%s)", type(e).__name__)
        raise

    return decision_id


def _write_decision_to_memory(
    session_id: str,
    incident_id: str | None,
    result: DecisionResult,
    gating_reason: str,
) -> None:
    """Write decision action step to agent_memory."""
    content = {
        "decision_id": result.decision_id,
        "execution_status": "proposed",
        "outcome_verified": False,
        "action": result.action,
        "action_type": result.action_type,
        "requires_approval": result.requires_approval,
        "confidence": result.confidence,
        "risk_level": result.risk_level,
        "gating_reason": gating_reason,
    }

    try:
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
                json.dumps(content),
            ),
        )
    except Exception as e:
        logger.warning("Failed to write decision memory: %s", e)


def run(
    alert: dict[str, Any],
    analysis_result: dict[str, Any],
    retrieval_context: dict[str, Any],
    session_id: str | None = None,
    incident_id: str | None = None,
) -> DecisionResult:
    """Execute the Decision Agent.

    Args:
        alert: Original alert payload.
        analysis_result: Output from Analysis Agent (hypothesis, confidence, evidence, etc.).
        retrieval_context: Output from Retrieval Agent (similar_incidents, prior_reasoning).
        session_id: Current workflow session ID.
        incident_id: The incident ID if already ingested.

    Returns:
        DecisionResult with action, approval requirement, and risk assessment.
    """
    if session_id is None:
        session_id = str(uuid.uuid4())

    hypothesis = analysis_result.get("hypothesis", "No hypothesis")
    confidence = float(analysis_result.get("confidence", 0.0))
    evidence = analysis_result.get("evidence", [])
    suggested_resolution = analysis_result.get("suggested_resolution", "")
    reasoning_steps = analysis_result.get("reasoning_steps_count", 0)
    similar_incidents = retrieval_context.get("similar_incidents", [])

    logger.info(
        "Decision agent running: confidence=%.2f, similar_incidents=%d",
        confidence,
        len(similar_incidents),
    )

    # Apply confidence gating
    should_automate, gating_reason = _apply_confidence_gating(confidence, similar_incidents)
    logger.info("Gating decision: automate=%s, reason=%s", should_automate, gating_reason)

    # Build the decision prompt
    prompt = DECISION_PROMPT_TEMPLATE.format(
        hypothesis=hypothesis,
        confidence=f"{confidence:.2f}",
        evidence_section=_format_evidence_section(evidence),
        suggested_resolution=suggested_resolution or "None provided",
        prior_resolutions=_format_prior_resolutions(similar_incidents),
    )

    # Call Bedrock Claude for decision
    try:
        response_text = invoke_text(
            prompt=prompt,
            system=DECISION_SYSTEM_PROMPT,
            max_tokens=2048,
            temperature=0.2,
        )
        parsed = DecisionPayload.model_validate(_parse_decision_response(response_text)).model_dump()
    except Exception as e:
        logger.error("Decision model call failed: %s", e)
        raise RuntimeError("Decision inference or response validation failed") from e

    # Override requires_approval based on gating logic
    if parsed.get("action_type") == "automated_remediation":
        parsed["requires_approval"] = True  # Always require approval for automated actions
    elif not should_automate:
        parsed["action_type"] = "investigation_steps"
        parsed["requires_approval"] = False

    result = DecisionResult(
        action=parsed.get("action", "No action determined"),
        action_type=parsed.get("action_type", "investigation_steps"),
        requires_approval=parsed.get("requires_approval", False),
        runbook_reference=parsed.get("runbook_reference", ""),
        estimated_impact=parsed.get("estimated_impact", "Unknown"),
        rollback_steps=parsed.get("rollback_steps", "N/A"),
        estimated_resolution_time_minutes=int(parsed.get("estimated_resolution_time_minutes", 30)),
        risk_level=parsed.get("risk_level", "medium"),
        confidence=confidence,
        hypothesis=hypothesis,
    )

    # Collect memory item IDs used (from similar incidents)
    memory_items_used = [
        inc["incident_id"]
        for inc in similar_incidents
        if inc.get("incident_id")
    ][:10]

    memory_enabled = retrieval_context.get("memory_available", False)

    # Write to agent_decisions table
    result.decision_id = _write_decision_to_db(
        session_id=session_id,
        incident_id=incident_id,
        result=result,
        memory_items_used=memory_items_used,
        memory_enabled=memory_enabled,
        reasoning_steps=reasoning_steps,
    )

    # Write to agent_memory
    _write_decision_to_memory(session_id, incident_id, result, gating_reason)

    logger.info(
        "Decision complete: action_type=%s, requires_approval=%s, risk=%s",
        result.action_type,
        result.requires_approval,
        result.risk_level,
    )

    return result
