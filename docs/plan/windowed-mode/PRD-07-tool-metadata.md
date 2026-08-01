# PRD-07 — Tool metadata consistency

**Severity:** P2 (but the cheapest fix in the program) · **Depends on:** nothing · **Blocks:** [PRD-03](./PRD-03-transcript-density.md) FR-3.8 · **Surface:** `thread-canvas/ToolCallCard.tsx`, `subagents/labels.ts`, `thread-canvas/eventProjector.ts`

## 1. Problem statement

In the captured session the six activity cards showed:

```
Calling ls            (no duration)
Calling write_todos   320 ms
Calling ls            2.2s
Calling read_file     2.1s
Manual file           645 ms
Calling write_todos   387 ms
```

Two defects in six rows: one card has no duration at all, and the unit is
rendered two ways — `320 ms` with a space, `2.2s` without.

Small, but this is precisely what "unfinished" looks like, and in a narrow window
where the metadata column is the only thing besides the title, it is most of what
the user sees.

## 2. Current state

### 2.1 Two formatters, in one package, that disagree

**`ToolCallCard.formatDuration`** — private, unexported
([ToolCallCard.tsx:301](../../../packages/chat-surface/src/thread-canvas/ToolCallCard.tsx#L301)):

```ts
if (durationMs < 1000) return `${Math.round(durationMs)} ms`; // space
if (durationMs < 60_000) {
  const s = durationMs / 1000;
  return `${s % 1 === 0 ? s : s.toFixed(1)}s`;
}
const minutes = durationMs / 60_000;
return `${minutes % 1 === 0 ? minutes : minutes.toFixed(1)}m`;
```

**`formatSubagentDuration`** — exported from the package barrel
([labels.ts:70](../../../packages/chat-surface/src/subagents/labels.ts#L70)):

```ts
if (ms < 1000) return `${ms}ms`; // no space
const seconds = ms / 1000;
if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
const minutes = Math.floor(seconds / 60);
return `${minutes}m ${remainder}s`;
```

They disagree on every axis:

| Input     | `ToolCallCard` | `formatSubagentDuration` |
| --------- | -------------- | ------------------------ |
| 645 ms    | `645 ms`       | `645ms`                  |
| 12 500 ms | `12.5s`        | `13s`                    |
| 90 000 ms | `1.5m`         | `1m 30s`                 |

**And these two cards render adjacently in the same transcript.** The shared
chrome file states its own purpose explicitly
([ActivityCardChrome.ts:3](../../../packages/chat-surface/src/activity/ActivityCardChrome.ts#L3)):

> Tool calls and subagent fleets intentionally share this geometry so a
> conversation can mix the two without the cards reading as separate UI systems.

The geometry is shared. The content inside it is not. A tool card and a subagent
card sitting one above the other will render the same elapsed time in two
different formats.

### 2.2 This exact bug was already fixed once — and the fix stopped short

From [labels.ts:3](../../../packages/chat-surface/src/subagents/labels.ts#L3):

> Before this file existed, SubagentCard and FleetSubagentRow each defined their
> own copy of `jumpLabelForPause` and `formatDuration` (byte-identical), plus a
> constellation of pause-reason → string helpers with subtly different outputs.

Someone already found duplicated duration formatting, consolidated it, and wrote
down why. The consolidation just stopped at the `subagents/` directory boundary
and never reached `ToolCallCard` — which is the other consumer of the very chrome
that exists to make them look like one system.

### 2.3 The missing duration

`durationMs` is populated at three projector sites, all with the same shape
([eventProjector.ts:941, 989, 1045](../../../packages/chat-surface/src/thread-canvas/eventProjector.ts#L941)):

```ts
durationMs: readToolDuration(event.payload?.["duration_ms"]) ?? prior?.durationMs,
```

`formatDuration` then returns `null` for `undefined`, and the card renders no
duration chip at all. So a card shows no timing precisely when **no event in that
tool's chain ever carried `duration_ms`** — the runtime did not emit it.

The projector does hold timestamps for other purposes (`ReasoningGroup`'s caller
synthesises elapsed as `max(updatedAtMs) − min(startedAtMs)`), so a client-side
fallback is available without a backend change.

## 3. Goals & non-goals

**Goals**

- One duration formatter in the package. One format on screen.
- Every settled tool call shows a duration, or the absence is explained.
- Finish the consolidation `labels.ts` started.

**Non-goals**

- Choosing a new visual style for the metadata. Format only.
- Backfilling `duration_ms` in historical persisted events.

## 4. Design decisions

**D-7.1 — `formatSubagentDuration` wins, renamed.** It is already exported from
the barrel, already consumed by three call sites, and already the survivor of one
consolidation. Rename it `formatActivityDuration`, move it to `activity/` next to
`ActivityCardChrome` (the shared home for things both families use), and re-export
under the old name for one release so nothing breaks.

Its behaviour is also better on the merits: `1m 30s` is more readable than `1.5m`,
and `13s` vs `12.5s` for a long call is the right precision — one decimal matters
at 2.2s, not at 12.5s.

**D-7.2 — Delete `ToolCallCard.formatDuration` entirely.** Not "align it" —
delete it. Two functions that must agree will eventually disagree; that is the
lesson `labels.ts` already recorded.

**D-7.3 — Derive duration client-side when the payload omits it.** Fall back to
`updatedAtMs − startedAtMs` from the projection. Prefer the authoritative
`duration_ms` when present; derive only when it is absent.

**D-7.4 — Mark derived durations, quietly.** A derived value measures
_event-arrival_ elapsed, not server-side execution — it includes queue and
transport time. Render it identically but expose `data-duration-source="derived"`
so the difference is inspectable without adding user-facing noise.

**D-7.5 — Instrument the missing payload.** A settled tool call with no
`duration_ms` is a runtime gap. Like PRD-04's unmapped error codes, it is
currently silent. Log it once per run with the tool name so the gap becomes
findable. This is the same lesson as PRD-04 FR-4.8, applied to a second silent
fallthrough.

## 5. UX specification

Format table (the one true format, everywhere):

| Range      | Output      | Example  |
| ---------- | ----------- | -------- |
| `< 1000ms` | `{ms}ms`    | `645ms`  |
| `< 10s`    | `{s.s}s`    | `2.2s`   |
| `< 60s`    | `{s}s`      | `13s`    |
| `>= 60s`   | `{m}m {s}s` | `1m 30s` |

No space before the unit, at any magnitude. Consistent with
`formatSubagentDuration` today, so subagent cards do not change at all — only
tool cards move, and only in the sub-second and multi-minute ranges.

**Placement, type, and colour are unchanged** — `activityCardMetaStyle`,
9px mono, `--color-text-subtle`. This PRD changes characters, not design.

## 6. User journeys

**J-7.1 — Sarah scans six cards in a narrow window (the captured scenario).**
Every card shows a duration, all in the same format. Her eye reads a clean
right-hand column of timings instead of tripping over `645 ms` / `2.2s` /
_(nothing)_.

**J-7.2 — Marcus reads a transcript mixing tool calls and subagents.**
A tool card reading `1m 30s` sits above a subagent card reading `1m 30s`. Today
the tool card would say `1.5m` and the subagent `1m 30s` for the same elapsed
time, and he would have to work out whether those are the same number.

**J-7.3 — A tool completes without `duration_ms`.**
The card shows a derived duration instead of nothing. The DOM carries
`data-duration-source="derived"`, and the run logs one line naming the tool. The
gap is now visible to us and invisible to the user — the right split.

**J-7.4 — A still-running tool.**
No duration is rendered (there is no elapsed yet), the spinner and `Running`
label carry the state. Unchanged from today, and correct.

## 7. Functional requirements

| ID      | Requirement                                                                                                          |
| ------- | -------------------------------------------------------------------------------------------------------------------- |
| FR-7.1  | `formatActivityDuration` is the single duration formatter in `packages/chat-surface`. Grep proves there is no other. |
| FR-7.2  | `ToolCallCard.formatDuration` is **deleted**, not aligned.                                                           |
| FR-7.3  | `formatSubagentDuration` remains exported as a deprecated alias for one release, with a comment naming the removal.  |
| FR-7.4  | Output matches the §5 table exactly, including the no-space rule at every magnitude.                                 |
| FR-7.5  | Subagent card and fleet row output is **byte-identical to today** (freeze test) — they are already correct.          |
| FR-7.6  | When `duration_ms` is absent and the call has settled, duration derives from `updatedAtMs − startedAtMs`.            |
| FR-7.7  | Derived durations carry `data-duration-source="derived"`; payload-sourced carry `"payload"`.                         |
| FR-7.8  | A settled tool call with no `duration_ms` logs once per run, naming the tool. No payload contents in the log.        |
| FR-7.9  | A running call renders no duration. Negative / non-finite values render nothing (preserve today's guard).            |
| FR-7.10 | PRD-03's group summary consumes `formatActivityDuration`. No third formatter is introduced for group elapsed.        |

## 8. Non-functional requirements

- **NFR-7.1** Pure function, no locale dependency, no `Intl`. It must produce
  identical output in jsdom, on desktop, and in CI.
- **NFR-7.2** The formatter lives in `activity/`, importable by both `thread-canvas/`
  and `subagents/` without either importing the other.

## 9. Acceptance criteria

- [ ] Table test over `{999, 1000, 2200, 9999, 10000, 12500, 59999, 60000, 90000}`
      asserting exact strings.
- [ ] `rg "function format.*[Dd]uration" packages/chat-surface/src` returns exactly
      one non-test hit.
- [ ] Freeze test: subagent card + fleet row durations unchanged from `main`.
- [ ] A projected tool call with no `duration_ms` but with timestamps renders a
      duration and `data-duration-source="derived"`.
- [ ] With `duration_ms` present, `data-duration-source="payload"` and the payload
      value wins over the derived one.
- [ ] A running call renders no duration node.
- [ ] `durationMs: -1` and `NaN` render nothing.
- [ ] The missing-payload log fires once per run, not once per render.
- [ ] `npx vitest run --root packages/chat-surface` green.

## 10. Open decisions

| ID    | Question                                                                       | Recommendation                                                                                                                                          |
| ----- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OD-71 | Should the runtime be fixed to always emit `duration_ms` rather than deriving? | Yes, eventually — FR-7.8's log is how you find out which tools omit it. Do not block this PRD on it; the client fallback is correct to have regardless. |
| OD-72 | Should derived durations be visually distinguished (e.g. a `~` prefix)?        | No. The difference is queue/transport overhead, usually tens of ms. A `~` would imply more uncertainty than exists. Keep it in the DOM only.            |
| OD-73 | Should `formatRelativeTime` in `util/time.ts` be folded in too?                | No. Different problem (wall-clock recency, not elapsed). Leave it alone.                                                                                |
