# PR 1.1 follow-ups — close the citations production gap

> **Status:** Spec · v1
> **Reads alongside:** [`01-citations-live-registry.md`](./01-citations-live-registry.md) (the parent design)
> **Scope:** the five gaps the parent PR explicitly deferred, sized so they can be reviewed and shipped together as a "production-shippable" follow-up.

## Context

PR 1.1 landed the citations wire end-to-end (event type, ledger, registry, FE chip, remark plugin) but consciously deferred five things to keep the parent PR focused. Without those follow-ups, citations work in dev but are invisible (no chip styling), can't persist (no Postgres adapter), have no producer wired (no per-tool instrumentation), and have no panel to verify in (no Sources surface). This PR closes the gap so PR 1.1 is production-shippable.

| #   | Gap                                              | Severity                                      | This PR ships?                       |
| --- | ------------------------------------------------ | --------------------------------------------- | ------------------------------------ |
| A   | Inline chips have no styling                     | Cosmetic but visible                          | ✅                                   |
| B   | No Postgres adapter for `CitationStorePort`      | Blocker for prod                              | ✅                                   |
| C   | No producer of citations from real tools         | "Wire is live, no chips appear"               | ✅ (generic MCP projector)           |
| D   | Anthropic native `citations_delta` not lifted    | Trust-signal completeness for chat-only turns | 🟡 spec only — see §D                |
| E   | No Sources panel for users to navigate citations | Verifiability                                 | ✅ (via existing `DetailsPanelHost`) |

## Goals

1. **PR 1.1 is shippable to production** without further glue. Postgres dialect supports the new table; the dependencies factory wires the adapter; the worker handler binds the ledger.
2. **Users see chips with real source data** when an MCP tool returns documents. No per-tool change needed for the common shapes (Anthropic MCP `content[].text` blocks, JSON results carrying `url` + `title`).
3. **Users can open a Sources panel** alongside the chat and verify the active conversation's citations. Re-uses the existing `DetailsPanelHost` slide-out pattern; the full Workspace right-rail (W3.2) lands later.
4. **Anthropic native passthrough has a concrete implementation plan.** We don't ship the adapter in this PR (model-stream invocation surface is non-trivial), but we lock the interface so it's a drop-in when ready.

## Non-goals

- The full Workspace pane right-rail (W3.2 — own surface, multiple tabs).
- OpenAI Responses adapter (parallel design to Anthropic; lands when Anthropic's adapter shape is proven).
- Per-connector custom citation projection (the generic MCP projector handles 90% of cases; specialized projection per connector is its own follow-up if needed).
- Branching/inline-edit/etc. from the parent plan (these belong to W2/W3 chat polish, not this PR).

## Per-item design

### A · Chip CSS

**File:** `apps/frontend/src/styles/citations.css` (new) + import from existing app stylesheet.

Five rules, design-token driven:

```css
.citation-chip {
  display: inline-block;
  vertical-align: super;
  font-size: 0.7em;
  line-height: 1;
  margin: 0 0.1em;
  padding: 0.1em 0.4em;
  border-radius: 999px;
  border: 1px solid var(--color-line, #2a2a2c);
  background: var(--color-surface, #1a1a1c);
  color: var(--color-accent, #d97757);
  text-decoration: none;
  cursor: pointer;
  transition:
    background-color 80ms,
    border-color 80ms;
}
.citation-chip a {
  color: inherit;
  text-decoration: none;
}
.citation-chip:hover {
  background: var(--color-accent-soft, rgba(217, 119, 87, 0.12));
  border-color: var(--color-accent, #d97757);
}
.citation-chip--unresolved {
  color: var(--color-text-dim, #7e7e84);
  border-style: dashed;
  cursor: help;
}
.citation-chip[data-connector] {
  /* hook for connector-specific color overrides */
}
```

Pulls only from existing tokens (already in `packages/design-system/src/styles.css`). Imported once at app entry so it covers every chat surface that ever renders an assistant message — no per-component import.

### B · Postgres adapter for `CitationStorePort`

**Files:**

- `services/ai-backend/src/runtime_adapters/postgres/citation_store.py` (new) — concrete adapter.
- `services/ai-backend/src/agent_runtime/persistence/_reader.py` — reuse the field codec for `title` + `snippet`.
- `services/ai-backend/src/runtime_worker/dependencies.py` — wire the adapter into the worker's `RuntimeRunHandler` constructor.
- `services/ai-backend/src/runtime_api/app.py` — same wiring on the API path (citation reads via the planned `GET /v1/agent/conversations/{id}/sources` lands with W1.5; for this PR the adapter is exposed only to the worker).

**Behavior contract (mirrors port):**

```python
class PostgresCitationStore:
    async def insert_or_get(self, record: CitationRecord) -> CitationRecord:
        # ON CONFLICT (run_id, source_connector, source_doc_id) DO NOTHING RETURNING *.
        # Falls back to a SELECT when DO NOTHING returns 0 rows.
        # Encrypts title + snippet via FieldCodec v1 on write; tags row encryption_version=1.

    async def list_for_run(self, *, org_id, run_id) -> Sequence[CitationRecord]: ...
    async def list_for_conversation(self, *, org_id, conversation_id) -> Sequence[CitationRecord]: ...
```

**Encryption.** Reuses the existing `FieldCodec` (migration 0011 / commits e18c8fe + 31d08c6). New writes always use `encryption_version=1`. Reads tolerate both 0 and 1, mirroring the convention every other PII column follows in this service.

**Idempotency.** UNIQUE INDEX on `(run_id, source_connector, source_doc_id)` from migration 0015. The adapter wraps INSERT in `ON CONFLICT … DO NOTHING RETURNING *`; if zero rows return, follow up with a `SELECT … FOR UPDATE` to fetch the existing row. This matches the contract `InMemoryCitationStore` already enforces.

**Tests.** A Postgres-backed test would require spinning up the database harness; lifting the existing in-memory test contract into a `Test*StorePortContract` mixin and running it against both adapters proves identical behavior. (The drafts PR uses the same pattern.) Concrete test cases:

- `insert_or_get_returns_inserted_row_for_new_key`
- `insert_or_get_returns_existing_row_on_conflict_idempotently`
- `list_for_run_orders_by_ordinal`
- `list_for_conversation_orders_by_created_at_and_filters_org`
- `title_and_snippet_round_trip_through_field_codec_v1`

### C · Generic MCP citation projector

The wire is universal but until something calls `CitationLedger.cite(...)` users never see chips. We don't have a `search_notion` first-party tool yet — what we DO have is an MCP middleware pipeline that receives every MCP tool's structured result before it goes back to the model. The projector is a small middleware that pattern-matches well-known MCP result shapes and registers citations for free.

**File:** `services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/cite_mcp.py` (new).

**Recognized shapes** (all common in the wild — Anthropic's MCP servers, official Notion / Drive / Slack MCP servers, custom enterprise MCP servers we expect to see):

1. **Anthropic MCP content blocks** — result has `{"content": [{"type":"text", "text":"...", "url":"..."}]}` or `{"type":"resource", "resource":{"uri":..., "name":...}}`. One citation per content block with a URL.
2. **Generic search-result list** — top-level `{"results": [{"id":..., "title":..., "url":..., "snippet":...}]}` (used by web-search MCPs and many SaaS connectors).
3. **Single-resource read** — `{"resource":{"uri":..., "title":..., "content":...}}` (used by `read_resource` style tools).

Anything else falls through unchanged — citations are best-effort enrichment.

```python
class CitationProjectingMcpMiddleware:
    @classmethod
    async def project(
        cls,
        *,
        connector: str,
        tool_name: str,
        tool_call_id: str | None,
        result: object,
    ) -> object:
        ledger = CitationLedger.active()
        if ledger is None:
            return result
        sources = cls._extract_sources(connector=connector, result=result)
        for source in sources:
            await ledger.register(
                source.with_tool_call_id(tool_call_id)
            )
        return result  # passthrough — never mutates the result given to the model

    @classmethod
    def _extract_sources(cls, *, connector: str, result: object) -> list[SourceRef]:
        # Pattern-match the three shapes above. Each pattern is a small classmethod.
```

**Embedding tokens in the model's view.** The ledger returns a token, but for the _generic_ projector we deliberately do NOT mutate the tool result text — we just register the source. Why: the model wasn't trained to expect tokens in MCP results, and rewriting structured JSON would break tools that read it back. Inline chips for MCP results land as a follow-up that adds a per-tool opt-in (`{"meta": {"cite_tokens": true}}` on the MCP tool descriptor) — citations still populate the Sources panel either way.

This means **for this PR, MCP-derived citations populate the Sources panel but don't render inline chips inside assistant prose.** Inline chips work only for tools/providers that explicitly write `[c<id>]` into the model context. That's a limitation, captured in §Open questions below.

**Tests.** Unit tests per recognized shape against the in-memory ledger:

- `anthropic_content_block_with_url_emits_citation`
- `generic_results_list_emits_one_citation_per_entry`
- `single_resource_read_emits_one_citation`
- `unrecognized_shape_passes_through_silently`
- `projector_is_noop_when_no_ledger_bound`

### D · Anthropic + OpenAI native passthrough — _not shipped this PR; lock the seam_

**Why not now.** LangChain's `BaseChatModel.astream` normalizes Anthropic's `citations_delta` blocks away before the runtime sees them. Lifting them through requires either:

1. A LangChain callback handler (`on_llm_new_token` doesn't carry the citation block; `on_chat_model_start` is too early). LangChain v0.3 does NOT expose a stable hook for content-block deltas.
2. Bypassing LangChain for Anthropic specifically — calling `AsyncAnthropic.messages.stream` ourselves and re-emitting LangChain-shaped messages. Real surface change to the model wrapper, with risk of subtle drift in tool-call serialization.

Both paths are tractable but neither is small. The right move for this follow-up PR: **lock the integration seam** so the adapter is a drop-in.

**Seam:** `agent_runtime.capabilities.citations.CitationLedger` already exposes the universal entry. The adapter only needs to:

```python
# services/ai-backend/src/agent_runtime/execution/providers/anthropic_stream_adapter.py
class AnthropicCitationStreamAdapter:
    """Wraps an Anthropic stream; intercepts citations_delta blocks; registers
    sources via the active ledger and substitutes [c<id>] tokens into the
    matching text delta before yielding upstream."""

    async def aiter(self, raw_stream): ...
```

We **add the file with a stub + tests for the substitution logic** so the only remaining work to ship native passthrough is wiring the adapter into the model invocation path. The substitution algorithm + token format is provider-agnostic and doesn't depend on LangChain at all — it's pure string-rewriting + ledger calls.

The same shape works for OpenAI Responses (`output_text.done.annotations` drainer); the difference is _when_ citations land (end-of-turn vs interleaved). The ledger doesn't care. Spec is identical; adapter is its own ~50 LOC file.

### E · Sources panel via `DetailsPanelHost`

The Workspace pane right-rail is W3.2 (own surface). For this PR we lean on the existing `DetailsPanelHost` (`apps/frontend/src/features/chat/components/details/`) — the same sliding panel that already hosts `/context` and `/usage`. Adding `sources` as a third `kind` is the smallest path that gives users a verifiable view today.

**Files:**

- `apps/frontend/src/features/chat/components/details/SourcesPanel.tsx` (new) — list of `CitationSourceRef`s, ordered by `ordinal`, app-icon glyph, title, snippet, freshness, "Open source" link.
- `apps/frontend/src/features/chat/components/details/DetailsPanelHost.tsx` — register the new kind.
- `apps/frontend/src/features/chat/ChatScreen.tsx` — pass the active citations (from `useCitations()` / the registry state) into the panel; expose a "View sources" affordance once the registry has ≥1 entry.

**Reads from:** the same context the chips read from (`citationsContext`). No new endpoint required for this PR; archived-conversation Sources viewing is gated on the conversation-level `GET /v1/agent/conversations/{id}/sources` endpoint that ships in W1.5.

**UX.** The panel is a list, not a tabbed surface. Each row mimics the prototype's `SourcesPane` (`/tmp/design-doc/enterprise-search/project/messages.jsx:268-302`): app glyph, title, breadcrumb metadata, excerpt, author/freshness. Clicking the row opens `source_url` (or shows "no link" when null).

**Auto-open.** When the registry transitions from 0 → 1 entries during a run and the user has never manually toggled the sources panel, surface a one-time toast: "3 sources cited — open Sources panel?" (low-noise; dismissible). This is the spec's "auto-open when there are sources/agents" behavior, scoped to what's possible without the full Workspace pane.

**Tests.**

- `SourcesPanel_renders_in_ordinal_order`
- `SourcesPanel_shows_unresolved_when_freshness_missing`
- `SourcesPanel_clicking_row_opens_source_url`
- `DetailsPanelHost_registers_sources_kind`

## Implementation order

A → B → C → E → D-stub. Everything except D-stub is true production code.

## Verification

- ai-backend pytest passes for the citation suites + the new Postgres adapter contract test + the new MCP projector tests.
- Frontend typecheck + build clean. New `SourcesPanel` test passes.
- Visual sanity: in dev mode (in-memory store), trigger a search-and-summarize prompt against any MCP server that returns a results-list shape; confirm Sources panel populates and chips render styled.
- Compliance: confirm `runtime_citations` rows write through `FieldCodec` v1; confirm RLS denies cross-org reads.

## Open questions

- **Per-MCP-tool inline chips.** The generic projector populates the Sources panel but not inline chips. We need a way for the assistant to learn the `[c<id>]` token for an MCP-derived source. Three options worth scoping:
  1. Inject a one-line synthetic system message after each tool result: _"For citation, refer to source `[c3]` (Notion: Aurora 4.0 Positioning v3)."_ — simple, but pollutes the model's context and may interfere with reasoning models.
  2. Append a small `_citation_tokens` field to the structured tool result with an instruction in the system prompt to use those tokens when summarizing. Cleaner but requires the model to follow a layered instruction.
  3. Per-MCP opt-in: tools that declare `cite_tokens=true` in their descriptor have their text fields rewritten in-place to interleave tokens. Requires per-tool buy-in but is most reliable.
     _Recommendation: ship (2) as a follow-up after we have telemetry on Sources-panel usage._

- **Sources panel for archived conversations.** Today the panel reads the live registry. Archived conversations (where the user opens an old chat) need `GET /v1/agent/conversations/{id}/sources` — that endpoint lands with W1.5 (subagent + sources discovery). For this PR the panel renders empty for archived conversations until then.
