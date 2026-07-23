-- 0006_stage_commit_ledger — Generative Surfaces v2 CommitEngine idempotency
-- ledger (PRD-D2; ../../docs/plan/generative-surfaces-v2/02-sdr.md §5/§10).
--
-- The v2 CommitEngine claims one row here BEFORE dispatching an approved write to
-- a real connector, so a retry / crash-replay observes the claim and never
-- double-sends (at-most-once for irreversible actions). The claim is the atomic
-- `INSERT ... ON CONFLICT (commit_key) DO NOTHING RETURNING` primitive: exactly
-- one concurrent worker's insert returns a row.
--
-- `commit_key` = `stage_id:rev:decision_seq`. `stage_id` is a uuid4 hex, so the
-- key is globally unique and unguessable; the row holds only claim state + a small
-- connector receipt (never tenant-readable content). It is therefore NOT
-- RLS-partitioned — it is an operator-role claim ledger keyed by an opaque token,
-- reached only through the worker-role connection (like the outbox claim), never a
-- tenant request path. `org_id` is stored for audit joins only.
--
-- Pure additive: a new table + its unique key; safe to apply as a separate deploy
-- step (RUNTIME_MIGRATIONS_AUTO_APPLY=false).

CREATE TABLE IF NOT EXISTS runtime_stage_commit_ledger (
    commit_key   text PRIMARY KEY,
    org_id       text,
    committed    boolean NOT NULL DEFAULT false,
    result_json  jsonb,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz
);
