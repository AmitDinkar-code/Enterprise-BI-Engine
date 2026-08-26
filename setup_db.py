"""
Enterprise BI Engine - Build Step 2
Deterministic local synthetic data foundation.

Creates and seeds:
  * enterprise_bi.duckdb   - billing_mrr, platform_telemetry, aws_cloudwatch_latency
  * chroma_db/             - zendesk_tickets, slack_engineering

All data is hardcoded here. No external API, cloud database, Streamlit, or
AutoGen agent is touched by this script. Running it twice produces byte-identical
table contents (tables are dropped and recreated).

Run:  python setup_db.py
"""

from __future__ import annotations

import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from db.embeddings import LocalDeterministicEmbedding

ROOT = Path(__file__).resolve().parent
DUCKDB_PATH = ROOT / "enterprise_bi.duckdb"
CHROMA_PATH = ROOT / "chroma_db"

# --------------------------------------------------------------------------
# Seed constants - the anomaly the whole prototype is built around.
# Build Step 2 contract:
#   2026-08-24 baseline MRR = 1,000,000
#   2026-08-25 MRR          =   960,000  (exactly -4.0%)
#   failed transactions     =    45,000
#   gateway timeout spike   =       500 errors at 10:02
#   aws latency history     = exactly 3 calendar days (08-23 .. 08-25)
# --------------------------------------------------------------------------
BASELINE_DATE = date(2026, 8, 24)
ANOMALY_DATE = date(2026, 8, 25)

BASELINE_MRR = 1_000_000.00
ANOMALY_MRR = 960_000.00                 # -4.00%
BASELINE_CHURN_RATE = 5.00               # percent of MRR
ANOMALY_CHURN_RATE = 5.60                # +12.0% relative  (PRD scenario 1)
BASELINE_FAILED_TXN = 450.00
ANOMALY_FAILED_TXN = 45_000.00

HISTORY_DAYS = 28                        # baseline window for the z-score


def _billing_rows() -> list[tuple]:
    """
    28 days of daily billing history ending on the anomaly date.

    The baseline is deliberately stable (a small deterministic ripple, no RNG)
    so the z-score of the anomaly day is large and reproducible on every run.
    """
    rows: list[tuple] = []
    start = ANOMALY_DATE - timedelta(days=HISTORY_DAYS - 1)

    for i in range(HISTORY_DAYS):
        d = start + timedelta(days=i)

        if d == ANOMALY_DATE:
            total = ANOMALY_MRR
            churn_rate = ANOMALY_CHURN_RATE
            failed = ANOMALY_FAILED_TXN
            new_mrr = 8_000.00
        elif d == BASELINE_DATE:
            total = BASELINE_MRR
            churn_rate = BASELINE_CHURN_RATE
            failed = BASELINE_FAILED_TXN
            new_mrr = 15_000.00
        else:
            # Deterministic ripple: +/- ~0.35% around 1,000,000. No randomness.
            ripple = ((i * 37) % 11 - 5) * 700.0
            total = round(BASELINE_MRR + ripple, 2)
            churn_rate = round(BASELINE_CHURN_RATE + ((i * 13) % 7 - 3) * 0.02, 4)
            failed = round(BASELINE_FAILED_TXN + ((i * 17) % 9 - 4) * 25.0, 2)
            new_mrr = round(15_000.00 + ((i * 23) % 9 - 4) * 400.0, 2)

        churned = round(total * churn_rate / 100.0, 2)
        rows.append((d, round(total, 2), new_mrr, churned, failed))

    return rows


def _telemetry_rows() -> list[tuple]:
    """Minute-grain platform telemetry across the incident window (09:55-10:10)."""
    rows: list[tuple] = []
    base = datetime(2026, 8, 25, 9, 55)

    # minute offset -> gateway timeout errors. Spike peaks at 10:02 with 500.
    profile = {
        0: 2, 1: 1, 2: 3, 3: 2, 4: 2, 5: 4,      # 09:55 - 10:00 normal
        6: 61,                                    # 10:01 onset
        7: 500,                                   # 10:02 PEAK  <- Build Step 2
        8: 476, 9: 392, 10: 265, 11: 154,         # 10:03 - 10:06 decay
        12: 88, 13: 39, 14: 12, 15: 5,            # 10:07 - 10:10 recovery
    }

    for minute, errors in profile.items():
        ts = base + timedelta(minutes=minute)
        active = 4500 + (minute * 17) % 220
        successful = max(0, active - errors - (minute * 7) % 40)
        rows.append((ts, active, errors, successful))

    return rows


def _latency_rows() -> list[tuple]:
    """
    Hourly p99 latency for the newly migrated service.

    Exactly 3 calendar days: 2026-08-23, 2026-08-24, 2026-08-25.
    This is the sparse-history fixture - too little history for a defensible
    z-score baseline, which the watcher must handle without failing.
    """
    rows: list[tuple] = []
    start = datetime(2026, 8, 23, 0, 0)
    service = "payment-gateway-v2"

    for h in range(72):                      # 3 days x 24 hours
        ts = start + timedelta(hours=h)
        if ts.date() == ANOMALY_DATE and ts.hour == 10:
            p99 = 3450.00                    # spike, correlates with the incident
        elif ts.date() == ANOMALY_DATE and ts.hour in (11, 12):
            p99 = round(1180.00 - (ts.hour - 11) * 420.0, 2)
        else:
            p99 = round(118.00 + ((h * 29) % 13 - 6) * 3.5, 2)
        rows.append((ts, service, p99))

    return rows


# --------------------------------------------------------------------------
# Unstructured evidence corpus
#
# Build Step 2 requires two Zendesk tickets (internal gateway timeout +
# competitor discount) and two Slack messages (engineering gateway evidence +
# sales competitor evidence). Those four are seeded verbatim below.
#
# One additional ticket and one additional Slack message cover the sparse-history
# scenario, so Scenario 3 retrieves real evidence instead of being handed
# unrelated payment documents. Documented as a scope note in the README.
# --------------------------------------------------------------------------
ZENDESK_TICKETS = [
    {
        "id": "TKT-8902",
        "text": (
            "Ticket TKT-8902 [severity: high] 2026-08-25T10:15:00Z - "
            "Subject: Credit card keeps declining on upgrade. "
            "Customer reports: I have tried upgrading my workspace three times "
            "this morning and every attempt fails with a payment gateway "
            "timeout error at checkout. The card is valid and works elsewhere. "
            "Billing shows the charge as pending then failed."
        ),
        "meta": {
            "ticket_id": "TKT-8902",
            "severity": "high",
            "timestamp": "2026-08-25T10:15:00Z",
            "hypothesis": "internal",
            "source": "zendesk",
        },
    },
    {
        "id": "TKT-8905",
        "text": (
            "Ticket TKT-8905 [severity: medium] 2026-08-25T11:30:00Z - "
            "Subject: Cancellation request - pricing. "
            "Customer reports: Please cancel our enterprise plan at the end of "
            "the cycle. Competitor X just launched a fifty percent off annual "
            "campaign and our procurement team has already signed with them. "
            "This is a pricing decision, the product has been fine."
        ),
        "meta": {
            "ticket_id": "TKT-8905",
            "severity": "medium",
            "timestamp": "2026-08-25T11:30:00Z",
            "hypothesis": "external",
            "source": "zendesk",
        },
    },
    {
        "id": "TKT-8911",
        "text": (
            "Ticket TKT-8911 [severity: medium] 2026-08-25T10:41:00Z - "
            "Subject: Dashboard extremely slow since Monday. "
            "Customer reports: Page loads that used to take under a second are "
            "now taking thirty seconds or timing out entirely. This started "
            "around the time you migrated the payment service. p99 latency on "
            "our side looks terrible."
        ),
        "meta": {
            "ticket_id": "TKT-8911",
            "severity": "medium",
            "timestamp": "2026-08-25T10:41:00Z",
            "hypothesis": "internal",
            "source": "zendesk",
        },
    },
]

SLACK_MESSAGES = [
    {
        "id": "SLK-1001",
        "text": (
            "Slack #eng-payments 2026-08-25T10:05:00Z @dev_ops_lead - "
            "Seeing massive spikes in Stripe API v2.2 timeouts, gateway timeout "
            "errors just went vertical at 10:02. The new rollback constraint we "
            "shipped last night is holding connections open until they expire. "
            "Recurring charges are failing. Recommend we revert to API v2.1 now."
        ),
        "meta": {
            "channel": "#eng-payments",
            "timestamp": "2026-08-25T10:05:00Z",
            "hypothesis": "internal",
            "source": "slack",
        },
    },
    {
        "id": "SLK-1002",
        "text": (
            "Slack #sales-floor 2026-08-25T11:47:00Z @ae_west - "
            "Heads up, third cancellation this morning all citing Competitor X's "
            "fifty percent off annual campaign that launched today. Procurement "
            "teams are using it as leverage. This is external pricing pressure, "
            "nothing to do with our platform."
        ),
        "meta": {
            "channel": "#sales-floor",
            "timestamp": "2026-08-25T11:47:00Z",
            "hypothesis": "external",
            "source": "slack",
        },
    },
    {
        "id": "SLK-1003",
        "text": (
            "Slack #infra-migration 2026-08-25T10:22:00Z @sre_oncall - "
            "p99 latency on payment-gateway-v2 is at 3450 ms, roughly thirty "
            "times the baseline we have been seeing since the migration on the "
            "23rd. We only have three days of history on this service so I "
            "cannot tell you whether this is seasonal or a real regression. "
            "Connection pool sizing on the new instances looks undersized."
        ),
        "meta": {
            "channel": "#infra-migration",
            "timestamp": "2026-08-25T10:22:00Z",
            "hypothesis": "internal",
            "source": "slack",
        },
    },
]


def seed_duckdb() -> None:
    print("=" * 70)
    print("DuckDB - creating and seeding structured tables")
    print("=" * 70)

    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        # Drop + recreate so repeated runs are idempotent.
        con.execute("DROP TABLE IF EXISTS billing_mrr")
        con.execute("DROP TABLE IF EXISTS platform_telemetry")
        con.execute("DROP TABLE IF EXISTS aws_cloudwatch_latency")

        con.execute("""
            CREATE TABLE billing_mrr (
                record_date             DATE PRIMARY KEY,
                total_mrr               DECIMAL(12, 2),
                new_mrr                 DECIMAL(12, 2),
                churned_mrr             DECIMAL(12, 2),
                failed_transactions_usd DECIMAL(12, 2)
            )
        """)
        con.executemany(
            "INSERT INTO billing_mrr VALUES (?, ?, ?, ?, ?)", _billing_rows()
        )

        con.execute("""
            CREATE TABLE platform_telemetry (
                ts                     TIMESTAMP,
                active_sessions        INTEGER,
                gateway_timeout_errors INTEGER,
                successful_logins      INTEGER
            )
        """)
        con.executemany(
            "INSERT INTO platform_telemetry VALUES (?, ?, ?, ?)", _telemetry_rows()
        )

        con.execute("""
            CREATE TABLE aws_cloudwatch_latency (
                ts             TIMESTAMP,
                service_name   VARCHAR(50),
                p99_latency_ms DECIMAL(10, 2)
            )
        """)
        con.executemany(
            "INSERT INTO aws_cloudwatch_latency VALUES (?, ?, ?)", _latency_rows()
        )

        # ---- verification output (Build Step 2 requires SELECT * on billing_mrr)
        print("\nbilling_mrr - last 5 days:")
        rows = con.execute("""
            SELECT record_date, total_mrr, new_mrr, churned_mrr,
                   failed_transactions_usd
            FROM billing_mrr ORDER BY record_date DESC LIMIT 5
        """).fetchall()
        print(f"  {'date':<12}{'total_mrr':>14}{'new_mrr':>12}"
              f"{'churned_mrr':>14}{'failed_txn':>13}")
        for r in reversed(rows):
            print(f"  {str(r[0]):<12}{float(r[1]):>14,.2f}{float(r[2]):>12,.2f}"
                  f"{float(r[3]):>14,.2f}{float(r[4]):>13,.2f}")

        n_billing = con.execute("SELECT COUNT(*) FROM billing_mrr").fetchone()[0]
        n_tel = con.execute("SELECT COUNT(*) FROM platform_telemetry").fetchone()[0]
        n_lat = con.execute("SELECT COUNT(*) FROM aws_cloudwatch_latency").fetchone()[0]
        days = con.execute(
            "SELECT COUNT(DISTINCT CAST(ts AS DATE)) FROM aws_cloudwatch_latency"
        ).fetchone()[0]
        peak = con.execute("""
            SELECT ts, gateway_timeout_errors FROM platform_telemetry
            ORDER BY gateway_timeout_errors DESC LIMIT 1
        """).fetchone()

        print(f"\n  billing_mrr             rows = {n_billing}")
        print(f"  platform_telemetry      rows = {n_tel}   peak = "
              f"{peak[1]} errors at {peak[0]}")
        print(f"  aws_cloudwatch_latency  rows = {n_lat}   distinct days = {days}")
        assert days == 3, f"sparse-history constraint violated: {days} days"
        assert peak[1] == 500, f"expected 500 peak errors, got {peak[1]}"
        print("  constraints OK (3-day sparse history, 500-error peak)")
    finally:
        con.close()


def seed_chroma() -> None:
    print()
    print("=" * 70)
    print("ChromaDB - creating and seeding unstructured evidence collections")
    print("=" * 70)

    import chromadb

    # Wipe for idempotency - repeated runs must not duplicate documents.
    if CHROMA_PATH.exists():
        shutil.rmtree(CHROMA_PATH)

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    embed = LocalDeterministicEmbedding()

    for name, docs in (
        ("zendesk_tickets", ZENDESK_TICKETS),
        ("slack_engineering", SLACK_MESSAGES),
    ):
        col = client.get_or_create_collection(name=name, embedding_function=embed)
        col.add(
            ids=[d["id"] for d in docs],
            documents=[d["text"] for d in docs],
            metadatas=[d["meta"] for d in docs],
        )
        print(f"  {name:<20} documents = {col.count()}")

    # ---- retrieval smoke test
    print("\n  retrieval check:")
    for query, expect in (
        ("payment gateway timeout failing charges", "internal"),
        ("competitor discount campaign cancellation", "external"),
    ):
        col = client.get_collection("zendesk_tickets", embedding_function=embed)
        res = col.query(query_texts=[query], n_results=1)
        got = res["metadatas"][0][0]["hypothesis"]
        flag = "OK " if got == expect else "FAIL"
        print(f"    [{flag}] '{query[:38]}...' -> {res['ids'][0][0]} ({got})")


def main() -> None:
    seed_duckdb()
    seed_chroma()
    print()
    print("=" * 70)
    print("Seeding complete.")
    print(f"  {DUCKDB_PATH.name}")
    print(f"  {CHROMA_PATH.name}/")
    print("Next: python agents.py   (standalone backend test)")
    print("=" * 70)


if __name__ == "__main__":
    main()
