"""
Enterprise BI Engine - Streamlit executive console.
Build Steps 3, 5 and 6.

Structured diagnostic console. Not a chatbot: there is one dashboard, one
sidebar, and no free-text input anywhere.

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
STAGE_LABELS = [
    "Watcher Agent checking SQL baseline...",
    "Researcher Agent querying unstructured logs...",
    "Bear vs. Bull Agents debating root cause...",
    "Storyteller synthesizing narrative...",
]
ABSTAIN_BANNER = (
    "Low Confidence: Evidence is contradictory. System is abstaining from "
    "automated action recommendations."
)
FEEDBACK_TOAST = "Feedback logged to Persistent Memory Graph"
TRANSCRIPT_LABEL = "View Agent Debate Transcript"

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
</style>
""", unsafe_allow_html=True)


# ===========================================================================
# session state
# ===========================================================================

def _init_state() -> None:
    defaults = {
        "current_persona": PERSONAS[0],
        "current_scenario_name": list(SCENARIOS)[0],
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
                "REPLAY MODE - no `OPENAI_API_KEY` configured. All SQL, "
                "statistics, retrieval, safety gating and schema validation "
                "execute for real; the debate text is replayed and token counts "
                "are estimated from transcript volume."
            )
        else:
            st.caption(f"LIVE - {tel.get('model', '')}")


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
# result rendering
# ===========================================================================

def _render_split(result: dict[str, Any]) -> None:
    arb = result["arbitration"]
    i, e = arb["internal_confidence"], arb["external_confidence"]
    st.markdown(
        f"""
        <div class="split-lbl"><span>INTERNAL {i}%</span>
        <span>EXTERNAL {e}%</span></div>
        <div class="split-bar">
          <div class="split-int" style="width:{i}%"></div>
          <div class="split-ext" style="width:{e}%"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if arb.get("rationale"):
        st.caption(f"Arbitrator: {arb['rationale']}")


def _render_transcript(result: dict[str, Any]) -> None:
    with st.expander(TRANSCRIPT_LABEL):
        for turn in result["debate_transcript"]:
            st.markdown(f"**{turn['agent']}**")
            st.write(turn["message"])
            st.divider()

        st.markdown("**Retrieved evidence**")
        for ev in result["evidence"]:
            st.markdown(
                f"`{ev['id']}` &middot; {ev['source']} &middot; "
                f"{ev['timestamp']} &middot; supports **{ev['hypothesis']}** "
                f"&middot; distance {ev['distance']:.3f}"
            )
            st.caption(ev["text"])


def _render_gate_log(result: dict[str, Any]) -> None:
    with st.expander("Execution trace - LLM vs deterministic boundary"):
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


def _render_result() -> None:
    result = st.session_state.diagnostic_result
    if result is None:
        return

    if result["status"] == "abstain":
        st.error(ABSTAIN_BANNER)
        st.markdown("#### Conflicting evidence")
        st.markdown(result["narrative"])
        _render_split(result)
        st.info(
            "No automated recommendation is issued in this state. "
            "Manual investigation is required."
        )
        _render_transcript(result)
        _render_gate_log(result)
        return

    st.markdown("#### Executive narrative")
    st.markdown(result["narrative"])

    st.markdown("#### Confidence distribution")
    _render_split(result)

    st.markdown("#### Action recommendation")
    with st.container(border=True):
        st.success(
            f"Schema-validated - confidence {result['confidence_score']}"
        )
        st.json(result["structured_action"])

    _render_transcript(result)
    _render_gate_log(result)
    _hitl(result)


# ===========================================================================
# pipeline trigger
# ===========================================================================

def _run_pipeline(persona: str, scenario: int) -> None:
    stage_map = {
        "watcher": 0, "researcher": 1, "debate": 2, "storyteller": 3,
    }
    with st.status("Running diagnostics...", expanded=True) as status:
        shown: set[int] = set()

        def on_stage(name: str) -> None:
            idx = stage_map.get(name)
            if idx is None:
                return
            # Emit every label up to and including this stage, in order.
            for i in range(idx + 1):
                if i not in shown:
                    st.write(STAGE_LABELS[i])
                    shown.add(i)

        try:
            result = run_diagnostic_pipeline(persona, scenario, on_stage=on_stage)
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

        if result.status == "abstain" and 3 not in shown:
            st.write("Safety gate engaged - Storyteller suppressed.")

        status.update(
            label=("Diagnostic complete" if result.status == "success"
                   else "Diagnostic complete - abstained"),
            state="complete",
            expanded=False,
        )

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

    _render_result()


if __name__ == "__main__":
    main()
