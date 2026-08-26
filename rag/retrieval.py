"""
Researcher / RAG layer - local ChromaDB retrieval of unstructured evidence.

Retrieval is scoped to the anomaly's timestamp window so evidence is linked to
the incident rather than merely topical (FR-R-01). No live Slack or Zendesk API
is contacted; the corpus is the local seeded store only (FR-R-02, TR-013).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from db.embeddings import LocalDeterministicEmbedding

ROOT = Path(__file__).resolve().parent.parent
CHROMA_PATH = ROOT / "chroma_db"

COLLECTIONS = ("zendesk_tickets", "slack_engineering")

# Query text per scenario. These are retrieval probes, not prompts - they never
# reach an LLM.
SCENARIO_QUERIES: dict[int, list[str]] = {
    1: [
        "payment gateway timeout failed recurring charges stripe api",
        "competitor discount campaign cancellation pricing pressure",
    ],
    2: [
        "payment gateway timeout failed recurring charges stripe api",
        "competitor discount campaign cancellation pricing pressure",
    ],
    3: [
        "p99 latency migration payment gateway service slow connection pool",
    ],
}

WINDOW_HOURS = 6


@dataclass
class EvidenceItem:
    id: str
    text: str
    source: str
    hypothesis: str
    timestamp: str
    collection: str
    distance: float
    in_window: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except Exception:
        return None


def retrieve_unstructured_context(
    scenario: int,
    anomaly_timestamp: str,
    window_hours: int = WINDOW_HOURS,
    per_query: int = 2,
) -> list[EvidenceItem]:
    """
    Retrieve evidence relevant to the anomaly, restricted to its time window.

    Returns an empty list rather than inventing evidence when nothing in the
    corpus falls inside the window - the honest sparse case.
    """
    import chromadb

    if not CHROMA_PATH.exists():
        raise FileNotFoundError(
            "chroma_db/ not found. Run `python setup_db.py` first."
        )

    anchor = _parse_ts(anomaly_timestamp) or datetime(2026, 8, 25, 10, 0)
    lo = anchor - timedelta(hours=window_hours)
    hi = anchor + timedelta(hours=window_hours)

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    embed = LocalDeterministicEmbedding()

    found: dict[str, EvidenceItem] = {}

    for name in COLLECTIONS:
        col = client.get_collection(name=name, embedding_function=embed)
        for query in SCENARIO_QUERIES.get(scenario, SCENARIO_QUERIES[1]):
            res = col.query(query_texts=[query], n_results=per_query)
            ids = res["ids"][0]
            docs = res["documents"][0]
            metas = res["metadatas"][0]
            dists = res.get("distances", [[0.0] * len(ids)])[0]

            for doc_id, text, meta, dist in zip(ids, docs, metas, dists):
                ts = _parse_ts(str(meta.get("timestamp", "")))
                in_window = bool(ts and lo <= ts <= hi)
                if doc_id in found:
                    continue
                found[doc_id] = EvidenceItem(
                    id=doc_id,
                    text=text,
                    source=str(meta.get("source", name)),
                    hypothesis=str(meta.get("hypothesis", "unknown")),
                    timestamp=str(meta.get("timestamp", "")),
                    collection=name,
                    distance=round(float(dist), 4),
                    in_window=in_window,
                )

    items = [e for e in found.values() if e.in_window]
    items.sort(key=lambda e: e.distance)
    return items


def evidence_balance(items: list[EvidenceItem]) -> dict[str, int]:
    """Count evidence supporting each hypothesis. Deterministic, no LLM."""
    tally = {"internal": 0, "external": 0, "unknown": 0}
    for item in items:
        tally[item.hypothesis] = tally.get(item.hypothesis, 0) + 1
    return tally


if __name__ == "__main__":
    for scen in (1, 2, 3):
        ev = retrieve_unstructured_context(scen, "2026-08-25T10:02:00Z")
        print(f"\nscenario {scen}: {len(ev)} items in window "
              f"{evidence_balance(ev)}")
        for e in ev:
            print(f"  [{e.hypothesis:8s}] {e.id} d={e.distance:.3f} "
                  f"{e.text[:64]}...")
