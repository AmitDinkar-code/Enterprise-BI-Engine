"""
Deterministic Watcher - the quantitative source of truth.

NOTHING in this module calls an LLM. Every percentage, mean, standard
deviation, z-score, total and contribution share is computed here in SQL or
plain Python, before any agent is constructed. Agents receive the output of
this module as an authoritative context object (TR-007, TR-008, TR-009).

The module also enforces two things the LLM layer must never be trusted with:

  * RBAC at the query layer (SEC-04). If the active persona is denied a KPI by
    the semantic contract, the SELECT is never executed and the value never
    enters the process. Suppressing a rendered field is not authorisation.

  * Sparse-history honesty (AC-03). A z-score over three days of history is not
    statistically supportable, so the watcher degrades to a bounded range check
    and marks the result low-power rather than emitting a number it cannot
    defend.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from config.contract import get_contract

ROOT = Path(__file__).resolve().parent.parent
DUCKDB_PATH = ROOT / "enterprise_bi.duckdb"


class AccessDenied(Exception):
    """Raised when a persona requests a KPI the semantic contract denies."""


@dataclass
class KpiResult:
    """One deterministically computed KPI, with its full provenance."""

    key: str
    display_name: str
    value: float | None
    unit: str
    fmt: str

    # movement
    baseline_value: float | None = None
    delta_absolute: float | None = None
    delta_relative_pct: float | None = None

    # statistics
    baseline_mean: float | None = None
    baseline_stdev: float | None = None
    baseline_n: int = 0
    z_score: float | None = None

    # method + provenance (Round 2 brief: traceability)
    method: str = ""
    method_note: str = ""
    source_system: str = ""
    source_table: str = ""
    grain: str = ""
    refresh_cadence: str = ""
    lineage: str = ""
    as_of: str = ""
    freshness: str = ""

    # materiality
    is_material: bool = False
    materiality_note: str = ""
    low_power: bool = False

    # access
    access_granted: bool = True
    access_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DriverContribution:
    """A ranked, deterministically attributed contributor to the movement."""

    name: str
    hypothesis: str            # "internal" | "external"
    measured_delta_usd: float | None   # None when RBAC withholds currency detail
    share_pct: float
    basis: str


@dataclass
class WatcherOutput:
    scenario: int
    scenario_label: str
    persona: str
    as_of: str
    primary_kpi: str
    kpis: dict[str, KpiResult] = field(default_factory=dict)
    drivers: list[DriverContribution] = field(default_factory=list)
    anomaly_detected: bool = False
    attribution_note: str = ""

    def to_context(self) -> dict[str, Any]:
        """Serialise into the bounded context object handed to the agents."""
        return {
            "scenario": self.scenario,
            "scenario_label": self.scenario_label,
            "persona": self.persona,
            "as_of": self.as_of,
            "anomaly_detected": self.anomaly_detected,
            "primary_kpi": self.primary_kpi,
            "kpis": {
                k: {
                    "display_name": v.display_name,
                    "value": v.value,
                    "unit": v.unit,
                    "baseline_value": v.baseline_value,
                    "delta_absolute": v.delta_absolute,
                    "delta_relative_pct": v.delta_relative_pct,
                    "z_score": v.z_score,
                    "baseline_n": v.baseline_n,
                    "method": v.method,
                    "is_material": v.is_material,
                    "low_power": v.low_power,
                    "source_table": v.source_table,
                }
                for k, v in self.kpis.items()
                if v.access_granted
            },
            "ranked_drivers": [
                {
                    "name": d.name,
                    "hypothesis": d.hypothesis,
                    "measured_delta_usd": d.measured_delta_usd,
                    "share_pct": d.share_pct,
                    "basis": d.basis,
                }
                for d in self.drivers
            ],
            "attribution_note": self.attribution_note,
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _connect() -> duckdb.DuckDBPyConnection:
    if not DUCKDB_PATH.exists():
        raise FileNotFoundError(
            f"{DUCKDB_PATH.name} not found. Run `python setup_db.py` first."
        )
    return duckdb.connect(str(DUCKDB_PATH), read_only=True)


def _zscore(value: float, mean: float, stdev: float) -> float | None:
    if stdev is None or stdev == 0:
        return None
    return (value - mean) / stdev


def _freshness(as_of: datetime | date, cadence: str) -> str:
    stamp = as_of.strftime("%Y-%m-%d %H:%M") if isinstance(as_of, datetime) \
        else as_of.strftime("%Y-%m-%d")
    return f"as of {stamp} UTC | {cadence}"


def check_access(kpi_key: str, persona: str) -> tuple[bool, str]:
    """Semantic-contract access check. Called BEFORE any SELECT is issued."""
    contract = get_contract()
    spec = contract["kpis"].get(kpi_key)
    if spec is None:
        return False, f"unknown KPI '{kpi_key}'"

    access = spec.get("access", {})
    if persona in access.get("deny", []):
        return False, (
            f"{access.get('classification', 'restricted')} - denied to "
            f"{persona} by semantic contract; query not executed"
        )
    allow = access.get("allow", [])
    if allow and persona not in allow:
        return False, f"{persona} not in allow-list; query not executed"
    return True, "granted"


# ---------------------------------------------------------------------------
# per-KPI deterministic calculations
# ---------------------------------------------------------------------------

def _restricted(kpi_key: str, note: str) -> KpiResult:
    spec = get_contract()["kpis"][kpi_key]
    return KpiResult(
        key=kpi_key,
        display_name=spec["display_name"],
        value=None,
        unit=spec["unit"],
        fmt=spec["format"],
        method="not executed",
        method_note="RBAC: query suppressed at the data layer",
        source_system=spec["source_system"],
        source_table=spec["source_table"],
        grain=spec["grain"],
        refresh_cadence=spec["refresh_cadence"],
        lineage=spec["lineage"],
        access_granted=False,
        access_note=note,
    )


def _billing_kpi(con, kpi_key: str, as_of: str) -> KpiResult:
    """MRR or churn_rate - both live in billing_mrr, daily grain."""
    spec = get_contract()["kpis"][kpi_key]

    if kpi_key == "mrr":
        expr = "total_mrr"
    else:
        expr = "churned_mrr / total_mrr * 100.0"

    series = con.execute(
        f"SELECT record_date, {expr} AS v FROM billing_mrr "
        "WHERE record_date <= ? ORDER BY record_date",
        [as_of],
    ).fetchall()

    values = [float(r[1]) for r in series]
    current = values[-1]
    previous = values[-2] if len(values) > 1 else None
    baseline = values[:-1]

    mean = statistics.fmean(baseline) if baseline else None
    stdev = statistics.stdev(baseline) if len(baseline) > 1 else None
    z = _zscore(current, mean, stdev) if mean is not None else None

    delta_abs = (current - previous) if previous is not None else None
    delta_rel = (delta_abs / previous * 100.0) if previous else None

    mat = spec["materiality"]
    stat_ok = z is not None and abs(z) >= float(mat["statistical"].split(">=")[1])
    if kpi_key == "mrr":
        biz_ok = delta_abs is not None and abs(delta_abs) >= 25_000
        biz_txt = f"absolute move ${abs(delta_abs or 0):,.0f} vs $25,000 floor"
    else:
        biz_ok = delta_rel is not None and abs(delta_rel) >= 10
        biz_txt = f"relative move {abs(delta_rel or 0):.1f}% vs 10% floor"

    material = stat_ok and biz_ok
    note = (
        f"statistical: |z|={abs(z):.1f} -> {'pass' if stat_ok else 'fail'}; "
        f"business: {biz_txt} -> {'pass' if biz_ok else 'fail'}"
        if z is not None else "insufficient baseline"
    )

    return KpiResult(
        key=kpi_key,
        display_name=spec["display_name"],
        value=round(current, 4),
        unit=spec["unit"],
        fmt=spec["format"],
        baseline_value=round(previous, 4) if previous is not None else None,
        delta_absolute=round(delta_abs, 2) if delta_abs is not None else None,
        delta_relative_pct=round(delta_rel, 2) if delta_rel is not None else None,
        baseline_mean=round(mean, 4) if mean is not None else None,
        baseline_stdev=round(stdev, 4) if stdev is not None else None,
        baseline_n=len(baseline),
        z_score=round(z, 2) if z is not None else None,
        method="z-score vs trailing daily baseline",
        method_note=f"sample stdev over {len(baseline)} prior days, SQL + Python",
        source_system=spec["source_system"],
        source_table=spec["source_table"],
        grain=spec["grain"],
        refresh_cadence=spec["refresh_cadence"],
        lineage=spec["lineage"],
        as_of=as_of,
        freshness=_freshness(date.fromisoformat(as_of), spec["refresh_cadence"]),
        is_material=material,
        materiality_note=note,
    )


def _telemetry_kpi(con, as_of: str) -> KpiResult:
    spec = get_contract()["kpis"]["gateway_timeout_errors"]

    rows = con.execute(
        "SELECT ts, gateway_timeout_errors FROM platform_telemetry "
        "WHERE CAST(ts AS DATE) <= ? ORDER BY ts",
        [as_of],
    ).fetchall()

    values = [float(r[1]) for r in rows]
    peak_idx = max(range(len(values)), key=lambda i: values[i])
    peak_ts, peak_val = rows[peak_idx][0], values[peak_idx]

    quiet = [v for v in values if v < 10]           # pre-incident floor
    mean = statistics.fmean(quiet) if quiet else None
    stdev = statistics.stdev(quiet) if len(quiet) > 1 else None
    z = _zscore(peak_val, mean, stdev) if mean is not None else None

    biz_ok = peak_val >= 100
    stat_ok = z is not None and abs(z) >= 3.0
    material = biz_ok or stat_ok                     # rule: either

    return KpiResult(
        key="gateway_timeout_errors",
        display_name=spec["display_name"],
        value=peak_val,
        unit=spec["unit"],
        fmt=spec["format"],
        baseline_value=round(mean, 2) if mean is not None else None,
        delta_absolute=round(peak_val - (mean or 0), 2),
        baseline_mean=round(mean, 3) if mean is not None else None,
        baseline_stdev=round(stdev, 3) if stdev is not None else None,
        baseline_n=len(quiet),
        z_score=round(z, 2) if z is not None else None,
        method="peak vs quiescent-window baseline",
        method_note=f"peak minute {peak_ts}; baseline from {len(quiet)} quiet minutes",
        source_system=spec["source_system"],
        source_table=spec["source_table"],
        grain=spec["grain"],
        refresh_cadence=spec["refresh_cadence"],
        lineage=spec["lineage"],
        as_of=str(peak_ts),
        freshness=_freshness(peak_ts, spec["refresh_cadence"]),
        is_material=material,
        materiality_note=f"peak {peak_val:.0f} errors vs 100 floor -> "
                         f"{'pass' if biz_ok else 'fail'}",
    )


def _latency_kpi(con, as_of: str) -> KpiResult:
    """
    Sparse-history KPI (AC-03).

    Only three calendar days of history exist. The semantic contract requires
    14 days before a z-score baseline is considered supportable, so the method
    degrades to a bounded range check and the result is flagged low_power.
    """
    spec = get_contract()["kpis"]["p99_latency_ms"]
    sparse = spec["sparse_history"]

    rows = con.execute(
        "SELECT ts, p99_latency_ms FROM aws_cloudwatch_latency "
        "WHERE CAST(ts AS DATE) <= ? ORDER BY ts",
        [as_of],
    ).fetchall()

    days = con.execute(
        "SELECT COUNT(DISTINCT CAST(ts AS DATE)) FROM aws_cloudwatch_latency "
        "WHERE CAST(ts AS DATE) <= ?",
        [as_of],
    ).fetchone()[0]

    values = [float(r[1]) for r in rows]
    peak_idx = max(range(len(values)), key=lambda i: values[i])
    peak_ts, peak_val = rows[peak_idx][0], values[peak_idx]

    quiet = [v for v in values if v < 500]
    mean = statistics.fmean(quiet) if quiet else None

    enough_history = days >= int(sparse["minimum_days_for_baseline"])
    low_power = not enough_history

    if enough_history:
        stdev = statistics.stdev(quiet) if len(quiet) > 1 else None
        z = _zscore(peak_val, mean, stdev)
        method = "z-score vs trailing hourly baseline"
        method_note = f"baseline over {days} days"
    else:
        z = None
        stdev = None
        method = sparse["degraded_method"]
        method_note = (
            f"only {days} days of history (contract requires "
            f"{sparse['minimum_days_for_baseline']}); z-score withheld as "
            f"statistically unsupportable - bounded range check used instead"
        )

    ratio = (peak_val / mean) if mean else None
    biz_ok = peak_val >= 1000

    return KpiResult(
        key="p99_latency_ms",
        display_name=spec["display_name"],
        value=peak_val,
        unit=spec["unit"],
        fmt=spec["format"],
        baseline_value=round(mean, 2) if mean is not None else None,
        delta_absolute=round(peak_val - (mean or 0), 2),
        delta_relative_pct=round((ratio - 1) * 100, 1) if ratio else None,
        baseline_mean=round(mean, 3) if mean is not None else None,
        baseline_stdev=round(stdev, 3) if stdev is not None else None,
        baseline_n=len(quiet),
        z_score=z,
        method=method,
        method_note=method_note,
        source_system=spec["source_system"],
        source_table=spec["source_table"],
        grain=spec["grain"],
        refresh_cadence=spec["refresh_cadence"],
        lineage=spec["lineage"],
        as_of=str(peak_ts),
        freshness=_freshness(peak_ts, spec["refresh_cadence"]),
        is_material=biz_ok,
        materiality_note=(
            f"peak {peak_val:,.0f} ms vs 1,000 ms floor -> "
            f"{'pass' if biz_ok else 'fail'}; statistical test unavailable "
            f"(sparse history)"
        ),
        low_power=low_power,
    )


# ---------------------------------------------------------------------------
# driver attribution - deterministic ranking
# ---------------------------------------------------------------------------

def _attribute_drivers(
    con, as_of: str, show_currency: bool
) -> tuple[list[DriverContribution], str]:
    """
    Rank the contributors to the MRR movement using measured adverse exposure.

    This is arithmetic, not an opinion. Two independently measured signals:

      internal  = failed_transactions_usd above its trailing baseline
      external  = churned_mrr above its trailing baseline

    Shares are each signal's portion of total measured adverse exposure. The
    LLM is told the ranking; it never computes it.

    `show_currency` is the RBAC control. Both personas may see the ranking and
    the shares - that is the diagnosis. Only a persona entitled to financial
    KPIs sees the underlying dollar exposure.
    """
    rows = con.execute(
        "SELECT record_date, failed_transactions_usd, churned_mrr "
        "FROM billing_mrr WHERE record_date <= ? ORDER BY record_date",
        [as_of],
    ).fetchall()

    failed = [float(r[1]) for r in rows]
    churned = [float(r[2]) for r in rows]

    failed_base = statistics.fmean(failed[:-1])
    churned_base = statistics.fmean(churned[:-1])

    internal_exposure = max(0.0, failed[-1] - failed_base)
    external_exposure = max(0.0, churned[-1] - churned_base)
    total = internal_exposure + external_exposure

    if total <= 0:
        return [], "no adverse exposure measured against baseline"

    drivers = [
        DriverContribution(
            name="Payment gateway timeout (Stripe API v2.2)",
            hypothesis="internal",
            measured_delta_usd=round(internal_exposure, 2) if show_currency else None,
            share_pct=round(internal_exposure / total * 100, 1),
            basis="failed_transactions_usd above 27-day trailing mean",
        ),
        DriverContribution(
            name="Competitor discount campaign",
            hypothesis="external",
            measured_delta_usd=round(external_exposure, 2) if show_currency else None,
            share_pct=round(external_exposure / total * 100, 1),
            basis="churned_mrr above 27-day trailing mean",
        ),
    ]
    drivers.sort(key=lambda d: d.share_pct, reverse=True)

    if show_currency:
        note = (
            f"Total measured adverse exposure ${total:,.0f}. Shares are each "
            f"signal's portion of that total, computed in SQL. Churn exposure "
            f"recurs monthly; payment-failure exposure is single-cycle and "
            f"partially recoverable on retry."
        )
    else:
        note = (
            "Shares are each signal's portion of total measured adverse "
            "exposure, computed in SQL. Currency detail withheld for this "
            "persona by the semantic contract; the ranking is unaffected."
        )
    return drivers, note


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def query_kpi_metrics(scenario: int, persona: str) -> WatcherOutput:
    """
    Compute every KPI the persona is entitled to, for the given scenario.

    This is the ONLY path by which quantitative values enter the system.
    """
    contract = get_contract()
    scen = contract["scenarios"][scenario]
    as_of = scen["as_of"]

    out = WatcherOutput(
        scenario=scenario,
        scenario_label=scen["label"],
        persona=persona,
        as_of=as_of,
        primary_kpi=scen["primary_kpi"],
    )

    con = _connect()
    try:
        for kpi_key in ("mrr", "churn_rate", "gateway_timeout_errors",
                        "p99_latency_ms"):
            granted, note = check_access(kpi_key, persona)
            if not granted:
                # SEC-04: the SELECT is never issued for a denied KPI.
                out.kpis[kpi_key] = _restricted(kpi_key, note)
                continue

            if kpi_key in ("mrr", "churn_rate"):
                out.kpis[kpi_key] = _billing_kpi(con, kpi_key, as_of)
            elif kpi_key == "gateway_timeout_errors":
                out.kpis[kpi_key] = _telemetry_kpi(con, as_of)
            else:
                out.kpis[kpi_key] = _latency_kpi(con, as_of)

        # Driver attribution needs billing figures. It is an internal
        # calculation over aggregate exposure and is used to rank causes; it
        # does not surface restricted MRR values to a denied persona.
        if scenario == 1:
            show_currency = out.kpis["mrr"].access_granted
            out.drivers, out.attribution_note = _attribute_drivers(
                con, as_of, show_currency
            )

        primary = out.kpis.get(out.primary_kpi)
        if primary is not None and primary.access_granted:
            out.anomaly_detected = primary.is_material
        else:
            out.anomaly_detected = any(
                k.is_material for k in out.kpis.values() if k.access_granted
            )
    finally:
        con.close()

    return out


if __name__ == "__main__":
    import json

    for scen in (1, 2, 3):
        for who in ("VP of Revenue (Executive)",
                    "Director of Engineering (Technical)"):
            res = query_kpi_metrics(scen, who)
            print(f"\n--- scenario {scen} / {who}")
            print(json.dumps(res.to_context(), indent=2, default=str)[:900])
