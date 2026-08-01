# PRD-01 — Thread switching from the cockpit

**Severity:** P0 · **Depends on:** [PRD-00](./PRD-00-overview.md) · **Surface:** `packages/chat-surface/src/shell`, `destinations/run`

## 1. Problem statement

In the captured window, the entire left side of 0xCopilot is 48px of unlabeled
glyphs. There is no conversation list, no recent threads, and no control anywhere
in the frame that would produce one. **You cannot switch conversations from the
Run cockpit at any window width.**

Claude Desktop, at a comparable size, holds a labelled sidebar with fifteen named
Recents. Codex holds Projects plus named threads. Both treat "which conversation
am I in, and what else is there" as the permanent left edge of the product.

This is not a narrow-window regression — it is true at 2560px too. It is simply
_unbearable_ in a window, because the rail's six glyphs are then the only
navigation on screen.

## 2. Current state

The cause is one line
([ChatShell.tsx:65](../../../packages/chat-surface/src/shell/ChatShell.tsx#L65)):

```ts
const FULL_BLEED_DESTINATIONS: ReadonlySet<ShellDestinationSlug> = new Set([
  "chats",
  "run",
]);
```

`run` is full-bleed, so `showContextPanel` is forced `false`
([ChatShell.tsx:267](../../../packages/chat-surface/src/shell/ChatShell.tsx#L267))
regardless of what a host passes. `run` is also in `SUPPRESS_TOPBAR`
([ChatShell.tsx:49](../../../packages/chat-surface/src/shell/ChatShell.tsx#L49)),
so there is no `Topbar` to hang a toggle on either. The cockpit's own header,
`RunHeader`, renders exactly three things: a centred product wordmark, an em
dash, and the mode name — plus a right-aligned Focus/Studio segmented control.
No navigation.

Switching conversations therefore costs a **destination change**: rail → Chats →
pick → back to Run. You lose the cockpit, and the cockpit is the product's front
door (`defaultDestinationForProfile(_) → "run"`).

**The good news: the data layer already exists and is not the problem.**

| Asset                                                        | What it gives us                                                                                                                     |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| `useChatsArchive()` → `ChatsArchiveController`               | bucketed conversations, `hasMore`, `onLoadMore`, `onTogglePin`, `onToggleArchive`, `retry`, and a live `conversation_changed` stream |
| `toChatArchiveRow(conversation)` (`projections/chats.ts:58`) | the pure api-types → row projection, already shared by both hosts                                                                    |
| `ContextPanel`                                               | a 224px titled column with search + primary action + scrollable body                                                                 |
| `CommandPalette` + `PaletteSearchPort`                       | ⌘K, already wired by both host bootstraps                                                                                            |

Nothing needs to be fetched, projected, or invented. The list exists; it is
mounted in exactly one place that the cockpit cannot reach.

## 3. Goals & non-goals

**Goals**

- Switch conversations without leaving the Run cockpit, at every width class.
- One thread-list implementation, shared by the Chats destination and the cockpit.
- A visible, discoverable control — not a keyboard-only secret.

**Non-goals**

- Redesigning the Chats destination. It stays as the full archive (search,
  buckets, bulk actions); this PRD adds a _switcher_, not a second archive.
- Multi-run selection chrome inside the cockpit. `RunMultiSelect` was
  deliberately deleted (FR-3.26 withdrawn); do not reintroduce a run-picker rail.
  This is conversation switching, which is a different axis.
- Removing `run` from `FULL_BLEED_DESTINATIONS` (see D-1.1).

## 4. Design decisions

**D-1.1 — Do not un-full-bleed `run`.** Tempting, but wrong: it would give the
cockpit a permanent 224px column at _every_ width, which is exactly the
full-screen-only thinking this program exists to remove. It would also re-open
the "unfed panel shows `Nothing here yet.`" regression the content gate was
written to close. The cockpit gets a **switcher it owns**, not a shell column.

**D-1.2 — One component, two presentations. Dock wherever docking fits.**
New `ThreadSwitcher` in `shell/`, built on `ContextPanel` chrome, fed by
`useChatsArchive`.

> ⚠️ **REVISED after implementation.** The first cut chose the presentation from
> the width CLASS: docked at `wide`/`regular`, modal overlay + scrim at
> `compact`. Driving it live showed why that is wrong — at an ordinary 640px
> window the scrim covered the cockpit's own composer, so you could browse
> threads or type, never both. Every reference product (ChatGPT, Claude, Codex)
> keeps the panel docked and the composer usable at that size.
>
> The presentation is now chosen by the cockpit's **observed container width in
> pixels**, not by the class — whether a panel can share the row with a usable
> composer is arithmetic about this container, not a chrome-density band.

| Container width                           | Presentation                                                          |
| ----------------------------------------- | --------------------------------------------------------------------- |
| `>= 552px` (`THREAD_SWITCHER_DOCK_FLOOR`) | **Docked.** 224px column, or 200px when the width class is `compact`. |
| `< 552px`                                 | **Overlay.** 260px, scrim, Esc closes, focus returns.                 |

552 is a **cockpit-container** width — the observer sits inside the 48px app
rail, so a 640px window measures 592px here. It is deliberately below `640 − 48`
(the narrowest cockpit the desktop app can produce, given PRD-00 OD-02's
`minWidth: 640`), so a real desktop user always gets a dock and never a scrim. The overlay
survives as a safety net for below-floor embeds (a phone-width web viewport, a
crushed split pane), not as a mode we design people into.

At 640px the arithmetic is `48 (rail) + 200 (panel) + 392 (canvas)` — the same
~31% sidebar proportion the reference products use at comparable widths.

**D-1.3 — The trigger lives in `RunHeader`'s left slot.** `RunHeader` currently
has an absolutely-positioned centred title layer with `pointerEvents: "none"` and
a right-aligned segmented control — the left of the bar is empty. That is where
the panel toggle goes, matching Claude and Codex, which both put the sidebar
toggle at top-left. PRD-02 fills the rest of that bar.

**D-1.4 — Persist per width class, not globally.** Storing one "panel open" bool
means opening the app narrow and later maximising leaves the panel shut. Key the
`KeyValueStore` entry by class: `run.thread_switcher_open.{wide,regular}`.
`compact` is never persisted — an overlay always opens closed.

**D-1.5 — ⌘K is not the answer, but it is the accelerator.** The palette already
exists and should gain conversation hits if it lacks them. It does not replace a
visible list: a palette answers "I know what I want", a list answers "what is
there". Both references ship both.

## 5. UX specification

```
┌─ RunHeader (38px) ─────────────────────────────────────────────┐
│ [▤]  0xCopilot — Focus                       [ Focus │ Studio ] │   ← [▤] is new
└────────────────────────────────────────────────────────────────┘
┌──────────────┬─────────────────────────────────────────────────┐
│ Threads    ⊕ │                                                 │
│ ┌──────────┐ │                 transcript                      │
│ │ search   │ │                                                 │
│ └──────────┘ │                                                 │
│ PINNED       │                                                 │
│ • Manual fi… │                                                 │
│ TODAY        │                                                 │
│ ○ Fix Linea… │                                                 │
│ ○ Model pil… │                                                 │
└──────────────┴─────────────────────────────────────────────────┘
```

- **Head** — title "Threads", a `primaryAction` "New" (⊕) wired to the same
  new-run intent as ⌘N, and `ContextPanel`'s existing `search`.
- **Rows** — reuse `toChatArchiveRow`. Each row: status dot, title (one line,
  ellipsised), relative time. The **active conversation is marked** — this is the
  half Claude and Codex both do and we currently cannot do at all, because the
  cockpit does not render a list to mark.
- **Buckets** — Pinned / Today / Earlier, from `useChatsArchive`'s existing
  section keys. `onLoadMore` on scroll-end.
- **Empty** — `ContextPanel`'s existing empty state; do **not** mint new copy.
- **Overlay (compact)** — 260px, slides from x=48 (rail edge), scrim
  `--color-bg` at 60%, closes on Esc / scrim click / row activation.

**Accessibility.** The toggle is a real `<button>` with
`aria-expanded` + `aria-controls`. The docked panel is the existing
`<aside aria-label="Threads panel">` from `ContextPanel`. The overlay is
`role="dialog" aria-modal="true"` with focus trapped and restored to the toggle
on close. Rows are a `<ul>`/`<li>` list of buttons, active row `aria-current="true"`.

**Motion.** Overlay slide is 140ms ease-out, zeroed under
`[data-reduce-motion]` and `prefers-reduced-motion` — the same gate `RunHeader`'s
pulse and `ToolCallCard`'s spinner already use.

## 6. User journeys

**J-1.1 — Sarah switches threads mid-run, windowed (the captured scenario).**
She is in a 900px window (`regular`), watching a filesystem run finish. She wants
the Linear thread from this morning. She clicks **▤** in the header; the Threads
column opens to 224px and the canvas reflows. "Fix Linear MCP 401" is under
TODAY. She clicks it; the cockpit rebinds to that conversation. The panel stays
open — she is browsing. She clicks **▤** again to reclaim the width. Next launch
at this size, it is still closed.
_Today: impossible without leaving the cockpit._

**J-1.2 — Marcus at full screen never notices a change.**
He opens the app maximised. The Threads column is docked at 224px, exactly as
Claude's sidebar is. `wide` renders as it always has, plus the column. He never
touches the toggle.

**J-1.3 — A very small window.**
Sarah pulls the window to 640px. The docked column would leave 368px of canvas,
so it is not docked — the toggle now opens an overlay. She picks a thread; the
overlay closes on activation and she is reading the transcript at full width.
Esc would have closed it too.

**J-1.4 — Keyboard-first.**
Marcus hits ⌘K, types "linear", picks the conversation hit. He never opens the
panel. The panel's state is unchanged.

**J-1.5 — A run completes in another thread while browsing.**
Sarah has the panel open on the Manual-file thread. A different conversation
finishes. `useChatsArchive`'s `conversation_changed` stream updates that row in
place — new relative time, status dot settles. Her scroll position and the active
mark do not move.

**J-1.6 — The list fails to load.**
The archive request errors. The panel body shows the controller's error state and
a **Retry** (`ChatsArchiveController.retry`). The cockpit and the running stream
are untouched — a failed sidebar must never take down the transcript.

## 7. Functional requirements

| ID      | Requirement                                                                                                                 |
| ------- | --------------------------------------------------------------------------------------------------------------------------- |
| FR-1.1  | `ThreadSwitcher` renders from `useChatsArchive` + `toChatArchiveRow`. No second fetch path, no duplicated projection.       |
| FR-1.2  | A toggle button renders in `RunHeader`'s left slot with `aria-expanded` / `aria-controls`.                                  |
| FR-1.3  | Presentation resolves from `useShellWidthClass()`: `wide`/`regular` docked, `compact` overlay.                              |
| FR-1.4  | Open state persists per width class under `run.thread_switcher_open.{class}`. Default: open at `wide`, closed at `regular`. |
| FR-1.5  | `compact` overlay is never persisted and always opens closed.                                                               |
| FR-1.6  | The active conversation is visually marked and carries `aria-current="true"`.                                               |
| FR-1.7  | Activating a row rebinds the cockpit **without remounting** `TcChat` — reuse the existing `runId`/`selectRun` seam.         |
| FR-1.8  | At `compact`, activation closes the overlay. At `wide`/`regular`, the panel stays open.                                     |
| FR-1.9  | Esc and scrim click close the overlay and restore focus to the toggle.                                                      |
| FR-1.10 | The panel's loading / empty / error states never block or unmount the transcript.                                           |
| FR-1.11 | `run` stays in `FULL_BLEED_DESTINATIONS`; `ChatShell`'s grid is unchanged by this PRD.                                      |
| FR-1.12 | ⌘K returns conversation hits. If `PaletteSearchPort` already covers them, assert it; if not, add the hit kind.              |
| FR-1.13 | Both host binders are updated in the same PR. Neither host gains a new required `ShellHostBinding` field.                   |

## 8. Non-functional requirements

- **NFR-1.1** Opening the panel issues no new SSE subscription. `useChatsArchive`
  is mounted once per cockpit, not per open.
- **NFR-1.2** Rows are keyed by `ConversationId`; a stream update must not
  re-order or re-mount unaffected rows.
- **NFR-1.3** The panel must not read the run event stream. One event projection
  (FR-3.3) stays intact.

## 9. Acceptance criteria

- [ ] With the cockpit mounted at 1400px, a Threads column is present, docked, and
      lists conversations from the archive controller.
- [ ] The active conversation carries `aria-current="true"`; switching moves the mark.
- [ ] At 640px the toggle opens an overlay with `role="dialog"`, `aria-modal="true"`;
      Esc closes it and focus returns to the toggle.
- [ ] Activating a row at 640px closes the overlay; at 1400px it does not.
- [ ] Toggling at `wide`, remounting, and re-rendering at `wide` restores the state;
      the `regular` key is independent.
- [ ] Switching conversations does not remount `TcChat` (mount-counter assertion).
- [ ] A rejected archive request renders retry inside the panel while a streaming
      transcript keeps appending.
- [ ] `grep -c "@0x-copilot/chat-surface" ` in both binders shows the switcher wired
      on web and desktop; both typecheck.
- [ ] Reduced-motion: overlay transition is `none` under both gates.
- [ ] Verified in the packaged desktop build, not only in vitest.

## 10. Open decisions

| ID    | Question                                                          | Recommendation                                                                                                                |
| ----- | ----------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| OD-11 | Should the Chats destination now be redundant?                    | No. Keep it as the archive (bulk pin/archive, full search). The switcher is a navigation aid. Revisit once usage data exists. |
| OD-12 | Should the switcher also appear on other full-bleed destinations? | Only `chats` is also full-bleed, and it _is_ the list. So: no. Scope to `run`.                                                |
| OD-13 | Docked width — 224px (`CONTEXT_PANEL_WIDTH`) or narrower?         | Reuse 224. Minting a second panel width is exactly the drift `CONTEXT_PANEL_WIDTH` exists to prevent.                         |
