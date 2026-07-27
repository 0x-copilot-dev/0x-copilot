-- 0024_effect_claim_row_outcomes — exact row-set attempt identity and results.
--
-- Both columns contain bounded, body-free review metadata. `row_keys` is part
-- of claim identity; `row_results` is immutable terminal outcome data. No
-- connector arguments, provider bodies, credentials, or physical paths enter
-- this table.

ALTER TABLE runtime_effect_claims
    ADD COLUMN IF NOT EXISTS row_keys jsonb,
    ADD COLUMN IF NOT EXISTS row_results jsonb;

ALTER TABLE runtime_effect_claims
    ADD CONSTRAINT runtime_effect_claims_row_keys_array_check
        CHECK (row_keys IS NULL OR jsonb_typeof(row_keys) = 'array'),
    ADD CONSTRAINT runtime_effect_claims_row_results_array_check
        CHECK (row_results IS NULL OR jsonb_typeof(row_results) = 'array');
