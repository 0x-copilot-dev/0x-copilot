-- PR 4.1 — Settings → "You" group: per-user profile + opinion-only preferences.
--
-- Two sidecars to ``users`` so the identity table stays small and SCIM-only.
--
-- ``user_profiles``: queryable presentation columns (title, timezone,
-- locale, working_hours, avatar_url) the admin members directory and the
-- working-hours-aware notification senders care about.
--
-- ``user_preferences``: opinion-only blob (theme/accent/density/reduce-motion,
-- shortcut overrides, notification matrix) that evolves faster than schema
-- and is never queried by predicate. JSONB keeps the read O(1) and lets
-- future top-level keys ship without a migration.
--
-- Both rows are per-user (PK = user_id, ON DELETE CASCADE from users) and
-- carry org_id denormalised for RLS uniformity with agent_conversations
-- and friends. RLS policy mirrors 0008_rls_tenant_isolation.sql:
-- ``app.current_org_id`` controls visibility; the staged Stage 3 ENABLE
-- ROW LEVEL SECURITY ALTER picks up these tables alongside the existing
-- list. Until then RLS is dormant — same as every other 0004..0017 table.

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id          TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    org_id           TEXT NOT NULL REFERENCES organizations(org_id),
    title            TEXT,
    timezone         TEXT,                                  -- IANA tz id, validated server-side
    locale           TEXT,                                  -- BCP-47 tag, validated server-side
    working_hours    JSONB,                                 -- { tz, start: 'HH:MM', end: 'HH:MM', days: [int] }
    avatar_url       TEXT,                                  -- URL only (file upload pipeline is a follow-up PR)
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_profiles_org
    ON user_profiles (org_id);

CREATE POLICY tenant_isolation ON user_profiles
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));


CREATE TABLE IF NOT EXISTS user_preferences (
    user_id          TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    org_id           TEXT NOT NULL REFERENCES organizations(org_id),
    preferences      JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_preferences_org
    ON user_preferences (org_id);

CREATE POLICY tenant_isolation ON user_preferences
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));


-- Bring the new tables into the existing app-role grant list so the
-- runtime connection (which connects as ``enterprise_app``) can read +
-- write. Idempotent: GRANT is silently a no-op when already held.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON user_profiles TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON user_preferences TO enterprise_app';
    END IF;
END
$$;
