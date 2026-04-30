# PRD: Streaming and Observability

## Problem

Agentic workflows can feel opaque. Users and admins need to see progress, tool calls, subagent activity, and failures without reading raw LangGraph event structures.

## Goal

Normalize Deep Agents and LangGraph v2 stream chunks into product-ready stream events and observability records.

## User Value

- Users see what the system is doing while it works.
- Long-running subagent tasks feel manageable and interruptible.
- Developers can debug traces, context compression, tool loading, and MCP failures.

## Scope

- Normalized `StreamEvent` contract.
- Main-agent and subagent event routing.
- Tool call, tool result, custom progress, lifecycle, and final response events.
- Trace IDs and correlation IDs for LangSmith or future observability.
- Filtering of internal summarization tokens from user-facing streams.

## Non-Goals

- Building a complete frontend UI in this backend spec.
- Streaming secrets, raw credentials, or unredacted connector payloads.
- Coupling event consumers to raw LangGraph chunk format.

## Acceptance Criteria

- Stream events have stable event types and source fields.
- Subagent events identify their parent task.
- Sensitive payloads are redacted before emission.
- Event normalizer is unit-testable with fake chunks.
- UI consumers do not need to parse raw `ns` tuples.

## Edge Cases

- Unknown event type.
- Missing namespace.
- Subagent emits events before supervisor records task metadata.
- Summarization tokens appear in message stream.
- Tool result is too large to stream directly.

## Unit Testing Requirements

- Normalize main-agent, subagent, tool call, tool result, and custom events.
- Redact sensitive fields.
- Handle malformed chunks without crashing.
- Preserve trace correlation fields.

