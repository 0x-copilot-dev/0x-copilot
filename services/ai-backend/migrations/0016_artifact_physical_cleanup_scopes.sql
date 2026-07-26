-- 0016_artifact_physical_cleanup_scopes — retain just enough ownership to
-- fence a legal hold after logical artifact metadata has been purged.
--
-- GC candidates deliberately outlive ``runtime_artifacts`` / revisions.  A
-- late hold previously had no row left from which to discover that a digest
-- belonged to its user or conversation.  These rows contain opaque ids only;
-- they never contain body bytes, paths, titles, source refs, or payloads.

CREATE TABLE runtime_artifact_gc_candidate_scopes (
    provenance_org_id text NOT NULL,
    blob_key text NOT NULL,
    -- Empty string is the durable sentinel for an unknown dimension. Existing
    -- user and conversation ids are non-empty by contract, so the sentinel is
    -- unambiguous and lets the composite key remain NULL-free.
    user_id text NOT NULL DEFAULT '',
    conversation_id text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provenance_org_id, blob_key, user_id, conversation_id),
    FOREIGN KEY (provenance_org_id, blob_key)
        REFERENCES runtime_artifact_gc_candidates (provenance_org_id, blob_key)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT runtime_artifact_gc_candidate_scopes_blob_key_check
        CHECK (blob_key ~ '^[0-9a-f]{64}$')
);

CREATE INDEX idx_runtime_artifact_gc_candidate_scopes_blob
    ON runtime_artifact_gc_candidate_scopes (blob_key, provenance_org_id);

ALTER TABLE runtime_artifact_gc_candidate_scopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_artifact_gc_candidate_scopes FORCE ROW LEVEL SECURITY;

CREATE POLICY artifact_candidate_scope_worker_or_tenant
    ON runtime_artifact_gc_candidate_scopes
    USING (
        current_setting('app.role', true) = 'worker'
        OR provenance_org_id = current_setting('app.current_org_id', true)
    )
    WITH CHECK (
        current_setting('app.role', true) = 'worker'
        OR provenance_org_id = current_setting('app.current_org_id', true)
    );

GRANT SELECT, INSERT, UPDATE, DELETE
    ON runtime_artifact_gc_candidate_scopes TO enterprise_app;

-- A hold and final byte deletion are serialized on this additional digest
-- fence.  The existing artifact advisory lock still coordinates publication
-- and reference changes; this lock specifically closes the late-hold window
-- between candidate discovery and final unlink.
CREATE OR REPLACE FUNCTION runtime_artifact_hold_acquire_fences()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_org_id text;
    v_user_id text;
    v_resource_id text;
    v_scope text;
    v_blob_key text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_org_id := OLD.org_id;
        v_user_id := OLD.user_id;
        v_resource_id := OLD.resource_id;
        v_scope := OLD.scope;
    ELSE
        v_org_id := NEW.org_id;
        v_user_id := NEW.user_id;
        v_resource_id := NEW.resource_id;
        v_scope := NEW.scope;
    END IF;
    IF v_scope = 'conversation' AND v_user_id IS NULL THEN
        SELECT user_id INTO v_user_id
          FROM agent_conversations
         WHERE org_id = v_org_id AND id = v_resource_id;
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended('artifact-hold:org:' || v_org_id, 0)
    );
    IF v_user_id IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(
            hashtextextended(
                'artifact-hold:user:' || v_org_id || ':' || v_user_id,
                0
            )
        );
    END IF;
    IF v_scope = 'conversation' THEN
        PERFORM pg_advisory_xact_lock(
            hashtextextended(
                'artifact-hold:conversation:' || v_org_id || ':' || v_resource_id,
                0
            )
        );
    END IF;

    -- Take the same deterministic fences as the physical collector.  A hold
    -- that commits before GC's final check is observed; a concurrent hold
    -- serializes after the already-completed delete rather than interleaving
    -- half-way through an unlink.
    FOR v_blob_key IN
        SELECT DISTINCT s.blob_key
          FROM runtime_artifact_gc_candidate_scopes s
         WHERE s.provenance_org_id = v_org_id
           AND (
                (v_scope = 'org' AND v_resource_id = s.provenance_org_id)
                OR (v_scope = 'user' AND v_user_id = NULLIF(s.user_id, ''))
                OR (
                    v_scope = 'conversation'
                    AND v_resource_id = NULLIF(s.conversation_id, '')
                )
           )
         ORDER BY s.blob_key
    LOOP
        PERFORM pg_advisory_xact_lock(
            hashtextextended('artifact-gc-hold:' || v_blob_key, 0)
        );
    END LOOP;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION runtime_artifact_hold_pin_or_release()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_released_at timestamptz;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_released_at := now();
    ELSIF (
        TG_OP = 'UPDATE'
        AND OLD.released_at IS NULL
        AND NEW.released_at IS NOT NULL
    ) THEN
        v_released_at := NEW.released_at;
    END IF;

    IF v_released_at IS NOT NULL THEN
        UPDATE runtime_artifact_reference_edges
           SET released_at = v_released_at
         WHERE org_id = OLD.org_id
           AND reference_kind = 'legal_hold'
           AND reference_id = OLD.id
           AND released_at IS NULL;
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.released_at IS NOT NULL THEN
        RETURN NEW;
    END IF;

    UPDATE runtime_artifacts a
       SET deleted_at = NULL,
           updated_at = GREATEST(a.updated_at, NEW.created_at)
     WHERE a.org_id = NEW.org_id
       AND a.deleted_at IS NOT NULL
       AND (
            (NEW.scope = 'org' AND NEW.resource_id = a.org_id)
            OR (NEW.scope = 'user' AND NEW.user_id = a.user_id)
            OR (
                NEW.scope = 'conversation'
                AND NEW.resource_id = a.conversation_id
            )
       );

    INSERT INTO runtime_artifact_reference_edges (
        org_id, edge_id, user_id, blob_key, reference_kind, reference_id,
        created_at, released_at
    )
    SELECT
        a.org_id,
        'legal_hold:' || NEW.id || ':' || r.blob_key,
        a.user_id,
        r.blob_key,
        'legal_hold',
        NEW.id,
        NEW.created_at,
        NULL
      FROM runtime_artifacts a
      JOIN runtime_artifact_revisions r
        ON r.org_id = a.org_id AND r.artifact_id = a.artifact_id
     WHERE a.org_id = NEW.org_id
       AND (
            (NEW.scope = 'org' AND NEW.resource_id = a.org_id)
            OR (NEW.scope = 'user' AND NEW.user_id = a.user_id)
            OR (
                NEW.scope = 'conversation'
                AND NEW.resource_id = a.conversation_id
            )
       )
    ON CONFLICT (org_id, edge_id) DO UPDATE
        SET released_at = NULL;

    -- Metadata may already have been purged.  Candidate scopes retain only
    -- the ownership evidence necessary to install the same hold pin before
    -- final physical collection/reaping.
    INSERT INTO runtime_artifact_reference_edges (
        org_id, edge_id, user_id, blob_key, reference_kind, reference_id,
        created_at, released_at
    )
    SELECT DISTINCT
        s.provenance_org_id,
        'legal_hold:' || NEW.id || ':' || s.blob_key,
        NULLIF(s.user_id, ''),
        s.blob_key,
        'legal_hold',
        NEW.id,
        NEW.created_at,
        NULL
      FROM runtime_artifact_gc_candidate_scopes s
     WHERE s.provenance_org_id = NEW.org_id
       AND (
            (NEW.scope = 'org' AND NEW.resource_id = s.provenance_org_id)
            OR (NEW.scope = 'user' AND NEW.user_id = NULLIF(s.user_id, ''))
            OR (
                NEW.scope = 'conversation'
                AND NEW.resource_id = NULLIF(s.conversation_id, '')
            )
       )
    ON CONFLICT (org_id, edge_id) DO UPDATE
        SET released_at = NULL;
    RETURN NEW;
END;
$$;
