# Refactor PRD — Service Consolidation (Phase 2)

**Status:** Draft
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §2.3](../architecture/refactor-audit.md#23-four-way-permission-model-3-specific--1-generic), [§2.4](../architecture/refactor-audit.md#24-toolbudgetmiddleware--toolbudgetguard-two-step), [§2.6](../architecture/refactor-audit.md#26-service-splits-inside-c4-that-should-be-one-service-each)
**Roadmap:** [00-roadmap.md](00-roadmap.md) → P9

---

## 1. Problem

Five small splits in the codebase that each represent the same anti-pattern: a concept was split across two files / classes / services when one would do, because adding a new file was easier than refactoring the existing one. Bundled into one PR because they share a single design principle (consolidate without losing behavior) and similar test surface.

### 1.1 `ConversationFork` + `SelfFork` — two services for fork variants

Per [C4](../architecture/05-runtime-services.puml), the `agent_runtime/api/` cluster has both:

- `ConversationFork` ([`agent_runtime/api/conversation_fork.py`](../../src/agent_runtime/api/conversation_fork.py)) — fork a conversation to share with another user.
- `SelfFork` ([`agent_runtime/api/self_fork.py`](../../src/agent_runtime/api/self_fork.py)) — fork a conversation for the same user.

The two services almost certainly share substantial logic (load conversation, walk message history, copy state, create new IDs, persist). The user-distinction is one boolean (target_user_id == self vs. other) plus probably a permission check.

### 1.2 `WorkspaceFeedService` + `WorkspaceDefaultsService` — two services for one workspace surface

- [`workspace_feed_service.py`](../../src/agent_runtime/api/workspace_feed_service.py) — workspace feed reads (drafts, subagent snapshots, source aggregates).
- [`workspace_defaults_service.py`](../../src/agent_runtime/api/workspace_defaults_service.py) — workspace default settings (model profiles, retention, etc.).

Both operate on workspace state. They probably share organization-resolution and permission-check code.

### 1.3 Four permission objects (3 specific + 1 generic)

Per [C6](../architecture/04-capabilities.puml):

- `ToolPermissionChecker` ([`agent_runtime/capabilities/tools/permissions.py`](../../src/agent_runtime/capabilities/tools/permissions.py)) — tool-card visibility and call-time authorization.
- `McpPermissionPolicy` ([`agent_runtime/capabilities/mcp/permissions.py`](../../src/agent_runtime/capabilities/mcp/permissions.py)) — MCP server visibility, tool-call authorization, auth-state-aware.
- `SkillPermissionPolicy` ([`agent_runtime/capabilities/skills/policy.py`](../../src/agent_runtime/capabilities/skills/policy.py)) — skill access policy.
- `CapabilityAuthGate` ([`agent_runtime/capabilities/auth_gate.py`](../../src/agent_runtime/capabilities/auth_gate.py)) — generic capability auth gate.

Either the gate replaces the three specifics or the three predate it. New developers don't know which to extend.

### 1.4 `ToolBudgetMiddleware` → `ToolBudgetGuard` two-step

- [`tool_budget_middleware.py`](../../src/agent_runtime/capabilities/tool_budget_middleware.py) — middleware that hooks per-tool-call execution.
- [`tool_budget_guard.py`](../../src/agent_runtime/capabilities/tool_budget_guard.py) — guard that decides allow/deny.

Two files for "is this tool call allowed under the per-task cap (default 5)." One file is enough unless the guard is reused outside the middleware (verify-first).

### 1.5 `UsageService.headroom_pct` is a utility, not a service

[`UsageService`](../../src/agent_runtime/api/usage_service.py) computes context-window usage / headroom percentage for in-chat `/context`. Per [f9](../architecture/f9-usage-metrics.puml), it is invoked through `ConversationContextBuilder` (also in the same file). That builder is the right home; the surrounding "service" wrapper is ceremony.

### What this is NOT

- Not a behavior change. Every fork variant, workspace pane query, permission decision, budget check, and `/context` response stays user-identical.
- Not the [coordinator split](00-roadmap.md#phase-6--coordinator-split-do-last) (P22). `RuntimeApiService` is untouched here.
- Not the [cluster boundary moves](07-cluster-boundary-moves.md) (P8). This PR consolidates within whichever directories these services live in _after_ P8 (or before, if landed first — the moves are independent).
- Not a change to the per-task tool-call cap value (default 5).

---

## 2. Goal and non-goals

### Goal

Five small consolidations in one PR, each preserving every public method's behavior. Reduce file count by 4–5 and reduce the cognitive surface of "which thing do I extend when X."

### Non-goals

- Reduce the budget feature surface. Budgets remain idempotent, CAS-based, etc.
- Change permission semantics. Visibility check at list time + call-time defense in depth (per [f8](../architecture/f8-mcp-auth.puml)) is non-negotiable.
- Touch the worker-facing budget charger (`BudgetCharger.charge_run` in `agent_runtime/budgets/charger.py`). Budget _enforcement_ (this PR) is separate from budget _charging_.
- Merge anything that needs verification first (see [§9 open questions](#9-open-questions)).

### Success criteria

- `ConversationFork` and `SelfFork` collapsed into a single `ForkService` with distinct entry points (`fork_for_self`, `fork_for_other_user`) sharing internal helpers.
- `WorkspaceFeedService` and `WorkspaceDefaultsService` collapsed into a single `WorkspaceService` with distinct read/write methods.
- One permission model, with the three specifics either retired in favor of the gate or the gate retired in favor of the specifics. (Decision in [§3.3](#33-permission-model-decision-merge-or-retire-the-gate).)
- `ToolBudgetGuard` merged into `ToolBudgetMiddleware`, _if_ the guard isn't reused elsewhere.
- `UsageService` collapsed into `ConversationContextBuilder` (or otherwise removed as a separate class), with the `/context` HTTP route calling the builder directly.
- All existing tests pass. New tests added for any merged behavior.
- Public API surface (HTTP routes, public methods called from the worker) unchanged.

---

## 3. Systems touched

### 3.1 Fork consolidation

**Files removed:**

| File                                                                                         | Replaced by                              |
| -------------------------------------------------------------------------------------------- | ---------------------------------------- |
| [`agent_runtime/api/conversation_fork.py`](../../src/agent_runtime/api/conversation_fork.py) | `services/fork_service.py` (or wherever) |
| [`agent_runtime/api/self_fork.py`](../../src/agent_runtime/api/self_fork.py)                 | same                                     |

**Files added:**

- `agent_runtime/services/fork_service.py` (or alongside other services per [P8](07-cluster-boundary-moves.md)) — single class with two entry points and a shared private `_perform_fork` helper.

**Sketch:**

```python
class ForkService:
    def __init__(self, persistence: PersistencePort, event_producer: RuntimeEventProducer, ...):
        ...

    async def fork_for_self(
        self,
        org_id: str,
        user_id: str,
        source_conversation_id: str,
        from_message_id: str | None = None,
    ) -> ForkResult:
        target_user_id = user_id  # same user, no permission elevation
        return await self._perform_fork(org_id, user_id, target_user_id, source_conversation_id, from_message_id)

    async def fork_for_other_user(
        self,
        org_id: str,
        user_id: str,
        target_user_id: str,
        source_conversation_id: str,
        from_message_id: str | None = None,
    ) -> ForkResult:
        # explicit permission check: user_id is allowed to share with target_user_id
        await self._authorize_share(org_id, user_id, target_user_id)
        return await self._perform_fork(org_id, user_id, target_user_id, source_conversation_id, from_message_id)

    async def _perform_fork(self, ...): ...
```

**HTTP route changes:**

- The HTTP routes for `/v1/agent/conversations/{id}/fork` (self) and `/v1/agent/conversations/{id}/fork-share` (other user) keep their paths. They now both call `ForkService` via the appropriate entry point.

### 3.2 Workspace consolidation

**Files removed:**

| File                                                                                                           | Replaced by                     |
| -------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| [`agent_runtime/api/workspace_feed_service.py`](../../src/agent_runtime/api/workspace_feed_service.py)         | `services/workspace_service.py` |
| [`agent_runtime/api/workspace_defaults_service.py`](../../src/agent_runtime/api/workspace_defaults_service.py) | same                            |

**Files added:**

- `agent_runtime/services/workspace_service.py` — single class with `feed_*` and `defaults_*` methods; shared org-resolution and permission helpers.

**Sketch:**

```python
class WorkspaceService:
    # Feed reads
    async def list_drafts(self, org_id: str, user_id: str, conversation_id: str) -> list[DraftRecord]: ...
    async def list_subagent_snapshots(self, org_id: str, conversation_id: str) -> list[SubagentSnapshot]: ...
    async def aggregate_sources(self, org_id: str, conversation_id: str) -> list[SourceAggregate]: ...

    # Defaults
    async def get_defaults(self, org_id: str) -> WorkspaceDefaults: ...
    async def update_defaults(self, org_id: str, user_id: str, patch: WorkspaceDefaultsPatch) -> WorkspaceDefaults: ...

    # Shared
    async def _authorize_workspace_member(self, org_id: str, user_id: str) -> None: ...
```

### 3.3 Permission model: decision (merge or retire the gate)

**Pre-flight requirement.** Before merging this PR, **read the four files** and answer:

- Is `CapabilityAuthGate` actually invoked from the per-subsystem checkers, or does it sit alongside them?
- Do the per-subsystem checkers carry domain logic that the gate cannot express (e.g. MCP `auth_state` checks, skill manifest validation, tool argument schema)?
- What does `core_builder` (in [`execution/factory.py`](../../src/agent_runtime/execution/factory.py)) currently use — the gate, the specifics, or both?

**Decision tree:**

| Finding                                                                                         | Action                                                                                                                                                                                                                  |
| ----------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Gate is a thin wrapper that delegates to the three specifics                                    | Keep the specifics as the canonical surface; delete the gate; update callers                                                                                                                                            |
| Gate carries shared cross-cutting logic (e.g. role/scope checks) the specifics don't            | Keep the gate as the entry point; convert the specifics into `Policy` strategies registered with the gate; delete `is_card_authorized` / `is_server_card_visible` / `is_skill_authorized` as separate top-level methods |
| Specifics and gate are independent (gate not called by specifics; specifics not called by gate) | The four-object setup is genuinely confused. Pick the _gate-as-entry-point_ model (above) and migrate                                                                                                                   |

**Output:** one permission entry point per capability subsystem. Either three (per-subsystem) or one (gate-with-strategies). Not four.

**Files modified:** the four permission files, plus every consumer in [`execution/factory.py`](../../src/agent_runtime/execution/factory.py), [`execution/deep_agent_builder.py`](../../src/agent_runtime/execution/deep_agent_builder.py), and the MCP middleware.

### 3.4 ToolBudget merge

**Pre-flight grep:**

```bash
grep -rn "ToolBudgetGuard\|tool_budget_guard" services/ai-backend/src services/ai-backend/tests
```

If `ToolBudgetGuard` is referenced only from `ToolBudgetMiddleware` and its tests:

- **Files removed:** [`agent_runtime/capabilities/tool_budget_guard.py`](../../src/agent_runtime/capabilities/tool_budget_guard.py).
- **Files modified:** [`agent_runtime/capabilities/tool_budget_middleware.py`](../../src/agent_runtime/capabilities/tool_budget_middleware.py) — inline the guard logic; rename the class to `ToolBudgetMiddleware` only (no separate `Guard` class).
- **Tests updated:** merge guard tests into middleware tests.

If `ToolBudgetGuard` is reused (e.g. from a slash command, a worker job, or a different middleware):

- Leave separate. Document the reuse site in a comment in both files.

### 3.5 UsageService → ConversationContextBuilder

Per [f9](../architecture/f9-usage-metrics.puml), the `/context` endpoint already runs through `ConversationContextBuilder`. The wrapper `UsageService` is mostly a routing wrapper.

**Files modified:**

- [`agent_runtime/api/usage_service.py`](../../src/agent_runtime/api/usage_service.py) — keep `ConversationContextBuilder` (or move to `services/`), remove `UsageService` wrapper. Consumers (the HTTP route, primarily) call the builder directly.
- HTTP route module: import `ConversationContextBuilder` instead of `UsageService`; method call shifts from `UsageService.get_conversation_context` to `ConversationContextBuilder.build`.
- The Usage page endpoints (`/v1/usage/me`, `/v1/usage/me/connectors`, `/v1/usage/org`) are served by `UsageQueryService` (different class — see [`usage_service.py:39`](../../src/agent_runtime/api/usage_service.py#L39) referenced in [f9](../architecture/f9-usage-metrics.puml)). `UsageQueryService` stays — it's a real query layer, not a wrapper.

**Net change:** one class deleted, one renamed/moved.

---

## 4. Behaviors to preserve

| Behavior                                                                                                                                                | How preserved                                                                                        |
| ------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Self-fork creates a new conversation owned by the same user                                                                                             | `ForkService.fork_for_self` preserves user ownership                                                 |
| Cross-user fork requires explicit share authorization                                                                                                   | `ForkService.fork_for_other_user` calls `_authorize_share` before forking                            |
| Workspace feed reads honor membership / scope                                                                                                           | `WorkspaceService._authorize_workspace_member` runs before every read                                |
| Workspace defaults updates require admin / write scope                                                                                                  | Same pattern with role check                                                                         |
| Tool / MCP / skill visibility check at list time                                                                                                        | Permission entry point is called from `core_factory` for `list_available_*`                          |
| Tool / MCP / skill authorization check at call time (defense in depth)                                                                                  | Per [f8](../architecture/f8-mcp-auth.puml), permission checks fire twice — preserved in both designs |
| MCP `auth_state {none, pending, valid, error}` is part of the visibility decision                                                                       | `McpPermissionPolicy` (or its strategy form under the gate) keeps this logic                         |
| Per-task tool-call cap (default 5) enforcement with `BUDGET_WARNING` event on overflow                                                                  | Merged middleware preserves the same check + event emission                                          |
| Idempotent `BudgetCharger.charge_run` post-completion                                                                                                   | Untouched — different file                                                                           |
| `/context` returns the same `ConversationContextResponse` shape (context_window, used_tokens, headroom_pct, compression_events, per_subagent_breakdown) | Builder unchanged; only the wrapper `UsageService` is removed                                        |
| `/v1/usage/*` endpoints (per-user / per-org / per-connector rollups)                                                                                    | `UsageQueryService` untouched                                                                        |
| Cost stamped at write time using active `ModelPricingRecord`                                                                                            | Untouched — write path is in the worker, not these services                                          |

---

## 5. Risks

| Risk                                                                                                                           | Likelihood | Mitigation                                                                                                                                                              |
| ------------------------------------------------------------------------------------------------------------------------------ | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Fork consolidation accidentally removes a permission check on cross-user fork                                                  | Medium     | Test must assert: a non-admin user attempting cross-user fork to another user without share auth gets 403                                                               |
| Workspace consolidation accidentally drops a per-method scope check                                                            | Medium     | Test matrix for every workspace method × {member, non-member, admin, guest}                                                                                             |
| Picking the wrong permission consolidation model and having to redo it                                                         | Medium     | Pre-flight read of all four files (see [§3.3](#33-permission-model-decision-merge-or-retire-the-gate)) before writing code; document the decision in the PR description |
| `ToolBudgetGuard` is reused in a subtle place (e.g. a CLI slash command implementation in the runtime) and the merge breaks it | Low        | Pre-flight grep is the gate; if any hit outside middleware, do not merge                                                                                                |
| `UsageService` removal breaks an internal dashboard or admin view that imports the wrapper                                     | Low        | Grep before removal; if hit, leave the wrapper as a thin re-export shim                                                                                                 |
| Tests rely on the exact class identity (`isinstance(svc, ConversationFork)`)                                                   | Low        | Update or delete those tests; they were testing the wrong thing                                                                                                         |
| Two PRs land back-to-back (this and P8 cluster moves) and create a merge conflict                                              | Medium     | Sequence them — land [P8](07-cluster-boundary-moves.md) first, this PR moves consolidated services to their final location                                              |

---

## 6. Unit testing requirements

### 6.1 ForkService

- `fork_for_self` creates a new conversation owned by the same user.
- `fork_for_self` does NOT call `_authorize_share`.
- `fork_for_other_user` calls `_authorize_share` before forking; on auth failure, raises a typed error and does NOT touch persistence.
- Both entry points produce identical message-history copies given identical inputs (parameterized test).
- `from_message_id` argument truncates history at the given message in both variants.

### 6.2 WorkspaceService

- Each method authorizes the caller before the underlying read/write.
- Non-member access raises a typed error.
- `update_defaults` requires the appropriate write scope (admin / owner — match current behavior).
- Read methods return empty results for empty workspaces (no errors).

### 6.3 Permission model (post-decision)

- Visibility check at list time is unchanged for tool / MCP / skill flows (test the same scenarios that exist today).
- Call-time check fires for every tool invocation (regression test for [f8](../architecture/f8-mcp-auth.puml) defense in depth).
- MCP `auth_state` cases:
  - `NONE` → server visible (with auth-required marker), tool calls denied.
  - `PENDING` → same.
  - `VALID` → server visible, tool calls allowed.
  - `ERROR` → server visible (with error marker), tool calls denied.
- Skill manifest validation (if previously in `SkillPermissionPolicy`) is preserved — invalid manifests rejected.

### 6.4 ToolBudgetMiddleware (merged)

- Per-task tool-call cap enforced (parameterize cap = 1, 2, 5).
- `BUDGET_WARNING` event emitted on overflow.
- `safe_message` returned to the model on overflow (no crash).
- Multiple tools within the same task share the same counter.
- Counter resets on a new task (different `task_id`).

### 6.5 ConversationContextBuilder (post-UsageService removal)

- Same `/context` golden response for a fixture conversation before and after the refactor.
- Subagent rollup behavior unchanged.

### 6.6 No-regression suite

- Full HTTP suite: every fork / workspace / usage endpoint returns the same status codes and response shapes for the same inputs as before.
- SSE: tool calls under cap stream normally; over cap emit `BUDGET_WARNING` and stop further tool calls — assert via fixture.

---

## 7. Rollback plan

| Sub-item                | Rollback                                                                                 |
| ----------------------- | ---------------------------------------------------------------------------------------- |
| Fork consolidation      | `git revert`. Both old service files return.                                             |
| Workspace consolidation | Same.                                                                                    |
| Permission model        | Revert is structural — depending on which direction was chosen, restore the deleted side |
| ToolBudget merge        | `git revert`. Two files return.                                                          |
| UsageService removal    | `git revert`. Wrapper class returns.                                                     |

Each sub-item is its own commit so a single sub-item can be reverted independently.

---

## 8. Implementation order within the PR

Land in this order so each consolidation independently passes CI before the next:

1. **UsageService → ConversationContextBuilder** (smallest; closest to a pure rename).
2. **ToolBudget merge** (only if pre-flight grep passes).
3. **Fork consolidation** (medium; new tests for the cross-user permission check).
4. **Workspace consolidation** (similar shape to fork).
5. **Permission model decision + collapse** (largest; touches the most consumers).

Each step is its own commit. Each commit is independently reviewable and revertable.

---

## 9. Open questions

- **Does the gate or the specifics own the "auth_state" decision** for MCP servers? Read [`mcp/permissions.py`](../../src/agent_runtime/capabilities/mcp/permissions.py) and [`auth_gate.py`](../../src/agent_runtime/capabilities/auth_gate.py) before [§3.3](#33-permission-model-decision-merge-or-retire-the-gate).
- **Is `ToolBudgetGuard` reused outside `ToolBudgetMiddleware`?** Pre-flight grep is the gate.
- **Does any consumer call `UsageService` directly** rather than via the HTTP route? Grep before removing the class.
- **Are `ConversationFork` and `SelfFork` truly fork variants of the same operation**, or do they have substantially different message-history semantics (e.g. self-fork branches at a point, cross-user fork copies whole)? Read both files; if the operations are structurally different, keep them separate and revisit.
- **Does any HTTP route bind to `WorkspaceFeedService` or `WorkspaceDefaultsService` by class type** (rather than by an interface)? FastAPI dependency injection patterns may matter.
- **If [P8](07-cluster-boundary-moves.md) lands first**, this PR's source paths shift from `agent_runtime/api/` to `agent_runtime/services/`. Update file paths in this PRD if so.

---

_Phase 2 PR. Ideally lands after [P8](07-cluster-boundary-moves.md) (so consolidated services are placed in `services/` not `api/`), but functionally independent. The five sub-items can be split into separate PRs if consolidation in one tranche is too much surface area._
