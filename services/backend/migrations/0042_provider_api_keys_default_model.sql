-- PRD-F PR-F.5 — project a default-model chip onto the provider-key summary.
--
-- The model chosen for a BYOK provider key was seeded from workspace defaults
-- on the host; the cleaner single-source form is a server-projected
-- ``default_model`` on the key summary. Persist that pick alongside the key so
-- the summary can carry it. Display-safe slug only — NEVER key material, so no
-- encryption and no CHECK on the value. ADDITIVE + nullable: existing rows and
-- older clients that never send a model keep ``NULL`` and the legacy shape.

ALTER TABLE provider_api_keys
    ADD COLUMN IF NOT EXISTS default_model text;
