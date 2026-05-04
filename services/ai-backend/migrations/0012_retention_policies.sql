-- C8: per-tenant retention policy table.
--
-- The schema already has runtime_context_payloads.retention_until,
-- runtime_legal_holds, and runtime_deletion_evidence (from 0001), but no
-- policy table and no sweeper. This migration adds the policy table; the
-- sweeper job is shipped in services/ai-backend/src/runtime_worker/jobs/.
--
-- Most-specific policy wins: conversation > assistant > user > org.
-- Each row is one (scope, resource_id, kind) policy with a TTL in seconds.
-- ``resource_id`` is NULL when ``scope='org'`` (the policy applies to the
-- whole tenant).  A unique index over the COALESCE(resource_id, '') key
-- enforces "one policy per (org, scope, resource, kind)" without breaking
-- on NULLs.

CREATE TABLE IF NOT EXISTS retention_policies (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    scope        TEXT NOT NULL CHECK (scope IN ('org','user','conversation','assistant')),
    resource_id  TEXT,
    kind         TEXT NOT NULL CHECK (kind IN (
        'messages','events','context_payloads','checkpoints','memory_items'
    )),
    ttl_seconds  BIGINT NOT NULL CHECK (ttl_seconds > 0),
    created_by_user_id TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_retention_policies_unique
    ON retention_policies (org_id, scope, COALESCE(resource_id, ''), kind);

CREATE INDEX IF NOT EXISTS idx_retention_policies_org_kind
    ON retention_policies (org_id, kind);
