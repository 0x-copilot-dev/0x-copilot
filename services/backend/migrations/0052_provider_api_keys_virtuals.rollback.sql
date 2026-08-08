-- Rollback for 0052_provider_api_keys_virtuals.sql.
--
-- Restore the five-value provider CHECK. Any ``virtuals`` rows must be removed
-- first or the narrowed CHECK would reject them; deleting them is correct — the
-- provider they authenticate is gone, and the key material is an opaque
-- TokenVault envelope that nothing else references.

DELETE FROM provider_api_keys WHERE provider = 'virtuals';

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
