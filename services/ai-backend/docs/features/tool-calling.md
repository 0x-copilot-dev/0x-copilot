# Tool Calling

How built-in tools and MCP tools are loaded, permissioned, and invoked during a run.
Covers the tool registry, MCP server discovery, and the middleware chain.

See also:

- [features/citations.md](citations.md) — citation projection in MCP tool results
- [features/approvals.md](approvals.md) — MCP auth interrupt when a tool is called on an unauthed server
- [diagrams/flows/f2-multi-turn-tool.puml](../architecture/diagrams/flows/f2-multi-turn-tool.puml)
- [diagrams/flows/f7-mcp-add.puml](../architecture/diagrams/flows/f7-mcp-add.puml)
- [guides/add-builtin-tool.md](../guides/add-builtin-tool.md)
- [guides/add-mcp-middleware.md](../guides/add-mcp-middleware.md)

---

## What it does

The agent factory (`execution/factory.py`) assembles a tool set for each run at
run-start time. There is no tool caching across runs. Two categories of tools exist:

1. **Built-in tools** — Python callables compiled into `agent_runtime/`. Always available
   (subject to permission policy). Examples: `ask_a_question`, `load_tool`, `suggest_mcp_connector`.
2. **MCP tools** — tools exposed by MCP servers registered in `backend`. Discovered at
   run-start via an HTTP call to `backend`'s internal API. Each server's tool list is
   fetched fresh every run.

---

## Key modules

| File                                                          | Role                                                             |
| ------------------------------------------------------------- | ---------------------------------------------------------------- |
| `agent_runtime/capabilities/tools/registry.py`                | `DynamicToolRegistry` — holds all loaded tool specs              |
| `agent_runtime/capabilities/tools/loader.py`                  | `ToolLoader` — resolves a tool name → async callable             |
| `agent_runtime/capabilities/tools/cards.py`                   | `ToolCard`, `ToolDisplayTemplate` — metadata for UI presentation |
| `agent_runtime/capabilities/tools/permissions.py`             | `ToolPermissionChecker` — auth/org/scope gates                   |
| `agent_runtime/capabilities/tools/builtin/`                   | Three built-in tool implementations                              |
| `agent_runtime/capabilities/mcp/registry.py`                  | `DynamicMcpRegistry` — queries `backend` for MCP servers         |
| `agent_runtime/capabilities/mcp/loader.py`                    | `McpLoader` — fetches tool list from each MCP server             |
| `agent_runtime/capabilities/mcp/client.py`                    | `McpClient` — dispatches `call_tool` RPC to a server             |
| `agent_runtime/capabilities/mcp/middleware/call_tool.py`      | Main MCP middleware: permission, auth, citation, retry           |
| `agent_runtime/capabilities/mcp/middleware/auth_mcp.py`       | `AuthMcpTool` — LangGraph interrupt for auth required            |
| `agent_runtime/capabilities/mcp/middleware/cite_mcp.py`       | `CitationProjectingMcpMiddleware` — MCP result → citation        |
| `agent_runtime/capabilities/mcp/middleware/dynamic_loader.py` | `DynamicMcpLoader` — loads MCP tools into registry at run-start  |

---

## Run-start tool assembly (`execution/factory.py`)

`acreate_agent_runtime()` builds the `RuntimeHarness` for each run:

1. `DynamicToolRegistry.load_builtin_tools()` — register all three built-in tools.
2. `DynamicMcpRegistry.list_available_servers(context)` — HTTP `GET /internal/v1/mcp/servers`
   to `backend`. Returns `McpServerCard[]` filtered by org, scopes, and
   `McpPermissionPolicy.is_server_card_visible`.
3. For each visible server: `McpLoader.fetch_tools(server_card)` — calls
   `backend`'s MCP proxy to list tools from the remote MCP server. Adds each tool
   as an `McpToolCard` to the registry.
4. `SkillManifest` — skill system-prompt injections (separate from tool list).
5. Registry is frozen and passed into `AgentRuntimeContext.tool_registry`.

**No caching across runs.** A server added at T=10s is visible in a run at T=11s because
the next `acreate_agent_runtime` call re-queries `backend`. This is intentional.

---

## Built-in tools

`agent_runtime/capabilities/tools/builtin/`

| Tool name               | File                       | What it does                                                                     |
| ----------------------- | -------------------------- | -------------------------------------------------------------------------------- |
| `ask_a_question`        | `ask_a_question.py`        | Emits an `APPROVAL_REQUESTED` interrupt; waits for user answer before continuing |
| `load_tool`             | `load_tool.py`             | Loads a prior tool result by tool_call_id (useful for long contexts)             |
| `suggest_mcp_connector` | `suggest_mcp_connector.py` | Emits a catalog suggestion card so the user can install an MCP connector in-chat |

---

## MCP tool dispatch — middleware chain

When the LangGraph graph calls an MCP tool, it goes through this chain (innermost to outermost):

```
McpClient.call_tool()           ← actual RPC to backend → MCP server
  ↑ wrapped by
CitationProjectingMcpMiddleware ← projects result items into SourceRefs
  ↑ wrapped by
ToolBudgetMiddleware            ← checks per-run tool budget cap
  ↑ wrapped by
RetryingTool                    ← retries on transient MCP errors
  ↑ wrapped by
CallMcpTool                     ← permission gate; routes to AuthMcpTool if unauthed
```

**Permission gate in `CallMcpTool`:**
`McpPermissionPolicy.is_tool_authorized(server_card, context)` checks:

- `auth_state` — if `NONE` or `EXPIRED`, routes to `AuthMcpTool` instead of calling the tool.
- `scope` — user must have been granted the required scope.
- Tenant isolation — `org_id` must match the server's owning org.

---

## Tool budget

`agent_runtime/capabilities/tool_budget_middleware.py`, `tool_budget_guard.py`

Each run has a per-run tool invocation cap (default 5, configurable per workspace).
`ToolBudgetMiddleware` decrements the counter before each tool call. On exhaustion it
raises `BudgetExceeded`, which `StreamOrchestrator` converts to a safe `BUDGET_WARNING`
event and terminates the tool call cleanly.

`ToolBudgetGuard` is the `ContextVar`-bound singleton for the current run.

---

## MCP server auth states

| `auth_state` | Meaning                                                                                |
| ------------ | -------------------------------------------------------------------------------------- |
| `NONE`       | Server installed but not authenticated. Calling any tool on it triggers the auth flow. |
| `PENDING`    | Auth session created; waiting for user to complete OAuth.                              |
| `VALID`      | Token present and not expired. Tools can be called.                                    |
| `EXPIRED`    | Token expired. Next tool call re-triggers auth.                                        |

See [features/approvals.md](approvals.md) for the full auth interrupt → resume flow.

---

## Tool display metadata

`agent_runtime/capabilities/tools/cards.py`, `agent_runtime/api/presentation.py`

Each tool has a `ToolDisplayTemplate` (icon, label, subtitle). These are projected into
the `RuntimeEventEnvelope.presentation` field by `RuntimeEventPresentationProjector`
at event-write time. The frontend never inspects raw tool names to generate labels.

---

## Security invariant

`ToolPermissionChecker.is_card_authorized()` is called for every tool load and cannot be
bypassed. A tool that fails the permission check is not added to the model's tool list.
An MCP tool that fails the permission check at call time (race condition where scope was
revoked mid-run) returns an error result without leaking internal detail.
