-- C11: grant SELECT on pg_stat_statements to enterprise_app.
--
-- The extension is assumed pre-installed by the operator (most managed
-- Postgres ships it). When it isn't, the grant is silently skipped and
-- the C11 scraper logs once + exits — the feature is opt-in.
--
-- We do NOT ``CREATE EXTENSION`` here because that requires superuser
-- on most managed Postgres flavors; we'd rather fail-soft than fail the
-- whole migration when the extension isn't available.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_available_extensions WHERE name = 'pg_stat_statements'
    ) AND EXISTS (
        SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'
    ) THEN
        EXECUTE 'GRANT SELECT ON pg_stat_statements TO enterprise_app';
    END IF;
END $$;
