-- Rollback for 0031_adapter_registry.sql.

DROP TABLE IF EXISTS adapter_registry_audit_events;
DROP TABLE IF EXISTS tenant_adapter_settings;
DROP TABLE IF EXISTS promoted_adapters;
DROP TABLE IF EXISTS adapter_reviews;
DROP TABLE IF EXISTS adapter_candidates;
