"""Provider citation stream pipeline (PRD 01).

The pipeline is the single seam through which model-stream chunks pass
between :meth:`StreamOrchestrator.stream_delta` and the
``MODEL_DELTA`` event-producer call in
:class:`runtime_worker.streaming_executor.StreamingExecutor`.

For each provider with native citation primitives (Anthropic
``citations_delta`` blocks, OpenAI Responses ``output_text.done``
annotations, Gemini ``grounding_metadata``), an adapter transforms the
LangChain ``AIMessageChunk`` into:

1. zero or more :class:`CitationLedger` registrations (which fire one
   ``source_ingested`` event per unique source, idempotent on
   ``(connector, doc_id)``), and
2. a possibly-rewritten text delta that carries inline ``[c<id>]``
   tokens after the cited spans.

The pipeline owns a single per-run adapter instance keyed by
``model_profile.provider``. Unknown providers fall through to a no-op
adapter — citations remain best-effort, never required for correctness.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ProviderCitationAdapter(Protocol):
    """Per-provider extractor that consumes a LangChain stream chunk.

    Adapters are stateful: the pipeline allocates one instance per run so
    adapters can hold accumulators across consecutive chunks (Anthropic
    keeps a per-block-index text buffer, OpenAI dedupes annotations
    within a content item, etc.).

    Implementations MUST be safe to call when the active
    :class:`agent_runtime.capabilities.citations.CitationLedger` is
    unbound — they then return ``raw_delta`` unchanged.
    """

    async def adapt_chunk(self, *, chunk: object, raw_delta: str | None) -> str | None:
        """Return the text delta to emit, or ``None`` to skip emission.

        ``raw_delta`` is the text the runtime already extracted from the
        chunk via :meth:`StreamOrchestrator.stream_delta`. The adapter
        either returns it unchanged (no citations on this chunk) or
        appends/embeds ``[c<id>]`` tokens for the citations carried by
        the chunk.
        """


class NoopCitationAdapter:
    """Pass-through adapter for providers without native citation primitives.

    Public so tests and the dispatcher can identify it explicitly. The
    adapter is stateless — one instance can serve every run, but the
    pipeline still allocates fresh instances per run for symmetry with
    stateful adapters.
    """

    async def adapt_chunk(self, *, chunk: object, raw_delta: str | None) -> str | None:
        del chunk
        return raw_delta


class CitationStreamPipeline:
    """Per-run dispatcher that routes deltas through the active adapter.

    The pipeline is stateless except for its adapter; the adapter holds
    the per-run buffers. Constructing one pipeline per run keeps adapter
    state isolated by run lifetime.
    """

    def __init__(self, *, adapter: ProviderCitationAdapter) -> None:
        self._adapter = adapter

    @property
    def adapter(self) -> ProviderCitationAdapter:
        """Expose the active adapter for tests and observability."""

        return self._adapter

    async def adapt_chunk(self, *, chunk: object, raw_delta: str | None) -> str | None:
        """Dispatch one stream chunk through the active adapter."""

        return await self._adapter.adapt_chunk(chunk=chunk, raw_delta=raw_delta)

    @classmethod
    def for_provider(cls, provider: str | None) -> "CitationStreamPipeline":
        """Return a pipeline backed by the right adapter for ``provider``.

        The provider strings are the normalised values
        :class:`ModelConfigResolver` produces (``anthropic`` / ``openai``
        / ``gemini``). Unknown or missing providers receive the no-op
        adapter so the pipeline is safe to install unconditionally.
        """

        return cls(adapter=_AdapterRegistry.build(provider))


class _AdapterRegistry:
    """Provider → adapter-factory mapping.

    Uses lazy imports so the citations pipeline doesn't drag every
    provider's adapter (and any provider-specific deps in the adapter
    module) into every runtime build.
    """

    @classmethod
    def build(cls, provider: str | None) -> ProviderCitationAdapter:
        if provider == "anthropic":
            return cls._anthropic()
        if provider == "openai":
            return cls._openai()
        if provider == "gemini":
            return cls._gemini()
        return NoopCitationAdapter()

    @staticmethod
    def _anthropic() -> ProviderCitationAdapter:
        from agent_runtime.execution.providers.anthropic_stream_adapter import (
            AnthropicCitationStreamAdapter,
        )

        return AnthropicCitationStreamAdapter()

    @staticmethod
    def _openai() -> ProviderCitationAdapter:
        from agent_runtime.execution.providers.openai_responses_stream_adapter import (
            OpenAIResponsesCitationStreamAdapter,
        )

        return OpenAIResponsesCitationStreamAdapter()

    @staticmethod
    def _gemini() -> ProviderCitationAdapter:
        from agent_runtime.execution.providers.gemini_grounding_stream_adapter import (
            GeminiGroundingCitationStreamAdapter,
        )

        return GeminiGroundingCitationStreamAdapter()


__all__ = (
    "CitationStreamPipeline",
    "NoopCitationAdapter",
    "ProviderCitationAdapter",
)
