-- 0007_artifact_repository — durable A2 metadata, references, and GC state.
-- Blob bodies stay on the explicitly configured shared filesystem volume.
-- Ledger publication uses the pre-existing runtime_outbox_events table.

CREATE TABLE runtime_artifacts (
    org_id text NOT NULL,
    user_id text NOT NULL,
    artifact_id text NOT NULL,
    conversation_id text NOT NULL,
    run_id text NOT NULL,
    kind text NOT NULL,
    title text NOT NULL,
    media_type text NOT NULL,
    current_revision integer NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    deleted_at timestamptz,
    PRIMARY KEY (org_id, artifact_id),
    CONSTRAINT runtime_artifacts_revision_positive CHECK (current_revision > 0),
    CONSTRAINT runtime_artifacts_kind_check
        CHECK (kind IN ('code', 'document', 'dataset', 'file')),
    CONSTRAINT runtime_artifacts_created_by_check
        CHECK (created_by IN ('model', 'subagent', 'user', 'system', 'import')),
    CONSTRAINT runtime_artifacts_deleted_after_created
        CHECK (deleted_at IS NULL OR deleted_at >= created_at)
);

CREATE TABLE runtime_artifact_revisions (
    org_id text NOT NULL,
    user_id text NOT NULL,
    artifact_id text NOT NULL,
    revision integer NOT NULL,
    parent_revision integer,
    content_ref text NOT NULL,
    content_digest text NOT NULL,
    byte_size bigint NOT NULL,
    author text NOT NULL,
    source_ref text,
    created_at timestamptz NOT NULL,
    blob_key text NOT NULL,
    range_supported boolean NOT NULL DEFAULT true,
    suggested_filename text,
    PRIMARY KEY (org_id, artifact_id, revision),
    CONSTRAINT runtime_artifact_revisions_artifact_fk
        FOREIGN KEY (org_id, artifact_id)
        REFERENCES runtime_artifacts (org_id, artifact_id)
        ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT runtime_artifact_revisions_revision_positive CHECK (revision > 0),
    CONSTRAINT runtime_artifact_revisions_parent_check CHECK (
        (revision = 1 AND parent_revision IS NULL)
        OR (revision > 1 AND parent_revision = revision - 1)
    ),
    CONSTRAINT runtime_artifact_revisions_byte_size_check CHECK (byte_size >= 0),
    CONSTRAINT runtime_artifact_revisions_digest_check
        CHECK (content_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT runtime_artifact_revisions_blob_key_check
        CHECK (blob_key ~ '^[0-9a-f]{64}$' AND blob_key = content_digest),
    CONSTRAINT runtime_artifact_revisions_author_check
        CHECK (author IN ('model', 'subagent', 'user', 'system', 'import'))
);

CREATE TABLE runtime_artifact_idempotency (
    org_id text NOT NULL,
    user_id text NOT NULL,
    route text NOT NULL,
    idempotency_key text NOT NULL,
    request_digest text NOT NULL,
    artifact_id text NOT NULL,
    revision integer,
    response_json jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, user_id, route, idempotency_key),
    CONSTRAINT runtime_artifact_idempotency_digest_check
        CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT runtime_artifact_idempotency_revision_check
        CHECK (revision IS NULL OR revision > 0)
);

CREATE TABLE runtime_artifact_reference_edges (
    org_id text NOT NULL,
    edge_id text NOT NULL,
    user_id text,
    blob_key text NOT NULL,
    reference_kind text NOT NULL,
    reference_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    released_at timestamptz,
    PRIMARY KEY (org_id, edge_id),
    CONSTRAINT runtime_artifact_reference_edges_blob_key_check
        CHECK (blob_key ~ '^[0-9a-f]{64}$'),
    CONSTRAINT runtime_artifact_reference_edges_kind_check
        CHECK (reference_kind IN (
            'artifact', 'effect', 'receipt', 'audit', 'legal_hold'
        )),
    CONSTRAINT runtime_artifact_reference_edges_release_check
        CHECK (released_at IS NULL OR released_at >= created_at),
    UNIQUE (org_id, reference_kind, reference_id, blob_key)
);

-- Purge eligibility keeps tenant provenance, but a final collector treats the
-- digest globally and rechecks every tenant before moving bytes.
CREATE TABLE runtime_artifact_gc_candidates (
    provenance_org_id text NOT NULL,
    blob_key text NOT NULL,
    candidate_since timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provenance_org_id, blob_key),
    CONSTRAINT runtime_artifact_gc_candidates_blob_key_check
        CHECK (blob_key ~ '^[0-9a-f]{64}$')
);

-- Physical quarantine is digest-global. provenance_org_id is diagnostic only
-- and never participates in the primary key, lock key, or reference decision.
CREATE TABLE runtime_artifact_gc_quarantine (
    blob_key text PRIMARY KEY,
    provenance_org_id text,
    candidate_since timestamptz NOT NULL,
    quarantined_at timestamptz NOT NULL DEFAULT now(),
    reaping_at timestamptz,
    CONSTRAINT runtime_artifact_gc_quarantine_blob_key_check
        CHECK (blob_key ~ '^[0-9a-f]{64}$'),
    CONSTRAINT runtime_artifact_gc_quarantine_reaping_check
        CHECK (reaping_at IS NULL OR reaping_at >= quarantined_at)
);

CREATE INDEX idx_runtime_artifacts_active_list
    ON runtime_artifacts (
        org_id, user_id, run_id, updated_at DESC, artifact_id ASC
    )
    WHERE deleted_at IS NULL;
CREATE INDEX idx_runtime_artifacts_deleted
    ON runtime_artifacts (org_id, deleted_at)
    WHERE deleted_at IS NOT NULL;
CREATE INDEX idx_runtime_artifacts_conversation_deleted
    ON runtime_artifacts (org_id, conversation_id, deleted_at)
    WHERE deleted_at IS NOT NULL;
CREATE INDEX idx_runtime_artifact_revisions_blob
    ON runtime_artifact_revisions (blob_key);
CREATE INDEX idx_runtime_artifact_idempotency_artifact
    ON runtime_artifact_idempotency (org_id, artifact_id, revision);
CREATE INDEX idx_runtime_artifact_reference_edges_live_blob
    ON runtime_artifact_reference_edges (blob_key, reference_kind)
    WHERE released_at IS NULL;
CREATE INDEX idx_runtime_artifact_gc_candidates_age
    ON runtime_artifact_gc_candidates (
        provenance_org_id, candidate_since, blob_key
    );
CREATE INDEX idx_runtime_artifact_gc_quarantine_age
    ON runtime_artifact_gc_quarantine (quarantined_at, blob_key);

ALTER TABLE runtime_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_artifact_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_artifact_idempotency ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_artifact_reference_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_artifact_gc_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_artifact_gc_quarantine ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_artifacts FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_artifact_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_artifact_idempotency FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_artifact_reference_edges FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_artifact_gc_candidates FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_artifact_gc_quarantine FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON runtime_artifacts
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
CREATE POLICY artifact_merge_worker_select ON runtime_artifacts
    FOR SELECT
    USING (
        current_setting('app.role', true) = 'worker'
        AND org_id IN (
            current_setting('app.artifact_merge_absorbed_org', true),
            current_setting('app.artifact_merge_survivor_org', true)
        )
    );
CREATE POLICY artifact_merge_worker_update ON runtime_artifacts
    FOR UPDATE
    USING (
        current_setting('app.role', true) = 'worker'
        AND org_id = current_setting('app.artifact_merge_absorbed_org', true)
    )
    WITH CHECK (
        current_setting('app.role', true) = 'worker'
        AND org_id = current_setting('app.artifact_merge_survivor_org', true)
    );
CREATE POLICY tenant_isolation ON runtime_artifact_revisions
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
CREATE POLICY artifact_worker_global_select ON runtime_artifact_revisions
    FOR SELECT
    USING (current_setting('app.role', true) = 'worker');
CREATE POLICY artifact_merge_worker_update ON runtime_artifact_revisions
    FOR UPDATE
    USING (
        current_setting('app.role', true) = 'worker'
        AND org_id = current_setting('app.artifact_merge_absorbed_org', true)
    )
    WITH CHECK (
        current_setting('app.role', true) = 'worker'
        AND org_id = current_setting('app.artifact_merge_survivor_org', true)
    );
CREATE POLICY tenant_isolation ON runtime_artifact_idempotency
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
CREATE POLICY artifact_merge_worker_select ON runtime_artifact_idempotency
    FOR SELECT
    USING (
        current_setting('app.role', true) = 'worker'
        AND org_id IN (
            current_setting('app.artifact_merge_absorbed_org', true),
            current_setting('app.artifact_merge_survivor_org', true)
        )
    );
CREATE POLICY artifact_merge_worker_update ON runtime_artifact_idempotency
    FOR UPDATE
    USING (
        current_setting('app.role', true) = 'worker'
        AND org_id = current_setting('app.artifact_merge_absorbed_org', true)
    )
    WITH CHECK (
        current_setting('app.role', true) = 'worker'
        AND org_id = current_setting('app.artifact_merge_survivor_org', true)
    );
CREATE POLICY tenant_isolation ON runtime_artifact_reference_edges
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
CREATE POLICY artifact_worker_global_select ON runtime_artifact_reference_edges
    FOR SELECT
    USING (current_setting('app.role', true) = 'worker');
CREATE POLICY artifact_merge_worker_update ON runtime_artifact_reference_edges
    FOR UPDATE
    USING (
        current_setting('app.role', true) = 'worker'
        AND org_id = current_setting('app.artifact_merge_absorbed_org', true)
    )
    WITH CHECK (
        current_setting('app.role', true) = 'worker'
        AND org_id = current_setting('app.artifact_merge_survivor_org', true)
    );
CREATE POLICY tenant_or_worker ON runtime_artifact_gc_candidates
    USING (
        current_setting('app.role', true) = 'worker'
        OR provenance_org_id = current_setting('app.current_org_id', true)
    )
    WITH CHECK (
        current_setting('app.role', true) = 'worker'
        OR provenance_org_id = current_setting('app.current_org_id', true)
    );
CREATE POLICY artifact_gc_worker_global ON runtime_artifact_gc_quarantine
    FOR ALL
    USING (current_setting('app.role', true) = 'worker')
    WITH CHECK (current_setting('app.role', true) = 'worker');
CREATE POLICY artifact_publication_restore ON runtime_artifact_gc_quarantine
    FOR DELETE
    USING (current_setting('app.role', true) IN ('api', 'worker'));
CREATE POLICY artifact_candidate_restore ON runtime_artifact_gc_candidates
    FOR DELETE
    USING (current_setting('app.role', true) IN ('api', 'worker'));
CREATE POLICY artifact_evidence_merge_worker_select ON runtime_deletion_evidence
    FOR SELECT
    USING (
        current_setting('app.role', true) = 'worker'
        AND request_type = 'artifact_lifecycle'
        AND org_id IN (
            current_setting('app.artifact_merge_absorbed_org', true),
            current_setting('app.artifact_merge_survivor_org', true)
        )
    );
CREATE POLICY artifact_evidence_merge_worker_update ON runtime_deletion_evidence
    FOR UPDATE
    USING (
        current_setting('app.role', true) = 'worker'
        AND request_type = 'artifact_lifecycle'
        AND org_id = current_setting('app.artifact_merge_absorbed_org', true)
    )
    WITH CHECK (
        current_setting('app.role', true) = 'worker'
        AND request_type = 'artifact_lifecycle'
        AND org_id = current_setting('app.artifact_merge_survivor_org', true)
    );

GRANT SELECT, INSERT, UPDATE, DELETE ON runtime_artifacts TO enterprise_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON runtime_artifact_revisions TO enterprise_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON runtime_artifact_idempotency TO enterprise_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON runtime_artifact_reference_edges TO enterprise_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON runtime_artifact_gc_candidates TO enterprise_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON runtime_artifact_gc_quarantine TO enterprise_app;
