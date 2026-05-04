DROP INDEX IF EXISTS idx_account_lockouts_locked_at;
DROP INDEX IF EXISTS idx_account_lockouts_auto_unlock;
DROP INDEX IF EXISTS idx_account_lockouts_active;
DROP TABLE IF EXISTS lockout_policies;
DROP TABLE IF EXISTS account_lockouts;
