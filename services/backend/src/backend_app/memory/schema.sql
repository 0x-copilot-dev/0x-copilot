-- =========================================================================
-- Memory destination — Phase 12 P12-A3 schema.
--
-- Source: docs/atlas-new-design/destinations/team-memory-cmdk-prd.md
--   §3.2 (wire shapes), §5.2 (new tables), §5.3 (retention), §6.2 (ACL).
--
-- Two tables: ``memory_items`` (the durable knowledge rows the runtime
-- reads via Purpose.MEMORY_RETRIEVAL) and ``memory_proposals`` (the
-- auto-extracted accept/reject queue surfaced as toasts + the
-- /v1/memory/proposals feed).
--
-- Embeddings are NOT in their own table — memory rows ride
-- ``library_embeddings`` with ``target_kind = 'memory'`` (sub-PRD §5.1;
-- DRY check is "no memory_embeddings table"). The indexer worker that
-- ships in the Library tree handles target_kind=memory unchanged once
-- P12-A3's enqueue path lands; Library's ``library_index_jobs`` queue is
-- the single source of truth for in-flight embedding work.
--
-- Idempotent — all CREATE TABLE / INDEX statements use IF NOT EXISTS so
-- the schema can be replayed safely during a deploy or a unit test
-- migration sweep.
-- =========================================================================


-- =========================================================================
-- memory_items — the canonical knowledge rows.
--
-- Sub-PRD §5.2:
--   (id, tenant_id, scope, kind, title, body, tags, created_by,
--    last_used_at, project_id, created_at, updated_at, deleted_at).
--
-- ``scope`` ∈ {user, workspace}; ``kind`` ∈ {skill, fact, preference}.
-- ``created_by`` is the JSONB envelope from the wire (kind + id, where
-- id is a UserId or an AgentId — kept as text on disk; the wire layer
-- casts at the trust boundary).
--
-- Soft-delete via ``deleted_at`` with a 90-day grace per sub-PRD §5.3.
-- Cascade to ``library_embeddings`` (where ``target_kind='memory'``)
-- happens in the retention sweep job that lives alongside the existing
-- library retention sweep — out of scope for P12-A3.
-- =========================================================================

CREATE TABLE IF NOT EXISTS memory_items (
    id                    uuid PRIMARY KEY,
    tenant_id             uuid NOT NULL,
    -- Owner — the user the row belongs to. For scope='workspace' the
    -- owner is the creator; reads fan out to any tenant member.
    owner_user_id         uuid NOT NULL,
    -- scope ∈ {user, workspace} (sub-PRD §3.2). Index axis on the
    -- default list view.
    scope                 text NOT NULL CHECK (scope IN ('user', 'workspace')),
    -- kind ∈ {skill, fact, preference} (sub-PRD §3.2).
    kind                  text NOT NULL CHECK (kind IN ('skill', 'fact', 'preference')),
    title                 text NOT NULL CHECK (char_length(title) <= 200),
    -- Body is markdown; cap at 16 KB (memory rows are short by design;
    -- the editor's body field is single-textarea per sub-PRD §7.2).
    body                  text NOT NULL CHECK (octet_length(body) <= 16384) DEFAULT '',
    tags                  text[] NOT NULL DEFAULT '{}',
    -- created_by ∈ {"kind": "user"|"agent", "id": "<UserId|AgentId>"}.
    -- JSONB to mirror the wire shape verbatim (sub-PRD §3.2).
    created_by            jsonb NOT NULL,
    last_used_at          timestamptz NULL,
    project_id            uuid NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    deleted_at            timestamptz NULL
);

-- Default list-view sort: most-recently-updated first, tenant-scoped,
-- live rows only. The sub-PRD §4.2 list endpoint defaults to this sort
-- when ``last_used_at`` is null (the cold-row case).
CREATE INDEX IF NOT EXISTS memory_items_tenant_updated_idx
    ON memory_items (tenant_id, updated_at DESC)
    WHERE deleted_at IS NULL;

-- Owner-scoped reads (scope='user'); the dominant access path.
CREATE INDEX IF NOT EXISTS memory_items_owner_idx
    ON memory_items (tenant_id, owner_user_id, updated_at DESC)
    WHERE deleted_at IS NULL;

-- Workspace-scoped reads — tenant fan-out (sub-PRD §6.2).
CREATE INDEX IF NOT EXISTS memory_items_workspace_idx
    ON memory_items (tenant_id, scope, updated_at DESC)
    WHERE scope = 'workspace' AND deleted_at IS NULL;

-- Project-scoped reads (cross-audit §1.3 — project-member access).
CREATE INDEX IF NOT EXISTS memory_items_project_idx
    ON memory_items (tenant_id, project_id, updated_at DESC)
    WHERE project_id IS NOT NULL AND deleted_at IS NULL;

-- last_used_desc sort (sub-PRD §4.2 — default sort token).
CREATE INDEX IF NOT EXISTS memory_items_last_used_idx
    ON memory_items (tenant_id, last_used_at DESC NULLS LAST)
    WHERE deleted_at IS NULL;

-- Retention sweep cursor.
CREATE INDEX IF NOT EXISTS memory_items_deleted_at_idx
    ON memory_items (tenant_id, deleted_at)
    WHERE deleted_at IS NOT NULL;


-- =========================================================================
-- memory_proposals — pending auto-extraction queue.
--
-- Sub-PRD §5.2 + §9.1: the post-run extractor writes one row per proposed
-- memory. ``status`` lifecycle: pending → accepted | rejected | snoozed.
-- Terminal rows are hard-deleted 30 days past ``decided_at`` (sub-PRD §5.3).
-- =========================================================================

CREATE TABLE IF NOT EXISTS memory_proposals (
    id                    uuid PRIMARY KEY,
    tenant_id             uuid NOT NULL,
    -- Owner — the user the originating chat/run belonged to.
    user_id               uuid NOT NULL,
    -- status ∈ {pending, accepted, rejected, snoozed}.
    status                text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'rejected', 'snoozed')),
    proposed_at           timestamptz NOT NULL DEFAULT now(),
    proposed_kind         text NOT NULL CHECK (proposed_kind IN ('skill', 'fact', 'preference')),
    proposed_title        text NOT NULL CHECK (char_length(proposed_title) <= 200),
    proposed_body         text NOT NULL CHECK (octet_length(proposed_body) <= 16384) DEFAULT '',
    -- ``source`` is the wire ItemRef ({"kind": "...", "id": "..."}).
    -- JSONB on disk to keep the wire shape verbatim.
    source                jsonb NOT NULL,
    decided_at            timestamptz NULL,
    -- The accepted MemoryItem id (NULL until accept).
    accepted_memory_id    uuid NULL
);

-- Pending feed (the FE renders this directly on /memory/proposals).
CREATE INDEX IF NOT EXISTS memory_proposals_pending_idx
    ON memory_proposals (tenant_id, user_id, proposed_at DESC)
    WHERE status = 'pending';

-- Retention sweep — find terminal rows 30+ days past decided_at.
CREATE INDEX IF NOT EXISTS memory_proposals_decided_idx
    ON memory_proposals (tenant_id, decided_at)
    WHERE status IN ('accepted', 'rejected');


-- =========================================================================
-- memory_audit_events — append-only audit trail per memory row.
--
-- Same pattern as library_audit_events + projects_audit_events: the
-- packages/audit-chain signer + chain verifier sit in front of this
-- table in production. P12-A3 lands the table; the chain wiring is the
-- deployment composer's job.
-- =========================================================================

CREATE TABLE IF NOT EXISTS memory_audit_events (
    audit_id              uuid PRIMARY KEY,
    tenant_id             uuid NOT NULL,
    actor_user_id         uuid NOT NULL,
    -- action ∈ {memory.created, memory.updated, memory.scope_changed,
    --           memory.deleted, memory.touched,
    --           memory.proposal_accepted, memory.proposal_rejected}.
    action                text NOT NULL,
    target_kind           text NOT NULL,  -- memory_item | memory_proposal
    target_id             uuid NOT NULL,
    before_state          jsonb NULL,
    after_state           jsonb NULL,
    correlation_id        uuid NULL,
    ts                    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS memory_audit_tenant_ts_idx
    ON memory_audit_events (tenant_id, ts DESC);

CREATE INDEX IF NOT EXISTS memory_audit_target_idx
    ON memory_audit_events (tenant_id, target_id, ts DESC);
