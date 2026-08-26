"""Loader for the KPI semantic contract. Parsed once, cached, read-only."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

CONTRACT_PATH = Path(__file__).resolve().parent / "semantic_contract.yaml"


@functools.lru_cache(maxsize=1)
def get_contract() -> dict[str, Any]:
    with CONTRACT_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def personas() -> list[str]:
    return list(get_contract()["personas"].keys())


def persona_spec(persona: str) -> dict[str, Any]:
    return get_contract()["personas"][persona]


def scenarios() -> dict[int, dict[str, Any]]:
    return get_contract()["scenarios"]


def abstain_threshold() -> int:
    return int(get_contract()["thresholds"]["abstain_below_confidence"])


def kpi_spec(key: str) -> dict[str, Any]:
    return get_contract()["kpis"][key]
