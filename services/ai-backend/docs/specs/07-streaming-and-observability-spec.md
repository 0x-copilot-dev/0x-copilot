# Spec: Streaming and Observability

## Purpose

Project documented Deep Agents and LangGraph v2 stream output into stable product events for the future work surface UI and debugging systems.

## Architecture

Implemented modules:

- `agent_runtime/execution/runtime.py`: invokes and streams Deep Agents/LangGraph runtime output with stable runtime config.
- `runtime_worker/handlers/run.py`: consumes documented LangGraph v2 `StreamPart` dictionaries and appends `model_delta`, tool, subagent, progress, `final_response`, and lifecycle events.
- `runtime_api/sse/adapter.py`: product-facing Server-Sent Events transport over replayable runtime envelopes.
- `observability/tracing.py`: trace IDs and correlation helpers.
- `observability/redaction.py`: payload redaction helpers.

The worker uses Deep Agents/LangGraph v2 streaming with
`stream_mode=["messages", "updates", "custom", "values"]`, `subgraphs=True`,
and `version="v2"` so main-agent and subagent progress, tokens, tool calls, and
custom updates arrive with namespace metadata. Every runtime stream chunk must be
the documented v2 `StreamPart` shape: `{"type": ..., "ns": ..., "data": ...}`.
Provider text chunks from OpenAI, Anthropic, Gemini, or any compatible LangChain
chat model are emitted from `type == "messages"` as `model_delta` events. Tool
calls/results, custom backend API events, and subagent activity are projected by
the worker from `messages`, `updates`, and `custom` parts into replayable runtime
event envelopes. The exact provider text belongs in `payload.delta`; the terminal
full answer is emitted as `final_response`.

## Pydantic Contracts

Required models:

- LangGraph v2 `StreamPart`: external stream input with `type`, `ns`, and
  `data` keys. This is an adapter input, not a persisted product contract.
- `RuntimeEventEnvelope`: persisted and streamed product event with source,
  event type, task/span correlation, UI presentation fields, redacted payload,
  metadata, and per-run sequence number.
- `RuntimeEventDraft`: pre-persistence envelope data produced by API-authored
  lifecycle events and by the `StreamPart` adapter.
- `StreamSource`: main agent, subagent, tool, MCP, summarization, system,
  runtime, and model.

Payloads must be redacted before serialization. Unknown event fields should be preserved only in an explicitly typed `metadata` object.

## Design Rules

- UI consumers should not parse raw LangGraph stream parts.
- The worker should parse Deep Agents namespaces explicitly: `()` is the main
  agent, and namespace segments like `tools:<id>` identify subagent execution.
  Undocumented string guesses must not become subagent routing rules.
- Stream events should be additive and backwards-compatible.
- Redaction happens before event emission.
- Internal summarization tokens should be filterable from user-facing streams.
- Raw private chain-of-thought is not a product event. Surface Deep Agents-style
  `updates`, `messages`, `custom`, tool, subagent, and `reasoning_summary`
  events with safe summaries and redacted payloads.
- Clients should concatenate `payload.delta` from `model_delta` events for live Markdown display, then reconcile against `final_response`.

## Unit Tests

- Consume v2 `StreamPart` fixtures for main-agent updates.
- Consume v2 `StreamPart` fixtures for subagent namespace chunks.
- Consume v2 `messages` fixtures for tool call chunks and tool result messages.
- Persist safe reasoning summaries, tool deltas/results/completions, and
  subagent lifecycle/progress events from worker streams.
- Emit provider text chunks as `model_delta` events before `final_response`.
- Redact secrets in args and payloads.
- Preserve trace/task correlation across events.
- Ignore non-v2 chunk shapes without exposing raw internals.

## Edge Cases

- Missing namespace.
- Unsupported v2 stream type.
- Empty provider chunks that carry metadata but no text.
- Subagent event arrives before task metadata.
- Tool result exceeds stream size limit.
- Summarization event leaks into user-facing output.
