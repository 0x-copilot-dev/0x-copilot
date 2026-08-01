# PRD-03 — Transcript density: the answer wins

**Severity:** P0 · **Depends on:** [PRD-00](./PRD-00-overview.md) · **Coordinates with:** [PRD-04](./PRD-04-recovered-failures.md) · **Surface:** `thread-canvas/TcChat.tsx`, `activity/`

## 1. Problem statement

In the captured session the agent did one small thing — create a text file — and
reported it in one line:

> Created `manual-156ea66f.txt` containing `MANUAL` in the artifact library.

Above that line sat **six tool cards**, consuming roughly 55% of the visible
transcript. The answer was the least prominent element on screen.

The same moment in the two references:

| Product        | How the tool run is presented                                                               |
| -------------- | ------------------------------------------------------------------------------------------- |
| Codex          | `Worked for 26s ›` — one line, collapsed, expandable                                        |
| Claude Desktop | inline italic reasoning + one-line `Read depth.py ›`                                        |
| 0xCopilot      | six stacked bordered cards, each with tile, title, chip, duration, summary, status, chevron |

At full screen this is a defensible "show your work" stance. In a 900px window it
inverts the information hierarchy: process is loud, conclusion is quiet.

## 2. Current state

**The individual card is not the problem.** `ToolCallCard` is already a
`<details>` that starts closed
([ToolCallCard.tsx:34](../../../packages/chat-surface/src/thread-canvas/ToolCallCard.tsx#L34)),
and `activityCardChrome` is deliberately compact — a 22px tile, `padding: 9px 11px`,
an 11px mono title. A collapsed card is ~40px by design.

**The problem is that there is no layer above the card.** `TcChat` renders each
projected entry as its own `<li>` in sequence. Six tool calls is six frames, six
borders, six sets of padding — ~240px minimum before any of them reveals
anything, and each _collapsed_ card still renders tile + title + provenance chip +
access + duration + summary line + status group + chevron.

There is **no run-level summary and no group affordance anywhere**. Nothing in
the package answers "the agent did six things, here's the gist".

**The precedent we need already exists.**
[`ReasoningGroup`](../../../packages/chat-surface/src/messages/ReasoningGroup.tsx)
is exactly this component for a different entry type:

- a native `<details>`, collapsed by default, keyboard-accessible;
- a summary label that flips with status (`Thinking…` → `Thought process`);
- an elapsed-time stamp, synthesised by the caller as
  `max(updatedAtMs) - min(startedAtMs)`;
- `data-status` exposed to CSS so the body renders a streaming cursor with no JS
  animation.

That is `Worked for 26s ›`, already designed, already shipped, applied to
reasoning parts instead of tool calls.

⚠️ **But note where its CSS lives** — the file says so plainly:

> CSS lives in the host substrate (`apps/frontend/src/styles.css`) under
> `.aui-reasoning-group`.

That is the stranded-CSS failure mode this repo has hit before: rules that live
only in the **web** host do not load on desktop. The new group must not repeat it
(FR-3.11).

## 3. Goals & non-goals

**Goals**

- A settled tool run collapses to one line by default.
- Live progress stays visible while the agent is working.
- The answer is the most prominent thing in a finished exchange.
- Density scales with width class — tighter when the window is small.

**Non-goals**

- Hiding work. Everything remains one click away, and the disclosure state is
  the user's to set.
- Redesigning `ToolCallCard`'s internals or `activityCardChrome`'s geometry —
  they are already compact and shared with the subagent family on purpose.
- Changing the event projection. `useEventProjector` stays the single source
  (FR-3.3); grouping is a pure view-layer fold over its output.

## 4. Design decisions

**D-3.1 — The group is a maximal consecutive run of non-message stream items.**

> ⚠️ **This decision was revised twice. The second revision was wrong; this is the
> corrected version.** Recording the whole path because the mistake is instructive.
>
> _First cut:_ "a maximal consecutive span of tool-call / fleet entries, bounded by
> assistant text". _Second cut (wrong):_ on seeing that `eventProjector` emits
> `stream_delta` for `model_delta` / `reasoning_summary_delta` and
> `assistant_message` only for `final_response`, I concluded interim narration
> should be absorbed INTO the group and only the final response should end it.
>
> That reasoned over the wrong data shape. `ChatEntry` is the **projector's**
> internal union; the transcript `TcChat` actually renders is
> `mergeStream(messages, fleets, toolCalls) → StreamItem[]`, and
> `destinations/run/chatProjection.ts` **folds the whole run of deltas into ONE
> assistant message**, with `reasoning_summary_delta` folded into a separate
> `reasoning` part of that same message. There is no free-standing interim-text
> item in the transcript to absorb.

So the fold is over `StreamItem`:

```
a group = a maximal consecutive run of { kind: "tool" | "fleet" }
```

**And the fold is OPT-IN, not boundary-enumerating.** `groupActivityStream` takes
`{ isGroupable, idOf }` from the caller; anything it does not opt in passes through
untouched. Stating the rule as _"groupable until a message breaks it"_ would have
been equivalent on the day it was written and wrong a week later: while this PRD was
being implemented, `dev` moved and `mergeStream` gained a fourth argument, so
`StreamItem` gained `{ kind: "approval" }`. A fold that listed its boundaries would
have swallowed that new kind into a collapsed group — burying the only control a
parked run gives the user, which is exactly the hazard `TcChat`'s own comment already
warns about ("must never early-return past the cards — inline, that hid a parked
run's only way out").

Opt-in makes the failure mode safe by default: an unrecognised kind renders visibly
and ungrouped. Two tests pin it — one that an approval splits a run of tool calls
into two groups without changing order, one that a synthetic unknown kind passes
through.

Consequences that follow, and are worth stating because they are easy to get wrong:

- **Reasoning is not the group's problem.** It is a `parts[].type === "reasoning"`
  entry on the assistant message, rendered inside that message by
  `ReasoningGroup`. PRD-03 does not touch it.
- **Multi-turn is handled for free.** `user → cards → answer → user → cards →
answer` yields one group per turn, because each answer breaks the run.
- **Approvals break a group** — an approval needs the user and must never be
  buried in a collapsed summary.

✅ **The live journey settled the open question.** `chatProjection` stamps the
synthesized assistant message with the **first** delta's timestamp, and
`mergeStream` slots cards by timestamp — so in principle a run that emits text
before later tool calls could anchor the streaming answer _between_ activity
items and split one turn's work into two groups.

`tools/desktop-journeys/transcript-density/long_run_grouping.py` drove the real
packaged app through a long task (web search + filesystem listing + one
subagent) and recorded:

```
transcript order: ['msg:user', 'group', 'msg:assistant']
FINDING  single group — the assistant message did NOT split the run
FINDING  loose (ungrouped) activity cards: 0
```

So the answer anchors after the activity in practice, and the simple fold holds.
The journey keeps logging the finding on every run rather than asserting a single
group, so a future runtime change that starts interleaving text shows up as a
recorded fact instead of a silent regression.

**D-3.2 — Expanded while running, collapsed when settled.** This is the whole
trick, and it is what Codex does. While any member is `running`, the group is
open and streaming — the user watches work happen. When the last member settles
**and** the assistant produces text, the group collapses to its summary line. The
user saw the process; now they read the answer.

**D-3.3 — Collapse is animated-free and never retroactive to user intent.** If
the user has manually toggled a group, that choice sticks for the session — auto-
collapse only applies to groups the user has not touched. A transcript that
re-collapses something you deliberately opened is hostile.

**D-3.4 — Single-member groups do not get a wrapper.** One tool call is already
one line-ish; wrapping it in a group would add a frame to save nothing. Threshold
is `>= 2` members.

**D-3.5 — Unrecovered failure keeps the group open.** If the run ended in failure,
the group stays expanded and scrolled to the failing step — the user needs the
detail. A _recovered_ failure collapses like any other (see PRD-04 for what the
recovered step looks like inside).

**D-3.6 — Density is width-aware.** At `compact`, collapsed cards drop the
summary line and the access chip, keeping tile + title + duration + status. This
is the only width-conditional behaviour in this PRD; everything else is
width-independent and lands as a straight improvement at full screen too.

## 5. UX specification

**Collapsed (the default for a settled run):**

```
┌────────────────────────────────────────────────────────────┐
│ ⚙  Worked for 26s · 6 steps                              ▾ │
└────────────────────────────────────────────────────────────┘
Created manual-156ea66f.txt containing MANUAL in the artifact library.
```

**Expanded (while running, or on click):**

```
┌────────────────────────────────────────────────────────────┐
│ ⚙  Working · 4 of 6                                      ▴ │
├────────────────────────────────────────────────────────────┤
│  L  ls                                    2.2s          ✓  │
│  W  write_todos                           320ms         ✓  │
│  R  read_file                             2.1s          !  │
│  …                                                          │
└────────────────────────────────────────────────────────────┘
```

**Summary label, by state** (mirroring `ReasoningGroup`'s label flip):

| State                        | Label                                                    |
| ---------------------------- | -------------------------------------------------------- |
| any member running           | `Working · {done} of {total}`                            |
| all settled, no errors       | `Worked for {elapsed} · {n} steps`                       |
| all settled, recovered error | `Worked for {elapsed} · {n} steps` + a muted `1 retried` |
| run failed                   | `Failed after {elapsed} · {n} steps` — group stays open  |

- `elapsed` = `max(updatedAtMs) − min(startedAtMs)` across members, formatted by
  the shared duration formatter (PRD-07 makes that a single function — use it,
  do not write a second one).
- **Answer prominence.** Assistant text following a collapsed group gets the
  transcript's normal body treatment; the group summary is one rung quieter
  (`--color-text-muted`, mono, `--font-size-2xs`), matching the existing
  `activityCardMetaStyle` register. The visual weight ordering must be
  answer > group summary, and it must be asserted, not eyeballed (FR-3.10).

**Accessibility.**

- Native `<details>`/`<summary>` — Enter/Space toggle, announced as a disclosure,
  no ARIA needed for the mechanics.
- `aria-label` on the summary: `"{label}. Show step details"` /
  `"…Hide step details"`, matching `ToolCallCard`'s existing pattern.
- `data-status="running|complete|error"` on the `<details>` for CSS, mirroring
  `ReasoningGroup`'s `data-status`.
- Auto-collapse must not move focus. If focus is inside the group when it settles,
  the group does **not** auto-collapse (FR-3.7).

## 6. User journeys

**J-3.1 — Sarah watches a run finish, windowed (the captured scenario).**
She sends the task. The group appears expanded, `Working · 1 of 6`, steps
streaming in. She watches. The last step settles, the answer streams in, and the
group folds to `Worked for 26s · 6 steps`. The answer now occupies the bottom of
the transcript with nothing competing.
_Today: six cards, and the answer is one line under 55% of screen of process._

**J-3.2 — Marcus wants the detail.**
He clicks the summary. The group expands to the six cards, each still its own
`<details>` for args/results. He opens `read_file`, reads the payload, and leaves
both open. They stay open for the session.

**J-3.3 — A long run in a small window.**
Twenty tool calls in a 640px window. Expanded-while-running would push the
composer off screen, so the group scrolls internally rather than growing
unbounded: `max-height` at `compact`, with the newest step pinned in view. When
it settles, one line.

**J-3.4 — The run fails.**
The final step errors and the run terminates failed. The group does **not**
collapse; it stays open showing `Failed after 12s · 4 steps`, scrolled to the
failing card. The user sees what broke without a click.

**J-3.5 — The run recovers from a failed step.**
`read_file` fails; the agent retries a different way and succeeds. The group
collapses like any success, with a muted `1 retried` in the summary. The red is
inside, one click away, not shouting from the transcript. (PRD-04 governs what
that step looks like.)

**J-3.6 — Keyboard user tabs into a group as it settles.**
Focus is on a card inside the group when the last step completes. The group does
not auto-collapse out from under them. It collapses only after focus leaves, or
never, if they toggled it.

**J-3.7 — A single tool call.**
The agent calls one tool. No group wrapper — the card renders as it does today.
No new chrome for no benefit.

## 7. Functional requirements

| ID      | Requirement                                                                                                                                                                                                               |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FR-3.1  | A pure `groupActivityEntries(entries)` fold produces maximal consecutive runs of `tool_call` \| `subagent_*` \| `stream_delta`, terminated by `assistant_message` / user message / approval. No new state, no new events. |
| FR-3.1a | Interim `stream_delta` entries render INSIDE the group as inline lines, in reading order — never as cards, and never as a group boundary.                                                                                 |
| FR-3.1b | An approval always ends a group and renders outside it — it needs the user and must never be buried in a collapsed summary.                                                                                               |
| FR-3.2  | Groups of `>= 2` members render a `ToolRunGroup`; a single member renders bare, exactly as today.                                                                                                                         |
| FR-3.3  | A group with any `running` member renders expanded.                                                                                                                                                                       |
| FR-3.4  | When all members settle **and** the group was never user-toggled, it collapses.                                                                                                                                           |
| FR-3.5  | A user toggle pins the group's state for the session; auto-collapse never overrides it.                                                                                                                                   |
| FR-3.6  | A group whose run terminated `failed` stays expanded regardless of FR-3.4.                                                                                                                                                |
| FR-3.7  | Auto-collapse is suppressed while focus is inside the group.                                                                                                                                                              |
| FR-3.8  | The summary label follows the state table in §5, using the shared duration formatter from PRD-07.                                                                                                                         |
| FR-3.9  | At `compact`, collapsed member cards omit the summary line and access chip; tile, title, duration, status remain.                                                                                                         |
| FR-3.10 | A test asserts the computed font-size/color of assistant answer text is more prominent than the group summary — not a visual review.                                                                                      |
| FR-3.11 | All CSS for the group ships **inside `packages/chat-surface`**, not in a host stylesheet. Verified present in the packaged desktop bundle.                                                                                |
| FR-3.12 | Grouping is a view fold only — `useEventProjector` output is untouched and remains the single projection (FR-3.3 of the cockpit contract).                                                                                |
| FR-3.13 | At `compact`, an expanded group has a `max-height` and scrolls internally with the newest member in view.                                                                                                                 |
| FR-3.14 | `ReasoningGroup` behaviour is unchanged by this PRD. (Its stranded CSS is noted as a follow-up, not fixed here.)                                                                                                          |

## 8. Non-functional requirements

- **NFR-3.1** No animation on auto-collapse. A transcript that moves while you
  read it is worse than one that is too long.
- **NFR-3.2** Grouping must not cause the transcript to scroll-jump. Collapse
  changes height; the scroll anchor must hold the user's reading position, and
  if they are pinned to the bottom they stay pinned.
- **NFR-3.3** The fold is O(n) over projected entries and memoised on the entry
  array identity.
- **NFR-3.4** No new dependency; `<details>` only, as with `ReasoningGroup` and
  `ToolCallCard`.

## 9. Acceptance criteria

- [ ] Six consecutive tool calls render one `<details>`, not six `<li>` cards.
- [ ] While one member is `running`, `details.open === true`; when all settle it
      becomes `false`.
- [ ] After a user click, settling does not change `open`.
- [ ] With focus inside, settling does not change `open`; blurring then allows it.
- [ ] A failed run leaves `open === true` and the failing card in view.
- [ ] One tool call renders with no group wrapper.
- [ ] Summary text matches the table for all four states, with durations formatted
      by the single shared formatter.
- [ ] Computed-style assertion: answer text is larger and/or higher-contrast than
      the group summary.
- [ ] At `compact`, member cards have no summary line; at `wide` they do.
- [ ] Grepping `apps/frontend/src/styles.css` and `apps/desktop` for the group's
      class names returns **zero** hits — all rules live in the package.
- [ ] Packaged desktop build: group renders collapsed and styled (guards the
      stranded-CSS class of bug).
- [ ] Scroll position is preserved across an auto-collapse (assert `scrollTop`
      delta within tolerance, or bottom-pinned stays pinned).

## 10. Open decisions

| ID    | Question                                                                                  | Recommendation                                                                                                                                              |
| ----- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OD-31 | Should the group also fold reasoning parts, unifying with `ReasoningGroup`?               | Not in this PRD. Unifying is right eventually, but `ReasoningGroup`'s CSS is stranded in the web host — fix that first, separately, or you inherit the bug. |
| OD-32 | Persist collapse state across sessions?                                                   | No. Session-scoped is enough and avoids a `KeyValueStore` key per group. Revisit if users complain.                                                         |
| OD-33 | Should the summary name the tools (`ls, write_todos, read_file…`) rather than count them? | Count first. Names are long, wrap badly at `compact`, and the expanded view already lists them.                                                             |
| OD-34 | Does the fleet/subagent card belong in the same group as tool calls?                      | Yes — both are "work the agent did" and both already share `activityCardChrome` by explicit design. Keep them in one span.                                  |
