# PRD-06 — Chip anchoring in card headers

**Severity:** P2 · **Depends on:** nothing · **Surface:** `thread-canvas/ToolCallCard.tsx`, `activity/ActivityCardChrome.ts`

## 1. Problem statement

In the captured window, one activity card — titled **Manual file**, 645 ms, with
the description _"Create the requested text file in the attached workspace"_ —
carried a bordered chip reading **Projects** parked at an arbitrary horizontal
position in the header row. Not adjacent to the title, not flush right, not
aligned to anything above or below it.

Every chip in Claude Desktop and Codex is either flush-left immediately after its
label, or flush-right in its row. Neither ever floats mid-row.

## 2. Current state

### 2.1 First: identify the owner (do this before writing code)

Two components can render that card, and **I could not determine which from the
cropped screenshot**. Resolve it first — the fix differs.

| Candidate                                                                                         | Discriminator                                                                                                         |
| ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `ToolCallCard` ([:86–142](../../../packages/chat-surface/src/thread-canvas/ToolCallCard.tsx#L86)) | Leading glyph is a **letter tile** — `toolTileGlyph()` renders `toolName[0].toUpperCase()` in `activityCardTileStyle` |
| `SubagentCard` ([:80–86](../../../packages/chat-surface/src/subagents/SubagentCard.tsx#L80))      | Leading glyph is `<ActivityStatusIcon>`, not a letter; chip is a design-system `<Badge>`                              |

The screenshot shows a **letter tile (`P`)**, which points at `ToolCallCard`. But
`ToolCallCard`'s chip is `provenanceLabel()`, which returns `` `MCP · ${serverName}` ``
— and the observed chip reads plain `Projects`, with no `MCP ·` prefix. That
mismatch is unexplained. Possibilities: it is a `SubagentCard` whose status badge
happens to read a scope name; the provenance format has diverged; or the crop cut
the prefix.

**FR-6.1 makes resolving this the first task.** Reproduce the card, read the DOM,
and record the answer in this file before touching styles.

### 2.2 The structural hazard, which is real either way

Independent of which card it was, `ToolCallCard`'s header row is an unstable
combination at narrow widths
([ToolCallCard.tsx:365](../../../packages/chat-surface/src/thread-canvas/ToolCallCard.tsx#L365)):

```ts
const identityLineStyle: CSSProperties = {
  alignItems: "baseline",
  display: "flex",
  flexWrap: "wrap",        // ← chips wrap to a new line…
  gap: "3px 7px",
  minWidth: 0,
};

const toolTitleStyle: CSSProperties = {
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",    // ← …but the title never wraps, it ellipsises
  …
};
```

Four children — title, provenance chip, access label, duration — share a
`flexWrap: wrap` line whose first child is a `nowrap`, ellipsising, flexible
title. The title takes whatever it can get; the remaining chips then wrap onto a
second line and **start wherever the wrap put them**. There is no anchor: no
`margin-left: auto` on a trailing group, no `flex-basis`, no explicit ordering
guarantee. At `wide` the row rarely wraps so it looks fine; at `compact` it wraps
constantly and lands the chips at arbitrary x.

That is exactly the symptom observed, and it is a genuine bug regardless of which
component owns the specific chip.

## 3. Goals & non-goals

**Goals**

- Every chip in an activity-card header is anchored to a defined edge.
- Wrapping, when it happens, is predictable and consistent between the tool card
  and the subagent card (they deliberately share chrome).
- Identify and document which component rendered the observed chip.

**Non-goals**

- Removing chips or changing what they say.
- Changing `activityCardChrome`'s tile size, padding, or type scale — those are
  shared with the subagent family on purpose and are already compact.
- Reformatting the duration string — that is [PRD-07](./PRD-07-tool-metadata.md).

## 4. Design decisions

**D-6.1 — Two anchored clusters, one flexible middle.** Restructure the header
row into three explicit zones instead of one wrapping soup:

```
[tile]  [ title ……………………… ]  [ provenance · access · duration ]  [ status ] [▾]
 fixed   flex:1, minWidth:0     flex:none, margin-left:auto         fixed
```

The metadata cluster becomes a single `flex: none` child pushed right with
`margin-left: auto`. It can no longer land mid-row, because it is anchored to the
right edge and moves as a unit.

**D-6.2 — Wrap the whole cluster, never its members.** If the row cannot fit, the
metadata cluster wraps as one block to the second line, still right-anchored.
Chips never separate from each other.

**D-6.3 — At `compact`, drop rather than wrap.** Per PRD-03 FR-3.9, collapsed
cards at `compact` already shed the summary line and the access chip. Extend the
same principle: the metadata cluster keeps duration and status, drops access.
Dropping a low-value chip beats wrapping the row and doubling the card's height in
the window where height is scarcest.

**D-6.4 — Apply the rule in shared chrome, not per card.** The zone structure
belongs in `ActivityCardChrome` alongside `activityCardHeaderStyle`, so
`ToolCallCard` and `SubagentCard` cannot drift. The chrome file's own docstring
already states the intent — _"Tool calls and subagent fleets intentionally share
this geometry so a conversation can mix the two without the cards reading as
separate UI systems"_ — this PRD makes that true for the header row too.

## 5. UX specification

```
wide / regular
┌──────────────────────────────────────────────────────────────────┐
│ [P] Manual file                    MCP · Projects  read  645ms ✓▾│
│     Create the requested text file in the attached workspace     │
└──────────────────────────────────────────────────────────────────┘

compact
┌──────────────────────────────────┐
│ [P] Manual file        645ms  ✓ ▾│      ← access chip dropped,
└──────────────────────────────────┘        summary line dropped

wrap case (long title, regular)
┌──────────────────────────────────────────────────┐
│ [P] A very long tool title that ellipsises…   ✓ ▾ │
│                       MCP · Projects  read  645ms │  ← cluster wraps intact,
└──────────────────────────────────────────────────┘     still right-anchored
```

**Accessibility.** Zone restructuring is presentational; the accessible name and
the existing `data-testid`s (`tc-chat-tool-{id}-status`, `…-details`, `…-args`,
`…-result`) are unchanged. The status group keeps its `aria-label`.

## 6. User journeys

**J-6.1 — Sarah reads a card in a narrow window (the captured scenario).**
The card's chips sit as one group flush against the right edge, in line with the
status mark. Her eye tracks a straight right margin down the card stack instead of
hunting for chips at four different x positions.

**J-6.2 — A tool with a very long name at `regular`.**
The title ellipsises; the metadata cluster wraps to line two as a unit, still
right-anchored. It does not interleave with the title, and the chips stay together.

**J-6.3 — The same card at 640px.**
The access chip is gone; tile, title, duration, and status remain on one line. The
card is one line tall instead of two. Across six cards that is a saved screenful.

**J-6.4 — A subagent card next to a tool card.**
Both use the shared zone structure from `ActivityCardChrome`, so their right
margins line up and the two read as one system — which is what the chrome file
says it exists to guarantee.

## 7. Functional requirements

| ID     | Requirement                                                                                                                                                                            |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FR-6.1 | **First task:** reproduce the observed card, inspect the DOM, and record in §2.1 which component and which prop produced the `Projects` chip. No style changes until this is answered. |
| FR-6.2 | `ActivityCardChrome` exports a three-zone header structure (leading / flexible title / anchored metadata + status).                                                                    |
| FR-6.3 | The metadata cluster is `flex: none` with `margin-left: auto`. It never renders at a computed x that is neither flush-right nor a wrapped line start.                                  |
| FR-6.4 | The cluster wraps as a unit. Individual chips never separate onto different lines.                                                                                                     |
| FR-6.5 | At `compact`, the access chip is omitted; duration and status remain.                                                                                                                  |
| FR-6.6 | `ToolCallCard` and `SubagentCard` both consume the shared structure. Neither declares its own header flex rules.                                                                       |
| FR-6.7 | All existing `data-testid`s and accessible names are preserved.                                                                                                                        |
| FR-6.8 | At `wide`, a card with a short title renders byte-identically to today (snapshot).                                                                                                     |

## 8. Non-functional requirements

- **NFR-6.1** No change to `activityCardTileStyle`, `activityCardHeaderStyle`
  padding, or the type scale — geometry parity with the subagent family is
  deliberate.
- **NFR-6.2** No JS measurement. This is a flex-structure fix; anchoring must
  come from layout, not from a `ResizeObserver` reading chip widths.

## 9. Acceptance criteria

- [ ] §2.1 is updated with the identified component and prop. (Gates the rest.)
- [ ] At 400px card width with a long title, the metadata cluster's computed left
      edge equals either the row's flush-right position or the wrapped line's start
      — asserted numerically, not by eye.
- [ ] Chips are never split across lines: all cluster children share a
      `getBoundingClientRect().top`.
- [ ] At `compact`, the access chip is absent; at `wide` it is present.
- [ ] A tool card and a subagent card rendered adjacently share a right margin
      within 1px.
- [ ] Snapshot at `wide` with a short title identical to `main`.
- [ ] `npx vitest run --root packages/chat-surface` green.

## 10. Open decisions

| ID    | Question                                                                          | Recommendation                                                                                                                                                    |
| ----- | --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OD-61 | If §2.1 resolves to `SubagentCard`, does this PRD still apply?                    | Yes. The `identityLineStyle` hazard in `ToolCallCard` is real independently and is worth the same fix.                                                            |
| OD-62 | Should the title be allowed to wrap to two lines instead of ellipsising?          | No. A wrapping title makes card height unpredictable, which fights PRD-03's density goal. Ellipsis + `title` tooltip.                                             |
| OD-63 | Should `provenanceLabel`'s `MCP · ` prefix be dropped to save width at `compact`? | Only if §2.1 confirms provenance is the chip. The prefix carries real meaning (this tool came from an MCP server); prefer dropping `access` first, as D-6.3 does. |
