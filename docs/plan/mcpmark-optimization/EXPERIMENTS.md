# Experiment design — MCPMark harness optimisation

Companion to [PRD.md](PRD.md) and [PLAN.md](PLAN.md). This document is the measurement
protocol: what we run, against what control, with what `n`, and what result changes the
decision.

Predictions in §7 are **pre-registered**. They are written before the runs so that a miss is
a finding rather than something we reinterpret afterwards.

---

## 1. Why naive before/after is wrong here

Three problems make "run the suite, change the code, run it again" produce misleading
numbers on this programme specifically.

### 1.1 The gates make cost and latency look like regressions

Today a run dies at LangGraph step 25 or blocks at MCP call 11. It is **cheap and fast
because it gives up.** Comparing aggregate cost before and after P0 compares a truncated
run to a complete one and reports "cost +70%" as though we made something worse.

**Correction: never compare aggregate cost across a gate change.** Stratify by terminal
outcome and report three numbers — cost per _solved_ task, cost per _attempted_ task, and
the outcome mix itself. Only cost-per-solved is comparable across a gate change, and it
usually _improves_ even as cost-per-attempted doubles.

### 1.2 The easy suite cannot detect the effects we predict

10 tasks × k=4 = 40 runs, and runs cluster hard by task (a task is reliably easy or
reliably hard). With intra-task correlation ≈ 0.5 the design effect is ~2.5, so effective
`n ≈ 16`.

Unpaired two-proportion, 80% power at α=0.05, detecting +15pp around a 40% base rate needs
**~170 effective samples per arm**. The easy suite is off by an order of magnitude.

**Correction: the easy suite is a smoke test for the harness, never an effect measurement.**
Every effect claim uses the full 127. §5 shows this is affordable.

### 1.3 Phase 1 lands four changes at once

The three gates are multiplicative — shipping two of three leaves the score at ~0 — so they
cannot be A/B'd sequentially against a live score.

**Correction: two different mechanisms.** Gates get measured by _instrumented
counterfactual_ (§2), which needs no A/B at all. Everything else is built as a **selectable
component behind a named seam**, so the post-P0 ablation is a variant sweep rather than a
rebuild — each component can be switched back to its control implementation on top of a
working harness.

**This is a design constraint on the implementation, not just on the experiment: if a
change is not selectable at runtime, it is not measurable.** The component architecture and
the full ablation matrix are in [COMPONENTS.md](COMPONENTS.md); this document assumes it.

## 2. The general method: measure the mechanism, not the outcome

Task-level pass/fail yields **1 bit per task-run**, at roughly $0.20–0.50 of API spend. The
mechanism each intervention acts on is observable at far higher frequency:

| Intervention acts on | Observable                      | Events per full sweep |
| -------------------- | ------------------------------- | --------------------- |
| Turn ceiling         | super-steps per run             | 508                   |
| Call budget          | calls per tool name per run     | ~8,800                |
| Error recovery       | error → next-call outcome       | ~1,300                |
| Compaction           | identifier retained at turn t+k | ~4,000                |

Measuring `p_retry` from ~1,300 error events gives a ±0.025 CI. Inferring the same quantity
from 508 pass/fail bits gives roughly ±0.10. **Same runs, 4× the precision, because we
instrumented the mechanism instead of the outcome.**

For the two gate interventions this goes further — it removes the experiment entirely.

### 2.1 Counterfactual instrumentation for gates

Whether a run exceeds 25 super-steps is **not stochastic given the trajectory**. So instead
of A/B-ing limit 25 against limit 60:

1. Set the limit to 200 (effectively unbounded) and log the actual super-step count per run.
2. The empirical CDF gives `P(survive limit L)` for **every candidate L simultaneously**.

One run set yields the entire limit-vs-survival curve, at zero incremental cost, with no
sampling error in the gate term. The shipped limit is then read off the data — pick the
knee — rather than argued for. Identical treatment for the call budget, logging calls per
inner tool name and per dispatcher name in the same pass.

This is why [PLAN.md](PLAN.md) 0.4's baseline run matters more than its score: it is the
instrumented pass that sizes both gates.

## 3. Per-intervention design

Every entry states the unit of analysis, the control, the primary readout, and the decision
rule. `PAIRED` means same task, same initial state, same pinned model, control and treatment
run in the same session (§4.1).

### P0-1 — `recursion_limit`

- **Design:** counterfactual instrumentation (§2.1). No A/B.
- **Unit:** run. **n:** 508 from the Phase 0 baseline sweep.
- **Primary:** ECDF of super-steps per run → `P(survive L)` for L ∈ [25, 200].
- **Secondary:** step count conditional on eventual pass vs fail. If failing runs have a
  much longer tail, the limit is also a useful runaway brake and the knee moves left.
- **Decision:** ship the smallest L with `P(survive L) > 0.98` on the passing distribution.
  If the ECDF shows `P(survive 25) > 0.8`, §4.1 of the PRD is wrong and P0-1 is not a gate.
- **Regression guard:** hermetic `fake_model` run scripted to 30 tool cycles — completes at
  60, fails cleanly and _distinguishably_ at 25. Zero API cost.

### P0-2 — tool budget re-key

- **Design:** counterfactual instrumentation, same sweep.
- **Primary:** two ECDFs from the same runs — calls per _dispatcher_ name (the current
  gate) and calls per _inner_ tool name (the proposed gate). The gap between them is
  exactly the bug's magnitude.
- **Decision:** ship cap = P99 of the inner-name distribution. If the two ECDFs nearly
  coincide, the re-key is cosmetic and only the cap raise matters.
- **Regression guard:** hermetic — 12 calls across distinct MCP tools all admitted; 11 to
  one tool rejected; rejection message names the real tool, not `call_mcp_tool`.

### P0-3 — unattended profile

- **Design:** deterministic count, not an experiment. Count runs raising an approval
  interrupt in the baseline sweep; expected ≈100% of write-bearing tasks.
- **The real risk is not accuracy, it is a silent-skip failure.** A profile that drops
  writes instead of executing them scores _worse_ while looking like it works.
- **Primary:** interrupt count → 0. **Guard:** write-effect count unchanged vs an
  approve-everything manual control on 5 tasks.
- **Security test (blocking, not statistical):** interactive-posture request carrying the
  unattended flag is refused; `destructive` still interrupts. This gates the PR regardless
  of any score.

### P1-1 — real MCP error text

The one intervention whose central claim genuinely needs an A/B, and the one with the most
statistical power available.

- **Design:** `PAIRED`, event-level. Full suite, both arms, same session.
- **Unit:** **error event**, not task. **n ≈ 1,300** per arm.
- **Primary:** `p_retry` = P(next call on the same target succeeds | previous call errored).
- **Secondary — the mechanism readout:** classify the model's next action into
  `same-args retry` (blind) / `modified-args retry` (informed) / `abandon`. The PRD's
  correlated-failure argument predicts the canned-error arm is dominated by _same-args
  retry_. If it isn't, the causal story is wrong even if `p_retry` moves.
- **Tertiary:** task-level `pass@1` and `pass^4`, paired, McNemar (§4.2).
- **Decision:** PRD §3.3 assumes a ~3.75× gap (0.20 → 0.75). **If the measured gap is
  < 2×, error quality is not the stability lever, PRD §3.3 is falsified, and Phase 2's
  ordering is re-derived before anything else ships.**

### P1-2 — per-tool descriptor degradation

- **Design:** deterministic, `n=5`. Not a statistical question.
- **Primary:** per server — does the load succeed, and how many tools survive?
- **Decision:** this is bimodal by nature. Effect is 0 if no real server trips the limit and
  large if GitHub does. **Check this before building it** — one `list_tools` against each of
  the five servers answers it in minutes.

### P1-3 — parallel tool calls

- **Pre-check that can cancel the work:** measure the fraction of assistant turns emitting
  > 1 tool call in the baseline sweep. If ≈0, the model isn't batching and the intervention
  > cannot help — skip it.
- **Design:** `PAIRED`, task-level, latency primary.
- **Primary:** wall-clock per task, paired difference (Wilcoxon signed-rank — latency is
  right-skewed, so don't use a t-test).
- **Guard:** accuracy unchanged. Parallel writes against shared state can introduce
  ordering bugs; check `pass@1` does not drop.

### P2-1 — result-scoped compaction

The highest-risk intervention, so it gets a mechanism guard that fires **before** any score
regression appears.

- **Primary guard:** identifier retention — for each identifier created at turn `t`, is it
  exactly recoverable from context at turn `t+k`? Instrumented directly, ~4,000 events.
  This is the failure mode that makes compaction lose tasks, and it is visible without
  running a single verification script.
- **Design:** `PAIRED`, task-level, at 2–3 compaction thresholds (a dose-response curve, not
  a single on/off).
- **Primary:** cost per solved task. **Guard:** `pass@1` non-inferiority — ship only if the
  paired lower CI bound on Δaccuracy is > −3pp.
- **Decision:** pick the threshold at the knee of cost-vs-accuracy, not the cheapest point.

### P2-2 — result field projection

Same shape as P2-1, but strictly riskier (drops data before the model sees it). **Do not
run this experiment until P2-1 has landed and been measured** — otherwise the two
interact and neither is attributable.

### P3-1 — adaptive tool exposure

- **Cost needs no experiment.** It is analytically determined by the PRD §3.1 formula once
  `T` per server is measured. Compute it; don't spend a sweep on it.
- **Accuracy design:** `PAIRED`, **stratified by server** — the entire hypothesis is that
  the effect differs by server size, so a pooled number would average away the finding.
- **Primary:** Δ`pass@1` for small-schema servers (Postgres, Filesystem) vs large (GitHub).
- **Decision:** predicted sign is _positive for small, negative-or-flat for large_. If the
  effect is uniform across strata, the threshold design is unnecessary and the simpler
  answer (pick one mode) wins.

## 4. Statistical protocol

### 4.1 Pairing and confound control

Pairing removes task-difficulty variance, which dominates everything else here.

- **Same task, same initial state.** MCPMark resets state per task; **verify the reset is
  complete** before trusting pairing, particularly on Notion and GitHub where residue is
  plausible.
- **Pin the exact model ID** and record it. A provider-side model update between a week-1
  baseline and a week-4 treatment silently confounds everything.
- **Run control and treatment in the same session, interleaved**, not sequentially. This is
  the single cheapest defence against provider drift, and it costs nothing but scheduling.
- **Randomise task order** within each arm to break ordering effects from any state leakage
  that survives the reset.

### 4.2 Tests

| Readout                | Test                                              | Why                                                           |
| ---------------------- | ------------------------------------------------- | ------------------------------------------------------------- |
| Paired accuracy (task) | **McNemar** on discordant pairs                   | Only flips carry information; ~7 net flips reach significance |
| Unpaired accuracy      | Two-proportion z, with design effect ≈2.5 applied | Clustering by task inflates variance                          |
| `p_retry` (event)      | Two-proportion z, n≈1,300                         | High-n, near-independent events                               |
| Latency                | **Wilcoxon signed-rank**                          | Right-skewed; the mean is not the estimand                    |
| Cost                   | Paired, stratified by terminal outcome            | §1.1                                                          |

At 127 tasks, a +15pp effect flips ~19 tasks — comfortably detectable paired. At 10 tasks
it flips 1.5. That is the whole argument for §1.2.

### 4.3 Reporting

Every sweep writes a `RESULTS.md` carrying **pre-registered prediction, measured value, and
whether the model was wrong** — with the miss stated plainly rather than reframed. The
point of §7 is to make rationalising after the fact difficult.

Report cache-adjusted cost **and** raw token counts. Cached input is ~0.1× price, so a
change that shifts cache hit rate moves cost 10× more than it moves tokens; reporting only
one of the two hides that.

## 5. What this costs

Per full-suite arm: 127 tasks × k=4 = 508 runs ≈ 137M raw input tokens, ≈26M cache-adjusted.
Output depends entirely on reasoning effort — at `xhigh` it is ~23M tokens, not the ~1.3M a
low-effort run would produce, and it is **84% of the bill**.

**Basis: `gpt-5.6-luna` @ `xhigh`** ($0.20/$1.20 per M; see PRD §3.1a). ≈ **$35 per
arm-sweep**, range $17–48 depending on the reasoning-token rate.

| Sweep                              | Arms | Est. cost |
| ---------------------------------- | ---- | --------- |
| Phase 0 baseline + instrumentation | 1    | ~$35      |
| P1-1 A/B                           | 2    | ~$70      |
| Post-P0 gate ablation              | 3    | ~$105     |
| P2-1 dose-response                 | 3    | ~$105     |
| **Total through Phase 2**          |      | **~$315** |

Roughly **6× cheaper than the frontier-pricing figure this table previously carried**
(~$1.8k). Two caveats that push the other way: failing runs cost _more_ than passing ones
because they run to the turn ceiling, and the reasoning-token rate is the least-measured
input in the estimate. Take the first Phase 0 sweep as the calibration and re-derive.

**Drop P2-1's dose-response if its cost case does not survive PRD §3.1a** — on this pricing
basis compaction buys −6–8%, not −35–50%, and three arms is a poor use of budget to measure
a lever that no longer matters.

**The measurement programme is cheap relative to the engineering.** That is the main reason
§1.2's conclusion — always use the full suite — is affordable advice rather than
aspirational. Running the easy suite to save money is a false economy: it costs ~$15 and
buys a number that cannot support any claim.

## 6. Order of operations

```
Phase 0 sweep (limit=200, budget=off, instrumented)
   ├── ECDF: super-steps      ──▶ sizes P0-1, no A/B needed
   ├── ECDF: calls/tool-name  ──▶ sizes P0-2, no A/B needed
   ├── interrupt count        ──▶ confirms P0-3
   ├── parallel-call fraction ──▶ go/no-go on P1-3
   └── S, T, m occupancy      ──▶ replaces PRD §3.4 assumptions

   ▼
Land P0 + P1-1  ──▶  P1-1 paired A/B (the one real experiment)
   ▼
Gate ablation (config sweep, 3 arms)  ──▶  attributes Phase 1 per-change
   ▼
P2-1 dose-response
```

One instrumented baseline sweep answers five separate questions and cancels three planned
experiments. **It is the highest-value run in the programme and it happens before any
optimisation ships.**

## 7. Pre-registered predictions

Recorded before the runs. Each has a falsifier that changes the plan.

| #   | Prediction                                       | Falsified if            | Consequence                                                                  |
| --- | ------------------------------------------------ | ----------------------- | ---------------------------------------------------------------------------- |
| 1   | `P(survive 25 steps)` ≈ 0.30                     | > 0.80                  | PRD §4.1 wrong; P0-1 is not a gate                                           |
| 2   | Dispatcher-keyed calls/run median > 15           | < 11                    | PRD §4.2 wrong; budget never binds                                           |
| 3   | Approval interrupts on > 90% of tasks            | < 50%                   | PRD §4.3 overstated                                                          |
| 4   | `p_retry` 0.20 → 0.75 (≥3× gap)                  | gap < 2×                | **PRD §3.3 falsified; re-derive Phase 2 order**                              |
| 5   | Canned-error arm dominated by same-args retry    | modified-args dominates | Correlated-failure story wrong; §3.3 mechanism wrong even if `p_retry` moves |
| 6   | Post-P1 `pass@1` (full) 35–50%                   | < 20%                   | **Bottleneck was never the harness; stop and re-scope**                      |
| 7   | Compaction costs < 3pp accuracy                  | > 5pp                   | Ship no compaction; find cost elsewhere                                      |
| 8   | Native exposure helps small servers, hurts large | uniform across strata   | Threshold design unnecessary; pick one mode                                  |

Prediction 6 is the one that ends the programme rather than adjusting it, and it is checked
at the Phase 1 gate — before Phase 2 spends anything.
