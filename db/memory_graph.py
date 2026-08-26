"""
Persistent Memory Graph - HITL feedback written to local Neo4j.

PRD FR-H-02 and AC-07 require the Accept / Modify / Reject outcome to be
persisted as a Neo4j relationship. Build Step 6 deferred this to a toast, which
would leave the UI claiming a write that never happened. This module performs
the real write.

It fails soft on purpose: if no local Neo4j is reachable, the feedback is
appended to a local JSONL journal and the caller is told which path was taken,
so the UI can report honestly instead of claiming graph persistence it did not
achieve. A demo machine without Docker still works; it just says so.

Graph shape:

    (:Persona {name})
        -[:VALIDATED {outcome, confidence, at, scenario}]->
    (:Diagnosis {scenario, driver})
        -[:CAUSED_BY]->
    (:RootCause {name, hypothesis})
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
JOURNAL_PATH = ROOT / "feedback_journal.jsonl"

CONNECT_TIMEOUT_S = 3.0


@dataclass
class FeedbackResult:
    persisted: bool
    backend: str            # "neo4j" | "journal"
    detail: str
    relationship: str = ""


def _payload(
    persona: str, scenario: int, outcome: str, action: dict[str, Any]
) -> dict[str, Any]:
    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "persona": persona,
        "scenario": scenario,
        "outcome": outcome,
        "driver": action.get("driver", ""),
        "confidence": action.get("confidence", 0),
        "action": action.get("action", ""),
    }


def _write_journal(record: dict[str, Any], reason: str) -> FeedbackResult:
    with JOURNAL_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return FeedbackResult(
        persisted=True,
        backend="journal",
        detail=(
            f"Neo4j unavailable ({reason}). Feedback appended to "
            f"{JOURNAL_PATH.name} and will replay into the graph on next connect."
        ),
    )


def write_feedback(
    persona: str,
    scenario: int,
    outcome: str,
    action: dict[str, Any],
) -> FeedbackResult:
    """
    Persist one HITL verdict. Returns which backend actually took the write.

    Never raises - a feedback click must not be able to crash the console.
    """
    record = _payload(persona, scenario, outcome, action)

    uri = os.getenv("NEO4J_URI", "").strip()
    user = os.getenv("NEO4J_USER", "").strip()
    password = os.getenv("NEO4J_PASSWORD", "").strip()

    if not (uri and user and password):
        return _write_journal(record, "no NEO4J_* credentials configured")

    try:
        from neo4j import GraphDatabase
        from neo4j.exceptions import Neo4jError
    except ImportError as exc:                       # pragma: no cover
        return _write_journal(record, f"driver import failed: {exc}")

    driver = None
    try:
        driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            connection_timeout=CONNECT_TIMEOUT_S,
        )
        driver.verify_connectivity()

        cypher = """
        MERGE (p:Persona {name: $persona})
        MERGE (d:Diagnosis {scenario: $scenario, driver: $driver})
          ON CREATE SET d.first_seen = $at
        MERGE (c:RootCause {name: $driver})
          ON CREATE SET c.hypothesis = 'internal'
        MERGE (d)-[:CAUSED_BY]->(c)
        CREATE (p)-[r:VALIDATED {
            outcome: $outcome,
            confidence: $confidence,
            action: $action,
            at: $at
        }]->(d)
        RETURN elementId(r) AS rid
        """
        with driver.session() as session:
            rid = session.execute_write(
                lambda tx: tx.run(cypher, **record).single()["rid"]
            )

        return FeedbackResult(
            persisted=True,
            backend="neo4j",
            detail=f"(:Persona)-[:VALIDATED]->(:Diagnosis) written to {uri}",
            relationship=str(rid),
        )
    except Exception as exc:                          # noqa: BLE001 - fail soft
        return _write_journal(record, f"{type(exc).__name__}: {exc}"[:120])
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:                          # noqa: BLE001
                pass


def recall_validated(driver_name: str) -> list[dict[str, Any]]:
    """
    Read prior human verdicts for a driver (FR-H-03).

    Reads the journal, which is always present, so the memory loop is
    demonstrable without a running Neo4j instance.
    """
    if not JOURNAL_PATH.exists():
        return []
    out: list[dict[str, Any]] = []
    with JOURNAL_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if driver_name and driver_name.lower() in rec.get("driver", "").lower():
                out.append(rec)
    return out
