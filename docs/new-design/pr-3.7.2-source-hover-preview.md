# PR 3.7.2 — Source hover preview card

> **Status:** Draft
> **Owner:** frontend
> **Size:** S. One presentational popover, two consumers (`SourceRow` + `CitationChip`).
> **Depends on:** PR 3.7 (favicons), PR 3.1 (chips + sources tab).
> **Reads alongside:** [`apps/frontend/src/features/chat/components/citations/CitationChip.tsx`](../../apps/frontend/src/features/chat/components/citations/CitationChip.tsx), [`apps/frontend/src/features/chat/components/citations/SourceRow.tsx`](../../apps/frontend/src/features/chat/components/citations/SourceRow.tsx).

---

## 0 · TL;DR

Today, hovering an inline `[1]` chip shows a one-line title tooltip via the chip's `title` attribute. Hovering a `SourceRow` in the panel shows nothing. To read the snippet or freshness the user has to open the source URL in a new tab. This PR adds a `<SourcePreviewCard>` popover that opens on hover (200ms delay) on both surfaces and shows title + snippet + connector + freshness in a 320×140 card. Click on the card itself opens the source URL.

LoC: FE ≈ 130 (popover + hook + CSS); zero wire / state changes.

---

## 1 · PRD

### 1.1 Problem

- Inline chips are number-only by design (numbered pill, accent color). The model writes "as noted [c1]…" — the user wants to know what `[c1]` is without leaving their place in the prose.
- The native `title="…"` attribute renders as an OS tooltip with browser-default styling and no markdown / no snippet — only the title string we passed to the `<a>`.
- In the Sources tab, the row already shows snippet inline. But hovering an inline chip doesn't take the user there directly, and the snippet is buried in the second line of small text.

### 1.2 Goals

1. Hovering a `CitationChip` for >200ms opens a fixed-position card with: connector glyph/favicon, title (linkable), connector pill, snippet, freshness.
2. Hovering a `SourceRow`'s glyph opens the same card (consistent affordance — chip and row are conceptually the same source).
3. Card is keyboard-accessible: focusing the chip via Tab opens the card; Esc closes; clicking outside closes.
4. The card itself is interactive — links inside are clickable; the card doesn't dismiss on its own hover.
5. Single component, single popover instance per page (managed by a context). No double-cards if user pans across many chips.

### 1.3 Non-goals

- No iframe / readability preview of the source URL. Cross-origin will block most; not worth the complexity.
- No image / OG-card thumbnails. Not yet — defer until we have enough enterprise data to know it's worth the network cost.
- No "open in workspace" affordance from the card (the chip ↔ row handshake already does this on click).
- No card on touch devices — a tap on the chip already opens the source URL; hover doesn't translate. Use `(hover: hover)` media query to gate.

### 1.4 Success criteria

- Hovering `[1]` for ~200ms shows a card; moving away dismisses within 100ms.
- Tabbing into a chip with keyboard focus opens the card; Esc closes.
- Card opens at the chip's position with collision avoidance (flip above when near bottom of viewport).
- Same card shape regardless of trigger (chip vs row glyph).

---

## 2 · Spec

### 2.1 New `<SourcePreviewProvider>` and `<SourcePreviewCard>`

Lives in `apps/frontend/src/features/chat/components/citations/SourcePreview.tsx`. Context exposes:

```ts
interface SourcePreviewApi {
  open(anchor: HTMLElement, source: SourceEntry): void;
  close(): void;
  isOpenFor(citationId: string): boolean;
}
```

A single `<SourcePreviewCard>` portal-mounts at the document root. Provider is mounted once at the chat shell level (where `CitationsProvider` already lives). The card subscribes to a state slot — only one card visible at a time.

### 2.2 Hover/focus hook: `useSourcePreviewTrigger(source)`

Returns props to spread on the trigger element (chip, row glyph):

```ts
const triggerProps = useSourcePreviewTrigger(source);
<a {...triggerProps} className="citation-chip">…</a>
```

Spreads `onPointerEnter` (start 200ms timer to open), `onPointerLeave` (start 100ms timer to close — cancels if pointer moves into card), `onFocus` (open immediately), `onBlur` (close).

### 2.3 Card markup

```tsx
<aside role="dialog" aria-label={source.title} className="source-preview-card">
  <header>
    <SourceFavicon source={source} size="sm" />
    <a href={source.source_url ?? "#"} target="_blank" rel="noreferrer">{source.title}</a>
    <Badge tone="neutral">{humanizeConnector(source.source_connector)}</Badge>
  </header>
  {source.snippet ? <p>{source.snippet}</p> : null}
  <footer>{sourceFreshnessLabel(...)}</footer>
</aside>
```

Width: 320px. Max-height: 200px (snippet truncates). Position: anchored to the trigger via getBoundingClientRect; flips above when bottom < 200px.

### 2.4 Wire into chips and rows

- `CitationChip.tsx` — `useSourcePreviewTrigger(citation)` and spread on the `<a>`.
- `SourceRow.tsx` — same hook on the `<SourceFavicon>` span (not the whole row — clicking the row already opens the source).

Mount provider in `ChatScreen.tsx` next to `<CitationsProvider>`.

### 2.5 Tests

- Hover chip → card opens after 200ms; pointer-leave → closes after 100ms; pointer into card → cancels close.
- Focus chip via keyboard → opens immediately; Esc closes.
- Touch-only viewport (`(hover: none)`) → no card opens.
- Card content matches the source: title, snippet, freshness, connector pill.
- Only one card visible when hovering between two chips quickly.

---

## 3 · Out of scope / future

- OG-card thumbnails / preview images.
- "Save to draft" affordance from the card.
- Quoting a snippet directly from the card into the composer.
