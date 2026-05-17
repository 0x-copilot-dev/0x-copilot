-- P3-A2 — todo_extractions table.
--
-- Stores per-proposal rows produced by the post-run extractor worker
-- (``services/ai-backend/src/runtime_worker/jobs/todo_extractor.py``).
-- The extractor scans a completed run's transcript via the canonical
-- ``build_chat_model`` entry point so existing token-usage tracking
-- captures the LLM call with purpose=todo_extraction.
--
-- Proposals are durable from the moment they land here; user accept
-- transitions a row to ``accepted`` and writes one row into the public
-- ``todos`` table on the backend service (via the internal service-token
-- path owned by P3-A1). Reject simply transitions to ``rejected``.
--
-- Index strategy: tenant-first composite to keep the hot "list pending
-- for me" query on a single B-tree path, with the partial WHERE clause
-- so accepted/rejected history doesn't bloat the working set. RLS policy
-- mirrors the ``runtime_drafts`` / ``conversation_shares`` convention so
-- the worker process and the API surface both see correctly-scoped rows.
--
-- Streaming impact: zero. The extraction job is off the request handler
-- and does not emit runtime_events; the frontend polls for proposals
-- on destination load (and on a 60s timer while open) per todos-prd §3.7.

CREATE TABLE todo_extractions (
    id                       TEXT            PRIMARY KEY,
    org_id                   TEXT            NOT NULL,
    owner_user_id            TEXT            NOT NULL,
    run_id                   TEXT            NOT NULL,
    conversation_id          TEXT            NOT NULL,
    proposed_text            TEXT            NOT NULL,
    suggested_due            TEXT,                       -- YYYY-MM-DD; nullable
    suggested_project_id     TEXT,
    source_message_id        TEXT,
    confidence_score         DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    state                    TEXT            NOT NULL DEFAULT 'pending'
                                              CHECK (state IN ('pending', 'accepted', 'rejected')),
    created_at               TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    resolved_at              TIMESTAMPTZ,
    CONSTRAINT todo_extractions_resolution_consistency
        CHECK (
            (state = 'pending'  AND resolved_at IS NULL) OR
            (state <> 'pending' AND resolved_at IS NOT NULL)
        )
);

-- Tenant-first composite for "list my pending proposals". The partial
-- predicate keeps the index tight; resolved history scans go through a
-- separate (eventually-archival) path if/when we expose one.
CREATE INDEX ix_todo_extractions_owner_pending
    ON todo_extractions (org_id, owner_user_id, created_at DESC)
    WHERE state = 'pending';

-- Tenant-scoped lookup by source run (extractor idempotency / dedupe).
CREATE INDEX ix_todo_extractions_org_run
    ON todo_extractions (org_id, run_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON todo_extractions TO enterprise_app;

-- Tenant isolation. Dormant until do_rls.sql enables RLS in the
-- separate enable-rls stage — same pattern as conversation_shares.
CREATE POLICY tenant_isolation ON todo_extractions
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
