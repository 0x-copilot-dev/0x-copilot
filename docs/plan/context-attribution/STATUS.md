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

## Fixed after the fact: the ledger captured nothing on a default deployment

The shipped ledger measured **zero rows on every default deployment**, and the
whole suite above was green while it did. The chain:

- `FeatureModeSet.f10` defaults to `FeatureMode.OFF`
  (`control_plane/feature_modes.py`).
- `ModelInvocationComposer.compose` returns `None` when
  `release.effective_f10_mode is FeatureMode.OFF`
  (`runtime_worker/model_invocation_composition.py`).
- so no F10 binding is installed, and `awrap_model_call` hits
  `if binding is None: return await handler(request)` before any capture runs.

The sink lived on the F10 binding, so measurement was reachable **only** while an
unrelated feature was enabled. Every occupancy test injected a binding or a sink
directly, which is exactly why 8908 green tests proved nothing about production.

**The fix** gives occupancy its own run-scoped binding
(`observability/context_occupancy_binding.py`), installed by the worker in both
`handlers/run.py` and `handlers/approval.py` **outside** the
`composed_model_invocation is not None` branch. F10 semantics are untouched and
F10 is still `OFF` by default.

Two constraints shaped it:

- **It cannot be a constructor argument.** `llm_seam_conformance` pins the funnel's
  spelling — root gets `ModelInvocationMiddleware()`, every child graph gets the
  _class itself_ as a universal factory — so a child-graph middleware constructs
  itself with no arguments. A context slot is reachable from both; that is why F10
  and the F2 prompt runtime already use one.
- **A "minimal occupancy-only F10 binding" was rejected.** Installing any
  `ModelInvocationRuntimeBinding` makes `awrap_model_call` run the whole F10
  machinery — authority preparation, attempt admission, recovery records, journal
  appends. It would either write journal records on a deployment whose F10 mode is
  OFF, breaking the byte-identical-journal invariant directly, or need null-object
  fakes for `authority_adapter` and `journal` in production. `compose` already
  refuses when `journal is None`.

On the F10-OFF path the request handed to the provider is the **identical object** —
no `override`, no re-attached callbacks, no copy. Usage is read off the
`ModelResponse` via `TokenUsageExtractorRegistry` instead of from a lifecycle
callback, so nothing about the dispatched payload or its digest can shift.

Mutation-verified both ways: restoring `return await handler(request)` fails 17
tests, and awaiting the write inline fails the two latency tests with a named
assertion rather than hanging.

## Known NOT done — read before building on this

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
7. **Occupancy persist is still on the critical path when F10 is ON**, with no
   timeout — on the file store that is an `fsync` under the global store lock.
   Fail-open covers exceptions, not latency. Now **half** fixed: the F10-OFF path
   (every default deployment) defers the write to
   `DeferredContextOccupancyWrites` and returns the provider response
   immediately, with the run handler's `finally` draining the lane. The F10-ON
   path deliberately still awaits inline, because its ordering relative to the
   journal appends is documented and pinned by test ("occupancy is subordinate to
   the journal"); there is nothing to order against when there is no journal.
   Unifying the two — deferring both — is the follow-up that closes this item.
8. **`detail` carries up to 200 chars of MCP-registry-controlled tool text**
   verbatim onto a tenant-readable API. Bounded and printable-only, but it is
   third-party-controlled text on an authenticated surface.
9. **`undeclared_tokens` is large and non-zero on a real run**, which §4.4 says
   should read as a contract bug. First live measurement, turn 1 of a real run on
   the in-memory store: `estimated_input_tokens` **10,154** against a 128k window,
   of which `undeclared_tokens` is **7,935** across 14 segments — 7 built-in tool
   schemas (`glob` 219, `grep` 339, `ls` 116, `read_file` 605, `task` 1,676,
   `web_search` 193, `write_todos` 997) plus a 3,777-token system span. This does
   **not** contradict the AST gate's `undeclared_context_contributors = 0`: the
   gate proves every contributor _we_ compose declares itself, and these schemas
   are composed inside the library, so no code of ours is there to stamp them.
   The golden inventory pins their _text_; nothing declares their _origin_. So the
   two numbers measure different things and both are correct — but until the
   library-owned tool surface is declared through the third-party adapter (which
   already attributes library system text: `default_general_purpose_description`
   matched at 74 tokens), `undeclared_tokens` cannot be read as the defect signal
   §4.4 designs it to be. Not fixed here; surfaced by the fix above.

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

0. **A real run with F10 `OFF` writes occupancy rows.** First on the list because it
   is the one invariant nothing checked, and its absence made every invariant below
   it vacuous. Pinned by
   `tests/unit/runtime_worker/test_real_run_context_occupancy.py`, which drives the
   real worker, real graph and real streaming executor with production feature
   defaults and injects **no** binding, sink or recorder. A test that reaches
   occupancy through an injected collaborator cannot stand in for this one.
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
