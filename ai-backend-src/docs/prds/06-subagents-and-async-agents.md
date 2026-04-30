# PRD: Subagents and Async Agents

## Problem

Complex enterprise tasks often require parallel research, connector-heavy investigation, or specialized reasoning. If the supervisor agent does all work directly, its context fills with tool calls and intermediate outputs.

## Goal

Support sync and async subagents that receive compact task handoffs and return concise responses plus execution and plan summaries.

## User Value

- Users can ask broader questions without waiting on one serial agent.
- The UI can show delegated work clearly.
- The supervisor remains focused and avoids context bloat.

## Scope

- Subagent catalog with names, descriptions, graph IDs, skills, tools, and transport.
- `SubagentTask` contract for compact handoffs.
- `SubagentResult` contract for final response, execution summary, plan summary, artifacts, and optional recent messages.
- Async task lifecycle: start, check, update, cancel, list.
- Co-deployed ASGI transport first; remote HTTP later.

## Non-Goals

- Sending full conversation history to subagents by default.
- Infinite subagent nesting.
- Background jobs without observable lifecycle state.

## Acceptance Criteria

- Subagent descriptions are specific enough for selection.
- Handoffs contain summaries and constraints, not raw chat dumps.
- Async task IDs are stored outside message history.
- Results include what the subagent did, not just the final answer.
- Timeouts, cancellation, and stale IDs are defined.

## Edge Cases

- Subagent unavailable.
- Async task launched then context is summarized.
- User updates task while it is running.
- Subagent returns oversized output.
- Subagent result lacks required summary fields.

## Unit Testing Requirements

- Validate `SubagentTask` and `SubagentResult`.
- Assert full conversation history is not included by default.
- Simulate start/check/update/cancel/list lifecycle.
- Test timeout, stale task ID, malformed result, and oversized result handling.

