# PRD — Optimising the agent harness for MCPMark

Status: **draft, not approved**
Owner: ai-backend / agent_runtime
Target surface: `services/ai-backend` (`agent_runtime.execution`, `agent_runtime.capabilities.mcp`)

---

## 1. Why

[MCPMark](https://github.com/eval-sys/mcpmark) is 127 CRUD-heavy tasks across five live
MCP servers (Notion, GitHub, Filesystem, Postgres, Playwright). Each task starts from a
curated initial state and is scored by a `verify.py` that inspects the **final environment
state**, not the transcript. Tasks average **16.2 execution turns and 17.4 tool calls**.
The headline metrics are `pass@1` and `pass^4` (all four runs pass) — the second isolates
_stability_, i.e. error recovery, not capability.

We care about MCPMark for two reasons, and only the second one justifies the work:

1. It is a public number.
2. **It is the only external instrument we have that measures the thing our product
   actually is** — a long-horizon, stateful, multi-connector tool loop. Every failure it
   exposes is a failure a real user hits on a real connector.

This PRD treats (2) as the goal. Where a change would move the score without helping a
user, it is marked `BENCH-ONLY` and scheduled last.

## 2. Metrics

We optimise three quantities, per task:

| Metric       | Definition                                           | Instrument                                              |
| ------------ | ---------------------------------------------------- | ------------------------------------------------------- |
| **Cost**     | total input + output tokens, cache-adjusted          | `context_occupancy` recorder + `model_invocation_usage` |
| **Latency**  | wall-clock, task start → terminal event              | run event `sequence_no` timestamps                      |
| **Accuracy** | `pass@1` and `pass^4` from MCPMark's own `verify.py` | MCPMark runner                                          |

These trade off against each other, and **the trade is not symmetric** (§4.5). The
headline decision this PRD asks for is which point on that curve we want.

## 3. Performance model (first principles)

Everything below is derived from this model. It is stated explicitly so the estimates in
§5 can be checked, argued with, and — once we baseline — corrected.

### 3.1 Cost

Let `N` = model turns, `S` = system-prompt tokens, `T` = resident tool-schema tokens,
`m` = tokens appended to history per turn (assistant message + tool result), `c` = completion
tokens per turn.

Each turn re-sends the entire prefix. So:

```
input_tokens  = Σ(t=1..N) [ S + T + (t-1)·m ]  =  N·(S+T)  +  m·N(N-1)/2
output_tokens = N·c
```

**The history term is quadratic in `N`.** That single fact orders every intervention in
this document.

With provider caching (already implemented, framework-owned) and a breakpoint per turn,
effective input ≈ `uncached × (1 − 0.9h)` where `h` is prefix hit rate. Caching changes the
_constant_, not the _exponent_ — a quadratic that is 10× cheaper is still quadratic.

**Reference task** (parameters in §3.4): `N=17, S=3000, T=3200, m=1200, c=150`

```
prefix term   17 × 6200          = 105,400   (39%)
history term  1200 × 17·16/2     = 163,200   (61%)
input total                      = 268,600
output total  17 × 150           =   2,550
```

### 3.1a Money, once a model is chosen — and why the choice reorders §5

Token counts above are model-independent. Money is not, and **the chosen model changes which
optimisations are worth building**, so the pricing basis is stated here rather than assumed.

**Basis: `gpt-5.6-luna` at reasoning effort `xhigh`.** List $0.20/M input, $1.20/M output
(the 2026-07-30 cut from $1/$6). Cached input assumed at the GPT-5-series 90%-off rate
(**$0.02/M — unconfirmed, verify before relying on it**). Reasoning tokens bill at the output
rate and, with `include_encrypted_content=False`
([contracts.py:173](../../../services/ai-backend/src/agent_runtime/execution/contracts.py:173)),
are **not** carried into later turns — so they inflate output only, never `m`.

At `xhigh`, `c = 150` visible + 1,000–4,000 reasoning tokens per turn. Per reference task:

|                           | tokens  | cost                              |
| ------------------------- | ------- | --------------------------------- |
| input, fresh (h=0.9)      | 26,860  | $0.0054                           |
| input, cached             | 241,740 | $0.0048                           |
| output @ 2,650/turn (mid) | 45,050  | **$0.0541**                       |
| **total**                 |         | **≈ $0.064** (range $0.034–0.095) |

**Output is ~84% of spend.** That inverts the §3.1 conclusion in one specific way: cost is
now roughly **linear** in `N` (each turn adds a fixed reasoning block) rather than quadratic,
because the quadratic term lives entirely in the 16% input share.

| Lever            | Elasticity, frontier pricing | Elasticity, Luna + `xhigh` |
| ---------------- | ---------------------------- | -------------------------- |
| Turns `N`        | 1.65                         | **1.10**                   |
| Reasoning effort | n/a (was negligible)         | **~0.84**                  |
| History size `m` | 0.61                         | **0.10**                   |

> **Superseded by [RESEARCH.md](RESEARCH.md) §4.** The demotion below reasons about
> compaction as a _cost_ lever. Published results on the closest comparable setup — long-horizon
> agents over **MCP tools** — show it is an **accuracy** lever: pruning to the last 5 tool
> call/response pairs took completion 71.0% → 79.0% while cutting tokens 64%, and adding
> summarisation reached 91.6%. P2-1's revised estimate is **accuracy +5 to +15pp**, and it is
> promoted to a Phase 1 candidate. The paragraph below is kept because its arithmetic about the
> _cost_ share is still correct — it was the framing that was wrong.

**Consequence for §5: P2-1 (compaction) is demoted.** It attacks the input history term —
61% of input, but input is only 16% of spend, so its modelled "−35–50% cost" becomes
**−6–8%** on this basis. It is no longer worth its accuracy risk. Meanwhile **reasoning
effort becomes the dominant cost control, and it is a knob, not a change we build.**

Turn reduction stays the top _engineering_ lever, so P1-1 keeps its ranking. Nothing else in
§5's ordering survives the model switch unexamined — re-derive the cost column against
measured `S`, `T`, `m` and a measured reasoning-token rate before committing to Phase 2.

### 3.1b Sensitivities (frontier-pricing basis)

Sensitivities at that point (`∂cost/∂x · x/cost`):

| Lever             | Elasticity | A 20% improvement in this lever buys |
| ----------------- | ---------- | ------------------------------------ |
| Turns `N`         | **1.65**   | **−30% cost**                        |
| Result size `m`   | 0.61       | −12% cost                            |
| Tool schema `T`   | 0.20       | −4% cost                             |
| System prompt `S` | 0.19       | −4% cost                             |

**Conclusion: turns dominate.** Trimming tool-schema bytes is the intervention that feels
most like optimisation and is worth the least. Anything that removes a round-trip is worth
~8× the same percentage saved on schema text.

### 3.2 Latency

```
L = Σ(t=1..N) [ TTFT(P_t) + c/rate + tool_rtt ]
```

`TTFT` grows with prefill size, but cached prefill runs ~10–20× faster, so with caching
working, `L` is close to linear in `N` (elasticity ≈ 1.0–1.3, the excess coming from
uncached-tail prefill growth).

`tool_rtt` for us = MCP server time + **our proxy hop**. `ai-backend` never speaks MCP
directly; it proxies every call over HTTP through `backend`'s `/internal/v1`
([backend_provider.py](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py)).
That is correct product architecture and costs ~1 extra RTT + serialisation per tool call.

Reference: `17 × (2.0s model + 0.6s tool) ≈ 44s` for a clean task; 2–4× that once retries
enter.

Latency has **one lever the other two metrics don't share**: parallelism. Independent
tool calls within a turn cost one `tool_rtt`, not two.

### 3.3 Accuracy

```
P(pass@1) = G × A
```

**`G` — gate survival.** The product of "did the run get to finish at all" probabilities.
These are hard limits, not soft ones; each is a step function.

**`A` — intrinsic solve probability.** For a task requiring `k` state-changing operations,
with first-try per-op success `p` and post-error retry success `p_retry`:

```
A = [ p + (1−p)·p_retry ]^k
```

`p_retry` is the whole ballgame, and it is **a property of the harness, not the model**.
Retry failures are _correlated_: a model that emitted a malformed `parent.page_id` will
emit the same malformed ID again unless the error tells it what was wrong. So:

- opaque error (`"The MCP server reported an error for this tool call."`) → `p_retry ≈ 0.20`
- informative error (`"body.parent.page_id should be a valid uuid"`) → `p_retry ≈ 0.75`

At `k=8, p=0.85`:

|                    | `A` = pass@1         | `A⁴` = pass^4 |
| ------------------ | -------------------- | ------------- |
| opaque errors      | `0.88^8` = **36%**   | **1.7%**      |
| informative errors | `0.9625^8` = **74%** | **29.5%**     |

This model reproduces the published shape of the benchmark (best model 52.6% pass@1 →
33.9% pass^4; others <30% → <15%) without being fitted to it. That is the main reason to
trust it enough to act on.

**The `pass@1` → `pass^4` collapse is an error-message-quality problem.** Not a model
problem, not a prompting problem.

### 3.4 Assumed parameters and their confidence

| Param                      | Value                    | Confidence | How we replace the assumption          |
| -------------------------- | ------------------------ | ---------- | -------------------------------------- |
| `N` = 17                   | MCPMark published mean   | High       | —                                      |
| `k` = 8 state-changing ops | inferred from 17.4 calls | Medium     | count writes in transcripts            |
| `S` = 3,000                | unmeasured               | **Low**    | `context_occupancy` per-segment report |
| `T` = 3,200                | unmeasured               | **Low**    | same, `ModelToolOwner` breakdown       |
| `m` = 1,200                | unmeasured               | **Low**    | same                                   |
| `p` = 0.85                 | judgement                | Low        | per-op success from transcripts        |
| `p_retry` 0.20 / 0.75      | judgement                | Medium     | A/B, §6                                |

`S`, `T`, `m` are all directly readable from the occupancy recorder we already ship. **Task
0 of the plan is to read them.** Every cost number in §5 is provisional until then.

## 4. Findings — what is actually wrong

Evidence for each is a file reference. Severity is against the model in §3.

### 4.1 `recursion_limit` is never set → LangGraph default 25 (GATE)

[runtime.py:94](../../../services/ai-backend/src/agent_runtime/execution/runtime.py:94) builds
the config with `configurable`, `max_concurrency`, `metadata`, `tags` and no recursion
limit. Confirmed empirically: `ensure_config()["recursion_limit"] → 25`.

A ReAct cycle costs ≥2 super-steps, so 25 buys ~12 model turns against a 16.2 mean.
`G_recursion ≈ 0.30`.

### 4.2 Tool budget is 10 per tool _name_, and all MCP shares one name (GATE)

`DefaultToolBudget.MAX_CALLS_PER_RUN = 10`
([tool_budgets.py:69](../../../services/ai-backend/src/agent_runtime/persistence/records/tool_budgets.py:69)),
keyed on the **model-visible** name. Every connector side effect routes through the single
`call_mcp_tool` dispatcher, so the effective budget is **10 MCP calls per run** against a
17.4 mean. The settings bound is `le=100`
([settings.py:208](../../../services/ai-backend/src/agent_runtime/settings.py:208)).

**And 10 is only the unscaled figure.** `DepthBudgetTable.apply`
([depth.py:127](../../../services/ai-backend/src/agent_runtime/execution/depth.py:127))
multiplies the budget by a reasoning-depth factor of `0.5 / 1.0 / 2.0`:

| Depth                       | Multiplier | Effective whole-run MCP budget |
| --------------------------- | ---------- | ------------------------------ |
| `fast`                      | ×0.5       | **5**                          |
| unset ("Auto") / `balanced` | ×1.0       | **10**                         |
| `deep`                      | ×2.0       | **20**                         |

Two consequences:

1. **On `fast` the gate is twice as tight as first stated** — 5 calls against a 17.4 mean.
2. **`deep` alone clears the mean without any code change.** Selecting depth is a config
   lever that moves this gate more than anything in Phase 1, and it should be set
   explicitly for benchmark runs rather than left to "Auto".

Related latent inconsistency worth fixing while we are here: `ModelRuntimeConfig`
([contracts.py:214](../../../services/ai-backend/src/agent_runtime/execution/contracts.py:214))
defaults `tool_call_budget` to **5**, mirroring `_DEFAULT_TOOL_CALL_BUDGET`
([deep_agent_builder.py:51](../../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py:51))
which feeds the prompt wording, while `RuntimeExecutionSettings` defaults to **10**. The
production path passes the settings value through
([models.py:156](../../../services/ai-backend/src/agent_runtime/execution/models.py:156)), so
the 5 binds only where a call site omits the field — but two defaults for one number is
exactly the drift both docstrings warn against, in a codebase that insists the prompt and
the enforced cap must agree.

`G_budget ≈ 0.28` at depth-unset, **≈0.10 on `fast`**. Note this is _positively correlated_
with 4.1 — the same long tasks trip both — so the joint gate is better than the naive
product but not by much.

### 4.3 No unattended approval path (GATE)

Defaults are `read=auto / write=ask / destructive=require`
([permissions.py:107](../../../services/ai-backend/src/agent_runtime/capabilities/tools/permissions.py:107));
`ask`/`require` install `HumanInTheLoopMiddleware` on `call_mcp_tool`. MCPMark is ~100%
CRUD. `grep -rn "auto_approve|headless|unattended|non_interactive"` over the service
returns **nothing** — the escape hatch does not exist.

`G_approval ≈ 0.05`. This gate also produces _unbounded_ latency (suspend, never resumed),
not just a failure.

**Joint gate `G ≈ 0.30 × 0.28 × 0.05 ≈ 0.004`**, call it 1–2% after correlation. Our
current score is indistinguishable from zero **for reasons that have nothing to do with
model quality**.

### 4.4 MCP errors reach the model as canned strings (STABILITY)

When an MCP tool returns `isError: true`, `McpOperationAdapter` discards the payload and
raises a fixed sentence
([operation_adapter.py:193](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/operation_adapter.py:193)):

```python
if McpToolCallOutcome.is_protocol_error(output):
    raise OperationGatewayError(
        OperationGatewayErrorCode.ADAPTER_FAILED,
        _CONNECTOR_PROTOCOL_ERROR,   # "The connector reported an error; no external change was made."
        retryable=False,
    )
```

Postgres `column "foo" does not exist`, GitHub `422 Validation Failed`, Notion
`body.parent.page_id should be a valid uuid` — all collapse to that one sentence. `output`,
which carries the server's real message, is on the line above and is never read.

**The fix is already written.** `McpToolCallOutcome.extract_error_text`
([outcomes.py:37](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/outcomes.py:37))
walks the outer and nested `content` blocks and returns the server's own text, falling back to
`PROTOCOL_ERROR_FALLBACK` only when there is genuinely no text block. It sits in the same
class as `is_protocol_error`, has **five unit tests**, and **has no production caller.** A
sibling helper is invoked at the call site; this one is not.

(Two constants that look like this failure are red herrings: `Messages.Loader.PROTOCOL_ERROR`
is dead — no consumers anywhere, including tests — and `_CONNECTOR_PROTOCOL_ERROR` above is
the live one.)

Separately `ErrorSanitizer` redacts `\b[0-9a-fA-F]{16,}\b`
([tool_error_sanitizer.py](../../../services/ai-backend/src/agent_runtime/execution/tool_error_sanitizer.py)),
which is exactly the shape of a Notion page ID and a GitHub SHA — so widening the message
without narrowing that rule would still strip the identifiers the model needs.

This is `p_retry ≈ 0.20` in §3.3, and it is the single largest non-gate term in the model.
It is also the cheapest thing in this document to fix: wire one already-tested method.

### 4.5 One bad descriptor fails the whole server (GATE, conditional)

`parse_tools` validates inside a generator and returns one `MALFORMED_DESCRIPTOR` for the
entire batch
([loader.py:532](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/loader.py:532)),
against limits of 4,000 chars/description and 16 KB/schema. The GitHub and Notion MCP
servers are precisely the shape that trips this: one verbose tool takes down all ~90.

`G_load` is 1.0 or ~0.0 per server with little in between — a coin-flip we can't see until
we run it.

### 4.6 No context compaction (COST)

MCPMark ships `--compaction-token` because 16+ turns of Notion blocks and Postgres result
sets overflow. We have no summarisation middleware. This is the quadratic term in §3.1
running unchecked.

### 4.7 The umbrella dispatcher is right for big servers and wrong for small ones (COST/ACCURACY)

Our surface is `load_mcp_server` → `load_tool_spec` → `call_mcp_tool`. MCPMark's reference
harness hands the model native tool schemas directly.

The obvious read is "the umbrella costs us accuracy". The model says something more
interesting. Native passthrough trades resident schema bytes (`T`, elasticity 0.20) for
turns (`N`, elasticity 1.65) — so it wins **only when `T` stays small**:

| Server                | tools | `T` native | `N` | modelled input cost | vs umbrella (268.6k) |
| --------------------- | ----- | ---------- | --- | ------------------- | -------------------- |
| Postgres / Filesystem | ~10   | 4,000      | 14  | 207,200             | **−23%**             |
| Notion                | ~25   | 12,000     | 14  | 319,200             | +19%                 |
| GitHub                | ~90   | 32,000     | 14  | 599,200             | **+123%**            |

**So the correct design is neither.** It is a threshold: native passthrough when the
server's total schema fits a byte budget, umbrella + lazy loading above it. That rule is
also the right _product_ behaviour — it is what "one connector installed" vs "twelve
connectors installed" should do — which promotes this from `BENCH-ONLY` to a real feature.

### 4.8 A fourth gate and a second budget layer, both in `model_invocation/` (GATE/COST)

`agent_runtime.execution.model_invocation` is a live model routing, failover and
circuit-breaker subsystem — composed into `runtime_worker/handlers/run.py`, `loop.py`,
`model_invocation_circuit.py` and `runtime_api/app.py`. §4.1–4.7 were written without reading
it, and it invalidates two things above.

**A fourth gate.** `ProviderCircuitConfig`
([circuit_health.py:64](../../../services/ai-backend/src/agent_runtime/execution/model_invocation/circuit_health.py:64))
opens a provider circuit after `open_failure_threshold = 3` failures inside a
120-second window, with a 30-second cooldown, tracked per worker process
(`ProcessLocalProviderCircuitHealth`). A 17-turn MCPMark task runs for minutes against a live
provider, so three transient failures inside any two-minute stretch stops the run. **`G` in
§3.3 has three terms and needs at least four**, and this one is _not_ independent of the
others — a long run is exactly the run most likely to accumulate three failures.

**A second budget layer.** `ModelInvocationBudget`
([contracts.py:324](../../../services/ai-backend/src/agent_runtime/execution/model_invocation/contracts.py:324))
carries `max_cost_microusd`, `max_input_tokens`, `max_output_tokens` and `deadline_at`
alongside `max_attempts` (**default 1**, ceiling 3) and `max_same_deployment_attempts`. So:

- §3.1 models output cost as unbounded. It is not — there is a per-invocation ceiling that
  can terminate a run on cost or on a deadline, independently of the tool budget and the
  recursion limit. **At `xhigh`, where output is 84% of spend (§3.1a), a cost ceiling is the
  bound most likely to bite first.**
- `max_attempts = 1` means **no model-level retry by default**: one transient provider
  failure ends the turn rather than failing over. §3.2's latency model assumed a clean
  call per turn and never accounted for attempts at all.

**Phase 0 must read these before sizing anything.** Whether they bind depends on the values
the run path persists, which is a measurement, not a reading of the defaults.

## 5. Interventions and estimated effect

Estimates are **modelled, not measured**, from §3. Signs are high-confidence; magnitudes
are not. Baselines: accuracy against current ~1%, cost/latency against a _completing_ run
(otherwise the comparison is meaningless — a run that dies at turn 12 is cheap and fast).

Accuracy figures are `pass@1` for a frontier model. `Δ` is absolute percentage points.

> **Read [RESEARCH.md](RESEARCH.md) before scheduling anything past P0.** HARBOR ran four
> rounds of exactly this kind of stacking on a production coding agent and scored **+2, −4,
> −5** — two of three rounds net-negative — concluding that net-positive harness features are a
> small class-specific subset and that stacking published techniques is often counterproductive.
> P0 is gate removal and is safe to land as a set; everything below it is feature stacking and
> inherits that warning. Two rows are already known to be wrong: **P2-1 is promoted** (§4 of
> RESEARCH), and **P1-4 is new** (§5).

| #        | Change                                              | Accuracy        | Cost             | Latency     | Confidence | Class   |
| -------- | --------------------------------------------------- | --------------- | ---------------- | ----------- | ---------- | ------- |
| **P0-1** | Set `recursion_limit` (run-configurable)            | **+15–25pp**    | **+40–70%**      | **+50–90%** | High       | Gate    |
| **P0-2** | Re-key budget to inner MCP tool name; raise ceiling | **+15–25pp**    | **+30–60%**      | **+30–60%** | High       | Gate    |
| **P0-3** | Unattended policy profile (`write=auto`)            | **+25–40pp**    | ~0%              | **−90%+**   | High       | Gate    |
| **P1-1** | Pass real MCP error text through redaction          | **+15–30pp**    | **−15–25%**      | **−15–25%** | Medium     | Quality |
| **P1-2** | Degrade descriptor validation per-tool              | **+0–20pp**     | ~0%              | ~0%         | Medium     | Gate    |
| **P1-3** | Parallel tool calls where independent               | ~0              | ~0%              | **−15–30%** | Medium     | Latency |
| **P1-4** | Signal every truncation to the model                | **+2–10pp**     | ~0%              | ~0%         | Medium     | Quality |
| **P2-1** | Compact tool _results_, not reasoning               | **+5 to +15pp** | **−40–60%**      | **−20–40%** | Medium     | Both    |
| **P2-2** | Result field projection / truncation                | **−10 to +3pp** | **−20–30%**      | **−10–15%** | Low        | Cost    |
| **P3-1** | Adaptive native-vs-umbrella tool exposure           | **+5–15pp**     | **−25% / +120%** | **−15%**    | Low        | Both    |

### 5.1 The honest trade in P0

**P0 makes cost and latency substantially worse, and that is correct.** Today a run dies at
step 25 or blocks at call 11. It is cheap and fast because it gives up. Removing the gates
means we start paying for the full 17-turn task — and also paying full price for tasks we
_fail_, which previously aborted early.

There is no version of this where accuracy goes to ~40% and per-task cost does not roughly
double. The question the P0 numbers answer is not "is this an improvement" but "is the
resulting cost acceptable", and that is a product decision, not an engineering one. §5.2 is
how we pay it back.

### 5.2 Which changes are actually free

Only two rows improve all three metrics at once, and they do it for the same reason —
**a failed call that the model can't recover from costs a retry turn, and turns are
quadratic**:

- **P1-1 (real error text)** — every avoided blind-retry cycle removes a turn. At
  elasticity 1.65, cutting wasted retries by 20% pays −30% cost _and_ −20% latency _and_
  raises `p_retry`. This is the highest-value change in the document and it is a bug fix.
- **P1-2 (per-tool descriptor degradation)** — strictly removes a failure mode.

Ordering follows from this: **P1-1 lands with P0, not after it.** It is the only lever that
funds the P0 cost increase.

### 5.3 What we are deliberately not doing

- **Subagent delegation.** MCPMark tasks are single-environment CRUD over shared mutable
  state. Delegation multiplies tokens and loses state coherence across the boundary.
  Modelled effect: cost +60–150%, accuracy −10pp or worse.
- **Prompt tuning for the benchmark.** Moves the number, teaches us nothing, does not
  survive a task-set refresh.
- **Retry-until-pass loops.** `pass^4` is designed to punish exactly this.
- **Trimming tool-schema bytes as a cost programme.** Elasticity 0.20. It is the most
  satisfying and least useful thing on the list.

## 6. Validation

An estimate we don't check is a guess with a table around it.

- **Task 0** replaces `S`, `T`, `m` with measurements from `context_occupancy` before any
  code changes. If they are far off §3.4, §5's cost column is re-derived before we build.
- Each phase runs the **10-task `easy` suite** at `k=4` before and after. Predicted vs
  actual goes in a `RESULTS.md` per phase, including where the model was wrong.
- P1-1's `p_retry` claim gets a **direct A/B**: same tasks, same seed, canned vs real error
  text. This is the one number the whole §3.3 argument rests on, so it gets measured
  rather than inferred.
- The full 127-task run happens **once**, after P1, to set a real baseline. Running it per
  phase burns budget to reduce noise on a number we aren't optimising yet.

## 7. Success criteria

|                         | Target                                     | Rationale                                             |
| ----------------------- | ------------------------------------------ | ----------------------------------------------------- |
| Gate survival `G`       | **> 0.95**                                 | P0 either works or it doesn't; this is arithmetic     |
| `pass@1`, easy suite    | **> 60%**                                  | 10 tasks — directional only, not a headline           |
| `pass@1`, full suite    | **> 40%**                                  | mid-pack on the public board; a real baseline         |
| `pass^4 / pass@1` ratio | **> 0.45**                                 | the stability ratio; this is the number we care about |
| Cost / solved task      | **< 2× current cost per _attempted_ task** | bounds §5.1                                           |
| P95 latency             | **< 5 min**                                | beyond this it is not a product interaction           |

`pass^4 / pass@1` is the criterion to defend if the others slip. A harness that solves a
task once in four is a demo; one that solves it three times in four is a product.

## 8. Open decisions

1. **Cost ceiling.** §5.1 roughly doubles per-task cost. Is that acceptable, or do we want
   P2 (compaction) landed _before_ P0 rather than after, accepting a slower start?
2. **`recursion_limit` default.** Raise the shipped default for all users, or only under
   the benchmark profile? The 25 default is arguably too low for real product work too —
   this PRD assumes we raise it globally and asks for confirmation.
3. **Write policy.** `write=auto` under an explicit unattended profile is a real posture
   change. It needs sign-off that it cannot leak into an interactive deployment.
4. **P3-1 scope.** Adaptive tool exposure is the largest piece of work here and the least
   certain. Recommend deferring until P0+P1 data shows where failures actually cluster.
