# Approvals

How the system handles mid-run interrupts — MCP authentication, human-in-the-loop tool
approval, and draft-send confirmation — including the LangGraph interrupt, the durable
approval row, and worker resume.

See also:

- [features/tool-calling.md](tool-calling.md) — MCP permission gate that triggers auth
- [diagrams/flows/f8-mcp-auth.puml](../architecture/diagrams/flows/f8-mcp-auth.puml)

---

## What it does

Some operations cannot proceed without user input: an MCP server needs OAuth tokens, a
risky tool needs user confirmation, a draft message needs final approval before send.
When one of these occurs mid-run:

1. The worker emits an event (`MCP_AUTH_REQUIRED`, `APPROVAL_REQUESTED`, …) that
   carries enough info for the frontend to render an action card.
2. The LangGraph graph executes `langgraph_interrupt()` — this suspends the graph
   mid-node and persists the checkpoint.
3. Run status transitions to `AWAITING_APPROVAL`. The worker does **not** call
   `queue.mark_complete()`.
4. The user responds via `POST /v1/approvals/{approval_id}/decision`.
5. A `RuntimeApprovalResolvedCommand` is enqueued; the worker claims it and resumes
   the graph from the checkpoint.

The approval row is the durable rendezvous between the user's click and the worker
process — it survives worker restarts and SSE disconnects.

---

## Key modules

| File                                                    | Role                                                                                |
| ------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `agent_runtime/api/approval_coordinator.py`             | `ApprovalCoordinator` — create, resolve, list approval records                      |
| `runtime_worker/handlers/approval.py`                   | `RuntimeApprovalHandler` — loads checkpoint, resumes executor                       |
| `runtime_worker/handlers/run.py`                        | Detects `action_interrupted=True` → updates run status                              |
| `runtime_worker/stream_events.py`                       | `StreamOrchestrator` — emits approval events from stream chunks                     |
| `runtime_worker/approval_recognisers.py`                | `ApprovalParamRecogniserRegistry` — pattern-match chunks → `ApprovalParam`          |
| `agent_runtime/capabilities/mcp/middleware/auth_mcp.py` | `AuthMcpTool` — issues auth session, calls `langgraph_interrupt()`                  |
| `runtime_api/http/routes.py`                            | `POST /v1/approvals/{id}/decision` endpoint                                         |
| `runtime_api/schemas/approvals.py`                      | `ApprovalRequestRecord`, `ApprovalDecision`, `ApprovalParam`, `McpApprovalMetadata` |

---

## Approval categories

| `approval_kind`     | Trigger                                                                      | Emitted event        |
| ------------------- | ---------------------------------------------------------------------------- | -------------------- |
| `mcp_auth`          | `McpPermissionPolicy` — tool called on server with `auth_state=NONE/EXPIRED` | `MCP_AUTH_REQUIRED`  |
| `mcp_tool_approval` | Tool marked `requires_approval=true` in server descriptor                    | `APPROVAL_REQUESTED` |
| `ask_a_question`    | `ask_a_question` built-in tool invoked                                       | `APPROVAL_REQUESTED` |
| `draft_send`        | User confirms a draft message before send                                    | `APPROVAL_REQUESTED` |

---

## LangGraph interrupt mechanism

`agent_runtime/capabilities/mcp/middleware/auth_mcp.py`

```python
from langgraph.types import interrupt as langgraph_interrupt

resume = self.interrupt_handler(payload)
```

`langgraph_interrupt(payload)` is a LangGraph primitive that:

1. Serialises the graph's current checkpoint to `CheckpointStorePort`.
2. Raises an internal `GraphInterrupt` exception that propagates up through
   `astream_runtime()` back to `StreamingExecutor`.
3. `StreamingExecutor` catches it, sets `result.action_interrupted = True`.
4. `RuntimeRunHandler` sees `action_interrupted=True` → calls
   `persistence.update_run_status(AWAITING_APPROVAL)`.
5. `RuntimeRunHandler` does **not** call `queue.mark_complete()` — the claim lock stays.

---

## Approval row lifecycle

`runtime_api/schemas/approvals.py` — `ApprovalRequestRecord`

| Field            | Notes                                                           |
| ---------------- | --------------------------------------------------------------- |
| `approval_id`    | UUID; used in `POST /v1/approvals/{id}/decision`                |
| `run_id`         | Which run is paused                                             |
| `approval_kind`  | `mcp_auth`, `mcp_tool_approval`, `ask_a_question`, `draft_send` |
| `status`         | `PENDING` → `APPROVED` or `DENIED` → `RESOLVED`                 |
| `payload`        | Kind-specific data (auth URL, question text, draft content, …)  |
| `parent_task_id` | Set for subagent-scoped pauses; used by resume handler to route |
| `expires_at`     | Swept by `approval_expiry_sweeper` job                          |

---

## Resume path (`RuntimeApprovalHandler`)

`runtime_worker/handlers/approval.py`

1. Claim the `RuntimeApprovalResolvedCommand` from the queue.
2. Load the run record and approval row.
3. Call `acreate_agent_runtime(context, deps)` — same factory as `RuntimeRunHandler`.
4. Resume via `astream_runtime_resume()` (`execution/runtime.py`) — loads the checkpoint
   and passes the approval decision as the `resume` value to the LangGraph graph.
5. The graph continues from the interrupted node. `AuthMcpTool.ainvoke()` returns
   `self._resume_result(session, resume)` and the tool call proceeds (or is skipped
   if denied).
6. `StreamingExecutor` continues emitting events normally → `FINAL_RESPONSE` →
   `RUN_COMPLETED`.

---

## MCP auth interrupt flow (detailed)

See flow diagram [f8-mcp-auth](../architecture/diagrams/flows/f8-mcp-auth.puml) for the
full sequence. In brief:

1. `CallMcpTool` detects `auth_state=NONE` → routes to `AuthMcpTool`.
2. `AuthMcpTool.ainvoke()` calls `backend`'s `/internal/v1/mcp/servers/{id}/auth/start`
   to get an `McpAuthSession` (auth URL + session id).
3. Inserts the approval row. Emits `MCP_AUTH_REQUIRED` event (carries `auth_url` for
   the frontend to render the "Connect X" card).
4. Calls `langgraph_interrupt(payload)` — graph suspends.
5. User completes OAuth in a browser popup. Backend stores tokens via `TokenVault`.
6. Frontend calls `POST /v1/approvals/{id}/decision` with `decision=APPROVE`.
7. Worker resumes. `McpPermissionPolicy` now sees `auth_state=VALID` → allows the tool.

Token rotation and expiry are owned by `backend`'s token vault. If tokens expire mid-run,
the same `MCP_AUTH_REQUIRED` → `APPROVAL_RESOLVED` loop fires again.

---

## Subagent-scoped approvals

When a subagent triggers an interrupt, the approval row's `parent_task_id` is set to
the subagent's task id. The `RuntimeApprovalHandler` checks `parent_task_id` to route
the resume to the correct subagent graph node rather than the top-level conversation graph.

---

## Approval expiry

`runtime_worker/jobs/approval_expiry_sweeper.py`

Background job that runs periodically. Sweeps approval rows past their `expires_at`
timestamp with status still `PENDING` → transitions them to `EXPIRED`. Emits a
`APPROVAL_EXPIRED` event so the frontend can un-render the action card.
