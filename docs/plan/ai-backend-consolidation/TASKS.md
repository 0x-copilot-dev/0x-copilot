# Execution plan — one task, one commit, one merge to `dev`

Companion to [PLAN.md](PLAN.md). That document says _what_ and _why_. This one is the
**ordered, executable task list**, written so that no step can regress the product.

**Every task is independently shippable.** Nothing depends on unmerged work.

---

## The no-regression protocol

Applied to **every** task, without exception.

### Baseline

```
9,861 passed · 141 skipped · 2 deselected
```

Full `services/ai-backend` suite at `dev` as of `98432285`. Re-establish it at the start of
each task — a stale baseline is how a regression gets attributed to the wrong change.

```bash
cd services/ai-backend && .venv/bin/python -m pytest tests/ -q
```

### The four checks, per task

1. **Suite delta is explained.** Not "still 9,861" — deletions legitimately reduce the count.
   The rule is that the delta equals the tests intentionally removed, and the task records
   the arithmetic.
2. **Lint and format clean.** `ruff check`, `ruff format`, `prettier@3.8.3` (pinned — a
   different version reformats untouched files and reds `lint-and-secrets`).
3. **Contracts typecheck.** `npm run typecheck --workspace @0x-copilot/api-types` whenever
   `packages/api-types` is touched.
4. **Behaviour change is declared.** A task either changes behaviour or it does not. If it
   does, it says what changed and ships a test that fails without the change.

### Two rules that exist because this session already tripped them

- **Re-verify immediately before deleting.** Audit Finding 0 went stale within a day —
  `f0c84471` fixed the exclusion set between the scan and the write-up. Any deletion re-runs
  its reachability check against `HEAD` at the moment of deletion, not against the audit.
- **Never `git commit --amend` after a hook aborts a commit.** `ruff-format` and `prettier`
  modify files and fail the commit; the recovery is `git add -A && git commit` again.

### Merge, per task

```bash
export GH_CONFIG_DIR="$PWD/.gh-cli-0x-copilot-dev"   # from the MAIN checkout, not a worktree
gh auth status                                        # must say 0x-copilot-dev
gh pr create --base dev --title "..." --body "..."
gh pr merge <n> --merge --admin
```

---

## Correction that reshapes Stage 1

[DELETE-REPLACE.md](../../audit/ai-backend-smells/DELETE-REPLACE.md) proposed deleting ten
orphan modules (~8,600 LOC with tests). **Checking when each was added changes the answer.**

| Module                       | Added          | Commits | Verdict                           |
| ---------------------------- | -------------- | ------- | --------------------------------- |
| `context_origin_conformance` | **2026-07-30** | 4       | **2 days old — in flight**        |
| `provider_hints`             | **2026-07-29** | 1       | **3 days old — in flight**        |
| `tool_result_admission_gate` | **2026-07-29** | 2       | **3 days old — in flight**        |
| `patch_plan`                 | **2026-07-27** | 2       | **5 days old — in flight**        |
| `e2_final_conformance`       | **2026-07-26** | 2       | **6 days old — in flight**        |
| `proposal_extractor`         | 2026-05-18     | 1       | aged, never wired                 |
| `inbox_fallback`             | 2026-05-18     | 1       | aged, never wired                 |
| `code_tool_adapter`          | 2026-05-18     | 1       | aged, never wired                 |
| `approval_expiry_sweeper`    | 2026-05-05     | 3       | aged — **wire, do not delete**    |
| `encrypt_existing_columns`   | 2026-05-04     | 3       | aged — migration job, check first |

**Half of them were written in the last week.** "Unreachable" and "abandoned" are not the same
property, and the scanner only measures the first. `tool_result_admission_gate` carries 3,101
LOC of tests against 413 of source and is three days old — that is someone mid-way through
landing a feature, and deleting it would destroy in-flight work.

**Rule adopted: nothing younger than 30 days is deleted without asking its author.** The
audit's ~8,600 LOC estimate for Stage 1 drops to **~1,174 LOC** of genuinely safe deletion.

That is a much smaller number, and it is the correct one.

---

## Stage 0 — Safety net (no behaviour change)

### T0.1 · Pin the verification baseline

Add `make verify-ai-backend` running suite + ruff + typecheck in one command, and record the
current counts in the Makefile comment so drift is visible in a diff.

**Behaviour change:** none. **Suite delta:** 0.
**Why first:** every later task's "no regression" claim is only as good as the command that
proves it.

### T0.2 · Ship the orphan scanner as a ratchet

`tools/ai-backend-smells/orphans.py` exists. Add a test that runs it and fails when the orphan
list **grows** beyond a checked-in baseline. Not a hard zero — that would block on the in-flight
modules above.

**Behaviour change:** none. **Suite delta:** +1 test.
**Why:** stops the next unwired module from being invisible for 2.5 months.

### T0.3 · Single-source-of-truth gate (G2)

Test that fails when one named default has two values. Seed with the known case:
`tool_call_budget` is 5 in `ModelRuntimeConfig`, 10 in `RuntimeExecutionSettings`.

**Ship the gate and the fix together** — a gate that lands red is a gate someone disables.

**Behaviour change:** yes — one of the two defaults changes. Declare which, and why 10 is the
correct one (it is the value the middleware actually enforces).
**Suite delta:** +1 test, plus any test asserting the old 5.

---

## Stage 1 — Almost nothing is dead. Read them.

**Second correction, and it inverts this stage.** The age check said five modules were in
flight. Reading all ten says the other five are too — every one is designed, documented,
tested work **waiting on a dependency that has not shipped**.

| Module                       | What it is actually waiting for                                                                                                           |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `code_tool_adapter`          | its own docstring: the fetcher "will hit the backend's `/internal/v1/tools/{id}/code` internal endpoint **once P10-A2 ships that route**" |
| `patch_plan`                 | "the closed, metadata-only plan that an **eventual C1 overlay transaction** may consume"                                                  |
| `inbox_fallback`             | implements a PRD'd routing rule (`inbox-prd.md` §1.3) with a tenant-configurable window; the scheduler exists, the caller does not        |
| `provider_hints`             | supplies F6.1's `TRUSTED_PROVIDER` tier, which "only `PRODUCT_CATALOG` ever had anything feeding it"                                      |
| `context_origin_conformance` | an AST merge gate; lands the rule that "declaration without a gate is a comment"                                                          |

**The pattern is not waste. It is landing a component before its wiring, with nothing tracking
the debt.** That is why ten modules were invisible for up to 2.5 months — not because anyone
abandoned them, but because "built, not yet wired" is a state this repo has no ledger for.

**So Stage 1 is no longer a deletion stage.** It is: build that ledger, and decide each
pending wiring on its merits.

### T1.1 · A pending-wiring ledger, not a delete list

Convert the orphan baseline from T0.2 into `docs/audit/ai-backend-smells/PENDING-WIRINGS.md`:
per module, what it waits on, and who owns that. A module that cannot name what it waits for
is the only real deletion candidate.

**Behaviour change:** none. **Suite delta:** 0.

### T1.2 · Only genuine deletions, one per task

On current evidence that is **`proposal_extractor`** (620 src + 523 tests, added 2026-05-18,
one commit) and **`encrypt_existing_columns`** (216, a backfill job) — and both still need
their owner asked first. `proposal_extractor` names no blocking dependency in its docstring;
`encrypt_existing_columns` is dead only if the backfill has run everywhere.

**Expected deletion from this whole stage: ~1,400 LOC, possibly zero.** The audit's ~8,600 was
wrong, and the ~1,174 in the first draft of this plan was still too high.

### T1.3 · Three of these would improve harness performance — promote them

Answering the question directly: **yes, three of the "orphans" are harness levers**, and two
of them are things the MCPMark PRD had scheduled as new work.

**`tool_result_admission_gate` → bounds `m`, the quadratic cost term.**
[DELETE-REPLACE.md](../../audit/ai-backend-smells/DELETE-REPLACE.md) §A1 called it a duplicate
of deepagents' `_offload_tool_message_content`. **That was wrong.** Reading it, it solves a
strictly harder problem: it hooks `RuntimeControlMiddleware`'s result sweep, so coverage is "a
property of the graph's topology rather than of who remembered to wrap a tool" — including
Deep Agents' own injected todo/filesystem/execute/task tools and a subagent's private copies,
which a `BaseTool` decorator cannot reach. It is constructible with no arguments, so absence of
a durable store no longer means an unbounded result ("an optional boundary is not a boundary").
And `verify_model_visible` fails closed. **Wire this instead of, or alongside,
`SummarizationMiddleware` — it is the better fit for our topology.**

**`provider_hints` → fixes "reads gate as writes".**
[FINDINGS.md](../../audit/ai-backend-smells/FINDINGS.md) §2b found the per-tool classifier
`mode_for_tool` unwired, so the approval gate fixes its axis once from the umbrella tool's
coarse side-effect class and asks users to approve read-only calls. `provider_hints` is the
missing production source: MCP's own `readOnlyHint` / `destructiveHint` / `idempotentHint`
annotations, already captured per run by `McpToolAnnotationsRegistry`. **`provider_hints` +
`mode_for_tool` together are the fix**, and that is MCPMark's P0-3 gate.

**`context_origin_conformance` → makes the cost baseline trustworthy.**
[PRD.md](../mcpmark-optimization/PRD.md) Task 0.1 measures `S`, `T`, `m` from the occupancy
recorder. If contributors add context without declaring an origin, those tokens land in
`undeclared_tokens` and the baseline is wrong by an unknown amount. This gate is what makes the
measurement complete — and it is the same shape as the G1 gate
[PLAN.md](PLAN.md) §1 proposes **building**.

**Each is its own Stage 2 task with its own verification.** None is free: `provider_hints`
changes who gets asked for approval, and the admission gate changes what the model sees.

## Stage 2 — Wirings (behaviour changes, one at a time)

Each of these makes the product **do something it currently does not**. That is the point, and
it is also the risk, so they land separately with their own verification.

### T2.1 · Wire `extract_error_text` — MCPMark P1-1

`McpOperationAdapter` discards `output` and raises `_CONNECTOR_PROTOCOL_ERROR`
([operation_adapter.py:193](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/operation_adapter.py:193)).
`McpToolCallOutcome.extract_error_text` already does the extraction and has five unit tests.

- Call it at that site.
- Narrow the `\b[0-9a-fA-F]{16,}\b` redaction so resource IDs the server itself returned
  survive, while internal run/org/conversation IDs stay redacted.
- Raise `SAFE_MESSAGE_MAX_LENGTH` for this field.
- Delete the dead `Messages.Loader.PROTOCOL_ERROR`.

**Behaviour change:** yes — the model now sees the connector's real error text.
**Regression risk: information disclosure.** New tests: a Postgres `column "x" does not exist`
reaches the model; a connection string in the same payload does not; a Notion page ID survives;
an org UUID does not.
**Needs security review.** Predicted +15–30pp on MCPMark.

### T2.2 · Add the truncation marker — MCPMark P1-4

`tool_outcomes.py:159` slices with `message[: _MAX_ERROR_MESSAGE_LENGTH]` and no marker, while
`ErrorSanitizer._truncate` in the same service appends `…[truncated]`.

**Behaviour change:** yes. NVIDIA measured 0/3 → 3/3 on read-file tests from exactly this.
**Suite delta:** +1 test. Trivial task, real effect.

### T2.3 · Wire `approval_expiry_sweeper`

Nothing currently expires stale approvals.

**Behaviour change:** yes — approvals now expire. **This is the highest-risk task in Stage 2**:
if the expiry window is wrong, live approvals get cancelled.
**Pre-check:** confirm the window and that expiry is idempotent.
**Ship behind a config default of "off", enable after one deployment observes the count it
would have expired.**

### T2.4 · Configure `deepagents.SummarizationMiddleware`

Cancels MCPMark P2-1 and P2-2 as builds. `trigger`/`keep` are the parameters under test —
[RESEARCH.md](../mcpmark-optimization/RESEARCH.md) §3 is emphatic that HARBOR's wins came from
tuning thresholds, not from adding features.

**Behaviour change:** yes, and the one most likely to lose accuracy — HARBOR's compression gate
cost 4 passes by being wired upstream of a cache.
**Verification:** an identifier created at turn 3 must be exactly recoverable at turn 15.
**Default off.** Enable per the ablation arms in [COMPONENTS.md](../mcpmark-optimization/COMPONENTS.md).

---

## Stage 3 — Dependency hygiene

### T3.1 · `tenacity` — adopt or drop

Pinned at 9.1.4, **zero import sites**. One line either way; pick one.
**Behaviour change:** none if dropped. **Suite delta:** 0.

### T3.2 · Install `langgraph-checkpoint-postgres`, use `AsyncPostgresSaver`

`runtime_checkpointer()` gives desktop an `AsyncSqliteSaver` and **every server deployment a
process-local `InMemorySaver`** — its own docstring says so. Server graph state does not
survive a worker restart.

**This is a production durability fix, not a cleanup.** It stands on its own merits regardless
of what it later lets us delete.
**Behaviour change:** yes — checkpoints become durable.
**Verification:** kill a worker mid-run; the run resumes from its checkpoint.
**Do not delete any worker recovery code in this task.** T3.3 sizes that separately.

### T3.3 · Spike: what does the checkpointer subsume?

Measure how much of `runtime_worker`'s restart-recovery logic `AsyncPostgresSaver` makes
redundant. **Output is a document, not a diff.**

---

## Stage 4+ — The large migrations

Each needs its own task breakdown before starting; listed here for ordering only.

- **Stage 4 — MCP consolidation.** `langchain-mcp-adapters` + `mcp` SDK; ~1,854 LOC replaced
  outright; permissions/approvals/citations move into `ToolCallInterceptor`; **stdio arrives
  free**. Open question first: the 2,253 LOC revision subsystem
  ([PLAN.md](PLAN.md) §2b) — answer whether revision tracking is a product requirement before
  writing any code.
- **Stage 5 — Adapter collapse.** 47,063 LOC, three implementations of a 116-method port, one
  SQL implementation with Postgres and SQLite dialects. Persistence rewrite against the
  desktop default store; stage behind the existing port so both run during migration.

## Sequencing

```
T0.1 → T0.2 → T0.3          safety net, no behaviour change
   ↓
T1.1 → T1.2 → T1.3 → T1.4   deletions, provably unreachable
   ↓
T2.2 → T2.1 → T2.3 → T2.4   wirings, cheapest and safest first
   ↓
T3.1 → T3.2 → T3.3          dependency hygiene; T3.2 fixes a real gap
   ↓
Stage 4 / Stage 5           own breakdowns
```

**T2.2 before T2.1** — the truncation marker is trivial and touches the same message path, so
landing it first means T2.1's security review has one fewer thing in the diff.

## What this plan will not do

- **Delete anything younger than 30 days** without its author confirming it is abandoned.
- **Delete a module whose absence is the bug.** `approval_expiry_sweeper` and
  `tool_result_admission_gate` are wirings, not deletions.
- **Combine a behaviour change with a deletion in one task.** Then a regression has two
  candidate causes and the bisect is useless.
- **Claim a task is done on a green suite alone.** Stage 2 tasks change behaviour by design; a
  green suite proves nothing was broken, not that the intended thing now happens.
