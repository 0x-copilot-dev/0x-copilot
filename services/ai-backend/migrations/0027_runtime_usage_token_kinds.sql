-- Sub-PRD 01a: normalized token kinds on usage rows.
--
-- Adds four token kinds the in-line extractor previously dropped:
--   * reasoning_tokens             (OpenAI o-series, Anthropic extended thinking)
--   * cache_creation_input_tokens  (Anthropic prompt-cache write)
--   * audio_input_tokens           (OpenAI Responses voice input)
--   * audio_output_tokens          (OpenAI Responses voice output)
--
-- input_tokens stays the GROSS input figure (regular + cached + cache_creation);
-- cached_input_tokens and cache_creation_input_tokens are SUBSETS billed at
-- their own rates. P12 pricing math operates on these columns directly.
--
-- Migration safety:
--   * NOT NULL DEFAULT 0 is a metadata-only change on PG 11+ — no table
--     rewrite, no lock beyond access-exclusive briefly.
--   * Pre-existing rows take 0 for the new kinds. Rollup math sums new
--     columns; 0 + 0 = 0 for old rows so existing reports are unaffected.

ALTER TABLE runtime_model_call_usage
    ADD COLUMN IF NOT EXISTS reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS audio_input_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS audio_output_tokens INTEGER NOT NULL DEFAULT 0;

ALTER TABLE runtime_run_usage
    ADD COLUMN IF NOT EXISTS reasoning_tokens BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cache_creation_input_tokens BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS audio_input_tokens BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS audio_output_tokens BIGINT NOT NULL DEFAULT 0;
