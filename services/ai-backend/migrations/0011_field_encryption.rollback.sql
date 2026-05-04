DROP INDEX IF EXISTS idx_runtime_context_payload_blobs_org;
DROP INDEX IF EXISTS idx_runtime_context_payload_blobs_payload;
DROP TABLE IF EXISTS runtime_context_payload_blobs;

ALTER TABLE runtime_memory_items DROP COLUMN IF EXISTS encryption_version;
ALTER TABLE runtime_tool_invocations DROP COLUMN IF EXISTS encryption_version;
ALTER TABLE runtime_subagent_results DROP COLUMN IF EXISTS encryption_version;
ALTER TABLE runtime_events DROP COLUMN IF EXISTS encryption_version;
ALTER TABLE runtime_audit_log DROP COLUMN IF EXISTS encryption_version;
ALTER TABLE agent_messages DROP COLUMN IF EXISTS encryption_version;
