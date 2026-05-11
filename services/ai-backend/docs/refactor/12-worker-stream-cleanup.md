# Refactor PRD — Worker streaming pipeline cleanup (P15) — **RETRACTED**

**Status:** Retracted (rewritten 2026-05-11 after reading the code)
**Original claims:** (1) `approval_recognisers.py` is a stream pattern-matcher that should be replaced by source emission; (2) `tool_call_ledger.py` duplicates `ToolInvocationStorePort`; (3) the four `stream_*` channel handler files are over-split.
**Why retracted:** All three claims were wrong. The original PRD was drafted from diagrams and named the files correctly but described their roles incorrectly. The code review found these are well-designed components with clear, distinct responsibilities.

---

## What the original PRD got wrong

### Claim 1 — `approval_recognisers.py` recognizes approval requests on the stream

**Wrong.** [`runtime_worker/approval_recognisers.py`](../../src/runtime_worker/approval_recognisers.py) does not look at the LangGraph stream at all. It is a **synchronous, server-side projection of tool-call arguments into approval-card param rows** (PR 4.4.6.2 / 4.4.6.4). Reads `arguments: Mapping[str, object]`, returns `tuple[ApprovalParam, ...]`. Vendor recognizers for Slack, GitHub, Linear, Notion, Atlassian, each contributing the labels that make the consent card readable (`Repo: acme/api · #42` instead of three separate rows).

Architecture is already excellent:

- `ApprovalParamRecogniser` abstract base with `vendor_tokens` + `recognise` + optional `reversibility`.
- `_normalize_server_name` strips MCP transport decoration (`mcp_*`, `*_mcp`, `*_com`).
- `ApprovalParamRecogniserRegistry` dispatches by first-match.
- Adding a vendor is "subclass + add to tuple" — no edits to catalog, schema, or FE.

**This is the kind of pattern a staff engineer would write.** Substitution-friendly, registry-based, vendor logic isolated. Nothing to refactor.

### Claim 2 — `tool_call_ledger.py` duplicates persistence

**Wrong.** [`runtime_worker/tool_call_ledger.py`](../../src/runtime_worker/tool_call_ledger.py) is 139 LOC of in-flight tracking with a single, well-scoped purpose stated in its own docstring:

> Records every `tool_call_started` event the worker emits and clears the entry when a matching `tool_result` event fires naturally. When the run hits a terminal failure path (asyncio.timeout, unhandled exception), the handler iterates `unsettled()` and emits a synthetic terminal `tool_result` for each entry — preventing orphaned "Running" cards from sticking on the client when the run failed before LangGraph could close the loop.
>
> The ledger is per-run, in-memory, lifecycle-scoped to a single run handler invocation. Crash recovery (worker death) is the reaper's job (Phase 3) and relies on the persisted `runtime_tool_invocations` projection rather than this in-memory ledger.

**It is explicitly NOT a source of truth.** Persistence is the source of truth (`runtime_tool_invocations`). This ledger is the _terminal-failure cleanup tracker_ that lets the worker emit synthetic `tool_result` events so the UI doesn't strand "Running" cards on an uncaught exception. Without it, every uncaught exception would leak ghost cards onto the client.

It additionally carries B8 budget bookkeeping (`charged_calls`, `total_input_tokens`, `record_input_tokens`, `mark_rejected`) — also process-local, also single-purpose.

**Single-source-of-truth is already honored.** The persistence layer wins; this is a cache + a cleanup hook.

### Claim 3 — channel handler files are over-split

**Maybe wrong, certainly unverified.** The four `stream_*` files are 102–658 LOC each:

| File                  | LOC                        |
| --------------------- | -------------------------- |
| `stream_parts.py`     | 102                        |
| `stream_messages.py`  | 422                        |
| `stream_tools.py`     | 634                        |
| `stream_subagents.py` | 658                        |
| `stream_events.py`    | 940 (`StreamOrchestrator`) |

These are not "five lines of switch-case spread across four files." They contain substantial per-channel logic. The original claim ("collapse to one") would have produced a single 1700+ LOC module routing four very different streams. That would have _reduced_ clarity, not increased it.

A merge would be appropriate only if these files share substantial code — and that needs a code-level read to decide, not a diagram-level guess.

### What about `tool_observations.py` and `run_metrics.py`?

Not read in detail (411 LOC and 613 LOC respectively). The diagram description ("worker-side derived state of tool observations" / "AssistantRunMetrics + TokenUsageExtractor") suggests purpose-built modules, but I haven't verified. They are out of scope for this retraction — if there's a real smell there, it deserves its own investigation with code reading first.

---

## Decision

Retract P15. The original PRD's deletion targets are all working code that earn their place.

The roadmap entry for P15 in [`00-roadmap.md`](00-roadmap.md) should be removed or marked **Retracted**.

---

## If you want to look harder

If the team still feels the worker streaming pipeline is over-fragmented, the right next step is **not** a deletion PRD. It's:

1. Read `stream_events.py` (940 LOC `StreamOrchestrator`) end to end.
2. List the responsibilities it actually carries; map each to a per-channel file.
3. Identify any logic that appears in multiple `stream_*` files (true duplication) vs logic that appears once per channel by necessity (true separation).
4. Then draft a PRD if (3) finds duplication.

That investigation belongs in a separate PR titled something like "Worker streaming pipeline review" — not a refactor PRD, an audit doc.

---

## Lesson recorded (for the audit doc itself)

The original audit ([`docs/architecture/refactor-audit.md`](../architecture/refactor-audit.md) §5.1, §5.2, §5.3) made three claims based on diagram + file names without reading the code. Two were demonstrably wrong; the third was unverified. The audit doc's own preamble says _"No source files were read. Every finding is a hypothesis derived from the documented design; every claim should be verified in code before any refactor is committed."_ — and this PRD's retraction is the reason that preamble exists. Future PRDs should not promote a hypothesis to a recommendation without that verification step.
