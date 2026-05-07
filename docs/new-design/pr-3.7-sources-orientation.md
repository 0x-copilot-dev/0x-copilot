# PR 3.7 — Sources orientation polish (favicons + connector glyphs)

> **Status:** Draft
> **Owner:** frontend (rows + glyph map) · design-system (one new BRAND_GLYPH entry)
> **Size:** XS. Pure presentational; no wire changes, no new state.
> **Depends on:** PR 3.1 (SourceRow), PR 3.2 (Workspace pane), PR 1.1 follow-up D (`CitationCapturingRegistry` — populates rows from web_search).
> **Reads alongside:** [`apps/frontend/src/features/chat/components/citations/SourceRow.tsx`](../../apps/frontend/src/features/chat/components/citations/SourceRow.tsx), [`packages/design-system/src/index.tsx`](../../packages/design-system/src/index.tsx) (`AppIcon`, `BRAND_GLYPHS`).

---

## 0 · TL;DR

Today the Sources tab populates a flat list of rows, each with a one-letter connector chip (`AppIcon`). That worked when sources were 1-3 MCP-branded entries. Now that local-tool web search emits 3-10 web sources per run, `web_search` renders as a generic "W" letter chip and every web URL looks identical. This PR:

1. Adds a `web` brand glyph (globe) so `web_search` and any future web-search tool render a recognisable mark instead of a letter.
2. Replaces the static `AppIcon` slot in `SourceRow` with `<SourceFavicon>` — uses the host's favicon (Google s2 service) for any source that has a real `source_url`, falls back to `AppIcon` (brand glyph or letter) on load error or when no URL is present.
3. Humanises the connector pill in the row footer (`web_search` → `Web search`, `notion` → `Notion`).

LoC: FE ≈ 80, DS ≈ 4.

---

## 1 · PRD

### 1.1 Problem

- After PR 1.1 follow-up D, a typical `web_search` run dumps 3-10 rows into the Sources tab. They are visually indistinguishable: same accent badge, same letter chip "W", same gray pill "web_search".
- The user has to read every title to find the source they want. There's no glanceable visual handle.
- For MCP sources (Notion, Drive), the brand glyph already works because `BRAND_GLYPHS` covers them. For web sources, the favicon IS the brand handle the user already recognises (the favicon is the sole UX affordance every web result has had since browsers existed).

### 1.2 Goals

1. Web sources render their host favicon. Multiple results from the same domain look related at a glance.
2. `web_search` (and bare `web`) renders a globe glyph in the brand map so anything without a usable favicon still has a recognisable mark.
3. The connector pill in the row footer uses human casing.
4. No wire changes. No new event types. No new state.

### 1.3 Non-goals

- No favicon caching layer / pre-warming. Browser caches Google s2 responses; that's enough.
- No "trust" decoration on web sources (verified domain, etc.).
- No favicon fallback chain beyond {favicon → brand glyph → letter}. Three levels is plenty.
- No changes to `CitationChip` (the inline chip stays a numbered pill — favicons there would compete with the number).

### 1.4 Success criteria

- `web_search`-derived rows show host favicons; same-domain results look grouped.
- A row whose `source_url` returns a 404 favicon falls back to the globe glyph silently.
- A row from `notion` MCP still shows the Notion brand glyph.
- A11y label still reads "Open citation N — Title from {connector}".

---

## 2 · Spec

### 2.1 Design-system change

Add to `BRAND_GLYPHS` in [`packages/design-system/src/index.tsx`](../../packages/design-system/src/index.tsx):

```ts
web: { label: "Web", bg: "#0f172a", fg: "#facc15", symbol: "🌐" },
web_search: { label: "Web search", bg: "#0f172a", fg: "#facc15", symbol: "🌐" },
```

Two entries (not one with an alias) to keep the map a flat lookup. No code change to `AppIcon`.

### 2.2 New `<SourceFavicon>` component

Lives in `apps/frontend/src/features/chat/components/citations/SourceFavicon.tsx`. Props:

```ts
{ source: SourceEntry; size?: "sm" | "lg"; className?: string }
```

Behavior:

- If `source.source_url` parses to a host: render `<img src="https://www.google.com/s2/favicons?domain={host}&sz=64" />` inside a span styled like `AppIcon`.
- On `<img>` `error` event: swap to `<AppIcon name={source.source_connector} />`.
- If no usable URL: render `<AppIcon name={source.source_connector} />` directly.
- Square 16px (sm) / 24px (lg). `aria-hidden="true"` on the img — the row's a11y label already names the source.

### 2.3 Humanise the connector pill

New helper in same folder: `connectorLabel.ts` — single function `humanizeConnector(slug: string): string`. Rules: replace `_` and `-` with spaces, title-case the first word only ("web search", "notion", "google drive"). Memoised.

Used in [SourceRow.tsx:75](../../apps/frontend/src/features/chat/components/citations/SourceRow.tsx#L75): `<Badge tone="neutral">{humanizeConnector(source.source_connector)}</Badge>`.

### 2.4 Tests

- `SourceFavicon` renders favicon `img` for URL-bearing source, falls back to `AppIcon` on error.
- `SourceFavicon` renders `AppIcon` directly when no URL.
- `humanizeConnector` snapshot for `web_search`, `notion`, `google_drive`, `pagerduty-incidents`.

---

## 3 · Out of scope / future

- Favicon caching service (rare; Google s2 already caches).
- Connector glyph customisation per workspace.
- Hover preview (covered by PR 3.7.2).
