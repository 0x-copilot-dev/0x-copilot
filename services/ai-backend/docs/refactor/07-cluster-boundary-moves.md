# Refactor PRD — Cluster Boundary Moves (P8) — **PARTIAL DISPOSITION**

**Status:** Shipped (1 of 3 moves); the other two **retracted** after code review.
**Author:** architecture audit, May 2026 — re-evaluated 2026-05-11 after reading the code.
**Tracks:** [refactor-audit §2.5](../architecture/refactor-audit.md#25-draftbackend-in-capabilities), [§5.4](../architecture/refactor-audit.md#54-atlas_task_toolpy-in-execution), [§5.5](../architecture/refactor-audit.md#55-agent_runtimeapi-mixes-coordinator-with-domain-services)
**Roadmap:** [00-roadmap.md](00-roadmap.md) → P8

---

## Disposition summary

| Sub-item                                                                                                | Audit recommended | After code review | Action                                                                       |
| ------------------------------------------------------------------------------------------------------- | ----------------- | ----------------- | ---------------------------------------------------------------------------- |
| `DraftBackend` → out of `capabilities/backends/`                                                        | Move              | **Stay**          | Retract                                                                      |
| `atlas_task_tool.py` → out of `execution/`                                                              | Move              | **Move**          | **Shipped** — now at `agent_runtime/delegation/subagents/atlas_task_tool.py` |
| Domain services (Draft / Share / Fork / Workspace / Usage / McpDiscovery) → out of `agent_runtime/api/` | Move              | **Stay**          | Retract                                                                      |

---

## 1. What shipped

### `atlas_task_tool.py` relocation

**Move executed.**

| From                                                                                                                   | To                                                                                                                         |
| ---------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/execution/atlas_task_tool.py`](../../src/agent_runtime/delegation/subagents/atlas_task_tool.py) (gone) | [`agent_runtime/delegation/subagents/atlas_task_tool.py`](../../src/agent_runtime/delegation/subagents/atlas_task_tool.py) |

**Reason it holds up.** The file builds a custom `task` tool that wraps `deepagents.middleware.subagents._build_task_tool`, injecting `supervisor_task_call_id` into subagent `RunnableConfig` metadata. The deterministic-linkage logic is _about subagent dispatch semantics_, not about the execution graph. It belongs next to the other subagent files (`contracts.py`, `definitions.py`, `handoff.py`, `runner.py`).

**Scope of change.**

- One import in [`agent_runtime/execution/factory.py:56`](../../src/agent_runtime/execution/factory.py#L56) rewritten:
  `from agent_runtime.execution.atlas_task_tool import install_atlas_task_tool`
  → `from agent_runtime.delegation.subagents.atlas_task_tool import install_atlas_task_tool`
- File moved via `git mv` so blame/history is preserved.
- No tests imported the file directly (only docstring references); they continue to pass without modification.
- 1533 unit tests pass after the move.

No re-export shim needed: production-grep confirmed exactly one import site.

---

## 2. What was retracted, and why

### 2.1 `DraftBackend` stays in `capabilities/backends/`

**The audit's argument.** "Capabilities are for surfaces the _model_ uses (tools, MCP servers, skills, subagents). Drafts are a _product_ concept (the Workspace pane reads drafts; users edit drafts) — they're not a model capability."

**Why it doesn't hold up after reading the code.** [`agent_runtime/capabilities/backends/draft_backend.py`](../../src/agent_runtime/capabilities/backends/draft_backend.py) implements `deepagents.backends.protocol.BackendProtocol`. The model exercises it directly through deepagents' built-in `write_file` / `edit_file` tools, with the `/drafts/` path prefix routed by `CompositeBackend` into `DraftBackend.awrite` / `aedit`. The translation `write_file("/drafts/<id>.md") → DraftStorePort.insert_version` is the entire reason the file exists.

This makes `DraftBackend` exactly the kind of thing `capabilities/backends/` is for: a backend the model accesses through a capability surface. The audit conflated the _draft artifact_ (product concept) with the _backend that creates drafts_ (capability primitive). Keep it.

**Forward-looking note.** If the team adds more deepagents backends (e.g. a publications backend, a memos backend), they'd all naturally live in `capabilities/backends/`. The current location is the cohesive home for that growing pattern.

### 2.2 Domain services stay in `agent_runtime/api/`

**The audit's argument.** "'API' should mean _presentation / coordination layer_. The domain services don't belong in `api/` — they're domain logic that happens to be called by API routes (and sometimes by the worker)."

**Why it doesn't hold up after reading the code.** [`services/ai-backend/CLAUDE.md`](../../CLAUDE.md) — the project's own canonical doc — defines `agent_runtime/api/` as:

> "`api/` (presentation/service layer for the runtime API)."

The package name `api` IS the service layer in this codebase's vocabulary, not the HTTP edge. (The HTTP edge lives in `runtime_api/`, a different top-level package.) Moving domain services to a new `agent_runtime/services/` package would:

- Contradict the project's own conventions (CLAUDE.md would also need a rewrite).
- Not improve any boundary, because the worker would still import from `agent_runtime/services/` _outside_ its own cluster — same cross-cluster import shape, different name.
- Introduce import-path churn and a re-export shim for every domain service file (8+ shims), in exchange for a renaming that's stylistic, not architectural.

The audit's mental model was that "api == HTTP edge." That's not how this project uses the name. The current layout is consistent with itself.

**If the team wants this anyway**, the change needs to start by updating CLAUDE.md to redefine the package boundary, then plan the moves + shims as a separate PR. It is not a free rename.

---

## 3. Behaviors preserved

- All public methods on `install_atlas_task_tool` and `build_atlas_task_tool` unchanged.
- The monkey-patch idempotency marker (`_ds._atlas_task_tool_installed`) is unchanged.
- `supervisor_task_call_id` injection into subagent `RunnableConfig.metadata` is unchanged.
- The `StreamPartParser.supervisor_task_call_id_for(part)` consumer (per [`01b-usage-attribution-context.md`](01b-usage-attribution-context.md)) continues to read the same metadata key.
- Worker stream handlers that read `supervisor_task_call_id` from chunk metadata are unaffected.

---

## 4. Tests

No new tests required (this is a file move with no behavior change). The two existing test references in [`tests/unit/runtime_worker/test_stream_events.py`](../../tests/unit/runtime_worker/test_stream_events.py) are docstring-only and continue to refer correctly to "our `atlas_task_tool`" without an import path.

Full unit suite: **1533 passed, 0 failed, 28 skipped** after the move.

---

## 5. Out of scope (not affected by this PR)

- [`RuntimeApiService` split](00-roadmap.md) (P22). Coordinator stays where it is.
- [Service consolidation](00-roadmap.md) (P9) — retracted in full; see roadmap.
- `runtime_api/` (HTTP routes, schemas, SSE) — already correctly named; not touched.

---

## 6. Documentation follow-ups

- [`docs/architecture/refactor-audit.md` §5.4](../architecture/refactor-audit.md#54-atlas_task_toolpy-in-execution) — marked resolved.
- [`docs/architecture/refactor-audit.md` §2.5](../architecture/refactor-audit.md#25-draftbackend-in-capabilities) — marked retracted with reasoning.
- [`docs/architecture/refactor-audit.md` §5.5](../architecture/refactor-audit.md#55-agent_runtimeapi-mixes-coordinator-with-domain-services) — marked retracted with reasoning.
- [`docs/architecture/08-execution-prompts.puml`](../architecture/08-execution-prompts.puml) — `atlas_task_tool` box reassigned from C5 (Agent Runtime Core) to C7 (Delegation) in the next diagram refresh.
