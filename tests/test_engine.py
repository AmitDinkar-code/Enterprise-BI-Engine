"""
Acceptance tests for the Enterprise BI Engine.

Mapped to the PRD acceptance criteria (AC-*), the TRD engineering requirements
(TR-*), the App Flow contract (FLOW-*) and the Round 2 brief's minimum
prototype expectations.

Run:  pytest tests/ -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents import (                                          # noqa: E402
    ANOMALY_TIMESTAMP,
    assess_separability,
    build_agent_context,
    evidence_is_balanced,
    run_diagnostic_pipeline,
)
from config.contract import abstain_threshold, get_contract    # noqa: E402
from outputs.schema import (                                   # noqa: E402
    CANONICAL_KEYS,
    SchemaViolation,
    validate_action,
)
from rag.retrieval import retrieve_unstructured_context        # noqa: E402
from watcher.deterministic import check_access, query_kpi_metrics  # noqa: E402

VP = "VP of Revenue (Executive)"
ENG = "Director of Engineering (Technical)"


# ===========================================================================
# G2 - seeded data determinism
# ===========================================================================

class TestDataFoundation:
    def test_mrr_anomaly_exact(self):
        """AC-01: the seeded anomaly is exactly -4.0%."""
        w = query_kpi_metrics(1, VP)
        mrr = w.kpis["mrr"]
        assert mrr.value == 960000.0
        assert mrr.baseline_value == 1000000.0
        assert mrr.delta_relative_pct == -4.0

    def test_churn_spike_exact(self):
        """PRD scenario 1: churn spikes 12%."""
        churn = query_kpi_metrics(1, VP).kpis["churn_rate"]
        assert churn.delta_relative_pct == 12.0

    def test_gateway_peak(self):
        gw = query_kpi_metrics(1, VP).kpis["gateway_timeout_errors"]
        assert gw.value == 500.0

    def test_sparse_history_is_three_days(self):
        """AC-03: exactly three calendar days of latency history."""
        import duckdb
        con = duckdb.connect(str(ROOT / "enterprise_bi.duckdb"), read_only=True)
        days = con.execute(
            "SELECT COUNT(DISTINCT CAST(ts AS DATE)) FROM aws_cloudwatch_latency"
        ).fetchone()[0]
        con.close()
        assert days == 3

    def test_seeding_is_idempotent(self):
        """Re-running setup_db.py must not duplicate rows."""
        import duckdb
        con = duckdb.connect(str(ROOT / "enterprise_bi.duckdb"), read_only=True)
        before = con.execute("SELECT COUNT(*) FROM billing_mrr").fetchone()[0]
        con.close()

        subprocess.run(
            [sys.executable, "setup_db.py"], cwd=ROOT,
            check=True, capture_output=True,
        )

        con = duckdb.connect(str(ROOT / "enterprise_bi.duckdb"), read_only=True)
        after = con.execute("SELECT COUNT(*) FROM billing_mrr").fetchone()[0]
        con.close()
        assert before == after


# ===========================================================================
# G3 - the LLM boundary
# ===========================================================================

class TestDeterministicBoundary:
    def test_math_survives_without_any_api_key(self, monkeypatch):
        """
        AC-04 / TR-007: the strongest proof of the boundary. With no API key
        present, every KPI figure is still computed correctly.
        """
        monkeypatch.setenv("OPENAI_API_KEY", "")
        w = query_kpi_metrics(1, VP)
        assert w.kpis["mrr"].delta_relative_pct == -4.0
        assert w.kpis["mrr"].z_score is not None
        assert w.kpis["churn_rate"].delta_relative_pct == 12.0

    def test_zscore_is_reproducible(self):
        a = query_kpi_metrics(1, VP).kpis["mrr"].z_score
        b = query_kpi_metrics(1, VP).kpis["mrr"].z_score
        assert a == b

    def test_sparse_history_withholds_zscore(self):
        """
        AC-03: three days is not enough for a defensible baseline, so the
        watcher degrades instead of emitting an unsupportable statistic.
        """
        lat = query_kpi_metrics(3, VP).kpis["p99_latency_ms"]
        assert lat.z_score is None
        assert lat.low_power is True
        assert lat.method == "range_check"
        assert lat.value == 3450.0          # still produces a finite answer

    def test_no_agent_has_tools_or_code_execution(self):
        """TR-007: agents cannot reach the database."""
        src = (ROOT / "agents.py").read_text()
        assert "tools=[]" in src
        for banned in ("code_execution_config=True", "register_function",
                       "function_map"):
            assert banned not in src

    def test_driver_ranking_is_deterministic_and_sums(self):
        """Brief: 'identifying and ranking the drivers'."""
        w = query_kpi_metrics(1, VP)
        assert len(w.drivers) == 2
        assert w.drivers[0].share_pct >= w.drivers[1].share_pct
        assert abs(sum(d.share_pct for d in w.drivers) - 100.0) < 0.2
        assert w.drivers[0].hypothesis == "internal"


# ===========================================================================
# G5 - RBAC, enforced at the query layer
# ===========================================================================

class TestRBAC:
    def test_vp_sees_mrr(self):
        assert query_kpi_metrics(1, VP).kpis["mrr"].access_granted is True

    def test_engineering_blocked_from_mrr(self):
        """AC-09 / SEC-03."""
        assert query_kpi_metrics(1, ENG).kpis["mrr"].access_granted is False

    def test_engineering_keeps_technical_kpis(self):
        w = query_kpi_metrics(1, ENG)
        assert w.kpis["gateway_timeout_errors"].access_granted is True
        assert w.kpis["p99_latency_ms"].access_granted is True
        assert w.kpis["churn_rate"].access_granted is True

    def test_restricted_value_never_enters_process(self):
        """
        SEC-04: hiding a field in the UI is not authorisation. The restricted
        value must not exist anywhere in the object graph.
        """
        w = query_kpi_metrics(1, ENG)
        assert w.kpis["mrr"].value is None
        blob = json.dumps(w.to_context(), default=str)
        assert "960000" not in blob
        assert "mrr" not in w.to_context()["kpis"]

    def test_restricted_value_absent_from_agent_context(self):
        w = query_kpi_metrics(1, ENG)
        ev = retrieve_unstructured_context(1, ANOMALY_TIMESTAMP)
        ctx = build_agent_context(w, ev)
        assert "960000" not in ctx
        assert "960,000" not in ctx

    def test_currency_detail_withheld_but_ranking_preserved(self):
        w = query_kpi_metrics(1, ENG)
        assert all(d.measured_delta_usd is None for d in w.drivers)
        assert w.drivers[0].share_pct > 0

    def test_contract_drives_access_not_hardcoding(self):
        granted, _ = check_access("mrr", ENG)
        assert granted is False
        granted, _ = check_access("mrr", VP)
        assert granted is True


# ===========================================================================
# G4 - strict structured output
# ===========================================================================

class TestOutputContract:
    def test_valid_action_accepted(self):
        payload = {
            "driver": "Payment gateway timeout",
            "controllable lever": "Stripe API version",
            "action": "Roll back to v2.1",
            "expected impact": "Recovers $40,000",
            "owner": "Director of Engineering",
            "confidence": 88,
            "monitoring plan": "Track error rate hourly for 48h",
        }
        assert validate_action(payload).confidence == 88

    def test_missing_field_rejected(self):
        payload = {
            "driver": "x" * 5, "controllable lever": "y" * 5,
            "action": "z" * 5, "expected impact": "w" * 5,
            "owner": "me", "confidence": 88,
            # monitoring plan missing
        }
        with pytest.raises(SchemaViolation):
            validate_action(payload)

    def test_confidence_out_of_range_rejected(self):
        payload = {
            "driver": "aaa", "controllable lever": "bbb", "action": "ccc",
            "expected impact": "ddd", "owner": "ee", "confidence": 150,
            "monitoring plan": "fff",
        }
        with pytest.raises(SchemaViolation):
            validate_action(payload)

    def test_non_numeric_confidence_rejected(self):
        payload = {
            "driver": "aaa", "controllable lever": "bbb", "action": "ccc",
            "expected impact": "ddd", "owner": "ee",
            "confidence": "very high", "monitoring plan": "fff",
        }
        with pytest.raises(SchemaViolation):
            validate_action(payload)

    def test_extra_field_rejected(self):
        payload = {
            "driver": "aaa", "controllable lever": "bbb", "action": "ccc",
            "expected impact": "ddd", "owner": "ee", "confidence": 80,
            "monitoring plan": "fff", "sneaky": "extra",
        }
        with pytest.raises(SchemaViolation):
            validate_action(payload)

    def test_garbage_text_rejected(self):
        with pytest.raises(SchemaViolation):
            validate_action("I think the answer is probably the gateway.")

    def test_canonical_keys_match_prd_exactly(self):
        """AC-10: exactly the PRD's key set, spaces and all."""
        result = run_diagnostic_pipeline(VP, 1)
        assert list(result.structured_action.keys()) == CANONICAL_KEYS


# ===========================================================================
# G6 - the abstain safety gate
# ===========================================================================

class TestAbstainGate:
    def test_scenario_2_abstains(self):
        """AC-02 / TR-011."""
        r = run_diagnostic_pipeline(VP, 2)
        assert r.status == "abstain"
        assert r.structured_action is None
        assert r.confidence_score < abstain_threshold()

    def test_scenario_2_confidence_is_split(self):
        r = run_diagnostic_pipeline(VP, 2)
        assert r.arbitration["internal_confidence"] == 50
        assert r.arbitration["external_confidence"] == 50

    def test_storyteller_never_constructed_on_abstain(self):
        r = run_diagnostic_pipeline(VP, 2)
        assert any("NOT constructed" in line for line in r.gate_log)

    def test_transcript_survives_abstain(self):
        """Abstain withholds the recommendation, not the evidence."""
        r = run_diagnostic_pipeline(VP, 2)
        assert len(r.debate_transcript) >= 3
        assert len(r.evidence) >= 2

    def test_non_separability_is_an_independent_gate(self):
        """
        Even a high-confidence verdict cannot pass when the data cannot
        separate the hypotheses.
        """
        w = query_kpi_metrics(2, VP)
        separable, reason = assess_separability(w)
        assert separable is False
        assert "decomposable" in reason

    def test_evidence_balance_detects_contradiction(self):
        ev = retrieve_unstructured_context(2, ANOMALY_TIMESTAMP)
        internal = [e for e in ev if e.hypothesis == "internal"][:2]
        external = [e for e in ev if e.hypothesis == "external"][:2]
        balanced, _ = evidence_is_balanced(internal + external)
        assert balanced is True

    def test_success_scenarios_clear_the_threshold(self):
        """Demo stability: 1 and 3 must not abstain."""
        for scenario in (1, 3):
            for persona in (VP, ENG):
                r = run_diagnostic_pipeline(persona, scenario)
                assert r.status == "success", f"scenario {scenario}/{persona}"
                assert r.confidence_score >= abstain_threshold()

    def test_repeated_runs_are_stable(self):
        """Five consecutive runs must not flip state mid-demo."""
        states = [run_diagnostic_pipeline(VP, 1).status for _ in range(5)]
        assert states == ["success"] * 5


# ===========================================================================
# G4 - agent pipeline / confidence integrity
# ===========================================================================

class TestPipeline:
    def test_five_agent_roles_defined(self):
        """AC-06."""
        from agents import SYSTEM_PROMPTS
        for name in ("Researcher_Agent", "Bear_Agent", "Bull_Agent",
                     "Arbitrator_Agent", "Storyteller_Agent"):
            assert name in SYSTEM_PROMPTS

    def test_bear_and_bull_take_opposing_sides(self):
        r = run_diagnostic_pipeline(VP, 1)
        agents_seen = [t["agent"] for t in r.debate_transcript]
        assert "Bear_Agent" in agents_seen and "Bull_Agent" in agents_seen
        bear = next(t["message"] for t in r.debate_transcript
                    if t["agent"] == "Bear_Agent")
        bull = next(t["message"] for t in r.debate_transcript
                    if t["agent"] == "Bull_Agent")
        assert bear != bull
        assert "internal" in bear.lower() or "gateway" in bear.lower()
        assert "external" in bull.lower() or "competitor" in bull.lower()

    def test_storyteller_preserves_arbitrator_confidence(self):
        """TRD section 6: the confidence value must be preserved exactly."""
        r = run_diagnostic_pipeline(VP, 1)
        assert r.structured_action["confidence"] == r.arbitration["confidence"]
        assert r.structured_action["confidence"] == r.confidence_score

    def test_scenario_1_names_both_causes(self):
        """AC-01: both simultaneous causes must be identified."""
        r = run_diagnostic_pipeline(VP, 1)
        text = (r.narrative + json.dumps(r.structured_action)).lower()
        assert "gateway" in text or "stripe" in text
        assert "competitor" in text

    def test_narrative_numbers_trace_to_watcher(self):
        """Every figure in the narrative must exist in the computed payload."""
        r = run_diagnostic_pipeline(VP, 1)
        assert "960,000" in r.narrative
        assert "4.0%" in r.narrative
        assert r.watcher["kpis"]["mrr"]["value"] == 960000.0

    def test_persona_narratives_differ(self):
        """Brief: two personas receive different narratives and actions."""
        vp = run_diagnostic_pipeline(VP, 1)
        eng = run_diagnostic_pipeline(ENG, 1)
        assert vp.narrative != eng.narrative
        assert vp.structured_action["action"] != eng.structured_action["action"]

    def test_evidence_is_time_windowed(self):
        """FR-R-01: retrieval is linked to the anomaly window."""
        ev = retrieve_unstructured_context(1, ANOMALY_TIMESTAMP)
        assert len(ev) > 0
        assert all(e.in_window for e in ev)

    def test_scenario_3_retrieves_relevant_evidence(self):
        ev = retrieve_unstructured_context(3, ANOMALY_TIMESTAMP)
        assert ev[0].id == "SLK-1003"       # the migration/latency thread


# ===========================================================================
# G7 - telemetry
# ===========================================================================

class TestTelemetry:
    def test_all_four_metrics_present(self):
        """AC-11 plus the brief's model-call count."""
        t = run_diagnostic_pipeline(VP, 1).telemetry
        for key in ("latency_ms", "total_tokens", "estimated_cost_usd",
                    "model_calls"):
            assert key in t

    def test_latency_and_calls_are_real(self):
        t = run_diagnostic_pipeline(VP, 1).telemetry
        assert t["latency_ms"] > 0
        assert t["model_calls"] >= 5

    def test_cost_reconciles_with_tokens(self):
        from telemetry.meter import estimate_cost
        t = run_diagnostic_pipeline(VP, 1).telemetry
        expected = estimate_cost(t["prompt_tokens"], t["completion_tokens"])
        assert abs(t["estimated_cost_usd"] - expected) < 1e-9

    def test_abstain_run_still_meters(self):
        t = run_diagnostic_pipeline(VP, 2).telemetry
        assert t["latency_ms"] > 0
        assert t["model_calls"] > 0

    def test_mode_is_labelled(self):
        t = run_diagnostic_pipeline(VP, 1).telemetry
        assert t["mode"] in ("live", "replay")


# ===========================================================================
# Semantic contract - the brief's minimum expectation #2
# ===========================================================================

class TestSemanticContract:
    def test_every_kpi_declares_the_required_facets(self):
        contract = get_contract()
        required = ("definition", "calculation", "grain", "refresh_cadence",
                    "source_system", "lineage", "drivers", "materiality",
                    "access")
        for key, spec in contract["kpis"].items():
            for facet in required:
                assert facet in spec, f"{key} missing {facet}"

    def test_four_kpis_across_multiple_sources_and_grains(self):
        """Brief: 3-5 KPIs, 2-3 sources, differing grains."""
        contract = get_contract()
        assert 3 <= len(contract["kpis"]) <= 5
        sources = {s["source_system"] for s in contract["kpis"].values()}
        grains = {s["grain"] for s in contract["kpis"].values()}
        assert len(sources) >= 3
        assert len(grains) >= 3

    def test_exactly_two_personas_and_three_scenarios(self):
        """FLOW-02 / FLOW-04."""
        contract = get_contract()
        assert len(contract["personas"]) == 2
        assert len(contract["scenarios"]) == 3

    def test_materiality_has_both_halves(self):
        """Brief: materiality by business impact AND statistical significance."""
        for spec in get_contract()["kpis"].values():
            mat = spec["materiality"]
            assert "statistical" in mat and "business" in mat


# ===========================================================================
# G1 - repository hygiene
# ===========================================================================

class TestRepositoryHygiene:
    def test_no_rejected_technology(self):
        """TRD section 14."""
        banned = ("langgraph", "langchain", "pinecone", "supabase", "nextauth")
        for path in ROOT.rglob("*.py"):
            # Skip the venv and this scanner, which necessarily contains the
            # very tokens it searches for.
            if ".venv" in str(path) or path.name == "test_engine.py":
                continue
            text = path.read_text(errors="ignore").lower()
            for token in banned:
                assert token not in text, f"{token} found in {path.name}"

    def test_no_hardcoded_secret(self):
        for path in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.toml")):
            if ".venv" in str(path) or path.name == "test_engine.py":
                continue
            text = path.read_text(errors="ignore")
            assert "sk-proj-" not in text
            assert "sk-ant-" not in text

    def test_gitignore_covers_secrets_and_local_stores(self):
        text = (ROOT / ".gitignore").read_text()
        for entry in (".env", "chroma_db/", "enterprise_bi.duckdb"):
            assert entry in text

    def test_env_example_has_no_real_key(self):
        text = (ROOT / ".env.example").read_text()
        assert "OPENAI_API_KEY=" in text
        assert "sk-" not in text
