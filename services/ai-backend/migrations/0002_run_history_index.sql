-- transactional: false
-- 0002_run_history_index — org+user run-history keyset index (PRD-05).
--
-- The org-scoped run-history read (list_runs_for_org, GET /v1/agent/runs) filters
-- on (org_id, user_id) and orders by (created_at DESC, id DESC). Neither existing
-- agent_runs index leads with (org_id, user_id): idx_agent_runs_org_conversation_created
-- leads with conversation_id, idx_agent_runs_org_status_started with status. This
-- covering-order index drives that keyset scan.
--
-- CONCURRENTLY so a live deployment can build the index without locking writes to
-- agent_runs; it therefore cannot run inside a transaction — the `transactional:
-- false` directive above tells the yoyo runner to apply this migration outside a
-- transaction block.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agent_runs_org_user_created
    ON agent_runs USING btree (org_id, user_id, created_at DESC, id DESC);
