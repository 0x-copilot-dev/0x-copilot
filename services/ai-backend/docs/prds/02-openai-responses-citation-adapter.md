# PRD 02 — OpenAI Responses Citation Stream Adapter

## Problem

OpenAI's Responses API (used by `langchain_openai.ChatOpenAI(use_responses_api=True)`, the default for tool-use builds) attaches grounding evidence to model output as `annotations` on the `output_text.done` event:

- `url_citation` annotations carry `url`, `title`, `start_index`, `end_index`.
- `file_citation` annotations carry `file_id`, `filename` (no offsets — they ground the entire output_text item).

These reach the runtime as `AIMessageChunk(content=[{"type": "text", "text": "", "annotations": [...]}])` and are dropped by `StreamMessageParser.content_delta_to_text` (only `text` / `content` keys are read). OpenAI runs return prose with no chips and no `source_ingested` events.

This is the same product bug as Anthropic, scoped to OpenAI. It also covers OpenAI-compatible self-hosted endpoints (vLLM, Ollama, Azure OpenAI), which use the same `init_chat_model("...", model_provider="openai", base_url=...)` path and emit the same chunk shape.

## Goals

1. Implement `OpenAIResponsesCitationStreamAdapter` against the foundation defined in PRD 01.
2. Register both `url_citation` and `file_citation` annotations with `CitationLedger`.
3. Emit `[c<id>]` chips that the FE can render as inline citations.

## Non-goals

- Supporting the legacy chat-completions API (no annotations there).
- Supporting OpenAI tool-call grounding (`tool_calls` carry their own data; tool-emitted citations already work via `cite_mcp` middleware).
- Inline-position chip placement using `start_index`/`end_index`. v1 emits chips at the boundary of the `output_text.done` event. Inline interleaving requires holding back text deltas; defer to a follow-up if user feedback demands it.

## Acceptance criteria

- New file `agent_runtime/execution/providers/openai_responses_stream_adapter.py` implementing `ProviderCitationAdapter`.
- Registered in `CitationStreamPipeline._ADAPTERS` under `"openai"`.
- `url_citation` → `SourceRef(source_connector="openai_web", source_doc_id=url, title=title, source_url=url, snippet=cited_text)` (mirrors Anthropic's URL handling).
- `file_citation` → `SourceRef(source_connector="openai_file", source_doc_id=file_id, title=filename or file_id)`.
- Per-chunk behaviour: `output_text.delta` chunks pass through unchanged. An `output_text.done` chunk with N annotations registers each, dedupes through the ledger's `(connector, doc_id)` key, and returns a delta of the form `[c1][c2]…[cN]` (concatenation of unique tokens in annotation order). When the ledger returns the empty string for any candidate (cap reached), that chip is omitted but the rest are kept.
- An OpenAI run with two `url_citation`s and one `file_citation` produces three `source_ingested` events and a `MODEL_DELTA` carrying `[c1][c2][c3]` after the prose deltas.
- When no annotations are present, `adapt_chunk` returns `raw_delta` unchanged.

## Risks

- Some OpenAI streams emit annotations on partial chunks (incremental annotation pushes). The adapter dedupes by `(connector, doc_id)` via the ledger, so a re-emission yields the same token without a duplicate event — this should be exercised in a test.
- Hosted OpenAI-compatible endpoints (vLLM, Ollama) sometimes emit annotations under slightly different shapes (e.g. `citations` rather than `annotations`). v1 reads only the official spec; document the gap and add a follow-up PRD if a self-hosted target needs it.

## Unit testing requirements

- `test_openai_responses_citation_stream_adapter.py`:
  - text-delta passthrough with no annotations,
  - single `url_citation` → one `source_ingested` + token,
  - single `file_citation` → one `source_ingested` + token,
  - mixed batch of two URL + one file → three tokens in deterministic order,
  - duplicate annotation emitted twice → exactly one `source_ingested`,
  - missing required fields (no url, no file_id) → annotation skipped without raising,
  - ledger cap reached → empty tokens omitted, no exception.
