-- PR B2: per-workspace + per-user privacy & data settings.
--
-- Five toggles + one knob:
--   * training_opt_out   — provider do-not-train signal on every call.
--   * region             — data residency (us-east-1 / eu-west-1 /
--                          ap-northeast-1). Null = deployment default.
--   * retention_days     — auto-delete after N days; null = forever.
--                          The retention sweeper reads this column at
--                          run start and bakes it into runtime
--                          retention_policies (existing C8 pipeline).
--   * share_metadata     — opt-in to admin-visible thread metadata
--                          (title, model, approvals); message content
--                          stays private regardless.
--   * memory_enabled     — toggle Atlas's cross-chat memory feature.
--
-- Same workspace-vs-user-override pattern as tool_use_policies:
--   user_id IS NULL  → workspace default
--   user_id IS NOT NULL → user override

CREATE TABLE IF NOT EXISTS privacy_settings (
    org_id            TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    user_id           TEXT,
    training_opt_out  BOOLEAN      NOT NULL DEFAULT TRUE,
    region            TEXT         CHECK (region IN ('us-east-1', 'eu-west-1', 'ap-northeast-1')),
    retention_days    INTEGER      CHECK (retention_days IS NULL OR retention_days > 0),
    share_metadata    BOOLEAN      NOT NULL DEFAULT TRUE,
    memory_enabled    BOOLEAN      NOT NULL DEFAULT TRUE,
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by_user_id TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_privacy_settings_scope
    ON privacy_settings (org_id, COALESCE(user_id, '__org__'));

ALTER TABLE privacy_settings ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON privacy_settings
    USING (org_id = current_setting('app.current_org', true));
