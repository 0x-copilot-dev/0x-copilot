ALTER TABLE runtime_effect_claims
    DROP CONSTRAINT IF EXISTS runtime_effect_claims_row_results_array_check,
    DROP CONSTRAINT IF EXISTS runtime_effect_claims_row_keys_array_check,
    DROP COLUMN IF EXISTS row_results,
    DROP COLUMN IF EXISTS row_keys;
