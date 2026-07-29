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

| Phase      | Contents                                                                                                          | State |
| ---------- | ----------------------------------------------------------------------------------------------------------------- | ----- |
| Foundation | `context_origin.py` — ContextOrigin, lifecycle, registry, declare/read seam                                       | —     |
| Build      | tool footprints + declarations · snapshot + token counter · message classifier · deepagents adapter · persistence | —     |
| Integrate  | `ModelInvocationMiddleware` hook, capture + reconcile, fail-open guard                                            | —     |
| Gate       | AST conformance gate + pinned golden inventory (the keystone)                                                     | —     |
| API        | `/v1/agent/runs/{id}/context`, conversation latest, SSE event, facade proxy                                       | —     |
| Verify     | full-suite regression sweep + adversarial invariant review                                                        | —     |

Phase states are updated as they land. See git log on this branch for what is actually
committed.

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
