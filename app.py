"""
Enterprise BI Engine - Streamlit executive console.
Build Steps 3, 5 and 6.

WhatsApp-style agent chat feed with live, turn-based conversation between
diagnostic agents. Not a chatbot: there is one dashboard, one sidebar, and
no free-text input anywhere.

Run:  streamlit run app.py
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from agents import run_diagnostic_pipeline
from config.contract import get_contract
from db.memory_graph import write_feedback
from outputs.schema import SchemaViolation
from telemetry.meter import blank
from watcher.deterministic import query_kpi_metrics

load_dotenv()

ROOT = Path(__file__).resolve().parent

# ---- exact strings from the App Flow specification ------------------------
PERSONAS = ["VP of Revenue (Executive)", "Director of Engineering (Technical)"]
SCENARIOS = {
    "Multi-Factor Incident": 1,
    "Ambiguous Data": 2,
    "Sparse History": 3,
}

# Agent avatars for chat messages
AVATARS = {
    "Researcher_Agent": "🔍",
    "Bear_Agent": "🐻",
    "Bull_Agent": "🐂",
    "Debate_Agent": "⚔️",
    "Arbitrator_Agent": "⚖️",
    "Storyteller_Agent": "📝",
    "Watcher": "⚡",
    "Researcher": "🔍",
    "Gate": "🛡️",
}

ABSTAIN_BANNER = (
    "Low Confidence: Evidence is contradictory. System is abstaining from "
    "automated action recommendations."
)
FEEDBACK_TOAST = "Feedback logged to Persistent Memory Graph"

st.set_page_config(
    page_title="Enterprise BI Engine",
    page_icon=":material/query_stats:",
    layout="wide",
)

st.markdown("""
<style>
  .kpi-caption   { font-size: 0.70rem; color: #6A6478; line-height: 1.35;
                   margin-top: -0.55rem; margin-bottom: 0.6rem; }
  .kpi-caption b { color: #A100FF; font-weight: 600; }
  .restricted    { color: #C22E3A !important; }
  .tel           { font-family: ui-monospace, "SF Mono", Menlo, monospace;
                   font-size: 0.74rem; line-height: 1.75; }
  .tel .k        { color: #6A6478; }
  .tel .v        { color: #0F1115; font-weight: 600; }
  .split-bar     { display:flex; height: 22px; border-radius: 3px;
                   overflow: hidden; margin: 6px 0 4px; }
  .split-int     { background:#A100FF; }
  .split-ext     { background:#CFCBD9; }
  .split-lbl     { display:flex; justify-content:space-between;
                   font-size:0.70rem; color:#6A6478; font-family: ui-monospace,
                   Menlo, monospace; }
  .gate-line     { font-family: ui-monospace, Menlo, monospace;
                   font-size: 0.72rem; color: #3D3B47; }
  /* Chat styling overrides for a cleaner look */
  [data-testid="stChatMessage"] {
    padding: 0.6rem 1rem;
    border-radius: 12px;
    margin-bottom: 0.4rem;
  }
</style>
""", unsafe_allow_html=True)


# ===========================================================================
# session state
# ===========================================================================

def _init_state() -> None:
    defaults = {
        "current_persona": PERSONAS[0],
        "current_scenario_name": list(SCENARIOS)[0],
        "current_mode": "Live",
        "current_model": "qwen/qwen3.8-27b",
        "diagnostic_result": None,
        "telemetry": blank(),
        "pipeline_error": None,
        "feedback_note": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _reset_diagnostic() -> None:
    """Return to the Default / Empty state (App Flow section 9)."""
    st.session_state.diagnostic_result = None
    st.session_state.pipeline_error = None


def _on_persona_change() -> None:
    st.session_state.current_persona = st.session_state.persona_selector
    _reset_diagnostic()


def _on_scenario_change() -> None:
    st.session_state.current_scenario_name = st.session_state.scenario_selector
    _reset_diagnostic()


def _on_mode_change() -> None:
    st.session_state.current_mode = st.session_state.mode_selector
    _reset_diagnostic()


def _on_model_change() -> None:
    st.session_state.current_model = st.session_state.model_selector
    _reset_diagnostic()


_init_state()


# ===========================================================================
# sidebar
# ===========================================================================

def _render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### Enterprise BI Engine")
        st.caption("Multi-Agent Adversarial Diagnostics")
        st.divider()

        st.selectbox(
            "Persona",
            PERSONAS,
            index=PERSONAS.index(st.session_state.current_persona),
            key="persona_selector",
            on_change=_on_persona_change,
        )
        st.radio(
            "Scenario",
            list(SCENARIOS),
            index=list(SCENARIOS).index(st.session_state.current_scenario_name),
            key="scenario_selector",
            on_change=_on_scenario_change,
        )

        st.divider()
        st.radio(
            "Mode",
            ["Live", "Replay"],
            index=["Live", "Replay"].index(st.session_state.current_mode),
            key="mode_selector",
            on_change=_on_mode_change,
        )
        if st.session_state.current_mode == "Live":
            st.selectbox(
                "Model",
                ["qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "openai/gpt-oss-120b"],
                index=["qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "openai/gpt-oss-120b"].index(
                    st.session_state.current_model
                ) if st.session_state.current_model in ["qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "openai/gpt-oss-120b"] else 0,
                key="model_selector",
                on_change=_on_model_change,
            )

        st.divider()
        st.markdown("**Runtime Telemetry**")
        tel = st.session_state.telemetry
        mode = tel.get("mode", "live")
        st.markdown(
            f"""<div class="tel">
            <span class="k">Query Latency</span>
              <span class="v">{tel.get('latency_ms', 0):,.2f} ms</span><br>
            <span class="k">Model Calls</span>
              <span class="v">{tel.get('model_calls', 0)}</span><br>
            <span class="k">Tokens Used</span>
              <span class="v">{tel.get('total_tokens', 0):,}</span><br>
            <span class="k">Estimated Cost</span>
              <span class="v">${tel.get('estimated_cost_usd', 0):.6f}</span>
            </div>""",
            unsafe_allow_html=True,
        )
        if mode == "replay":
            st.caption(
                "REPLAY MODE — no `GROQ_API_KEY` configured. All SQL, "
                "statistics, retrieval, safety gating and schema validation "
                "execute for real; the debate text is replayed and token counts "
                "are estimated from transcript volume."
            )
        else:
            st.caption(f"LIVE — Groq · {tel.get('model', '')}")


# ===========================================================================
# KPI grid
# ===========================================================================

def _format_value(kpi: Any) -> str:
    if kpi.value is None:
        return "[RESTRICTED]"
    if kpi.fmt == "currency":
        return f"${kpi.value:,.0f}"
    if kpi.fmt == "percent":
        return f"{kpi.value:.2f}%"
    if kpi.fmt == "latency":
        return f"{kpi.value:,.0f} ms"
    return f"{kpi.value:,.0f}"


def _delta_text(kpi: Any) -> str | None:
    if not kpi.access_granted:
        return "RBAC Enforced"
    if kpi.delta_relative_pct is not None:
        return f"{kpi.delta_relative_pct:+.1f}%"
    if kpi.delta_absolute is not None:
        return f"{kpi.delta_absolute:+,.0f} vs baseline"
    return None


def _render_kpis(watcher) -> None:
    order = ["mrr", "churn_rate", "gateway_timeout_errors", "p99_latency_ms"]
    cols = st.columns(4)

    for col, key in zip(cols, order):
        kpi = watcher.kpis[key]
        with col:
            st.metric(
                label=kpi.display_name,
                value=_format_value(kpi),
                delta=_delta_text(kpi),
                delta_color="inverse" if kpi.access_granted else "off",
            )

            if not kpi.access_granted:
                st.markdown(
                    f'<div class="kpi-caption restricted">'
                    f'<b>Query not executed.</b> {kpi.access_note}</div>',
                    unsafe_allow_html=True,
                )
                continue

            # Traceability caption: source, grain, freshness, method.
            zed = (
                f" &middot; z={kpi.z_score:.1f}" if kpi.z_score is not None
                else " &middot; z withheld"
            )
            flag = " &middot; <b>LOW POWER</b>" if kpi.low_power else ""
            st.markdown(
                f'<div class="kpi-caption">'
                f'<b>{kpi.source_table}</b> &middot; {kpi.grain} &middot; '
                f'{kpi.refresh_cadence}<br>'
                f'{kpi.method}{zed}{flag}</div>',
                unsafe_allow_html=True,
            )


# ===========================================================================
# WhatsApp-style chat result rendering
# ===========================================================================

def _render_chat_result() -> None:
    """Render the diagnostic result as a WhatsApp-style agent chat thread."""
    result = st.session_state.diagnostic_result
    if result is None:
        return

    st.markdown("---")
    st.markdown("#### 💬 Agent Diagnostic Thread")

    # ---- Data gathering toast (Watcher + Researcher) ----
    watcher_data = result.get("watcher", {})
    kpi_count = len([k for k, v in watcher_data.get("kpis", {}).items()
                     if v.get("access_granted", True)])
    evidence_count = len(result.get("evidence", []))

    with st.chat_message("Watcher", avatar="⚡"):
        st.markdown(
            f"**Watcher Agent** — Computed **{kpi_count} KPIs** from SQL baseline. "
            f"Scenario: *{watcher_data.get('scenario_label', 'N/A')}*. "
            f"Anomaly detected: **{watcher_data.get('anomaly_detected', 'N/A')}**."
        )

    with st.chat_message("Researcher", avatar="🔍"):
        st.markdown(
            f"**Researcher Agent** — Retrieved **{evidence_count} evidence documents** "
            f"from the incident window via ChromaDB vector search."
        )
        # Show evidence list in a compact expander
        if result["evidence"]:
            with st.expander("📄 Retrieved Evidence", expanded=False):
                for ev in result["evidence"]:
                    st.markdown(
                        f"`{ev['id']}` &middot; {ev['source']} &middot; "
                        f"supports **{ev['hypothesis']}** &middot; "
                        f"distance {ev['distance']:.3f}"
                    )
                    st.caption(ev["text"])

    # ---- Debate transcript as chat messages ----
    for turn in result["debate_transcript"]:
        agent = turn["agent"]
        avatar = AVATARS.get(agent, "🤖")

        # Map agent names to friendly display names
        display_names = {
            "Researcher_Agent": "Researcher",
            "Bear_Agent": "Bear (Internal)",
            "Bull_Agent": "Bull (External)",
            "Debate_Agent": "Debate Agent",
        }
        display_name = display_names.get(agent, agent)

        with st.chat_message(display_name, avatar=avatar):
            st.markdown(f"**{display_name}**")
            st.write(turn["message"])

    # ---- Arbitrator verdict ----
    arb = result["arbitration"]
    i_conf = arb["internal_confidence"]
    e_conf = arb["external_confidence"]

    with st.chat_message("Arbitrator", avatar="⚖️"):
        st.markdown("**Arbitrator**")
        st.markdown(
            f"After weighing the debate, I assign "
            f"**Internal: {i_conf}%** vs **External: {e_conf}%**."
        )

        # Confidence split bar
        st.markdown(
            f"""
            <div class="split-lbl"><span>INTERNAL {i_conf}%</span>
            <span>EXTERNAL {e_conf}%</span></div>
            <div class="split-bar">
              <div class="split-int" style="width:{i_conf}%"></div>
              <div class="split-ext" style="width:{e_conf}%"></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if arb.get("rationale"):
            st.caption(f"*{arb['rationale']}*")

    # ---- Python Safety Gate (structurally intact) ----
    if result["status"] == "abstain":
        with st.chat_message("Gate", avatar="🛡️"):
            st.error(
                "⛔ **Evidence is contradictory. I am abstaining from "
                "automated action.** The Python Safety Gate has halted "
                "execution. Manual investigation is required."
            )
            if result.get("abstain_reason"):
                st.caption(f"Reason: {result['abstain_reason']}")

        # Show the abstain narrative
        st.markdown("#### Conflicting Evidence")
        st.markdown(result["narrative"])
        st.info(
            "No automated recommendation is issued in this state. "
            "Manual investigation is required."
        )
    else:
        # ---- Storyteller summary ----
        with st.chat_message("Storyteller", avatar="📝"):
            st.markdown("**Storyteller**")
            st.write(result["narrative"])

        # ---- 7-field JSON Action Card ----
        st.markdown("#### ✅ Action Recommendation")
        with st.container(border=True):
            st.success(
                f"Schema-validated · confidence {result['confidence_score']} · "
                f"Groq · {result.get('watcher', {}).get('scenario_label', 'N/A')}"
            )
            st.json(result["structured_action"])

    # ---- Execution trace (preserved from original) ----
    with st.expander("Execution trace — LLM vs deterministic boundary"):
        st.markdown(
            "Every line below was produced by **Python or SQL**, not by a "
            "language model. The gate decision is the last line."
        )
        for line in result["gate_log"]:
            st.markdown(f'<div class="gate-line">- {line}</div>',
                        unsafe_allow_html=True)

        st.divider()
        left, right = st.columns(2)
        with left:
            st.markdown("**Deterministic (no LLM)**")
            st.markdown(
                "- KPI values, deltas, percentages\n"
                "- Baseline mean, stdev, z-scores\n"
                "- Materiality tests\n"
                "- Driver attribution and ranking\n"
                "- Vector retrieval and time-window filter\n"
                "- RBAC access decisions\n"
                "- **Abstain gate**\n"
                "- Schema validation"
            )
        with right:
            st.markdown("**LLM (bounded reasoning)**")
            st.markdown(
                "- Researcher evidence summary\n"
                "- Bear internal argument\n"
                "- Bull external argument\n"
                "- Arbitrator confidence split\n"
                "- Storyteller narrative and action wording"
            )

    # ---- HITL feedback (only if action was issued) ----
    if result["status"] != "abstain":
        _hitl(result)


def _hitl(result: dict[str, Any]) -> None:
    st.markdown("**Analyst validation**")
    cols = st.columns([1, 1, 1, 5])
    for col, outcome in zip(cols[:3], ("Accept", "Modify", "Reject")):
        with col:
            if st.button(outcome, use_container_width=True,
                         key=f"hitl_{outcome}"):
                res = write_feedback(
                    persona=result["persona"],
                    scenario=result["scenario"],
                    outcome=outcome.lower(),
                    action=result["structured_action"] or {},
                )
                st.session_state.feedback_note = {
                    "backend": res.backend,
                    "detail": res.detail,
                }
                st.toast(FEEDBACK_TOAST)
                _reset_diagnostic()
                st.rerun()


# ===========================================================================
# pipeline trigger
# ===========================================================================

def _run_pipeline(persona: str, scenario: int) -> None:
    with st.status("Running diagnostics...", expanded=True) as status:
        def on_stage(name: str) -> None:
            stage_labels = {
                "watcher": "⚡ Watcher Agent checking SQL baseline...",
                "researcher": "🔍 Researcher Agent querying logs...",
                "debate": "⚔️ Agents debating root cause...",
                "arbitrator": "⚖️ Arbitrator evaluating evidence...",
                "storyteller": "📝 Storyteller generating JSON Action Plan...",
            }
            if name in stage_labels:
                status.write(stage_labels[name])

        try:
            result = run_diagnostic_pipeline(
                persona,
                scenario,
                on_stage=on_stage,
                use_live_mode=(st.session_state.current_mode == "Live"),
                model_name=st.session_state.current_model,
            )
        except SchemaViolation as exc:
            status.update(label="Diagnostic failed - output contract violated",
                          state="error")
            st.session_state.pipeline_error = (
                f"The Storyteller returned output that failed schema "
                f"validation, so nothing was rendered.\n\n`{exc}`"
            )
            st.session_state.diagnostic_result = None
            return
        except Exception as exc:                       # noqa: BLE001
            status.update(label="Diagnostic failed", state="error")
            st.session_state.pipeline_error = f"`{type(exc).__name__}: {exc}`"
            st.session_state.diagnostic_result = None
            return

        if result.status == "abstain":
            status.update(label="Diagnostics Complete (Abstained)", state="complete", expanded=False)
        else:
            status.update(label="Diagnostic complete", state="complete", expanded=False)

    st.session_state.diagnostic_result = {
        "status": result.status,
        "scenario": result.scenario,
        "persona": result.persona,
        "narrative": result.narrative,
        "structured_action": result.structured_action,
        "confidence_score": result.confidence_score,
        "arbitration": result.arbitration,
        "debate_transcript": result.debate_transcript,
        "evidence": result.evidence,
        "watcher": result.watcher,
        "gate_log": result.gate_log,
        "abstain_reason": result.abstain_reason,
    }
    st.session_state.telemetry = result.telemetry
    st.rerun()


# ===========================================================================
# main
# ===========================================================================

def main() -> None:
    _render_sidebar()

    persona = st.session_state.current_persona
    scenario_name = st.session_state.current_scenario_name
    scenario = SCENARIOS[scenario_name]

    st.title("Enterprise BI Engine: Multi-Agent Diagnostics Stack")
    st.caption(
        f"Local synthetic environment &middot; **{persona}** &middot; "
        f"**{scenario_name}** &middot; as of "
        f"{get_contract()['scenarios'][scenario]['as_of']}"
    )

    if not (ROOT / "enterprise_bi.duckdb").exists():
        st.error("Database not initialised. Run `python setup_db.py` first.")
        st.stop()

    watcher = query_kpi_metrics(scenario, persona)
    _render_kpis(watcher)

    note = st.session_state.feedback_note
    if note:
        if note["backend"] == "neo4j":
            st.success(f"Persisted to graph: {note['detail']}")
        else:
            st.warning(f"Persisted locally: {note['detail']}")
        st.session_state.feedback_note = None

    st.divider()

    if st.button("Run Diagnostics", type="primary"):
        _run_pipeline(persona, scenario)

    if st.session_state.pipeline_error:
        st.error(st.session_state.pipeline_error)

    _render_chat_result()


if __name__ == "__main__":
    main()
