-- Phase 7A — tier-2 adapter registry (candidates / reviews / promoted /
-- per-tenant settings / audit chain).
--
-- Source bytes are stored externally (object-store in prod, filesystem
-- in dev); the rows below carry the ``storage_key`` + ``source_digest``
-- pair so the artifact is content-addressed. The digest is 64 hex chars
-- (sha256). RLS scopes candidate + audit rows to the origin tenant;
-- promoted adapters are visible across tenants by design, but listed
-- via the service layer which honours ``tenant_adapter_settings``.

CREATE TABLE IF NOT EXISTS adapter_candidates (
    candidate_id        TEXT         PRIMARY KEY,
    tenant_id           TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    submitter_user_id   TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    scheme              TEXT         NOT NULL,
    version             INTEGER      NOT NULL CHECK (version >= 1),
    layout              TEXT         NOT NULL CHECK (layout IN (
        'form','table','kanban','definition-list'
    )),
    storage_key         TEXT         NOT NULL,
    source_digest       TEXT         NOT NULL CHECK (char_length(source_digest) = 64),
    source_bytes        INTEGER      NOT NULL CHECK (source_bytes > 0),
    harvest_metrics     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    status              TEXT         NOT NULL CHECK (status IN (
        'submitted','in-review','changes-requested','approved','rejected'
    )),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_adapter_candidates_tenant
    ON adapter_candidates (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_adapter_candidates_status
    ON adapter_candidates (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_adapter_candidates_scheme_version
    ON adapter_candidates (scheme, version);

ALTER TABLE adapter_candidates ENABLE ROW LEVEL SECURITY;
CREATE POLICY adapter_candidates_tenant_isolation ON adapter_candidates
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));

CREATE TABLE IF NOT EXISTS adapter_reviews (
    review_id           TEXT         PRIMARY KEY,
    candidate_id        TEXT         NOT NULL REFERENCES adapter_candidates(candidate_id) ON DELETE CASCADE,
    reviewer_user_id    TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    reviewer_org_id     TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE RESTRICT,
    action              TEXT         NOT NULL CHECK (action IN (
        'approve','reject','request-changes'
    )),
    notes               TEXT,
    decided_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_adapter_reviews_candidate
    ON adapter_reviews (candidate_id, decided_at);

CREATE TABLE IF NOT EXISTS promoted_adapters (
    promoted_id            TEXT         PRIMARY KEY,
    scheme                 TEXT         NOT NULL,
    version                INTEGER      NOT NULL CHECK (version >= 1),
    schema_version         INTEGER      NOT NULL CHECK (schema_version >= 1),
    layout                 TEXT         NOT NULL CHECK (layout IN (
        'form','table','kanban','definition-list'
    )),
    storage_key            TEXT         NOT NULL,
    source_digest          TEXT         NOT NULL CHECK (char_length(source_digest) = 64),
    source_bytes           INTEGER      NOT NULL CHECK (source_bytes > 0),
    origin_tenant_id       TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE RESTRICT,
    source_candidate_id    TEXT         NOT NULL REFERENCES adapter_candidates(candidate_id) ON DELETE RESTRICT,
    promoted_by_user_id    TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    promoted_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (scheme, schema_version)
);

CREATE INDEX IF NOT EXISTS idx_promoted_adapters_scheme
    ON promoted_adapters (scheme, schema_version DESC);

CREATE TABLE IF NOT EXISTS tenant_adapter_settings (
    tenant_id           TEXT         PRIMARY KEY REFERENCES organizations(org_id) ON DELETE CASCADE,
    opted_out           BOOLEAN      NOT NULL DEFAULT FALSE,
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by_user_id  TEXT         REFERENCES users(user_id) ON DELETE SET NULL
);

ALTER TABLE tenant_adapter_settings ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_adapter_settings_tenant_isolation ON tenant_adapter_settings
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));

CREATE TABLE IF NOT EXISTS adapter_registry_audit_events (
    audit_id            TEXT         PRIMARY KEY,
    tenant_id           TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE RESTRICT,
    actor_user_id       TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    candidate_id        TEXT         REFERENCES adapter_candidates(candidate_id) ON DELETE SET NULL,
    promoted_id         TEXT         REFERENCES promoted_adapters(promoted_id) ON DELETE SET NULL,
    action              TEXT         NOT NULL CHECK (char_length(action) BETWEEN 1 AND 64),
    metadata            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    seq                 BIGINT,
    prev_hash           BYTEA,
    signature           BYTEA,
    key_version         INTEGER
);

CREATE INDEX IF NOT EXISTS idx_adapter_registry_audit_tenant
    ON adapter_registry_audit_events (tenant_id, seq DESC);

ALTER TABLE adapter_registry_audit_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY adapter_registry_audit_tenant_isolation ON adapter_registry_audit_events
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON adapter_candidates TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON adapter_reviews TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON promoted_adapters TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_adapter_settings TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT ON adapter_registry_audit_events TO enterprise_app';
    END IF;
END
$$;
