"""SRE Memory Agent - Three-panel Streamlit UI.

Panels:
1. (Left) Incoming Signals - Alert payloads as they fire
2. (Center) Agent Reasoning Stream - Step-by-step agent reasoning
3. (Right) Memory Recall - What the agent pulled from CockroachDB

Bottom:
- Step Functions Timeline
- Judge Score Delta Chart
- CockroachDB Cluster Health

Trigger: "Fire Alert" button starts the incident scenario.
"""

import sys
import uuid
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

st.set_page_config(
    page_title="SRE Memory Agent",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# === Custom CSS ===
st.markdown("""
<style>
    .block-container { padding-top: 0.8rem; padding-bottom: 0rem; max-width: 1400px; }
    [data-testid="stHeader"] { background: transparent; }
    body, p, li, span, div { font-size: 17px !important; line-height: 1.6 !important; }
    h1 { font-size: 2rem !important; font-weight: 700 !important; margin-bottom: 0 !important; }
    h2 { font-size: 1.4rem !important; font-weight: 600 !important; }
    h3 { font-size: 1.15rem !important; font-weight: 600 !important; }
    .stMetric label { font-size: 13px !important; }
    .stMetric [data-testid="stMetricValue"] { font-size: 24px !important; }
    .reasoning-step {
        padding: 10px 14px; margin: 6px 0; border-radius: 8px;
        border-left: 5px solid; font-size: 16px !important; line-height: 1.5;
    }
    .step-retrieval { border-color: #4A9EFF; background: rgba(74,158,255,0.08); }
    .step-analysis { border-color: #FFB84D; background: rgba(255,184,77,0.08); }
    .step-decision { border-color: #4ADE80; background: rgba(74,222,128,0.08); }
    .memory-active { color: #16a34a; font-weight: bold; font-size: 18px !important; }
    .memory-none { color: #d97706; font-weight: bold; font-size: 18px !important; }
    code { font-size: 14px !important; }
    .stExpander { border: 1px solid rgba(128,128,128,0.2); border-radius: 8px; }
    /* Tighten spacing */
    .stMarkdown { margin-bottom: -10px; }
</style>
""", unsafe_allow_html=True)

# === Session State ===
if "workflow_history" not in st.session_state:
    st.session_state.workflow_history = []
if "current_run" not in st.session_state:
    st.session_state.current_run = None
if "run_count" not in st.session_state:
    st.session_state.run_count = 0

# === Demo Scenarios ===
DEMO_SCENARIOS = [
    {
        "name": "CPU Spike - Payment Service",
        "alert": {
            "service": "payment-service",
            "severity": "critical",
            "description": "CPU utilization on payment-service exceeded 95% for 5 minutes. Transaction latency increased from 50ms to 2300ms.",
            "logs": [
                "2026-07-30T10:23:01Z [payment-service] WARN cpu_usage=96.2% threshold=90%",
                "2026-07-30T10:23:15Z [payment-service] ERROR request_timeout path=/api/v2/charge latency=2341ms",
                "2026-07-30T10:23:22Z [payment-service] WARN thread_pool_exhausted active=200 max=200",
                "2026-07-30T10:23:30Z [payment-service] ERROR circuit_breaker_open service=fraud-check",
            ],
        },
    },
    {
        "name": "Memory Leak - Order Service",
        "alert": {
            "service": "order-service",
            "severity": "critical",
            "description": "Order service memory growing linearly, OOMKilled after 6 hours. Heap dump shows 2.1GB of unreleased HTTP connections.",
            "logs": [
                "2026-07-30T02:00:00Z [order-service] WARN memory_usage=78% growing=true",
                "2026-07-30T04:30:00Z [order-service] WARN memory_usage=91% gc_pressure=high",
                "2026-07-30T05:15:00Z [order-service] ERROR OOMKilled container=order-svc-pod-3",
                "2026-07-30T05:15:01Z [kubernetes] INFO pod_restart reason=OOMKilled restart_count=3",
            ],
        },
    },
    {
        "name": "Cascading Failure - Checkout",
        "alert": {
            "service": "checkout-flow",
            "severity": "critical",
            "description": "Checkout cascade: payment timeout caused cart backup, overwhelming session store. 503 rate at 45%.",
            "logs": [
                "2026-07-30T12:00:00Z [payment-gateway] ERROR timeout upstream=stripe latency=30000ms",
                "2026-07-30T12:00:05Z [cart-service] WARN request_queue_depth=5000 blocked_on=payment",
                "2026-07-30T12:00:10Z [session-store] ERROR max_connections_reached limit=1000",
                "2026-07-30T12:00:15Z [frontend] ERROR 503_rate=45% all_downstream_failing=true",
            ],
        },
    },
]


def run_agent_workflow(alert: dict) -> dict:
    """Execute the full agent workflow and return results."""
    from agents.orchestrator import run_workflow
    result = run_workflow(alert, skip_approval=True, write_memory=True)
    return result.to_dict()


# =============================================================================
# HEADER
# =============================================================================
col_title, col_m1, col_m2, col_m3 = st.columns([4, 1, 1, 1])
with col_title:
    st.title("🧠 SRE Memory Agent")
    st.caption("Distributed AI SRE Assistant with Agentic Memory | CockroachDB (3 regions) + AWS Bedrock")

with col_m1:
    st.metric("Runs", st.session_state.run_count)
with col_m2:
    if st.session_state.current_run:
        conf = st.session_state.current_run.get("analysis", {}).get("confidence", 0)
        st.metric("Confidence", f"{conf:.0%}")
    else:
        st.metric("Confidence", "—")
with col_m3:
    if st.session_state.current_run:
        mem = st.session_state.current_run.get("retrieval", {}).get("memory_available", False)
        st.metric("Memory", "Active" if mem else "Cold")
    else:
        st.metric("Memory", "—")

st.divider()


# =============================================================================
# CONTROLS (above panels so they're always visible)
# =============================================================================
col_scenario, col_fire, col_approve, col_clear = st.columns([4, 1.5, 1, 1])

with col_scenario:
    scenario_names = [s["name"] for s in DEMO_SCENARIOS]
    selected = st.selectbox("Scenario", scenario_names, index=0, label_visibility="collapsed")
    scenario_idx = scenario_names.index(selected)

with col_fire:
    fire_clicked = st.button("🚨 Fire Alert", type="primary", use_container_width=True)

with col_approve:
    approve_clicked = st.button("✅ Approve", use_container_width=True)

with col_clear:
    clear_clicked = st.button("🗑️ Clear", use_container_width=True)

if clear_clicked:
    st.session_state.workflow_history = []
    st.session_state.current_run = None
    st.session_state.run_count = 0
    st.rerun()

if approve_clicked and st.session_state.current_run:
    decision = st.session_state.current_run.get("decision", {})
    if decision.get("requires_approval"):
        st.toast("Resolution approved! Executing remediation...", icon="✅")

if fire_clicked:
    scenario = DEMO_SCENARIOS[scenario_idx]
    alert = scenario["alert"].copy()
    alert["alert_id"] = f"demo-{uuid.uuid4().hex[:8]}"

    with st.spinner(f"Processing: {scenario['name']}..."):
        try:
            result = run_agent_workflow(alert)
            st.session_state.current_run = result
            st.session_state.workflow_history.append(result)
            st.session_state.run_count += 1
        except Exception as e:
            st.error(f"Workflow failed: {str(e)[:200]}")
            st.session_state.current_run = {
                "ingestion": {"alert": alert, "service": alert.get("service")},
                "retrieval": {"memory_available": False, "similar_incidents": [], "num_similar": 0},
                "analysis": {"hypothesis": f"Error: {e}", "confidence": 0, "reasoning_chain": [], "reasoning_steps_count": 0},
                "decision": {"action": "Escalate (error)", "action_type": "escalate", "requires_approval": False},
                "steps_completed": ["ingestion"],
                "total_elapsed_ms": 0,
                "errors": [{"step": "workflow", "error": str(e)}],
            }
            st.session_state.workflow_history.append(st.session_state.current_run)
            st.session_state.run_count += 1
    st.rerun()

st.divider()


# =============================================================================
# THREE-PANEL LAYOUT
# =============================================================================
col_left, col_center, col_right = st.columns([1, 2, 1])

# --- Panel 1: Incoming Signals ---
with col_left:
    st.subheader("📡 Incoming Signals")
    if st.session_state.current_run:
        run = st.session_state.current_run
        ingestion = run.get("ingestion", {})
        alert = ingestion.get("alert", {})
        severity = alert.get("severity", "medium")
        sev_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(severity, "⚪")

        st.markdown(f"**{sev_icon} {severity.upper()}** — `{alert.get('service', 'unknown')}`")
        st.markdown(f"_{alert.get('description', '')}_")

        with st.expander("📋 Raw Logs", expanded=False):
            logs = alert.get("logs") or alert.get("raw_logs") or []
            for log in logs:
                st.code(log, language="log")

        st.caption(f"ID: `{alert.get('alert_id', 'N/A')}`")
    else:
        st.info("Press **Fire Alert** to begin.")

# --- Panel 2: Agent Reasoning ---
with col_center:
    st.subheader("⚡ Agent Reasoning")
    if st.session_state.current_run:
        run = st.session_state.current_run
        steps = run.get("steps_completed", [])

        if "retrieval" in steps:
            retrieval = run.get("retrieval", {})
            n_similar = retrieval.get("num_similar", 0)
            elapsed = retrieval.get("elapsed_ms", 0)
            st.markdown(
                f'<div class="reasoning-step step-retrieval">'
                f'<b>🔍 Retrieval</b> — {n_similar} similar incidents found ({elapsed}ms)</div>',
                unsafe_allow_html=True,
            )

        if "analysis" in steps:
            analysis = run.get("analysis", {})
            confidence = analysis.get("confidence", 0)
            st.markdown(
                f'<div class="reasoning-step step-analysis">'
                f'<b>🧪 Analysis</b> — Confidence: {confidence:.0%}</div>',
                unsafe_allow_html=True,
            )

            # Reasoning chain
            chain = analysis.get("reasoning_chain", [])
            if chain:
                with st.expander(f"Chain of Thought ({len(chain)} steps)", expanded=True):
                    for i, step in enumerate(chain, 1):
                        st.markdown(f"{i}. {step}")

            # Hypothesis
            hypothesis = analysis.get("hypothesis", "")
            if hypothesis:
                st.markdown(f"**Root Cause:** {hypothesis}")

        if "decision" in steps:
            decision = run.get("decision", {})
            action_type = decision.get("action_type", "unknown")
            icon = {"automated_remediation": "🤖", "investigation_steps": "🔬", "escalate": "🚨"}.get(action_type, "❓")

            st.markdown(
                f'<div class="reasoning-step step-decision">'
                f'<b>{icon} Decision</b> — {action_type.replace("_", " ").title()} '
                f'| Risk: {decision.get("risk_level", "?")}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(f"**Remediation:** {decision.get('action', 'N/A')}")

            if decision.get("requires_approval"):
                st.warning("⏳ Awaiting human approval — click **Approve** above")

        if "write_memory" in steps:
            mem = run.get("memory_write", {})
            st.success(f"✅ Written to memory ({mem.get('memory_entries_created', 0)} entries) — available for future incidents")
    else:
        st.caption("Reasoning will appear here after firing an alert.")

# --- Panel 3: Memory Recall ---
with col_right:
    st.subheader("🧠 Memory Recall")
    if st.session_state.current_run:
        run = st.session_state.current_run
        retrieval = run.get("retrieval", {})

        if retrieval.get("memory_available"):
            st.markdown('<span class="memory-active">🧠 Memory Active</span>', unsafe_allow_html=True)
            similar = retrieval.get("similar_incidents", [])
            if similar:
                st.markdown(f"**{len(similar)} prior incidents recalled:**")
                for i, inc in enumerate(similar[:3], 1):
                    score = inc.get("similarity_score", 0)
                    with st.expander(f"#{i} {inc.get('service', '?')} — {score:.0%} match", expanded=(i == 1)):
                        st.markdown(f"**Class:** `{inc.get('incident_class', '?')}`")
                        st.markdown(f"**Root Cause:** {inc.get('root_cause', 'N/A')}")
                        st.markdown(f"**Resolution:** {inc.get('resolution', 'N/A')}")

            prior = retrieval.get("prior_reasoning", [])
            if prior:
                with st.expander(f"Prior Reasoning ({len(prior)})"):
                    for entry in prior[:3]:
                        st.json(entry.get("content", {}))
        else:
            st.markdown('<span class="memory-none">⚠️ No Prior Memory</span>', unsafe_allow_html=True)
            st.caption("First encounter with this incident class. After resolution, memory will be available for future incidents.")
    else:
        st.caption("Memory context will appear here.")


# =============================================================================
# BOTTOM SECTION: Timeline + Metrics + Cluster Health
# =============================================================================
st.divider()

# Timeline
if st.session_state.current_run:
    run = st.session_state.current_run
    steps_done = run.get("steps_completed", [])
    all_steps = ["ingestion", "retrieval", "analysis", "decision", "write_memory"]

    step_cols = st.columns(len(all_steps))
    for step_name, col in zip(all_steps, step_cols):
        with col:
            if step_name in steps_done:
                st.markdown(f"✅ **{step_name.replace('_', ' ').title()}**")
            else:
                st.markdown(f"⬜ {step_name.replace('_', ' ').title()}")

    total_ms = run.get("total_elapsed_ms", 0)
    if total_ms:
        st.caption(f"Total: **{total_ms:,}ms** ({total_ms/1000:.1f}s)")

# Performance comparison (after 2+ runs)
if len(st.session_state.workflow_history) >= 2:
    st.divider()
    st.subheader("📊 Memory Improvement Delta")

    import pandas as pd

    data = []
    for i, run in enumerate(st.session_state.workflow_history[-5:], 1):  # Last 5 runs
        analysis = run.get("analysis", {})
        retrieval = run.get("retrieval", {})
        mem_status = "With Memory" if retrieval.get("memory_available") else "No Memory"
        data.append({
            "Run": f"Run {i}",
            "Confidence": analysis.get("confidence", 0.0),
            "Steps": analysis.get("reasoning_steps_count", 0),
            "Memory": mem_status,
        })

    df = pd.DataFrame(data)

    col_c, col_s, col_d = st.columns(3)
    with col_c:
        st.bar_chart(df.set_index("Run")["Confidence"])
        st.caption("Confidence (higher = better)")
    with col_s:
        st.bar_chart(df.set_index("Run")["Steps"])
        st.caption("Reasoning Steps (fewer = faster)")
    with col_d:
        # Delta metrics
        first_conf = data[0]["Confidence"]
        last_conf = data[-1]["Confidence"]
        delta = last_conf - first_conf
        st.metric("Confidence Delta", f"{last_conf:.0%}", delta=f"{delta:+.0%}")
        st.metric("Memory Status", data[-1]["Memory"])

# CockroachDB Cluster Health
with st.expander("🔧 CockroachDB Cluster Health (3 regions)", expanded=False):
    try:
        from agents.ops.ops_agent import OpsAgent
        ops = OpsAgent()
        health = ops.get_cluster_health_summary()

        col_c1, col_c2, col_c3, col_c4 = st.columns(4)
        cluster = health.get("cluster", {})
        with col_c1:
            state = cluster.get("state", "UNKNOWN")
            st.metric("State", f"{'🟢' if state == 'READY' else '🟡'} {state}")
        with col_c2:
            st.metric("Regions", "us-east-1, us-east-2, us-west-2")
        with col_c3:
            st.metric("Survival", "REGION FAILURE")
        with col_c4:
            st.metric("Storage", f"{cluster.get('storage_mb', 0)} MB")

        audit = health.get("audit", {})
        entries = audit.get("recent_entries", [])
        if entries:
            st.markdown("**Agent Memory Access Log:**")
            for entry in entries[:5]:
                action = entry.get("action", "?")
                table = entry.get("table", "?")
                icon = {"SELECT": "📖", "INSERT": "✏️", "UPDATE": "🔄"}.get(action, "❓")
                rows = entry.get("rows_read", entry.get("rows_written", 0))
                st.markdown(f"&nbsp;&nbsp;{icon} `{action}` on `{table}` — {rows} rows")

        if st.button("📦 Trigger Backup", key="backup_btn"):
            backup_result = ops.trigger_backup()
            if backup_result.get("success"):
                bkp = backup_result.get("data", {})
                st.success(f"Backup: {bkp.get('backup_id', 'N/A')} ({bkp.get('type', 'INCREMENTAL')})")
            else:
                st.warning(f"Backup: {backup_result.get('error', 'unknown')}")

    except Exception as e:
        st.caption(f"Cluster info unavailable: {str(e)[:80]}")
