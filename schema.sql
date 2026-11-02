-- Minimal schema for the application -> payment reconciliation pattern.
-- SQLite-flavored for this demo (reconcile.py targets sqlite3); the ideas
-- carry over directly to Postgres, which is what I actually run in production.

-- Everything inbound lands here first. A row here is a claim, not a fact.
-- It becomes a fact only once it has been validated and copied into one of
-- the tables below. Keeping a landing table means a bad payload never
-- corrupts a real record, and you can always replay it later.
CREATE TABLE IF NOT EXISTS raw_inbound (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,          -- e.g. 'stripe_webhook', 'manual_entry'
    payload_json    TEXT NOT NULL,
    received_at     TEXT NOT NULL,
    processed_at    TEXT,
    processing_note TEXT
);

CREATE TABLE IF NOT EXISTS applications (
    application_id   TEXT PRIMARY KEY,
    applicant_name   TEXT NOT NULL,
    organization     TEXT NOT NULL,         -- school / employer / cohort source
    program_code     TEXT NOT NULL,
    submitted_at     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'submitted'  -- submitted | paid | expired
);

-- Money is stored as minor units (cents / paise) plus an explicit currency
-- code. Nothing is converted at rest; conversion is a display-time concern.
CREATE TABLE IF NOT EXISTS payments (
    payment_id        TEXT PRIMARY KEY,     -- the provider's own event/charge id (idempotency key)
    application_id    TEXT REFERENCES applications(application_id),
    amount_minor      INTEGER NOT NULL,
    currency          TEXT NOT NULL,
    provider          TEXT NOT NULL,
    match_method      TEXT NOT NULL,        -- 'metadata' | 'fallback_name_match' | 'manual'
    match_confidence  TEXT NOT NULL DEFAULT 'confirmed', -- 'confirmed' | 'needs_review'
    received_at       TEXT NOT NULL
);

-- A refund is its own record, never a status overwrite on the payment row,
-- so the payment stays a clean historical fact.
CREATE TABLE IF NOT EXISTS refunds (
    refund_id       TEXT PRIMARY KEY,
    payment_id      TEXT NOT NULL REFERENCES payments(payment_id),
    amount_minor    INTEGER NOT NULL,
    reason          TEXT,
    refunded_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminder_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  TEXT NOT NULL REFERENCES applications(application_id),
    stage           TEXT NOT NULL,          -- 'immediate' | 'day_2' | 'day_5' | 'day_7' | 'extended'
    sent_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payments_application ON payments(application_id);
CREATE INDEX IF NOT EXISTS idx_reminders_application ON reminder_events(application_id);
