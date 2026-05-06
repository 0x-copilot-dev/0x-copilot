-- PR 8.3 — server-stored avatar bytes (replaces the inline data: URL pipeline).
--
-- One row per user, ≤ 200 KB after FE-side resize to 256x256 JPEG/PNG/WEBP.
-- ``etag`` is a sha256 of the bytes used by the GET route's ETag header so
-- browsers cache cleanly across versions; ``updated_at`` drives the cache-
-- busting ``?v=`` query the FE writes into ``user_profiles.avatar_url``.
--
-- Keeping bytes in Postgres rather than introducing object storage is a
-- Phase-3 trade-off: small, predictable blob; same RLS posture as every
-- other tenant-scoped table; future S3 swap replaces the AvatarStore
-- adapter without touching routes or the FE.

CREATE TABLE IF NOT EXISTS user_avatars (
    user_id      TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    org_id       TEXT NOT NULL REFERENCES organizations(org_id),
    content_type TEXT NOT NULL CHECK (content_type IN (
        'image/png','image/jpeg','image/webp'
    )),
    bytes        BYTEA NOT NULL,
    size_bytes   INTEGER NOT NULL CHECK (size_bytes BETWEEN 1 AND 204800),
    etag         TEXT NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_avatars_org ON user_avatars (org_id);

ALTER TABLE user_avatars ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON user_avatars
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON user_avatars TO enterprise_app';
    END IF;
END
$$;
