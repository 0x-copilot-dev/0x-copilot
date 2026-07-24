-- 0008_artifact_hold_fences — make legal holds authoritative for A2 bytes.
--
-- ``runtime_legal_holds`` predates artifacts and may be written directly by
-- the retention owner.  Database triggers are therefore the only reliable
-- place to make a concurrently-created hold fence lifecycle work; an
-- application post-commit callback would always retain a TOCTOU window.

CREATE OR REPLACE FUNCTION runtime_artifact_hold_acquire_fences()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_user_id text;
BEGIN
    v_user_id := NEW.user_id;
    IF NEW.scope = 'conversation' AND v_user_id IS NULL THEN
        SELECT user_id INTO v_user_id
          FROM agent_conversations
         WHERE org_id = NEW.org_id AND id = NEW.resource_id;
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended('artifact-hold:org:' || NEW.org_id, 0)
    );
    IF v_user_id IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(
            hashtextextended(
                'artifact-hold:user:' || NEW.org_id || ':' || v_user_id,
                0
            )
        );
    END IF;
    IF NEW.scope = 'conversation' THEN
        PERFORM pg_advisory_xact_lock(
            hashtextextended(
                'artifact-hold:conversation:' || NEW.org_id || ':' || NEW.resource_id,
                0
            )
        );
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION runtime_artifact_hold_pin_or_release()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND OLD.released_at IS NULL
       AND NEW.released_at IS NOT NULL THEN
        UPDATE runtime_artifact_reference_edges
           SET released_at = NEW.released_at
         WHERE org_id = NEW.org_id
           AND reference_kind = 'legal_hold'
           AND reference_id = NEW.id
           AND released_at IS NULL;
        RETURN NEW;
    END IF;

    IF NEW.released_at IS NOT NULL THEN
        RETURN NEW;
    END IF;

    -- If deletion acquired the fence immediately before this hold request,
    -- restore its metadata tombstone inside the hold's own transaction.  The
    -- physical bytes are separately pinned below, so a retention/GC worker
    -- cannot advance while the hold is active.
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

CREATE TRIGGER runtime_legal_holds_artifact_fence_before
BEFORE INSERT OR UPDATE OF released_at ON runtime_legal_holds
FOR EACH ROW
EXECUTE FUNCTION runtime_artifact_hold_acquire_fences();

CREATE TRIGGER runtime_legal_holds_artifact_pin_after
AFTER INSERT OR UPDATE OF released_at ON runtime_legal_holds
FOR EACH ROW
EXECUTE FUNCTION runtime_artifact_hold_pin_or_release();
