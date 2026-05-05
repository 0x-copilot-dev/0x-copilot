# PR 3.1 — Citation chips polish + Sources tab integration

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 3, PR 3.1 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** frontend (chips + tab body + auto-open) · ai-backend (one read endpoint, optional this PR) · api-types (one optional field)
> **Size:** **M.** Almost entirely FE. PR 1.1 already shipped the wire, ledger, registry, chip, plugin, and `SourcesPanel` overlay. This PR is the **polish + right-rail integration** pass.
> **Depends on:** PR 1.1 (citations live registry — implemented), PR 3.2 (workspace pane host — ships in same wave; this PR provides the SourcesTab body that PR 3.2 mounts)
> **Reads alongside:** [`01-citations-live-registry.md`](01-citations-live-registry.md), [`02-citations-followups.md`](02-citations-followups.md), [`pr-3.2-workspace-pane-right-rail.md`](pr-3.2-workspace-pane-right-rail.md), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md)
> **Sibling docs (Wave 3):** PR 3.2 — workspace pane right rail · PR 3.3 — MCP discovery + approval forwarding polish · PR 3.4 — connector popover

---

## 0 · TL;DR

PR 1.1 shipped:

- the `source_ingested` event, the `runtime_citations` table, the `CitationLedger`, the universal `cite()` seam,
- the FE `CitationsProvider`, `CitationChip`, `citationsRegistry`, `applyCitationEvent` reducer branch,
- the Streamdown `citationRemarkPlugin` rewriting `[c<id>]` → `[<token>](#cite:<id>)` and routing through `MarkdownLink`,
- a working `SourcesPanel` mounted via the slash-command `DetailsPanelHost` overlay.

This PR does four small things:

1. **Promotes `SourcesPanel` to a tab body.** The same component that today renders inside the `details-panel` overlay also renders inside the right-rail tab host (PR 3.2). Zero new data, zero new fetch, zero new state.
2. **Adds the auto-open handshake.** When `source_ingested` lands and the workspace pane is closed, fire one signal that PR 3.2 owns: "open me on the Sources tab." Reuses the citations registry as the trigger; no new event, no new sub-state.
3. **Polishes chips per Atlas tokens.** Superscript number, hover tooltip, connector glyph, accent color on hover, dim by default. Adds the **post-prose Sources strip** ("[1] [2] [3]" chip row beneath an assistant message) the design doc calls for.
4. **Adds an archive read.** `GET /v1/agent/conversations/{id}/sources?run_id?` so a thread loaded from history (no live registry) populates the same tab the same way. The runtime already persists `runtime_citations` with org-RLS; this is the smallest read endpoint over an existing table.

LoC estimate: FE ≈ 220 (chips CSS, sources strip component, auto-open hook, archive merge into registry) · ai-backend ≈ 80 (read endpoint + projector reuse) · api-types ≈ 6.

---

## 1 · PRD

### 1.1 Problem

Citations exist on the wire and resolve into `<CitationChip />` inline. But:

- The Sources view today is a **slash-command overlay** (`/sources`) — a transient panel a user has to summon. The design doc requires Sources to live as a **tab on the right rail** alongside Agents / Draft / Approvals / Skills, and the rail to **auto-open** the first time the agent reads a source ("open when there are sources/agents" — confirmed user decision).
- Chip styling today is minimal (`<sup>` with default link color). The design specifies a superscript chip with a connector glyph slot, dim default, accent on hover, tooltip showing connector + title.
- A thread loaded from history rebuilds the registry from replayed `source_ingested` events. That works for chats from this run, but the registry lookup is **per-conversation only via the live ledger**. There is no archive read, so opening a 2-day-old chat re-replays every event end-to-end before anything renders. We can avoid the cold-start by reading the persisted projection.
- The "post-prose Sources strip" — the row of chip buttons under each assistant message that the prototype renders ("Sources [1] [2] [3]") — is not implemented. Today only the inline `<sup>` chips appear.

### 1.2 Goals

1. **One Sources component, two mount sites.** The body shipped by PR 1.1 (`SourcesPanel`) becomes a content body the right-rail tab embeds and the slash-command overlay continues to host. No duplicate data path.
2. **Auto-open the workspace pane** on first ingest of a citation **per conversation per visit**. Re-runs in the same conversation don't re-pop the pane. Closing it manually persists for the rest of the session.
3. **Chip + sources strip styling** matches the Atlas design doc (`--accent #d97757`; superscript; connector glyph slot; hover tooltip). The post-prose Sources strip is a new tiny component, not a new event or wire field.
4. **Archive parity.** Loading any historical thread populates the Sources tab without requiring SSE replay of every event. A single `GET /…/sources?conversation_id` round-trip seeds the registry.
5. **Zero new event types.** Auto-open and post-prose Sources strip both read off the existing citations registry. No protocol change.

### 1.3 Non-goals

- **No re-implementation of the citation chip.** PR 1.1's `CitationChip` stays.
- **No per-citation analytics** (click counts, source clicks). Future PR.
- **No edit/annotate citations.** The chip remains a read-only projection of the source.
- **No "show me what was searched" trust UI** (design's future explorations) — not in this PR.
- **No multimodal sources** (Figma frame, Excel cell, Loom timestamp) — design doc explicitly defers.
- **No sharing-restricted view** (`Source restricted` tooltip) — that lands with W6 sharing schema.
- **No promotion of `CitationChip` into design-system.** Per [`packages/design-system/CLAUDE.md`](../../packages/design-system/CLAUDE.md) — feature workflow stays in `apps/frontend`.
- **No Streamdown plugin churn.** `citationRemarkPlugin` already rewrites `[c<id>]` correctly.

### 1.4 Success criteria

- ✅ Workspace pane (PR 3.2) auto-opens on the **first** `source_ingested` of a conversation visit.
- ✅ The Sources tab body inside the pane is the same React tree (`SourcesPanel`) the slash-command overlay uses.
- ✅ A chat opened from history populates Sources in ≤1 round-trip after `getConversation` resolves; chips render before the user scrolls past the first assistant message.
- ✅ Chip styling matches design tokens — superscript, dim default, `--accent` on hover; tooltip shows `${title} — ${connector}`; click jumps to the corresponding row in the Sources tab.
- ✅ Post-prose Sources strip renders one chip per cited source under each assistant message; clicking opens the same Sources tab and scrolls to the row.
- ✅ Replay determinism: a completed run replayed via `replayRunEvents` produces an identical Sources tab. Existing `01-citations-live-registry.md` test cases are extended to cover the archive read path.
- ✅ No regression on PR 1.1 chip semantics — unresolved id still renders `?` placeholder, surrounding markdown still uses Streamdown.

### 1.5 User stories

| #    | Persona                        | Story                                                                                                                                                                  |
| ---- | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | Sarah (in a fresh chat)        | I ask "summarize last week in #launch-aurora." The right rail slides open the moment Atlas reads the first thread; rows appear as docs are read.                       |
| US-2 | Sarah (mid-prose)              | The assistant streams "the embargo lifts on Apr 21 [3]". The chip is dim; on hover it accents and shows "FY26 Q1 GTM plan — drive". I click; the rail focuses the row. |
| US-3 | Sarah (after the answer)       | Beneath the assistant message I see a "Sources" row with 6 chip buttons. I click "[2]"; the rail scrolls to row 2.                                                     |
| US-4 | Sarah (re-opens chat tomorrow) | I open yesterday's thread. Sources tab is populated immediately, in the same order, with the same titles.                                                              |
| US-5 | Sarah (closes the rail)        | I close the rail mid-thread. The next `source_ingested` in this thread does **not** reopen it — my close was intentional.                                              |
| US-6 | Engineer adding a tool         | I call `await CitationLedger.cite(source)` once per doc. Chip + strip + tab row + auto-open all light up with no extra wiring.                                         |

---

## 2 · Spec

### 2.1 Wire — what is and isn't new

| Surface                                                         | Touched?                                                                                                                                                                                 |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `source_ingested` event payload                                 | **No.** Already includes `citation_id`, `source_connector`, `source_doc_id`, `source_url`, `title`, `snippet`, `freshness_at`, `source_tool_call_id`.                                    |
| `RuntimeFinalResponsePayload.citations`                         | **No.** Already exists; sealed by worker on completion.                                                                                                                                  |
| `runtime_citations` table                                       | **No.**                                                                                                                                                                                  |
| Chip protocol (`#cite:<id>` → `<CitationChip />`)               | **No.**                                                                                                                                                                                  |
| New endpoint `GET /v1/agent/conversations/{id}/sources?run_id?` | **Yes** — additive, read-only, returns the same `CitationSourceRef[]` the live registry produces.                                                                                        |
| `Conversation` projection                                       | **Optional add:** `cited_source_count?: number` (cheap denormalization in the GET conversations list so the sidebar can show a "📎 6" affordance later). Defer if undesired; not gating. |

### 2.2 Endpoint — `GET /v1/agent/conversations/{id}/sources`

Smallest possible projection over `runtime_citations`:

```http
GET /v1/agent/conversations/conv_01HM…/sources?after_ordinal=0&limit=200
```

Response:

```jsonc
{
  "conversation_id": "conv_01HM…",
  "sources": [
    {
      "citation_id": "c5n",
      "ordinal": 1,
      "run_id": "run_01HMP…",
      "source_connector": "notion",
      "source_doc_id": "page_01HMA…",
      "source_url": "https://notion.so/…",
      "title": "Aurora 4.0 — Approved Positioning v3",
      "snippet": "Aurora 4.0 brings agentic search to every desk…",
      "freshness_at": "2026-04-29T16:40:00Z",
      "source_tool_call_id": "tc_01HMP…",
    },
    // …
  ],
  "next_after_ordinal": 6,
}
```

Server-side:

- Uses the **existing** `CitationStorePort.list_for_conversation(org_id, conversation_id, after_ordinal, limit)`. PR 1.1 ships the in-memory adapter; the Postgres adapter is a follow-up that this PR can land in the same diff (≈40 lines: `SELECT … WHERE org_id = $1 AND conversation_id = $2 AND ordinal > $3 ORDER BY ordinal LIMIT $4`). RLS already enforces org isolation.
- The route is **read-only**, idempotent, cacheable. No new audit row.
- The route lives in `services/ai-backend/src/runtime_api/http/routes.py` (sibling to existing conversation routes) and proxies through `services/backend-facade` exactly like `getConversation`.

### 2.3 FE — components added, components reused

| Component                             | Source                                                                                        | Notes                                                                                                                                                                                                    |
| ------------------------------------- | --------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CitationChip`                        | _existing_ `apps/frontend/src/features/chat/components/citations/CitationChip.tsx`            | **No JSX change.** CSS upgrade only — adds `data-connector` attr-driven hover accent + connector glyph slot via `::before` background.                                                                   |
| `SourcesPanel`                        | _existing_ `apps/frontend/src/features/chat/components/details/SourcesPanel.tsx`              | **Made portable** — exports a presentational variant that takes `{citations, onSelect?, dense?}` and a host shell. Tab embeds presentational variant; overlay keeps the shell.                           |
| `SourcesTab` (new)                    | `apps/frontend/src/features/chat/components/workspace/SourcesTab.tsx`                         | Thin wrapper: subscribes to `useCitations(conversationId)`; falls back to `useArchivedSources(conversationId)` when no live registry. PR 3.2 host mounts this.                                           |
| `useArchivedSources` (new hook)       | `apps/frontend/src/features/connectors/citations/useArchivedSources.ts` _(or `…/citations/`)_ | One `useEffect`; loads via `getConversationSources(id, identity)`; **merges** into the existing `CitationsProvider` registry (so chips resolve identically).                                             |
| `useWorkspacePaneAutoOpen` (new hook) | `apps/frontend/src/features/chat/components/workspace/useWorkspacePaneAutoOpen.ts`            | Subscribes to citations registry; emits "open on Sources" exactly once per conversation visit; PR 3.2 owns the actual pane state.                                                                        |
| `MessageSourcesStrip` (new)           | `apps/frontend/src/features/chat/components/messages/MessageSourcesStrip.tsx`                 | Renders one chip-button per cited source under an assistant message. Reads citations referenced by **that specific run/message** (via `RuntimeFinalResponsePayload.citations` already sealed in PR 1.1). |
| `getConversationSources` (api client) | `apps/frontend/src/api/agentApi.ts` _(extension)_                                             | One `fetch` wrapping the new endpoint. Lives next to `getConversation`.                                                                                                                                  |

### 2.4 FE — registry merge semantics

The citations registry today is keyed by `run_id` per `CitationRegistryByRun`. We extend the lookup with a **conversation-scoped fallback**:

```ts
// citationsContext.tsx — additive: same shape, two layers.
export interface CitationLookup {
  byRun: CitationRegistryByRun; // existing (live)
  byConversation: Record<string, CitationSourceRef[]>; // new (archive seeds + sealed runs)
  resolve: (citationId: string) => CitationSourceRef | undefined;
}
```

`resolve()` checks `byRun` first (live), then falls back to `byConversation`. Replays already populate `byRun` deterministically; the archive read populates `byConversation`. Both surface identical data — the only reason to keep them separate is so re-running an existing run without contradiction.

### 2.5 Auto-open handshake — single signal

PR 3.2 owns the workspace-pane open/closed state. PR 3.1 only emits a hint:

```ts
// Inside useWorkspacePaneAutoOpen — pseudocode
useEffect(() => {
  if (!conversationId) return;
  if (closedManuallyForConversation.current.has(conversationId)) return;
  if (firstIngestSeenForConversation.current.has(conversationId)) return;
  if (citationsCount === 0) return;
  firstIngestSeenForConversation.current.add(conversationId);
  pane.openOn("sources"); // PR 3.2 hook
}, [citationsCount, conversationId]);
```

Rules:

- **Once per conversation visit.** Switching to a different chat resets nothing — the next chat has its own first-ingest tracker.
- **Honours manual close.** If the user closes the pane after auto-open, we record `closedManuallyForConversation.add(conversationId)` and never auto-pop again until reload.
- **Same trigger for agents** (PR 1.5 / 3.2). PR 3.2's `openOn` accepts `"sources" | "agents" | …`; first non-empty registry wins.

### 2.6 Streaming impact — explicitly **none**

| Subsystem                            | Touched?                                                                      |
| ------------------------------------ | ----------------------------------------------------------------------------- |
| `runtime_events` schema              | **No.** No new event type.                                                    |
| `RuntimeEventEnvelope` Pydantic / TS | **No.**                                                                       |
| SSE handshake (`?after_sequence=N`)  | **No.** Reconnect identical.                                                  |
| `runtime_worker` job loop            | **No.**                                                                       |
| `chatModel/eventReducer.ts`          | **No.** PR 1.1 already routes `source_ingested` through `applyCitationEvent`. |
| Capabilities middleware              | **No.**                                                                       |
| Audit chain                          | **No.**                                                                       |

The only protocol delta is the **new GET endpoint**. It is read-only, idempotent, RLS-bound to the caller's `org_id`. No event, no projection change.

### 2.7 Permissions

| Caller                                              | Sources tab + chips                                                                                                                                                                                                                                                                              |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Conversation owner                                  | Full read.                                                                                                                                                                                                                                                                                       |
| Workspace member viewing a shared conversation (W6) | Read **filtered** server-side: rows the recipient cannot see render with `snippet=null`, `title="Source restricted"`. The chip itself stays in the prose so the answer's structure isn't deformed. (This is forward-compatible with PR 6.1 — server already has the connector identity per row.) |
| Workspace admin                                     | Full read.                                                                                                                                                                                                                                                                                       |
| Service-to-service call from `backend-facade`       | Allowed via `x-enterprise-service-token` + identity headers.                                                                                                                                                                                                                                     |

### 2.8 Error semantics

| Condition                                                  | UI behavior                                                                                                                                                         |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Archive read fails (5xx / network)                         | Tab renders the live registry contents (may be empty); shows a `data-stale` banner with retry; chip resolution falls back to the unresolved-`?` PR 1.1 placeholder. |
| Auto-open arrives while chat is mid-render (initial paint) | Hook waits for `initialHistoryLoaded` (already a `ChatScreen` boolean) before evaluating count. No flash.                                                           |
| Citation referenced by chip is later removed               | Cannot happen — citations are immutable for the run lifetime (PR 1.1 §2.1). The chip continues to resolve.                                                          |
| Run is canceled mid-stream                                 | Citations ingested before cancel remain visible; the registry doesn't get cleared.                                                                                  |
| Chat switched mid-archive-load                             | The hook is keyed by `conversationId`; the in-flight load is ignored on resolve.                                                                                    |
| Restricted source in shared view (W6 forward-look)         | Snippet replaced by "Source restricted" tooltip; chip remains clickable but the side row only shows title-only.                                                     |

### 2.9 Accessibility

- `<CitationChip>` becomes `<sup role="link">` semantically; keyboard focusable; **Enter** activates the same `onClick` (today). `aria-label` reads "Citation N — {title}, {connector}".
- The hover tooltip becomes a `data-tooltip` reusing the existing pattern in `apps/frontend/src/styles.css` so it works for keyboard users too (CSS focus-within selector covers both).
- `MessageSourcesStrip` is a `<ul role="list">` of focusable buttons; arrow-key navigation is left to default browser focus order (single line, low cardinality — no roving tabindex needed).
- The Sources tab body announces row count via `aria-live="polite"` when it grows during a stream.

### 2.10 What we explicitly do NOT add

- **No third-party citation library.** Surveyed: `react-citations`, `citeproc-js`, MathJax — none are remotely close to the use case (academic citation formatting). PR 1.1's plugin already does the only transformation we need.
- **No `@radix-ui/react-tooltip`.** The `data-tooltip` CSS pattern is in place, ~10 LOC, accessible.
- **No new design-system primitive.** Sources strip + chip + tab body are feature components.
- **No client-side caching layer for archive sources.** The endpoint is small, the registry is in-memory, and revisits within a session use the seeded registry.

---

## 3 · Architecture

### 3.1 Where the pieces live

```
                                ┌────────────────────────────────────────────────────┐
                                │  ai-backend                                        │
                                │  ─ runtime_citations table (PR 1.1)                │
                                │  ─ CitationLedger / cite() / source_ingested event │
                                │  ─ NEW: GET /v1/agent/conversations/{id}/sources   │
                                │         routes.py → CitationStorePort.list_for_…   │
                                └────────────────────────────┬───────────────────────┘
                                                             │ HTTP
                                                ┌────────────▼────────────┐
                                                │  backend-facade          │
                                                │  proxy passthrough       │
                                                └────────────┬─────────────┘
                                                             │
            ┌────────────────────────────────────────────────▼────────────────────────────────────────────────┐
            │                           apps/frontend                                                          │
            │  ChatScreen.tsx (existing)                                                                       │
            │   ├─ CitationsProvider  (PR 1.1 — extend with byConversation layer)                              │
            │   ├─ useArchivedSources(conversationId)  (NEW — single GET on conv switch)                       │
            │   ├─ useWorkspacePaneAutoOpen(citations, conversationId)  (NEW — emits openOn("sources"))         │
            │   └─ <ThreadBody …>                                                                              │
            │       ├─ <AssistantMessage> + <MarkdownText>  → <MarkdownLink href="#cite:..."> → <CitationChip /> │
            │       └─ <MessageSourcesStrip /> beneath each assistant message                                  │
            │                                                                                                  │
            │  PR 3.2 mounts <SourcesTab /> via <WorkspacePane />                                              │
            └──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Sequence — fresh chat, first ingest

```
Sarah                                    FE                                       Worker / ledger
 │                                        │                                          │
 │  prompt sent                           │                                          │
 │ ──────────────────────────────────────►│ POST /v1/agent/runs                       │
 │                                        │ ───────────────────────────────────────► │ run starts
 │                                        │                                          │ tool fires; CitationLedger.cite(src)
 │                                        │  source_ingested SSE event               │ ◄──── runtime_citations INSERT
 │                                        │ ◄─────────────────────────────────────── │      event emitted
 │                                        │                                          │
 │                                        │  applyCitationEvent → citationsRegistry  │
 │                                        │  citationsCount: 0 → 1                   │
 │                                        │  useWorkspacePaneAutoOpen → openOn("sources")
 │                                        │  PR 3.2 pane opens, SourcesTab renders   │
 │                                        │                                          │
 │                                        │  model_delta "the embargo [c1] lifts…"   │
 │                                        │ ◄─────────────────────────────────────── │
 │                                        │  Streamdown remark plugin rewrites token │
 │                                        │  → MarkdownLink → CitationChip resolves  │
 │                                        │    against the registry → renders 1      │
 │                                        │                                          │
 │  hovers chip                                                                       │
 │  → tooltip: "FY26 Q1 GTM plan — drive"                                             │
 │  → click → onSelect → focuses the SourcesTab row                                   │
 │                                                                                    │
 │  final_response → MessageSourcesStrip   reads payload.citations, renders chips     │
 │                                                                                    │
```

### 3.3 Sequence — historical chat load

```
Sarah opens yesterday's chat                                                              ai-backend
 │                                                                                          │
 │  loadConversationById(id)                                                                │
 │ ────►   GET /v1/agent/conversations/{id}    + listMessages    + replayRunEvents (existing)
 │                                                                                          │
 │                                                                                          │
 │       NEW parallel call:                                                                 │
 │       getConversationSources(id, identity)  ──────────────────────────────────────►      │
 │                                                                                          │   list_for_conversation(org, id)
 │                                                                                          │   SELECT … FROM runtime_citations
 │       ◄──────────────────────────────  CitationSourceRef[]                                │   ORDER BY ordinal
 │                                                                                          │
 │       seed CitationsProvider.byConversation                                              │
 │       SourcesTab renders rows immediately                                                │
 │       MarkdownLink → CitationChip resolves through byConversation fallback                │
 │                                                                                          │
 │       (replay completes; byRun fills; resolve order: byRun → byConversation)             │
```

### 3.4 DRY — what's reused vs. what's added

| Concern                      | Reuse                                                                 | Add                                                           |
| ---------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------- |
| Wire types                   | `CitationSourceRef`, `RuntimeFinalResponsePayload.citations` (PR 1.1) | one optional list-response wrapper (~6 LOC api-types)         |
| Event reducer                | `applyCitationEvent` (PR 1.1)                                         | —                                                             |
| Streamdown plugin            | `citationRemarkPlugin` (PR 1.1)                                       | —                                                             |
| Chip component               | `CitationChip` (PR 1.1)                                               | CSS only (~30 LOC)                                            |
| Sources content              | `SourcesPanel` (PR 1.1)                                               | extract presentational `<SourcesList />` body (~20 LOC delta) |
| Conversation load round-trip | `getConversation`, `listMessages`, `replayRunEvents` (existing)       | one parallel `getConversationSources` call (~10 LOC)          |
| Registry                     | `citationsRegistry`, `CitationsProvider` (PR 1.1)                     | a second layer key (`byConversation`) with merge resolution   |
| Pane open/close              | PR 3.2 owns it                                                        | one hook firing `openOn("sources")` once per visit            |
| Backend store port + RLS     | `CitationStorePort` + `runtime_citations` RLS policy (PR 1.1)         | one `list_for_conversation` Postgres adapter method (~40 LOC) |
| Projection / activity_kind   | PR 1.1 already projects `source_ingested → activity_kind=tool`        | —                                                             |

Net new code: **FE ≈ 220, ai-backend ≈ 80, api-types ≈ 6**.

### 3.5 Dependency survey — nothing added

- **`@radix-ui/react-tooltip`** — beautiful, but `data-tooltip` CSS pattern handles our case in ~10 LOC. Not added.
- **`citeproc-js`, `react-citations`, `react-bibtex`** — wrong domain (academic). Not added.
- **`@floating-ui/react`** — not needed; the chip tooltip is anchored, the Sources row is inline.
- **No new state library.** React context + a `Map`-backed registry already match the model.

### 3.6 Edge cases

| Case                                                                                                | Behavior                                                                                                                                                                                  |
| --------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Archive read returns rows that aren't yet in the live registry (run still streaming on this client) | Merge: live `byRun` overrides `byConversation` for the same `citation_id`. Chips resolve consistently.                                                                                    |
| Two consecutive runs both cite the same source                                                      | The ledger upserts (PR 1.1 §2.1); both runs reference the same `citation_id`. Sources tab shows one row.                                                                                  |
| Chip is rendered before its citation is ingested (race during stream)                               | Token tokenizer reserves the chip; until `source_ingested` arrives, `useCitation(id)` returns `undefined` → chip renders the `?` placeholder; on ingest it re-renders. (PR 1.1 behavior.) |
| User scrolls mid-stream and `MessageSourcesStrip` re-mounts                                         | Reads from `final_response.citations` (sealed) once available; otherwise from running registry intersection with that message's chip ids.                                                 |
| Source URL is `null`                                                                                | Chip is still focusable but `target="_blank"` is omitted; tab row title is plain text not a link.                                                                                         |
| Long title in tab row                                                                               | CSS `text-overflow: ellipsis`; full title in `title=` attr.                                                                                                                               |
| Pane was closed manually, then a new conversation is opened                                         | Tracker is per-conversation; auto-open evaluates afresh on the new chat.                                                                                                                  |
| Pane was closed manually, then user reloads tab                                                     | Tracker is in-memory only — first-ingest auto-open fires after reload. (Acceptable per design's "open when there are sources" rule.)                                                      |
| Chip clicked while pane is closed                                                                   | onSelect calls `pane.openOn("sources", { focusCitationId })`; pane opens and scrolls to the row.                                                                                          |
| Tooltip on touch devices                                                                            | Tap on chip behaves like click → opens pane / scrolls to row. No hover preview.                                                                                                           |

### 3.7 Test plan

**Frontend**

- `CitationChip.css.test.tsx` — visual contract: dim default, `--accent` on hover, connector glyph slot, focus ring.
- `MessageSourcesStrip.test.tsx` — renders one button per citation in `final_response.citations`; click invokes `pane.openOn("sources", { focusCitationId })`; empty list renders nothing.
- `SourcesTab.test.tsx` — renders live registry; falls back to archive read on conversation load; merges live deltas into archive seed; row click focuses citation.
- `useArchivedSources.test.ts` — happy path; switch conversations cancels in-flight request; 5xx surfaces stale flag.
- `useWorkspacePaneAutoOpen.test.ts` — fires once per conversation; honours manual close; reset on conversation switch.

**ai-backend**

- `tests/unit/runtime_api/test_sources_endpoint.py` — happy path; pagination via `after_ordinal`; org isolation (RLS); 404 on unknown conversation; respects `run_id` filter when present.
- `tests/integration/runtime_api/test_sources_replay_parity.py` — running flow + archive read produce the same set; ordering matches.

**Cross-service smoke**

- `make test` — extend the `01-citations-live-registry` use-case test to also assert the GET endpoint after run completion.

### 3.8 Rollout

- **Flag-free.** Endpoint additive; FE registry layer additive; chip CSS purely visual.
- **Backout.** Revert PR. PR 1.1's behavior persists exactly. Slash-command overlay continues to work.
- **Migration order.** Not migration-bound. Postgres adapter for `CitationStorePort.list_for_conversation` is the only persistence delta — same migration as PR 1.1's table.

### 3.9 Open questions

1. **Server-side filter for shared-view restriction** — schema already has `source_connector` per row, so we can filter at SQL time once W6 ships. Decide there whether the restricted view returns a rewritten `title` or a partial row + flag.
2. **Sidebar count badge.** The `cited_source_count` projection in §2.1 is optional; landing it now removes a future round-trip but adds a dimension to the conversation list query. Defer if undesired.
3. **Order of merge** when live and archive disagree on snippet. Today: `byRun` wins. Acceptable because live always reflects the latest tool emission.

---

## 4 · Acceptance checklist

- [ ] `services/ai-backend/src/runtime_api/http/routes.py` exposes `GET /v1/agent/conversations/{id}/sources` returning the existing `CitationStorePort.list_for_conversation` projection.
- [ ] `services/ai-backend/src/runtime_adapters/postgres/citation_store.py` implements `list_for_conversation` (lands here if not already in PR 1.1's follow-up).
- [ ] `services/backend-facade` proxies the new route with identity headers.
- [ ] `packages/api-types/src/index.ts` exports `ConversationSourcesResponse` (and the optional `cited_source_count` projection if accepted).
- [ ] `apps/frontend/src/api/agentApi.ts` adds `getConversationSources`.
- [ ] `apps/frontend/src/features/connectors/citations/useArchivedSources.ts` (or `apps/frontend/src/features/chat/components/citations/`) ships and is tested.
- [ ] `apps/frontend/src/features/chat/components/citations/citationsContext.tsx` extends `CitationLookup` with a `byConversation` layer; `resolve()` falls back through it.
- [ ] `apps/frontend/src/features/chat/components/messages/MessageSourcesStrip.tsx` ships under each `<AssistantMessage>` and reads from sealed `final_response.citations`.
- [ ] `apps/frontend/src/features/chat/components/workspace/SourcesTab.tsx` ships and is consumed by PR 3.2's `<WorkspacePane />` host.
- [ ] `apps/frontend/src/features/chat/components/workspace/useWorkspacePaneAutoOpen.ts` ships; emits `openOn("sources")` exactly once per conversation visit; honours manual close.
- [ ] Chip CSS in `apps/frontend/src/styles.css` aligns with `--accent #d97757`, hover-accent, connector glyph slot via `data-connector` attribute.
- [ ] No new `RuntimeApiEventType`. Pydantic schemas in `services/ai-backend/src/runtime_api/schemas/events.py` are unchanged.
- [ ] No regression on PR 1.1 tests. New tests added for endpoint + archive merge + auto-open hook.
- [ ] `npm run typecheck --workspace @enterprise-search/frontend` and `npm run build --workspace @enterprise-search/frontend` pass.
- [ ] `services/ai-backend` full test suite passes; manifest matches lock; rollback round-trip green.
- [ ] `make test` green.

---

## 5 · References

- [`docs/new-design/01-citations-live-registry.md`](01-citations-live-registry.md) — wire + registry + plugin (PR 1.1).
- [`docs/new-design/02-citations-followups.md`](02-citations-followups.md) — open follow-ups for citations (PR 1.1.x).
- [`docs/new-design/pr-3.2-workspace-pane-right-rail.md`](pr-3.2-workspace-pane-right-rail.md) — owns the pane open/closed state and the tab host.
- [`apps/frontend/src/features/chat/components/citations/CitationChip.tsx`](../../apps/frontend/src/features/chat/components/citations/CitationChip.tsx) — chip rendered by markdown plugin.
- [`apps/frontend/src/features/chat/components/details/SourcesPanel.tsx`](../../apps/frontend/src/features/chat/components/details/SourcesPanel.tsx) — body re-mounted as a tab.
- [`services/ai-backend/src/agent_runtime/persistence/records/citations.py`](../../services/ai-backend/src/agent_runtime/persistence/records/citations.py) — record contract.
- [`services/ai-backend/src/agent_runtime/capabilities/citations.py`](../../services/ai-backend/src/agent_runtime/capabilities/citations.py) — `cite()` seam.
- [`services/ai-backend/migrations/0015_runtime_citations.sql`](../../services/ai-backend/migrations/0015_runtime_citations.sql) — table + RLS.
- [Streamdown remark plugin contract](https://github.com/remarkjs/remark) — used unchanged.
- Atlas Design Doc — §"Citations are first-class", §"Workspace pane (right rail)", §"Sources strip after assistant message".
