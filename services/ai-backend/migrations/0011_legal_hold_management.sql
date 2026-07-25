-- 0011_legal_hold_management — durable, revisioned D11 legal-hold control plane.
--
-- ``runtime_legal_holds`` already owns enforcement.  This migration adds the
-- minimum state required to expose that ownership safely: a closed reason
-- vocabulary, idempotent create/release operations, and a compare-and-swap
-- revision.  No generic external resource reference is introduced.

-- Normalize the historical scope encoding before constraining new writes.
-- A user hold with a NULL ``user_id`` was never safely enforceable against
-- user-owned rows. Its canonical resource id is the only safe owner value;
-- normalizing it can only retain more data, never broaden deletion. Org holds
-- likewise canonically target their own tenant. Conversation holds retain
-- their resource id even when a deleted source conversation no longer lets us
-- recover the old user owner.
UPDATE runtime_legal_holds
   SET user_id = resource_id
 WHERE scope = 'user' AND user_id IS NULL;

UPDATE runtime_legal_holds
   SET resource_id = org_id,
       user_id = NULL
 WHERE scope = 'org' AND (resource_id <> org_id OR user_id IS NOT NULL);

UPDATE runtime_legal_holds h
   SET user_id = c.user_id
  FROM agent_conversations c
 WHERE h.scope = 'conversation'
   AND h.user_id IS NULL
   AND c.org_id = h.org_id
   AND c.id = h.resource_id;

-- Re-fire the pre-existing active-hold trigger after normalization so its
-- artifact pins also reflect the repaired user-scoped owner mapping.
UPDATE runtime_legal_holds
   SET released_at = released_at
 WHERE scope = 'user' AND released_at IS NULL;

ALTER TABLE runtime_legal_holds
    ADD COLUMN reason_code text NOT NULL DEFAULT 'legacy',
    ADD COLUMN revision integer NOT NULL DEFAULT 1,
    ADD COLUMN create_idempotency_key text,
    ADD COLUMN create_request_digest text,
    ADD COLUMN release_idempotency_key text,
    ADD COLUMN release_request_digest text;

ALTER TABLE runtime_legal_holds
    ADD CONSTRAINT runtime_legal_holds_reason_code_check CHECK (
        reason_code IN (
            'legal_request', 'investigation', 'compliance', 'security', 'legacy'
        )
    ),
    ADD CONSTRAINT runtime_legal_holds_revision_check CHECK (revision > 0),
    ADD CONSTRAINT runtime_legal_holds_scope_owner_check CHECK (
        (scope = 'org' AND resource_id = org_id AND user_id IS NULL)
        OR (scope = 'user' AND user_id = resource_id)
        OR scope = 'conversation'
    ),
    ADD CONSTRAINT runtime_legal_holds_create_idempotency_shape_check CHECK (
        (create_idempotency_key IS NULL AND create_request_digest IS NULL)
        OR (
            create_idempotency_key IS NOT NULL
            AND create_request_digest ~ '^[0-9a-f]{64}$'
        )
    ),
    ADD CONSTRAINT runtime_legal_holds_release_idempotency_shape_check CHECK (
        (release_idempotency_key IS NULL AND release_request_digest IS NULL)
        OR (
            release_idempotency_key IS NOT NULL
            AND release_request_digest ~ '^[0-9a-f]{64}$'
        )
    );

-- User identity is part of the idempotency scope: an administrator can retry
-- their own request safely while a separate administrator's opaque key cannot
-- reveal whether a hold already exists.
CREATE UNIQUE INDEX idx_runtime_legal_holds_create_idempotency
    ON runtime_legal_holds (org_id, created_by_user_id, create_idempotency_key)
    WHERE create_idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX idx_runtime_legal_holds_release_idempotency
    ON runtime_legal_holds (org_id, released_by_user_id, release_idempotency_key)
    WHERE release_idempotency_key IS NOT NULL;

CREATE INDEX idx_runtime_legal_holds_org_active_created
    ON runtime_legal_holds (org_id, created_at DESC)
    WHERE released_at IS NULL;
