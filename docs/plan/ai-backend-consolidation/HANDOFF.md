# Handoff — MCPMark harness + ai-backend consolidation

Everything produced in this session, what is true, what is merged, and what to do next.
Written so someone with no context can pick it up.

**Start here:** [TASKS.md](TASKS.md) is the executable plan. This document is the map.

---

## 1. What shipped

| Commit      | What                                                                                                                                                                     |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `1287f5a7`  | **Only production code change.** Parse each model's reasoning-effort ladder from models.dev; add `max` to `ModelReasoningEffort`. `xhigh` is now expressible end to end. |
| `98432285`  | PR #492 merged to `dev` — the above plus all planning/audit docs                                                                                                         |
| `8ec77b16`  | Step-by-step execution plan                                                                                                                                              |
| `b8b95208`  | The orphan-reading correction (see §4)                                                                                                                                   |
| this commit | **T0.1** — `make verify-ai-backend`                                                                                                                                      |

## 2. The documents, and what each is for

**Benchmark programme** — `docs/plan/mcpmark-optimization/`

- `PRD.md` — first-principles cost/latency/accuracy model, findings, per-intervention estimates.
- `EXPERIMENTS.md` — measurement protocol. **Pre-registered predictions with falsifiers.**
- `COMPONENTS.md` — every intervention as a swappable seam; the ablation matrix.
- `RESEARCH.md` — six published results with baselines. **Read before scheduling any of it.**
- `PLAN.md` — phased implementation.

**Consolidation programme** — `docs/plan/ai-backend-consolidation/`

- `PLAN.md` — the three constraints as enforced gates, five phases.
- `TASKS.md` — **the executable task list.** One task, one commit, one merge.
- `HANDOFF.md` — this file.

**Audit** — `docs/audit/ai-backend-smells/`

- `FINDINGS.md` — what exists, is tested, and never runs.
- `DELETE-REPLACE.md` — what should not exist.
- `REPLACEMENTS.md` — library inventory.
- Scanners: `tools/ai-backend-smells/{orphans,smells}.py`, re-runnable.

## 3. The findings that matter, ranked

1. **Three gates would floor an MCPMark score at ~0 regardless of model.** `recursion_limit`
   never set (LangGraph default 25, vs a 16.2-turn mean); the per-tool-name call budget is 10
   scaled ×0.5/1/2 by reasoning depth, while every MCP call shares the `call_mcp_tool` name;
   and `write=ask` has no unattended path.
2. **`McpOperationAdapter` discards the connector's error text.** `extract_error_text` already
   does the extraction, has five unit tests, and has no caller. Estimated +15–30pp.
3. **Production has no durable graph state.** `runtime_checkpointer()` gives desktop an
   `AsyncSqliteSaver` and _every server deployment_ a process-local `InMemorySaver` — its own
   docstring says so. `langgraph-checkpoint-postgres` is not installed. **This is a production
   bug, not a cleanup item.**
4. **We hand-rolled ~11,900 LOC of MCP across two services** without the official `mcp` SDK or
   `langchain-mcp-adapters` — whose documented default is returning a failed call as
   `ToolMessage(status="error")` so the agent self-corrects. That is finding 2, as a library
   default.
5. **The approval gate asks users to approve read-only calls.** `mode_for_tool` (per-tool
   classifier) is unwired, so the axis is fixed once from the umbrella tool's coarse
   side-effect class.
6. **Memory policy never evaluates.** Policies are attached to routes; `authorize()` is
   reachable only through `ensure_authorized`, which has no callers. Role checks and
   prompt-injection rejection do not run.
7. **47,063 LOC implements one 116-method port three times.** This already caused an outage:
   the suite ran the adapter that could not exhibit the bug.
8. **`tenacity` is pinned with zero import sites** while retry/backoff is hand-rolled.

## 4. Where I was wrong — read this before trusting the rest

Six corrections, all recorded in place rather than edited away.

| Claim                                                   | Reality                                                                                                                    |
| ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `Messages.Loader.PROTOCOL_ERROR` is what the model sees | Dead constant. The live one is `_CONNECTOR_PROTOCOL_ERROR`.                                                                |
| The tool surface is `ls/read/glob/grep/write/edit`      | Copied from a docstring. Real names are `read_file`/`write_file`/`edit_file`.                                              |
| I checked `execution/`                                  | Read 8 of 25 files; missed `model_invocation/` — a live routing/failover/circuit-breaker subsystem, and **a fourth gate**. |
| `MemoryAccessPolicy` is unreachable                     | No such class. Real name `MemoryPolicyAuthorizer`, and it _is_ used — only the evaluation chain is unreachable.            |
| Compaction is a cost lever worth −6–8%                  | On MCP-tool agents it is an **accuracy** lever: 71.0% → 91.6% with 64% fewer tokens.                                       |
| `capabilities/` is mostly MCP and legitimately ours     | MCP is 11%; it is a client for our own proxy; the SDK and adapter exist and are not installed.                             |
| ~8,600 LOC of orphans are deletable                     | **~1,400, possibly zero.** See below.                                                                                      |

**The last one is the important one.** The scanner measures _unreachable_. It cannot measure
_abandoned_. Checking `git log` showed five of ten modules were written in the last week;
**reading** all ten showed the other five name what they wait for —
`code_tool_adapter` waits on a backend route "once P10-A2 ships", `patch_plan` on "an eventual
C1 overlay transaction", `inbox_fallback` on a PRD'd caller.

**This codebase lands components before their wiring and has no ledger for the debt.** That is
why ten modules were invisible for up to 2.5 months. The fix is the ledger, not the delete key.

## 5. Three orphans are harness levers, not waste

- **`tool_result_admission_gate`** bounds `m`, the quadratic cost term. It hooks
  `RuntimeControlMiddleware`'s result sweep, so coverage follows graph topology rather than who
  remembered to wrap a tool — reaching Deep Agents' injected tools and subagent copies, which a
  `BaseTool` decorator cannot. **Wire it.**
- **`provider_hints`** supplies the MCP `readOnlyHint`/`destructiveHint`/`idempotentHint` tier
  that `mode_for_tool` needs. Together they fix finding 5.
- **`context_origin_conformance`** makes the occupancy baseline complete, which the PRD's
  Task 0.1 depends on — and it is the same shape as the G1 gate `PLAN.md` proposes _building_.

## 6. What to do next

Ordered. `TASKS.md` has the detail.

1. **T0.2** — orphan scanner as a ratchet (fails when the list _grows_).
2. **T0.3** — single-source-of-truth gate, shipped with its fix (`tool_call_budget` 5 vs 10).
3. **T1.1** — the pending-wiring ledger.
4. **T2.2** — truncation marker. Trivial, measured effect elsewhere.
5. **T2.1** — wire `extract_error_text`. **Needs security review** (redaction boundary).
6. **T3.2** — install `langgraph-checkpoint-postgres`. **Do this early; it is a real bug.**

Then Stage 4 (MCP consolidation) and Stage 5 (adapter collapse), each needing its own
breakdown. Stage 4 opens with a question, not code: **is descriptor-revision tracking a product
requirement, or just cache coherence for the proxy we are removing?**

## 7. Rules learned the hard way

- **Run it, don't read it.** Every wrong claim in §4 came from trusting prose.
- **Re-verify before deleting.** Finding 0 went stale in a day when `f0c84471` landed between
  the scan and the write-up.
- **Unreachable ≠ abandoned.** Check `git log --diff-filter=A` and _read the docstring_.
- **Never `git commit --amend` after a hook aborts.** `ruff-format`/`prettier` modify files and
  fail the commit; recover with `git add -A && git commit` again.
- **Use pinned `prettier@3.8.3`.** A different version reformats untouched files.
- **`GH_CONFIG_DIR` must point at the main checkout**, not a worktree, or `gh` acts as the
  wrong account.
- **Estimates in the PRD are modelled, not measured.** `EXPERIMENTS.md` says what would
  falsify each.

## 8. Verification

```bash
make verify-ai-backend
```

Baseline at `dev` `98432285`: **9,861 passed · 141 skipped · 2 deselected**, ruff clean,
`api-types` typechecks.

The count is _expected to move_ — a task that deletes a module deletes its tests. The rule is
that the delta is explained, not that it is zero.

**Known-red and not ours:** `apps/frontend` typecheck fails on `dev` (filesystem-bypass
symbols). Verified pre-existing by reverting this branch's only TS edit.
