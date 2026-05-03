# Spec: Runtime worker queue command validation

**Related:** [11-persistence-org-scoping-audit.md](11-persistence-org-scoping-audit.md), deployment plan `04-runtime-worker-tenant-validation.md`.

## Requirements

Queued commands (`RuntimeRunCommand`, `RuntimeCancelCommand`, `RuntimeApprovalResolvedCommand`) carry `org_id` and identifiers produced when the API **trusted identity** created the run. Workers must not execute side effects if payload fields disagree with **authoritative persisted rows** fetched by `(org_id, …)` keys.

## Implemented behavior

- **`RuntimeRunHandler`:** After `get_run(org_id, run_id)`, rejects commands whose `conversation_id` or `user_id` does not match the persisted run (forged outbox payload defense).
- **`RuntimeCancelHandler`:** Loads the run with `get_run(org_id, run_id)` before cancelling; **no-ops** when `requested_by_user_id` ≠ persisted run `user_id`; avoids `update_run_status` keyed only by `run_id` without org verification.
- **`RuntimeApprovalHandler`:** Ensures `approval.run_id` matches `command.run_id` after loading the approval by `(org_id, approval_id)`.

## Tests

See `tests/unit/runtime_worker/test_worker_command_integrity.py`.
