# ai-backend boundary audit — what is runtime, what is misplaced

Measures every `services/ai-backend` subpackage against the boundary the repo's own
[CLAUDE.md](../../../CLAUDE.md) already declares — _"Don't put tenant auth, billing, or
product persistence in `ai-backend`"_, and _"Tenants, IdP integration, permissions,
product persistence, admin workflows, and jobs are [`backend`'s] target home"_ — and
against the target architecture: **ai-backend should be a lean Deep Agents / LangGraph
runtime plus the adapters that map LangGraph output into our event format.**

6 parallel readers, source-verified, 2026-08-02. **Different lens from the earlier
audits:** those hunted _duplication_ and kept finding nothing. This one hunts
_misplacement_ — code that works fine but lives in the wrong service.

---

## Headline — and a correction to the hypothesis that prompted this

The hypothesis was _"~90% of ai-backend isn't the agent runtime"_ (from a naive reading
that counted only `execution/` + `context/` + `prompts/` + `delegation/` as runtime).

**That was wrong. ~80% genuinely IS runtime.**

| Verdict       |         LOC | Meaning                                                                          |
| ------------- | ----------: | -------------------------------------------------------------------------------- |
| **RUNTIME**   | **180,461** | genuinely the agent runtime — keep                                               |
| **SPLIT**     |      71,227 | a half moves (usually: authoring/storage/admin → `backend`; enforcement stays)   |
| **MISPLACED** |  **35,949** | product/tenant/billing/admin concern, target home is `backend`                   |
| **DEAD**      |   **7,342** | unreachable — see §4 (was 9,032; `context_contracts.py` is live). 3,908 deleted. |
| **SHARED**    |       3,771 | belongs in `packages/service-contracts`                                          |

Rolled up by the readers: **~209.9k runtime vs ~52.2k misplaced ≈ 20% misplaced.**

Why the hypothesis was so far off: `capabilities/` (77.9k) is **92% runtime** —
`concurrency/`, `discovery/`, `desktop/`, `operations/`, `workspace/`, `interpreter/`,
`browser/`, `middleware/` are all genuinely in-graph-loop. And `runtime_worker/` +
`runtime_api/` (54.4k) are **73% runtime** — including the 5.6k
LangGraph→`RuntimeEventEnvelope` stream adapter, which is _exactly_ the adapter layer
the target architecture wants to live here.

**This is the fourth headline overstatement this programme has corrected at source**
(orphans ~8,600→~160; compaction "add it"→already running; adapter collapse
~25–30k→~5–8k wrong-direction; and now this one). The pattern is unchanged: totals are
right, framing inflates.

## 1. The PDP/PEP rule — validated, and it settles the permissions question

**Permissions are NOT misplaced.** `tools/permissions.py` already fetches
`ToolUsePolicySnapshot.from_response` from `backend`'s `/internal/v1/policies/tool-use`
**once at run start** and enforces **in-process**. That is the correct split, and it is
already implemented:

|                                              | Home                         | Status                     |
| -------------------------------------------- | ---------------------------- | -------------------------- |
| Policy **authoring / storage / admin** (PDP) | `backend`                    | ✅ already there           |
| Policy **snapshot** at run start             | fetched once, cached per run | ✅ already the pattern     |
| Policy **enforcement** (PEP)                 | in-graph middleware          | ✅ correctly in ai-backend |

**The PEP cannot move.** The model picks tools mid-graph-loop; the facade sees one
"start a run" call and never sees the 17 tool calls. A per-tool-call HTTP hop would also
put a network round-trip on the hot path of every tool invocation.

Only the **vocabulary** should move: `ToolUsePolicyKind`/`Mode` and the
`read=auto / write=ask / destructive=require` default table self-describe as a _mirror_
of backend's four modes — that duplication belongs in `packages/service-contracts`.
`capabilities/policy/service.py` already takes its data by constructor injection, so the
backend-owned half is **a wiring change, not a code move**.

## 2. Hard misplacements — ranked (~35.9k)

|   LOC | What                                                      | Target              | Why it's misplaced                                                                                                |
| ----: | --------------------------------------------------------- | ------------------- | ----------------------------------------------------------------------------------------------------------------- |
| 7,848 | `harness_quality/`                                        | `backend` / tools   | fixture-only eval + promotion gating; **no graph, model or connector in its call graph** — not the runtime at all |
| 4,477 | `surfaces_v2` lifecycle_refs / snapshots / retention      | `backend`           | product data lifecycle                                                                                            |
| 3,825 | `api/legacy_*_migration` + `surfaces_v2/legacy_*`         | `backend` + ops job | one-shot migrations                                                                                               |
| 3,299 | `api/` share + notifications + membership + inbox + todos | `backend`           | product features                                                                                                  |
| 2,900 | store trio: pricing, cost, `usage_daily` rollups          | `backend`           | **billing — explicitly banned here by CLAUDE.md**                                                                 |
| 2,894 | `runtime_worker/jobs` (todos, routines, proposals)        | `backend`           | "admin workflows and jobs" are backend's target home; all unwired (Blocked on backend P3-A1/P5-A1)                |
| 2,286 | conformance sweeps (origin / llm_seam / e2_final)         | CI / tests          | merge gates, not runtime                                                                                          |
| 1,809 | `api/` workspace-admin + usage + legal_hold               | `backend`           | tenant admin                                                                                                      |
| 1,516 | `api/` model_catalog + tiers + enablement                 | `backend`           | product catalog                                                                                                   |
| 1,495 | share_store trio + export_import + todo stores            | `backend`           | product persistence                                                                                               |
| 1,248 | `runtime_api/http` retention, holds, audit, merge         | `backend`           | tenant/compliance admin CRUD                                                                                      |
| 1,215 | `persistence/` KMS column encryption + compliance records | `backend`           | compliance surface                                                                                                |
| 1,137 | `runtime_api/local_models/`                               | `backend`           | an Ollama install control plane                                                                                   |

**Two systemic smells** worth naming: ai-backend runs a **5th audit stream** that the
facade already merges with backend's 4, and `observability/logging + redactor` (1,006)
is a **third copy** of a contract `backend` owns.

## 3. The SPLIT set (~71.2k, only a fraction moves)

The pattern is consistent — **runtime keeps the in-loop half; backend takes
authoring / storage / scheduling / export**:

- `runtime_api_store` trio (15,219) — the god object. Event ledger + run lifecycle +
  outbox **stay** (8,200); retention / legal holds / approvals routing / org defaults /
  account-merge **move** (~7,000).
- `artifacts/` (8,790 adapters + 2,847 domain) — blob write/read + the run's artifact
  ledger stay; per-tenant GC leases, quarantine reaping, retention purge, org-deletion
  tombstoning move.
- `mcp/` (9,127) — connect/list/call stay; the descriptor **control plane** (freshness,
  revision feed/resolver/binding, ~2.4k) duplicates registry admin backend owns —
  **and the Lineara migration supersedes most of it anyway**.
- `sandbox/` (8,957) — execution stays; `cleanup_store` (teardown retry schedule),
  `usage_meter` (billing) and `session_store` move (~0.7k).
- `capabilities/surfaces/` (3,940) — the LLM-authoring half stays; `store` + `commit`
  (approval/idempotency ledger + audit, ~1.4k) move.
- `observability/usage_* + pricing/ + budgets/` (3,474), `release/` (1,000),
  `rollout*` (2,642), `runtime_worker/jobs` sweepers (4,019) — same shape.

**Never per-call HTTP.** Every move above must use the pattern that already works:
**snapshot-at-run-start** for policy data, **POST-the-facts** for telemetry/usage.

## 4. DEAD — 7,342 LOC (was reported as 9,032), and the scanner blind spot that hid it

> **Correction, applied when F5 was actually deleted:** the 9,032 figure counted
> `context/context_contracts.py` (1,690) as dead. It is not — `tool_result_admission.py`
> imports it and is itself reached from `tool_budget_guard` and `file_store_wiring`.
> The honest DEAD total for this section is **7,342**, of which the **3,908 F5 lines
> (`context/planning/` + `context/evidence_registry.py`) are now deleted**.

The single largest genuinely-deletable find of the whole programme, and **the orphan
ratchet could not see any of it**:

|   LOC | Module                                          | Note                                                                                                                                                   |
| ----: | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 2,298 | ~~`context/planning/`~~ **DELETED**             | the F5 context-planning lane…                                                                                                                          |
| 1,690 | `context/context_contracts.py` — **LIVE, KEPT** | **correction:** transitively live via `context/tool_result_admission.py`, which `tool_budget_guard` and `file_store_wiring` both reach. Not deletable. |
| 1,610 | ~~`context/evidence_registry.py`~~ **DELETED**  | …3,908 LOC deleted as one dead subsystem                                                                                                               |
| 1,522 | `capabilities/render_adapter_generator/`        | desktop's 6C consumer shipped; the producer was never wired                                                                                            |
| 1,499 | `delegation/subagents` unmounted half           | reachable only from unit tests                                                                                                                         |
|   413 | `context/tool_result_admission_gate.py`         | already a known pending wiring                                                                                                                         |

**Scanner bug (mine, from T0.2/PR1):** [`orphans.py`](../../../tools/ai-backend-smells/orphans.py)
puts `__init__` in `ENTRY_HINTS`, so **a package reachable only via its own `__init__`
is invisible to the scan** — which is exactly how 9k of dead code hid from a gate built
to catch dead code. Two independent readers found this. Fixing it is the highest-value
follow-up in this document, and it will grow the ratchet baseline substantially before
it shrinks it.

## 5. What this means

1. **The service is not 90% misplaced. It is ~20% misplaced and ~4% dead.** The
   re-architecture is real but far smaller than the framing suggested — and, unlike the
   three prior "big wins," this one is _actionable_ because it names concrete targets.
2. **The event-mapping adapter layer already exists and is correctly placed**
   (`runtime_worker/stream_*`, `capabilities/middleware/`, `operations/presentation_boundary`).
   The target architecture is closer to reality than expected.
3. **The highest-value work is not a migration — it is the 9k of dead code plus the
   scanner fix that would have caught it.** That is a genuine reduction; the moves are
   a boundary-hygiene programme to sequence against Lineara's MCP work.
4. **Sequence the moves after Lineara.** The `mcp/` control-plane split and the
   `tools/permissions` vocabulary move both land inside the surface that migration is
   already rewriting.

_Method: 6 readers, each scoped to a bounded subpackage set, classifying at subpackage
(not file) level with LOC from `wc -l`; verdicts required source evidence, not filename
matching. Cross-checked against [PENDING-WIRINGS.md](PENDING-WIRINGS.md) and
[ADAPTER-COLLAPSE-REALITY.md](ADAPTER-COLLAPSE-REALITY.md)._
