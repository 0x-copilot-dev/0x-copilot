-- Rollback for 0026_conversation_tool_ordinals.sql.
DROP POLICY IF EXISTS tenant_isolation ON agent_conversation_tool_ordinals;
DROP INDEX IF EXISTS idx_actio_conversation_run;
DROP TABLE IF EXISTS agent_conversation_tool_ordinals;
