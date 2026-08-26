# Harness backlog — what is still open, ranked

**Verified against:** `f05ae4fc` (= `origin/dev` = `origin/main`), 2026-08-26.
Every "still open" below was re-checked by grep against that tree rather than
carried over from the audit — four items the audit listed are now closed and are
recorded as such in [§6](#6-closed-since-the-audit--do-not-re-raise).

**Derived from:** [OPENCODE-HERMES-COMPARISON.md](OPENCODE-HERMES-COMPARISON.md)
(the three-way audit, 49 gaps → 6 root causes → a Top 8) and
[tools/harness-bench/FINDINGS.md](../../../tools/harness-bench/FINDINGS.md)
(what the measurements say, §1–§7).

> **A note on how to read the effort column.** "hours" means the change itself is
> small — it does not include measuring whether it worked. Every cost item below
> should be re-measured with `cache_profile.py` (free) or a bench arm (~$1).

---

## The state in one paragraph

The cost work is done and measured: cold prompt **23,181 → 20,547 (−11.4%)**,
four-task total **−12.5%**, completion **3/4 → 4/4** — the completion win being
one knob (`recursion_limit`, inherited at LangGraph's default of 25). §7 then
settled the question §4 left open: **a run that opens a session is cold about two
times in three, and no amount of elapsed time changes that** — the driver is the
process boundary, not the cache clock, so every token cut from the resident
prefix is paid back on the first run of every session. What is _not_ done is the
instrument underneath it. The per-model-call occupancy ledger carries
`cached_input_tokens: 0` and `provider_input_tokens: null` in **all 820 records
across 98 stores**, and the composer's context meter (#625/#626) is presenting
those cache-blind numbers to users — which the record's own docstring says leads
a reader to "recommend trimming the stable prefix, which is exactly backwards".

---

## 1. Ranked

| #      | Item                                                  | Effort | Why it is where it is                                                                            |
| ------ | ----------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------ |
| **1**  | ~~Anthropic cache accounting~~ ✅ landed              | hours  | was two defects: writes invisible (latent) **and** reads counted twice (live, 71.7% over-report) |
| **2**  | Populate the occupancy ledger's provider/cache fields | days   | 820/820 records empty; a shipped user-facing meter is cache-blind because of it                  |
| **3**  | Lossless JSON-schema slimming (drop `title`)          | hours  | the named next lever; ~15–20% of every args schema, and it reaches third-party tools             |
| **4**  | Decide `artifact_family` exposure                     | —      | a decision, not code: 1,705 resident tokens on every cold run, parked pending an owner           |
| **5**  | Run the heavy arms                                    | ~$2    | 830 LOC of validated harness never driven; 5 claims still unmeasured                             |
| **6**  | `expires_at` + sweeper default                        | hours  | Top-8 #5, re-verified open; half-built machinery a compliance reviewer reads as implemented      |
| **7**  | A correctness axis on the bench                       | days   | today a trimming change can degrade answers and still report 4/4                                 |
| **8**  | Wire FTS5 conversation search                         | days   | Top-8 #6, re-verified open; we pay to maintain the index on every write                          |
| **9**  | Desktop skill authoring                               | days   | Top-8 #8, re-verified open; on the surface CLAUDE.md calls the product                           |
| **10** | Close the dark-wiring zero-default blind spot         | days   | a field defaulting to `0` and never produced reads as a measurement, not as absent               |
| **11** | Grow the interactive corpus                           | —      | 13 runs on one machine is not a sample; it is the denominator for all cost work                  |

---

## 2. Cost and caching — the thread just measured

### 1. Anthropic cache accounting — ✅ **LANDED on this branch**

Opened as "restore the cache-**write** read symmetry" and turned out to be **two**
defects in the same function, one of them live. They could not be fixed apart:
adding the missing write lookup to the old arithmetic would have added it on top
of an already-gross figure and made the live one worse.

**1a — writes are invisible (latent).** `cache_read` gets a second lookup inside
`input_token_details`; `cache_creation` got none, while LangChain 1.5.3's
`InputTokenDetails` exposes exactly `audio` / `cache_creation` / `cache_read`.
6,333,964 recorded cache-read tokens against **zero** writes, which is impossible.
Whether it was costing tokens is still **not** established — a dropped write and
an absent write produce an identical record.

**1b — reads are counted twice (live).** `_UsageBlocks` yields two wire shapes
that disagree about `input_tokens`: provider-raw excludes the caches, LangChain's
`usage_metadata` is "the sum of all input token types" with the details as
**subsets**. `gross = input + creation + read` was applied to both, so every
LangChain-shaped Anthropic call was inflated by its own cache read. Anthropic's
warm "fresh" portion (`input − cached`) measured **21,091 tokens — the whole
prompt again** — against OpenAI's 346 on the same metric.

**Impact:** anthropic input over-reported by **71.7%** across 442 runs (7,972,019
recorded vs 4,642,605 corrected). Cost identity collapses to
`true_gross·p_in + cached·p_cached`, i.e. **cached tokens billed at the full input
rate as well as the cached rate.** Any absolute anthropic token or cost figure
read out of the run store before this fix is inflated; §4's `cached / input` ratio
is unaffected, since both terms move together.

**Landed:** shape decided before the arithmetic, five tests including a regression
that pins the old assertion as wrong. Written up in
[FINDINGS.md §7.1](../../../tools/harness-bench/FINDINGS.md), including the method
failure — the anomaly was in the first table §7 printed and was explained rather
than tested.

### 2. Populate the occupancy ledger's provider/cache fields — `days`

All 820 per-model-call records across 98 stores carry `cached_input_tokens: 0`
and `provider_input_tokens: null`. `_cache_subsets`
(`context_occupancy_recorder.py:1511`) has never received a populated usage
object on a real run — its reconciliation, its clamping, and its warning path are
all dead in production.

**Why this outranks a pure-instrument item:** the composer context meter
(#625/#626) reads this ledger. A reader without cache fields "would recommend
trimming the stable prefix, which is exactly backwards" — that sentence is the
record's own docstring, and it is describing what we currently ship.

**Skip cost:** the one screen that tells a user where their context went is
cache-blind, and the trimming program's own ledger cannot audit its own results.

### 3. Lossless JSON-schema slimming — `hours`

Pydantic emits a `"title"` for every field: ~15–20% of every args schema, zero
semantic loss, **no model behaviour change**. Unlike progressive disclosure it
reaches third-party tools we do not author — `write_todos` 997 (LangChain
middleware), `grep` 539 (deepagents) — which are now among the largest resident
schemas precisely because disclosure already took the first-party ones down.

**Seam:** `observability/context_tool_ledger.py:204-208` is where
`model_json_schema()` is expanded, and the same entry feeds
`tool_schema_revision`. Slimming there changes the digest — confirm that is
intended before landing, since the digest binds prompt-cache identity.

### 4. Decide `artifact_family` exposure — _a decision, not an implementation_

`hyperparameters/contracts.py:265` — `artifact_family: ArtifactToolFamilyExposure
= ALWAYS`. That holds `publish_artifact` (805) + `stage_rowset_write` (900) =
**1,705 tokens resident on every cold run**. PR #632 shipped `ALWAYS` on purpose
because the only other state withholds `publish_artifact` from the model
entirely — a live capability change wanting an owner's decision.

**§7 changes the stakes:** a session's opening run is cold about two times in
three regardless of elapsed time, so this is 1,705 tokens at full price on the
first run of essentially every session — not an occasional cost.

### 11. Grow the interactive corpus — _the denominator under all of the above_

`cache_profile.py` scores 442 runs, but **424 are journey boots**. The
interactive corpus is **13 runs from one machine on one day**, which is why §7
reports the process-boundary result (n=437, robust) and explicitly declines to
report a user-behaviour rate. Every cost decision above is being made against a
denominator we have not actually measured.

---

## 3. Completion rate and correctness

### 5. Run the heavy arms — `~$2 and an afternoon`

[`heavy_tasks_ab.py`](../../../tools/harness-bench/heavy_tasks_ab.py) is 830 LOC,
`--plan`-checked, 19 offline gate tests, mutation-verified — and **no arm has ever
been driven against a model**. It was blocked on a staged runtime four days
behind the tree; that staleness is now much worse, so re-stage first.

Still unmeasured, and unreachable by the four short prompts: **delegation**,
**parallel tool execution**, the **tool-result cap**, the **per-tool-name call
budget**, and **MCP tool-name namespacing**.

```bash
node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64
npm run build --workspace @0x-copilot/desktop
BENCH_ARM=500 HEAVY_TASKS=h1-corpus python tools/harness-bench/heavy_tasks_ab.py  # validate for 1 task
BENCH_ARM=25  python tools/harness-bench/heavy_tasks_ab.py                        # own process
BENCH_ARM=500 python tools/harness-bench/heavy_tasks_ab.py                        # own process
python tools/harness-bench/rescore.py heavy-arm-25 heavy-arm-500
```

Expect ~45–60 model calls per arm. A number far from that is itself the finding.

### 7. A correctness axis on the bench — `days`

The bench scores **termination, not correctness**. Every cost number in
FINDINGS.md is therefore conditional: a prompt-trimming change could degrade
answer quality and still report 4/4. `heavy_tasks_ab.py` already carries the
primitive — each task declares an `expect` regex its final answer must match, and
`outcome_ok` is scored separately from `tool_rounds`. The gap is that the
recursion set, which is what every published cost number was measured on, has no
equivalent.

**Do this before, not after, the next trimming change** — otherwise item 3's
result is unfalsifiable in the direction that matters.

---

## 4. Still open from the audit's Top 8

The other five Top-8 items closed in #632 — see [§6](#6-closed-since-the-audit--do-not-re-raise).

### 6. `expires_at` + sweeper default — `hours`

Re-verified open: `runtime_worker/__main__.py:466` is still
`ApprovalExpirySweeperEnv.ENABLED, default=False`, and no creation site sets
`expires_at`. The query (`runtime_api_store.py:2591-2594`) and the sweeper
(`jobs/approval_expiry_sweeper.py:159`) both exist and both act on a field
nothing populates.

**Skip cost:** abandoned runs park forever, **and a compliance reviewer finds the
sweeper and cites it as implemented** — the exact failure mode `CLAUDE.md`'s
compliance section warns about.

**Already recorded as debt:** `dark_wiring_baseline.txt:169` names this exact
field — _"the expiry sweeper queries this field and nothing populates it"_ —
alongside three sibling `expires_at` fields (`:154`, `:171`, `:193`). The ratchet
is doing its job; nobody has spent the hours.

### 8. Wire FTS5 conversation search — `days`

Re-verified open: `grep -rn "conversations/search"` over `ai-backend` and the
facade returns nothing. The FTS5 table over titles _and_ message bodies is
created (`_catalog_index.py:102`), bm25 ranking is written, and
`runtime_api_store.py:1471` exposes it — with no port method, no route, no caller.
We are ahead of OpenCode on the mechanism (`session.ts:563` is
`like(title, '%q%')`) and behind it on the product.

**Skip cost:** the most-reached-for feature in any chat product is missing while
we pay to maintain the index on every write.

### 9. Desktop skill authoring — `days`

Re-verified open: `apps/desktop/renderer/destinationBinders.tsx:779` still reads
_"The skill editor route isn't built on desktop yet"_. The props exist and the
**deprecated** web host binds them.

**Skip cost:** on the surface `CLAUDE.md` declares is the product, a user cannot
author a skill, and the library stays at 3 runtime `SKILL.md` packages.

---

## 5. Root causes that outlive the individual items

### 10. Close the dark-wiring detector's zero-default blind spot — `days`

The audit's complaint that the repo had no sub-module wiring ledger is **no
longer true**: [`tools/check_dark_wiring.py`](../../../tools/check_dark_wiring.py)
now runs three detectors — test-only symbols, contract fields with a reader and
no producer, and wire keys the TypeScript side never property-reads — against
[`dark_wiring_baseline.txt`](../../../tools/dark_wiring_baseline.txt). It is a
good tool and it already catches `expires_at` four times over.

**Item 2 slipped past it anyway, and the reason is precise.** The contract-field
detector is deliberately scoped to the `x: T | None = None` shape, on the stated
grounds that _"a field merely sitting on a working default is a different and
uninteresting fact."_ `ContextOccupancySnapshot.cached_input_tokens` is
`NonNegativeInt = 0` — a working default. So it is out of scope by design.

**The occupancy fields are the counter-example to that assumption.** A numeric
field that defaults to `0` and is never produced does not read as absent; it
reads as _a measurement of zero_. That is the exact failure FINDINGS.md's method
notes single out — _"a broken instrument reporting zero is indistinguishable from
a genuinely cheap run"_ — and here it ran for 820 model calls across 98 stores
without one test going red.

**Fix:** extend the contract-field detector to numeric fields whose default is
the identity value (`0`), reported separately from the `None` population since
the false-positive rate will differ. Two of the three forms the audit named
remain uncovered either way: **wired-on-one-store-backend** and
**interpolated-into-a-prompt-string**.

### Root cause C — no user-authorable surface

Every extension point is a Python edit plus a redeploy. OpenCode's bet is that
everything is user-authorable and generated from one schema; that is what earns
them 7 of 9 dimensions. **Not proposed as a project here** — recorded so that
each new extension point is a deliberate choice rather than a default.

### Root cause D — no inbound protocol, no generated client

A hand-maintained ~6.5k-line type mirror against OpenCode's one Effect HttpApi
reflected into CI-diff-gated generated clients.

---

## 6. Closed since the audit — do not re-raise

Verified present at `f05ae4fc`:

| Audit item                            | Where it landed                                                                       |
| ------------------------------------- | ------------------------------------------------------------------------------------- |
| Top-8 #1 MCP vendor schema repair     | `capabilities/mcp/schema_repair.py`                                                   |
| Top-8 #2 MCP tool-name namespacing    | `capabilities/mcp/tool_source.py`                                                     |
| Top-8 #3 Subagent depth limit         | delegation depth caps, PR #632                                                        |
| Top-8 #4 `DESTRUCTIVE` above `BYPASS` | `capabilities/policy/service.py`                                                      |
| Top-8 #7 One per-PR journey           | `.github/workflows/ci-desktop.yml` — `desktop journey (first_run FR-0)`, PR-triggered |
| §1 step ceiling                       | `recursion_limit` 25 → 500; +25pts completion for +0.1% tokens                        |
| §2 error taxonomy                     | `ToolErrorCode.TOOL_RUN_FAILED`                                                       |

> **Trap worth repeating.** The main checkout on this machine sits on `dev` at
> `21ff212b`, twelve days behind `origin/dev`. Four of the rows above grep as
> _missing_ there. Always re-verify "still open" against a fresh `origin/dev`.
