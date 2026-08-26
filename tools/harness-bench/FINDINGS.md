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

## Method notes — two instrument failures, both worth not repeating

1. The first scorer counted `usage.recorded` events off the events API and
   returned **0 tokens for every task**. A broken instrument reporting zero is
   indistinguishable from a genuinely cheap run. Scoring now reads the run store
   directly, and `rescore.py` is offline, so fixing a measurement never costs
   another paid run.
2. The first scorer's round count was a **lower bound that could not observe the
   failure mode under test** (finding 1). A proxy metric must be checked against
   the thing it proxies before any conclusion rests on it — especially a
   negative conclusion, which is the kind that stops further investigation.

`tool_rounds` still counts COMPLETED tool invocations and is still a lower bound
on super-step spend. It is retained as a rough cost signal only; the ceiling
question is now answered by `terminal_code`.

## 5. The four short prompts cannot reach most of the remaining claims

`recursion_ceiling_ab.py`'s tasks peak at **3 completed tool rounds** (4 distinct
invocations, one of which never ran — see below). That is enough to trip a
ceiling of 25 and nothing else. Rescoring both arms with the extended scorer says
how far short they fall:

```
peak COMPLETED tool rounds in any task: 3      (4 invocations)
peak ESTIMATED super-steps in any task: 22     (fit: 6 + 4/round, ceiling 25)
peak tool result entering context:      122 tokens   (the cap is 8,192)
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
tasks, five of which need no folder grant and no connector, each declaring what
it needs from the machine, what it plans to spend per tool _name_, and a regex
its final answer must match. `--plan` prints all of that for free.

Two design constraints came out of reading the runtime rather than guessing:

- `execution.tool_call_budget` is **10 calls of one tool name per run**. A task
  planning more measures the budget cutting the chain off, which looks identical
  from outside to a ceiling stop. Every task declares `planned_calls` and a gate
  test fails on any plan that reaches the budget.
- A grant-free task must address `/memories/`, not a host-absolute path
  (refused without a grant), and must never ask for `grep`/`glob` there —
  `FileMemoryBackend` answers both with an **empty result, not an error**, which
  is the same green-tick-over-nothing shape as the original `ls ~/Downloads`
  defect.

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
