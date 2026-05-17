-- Phase 4 — Inbox destination canonical schema. The DDL below is the
-- single source of truth; the migration file at
-- ``services/backend/migrations/<NNNN>_inbox.sql`` is a verbatim copy so
-- the migration runner picks it up at boot.
--
-- Authorization is service-layer (cross-audit §1.3 — recipient writes,
-- project-member reads, admin compliance reads, 404-not-403) plus RLS
-- for tenant isolation; project membership lookup composes with this
-- table at the service layer (no FK to a projects table yet — Phase
-- 6+ ships ``project_members``).
--
-- Body split (inbox-prd §3 + §10): list rows carry ``body_ref`` opaque
-- pointer; the body row lives in ``inbox_bodies`` so list queries
-- never pay for body bytes.

CREATE TABLE IF NOT EXISTS inbox_bodies (
    body_ref     TEXT         PRIMARY KEY,
    tenant_id    TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    -- Markdown body; rendered client-side via the existing markdown
    -- primitive. Cap matches inbox-prd §5.1 (64 KB).
    body_markdown TEXT        NOT NULL CHECK (char_length(body_markdown) <= 65536),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

ALTER TABLE inbox_bodies ENABLE ROW LEVEL SECURITY;

CREATE POLICY inbox_bodies_tenant_isolation ON inbox_bodies
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


CREATE TABLE IF NOT EXISTS inbox_items (
    id              TEXT         PRIMARY KEY,
    tenant_id       TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    -- Recipient — the user this item is addressed to. The state
    -- machine + audit are driven from this column; cross-tenant
    -- inserts are rejected at the service layer (inbox-prd §7.1).
    owner_user_id   TEXT         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    project_id      TEXT,                                              -- nullable; no FK until Projects ships
    kind            TEXT         NOT NULL CHECK (
        kind IN (
            'approval_request',
            'mention',
            'error',
            'agent_question',
            'share_invite',
            'system_announcement'
        )
    ),
    title           TEXT         NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
    body_ref        TEXT         REFERENCES inbox_bodies(body_ref) ON DELETE SET NULL,
    -- ``links`` is the unified cross-destination pointer field
    -- (cross-audit §1.1) — every ``thread_id``/``run_id``/``approval_id``
    -- / ``project_id`` from the original sub-PRD collapses into a JSONB
    -- array of ItemRef rows. GIN index below indexes the (kind,id) pairs
    -- so reverse lookups ("find inbox items linked to run X") are fast.
    links           JSONB        NOT NULL DEFAULT '[]'::jsonb,
    -- Denormalized for list rendering; the canonical resolve goes
    -- through the ItemRef registry on row open. JSONB so the in-memory
    -- + postgres adapters share the schema.
    sender          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    state           TEXT         NOT NULL DEFAULT 'unread' CHECK (
        state IN ('unread','read','snoozed','dismissed')
    ),
    received_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    read_at         TIMESTAMPTZ,
    -- ``snoozed_until`` is required when state='snoozed'; CHECK below.
    snoozed_until   TIMESTAMPTZ,
    dismissed_at    TIMESTAMPTZ,
    -- Producer idempotency (inbox-prd §7.4); UNIQUE partial index
    -- below means resubmits with the same (producer_id, external_ref)
    -- return the existing row instead of a dupe.
    producer_id     TEXT,
    external_ref    TEXT,
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- State-machine invariant: snoozed_until is set IFF state='snoozed'.
    CONSTRAINT inbox_items_snoozed_until_invariant CHECK (
        (state = 'snoozed' AND snoozed_until IS NOT NULL)
        OR (state <> 'snoozed' AND snoozed_until IS NULL)
    )
);

-- Hot path: per-recipient state queue, newest first. Matches the list
-- endpoint default sort (received_at DESC, id) for cursor pagination.
CREATE INDEX IF NOT EXISTS inbox_items_recipient_state_idx
    ON inbox_items (tenant_id, owner_user_id, state, received_at DESC);

-- Project-scoped reads (cross-audit §1.3 — project-member visibility).
CREATE INDEX IF NOT EXISTS inbox_items_tenant_project_idx
    ON inbox_items (tenant_id, project_id, received_at DESC)
    WHERE project_id IS NOT NULL;

-- Reverse-link probe ("find inbox items pointing at run X"): GIN on
-- the JSONB ``links`` array indexes both kind and id fields.
CREATE INDEX IF NOT EXISTS inbox_items_links_gin
    ON inbox_items USING GIN (links jsonb_path_ops);

-- Reverse-sender probe ("inbox items from agent X"): JSONB GIN.
CREATE INDEX IF NOT EXISTS inbox_items_sender_gin
    ON inbox_items USING GIN (sender jsonb_path_ops);

-- Snooze wake cron support: ordered by snoozed_until.
CREATE INDEX IF NOT EXISTS inbox_items_snooze_wake_idx
    ON inbox_items (tenant_id, snoozed_until)
    WHERE state = 'snoozed';

-- Producer idempotency (inbox-prd §7.4). Partial UNIQUE so non-producer
-- rows (manual inserts in tests) are unaffected.
CREATE UNIQUE INDEX IF NOT EXISTS inbox_items_producer_idem_idx
    ON inbox_items (tenant_id, producer_id, external_ref)
    WHERE producer_id IS NOT NULL AND external_ref IS NOT NULL;

-- Tenant isolation via RLS — matches the policy on every product
-- table.
ALTER TABLE inbox_items ENABLE ROW LEVEL SECURITY;

CREATE POLICY inbox_items_tenant_isolation ON inbox_items
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));

-- ---------------------------------------------------------------------------
-- Audit events — append-only; bulk actions stamp a shared correlation_id
-- across every row written by the same bulk write (inbox-prd §6.1).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS inbox_audit_events (
    audit_id            TEXT         PRIMARY KEY,
    tenant_id           TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE RESTRICT,
    actor_user_id       TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    -- Dotted action taxonomy per inbox-prd §6.1:
    --   inbox.item_created
    --   inbox.mark_read / inbox.mark_unread / inbox.mark_snoozed / inbox.mark_dismissed
    --   inbox.item_body_accessed (compliance — every body read audited)
    action              TEXT         NOT NULL,
    target_kind         TEXT         NOT NULL DEFAULT 'inbox_item',
    target_id           TEXT         NOT NULL,
    before_state        JSONB,
    after_state         JSONB,
    correlation_id      TEXT,
    ts                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- audit-chain integration; same shape as todo_audit_events.
    seq                 BIGINT,
    prev_hash           BYTEA,
    signature           BYTEA,
    key_version         INTEGER
);

CREATE INDEX IF NOT EXISTS inbox_audit_tenant_idx
    ON inbox_audit_events (tenant_id, ts DESC);

CREATE INDEX IF NOT EXISTS inbox_audit_correlation_idx
    ON inbox_audit_events (correlation_id)
    WHERE correlation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS inbox_audit_target_idx
    ON inbox_audit_events (tenant_id, target_id, ts);

ALTER TABLE inbox_audit_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY inbox_audit_tenant_isolation ON inbox_audit_events
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON inbox_items TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON inbox_bodies TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT ON inbox_audit_events TO enterprise_app';
    END IF;
END
$$;
