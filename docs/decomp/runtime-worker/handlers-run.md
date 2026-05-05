# Decomp — `runtime_worker/handlers/run.py`

Source: [services/ai-backend/src/runtime_worker/handlers/run.py](../../../services/ai-backend/src/runtime_worker/handlers/run.py) — **997 LOC, XL.** Single class plus a thin module shell. Owns the full run lifecycle: validate → claim → build prompt → invoke (stream or non-stream) → settle → record usage / cost / audit.

## A. Top-level structure

### Module shell (lines 1–58)

| Symbol                                  | Lines | Purpose                                                                                            |
| --------------------------------------- | ----: | -------------------------------------------------------------------------------------------------- |
| Type alias `RuntimeDependenciesFactory` |    53 | `Callable[[AgentRuntimeContext], RuntimeDependencies]` — injected dependencies-per-run hook.       |
| Type alias `AgentFactory`               |    54 | `Callable[..., RuntimeHarness]` — pluggable agent constructor (default `create_agent_runtime`).    |
| Type alias `RuntimeInvoker`             |    55 | `Callable[[RuntimeHarness, Sequence[object]], object]` — non-streaming invocation hook.            |
| Type alias `RuntimeStreamer`            |    56 | `Callable[[RuntimeHarness, Sequence[object]], AsyncIterator[object]]` — streaming invocation hook. |
| Constant `MAX_STRUCTURED_CONTEXT_CHARS` |    57 | `4_000` — char cap for any single section of injected user-context (quote / parts / attachments).  |

### Class `RuntimeRunHandler` (lines 60–997)

| Symbol                                                                      |   Lines | Purpose                                                                                                                                              |
| --------------------------------------------------------------------------- | ------: | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ |
| Class attribute `action_interrupt_events`                                   |   63–68 | Frozen set of `RuntimeApiEventType` values (`APPROVAL_REQUESTED`, `MCP_AUTH_REQUIRED`) that imply the run must transition to `WAITING_FOR_APPROVAL`. |
| Inner class `_Fields`                                                       |   70–98 | Pool of magic-string keys used across LangChain result shapes, message records, and event payloads (29 entries).                                     |
| `__init__`                                                                  | 100–129 | Wire persistence + event-store + factories + producers + audit + pricing catalog. Adapts sync ports to async via `adapt_*_to_async`.                 |
| `handle(command)`                                                           | 131–344 | **Main entry point.** Full run lifecycle, all status transitions, all error paths.                                                                   |
| `_record_run_usage(run, *, metrics, completed_at, status)`                  | 346–406 | Best-effort per-run usage row + pricing-stamped cost (B1 + B3 spec).                                                                                 |
| `_record_per_call_usage(run, *, metrics)`                                   | 408–467 | Best-effort per-LLM-call usage rows + cost stamps (B2 spec).                                                                                         |
| `_messages_for_run(command, run, *, tool_observation_index)`                | 469–501 | Build prompt message list from durable message history + prior tool observations.                                                                    |
| `_dependencies_for_run(command, tool_observation_index)`                    | 503–522 | Compose `RuntimeDependencies` with `SubagentArtifactsBackend` and `PriorToolResultLoader`.                                                           |
| `_tool_observation_index(command, run)`                                     | 524–535 | Load message chain + delegate to builder.                                                                                                            |
| `_tool_observation_index_from_selected(...)`                                | 537–548 | Build observation index from a pre-loaded message slice.                                                                                             |
| classmethod `_insert_prior_tool_context(messages, prompt_context)`          | 550–567 | Inject a system message with prior tool context immediately before the **last user** message (or at end if none).                                    |
| classmethod `_message_content_for_runtime(message)`                         | 569–590 | Compose full prompt-string for a user message: text + quote + parts + attachments + branch metadata.                                                 |
| classmethod `_quote_context(quote)`                                         | 592–607 | Extract a "Quoted context: …" section.                                                                                                               |
| classmethod `_content_parts_context(parts, content_text)`                   | 609–625 | Walk multimodal `content` parts; emit one summary line per non-redundant part.                                                                       |
| classmethod `_attachments_context(attachments)`                             | 627–650 | "- name (mime, bytes, file_id, url): preview-text" per attachment.                                                                                   |
| classmethod `_branch_context(message)`                                      | 652–675 | Branch metadata: branch_id, source_message_id, regenerate/replace_from, parent_message_id.                                                           |
| classmethod `_part_summary(part_type, part, text)`                          | 677–697 | Per-part summary line builder.                                                                                                                       |
| classmethod `_details(payload, *, content_type)`                            | 699–718 | Comma-joined detail string: content_type, byte size, file_id, url.                                                                                   |
| classmethod `_content_text(payload)`                                        | 720–726 | First non-empty of `text` / `content` / nested blocks.                                                                                               |
| classmethod `_content_blocks_text(value)`                                   | 728–751 | Recursive extraction of text from nested LangChain content blocks.                                                                                   |
| classmethod `_truncate(value)`                                              | 753–757 | Cap at `MAX_STRUCTURED_CONTEXT_CHARS`, append `[truncated]`.                                                                                         |
| classmethod `_selected_message_chain(records, user_message_id)`             | 759–786 | Walk parent-message chain for the run's user message; falls back to "all up to created_at" when chain is rooted (no parent).                         |
| `_stream_runtime(command, run, harness, messages, metrics)`                 | 788–808 | Wrap `StreamingExecutor.run` inside `asyncio.timeout`; call `compose_final` on the result.                                                           |
| classmethod `_is_action_interrupt(result)`                                  | 810–819 | Detect interrupt by checking `interrupts` attribute or `action_required` / `approval_requested` / `interrupts` keys in dict.                         |
| `_reconcile_inflight_tool_calls(run, *, outcome, error_code)`               | 821–885 | Synthesize terminal `tool_result` + `tool_call_completed` events for unsettled ledger entries.                                                       |
| `_append_lifecycle(run, event_type, summary, *, source, payload, metadata)` | 887–907 | Thin wrapper around `event_producer.append_api_event` with sensible defaults; FINAL_RESPONSE gets `status="completed"`.                              |
| `_append_model_call_started(run, metrics, messages)`                        | 909–941 | Compute `prompt_build_ms` (now − metrics.started_at) + prompt_chars; emit `MODEL_CALL_STARTED`.                                                      |
| classmethod `_extract_final_text(result)`                                   | 943–967 | Robust final-text extraction across LangChain result shapes (string / dict / messages list).                                                         |
| classmethod `_message_content(message)`                                     | 969–973 | Read `.content` from a Mapping or attr access.                                                                                                       |
| classmethod `_content_to_text(value)`                                       | 975–992 | Coerce content to a non-empty string (string / list of strings / list of `{text                                                                      | content}` blocks). |
| classmethod `_trace_text(context, key)`                                     | 994–997 | Read a string field from `context.trace_metadata`.                                                                                                   |

### Constants & singletons

- `_Fields` (70–98): 29 magic strings — the file's only allowed source of payload keys. Per [services/ai-backend/CLAUDE.md](../../../services/ai-backend/CLAUDE.md), inline string duplication is banned, so every `result.get("messages")` etc. routes through `_Fields.MESSAGES`.
- `action_interrupt_events` (63–68): `frozenset` of two event types — note this is a _class-level_ set, immutable, so handlers can't mutate the policy at runtime.
- `MAX_STRUCTURED_CONTEXT_CHARS = 4_000` (57): only magic number in the file.

## B. Feature inventory (domains mixed in this file)

| Domain                                                | Symbols                                                                                                                                                                                                                                             |  LOC |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---: |
| **Run lifecycle orchestration**                       | `__init__`, `handle`, `_append_lifecycle`, `_append_model_call_started`                                                                                                                                                                             | ~280 |
| **Usage + cost accounting**                           | `_record_run_usage`, `_record_per_call_usage`                                                                                                                                                                                                       | ~120 |
| **Prompt assembly from message history**              | `_messages_for_run`, `_dependencies_for_run`, `_tool_observation_index*`, `_insert_prior_tool_context`, `_message_content_for_runtime`, all `_*_context` helpers, `_part_summary`, `_details`, `_content_*`, `_truncate`, `_selected_message_chain` | ~330 |
| **Streaming runtime invocation**                      | `_stream_runtime`                                                                                                                                                                                                                                   |  ~25 |
| **Action-interrupt detection**                        | `_is_action_interrupt`, `action_interrupt_events`                                                                                                                                                                                                   |  ~15 |
| **Tool-call orphan reconciliation**                   | `_reconcile_inflight_tool_calls`                                                                                                                                                                                                                    |  ~65 |
| **Final-text extraction across heterogeneous shapes** | `_extract_final_text`, `_message_content`, `_content_to_text`, `_trace_text`                                                                                                                                                                        |  ~60 |

The file conflates **orchestration** (the lifecycle SM, the timeout/exception paths, the audit/usage hooks) with **prompt assembly** (the multimodal message-context builder + parent-chain walker) and a **payload-shape canonicalizer** (the `_extract_final_text` ladder). All three are independently complex and could each stand alone.

## C. Functional spec per domain

### Domain 1 — Run lifecycle orchestration (`handle`, lines 131–344)

**Inputs:** `RuntimeRunCommand` (org_id, run_id, conversation_id, user_id, runtime_context, trace_id).
**Outputs:** None — purely side-effecting on persistence + event-store.
**Side effects:** Run row status transitions; events appended; messages appended; usage rows + cost stamps; audit emissions.

**State machine — `RunRecord.status` transitions inside `handle`:**

```
QUEUED (preset by API)
   │
   ▼
RUNNING                       ← line 159–163 (with_optimistic_retry)
   │       (emits RUN_STARTED, audit run_started, MODEL_CALL_STARTED)
   │
   ├──► WAITING_FOR_APPROVAL  ← line 213–218 (when _is_action_interrupt(result))
   │
   ├──► TIMED_OUT             ← line 261–265 (asyncio TimeoutError caught at 255)
   │       (orphan-settle tool calls, emit RUN_FAILED, audit, record usage)
   │
   ├──► FAILED                ← line 291–295 (any other Exception caught at 285)
   │       (orphan-settle tool calls, emit RUN_FAILED, audit, record usage, re-raise)
   │
   └──► COMPLETED             ← line 316–320
           (emit FINAL_RESPONSE [if final_text], emit RUN_COMPLETED, audit, record usage)
```

**Validation rules (lines 134–157):**

- `run` must exist (`get_run` returns `None` → `VALIDATION_ERROR`, non-retryable).
- `run.conversation_id == command.conversation_id` (cross-binding guard).
- `run.user_id == command.user_id` (cross-binding guard).

**Tenant-isolation guards:**

- `get_run` is called with `org_id=command.org_id` (134–136) — port-level RLS.
- All subsequent persistence calls receive `org_id` either explicitly (`list_messages`) or via the run record's own `org_id`.

**Error types raised:**

- `AgentRuntimeError(RuntimeErrorCode.VALIDATION_ERROR, retryable=False)` — three sites, lines 138, 145, 152.
- Re-raises any non-Timeout exception after settlement (line 314).

### Domain 2 — Usage + cost accounting (`_record_run_usage` + `_record_per_call_usage`, 346–467)

**Inputs:** completed/failed `RunRecord`, `AssistantRunMetrics`, `completed_at`, status string.
**Outputs:** Persistence side-effects only.
**Side effects:**

- `record_run_usage(usage_record)` (B1 spec)
- `record_model_call_usage(record)` per LLM call (B2 spec)
- `update_run_usage_cost` + `update_model_call_usage_cost` (B3 spec)

**Pricing lookup logic (380–400):**

- `pricing_catalog.lookup(provider, model, region="global", at=completed_at)` — region is hardcoded `"global"`.
- If `pricing is None`: cost stays NULL (B3: unknown models are null-safe).
- `CostCalculator.compute(input_tokens, output_tokens, cached_input_tokens, pricing)`.

**Failure handling:**

- All exceptions in this domain are caught + logged with `runtime_run_usage_*_write_failed` / `runtime_model_call_usage_*_write_failed` and swallowed. The run lifecycle MUST NOT break for usage/pricing failures (docstring 354–365).

**Reconciliation invariant (docstring 414–424):** `sum(model_call_usage rows for run_id) == runtime_run_usage` for that run; held by construction because both come from the same `AssistantRunMetrics` accumulator.

### Domain 3 — Prompt assembly (`_messages_for_run` + helpers, 469–786)

**Inputs:** `RuntimeRunCommand`, `RunRecord`, optional `ToolObservationIndex`.
**Outputs:** `tuple[dict[str, str], ...]` of `{role, content}` messages ready for the runtime.

**Algorithm:**

1. List up to **200** durable messages for the conversation (479).
2. Walk parent chain from `run.user_message_id` backward via `_selected_message_chain` (481, 759–786).
   - If the user message has no parent (root) → return all messages with `created_at <= run_user.created_at` (778–783).
   - Otherwise → only ancestors (parent → … → root) (784–786).
3. Filter to `USER`, `ASSISTANT`, `SYSTEM` roles only (487–489).
4. For each user message, expand content via `_message_content_for_runtime`:
   - Base text
   - "Quoted context:\n…" if `quote` present
   - "Structured content:\n…" if multimodal parts present
   - "Attachments:\n…" if attachments present
   - "Branch metadata:\n…" if any branch field present
5. If observations have `prompt_context` non-None, insert a **synthetic SYSTEM message** immediately before the last user message via `_insert_prior_tool_context` (499–500, 550–567).

**Validation rules:**

- `MAX_STRUCTURED_CONTEXT_CHARS = 4_000` cap on any single context section, suffixed with `[truncated]` (753–757).
- Roles are filtered against an explicit allow-list (USER / ASSISTANT / SYSTEM).
- `StreamTextHelper.extract` is used for every untrusted-shape lookup.

### Domain 4 — Streaming invocation (`_stream_runtime`, 788–808)

**Inputs:** command, run, harness, messages, metrics.
**Outputs:** Composed final result via `StreamingExecutor.compose_final`.
**Behavior:** Wraps `StreamingExecutor.run` in `asyncio.timeout(model_profile.timeout_seconds)`; passes `track_subagents=True`. All real streaming logic lives in [`streaming_executor.py`](streaming-bundle.md) and [`stream_events.py`](stream-events.md).

### Domain 5 — Action-interrupt detection (`_is_action_interrupt`, 810–819)

**Inputs:** any `result` object.
**Outputs:** `bool`.
**Rules:** True if any of:

- `getattr(result, "interrupts", None)` is truthy.
- `result` is a Mapping AND any of `action_required is True`, `approval_requested is True`, `bool(interrupts)`.

This is the gate that decides **WAITING_FOR_APPROVAL vs COMPLETED** at line 212.

### Domain 6 — Orphan tool-call reconciliation (`_reconcile_inflight_tool_calls`, 821–885)

**Trigger:** `TimeoutError` (256) or any other `Exception` (286) — only on failure paths.
**Inputs:** run, outcome (`ToolOutcome.TIMED_OUT` / `ToolOutcome.FAILED`), error_code (`TOOL_RUN_TIMEOUT` / `TOOL_EXCEPTION`).
**Outputs:** Synthesized `TOOL_RESULT` + `TOOL_CALL_COMPLETED` events for every unsettled ledger entry, in started-order.

**Why this exists (docstring 828–840):** Without reconciliation, any tool call still in `tool_call_started` without a matching `tool_result` would leave a **"Running" card stuck on the client** because the SSE consumer never sees the lifecycle terminate. Events are emitted **before** `RUN_FAILED` so SSE consumers see lifecycle terminate top-down.

**Failure within reconciliation:** Logged but never raised — the caller is already on a failure path; partial reconciliation is strictly better than none.

**Side effects:** Two events appended per entry; ledger marked `observed_settled(call_id)`.

### Domain 7 — Final-text extraction (`_extract_final_text` + helpers, 943–992)

Robust extractor for "what is the final assistant text in this LangChain/LangGraph result?" Tries in order:

1. `None` → `None`.
2. `str` → stripped string or `None`.
3. `dict` → first non-None of `final_response`, `response`, `output`, `content`.
4. `dict` with `messages: Sequence` → walk messages in **reverse** for last extractable text.
5. Fall back to `_message_content(result)` (attribute or mapping `.content`).

Used to decide whether to append an `ASSISTANT` `MessageRecord` and a `FINAL_RESPONSE` event (line 220–254). If `None`, neither is emitted (run still completes, but with no terminal message).

## D. Bugs / edge cases / invariants

- **Run/command cross-binding guards** (144–157): conversation_id and user_id mismatch are non-retryable validation errors. Defends against queue/command tampering or stale enqueues.
- **Optimistic-retry on every status change** (159, 213, 261, 291, 316): all status mutations go through `with_optimistic_retry` to handle concurrent updates from cancel-handler / approval-handler racing.
- **`_runtime_streamer_explicit` flag** (127): if the caller passed an explicit streamer, it overrides the streaming-capability detection on the harness. Lets tests inject a streamer regardless of the agent class.
- **Best-effort usage/cost writes** (354–365, 414–424): docstrings explicitly call out "must never break the run lifecycle." Failures are swallowed to a `WARNING` log.
- **Pricing as-of `completed_at`** (385): cost is stamped against the pricing snapshot in effect when the run completed, not when each call happened. Per-call usage rows are stamped against `datetime.now()` (line 446) — a small skew, called out at 444.
- **Reconciliation runs before RUN_FAILED** (255–284 + 285–314): the docstring at 833–839 makes it explicit that lifecycle terminates **top-down** so SSE consumers don't get stuck on "running" tool cards.
- **Reconciliation failures swallowed individually** (878–885): per-entry try/except prevents one failed entry from blocking the rest.
- **Ledger discard on completion AND on failure paths** (282, 312, 321): `discard_ledger` + `discard_metrics` are called in every terminal branch to prevent memory leaks across run terminations.
- **`MAX_STRUCTURED_CONTEXT_CHARS = 4_000` cap** (57, 754): bounds prompt growth from arbitrarily-large user-supplied quote / parts / attachment content. Note this cap is **per section**, so a message with quote + parts + attachments could still inject up to ~12k chars.
- **Parent-chain root fallback** (778–783): when the run's user message is a conversation root (no `parent_message_id`), the function falls back to "all messages with `created_at <= run_user.created_at`" rather than only the lone root. Prevents bizarre regressions when a root message is regenerated.
- **`MessageRole` allow-list** (487–489): TOOL / SYSTEM-prompt-injection roles are silently dropped. Explicit allow-list rather than deny-list.
- **`prompt_build_ms` clamp** (923–925): `max(0, …)` guards against clock skew giving negative values (e.g. NTP correction during a run).
- **Final-text reverse-walk** (962–966): explicitly walks the `messages` list in reverse to grab the LAST text, not the first.
- **Branch-metadata fan-in** (652–675): pulls branch_id from both the message column AND `metadata["branch"][regenerate_from_message_id|replace_from_message_id]`, AND a top-level `metadata["regenerate_from_message_id"]`. Three sources fanned in; first non-empty wins per key.

## E. Hardcoded vs configurable

### Hardcoded

- `MAX_STRUCTURED_CONTEXT_CHARS = 4_000` (57) — char cap per section.
- `limit=200` on `list_messages` (479, 532) — message-history fetch ceiling.
- Region `"global"` on pricing lookup (385, 444) — pricing-catalog region key.
- Magic strings via `_Fields` (70–98) — but these are local constants, not duplicated literals.
- Error messages: `"Run command references an unknown run."`, `"Run command conversation_id does not match persisted run."`, `"Run command user_id does not match persisted run."`, `"Run started"`, `"Run timed out"`, `"Run failed"`, `"Run completed"`, `"Model call started"`.
- Log keys: `runtime_run_usage_write_failed`, `runtime_run_usage_cost_write_failed`, `runtime_model_call_usage_write_failed`, `runtime_model_call_usage_cost_write_failed`, `tool_call_reconcile.failed`.
- Synthesized payload keys for orphan settlement: `tool_name`, `call_id`, `status`, `error_code`, `error_message` (849–873).
- Truncation suffix `[truncated]` (757).

### Configurable (via `RuntimeSettings` / command)

- Persistence + event-store ports: injected.
- `RuntimeDependencies` factory: injected (defaults to `DefaultRuntimeDependenciesFactory(self.settings)`).
- Agent factory, runtime invoker, runtime streamer: all injected (testing seam).
- Model timeout: `command.runtime_context.model_profile.timeout_seconds` (204, 797).
- Streaming-vs-not branch: `command.runtime_context.model_profile.supports_streaming` (187).
- Pricing catalog: `ModelPricingCatalog(self.persistence)` constructed in `__init__`.

### From env / settings

- Indirectly via `RuntimeSettings.load()` (114) when no settings injected.

## F. External dependencies and coupling

### Internal `agent_runtime.*`

- `agent_runtime.api.presentation_templates._ErrorMessage` (11) — **deep import of a leading-underscore symbol**; coupling smell.
- `agent_runtime.execution.contracts` (12–17): `AgentRuntimeContext`, `RuntimeDependencies`, `RuntimeErrorCode`, `StreamEventSource`.
- `agent_runtime.execution.tool_outcomes`: `ToolErrorCode`, `ToolOutcome`.
- `agent_runtime.execution.errors.AgentRuntimeError`.
- `agent_runtime.execution.factory`: `RuntimeHarness`, `create_agent_runtime`.
- `agent_runtime.execution.runtime`: `ainvoke_runtime`, `astream_runtime`.
- `agent_runtime.api.async_ports`: `AsyncEventStorePort`, `AsyncPersistencePort`.
- `agent_runtime.api.events.RuntimeEventProducer`.
- `agent_runtime.api.ports`: `EventStorePort`, `PersistencePort`.
- `agent_runtime.persistence.with_optimistic_retry`.
- `agent_runtime.pricing`: `CostCalculator`, `ModelPricingCatalog`.
- `agent_runtime.settings.RuntimeSettings`.
- `agent_runtime.context.memory.subagent_trace.SubagentArtifactsBackend`.

### Internal `runtime_*`

- `runtime_adapters.async_wrappers`: `adapt_event_store_to_async`, `adapt_persistence_to_async`.
- `runtime_api.schemas`: `AgentRunStatus`, `MessageRecord`, `MessageRole`, `RunRecord`, `RuntimeApiEventType`, `RuntimeRunCommand` — coupling to the API schema package.
- `runtime_worker.audit.WorkerAuditEmitter`.
- `runtime_worker.dependencies.DefaultRuntimeDependenciesFactory`.
- `runtime_worker.run_metrics.AssistantRunMetrics`.
- `runtime_worker.stream_events.StreamOrchestrator`.
- `runtime_worker.stream_messages.StreamTextHelper`.
- `runtime_worker.streaming_executor.StreamingExecutor`.
- `runtime_worker.tool_observations`: `PriorToolResultLoader`, `ToolObservationIndex`, `ToolObservationIndexBuilder`.

### Stdlib / third-party

- `asyncio`, `logging`, `time`, `datetime` — stdlib only inside this file; no LangChain / LangGraph / Pydantic imports here. All SDK touch is delegated to `factory` / `runtime` / `streaming_executor`.

## G. Suggested decomposition seams

The file has clear seams that the existing code already hints at — most via separate helper imports. Possible cuts (descriptive only):

1. **`prompt_assembly.py`** — split out `_messages_for_run` + `_dependencies_for_run` + `_tool_observation_index*` + `_insert_prior_tool_context` + `_message_content_for_runtime` + all `_*_context` / `_part_*` / `_details` / `_content_*` / `_truncate` / `_selected_message_chain`. ~330 LOC. Pure functions of (command, run, persistence). Almost all are classmethods today, which is the seam itself.
2. **`run_lifecycle.py`** — keep `handle` + `_append_lifecycle` + `_append_model_call_started` + `__init__`. ~280 LOC. The actual orchestration state machine.
3. **`run_usage.py`** — split out `_record_run_usage` + `_record_per_call_usage`. ~120 LOC. Self-contained, only depends on `metrics`, `persistence`, `pricing_catalog`. Already labeled with B1/B2/B3 spec links in docstrings.
4. **`tool_reconcile.py`** — `_reconcile_inflight_tool_calls`. ~65 LOC. Could even move to `runtime_worker/tool_call_ledger.py` since the ledger is the only thing it touches.
5. **`final_text.py`** — `_extract_final_text` + `_message_content` + `_content_to_text` + `_trace_text`. ~60 LOC. A pure payload-shape canonicalizer; depends on nothing in `runtime_worker`. Could even move into `runtime_worker/stream_messages.py` next to `StreamTextHelper`.
6. **`interrupt_detect.py`** — `_is_action_interrupt` + `action_interrupt_events`. ~15 LOC. Tiny but cohesive; could live next to `runtime_worker/stream_events.py` which already handles the `APPROVAL_REQUESTED` / `MCP_AUTH_REQUIRED` projection.

The `_Fields` inner class is the seam-of-seams: most prompt-assembly methods need only a subset of those keys (the multimodal-parts subset), while orchestration only uses ~5 of them (`STATUS`, `MESSAGE`, `BRANCH_ID`, `INTERRUPTS`, `ACTION_REQUIRED` / `APPROVAL_REQUESTED`). Splitting `_Fields` along those lines would make the cuts above clean.

The dependency from `agent_runtime.api.presentation_templates._ErrorMessage` (line 11) to a leading-underscore symbol is a coupling smell — if a seam is taken, the message lookup should move behind a public API.
