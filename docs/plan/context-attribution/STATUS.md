# Context Occupancy Ledger — STATUS

Source of truth for this program. Design doc: [00-solution-design.md](00-solution-design.md).

**Branch:** `claude/composer-context-attribution-946cb5`
**Worktree:** `.claude/worktrees/eager-zhukovsky-57ee80`

## Decisions taken under autonomy

The design doc left five open questions (§10). Answered as follows so implementation
could proceed unblocked — all are reversible, none changes the contract shape:

| Q                                  | Decision                                                                                     |
| ---------------------------------- | -------------------------------------------------------------------------------------------- |
| Runtime fail-closed on undeclared? | **No.** AST gate hard-fails CI; runtime records `UNDECLARED` and never raises (§6.4 wins).   |
| Tool segment granularity           | **Per-tool.** JSONB keeps row size fine and per-tool is what makes the report actionable.    |
| Measure `response_format`?         | **Yes**, for residual completeness.                                                          |
| Cross-run aggregate table          | **Not in v1.** Per-run only.                                                                 |
| Pricing gaps                       | `free_tokens = None` when the model is absent from the pricing catalog. No invented default. |

## Build order

Implemented as a 6-phase workflow, not in the doc's PRD numbering (the doc's order is
logical; this order is what parallelises safely without two agents fighting over a file).

| Phase      | Contents                                                                                                          | State                       |
| ---------- | ----------------------------------------------------------------------------------------------------------------- | --------------------------- |
| Foundation | `context_origin.py` — ContextOrigin, lifecycle, registry, declare/read seam                                       | **done**                    |
| Build      | tool footprints + declarations · snapshot + token counter · message classifier · deepagents adapter · persistence | **done**                    |
| Integrate  | `ModelInvocationMiddleware` hook, capture + reconcile, fail-open guard                                            | **done**                    |
| Gate       | AST conformance gate + pinned golden inventory (the keystone)                                                     | **done** — 0 undeclared     |
| API        | read routes + facade proxy **done**; SSE event contract ships but **has no producer**                             | **partial** — see below     |
| Verify     | full-suite sweep + 3-lens adversarial review + mutation-verified confirmation                                     | **done** — 16 defects fixed |

> **Read item 0 of "Known NOT done" first.** Everything below is true of the code,
> and the ledger records nothing on a default deployment regardless, because its
> capture point sits inside F10's middleware and F10 defaults to off. Found by
> live-running the real graph after merge; no unit test could see it.

## Final verified state

`ai-backend tests/unit` **8908 passed / 107 skipped**. `backend-facade tests`
**373 passed / 1 skipped**. `ruff check` + `ruff format --check` clean across
1570 files. Seam gate + origin gate **44 passed**, `undeclared_context_contributors`
= **0**, inventory 36 rows. Tool-schema digest proven **byte-identical to
`origin/main`** across 9 shape cases by copying main's implementation verbatim
and comparing — prompt-cache identity is intact.

Every adversarial fix was **mutation-verified**: reverted in place, the claimed
test made to fail, then restored. No test was weakened — the only two deletions
in the round were retargeted tests replaced with stronger assertions, and every
removed line was read.

## Known NOT done — read before building on this

0. **THE LEDGER IS DARK ON A DEFAULT DEPLOYMENT.** Occupancy captures nothing
   unless the **F10 model-reliability** feature is on, and `FeatureModeSet.f10`
   defaults to `FeatureMode.OFF`
   (`agent_runtime/control_plane/feature_modes.py:285`). The chain:
   `ModelInvocationComposer.compose` returns `None` when
   `release.effective_f10_mode is FeatureMode.OFF`
   (`runtime_worker/model_invocation_composition.py:178`) → no binding is
   installed → `ModelInvocationMiddleware.awrap_model_call` takes its
   `if binding is None: return await handler(request)` early return before any
   capture happens.

   **Measured, not inferred.** A real run driven through the real worker, real
   Deep Agents graph and real streaming executor (only the chat model faked, per
   `tests/unit/runtime_worker/test_fake_model_run_stream.py`) persisted **zero**
   occupancy rows. Instrumenting the middleware showed `awrap_model_call` was
   called with `binding=None`, while the store did expose
   `append_context_occupancy` — so the cause is the early return, not a missing
   sink.

   This was invisible to the whole test suite because every occupancy test
   injects a binding or a sink directly. §3.1 chose that boundary for good
   reasons (it is the only place the materialized request exists, and the AST
   topology gate proves it is installed on root _and_ subagent graphs) — but it
   did not account for the middleware body short-circuiting on an unrelated
   feature flag.

   The fix is a design decision, not a one-liner: occupancy needs the
   materialized request (always present) and a store (today carried **on the F10
   binding**), so either the middleware gets a store reference independent of
   F10, or the composer emits a minimal occupancy-only binding when F10 is OFF.
   Do not "fix" this by defaulting F10 on — it is an unrelated feature with real
   behavioural weight.

1. **The `context_occupancy` SSE event has no producer.** The event type, payload
   contract, projector branches and public TypeScript contract all ship; nothing
   emits one. A consumer written against the stream will silently receive
   nothing. Use the read endpoints. Wiring it touches the `sequence_no` /
   causal-prefix seal contract, which is why it was not bolted on at the end.
2. **`assembly_record_id` is NULL on every row**, so the designed link to
   `PromptAssembledRecord` does not exist in practice. The naive fix is wrong:
   the F2 handoff carries `PromptRuntimeResult`, not the assembled record.
3. **Postgres retention does not erase occupancy.** The file store (desktop
   default) now does. Postgres needs occupancy added to the explicit
   `RetentionKind` enumeration — there was never a cascade to inherit.
4. **`unattributed_delta` is envelope + drift together, and the envelope
   dominates** (~+5.9% on a realistically-shaped request). Do not read it as
   drift yet. The §9 ±5% bound was never achievable as specified; the bias is
   pinned by test instead.
5. **`counter_source=TOKENIZER` is not provider-authoritative** in this
   deployment — the offline guardrail means one tiktoken encoder for every
   provider (measured).
6. **Zero CI coverage of the Postgres relation.** All 10 postgres store tests
   skip without a live database.
7. **Occupancy persist is on the model call's critical path** with no timeout.
   Fail-open covers exceptions, not latency; on the file store that is an
   `fsync` under the global store lock.
8. **`detail` carries up to 200 chars of MCP-registry-controlled tool text**
   verbatim onto a tenant-readable API. Bounded and printable-only, but it is
   third-party-controlled text on an authenticated surface.

## What this bought

- **The deepagents blind spot is now measured.** 35 library-owned prompt/tool constants,
  **13,812 estimated tokens**, pinned as a golden fixture — a dependency bump that changes
  any of them fails CI naming the constant. Largest: `TASK_TOOL_DESCRIPTION` 1,644,
  `MEMORY_SYSTEM_PROMPT` 1,281, `EXECUTE_TOOL_DESCRIPTION` 693 (excluded on the web
  profile, present on desktop — which is why occupancy is resolved through the live
  profile rather than assumed).

## Things worth a second look at review

1. **`ThirdPartyContextOrigins` deliberately never registers harness profiles.** I nearly
   "fixed" a `None` return by making it call `_ensure_web_harness_profiles_registered()`.
   That would have been a real bug: `register_harness_profile` merges additively, and a
   second registration collapses the per-child `extra_middleware` factory into fixed
   instances — an observability read would have perturbed the topology it measures. The
   refusal to register is correct and load-bearing; the test arranges registration itself.
2. **`services/ai-backend/.coverage` is tracked in git** and churns on every test run.
   Pre-existing, not from this branch, flagged as a separate task.

## Invariants under test

These are the things that must not break, in priority order. Any one of them red means
the ledger is not shippable:

1. **Tool schema digest is byte-identical** to the pre-change
   `_model_tool_schema_revision`. Prompt-cache identity depends on it.
2. **Fail-open** — no path in capture/persist can raise into a model call.
3. **`tests/unit/test_llm_seam_gate.py` stays green** — the canonical LLM funnel and
   graph topology are unchanged by this work.
4. **Single-tracker** — no usage rows written, `Purpose` unextended.
5. **Subagent scope isolation** — occupancy never summed across `graph_scope`.
6. **No content leakage** — segments carry counts and bounded detail only; this is
   exposed over HTTP.

## Notes for review

- The ledger is **purely additive**. It reads the materialized `ModelRequest` and the
  `NormalizedTokenUsage` the `UsageMeter` already receives. It writes one new table.
- The interesting number to look at first once this runs:
  `publish_artifact` + `revise_artifact` + `stage_rowset_write` ≈ **1,337 est. tokens
  resident on every model call**. The mechanism to defer them (`load_tool_spec` +
  `CAPABILITY_DISCOVERY_PROTOCOL`) already exists — this ledger is what turns that from
  a guess into a measurement.
