DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'
    ) THEN
        EXECUTE 'REVOKE SELECT ON pg_stat_statements FROM enterprise_app';
    END IF;
END $$;
