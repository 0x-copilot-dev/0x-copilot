-- Rollback 0006_stage_commit_ledger: drop the v2 CommitEngine idempotency ledger.
-- Reverses the additive CREATE TABLE exactly.

DROP TABLE IF EXISTS runtime_stage_commit_ledger;
