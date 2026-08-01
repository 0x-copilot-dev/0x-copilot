# Component architecture and ablation protocol

Companion to [PRD.md](PRD.md), [PLAN.md](PLAN.md), [EXPERIMENTS.md](EXPERIMENTS.md).

Two questions answered together, because they have the same answer: **how do we ablate each
change**, and **how do we build them so ablation is possible at all**. An ablation is only as
honest as the seam it toggles, so the component design _is_ the experiment design.

---

## 1. The rule

> **If a change is not selectable at runtime, it is not measurable.**

A change that ships as "the code after the PR" cannot be the treatment arm of a controlled
experiment, because its control arm is a different binary at a different commit. That breaks
the drift protection in [EXPERIMENTS.md](EXPERIMENTS.md) §4.1 — control and treatment must
run **interleaved in one process against one pinned model**, or provider drift confounds
everything.

So every intervention is built as a **strategy behind a named seam, with at least two
implementations, one of which reproduces today's behaviour exactly.**

The control arm is a real, tested, shipped component — `CannedErrorPresenter`,
`DispatcherKeyedBudget`, `NullCompaction`. Not an absence. This costs a little more to build
and is the difference between an ablation and an anecdote.

## 2. Plugging into what exists

`agent_runtime.harness_quality` already carries the substrate; we should not invent a second
one:

- **`HarnessVariant`** — `variant_id`, four revision axes (`prompt_plan`,
  `capability_policy`, `context_policy`, `model_route`), `feature_flags: frozenset[str]`, and
  a `digest` over canonical JSON.
- **`HarnessManifest`** — signed (ed25519) envelope of `HarnessManifestAssignment`s, each
  with `variant_ref`, `variant_digest`, `allocation_basis_points`.
- **`EvaluationRepositoryPort`** — `put_harness_manifest`,
  `compare_and_set_active_harness_manifest`, plus `PromotionThresholds` / `PromotionDecision`.

Our eleven components map onto the existing revision axes rather than adding new ones:

| Axis                         | Components it versions                                                 |
| ---------------------------- | ---------------------------------------------------------------------- |
| `capability_policy_revision` | tool exposure, budget keying, descriptor admission, error presentation |
| `context_policy_revision`    | compaction, result projection                                          |
| `prompt_plan_revision`       | phase-structured prompt + time-budget warning (P2.5d)                  |
| `model_route_revision`       | pinned model — held constant across all arms                           |

**An arm is a `HarnessVariant`. An ablation is a set of variants. A sweep enumerates them.**

Two adaptations needed:

1. **Deterministic pinning, not allocation.** `allocation_basis_points` splits live traffic;
   a benchmark arm must be pinned, so the runner selects a variant by ref rather than
   sampling one. Add pinned selection alongside allocation — do not repurpose allocation.
2. **Digest recorded on every run.** Each run record carries `variant_digest`, so results
   are attributable **post-hoc from the run itself** rather than from the runner's
   bookkeeping. If a sweep is misconfigured, the digests reveal it instead of silently
   mislabelling an arm.

## 3. The seams

Each is a `Protocol` in the repo's existing dependency-inversion idiom, resolved once at
runtime composition.

| #   | Seam                        | Control impl (today)    | Treatment impl                               | PRD ref |
| --- | --------------------------- | ----------------------- | -------------------------------------------- | ------- |
| C1  | `TurnCeilingPolicy`         | `LangGraphDefault` (25) | `ConfiguredCeiling(n)`                       | P0-1    |
| C2  | `BudgetKeyStrategy`         | `DispatcherKeyed`       | `InnerToolKeyed`                             | P0-2    |
| C3  | `ToolErrorPresenter`        | `CannedErrorPresenter`  | `PassthroughPresenter`                       | P1-1    |
| C4  | `DescriptorAdmissionPolicy` | `AllOrNothing`          | `PerToolDegrade`                             | P1-2    |
| C5  | `ContextCompactionPolicy`   | `NullCompaction`        | `ResultScopedCompaction(t)`                  | P2-1    |
| C6  | `ResultProjectionPolicy`    | `NullProjection`        | `FieldProjection`                            | P2-2    |
| C7  | `ToolExposureStrategy`      | `UmbrellaDispatcher`    | `NativePassthrough` / `AdaptiveThreshold(b)` | P3-1    |
| C8  | `TruncationNotice`          | `SilentSlice`           | `MarkedTruncation`                           | P1-4    |
| C9  | `RunContextPrimer`          | `NullPrimer`            | `McpInventoryPrimer(bytes)`                  | P2.5a   |
| C10 | `RepeatCallPolicy`          | `NullPolicy`            | `NudgeAfterN(n)`                             | P2.5b   |
| C11 | `PreCompletionPolicy`       | `NullPolicy`            | `StateReReadChecklist`                       | P2.5c   |

C8–C11 are the [RESEARCH.md](RESEARCH.md) §2 ports of LangChain's middleware set. They are
listed as seams rather than as a shipped block on purpose: HARBOR's rounds C and D were
exactly this kind of stack and cost 9 passes between them, and its self-evaluation gate is
C11's nearest relative. **The default for each is the control implementation.**

Two of them carry parameters that matter more than their on/off state — `NudgeAfterN(n)` and
`McpInventoryPrimer(bytes)` — which is HARBOR's central finding: its only clean win came
largely from moving two thresholds (0.30→0.85, 0.50→0.80), not from adding features. Treat
those numbers as the thing under test, not the component.

C1–C4 and C6 are **pure functions** — value resolution, a key derivation, an error→text map,
a validation fold, a payload transform. They are trivially pluggable and trivially testable
hermetically with `execution/fake_model.py`, at zero API cost.

C5 and C7 are **structural but still pluggable**: C5 is middleware with a null
implementation; C7 recomposes the model tool surface. Both are swappable, with the caveat in
§6.2.

## 4. What is deliberately _not_ pluggable

The user's framing is the right one, and the important half is knowing which components must
refuse to be plugs.

### N1 — `ApprovalPolicyProfile` (P0-3) is posture-scoped, not variant-scoped

A runtime-selectable "skip approvals" strategy is precisely the dual-path pattern that leaks.
If `write=auto` is reachable by selecting a variant, then anything that can influence variant
selection can disable the approval boundary — and variant refs would become an untrusted
input on the security path.

**Design:** resolved from **deployment posture** (`BACKEND_ENVIRONMENT` + an explicit
unattended profile), never from `HarnessVariant`. A production-posture process has no code
path that reaches `write=auto`; the benchmark deployment does. `destructive` stays `require`
in both.

**Ablation consequence:** P0-3 cannot be leave-one-out ablated in the same binary as the
other arms. It is measured differently — see §5.4. **This is a real cost of getting the
security design right, and it is the correct trade.**

### N2 — `ToolCallConcurrency` (P1-3) is only half ours

We control whether parallel tool calls are _permitted_; the model decides whether to _emit_
them. Toggling our half measures a quantity confounded by model behaviour, so it is reported
as a latency observation, not a clean ablation arm.

## 5. Ablation protocol

### 5.1 Why leave-one-out, not add-one-in

Full factorial over 11 pluggable components is 2¹¹ = 2,048 arms. Two reduced designs are
available and they answer different questions:

- **Add-one-in (AOI)** — baseline + one component. Measures **standalone** effect.
- **Leave-one-out (LOO)** — everything on, minus one component. Measures **marginal
  contribution given everything else** — i.e. "can we drop this?"

For this programme the two diverge sharply, and predictably. The P0 gates are
**multiplicative**: with the other two gates shut, opening one changes nothing, so AOI reads
~0 for each. With everything open, closing one shuts the run down again, so LOO reads the
full effect for each.

**LOO is primary.** But the _asymmetry itself is the diagnostic_: running AOI on the P0 trio
as well — 3 extra arms — is what **demonstrates** the multiplicative structure the PRD
asserts, instead of assuming it. If AOI shows a large standalone effect for any gate, the
gates were not multiplicative and PRD §3.3's `G` term is wrong.

### 5.2 Offline ablation — the arms we don't have to run

Several components' effects are computable from a **recorded trajectory** without re-invoking
the model, because they gate or transform something already observed:

| Component                 | Offline?    | Method                                                             |
| ------------------------- | ----------- | ------------------------------------------------------------------ |
| C1 turn ceiling           | **Yes**     | replay step counts against every candidate L → full survival curve |
| C2 budget key             | **Yes**     | replay call counts under both key strategies × every cap           |
| C4 descriptor admission   | **Yes**     | deterministic, `n=5` servers, one `list_tools` each                |
| C6 projection (cost only) | **Partial** | token savings computable; accuracy needs live                      |
| C3 error presenter        | **No**      | changes the model's next action                                    |
| C5 compaction             | **No**      | changes what the model sees                                        |
| C7 exposure               | **No**      | changes the tool surface                                           |

C1 and C2 do not need ablation _arms_ at all — one instrumented sweep yields their entire
response surface at zero incremental cost and with no sampling error, since the gate is
deterministic given the trajectory. This is the same counterfactual method as
[EXPERIMENTS.md](EXPERIMENTS.md) §2.1, applied as ablation.

**Live arms required: 5** (C3, C5, C6, C7, plus all-on control) — not 8.

### 5.3 The live arm matrix

| Arm  | C3 error   | C5 compact | C6 project | C7 exposure  | Purpose                      |
| ---- | ---------- | ---------- | ---------- | ------------ | ---------------------------- |
| `A0` | pass       | on         | on         | adaptive     | all-on control               |
| `A1` | **canned** | on         | on         | adaptive     | LOO C3 — the `p_retry` claim |
| `A2` | pass       | **null**   | on         | adaptive     | LOO C5                       |
| `A3` | pass       | on         | **null**   | adaptive     | LOO C6                       |
| `A4` | pass       | on         | on         | **umbrella** | LOO C7, stratified by server |
| `B1` | **canned** | null       | null       | umbrella     | AOI baseline for the P0 demo |

`A1` doubles as the P1-1 paired A/B in [EXPERIMENTS.md](EXPERIMENTS.md) §3 — **the same two
arms serve both the hypothesis test and the ablation**, so it is run once, not twice.

#### The C8–C11 middleware arms are add-one-in, not leave-one-out

The LOO design above answers "can we drop this?" for components we intend to ship. For the
LangChain middleware ports the question is the opposite — **"is this worth adopting at all?"**
— and HARBOR's evidence says the prior should be _no_ for most of them. So they are measured
**add-one-in against the post-P1 harness**, each alone:

| Arm  | Added to the post-P1 baseline | Purpose                                     |
| ---- | ----------------------------- | ------------------------------------------- |
| `M0` | nothing                       | post-P1 control                             |
| `M1` | C8 marked truncation          | cheapest; NVIDIA measured 0/3 → 3/3 on this |
| `M2` | C9 MCP inventory primer       | turn reduction vs resident-token cost       |
| `M3` | C10 repeat-call nudge         | **run after P1-1**, else confounded         |
| `M4` | C11 pre-completion checklist  | highest upside, HARBOR's failure shape      |

AOI is also the honest design here for a second reason: these four are **not multiplicative
the way the P0 gates are**, so unlike §5.1 there is no reason to expect AOI to read ~0. If it
does, that is the answer.

`M4` gets a **pre-registered stopping rule**: HARBOR's equivalent gate turned passing answers
into failures, so if `M4` is negative on the first arm it is dropped, not tuned. Only a
positive first arm earns a threshold sweep.

**Adopt the union of the positive arms, then re-run `A0` with that union on.** Individually
positive components can still be jointly negative — which is the whole content of HARBOR's
rounds C and D — so the union needs its own confirmation before it ships.

### 5.4 Ablating the non-pluggable ones

- **N1 approval (P0-3):** measured as a **deterministic count**, not an arm — approval
  interrupts raised per run, which is ~100% of write-bearing tasks in the baseline and must
  be 0 under the unattended profile. Its accuracy contribution is then _inferred_ rather than
  ablated, and the doc should say so plainly: **P0-3's +25–40pp is the least
  experimentally-supported number in the PRD.** Its supporting evidence is a mechanism proof
  (runs suspend and never terminate), not a controlled comparison. That is acceptable because
  the mechanism is unambiguous, but it should not be presented as if it were measured.
- **N2 concurrency (P1-3):** reported as a latency observation with the batching-fraction
  pre-check from [EXPERIMENTS.md](EXPERIMENTS.md) §3.

### 5.5 Interactions worth one targeted cell

LOO measures marginal contribution but hides interactions. One is predicted strongly enough
to test: **C3 × C1** — better errors mean fewer wasted retry turns, so the turn ceiling
should bind _less often_. Because C1's effect is measured offline from step counts, this
costs nothing extra: compare the step-count distribution between `A0` and `A1`.

A negative result here is informative. If passthrough errors do _not_ reduce step counts,
then the −15–25% cost estimate for P1-1 (PRD §5.2) is wrong even if its accuracy estimate
holds — and P1-1 stops being the change that funds P0.

## 6. Constraints the component design imposes

### 6.1 Composition order must be declared

C5 (compaction) and C6 (projection) both transform the same tool-result data, and they do not
commute: projecting then compacting discards different information than compacting then
projecting. The pipeline declares a fixed order, and **`A3` is only interpretable with that
order held constant**. This is the concrete reason [PLAN.md](PLAN.md) defers P2-2 until P2-1
has landed and been measured.

### 6.2 Swapping C7 invalidates the prompt cache

Changing the tool surface changes the cached prefix, so `A4`'s cost is not comparable to
`A0`'s on a per-token basis without accounting for hit-rate change. Report `A4` cost with
cache-hit rate alongside, and compare cache-adjusted cost — otherwise a real
architectural win looks like a regression, or vice versa.

### 6.3 Every seam needs a hermetic test at both settings

Each component ships with a `fake_model` test asserting **both** implementations behave as
specified. This is what keeps the control arm honest over time: a control implementation with
no test silently rots into something that is no longer "today's behaviour", and every later
ablation measures against a moving baseline without anyone noticing.

## 7. What this buys

- **Attribution.** Every run carries a `variant_digest`; results are attributable from the
  data, not from a spreadsheet of what we believe we ran.
- **Cheap ablation.** 5 live arms instead of 128, because 3 components are measurable offline
  and full factorial is unnecessary.
- **Reversibility.** Shipping a component is selecting a default, not deleting the
  alternative. A regression found in production is a variant flip, not a revert.
- **Reuse.** This is the substrate `harness_quality` was built for. The MCPMark programme is
  its first real consumer; the seams outlive the benchmark.

## 8. Open question this raises

Building 7 seams with 2 implementations each is meaningfully more work than 7 direct changes —
roughly +40% on Phase 1–2 engineering. The payoff is that every number in
[PRD.md](PRD.md) §5 becomes measured rather than modelled, and stays measurable as the
codebase moves.

**That trade is worth taking for C2, C3, C5 and C7** — the components where the PRD's estimate
is uncertain and the alternative is worth keeping. It is probably _not_ worth it for C1, where
the "control" is a bug (a limit of 25 that nobody chose) and the treatment is a settable
value with an offline-derived answer. **Recommend: seam C1 as a plain setting, not a strategy
protocol.** Full seam treatment for the rest.
