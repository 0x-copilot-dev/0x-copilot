# PR 3.7.3 — Sources scale handling (connector grouping + reverse handshake)

> **Status:** Draft
> **Owner:** frontend
> **Size:** S. Two presentational changes; uses existing data and scroll APIs.
> **Depends on:** PR 3.7 (humanizer), PR 3.1 (chip ↔ row forward handshake).
> **Reads alongside:** [`apps/frontend/src/features/chat/components/workspace/SourcesTab.tsx`](../../apps/frontend/src/features/chat/components/workspace/SourcesTab.tsx), [`apps/frontend/src/features/chat/ChatScreen.tsx`](../../apps/frontend/src/features/chat/ChatScreen.tsx).

---

## 0 · TL;DR

Two changes for runs with many sources:

1. When `ordered.length >= 5`, soft-group the rows by connector — `Web (3)` `Notion (2)` headers — so the user can scan by source type. Below 5, keep the flat list (no premature grouping).
2. Clicking a `SourceRow` scrolls the chat thread to the assistant message that first cited that source, mirroring today's chip → row handshake. Same `data-citation-id` plumbing — no new state.

LoC: FE ≈ 110.

---

## 1 · PRD

### 1.1 Problem

- A research run that issues 2–3 web searches plus reads from Notion can produce 10–15 sources. The current flat-by-citation_count list is hard to scan: same Card chrome, no visual section breaks.
- The forward handshake (chip click → workspace pane scrolls to row) exists. The reverse — "I see this Notion page in the panel; where in the conversation did the agent cite it?" — does not. Users mentally lose track of which assistant message introduced which source on long threads.

### 1.2 Goals

1. At ≥5 sources, render connector sections in the order: highest-citation-count connector first, then alphabetical. Each header is `{Connector} ({count})` with a thin divider above; rows within a section keep today's `citation_count` sort.
2. Below 5 sources: flat list, no grouping (avoids cognitive overhead for the common case).
3. Clicking a `SourceRow`'s title (not the favicon — that's reserved for hover preview) scrolls the chat to the first assistant message that contains a chip resolving to that citation, and briefly highlights the chip.
4. Existing forward handshake (`focusCitationId` prop scrolls panel into view) keeps working unchanged.

### 1.3 Non-goals

- No collapse/expand on group sections. Always-open keeps it visually predictable.
- No filter chips ("show only Web") — premature for a list this size; revisit if users actually have 30+ sources.
- No multi-select / bulk actions on rows.
- No animation on the chip-highlight other than a 1.5s outline pulse.

### 1.4 Success criteria

- 4 sources: flat list as today.
- 6 sources from {web×3, notion×2, drive×1}: three sections, in that order.
- Click a Notion row → chat scrolls to the first message with that `[c<id>]` chip, chip outline pulses.
- Source not yet cited in any message (e.g. archive seed only) → click no-ops gracefully (no scroll, no error).

---

## 2 · Spec

### 2.1 Grouping

In `SourcesTab.tsx`, after `sourcesByCitationCount` produces `ordered`:

```ts
const grouped = ordered.length >= 5 ? groupByConnector(ordered) : null;
```

`groupByConnector(sources)` returns an array of `{connector: string; total: number; rows: readonly SourceEntry[]}` sorted by `total` desc then by `connector` asc.

Render branches:

- `grouped === null` → existing `<ul>` of `<SourceRow>`.
- `grouped !== null` → `<section>` per group, `<header>` = `humanizeConnector(connector) + ` `(${total})``, then `<ul>`.

CSS: section header is the same row height as a `SourceRow` minus the Card chrome — small caps, neutral color, top-border for separation.

### 2.2 Reverse handshake

`SourceRow.tsx` `onSelect` already fires when the row's title button is clicked. Today consumers (`SourcesTab`) just no-op. Wire it up:

`ChatScreen.tsx` passes `onSelect={handleSourceSelect}` to `<SourcesTab>`. `handleSourceSelect(source)`:

1. Look up the first message in the active conversation whose `chips` (the per-message citation_id list maintained by `chatModel`) contains `source.citation_id`.
2. If found: scroll the chat to that message via `messageNode.scrollIntoView({block: "center"})` and set a transient `pulseCitationId` state.
3. The chip with `data-citation-id={pulseCitationId}` gets a temporary CSS class `citation-chip--pulse` for 1500ms.

Mapping `citation_id → message_id` lives in the `citationsRegistry` (per-run map). Add a one-method helper `firstMessageForCitation(registry, citationId): string | null`. If the registry doesn't yet track per-message provenance, extend it during the chip-render pass — each time `MarkdownLink` resolves a chip, it can register `(citationId, messageId)` into a small new sub-map.

### 2.3 Chip pulse CSS

Add to `styles.css`:

```css
.citation-chip--pulse {
  animation: citation-pulse 1.5s ease-out;
}
@keyframes citation-pulse {
  0%,
  100% {
    box-shadow: none;
  }
  20%,
  60% {
    box-shadow: 0 0 0 2px var(--color-accent);
  }
}
```

### 2.4 Tests

- 4 sources → flat list (no `<section>`s).
- 5+ sources → grouped, sections sorted by total desc.
- Reverse handshake: row click → first-citing message scrolls into view, chip gains pulse class then loses it after 1500ms.
- Source with no message (archive-seeded but never cited in current loaded thread) → click is no-op, no console error.

---

## 3 · Out of scope / future

- Collapse / expand of group sections.
- Filter chips for connectors.
- "Jump to next citation" keyboard shortcut.
- Cross-conversation source aggregation views.
