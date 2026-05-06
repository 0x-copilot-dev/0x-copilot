-- PR 6.1 — conversation_shares + conversation_share_recipients.
--
-- A share is one row in ``conversation_shares`` granting an org's members
-- (workspace mode) or a named recipient set (specific mode) read access to
-- a conversation snapshot. An optional bearer token (sha256-hashed, never
-- stored in plaintext — same pattern as ``scim_tokens.token_hash`` on the
-- backend service) doubles as a copy-link.
--
-- Snapshot semantics: ``snapshot_at`` is immutable; the recipient endpoint
-- clamps message / event / citation reads to ``created_at <= snapshot_at``.
-- "Share latest" creates a new row + revokes the old token in one TX.
--
-- Streaming impact: zero. No event_type, no projection change, no
-- runtime_events touch. Recipient view is a snapshot read built from the
-- existing message / event / citation / draft / subagent read paths.
--
-- RLS follows the 0008_rls_tenant_isolation.sql convention. The recipient
-- read happens on a request the API service has already authorised — the
-- service sets ``app.current_org_id`` to the share's ``org_id`` after
-- proving the caller belongs to that org.
--
-- Sequence note: PR 6.2 (migration 0022) already added the fork-lineage FK
-- + the ``forked_from_share_id`` column on ``agent_conversations``. This
-- migration (0023) only adds the share tables themselves; PR 6.2's column
-- references back to a row this migration creates, but at the schema
-- level the column carries no FK to ``conversation_shares.share_id`` —
-- shares can be revoked / cleaned up independently of the conversation
-- they authorised.

CREATE TABLE conversation_shares (
    share_id                    TEXT            PRIMARY KEY,
    org_id                      TEXT            NOT NULL,
    conversation_id             TEXT            NOT NULL
                                                  REFERENCES agent_conversations(id) ON DELETE CASCADE,
    created_by_user_id          TEXT            NOT NULL,
    view_access                 TEXT            NOT NULL
                                                  CHECK (view_access IN ('workspace', 'specific')),
    sources_visible_to_viewer   BOOLEAN         NOT NULL DEFAULT FALSE,
    -- ``share_token_hash`` is sha256(plaintext) — the plaintext is returned
    -- once at create time and never persisted. ``share_token_prefix`` is the
    -- first 10 plaintext chars (``s_`` namespace + 8) for the share-list UI;
    -- cryptographic safety still rests on the full hash.
    share_token_hash            TEXT,
    share_token_prefix          TEXT,
    snapshot_at                 TIMESTAMPTZ     NOT NULL,
    expires_at                  TIMESTAMPTZ,
    revoked_at                  TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    -- Token shape consistency: either both columns are set (link share)
    -- or both are null (people-only share, no copy-link).
    CONSTRAINT conversation_shares_token_consistency
        CHECK (
            (share_token_hash IS NULL AND share_token_prefix IS NULL) OR
            (share_token_hash IS NOT NULL AND share_token_prefix IS NOT NULL)
        )
);

-- Token lookup is O(1). Only rows with a token participate; people-only
-- shares stay out of this index.
CREATE UNIQUE INDEX ux_conversation_shares_token_hash
    ON conversation_shares (share_token_hash)
    WHERE share_token_hash IS NOT NULL;

-- Creator's "shares on this chat" list (for the popover). Excludes revoked.
CREATE INDEX ix_conversation_shares_active
    ON conversation_shares (org_id, conversation_id, created_at DESC)
    WHERE revoked_at IS NULL;

-- Grants — same pattern as runtime_drafts (0014). enterprise_admin BYPASSRLS
-- so migrations + audit exporter are unaffected.
GRANT SELECT, INSERT, UPDATE, DELETE ON conversation_shares TO enterprise_app;

-- Tenant-isolation policy. Dormant until do_rls.sql enables RLS in the
-- separate enable-rls stage.
CREATE POLICY tenant_isolation ON conversation_shares
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));


CREATE TABLE conversation_share_recipients (
    share_id                    TEXT            NOT NULL
                                                  REFERENCES conversation_shares(share_id) ON DELETE CASCADE,
    user_id                     TEXT            NOT NULL,
    granted_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (share_id, user_id)
);

-- Reverse lookup: "shares assigned to this user". Cheap; used by the
-- recipient gate when the path is `view_access='specific'`.
CREATE INDEX ix_conversation_share_recipients_user
    ON conversation_share_recipients (user_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON conversation_share_recipients TO enterprise_app;

-- Recipients inherit tenant isolation through their parent share row.
-- The EXISTS subquery is cheap (PK lookup on share_id) and keeps the
-- policy correct under all join paths.
CREATE POLICY tenant_isolation ON conversation_share_recipients
    USING (
        EXISTS (
            SELECT 1 FROM conversation_shares s
             WHERE s.share_id = conversation_share_recipients.share_id
               AND s.org_id   = current_setting('app.current_org_id', true)
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM conversation_shares s
             WHERE s.share_id = conversation_share_recipients.share_id
               AND s.org_id   = current_setting('app.current_org_id', true)
        )
    );
