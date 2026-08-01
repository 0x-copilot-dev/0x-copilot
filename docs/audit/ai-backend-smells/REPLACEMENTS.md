# What else can be replaced — library inventory

Companion to [DELETE-REPLACE.md](DELETE-REPLACE.md) and the
[consolidation plan](../../plan/ai-backend-consolidation/PLAN.md).

Method: read `requirements.txt`, count import sites for each dependency, then compare what a
dependency provides against what we wrote. **Confidence is stated per row** — some of these
are verified substitutions, some are hypotheses that need a spike.

---

## 0. The headline: we ship dependencies we do not use

| Dependency                      | Version | Import sites in `src`                              | Status                                                              |
| ------------------------------- | ------- | -------------------------------------------------- | ------------------------------------------------------------------- |
| `tenacity`                      | 9.1.4   | **0**                                              | **dead** — while retry/backoff is hand-rolled in `model_invocation` |
| `langsmith`                     | 0.10.5  | 1 (a lazy sandbox provider + optional `traceable`) | near-unused                                                         |
| `langgraph-checkpoint-sqlite`   | 3.1.0   | 1                                                  | desktop only                                                        |
| `langgraph-checkpoint-postgres` | —       | —                                                  | **not installed**                                                   |
| `langchain-mcp-adapters`        | —       | —                                                  | **not installed**                                                   |
| `mcp` (official SDK)            | —       | —                                                  | **not installed**                                                   |
| SQLAlchemy / SQLModel           | —       | —                                                  | **not installed** — 16,576 LOC of hand-written psycopg SQL          |

## 1. Production has no durable graph state

`runtime_checkpointer()`
([deep_agent_builder.py:378](../../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py:378))
returns an `AsyncSqliteSaver` **only** on the desktop file-store path. Its own docstring:

> Every other deployment (postgres, in-memory, web) keeps the process-local `InMemorySaver`.

So on the server, LangGraph checkpoints live in process memory. A worker restart mid-run loses
graph state, and the durability that replaces it is our own — run records, the event store with
monotonic `sequence_no`, the outbox, the approval resume path — a meaningful share of
`runtime_worker`'s 33,104 LOC.

`langgraph-checkpoint-postgres` ships `AsyncPostgresSaver`, which persists checkpoints to
Postgres and **runs its own migrations via `setup()`**. It is not installed.

**Confidence: high that the gap is real; medium that closing it removes much code.** Our event
store also serves SSE resume, audit and client projections, which a checkpointer does not do.
The overlap is _durability and resume_, not the whole store. **A spike should answer how much
of the worker's restart-recovery logic `AsyncPostgresSaver` subsumes** before anything is
deleted — and either way, installing it fixes a production durability gap on its own merits.

## 2. Retry and backoff — a dead dependency and a hand-rolled replacement

`tenacity` is pinned at 9.1.4 with **zero import sites**. Meanwhile
`execution/model_invocation/` implements attempt budgets (`max_attempts`, ceiling 3),
per-deployment attempt limits, a failure-class taxonomy, and a process-local circuit breaker
(`open_failure_threshold=3` in a 120s window, 30s cooldown).

**Not a straight swap.** `tenacity` gives retry/backoff/jitter; it does not give
route-plan failover across deployments, credential-mode selection, or a shared circuit. The
honest read is that **the retry mechanics inside our policy could be tenacity**, and the
routing policy stays ours.

**Confidence: medium.** Minimum action regardless: either use it or drop it from
`requirements.txt` — a pinned unused dependency is supply-chain surface for nothing.

## 3. Evaluation and tracing — `harness_quality` vs LangSmith

`harness_quality` is **7,848 LOC**: fixture-replay evaluation, scorers, promotion cohorts,
signed harness manifests, `PromotionThresholds`/`PromotionDecision`.

`langsmith` is installed and used for **one thing** — a lazy sandbox provider — plus an
optional `traceable` decorator in `observability/tracing.py`. LangChain's own published harness
work ([RESEARCH.md](../../plan/mcpmark-optimization/RESEARCH.md) §2) leaned on **LangSmith
tracing at scale to identify failure modes**, which is precisely the job
[EXPERIMENTS.md](../../plan/mcpmark-optimization/EXPERIMENTS.md) proposes doing by hand.

**Confidence: low-medium, and deliberately so.** The hermetic replay guarantee ("no ambient
HTTP client, connector, MCP client, or effect executor in the call graph"), the ed25519-signed
manifests and the promotion gates are plausibly product requirements LangSmith will not meet.
**The overlap worth testing is the trace-analysis half, not the promotion half.** Do not delete
`harness_quality`; do ask why the benchmark programme is planning bespoke instrumentation while
a tracing product sits installed and unused.

## 4. Observability — 13,376 LOC over a full OTel stack

Ten `opentelemetry-*` packages are installed with 33 import sites, so OTel is genuinely used.
The custom mass is elsewhere:

| LOC   | Module                       | Note                                                |
| ----- | ---------------------------- | --------------------------------------------------- |
| 1,566 | `context_occupancy_recorder` | **keep** — the instrument the cost model depends on |
| 1,415 | `context_message_classifier` |                                                     |
| 1,271 | `context_origin_conformance` | **orphan** — see [FINDINGS.md](FINDINGS.md) §1      |
| 798   | `lifecycle_metrics`          |                                                     |
| 741   | `model_invocation_metrics`   |                                                     |

**Confidence: low.** Context occupancy attribution is genuinely ours and genuinely valuable —
it is what makes the PRD's cost model measurable. This block is not a replacement candidate so
much as a candidate for _deleting the orphan_ and leaving the rest alone.

## 5. Persistence — no ORM at all

16,576 LOC of Postgres adapter written directly against `psycopg`, with `yoyo-migrations` for
schema. No SQLAlchemy, no SQLModel. Combined with the file (18,100) and in-memory (8,654)
adapters, that is 47,063 LOC implementing one 116-method port three times.

**Confidence: high on the diagnosis, medium on the remedy.** An ORM plus a single SQL
implementation with Postgres and SQLite dialects is the standard answer and would collapse
three codebases into one — but this is a persistence rewrite against the desktop's default
store, so it is Phase 3 of the plan for a reason.

## 6. Blocks I looked at and am _not_ calling replaceable

Stated so this reads as an inventory rather than a demolition list.

- **`capabilities/concurrency` (10,164)** — batch coordination, graph admission, permits, kill
  switches. Generic libraries (`aiolimiter`, `anyio` semaphores) cover primitives, not the
  run-scoped admission model. **No credible single replacement found.**
- **`capabilities/sandbox` (8,957)** — already delegates to a pinned provider
  (`deepagents.backends.langsmith`). The custom mass is the provider registry, snapshotting and
  workspace transfer. Hosted alternatives exist (E2B, Modal, Daytona) but this is a provider
  swap, not a deletion.
- **`capabilities/desktop` (6,472)** — host paths, workspace grants, broker client. Product
  surface with no framework equivalent.
- **`surfaces_v2` (18,336)** — generative UI. Product.

## 7. Ranked additions to the plan

| #   | Action                                                                 | LOC    | Confidence | Why now                                                              |
| --- | ---------------------------------------------------------------------- | ------ | ---------- | -------------------------------------------------------------------- |
| 1   | Drop or adopt `tenacity`                                               | 0      | High       | unused pinned dependency, one line either way                        |
| 2   | Install `langgraph-checkpoint-postgres`, use `AsyncPostgresSaver`      | —      | High       | **closes a production durability gap** regardless of what it deletes |
| 3   | Spike: how much worker restart-recovery does the checkpointer subsume? | ?      | Med        | sizes a slice of the 33k worker                                      |
| 4   | Spike: LangSmith tracing for the benchmark's failure-mode analysis     | ?      | Med        | may cancel bespoke instrumentation in EXPERIMENTS.md                 |
| 5   | Delete `context_origin_conformance` (orphan)                           | −1,271 | High       | already in Phase 1                                                   |

Items 1 and 2 are near-free and independent of everything else. **Item 2 is the one I would do
first even if no code were ever deleted**, because the current state is that production graph
state is not durable and the docstring says so plainly.

## Limits

- Import-site counts are textual; a dependency used only through a lazy `import_module` string
  would undercount. `langsmith` was checked by hand for this reason.
- "Replaceable" here means _a dependency provides the capability_, not _the substitution
  preserves our semantics_. Every row above §5 needs the G1 question answered — what does ours
  carry that theirs cannot express — before code moves.
- I have not spiked any of these. The confidence column is about the strength of the evidence
  for the gap, not about the migration being easy.
