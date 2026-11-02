# Payment reconciliation template

A small, runnable template for a pattern I use in production: an application comes in, a payment
confirms it later (sometimes much later, sometimes never), and something has to join the two
without manual spreadsheet work.

This is original code written for this repo. It is not the system I run at work — the real one
reads from a central Postgres database behind role-based access, and the business rules (which
programs, which currencies, which reminder cadence) belong to my employer. This template keeps the
shape of the problem and throws away everything specific to any one company, using invented sample
data.

## The problem

A student fills a form (an "application"). Later, they pay to confirm a seat. Three things make
this harder than a simple join:

- The payment event (a webhook from a payment provider) and the application often arrive minutes
  to days apart, from two different systems.
- A payment's own fields rarely include a clean foreign key back to the application, unless you put
  one there yourself, as metadata, at checkout time.
- The job has to be safe to re-run. Webhooks get retried, scripts get re-triggered, and an
  idempotent design means replaying the same event twice never creates a duplicate payment or a
  double-counted application.

## Folder map

| Folder | What is in it |
|---|---|
| `Sql/` | `Schema.sql`: raw inbound landing table, applications, payments, refunds, reminder events |
| `Src/` | `reconcile.py` matches payments to applications. `funnel_analysis.py` reports conversion per reminder stage |
| `Sample-Data/` | Invented applicants, payment-provider webhook payloads (metadata included), and reminder sends |
| `Reports/` | Output from a run lands here. Ignored by git |

## How the matching works

1. Every checkout is created with the application's ID and a program code written into the payment
   provider's `metadata` field. This is the cheap, reliable join key, and it costs nothing at
   checkout time.
2. `reconcile.py` tries the metadata match first. If a payload arrives without usable metadata (an
   older link, a manual bank transfer logged by hand), it falls back to a looser match on applicant
   name and organization, and flags that row as `needs_review` rather than guessing silently.
3. Every payment is inserted with the provider's own event ID as a unique key, so replaying the same
   webhook is a no-op, not a duplicate.
4. A refund is its own row, linked to the original payment, never a status flag overwritten on top
   of the payment record. That keeps the payment row as a historical fact.

## Try it

```bash
pip install -r requirements.txt
python Src/reconcile.py        # builds Reports/reconciliation.db and Reports/reconciliation_report.csv
python Src/funnel_analysis.py  # reads Sample-Data/Reminder-Events.csv and prints conversion per stage
```

Run `python Src/reconcile.py` twice in a row. The report is identical both times, nothing is
double-counted, and the second run's log says how many events were already-seen no-ops. That
idempotency check is the part I actually care about demonstrating.

## A note on the companion case study

The real-world version of this problem, the reminder cadence, how I measured it, and what I would
change, is written up without code or company data in
[Application-To-Payment-Journey](https://github.com/nishantaraaj-prog/case-studies/blob/main/04-Product/Application-To-Payment-Journey.md).

## What I'd add next

A holdout test: withhold reminders from a small random slice of new applicants so "paid after a
reminder" can be separated from "paid because of a reminder." This template reports stage-level
conversion but doesn't yet prove causation, on purpose, that's a harder and separate problem.
