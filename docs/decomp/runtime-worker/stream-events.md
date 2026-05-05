# Decomp — `runtime_worker/stream_events.py`

Source: [services/ai-backend/src/runtime_worker/stream_events.py](../../../services/ai-backend/src/runtime_worker/stream_events.py) — **624 LOC, L.** Three classes — `_Fields`, `StreamCustomProcessor`, `StreamOrchestrator`. The orchestrator is the **central dispatcher** that routes LangGraph stream chunks into typed `RuntimeApiEvent` rows. Owns: native-interrupt extraction, approval-request creation, MCP-tool-approval payload construction (action_requests fan-out), tool-bubble morphing, and source-tag classification.

## A. Top-level structure

| Symbol                                                                        |   Lines | Purpose                                                                                                                              |
| ----------------------------------------------------------------------------- | ------: | ------------------------------------------------------------------------------------------------------------------------------------ |
| `_Fields`                                                                     |   22–48 | 26 magic-string keys for stream-payload access.                                                                                      |
| `StreamCustomProcessor.process(...)` (classmethod)                            |   51–82 | Process custom + fallthrough events. Routes to `SUBAGENT_PROGRESS` or `PROGRESS` based on namespace.                                 |
| `StreamOrchestrator.__init__`                                                 |   92–97 | Compose `StreamUpdateProcessor` + `StreamMessageProcessor`.                                                                          |
| `append_activity_events(*, run, chunk, delta)`                                |  99–196 | **The main dispatch.** Native interrupts → explicit api payloads → message-vs-update branch.                                         |
| classmethod `stream_delta(chunk)`                                             | 198–210 | Extract a textual delta from a `messages` chunk for the **main agent only** (no subagents).                                          |
| classmethod `_source_tool_call_id_for_payload(payload)`                       | 212–222 | Pull the originating tool_call_id from a tool-result message.                                                                        |
| classmethod `_approval_event_morphs_tool_bubble(event_type, payload)`         | 224–246 | True for `MCP_AUTH_REQUIRED` and `APPROVAL_REQUESTED(kind=mcp_tool)`.                                                                |
| `create_approval_request(*, run, payload)`                                    | 248–272 | Idempotent insert of an `ApprovalRequestRecord` from a payload's `approval_id`.                                                      |
| `append_native_interrupt_events(*, run, value)`                               | 274–295 | Non-streaming path: scan `value` for native interrupts and emit `APPROVAL_REQUESTED`/`MCP_AUTH_REQUIRED`.                            |
| classmethod `payload_with_action_id(event_type, payload)`                     | 297–316 | Normalize `approval_id` and `action_id`; default `approval_kind="mcp_auth"` for MCP_AUTH_REQUIRED.                                   |
| classmethod `native_interrupt_payloads(run, value)`                           | 318–347 | **Native interrupt fan-out:** for each interrupt, try auth → ask_a_question → tool-approval.                                         |
| classmethod `_native_interrupts(value)`                                       | 349–362 | Pull interrupts list from `__interrupt__` / `interrupts` (mapping or attribute or nested payload).                                   |
| classmethod `_native_interrupt_value(interrupt)`                              | 364–368 | `interrupt.value` if present, else `interrupt`.                                                                                      |
| classmethod `_native_interrupt_id(interrupt, *, fallback)`                    | 370–376 | `interrupt.id` / `interrupt_id`, else `fallback`.                                                                                    |
| classmethod `_native_auth_payload(interrupt_id, interrupt_value)`             | 378–403 | Build MCP_AUTH_REQUIRED payload from native interrupt; defaults `approval_kind="mcp_auth"`.                                          |
| classmethod `_native_ask_a_question_payload(interrupt_id, interrupt_value)`   | 405–435 | Build APPROVAL_REQUESTED(kind=ask_a_question) payload.                                                                               |
| classmethod `native_tool_approval_payloads(*, interrupt_id, interrupt_value)` | 437–504 | **MCP tool approval fan-out**: walk `action_requests`, build one `mcp_tool` approval payload per request that names `call_mcp_tool`. |
| classmethod `_connector_display_name(value)`                                  | 506–524 | Strip `mcp_` prefix / `_mcp` suffix / `_com`/`-com`; brand-word capitalize.                                                          |
| static `_connector_brand_word(value)`                                         | 526–535 | Brand-name override map: `clickup→ClickUp`, `github→GitHub`, `gitlab→GitLab`, `slack→Slack`, `google→Google`.                        |
| classmethod `_connector_action_name(tool_name)`                               | 537–549 | Heuristic: `search`/`read`/`modify`/`action`.                                                                                        |
| classmethod `_connector_action_is_read_only(tool_name)`                       | 551–559 | False if name contains `create`/`post`/`send`/`update`/`delete`/`write`.                                                             |
| classmethod `_review_configs_by_action(value)`                                | 561–582 | Build `{action_name: (allowed_decisions...)}` map from review_configs list.                                                          |
| classmethod `stream_result_candidate(chunk)`                                  | 584–593 | Pull `data` from a `values`-typed main-agent chunk; used to detect the final result.                                                 |
| classmethod `_source_for_event(event_type, namespace)`                        | 595–624 | Map `event_type` → `StreamEventSource`: MCP/RUNTIME/TOOL/SUBAGENT/MAIN_AGENT.                                                        |

## B. Feature inventory

| Domain                                         | Symbols                                                                                                                                |  LOC |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ---: |
| **Stream-chunk dispatch**                      | `append_activity_events`, `stream_delta`, `stream_result_candidate`                                                                    | ~120 |
| **Approval request creation**                  | `create_approval_request`, `payload_with_action_id`                                                                                    |  ~50 |
| **Native interrupt extraction**                | `append_native_interrupt_events`, `native_interrupt_payloads`, `_native_interrupts`, `_native_interrupt_value`, `_native_interrupt_id` |  ~75 |
| **Native interrupt → typed payload (3 kinds)** | `_native_auth_payload`, `_native_ask_a_question_payload`, `native_tool_approval_payloads`                                              | ~125 |
| **Connector-name humanization**                | `_connector_display_name`, `_connector_brand_word`, `_connector_action_name`, `_connector_action_is_read_only`                         |  ~70 |
| **Review-config index**                        | `_review_configs_by_action`                                                                                                            |  ~22 |
| **Tool-bubble morphing**                       | `_approval_event_morphs_tool_bubble`, `_source_tool_call_id_for_payload`                                                               |  ~45 |
| **Source classification**                      | `_source_for_event`                                                                                                                    |  ~30 |
| **Custom/fallthrough processor**               | `StreamCustomProcessor.process`                                                                                                        |  ~32 |

## C. Functional spec per domain

### Main dispatch (`append_activity_events`, 99–196)

Inputs: `run`, `chunk` (LangGraph stream part), `delta` (current text delta or None).

Algorithm:

1. Parse stream part via `StreamPartParser.stream_part(chunk)`. Bail if not parseable.
2. Determine `stream_type` ∈ `{messages, updates, custom, values}`.
3. Compute `namespace` (subagent task ids embedded in the stream-graph path).
4. Pull `data`, `metadata`, `parent_task_id`, `source_tool_call_id` (only for messages stream).
5. **Native interrupt path** (121–136): if any native interrupts in data, emit one event per (creating approval request inline for `APPROVAL_REQUESTED`/`MCP_AUTH_REQUIRED`); **return early**.
6. **Explicit API payloads** (138–163): each payload's event type drives an `append_api_event`. For `APPROVAL_REQUESTED`/`MCP_AUTH_REQUIRED`, also create the approval row. **Tool-bubble morph**: when `source_tool_call_id` is present and `_approval_event_morphs_tool_bubble`, copy it onto the payload.
7. **Messages branch** (165–172): if `stream_type == messages`, hand off to `message_processor.process(...)`; return.
8. **Updates / custom branch** (175–196): if data already contains an explicit API event, return (already emitted above). Otherwise, try `update_processor.process(...)`. If update processor doesn't claim it, fall through to `StreamCustomProcessor.process(...)`.

### Native interrupt fan-out (`native_interrupt_payloads`, 318–347)

For each interrupt in `value`:

1. Compute `interrupt_id` (or `f"interrupt:{run.run_id}:{index}"` fallback).
2. Compute `interrupt_value`.
3. Try `_native_auth_payload` → if it returns a payload, use it and continue.
4. Try `_native_ask_a_question_payload` → if it returns a payload, use it and continue.
5. Otherwise call `native_tool_approval_payloads` (which can return MULTIPLE payloads, one per `action_request`).

This means a single native interrupt can generate **multiple** approval requests for multi-tool review configs.

### MCP tool approval (`native_tool_approval_payloads`, 437–504)

For each `action_request` whose `name == "call_mcp_tool"`:

1. Pull `args.server_name`, `args.tool_name`, `args.arguments`.
2. Build:
   - `display_name` ← `_connector_display_name(server_name)`
   - `action_label` ← `_connector_action_name(tool_name)`
   - `read_only` ← `_connector_action_is_read_only(tool_name)`
3. **Approval ID strategy**: `interrupt_id` if 1 action request, else `f"{interrupt_id}:{index}"` (fan-out preserves grouping).
4. Build payload with `approval_kind="mcp_tool"`, `risk_level=low` (read-only) or `medium` (write), `grant_options=["allow_once"]`, `message="Allow {display_name} {action_label}?"`, plus action_index/count.
5. Allowed decisions pulled from `review_configs` for `call_mcp_tool` action_name.

### Tool-bubble morphing (`_approval_event_morphs_tool_bubble`, 224–246)

True when:

- Event type is `MCP_AUTH_REQUIRED`, OR
- Event type is `APPROVAL_REQUESTED` AND `payload.approval_kind == "mcp_tool"`.

False for `ask_a_question` and other free-standing approval kinds. Comment 230–238: "If they carry source_tool_call_id they displace that tool's bubble in the chat timeline."

### Source classification (`_source_for_event`, 595–624)

Lookup ladder:

- `MCP_AUTH_REQUIRED` → `MCP`
- `APPROVAL_REQUESTED` → `RUNTIME`
- Tool events → `TOOL`
- Subagent events OR `namespace.is_subagent` → `SUBAGENT`
- Else → `MAIN_AGENT`

### `create_approval_request` idempotency

`get_approval_request` first; if exists, no-op (261–262). The approval-record write is `metadata=payload` — the entire event payload is preserved on the row.

## D. Bugs / edge cases / invariants

- **Native interrupts win over explicit api payloads** (135–136): when both are present in the same chunk, only native interrupts fire. Defends against double-emission.
- **Tool-bubble morph is event-type-specific** (224–246): `ask_a_question` deliberately doesn't morph; mcp_tool / mcp_auth do.
- **Approval idempotency** (255–262): two creators of the same approval converge silently.
- **MCP tool approval ID fan-out** (478–479): single-action-request keeps `interrupt_id`; multi-action splits with `:N` suffix. Frontend can group by `native_interrupt_id` regardless.
- **Brand-word capitalization** (526–535): hand-curated map for known brands. Anything else falls back to `value.capitalize()` (so `linear` becomes `Linear` but `clickup` becomes `ClickUp`).
- **`_connector_action_is_read_only` is permissive** (552–559): treats unknown verbs as read-only by default. New mutating-tool-name verbs need to be added to the list.
- **Action label heuristic** (538–549): cheap classification — `search`/`read`/`modify`/`action`. Not dynamic per-tool.
- **`stream_delta` excludes subagent streams** (203): main-agent text deltas only feed the assistant message; subagent text feeds the supervisor's virtual-file system instead (see [subagent_trace.md](../context-memory/subagent-trace.md)).
- **`stream_delta` excludes tool-call chunks and tool-result messages** (206–209): only "the assistant's free-form text" counts as delta.
- **`stream_result_candidate` only returns `values`-stream main-agent chunks** (587–591): subagent values are not the run's final result.
- **Interrupt fallback id uses run_id + index** (327–328): if the SDK doesn't supply an id, we manufacture one stable per (run, position).
- **`grant_options=["allow_once"]`** (501): MCP-tool approvals always offer single-shot grant; persistent grants are not supported in this code path.
- **`risk_level="low" if read_only else "medium"`** (498): never `high`. New high-risk actions need a different classification.
- **`_review_configs_by_action` skips non-string decisions** (580): defensive against malformed config.

## E. Hardcoded vs configurable

### Hardcoded

- Brand map (528–534): five brands. Anything new must be added here for proper capitalization.
- Action verb lists (`search`/`read`/`modify` heuristics) (540–548).
- Read-only verb denylist (555–558): `create`, `post`, `send`, `update`, `delete`, `write`.
- Approval kind strings: `"mcp_auth"`, `"mcp_tool"`, `"ask_a_question"`.
- `risk_level`: `"low"` / `"medium"` only.
- `grant_options`: `["allow_once"]`.
- Approval message template: `"Allow {display_name} {action_label}?"`.
- Acronym uppercase set (518): `api`, `url`, `id`, `mcp`.
- Connector name fallbacks: `"MCP server"`, `"MCP tool"`, `"Connector"`.
- Source-classification ladder (601–624): hard-coded.

### Configurable

- All processors injected.
- `event_producer` carries persistence + event store.

## F. External dependencies and coupling

### Internal

- `agent_runtime.api.constants.Keys`, `Values` — payload-key + value pools.
- `agent_runtime.api.events.RuntimeEventProducer` — append API.
- `agent_runtime.capabilities.mcp.constants.Values as McpValues` — `ToolName.CALL_MCP_TOOL`.
- `agent_runtime.execution.contracts.StreamEventSource`.
- `runtime_api.schemas` — `ApprovalRequestRecord`, `RunRecord`, `RuntimeApiEventType`.
- `runtime_worker.stream_messages` — `StreamMessageParser`, `StreamTextHelper`.
- `runtime_worker.stream_parts` — `StreamNamespace`, `StreamPartParser`.
- `runtime_worker.stream_subagents.StreamUpdateProcessor`.
- `runtime_worker.stream_tools.StreamMessageProcessor`.

### Stdlib

- `collections.abc.Mapping`, `Sequence` only.

## G. Suggested decomposition seams

The orchestrator is the dispatch hub — already composes specialised processors. Cuts:

1. **`native_interrupt_extraction.py`** — `_native_interrupts`, `_native_interrupt_value`, `_native_interrupt_id`, `_native_auth_payload`, `_native_ask_a_question_payload`, `native_tool_approval_payloads`, `payload_with_action_id`. ~200 LOC. Pure functions over `interrupt_value`.
2. **`approval_request_creation.py`** — `create_approval_request` + the idempotency rules. ~30 LOC.
3. **`connector_name_resolver.py`** — `_connector_display_name`, `_connector_brand_word`, `_connector_action_name`, `_connector_action_is_read_only`, `_review_configs_by_action`. ~85 LOC. The brand map + verb heuristics.
4. **`stream_orchestrator.py`** — keeps `StreamOrchestrator.__init__`, `append_activity_events`, `append_native_interrupt_events`, `stream_delta`, `stream_result_candidate`, `_approval_event_morphs_tool_bubble`, `_source_tool_call_id_for_payload`, `_source_for_event`. ~200 LOC.
5. **`stream_custom_processor.py`** — `StreamCustomProcessor.process`. ~30 LOC.

The brand map (528–535) and action verb heuristics (540–548) currently live behind `classmethod` access on the orchestrator — they should be plain module-level data; the seams in 3 above expose that.

The native-interrupt extraction (1 above) is **the most isolated chunk** — pure functions over an opaque `interrupt_value`, no side effects, no I/O. That's a clean refactor target.
