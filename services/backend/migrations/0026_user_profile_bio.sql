-- PR 8.2 — bio column on the user_profiles sidecar.
--
-- Single TEXT column. Length cap is enforced server-side (≤ 600 chars) so
-- the rollback is trivial and we can iterate on the limit without a DDL.
-- No backfill required: NULL means "no bio" everywhere it surfaces.

ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS bio TEXT;
