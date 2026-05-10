# Refactor PRD — Cluster Boundary Moves (Phase 2)

**Status:** Draft
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §2.5](../architecture/refactor-audit.md#25-draftbackend-in-capabilities), [§5.4](../architecture/refactor-audit.md#54-atlas_task_toolpy-in-execution), [§5.5](../architecture/refactor-audit.md#55-agent_runtimeapi-mixes-coordinator-with-domain-services)
**Roadmap:** [00-roadmap.md](00-roadmap.md) → P8

---

## 1. Problem

Three modules currently live in clusters where they don't fit. The mismatches make the cluster boundaries less informative ("what's in `capabilities/`?" no longer has a tight answer) and create import edges that cross conceptual boundaries.

### 1.1 `DraftBackend` in `capabilities/`

[`agent_runtime/capabilities/backends/draft_backend.py`](../../src/agent_runtime/capabilities/backends/draft_backend.py) sits in the capabilities cluster. The capabilities cluster ([C6](../architecture/04-capabilities.puml)) is for surfaces the **model** uses: tools, MCP servers, skills, subagents. Drafts are a **product** concept (the Workspace pane reads drafts; users edit drafts) — they're not a model capability. The model interacts with drafts only by writing to `/drafts/` virtual paths, which is a filesystem-routing concern, not a capability surface.

### 1.2 `atlas_task_tool.py` in `execution/`

[`agent_runtime/execution/atlas_task_tool.py`](../../src/agent_runtime/execution/atlas_task_tool.py) provides "supervisor task → subagent trace linking" per [C5](../architecture/08-execution-prompts.puml). This couples the execution cluster (graph + builder + providers + prompts) to:

- The delegation cluster (subagent task IDs, parent/child trace relationships).
- The observability cluster (trace propagation).

The trace-linking concern belongs to one of those two clusters, not to execution.

### 1.3 `agent_runtime/api/` mixes coordinator with domain services

[`agent_runtime/api/`](../../src/agent_runtime/api/) currently holds:

- `RuntimeApiService` (the 2.4k-LOC coordinator).
- `RuntimeEventProducer` + `PresentationGenerator` (event production layer).
- `DraftService`, `ShareService`, `ConversationFork`, `SelfFork`, `WorkspaceFeedService`, `WorkspaceDefaultsService`, `UsageService`, `McpDiscoveryService` (domain services).
- `MembershipResolver`, `UserPoliciesResolver`, `SuggestibleConnectorsResolver`, `NotificationDispatcher` (resolvers).
- Sync + async `ports.py`.

"API" should mean _presentation / coordination layer_. The domain services don't belong in `api/` — they're domain logic that happens to be called by API routes (and sometimes by the worker). The current layout muddies the boundary that would otherwise separate "what handles HTTP requests" from "what implements business logic."

### What this is NOT

- Not a behavior change. Every public method is preserved.
- Not the [`RuntimeApiService` split](00-roadmap.md#phase-6--coordinator-split-do-last) (P22). The coordinator stays where it is for now; only its peers move.
- Not the [service consolidation](08-service-consolidation.md) (P9). Fork / Workspace stay as-is at file count level — they just move directory.
- Not a change to `RuntimeEventProducer` or `PresentationGenerator`. They stay in `api/` because they're presentation-layer concerns.

---

## 2. Goal and non-goals

### Goal

Move three modules into clusters that match their actual responsibility. Maintain backwards compatibility for any external import via re-exports during a deprecation window.

### Non-goals

- Reduce the file count. (One file moves; nothing merges or splits.)
- Change behavior, public API surface, test coverage, or contracts.
- Split `RuntimeApiService` (P22).
- Consolidate Fork / Workspace services (P9).
- Touch `runtime_api/` (HTTP routes, schemas, SSE) — that's where the FastAPI surface lives and is correctly named.

### Success criteria

- `DraftBackend` lives in a domain location (see [§3.1](#31-draftbackend-relocation)) — _not_ in `agent_runtime/capabilities/backends/`.
- `atlas_task_tool.py` lives in either `agent_runtime/delegation/subagents/` or `agent_runtime/observability/` — _not_ in `agent_runtime/execution/`.
- Domain services from `agent_runtime/api/` (DraftService, ShareService, ConversationFork, SelfFork, WorkspaceFeedService, WorkspaceDefaultsService, UsageService, McpDiscoveryService) live in `agent_runtime/services/` (new package).
- All previous import paths resolve via re-exports for one release; deprecation warning logged on each.
- Full test suite green with no test changes (the moves are import-path only).
- New cluster diagrams (or notes appended to existing diagrams in `docs/architecture/`) reflect the moves.

---

## 3. Systems touched

### 3.1 `DraftBackend` relocation

**Move:**

| From                                                                                                                     | To                                                                                     |
| ------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| [`agent_runtime/capabilities/backends/draft_backend.py`](../../src/agent_runtime/capabilities/backends/draft_backend.py) | `agent_runtime/services/draft_backend.py` (preferred, alongside `DraftService`) — _or_ |
|                                                                                                                          | `agent_runtime/drafts/draft_backend.py` if a per-feature subpackage is preferred       |

**Choice criterion:** if `DraftService` (currently in `api/`) moves to `services/draft_service.py` as part of [§3.3](#33-domain-services-move-out-of-api), put `DraftBackend` in the same neighborhood. If they split into per-feature subpackages, both go into `agent_runtime/drafts/`. The PR chooses one; do not split them across packages.

**Re-export shim** (kept for one release):

```python
# agent_runtime/capabilities/backends/draft_backend.py (new contents)
import warnings
from agent_runtime.services.draft_backend import DraftBackend as _DraftBackend  # or wherever it landed

warnings.warn(
    "agent_runtime.capabilities.backends.draft_backend is deprecated; "
    "import from agent_runtime.services.draft_backend instead.",
    DeprecationWarning,
    stacklevel=2,
)

DraftBackend = _DraftBackend
__all__ = ["DraftBackend"]
```

After one release, delete the shim.

### 3.2 `atlas_task_tool.py` relocation

**Decision input needed before move:** read `atlas_task_tool.py` to determine its primary responsibility. Two plausible homes:

- **`agent_runtime/delegation/subagents/atlas_task_tool.py`** if the file is primarily about subagent task ID handling (parent_task_id ↔ subagent trace_id wiring, supervisor handoff conventions).
- **`agent_runtime/observability/atlas_task_tracing.py`** (rename) if the file is primarily about trace propagation (OTel span linking, trace context flowing across the supervisor/subagent boundary).

If the file does both, split it. (This is the only sub-item that may legitimately split a file. If splitting, document why in the PR description.)

**Re-export shim** at `agent_runtime/execution/atlas_task_tool.py` for one release, identical pattern to [§3.1](#31-draftbackend-relocation).

### 3.3 Domain services move out of `api/`

**New package:** `agent_runtime/services/` (with `__init__.py`).

**Moves:**

| From (path under `agent_runtime/api/`)      | To (path under `agent_runtime/services/`)                             | Notes                                                                        |
| ------------------------------------------- | --------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `draft_service.py`                          | `services/draft_service.py`                                           | Co-located with `DraftBackend` (per [§3.1](#31-draftbackend-relocation))     |
| `share_service.py`                          | `services/share_service.py`                                           |                                                                              |
| `share_token.py`                            | `services/share_token.py`                                             | Companion module to ShareService                                             |
| `conversation_fork.py`                      | `services/conversation_fork.py`                                       | (Will be merged with `self_fork.py` in P9)                                   |
| `self_fork.py`                              | `services/self_fork.py`                                               | (Will be merged with `conversation_fork.py` in P9)                           |
| `workspace_feed_service.py`                 | `services/workspace_feed_service.py`                                  | (Will be merged with `workspace_defaults_service.py` in P9)                  |
| `workspace_defaults_service.py`             | `services/workspace_defaults_service.py`                              | (Will be merged with `workspace_feed_service.py` in P9)                      |
| `usage_service.py`                          | `services/usage_service.py`                                           | (`headroom_pct` may collapse into ConversationContextBuilder in P9)          |
| `mcp_discovery_service.py`                  | `services/mcp_discovery_service.py`                                   |                                                                              |
| `membership.py` (MembershipResolver)        | `services/membership.py` _or_ `agent_runtime/resolvers/membership.py` | Resolvers may go in `services/` or a new `resolvers/` subpackage; choose one |
| `user_policies_resolver.py`                 | same call as above                                                    |                                                                              |
| `suggestible_connectors_resolver.py`        | same call as above                                                    |                                                                              |
| `notifications.py` (NotificationDispatcher) | `services/notifications.py`                                           | Dispatcher belongs with services                                             |

**Stays in `api/`:**

| File                        | Why it stays                                                              |
| --------------------------- | ------------------------------------------------------------------------- |
| `service.py`                | `RuntimeApiService` — the coordinator. Splits in P22, not here.           |
| `events.py`                 | `RuntimeEventProducer` — presentation/event-emission layer. Stays.        |
| `presentation.py`           | `PresentationGenerator` — same.                                           |
| `presentation_templates.py` | Same.                                                                     |
| `ports.py`                  | (Removed by P5 async-only ports.) If P5 hasn't shipped yet, leave for P5. |
| `async_ports.py`            | Becomes the only `ports.py` after P5.                                     |
| `constants.py`              | API-layer constants. Stays.                                               |

**Re-export shims** for every moved file. Each old `agent_runtime/api/<file>.py` becomes:

```python
import warnings
from agent_runtime.services.<file> import *  # noqa
warnings.warn(
    "agent_runtime.api.<file> is deprecated; import from agent_runtime.services.<file> instead.",
    DeprecationWarning, stacklevel=2,
)
```

After one release, delete shims.

### 3.4 Imports update

In the same PR, update first-party imports inside `services/ai-backend/src/` to use the new paths. External callers (tests, other packages) continue to use the old paths via shims until the next PR.

```bash
# Find every consumer of the old paths
grep -rn "from agent_runtime\.api\.\(draft_service\|share_service\|conversation_fork\|self_fork\|workspace_feed_service\|workspace_defaults_service\|usage_service\|mcp_discovery_service\|membership\|user_policies_resolver\|suggestible_connectors_resolver\|notifications\)" \
    services/ai-backend/src services/ai-backend/tests
```

Update src callers; leave test callers (they exercise the shim, which is desirable during the deprecation window).

---

## 4. Behaviors to preserve

| Behavior                                                                                                             | How preserved                                             |
| -------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| All public method signatures on every moved service                                                                  | Files moved verbatim; only path changes                   |
| Pydantic contracts on inputs/outputs                                                                                 | Untouched                                                 |
| Filesystem permission rules for `/drafts/` writes routed through `DraftStorePort`                                    | `DraftBackend` keeps its current relationship to the port |
| Trace linking semantics for atlas_task_tool (parent_task_id ↔ subagent trace_id)                                     | File moves; behavior identical                            |
| External import paths resolvable for one release                                                                     | Re-export shims with deprecation warnings                 |
| Service-boundary rule "no deployable component imports another's `src/`" (root [`CLAUDE.md`](../../../../CLAUDE.md)) | Moves are intra-package; no new cross-component imports   |

---

## 5. Risks

| Risk                                                                                | Likelihood | Mitigation                                                                                                                                                                                                                         |
| ----------------------------------------------------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A re-export shim emits deprecation warnings during normal operation, polluting logs | Medium     | Shim warnings use `DeprecationWarning` (silent by default in production); tests can opt-in via `-W error::DeprecationWarning` to flush them out                                                                                    |
| A consumer outside `services/ai-backend/` imports one of the moved services         | Low        | Service-boundary rule forbids this; grep across `apps/`, `packages/`, `services/backend*/` to confirm before merge                                                                                                                 |
| Shim left in place forever; technical debt persists                                 | Medium     | PR description includes a follow-up issue for shim removal; assign to next sprint                                                                                                                                                  |
| `atlas_task_tool.py` doesn't fit cleanly in either home, gets split badly           | Low        | Read the file before deciding; if split, both halves get docstrings explaining the split                                                                                                                                           |
| Test discovery breaks because the move changes pytest collection paths              | Low        | Run full suite locally before push; conftest-based fixtures should not depend on file paths                                                                                                                                        |
| Circular imports introduced by the reorganization                                   | Medium     | If `services/` imports `api/` (e.g. for `RuntimeEventProducer`) and `api/` imports `services/`, restructure: services should not import from `api/`. If they need to emit events, take an `EventEmitter` Protocol as a dependency. |

---

## 6. Unit testing requirements

This refactor is import-path only. Tests should not change in behavior or assertions.

### 6.1 No-regression assertion

- `make test` and per-service `pytest` pass with zero test modifications.

### 6.2 Shim coverage

- New tests under `tests/unit/refactor/test_deprecation_shims.py` (or similar):
  - Importing each moved name from the old path triggers a `DeprecationWarning`.
  - The imported object is identical to the one from the new path (`is`-equality after `from <new> import X`).
  - All names previously importable from each old path are still importable.

### 6.3 Static checks

- Add a CI step: `python -W error::DeprecationWarning -m pytest tests/unit/` on a small canary subset to ensure first-party src/ imports use the new paths (any deprecation warning from src/ becomes a test failure).

### 6.4 Cluster-boundary lint

- New script `scripts/check_cluster_boundaries.py` (or similar): asserts no file in `agent_runtime/capabilities/` imports anything from `agent_runtime/services/` (capabilities should not depend on services); asserts no file in `agent_runtime/execution/` imports anything from `agent_runtime/observability/` _for trace-linking purposes_ (post-atlas-move). This is a soft lint to catch drift.

---

## 7. Rollback plan

- Pure file moves with shims: `git revert` restores both the old paths and removes the new ones cleanly.
- If a consumer is found mid-deployment that the shims didn't cover, add a one-line shim in a hotfix.

---

## 8. Implementation order within the PR

1. Create new package directories: `agent_runtime/services/`, plus subdirectory choices for `DraftBackend` and `atlas_task_tool.py`.
2. Move files (`git mv`). Run tests — they should still pass because the old path imports still resolve as long as the file is found at _some_ place in PYTHONPATH, but they won't, because the file is no longer at the old path. So:
3. Add re-export shims at the old paths immediately after the move. Re-run tests — should pass.
4. Update first-party `src/` imports to the new paths.
5. Add deprecation-shim tests.
6. Update the architecture cluster diagrams ([04-capabilities.puml](../architecture/04-capabilities.puml), [05-runtime-services.puml](../architecture/05-runtime-services.puml), [08-execution-prompts.puml](../architecture/08-execution-prompts.puml), [09-delegation.puml](../architecture/09-delegation.puml), [11-cross-cutting.puml](../architecture/11-cross-cutting.puml)) to reflect the moves.
7. Update [`docs/architecture/index.md`](../architecture/index.md) cluster table if needed.

---

## 9. Open questions

- Where exactly does `DraftBackend` belong: alongside `DraftService` in `services/`, or in its own `agent_runtime/drafts/` subpackage? Decide based on whether other draft-related modules will follow.
- Is `atlas_task_tool.py` a delegation concern, an observability concern, or both? Read the file.
- Should resolvers (`MembershipResolver`, `UserPoliciesResolver`, `SuggestibleConnectorsResolver`) live in `services/` or in a new `agent_runtime/resolvers/` subpackage? They have a distinct shape (pluggable HTTP-or-InMem) that may justify their own home.
- Does any consumer outside `services/ai-backend/` (tests in other services, packages, or apps) import any of these names? Should not — the service-boundary rule forbids it — but verify with grep before assuming.
- Does the PRD format require a corresponding spec under `docs/specs/`? Per [`docs/CLAUDE.md`](../CLAUDE.md), specs are for behavior; this PR has no behavior change. Document the move in [`docs/architecture/`](../architecture/) instead of adding a spec.

---

_Phase 2 PR. Independent of [P5](01-async-only-ports.md), [P6](05-cleanup-wave.md), [P7](06-citation-batching.md), and [P9](08-service-consolidation.md). Land in any order within Phase 2._
