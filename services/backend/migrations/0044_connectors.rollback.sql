-- Rollback for 0044_connectors.sql.
--
-- Policies and indexes drop with their tables.

DROP TABLE IF EXISTS connector_audit_events;

DROP TABLE IF EXISTS connectors;
