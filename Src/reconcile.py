#!/usr/bin/env python3
"""
Reconcile sample payment-provider webhook events against sample applications.

This is a from-scratch demo of a pattern I run in production (see the README
for the real-world write-up). It is intentionally small: one SQLite file,
two input files, no external services. The things it tries to prove are:

  1. A payment can be matched to its application cheaply, if checkout-time
     metadata carries the application id.
  2. A payment that arrives without that metadata still gets matched, via a
     looser fallback, but is flagged `needs_review` rather than trusted
     silently.
  3. Replaying the same webhook event twice is a no-op. Run this script
     twice in a row and the report does not change.

Usage:
    python Src/reconcile.py
"""

import csv
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "Reports"
DB_PATH = OUTPUT_DIR / "reconciliation.db"
SCHEMA_PATH = ROOT / "Sql" / "Schema.sql"
APPLICATIONS_CSV = ROOT / "Sample-Data" / "Applications.csv"
WEBHOOK_EVENTS_JSON = ROOT / "Sample-Data" / "Webhook-Events.json"
REPORT_CSV = OUTPUT_DIR / "reconciliation_report.csv"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())


def load_applications(conn: sqlite3.Connection) -> None:
    with APPLICATIONS_CSV.open() as f:
        for row in csv.DictReader(f):
            conn.execute(
                """
                INSERT OR IGNORE INTO applications
                    (application_id, applicant_name, organization, program_code, submitted_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["application_id"],
                    row["applicant_name"],
                    row["organization"],
                    row["program_code"],
                    row["submitted_at"],
                ),
            )
    conn.commit()


def find_application_by_hint(conn: sqlite3.Connection, name_hint: str, org_hint: str):
    """Loose fallback match. Flagged needs_review by the caller no matter what."""
    cur = conn.execute(
        """
        SELECT application_id FROM applications
        WHERE lower(applicant_name) = lower(?) AND lower(organization) = lower(?)
        """,
        (name_hint, org_hint),
    )
    row = cur.fetchone()
    return row[0] if row else None


def process_event(conn: sqlite3.Connection, event: dict) -> str:
    """Returns a short status string for logging: inserted / skipped_duplicate / unmatched."""
    event_id = event["id"]

    # Idempotency check: a payment_id that already exists means this webhook
    # was already processed, possibly on a prior run, possibly a retried
    # delivery within the same run. Either way, do nothing further.
    existing = conn.execute(
        "SELECT 1 FROM payments WHERE payment_id = ?", (event_id,)
    ).fetchone()

    # Always log the raw payload, even a duplicate, because the landing
    # table is a record of what arrived, not what was acted on.
    conn.execute(
        "INSERT INTO raw_inbound (source, payload_json, received_at, processed_at, processing_note) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            event.get("provider", "unknown"),
            json.dumps(event),
            event.get("created", now_iso()),
            now_iso(),
            "duplicate, already processed" if existing else "processing",
        ),
    )

    if existing:
        conn.commit()
        return "skipped_duplicate"

    application_id = (event.get("metadata") or {}).get("application_id")
    match_method = None
    match_confidence = "confirmed"
    resolved_application_id = None

    if application_id:
        found = conn.execute(
            "SELECT 1 FROM applications WHERE application_id = ?", (application_id,)
        ).fetchone()
        if found:
            resolved_application_id = application_id
            match_method = "metadata"
            match_confidence = "confirmed"
        else:
            match_method = "metadata"
            match_confidence = "needs_review"
    else:
        name_hint = event.get("payer_name_hint")
        org_hint = event.get("organization_hint")
        if name_hint and org_hint:
            resolved_application_id = find_application_by_hint(conn, name_hint, org_hint)
            match_method = "fallback_name_match"
            match_confidence = "needs_review"  # fallback matches are always reviewed by a human
        else:
            match_method = "unresolvable"
            match_confidence = "needs_review"

    conn.execute(
        """
        INSERT INTO payments
            (payment_id, application_id, amount_minor, currency, provider,
             match_method, match_confidence, received_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            resolved_application_id,
            event.get("amount", 0),
            event.get("currency", "unknown"),
            event.get("provider", "unknown"),
            match_method,
            match_confidence,
            event.get("created", now_iso()),
        ),
    )

    if resolved_application_id:
        conn.execute(
            "UPDATE applications SET status = 'paid' WHERE application_id = ?",
            (resolved_application_id,),
        )

    conn.commit()
    return "unmatched" if resolved_application_id is None else "inserted"


def write_report(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT a.application_id, a.applicant_name, a.organization, a.status,
               p.payment_id, p.match_method, p.match_confidence
        FROM applications a
        LEFT JOIN payments p ON p.application_id = a.application_id
        ORDER BY a.application_id
        """
    ).fetchall()

    with REPORT_CSV.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["application_id", "applicant_name", "organization", "status",
             "payment_id", "match_method", "match_confidence"]
        )
        writer.writerows(rows)

    unmatched = conn.execute(
        "SELECT payment_id, match_method FROM payments WHERE application_id IS NULL"
    ).fetchall()

    total_applications = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    paid = conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'paid'").fetchone()[0]
    needs_review = conn.execute(
        "SELECT COUNT(*) FROM payments WHERE match_confidence = 'needs_review'"
    ).fetchone()[0]

    print(f"Applications: {total_applications}  Paid: {paid}  Needs review: {needs_review}")
    if unmatched:
        print("Payments with no matching application (wrote to report as blank applicant):")
        for payment_id, method in unmatched:
            print(f"  - {payment_id} ({method})")
    print(f"Wrote {REPORT_CSV}")


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    fresh = not DB_PATH.exists()
    conn = sqlite3.connect(DB_PATH)
    load_schema(conn)
    load_applications(conn)

    events = json.loads(WEBHOOK_EVENTS_JSON.read_text())
    counts = {"inserted": 0, "skipped_duplicate": 0, "unmatched": 0}
    for event in events:
        result = process_event(conn, event)
        counts[result if result in counts else "inserted"] += 1
        print(f"  {event['id']}: {result}")

    print(f"\n{'Created' if fresh else 'Reused'} {DB_PATH}")
    print(f"Events processed this run: {counts}")
    write_report(conn)
    conn.close()


if __name__ == "__main__":
    main()
