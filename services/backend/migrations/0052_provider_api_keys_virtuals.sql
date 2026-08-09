-- Virtuals compute as a native BYOK provider.
--
-- Widens the provider CHECK from five values to six. Virtuals is a fixed-endpoint
-- gateway (compute.virtuals.io/v1) like ``openrouter``, NOT a user-supplied
-- endpoint like ``openai_compatible`` — so it never populates ``base_url`` or
-- ``label``, and this migration adds no columns.
--
-- Re-creating the constraint is the only way to extend an ``ANY (ARRAY[...])``
-- CHECK. The (org_id, user_id, provider) primary key is unchanged, so a user
-- still holds at most one key per provider.

ALTER TABLE provider_api_keys
    DROP CONSTRAINT IF EXISTS provider_api_keys_provider_check;

ALTER TABLE provider_api_keys
    ADD CONSTRAINT provider_api_keys_provider_check
    CHECK (provider = ANY (ARRAY[
        'openai'::text,
        'anthropic'::text,
        'google'::text,
        'openrouter'::text,
        'openai_compatible'::text,
        'virtuals'::text
    ]));
