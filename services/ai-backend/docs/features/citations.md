# Citations

How source references flow from tool results and provider grounding through ordinal
allocation, deduplication, and into the model's final response as inline `[[N]]` markers.

See also:

- [features/tool-calling.md](tool-calling.md) — MCP middleware that projects citations
- [diagrams/flows/f5-citations.puml](../architecture/diagrams/flows/f5-citations.puml)

---

## What it does

When a tool call or provider grounding returns source documents (a Linear ticket, a Notion
page, a web search result), the system:

1. Ingests the source into the per-run `CitationLedger` (idempotent per `connector+doc_id`).
2. Allocates a conversation-scoped ordinal (monotonic integer across all turns and subagents).
3. Projects `[[N]]` markers into the tool result text that the model sees.
4. When the model writes `[[N]]` in its response text, the `CitationResolver` detects it
   and emits a `citation_made` event tying ordinal N to the underlying tool call.
5. The final `FINAL_RESPONSE` event carries a sealed citation list.

---

## Key modules

| File                                                       | Role                                                                                |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `agent_runtime/capabilities/citations.py`                  | `CitationLedger` — per-run idempotent registry; single entry point for all paths    |
| `agent_runtime/capabilities/citation_resolver.py`          | `CitationResolver` — watches model text for `[[N]]` markers                         |
| `agent_runtime/capabilities/citation_projection.py`        | `CitationProjector` — shared shape extractor: maps tool result dicts to `SourceRef` |
| `agent_runtime/capabilities/conversation_ordinals.py`      | `ConversationOrdinalAllocator` — allocates conversation-scoped ordinals             |
| `agent_runtime/capabilities/mcp/middleware/cite_mcp.py`    | `CitationProjectingMcpMiddleware` — MCP-side alias for `CitationProjector`          |
| `agent_runtime/execution/providers/citation_pipeline.py`   | `CitationStreamPipeline` — intercepts provider grounding/citation chunks            |
| `agent_runtime/execution/providers/citation_extraction.py` | Provider-specific citation extractors (Anthropic, OpenAI Responses, Gemini)         |
| `agent_runtime/persistence/ports.py`                       | `CitationStorePort`, `ConversationToolOrdinalStorePort`, `SourceStorePort`          |

---

## `CitationLedger` — the single seam

`agent_runtime/capabilities/citations.py`

All citation paths — MCP middleware, provider adapters, subagent tools — funnel through
`CitationLedger.register()` or `CitationLedger.register_many()`. No caller is allowed
to write to `CitationStorePort` directly.

```python
class SourceRef(RuntimeContract):
    source_connector: str       # e.g. "linear", "notion", "web"
    source_doc_id: str          # stable id within connector namespace
    title: str
    source_url: str | None
    snippet: str | None
    source_tool_call_id: str | None
```

Idempotency key: `(run_id, connector, doc_id)`. Re-registering the same source on the
same run returns the existing ordinal without emitting a duplicate event.

Per-run cap: `_Limits.PER_RUN_MAX = 50` sources. Sources beyond the cap are silently
dropped — citations are decoration, not required for correctness.

The `CitationLedger` instance is bound per-run via a `ContextVar`. Tools that run
outside the worker context (rare) get `None` from the contextvar; `cite()` returns
an empty string.

---

## Ordinal format

`[c<base36(ordinal)>]` — e.g. `[c1]`, `[c2]`, `[czh]`.

Conversation-scoped (not run-scoped): the same ordinal namespace is shared across all
turns in a conversation and across all subagents spawned by that conversation. This
ensures that `[[N]]` in turn 3 refers to the same source as `[[N]]` in turn 7.

---

## `ConversationOrdinalAllocator`

`agent_runtime/capabilities/conversation_ordinals.py`

Persists ordinal bindings via `ConversationToolOrdinalStorePort.record()`. The record
is idempotent on `tool_call_id` — if a worker crashes and restarts, re-registering the
same tool call returns the same ordinal. Subagents share the same allocator instance
(passed through `AgentRuntimeContext`).

---

## `CitationResolver`

`agent_runtime/capabilities/citation_resolver.py`

Watches streamed assistant text for `[[N]]` patterns (where `N` is a conversation
ordinal). For each newly-observed marker:

- Emits a `citation_made` event with the `CitationLink` payload tying the prose location
  to the underlying tool call.
- Idempotent on `(prose_offset, ordinal)` — re-deliveries of the same delta do not
  duplicate the event.
- Hallucinated ordinals (model writes `[[99]]` for an unallocated ordinal): event is
  still emitted with `source_tool_call_id` left empty. The frontend renders a muted placeholder.

The resolver does **not** rewrite model output text — `[[N]]` stays as-is in the persisted
assistant message. The frontend remark plugin replaces it with a chip at render time.

---

## Provider grounding (Gemini / OpenAI web search)

`agent_runtime/execution/providers/citation_pipeline.py`

`CitationStreamPipeline` intercepts provider-native citation metadata from the stream:

- **Anthropic**: `citations_delta` blocks in the chunk.
- **OpenAI Responses**: `output_text.done` annotations.
- **Gemini**: grounding metadata attached to the final chunk.

For each extracted source URL/title/snippet, it calls `CitationLedger.register()` so
the source enters the same namespace as MCP-sourced citations. Provider grounding
citations are assigned `connector="web"`.

---

## Subagent citation inheritance

Subagents inherit the **same** `CitationLedger` and `ConversationOrdinalAllocator`
as their parent run. When a subagent calls a tool, citations are registered in the
parent conversation's namespace with the same ordinals. There is no per-subagent
citation registry — the shared registry is the invariant.

---

## Sources tab (workspace pane)

`SourceStorePort.aggregate_for_conversation()` returns a list of `SourceAggregate`
objects grouped by `connector` + `doc_id`. This powers the conversation Sources tab in
the frontend. The aggregate is read-only; it is populated by `CitationLedger.register()`
writes.

---

## Seal on FINAL_RESPONSE

When the worker emits `FINAL_RESPONSE`:

1. `CitationLedger.snapshot(run_id)` reads `CitationStorePort.list_for_run(run_id)`.
2. Returns the ordered citation list.
3. The list is embedded in the `FINAL_RESPONSE` payload as `payload.citations`.

On approval-resume or worker crash: the ledger is rebuilt by reading
`CitationStorePort.list_for_run(run_id)` — the ordinal sequence continues from where
it left off.
