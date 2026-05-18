-- =========================================================================
-- Library destination — Phase 7 P7-A1 schema (metadata + CRUD only).
--
-- Source: docs/atlas-new-design/destinations/library-prd.md §5.1 / §5.2.
-- Cross-audit §1.3 binding: tenant-first indexing on every table;
-- project_id is a filing axis whose ACL is resolved via the canonical
-- backend_app.projects.acl helper (no per-destination membership
-- duplication). Soft-delete via deleted_at; retention sweeps in
-- jobs/library_retention.py (P7-A2+).
--
-- Out of scope here (other Phase 7 tickets):
--   - library_embeddings, library_page_versions — P7-A3 / P7-A2.
--   - library_pins, library_access_log, library_citations — P7-A2+.
--   - tsvector generated columns — P7-A3 owns the text-extraction
--     pipeline (library-prd §6); P7-A1 uses application-side filters
--     for the dev/in-memory store, and the Postgres deployment adds
--     the generated columns when the indexer ships.
-- =========================================================================

-- =========================================================================
-- library_files — file metadata + opaque blob_ref to object store.
--
-- Bytes live in the object store; this row carries metadata only.
-- ``blob_ref`` is opaque to clients — preview / download routes return
-- signed GET URLs with a short TTL (library-prd §7.4 binding). No
-- cleartext object-store URLs in audit rows.
-- =========================================================================

CREATE TABLE IF NOT EXISTS library_files (
    id                    uuid PRIMARY KEY,
    tenant_id             uuid NOT NULL,
    owner_user_id         uuid NOT NULL,
    project_id            uuid NULL,
    -- file_kind ∈ {doc, image, pdf, sheet, slide, other} — derived from
    -- mime at write time so the FE picks an icon without parsing.
    file_kind             text NOT NULL,
    name                  text NOT NULL CHECK (char_length(name) <= 200),
    mime                  text NOT NULL,
    size_bytes            bigint NOT NULL DEFAULT 0,
    blob_ref              text NOT NULL,
    thumbnail_blob_ref    text NULL,
    -- ``source`` is the discriminated union from api-types/library.ts:
    -- {kind: "user_upload" | "agent_save" | "connector_sync", ...}.
    source                jsonb NOT NULL,
    tags                  text[] NOT NULL DEFAULT '{}',
    -- index_status ∈ {pending, indexing, indexed, failed, skipped}.
    index_status          text NOT NULL DEFAULT 'pending',
    index_error           text NULL,
    checksum_sha256       text NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    last_accessed_at      timestamptz NULL,
    deleted_at            timestamptz NULL
);

-- Default list-view sort: most-recently-updated first, tenant-scoped,
-- live rows only. The composite key is the index walk for the
-- destination's "All" view.
CREATE INDEX IF NOT EXISTS library_files_tenant_updated_idx
    ON library_files (tenant_id, updated_at DESC)
    WHERE deleted_at IS NULL;

-- Owner-scoped reads (caller's own items).
CREATE INDEX IF NOT EXISTS library_files_owner_idx
    ON library_files (tenant_id, owner_user_id, updated_at DESC)
    WHERE deleted_at IS NULL;

-- Project-scoped reads (cross-audit §1.3 — project-member access).
CREATE INDEX IF NOT EXISTS library_files_project_idx
    ON library_files (tenant_id, project_id, updated_at DESC)
    WHERE project_id IS NOT NULL AND deleted_at IS NULL;

-- Indexer worker poll — partial index keeps the hot path narrow.
CREATE INDEX IF NOT EXISTS library_files_index_status_idx
    ON library_files (tenant_id, index_status)
    WHERE index_status IN ('pending', 'indexing', 'failed');

-- Retention sweep cursor.
CREATE INDEX IF NOT EXISTS library_files_deleted_at_idx
    ON library_files (tenant_id, deleted_at)
    WHERE deleted_at IS NOT NULL;


-- =========================================================================
-- library_pages — markdown page (knowledge card). Body is canonical
-- content in this row; up to 1 MB enforced at write time.
--
-- ``version`` + ``version_etag`` support optimistic concurrency on
-- body edits — the service rotates the etag on every successful save
-- and appends a row to ``library_page_versions`` (the versions table
-- ships in P7-A2 alongside the history surface).
-- =========================================================================

CREATE TABLE IF NOT EXISTS library_pages (
    id                    uuid PRIMARY KEY,
    tenant_id             uuid NOT NULL,
    owner_user_id         uuid NOT NULL,
    project_id            uuid NULL,
    title                 text NOT NULL CHECK (char_length(title) <= 200),
    markdown              text NOT NULL CHECK (octet_length(markdown) <= 1048576),
    version               int NOT NULL DEFAULT 1,
    version_etag          text NOT NULL,
    source                jsonb NOT NULL,
    tags                  text[] NOT NULL DEFAULT '{}',
    index_status          text NOT NULL DEFAULT 'pending',
    index_error           text NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    last_accessed_at      timestamptz NULL,
    deleted_at            timestamptz NULL
);

CREATE INDEX IF NOT EXISTS library_pages_tenant_updated_idx
    ON library_pages (tenant_id, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS library_pages_owner_idx
    ON library_pages (tenant_id, owner_user_id, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS library_pages_project_idx
    ON library_pages (tenant_id, project_id, updated_at DESC)
    WHERE project_id IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS library_pages_index_status_idx
    ON library_pages (tenant_id, index_status)
    WHERE index_status IN ('pending', 'indexing', 'failed');

CREATE INDEX IF NOT EXISTS library_pages_deleted_at_idx
    ON library_pages (tenant_id, deleted_at)
    WHERE deleted_at IS NOT NULL;


-- =========================================================================
-- library_datasets — tabular data with a schema. Bytes (Parquet / CSV /
-- JSONL) in object store; this row carries metadata + schema only.
-- row_count + size_bytes populated post-finalize by the indexer.
-- =========================================================================

CREATE TABLE IF NOT EXISTS library_datasets (
    id                    uuid PRIMARY KEY,
    tenant_id             uuid NOT NULL,
    owner_user_id         uuid NOT NULL,
    project_id            uuid NULL,
    name                  text NOT NULL CHECK (char_length(name) <= 200),
    description           text NULL,
    -- ``schema_json`` is an array of {name, type, nullable, sample_values?}
    -- (matches LibraryDatasetColumnSpec[] on the wire). JSONB on disk;
    -- the wire field is named ``schema`` — the renaming is a Python
    -- shadow guard since ``schema`` collides with pydantic's reserved
    -- attribute name on the model. Storage column stays ``schema_json``
    -- to make that boundary explicit in SQL too.
    schema_json           jsonb NOT NULL DEFAULT '[]'::jsonb,
    row_count             bigint NOT NULL DEFAULT 0,
    size_bytes            bigint NOT NULL DEFAULT 0,
    blob_ref              text NOT NULL,
    -- format ∈ {parquet, csv, jsonl}; canonical = parquet.
    format                text NOT NULL DEFAULT 'parquet',
    source                jsonb NOT NULL,
    tags                  text[] NOT NULL DEFAULT '{}',
    index_status          text NOT NULL DEFAULT 'pending',
    index_error           text NULL,
    checksum_sha256       text NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    last_accessed_at      timestamptz NULL,
    deleted_at            timestamptz NULL
);

CREATE INDEX IF NOT EXISTS library_datasets_tenant_updated_idx
    ON library_datasets (tenant_id, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS library_datasets_owner_idx
    ON library_datasets (tenant_id, owner_user_id, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS library_datasets_project_idx
    ON library_datasets (tenant_id, project_id, updated_at DESC)
    WHERE project_id IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS library_datasets_index_status_idx
    ON library_datasets (tenant_id, index_status)
    WHERE index_status IN ('pending', 'indexing', 'failed');

CREATE INDEX IF NOT EXISTS library_datasets_deleted_at_idx
    ON library_datasets (tenant_id, deleted_at)
    WHERE deleted_at IS NOT NULL;


-- =========================================================================
-- library_audit_events — append-only audit trail per Library item.
--
-- Same pattern as projects_audit_events / inbox_audit_events: the
-- packages/audit-chain signer + chain verifier sit in front of this
-- table in production. P7-A1 lands the table; the chain wiring is the
-- deployment composer's job.
-- =========================================================================

CREATE TABLE IF NOT EXISTS library_audit_events (
    audit_id              uuid PRIMARY KEY,
    tenant_id             uuid NOT NULL,
    actor_user_id         uuid NOT NULL,
    -- action ∈ {library.file_created, library.file_updated,
    --           library.file_deleted, library.page_created,
    --           library.page_updated, library.page_deleted,
    --           library.dataset_created, library.dataset_updated,
    --           library.dataset_deleted, ...}
    action                text NOT NULL,
    target_kind           text NOT NULL,  -- library_file | library_page | library_dataset
    target_id             uuid NOT NULL,
    -- before_state / after_state are redacted server-side for page
    -- markdown (library-prd §7.4 sensitive-field handling) — the body
    -- is replaced with a content-hash + length stub.
    before_state          jsonb NULL,
    after_state           jsonb NULL,
    context               jsonb NULL,  -- cross-audit §1.4 — what + why
    correlation_id        uuid NULL,
    ts                    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS library_audit_tenant_ts_idx
    ON library_audit_events (tenant_id, ts DESC);

CREATE INDEX IF NOT EXISTS library_audit_target_idx
    ON library_audit_events (tenant_id, target_id, ts DESC);
