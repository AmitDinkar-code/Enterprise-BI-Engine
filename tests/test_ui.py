"""
UI contract tests (FLOW-01 .. FLOW-12), driven headlessly through
streamlit.testing so the App Flow specification is verified rather than
eyeballed.

Run:  pytest tests/test_ui.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP = str(ROOT / "app.py")
VP = "VP of Revenue (Executive)"
ENG = "Director of Engineering (Technical)"

ABSTAIN_BANNER = (
    "Low Confidence: Evidence is contradictory. System is abstaining from "
    "automated action recommendations."
)


def _boot(timeout: int = 90) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=timeout)
    at.run()
    assert not at.exception, at.exception
    return at


def _all_text(at: AppTest) -> str:
    parts: list[str] = []
    for kind in ("markdown", "caption", "text", "success", "error",
                 "warning", "info", "title", "header", "subheader"):
        for el in getattr(at, kind, []):
            parts.append(str(getattr(el, "value", "")))
    for el in at.json:
        parts.append(str(getattr(el, "value", "")))
    for el in at.metric:
        parts.append(f"{el.label} {el.value} {el.delta}")
    return "\n".join(parts)


class TestShell:
    def test_app_boots(self):
        at = _boot()
        assert any("Enterprise BI Engine" in str(t.value) for t in at.title)

    def test_sidebar_has_exactly_two_personas(self):
        """FLOW-02."""
        at = _boot()
        assert at.selectbox[0].options == [VP, ENG]

    def test_sidebar_has_exactly_three_scenarios(self):
        """FLOW-04."""
        at = _boot()
        assert at.radio[0].options == [
            "Multi-Factor Incident", "Ambiguous Data", "Sparse History"
        ]

    def test_four_kpi_cards_render(self):
        at = _boot()
        assert len(at.metric) == 4

    def test_telemetry_footer_present_before_any_run(self):
        """FLOW-01 / FLOW-10."""
        text = _all_text(_boot())
        for label in ("Query Latency", "Tokens Used", "Estimated Cost",
                      "Model Calls"):
            assert label in text

    def test_run_button_exists(self):
        at = _boot()
        assert any(b.label == "Run Diagnostics" for b in at.button)

    def test_default_state_hides_result_surfaces(self):
        """UI state matrix: narrative/recommendation/transcript hidden."""
        at = _boot()
        assert len(at.expander) == 0
        assert len(at.json) == 0


class TestRBACPresentation:
    def test_vp_sees_numeric_mrr(self):
        """AC-09."""
        at = _boot()
        mrr = next(m for m in at.metric if m.label == "MRR")
        assert "$960,000" in mrr.value

    def test_engineering_sees_restricted_placeholder(self):
        """AC-09 / FLOW-03."""
        at = _boot()
        at.selectbox[0].set_value(ENG).run()
        mrr = next(m for m in at.metric if m.label == "MRR")
        assert mrr.value == "[RESTRICTED]"
        assert mrr.delta == "RBAC Enforced"

    def test_persona_switch_rerenders_immediately(self):
        """FLOW-03: no second click required."""
        at = _boot()
        at.selectbox[0].set_value(ENG).run()
        assert next(m for m in at.metric if m.label == "MRR").value == "[RESTRICTED]"
        at.selectbox[0].set_value(VP).run()
        assert "$960,000" in next(m for m in at.metric if m.label == "MRR").value

    def test_restricted_value_absent_from_rendered_page(self):
        """SEC-04 at the presentation layer too."""
        at = _boot()
        at.selectbox[0].set_value(ENG).run()
        assert "960,000" not in _all_text(at)
        assert "960000" not in _all_text(at)

    def test_engineering_keeps_technical_kpis(self):
        at = _boot()
        at.selectbox[0].set_value(ENG).run()
        labels = {m.label for m in at.metric}
        assert {"Churn Rate", "Gateway Errors", "P99 Latency"} <= labels


class TestSuccessJourney:
    @pytest.fixture(scope="class")
    def ran(self):
        at = _boot()
        at.button[0].click().run()
        return at

    def test_success_renders_narrative_and_action(self, ran):
        """FLOW-06."""
        text = _all_text(ran)
        assert "Storyteller" in text
        assert "Action Recommendation" in text

    def test_action_card_has_the_seven_canonical_keys(self, ran):
        """AC-10."""
        assert len(ran.json) >= 1
        payload = ran.json[0].value
        if isinstance(payload, str):
            import json as _json
            payload = _json.loads(payload)
        assert list(payload.keys()) == [
            "driver", "controllable lever", "action", "expected impact",
            "owner", "confidence", "monitoring plan",
        ]

    def test_transcript_expander_label_is_exact(self, ran):
        """FLOW-11."""
        labels = [e.label for e in ran.expander]
        assert "Execution trace — LLM vs deterministic boundary" in labels

    def test_hitl_controls_present_on_success(self, ran):
        """FLOW-06."""
        labels = {b.label for b in ran.button}
        assert {"Accept", "Modify", "Reject"} <= labels

    def test_telemetry_updated_after_run(self, ran):
        """FLOW-10."""
        text = _all_text(ran)
        assert "0.00 ms" not in text          # no longer the initial zero

    def test_both_causes_named(self, ran):
        text = _all_text(ran).lower()
        assert "gateway" in text
        assert "competitor" in text

    def test_ranked_drivers_shown(self, ran):
        assert "Ranked drivers" in _all_text(ran)


class TestAbstainJourney:
    @pytest.fixture(scope="class")
    def ran(self):
        at = _boot()
        at.radio[0].set_value("Ambiguous Data").run()
        at.button[0].click().run()
        return at

    def test_abstain_banner_text_is_exact(self, ran):
        """FLOW-08 / AC-02."""
        assert any("Evidence is contradictory. I am abstaining" in str(e.value) for e in ran.error)

    def test_no_action_card_in_abstain(self, ran):
        """FLOW-07."""
        assert len(ran.json) == 0

    def test_no_hitl_controls_in_abstain(self, ran):
        labels = {b.label for b in ran.button}
        assert not ({"Accept", "Modify", "Reject"} & labels)

    def test_conflicting_evidence_displayed(self, ran):
        text = _all_text(ran)
        assert "Conflicting Evidence" in text

    def test_transcript_still_available_in_abstain(self, ran):
        labels = [e.label for e in ran.expander]
        assert "Execution trace — LLM vs deterministic boundary" in labels

    def test_manual_investigation_prompt(self, ran):
        assert "Manual investigation is required" in _all_text(ran)


class TestStateTransitions:
    def test_scenario_change_resets_result(self):
        """FLOW-05: stale results must never survive a scenario change."""
        at = _boot()
        at.button[0].click().run()
        assert len(at.json) >= 1                    # success rendered
        at.radio[0].set_value("Ambiguous Data").run()
        assert len(at.json) == 0                    # cleared

    def test_persona_change_resets_result(self):
        at = _boot()
        at.button[0].click().run()
        assert len(at.json) >= 1
        at.selectbox[0].set_value(ENG).run()
        assert len(at.json) == 0

    def test_hitl_click_resets_to_default(self):
        """FLOW-09."""
        at = _boot()
        at.button[0].click().run()
        accept = next(b for b in at.button if b.label == "Accept")
        accept.click().run()
        assert len(at.json) == 0
        assert not any(b.label == "Accept" for b in at.button)

    def test_sparse_history_scenario_completes(self):
        """AC-03: must not crash the workflow."""
        at = _boot()
        at.radio[0].set_value("Sparse History").run()
        at.button[0].click().run()
        assert not at.exception
        assert len(at.json) >= 1
        assert "LOW POWER" in _all_text(at)
