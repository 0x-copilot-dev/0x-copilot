DROP POLICY IF EXISTS tenant_isolation ON runtime_citations;
DROP INDEX IF EXISTS runtime_citations_org_idx;
DROP INDEX IF EXISTS runtime_citations_conv_idx;
DROP INDEX IF EXISTS runtime_citations_run_source_uk;
DROP TABLE IF EXISTS runtime_citations;
