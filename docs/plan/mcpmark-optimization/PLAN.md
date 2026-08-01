# Implementation plan — MCPMark harness optimisation

Companion to [PRD.md](PRD.md). Every phase states the **prediction** it is testing, so a
phase that lands and misses is information rather than a surprise.

Branching follows repo convention: branch from `dev`, PR into `dev`, one PR per unit below.

---

## Phase 0 — Measure and connect (blocking; no optimisation)

Nothing in Phase 1+ is trustworthy until this lands. Two of these units exist purely to
replace an assumption with a number.

### 0.1 Baseline the cost parameters — `S`, `T`, `m`

The occupancy recorder already persists per-segment token attribution
(`observability/context_occupancy.py`, `context_occupancy_recorder.py`, stored via
`append_context_occupancy` in all three adapters). We do not need to build an instrument,
only to read one.

- Drive 5 representative runs against a live MCP connector.
- Emit a report: system-prompt tokens, per-`ModelToolOwner` schema tokens, mean tokens
  appended per turn.
- Write the measured values into [PRD.md](PRD.md) §3.4 and **re-derive §5's cost column**.

**Also read the invocation budget the run path actually persists** (PRD §4.8):
`max_attempts`, `max_same_deployment_attempts`, `max_cost_microusd`, `max_input_tokens`,
`max_output_tokens`, `deadline_at`. The contract defaults (`max_attempts=1`, ceiling 3) are
not evidence of what a run carries, and a cost or deadline ceiling would bound the sweep
before any gate in §4.1–4.3 does. Read `execution/model_invocation/journal.py` for the
per-attempt record rather than adding a counter.

**Exit:** `S`, `T`, `m` measured, and the invocation budget known to bind or not. If any is
off the assumption by more than 2×, §5 is rewritten before Phase 1 starts.
**Effort:** S · **Risk:** none · **Metric effect:** none (measurement only)

### 0.2 Register the five benchmark MCP servers

`ai-backend` reaches MCP only through `backend`'s `/internal/v1` RPC proxy
([backend_provider.py](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py)),
so each server needs a registry entry before the agent can address it.

- Verify **stdio transport actually works end to end**. `McpTransport.STDIO` exists in the
  enum ([cards.py:49](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/cards.py:49)),
  but the local-server work lives on the unmerged `claude/app-failure-diagnosis-f222b9`
  branch. Filesystem, Postgres and Playwright are stdio — **if this fails, three of five
  environments are unreachable and this unit becomes the critical path.**
- Seed registry entries for all five under a benchmark org.

**Exit:** all five servers load and `list_tools` returns.
**Effort:** M (L if stdio needs porting) · **Risk:** **high — schedule first**

### 0.3 MCPMark agent adapter

A `MCPMarkAgent`-compatible shim that drives our runtime instead of a raw tool loop:
`POST /v1/agent/runs` on the facade → consume SSE to terminal → report tokens and turns
back in MCPMark's expected shape. Registered via `--agent copilot`.

Uses MCPMark's own `verify.py` untouched — we score against their verifier, not ours.

**Exit:** `easy` suite runs end to end and produces a score, however bad.
**Effort:** M · **Risk:** medium

### 0.4 Baseline run

`easy` suite, `k=4`, current defaults. Expected to score ~0.

**This is the point of the phase.** A measured zero with a recorded failure taxonomy —
recursion vs budget vs suspend vs load — validates the §4 gate model before we spend
anything on fixing it. If the observed failure mix disagrees with §4, the priority order in
the PRD is wrong and gets rewritten here.

**Effort:** S · **Predicted:** `pass@1` 0–5%, failures dominated by suspend-on-approval

---

## Phase 1 — Open the gates (P0) + the change that pays for them (P1-1)

Landed together deliberately: P0 raises cost ~2×, P1-1 is the only lever that funds it
(PRD §5.2).

### 1.1 `recursion_limit` becomes a real setting — _P0-1_

`runtime_config` ([runtime.py:94](../../../services/ai-backend/src/agent_runtime/execution/runtime.py:94))
returns a config with no `recursion_limit`, so LangGraph's default 25 applies.

- Add `recursion_limit` to the returned config, sourced from
  `RuntimeExecutionSettings` (new field, env `RUNTIME_RECURSION_LIMIT`, default **60**).
- Plumb a per-run override through `AgentRuntimeContext`.
- Surface `GraphRecursionError` as a **typed, distinguishable** terminal reason. Today it
  is indistinguishable from a generic failure, which is why we never noticed.

60 = 2 super-steps × ~25 turns + middleware headroom, i.e. ~1.5× the observed mean, which
covers the tail without letting a runaway loop run forever.

**Test:** a fake-model run scripted to 30 tool cycles completes at 60 and fails cleanly at 25.
**Effort:** S · **Predicted:** accuracy +15–25pp · cost +40–70% · latency +50–90%

### 1.2 Budget keys on the inner MCP tool name — _P0-2_

`DefaultToolBudget.MAX_CALLS_PER_RUN = 10` keyed on the model-visible name, and every MCP
call is `call_mcp_tool`, so one budget covers all connector work
([tool_budgets.py:69](../../../services/ai-backend/src/agent_runtime/persistence/records/tool_budgets.py:69)).

- Key admission on the **unwrapped** name via the existing
  `McpDispatcherUnwrap.effective_tool_name`
  ([dispatcher.py](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/dispatcher.py)) —
  the helper already exists for exactly this unwrap, and centralising on it is the
  documented intent.
- Raise the `le=100` bound in
  [settings.py:208](../../../services/ai-backend/src/agent_runtime/settings.py:208).
- Keep the prompt suffix and the enforced cap in sync — the docstring is explicit that they
  must agree, and this change makes it easy to break.
- **Reconcile the two defaults.** `ModelRuntimeConfig.tool_call_budget` defaults to 5
  ([contracts.py:214](../../../services/ai-backend/src/agent_runtime/execution/contracts.py:214));
  `RuntimeExecutionSettings.tool_call_budget` defaults to 10. One concept, two numbers.
- **Set reasoning depth explicitly for benchmark runs.** `DepthBudgetTable.apply`
  ([depth.py:127](../../../services/ai-backend/src/agent_runtime/execution/depth.py:127))
  scales the budget ×0.5 / ×1.0 / ×2.0 for `fast` / `balanced` / `deep`. Leaving depth on
  "Auto" makes the gate depth-dependent and the sweep non-reproducible.

**Zero-code mitigation available now:** selecting `deep` doubles the effective budget to 20,
which alone clears the 17.4 mean. Worth running in the Phase 0 baseline as a second control
arm — it costs nothing and bounds how much of P0-2's predicted gain is really just a config
default.

This is a **product correctness fix**, not a benchmark accommodation: "10 calls to
`github.create_issue`" is the honest unit; "10 calls to the dispatcher" was never what the
setting meant or what the prompt promised.

**Test:** 12 calls to distinct MCP tools all admitted; 11 to the same tool rejected;
the rejection message names the real tool.
**Effort:** M · **Predicted:** accuracy +15–25pp · cost +30–60% · latency +30–60%

### 1.3 Unattended policy profile — _P0-3_

Defaults `write=ask / destructive=require`
([permissions.py:107](../../../services/ai-backend/src/agent_runtime/capabilities/tools/permissions.py:107))
install `HumanInTheLoopMiddleware`, and no auto-approve path exists.

- Add an explicit `unattended` execution profile that resolves `write=auto`.
- **Gate it on deployment posture, not a request flag** — a caller-supplied field that
  disables approvals is exactly the untrusted-input pattern CLAUDE.md forbids. It must be
  unreachable from a request body.
- `destructive` stays `require` even unattended, and the profile is refused outright when
  `BACKEND_ENVIRONMENT` is not the benchmark/dev posture.

**Test:** unattended profile dispatches a write with no interrupt; an interactive-posture
request carrying the same flag is refused; `destructive` still interrupts.
**Effort:** M · **Risk:** **high — security-relevant. Needs review sign-off** (PRD §8.3)
**Predicted:** accuracy +25–40pp · cost ~0 · latency −90%+

### 1.4 Real MCP error text reaches the model — _P1-1_

`McpOperationAdapter` discards `output` and raises `_CONNECTOR_PROTOCOL_ERROR`
([operation_adapter.py:193](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/operation_adapter.py:193)),
so every connector error becomes "The connector reported an error; no external change was
made." `ErrorSanitizer` then redacts `\b[0-9a-fA-F]{16,}\b`, the shape of every Notion page
ID and GitHub SHA.

**The core change is one line.** `McpToolCallOutcome.extract_error_text` already does the
extraction, already has five unit tests, and already sits in the class whose
`is_protocol_error` is invoked immediately above — it simply has no caller.

- Call `extract_error_text(output)` at that site and carry the result as the failure message.
- Narrow the hex-ID rule so **resource identifiers returned by the server the model is
  already talking to** survive. Those IDs are not secrets; the model just read them from
  the same connector two turns ago. Internal run/org/conversation IDs stay redacted.
- Raise `SAFE_MESSAGE_MAX_LENGTH` (500) for this field — a Postgres error with position
  info exceeds it.
- Delete `Messages.Loader.PROTOCOL_ERROR` — it is dead, and leaving two near-identical
  strings around is how the next reader concludes the wrong one is live (as I did).

**Test:** a Postgres `column "x" does not exist` reaches the model verbatim; a connection
string in the same payload does not; a Notion page ID survives; an org UUID does not.
**Effort:** **S** (was M — the extractor exists) · **Risk:** medium — touches a redaction
boundary, needs security review
**Predicted:** accuracy **+15–30pp** · cost **−15–25%** · latency **−15–25%**

### 1.5 A/B the `p_retry` claim

The whole of PRD §3.3 rests on `p_retry` 0.20 → 0.75. Measure it: same tasks, same seed,
canned vs real error text, count retry-after-error successes.

**Exit:** measured `p_retry` written into the PRD. If the gap is <2×, P1-1's accuracy
estimate is wrong and §5 gets re-derived.
**Effort:** S

**Phase gate:** `easy` suite at `k=4`. Predicted `pass@1` **55–75%**, `G > 0.95`.
Full 127-task baseline runs once here.

---

## Phase 2 — Pay the cost back

Only start once Phase 1 has a real number. These are optimisations, and optimising before
the baseline exists is how you optimise the wrong term.

### 2.1 Per-tool descriptor degradation — _P1-2_

`parse_tools` returns one `MALFORMED_DESCRIPTOR` for the whole batch
([loader.py:532](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/loader.py:532)),
so one oversized tool takes down a ~90-tool server.

- Validate per descriptor; drop the bad one, keep the rest, attach an `McpLoadWarning`.
- Only fail the server load when **zero** tools survive.

**Test:** a 3-tool payload with one 5,000-char description loads 2 tools and 1 warning.
**Effort:** S · **Predicted:** accuracy +0–20pp (bimodal — it is 0 unless a real server
trips the limit, and ~20pp if GitHub does) · cost ~0 · latency ~0

### 2.2 Parallel tool calls — _P1-3_

`max_concurrency` is already set from `max_parallel_tasks`
([runtime.py:117](../../../services/ai-backend/src/agent_runtime/execution/runtime.py:117)),
but nothing configures `parallel_tool_calls` on the provider side — it appears only in
`fake_model.py`. Confirm whether the model is being told it may batch, and that our
middleware chain does not serialise what it emits.

**Effort:** S–M · **Predicted:** accuracy ~0 · cost ~0 · latency −15–30%

### 2.3 Result-scoped compaction — _P2-1_

No summarisation exists. This is the quadratic term running unchecked (PRD §3.1).

Compact **tool results**, never assistant reasoning or IDs. Tool results are bulky and
re-derivable; the assistant's turn is where the created-page-ID lives, and losing it fails
the task. A naive `SummarizationMiddleware` over the whole history would do exactly that —
which is why this is P2 with a **negative accuracy risk band** (−5pp) rather than a free win.

- Trigger on a token threshold; keep the last `k` results verbatim.
- Never compact a message containing an identifier the run has written to.

**Test:** a run over threshold compacts old results, keeps the newest, and a created-object
ID from turn 3 is still exactly recoverable at turn 15.
**Effort:** L · **Risk:** **high — this is the change most likely to cost accuracy**
**Predicted:** accuracy −5 to +5pp · cost −35–50% · latency −20–30%

### 2.4 Result field projection — _P2-2_

Reduce `m` at the source: project MCP result payloads to the fields the model needs.
Strictly riskier than 2.3 (drops data before the model ever sees it) and lower value.
**Do not start until 2.3 has landed and been measured.**

**Effort:** M · **Predicted:** accuracy −10 to +3pp · cost −20–30% · latency −10–15%

---

## Phase 2.5 — The LangChain middleware set, one seam at a time

[RESEARCH.md](RESEARCH.md) §2: LangChain took `deepagents-cli` from **52.8% → 66.5%** on
Terminal-Bench 2.0 with no model change, using the middleware below. This phase ports the
same ideas.

**It is also the phase most likely to lose points.** HARBOR stacked published techniques on a
production harness and scored **+2, −4, −5**; its self-evaluation gate "corrected passing
answers into failing ones", which is precisely what 2.5c risks. So every item here ships as
a **separate seam with a null control**, none is on by default, and **none is adopted without
its own ablation arm** ([COMPONENTS.md](COMPONENTS.md) §5.3). Shipping this set as a block is
the documented way to go backwards.

Order is by expected value per unit of risk, not by the order LangChain lists them.

### 2.5a `EnvironmentContextMiddleware` — inject what the run can reach

LangChain's `LocalContextMiddleware` maps the directory tree and discovers available tooling
at startup, so the agent stops burning turns finding its own environment. Our equivalent is
not a directory tree — it is **the connected MCP servers and their tool inventory**, which the
agent currently discovers through `load_mcp_server` / `load_tool_spec` round-trips.

Injecting a compact server+tool inventory at run start trades resident tokens (`T`,
elasticity 0.20) for turns (`N`, 1.65 — see PRD §3.1b). That is the same trade as PRD §4.7's
adaptive exposure and it obeys the same byte threshold, so **build them together or not at
all** — two independent mechanisms that both decide "how much tool schema is resident" will
fight.

**Seam:** `RunContextPrimer` — `NullPrimer` / `McpInventoryPrimer(byte_budget)`
**Effort:** M · **Predicted:** accuracy +3–10pp · cost −10–20% · latency −10–20%

### 2.5b `LoopDetectionMiddleware` — notice the agent is stuck

Counts repeated operations and nudges the agent to reconsider after N. LangChain counts file
edits; **our analogue is the same MCP tool called with the same arguments**, which is exactly
the blind-retry signature PRD §3.3 predicts under opaque errors.

This composes with P1-1 rather than duplicating it: P1-1 makes the error informative so the
retry is different; 2.5b catches the case where it is not. **Measure it after P1-1**, or its
effect is confounded by the errors P1-1 already fixed.

Note it partially overlaps the tool budget (P0-2) — a budget also stops a loop, just bluntly
and without telling the model why. If P0-2's ECDF shows repeats are rare, skip this.

**Seam:** `RepeatCallPolicy` — `NullPolicy` / `NudgeAfterN(n)`
**Effort:** S · **Predicted:** accuracy +2–8pp · cost −5–15% · latency −5–15%

### 2.5c `PreCompletionChecklistMiddleware` — verify before declaring done

Intercepts the agent before it exits and forces a verification pass against the task spec.

**This is the highest-upside and highest-risk item in the whole plan.** Upside: MCPMark scores
by running `verify.py` against **final environment state**, so an agent that re-reads the
state it claims to have written is checking the exact thing the grader will. Nothing else in
the plan targets the grading criterion directly.

Risk: HARBOR's self-evaluation gate is the same shape and cost it **4 passes**, by overturning
correct answers. The difference we should preserve is that a checklist which **re-reads state**
is an observation, whereas a gate which **re-judges the answer** is a second opinion — and it
was the second opinion that failed. Build the observing kind; do not let it revise a
conclusion.

**Seam:** `PreCompletionPolicy` — `NullPolicy` / `StateReReadChecklist`
**Effort:** M · **Predicted:** accuracy **−5 to +15pp** · cost +10–20% · latency +10–20%
**Risk: high — pre-register the direction and stop if the first arm is negative.**

### 2.5d Phase-structured prompt + time-budget warning

LangChain restructured the system prompt into **Planning & Discovery → Build → Verify → Fix**
and injected time-budget warnings.

Cheapest item here and the least separable: prompt changes cannot be cleanly ablated against
a fixed control the way a middleware can, because the prompt is one string. Ship it as a
`prompt_plan_revision` on the variant ([COMPONENTS.md](COMPONENTS.md) §2) so at least the two
revisions are addressable and the digest records which ran.

**Effort:** S · **Predicted:** accuracy +2–8pp · cost ~0 · latency ~0

### 2.5e Reasoning sandwich (`xhigh` → `high` → `xhigh`) — blocked

Max effort for planning, moderate for implementation, max for verification. On Luna pricing
output is ~84% of spend (PRD §3.1a), so this is simultaneously the accuracy and the cost play
— the single best-shaped item in the research.

**We cannot express it.** Effort is fixed per run; the catalog ladder shipped in `1287f5a7` is
a precondition, not the feature. Needs per-phase effort selection
([model-catalog-effort DESIGN.md](../model-catalog-effort/DESIGN.md) Phase 2, which splits
`reasoning_effort` from `resource_profile`).

**Blocked on that split.** Predicted: accuracy +3–10pp · cost **−20–40%** · latency −10–20%

### 2.5 phase gate

Each of 2.5a–2.5d gets its own arm. **Adopt only the ones whose arm is positive** — HARBOR's
finding is that net-positive harness features are a small, class-specific subset, and the
default expectation for any individual item here should be "no effect" rather than "the
published number".

## Phase 3 — Adaptive tool exposure (defer)

### 3.1 Native-vs-umbrella threshold — _P3-1_

PRD §4.7: native passthrough beats the umbrella by −25% cost on a 10-tool server and loses
by +121% on a 90-tool one. The design is a byte-budget threshold, not a mode flag.

Largest unit of work here and the least certain. **Deferred until Phase 1+2 data shows
where failures actually cluster** — if arg-shape errors are not a major failure class after
P1-1, the accuracy premise for this evaporates and only the cost argument remains.

**Effort:** L · **Predicted:** accuracy +5–15pp · cost −25% (small servers) / +120% (large)

---

## Sequencing and rationale

```
0.2 stdio ──▶ 0.3 adapter ──▶ 0.4 baseline ──┐
0.1 measure ─────────────────────────────────┤
                                             ▼
                      1.1 recursion ─┐
                      1.2 budget ────┼──▶ 1.5 A/B ──▶ Phase gate ──▶ Phase 2 ──▶ Phase 3
                      1.3 unattended ┤
                      1.4 errors ────┘
```

- **0.2 is the schedule risk.** If stdio needs porting from the unmerged branch, it is the
  critical path for three of five environments. Start it first.
- **Phase 1 lands as one set.** The three gates are multiplicative — fixing two of three
  leaves the score at ~0, so partial delivery is indistinguishable from no delivery.
- **1.4 is not optional in Phase 1.** It is what makes the Phase 1 cost increase
  survivable, and it is the only change in the document that improves all three metrics.

## Effort summary

| Phase | Units | Effort | Predicted `pass@1` after       |
| ----- | ----- | ------ | ------------------------------ |
| 0     | 4     | M      | ~0% (measured, not assumed)    |
| 1     | 5     | M–L    | 55–75% (easy) / 35–50% (full)  |
| 2     | 4     | L      | +0–20pp accuracy, −40–60% cost |
| 3     | 1     | L      | deferred pending Phase 2 data  |

## Risk register

| Risk                                          | Impact                                    | Mitigation                                                                               |
| --------------------------------------------- | ----------------------------------------- | ---------------------------------------------------------------------------------------- |
| stdio transport unimplemented                 | 3 of 5 environments unreachable           | 0.2 scheduled first; port from `claude/app-failure-diagnosis-f222b9`                     |
| Unattended profile reachable from a request   | **Security** — approvals bypassed in prod | Posture-gated, not flag-gated; refused outside dev/bench; review sign-off                |
| Error passthrough widens exfiltration surface | **Security**                              | Keep path/token/conn-string redaction; narrow only the resource-ID rule; security review |
| Compaction drops an ID mid-run                | Accuracy regression                       | Compact results only; never compact ID-bearing turns; explicit test                      |
| Cost doubles and is not accepted              | Programme stalls after Phase 1            | PRD §8.1 decision taken _before_ Phase 1, not after                                      |
| Model params (`S`,`T`,`m`) badly wrong        | §5 cost column invalid                    | 0.1 blocks Phase 1                                                                       |

## What would falsify this plan

Stated up front so we notice rather than rationalise:

- **0.4's failure taxonomy doesn't match §4.** If runs fail for reasons other than
  recursion/budget/suspend, the gate model is wrong and Phase 1 is mis-prioritised.
- **1.5 measures `p_retry` gap < 2×.** Then error quality is not the stability lever, §3.3
  is wrong, and Phase 2's ordering should change.
- **Phase 1 lands and `pass@1` stays under 20%.** Then the bottleneck was never the harness
  and this whole programme was mis-aimed — stop and re-scope rather than proceeding to
  Phase 2.
