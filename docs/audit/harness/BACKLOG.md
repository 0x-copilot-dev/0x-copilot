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

| #      | Item                                                 | Effort | Why it is where it is                                                                            |
| ------ | ---------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------ |
| **1**  | ~~Anthropic cache accounting~~ ✅ landed             | hours  | was two defects: writes invisible (latent) **and** reads counted twice (live, 71.7% over-report) |
| **2**  | ~~Occupancy ledger provider/cache fields~~ ✅ landed | days   | was three defects; the middle one raised inside a callback LangChain swallows                    |
| **3**  | Lossless JSON-schema slimming (drop `title`)         | hours  | the named next lever; ~15–20% of every args schema, and it reaches third-party tools             |
| **4**  | Decide `artifact_family` exposure                    | —      | a decision, not code: 1,705 resident tokens on every cold run, parked pending an owner           |
| **5**  | ~~Run the heavy arms~~ ✅ done                       | $0.03  | the ceiling never bound; the per-tool-name budget did. 3 of 5 dark claims now measured           |
| **6**  | `expires_at` + sweeper default                       | hours  | Top-8 #5, re-verified open; half-built machinery a compliance reviewer reads as implemented      |
| **7**  | A correctness axis on the bench                      | days   | today a trimming change can degrade answers and still report 4/4                                 |
| **8**  | Wire FTS5 conversation search                        | days   | Top-8 #6, re-verified open; we pay to maintain the index on every write                          |
| **9**  | Desktop skill authoring                              | days   | Top-8 #8, re-verified open; on the surface CLAUDE.md calls the product                           |
| **10** | Close the dark-wiring zero-default blind spot        | days   | a field defaulting to `0` and never produced reads as a measurement, not as absent               |
| **11** | Grow the interactive corpus                          | —      | 13 runs on one machine is not a sample; it is the denominator for all cost work                  |
| **12** | No per-model-call usage event on the run stream      | days   | three separate readers have now been written against an event that cannot fire on this path      |
| **13** | Model picker shows raw, dated, duplicated ids        | days   | five Haiku rows for one family, three badly named, one retired and 404ing                        |

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

### 2. Occupancy ledger provider/cache fields — ✅ **LANDED on this branch**

Filed as "the reconciliation has never received a populated usage object". True,
and incomplete: **three** independent defects sat between the provider's answer
and the row, and each alone was enough to keep the lane dark — so fixing the
obvious one first would have changed nothing measurable.

**2a — the dispatcher captured usage and dropped it.** `f10` ships `OFF`, so the
default path is `_awrap_occupancy_only`, whose docstring declared as a
deliberate limit that no `_ProviderLifecycleCallback` exists there. It stopped
being true when `_dispatch_with_retry` began attaching one for failure
classification; the observer's `on_llm_end` was recording usage that the success
path threw away, while the append site passed a hard-coded `usage=None`.

**2b — reading that usage raised, inside a callback the framework swallows.**
`for_provider` was typed `str`; the default path passes `provider=None` on
purpose (naming it would make every failure UNKNOWN and never retried). So every
observation did `None.strip()` → `AttributeError` → swallowed by LangChain. Run
succeeded, suite green, ledger null. One field was answering two questions whose
right answers differ.

**2c — even given a slug, it reached the wrong extractor.** Keys are normalized
slugs (`anthropic`); the only hint without a resolved route is `_llm_type`
(`anthropic-chat`), which matched nothing and fell to the LCD fallback — which
by design surfaces no `cache_creation`.

**The tell was in §7 all along:** `run_usage.jsonl` has cache data on the very
runs where `context_occupancy.jsonl` has none. Same calls, two lanes, because
`run_metrics.py` resolves from the normalized slug and this lane resolved from
`None`.

**Landed:** all three, with the seam driven end-to-end (not by handing the
observer to the code under test) and each mutation-checked to fail exactly the
test naming it. Written up in
[FINDINGS.md §7.2](../../../tools/harness-bench/FINDINGS.md).

**Confirmed against a live run** (2026-08-26): re-staged from this branch,
one Anthropic task on the packaged app. `occupancy calls carrying provider
totals` went `0 of 820` → `2 of 2`, and `runs writing cache` `0 of 442` → `1 of
1`. Item 1's correction measured on the same run: a warm run's fresh portion is
**938** tokens, against a pre-fix median of 21,091. See
[FINDINGS.md §7.3](../../../tools/harness-bench/FINDINGS.md).

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

### 5. Run the heavy arms — ✅ **DONE**

Both arms run on `claude-haiku-4-5`, five grant-free tasks, **$0.025 total** —
against §5's estimate of "a bit under $1 an arm". Written up in
[FINDINGS.md §8](../../../tools/harness-bench/FINDINGS.md).

**The headline is a negative result worth having:** _the step ceiling never bound
in either arm._ What stopped the two failing tasks at limit=500 was the
**per-tool-name call budget** (`execution.tool_call_budget = 10`), with six
`read_file:tool_budget_exceeded` rows. §1's ceiling win does not reproduce on
this task set with this model.

Three of the five previously-unreachable claims are now measured — the budget
(binds), delegation (6 → 21 rounds), parallel execution (peak 12). Two remain
unmeasured and the reasons are recorded rather than papered over: the
tool-result cap needs `h6-bigread`'s folder grant, which cannot be driven on this
host, and MCP namespacing needs two hand-connected servers.

**Do not over-read the correctness column.** n=1 per cell, and the same task made
1 tool call in one arm and 21 in the other. The defensible claims are the
mechanical ones, which come from terminal codes rather than a difference of one.

**Follow-up worth doing:** re-base `h6-bigread` on `/memories/` so the
tool-result cap stops depending on a native folder picker. It is the only reason
that claim is still dark.

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

### 12. There is no per-model-call usage event on the run stream — `days`

`usage.recorded` looks like one and is not. It is a **Generative Surfaces v2
ledger event**: `streaming_executor` returns early on `if not
surfaces_v2_enabled`, and the `handlers/run` emitter meters only the
VIEW_SHAPING spec-generation path. On the ordinary run path it cannot fire.

Three readers have now been written against it in the belief that it could:

- the harness-program's first scorer (documented in FINDINGS.md's method notes
  as "wrong event matcher" — the truer statement is that the event does not
  exist for this purpose);
- `heavy_tasks_ab.measure()`, which retained that reader as a "lower bound" and
  printed `in=0 out=0` on a run whose store recorded 20,287 input tokens;
- the composer's context meter, which reads the two `/context` REST endpoints
  precisely because the `context_occupancy` run event has no emitter either.

Each author independently concluded the runtime emits per-call usage on the
stream. It does not, and nothing says so.

**Fix:** either emit a real per-model-call usage event on the run path (the
occupancy recorder already has every number at `_append_occupancy`), or document
loudly at the event-type definition that `usage.recorded` is surfaces-only and
name the REST endpoint that answers the question instead. The first is a
feature; the second is an afternoon and stops the fourth reader.

**Skip cost:** a fourth person writes a fourth reader against a silent event,
and every live cost readout in the program keeps saying zero.

### 13. The model picker shows raw ids, dates, and dead models — `days`

Found while pinning a cheap model for the heavy arms. The picker offers **five
Haiku rows for one model family**:

| catalog id                   | rendered label             | verdict                      |
| ---------------------------- | -------------------------- | ---------------------------- |
| `claude-haiku-4-5`           | Claude Haiku 4.5           | correct                      |
| `claude-haiku-4-5-20251001`  | Claude Haiku 4.5.20251001  | date glued into the version  |
| `anthropic/claude-haiku-4.5` | Anthropic/claude Haiku 4.5 | provider prefix leaked       |
| `anthropic/claude-3-haiku`   | Anthropic/claude 3 Haiku   | provider prefix leaked       |
| `claude-3-haiku-20240307`    | Claude 3 Haiku 20240307    | **retired — Anthropic 404s** |

Three defects, one deriver. `ModelDisplayName.derive`
(`api/litellm_model_source.py:75`) splits on `-` and collapses a trailing run of
bare integers into a dotted version. It has no rule for:

1. **a trailing `YYYYMMDD` snapshot stamp** — `4-5-20251001` collapses to
   `4.5.20251001` rather than being recognised as a date and dropped;
2. **a `provider/` prefix** — never stripped, and the `/` blocks the title-case
   pass, so `anthropic/claude` renders with a lowercase `claude`;
3. **brand words** — `KNOWN_ACRONYMS` holds only `gpt`.

The fourth problem is not naming at all: **the catalog surfaces retired models
the vendor will 404.** Selecting `Claude 3 Haiku 20240307` produces
`external_service_error` → _"We couldn't complete this run. Please try again."_ —
a retryable-looking message for a permanently dead model. That cost this session
one wasted arm before the log was read.

**Fix:** a date-stamp rule and a prefix strip in the deriver (cheap, testable —
the existing test file already pins `claude-opus-4-8` → `"Claude Opus 4.8"`);
prefer the undated alias when both are present so one family shows one row; and
either filter models the pricing catalog marks deprecated, or classify a
`not_found_error` as non-retryable so the copy stops inviting a retry.

**Skip cost:** the model picker is the first screen a BYOK user meets after
adding a key, and it currently offers them five ways to pick one model, one of
which cannot work. Related: [[project_error_copy_is_model_paraphrase]] — the
404's user-facing text is the collapsed-taxonomy problem again.

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
