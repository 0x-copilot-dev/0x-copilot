# PRD-00 — Windowed Mode: Program Overview & Responsive Substrate

**Status:** Draft · **Branch:** `claude/0xcopilot-windows-mode-a89c12` · **Surface:** `packages/chat-surface` (SSOT), both hosts

This program fixes what breaks when the 0xCopilot desktop app runs in a **normal
window** rather than full-screen. It was opened from a three-way comparison of
the same moment in three products — Claude Desktop, Codex, and 0xCopilot — at
comparable window sizes.

The nine section PRDs:

| PRD                                       | Finding                                                    | Severity |
| ----------------------------------------- | ---------------------------------------------------------- | -------- |
| [PRD-01](./PRD-01-thread-switching.md)    | No thread list, and no way to get one                      | P0       |
| [PRD-02](./PRD-02-run-header.md)          | The run header is 100% decoration; the goal is `clip()`-ed | P0       |
| [PRD-03](./PRD-03-transcript-density.md)  | Transcript is all process, no answer                       | P0       |
| [PRD-04](./PRD-04-recovered-failures.md)  | A recovered failure stays red forever                      | P1       |
| [PRD-05](./PRD-05-content-grid.md)        | Two content grids in one column                            | P1       |
| [PRD-06](./PRD-06-chip-anchoring.md)      | Card-header chips anchor to nothing                        | P2       |
| [PRD-07](./PRD-07-tool-metadata.md)       | Tool metadata is inconsistent (units, missing durations)   | P2       |
| [PRD-08](./PRD-08-timeline-legibility.md) | The bottom timeline reads as unlabeled dots                | P1       |
| [PRD-09](./PRD-09-edge-affordances.md)    | No message actions; the rail identity is a bare initial    | P2       |

---

## 1. Problem statement

Claude Desktop and Codex treat a small window as a **budget problem**: scarce
pixels go to navigation and to the answer. 0xCopilot spends them on chrome and
on process.

That is not a styling accident — it is structural. The shared surface has **no
responsive layer at all**:

```
media queries in packages/chat-surface/src:
  shell/          0
  composer/       0
  destinations/   1
  activity/       1
  messages/       2
  thread-canvas/  2
  subagents/      2
container queries anywhere:  0
```

Every shell dimension is a fixed constant:

| Constant               | Value | Source                                                                                      |
| ---------------------- | ----- | ------------------------------------------------------------------------------------------- |
| `RAIL_WIDTH`           | 48    | [AppRail.tsx:16](../../../packages/chat-surface/src/shell/AppRail.tsx#L16)                  |
| `CONTEXT_PANEL_WIDTH`  | 224   | [ContextPanel.tsx:3](../../../packages/chat-surface/src/shell/ContextPanel.tsx#L3)          |
| `TOPBAR_HEIGHT`        | 46    | [Topbar.tsx:12](../../../packages/chat-surface/src/shell/Topbar.tsx#L12)                    |
| `RunHeader` height     | 38    | [RunHeader.tsx:305](../../../packages/chat-surface/src/destinations/run/RunHeader.tsx#L305) |
| `--chat-content-width` | 68rem | [design-system/styles.css:171](../../../packages/design-system/src/styles.css#L171)         |

`ChatShell` composes them into a grid that is computed **once**, from
destination identity alone — never from available width
([ChatShell.tsx:282](../../../packages/chat-surface/src/shell/ChatShell.tsx#L282)):

```ts
const gridTemplateColumns = [
  `${APP_RAIL_WIDTH}px`,
  ...(showContextPanel ? [`${CONTEXT_PANEL_WIDTH}px`] : []),
  "1fr",
  ...(showRightRail ? [rightOpen ? `${RIGHT_RAIL_WIDTH}px` : "0"] : []),
].join(" ");
```

**The user-visible harm:** at ~900px the app does not become a smaller version
of itself — it becomes a **cropped** version of itself. The 48px rail, the 38px
header, the full-size tool cards and the timeline strip all keep their
full-screen footprint, and the only thing that yields is the transcript. In the
captured session, six tool cards consumed roughly 55% of the visible transcript
while the actual answer was one line at the bottom.

## 2. What "done" means

1. **The shell adapts.** Width classes are derived from the container, and the
   rail / panels / header / transcript each have a defined compact behaviour.
2. **Navigation survives.** Switching conversations is possible from the cockpit
   at every width (PRD-01).
3. **The answer wins.** At any width, an answer is more visually prominent than
   the process that produced it (PRD-03).
4. **Chrome is honest.** Every persistent row either carries information or is
   not drawn (PRD-02, PRD-08).
5. **Boundaries intact.** `chat-surface` stays substrate-agnostic; no `window`,
   no `matchMedia`, no viewport listeners; both hosts stay in lockstep.

## 3. Goals & non-goals

**Goals**

- A single, shared, container-scoped responsive primitive in the shell.
- Compact behaviour specified per surface, not improvised per component.
- Close the nine findings, each with its own acceptance criteria and tests.

**Non-goals**

- Redesigning the 6-destination solo model or the Studio/Focus mode split.
- Mobile / touch layouts. The target is a **resized desktop window**, floor
  ~640px wide. Below that we degrade gracefully, we do not design for it.
- `apps/frontend` polish. Web must stay green and inherits the shared surface
  for free, but is not the target (root `CLAUDE.md`: desktop-first, web
  deprecated).
- Reflowing Studio mode's resizable rail (`useRailWidth`) into the new scale —
  it is a user-set preference on a different axis. It only gains a clamp
  (FR-0.7).

---

## 4. The cross-cutting decision — container breakpoints, not media queries

`chat-surface` cannot use `@media`, and should not want to. Its eslint boundary
bans `window`, `document`, `matchMedia` and friends
([eslint.config.js:55](../../../packages/chat-surface/eslint.config.js#L55)), and
more importantly **the viewport is the wrong signal**. The cockpit is mounted
inside a grid cell whose width depends on the rail, the context panel, the right
rail, and the Studio rail split. A viewport query would tell a component the
window is 1400px wide while it is being rendered into a 380px column.

**We do not need to invent this.** The package already contains exactly the right
pattern, shipped and tested, in
[`useInboxLayout.tsx`](../../../packages/chat-surface/src/destinations/inbox/useInboxLayout.tsx):

> - No JS `window` resize listeners. The hook observes the destination container
>   via `ResizeObserver`. This keeps the breakpoint local to the destination —
>   desktop/web embeds with shrunk side rails or split workspace panes inherit
>   the correct mode for free, because the container width — not the viewport —
>   is what matters.
> - Works in jsdom: `ResizeObserver` is a class, and tests can shim it to control
>   the observed width directly. No layout/paint required.
> - One source of truth for the breakpoint constant.

`ResizeObserver` is **not** in the banned-globals list. This is sanctioned code.

**Decision (D-0.1):** generalize that hook into a shell-level primitive and make
Inbox a consumer of it, so there is one observer implementation in the package
rather than one per destination. Inbox keeps its own 960px threshold — that is a
destination-local _pane_ decision, a different axis from the shell's width class
— but stops owning the mechanism.

**Decision (D-0.2):** the shell publishes its width class as a **data attribute**
on the shell root, so plain CSS in any descendant can respond without threading
a prop through ten components:

```html
<div
  data-component="chat-shell"
  data-active-destination="run"
  data-width="compact"
></div>
```

This mirrors the existing, shipped precedent — `data-right-rail-open` on the same
element ([ChatShell.tsx:324](../../../packages/chat-surface/src/shell/ChatShell.tsx#L324))
— and the `[data-reduce-motion]` gate used by `RunHeader` and `ToolCallCard`.

⚠️ **Cascade hazard.** Host stylesheets that re-declare package-owned class names
win the cascade on desktop; this has bitten us before (PR #459). Any
`[data-width="compact"]` rule authored in the package must be verified in the
**built desktop bundle**, not just in the package source.

### 4.1 The width scale (SSOT)

New file: `packages/chat-surface/src/shell/layout.ts`.

| Class     | Container width | Intent                                                     |
| --------- | --------------- | ---------------------------------------------------------- |
| `compact` | `< 720px`       | One column. Side panels become overlays. Chrome minimises. |
| `regular` | `720–1119px`    | One side panel at a time. Full chrome.                     |
| `wide`    | `>= 1120px`     | Today's layout, unchanged.                                 |

Thresholds are the **only** place these numbers appear. 720 is chosen so that a
720px container still affords `48 + 224 + 448`; below it, a 224px panel is taking
a third of the screen and must become an overlay. 1120 is `48 + 224 + 848` — the
point at which the transcript still gets a comfortable measure alongside a panel.

`wide` must render **byte-identically to today**. This is a pure extension: the
program adds two narrower behaviours and changes nothing at full screen.

### 4.2 API

```ts
// packages/chat-surface/src/shell/layout.ts
export const SHELL_BREAKPOINTS = { compact: 720, regular: 1120 } as const;
export type ShellWidthClass = "compact" | "regular" | "wide";
export function widthClassFor(px: number): ShellWidthClass;

// packages/chat-surface/src/shell/useContainerWidth.ts
export function useContainerWidth(
  ref: RefObject<HTMLElement | null>,
  defaultWidthPx?: number,
): number;

// packages/chat-surface/src/shell/useShellWidthClass.ts
export function useShellWidthClass(): ShellWidthClass; // reads context
```

`ChatShell` owns the observer and provides the class via context;
`useShellWidthClass()` is the read hook for any descendant. Destinations that
need a _different_ threshold against the same observed width (Inbox) call
`useContainerWidth` directly.

**SSR / pre-observer default is `wide`** — matching `useInboxLayout`'s
`defaultWidthPx` stance, so the first paint is the full layout and narrowing is
an opt-in transition rather than a flash of compact chrome.

---

## 5. Functional requirements

| ID     | Requirement                                                                                                                                       |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| FR-0.1 | `layout.ts` is the single source of the breakpoint numbers. No component may hard-code 720 or 1120.                                               |
| FR-0.2 | `useContainerWidth` observes via `ResizeObserver` only. No `window`, no `matchMedia`, no resize listener. Lint must keep passing unchanged.       |
| FR-0.3 | `ChatShell` installs one observer on its root and provides the resulting `ShellWidthClass` through context.                                       |
| FR-0.4 | The shell root emits `data-width={class}`. Absent only before the first observer callback.                                                        |
| FR-0.5 | At `wide`, every rendered output is byte-identical to the pre-change build. Enforced by a snapshot test.                                          |
| FR-0.6 | `useInboxLayout` is refactored onto `useContainerWidth`. Its 960px threshold and its three-mode public API are unchanged.                         |
| FR-0.7 | `clampRailWidth` gains an upper bound of `containerWidth - 320` so a persisted 584px Studio rail cannot squeeze the canvas to nothing at compact. |
| FR-0.8 | `ResizeObserver` absence degrades to the default width class, never throws (mirrors `useInboxLayout.tsx:160`).                                    |
| FR-0.9 | Width-class transitions must not remount subtrees — in particular never the `TcChat` mount, which would restart streaming.                        |

## 6. Non-functional requirements

- **NFR-0.1** Resize must not thrash: the observer callback sets state only when
  the derived _class_ changes, not on every pixel.
- **NFR-0.2** No layout/paint dependency in tests. jsdom shims `ResizeObserver`
  and drives width directly, as Inbox's tests already do.
- **NFR-0.3** No new dependency.
- **NFR-0.4** Both host binders keep compiling with no signature change — this
  PRD adds no host-owned prop. (`ShellHostBinding` is a total contract; adding a
  required field would break both hosts by design.)

## 7. User journeys

**J-0.1 — Sarah drags the window narrow while a run streams.**
She grabs the window edge and pulls from 1400px to 900px. The rail stays put, the
context panel (if open) becomes an overlay, the transcript reflows. The run keeps
streaming — no reconnect, no lost tokens, no scroll jump. She drags back out and
the layout returns to exactly what it was.

**J-0.2 — Marcus splits Studio 50/50 in a 1500px window.**
The canvas column is now ~600px. Even though the _window_ is wide, the canvas
resolves to `regular` because the observer is on its container, not the viewport.
Chips inside the canvas wrap correctly instead of being told they have 1500px.

**J-0.3 — A first paint at an unknown width.**
The app opens; the observer has not fired. The shell renders `wide`. One frame
later the real class applies. Nothing flickers between two _compact_ states —
the only possible transition is wide → narrower, once.

## 8. Acceptance criteria

- [ ] `SHELL_BREAKPOINTS` is the only literal 720/1120 in the package (grep-verified).
- [ ] `ChatShell` root carries `data-width` in all three classes; asserted per class.
- [ ] Rendering `ChatShell` at 1400px produces markup identical to `main` (snapshot).
- [ ] `useInboxLayout`'s existing test suite passes unmodified after the refactor.
- [ ] A test drives a shimmed `ResizeObserver` 1400 → 900 → 640 → 1400 and asserts
      the class sequence `wide → regular → compact → wide` with no remount of the
      chat subtree (assert stable node identity / a mount-counter ref).
- [ ] Removing the `ResizeObserver` global leaves the shell rendering at `wide`
      with no thrown error.
- [ ] `npx vitest run --root packages/chat-surface` green; eslint green with the
      boundary rules untouched.
- [ ] Desktop bundle verified: `data-width` present and compact rules apply in the
      **packaged** app, not only in the package tests (guards the PR #459 class).

## 9. Sequencing

```
Wave A (this PRD)      PRD-00 responsive substrate ─────────────┐
                                                                 │ prerequisite
Wave B (structural)    PRD-01 thread switching                  │
                       PRD-02 run header                        │
                       PRD-03 transcript density  ←─────────────┘

Wave C (independent — no dependency on Wave A, land in parallel)
                       PRD-04 recovered failures
                       PRD-05 content grid
                       PRD-07 tool metadata
                       PRD-08 timeline legibility

Wave D (polish)        PRD-06 chip anchoring   (easier after PRD-05)
                       PRD-09 edge affordances
```

Wave C is genuinely independent — those four are correctness/consistency bugs
that are _worse_ in a small window but are not caused by width. They can be
picked up immediately by anyone while Wave A lands.

## 10. Open decisions

| ID    | Question                                                            | Recommendation                                                                                                                             |
| ----- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| OD-01 | Should `compact` also collapse the 48px rail to 0 with a hamburger? | **No.** 48px is 5% of a 900px window and it is the only always-available navigation. Keep it.                                              |
| OD-02 | Does the desktop host need a minimum window size?                   | Yes — set `minWidth: 640` in the `BrowserWindow` options so we never have to design below the floor. Host-side, one line, do it in Wave A. |
| OD-03 | Should Inbox's 960 migrate onto the shared scale?                   | Not now. Different axis (pane count vs shell chrome). Revisit once two destinations want it.                                               |
