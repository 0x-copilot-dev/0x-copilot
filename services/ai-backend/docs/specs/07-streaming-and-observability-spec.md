# Spec: Streaming and Observability

## Purpose

Normalize Deep Agents and LangGraph stream output into stable product events for the future work surface UI and debugging systems.

## Architecture

Implemented modules:

- `agent_runtime/events/normalization/langgraph.py`: normalizes LangGraph chunks into product-safe stream events.
- `agent_runtime/execution/runtime.py`: invokes and streams Deep Agents/LangGraph runtime output with stable runtime config.
- `runtime_worker/handlers/run.py`: consumes provider chunks and appends `model_delta`, `final_response`, and lifecycle events.
- `runtime_api/sse/adapter.py`: product-facing Server-Sent Events transport over replayable runtime envelopes.
- `observability/tracing.py`: trace IDs and correlation helpers.
- `observability/redaction.py`: payload redaction helpers.

The worker uses Deep Agents/LangGraph v2 streaming with
`stream_mode=["messages", "updates", "custom", "values"]`, `subgraphs=True`,
and `version="v2"` so main-agent and subagent progress, tokens, tool calls, and
custom updates arrive with namespace metadata. It falls back to
`["messages", "values"]` when a graph does not support the richer stream options.
Provider text chunks from OpenAI, Anthropic, Gemini, or any compatible LangChain
chat model are emitted as `model_delta` events. Non-model chunks are normalized
through `LangGraphStreamNormalizer` and persisted as replayable runtime events.
The exact provider text belongs in `payload.delta`; the terminal full answer is
emitted as `final_response`.

## Pydantic Contracts

Required models:

- `StreamEvent`: event ID, source, event type, timestamp, trace ID, parent task ID, payload.
- `StreamSource`: main agent, subagent, tool, MCP, summarization, system.
- `ToolCallEvent`: tool name, call ID, redacted args, status.
- `SubagentLifecycleEvent`: task ID, subagent name, status, summary.
- `ObservationEvent`: metric name, value, tags, trace ID.

Payloads must be redacted before serialization. Unknown event fields should be preserved only in an explicitly typed `metadata` object.

## Design Rules

- UI consumers should not parse raw LangGraph namespace tuples.
- Stream events should be additive and backwards-compatible.
- Redaction happens before event emission.
- Internal summarization tokens should be filterable from user-facing streams.
- Raw private chain-of-thought is not a product event. Surface Deep Agents-style
  `updates`, `messages`, `custom`, tool, subagent, and `reasoning_summary`
  events with safe summaries and redacted payloads.
- Clients should concatenate `payload.delta` from `model_delta` events for live text display, then reconcile against `final_response`.

## Unit Tests

- Normalize main-agent update chunks.
- Normalize subagent namespace chunks.
- Normalize tool call and tool result message chunks.
- Persist safe reasoning summaries, tool deltas/results/completions, and
  subagent lifecycle/progress events from worker streams.
- Emit provider text chunks as `model_delta` events before `final_response`.
- Redact secrets in args and payloads.
- Preserve trace/task correlation across events.
- Gracefully handle malformed chunks.

## Edge Cases

- Missing namespace.
- Unknown stream mode.
- Empty provider chunks that carry metadata but no text.
- Subagent event arrives before task metadata.
- Tool result exceeds stream size limit.
- Summarization event leaks into user-facing output.

