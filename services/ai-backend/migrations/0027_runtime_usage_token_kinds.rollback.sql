-- Rollback for 0027: drop the four new token-kind columns.
--
-- Old code keeps reading the columns it knows (input/output/cached/total)
-- so the rollback is safe at the schema layer. Any rows written between
-- the forward migration and the rollback lose their reasoning /
-- cache_creation / audio totals — acceptable because new code is
-- additive over old behavior; old code never read those columns.

ALTER TABLE runtime_model_call_usage
    DROP COLUMN IF EXISTS reasoning_tokens,
    DROP COLUMN IF EXISTS cache_creation_input_tokens,
    DROP COLUMN IF EXISTS audio_input_tokens,
    DROP COLUMN IF EXISTS audio_output_tokens;

ALTER TABLE runtime_run_usage
    DROP COLUMN IF EXISTS reasoning_tokens,
    DROP COLUMN IF EXISTS cache_creation_input_tokens,
    DROP COLUMN IF EXISTS audio_input_tokens,
    DROP COLUMN IF EXISTS audio_output_tokens;
