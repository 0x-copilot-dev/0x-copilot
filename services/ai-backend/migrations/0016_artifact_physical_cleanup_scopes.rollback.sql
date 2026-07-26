-- Rollback 0016_artifact_physical_cleanup_scopes.
-- Restore the original 0008 trigger behavior before dropping its source rows.

CREATE OR REPLACE FUNCTION runtime_artifact_hold_acquire_fences()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_org_id text;
    v_user_id text;
    v_resource_id text;
    v_scope text;
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
    RETURN NEW;
END;
$$;

DROP TABLE IF EXISTS runtime_artifact_gc_candidate_scopes;
