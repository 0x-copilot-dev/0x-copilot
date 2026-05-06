# PRD 03 — Gemini Grounding Citation Stream Adapter

## Problem

Google Gemini, when run with grounding tools (`tools=[{"google_search": {}}]` or Vertex grounding), attaches grounding evidence to chunks as `response_metadata.grounding_metadata`:

- `grounding_chunks: [{"web": {"uri": "...", "title": "..."}}, {"retrieved_context": {"uri": "...", "title": "..."}}]`
- `grounding_supports: [{"segment": {"start_index": int, "end_index": int, "text": "..."}, "grounding_chunk_indices": [int, ...]}]`

These reach the runtime as `AIMessageChunk(content="...", response_metadata={"grounding_metadata": {...}})` and are dropped — `StreamMessageParser.message_delta` reads only `content`, never `response_metadata`. Gemini runs return prose with no chips and no `source_ingested` events.

Same product bug as Anthropic and OpenAI, scoped to Gemini.

## Goals

1. Implement `GeminiGroundingCitationStreamAdapter` against the PRD 01 foundation.
2. Register Gemini grounding chunks with `CitationLedger`.
3. Emit `[c<id>]` chips that the FE can render.

## Non-goals

- Inline-position chip placement using `segment.start_index`/`end_index`. v1 emits chips at the chunk boundary that carries the grounding metadata.
- `retrieved_context` (Vertex RAG) handling beyond the same `web`/`retrieved_context` pair shape — both expose `uri` + `title`.

## Acceptance criteria

- New file `agent_runtime/execution/providers/gemini_grounding_stream_adapter.py` implementing `ProviderCitationAdapter`.
- Registered in `CitationStreamPipeline._ADAPTERS` under `"gemini"`.
- For each `grounding_supports[i]`: pull every chunk in `grounding_chunk_indices`, build `SourceRef(source_connector="gemini_web" or "gemini_retrieved", source_doc_id=uri, title=title, source_url=uri)`, register, and append the chip token to the returned delta.
- For chunks that carry **only** grounding metadata (no text), the adapter returns the chip concatenation as the delta.
- For chunks that carry text + grounding metadata, the adapter returns `<text><chips>` so chips trail the text fragment that carried them.
- When `response_metadata` is missing or empty, `adapt_chunk` returns `raw_delta` unchanged.

## Risks

- Gemini `grounding_chunk_indices` can repeat across supports — same chunk grounds multiple segments. The ledger dedupes; the chip just resolves to the same token both times. Cover in a test.
- `langchain_google_genai` keeps grounding metadata under `response_metadata` for some versions and `additional_kwargs` for others. The adapter must read both keys defensively.
- Vertex grounding adds a `web_search_queries` array. v1 ignores it; queries aren't sources.

## Unit testing requirements

- `test_gemini_grounding_citation_stream_adapter.py`:
  - text-delta passthrough with no `response_metadata`,
  - chunk with `web` grounding chunk → one `source_ingested` + chip,
  - chunk with `retrieved_context` grounding chunk → one `source_ingested` + chip,
  - support indexing into multiple chunks → multiple chips,
  - duplicate `(uri, title)` across supports → exactly one `source_ingested`, two chip tokens that resolve to the same `[c<id>]`,
  - grounding metadata under `additional_kwargs` (legacy shape) read correctly.
