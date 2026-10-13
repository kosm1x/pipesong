-- A.1 / A.2: caller-heard time to first audio and corpus-hit count per latency turn.
-- Only needed on databases created BEFORE 2026-10-13 — fresh boxes get the new schema
-- from SQLAlchemy create_all() at startup (create_all never alters existing tables).
--
-- ORDERING:
--   1. Apply this SQL FIRST, then deploy/restart the new code. New code against an
--      un-migrated DB fails every call_latency write and read (UndefinedColumn).
--   2. Additive only (two nullable columns): old code keeps working against the
--      migrated table, so no need to stop it first.
-- Apply: psql "$DATABASE_URL" -f scripts/migrations/2026-10-13-call-latency-ttfa.sql

BEGIN;

ALTER TABLE call_latency ADD COLUMN IF NOT EXISTS ttfa_ms double precision;  -- user EOT -> bot first audio (NULL = no EOT, e.g. turn 0, or pre-deploy row)
ALTER TABLE call_latency ADD COLUMN IF NOT EXISTS prerecorded_hits integer;  -- corpus clips played in the turn (NULL = pre-deploy row)

COMMIT;

-- Rows written before this deploy hold NULL in both columns, and their tts_ttfb_ms is the
-- turn's LAST sentence's synthesis TTFB (pre-A.1 overwrite semantics), not the first.
-- A.2 split: prerecorded_hits = 0 -> all-TTS turn; > 0 -> the turn played clips. Read "was the
-- first audio a clip?" from prerecorded_hits together with ttfa_ms, NOT from tts_ttfb_ms: a hit's
-- MetricsFrame can overtake LLMFullResponseStartFrame when the LLM's first sentence is instant,
-- leaving tts_ttfb_ms NULL or set by a later Kokoro sentence.
