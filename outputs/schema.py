"""
Strict output contracts.

The UI never parses natural language. Anything rendered as a recommendation has
passed Pydantic validation first; malformed agent output is rejected, not
coerced (TR-010, Build Step 4 section 4).

Field names are snake_case in Python (they must be valid identifiers) but
serialise to the PRD's canonical key set - with spaces - via aliases, so
`model_dump(by_alias=True)` matches PRD section 6 and AC-10 literally.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class SchemaViolation(RuntimeError):
    """Raised when agent output cannot be validated into the action contract."""


class ActionRecommendation(BaseModel):
    """The seven-field action contract. Exactly these keys, always."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    driver: str = Field(..., min_length=3)
    controllable_lever: str = Field(..., alias="controllable lever", min_length=3)
    action: str = Field(..., min_length=3)
    expected_impact: str = Field(..., alias="expected impact", min_length=3)
    owner: str = Field(..., min_length=2)
    confidence: int = Field(..., ge=0, le=100)
    monitoring_plan: str = Field(..., alias="monitoring plan", min_length=3)

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v: Any) -> Any:
        """Accept 88, '88', '88%' - reject anything that is not a number."""
        if isinstance(v, str):
            m = re.search(r"-?\d+", v)
            if not m:
                raise ValueError(f"confidence is not numeric: {v!r}")
            return int(m.group())
        if isinstance(v, float):
            return int(round(v))
        return v

    def canonical(self) -> dict[str, Any]:
        """Serialise with the PRD's exact key set (spaces, not underscores)."""
        return self.model_dump(by_alias=True)


class ArbitrationResult(BaseModel):
    """
    The Arbitrator's verdict.

    `distribution` is the confidence split across the two competing hypotheses.
    Round 1 promised a confidence distribution rather than a bare scalar; this
    is that promise, and it is also what makes a 50/50 abstain self-evident.
    """

    model_config = ConfigDict(extra="forbid")

    winning_hypothesis: str = Field(..., pattern="^(internal|external|none)$")
    confidence: int = Field(..., ge=0, le=100)
    internal_confidence: int = Field(..., ge=0, le=100)
    external_confidence: int = Field(..., ge=0, le=100)
    rationale: str = ""

    @property
    def is_split(self) -> bool:
        return abs(self.internal_confidence - self.external_confidence) <= 5


def extract_json_block(text: str) -> dict[str, Any]:
    """
    Pull the first JSON object out of an agent reply.

    Handles fenced blocks and bare objects. Raises rather than guessing.
    """
    if not text or not text.strip():
        raise SchemaViolation("agent returned empty output")

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None

    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise SchemaViolation("no JSON object found in agent output")
        candidate = text[start:end + 1]

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise SchemaViolation(f"agent output is not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise SchemaViolation("agent output JSON is not an object")
    return parsed


def validate_action(payload: dict[str, Any] | str) -> ActionRecommendation:
    """
    Hard gate. Returns a validated model or raises SchemaViolation.

    There is no path that returns a partially-valid recommendation.
    """
    if isinstance(payload, str):
        payload = extract_json_block(payload)

    # Accept either key style on the way in; always emit canonical on the way out.
    normalised = {k.replace("_", " ") if k in {
        "controllable_lever", "expected_impact", "monitoring_plan"
    } else k: v for k, v in payload.items()}

    try:
        return ActionRecommendation.model_validate(normalised)
    except ValidationError as exc:
        raise SchemaViolation(
            "Storyteller output failed the action contract: "
            + "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()
            )
        ) from exc


CANONICAL_KEYS = [
    "driver",
    "controllable lever",
    "action",
    "expected impact",
    "owner",
    "confidence",
    "monitoring plan",
]
