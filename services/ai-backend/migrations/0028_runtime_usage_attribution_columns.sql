-- Sub-PRD 01b: attribution columns on per-LLM-call usage rows.
--
-- Adds three columns the in-line streaming executor populates from a
-- typed UsageAttributionContext built at emit time:
--
--   * purpose                  TEXT NOT NULL DEFAULT 'main'
--     The Purpose enum string: 'main' | 'tool_planning' |
--     'tool_interpretation' | 'subagent_work' | 'context_compression'.
--     Default keeps pre-migration rows + any code path that doesn't
--     build a context in the safe bucket.
--   * originating_tool_call_id TEXT NULL
--   * originating_tool_name    TEXT NULL
--     Carry from the ToolCallLedger's pending-attribution pop. Only
--     tool_interpretation / tool_planning rows populate these; other
--     purposes leave them NULL.
--
-- Note: ``subagent_id`` and ``connector_slug`` columns already exist
-- on this table. 01b changes how they're populated (chunk namespace +
-- ledger instead of a never-populated SQL heuristic) but not the
-- column shape. No schema change for those.
--
-- Migration safety: NOT NULL DEFAULT 'main' is a metadata-only change
-- on PG 11+. Pre-existing rows take 'main' for ``purpose``.

ALTER TABLE runtime_model_call_usage
    ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT 'main',
    ADD COLUMN IF NOT EXISTS originating_tool_call_id TEXT,
    ADD COLUMN IF NOT EXISTS originating_tool_name TEXT;
