# PRD 01 — Provider Citation Stream Pipeline (foundation + Anthropic)

## Problem

The product principle "every claim Atlas makes is a hyperlink to the source" only holds for sources that arrive through tools (MCP / connector reads), because tool middleware (`agent_runtime/capabilities/mcp/middleware/cite_mcp.py`) is the only `src/` writer to `CitationLedger` today.

When the **model itself** emits native citations alongside its text — Claude `citations_delta` blocks, OpenAI Responses `output_text.done.annotations`, Gemini `grounding_metadata` — those primitives are silently dropped. `agent_runtime/execution/providers/anthropic_stream_adapter.py` exists as a scaffold (PR 1.1 follow-up D) but is referenced **only by its own unit tests** — no `src/` importer wraps the model stream with it. `runtime_worker/streaming_executor.py` reads each chunk's text via `StreamOrchestrator.stream_delta(chunk)` and emits it raw as `MODEL_DELTA`. Anything attached to the chunk that isn't plain text — citations, annotations, grounding — is discarded before the FE sees it.

The user-visible bug: a Claude run grounded against an attached PDF returns prose with **zero `[c<id>]` chips**, even though the model attached citation blocks to every claim.

## Goals

1. Define one stable adapter abstraction that all three providers (Anthropic, OpenAI Responses, Gemini) implement, consuming **LangChain `AIMessageChunk`** rather than raw provider streams.
2. Wire the abstraction into `StreamingExecutor.run` so every model-stream chunk passes through a per-run, per-provider citation pipeline before `MODEL_DELTA` is emitted.
3. Re-target the existing `AnthropicCitationStreamAdapter` against the new abstraction so Claude citations chips end up in the assistant prose in production.
4. Citations registered through provider adapters reach `CitationLedger`, fire exactly one `source_ingested` event per unique source, and surface via the existing `MessageSourcesStrip` and `[c<id>]` chip pipeline — no FE changes required.

## Non-goals

- Replacing LangChain `init_chat_model` with raw provider SDKs. The adapter must work against parsed `AIMessageChunk` content.
- OpenAI and Gemini adapters (PRDs 02 and 03 respectively).
- Changing the `[c<id>]` token format or the FE rendering path.
- Adding new persistence tables — `runtime_citations` already exists.

## Acceptance criteria

- New module `agent_runtime/execution/providers/citation_pipeline.py` defines `ProviderCitationAdapter` (Protocol) and `CitationStreamPipeline` (per-run dispatcher), both Pydantic-typed where they cross IO boundaries.
- `CitationStreamPipeline.for_provider(provider)` returns a pipeline backed by the right adapter; unknown providers fall through to a no-op adapter.
- `AnthropicCitationStreamAdapter` rewritten to implement `ProviderCitationAdapter.adapt_chunk(chunk, raw_delta)`. The old `aiter` API is removed (no `src/` consumers).
- `StreamingExecutor.run` accepts a `citation_pipeline` and routes every chunk through `pipeline.adapt_chunk(...)` before emitting `MODEL_DELTA`. The handler in `runtime_worker/handlers/run.py` constructs the pipeline once per run, keyed on `command.runtime_context.model_profile.provider`.
- A streaming integration test exercises a synthesised Anthropic-style chunk sequence (text deltas + a `citations_delta` block) end-to-end through `StreamingExecutor.run`, asserting:
  - the assistant `MODEL_DELTA` events concatenate to `"The launch is on April 21.[c1]"`,
  - exactly one `source_ingested` event fires with `payload.citation.source_connector == "anthropic"`,
  - replay through the existing event store produces the same byte-equal stream.
- When the active `CitationLedger` is unbound (citations disabled), the pipeline is a passthrough — text deltas reach the wire unchanged.
- No regressions: existing tests under `tests/unit/agent_runtime/execution/test_citation_substitution.py` (which currently exercise the scaffold) are migrated to the new shape rather than deleted.

## Risks

- LangChain Anthropic chunk content can place citations in two shapes (interleaved with the same text block, or as a standalone block with empty text). The adapter must handle both.
- Per-chunk emission of a `[c<id>]` token after the cited text means the chip appears just **after** the prose run that grounds it. That matches Anthropic's wire ordering — citations follow the text — but we should snapshot the FE rendering against this in a unit test.
- If the pipeline mutates the delta but the chunk is routed to a subagent stream (the executor checks `active_subagent_tasks` to decide whether the delta is a top-level response delta), we must ensure the substitution is consistent — i.e., the chip emission is still bound to the parent agent's `MODEL_DELTA` axis.

## Unit testing requirements

- `test_citation_pipeline.py` — protocol conformance, dispatcher correctness, no-op fallback for unknown providers, idempotent across repeated chunk dispatches.
- `test_anthropic_citation_stream_adapter.py` (rewritten from existing) — handles text-only chunks, interleaved citations, citations on a separate empty-text block, missing fields (no url/title) correctly skipped, ledger cap reached path returns the empty token without dropping the text delta.
- `test_streaming_executor_with_citation_pipeline.py` — full integration: synthesised chunk stream → executor → event store → assert events.
