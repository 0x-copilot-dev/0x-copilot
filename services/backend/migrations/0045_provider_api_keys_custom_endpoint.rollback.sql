-- Rollback for 0045_provider_api_keys_custom_endpoint.sql.
--
-- Restore the four-value provider CHECK and drop the custom-endpoint columns.
-- Any ``openai_compatible`` rows must be removed first or the narrowed CHECK
-- would reject them; deleting them is correct — the feature they back is gone.

DELETE FROM provider_api_keys WHERE provider = 'openai_compatible';

ALTER TABLE provider_api_keys
    DROP CONSTRAINT IF EXISTS provider_api_keys_provider_check;

ALTER TABLE provider_api_keys
    ADD CONSTRAINT provider_api_keys_provider_check
    CHECK (provider = ANY (ARRAY[
        'openai'::text,
        'anthropic'::text,
        'google'::text,
        'openrouter'::text
    ]));

ALTER TABLE provider_api_keys
    DROP COLUMN IF EXISTS label,
    DROP COLUMN IF EXISTS base_url;
