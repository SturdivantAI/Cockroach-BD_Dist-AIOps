"""Trust-aware incident-memory demo: saved evidence replay and live proposals."""

import json
import sys
import uuid
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)

from frontend.reporting import read_report, summary_rows

st.set_page_config(page_title="SRE-Agent V2 | Trust-Aware Memory", page_icon="🛡️", layout="wide")
st.markdown("""
<style>
.block-container {max-width: 1300px; padding-top: 2rem;}
[data-testid="stMetric"] {border: 1px solid #71809644; border-radius: 12px; padding: 14px;}
</style>
""", unsafe_allow_html=True)
st.title("SRE-Agent V2")
st.markdown("### AI incident response with memory it can trust.")
st.caption("NVIDIA Nemotron on Nebius Token Factory · PostgreSQL evidence ledger · Synthetic incident evaluation")
view = st.radio("View", ["Evidence replay", "Live proposal"], horizontal=True)


def show_episode(episode: dict[str, Any] | None, title: str) -> None:
    st.subheader(title)
    if episode is None:
        st.info("This episode is not present in the selected report.")
        return
    if episode["status"] != "scored":
        st.error("This inference attempt failed; it is not counted as safe or resolved.")
        st.write(episode.get("error_type", "Unreported error"))
        return
    score = episode["score"]
    if score["unsafe_recommendation"]:
        st.error("Unsafe recommendation · blocked by the simulator")
    elif score["correct_resolution"]:
        st.success("Correct resolution in simulation")
    else:
        st.warning("Did not resolve the simulated incident")
    action = episode["proposal"]["action"]
    st.markdown(f"**Proposed action:** `{action['type']}`")
    st.write(f"Service: {action['service']}")
    if action.get("to_release"):
        st.write(f"Release: {action.get('from_release')} → {action['to_release']}")
    st.write(episode["proposal"]["rationale"])
    a, b = st.columns(2)
    a.metric("Validation", f"{episode.get('validation_total_ms', 0):.0f} ms")
    request_ms = episode.get("request_and_parse_ms", episode.get("elapsed_ms", 0))
    b.metric("Model request + parsing", f"{request_ms:.0f} ms")
    st.caption(f"Reported tokens: {episode.get('usage', {}).get('total_tokens', 'unreported')}")
    decisions = episode.get("validation_report", [])
    if decisions:
        st.markdown("**Memory decisions**")
        st.dataframe([{
            "Source": row.get("channel"), "Decision": row.get("verdict"),
            "Reason": row.get("reason"), "Evidence scope": row.get("evidence_scope"),
        } for row in decisions], hide_index=True, width="stretch")
    else:
        st.caption("No validation in this arm." if episode["mode"] == "ordinary" else "No retrieved memory in this control.")
    with st.expander("Action checks and model response"):
        st.json(score["simulated_outcome"])
        st.code(episode["raw_response"], language="json")
        st.code(episode["prompt_sha256"], language="text")


def replay() -> None:
    st.info("Saved experimental replay. No inference requests or operational actions occur in this view.")
    by_name = {path.name: path for path in (Path.home() / "sre-agent-v2-results").glob("controlled-comparison-*.json")}
    by_name.update({path.name: path for path in (ROOT / "evaluation/results").glob("controlled-comparison-*.json")})
    paths = sorted(by_name.values(), key=lambda path: path.name, reverse=True)
    uploaded = st.file_uploader("Or open a saved experiment report", type=["json"])
    if uploaded is not None:
        payload = uploaded.getvalue()
    elif paths:
        names = [str(path) for path in paths]
        chosen = st.selectbox("Saved run", names, format_func=lambda name: Path(name).name)
        payload = Path(chosen).read_bytes()
    else:
        st.warning("No experiment report available. Upload a saved JSON report to replay it.")
        return
    try:
        report = read_report(payload)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        st.error(f"Cannot replay this report: {exc}")
        return
    st.caption(f"Run: {report.get('run_id', 'unknown')} · Model: {report.get('model', 'unreported')}")
    st.warning("One synthetic incident. Repeated calls measure consistency here, not reliability across different incidents.")
    with st.expander("Current incident and available releases", expanded=True):
        alert = report.get("alert", {})
        st.write(alert.get("description", "No incident description"))
        st.json({k: alert[k] for k in ("service", "release", "configuration_version", "available_releases", "logs") if k in alert})
    conditions = [c for c in ("stale", "poisoned", "clean", "none") if any(e["condition"] == c for e in report["episodes"])]
    condition = st.selectbox("Memory condition", conditions, format_func=lambda c: {
        "stale": "Stale experience", "poisoned": "Poisoned experience", "clean": "Supported experience", "none": "No-memory control",
    }[c])
    repeats = sorted({e["repeat"] for e in report["episodes"] if e["condition"] == condition})
    repeat = st.selectbox("Repeat", repeats, format_func=lambda value: str(value + 1))
    snapshot = report.get("memory_snapshots", {}).get(condition)
    if snapshot and snapshot[0]:
        with st.expander("Historical memory supplied to the comparison", expanded=True):
            for item in snapshot[0]:
                st.write(f"Source contract: {item.get('configuration_version', 'not captured')}")
                st.write(f"Historical recommendation: {item.get('resolution', '')}")
    episodes = [e for e in report["episodes"] if e["condition"] == condition and e["repeat"] == repeat]
    if condition == "none":
        show_episode(next(iter(episodes), None), "Shared no-memory control")
    else:
        left, right = st.columns(2, gap="large")
        with left:
            show_episode(next((e for e in episodes if e["mode"] == "ordinary"), None), "Ordinary memory")
        with right:
            show_episode(next((e for e in episodes if e["mode"] == "validated"), None), "Validated memory")
    st.divider()
    st.subheader("All observed outcomes")
    st.dataframe(summary_rows(report), hide_index=True, width="stretch")
    st.caption("Correctness comes from deterministic action checks. Unsafe labels cover the declared prohibited actions; errors remain separate.")
    with st.expander("Provenance and limitations"):
        provenance = report.get("code_provenance", {})
        st.json({k: provenance[k] for k in ("git_commit", "git_branch", "working_tree_dirty", "source_sha256") if k in provenance})
        for limitation in report.get("limitations", []):
            st.write(limitation)
        st.caption("Replay checks prompt hashes and recomputes action scores; it does not attest that uploaded evidence is genuine.")
    st.download_button("Download complete run evidence", payload, file_name=f"{report.get('run_id', 'experiment')}.json", mime="application/json")


def live_proposal() -> None:
    from agents.config import settings
    from frontend.access import live_access_allowed

    st.info("Live validated workflow: retrieve → validate → analyze → propose → record. No remediation is executed.")
    st.caption(f"Provider: {settings.inference_provider} · Model: {settings.nebius_model_id if settings.inference_provider == 'nebius' else settings.bedrock_model_id}")
    st.caption("Running this workflow incurs inference usage and writes incident, decision, and proposal records to the database.")
    if settings.deployment_mode == "hosted":
        expected = settings.demo_access_code.get_secret_value()
        if not expected:
            st.info("Live proposals are not enabled on this deployment. Evidence replay remains available.")
            return
        supplied = st.text_input("Demo access code", type="password")
        if not live_access_allowed(settings.deployment_mode, expected, supplied):
            st.info("Enter the demo access code supplied in the testing instructions to use live inference.")
            return
    service = st.text_input("Service", "payment-service")
    configuration = st.text_input("Configuration version", "release-contract-v1")
    description = st.text_area("Incident", "Error rate rose from 0.5% to 25% immediately after deploying release v2.")
    logs = st.text_area("Logs, one per line", "deployment_complete release=v2\nhttp_5xx_rate=0.25\naudit_logging=enabled")
    if st.button("Generate validated proposal", type="primary", disabled=not service.strip() or not description.strip()):
        from agents.orchestrator import run_workflow

        alert = {
            "alert_id": "ui-" + uuid.uuid4().hex, "service": service.strip(), "severity": "high",
            "configuration_version": configuration.strip(), "description": description.strip(),
            "logs": logs.splitlines(),
        }
        with st.spinner("Retrieving evidence and generating a proposal…"):
            st.session_state["live_result"] = run_workflow(alert, validation_enabled=True).to_dict()
    result = st.session_state.get("live_result")
    if result is None:
        return
    if not result["success"]:
        st.error("Workflow stopped before completion.")
        st.json(result["errors"])
    else:
        st.success("Proposal recorded. No action executed; no recovery claimed.")
    st.write("Completed steps:", " → ".join(result["steps_completed"]))
    st.write("Proposed action:", result["decision"].get("action", "No proposal"))
    st.write("Hypothesis:", result["analysis"].get("hypothesis", "Unavailable"))
    st.caption(f"Workflow time: {result['total_elapsed_ms']} ms · Execution: {result['execution_status']} · Approval: {result['approval_status']}")
    decisions = result["retrieval"].get("validation_report", [])
    if decisions:
        st.subheader("Memory decisions")
        st.dataframe(decisions, hide_index=True, width="stretch")
    else:
        st.caption("No candidate memory decisions were recorded.")
    with st.expander("Complete workflow trace"):
        st.json(result)
    st.download_button("Download workflow trace", json.dumps(result, indent=2, default=str), file_name="workflow-trace.json", mime="application/json")


if view == "Evidence replay":
    replay()
else:
    live_proposal()
