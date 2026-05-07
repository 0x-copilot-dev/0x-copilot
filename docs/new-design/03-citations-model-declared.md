# PR 1.1-rev2 — Citations: model-declared, conversation-scoped pointers

> **Status:** Proposed · supersedes [`01-citations-live-registry.md`](./01-citations-live-registry.md) and [`02-citations-followups.md`](./02-citations-followups.md) · Owner: TBD · Target wave: W1
> **Scope:** `services/ai-backend` (event + persistence + stream filter) · `apps/frontend` (reducer + chip + provider) · `packages/api-types` (wire contract)
> **Reads alongside:** [`docs/new-design/0-OVERALL_PLAN.md`](./0-OVERALL_PLAN.md), `services/ai-backend/CLAUDE.md`, `apps/frontend/CLAUDE.md`.

## Why this supersedes PR 1.1

PR 1.1 shipped a tool-result extraction pipeline: `CitationProjector` walks every tool result, pattern-matches on a fixed set of shapes (Anthropic `content` blocks, generic `results` lists, single `resource` reads, top-level dict-lists), and registers any matches with a per-run `CitationLedger`.

In production this fails for the two most common citation surfaces:

1. **MCP servers that JSON-encode their data inside `TextContent.text`** (Linear, most Notion/Slack/Atlassian wrappers, almost every MCP server in the wild). The projector sees a `type="text"` block with no `url`/`title`/`source` siblings, and `_build()` returns `None`. Zero sources ingested. Linear's `list_issues` returning four issues produces an empty Sources tab.
2. **`langchain_community.tools.DuckDuckGoSearchResults` with `output_format="list"`** — version-dependent return shape (string vs. list), drops on `isinstance(result, dict)` check.

The deeper problem is structural, not patchable: **shape detection over arbitrary tool outputs cannot be complete.** Each new MCP server adds a shape we forgot. Each new tool adds a fallback we missed. The "for free" promise of the auto-projector forces a parallel, never-finished oracle to live next to a runtime that already has structured ground truth (the tool invocation log itself).

This redesign drops shape detection entirely. **The model declares its citations; the runtime resolves the declarations against the existing tool invocation log.** No tool result is ever parsed for sources.

## Implementation deltas vs. PR 1.1 (what changes)

| Layer                    | PR 1.1 (built)                                                                                   | This PRD (proposed)                                                                                                                                |
| ------------------------ | ------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Source extraction        | `CitationProjector` walks tool output shapes                                                     | **Deleted.** The model emits `[[N]]` markers; the runtime resolves them.                                                                           |
| Tool wrapper             | `CitationCapturingTool` + `CitationCapturingRegistry` wrap LangChain tools                       | **Deleted.** Tool dispatch is unwrapped.                                                                                                           |
| MCP middleware           | `CitationProjectingMcpMiddleware` calls projector on every MCP result                            | **Deleted.** MCP `call_tool` returns its result unchanged.                                                                                         |
| Per-run cache            | `CitationLedger` (per-run idempotency on `(connector, doc_id)`, ordinal allocation, persistence) | **Deleted.** Tool invocations already have unique IDs and conversation-scoped ordinals.                                                            |
| Persistence              | `runtime_citations` table (encrypted title/snippet, RLS, in-memory adapter only)                 | **Deleted.** Citations are derivable from `tool_invocations` + the resolved-citation event log.                                                    |
| Wire event               | `source_ingested` carrying `CitationSourceRef`                                                   | **Deleted.** Replaced by `citation_made` carrying a pointer (assistant message offset → tool invocation ordinal).                                  |
| Sealed snapshot          | `final_response.citations: CitationSourceRef[]`                                                  | Replaced by `final_response.cited_ordinals: number[]` — a small list of integers, no payload duplication.                                          |
| Inline token             | `[c<base36>]` (per-run, e.g. `[c1]`, `[c3]`, `[czh]`)                                            | `[[N]]` (conversation-scoped decimal int, stable across turns).                                                                                    |
| Tool invocation ordinals | Not exposed                                                                                      | **New.** `tool_invocations.conversation_ordinal` — monotonic per conversation, assigned at first persistence.                                      |
| FE registry              | `CitationRegistryByRun` map of `runId → citationId → CitationSourceRef`                          | Single conversation-scoped map: `conversation_ordinal → tool_invocation`. Already in the run-state reducer; no new map.                            |
| Anthropic adapter        | `anthropic_stream_adapter.py` — calls `cite()` on `citations_delta` blocks                       | **Trimmed.** Adapter normalizes `citations_delta` into `[[N]]` markers in the assembled text by looking up the relevant tool invocation's ordinal. |
| OpenAI adapter           | `openai_response_adapter.py` (deferred in PR 1.1) — drains `output_text.done.annotations`        | Same role, simpler — emit `[[N]]` substitutions.                                                                                                   |

Net code change: **~700 LOC deleted**, ~150 LOC added (stream filter + ordinal allocation + FE chip resolver). One fewer table. One fewer wire event type. One fewer reducer branch.

## Test results

Not implemented yet. PR 1.1 ledger + projector tests will be deleted; replacement tests are listed in §8.

---

## 1 · PRD

### 1.1 Problem

Atlas's product principle is **"every claim is a hyperlink to its source."** Without reliable inline chips and a populated Sources tab, every other right-rail surface (share-recipient view, per-connector usage attribution, "verify the agent's work") is blocked.

PR 1.1 attempted to deliver this via tool-result extraction. As shipped, it works for tool-emitted results that happen to match one of four hardcoded shapes, plus Anthropic native `citations_delta` (when the adapter is enabled). For the dominant production case — MCP servers returning JSON-encoded data in TextContent blocks — it produces zero sources. The Sources tab is empty for Linear, Notion MCP, most Slack MCP variants, and any future MCP server that follows the common community pattern.

A patch-on-top fix (add JSON parsing to the projector, add per-server adapters) does not scale: every MCP server, every internal API onboarded, every future shape requires a forever-growing central detector or a forever-growing adapter sprawl.

### 1.2 Goals

1. **Inline chips with the words.** When the assistant references a fact, the chip renders next to that fact — within ≤1 frame of the surrounding text token landing.
2. **Sources tab populates.** Every cited tool invocation appears in the Workspace pane → Sources tab, conversation-scoped, with click-to-evidence.
3. **Cross-turn citation works.** A turn-7 assistant message can cite a turn-3 tool result; the chip resolves; the Sources row surfaces.
4. **Survives compaction.** When prior turns are summarized to fit the context window, citation pointers continue to resolve.
5. **Stable, replayable.** Replaying a conversation reproduces the same chips, in the same locations, every time.
6. **Zero per-tool, per-MCP, per-API code.** Onboarding a new MCP server or internal API requires no citation work. The system is shape-agnostic by construction.
7. **Cross-provider.** Anthropic native citations, OpenAI Responses annotations, and prompted-marker fallback all funnel through the same wire contract.

### 1.3 Non-goals (this PR)

- Editing or annotating citations after the fact.
- Multimodal citations (Figma frames, Loom timestamps, Excel cells) — design's "future explorations".
- Per-citation analytics ("which source got clicked").
- Cross-conversation citation (sharing a thread into a new chat with citations carrying over) — explicitly out of scope; ordinals are conversation-local.
- Citation of memory entries (W4) — same mechanism via `[[mem:42]]` namespace, but lands with W4.
- Citation of user-pasted content — user input is not a "source"; out of scope.

### 1.4 Success criteria

- Linear `list_issues` ground truth check: a conversation that loads Linear MCP, runs `list_issues`, then asks "which is highest priority?" produces a Sources row referencing the `list_issues` tool invocation, accessible via an inline chip in the assistant's reply.
- Cross-turn check: a conversation that runs `web_search` in turn 2, then in turn 7 asks "what was that langchain blog post you found?" produces an inline chip in turn 7 resolving to turn 2's `web_search` invocation. **No new tool runs. No re-extraction. The Sources row already exists.**
- Compaction check: a conversation summarized at turn 12 still resolves chips for tool invocations from turns 1–6.
- Onboarding scale check: registering a brand-new MCP server requires zero citation-related code changes; running its tool produces a Sources row.
- `make test` passes; ai-backend full suite passes; frontend `typecheck`+`build` pass.
- Replaying a completed run via `replayRunEvents` produces the same chip set, in the same prose locations.

### 1.5 User stories

| #    | Persona                       | Story                                                                                                                                                                                                            |
| ---- | ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | End user (Sarah)              | I ask Atlas "summarize last week in #launch-aurora." Sources appear in the rail as the agent works. When prose lands, every claim has a numbered chip — I click any chip to verify within 2s.                    |
| US-2 | End user, long thread         | Twelve turns deep, I ask "what was that customer quote?" The agent doesn't re-search Slack — it answers from context, citing turn-3's tool result. The chip in turn-12's reply resolves to turn-3's Sources row. |
| US-3 | End user mid-run              | My SSE drops for 4s. After reconnect, chips and Sources are exactly where they were.                                                                                                                             |
| US-4 | Share recipient (W6)          | A chip whose tool I can't see shows "Source restricted" — but the chip still renders so I can see _that_ a citation existed.                                                                                     |
| US-5 | Engineer onboarding a new MCP | I register `confluence-mcp`. Without writing any citation code, calls to its tools produce Sources rows.                                                                                                         |
| US-6 | Compliance reviewer           | I open a 6-month-old conversation. Chips still resolve. The evidence is in the persisted `tool_invocations` log, not a denormalized snapshot.                                                                    |

---

## 2 · Wire contract

One new event type, one inline token convention, one new column on `tool_invocations`. No new payload duplication: citations are pointers, not snapshots.

### 2.1 Inline token

Model output contains opaque tokens of the form `[[N]]` where `N` is a positive decimal integer matching `tool_invocations.conversation_ordinal` for the active conversation. Frontend matches `/\[\[(\d+)\]\]/g`. Unknown ordinals render as a muted `[?]` placeholder (defensive against hallucinated numbers).

The token format is deliberately:

- **Decimal integer**, not base36 — readable in logs, readable to the model, no encoding step.
- **Double-bracket**, not single — `[1]` collides with markdown footnote syntax and prose-internal numbering ("step [1] is..."). `[[1]]` is rare in natural prose and rare in code.
- **Conversation-scoped**, not run-scoped — the same ordinal resolves whether it appears in turn 2 or turn 12.

### 2.2 New event: `citation_made`

```ts
// packages/api-types/src/index.ts
export interface CitationLink {
  conversation_ordinal: number; // resolves to tool_invocations row
  message_id: string; // assistant message containing the chip
  prose_offset: number; // 0-based char offset of "[[" in the assembled text
  prose_length: number; // length of the matched token (e.g. "[[12]]".length === 6)
  source_tool_call_id: string; // denormalized for FE convenience; same as tool_invocations.tool_call_id
}

export interface RuntimeCitationMadeEvent extends RuntimeEventEnvelopeBase {
  event_type: "citation_made";
  payload: { link: CitationLink };
}
```

The presentation projector emits `activity_kind=tool` for these (they ride with the tool that surfaced the source) but **without** generating a separate timeline row — `citation_made` is a registration event, not a visible activity. The visible work is the original `tool_invocation`.

### 2.3 `final_response` augmentation

```ts
export interface RuntimeFinalResponseEvent extends RuntimeEventEnvelopeBase {
  event_type: "final_response";
  payload: {
    text: string;
    cited_ordinals: number[]; // sealed list of conversation_ordinals referenced in `text`, in first-occurrence order
  };
}
```

`cited_ordinals` replaces PR 1.1's `citations: CitationSourceRef[]`. We carry **integers, not payloads** — the source detail lives in `tool_invocations` and is fetched on demand. This shrinks the wire by ~95% for citation-heavy turns and removes a denormalization risk (FE rendering stale title/snippet that diverged from the source).

---

## 3 · Architecture

### 3.1 Conversation-scoped tool invocation ordinals

The single new persistence concept. Every `tool_invocations` row gains a `conversation_ordinal INTEGER NOT NULL` column, allocated monotonically per conversation at insert time:

```sql
ALTER TABLE tool_invocations
  ADD COLUMN conversation_ordinal INTEGER NOT NULL;

CREATE UNIQUE INDEX tool_invocations_conv_ordinal_uk
  ON tool_invocations (conversation_id, conversation_ordinal);
```

Allocation logic lives **inside** the persistence repo (per `services/ai-backend/CLAUDE.md`: "Keep production helper behavior **inside** classes"):

```python
class ToolInvocationRepository:
    async def append(self, *, conversation_id: UUID, ...) -> ToolInvocationRecord:
        async with self._tx() as tx:
            ordinal = await tx.fetchval(
                "SELECT COALESCE(MAX(conversation_ordinal), 0) + 1 "
                "FROM tool_invocations WHERE conversation_id = $1 FOR UPDATE",
                conversation_id,
            )
            return await tx.insert(... conversation_ordinal=ordinal ...)
```

The `FOR UPDATE` lock against the conversation row ensures ordinal monotonicity under concurrent runs (rare — runs within one conversation are serialized today, but this future-proofs for branching).

For the in-memory adapter, the same logic uses an asyncio lock per conversation.

### 3.2 Model context: ordinals are visible to the model

When the runtime serializes a tool result into the message stream the model sees, it prepends a single line:

```
[Tool call #47 — linear.list_issues — cite as [[47]] when referencing this result.]
{ ... result payload, unchanged ... }
```

This is the **only** instruction the model needs. It's appended by the message assembler ([`agent_runtime/api/`](services/ai-backend/src/agent_runtime/api/) — the presentation/service layer), not by individual tools. Every tool result, regardless of source (local, MCP, future internal API), gets the prefix.

The system prompt gains one sentence:

> "When grounding any factual claim in a prior tool result — including from earlier turns — append `[[N]]` immediately after the claim, where N is the tool call number shown in that result's prefix."

That is the entire surface the model needs to learn. Strong instruction-followers (Claude 3.5+, GPT-4+, Llama 3.3 70B+) follow this reliably; the eval at §8.4 enforces a ≥80% retention threshold.

### 3.3 Stream filter: parse `[[N]]` from `model_delta`

A small, classes-based filter watches `model_delta` events as they fire:

```python
# services/ai-backend/src/agent_runtime/capabilities/citation_resolver.py

class CitationResolver:
    """Per-run filter — resolves [[N]] markers in streamed model output."""

    _CITATION_PATTERN = re.compile(r"\[\[(\d+)\]\]")

    class _State:
        # We hold a small rolling buffer per assistant message so a token like
        # "[[" arriving in one delta and "47]]" in the next still resolves.
        ROLLING_BUFFER_MAX = 16

    def __init__(self, *, run, conversation_id, repo, producer, source) -> None:
        self._run = run
        self._conversation_id = conversation_id
        self._repo = repo
        self._producer = producer
        self._source = source
        self._buffer = ""
        self._committed_offset = 0
        self._seen_ordinals: set[int] = set()

    async def observe_delta(self, *, message_id: str, delta_text: str) -> None:
        self._buffer += delta_text
        for match in self._CITATION_PATTERN.finditer(self._buffer):
            ordinal = int(match.group(1))
            invocation = await self._repo.find_by_ordinal(
                conversation_id=self._conversation_id,
                conversation_ordinal=ordinal,
            )
            if invocation is None:
                continue  # hallucinated ordinal; FE will render [?]
            await self._producer.append_api_event(
                run=self._run,
                source=self._source,
                event_type=RuntimeApiEventType.CITATION_MADE,
                payload={
                    "link": {
                        "conversation_ordinal": ordinal,
                        "message_id": message_id,
                        "prose_offset": self._committed_offset + match.start(),
                        "prose_length": match.end() - match.start(),
                        "source_tool_call_id": invocation.tool_call_id,
                    },
                },
            )
            self._seen_ordinals.add(ordinal)
        # Trim the buffer past the last potential partial-token boundary.
        ...

    def sealed_ordinals(self) -> list[int]:
        return sorted(self._seen_ordinals)
```

The stream filter:

- Holds a small rolling buffer to handle tokens split across deltas (`[[` in one delta, `47]]` in the next).
- Emits `citation_made` once per resolved marker. Idempotency on `(conversation_ordinal, message_id, prose_offset)` so re-streamed deltas don't duplicate.
- Returns the set of resolved ordinals at end-of-turn for the `final_response.cited_ordinals` field.

The filter binds via a ContextVar mirroring the existing pattern (PR 1.1's `CITATION_LEDGER_CTX` shape), bound by the worker at run start. No new injection plumbing.

### 3.4 Three production paths, one resolution mechanism

```
┌──────────────────────────────┐
│  Tool returns docs           │
│  (any tool, any MCP, any API)│  ──────► result returned UNCHANGED to the model.
│                              │           runtime prepends "[Cite as [[N]]]" line
└──────────────────────────────┘           via the message assembler.

┌──────────────────────────────┐
│  Anthropic citations_delta   │  ──────► adapter substitutes [[N]] tokens into
│  blocks                      │           the text delta. N looked up by mapping
└──────────────────────────────┘           anthropic doc_id → tool invocation
                                           that produced it.

┌──────────────────────────────┐
│  OpenAI output_text.done     │  ──────► adapter rewrites assembled text once
│  annotations                 │           at end-of-turn, substituting [[N]] for
└──────────────────────────────┘           each annotation's source.

                  │
                  ▼
        Model emits prose with [[N]] markers
                  │
                  ▼
   ┌──────────────────────────────────┐
   │ CitationResolver (per-run)       │
   │  - watches model_delta           │
   │  - parses [[N]]                  │
   │  - resolves to tool_invocations  │
   │  - emits citation_made events    │
   └──────────────────────────────────┘
                  │
                  ▼
       Frontend chip renderer +
       Sources tab population
```

#### 3.4.1 Tool / MCP / internal API path (the universal path)

Every tool result is wrapped by the message assembler in a one-line prefix that includes the result's `conversation_ordinal`. The result body is **unchanged** — the model sees the structured data exactly as the tool returned it, plus a citation hint.

There is **no per-tool, per-MCP, per-API code**. The message assembler is one place; it processes every tool result regardless of origin.

#### 3.4.2 Anthropic native passthrough

The existing [`anthropic_stream_adapter.py`](services/ai-backend/src/agent_runtime/execution/providers/anthropic_stream_adapter.py) is trimmed: instead of calling `cite()` and registering a separate citation row, it looks up the tool invocation that produced the cited document (Anthropic's `citations_delta` carries `document_index` and the document is the result of a recent tool call) and substitutes `[[N]]` in the corresponding text delta. Same mechanism as the universal path; no separate citation registry.

#### 3.4.3 OpenAI Responses end-of-turn

[`openai_response_adapter.py`](services/ai-backend/src/agent_runtime/execution/providers/openai_response_adapter.py) drains `output_text.done.annotations`, maps each annotation to a tool invocation ordinal, rewrites the assembled text once. Same mechanism.

### 3.5 Persistence

```sql
-- migrations/0017_tool_invocation_ordinals.sql (number subject to slot collision)
ALTER TABLE tool_invocations
  ADD COLUMN conversation_ordinal INTEGER NOT NULL;
CREATE UNIQUE INDEX tool_invocations_conv_ordinal_uk
  ON tool_invocations (conversation_id, conversation_ordinal);

-- migrations/0018_drop_runtime_citations.sql
DROP TABLE runtime_citations;
DROP INDEX IF EXISTS runtime_citations_run_source_uk;
DROP INDEX IF EXISTS runtime_citations_conv_idx;
```

Citations themselves (the `citation_made` events) live in the existing event log alongside every other run event — same RLS, same retention sweep, same encryption posture, same SSE replay. No new table.

The Sources tab read path is one query:

```sql
-- "tool invocations cited at least once in this conversation, ordered by recency of citation"
SELECT ti.*
FROM tool_invocations ti
WHERE ti.conversation_id = $1
  AND EXISTS (
    SELECT 1 FROM runtime_events re
    WHERE re.conversation_id = $1
      AND re.event_type = 'citation_made'
      AND (re.payload->'link'->>'conversation_ordinal')::int = ti.conversation_ordinal
  )
ORDER BY ti.created_at DESC;
```

A toggle ("Show all tool invocations, not just cited ones") drops the `EXISTS` clause.

### 3.6 Frontend

#### 3.6.1 Reducer

The existing tool invocation reducer ([`apps/frontend/src/features/chat/chatModel/`](apps/frontend/src/features/chat/chatModel/)) gains the `conversation_ordinal` field on each `ToolInvocation` record. One new branch in the run-state reducer for `citation_made`:

```ts
case "citation_made": {
  const link = event.payload.link;
  return {
    ...state,
    citationsByMessage: upsert(state.citationsByMessage, link.message_id, link),
  };
}
```

`citationsByMessage: Map<messageId, CitationLink[]>` — keyed by assistant message, sorted by `prose_offset`. That's the entire FE state addition.

#### 3.6.2 Streamdown remark plugin

The existing `[c<id>]` plugin ([`citationRemarkPlugin.ts`](apps/frontend/src/features/chat/components/markdown/citationRemarkPlugin.ts)) is renamed and repointed: `\[\[(\d+)\]\]` → `<CitationChip ordinal={N} />`. The chip resolves by:

1. Reading `citationsByMessage` for the current assistant message + `prose_offset`.
2. Looking up the tool invocation by `(conversation_id, conversation_ordinal=N)` in the existing tool invocation registry.
3. Rendering ordinal + tool's connector glyph + tooltip.

#### 3.6.3 Sources tab

[`SourcesTab.tsx`](apps/frontend/src/features/chat/components/workspace/SourcesTab.tsx) reads cited tool invocations from the run-state reducer, conversation-scoped. No separate citations registry, no separate fetch endpoint. Each row shows the tool's name, args, output preview, status — clicking opens the full tool invocation card.

When an inline chip is clicked, the tab scrolls the matching row into view and flashes the accent border. Existing `useScrollIntoView` helper.

#### 3.6.4 Pane auto-open

When `citationsByMessage` crosses 0 → 1 in the active conversation and the user hasn't manually closed the pane, open it. Same UX as PR 1.1.

### 3.7 Cross-provider behavior matrix

| Provider                                                 | When citations land                                       | UX impact                                                                                                           |
| -------------------------------------------------------- | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| **Universal path** (any tool, any MCP, any internal API) | Live, as the model writes `[[N]]` next to grounded claims | Sources tab populates as chips appear inline; ordinals stable across turns                                          |
| **Anthropic Claude with `citations` enabled**            | Live, interleaved with `model_delta`                      | Adapter substitutes `[[N]]`; identical UX to universal path                                                         |
| **OpenAI Responses**                                     | Burst at end-of-turn                                      | Adapter substitutes `[[N]]` once; chips appear as final text settles                                                |
| **Self-hosted / OSS models**                             | Live, model retention dependent                           | Strong instruction-followers retain `[[N]]`; weaker models degrade to empty Sources tab. No code change to recover. |

---

## 4 · The cross-turn property (the design's central feature)

This is what tool-result extraction can never deliver and what model-declared pointers handle for free.

### 4.1 The mechanism

`conversation_ordinal` is allocated at tool invocation persistence and **never changes** for the lifetime of the conversation. When the runtime assembles the model's context for turn T:

- Every prior tool result still in context retains its original `[Tool call #47 — ... cite as [[47]]]` prefix.
- The model can cite `[[47]]` from turn T even if call #47 ran in turn T-k.
- The `CitationResolver` looks up `tool_invocations.where(conversation_id, conversation_ordinal=47)` — one indexed query, resolves regardless of how many turns separate the citation from the source.

### 4.2 What this enables that PR 1.1 cannot

- **No tool re-runs for "remind me what we found earlier."** The model answers from context; the chip resolves to the original tool's row.
- **One Sources row, two prose chips.** A source cited in turn 2 and turn 7 produces two `citation_made` events pointing to the same tool invocation. The Sources tab dedupes naturally.
- **Compaction-safe.** When older turns are summarized to fit the window, the summarizer is instructed: _"Preserve `[[N]]` markers verbatim."_ Markers survive; the underlying tool invocation row stays in the database; resolution still works. PR 1.1's per-run registry has no equivalent for this case.
- **Branch-safe.** If the user edits turn 4 and re-runs, the new branch's tool calls continue the conversation's ordinal sequence (allocator is conversation-scoped, not branch-scoped). Ordinals from unaffected turns still resolve. The Sources tab filters by `branch_id` for display, identical to how messages filter today.

### 4.3 What about user-pasted content / memory entries?

Out of scope for v1. The same marker mechanism extends naturally:

- **User-pasted content**: not a "source" in citation semantics; the user is citing themselves. No marker.
- **Memory entries (W4)**: when loaded into context, prefix with `[Memory entry — cite as [[mem:42]]]`. Resolver dispatches by namespace (`mem:`/no-prefix). Lands with W4.

Single marker grammar; namespace introduces are additive.

---

## 5 · DRY / re-use audit

| Need                                            | Re-used                                                                       | Why this beats a fork                                                               |
| ----------------------------------------------- | ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Per-conversation monotonic counter              | New `conversation_ordinal` column + `FOR UPDATE` lock on the conversation row | One column, one index. No new sequence, no new generator service.                   |
| Event ordering, sequence_no, replay, SSE resume | `RuntimeEventProducer.append_api_event`                                       | Same wire guarantees as every other event.                                          |
| Tool invocation persistence                     | Existing `tool_invocations` table                                             | Citations are derived from this; no parallel store.                                 |
| Inline rendering                                | Existing Streamdown remark plugin slot                                        | Reuse the existing `[c<id>]` plugin path; just change the regex and the resolution. |
| RLS, audit, retention                           | `tool_invocations` already RLS'd and retention-managed                        | No new compliance surface.                                                          |
| Tool result message assembly                    | Existing message assembler in `agent_runtime/api/`                            | One added line per tool result — single change point for the universal path.        |
| Frontend event routing                          | `applyRuntimeEvent` reducer                                                   | One new `case` mirrors every other event.                                           |

**Things we explicitly do not introduce:**

- A new "citation service" module — there is no service.
- A new persistence table — citations live in the event log; sources live in `tool_invocations`.
- A new per-tool wrapper — tool dispatch is unwrapped.
- A separate per-run cache — the database is the cache.
- Per-MCP-server adapters — MCP middleware no longer has a citation responsibility.

---

## 6 · Code surface inventory

Approximate sizes are upper bounds — pessimistic guesses for code review.

### 6.1 `packages/api-types`

| File           | Change                                                                                                                                                                  | Est. LoC         |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- |
| `src/index.ts` | Replace `CitationSourceRef` / `RuntimeSourceIngestedEvent` with `CitationLink` / `RuntimeCitationMadeEvent`; augment `RuntimeFinalResponseEvent.payload.cited_ordinals` | ±0 (net neutral) |

### 6.2 `services/ai-backend`

| File                                                                              | Change                                                                                               | Est. LoC    |
| --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ----------- |
| `migrations/0017_tool_invocation_ordinals.sql` (new) + rollback                   | add column, unique index                                                                             | +30         |
| `migrations/0018_drop_runtime_citations.sql` (new) + rollback                     | drop PR 1.1 table                                                                                    | +20         |
| `src/agent_runtime/persistence/records/citations.py`                              | **deleted**                                                                                          | -70         |
| `src/agent_runtime/persistence/ports.py`                                          | drop `CitationStorePort`                                                                             | -20         |
| `src/runtime_adapters/in_memory/citation_store.py`                                | **deleted**                                                                                          | -40         |
| `src/runtime_adapters/postgres/citation_repo.py` (was deferred)                   | **never written**                                                                                    | 0           |
| `src/agent_runtime/capabilities/citations.py`                                     | **deleted** (`CitationLedger`, `SourceRef`, `CITATION_LEDGER_CTX`)                                   | -224        |
| `src/agent_runtime/capabilities/citation_projection.py`                           | **deleted**                                                                                          | -240        |
| `src/agent_runtime/capabilities/citation_capturing_tool.py`                       | **deleted**                                                                                          | -116        |
| `src/agent_runtime/capabilities/mcp/middleware/cite_mcp.py`                       | **deleted**                                                                                          | -37         |
| `src/agent_runtime/capabilities/mcp/middleware/call_tool.py`                      | drop `CitationProjectingMcpMiddleware.project` call                                                  | -10         |
| `src/agent_runtime/capabilities/citation_resolver.py` (new)                       | `CitationResolver` (stream filter + ContextVar)                                                      | +180        |
| `src/agent_runtime/persistence/repositories/tool_invocations.py`                  | add `find_by_ordinal`, `conversation_ordinal` allocation in `append`                                 | +40         |
| `src/agent_runtime/api/messages.py` (or wherever the assembler lives)             | prepend `[Tool call #N — ... cite as [[N]]]` line to every tool result                               | +30         |
| `src/agent_runtime/execution/deep_agent_builder.py`                               | system prompt sentence                                                                               | +5          |
| `src/runtime_api/schemas/common.py`                                               | enum case `CITATION_MADE`, drop `SOURCE_INGESTED`                                                    | ±0          |
| `src/runtime_api/schemas/events.py`                                               | projector branch for `citation_made`                                                                 | +30         |
| `src/agent_runtime/api/constants.py`                                              | `Messages.Event.CITATION_MADE`                                                                       | +10         |
| `src/runtime_worker/handlers/run.py`                                              | replace `_bind_citation_ledger` with `_bind_citation_resolver`; seal `final_response.cited_ordinals` | -10 +30     |
| `src/agent_runtime/execution/providers/anthropic_stream_adapter.py`               | rewrite to substitute `[[N]]` rather than call `cite()`                                              | -30 +40     |
| `src/agent_runtime/execution/providers/openai_response_adapter.py` (was deferred) | implement `[[N]]` substitution                                                                       | +50         |
| `tests/unit/agent_runtime/test_citation_resolver.py` (new)                        | resolver unit tests                                                                                  | +250        |
| `tests/unit/runtime_api/test_runtime_event_timeline.py`                           | extend timeline assertions to cover `citation_made` ordering + replay                                | +60         |
| `tests/integration/test_cross_turn_citation.py` (new)                             | cross-turn + compaction tests                                                                        | +120        |
| `docs/use-cases/14-citations-during-streaming.md`                                 | update to new model                                                                                  | +30 (delta) |

### 6.3 `apps/frontend`

| File                                                              | Change                                                                            | Est. LoC                  |
| ----------------------------------------------------------------- | --------------------------------------------------------------------------------- | ------------------------- |
| `src/features/chat/chatModel/citationsRegistry.ts`                | **deleted**                                                                       | -60                       |
| `src/features/chat/chatModel/citationReducer.ts`                  | replace with `citationLinkReducer.ts` keyed by message_id                         | -100 +60                  |
| `src/features/chat/components/citations/citationsContext.tsx`     | provider reads `citationsByMessage` + tool invocation registry; signature changes | -25 +30                   |
| `src/features/chat/components/citations/CitationChip.tsx`         | resolve by `(conversation_ordinal)` against tool invocation registry              | -50 +30                   |
| `src/features/chat/components/markdown/citationRemarkPlugin.ts`   | regex `/\[\[(\d+)\]\]/g`; emit `<CitationChip ordinal={N} />` mdast node          | -40 +35                   |
| `src/features/chat/components/workspace/SourcesTab.tsx`           | bind to `citationsByMessage` + tool invocation registry                           | (covered by W3.2 surface) |
| `__tests__/citationRemarkPlugin.test.ts`, `CitationChip.test.tsx` | rewrite for new token + resolution                                                | -150 +180                 |

**Totals:** ai-backend net **−~800 LOC + ~900 LOC = +~100 LOC** (and one fewer table). Frontend net **−~425 LOC + ~335 LOC = −~90 LOC**. Contracts net 0.

---

## 7 · Edge cases

| Case                                                      | Resolution                                                                                                                                                                                                                                                                |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Same source cited twice in one assistant message          | Two `citation_made` events with different `prose_offset`s; FE renders two chips. Sources tab dedupes by `(conversation_id, conversation_ordinal)`.                                                                                                                        |
| Same source cited in two turns of the same conversation   | Two `citation_made` events; same `conversation_ordinal`; one Sources row.                                                                                                                                                                                                 |
| Model fabricates `[[99]]` we never registered             | `find_by_ordinal` returns None; resolver skips; FE renders muted `[?]`. Logged + counted; never raises.                                                                                                                                                                   |
| Partial token mid-stream (`[[`, `[[4`)                    | Rolling buffer in resolver; matches only complete `[[N]]` tokens; partial sequences pass through as plain text until closed.                                                                                                                                              |
| Model cites `[[3]]` where call #3 was in a sibling branch | Resolver filters by `branch_id` matching the active branch; ordinals from inactive branches return None. (Ordinals are unique per `conversation_id` — branches share the sequence — but Sources tab respects branch filter for display.)                                  |
| SSE drops mid-run                                         | Reconnect via `?after_sequence=N`; `citation_made` events stream through normal replay; FE state rebuilt deterministically.                                                                                                                                               |
| Run cancelled before `final_response`                     | `citation_made` events persist; UI shows partial chips. `final_response.cited_ordinals` omitted (event never fires). Replay still works.                                                                                                                                  |
| Tool returns 50 docs (e.g. large web search)              | Each doc is part of the same tool invocation. Sources tab shows one row for the invocation; expanding the row shows all 50 results. The model only cites `[[N]]` for the tool call as a whole — sub-citation (chip per result) is out of scope for v1.                    |
| MCP server returns no usable URL                          | Sources row still renders — title = tool name, body = the result preview. Clicking shows the full tool input/output. The user can verify even without a clickable URL.                                                                                                    |
| Compaction summary loses a marker                         | Defensive system prompt to summarizer + a post-pass that scans the original turn for `[[N]]` and ensures preservation. If a marker is lost, the citation-from-compacted-turn case degrades gracefully — the model can't cite what it can't see, and the UI shows no chip. |
| Share recipient (W6) without tool access                  | Share-resolver substitutes `tool_name="Restricted"`, `output=null` for tool invocations the recipient can't see. Chip still renders ("Source restricted"); the row in Sources is muted.                                                                                   |
| Two concurrent runs in one conversation                   | `FOR UPDATE` lock on the conversation row during `conversation_ordinal` allocation serializes the assignment. (Today runs in one conversation are sequential, but this future-proofs.)                                                                                    |

---

## 8 · Test plan

### 8.1 Unit — ai-backend (~14 cases)

- `resolver_emits_event_on_complete_token`
- `resolver_handles_token_split_across_deltas`
- `resolver_skips_unknown_ordinals`
- `resolver_idempotent_on_redelivered_delta`
- `resolver_buffer_trim_after_match`
- `tool_invocation_repo_allocates_monotonic_ordinals`
- `tool_invocation_repo_unique_per_conversation`
- `tool_invocation_repo_concurrent_runs_serialize_via_for_update`
- `message_assembler_prepends_citation_hint`
- `message_assembler_unchanged_for_non_tool_messages`
- `event_projector_emits_activity_kind_tool_for_citation_made`
- `final_response_seals_cited_ordinals_in_first_occurrence_order`
- `anthropic_adapter_substitutes_double_bracket_token`
- `openai_adapter_rewrites_text_with_double_bracket_tokens`

### 8.2 Frontend (~10 cases)

- `remark_plugin_replaces_closed_token_with_chip_node`
- `remark_plugin_holds_partial_token_during_stream`
- `remark_plugin_handles_multiple_tokens_in_one_delta`
- `chip_resolves_against_tool_invocation_registry`
- `unknown_ordinal_renders_placeholder`
- `reducer_indexes_citation_links_by_message_id`
- `reducer_idempotent_on_redelivered_event`
- `pane_auto_opens_on_first_citation_link`
- `pane_stays_closed_for_tool_free_chat`
- `clicking_chip_scrolls_sources_row`

### 8.3 Integration

- **Cross-turn citation** (`tests/integration/test_cross_turn_citation.py`): conversation runs `web_search` in turn 2, asks "what was that langchain blog?" in turn 7 with no new tool call, asserts `citation_made` event in turn 7 resolves to turn-2's invocation.
- **Compaction survival**: build a 12-turn conversation; trigger context compaction; turn 13 cites `[[N]]` for a turn-2 tool; assert chip resolves.
- **Branch isolation**: edit turn 4, re-run; turn-5-of-original-branch's ordinal still resolves on the original branch but is hidden on the new branch.
- **MCP onboarding**: register a fresh MCP server with no citation-related code; assert tool invocations gain ordinals; assert chips resolve.
- **SSE reconnect**: kill SSE after first `citation_made`, reconnect with `?after_sequence=N`, assert chip and Sources state identical.
- **Replay**: load a completed conversation via `replayRunEvents`, assert chip set + Sources rows identical to live.

### 8.4 Token-retention eval

`tests/eval/test_citation_marker_retention.py`: run each configured provider against a fixture prompt + canned tool result, assert ≥80% of expected `[[N]]` markers retained in the model output. Models below threshold flagged in the model-catalog UI as "inline citations: best-effort."

### 8.5 Compliance check

- `tool_invocations.conversation_ordinal` is in the same retention sweep as the rest of `tool_invocations` (existing migration).
- RLS denies cross-org reads — tested by setting `app.current_org_id` to another org and asserting empty result.
- `citation_made` events are encrypted at rest via the existing event payload codec — tested via existing event-codec round-trip pattern.
- SIEM exporter picks up no new event categories — `citation_made` is informational, not security-relevant.

---

## 9 · Rollout

1. **Feature-flag-free.** Unlike PR 1.1's never-actually-wired `RUNTIME_CITATIONS_ENABLED`, this rolls out as a single behavior change. Either the system understands `[[N]]` or it doesn't; there is no half state.
2. **Phase 1 — backend wire (this PR).** Migration adds `conversation_ordinal`. Resolver lands. Message assembler adds the citation hint. PR 1.1 components deleted in the same PR. System prompt updated. Provider adapters trimmed.
3. **Phase 2 — frontend (this PR or immediate follow-up).** Reducer + plugin + chip + Sources tab repointed. Old `c<id>`-resolving code deleted.
4. **Phase 3 — drop the PR 1.1 table.** A separate small migration. Done after Phase 1+2 ship and one sweep confirms no consumer is reading the old table.
5. **Phase 4 — eval gate.** Token-retention eval runs in CI; any provider below threshold flagged in `model-catalog`.
6. **Backfill: not required.** Prior conversations have no `conversation_ordinal` for tool invocations. A one-time backfill (`UPDATE tool_invocations SET conversation_ordinal = row_number() OVER (PARTITION BY conversation_id ORDER BY created_at)`) populates them. Old conversations gain working chips on next turn.

---

## 10 · Open questions (non-blocking)

- **Sub-citation for high-fanout tools** (one `web_search` returning 10 distinct URLs): v1 treats the whole tool invocation as one source. If users want chip-per-result, add `[[N.M]]` syntax later — purely additive, ordinals unchanged.
- **Memory citation namespace** (`[[mem:42]]`): land with W4. Today's resolver dispatches only on numeric ordinals.
- **Retention for `citation_made` events**: same as `tool_invocations` — they're cheap pointers, no need for a shorter retention window.
- **Citation budget per turn**: cap at ~50 distinct `[[N]]`s per turn? Defer until we observe flooding; the model self-regulates well in practice.
- **Markdown blocks containing tokens** (e.g. inside fenced code or links) — by spec we don't render chips inside ` ```code` or `<a>`; the remark plugin walks text nodes only and skips inside `code`/`link`.

---

## 11 · References

- [`docs/new-design/01-citations-live-registry.md`](./01-citations-live-registry.md) — the PR 1.1 design this supersedes.
- [`docs/new-design/02-citations-followups.md`](./02-citations-followups.md) — known limitations of PR 1.1, all addressed by this redesign.
- Atlas Design Doc — §"Citations as superscript chips" decision; §"Citations are first-class" principle.
- [Anthropic Citations API](https://docs.anthropic.com/en/docs/build-with-claude/citations) — `citations_delta` streaming contract.
- [OpenAI Responses streaming events](https://platform.openai.com/docs/api-reference/responses-streaming) — annotations on `output_text.done`.
- `apps/frontend/CLAUDE.md` — Streamdown rendering rule; activity_kind/display_title/summary/status projection rule.
- `services/ai-backend/CLAUDE.md` — module boundaries; production helpers stay inside classes.
- `services/ai-backend/docs/CLAUDE.md` — spec-first workflow.

---

## 12 · The principle in one line

> **A citation is a pointer, not a snapshot.** The model declares pointers; the runtime resolves them against an invocation log it already keeps. No tool result is ever parsed for sources.
