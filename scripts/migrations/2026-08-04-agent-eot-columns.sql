-- REPROBE Phase 1 (merge-gate S1): replace Silero-era VAD tuning with Flux EOT tuning.
-- Only needed on databases created BEFORE 2026-08-04 — fresh boxes get the new schema
-- from SQLAlchemy create_all() at startup (create_all never alters existing tables).
--
-- ORDERING (QA 2026-08-04 Phase 1 W1):
--   1. Apply this SQL FIRST, then deploy/restart the new code. New code against an
--      un-migrated DB boots clean but fails every agent read (UndefinedColumn).
--   2. Stop any OLD app process before applying: the DROPs below break old code that
--      still selects vad_* columns.
-- Apply: psql "$DATABASE_URL" -f scripts/migrations/2026-08-04-agent-eot-columns.sql

BEGIN;

ALTER TABLE agents ADD COLUMN IF NOT EXISTS eot_threshold double precision;        -- 0.5-0.9 (NULL = Flux default 0.7)
ALTER TABLE agents ADD COLUMN IF NOT EXISTS eager_eot_threshold double precision;  -- 0.3-0.9, <= eot_threshold (NULL = eager OFF)
ALTER TABLE agents ADD COLUMN IF NOT EXISTS eot_timeout_ms integer;                -- 500-10000 (NULL = Flux default 5000)

ALTER TABLE agents DROP COLUMN IF EXISTS vad_stop_secs;
ALTER TABLE agents DROP COLUMN IF EXISTS vad_confidence;

COMMIT;

-- Bounds are enforced at the API layer (Pydantic) and re-guarded at call time in
-- create_pipeline. DB CHECK constraints deliberately omitted (single writer = the API);
-- revisit if a second writer ever appears.
