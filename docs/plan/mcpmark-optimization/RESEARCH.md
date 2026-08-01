# Prior art — measured results from Deep Agents / agent-harness work

Only results with **published numbers** are recorded here. Blog claims without a baseline are
excluded. Where a result contradicts [PRD.md](PRD.md) or [PLAN.md](PLAN.md), the correction is
stated rather than reconciled away.

---

## 1. The evidence

| Work                                                                                                                                                                     | Setup                                                  | Baseline → result                                  |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------ | -------------------------------------------------- |
| [LangChain, deepagents-cli](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering)                                                               | Terminal-Bench 2.0, gpt-5.2-codex unchanged            | **52.8% → 66.5%** (+13.7pp), Top 30 → Top 5        |
| [NVIDIA, Nemotron 3 Ultra profile](https://developer.nvidia.com/blog/create-a-langchain-deep-agents-harness-profile-for-nvidia-nemotron-3-ultra-to-improve-performance/) | deepagents' own eval suite (127 tasks — _not_ MCPMark) | **94/127 → 96/127**; read-file tests **0/3 → 3/3** |
| [HARBOR](https://arxiv.org/abs/2604.20938)                                                                                                                               | Terminal-Bench 2 (89 tasks), codex-py                  | 15/89 → **17/89 peak**, then **13**, then **12**   |
| [Less Context, Better Agents](https://arxiv.org/abs/2606.10209v1)                                                                                                        | 50 MCP-tool tasks, GPT-5                               | **71.0% → 91.6%**, tokens −64%                     |
| [Slipstream](https://arxiv.org/html/2605.08580)                                                                                                                          | SWE-bench Verified, BrowseComp                         | **+2.6–8.8pp**, latency **−11.3–39.7%**            |
| [CompactionRL](https://arxiv.org/pdf/2607.05378)                                                                                                                         | SWE-bench Verified / Terminal-Bench 2.0                | **+5.5** / **+6.8** points                         |

## 2. What each actually did

**LangChain** — `LocalContextMiddleware` (inject directory structure + discovered tooling at
startup), `LoopDetectionMiddleware` (count edits, nudge after N), `PreCompletionChecklistMiddleware`
(force a verification pass against the task spec before exit), a prompt restructured into
Planning & Discovery → Build → Verify → Fix, time-budget warnings, and an
**"xhigh–high–xhigh reasoning sandwich"** — max effort for planning, moderate for
implementation, max again for verification.

**NVIDIA** — one middleware: `ReadFileContinuationNoticeMiddleware`, appending a notice when a
file read returns the maximum permitted lines so the model knows to paginate. Also documents
what a **harness profile** is: per-model overrides in three categories — prompts (base system
prompt, suffix, tool descriptions), exclusions (remove tools or middleware), additions (extra
middleware or subagents).

**HARBOR** — four rounds of manual flag stacking on a ~30-enhancement harness, then automated
search over the same flag space.

## 3. HARBOR is the one that should change our plan

Its rounds, in order: **+2, −4, −5.**

- **B (+2, the only clean win):** cache-key normalisation, failure-aware trajectory library,
  raised hint-injection thresholds (0.30→0.85, 0.50→0.80). Cache hit rate 1% → 3.7%.
- **C (−4):** a Terminus-KIRA-style self-evaluation gate fired often and **"corrected passing
  answers into failing ones."** Intent canonicalisation produced zero extra cache hits.
- **D (−5):** an ACON observation-compression gate was **wired upstream of the cache**, so
  cached entries stored compressed summaries instead of raw output and every task needing
  line-level detail failed. Two further integrations were silently inert: a reflection store
  that never propagated between containers (**"write counters healthy, retrieval counters
  zero"**) and a speculative-execution predictor that was never invoked.

Automated search then recovered **17/89 — matching the manual peak — with a quarter of the
flags** (cross-session memory index + tiered conversation compressor), and beat the manual
all-on anchor by +5. Oracle upper bound was 81/89 against a best achieved 17/89.

Their conclusions, verbatim in substance: **net-positive harness features are a small,
class-specific subset**; stacking published techniques is often counterproductive; the real
signal is in **component-internal tuning — thresholds and compression parameters — not
feature on/off switches**; and automated search dominates manual stacking once the flag space
exceeds a handful of bits.

### 3.1 Corrections this forces on our plan

**PLAN.md stacks roughly eight interventions across four phases. That is the documented
failure mode.** Two of HARBOR's three stacking rounds were net-negative on a harness that
already had ~30 enhancements. We should not assume our stack behaves better.

Concretely:

- **Ship P0 as a set (it is gate removal, not feature stacking), then stop and measure.**
  Removing a cap that fires is categorically different from adding a technique, and the
  gates are multiplicative, so they must land together. Everything after P0 is feature
  stacking and inherits HARBOR's warning.
- **[COMPONENTS.md](COMPONENTS.md)'s leave-one-out ablation is the right instinct and the
  wrong scale.** HARBOR shows LOO over a handful of flags is where manual reasoning still
  works; beyond that, search wins. Our seam design already makes every component a
  runtime-selectable flag — which is exactly the input an automated search needs. That is
  the argument for building the seams, independent of the ablation.
- **Tune thresholds, not switches.** HARBOR's only clean win came substantially from moving
  two numbers (0.30→0.85, 0.50→0.80), and its worst round came from adding features. Our
  P0-1 (`recursion_limit`) and P0-2 (budget cap) are threshold changes — which is
  encouraging — but P2-1's compaction threshold deserves the same treatment as a tuned
  parameter, not an on/off.
- **HARBOR's two silent integrations are our audit's bug class**, found independently:
  components wired in, counters healthy on the write side, zero on the read side.
  [docs/audit/ai-backend-smells](../../audit/ai-backend-smells/FINDINGS.md) found ten such
  modules in `ai-backend`. This is apparently the dominant failure mode of harness work, not
  a quirk of our codebase.

## 4. Compaction: the PRD demoted it and the evidence says promote it

[PRD.md](PRD.md) §3.1a demoted P2-1 to −6–8% cost on Luna pricing and carried a −5pp accuracy
risk band. **Both halves look wrong.**

**Less Context, Better Agents** is the closest published setup to MCPMark — long-horizon,
tool-using, over **MCP tools**, with verbose enterprise tool responses:

| Approach                    | Completion | Tokens    | Time    |
| --------------------------- | ---------- | --------- | ------- |
| Full history                | 71.0%      | 1,480,996 | 14.56 h |
| Last-5 tool pairs           | 79.0%      | 535,274   | 5.39 h  |
| **Pruning + summarisation** | **91.6%**  | 553,374   | 5.79 h  |

Pruning to the last 5 tool call/response pairs **raised** completion 8pp and cut tokens 64%.
Adding summarisation on top raised it another 12.6pp. So in the regime we care about,
context management is an **accuracy** intervention that happens to be cheaper — not a cost
intervention that risks accuracy.

That is corroborated: CompactionRL **+5.5 / +6.8** points, Slipstream **+2.6–8.8pp with
11–40% lower latency**.

**Revised P2-1 estimate: accuracy +5 to +15pp, cost −40–60%, latency −20–40%** — promoted
from "demoted, marginal" to a Phase 1 candidate. The caveat that survives is HARBOR's:
compaction hurt badly when wired in the wrong place (upstream of the cache). Slipstream's
answer is to validate the summary against the agent's own next-k steps and repair on
rejection — rejections fire in only 1.0–8.5% of cases, so validation is cheap.

And per [the audit](../../audit/ai-backend-smells/FINDINGS.md), we may not need to build the
mechanism: `context/tool_result_admission_gate` already implements admission plus an offload
writer, tested, unwired.

## 5. A cheap intervention we did not have, and a bug it exposes

NVIDIA's entire measured delta came from telling the model that a truncated read **was**
truncated. Read-file tests went 0/3 → 3/3.

We truncate tool error messages with a bare slice and **no marker**:

```python
return message[: cls._MAX_ERROR_MESSAGE_LENGTH]
```

([tool_outcomes.py:159](../../../services/ai-backend/src/agent_runtime/execution/tool_outcomes.py:159))

Meanwhile `ErrorSanitizer._truncate` in the same service **does** append `…[truncated]`. Two
truncation paths, inconsistent, and the silent one is on the tool-result path the model reads
every turn. A model given a sentence cut mid-word has no way to know it is incomplete.

**New item, P1-4: signal every truncation.** Effort S, and it composes with P1-1 (real error
text) since both act on the same message.

## 6. On uniform `xhigh`

LangChain's result used a **reasoning sandwich** — xhigh for planning, high for
implementation, xhigh for verification — chosen because uniform maximum effort fought the
timeout budget. Since output is ~84% of spend at `xhigh` ([PRD.md](PRD.md) §3.1a), varying
effort by phase is simultaneously the accuracy play and the cost play.

We cannot express this yet: effort is fixed per run. The catalog-driven ladder shipped in
`1287f5a7` is a precondition, not the feature. **Worth a Phase 2 item once per-phase effort
is expressible — and worth pricing before assuming uniform `xhigh` is the right benchmark
configuration.**

## 7. What nobody has published

No result was found for MCPMark specifically, for Deep Agents on MCPMark, or for any harness
tuned against MCP-server CRUD tasks. The nearest neighbour is §4's 50-task MCP benchmark.
So the MCPMark numbers in [PRD.md](PRD.md) §5 have no external anchor and remain modelled —
[EXPERIMENTS.md](EXPERIMENTS.md) is the only thing that will make them real.
