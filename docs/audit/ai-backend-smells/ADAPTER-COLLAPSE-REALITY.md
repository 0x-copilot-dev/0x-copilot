# Adapter collapse — the reality, and why the specced approach is wrong

**Supersedes [DELETE-REPLACE.md](DELETE-REPLACE.md) §C.** That section proposed
collapsing `runtime_adapters` (47,063 LOC) into "one SQL implementation, SQLite for
desktop+tests, Postgres for server," projecting a **~25–30k LOC** saving. Read against
source (Phase 3 investigation, 2026-08-02), that recommendation **harvests the wrong
duplication, points the opposite direction from where the code actually overlaps, and
sacrifices a deliberate local-first design.** The honest collapsible duplication is
**~5–8k LOC**, and the right way to harvest it is _not_ a SQL rewrite.

This is the third time this program's audit has overstated an opportunity — orphans
(~8,600 → ~160, [PENDING-WIRINGS.md](PENDING-WIRINGS.md)), compaction ("add it" → it
was already running), and now the adapter collapse. The pattern is consistent: the raw
LOC totals are right; the _framing_ ("N hand-written impls of one contract") is what
inflates the opportunity.

---

## 1. The 47k is real; "three impls of one 116-method port" is not

The totals check out (measured 47,018 LOC: `file` 18,100 · `postgres` 16,576 ·
`in_memory` 8,609). But the framing is wrong three ways:

- **It's a _family_ of ports, not one.** `factory.py` wires `api/ports.py` **plus**
  `artifacts/`, `harness_quality/`, `control_plane/`, `persistence/`, and
  `model_invocation` ports. The "116 methods" (actually **115**, of which **93** are in
  the composite `PersistencePort`) are implemented only by the **15,368-LOC
  `runtime_api_store` trio** — not the whole 47k.
- **Most stores are not three-way.** Of **35** distinct store concepts, only **15**
  exist in all three backends. **12 are single-backend** (10 desktop-only local-first
  stores — `agent_state`, `object_store`, `repair`, `export_import`, …; 2 server-only —
  `account_merge` re-key 1,611, `artifact_store` 1,308) and **8 are two-backend**
  (`evaluation_repository` is desktop/test-only; `account_merge` is server+test-only —
  asymmetric **by design**). ~**16k** is one/two-backend features + local-first
  machinery that consolidation **cannot delete**; **3,733** is already backend-agnostic
  shared code (`base.py`, `factory.py`, `artifact_lifecycle`, `_event_idempotency`).

## 2. The real duplication is a PAIR, and it points the other way

The genuine overlap is `file` ↔ `in_memory`, **not** a triple:

| Pair                 | Shared distinct lines                     | What differs                                                                                                                                                                                                                   |
| -------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `file` ↔ `in_memory` | **~73%** (136 shared method names)        | `file` = `in_memory` + a JSONL persistence sidecar. `create_conversation`/`get_conversation`/`append_event` use the same in-memory structures + `len(events)+1` sequencing; `file` only adds an `asyncio` lock + `_persist_*`. |
| `postgres` ↔ either  | **~22–34%** (mostly signatures/constants) | Genuinely different SQL: optimistic `UniqueViolation` retry loops, `FOR UPDATE` run fences, `SELECT MAX(seq)+1`, payload encryption, `LISTEN/NOTIFY`.                                                                          |

So the natural collapse is **"factor the shared in-memory-view + domain-policy that
`file` and `in_memory` both carry into a base both extend"** — **~5–8k LOC**. A
"one SQL impl" does the opposite: it **discards** the 73%-shared `file`/`in_memory`
code and standardizes on the **22%-shared** Postgres SQL.

## 3. Why "canonical SQLite on desktop" is a product decision, not a cleanup

The desktop file store is **deliberately JSONL-canonical**:
`file/_catalog_index.py` is a _"disposable SQLite catalog index over the canonical JSONL
session folders — every row is derivable from the JSONL; losing the index never loses
data"_ (WAL + `synchronous=NORMAL` chosen precisely because JSONL is the real
durability). Making SQLite **canonical** sacrifices:

- append-only durability + line-level salvage/repair (`file/repair.py`, 741 LOC of
  "corruption diagnosis, salvage-export"),
- human-inspectable per-session folders,
- signed tamper-evident manifests (`_audit_manifest`, 199 LOC),
- per-conversation export archives (`export_import.py`, 570 LOC).

**~6.3k of the `file` adapter must be _rewritten_ for SQLite, not deleted.** And
`in_memory → SQLite-in-memory` is not free: `in_memory` is the fast, transparent double
for the thousands-strong unit suite; a SQL serialization boundary + connection/shared-
cache management taxes every test. Concurrency/streaming genuinely differ per backend
and **stay forked inside any "one" impl** (a dialect-parameterized store still
special-cases the retry loop and `LISTEN/NOTIFY`).

The audit's strongest argument — "triplication hid the outage in the shipping impl" —
is **now stale**: all 16 Postgres hydrators are field-by-field with `row.get()`
migration-compat; the blind `Model(**row)` splat that caused the outage is gone. The
consolidation-for-correctness case is real in principle but no longer backed by a live
bug.

## 4. Recommendation

1. **Do NOT execute the specced SQL collapse.** It is a high-risk persistence rewrite
   that harvests the wrong duplication, reverses the local-first design, and saves far
   less than claimed.
2. **Take the low-risk win: a shared in-memory-view base for `file` + `in_memory`
   (~5–8k LOC).** `file` becomes `in_memory` + a JSONL sidecar mixin. This harvests the
   _actual_ duplication, keeps the local-first design intact, and shrinks the
   maintain-two-ways divergence surface (the outage _class_) without touching Postgres.
3. **Treat "canonical SQLite on desktop" as a separate strategic product decision** —
   evaluated on local-first tradeoffs, not filed as a dedup cleanup.
4. **First slice (if pursuing the base-extraction):** `ContextOccupancyStorePort`
   (`api/ports.py:126`) — 2 methods (`append`/`list_context_occupancy`), append+read
   only, idempotent, **off the critical path** (the port docstring: "measurement must
   never take a run down; treat every failure here as a dropped snapshot"). It has a
   dedicated cross-backend test + migration, so it is the lowest-blast-radius domain to
   prove the base-extraction pattern, parity-verified against the existing three tests.

**Status:** recommendation (2) is now under way. The base
(`MaterializedViewStoreBase`) exists and the first two slices —
`ContextOccupancyStorePort` and `UsageAttributionEdgeStorePort` — are extracted
and parity-verified. See [ADAPTER-BASE-EXTRACTION.md](ADAPTER-BASE-EXTRACTION.md)
for the implementation record and the tiered ledger of the remaining method
groups.

## 5. Why this was not implemented autonomously

The `file`↔`in_memory` base extraction is the right win, but it is a **~5–8k-LOC
persistence refactor**. Under an unattended "merge if green" directive, a green unit
suite does **not** fully cover persistence-layer subtleties (crash-consistency,
sequencing, lock semantics), and the multi-backend integration/live-desktop e2e that
_would_ cover them is not reliably runnable here. The refactor is also best designed
holistically (one base pattern) rather than merged slice-by-slice unattended. So the
responsible output is this corrected analysis + plan; the implementation is filed as a
reviewed follow-up. See [TASKS.md](../../plan/ai-backend-consolidation/TASKS.md) Stage 5.

_Method: read `runtime_adapters/{file,postgres,in_memory}/runtime_api_store.py`
method-by-method, `api/ports.py`, and `factory.py`; quantified shared vs backend-specific
distinct lines. Every figure is from source, not the audit's prose._
