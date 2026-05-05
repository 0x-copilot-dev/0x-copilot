-- PR 1.3: runtime_drafts table — append-only versioned draft artifacts.
--
-- Drafts back the Atlas Workspace pane "Draft" tab. Producers: the agent via
-- deepagents' write_file/edit_file routed through DraftBackend (CompositeBackend
-- /drafts/ prefix), and the user via the PATCH /v1/agent/drafts/{id} edit-in-
-- place endpoint. Each successful write inserts one new row; status changes
-- (send_pending_approval, sent, discarded, send_failed, draft) are also new
-- rows. Readers always select MAX(version).
--
-- Encryption follows the C7 convention: title, content_text, target_metadata
-- store encrypted_v1 envelopes (BYTEA) and carry encryption_version SMALLINT.
-- New writes always use encryption_version=1.
--
-- RLS follows the 0008_rls_tenant_isolation.sql convention: a tenant_isolation
-- policy that keys off current_setting('app.current_org_id', true). The policy
-- is created here but row-level security is enabled by do_rls.sql in a
-- separate stage.

CREATE TABLE runtime_drafts (
    id                  TEXT            PRIMARY KEY,
    draft_id            TEXT            NOT NULL,
    version             INTEGER         NOT NULL CHECK (version > 0),
    org_id              TEXT            NOT NULL,
    conversation_id     TEXT            NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
    run_id              TEXT            REFERENCES agent_runs(id) ON DELETE SET NULL,
    user_id             TEXT            NOT NULL,
    -- Encrypted fields. Plaintext historical rows would have encryption_version=0;
    -- new writes are always encryption_version=1.
    title               BYTEA           NOT NULL,
    content_text        BYTEA           NOT NULL,
    target_connector    TEXT,
    target_metadata     BYTEA,
    citation_ids        TEXT[]          NOT NULL DEFAULT '{}',
    status              TEXT            NOT NULL DEFAULT 'draft',
    encryption_version  SMALLINT        NOT NULL DEFAULT 1,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, draft_id, version),
    CHECK (status IN ('draft', 'send_pending_approval', 'sent', 'discarded', 'send_failed'))
);

-- Latest-version-per-draft lookups dominate the read path
-- (Workspace pane render + send precondition check). Composite index plus
-- DESC on version makes (org_id, conversation_id, draft_id) → latest a fast
-- scan-stop.
CREATE INDEX runtime_drafts_conversation_idx
    ON runtime_drafts (org_id, conversation_id, draft_id, version DESC);

-- Per-draft latest lookup (used by send/discard/patch endpoints).
CREATE INDEX runtime_drafts_draft_id_version_idx
    ON runtime_drafts (org_id, draft_id, version DESC);

-- Grants for the app role (matches 0008 pattern). enterprise_app handles all
-- runtime CRUD; enterprise_admin (BYPASSRLS) keeps migrations and the audit
-- exporter unaffected.
GRANT SELECT, INSERT, UPDATE, DELETE ON runtime_drafts TO enterprise_app;

-- Tenant-isolation policy. Dormant until do_rls.sql enables RLS on the table
-- in the separate enable-rls stage.
CREATE POLICY tenant_isolation ON runtime_drafts
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
