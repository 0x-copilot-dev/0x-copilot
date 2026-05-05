-- PR 1.4.1 — chain_depth column on runtime_approval_requests.
--
-- PR 1.4 stored the parent link via ``chain_parent_approval_id`` and
-- inferred depth at runtime as "0 if null, 1 otherwise". With cap = 3
-- the practical cap was 2 — cap > inferred-depth always evaluated true.
-- This migration persists depth as a column set on insert
-- (``parent.chain_depth + 1``); the runtime guard becomes O(1) and the
-- cap honors 3 exactly.
--
-- The CHECK aligns with ``RuntimeApiService.APPROVAL_FORWARD_MAX_CHAIN_DEPTH``;
-- raising the cap means changing both. A unit test asserts the coupled
-- invariant.

ALTER TABLE runtime_approval_requests
    ADD COLUMN IF NOT EXISTS chain_depth INTEGER NOT NULL DEFAULT 0
        CHECK (chain_depth >= 0 AND chain_depth <= 3);

-- Backfill via a recursive CTE: depth = 0 for root rows; depth = parent.depth + 1
-- for descendants. The WHERE clause skips already-backfilled rows so a
-- partial migration apply is safely idempotent on rerun.
WITH RECURSIVE chain AS (
    SELECT id, 0 AS depth
      FROM runtime_approval_requests
     WHERE chain_parent_approval_id IS NULL
    UNION ALL
    SELECT child.id, parent.depth + 1
      FROM runtime_approval_requests child
      JOIN chain parent ON child.chain_parent_approval_id = parent.id
)
UPDATE runtime_approval_requests AS r
   SET chain_depth = chain.depth
  FROM chain
 WHERE r.id = chain.id AND r.chain_depth = 0;
