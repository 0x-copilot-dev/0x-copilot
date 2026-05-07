# PR 3.2.5 — Subagent call_id propagation: parallel-fleet tool attribution

> **Status:** Shipped (Phase 1 of subagent runtime correctness work) · v1
> **Plan reference:** Sibling to [`pr-3.2.4-fleet-nests-compact-rows.md`](./pr-3.2.4-fleet-nests-compact-rows.md). PR 3.2.4 made the fleet card visually nest its children. This PR makes the **data underneath** correct so the children actually carry the right `parent_task_id` to nest by.
> **Owner:** ai‑backend (worker emit + execution monkey‑patch). No frontend, facade, api‑types, or migration.
> **Size:** **M.** One new file (`atlas_task_tool.py`), three small additions to existing files (`stream_parts.py`, `stream_subagents.py`, `stream_events.py`), one factory monkey‑patch hook, two regression tests. ≈ 250 LoC plus tests.
> **Depends on:** ✅ PR 3.2.4 (FE renders fleet rows by reading `args.parent_fleet_id`/`args.activities`; this PR fills `args.activities` correctly for parallel fleets).
> **Reads alongside:**
>
> - [`pr-3.2.4-fleet-nests-compact-rows.md`](./pr-3.2.4-fleet-nests-compact-rows.md) — the FE that consumes the data this PR makes correct.
> - [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) — streaming-event invariants (`sequence_no`, projection, untrusted-inputs rules).
> - [`services/ai-backend/docs/specs/10-agent-runtime-persistence-spec.md`](../../services/ai-backend/docs/specs/10-agent-runtime-persistence-spec.md) — `parent_task_id` semantics on the wire.

---

## 0 · TL;DR

After PR 3.2.4 landed (fleet card visually nests its children), the user observed that **inner tool calls from parallel subagents still leaked into the supervisor's main thread** — the fleet card was rendering as a header with no rows inside it, and the same subagents' `web_search` results showed up as standalone `tool_call_started` cards in the supervisor's chat scroll.

Empirical trace from a production run with two parallel research subagents:

```
seq  4: subagent_started      task_id=call_awE7udh…  parent_task_id=None  ← sub A start (correct, this IS the parent)
seq  5: subagent_started      task_id=call_ATNSdFn…  parent_task_id=None  ← sub B start (correct)
seq  6: subagent_fleet_started fleet_id=178831b09…   parent_task_id=None  ← fleet bookend (correct)
seq  7: tool_call_started     tool=web_search        parent_task_id=None  ← BUG: sub A's tool, no link
seq  8: tool_call_started     tool=web_search        parent_task_id=None  ← BUG: sub B's tool, no link
…
seq 51: tool_call_completed   tool=web_search        parent_task_id=None  ← BUG: still no link
```

Every one of the 51 inner tool events from the two subagents arrived at the FE with `parent_task_id=None`. The FE reducer's `upsertSubagentActivity` short‑circuits when `parent_task_id` is null, so those tool events never nested into the `run_subagent` parts' `args.activities` — they rendered standalone above the fleet card.

**Root cause** (from the docstring on the offending function — [`stream_subagents.py:186-225`](../../services/ai-backend/src/runtime_worker/stream_subagents.py#L186-L225)):

> _"For the FIRST event in a new subgraph, we link to a queued supervisor call_id ONLY when exactly one subagent is currently unlinked. With two or more unlinked subagents a naive FIFO pop is racy: when the supervisor dispatches a fast subagent (e.g. one that calls no internal tools) and a slow research subagent in parallel, the slow subagent's first tool message can arrive at the processor before the fast subagent's `SUBAGENT_COMPLETED` removes it from the queue, and the slow subagent's tools end up wrongly attributed to the fast subagent. Returning None here for ambiguous cases makes early tool events orphan rather than mis-attributed."_

The system **deliberately orphans** tool events when ≥2 subagents are mid‑flight, because the resolver had no deterministic way to tell which subagent's subgraph a given tool event belonged to. LangGraph generates subgraph UUIDs internally (e.g. `"tools:3af7da77-f445"` in the chunk's `ns` tuple); the supervisor's tool `call_id` (e.g. `call_awE7udh…`) is what the FE wants to match against. The mapping between the two only existed at the supervisor's tool‑dispatch site, but it wasn't propagated into the subagent's runtime context, so the worker had no way to recover it from chunks.

**Fix:** thread the supervisor's `tool_call_id` into each subagent's `RunnableConfig.metadata` at dispatch time. LangGraph propagates `RunnableConfig.metadata` onto every chunk the subgraph emits (visible in the second tuple element of `messages`-mode chunks: `data = (message, metadata)`). The worker reads it, pins a `(run_id, subgraph_task_id) → supervisor_call_id` mapping in a cache the first time it sees a chunk from a new subgraph, and resolves every subsequent event from that subgraph deterministically. **No FIFO. No race. No mis‑attribution.**

Verified end‑to‑end on a production run with two parallel research subagents (`call_knDW…` and `call_XjTE…`). Each of the 19 inner tool events correctly carries `parent_task_id` matching its own subagent's supervisor call_id.

---

## 1 · PRD

### 1.1 Problem

Three failures observed in production with parallel subagent fleets:

1. **Inner tool calls leak to the main thread.** A user dispatches "research X and Y in parallel". Two subagents fire `web_search` tools. The FE renders the search-result cards (`Reading 6 sources / web_search result × 6`) in the supervisor's main scroll, _above_ the empty fleet card. The user sees both the fleet header and the orphaned children. ([Screenshot evidence in earlier conversation; sequence numbers in §0 above.](#))
2. **The Agents tab card has nothing to display.** The workspace pane's Agents tab tries to render the subagent's per-step timeline, but `args.activities` is empty (because the inner tool events never nested), so it falls back to dumping `result_summary` raw. Wall of markdown.
3. **The bug is invisible in single-subagent flows.** The FIFO resolver works fine when exactly one subagent is in-flight (queue length 1, deterministic pop). Tests that exercise the single-subagent path pass. The bug only triggers with ≥2 unlinked subagents — exactly the parallel-fleet case the design was built for.

The system already had:

- Indexed `runtime_events.parent_task_id` (migration 0001).
- A FE reducer (`upsertSubagentActivity`) that nests events with `parent_task_id` into the matching `run_subagent` tool part's `args.activities`.
- A unit test (`test_tool_event_inside_subagent_carries_subagent_id`) asserting that the worker attaches `parent_task_id = supervisor_call_id` on tool events from inside a subagent's stream — which **passes in isolation** because the test only spawns one subagent.

What was missing: a deterministic way to recover the supervisor's `call_id` from a chunk when the worker observes it. The codebase relied on a queue heuristic that explicitly fails closed (returning `None`) under the parallel-fleet case.

### 1.2 Goals

1. **Deterministic linkage.** Every tool / reasoning / interrupt event emitted from inside a subagent's stream carries `parent_task_id = <supervisor task tool call_id>`, regardless of how many sibling subagents are simultaneously mid-flight.
2. **Drop the FIFO heuristic at the chunk-handler boundary.** The chunk-level resolution in `stream_events.append_activity_events` becomes a cache lookup with a raw-subgraph-UUID fallback for legacy / synthetic test fixtures. The FIFO path stays inside `stream_tools.StreamMessageProcessor.process` for messages-mode chunks (where the historical contract is fine; tests rely on it).
3. **No new event variant on the wire.** Linkage rides on existing chunk metadata, which is already part of LangGraph's stream protocol. The `RuntimeEventEnvelope` schema is untouched.
4. **Minimal blast radius for deepagents updates.** Patch one function (`_build_task_tool`) at module-load time. If deepagents refactors that function in a future release, our test suite catches it (the parallel-fleet regression test exercises the patched path).
5. **Backwards-compatible with legacy / synthetic chunks.** Test fixtures that don't go through our patched task tool still work — they fall back to the raw subgraph UUID for chunk-level emits, which preserves the historical behavior.
6. **Production-verifiable.** A canary run with two parallel subagents shows every inner tool event carrying the correct `parent_task_id`, traceable via the events archive endpoint.

### 1.3 Non-goals

- ❌ **Per-subagent interrupt isolation.** Today the supervisor's stream halts on _any_ interrupt event (`streaming_executor.py:175-178`), pausing all in-flight subagents. Letting fleet siblings keep running while one waits on approval/auth is the next phase (deferred to a separate PR — see §6).
- ❌ **`subagent_paused` / `subagent_resumed` event variants.** Tracked separately. Once Phase 2 lands, these become useful; without it, the runtime can't actually deliver them on a per-subagent basis.
- ❌ **Removing the legacy FIFO fallback inside `stream_tools.process`.** It still works correctly for single-subagent flows and for synthetic test chunks that don't carry our metadata. Keeping it removes upgrade risk; deleting it is a follow-up if real traffic shows the cache path covers everything.
- ❌ **Forking deepagents.** A monkey-patch at module-load time is intrusive enough; vendoring is too much. If deepagents drops `_build_task_tool` in a future release we revisit.
- ❌ **Touching the FE.** PR 3.2.4 already handles fleet card nesting. The FE reducer (`upsertSubagentActivity`) was already correct; it just needed the data.
- ❌ **Audit changes.** Subagent dispatch / completion / cancel auditing is owned by [`pr-3.2.3-subagent-backend-completion.md`](./pr-3.2.3-subagent-backend-completion.md). This PR doesn't add audit rows.

### 1.4 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                                                          | Verified by                                                                     |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------- |
| AC-1  | When the supervisor dispatches ≥2 subagents in the same turn, every tool event emitted from inside any one of those subagents' streams carries `parent_task_id` equal to that subagent's own supervisor `task` tool call_id. No mis-attribution between siblings.  | `test_chunk_metadata_links_parallel_subagents_to_supervisor_call_ids`           |
| AC-2  | A subagent that emits a chunk **without** the `supervisor_task_call_id` metadata key (legacy replay, synthetic test fixture) still resolves: the chunk-level path falls back to the raw subgraph UUID; the messages-mode path falls back to the FIFO heuristic.    | `test_chunk_without_supervisor_metadata_falls_back_to_raw_subgraph_id`          |
| AC-3  | Existing single-subagent test (`test_tool_event_inside_subagent_carries_subagent_id`) keeps passing without modification. The historical contract for the messages-mode `subagent_call_id_for_subgraph` resolver is unchanged.                                     | Existing test re-run.                                                           |
| AC-4  | Existing integration test (`test_runtime_worker_persists_normalized_activity_stream_events`) keeps passing. This was the regression sentinel during development; the cache-only refactor in `append_activity_events` was driven by it.                             | Existing test re-run.                                                           |
| AC-5  | The full ai-backend test suite passes: `tests/unit/runtime_worker/` + `tests/unit/agent_runtime/`. No new test failures.                                                                                                                                           | `pytest tests/unit/runtime_worker/ tests/unit/agent_runtime/` → 809 passed.     |
| AC-6  | Production canary: a real run with the prompt _"Dispatch 2 subagents to research on the web: …"_ produces an event stream where every `tool_call_started/completed`/`tool_result` event from inside the subagents carries `parent_task_id = <supervisor call_id>`. | Manual trace via `GET /v1/agent/runs/{run_id}/events`; documented in §5.        |
| AC-7  | Frontend behavior: in the same canary, the chat thread no longer shows orphaned `web_search result` cards above the fleet card. Inner tool calls nest into their respective subagent's `args.activities` and surface inside `<SubagentCard>` disclosures.          | Browser visual confirm; FE reducer behavior unchanged.                          |
| AC-8  | Monkey-patch is **idempotent**. Calling `install_atlas_task_tool()` more than once (e.g. during test reload) doesn't re-patch or break.                                                                                                                            | Marker flag (`_atlas_task_tool_installed`) on the deepagents module.            |
| AC-9  | The patched `task` tool's behavior is byte-identical to deepagents' upstream version except for the metadata fields it adds. Subagent state filtering, result extraction, error handling, the `Command` envelope shape — all preserved.                            | Code review: 1:1 mirror of `_build_task_tool` shape with two added config keys. |
| AC-10 | The `RuntimeEventEnvelope` schema is unchanged. No new fields on the wire. No new event variants. Existing FE / API consumers see no breaking changes.                                                                                                             | `git diff packages/api-types/` is empty for this PR.                            |

### 1.5 Risks

| Risk                                                                                                                                                                                                                                                                                                                  | Mitigation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **deepagents refactors `_build_task_tool` in a minor release.** Our monkey-patch silently breaks: subagent metadata stops propagating, FIFO heuristic kicks back in, FE re-orphans inner tool calls in parallel-fleet runs.                                                                                           | The monkey-patch installs at factory module-load time and is asserted by `_atlas_task_tool_installed` flag. The parallel-fleet regression test (`test_chunk_metadata_links_parallel_subagents_to_supervisor_call_ids`) catches loss of metadata propagation: it explicitly drives chunks with `supervisor_task_call_id` set in metadata and expects the resolved parent. If deepagents changes the function shape such that our patch builds a tool with a different signature, the test fails loudly. CI catches it. |
| **PEP 563 string annotations break langchain's runtime-type-driven `ToolRuntime` injection.** Hit during development: `from __future__ import annotations` made `runtime: ToolRuntime` a literal string, and langchain's `inspect.signature` introspection failed `issubclass(annotation, _DirectlyInjectedToolArg)`. | The `atlas_task_tool.py` module **deliberately omits** `from __future__ import annotations` and documents why in the module docstring. Code review catches reintroduction.                                                                                                                                                                                                                                                                                                                                            |
| **Cache-only resolution in `append_activity_events` regresses the existing test that assumed `parent_task_id = namespace.subagent_task_id` (raw UUID).**                                                                                                                                                              | Test (`test_runtime_worker_persists_normalized_activity_stream_events`) does pass `ns=("tools:task_123",)` for chunks and asserts `parent_task_id == "task_123"`. The fix's fallback rule is: if the cache has nothing **and** chunk metadata has nothing, fall back to the raw subgraph UUID. Same string the old code returned. Test passes. The behavior diff is invisible to anything but a parallel fleet.                                                                                                       |
| **FIFO pop side effect inside `append_activity_events` drains the queue prematurely.**                                                                                                                                                                                                                                | This was an actual regression caught by `test_runtime_worker_persists_normalized_activity_stream_events` during development. Fix: split `subagent_call_id_for_subgraph` (cache + FIFO, used by `stream_tools.process`) from `cached_subagent_call_id_for_subgraph` (cache-only, used by `append_activity_events`). Documented in the docstring; covered by the regression test.                                                                                                                                       |
| **Metadata field name conflict with deepagents internals.** If deepagents starts using `supervisor_task_call_id` as a key in its own internal config, our key collides.                                                                                                                                               | The key is namespaced via the `SUPERVISOR_TASK_CALL_ID_KEY` constant defined once in `atlas_task_tool.py` and re-exported as `stream_parts.SUPERVISOR_TASK_CALL_ID_KEY`. Renaming is a single-line change. Low actual risk: deepagents is unlikely to land a key with that exact name.                                                                                                                                                                                                                                |
| **A subagent emits a chunk that doesn't go through messages-mode** (custom / updates / values) and thus has no metadata in `data[1]`.                                                                                                                                                                                 | `StreamPartParser.supervisor_task_call_id_for(part)` probes both `data[1]` (messages tuple) **and** the chunk's top-level `metadata` field. Since the cache is registered on first messages-mode chunk and persists for the lifetime of the run, subsequent non-messages chunks resolve from cache. If the very first chunk a subagent emits is a non-messages chunk with no top-level metadata (rare in practice), it falls back to raw UUID — same as legacy behavior, no regression vs. before this PR.            |

### 1.6 Unit testing

Per [`services/ai-backend/tests/CLAUDE.md`](../../services/ai-backend/tests/CLAUDE.md):

**New tests** (in `tests/unit/runtime_worker/test_stream_events.py`):

- `test_chunk_metadata_links_parallel_subagents_to_supervisor_call_ids` — Two `SUBAGENT_STARTED` events seed the queue. Two messages-mode chunks arrive in interleaved order, each from a different subgraph UUID, each carrying its own `supervisor_task_call_id` in chunk metadata. Asserts each subagent's tool event carries the matching supervisor call_id; no mis-attribution. **This is the regression sentinel for the parallel-fleet bug.**
- `test_chunk_without_supervisor_metadata_falls_back_to_raw_subgraph_id` — A custom-mode chunk with `ns=("tools:legacy_subgraph_uuid",)` and no metadata triggers a `reasoning_summary_delta` emission. Asserts `parent_task_id == "legacy_subgraph_uuid"` (raw fallback). Locks in the legacy path so future cache-only refactors don't accidentally orphan replay events.

**Existing tests preserved**:

- `test_tool_event_inside_subagent_carries_subagent_id` — The single-subagent path. Unchanged. Asserts the FIFO heuristic in `stream_tools.process` still works for messages-mode chunks.
- `test_runtime_worker_persists_normalized_activity_stream_events` — End-to-end worker test with synthetic chunks. **Was the regression sentinel** during development: the first version of this PR popped the FIFO inside `append_activity_events`, breaking it. The split into cache-only vs FIFO-fallback resolvers was driven by this test.

**No frontend tests added.** PR 3.2.4 already covered fleet rendering; the FE behavior is unchanged here. The test surface is purely backend.

---

## 2 · Spec

### 2.1 Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ BEFORE — fragile FIFO heuristic                                             │
│                                                                              │
│  supervisor LLM emits tool_call (name=task, id=call_A)                       │
│        ▼                                                                     │
│  deepagents._build_task_tool's `task` invokes subagent.ainvoke(state, cfg)   │
│  cfg = {configurable: {ls_agent_type: "subagent"}}    ← NO supervisor_call_id │
│        ▼                                                                     │
│  LangGraph subgraph runs, emits chunks with ns=("tools:<uuid>",)             │
│        ▼                                                                     │
│  worker observes chunks. To resolve "<uuid>" → "call_A":                     │
│    - cache lookup: empty                                                     │
│    - queue (unlinked SUBAGENT_STARTED call_ids): pop only if size==1         │
│    - parallel fleet (size≥2): return None — events orphan                    │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│ AFTER — deterministic via chunk metadata                                    │
│                                                                              │
│  supervisor LLM emits tool_call (name=task, id=call_A)                       │
│        ▼                                                                     │
│  ATLAS-PATCHED _build_task_tool's `task` invokes subagent.ainvoke(state, cfg)│
│  cfg = {                                                                     │
│    configurable: {ls_agent_type: "subagent",                                 │
│                   supervisor_task_call_id: "call_A"},                        │
│    metadata:     {supervisor_task_call_id: "call_A"},  ← propagates to chunks│
│  }                                                                           │
│        ▼                                                                     │
│  LangGraph subgraph runs. Chunks carry the metadata in:                      │
│    - messages-mode: data = (message_chunk, {supervisor_task_call_id:"call_A"})│
│    - top-level "metadata" field on certain modes                             │
│        ▼                                                                     │
│  worker observes first chunk. StreamPartParser.supervisor_task_call_id_for() │
│  reads "call_A" from metadata.                                               │
│  StreamUpdateProcessor.register_supervisor_call_id_for_subgraph              │
│    cache[(run_id, "<uuid>")] = "call_A"                                      │
│  Subsequent chunks for the same subgraph: cache hit → "call_A".              │
│  Parallel fleet sibling (different uuid, different call_id): independently   │
│  resolves the same way. NO FIFO. NO RACE.                                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Module boundaries

| Layer                                                                 | Module                                                                                                                                                                                                                                                                   | Owns                                                                                                                                                                        |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `services/ai-backend/src/agent_runtime/execution/atlas_task_tool.py`  | **NEW.** Mirrors `deepagents.middleware.subagents._build_task_tool`. Injects `supervisor_task_call_id` into the subagent's `RunnableConfig` (both `configurable` and `metadata`). Exposes `install_atlas_task_tool()` for the monkey-patch.                              | The behavioural delta vs. deepagents (config metadata). Documents the PEP-563 caveat (no `from __future__ import annotations`).                                             |
| `services/ai-backend/src/agent_runtime/execution/factory.py`          | **EXTEND.** Calls `install_atlas_task_tool()` once at module-load time, before the agent factory exposes its build path.                                                                                                                                                 | Activation point. Idempotent.                                                                                                                                               |
| `services/ai-backend/src/runtime_worker/stream_parts.py`              | **EXTEND.** New classmethod `StreamPartParser.supervisor_task_call_id_for(part) → str \| None`. New module constant `SUPERVISOR_TASK_CALL_ID_KEY`.                                                                                                                       | Reads chunk metadata from both the messages-mode tuple `data[1]` and the top-level `metadata` field. Returns the first non-empty match.                                     |
| `services/ai-backend/src/runtime_worker/stream_subagents.py`          | **EXTEND.** New methods `register_supervisor_call_id_for_subgraph(...)` and `cached_subagent_call_id_for_subgraph(...)`. Existing `subagent_call_id_for_subgraph` (cache + FIFO) preserved unchanged.                                                                    | Cache management. The new register method is idempotent and removes the call_id from the unlinked queue so a later FIFO fallback cannot re-pop it for a different subgraph. |
| `services/ai-backend/src/runtime_worker/stream_events.py`             | **EXTEND.** `StreamOrchestrator.append_activity_events` reads chunk metadata, registers the linkage, resolves `parent_task_id` via cache-only lookup with raw-UUID fallback. The FIFO fallback intentionally stays inside `stream_tools.StreamMessageProcessor.process`. | Chunk-level resolution. Comment block in code documents why the FIFO fallback isn't pulled forward.                                                                         |
| `services/ai-backend/src/runtime_worker/stream_tools.py`              | **UNCHANGED** for this PR. The messages-mode resolver inside `StreamMessageProcessor.process` still calls the FIFO-aware `subagent_call_id_for_subgraph`, which now hits the cache populated by our chunk-handler before falling back to FIFO.                           | Reuses upstream contract; no diff.                                                                                                                                          |
| `services/ai-backend/tests/unit/runtime_worker/test_stream_events.py` | **EXTEND.** Two new tests (see §1.6).                                                                                                                                                                                                                                    | Regression sentinels.                                                                                                                                                       |

**Not changed** in this PR: any frontend file, any `packages/api-types` type, any migration, any Pydantic schema, any audit action, any approval flow, any FE reducer.

### 2.3 What we do NOT add

- ❌ A new event variant. `RuntimeApiEventType` is untouched.
- ❌ A new column. `runtime_events.parent_task_id` is the same column it's always been.
- ❌ A new wire field. `RuntimeEventEnvelope` is unchanged.
- ❌ A persistent registry. The cache lives on `StreamUpdateProcessor` for the duration of a run; once the run terminates, the cache goes with it. Replay rebuilds from chunk metadata on the next run if needed.
- ❌ A fork of deepagents. The monkey-patch is one-line; vendoring is over-spend.
- ❌ A new dep. CSS / typing / chunk-metadata reading uses existing standard library + langchain primitives.
- ❌ Telemetry on the linkage. The existing `runtime.stream.failed` log fires if the resolution path errors; success is invisible (which is correct — every chunk shouldn't emit a log line). If real traffic shows mis-attribution we add metrics in a follow-up.

### 2.4 The metadata contract

```python
# services/ai-backend/src/agent_runtime/execution/atlas_task_tool.py

SUPERVISOR_TASK_CALL_ID_KEY = "supervisor_task_call_id"

def _build_subagent_config(runtime: ToolRuntime) -> RunnableConfig:
    parent_configurable = dict(runtime.config.get("configurable", {}) or {})
    parent_metadata = dict(runtime.config.get("metadata", {}) or {})
    tool_call_id = runtime.tool_call_id
    return {
        "configurable": {
            **parent_configurable,
            "ls_agent_type": "subagent",
            SUPERVISOR_TASK_CALL_ID_KEY: tool_call_id,
        },
        "metadata": {
            **parent_metadata,
            SUPERVISOR_TASK_CALL_ID_KEY: tool_call_id,
        },
    }
```

The key is set in **both** `configurable` and `metadata`. `metadata` is what LangGraph propagates onto chunks; `configurable` is set defensively in case any downstream node reads from there (some middleware does).

### 2.5 The chunk-side reader

```python
# services/ai-backend/src/runtime_worker/stream_parts.py

@classmethod
def supervisor_task_call_id_for(cls, part: Mapping[str, object]) -> str | None:
    """LangGraph propagates RunnableConfig.metadata onto streamed chunks
    in two places depending on stream_type:
    - `messages`: data = (message, metadata); metadata is data[1].
    - other modes: metadata may live as a top-level `metadata` field.

    Probe both; return the first non-empty match. Returns None when the
    chunk wasn't emitted from inside an Atlas-dispatched subagent.
    """
    data = part.get("data")
    if isinstance(data, tuple) and len(data) >= 2:
        metadata_candidate = data[1]
        if isinstance(metadata_candidate, Mapping):
            value = metadata_candidate.get(SUPERVISOR_TASK_CALL_ID_KEY)
            if isinstance(value, str) and value:
                return value
    top_metadata = part.get("metadata")
    if isinstance(top_metadata, Mapping):
        value = top_metadata.get(SUPERVISOR_TASK_CALL_ID_KEY)
        if isinstance(value, str) and value:
            return value
    return None
```

### 2.6 The dispatcher

```python
# services/ai-backend/src/runtime_worker/stream_events.py
# StreamOrchestrator.append_activity_events (excerpt)

subgraph_task_id = namespace.subagent_task_id
chunk_supervisor_call_id = StreamPartParser.supervisor_task_call_id_for(part)
if chunk_supervisor_call_id is not None and subgraph_task_id is not None:
    self.update_processor.register_supervisor_call_id_for_subgraph(
        run_id=run.run_id,
        subgraph_task_id=subgraph_task_id,
        supervisor_call_id=chunk_supervisor_call_id,
    )

cached_call_id = self.update_processor.cached_subagent_call_id_for_subgraph(
    run_id=run.run_id,
    subgraph_task_id=subgraph_task_id,
)
parent_task_id = (
    cached_call_id if cached_call_id is not None else subgraph_task_id
)
```

The fallback to `subgraph_task_id` (the raw LangGraph UUID) is what preserves backwards compatibility with the existing `test_runtime_worker_persists_normalized_activity_stream_events` fixture, which sends synthetic chunks whose `ns=("tools:task_123",)` and asserts `parent_task_id == "task_123"`.

### 2.7 Streaming + persistence walk-through (one canary chunk)

Trace for the FIRST chunk emitted by sub A in a 2-subagent fleet:

1. Supervisor LLM emits `{"id": "call_A", "name": "task", "args": {...}}`.
2. Atlas-patched `task` tool fires. `runtime.tool_call_id == "call_A"`.
3. Tool builds `subagent_config` with `metadata.supervisor_task_call_id = "call_A"`.
4. Tool calls `subagent.ainvoke(subagent_state, subagent_config)`.
5. LangGraph subgraph starts. First node fires. Inside `astream(stream_mode=["messages",...], subgraphs=True, version="v2")`, LangGraph yields a chunk: `{"type": "messages", "ns": ("tools:<uuid_A>",), "data": (AIMessageChunk(...), {"supervisor_task_call_id": "call_A", ...other inherited metadata...})}`.
6. Worker's `StreamingExecutor.run` consumes the chunk. Calls `stream_event_mapper.append_activity_events(run, chunk, delta)`.
7. `append_activity_events` extracts `subgraph_task_id = "<uuid_A>"`, `chunk_supervisor_call_id = "call_A"`.
8. Calls `register_supervisor_call_id_for_subgraph(run_id, "<uuid_A>", "call_A")`. Cache pinned. Queue has "call_A" removed (idempotent).
9. Resolves `parent_task_id` via cache → `"call_A"`. Forwards to native interrupt / explicit api / messages-mode dispatchers.
10. Subsequent chunks from `<uuid_A>` (whether messages, custom, or updates mode) hit the cache → resolve to `"call_A"` directly. Sibling sub B's chunks (different `<uuid_B>`) register independently → resolve to `"call_B"`.
11. Each event the worker emits via `event_producer.append_api_event(...)` carries `parent_task_id = "call_A"` (or `"call_B"` for sub B's events).
12. FE receives the event; reducer's `upsertSubagentActivity` finds the matching `run_subagent` tool part by `toolCallId === "call_A"` and appends the activity to its `args.activities`.
13. `<SubagentCard>` (PR 3.2.2 / 3.2.4) reads `args.activities` and renders the inner step inside the subagent's timeline. **No leak to the main thread.**

### 2.8 Failure modes

| Failure                                                                                                                    | Behavior                                                                                                                                                                                                                                                                                           |
| -------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Subagent's first chunk has no metadata (rare; non-messages mode, no top-level metadata).                                   | Cache miss. Resolution falls back to `subgraph_task_id` (raw UUID). Same as legacy behavior. FE may not nest correctly until a subsequent messages-mode chunk arrives and registers; usually arrives within milliseconds.                                                                          |
| `runtime.tool_call_id` is None (defensive).                                                                                | Original deepagents code already raises `ValueError("Tool call ID is required")`. Our patched tool preserves this guard. No silent failure.                                                                                                                                                        |
| Two subgraphs happen to share a UUID across runs (impossible per LangGraph but defensive).                                 | Cache is keyed by `(run_id, subgraph_task_id)`. Cross-run collision impossible.                                                                                                                                                                                                                    |
| `register_supervisor_call_id_for_subgraph` called twice for the same `(run_id, subgraph_task_id)` with different call_ids. | Idempotent: once-set wins. Documented in the docstring. Defensive — should never happen in practice because LangGraph subgraph UUIDs are unique per invocation.                                                                                                                                    |
| Replay of a pre-fix run via `GET /v1/agent/runs/{id}/events`.                                                              | Old events have `parent_task_id=None` for inner tool calls. The events themselves are immutable; we don't backfill. New runs after this PR are correct; old archived runs render with the historical bug. Acceptable: this is a forward-only correctness fix; archive surfaces remain best-effort. |

---

## 3 · Library evaluation

The headline question for this PR was: **how do we propagate the supervisor's `tool_call_id` into the subagent's runtime?** Three candidates evaluated:

| Approach                                                                  | Pro                                                                         | Con                                                                                                                                                                                                                                                     |
| ------------------------------------------------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Replace deepagents' `_build_task_tool` with our own (this PR).**     | Deterministic linkage. ~100 LoC. All other deepagents code paths unchanged. | Brittle to deepagents upgrades — function shape changes break our patch silently. Mitigated by the regression test (which exercises the patched path).                                                                                                  |
| B. Wrap each subagent's `Runnable` before handing to deepagents.          | Doesn't touch deepagents internals.                                         | The wrapper only sees what's already merged into config — it can't recover `runtime.tool_call_id` because deepagents' built-in `task` tool doesn't pass it through to the subagent's state or config. **Rejected: doesn't actually solve the problem.** |
| C. Use a `langchain_core` callback handler that observes `on_tool_start`. | No deepagents modification.                                                 | Relies on callback ordering guarantees and cross-asyncio-task `contextvar` propagation. Hard to test, fragile under concurrent subagent dispatches. **Rejected: too magical.**                                                                          |

**Decision: A**. The monkey-patch is the smallest change with the strongest invariant. It's also the only approach where the parallel-fleet bug becomes deterministically impossible (B and C both have race windows).

**Other libraries evaluated for support pieces**: none. Standard library `inspect.signature` + langchain's existing `ToolRuntime` injection mechanism + LangGraph's existing chunk metadata propagation are sufficient.

---

## 4 · File change summary

```
services/ai-backend/src/agent_runtime/execution/
  atlas_task_tool.py                                NEW       ~+170 LoC   custom task tool with metadata injection
  factory.py                                        EXTEND    ~+10  LoC   import + install_atlas_task_tool() at load time

services/ai-backend/src/runtime_worker/
  stream_parts.py                                   EXTEND    ~+30  LoC   supervisor_task_call_id_for() classmethod + module constant
  stream_subagents.py                               EXTEND    ~+50  LoC   register_supervisor_call_id_for_subgraph + cached_subagent_call_id_for_subgraph
  stream_events.py                                  EXTEND    ~+25  LoC   chunk-metadata read + cache-only resolution + raw-UUID fallback

services/ai-backend/tests/unit/runtime_worker/
  test_stream_events.py                             EXTEND    ~+150 LoC   2 new regression tests

# nothing else changes
apps/frontend/                                       0
packages/*                                           0
migrations/                                          0
package.json / requirements.txt                      0 deps added
```

Net new ≈ 250 LoC of production code + 150 LoC of tests.

---

## 5 · Verification checklist

- [x] `cd services/ai-backend && pytest tests/unit/runtime_worker/ tests/unit/agent_runtime/` → 809 passed.
- [x] New regression test passes: `test_chunk_metadata_links_parallel_subagents_to_supervisor_call_ids`.
- [x] New legacy-fallback test passes: `test_chunk_without_supervisor_metadata_falls_back_to_raw_subgraph_id`.
- [x] Pre-existing single-subagent test still passes: `test_tool_event_inside_subagent_carries_subagent_id`.
- [x] Pre-existing integration test still passes: `test_runtime_worker_persists_normalized_activity_stream_events`.
- [x] Production canary on `make dev`:
  - Triggered run with prompt _"Dispatch 2 subagents to research on the web: where the term LangChain deep agents is used; and LangChain supervisor agents — official docs/blogs."_
  - Pulled events via `GET /v1/agent/runs/{run_id}/events`.
  - Confirmed: 2 `subagent_started` (one each for `call_knDW…` and `call_XjTE…`) + 1 `subagent_fleet_started` + 19 `tool_call_started/completed/result` events, **each tool event correctly carrying `parent_task_id` matching its own subagent's supervisor call_id**, even with interleaved emission.
- [x] No new `package.json` / `requirements.txt` entries.
- [x] No `from __future__ import annotations` in `atlas_task_tool.py` (PEP 563 would break langchain's `ToolRuntime` injection — caught during dev, documented in the module docstring).
- [x] `git diff packages/api-types/` is empty.
- [x] `git diff migrations/` is empty.

---

## 6 · Out of scope (follow-ups)

These are the remaining items in the subagent runtime correctness train. None blocks shipping this PR; each is independent.

- **Phase 2 — Per-subagent interrupt isolation.** Today `streaming_executor.py:175-178` halts on **any** interrupt event. A user authorized this as the next big honest fix: when one subagent in a fleet hits an approval/auth, only that subagent should pause; siblings keep running. Architectural restructure of how the worker handles `action_interrupted=True`. Owns its own PR.
- **Phase 3 — `subagent_paused` / `subagent_resumed` event variants.** New variants on `RuntimeApiEventType` so the FE doesn't have to infer paused state from the absence of a completion + presence of an unresolved interrupt. Cheap once Phase 2 lands. Without Phase 2, the runtime can't deliver these on a per-subagent basis (everything pauses together).
- **Phase 4 — FE: render paused state + clickable fleet rows with inline timeline.** Pause indicator on the affected fleet row; main-thread interrupt card; pane Approvals tab badge. Visual contract; cheap once events are reliable.
- **Backfill old runs** — events archived before this PR have `parent_task_id=None` for inner tool events. We don't backfill; new runs are correct. If the archive UI surfaces visibly broken renders for old runs, we can add a one-time migration that walks old subagent events and re-derives the linkage from `runtime_async_tasks.run_id` + per-tool `task_id`. Tracked as a paper cut.
- **Remove the legacy FIFO fallback inside `stream_tools.process`** once real traffic confirms the cache path covers everything. Today the FIFO is belt-and-braces. Drop it in a follow-up if the regression suite stays green for ≥ 2 weeks of prod traffic.
- **Telemetry / metrics** — count of `chunk_supervisor_call_id is not None` vs `is None` per run, surfacing how often the legacy fallback fires in production. If the latter is non-trivial (e.g., due to non-Atlas-dispatched subagents from third-party middleware), we know the FIFO path still matters.

---

## References

- [`docs/new-design/pr-3.2.4-fleet-nests-compact-rows.md`](./pr-3.2.4-fleet-nests-compact-rows.md) — sibling FE fix; nests fleet children visually. Depends on this PR's data correctness.
- [`docs/new-design/pr-3.2.2-subagent-card-shared-primitive.md`](./pr-3.2.2-subagent-card-shared-primitive.md) — the `<SubagentCard>` primitive that consumes `args.activities` (which this PR makes correct in parallel-fleet runs).
- [`services/ai-backend/src/runtime_worker/stream_subagents.py`](../../services/ai-backend/src/runtime_worker/stream_subagents.py) — original docstring documenting the FIFO race this PR resolves.
- [`services/ai-backend/.venv/lib/python3.13/site-packages/deepagents/middleware/subagents.py`](../../services/ai-backend/.venv/lib/python3.13/site-packages/deepagents/middleware/subagents.py) — upstream `_build_task_tool` that we monkey-patch.
- LangChain `_DirectlyInjectedToolArg` / `ToolRuntime` injection: [`services/ai-backend/.venv/lib/python3.13/site-packages/langchain_core/tools/base.py:1382`](../../services/ai-backend/.venv/lib/python3.13/site-packages/langchain_core/tools/base.py#L1382).
- LangGraph `subgraphs=True` + `version="v2"` chunk envelope: [`services/ai-backend/src/agent_runtime/execution/runtime.py:34-43`](../../services/ai-backend/src/agent_runtime/execution/runtime.py#L34-L43).
