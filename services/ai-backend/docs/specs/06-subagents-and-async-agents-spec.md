# Spec: Subagents and Async Agents

## Purpose

Define sync and async subagent delegation so the supervisor can isolate context-heavy work and manage long-running tasks.

## Architecture

Implemented modules:

- `agent_runtime/delegation/subagents/definitions.py`: subagent catalog and descriptions.
- `agent_runtime/delegation/subagents/handoff.py`: task creation, runtime context references, and supervisor handoff rules.
- `agent_runtime/delegation/subagents/contracts.py`: sync and async task/result contracts.
- `agent_runtime/delegation/subagents/runner.py`: protocol for sync and async execution plus async lifecycle operations.
- `agent_runtime/execution/graph.py`: graph exports for supervisor and co-deployed subagents.

Start with co-deployed ASGI async subagents registered in `langgraph.json`. Add remote HTTP transport when scaling or ownership requires it.

## Pydantic Contracts

Required models:

- `SubagentDefinition`: name, description, graph ID, transport, tools, skills, timeout, concurrency limit.
- `SubagentTask`: objective, relevant summary, constraints, runtime context reference, allowed tools, allowed skills, output contract.
- `AsyncTaskState`: task ID, subagent name, thread ID, run ID, status, timestamps.
- `SubagentResult`: response, execution summary, plan summary, artifacts, optional recent messages, error.

## Design Rules

- Do not pass full multi-turn conversation history by default.
- Store async task metadata in dedicated state, not only tool messages.
- Subagent descriptions must be specific and action-oriented.
- Results must explain what was done, not just the answer.
- Avoid broad subagent types that become untestable catch-alls.

## Unit Tests

- Handoff builder excludes raw conversation unless explicitly allowed.
- Validate required summary and objective fields.
- Simulate async start/check/update/cancel/list lifecycle.
- Reject malformed subagent results.
- Ensure stale task IDs and cancelled tasks are handled deterministically.

## Edge Cases

- Subagent launch queues because worker pool is exhausted.
- Supervisor polls immediately after async launch.
- Task ID is truncated by model output.
- Subagent returns oversized raw data.
- User updates task while previous run is active.

