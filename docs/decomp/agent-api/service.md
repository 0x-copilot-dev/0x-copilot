# Decomp — `agent_runtime/api/service.py`

Source: [services/ai-backend/src/agent_runtime/api/service.py](../../../services/ai-backend/src/agent_runtime/api/service.py) — **743 LOC, L.** Single class `RuntimeApiService` plus one module helper. The "thin application service" between FastAPI HTTP routes and the persistence + event-store + queue ports. Every API endpoint flows through one method here. Despite the docstring "thin", it owns several non-trivial domain rules: scope/permission validation, runtime-context construction, audit emission, the `ask_a_question` wire-status translation, and the prior-run-id chain walker.

## A. Top-level structure

### Module shell (lines 1–61, 802–807)

| Symbol                            |   Lines | Purpose                                                                                |
| --------------------------------- | ------: | -------------------------------------------------------------------------------------- |
| `_display_model_name(model_name)` | 802–806 | Format `model_name` like `"gpt-4-1"` → `"GPT 4 1"`. Special-cases `gpt` for uppercase. |

### Class `RuntimeApiService` (63–800)

| Symbol                                                          |   Lines | Purpose                                                                                                                                                                                                         |
| --------------------------------------------------------------- | ------: | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Class attribute `TERMINAL_RUN_STATUSES`                         |   66–73 | Frozen set: `CANCELLED`, `COMPLETED`, `FAILED`, `TIMED_OUT`.                                                                                                                                                    |
| `__init__`                                                      |  75–102 | Adapt sync ports → async; build event producer; build `ModelPricingCatalog`.                                                                                                                                    |
| `list_models()`                                                 | 104–164 | Build the model catalog from `RuntimeSettings`; return 4 hard-coded entries (default + 3 named models).                                                                                                         |
| `create_conversation(request)`                                  | 166–182 | Persistence call + audit log.                                                                                                                                                                                   |
| `get_conversation(...)`                                         | 184–198 | Scope check + return.                                                                                                                                                                                           |
| `list_conversations(...)`                                       | 200–220 | Scoped list with `bounded_limit = clamp(limit, 1, MAX_MESSAGE_LIMIT)`.                                                                                                                                          |
| `list_messages(...)`                                            | 222–249 | Scope check on conversation, then scoped message list with bounded limit.                                                                                                                                       |
| `get_conversation_context(...)`                                 | 251–305 | **B5 spec — context-window panel.** Walks: latest run usage → per-call rows → compression events → pricing → `ConversationContextBuilder.build`. Falls back to default model when no completed runs yet.        |
| `create_run(request)`                                           | 307–371 | Build runtime context → scope check → `create_run_with_user_message` → audit + `RUN_QUEUED` event + `enqueue_run` (only if newly created) → walk prior-run chain → response.                                    |
| `delete_user_history(...)`                                      | 373–402 | Persistence call + structured audit log with counts.                                                                                                                                                            |
| `get_run(...)`                                                  | 404–410 | Scope check + return.                                                                                                                                                                                           |
| `replay_events(...)`                                            | 412–440 | Scope check + `list_events_after` + `latest_sequence_no` lookup + envelope.                                                                                                                                     |
| `cancel_run(...)`                                               | 442–510 | Scope check → permission check on `requested_by_user_id` → terminal-status fast path → status flip to `CANCELLING` + emit `RUN_CANCELLING` event + `enqueue_cancel` + audit.                                    |
| `record_approval_decision(...)`                                 | 512–607 | Scope+permission checks → persist decision → walk run for scope → emit `APPROVAL_RESOLVED` (with translated wire status) → enqueue resume command → audit.                                                      |
| classmethod `_wire_status_for(*, approval_kind, record_status)` | 609–627 | Translate `ApprovalStatus.{APPROVED, REJECTED}` → `{ANSWERED, SKIPPED}` for `ask_a_question` approvals.                                                                                                         |
| classmethod `_create_run_response(...)`                         | 629–647 | Build `CreateRunResponse` with hard-coded `stream_url` / `events_url` paths.                                                                                                                                    |
| `_prior_run_ids_for_chain(...)`                                 | 649–684 | Walk `parent_message_id` chain backwards from the user message; collect distinct prior run_ids in chain order (then reverse).                                                                                   |
| `_request_with_runtime_context(request)`                        | 686–765 | **The runtime-context builder** — the most complex helper. Resolves model config, pulls request_context, fans in trace metadata (quote, attachments, content_parts, branch info), builds `AgentRuntimeContext`. |
| `_conversation_for_scope(...)`                                  | 767–786 | 404 if conversation not in scope.                                                                                                                                                                               |
| `_run_for_scope(...)`                                           | 788–799 | 404 if run not in user scope.                                                                                                                                                                                   |

## B. Feature inventory

| Domain                                  | Symbols                                                                                                             |  LOC |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | ---: |
| **Conversation CRUD pass-through**      | `create_conversation`, `get_conversation`, `list_conversations`, `list_messages`, `_conversation_for_scope`         | ~100 |
| **Conversation context panel (B5)**     | `get_conversation_context`                                                                                          |  ~55 |
| **Model catalog**                       | `list_models` + `_display_model_name`                                                                               |  ~65 |
| **Run create + chain walk**             | `create_run`, `_create_run_response`, `_prior_run_ids_for_chain`, `_request_with_runtime_context`, `_run_for_scope` | ~210 |
| **Run lifecycle (cancel, get, replay)** | `cancel_run`, `get_run`, `replay_events`                                                                            | ~110 |
| **Approval decision routing**           | `record_approval_decision`, `_wire_status_for`                                                                      | ~120 |
| **Right-to-erasure**                    | `delete_user_history`                                                                                               |  ~30 |
| **Audit emission** (cross-cutting)      | inline in every mutation                                                                                            |  ~50 |

## C. Functional spec per domain

### Run creation (`create_run`, 307–371)

1. **Build runtime context** (310): `_request_with_runtime_context` mutates the request to attach `runtime_context`. May raise `RuntimeApiError(VALIDATION_ERROR, 400)` on invalid model selection.
2. **Validate context exists** (312–318).
3. **Scope-check conversation** (319–323): 404 if the conversation isn't in (org, user) scope.
4. **Persistence creates run+message** (324–331): returns `(run, user_message, created)` — `created=False` for idempotency hits.
5. **First-time-only side effects** (332–360): audit log, `RUN_QUEUED` event, queue enqueue. Idempotent retries skip these.
6. **Compute prior_run_ids chain** (361–366) via `_prior_run_ids_for_chain`.
7. **Return response** with `stream_url` and `events_url` baked from `run.run_id`.

### Runtime-context builder (`_request_with_runtime_context`, 686–765)

Inputs: `CreateRunRequest`. Output: `CreateRunRequest` with `runtime_context` set.

Algorithm:

1. **Resolve model config** (689–707) via `ModelConfigResolver.resolve(ModelSelection(...))`. Each `ModelSelection` field is `model.field if model is not None else None`. Catches `AgentRuntimeError` → 400 with safe message + correlation_id.
2. **Build trace_metadata** by fanning in:
   - `request_context.trace_metadata` (base, 717)
   - `"request_context"` ← `context.context` (719)
   - `"quote"` ← `request.quote_payload()` (722)
   - `"attachments"` ← `[attachment.model_dump(mode=json, exclude_none=True, exclude_defaults=True)]` (724–731)
   - `"content_parts"` ← same pattern for `request.content` (733–740)
   - `"regenerate_from_message_id"` (742–744)
   - `"source_message_id"` (745–746)
   - `"parent_message_id"` (747–748)
   - `"branch_id"` (749–750)
   - `"branch"` ← `request.branch_payload()` (752–753)
3. **Build `AgentRuntimeContext`** (754–764) from `request.user_id`, `request.org_id`, `context.roles`, `context.permission_scopes`, `context.connector_scopes`, resolved `model_profile`, `settings.execution.max_parallel_tasks`, the assembled `trace_metadata`, `context.feature_flags`.
4. **Return** `request.model_copy(update={"runtime_context": runtime_context})`.

### Cancel run (`cancel_run`, 442–510)

State machine the request can drive:

```
QUEUED / RUNNING / WAITING_FOR_APPROVAL → CANCELLING
   (writes RUN_CANCELLING event + enqueues RuntimeCancelCommand + audit)

CANCELLING → CANCELLING (no-op)
   (returns the running record, no second enqueue)

TERMINAL_RUN_STATUSES (CANCELLED/COMPLETED/FAILED/TIMED_OUT) → unchanged
   (response carries `cancel_requested_at = run.cancelled_at`)
```

**Permission rule** (453–460): `request.requested_by_user_id == user_id`. Mismatch → 403 `PERMISSION_DENIED`. Defends against authenticated user A cancelling user B's run within the same org.

**Refresh-after-status-update** (482–483): `update_run_status` returns the updated row, but a `get_run` is then issued to capture any concurrent writes — comment-free defensive read; if `refreshed` is None, fall back to the local copy.

### Approval decision (`record_approval_decision`, 512–607)

Validation gates:

1. **Approval exists** (524–530): 404 `CAPABILITY_NOT_FOUND` if missing.
2. **Approver matches scope** (531–537): 403 `PERMISSION_DENIED` if `approval.user_id != request.decided_by_user_id`.
3. **Status normalization** (538–542): explicit `if request.decision.value == APPROVED.value` ladder — only APPROVED or REJECTED are accepted.

Side effects (in order):

1. Persist `ApprovalDecisionRecord` (543–555).
2. Walk run for scope (556–560) — pull current `run` for event payload.
3. Emit `APPROVAL_RESOLVED` event (562–580) with **wire-status translation**: for `ask_a_question` approvals, surface `Answered`/`Skipped` instead of `approved`/`rejected` (see `_wire_status_for`).
4. Enqueue `RuntimeApprovalResolvedCommand` (581–589) — worker consumes this to resume the paused run.
5. Audit log (590–601).

### Wire-status translation (`_wire_status_for`, 609–627)

```python
if approval_kind == Values.ApprovalKind.ASK_A_QUESTION:
    if record_status == ApprovalStatus.APPROVED.value:
        return Values.Status.ANSWERED      # "answered"
    return Values.Status.SKIPPED            # "skipped"
return record_status  # passthrough for normal approvals
```

Comment at 569–572: "ask_a_question is a question-to-user, not a permission gate." Translates persisted vocabulary into UI-vocabulary so the chat UI doesn't render an "Approved/Rejected" badge on a question card.

### Prior-run chain walker (`_prior_run_ids_for_chain`, 649–684)

Walks `parent_message_id` from `user_message` backward, collecting distinct `run_id` values that aren't `current_run_id`.

Algorithm:

1. List up to 200 messages for the conversation (666–670).
2. Index by `message_id`.
3. Cursor = `user_message.parent_message_id`.
4. While cursor exists in index:
   - If record's `run_id` is set, isn't `current_run_id`, and isn't already seen → append.
   - Advance cursor to `record.parent_message_id`.
5. Return reversed list (oldest first).

Comment at 657–663: "The chain mirrors `RuntimeRunHandler._selected_message_chain` so the ids surfaced here match the runs whose events feed the next turn's prompt context. Surfacing them keeps debugging local — on-call can replay just the runs that shaped a given turn."

**Coupling alert**: this method MUST stay in sync with `RuntimeRunHandler._selected_message_chain`. Two parallel implementations of the parent-chain rule.

### Conversation context (`get_conversation_context`, 251–305) — B5

Returns the runtime-side data that powers the conversation context panel:

- Latest run usage (most recent completed run for the conversation).
- Per-LLM-call usage rows for that run.
- Compression events for that run.
- Pricing for `(provider, model, "global", run.completed_at)`.

Falls back to default-model "no data" state when no completed runs yet (275–284). All of the heavy lifting is in `ConversationContextBuilder.build`; this method is just orchestration.

### Audit emission (cross-cutting)

Every mutation emits a `write_audit_log(event_type, record)` call. Captured event types:

- `conversation_created` (172)
- `run_created` (333)
- `user_history_deleted` (385)
- `run_cancel_requested` (492)
- `approval_decision_recorded` (590)

Each has a structured `record` dict: `org_id`, `user_id`, `resource_type`, `resource_id`, `outcome="success"`, plus context-specific `metadata` and `run_id`/`trace_id` when relevant.

## D. Bugs / edge cases / invariants

- **Idempotency on `create_run`**: side effects (audit, RUN_QUEUED event, enqueue) **only on `created=True`** (332–360). Idempotent reuse returns the prior run without re-enqueueing.
- **Hard-coded URL paths** in `_create_run_response` (643–644): `/v1/agent/runs/{run_id}/stream` + `/events`. If the route prefix changes, this is one of two places to update.
- **`cancel_run` permission check** (453–460): defends against same-org cross-user cancel.
- **`cancel_run` terminal fast path** (461–467): returns successfully with `cancel_requested_at = run.cancelled_at` (which may be None for COMPLETED). Clients shouldn't expect the field to be set on terminal-non-cancelled runs.
- **`cancel_run` already-CANCELLING fast path** (468): no second event, no second enqueue. Defends against double-cancel.
- **`record_approval_decision` permission check** (531–537): only the originator can decide.
- **Approval status normalization** (538–542): explicit ternary that drops anything except APPROVED/REJECTED.
- **`_wire_status_for` only applies to `ask_a_question`** (623): regular approvals pass through. The branch is keyed on `Values.ApprovalKind.ASK_A_QUESTION` exact match.
- **Bounded limit on lists** (210, 238): `min(max(1, limit), MAX_MESSAGE_LIMIT)` — clamps at the API boundary.
- **`_run_for_scope` enforces user_id** (792): `run is None or run.user_id != user_id` → 404. Doesn't leak existence to wrong-user.
- **`_conversation_for_scope` doesn't leak existence** (260–263 comment + 778–785): "404s for foreign-tenant conversations (does not leak existence)."
- **Replay events latest-sequence fallback** (430–433): `max(events.sequence_no, default=get_latest_sequence)`. Empty-list case still returns the run's latest_sequence_no, not 0.
- **`prior_run_ids` reversal** (684): chain is collected newest→oldest, reversed for caller. Result is oldest→newest.
- **`_prior_run_ids_for_chain` 200-message cap** (669): same as `RuntimeRunHandler._messages_for_run`. If a conversation has > 200 messages, the chain truncates silently.
- **Hard-coded model catalog** (113–159): three named models in addition to the default. New models must be added here AND in the resolver. Drift risk.
- **`_request_with_runtime_context` AgentRuntimeError → 400** (708–715): reflects model-config validation failure as a client error.
- **Audit logs include `metadata` when relevant** (393, 502, 599): structured fields, not free-text.
- **"approval_kind" lookup is `metadata.get(Keys.Field.APPROVAL_KIND)`** (561): if the approval was created without this metadata key, the wire-status translation passes through. Newly-introduced approval kinds need to set this metadata.

## E. Hardcoded vs configurable

### Hardcoded

- **Model catalog entries** (127–158): `gpt-5.4-mini`, `claude-opus-4-7`, `gemini-2.5-pro`. Plus default. Each entry has hardcoded `name`, `description`, `supports_streaming`, `supports_attachments`, `supports_reasoning`.
- **Reasoning config** for `gpt-5.4-mini` (137): `{"enabled": True, "effort": "medium", "summary": "auto"}`.
- URL templates (643–644): `/v1/agent/runs/{run_id}/stream`, `/v1/agent/runs/{run_id}/events`.
- Pricing region: `"global"` (295).
- Message-list limit cap: 200 (669) — duplicated with `handlers/run.py`.
- Audit `outcome="success"` on every emit.
- Audit `actor_type` defaults handled by the persistence adapter; not set here.

### Configurable

- All ports injected.
- `settings` injected, defaults to `RuntimeSettings.load()`.
- `model_resolver` injected, defaults to `ModelConfigResolver(settings)`.
- `on_event_appended` callback (for in-process worker triggering).
- `Values.DEFAULT_CONVERSATION_LIMIT`, `Values.MAX_MESSAGE_LIMIT`, `Values.DEFAULT_MESSAGE_LIMIT` — from constants.
- `RuntimeSettings.execution.max_parallel_tasks` — driven into runtime context (761).
- `RuntimeSettings.openai/anthropic/gemini.is_configured` — drives catalog availability (109–112).
- `RuntimeSettings.default_model` — primary catalog entry (107).

## F. External dependencies and coupling

### Internal `agent_runtime.*`

- `agent_runtime.execution.contracts.AgentRuntimeContext`, `RuntimeErrorCode`, `StreamEventSource`.
- `agent_runtime.api.constants.Keys`, `Messages`, `Values` — string pools + status / event vocabulary.
- `agent_runtime.api.usage_service.ConversationContextBuilder`.
- `agent_runtime.pricing.ModelPricingCatalog`.
- `agent_runtime.api.events.RuntimeEventProducer`.
- `agent_runtime.api.async_ports`, `agent_runtime.api.ports`.
- `agent_runtime.execution.errors.AgentRuntimeError`.
- `agent_runtime.execution.models.ModelConfigResolver`, `ModelSelection`.
- `agent_runtime.settings.RuntimeSettings`.

### Internal `runtime_*`

- `runtime_api.schemas` — every record / request / response / command type.
- `runtime_api.http.errors.RuntimeApiError`.
- `runtime_adapters.async_wrappers.adapt_*_to_async` — the sync-to-async port-coupling boundary.

### Stdlib / third-party

- `starlette.status`.
- `datetime`, `collections.abc.Callable`.

## G. Suggested decomposition seams

The class is a single FastAPI service-of-everything; cuts:

1. **`conversations_service.py`** — `create_conversation`, `get_conversation`, `list_conversations`, `list_messages`, `delete_user_history`, `_conversation_for_scope`. ~120 LOC.
2. **`runs_service.py`** — `create_run`, `get_run`, `cancel_run`, `replay_events`, `_create_run_response`, `_prior_run_ids_for_chain`, `_run_for_scope`, `_request_with_runtime_context`, `TERMINAL_RUN_STATUSES`. ~280 LOC. Includes the runtime-context builder (which is the most complex helper here).
3. **`approvals_service.py`** — `record_approval_decision`, `_wire_status_for`. ~100 LOC. Self-contained.
4. **`models_catalog.py`** — `list_models`, `_display_model_name`, hard-coded catalog. ~65 LOC. Mostly data; could be promoted to a static config file.
5. **`context_panel_service.py`** — `get_conversation_context`. ~55 LOC. Already delegates the heavy lifting to `ConversationContextBuilder`.

The **runtime-context builder** (`_request_with_runtime_context`) is the densest seam — it's effectively a `request → runtime_context` mapper that fans in 9 different request fields. Could move into a dedicated `RuntimeContextBuilder` class (mirroring `ConversationContextBuilder`).

The **prior-run chain walker** (`_prior_run_ids_for_chain`) and `RuntimeRunHandler._selected_message_chain` are duplicated rules. A shared `MessageChainResolver` utility would make this seam explicit.

**Audit emission** is fanned across every mutation. A small `AuditingMixin` or decorator (`@audit("run_created")`) could centralise the pattern, but the per-call metadata variance argues for keeping the inlines.
