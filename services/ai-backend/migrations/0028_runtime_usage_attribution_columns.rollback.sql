-- Rollback for 0028: drop the three attribution columns.
--
-- Old code keeps reading the columns it knows. Any rows written
-- between the forward migration and the rollback lose their purpose /
-- originating_tool_* values — acceptable because the carry mechanism
-- is additive; old code never read these columns.

ALTER TABLE runtime_model_call_usage
    DROP COLUMN IF EXISTS purpose,
    DROP COLUMN IF EXISTS originating_tool_call_id,
    DROP COLUMN IF EXISTS originating_tool_name;
