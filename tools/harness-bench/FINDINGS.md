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
