# harness-bench — what the measurements actually say

Every number here came from the packaged app running against a real model, scored
from the file-native run store (`run_usage.jsonl`, `tool_invocations.jsonl`,
`context_occupancy.jsonl`, and the session's `events.jsonl`) — the same records
the product bills from.

> **Revision note.** An earlier version of this file declared finding 1
> FALSIFIED on the strength of a single arm and a metric that could not see the
> failure it was measuring. It was wrong, and the correction is below rather
> than quietly edited away, because the mistake is more instructive than the
> result.

## 1. The step ceiling was binding real work. Raising it is a measured win.

`recursion_limit` was raised from LangGraph's inherited **25** super-steps to an
explicit 500. Same stage, same tasks, same model, same order; the only variable
is `COPILOT_HP__EXECUTION__RECURSION_LIMIT`.

| task           | limit=25             | limit=500               |
| -------------- | -------------------- | ----------------------- |
| t1-trivial     | completed            | completed               |
| t2-three-steps | completed            | completed               |
| t3-todo-driven | **failed** — ceiling | **completed**, 5 rounds |
| t4-long-chain  | completed            | completed               |

```
limit=25 : 3/4 completed, 95,655 total tokens
limit=500: 4/4 completed, 95,746 total tokens   (+0.1%)
```

**+25 points of completion rate for +0.1% tokens.** t3's terminal event at
limit=25 reads `"code": "recursion_limit_exceeded"` — the run was stopped by the
ceiling, not by anything it was doing wrong. This is, so far, the only measured
outcome win in the harness program.

The finding stands as stated, because a run the ceiling stopped is a run that
did not finish and that is what these columns are about. But **the word
"completed" in this table means terminated, not answered** — §6.1 shows two runs
counted here that completed and answered the wrong question.

### How the first pass got this exactly backwards

The first scorer inferred "did the ceiling bind?" from the count of **completed**
tool invocations. It reported 3 rounds against a ceiling of 25 and I concluded
the ceiling was never approached — then declined to run the second arm on the
reasoning that a ceiling never reached cannot behave differently when raised.
Both steps were wrong, and the second compounded the first.

A run that trips the ceiling with a tool call still in flight **never completes
that call**, so the failing round is invisible to a completed-rounds count. The
metric was structurally blind to the event it existed to detect. The run's own
`run_failed` event had said `recursion_limit_exceeded` the whole time.

The rule the scorer now encodes: **read the terminal code; never infer it.**

## 2. The `write_todos` "crash" was a mis-stamped innocent

The earlier version of this file reported that `write_todos` threw
`tool_exception` on its 4th call. It did not throw at all. The 4th call was in
flight when the graph hit its step ceiling; the blanket `except Exception`
handler reconciled every open call with `ToolErrorCode.TOOL_EXCEPTION`, stamping
a tool that never ran. The tell is in the ledger: the three good rows carry a
`result_summary`, the accused row carries `{}`.

The real defect was therefore in the error taxonomy, not the todo tool — a
single blanket code applied to every unhandled-exception run failure, which made
an infrastructure failure read as a tool bug. That is now
`ToolErrorCode.TOOL_RUN_FAILED`, with copy that declines to blame the tool and
names the run's own error instead.

**Both of this file's original headline findings were wrong in the same
direction: they blamed the thing in front of the evidence rather than reading
the evidence.**

## 3. The cost structure — the user's prompt is 15 tokens, the tool schemas are 9,759

Per-run context occupancy:

```
declared = 14,627      UNDECLARED = 8,312

    9,759  tools        ← 67% of declared
    4,853  system       (entirely undeclared)
       15  messages     ← the actual user prompt

top tool schemas
    1,381  publish_artifact
    1,223  stage_rowset_write
      722  revise_artifact
      667  ask_a_question
      600  run_tool_program     ← added by this program; nothing has ever called it
```

## 4. …but 97% of it is cache reads, so tokens ≠ cost

Applying the standard cache multipliers (fresh 1.0x, cache read 0.1x, cache
write 1.25x):

| run      | in     | cached | out | full-price-equivalent |
| -------- | ------ | ------ | --- | --------------------- |
| 1 (cold) | 23,181 | 0      | 5   | **23,181**            |
| 2        | 23,253 | 23,094 | 99  | 2,468                 |
| 3        | 24,221 | 23,337 | 176 | 3,218                 |
| 4        | 24,615 | 23,714 | 105 | 3,272                 |

```
raw tokens billed-as-listed : 95,655
full-price-equivalent       : 32,525   (34% of raw)
```

**The cold run alone is 71% of the total cost of all four.** The fixed ~23k
prompt is nearly free when warm and full price on every cold start.

**The decision this forces:** prompt-trimming pays off strictly in proportion to
cold-start frequency. For a continuously-used session it is close to worthless;
for a bursty desktop user — open the app, ask one thing, close it — nearly every
run is a cold start and the 23k is paid in full each time. Anything that shrinks
the _cold_ prompt is the lever; anything that only helps warm runs is not.

Unmeasured and worth measuring next: what fraction of real runs hit a cold cache.

## Method notes — three instrument failures, all worth not repeating

1. The first scorer counted `usage.recorded` events off the events API and
   returned **0 tokens for every task**. A broken instrument reporting zero is
   indistinguishable from a genuinely cheap run. Scoring now reads the run store
   directly, and `rescore.py` is offline, so fixing a measurement never costs
   another paid run.
2. The first scorer's round count was a **lower bound that could not observe the
   failure mode under test** (finding 1). A proxy metric must be checked against
   the thing it proxies before any conclusion rests on it — especially a
   negative conclusion, which is the kind that stops further investigation.
3. **A completion-rate metric cannot see a wrong answer**, and until now nothing
   in `recursion_ceiling_ab.py` could. §6.1 has the evidence: two runs that
   terminated `completed`, counted toward the completion column every published
   cost number is quoted beside, and answered something other than what was
   asked. Termination and correctness are separate axes and neither substitutes
   for the other, so `outcome_ok` is now scored alongside `status` — with the
   three-valued discipline note 1 earned: `None` means NOT MEASURED, never
   wrong, because a fabricated negative stops an investigation that a
   fabricated zero would at least have invited.

`tool_rounds` still counts COMPLETED tool invocations and is still a lower bound
on super-step spend. It is retained as a rough cost signal only; the ceiling
question is now answered by `terminal_code`.

Note 1 has a second, still-live instance that the correctness work surfaced. The
live `llm_calls` column in both committed recursion reports reads **0 on all
eight rows** while the store records 8 model calls over the same runs, because
`usage.recorded` does not fire on the ordinary run path at all — the event
appears zero times in those sessions' `events.jsonl`. The live counter now
returns `None`; the zeros already in `runs/` are a dead instrument's and should
be read as `model_calls`. `compare()` had in turn been printing _"the old ceiling
of 25 was NEVER reached by this task set, so raising it bought nothing"_ off
that zero — a conclusion finding 1 above had already falsified from the same
directory.

## 5. The four short prompts cannot reach most of the remaining claims

`recursion_ceiling_ab.py`'s tasks peak at **3 completed tool rounds** (4 distinct
invocations, one of which never ran — see below). That is enough to trip a
ceiling of 25 and nothing else. Rescoring both arms with the extended scorer says
how far short they fall:

```
peak COMPLETED tool rounds in any task: 3      (4 invocations)
peak ESTIMATED super-steps in any task: 22     (fit: 6 + 4/round, ceiling 25)
peak tool result entering context:      122 tokens   (the cap is 8,000)
delegated rounds, in any task:          0
peak parallel tool calls, in any task:  1
MCP tool names with an mcp__ prefix:    0
```

The super-step estimate is the useful one: `t3-todo-driven` sat at **22 of its
25**, and the next round's four steps would have taken it past — which is what
the ceiling did. That is a metric agreeing with a known ground truth, not a
prediction, and it is the reason the fit is worth carrying.

So **delegation, parallel execution, the tool-result cap and MCP namespacing are
all still unmeasured** — not because they failed, but because nothing in the set
touches them. `heavy_tasks_ab.py` is the task set built to reach them: seven
tasks, six of which need no folder grant and no connector, each declaring what
it needs from the machine, what it plans to spend per tool _name_, and a regex
its final answer must match. `--plan` prints all of that for free.

Three design constraints came out of reading the runtime rather than guessing:

- `execution.tool_call_budget` is **10 calls of one tool name per run**. A task
  planning more measures the budget cutting the chain off, which looks identical
  from outside to a ceiling stop. Every task declares `planned_calls` and a gate
  test fails on any plan that reaches the budget.
- A grant-free task must address `/memories/`, not a host-absolute path
  (refused without a grant), and must never ask for `grep`/`glob` there —
  `FileMemoryBackend` answers both with an **empty result, not an error**, which
  is the same green-tick-over-nothing shape as the original `ls ~/Downloads`
  defect.
- **`FileMemoryBackend.read` accepts `offset` and `limit` and uses neither.**
  It returns the whole document on every call
  (`runtime_adapters/file/agent_state_store.py`, the params are bound and never
  referenced). So on the `/memories/` route there is no paging to measure and
  `reads.default_line_limit` (2000) is unreachable. A prompt that says "page
  through it with the offset argument" is asking for something that cannot
  happen, and would grade the model down for it.

#### One claim was LOST when `h6-bigread` moved to `/memories/`, and it is not being dropped quietly

H6 used to claim two things: "a >2000-line read pages correctly **and** pushes
the tool-result cap". Rebased on `/memories/` so it needs no folder grant, it
buys the cap and loses the paging half — to the constraint immediately above.
So:

| claim                               | status                                                                     |
| ----------------------------------- | -------------------------------------------------------------------------- |
| pre-model tool-result cap           | now reachable, grant-free — see §8                                         |
| `reads.default_line_limit` paging   | **newly unmeasured**: the memory backend ignores `offset`/`limit` entirely |
| host-path refusal / workspace reads | **newly unmeasured**: nothing in the set touches a host path any more      |

Recording it rather than deleting the row is the point. A set of seven tasks
that silently stops claiming something it used to claim is the same pathology
`Needs` exists to prevent — it just moves from the row count to the claim
column. Measuring paging again needs a route whose `read` honours `offset`,
which the workspace backend does and the memory backend does not.

**The `Needs.HOST_GRANT` lane was removed with it.** Its grant could only be
minted through the app's own NATIVE folder picker, this host denies the
controlling process Accessibility, and so across every arm the harness has run
that lane produced a `skipped` row and never once a measurement. Bringing it
back costs the enum member, a `fixture_keys` field on `HeavyTask`, the
substitution branch in `Arm.run_task`, the `_workspace_lib.attach_folder` call —
all four are in this file's git history — plus a host that grants Accessibility.
It is not free to restore, and it was not free to keep: a mechanism nothing
exercises is a mechanism nobody knows still works.

### The scorer re-derived finding 2 without being told it

`reconciled_rounds` counts invocations that reached a terminal row carrying an
**empty `result_summary`** — closed by the blanket handler rather than by
running. Pointed at the arm-25 store it names `write_todos` in `t3-todo-driven`
unprompted, which is exactly the mis-stamped innocent of §2. `orphaned_rounds`
does **not** see that case, and that is worth stating plainly: the reconciler
leaves no open row behind, so "count the calls that never closed" finds zero.
Two metrics, two different blind spots, and neither is a substitute for the
other. Every column in `rescore.py` now carries its blind spot in the header.

### The heavy set has NOT been run yet, and that is a statement about this box

It is structurally validated — `--plan`, 19 offline gate tests, and a mutation
check confirming each design test fails on the defect it names and only that one
— but no arm has been driven against a model. The reason is worth recording
because it will be the next person's reason too:

```
apps/desktop/resources/runtime/darwin-arm64/services/ai-backend/src   Aug 10 19:50
newest commit touching services/ai-backend/src                       Aug 14 13:17
```

**The only staged runtime on this machine is four days behind the tree.** Per
[the journeys README §1b](../desktop-journeys/README.md) it would run old backend
code and report its verdict with total confidence — and specifically it predates
the `TOOL_RUN_FAILED` fix that finding 2 above produced, so it would measure the
error taxonomy this file has already corrected.

Re-stage first, then validate for the price of one task, then pay for the arms:

```bash
node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64
npm run build --workspace @0x-copilot/desktop

BENCH_ARM=500 HEAVY_TASKS=h1-corpus python tools/harness-bench/heavy_tasks_ab.py
BENCH_ARM=25  python tools/harness-bench/heavy_tasks_ab.py     # own process
BENCH_ARM=500 python tools/harness-bench/heavy_tasks_ab.py     # own process
python tools/harness-bench/rescore.py heavy-arm-25 heavy-arm-500
```

Each arm runs in its OWN process: the arms share nothing, and the ceiling is read
once per service start.

What the heavy tasks should be expected to cost, so a surprise is legible: about
**45-60 model calls per arm**, ~1.2M listed input tokens, ~10k output, ~150k
full-price-equivalent, 5-8 minutes — a bit under $1 an arm at Sonnet-class list
prices, and about 15x the recursion set. A number far from that is itself the
finding: far below means the model batched work the prompts asked it to serialise
(read `peak_parallel`), far above means something is looping (read `budget_notes`
and `terminal_code` before concluding anything about the ceiling).

## 6. The cold-prompt trajectory, measured at each step

Same four tasks, same model, same stage discipline (re-staged from the tree
under test before every run). Cold prompt is run 1's input, which is the one
paid at full price.

| build                                   | cold prompt | tools segment | 4-task total | completion |
| --------------------------------------- | ----------- | ------------- | ------------ | ---------- |
| baseline (harness program as merged)    | 23,181      | 9,759         | 95,655       | 3/4 @ 25   |
| + `run_tool_program` gated, attribution | 22,304      | 9,159         | 91,098       | 4/4        |
| + first-party tool disclosure           | **20,547**  | **7,910**     | **83,662**   | 4/4        |

**Cumulative: −2,634 cold tokens (−11.4%), −11,993 total (−12.5%), completion
3/4 → 4/4.**

The disclosure step was predicted at −1,326 and measured **−1,757** — it beat its
own estimate, because deferring prose also shrank text the estimate attributed
elsewhere. Predictions here are worth recording precisely so they can be scored;
this one was conservative.

What remains resident and what it costs: `write_todos` 997 (third-party,
LangChain middleware), `stage_rowset_write` 900, `publish_artifact` 805,
`ask_a_question` 667 (deliberately resident — it is reached while a human
waits), `grep` 539 (deepagents). The named next lever is **lossless JSON-schema
slimming**: pydantic emits a `"title"` for every field, ~15–20% of every args
schema with zero semantic loss, applying to third-party tools too and requiring
no model behaviour change at all.

### 6.1 The completion column is not a correctness column

Every number in the table above is a cost or a termination count. Nothing in it
looks at the answer, so a trim that made the model cheaper **and worse** would
have read as pure win. That is not a hypothetical about future trims — it is
already true of the arms those numbers came from:

- **limit=500, `t4-long-chain`** (run `733191036bb64fdb858006d0b5f8b934`):
  `status: completed`, `terminal_code: run_completed`, 1 model call, 0 tool
  invocations, 94 output tokens. Its entire final answer:
  _"**1:** Not prime — 1 has only one divisor (itself), so it doesn't meet the
  definition of prime."_ It was asked to walk 1 through 12 and list the primes.
  It answered one number of twelve and listed nothing.
- **limit=25, `t4-long-chain`** (run `3b323faecf7048f1b670be22d4fb40df`): also
  `completed`, and it replied with a checklist about **European capitals** —
  the previous task's content — then "Step 1 — Three European capitals: Paris,
  Rome, Madrid." It never counted, and never listed a prime.

So `3/4 @ 25 → 4/4` is a **termination** claim and was never an answer-quality
claim. Read strictly, at most **1 of 4** answers in either arm is verifiably
right (`t1`, which asked for one word), because `t2` and `t3` as written had no
unique answer at all: "three primary colours" is red/yellow/blue _or_
red/green/blue, and "three European capitals … one river in each" is whatever
the model picks.

**What changed.** Each of the four tasks now declares `expect`, a regex its
final answer must match, fixed in `recursion_ceiling_ab.py` and derived from a
constant there. `rescore.py` re-grades it offline from the store, so a
correctness mistake never costs a paid run. `t2` and `t3` needed new PROMPTS to
have a checkable answer (`t2` is now arithmetic, `t3` a three-row sort); each
keeps verbatim the structural instruction that drives its round count, and `t4`
gained only a sentinel line. Governance, stated so it can be held to: **an
expectation may be relaxed only by changing the prompt and re-running — never by
editing the pattern after reading an answer.**

**What this costs the table above, and it is not nothing.** `arm-25` and
`arm-500` ran **prompt set v1**. They carry no recorded expectation, so they are
reported `?` — UNKNOWN, explicitly distinct from wrong — and they are _not_
graded against v2's answers, because an arm is a measurement of the prompts it
actually ran. Two consequences:

- **§6's trajectory cannot be extended by a v2 arm.** Splicing a v2 row into
  that table would compare four prompts against three different ones. Both arms
  must be re-measured under v2 before any new cost claim joins it.
- **The cost shape of v2 is asserted, not measured.** v1's model calls were
  1/1/4/2 at limit=25 and 1/1/1/1 at limit=500. The re-run must be compared
  against those; if they move materially, the trajectory restarts rather than
  continues.

The first honest correctness number this produces will look worse than `4/4`.
That is the point of measuring it.

## 7. The cold-start question §4 left open: it is a process boundary, not a clock

§4 ended by naming the next measurement: _"what fraction of real runs hit a cold
cache."_ That fraction is the multiplier on every trimming change, so it decides
whether the remaining prompt levers are worth building at all.

`cache_profile.py` answers it offline from `run_usage.jsonl` records already on
disk — **99 stores, 442 runs, no paid run**. It is the same discipline as
`rescore.py`: scoring is free, so a measurement mistake never costs money.

```
cold rate by position in store
  first in store              96 runs    65 cold    67.7%
  later in store             341 runs    27 cold     7.9%
```

**A run that opens a store is cold about two times in three. A run that follows
another inside the same store is cold about one time in thirteen.** Those two
populations sit on the same time scale — a store's runs are typically seconds to
minutes apart either way — so the ~9x difference is not the cache expiring.

### The obvious explanation is wrong, and the test that kills it

A prompt cache lives at the **provider**, keyed on the prefix — not on our disk.
So a fresh COPILOT_HOME should still hit a warm cache when an identical prefix
went out recently from any process, and the cold rate on a store's first run
should fall away as the gap to the nearest earlier run anywhere shrinks.

It does not:

```
first-run-in-store, by gap to the nearest earlier run ANYWHERE (same provider)
  < 1 min      27 runs    74.1% cold     ← the LOWEST gap is the HIGHEST cold rate
  1-5 min      22 runs    54.5% cold
  5-15 min     10 runs    60.0% cold
  15-60 min    11 runs    81.8% cold
  1-24 h       17 runs    70.6% cold
  > 1 day       6 runs    66.7% cold
```

Flat, non-monotone, and worst at the shortest gap. **Time is not the driver.**
Whatever makes a first run cold is structural, and it survives an identical
prompt having gone out seconds earlier.

**What this buys the trimming program:** §4 said prompt-trimming pays "strictly
in proportion to cold-start frequency", and that for a bursty desktop user —
open the app, ask one thing, close it — "nearly every run is a cold start". That
was a reasonable assumption. It is now an evidenced one: a session's opening run
is the expensive one regardless of when the app was last used, so **there is no
usage pattern a user can adopt that makes the resident prefix cheap.** Every
token cut from the cold prompt is paid back on the first run of every session.

### Three limits on that number, stated rather than buried

1. **`run_usage` is a rollup across every model call in the run.** A run whose
   first call was cold and whose later calls were warm reports `cached > 0` and
   scores WARM. 67.7% is a **lower bound**.
2. **A store is a COPILOT_HOME, not a person.** 424 of the 442 runs are journey
   boots with heterogeneous configs, so "first in store" conflates a process
   start with a prefix change. This corpus cannot separate them — that needs the
   per-call ledger, which is empty (below).
3. **The interactive corpus is 13 runs from one machine on one day.** Its 15.4%
   is reported for completeness and should not be read as a user-behaviour rate.

### The instrument that could settle it has never been populated

```
runs reading cache :   346 of 442   (6,333,964 tokens)
runs writing cache :     0 of 442   (0 tokens)
occupancy calls carrying any cache field : 0 of 820
occupancy calls carrying provider totals : 0 of 820
```

A cache read is impossible without a preceding write. 6.3M read tokens against
zero recorded writes is a statement about the instrument, not the cache.

Two distinct defects sit behind it, and neither is visible from a green test
suite:

- **`ContextOccupancySnapshot` cache fields are dead on every real run.** All
  820 per-model-call records across 98 stores carry `cached_input_tokens: 0`
  and `provider_input_tokens: null`. The reconciliation that consumes them
  (`context_occupancy_recorder.py:1511`, `_cache_subsets`) has never received a
  populated usage object in production. The record's own docstring says these
  fields are "what makes the report correct rather than merely large", because
  without them a reader "would recommend trimming the stable prefix, which is
  exactly backwards" — which is precisely the reading the composer's context
  meter (#625/#626) now presents to users.
- **The Anthropic extractor cannot see a cache write in LangChain's normalized
  shape.** `token_usage.py:432-441` reads `cache_creation_input_tokens` from the
  top-level block only, while `cache_read` gets a second lookup inside
  `input_token_details`. LangChain 1.5.3's `InputTokenDetails` has exactly three
  keys — `audio`, `cache_creation`, `cache_read` — so the read is found there and
  the write never is. `provider_cache_metadata_observed` carries the same
  asymmetry.

**On the write half, what is NOT established** is whether the missing counter is
currently costing tokens. If writes were being dropped, a cold run's recorded
`input_tokens` would be anomalously small, since
`gross_input = non_cache + cache_creation + cache_read`. Measured, they are not:

```
                cold median input    warm median input
  anthropic            20,058               40,844
  openai               11,465               12,898
  virtuals             21,655               22,977
```

Cold runs are comparable to warm ones, so no write-sized hole is visible. **A
dropped write and an absent write produce an identical record — and that
indistinguishability is the finding.** It is the same shape as §1: a metric that
cannot observe the event it exists to detect.

### 7.1 The same table had a second finding in it, and this file missed it

The row above was read once and explained away — anthropic warm runs are 2x cold
runs because a warm run is a later turn carrying more conversation. That is a
plausible story and it is wrong, which the neighbouring rows already say: openai
and virtuals are 1.1x on the same argument. Only anthropic doubles.

The check that settles it asks what a warm run's **fresh** portion is. If
`input_tokens` is a correct gross figure, `input − cached` is just the new turn's
content and should be small. If a cache read was added on top of a figure that
already included it, `input − cached` is the whole prompt over again:

```
             warm median (input − cached)     cold median input      ratio
  anthropic            21,091                      20,058            1.05
  openai                  346                      11,465            0.03
  virtuals                931                      21,655            0.04
```

**Anthropic's "fresh" portion is the entire prompt.** OpenAI's is 346 tokens,
which is what a correct figure looks like.

The cause is one line up from the missing write counter, in the same function.
`_UsageBlocks` yields **two** wire shapes and they disagree about what
`input_tokens` means. Provider-raw `response_metadata.usage` excludes the caches,
so summing is right. LangChain's `usage_metadata` documents `input_tokens` as
"the sum of all input token types" — its own example is `input_tokens: 350` over
details summing to 310 — so the details are **subsets**, and
`gross = input + cache_creation + cache_read` counts the read twice. Anthropic is
the only provider whose extractor takes that branch, which is exactly the shape
of the measurement.

Across 442 runs, anthropic input is over-reported by **71.7%** — 7,972,019
recorded against a corrected 4,642,605. The pricing consequence is not a wash,
because the identity `(input − cached − creation)·p_in + cached·p_cached` turns
into `true_gross·p_in + cached·p_cached` once `input` carries `cached` twice:
**the cached tokens are billed at the full input rate _and_ at the cached rate.**

Two things worth keeping from how this went. The first is that §4's headline —
"97% of it is cache reads" — is unaffected, because it was computed from
`cached / input` on a bench arm and both terms move together; but any absolute
anthropic token or cost figure taken from the run store before this fix is
inflated. The second is the method failure, which is the familiar one: **the
anomaly was in the first table this file printed, and it was explained rather
than tested.** A one-line ratio against the other two providers would have caught
it immediately. §1's rule generalizes — read the number, never infer the reason.

Both halves are fixed together, because they cannot be fixed apart: adding the
missing `cache_creation` details lookup to the old arithmetic would have added it
on top of an already-gross figure and made the over-count worse.

```bash
python tools/harness-bench/cache_profile.py          # free, offline, no app
```

### 7.2 Why the ledger was empty: three defects, and a green suite over all of them

§7 reported that the per-model-call ledger carried `provider_input_tokens: null`
and zeroed cache figures on **820 of 820** records, and filed it as "the
reconciliation has never received a populated usage object". That was true and
incomplete. Three independent defects sat between the provider's answer and the
row, and **each one alone was sufficient to keep the lane dark** — which is why
fixing the obvious one first would have changed nothing at all.

**1. The dispatcher captured the usage and dropped it.** `FeatureModeSet.f10`
ships `OFF`, so the shipped default takes `_awrap_occupancy_only`, whose
docstring declared as a deliberate limit: _"There is no
`_ProviderLifecycleCallback` here, so the snapshot carries no
`provider_input_tokens` and no cache figures."_ That stopped being true when
`_dispatch_with_retry` began attaching one for failure classification. The
observer is a `BaseCallbackHandler` whose `on_llm_end` records usage, so the
totals were being collected and then discarded on the success path, while the
append site passed a hard-coded `usage=None`. **The sentence outlived the
condition it described, and because it read as a considered limit rather than a
gap, nobody re-checked it.**

**2. Reading the usage raised, inside a callback the framework swallows.**
`TokenUsageExtractorRegistry.for_provider` was typed `str` and implemented as
`provider.strip().lower()`. The default path constructs its observer with
`provider=None` **on purpose** — naming it would yield LangChain's `_llm_type`
("anthropic-chat"), which matches no failure-adapter key and would classify every
provider failure as UNKNOWN, i.e. never retry. So every usage observation on the
default path did `None.strip()`:

```
>>> obs = _ProviderLifecycleCallback(provider=None, adapters=...)
>>> obs.on_llm_end(result_with_usage)
AttributeError: 'NoneType' object has no attribute 'strip'
>>> obs.usage
(NormalizedTokenUsage(input_tokens=0, ...), False)
```

LangChain does not fail a call when a callback handler raises. The run
succeeded, the response was correct, no test went red, and the only symptom
anywhere was a ledger column that was always null — **which is indistinguishable
from a provider that reports no usage.** One field was answering two questions
(which adapter classifies failures, which extractor reads usage) whose right
answers differ, and the type signature hid the collision.

**3. Even given a slug, it reached the wrong extractor.** Registry keys are our
normalized slugs (`anthropic`); the only hint available without a resolved route
is `_llm_type` (`anthropic-chat`), which matched nothing and fell to the LCD
fallback — and the LCD _deliberately_ surfaces no `cache_creation`. So the lane
would have stayed half-blind even after it stopped raising.

The two lanes' disagreement was the tell all along, and §7 printed it without
reading it: `run_usage.jsonl` **has** cache data on the same runs where
`context_occupancy.jsonl` has none. Same provider, same calls, two lanes —
because `run_metrics.py` resolves its extractor from the normalized slug and the
occupancy lane resolved it from `None`.

All three are fixed, with the seam driven end-to-end rather than by handing the
observer to the code under test, and each mutation-checked to fail exactly the
test that names it. Re-run `cache_profile.py` against a store written by a build
carrying this fix — the `occupancy calls carrying provider totals` line is the
one to read, and it should stop being `0 of N`.

**The rule this earns:** a value a framework will swallow an exception around is
not observable by testing that the surrounding operation succeeded. Both §1 and
§7.2 are the same failure at different layers — a signal that cannot distinguish
"measured zero" from "never measured". The ledger needs the distinction the
`NormalizedTokenUsage` contract already names: `provider_cache_metadata_observed`
exists precisely so that "zero cache tokens without that bit must never be called
a miss."

### 7.3 Confirmed against a live run — and a third instrument reporting zero

§7.1 and §7.2 were argued from code and unit tests. Both are now confirmed on the
packaged app against a real Anthropic model, which is the only evidence that
settles a "landed not wired" claim.

**Stage discipline first**, because §5 is emphatic that this is where a verdict
goes wrong: the runtime was re-staged from the branch under test and the stamps
checked before anything was believed.

```
staged src mtime          Aug 26 13:26:41
newest ai-backend commit  Aug 26 13:23:42     ← stage is NEWER: not a stale snapshot
```

One task (`HEAVY_TASKS=h1-corpus`, `JOURNEY_PROVIDER=anthropic`), six `write_file`
rounds, completed. What the store now holds:

```
                                            BEFORE            AFTER
runs writing cache                          0 of 442          1 of 1   (936 tokens)
occupancy calls carrying cache fields       0 of 820          2 of 2
occupancy calls carrying provider totals    0 of 820          2 of 2
```

Every counter this program has ever read as structurally zero is populated. The
two occupancy rows also show the cold→warm pair the old code could not represent
at all — row 1 `cache_creation 19,349 / cached 0` (the call that wrote the
prefix), row 2 `cached 19,349 / cache_creation 936` (the call that read it).

**§7.1's correction, measured:**

```
warm Anthropic run, fresh portion (input − cached)
  before (median of 157 runs)   21,091     ← the whole prompt again
  OpenAI, same metric              346     ← what a correct figure looks like
  after, this run                  938
```

#### The third instrument, found by disbelieving a passing run

The arm reported `PASS`, and printed:

```
h1-corpus: status=completed ok=True llm_calls=0 tool_calls=6 in=0 out=0 12.1s
```

Six tool calls and a completed answer for **zero tokens and zero model calls**.
The store for that same run says 20,287 input tokens. `measure()` sums
`usage.recorded` events off the events API — and this file's own method notes
open by documenting that exact reader returning "0 tokens for every task". It was
never removed; it was retained as a "lower bound", which is how it survived.

It is not a lower bound. **`usage.recorded` is a Generative Surfaces v2 ledger
event, not a per-model-call usage event on the run stream.**
`streaming_executor` returns early on `if not surfaces_v2_enabled`, and the
`handlers/run` emitter meters only the VIEW_SHAPING spec-generation path. On the
ordinary run path the sum is structurally 0 forever, on every arm, in every
build. The run's actual event stream carries `model_call_started`, `tool_result`,
`final_response` — and no usage event of any kind.

`measure()` now counts `model_call_started` and reports tokens as **`None`**,
printed as `tokens=via rescore.py`. A number that cannot be observed should say
so rather than print a zero, because _this_ is the third time in this program a
zero has been mistaken for a cheap run:

| #   | Instrument                        | Reported | Actually                   |
| --- | --------------------------------- | -------- | -------------------------- |
| 1   | first scorer, `usage.recorded`    | 0 tokens | never emitted on this path |
| 2   | occupancy `provider_input_tokens` | 0 / null | raised inside a callback   |
| 3   | `measure()`, retained as a bound  | `in=0`   | 20,287, per the same run   |

All three are the same defect: **a signal that cannot distinguish "measured
zero" from "never measured"**, shipped because the surrounding operation
succeeded. The `NormalizedTokenUsage` contract already names the cure — a
separate `provider_cache_metadata_observed` bit, so that "zero cache tokens
without that bit must never be called a miss". Every counter this program adds
from here should carry its own version of that bit.

## 8. The heavy arms, finally run — the ceiling never binds, the tool budget does

§5 built `heavy_tasks_ab.py` to reach five claims the four short prompts cannot,
and then recorded that no arm had ever been driven. Both arms are now run.

**Setup, and two deliberate departures from §5's recipe.** Model pinned to
`claude-haiku-4-5` via `COPILOT_JOURNEY_MODEL` — the earlier passes silently used
whatever the app defaulted to, which was `claude-opus-4-5`, the most expensive
model in the catalog, for a benchmark that writes six files. Task set pinned to
the five that declared `Needs.NOTHING` **at the time of this run** — `h6-bigread`
then needed a folder grant that cannot be driven on this host, and
`h7-mcp-namespace` needs two hand-connected MCP servers. Both arms: same model,
same tasks, same order, own process. (H6 has since been rebased onto
`/memories/` and needs nothing; the arms below predate that, which is exactly
why their tool-result-cap row is empty.)

```
arm 25 : 5/5 completed, 4/5 correct, 81,330 listed input, $0.0119
arm 500: 3/5 completed, 3/5 correct, 69,558 listed input, $0.0135
```

**Cost first, because §5's estimate was badly wrong in the useful direction.** It
predicted "~1.2M listed input tokens, ~150k full-price-equivalent, a bit under $1
an arm". Measured: **81k listed and $0.012**. Two reasons — the estimate assumed a
Sonnet-class model, and it predicted 45–60 model calls per arm against an actual
5 (one per task; Haiku batches its tool calls into a single assistant turn rather
than round-tripping per call). The whole two-arm experiment costs **$0.025**.

### The headline: the step ceiling never bound, in either arm

```
no run was stopped by the step ceiling in any arm
```

§1's win — `recursion_limit` 25 → 500 buying +25 points of completion — does not
reproduce here, and the reason is visible in the failure column rather than
inferred. What stopped `h4-delegate` and `h5-longchain` at limit=500 was the
**per-tool-name call budget**:

```
read_file:tool_budget_exceeded  x6      (execution.tool_call_budget = 10)
read_file:tool_run_failed       x2
```

That is one of the five claims §5 listed as unreachable, and it is now measured:
**the budget binds on real work, and the ceiling does not.** For the tasks in
this set, the ceiling raise §1 paid for is not the constraint that matters.

### Three of five previously-unmeasured claims are now reached

| claim                | §5 said       | measured now                              |
| -------------------- | ------------- | ----------------------------------------- |
| per-tool-name budget | unmeasured    | **binds** — 6 `tool_budget_exceeded` rows |
| delegation           | 0 in any task | **6 → 21** delegated rounds (`h4`)        |
| parallel execution   | peak 1        | **peak 12** parallel calls                |
| tool-result cap      | unmeasured    | reachable but NOT YET RUN — see below     |
| MCP namespacing      | unmeasured    | still unmeasured — 0 servers connected    |

The two that remain unmeasured are the two whose tasks were excluded, and the
scorer says so itself rather than reporting a zero: _"zero namespaced names on a
profile with no connected server is NOT evidence either way."_

#### The tool-result cap: the number in this table's first draft was the wrong constant

That row originally read _"still unmeasured — peak 68 of 8,192"_. Two things
were wrong with it and both are worth writing down.

**8,192 is not the cap.** It is `context.model_result_preview_bytes`, in bytes,
read in exactly one place — `runtime_worker/mcp_operation_storage.py` — and it
never touches a `read_file` result. The cap that actually bounds a tool result
before the model sees it is `ToolResultAdmissionAdapter.DEFAULT_INLINE_TOKEN_BUDGET`
= **8,000 estimated tokens** at `ceil(len/4)` chars each, i.e. 32,000 characters,
applied by `ToolBudgetGuard.admit_model_visible_result` via
`RuntimeToolControlMiddleware`, wired on the run path in
`runtime_worker/handlers/run.py`. Sizing a fixture against 8,192 _bytes_ rather
than 8,000 _tokens_ is a 4x error — enough to land an intended-inline read on the
wrong side of the threshold and invert what the task measures. `heavy_tasks_ab.py`
now holds `INLINE_TOKEN_BUDGET` with the confusion written on it, and a gate test
reads the literal out of the shipped adapter as text.

**"peak 68" was a number from a task that never ran.** `h6-bigread` needed a
folder grant this host cannot mint, so it recorded `skipped` in both arms; 68 is
the largest result of the five _other_ tasks, and the cap was never approached by
anything. H6 is now rebased on `/memories/` and needs nothing — it writes its own
fixture, which also makes it the one task in the set valid pinned alone
(`HEAVY_TASKS=h6-bigread`).

Measured offline against the shipped adapter and deepagents' real
`format_content_with_line_numbers`, the two reads straddle the cap:

```
after edit 3:  16,113 rendered chars =  4,029 est tokens ( 50% of 8,000) -> INLINE   16,113 chars reach the model
after edit 4:  63,793 rendered chars = 15,949 est tokens (199% of 8,000) -> OFFLOAD   2,233-char stub
```

The inline read carries all eight `part-NN owner=X hours=N` rows, so `HOURS=44`
is answerable from it. The offloaded read returns the header _"Oversized tool
result offloaded before model admission."_, a `/large_tool_results/<sha256>`
reference and a preview clipped to 2,000 characters — which is part of **line
one** — so the same question is not answerable from it. One task, both sides.
63,793 also sits ~20% under deepagents' own 80,000-char read truncation, so the
cap under test is ours and not the library's.

**Two things `outcome_ok` cannot tell you about this task**, both now in its
docstring and both answered by columns instead:

- The agent **authors** the fixture, so it can emit `HOURS=44` from memory
  without either read reaching it. A green H6 is consistent with the big result
  never entering context. `offloaded_results` (new in `rescore.py`) is what says
  the cap fired.
- `SECOND=FULL` has three causes the answer text cannot separate: the staged
  runtime has no admission wiring, the model mis-transcribed an expansion (three
  copies instead of four leaves the file inline at ~20KB with every call
  reporting success), or the model simply answered wrongly. The on-disk memory
  document's **byte size** separates the middle one from the other two, which is
  why `memory_files` now reports sizes.

`rescore.py` needed the offload column for any of this to be visible: an
offloaded result is labelled `agent_runtime.context:offload_stub`, **not**
`agent_runtime.conversation:tool_result`, and `occupancy_shape` filtered on the
latter alone. Shipped as-is, H6's cap-crossing read would have been dropped and
the report would have shown the peak of the remaining small results — a real
number, correctly computed, answering a question nobody asked.

While fixing that, the same function's `peak_tokens = peak_bytes = 0` was
corrected to `None`. A run with no inline tool result reported `0`,
indistinguishable from one whose results were genuinely tiny — the **third**
appearance of the defect these method notes open with, and still visible in
`runs/arm-500.json`, where every task carries a peak of 0.

**Not yet run against a model.** The design is verified offline — `--plan` prints
the straddle for free, and 24 gate tests cover it, each mutation-checked. Per §5's
own rule: re-stage from the tree under test, validate with
`HEAVY_TASKS=h6-bigread` (one task, well under a minute), then pay for the arms.

### What this does NOT establish

**n=1 per cell.** 4/5 correct against 3/5 is one task, one sample, on a small
model whose tool-call behaviour visibly varies run to run — the same
`h5-longchain` made **1** tool call in one arm and **21** in the other. Reading
that as "raising the ceiling hurts correctness" would be exactly the mistake §1
documents: a conclusion drawn from a single arm. The defensible claims are the
mechanical ones — the ceiling was never the stop, the budget was — because those
come from terminal codes and failure rows, not from a difference of one.

`h5-longchain` was designed to span the old ceiling of 25 super-steps and did not
reach it in either arm (peak estimated 34 at limit=25). Either the fit
overestimates, or Haiku's batching collapses the chain the prompt intended to
serialise. `peak_parallel` of 12 favours the second.

### §2's mis-stamped innocent reappeared, from a different cause

```
calls CLOSED BY RECONCILIATION, not by running:
  limit=500  h4-delegate   read_file, read_file
  limit=500  h5-longchain  read_file, read_file
```

Same shape as §2 — a terminal row with an empty `result_summary`, a tool that
looks like it threw and never ran — but the cause here is budget exhaustion, not
a step ceiling. The scorer named it unprompted, which is the second time that
column has found this shape without being told what to look for.
