#!/usr/bin/env python3
"""
Conversion-per-stage report for a staged reminder sequence.

Reads Sample-Data/Reminder-Events.csv: one row per reminder sent to an
application, with a `paid_at` timestamp if that application ever paid
(blank if it never did). An application can appear on several rows, one per
stage it reached before paying or running out of stages.

The question this answers: of the applications that were still unpaid when
a given stage's reminder went out, how many paid before the next one? That
is a meaningfully different (and smaller) number than "how many paid
eventually", and it is the number that tells you which stage is pulling its
weight.

Usage:
    python Src/funnel_analysis.py
"""

import csv
from collections import defaultdict
from pathlib import Path

REMINDER_EVENTS_CSV = Path(__file__).resolve().parent.parent / "Sample-Data" / "Reminder-Events.csv"
STAGE_ORDER = ["immediate", "day_2", "day_5", "day_7", "extended"]


def load_events():
    events = []
    with REMINDER_EVENTS_CSV.open() as f:
        for row in csv.DictReader(f):
            events.append(row)
    return events


def main():
    events = load_events()

    # Group reminder rows by application, in stage order, so we can tell
    # which stage was "current" when a payment landed.
    by_application = defaultdict(list)
    for row in events:
        by_application[row["application_id"]].append(row)

    for app_id in by_application:
        by_application[app_id].sort(
            key=lambda r: STAGE_ORDER.index(r["stage"])
        )

    sent_counts = defaultdict(int)
    paid_counts = defaultdict(int)
    never_paid = 0

    for app_id, rows in by_application.items():
        paid_at = next((r["paid_at"] for r in rows if r["paid_at"]), None)

        for i, row in enumerate(rows):
            stage = row["stage"]
            sent_counts[stage] += 1

            if not paid_at:
                continue

            # Credit this stage if the payment happened at or after this
            # reminder, and before the next one was sent (or there is no
            # next one).
            next_sent_at = rows[i + 1]["sent_at"] if i + 1 < len(rows) else None
            if paid_at >= row["sent_at"] and (next_sent_at is None or paid_at < next_sent_at):
                paid_counts[stage] += 1

        if not paid_at:
            never_paid += 1

    print(f"{'Stage':<10} {'Sent':>6} {'Paid after':>12} {'Conversion':>12}")
    print("-" * 44)
    for stage in STAGE_ORDER:
        sent = sent_counts.get(stage, 0)
        paid = paid_counts.get(stage, 0)
        if sent == 0:
            continue
        conversion = paid / sent * 100
        bar = "#" * int(conversion / 5)
        print(f"{stage:<10} {sent:>6} {paid:>12} {conversion:>10.1f}%  {bar}")

    total_applications = len(by_application)
    print(f"\n{total_applications} applications, {never_paid} never paid in this sample.")
    print(
        "\nNote: 'paid after a reminder' is not the same as 'paid because of "
        "a reminder' -- see the case study for why a holdout test is the "
        "honest way to separate the two."
    )


if __name__ == "__main__":
    main()
