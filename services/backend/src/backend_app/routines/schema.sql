-- Phase 5 — Routines destination canonical schema. The DDL below is the
-- single source of truth; the migration file at
-- ``services/backend/migrations/<NNNN>_routines.sql`` is a verbatim copy
-- so the migration runner picks it up at boot.
--
-- Authorization is service-layer (cross-audit §1.3 / routines-prd §7 —
-- owner writes, project-member reads, admin compliance reads,
-- 404-not-403) plus RLS for tenant isolation; project-member lookup
-- composes with this table at the service layer (no FK to a projects
-- table yet — Phase 6+ ships ``project_members``).
--
-- Triggers are inline JSONB rather than a child table. The full per-
-- trigger CRUD endpoints in routines-prd §4.2 are P5-A2's surface; the
-- P5-A1 wire shape is array-on-routine so the editor can save the
-- whole definition in one round-trip and the scheduler can read it
-- without a join (the per-trigger child table lands alongside the
-- claim-queue work).

CREATE TABLE IF NOT EXISTS routines (
    id                    TEXT         PRIMARY KEY,
    tenant_id             TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    owner_user_id         TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    project_id            TEXT,                                              -- nullable; no FK until Projects ships
    name                  TEXT         NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    instructions          TEXT         NOT NULL CHECK (char_length(instructions) <= 16384),
    agent_id              TEXT         NOT NULL,
    -- Optional pin to a specific agent version slug. NULL = re-resolve
    -- live at fire time per cross-audit §9.7 Q11.
    agent_version_pin     TEXT,
    -- Trigger array (cron/event/webhook). The state-machine + filter
    -- routes consume this; per-trigger CRUD endpoints (routines-prd
    -- §4.2) land in P5-A2 alongside the scheduler. JSONB so the in-memory
    -- + postgres adapters share the shape.
    triggers              JSONB        NOT NULL DEFAULT '[]'::jsonb,
    -- Per-routine connector scope override. Sparse: missing keys mean
    -- "inherit owner default at fire time" (P5-A4 intersection check).
    connectors_scope      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    -- Behaviour knobs (autonomy, retries, output target). Opaque JSON
    -- on P5-A1; richer schema constraints land alongside the
    -- intersection check.
    behavior              JSONB        NOT NULL DEFAULT '{}'::jsonb,
    -- Permissions JSON; the only mandatory key today is `manual_fire`,
    -- enforced at the service layer via the routines.ts wire shape.
    -- cross-audit §9.7 Q2.
    permissions           JSONB        NOT NULL DEFAULT '{"manual_fire": "owner"}'::jsonb,
    -- Code-routines wire shape (Wave 6 executor; cross-audit §9.7 Q1).
    -- Persisted today behind the routines-prd §16 feature flag; the
    -- executor pipeline ships in Wave 6.
    code                  JSONB,
    status                TEXT         NOT NULL DEFAULT 'draft' CHECK (
        status IN ('draft','active','paused','errored')
    ),
    -- Populated iff status='paused'; the CHECK below validates that
    -- (pause_reason set IFF status='paused' or status='errored' — the
    -- two reachable "needs attention" states for the rail badge).
    pause_reason          TEXT         CHECK (
        pause_reason IS NULL
        OR pause_reason IN ('manual','permission_shrinkage','error')
    ),
    missed_fire_policy    TEXT         NOT NULL DEFAULT 'fire_once' CHECK (
        missed_fire_policy IN ('fire_once','fire_all','skip')
    ),
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Soft-delete marker; cleanup job in P5-A2 hard-deletes after the
    -- retention window (routines-prd §5.3).
    deleted_at            TIMESTAMPTZ,
    CONSTRAINT routines_pause_reason_invariant CHECK (
        (pause_reason IS NULL AND status NOT IN ('paused','errored'))
        OR (pause_reason IS NOT NULL AND status IN ('paused','errored'))
        OR (status = 'paused' AND pause_reason IS NULL)
        -- `status='paused' AND pause_reason IS NULL` is allowed for
        -- legacy rows; the service layer always stamps a reason on
        -- new pauses. The intent here is "pause_reason cannot be set
        -- on active/draft" — the third arm relaxes the first arm so
        -- transitional rows survive.
    )
);

-- Hot path: per-owner active queue, newest first. Matches the list
-- endpoint default sort (created_at DESC, id) for cursor pagination.
CREATE INDEX IF NOT EXISTS routines_owner_status_idx
    ON routines (tenant_id, owner_user_id, status, created_at DESC)
    WHERE deleted_at IS NULL;

-- Project-scoped reads (cross-audit §1.3 — project-member visibility).
CREATE INDEX IF NOT EXISTS routines_tenant_project_idx
    ON routines (tenant_id, project_id, created_at DESC)
    WHERE deleted_at IS NULL AND project_id IS NOT NULL;

-- Quota gate: per-user count of ACTIVE routines (cross-audit §9.7 Q8
-- — 100 active per USER, not per tenant). Partial UNIQUE doesn't fit
-- (we want a COUNT, not a UNIQUE), so this is a covering b-tree the
-- quota check uses for `COUNT(*) WHERE owner_user_id = ? AND status =
-- 'active'`.
CREATE INDEX IF NOT EXISTS routines_active_owner_quota_idx
    ON routines (tenant_id, owner_user_id)
    WHERE status = 'active' AND deleted_at IS NULL;

-- Reverse-link probe: routines pointing at agent X (audit / agent
-- deletion cascade).
CREATE INDEX IF NOT EXISTS routines_agent_idx
    ON routines (tenant_id, agent_id);

-- Tenant isolation via RLS — matches the policy on every product
-- table.
ALTER TABLE routines ENABLE ROW LEVEL SECURITY;

CREATE POLICY routines_tenant_isolation ON routines
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- ---------------------------------------------------------------------------
-- Routine fires — lightweight metadata for each manual / scheduler /
-- webhook fire. The actual run record lives in ai-backend via
-- ``run.source.kind = "routine"`` (cross-audit §9.7 token-usage rule);
-- this table stores only the dispatch metadata so the routine's "last
-- fire" rail can render without a cross-service join on the hot path.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS routine_fires (
    id              TEXT         PRIMARY KEY,
    tenant_id       TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    routine_id      TEXT         NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
    trigger_kind    TEXT         NOT NULL CHECK (
        trigger_kind IN ('cron','event','webhook','manual')
    ),
    fired_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Source attribution. ``source_ip`` is populated for webhook fires
    -- so the IP allowlist audit trail survives the response; cron
    -- fires set NULL.
    source_ip       TEXT,
    -- Inbound payload snapshot (webhook body, event envelope). NULL
    -- for cron / manual fires. Capped at 256 KB at the route layer.
    source_payload  JSONB,
    -- Cross-service handoff: the ai-backend run id. NULL until P5-A2
    -- wires the run-coordinator (today the API stamps it on the
    -- response but doesn't persist it back; that lands with the
    -- scheduler so retries don't duplicate runs).
    run_id          TEXT,
    -- Status mirrors the run state machine for the "last fire" rail.
    status          TEXT         NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued','running','succeeded','failed','skipped')
    )
);

CREATE INDEX IF NOT EXISTS routine_fires_routine_idx
    ON routine_fires (tenant_id, routine_id, fired_at DESC);

CREATE INDEX IF NOT EXISTS routine_fires_status_idx
    ON routine_fires (tenant_id, status, fired_at DESC);

ALTER TABLE routine_fires ENABLE ROW LEVEL SECURITY;

CREATE POLICY routine_fires_tenant_isolation ON routine_fires
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- ---------------------------------------------------------------------------
-- Audit events — append-only; status transitions, manual fires,
-- routine CRUD all funnel through this table.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS routine_audit_events (
    audit_id            TEXT         PRIMARY KEY,
    tenant_id           TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE RESTRICT,
    actor_user_id       TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    -- Dotted action taxonomy per routines-prd §6.1:
    --   routine.created / routine.updated / routine.deleted
    --   routine.activated / routine.paused / routine.errored
    --   routine.manual_fired / routine.auto_paused
    action              TEXT         NOT NULL,
    target_kind         TEXT         NOT NULL DEFAULT 'routine',
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

CREATE INDEX IF NOT EXISTS routine_audit_tenant_idx
    ON routine_audit_events (tenant_id, ts DESC);

CREATE INDEX IF NOT EXISTS routine_audit_target_idx
    ON routine_audit_events (tenant_id, target_id, ts);

CREATE INDEX IF NOT EXISTS routine_audit_correlation_idx
    ON routine_audit_events (correlation_id)
    WHERE correlation_id IS NOT NULL;

ALTER TABLE routine_audit_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY routine_audit_tenant_isolation ON routine_audit_events
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON routines TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON routine_fires TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT ON routine_audit_events TO enterprise_app';
    END IF;
END
$$;
