# Refactor Roadmap

**Status:** Draft
**Source of truth:** [`docs/architecture/refactor-audit.md`](../architecture/refactor-audit.md)
**Convention:** every entry below either points to a PRD already in this folder or is a placeholder PRD slot. PRDs follow the format in [`docs/CLAUDE.md`](../CLAUDE.md): Problem, Goals, Non-goals, Acceptance criteria, Risks, Unit testing requirements.

---

## How to read this roadmap

- PRs are grouped into six phases. **Within a phase** the items are independent and can ship in any order. **Across phases** later phases build on earlier ones — don't skip ahead.
- Each row links to either an existing PRD (already drafted in this folder) or a `TBD` marker for one that still needs a PRD before implementation.
- "Audit ref" links into [`refactor-audit.md`](../architecture/refactor-audit.md) where the finding is justified.
- Risk is the same scale as the audit (Low / Medium / High).
- "Behaviors preserved" lists the load-bearing invariants the PRD must pin tests to. Sourced from [`refactor-audit.md` § Behaviors that must be preserved](../architecture/refactor-audit.md#behaviors-that-must-be-preserved).

**Do not bundle a Phase-3 library swap with a Phase-1 latency fix.** Each PR ships one outcome with one rollback boundary.

---

## Phase 1 — Performance wins (no structural change)

Latency and cost wins that are self-contained and reversible. Land all four before any structural refactor — every cleanup downstream gets cheaper once event volume and LLM-call volume drop.

| #   | PR                                        | Audit ref                                                                                 | PRD                                                                      | Risk       | Behaviors preserved                                                                                                                             |
| --- | ----------------------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| P1  | PresentationGenerator polish removal      | [§1.1](../architecture/refactor-audit.md#11-presentationgenerator-polish-on-every-event)  | [`01-presentation-polish-removal.md`](01-presentation-polish-removal.md) | Low–Medium | `RuntimeEventPresentation` schema; frozen lifecycle fields (title / status_label / kind); `activity_kind` enum                                  |
| P2  | SSE bus → Postgres `LISTEN/NOTIFY`        | [§4.1](../architecture/refactor-audit.md#41-sse-delivery-is-1s-in-production)             | TBD: `02-sse-listen-notify.md`                                           | Medium     | SSE wire format; `?after_sequence=N` resume; `follow=false` heartbeat; terminal-status close; same fix on inbox bus                             |
| P3  | Parallel `create_agent_runtime` bootstrap | [§4.4](../architecture/refactor-audit.md#44-sequential-bootstrap-in-create_agent_runtime) | TBD: `03-parallel-bootstrap.md`                                          | Low        | Permission decisions (no listing past unauthorized scope); any inter-dep ordering that exists in code                                           |
| P4  | Per-event DB ops consolidation            | [§4.3](../architecture/refactor-audit.md#43-per-event-db-amplification)                   | TBD: `04-event-write-consolidation.md`                                   | Medium     | Per-run monotonic `sequence_no`; `UNIQUE(run_id, sequence_no)`; `set_run_latest_sequence` never rewinds; concurrent-write serialization per run |

**Phase exit criterion:** SSE p50 latency under 100ms in staging; visible-event volume per turn at least halved; no regression in run-create / approval-resolve p99. After this phase, run a representative latency benchmark and pin the numbers — every subsequent phase compares against this baseline.

---

## Phase 2 — Decoupling foundation + hygiene

Low-to-medium-risk work that shrinks the surface area before bigger restructures. Most of these don't touch product behavior at all.

| #   | PR                                              | Audit ref                                                                                                                                                                                                                                                                                                                                                           | PRD                                                | Risk        | Behaviors preserved                                                                            |
| --- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- | ----------- | ---------------------------------------------------------------------------------------------- |
| P5  | Async-only ports (sync ports + bridge deletion) | [§1.2](../architecture/refactor-audit.md#12-sync-ports--async-ports--async_wrappers-3-layers-for-1)                                                                                                                                                                                                                                                                 | [`01-async-only-ports.md`](01-async-only-ports.md) | Medium      | Test fakes still importable with default `pytest-asyncio` fixture; no Protocol drift           |
| P6  | Cleanup wave                                    | [§5.6](../architecture/refactor-audit.md#56-6-empty-legacy-directories-under-agent_runtime), [§1.7](../architecture/refactor-audit.md#17-custom-migration-runner), [§1.8](../architecture/refactor-audit.md#18-encryptexistingcolumns-running-as-a-perpetual-job), [§5.7](../architecture/refactor-audit.md#57-dev_auth_bypass_allowed-toggle-on-deploymentprofile) | TBD: `05-cleanup-wave.md`                          | Trivial–Low | None destructive; encryption logic preserved by Alembic data migration                         |
| P7  | Batch citation ingestion                        | [§4.5](../architecture/refactor-audit.md#45-sequential-citation-ingestion)                                                                                                                                                                                                                                                                                          | TBD: `06-citation-batching.md`                     | Low–Medium  | Idempotency `(run_id, connector, doc_id)`; ordinal allocation order matching marker references |
| P8  | Move misfiled modules                           | [§2.5](../architecture/refactor-audit.md#25-draftbackend-in-capabilities), [§5.4](../architecture/refactor-audit.md#54-atlas_task_toolpy-in-execution), [§5.5](../architecture/refactor-audit.md#55-agent_runtimeapi-mixes-coordinator-with-domain-services)                                                                                                        | TBD: `07-cluster-boundary-moves.md`                | Low         | Public import paths via re-exports if anything outside the package depends on them             |
| P9  | Service consolidation                           | [§2.3](../architecture/refactor-audit.md#23-four-way-permission-model-3-specific--1-generic), [§2.4](../architecture/refactor-audit.md#24-toolbudgetmiddleware--toolbudgetguard-two-step), [§2.6](../architecture/refactor-audit.md#26-service-splits-inside-c4-that-should-be-one-service-each)                                                                    | TBD: `08-service-consolidation.md`                 | Low–Medium  | All public methods on consolidated services; permission semantics (visibility + call-time)     |

**Phase exit criterion:** persistence Protocol family is single (async); empty legacy directories gone; misfiled modules in correct clusters. No new public surface added.

---

## Phase 3 — Library replacements (independent)

Each row swaps an in-house subsystem for a battle-tested library. None depend on each other; pick them up as time allows. Must each pass golden-output diff tests so behavioral drift is caught early.

| #   | PR                                             | Audit ref                                                                                                                             | PRD                                                      | Risk                | Behaviors preserved                                                               |
| --- | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- | ------------------- | --------------------------------------------------------------------------------- |
| P10 | Audit chain → SIEM-side / managed              | [§1.3](../architecture/refactor-audit.md#13-custom-hash-chained-audit-log)                                                            | [`01-audit-chain.md`](01-audit-chain.md)                 | Medium (compliance) | `AuditLogRecord` schema; append-only application semantics                        |
| P11 | Redactor → Presidio + detect-secrets           | [§1.4](../architecture/refactor-audit.md#14-custom-redactor)                                                                          | [`01-redaction-subsystem.md`](01-redaction-subsystem.md) | Medium              | Allow-listed user content keys; field-validator invocation; `redaction_state`     |
| P12 | Pricing source → LiteLLM (frozen rows)         | [§1.6](../architecture/refactor-audit.md#16-custom-budget--pricing-system--seed-catalog)                                              | TBD: `09-pricing-from-litellm.md`                        | Low                 | Cost stamped at write time; integer micro-USD; banker's rounding; CAS idempotency |
| P13 | OTel auto-instrumentation; thin observability/ | [§3](../architecture/refactor-audit.md#3-library-replacements) (rows on `db_statement_metrics` and the broader observability surface) | TBD: `10-otel-adoption.md`                               | Medium              | `usage_attribution` per-user/org/connector tagging; trace context propagation     |

**Phase exit criterion:** observability stack runs on OTel; pricing rows continue to match historical frozen values; redaction pass produces identical `payload`/`metadata` shapes for a representative event corpus.

---

## Phase 4 — Targeted decoupling

Larger, focused decompositions. Higher risk than Phase 2 but each is still a single-PR scope.

| #   | PR                                                                          | Audit ref                                                                                                                                                                                                                                                                                                | PRD                                                                           | Risk   | Behaviors preserved                                                                                                                                                                                                                         |
| --- | --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P14 | ~~Citation infrastructure consolidation~~ — **RETRACTED** after code review | [§2.2](../architecture/refactor-audit.md#22-8-files-of-citation-infrastructure)                                                                                                                                                                                                                          | [`11-citations-consolidation.md`](11-citations-consolidation.md) (retraction) | —      | Architecture is sound. Two distinct ordinal systems (`[c<base36>]` source citations + `[[N]]` tool-call ordinals) plus a model-text watcher with clear separation of concerns. Optional folder reorg only.                                  |
| P15 | ~~Worker streaming pipeline cleanup~~ — **RETRACTED** after code review     | [§5.1](../architecture/refactor-audit.md#51-worker-side-toolcallledger-duplicates-persistence-side-toolinvocationstoreport), [§5.2](../architecture/refactor-audit.md#52-approvalrecognisers-in-the-worker), [§5.3](../architecture/refactor-audit.md#53-streaming-pipeline--10-files-inside-the-worker) | [`12-worker-stream-cleanup.md`](12-worker-stream-cleanup.md) (retraction)     | —      | `approval_recognisers.py` is vendor-specific approval-card param projection (good code); `tool_call_ledger.py` is a transient in-flight cleanup tracker (good code); channel handlers are 400–900 LOC each (substantive).                   |
| P16 | Drop `agent_runs` row lock from `append_event`                              | [§4.3](../architecture/refactor-audit.md#43-per-event-db-amplification) (point 2)                                                                                                                                                                                                                        | [`13-per-run-sequence.md`](13-per-run-sequence.md)                            | Medium | Strict per-run monotonic `sequence_no`; `UNIQUE(run_id, sequence_no)` as source of truth; H3 never-rewind on `latest_sequence_no`; cancel-mid-stream race per [f4](../architecture/f4-cancel.puml). P2 + P4 already shipped behind toggles. |

**Phase exit criterion:** P14 and P15 retracted; per-run event append runs without the `agent_runs` row lock at p99 ≤ Phase 1 baseline.

---

## Phase 5 — Major library swaps + structural shifts

Each of these is its own initiative — plan as a quarter, not a sprint. Land one before starting the next; they each need a stabilization window.

| #   | PR                                                                | Audit ref                                                                                    | PRD                                 | Risk        | Behaviors preserved                                                                                                                                                                                                             |
| --- | ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------- | ----------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P17 | LangGraph Checkpointer replaces `CheckpointStorePort`             | [§3](../architecture/refactor-audit.md#3-library-replacements)                               | TBD: `14-langgraph-checkpointer.md` | Medium      | Per-run checkpoint reliability across worker restart; resume-from-checkpoint on approval flows                                                                                                                                  |
| P18 | `pg_partman` partitioning replaces retention sweep                | [§1.5](../architecture/refactor-audit.md#15-custom-retention-sweep--5-level-policy-resolver) | TBD: `15-pg-partman-retention.md`   | Medium–High | 5-level retention resolution hierarchy preserved (resolver stays); user-visible deletion semantics in observable time; PII scope retention still honored                                                                        |
| P19 | Repository pattern collapses 9 ports + 17 record types            | [§2.1](../architecture/refactor-audit.md#21-9-persistence-ports--17-record-types)            | TBD: `16-repository-collapse.md`    | High        | Every write idempotency invariant; field-level encryption; role-tagged `application_name` on the Postgres pool; all current Pydantic boundary contracts                                                                         |
| P20 | LiteLLM provider streaming (after verification)                   | [§3](../architecture/refactor-audit.md#3-library-replacements)                               | TBD: `17-litellm-providers.md`      | High        | Anthropic `thinking_mode` + `display`; OpenAI Responses API summary modes; Gemini grounding metadata; reasoning-token billing column; per-provider error → typed `RuntimeErrorCode` mapping                                     |
| P21 | LangGraph human-in-the-loop interrupts replace approval lifecycle | [§3](../architecture/refactor-audit.md#3-library-replacements)                               | TBD: `18-langgraph-interrupts.md`   | High        | `AWAITING_APPROVAL` run state; durable approval row across worker restart and SSE drop; resume via separate `APPROVAL_RESOLVED` command; multi-fire on token rotation; MCP auth flow per [f8](../architecture/f8-mcp-auth.puml) |

**Phase exit criterion:** LangGraph-native checkpointing + interrupts; partitioned retention at scale; persistence layer is repository-pattern with one ORM model per table. Provider stack uses LiteLLM where reasoning streaming is verified; bespoke adapters retained only for whatever LiteLLM cannot cover.

---

## Phase 6 — Coordinator split (do last)

| #   | PR                                    | Audit ref                                                                 | PRD                                    | Risk | Behaviors preserved                                                                                                                                           |
| --- | ------------------------------------- | ------------------------------------------------------------------------- | -------------------------------------- | ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P22 | Split `RuntimeApiService` (god class) | [§2.7](../architecture/refactor-audit.md#27-runtimeapiservice-at-24k-loc) | TBD: `19-runtime-api-service-split.md` | High | Every public method on `RuntimeApiService` (API + worker call surfaces); idempotency on retried commands; both API and worker calling the same coordinator(s) |

Why last: every prior phase shrinks one of the coordinator's dependents. Splitting before Phases 4–5 would mean rewiring the split twice.

---

## Dependency map

Read top-down. Arrows mean "must land first."

```
Phase 1 (P1, P2, P3, P4)
   │
   ├──► Phase 2 (P5, P6, P7, P8, P9)
   │       │
   │       ├──► Phase 3 (P10, P11, P12, P13)   [independent, may interleave with Phase 4]
   │       │
   │       └──► Phase 4 (P14, P15, P16)
   │               │
   │               └──► Phase 5 (P17, P18, P19, P20, P21)
   │                       │
   │                       └──► Phase 6 (P22)
   │
   └──► Phase 4 P16 also depends on Phase 1 P4 (sequence allocator built on append-consolidated path)
```

Specific cross-phase dependencies worth flagging:

- ~~**P4 → P16:** the `INSERT … RETURNING` pattern from P4 is the foundation for the per-run sequence allocator in P16.~~ — P4 (consolidated writes) is already shipped behind the `_consolidated_writes` toggle in [`PostgresRuntimeApiStore`](../../src/runtime_adapters/postgres/runtime_api_store.py). P16 is now independent.
- **P5 → P19:** the repository-pattern collapse assumes an async-only persistence surface.
- **P9 → P22:** consolidating Fork / Workspace services is a precondition to splitting `RuntimeApiService`, since the coordinator currently depends on the unconsolidated set.
- **P17 → P21:** durable approval state in P21 relies on LangGraph checkpointer behavior from P17.
- ~~**P14 + P15 → P22**~~ — retracted; no dependents.

---

## What each PRD must answer

(Per [`docs/CLAUDE.md`](../CLAUDE.md). Repeated here so every TBD slot is consistently scoped.)

1. **Problem.** What's currently in code, with file paths and LOC. Cite [`refactor-audit.md`](../architecture/refactor-audit.md).
2. **Goal + non-goals.** Be explicit about what is _not_ changing.
3. **Acceptance criteria.** Concrete, testable outcomes (file deletions, method signatures, p99 thresholds).
4. **Systems touched.** Inventory of files added / changed / deleted.
5. **Behaviors preserved.** Pull the relevant subset from [`refactor-audit.md` § Behaviors that must be preserved](../architecture/refactor-audit.md#behaviors-that-must-be-preserved). Each behavior gets at least one pinned test.
6. **Risks.** What could break, what could regress, what's hard to roll back.
7. **Unit testing requirements.** Which tests are added, which existing tests are tightened, which golden snapshots are diffed.
8. **Rollback plan.** Feature flag if applicable; otherwise the smallest revert that restores production behavior.

---

## Out-of-scope items (verify-first list)

Not yet PRDs. Do not implement until the underlying code question is answered.

| Question                                                                                      | Audit ref                                                                                        | Action                                                                                                                                                            |
| --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Does LiteLLM stream Anthropic thinking, OpenAI reasoning summary, Gemini grounding correctly? | [§3](../architecture/refactor-audit.md#3-library-replacements)                                   | Spike before opening P20 PRD. If LiteLLM lacks first-class support for any provider's reasoning surface, P20 becomes a partial replacement.                       |
| Does the frontend consume `presentation.summary` directly?                                    | [§1.1](../architecture/refactor-audit.md#11-presentationgenerator-polish-on-every-event)         | Grep frontend before P1 final review. If it does, P1 ships as "batch + only on terminal events" instead of "drop entirely."                                       |
| Is `RuntimeEventBus` truly process-local in production?                                       | [§4.1](../architecture/refactor-audit.md#41-sse-delivery-is-1s-in-production)                    | Confirm in code (`runtime_api/sse/event_bus.py`) before P2 PRD. Hypothesis is yes; verifying changes nothing about the fix but locks down the rollback condition. |
| Is multi-tool parallel execution enabled in `StreamingExecutor`?                              | [§4.7](../architecture/refactor-audit.md#47-multi-tool-parallel-execution-verify)                | Verify in code; if disabled, fold into P3 (parallel bootstrap is the same kind of asyncio change).                                                                |
| Does any signed buyer contract require in-app audit-chain integrity?                          | [§1.3](../architecture/refactor-audit.md#13-custom-hash-chained-audit-log)                       | Compliance / sales sign-off before P10 PRD ships. If yes, P10 becomes "managed audit service" not "delete chain."                                                 |
| Does `ToolBudgetGuard` get reused outside `ToolBudgetMiddleware`?                             | [§2.4](../architecture/refactor-audit.md#24-toolbudgetmiddleware--toolbudgetguard-two-step)      | Grep before P9. If reused, leave separate.                                                                                                                        |
| Is `dev_auth_bypass_allowed` actually stale?                                                  | [§5.7](../architecture/refactor-audit.md#57-dev_auth_bypass_allowed-toggle-on-deploymentprofile) | Grep + check root [`CLAUDE.md`](../../../../CLAUDE.md). If stale, fold into P6 cleanup wave.                                                                      |
| Frequency of `/v1/agent/conversations/{id}/context` from the frontend.                        | [§4.9](../architecture/refactor-audit.md#49-conversationcontextbuilder-per-context-query)        | If high-frequency, opens its own PRD slot in Phase 4 for a Redis / MV cache. Otherwise no PRD needed.                                                             |
| Are background jobs scheduled concurrently or serially in the worker?                         | [§4.12](../architecture/refactor-audit.md#412-background-jobs-in-the-worker)                     | Verify before P6 cleanup wave. If serial, add to P6 scope.                                                                                                        |

---

## Status checklist

Tick as PRDs are written and PRs ship. Update in the same PR that adds the PRD.

- [x] P1 — PRD drafted: [`01-presentation-polish-removal.md`](01-presentation-polish-removal.md)
- [x] P2 — PRD drafted + shipped: [`02-sse-listen-notify.md`](02-sse-listen-notify.md). EventBusBackend Protocol + InMemoryEventBus (renamed) + PostgresEventBus + LISTEN/NOTIFY adapter hook + lifespan wiring; default `RUNTIME_EVENT_BUS_BACKEND=in_memory` so the change ships dark. Inbox-bus refactor: Protocol + InMemoryInboxBus rename + backward-compat alias landed; persistent Postgres inbox backend + `LISTEN/NOTIFY runtime_inbox_v1` is the documented schema-bearing follow-up.
- [x] P3 — PRD drafted + shipped: [`03-parallel-bootstrap.md`](03-parallel-bootstrap.md). 3-resolver gather in `create_run` (11.a) + 4-registry gather in `acreate_agent_runtime` (11.b) were shipped pre-PRD; 5-way gather adding `_skill_cards` (11.c) shipped this round.
- [x] P4 — PRD drafted + shipped (both stages): [`04-event-write-consolidation.md`](04-event-write-consolidation.md). Stage 1 folds `INSERT runtime_events` + `UPDATE agent_runs.latest_sequence_no` into one transaction (default-on); Stage 2 adds `EventStorePort.append_events_batch` + `RuntimeEventProducer.append_api_events_batch` + `DeltaCoalescer` in the streaming executor (default `RUNTIME_DELTA_COALESCE_WINDOW_MS=0` — ships dark).
- [x] P5 — PRD drafted: [`01-async-only-ports.md`](01-async-only-ports.md)
- [x] P6 — PRD drafted: [`05-cleanup-wave.md`](05-cleanup-wave.md). All 6 legacy directories already deleted in prior work; the 3 other sub-items (`dev_auth_bypass_allowed`, `migrate.py`, `EncryptExistingColumns`) were withdrawn after pre-flight verification — see PRD §1.5.
- [x] P7 — PRD drafted + shipped (both PRs): [`06-citation-batching.md`](06-citation-batching.md). PR1 = infra (async `insert_many_or_get` port + adapters, `SOURCES_INGESTED` event type wired through schemas / api-types / FE reducers, `CitationLedger.register_many` + shared `_register_internal`, FE `citationReducer` + `sourcesReducer` branches, dual-store invariant test parametrized across both event shapes). PR2 = projector switch via `RUNTIME_BATCH_SOURCE_INGESTION` flag (default off; ships dark). 1031 BE tests + 762 FE tests pass; latent sync/async bug fixed as side effect. See PRD §11 for the divergences from the original plan.
- [x] P8 — PRD drafted: [`07-cluster-boundary-moves.md`](07-cluster-boundary-moves.md)
- [x] P9 — PRD drafted: [`08-service-consolidation.md`](08-service-consolidation.md)
- [x] P10 — PRD drafted + shipped: [`01-audit-chain.md`](01-audit-chain.md). Resolved 2026-05-10 — chain kept, deduped into shared `packages/audit-chain/`.
- [x] P11 — PRD drafted + shipped: [`01-redaction-subsystem.md`](01-redaction-subsystem.md). Direction pivoted 2026-05-11 from libraries-with-regex to structural redaction. All six sub-PRDs landed 2026-05-11: [P11.1](01a-redaction-protocol.md) (Protocol — later retired in P11.6), [P11.2](01b-redaction-exact-match-deny-keys.md) (exact-match `DENY_KEYS`; delete `SENSITIVE_VALUE` regex + `_TOKEN_COUNT_KEYS`), [P11.3](01c-redaction-field-tagging.md) (`Sensitive[]` annotation + `SafeLogDumper`), [P11.4](01d-redaction-pattern-consolidation.md) (single-source patterns + `PromptInjectionDetector`), [P11.5](01e-redaction-remove-from-non-log-paths.md) (strip `redact_json_object` from 14 non-log callsites; `JsonObjectCoercer`), [P11.6](01f-redaction-cleanup.md) (delete facade, `RegexRedactor`, `RedactorRegistry`).
- [x] P12 — PRD drafted: [`01-pricing-from-litellm.md`](01-pricing-from-litellm.md). Code-verified 2026-05-10. **Implementation pending.**
- [x] P13 — PRD drafted + shipped: [`01-otel-adoption.md`](01-otel-adoption.md). Rewritten 2026-05-10 after code-level verification — codebase already heavily on OTel; rescoped to coverage hardening. Landed 2026-05-11 as three small steps: (1) cross-process trace propagation via `QueueTracePropagator` (W3C `traceparent` on `RuntimeRunCommand` / `RuntimeCancelCommand` / `RuntimeApprovalResolvedCommand`; worker `_dispatch` extracts and re-parents; flag `RUNTIME_PROPAGATE_QUEUE_TRACE` default-on, fail-soft on missing / malformed carriers); (2) consolidated `_MetadataRedactor` from `logging.py` and `http_logging.py` into a single `MetadataRedactor` in `observability.redactor`; (3) pinned-set audit test for `SafeAttributeSpanProcessor` deny rules + LangSmith decision documented in `tracing.py` (kept; opt-in via `LANGSMITH_TRACING`).
- [x] P14 — **Retracted** ([`11-citations-consolidation.md`](11-citations-consolidation.md))
- [x] P15 — **Retracted** ([`12-worker-stream-cleanup.md`](12-worker-stream-cleanup.md))
- [x] P16 — PRD drafted: [`13-per-run-sequence.md`](13-per-run-sequence.md)
- [x] P17 — PRD drafted: [`14-langgraph-checkpointer.md`](14-langgraph-checkpointer.md). Pre-investigation; verification spike required (§2) before implementation. Hard blocker for P21.
- [x] P18 — PRD drafted: [`15-pg-partman-retention.md`](15-pg-partman-retention.md). Pre-investigation; multi-phase per-table conversion. Touches compliance — buyer sign-off may be required.
- [x] P19 — PRD drafted: [`16-repository-collapse.md`](16-repository-collapse.md). Pre-investigation; largest single restructure in the audit. Hard depends on P5.
- [x] P20 — PRD drafted: [`17-litellm-providers.md`](17-litellm-providers.md). Pre-investigation; blocking spike required (§2). Hybrid path likely — keep custom adapters where LiteLLM doesn't cover reasoning streaming.
- [x] P21 — PRD drafted: [`18-langgraph-interrupts.md`](18-langgraph-interrupts.md). Pre-investigation; verification spike required (§2). Hard depends on P17.
- [ ] P22 — PRD pending

---

## Naming convention for new PRD files

When you write the next PRD, name it `NN-short-slug.md` where `NN` matches its phase position. Suggested slugs are listed in the tables above (e.g. `02-sse-listen-notify.md`, `09-pricing-from-litellm.md`). The four already-drafted PRDs currently use `01-` prefixes; consider renaming to match phase order once the master plan is locked, or accept that the `01-` prefix means "first batch drafted" rather than "Phase 1." Either is fine — pick one and stay consistent.

---

_This roadmap reflects the audit as of May 2026. When a PR lands and changes assumptions for downstream PRs, update the corresponding row's "Behaviors preserved" or "Audit ref" rather than letting the roadmap drift._
