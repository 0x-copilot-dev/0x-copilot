-- PR 8.3 — split api_keys into personal vs workspace tokens.
--
-- Workspace keys are admin-issued tokens with workspace-wide scopes.
-- They still have a ``user_id`` (the admin who minted them) for audit
-- attribution; ``kind`` distinguishes them from per-user personal keys
-- so the Settings UI lists them under the right tab and the directory
-- API filters correctly.

ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'personal'
    CHECK (kind IN ('personal', 'workspace'));

CREATE INDEX IF NOT EXISTS idx_api_keys_workspace
    ON api_keys (org_id) WHERE revoked_at IS NULL AND kind = 'workspace';
