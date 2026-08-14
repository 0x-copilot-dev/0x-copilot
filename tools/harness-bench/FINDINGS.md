# harness-bench — what the measurements actually say

Every number here came from the packaged app running against a real model, scored
from the file-native run store (`run_usage.jsonl`, `tool_invocations.jsonl`,
`context_occupancy.jsonl`) — the same records the product bills from.

## 1. The step-ceiling raise bought nothing (FALSIFIED)

`recursion_limit` was raised from LangGraph's inherited **25** super-steps to an
explicit 500. Measured on a 4-task set:

| task           | status     | completed tool rounds |
| -------------- | ---------- | --------------------- |
| t1-trivial     | completed  | 0                     |
| t2-three-steps | completed  | 0                     |
| t3-todo-driven | **failed** | 3                     |
| t4-long-chain  | completed  | 1                     |

**Peak spend: 3 rounds against a ceiling of 25.** The mechanism cannot bind on
work like this, so raising it changes no outcome. Arm B (limit=500) was
deliberately NOT run: a ceiling never approached cannot behave differently when
raised, and running it would spend real credits to confirm a foregone
conclusion.

Caveat, stated rather than buried: four short prompts is a thin task set. Heavy
agentic work — multi-file edits, connector chains — would spend far more rounds
and could change this. The claim is falsified _for this task set_, and no
completion-rate win may be claimed until a heavier one says otherwise.

## 2. A real bug, found on the harness's first run

`t3-todo-driven` did not fail on any limit. `write_todos` threw
`tool_exception` on its **4th** call and killed the task. Invisible to 9,892
unit tests; caught in 28 seconds of live running.

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

**The cold run alone is 71% of the total cost of all four.** So the fixed ~23k
prompt is nearly free when warm and full price on every cold start.

**The decision this forces:** prompt-trimming pays off strictly in proportion to
cold-start frequency. For a continuously-used session it is close to worthless;
for a bursty desktop user — open the app, ask one thing, close it — nearly every
run is a cold start and the 23k is paid in full each time. Trimming is therefore
worth doing, but sized against that, not against the raw token count. Anything
that shrinks the _cold_ prompt is the lever; anything that only helps warm runs
is not.

Unmeasured and worth measuring next: what fraction of real runs actually hit a
cold cache.

## Method notes, including a mistake worth not repeating

The first scorer counted `usage.recorded` events off the events API and returned
**0 tokens for every task** — the matcher was wrong, and a broken instrument
reporting zero is indistinguishable from a genuinely cheap run. Scoring now
reads the run store directly, and `rescore.py` is offline, so fixing a
measurement never costs another paid run.

`tool_rounds` counts COMPLETED tool invocations. That is a lower bound on
super-step spend, not the spend itself — a turn spends several graph steps
without calling a tool. It is the honest proxy the store affords, and it is the
right shape for the ceiling question: a lower bound nowhere near 25 settles it.
