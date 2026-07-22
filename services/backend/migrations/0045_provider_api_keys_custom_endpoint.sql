-- Decision D-2 — a real "any OpenAI-compatible endpoint" custom-provider add-flow.
--
-- Adds ONE generic provider identity ``openai_compatible`` and the two
-- user-supplied, display-safe columns that back it: ``base_url`` (the endpoint
-- the runtime routes to) and ``label`` (a human name like "My vLLM"). Both are
-- NULL for the four native providers, so existing rows and clients are
-- untouched. Neither is key material — the api key stays TokenVault-encrypted
-- in ``encrypted_key`` exactly as before.
--
-- The provider CHECK is widened from four values to five. Re-creating the
-- constraint is the only way to extend an ``ANY (ARRAY[...])`` CHECK; the
-- (org_id, user_id, provider) primary key is unchanged, so a user still holds
-- at most one custom endpoint (single-endpoint MVP).

ALTER TABLE provider_api_keys
    ADD COLUMN IF NOT EXISTS base_url text,
    ADD COLUMN IF NOT EXISTS label text;

ALTER TABLE provider_api_keys
    DROP CONSTRAINT IF EXISTS provider_api_keys_provider_check;

ALTER TABLE provider_api_keys
    ADD CONSTRAINT provider_api_keys_provider_check
    CHECK (provider = ANY (ARRAY[
        'openai'::text,
        'anthropic'::text,
        'google'::text,
        'openrouter'::text,
        'openai_compatible'::text
    ]));
