# Spec: Streaming and Observability

## Purpose

Normalize Deep Agents and LangGraph stream output into stable product events for the future work surface UI and debugging systems.

## Architecture

Future modules:

- `agent/streaming.py`: event normalizer and router.
- `observability/tracing.py`: trace IDs and correlation helpers.
- `observability/redaction.py`: payload redaction helpers.
- `app/streams.py`: future API streaming adapter.

Use LangGraph v2 stream format with `subgraphs=True` so subagent events are visible.

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

## Unit Tests

- Normalize main-agent update chunks.
- Normalize subagent namespace chunks.
- Normalize tool call and tool result message chunks.
- Redact secrets in args and payloads.
- Preserve trace/task correlation across events.
- Gracefully handle malformed chunks.

## Edge Cases

- Missing namespace.
- Unknown stream mode.
- Subagent event arrives before task metadata.
- Tool result exceeds stream size limit.
- Summarization event leaks into user-facing output.

