-- Rollback 0010_usage_attribution_edges: remove immutable usage attribution.

DROP TABLE IF EXISTS runtime_usage_attribution_edges;
ALTER TABLE runtime_model_call_usage
    DROP CONSTRAINT IF EXISTS runtime_model_call_usage_org_id_id_key;
