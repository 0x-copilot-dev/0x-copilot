# ai-backend — what to delete, what to replace, what to keep

Companion to [FINDINGS.md](FINDINGS.md). That document asked "what never runs". This one asks
**"what should not exist"** — code we wrote that a dependency already provides, code written
three times, and code written once and never reached.

Every number below was measured, not estimated. Nothing here is a recommendation to delete
without the verification step named against it.

---

## 0. The size problem, stated

|                             | LOC         |
| --------------------------- | ----------- |
| `services/ai-backend/src`   | **299,144** |
| `services/ai-backend/tests` | **256,069** |
| **total**                   | **555,213** |

By subpackage (src):

| LOC    | Package             |
| ------ | ------------------- |
| 76,191 | `capabilities`      |
| 47,063 | `runtime_adapters`  |
| 33,104 | `runtime_worker`    |
| 28,480 | `agent_runtime/api` |
| 21,108 | `runtime_api`       |
| 18,336 | `surfaces_v2`       |
| 13,685 | `execution`         |
| 13,376 | `observability`     |

For scale: this wraps a framework (`deepagents` + LangGraph) that already supplies the agent
loop, the filesystem tools, subagents, summarisation, permissions and skills. **The wrapper is
an order of magnitude larger than what it wraps.**

That alone is not proof of waste — tenant isolation, MCP, audit and approvals are real product
concerns the framework does not cover. The categories below separate the two.

## A. Replace with the framework (highest value, lowest risk)

We import `deepagents.backends.*`, `middleware.filesystem`, `middleware.subagents` and
`_fs_interrupt`. We do **not** import `summarization`, `permissions`, `memory`, `skills`,
`rubric`, `_message_eviction`, `_overflow_clip`, or `patch_tool_calls` — several of which we
have parallel implementations of, and two of which the MCPMark PRD had scheduled as **new
work**.

> **Correction — the `tool_result_admission_gate` half of this section is wrong.** Reading the
> module (rather than matching its name against `_offload_tool_message_content`) shows it solves
> a harder problem: it hooks `RuntimeControlMiddleware`'s result sweep, so coverage is a property
> of the graph's topology and reaches Deep Agents' own injected tools and a subagent's private
> copies — which a `BaseTool` decorator cannot. It is constructible with no arguments so a
> missing durable store cannot silently disable bounding, and `verify_model_visible` fails
> closed. **Wire it; do not delete it.** See
> [TASKS.md](../../plan/ai-backend-consolidation/TASKS.md) §T1.3. The `SummarizationMiddleware`
> half of this section stands.

### A1 — Context compaction: already shipped by the framework, scheduled as new work by us

`deepagents/middleware/summarization.py` provides `SummarizationMiddleware` with:

- token-or-fraction triggers (`trigger=("fraction", 0.85)`, `keep=("fraction", 0.10)`),
- `TruncateArgsSettings` — a cheaper intermediate that strips only `tool_calls` args before
  the keep window,
- a `CompactConversationSchema` tool so the agent or a human-in-the-loop flow can compact on
  demand.

`_overflow_clip.py` adds tail-`ToolMessage` clipping with a **`read_file`-aware slice**, and
`_message_eviction.py` provides head+tail preview eviction plus `_offload_tool_message_content`.

**Consequences:**

- **PRD P2-1 ("compact tool results") is a configuration task, not a build.** [RESEARCH.md](../../plan/mcpmark-optimization/RESEARCH.md) §4 rates it +5–15pp accuracy and −40–60% cost — the highest-value item in the programme — and the mechanism already exists behind a trigger we have never set.
- **PRD P2-2 ("result field projection") is `_overflow_clip`.**
- **`context/tool_result_admission_gate` (413 LOC, unwired, 22 test references) duplicates `_offload_tool_message_content`.** We wrote an offload writer, unit-tested it, never wired it, while the framework's equivalent sat unused in the same process.

**Action:** delete `tool_result_admission_gate`, configure `SummarizationMiddleware`.
**Verify first:** that the framework's keep-window semantics preserve identifiers created
earlier in a run (the PRD's stated compaction risk, and HARBOR's actual failure).
**Saving:** ~413 LOC deleted, ~2 PRD items cancelled.

### A2 — Candidates that need a semantic check before deletion

These have framework counterparts, but ours plausibly carry product semantics theirs does not
(tenant scoping, audit, MCP). **Do not delete on the strength of a filename match.**

| Ours                               | LOC   | Framework counterpart       | The question to answer                                                                                         |
| ---------------------------------- | ----- | --------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `capabilities/skills`              | 1,361 | `middleware/skills.py`      | do we need org-scoped skill bundles theirs cannot express?                                                     |
| `context/memory`                   | 1,990 | `middleware/memory.py`      | ours adds path policy + prompt-injection rejection — **which [FINDINGS.md](FINDINGS.md) §2a shows never runs** |
| `capabilities/tools` (permissions) | 4,215 | `middleware/permissions.py` | ours adds the read/write/destructive axes and MCP scope; theirs may cover the tool-gating half                 |
| `delegation/subagents`             | 3,134 | `middleware/subagents.py`   | we already import theirs — so what is the extra 3.1k doing?                                                    |

`context/memory` is the sharpest of these: the part that justifies a custom implementation
over the framework's is exactly the part that is unreachable. Either wire it or drop to the
framework's — the current state is the cost of both with the benefit of neither.

**Saving if all four collapse:** up to ~10,700 LOC. Realistically less; treat as a
per-module investigation, not a sweep.

## B. Delete outright — unreachable code

Ten modules, **~4,700 LOC**, unit-tested, no `__main__`, imported by nothing in `src`
([FINDINGS.md](FINDINGS.md) §1). Re-verified after merging `origin/dev`.

`context_origin_conformance` (1,271) · `patch_plan` (804) · `proposal_extractor` (620) ·
`e2_final_conformance` (524) · `provider_hints` (332) · `inbox_fallback` (316) ·
`approval_expiry_sweeper` (245) · `code_tool_adapter` (238) · `tool_result_admission_gate`
(413, see A1) · `encrypt_existing_columns` (216)

**Two are not deletions but wirings.** `approval_expiry_sweeper` is the only thing that
expires stale approvals, and `tool_result_admission_gate` is A1's capability. Deleting a
module whose _absence_ is the bug converts a silent gap into a permanent one.

**Plus their tests.** At this repo's ~0.86 test-to-source ratio, ~4,700 LOC of dead source
implies roughly the same again in tests that pass over code that never runs.

**Saving:** ~4,300 LOC source (excluding the two to wire) + comparable test LOC.

## C. Collapse the adapter triplication — the largest single win

`runtime_adapters` is **47,063 LOC** across three hand-written implementations of one
contract:

| LOC    | Adapter     |
| ------ | ----------- |
| 18,100 | `file`      |
| 16,576 | `postgres`  |
| 8,654  | `in_memory` |

`agent_runtime/api/ports.py` declares **116 async methods**. One file, `runtime_api_store.py`,
exists three times — **7,698 + 4,365 + 3,305 = 15,368 LOC** — and every new port method must
be written, tested and kept consistent three ways.

**This has already caused a production outage.** The in-memory adapter builds its model
field-by-field while Postgres splats `SELECT *` into an `extra="forbid"` contract, so a
`ValidationError` surfaced as an HTTP 400 that read like an upstream rejection — with the
full unit suite green, because the tests ran against the adapter that could not exhibit the
bug. Triplication does not merely cost LOC; **it systematically hides the failure in the one
implementation that ships.**

**Recommendation: one SQL implementation, two deployments.** Postgres for the server,
**SQLite for desktop and tests**. That preserves all three deployment targets — the file
adapter exists because desktop needs local storage, and in-memory because tests need speed —
while collapsing three divergent codebases into one, and makes the test suite exercise the
same code path production runs.

**Saving:** plausibly ~25–30k LOC, and the elimination of a whole bug class.
**Risk:** high — this is a persistence rewrite, and the file adapter is the desktop default.
Stage it behind the existing port so both can run during migration.

## D. Keep — this is not all waste

Stated explicitly so the audit is not read as "delete ai-backend":

- ~~**MCP** (`capabilities/mcp`) — the framework has no equivalent~~ **WRONG, see
  [the consolidation plan](../../plan/ai-backend-consolidation/PLAN.md) §0.** MCP is 11% of
  `capabilities/`, not most of it; `capabilities/mcp` is a client for our own
  `/internal/v1` proxy rather than an MCP client; and `langchain-mcp-adapters` +
  the official `mcp` SDK — neither installed — provide what both services hand-rolled.
  What survives as genuinely ours: the **registry, OAuth, token vault and scope model**.
- **Tenant isolation, audit chain, retention** — compliance surface, not framework territory.
- **`runtime_api` / `runtime_worker`** — the durable queue, event sequencing and SSE resume
  contract are ours by design and are the product's differentiator.
- **`observability/context_occupancy`** — the instrument that makes the cost model
  measurable at all.

## Ranked worklist

| #   | Action                                                                   | LOC      | Risk     | Blocks                            |
| --- | ------------------------------------------------------------------------ | -------- | -------- | --------------------------------- |
| 1   | Configure `SummarizationMiddleware`; delete `tool_result_admission_gate` | −413     | Low      | cancels PRD P2-1 + P2-2 as builds |
| 2   | Delete the 8 genuinely-dead orphan modules + tests                       | ~−8,600  | Low      | —                                 |
| 3   | Wire `approval_expiry_sweeper` (do not delete)                           | +0       | Low      | —                                 |
| 4   | Decide `context/memory`: wire the policy or drop to framework memory     | ~−2,000  | Med      | [FINDINGS.md](FINDINGS.md) §2a    |
| 5   | Investigate skills / permissions / subagents overlap                     | ~−8,700  | Med      | needs semantic check              |
| 6   | Collapse 3 store adapters to 1 SQL + 2 dialects                          | ~−25,000 | **High** | persistence rewrite               |

**Order matters.** 1–3 are near-free and should land before any benchmark sweep, because they
change what the harness does. 6 is the biggest number and the only one that needs its own
programme.

## Method and limits

- Sizes from `wc -l`; port method count from `grep -c "async def"` on `api/ports.py`;
  orphans from [`tools/ai-backend-smells/orphans.py`](../../../tools/ai-backend-smells/).
- Framework capabilities read from the **installed package**, not from documentation.
- **Filename overlap is a hypothesis, not a finding.** Section A2 is explicitly a list of
  questions. The one place this audit asserts duplication (A1) was verified by reading both
  implementations.
- The scan cannot tell whether a framework counterpart preserves our semantics. Every A-row
  needs that check before code is removed.
