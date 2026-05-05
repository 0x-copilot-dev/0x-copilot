-- Citations live registry (PR 1.1, design at docs/new-design/01-citations-live-registry.md).
--
-- Stores one row per (run, source) the assistant cited, so the workspace
-- pane Sources tab and the share-recipient ACL view can read by index
-- instead of replaying every `source_ingested` event for the run.
--
-- The row is the durable mirror of the wire `CitationSourceRef`. The
-- citation_id is allocated by `CitationLedger` as `c<base36(ordinal)>`
-- (e.g. c1, c2, czh) and is stable per run.
--
-- Encryption: title and snippet may carry user-content from connector
-- documents; both are encryption-version-tagged so the existing
-- envelope codec (migration 0011) handles them on read/write.
--
-- RLS: tenant_isolation policy mirrors the pattern in 0008. The
-- corresponding ENABLE/FORCE statements live in staged/do_rls.sql so
-- the rollout matches the rest of the runtime.

CREATE TABLE IF NOT EXISTS runtime_citations (
    citation_id          TEXT NOT NULL,
    run_id               TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    conversation_id      TEXT NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
    org_id               TEXT NOT NULL,
    ordinal              INTEGER NOT NULL,
    source_connector     TEXT NOT NULL,
    source_doc_id        TEXT NOT NULL,
    source_url           TEXT,
    title                TEXT NOT NULL,
    snippet              TEXT,
    freshness_at         TIMESTAMPTZ,
    source_tool_call_id  TEXT,
    encryption_version   SMALLINT NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (run_id, citation_id)
);

-- Idempotency: cite() on the same (run, connector, doc_id) returns the
-- cached citation_id and does not insert a new row.
CREATE UNIQUE INDEX IF NOT EXISTS runtime_citations_run_source_uk
    ON runtime_citations (run_id, source_connector, source_doc_id);

-- Sources tab read path: list a conversation's citations in seen order.
CREATE INDEX IF NOT EXISTS runtime_citations_conv_idx
    ON runtime_citations (conversation_id, created_at);

-- Tenant-scoped reads (RLS-friendly) and admin lookups by connector.
CREATE INDEX IF NOT EXISTS runtime_citations_org_idx
    ON runtime_citations (org_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON runtime_citations TO enterprise_app;

CREATE POLICY tenant_isolation ON runtime_citations
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
