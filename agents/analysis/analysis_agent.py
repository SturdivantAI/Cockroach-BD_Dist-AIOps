"""Analysis Agent - Synthesizes retrieved context into a root cause hypothesis.

This agent:
1. Receives alert payload + retrieval context (similar incidents + prior reasoning)
2. Constructs a structured chain-of-thought prompt
3. Calls Bedrock Claude 3.5 Sonnet with streaming-style response
4. Parses structured output: hypothesis, confidence, reasoning_chain, evidence
5. Writes reasoning steps to agent_memory in CockroachDB

Key design: When memory contains a prior resolution for the same incident class,
the prompt explicitly references it — this produces the measurable improvement delta.
"""

import json
import logging
import uuid
from typing import Any

from agents.bedrock_client import bedrock
from agents.db import execute_command

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an expert Site Reliability Engineer analyst. Your role is to \
diagnose incidents by analyzing alerts, logs, and historical context. You produce structured \
root cause hypotheses with confidence ratings and clear reasoning chains.

You MUST respond with valid JSON matching the schema below. No other text."""

ANALYSIS_PROMPT_TEMPLATE = """Analyze this incident and propose a root cause hypothesis.

## Current Alert
- Service: {service}
- Severity: {severity}
- Description: {description}

## Logs
{logs_section}

## Similar Past Incidents from Memory
{memory_section}

## Prior Reasoning (from previous agent sessions)
{prior_reasoning_section}

## Instructions
Based on the alert, logs, and any available memory of similar past incidents:
1. Identify the most likely root cause
2. Rate your confidence (0.0 to 1.0)
3. Show your reasoning step by step
4. Cite specific evidence from the logs and memory

{memory_guidance}

Respond with EXACTLY this JSON structure:
{{
  "hypothesis": "<your root cause hypothesis - be specific and technical>",
  "confidence": <float 0.0-1.0>,
  "reasoning_chain": [
    "<step 1: what you observed in the alert/logs>",
    "<step 2: what pattern this matches>",
    "<step 3: how memory/history informed your diagnosis>",
    "<step 4: your conclusion>"
  ],
  "evidence": [
    {{"source": "log|memory|alert", "content": "<specific evidence>", "relevance": "<why this matters>"}}
  ],
  "incident_class_guess": "<cpu_spike|memory_leak|network_partition|disk_io_saturation|cascading_failure|unknown>",
  "suggested_resolution": "<brief resolution suggestion based on analysis>"
}}"""


def _format_logs_section(alert: dict[str, Any]) -> str:
    """Format log lines for the prompt."""
    logs = alert.get("logs") or alert.get("raw_logs") or []
    if isinstance(logs, list):
        return "\n".join(f"  {log}" for log in logs[:15])
    return str(logs)[:1000]


def _format_memory_section(similar_incidents: list[dict[str, Any]]) -> str:
    """Format similar incidents for the prompt."""
    if not similar_incidents:
        return "No similar past incidents found in memory."

    sections = []
    for i, inc in enumerate(similar_incidents[:5], 1):
        score = inc.get("similarity_score", 0)
        sections.append(
            f"### Match {i} (similarity: {score:.2f})\n"
            f"  - Service: {inc.get('service')}\n"
            f"  - Class: {inc.get('incident_class')}\n"
            f"  - Root Cause: {inc.get('root_cause')}\n"
            f"  - Resolution: {inc.get('resolution')}\n"
            f"  - Resolved: {inc.get('resolved_at', 'unknown')}"
        )
    return "\n\n".join(sections)


def _format_prior_reasoning_section(prior_reasoning: list[dict[str, Any]]) -> str:
    """Format prior reasoning entries for the prompt."""
    if not prior_reasoning:
        return "No prior reasoning available for this service/class."

    sections = []
    for entry in prior_reasoning[:5]:
        content = entry.get("content", {})
        sections.append(
            f"- Agent: {entry.get('agent_name')} | "
            f"Step: {entry.get('step_type')} | "
            f"Content: {json.dumps(content)[:200]}"
        )
    return "\n".join(sections)


def _get_memory_guidance(similar_incidents: list[dict[str, Any]]) -> str:
    """Generate memory-specific guidance for the prompt.

    This is the key mechanism that makes the agent perform better on repeat incidents.
    """
    if not similar_incidents:
        return (
            "NOTE: No prior incidents found in memory. This appears to be a novel incident. "
            "Base your analysis solely on the alert and logs."
        )

    top_match = similar_incidents[0]
    score = top_match.get("similarity_score", 0)

    if score >= 0.75:
        return (
            f"IMPORTANT: A highly similar past incident was found (similarity: {score:.2f}). "
            f"The prior resolution was: '{top_match.get('resolution')}'. "
            f"Assess whether this same resolution applies to the current incident. "
            f"If the pattern matches, your confidence should be HIGH (>= 0.7). "
            f"Cite the prior incident as evidence."
        )
    elif score >= 0.5:
        return (
            f"NOTE: A moderately similar past incident was found (similarity: {score:.2f}). "
            f"The prior root cause was: '{top_match.get('root_cause')}'. "
            f"Consider whether a similar pattern is at play, but verify against current logs."
        )
    else:
        return (
            "NOTE: Only weakly similar incidents found in memory. "
            "Use them as general context but rely primarily on the current alert and logs."
        )


class AnalysisResult:
    """Structured result from the Analysis Agent."""

    def __init__(
        self,
        hypothesis: str,
        confidence: float,
        reasoning_chain: list[str],
        evidence: list[dict[str, str]],
        incident_class_guess: str = "unknown",
        suggested_resolution: str = "",
    ):
        self.hypothesis = hypothesis
        self.confidence = confidence
        self.reasoning_chain = reasoning_chain
        self.evidence = evidence
        self.incident_class_guess = incident_class_guess
        self.suggested_resolution = suggested_resolution

    def to_dict(self) -> dict[str, Any]:
        """Serialize for Step Functions payload."""
        return {
            "hypothesis": self.hypothesis,
            "confidence": self.confidence,
            "reasoning_chain": self.reasoning_chain,
            "evidence": self.evidence,
            "incident_class_guess": self.incident_class_guess,
            "suggested_resolution": self.suggested_resolution,
            "reasoning_steps_count": len(self.reasoning_chain),
        }


def _parse_response(response_text: str) -> dict[str, Any]:
    """Parse Claude's JSON response, handling potential formatting issues."""
    text = response_text.strip()

    # Strip markdown code block if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from mixed text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass

    # Fallback: return a default structure
    logger.warning("Failed to parse analysis response, using fallback")
    return {
        "hypothesis": "Unable to parse structured response. Raw: " + text[:200],
        "confidence": 0.3,
        "reasoning_chain": ["Response parsing failed"],
        "evidence": [],
        "incident_class_guess": "unknown",
        "suggested_resolution": "",
    }


def _write_reasoning_to_memory(
    session_id: str,
    incident_id: str | None,
    result: AnalysisResult,
) -> None:
    """Write analysis reasoning chain to agent_memory."""
    # Write the hypothesis as a memory entry
    content = {
        "hypothesis": result.hypothesis,
        "confidence": result.confidence,
        "incident_class_guess": result.incident_class_guess,
        "suggested_resolution": result.suggested_resolution,
        "evidence_count": len(result.evidence),
        "reasoning_steps": len(result.reasoning_chain),
    }

    try:
        # Write hypothesis step
        hypothesis_id = str(uuid.uuid4())
        execute_command(
            """
            INSERT INTO agent_memory (id, session_id, incident_id, agent_name, step_type, content)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                hypothesis_id,
                session_id,
                incident_id,
                "analysis",
                "hypothesis",
                json.dumps(content),
            ),
        )

        # Write each reasoning step as an observation
        for i, step in enumerate(result.reasoning_chain):
            execute_command(
                """
                INSERT INTO agent_memory
                    (id, session_id, incident_id, agent_name, step_type, content, parent_step_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    session_id,
                    incident_id,
                    "analysis",
                    "observation",
                    json.dumps({"step_index": i, "reasoning": step}),
                    hypothesis_id,
                ),
            )
    except Exception as e:
        logger.warning("Failed to write analysis memory: %s", e)


def run(
    alert: dict[str, Any],
    retrieval_context: dict[str, Any],
    session_id: str | None = None,
    incident_id: str | None = None,
) -> AnalysisResult:
    """Execute the Analysis Agent.

    Args:
        alert: Alert payload with service, severity, description, logs.
        retrieval_context: Output from Retrieval Agent (similar_incidents, prior_reasoning).
        session_id: Current workflow session ID.
        incident_id: The incident ID if already ingested.

    Returns:
        AnalysisResult with hypothesis, confidence, and reasoning chain.
    """
    if session_id is None:
        session_id = str(uuid.uuid4())

    similar_incidents = retrieval_context.get("similar_incidents", [])
    prior_reasoning = retrieval_context.get("prior_reasoning", [])

    logger.info(
        "Analysis agent running: service=%s, similar=%d, prior=%d",
        alert.get("service"),
        len(similar_incidents),
        len(prior_reasoning),
    )

    # Build the analysis prompt
    prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        service=alert.get("service", "unknown"),
        severity=alert.get("severity", "unknown"),
        description=alert.get("description", "No description provided"),
        logs_section=_format_logs_section(alert),
        memory_section=_format_memory_section(similar_incidents),
        prior_reasoning_section=_format_prior_reasoning_section(prior_reasoning),
        memory_guidance=_get_memory_guidance(similar_incidents),
    )

    # Call Bedrock Claude
    logger.info("Invoking Claude for analysis (prompt length=%d chars)", len(prompt))
    try:
        response_text = bedrock.invoke_claude(
            prompt=prompt,
            system=SYSTEM_PROMPT,
            max_tokens=4096,
            temperature=0.3,
        )
    except Exception as e:
        logger.error("Bedrock invocation failed: %s", e)
        result = AnalysisResult(
            hypothesis=f"Analysis failed due to model error: {str(e)[:100]}",
            confidence=0.0,
            reasoning_chain=["Model invocation failed"],
            evidence=[],
        )
        _write_reasoning_to_memory(session_id, incident_id, result)
        return result

    # Parse the response
    parsed = _parse_response(response_text)

    result = AnalysisResult(
        hypothesis=parsed.get("hypothesis", "No hypothesis generated"),
        confidence=max(0.0, min(1.0, float(parsed.get("confidence", 0.3)))),
        reasoning_chain=parsed.get("reasoning_chain", []),
        evidence=parsed.get("evidence", []),
        incident_class_guess=parsed.get("incident_class_guess", "unknown"),
        suggested_resolution=parsed.get("suggested_resolution", ""),
    )

    # Write reasoning to memory
    _write_reasoning_to_memory(session_id, incident_id, result)

    logger.info(
        "Analysis complete: hypothesis='%s...', confidence=%.2f, steps=%d",
        result.hypothesis[:60],
        result.confidence,
        len(result.reasoning_chain),
    )

    return result
