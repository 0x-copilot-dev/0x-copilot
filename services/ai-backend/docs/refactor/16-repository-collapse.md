# Refactor PRD — Repository pattern collapse (P19 / Phase 5)

**Status:** Draft — pre-investigation. **High retraction risk** — see disclaimer below.
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §2.1](../architecture/refactor-audit.md#21-9-persistence-ports--17-record-types)
**Roadmap slot:** [P19](00-roadmap.md#phase-5--major-library-swaps--structural-shifts)
**Pre-requisite:** [P5 async-only ports](01-async-only-ports.md) — **shipped**. `agent_runtime/api/ports.py` is now async-native single-Protocol; `async_ports.py` + `async_wrappers.py` + `AsyncInMemoryRuntimeApiStore` are deleted. Anything in this PRD that referenced sync/async pair plumbing is past-tense.
**Risk:** High — this is the largest single restructure in the audit. The retraction-risk disclaimer below is doubly relevant.

---

## Retraction-risk disclaimer

This PRD was drafted from architecture diagrams without reading the source. The pattern in this codebase is that diagram-derived PRDs about "N files / N ports — collapse to M" frequently get retracted after code review because what looks like duplication on a diagram is often genuine separation of concerns:

- [`11-citations-consolidation.md`](11-citations-consolidation.md) (originally P14) — **retracted**. Diagram said "8 citation files duplicate one another, collapse to 3." Code review found three distinct subsystems with clear boundaries and a single source of truth (`CitationLedger._register_internal`). Nothing to collapse.
- [`12-worker-stream-cleanup.md`](12-worker-stream-cleanup.md) (originally P15) — **retracted**. Diagram said `ApprovalRecognisers` was a stream pattern-matcher; code said it's a tool-args projector for approval cards. Diagram said `ToolCallLedger` duplicated persistence; code said it's 139 LOC of in-flight tracking that emits synthetic terminal events on crash. All three smells were wrong.

**This PRD's central claim — "9 ports + 17 records → 4 repositories" — is structurally the same kind of claim.** It is highly likely that several of the 9 ports represent legitimately separate domain surfaces (Drafts vs Citations vs Shares are not the same concern), and that several of the 17 records are boundary types whose Pydantic shape is non-negotiable per [`docs/CLAUDE.md`](../CLAUDE.md).

**The 4-repository split in §3 is a hypothesis, not a prescription.** The verification spike in §2 is the gate.

If verification shows the current decomposition is already correct, this PRD becomes **withdrawn** (like P14/P15), not "implement smaller scope."

---

---

## 1. Problem (hypothesis — verify in §2 before acting)

The persistence layer **may** carry more port surface area than the production split (in-memory + Postgres) justifies:

- **9+ persistence Protocols** in [`agent_runtime/persistence/ports.py`](../../src/agent_runtime/persistence/ports.py): `MemoryMetadataPort`, `PayloadStoragePort`, `CheckpointStorePort`, `DraftStorePort`, `SubagentStorePort`, `SourceStorePort`, `CitationStorePort`, `ConversationToolOrdinalStorePort`, `ShareStorePort`.
- **Plus the trio in [`agent_runtime/api/ports.py`](../../src/agent_runtime/api/ports.py)** (post-[P5](01-async-only-ports.md), now async-native single-Protocol): `PersistencePort`, `EventStorePort`, `RuntimeQueuePort`.
- **17+ record types** in [`agent_runtime/persistence/records/`](../../src/agent_runtime/persistence/records/). Each is a Pydantic model wrapping a row.
- **Two adapter trees** ([`runtime_adapters/in_memory/`](../../src/runtime_adapters/in_memory/) + [`runtime_adapters/postgres/`](../../src/runtime_adapters/postgres/)) where every port × every adapter = a file.

**Crucial caveat.** The retracted PRDs P14 and P15 also started with a "many files, must duplicate" framing. Both turned out to be wrong on code review. Treat the count above as raw inventory, not as evidence of duplication.

Adding a column requires:

1. Update the Pydantic record.
2. Update the port (Protocol method signature).
3. Update the in-memory adapter.
4. Update the Postgres adapter.
5. Update the schema DDL.
6. Update tests.

Six places for one column. Every developer pays this tax.

### What this actually buys today

The 9 ports exist mostly as **one Protocol per domain object** (citation, ordinal, draft, share, source, subagent, …). The hexagonal pattern's value is the ability to swap implementations — and the codebase has exactly two: in-memory (tests) and Postgres (prod). The interface granularity is dialed up far past that need.

### What this is NOT

- Not a switch from `psycopg` to anything else; or from `asyncpg` to anything else. The driver doesn't change.
- Not a change to the **Pydantic boundary contracts** (HTTP request / response, runtime events, MCP descriptors). Pydantic stays at boundaries.
- Not a database schema rewrite. The tables don't change shape; what changes is how Python code maps to them.
- Not P17 (Checkpointer) or P18 (partitioning) — those touch specific subsystems. P19 is the broader persistence-layer collapse.
- Not removing the in-memory adapter. Tests still need it; it just becomes much smaller because there's less surface to mirror.

---

## 2. Verification required before approval

This is the highest-risk PRD in the audit. Each row below can flip the design.

| Question                                                                                                      | How to answer                                                                                  | If answer is X, then PRD shape changes how                                                                                                 |
| ------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Which ports are actually _used_ by more than one caller, and which are single-caller?                         | Grep callers of each Protocol method across `agent_runtime/` and `runtime_worker/`.            | Single-caller ports can become methods on the caller's class; no Protocol needed at all.                                                   |
| Does every record type round-trip through HTTP or events, or are some pure storage shapes?                    | Grep each record's import sites.                                                               | Storage-only records can become SQLAlchemy ORM models directly; only boundary types stay Pydantic.                                         |
| What idempotency / uniqueness constraints are pinned in tests today?                                          | Read tests under `tests/unit/agent_runtime/persistence/`.                                      | Each constraint becomes a non-negotiable post-refactor assertion. List explicitly here before §5.                                          |
| Does `FieldCodec` work at the row level, the field level, or both?                                            | Read [`runtime_adapters/postgres/`](../../src/runtime_adapters/postgres/) field codec helpers. | Determines whether ORM models can declare encrypted columns inline or need an explicit `TypeDecorator`.                                    |
| Are there subtle invariants in `PersistenceValueNormalizer` (in `records/common.py`) that adapters depend on? | Read that file + grep usage.                                                                   | If yes: normalizer logic must move into the ORM type adapters or repository methods, not be lost.                                          |
| What share / draft / citation tests rely on adapter-level fakes versus repository-level fakes?                | Grep `InMemory.*Store` in tests.                                                               | Determines whether the in-memory adapter becomes a single in-memory repository or multiple.                                                |
| Is SQLAlchemy 2.0 acceptable as a dependency? Does Deep Agents pin an incompatible version?                   | Check pyproject.toml + Deep Agents pin.                                                        | If conflict: PRD blocks on resolving the dep conflict; SQLAlchemy 1.4 with `future=True` is a fallback.                                    |
| Are any current ports leaked across deployable boundaries (e.g. imported from `services/backend`)?            | Grep `from agent_runtime.persistence` outside ai-backend.                                      | Should be **none** per the monorepo CLAUDE.md. If any exist, fix that first; otherwise re-importing collapse breaks cross-service compile. |

---

## 3. Goal and non-goals

### Goal

Collapse the 9 per-domain-object ports + 17+ record types into **3–4 topical repositories** backed by SQLAlchemy 2.0 (async). Pydantic stays at HTTP / event boundaries; SQLAlchemy ORM owns rows.

Suggested topical split (to be confirmed during Phase A spike — naming/grouping may shift):

- **`RunRepository`** — runs, events, queue (outbox), approvals, run-level telemetry, tool invocations, async tasks.
- **`WorkspaceRepository`** — drafts, shares, citations, ordinals, sources, subagent projections, workspace feeds.
- **`MemoryRepository`** — memory items, scoped payloads, checkpoints (or none if [P17](14-langgraph-checkpointer.md) lands first and deletes them).
- **`AdminRepository`** — audit log, retention policy records, budget rows, pricing records, usage rollups.

### Non-goals

- Reducing the **methods** any caller can invoke. The repositories expose the union of today's per-port methods.
- Changing Pydantic boundary contracts. Every Pydantic type currently consumed by HTTP / SSE / MCP / runtime events is preserved.
- Removing the in-memory adapter. It becomes one in-memory repository per topical split, much smaller than 17+ files today.
- Splitting any repository smaller than this. If `RunRepository` ends up at 1.5k LOC, that is fine — it's still one file.

### Success criteria

- 9 persistence ports → 4 repository classes.
- 17+ record types → either:
  - **ORM models** (the storage shape), or
  - **Pydantic boundary types**, with explicit `to_orm()` / `from_orm()` mapping methods on the boundary side.
- 2 adapter trees become 2 repository implementations (in-memory + Postgres) per topical repository — so 8 implementation files total, down from ~25+.
- All idempotency constraints from §5 pin tests pass byte-identical results before and after.
- All Pydantic boundary contracts unchanged — `pip diff` on `packages/api-types` and the runtime event schemas shows zero changes.
- `pg_stat_activity` continues to show role-tagged `application_name`.
- No regression on any benchmark from [Phase 1 baseline](00-roadmap.md#phase-1--performance-wins-no-structural-change).

---

## 4. Systems touched

**Provisional inventory pending Phase A spike.**

### 4.1 Files deleted

| File                                                                                         | Reason                                                                     |
| -------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Most of [`agent_runtime/persistence/records/`](../../src/agent_runtime/persistence/records/) | 17+ record files collapse into ORM models or boundary types                |
| Most of [`runtime_adapters/in_memory/`](../../src/runtime_adapters/in_memory/)               | Per-domain in-memory stores collapse into per-topic in-memory repositories |
| Most of [`runtime_adapters/postgres/`](../../src/runtime_adapters/postgres/)                 | Same, Postgres side                                                        |
| `agent_runtime/persistence/ports.py` (post-P5, async-only)                                   | 9 Protocols collapse into 4 repository classes                             |

### 4.2 Files added

| File                                                                | Purpose                                                                    |
| ------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `agent_runtime/persistence/orm/__init__.py` + per-table model files | SQLAlchemy 2.0 declarative models                                          |
| `agent_runtime/persistence/orm/base.py`                             | `DeclarativeBase`, naming convention, encrypted column type, common mixins |
| `agent_runtime/persistence/repositories/run_repository.py`          | RunRepository protocol + implementations                                   |
| `agent_runtime/persistence/repositories/workspace_repository.py`    | WorkspaceRepository protocol + implementations                             |
| `agent_runtime/persistence/repositories/memory_repository.py`       | MemoryRepository protocol + implementations                                |
| `agent_runtime/persistence/repositories/admin_repository.py`        | AdminRepository protocol + implementations                                 |
| `runtime_adapters/in_memory_repositories/*`                         | In-memory repositories (test)                                              |
| `runtime_adapters/postgres_repositories/*`                          | Postgres repositories (prod), wired to SQLAlchemy async engine             |

### 4.3 Files changed

| File                                                                   | Change                                                                      |
| ---------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| [`runtime_adapters/factory.py`](../../src/runtime_adapters/factory.py) | Construct repositories instead of stores; expose them through the dataclass |
| Every caller of a current Protocol method                              | Move to calling repository method (mostly mechanical)                       |
| Tests                                                                  | Switch from per-port fakes to per-repository in-memory implementations      |

### 4.4 Schema changes

None planned. Tables stay; only the Python-side mapping changes. Confirm during Phase A that nothing about the existing DDL is incompatible with SQLAlchemy 2.0 declarative-mapped types.

---

## 5. Behaviors preserved

Pulled from [refactor-audit § Behaviors that must be preserved](../architecture/refactor-audit.md#behaviors-that-must-be-preserved). Each pinned by an explicit test.

### Idempotency / uniqueness

- `UNIQUE(run_id, sequence_no)` on `runtime_events`.
- `UNIQUE(tool_call_id)` on the ordinal table.
- `UNIQUE(run_id, connector, doc_id)` on the citation table.
- `UNIQUE(worker_id, claim_id)` on the queue claim table (or equivalent — confirm).
- Any other UNIQUE present in the DDL today.

### Concurrency

- `set_run_latest_sequence` never rewinds (whether enforced by `WHERE ... > current_latest` or trigger, must continue to enforce).
- Per-run write serialization (current implementation: `SELECT FOR UPDATE` — see [P16](00-roadmap.md#phase-4--targeted-decoupling) for the eventual replacement; today's behavior must be preserved through this PR).
- Worker concurrency bounded by `settings.execution.max_parallel_runs`.

### Encryption

- Field-level encryption via `FieldCodec` works for every encrypted column. The ORM column type performs encrypt-on-write and decrypt-on-read transparently.
- Key rotation path (`EncryptExistingColumns` / its Alembic successor per [P6](00-roadmap.md#phase-2--decoupling-foundation--hygiene)) operates on partitioned and non-partitioned tables alike.

### Boundary contracts

- Every Pydantic model in [`runtime_api/schemas/`](../../src/runtime_api/schemas/) is unchanged.
- Every record type read by SSE / HTTP returns identical JSON.
- `RuntimeEventEnvelope` round-trips byte-identically.

### Observability

- Role-tagged `application_name` on the SQLAlchemy async engine (`api` / `worker`).
- All current SQL statement instrumentation (pre-OTel per [P13](00-roadmap.md#phase-3--library-replacements-independent) or OTel after) still tags `db.statement`.

---

## 6. Phasing

This refactor cannot land in one PR. The cutover must be done per repository, with both old and new code coexisting in tree across multiple PRs.

### Phase A — Investigation spike (1–2 weeks)

Answer every row in §2. Decide the exact repository split. Produce a mapping table: every existing port method → its new repository method. **No production code changes.** Output is an updated version of this PRD with concrete repository signatures filled in.

### Phase B — ORM models, no callers (1 week)

Add SQLAlchemy 2.0 models for every table. Configure the encrypted column type. Add unit tests that round-trip each ORM model. Do **not** wire anything to production yet. The new code lives alongside the old.

### Phase C — RunRepository (one PR)

Implement `RunRepository` (in-memory + Postgres). Dual-write through both old ports and the new repository in development; route reads through repository under a feature flag (`RUNTIME_USE_RUN_REPOSITORY`). Land all RunRepository tests. Enable flag in staging.

### Phase D — Per-repository cutover (one PR each)

`WorkspaceRepository`, `MemoryRepository`, `AdminRepository` — each follows Phase C's pattern. Independent feature flags.

### Phase E — Old code retirement

Once every repository's flag is on in production for at least one stable week: delete the old ports, records, and adapters. This is the biggest single-PR diff in the refactor, but it's behaviorally trivial because reads and writes are already going through the new path.

### Phase F — Verification

Latency benchmark vs [Phase 1 baseline](00-roadmap.md#phase-1--performance-wins-no-structural-change). Idempotency tests strengthened (per §5). Schema dump diff vs pre-refactor (must be **zero** difference).

---

## 7. Risks

| Risk                                                                                   | Severity         | Mitigation                                                                                                                                                   |
| -------------------------------------------------------------------------------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Silently drop an idempotency constraint during the rewrite                             | High             | §5 lists each constraint; tests pin each before any code moves. Tests are written in Phase A.                                                                |
| Lose a `PersistenceValueNormalizer` invariant                                          | Medium           | Phase A audits the normalizer; behavior moves into ORM `TypeDecorator` or repository method, not just deleted.                                               |
| Performance regression — SQLAlchemy adds overhead vs raw asyncpg                       | Medium           | Run Phase 1 benchmarks at the end of each phase. SQLAlchemy 2.0 async is competitive; if any hot path regresses, drop to raw asyncpg for that specific call. |
| The 4-repository split is wrong — turns out a 6-repository split is the real shape     | Medium           | Phase A spike is the time to discover this. Better to ship a 6-way split than to ship 4 and need to re-split.                                                |
| Cross-service import leak found during Phase A                                         | Medium (process) | Fix the leak first; then continue. Audit history is on side of "ai-backend imports nothing from other deployable services," but verify.                      |
| Migration of in-memory tests stalls on per-port fake construction patterns             | Low–Medium       | Each test file is mechanical. Budget 1 hour per file × ~38 files (per [P5 PRD](01-async-only-ports.md) count).                                               |
| Field-level encryption breaks during ORM column type conversion                        | High             | Phase B writes round-trip tests for every encrypted column before any caller moves.                                                                          |
| Alembic autogeneration sees the new ORM models and proposes destructive schema changes | Medium           | Use `--autogenerate` in dry-run mode. Disable `compare_type`/`compare_server_default` if it produces noise. Each migration is reviewed.                      |

---

## 8. Unit testing requirements

Per [`docs/CLAUDE.md`](../CLAUDE.md). Pin every behavior in §5 with at least one test.

### New (write before any code change)

- **`test_idempotency_constraints.py`** — for each constraint in §5, write a test that proves a duplicate-insert raises the expected typed exception. Currently passing tests stay passing; new tests cover constraints that aren't currently pinned.
- **`test_set_latest_sequence_never_rewinds.py`** — concurrent calls with stale and fresh sequence_no; only the fresh one wins.
- **`test_encrypted_columns_roundtrip.py`** — for each encrypted field: write encrypted, read decrypted, assert match.
- **`test_pool_role_tagging.py`** — `application_name` is `api` from API process, `worker` from worker process.

### Existing — keep green

- Every test under `tests/unit/agent_runtime/persistence/*` runs unchanged after each phase.
- Every test that constructs an in-memory store inline is migrated to construct an in-memory repository (Phase C/D pattern).

### Performance

- Per-phase rerun of the [Phase 1 baseline](00-roadmap.md#phase-1--performance-wins-no-structural-change) latency benchmark. p99 must not regress > 5%.

### Schema snapshot

- A test that dumps the schema (via `alembic` + a diff helper) and asserts byte-identical to a checked-in snapshot. New repositories add zero rows to the diff.

---

## 9. Rollback plan

Multi-PR feature-flagged rollout. Per phase:

- **Phase B (ORM models added)** — rollback = `git revert`. No production impact; nothing is wired up.
- **Phase C-D (per-repository cutover)** — rollback = flip the flag off. Old port code is still in tree.
- **Phase E (delete old code)** — rollback = `git revert` of that PR. Slightly painful but bounded: the deleted code was unused for at least a week before deletion.

Each Phase C/D PR ships with a "fall back to old ports" feature flag and a smoke-test runbook. The flag is removed in Phase E, not before.

---

## 10. Dependencies on other roadmap items

- **Hard depends on:** [P5 async-only ports](01-async-only-ports.md). Repository methods are `async def`; there is no sync surface to support.
- **Hard depends on:** [P6 cleanup wave / Alembic adoption](00-roadmap.md#phase-2--decoupling-foundation--hygiene). New ORM models need Alembic to autogenerate / verify schema; the bespoke migration runner is unsuitable.
- **Should land before:** [P22 RuntimeApiService split](00-roadmap.md#phase-6--coordinator-split-do-last). The coordinator's persistence surface shrinks naturally as repositories absorb the ports.
- **Independent of:** P17 (Checkpointer), P18 (partitioning), P20 (LiteLLM providers), P21 (LangGraph interrupts). May interleave with any of these.

---

## 11. Open questions tracked from §2

(Filled in during Phase A spike.)

- [ ] Which Protocols are single-caller and can become inlined methods?
- [ ] Which records are storage-only vs boundary types?
- [ ] Full list of idempotency / uniqueness constraints currently in DDL.
- [ ] `FieldCodec` granularity — column or row?
- [ ] `PersistenceValueNormalizer` invariants — exhaustive list.
- [ ] SQLAlchemy 2.0 compatibility with current Deep Agents pin.
- [ ] Cross-service import leakage check.
- [ ] Repository split confirmed: 4-way, 5-way, or 6-way?
