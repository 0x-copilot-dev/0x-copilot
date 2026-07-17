-- Rollback for 0036_provider_api_keys_openrouter.sql.
--
-- Narrowing the CHECK requires the surviving rows to satisfy it first, so
-- any stored OpenRouter keys are removed before the constraint is
-- re-tightened to the original three providers.

DELETE FROM provider_api_keys WHERE provider = 'openrouter';
ALTER TABLE provider_api_keys
    DROP CONSTRAINT IF EXISTS provider_api_keys_provider_check;
ALTER TABLE provider_api_keys
    ADD CONSTRAINT provider_api_keys_provider_check
    CHECK (provider IN ('openai', 'anthropic', 'google'));
