# PR 1.1 — Citations: live registry + inline tokens

> **Status:** Implemented · v1 · Owner: TBD · Target wave: W1 (blocker for chat polish in W3)
> **Scope:** `services/ai-backend` (event + persistence + ledger) · `apps/frontend` (reducer + Streamdown plugin + chip + provider) · `packages/api-types` (wire contract)
> **Reads alongside:** [`docs/new-design/00-plan.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md), Atlas Design Doc (handoff bundle), `services/ai-backend/CLAUDE.md`, `apps/frontend/CLAUDE.md`.

## Implementation deviations (from spec → as built)

The architecture landed close to spec; the deltas worth flagging on review:

- **Migration is `0015_runtime_citations.sql`**, not `0014`. `0014_runtime_drafts` (PR 1.3) was already in flight on the same branch tip when this PR landed; bumping avoids the number collision.
- **`cite()` is a class method, not a top-level function**, per `services/ai-backend/CLAUDE.md` ("Keep production helper behavior **inside** classes. Avoid module-level helper functions"). Tools call `await CitationLedger.cite(source)`. The instance still owns idempotency, ordinal allocation, and emission; the class method just resolves the active ledger from a `ContextVar`.
- **Ledger uses `producer.append_api_event` directly**, not `langgraph.config.get_stream_writer()`. Going through the producer keeps every event under the same projection / preliminary-presentation path as the rest of the runtime, with no separate parallel pipe to maintain. The ContextVar still gives tools a zero-thread access pattern.
- **Postgres adapter is deferred** to a follow-up PR. The in-flight PR 1.3 (drafts) ships in-memory only too; both share the pattern. Tests use `InMemoryCitationStore`. Production must add a `runtime_citations` repo before this PR can ship.
- **Streamdown remark plugin re-uses the existing `MarkdownLink` slot** by rewriting `[c<id>]` tokens to `[<token>](#cite:<id>)` mdast links. `MarkdownLink` checks the `#cite:` prefix and renders `<CitationChip />`. One inline-element slot, no new component-map registration, no rehype dance.
- **Per-tool `cite()` instrumentation is left for a follow-up.** The ledger + wire is universal; only one reference instrumentation will land per connector family in a separate PR (see plan W1 phase 4).
- **Anthropic and OpenAI native passthrough adapters are deferred** to a follow-up. The universal tool-side path is in place; native passthrough is pure additive optimization (per spec §3.6).
- **Frontend "auto-open Workspace pane on first ingest"** lands with W3.2 (Workspace pane right-rail). Today the registry is built and the chips render; the pane that hosts the Sources tab is the next PR.

## What ships in this PR

| Layer           | File                                                                                                                                                                                                                                                   | Purpose                                                                                                                                                           |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Migration       | [`services/ai-backend/migrations/0015_runtime_citations.sql`](../../services/ai-backend/migrations/0015_runtime_citations.sql) + rollback                                                                                                              | `runtime_citations` table, RLS policy, app grants                                                                                                                 |
| do_rls          | [`services/ai-backend/migrations/staged/do_rls.sql`](../../services/ai-backend/migrations/staged/do_rls.sql)                                                                                                                                           | ENABLE/FORCE RLS on the new table (matches existing rollout pattern)                                                                                              |
| Wire types      | [`packages/api-types/src/index.ts`](../../packages/api-types/src/index.ts)                                                                                                                                                                             | `CitationSourceRef`, `SourceIngestedPayload`, `RuntimeFinalResponsePayload`, `source_ingested` event type, `isCitationSourceRef`/`isSourceIngestedPayload` guards |
| Record          | [`services/ai-backend/src/agent_runtime/persistence/records/citations.py`](../../services/ai-backend/src/agent_runtime/persistence/records/citations.py)                                                                                               | `CitationRecord` Pydantic contract + `to_wire_payload()`                                                                                                          |
| Port            | [`services/ai-backend/src/agent_runtime/persistence/ports.py`](../../services/ai-backend/src/agent_runtime/persistence/ports.py)                                                                                                                       | `CitationStorePort` (insert_or_get + list_for_run + list_for_conversation)                                                                                        |
| In-memory store | [`services/ai-backend/src/runtime_adapters/in_memory/citation_store.py`](../../services/ai-backend/src/runtime_adapters/in_memory/citation_store.py)                                                                                                   | `InMemoryCitationStore`                                                                                                                                           |
| Ledger          | [`services/ai-backend/src/agent_runtime/capabilities/citations.py`](../../services/ai-backend/src/agent_runtime/capabilities/citations.py)                                                                                                             | `SourceRef`, `CitationLedger`, `CITATION_LEDGER_CTX`                                                                                                              |
| Enum            | [`services/ai-backend/src/runtime_api/schemas/common.py`](../../services/ai-backend/src/runtime_api/schemas/common.py)                                                                                                                                 | `RuntimeApiEventType.SOURCE_INGESTED`                                                                                                                             |
| Projector       | [`services/ai-backend/src/runtime_api/schemas/events.py`](../../services/ai-backend/src/runtime_api/schemas/events.py)                                                                                                                                 | activity_kind = TOOL · display_title = "Cited <title>" · status = COMPLETED · payload allow-list                                                                  |
| Messages        | [`services/ai-backend/src/agent_runtime/api/constants.py`](../../services/ai-backend/src/agent_runtime/api/constants.py)                                                                                                                               | `Messages.Event.SOURCE_INGESTED`, `source_cited_title()`                                                                                                          |
| Worker wiring   | [`services/ai-backend/src/runtime_worker/handlers/run.py`](../../services/ai-backend/src/runtime_worker/handlers/run.py)                                                                                                                               | `_bind_citation_ledger`, ContextVar bind/unbind, seal `final_response.citations`                                                                                  |
| FE registry     | [`apps/frontend/src/features/chat/chatModel/citationsRegistry.ts`](../../apps/frontend/src/features/chat/chatModel/citationsRegistry.ts)                                                                                                               | `CitationRegistryByRun`, `upsertCitation(s)`, `citationsForRun`, `citationsByOrdinal`                                                                             |
| FE reducer      | [`apps/frontend/src/features/chat/chatModel/citationReducer.ts`](../../apps/frontend/src/features/chat/chatModel/citationReducer.ts)                                                                                                                   | `applyCitationEvent`, `buildCitationRegistry`                                                                                                                     |
| Provider        | [`apps/frontend/src/features/chat/components/citations/citationsContext.tsx`](../../apps/frontend/src/features/chat/components/citations/citationsContext.tsx)                                                                                         | `CitationsProvider`, `useCitation`, `useCitations`                                                                                                                |
| Chip            | [`apps/frontend/src/features/chat/components/citations/CitationChip.tsx`](../../apps/frontend/src/features/chat/components/citations/CitationChip.tsx)                                                                                                 | Inline superscript chip with connector glyph + tooltip + open-source action                                                                                       |
| Plugin          | [`apps/frontend/src/features/chat/components/markdown/citationRemarkPlugin.ts`](../../apps/frontend/src/features/chat/components/markdown/citationRemarkPlugin.ts)                                                                                     | Tokenizer for `[c<id>]` → `#cite:<id>` link                                                                                                                       |
| Markdown        | [`apps/frontend/src/features/chat/components/markdown/MarkdownText.tsx`](../../apps/frontend/src/features/chat/components/markdown/MarkdownText.tsx), [`MarkdownLink.tsx`](../../apps/frontend/src/features/chat/components/markdown/MarkdownLink.tsx) | Plugin registration + chip routing                                                                                                                                |
| ChatScreen      | [`apps/frontend/src/features/chat/ChatScreen.tsx`](../../apps/frontend/src/features/chat/ChatScreen.tsx)                                                                                                                                               | Registry state, reducer wiring, replay rebuild on history load / OAuth restore / new chat / conversation switch, provider mount                                   |

## Test results

- **ai-backend**: 19 unit tests for ledger + projection passing. Migration apply/rollback round-trip passes against sqlite. Manifest matches lock. Wider suite: 519 passing, the only pre-existing failure is in `tests/unit/agent_runtime/budgets/test_enforcer_and_charger.py::TestConcurrentReservation::test_two_concurrent_runs_against_one_dollar_each_admit_one_each` — touches no citation code.
- **frontend**: 11 tests for the citation reducer + remark plugin passing. `npm run typecheck --workspace @enterprise-search/frontend` clean. `npm run build --workspace @enterprise-search/frontend` clean. Wider suite: 168 passing. The 4 unrelated failures are in `useConversationConnectors.test.tsx` (in-flight PR 1.2 work).

## Remaining work for this surface (follow-up PRs)

1. **Postgres adapter for `CitationStorePort`** — UPSERT on `(run_id, source_connector, source_doc_id)` returning the existing row.
2. **Reference tool wiring** — `await CitationLedger.cite(source)` in 4 reference connectors (Notion / Drive / Slack / web). One PR per connector family.
3. **Anthropic stream adapter** — intercept `citations_delta`, route through the same `cite()`, substitute the token into the corresponding text delta.
4. **OpenAI Responses adapter** — drain `output_text.done.annotations`, route through `cite()`, rewrite final text once.
5. **Workspace pane Sources tab** — lands with W3.2; reads `useCitations()` for live + `GET /v1/agent/conversations/{id}/sources` for archive.
6. **Pane auto-open on first citation** — wires to the same registry once W3.2 ships.
7. **CSS for `.citation-chip`** — minimal styles for the superscript chip; ships with W0 design tokens / W3.1 chat polish.

---

## 1 · PRD

### 1.1 Problem

Atlas is sold on the principle **"citations are first-class — every claim Atlas makes is a hyperlink to the source."** Today the assistant emits plain text. There is no structured wire field for citations, no way for the Sources tab to populate as the agent works, and no mechanism for inline superscript chips. Without this, every other right-rail feature (Sources tab, "Source restricted" share view, per-connector usage attribution) is blocked.

### 1.2 Goals

1. **Inline chips that appear _with the words_.** When the assistant streams "the embargo lifts on Apr 21 [c3]", the `[c3]` token must render as a clickable chip the moment the surrounding token leaves the stream — not at end-of-turn.
2. **Sources panel populates live.** The Workspace pane → Sources tab gains a row the instant the underlying tool result is observed, before the model has finished writing about it.
3. **Stable, replayable.** Reconnecting to a run via `?after_sequence=N` reproduces the citation registry deterministically. Loading a run from history via `replayRunEvents` reproduces the same chips.
4. **Cross-provider, no fork.** Works for Anthropic (native `citations_delta`), OpenAI Responses (end-of-turn `annotations`), and tool-emitted citations (every other tool that returns docs). Single emission seam.
5. **Forward-compatible with sharing ACLs.** A citation must carry enough source identity that the share-recipient view (W6) can resolve "viewer can / cannot see this source" without hitting the source system.

### 1.3 Non-goals (this PR)

- Editing or annotating citations after the fact.
- Multimodal citations (Figma frames, Loom timestamps, Excel cells) — design's "future explorations".
- Per-citation analytics ("which source got clicked").
- Source previews beyond title + snippet + freshness.
- Re-ranking or de-duplication across runs.
- Per-tool MCP scope toggles — separate PR.

### 1.4 Success criteria

- Search-and-summarize flow renders Sources panel with ≥1 row before `final_response` fires (live test).
- Inline `[c<id>]` tokens in streamed text render as `<CitationChip>` within ≤1 frame of the containing text token landing.
- `make test` passes; ai-backend full suite passes; frontend `typecheck`+`build` pass.
- Replaying a completed run via `replayRunEvents` produces the same chip set, in the same order.
- Sources panel survives an SSE reconnect (proven by `02-sse-reconnect-after-blip.md` use case extended).
- No new top-level abstraction in the agent harness — only one new helper, one new event type, one new table, one new FE reducer branch, one Streamdown plugin.

### 1.5 User stories

| #    | Persona                         | Story                                                                                                                                                                                                                                                     |
| ---- | ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | End user (Sarah, Marketing Ops) | I ask Atlas to "summarize last week in #launch-aurora." Sources begin appearing in the right rail as soon as the agent reads the first thread. When the prose lands, every claim has a numbered chip — I can click any chip to verify within two seconds. |
| US-2 | End user mid-run                | My SSE drops for 4 seconds. When it reconnects via `?after_sequence=N`, the chips and Sources panel are exactly where they were — no flash, no duplication.                                                                                               |
| US-3 | End user reading later          | I open a thread from yesterday. The chips render exactly as they did live; clicking still scrolls to the right Sources row.                                                                                                                               |
| US-4 | Share recipient (W6)            | I open a shared thread. A chip whose source I can't see shows "Source restricted" instead of the snippet — but the chip itself still renders so I can see _that_ a citation existed.                                                                      |
| US-5 | Engineer wiring a new tool      | I add a `search_confluence` tool. To make its results citable I call `cite(source)` once per doc and embed the returned token in the snippet. No other wiring needed.                                                                                     |

---

## 2 · Wire contract

One new event type, one new payload field on an existing event, one inline token convention. Everything else reuses what `RuntimeEventEnvelope` already gives us (`run_id`, `sequence_no`, `event_type`, `payload`, `metadata`, `presentation`).

### 2.1 New event: `source_ingested`

```ts
// packages/api-types/src/index.ts
export interface CitationSourceRef {
  citation_id: string; // "c<base36>", short, stable per run
  source_connector: string; // "notion" | "drive" | "slack" | "salesforce" | ... | "web" | "file"
  source_doc_id: string; // connector-native ID (e.g. notion page id, slack ts, drive fileId, sha for files)
  source_url: string | null; // canonical deep link if available
  title: string;
  snippet: string | null; // 0–280 chars; can be null for opaque sources
  freshness_at: string | null; // ISO 8601 of source's "modified at" if known
  source_tool_call_id: string | null; // ties chip → tool card (for "show the work")
  ordinal: number; // 1-based, monotonically allocated per run
}

export interface RuntimeSourceIngestedEvent extends RuntimeEventEnvelopeBase {
  event_type: "source_ingested";
  payload: { citation: CitationSourceRef };
}
```

The presentation projector emits `activity_kind=tool` for these (they belong with the tool that surfaced them) but with a separate `display_title="Cited {title}"` so the timeline doesn't double-count them as full tool cards. They're informational; the visible work is still the tool call.

### 2.2 Inline token

Model-emitted text contains opaque tokens of the form `[c<base36>]` (e.g. `[c1]`, `[c2k]`, `[czh]`). Frontend matches them with regex `/\[c([0-9a-z]+)\]/g` and resolves via the run's citation registry. Unknown tokens render as a muted placeholder (`[?]`) — protective against model hallucinating IDs we never registered.

### 2.3 `final_response` augmentation

```ts
export interface RuntimeFinalResponseEvent extends RuntimeEventEnvelopeBase {
  event_type: "final_response";
  payload: {
    text: string;
    citations: CitationSourceRef[]; // sealed snapshot; identical to ingest order, used for archive/share
  };
}
```

`citations[]` is a **sealed** post-stream copy of everything observed during the run. Replay can rebuild the registry from `source_ingested` events; `final_response.citations` is convenience + a contract anchor for downstream consumers (share recipient view, audit log, future per-connector usage).

---

## 3 · Architecture

### 3.1 Single emission seam — `cite()` helper

All three production paths converge on **one helper** (DRY anchor of the design):

```python
# services/ai-backend/src/agent_runtime/capabilities/citations.py
def cite(source: SourceRef) -> str:
    """
    Register a source against the active run. Idempotent on (connector, doc_id).
    Returns the inline token (e.g. "[c3]") to embed in tool result text or model output.

    Implementation: look up run-scoped registry → if present, return cached token;
    else allocate ordinal, persist row, stream `source_ingested` via get_stream_writer(),
    return token.
    """
```

`SourceRef` is the input shape (everything in `CitationSourceRef` minus `citation_id` and `ordinal`, which the helper allocates). It uses [`langgraph.config.get_stream_writer()`][langgraph-stream] to push the event into the existing custom-channel of the LangGraph stream — _no new channel, no parallel pipe_. The runtime already runs with `stream_mode=["messages","updates","custom","values"]` (see `agent_runtime/execution/runtime.py:28-43`), so custom events flow through `streaming_executor.py` and become normal `RuntimeEventEnvelope`s with auto-allocated `sequence_no`.

The registry lives **per-run inside `RuntimeEventProducer`**, mirroring the existing `_intent_buffer` pattern (`agent_runtime/api/events.py:77`). No LangGraph state-channel needed; nothing for tool authors to thread; nothing to manage at graph compile time.

### 3.2 Three production paths, one helper

```
                      ┌──────────────────────────────────┐
  Tool returns docs ──┤  cite(source) → "[c3]"           │
  (search_notion etc) │  embedded in tool result text    │──┐
                      └──────────────────────────────────┘  │
                                                            │
                      ┌──────────────────────────────────┐  │
  Anthropic stream:   │  citations_delta intercepted in  │  │
  citations_delta  ───┤  provider wrapper → cite(source) │──┤
                      │  → "[c3]" inserted in delta text │  │     ┌────────────────────┐
                      └──────────────────────────────────┘  ├────▶│  RuntimeEventProducer
                                                            │     │  ._citation_registry
                      ┌──────────────────────────────────┐  │     │  (per-run, like     │
  OpenAI Responses    │  output_text.done annotations    │  │     │   _intent_buffer)   │
  (end of turn) ──────┤  drained → cite(source) per item │──┘     └─────────┬──────────┘
                      │  → tokens substituted in final   │                  │
                      │  text by FE token-resolver       │                  │ stream
                      └──────────────────────────────────┘                  ▼
                                                            ┌────────────────────────┐
                                                            │ source_ingested event  │
                                                            │  (sequence_no, payload │
                                                            │   = CitationSourceRef) │
                                                            └────────────┬───────────┘
                                                                         │ persists
                                                                         ▼
                                                            ┌────────────────────────┐
                                                            │ runtime_citations row  │
                                                            └────────────────────────┘
```

#### 3.2.1 Tool-side (default for every connector tool)

Tool authors append `cite(source)` once per source they want the assistant to be able to cite, and **embed the returned token in the snippet they hand back to the model**. Example pattern — concentrated, ≤6 LOC per tool:

```python
# services/ai-backend/src/agent_runtime/capabilities/tools/builtin/search_notion.py
async def search_notion(query: str) -> str:
    pages = await notion_client.search(query)
    lines = []
    for page in pages:
        token = cite(SourceRef(
            source_connector="notion",
            source_doc_id=page.id,
            source_url=page.url,
            title=page.title,
            snippet=page.preview,
            freshness_at=page.last_edited_at,
        ))
        lines.append(f"{token} {page.title}\n  {page.preview}")
    return "\n".join(lines)
```

The model sees `[c3] Aurora 4.0 — Approved Positioning v3 …` in the tool result and is naturally inclined to keep the token in its output when summarizing. We add one short sentence to the system prompt: _"When you reference content from a tool result that contains a `[c<id>]` token, include the token in your reply so the source can be linked."_ (Anthropic + GPT-5 both follow this reliably; we test it.)

#### 3.2.2 Anthropic native passthrough

Anthropic's Messages API emits `content_block_delta { delta: { type: "citations_delta", citation: {...} } }` while streaming text deltas. The provider wrapper (we add the wrapper ONLY here — currently LangChain hides these blocks; see Explore report §G) intercepts these blocks before they normalize to `AIMessage.content`, calls `cite(...)` (re-using the same helper), and substitutes the returned token into the corresponding text delta. From the rest of the system's perspective there is no Anthropic-specific code — the events look identical to tool-emitted ones.

This is the only place we bypass LangChain. We keep the bypass to a 30-LOC adapter (`agent_runtime/execution/providers/anthropic_stream_adapter.py`) that wraps the `AsyncAnthropic.messages.stream` call. If Anthropic citations are disabled (e.g. older Claude model), the adapter is a no-op and we degrade to tool-side citations.

> **DRY check:** we don't fork the model invocation path — the adapter wraps the existing `BaseChatModel` flow used elsewhere. If LangChain ships native citation passthrough later (issue [#254 in claude-agent-sdk-typescript][langchain-cite-issue] tracks this for ACP; LangChain Python is on a similar trajectory), we delete the adapter and route through LangChain again.

#### 3.2.3 OpenAI Responses end-of-turn

OpenAI's Responses API delivers `file_citation` / `url_citation` annotations only in the terminal `response.output_text.done` event — not during streaming. The wrapper drains this event, calls `cite(...)` per annotation (same helper, same `source_ingested` events fire — they just all land just before `final_response`), and the FE resolves tokens for them at the same time the rest of the text settles. **No FE branching** required — the resolver runs whether tokens land mid-stream or at the end.

OpenAI annotations are referenced positionally in the original streamed text. The wrapper rewrites the assembled text once, replacing `[anno:i]` placeholders (or position spans) with our `[c<id>]` tokens, then emits a single `final_response` carrying the rewritten text.

### 3.3 Persistence

One new table, RLS-isolated like the rest of ai-backend, encrypted via the existing `FieldCodec` v1.

```sql
-- services/ai-backend/migrations/0014_runtime_citations.sql
CREATE TABLE runtime_citations (
  citation_id          TEXT PRIMARY KEY,             -- "c" + base36(ordinal); cheap, stable per run
  run_id               UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  conversation_id      UUID NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
  org_id               UUID NOT NULL,
  ordinal              INTEGER NOT NULL,             -- 1-based per run
  source_connector     TEXT NOT NULL,
  source_doc_id        TEXT NOT NULL,
  source_url           TEXT,
  title                TEXT NOT NULL,                -- encrypted v1
  snippet              TEXT,                          -- encrypted v1
  freshness_at         TIMESTAMPTZ,
  source_tool_call_id  TEXT,
  encryption_version   SMALLINT NOT NULL DEFAULT 1,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Idempotency: cite() on the same (run, connector, doc) returns the cached row.
CREATE UNIQUE INDEX runtime_citations_run_source_uk
  ON runtime_citations (run_id, source_connector, source_doc_id);
-- Sources tab read path:
CREATE INDEX runtime_citations_conv_idx
  ON runtime_citations (conversation_id, created_at);
-- RLS (mirrors migration 0008 patterns):
ALTER TABLE runtime_citations ENABLE ROW LEVEL SECURITY;
CREATE POLICY runtime_citations_tenant_isolation ON runtime_citations
  USING (org_id = current_setting('app.current_org_id')::uuid);
```

`runtime_citations` is **denormalized for read** — the same data exists implicitly in `source_ingested` event payloads, but we want the Sources tab and share-recipient ACL check to hit a single index, not replay events.

Citation rows are **transactionally tied** to the event append: `cite()` inserts the row and the event in the same `event_store.append_event` transaction. If either fails, neither persists.

Retention follows conversation retention: ON DELETE CASCADE handles teardown for hard deletes; soft-delete via `agent_conversations.deleted_at` (W1.6) hides the rows behind a view filter (added in same migration).

### 3.4 Frontend

#### 3.4.1 Reducer

One new branch in `apps/frontend/src/features/chat/chatModel/eventReducer.ts`:

```ts
case "source_ingested": {
  const next = upsertCitation(items, event.run_id, event.payload.citation);
  return next;
}
```

`upsertCitation` lives next to the other reducer helpers, idempotent on `citation_id`. Citations are stored on the _run_ (a `Map<runId, Map<citationId, CitationSourceRef>>`) so they survive interleaved messages.

#### 3.4.2 Streamdown plugin (the only inline-rendering code)

Streamdown (already in use, see `MarkdownText.tsx:1-39`) accepts remark/rehype plugins. We add **one** remark plugin — a tokenizer that finds `[c<id>]` in text nodes and rewrites them to a custom `citationChip` mdast node. A matching `components` entry on `<Streamdown>` renders that node as `<CitationChip id={id} />`, which pulls the source from the run-scoped citation registry via React context.

Total FE delta: one plugin file (~40 LOC), one component (~30 LOC), one reducer case (~15 LOC), one context provider (~25 LOC). Streamdown does the streaming-safe parsing for us; we don't reimplement markdown.

> **DRY check:** the Atlas prototype's `Prose` component (`messages.jsx:43-87`) hand-parses `[1]` tokens with a regex during render. We're deliberately _not_ copying that — the prototype was a single-file hi-fi mock; production uses Streamdown's tokenizer so partial tokens during streaming (`[c`, `[c3`) don't render as chips until the closing `]` arrives.

#### 3.4.3 Sources tab

`WorkspacePane > SourcesTab` (built in W3.2) reads the registry for the active run + falls back to `GET /v1/agent/conversations/{id}/sources` for archived runs. List is rendered in `ordinal` order. Each row mirrors `data.jsx:62-122` of the prototype: app icon, title, breadcrumb, snippet, freshness, author. Clicking a chip in the thread scrolls the matching row into view and flashes the accent border (existing `useScrollIntoView` helper).

#### 3.4.4 Pane auto-open

Per the user decision in W0, the workspace pane opens automatically when the registry crosses 0 → 1 entries. We hang this off the same context: if `citations.size === 0` and the user hasn't manually toggled, leave closed; first ingest opens it.

### 3.5 Cross-provider behavior matrix

| Provider                                                          | When citations land                                | UX impact                                                                                       |
| ----------------------------------------------------------------- | -------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| **Tool-emitted** (Notion / Drive / Slack / Salesforce / web etc.) | Live, before `model_delta` references              | Sources tab populates first; chips appear inline as model streams                               |
| **Anthropic (Claude 3.5+, 4.x)** with `citations` enabled         | Live, interleaved with `model_delta`               | Identical to tool-emitted from the user's perspective                                           |
| **OpenAI Responses**                                              | Burst at end-of-turn, just before `final_response` | Sources tab fills near the end; chips appear as final text settles. Same path; no FE branching. |
| **Tool-emitted + Anthropic** mixed                                | All live                                           | Anthropic's IDs and tool IDs share the same registry, deduped by `(connector, doc_id)`          |

The user-perceived behavior is consistent: _chips appear with the words that reference them_. The only variance is when the words land — which is a property of the model, not our UI.

### 3.6 Provider-agnostic guarantee (self-hosted, BYOK, OSS models)

The tool-side path is the **universal path** — it does not depend on any provider primitive. Because `cite()` runs inside the tool function and returns a token that gets embedded in the tool result the model reads, this works with _any_ model the runtime can stream from: self-hosted Llama via vLLM / Ollama / TGI, Groq-hosted models, Gemini, Grok, future BYOK providers. The two native-passthrough adapters (§3.2.2 Anthropic, §3.2.3 OpenAI Responses) are _pure additive optimizations_ that no-op when the provider doesn't emit the relevant primitive.

**What you get on a self-hosted / OSS model:**

| Capability                                      | Self-hosted (e.g. Llama 3.3 70B on vLLM)      | Anthropic Claude | OpenAI Responses    |
| ----------------------------------------------- | --------------------------------------------- | ---------------- | ------------------- |
| Sources tab populates live as tools run         | ✅                                            | ✅               | ✅                  |
| Inline `[c<id>]` chips render in assistant text | ✅ — _if model retains the token_ (see below) | ✅               | ✅ (at end-of-turn) |
| Replay / SSE-resume produces identical chips    | ✅                                            | ✅               | ✅                  |
| Encrypted persistence, RLS, retention           | ✅                                            | ✅               | ✅                  |
| Share-recipient ACL on chip (W6)                | ✅                                            | ✅               | ✅                  |

**Token-retention failure mode.** Inline chips depend on the model keeping `[c<id>]` tokens in its output. Stronger instruction-followers (Claude, GPT-5, Llama 3.3 70B-class, Gemini Pro) follow the one-line system-prompt instruction reliably; smaller / heavily-quantized / older models may drop the tokens. **When that happens, nothing breaks** — `source_ingested` events still fire from the tools, the Sources tab still populates, only the inline chips are absent. The trust signal degrades from _"verifiable next to the claim"_ to _"verifiable in the panel."_ No code change is needed to recover; switching the conversation to a stronger model brings inline chips back.

We help weaker models retain tokens with three nudges, all already in the design:

1. Tools embed the token _adjacent to the snippet_ the model is most likely to quote (`f"{token} {snippet}"`), making retention the path of least resistance.
2. The system-prompt rule is short and unambiguous (single sentence, no examples).
3. We add one optional eval (`tests/integration/test_citation_retention_across_models.py`) that runs each configured provider against a fixture prompt + tool result and asserts ≥80% token retention. Models below threshold are flagged in the model-catalog UI as "inline citations: best-effort," and the Settings → Model & behavior pane (W4.3) surfaces the flag so admins can decide.

**No new code per provider.** Adding a new self-hosted provider requires only a LangChain chat-model class — no citation-specific glue. Adding a _future_ native-citation provider (e.g. Gemini grounding) means writing one more 50-LOC adapter that calls `cite()`, exactly like the Anthropic and OpenAI ones.

---

## 4 · DRY / re-use audit

Every existing primitive we deliberately reuse rather than reinvent:

| Need                                            | Re-used                                                                                                     | Why this beats a fork                                                                       |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Event ordering, sequence_no, replay, SSE resume | `RuntimeEventProducer.append_api_event` + `event_store.append_event` (`agent_runtime/api/events.py:81-143`) | All wire guarantees come for free — same backpressure, same persistence, same RLS.          |
| Per-run mutable scratch                         | `RuntimeEventProducer._intent_buffer` pattern (`api/events.py:77`)                                          | Avoid a LangGraph state-channel migration and any tool-author thread-through.               |
| Custom in-graph emission from tools             | `langgraph.config.get_stream_writer()` ([LangGraph custom-events][langgraph-stream])                        | First-party LangGraph API; we already use `stream_mode=["custom"]`.                         |
| Anthropic citation primitive                    | `citations_delta` content-block delta ([Anthropic Citations API][anthropic-citations])                      | Use the model's own grounding; don't ask the model to fabricate IDs.                        |
| OpenAI annotations                              | `response.output_text.done` ([OpenAI Responses streaming][openai-streaming])                                | Same wire shape, same helper, no provider-specific FE code.                                 |
| Inline rendering                                | Streamdown remark plugin slot (`MarkdownText.tsx`)                                                          | Streaming-safe markdown is a solved problem — we don't reparse text by hand.                |
| Persistence encryption                          | `FieldCodec` v1 (migration 0011)                                                                            | Title + snippet may carry sensitive content; existing codec is the contract.                |
| RLS, audit, retention                           | Existing patterns from migrations 0008 / 0012                                                               | Compliance review only needs to verify "this table follows the same pattern as the others." |
| Frontend event routing                          | `applyRuntimeEvent` reducer (`eventReducer.ts`)                                                             | One new `case` mirrors every other event handler — no parallel state machine.               |
| Sources tab list                                | Existing `WorkspacePane` slot from W3.2 + the same `app-ic` glyph used elsewhere                            | One renderer, one source of truth for connector glyphs.                                     |

**Things we explicitly do not introduce:**

- A new "citation service" module — the helper is 50 LOC.
- A LangGraph state-schema migration.
- A custom markdown parser on the FE.
- A per-provider FE branch.
- A separate "citation event bus."
- A new background polish step (the deterministic template covers presentation; no LLM-enriched titles needed for citation rows).

---

## 5 · Code surface inventory

Approximate sizes are upper bounds — pessimistic guesses for code review.

### 5.1 `packages/api-types`

| File           | Change                                                                                                           | Est. LoC |
| -------------- | ---------------------------------------------------------------------------------------------------------------- | -------- |
| `src/index.ts` | `CitationSourceRef`, `RuntimeSourceIngestedEvent`, augment `RuntimeFinalResponseEvent`, new `event_type` literal | +50      |

### 5.2 `services/ai-backend`

| File                                                                      | Change                                                                                                | Est. LoC   |
| ------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- | ---------- |
| `migrations/0014_runtime_citations.sql` (+ rollback)                      | new table, indexes, RLS policy, view filter for soft-delete                                           | +60        |
| `src/agent_runtime/persistence/records/citations.py` (new)                | `CitationRecord` dataclass, codec hooks                                                               | +70        |
| `src/agent_runtime/persistence/ports.py`                                  | `insert_citation(...)` port + idempotent UPSERT                                                       | +20        |
| `src/agent_runtime/persistence/postgres/citation_repo.py` (new)           | concrete adapter                                                                                      | +60        |
| `src/agent_runtime/capabilities/citations.py` (new)                       | `SourceRef`, `cite()` helper, `_resolve_writer()`                                                     | +80        |
| `src/agent_runtime/api/events.py`                                         | add `_citation_registry` buffer (mirrors `_intent_buffer`); helper integration                        | +30        |
| `src/agent_runtime/api/presentation_templates.py`                         | template for `source_ingested` (deterministic — no LLM polish)                                        | +20        |
| `src/runtime_api/schemas/common.py` + `schemas/events.py`                 | enum case `SOURCE_INGESTED`, projector branch, payload extractor                                      | +40        |
| `src/runtime_worker/streaming_executor.py`                                | drain `final_response.citations` from registry on terminal event                                      | +15        |
| `src/agent_runtime/execution/providers/anthropic_stream_adapter.py` (new) | wraps Anthropic stream, intercepts `citations_delta`, calls `cite()`                                  | +60        |
| `src/agent_runtime/execution/providers/openai_response_adapter.py` (new)  | drains `output_text.done.annotations`, calls `cite()`, rewrites text once                             | +50        |
| `src/agent_runtime/capabilities/tools/builtin/*`                          | embed `cite()` in 4 reference connectors (notion/drive/slack/web). Other tools migrate in follow-ups. | +6 LOC × 4 |
| `tests/unit/agent_runtime/test_citations.py` (new)                        | helper unit tests + provider-adapter tests                                                            | +200       |
| `tests/unit/runtime_api/test_runtime_event_timeline.py`                   | extend timeline assertions to cover `source_ingested` ordering + replay                               | +80        |
| `docs/use-cases/14-citations-during-streaming.md` (new)                   | use-case doc following the existing template                                                          | +120       |

### 5.3 `apps/frontend`

| File                                                                                      | Change                                                                    | Est. LoC          |
| ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ----------------- |
| `src/features/chat/chatModel/citationsRegistry.ts` (new)                                  | per-run `Map`, context provider, hooks (`useCitation`, `useRunCitations`) | +60               |
| `src/features/chat/chatModel/eventReducer.ts`                                             | one `case "source_ingested"` branch; thread registry through items        | +25               |
| `src/features/chat/components/citations/CitationChip.tsx` (new)                           | chip component (number + connector glyph + tooltip + click handler)       | +50               |
| `src/features/chat/components/markdown/citationRemarkPlugin.ts` (new)                     | remark plugin tokenizer for `[c<id>]`                                     | +40               |
| `src/features/chat/components/markdown/MarkdownText.tsx`                                  | register plugin + components map entry                                    | +10               |
| `src/features/chat/components/workspace/SourcesTab.tsx` (W3.2; this PR adds data binding) | bind to `useRunCitations`                                                 | (covered in W3.2) |
| `__tests__/citationRemarkPlugin.test.ts`, `CitationChip.test.tsx`                         | edge cases incl. partial tokens during stream                             | +150              |

**Totals:** ai-backend ~1.0k LoC (incl. tests + use case); frontend ~340 LoC (incl. tests); contracts ~50 LoC. The system core (helper + table + event + plugin + reducer) is ~250 LoC. The rest is provider adapters and tests.

---

## 6 · End-to-end sequence (search & summarize)

```
Browser           backend-facade        ai-backend (api)        runtime_worker         LangGraph
  │                    │                      │                      │                     │
  │  POST /v1/agent/runs                                              │                     │
  │ ──────────────────▶│ ─────────────────────▶│ create run, queue ──▶│                    │
  │                    │                      │                      │ ─astream(graph)───▶ │
  │  GET /…/stream?after_sequence=0           │                      │                     │
  │ ──────────────────▶│ ─────────────────────▶│  open SSE channel    │                     │
  │                    │                      │                      │ ◀─tool node fires── │
  │                    │                      │                      │                     │
  │                    │                      │                      │   search_notion(…) returns 3 docs
  │                    │                      │                      │                     │
  │                    │                      │                      │ ◀─cite() ×3 (writer.send) via
  │                    │                      │                      │   get_stream_writer()
  │                    │                      │                      │                     │
  │                    │                      │  source_ingested×3   │                     │
  │ ◀─────────────── SSE ◀───────────── proxy ◀│  (seq 11,12,13)      │                     │
  │   (Sources tab gains 3 rows; pane auto-opens)                     │                     │
  │                    │                      │                      │                     │
  │                    │                      │                      │ ◀── model_delta ──── │
  │                    │                      │                      │   "Per the [c1] positioning…"
  │                    │                      │  model_delta (seq 14)│                     │
  │ ◀─────────────── SSE                       │                     │                     │
  │   (chip [c1] resolves immediately via registry)                   │                     │
  │                    │                      │                      │ ◀── final_response ─│
  │                    │                      │                      │   {text, citations[]}
  │                    │                      │  final_response      │                     │
  │ ◀─────────────── SSE                       │                     │                     │
```

For Anthropic citations, replace `cite() ×3` with the provider adapter doing the same thing during the model node (instead of inside a tool node). For OpenAI, the `cite() ×N` and `final_response` come back-to-back at the very end of the run.

---

## 7 · Edge cases & their resolutions

| Case                                                             | Resolution                                                                                                                                                                                                                                         |
| ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Same source cited twice in one run                               | `cite()` is idempotent on `(run_id, connector, doc_id)`; second call returns the cached token without emitting a new event.                                                                                                                        |
| Same source cited in two different runs of the same conversation | Two separate rows, two distinct `citation_id`s. The Sources tab dedupes display by `(connector, doc_id)` for the conversation-wide view.                                                                                                           |
| Model fabricates a `[c99]` we never registered                   | FE renders muted `[?]` placeholder; we count + log; never raises. (Test: `unknown_token_renders_placeholder`.)                                                                                                                                     |
| Partial token mid-stream (`[c`, `[c3`)                           | Streamdown's tokenizer holds incomplete tokens; remark plugin only matches closed tokens. Until `]` arrives the bytes render as plain text.                                                                                                        |
| SSE drops mid-run                                                | Reconnect with `?after_sequence=N`; `source_ingested` events stream through normal replay; registry is rebuilt deterministically. (Reuses existing replay path; no special case.)                                                                  |
| Run cancelled before `final_response`                            | All `source_ingested` rows persist; UI shows partial chip set. `final_response.citations` is omitted (it never fires). Replay still works because the events are persisted.                                                                        |
| Tool returns 50 docs                                             | Each is a row + event. Activity card stays compressed (one line: "Read 50 docs"); Sources tab handles long lists with virtualization (already used elsewhere). We add a per-tool soft cap of 25 to discourage flooding; tool authors can override. |
| Source title or snippet contains sensitive PII                   | Encrypted at rest via `FieldCodec` v1; the wire still carries plaintext (this matches the existing message wire, which is also plaintext over TLS in transit and encrypted at rest).                                                               |
| Share recipient (W6) without source access                       | The share-resolver substitutes `title="Source restricted"`, `snippet=null`, `source_url=null` based on a connector-ACL check before serving the conversation. Chip still renders (US-4).                                                           |
| Anthropic citation refers to a doc the user lacks access to      | This shouldn't happen for tool-mediated retrieval — the tool ran under the user's session. For non-tool sources (Anthropic web search), we treat the source as `source_connector="web"` with no ACL check needed.                                  |

---

## 8 · Test plan

### 8.1 Unit (ai-backend, ~12 cases)

- `cite_returns_token` · `cite_is_idempotent_on_run_source` · `cite_allocates_monotonic_ordinals`
- `cite_persists_in_same_txn_as_event` · `cite_writes_encrypted_title_and_snippet`
- `anthropic_adapter_extracts_citations_delta` · `anthropic_adapter_substitutes_token_into_text_delta`
- `openai_adapter_drains_annotations_at_done` · `openai_adapter_rewrites_text_once`
- `event_projector_emits_activity_kind_tool_for_source_ingested`
- `final_response_seals_citations_in_ordinal_order`
- `unknown_run_id_writer_raises` (defensive)

### 8.2 Frontend (~10 cases)

- `remark_plugin_replaces_closed_token` · `remark_plugin_holds_partial_token` · `remark_plugin_handles_multiple_tokens_in_one_run_of_text`
- `chip_resolves_against_registry` · `unknown_chip_renders_placeholder`
- `reducer_upserts_citation` · `reducer_idempotent_on_citation_id`
- `pane_auto_opens_on_first_citation` · `pane_stays_closed_for_tool_free_chat`
- `clicking_chip_scrolls_sources_row`

### 8.3 Integration

- Extend `02-sse-reconnect-after-blip.md` use case test: kill SSE after first `source_ingested`, reconnect, assert Sources tab and chips identical.
- Extend `01-cold-start-first-message.md`: assert Sources panel populates before `final_response`.
- New `14-citations-during-streaming.md` use case doc + test covering the reference flow.
- E2E (Playwright, gated): search & summarize prompt → workspace pane gains a row before final → click chip → row scrolls into view.

### 8.4 Compliance check

- Confirm `runtime_citations` is in the same retention sweep as `runtime_events` (migration 0012).
- Confirm RLS denies cross-org reads in a unit test that sets `app.current_org_id` to another org.
- Confirm `FieldCodec` v1 round-trips for title and snippet (existing test pattern).
- Confirm SIEM exporter (migration 0016) picks up `runtime_audit_log` rows for any citation-related approval-style writes (none in this PR; reserved for future "share recipient resolver" PR).

---

## 9 · Rollout

1. **Behind a runtime config flag** `RUNTIME_CITATIONS_ENABLED` (default off in prod, on in dev). Flag gates: provider adapters, the `cite()` helper return value (no-op string when off), the FE plugin (renders raw `[c<id>]` text when off — no surprises if a stale build hits a new backend or vice versa).
2. **Phase 1 — backend wire.** Land contract + helper + table + adapters with FE flag still off. Replay passes; new event observable via existing `/v1/agent/runs/{id}/events`. No UI change for users.
3. **Phase 2 — instrument 4 reference tools** (notion, drive, slack, web). Internal dogfood with flag on; verify behavior across providers.
4. **Phase 3 — FE flag on.** Chips and Sources tab go live in the Workspace pane (which itself ships in W3.2; gate the pane by feature presence).
5. **Phase 4 — sweep remaining tools.** Add `cite()` to all source-returning tools across MCP wrappers. Each is one PR per connector family.
6. **Phase 5 — flag default-on**, then remove the flag.

Backfill: not required. Prior runs have no citations; the FE renders them as before.

---

## 10 · Open questions (non-blocking)

- **Anthropic vs. tool dedupe**: if Anthropic emits a `citations_delta` for a doc that a tool already cited in the same run, do we coalesce? Recommendation: yes, dedupe on `(connector, doc_id)` — the tool's row wins because it has richer metadata (it knows the connector). Anthropic's web-search citations are `connector="web"` so they won't collide with tool citations.
- **Citation budget per turn**: cap at ~50/run in v1 to keep registry small? Defer until we see flooding.
- **Citation IDs across runs in the same conversation**: stay run-local in v1 (`c1` resets per run). The Sources tab keys on `(connector, doc_id)` for cross-run dedupe at display time.
- **Markdown blocks containing tokens** (e.g. inside fenced code or links) — by spec we don't render chips inside ```code or`<a>`; the remark plugin walks text nodes only and skips inside `code`/`link`. (Test: `chip_does_not_render_inside_code_fence`.)

---

## 11 · References

- Atlas Design Doc (handoff bundle, `/tmp/design-doc/enterprise-search/project/Design Doc.html`) — §"Citations as superscript chips" decision, §"Citations are first-class" principle, Sources tab spec.
- [Anthropic Citations API][anthropic-citations] — `citations_delta` streaming contract.
- [OpenAI Responses streaming events][openai-streaming] — annotations on `output_text.done`.
- [LangGraph streaming custom events][langgraph-stream] — `get_stream_writer()` from tools.
- [LangChain Anthropic citation issue tracker][langchain-cite-issue] — context for our temporary adapter.
- `apps/frontend/CLAUDE.md` — Streamdown rendering rule; activity_kind/display_title/summary/status projection rule.
- `services/ai-backend/CLAUDE.md` — module boundaries.
- `services/ai-backend/docs/CLAUDE.md` — spec-first workflow.
- Inventory report from Explore agent (W0 plan, §A–§I).

[anthropic-citations]: https://docs.anthropic.com/en/docs/build-with-claude/citations
[openai-streaming]: https://platform.openai.com/docs/api-reference/responses-streaming
[langgraph-stream]: https://docs.langchain.com/oss/python/langchain/streaming
[langchain-cite-issue]: https://github.com/anthropics/claude-agent-sdk-typescript/issues/254
