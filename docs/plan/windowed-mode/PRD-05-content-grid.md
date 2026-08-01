# PRD-05 — One content grid

**Severity:** P2 (downgraded from P1 — see §1.1) · **Depends on:** [PRD-00](./PRD-00-overview.md) for the width-class half only · **Surface:** `thread-canvas/TcChat.tsx`

## 1. Problem statement

The original observation was: _"the user bubble starts ~155px right of the tool
cards' left edge; assistant content is full-bleed, user content is inset, nothing
aligns down the left edge."_

### 1.1 Correction — most of that is deliberate and correct

Reading the code and re-reading the references, **the asymmetry itself is right**
and this finding is the softest of the nine. The relevant style
([TcChat.tsx:1325](../../../packages/chat-surface/src/thread-canvas/TcChat.tsx#L1325))
is explicit about its source:

```ts
const messageItemStyle = (role: TcChatMessage["role"]): CSSProperties => ({
  // v3 `.msg.you` — a right-aligned speech bubble (muted surface, asymmetric
  // radius, 88% cap) for the user; the assistant message renders flush.
  alignSelf: role === "user" ? "flex-end" : "stretch",
  maxWidth: role === "user" ? "88%" : "100%",
  …
});
```

Codex does exactly this — `what is MCPMark?` is a right-aligned bubble above a
flush-left full-width answer. Claude Desktop does it too. A right-aligned user
bubble against flush assistant prose is the industry pattern, it is in the v3
design, and the 155px offset is simply `100% − 88%` of the shared rail resolving
as expected. **There is no bug there.** The observation was a correct measurement
attached to an incorrect conclusion.

There is also already a shared measure — this is not a package with no concept of
a reading column ([TcChat.tsx:1284](../../../packages/chat-surface/src/thread-canvas/TcChat.tsx#L1284)):

```ts
/** One shared reading rail for every conversation-owned surface. The outer
 * chat remains full-width so scrolling and the Focus side panel keep working;
 * only readable content is centered and capped. */
const conversationRailStyle: CSSProperties = {
  marginLeft: "auto",
  marginRight: "auto",
  maxWidth: "var(--chat-content-width, 68rem)",
  width: "100%",
};
```

### 1.2 What is actually wrong

Three narrower things survive the correction:

**(a) The 88% cap does not scale.** At `wide` a 12% right-side offset reads as
deliberate composition. At `compact` — a 640px container — it is a 77px dead
gutter, and the user's own text is forced into a narrow column while the window
is already narrow. The constant was chosen for a full-screen canvas and is
applied unconditionally.

**(b) `--chat-content-width` has a max but no floor.** It is `68rem` (1088px), a
cap that is inert below 1088px. Nothing pairs it with a minimum gutter, so at
`compact` the only horizontal breathing room is `focusContainerStyle`'s
`padding: 12`. There is no token expressing "content never touches the edge by
less than X", so the value drifts per container.

**(c) Sibling entry types are not guaranteed to share a left edge.** This is the
defensible core of the original observation. The transcript's `<li>` types are
styled independently:

| Entry           | Style                                 | Horizontal treatment            |
| --------------- | ------------------------------------- | ------------------------------- |
| message (user)  | `messageItemStyle("user")`            | `padding: 8px 11px`, 88%, right |
| message (agent) | `messageItemStyle("agent")`           | `padding: 0`, 100%, stretch     |
| fleet card      | `fleetItemStyle`                      | `padding: 0`                    |
| tool card       | `activityCardFrameStyle` (1px border) | border, then `9px 11px` inside  |

The assistant's **text** starts at x=0 of the rail; a tool card's **text** starts
at `1px border + 11px padding + 22px tile + 9px gap` ≈ 43px in. Nothing enforces a
relationship between them, and nothing tests one. Whether they currently agree is
incidental, and a future card type will get it wrong for free.

### 1.3 The gutter between the rail and the content is owned by a leaf component

Traced end to end, the horizontal distance from the app rail to the first pixel of
the transcript **and** of the composer is `12px`, and every layer above the
innermost component contributes nothing:

| Layer                          | Left gutter                                                    | Source                 |
| ------------------------------ | -------------------------------------------------------------- | ---------------------- |
| `AppRail`                      | 48px total, its 1px `borderRight` inside (`border-box`)        | `AppRail.tsx:196–208`  |
| `ChatShell` grid               | **0** — no `gap`, and `rg "padding"` finds nothing in the file | `ChatShell.tsx`        |
| `mainBodyStyle`                | **0**                                                          | `ChatShell.tsx:308`    |
| `ThreadCanvas` `chatSlotStyle` | **0**                                                          | `ThreadCanvas.tsx:825` |
| `TcChat` `focusContainerStyle` | **12px** ← the entire gutter                                   | `TcChat.tsx:1364`      |

Four consequences:

1. **The shell hands every destination a cell flush against the rail's border.**
   The inset is not a layout property; it is something each leaf has to remember.
   A destination that forgets butts straight against the rail. This is the same
   shape as the "unfed context panel" problem `ChatShell`'s own comments describe
   — a layout responsibility pushed down into content.
2. **Two gutter values already coexist.** `Topbar` uses `padding: 0 18px`
   (`Topbar.tsx:109`); the cockpit body uses 12. On `run` the topbar is
   suppressed so they never stack, which is the only reason this has not been
   noticed.
3. **The measure is declared twice, nested.** `composerSlotStyle` is
   `{...conversationRailStyle}` (max 68rem, auto margins) and the
   `.aui-composer` root inside it independently declares
   `margin: var(--space-sm) auto 0; max-width: var(--chat-content-width)` —
   68rem again. Idempotent today, so nothing is visibly broken, but the
   composer's alignment with the transcript is **coincidence, not construction**:
   two independent declarations that happen to reference the same token. Change
   one and they diverge silently.
4. **12px never adapts.** Same value at 640px and at 2560px.

⚠️ **A host stylesheet already overrides this element.**
`apps/desktop/renderer/desktop.css:180`:

```css
.desktop-window-frame [data-testid="run-composer"] .aui-composer {
  margin: 0 auto;
}
```

That override is **vertical only** — it drops the package's `--space-sm` top
margin and preserves `auto` side margins, so it does not currently move the
composer horizontally, and its comment explains why it exists. But it proves the
host-shadowing path is live for exactly this selector at higher specificity. Any
horizontal change made in the package can be silently overridden here, and must
be verified in the built desktop bundle (the PR #459 class of bug).

**Honest limit:** from the code, the transcript and the composer both resolve to
12px and should align. If they visibly do not in the running app, `desktop.css:180`
is the first place to look, and the discrepancy must be **measured in the packaged
build** — not inferred from source, and not from a screenshot.

## 2. Goals & non-goals

**Goals**

- The user-bubble cap adapts to width class.
- A real token for the minimum content gutter.
- An enforced, tested rule for where transcript entry content begins.

**Non-goals**

- Removing the right-aligned user bubble. It is correct, it matches both
  references and the v3 design, and changing it would be a regression.
- Changing `--chat-content-width`'s 68rem value.
- Restyling `activityCardChrome`. Its internal geometry is shared with the
  subagent family on purpose (PRD-06 touches its wrapping, not its padding).

## 3. Design decisions

**D-5.1 — Width-scaled bubble cap.** Replace the constant with a per-class value:
`wide` 88% (unchanged), `regular` 88%, `compact` 94%. Only `compact` moves. Every
other width renders byte-identically to today.

**D-5.2 — Introduce `--chat-content-gutter`.** A design-system token for the
minimum horizontal breathing room, applied by the conversation rail. `12px` at
`compact`, `18px` above — matching `Topbar`'s existing `padding: 0 18px`, so the
transcript's gutter and the shell's chrome gutter agree instead of being two
independent numbers.

**D-5.3 — Define a content origin and test it.** State the rule explicitly:
_every transcript entry's primary text begins at the same x_. Card-framed entries
achieve this via their own padding; unframed entries (assistant text) sit flush.
Because those are different mechanisms, the guarantee has to be a test, not a
convention — a computed-style assertion over one instance of each entry type.

This is the only part of the finding worth engineering effort, and it is worth it
precisely because it is invisible until someone adds an entry type.

**D-5.4 — The gutter moves up to the shell.** Per §1.3, the rail-to-content inset
belongs to the layout, not to `TcChat`. Apply `--chat-content-gutter` on
`ChatShell`'s `mainBodyStyle` so **every** destination inherits it, and delete the
compensating `padding: 12` from `focusContainerStyle`. A destination that wants
more can add its own; none has to remember to add the baseline.

This is a strictly better place for it: one declaration instead of one per leaf,
and it becomes width-aware for free because the shell already knows the width class
(PRD-00).

**D-5.5 — Declare the measure once.** `composerSlotStyle` already applies
`conversationRailStyle`. Remove the duplicate `max-width` /`auto` margins from
`.aui-composer`'s root so the rail is declared in exactly one place and the
composer's alignment with the transcript is structural rather than coincidental.
Verify in the packaged desktop build, given the live host override at
`desktop.css:180`.

## 4. UX specification

```
compact (640px container)
┌────────────────────────────────────────────┐
│ ← 12px gutter                  12px → │
│                    ┌──────────────────────┐│
│                    │ user bubble, 94% cap ││
│                    └──────────────────────┘│
│ ┌────────────────────────────────────────┐ │
│ │ ⚙ Worked for 26s · 6 steps           ▾ │ │
│ └────────────────────────────────────────┘ │
│ Assistant answer text, flush left.         │
└────────────────────────────────────────────┘
     ↑                ↑
     └── content origin: assistant text and card text
         resolve to the same x (D-5.3)
```

No visual change at `wide` or `regular` beyond the gutter token resolving to the
same 18px the topbar already uses.

## 5. User journeys

**J-5.1 — Sarah types a long message in a narrow window.**
At 640px her message wraps into a bubble that uses 94% of the rail rather than
88%, so it is roughly two lines shorter and the dead left gutter is 38px instead
of 77px. Nothing else moves.
_Today: the full-screen cap applies unchanged and her own text is squeezed._

**J-5.2 — Marcus at full screen.**
He sees no difference whatsoever. This is the point: PRD-05 is almost entirely a
`compact` change plus a test.

**J-5.3 — A developer adds a new transcript entry type.**
They add an approval-receipt row and give it `padding: 0`. The content-origin test
fails, naming the offending entry type and both x values. They add the wrapper and
it passes. The alignment guarantee is now something the repo enforces rather than
something a reviewer has to notice.

**J-5.4 — The Studio rail is dragged narrow inside a wide window.**
The canvas container hits `compact` even though the window is 1600px, because the
observer is on the container (PRD-00 D-0.1). The bubble cap and gutter follow the
container. The user gets consistent treatment regardless of how the width was lost.

## 6. Functional requirements

| ID      | Requirement                                                                                                                         |
| ------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| FR-5.1  | The user-bubble `maxWidth` resolves per width class: `compact` 94%, `regular`/`wide` 88%.                                           |
| FR-5.2  | `--chat-content-gutter` is added to the design system and consumed by `conversationRailStyle`. `18px`, `12px` at `compact`.         |
| FR-5.3  | The gutter value at `regular`/`wide` equals `Topbar`'s horizontal padding. One number, referenced twice, not two constants.         |
| FR-5.4  | A content-origin test asserts that assistant text, tool-card title text, and fleet-card title text resolve to the same x.           |
| FR-5.5  | At `regular` and `wide` the rendered transcript is byte-identical to before this PRD (snapshot).                                    |
| FR-5.6  | The right-aligned user bubble, its asymmetric radius, and its muted surface are unchanged.                                          |
| FR-5.7  | No component hard-codes 88 or 94; both come from one exported record keyed by width class.                                          |
| FR-5.8  | `ChatShell`'s `mainBodyStyle` applies `--chat-content-gutter`, so every destination inherits the rail-to-content inset.             |
| FR-5.9  | `focusContainerStyle`'s `padding: 12` is removed once FR-5.8 lands. Net gutter is unchanged at `compact`; no double inset.          |
| FR-5.10 | `.aui-composer`'s root drops its duplicate `max-width` / `auto` side margins; `composerSlotStyle` is the only rail declaration.     |
| FR-5.11 | The desktop override at `desktop.css:180` is re-read after FR-5.10 and either kept (still vertical-only) or updated in the same PR. |
| FR-5.12 | A test asserts the composer's left edge and the transcript's left edge are equal at all three width classes.                        |

## 7. Non-functional requirements

- **NFR-5.1** No change to scroll behaviour or to the full-width outer container
  — the rail comment is explicit that the outer chat stays full-width so
  scrolling and the Focus side panel keep working. Do not narrow the scroller.
- **NFR-5.2** The content-origin test must fail loudly with both measured values
  in the message. A test that says only "expected true" teaches nothing.

## 8. Acceptance criteria

- [ ] At a 640px container, user-bubble computed `max-width` is 94%; at 1400px, 88%.
- [ ] `--chat-content-gutter` exists in `packages/design-system/src/styles.css`
      and is referenced by `conversationRailStyle`.
- [ ] Grep shows no second literal for the topbar gutter value.
- [ ] Content-origin test passes with assistant text, tool card, and fleet card
      mounted; deliberately breaking one entry's padding fails it with both x values named.
- [ ] Snapshot at 1400px identical to `main`.
- [ ] `rg "padding" packages/chat-surface/src/shell/ChatShell.tsx` now returns the
      gutter declaration — the shell owns the inset, not `TcChat`.
- [ ] Composer left edge equals transcript left edge at `compact`/`regular`/`wide`.
- [ ] `.aui-composer` declares no `max-width` and no `auto` side margins.
- [ ] Packaged desktop build measured: the `desktop.css:180` override still does not
      move the composer horizontally.
- [ ] `npx vitest run --root packages/chat-surface` green.

## 9. Open decisions

| ID    | Question                                                                   | Recommendation                                                                                                                                                                                                                                                    |
| ----- | -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OD-51 | Is this PRD worth doing at all, given §1.1?                                | Yes — more than when it was written. §1.3 (the shell owns no gutter; the measure is declared twice) is a real structural defect, not cleanup. Minimum viable cut is **FR-5.1, FR-5.4, FR-5.8–5.10**; the bubble-cap polish is the droppable part, not the gutter. |
| OD-52 | Should assistant messages also get a bubble at `compact` for scan-ability? | No. Both references keep assistant prose flush at every width. Do not diverge from them on a hunch.                                                                                                                                                               |
| OD-53 | Should `--chat-content-width` shrink at `compact`?                         | No — it is a cap, and below 1088px it is already inert. Shrinking it would do nothing.                                                                                                                                                                            |
