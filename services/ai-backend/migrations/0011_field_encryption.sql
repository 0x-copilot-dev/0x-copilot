-- C7 phase 1: schema for envelope-encrypted PII columns.
--
-- ``encryption_version=0`` means plaintext; ``=1`` means the column contains
-- a ``v1:<wrapped_dek>:<iv>:<ciphertext+tag>`` envelope produced by
-- ``EnvelopeFieldEncryption``. This PR ships the schema + adapter + read
-- tolerance for both versions; the writes-flip and the backfill happen as
-- separate operator-driven phases per docs/security/field-encryption.md.
--
-- Targeted columns (per the C7 spec):
--   agent_messages.content_text, content_json, metadata_json
--   runtime_audit_log.metadata_json_redacted
--   runtime_events.payload_json_redacted, metadata_json_redacted
--   runtime_subagent_results.response_text
--   runtime_tool_invocations.args_json_redacted, result_summary_json_redacted
--   runtime_memory_items.content_summary
--   runtime_context_payloads — split out into runtime_context_payload_blobs
--
-- Excluded by design (queryability or no PII): ids, FKs, timestamps,
-- status enums, indexed columns used in WHERE clauses.

ALTER TABLE agent_messages
    ADD COLUMN IF NOT EXISTS encryption_version SMALLINT NOT NULL DEFAULT 0;

ALTER TABLE runtime_audit_log
    ADD COLUMN IF NOT EXISTS encryption_version SMALLINT NOT NULL DEFAULT 0;

ALTER TABLE runtime_events
    ADD COLUMN IF NOT EXISTS encryption_version SMALLINT NOT NULL DEFAULT 0;

ALTER TABLE runtime_subagent_results
    ADD COLUMN IF NOT EXISTS encryption_version SMALLINT NOT NULL DEFAULT 0;

ALTER TABLE runtime_tool_invocations
    ADD COLUMN IF NOT EXISTS encryption_version SMALLINT NOT NULL DEFAULT 0;

ALTER TABLE runtime_memory_items
    ADD COLUMN IF NOT EXISTS encryption_version SMALLINT NOT NULL DEFAULT 0;

-- Encrypted blobs that exceed PG row size practicality (multi-KB context
-- payloads) live in a sidecar table; the parent ``runtime_context_payloads``
-- row stays intact for queryability.
CREATE TABLE IF NOT EXISTS runtime_context_payload_blobs (
    id                  TEXT PRIMARY KEY,
    payload_id          TEXT NOT NULL REFERENCES runtime_context_payloads(id) ON DELETE CASCADE,
    org_id              TEXT NOT NULL,
    encrypted_blob      BYTEA NOT NULL,
    encryption_version  SMALLINT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_runtime_context_payload_blobs_payload
    ON runtime_context_payload_blobs (payload_id);

-- Tenant-scope index for RLS-friendly lookups (matches the policy installed
-- in 0008 for sibling tables).
CREATE INDEX IF NOT EXISTS idx_runtime_context_payload_blobs_org
    ON runtime_context_payload_blobs (org_id);
