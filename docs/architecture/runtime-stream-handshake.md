# Runtime Stream Handshake

This document is the frontend/backend contract for chat streaming. The frontend
consumes `RuntimeEventEnvelope` records from `backend-facade` over `/v1/*`; it
must not parse raw Deep Agents, LangGraph, provider, or tool framework chunks.

## Backend Responsibilities

`services/ai-backend` uses Deep Agents/LangGraph v2 streaming with:

```text
stream_mode=["messages", "updates", "custom", "values"]
subgraphs=true
version="v2"
```

The worker translates those chunks into stable runtime events before persistence
and SSE fanout. Raw stream chunks are internal. Unknown `updates` or `custom`
payloads must be dropped unless they can be projected into one of the stable
event types below with product-safe fields.

## Stable Event Families

- Run lifecycle: `run_queued`, `run_started`, `run_completed`, `run_failed`,
  `run_cancelled`.
- Assistant output: `model_delta` while text is streaming, then
  `final_response` with the reconciled assistant message.
- Reasoning summaries: `reasoning_summary` and `reasoning_summary_delta`.
  These are safe summaries only, never raw chain-of-thought or hidden
  scratchpad content.
- Tools: `tool_call_started`, `tool_call_delta`, `tool_result`,
  `tool_call_completed`, grouped by `call_id`.
- Subagents: `subagent_started`, `subagent_progress`, `subagent_completed`,
  grouped by `task_id`, `parent_task_id`, or `subagent_id`.
- Gated actions: `mcp_auth_required`, `approval_requested`,
  `approval_resolved`.

Every event includes UI projection fields:

```text
event_type
activity_kind
status
display_title
summary
span_id
parent_task_id
task_id
subagent_id
payload
```

The frontend should render from these fields, not from provider-specific
payload structure.

## Frontend Responsibilities

`apps/frontend` should:

- Concatenate `model_delta.payload.delta` into the live assistant message.
- Reconcile the live assistant message with `final_response.payload.message`.
- Render assistant output with Streamdown in streaming mode until the run is
  terminal.
- Render reasoning summaries in the activity panel using Streamdown, scoped to
  the main agent or subagent based on `parent_task_id` / `subagent_id`.
- Render tool calls and results by `call_id`.
- Render subagent tabs by `task_id` / `subagent_id`.
- Ignore events it does not understand.

## Non-Goals

- The frontend does not receive raw Deep Agents v2 `StreamPart` chunks.
- The frontend does not infer event meaning from LangGraph namespace strings.
- Raw provider reasoning, hidden chain-of-thought, prompts, and scratchpad state
  are never client-visible payloads.
