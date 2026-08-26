"""
Runtime telemetry.

The Round 2 brief asks for model calls, latency, token usage and estimated
cost. PRD section 7 and TR-012 name three of those four; the model-call counter
is included here because the brief scores it.

Cost is derived from measured tokens and the rates configured in .env. If no
rates are configured the estimate is 0 rather than an invented provider price -
but .env.example ships real gpt-4o-mini rates so the footer is never a
meaningless zero at demo time.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class Telemetry:
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model_calls: int = 0
    estimated_cost_usd: float = 0.0
    mode: str = "live"          # "live" | "replay"
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Meter:
    """Wall-clock and token accounting for one diagnostic run."""

    def __init__(self, model: str = "", mode: str = "live") -> None:
        self._t0 = time.perf_counter()
        self.telemetry = Telemetry(mode=mode, model=model)

    def record_call(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        self.telemetry.model_calls += 1
        self.telemetry.prompt_tokens += int(prompt_tokens or 0)
        self.telemetry.completion_tokens += int(completion_tokens or 0)
        self.telemetry.total_tokens = (
            self.telemetry.prompt_tokens + self.telemetry.completion_tokens
        )

    def stop(self) -> Telemetry:
        self.telemetry.latency_ms = round(
            (time.perf_counter() - self._t0) * 1000.0, 2
        )
        self.telemetry.estimated_cost_usd = estimate_cost(
            self.telemetry.prompt_tokens, self.telemetry.completion_tokens
        )
        return self.telemetry


def _rate(name: str) -> float:
    try:
        return float(os.getenv(name, "") or 0.0)
    except ValueError:
        return 0.0


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """USD estimate from configured per-million-token rates."""
    inp = _rate("COST_PER_MTOK_INPUT")
    out = _rate("COST_PER_MTOK_OUTPUT")
    cost = (prompt_tokens / 1_000_000.0) * inp + (completion_tokens / 1_000_000.0) * out
    return round(cost, 6)


def blank() -> dict[str, Any]:
    return Telemetry().to_dict()
