# Subagents and Delegation

How the agent spawns and manages a fleet of subagents to execute parallel or
specialised tasks, and how subagent results, citations, and lifecycle events flow
back to the parent conversation.

See also:

- [features/citations.md](citations.md) — subagents share the parent citation namespace
- [features/approvals.md](approvals.md) — subagent-scoped approval interrupts
- [features/memory-context.md](memory-context.md) — subagent memory isolation
- [diagrams/flows/f5-citations.puml](../architecture/diagrams/flows/f5-citations.puml)
- [architecture/diagrams/clusters/09-delegation.puml](../architecture/diagrams/clusters/09-delegation.puml)

---

## What it does

The Deep Agents graph supports delegation: the parent model emits a task to one or
more subagents. Each subagent is a lightweight ephemeral agent that runs its own
LangGraph graph instance with the same capability set (tools, MCP, skills) but a
different goal and context. Subagents emit their own stream events tagged with
`subagent_id` and `task_id`. Results are collected by the parent, which synthesises
a final response.

---

## Key modules

| File                                                    | Role                                                                                                |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `agent_runtime/delegation/subagents/runner.py`          | `SubagentFleetRunner` — launches, monitors, and collects results from all subagents                 |
| `agent_runtime/delegation/subagents/handoff.py`         | `SubagentHandoff` — prepares the context and tool set for a subagent run                            |
| `agent_runtime/delegation/subagents/atlas_task_tool.py` | `AtlasTaskTool` — model-facing delegation primitive; called by the parent graph                     |
| `agent_runtime/delegation/subagents/definitions.py`     | `SubagentDefinition`, `SubagentResult` — contracts for a delegation task                            |
| `agent_runtime/delegation/subagents/contracts.py`       | `SubagentTask`, `SubagentStatus`                                                                    |
| `agent_runtime/context/memory/subagent_trace.py`        | `SubagentArtifactsBackend` — persists subagent summaries for parent context                         |
| `runtime_api/schemas/events.py`                         | `SUBAGENT_FLEET_STARTED`, `SUBAGENT_STARTED`, `SUBAGENT_PROGRESS`, `SUBAGENT_COMPLETED` event types |

---

## Delegation flow

1. Parent model emits a tool call to `AtlasTaskTool` with `{goal, constraints, …}`.
2. `AtlasTaskTool.ainvoke()` calls `SubagentFleetRunner.run()`.
3. `SubagentFleetRunner` emits `SUBAGENT_FLEET_STARTED` and one `SUBAGENT_STARTED` per task.
4. For each subagent task, `SubagentHandoff.prepare(task, parent_context)` resolves:
   - A subagent-scoped `AgentRuntimeContext` (same `org_id`, `conversation_id`, new `run_id`).
   - A capability set from the parent's deps (same tool registry, MCP registry, skills).
   - A restricted memory view (see [features/memory-context.md](memory-context.md)).
5. Each subagent runs its own `acreate_agent_runtime()` → `astream_runtime()` loop.
   All events are tagged with `subagent_id` and `parent_task_id`.
6. As the subagent streams: `SUBAGENT_PROGRESS` events surface tool calls and intermediate
   results. `SUBAGENT_COMPLETED` (or `SUBAGENT_FAILED`) fires when the subagent finishes.
7. `SubagentFleetRunner` collects all `SubagentResult` objects and returns them to the
   parent graph.
8. The parent model receives the results and synthesises the final answer.

---

## Shared citation namespace

Subagents **inherit** the parent conversation's `CitationLedger` and
`ConversationOrdinalAllocator`. When a subagent calls an MCP tool, the source is
registered in the parent conversation's namespace with a conversation-scoped ordinal.
This means `[[N]]` in a subagent's result text refers to the same ordinal as `[[N]]`
in the parent's response. There is no per-subagent citation registry.

---

## Event tagging

All events emitted by a subagent carry:

- `subagent_id` — stable id for this subagent instance
- `task_id` — which delegation task it belongs to
- `parent_event_id` — links to the `SUBAGENT_FLEET_STARTED` envelope

The frontend uses these to group subagent events under the correct delegation card
in the timeline.

---

## Subagent-scoped approvals

If a subagent hits a LangGraph interrupt (e.g. MCP auth required), the approval row's
`parent_task_id` is set to the subagent's task id. `RuntimeApprovalHandler` uses this
to route the resume to the correct subagent graph node. The parent fleet runner holds
the subagent's future until the approval is resolved.

---

## `SubagentStorePort`

Persists `SubagentRunRecord` rows: `subagent_id`, `task_id`, `parent_run_id`,
`conversation_id`, `status`, `started_at`, `completed_at`, `result_summary`.

`SubagentArtifactsBackend` reads these to assemble the subagent trace injected
into the parent's context on the next turn (so the parent "remembers" what each
subagent found).

---

## Context token budget for subagents

`ConversationContextBuilder` (`agent_runtime/api/usage_service.py`) collapses
subagent rows up to the supervisor before reporting per-task token breakdowns.
The `/context` endpoint returns `per_subagent_breakdown` so the frontend can show
which subagents consumed the most context.
