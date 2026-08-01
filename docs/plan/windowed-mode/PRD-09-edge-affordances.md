# PRD-09 — Edge affordances

**Severity:** P2 · **Depends on:** nothing · **Surface:** `thread-canvas/TcChat.tsx`, `shell/AppRail.tsx`

## 1. Problem statement

Three dead edges in the captured window:

1. **Nothing under the answer.** Codex puts copy / 👍 / 👎 / branch under every
   response. 0xCopilot puts nothing under
   `Created manual-156ea66f.txt containing MANUAL in the artifact library.` —
   no copy, no retry, no feedback.
2. **The account is a bare "L".** A 26px circle with one letter. Claude's footer
   reads `C Copilot · Max ⌄`; Codex's reads `PP Parth Pahwa`. Whose session is
   this, on what plan?
3. **The composer's `Manual ⌄` renders in disabled grey** next to an enabled
   `Tools 1`, which reads as broken rather than as a mode.

## 2. Current state

### 2.1 Message actions: not "sparse" — absent

```
rg "onCopy|Copy message|onRetry|thumbs|feedback" \
   packages/chat-surface/src/thread-canvas/TcChat.tsx \
   packages/chat-surface/src/messages/*.tsx
→ (no matches)
```

There is no message-action affordance in the transcript at all — not hidden
behind hover, not behind a context menu. The capability was never built.

**The substrate half is already solved, though.** `ClipboardPort` exists
([ports/ClipboardPort.ts:9](../../../packages/chat-surface/src/ports/ClipboardPort.ts#L9)):

```ts
export interface ClipboardPort {
  copyText(text: string): Promise<void>;
}
```

and **both hosts already implement and provide it** — `apps/frontend/src/ports/ClipboardWeb.ts`
via `PortProvider`, and the desktop binder in `renderer/destinationBinders.tsx`.
So "copy the answer" needs a button and a call, not a port, not an IPC channel,
not a host change.

### 2.2 The rail identity already knows the name

[AppRail.tsx:192](../../../packages/chat-surface/src/shell/AppRail.tsx#L192):

```ts
const displayName = identity?.displayName.trim() ?? "";
const initialGlyph = displayName.length > 0 ? displayName.charAt(0) : null;
const accountLabel = displayName.length > 0 ? displayName : "Account";
```

The full display name is present and correct — both host bindings thread it
(`buildWebShellBinding` asserts `{ displayName: "Sarah Chen" }`). It is reduced
to one character because the rail is 48px wide and a name does not fit.

That is a reasonable constraint for a 48px rail, and it is _the same_ constraint
Claude and Codex avoid by not having a 48px-only rail — they have a labelled
sidebar. Which is precisely what [PRD-01](./PRD-01-thread-switching.md) adds. So
this finding largely resolves as a consequence of PRD-01 rather than on its own.

### 2.3 The composer moved — verify before specifying

⚠️ The `Manual ⌄` observation predates a `dev` merge that touched **14 composer
files**, including `composer.css`, `AssistantComposer.tsx`, a new `BypassPill`,
`WorkspaceFolderBar`, `filesystemBypass.ts`, and `AssistantComposer.layout.test.tsx`.

The disabled-looking pill may already be fixed, may have moved, or may never have
been disabled (it could be a low-emphasis mode selector rendering exactly as
designed). **FR-9.7 makes re-verification the first task**; do not write styling
against a screenshot that is now behind the code.

## 3. Goals & non-goals

**Goals**

- Copy an answer in one click.
- Retry / edit-and-resend a turn.
- The account affordance names the account wherever there is room to.
- Establish whether the composer pill is a real defect.

**Non-goals**

- 👍/👎 feedback collection. That needs a destination for the signal (a store, a
  telemetry sink, a review queue) and a privacy story. Without one it is a button
  that discards the user's input, which is worse than no button. Out of scope
  until there is a consumer — see OD-91.
- Message branching / forking (Codex's fourth action). Large, separate.
- Widening the 48px rail.

## 4. Design decisions

**D-9.1 — Two actions, not four.** `Copy` and `Retry`. Both have unambiguous,
already-available behaviour. Ship what works rather than a row of affordances
where half do nothing.

**D-9.2 — Actions live under the last assistant message, always visible.** Not
hover-revealed. Hover-only actions are undiscoverable, invisible to keyboard
users until focus lands, and the transcript already has a settled bottom edge to
hang them from. Earlier messages reveal on hover/focus to avoid a ladder of
button rows down a long transcript.

**D-9.3 — Copy copies the rendered text, not the markdown source.** The user sees
prose; copying should paste prose. (Streaming markdown is reconstructed for
display; the source may carry citation plumbing.)

**D-9.4 — Retry is edit-and-resend of the preceding user turn.** Seeds the
composer with the user's original message so they can adjust before re-sending.
This composes with PRD-04's step-level retry — same verb, same mechanism
(composer prefill), different scope.

**D-9.5 — The account affordance follows PRD-01.** When the Threads panel is
docked, the account renders in its footer with the full name, as both references
do. The 48px rail keeps the initial-only glyph as the collapsed/overlay form.
`accountLabel` is already computed for exactly this.

**D-9.6 — Copy failures must surface.** `ClipboardPort.copyText` rejects on web
without a secure context; the port's own docstring says _"Destinations surface
success/failure via their own UI toast."_ Use the existing `ToastStack`. A silent
failed copy is a data-loss bug from the user's point of view.

## 5. UX specification

```
Created manual-156ea66f.txt containing MANUAL in the artifact library.

  ⧉ Copy    ↻ Retry
  ↑ always visible on the last assistant message;
    hover/focus-revealed on earlier ones
```

- **Copy** — `⧉` + label at `wide`/`regular`; icon only at `compact`, with
  `aria-label` retained. On success the label flips to `Copied` for ~1.5s. On
  failure, a toast.
- **Retry** — `↻` + label. Seeds the composer, focuses it, does not auto-send.
- Type register matches `activityCardMetaStyle` (quiet, small) — actions are
  secondary to the answer and must not compete with it (this is PRD-03's whole
  premise).
- **Account footer** (docked panel only): 26px avatar + display name, single
  line, ellipsised. No plan/tier string — we do not have a trustworthy source for
  one, and inventing it is worse than omitting it.

**Accessibility.** Real `<button>`s in a `role="group"` labelled
`Message actions`. Hover-revealed rows on earlier messages must also appear on
`:focus-within`, so keyboard users reach them. The `Copied` flip is announced via
`aria-live="polite"`.

## 6. User journeys

**J-9.1 — Sarah copies an answer.**
The run finishes. Under the answer: `⧉ Copy  ↻ Retry`. She clicks Copy; it reads
`Copied`; she pastes into Slack.
_Today: she selects the text by hand and hopes the selection does not grab the
tool cards above it._

**J-9.2 — The answer is wrong.**
Marcus clicks Retry. The composer fills with his original message. He adds
"…and use the absolute path", sends. Nothing was auto-sent on his behalf.

**J-9.3 — Copying an older answer.**
Sarah scrolls up. The old message's action row is hidden until she hovers it —
so the transcript is not a ladder of buttons — then appears. Tabbing to it works
identically via `:focus-within`.

**J-9.4 — Copy fails on the web host.**
`copyText` rejects (no secure context). A toast says copy is unavailable. She is
not left thinking she has the text when she does not.

**J-9.5 — Sarah checks which account she is in.**
She opens the Threads panel (PRD-01). Its footer reads `Sarah Chen` next to the
avatar. Collapsed, the rail still shows `S`, with the name as its `title`.

**J-9.6 — The composer pill (verify first).**
Someone reproduces the composer at `compact` on current `dev` and records whether
`Manual ⌄` is genuinely disabled, genuinely low-emphasis-by-design, or already
changed. That finding decides whether there is anything to fix.

## 7. Functional requirements

| ID      | Requirement                                                                                                                             |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| FR-9.1  | Assistant messages render a `Copy` and a `Retry` action in a `role="group"` labelled `Message actions`.                                 |
| FR-9.2  | Actions are always visible on the last assistant message; hover/`:focus-within`-revealed on earlier ones.                               |
| FR-9.3  | Copy uses the existing `ClipboardPort`. No new port, no host change.                                                                    |
| FR-9.4  | Copy writes rendered text, not raw markdown source.                                                                                     |
| FR-9.5  | Copy success flips the label for ~1.5s with `aria-live="polite"`; rejection raises a `ToastStack` toast.                                |
| FR-9.6  | Retry seeds and focuses the composer with the preceding user turn. It never auto-sends.                                                 |
| FR-9.7  | **First task:** re-verify the composer `Manual ⌄` pill against current `dev` and record the finding here. No styling change until then. |
| FR-9.8  | With the Threads panel docked (PRD-01), its footer shows the avatar plus `accountLabel`. The 48px rail is unchanged.                    |
| FR-9.9  | At `compact`, actions render icon-only with `aria-label` preserved.                                                                     |
| FR-9.10 | No feedback (👍/👎) affordance ships until a consumer exists (see OD-91).                                                               |

## 8. Non-functional requirements

- **NFR-9.1** Action rows must not shift transcript layout when they appear on
  hover — reserve the row height, or render them in a fixed-height slot.
- **NFR-9.2** No `navigator.clipboard` in the package. The port exists precisely
  so the boundary holds; a direct call would fail lint and break desktop.
- **NFR-9.3** Copy must not re-render the transcript. Local state on the action
  row only.

## 9. Acceptance criteria

- [ ] The last assistant message renders both actions; an earlier one renders them
      only under hover or `:focus-within`.
- [ ] Clicking Copy calls `ClipboardPort.copyText` with the rendered text (assert
      the argument contains no citation markup).
- [ ] A rejecting `copyText` produces a toast; the label does not flip to `Copied`.
- [ ] Retry populates and focuses the composer with the preceding user message and
      sends nothing.
- [ ] `rg "navigator.clipboard" packages/chat-surface/src` returns zero hits.
- [ ] At `compact` the actions are icon-only and retain accessible names.
- [ ] Hover reveal causes no layout shift (assert stable `getBoundingClientRect`
      of the message above).
- [ ] FR-9.7's composer finding is recorded in §2.3 before any composer change.
- [ ] `npx vitest run --root packages/chat-surface` green; both host binders typecheck.

## 10. Open decisions

| ID    | Question                                                          | Recommendation                                                                                                                                                                                                                   |
| ----- | ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OD-91 | Should 👍/👎 ship?                                                | Not until there is a consumer. Decide _first_ where the signal goes (local store? telemetry? eval corpus? `harness_quality/` looks like a candidate), then build the button. A button that discards feedback is worse than none. |
| OD-92 | Should Copy also offer "copy as markdown"?                        | Not in v1. One Copy that does the obvious thing beats a menu. Revisit on request.                                                                                                                                                |
| OD-93 | Should the account footer show a plan/tier like Claude's `· Max`? | Only if a trustworthy source exists. On a local-first desktop build there may be no meaningful tier — omit rather than invent.                                                                                                   |
| OD-94 | Does Retry belong on user messages instead of assistant ones?     | Assistant. The user's mental model is "that answer was wrong, try again", and the action row is already anchored to the answer.                                                                                                  |
