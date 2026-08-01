# PRD-04 — A recovered failure stops shouting

**Severity:** P1 · **Depends on:** nothing (Wave C — land in parallel) · **Coordinates with:** [PRD-03](./PRD-03-transcript-density.md) · **Surface:** `thread-canvas/ToolCallCard.tsx`, `services/ai-backend/.../presentation_templates.py`

## 1. Problem statement

In the captured session:

```
R  Calling read_file            2.1s
   0xCopilot couldn't complete this step.        ← red, permanent
```

Two cards later the agent recovered, created the file, and reported success. The
run **succeeded**. The red line is still there, forever, in the transcript.

It is also content-free. It names no reason, no cause, and offers no action. A
user reading it learns only that something, somewhere, went wrong — which the
subsequent success then contradicts.

Neither reference leaves a red error standing after recovery.

## 2. Current state

### 2.1 The front end: error styling is terminal-state-blind

[ToolCallCard.tsx:81–84](../../../packages/chat-surface/src/thread-canvas/ToolCallCard.tsx#L81):

```ts
const summary =
  toolCall.status === "error"
    ? (toolCall.errorMessage ?? toolCall.summary)
    : toolCall.summary;
```

and [:107](../../../packages/chat-surface/src/thread-canvas/ToolCallCard.tsx#L107):

```ts
style={toolCall.status === "error" ? errorSummaryTextStyle : summaryTextStyle}
```

with `errorSummaryTextStyle` = `{...summaryTextStyle, color: "var(--color-danger)"}`,
plus a `!` glyph in `--color-danger` and the label `Failed`
([:318–327](../../../packages/chat-surface/src/thread-canvas/ToolCallCard.tsx#L318)).

The card knows exactly one thing: _this step's_ status. It has **no idea whether
the run recovered**. There is no notion of a superseded, retried, or recovered
step anywhere in `ToolCallEntry`. So a step that failed and was worked around is
styled identically to a step that killed the run.

There is also **no retry affordance** on a tool card. `rg "retry"` across
`thread-canvas/` finds retry only in the staged-table bulk-apply surface
(`tc-bulk-retry`) and in an approvals comment — nothing on `ToolCallCard`.

### 2.2 The back end: the copy is a _template default_, not a paraphrase

The string is a literal, at
[presentation_templates.py:78](../../../services/ai-backend/src/agent_runtime/api/presentation_templates.py#L78):

```python
DEFAULT = ("Step failed", "0xCopilot couldn't complete this step.")
```

It sits at the bottom of a genuinely good taxonomy — ten typed codes with
specific, human copy:

| Code                     | Summary                                                            |
| ------------------------ | ------------------------------------------------------------------ |
| `TIMEOUT`                | This step took too long and was stopped.                           |
| `PERMISSION_DENIED`      | 0xCopilot isn't allowed to do this.                                |
| `EXTERNAL_SERVICE_ERROR` | The connected app didn't respond.                                  |
| `TOOL_EXCEPTION`         | The tool reported an error and didn't return a result.             |
| `TOOL_TIMEOUT`           | The tool took too long to respond and was stopped.                 |
| `TOOL_RUN_TIMEOUT`       | 0xCopilot ran out of time before this tool finished.               |
| `TOOL_RUN_ABANDONED`     | 0xCopilot lost track of this step and stopped it.                  |
| `TOOL_CANCELLED`         | This step was cancelled before it could finish.                    |
| `RUN_WORKER_LOST`        | 0xCopilot stopped this run because the worker became unresponsive. |
| **`DEFAULT`**            | **0xCopilot couldn't complete this step.**                         |

Seeing `DEFAULT` in a real session means the failure carried **no
`payload["error_code"]`, or one that is not in the table**. The resolver:

```python
@classmethod
def for_code(cls, code: str | None) -> tuple[str, str]:
    if not isinstance(code, str):
        return cls.DEFAULT
    upper = code.strip().upper().replace("-", "_")
    return getattr(cls, upper, cls.DEFAULT)
```

**The fallthrough is completely silent.** No log, no counter, no event field.
Which means: the docstring says _"Add codes here as new failure modes show up in
`payload["error_code"]`"_ — but nothing in the system ever tells anyone that a
new failure mode showed up. The taxonomy cannot grow from evidence, so it decays
toward DEFAULT and the user reads generic copy for a specific failure.

(Minor, but real: `getattr(cls, upper, …)` resolves against **every** class
attribute, so a code named `FOR_CODE` or `DEFAULT` would return something
nonsensical rather than the default. Use an explicit dict.)

## 3. Goals & non-goals

**Goals**

- A failure the run recovered from reads as history, not as an alarm.
- A failure that actually broke the run stays loud.
- The user can retry a failed step.
- Unmapped error codes become **visible to us**, so the taxonomy can grow.

**Non-goals**

- Rewriting the ten existing template strings. They are good.
- Auto-retry. This PRD adds a user-initiated affordance, not a policy.
- Reworking the effects/error subsystem in `agent_runtime`.

## 4. Design decisions

**D-4.1 — Add a recovery dimension to the projection, not to the styling.** The
card should not guess. `ToolCallEntry` gains
`outcome: "failed" | "recovered" | "superseded"` for error-status entries,
derived in `eventProjector` from what happened _after_ the failure in the same
run. Styling then reads a fact instead of inferring one.

**D-4.2 — "Recovered" means the run reached a terminal success after this step
failed.** That is the honest, cheap definition and needs no new backend event.
Refinement (matching a retry to its original call) is a follow-up, not a blocker.

**D-4.3 — Recovered failures are muted, not hidden.** Colour drops from
`--color-danger` to `--color-text-muted`; the glyph goes from `!` to a neutral
mark; the label goes from `Failed` to `Retried`. The detail — including the full
error — stays in the disclosure. We are lowering volume, not deleting evidence.

**D-4.4 — Instrument the fallthrough before extending the taxonomy.** Adding
codes blind is guesswork. Ship the counter + log first, watch what actually
arrives, then map. This is one small change that converts a decaying taxonomy
into a growing one.

**D-4.5 — Retry is a composer prefill, not a new runtime verb.** The cheapest
honest affordance: "Retry this step" seeds the composer with a re-attempt
instruction referencing the tool and args. It requires no new backend contract,
no idempotency story, and no partial-state reasoning. A true runtime-level retry
is a much larger design and is explicitly out of scope.

## 5. UX specification

**Failed and unrecovered** (unchanged from today):

```
 R  read_file                          2.1s      ! Failed
    The tool reported an error and didn't return a result.      ← --color-danger
```

**Failed but recovered:**

```
 R  read_file                          2.1s      ↻ Retried
    The tool reported an error and didn't return a result.      ← --color-text-muted
```

- Same card, same geometry, same disclosure contents. Only tone changes.
- Inside the disclosure, the `error` `DetailRow` keeps `--color-danger` — the
  detail view is where the full truth belongs.
- Group summary (PRD-03) shows a muted `1 retried`.

**Retry affordance** — in the expanded disclosure of an unrecovered failure only,
a single text button `Retry this step`. Not on recovered steps (the agent already
did), not on the collapsed header (it would compete with the disclosure toggle).

**Copy.** No new strings in the front end. The card renders whatever the backend
template produced. If the copy is generic, that is a backend taxonomy gap and
FR-4.7/4.8 is how it gets closed — not a front-end string patch.

## 6. User journeys

**J-4.1 — Sarah's file-creation run (the captured scenario).**
`read_file` fails; the agent works around it; the run succeeds. The card is muted
grey and reads `↻ Retried`. Sarah's eye goes to the answer, not to a red line
contradicted by the success below it.
_Today: permanent red, mid-transcript, on a successful run._

**J-4.2 — A run that genuinely fails.**
The final tool call fails and the run terminates. The card stays red, `! Failed`,
and PRD-03 keeps the group expanded on it. Sarah expands and sees
`The connected app didn't respond.` She clicks **Retry this step**; the composer
is seeded with a re-attempt she can edit before sending.

**J-4.3 — Marcus hits an unmapped failure.**
A new failure mode returns `error_code: "MCP_HANDSHAKE_REJECTED"`. Today: the
DEFAULT string, silently. After this PRD: he still sees the DEFAULT string this
time — but a counter increments and a warning logs the unmapped code. It shows up
in triage, someone adds three lines to `_ErrorMessage`, and the next user gets
specific copy. **The taxonomy grows from evidence instead of decaying.**

**J-4.4 — Scrubbing back to before the recovery.**
Marcus scrubs to a point where the step had failed and the run had not yet
recovered. At that cursor the outcome is `failed`, so the card is red — correct,
because at that moment in time it _was_ an unresolved failure. Time-travel stays
honest.

**J-4.5 — Screen-reader user.**
The status group's `aria-label` reads `Retried` instead of `Failed` for recovered
steps, so the announcement matches the visual tone and the truth.

## 7. Functional requirements

| ID      | Requirement                                                                                                                          |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| FR-4.1  | `ToolCallEntry` gains an `outcome` field for error-status entries: `failed` \| `recovered`.                                          |
| FR-4.2  | `eventProjector` derives `recovered` when the run reached terminal success after the step failed. Pure function of the event array.  |
| FR-4.3  | `outcome` is cursor-correct under scrubbing — `projectAt(seq)` yields `failed` at a cursor before the recovery.                      |
| FR-4.4  | Recovered steps render `--color-text-muted`, a neutral glyph, and the label `Retried`. Unrecovered keep today's danger treatment.    |
| FR-4.5  | The `error` `DetailRow` inside the disclosure keeps `--color-danger` in both cases.                                                  |
| FR-4.6  | An unrecovered failure's expanded disclosure shows `Retry this step`, which seeds the composer. Recovered steps show no such button. |
| FR-4.7  | `_ErrorMessage.for_code` uses an explicit mapping dict, not `getattr` on the class.                                                  |
| FR-4.8  | A code that falls through to `DEFAULT` emits a structured warning **and** increments a counter carrying the unmapped code.           |
| FR-4.9  | A `None`/absent code is distinguished from an unmapped code in that instrumentation — they are different bugs.                       |
| FR-4.10 | No new user-facing strings in `chat-surface`. Copy stays backend-owned.                                                              |
| FR-4.11 | `aria-label` on the status group matches the rendered label.                                                                         |

## 8. Non-functional requirements

- **NFR-4.1** The recovery derivation is O(n) in one pass over the projected
  events, memoised with the rest of the projection. No second traversal per card.
- **NFR-4.2** No new SSE event type and no runtime API change. This is a
  projection refinement plus a logging change.
- **NFR-4.3** The unmapped-code log must not include payload contents — the code
  only. Error payloads can carry user data.

## 9. Acceptance criteria

- [ ] Event fixture: tool fails at seq 4, run succeeds at seq 9 → card renders
      `Retried`, muted.
- [ ] Same fixture, run terminates `failed` → card renders `Failed`, danger.
- [ ] `projectAt(6)` on the first fixture yields `outcome: "failed"`.
- [ ] Expanded disclosure of an unrecovered failure contains a retry control;
      a recovered one does not.
- [ ] Clicking retry seeds the composer with a re-attempt referencing the tool.
- [ ] `_ErrorMessage.for_code("FOR_CODE")` returns `DEFAULT` (regression test for
      the `getattr` hazard).
- [ ] `for_code("MCP_HANDSHAKE_REJECTED")` returns `DEFAULT`, logs a warning
      containing the code, and increments the counter.
- [ ] `for_code(None)` is instrumented distinctly from an unmapped string.
- [ ] Every mapped code still returns its existing tuple — byte-identical
      (freeze test over the table).
- [ ] `cd services/ai-backend && .venv/bin/python -m pytest` green.
- [ ] `npx vitest run --root packages/chat-surface` green.

## 10. Open decisions

| ID    | Question                                                                 | Recommendation                                                                                                                                                                 |
| ----- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| OD-41 | Should `recovered` require matching the retry to the _same_ tool + args? | Not in v1. "Run succeeded after this failed" is honest and cheap. Tighten later if it proves too loose.                                                                        |
| OD-42 | Should a recovered step collapse out of the transcript entirely?         | No. Muting is the right volume; deleting evidence of what the agent tried is a trust problem.                                                                                  |
| OD-43 | Is a true runtime-level retry worth doing?                               | Probably, eventually — it needs idempotency and partial-state design. Out of scope; D-4.5 is the cheap 80%.                                                                    |
| OD-44 | Where should the unmapped-code counter surface?                          | Wherever the service's existing metrics go. If there is no metrics sink yet, the structured warning alone still closes the "invisible" half — do not block on building a sink. |
