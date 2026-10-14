"""
Enterprise BI Engine - Build Step 4 / 6
Standalone multi-agent diagnostic engine. No Streamlit import anywhere.

Pipeline:

    Watcher (deterministic, no LLM)
        -> Researcher (local ChromaDB retrieval)
        -> Bear vs Bull (AutoGen adversarial debate)
        -> Arbitrator (confidence distribution)
        -> [ PYTHON SAFETY GATE ]
        -> Storyteller (only if the gate passes)

THE SAFETY GATE IS NOT A PROMPT. Two independent checks run in Python, above
the model layer:

    1. confidence < abstain threshold  -> abstain
    2. attribution non-separable       -> abstain regardless of confidence

The Storyteller is never constructed on the abstain path, so no language model
is in a position to generate a recommendation past either gate.

Run standalone:  python agents.py [scenario] [persona]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

_groq_client = None

def _get_client():
    """Singleton OpenAI client pointed at Groq's inference endpoint."""
    global _groq_client
    if _groq_client is None:
        from openai import OpenAI
        _groq_client = OpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
        )
    return _groq_client

from dotenv import load_dotenv

from config.contract import abstain_threshold, persona_spec
from outputs.schema import (
    ActionRecommendation,
    ArbitrationResult,
    SchemaViolation,
    validate_action,
)
from rag.retrieval import EvidenceItem, evidence_balance, retrieve_unstructured_context
from telemetry.meter import Meter, Telemetry
from watcher.deterministic import WatcherOutput, query_kpi_metrics

load_dotenv()

ANOMALY_TIMESTAMP = "2026-08-25T10:02:00Z"


# ===========================================================================
# Result payload
# ===========================================================================

@dataclass
class DiagnosticResult:
    status: str                       # "success" | "abstain"
    scenario: int
    persona: str
    narrative: str = ""
    structured_action: dict[str, Any] | None = None
    confidence_score: int = 0
    arbitration: dict[str, Any] = field(default_factory=dict)
    debate_transcript: list[dict[str, str]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    watcher: dict[str, Any] = field(default_factory=dict)
    telemetry: dict[str, Any] = field(default_factory=dict)
    gate_log: list[str] = field(default_factory=list)
    abstain_reason: str = ""


# ===========================================================================
# Deterministic pre-checks (no LLM)
# ===========================================================================

def assess_separability(watcher: WatcherOutput) -> tuple[bool, str]:
    """
    Can the movement be attributed to a single driver with the data available?

    Scenario 2's churn spike is the honest failure case: `churned_mrr` is a
    single column with no voluntary/involuntary split, so competitor-driven and
    payment-driven cancellation are mathematically indistinguishable in the
    warehouse. No amount of language modelling fixes a missing column.
    """
    if watcher.primary_kpi != "churn_rate":
        return True, "attribution supported by measured per-driver columns"

    return False, (
        "churned_mrr is not decomposable: the billing schema carries no "
        "voluntary/involuntary split, so competitor-driven and payment-driven "
        "cancellation cannot be separated from the available data"
    )


def evidence_is_balanced(items: list[EvidenceItem]) -> tuple[bool, str]:
    """Deterministic contradiction check over retrieved evidence."""
    tally = evidence_balance(items)
    internal, external = tally.get("internal", 0), tally.get("external", 0)
    total = internal + external
    if total == 0:
        return False, "no evidence retrieved in window"
    if internal == 0 or external == 0:
        return False, f"evidence one-sided ({internal} internal / {external} external)"
    ratio = min(internal, external) / max(internal, external)
    balanced = ratio >= 0.6
    return balanced, f"{internal} internal vs {external} external (ratio {ratio:.2f})"


# ===========================================================================
# Context assembly - what the agents are allowed to see
# ===========================================================================

def build_agent_context(
    watcher: WatcherOutput, evidence: list[EvidenceItem]
) -> str:
    """
    The bounded context object. Everything numeric in here was computed by the
    deterministic layer; agents may cite these values and may not recompute or
    invent others.
    """
    payload = {
        "computed_metrics": watcher.to_context(),
        "retrieved_evidence": [
            {
                "id": e.id,
                "source": e.source,
                "timestamp": e.timestamp,
                "supports_hypothesis": e.hypothesis,
                "text": e.text,
            }
            for e in evidence
        ],
    }
    return json.dumps(payload, indent=2, default=str)


MATH_BAN = (
    "HARD CONSTRAINT: every number you use must be copied verbatim from "
    "computed_metrics. You must not calculate, re-derive, estimate, average, "
    "round or infer any percentage, standard deviation, z-score, total, share "
    "or currency amount. If a number you want is not in computed_metrics, say "
    "that it is not available. You are a reasoner over facts, not a calculator."
)

SYSTEM_PROMPTS: dict[str, str] = {
    "Researcher_Agent": (
        "You are the Researcher. Summarise the retrieved evidence factually in "
        "at most 120 words. State what each document supports and note any "
        "contradiction between documents. Do not take a side and do not "
        "recommend anything. " + MATH_BAN
    ),
    "Bear_Agent": (
        "You are Bear, arguing that the movement has an INTERNAL, controllable "
        "cause — our own systems, deploys or defects. Build the strongest case "
        "you honestly can in at most 130 words, citing evidence ids and the "
        "computed metrics that support you. Engage the strongest opposing "
        "point rather than ignoring it. Never present speculation as fact. "
        "Write in a direct, professional tone as if speaking in a standard "
        "business meeting. Avoid dramatic, poetic, or theatrical language. "
        + MATH_BAN
    ),
    "Bull_Agent": (
        "You are Bull, arguing that the movement has an EXTERNAL cause — "
        "market, competitor or seasonal pressure outside our control. Build "
        "the strongest case you honestly can in at most 130 words, citing "
        "evidence ids and computed metrics. You must address contradictory "
        "evidence rather than ignore it. "
        "Write in a direct, professional tone as if speaking in a standard "
        "business meeting. Avoid dramatic, poetic, or theatrical language. "
        + MATH_BAN
    ),
    "Arbitrator_Agent": (
        "You are the Arbitrator. You did not argue; you judge. Weigh Bear "
        "against Bull strictly on the evidence and the computed metrics. "
        "Return ONLY a JSON object, no prose, with exactly these keys: "
        '{"winning_hypothesis": "internal"|"external"|"none", '
        '"internal_confidence": <int 0-100>, "external_confidence": <int 0-100>, '
        '"confidence": <int 0-100>, "rationale": "<one sentence>"}. '
        "internal_confidence and external_confidence must sum to 100. "
        "`confidence` is the winner's share. If the evidence genuinely does not "
        "separate the two, return a near-even split - do NOT manufacture "
        "certainty to appear decisive. " + MATH_BAN
    ),
    "Storyteller_Agent": (
        "You are the Storyteller. Produce the final recommendation for the "
        "stated persona. Return ONLY a JSON object, no prose, with exactly "
        'these keys: {"driver", "controllable lever", "action", '
        '"expected impact", "owner", "confidence", "monitoring plan"}. '
        "`confidence` must equal the Arbitrator's confidence exactly - you may "
        "not restate, round or revise it. `expected impact` must reference a "
        "value from computed_metrics. Keep each field under 30 words. "
        + MATH_BAN
    ),
    "Debate_Agent": (
        "You are a dual-agent system. First, argue that the movement has an INTERNAL, "
        "controllable cause (Bear). Then, argue that it has an EXTERNAL cause (Bull). "
        "Build the strongest honest cases you can, citing evidence ids and computed metrics. "
        "Engage contradictory evidence. Keep both arguments under 150 words each. "
        + MATH_BAN
    ),
}

DEBATE_ORDER = ["Debate_Agent"]


# ===========================================================================
# LIVE PATH — Groq via OpenAI SDK
# ===========================================================================

def _llm_available() -> bool:
    key = os.getenv("GROQ_API_KEY", "").strip()
    return bool(key) and not key.startswith("placeholder")


def _run_groq_debate(
    context_blob: str, persona: str, meter: Meter, model_name: str
) -> tuple[list[dict[str, str]], str]:
    """Run Debate using the Groq inference endpoint via OpenAI SDK."""
    model = model_name
    client = _get_client()

    transcript: list[dict[str, str]] = []
    for agent_name in DEBATE_ORDER:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPTS[agent_name]},
                    {"role": "user", "content": (
                        f"Persona in the room: {persona}.\n"
                        f"Diagnostic context (authoritative, computed deterministically):\n"
                        f"{context_blob}\n\n"
                        f"Return a concise answer only."
                    )},
                ],
                temperature=0.2,
                max_tokens=256,
            )
            content = response.choices[0].message.content or ""
            if content:
                transcript.append({"agent": agent_name, "message": content.strip()})
            usage = response.usage
            if usage is not None:
                meter.record_call(
                    getattr(usage, "prompt_tokens", 0),
                    getattr(usage, "completion_tokens", 0),
                )
        except Exception as exc:  # noqa: BLE001
            # Graceful degradation: log the error and use a fallback message
            transcript.append({"agent": agent_name, "message": f"[API Error: {exc}]"})

    return transcript, model


def _run_single_agent(
    name: str, prompt: str, meter: Meter, model_name: str,
    json_mode: bool = False,
) -> str:
    """One-shot call for the Arbitrator and the Storyteller using Groq via OpenAI SDK."""
    model = model_name
    client = _get_client()

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPTS[name]},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 256,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        response = client.chat.completions.create(**kwargs)
        reply = response.choices[0].message.content or ""
        usage = response.usage
        if usage is not None:
            meter.record_call(
                getattr(usage, "prompt_tokens", 0),
                getattr(usage, "completion_tokens", 0),
            )
        return reply
    except Exception as exc:  # noqa: BLE001
        # Graceful degradation: return schema-correct fallback per agent type.
        if name == "Arbitrator_Agent":
            return json.dumps({
                "winning_hypothesis": "none",
                "confidence": 50,
                "internal_confidence": 50,
                "external_confidence": 50,
                "rationale": f"Fallback applied due to API error: {exc}",
            })
        else:
            # Storyteller 7-field ActionRecommendation schema
            return json.dumps({
                "driver": "API Timeout",
                "controllable lever": "N/A",
                "action": "N/A",
                "expected impact": "N/A",
                "owner": "N/A",
                "confidence": 0,
                "monitoring plan": "N/A",
            })


# ===========================================================================
# REPLAY PATH - no API key configured
# ===========================================================================
# The debate text below is pre-authored, but it argues over the SAME
# deterministically computed numbers and the SAME retrieved evidence ids as the
# live path. The watcher, the retrieval layer, the safety gate and the schema
# validator all execute for real in this mode. Only the language generation is
# replayed, and the UI labels the run REPLAY so nobody mistakes it for a live
# model call. See README "Running without an API key".
# ===========================================================================

def _replay_debate(
    watcher: WatcherOutput, evidence: list[EvidenceItem], meter: Meter
) -> list[dict[str, str]]:
    kpis = watcher.kpis

    def cite(prefix: str, hypothesis: str | None = None) -> str:
        """First evidence id matching the prefix and (optionally) the side."""
        for e in evidence:
            if not e.id.startswith(prefix):
                continue
            if hypothesis is not None and e.hypothesis != hypothesis:
                continue
            return e.id
        return "n/a"

    if watcher.scenario == 3:
        lat = kpis["p99_latency_ms"]
        researcher = (
            f"Retrieved {len(evidence)} documents inside the incident window. "
            f"{cite('SLK', 'internal')} reports p99 latency on payment-gateway-v2 at "
            f"{lat.value:,.0f} ms against a post-migration baseline of "
            f"{lat.baseline_value:,.0f} ms, and explicitly notes that only three "
            f"days of history exist. {cite('TKT', 'internal')} independently reports customer-"
            f"visible slowness starting at the migration. No document offers a "
            f"competing external explanation. Note the watcher withheld a z-score "
            f"here: {lat.method_note}"
        )
        bear = (
            f"Internal cause. The service was migrated on 2026-08-23 and p99 is "
            f"now {lat.value:,.0f} ms, {lat.delta_relative_pct:,.1f}% above the "
            f"observed baseline. {cite('SLK')} names undersized connection pool "
            f"sizing on the new instances - a configuration we control. Customer "
            f"impact in {cite('TKT', 'internal')} starts at the migration, not before it. "
            f"I accept the caveat that three days is thin history, which is "
            f"exactly why I am arguing from the migration event and the config, "
            f"not from a seasonal baseline we do not have."
        )
        bull = (
            f"I cannot build an honest external case here. There is no competitor, "
            f"seasonal or market document in the retrieved window, and the latency "
            f"step change coincides with a deploy we performed. The strongest "
            f"external-facing point available to me is that with only "
            f"{lat.baseline_n} baseline samples we cannot exclude normal variance "
            f"statistically - but that is an argument for lower confidence, not "
            f"for an external cause. I concede the internal hypothesis."
        )
    else:
        mrr = kpis.get("mrr")
        churn = kpis["churn_rate"]
        gw = kpis["gateway_timeout_errors"]
        mrr_txt = (
            f"MRR fell {abs(mrr.delta_relative_pct):.1f}% to ${mrr.value:,.0f} "
            f"(z = {mrr.z_score:.1f}); "
            if mrr and mrr.access_granted else
            "MRR detail is restricted for this persona; "
        )
        researcher = (
            f"Retrieved {len(evidence)} documents inside the incident window, and "
            f"they contradict each other. {mrr_txt}churn rose "
            f"{churn.delta_relative_pct:.1f}% to {churn.value:.2f}%. "
            f"{cite('SLK', 'internal')} attributes failures to Stripe API v2.2 "
            f"timeouts, and gateway errors peaked at {gw.value:,.0f} at 10:02. "
            f"Against that, {cite('TKT', 'external')} and "
            f"{cite('SLK', 'external')} attribute cancellations to a "
            f"competitor's 50%-off campaign launched the same day. Both accounts "
            f"are supported; neither is refuted."
        )
        bear = (
            f"Internal cause. Gateway timeout errors peaked at {gw.value:,.0f} "
            f"against a quiescent baseline of {gw.baseline_value:.1f} - a step "
            f"change, not drift. {cite('SLK')} names the mechanism precisely: a "
            f"rollback constraint shipped last night holds connections open until "
            f"they expire, so recurring charges fail. The customer in "
            f"{cite('TKT', 'internal')} reports a valid card declining three "
            f"times. Bull will "
            f"point at the competitor campaign, and it is real - but a discount "
            f"campaign does not produce a 10:02 error spike in our own payment "
            f"path. This cause is ours and it is controllable today."
        )
        bull = (
            f"External pressure is real and Bear is under-weighting it. A sales "
            f"thread reports three cancellations in one morning, all citing "
            f"Competitor X's 50%-off annual campaign launched that day, and "
            f"{cite('TKT', 'external')} is an explicit procurement decision with "
            f"the product "
            f"working fine. Churn is up {churn.delta_relative_pct:.1f}%, and "
            f"cancellations of that kind do not show up as gateway errors - which "
            f"is precisely why Bear's error-spike argument cannot account for "
            f"them. I accept the timeout spike is internal. I dispute that it "
            f"explains the whole movement."
        )

    transcript = [
        {"agent": "Researcher_Agent", "message": researcher},
        {"agent": "Bear_Agent", "message": bear},
        {"agent": "Bull_Agent", "message": bull},
    ]

    # Replay-mode telemetry: measured from the actual text volume that would
    # have been exchanged, labelled as an estimate in the UI.
    ctx_chars = len(build_agent_context(watcher, evidence))
    for turn in transcript:
        meter.record_call(
            prompt_tokens=(ctx_chars + len(SYSTEM_PROMPTS[turn["agent"]])) // 4,
            completion_tokens=len(turn["message"]) // 4,
        )
    return transcript


def _replay_arbitration(
    watcher: WatcherOutput,
    evidence: list[EvidenceItem],
    separable: bool,
    meter: Meter,
) -> ArbitrationResult:
    balanced, _ = evidence_is_balanced(evidence)

    if not separable and balanced:
        result = ArbitrationResult(
            winning_hypothesis="none",
            confidence=50,
            internal_confidence=50,
            external_confidence=50,
            rationale=(
                "Both hypotheses are independently supported and the available "
                "schema cannot separate their contributions."
            ),
        )
    elif watcher.scenario == 3:
        result = ArbitrationResult(
            winning_hypothesis="internal",
            confidence=72,
            internal_confidence=72,
            external_confidence=28,
            rationale=(
                "Latency step change coincides with a controlled migration and "
                "no external explanation was retrieved; confidence capped "
                "because the baseline is only three days deep."
            ),
        )
    else:
        top = watcher.drivers[0] if watcher.drivers else None
        internal = int(round(top.share_pct)) if top else 88
        internal = max(60, min(92, internal))
        result = ArbitrationResult(
            winning_hypothesis="internal",
            confidence=internal,
            internal_confidence=internal,
            external_confidence=100 - internal,
            rationale=(
                "Internal payment-gateway failure carries the larger measured "
                "adverse exposure, with external competitor pressure a real but "
                "smaller concurrent contributor."
            ),
        )

    meter.record_call(prompt_tokens=520, completion_tokens=90)
    return result


def _replay_story(
    watcher: WatcherOutput, arb: ArbitrationResult, persona: str, meter: Meter
) -> dict[str, Any]:
    spec = persona_spec(persona)
    technical = spec["lens"] == "technical"
    kpis = watcher.kpis

    if watcher.scenario == 3:
        lat = kpis["p99_latency_ms"]
        payload = {
            "driver": "payment-gateway-v2 p99 latency regression after migration",
            "controllable lever": (
                "Connection pool sizing on the migrated service"
                if technical else "Service reliability of the migrated payment path"
            ),
            "action": (
                "Raise connection pool size on payment-gateway-v2 and re-run the "
                "migration load profile before the next billing cycle"
                if technical else
                "Approve an engineering fix window for the migrated payment "
                "service before the next billing run"
            ),
            "expected impact": (
                f"Return p99 from {lat.value:,.0f} ms toward the "
                f"{lat.baseline_value:,.0f} ms post-migration baseline"
            ),
            "owner": "Director of Engineering",
            "confidence": arb.confidence,
            "monitoring plan": (
                "Track p99_latency_ms hourly for 48 hours; hold the change if the "
                "baseline does not re-establish within 14 days of history"
            ),
        }
    else:
        mrr = kpis.get("mrr")
        impact = (
            f"Recovers the ${abs(mrr.delta_absolute):,.0f} single-day MRR "
            f"shortfall as failed charges retry"
            if mrr and mrr.access_granted and mrr.delta_absolute is not None
            else "Restores successful recurring charge processing on the "
                 "payment path"
        )
        payload = {
            "driver": "Payment gateway timeout on Stripe API v2.2",
            "controllable lever": (
                "Stripe API version pinning and the rollback constraint shipped "
                "in the last deploy" if technical else "Payment processing continuity"
            ),
            "action": (
                "Roll back the payment service to Stripe API v2.1 and remove the "
                "connection-holding rollback constraint"
                if technical else
                "Authorise an immediate rollback of the payment integration and "
                "notify affected accounts with a retry confirmation"
            ),
            "expected impact": impact,
            "owner": "Director of Engineering",
            "confidence": arb.confidence,
            "monitoring plan": (
                "Track gateway_timeout_errors per minute and failed_transactions_usd "
                "hourly for 48 hours; escalate if errors exceed 100 in any minute"
            ),
        }

    meter.record_call(prompt_tokens=610, completion_tokens=150)
    return payload


# ===========================================================================
# Orchestration
# ===========================================================================

def run_diagnostic_pipeline(
    persona: str,
    scenario: int,
    on_stage: Any = None,
    use_live_mode: bool = True,
    model_name: str = "qwen/qwen3.8-27b",
) -> DiagnosticResult:
    """
    Execute the full pipeline for one persona and scenario.

    `on_stage(name)` is an optional progress callback used by the UI.
    """
    def stage(name: str) -> None:
        if on_stage:
            on_stage(name)

    live = use_live_mode and _llm_available()
    effective_model = model_name if live else "replay"
    meter = Meter(model=effective_model, mode="live" if live else "replay")
    gate_log: list[str] = []

    # ---- 1. Deterministic watcher (no LLM) --------------------------------
    stage("watcher")
    watcher = query_kpi_metrics(scenario, persona)
    gate_log.append(
        f"watcher: {len([k for k in watcher.kpis.values() if k.access_granted])} "
        f"KPIs computed in SQL/Python, "
        f"{len([k for k in watcher.kpis.values() if not k.access_granted])} "
        f"suppressed by RBAC before query"
    )

    # ---- 2. Researcher / retrieval ---------------------------------------
    stage("researcher")
    evidence = retrieve_unstructured_context(scenario, ANOMALY_TIMESTAMP)
    if scenario == 2:
        # Ambiguous fixture: take a balanced slice so the contradiction is
        # genuine rather than an artefact of retrieval ordering.
        internal = [e for e in evidence if e.hypothesis == "internal"][:2]
        external = [e for e in evidence if e.hypothesis == "external"][:2]
        evidence = sorted(internal + external, key=lambda e: e.distance)
    gate_log.append(
        f"researcher: {len(evidence)} documents retrieved in window, "
        f"balance {evidence_balance(evidence)}"
    )

    context_blob = build_agent_context(watcher, evidence)

    # ---- 3. Adversarial debate -------------------------------------------
    stage("debate")
    if live:
        transcript, effective_model = _run_groq_debate(
            context_blob, persona, meter, model_name
        )
    else:
        transcript = _replay_debate(watcher, evidence, meter)

    # ---- 4. Arbitration ---------------------------------------------------
    stage("arbitrator")
    separable, sep_reason = assess_separability(watcher)
    balanced, bal_reason = evidence_is_balanced(evidence)

    if live:
        debate_text = "\n\n".join(
            f"{t['agent']}: {t['message']}" for t in transcript
        )
        arb_prompt = (
            f"Context:\n{context_blob}\n\nDebate:\n{debate_text}\n\n"
            f"Deterministic note - attribution separable: {separable} "
            f"({sep_reason}). Evidence balance: {bal_reason}.\n"
            f"Return the JSON verdict now."
        )
        raw = _run_single_agent("Arbitrator_Agent", arb_prompt, meter, model_name, json_mode=True)
        try:
            from outputs.schema import extract_json_block
            arb = ArbitrationResult.model_validate(extract_json_block(raw))
        except Exception as exc:                     # noqa: BLE001
            # FAIL CLOSED: unparsable arbitration is zero confidence, not a guess.
            gate_log.append(f"gate: arbitration unparsable ({exc}) -> confidence 0")
            arb = ArbitrationResult(
                winning_hypothesis="none",
                confidence=0,
                internal_confidence=50,
                external_confidence=50,
                rationale="Arbitrator output could not be parsed; failing closed.",
            )
    else:
        arb = _replay_arbitration(watcher, evidence, separable, meter)

    gate_log.append(
        f"arbitrator: internal {arb.internal_confidence} / external "
        f"{arb.external_confidence}, winner={arb.winning_hypothesis}"
    )

    # ---- 5. PYTHON SAFETY GATE -------------------------------------------
    threshold = abstain_threshold()
    abstain_reason = ""

    if arb.confidence < threshold:
        abstain_reason = (
            f"Arbitrator confidence {arb.confidence} is below the {threshold} "
            f"threshold required for an automated recommendation."
        )
        gate_log.append(f"GATE 1 FAILED: confidence {arb.confidence} < {threshold}")
    elif not separable:
        abstain_reason = f"Attribution is not separable from the data: {sep_reason}."
        gate_log.append("GATE 2 FAILED: attribution non-separable")

    if abstain_reason:
        gate_log.append("Storyteller_Agent NOT constructed - abstain path taken")
        stage("abstain")
        tel = meter.stop()
        return DiagnosticResult(
            status="abstain",
            scenario=scenario,
            persona=persona,
            narrative=_abstain_narrative(watcher, evidence, arb, sep_reason),
            structured_action=None,
            confidence_score=arb.confidence,
            arbitration=arb.model_dump(),
            debate_transcript=transcript,
            evidence=[e.to_dict() for e in evidence],
            watcher=_watcher_payload(watcher),
            telemetry=tel.to_dict(),
            gate_log=gate_log,
            abstain_reason=abstain_reason,
        )

    gate_log.append(
        f"GATE PASSED: confidence {arb.confidence} >= {threshold} and "
        f"attribution separable - Storyteller authorised"
    )

    # ---- 6. Storyteller (only reachable past the gate) --------------------
    stage("storyteller")
    if live:
        story_prompt = (
            f"Persona: {persona} ({persona_spec(persona)['narrative_focus'].strip()})\n"
            f"Arbitrator confidence (use exactly): {arb.confidence}\n"
            f"Winning hypothesis: {arb.winning_hypothesis}\n"
            f"Context:\n{context_blob}\n\nReturn the JSON action now."
        )
        raw = _run_single_agent("Storyteller_Agent", story_prompt, meter, model_name, json_mode=True)
        action: ActionRecommendation = validate_action(raw)
    else:
        action = validate_action(_replay_story(watcher, arb, persona, meter))

    if action.confidence != arb.confidence:
        raise SchemaViolation(
            f"Storyteller altered the Arbitrator's confidence "
            f"({arb.confidence} -> {action.confidence}); rejecting output."
        )

    narrative = _success_narrative(watcher, evidence, arb, persona)
    tel = meter.stop()

    return DiagnosticResult(
        status="success",
        scenario=scenario,
        persona=persona,
        narrative=narrative,
        structured_action=action.canonical(),
        confidence_score=arb.confidence,
        arbitration=arb.model_dump(),
        debate_transcript=transcript,
        evidence=[e.to_dict() for e in evidence],
        watcher=_watcher_payload(watcher),
        telemetry=tel.to_dict(),
        gate_log=gate_log,
    )


def _watcher_payload(watcher: WatcherOutput) -> dict[str, Any]:
    return {
        "scenario": watcher.scenario,
        "scenario_label": watcher.scenario_label,
        "as_of": watcher.as_of,
        "primary_kpi": watcher.primary_kpi,
        "anomaly_detected": watcher.anomaly_detected,
        "attribution_note": watcher.attribution_note,
        "kpis": {k: v.to_dict() for k, v in watcher.kpis.items()},
        "drivers": [
            {
                "name": d.name,
                "hypothesis": d.hypothesis,
                "measured_delta_usd": d.measured_delta_usd,
                "share_pct": d.share_pct,
                "basis": d.basis,
            }
            for d in watcher.drivers
        ],
    }


def _success_narrative(
    watcher: WatcherOutput,
    evidence: list[EvidenceItem],
    arb: ArbitrationResult,
    persona: str,
) -> str:
    spec = persona_spec(persona)
    technical = spec["lens"] == "technical"
    kpis = watcher.kpis

    if watcher.scenario == 3:
        lat = kpis["p99_latency_ms"]
        head = (
            f"**{lat.display_name}** on `payment-gateway-v2` peaked at "
            f"**{lat.value:,.0f} ms**, {lat.delta_relative_pct:,.0f}% above the "
            f"{lat.baseline_value:,.0f} ms baseline observed since migration."
        )
        caveat = (
            f"\n\nThis KPI has **{lat.baseline_n} hourly samples across 3 days**. "
            f"The semantic contract requires 14 days before a z-score baseline is "
            f"considered supportable, so the watcher used a *{lat.method}* instead "
            f"and the arbitrator capped confidence at {arb.confidence}. "
            f"The finding is real; the statistical power behind it is not yet."
        )
        body = (
            "Root cause is the connection pool sizing on the migrated instances "
            "(`SLK-1003`), with customer-visible slowness confirmed in `TKT-8911`."
            if technical else
            "Engineering has traced this to capacity configuration on the newly "
            "migrated payment service, with customer complaints confirming impact."
        )
        return f"{head}{caveat}\n\n{body}"

    mrr = kpis.get("mrr")
    churn = kpis["churn_rate"]
    gw = kpis["gateway_timeout_errors"]

    if mrr and mrr.access_granted:
        head = (
            f"**MRR** fell **{abs(mrr.delta_relative_pct):.1f}%** to "
            f"**${mrr.value:,.0f}** (z = {mrr.z_score:.1f} against a "
            f"{mrr.baseline_n}-day baseline), while **churn** rose "
            f"**{churn.delta_relative_pct:.1f}%** to {churn.value:.2f}%."
        )
    else:
        head = (
            f"**Churn** rose **{churn.delta_relative_pct:.1f}%** to "
            f"{churn.value:.2f}% (z = {churn.z_score:.1f}). *MRR detail is "
            f"restricted for this persona.*"
        )

    ranked = ""
    if watcher.drivers:
        lines = []
        for d in watcher.drivers:
            amount = (
                f" (${d.measured_delta_usd:,.0f} measured exposure)"
                if d.measured_delta_usd is not None else ""
            )
            lines.append(f"- **{d.share_pct:.1f}%** — {d.name}{amount}")
        ranked = (
            "\n\n**Ranked drivers** — attributed deterministically in SQL, "
            "not by the debate:\n" + "\n".join(lines)
            + f"\n\n<small>{watcher.attribution_note}</small>"
        )

    body = (
        f"\n\nTwo causes ran simultaneously. Gateway timeout errors peaked at "
        f"**{gw.value:,.0f}** at 10:02 against a baseline of "
        f"{gw.baseline_value:.1f} — the Stripe API v2.2 rollback constraint "
        f"holding connections open (`SLK-1001`, `TKT-8902`). Concurrently, a "
        f"competitor discount campaign drove voluntary cancellations "
        f"(`SLK-1002`, `TKT-8905`)."
    )
    if technical:
        body += (
            "\n\nThe controllable failure is ours: revert the API version and "
            "drop the connection-holding constraint. The competitor campaign is "
            "not an engineering action."
        )
    else:
        body += (
            "\n\nThe larger share is internal and fixable today. The competitor "
            "campaign is real but smaller, and needs a commercial response "
            "rather than an engineering one."
        )
    return head + ranked + body


def _abstain_narrative(
    watcher: WatcherOutput,
    evidence: list[EvidenceItem],
    arb: ArbitrationResult,
    sep_reason: str,
) -> str:
    churn = watcher.kpis["churn_rate"]
    internal = [e.id for e in evidence if e.hypothesis == "internal"]
    external = [e.id for e in evidence if e.hypothesis == "external"]

    return (
        f"The movement itself is not in doubt: **churn rose "
        f"{churn.delta_relative_pct:.1f}%** to {churn.value:.2f}% "
        f"(z = {churn.z_score:.1f}). What cannot be established is **why**.\n\n"
        f"**Internal logs suggest** a payment-gateway failure — "
        f"{', '.join(f'`{i}`' for i in internal)} — where recurring charges fail "
        f"and subscriptions lapse involuntarily.\n\n"
        f"**External evidence suggests** competitor pricing pressure — "
        f"{', '.join(f'`{i}`' for i in external)} — where customers cancel "
        f"deliberately and say so.\n\n"
        f"Both produce the same churn number. The arbitrator split "
        f"**{arb.internal_confidence}/{arb.external_confidence}**, and "
        f"independently of that split, {sep_reason}.\n\n"
        f"**This is a missing-column problem, not a reasoning problem.** No "
        f"model can separate what the warehouse does not record. A human should "
        f"pull the cancellation reason codes from the billing provider and "
        f"re-run this diagnosis."
    )


# ===========================================================================
# CLI
# ===========================================================================

def _cli() -> None:
    scenario = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    persona = sys.argv[2] if len(sys.argv) > 2 else "VP of Revenue (Executive)"

    mode = "LIVE (Groq + Qwen 3.8)" if _llm_available() else "REPLAY (no API key)"
    print("=" * 74)
    print(f"ENTERPRISE BI ENGINE - scenario {scenario} - {persona}")
    print(f"mode: {mode}")
    print("=" * 74)

    res = run_diagnostic_pipeline(persona, scenario,
                                  on_stage=lambda s: print(f"  [stage] {s}"))

    print(f"\nSTATUS: {res.status.upper()}   confidence={res.confidence_score}")
    print(f"arbitration: internal {res.arbitration['internal_confidence']} / "
          f"external {res.arbitration['external_confidence']}")

    print("\n--- GATE LOG " + "-" * 60)
    for line in res.gate_log:
        print(f"  {line}")

    print("\n--- NARRATIVE " + "-" * 59)
    print(res.narrative[:1100])

    if res.structured_action:
        print("\n--- ACTION (schema-validated, PRD canonical keys) " + "-" * 23)
        print(json.dumps(res.structured_action, indent=2))
    else:
        print("\n--- ACTION " + "-" * 62)
        print("  none - system abstained")
        print(f"  reason: {res.abstain_reason}")

    print("\n--- TELEMETRY " + "-" * 59)
    t = res.telemetry
    print(f"  mode={t['mode']}  model={t['model']}")
    print(f"  latency={t['latency_ms']:.1f} ms   calls={t['model_calls']}   "
          f"tokens={t['total_tokens']}   cost=${t['estimated_cost_usd']:.6f}")

    print("\n--- DEBATE TRANSCRIPT " + "-" * 51)
    for turn in res.debate_transcript:
        print(f"\n  [{turn['agent']}]")
        print("   " + turn["message"][:420])
    print()


if __name__ == "__main__":
    _cli()
