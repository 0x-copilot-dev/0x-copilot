-- ===========================================================================
-- ⌘K palette destination — denormalized search index.
--
-- Source: docs/atlas-new-design/destinations/team-memory-cmdk-prd.md
--   §5.2 (new tables — palette_index).
--
-- One row per searchable entity across every destination (chats /
-- projects / library / agents / tools / connectors / people / memories /
-- routines). Refreshed by per-destination LISTEN/NOTIFY hooks (see
-- backend_app/palette/refresh.py) — destinations do NOT write to this
-- table directly except through the canonical dispatcher.
--
-- Indexes:
--   * GIN on tsv for BM25-style lexical search (Postgres ts_rank_cd).
--   * IVFFLAT on embedding for hybrid vector recall (Phase 7.5 infra
--     reused; Purpose.PALETTE_RANKING at the embed-side).
--   * B-tree on (tenant_id, entity_kind, updated_at desc) for the
--     "recent-by-kind" projection the palette uses as a fallback when
--     the query is empty.
--
-- Retention: stale rows (entity_kind/entity_id no longer exists in the
-- owning destination) are garbage-collected nightly (sub-PRD §5.3).
-- The retention job lives outside this DDL.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS palette_index (
    tenant_id      TEXT NOT NULL,
    entity_kind    TEXT NOT NULL,          -- chat | project | library_item | agent | tool | connector | person | memory | routine
    entity_id      TEXT NOT NULL,
    title          TEXT NOT NULL,
    body           TEXT NOT NULL DEFAULT '',
    tags           TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    route          TEXT NOT NULL,          -- canonical route (e.g. /library/{id})
    owner_user_id  TEXT,                   -- for owner-only ACL filtering (memory user-scope, etc.)
    project_id     TEXT,                   -- for project-membership ACL filtering
    tsv            TSVECTOR NOT NULL,
    embedding      VECTOR(1536),           -- nullable; embedding may be queued
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, entity_kind, entity_id)
);

CREATE INDEX IF NOT EXISTS palette_index_tsv_gin
    ON palette_index USING GIN (tsv);

-- IVFFLAT requires a populated table to be useful; we still create it
-- so the production composer can REINDEX when the table fills.
CREATE INDEX IF NOT EXISTS palette_index_embedding_ivf
    ON palette_index USING IVFFLAT (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS palette_index_recent_by_kind
    ON palette_index (tenant_id, entity_kind, updated_at DESC);
