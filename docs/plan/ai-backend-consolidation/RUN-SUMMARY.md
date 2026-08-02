# ai-backend consolidation — autonomous run summary (2026-08-02)

A single unattended session executed the consolidation program via workflows in
four phases. This records what shipped, what was corrected, and what is parked.

## The headline

**Real value shipped is the Phase-1 wirings/fixes. The program's big-ticket
"replace / collapse" items shrank dramatically under source-level scrutiny — the
same overstatement pattern, three times.** Catching that (and _not_ executing the
misguided large rewrites) is itself a primary outcome.

| Audit claim                                     | Claimed     | Reality (verified at source)                                                                                                                  |
| ----------------------------------------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Delete dead orphan modules (§B)                 | ~8,600 LOC  | ~160 LOC ([PENDING-WIRINGS.md](../../audit/ai-backend-smells/PENDING-WIRINGS.md))                                                             |
| Add compaction / SummarizationMiddleware (T2.4) | new work    | **already running** — deepagents installs it on every agent                                                                                   |
| Adapter collapse to one SQL impl (§C)           | ~25–30k LOC | ~5–8k, and the SQL approach is **wrong-direction** ([ADAPTER-COLLAPSE-REALITY.md](../../audit/ai-backend-smells/ADAPTER-COLLAPSE-REALITY.md)) |

## Merged to `dev` this run

**Phase 1a/1b (7 PRs):**

- **#501** — orphan scanner skips `__main__`-guarded entrypoints (prune 2 false-positives)
- **#502** — wire `provider_hints`: MCP annotations narrow concurrency, default-on, narrow-only (T1.3a)
- **#503** — delete 3 superseded/stale/redundant modules (T1.2)
- **#504** — durable `AsyncPostgresSaver` checkpointer for server (T3.2)
- **#505** — wire `approval_expiry_sweeper`, **default-OFF** (T2.3)
- **#506** — adopt `tenacity` at `RetryingTool` (T3.1) — it is actually required-by langchain-core, so "drop" would have been wrong
- **#507** — surface connector error text to the model + truncation marker (T2.1/T2.2), predicted +15–30pp

**Phase 3:** **#509** — the adapter-collapse reality doc.

Plus this Phase-4 cleanup: pruned the now-wired `provider_hints` from the orphan
baseline; the full ai-backend suite is green on merged `dev` (gate exit 0).

## Phase outcomes

- **Phase 1 — done.** The wirings + fixes above. Highest-value: T2.1 error-text
  (+15–30pp), the durable checkpointer, provider_hints.
- **Phase 2 — no merge, correctly.** Compaction is already active (deepagents
  `SummarizationMiddleware`, triggers at 0.85 + offload). The memory and subagents
  swaps are entangled (our `context/memory` carries offload/token-budget/subagent-trace;
  `delegation/subagents` is mostly our own additions, not pure duplication). Forcing a
  swap would have been wrong.
- **Phase 3 — analysis, not rewrite.** The specced SQL collapse harvests the wrong
  22%-shared Postgres code, discards the 73%-shared `file`/`in_memory` code, and
  sacrifices the deliberate JSONL-canonical local-first design. The real ~5–8k win
  (a shared `file`+`in_memory` base) is filed for review.
- **Phase 4 — converge/docs** (this summary + the baseline prune).

## The O(N²) question, resolved honestly

Two distinct problems: (1) the **model-visible / provider-side** O(N²) per turn is
**already mitigated** by the active deepagents compaction; (2) the **checkpointer**
O(N²) (a full growing-`messages` snapshot each step) is **still unfixed** — that
compaction is deliberately non-mutating, and PR5 made those writes durable, not
smaller. Options are in the compaction chip.

## Parked for review (chips)

1. **Security review** — the T2.1 redaction lets an unlabeled bare-hex internal id
   (`uuid4().hex` form) survive where the old blunt regex caught it (deliberate, to let
   server resource ids through). Merged under "merge if green"; flagged.
2. **Admission gate** — `tool_result_admission_gate` should land as a stage in the
   Lineara MCP tool-policy pipeline, not the old middleware (deferred T1.3b).
3. **Compaction + checkpointer O(N²)** — close the fallback/tunability gaps in the
   active compaction (needs a human accuracy call) and decide the checkpointer O(N²).
4. **Adapter dedup** — the real `file`+`in_memory` shared-base win (~5–8k), reviewed.

## Guardrails honored

Merge-if-green (admin-merge over the base-branch policy; CI green each PR).
Enable-new-defaults — **except** `approval_expiry_sweeper`, kept OFF (auto-cancelling
approvals unobserved is destructive). Skip/hold blockers → the four chips above.
Per-phase gate = full unit+integration suite on merged `dev`; live desktop-journeys
were reserved for genuinely desktop-visible changes (Phase 1 was server-side/narrow;
Phase 2 produced no merge; Phase 3 no rewrite), and an isolated e2e worktree is built
and ready for the adapter dedup when it is implemented.

## Coordination

The parallel "Lineara" chat owns Stage 4 (MCP → `langchain-mcp-adapters` + a
capability-agnostic tool pipeline). This run stayed out of the MCP client + that
pipeline, deferred the admission gate and the permissions swap to it, and flagged
that T2.1's `operation_adapter.py` edit is on its eventual delete list.
