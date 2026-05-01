# Spec: Runtime Events Producer/Consumer

## Purpose

Document the implemented runtime event producer, replay, streaming, and queue contracts that separate HTTP request handling from long-running agent execution.

The current implementation provides the FastAPI producer in `runtime_api`, typed command queue port in `agent_runtime`, deterministic in-memory queue in `runtime_adapters`, event envelope projection, replay endpoint, SSE adapter, and the first async worker loop in `runtime_worker`. A production external broker adapter can be added behind the same ports.

## Event Envelope

Client-visible runtime events use `RuntimeEventEnvelope` from `src/runtime_api/schemas/events.py` and the compatibility import at `src/agent_runtime/api/contracts.py`.

Required semantics:

- `sequence_no` is monotonically increasing per `run_id`.
- `event_protocol_version` starts at `1`.
- `source` and `event_type` are stable enum values, not raw LangGraph chunk names.
- `display_title`, `summary`, and `status` are product-safe UI timeline fields.
- `parent_event_id`, `span_id`, `parent_span_id`, `parent_task_id`, `task_id`, and `subagent_id` preserve correlation without exposing raw runtime internals.
- `payload` and `metadata` are redacted before persistence and streaming.
- Raw chain-of-thought, provider reasoning tokens, hidden scratchpads, and private prompt text are never client-visible payloads. Use `reasoning_summary` or `reasoning_summary_delta` for safe UI explanations.

## Producer Responsibilities

`RuntimeApiService.create_run` is the implemented producer path:

1. Validate `CreateRunRequest`.
2. Build `AgentRuntimeContext` from `org_id`, `user_id`, request context, and env-backed model settings when the request does not include a full internal context.
3. Load the conversation by `org_id`, `user_id`, and `conversation_id`.
4. Create the user message and queued run through `PersistencePort`.
5. Append a `run_queued` event through `RuntimeEventProducer`.
6. Enqueue `RuntimeRunCommand` through `RuntimeQueuePort`.
7. Return `run_id`, `events_url`, and `stream_url`.

The producer does not invoke the runtime inline.

## Consumer Contract

Runtime consumers must satisfy `RuntimeQueuePort`:

- `claim_next`
- `mark_complete`
- `mark_retry`
- `mark_dead_letter`

Claim records use `RuntimeWorkerClaim`; worker outcomes use `RuntimeWorkerResult`. The in-memory implementation supports lock-aware claiming, retry availability, completion, and dead-letter transitions for deterministic unit tests.

`RuntimeWorker` now implements the first consumer slice:

- Claim queued commands with lock expiration.
- Limit concurrent command handling with `RUNTIME_MAX_PARALLEL_RUNS`.
- Limit concurrent LangGraph tool/task execution inside a run with `RUNTIME_MAX_PARALLEL_TASKS`.
- Retry retryable failures up to `RUNTIME_MAX_RETRIES`, then dead-letter.
- Re-check current run state before starting run execution.
- Load conversation history through the persistence port.
- Build no-op local runtime dependencies until production connector adapters exist.
- Invoke `astream_runtime()` through `create_agent_runtime()` for streaming-capable model profiles, falling back to `ainvoke_runtime()` when streaming is disabled or unavailable.
- Append ordered lifecycle events for queued, started, completed, cancelled, failed, and approval-resolution paths.
- Consume LangGraph/Deep Agents v2 `StreamPart` chunks with `type`, `ns`, and `data` through `RuntimeStreamPartAdapter`.
- Parse Deep Agents namespaces explicitly: `()` is main-agent output and segments
  such as `tools:<id>` identify subagent execution. Other namespace strings do
  not imply subagent routing.
- Append `model_delta` events from `messages` token chunks while the model is running.
- Project `messages`, `updates`, and `custom` parts into replayable reasoning, tool, observation, and subagent events through `RuntimeEventProducer`.
- Persist the final assistant output as both an assistant message and a `final_response` event.
- Build a branch-scoped prior tool observation index from user-visible
  `tool_result` events for later turns. The next run receives only compact
  summaries in prompt context; the model may call `load_prior_tool_result` to
  read one full persisted, redacted prior result by observation id.
- Observe cancellation and approval commands.
- Mark terminal run state exactly once.

## Streaming And Replay

`GET /v1/agent/runs/{run_id}/events` replays persisted events after `after_sequence`.

`GET /v1/agent/runs/{run_id}/stream` uses Server-Sent Events:

- Replay persisted events first.
- Emit `runtime_event` SSE frames.
- Follow live event appends until the run reaches a terminal state when the app has an active worker.
- Send a transient heartbeat only for non-follow replay streams that have no persisted events and are still non-terminal.
- Reuse the same `RuntimeEventEnvelope` contract as replay.

Clients should store the highest received `sequence_no` per `run_id` and reconnect with `after_sequence`.
Clients should treat malformed SSE JSON or JSON that fails the
`RuntimeEventEnvelope` contract as stream protocol errors. Those errors should
not be confused with EventSource network errors or silently dropped.

Provider message token chunks are exposed as `model_delta` events. The exact text
chunk is in `payload.delta`; `summary` is display-oriented and may be normalized.
Clients that want incremental Markdown rendering should concatenate
`payload.delta` until the `final_response` event arrives.

## Lifecycle Events

Implemented API event types include:

- Run lifecycle: `run_queued`, `run_started`, `run_cancelling`, `run_cancelled`, `run_completed`, `run_failed`.
- Progress/model output: `progress`, `reasoning_summary`, `reasoning_summary_delta`, `observation`, `model_delta`, `final_response`.
- Tools: `tool_call`, `tool_call_started`, `tool_call_delta`, `tool_result`, `tool_call_completed`.
  Tool results remain visible as result events; completion events are emitted when
  source data indicates the tool call finished.
  User-visible prior results may also be summarized into later-turn prompt
  context for the same selected message branch. This replay is context-only:
  original tools are not memoized or skipped, and fresh/current requests should
  still call the underlying tool again.
- Subagents: `subagent_update`, `subagent_started`, `subagent_progress`, `subagent_completed`.
- Approvals: `approval_requested`, `approval_resolved`.
- Transport/system: `heartbeat`, `error`.

## Backpressure And Recovery Rules

- Persist events before fanout.
- Treat the event store as the replay source of truth.
- Slow live consumers may reconnect from their last sequence number.
- Cancellation and approval decisions are durable commands, not direct stream mutations.
- Failed workers should retry or dead-letter through `RuntimeQueuePort`.

## Test Coverage

Unit tests cover ordered event envelopes, replay from sequence checkpoints, SSE output, heartbeats, provider chunk `model_delta` streaming, cancellation events, approval resolution events, queue claim/retry/dead-letter behavior, async worker execution, retry limits, parallelism limits, and an acceptance-style multi-turn run lifecycle.
