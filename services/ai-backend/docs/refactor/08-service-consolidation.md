# Refactor PRD — Service Consolidation (P9) — **RETRACTED**

**Status:** Retracted in full (rewritten 2026-05-11 after reading the code).
**Author:** architecture audit, May 2026 — re-evaluated 2026-05-11.
**Tracks:** [refactor-audit §2.3](../architecture/refactor-audit.md#23-four-way-permission-model-3-specific--1-generic), [§2.4](../architecture/refactor-audit.md#24-toolbudgetmiddleware--toolbudgetguard-two-step), [§2.6](../architecture/refactor-audit.md#26-service-splits-inside-c4-that-should-be-one-service-each)
**Roadmap:** [00-roadmap.md](00-roadmap.md) → P9

---

## Original claim

Five splits in `agent_runtime/api/` and `agent_runtime/capabilities/` "should be one thing each." Bundled into one PR.

## Disposition

**All six sub-items retracted.** Code-level review found that each split is justified by either distinct security postures, distinct surfaces, distinct lifecycles, or a clean policy-vs-interceptor separation. Nothing in P9 holds up under a staff-engineer code review.

| #   | Claim                                                           | Verdict                                            |
| --- | --------------------------------------------------------------- | -------------------------------------------------- |
| 1.1 | `ConversationFork` + `SelfFork` → merge                         | **Stay separate** — distinct security postures     |
| 1.2 | `WorkspaceFeedService` + `WorkspaceDefaultsService` → merge     | **Stay separate** — distinct surfaces              |
| 1.3 | `McpDiscoveryService` + `SuggestibleConnectorsResolver` → merge | **Stay separate** — distinct consumers             |
| 1.4 | `UsageService` "is a utility, fold into ContextBuilder"         | **Stay** — 492 LOC, not a utility                  |
| 1.5 | `ToolBudgetMiddleware` + `ToolBudgetGuard` → merge              | **Stay separate** — clean policy/interceptor split |
| 1.6 | Pick one permission model (3 specific + 1 generic)              | **Stay all four** — they solve different problems  |

---

## Why each retraction holds up

### 1.1 — `ConversationFork` + `SelfFork` are different security postures

[`api/conversation_fork.py`](../../src/agent_runtime/api/conversation_fork.py) (278 LOC) is the **share-based** fork: resolves a bearer share token via `ShareSnapshotPort`, enforces workspace + recipient gates, enforces cross-org opacity (returns 404 across tenants), emits `conversation.fork` audit + share-forked notification.

[`api/self_fork.py`](../../src/agent_runtime/api/self_fork.py) (238 LOC) is the **owner-only** fork in the same tenant: the caller owns the source conversation, no share, no recipient gate, no cross-org opacity, no share-forked notification.

The shared **mechanical** logic — slice messages, copy via `MessageCopyPlanner`, insert new conversation row, audit — is already extracted to [`MessageCopyPlanner`](../../src/agent_runtime/persistence/message_copy.py). The two services differ where they should: in _security_. Merging them would conflate the bearer-token gate with the owner-identity gate, which is exactly the kind of consolidation a security reviewer would push back on.

### 1.2 — Workspace feed vs defaults are different surfaces

[`api/workspace_feed_service.py`](../../src/agent_runtime/api/workspace_feed_service.py) (184 LOC) is **read-side aggregation** of workspace activity — sources, subagent snapshots, draft latest versions. Pure reads.

[`api/workspace_defaults_service.py`](../../src/agent_runtime/api/workspace_defaults_service.py) (334 LOC) is **read/write** of workspace-level default settings — model selection, connector enablement, behavior overrides. Mutates `agent_workspace_defaults` row state.

The two halves don't call each other. They share zero state. Merging produces one class whose two halves are unrelated.

### 1.3 — MCP discovery vs suggestible-connector resolver have different consumers

Per [audit flow f7](../architecture/f7-mcp-add.puml):

- [`api/mcp_discovery_service.py`](../../src/agent_runtime/api/mcp_discovery_service.py) (502 LOC): **per-run binding**, backs the `suggest_mcp_connector` builtin tool the _model_ invokes when a user asks about an uninstalled connector. Emits the inline approval-style card with audit trail.
- [`api/suggestible_connectors_resolver.py`](../../src/agent_runtime/api/suggestible_connectors_resolver.py) (244 LOC): **stateless per-run resolver** that feeds system-prompt hints, nudging the model toward suggesting connectors when relevant.

Different consumers (in-chat tool vs system prompt), different lifecycles (per-tool-call vs per-run), different surfaces (event-emitting vs prompt-injecting). Merging just produces a class with two entry points that never share state.

### 1.4 — `UsageService` is 492 LOC, not a utility

The audit claimed it was "just `headroom_pct`." Reading the file shows it owns **two distinct surfaces** (per [audit flow f9](../architecture/f9-usage-metrics.puml)):

- **In-chat `/context` builder.** Server-computed headroom for the active conversation; pure stateless `ConversationContextBuilder`.
- **`/v1/usage/*` rollups.** Per-user / per-org / per-connector rollups, with `_RollupBucket` aggregation, period parsing, sum-totals.

Two clearly-named internal facets (`ConversationContextBuilder`, `UsageQueryService`) inside one cohesive surface. Folding `UsageQueryService` into the `ConversationContextBuilder` would conflate "live conversation headroom" with "historical rollup queries" — totally different access patterns.

### 1.5 — Tool budget middleware/guard is a clean policy/interceptor split

Read the docstrings:

[`capabilities/tool_budget_middleware.py`](../../src/agent_runtime/capabilities/tool_budget_middleware.py) (203 LOC):

> _"Pure decision module. Given a snapshot of `runtime_tool_budgets` rows + the run's `ToolCallLedger`, the middleware decides whether to admit a tool call."_

Stateless. Returns `ToolBudgetAdmit | ToolBudgetWarn | ToolBudgetReject`. Trivial to unit-test in isolation.

[`capabilities/tool_budget_guard.py`](../../src/agent_runtime/capabilities/tool_budget_guard.py) (353 LOC):

> _"Bridges `ToolBudgetMiddleware` to the LangChain tool dispatch loop. Constructs one `ToolBudgetGuard` per run, binds it on a contextvars slot, registers each model-visible tool wrapped in `ToolBudgetGuardedTool`."_

Per-run state. ContextVar-bound. LangChain glue. Event emission.

Merging them is **strictly worse**: the policy loses its testability-in-isolation, and the guard becomes harder to read because the policy logic intrudes on the wiring.

### 1.6 — Four "permissions" solve four different problems

The audit treated "anything with the keyword 'permission' or 'auth'" as one concept. They aren't:

- [`tools/permissions.py`](../../src/agent_runtime/capabilities/tools/permissions.py) — `ToolPermissionPolicy` mirrors the **backend's three policy axes** (read / write / destructive) and four modes (auto / ask / require / ...). Decides which _tool cards_ the model sees, and which tool calls go through what mode.
- [`mcp/permissions.py`](../../src/agent_runtime/capabilities/mcp/permissions.py) — `McpPermissionPolicy.is_server_card_visible` decides which **MCP server cards** the model sees, based on health (HEALTHY / DEGRADED) plus auth state.
- [`skills/policy.py`](../../src/agent_runtime/capabilities/skills/policy.py) — `SkillAccessPolicy` is a **least-privilege** filter on skill visibility, applied separately for main-agent vs subagent — different agent classes get different skills.
- [`capabilities/auth_gate.py`](../../src/agent_runtime/capabilities/auth_gate.py) — `CapabilityAuthGate.check(target_connector, runtime_context)` answers _a different question entirely_: **"is `target_connector` reachable for the Workspace-pane draft send flow, right now?"** Used by `DraftService.send` as a pre-check and by the approval-resolution path as a re-check at dispatch time. Returns `AUTHENTICATED | NOT_AUTHENTICATED | UNKNOWN_CAPABILITY | WORKSPACE_DISABLED`.

The first three are **visibility filters** for distinct capability surfaces. The fourth is a **connector reachability gate** for the draft-send flow. They share a keyword and nothing else.

---

## Decision

P9 is retracted in full. The architecture is sound. The roadmap entry for P9 in [`00-roadmap.md`](00-roadmap.md) is marked **Retracted**.

---

## Lesson recorded

P9 is the third blanket retraction from the original audit, after [P14 — citations consolidation](11-citations-consolidation.md) and [P15 — worker streaming cleanup](12-worker-stream-cleanup.md). The pattern is consistent: file-count-based smells from a diagram-only review do not survive code-level review.

For future audit-driven PRDs: **read every file before promoting a "consolidate N files" claim from the audit doc to a PRD recommendation.** The audit doc's own preamble warns about this; subsequent PRDs need to honor that warning.
