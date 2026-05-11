"""Provider-agnostic token-usage extraction.

Sub-PRD 01a of [01-usage-capture-and-attribution.md].

Two contracts plus a registry:

- :class:`NormalizedTokenUsage` — frozen Pydantic value object with
  explicit fields for every token kind we price. Default 0 everywhere
  so pricing math is total. ``input_tokens`` is the GROSS input figure
  (regular + cached + cache_creation); ``cached_input_tokens`` and
  ``cache_creation_input_tokens`` are subsets of it billed at their
  own rates.
- :class:`ProviderTokenUsageExtractor` — Protocol with one method,
  ``extract(chunk) -> NormalizedTokenUsage | None``. One implementation
  per provider — the only provider-aware code on the usage path.
- :class:`TokenUsageExtractorRegistry` — dispatch by provider slug.
  Unknown providers fall through to a permissive least-common-
  denominator extractor so a new provider's tokens are captured (at
  lcd quality) before a dedicated extractor lands.

This module replaces the alias-soup ``TokenUsageExtractor`` /
``_token_value`` / ``_cached_input_tokens`` helpers that used to live
in ``run_metrics.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, NonNegativeInt, computed_field


class NormalizedTokenUsage(BaseModel):
    """Provider-agnostic token-usage value object.

    Field semantics:

    - ``input_tokens``: GROSS input (regular + cached + cache_creation).
      Provider extractors normalize provider-specific subsets into this
      gross figure before constructing.
    - ``cached_input_tokens``: subset of ``input_tokens`` billed at the
      cached-read rate.
    - ``cache_creation_input_tokens``: subset of ``input_tokens`` billed
      at the cache-write rate.
    - ``output_tokens``: completion / response tokens.
    - ``reasoning_tokens``: reasoning / hidden-chain tokens (OpenAI
      o-series, Anthropic extended thinking). Counted separately from
      output — pricing rate may differ.
    - ``audio_input_tokens`` / ``audio_output_tokens``: voice tokens
      where the provider charges separately.

    Pricing math (P12 plugs in here)::

        cost = (input - cached - cache_creation) * price_input
             + cached                            * price_cached_input
             + cache_creation                    * price_cache_creation
             + output                            * price_output
             + reasoning                         * price_reasoning
             + audio_input                       * price_audio_input
             + audio_output                      * price_audio_output
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cached_input_tokens: NonNegativeInt = 0
    cache_creation_input_tokens: NonNegativeInt = 0
    reasoning_tokens: NonNegativeInt = 0
    audio_input_tokens: NonNegativeInt = 0
    audio_output_tokens: NonNegativeInt = 0

    @computed_field  # type: ignore[misc]
    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.reasoning_tokens
            + self.audio_input_tokens
            + self.audio_output_tokens
        )

    def merge(self, other: "NormalizedTokenUsage") -> "NormalizedTokenUsage":
        """Field-wise max merge for cumulative-chunk providers.

        OpenAI streams usage cumulatively across chunks of the same
        AIMessage; the final chunk carries the authoritative total. Max
        per kind ensures we never undercount when a mid-stream chunk
        reported a smaller running total than a later one.
        """

        return NormalizedTokenUsage(
            input_tokens=max(self.input_tokens, other.input_tokens),
            output_tokens=max(self.output_tokens, other.output_tokens),
            cached_input_tokens=max(
                self.cached_input_tokens, other.cached_input_tokens
            ),
            cache_creation_input_tokens=max(
                self.cache_creation_input_tokens, other.cache_creation_input_tokens
            ),
            reasoning_tokens=max(self.reasoning_tokens, other.reasoning_tokens),
            audio_input_tokens=max(self.audio_input_tokens, other.audio_input_tokens),
            audio_output_tokens=max(
                self.audio_output_tokens, other.audio_output_tokens
            ),
        )


@runtime_checkable
class ProviderTokenUsageExtractor(Protocol):
    """Normalize provider-specific chunks into :class:`NormalizedTokenUsage`.

    Implementations are stateless and shareable. ``extract`` returns
    ``None`` when the chunk carries no usage block (e.g. mid-stream
    content delta without final-chunk metadata). Returning ``None`` is
    the signal the streaming loop uses to skip a per-call emit.

    Implementations MUST NOT raise on malformed chunks — the model
    response is untrusted input. Return ``None`` instead.
    """

    def extract(self, chunk: object) -> NormalizedTokenUsage | None: ...


class _ChunkInspector:
    """Shared helpers for picking values off chunks safely.

    LangChain stream chunks vary by provider: an ``AIMessage`` /
    ``AIMessageChunk`` (object with ``usage_metadata`` / ``response_metadata``
    attributes), a plain mapping with the same fields, or a wrapper
    envelope under ``message`` / ``data``. These helpers tolerate all
    three and return ``None`` for anything off-shape.
    """

    @staticmethod
    def get_attr_or_item(value: object, key: str) -> object | None:
        attr = getattr(value, key, None)
        if attr is not None:
            return attr
        if isinstance(value, Mapping):
            return value.get(key)
        return None

    @staticmethod
    def as_mapping(value: object) -> Mapping[str, object] | None:
        return value if isinstance(value, Mapping) else None

    @staticmethod
    def non_negative_int(value: object) -> int:
        # Bools are ints in Python; exclude them explicitly.
        if isinstance(value, bool):
            return 0
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, float) and value >= 0 and value.is_integer():
            return int(value)
        return 0

    @classmethod
    def descend_to_message(cls, value: object) -> object:
        """Unwrap one level of envelope (``{"message": ...}`` / ``{"data": ...}``)."""

        for key in ("message", "data", "output"):
            inner = cls.get_attr_or_item(value, key)
            if inner is not None:
                return inner
        return value


class _UsageBlocks:
    """Locate usage / response_metadata mappings on a chunk.

    LangGraph wraps the actual AIMessage in a stream envelope like
    ``{"type": "messages", "ns": (...), "data": (AIMessageChunk, {})}``;
    the LangChain-native adapter also exposes the message directly. This
    walker handles both shapes plus a couple of common alternatives:

    1. Direct attribute / mapping key access for ``usage_metadata`` and
       ``response_metadata`` on the chunk.
    2. One level of envelope unwrap into ``message``, ``data``,
       ``output``, ``chunk`` (each may be the AIMessage itself, OR a
       tuple/sequence whose first element is the AIMessage).
    3. Provider-raw dicts under ``response_metadata.token_usage`` and
       ``response_metadata.usage``.

    Returns each block found (in order) so provider extractors can pick
    the first one with the field they need.
    """

    _ENVELOPE_KEYS = ("message", "data", "output", "chunk")
    _MAX_DEPTH = 2

    @classmethod
    def find(cls, chunk: object) -> tuple[Mapping[str, object], ...]:
        blocks: list[Mapping[str, object]] = []
        cls._walk(chunk, blocks, depth=0)
        return tuple(blocks)

    @classmethod
    def _walk(
        cls,
        value: object,
        sink: list[Mapping[str, object]],
        *,
        depth: int,
    ) -> None:
        if value is None or depth > cls._MAX_DEPTH:
            return

        # Direct extraction at this level.
        cls._collect_at(value, sink)

        # Descend into envelope keys (one or two layers deep covers every
        # LangGraph / LangChain stream envelope we currently observe).
        for key in cls._ENVELOPE_KEYS:
            inner = _ChunkInspector.get_attr_or_item(value, key)
            if inner is None:
                continue
            if isinstance(inner, tuple) and inner:
                # LangGraph stream chunks pack ``(AIMessageChunk, metadata)``
                # under ``data`` — descend into the first element.
                cls._walk(inner[0], sink, depth=depth + 1)
            else:
                cls._walk(inner, sink, depth=depth + 1)

    @classmethod
    def _collect_at(cls, value: object, sink: list[Mapping[str, object]]) -> None:
        # Primary: LangChain-native ``usage_metadata``.
        candidate = _ChunkInspector.get_attr_or_item(value, "usage_metadata")
        if isinstance(candidate, Mapping):
            sink.append({str(k): v for k, v in candidate.items()})

        # Secondary: provider-raw dicts under ``response_metadata``.
        resp_meta = _ChunkInspector.get_attr_or_item(value, "response_metadata")
        if isinstance(resp_meta, Mapping):
            normalized = {str(k): v for k, v in resp_meta.items()}
            for key in ("token_usage", "usage"):
                inner = normalized.get(key)
                if isinstance(inner, Mapping):
                    sink.append({str(k): v for k, v in inner.items()})


class _LcdFallbackExtractor:
    """Permissive least-common-denominator extractor.

    Used for unknown provider slugs. Reads only the four columns the
    pre-01a code knew about (input, output, total, cached_input via
    ``prompt_tokens_details.cached_tokens``). Reasoning / cache_creation
    / audio are not surfaced for unknown providers — adding a dedicated
    extractor unlocks them.
    """

    class _F:
        INPUT = "input_tokens"
        OUTPUT = "output_tokens"
        TOTAL = "total_tokens"
        PROMPT = "prompt_tokens"
        COMPLETION = "completion_tokens"
        PROMPT_COUNT = "prompt_token_count"
        COMPLETION_COUNT = "completion_token_count"
        TOTAL_COUNT = "total_token_count"
        PROMPT_DETAILS = "prompt_tokens_details"
        INPUT_DETAILS = "input_token_details"
        CACHED_TOKENS = "cached_tokens"
        CACHE_READ = "cache_read"

    def extract(self, chunk: object) -> NormalizedTokenUsage | None:
        blocks = _UsageBlocks.find(chunk)
        if not blocks:
            return None
        for block in blocks:
            usage = self._normalize(block)
            if usage is not None:
                return usage
        return None

    @classmethod
    def _normalize(cls, block: Mapping[str, object]) -> NormalizedTokenUsage | None:
        input_tokens = _first_int(
            block, cls._F.INPUT, cls._F.PROMPT, cls._F.PROMPT_COUNT
        )
        output_tokens = _first_int(
            block, cls._F.OUTPUT, cls._F.COMPLETION, cls._F.COMPLETION_COUNT
        )
        if input_tokens == 0 and output_tokens == 0:
            # Nothing actionable.
            return None
        cached = _cached_input_from_details(
            block, cls._F.PROMPT_DETAILS, cls._F.INPUT_DETAILS
        )
        return NormalizedTokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached,
        )


class OpenAIProviderTokenUsageExtractor:
    """OpenAI Chat Completions + Responses API.

    Token kinds extracted:

    - ``input_tokens`` from ``input_tokens`` (LC-native) or ``prompt_tokens``.
    - ``output_tokens`` from ``output_tokens`` or ``completion_tokens``.
    - ``cached_input_tokens`` from ``prompt_tokens_details.cached_tokens``
      (Chat Completions) or ``input_tokens_details.cached_tokens``
      (Responses API).
    - ``reasoning_tokens`` from ``completion_tokens_details.reasoning_tokens``
      (Chat Completions o-series) or ``output_tokens_details.reasoning_tokens``
      (Responses API).
    - ``audio_input_tokens`` / ``audio_output_tokens`` from the same
      details blocks under the ``audio_tokens`` key.

    OpenAI's ``prompt_tokens`` field is already the GROSS input
    (includes cached) — no normalization needed there.
    """

    class _F:
        INPUT = "input_tokens"
        OUTPUT = "output_tokens"
        PROMPT = "prompt_tokens"
        COMPLETION = "completion_tokens"
        # OpenAI exposes details under three sibling key names depending
        # on the API path: ``prompt_tokens_details`` (Chat Completions),
        # ``input_tokens_details`` (Responses), and
        # ``input_token_details`` (LangChain's normalized usage_metadata
        # shape — singular ``token``).
        PROMPT_DETAILS = "prompt_tokens_details"
        INPUT_TOKENS_DETAILS = "input_tokens_details"
        INPUT_TOKEN_DETAILS = "input_token_details"
        COMPLETION_DETAILS = "completion_tokens_details"
        OUTPUT_TOKENS_DETAILS = "output_tokens_details"
        OUTPUT_TOKEN_DETAILS = "output_token_details"
        CACHED_TOKENS = "cached_tokens"
        CACHE_READ = "cache_read"
        REASONING_TOKENS = "reasoning_tokens"
        AUDIO_TOKENS = "audio_tokens"

    _INPUT_DETAIL_KEYS = (
        _F.PROMPT_DETAILS,
        _F.INPUT_TOKENS_DETAILS,
        _F.INPUT_TOKEN_DETAILS,
    )
    _OUTPUT_DETAIL_KEYS = (
        _F.COMPLETION_DETAILS,
        _F.OUTPUT_TOKENS_DETAILS,
        _F.OUTPUT_TOKEN_DETAILS,
    )

    def extract(self, chunk: object) -> NormalizedTokenUsage | None:
        blocks = _UsageBlocks.find(chunk)
        if not blocks:
            return None
        for block in blocks:
            usage = self._normalize(block)
            if usage is not None:
                return usage
        return None

    @classmethod
    def _normalize(cls, block: Mapping[str, object]) -> NormalizedTokenUsage | None:
        input_tokens = _first_int(block, cls._F.INPUT, cls._F.PROMPT)
        output_tokens = _first_int(block, cls._F.OUTPUT, cls._F.COMPLETION)
        if input_tokens == 0 and output_tokens == 0:
            return None
        cached = _cached_input_from_details(block, *cls._INPUT_DETAIL_KEYS)
        reasoning = _detail_int(block, cls._OUTPUT_DETAIL_KEYS, cls._F.REASONING_TOKENS)
        audio_input = _detail_int(block, cls._INPUT_DETAIL_KEYS, cls._F.AUDIO_TOKENS)
        audio_output = _detail_int(block, cls._OUTPUT_DETAIL_KEYS, cls._F.AUDIO_TOKENS)
        return NormalizedTokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached,
            reasoning_tokens=reasoning,
            audio_input_tokens=audio_input,
            audio_output_tokens=audio_output,
        )


class AnthropicProviderTokenUsageExtractor:
    """Anthropic Messages API.

    Anthropic's wire shape differs from OpenAI's:

    - ``input_tokens`` is the NON-cache portion only.
    - ``cache_creation_input_tokens`` is tokens written to cache.
    - ``cache_read_input_tokens`` is tokens read from cache.

    Gross input = ``input + cache_creation + cache_read``. This
    extractor normalizes so ``NormalizedTokenUsage.input_tokens`` is
    the gross figure, matching the OpenAI semantic.

    Extended-thinking models surface reasoning tokens under
    ``cache_creation`` semantics in some preview API shapes; this
    extractor falls back to a ``reasoning_tokens`` key when present.
    """

    class _F:
        INPUT = "input_tokens"
        OUTPUT = "output_tokens"
        CACHE_CREATION = "cache_creation_input_tokens"
        CACHE_READ_INPUT = "cache_read_input_tokens"
        # LangChain-native sometimes surfaces these under
        # ``cache_creation`` / ``cache_read`` (shorter names).
        CACHE_CREATION_SHORT = "cache_creation"
        CACHE_READ_SHORT = "cache_read"
        REASONING_TOKENS = "reasoning_tokens"
        # LangChain's normalized ``usage_metadata`` may also expose
        # cached input via ``input_token_details.cache_read``.
        INPUT_DETAILS = "input_token_details"

    def extract(self, chunk: object) -> NormalizedTokenUsage | None:
        blocks = _UsageBlocks.find(chunk)
        if not blocks:
            return None
        for block in blocks:
            usage = self._normalize(block)
            if usage is not None:
                return usage
        return None

    @classmethod
    def _normalize(cls, block: Mapping[str, object]) -> NormalizedTokenUsage | None:
        non_cache_input = _first_int(block, cls._F.INPUT)
        output_tokens = _first_int(block, cls._F.OUTPUT)
        if non_cache_input == 0 and output_tokens == 0:
            return None
        cache_creation = _first_int(
            block, cls._F.CACHE_CREATION, cls._F.CACHE_CREATION_SHORT
        )
        cache_read = _first_int(block, cls._F.CACHE_READ_INPUT, cls._F.CACHE_READ_SHORT)
        # If neither cache field was on the top-level block, look at
        # ``input_token_details`` (LangChain-normalized).
        if cache_read == 0:
            cache_read = _detail_int(
                block, (cls._F.INPUT_DETAILS,), cls._F.CACHE_READ_SHORT
            )
        # Anthropic's input_tokens is non-cache; gross = sum of all three.
        gross_input = non_cache_input + cache_creation + cache_read
        reasoning = _first_int(block, cls._F.REASONING_TOKENS)
        return NormalizedTokenUsage(
            input_tokens=gross_input,
            output_tokens=output_tokens,
            cached_input_tokens=cache_read,
            cache_creation_input_tokens=cache_creation,
            reasoning_tokens=reasoning,
        )


class GeminiProviderTokenUsageExtractor:
    """Google Gemini.

    Reads the LangChain-native ``usage_metadata`` shape (input_tokens /
    output_tokens / total_tokens) plus Gemini's raw
    ``prompt_token_count`` / ``candidates_token_count`` /
    ``total_token_count``.

    Today's Gemini API has no cache, reasoning, or audio token kinds —
    those return 0. When Gemini ships those fields (e.g. 2.0 Flash
    thinking mode), extend this extractor and add a fixture.
    """

    class _F:
        INPUT = "input_tokens"
        OUTPUT = "output_tokens"
        PROMPT_COUNT = "prompt_token_count"
        CANDIDATES_COUNT = "candidates_token_count"
        # Some Gemini preview surfaces expose thinking tokens.
        THOUGHTS_COUNT = "thoughts_token_count"
        REASONING_TOKENS = "reasoning_tokens"

    def extract(self, chunk: object) -> NormalizedTokenUsage | None:
        blocks = _UsageBlocks.find(chunk)
        if not blocks:
            return None
        for block in blocks:
            usage = self._normalize(block)
            if usage is not None:
                return usage
        return None

    @classmethod
    def _normalize(cls, block: Mapping[str, object]) -> NormalizedTokenUsage | None:
        input_tokens = _first_int(block, cls._F.INPUT, cls._F.PROMPT_COUNT)
        output_tokens = _first_int(block, cls._F.OUTPUT, cls._F.CANDIDATES_COUNT)
        if input_tokens == 0 and output_tokens == 0:
            return None
        reasoning = _first_int(block, cls._F.REASONING_TOKENS, cls._F.THOUGHTS_COUNT)
        return NormalizedTokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning,
        )


class TokenUsageExtractorRegistry:
    """Dispatch provider slug → extractor.

    Slugs match :class:`agent_runtime.execution.models` normalization:
    ``openai``, ``anthropic``, ``gemini``. Unknown slugs return the
    LCD fallback so a new provider's tokens are captured at minimum
    quality until a dedicated extractor lands.
    """

    _OPENAI = OpenAIProviderTokenUsageExtractor()
    _ANTHROPIC = AnthropicProviderTokenUsageExtractor()
    _GEMINI = GeminiProviderTokenUsageExtractor()
    _LCD = _LcdFallbackExtractor()

    _BY_PROVIDER: dict[str, ProviderTokenUsageExtractor] = {
        "openai": _OPENAI,
        "anthropic": _ANTHROPIC,
        "gemini": _GEMINI,
    }

    @classmethod
    def for_provider(cls, provider: str) -> ProviderTokenUsageExtractor:
        return cls._BY_PROVIDER.get(provider.strip().lower(), cls._LCD)


# ---------------------------------------------------------------------------
# Module-private helpers. Kept module-level (not on a class) because they're
# pure functions called by the extractor classes — wrapping them in a class
# would add no value.
# ---------------------------------------------------------------------------


def _first_int(block: Mapping[str, object], *keys: str) -> int:
    for key in keys:
        value = _ChunkInspector.non_negative_int(block.get(key))
        if value > 0:
            return value
    return 0


def _cached_input_from_details(
    block: Mapping[str, object],
    *detail_keys: str,
) -> int:
    """Pull ``cached_tokens`` / ``cache_read`` out of a details sub-mapping.

    OpenAI uses ``prompt_tokens_details.cached_tokens``. LangChain's
    normalized shape uses ``input_token_details.cache_read``. Try each
    detail key + each known cached-token name.
    """

    for detail_key in detail_keys:
        details = block.get(detail_key)
        if not isinstance(details, Mapping):
            continue
        normalized = {str(k): v for k, v in details.items()}
        cached = _first_int(normalized, "cached_tokens", "cache_read")
        if cached > 0:
            return cached
    return 0


def _detail_int(
    block: Mapping[str, object],
    detail_keys: tuple[str, ...],
    field: str,
) -> int:
    """Pull ``field`` out of the first details sub-mapping that has it."""

    for detail_key in detail_keys:
        details = block.get(detail_key)
        if not isinstance(details, Mapping):
            continue
        normalized = {str(k): v for k, v in details.items()}
        value = _ChunkInspector.non_negative_int(normalized.get(field))
        if value > 0:
            return value
    return 0
