# Decomp — `runtime_adapters/in_memory/runtime_api_store.py`

Source: [services/ai-backend/src/runtime_adapters/in_memory/runtime_api_store.py](../../../services/ai-backend/src/runtime_adapters/in_memory/runtime_api_store.py) — **944 LOC, XL.** Single class `InMemoryRuntimeApiStore`. Implements every persistence + event-store + queue port using nested Python dicts/lists, deterministic enough for tests to assert directly against the internal state. Acts as the **reference implementation** that the postgres adapter must match behaviorally.

## A. Top-level structure

### Module shell (lines 1–50)

No top-level functions, no module constants. Only imports, including the central inner-class import:

- `runtime_adapters.base` provides `RuntimeAdapterHelpers`, `StatusTransition`, and `_Fields` (line 25–29). `_Fields` is the **shared payload-key pool** that both adapters use; `StatusTransition` is the shared status→timestamp + terminal-status set.
- `agent_runtime.observability.audit_chain.AuditChainSigner` (13) — HMAC chain primitive.

### Class `InMemoryRuntimeApiStore` (53–944)

| Symbol                                                                                   |   Lines | Purpose                                                                                                                                                                                           |
| ---------------------------------------------------------------------------------------- | ------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `__init__`                                                                               |   56–89 | Initialise 17 in-memory containers + audit-chain signer + idempotency maps.                                                                                                                       |
| `create_conversation(request)`                                                           |  91–115 | Create or idempotently return a scoped conversation.                                                                                                                                              |
| `get_conversation(*, org_id, user_id, conversation_id)`                                  | 117–131 | Return a conversation only when org+user scope match.                                                                                                                                             |
| `list_conversations(*, org_id, user_id, limit, include_archived=False)`                  | 133–158 | Scoped list ordered by `updated_at DESC`.                                                                                                                                                         |
| `list_messages(*, org_id, conversation_id, limit, include_deleted=False)`                | 160–177 | Ordered by `created_at ASC`.                                                                                                                                                                      |
| `append_message(message)`                                                                | 179–188 | Append + bump conversation `updated_at`.                                                                                                                                                          |
| `create_run_with_user_message(*, request, conversation)`                                 | 190–253 | Create run+message records, or return idempotent prior run. Returns `(run, message, created_bool)`.                                                                                               |
| `_latest_message_id(org_id, conversation_id)`                                            | 255–267 | Most recent non-deleted message by `created_at DESC`.                                                                                                                                             |
| `_find_latest_assistant_for_run(org_id, conversation_id, run_id)`                        | 269–285 | Latest non-deleted ASSISTANT message for a given run.                                                                                                                                             |
| `get_run(*, org_id, run_id)`                                                             | 287–293 | Org-scoped run lookup.                                                                                                                                                                            |
| `update_run_status(*, run_id, status)`                                                   | 295–304 | Apply `StatusTransition.timestamp_updates`.                                                                                                                                                       |
| `set_run_latest_sequence(*, run_id, latest_sequence_no)`                                 | 306–315 | Persist latest event sequence_no on the run row.                                                                                                                                                  |
| `record_approval_decision(*, record)`                                                    | 317–329 | Persist decision + flip request status.                                                                                                                                                           |
| `create_approval_request(*, record)`                                                     | 331–347 | Idempotent on `approval_id`; normalises `risk_level` via `RuntimeAdapterHelpers.normalize_risk_class`.                                                                                            |
| `get_approval_request(*, org_id, approval_id)`                                           | 349–360 | Org-scoped approval lookup.                                                                                                                                                                       |
| `write_audit_log(*, event_type, record)`                                                 | 362–373 | Append HMAC-chain-signed audit record to `self.audit_log`.                                                                                                                                        |
| `_sign_audit_record(*, event_type, record)`                                              | 375–391 | Compute `seq` + `prev_hash` + `signature` + `key_version`; advance per-org head.                                                                                                                  |
| static `_audit_signing_payload(*, event_type, record)`                                   | 393–412 | Strip chain fields, inject `__event_type__`, return signable dict.                                                                                                                                |
| `delete_user_history(*, org_id, user_id, reason=None)`                                   | 414–495 | Tombstone user-visible history (archive convs, tombstone messages, cancel non-terminal runs); preserve audit + event evidence.                                                                    |
| `record_run_usage(record)`                                                               | 499–504 | Idempotent on `run_id`; second write is a no-op.                                                                                                                                                  |
| `record_model_call_usage(record)`                                                        | 506–507 | Append-only list.                                                                                                                                                                                 |
| `update_run_usage_cost(*, run_id, cost_micro_usd, pricing_id, pricing_version)`          | 509–526 | Stamp cost on existing usage row.                                                                                                                                                                 |
| `update_model_call_usage_cost(*, usage_id, cost_micro_usd, pricing_id, pricing_version)` | 528–545 | Stamp cost on existing per-call row.                                                                                                                                                              |
| `upsert_pricing(record)`                                                                 | 547–562 | **Closes** the prior active row for the same `(provider, model, region)` triple by setting its `effective_until` to the new row's `effective_from`; preserves the partial unique index semantics. |
| `lookup_pricing(*, provider, model_name, region, at)`                                    | 564–583 | Pick the row whose `[effective_from, effective_until)` window contains `at`; ties broken by latest `effective_from`.                                                                              |
| `list_runs_missing_cost(*, limit, cursor=None)`                                          | 585–597 | Sorted by `id` ASC; cursor-paginated. For the cost-backfill loop.                                                                                                                                 |
| `upsert_user_daily_usage(row)`                                                           | 599–607 | Key: `(org, user, day, provider, model)`.                                                                                                                                                         |
| `upsert_org_daily_usage(row)`                                                            | 609–616 | Key: `(org, day, provider, model)`.                                                                                                                                                               |
| `query_user_daily_usage(*, org_id, user_id, start_day, end_day)`                         | 618–638 | Day-DESC, inclusive range.                                                                                                                                                                        |
| `query_org_daily_usage(*, org_id, start_day, end_day)`                                   | 640–657 | Day-DESC, inclusive range.                                                                                                                                                                        |
| `query_run_usage(*, org_id, run_id)`                                                     | 659–668 | Org-scoped single-row read.                                                                                                                                                                       |
| `query_run_usage_for_range(*, org_id, user_id, start, end)`                              | 670–691 | `org_id`/`user_id` optional; PII-purged rows are excluded **only when `user_id` is provided**.                                                                                                    |
| `query_top_conversations(*, org_id, user_id, start, end, limit)`                         | 693–715 | Group by conversation_id, sum total_tokens, return top-N.                                                                                                                                         |
| `query_model_call_usage_for_run(*, org_id, run_id)`                                      | 717–727 | Org+run filter on the per-call list.                                                                                                                                                              |
| `append_event(event)`                                                                    | 729–761 | Append with `sequence_no = len(events) + 1`. Computes `activity_kind` via `RuntimeEventPresentationProjector.activity_kind_for` if not provided.                                                  |
| `list_events_after(*, org_id, run_id, after_sequence)`                                   | 763–779 | Replay-after-cursor; returns `()` if run not in org.                                                                                                                                              |
| `get_latest_sequence(*, run_id)`                                                         | 781–784 | `len(events)` for the run.                                                                                                                                                                        |
| `enqueue_run(command)`                                                                   | 786–797 | Append to `run_commands` list **and** register command in outbox.                                                                                                                                 |
| `enqueue_cancel(command)`                                                                | 799–810 | Same for cancel.                                                                                                                                                                                  |
| `enqueue_approval_resolved(command)`                                                     | 812–825 | Same for approval-resolved (carries `approval_id`).                                                                                                                                               |
| `claim_next(*, worker_id, lock_expires_at)`                                              | 827–853 | Iterate `_queue_order`; skip `COMPLETED`/`DEAD_LETTER`, future `available_at`, and unexpired claims; return first eligible.                                                                       |
| `mark_complete(*, result)`                                                               | 855–859 | Set `OutboxStatus.COMPLETED`, drop claim.                                                                                                                                                         |
| `mark_retry(*, result)`                                                                  | 861–868 | Set `RETRY`, set `available_at` (default now), drop claim.                                                                                                                                        |
| `mark_dead_letter(*, result)`                                                            | 870–874 | Set `DEAD_LETTER`, drop claim.                                                                                                                                                                    |
| `seed_approval_request(record)`                                                          | 876–882 | Test-fixture seed.                                                                                                                                                                                |
| `_ensure_run_idempotency_match(*, key, request)`                                         | 884–898 | Validate request fingerprint matches stored `(conversation_id, user_input)`; raise `IDEMPOTENCY_CONFLICT` (HTTP 409).                                                                             |
| `_register_command(*, command_id, command_type, org_id, run_id, approval_id, payload)`   | 900–921 | Append to queue_order, store payload, mark `PENDING`, attempts=0, available now.                                                                                                                  |
| `_claim_command(*, command_id, worker_id, lock_expires_at)`                              | 923–944 | Increment attempts, build `RuntimeWorkerClaim`.                                                                                                                                                   |

### Internal state (init at lines 56–89)

| Container                                                | Type                                       | Purpose                                            |
| -------------------------------------------------------- | ------------------------------------------ | -------------------------------------------------- |
| `conversations`                                          | `dict[str, ConversationRecord]`            | Conversation by id.                                |
| `messages`                                               | `dict[str, MessageRecord]`                 | Message by id.                                     |
| `runs`                                                   | `dict[str, RunRecord]`                     | Run by id.                                         |
| `approval_requests`                                      | `dict[str, ApprovalRequestRecord]`         | Approval request by id.                            |
| `approval_decisions`                                     | `dict[str, ApprovalDecisionRecord]`        | Approval decision by id.                           |
| `events_by_run`                                          | `dict[str, list[RuntimeEventEnvelope]]`    | Append-only per-run event list.                    |
| `run_commands` / `cancel_commands` / `approval_commands` | `list[…]`                                  | Per-type command lists for direct test inspection. |
| `_queue_order`                                           | `list[str]`                                | Outbox ordering.                                   |
| `_queue_payloads`                                        | `dict[str, dict[str, object]]`             | Outbox payload by command_id.                      |
| `_queue_statuses`                                        | `dict[str, OutboxStatus]`                  | Outbox status by command_id.                       |
| `_queue_attempts`                                        | `dict[str, int]`                           | Attempt counter per command.                       |
| `_queue_available_at`                                    | `dict[str, datetime]`                      | Earliest claim time.                               |
| `_queue_claims`                                          | `dict[str, RuntimeWorkerClaim]`            | Active claim per command.                          |
| `audit_log`                                              | `list[tuple[str, dict[str, object]]]`      | Append-only signed audit records.                  |
| `_audit_chain_signer`                                    | `AuditChainSigner`                         | Loaded from env.                                   |
| `_audit_chain_heads_by_org`                              | `dict[str, bytes]`                         | Per-org chain head.                                |
| `_audit_chain_counts_by_org`                             | `dict[str, int]`                           | Per-org chain seq.                                 |
| `_conversation_idempotency`                              | `dict[(org, user, key), conversation_id]`  | Conversation idempotency map.                      |
| `_run_idempotency`                                       | `dict[(org, user, key), run_id]`           | Run idempotency map.                               |
| `_run_idempotency_fingerprint`                           | `dict[(org, user, key), (conv_id, input)]` | Idempotency fingerprint.                           |
| `run_usage`                                              | `dict[run_id, RuntimeRunUsageRecord]`      | B1 per-run usage.                                  |
| `model_call_usage`                                       | `list[RuntimeModelCallUsageRecord]`        | B2 per-call usage.                                 |
| `pricing_rows`                                           | `list[ModelPricingRecord]`                 | B3 pricing.                                        |
| `user_daily_usage` / `org_daily_usage`                   | `dict[…]`                                  | B4 daily rollups.                                  |

## B. Feature inventory

| Domain                                        | Symbols                                                                                                                    |  LOC |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ---: |
| **Conversation CRUD**                         | `create_conversation`, `get_conversation`, `list_conversations`                                                            |  ~68 |
| **Message CRUD**                              | `list_messages`, `append_message`, `_latest_message_id`, `_find_latest_assistant_for_run`                                  |  ~50 |
| **Run lifecycle CRUD + idempotency**          | `create_run_with_user_message`, `get_run`, `update_run_status`, `set_run_latest_sequence`, `_ensure_run_idempotency_match` |  ~90 |
| **Approval CRUD**                             | `record_approval_decision`, `create_approval_request`, `get_approval_request`, `seed_approval_request`                     |  ~40 |
| **Audit chain (HMAC hash chain)**             | `write_audit_log`, `_sign_audit_record`, `_audit_signing_payload`                                                          |  ~50 |
| **User-history deletion (right-to-erasure)**  | `delete_user_history`                                                                                                      |  ~82 |
| **Usage + pricing (B1/B2/B3/B4)**             | 14 methods, lines 497–727                                                                                                  | ~230 |
| **Event store (append + replay)**             | `append_event`, `list_events_after`, `get_latest_sequence`                                                                 |  ~55 |
| **Outbox / queue (enqueue + claim + settle)** | `enqueue_*`, `claim_next`, `mark_*`, `_register_command`, `_claim_command`                                                 | ~155 |

## C. Functional spec per domain

### Conversation CRUD

**Idempotency:** `(org_id, user_id, idempotency_key)` triple → conversation_id (lines 96–100, 111–114). Second-call returns the prior conversation **without** updating any field.
**Tenant-isolation guards:** `get_conversation` returns `None` for cross-tenant or cross-user access (129–130).
**Validation:** None at this layer (relies on Pydantic).

### Message CRUD

**Side effects:** `append_message` bumps the parent conversation's `updated_at` to `message.created_at` (184–187).
**Sort order:** `list_messages` is `created_at ASC`; `_latest_message_id` and `_find_latest_assistant_for_run` are `created_at DESC max()`.
**Filtering:** `include_deleted=False` (default) excludes rows with `deleted_at IS NOT NULL` (175–176).

### Run lifecycle + idempotency

**`create_run_with_user_message`** (190–253) is the most complex method.

Inputs: `CreateRunRequest`, `ConversationRecord`. Output: `(RunRecord, MessageRecord, created_bool)`.

Validation rules:

- `request.runtime_context` must be non-None or `RuntimeApiError(VALIDATION_ERROR, 400, retryable=False)` (199–205).

Idempotency flow:

1. If `idempotency_key`: form key `(org_id, user_id, key)` (208).
2. If existing run for that key: validate fingerprint via `_ensure_run_idempotency_match` (211); return prior run + its user message + `created=False`.
3. Else: build user_message via `RuntimeAdapterHelpers.message_for_run_request` (218–225) (delegates message-id reuse + parent-message resolution to base helpers).
4. Build new RunRecord with `run_id` from `runtime_context.run_id` (227); store run, store message (only if not already present — line 239), bump conversation `updated_at`, init events list, store idempotency mapping + fingerprint `(conversation_id, user_input)`.

Idempotency conflict (`_ensure_run_idempotency_match`, 884–898): same `(org, user, key)` but different `(conversation_id, user_input)` → `RuntimeApiError(VALIDATION_ERROR, 409 CONFLICT)` with `Messages.Error.IDEMPOTENCY_CONFLICT`.

**`update_run_status`** (295–304): delegates to `StatusTransition.timestamp_updates(status, already_started=run.started_at is not None)` for the started_at/completed_at/cancelled_at logic. Pure write; no concurrency check (callers are expected to use `with_optimistic_retry` if needed in production).

**Tenant isolation:** `get_run` returns None for cross-org access (291–292).

### Approval CRUD

**`create_approval_request`** (331–347): idempotent on `approval_id` — second call returns the existing record. Normalises `risk_level` in metadata via `RuntimeAdapterHelpers.normalize_risk_class` before storing (342–345).
**`record_approval_decision`** (317–329): atomically persists decision and flips the parent request's `status` to match.

### Audit chain (HMAC hash chain)

**Algorithm (`_sign_audit_record`, 375–391):**

1. `org_id = record["org_id"]` (or `"unknown"`).
2. `prev_hash = chain_heads_by_org[org_id]` (None for the first row).
3. `payload = _audit_signing_payload(event_type, record)` — strips chain fields (`seq`, `prev_hash`, `signature`, `key_version`) and injects `__event_type__`.
4. `sig = signer.sign(prev_hash, payload)`.
5. `seq = counts_by_org[org_id] + 1`.
6. Update head + count.
7. Return `record + {seq, prev_hash.hex() | None, signature.hex(), key_version}`.

**Tamper-evidence invariants:**

- Chain is **per-(audit_log, org_id)**.
- Signed payload **excludes** chain fields (so the signature is independent of itself).
- Datetime values flow through the canonicalizer as ISO 8601 (delegated to `AuditChainSigner`).

### User-history deletion

**`delete_user_history`** (414–495) — five-stage tombstone:

1. Find all conversation_ids where `org_id` + `user_id` match (424–428).
2. Archive each non-archived conversation: `status=ARCHIVED, archived_at=now, updated_at=now` (430–440); count incremented only when the conversation wasn't already archived.
3. Tombstone each non-deleted message in those conversations: `status=DELETED, content_text="[deleted by user request]", deleted_at=now` (442–457). Audit trail preserved (other fields remain).
4. Cancel non-terminal runs (status not in `StatusTransition.TERMINAL_STATUSES`): `status=CANCELLED, cancelled_at=now` (459–467).
5. Count `events_retained` for org+user (469–475) — events are NOT deleted, only marked as retained for audit.
6. Write audit log entry `user_history_deleted` (476–486) with `audit_event_id = f"history_delete_{org_id}_{user_id}_{int(now.timestamp())}"`.

Returns `HistoryDeletionResponse(conversations_archived, messages_tombstoned, runs_cancelled, events_retained, audit_event_id)`.

**Hard rule visible in code:** events and audit log are **append-only** in deletion path (462–467 vs 442–457). Messages are tombstoned not removed.

### Usage + pricing (B1/B2/B3/B4)

**Idempotency:**

- `record_run_usage` is idempotent on `run_id` (502–504).
- `record_model_call_usage` is append-only — caller is responsible for non-duplication (matches B2 spec where each model call has a unique `id`).

**Pricing window logic (`upsert_pricing`, 547–562):**

- For each existing row in the same `(provider, model, region)` triple with `effective_until IS NULL` AND `effective_from < new.effective_from`: set its `effective_until = new.effective_from`. This **closes the previous active row** so the partial unique index `(provider, model, region) WHERE effective_until IS NULL` would hold in postgres.
- Append new row.

**Lookup logic (`lookup_pricing`, 564–583):**

- Filter rows: `provider==`, `model==`, `region==`, `effective_from <= at`, `effective_until is None OR effective_until > at`.
- Pick `max(effective_from)` if multiple match. Returns None if none match (cost stays NULL — matches the B3 docstring in `handlers/run.py`).

**Range-query PII guard (`query_run_usage_for_range`, 670–691):**

- When `user_id` is provided (i.e. user-scoped read), rows with `pii_purged_at is not None` are **excluded**.
- When `user_id is None` (org-only), purged rows are returned (org-aggregate doesn't leak PII).

**Top-conversations PII guard (`query_top_conversations`, 693–715):**

- Always excludes `pii_purged_at is not None` rows. User-facing query — must never surface purged conversations.

### Event store

**Sequence numbering (`append_event`, 729–761):**

- `sequence_no = len(events) + 1` — monotonic, gap-free, per run (728).
- `activity_kind` is computed from `RuntimeEventPresentationProjector.activity_kind_for(event_type, source)` when not supplied (749–753) — backend-projected so frontend doesn't derive activity from event-name prefixes.

**Replay (`list_events_after`, 763–779):** Org-scoped — returns `()` if the run isn't in the requested org. Filters `sequence_no > after_sequence`. The signature matches the postgres-backed semantics for SSE resume.

### Outbox / queue

**Enqueue path:** Each `enqueue_*` does two things — appends to a per-type list (for direct test inspection) and registers in the unified outbox via `_register_command` (790, 803, 818).

**`_register_command`** (900–921):

- `_queue_order.append(command_id)` (910) — ordering preserved.
- `_queue_payloads[command_id] = payload + {COMMAND_ID, COMMAND_TYPE, ORG_ID, RUN_ID, APPROVAL_ID}` (911–918).
- `status = OutboxStatus.PENDING`, `attempts = 0`, `available_at = now` (919–921).

**`claim_next`** (827–853):

- Iterate `_queue_order` in insertion order.
- Skip if status in `{COMPLETED, DEAD_LETTER}` (838–839).
- Skip if `available_at > now` (840–841) — backoff respected.
- Skip if active claim exists with `lock_expires_at > now` (842–844) — lease respected.
- First eligible: increment attempts, store claim, set status `CLAIMED`, return `RuntimeWorkerClaim`.

**Settlement transitions** (`mark_*`):

- `mark_complete`: `COMPLETED`, drop claim. Terminal.
- `mark_retry`: `RETRY` + `available_at = result.retry_available_at or now`, drop claim. Re-eligible after the `available_at` time.
- `mark_dead_letter`: `DEAD_LETTER`, drop claim. Terminal.

## D. Bugs / edge cases / invariants

- **Idempotency conflicts return 409** (892–898) — same key + different fingerprint = error, not silent overwrite.
- **`record_run_usage` idempotency** (502–504) — second write is a no-op, cost stamp must update via `update_run_usage_cost`. Matches the postgres `INSERT ... ON CONFLICT (run_id) DO NOTHING` semantics.
- **Pricing-row closure on upsert** (547–561) — explicit comment "preserves the partial unique index semantics". Documents the postgres invariant: at most one row per triple has `effective_until IS NULL`.
- **PII purge fence in user-scoped queries** (685–686, 707–708) — purged rows are excluded for user-facing queries, included for org-only aggregates. Two distinct privacy rules.
- **Tombstone vs delete** (442–457): messages are tombstoned (`content_text="[deleted by user request]"`, `status=DELETED`) — original metadata + ids preserved for audit.
- **Run cancel only if non-terminal** (463): runs already in `TERMINAL_STATUSES` are not touched. Prevents downgrading a `COMPLETED` run to `CANCELLED`.
- **Events retained, not deleted, in user-history deletion** (469–475): explicit invariant — audit/event evidence outlives PII.
- **Audit chain payload excludes chain fields** (393–412 docstring): "Only the canonical record fields are signed; chain fields are excluded so the signature is independent of itself." Datetimes go through canonicalizer as ISO 8601.
- **`activity_kind_for` is backend-derived** (749–753): never trust the caller's `activity_kind`; if missing, the projector decides. Aligns with [services/ai-backend/CLAUDE.md](../../../services/ai-backend/CLAUDE.md): "Backend projects events into activity_kind/display_title/summary/status for the frontend; do not derive activity types from event-name prefixes."
- **Lock expiry is honoured** (842–844): a still-locked command is skipped even if its status is technically `CLAIMED`. Lease-based stealing is the primary recovery.
- **Top-conversations query is `total_tokens`-ranked** (711–712), not cost-ranked. Worth flagging — semantic difference.
- **Conversation `updated_at` bump on message append** (185–187): conversations are sorted by `updated_at` in `list_conversations` (155–157), so this affects UI sort order.
- **`query_run_usage_for_range` allows `org_id is None`** (683): supports cross-org sweeper queries — but typically callers pass a value. Cross-org behaviour is intentional for ops scripts, not user routes.
- **`message_for_run_request` is delegated** (218–225) to `RuntimeAdapterHelpers` so postgres + in-memory share the message-creation rules.
- **Audit chain head per org** (74, 384) — chains are isolated per tenant; no global head. A second tenant's audit can't break your chain verification.

## E. Hardcoded vs configurable

### Hardcoded

- `"unknown"` org_id fallback in audit signing (378).
- Tombstone text: `"[deleted by user request]"` (454).
- Audit event id format: `f"history_delete_{org_id}_{user_id}_{int(now.timestamp())}"` (476).
- `__event_type__` literal as the inserted signing field (411).
- Daily-rollup dict keys structure: `(org, user, day_iso, provider, model)` for user; `(org, day_iso, provider, model)` for org.

### Configurable

- Audit chain signer: loaded via `AuditChainSigner.from_env()` (73) — key + key_version come from env.
- All injected — none of the records or commands have hard-coded org_id / user_id.

### From env (indirect)

- `AuditChainSigner.from_env()` reads chain key material from environment.

## F. External dependencies and coupling

### Internal

- `agent_runtime.api.constants.Messages` — error message strings.
- `agent_runtime.execution.contracts.RuntimeErrorCode` — typed error codes.
- `agent_runtime.observability.audit_chain.AuditChainSigner` — HMAC primitive.
- `agent_runtime.persistence.constants.Values as PersistenceValues` — `EventType.RUN_REQUESTED`, `RUN_CANCEL_REQUESTED`, `APPROVAL_RESOLVED`.
- `agent_runtime.persistence.records` — `ModelPricingRecord`, `OutboxStatus`, `RuntimeModelCallUsageRecord`, `RuntimeRunUsageRecord`, `RuntimeWorkerClaim`, `RuntimeWorkerResult`, `UsageDailyOrgRow`, `UsageDailyUserRow`.
- `runtime_adapters.base` — `RuntimeAdapterHelpers`, `StatusTransition`, `_Fields`. **Tight cross-adapter coupling lives here.**
- `runtime_api.http.errors.RuntimeApiError` — HTTP error type.
- `runtime_api.schemas` — every record + command type.

### Stdlib / third-party

- `starlette.status` — HTTP status constants.
- `datetime`, `collections.abc.Sequence`, `typing.Any`.
- No psycopg, no asyncio (sync-only adapter).

## G. Suggested decomposition seams

The file is already a single class implementing four distinct port protocols. Natural cuts:

1. **`conversation_store.py`** — conversations + messages + idempotency: `create_conversation`, `get_conversation`, `list_conversations`, `list_messages`, `append_message`, `_latest_message_id`, `_find_latest_assistant_for_run`, `_conversation_idempotency`. ~120 LOC.
2. **`run_store.py`** — runs + run-idempotency: `create_run_with_user_message`, `get_run`, `update_run_status`, `set_run_latest_sequence`, `_ensure_run_idempotency_match`, `_run_idempotency*`. ~120 LOC.
3. **`approval_store.py`** — approval CRUD: `record_approval_decision`, `create_approval_request`, `get_approval_request`, `seed_approval_request`. ~40 LOC.
4. **`audit_store.py`** — audit chain: `write_audit_log`, `_sign_audit_record`, `_audit_signing_payload`, `audit_log`, chain heads. ~60 LOC.
5. **`history_store.py`** — `delete_user_history`. ~82 LOC. Pure orchestration over the other stores; could move into `runtime_adapters/base.py` since the postgres adapter does the same thing.
6. **`usage_store.py`** — usage + pricing + daily rollups. ~230 LOC. Cohesive B1/B2/B3/B4 cluster — already grouped with a comment fence at line 497 (`# Usage + pricing (B1, B2, B3, B4) -----------------------------------`).
7. **`event_store.py`** — append + replay: `append_event`, `list_events_after`, `get_latest_sequence`. ~55 LOC.
8. **`outbox_store.py`** — enqueue + claim + settle. ~155 LOC. Already groups its private helpers (`_register_command`, `_claim_command`).

The `__init__` is where the seams snap visually — the 17 containers initialised there split cleanly along the cuts above. The shared `_Fields` / `StatusTransition` / `RuntimeAdapterHelpers` from `runtime_adapters/base.py` make this safe: each cut would import only what it needs from base and the postgres adapter would split along the same boundaries.

The B1/B2/B3/B4 comment fence (497) and the per-domain method clustering already in the file (Conversations → Messages → Runs → Approvals → Audit → Deletion → Usage → Events → Outbox) confirm the seam structure.
