# Cluster: `agent_runtime/capabilities/`

**Total: 5,893 LOC across 25 files.** Largest cluster. Sub-divided into three sub-clusters: `tools/` (built-in callables + dynamic tool cards), `mcp/` (MCP server discovery, loading, OAuth-style auth, middleware), and `skills/` (virtual SKILL.md skills + visibility policy).

This is the **permission and capability layer** of the agent runtime. It decides which tools, MCP servers, and skills the model can see and call, and enforces least-privilege at the boundary between the LLM and the rest of the system. Most of the bespoke "policy" logic in the codebase lives here.

## Role in the request lifecycle

When a run is created, the [execution factory](../execution/_index.md) calls into capability loaders to build the agent's tool / MCP / skill exposure based on the caller's `org_id`, agent type (main vs subagent), and the run's configured allowlist. Tool cards (compact descriptors visible to the model) are filtered by `permissions.py` modules in each sub-cluster. When the model calls `load_tool`, `mcp_load`, or a skill-loader, the loader fetches the full spec, validates it, and wires it into the live LangGraph state. The middleware modules (`mcp/middleware/auth_mcp.py`, `mcp/middleware/call_tool.py`, `skills/middleware.py`) intercept tool invocations to enforce auth + permission before the call escapes the runtime.

## Files in this cluster

### `tools/` — built-in callables and dynamic tool cards (8 files, 1,177 LOC)

| File                                                                                                                                                                                                                  | LOC | Doc                                |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --: | ---------------------------------- |
| [`tools/cards.py`](../../../services/ai-backend/src/agent_runtime/capabilities/tools/cards.py) — Pydantic contracts for dynamic tool cards and loaded tool specifications.                                            | 298 | [tools-bundle.md](tools-bundle.md) |
| [`tools/builtin/ask_a_question.py`](../../../services/ai-backend/src/agent_runtime/capabilities/tools/builtin/ask_a_question.py) — Built-in tool that pauses the agent to ask the human user a question with options. | 218 | [tools-bundle.md](tools-bundle.md) |
| [`tools/loader.py`](../../../services/ai-backend/src/agent_runtime/capabilities/tools/loader.py) — Lazy full-spec loader for dynamically selected tools with validation.                                              | 162 | [tools-bundle.md](tools-bundle.md) |
| [`tools/registry.py`](../../../services/ai-backend/src/agent_runtime/capabilities/tools/registry.py) — Provider-backed registry for compact dynamic tool cards with authorization filtering.                          | 149 | [tools-bundle.md](tools-bundle.md) |
| [`tools/constants.py`](../../../services/ai-backend/src/agent_runtime/capabilities/tools/constants.py) — Shared keys, limits, patterns, and public messages for dynamic tools.                                        | 139 | [tools-bundle.md](tools-bundle.md) |
| [`tools/prior_results.py`](../../../services/ai-backend/src/agent_runtime/capabilities/tools/prior_results.py) — Model-facing tool for loading prior persisted tool observations from conversation context.           |  87 | [tools-bundle.md](tools-bundle.md) |
| [`tools/builtin/load_tool.py`](../../../services/ai-backend/src/agent_runtime/capabilities/tools/builtin/load_tool.py) — Built-in callable that lets the model lazily load full tool specs.                           |  72 | [tools-bundle.md](tools-bundle.md) |
| [`tools/permissions.py`](../../../services/ai-backend/src/agent_runtime/capabilities/tools/permissions.py) — Shared authorization helpers for dynamic tool loading and card visibility.                               |  52 | [tools-bundle.md](tools-bundle.md) |

### `mcp/` — MCP server discovery, loading, OAuth-style auth (11 files, 2,327 LOC)

| File                                                                                                                                                                                                    | LOC | Doc                                                             |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --: | --------------------------------------------------------------- |
| [`mcp/cards.py`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/cards.py) — Pydantic contracts for dynamic MCP server loading and tool descriptors.                                    | 571 | [mcp-cards.md](mcp-cards.md) (standalone, L)                    |
| [`mcp/loader.py`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/loader.py) — Explicit loader for dynamically selected MCP servers with health and connection handling.                | 391 | [mcp-loader.md](mcp-loader.md) (standalone)                     |
| [`mcp/backend_provider.py`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py) — Backend-backed MCP provider for production registry integration with authentication. | 360 | [mcp-backend-provider.md](mcp-backend-provider.md) (standalone) |
| [`mcp/constants.py`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/constants.py) — Constants and message factories for dynamic MCP loading and validation.                            | 312 | [mcp-bundle.md](mcp-bundle.md)                                  |
| [`mcp/registry.py`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/registry.py) — Provider-backed registry for compact MCP server cards with authorization filtering.                  | 155 | [mcp-bundle.md](mcp-bundle.md)                                  |
| [`mcp/middleware/call_tool.py`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py) — Model-facing tool that invokes a selected MCP tool after discovery.          | 138 | [mcp-bundle.md](mcp-bundle.md)                                  |
| [`mcp/middleware/auth_mcp.py`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py) — Model-facing tool that requests user authentication for an MCP server.         | 134 | [mcp-bundle.md](mcp-bundle.md)                                  |
| [`mcp/middleware/dynamic_loader.py`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/dynamic_loader.py) — Built-in callable that lets the model explicitly load MCP servers. |  82 | [mcp-bundle.md](mcp-bundle.md)                                  |
| [`mcp/__init__.py`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/__init__.py) — Dynamic MCP loading primitives and public API surface.                                               |  73 | [mcp-bundle.md](mcp-bundle.md)                                  |
| [`mcp/client.py`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/client.py) — Protocol boundaries for MCP client adapters and error definitions.                                       |  68 | [mcp-bundle.md](mcp-bundle.md)                                  |
| [`mcp/permissions.py`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/permissions.py) — Shared authorization helpers for dynamic MCP loading and card visibility.                      |  43 | [mcp-bundle.md](mcp-bundle.md)                                  |

### `skills/` — virtual Markdown skills (6 files, 1,221 LOC)

| File                                                                                                                                                                                 | LOC | Doc                                  |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --: | ------------------------------------ |
| [`skills/manifest.py`](../../../services/ai-backend/src/agent_runtime/capabilities/skills/manifest.py) — Agent Skills-compatible SKILL.md manifest parsing and validation.           | 400 | [skills-bundle.md](skills-bundle.md) |
| [`skills/sources.py`](../../../services/ai-backend/src/agent_runtime/capabilities/skills/sources.py) — Configured skill source paths and deterministic source precedence.            | 211 | [skills-bundle.md](skills-bundle.md) |
| [`skills/virtual.py`](../../../services/ai-backend/src/agent_runtime/capabilities/skills/virtual.py) — Virtual, backend-backed Skill registry for user-created Markdown skills.      | 203 | [skills-bundle.md](skills-bundle.md) |
| [`skills/policy.py`](../../../services/ai-backend/src/agent_runtime/capabilities/skills/policy.py) — Access policy for main-agent and subagent skill visibility enforcement.         | 171 | [skills-bundle.md](skills-bundle.md) |
| [`skills/constants.py`](../../../services/ai-backend/src/agent_runtime/capabilities/skills/constants.py) — Shared keys, limits, patterns, and public messages for skills middleware. | 151 | [skills-bundle.md](skills-bundle.md) |
| [`skills/middleware.py`](../../../services/ai-backend/src/agent_runtime/capabilities/skills/middleware.py) — Model-facing tools for virtual Skill loading and on-demand retrieval.   |  86 | [skills-bundle.md](skills-bundle.md) |

## Doc layout

- [mcp-cards.md](mcp-cards.md) — `mcp/cards.py` (L, 571)
- [mcp-loader.md](mcp-loader.md) — `mcp/loader.py` (M, 391)
- [mcp-backend-provider.md](mcp-backend-provider.md) — `mcp/backend_provider.py` (M, 360)
- [mcp-bundle.md](mcp-bundle.md) — remaining `mcp/*` (registry, client, constants, permissions, middleware/\*)
- [skills-bundle.md](skills-bundle.md) — all `skills/*`
- [tools-bundle.md](tools-bundle.md) — all `tools/*`

## Cross-cluster dependencies

**Imports from:**

- [`agent_runtime/api/`](../agent-api/_index.md) — for typed contracts and constants
- [`agent_runtime/persistence/`](../persistence/_index.md) — for skill/tool/MCP record types
- `service-contracts` (constants-only shared package)
- HTTP backend client (production MCP/skill providers)
- LangGraph + Deep Agents SDK for tool wiring

**Imported by:**

- [`agent_runtime/execution/`](../execution/_index.md) — when assembling per-run tool/skill/MCP exposure
- [`runtime_worker/handlers/run.py`](../runtime-worker/handlers-run.md) — when middleware fires during a run
- [`agent_runtime/delegation/subagents/`](../delegation-subagents/_index.md) — for subagent capability narrowing

## Use-case relevance

- [06-mcp-installed-not-authenticated.md](../../use-cases/06-mcp-installed-not-authenticated.md) — `mcp/middleware/auth_mcp.py` is the entry point.
- [03-tool-call-with-approval.md](../../use-cases/03-tool-call-with-approval.md) — `tools/builtin/ask_a_question.py` and `mcp/middleware/call_tool.py`.
- [04-ask-a-question-single.md](../../use-cases/04-ask-a-question-single.md) / [05-ask-a-question-consecutive.md](../../use-cases/05-ask-a-question-consecutive.md).
