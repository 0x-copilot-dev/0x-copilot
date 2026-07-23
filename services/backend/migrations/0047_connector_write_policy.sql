-- PRD-C1 (Generative Surfaces v2) — Per-connector write policy: the
-- approval-POSTURE control's durable column.
--
-- Adds the per-connector agent *write policy* (ask_first / allow_always) as
-- a durable column on the existing `connectors` denormalized read model
-- (added in 0044_connectors.sql; access_mode added in
-- 0046_connector_access_mode.sql).
--
-- This is a DISTINCT axis from `access_mode`:
--
--   * access_mode  (read | read_act | off)   — the CAPABILITY axis: what the
--                                               agent may do with the connector.
--   * write_policy (ask_first | allow_always) — the APPROVAL-POSTURE axis: for
--                                               a WRITE the agent is otherwise
--                                               allowed to make, does it hold
--                                               for approval (ask_first) or run
--                                               without asking (allow_always)?
--
-- The two never fold together. `write_policy` is an OVERRIDE on top of the
-- global Settings → Model & behavior → Approval Policy: NULL = no override
-- (fall back to the global policy). Only the two explicit values are legal;
-- the runtime composes NULL → "defer to global".
--
-- The module mirror (src/backend_app/connectors/schema.sql) is kept
-- byte-for-semantics identical so fresh installs and migrated installs
-- converge (the 0043_projects incident: a migration/mirror divergence
-- shipped a 500 on fresh installs).

ALTER TABLE connectors
    ADD COLUMN IF NOT EXISTS write_policy TEXT NULL CHECK (write_policy IN ('ask_first', 'allow_always'));
