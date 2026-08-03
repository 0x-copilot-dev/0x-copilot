# Reduction ledger — the deduplicated, adjudicated answer

**Supersedes the "87,632 LOC is dark" headline in
[LEAN-RUNTIME-SPIKES.md](../../plan/ai-backend-consolidation/LEAN-RUNTIME-SPIKES.md) §3.**
That number was materially wrong, and this document is the correction.

11 agents applied one test to every reduction candidate the seven spikes surfaced:
**(1)** what does the block actually provide · **(2)** is that capability already
provided, by a framework or by a different wired path — _verified by importing or
running_ · **(3)** would it work if the flag were flipped. Every claim below was
executed, not inferred.

---

## The correction: "dark" was over-counted, because `off` does not mean off

The flag census treated the E2 rollout lanes as on/off gates. **They are not.**
Executed: `E2RolloutAdmission.permits_all(...)` under an empty env returns **True**, with
every outcome `legacy_passthrough` (`rollout_admission.py:274-276`). A lane that is not
_explicitly enforced_ **permits**. So `MODE=off` ≠ gated off.

Consequences, all verified by running the code:

| Claimed dark                                         | Reality                                                                                                                                                                                                                                                                                                              |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| operation gateway 4,692 + effect stager/commit 4,531 | **~9.2k is LIVE on the shipped desktop path.** The real switch is `SURFACES_V2` (default **True**); `compose(surfaces_v2=True)` really does build an `EffectStager` + `OperationGateway`. Only the workspace overlay/commit lane (~4.5k) is genuinely dark — and via `WORKSPACE_EFFECT_MODE`, not either flag named. |
| browser 1,951                                        | **LIVE.** `build_browser_mcp(...)` returns a `DesktopBrowserMcpProvider` and `dependencies.py:572` appends it to the registry.                                                                                                                                                                                       |
| artifacts publish/revise + `stage_rowset_write`      | **LIVE.** `ARTIFACT_EFFECTS_V2`/`ARTIFACT_DRAFTS_V2` default **true** on desktop; desktop never emits `ARTIFACT_REPOSITORY_MODE` at all.                                                                                                                                                                             |
| citations cluster                                    | **LIVE path — do not touch.**                                                                                                                                                                                                                                                                                        |

**Honest deletable total is ~19–22k, not 87.6k.** This is the seventh headline
overstatement this programme has corrected — and the first one I authored _and merged_.

## Three shipped "wins" that are inert — the same failure mode, three times

A pattern worth naming, because two of these are mine from this programme:

| Change                              | Why it does nothing                                                                                                                                                                        |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **PR #502** `provider_hints`        | Wired at `batch_concurrency_composition.py:502`, inside `_compose`, which line 616 gates behind `F6_BATCH_CONCURRENCY` — set nowhere. **Deleted with F6; it never had production effect.** |
| **PR #505** approval-expiry sweeper | Postgres — the only backend that topology allows — implements **neither port method** the sweeper calls.                                                                                   |
| **`MCP_PER_TOOL_ENABLED=true`**     | Still registers **zero** tools: `RuntimeDependencies.mcp_per_tool_collaborators` is assigned **only in test files**.                                                                       |

All three passed CI. Green tests over an unreachable branch prove nothing about
production. **Any future "wire it" task must end by driving the real path, not by
asserting on an injected collaborator.**

## Clean deletions — adjudicated, deduplicated (~19–22k)

|    LOC | Block                                              | Verdict    | Why                                                                                                                                                                                                                                                  |
| -----: | -------------------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|  8,037 | F3 capability discovery                            | REDUNDANT  | Lineara's MCP filesystem catalog delivers the same search→describe→invoke chain, finer-grained, unflagged, wired every run, in 1,982 LOC. F3's "constant-size replacement" prompt block measures **122 chars larger** than the header it suppresses. |
|  3,908 | F5 context budgeting                               | REDUNDANT  | **No gate reads `f5` at all.** Four ports, zero production implementers. Flipping it changes only a persisted enum. deepagents' summarization already ships unconditionally.                                                                         |
|  2,375 | `file`↔`in_memory` duplicate bodies                | REDUNDANT  | Already in flight (`MaterializedViewStoreBase`). **But smaller than sold: 1,237 identical + 1,138 near LOC — the "73% shared" was name-level, not body-level.**                                                                                      |
|  1,727 | E2 cohort selector + shadow                        | OBSOLETE   | `explicit_enforced` is empty in every shipped config, so every admission is `legacy_passthrough`. Cohort rollout is meaningless for one user.                                                                                                        |
|  1,427 | `answer_verification.py`                           | HALF_BUILT | Only 2 enums imported.                                                                                                                                                                                                                               |
|  1,291 | `delegation/subagents` runner+coordination+handoff | REDUNDANT  | Reachable only from tests. _Correction: the previously-reported 1,499 wrongly included `authority.py` (208), which is live._                                                                                                                         |
|    744 | `presentation_v2_1` E2 shadow lane                 | OBSOLETE   | (inside E2 5,971 — count once)                                                                                                                                                                                                                       |
|    690 | `code_sandbox.py` + `code_tool_adapter.py`         | MIXED      | **Code execution is not a product surface**: deepagents' `execute` is explicitly withheld from the model (`tool_surface.py:43`).                                                                                                                     |
|    531 | `mcp/credentials/desktop.py`                       | OBSOLETE   | superseded by `credentials.backend`                                                                                                                                                                                                                  |
|    301 | `observability/db_statement_metrics.py`            | OBSOLETE   |                                                                                                                                                                                                                                                      |
|    290 | `mcp/files.py`                                     | OBSOLETE   |                                                                                                                                                                                                                                                      |
|    216 | `encrypt_existing_columns`                         | OBSOLETE   | one-shot migration; banned in this service                                                                                                                                                                                                           |
|    151 | `sandbox/providers/langsmith.py`                   | REDUNDANT  | deepagents ships `LangSmithSandbox` (275 LOC); ours adds only `isolation_ready`, hardcoded `False`.                                                                                                                                                  |
| ~10.9k | F6 batch concurrency + `RunSerialAdmission`        | **DONE**   | Deleted. It existed only to grant width back through a lock we installed ourselves; langgraph schedules the turn's tool calls. See below.                                                                                                            |

## Genuine gaps — keep, and in two cases wire

- **`retention_sweeper` + `retention_backfill` (561)** and **`audit_export_verification` (614)** — real **compliance** obligations, currently unwired. **WIRE_IT.**
- **`file/repair.py` + `export_import.py` (1,309)** — zero non-test callers, but they are the desktop's corruption-recovery and export story. **WIRE_IT.**
- **Monty interpreter (1,984)** — genuine, works end-to-end via `copilot --code-mode`; nothing in deepagents provides a sandboxed-Python interpreter. **KEEP_DARK.**
- **Browser read lane (1,086)**, **citations (1,752)**, **artifact builtins (1,051)** — **LIVE. KEEP.**

**The fact that outranks every job flag: the desktop runs no job loop at all.**
`python -m runtime_worker` appears only in two docker composes; `runtime_api`'s
in-process worker constructs zero loops. Every job question is moot for the product.

## Needs your decision (~50k)

- ~~**F6 batch concurrency (10,797)**~~ — **DECIDED AND DONE.** See below.
- **F1 (10,961)** — trajectory projection (~6.8k) is wired and **empirically works**, but
  nothing consumes it; fixture-only eval (~4.2k) has no entry point in any shipped process.
- **Sandbox (8,632)** — HALF*BUILT, not dark: with \_every* documented flag ON,
  `build_sandbox_backend` returns `None` for both providers and the composition resolver
  is an unconditional `return None`.
- **Postgres tree (16,576)** — not free: self-host prod, desktop rollback, and 2 CI
  workflows all build it.
- **F4 (3,367)** — enforcing it caps _every_ run at 6 tool calls / 8 turns.
- **Signed-release lane (3,037)** — HALF_BUILT: **nothing in the repo can sign a
  manifest** (no private-key input, no signing operation), so
  `RUNTIME_HARNESS_RELEASE_CONFIG_PATH` does nothing on its own.

### F6 was the one with a live cost — resolved by deleting it

The finding, as measured here: bare langchain `ToolNode` → **3/3 tool calls
simultaneous**; add our `RuntimeControlMiddleware` with F6 dark → **1/3**.
`RunSerialAdmission` was an exclusive per-run lock and **F6 was the only seam that
widened it**, so F6 being off was not neutral — _we serialized tool calls that langgraph
would have run in parallel_.

**Resolution (owner's call): stop governing concurrency ourselves and let
langgraph/deepagents schedule it natively.** The lock is gone and the apparatus that
existed only to grant width back is deleted — `capabilities/concurrency/` (9,073),
`runtime_worker/batch_concurrency_composition.py` (633),
`batch_approval_authority.py` (155), `control_plane/parallel_admission.py` (221), the
batch-journal event family, and the eval lane that graded it.

Re-measured on a real graph after the change: **4/4 simultaneous**, and the framework's
own `max_concurrency` (already set from `execution.max_parallel_tasks`, default 4) is
what bounds it — the control the deleted subsystem was reinventing.

Two real races were exposed by removing the lock and fixed with it, both proven by
driving a real graph and both regression-tested
(`tests/unit/runtime_worker/test_tool_concurrency_contract.py`):

1. **`ToolCallLedger` iteration vs. insert** — every counting read walks the entry dict;
   a concurrent insert raises `RuntimeError: dictionary changed size during iteration`.
   Observed **deterministically** on the synchronous (thread-pool) tool node.
2. **Per-tool budget check-then-charge** — `check_admit` reads and `record_started`
   writes, and nothing held the pair together. Observed **46 executions against a cap of
   40**. Fixed by `ToolBudgetGuard.admit_and_charge`, which is now the only place a cap
   is enforced.

## Do not plan these twice — Lineara owns them

`mcp/catalog.py` (1,605, their live fix for an empty-success bug), the F8 revision plane
(3,017), the op-gateway bridge (724), the browser effect lane (865), and the per-tool MCP
lane (3,234). Note an unresolved contradiction **inside their own docs**:
`PLAN.md:164` says DELETE the op-gateway; `P2-PLAN:109-110` says PRESERVE it. Worth
settling before either team acts.

## Corrections to earlier documents in this programme

| Document                 | Claim                                        | Correction                                            |
| ------------------------ | -------------------------------------------- | ----------------------------------------------------- |
| LEAN-RUNTIME-SPIKES §3   | 87,632 LOC dark                              | **~19–22k** genuinely deletable; `off` ≠ gated off    |
| LEAN-RUNTIME-SPIKES §3   | browser, artifacts, effects/gateway dark     | **LIVE**                                              |
| ADAPTER-COLLAPSE-REALITY | `file`↔`in_memory` ~5–8k, "73% shared"       | **1.4–2.4k**; the 73% was name-level                  |
| S1-D spike               | "drop 2 of 3 stores"                         | Does not survive: all three build in a shipped config |
| BOUNDARY-AUDIT §4        | `context_contracts` DEAD                     | Transitively **live** via `tool_result_admission`     |
| BOUNDARY-AUDIT §4        | delegation 1,499                             | **1,291** — `authority.py` (208) is live              |
| PENDING-WIRINGS          | `encrypt_existing_columns` precondition live | Corrected: obsolete                                   |

_Method: 11 agents, each scoped to a bounded block, required to verify framework claims
by import or execution and to name overlapping findings so nothing is double-counted._
