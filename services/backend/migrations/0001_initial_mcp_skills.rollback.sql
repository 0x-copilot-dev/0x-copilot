-- Rollback for 0001_initial_mcp_skills. Use CASCADE so FK ordering is implicit.
DROP TABLE IF EXISTS skill_audit_events CASCADE;
DROP TABLE IF EXISTS skills CASCADE;
DROP TABLE IF EXISTS mcp_audit_events CASCADE;
DROP TABLE IF EXISTS mcp_auth_connections CASCADE;
DROP TABLE IF EXISTS mcp_auth_sessions CASCADE;
DROP TABLE IF EXISTS mcp_servers CASCADE;
