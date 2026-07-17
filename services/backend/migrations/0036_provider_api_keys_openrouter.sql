-- BYOK — add OpenRouter to the provider_api_keys CHECK (Round 1).
--
-- OpenRouter is an OpenAI-wire-compatible gateway; the runtime routes it
-- through ChatOpenAI with a fixed base_url (https://openrouter.ai/api/v1)
-- and the Responses API disabled. Widening a CHECK only adds an allowed
-- value, so existing rows always satisfy it — no data backfill needed.
--
-- Mirrors the enum change in backend_app/provider_keys/store.py
-- (ProviderName.OPENROUTER); the two must move together (see 0034's note).

ALTER TABLE provider_api_keys
    DROP CONSTRAINT IF EXISTS provider_api_keys_provider_check;
ALTER TABLE provider_api_keys
    ADD CONSTRAINT provider_api_keys_provider_check
    CHECK (provider IN ('openai', 'anthropic', 'google', 'openrouter'));
