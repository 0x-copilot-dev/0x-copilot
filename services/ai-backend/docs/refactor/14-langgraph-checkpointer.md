# Refactor PRD — LangGraph Checkpointer adoption (P17 / Phase 5)

**Status:** Draft — pre-investigation. **High retraction risk** — see disclaimer below.
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §3](../architecture/refactor-audit.md#3-library-replacements) (CheckpointStorePort row)
**Roadmap slot:** [P17](00-roadmap.md#phase-5--major-library-swaps--structural-shifts)
**Pre-requisite:** none in this folder; LangGraph version pinned by Deep Agents dependency
**Blocks:** [P21 LangGraph interrupts](18-langgraph-interrupts.md) — durable interrupts require Checkpointer

---

## Retraction-risk disclaimer

This PRD was drafted from architecture diagrams without reading the source. The pattern in this codebase is that diagram-derived PRDs frequently get retracted after code review because the documented design hides genuine separation of concerns that look like duplication on a diagram:

- [`11-citations-consolidation.md`](11-citations-consolidation.md) (originally P14) — **retracted**: 8 citation files turned out to be 3 distinct subsystems with clear boundaries.
- [`12-worker-stream-cleanup.md`](12-worker-stream-cleanup.md) (originally P15) — **retracted**: all 3 hypothesized smells (`ApprovalRecognisers`, `ToolCallLedger`, over-split `stream_*` files) turned out to be well-designed components with distinct responsibilities.
- [`15-pg-partman-retention.md`](15-pg-partman-retention.md) (originally P18) — **superseded** by a different approach after code review.

**Before committing to this PRD's design**, complete the §2 verification spike. If `CheckpointStorePort` turns out to be: (a) genuinely vestigial / unused (delete-only refactor), or (b) load-bearing for some specific in-memory test-only behavior that doesn't need LangGraph at all, then this PRD changes shape entirely.

The verification questions in §2 are the gate, not the formalities.

---

---

## 1. Problem

The runtime carries a bespoke checkpoint subsystem alongside the LangGraph graph it already runs:

- **`CheckpointStorePort`** — Protocol in [`agent_runtime/persistence/ports.py`](../../src/agent_runtime/persistence/ports.py) (TBD LOC after verification).
- **`CheckpointRecord`** — Pydantic type in [`agent_runtime/persistence/records/checkpoints.py`](../../src/agent_runtime/persistence/records/checkpoints.py).
- **In-memory + Postgres adapters** — implementations in `runtime_adapters/in_memory/` and `runtime_adapters/postgres/`.

LangGraph ships its own `Checkpointer` subsystem ([`langgraph.checkpoint.postgres`](https://langchain-ai.github.io/langgraph/concepts/persistence/), `langgraph.checkpoint.memory`, `langgraph.checkpoint.sqlite`) that is the canonical persistence story for graph state inside a LangGraph application. The two stories overlap; one is bespoke and one is library-native.

### Symptoms (today)

- Two parallel persistence stories for "what is the durable state of a graph run." (Hypothesized — verify in code.)
- Adding a column to `CheckpointRecord` historically required touching record + port + adapter. Since [P5 async-only ports](01-async-only-ports.md) shipped, the sync-mirror surface is gone, but the rest of the drift surface remains.
- It is unclear from the architecture diagrams whether `CheckpointStorePort` is actively read by the graph builder, or whether LangGraph already runs without a checkpointer in production (i.e. the port may be **vestigial**, not load-bearing). This investigation is the first step before any code change.

### What this is NOT

- Not a graph-execution rewrite. LangGraph + Deep Agents stays.
- Not a change to `runtime_events` / `agent_runs` / approval persistence — checkpointer is graph state, not the runtime API's event log.
- Not a precursor to migrating LangGraph itself off Postgres. The replacement is Postgres-backed.

---

## 2. Verification required before approval

This PRD cannot be finalized until the following code-level facts are pinned. Treat each as a blocker: the PRD's recommendation may flip (as it did for the [audit-chain PRD](01-audit-chain.md)) once any of these comes back differently than assumed.

| Question                                                                                                                                                          | How to answer                                                                                                                                     | If answer is X, then PRD shape changes how                                                                                                                                                                |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Is `CheckpointStorePort` actually read by the Deep Agents / LangGraph builder, or only written to as dead code?                                                   | Grep `CheckpointStorePort`, `CheckpointRecord` across `agent_runtime/execution/` and `runtime_worker/`.                                           | If unused: PRD becomes a delete, no LangGraph adoption needed. If used: continue.                                                                                                                         |
| Is LangGraph already configured with a checkpointer (any backend) inside `DeepAgentBuilder`?                                                                      | Read [`agent_runtime/execution/deep_agent_builder.py`](../../src/agent_runtime/execution/deep_agent_builder.py).                                  | If yes: PRD becomes "consolidate dual checkpointers." If no: continue.                                                                                                                                    |
| Does the current `CheckpointStorePort` store any data LangGraph's native schema can't represent?                                                                  | Diff `CheckpointRecord` fields against [`langgraph.checkpoint.base.Checkpoint`](https://langchain-ai.github.io/langgraph/reference/checkpoints/). | If extra fields exist and are read: either store them in `metadata` blob alongside the LangGraph checkpoint, or keep a thin custom store for those fields only.                                           |
| What is the actual schema for LangGraph's Postgres Checkpointer? Does it conflict with `runtime_*` table names?                                                   | Inspect `langgraph.checkpoint.postgres` source / docs at the pinned version.                                                                      | If conflict: namespace via schema (e.g. `langgraph` schema) or table prefix. Migration adds the tables.                                                                                                   |
| How does P21 (LangGraph interrupts) intend to use the checkpointer? Are interrupt+resume across worker process restart durable only with a Postgres checkpointer? | Read LangGraph docs on `interrupt` / `resume` with checkpointers.                                                                                 | If interrupts need Postgres checkpointer for the durability guarantees [f8](../architecture/f8-mcp-auth.puml) describes, P17 must land before P21 — currently asserted as the dependency.                 |
| Is there any current "resume run after worker restart mid-stream" behavior, and what code path does it use?                                                       | Grep for resume / restart / replay paths in `runtime_worker/`.                                                                                    | If resume goes through `replay_events` and `CheckpointStorePort` is unused: P17 is a pure deletion + Checkpointer-introduction. If existing resume reads `CheckpointStorePort`: that path must port over. |

---

## 3. Goal and non-goals

### Goal

Have exactly one checkpoint persistence story for the LangGraph graph: **`langgraph.checkpoint.postgres.PostgresSaver`** (and `MemorySaver` for tests). Delete the bespoke `CheckpointStorePort` + `CheckpointRecord` + adapters, _or_ (if verification shows the bespoke store carries data LangGraph cannot represent) keep a minimal companion port for that residual data only.

### Non-goals

- Reorganizing `agent_runtime/persistence/` more broadly. That is [P19 repository collapse](16-repository-collapse.md).
- Changing what the graph itself does between steps. LangGraph decides what to checkpoint.
- Adding new resume semantics. If today's runtime doesn't resume mid-graph, this PRD doesn't add the capability — it just makes the persistence layer ready for P21.

### Success criteria

- `agent_runtime/persistence/records/checkpoints.py` deleted _or_ reduced to companion-only metadata (decision pinned by verification).
- `CheckpointStorePort` deleted _or_ renamed to reflect its companion role.
- `runtime_adapters/{in_memory,postgres}/checkpoint_store.py` (or equivalents) deleted, replaced by LangGraph's saver wiring.
- LangGraph builder in [`agent_runtime/execution/deep_agent_builder.py`](../../src/agent_runtime/execution/deep_agent_builder.py) constructs a `PostgresSaver` (or `MemorySaver` for tests) and passes it to `.compile(checkpointer=...)`.
- A migration adds LangGraph's checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`) under a namespaced schema or table prefix.
- Tests cover: cold-start graph compile, checkpoint write on step boundary, checkpoint read on resume thread.
- Latency benchmark from [Phase 1 baseline](00-roadmap.md#phase-1--performance-wins-no-structural-change) shows no regression on run-create p99 or steady-state event throughput.

---

## 4. Systems touched

**Pending verification.** Inventory below is provisional.

### 4.1 Files likely deleted

| File                                                                                                                | Reason                                                       |
| ------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| [`agent_runtime/persistence/records/checkpoints.py`](../../src/agent_runtime/persistence/records/checkpoints.py)    | `CheckpointRecord` superseded by LangGraph's checkpoint type |
| `runtime_adapters/in_memory/checkpoint_store.py`                                                                    | Bespoke in-memory checkpoint adapter                         |
| `runtime_adapters/postgres/checkpoint_store.py`                                                                     | Bespoke Postgres checkpoint adapter                          |
| `CheckpointStorePort` entry in [`agent_runtime/persistence/ports.py`](../../src/agent_runtime/persistence/ports.py) | Protocol superseded                                          |

### 4.2 Files changed

| File                                                                                                       | Change                                                                        |
| ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| [`agent_runtime/execution/deep_agent_builder.py`](../../src/agent_runtime/execution/deep_agent_builder.py) | Construct `PostgresSaver`/`MemorySaver`; pass to `.compile(checkpointer=...)` |
| [`agent_runtime/execution/factory.py`](../../src/agent_runtime/execution/factory.py)                       | Inject Checkpointer; remove any `CheckpointStorePort` wiring                  |
| [`runtime_adapters/factory.py`](../../src/runtime_adapters/factory.py)                                     | Drop checkpoint port wiring; expose Checkpointer construction helper          |
| `runtime_api/app.py` lifespan                                                                              | Open + close PostgresSaver alongside the existing pool                        |

### 4.3 New files

| File                                                                         | Purpose                                                                                                                                                                                 |
| ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agent_runtime/persistence/schema/langgraph_checkpointer.py`                 | Thin helper to construct `PostgresSaver` with the role-tagged pool from [refactor-audit §4](../architecture/refactor-audit.md#3-library-replacements) (`application_name=api`/`worker`) |
| Alembic migration adding LangGraph checkpoint tables under namespaced schema | DDL — `langgraph` schema, three tables                                                                                                                                                  |

---

## 5. Behaviors preserved

Pulled from [refactor-audit § Behaviors that must be preserved](../architecture/refactor-audit.md#behaviors-that-must-be-preserved). Pin tests to each.

- **No regression in run-create p99 or event throughput.** Adding a checkpointer that writes on every node transition could regress write volume; benchmark before and after.
- **Idempotent resume.** If any code path today reads from `CheckpointStorePort` to resume, the equivalent must work via LangGraph thread IDs after migration. Specifically: run replays via `?after_sequence=N` must continue to work (these read the event log, not the checkpoint — but verify).
- **Postgres pool role tagging.** LangGraph's saver must use the existing `application_name`-tagged pool (per [refactor-audit §C3](../architecture/refactor-audit.md#3-library-replacements)), not open its own.
- **Worker concurrency model.** `settings.execution.max_parallel_runs` is the only concurrency limit; LangGraph checkpointer must not introduce its own bottleneck (e.g. exclusive lock per thread).
- **Test-suite ergonomics.** Tests using `MemorySaver` must construct trivially — no event-loop ceremony beyond `pytest-asyncio` default.

---

## 6. Phasing

A single-phase PR is feasible if verification shows `CheckpointStorePort` is vestigial. Otherwise:

### Phase A — Investigation spike (1–2 days)

Answer every row in §2. Decide single-phase vs multi-phase. **No code changes ship.** Output is an updated version of this PRD with the verification results filled in and the recommended path chosen.

### Phase B — Coexistence (only if vestigial path is rejected)

Construct LangGraph's checkpointer alongside the bespoke port. Wire LangGraph's saver into `DeepAgentBuilder.compile`; leave `CheckpointStorePort` untouched. Verify checkpoints write and read correctly. Run full test suite.

### Phase C — Retirement

Delete the bespoke port + record + adapters. Migrate any companion data (if §2 verification found any) into a smaller residual port. Delete unused files.

### Phase D — Verification

Latency benchmark vs Phase 1 baseline. Schema migration applied in staging. Smoke-test approval flow ([f8](../architecture/f8-mcp-auth.puml)) end-to-end — relevant because [P21](18-langgraph-interrupts.md) will depend on this.

---

## 7. Risks

| Risk                                                                                                                                     | Severity | Mitigation                                                                                                                             |
| ---------------------------------------------------------------------------------------------------------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `CheckpointStorePort` turns out to carry domain data LangGraph cannot represent (e.g. tenant-scoped retention metadata, redaction flags) | High     | Verification step §2 must catch this. If yes: keep a thin companion port for those fields only; do not delete blindly.                 |
| LangGraph version pinned by Deep Agents is older than the stable `PostgresSaver` release                                                 | Medium   | Verify in Phase A. If outdated: bump Deep Agents, run regression, otherwise stay on `MemorySaver` until upgrade.                       |
| Checkpoint write volume regresses throughput                                                                                             | Medium   | Phase D benchmark. `PostgresSaver` writes per-step are typically batched; verify against Phase 1 baseline.                             |
| LangGraph checkpoint tables conflict with existing `runtime_*` naming                                                                    | Low      | Namespace via schema (`langgraph` schema) or table prefix in the migration.                                                            |
| Test setup breakage when switching to `MemorySaver`                                                                                      | Low      | Most tests don't checkpoint; those that do can use `MemorySaver` directly.                                                             |
| Migration window required if `agent_runs` already has long-lived rows that need checkpoints backfilled                                   | Medium   | If the bespoke store has historical data we care about: write a one-shot migration, otherwise truncate at cutover. Pin decision in §2. |

---

## 8. Unit testing requirements

Per [`docs/CLAUDE.md`](../CLAUDE.md), tests are added that pin each behavior listed in §5. Minimum:

- **`test_checkpointer_compile.py`** — graph compiles with `MemorySaver` and `PostgresSaver`; both produce checkpoint rows on step boundaries.
- **`test_checkpoint_resume.py`** — checkpoint written at step N; new builder reads it; graph state matches.
- **`test_pool_role_tagging.py`** — `PostgresSaver` connections appear in `pg_stat_activity` with the expected `application_name`.
- **`test_concurrency_limit.py`** — `max_parallel_runs=4` correctly bounds concurrent graph executions; `PostgresSaver` does not serialize across runs.
- **`test_resume_after_replay.py`** — `?after_sequence=N` SSE replay continues to work independently of checkpoint state (regression test for the orthogonality of the two persistence stories).

Existing tests touching `CheckpointStorePort`: enumerate during Phase A; convert or delete.

---

## 9. Rollback plan

Feature-flag the Checkpointer at `DeepAgentBuilder.compile` site (`RUNTIME_USE_LANGGRAPH_CHECKPOINTER=true|false`). Default `false` until staging confidence. Rollback = flip the flag; old port stays in tree through Phase B for exactly this reason.

After Phase C deletion, rollback is a `git revert` plus migration `DROP SCHEMA langgraph CASCADE`. Coordinate with [P21](18-langgraph-interrupts.md) — interrupts depend on this PR's outcome and cannot ship before it.

---

## 10. Open questions tracked from §2

(Filled in during Phase A spike, then this section becomes the decision record for the PRD.)

- [ ] Is `CheckpointStorePort` read at all today?
- [ ] Is LangGraph already running with a checkpointer (`MemorySaver` default)?
- [ ] Are there fields in `CheckpointRecord` that LangGraph's checkpoint cannot represent?
- [ ] Does LangGraph's Postgres saver schema collide with `runtime_*` tables?
- [ ] What is the precise dependency contract with P21 interrupts — Postgres saver required, or `MemorySaver` sufficient for the durability story?
- [ ] Is there a "resume run after worker crash mid-stream" path that uses checkpoints today?
