# PR 3.7.1 — Live source progress skeleton

> **Status:** Draft
> **Owner:** frontend
> **Size:** XS–S. One new hook, one skeleton row, no wire changes.
> **Depends on:** PR 3.7 (favicons + glyphs) for visual continuity.
> **Reads alongside:** [`apps/frontend/src/features/chat/components/workspace/SourcesTab.tsx`](../../apps/frontend/src/features/chat/components/workspace/SourcesTab.tsx), [`apps/frontend/src/features/chat/chatRunState.ts`](../../apps/frontend/src/features/chat/chatRunState.ts).

---

## 0 · TL;DR

Today the Sources tab shows the empty-state copy "Sources will appear here as Atlas finds them" for the entire duration of a search — typically 3–8 seconds during which the user has no signal that something is in flight. This PR replaces the empty state with a single "Looking for sources…" shimmer row whenever a source-producing tool call is in flight, then fades to real source rows as they arrive. One row max — multi-search runs aggregate into a single line so we never crowd the panel.

LoC: FE ≈ 90 (one hook + one skeleton component + a few CSS rules).

---

## 1 · PRD

### 1.1 Problem

- The user clicks send. The model thinks → calls `web_search` → DuckDuckGo / Tavily / MCP-search runs → results return → projector registers sources → SSE fires → SourcesTab updates.
- Total elapsed time for the visible "Sources will appear here…" → "first row appears" transition is 3–8 seconds. During this window the only feedback the user has is the chat area's "Planning next step…" (different surface, easy to miss).
- The Sources tab silence reads as "nothing's happening here" — which isn't true. The runtime knows tools are in flight; we just don't surface it.

### 1.2 Goals

1. While ≥1 tool call is in flight that _might_ produce sources, the Sources tab shows a single shimmer row labelled "Looking for sources…" (or "Searching the web…" / "Searching Notion…" when the tool name is recognisable).
2. When the first real `source_ingested` event arrives, the shimmer disappears and the real row takes its place. No layout flash.
3. When the in-flight tool count goes back to 0 with **no** sources produced, the empty state returns cleanly.
4. ONE shimmer row max, regardless of parallel tool calls — multi-search runs render as `Looking for sources from 3 tools…` rather than three separate rows.

### 1.3 Non-goals

- No per-tool progress bars, ETAs, or cancellation handles. The skeleton is a status indicator, not a control surface.
- No notification / toast on source arrival.
- No skeleton in the post-prose `MessageSourcesStrip` (that strip is sealed at run completion — pre-arrival shimmer would be misleading).
- No cross-tab status broadcasting (Agents tab already has its own status surface).

### 1.4 Success criteria

- Run a `web_search` query: skeleton appears within one paint of the model's tool call, fades when first source row arrives.
- Run a query that calls a tool but produces no citations (e.g. `read_file`): skeleton appears, then disappears with the empty state when the tool completes.
- Two parallel MCP searches: one shimmer row labelled "Searching from 2 tools…", not two.

---

## 2 · Spec

### 2.1 New hook: `useInFlightSourceTools(runId)`

Lives at `apps/frontend/src/features/chat/components/workspace/useInFlightSourceTools.ts`. Subscribes to the existing run-event stream the workspace pane already consumes. Reduces `tool_call_started` / `tool_call_completed` / `tool_call_failed` envelopes into:

```ts
interface InFlightSourceTools {
  count: number; // unique in-flight tool_call_ids
  primaryToolName: string | null; // most recent tool_call_started's tool name, for the label
}
```

Filters: only counts tools whose `tool_name` matches `/search|find|fetch|browse|query|lookup|call_mcp_tool/i` OR whose started event has been seen but corresponding completed not. Default: include unless we know it can't produce sources (e.g. `write_file`).

### 2.2 Label rule

```
count === 0 → null (render empty state instead)
count === 1 && tool name maps → "Searching the web…" / "Searching Notion…" / "Looking up GitHub…"
count === 1 && unmapped → "Looking for sources…"
count > 1   → "Looking for sources from N tools…"
```

A small `toolNameToHumanAction(toolName: string): string | null` helper maps known names; default null falls back to the generic copy.

### 2.3 Skeleton row component

New file `apps/frontend/src/features/chat/components/citations/SourceSkeletonRow.tsx`. Shape mirrors `SourceRow`: same Card, same height, same chip-position. The difference:

- The accent badge is a pulse instead of a number.
- The title is the dynamic label from §2.2.
- No snippet, no footer.
- `aria-live="polite"` so screen readers announce the label change.

CSS: existing `--shimmer` token if present, else add a 1.5s linear-gradient sweep.

### 2.4 SourcesTab integration

[SourcesTab.tsx:48-60](../../apps/frontend/src/features/chat/components/workspace/SourcesTab.tsx#L48-L60):

```tsx
if (ordered.length === 0) {
  if (inFlight.count > 0) {
    return <SourceSkeletonRow label={labelFor(inFlight)} />;
  }
  return /* existing empty-state */;
}
```

When `ordered.length > 0 && inFlight.count > 0` we still show the skeleton at the bottom of the list — the user's already getting feedback from real rows, but the shimmer signals "more coming." If you find this distracting in dogfooding, drop it; it's a one-liner toggle.

### 2.5 Tests

- `useInFlightSourceTools` increments on `tool_call_started`, decrements on `tool_call_completed` and `tool_call_failed`, ignores unrelated events.
- Filter excludes `write_file` / `update_memory` style names.
- Label rules: 0/1-mapped/1-unmapped/N variants.
- SourcesTab shows skeleton when sources empty + tools in flight; shows empty-state copy when both are zero; shows real rows when sources non-empty.

---

## 3 · Out of scope / future

- Skeleton in the chat area or composer (covered by `chatRunState`'s "Planning next step" indicator).
- Showing in-flight tool args in the skeleton title.
- Per-tool ETA from telemetry.
