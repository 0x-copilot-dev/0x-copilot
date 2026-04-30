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
- Retry retryable failures up to `RUNTIME_MAX_RETRIES`, then dead-letter.
- Re-check current run state before starting run execution.
- Load conversation history through the persistence port.
- Build no-op local runtime dependencies until production connector adapters exist.
- Invoke `ainvoke_runtime()` through `create_agent_runtime()`.
- Append ordered lifecycle events for queued, started, completed, cancelled, failed, and approval-resolution paths.
- Observe cancellation and approval commands.
- Mark terminal run state exactly once.

## Streaming And Replay

`GET /v1/agent/runs/{run_id}/events` replays persisted events after `after_sequence`.

`GET /v1/agent/runs/{run_id}/stream` uses Server-Sent Events:

- Replay persisted events first.
- Emit `runtime_event` SSE frames.
- Send a transient heartbeat if no replayed events are available and the run is non-terminal.
- Reuse the same `RuntimeEventEnvelope` contract as replay.

Clients should store the highest received `sequence_no` per `run_id` and reconnect with `after_sequence`.

## Lifecycle Events

Implemented API event types include:

- Run lifecycle: `run_queued`, `run_started`, `run_cancelling`, `run_cancelled`, `run_completed`, `run_failed`.
- Progress: `progress`, `reasoning_summary`, `reasoning_summary_delta`, `observation`, `final_response`.
- Tools: `tool_call`, `tool_call_started`, `tool_call_delta`, `tool_result`, `tool_call_completed`.
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

Unit tests cover ordered event envelopes, replay from sequence checkpoints, SSE output, heartbeats, cancellation events, approval resolution events, queue claim/retry/dead-letter behavior, async worker execution, retry limits, parallelism limits, and an acceptance-style multi-turn run lifecycle.
